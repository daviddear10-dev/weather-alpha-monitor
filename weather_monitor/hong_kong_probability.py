from __future__ import annotations

import math
from dataclasses import dataclass, field

BUCKETS = [
    ("24°C or below", None, 24.5),
    ("25°C", 24.5, 25.5),
    ("26°C", 25.5, 26.5),
    ("27°C", 26.5, 27.5),
    ("28°C", 27.5, 28.5),
    ("29°C", 28.5, 29.5),
    ("30°C", 29.5, 30.5),
    ("31°C", 30.5, 31.5),
    ("32°C", 31.5, 32.5),
    ("33°C", 32.5, 33.5),
    ("34°C or higher", 33.5, None),
]


def bucket_for_temp(temp: float) -> str:
    """Return the bucket name that a given temperature falls into."""
    for name, lo, hi in BUCKETS:
        if lo is None:
            if temp <= hi:
                return name
        elif hi is None:
            if temp >= lo:
                return name
        else:
            if lo <= temp < hi:
                return name
    return "34°C or higher"


@dataclass
class HongKongProbabilityInput:
    observed_at: str
    current_temp: float
    achieved_max_temp: float
    forecast_highs: list[float]
    local_hour: float
    settlement_station: str = "香港天文台"


@dataclass
class HongKongProbabilityResult:
    settlement_station: str
    observed_at: str
    current_temp: float
    achieved_max_temp: float
    forecast_mean: float
    source_spread: float
    model_center: float
    sigma: float
    no_new_high_probability: float = 0.0
    remaining_upside_probability: float = 0.0
    probabilities: dict[str, float] = field(default_factory=dict)
    model_version: str = "hk-max-v1-heuristic"
    warning: str = "未经历史数据校准，仅为启发式模型估计"


def compute_probabilities(inp: HongKongProbabilityInput) -> HongKongProbabilityResult:
    achieved = inp.achieved_max_temp
    forecast_highs = inp.forecast_highs
    hour = inp.local_hour

    forecast_mean = sum(forecast_highs) / len(forecast_highs)
    source_spread = max(forecast_highs) - min(forecast_highs)

    # --- Step 1: no_new_high_probability ---
    no_new_high = _compute_no_new_high(inp, forecast_mean)

    # --- Step 2: conditionally allocate upside probability ---
    remaining = 1.0 - no_new_high

    # Build probability dict, start with no_new_high going to achieved bucket
    achieved_bucket = bucket_for_temp(achieved)
    probs = {name: 0.0 for name, _, _ in BUCKETS}
    probs[achieved_bucket] = no_new_high

    if remaining > 0:
        # Conditional distribution: N(center, sigma) truncated strictly > achieved
        center, sigma = _compute_conditional_params(inp, forecast_mean)

        raw_upside = {}
        for name, lo, hi in BUCKETS:
            raw_upside[name] = _trunc_normal_above(
                lo, hi, center, sigma, achieved
            )

        total_upside = sum(raw_upside.values())
        if total_upside > 0:
            for name in probs:
                probs[name] += (raw_upside[name] / total_upside) * remaining

    return HongKongProbabilityResult(
        settlement_station=inp.settlement_station,
        observed_at=inp.observed_at,
        current_temp=inp.current_temp,
        achieved_max_temp=inp.achieved_max_temp,
        forecast_mean=forecast_mean,
        source_spread=source_spread,
        model_center=center if remaining > 0 else achieved,
        sigma=sigma if remaining > 0 else 0.0,
        no_new_high_probability=no_new_high,
        remaining_upside_probability=remaining,
        probabilities=probs,
    )


def _compute_no_new_high(inp: HongKongProbabilityInput, forecast_mean: float) -> float:
    """Compute probability that no new daily high will be set."""
    hour = inp.local_hour

    # Base by time of day
    if hour < 12:
        p = 0.15
    elif hour < 14:
        p = 0.35
    elif hour < 16:
        p = 0.65
    elif hour < 18:
        p = 0.85
    else:
        p = 0.97

    # Adjust based on forecast vs achieved
    if forecast_mean <= inp.achieved_max_temp:
        p += 0.15

    if inp.current_temp <= inp.achieved_max_temp - 1.0:
        p += 0.08

    if inp.current_temp <= inp.achieved_max_temp - 2.0:
        p += 0.07

    if forecast_mean >= inp.achieved_max_temp + 1.0:
        p -= 0.15

    return min(max(p, 0.05), 0.995)


def _compute_conditional_params(
    inp: HongKongProbabilityInput, forecast_mean: float
) -> tuple[float, float]:
    """Compute center and sigma for the conditional upside distribution."""
    achieved = inp.achieved_max_temp
    hour = inp.local_hour
    forecast_highs = inp.forecast_highs
    source_spread = max(forecast_highs) - min(forecast_highs)

    # Time factor
    if hour < 12:
        tf = 1.00
    elif hour < 14:
        tf = 0.75
    elif hour < 16:
        tf = 0.40
    elif hour < 18:
        tf = 0.15
    else:
        tf = 0.05

    remaining_uplift = max(0.0, forecast_mean - achieved)
    center = achieved + remaining_uplift * tf
    center = max(center, achieved + 0.15)

    # Sigma
    sigma = 0.45 + source_spread * 0.35

    if hour < 12:
        sigma += 0.45
    elif hour < 14:
        sigma += 0.25
    elif hour < 16:
        sigma += 0.10
    elif hour < 18:
        sigma -= 0.05
    else:
        sigma -= 0.15

    sigma = min(max(sigma, 0.25), 2.0)

    return center, sigma


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _trunc_normal_above(
    lo: float | None,
    hi: float | None,
    mu: float,
    sigma: float,
    trunc_low: float,
) -> float:
    """Probability mass in [lo, hi) for N(mu, sigma), truncated below trunc_low."""
    a = float("-inf") if lo is None else lo
    b = float("inf") if hi is None else hi

    a = max(a, trunc_low)
    b = max(b, trunc_low)

    if a >= b:
        return 0.0

    cdf_a = _normal_cdf((a - mu) / sigma) if a > float("-inf") else 0.0
    cdf_b = _normal_cdf((b - mu) / sigma) if b < float("inf") else 1.0

    return max(0.0, cdf_b - cdf_a)


def format_probability_table(probabilities: dict[str, float]) -> str:
    lines = []
    for name in [b[0] for b in BUCKETS]:
        pct = probabilities.get(name, 0.0) * 100
        bar = "█" * max(0, int(pct * 2))
        lines.append(f"  {name:<16s} {pct:6.2f}% {bar}")
    return "\n".join(lines)
