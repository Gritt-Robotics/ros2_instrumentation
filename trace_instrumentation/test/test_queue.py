# Copyright 2026 Gritt Robotics Inc.

import csv

from synthetic_events import (
    node_init_event,
    publish_events,
    publisher_init_event,
    rmw_take_event,
    subscription_init_event,
)
from trace_instrumentation.trace_analysis.analysis import TraceAnalysis
from trace_instrumentation.trace_analysis.ctf.reader import Metadata
from trace_instrumentation.trace_analysis.flow_graph import FlowGraph
from trace_instrumentation.trace_analysis.graph import QueueHealthStats
from trace_instrumentation.trace_analysis.metrics.queue import (
    NO_SUBSCRIBER_LABEL,
    SUB_NODE_COLUMN,
    TOPIC_COLUMN,
    write_queue_health_csv,
)

VPID = 1
VTID = 1
NODE_HANDLE = 900
# Endpoints resolve to a topic name only once their owning node is registered.
NODE_NAME = "pubsub_node"

TOPIC_A = "/topic_a"
TOPIC_A_PUBLISHER_HANDLE = 1
TOPIC_A_RMW_PUBLISHER_HANDLE = 11
TOPIC_A_SUBSCRIPTION_HANDLE = 21
TOPIC_A_RMW_SUBSCRIPTION_HANDLE = 31

TOPIC_B = "/topic_b"
TOPIC_B_PUBLISHER_HANDLE = 2
TOPIC_B_RMW_PUBLISHER_HANDLE = 12

(
    TOTAL_PUBLISHED_KEY,
    DELIVERED_KEY,
    UNDELIVERED_KEY,
    UNDELIVERED_PCT_KEY,
    DELIVERY_RATIO_KEY,
) = QueueHealthStats._fields


def _build(events):
    meta = Metadata()
    analysis = TraceAnalysis(meta, events).build()
    return FlowGraph(meta, events, analysis).build()


def test_write_queue_health_csv_reports_delivered_and_undelivered_counts(tmp_path):
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
        subscription_init_event(
            0,
            VPID,
            VTID,
            TOPIC_A_SUBSCRIPTION_HANDLE,
            NODE_HANDLE,
            TOPIC_A_RMW_SUBSCRIPTION_HANDLE,
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
        # topic_a: one delivered publish (a take lands on its source_timestamp) ...
        *publish_events(0, VPID, VTID, 100, TOPIC_A_PUBLISHER_HANDLE, 5000),
        rmw_take_event(10, VPID, VTID, 100, TOPIC_A_RMW_SUBSCRIPTION_HANDLE, 5000),
        # ... and one undelivered publish (no take matches its source_timestamp).
        *publish_events(100, VPID, VTID, 101, TOPIC_A_PUBLISHER_HANDLE, 6000),
        # topic_b is never subscribed to, so both its publishes are undelivered.
        *publish_events(0, VPID, VTID, 200, TOPIC_B_PUBLISHER_HANDLE, 7000),
        *publish_events(100, VPID, VTID, 201, TOPIC_B_PUBLISHER_HANDLE, 8000),
    ]
    flow = _build(events)
    path = str(tmp_path / "queue_health.csv")

    write_queue_health_csv(path, flow)

    with open(path, newline="") as fh:
        rows = {
            (row[TOPIC_COLUMN], row[SUB_NODE_COLUMN]): row for row in csv.DictReader(fh)
        }

    # topic_a has one subscriber node, keyed by that node's name.
    topic_a_row = rows[(TOPIC_A, NODE_NAME)]
    assert topic_a_row[TOTAL_PUBLISHED_KEY] == "2"
    assert topic_a_row[DELIVERED_KEY] == "1"
    assert topic_a_row[UNDELIVERED_KEY] == "1"
    assert topic_a_row[UNDELIVERED_PCT_KEY] == "50.00"
    assert topic_a_row[DELIVERY_RATIO_KEY] == "0.500"

    # topic_b has no subscribers, so it gets the sentinel no-subscriber row.
    topic_b_row = rows[(TOPIC_B, NO_SUBSCRIBER_LABEL)]
    assert topic_b_row[TOTAL_PUBLISHED_KEY] == "2"
    assert topic_b_row[DELIVERED_KEY] == "0"
    assert topic_b_row[UNDELIVERED_KEY] == "2"
    assert topic_b_row[UNDELIVERED_PCT_KEY] == "100.00"
    assert topic_b_row[DELIVERY_RATIO_KEY] == "0.000"
