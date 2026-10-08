"""Decide whether a runner-produced file should be sent in chat.

This module deliberately has no AstrBot imports so the gating and path logic can
be tested with the standard library alone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import formatter


logger = logging.getLogger(__name__)

_DEFAULT_PLATFORMS = ("qqofficial", "webchat")


@dataclass(frozen=True)
class FileDelivery:
    """A validated local file ready to be converted to an AstrBot File segment."""

    summary_text: str
    file_name: str
    abs_path: Path


def _config_value(config: Mapping[str, Any] | Any, key: str, default: Any) -> Any:
    """Read a config value from a mapping or AstrBotConfig-like object."""
    try:
        getter = getattr(config, "get", None)
        if callable(getter):
            value = getter(key, default)
        else:
            value = config[key]
    except (KeyError, TypeError, AttributeError):
        return default
    return default if value is None else value


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"", "0", "false", "no", "off"}:
            return False
        if text in {"1", "true", "yes", "on"}:
            return True
    return bool(value)


def _platforms(config: Mapping[str, Any] | Any) -> set[str]:
    value = _config_value(config, "file_message_platforms", list(_DEFAULT_PLATFORMS))
    if isinstance(value, str):
        # The schema requires a list; malformed scalar values disable delivery.
        return set()
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set(_DEFAULT_PLATFORMS)
    return {str(item).strip() for item in value if str(item).strip()}


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def prepare_file_delivery(
    platform_name: str,
    config: Mapping[str, Any] | Any,
    file_info: Mapping[str, Any] | dict[str, Any],
    download_root: Path | str,
) -> FileDelivery | None:
    """Build a file-delivery plan, or return ``None`` for text-only fallback.

    ``file_info["rel_path"]`` must be the final relative path returned by the
    runner after output finalization (including any remote filename rename).
    """
    if not _as_bool(_config_value(config, "send_file_in_chat", True), True):
        return None
    if str(platform_name or "") not in _platforms(config):
        return None
    if not isinstance(file_info, Mapping):
        return None

    rel_path = file_info.get("rel_path")
    file_name = file_info.get("name")
    if not isinstance(rel_path, str) or not rel_path.strip():
        return None
    if not isinstance(file_name, str) or not file_name:
        return None

    root = Path(download_root).resolve()
    candidate = (root / rel_path).resolve()
    if not _within(candidate, root):
        logger.warning("Refusing file delivery outside download root: %s", rel_path)
        return None
    if not candidate.is_file():
        logger.warning("File delivery target is missing: %s", candidate)
        return None

    return FileDelivery(
        summary_text=formatter.format_download_result(dict(file_info)),
        file_name=file_name,
        abs_path=candidate,
    )


__all__ = ["FileDelivery", "prepare_file_delivery"]
