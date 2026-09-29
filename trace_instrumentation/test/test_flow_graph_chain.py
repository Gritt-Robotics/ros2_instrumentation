# Copyright 2026 Gritt Robotics Inc.

from typing import List

import pytest
from flow_graph_fixtures import (
    DECL_SUB_PAYLOAD,
    DECL_SUB_ZONE,
    DECL_TIMER_PAYLOAD,
    DECL_TIMER_ZONE,
    INIT_TS_NS,
    PRODUCER_NODE_HANDLE,
    PRODUCER_VPID,
    PRODUCER_VTID,
    PUB_LINK_PAYLOAD,
    RELAY_NODE_HANDLE,
    RELAY_RMW_SUB_HANDLE,
    RELAY_SUB_HANDLE,
    RELAY_VPID,
    RELAY_VTID,
    ROOT_NAMESPACE,
    STEP0_TOPIC,
    STEP1_TOPIC,
    TAKE_LINKS_PAYLOAD,
    TIMER_PERIOD_NS,
    build_flow_graph,
    node_init_events,
    relay_subscription_init,
)
from synthetic_events import (
    node_init_event,
    publish_events,
    publisher_init_event,
    python_zone_event,
    rmw_take_event,
    subscription_init_event,
    zone_declaration_event,
)
from trace_instrumentation.trace_analysis.ctf.reader import Event
from trace_instrumentation.trace_analysis.flow_graph import (
    ChainSpec,
    split_chain_terminator,
)
from trace_instrumentation.trace_analysis.graph import (
    CHAIN_TERMINATOR_PUB,
    CHAIN_TERMINATOR_TAKE,
)
from trace_instrumentation.trace_analysis.python_zones import (
    PYTHON_ZONE_PHASE_BEGIN,
    PYTHON_ZONE_PHASE_END,
)

# The far end of the chain: subscribes to what the relay republishes and republishes
# nothing itself, so a traversal has somewhere to terminate.
SINK_VPID = 300
SINK_VTID = 30
SINK_NODE_HANDLE = 930
SINK_RMW_NODE_HANDLE = 952
SINK_NODE_NAME = "sink"

# Handles are opaque pointers. rcl-level and rmw-level handles are separate spaces
# keyed by separate registries, so they are numbered apart here to stay unmixable.
PRODUCER_PUB_HANDLE = 1
RELAY_PUB_HANDLE = 2
SINK_SUB_HANDLE = 6
OBSERVER_SUB_HANDLE = 7
OBSERVER_SINK_SUB_HANDLE = 8
PRODUCER_RMW_PUB_HANDLE = 101
RELAY_RMW_PUB_HANDLE = 102
SINK_RMW_SUB_HANDLE = 106
OBSERVER_RMW_SUB_HANDLE = 107
OBSERVER_SINK_RMW_SUB_HANDLE = 108

SINK_SUB_ZONE = "_on_relayed"
OBSERVER_SUB_ZONE = "_observe"
OBSERVER_SINK_SUB_ZONE = "_also_on_relayed"

# Node labels a chain step can pin as its consumer.
SINK_NODE_LABEL = "/sink"
PRODUCER_NODE_LABEL = "/producer"

UID_A = 1

# Pointers and timestamps stay distinct per message so a mis-key cannot match by luck.
ORIGIN_MSG = 331
RELAYED_MSG = 332
LINKED_RELAYED_MSG = 334
ORIGIN_SOURCE_TS = 8000
RELAYED_SOURCE_TS = 8100
LINKED_RELAYED_SOURCE_TS = 8200

ORIGIN_PUBLISH_TS_NS = 1000
OBSERVER_TAKE_TS_NS = 1040
OBSERVER_ZONE_BEGIN_TS_NS = 1050
OBSERVER_ZONE_END_TS_NS = 1060
RELAY_TAKE_TS_NS = 1100
RELAY_ZONE_BEGIN_TS_NS = 1150
PUB_LINK_TS_NS = 1160
RELAY_PUBLISH_TS_NS = 1200
RELAY_ZONE_END_TS_NS = 1250
SINK_TAKE_TS_NS = 1300
SINK_ZONE_BEGIN_TS_NS = 1350
SINK_ZONE_END_TS_NS = 1400

# The second subscriber on the sink topic, on a different node than the sink itself.
OBSERVER_SINK_TAKE_TS_NS = 1310
OBSERVER_SINK_ZONE_BEGIN_TS_NS = 1360
OBSERVER_SINK_ZONE_END_TS_NS = 1410

