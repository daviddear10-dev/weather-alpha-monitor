from __future__ import annotations

import json
from dataclasses import asdict

import requests

from .singapore_nea import fetch_singapore_nea_forecast

USER_AGENT = "weather-alpha-monitor/0.1 (test-singapore-nea)"


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    print("新加坡 NEA/MSS 官方天气源独立测试")
    print("API 端点: https://api.data.gov.sg/v1/environment/4-day-weather-forecast")
    print()

    record = fetch_singapore_nea_forecast(session=session)
    if record is None:
        print("结果：获取失败")
    else:
        print("解析成功，ForecastRecord:")
        print(json.dumps(asdict(record), ensure_ascii=False, indent=4))


if __name__ == "__main__":
    main()
