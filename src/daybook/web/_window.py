"""Shared local-today defaults and aware half-open query windows."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from daybook.journal import TimeRange


def local_now() -> datetime:
    name = os.environ.get("TZ", "").lstrip(":")
    if name:
        try:
            return datetime.now(ZoneInfo(name))
        except (ZoneInfoNotFoundError, ValueError):
            pass
    try:
        with Path("/etc/localtime").open("rb") as source:
            zone = ZoneInfo.from_file(source)
        return datetime.now(zone)
    except (OSError, ValueError):
        return datetime.now(UTC)


def time_range(start: datetime | None, end: datetime | None) -> TimeRange:
    if any(t is not None and t.tzinfo is None for t in (start, end)):
        raise HTTPException(422, "invalid_time_range")
    end = end or local_now()
    start = start or end.replace(hour=0, minute=0, second=0, microsecond=0)
    if start >= end:
        raise HTTPException(422, "invalid_time_range")
    return TimeRange(start.astimezone(UTC), end.astimezone(UTC))
