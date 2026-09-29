# Copyright 2026 Gritt Robotics Inc.

"""
stats.py
========

Small stats helpers shared by every per-topic/per-callback/per-zone latency
report in the trace analyzer.
"""

from typing import Any, List, NamedTuple, Optional

from numpy import percentile

NANOSECONDS_PER_MICROSECOND = 1_000
NANOSECONDS_PER_MILLISECOND = 1_000_000
NANOSECONDS_PER_SECOND = 1_000_000_000

STAT_COUNT = "count"
STAT_MIN = "min"
STAT_MEAN = "mean"
STAT_MEDIAN = "median"
STAT_P90 = "p90"
STAT_P99 = "p99"
STAT_MAX = "max"
STAT_VALUES = "values"


class StatsSummary(NamedTuple):
    """count/min/percentile/max/mean summary of a collection of duration values.

    `values` is appended last, and every other field keeps its existing position,
    so nothing that names a field precedes it in a positional unpack.
    """

    count: int
    min: float
    p25: float
    p50: float
    p75: float
    p90: float
    p99: float
    max: float
    mean: float
    values: List[float]


def stats(values: List[float]) -> Optional[StatsSummary]:
    """
    Compute count/min/p25/p50/p75/p90/p99/max/mean over a collection of values.

    Args:
        values (List[float]): Values to summarize.

    Returns:
        Optional[StatsSummary]: Summary of count/min/p25/p50/p75/p90/p99/max/mean,
        plus `values` itself in the order given (not the sorted order used to compute
        the percentiles above), or None if values is empty.
    """
    if len(values) == 0:
        return None
    s = sorted(values)
    return StatsSummary(
        count=len(s),
        min=s[0],
        p25=percentile(s, 25),
        p50=percentile(s, 50),
        p75=percentile(s, 75),
        p90=percentile(s, 90),
        p99=percentile(s, 99),
        max=s[-1],
        mean=sum(s) / len(s),
        values=list(values),
    )


def stats_csv_row(st: StatsSummary) -> List[int]:
    """
    Format a stats()-shaped StatsSummary as a CSV row.

    Args:
        st (StatsSummary): stats() output (must not be None).

    Returns:
        List[int]: [count, min, mean, median, p99, max].
    """
    return [
        st.count,
        int(st.min),
        int(st.mean),
        int(st.p50),
        int(st.p99),
        int(st.max),
    ]


def stats_ms(st: Optional[StatsSummary]) -> Optional[dict]:
    """
    Convert a stats()-shaped StatsSummary (nanoseconds) to a milliseconds dict.

    Args:
        st (Optional[StatsSummary]): stats() output, or None.

    Returns:
        Optional[dict]: keys count/min/mean/median/p90/p99/max, min/mean/median/p90/p99/max
        divided by 1e6 (count unchanged), and `values` (st.values, each divided by 1e6
        in its original order); None if st is None.
    """
    if st is None:
        return None
    return {
        STAT_COUNT: st.count,
        STAT_MIN: st.min / NANOSECONDS_PER_MILLISECOND,
        STAT_MEAN: st.mean / NANOSECONDS_PER_MILLISECOND,
        STAT_MEDIAN: st.p50 / NANOSECONDS_PER_MILLISECOND,
        STAT_P90: st.p90 / NANOSECONDS_PER_MILLISECOND,
        STAT_P99: st.p99 / NANOSECONDS_PER_MILLISECOND,
        STAT_MAX: st.max / NANOSECONDS_PER_MILLISECOND,
        STAT_VALUES: [v / NANOSECONDS_PER_MILLISECOND for v in st.values],
    }


def write_stat_row(
    writer: Any, step_label: str, kind: str, st: Optional[StatsSummary]
) -> None:
    """
    Write one step's stats as a CSV row, or nothing if there are no samples.

    Args:
        writer (Any): A csv.writer() instance to append the row to (untyped: the csv
            module exposes no public writer type).
        step_label (str): Row label identifying the step.
        kind (str): Row label identifying the stat kind (e.g. "network").
        st (Optional[StatsSummary]): stats() output for this step/kind, or None to skip
            the row.
    """
    if st is None:
        return
    writer.writerow([step_label, kind, *stats_csv_row(st)])


def fmt_ns(ns: Optional[float]) -> str:
    """
    Human-friendly duration.

    Args:
        ns (Optional[float]): Duration in nanoseconds, or None.
    Returns:
        str: A formatted string like "1.23ms", or "-" if ns is None.
    """
    if ns is None:
        return "-"
    if ns < NANOSECONDS_PER_MICROSECOND:
        return f"{ns:.0f}ns"
    if ns < NANOSECONDS_PER_MILLISECOND:
        return f"{ns / NANOSECONDS_PER_MICROSECOND:.2f}us"
    if ns < NANOSECONDS_PER_SECOND:
        return f"{ns / NANOSECONDS_PER_MILLISECOND:.2f}ms"
    return f"{ns / NANOSECONDS_PER_SECOND:.3f}s"


def fmt_hex_or_str(x: Any) -> str:
    """
    Render a value as a hex string if it's an int, else str().

    An int is typically a handle (a pointer to a publisher, subscriber, or
    callback), which are easier to correlate across events in hex.

    Args:
        x (Any): Value to render.

    Returns:
        str: Hex string for ints, str(x) otherwise.
    """
    # bools are a subclass of int but should not be rendered as hex
    if isinstance(x, bool):
        return str(x)
    return hex(x) if isinstance(x, int) else str(x)
