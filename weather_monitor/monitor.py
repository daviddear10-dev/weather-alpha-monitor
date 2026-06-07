from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from tabulate import tabulate



OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
HKO_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
LOCAL_TZ = timezone(timedelta(hours=8))
DEFAULT_TIMEZONE = "Asia/Shanghai"
HKO_TIMEZONE = "Asia/Hong_Kong"
NWS_US_CITIES = {"纽约", "洛杉矶", "迈阿密"}


@dataclass(frozen=True)
class City:
    name: str
    latitude: float
    longitude: float
    timezone: str


DEFAULT_CITIES = [
    City("深圳", 22.5431, 114.0579, "Asia/Shanghai"),
    City("香港", 22.3193, 114.1694, "Asia/Hong_Kong"),
    City("北京", 39.9042, 116.4074, "Asia/Shanghai"),
]
DEFAULT_CITIES_PATH = Path(__file__).with_name("cities.json")


@dataclass(frozen=True)
class ForecastRecord:
    fetched_at: str
    forecast_run_label: str
    city: str
    source: str
    forecast_date: str
    temp_min: float | None
    temp_max: float
    data_update_time: str


@dataclass(frozen=True)
class ComparisonRecord:
    forecast_date: str
    city: str
    source_count: int
    temp_min_range: str
    temp_max_range: str
    temp_min_diff: str
    temp_max_diff: str
    confidence: str


def now_local() -> datetime:
    return datetime.now(LOCAL_TZ)


def tomorrow_date_for_timezone(timezone_name: str) -> str:
    tz = safe_zoneinfo(timezone_name)
    return (datetime.now(tz).date() + timedelta(days=1)).isoformat()



def today_date_for_timezone(timezone_name: str) -> str:
    tz = safe_zoneinfo(timezone_name)
    return datetime.now(tz).date().isoformat()


def tomorrow_date_for_city(city: City) -> str:
    return tomorrow_date_for_timezone(city.timezone)


def safe_timezone_name(timezone_name: str) -> str:
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return DEFAULT_TIMEZONE
    return timezone_name


def safe_zoneinfo(timezone_name: str) -> ZoneInfo:
    return ZoneInfo(safe_timezone_name(timezone_name))


def load_cities(config_path: Path = DEFAULT_CITIES_PATH) -> list[City]:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("cities config must be a list")
        cities = []
        for item in payload:
            if not isinstance(item, dict) or item.get("enabled") is not True:
                continue
            name = str(item["name"]).strip()
            latitude = float(item["latitude"])
            longitude = float(item["longitude"])
            timezone_name = safe_timezone_name(str(item.get("timezone", DEFAULT_TIMEZONE)))
            if not name:
                continue
            cities.append(City(name, latitude, longitude, timezone_name))
        if not cities:
            raise ValueError("cities config has no enabled cities")
        return cities
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return DEFAULT_CITIES


def get_forecast_run_label(current_time: Optional[datetime] = None) -> str:
    current = current_time or now_local()
    minutes = current.hour * 60 + current.minute
    if 17 * 60 <= minutes <= 19 * 60 + 30:
        return "evening_1800"
    if 20 * 60 <= minutes <= 21 * 60 + 30:
        return "evening_2030"
    if 22 * 60 + 30 <= minutes <= 23 * 60 + 59:
        return "night_2300"
    if 6 * 60 <= minutes <= 8 * 60:
        return "morning_0700"
    return "manual"



def fetch_open_meteo(city: City, fetched_at: str, forecast_run_label: str) -> list[ForecastRecord]:
    params = {
        "latitude": city.latitude,
        "longitude": city.longitude,
        "daily": "temperature_2m_max,temperature_2m_min",
        "timezone": city.timezone,
        "forecast_days": 2,
    }
    response = requests.get(OPEN_METEO_URL, params=params, timeout=20)
    response.raise_for_status()
    payload = response.json()

    daily = payload.get("daily", {})
    dates = daily.get("time", [])
    max_temps = daily.get("temperature_2m_max", [])
    min_temps = daily.get("temperature_2m_min", [])

    def _make_record(target_date: str) -> ForecastRecord:
        try:
            index = dates.index(target_date)
        except ValueError as exc:
            raise ValueError(
                f"Open-Meteo response for {city.name} has no forecast for {target_date}"
            ) from exc
        return ForecastRecord(
            fetched_at=fetched_at,
            forecast_run_label=forecast_run_label,
            city=city.name,
            source="Open-Meteo",
            forecast_date=target_date,
            temp_min=float(min_temps[index]),
            temp_max=float(max_temps[index]),
            data_update_time=payload.get("generationtime_ms") is not None and fetched_at or "",
        )

    tomorrow = tomorrow_date_for_city(city)
    records = [_make_record(tomorrow)]

    # For Hong Kong, also produce today's forecast
    if city.name == "香港":
        today = today_date_for_timezone(city.timezone)
        if today != tomorrow:
            try:
                records.append(_make_record(today))
            except ValueError:
                pass  # today may not be in the 2-day window if we're late UTC

    return records

