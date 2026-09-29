# Copyright 2026 Gritt Robotics Inc.

import csv

from synthetic_events import (
    make_event,
    node_init_event,
    publish_events,
    publisher_init_event,
)
from trace_instrumentation.trace_analysis.analysis import TraceAnalysis
from trace_instrumentation.trace_analysis.ctf.reader import Metadata
from trace_instrumentation.trace_analysis.event_fields import (
    FIELD_MESSAGE,
    FIELD_TIMESTAMP,
)
from trace_instrumentation.trace_analysis.event_names import (
    EVT_RCLCPP_PUBLISH,
    EVT_RMW_PUBLISH,
)
from trace_instrumentation.trace_analysis.flow_graph import FlowGraph
from trace_instrumentation.trace_analysis.metrics.frequency import (
    BUCKET_INTERVAL_KEY,
    CUMULATIVE_TOPIC_KEY,
    DURATION_KEY,
    MEAN_HZ_KEY,
    TOPIC_KEY,
    TOTAL_KEY,
    _topic_publish_hz,  # noqa: PLC2701
    write_topic_frequency_csv,
)

VPID = 1
VTID = 1
NODE_HANDLE = 900
# A publisher endpoint resolves -- and so carries a topic name -- only once its owning
# node is registered, so every scenario expecting a named topic registers the node.
NODE_NAME = "publisher_node"

TOPIC_A = "/topic_a"
TOPIC_A_PUBLISHER_HANDLE = 1
TOPIC_A_RMW_PUBLISHER_HANDLE = 11

TOPIC_B = "/topic_b"
TOPIC_B_PUBLISHER_HANDLE = 2
TOPIC_B_RMW_PUBLISHER_HANDLE = 12

UNREGISTERED_PUBLISHER_HANDLE = 99


def _build(events):
    meta = Metadata()
    analysis = TraceAnalysis(meta, events).build()
    return FlowGraph(meta, events, analysis).build()


def _publish_without_publisher_handle(ts_ns, message):
    # rclcpp_publish + rmw_publish only (skip rcl_publish), so publisher_handle is
    # never captured -- both topic and publisher_handle stay unresolved.
    return [
        make_event(EVT_RCLCPP_PUBLISH, ts_ns, VPID, VTID, {FIELD_MESSAGE: message}),
        make_event(
            EVT_RMW_PUBLISH,
            ts_ns + 10,
            VPID,
            VTID,
            {FIELD_MESSAGE: message, FIELD_TIMESTAMP: 0},
        ),
    ]