# The relay's timer invocation, which takes the uid and republishes on the sink's topic.
TIMER_ZONE_BEGIN_TS_NS = 1450
TAKE_LINKS_TS_NS = 1460
LINKED_PUBLISH_TS_NS = 1500
TIMER_ZONE_END_TS_NS = 1550
LINKED_SINK_TAKE_TS_NS = 1600
LINKED_SINK_ZONE_BEGIN_TS_NS = 1650
LINKED_SINK_ZONE_END_TS_NS = 1700


def _sink_node_init() -> Event:
    """
    Register the sink node.

    Returns:
        Event: The sink's node registration.
    """
    return node_init_event(
        INIT_TS_NS,
        SINK_VPID,
        SINK_VTID,
        SINK_NODE_HANDLE,
        SINK_NODE_NAME,
        ROOT_NAMESPACE,
        SINK_RMW_NODE_HANDLE,
    )


def _producer_pub_init() -> Event:
    """
    rcl_publisher_init for the publisher that opens the chain.

    Returns:
        Event: The synthetic init event.
    """
    return publisher_init_event(
        INIT_TS_NS,
        PRODUCER_VPID,
        PRODUCER_VTID,
        PRODUCER_PUB_HANDLE,
        PRODUCER_NODE_HANDLE,
        PRODUCER_RMW_PUB_HANDLE,
        STEP0_TOPIC,
    )


def _relay_pub_init() -> Event:
    """
    rcl_publisher_init for the publisher the relay continues the chain through.

    Returns:
        Event: The synthetic init event.
    """
    return publisher_init_event(
        INIT_TS_NS,
        RELAY_VPID,
        RELAY_VTID,
        RELAY_PUB_HANDLE,
        RELAY_NODE_HANDLE,
        RELAY_RMW_PUB_HANDLE,
        STEP1_TOPIC,
    )


def _sink_sub_init() -> Event:
    """
    rcl_subscription_init for the sink node's subscription on the last topic.

    Returns:
        Event: The synthetic init event.
    """
    return subscription_init_event(
        INIT_TS_NS,
        SINK_VPID,
        SINK_VTID,
        SINK_SUB_HANDLE,
        SINK_NODE_HANDLE,
        SINK_RMW_SUB_HANDLE,
        STEP1_TOPIC,
    )


def _observer_sub_init() -> Event:
    """
    rcl_subscription_init for a second subscription on the chain's first topic.

    Held by the producer node, so the origin publish fans out to two takes and only one
    of them relays onward.

    Returns:
        Event: The synthetic init event.
    """
    return subscription_init_event(
        INIT_TS_NS,
        PRODUCER_VPID,
        PRODUCER_VTID,
        OBSERVER_SUB_HANDLE,
        PRODUCER_NODE_HANDLE,
        OBSERVER_RMW_SUB_HANDLE,
        STEP0_TOPIC,
    )


def _sub_zone_declaration(vpid: int, vtid: int, zone_name: str, topic: str) -> Event:
    """
    Declare an instrumented Python zone to be a topic's subscription callback.

    Args:
        vpid (int): Process the zone runs in.
        vtid (int): Thread the registration ran on.
        zone_name (str): The instrumented method's name.
        topic (str): Topic the zone handles.

    Returns:
        Event: The synthetic declaration event.
    """
    return zone_declaration_event(
        INIT_TS_NS,
        vpid,
        vtid,
        DECL_SUB_PAYLOAD.format(zone_name=zone_name, topic=topic),
    )


def _zone_invocation(
    vpid: int,
    vtid: int,
    zone_name: str,
    begin_ts_ns: int,
    end_ts_ns: int,
    inner_events: List[Event],
) -> List[Event]:
    """
    One invocation of an instrumented Python zone, wrapping whatever ran inside it.

    Args:
        vpid (int): Process the invocation runs in.
        vtid (int): Thread the invocation runs on.
        zone_name (str): The zone's name, matching its declaration.
        begin_ts_ns (int): Zone-begin timestamp in nanoseconds.
        end_ts_ns (int): Zone-end timestamp in nanoseconds.
        inner_events (List[Event]): Events fired from inside the zone, in order.

    Returns:
        List[Event]: The zone begin, the inner events, and the zone end.
    """
    return [
        python_zone_event(begin_ts_ns, vpid, vtid, zone_name, PYTHON_ZONE_PHASE_BEGIN),
        *inner_events,
        python_zone_event(end_ts_ns, vpid, vtid, zone_name, PYTHON_ZONE_PHASE_END),
    ]


