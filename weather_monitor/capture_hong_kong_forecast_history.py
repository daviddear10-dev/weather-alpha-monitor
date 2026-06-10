from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from .hong_kong_forecast_parser import extract_hko_today_max_temp


HKO_TZ = ZoneInfo("Asia/Hong_Kong")
HKO_API_URL = (
    "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
)
OUTPUT_PATH = Path("docs/hong_kong_forecast_history.json")
USER_AGENT = "weather-alpha-monitor/0.1"


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    now_hk = datetime.now(HKO_TZ)
    captured_at = now_hk.isoformat(timespec="seconds")
    local_date = now_hk.date().isoformat()

    payload, error = fetch_flw_with_retries(session)

    if payload is None:
        record = {
            "captured_at": captured_at,
            "forecast_run_label": "manual",
            "city": "香港",
            "source": "香港天文台",
            "forecast_date": local_date,
            "min_temp": None,
            "max_temp": None,
            "update_time": "",
            "status": "fetch_failed",
            "status_text": "抓取失败",
            "forecast_period": "",
            "forecast_desc": "",
            "error": error or "未知错误",
        }
    else:
        forecast_desc = str(payload.get("forecastDesc") or "")
        forecast_high = extract_hko_today_max_temp(forecast_desc)

        if forecast_high is None:
            status = "no_explicit_value"
            status_text = "无明确数值"
        else:
            status = "ok"
            status_text = "正常"

        record = {
            "captured_at": captured_at,
            "forecast_run_label": "manual",
            "city": "香港",
            "source": "香港天文台",
            "forecast_date": local_date,
            "min_temp": None,
            "max_temp": forecast_high,
            "update_time": str(payload.get("updateTime") or ""),
            "status": status,
            "status_text": status_text,
            "forecast_period": str(
                payload.get("forecastPeriod") or ""
            ),
            "forecast_desc": forecast_desc,
            "error": "",
        }

    save_record(record)

    print("✓ 已记录香港天文台本批今日最高温预测")
    print(f"  captured_at: {record['captured_at']}")
    print(f"  forecast_date: {record['forecast_date']}")
    print(f"  status: {record['status_text']}")

    if record["max_temp"] is None:
        print("  max_temp: 无明确数值")
    else:
        print(f"  max_temp: {record['max_temp']}℃")

    if record["update_time"]:
        print(f"  update_time: {record['update_time']}")

    if record["error"]:
        print(f"  error: {record['error']}")


def fetch_flw_with_retries(
    session: requests.Session,
    attempts: int = 3,
) -> tuple[dict[str, Any] | None, str]:
    last_error = ""

    for attempt in range(1, attempts + 1):
        try:
            response = session.get(
                HKO_API_URL,
                params={"dataType": "flw", "lang": "sc"},
                timeout=25,
            )
            response.raise_for_status()
            payload = response.json()

            if not isinstance(payload, dict):
                raise ValueError("香港天文台返回内容不是 JSON 对象")

            return payload, ""

        except (
            requests.RequestException,
            ValueError,
            TypeError,
        ) as exc:
            last_error = str(exc)

            if attempt < attempts:
                delay_seconds = attempt * 2
                print(
                    f"香港天文台今日预测抓取失败，"
                    f"第 {attempt}/{attempts} 次，"
                    f"{delay_seconds} 秒后重试"
                )
                time.sleep(delay_seconds)

    return None, last_error


def save_record(
    record: dict[str, Any],
    output_path: Path = OUTPUT_PATH,
) -> None:
    rows = load_history(output_path)
    local_date = str(record["forecast_date"])

    # 只保留香港当地今天。
    rows = [
        row
        for row in rows
        if row.get("city") == "香港"
        and row.get("forecast_date") == local_date
    ]

    # 相同 captured_at 不重复。
    by_captured_at = {
        str(row.get("captured_at")): row
        for row in rows
        if row.get("captured_at")
    }
    by_captured_at[str(record["captured_at"])] = record

    output_rows = sorted(
        by_captured_at.values(),
        key=lambda row: str(row.get("captured_at", "")),
        reverse=True,
    )

    atomic_write_json(output_path, output_rows)


def load_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"{path} 读取失败或不是合法 JSON：{exc}"
        ) from exc

    if not isinstance(payload, list):
        raise RuntimeError(f"{path} 必须是 JSON 数组")

    return [
        item
        for item in payload
        if isinstance(item, dict)
    ]


def atomic_write_json(
    path: Path,
    payload: Any,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        suffix=".tmp",
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
            )
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


if __name__ == "__main__":
    main()
