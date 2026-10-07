"""访问控制与作业提交确认令牌（设计文档 §7.6、§10）。

- 私聊限制与用户白名单在所有个人信息查询、文件操作和作业提交前执行；
- 白名单为空时默认拒绝，不放行；
- 确认令牌绑定用户、会话、课程、内容、文件与 SHA-256，
  短期有效、一次性消费，插件重启后全部失效（纯内存存储）。
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import AccessDeniedError, SustechError

_TOKEN_INVALID_MESSAGE = "确认令牌无效或已过期，请重新执行提交预览。"
_FILE_OPS_DISABLED_MESSAGE = "文件下载与导出功能未启用。"
_SUBMISSION_DISABLED_MESSAGE = "作业提交功能未启用。"


def _cfg(config: Mapping[str, Any], key: str, default: Any) -> Any:
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


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


class AccessController:
    """统一的访问规则检查（§10.2、§10.3）。"""

    def __init__(self, config: Mapping[str, Any]) -> None:
        self._private_only = _as_bool(_cfg(config, "private_only", True))
        raw_users = _cfg(config, "allowed_users", [])
        if not isinstance(raw_users, (list, tuple, set)):
            raw_users = [raw_users]
        self._allowed_users = {str(user).strip() for user in raw_users if str(user).strip()}
        self._allow_file_operations = _as_bool(_cfg(config, "allow_file_operations", True))
        self._allow_assignment_submission = _as_bool(
            _cfg(config, "allow_assignment_submission", True)
        )

    def ensure_query_allowed(self, *, is_private: bool, user_id: str) -> None:
        """个人信息查询的统一入口检查。

        顺序（§10.2）：私聊限制 → 白名单为空拒绝 → 用户必须在白名单内。
        拒绝消息统一，不泄露课程数据是否存在。
        """
        if self._private_only and not is_private:
            raise AccessDeniedError()
        if not self._allowed_users:
            raise AccessDeniedError()
        if str(user_id) not in self._allowed_users:
            raise AccessDeniedError()

    def ensure_file_allowed(self, *, is_private: bool, user_id: str) -> None:
        """文件下载 / 导出：查询权限 + allow_file_operations 开关。"""
        self.ensure_query_allowed(is_private=is_private, user_id=user_id)
        if not self._allow_file_operations:
            raise AccessDeniedError(_FILE_OPS_DISABLED_MESSAGE)

    def ensure_submission_allowed(self, *, is_private: bool, user_id: str) -> None:
        """作业提交：查询权限 + allow_assignment_submission 开关。"""
        self.ensure_query_allowed(is_private=is_private, user_id=user_id)
        if not self._allow_assignment_submission:
            raise AccessDeniedError(_SUBMISSION_DISABLED_MESSAGE)


@dataclass(frozen=True)
class PendingSubmission:
    """一次待确认的作业提交（绑定 token 的全部字段，§7.6）。"""

    token: str
    user_id: str
    session_id: str
    course_id: str
    content_id: str | None
    column_id: str | None
    file_path: str  # 绝对路径（runner 已校验位于 input_root 内）
    file_sha256: str
    comment: str
    allow_late: bool
    created_at: float  # time.time()
    preview: dict  # submit_preview 的返回 JSON


class ConfirmationStore:
    """纯内存确认令牌存储。

    - token = secrets.token_urlsafe(16)；
    - 超过 ttl_seconds 自动失效；
    - 一次性消费，consume 成功即删除；
    - 进程重启后全部失效。

    AstrBot 事件循环为单线程，create/consume 均不 await，
    不存在并发交错点，因此不需要额外的锁。
    """

    def __init__(self, ttl_seconds: int = 600) -> None:
        try:
            ttl = int(ttl_seconds)
        except (TypeError, ValueError):
            ttl = 600
        self._ttl = ttl if ttl > 0 else 600
        self._pending: dict[str, PendingSubmission] = {}

    def _purge_expired(self, now: float) -> None:
        expired = [
            token
            for token, item in self._pending.items()
            if now - item.created_at > self._ttl
        ]
        for token in expired:
            del self._pending[token]

    def create(
        self,
        *,
        user_id: str,
        session_id: str,
        course_id: str,
        content_id: str | None,
        column_id: str | None,
        file_path: str,
        file_sha256: str,
        comment: str,
        allow_late: bool,
        preview: dict,
    ) -> str:
        now = time.time()
        self._purge_expired(now)
        token = secrets.token_urlsafe(16)
        self._pending[token] = PendingSubmission(
            token=token,
            user_id=str(user_id),
            session_id=str(session_id),
            course_id=course_id,
            content_id=content_id,
            column_id=column_id,
            file_path=file_path,
            file_sha256=file_sha256,
            comment=comment,
            allow_late=allow_late,
            created_at=now,
            preview=preview,
        )
        return token

    def consume(self, token: str, *, user_id: str, session_id: str) -> PendingSubmission:
        """校验并一次性消费 token。

        不存在、已过期、用户或会话不匹配时抛出统一的 SustechError，
        不区分具体失败原因。
        """
        now = time.time()
        self._purge_expired(now)
        item = self._pending.get(str(token).strip())
        if item is None:
            raise SustechError(_TOKEN_INVALID_MESSAGE)
        if item.user_id != str(user_id) or item.session_id != str(session_id):
            raise SustechError(_TOKEN_INVALID_MESSAGE)
        del self._pending[item.token]
        return item
