from __future__ import annotations

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .hong_kong_forecast_cache import (
    ForecastCacheEntry,
    save_forecast_cache,
)
from .test_hong_kong_live_probability import extract_hko_today_max_temp

HKO_TZ = ZoneInfo("Asia/Hong_Kong")
HKO_FLW_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"


def main() -> None:
    now_hk = datetime.now(HKO_TZ)
    today_str = now_hk.strftime("%Y-%m-%d")

    print("HKO 今日预测快照采集")
    print(f"香港当地时间: {now_hk.isoformat(timespec='seconds')}")
    print(f"目标日期:      {today_str}")
    print()

    # Fetch flw
    session = requests.Session()
    session.headers.update({"User-Agent": "weather-alpha-monitor/0.1"})
    params = {"dataType": "flw", "lang": "sc"}

    try:
        r = session.get(HKO_FLW_URL, params=params, timeout=25)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        print(f"flw 请求失败: {exc}")
        print("正常退出，不修改缓存")
        sys.exit(0)

    forecast_period = data.get("forecastPeriod", "")
    update_time = data.get("updateTime", "")
    forecast_desc = data.get("forecastDesc", "")

    print(f"forecastPeriod: {forecast_period}")
    print(f"updateTime:     {update_time}")
    print(f"forecastDesc:   {forecast_desc[:300]}")
    print()

    # Try to extract today's max temp
    today_max = extract_hko_today_max_temp(forecast_desc)

    if today_max is None:
        print("forecastDesc 未包含明确的今日最高温")
        print("flw 可能已切换至今晚/明日预报，不覆盖已有缓存")
        print("正常退出")
        sys.exit(0)

    # Save cache
    entry = ForecastCacheEntry(
        forecast_date=today_str,
        source="香港天文台 Local Weather Forecast",
        forecast_high=today_max,
        captured_at=now_hk.isoformat(timespec="seconds"),
        update_time=update_time,
        forecast_period=forecast_period,
        forecast_desc=forecast_desc[:500],
    )

    try:
        save_forecast_cache(entry)
    except Exception as exc:
        print(f"缓存保存失败: {exc}")
        sys.exit(1)

    print(f"✓ 快照已保存")
    print(f"  预测日期:   {today_str}")
    print(f"  最高温:     {today_max}℃")
    print(f"  抓取时间:   {entry.captured_at}")


if __name__ == "__main__":
    main()
