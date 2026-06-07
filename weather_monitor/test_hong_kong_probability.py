from __future__ import annotations

from .hong_kong_probability import (
    HongKongProbabilityInput,
    compute_probabilities,
    format_probability_table,
    BUCKETS,
    bucket_for_temp,
)


def run_case(label: str, inp: HongKongProbabilityInput) -> None:
    print("=" * 60)
    print(f"案例 {label}")
    print("=" * 60)
    print()

    result = compute_probabilities(inp)

    print("输入:")
    print(f"  current_temp:            {inp.current_temp}℃")
    print(f"  achieved_max_temp:       {inp.achieved_max_temp}℃")
    print(f"  achieved_bucket:         {bucket_for_temp(inp.achieved_max_temp)}")
    print(f"  forecast_highs:          {inp.forecast_highs}")
    print(f"  local_hour:              {inp.local_hour}")
    print()
    print("模型参数:")
    print(f"  forecast_mean:               {result.forecast_mean:.2f}℃")
    print(f"  source_spread:               {result.source_spread:.2f}℃")
    print(f"  model_center:                {result.model_center:.2f}℃")
    print(f"  sigma:                       {result.sigma:.4f}")
    print(f"  no_new_high_probability:     {result.no_new_high_probability:.4f}")
    print(f"  remaining_upside_probability:{result.remaining_upside_probability:.4f}")
    print()
    print("档位概率:")
    print(format_probability_table(result.probabilities))
    print()
    total = sum(result.probabilities.values())
    print(f"概率总和: {total:.6f}")
    print()

    # Assertions
    probs = result.probabilities
    assert all(0 <= v <= 1 for v in probs.values()), "概率不在 [0,1] 范围"
    assert abs(total - 1.0) < 1e-6, f"概率总和偏差过大: {total}"

    assert result.model_center >= inp.achieved_max_temp, (
        f"model_center ({result.model_center}) < achieved_max_temp ({inp.achieved_max_temp})"
    )

    assert abs(
        result.no_new_high_probability + result.remaining_upside_probability - 1.0
    ) < 1e-6, "no_new_high + remaining != 1"

    # Bins fully below achieved_max_temp should have zero probability
    for name, lo, hi in BUCKETS:
        if hi is not None and hi <= inp.achieved_max_temp:
            assert probs.get(name, 0.0) == 0.0, (
                f"档位 {name} (上限 {hi}) 应已被 achieved_max_temp ({inp.achieved_max_temp}) 跨过，但概率非零: {probs[name]}"
            )

    # Case-specific assertions
    achieved_bucket = bucket_for_temp(inp.achieved_max_temp)
    if label == "A":
        assert result.no_new_high_probability >= 0.80, (
            f"案例 A: no_new_high 应 >= 0.80, 实际 {result.no_new_high_probability}"
        )
        assert probs[achieved_bucket] >= probs.get("33°C", 0), "32°C 应 >= 33°C"
        assert probs.get("34°C or higher", 0) < 0.05, "34°C+ 应为小尾部"
    elif label == "B":
        assert result.no_new_high_probability < 0.85, "案例 B: no_new_high 应明显低于案例 A"
        assert probs.get("32°C", 0) > 0.15, "32°C 应有明显概率"
        assert probs.get("33°C", 0) > 0.15, "33°C 应有明显概率"
    elif label == "C":
        assert result.remaining_upside_probability > 0.3, "上午应有较高剩余升温概率"
        assert result.sigma >= 1.5, "大分歧应有大 sigma"

    print("✓ 所有断言通过")
    print()
    print()


def main() -> None:
    print("香港最终全天最高温概率模型 — 测试 (v2 混合模型)")
    print()

    case_a = HongKongProbabilityInput(
        observed_at="2026-06-07T15:48:00+08:00",
        current_temp=28.0,
        achieved_max_temp=32.0,
        forecast_highs=[30.2, 29.0],
        local_hour=15.8,
    )

    case_b = HongKongProbabilityInput(
        observed_at="2026-06-07T13:00:00+08:00",
        current_temp=31.0,
        achieved_max_temp=31.2,
        forecast_highs=[32.0, 33.0],
        local_hour=13.0,
    )

    case_c = HongKongProbabilityInput(
        observed_at="2026-06-07T11:00:00+08:00",
        current_temp=29.0,
        achieved_max_temp=29.5,
        forecast_highs=[30.0, 34.0],
        local_hour=11.0,
    )

    run_case("A", case_a)
    run_case("B", case_b)
    run_case("C", case_c)


if __name__ == "__main__":
    main()
