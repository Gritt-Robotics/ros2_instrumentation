# Copyright 2026 Gritt Robotics Inc.

"""
flow_graph_fixtures.py
======================

The topology every FlowGraph scenario hangs its traffic off: two nodes, the relay's
subscription, and the builder that turns an event list into a built graph.

Lives outside the test modules so `test_flow_graph.py` and `test_flow_graph_links.py`
share one definition of the scenario prelude rather than each carrying its own copy.
"""

from typing import List, Tuple

from synthetic_events import node_init_event, subscription_init_event
from trace_instrumentation.trace_analysis.analysis import TraceAnalysis
from trace_instrumentation.trace_analysis.ctf.reader import Event, Metadata
from trace_instrumentation.trace_analysis.flow_graph import FlowGraph

RELAY_VPID = 100
RELAY_VTID = 10
RELAY_NODE_HANDLE = 910
RELAY_RMW_NODE_HANDLE = 950
RELAY_NODE_NAME = "relay"

PRODUCER_VPID = 200
PRODUCER_VTID = 20
PRODUCER_NODE_HANDLE = 920
PRODUCER_RMW_NODE_HANDLE = 951
PRODUCER_NODE_NAME = "producer"

ROOT_NAMESPACE = "/"
# every registration event predates the traffic that uses it
INIT_TS_NS = 0

STEP0_TOPIC = "/step0"
STEP1_TOPIC = "/step1"

# Handles are opaque pointers. rcl-level and rmw-level handles are separate spaces
# keyed by separate registries, so they are numbered apart here to stay unmixable.
RELAY_SUB_HANDLE = 3
RELAY_RMW_SUB_HANDLE = 103

TIMER_PERIOD_NS = 100_000_000
DECL_SUB_ZONE = "_on_image"
DECL_TIMER_ZONE = "_tick"

# Zone-declaration payloads, as the instrumentation emits them at registration time.
DECL_SUB_PAYLOAD = "trace_decl sub zone={zone_name} topic={topic}"
DECL_TIMER_PAYLOAD = "trace_decl timer zone={zone_name} period_ns={period_ns}"

PUB_LINK_PAYLOAD = "trace_link pub uid={uid}"
TAKE_LINKS_PAYLOAD = "trace_link take uids={uids}"
TRUNCATED_TAKE_LINKS_PAYLOAD = "trace_link take uids={uids} truncated=1"
LINK_UID_SEPARATOR = ","


def build_analysis_and_flow(events: List[Event]) -> Tuple[TraceAnalysis, FlowGraph]:
    """
    Build both halves of the model for a synthetic event stream.

    Reporting scenarios need the analysis alongside the graph; graph-only scenarios
    use build_flow_graph() instead of discarding it at every call site.

    Args:
        events (List[Event]): The synthetic event sequence.

    Returns:
        Tuple[TraceAnalysis, FlowGraph]: The built analysis and the built graph.
    """
    meta = Metadata()
    analysis = TraceAnalysis(meta, events).build()
    return analysis, FlowGraph(meta, events, analysis).build()


def build_flow_graph(events: List[Event]) -> FlowGraph:
    """
    Build the flow graph for a synthetic event stream.

    Args:
        events (List[Event]): The synthetic event sequence.

    Returns:
        FlowGraph: The built graph.
    """
    _analysis, flow = build_analysis_and_flow(events)
    return flow


def node_init_events() -> List[Event]:
    """
    Register both nodes the scenarios hang their endpoints off.

    Every scenario needs these: an endpoint whose node has no `rcl_node_init` is left
    out of the topology rather than attached to a fabricated node.

    Returns:
        List[Event]: The relay's and the producer's node registrations.
    """
    return [
        node_init_event(
            INIT_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            RELAY_NODE_HANDLE,
            RELAY_NODE_NAME,
            ROOT_NAMESPACE,
            RELAY_RMW_NODE_HANDLE,
        ),
        node_init_event(
            INIT_TS_NS,
            PRODUCER_VPID,
            PRODUCER_VTID,
            PRODUCER_NODE_HANDLE,
            PRODUCER_NODE_NAME,
            ROOT_NAMESPACE,
            PRODUCER_RMW_NODE_HANDLE,
        ),
    ]


def relay_subscription_init(topic: str = STEP0_TOPIC) -> Event:
    """
    rcl_subscription_init for the relay node's subscription.

    Args:
        topic (str): Topic the relay subscribes to. Defaults to STEP0_TOPIC.

    Returns:
        Event: The synthetic init event.
    """
    return subscription_init_event(
        INIT_TS_NS,
        RELAY_VPID,
        RELAY_VTID,
        RELAY_SUB_HANDLE,
        RELAY_NODE_HANDLE,
        RELAY_RMW_SUB_HANDLE,
        topic,
    )
