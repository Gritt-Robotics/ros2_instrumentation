# Copyright 2026 Gritt Robotics Inc.

import json
from pathlib import Path

from trace_instrumentation.trace_analysis.reports.model import (
    ChainEndpoint,
    ChainStepRow,
    Metric,
    build_edges,
    build_report,
    callback_rows,
    chain_rows,
    edge_rows,
    publisher_rows,
    topo_order,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_trace.json"


def _load_fixture() -> dict:
    with open(FIXTURE_PATH) as fh:
        return json.load(fh)


def test_metric_from_dict_maps_median_to_p50():
    metric = Metric.from_dict({
        "count": 5,
        "min": 1.0,
        "mean": 2.0,
        "median": 2.5,
        "p90": 3.5,
        "p99": 4.0,
        "max": 5.0,
    })
    assert metric.p50 == 2.5
    assert metric.p90 == 3.5
    assert metric.count == 5


def test_metric_from_dict_none_is_none():
    assert Metric.from_dict(None) is None


def test_metric_from_dict_tolerates_missing_p90():
    metric = Metric.from_dict({
        "count": 1,
        "min": 1.0,
        "mean": 1.0,
        "median": 1.0,
        "p99": 1.0,
        "max": 1.0,
    })
    assert metric.p90 is None


def test_metric_from_dict_defaults_values_to_empty_list():
    metric = Metric.from_dict({
        "count": 1,
        "min": 1.0,
        "mean": 1.0,
        "median": 1.0,
        "p99": 1.0,
        "max": 1.0,
    })
    assert metric.values == []
    assert metric.significant is None


def test_metric_from_dict_parses_values():
    metric = Metric.from_dict({
        "count": 2,
        "min": 1.0,
        "mean": 1.5,
        "median": 1.5,
        "p99": 2.0,
        "max": 2.0,
        "values": [1.0, 2.0],
    })
    assert metric.values == [1.0, 2.0]


def test_build_edges_derives_from_node_adjacency():
    trace = _load_fixture()
    edges = build_edges(trace)
    assert len(edges) == 1
    assert edges[0].from_node == "/producer"
    assert edges[0].to_node == "/subscriber"
    assert edges[0].topic == "/step0"
    assert edges[0].publisher is None
    assert edges[0].callback is None


def test_topo_order_puts_producer_before_subscriber():
    trace = _load_fixture()
    order = topo_order(trace)
    assert order.index("/producer") < order.index("/subscriber")


def test_callback_rows_covers_every_node_in_topo_order():
    trace = _load_fixture()
    rows = callback_rows(trace)
    nodes_seen = [row.node for row in rows]
    assert nodes_seen == ["/producer", "/subscriber"]


def test_callback_row_carries_interarrival_and_gap():
    trace = _load_fixture()
    rows = callback_rows(trace)
    sub_row = next(r for r in rows if r.node == "/subscriber")
    assert sub_row.interarrival.p50 == 100.0
    assert sub_row.inter_callback_gap.p50 == 99.5
    assert sub_row.instantaneous_wait_count == 0


def test_callback_row_topic_latency_is_none_for_timer():
    trace = _load_fixture()
    rows = callback_rows(trace)
    producer_row = next(r for r in rows if r.node == "/producer")
    assert producer_row.topic_latency is None


def test_publisher_row_carries_dropped_count_and_pct():
    trace = _load_fixture()
    rows = publisher_rows(trace)
    row = next(r for r in rows if r.node == "/producer")
    assert row.dropped_count == 4
    assert row.dropped_pct == 4.0


def test_publisher_row_dropped_is_none_when_absent():
    trace = _load_fixture()
    del trace["nodes"]["/producer"]["publishers"]["/step0"]["dropped"]
    rows = publisher_rows(trace)
    row = next(r for r in rows if r.node == "/producer")
    assert row.dropped_count is None
    assert row.dropped_pct is None


def test_publisher_row_carries_published_and_received_counts():
    trace = _load_fixture()
    rows = publisher_rows(trace)
    row = next(r for r in rows if r.node == "/producer")
    assert row.published_count == 100
    assert row.received_count == 96
    # Rcv is matched takes, so it can never exceed Pub.
    assert row.received_count <= row.published_count


def test_edge_rows_joins_publisher_and_callback_by_topic():
    trace = _load_fixture()
    rows = edge_rows(trace)
    assert len(rows) == 1
    row = rows[0]
    assert row.from_node == "/producer"
    assert row.to_node == "/subscriber"
    assert row.topic == "/step0"
    assert row.publisher.dropped_count == 4
    assert row.callback.callback_type == "subscription"


def test_chain_rows_parses_indirect_wait_when_present():
    trace = _load_fixture()
    trace["pipeline_chain_latency"][0]["steps"][0]["indirect_wait_ms"] = {
        "count": 1,
        "min": 5.0,
        "mean": 5.0,
        "median": 5.0,
        "p99": 5.0,
        "max": 5.0,
    }
    chains = chain_rows(trace)
    assert chains[0].steps[0].indirect_wait.p50 == 5.0


def test_chain_rows_parses_is_indirect():
    trace = _load_fixture()
    assert chain_rows(trace)[0].steps[0].is_indirect is False


def test_chain_rows_parses_each_chains_terminator():
    trace = _load_fixture()
    chains = chain_rows(trace)
    assert [chain.terminator for chain in chains] == ["take", "pub"]


def test_chain_rows_keeps_a_publish_terminated_last_step_without_a_subscriber():
    trace = _load_fixture()
    pub_terminated = chain_rows(trace)[1]
    assert pub_terminated.steps[-1].subscribing_node is None
    assert pub_terminated.steps[-1].topic_latency is None
    assert pub_terminated.steps[-1].take_to_callback_start is None
    assert pub_terminated.latency.p50 == 12.4


def test_chain_rows_parses_take_to_callback_start():
    trace = _load_fixture()
    chains = chain_rows(trace)
    assert chains[0].steps[0].take_to_callback_start.p50 == 0.02


def test_chain_step_row_from_dict_parses_endpoints_and_metric():
    row = ChainStepRow.from_dict({
        "from": {"topic": "/step0", "kind": "publish", "node": "/producer"},
        "to": {"topic": "/step0", "kind": "take", "node": "/subscriber"},
        "metric_ms": {
            "count": 1,
            "min": 1.0,
            "mean": 1.0,
            "median": 1.0,
            "p99": 1.0,
            "max": 1.0,
        },
    })
    assert row.from_endpoint == ChainEndpoint(
        topic="/step0", kind="publish", node="/producer"
    )
    assert row.to_endpoint == ChainEndpoint(
        topic="/step0", kind="take", node="/subscriber"
    )
    assert row.metric.p50 == 1.0


def test_chain_step_row_from_dict_tolerates_null_metric():
    row = ChainStepRow.from_dict({
        "from": {"topic": "/step1", "kind": "publish", "node": "/relay"},
        "to": {"topic": "/step1", "kind": "take", "node": None},
        "metric_ms": None,
    })
    assert row.metric is None
    assert row.to_endpoint.node is None


def test_chain_rows_parses_step_endpoints():
    trace = _load_fixture()
    chains = chain_rows(trace)
    assert [row.from_endpoint.kind for row in chains[0].step_endpoints] == [
        "publish",
        "take",
        "callback start",
    ]
    assert chains[0].step_endpoints[0].metric.p50 == 8.7


def test_build_report_assembles_everything():
    trace = _load_fixture()
    report = build_report(trace, title="Test Trace")
    assert report.title == "Test Trace"
    assert report.duration_s == 10.0
    assert report.node_names == ["/producer", "/subscriber"]
    assert len(report.edges) == 1
    assert len(report.callbacks) == 2
    assert len(report.publishers) == 1
    assert len(report.chains) == 2
    assert report.edges[0].from_node == "/producer"
    assert report.edges[0].publisher.dropped_count == 4
    assert report.edges[0].callback.callback_type == "subscription"


def test_build_report_parses_throughput_when_present():
    trace = {
        "duration_s": 2.0,
        "nodes": {},
        "throughput": {
            "bucket_interval_s": 1.0,
            "bucket_starts_s": [0.0, 1.0],
            "total_hz": [2.0, 1.0],
            "per_topic_hz": {"/a": [2.0, 1.0]},
        },
    }
    report = build_report(trace)
    assert report.throughput is not None
    assert report.throughput.bucket_starts_s == [0.0, 1.0]
    assert report.throughput.per_topic_hz["/a"] == [2.0, 1.0]


def test_build_report_throughput_is_none_when_absent():
    report = build_report({"duration_s": 2.0, "nodes": {}})
    assert report.throughput is None
