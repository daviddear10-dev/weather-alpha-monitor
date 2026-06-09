from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .hong_kong_realtime import fetch_hong_kong_realtime
from .hong_kong_probability import (
    HongKongProbabilityInput,
    TemperatureBucket,
    bucket_for_temp,
    build_temperature_buckets,
    compute_probabilities,
)
from .hong_kong_forecast_cache import load_forecast_cache
from .hong_kong_forecast_parser import extract_hko_today_max_temp

HKO_TZ = ZoneInfo("Asia/Hong_Kong")
HKO_API_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
JSON_OUTPUT = "docs/hong_kong_live_probability.json"


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "weather-alpha-monitor/0.1"})

    now_hk = datetime.now(HKO_TZ)
    today_str = now_hk.strftime("%Y-%m-%d")
    local_hour = now_hk.hour + now_hk.minute / 60.0

    print("香港实时最高温概率采集")
    print(f"香港当地时间: {now_hk.isoformat(timespec='seconds')}")
    print()

    market_bucket_source, market_buckets = _load_market_buckets(today_str)
    print(f"市场档位来源: {market_bucket_source}")
    print("市场档位: " + ", ".join(bucket.bucket for bucket in market_buckets))
    print()

    # ── Step 1: real-time observation ──
    obs = fetch_hong_kong_realtime(session=session)
    if obs is None or obs.current_temp is None:
        print("无法获取实时观测数据", file=sys.stderr)
        sys.exit(1)

    achieved_max = obs.today_max_temp or obs.current_temp

    # ── Step 2: forecast highs for TODAY from multiple sources ──
    forecast_sources: list[dict] = []
    forecast_highs: list[float] = []

    # 2a: Open-Meteo today
    om_high = _fetch_open_meteo_today_max(session)
    if om_high is not None:
        print(f"Open-Meteo 今日({today_str})最高温: {om_high}℃")
        forecast_highs.append(om_high)
        forecast_sources.append({"source": "Open-Meteo", "forecast_high": om_high})
    else:
        print("Open-Meteo 今日最高温: 获取失败")

    # 2b: HKO flw today (with cache fallback)
    hko_source, hko_high, hko_meta = _fetch_hko_today_max_with_cache(session)
    if hko_high is not None:
        print(f"{hko_source} 今日({today_str})最高温: {hko_high}℃")
        forecast_highs.append(hko_high)
        forecast_sources.append({
            "source": hko_source,
            "forecast_high": hko_high,
            **hko_meta,
        })
    else:
        print("香港天文台今日最高温: 获取失败（包括缓存）")

    if len(forecast_highs) < 2:
        print(f"预测源不足（当前 {len(forecast_highs)} 个），不生成概率分布，不写入 JSON", file=sys.stderr)
        print(f"可用来源: {[s['source'] for s in forecast_sources]}")
        sys.exit(1)

    # ── Step 3: run probability model ──
    inp = HongKongProbabilityInput(
        observed_at=obs.observed_at or now_hk.isoformat(timespec="seconds"),
        current_temp=obs.current_temp,
        achieved_max_temp=achieved_max,
        forecast_highs=forecast_highs,
        local_hour=local_hour,
        settlement_station="香港天文台",
    )

    result = compute_probabilities(inp, buckets=market_buckets)

    # Validate probability sum
    prob_sum = sum(result.probabilities.values())
    if abs(prob_sum - 1.0) > 1e-6:
        print(f"概率总和偏差过大: {prob_sum:.10f}，不写入 JSON", file=sys.stderr)
        sys.exit(1)

    # ── Step 4: build JSON payload ──
    probabilities_list = _build_probability_rows(result.probabilities, result.buckets)
    output_prob_sum = round(sum(row["probability"] for row in probabilities_list), 6)

    payload = {
        "city": "香港",
        "model_version": result.model_version,
        "captured_at": now_hk.isoformat(timespec="seconds"),
        "local_date": today_str,
        "local_hour": round(local_hour, 2),
        "settlement_station": obs.settlement_station,
        "current_temp": obs.current_temp,
        "today_max_temp": achieved_max,
        "achieved_bucket": bucket_for_temp(achieved_max, result.buckets),
        "observed_at": obs.observed_at,
        "max_temp_updated_at": obs.max_temp_updated_at,
        "forecast_sources": forecast_sources,
        "model_parameters": {
            "forecast_mean": round(result.forecast_mean, 2),
            "source_spread": round(result.source_spread, 2),
            "model_center": round(result.model_center, 2),
            "sigma": round(result.sigma, 4),
            "no_new_high_probability": round(result.no_new_high_probability, 6),
            "remaining_upside_probability": round(result.remaining_upside_probability, 6),
        },
        "probabilities": probabilities_list,
        "probability_sum": output_prob_sum,
        "warnings": [result.warning],
    }

    # ── Step 5: atomic write ──
    output_path = Path(JSON_OUTPUT)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(output_path.parent), suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(output_path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    print(f"✓ 已导出到 {JSON_OUTPUT}")
    print(f"  model_center: {result.model_center:.2f}℃")
    print(f"  no_new_high_probability: {result.no_new_high_probability:.4f}")
    print(f"  probability_sum: {output_prob_sum:.6f}")


def _load_market_buckets(today_str: str) -> tuple[str, list[TemperatureBucket]]:
    for path_text in ["docs/markets_draft.json", "docs/markets.json"]:
        path = Path(path_text)
        rows = _load_market_rows(path, today_str)
        if not rows:
            continue
        try:
            return path_text, build_temperature_buckets(rows)
        except ValueError as exc:
            print(f"{path_text} 市场档位无效：{exc}", file=sys.stderr)
            sys.exit(1)

    print("未找到香港今日 Polymarket 最高温市场档位，不写入 JSON", file=sys.stderr)
    sys.exit(1)


def _load_market_rows(path: Path, today_str: str) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"{path} 读取失败：{exc}", file=sys.stderr)
        return []
    if not isinstance(data, list):
        print(f"{path} 不是 JSON 数组", file=sys.stderr)
        return []
    return [
        item
        for item in data
        if isinstance(item, dict)
        and item.get("city") == "香港"
        and item.get("forecast_date") == today_str
        and item.get("metric") == "max_temp"
    ]


