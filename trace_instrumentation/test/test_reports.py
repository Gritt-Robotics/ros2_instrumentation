# Copyright 2026 Gritt Robotics Inc.

from synthetic_events import (
    make_event,
    publish_events,
    publisher_init_event,
)
from trace_instrumentation.trace_analysis.analysis import TraceAnalysis
from trace_instrumentation.trace_analysis.ctf.reader import Metadata
from trace_instrumentation.trace_analysis.event_names import EVT_RCL_NODE_INIT
from trace_instrumentation.trace_analysis.flow_graph import FlowGraph
from trace_instrumentation.trace_analysis.reports.report import build_node_report
from trace_instrumentation.trace_analysis.reports.schema import (
    KEY_THROUGHPUT,
    validate_trace,
)

PRODUCER_VPID = 200
PRODUCER_VTID = 20
PRODUCER_NODE_HANDLE = 900
PRODUCER_NODE_NAME = "producer"
PRODUCER_PUB_HANDLE = 1
TOPIC_A = "/topic_a"
NS_PER_S = 1_000_000_000


def _build(events):
    meta = Metadata()
    analysis = TraceAnalysis(meta, events).build()
    flow = FlowGraph(meta, events, analysis).build()
    return analysis, flow


def _node_init_event(ts_ns, vpid, vtid, node_handle, name):
    # Mirrors test_metrics.py's local helper (synthetic_events has no node-init builder).
    return make_event(
        EVT_RCL_NODE_INIT,
        ts_ns,
        vpid,
        vtid,
        {
            "node_handle": node_handle,
            "node_name": name,
            "namespace": "/",
            "rmw_handle": 0,
        },
    )


def _producer_events():
    events = [
        _node_init_event(
            0, PRODUCER_VPID, PRODUCER_VTID, PRODUCER_NODE_HANDLE, PRODUCER_NODE_NAME
        ),
        publisher_init_event(
            0,
            PRODUCER_VPID,
            PRODUCER_VTID,
            PRODUCER_PUB_HANDLE,
            PRODUCER_NODE_HANDLE,
            901,
            TOPIC_A,
        ),
    ]
    for i, bucket in enumerate((0, 1)):
        events += publish_events(
            bucket * NS_PER_S,
            PRODUCER_VPID,
            PRODUCER_VTID,
            100 + i,
            PRODUCER_PUB_HANDLE,
            5000 + i,
        )
    return events


def test_build_node_report_includes_throughput_block_with_hz_rates():
    analysis, flow = _build(_producer_events())
    report = build_node_report(flow, analysis)
    assert KEY_THROUGHPUT in report
    tp = report[KEY_THROUGHPUT]
    assert tp["bucket_interval_s"] == 1.0
    assert tp["bucket_starts_s"] == [0.0, 1.0]
    # 1 publish per 1.0s bucket == 1.0 Hz.
    assert tp["total_hz"] == [1.0, 1.0]
    assert tp["per_topic_hz"][TOPIC_A] == [1.0, 1.0]


def test_build_node_report_throughput_hz_scales_with_interval():
    analysis, flow = _build(_producer_events())
    report = build_node_report(flow, analysis, throughput_interval_s=2.0)
    tp = report[KEY_THROUGHPUT]
    # Both publishes (t=0s, t=1s) fall in one 2.0s bucket -> 2 counts / 2.0s == 1.0 Hz.
    assert tp["bucket_starts_s"] == [0.0]
    assert tp["total_hz"] == [1.0]
    assert tp["per_topic_hz"][TOPIC_A] == [1.0]


def test_build_node_report_output_validates_against_report_schema():
    # Pins build_node_report()'s output to report_schema.py's contract so the two
    # can't silently drift apart.
    analysis, flow = _build(_producer_events())
    report = build_node_report(flow, analysis)
    validate_trace(report)