def test_write_topic_frequency_csv_raises_for_non_positive_interval(tmp_path):
    flow = _build([])
    try:
        write_topic_frequency_csv(str(tmp_path / "report"), flow, interval_seconds=0)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_write_topic_frequency_csv_writes_frequency_and_throughput_files(tmp_path):
    events = [
        node_init_event(0, VPID, VTID, NODE_HANDLE, NODE_NAME),
        publisher_init_event(
            0,
            VPID,
            VTID,
            TOPIC_A_PUBLISHER_HANDLE,
            NODE_HANDLE,
            TOPIC_A_RMW_PUBLISHER_HANDLE,
            TOPIC_A,
        ),
        publisher_init_event(
            0,
            VPID,
            VTID,
            TOPIC_B_PUBLISHER_HANDLE,
            NODE_HANDLE,
            TOPIC_B_RMW_PUBLISHER_HANDLE,
            TOPIC_B,
        ),
        *publish_events(0, VPID, VTID, 100, TOPIC_A_PUBLISHER_HANDLE, 0),
        *publish_events(10, VPID, VTID, 101, TOPIC_A_PUBLISHER_HANDLE, 0),
        *publish_events(20, VPID, VTID, 102, TOPIC_A_PUBLISHER_HANDLE, 0),
        *publish_events(0, VPID, VTID, 200, TOPIC_B_PUBLISHER_HANDLE, 0),
        *publish_events(10, VPID, VTID, 201, TOPIC_B_PUBLISHER_HANDLE, 0),
    ]
    flow = _build(events)
    prefix = str(tmp_path / "report")

    write_topic_frequency_csv(prefix, flow)

    with open(f"{prefix}_frequency.csv", newline="") as fh:
        rows = {row["topic"]: row for row in csv.DictReader(fh)}
    assert rows[TOPIC_A][TOTAL_KEY] == "3"
    assert rows[TOPIC_B][TOTAL_KEY] == "2"

    with open(f"{prefix}_throughput.csv", newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == [
            TOPIC_A,
            TOPIC_B,
            BUCKET_INTERVAL_KEY,
            CUMULATIVE_TOPIC_KEY,
        ]
        rows = list(reader)
    assert len(rows) == 1, "all publishes land in the same one-second bucket"
    assert rows[0][CUMULATIVE_TOPIC_KEY] == "5"
    assert rows[0][TOPIC_A] == "3"
    assert rows[0][TOPIC_B] == "2"


def test_write_topic_frequency_csv_throughput_reports_per_bucket_counts(tmp_path):
    # Regression test: every occupied bucket used to echo a topic's grand total
    # publish count instead of that bucket's own count.
    events = [
        node_init_event(0, VPID, VTID, NODE_HANDLE, NODE_NAME),
        publisher_init_event(
            0,
            VPID,
            VTID,
            TOPIC_A_PUBLISHER_HANDLE,
            NODE_HANDLE,
            TOPIC_A_RMW_PUBLISHER_HANDLE,
            TOPIC_A,
        ),
        publisher_init_event(
            0,
            VPID,
            VTID,
            TOPIC_B_PUBLISHER_HANDLE,
            NODE_HANDLE,
            TOPIC_B_RMW_PUBLISHER_HANDLE,
            TOPIC_B,
        ),
        # TOPIC_A: 3 publishes in bucket 0, 1 in bucket 1 (total 4).
        *publish_events(0, VPID, VTID, 100, TOPIC_A_PUBLISHER_HANDLE, 0),
        *publish_events(10, VPID, VTID, 101, TOPIC_A_PUBLISHER_HANDLE, 0),
        *publish_events(20, VPID, VTID, 102, TOPIC_A_PUBLISHER_HANDLE, 0),
        *publish_events(1_000_000_000, VPID, VTID, 103, TOPIC_A_PUBLISHER_HANDLE, 0),
        # TOPIC_B: 1 publish in bucket 0, 2 in bucket 1 (total 3).
        *publish_events(0, VPID, VTID, 200, TOPIC_B_PUBLISHER_HANDLE, 0),
        *publish_events(1_000_000_000, VPID, VTID, 201, TOPIC_B_PUBLISHER_HANDLE, 0),
        *publish_events(1_000_000_010, VPID, VTID, 202, TOPIC_B_PUBLISHER_HANDLE, 0),
    ]
    flow = _build(events)
    prefix = str(tmp_path / "report")

    write_topic_frequency_csv(prefix, flow, interval_seconds=1.0)

    with open(f"{prefix}_throughput.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2
    assert rows[0][TOPIC_A] == "3"
    assert rows[0][TOPIC_B] == "1"
    assert rows[1][TOPIC_A] == "1"
    assert rows[1][TOPIC_B] == "2"


def test_write_topic_frequency_csv_computes_duration_and_mean_hz_across_occupied_buckets(
    tmp_path,
):
    # Timestamps 0 and 2_500_000_000 (plus the fixed rcl/rmw publish-triplet offsets)
    # span 3 one-second buckets (0, 1, 2) -> duration_s=3.0, mean_hz=2/3.
    events = [
        node_init_event(0, VPID, VTID, NODE_HANDLE, NODE_NAME),
        publisher_init_event(
            0,
            VPID,
            VTID,
            TOPIC_A_PUBLISHER_HANDLE,
            NODE_HANDLE,
            TOPIC_A_RMW_PUBLISHER_HANDLE,
            TOPIC_A,
        ),
        *publish_events(0, VPID, VTID, 100, TOPIC_A_PUBLISHER_HANDLE, 0),
        *publish_events(2_500_000_000, VPID, VTID, 101, TOPIC_A_PUBLISHER_HANDLE, 0),
    ]
    flow = _build(events)
    prefix = str(tmp_path / "report")

    write_topic_frequency_csv(prefix, flow, interval_seconds=1.0)

    with open(f"{prefix}_frequency.csv", newline="") as fh:
        row = next(csv.DictReader(fh))
    assert row[TOTAL_KEY] == "2"
    assert row[DURATION_KEY] == "3.00"
    assert row[MEAN_HZ_KEY] == f"{2.0 / 3.0:.2f}"


def test_write_topic_frequency_csv_writes_header_only_files_when_no_publishes(
    tmp_path,
):
    flow = _build([])
    prefix = str(tmp_path / "report")

    write_topic_frequency_csv(prefix, flow)

    with open(f"{prefix}_frequency.csv", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows == [[TOPIC_KEY, TOTAL_KEY, DURATION_KEY, MEAN_HZ_KEY]]

    with open(f"{prefix}_throughput.csv", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows == [[BUCKET_INTERVAL_KEY, CUMULATIVE_TOPIC_KEY]]


def test_topic_publish_hz_uses_full_trace_duration_not_topic_span():
    # Two publishes ~1us apart: dividing by this topic's own span instead of the
    # caller-supplied full-trace duration_s would inflate the rate enormously.
    events = [
        node_init_event(0, VPID, VTID, NODE_HANDLE, NODE_NAME),
        publisher_init_event(
            0,
            VPID,
            VTID,
            TOPIC_A_PUBLISHER_HANDLE,
            NODE_HANDLE,
            TOPIC_A_RMW_PUBLISHER_HANDLE,
            TOPIC_A,
        ),
        *publish_events(0, VPID, VTID, 100, TOPIC_A_PUBLISHER_HANDLE, 0),
        *publish_events(1000, VPID, VTID, 101, TOPIC_A_PUBLISHER_HANDLE, 0),
    ]
    flow = _build(events)

    hz_by_topic = _topic_publish_hz(flow, duration_s=10.0)

    assert hz_by_topic[TOPIC_A] == 2 / 10.0
