from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

from .monitor import ForecastRecord, get_forecast_run_label, tomorrow_date_for_timezone


BASE_URL = "https://opendata.sz.gov.cn"
RES_ID = "29200/00900269"
DETAILS_URL = f"{BASE_URL}/data/api/toApiDetails/29200_00900269"
API_DOCUMENT_URL = f"{BASE_URL}/data/api/getApiDocument"
PREVIEW_FIELDS_URL = f"{BASE_URL}/data/api/getPreviewApiItem"
PREVIEW_ROWS_URL = f"{BASE_URL}/data/api/getPreviewApi"
SHENZHEN_TZ = "Asia/Shanghai"


def main() -> None:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "weather-alpha-monitor/0.1",
            "Referer": DETAILS_URL,
        }
    )

    print("深圳官方天气源独立测试")
    print(f"资源页面: {DETAILS_URL}")

    api_context, source_table_name = discover_api(session)
    rows = []
    if api_context:
        rows = try_official_api(session, api_context)
    if not rows and source_table_name:
        rows = try_preview_api(session, source_table_name)

    if not rows:
        print("未获取到可解析的预报行。")
        return

    print_key_fields(rows)
    record = parse_forecast_record(rows)
    if record is None:
        print("解析失败：没有找到深圳当地明天日期对应的温度数据。")
        return

    print("解析成功，ForecastRecord:")
    print(json.dumps(asdict(record), ensure_ascii=False, indent=2))


