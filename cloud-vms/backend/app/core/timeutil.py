"""Time helpers. All timestamps are stored as naive UTC datetimes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def from_epoch(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def to_epoch(dt: datetime) -> float:
    return dt.replace(tzinfo=timezone.utc).timestamp()


@lru_cache
def local_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def to_local(dt_utc_naive: datetime) -> datetime:
    return dt_utc_naive.replace(tzinfo=timezone.utc).astimezone(local_tz())


def local_day_bounds_utc(day: datetime | None = None) -> tuple[datetime, datetime]:
    """Return [start, end) of the local calendar day as naive UTC datetimes."""
    now_local = (day or datetime.now(local_tz())).astimezone(local_tz())
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return (start_local.astimezone(timezone.utc).replace(tzinfo=None),
            end_local.astimezone(timezone.utc).replace(tzinfo=None))


def iso_z(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat(timespec="milliseconds") + "Z"
