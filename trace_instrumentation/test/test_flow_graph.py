# Copyright 2026 Gritt Robotics Inc.

from typing import List, Optional

from flow_graph_fixtures import (
    DECL_SUB_PAYLOAD,
    DECL_SUB_ZONE,
    DECL_TIMER_PAYLOAD,
    DECL_TIMER_ZONE,
    INIT_TS_NS,
    PRODUCER_NODE_HANDLE,
    PRODUCER_VPID,
    PRODUCER_VTID,
    RELAY_NODE_HANDLE,
    RELAY_RMW_SUB_HANDLE,
    RELAY_SUB_HANDLE,
    RELAY_VPID,
    RELAY_VTID,
    STEP0_TOPIC,
    STEP1_TOPIC,
    TIMER_PERIOD_NS,
    build_flow_graph,
    node_init_events,
    relay_subscription_init,
)
from synthetic_events import (
    callback_end_event,
    callback_start_event,
    make_event,
    publish_events,
    publisher_init_event,
    python_zone_event,
    rclcpp_subscription_init_event,
    rmw_publisher_init_event,
    rmw_take_event,
    subscription_callback_added_event,
    subscription_init_event,
    timer_events,
    zone_declaration_event,
)
from trace_instrumentation.trace_analysis.ctf.reader import Event
from trace_instrumentation.trace_analysis.event_fields import (
    FIELD_MESSAGE,
    FIELD_PUBLISHER_HANDLE,
    FIELD_RMW_PUBLISHER_HANDLE,
    FIELD_TIMESTAMP,
)
from trace_instrumentation.trace_analysis.event_names import (
    EVT_CALLBACK_START,
    EVT_RCL_PUBLISH,
    EVT_RMW_PUBLISH,
)
from trace_instrumentation.trace_analysis.flow_graph import (
    source_timestamp_or_none,
)
from trace_instrumentation.trace_analysis.graph import (
    CB,
    CB_KIND_SUBSCRIPTION,
    CB_KIND_TIMER,
    CB_KIND_UNKNOWN,
    CB_KIND_ZONE,
    UNKNOWN_HANDLER_LABEL,
)
from trace_instrumentation.trace_analysis.python_zones import (
    PYTHON_ZONE_PHASE_BEGIN,
    PYTHON_ZONE_PHASE_END,
)

# Handles are opaque pointers. rcl-level and rmw-level handles are separate spaces
# keyed by separate registries, so they are numbered apart here to stay unmixable.
PRODUCER_PUB_HANDLE = 1
RELAY_PUB_HANDLE = 2
OTHER_PUB_HANDLE = 4
PRODUCER_SUB_HANDLE = 5
PRODUCER_RMW_PUB_HANDLE = 101
RELAY_RMW_PUB_HANDLE = 102
OTHER_RMW_PUB_HANDLE = 104
PRODUCER_RMW_SUB_HANDLE = 105
# an rmw publisher handle no rcl_publisher_init covers, as DDS graph traffic publishes
UNREGISTERED_RMW_PUB_HANDLE = 999
# an rmw subscription handle no rcl_subscription_init covers
UNREGISTERED_RMW_SUB_HANDLE = 998
# rclcpp-layer subscription pointers, which bind a callback pointer to an rcl handle
RELAY_RCLCPP_SUB_PTR = 201
PRODUCER_RCLCPP_SUB_PTR = 202

PRODUCER_PUB_GID = [1, 2, 3]

# shared producer -> relay scenario: one message published on /step0 and taken once
PRODUCER_MSG = 333
SOURCE_TS = 5000
PUBLISH_TS_NS = 1000
TAKE_TS_NS = 1500
# what both tracepoints emit when no DDS publication instant was available
UNAVAILABLE_SOURCE_TS = 0

ON_MESSAGE_ZONE = "_on_message"
RELAY_ZONE = "_relay"

# Python-zone scenario: the relay takes one message inside a zone and republishes it.
# Pointers/timestamps stay distinct across scenarios so a mis-key can't match by luck.
ZONE_TAKEN_MSG = 222
ZONE_PUBLISHED_MSG = 223
ZONE_TAKE_SOURCE_TS = 7000
ZONE_PUBLISH_SOURCE_TS = 7100
ZONE_TAKE_TS_NS = 1000
ZONE_BEGIN_TS_NS = 1100
ZONE_INNER_BEGIN_TS_NS = 1150
ZONE_PUBLISH_TS_NS = 1200
ZONE_INNER_END_TS_NS = 1300
ZONE_END_TS_NS = 1400

# callback-stack scenario: two processes that happen to share a vtid
SHARED_VTID = RELAY_VTID
RELAY_CB = 501
PRODUCER_CB = 502
RELAY_TAKEN_MSG = 111
PRODUCER_TAKEN_MSG = 112
RELAY_PUBLISHED_MSG = 113
CB_RELAY_TAKE_TS_NS = 1000
CB_RELAY_START_TS_NS = 1100
CB_PRODUCER_TAKE_TS_NS = 1150
CB_PRODUCER_START_TS_NS = 1200
CB_RELAY_PUBLISH_TS_NS = 1250
CB_PRODUCER_END_TS_NS = 1300
CB_RELAY_END_TS_NS = 1350

