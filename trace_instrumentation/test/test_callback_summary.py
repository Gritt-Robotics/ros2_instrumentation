# Copyright 2026 Gritt Robotics Inc.

from synthetic_events import (
    callback_end_event,
    callback_start_event,
    make_event,
    python_zone_event,
    rmw_take_event,
    subscription_init_event,
    zone_declaration_event,
)
from trace_instrumentation.trace_analysis.analysis import TraceAnalysis
from trace_instrumentation.trace_analysis.ctf.reader import Metadata
from trace_instrumentation.trace_analysis.event_fields import (
    FIELD_CALLBACK,
    FIELD_NAMESPACE,
    FIELD_NODE_HANDLE,
    FIELD_NODE_NAME,
    FIELD_PERIOD,
    FIELD_RMW_HANDLE,
    FIELD_TIMER_HANDLE,
)
from trace_instrumentation.trace_analysis.event_names import (
    EVT_RCL_NODE_INIT,
    EVT_RCL_TIMER_INIT,
    EVT_RCLCPP_TIMER_CALLBACK_ADDED,
    EVT_RCLCPP_TIMER_LINK_NODE,
)
from trace_instrumentation.trace_analysis.flow_graph import FlowGraph
from trace_instrumentation.trace_analysis.graph import CB_KIND_ZONE
from trace_instrumentation.trace_analysis.metrics.latency import (
    callback_summary_rows,
)
from trace_instrumentation.trace_analysis.metrics.stats import fmt_hex_or_str

# Zone-declaration payload, as the instrumentation emits it at registration time. An
# undeclared zone stays CB_KIND_ZONE, so a Python subscription callback must declare it.
DECL_SUB_PAYLOAD = "trace_decl sub zone={zone_name} topic={topic}"

# root namespace, and an rmw handle the identity path never reads
NODE_NAMESPACE = "/"
UNUSED_RMW_HANDLE = 0

RELAY_VPID = 100
RELAY_VTID = 10
RELAY_NODE_HANDLE = 910
RELAY_NODE_NAME = "relay"

TIMER_VPID = 200
TIMER_VTID = 20
TIMER_NODE_HANDLE = 920
TIMER_NODE_NAME = "timer_node"
TIMER_HANDLE = 30
TIMER_CALLBACK_PTR = 31
TIMER_PERIOD_NS = 100_000_000
SECOND_TIMER_HANDLE = 40
SECOND_TIMER_CALLBACK_PTR = 41
SECOND_TIMER_PERIOD_NS = 200_000_000

STEP0_TOPIC = "/step0"
SUB_ZONE = "_on_message"
TIMER_ZONE = "_tick"
NESTED_ZONE = "_inner_helper"


def _node_init_event(ts_ns, vpid, vtid, node_handle, name):
    return make_event(
        EVT_RCL_NODE_INIT,
        ts_ns,
        vpid,
        vtid,
        {
            FIELD_NODE_HANDLE: node_handle,
            FIELD_NODE_NAME: name,
            FIELD_NAMESPACE: NODE_NAMESPACE,
            FIELD_RMW_HANDLE: UNUSED_RMW_HANDLE,
        },
    )


def _timer_init_event(ts_ns, vpid, vtid, timer_handle, period):
    return make_event(
        EVT_RCL_TIMER_INIT,
        ts_ns,
        vpid,
        vtid,
        {FIELD_TIMER_HANDLE: timer_handle, FIELD_PERIOD: period},
    )


def _timer_link_node_event(ts_ns, vpid, vtid, timer_handle, node_handle):
    return make_event(
        EVT_RCLCPP_TIMER_LINK_NODE,
        ts_ns,
        vpid,
        vtid,
        {FIELD_TIMER_HANDLE: timer_handle, FIELD_NODE_HANDLE: node_handle},
    )


def _timer_callback_added_event(ts_ns, vpid, vtid, timer_handle, callback):
    return make_event(
        EVT_RCLCPP_TIMER_CALLBACK_ADDED,
        ts_ns,
        vpid,
        vtid,
        {FIELD_TIMER_HANDLE: timer_handle, FIELD_CALLBACK: callback},
    )


