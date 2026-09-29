# Copyright 2026 Gritt Robotics Inc.

"""
latency.py
==========

Per-topic pub<->take latency, per-topic callback-duration, and the
corresponding `write_*_csv` outputs (topic latency, callback duration, Python
zone duration, callback summary).

Note: multi-step pipeline chain latency (`_pipeline_chain_latency_stats`,
`write_pipeline_chain_latency_csv`) is deliberately NOT in this module -- it is
deferred to a later task.
"""

import csv
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

from trace_instrumentation.trace_analysis.analysis import TraceAnalysis
from trace_instrumentation.trace_analysis.flow_graph import FlowGraph
from trace_instrumentation.trace_analysis.graph import (
    CB,
    CB_KIND_TIMER,
    CallbackDurationRow,
    CallbackGroup,
    CallbackKey,
    CallbackTimingStats,
)
from trace_instrumentation.trace_analysis.metrics.stats import (
    StatsSummary,
    stats,
    stats_csv_row,
)

# Below this, an inter-callback-gap sample is treated as the executor re-entering
# essentially immediately rather than genuinely idling between invocations.
INSTANTANEOUS_WAIT_THRESHOLD_MS = 3.0
INSTANTANEOUS_WAIT_THRESHOLD_NS = INSTANTANEOUS_WAIT_THRESHOLD_MS * 1_000_000


def _latency_by_topic(
    flow: FlowGraph,
) -> Dict[str, StatsSummary]:
    """Per-topic pub<->take latency samples from matched edges.

    Args:
        flow (FlowGraph): Built flow graph.

    Returns:
        Dict[str, StatsSummary]: topic -> latency stats
    """
    lat_by_topic: Dict[str, List[int]] = defaultdict(list)
    for ed in flow.edges:
        if ed.latency_ns is None:
            continue
        lat_by_topic[ed.take.topic_label].append(ed.latency_ns)
    return {topic: stats(latencies) for topic, latencies in lat_by_topic.items()}


def callback_groups(callbacks: Iterable[CB]) -> Dict[CallbackKey, CallbackGroup]:
    """Gather callback invocations by identity, the one grouping every per-callback
    aggregation is built on.

    Takes the invocations rather than the whole graph so a caller that reports on a
    subset -- the node report drops internal-topic callbacks -- aggregates over exactly
    the set it reports, instead of filtering a graph-wide result afterwards.

    Args:
        callbacks (Iterable[CB]): Invocations to group, already carrying the identity
            FlowGraph stamped on them.

    Returns:
        Dict[CallbackKey, CallbackGroup]: group key -> its gathered invocations. A
        callback that never started is omitted entirely.
    """
    starts: Dict[CallbackKey, List[int]] = defaultdict(list)
    durations: Dict[CallbackKey, List[int]] = defaultdict(list)
    intra_counts: Dict[CallbackKey, int] = defaultdict(int)
    timer_periods: Dict[CallbackKey, int] = {}

    for cb in callbacks:
        if cb.ts_start is None:
            continue
        key = cb.group_key
        starts[key].append(cb.ts_start)
        if cb.kind == CB_KIND_TIMER and cb.timer_period_ns is not None:
            timer_periods.setdefault(key, cb.timer_period_ns)
        if cb.ts_end is None:
            continue
        durations[key].append(cb.ts_end - cb.ts_start)
        if cb.is_intra:
            intra_counts[key] += 1

    return {
        key: CallbackGroup(
            starts=group_starts,
            durations=durations.get(key, []),
            intra_count=intra_counts.get(key, 0),
            timer_period_ns=timer_periods.get(key),
        )
        for key, group_starts in starts.items()
    }