TIMER_CB_PTR = 601
SUB_CB_PTR = 602
TIMER_HANDLE = 700
SECOND_TAKEN_MSG = 444
SECOND_SOURCE_TS = 6000
# steps between the events of the owner-veto callback sequence
VETO_STEP_NS = 10
DISPLACED_TAKE_OFFSET_NS = 5


def _producer_sub_init(topic: str = STEP1_TOPIC) -> Event:
    """
    rcl_subscription_init for the producer node's subscription.

    Args:
        topic (str): Topic the producer subscribes to. Defaults to STEP1_TOPIC.

    Returns:
        Event: The synthetic init event.
    """
    return subscription_init_event(
        INIT_TS_NS,
        PRODUCER_VPID,
        PRODUCER_VTID,
        PRODUCER_SUB_HANDLE,
        PRODUCER_NODE_HANDLE,
        PRODUCER_RMW_SUB_HANDLE,
        topic,
    )


def _producer_pub_init(
    topic: str = STEP0_TOPIC,
    pub_handle: int = PRODUCER_PUB_HANDLE,
    rmw_pub_handle: int = PRODUCER_RMW_PUB_HANDLE,
) -> Event:
    """
    rcl_publisher_init for a publisher on the producer node.

    Args:
        topic (str): Topic published. Defaults to STEP0_TOPIC.
        pub_handle (int): rcl publisher handle. Defaults to PRODUCER_PUB_HANDLE.
        rmw_pub_handle (int): rmw publisher handle. Defaults to PRODUCER_RMW_PUB_HANDLE.

    Returns:
        Event: The synthetic init event.
    """
    return publisher_init_event(
        INIT_TS_NS,
        PRODUCER_VPID,
        PRODUCER_VTID,
        pub_handle,
        PRODUCER_NODE_HANDLE,
        rmw_pub_handle,
        topic,
    )


def _producer_pub_init_pair() -> List[Event]:
    """
    Both halves of the producer publisher's registration.

    The rcl init names the topic and the owning node; the rmw init carries the DDS GID,
    which only reaches the endpoint through the rmw handle the two share.

    Returns:
        List[Event]: The rcl and rmw publisher init events.
    """
    return [
        _producer_pub_init(),
        rmw_publisher_init_event(
            INIT_TS_NS,
            PRODUCER_VPID,
            PRODUCER_VTID,
            PRODUCER_RMW_PUB_HANDLE,
            PRODUCER_PUB_GID,
        ),
    ]


def _relay_pub_init() -> Event:
    """
    rcl_publisher_init for the relay node's /step1 publisher.

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


def _subscription_callback_binding(
    vpid: int, vtid: int, rclcpp_sub_ptr: int, sub_handle: int, callback: int
) -> List[Event]:
    """
    Bind a C++ callback pointer to the subscription it serves.

    Args:
        vpid (int): Process the subscription belongs to.
        vtid (int): Thread the registration ran on.
        rclcpp_sub_ptr (int): rclcpp-layer subscription pointer.
        sub_handle (int): rcl subscription handle.
        callback (int): CB object pointer.

    Returns:
        List[Event]: The two synthetic registration events.
    """
    return [
        rclcpp_subscription_init_event(
            INIT_TS_NS, vpid, vtid, rclcpp_sub_ptr, sub_handle
        ),
        subscription_callback_added_event(
            INIT_TS_NS, vpid, vtid, rclcpp_sub_ptr, callback
        ),
    ]


def _producer_publish_and_relay_take(
    source_timestamp: int = SOURCE_TS, taken: int = 1
) -> List[Event]:
    """
    The producer publishing one message and the relay taking that same message.

    Args:
        source_timestamp (int): DDS timestamp carried by both sides. Defaults to
            SOURCE_TS.
        taken (int): Whether the take delivered a message. Defaults to 1.

    Returns:
        List[Event]: The publish triplet followed by the rmw_take.
    """
    return [
        *publish_events(
            PUBLISH_TS_NS,
            PRODUCER_VPID,
            PRODUCER_VTID,
            PRODUCER_MSG,
            PRODUCER_PUB_HANDLE,
            source_timestamp=source_timestamp,
            # the vendored 3-arg rmw_publish, so dropping rcl_publish still leaves a
            # handle the publish can resolve through
            rmw_publisher_handle=PRODUCER_RMW_PUB_HANDLE,
        ),
        rmw_take_event(
            TAKE_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            PRODUCER_MSG,
            RELAY_RMW_SUB_HANDLE,
            source_timestamp=source_timestamp,
            taken=taken,
        ),
    ]


def _producer_scenario(
    source_timestamp: int = SOURCE_TS, taken: int = 1
) -> List[Event]:
    """
    One message crossing from the producer to the relay, fully registered on both sides.

    Args:
        source_timestamp (int): DDS timestamp carried by both sides. Defaults to
            SOURCE_TS.
        taken (int): Whether the take delivered a message. Defaults to 1.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *node_init_events(),
        *_producer_pub_init_pair(),
        relay_subscription_init(),
        *_producer_publish_and_relay_take(
            source_timestamp=source_timestamp, taken=taken
        ),
    ]


