from __future__ import annotations

import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .hong_kong_realtime import fetch_hong_kong_realtime
from .hong_kong_probability import (
    BUCKETS,
    HongKongProbabilityInput,
    compute_probabilities,
    format_probability_table,
    bucket_for_temp,
)
from .hong_kong_forecast_cache import (
    ForecastCacheEntry,
    save_forecast_cache,
    load_forecast_cache,
)

HKO_TZ = ZoneInfo("Asia/Hong_Kong")
HKO_API_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "weather-alpha-monitor/0.1"})

    now_hk = datetime.now(HKO_TZ)
    today_str = now_hk.strftime("%Y-%m-%d")
    local_hour = now_hk.hour + now_hk.minute / 60.0

    print("香港实时最高温概率 — 实时测试")
    print(f"香港当地时间: {now_hk.isoformat(timespec='seconds')}")
    print()

    # Step 1: real-time data
    obs = fetch_hong_kong_realtime(session=session)
    if obs is None or obs.current_temp is None:
        print("无法获取实时观测数据")
        return

    # Step 2: forecast highs for TODAY from multiple sources
    forecast_highs: list[float] = []
    source_names: list[str] = []

    # 2a: Open-Meteo today
    om_high = fetch_open_meteo_today_max(session)
    if om_high is not None:
        print(f"Open-Meteo 今日({today_str})最高温: {om_high}℃")
        forecast_highs.append(om_high)
        source_names.append("Open-Meteo")
    else:
        print("Open-Meteo 今日最高温: 获取失败")

    # 2b: HKO Local Weather Forecast (flw) for today — with cache fallback
    hko_source, hko_high = fetch_hko_flw_today_max_with_cache(session)
    if hko_high is not None:
        print(f"{hko_source} 今日({today_str})最高温: {hko_high}℃")
        forecast_highs.append(hko_high)
        source_names.append(hko_source)
    else:
        print("香港天文台今日最高温: 获取失败（包括缓存）")

    print()

    if len(forecast_highs) < 2:
        print(f"预测源不足（当前 {len(forecast_highs)} 个），暂不生成概率分布")
        print(f"可用来源: {source_names}")
        return

    # Step 3: build probability input
    inp = HongKongProbabilityInput(
        observed_at=obs.observed_at or now_hk.isoformat(timespec="seconds"),
        current_temp=obs.current_temp,
        achieved_max_temp=obs.today_max_temp or obs.current_temp,
        forecast_highs=forecast_highs,
        local_hour=local_hour,
        settlement_station="香港天文台",
    )

    print("实时输入:")
    print(f"  当前温度:          {inp.current_temp}℃")
    print(f"  今日已实现最高温:  {inp.achieved_max_temp}℃")
    print(f"  已实现档位:        {bucket_for_temp(inp.achieved_max_temp)}")
    print(f"  预测源:            {list(zip(source_names, forecast_highs))}")
    print(f"  本地时间:          {inp.local_hour:.2f}")
    print()

    # Step 4: run probability model
    result = compute_probabilities(inp)

    print("模型输出:")
    print(f"  forecast_mean:               {result.forecast_mean:.2f}℃")
    print(f"  source_spread:               {result.source_spread:.2f}℃")
    print(f"  no_new_high_probability:     {result.no_new_high_probability:.4f}")
    print(f"  remaining_upside_probability:{result.remaining_upside_probability:.4f}")
    print(f"  model_center:                {result.model_center:.2f}℃")
    print(f"  sigma:                       {result.sigma:.4f}")
    print()
    print("Polymarket 温度档位概率:")
    print(format_probability_table(result.probabilities))
    print()
    total = sum(result.probabilities.values())
    print(f"概率总和: {total:.6f}")
    print(f"模型版本: {result.model_version}")
    print(f"警告: {result.warning}")
    print()

    # Validation
    probs = result.probabilities
    assert all(0 <= v <= 1 for v in probs.values()), "概率越界"
    assert abs(total - 1.0) < 1e-6, f"概率总和偏差: {total}"
    assert result.model_center >= inp.achieved_max_temp, "center < achieved"

    # Bins below achieved should be zero
    for name, lo, hi in BUCKETS:
        if hi is not None and hi <= inp.achieved_max_temp:
            assert probs.get(name, 0) == 0, f"{name} 不应有概率"

    print("✓ 所有验证通过")


