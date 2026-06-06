from __future__ import annotations

import json
import re
from pathlib import Path
from datetime import datetime
from typing import Any, Optional

import requests

from .monitor import load_cities


EVENTS_URL = "https://gamma-api.polymarket.com/events"
OUTPUT_PATH = Path("docs/polymarket_candidates.json")
POLYMARKET_EVENT_URL = "https://polymarket.com/event/{slug}"

WEATHER_KEYWORDS = [
    "weather",
    "temperature",
    "high temperature",
    "highest temperature",
    "max temperature",
]

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

CITY_ALIASES = {
    "香港": ["香港", "Hong Kong"],
    "深圳": ["深圳", "Shenzhen"],
    "北京": ["北京", "Beijing"],
    "上海": ["上海", "Shanghai"],
    "新加坡": ["新加坡", "Singapore"],
    "纽约": ["纽约", "New York", "NYC"],
    "伦敦": ["伦敦", "London"],
    "洛杉矶": ["洛杉矶", "Los Angeles", "LA"],
    "迈阿密": ["迈阿密", "Miami"],
    "多伦多": ["多伦多", "Toronto"],
}


def fetch_events() -> list[dict[str, Any]]:
    params = {
        "active": "true",
        "closed": "false",
        "limit": 100,
        "order": "volume24hr",
        "ascending": "false",
    }
    response = requests.get(EVENTS_URL, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("Unexpected Polymarket events response")
    return payload


def build_city_aliases() -> dict[str, list[str]]:
    aliases = {}
    for city in load_cities():
        city_aliases = CITY_ALIASES.get(city.name, [city.name])
        if city.name not in city_aliases:
            city_aliases = [city.name, *city_aliases]
        aliases[city.name] = city_aliases
    return aliases


def collect_candidates(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    city_aliases = build_city_aliases()
    candidates = []
    seen_keys = set()
    for event in events:
        markets = event.get("markets")
        if not isinstance(markets, list) or not markets:
            markets = [None]
        for market in markets:
            text = combined_text(event, market)
            if not has_weather_keyword(text):
                continue
            city_match = match_city(text, city_aliases)
            if city_match is None:
                continue
            candidate = build_candidate(event, market, city_match)
            dedupe_key = (
                candidate.get("raw_event_id"),
                candidate.get("raw_market_id"),
                candidate.get("city"),
            )
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            candidates.append(candidate)
    return candidates


def combined_text(event: dict[str, Any], market: Optional[dict[str, Any]]) -> str:
    values = [
        event.get("title"),
        event.get("slug"),
        event.get("description"),
    ]
    if market:
        values.extend(
            [
                market.get("question"),
                market.get("slug"),
                market.get("description"),
            ]
        )
    return " ".join(str(value or "") for value in values)


def has_weather_keyword(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in WEATHER_KEYWORDS)


def match_city(text: str, city_aliases: dict[str, list[str]]) -> Optional[tuple[str, str]]:
    lowered = text.lower()
    for city, aliases in city_aliases.items():
        for alias in aliases:
            if alias_matches(lowered, alias):
                return city, alias
    return None


def alias_matches(lowered_text: str, alias: str) -> bool:
    lowered_alias = alias.lower()
    if lowered_alias.isascii():
        pattern = r"(?<![a-z0-9])" + re.escape(lowered_alias) + r"(?![a-z0-9])"
        return re.search(pattern, lowered_text) is not None
    return lowered_alias in lowered_text


def build_candidate(
    event: dict[str, Any],
    market: Optional[dict[str, Any]],
    city_match: tuple[str, str],
) -> dict[str, Any]:
    city, matched_alias = city_match
    market_question = market.get("question") if market else event.get("title")
    event_slug = event.get("slug")
    slug = market.get("slug") if market else event_slug
    parsing_text = " ".join(
        [
            str(event.get("title") or ""),
            str(market_question or ""),
            str(event_slug or ""),
            str(slug or ""),
        ]
    )
    temperature = parse_temperature(parsing_text)
    condition, condition_reason = parse_condition(parsing_text)
    candidate = {
        "city": city,
        "matched_city_alias": matched_alias,
        "event_title": event.get("title"),
        "market_question": market_question,
        "slug": slug,
        "event_slug": event_slug,
        "forecast_date": parse_forecast_date(parsing_text),
        "condition": condition,
        "condition_reason": condition_reason,
        "start_date": get_first_value(market, event, "startDate"),
        "end_date": get_first_value(market, event, "endDate"),
        "volume": parse_number(get_first_value(market, event, "volume")),
        "volume24hr": parse_number(get_first_value(market, event, "volume24hr")),
        "liquidity": parse_number(get_first_value(market, event, "liquidity")),
        "active": bool(get_first_value(market, event, "active")),
        "closed": bool(get_first_value(market, event, "closed")),
        "raw_market_id": market.get("id") if market else None,
        "raw_event_id": event.get("id"),
        "url": POLYMARKET_EVENT_URL.format(slug=event_slug) if event_slug else None,
    }
    if temperature:
        candidate.update(
            {
                "metric": "max_temp",
                "threshold": temperature.get("threshold"),
                "yes_price": parse_yes_price(market),
            }
        )
        if temperature.get("threshold_fahrenheit") is not None:
            candidate["threshold_fahrenheit"] = temperature["threshold_fahrenheit"]
    return candidate


def get_first_value(
    market: Optional[dict[str, Any]],
    event: dict[str, Any],
    key: str,
) -> Any:
    if market and market.get(key) is not None:
        return market.get(key)
    return event.get(key)


def parse_temperature(text: str) -> Optional[dict[str, Optional[float]]]:
    celsius_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:°\s*C|℃|celsius)", text, re.IGNORECASE)
    if celsius_match:
        return {"threshold": float(celsius_match.group(1)), "threshold_fahrenheit": None}

    fahrenheit_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:°\s*F|fahrenheit|degrees)",
        text,
        re.IGNORECASE,
    )
    if fahrenheit_match:
        return {"threshold": None, "threshold_fahrenheit": float(fahrenheit_match.group(1))}

    return None