def _single_zone_events() -> List[Event]:
    """
    A take on RELAY_VTID immediately followed by one non-nested Python zone
    ("_on_message") that republishes once, all on the same thread.

    Returns:
        List[Event]: The synthetic event sequence.
    """
    return [
        relay_subscription_init(),
        _relay_pub_init(),
        rmw_take_event(
            ZONE_TAKE_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            ZONE_TAKEN_MSG,
            RELAY_RMW_SUB_HANDLE,
            source_timestamp=ZONE_TAKE_SOURCE_TS,
        ),
        python_zone_event(
            ZONE_BEGIN_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            ON_MESSAGE_ZONE,
            PYTHON_ZONE_PHASE_BEGIN,
        ),
        *publish_events(
            ZONE_PUBLISH_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            ZONE_PUBLISHED_MSG,
            RELAY_PUB_HANDLE,
            source_timestamp=ZONE_PUBLISH_SOURCE_TS,
        ),
        python_zone_event(
            ZONE_END_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            ON_MESSAGE_ZONE,
            PYTHON_ZONE_PHASE_END,
        ),
    ]


def _relay_scenario() -> List[Event]:
    """
    The relay taking one message on /step0 inside a Python zone and republishing it.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *node_init_events(),
        zone_declaration_event(
            INIT_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            DECL_SUB_PAYLOAD.format(zone_name=ON_MESSAGE_ZONE, topic=STEP0_TOPIC),
        ),
        *_single_zone_events(),
    ]


def _two_process_scenario() -> List[Event]:
    """
    Two processes sharing one vtid, each taking a message and running its own
    subscription callback, interleaved in the merged stream.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *node_init_events(),
        relay_subscription_init(),
        _relay_pub_init(),
        _producer_sub_init(),
        *_subscription_callback_binding(
            RELAY_VPID, SHARED_VTID, RELAY_RCLCPP_SUB_PTR, RELAY_SUB_HANDLE, RELAY_CB
        ),
        *_subscription_callback_binding(
            PRODUCER_VPID,
            SHARED_VTID,
            PRODUCER_RCLCPP_SUB_PTR,
            PRODUCER_SUB_HANDLE,
            PRODUCER_CB,
        ),
        rmw_take_event(
            CB_RELAY_TAKE_TS_NS,
            RELAY_VPID,
            SHARED_VTID,
            RELAY_TAKEN_MSG,
            RELAY_RMW_SUB_HANDLE,
            source_timestamp=ZONE_TAKE_SOURCE_TS,
        ),
        callback_start_event(CB_RELAY_START_TS_NS, RELAY_VPID, SHARED_VTID, RELAY_CB),
        rmw_take_event(
            CB_PRODUCER_TAKE_TS_NS,
            PRODUCER_VPID,
            SHARED_VTID,
            PRODUCER_TAKEN_MSG,
            PRODUCER_RMW_SUB_HANDLE,
            source_timestamp=ZONE_PUBLISH_SOURCE_TS,
        ),
        callback_start_event(
            CB_PRODUCER_START_TS_NS, PRODUCER_VPID, SHARED_VTID, PRODUCER_CB
        ),
        # Published by RELAY_VPID while PRODUCER_VPID's same-vtid PRODUCER_CB is still
        # open, so it must attribute to RELAY_CB -- RELAY_VPID's own open callback.
        *publish_events(
            CB_RELAY_PUBLISH_TS_NS,
            RELAY_VPID,
            SHARED_VTID,
            RELAY_PUBLISHED_MSG,
            RELAY_PUB_HANDLE,
            source_timestamp=SOURCE_TS,
        ),
        callback_end_event(
            CB_PRODUCER_END_TS_NS, PRODUCER_VPID, SHARED_VTID, PRODUCER_CB
        ),
        callback_end_event(CB_RELAY_END_TS_NS, RELAY_VPID, SHARED_VTID, RELAY_CB),
    ]


def _owner_veto_scenario() -> List[Event]:
    """
    One take pending on the relay thread, then a timer callback, then the
    subscription's own callback -- both on the same (vpid, vtid).

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *node_init_events(),
        relay_subscription_init(),
        *_producer_pub_init_pair(),
        *timer_events(
            INIT_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            TIMER_HANDLE,
            RELAY_NODE_HANDLE,
            TIMER_CB_PTR,
            TIMER_PERIOD_NS,
        ),
        *_subscription_callback_binding(
            RELAY_VPID, RELAY_VTID, RELAY_RCLCPP_SUB_PTR, RELAY_SUB_HANDLE, SUB_CB_PTR
        ),
        rmw_take_event(
            TAKE_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            RELAY_TAKEN_MSG,
            RELAY_RMW_SUB_HANDLE,
            source_timestamp=SOURCE_TS,
        ),
        callback_start_event(
            TAKE_TS_NS + VETO_STEP_NS, RELAY_VPID, RELAY_VTID, TIMER_CB_PTR
        ),
        callback_end_event(
            TAKE_TS_NS + 2 * VETO_STEP_NS, RELAY_VPID, RELAY_VTID, TIMER_CB_PTR
        ),
        callback_start_event(
            TAKE_TS_NS + 3 * VETO_STEP_NS, RELAY_VPID, RELAY_VTID, SUB_CB_PTR
        ),
        callback_end_event(
            TAKE_TS_NS + 4 * VETO_STEP_NS, RELAY_VPID, RELAY_VTID, SUB_CB_PTR
        ),
    ]


def _displacement_scenario() -> List[Event]:
    """
    Two takes on one thread with no callback between them, so the second displaces the
    first from the thread's single pending-take slot.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    base = _owner_veto_scenario()
    second_take = rmw_take_event(
        TAKE_TS_NS + DISPLACED_TAKE_OFFSET_NS,
        RELAY_VPID,
        RELAY_VTID,
        SECOND_TAKEN_MSG,
        RELAY_RMW_SUB_HANDLE,
        source_timestamp=SECOND_SOURCE_TS,
    )
    insert_at = next(
        index for index, event in enumerate(base) if event.name == EVT_CALLBACK_START
    )
    return [*base[:insert_at], second_take, *base[insert_at:]]


