# Copyright 2026 Gritt Robotics Inc.

from typing import List, Tuple

from flow_graph_fixtures import (
    DECL_SUB_PAYLOAD,
    DECL_SUB_ZONE,
    DECL_TIMER_PAYLOAD,
    DECL_TIMER_ZONE,
    INIT_TS_NS,
    LINK_UID_SEPARATOR,
    PRODUCER_VPID,
    PRODUCER_VTID,
    PUB_LINK_PAYLOAD,
    RELAY_RMW_SUB_HANDLE,
    RELAY_VPID,
    RELAY_VTID,
    STEP0_TOPIC,
    TAKE_LINKS_PAYLOAD,
    TIMER_PERIOD_NS,
    TRUNCATED_TAKE_LINKS_PAYLOAD,
    build_flow_graph,
    node_init_events,
    relay_subscription_init,
)
from synthetic_events import python_zone_event, rmw_take_event, zone_declaration_event
from trace_instrumentation.trace_analysis.ctf.reader import Event
from trace_instrumentation.trace_analysis.python_zones import (
    PYTHON_ZONE_PHASE_BEGIN,
    PYTHON_ZONE_PHASE_END,
)

UID_A = 1
UID_B = 2
UNPUBLISHED_UID = 99

# First subscription invocation: one message taken, one uid published inside the zone.
# Pointers and timestamps stay distinct per invocation so a mis-key cannot match by luck.
FIRST_TAKEN_MSG = 222
FIRST_TAKE_SOURCE_TS = 7000
FIRST_TAKE_TS_NS = 1000
FIRST_ZONE_BEGIN_TS_NS = 1100
PUB_LINK_TS_NS = 1200
FIRST_ZONE_END_TS_NS = 1300

# Second subscription invocation, so two published uids can be taken together.
SECOND_TAKEN_MSG = 444
SECOND_TAKE_SOURCE_TS = 7200
SECOND_TAKE_TS_NS = 1350
SECOND_ZONE_BEGIN_TS_NS = 1400
SECOND_PUB_LINK_TS_NS = 1450
SECOND_ZONE_END_TS_NS = 1500

# Timer invocation that takes what the subscription invocations published.
TAKE_ZONE_BEGIN_TS_NS = 1550
TAKE_LINKS_TS_NS = 1600
TAKE_ZONE_END_TS_NS = 1650

# A second timer invocation, so one published uid can be taken twice.
SECOND_TAKE_ZONE_BEGIN_TS_NS = 1700
SECOND_TAKE_LINKS_TS_NS = 1750
SECOND_TAKE_ZONE_END_TS_NS = 1800

# A timer invocation that runs before anything was published.
EARLY_TAKE_ZONE_BEGIN_TS_NS = 500
EARLY_TAKE_LINKS_TS_NS = 550
EARLY_TAKE_ZONE_END_TS_NS = 600


def _take_links_payload(uids: List[int]) -> str:
    """
    Render a link-take payload naming several uids.

    Args:
        uids (List[int]): The uids taken, in declaration order.

    Returns:
        str: The `trace_link take` payload.
    """
    return TAKE_LINKS_PAYLOAD.format(
        uids=LINK_UID_SEPARATOR.join(str(uid) for uid in uids)
    )


def _zone_declarations(vpid: int, vtid: int) -> List[Event]:
    """
    Declare both instrumented zones in one process.

    Args:
        vpid (int): Process the zones run in.
        vtid (int): Thread the registration ran on.

    Returns:
        List[Event]: The subscription-zone and timer-zone declarations.
    """
    return [
        zone_declaration_event(
            INIT_TS_NS,
            vpid,
            vtid,
            DECL_SUB_PAYLOAD.format(zone_name=DECL_SUB_ZONE, topic=STEP0_TOPIC),
        ),
        zone_declaration_event(
            INIT_TS_NS,
            vpid,
            vtid,
            DECL_TIMER_PAYLOAD.format(
                zone_name=DECL_TIMER_ZONE, period_ns=TIMER_PERIOD_NS
            ),
        ),
    ]


