"""插件内部错误类型。

异常消息会向聊天用户展示。CLI 诊断信息保留原始错误的可定位部分，
但会截断并隐藏密码、令牌、Cookie 等敏感字段。
"""

from __future__ import annotations

import re
from typing import Iterable

# 设计文档 §11：sustech-cli 错误码 -> 用户可见提示
CLI_ERROR_MESSAGES: dict[str, str] = {
    "MASTER_PASSWORD_REQUIRED": "插件未配置 SUSTech 主密码",
    "MASTER_PASSWORD_INVALID": "SUSTech 主密码不正确",
    "CREDENTIAL_PROFILE_NOT_FOUND": "当前 profile 尚未登录，请先配置 SUSTech 凭证",
    "CREDENTIAL_STORE_UNAVAILABLE": "本地凭证存储后端不可用",
    "CREDENTIAL_STORE_TIMEOUT": "本地凭证存储响应超时",
    "CREDENTIALS_REQUIRED": "尚未配置 SUSTech 登录凭证",
    "BLACKBOARD_SUBMISSION_OUTCOME_UNKNOWN": (
        "Blackboard 返回的提交结果不确定，插件不会自动重试。"
        "请登录 Blackboard 检查提交状态后再决定是否操作。"
    ),
}

_FALLBACK_MESSAGE = "SUSTech 查询失败，请稍后重试。"
_MAX_CLI_DETAIL_LENGTH = 2000
_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_SENSITIVE_FIELD_RE = re.compile(
    r"(?i)(password|passwd|token|cookie|authorization|set-cookie)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)


def sanitize_cli_error_detail(detail: str | None, *, secrets: Iterable[str] = ()) -> str:
    """保留 CLI 原始诊断，同时隐藏凭证和控制字符。"""
    text = str(detail or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    text = _ANSI_ESCAPE_RE.sub("", text)
    for secret in sorted({str(value) for value in secrets if str(value)}, key=len, reverse=True):
        text = text.replace(secret, "[已隐藏]")
    text = _SENSITIVE_FIELD_RE.sub(r"\1\2[已隐藏]", text)
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    if len(text) > _MAX_CLI_DETAIL_LENGTH:
        text = text[:_MAX_CLI_DETAIL_LENGTH].rstrip() + "…"
    return text


def message_for_cli_error(
    code: str | None,
    *,
    detail: str | None = None,
    exit_code: int | None = None,
) -> str:
    """生成包含错误码和 CLI 原始诊断的用户错误消息。"""
    message = CLI_ERROR_MESSAGES.get(code or "", _FALLBACK_MESSAGE)
    lines = [message]
    if code:
        lines.append(f"错误代码：{code}")
    if exit_code is not None:
        lines.append(f"CLI 退出码：{exit_code}")
    if detail:
        lines.append(f"CLI 原始错误（敏感字段已隐藏）：{detail}")
    return "\n".join(lines)


class SustechError(Exception):
    """插件内部错误类型。"""

    def __init__(self, user_message: str, *, code: str | None = None) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.code = code


class ConfigError(SustechError):
    """插件配置缺失或非法。"""


class AccessDeniedError(SustechError):
    """访问被拒绝（私聊限制或用户白名单未通过）。"""

    def __init__(
        self,
        user_message: str = "该功能只允许机器人管理员或授权用户使用。",
        *,
        code: str | None = None,
    ) -> None:
        super().__init__(user_message, code=code)


class ValidationError(SustechError):
    """用户或 LLM 传入的参数未通过校验。"""


class CliError(SustechError):
    """CLI 非零退出或输出无法解析。"""

    def __init__(
        self,
        user_message: str,
        *,
        code: str | None = None,
        detail: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        super().__init__(user_message, code=code)
        self.detail = detail
        self.exit_code = exit_code


class CliTimeoutError(SustechError):
    """CLI 执行超时。"""

    def __init__(
        self,
        user_message: str = "查询超时，请稍后重试。",
        *,
        code: str | None = None,
    ) -> None:
        super().__init__(user_message, code=code)
