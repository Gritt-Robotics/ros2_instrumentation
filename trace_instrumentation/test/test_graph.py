# Copyright 2026 Gritt Robotics Inc.

from trace_instrumentation.trace_analysis.graph import (
    CB,
    CB_KIND_SUBSCRIPTION,
    CB_KIND_ZONE,
    BuildState,
    Diagnostics,
    Edge,
    LinkedPub,
    LinkedTake,
    Node,
    Pub,
    PublisherEndpoint,
    Step,
    SubscriptionEndpoint,
    Take,
    TimerEndpoint,
    Topic,
    Traversal,
    ZoneDeclaration,
)

NODE_HANDLE = 900
PUB_HANDLE = 1
SUB_HANDLE = 2
TIMER_HANDLE = 3
RMW_PUB_HANDLE = 101
RMW_SUB_HANDLE = 102
QUEUE_DEPTH = 10
TIMER_PERIOD_NS = 100_000_000
VPID = 100
TOPIC_NAME = "/image"

PUB_ID = 0
TAKE_ID = 0
CB_ID = 0
MESSAGE_PTR = 333
SOURCE_TS = 5000
PUB_TS_NS = 1000
TAKE_TS_NS = 1500
CB_START_TS_NS = 1600
CB_END_TS_NS = 1900
DIRECT_REPUBLISH_TS_NS = 2000
RELAY_CB_START_TS_NS = 2400
RELAYED_PUB_TS_NS = 2600
OTHER_VPID = 200

UID_A = 1
UID_B = 2
UNPUBLISHED_UID = 99
LINK_TS_NS = 1700
DECL_ZONE = "_on_image"


def _node(label: str = "/producer") -> Node:
    return Node(
        handle=NODE_HANDLE,
        name=label.lstrip("/"),
        namespace="/",
        label=label,
        vpid=VPID,
    )


def test_node_defaults_to_empty_endpoint_lists():
    node = _node()
    assert node.publishers == []
    assert node.subscriptions == []
    assert node.timers == []
    assert node.services == []


def test_endpoints_are_hashable_and_identity_compared():
    node = _node()
    topic = Topic(name=TOPIC_NAME)
    first = PublisherEndpoint(
        handle=PUB_HANDLE,
        rmw_handle=RMW_PUB_HANDLE,
        node=node,
        topic=topic,
        queue_depth=QUEUE_DEPTH,
    )
    second = PublisherEndpoint(
        handle=PUB_HANDLE,
        rmw_handle=RMW_PUB_HANDLE,
        node=node,
        topic=topic,
        queue_depth=QUEUE_DEPTH,
    )
    # identity, not field-wise: two endpoints with equal fields are still two endpoints
    assert first != second
    assert first == first
    assert len({first, second}) == 2


def test_topic_holds_many_publishers_and_subscribers():
    topic = Topic(name=TOPIC_NAME)
    producer = _node("/producer")
    consumer_a = _node("/consumer_a")
    consumer_b = _node("/consumer_b")
    publisher = PublisherEndpoint(
        handle=PUB_HANDLE,
        rmw_handle=RMW_PUB_HANDLE,
        node=producer,
        topic=topic,
        queue_depth=QUEUE_DEPTH,
    )
    subscribers = [
        SubscriptionEndpoint(
            handle=SUB_HANDLE + offset,
            rmw_handle=RMW_SUB_HANDLE + offset,
            node=node,
            topic=topic,
            queue_depth=QUEUE_DEPTH,
        )
        for offset, node in enumerate((consumer_a, consumer_b))
    ]
    topic.publishers.append(publisher)
    topic.subscribers.extend(subscribers)

    assert len(topic.publishers) == 1
    assert len(topic.subscribers) == 2
    assert topic.subscribers[0].topic is topic


