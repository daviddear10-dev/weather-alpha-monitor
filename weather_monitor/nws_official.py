from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from .monitor import ForecastRecord, get_forecast_run_label

NWS_POINTS_URL = "https://api.weather.gov/points/{lat},{lon}"
USER_AGENT = "weather-alpha-monitor/0.1"


def fetch_nws_forecast(
    city_name: str,
    latitude: float,
    longitude: float,
    timezone: str,
    session: Optional[requests.Session] = None,
) -> Optional[ForecastRecord]:
    """Fetch tomorrow's min/max temperature from NOAA/NWS for a US city.

    Args:
        city_name: Display name (e.g. "纽约").
        latitude: City latitude.
        longitude: City longitude.
        timezone: IANA timezone name (e.g. "America/New_York").
        session: Optional requests.Session; a new one is created if None.

    Returns:
        ForecastRecord on success, None on any failure (error is printed).
    """
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})

    tz = ZoneInfo(timezone)
    tomorrow_date = (datetime.now(tz).date() + timedelta(days=1)).isoformat()

    # Step 1: get gridpoint metadata
    points_url = NWS_POINTS_URL.format(lat=latitude, lon=longitude)
    try:
        response = session.get(points_url, timeout=20)
        response.raise_for_status()
        points_data = response.json()
    except Exception as exc:
        print(f"NWS points 请求失败 ({city_name}): {exc}")
        return None

    props = points_data.get("properties", {})
    forecast_url = props.get("forecast")
    if not forecast_url:
        print(
            f"NWS points 响应中无 forecast URL ({city_name}), "
            f"properties keys: {sorted(props.keys())}"
        )
        return None

    # Step 2: get daily forecast
    try:
        response = session.get(forecast_url, timeout=20)
        response.raise_for_status()
        forecast_data = response.json()
    except Exception as exc:
        print(f"NWS forecast 请求失败 ({city_name}): {exc}")
        return None

    periods = forecast_data.get("properties", {}).get("periods", [])
    if not periods:
        print(f"NWS forecast 中无 periods 数据 ({city_name})")
        return None

    # Parse periods: group day/night by local date (from startTime)
    daytime_temps: dict[str, list[float]] = {}
    nighttime_temps: dict[str, list[float]] = {}

    for period in periods:
        start_time_str = period.get("startTime", "")
        temp = period.get("temperature")
        temp_unit = period.get("temperatureUnit", "F")
        is_daytime = period.get("isDaytime", False)

        if temp is None or not start_time_str:
            continue

        date_str = start_time_str[:10]
        temp_c = round(
            fahrenheit_to_celsius(temp) if temp_unit == "F" else float(temp), 1
        )

        if is_daytime:
            daytime_temps.setdefault(date_str, []).append(temp_c)
        else:
            nighttime_temps.setdefault(date_str, []).append(temp_c)

    # Find tomorrow's data
    all_temps = daytime_temps.get(tomorrow_date, []) + nighttime_temps.get(
        tomorrow_date, []
    )
    if not all_temps:
        available_dates = sorted(
            set(list(daytime_temps.keys()) + list(nighttime_temps.keys()))
        )
        print(
            f"NWS 未找到 {tomorrow_date} 的温度数据 ({city_name}), "
            f"可用日期: {available_dates}"
        )
        return None

    temp_min = min(all_temps)
    temp_max = max(all_temps)

    update_time = forecast_data.get("properties", {}).get("generatedAt", "")
    if not update_time:
        update_time = forecast_data.get("properties", {}).get("updated", "")
    fetched_at = datetime.now(ZoneInfo(timezone)).isoformat(timespec="seconds")

    return ForecastRecord(
        fetched_at=fetched_at,
        forecast_run_label=get_forecast_run_label(),
        city=city_name,
        source="NOAA/NWS",
        forecast_date=tomorrow_date,
        temp_min=temp_min,
        temp_max=temp_max,
        data_update_time=update_time,
    )


def fahrenheit_to_celsius(f: float) -> float:
    return (f - 32) * 5 / 9
