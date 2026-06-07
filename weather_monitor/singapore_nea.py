from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from .monitor import ForecastRecord, get_forecast_run_label

FOUR_DAY_URL = "https://api.data.gov.sg/v1/environment/4-day-weather-forecast"
SINGAPORE_TZ = "Asia/Singapore"
USER_AGENT = "weather-alpha-monitor/0.1"


def fetch_singapore_nea_forecast(
    session: Optional[requests.Session] = None,
) -> Optional[ForecastRecord]:
    """Fetch tomorrow's min/max temperature from Singapore NEA/MSS 4-day forecast.

    Args:
        session: Optional requests.Session; a new one is created if None.

    Returns:
        ForecastRecord on success, None on any failure (error is printed).
    """
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})

    tz = ZoneInfo(SINGAPORE_TZ)
    tomorrow_date = (datetime.now(tz).date() + timedelta(days=1)).isoformat()

    # Request 4-day forecast
    params = {"date": datetime.now(tz).strftime("%Y-%m-%d")}
    try:
        response = session.get(FOUR_DAY_URL, params=params, timeout=25)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        print(f"Singapore NEA 4-day forecast API 请求失败: {exc}")
        return None

    items = payload.get("items", [])
    if not items:
        print("Singapore NEA API 返回无 items 数据")
        return None

    item = items[0]
    if not isinstance(item, dict):
        print(f"Singapore NEA items[0] 不是 dict: {type(item).__name__}")
        return None

    update_time = (
        item.get("update_timestamp")
        or item.get("timestamp")
        or (item.get("valid_period") or {}).get("start")
        or ""
    )

    forecasts = item.get("forecasts", [])
    if not forecasts:
        print("Singapore NEA API 返回无 forecasts 数据")
        return None

    # Find tomorrow's forecast
    tomorrow_forecast = None
    for f in forecasts:
        if str(f.get("date")) == tomorrow_date:
            tomorrow_forecast = f
            break

    if tomorrow_forecast is None:
        available_dates = [str(f.get("date")) for f in forecasts]
        print(
            f"Singapore NEA 未找到 {tomorrow_date} 的预报数据, "
            f"可用日期: {available_dates}"
        )
        return None

    temperature = tomorrow_forecast.get("temperature", {})
    if not isinstance(temperature, dict):
        print(
            f"Singapore NEA temperature 字段格式异常: "
            f"{type(temperature).__name__}"
        )
        return None

    temp_low = temperature.get("low")
    temp_high = temperature.get("high")
    if temp_low is None or temp_high is None:
        print(f"Singapore NEA temperature 缺少 low/high: {temperature}")
        return None

    try:
        temp_min = float(temp_low)
        temp_max = float(temp_high)
    except (TypeError, ValueError) as exc:
        print(
            f"Singapore NEA 温度值无法解析: "
            f"low={temp_low}, high={temp_high} ({exc})"
        )
        return None

    data_update_time = str(update_time)
    fetched_at = datetime.now(ZoneInfo(SINGAPORE_TZ)).isoformat(timespec="seconds")

    return ForecastRecord(
        fetched_at=fetched_at,
        forecast_run_label=get_forecast_run_label(),
        city="新加坡",
        source="NEA/MSS",
        forecast_date=tomorrow_date,
        temp_min=temp_min,
        temp_max=temp_max,
        data_update_time=data_update_time,
    )