def _build(events):
    meta = Metadata()
    analysis = TraceAnalysis(meta, events).build()
    return FlowGraph(meta, events, analysis).build()


def _timer_events(ts_ns, vpid, vtid, timer_handle, node_handle, callback, period):
    return [
        _timer_init_event(ts_ns, vpid, vtid, timer_handle, period),
        _timer_link_node_event(ts_ns, vpid, vtid, timer_handle, node_handle),
        _timer_callback_added_event(ts_ns, vpid, vtid, timer_handle, callback),
    ]


def test_callback_summary_rows_has_documented_keys_and_correct_counts():
    events = [
        _node_init_event(0, TIMER_VPID, TIMER_VTID, TIMER_NODE_HANDLE, TIMER_NODE_NAME),
        *_timer_events(
            0,
            TIMER_VPID,
            TIMER_VTID,
            TIMER_HANDLE,
            TIMER_NODE_HANDLE,
            TIMER_CALLBACK_PTR,
            TIMER_PERIOD_NS,
        ),
        callback_start_event(1000, TIMER_VPID, TIMER_VTID, TIMER_CALLBACK_PTR),
        callback_end_event(2000, TIMER_VPID, TIMER_VTID, TIMER_CALLBACK_PTR),
        callback_start_event(3000, TIMER_VPID, TIMER_VTID, TIMER_CALLBACK_PTR),
        callback_end_event(6000, TIMER_VPID, TIMER_VTID, TIMER_CALLBACK_PTR),
    ]
    flow = _build(events)

    rows = callback_summary_rows(flow)
    assert len(rows) == 1
    row = rows[0]
    assert row._fields == (
        "node",
        "callback_type",
        "topic",
        "handler",
        "timer_period_ns",
        "invocation_count",
        "intra_count",
        "inter_count",
        "min_ns",
        "mean_ns",
        "median_ns",
        "p99_ns",
        "max_ns",
    )
    assert row.node == f"/{TIMER_NODE_NAME}"
    assert row.callback_type == "timer"
    # No EVT_RCLCPP_CALLBACK_REGISTER here, so the label falls back to the timer handle's
    # hex -- still stable and non-empty, so it distinguishes timers on the same node.
    assert row.topic == fmt_hex_or_str(TIMER_HANDLE)
    assert row.timer_period_ns == TIMER_PERIOD_NS
    assert row.invocation_count == 2
    assert row.intra_count == 0
    assert row.inter_count == 2
    assert row.min_ns == 1000
    assert row.max_ns == 3000
    assert row.mean_ns == 2000


def test_callback_summary_rows_keeps_distinct_timers_on_same_node_separate():
    # Two timers on one node must not collapse into a single row, so each needs its own
    # stable label -- here the timer handle's hex, neither callback having a symbol.
    events = [
        _node_init_event(0, TIMER_VPID, TIMER_VTID, TIMER_NODE_HANDLE, TIMER_NODE_NAME),
        *_timer_events(
            0,
            TIMER_VPID,
            TIMER_VTID,
            TIMER_HANDLE,
            TIMER_NODE_HANDLE,
            TIMER_CALLBACK_PTR,
            TIMER_PERIOD_NS,
        ),
        *_timer_events(
            0,
            TIMER_VPID,
            TIMER_VTID,
            SECOND_TIMER_HANDLE,
            TIMER_NODE_HANDLE,
            SECOND_TIMER_CALLBACK_PTR,
            SECOND_TIMER_PERIOD_NS,
        ),
        callback_start_event(1000, TIMER_VPID, TIMER_VTID, TIMER_CALLBACK_PTR),
        callback_end_event(2000, TIMER_VPID, TIMER_VTID, TIMER_CALLBACK_PTR),
        callback_start_event(1000, TIMER_VPID, TIMER_VTID, SECOND_TIMER_CALLBACK_PTR),
        callback_end_event(3000, TIMER_VPID, TIMER_VTID, SECOND_TIMER_CALLBACK_PTR),
    ]
    flow = _build(events)

    rows = callback_summary_rows(flow)
    timer_rows = [r for r in rows if r.callback_type == "timer"]
    assert len(timer_rows) == 2
    labels = {r.topic for r in timer_rows}
    assert labels == {fmt_hex_or_str(TIMER_HANDLE), fmt_hex_or_str(SECOND_TIMER_HANDLE)}
    periods = {r.topic: r.timer_period_ns for r in timer_rows}
    assert periods[fmt_hex_or_str(TIMER_HANDLE)] == TIMER_PERIOD_NS
    assert periods[fmt_hex_or_str(SECOND_TIMER_HANDLE)] == SECOND_TIMER_PERIOD_NS


