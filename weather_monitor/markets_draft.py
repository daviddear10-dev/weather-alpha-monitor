from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


CANDIDATES_PATH = Path("docs/polymarket_candidates.json")
WEATHER_DATA_PATH = Path("docs/weather_data.json")
OUTPUT_PATH = Path("docs/markets_draft.json")
ALLOWED_CONDITIONS = {">=", "<=", "="}


def load_json_array(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def extract_weather_city_dates(weather_data: list[dict[str, Any]]) -> set[tuple[str, str]]:
    pairs = set()
    for item in weather_data:
        city = item.get("city")
        forecast_date = item.get("forecast_date")
        if city and forecast_date:
            pairs.add((str(city), str(forecast_date)))
    return pairs


def build_drafts(
    candidates: list[dict[str, Any]],
    weather_city_dates: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str, float, str], dict[str, Any]] = {}
    for candidate in candidates:
        if not is_eligible_candidate(candidate, weather_city_dates):
            continue
        draft = build_draft_record(candidate)
        key = (
            draft["city"],
            draft["forecast_date"],
            float(draft["threshold"]),
            draft["condition"],
        )
        existing = deduped.get(key)
        if existing is None or value_for_sort(draft.get("volume24hr")) > value_for_sort(existing.get("volume24hr")):
            deduped[key] = draft
    return sorted(
        deduped.values(),
        key=lambda item: (
            item["forecast_date"],
            item["city"],
            float(item["threshold"]),
        ),
    )


def is_eligible_candidate(
    candidate: dict[str, Any],
    weather_city_dates: set[tuple[str, str]],
) -> bool:
    city = candidate.get("city")
    forecast_date = candidate.get("forecast_date")
    return (
        bool(city)
        and bool(forecast_date)
        and (str(city), str(forecast_date)) in weather_city_dates
        and candidate.get("threshold") is not None
        and candidate.get("metric") == "max_temp"
        and candidate.get("condition") in ALLOWED_CONDITIONS
        and candidate.get("active") is True
        and candidate.get("closed") is False
    )


def build_draft_record(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "city": candidate.get("city"),
        "forecast_date": candidate.get("forecast_date"),
        "metric": candidate.get("metric"),
        "market_question": candidate.get("market_question"),
        "threshold": parse_number(candidate.get("threshold")),
        "condition": candidate.get("condition"),
        "yes_price": parse_number(candidate.get("yes_price")),
        "source": "polymarket_candidate",
        "url": candidate.get("url"),
        "volume": parse_number(candidate.get("volume")),
        "volume24hr": parse_number(candidate.get("volume24hr")),
        "liquidity": parse_number(candidate.get("liquidity")),
        "raw_market_id": candidate.get("raw_market_id"),
        "raw_event_id": candidate.get("raw_event_id"),
        "condition_reason": candidate.get("condition_reason"),
    }


def parse_number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def value_for_sort(value: Any) -> float:
    number = parse_number(value)
    return number if number is not None else -1.0


def write_drafts(drafts: list[dict[str, Any]], output_path: Path = OUTPUT_PATH) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(drafts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    candidates = load_json_array(CANDIDATES_PATH)
    weather_data = load_json_array(WEATHER_DATA_PATH)
    weather_city_dates = extract_weather_city_dates(weather_data)
    drafts = build_drafts(candidates, weather_city_dates)
    write_drafts(drafts)
    print(f"weather_data 中有 {len(weather_city_dates)} 个 city/date 组合")
    print(f"candidates 有 {len(candidates)} 条")
    print(f"生成 markets_draft {len(drafts)} 条")


if __name__ == "__main__":
    main()