def _prelude() -> List[Event]:
    """
    The registrations every scenario below hangs its zones off.

    Returns:
        List[Event]: Both node registrations, the relay's subscription, and the relay's
        zone declarations.
    """
    return [
        *node_init_events(),
        relay_subscription_init(),
        *_zone_declarations(RELAY_VPID, RELAY_VTID),
    ]


def _zone_invocation(
    vpid: int,
    vtid: int,
    zone_name: str,
    begin_ts_ns: int,
    end_ts_ns: int,
    declarations: List[Tuple[int, str]],
) -> List[Event]:
    """
    One declared-zone invocation, with link declarations fired from inside it.

    Args:
        vpid (int): Process the invocation runs in.
        vtid (int): Thread the invocation runs on.
        zone_name (str): The declared zone's name.
        begin_ts_ns (int): Zone-begin timestamp in nanoseconds.
        end_ts_ns (int): Zone-end timestamp in nanoseconds.
        declarations (List[Tuple[int, str]]): (timestamp, payload) per link declaration.

    Returns:
        List[Event]: The zone begin, the declarations, and the zone end.
    """
    return [
        python_zone_event(begin_ts_ns, vpid, vtid, zone_name, PYTHON_ZONE_PHASE_BEGIN),
        *[
            zone_declaration_event(ts_ns, vpid, vtid, payload)
            for ts_ns, payload in declarations
        ],
        python_zone_event(end_ts_ns, vpid, vtid, zone_name, PYTHON_ZONE_PHASE_END),
    ]


def _subscription_invocation(
    take_ts_ns: int,
    message: int,
    source_timestamp: int,
    begin_ts_ns: int,
    end_ts_ns: int,
    declarations: List[Tuple[int, str]],
) -> List[Event]:
    """
    One message delivered to the relay and handled inside its declared subscription zone.

    Args:
        take_ts_ns (int): rmw_take timestamp in nanoseconds.
        message (int): Message buffer pointer.
        source_timestamp (int): DDS timestamp carried by the message.
        begin_ts_ns (int): Zone-begin timestamp in nanoseconds.
        end_ts_ns (int): Zone-end timestamp in nanoseconds.
        declarations (List[Tuple[int, str]]): (timestamp, payload) per link declaration.

    Returns:
        List[Event]: The take followed by the zone invocation.
    """
    return [
        rmw_take_event(
            take_ts_ns,
            RELAY_VPID,
            RELAY_VTID,
            message,
            RELAY_RMW_SUB_HANDLE,
            source_timestamp=source_timestamp,
        ),
        *_zone_invocation(
            RELAY_VPID,
            RELAY_VTID,
            DECL_SUB_ZONE,
            begin_ts_ns,
            end_ts_ns,
            declarations,
        ),
    ]


def _relay_timer_invocation(
    begin_ts_ns: int, end_ts_ns: int, declarations: List[Tuple[int, str]]
) -> List[Event]:
    """
    One invocation of the relay's declared timer zone.

    Args:
        begin_ts_ns (int): Zone-begin timestamp in nanoseconds.
        end_ts_ns (int): Zone-end timestamp in nanoseconds.
        declarations (List[Tuple[int, str]]): (timestamp, payload) per link declaration.

    Returns:
        List[Event]: The zone invocation.
    """
    return _zone_invocation(
        RELAY_VPID, RELAY_VTID, DECL_TIMER_ZONE, begin_ts_ns, end_ts_ns, declarations
    )


def _first_pub_link_invocation() -> List[Event]:
    """
    The relay's subscription zone taking one message and publishing UID_A inside it.

    Returns:
        List[Event]: The take and the zone invocation.
    """
    return _subscription_invocation(
        FIRST_TAKE_TS_NS,
        FIRST_TAKEN_MSG,
        FIRST_TAKE_SOURCE_TS,
        FIRST_ZONE_BEGIN_TS_NS,
        FIRST_ZONE_END_TS_NS,
        [(PUB_LINK_TS_NS, PUB_LINK_PAYLOAD.format(uid=UID_A))],
    )


