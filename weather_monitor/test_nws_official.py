from __future__ import annotations

import json
from dataclasses import asdict

import requests

from .nws_official import fetch_nws_forecast

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
        record = fetch_nws_forecast(
            city_name=city_cfg["city"],
            latitude=city_cfg["latitude"],
            longitude=city_cfg["longitude"],
            timezone=city_cfg["timezone"],
            session=session,
        )
        if record is None:
            print("  结果：获取失败")
        else:
            print("  解析成功，ForecastRecord:")
            print(json.dumps(asdict(record), ensure_ascii=False, indent=4))
        print()


if __name__ == "__main__":
    main()