def _declared_zone_scenario() -> List[Event]:
    """
    A Python zone that declared itself as /step0's subscription callback, with one
    message pending on its thread.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *node_init_events(),
        relay_subscription_init(),
        *_producer_pub_init_pair(),
        zone_declaration_event(
            INIT_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            DECL_SUB_PAYLOAD.format(zone_name=DECL_SUB_ZONE, topic=STEP0_TOPIC),
        ),
        rmw_take_event(
            ZONE_TAKE_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            ZONE_TAKEN_MSG,
            RELAY_RMW_SUB_HANDLE,
            source_timestamp=ZONE_TAKE_SOURCE_TS,
        ),
        python_zone_event(
            ZONE_BEGIN_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            DECL_SUB_ZONE,
            PYTHON_ZONE_PHASE_BEGIN,
        ),
        python_zone_event(
            ZONE_END_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            DECL_SUB_ZONE,
            PYTHON_ZONE_PHASE_END,
        ),
    ]


def _declared_timer_zone_scenario() -> List[Event]:
    """
    A Python zone that declared itself a timer callback, carrying its period.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *node_init_events(),
        zone_declaration_event(
            INIT_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            DECL_TIMER_PAYLOAD.format(
                zone_name=DECL_TIMER_ZONE, period_ns=TIMER_PERIOD_NS
            ),
        ),
        python_zone_event(
            ZONE_BEGIN_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            DECL_TIMER_ZONE,
            PYTHON_ZONE_PHASE_BEGIN,
        ),
        python_zone_event(
            ZONE_END_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            DECL_TIMER_ZONE,
            PYTHON_ZONE_PHASE_END,
        ),
    ]


def _undeclared_zone_scenario() -> List[Event]:
    """
    A Python zone that declared nothing, so nothing in the trace says what it is.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *node_init_events(),
        python_zone_event(
            ZONE_BEGIN_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            ON_MESSAGE_ZONE,
            PYTHON_ZONE_PHASE_BEGIN,
        ),
        python_zone_event(
            ZONE_END_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            ON_MESSAGE_ZONE,
            PYTHON_ZONE_PHASE_END,
        ),
    ]


def _nested_zone_scenario() -> List[Event]:
    """
    One Python zone opened inside another on the same thread.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *node_init_events(),
        python_zone_event(
            ZONE_BEGIN_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            ON_MESSAGE_ZONE,
            PYTHON_ZONE_PHASE_BEGIN,
        ),
        python_zone_event(
            ZONE_INNER_BEGIN_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            RELAY_ZONE,
            PYTHON_ZONE_PHASE_BEGIN,
        ),
        python_zone_event(
            ZONE_INNER_END_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            RELAY_ZONE,
            PYTHON_ZONE_PHASE_END,
        ),
        python_zone_event(
            ZONE_END_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            ON_MESSAGE_ZONE,
            PYTHON_ZONE_PHASE_END,
        ),
    ]


def _nested_zone_publish_scenario() -> List[Event]:
    """
    The relay scenario with the republish moved inside a nested inner zone.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *node_init_events(),
        zone_declaration_event(
            INIT_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            DECL_SUB_PAYLOAD.format(zone_name=ON_MESSAGE_ZONE, topic=STEP0_TOPIC),
        ),
        relay_subscription_init(),
        _relay_pub_init(),
        rmw_take_event(
            ZONE_TAKE_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            ZONE_TAKEN_MSG,
            RELAY_RMW_SUB_HANDLE,
            source_timestamp=ZONE_TAKE_SOURCE_TS,
        ),
        python_zone_event(
            ZONE_BEGIN_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            ON_MESSAGE_ZONE,
            PYTHON_ZONE_PHASE_BEGIN,
        ),
        python_zone_event(
            ZONE_INNER_BEGIN_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            RELAY_ZONE,
            PYTHON_ZONE_PHASE_BEGIN,
        ),
        *publish_events(
            ZONE_PUBLISH_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            ZONE_PUBLISHED_MSG,
            RELAY_PUB_HANDLE,
            source_timestamp=ZONE_PUBLISH_SOURCE_TS,
        ),
        python_zone_event(
            ZONE_INNER_END_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            RELAY_ZONE,
            PYTHON_ZONE_PHASE_END,
        ),
        python_zone_event(
            ZONE_END_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            ON_MESSAGE_ZONE,
            PYTHON_ZONE_PHASE_END,
        ),
    ]


def _orphan_callback_scenario() -> List[Event]:
    """
    A callback pointer no *_callback_added event ever claimed.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *node_init_events(),
        callback_start_event(CB_RELAY_START_TS_NS, RELAY_VPID, RELAY_VTID, RELAY_CB),
        callback_end_event(CB_RELAY_END_TS_NS, RELAY_VPID, RELAY_VTID, RELAY_CB),
    ]


