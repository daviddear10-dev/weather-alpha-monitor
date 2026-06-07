from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

from .monitor import ForecastRecord, get_forecast_run_label

# data.gov.sg real-time 4-day Weather Forecast API
FOUR_DAY_URL = "https://api.data.gov.sg/v1/environment/4-day-weather-forecast"
SINGAPORE_TZ = "Asia/Singapore"
USER_AGENT = "weather-alpha-monitor/0.1 (test-singapore-nea)"


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    print("新加坡 NEA/MSS 官方天气源独立测试")
    print(f"API 端点: {FOUR_DAY_URL}")
    print()

    record = fetch_nea_forecast(session)
    if record is None:
        print("结果：获取失败")
    else:
        print("解析成功，ForecastRecord:")
        print(json.dumps(asdict(record), ensure_ascii=False, indent=4))


def fetch_nea_forecast(session: requests.Session) -> Optional[ForecastRecord]:
    tz = ZoneInfo(SINGAPORE_TZ)
    tomorrow_date = (datetime.now(tz).date() + timedelta(days=1)).isoformat()
    print(f"新加坡当地明天: {tomorrow_date}")

    # Request 4-day forecast
    params = {"date": datetime.now(tz).strftime("%Y-%m-%d")}
    try:
        response = session.get(FOUR_DAY_URL, params=params, timeout=25)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        print(f"4-day forecast API 请求失败: {exc}")
        return None

    # data.gov.sg API wraps data in "items" array
    items = payload.get("items", [])
    if not items:
        print("API 返回无 items 数据")
        print(f"  完整响应 keys: {sorted(payload.keys()) if isinstance(payload, dict) else type(payload).__name__}")
        return None

    # Use the first (latest) item
    item = items[0]
    if not isinstance(item, dict):
        print(f"items[0] 不是 dict: {type(item).__name__}")
        return None

    update_time = (
        item.get("update_timestamp")
        or item.get("timestamp")
        or (item.get("valid_period") or {}).get("start")
        or ""
    )
    forecasts = item.get("forecasts", [])
    if not forecasts:
        print("API 返回无 forecasts 数据")
        print(f"  item keys: {sorted(item.keys())}")
        return None

    print(f"API 返回 {len(forecasts)} 天预报")
    for f in forecasts[:4]:
        date_val = f.get("date", "?")
        low = f.get("temperature", {}).get("low", "?")
        high = f.get("temperature", {}).get("high", "?")
        desc = f.get("forecast", "")
        print(f"  {date_val}: {low}°C - {high}°C ({desc})")

    # Find tomorrow's forecast
    tomorrow_forecast = None
    for f in forecasts:
        if str(f.get("date")) == tomorrow_date:
            tomorrow_forecast = f
            break

    if tomorrow_forecast is None:
        available_dates = [str(f.get("date")) for f in forecasts]
        print(f"未找到 {tomorrow_date} 的预报数据")
        print(f"  可用日期: {available_dates}")
        return None

    temperature = tomorrow_forecast.get("temperature", {})
    if not isinstance(temperature, dict):
        print(f"temperature 字段格式异常: {type(temperature).__name__}")
        return None

    temp_low = temperature.get("low")
    temp_high = temperature.get("high")
    if temp_low is None or temp_high is None:
        print(f"temperature 缺少 low/high: {temperature}")
        return None

    try:
        temp_min = float(temp_low)
        temp_max = float(temp_high)
    except (TypeError, ValueError) as exc:
        print(f"温度值无法解析为 float: low={temp_low}, high={temp_high} ({exc})")
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


if __name__ == "__main__":
    main()