def discover_api(session: requests.Session) -> tuple[Optional[str], Optional[str]]:
    try:
        response = session.post(API_DOCUMENT_URL, data={"resId": RES_ID}, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        print(f"接口文档请求失败: {exc}")
        return None, None

    print("接口文档关键字段:")
    print(json.dumps(summarize_api_document(payload), ensure_ascii=False, indent=2))

    api_context = None
    source_table_name = None
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            if item.get("api_context"):
                api_context = item.get("api_context")
            api_info = item.get("api")
            if isinstance(api_info, dict):
                source_table_name = api_info.get("sourceTableName")
    return api_context, source_table_name


def summarize_api_document(payload: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {"api_context": None, "source_table_name": None, "fields": []}
    if not isinstance(payload, list):
        summary["raw_type"] = type(payload).__name__
        return summary
    for item in payload:
        if not isinstance(item, dict):
            continue
        if item.get("api_context"):
            summary["api_context"] = item.get("api_context")
            summary["res_title"] = item.get("res_title")
        api_info = item.get("api")
        if isinstance(api_info, dict):
            summary["source_table_name"] = api_info.get("sourceTableName")
            summary["api_title"] = api_info.get("resTitle")
            summary["data_update_time"] = api_info.get("dataUpdateTime")
            summary["open_level_name"] = api_info.get("openLevelName")
        if item.get("columnName"):
            summary["fields"].append(
                {
                    "columnName": item.get("columnName"),
                    "columnComment": item.get("columnComment"),
                }
            )
    return summary


def try_official_api(session: requests.Session, api_context: str) -> list[dict[str, Any]]:
    app_key = os.environ.get("SZ_OPEN_DATA_APP_KEY", "")
    if not app_key:
        print("未设置 SZ_OPEN_DATA_APP_KEY，跳过正式 API，仅尝试预览接口。")
        return []

    url = f"{BASE_URL}/{api_context}"
    params = {"page": 1, "rows": 200, "appKey": app_key}
    try:
        response = session.get(url, params=params, timeout=25)
        payload = response.json()
    except Exception as exc:
        print(f"正式 API 请求失败: {exc}")
        return []

    print("正式 API 返回关键字段:")
    print(json.dumps(summarize_payload(payload), ensure_ascii=False, indent=2))

    rows = extract_rows(payload)
    if rows:
        print(f"正式 API 获取到 {len(rows)} 行。")
    else:
        print("正式 API 未返回可用行；如果看到 errorCode=10001，说明需要订阅后的 appKey。")
    return rows


def try_preview_api(session: requests.Session, source_table_name: str) -> list[dict[str, Any]]:
    print("尝试网页预览接口，仅用于观察字段结构。")
    try:
        fields_response = session.post(
            PREVIEW_FIELDS_URL,
            data={"tableName": source_table_name},
            timeout=20,
        )
        fields = fields_response.json()
        print("预览字段:")
        print(json.dumps(fields[:8] if isinstance(fields, list) else fields, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"预览字段请求失败: {exc}")

    try:
        rows_response = session.get(
            PREVIEW_ROWS_URL,
            params={"page": 1, "rows": 200, "tableName": source_table_name},
            timeout=25,
        )
        payload = rows_response.json()
    except Exception as exc:
        print(f"预览数据请求失败: {exc}")
        return []

    rows = extract_rows(payload)
    print("预览数据关键字段:")
    print(json.dumps(summarize_payload(payload), ensure_ascii=False, indent=2))
    if rows:
        print(f"预览接口获取到 {len(rows)} 行。注意：预览接口可能不是最新业务数据。")
    return rows


def summarize_payload(payload: Any) -> dict[str, Any]:
    rows = extract_rows(payload)
    summary = {
        "type": type(payload).__name__,
        "row_count": len(rows),
    }
    if isinstance(payload, dict):
        for key in ["errorCode", "message", "total", "page", "rows"]:
            if key in payload:
                summary[key] = payload.get(key)
    if rows:
        summary["first_row_keys"] = sorted(rows[0].keys())
        summary["first_row_sample"] = rows[0]
    return summary


def extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ["data", "rows", "result", "records"]:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested_rows = extract_rows(value)
            if nested_rows:
                return nested_rows
    return []


def print_key_fields(rows: list[dict[str, Any]]) -> None:
    print("原始 JSON 关键字段样例:")
    keys = [
        "FORECASTTIME",
        "DDATETIME",
        "WRITETIME",
        "CRTTIME",
        "UPDATETIME",
        "AREANAME",
        "TEMPERATURE",
        "QPFTEMP",
        "WEATHERSTATUS",
        "QPFWEATHERSTATUS",
    ]
    sample = []
    for row in rows[:5]:
        sample.append({key: row.get(key) for key in keys if key in row})
    print(json.dumps(sample, ensure_ascii=False, indent=2))


def parse_forecast_record(rows: list[dict[str, Any]]) -> Optional[ForecastRecord]:
    target_date = tomorrow_date_for_timezone(SHENZHEN_TZ)
    target_rows = [
        row
        for row in rows
        if parse_date_prefix(row.get("FORECASTTIME")) == target_date
    ]
    temps = [
        temp
        for row in target_rows
        for temp in [parse_float(row.get("QPFTEMP", row.get("TEMPERATURE")))]
        if temp is not None
    ]
    if not temps:
        return None

    update_time = latest_update_time(target_rows) or ""
    fetched_at = datetime.now(ZoneInfo(SHENZHEN_TZ)).isoformat(timespec="seconds")
    return ForecastRecord(
        fetched_at=fetched_at,
        forecast_run_label=get_forecast_run_label(),
        city="深圳",
        source="深圳气象局",
        forecast_date=target_date,
        temp_min=min(temps),
        temp_max=max(temps),
        data_update_time=update_time,
    )


def parse_date_prefix(value: Any) -> Optional[str]:
    if not value:
        return None
    text = str(value)
    if len(text) >= 10:
        return text[:10]
    return None


def latest_update_time(rows: list[dict[str, Any]]) -> Optional[str]:
    candidates = []
    for row in rows:
        for key in ["WRITETIME", "CRTTIME", "DDATETIME"]:
            if row.get(key):
                candidates.append(str(row[key]))
        if row.get("UPDATETIME"):
            candidates.append(format_epoch_millis(row["UPDATETIME"]))
    return max(candidates) if candidates else None


def format_epoch_millis(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return datetime.fromtimestamp(number / 1000, ZoneInfo(SHENZHEN_TZ)).isoformat(timespec="seconds")


def parse_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
