from __future__ import annotations

import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .hong_kong_realtime import fetch_hong_kong_realtime
from .hong_kong_realtime_history import save_realtime_observation


OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
HKO_TZ = ZoneInfo("Asia/Hong_Kong")

# 暂时沿用项目原本用于香港的坐标。
HONG_KONG_LATITUDE = 22.3193
HONG_KONG_LONGITUDE = 114.1694


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "weather-alpha-monitor/0.1"})

    hko_observation = fetch_hong_kong_realtime_with_retries(session)
    if hko_observation is None:
        print(
            "连续 3 次无法获取完整的香港天文台实时数据",
            file=sys.stderr,
        )
        sys.exit(1)

    open_meteo_result = fetch_open_meteo_current_with_retries(session)
    if open_meteo_result is None:
        print(
            "连续 3 次无法获取 Open-Meteo 当前温度",
            file=sys.stderr,
        )
        sys.exit(1)

    open_meteo_temp, open_meteo_observed_at = open_meteo_result

    record = save_realtime_observation(
        observation=hko_observation,
        open_meteo_current_temp=open_meteo_temp,
        open_meteo_observed_at=open_meteo_observed_at,
    )

    print("✓ 已保存双源实时温度")
    print(f"  captured_at: {record['captured_at']}")
    print(f"  香港天文台当前温度: {record['hko_current_temp']}℃")
    print(f"  Open-Meteo 当前温度: {record['open_meteo_current_temp']}℃")
    print(f"  双源平均实时温度: {record['average_current_temp']}℃")
    print(f"  香港天文台今日已录得最高温: {record['today_max_temp']}℃")


def fetch_hong_kong_realtime_with_retries(
    session: requests.Session,
    attempts: int = 3,
):
    for attempt in range(1, attempts + 1):
        observation = fetch_hong_kong_realtime(session=session)

        if (
            observation is not None
            and observation.current_temp is not None
            and observation.today_max_temp is not None
        ):
            return observation

        if attempt < attempts:
            delay_seconds = attempt * 2
            print(
                f"香港天文台实时数据获取失败或不完整，"
                f"第 {attempt}/{attempts} 次，"
                f"{delay_seconds} 秒后重试",
                file=sys.stderr,
            )
            time.sleep(delay_seconds)

    return None


def fetch_open_meteo_current_with_retries(
    session: requests.Session,
    attempts: int = 3,
) -> tuple[float, str] | None:
    for attempt in range(1, attempts + 1):
        result = fetch_open_meteo_current_temperature(session)
        if result is not None:
            return result

        if attempt < attempts:
            delay_seconds = attempt * 2
            print(
                f"Open-Meteo 当前温度获取失败，"
                f"第 {attempt}/{attempts} 次，"
                f"{delay_seconds} 秒后重试",
                file=sys.stderr,
            )
            time.sleep(delay_seconds)

    return None


def fetch_open_meteo_current_temperature(
    session: requests.Session,
) -> tuple[float, str] | None:
    params = {
        "latitude": HONG_KONG_LATITUDE,
        "longitude": HONG_KONG_LONGITUDE,
        "current": "temperature_2m",
        "timezone": "Asia/Hong_Kong",
    }

    try:
        response = session.get(
            OPEN_METEO_URL,
            params=params,
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()

        current = payload.get("current")
        if not isinstance(current, dict):
            print("Open-Meteo 响应缺少 current 对象")
            return None

        raw_temp = current.get("temperature_2m")
        if raw_temp is None:
            print("Open-Meteo 响应缺少 current.temperature_2m")
            return None

        observed_at = normalize_open_meteo_time(current.get("time"))
        return float(raw_temp), observed_at

    except (requests.RequestException, TypeError, ValueError) as exc:
        print(f"Open-Meteo 当前温度请求失败: {exc}")
        return None


def normalize_open_meteo_time(value: object) -> str:
    if not value:
        return ""

    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=HKO_TZ)

    return parsed.isoformat(timespec="seconds")


if __name__ == "__main__":
    main()
