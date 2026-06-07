from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

RHRREAD_URL = (
    "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
    "?dataType=rhrread&lang=sc"
)
MAX_MIN_CSV_URL = (
    "https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather/"
    "latest_since_midnight_maxmin_sc.csv"
)
HKO_TZ = ZoneInfo("Asia/Hong_Kong")
USER_AGENT = "weather-alpha-monitor/0.1 (test-hk-realtime)"

STATION_ALIASES = {
    "香港天文台": {
        "香港天文台",
        "天文台",
        "香港天文台總部",
        "香港天文台总部",
    },
    "京士柏": {
        "京士柏",
    },
}

MAX_TEMP_COLUMN = "午夜至现时的最高气温（摄氏）"
RESOLUTION_SOURCE = "HKO Daily Extract - Absolute Daily Max (deg. C)"


@dataclass
class StationObservation:
    station: str
    current_temp: Optional[float]
    today_max_temp: Optional[float]
    observed_at: str
    max_temp_updated_at: str
    incomplete: bool
    source: str


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    print("香港实时温度与午夜至现时最高温 — Polymarket 结算参考")
    print(f"rhrread API: {RHRREAD_URL}")
    print(f"Max/Min CSV: {MAX_MIN_CSV_URL}")
    print()

    # Fetch rhrread JSON
    rhrread_data = None
    try:
        response = session.get(RHRREAD_URL, timeout=25)
        response.raise_for_status()
        rhrread_data = response.json()
        print("rhrread API 获取成功")
    except Exception as exc:
        print(f"rhrread API 请求失败: {exc}")

    # Fetch max/min CSV
    csv_rows = None
    csv_update_time = ""
    try:
        response = session.get(MAX_MIN_CSV_URL, timeout=25)
        response.raise_for_status()
        text = response.content.decode("utf-8-sig")
        csv_rows, csv_update_time = parse_maxmin_csv(text)
        print(f"CSV 获取成功, {len(csv_rows)} 行, 更新时间: {csv_update_time}")
    except Exception as exc:
        print(f"Max/Min CSV 请求失败: {exc}")

    print()

    # Parse current temps from rhrread
    current_temps = {}
    rhrread_time = ""
    if rhrread_data:
        current_temps, rhrread_time = parse_rhrread_temps(rhrread_data)

    # Parse max temps from CSV
    max_temps = {}
    if csv_rows:
        max_temps = build_max_temp_map(csv_rows)

    # ==== Primary: Settlement station (香港天文台) ====
    cur_hko = current_temps.get("香港天文台")
    tmax_hko, inc_hko, max_time_hko = max_temps.get(
        "香港天文台", (None, True, "")
    )
    max_updated_at_hko = max_time_hko or csv_update_time

    print("=" * 50)
    print("结算站（Polymarket 参考）")
    print("=" * 50)
    print(json.dumps({
        "settlement_station": "香港天文台",
        "current_temp": cur_hko,
        "today_max_temp": tmax_hko,
        "observed_at": rhrread_time,
        "max_temp_updated_at": max_updated_at_hko,
        "resolution_source": RESOLUTION_SOURCE,
        "incomplete": (tmax_hko is None) or inc_hko,
    }, ensure_ascii=False, indent=2))
    print()

    if tmax_hko is not None:
        bucket = temperature_bucket(tmax_hko)
        print(f"当前已实现结算档位：{bucket}")
    else:
        print("当前已实现结算档位：（暂无数据）")
    print()

    # ==== Reference: 京士柏 cross-check only ====
    cur_ksp = current_temps.get("京士柏")
    tmax_ksp, inc_ksp, max_time_ksp = max_temps.get(
        "京士柏", (None, True, "")
    )

    print("=" * 50)
    print("备用交叉验证站（非结算站）")
    print("=" * 50)
    print(json.dumps({
        "reference_station": "京士柏",
        "current_temp": cur_ksp,
        "today_max_temp": tmax_ksp,
        "role": "备用交叉验证",
    }, ensure_ascii=False, indent=2))
    print()

    print("注意：当前已实现最高温只代表截至当前，不代表最终全天最高温。")
    print(f"结算来源：{RESOLUTION_SOURCE}")
    print()