def _rmw_publish_only(rmw_pub_handle: Optional[int] = None) -> Event:
    """
    An rmw_publish with no preceding rclcpp/rcl_publish, as DDS-internal traffic emits.

    Args:
        rmw_pub_handle (Optional[int]): rmw publisher handle to carry, or None to omit
            the field entirely (the stock 1-arg tracepoint). Defaults to None.

    Returns:
        Event: The synthetic rmw_publish event.
    """
    fields = {FIELD_MESSAGE: PRODUCER_MSG, FIELD_TIMESTAMP: SOURCE_TS}
    if rmw_pub_handle is not None:
        fields[FIELD_RMW_PUBLISHER_HANDLE] = rmw_pub_handle
    return make_event(
        EVT_RMW_PUBLISH, PUBLISH_TS_NS, PRODUCER_VPID, PRODUCER_VTID, fields
    )


# ---- instances hold resolved objects, not handles ---- #


def test_publish_instance_holds_its_endpoint_not_a_handle():
    flow = build_flow_graph(_producer_scenario())
    pub = flow.publishes[0]
    assert pub.publisher is flow.publisher_endpoints[PRODUCER_PUB_HANDLE]
    assert pub.topic == STEP0_TOPIC
    assert pub.node is flow.nodes[PRODUCER_NODE_HANDLE]


def test_take_instance_holds_its_endpoint_not_a_handle():
    flow = build_flow_graph(_producer_scenario())
    take = flow.takes[0]
    assert take.subscription is flow.subscription_endpoints[RELAY_SUB_HANDLE]
    assert take.topic == STEP0_TOPIC


def test_callback_references_its_take_and_publishes_by_object():
    flow = build_flow_graph(_relay_scenario())
    cb = flow.callbacks[0]
    take = flow.takes[0]
    assert cb.trigger_take is take
    assert take.consumer_cb is cb
    assert len(cb.pubs) == 1
    assert cb.pubs[0].enclosing_cb is cb


def test_python_step_co_threading_invariant():
    flow = build_flow_graph(_relay_scenario())
    cb = flow.callbacks[0]
    assert cb.trigger_take is not None
    assert cb.trigger_take.vtid == cb.vtid == cb.pubs[0].vtid


# ---- the ownership-vetoed binding rule ---- #


def test_subscription_callback_binds_its_own_take():
    flow = build_flow_graph(_owner_veto_scenario())
    sub_cb = next(cb for cb in flow.callbacks if cb.callback == SUB_CB_PTR)
    assert sub_cb.trigger_take is flow.takes[0]
    assert sub_cb.kind == CB_KIND_SUBSCRIPTION
    assert sub_cb.owner is flow.subscription_endpoints[RELAY_SUB_HANDLE]


def test_timer_callback_does_not_claim_a_pending_subscription_take():
    # a timer starting first on the thread must leave the take for its own callback
    flow = build_flow_graph(_owner_veto_scenario())
    timer_cb = next(cb for cb in flow.callbacks if cb.callback == TIMER_CB_PTR)
    assert timer_cb.trigger_take is None
    assert timer_cb.kind == CB_KIND_TIMER
    assert timer_cb.owner is flow.timer_endpoints[TIMER_HANDLE]
    assert timer_cb.timer_period_ns == TIMER_PERIOD_NS
    assert flow.diagnostics.unbound_callbacks == 1
    # and the take still found its real consumer
    assert flow.takes[0].consumer_cb is not None


def test_interleaved_processes_bind_within_their_own_process():
    # take(P1), take(P2), cb(P1), cb(P2) in the merged stream: each slot is keyed by
    # (vpid, vtid), so neither process displaces the other's pending take
    flow = build_flow_graph(_two_process_scenario())
    relay_cb = next(cb for cb in flow.callbacks if cb.vpid == RELAY_VPID)
    producer_cb = next(cb for cb in flow.callbacks if cb.vpid == PRODUCER_VPID)
    assert relay_cb.trigger_take is not None
    assert producer_cb.trigger_take is not None
    assert relay_cb.trigger_take.vpid == RELAY_VPID
    assert producer_cb.trigger_take.vpid == PRODUCER_VPID
    assert flow.diagnostics.displaced_pending_takes == 0