def _prelude() -> List[Event]:
    """
    Every registration the chain scenarios hang their traffic off.

    The relay and the sink are driven by declared Python zones, so each one's callback
    resolves to the subscription it serves and can bind the take it consumed.

    Returns:
        List[Event]: The three node registrations, the endpoints, and the declarations.
    """
    return [
        *node_init_events(),
        _sink_node_init(),
        _producer_pub_init(),
        relay_subscription_init(),
        _relay_pub_init(),
        _sink_sub_init(),
        _sub_zone_declaration(RELAY_VPID, RELAY_VTID, DECL_SUB_ZONE, STEP0_TOPIC),
        _sub_zone_declaration(SINK_VPID, SINK_VTID, SINK_SUB_ZONE, STEP1_TOPIC),
    ]


def _origin_publish() -> List[Event]:
    """
    The producer publishing the message that opens the chain.

    Returns:
        List[Event]: The publish triplet.
    """
    return publish_events(
        ORIGIN_PUBLISH_TS_NS,
        PRODUCER_VPID,
        PRODUCER_VTID,
        ORIGIN_MSG,
        PRODUCER_PUB_HANDLE,
        source_timestamp=ORIGIN_SOURCE_TS,
    )


def _relay_take() -> Event:
    """
    The relay taking the origin message.

    Returns:
        Event: The rmw_take event.
    """
    return rmw_take_event(
        RELAY_TAKE_TS_NS,
        RELAY_VPID,
        RELAY_VTID,
        ORIGIN_MSG,
        RELAY_RMW_SUB_HANDLE,
        source_timestamp=ORIGIN_SOURCE_TS,
    )


def _relay_publish(
    ts_ns: int = RELAY_PUBLISH_TS_NS,
    message: int = RELAYED_MSG,
    source_timestamp: int = RELAYED_SOURCE_TS,
) -> List[Event]:
    """
    The relay republishing onto the chain's second topic.

    Args:
        ts_ns (int): Timestamp of the first event of the triplet, in nanoseconds.
        message (int): Message buffer pointer.
        source_timestamp (int): DDS timestamp carried by the message.

    Returns:
        List[Event]: The publish triplet.
    """
    return publish_events(
        ts_ns,
        RELAY_VPID,
        RELAY_VTID,
        message,
        RELAY_PUB_HANDLE,
        source_timestamp=source_timestamp,
    )


def _sink_consumption(
    take_ts_ns: int = SINK_TAKE_TS_NS,
    message: int = RELAYED_MSG,
    source_timestamp: int = RELAYED_SOURCE_TS,
    begin_ts_ns: int = SINK_ZONE_BEGIN_TS_NS,
    end_ts_ns: int = SINK_ZONE_END_TS_NS,
) -> List[Event]:
    """
    The sink taking the relayed message and handling it inside its declared zone.

    Args:
        take_ts_ns (int): rmw_take timestamp in nanoseconds.
        message (int): Message buffer pointer.
        source_timestamp (int): DDS timestamp carried by the message.
        begin_ts_ns (int): Zone-begin timestamp in nanoseconds.
        end_ts_ns (int): Zone-end timestamp in nanoseconds.

    Returns:
        List[Event]: The take followed by the zone invocation.
    """
    return [
        rmw_take_event(
            take_ts_ns,
            SINK_VPID,
            SINK_VTID,
            message,
            SINK_RMW_SUB_HANDLE,
            source_timestamp=source_timestamp,
        ),
        *_zone_invocation(
            SINK_VPID, SINK_VTID, SINK_SUB_ZONE, begin_ts_ns, end_ts_ns, []
        ),
    ]


def _relay_timer_declaration() -> Event:
    """
    Declare the relay's timer zone, which is where the uid-linked republish happens.

    Returns:
        Event: The synthetic declaration event.
    """
    return zone_declaration_event(
        INIT_TS_NS,
        RELAY_VPID,
        RELAY_VTID,
        DECL_TIMER_PAYLOAD.format(zone_name=DECL_TIMER_ZONE, period_ns=TIMER_PERIOD_NS),
    )