def _build_probability_rows(
    probabilities: dict[str, float],
    buckets: list[TemperatureBucket],
) -> list[dict]:
    rows = []
    for bucket in buckets:
        rows.append({
            "bucket": bucket.bucket,
            "condition": bucket.condition,
            "threshold": bucket.threshold,
            "probability": round(probabilities.get(bucket.bucket, 0.0), 6),
        })

    delta = round(1.0 - sum(row["probability"] for row in rows), 6)
    if rows and delta:
        target = max(rows, key=lambda row: row["probability"])
        target["probability"] = round(target["probability"] + delta, 6)
    return rows


def _fetch_open_meteo_today_max(session: requests.Session) -> float | None:
    """Get Open-Meteo forecast max temp for TODAY in Hong Kong."""
    params = {
        "latitude": 22.3193,
        "longitude": 114.1694,
        "daily": "temperature_2m_max",
        "timezone": "Asia/Hong_Kong",
        "forecast_days": 2,
    }
    try:
        r = session.get(OPEN_METEO_URL, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        max_temps = daily.get("temperature_2m_max", [])
        today = datetime.now(HKO_TZ).strftime("%Y-%m-%d")
        if today not in dates:
            print(f"  Open-Meteo 返回日期 {dates} 不含今天 {today}")
            return None
        idx = dates.index(today)
        return float(max_temps[idx])
    except Exception as exc:
        print(f"  Open-Meteo 错误: {exc}")
        return None


def _fetch_hko_today_max_with_cache(
    session: requests.Session,
) -> tuple[str, float | None, dict]:
    """Get HKO today's max temp from flw, falling back to cache.

    Returns (source_name, max_temp_or_None, meta_dict).
    """
    source = "香港天文台 Local Weather Forecast"
    today_str = datetime.now(HKO_TZ).strftime("%Y-%m-%d")

    # Try live flw first
    flw_data = _fetch_flw_raw(session)
    if flw_data is not None:
        forecast_period = flw_data.get("forecastPeriod", "")
        update_time = flw_data.get("updateTime", "")
        forecast_desc = flw_data.get("forecastDesc", "")
        print(f"  flw forecastPeriod: {forecast_period}")
        print(f"  flw updateTime:     {update_time}")
        print(f"  flw forecastDesc:   {forecast_desc[:200]}")

        today_max = extract_hko_today_max_temp(forecast_desc)
        if today_max is not None:
            return source, today_max, {
                "from_cache": False,
                "hko_update_time": update_time,
            }

        print("  flw 未找到明确的今日最高温数值")

    # Fall back to cache
    cached = load_forecast_cache()
    if cached is not None:
        print(f"  当前 flw 已切换，使用今日最近一次 HKO 缓存快照")
        print(f"  快照 captured_at:   {cached.captured_at}")
        print(f"  快照 forecast_high: {cached.forecast_high}℃")
        return source, cached.forecast_high, {
            "from_cache": True,
            "cache_captured_at": cached.captured_at,
            "hko_update_time": cached.update_time,
        }

    return source, None, {}


def _fetch_flw_raw(session: requests.Session) -> dict | None:
    params = {"dataType": "flw", "lang": "sc"}
    try:
        r = session.get(HKO_API_URL, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        print(f"  HKO flw 错误: {exc}")
        return None


if __name__ == "__main__":
    main()
