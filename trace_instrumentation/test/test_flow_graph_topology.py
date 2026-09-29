# Copyright 2026 Gritt Robotics Inc.

from typing import List

from flow_graph_fixtures import build_flow_graph
from synthetic_events import (
    node_init_event,
    publisher_init_event,
    rmw_publisher_init_event,
    rmw_subscription_init_event,
    service_init_event,
    subscription_init_event,
    timer_init_event,
    timer_link_node_event,
)
from trace_instrumentation.trace_analysis.ctf.reader import Event

PRODUCER_VPID = 100
PRODUCER_VTID = 10
CONSUMER_VPID = 200
CONSUMER_VTID = 20

PRODUCER_NODE_HANDLE = 900
CONSUMER_NODE_HANDLE = 901
PRODUCER_RMW_NODE_HANDLE = 950
CONSUMER_RMW_NODE_HANDLE = 951
PRODUCER_NAME = "producer"
CONSUMER_NAME = "consumer"
NAMESPACE = "/"

PUB_HANDLE = 1
SUB_HANDLE = 2
TIMER_HANDLE = 3
SERVICE_HANDLE = 4
RMW_PUB_HANDLE = 101
RMW_SUB_HANDLE = 102
RMW_SERVICE_HANDLE = 104

TOPIC = "/image"
SERVICE_NAME = "/reset"
TIMER_PERIOD_NS = 100_000_000
PUB_GID = [1, 2, 3]
SUB_GID = [4, 5, 6]
DEFAULT_QUEUE_DEPTH = 10
INIT_TS_NS = 0


def _topology_events() -> List[Event]:
    return [
        node_init_event(
            INIT_TS_NS,
            PRODUCER_VPID,
            PRODUCER_VTID,
            PRODUCER_NODE_HANDLE,
            PRODUCER_NAME,
            NAMESPACE,
            PRODUCER_RMW_NODE_HANDLE,
        ),
        node_init_event(
            INIT_TS_NS,
            CONSUMER_VPID,
            CONSUMER_VTID,
            CONSUMER_NODE_HANDLE,
            CONSUMER_NAME,
            NAMESPACE,
            CONSUMER_RMW_NODE_HANDLE,
        ),
        publisher_init_event(
            INIT_TS_NS,
            PRODUCER_VPID,
            PRODUCER_VTID,
            PUB_HANDLE,
            PRODUCER_NODE_HANDLE,
            RMW_PUB_HANDLE,
            TOPIC,
        ),
        rmw_publisher_init_event(
            INIT_TS_NS, PRODUCER_VPID, PRODUCER_VTID, RMW_PUB_HANDLE, PUB_GID
        ),
        subscription_init_event(
            INIT_TS_NS,
            CONSUMER_VPID,
            CONSUMER_VTID,
            SUB_HANDLE,
            CONSUMER_NODE_HANDLE,
            RMW_SUB_HANDLE,
            TOPIC,
        ),
        rmw_subscription_init_event(
            INIT_TS_NS, CONSUMER_VPID, CONSUMER_VTID, RMW_SUB_HANDLE, SUB_GID
        ),
        timer_init_event(
            INIT_TS_NS, PRODUCER_VPID, PRODUCER_VTID, TIMER_HANDLE, TIMER_PERIOD_NS
        ),
        timer_link_node_event(
            INIT_TS_NS, PRODUCER_VPID, PRODUCER_VTID, TIMER_HANDLE, PRODUCER_NODE_HANDLE
        ),
        service_init_event(
            INIT_TS_NS,
            CONSUMER_VPID,
            CONSUMER_VTID,
            SERVICE_HANDLE,
            CONSUMER_NODE_HANDLE,
            RMW_SERVICE_HANDLE,
            SERVICE_NAME,
        ),
    ]


def test_nodes_are_materialized_with_labels():
    flow = build_flow_graph(_topology_events())
    assert set(flow.nodes.keys()) == {PRODUCER_NODE_HANDLE, CONSUMER_NODE_HANDLE}
    assert flow.nodes[PRODUCER_NODE_HANDLE].label == f"/{PRODUCER_NAME}"
    assert flow.nodes[PRODUCER_NODE_HANDLE].vpid == PRODUCER_VPID


def test_one_topic_object_is_shared_by_both_endpoints():
    flow = build_flow_graph(_topology_events())
    topic = flow.topics[TOPIC]
    assert len(topic.publishers) == 1
    assert len(topic.subscribers) == 1
    # the same Topic instance on both sides, so a chain walk compares by identity
    assert topic.publishers[0].topic is topic
    assert topic.subscribers[0].topic is topic


def test_endpoints_are_attached_to_their_owning_nodes():
    flow = build_flow_graph(_topology_events())
    producer = flow.nodes[PRODUCER_NODE_HANDLE]
    consumer = flow.nodes[CONSUMER_NODE_HANDLE]
    assert [endpoint.handle for endpoint in producer.publishers] == [PUB_HANDLE]
    assert [endpoint.handle for endpoint in consumer.subscriptions] == [SUB_HANDLE]
    assert [endpoint.handle for endpoint in producer.timers] == [TIMER_HANDLE]
    assert [endpoint.handle for endpoint in consumer.services] == [SERVICE_HANDLE]


def test_endpoints_carry_queue_depth_and_gid():
    flow = build_flow_graph(_topology_events())
    publisher = flow.publisher_endpoints[PUB_HANDLE]
    subscription = flow.subscription_endpoints[SUB_HANDLE]
    assert publisher.queue_depth == DEFAULT_QUEUE_DEPTH
    assert publisher.gid == PUB_GID
    assert subscription.queue_depth == DEFAULT_QUEUE_DEPTH
    assert subscription.gid == SUB_GID


def test_rmw_handles_index_back_to_their_endpoints():
    flow = build_flow_graph(_topology_events())
    assert (
        flow.rmwpub_to_endpoint[RMW_PUB_HANDLE] is flow.publisher_endpoints[PUB_HANDLE]
    )
    assert (
        flow.rmwsub_to_endpoint[RMW_SUB_HANDLE]
        is flow.subscription_endpoints[SUB_HANDLE]
    )


def test_timer_and_service_endpoints_resolve_their_details():
    flow = build_flow_graph(_topology_events())
    timer = flow.timer_endpoints[TIMER_HANDLE]
    assert timer.period_ns == TIMER_PERIOD_NS
    assert timer.node is flow.nodes[PRODUCER_NODE_HANDLE]
    service = flow.service_endpoints[SERVICE_HANDLE]
    assert service.name == SERVICE_NAME
    assert service.node is flow.nodes[CONSUMER_NODE_HANDLE]


def test_endpoint_whose_node_was_never_registered_is_skipped():
    # a publisher_init with no preceding node_init cannot be attached to a Node, so it
    # is left out of the topology rather than attached to a fabricated one
    events = [
        publisher_init_event(
            INIT_TS_NS,
            PRODUCER_VPID,
            PRODUCER_VTID,
            PUB_HANDLE,
            PRODUCER_NODE_HANDLE,
            RMW_PUB_HANDLE,
            TOPIC,
        )
    ]
    flow = build_flow_graph(events)
    assert len(flow.nodes) == 0
    assert PUB_HANDLE not in flow.publisher_endpoints