# vtid values repeat across processes (tid reuse, pid namespaces), so callback-stack
# state must stay per-(vpid, vtid) rather than per-vtid.
def test_cb_stack_does_not_cross_contaminate_between_vpids_sharing_a_vtid():
    flow = build_flow_graph(_two_process_scenario())
    cb_relay = next(cb for cb in flow.callbacks if cb.callback == RELAY_CB)
    cb_producer = next(cb for cb in flow.callbacks if cb.callback == PRODUCER_CB)
    pub = next(p for p in flow.publishes if p.message == RELAY_PUBLISHED_MSG)

    assert pub.enclosing_cb is cb_relay
    assert cb_producer.pubs == []


def test_second_take_on_one_thread_counts_as_displaced():
    # ROS 2 should never emit this; the counter is how we would find out if it does
    flow = build_flow_graph(_displacement_scenario())
    assert flow.diagnostics.displaced_pending_takes == 1


# ---- callback identity ---- #


def test_declared_python_zone_resolves_kind_and_target_without_guessing():
    flow = build_flow_graph(_declared_zone_scenario())
    cb = flow.callbacks[0]
    assert cb.zone_name == DECL_SUB_ZONE
    assert cb.kind == CB_KIND_SUBSCRIPTION
    assert cb.target_label == STEP0_TOPIC
    assert cb.owner is flow.subscription_endpoints[RELAY_SUB_HANDLE]
    assert cb.trigger_take is flow.takes[0]


def test_declared_timer_zone_carries_its_period():
    flow = build_flow_graph(_declared_timer_zone_scenario())
    cb = flow.callbacks[0]
    assert cb.kind == CB_KIND_TIMER
    assert cb.timer_period_ns == TIMER_PERIOD_NS
    assert cb.trigger_take is None


def test_undeclared_python_zone_stays_a_plain_zone():
    # absence over inference: nothing in the trace says what this zone is
    flow = build_flow_graph(_undeclared_zone_scenario())
    cb = flow.callbacks[0]
    assert cb.kind == CB_KIND_ZONE
    assert cb.target_label == ON_MESSAGE_ZONE
    assert cb.owner is None


def test_nested_zones_synthesize_one_callback():
    flow = build_flow_graph(_nested_zone_scenario())
    assert len(flow.callbacks) == 1
    assert flow.callbacks[0].zone_name == ON_MESSAGE_ZONE
    assert flow.callbacks[0].duration_ns == ZONE_END_TS_NS - ZONE_BEGIN_TS_NS


def test_nested_zone_publish_attributes_to_the_outer_callback():
    flow = build_flow_graph(_nested_zone_publish_scenario())
    assert len(flow.callbacks) == 1, "nested zone must not synthesize its own CB"
    cb = flow.callbacks[0]
    assert cb.zone_name == ON_MESSAGE_ZONE
    assert len(cb.pubs) == 1
    assert cb.pubs[0].message == ZONE_PUBLISHED_MSG
    assert cb.pubs[0].enclosing_cb is cb


def test_cpp_callback_without_a_recorded_owner_is_unknown():
    flow = build_flow_graph(_orphan_callback_scenario())
    cb = flow.callbacks[0]
    assert cb.kind == CB_KIND_UNKNOWN
    assert cb.owner is None
    assert cb.trigger_take is None


# ---- cross-linking publishes to takes ---- #


def test_cross_process_link_via_matching_source_timestamp():
    flow = build_flow_graph(_producer_scenario())

    assert len(flow.edges) == 1
    edge = flow.edges[0]
    assert edge is not None
    assert edge.pub.topic == STEP0_TOPIC
    assert edge.take.topic == STEP0_TOPIC
    assert len(flow.diagnostics.unresolved_pub_handles) == 0
    assert len(flow.diagnostics.unresolved_rmwsub_handles) == 0


def test_cross_link_refuses_edge_when_publisher_init_is_missing():
    # No _producer_pub_init_pair() -- mimics a session started after the nodes launched.
    events = [
        *node_init_events(),
        relay_subscription_init(),
        *_producer_publish_and_relay_take(),
    ]
    flow = build_flow_graph(events)

    assert len(flow.edges) == 0, "an unconfirmable topic must not be guessed at"
    assert flow.takes[0].origin_pub is None
    assert flow.diagnostics.unresolved_pub_handles == {PRODUCER_PUB_HANDLE}


def test_cross_link_refuses_edge_when_subscription_init_is_missing():
    events = [
        *node_init_events(),
        *_producer_pub_init_pair(),
        *_producer_publish_and_relay_take(),
    ]
    flow = build_flow_graph(events)

    assert len(flow.edges) == 0
    assert flow.diagnostics.unresolved_rmwsub_handles == {RELAY_RMW_SUB_HANDLE}


# Both tracepoints emit 0 when no DDS publication instant was available, so 0 names no
# instant and must never be matched -- otherwise every such pub/take would collide.
def test_zero_source_timestamp_is_treated_as_unavailable():
    flow = build_flow_graph(_producer_scenario(source_timestamp=UNAVAILABLE_SOURCE_TS))

    assert flow.publishes[0].source_timestamp is None
    assert flow.takes[0].source_timestamp is None
    assert len(flow.edges) == 0
    assert flow.takes[0].origin_pub is None