def _pub_link_event() -> Event:
    """
    The relay's subscription zone publishing UID_A into the link graph.

    Returns:
        Event: The synthetic declaration event.
    """
    return zone_declaration_event(
        PUB_LINK_TS_NS, RELAY_VPID, RELAY_VTID, PUB_LINK_PAYLOAD.format(uid=UID_A)
    )


def _linked_timer_invocation() -> List[Event]:
    """
    The relay's timer zone taking UID_A and republishing on the chain's second topic.

    Returns:
        List[Event]: The zone invocation, with the link take and the publish inside it.
    """
    return _zone_invocation(
        RELAY_VPID,
        RELAY_VTID,
        DECL_TIMER_ZONE,
        TIMER_ZONE_BEGIN_TS_NS,
        TIMER_ZONE_END_TS_NS,
        [
            zone_declaration_event(
                TAKE_LINKS_TS_NS,
                RELAY_VPID,
                RELAY_VTID,
                TAKE_LINKS_PAYLOAD.format(uids=UID_A),
            ),
            *_relay_publish(
                LINKED_PUBLISH_TS_NS, LINKED_RELAYED_MSG, LINKED_RELAYED_SOURCE_TS
            ),
        ],
    )


def _no_sink_take_scenario() -> List[Event]:
    """
    A two-step chain the relay republishes onto and nothing ever takes, which only a
    publish-terminated chain can complete.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *_prelude(),
        *_origin_publish(),
        _relay_take(),
        *_zone_invocation(
            RELAY_VPID,
            RELAY_VTID,
            DECL_SUB_ZONE,
            RELAY_ZONE_BEGIN_TS_NS,
            RELAY_ZONE_END_TS_NS,
            _relay_publish(),
        ),
    ]


def _direct_relay_chain_scenario() -> List[Event]:
    """
    A two-step chain where the relay republishes from inside the callback that took the
    message, and the sink takes what it republished.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [*_no_sink_take_scenario(), *_sink_consumption()]


def _linked_relay_chain_scenario() -> List[Event]:
    """
    A two-step chain that crosses a uid link: the receiving callback caches the message
    and publishes a uid, and a later timer invocation takes that uid and republishes.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *_prelude(),
        _relay_timer_declaration(),
        *_origin_publish(),
        _relay_take(),
        *_zone_invocation(
            RELAY_VPID,
            RELAY_VTID,
            DECL_SUB_ZONE,
            RELAY_ZONE_BEGIN_TS_NS,
            RELAY_ZONE_END_TS_NS,
            [_pub_link_event()],
        ),
        *_linked_timer_invocation(),
        *_sink_consumption(
            LINKED_SINK_TAKE_TS_NS,
            LINKED_RELAYED_MSG,
            LINKED_RELAYED_SOURCE_TS,
            LINKED_SINK_ZONE_BEGIN_TS_NS,
            LINKED_SINK_ZONE_END_TS_NS,
        ),
    ]


def _direct_and_linked_relay_scenario() -> List[Event]:
    """
    A relay that both republishes directly and publishes a uid a later timer takes, so
    the two candidate continuations compete.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *_prelude(),
        _relay_timer_declaration(),
        *_origin_publish(),
        _relay_take(),
        *_zone_invocation(
            RELAY_VPID,
            RELAY_VTID,
            DECL_SUB_ZONE,
            RELAY_ZONE_BEGIN_TS_NS,
            RELAY_ZONE_END_TS_NS,
            [_pub_link_event(), *_relay_publish()],
        ),
        *_sink_consumption(),
        *_linked_timer_invocation(),
    ]