def parse_condition(text: str) -> tuple[str, str]:
    lowered = text.lower()
    below_phrases = ["at or below", "or below", "or lower"]
    above_phrases = ["at or above", "or higher", "or above"]
    for phrase in below_phrases:
        if phrase in lowered:
            return "<=", f'包含短语 "{phrase}"'
    for phrase in above_phrases:
        if phrase in lowered:
            return ">=", f'包含短语 "{phrase}"'
    return "=", "未发现 or below/or above 等方向短语，按精确温度市场处理"


def parse_forecast_date(text: str) -> Optional[str]:
    normalized = text.replace("-", " ")
    patterns = [
        re.compile(
            r"\b(?:on\s+)?(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+"
            r"(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<year>\d{4}))?\b",
            re.IGNORECASE,
        ),
    ]
    for pattern in patterns:
        match = pattern.search(normalized)
        if not match:
            continue
        month = MONTHS[match.group("month").lower()]
        day = int(match.group("day"))
        year = int(match.group("year") or datetime.now().year)
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            return None
    return None


def parse_yes_price(market: Optional[dict[str, Any]]) -> Optional[float]:
    if not market:
        return None
    outcomes = parse_jsonish_list(market.get("outcomes"))
    prices = parse_jsonish_list(market.get("outcomePrices"))
    if not outcomes or not prices:
        return None
    for index, outcome in enumerate(outcomes):
        if str(outcome).strip().lower() == "yes" and index < len(prices):
            return parse_number(prices[index])
    return None


def parse_jsonish_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def parse_number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def write_candidates(candidates: list[dict[str, Any]], output_path: Path = OUTPUT_PATH) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    candidates: list[dict[str, Any]] = []
    try:
        events = fetch_events()
        candidates = collect_candidates(events)
    except Exception as exc:
        print(f"Polymarket API 请求失败：{exc}")
    write_candidates(candidates)
    print(f"找到 {len(candidates)} 个候选市场")
    print(f"写入 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
