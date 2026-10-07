"""插件内部错误类型。

所有异常的 ``user_message`` 都可以安全地展示给用户：
绝不包含主密码、本地路径、Node.js 堆栈或 CLI stderr 原文。
对应设计文档 §11 错误映射。
"""

from __future__ import annotations

# 设计文档 §11：sustech-cli 错误码 -> 用户可见提示
CLI_ERROR_MESSAGES: dict[str, str] = {
    "MASTER_PASSWORD_REQUIRED": "插件未配置 SUSTech 主密码",
    "MASTER_PASSWORD_INVALID": "SUSTech 主密码不正确",
    "CREDENTIAL_PROFILE_NOT_FOUND": "当前 profile 尚未登录，请先配置 SUSTech 凭证",
    "CREDENTIAL_STORE_UNAVAILABLE": "本地凭证存储后端不可用",
    "CREDENTIAL_STORE_TIMEOUT": "本地凭证存储响应超时",
    "CREDENTIALS_REQUIRED": "尚未配置 SUSTech 登录凭证",
}

_FALLBACK_MESSAGE = "SUSTech 查询失败，请稍后重试。"


def message_for_cli_error(code: str | None) -> str:
    """将 CLI 错误码映射为用户可见提示；未识别的错误码返回通用文案。"""
    if code and code in CLI_ERROR_MESSAGES:
        return CLI_ERROR_MESSAGES[code]
    return _FALLBACK_MESSAGE


class SustechError(Exception):
    """插件错误基类。

    ``user_message`` 是唯一允许发送到聊天或日志的消息文本。
    """

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
    """CLI 非零退出或输出无法解析。

    ``code`` 为可识别的 sustech-cli 错误码，无法识别时为 None。
    """


class CliTimeoutError(SustechError):
    """CLI 执行超时。"""

    def __init__(
        self,
        user_message: str = "查询超时，请稍后重试。",
        *,
        code: str | None = None,
    ) -> None:
        super().__init__(user_message, code=code)