# taken=0 is an empty reader queue: the tracepoint still fires and still carries a
# source_timestamp field, but no message was delivered, so there is nothing to link.
def test_empty_poll_is_not_linked_to_a_publish():
    flow = build_flow_graph(_producer_scenario(taken=0))

    assert flow.takes[0].taken == 0
    assert len(flow.edges) == 0
    assert flow.takes[0].origin_pub is None


def test_cross_link_rejects_edge_with_conflicting_known_topics():
    events = [
        *node_init_events(),
        *_producer_pub_init_pair(),
        relay_subscription_init(STEP1_TOPIC),
        *_producer_publish_and_relay_take(),
    ]
    flow = build_flow_graph(events)

    assert len(flow.edges) == 0
    assert flow.takes[0].origin_pub is None


# ---- publish endpoint resolution ---- #


# rmw-only publishes (DDS graph traffic) have no rcl publisher at all, so a missing
# handle is normal and must not be reported as a missing init event.
def test_publish_without_a_handle_is_not_flagged_as_unresolved():
    flow = build_flow_graph([
        *node_init_events(),
        relay_subscription_init(),
        _rmw_publish_only(),
    ])

    assert len(flow.publishes) == 1
    assert flow.publishes[0].publisher_handle is None
    assert flow.publishes[0].topic is None
    assert len(flow.diagnostics.unresolved_pub_handles) == 0


def test_rmw_publisher_handle_names_the_topic_without_an_rcl_publish():
    events = [
        *node_init_events(),
        *_producer_pub_init_pair(),
        relay_subscription_init(),
        # only the rmw handle is available to name this publish's topic
        _rmw_publish_only(PRODUCER_RMW_PUB_HANDLE),
        rmw_take_event(
            TAKE_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            PRODUCER_MSG,
            RELAY_RMW_SUB_HANDLE,
            source_timestamp=SOURCE_TS,
        ),
    ]
    flow = build_flow_graph(events)

    pub = flow.publishes[0]
    assert pub.publisher_handle is None
    assert pub.rmw_publisher_handle == PRODUCER_RMW_PUB_HANDLE
    assert pub.publisher is flow.publisher_endpoints[PRODUCER_PUB_HANDLE]
    assert pub.topic == STEP0_TOPIC
    assert len(flow.edges) == 1, "the rmw-named topic should confirm the link"
    assert len(flow.diagnostics.unresolved_pub_handles) == 0


# DDS graph traffic publishes at the rmw level with a handle no rcl_publisher_init
# covers, so it stays unnamed without being reported as a missing init event.
def test_unregistered_rmw_publisher_handle_is_not_flagged_as_unresolved():
    events = [
        *node_init_events(),
        relay_subscription_init(),
        _rmw_publish_only(UNREGISTERED_RMW_PUB_HANDLE),
    ]
    flow = build_flow_graph(events)

    pub = flow.publishes[0]
    assert pub.rmw_publisher_handle == UNREGISTERED_RMW_PUB_HANDLE
    assert pub.topic is None
    assert len(flow.diagnostics.unresolved_pub_handles) == 0


def test_rcl_publisher_handle_wins_over_the_rmw_handle():
    events = [
        *node_init_events(),
        *_producer_pub_init_pair(),
        _producer_pub_init(STEP1_TOPIC, OTHER_PUB_HANDLE, OTHER_RMW_PUB_HANDLE),
        # the two handles disagree about the topic; the rcl one is authoritative
        make_event(
            EVT_RCL_PUBLISH,
            PUBLISH_TS_NS,
            PRODUCER_VPID,
            PRODUCER_VTID,
            {FIELD_MESSAGE: PRODUCER_MSG, FIELD_PUBLISHER_HANDLE: PRODUCER_PUB_HANDLE},
        ),
        _rmw_publish_only(OTHER_RMW_PUB_HANDLE),
    ]
    flow = build_flow_graph(events)

    assert len(flow.publishes) == 1
    assert flow.publishes[0].topic == STEP0_TOPIC


def test_publish_resolves_through_its_rcl_handle():
    flow = build_flow_graph(_producer_scenario())

    assert flow.publishes[0].publisher is flow.publisher_endpoints[PRODUCER_PUB_HANDLE]
    assert len(flow.diagnostics.unresolved_pub_handles) == 0


def test_publish_falls_back_to_the_rmw_handle_when_rcl_publish_is_missing():
    # drop rcl_publish: only rclcpp_publish and rmw_publish remain, so the rcl handle
    # never reaches the instance and the rmw handle is the only route to the endpoint
    events = [event for event in _producer_scenario() if event.name != EVT_RCL_PUBLISH]
    flow = build_flow_graph(events)

    pub = flow.publishes[0]
    assert pub.publisher_handle is None
    assert pub.publisher is flow.publisher_endpoints[PRODUCER_PUB_HANDLE]
    assert pub.topic == STEP0_TOPIC
    assert len(flow.diagnostics.unresolved_pub_handles) == 0


