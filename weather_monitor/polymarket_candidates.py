from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from .monitor import load_cities


EVENTS_URL = "https://gamma-api.polymarket.com/events"
OUTPUT_PATH = Path("docs/polymarket_candidates.json")
POLYMARKET_EVENT_URL = "https://polymarket.com/event/{slug}"
DEFAULT_TIMEZONE = "Asia/Shanghai"

WEATHER_KEYWORDS = [
    "weather",
    "temperature",
    "high temperature",
    "highest temperature",
    "max temperature",
]

MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
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


def build_city_timezones() -> dict[str, ZoneInfo]:
    """Build city_name -> ZoneInfo mapping from cities.json."""
    tz_map: dict[str, ZoneInfo] = {}
    for city in load_cities():
        tz_name = city.timezone
        try:
            tz_map[city.name] = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz_map[city.name] = ZoneInfo(DEFAULT_TIMEZONE)
    return tz_map


def get_city_local_today_tomorrow(city_name: str, tz_map: dict[str, ZoneInfo]) -> set[str]:
    """Return {today_iso, tomorrow_iso} for the given city's local timezone."""
    tz = tz_map.get(city_name, ZoneInfo(DEFAULT_TIMEZONE))
    today = datetime.now(tz).date()
    tomorrow = today + timedelta(days=1)
    return {today.isoformat(), tomorrow.isoformat()}


def collect_candidates(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    city_aliases = build_city_aliases()
    city_timezones = build_city_timezones()
    # Pre-compute today/tomorrow for each known city
    city_date_ranges = {
        name: get_city_local_today_tomorrow(name, city_timezones)
        for name in city_aliases
    }

    candidates = []
    seen_keys = set()
    stats = Counter()

    for event in events:
        markets = event.get("markets")
        if not isinstance(markets, list) or not markets:
            markets = [None]
        for market in markets:
            stats["api_raw_markets"] += 1

            text = combined_text(event, market)
            if not has_weather_keyword(text):
                stats["skipped_no_keyword"] += 1
                continue

            city_match = match_city(text, city_aliases)
            if city_match is None:
                stats["skipped_no_city"] += 1
                continue

            city_name, matched_alias = city_match

            candidate = build_candidate(event, market, city_match)

            # Filter: closed markets
            if candidate.get("closed") is True:
                stats["filtered_closed"] += 1
                continue

            # Filter: not active
            if candidate.get("active") is False:
                stats["filtered_inactive"] += 1
                continue

            # Filter: not accepting orders
            if candidate.get("accepting_orders") is False:
                stats["filtered_not_accepting_orders"] += 1
                continue

            # Filter: end_date has passed (market already ended)
            end_date_str = candidate.get("end_date")
            if end_date_str:
                end_dt = parse_iso_datetime(end_date_str)
                if end_dt is not None and end_dt < datetime.now(timezone.utc):
                    stats["filtered_ended"] += 1
                    continue

            # Filter: forecast_date must be today or tomorrow in city's local TZ
            forecast_date = candidate.get("forecast_date")
            if not forecast_date:
                stats["filtered_no_date"] += 1
                continue

            allowed_dates = city_date_ranges.get(
                city_name,
                get_city_local_today_tomorrow(city_name, city_timezones),
            )
            if forecast_date not in allowed_dates:
                stats["filtered_date_out_of_range"] += 1
                continue

            dedupe_key = (
                candidate.get("raw_event_id"),
                candidate.get("raw_market_id"),
                candidate.get("city"),
            )
            if dedupe_key in seen_keys:
                stats["skipped_duplicate"] += 1
                continue
            seen_keys.add(dedupe_key)

            # Mark date category for sorting
            today_iso = datetime.now(
                city_timezones.get(city_name, ZoneInfo(DEFAULT_TIMEZONE))
            ).date().isoformat()
            candidate["_date_category"] = 0 if forecast_date == today_iso else 1

            candidates.append(candidate)

    # Sort: today first, then tomorrow, then by volume24hr desc
    candidates.sort(
        key=lambda c: (
            c.get("_date_category", 99),
            -(parse_number(c.get("volume24hr")) or 0),
        )
    )

    # Remove internal sort key
    for c in candidates:
        c.pop("_date_category", None)

    # Print stats
    print_statistics(stats, len(candidates))

    return candidates


def print_statistics(stats: Counter, final_count: int) -> None:
    print(f"API 原始市场数量: {stats['api_raw_markets']}")
    print(f"因无天气关键词跳过: {stats.get('skipped_no_keyword', 0)}")
    print(f"因无法匹配城市跳过: {stats.get('skipped_no_city', 0)}")
    print(f"因市场已关闭过滤: {stats.get('filtered_closed', 0)}")
    print(f"因市场不活跃过滤: {stats.get('filtered_inactive', 0)}")
    print(f"因停止接单过滤: {stats.get('filtered_not_accepting_orders', 0)}")
    print(f"因市场结束时间已到过滤: {stats.get('filtered_ended', 0)}")
    print(f"因日期不在当地今天/明天过滤: {stats.get('filtered_date_out_of_range', 0)}")
    print(f"因无预报日期过滤: {stats.get('filtered_no_date', 0)}")
    print(f"因重复跳过: {stats.get('skipped_duplicate', 0)}")
    print(f"最终候选市场数量: {final_count}")


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
    forecast_date = parse_forecast_date(parsing_text)

    candidate = {
        "city": city,
        "matched_city_alias": matched_alias,
        "event_title": event.get("title"),
        "market_question": market_question,
        "slug": slug,
        "event_slug": event_slug,
        "forecast_date": forecast_date,
        "condition": condition,
        "condition_reason": condition_reason,
        "start_date": get_first_value(market, event, "startDate"),
        "end_date": get_first_value(market, event, "endDate"),
        "volume": parse_number(get_first_value(market, event, "volume")),
        "volume24hr": parse_number(get_first_value(market, event, "volume24hr")),
        "liquidity": parse_number(get_first_value(market, event, "liquidity")),
        "active": parse_bool(get_first_value(market, event, "active")),
        "closed": parse_bool(get_first_value(market, event, "closed")),
        "accepting_orders": parse_bool(get_first_value(market, event, "acceptingOrders")),
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


def parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = str(value).strip()
        # Handle 'Z' suffix and timezone offsets
        text = text.replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None


def parse_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1"):
            return True
        if lowered in ("false", "0"):
            return False
    return None


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
    print(f"写入 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
