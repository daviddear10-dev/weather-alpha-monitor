from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import requests

from .hong_kong_forecast_cache import (
    ForecastCacheEntry,
    save_forecast_cache,
    load_forecast_cache,
)
from .hong_kong_forecast_parser import extract_hko_today_max_temp

HKO_TZ = ZoneInfo("Asia/Hong_Kong")
HKO_FLW_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"


def main(
    session: requests.Session | None = None,
    *,
    cache_dir: str | None = None,
) -> None:
    """Capture HKO today forecast snapshot.

    Args:
        session: requests.Session to use (injectable for testing).
        cache_dir: Override cache directory (for testing with temp dirs).
    """
    now_hk = datetime.now(HKO_TZ)
    today_str = now_hk.strftime("%Y-%m-%d")

    print("HKO 今日预测快照采集")
    print(f"香港当地时间: {now_hk.isoformat(timespec='seconds')}")
    print(f"目标日期:      {today_str}")
    print()

    # Fetch flw
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": "weather-alpha-monitor/0.1"})

    params = {"dataType": "flw", "lang": "sc"}

    # Step 1: network request
    try:
        r = session.get(HKO_FLW_URL, params=params, timeout=25)
        r.raise_for_status()
    except Exception as exc:
        print(f"flw 请求失败: {exc}", file=sys.stderr)
        print("采集失败，保留已有缓存不变", file=sys.stderr)
        sys.exit(1)

    # Step 2: parse JSON
    try:
        data = r.json()
    except Exception as exc:
        print(f"flw JSON 解析失败: {exc}", file=sys.stderr)
        print("采集失败，保留已有缓存不变", file=sys.stderr)
        sys.exit(1)

    # Step 3: validate response structure
    if not isinstance(data, dict):
        print("flw 响应不是有效的 JSON 对象", file=sys.stderr)
        print("采集失败，保留已有缓存不变", file=sys.stderr)
        sys.exit(1)

    forecast_period = data.get("forecastPeriod", "")
    update_time = data.get("updateTime", "")
    forecast_desc = data.get("forecastDesc", "")

    if not isinstance(forecast_desc, str) or not forecast_desc.strip():
        print("flw 响应缺少 forecastDesc 字段或为空", file=sys.stderr)
        print("采集失败，保留已有缓存不变", file=sys.stderr)
        sys.exit(1)

    if not update_time:
        print("提示: flw 未提供 updateTime")

    print(f"forecastPeriod: {forecast_period}")
    print(f"updateTime:     {update_time}")
    print(f"forecastDesc:   {forecast_desc[:300]}")
    print()

    # Step 4: try to extract today's max temp
    today_max = extract_hko_today_max_temp(forecast_desc)

    if today_max is None:
        print("当前 flw 未提供今日最高温，可能已切换至今晚/明日")
        print("不覆盖已有缓存，正常退出")
        sys.exit(0)

    # Step 5: save cache
    entry = ForecastCacheEntry(
        forecast_date=today_str,
        source="香港天文台 Local Weather Forecast",
        forecast_high=today_max,
        captured_at=now_hk.isoformat(timespec="seconds"),
        update_time=update_time,
        forecast_period=forecast_period,
        forecast_desc=forecast_desc[:500],
    )

    try:
        save_forecast_cache(entry, cache_dir=cache_dir)
    except Exception as exc:
        print(f"缓存保存失败: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"✓ 快照已保存")
    print(f"  预测日期:   {today_str}")
    print(f"  最高温:     {today_max}℃")
    print(f"  抓取时间:   {entry.captured_at}")
    sys.exit(0)


# ── Exit-code tests ─────────────────────────────────────────────────────────

def run_exit_code_tests() -> None:
    """Run offline exit-code tests using mock responses.

    All cache I/O uses temp directories — never touches the real data/ path.
    """
    print("=" * 60)
    print("退出码测试")
    print("=" * 60)

    passed = 0
    failed = 0

    def record(test_name: str, condition: bool):
        nonlocal passed, failed
        if condition:
            print(f"✓ {test_name}")
            passed += 1
        else:
            print(f"✗ {test_name}")
            failed += 1

    # ── Test 1: network exception → exit 1 ──
    with tempfile.TemporaryDirectory() as tmp:
        s = MagicMock()
        s.get.side_effect = requests.ConnectionError("DNS failure")
        code = _capture_exit_code(main, session=s, cache_dir=tmp)
        record("测试1: 网络异常 → exit 1", code == 1)

    # ── Test 2: HTTP 500 → exit 1 ──
    with tempfile.TemporaryDirectory() as tmp:
        s = MagicMock()
        mr = MagicMock()
        mr.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
        s.get.return_value = mr
        code = _capture_exit_code(main, session=s, cache_dir=tmp)
        record("测试2: HTTP 500 → exit 1", code == 1)

    # ── Test 3: invalid JSON → exit 1 ──
    with tempfile.TemporaryDirectory() as tmp:
        s = MagicMock()
        mr = MagicMock()
        mr.raise_for_status.return_value = None
        mr.json.side_effect = ValueError("Invalid JSON")
        s.get.return_value = mr
        code = _capture_exit_code(main, session=s, cache_dir=tmp)
        record("测试3: 非法JSON → exit 1", code == 1)

    # ── Test 4: empty forecastDesc → exit 1 ──
    with tempfile.TemporaryDirectory() as tmp:
        s = MagicMock()
        mr = MagicMock()
        mr.raise_for_status.return_value = None
        mr.json.return_value = {"forecastDesc": ""}
        s.get.return_value = mr
        code = _capture_exit_code(main, session=s, cache_dir=tmp)
        record("测试4: forecastDesc为空 → exit 1", code == 1)

    # ── Test 5: valid "今晚及明日" but no today max → exit 0, no cache ──
    with tempfile.TemporaryDirectory() as tmp:
        s = MagicMock()
        mr = MagicMock()
        mr.raise_for_status.return_value = None
        mr.json.return_value = {
            "forecastPeriod": "今晚及明日",
            "updateTime": "2026-06-07T16:30:00+08:00",
            "forecastDesc": "今晚大致多云。明日部分时间有阳光，天气炎热。",
        }
        s.get.return_value = mr
        code = _capture_exit_code(main, session=s, cache_dir=tmp)
        cache_path = Path(tmp) / "hong_kong_today_forecast_cache.json"
        no_cache = not cache_path.is_file()
        record("测试5: 今晚/明日无今日最高温 → exit 0, 无缓存",
               code == 0 and no_cache)

    # ── Test 6: valid today max → exit 0, cache written ──
    with tempfile.TemporaryDirectory() as tmp:
        s = MagicMock()
        mr = MagicMock()
        mr.raise_for_status.return_value = None
        mr.json.return_value = {
            "forecastPeriod": "今天",
            "updateTime": "2026-06-07T11:30:00+08:00",
            "forecastDesc": "本港地区今日天气预测：大致天晴，日间酷热。最高气温约为33度。吹轻微至和缓偏南风。",
        }
        s.get.return_value = mr
        code = _capture_exit_code(main, session=s, cache_dir=tmp)
        cache_path = Path(tmp) / "hong_kong_today_forecast_cache.json"
        ok = code == 0 and cache_path.is_file()
        if ok:
            entry = load_forecast_cache(cache_dir=tmp)
            ok = entry is not None and entry.forecast_high == 33.0
        record("测试6: 有效今日最高温 → exit 0, 写入缓存 33.0℃", ok)

    # ── Test 7: non-dict response → exit 1 ──
    with tempfile.TemporaryDirectory() as tmp:
        s = MagicMock()
        mr = MagicMock()
        mr.raise_for_status.return_value = None
        mr.json.return_value = ["not", "a", "dict"]
        s.get.return_value = mr
        code = _capture_exit_code(main, session=s, cache_dir=tmp)
        record("测试7: 非dict响应 → exit 1", code == 1)

    # ── Test 8: missing forecastDesc key → exit 1 ──
    with tempfile.TemporaryDirectory() as tmp:
        s = MagicMock()
        mr = MagicMock()
        mr.raise_for_status.return_value = None
        mr.json.return_value = {"forecastPeriod": "今天"}
        s.get.return_value = mr
        code = _capture_exit_code(main, session=s, cache_dir=tmp)
        record("测试8: 缺少forecastDesc → exit 1", code == 1)

    total = passed + failed
    print()
    print(f"通过: {passed}/{total}")
    if failed > 0:
        sys.exit(1)


def _capture_exit_code(func, *args, **kwargs) -> int:
    """Call func and return the SystemExit code, or -1 if no exit."""
    try:
        func(*args, **kwargs)
        return -1  # no SystemExit raised
    except SystemExit as e:
        code = e.code
        return 0 if code is None else int(code)


if __name__ == "__main__":
    if "--test-exit-codes" in sys.argv:
        run_exit_code_tests()
    else:
        main()
