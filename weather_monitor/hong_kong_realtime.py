from __future__ import annotations

import csv
import io
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
USER_AGENT = "weather-alpha-monitor/0.1"

STATION_ALIASES = {
    "香港天文台": {
        "香港天文台", "天文台", "香港天文台總部", "香港天文台总部",
    },
    "京士柏": {
        "京士柏",
    },
}

MAX_TEMP_COLUMN = "午夜至现时的最高气温（摄氏）"


@dataclass
class SettlementObservation:
    settlement_station: str
    current_temp: Optional[float]
    today_max_temp: Optional[float]
    observed_at: str
    max_temp_updated_at: str
    incomplete: bool


def fetch_hong_kong_realtime(
    session: Optional[requests.Session] = None,
) -> Optional[SettlementObservation]:
    """Fetch real-time Hong Kong Observatory settlement station data."""
    if session is None:
        session = _make_session()

    # Fetch rhrread
    rhrread_data = None
    try:
        r = session.get(RHRREAD_URL, timeout=25)
        r.raise_for_status()
        rhrread_data = r.json()
    except Exception as exc:
        print(f"rhrread 请求失败: {exc}")

    # Fetch max/min CSV
    csv_rows = None
    csv_update_time = ""
    try:
        r = session.get(MAX_MIN_CSV_URL, timeout=25)
        r.raise_for_status()
        text = r.content.decode("utf-8-sig")
        csv_rows, csv_update_time = _parse_maxmin_csv(text)
    except Exception as exc:
        print(f"Max/Min CSV 请求失败: {exc}")

    # Parse current temps
    current_temps = {}
    rhrread_time = ""
    if rhrread_data:
        current_temps, rhrread_time = _parse_rhrread_temps(rhrread_data)

    # Parse max temps from CSV
    max_temps = {}
    if csv_rows:
        max_temps = _build_max_temp_map(csv_rows)

    cur = current_temps.get("香港天文台")
    tmax, inc, max_time = max_temps.get("香港天文台", (None, True, ""))
    max_updated = max_time or csv_update_time

    return SettlementObservation(
        settlement_station="香港天文台",
        current_temp=cur,
        today_max_temp=tmax,
        observed_at=rhrread_time,
        max_temp_updated_at=max_updated,
        incomplete=(tmax is None) or inc,
    )


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def _parse_rhrread_temps(payload: dict) -> tuple[dict[str, float], str]:
    result: dict[str, float] = {}
    record_time = ""
    temp = payload.get("temperature", {})
    if isinstance(temp, dict):
        record_time = str(temp.get("recordTime", ""))
        for s in (temp.get("data") or []):
            name = str(s.get("place", s.get("name", ""))).strip()
            if not name:
                continue
            v = _safe_float(s.get("value", s.get("temp")))
            if v is not None:
                result[name] = v
    return result, record_time


def _parse_maxmin_csv(text: str) -> tuple[list[dict[str, str]], str]:
    lines = text.strip().split("\n")
    update_time = ""
    header_idx = 0
    for i, line in enumerate(lines):
        if any(kw in line for kw in ["自動氣象站", "最高溫度", "日期", "Automatic", "Temperature"]):
            header_idx = i
            break
    for i in range(header_idx):
        if lines[i].strip():
            update_time = lines[i].strip()
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    return list(reader), update_time


def _build_max_temp_map(rows: list[dict[str, str]]) -> dict[str, tuple[Optional[float], bool, str]]:
    result: dict[str, tuple[Optional[float], bool, str]] = {}
    for row in rows:
        raw = str(row.get("自动气象站") or row.get("自動氣象站") or "").strip()
        if not raw:
            continue
        canonical = _canonical_name(raw)
        if canonical not in STATION_ALIASES:
            continue
        dt_raw = str(row.get("日期时间", row.get("日期時間", ""))).strip()
        row_time = _parse_csv_dt(dt_raw)
        val = row.get(MAX_TEMP_COLUMN, "").strip()
        tmax, inc = _parse_temp_val(val) if val else (None, True)
        result[canonical] = (tmax, inc, row_time)
    return result


def _canonical_name(raw: str) -> str:
    n = raw.strip()
    for canonical, aliases in STATION_ALIASES.items():
        if n in aliases:
            return canonical
    return n


def _parse_csv_dt(value: str) -> str:
    if not value.strip():
        return ""
    try:
        dt = datetime.strptime(value.strip(), "%Y%m%d%H%M")
        dt = dt.replace(tzinfo=HKO_TZ)
        return dt.isoformat(timespec="seconds")
    except (ValueError, AttributeError):
        return ""


def _parse_temp_val(raw: str) -> tuple[Optional[float], bool]:
    raw = raw.strip()
    if not raw or raw in ("N/A", "-", "—", ""):
        return None, True
    incomplete = raw.endswith("*")
    if incomplete:
        raw = raw[:-1].strip()
    try:
        return float(raw), incomplete
    except (ValueError, TypeError):
        return None, True


def _safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
