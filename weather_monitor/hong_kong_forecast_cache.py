from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

HKO_TZ = ZoneInfo("Asia/Hong_Kong")

_DEFAULT_CACHE_DIR = "data"
_DEFAULT_CACHE_FILENAME = "hong_kong_today_forecast_cache.json"


def _resolve_cache_path(cache_dir: str | None = None) -> str:
    """Resolve the full cache file path, with optional directory override."""
    base = cache_dir if cache_dir is not None else _DEFAULT_CACHE_DIR
    return os.path.join(base, _DEFAULT_CACHE_FILENAME)


@dataclass
class ForecastCacheEntry:
    forecast_date: str     # "YYYY-MM-DD" in HKT
    source: str
    forecast_high: float
    captured_at: str       # ISO timestamp
    update_time: str       # HKO flw updateTime
    forecast_period: str   # HKO flw forecastPeriod
    forecast_desc: str     # HKO flw forecastDesc (truncated if very long)


def save_forecast_cache(
    entry: ForecastCacheEntry,
    *,
    cache_dir: str | None = None,
) -> None:
    """Atomically write today's HKO forecast snapshot to the cache file."""
    cache_file = _resolve_cache_path(cache_dir)
    dir_path = os.path.dirname(cache_file) or "."
    Path(dir_path).mkdir(parents=True, exist_ok=True)

    data = {
        "forecast_date": entry.forecast_date,
        "source": entry.source,
        "forecast_high": entry.forecast_high,
        "captured_at": entry.captured_at,
        "update_time": entry.update_time,
        "forecast_period": entry.forecast_period,
        "forecast_desc": entry.forecast_desc,
    }

    # Atomic write: temp file then rename
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, cache_file)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_forecast_cache(
    *,
    cache_dir: str | None = None,
) -> Optional[ForecastCacheEntry]:
    """Load today's valid HKO forecast snapshot from cache.

    Returns None if cache missing, corrupted, or forecast_date != today in HKT.
    """
    cache_file = _resolve_cache_path(cache_dir)
    if not os.path.isfile(cache_file):
        return None

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(data, dict):
        return None

    today = datetime.now(HKO_TZ).strftime("%Y-%m-%d")
    if data.get("forecast_date") != today:
        return None

    forecast_high = data.get("forecast_high")
    if not isinstance(forecast_high, (int, float)):
        return None

    return ForecastCacheEntry(
        forecast_date=str(data.get("forecast_date", "")),
        source=str(data.get("source", "")),
        forecast_high=float(forecast_high),
        captured_at=str(data.get("captured_at", "")),
        update_time=str(data.get("update_time", "")),
        forecast_period=str(data.get("forecast_period", "")),
        forecast_desc=str(data.get("forecast_desc", "")),
    )
