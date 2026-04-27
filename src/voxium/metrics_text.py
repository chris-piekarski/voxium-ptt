"""Formatting helpers for console metrics (pure, Rich markup in strings)."""

from __future__ import annotations


def format_seconds(value) -> str:
    if value is None:
        return "[dim]n/a[/dim]"
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return "[dim]n/a[/dim]"
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    return f"{seconds:.2f} s"


def format_number(value, suffix: str = "", digits: int = 2) -> str:
    if value is None:
        return "[dim]n/a[/dim]"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        formatted = str(int(number))
    else:
        formatted = f"{number:.{digits}f}"
    return f"{formatted}{suffix}"


def format_number_plain(value, suffix: str = "", digits: int = 2) -> str:
    """Same as :func:`format_number` but without Rich markup (downlink / plain text)."""
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        formatted = str(int(number))
    else:
        formatted = f"{number:.{digits}f}"
    return f"{formatted}{suffix}"


def format_bytes(value) -> str:
    if value is None:
        return "[dim]n/a[/dim]"
    try:
        size = float(value)
    except (TypeError, ValueError):
        return str(value)
    units = ["B", "KB", "MB", "GB"]
    unit = units[0]
    for u in units:
        unit = u
        if size < 1024 or u == units[-1]:
            break
        size /= 1024
    digits = 0 if unit == "B" else 2
    return f"{size:.{digits}f} {unit}"


def format_optional_seconds(value) -> str:
    if isinstance(value, (list, tuple)):
        return " / ".join(format_seconds(item) for item in value)
    return format_seconds(value)


def describe_server(info: dict) -> str:
    model = info.get("model") or "unknown"
    device = info.get("device")
    compute = info.get("compute")
    details = [f"model={model}"]
    if device:
        details.append(f"device={device}")
    if compute:
        details.append(f"compute={compute}")
    return ", ".join(details)
