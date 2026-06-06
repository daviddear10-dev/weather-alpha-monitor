from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

from .monitor import ForecastRecord, get_forecast_run_label

NWS_POINTS_URL = "https://api.weather.gov/points/{lat},{lon}"
USER_AGENT = "weather-alpha-monitor/0.1 (test-nws)"

TEST_CITIES = [
    {
        "city": "纽约",
        "latitude": 40.7128,
        "longitude": -74.0060,
        "timezone": "America/New_York",
    },
    {
        "city": "洛杉矶",
        "latitude": 34.0522,
        "longitude": -118.2437,
        "timezone": "America/Los_Angeles",
    },
    {
        "city": "迈阿密",
        "latitude": 25.7617,
        "longitude": -80.1918,
        "timezone": "America/New_York",
    },
]


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    print("NOAA/NWS 官方天气源独立测试")
    print("目标城市：纽约、洛杉矶、迈阿密")
    print()

    for city_cfg in TEST_CITIES:
        print(f"--- {city_cfg['city']} ---")
        record = fetch_nws_forecast(session, city_cfg)
        if record is None:
            print(f"  结果：获取失败")
        else:
            print("  解析成功，ForecastRecord:")
            print(json.dumps(asdict(record), ensure_ascii=False, indent=4))
        print()


def fetch_nws_forecast(
    session: requests.Session,
    city_cfg: dict[str, Any],
) -> Optional[ForecastRecord]:
    lat = city_cfg["latitude"]
    lon = city_cfg["longitude"]
    city_name = city_cfg["city"]
    tz_name = city_cfg["timezone"]

    tz = ZoneInfo(tz_name)
    tomorrow_date = (datetime.now(tz).date() + timedelta(days=1)).isoformat()

    # Step 1: get gridpoint metadata
    points_url = NWS_POINTS_URL.format(lat=lat, lon=lon)
    try:
        response = session.get(points_url, timeout=20)
        response.raise_for_status()
        points_data = response.json()
    except Exception as exc:
        print(f"  NWS points 请求失败: {exc}")
        return None

    props = points_data.get("properties", {})
    forecast_url = props.get("forecast")
    if not forecast_url:
        print(f"  NWS points 响应中无 forecast URL，properties keys: {sorted(props.keys())}")
        return None

    # Step 2: get daily forecast
    try:
        response = session.get(forecast_url, timeout=20)
        response.raise_for_status()
        forecast_data = response.json()
    except Exception as exc:
        print(f"  NWS forecast 请求失败: {exc}")
        return None

    periods = forecast_data.get("properties", {}).get("periods", [])
    if not periods:
        print("  NWS forecast 中无 periods 数据")
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

        # Extract date part from ISO timestamp
        date_str = start_time_str[:10]

        # Convert to Celsius if Fahrenheit
        temp_c = round(fahrenheit_to_celsius(temp) if temp_unit == "F" else float(temp), 1)

        if is_daytime:
            daytime_temps.setdefault(date_str, []).append(temp_c)
        else:
            nighttime_temps.setdefault(date_str, []).append(temp_c)

    # Find tomorrow's data
    daytime_for_tomorrow = daytime_temps.get(tomorrow_date, [])
    nighttime_for_tomorrow = nighttime_temps.get(tomorrow_date, [])

    all_temps = daytime_for_tomorrow + nighttime_for_tomorrow
    if not all_temps:
        print(f"  未找到 {tomorrow_date} 的温度数据")
        print(f"  NWS 可用日期: {sorted(set(list(daytime_temps.keys()) + list(nighttime_temps.keys())))}")
        return None

    temp_min = min(all_temps)
    temp_max = max(all_temps)

    # data_update_time from forecast generation
    update_time = forecast_data.get("properties", {}).get("generatedAt", "")
    if not update_time:
        update_time = forecast_data.get("properties", {}).get("updated", "")
    fetched_at = datetime.now(ZoneInfo(tz_name)).isoformat(timespec="seconds")

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


if __name__ == "__main__":
    main()