def _broken_relay_chain_scenario() -> List[Event]:
    """
    A relay that takes the message but never republishes it.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *_prelude(),
        *_origin_publish(),
        _relay_take(),
        *_zone_invocation(
            RELAY_VPID,
            RELAY_VTID,
            DECL_SUB_ZONE,
            RELAY_ZONE_BEGIN_TS_NS,
            RELAY_ZONE_END_TS_NS,
            [],
        ),
    ]


def _observer_sink_sub_init() -> Event:
    """
    rcl_subscription_init for a second subscription on the chain's sink topic.

    Held by the producer node, so the sink topic has two subscribers on two different
    nodes and a step that pins one of them has something to choose between.

    Returns:
        Event: The synthetic init event.
    """
    return subscription_init_event(
        INIT_TS_NS,
        PRODUCER_VPID,
        PRODUCER_VTID,
        OBSERVER_SINK_SUB_HANDLE,
        PRODUCER_NODE_HANDLE,
        OBSERVER_SINK_RMW_SUB_HANDLE,
        STEP1_TOPIC,
    )


def _observer_sink_consumption() -> List[Event]:
    """
    The second sink subscriber taking the relayed message inside its declared zone.

    Returns:
        List[Event]: The take followed by the zone invocation.
    """
    return [
        rmw_take_event(
            OBSERVER_SINK_TAKE_TS_NS,
            PRODUCER_VPID,
            PRODUCER_VTID,
            RELAYED_MSG,
            OBSERVER_SINK_RMW_SUB_HANDLE,
            source_timestamp=RELAYED_SOURCE_TS,
        ),
        *_zone_invocation(
            PRODUCER_VPID,
            PRODUCER_VTID,
            OBSERVER_SINK_SUB_ZONE,
            OBSERVER_SINK_ZONE_BEGIN_TS_NS,
            OBSERVER_SINK_ZONE_END_TS_NS,
            [],
        ),
    ]


def _two_consumer_sink_scenario() -> List[Event]:
    """
    A direct relay chain whose sink topic is taken by two subscribers on two nodes.

    The sink node takes first, so a walk that simply picked the first take on the topic
    would always report the sink and never the observer.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *_prelude(),
        _observer_sink_sub_init(),
        _sub_zone_declaration(
            PRODUCER_VPID, PRODUCER_VTID, OBSERVER_SINK_SUB_ZONE, STEP1_TOPIC
        ),
        *_origin_publish(),
        _relay_take(),
        *_zone_invocation(
            RELAY_VPID,
            RELAY_VTID,
            DECL_SUB_ZONE,
            RELAY_ZONE_BEGIN_TS_NS,
            RELAY_ZONE_END_TS_NS,
            _relay_publish(),
        ),
        *_sink_consumption(),
        *_observer_sink_consumption(),
    ]


