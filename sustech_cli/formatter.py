"""Human-readable formatters for sustech command output."""

from datetime import date as _date
from datetime import datetime
from typing import Any


_UNKNOWN = "未知"


def _items(data: dict, *keys: str) -> list[Any]:
    """Return the command's result list from the first matching payload key."""
    if not isinstance(data, dict):
        return []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _value(item: Any, *keys: str) -> Any:
    if not isinstance(item, dict):
        return None
    for key in keys:
        value = item.get(key)
        if value is not None and value != "":
            return value
    return None


def _text(item: Any, *keys: str) -> str:
    value = _value(item, *keys)
    return _UNKNOWN if value is None else str(value)



def _size_text(size: Any) -> str:
    try:
        value = float(size)
    except (TypeError, ValueError):
        return _UNKNOWN
    if value < 1024:
        number = value
        unit = "B"
        return f"{_number(number)} {unit}"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / (1024 * 1024):.1f} MB"


def _number(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def format_deadlines(data: dict, days: int) -> str:
    """Format upcoming course deadlines (``bb deadlines`` payload)."""
    items = _items(data, "deadlines")
    if not items:
        return f"未来 {days} 天没有发现课程 DDL。"

    status_map = {
        "not_attempted": "未提交",
        "in_progress": "进行中",
        "submitted": "已提交",
        "completed": "已完成",
        "mixed": "混合",
    }
    lines = [f"未来 {days} 天共有 {len(items)} 项课程 DDL：", ""]
    for index, item in enumerate(items, 1):
        summary = _value(item, "attemptSummary")
        raw_status = _value(summary, "state") if isinstance(summary, dict) else None
        status = status_map.get(str(raw_status), "其他") if raw_status is not None else _UNKNOWN
        title = _text(item, "title")
        lines.extend(
            [
                f"{index}. {_text(item, 'courseName', 'course_name')} — {title}",
                f"   截止时间：{_text(item, 'dueAt', 'due_date')}",
                f"   状态：{status}",
            ]
        )
        if index != len(items):
            lines.append("")
    return "\n".join(lines)


def _weekday_label(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    parsed: datetime | _date | None = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = _date.fromisoformat(text)
        except ValueError:
            return None
    weekdays = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
    return weekdays[parsed.weekday()]


def _time_label(item: Any) -> str:
    """HH:MM-HH:MM from startAt/endAt ISO datetimes, else period range."""
    def hhmm(value: Any) -> str | None:
        if value is None:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%H:%M")
        except ValueError:
            return None

    start = hhmm(_value(item, "startAt", "start"))
    end = hhmm(_value(item, "endAt", "end"))
    if start is not None or end is not None:
        return f"{start or _UNKNOWN}-{end or _UNKNOWN}"
    period_start = _value(item, "periodStart")
    period_end = _value(item, "periodEnd")
    if period_start is not None and period_end is not None:
        return f"第{period_start}-{period_end}节"
    return _UNKNOWN


def format_schedule(data: dict, date_label: str | None = None) -> str:
    """Format a course schedule (``tis schedule`` payload)."""
    items = _items(data, "entries")
    if not items:
        return "没有查询到课程安排。"

    if date_label is None:
        date_value = _value(data, "date")
        weekday = _weekday_label(date_value)
        if date_value is not None and weekday is not None:
            date_label = f"{date_value} {weekday}"
        elif date_value is not None:
            date_label = str(date_value)
        elif _value(data, "week") is not None:
            date_label = f"第{_value(data, 'week')}周"
        else:
            date_label = _UNKNOWN
    lines = [f"{date_label}课表：", ""]
    for index, item in enumerate(items):
        lines.extend(
            [
                f"{_time_label(item)}  {_text(item, 'courseName', 'course_name')}",
                f"地点：{_text(item, 'room', 'location')}",
                f"教师：{_text(item, 'teacher', 'instructor')}",
            ]
        )
        if index != len(items) - 1:
            lines.append("")
    return "\n".join(lines)


def format_courses(data: dict) -> str:
    """Format the current Blackboard course list (``bb courses`` payload)."""
    items = _items(data, "courses")
    if not items:
        return "当前没有查询到 Blackboard 课程。"
    lines = ["当前 Blackboard 课程：", ""]
    for index, item in enumerate(items, 1):
        code = _value(item, "courseCode")
        course_id = _value(item, "id")
        suffix = " ".join(str(part) for part in (code, course_id) if part)
        name = _text(item, "name", "courseName", "title")
        lines.append(f"{index}. {name}（{suffix}）" if suffix else f"{index}. {name}")
    return "\n".join(lines)


def format_contents(data: dict) -> str:
    """Format one level of course content items (``bb content`` payload)."""
    items = _items(data, "items")
    if not items:
        return "该目录下没有内容项。"
    kind_map = {"folder": "文件夹", "file": "文件", "assignment": "作业", "document": "文档"}
    lines = ["课程内容：", ""]
    for index, item in enumerate(items, 1):
        kind = kind_map.get(str(_value(item, "kind")), "内容")
        title = _text(item, "title")
        content_id = _text(item, "id")
        child_hint = "（含子项目）" if _value(item, "hasChildren") is True else ""
        lines.append(f"{index}. [{kind}] {title}{child_hint}（id: {content_id}）")
    lines.extend(["", "文件夹可用 parent_id 继续展开；文件/作业可查看其附件。"])
    return "\n".join(lines)


def format_attachments(data: dict) -> str:
    """Format the attachment list of one content item (``bb attachments`` payload)."""
    items = _items(data, "attachments")
    if not items:
        return "该内容项没有附件。"
    lines = ["附件列表：", ""]
    for index, item in enumerate(items, 1):
        name = _text(item, "fileName", "name")
        attachment_id = _text(item, "id")
        mime = _value(item, "mimeType")
        suffix = f" · {mime}" if mime else ""
        lines.append(f"{index}. {name}{suffix}（id: {attachment_id}）")
    return "\n".join(lines)


def format_download_result(file: dict) -> str:
    """Format a successful file download result."""
    return "\n".join(
        [
            "文件下载成功：",
            f"文件名：{_text(file, 'name')}",
            f"大小：{_size_text(_value(file, 'size'))}",
            f"SHA-256：{_text(file, 'sha256')}",
        ]
    )


def format_submit_preview(preview: dict, token: str) -> str:
    """Format a submission preview without implying that submission occurred."""
    local_file = _value(preview, "local_file")
    if not isinstance(local_file, dict):
        local_file = {}
    assignment = _value(preview, "assignment")
    if not isinstance(assignment, dict):
        assignment = {}
    target = _value(preview, "target")
    if not isinstance(target, dict):
        target = {}
    submission = _value(preview, "submission")
    submitted_file = _value(submission, "file") if isinstance(submission, dict) else None
    if not isinstance(submitted_file, dict):
        submitted_file = {}
    late = _value(preview, "late")
    late_label = "是" if late is True or str(late).lower() == "true" else "否"
    return "\n".join(
        [
            "请确认是否提交以下作业：",
            f"课程：{_text(target, 'courseId', 'course_id')}",
            f"作业：{_text(assignment, 'title')}",
            f"文件：{_text(submitted_file, 'name')}",
            f"大小：{_size_text(_value(local_file, 'size'))}",
            f"SHA-256：{_text(local_file, 'sha256')}",
            f"是否迟交：{late_label}",
            "",
            "如需提交，请回复：",
            f"/sustech-submit-confirm {token}",
            "",
            "预览不会执行远程提交。",
        ]
    )


def format_submit_result(data: dict) -> str:
    """Format a successful submission result (``bb submit apply`` payload)."""
    assignment = _value(data, "assignment")
    if not isinstance(assignment, dict):
        assignment = {}
    target = _value(data, "target")
    if not isinstance(target, dict):
        target = {}
    verification = _value(data, "verification")
    verified = isinstance(verification, dict) and verification.get("status") == "confirmed"
    status_text = "已确认" if verified else "已提交（回读验证未完全确认）"
    return "\n".join(
        [
            "作业提交成功。",
            f"课程：{_text(target, 'courseId', 'course_id')}",
            f"作业：{_text(assignment, 'title')}",
            f"提交状态：{status_text}",
        ]
    )