def _pub_link_inside_subscription_scenario() -> List[Event]:
    """
    A uid published from inside the subscription callback that took the message.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [*_prelude(), *_first_pub_link_invocation()]


def _pub_link_then_take_scenario() -> List[Event]:
    """
    A uid published in a subscription callback and taken in a later timer callback.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *_prelude(),
        *_first_pub_link_invocation(),
        *_relay_timer_invocation(
            TAKE_ZONE_BEGIN_TS_NS,
            TAKE_ZONE_END_TS_NS,
            [(TAKE_LINKS_TS_NS, _take_links_payload([UID_A]))],
        ),
    ]


def _two_pub_links_one_take_scenario() -> List[Event]:
    """
    Two subscription invocations publishing one uid each, taken together downstream.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *_prelude(),
        *_first_pub_link_invocation(),
        *_subscription_invocation(
            SECOND_TAKE_TS_NS,
            SECOND_TAKEN_MSG,
            SECOND_TAKE_SOURCE_TS,
            SECOND_ZONE_BEGIN_TS_NS,
            SECOND_ZONE_END_TS_NS,
            [(SECOND_PUB_LINK_TS_NS, PUB_LINK_PAYLOAD.format(uid=UID_B))],
        ),
        *_relay_timer_invocation(
            TAKE_ZONE_BEGIN_TS_NS,
            TAKE_ZONE_END_TS_NS,
            [(TAKE_LINKS_TS_NS, _take_links_payload([UID_A, UID_B]))],
        ),
    ]


def _one_pub_link_two_takes_scenario() -> List[Event]:
    """
    One published uid taken by two separate timer invocations.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *_prelude(),
        *_first_pub_link_invocation(),
        *_relay_timer_invocation(
            TAKE_ZONE_BEGIN_TS_NS,
            TAKE_ZONE_END_TS_NS,
            [(TAKE_LINKS_TS_NS, _take_links_payload([UID_A]))],
        ),
        *_relay_timer_invocation(
            SECOND_TAKE_ZONE_BEGIN_TS_NS,
            SECOND_TAKE_ZONE_END_TS_NS,
            [(SECOND_TAKE_LINKS_TS_NS, _take_links_payload([UID_A]))],
        ),
    ]


def _take_unpublished_scenario() -> List[Event]:
    """
    A link take naming a uid no declaration ever published.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *_prelude(),
        *_relay_timer_invocation(
            TAKE_ZONE_BEGIN_TS_NS,
            TAKE_ZONE_END_TS_NS,
            [(TAKE_LINKS_TS_NS, _take_links_payload([UNPUBLISHED_UID]))],
        ),
    ]


def _cross_process_uid_scenario() -> List[Event]:
    """
    UID_A published in the producer process and taken in the relay process.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *_prelude(),
        *_zone_declarations(PRODUCER_VPID, PRODUCER_VTID),
        *_zone_invocation(
            PRODUCER_VPID,
            PRODUCER_VTID,
            DECL_TIMER_ZONE,
            FIRST_ZONE_BEGIN_TS_NS,
            FIRST_ZONE_END_TS_NS,
            [(PUB_LINK_TS_NS, PUB_LINK_PAYLOAD.format(uid=UID_A))],
        ),
        *_relay_timer_invocation(
            TAKE_ZONE_BEGIN_TS_NS,
            TAKE_ZONE_END_TS_NS,
            [(TAKE_LINKS_TS_NS, _take_links_payload([UID_A]))],
        ),
    ]