def fetch_hko_flw_today_max_with_cache(
    session: requests.Session,
) -> tuple[str, float | None]:
    """Get HKO today's max temp from flw, falling back to cache when flw
    has switched to tonight/tomorrow and no longer includes today's max.
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
            # flw still has today's max → save cache and return
            entry = ForecastCacheEntry(
                forecast_date=today_str,
                source=source,
                forecast_high=today_max,
                captured_at=datetime.now(HKO_TZ).isoformat(timespec="seconds"),
                update_time=update_time,
                forecast_period=forecast_period,
                forecast_desc=forecast_desc[:500],
            )
            try:
                save_forecast_cache(entry)
                print("  ✓ 已保存今日 HKO 预测快照到缓存")
            except Exception as exc:
                print(f"  ⚠ 缓存写入失败: {exc}")
            return source, today_max
        else:
            print("  flw 未找到明确的今日最高温数值")

    # flw didn't have today's max → try cache
    cached = load_forecast_cache()
    if cached is not None:
        print(f"  当前 flw 已切换至今晚/明日，使用今日最近一次 HKO 有效预测快照")
        print(f"  快照 captured_at:   {cached.captured_at}")
        print(f"  快照 update_time:   {cached.update_time}")
        print(f"  快照 forecast_high: {cached.forecast_high}℃")
        return source, cached.forecast_high
    else:
        print("  无今日有效缓存可用")
        return source, None


def _fetch_flw_raw(session: requests.Session) -> dict | None:
    """Fetch raw flw JSON, or None on any failure."""
    params = {"dataType": "flw", "lang": "sc"}
    try:
        r = session.get(HKO_API_URL, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        print(f"  HKO flw 错误: {exc}")
        return None


def fetch_open_meteo_today_max(session: requests.Session) -> float | None:
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


# Regex patterns for extracting daily max temperature from HKO flw forecastDesc.
_HKO_MAX_TEMP_PATTERNS = [
    re.compile(r"最高气温(?:约[为是]?|可达|大約[是]?|大约[是]?|为|為|達)?\s*(\d+(?:\.\d+)?)\s*度"),
    re.compile(r"最高氣溫(?:約[為是]?|可達|大約[是]?|大约[是]?|為|为|達)?\s*(\d+(?:\.\d+)?)\s*度"),
]


def extract_hko_today_max_temp(forecast_desc: str) -> float | None:
    """Extract today's forecast max temperature from HKO flw forecastDesc text."""
    if not forecast_desc:
        return None
    for pattern in _HKO_MAX_TEMP_PATTERNS:
        m = pattern.search(forecast_desc)
        if m:
            try:
                return float(m.group(1))
            except (ValueError, TypeError):
                continue
    return None


# ── Offline cache tests ────────────────────────────────────────────────────