def _fetch_hko_today_from_cache(fetched_at: str, forecast_run_label: str) -> ForecastRecord | None:
    """Read today's HKO forecast from cache if available and valid."""
    try:
        from .hong_kong_forecast_cache import load_forecast_cache  # noqa: E402
        cache_entry = load_forecast_cache()
    except Exception:
        return None

    if cache_entry is None:
        return None

    today_hk = today_date_for_timezone(HKO_TIMEZONE)
    if cache_entry.forecast_date != today_hk:
        return None

    return ForecastRecord(
        fetched_at=fetched_at,
        forecast_run_label=forecast_run_label,
        city="香港",
        source="香港天文台-今日预测",
        forecast_date=today_hk,
        temp_min=None,  # cache has no min temp
        temp_max=cache_entry.forecast_high,
        data_update_time=cache_entry.update_time or fetched_at,
    )



def fetch_hko(fetched_at: str, forecast_run_label: str) -> ForecastRecord:
    params = {"dataType": "fnd", "lang": "sc"}
    response = requests.get(HKO_URL, params=params, timeout=20)
    response.raise_for_status()
    payload = response.json()

    target_date = tomorrow_date_for_timezone(HKO_TIMEZONE)
    target_compact_date = target_date.replace("-", "")
    forecasts = payload.get("weatherForecast", [])
    forecast = next(
        (item for item in forecasts if str(item.get("forecastDate")) == target_compact_date),
        None,
    )
    if forecast is None:
        raise ValueError(f"香港天文台 response has no forecast for {target_date}")

    min_temp = extract_temperature_value(forecast, "forecastMintemp")
    max_temp = extract_temperature_value(forecast, "forecastMaxtemp")

    return ForecastRecord(
        fetched_at=fetched_at,
        forecast_run_label=forecast_run_label,
        city="香港",
        source="香港天文台",
        forecast_date=target_date,
        temp_min=min_temp,
        temp_max=max_temp,
        data_update_time=str(payload.get("updateTime", "")),
    )


