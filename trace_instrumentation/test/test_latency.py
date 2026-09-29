# Copyright 2026 Gritt Robotics Inc.

import csv

from synthetic_events import (
    callback_end_event,
    callback_start_event,
    node_init_event,
    publish_events,
    publisher_init_event,
    python_zone_event,
    rmw_take_event,
    subscription_init_event,
    timer_events,
)
from trace_instrumentation.trace_analysis.analysis import TraceAnalysis
from trace_instrumentation.trace_analysis.ctf.reader import Metadata
from trace_instrumentation.trace_analysis.flow_graph import FlowGraph
from trace_instrumentation.trace_analysis.metrics.latency import (
    INSTANTANEOUS_WAIT_THRESHOLD_NS,
    callback_timing_stats,
    write_callback_summary_csv,
    write_python_zone_csv,
    write_topic_latency_csv,
)

PRODUCER_VPID = 200
PRODUCER_VTID = 20
PRODUCER_NODE_HANDLE = 900
PRODUCER_NODE_NAME = "producer"
PRODUCER_PUBLISHER_HANDLE = 1
PRODUCER_RMW_PUBLISHER_HANDLE = 901

RELAY_VPID = 100
RELAY_VTID = 10
RELAY_NODE_HANDLE = 910
RELAY_NODE_NAME = "relay"
RELAY_SUBSCRIPTION_HANDLE = 10
RELAY_RMW_SUBSCRIPTION_HANDLE = 11

STEP0_TOPIC = "/step0"
ON_MESSAGE_ZONE = "_on_message"

TIMER_VPID = 300
TIMER_VTID = 30
TIMER_NODE_HANDLE = 920
TIMER_NODE_NAME = "timer_node"
TIMER_HANDLE = 40
TIMER_CALLBACK_PTR = 41
TIMER_PERIOD_NS = 100_000_000


def _build(events):
    meta = Metadata()
    analysis = TraceAnalysis(meta, events).build()
    flow = FlowGraph(meta, events, analysis).build()
    return analysis, flow


def _one_step_events():
    # Producer -> relay: one publish, one take, one Python zone wrapping the take --
    # a single cross-process edge.
    return [
        node_init_event(
            0, PRODUCER_VPID, PRODUCER_VTID, PRODUCER_NODE_HANDLE, PRODUCER_NODE_NAME
        ),
        node_init_event(0, RELAY_VPID, RELAY_VTID, RELAY_NODE_HANDLE, RELAY_NODE_NAME),
        publisher_init_event(
            0,
            PRODUCER_VPID,
            PRODUCER_VTID,
            PRODUCER_PUBLISHER_HANDLE,
            PRODUCER_NODE_HANDLE,
            PRODUCER_RMW_PUBLISHER_HANDLE,
            STEP0_TOPIC,
        ),
        subscription_init_event(
            0,
            RELAY_VPID,
            RELAY_VTID,
            RELAY_SUBSCRIPTION_HANDLE,
            RELAY_NODE_HANDLE,
            RELAY_RMW_SUBSCRIPTION_HANDLE,
            STEP0_TOPIC,
        ),
        *publish_events(
            1000, PRODUCER_VPID, PRODUCER_VTID, 100, PRODUCER_PUBLISHER_HANDLE, 5000
        ),
        python_zone_event(2000, RELAY_VPID, RELAY_VTID, ON_MESSAGE_ZONE, "begin"),
        rmw_take_event(
            2100, RELAY_VPID, RELAY_VTID, 200, RELAY_RMW_SUBSCRIPTION_HANDLE, 5000
        ),
        python_zone_event(2400, RELAY_VPID, RELAY_VTID, ON_MESSAGE_ZONE, "end"),
    ]


def _timer_fixture_events():
    # A timer node with two callback invocations exactly TIMER_PERIOD_NS apart.
    return [
        node_init_event(0, TIMER_VPID, TIMER_VTID, TIMER_NODE_HANDLE, TIMER_NODE_NAME),
        *timer_events(
            0,
            TIMER_VPID,
            TIMER_VTID,
            TIMER_HANDLE,
            TIMER_NODE_HANDLE,
            TIMER_CALLBACK_PTR,
            TIMER_PERIOD_NS,
        ),
        callback_start_event(0, TIMER_VPID, TIMER_VTID, TIMER_CALLBACK_PTR),
        callback_end_event(1000, TIMER_VPID, TIMER_VTID, TIMER_CALLBACK_PTR),
        callback_start_event(
            TIMER_PERIOD_NS, TIMER_VPID, TIMER_VTID, TIMER_CALLBACK_PTR
        ),
        callback_end_event(
            TIMER_PERIOD_NS + 1000, TIMER_VPID, TIMER_VTID, TIMER_CALLBACK_PTR
        ),
    ]