def run_offline_tests() -> None:
    from pathlib import Path
    """Run self-contained cache tests without any network calls."""
    import json
    import os
    import tempfile
    from datetime import timedelta

    from .hong_kong_forecast_cache import CACHE_DIR, CACHE_FILE

    print("=" * 60)
    print("离线缓存测试")
    print("=" * 60)
    passed = 0
    failed = 0

    now_hk = datetime.now(HKO_TZ)
    today = now_hk.strftime("%Y-%m-%d")
    yesterday = (now_hk - timedelta(days=1)).strftime("%Y-%m-%d")
    tomorrow = (now_hk + timedelta(days=1)).strftime("%Y-%m-%d")

    # Backup real cache if it exists
    real_cache = None
    if os.path.isfile(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            real_cache = f.read()

    def restore():
        if real_cache is not None:
            Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, "w") as f:
                f.write(real_cache)
        elif os.path.isfile(CACHE_FILE):
            os.unlink(CACHE_FILE)

    def write_cache(data: dict):
        Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f)

    try:
        # Test 1: today's valid cache is loadable
        write_cache({
            "forecast_date": today,
            "source": "香港天文台 Local Weather Forecast",
            "forecast_high": 31.0,
            "captured_at": now_hk.isoformat(timespec="seconds"),
            "update_time": "2026-06-07T11:30:00+08:00",
            "forecast_period": "今天",
            "forecast_desc": "最高气温约为31度",
        })
        entry = load_forecast_cache()
        assert entry is not None, "today cache should load"
        assert entry.forecast_high == 31.0, f"wrong high: {entry.forecast_high}"
        assert entry.forecast_date == today, f"wrong date: {entry.forecast_date}"
        print("✓ 测试1: 今天有效缓存可以读取")
        passed += 1

        # Test 2: yesterday's cache is rejected
        write_cache({
            "forecast_date": yesterday,
            "source": "香港天文台 Local Weather Forecast",
            "forecast_high": 30.0,
            "captured_at": (now_hk - timedelta(days=1)).isoformat(),
            "update_time": "",
            "forecast_period": "今天",
            "forecast_desc": "",
        })
        entry = load_forecast_cache()
        assert entry is None, "yesterday cache should be rejected"
        print("✓ 测试2: 昨天缓存会被拒绝")
        passed += 1

        # Test 3: tomorrow's cache is rejected
        write_cache({
            "forecast_date": tomorrow,
            "source": "香港天文台 Local Weather Forecast",
            "forecast_high": 32.0,
            "captured_at": now_hk.isoformat(),
            "update_time": "",
            "forecast_period": "",
            "forecast_desc": "",
        })
        entry = load_forecast_cache()
        assert entry is None, "tomorrow cache should be rejected"
        print("✓ 测试3: 明天缓存会被拒绝")
        passed += 1

        # Test 4: corrupted JSON does not crash
        with open(CACHE_FILE, "w") as f:
            f.write("not valid json {{{")
        entry = load_forecast_cache()
        assert entry is None, "corrupt JSON should return None"
        print("✓ 测试4: 损坏JSON不崩溃")
        passed += 1

        # Test 5: missing file returns None
        os.unlink(CACHE_FILE)
        entry = load_forecast_cache()
        assert entry is None, "missing file should return None"
        print("✓ 测试5: 缺失文件返回None")
        passed += 1

        # Test 6: save + load round-trip
        test_entry = ForecastCacheEntry(
            forecast_date=today,
            source="香港天文台 Local Weather Forecast",
            forecast_high=33.5,
            captured_at=now_hk.isoformat(timespec="seconds"),
            update_time="2026-06-07T16:00:00+08:00",
            forecast_period="今天",
            forecast_desc="最高气温约为33度。",
        )
        save_forecast_cache(test_entry)
        loaded = load_forecast_cache()
        assert loaded is not None, "round-trip should load"
        assert loaded.forecast_high == 33.5
        assert loaded.forecast_date == today
        assert loaded.source == test_entry.source
        print("✓ 测试6: 保存后读取一致")
        passed += 1

        # Test 7: save overwrites previous cache (today value updates)
        write_cache({
            "forecast_date": today,
            "source": "香港天文台 Local Weather Forecast",
            "forecast_high": 29.0,
            "captured_at": now_hk.isoformat(),
            "update_time": "",
            "forecast_period": "",
            "forecast_desc": "",
        })
        # Now save a newer entry — simulating flw still has today data
        newer = ForecastCacheEntry(
            forecast_date=today,
            source="香港天文台 Local Weather Forecast",
            forecast_high=30.5,
            captured_at=now_hk.isoformat(timespec="seconds"),
            update_time="later",
            forecast_period="今天",
            forecast_desc="",
        )
        save_forecast_cache(newer)
        loaded = load_forecast_cache()
        assert loaded is not None
        assert loaded.forecast_high == 30.5, "should use newer value"
        print("✓ 测试7: 同天覆盖缓存")
        passed += 1

        print()
        print(f"全部离线缓存测试通过: {passed}/{passed + failed}")

    except Exception as exc:
        print(f"✗ 测试失败: {exc}")
        import traceback
        traceback.print_exc()
    finally:
        restore()


if __name__ == "__main__":
    if "--offline-tests" in sys.argv:
        run_offline_tests()
    else:
        main()