def test_node_endpoint_cycle_reprs_without_recursion():
    node = _node()
    topic = Topic(name=TOPIC_NAME)
    publisher = PublisherEndpoint(
        handle=PUB_HANDLE,
        rmw_handle=RMW_PUB_HANDLE,
        node=node,
        topic=topic,
        queue_depth=QUEUE_DEPTH,
    )
    node.publishers.append(publisher)
    topic.publishers.append(publisher)
    assert "PublisherEndpoint" in repr(node)


def test_timer_endpoint_allows_unlinked_node():
    # rclcpp_timer_link_node may be absent from a trace, so node is Optional
    timer = TimerEndpoint(handle=TIMER_HANDLE, node=None, period_ns=TIMER_PERIOD_NS)
    assert timer.node is None
    assert timer.period_ns == TIMER_PERIOD_NS


def _publisher_endpoint(topic: Topic, node: Node) -> PublisherEndpoint:
    return PublisherEndpoint(
        handle=PUB_HANDLE,
        rmw_handle=RMW_PUB_HANDLE,
        node=node,
        topic=topic,
        queue_depth=QUEUE_DEPTH,
    )


def _subscription_endpoint(topic: Topic, node: Node) -> SubscriptionEndpoint:
    return SubscriptionEndpoint(
        handle=SUB_HANDLE,
        rmw_handle=RMW_SUB_HANDLE,
        node=node,
        topic=topic,
        queue_depth=QUEUE_DEPTH,
    )


def test_pub_topic_and_node_resolve_through_endpoint():
    node = _node()
    topic = Topic(name=TOPIC_NAME)
    pub = Pub(id=PUB_ID, publisher=_publisher_endpoint(topic, node))
    assert pub.topic == TOPIC_NAME
    assert pub.node is node


def test_pub_topic_is_none_when_endpoint_unresolved():
    # a publish whose handle no rcl_publisher_init named keeps the raw handle only
    pub = Pub(id=PUB_ID, publisher=None, publisher_handle=PUB_HANDLE)
    assert pub.topic is None
    assert pub.node is None
    assert pub.publisher_handle == PUB_HANDLE


def test_take_topic_and_node_resolve_through_endpoint():
    node = _node("/consumer")
    topic = Topic(name=TOPIC_NAME)
    take = Take(id=TAKE_ID, subscription=_subscription_endpoint(topic, node))
    assert take.topic == TOPIC_NAME
    assert take.node is node


def test_cb_duration_is_none_until_closed():
    cb = CB(id=CB_ID, kind=CB_KIND_ZONE, ts_start=CB_START_TS_NS)
    assert cb.duration_ns is None
    cb.ts_end = CB_END_TS_NS
    assert cb.duration_ns == CB_END_TS_NS - CB_START_TS_NS


def test_edge_latency_and_cross_process():
    pub = Pub(id=PUB_ID, vpid=VPID, ts_rmw=PUB_TS_NS, source_timestamp=SOURCE_TS)
    take = Take(id=TAKE_ID, vpid=OTHER_VPID, ts_rmw=TAKE_TS_NS, taken=1)
    edge = Edge(pub=pub, take=take)
    assert edge.latency_ns == TAKE_TS_NS - PUB_TS_NS
    assert edge.cross_process is True


def test_edge_is_same_process_within_one_vpid():
    pub = Pub(id=PUB_ID, vpid=VPID, ts_rmw=PUB_TS_NS)
    take = Take(id=TAKE_ID, vpid=VPID, ts_rmw=TAKE_TS_NS, taken=1)
    assert Edge(pub=pub, take=take).cross_process is False


def test_instance_cycle_is_hashable_both_ways():
    pub = Pub(id=PUB_ID)
    take = Take(id=TAKE_ID, origin_pub=pub, ts_rmw=TAKE_TS_NS)
    pub.takes.append(take)
    # the cycle that makes eq=True unusable; identity hashing must survive it
    assert len({pub, take}) == 2
    assert take.origin_pub is pub
    assert pub.takes[0] is take