def test_callback_summary_rows_does_not_double_count_nested_python_zones():
    # TraceAnalysis records every begin/end pair including the nested helper, but only
    # the outer zone synthesizes a CB, so the nested one must not count as a 2nd call.
    events = [
        _node_init_event(0, RELAY_VPID, RELAY_VTID, RELAY_NODE_HANDLE, RELAY_NODE_NAME),
        subscription_init_event(
            0, RELAY_VPID, RELAY_VTID, 10, RELAY_NODE_HANDLE, 11, STEP0_TOPIC
        ),
        zone_declaration_event(
            0,
            RELAY_VPID,
            RELAY_VTID,
            DECL_SUB_PAYLOAD.format(zone_name=SUB_ZONE, topic=STEP0_TOPIC),
        ),
        rmw_take_event(1000, RELAY_VPID, RELAY_VTID, 222, 11, source_timestamp=5000),
        python_zone_event(1100, RELAY_VPID, RELAY_VTID, SUB_ZONE, "begin"),
        python_zone_event(1150, RELAY_VPID, RELAY_VTID, NESTED_ZONE, "begin"),
        python_zone_event(1300, RELAY_VPID, RELAY_VTID, NESTED_ZONE, "end"),
        python_zone_event(1400, RELAY_VPID, RELAY_VTID, SUB_ZONE, "end"),
    ]
    flow = _build(events)

    assert len(flow.trace_analysis.python_zones) == 2, (
        "outer + nested zone both recorded"
    )
    assert len(flow.callbacks) == 1, "only the outer zone synthesizes a CB"

    rows = callback_summary_rows(flow)
    assert len(rows) == 1
    row = rows[0]
    assert row.node == f"/{RELAY_NODE_NAME}"
    assert row.callback_type == "subscription"
    assert row.invocation_count == 1


def test_callback_summary_rows_never_infers_a_timer_from_an_undeclared_zone():
    # Absence over inference: a recurring zone on a vpid that owns timers looks exactly
    # like a timer, but nothing in the trace says it is one, so it stays a plain zone.
    events = [
        _node_init_event(0, TIMER_VPID, TIMER_VTID, TIMER_NODE_HANDLE, TIMER_NODE_NAME),
        *_timer_events(
            0,
            TIMER_VPID,
            TIMER_VTID,
            TIMER_HANDLE,
            TIMER_NODE_HANDLE,
            TIMER_CALLBACK_PTR,
            TIMER_PERIOD_NS,
        ),
        *_timer_events(
            0,
            TIMER_VPID,
            TIMER_VTID,
            SECOND_TIMER_HANDLE,
            TIMER_NODE_HANDLE,
            SECOND_TIMER_CALLBACK_PTR,
            SECOND_TIMER_PERIOD_NS,
        ),
        python_zone_event(1000, TIMER_VPID, TIMER_VTID, TIMER_ZONE, "begin"),
        python_zone_event(1100, TIMER_VPID, TIMER_VTID, TIMER_ZONE, "end"),
        python_zone_event(2000, TIMER_VPID, TIMER_VTID, TIMER_ZONE, "begin"),
        python_zone_event(2100, TIMER_VPID, TIMER_VTID, TIMER_ZONE, "end"),
    ]
    flow = _build(events)

    rows = callback_summary_rows(flow)
    assert [r for r in rows if r.callback_type == "timer"] == []
    zone_rows = [r for r in rows if r.callback_type == CB_KIND_ZONE]
    assert len(zone_rows) == 1
    assert zone_rows[0].topic == TIMER_ZONE
    assert zone_rows[0].timer_period_ns is None
    assert zone_rows[0].invocation_count == 2
