"""Human-readable formatters for sustech command output."""

from datetime import date as _date
from datetime import datetime
from typing import Any


_UNKNOWN = "未知"


def _items(data: dict) -> list[Any]:
    """Return the command's result list using its supported field aliases."""
    if not isinstance(data, dict):
        return []
    for key in ("items", "results"):
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


def _course_name(item: Any) -> str:
    return _text(item, "course_name", "course", "title")


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
    """Format upcoming course deadlines."""
    items = _items(data)
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
        raw_status = _value(item, "status", "state")
        status = status_map.get(str(raw_status), "其他") if raw_status is not None else _UNKNOWN
        lines.extend(
            [
                f"{index}. {_course_name(item)}",
                f"   截止时间：{_text(item, 'due_date', 'deadline')}",
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


def format_schedule(data: dict, date_label: str | None = None) -> str:
    """Format a day's course schedule."""
    items = _items(data)
    if not items:
        return "当天没有课程安排。"

    first = items[0]
    if date_label is None:
        date_value = _value(first, "date")
        weekday = _weekday_label(date_value)
        if weekday is not None:
            date_label = f"{date_value} {weekday}"
        else:
            date_label = weekday or _UNKNOWN
    lines = [f"{date_label}课表：", ""]
    for index, item in enumerate(items):
        start = _value(item, "start_time", "start")
        end = _value(item, "end_time", "end")
        if start is not None or end is not None:
            time_label = f"{start if start is not None else _UNKNOWN}-{end if end is not None else _UNKNOWN}"
        else:
            time_label = _text(item, "time")
        lines.extend(
            [
                f"{time_label}  {_course_name(item)}",
                f"地点：{_text(item, 'location', 'room')}",
                f"教师：{_text(item, 'teacher', 'instructor')}",
            ]
        )
        if index != len(items) - 1:
            lines.append("")
    return "\n".join(lines)


def format_courses(data: dict) -> str:
    """Format the current Blackboard course list."""
    items = _items(data)
    if not items:
        return "当前没有查询到 Blackboard 课程。"
    lines = ["当前 Blackboard 课程：", ""]
    for index, item in enumerate(items, 1):
        lines.append(f"{index}. {_text(item, 'name', 'course_name', 'course', 'title')}")
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
    late = _value(preview, "is_late")
    late_label = "是" if late is True or str(late).lower() == "true" else "否"
    return "\n".join(
        [
            "请确认是否提交以下作业：",
            f"课程：{_text(preview, 'course_name', 'course', 'title')}",
            f"作业：{_text(preview, 'assignment_name')}",
            f"文件：{_text(preview, 'file_name')}",
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
    """Format a submission result, including the non-retryable uncertainty case."""
    if isinstance(data, dict) and (
        data.get("status") == "unknown"
        or data.get("code") == "DO_NOT_RETRY_AUTOMATICALLY"
    ):
        return "\n".join(
            [
                "Blackboard 返回的提交结果不确定，插件不会自动重试。",
                "请登录 Blackboard 检查提交状态后再决定是否操作。",
            ]
        )
    return "\n".join(
        [
            "作业提交成功。",
            f"课程：{_text(data, 'course_name', 'course', 'title')}",
            f"作业：{_text(data, 'assignment_name')}",
            "提交状态：已确认",
        ]
    )