def test_cb_holds_separate_direct_and_linked_output_lists():
    cb = CB(id=CB_ID, kind=CB_KIND_SUBSCRIPTION)
    cb.pubs.append(Pub(id=PUB_ID))
    assert len(cb.pubs) == 1
    assert cb.linked_pubs == []
    assert cb.linked_takes == []


def test_linked_pub_origin_take_follows_its_callback():
    take = Take(id=TAKE_ID, taken=1)
    cb = CB(id=CB_ID, kind=CB_KIND_SUBSCRIPTION, trigger_take=take)
    linked_pub = LinkedPub(id=0, uid=UID_A, cb=cb, ts_ns=LINK_TS_NS)
    assert linked_pub.origin_take is take


def test_linked_pub_origin_take_is_none_without_a_bound_callback():
    linked_pub = LinkedPub(id=0, uid=UID_A, cb=None, ts_ns=LINK_TS_NS)
    assert linked_pub.origin_take is None
    unbound_cb = CB(id=CB_ID, kind=CB_KIND_ZONE, trigger_take=None)
    assert (
        LinkedPub(id=1, uid=UID_A, cb=unbound_cb, ts_ns=LINK_TS_NS).origin_take is None
    )


def test_linked_take_supports_fan_in_and_records_unresolved_uids():
    first = LinkedPub(id=0, uid=UID_A, ts_ns=LINK_TS_NS)
    second = LinkedPub(id=1, uid=UID_B, ts_ns=LINK_TS_NS)
    linked_take = LinkedTake(
        id=0, uids=[UID_A, UID_B, UNPUBLISHED_UID], ts_ns=LINK_TS_NS
    )
    linked_take.origin_linked_pubs.extend((first, second))
    linked_take.unresolved_uids.append(UNPUBLISHED_UID)
    assert len(linked_take.origin_linked_pubs) == 2
    assert linked_take.unresolved_uids == [UNPUBLISHED_UID]
    assert linked_take.truncated is False


def test_linked_pub_supports_fan_out():
    linked_pub = LinkedPub(id=0, uid=UID_A, ts_ns=LINK_TS_NS)
    linked_pub.linked_takes.extend((
        LinkedTake(id=0, uids=[UID_A], ts_ns=LINK_TS_NS),
        LinkedTake(id=1, uids=[UID_A], ts_ns=LINK_TS_NS),
    ))
    assert len(linked_pub.linked_takes) == 2


def test_step_callback_time_ns_is_none_at_the_sink():
    pub = Pub(id=PUB_ID, ts_rmw=PUB_TS_NS)
    cb = CB(id=CB_ID, kind=CB_KIND_SUBSCRIPTION, ts_start=CB_START_TS_NS)
    take = Take(id=TAKE_ID, ts_rmw=TAKE_TS_NS, taken=1, consumer_cb=cb)
    sink_step = Step(topic=TOPIC_NAME, next_topic=None, pub=pub, take=take)
    assert sink_step.is_indirect is False
    assert sink_step.linked_pub is None
    assert sink_step.linked_take is None
    assert sink_step.callback_time_ns is None
    # take_to_callback_start_ns stays reported at the sink, unlike callback_time_ns,
    # since it sits inside chain_latency_ns under the take terminator.
    assert sink_step.take_to_callback_start_ns == CB_START_TS_NS - TAKE_TS_NS


def test_step_take_to_callback_start_ns_is_none_without_a_take():
    pub = Pub(id=PUB_ID, ts_rmw=PUB_TS_NS)
    pub_terminated_step = Step(topic=TOPIC_NAME, next_topic=None, pub=pub, take=None)
    assert pub_terminated_step.take_to_callback_start_ns is None


def test_step_take_to_callback_start_ns_is_none_without_a_bound_callback():
    pub = Pub(id=PUB_ID, ts_rmw=PUB_TS_NS)
    take = Take(id=TAKE_ID, ts_rmw=TAKE_TS_NS, taken=1, consumer_cb=None)
    unbound_step = Step(topic=TOPIC_NAME, next_topic=None, pub=pub, take=take)
    assert unbound_step.take_to_callback_start_ns is None


