from __future__ import annotations

from pathlib import Path
from typing import Any

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import AstrBotConfig, logger

from .sustech_cli.runner import SustechRunner, sha256_file
from .sustech_cli.access import AccessController, ConfirmationStore
from .sustech_cli.errors import SustechError
from .sustech_cli import formatter


_ERROR_MESSAGE = "SUSTech 查询失败，请稍后重试。"


@register("astrbot_plugin_sustech_cli", "OBG-tech", "通过本机 sustech CLI 查询课程信息", "1.0.0")
class SustechCliPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.runner = SustechRunner(config)
        self.access = AccessController(config)
        ttl = 600
        try:
            ttl = int(config.get("confirmation_ttl_seconds", 600))
        except (TypeError, ValueError):
            pass
        self.confirmations = ConfirmationStore(ttl)

    def _is_private(self, event) -> bool:
        try:
            return event.get_message_type().name == "FRIEND_MESSAGE"
        except Exception:
            return False

    def _args(self, event) -> list[str]:
        parts = str(getattr(event, "message_str", "") or "").split()
        return parts[1:]

    def _user_id(self, event) -> str:
        return str(event.get_sender_id())

    def _session_id(self, event) -> str:
        return str(getattr(event, "unified_msg_origin", ""))

    def _input_root(self) -> Path:
        try:
            value = self.config.get("input_root", "data/sustech-cli/inputs")
        except Exception:
            value = "data/sustech-cli/inputs"
        return Path(str(value)).resolve()

    def _llm_enabled(self) -> bool:
        try:
            value = self.config.get("enable_llm_tools", False)
        except Exception:
            value = False
        if isinstance(value, str):
            return value.strip().lower() not in ("", "0", "false", "no", "off")
        return bool(value)

    def _access_query(self, event) -> None:
        self.access.ensure_query_allowed(
            is_private=self._is_private(event), user_id=self._user_id(event)
        )

    def _access_file(self, event) -> None:
        self.access.ensure_file_allowed(
            is_private=self._is_private(event), user_id=self._user_id(event)
        )

    def _access_submission(self, event) -> None:
        self.access.ensure_submission_allowed(
            is_private=self._is_private(event), user_id=self._user_id(event)
        )

    def _status_text(self, data: dict) -> str:
        # ``auth status`` 负载字段见 sustech-cli getCredentialStatus()
        profile = data.get("profile") or "未知"
        configured = data.get("configured") is True
        available = data.get("credentialAvailable") is True
        if available:
            auth_text = "已登录"
        elif configured:
            auth_text = "凭证已配置但不可用"
        else:
            auth_text = "未登录"
        lines = ["SUSTech 状态：", f"Profile：{profile}", f"认证状态：{auth_text}"]
        backend = data.get("backend")
        if backend:
            lines.append(f"凭证存储：{backend}")
        return "\n".join(lines[:4])


    def _parse_submission(self, args: list[str]) -> tuple[str, str | None, str | None, str, str]:
        if len(args) < 3:
            raise SustechError("用法：/sustech-submit-preview <course_id> <content_id|-> <file> [comment]")
        course_id, target = args[0], args[1]
        if target == "-":
            if len(args) < 4:
                raise SustechError("用法：/sustech-submit-preview <course_id> - <column_id> <file> [comment]")
            column_id, file_path = args[2], args[3]
            comment = " ".join(args[4:])
            return course_id, None, column_id, file_path, comment
        return course_id, target, None, args[2], " ".join(args[3:])

    def _preview_token(self, event, preview: dict, course_id: str, content_id: str | None,
                       column_id: str | None, file_path: str, comment: str) -> str:
        local_file = preview["local_file"]
        absolute = (self._input_root() / str(local_file["path"])).resolve()
        return self.confirmations.create(
            user_id=self._user_id(event), session_id=self._session_id(event),
            course_id=course_id, content_id=content_id, column_id=column_id,
            file_path=str(absolute), file_sha256=str(local_file["sha256"]),
            comment=comment, allow_late=False, preview=preview,
        )

    @filter.command("sustech-status")
    async def sustech_status(self, event: AstrMessageEvent):
        try:
            self._access_query(event)
            yield event.plain_result(self._status_text(await self.runner.status()))
        except SustechError as e:
            yield event.plain_result(e.user_message)
        except Exception:
            logger.exception("SUSTech status failed")
            yield event.plain_result(_ERROR_MESSAGE)

    @filter.command("sustech-ddl")
    async def sustech_deadlines(self, event: AstrMessageEvent):
        try:
            self._access_query(event)
            args = self._args(event)
            days = None
            course = ""
            if args:
                try:
                    days = int(args[0])
                    course = " ".join(args[1:])
                except ValueError:
                    course = " ".join(args)
            data = await self.runner.deadlines(days=days, course=course)
            effective = days
            if effective is None:
                try:
                    effective = int(self.config.get("default_deadline_days", 14))
                except (TypeError, ValueError):
                    effective = 14
            yield event.plain_result(formatter.format_deadlines(data, effective))
        except SustechError as e:
            yield event.plain_result(e.user_message)
        except Exception:
            logger.exception("SUSTech deadlines failed")
            yield event.plain_result(_ERROR_MESSAGE)

    @filter.command("sustech-schedule")
    async def sustech_schedule(self, event: AstrMessageEvent):
        try:
            self._access_query(event)
            args = self._args(event)
            date = args[0] if args else None
            data = await self.runner.schedule(date=date)
            yield event.plain_result(formatter.format_schedule(data, date_label=date))
        except SustechError as e:
            yield event.plain_result(e.user_message)
        except Exception:
            logger.exception("SUSTech schedule failed")
            yield event.plain_result(_ERROR_MESSAGE)

    @filter.command("sustech-courses")
    async def sustech_courses(self, event: AstrMessageEvent):
        try:
            self._access_query(event)
            yield event.plain_result(formatter.format_courses(await self.runner.courses(" ".join(self._args(event)))))
        except SustechError as e:
            yield event.plain_result(e.user_message)
        except Exception:
            logger.exception("SUSTech courses failed")
            yield event.plain_result(_ERROR_MESSAGE)

    @filter.command("sustech-download")
    async def sustech_download(self, event: AstrMessageEvent):
        try:
            self._access_file(event)
            args = self._args(event)
            if len(args) < 3:
                yield event.plain_result("用法：/sustech-download <course_id> <content_id> <attachment_id>")
                return
            data = await self.runner.download_attachment(*args[:3])
            yield event.plain_result(formatter.format_download_result(data["file"]))
        except SustechError as e:
            yield event.plain_result(e.user_message)
        except Exception:
            logger.exception("SUSTech download failed")
            yield event.plain_result(_ERROR_MESSAGE)

    @filter.command("sustech-calendar-export")
    async def sustech_calendar_export(self, event: AstrMessageEvent):
        try:
            self._access_file(event)
            data = await self.runner.export_calendar()
            yield event.plain_result(formatter.format_download_result(data["file"]))
        except SustechError as e:
            yield event.plain_result(e.user_message)
        except Exception:
            logger.exception("SUSTech calendar export failed")
            yield event.plain_result(_ERROR_MESSAGE)

    @filter.command("sustech-submit-preview")
    async def sustech_submit_preview(self, event: AstrMessageEvent):
        try:
            self._access_submission(event)
            course, content, column, file_path, comment = self._parse_submission(self._args(event))
            preview = await self.runner.submit_preview(course, file_path, content_id=content, column_id=column, comment=comment)
            token = self._preview_token(event, preview, course, content, column, file_path, comment)
            yield event.plain_result(formatter.format_submit_preview(preview, token))
        except SustechError as e:
            yield event.plain_result(e.user_message)
        except Exception:
            logger.exception("SUSTech submission preview failed")
            yield event.plain_result(_ERROR_MESSAGE)

    @filter.command("sustech-submit-confirm")
    async def sustech_submit_confirm(self, event: AstrMessageEvent):
        try:
            self._access_submission(event)
            args = self._args(event)
            if len(args) != 1:
                yield event.plain_result("用法：/sustech-submit-confirm <token>")
                return
            pending = self.confirmations.consume(args[0], user_id=self._user_id(event), session_id=self._session_id(event))
            if sha256_file(pending.file_path) != pending.file_sha256:
                yield event.plain_result("文件已变化，请重新执行提交预览。")
                return
            data = await self.runner.submit_apply(pending.course_id, pending.file_path, pending.file_sha256, content_id=pending.content_id, column_id=pending.column_id, comment=pending.comment, allow_late=pending.allow_late)
            yield event.plain_result(formatter.format_submit_result(data))
        except SustechError as e:
            yield event.plain_result(e.user_message)
        except Exception:
            logger.exception("SUSTech submission confirmation failed")
            yield event.plain_result(_ERROR_MESSAGE)

    async def _llm_guard(self, event) -> str | None:
        if not self._llm_enabled():
            return "SUSTech 查询工具未启用。"
        return None

    @filter.llm_tool(name="sustech_get_deadlines")
    async def sustech_get_deadlines(self, event, days: int = 14, course: str = "") -> str:
        """查询当前账号的 Blackboard 课程 DDL。

        Args:
            days(number): 查询未来多少天，范围为 1 到 90。
            course(string): 可选的课程名称或关键词。
        """
        try:
            if (blocked := await self._llm_guard(event)):
                return blocked
            self._access_query(event)
            return formatter.format_deadlines(await self.runner.deadlines(days=days, course=course), days)
        except SustechError as e:
            return e.user_message
        except Exception:
            logger.exception("SUSTech LLM deadlines failed")
            return _ERROR_MESSAGE

    @filter.llm_tool(name="sustech_get_schedule")
    async def sustech_get_schedule(self, event, date: str = "", week: int = 0, semester: str = "") -> str:
        """查询当前账号某天或某周的 TIS 课表。

        Args:
            date(string): 可选，YYYY-MM-DD 格式的日期。
            week(number): 可选，教学周次，0 表示未指定。
            semester(string): 可选，YYYY-YYYY-N 格式的学期。
        """
        try:
            if (blocked := await self._llm_guard(event)):
                return blocked
            self._access_query(event)
            data = await self.runner.schedule(semester=semester or None, week=week or None, date=date or None)
            return formatter.format_schedule(data, date_label=date or None)
        except SustechError as e:
            return e.user_message
        except Exception:
            logger.exception("SUSTech LLM schedule failed")
            return _ERROR_MESSAGE

    @filter.llm_tool(name="sustech_get_courses")
    async def sustech_get_courses(self, event, query: str = "") -> str:
        """查询当前账号的 Blackboard 课程列表。

        Args:
            query(string): 可选的课程名称关键词。
        """
        try:
            if (blocked := await self._llm_guard(event)):
                return blocked
            self._access_query(event)
            return formatter.format_courses(await self.runner.courses(query))
        except SustechError as e:
            return e.user_message
        except Exception:
            logger.exception("SUSTech LLM courses failed")
            return _ERROR_MESSAGE

    @filter.llm_tool(name="sustech_download_attachment")
    async def sustech_download_attachment(self, event, course_id: str, content_id: str, attachment_id: str) -> str:
        """下载指定 Blackboard 课程内容的附件到受控目录。

        Args:
            course_id(string): Blackboard 课程 ID。
            content_id(string): Blackboard 内容 ID。
            attachment_id(string): 附件 ID。
        """
        try:
            if (blocked := await self._llm_guard(event)):
                return blocked
            self._access_file(event)
            data = await self.runner.download_attachment(course_id, content_id, attachment_id)
            return formatter.format_download_result(data["file"])
        except SustechError as e:
            return e.user_message
        except Exception:
            logger.exception("SUSTech LLM download failed")
            return _ERROR_MESSAGE

    @filter.llm_tool(name="sustech_export_calendar")
    async def sustech_export_calendar(self, event) -> str:
        """将当前课表导出为 iCalendar 文件，写入受控目录。"""
        try:
            if (blocked := await self._llm_guard(event)):
                return blocked
            self._access_file(event)
            return formatter.format_download_result((await self.runner.export_calendar())["file"])
        except SustechError as e:
            return e.user_message
        except Exception:
            logger.exception("SUSTech LLM calendar export failed")
            return _ERROR_MESSAGE

    @filter.llm_tool(name="sustech_prepare_assignment_submission")
    async def sustech_prepare_assignment_submission(self, event, course_id: str, content_id: str, file: str, comment: str = "") -> str:
        """生成 Blackboard 作业提交预览，不会真正提交。真正提交必须由用户通过 /sustech-submit-confirm 明确确认。

        Args:
            course_id(string): Blackboard 课程 ID。
            content_id(string): 作业内容 ID。
            file(string): 待提交文件，相对于插件输入目录的路径。
            comment(string): 可选的提交备注，最长 2000 字符。
        """
        try:
            if (blocked := await self._llm_guard(event)):
                return blocked
            self._access_submission(event)
            preview = await self.runner.submit_preview(course_id, file, content_id=content_id, comment=comment)
            token = self._preview_token(event, preview, course_id, content_id, None, file, comment)
            return formatter.format_submit_preview(preview, token)
        except SustechError as e:
            return e.user_message
        except Exception:
            logger.exception("SUSTech LLM submission preview failed")
            return _ERROR_MESSAGE