def callback_timing_stats(
    callbacks: Iterable[CB],
) -> Dict[CallbackKey, CallbackTimingStats]:
    """Per-callback-identity interarrival and inter-callback-gap stats, grouped
    identically to `callback_groups()`.

    Args:
        callbacks (Iterable[CB]): Invocations to aggregate; see `callback_groups()`
            for why this takes the invocations rather than the graph.

    Returns:
        Dict[CallbackKey, CallbackTimingStats]: key -> its timing stats.
    """
    starts_by_key: Dict[CallbackKey, List[int]] = defaultdict(list)
    by_node: Dict[str, List[Tuple[CallbackKey, int, Optional[int]]]] = defaultdict(list)
    for cb in callbacks:
        if cb.ts_start is None:
            continue
        key = cb.group_key
        starts_by_key[key].append(cb.ts_start)
        by_node[key[0]].append((key, cb.ts_start, cb.ts_end))

    gaps_by_key: Dict[CallbackKey, List[int]] = defaultdict(list)
    for node_invocations in by_node.values():
        node_invocations.sort(key=lambda item: item[1])
        for (key, _ts_start, ts_end), (_next_key, next_ts_start, _next_ts_end) in zip(
            node_invocations, node_invocations[1:], strict=False
        ):
            if ts_end is not None:
                gaps_by_key[key].append(next_ts_start - ts_end)

    result: Dict[CallbackKey, CallbackTimingStats] = {}
    for key in set(starts_by_key) | set(gaps_by_key):
        starts_sorted = sorted(starts_by_key.get(key, []))
        interarrivals = [
            b - a for a, b in zip(starts_sorted, starts_sorted[1:], strict=False)
        ]
        gap_samples = gaps_by_key.get(key, [])
        result[key] = CallbackTimingStats(
            interarrival_ns=stats(interarrivals),
            inter_callback_gap_ns=stats(gap_samples),
            instantaneous_wait_count=sum(
                1 for g in gap_samples if g < INSTANTANEOUS_WAIT_THRESHOLD_NS
            ),
        )
    return result


def callback_summary_rows(flow: FlowGraph) -> List[CallbackDurationRow]:
    """Aggregated callback *execution duration* stats per callback identity, unified
    across C++ (callback_start/callback_end) and Python (lttng zones) sources.

    Args:
        flow (FlowGraph): Built flow graph.

    Returns:
        List[CallbackDurationRow]: One row per group that completed at least one
        invocation, sorted by group key.
    """
    # flow.callbacks holds one CB per outermost zone -- iterating
    # analysis.python_zones instead would double-count nested helper zones.
    groups = callback_groups(flow.callbacks)

    rows = []
    for key in sorted(groups.keys()):
        group = groups[key]
        # A group whose every invocation is still open has no measurable duration.
        st = stats(group.durations)
        if st is None:
            continue
        node, kind, topic, handler = key
        rows.append(
            CallbackDurationRow(
                node=node,
                callback_type=kind,
                topic=topic,
                handler=handler,
                timer_period_ns=(
                    group.timer_period_ns if kind == CB_KIND_TIMER else None
                ),
                invocation_count=st.count,
                intra_count=group.intra_count,
                inter_count=st.count - group.intra_count,
                min_ns=st.min,
                mean_ns=int(st.mean),
                median_ns=int(st.p50),
                p99_ns=int(st.p99),
                max_ns=st.max,
            )
        )
    return rows


def write_topic_latency_csv(path: str, flow: FlowGraph) -> None:
    """
    Write per-topic aggregated latency stats: real cross-process pub->take latency.

    Args:
        path (str): Output CSV path.
        flow (FlowGraph): Built flow graph.
    """
    lat_by_topic = _latency_by_topic(flow)

    with open(path, "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow([
            "topic",
            "count",
            "min_ns",
            "mean_ns",
            "median_ns",
            "p99_ns",
            "max_ns",
        ])
        for topic in sorted(lat_by_topic.keys()):
            wr.writerow([topic, *stats_csv_row(lat_by_topic[topic])])


def write_python_zone_csv(path: str, analysis: TraceAnalysis) -> None:
    """
    Write per-Python-zone duration stats.

    Args:
        path (str): Output CSV path.
        analysis (TraceAnalysis): Built analysis.
    """
    with open(path, "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow([
            "zone_label",
            "logger_name",
            "zone_name",
            "count",
            "min_ns",
            "mean_ns",
            "median_ns",
            "p99_ns",
            "max_ns",
        ])
        for zone_label, durs in sorted(analysis.python_zone_durations.items()):
            st = stats(durs)
            logger_name, zone_name = zone_label.rsplit(":", 1)
            wr.writerow([zone_label, logger_name, zone_name, *stats_csv_row(st)])


def write_callback_summary_csv(path: str, flow: FlowGraph) -> None:
    """
    Write per-(node, callback_type, topic) aggregated callback duration stats.

    Args:
        path (str): Output CSV path.
        flow (FlowGraph): Built flow graph.
    """
    rows = callback_summary_rows(flow)
    with open(path, "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(CallbackDurationRow._fields)
        for row in rows:
            wr.writerow(row)