def temperature_bucket(value: float) -> str:
    """Map temperature to nearest integer bucket for settlement reference.

    31.5–32.4 -> "32°C"
    32.5–33.4 -> "33°C"
    <= 24.4 -> "24°C or below"
    >= 33.5 -> "34°C or higher"
    """
    if value <= 24.4:
        return "24°C or below"
    if value >= 33.5:
        return "34°C or higher"
    center = round(value)
    return f"{center}°C"


def parse_rhrread_temps(payload: dict[str, Any]) -> tuple[dict[str, float], str]:
    """Extract current temperature per station from rhrread JSON."""
    result: dict[str, float] = {}
    record_time = ""

    temperature = payload.get("temperature", {})
    if isinstance(temperature, dict):
        record_time = str(temperature.get("recordTime", ""))
        stations = temperature.get("data", [])
        if isinstance(stations, list):
            for s in stations:
                name = str(s.get("place", s.get("name", ""))).strip()
                if not name:
                    continue
                temp = safe_float(s.get("value", s.get("temp")))
                if temp is not None:
                    result[name] = temp

    print(f"rhrread 观测时间: {record_time}, 站点数: {len(result)}")
    return result, record_time


def parse_maxmin_csv(text: str) -> tuple[list[dict[str, str]], str]:
    """Parse the max/min since midnight CSV and return rows + update time."""
    lines = text.strip().split("\n")
    update_time = ""

    header_idx = 0
    for i, line in enumerate(lines):
        if any(kw in line for kw in ["自動氣象站", "最高溫度", "日期", "時間", "Automatic", "Temperature"]):
            header_idx = i
            break

    for i in range(header_idx):
        if lines[i].strip():
            update_time = lines[i].strip()

    data_lines = lines[header_idx:]
    reader = csv.DictReader(io.StringIO("\n".join(data_lines)))
    fieldnames = reader.fieldnames or []
    print(f"CSV 表头: {fieldnames}")
    rows = list(reader)

    if rows:
        print(f"CSV 前 3 行:")
        for row in rows[:3]:
            print(f"  {row}")

    return rows, update_time


def build_max_temp_map(csv_rows: list[dict[str, str]]) -> dict[str, tuple[Optional[float], bool, str]]:
    """Build station -> (max_temp, incomplete, update_time) from CSV rows."""
    result: dict[str, tuple[Optional[float], bool, str]] = {}

    if not csv_rows:
        return result

    for row in csv_rows:
        station_name_raw = str(
            row.get("自动气象站")
            or row.get("自動氣象站")
            or ""
        ).strip()
        if not station_name_raw:
            continue

        canonical = canonical_station_name(station_name_raw)

        # Only track stations we care about
        if canonical not in STATION_ALIASES:
            continue

        datetime_raw = str(row.get("日期时间", row.get("日期時間", ""))).strip()
        row_max_time = parse_csv_datetime(datetime_raw)

        max_temp = None
        incomplete = False
        val = row.get(MAX_TEMP_COLUMN, "").strip()
        if val:
            max_temp, incomplete = parse_temp_value(val)

        result[canonical] = (max_temp, incomplete, row_max_time)

    return result


def canonical_station_name(raw_name: str) -> str:
    """Map a raw station name to its canonical form via exact alias match."""
    normalized = str(raw_name or "").strip()
    for canonical, aliases in STATION_ALIASES.items():
        if normalized in aliases:
            return canonical
    return normalized


def parse_csv_datetime(value: str) -> str:
    """Parse CSV datetime in YYYYMMDDHHMM format to ISO string in HKT."""
    if not value or not value.strip():
        return ""
    try:
        dt = datetime.strptime(value.strip(), "%Y%m%d%H%M")
        dt = dt.replace(tzinfo=HKO_TZ)
        return dt.isoformat(timespec="seconds")
    except (ValueError, AttributeError):
        return ""


def parse_temp_value(raw: str) -> tuple[Optional[float], bool]:
    """Parse a temperature value from CSV cell."""
    raw = raw.strip()
    if not raw or raw in ("N/A", "-", "—", ""):
        return None, True

    incomplete = False
    if raw.endswith("*"):
        incomplete = True
        raw = raw[:-1].strip()

    try:
        return float(raw), incomplete
    except (ValueError, TypeError):
        return None, True


def safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