def _take_before_pub_link_scenario() -> List[Event]:
    """
    A link take that runs before the link publish it names.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *_prelude(),
        *_relay_timer_invocation(
            EARLY_TAKE_ZONE_BEGIN_TS_NS,
            EARLY_TAKE_ZONE_END_TS_NS,
            [(EARLY_TAKE_LINKS_TS_NS, _take_links_payload([UID_A]))],
        ),
        *_first_pub_link_invocation(),
    ]


def _truncated_take_scenario() -> List[Event]:
    """
    A link take the emitter had to truncate at its uid-list cap.

    Returns:
        List[Event]: The full synthetic event sequence.
    """
    return [
        *_prelude(),
        *_first_pub_link_invocation(),
        *_relay_timer_invocation(
            TAKE_ZONE_BEGIN_TS_NS,
            TAKE_ZONE_END_TS_NS,
            [(TAKE_LINKS_TS_NS, TRUNCATED_TAKE_LINKS_PAYLOAD.format(uids=UID_A))],
        ),
    ]


def test_pub_link_binds_to_the_callback_it_fired_inside():
    flow = build_flow_graph(_pub_link_inside_subscription_scenario())
    linked_pub = flow.linked_pubs[0]
    assert linked_pub.uid == UID_A
    assert linked_pub.cb is flow.callbacks[0]
    # the property that makes a uid traceable back to an rmw_take
    assert linked_pub.origin_take is flow.takes[0]


def test_take_resolves_to_the_pub_link_and_links_both_ways():
    flow = build_flow_graph(_pub_link_then_take_scenario())
    linked_pub, linked_take = flow.linked_pubs[0], flow.linked_takes[0]
    assert linked_take.origin_linked_pubs == [linked_pub]
    assert linked_pub.linked_takes == [linked_take]
    assert linked_take.cb is flow.callbacks[1]
    assert len(linked_take.unresolved_uids) == 0


def test_fan_in_resolves_every_named_uid():
    flow = build_flow_graph(_two_pub_links_one_take_scenario())
    linked_take = flow.linked_takes[0]
    assert [pub.uid for pub in linked_take.origin_linked_pubs] == [UID_A, UID_B]


def test_fan_out_records_every_take_of_one_pub_link():
    flow = build_flow_graph(_one_pub_link_two_takes_scenario())
    assert len(flow.linked_pubs[0].linked_takes) == 2


def test_taking_a_uid_that_was_never_published_is_recorded_not_guessed():
    flow = build_flow_graph(_take_unpublished_scenario())
    linked_take = flow.linked_takes[0]
    assert len(linked_take.origin_linked_pubs) == 0
    assert linked_take.unresolved_uids == [UNPUBLISHED_UID]
    assert flow.diagnostics.unresolved_link_takes == 1


def test_a_uid_published_in_another_process_does_not_resolve():
    # uids are process-local, so the same number in another process is a different uid
    flow = build_flow_graph(_cross_process_uid_scenario())
    assert len(flow.linked_takes[0].origin_linked_pubs) == 0
    assert flow.diagnostics.unresolved_link_takes == 1


def test_a_take_before_its_pub_link_does_not_resolve():
    # chronological resolution rejects a take of a later publish, with no extra check
    flow = build_flow_graph(_take_before_pub_link_scenario())
    assert len(flow.linked_takes[0].origin_linked_pubs) == 0
    assert flow.diagnostics.unresolved_link_takes == 1


def test_truncated_takes_are_counted():
    flow = build_flow_graph(_truncated_take_scenario())
    assert flow.linked_takes[0].truncated is True
    assert flow.diagnostics.truncated_link_takes == 1


def test_a_link_declaration_outside_any_callback_is_still_recorded():
    # a link publish the sweep cannot attribute is kept unbound rather than dropped
    events = [
        *_prelude(),
        zone_declaration_event(
            PUB_LINK_TS_NS, RELAY_VPID, RELAY_VTID, PUB_LINK_PAYLOAD.format(uid=UID_A)
        ),
    ]
    flow = build_flow_graph(events)
    assert flow.linked_pubs[0].cb is None
    assert flow.linked_pubs[0].origin_take is None
