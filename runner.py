"""受限 CLI Runner：以固定 operation 白名单调用本机 sustech 命令。

安全约束（设计文档 §6.2、§7、§8）：

- 只使用 ``asyncio.create_subprocess_exec``，禁止 shell；
- Runner 不接受任意命令字符串，只接受内部 operation；
- 主密码只通过子进程环境变量注入，绝不出现在参数、日志或返回值中；
- 所有输出文件必须位于 ``download_root`` 内，提交输入文件必须位于
  ``input_root`` 内，均经过 ``resolve()`` 与符号链接检查；
- 同一时间只执行一个 CLI 查询（``asyncio.Semaphore(1)``）。
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import json
import logging
import os
import re
import stat
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

try:  # 插件目录加载方式不定，兼容包内与扁平两种导入
    from .errors import (
        CLI_ERROR_MESSAGES,
        CliError,
        CliTimeoutError,
        ConfigError,
        ValidationError,
        message_for_cli_error,
    )
except ImportError:  # pragma: no cover - 扁平布局
    from errors import (
        CLI_ERROR_MESSAGES,
        CliError,
        CliTimeoutError,
        ConfigError,
        ValidationError,
        message_for_cli_error,
    )

logger = logging.getLogger(__name__)

ALLOWED_OPERATIONS: frozenset[str] = frozenset(
    {
        "status",
        "deadlines",
        "schedule",
        "courses",
        "download_attachment",
        "export_calendar",
        "submit_preview",
        "submit_apply",
    }
)

_SUBMISSION_STATES: frozenset[str] = frozenset(
    {"not_attempted", "in_progress", "submitted", "completed", "mixed", "other"}
)

# opaque token：非空、≤256 字符，禁止路径分隔符、空白与 shell 元字符
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9_\-.:=]{1,256}$")
_SEMESTER_RE = re.compile(r"^\d{4}-\d{4}-\d+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_MAX_QUERY_LEN = 100
_MAX_COMMENT_LEN = 2000

_MISSING_PASSWORD_MESSAGE = "SUSTECH 主密码尚未配置，请在 AstrBot 插件设置中填写。"


@dataclass(frozen=True)
class ProducedFile:
    """插件在 download_root 内生成的文件。"""

    name: str  # 安全文件名
    rel_path: str  # 相对 download_root 的路径
    size: int
    sha256: str


def _cfg(config: Mapping[str, Any], key: str, default: Any) -> Any:
    """从 AstrBotConfig / dict / 普通对象读取配置值。"""
    get = getattr(config, "get", None)
    if callable(get):
        try:
            value = get(key, default)
        except TypeError:
            value = get(key)
            if value is None:
                value = default
    else:
        value = getattr(config, key, default)
    return default if value is None else value


# ---------------------------------------------------------------------------
# 参数校验（设计文档 §8）
# ---------------------------------------------------------------------------


def validate_days(value: Any) -> int:
    """days 必须为 1-90 的整数。"""
    if isinstance(value, bool):
        raise ValidationError("days 必须是 1 到 90 之间的整数。")
    try:
        days = int(value)
    except (TypeError, ValueError):
        raise ValidationError("days 必须是 1 到 90 之间的整数。") from None
    if not 1 <= days <= 90:
        raise ValidationError("days 必须在 1 到 90 之间。")
    return days


def validate_opaque_id(value: Any, field: str) -> str:
    """course_id / content_id / attachment_id 等 opaque token 校验。"""
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ValidationError(f"{field} 不能为空。")
    if not _OPAQUE_ID_RE.match(text):
        raise ValidationError(f"{field} 含有非法字符。")
    return text


def validate_query(value: Any, field: str = "query") -> str:
    """课程关键词 / 查询词：可选，≤100 字符。"""
    text = str(value).strip() if value else ""
    if len(text) > _MAX_QUERY_LEN:
        raise ValidationError(f"{field} 长度不能超过 {_MAX_QUERY_LEN} 个字符。")
    return text


def validate_submission_state(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    state = str(value).strip()
    if state not in _SUBMISSION_STATES:
        raise ValidationError(f"submission_state 必须是以下之一：{', '.join(sorted(_SUBMISSION_STATES))}。")
    return state


def validate_date(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    if not _DATE_RE.match(text):
        raise ValidationError("date 必须是 YYYY-MM-DD 格式。")
    try:
        _dt.date.fromisoformat(text)
    except ValueError:
        raise ValidationError("date 不是有效日期。") from None
    return text


def validate_semester(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    if not _SEMESTER_RE.match(text):
        raise ValidationError("semester 必须是 YYYY-YYYY-N 格式。")
    return text


def validate_week(value: Any) -> int:
    if isinstance(value, bool):
        raise ValidationError("week 必须是正整数。")
    try:
        week = int(value)
    except (TypeError, ValueError):
        raise ValidationError("week 必须是正整数。") from None
    if week < 1:
        raise ValidationError("week 必须是正整数。")
    return week


def validate_comment(value: Any) -> str:
    text = str(value).strip() if value else ""
    if len(text) > _MAX_COMMENT_LEN:
        raise ValidationError(f"comment 长度不能超过 {_MAX_COMMENT_LEN} 个字符。")
    return text


# ---------------------------------------------------------------------------
# 路径安全（设计文档 §7.5）
# ---------------------------------------------------------------------------


def sanitize_filename(name: str) -> str:
    """清理远程文件名：去除路径分隔符、``..`` 片段与控制字符。"""
    base = str(name).replace("\\", "/").split("/")[-1]
    base = base.replace("\x00", "")
    base = re.sub(r"[\r\n]+", "", base).strip()
    if base in ("", ".", ".."):
        base = "file"
    # 折叠残留的 ".." 片段
    while ".." in base:
        base = base.replace("..", ".")
    return base[:255] or "file"


def _ensure_within(resolved: Path, root: Path, what: str) -> Path:
    if resolved != root and root not in resolved.parents:
        raise ValidationError(f"{what}必须位于受控目录内。")
    return resolved


def _check_no_symlink_escape(path: Path, root: Path) -> None:
    """逐级检查 root 与 path 之间的父目录是否为符号链接。"""
    current = root
    if current.is_symlink():
        raise ValidationError("受控目录不能是符号链接。")
    relative = path.relative_to(root)
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValidationError("目标路径包含符号链接，已拒绝。")


def resolve_output_path(root: Path | str, operation: str, filename: str) -> Path:
    """生成 ``root/<operation>/<date>/<safe-name>`` 并验证未逃逸 root。

    同名文件已存在时自动追加 ``-2``/``-3`` 等后缀（默认不覆盖）。
    """
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root_path, stat.S_IRWXU)  # 0o700，尽量严格
    except OSError:
        pass
    resolved_root = root_path.resolve()

    safe_name = sanitize_filename(filename)
    date_dir = _dt.date.today().isoformat()
    candidate_dir = resolved_root / operation / date_dir
    candidate_dir.mkdir(parents=True, exist_ok=True)

    candidate = candidate_dir / safe_name
    if candidate.exists() or candidate.is_symlink():
        stem, suffix = os.path.splitext(safe_name)
        counter = 2
        while candidate.exists() or candidate.is_symlink():
            candidate = candidate_dir / f"{stem}-{counter}{suffix}"
            counter += 1

    resolved = candidate.resolve()
    _ensure_within(resolved, resolved_root, "输出路径")
    _check_no_symlink_escape(candidate, resolved_root)
    return candidate


def resolve_input_path(root: Path | str, file: str) -> Path:
    """解析提交输入文件，必须位于 input_root 内且是普通文件。"""
    root_path = Path(root)
    resolved_root = root_path.resolve()
    candidate = Path(str(file).strip())
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    resolved = candidate.resolve()
    _ensure_within(resolved, resolved_root, "提交文件")
    _check_no_symlink_escape(resolved, resolved_root)
    if not resolved.is_file():
        raise ValidationError("提交文件不存在或不是普通文件。")
    return resolved


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _human_safe_relpath(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class SustechRunner:
    """以固定 operation 白名单执行本机 sustech CLI。"""

    def __init__(self, config: Mapping[str, Any]) -> None:
        self._command = str(_cfg(config, "sustech_command", "sustech")).strip() or "sustech"
        self._master_password = str(_cfg(config, "master_password", "") or "")
        self._profile = str(_cfg(config, "profile", "default") or "default")
        try:
            self._timeout = float(_cfg(config, "timeout_seconds", 45))
        except (TypeError, ValueError):
            self._timeout = 45.0
        if self._timeout <= 0:
            self._timeout = 45.0
        self._default_deadline_days = _cfg(config, "default_deadline_days", 14)
        self._download_root = Path(str(_cfg(config, "download_root", "data/sustech-cli/files")))
        self._input_root = Path(str(_cfg(config, "input_root", "data/sustech-cli/inputs")))
        try:
            self._max_download_bytes = int(_cfg(config, "max_download_bytes", 52428800))
        except (TypeError, ValueError):
            self._max_download_bytes = 52428800
        self._semaphore = asyncio.Semaphore(1)

    # -- 子进程执行 ---------------------------------------------------------

    async def _run(self, operation: str, args: list[str]) -> dict:
        if operation not in ALLOWED_OPERATIONS:
            raise ValidationError(f"不允许的操作：{operation}")
        if not self._master_password:
            # 不能让子进程进入交互式等待（设计文档 §7.2）
            raise ConfigError(_MISSING_PASSWORD_MESSAGE)

        env = os.environ.copy()
        env["SUSTECH_MASTER_PASSWORD"] = self._master_password
        env["SUSTECH_PROFILE"] = self._profile

        started = time.monotonic()
        exit_code: int | None = None
        async with self._semaphore:
            process = await asyncio.create_subprocess_exec(
                self._command,
                *args,
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(), timeout=self._timeout
                )
            except (asyncio.TimeoutError, TimeoutError):
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except (asyncio.TimeoutError, TimeoutError):
                    pass
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.info("operation=%s timeout elapsed_ms=%d", operation, elapsed_ms)
                raise CliTimeoutError() from None
            exit_code = process.returncode

        elapsed_ms = int((time.monotonic() - started) * 1000)
        # 日志只记录 operation / elapsed / exit_code，绝不记录密码与 stderr（§10.4）
        logger.info(
            "operation=%s elapsed_ms=%d exit_code=%s", operation, elapsed_ms, exit_code
        )

        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        if exit_code != 0:
            raise CliError(
                message_for_cli_error(self._extract_error_code(stdout_text, stderr_bytes)),
                code=self._extract_error_code(stdout_text, stderr_bytes),
            )
        try:
            data = json.loads(stdout_text) if stdout_text else {}
        except json.JSONDecodeError:
            raise CliError(message_for_cli_error(None)) from None
        if not isinstance(data, dict):
            raise CliError(message_for_cli_error(None))
        embedded_code = self._extract_error_code(stdout_text, b"")
        if embedded_code:
            raise CliError(message_for_cli_error(embedded_code), code=embedded_code)
        return data

    @staticmethod
    def _extract_error_code(stdout_text: str, stderr_bytes: bytes) -> str | None:
        """从 CLI 输出中提取错误码（不向外暴露任何原文）。"""
        for text in (stdout_text, stderr_bytes.decode("utf-8", errors="replace")):
            text = text.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict) and isinstance(error.get("code"), str):
                    return error["code"]
                code = payload.get("code")
                # 顶层 code 只有已知凭证类错误码才视为失败；
                # 其余（如 DO_NOT_RETRY_AUTOMATICALLY）交给上层格式化（§12.6）。
                if isinstance(code, str) and code in CLI_ERROR_MESSAGES:
                    return code
        return None

    # -- 输出文件收尾 ---------------------------------------------------------

    def _finalize_output(self, data: dict, operation: str, suggested_name: str) -> dict:
        """校验 CLI 写入的文件在 download_root 内、未超限，并补充文件信息。"""
        raw_path = data.get("file") or data.get("path") or ""
        resolved_root = self._download_root.resolve()
        if raw_path:
            written = Path(str(raw_path)).resolve()
        else:
            written = resolve_output_path(resolved_root, operation, suggested_name)
        try:
            _ensure_within(written, resolved_root, "输出文件")
        except ValidationError:
            if raw_path and written.exists():
                written.unlink()
            raise
        if not written.is_file():
            raise CliError(message_for_cli_error(None))
        size = written.stat().st_size
        if size > self._max_download_bytes:
            written.unlink()
            raise ValidationError("文件超过大小限制，已删除。")
        produced = ProducedFile(
            name=written.name,
            rel_path=_human_safe_relpath(written, resolved_root),
            size=size,
            sha256=sha256_file(written),
        )
        result = dict(data)
        result["file"] = asdict(produced)
        return result

    # -- 公开 operation ----------------------------------------------------

    async def status(self) -> dict:
        return await self._run("status", ["auth", "status"])

    async def deadlines(
        self,
        days: int | None = None,
        course: str = "",
        submission_state: str | None = None,
    ) -> dict:
        if days is None:
            days = validate_days(self._default_deadline_days)
        else:
            days = validate_days(days)
        args = ["bb", "deadlines", "--days", str(days)]
        keyword = validate_query(course, "course")
        if keyword:
            args += ["--course", keyword]
        state = validate_submission_state(submission_state)
        if state:
            args += ["--submission-state", state]
        return await self._run("deadlines", args)

    async def schedule(
        self,
        semester: str | None = None,
        week: int | None = None,
        date: str | None = None,
        all: bool = False,
    ) -> dict:
        selectors = sum(
            1 for present in (week is not None, bool(date), bool(all)) if present
        )
        if selectors > 1:
            raise ValidationError("week、date、all 三个参数互斥，只能选择一个。")
        args = ["tis", "schedule"]
        if semester:
            args += ["--semester", validate_semester(semester)]
        if week is not None:
            args += ["--week", str(validate_week(week))]
        if date:
            args += ["--date", validate_date(date)]
        if all:
            args += ["--all"]
        return await self._run("schedule", args)

    async def courses(self, query: str = "") -> dict:
        args = ["bb", "courses"]
        keyword = validate_query(query)
        if keyword:
            args += ["--query", keyword]
        return await self._run("courses", args)

    async def download_attachment(
        self, course_id: str, content_id: str, attachment_id: str
    ) -> dict:
        course = validate_opaque_id(course_id, "course_id")
        content = validate_opaque_id(content_id, "content_id")
        attachment = validate_opaque_id(attachment_id, "attachment_id")
        destination = resolve_output_path(
            self._download_root, "download", f"{attachment}.bin"
        )
        args = [
            "bb",
            "download",
            course,
            content,
            attachment,
            "--destination",
            str(destination),
        ]
        try:
            data = await self._run("download_attachment", args)
            return self._finalize_output(data, "download", destination.name)
        except Exception:
            # 失败时删除不完整文件（§7.5）
            if destination.exists():
                destination.unlink()
            raise

    async def export_calendar(self) -> dict:
        destination = resolve_output_path(self._download_root, "calendar", "schedule.ics")
        args = ["tis", "ical", "--destination", str(destination)]
        try:
            data = await self._run("export_calendar", args)
            return self._finalize_output(data, "calendar", destination.name)
        except Exception:
            if destination.exists():
                destination.unlink()
            raise

    # -- 作业提交（设计文档 §7.6） -------------------------------------------

    def _resolve_submit_target(
        self, content_id: str | None, column_id: str | None
    ) -> list[str]:
        has_content = bool(content_id and str(content_id).strip())
        has_column = bool(column_id and str(column_id).strip())
        if has_content == has_column:
            raise ValidationError("content_id 和 column_id 必须且只能提供一个。")
        if has_content:
            return ["--content-id", validate_opaque_id(content_id, "content_id")]
        return ["--column-id", validate_opaque_id(column_id, "column_id")]

    def _resolve_submit_file(self, file: str) -> Path:
        return resolve_input_path(self._input_root, file)

    async def submit_preview(
        self,
        course_id: str,
        file: str,
        *,
        content_id: str | None = None,
        column_id: str | None = None,
        comment: str = "",
        allow_late: bool = False,
    ) -> dict:
        course = validate_opaque_id(course_id, "course_id")
        target_args = self._resolve_submit_target(content_id, column_id)
        resolved_file = self._resolve_submit_file(file)
        comment_text = validate_comment(comment)

        digest = sha256_file(resolved_file)
        size = resolved_file.stat().st_size

        args = ["bb", "submit", "preview", "--course-id", course, *target_args,
                "--file", str(resolved_file)]
        if comment_text:
            args += ["--comment", comment_text]
        if allow_late:
            args += ["--allow-late"]

        data = await self._run("submit_preview", args)
        result = dict(data)
        result["local_file"] = {
            "path": _human_safe_relpath(resolved_file, self._input_root.resolve()),
            "size": size,
            "sha256": digest,
        }
        return result

    async def submit_apply(
        self,
        course_id: str,
        file: str,
        sha256: str,
        *,
        content_id: str | None = None,
        column_id: str | None = None,
        comment: str = "",
        allow_late: bool = False,
    ) -> dict:
        """执行真正的提交。只允许由插件内部确认流程调用。"""
        course = validate_opaque_id(course_id, "course_id")
        target_args = self._resolve_submit_target(content_id, column_id)
        resolved_file = self._resolve_submit_file(file)
        comment_text = validate_comment(comment)
        expected = str(sha256).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValidationError("expected sha256 格式不正确。")

        args = [
            "bb", "submit", "apply",
            "--course-id", course,
            *target_args,
            "--file", str(resolved_file),
            "--expected-sha256", expected,
            "--confirm",
        ]
        if comment_text:
            args += ["--comment", comment_text]
        if allow_late:
            args += ["--allow-late"]
        return await self._run("submit_apply", args)