def extract_temperature_value(forecast: dict[str, Any], key: str) -> float:
    value = forecast.get(key)
    if isinstance(value, dict):
        value = value.get("value")
    if value is None:
        raise ValueError(f"Missing {key} in 香港天文台 forecast")
    return float(value)


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table if not exists weather_forecasts (
                id integer primary key autoincrement,
                fetched_at text not null,
                city text not null,
                source text not null,
                forecast_date text not null,
                forecast_run_label text not null default 'manual',
                temp_min real not null,
                temp_max real not null,
                data_update_time text not null,
                created_at text not null default (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            create index if not exists idx_weather_forecasts_lookup
            on weather_forecasts (forecast_date, city, source)
            """
        )
        migrate_db(conn)


def migrate_db(conn: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in conn.execute("pragma table_info(weather_forecasts)").fetchall()
    }
    if "forecast_run_label" not in columns:
        conn.execute(
            """
            alter table weather_forecasts
            add column forecast_run_label text not null default 'manual'
            """
        )


def save_records(db_path: Path, records: list[ForecastRecord]) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            insert into weather_forecasts (
                fetched_at,
                forecast_run_label,
                city,
                source,
                forecast_date,
                temp_min,
                temp_max,
                data_update_time
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record.fetched_at,
                    record.forecast_run_label,
                    record.city,
                    record.source,
                    record.forecast_date,
                    record.temp_min,
                    record.temp_max,
                    record.data_update_time,
                )
                for record in records
            ],
        )


def load_recent_records(db_path: Path, limit: int = 20) -> list[ForecastRecord]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            select
                fetched_at,
                forecast_run_label,
                city,
                source,
                forecast_date,
                temp_min,
                temp_max,
                data_update_time
            from weather_forecasts
            order by fetched_at desc, id desc
            limit ?
            """,
            (limit,),
        ).fetchall()

    return [
        ForecastRecord(
            fetched_at=row[0],
            forecast_run_label=row[1],
            city=row[2],
            source=row[3],
            forecast_date=row[4],
            temp_min=float(row[5]) if row[5] is not None else None,
            temp_max=float(row[6]),
            data_update_time=row[7],
        )
        for row in rows
    ]


def _parse_captured_at_ts(captured_at: str) -> float:
    """Parse captured_at ISO string to a numeric timestamp for sorting.
    Handles timezone offsets like +08:00 or Z."""
    try:
        dt = datetime.fromisoformat(captured_at)
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0


def _record_key(record: dict) -> tuple:
    """Stable dedup key for a weather record dict."""
    return (
        record.get("city", ""),
        record.get("source", ""),
        record.get("forecast_date", ""),
        record.get("captured_at", ""),
        record.get("forecast_run_label", ""),
    )


def export_weather_data(
    new_records: list[ForecastRecord],
    output_path: Path,
    max_records: int = 100,
    db_path: Path | None = None,
) -> None:
    """Export weather data JSON, merging new records with existing history.

    - Reads existing docs/weather_data.json if present
    - Merges new_records in ForecastRecord form
    - Deduplicates on (city, source, forecast_date, captured_at, forecast_run_label)
    - Sorts by captured_at descending (real time, not string sort)
    - Keeps at most max_records entries
    - Filters to currently enabled cities only
    """
    enabled_city_names = {city.name for city in load_cities()}

    # Load existing JSON history
    existing: list[dict] = []
    if output_path.is_file():
        try:
            raw = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                existing = [item for item in raw if isinstance(item, dict)]
        except (json.JSONDecodeError, OSError):
            pass  # corrupt or empty → start fresh

    # Convert new ForecastRecords to dict form
    new_dicts = [
        {
            "captured_at": r.fetched_at,
            "forecast_run_label": r.forecast_run_label,
            "city": r.city,
            "source": r.source,
            "forecast_date": r.forecast_date,
            "min_temp": r.temp_min,
            "max_temp": r.temp_max,
            "update_time": r.data_update_time,
        }
        for r in new_records
    ]

    # Merge + dedup (newer records win on key collision)
    merged: dict[tuple, dict] = {}
    for item in existing + new_dicts:
        if item.get("city") not in enabled_city_names:
            continue
        key = _record_key(item)
        merged[key] = item  # last-write-wins (new_dicts come after existing)

    # Sort by real captured_at descending
    sorted_records = sorted(
        merged.values(),
        key=lambda r: _parse_captured_at_ts(r.get("captured_at", "")),
        reverse=True,
    )

    # Truncate
    payload = sorted_records[:max_records]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_comparison_records(db_path: Path) -> list[ComparisonRecord]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            with latest_source_records as (
                select
                    forecast_date,
                    city,
                    source,
                    temp_min,
                    temp_max,
                    row_number() over (
                        partition by forecast_date, city, source
                        order by fetched_at desc, id desc
                    ) as rn
                from weather_forecasts
            )
            select
                forecast_date,
                city,
                count(*) as source_count,
                min(temp_min) as min_temp_min,
                max(temp_min) as max_temp_min,
                min(temp_max) as min_temp_max,
                max(temp_max) as max_temp_max
            from latest_source_records
            where rn = 1
            group by forecast_date, city
            order by forecast_date desc, city asc
            """
        ).fetchall()

    return [
        build_comparison_record(
            forecast_date=row[0],
            city=row[1],
            source_count=int(row[2]),
            min_temp_min=float(row[3]),
            max_temp_min=float(row[4]),
            min_temp_max=float(row[5]),
            max_temp_max=float(row[6]),
        )
        for row in rows
    ]


def build_comparison_record(
    *,
    forecast_date: str,
    city: str,
    source_count: int,
    min_temp_min: float,
    max_temp_min: float,
    min_temp_max: float,
    max_temp_max: float,
) -> ComparisonRecord:
    temp_min_diff = max_temp_min - min_temp_min
    temp_max_diff = max_temp_max - min_temp_max
    return ComparisonRecord(
        forecast_date=forecast_date,
        city=city,
        source_count=source_count,
        temp_min_range=format_temperature_range(min_temp_min, max_temp_min),
        temp_max_range=format_temperature_range(min_temp_max, max_temp_max),
        temp_min_diff=format_temperature(temp_min_diff),
        temp_max_diff=format_temperature(temp_max_diff),
        confidence=classify_confidence(source_count, temp_min_diff, temp_max_diff),
    )


def classify_confidence(source_count: int, temp_min_diff: float, temp_max_diff: float) -> str:
    if source_count < 2:
        return "数据源不足"
    max_diff = max(temp_min_diff, temp_max_diff)
    if max_diff <= 1:
        return "可信度高"
    if max_diff >= 3:
        return "分歧大"
    return "中等"


def format_temperature_range(low: float | None, high: float) -> str:
    if low is None:
        return format_temperature(high)
    if low == high:
        return format_temperature(low)
    return f"{format_number(low)}-{format_number(high)}℃"


def format_temperature(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{format_number(value)}℃"


def format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}"


def print_table(records: list[ForecastRecord], *, tomorrow_labels: bool = True) -> None:
    rows = [
        [
            record.fetched_at,
            record.forecast_run_label,
            record.city,
            record.source,
            record.forecast_date,
            f"{record.temp_min:.1f}" if record.temp_min is not None else "N/A",
            f"{record.temp_max:.1f}",
            record.data_update_time,
        ]
        for record in records
    ]
    headers = [
        "抓取时间",
        "批次",
        "城市",
        "数据源",
        "预报日期",
        "目标日期最低温" if tomorrow_labels else "最低温",
        "目标日期最高温" if tomorrow_labels else "最高温",
        "数据更新时间" if tomorrow_labels else "更新时间",
    ]
    print(tabulate(rows, headers=headers, tablefmt="github"))


def print_comparison_table(records: list[ComparisonRecord]) -> None:
    rows = [
        [
            record.forecast_date,
            record.city,
            record.source_count,
            record.temp_min_range,
            record.temp_max_range,
            record.temp_min_diff,
            record.temp_max_diff,
            record.confidence,
        ]
        for record in records
    ]
    headers = [
        "预报日期",
        "城市",
        "数据源数量",
        "最低温范围",
        "最高温范围",
        "最低温差值",
        "最高温差值",
        "可信度",
    ]
    print(tabulate(rows, headers=headers, tablefmt="github"))


def collect_forecasts() -> list[ForecastRecord]:
    current_time = now_local()
    fetched_at = current_time.isoformat(timespec="seconds")
    forecast_run_label = get_forecast_run_label(current_time)
    cities = load_cities()
    records = []
    for city in cities:
        om_records = fetch_open_meteo(city, fetched_at, forecast_run_label)
        records.extend(om_records)
    if any(city.name == "香港" for city in cities):
        records.append(fetch_hko(fetched_at, forecast_run_label))
        hko_today = _fetch_hko_today_from_cache(fetched_at, forecast_run_label)
        if hko_today is not None:
            records.append(hko_today)
        else:
            print("香港天文台今日预测缓存不可用，跳过")
    for city in cities:
        if city.name in NWS_US_CITIES:
            try:
                from .nws_official import fetch_nws_forecast  # noqa: E402
                nws_record = fetch_nws_forecast(
                    city_name=city.name,
                    latitude=city.latitude,
                    longitude=city.longitude,
                    timezone=city.timezone,
                )
                if nws_record is not None:
                    records.append(nws_record)
                else:
                    print(f"NOAA/NWS 获取失败 ({city.name})，仅使用 Open-Meteo 数据")
            except Exception as exc:
                print(f"NOAA/NWS 异常 ({city.name}): {exc}，仅使用 Open-Meteo 数据")
    if any(city.name == "新加坡" for city in cities):
        try:
            from .singapore_nea import fetch_singapore_nea_forecast  # noqa: E402
            nea_record = fetch_singapore_nea_forecast()
            if nea_record is not None:
                records.append(nea_record)
            else:
                print("NEA/MSS 获取失败（新加坡），仅使用 Open-Meteo 数据")
        except Exception as exc:
            print(f"NEA/MSS 异常（新加坡）: {exc}，仅使用 Open-Meteo 数据")
    return records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch tomorrow temperature forecasts.")
    parser.add_argument(
        "--db",
        default="weather_forecasts.sqlite",
        help="SQLite database path. Default: weather_forecasts.sqlite",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Show the latest 20 saved records without fetching new data.",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Compare saved forecasts by forecast date and city without fetching new data.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve()
    export_path = Path("docs/weather_data.json").resolve()
    init_db(db_path)
    if args.show:
        records = load_recent_records(db_path)
        print_table(records, tomorrow_labels=False)
        return
    if args.compare:
        records = load_comparison_records(db_path)
        print_comparison_table(records)
        return

    records = collect_forecasts()
    save_records(db_path, records)
    export_weather_data(records, export_path)
    print_table(records)
    print(f"\nSaved {len(records)} rows to {db_path}")
    print(f"Exported latest weather data to {export_path}")