def _timer_fixture_events_with_tight_gap():
    # A timer node with two callback invocations whose end-to-next-start gap is well
    # under INSTANTANEOUS_WAIT_THRESHOLD_NS.
    tight_gap_ns = 500
    return [
        node_init_event(0, TIMER_VPID, TIMER_VTID, TIMER_NODE_HANDLE, TIMER_NODE_NAME),
        *timer_events(
            0,
            TIMER_VPID,
            TIMER_VTID,
            TIMER_HANDLE,
            TIMER_NODE_HANDLE,
            TIMER_CALLBACK_PTR,
            TIMER_PERIOD_NS,
        ),
        callback_start_event(0, TIMER_VPID, TIMER_VTID, TIMER_CALLBACK_PTR),
        callback_end_event(1000, TIMER_VPID, TIMER_VTID, TIMER_CALLBACK_PTR),
        callback_start_event(
            1000 + tight_gap_ns, TIMER_VPID, TIMER_VTID, TIMER_CALLBACK_PTR
        ),
        callback_end_event(
            2000 + tight_gap_ns, TIMER_VPID, TIMER_VTID, TIMER_CALLBACK_PTR
        ),
    ]


def test_write_topic_latency_csv(tmp_path):
    _analysis, flow = _build(_one_step_events())
    path = str(tmp_path / "topic_latency.csv")

    write_topic_latency_csv(path, flow)

    with open(path, newline="") as fh:
        rows = {row["topic"]: row for row in csv.DictReader(fh)}
    assert int(rows[STEP0_TOPIC]["count"]) == 1


def test_write_topic_latency_csv_writes_header_only_when_no_matched_edges(tmp_path):
    _analysis, flow = _build([])
    path = str(tmp_path / "topic_latency.csv")

    write_topic_latency_csv(path, flow)

    with open(path, newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows == [
        ["topic", "count", "min_ns", "mean_ns", "median_ns", "p99_ns", "max_ns"]
    ]


def test_write_python_zone_csv(tmp_path):
    analysis, _flow = _build(_one_step_events())
    path = str(tmp_path / "python_zones.csv")

    write_python_zone_csv(path, analysis)

    with open(path, newline="") as fh:
        rows = {row["zone_name"]: row for row in csv.DictReader(fh)}
    assert int(rows[ON_MESSAGE_ZONE]["count"]) == 1


def test_write_callback_summary_csv(tmp_path):
    analysis, flow = _build(_timer_fixture_events())
    path = str(tmp_path / "callback_summary.csv")

    write_callback_summary_csv(path, flow)

    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    row = rows[0]
    assert row["node"] == f"/{TIMER_NODE_NAME}"
    assert row["callback_type"] == "timer"
    assert int(row["invocation_count"]) == 2


def _only_timer_timing(timing_by_key):
    timer_entries = [
        timing for key, timing in timing_by_key.items() if key[1] == "timer"
    ]
    assert len(timer_entries) == 1
    return timer_entries[0]


def test_callback_timing_stats_reports_interarrival_and_gap():
    analysis, flow = _build(_timer_fixture_events())

    timing = _only_timer_timing(callback_timing_stats(flow.callbacks))

    assert timing.interarrival_ns.count == 1
    assert timing.interarrival_ns.p50 == TIMER_PERIOD_NS

    assert timing.inter_callback_gap_ns.count == 1
    assert timing.inter_callback_gap_ns.p50 == TIMER_PERIOD_NS - 1000

    assert timing.instantaneous_wait_count == 0


def test_callback_timing_stats_counts_instantaneous_waits_below_threshold():
    analysis, flow = _build(_timer_fixture_events_with_tight_gap())

    timing = _only_timer_timing(callback_timing_stats(flow.callbacks))

    assert timing.inter_callback_gap_ns.p50 == 500
    assert INSTANTANEOUS_WAIT_THRESHOLD_NS > 500
    assert timing.instantaneous_wait_count == 1