def test_step_records_direct_and_linked_kinds():
    pub = Pub(id=PUB_ID, ts_rmw=PUB_TS_NS)
    cb = CB(id=CB_ID, kind=CB_KIND_SUBSCRIPTION, ts_start=CB_START_TS_NS)
    take = Take(id=TAKE_ID, ts_rmw=TAKE_TS_NS, taken=1, consumer_cb=cb)

    direct_pub = Pub(id=PUB_ID + 1, ts_rmw=DIRECT_REPUBLISH_TS_NS)
    direct = Step(
        topic=TOPIC_NAME, next_topic="/out", pub=pub, take=take, next_pub=direct_pub
    )
    assert direct.is_indirect is False
    assert direct.linked_pub is None
    assert direct.linked_take is None
    assert direct.take_to_callback_start_ns == CB_START_TS_NS - TAKE_TS_NS
    assert direct.callback_time_ns == DIRECT_REPUBLISH_TS_NS - CB_START_TS_NS
    assert direct.indirect_wait_ns is None
    assert direct.relay_callback_time_ns is None

    linked_pub = LinkedPub(id=0, uid=UID_A, ts_ns=LINK_TS_NS)
    relay_cb = CB(id=CB_ID + 1, kind=CB_KIND_ZONE, ts_start=RELAY_CB_START_TS_NS)
    linked_take = LinkedTake(id=0, uids=[UID_A], ts_ns=LINK_TS_NS + 50, cb=relay_cb)
    relayed_pub = Pub(id=PUB_ID + 2, ts_rmw=RELAYED_PUB_TS_NS)
    linked = Step(
        topic=TOPIC_NAME,
        next_topic="/out",
        pub=pub,
        take=take,
        is_indirect=True,
        linked_pub=linked_pub,
        linked_take=linked_take,
        next_pub=relayed_pub,
    )
    assert linked.linked_pub is linked_pub
    assert linked.linked_take is linked_take
    assert linked.take_to_callback_start_ns == CB_START_TS_NS - TAKE_TS_NS
    assert linked.callback_time_ns == LINK_TS_NS - CB_START_TS_NS
    assert linked.indirect_wait_ns == RELAY_CB_START_TS_NS - LINK_TS_NS
    assert linked.relay_callback_time_ns == RELAYED_PUB_TS_NS - RELAY_CB_START_TS_NS


def test_traversal_defaults_to_no_steps():
    traversal = Traversal()
    assert traversal.steps == []
    assert traversal.total_latency_ns is None
    assert traversal.callback_start_latency_ns is None


def test_build_state_slots_start_empty():
    state = BuildState()
    assert len(state.pending_pub) == 0
    assert len(state.pending_take) == 0
    assert len(state.thread_take) == 0
    # defaultdicts: touching a missing thread key yields an empty stack, not a KeyError
    assert state.cb_stack[(VPID, 10)] == []
    assert state.python_zone_depth[(VPID, 10)] == 0


def test_diagnostics_counters_start_at_zero():
    diagnostics = Diagnostics()
    assert diagnostics.displaced_pending_takes == 0
    assert diagnostics.unbound_callbacks == 0
    assert diagnostics.unconsumed_takes == 0
    assert diagnostics.unresolved_link_takes == 0
    assert diagnostics.truncated_link_takes == 0
    assert len(diagnostics.unresolved_pub_handles) == 0
    assert len(diagnostics.unresolved_rmwsub_handles) == 0


def test_zone_declaration_carries_kind_and_target():
    declaration = ZoneDeclaration(
        zone_name=DECL_ZONE,
        kind=CB_KIND_SUBSCRIPTION,
        target=TOPIC_NAME,
        period_ns=None,
    )
    assert declaration.kind == CB_KIND_SUBSCRIPTION
    assert declaration.target == TOPIC_NAME
