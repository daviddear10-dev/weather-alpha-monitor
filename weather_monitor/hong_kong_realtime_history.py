from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .hong_kong_realtime import SettlementObservation

HKO_TZ = ZoneInfo("Asia/Hong_Kong")
OUTPUT_PATH = Path("docs/hong_kong_realtime_history.json")
SOURCE = "Open-Meteo + 香港天文台-实时观测"


def save_realtime_observation(
    observation: SettlementObservation,
    open_meteo_current_temp: float,
    open_meteo_observed_at: str,
    output_path: Path = OUTPUT_PATH,
    captured_at: str | None = None,
) -> dict[str, Any]:
    now_hk = datetime.now(HKO_TZ)
    local_date = now_hk.date().isoformat()
    if observation.current_temp is None:
        raise ValueError("香港天文台当前温度不能为空")

    hko_current_temp = float(observation.current_temp)
    open_meteo_current_temp = float(open_meteo_current_temp)
    average_current_temp = round(
        (hko_current_temp + open_meteo_current_temp) / 2.0,
        2,
    )

    record = {
        "city": "香港",
        "source": SOURCE,
        "sources": ["Open-Meteo", "香港天文台"],
        "local_date": local_date,
        "captured_at": captured_at or now_hk.isoformat(timespec="seconds"),
        "hko_current_temp": hko_current_temp,
        "open_meteo_current_temp": open_meteo_current_temp,
        "average_current_temp": average_current_temp,

        # 暂时保留 current_temp，兼容现有页面。
        # 后续页面修改完成后，它代表双源平均实时温度。
        "current_temp": average_current_temp,

        "hko_observed_at": observation.observed_at,
        "open_meteo_observed_at": open_meteo_observed_at,

        # 兼容现有字段；Polymarket 已实现最高温仍只采用香港天文台。
        "observed_at": observation.observed_at,
        "today_max_temp": observation.today_max_temp,
        "max_temp_updated_at": observation.max_temp_updated_at,
    }

    rows = [
        row
        for row in _load_history(output_path)
        if row.get("city") == "香港" and row.get("local_date") == local_date
    ]
    by_captured_at = {
        str(row.get("captured_at")): row
        for row in rows
        if row.get("captured_at")
    }
    by_captured_at.setdefault(record["captured_at"], record)

    output_rows = sorted(
        by_captured_at.values(),
        key=lambda row: str(row.get("captured_at", "")),
        reverse=True,
    )
    _atomic_write_json(output_path, output_rows)
    return record



def load_latest_realtime_record(
    output_path: Path = OUTPUT_PATH,
    local_date: str | None = None,
) -> dict[str, Any] | None:
    target_date = (
        local_date
        or datetime.now(HKO_TZ).date().isoformat()
    )

    rows = [
        row
        for row in _load_history(output_path)
        if row.get("city") == "香港"
        and row.get("local_date") == target_date
        and row.get("captured_at")
    ]

    if not rows:
        return None

    return max(
        rows,
        key=lambda row: str(row.get("captured_at", "")),
    )


def _load_history(path: Path) -> list[dict[str, Any]]:
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

    return [item for item in payload if isinstance(item, dict)]


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
