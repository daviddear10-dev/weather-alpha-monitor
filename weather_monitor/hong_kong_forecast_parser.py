from __future__ import annotations

import re

# Regex patterns for extracting daily max temperature from HKO flw forecastDesc.
# Ordered from most specific to least specific.
_HKO_MAX_TEMP_PATTERNS = [
    # "最高气温约为31度" / "最高气温约31度" / "最高气温为31度"
    re.compile(r"最高气温(?:约[为是]?|可达|大約[是]?|大约[是]?|为|為|達)?\s*(\d+(?:\.\d+)?)\s*度"),
    # fallback: "最高氣溫約31度" (traditional)
    re.compile(r"最高氣溫(?:約[為是]?|可達|大約[是]?|大约[是]?|為|为|達)?\s*(\d+(?:\.\d+)?)\s*度"),
]


def extract_hko_today_max_temp(forecast_desc: str) -> float | None:
    """Extract today's forecast max temperature from HKO flw forecastDesc text.

    Returns the extracted temperature in Celsius, or None if not found.
    """
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
