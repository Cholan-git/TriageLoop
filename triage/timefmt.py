"""Simulation clock formatting.

A run's operational clock is ``start_local`` (a local wall-clock time in the
run's timezone) plus the run's simulated minutes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def sim_datetime(st, minute: int) -> datetime:
    return datetime.fromisoformat(st.start_local) + timedelta(minutes=minute)


def sim_clock(st, minute: int) -> dict:
    dt = sim_datetime(st, minute)
    return {"minute": minute, "date": dt.strftime("%d %b %Y"), "time": dt.strftime("%H:%M"),
            "iso": dt.isoformat(timespec="minutes")}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_now(tz_name: str) -> datetime:
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone.utc
    return datetime.now(tz).replace(second=0, microsecond=0, tzinfo=None)


def valid_timezone(tz_name: str) -> bool:
    try:
        ZoneInfo(tz_name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False
