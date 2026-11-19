"""Reading the times people type, like "1:23-2:05", "83-125" or "1:02:03 - 1:02:45"."""
from __future__ import annotations

import re

MIN_LENGTH = 1.0          # seconds
MAX_LENGTH = 10 * 60.0    # Shorts are at most 3 minutes; longer parts are allowed, up to this

_RANGE = re.compile(r"^\s*([\d:.]+)\s*(?:-|–|—|\bto\b)\s*([\d:.]+)\s*$", re.IGNORECASE)
_WHOLE = re.compile(r"^\d+$")
_SECONDS = re.compile(r"^\d+(?:\.\d+)?$")


class TimestampError(ValueError):
    """A time or range that cannot be read. The message says how to write it."""


def parse_time(text: str) -> float:
    """Seconds from "83", "1:23", "1:23.5" or "1:02:03"."""
    raw = text.strip()
    parts = raw.split(":")
    if (not raw or len(parts) > 3 or not all(_WHOLE.match(p) for p in parts[:-1])
            or not _SECONDS.match(parts[-1])):
        raise TimestampError(f'"{raw}" is not a time. Write it like 1:23, 1:23.5 or 1:02:03.')
    numbers = [float(p) for p in parts]
    if any(n >= 60 for n in numbers[1:]):
        raise TimestampError(f'"{raw}" is not a time: minutes and seconds go up to 59.')
    seconds = 0.0
    for n in numbers:
        seconds = seconds * 60 + n
    return round(seconds, 3)


def parse_range(text: str) -> tuple[float, float]:
    """(start, end) in seconds from "1:23-2:05". Dashes and the word "to" both work."""
    shown = text.strip()
    match = _RANGE.match(text)
    if not match:
        raise TimestampError(f'"{shown}" is not a time range. Write it like 1:23-2:05.')
    start, end = parse_time(match.group(1)), parse_time(match.group(2))
    if end <= start:
        raise TimestampError(f'"{shown}": the end has to be after the start.')
    if end - start < MIN_LENGTH:
        raise TimestampError(f'"{shown}" is shorter than a second.')
    if end - start > MAX_LENGTH:
        raise TimestampError(f'"{shown}" is longer than 10 minutes. Cut it into shorter parts.')
    return start, end


def parse_ranges(items) -> list[tuple[float, float]]:
    """Every range in the arguments. One argument may hold several, separated by commas."""
    ranges = [parse_range(piece) for item in items for piece in item.split(",") if piece.strip()]
    if not ranges:
        raise TimestampError("Give at least one time range, like 1:23-2:05.")
    return ranges


def clock(seconds: float) -> str:
    """For messages: 83.5 -> "1:23.5", 3723 -> "1:02:03"."""
    total = round(max(0.0, seconds), 1)
    whole = int(total)
    tenths = int(round((total - whole) * 10))
    h, m, s = whole // 3600, whole % 3600 // 60, whole % 60
    text = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    return f"{text}.{tenths}" if tenths else text


def slug(seconds: float) -> str:
    """For folder names: 83 -> "01m23s", 3723 -> "1h02m03s"."""
    whole = int(max(0.0, seconds))
    h, m, s = whole // 3600, whole % 3600 // 60, whole % 60
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m:02d}m{s:02d}s"