def _fanout_relay_chain_scenario() -> List[Event]:
    """
    The origin publish delivered to two subscriptions on the first topic, only one of
    which relays onward.

    The observer takes first, so a walk that simply picked the first take on the topic
    would pick the wrong one.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *_prelude(),
        _observer_sub_init(),
        _sub_zone_declaration(
            PRODUCER_VPID, PRODUCER_VTID, OBSERVER_SUB_ZONE, STEP0_TOPIC
        ),
        *_origin_publish(),
        rmw_take_event(
            OBSERVER_TAKE_TS_NS,
            PRODUCER_VPID,
            PRODUCER_VTID,
            ORIGIN_MSG,
            OBSERVER_RMW_SUB_HANDLE,
            source_timestamp=ORIGIN_SOURCE_TS,
        ),
        *_zone_invocation(
            PRODUCER_VPID,
            PRODUCER_VTID,
            OBSERVER_SUB_ZONE,
            OBSERVER_ZONE_BEGIN_TS_NS,
            OBSERVER_ZONE_END_TS_NS,
            [],
        ),
        _relay_take(),
        *_zone_invocation(
            RELAY_VPID,
            RELAY_VTID,
            DECL_SUB_ZONE,
            RELAY_ZONE_BEGIN_TS_NS,
            RELAY_ZONE_END_TS_NS,
            _relay_publish(),
        ),
        *_sink_consumption(),
    ]


def test_a_two_step_direct_chain_yields_one_traversal():
    flow = build_flow_graph(_direct_relay_chain_scenario())
    traversals = flow.walk_topic_chain([STEP0_TOPIC, STEP1_TOPIC])
    assert len(traversals) == 1
    traversal = traversals[0]
    assert [step.topic for step in traversal.steps] == [STEP0_TOPIC, STEP1_TOPIC]
    assert traversal.steps[0].next_topic == STEP1_TOPIC
    assert traversal.steps[-1].next_topic is None
    assert traversal.steps[0].callback_time_ns is not None
    assert traversal.steps[-1].callback_time_ns is None


def test_traversal_latencies_are_direct_subtractions():
    flow = build_flow_graph(_direct_relay_chain_scenario())
    traversal = flow.walk_topic_chain([STEP0_TOPIC, STEP1_TOPIC])[0]
    origin_pub = traversal.steps[0].pub
    sink_take = traversal.steps[-1].take
    assert traversal.total_latency_ns == sink_take.ts_rmw - origin_pub.ts_rmw
    sink_cb = sink_take.consumer_cb
    assert traversal.callback_start_latency_ns == sink_cb.ts_start - origin_pub.ts_rmw


def test_a_chain_crossing_a_uid_link_traverses():
    # the receiving callback caches and publishes a uid; a timer takes it and republishes
    flow = build_flow_graph(_linked_relay_chain_scenario())
    traversals = flow.walk_topic_chain([STEP0_TOPIC, STEP1_TOPIC])
    assert len(traversals) == 1
    step = traversals[0].steps[0]
    assert step.is_indirect is True
    assert step.linked_pub is not None
    assert step.linked_take is not None
    # The wait is measured to the timer callback's start, not to TAKE_LINKS_TS_NS
    # (when its code reaches the linked_take() call).
    assert step.indirect_wait_ns == TIMER_ZONE_BEGIN_TS_NS - PUB_LINK_TS_NS


def test_a_direct_republish_is_preferred_over_a_linked_one():
    flow = build_flow_graph(_direct_and_linked_relay_scenario())
    step = flow.walk_topic_chain([STEP0_TOPIC, STEP1_TOPIC])[0].steps[0]
    assert step.is_indirect is False
    assert step.linked_pub is None
    assert step.linked_take is None
    assert step.indirect_wait_ns is None


def test_a_chain_that_does_not_complete_yields_nothing():
    # the relay takes the message but never republishes on the next topic
    flow = build_flow_graph(_broken_relay_chain_scenario())
    assert flow.walk_topic_chain([STEP0_TOPIC, STEP1_TOPIC]) == []


def test_fanout_follows_only_the_take_whose_callback_republished():
    # two subscriptions on STEP0_TOPIC; only one relays onward
    flow = build_flow_graph(_fanout_relay_chain_scenario())
    traversals = flow.walk_topic_chain([STEP0_TOPIC, STEP1_TOPIC])
    assert len(traversals) == 1
    assert (
        traversals[0].steps[0].take.subscription
        is flow.subscription_endpoints[RELAY_SUB_HANDLE]
    )


def test_a_pinned_sink_step_selects_that_nodes_take():
    # two subscribers on STEP1_TOPIC; the pin decides which one terminates the chain
    flow = build_flow_graph(_two_consumer_sink_scenario())

    to_sink = flow.walk_topic_chain([STEP0_TOPIC, f"{STEP1_TOPIC}@{SINK_NODE_LABEL}"])
    assert len(to_sink) == 1
    assert to_sink[0].steps[-1].take.node.label == SINK_NODE_LABEL
    assert to_sink[0].steps[-1].take.ts_rmw == SINK_TAKE_TS_NS

    to_observer = flow.walk_topic_chain([
        STEP0_TOPIC,
        f"{STEP1_TOPIC}@{PRODUCER_NODE_LABEL}",
    ])
    assert len(to_observer) == 1
    assert to_observer[0].steps[-1].take.node.label == PRODUCER_NODE_LABEL
    assert to_observer[0].steps[-1].take.ts_rmw == OBSERVER_SINK_TAKE_TS_NS


def test_a_pinned_relay_step_selects_that_nodes_take():
    # only the relay node's take republishes onward, so pinning it resolves and
    # pinning the observer that never relayed does not
    flow = build_flow_graph(_fanout_relay_chain_scenario())
    assert len(flow.walk_topic_chain([f"{STEP0_TOPIC}@/relay", STEP1_TOPIC])) == 1
    assert (
        flow.walk_topic_chain([f"{STEP0_TOPIC}@{PRODUCER_NODE_LABEL}", STEP1_TOPIC])
        == []
    )


def test_a_pinned_step_naming_a_node_that_never_took_it_yields_nothing():
    flow = build_flow_graph(_two_consumer_sink_scenario())
    assert flow.walk_topic_chain([STEP0_TOPIC, f"{STEP1_TOPIC}@/absent"]) == []


def test_an_unpinned_step_still_accepts_any_subscriber():
    # the pin is opt-in: without it the sink resolves as before
    flow = build_flow_graph(_two_consumer_sink_scenario())
    traversals = flow.walk_topic_chain([STEP0_TOPIC, STEP1_TOPIC])
    assert len(traversals) == 1
    assert traversals[0].steps[-1].take is not None


def test_a_sequence_shorter_than_two_topics_is_rejected():
    flow = build_flow_graph(_direct_relay_chain_scenario())
    assert flow.walk_topic_chain([]) == []
    assert flow.walk_topic_chain([STEP0_TOPIC]) == []


def test_a_publish_terminated_chain_completes_with_nothing_taking_the_last_topic():
    flow = build_flow_graph(_no_sink_take_scenario())
    assert flow.walk_topic_chain([STEP0_TOPIC, STEP1_TOPIC]) == []

    traversals = flow.walk_topic_chain([STEP0_TOPIC, STEP1_TOPIC, CHAIN_TERMINATOR_PUB])
    assert len(traversals) == 1
    traversal = traversals[0]
    assert traversal.terminator == CHAIN_TERMINATOR_PUB
    assert [step.topic for step in traversal.steps] == [STEP0_TOPIC, STEP1_TOPIC]
    sink_step = traversal.steps[-1]
    assert sink_step.take is None
    assert sink_step.pub.topic == STEP1_TOPIC
    assert sink_step.end_ts_ns == sink_step.pub.ts_rmw
    assert sink_step.callback_time_ns is None
    origin_pub = traversal.steps[0].pub
    assert traversal.total_latency_ns == sink_step.pub.ts_rmw - origin_pub.ts_rmw
    assert traversal.callback_start_latency_ns is None
    assert traversal.chain_latency_ns == traversal.total_latency_ns


def test_a_publish_terminated_chain_ends_at_the_wire_even_when_a_take_followed():
    flow = build_flow_graph(_direct_relay_chain_scenario())
    to_publish = flow.walk_topic_chain([
        STEP0_TOPIC,
        STEP1_TOPIC,
        CHAIN_TERMINATOR_PUB,
    ])[0]
    to_take = flow.walk_topic_chain([STEP0_TOPIC, STEP1_TOPIC])[0]
    assert to_publish.steps[-1].take is None
    assert to_take.steps[-1].take is not None
    assert to_publish.chain_latency_ns < to_take.chain_latency_ns


def test_an_explicit_take_terminator_matches_the_default():
    flow = build_flow_graph(_direct_relay_chain_scenario())
    explicit = flow.walk_topic_chain([STEP0_TOPIC, STEP1_TOPIC, CHAIN_TERMINATOR_TAKE])
    default = flow.walk_topic_chain([STEP0_TOPIC, STEP1_TOPIC])
    assert len(explicit) == len(default) == 1
    assert explicit[0].terminator == CHAIN_TERMINATOR_TAKE
    assert default[0].terminator == CHAIN_TERMINATOR_TAKE
    assert explicit[0].steps[-1].take is default[0].steps[-1].take
    assert explicit[0].chain_latency_ns == default[0].chain_latency_ns


def test_a_publish_terminated_chain_still_needs_the_relayed_publish():
    # the relay took the message and never republished, so there is no publish to end at
    flow = build_flow_graph(_broken_relay_chain_scenario())
    assert flow.walk_topic_chain([STEP0_TOPIC, STEP1_TOPIC, CHAIN_TERMINATOR_PUB]) == []


def test_a_terminator_token_is_not_walked_as_a_step():
    flow = build_flow_graph(_direct_relay_chain_scenario())
    assert flow.walk_topic_chain([STEP0_TOPIC, CHAIN_TERMINATOR_PUB]) == []


def test_split_chain_terminator_defaults_to_take_when_no_token_is_given():
    spec = split_chain_terminator([STEP0_TOPIC, STEP1_TOPIC])
    assert spec == ChainSpec([STEP0_TOPIC, STEP1_TOPIC], CHAIN_TERMINATOR_TAKE)
    assert spec.steps == [STEP0_TOPIC, STEP1_TOPIC]
    assert spec.terminator == CHAIN_TERMINATOR_TAKE


def test_split_chain_terminator_takes_the_trailing_token_off_the_steps():
    spec = split_chain_terminator([STEP0_TOPIC, STEP1_TOPIC, CHAIN_TERMINATOR_PUB])
    assert spec.steps == [STEP0_TOPIC, STEP1_TOPIC]
    assert spec.terminator == CHAIN_TERMINATOR_PUB


def test_sink_step_rejects_a_publish_onto_a_topic_other_than_the_steps():
    flow = build_flow_graph(_direct_relay_chain_scenario())
    origin_pub = next(pub for pub in flow.publishes if pub.topic == STEP0_TOPIC)
    with pytest.raises(AssertionError, match=STEP1_TOPIC):
        flow.sink_step(origin_pub, STEP1_TOPIC, CHAIN_TERMINATOR_PUB)