def test_unresolvable_rmw_subscription_handle_is_recorded():
    events = [
        *node_init_events(),
        rmw_take_event(
            TAKE_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            RELAY_TAKEN_MSG,
            UNREGISTERED_RMW_SUB_HANDLE,
            source_timestamp=SOURCE_TS,
        ),
    ]
    flow = build_flow_graph(events)

    assert flow.takes[0].subscription is None
    assert UNREGISTERED_RMW_SUB_HANDLE in flow.diagnostics.unresolved_rmwsub_handles


def test_unresolvable_publisher_handle_is_recorded_not_guessed():
    events = [
        *node_init_events(),
        *publish_events(
            PUBLISH_TS_NS,
            PRODUCER_VPID,
            PRODUCER_VTID,
            PRODUCER_MSG,
            OTHER_PUB_HANDLE,
            source_timestamp=SOURCE_TS,
        ),
    ]
    flow = build_flow_graph(events)

    pub = flow.publishes[0]
    assert pub.publisher is None
    assert pub.topic is None
    assert pub.publisher_handle == OTHER_PUB_HANDLE
    assert OTHER_PUB_HANDLE in flow.diagnostics.unresolved_pub_handles


# ---- unconsumed takes ---- #


def test_empty_takes_are_not_counted_as_unconsumed():
    # an empty poll found no message, so no callback was ever owed to it
    flow = build_flow_graph(_producer_scenario(taken=0))

    assert flow.diagnostics.unconsumed_takes == 0


def test_a_taken_message_no_callback_claimed_is_counted():
    flow = build_flow_graph(_producer_scenario())

    assert flow.diagnostics.unconsumed_takes == 1


# ---- report ---- #


# ---- module-level helpers ---- #


def test_source_timestamp_or_none_returns_a_nonzero_timestamp():
    fields = {FIELD_TIMESTAMP: SOURCE_TS}

    assert source_timestamp_or_none(fields, FIELD_TIMESTAMP) == SOURCE_TS


def test_source_timestamp_or_none_maps_zero_to_none():
    fields = {FIELD_TIMESTAMP: UNAVAILABLE_SOURCE_TS}

    assert source_timestamp_or_none(fields, FIELD_TIMESTAMP) is None


def test_source_timestamp_or_none_maps_an_absent_field_to_none():
    assert source_timestamp_or_none({}, FIELD_TIMESTAMP) is None


def test_handler_label_is_stamped_with_the_zone_name_for_a_synthesized_cb():
    flow = build_flow_graph(_undeclared_zone_scenario())

    assert flow.callbacks[0].handler_label == ON_MESSAGE_ZONE


def test_handler_label_is_stamped_from_the_analysis_for_a_cpp_callback():
    flow = build_flow_graph(_orphan_callback_scenario())

    assert flow.callbacks[0].handler_label == hex(RELAY_CB)


def test_handler_label_falls_back_when_neither_callback_nor_zone_is_set():
    assert CB(0).handler_label == UNKNOWN_HANDLER_LABEL


def test_matched_delivery_produces_a_typed_edge():
    flow = build_flow_graph(_producer_scenario())
    assert len(flow.edges) == 1
    edge = flow.edges[0]
    assert edge.pub is flow.publishes[0]
    assert edge.take is flow.takes[0]
    assert edge.latency_ns == flow.takes[0].ts_rmw - flow.publishes[0].ts_rmw
    assert edge.cross_process is True


def test_both_sides_of_a_match_reference_each_other():
    flow = build_flow_graph(_producer_scenario())
    pub, take = flow.publishes[0], flow.takes[0]
    assert take.origin_pub is pub
    assert pub.takes == [take]


def test_a_take_with_no_resolved_topic_is_not_linked():
    events = [
        *node_init_events(),
        *_producer_pub_init_pair(),
        *publish_events(
            PUBLISH_TS_NS,
            PRODUCER_VPID,
            PRODUCER_VTID,
            PRODUCER_MSG,
            PRODUCER_PUB_HANDLE,
            SOURCE_TS,
        ),
        rmw_take_event(
            TAKE_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            RELAY_TAKEN_MSG,
            UNREGISTERED_RMW_PUB_HANDLE,
            SOURCE_TS,
        ),
    ]
    flow = build_flow_graph(events)
    assert len(flow.edges) == 0
    assert flow.takes[0].origin_pub is None


def test_an_empty_take_is_never_linked():
    flow = build_flow_graph(_producer_scenario(taken=0))
    assert len(flow.edges) == 0


def test_a_matching_timestamp_on_another_topic_does_not_link():
    # same source_timestamp, different topic: refused rather than matched by luck
    events = [
        *node_init_events(),
        relay_subscription_init(topic=STEP1_TOPIC),
        *_producer_pub_init_pair(),
        *publish_events(
            PUBLISH_TS_NS,
            PRODUCER_VPID,
            PRODUCER_VTID,
            PRODUCER_MSG,
            PRODUCER_PUB_HANDLE,
            SOURCE_TS,
        ),
        rmw_take_event(
            TAKE_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            RELAY_TAKEN_MSG,
            RELAY_RMW_SUB_HANDLE,
            SOURCE_TS,
        ),
    ]
    flow = build_flow_graph(events)
    assert len(flow.edges) == 0
