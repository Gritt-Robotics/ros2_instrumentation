# Copyright 2026 Gritt Robotics Inc.

from trace_instrumentation.trace_analysis.metrics.stats import (
    STAT_VALUES,
    fmt_hex_or_str,
    fmt_ns,
    stats,
    stats_ms,
)


def test_stats_returns_none_for_empty_values():
    assert stats([]) is None


def test_stats_single_value_has_zero_spread():
    summary = stats([42.0])
    assert summary is not None
    assert summary.count == 1
    assert summary.min == 42.0
    assert summary.p25 == 42.0
    assert summary.p50 == 42.0
    assert summary.p75 == 42.0
    assert summary.p90 == 42.0
    assert summary.p99 == 42.0
    assert summary.max == 42.0
    assert summary.mean == 42.0
    assert summary.values == [42.0]


def test_stats_computes_interpolated_percentiles():
    summary = stats([10.0, 20.0, 30.0, 40.0, 50.0])
    assert summary is not None
    assert summary.count == 5
    assert summary.min == 10.0
    assert summary.max == 50.0
    assert summary.mean == 30.0
    # idx = q/100 * (5 - 1); interpolate between the bracketing sorted values.
    assert summary.p25 == 20.0  # idx = 1.0 -> s[1]
    assert summary.p50 == 30.0  # idx = 2.0 -> s[2]
    assert summary.p75 == 40.0  # idx = 3.0 -> s[3]
    assert summary.p90 == 46.0  # idx = 3.6 -> 40% of s[3]=40, 60% of s[4]=50
    assert summary.p99 == 49.6  # idx = 3.96 -> 4% of s[3]=40, 96% of s[4]=50


def test_stats_values_preserve_input_order_not_sorted_order():
    summary = stats([30.0, 10.0, 50.0, 20.0, 40.0])
    assert summary is not None
    assert summary.values == [30.0, 10.0, 50.0, 20.0, 40.0]


def test_stats_ms_converts_values_to_milliseconds_in_original_order():
    summary_ms = stats_ms(stats([3_000_000.0, 1_000_000.0, 2_000_000.0]))
    assert summary_ms is not None
    assert summary_ms[STAT_VALUES] == [3.0, 1.0, 2.0]


def test_stats_ms_returns_none_for_none_input():
    assert stats_ms(None) is None


def test_fmt_ns_renders_none_as_placeholder():
    assert fmt_ns(None) == "-"


def test_fmt_ns_renders_nanoseconds_below_1000():
    assert fmt_ns(999) == "999ns"


def test_fmt_ns_renders_microseconds():
    assert fmt_ns(1_500) == "1.50us"


def test_fmt_ns_renders_milliseconds():
    assert fmt_ns(1_500_000) == "1.50ms"


def test_fmt_ns_renders_seconds():
    assert fmt_ns(1_500_000_000) == "1.500s"


def test_fmt_hex_or_str_renders_ints_as_hex():
    assert fmt_hex_or_str(255) == "0xff"


def test_fmt_hex_or_str_renders_non_ints_with_str():
    assert fmt_hex_or_str("topic_name") == "topic_name"
