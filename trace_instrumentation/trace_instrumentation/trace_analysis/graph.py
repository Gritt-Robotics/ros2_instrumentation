# Copyright 2026 Gritt Robotics Inc.

"""
graph.py
========

This module defines the data structures that represent the reconstructed flow graph of a ROS 2 system defined by the trace events in the third_party/ros2_tracing repository.

`flow_graph.py` holds the algorithm that populates these and declares no type of its own.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Dict, List, NamedTuple, Optional, Set, Tuple, Union

from trace_instrumentation.trace_analysis.metrics.stats import (
    StatsSummary,
    fmt_hex_or_str,
)

CB_KIND_SUBSCRIPTION = "subscription"
CB_KIND_TIMER = "timer"
CB_KIND_SERVICE = "service"
CB_KIND_ZONE = "zone"
CB_KIND_UNKNOWN = "unknown"

# A chain's last step declares where a traversal ends: at the publish onto that topic,
# or -- the default -- at a subscriber's take of it.
CHAIN_TERMINATOR_PUB = "pub"
CHAIN_TERMINATOR_TAKE = "take"
CHAIN_TERMINATORS = frozenset({CHAIN_TERMINATOR_PUB, CHAIN_TERMINATOR_TAKE})

# Stands in for the owning node of an invocation whose node never resolved.
UNKNOWN_NODE_LABEL = "<unknown>"
# Stands in for the handler of an invocation carrying neither a callback nor a zone name.
UNKNOWN_HANDLER_LABEL = "<unknown callback>"

# (node label, callback kind, target label, handler label) -- see CB.group_key.
CallbackKey = Tuple[str, str, str, str]


@dataclass(slots=True, eq=False)
class Topic:
    """One topic name, with every endpoint the trace registered against it."""

    name: str  # Fully-qualified topic name.
    publishers: List[PublisherEndpoint] = dc_field(
        default_factory=list
    )  # Every publisher registered on this topic.
    subscribers: List[SubscriptionEndpoint] = dc_field(
        default_factory=list
    )  # Every subscription registered on it.


@dataclass(slots=True, eq=False)
class Node:
    """One ROS node, from `ros2:rcl_node_init`."""

    handle: int  # The node's `rcl_node_t` handle.
    name: str  # Node name, without namespace.
    namespace: str  # Node namespace
    label: str  # Precomputed "/namespace/name" display label.
    vpid: Optional[int] = None  # Process id the node was initialized in.
    publishers: List[PublisherEndpoint] = dc_field(
        default_factory=list
    )  # publishers owned by this node
    subscriptions: List[SubscriptionEndpoint] = dc_field(
        default_factory=list
    )  # subscriptions owned by this node
    timers: List[TimerEndpoint] = dc_field(
        default_factory=list
    )  # timers owned with this node
    services: List[ServiceEndpoint] = dc_field(
        default_factory=list
    )  # services owned by this node


@dataclass(slots=True, eq=False)
class PublisherEndpoint:
    """One publisher registration, from `ros2:rcl_publisher_init`."""

    handle: int  # rcl publisher handle
    rmw_handle: Optional[int]  # rmw-layer publisher handle, if the trace named it
    node: Node  # Owning node
    topic: Topic  # Topic published to
    queue_depth: Optional[int] = None  # Configured QoS history depth
    gid: Optional[List[int]] = None  # DDS GID, from `ros2:rmw_publisher_init`
    pubs: List[Pub] = dc_field(
        default_factory=list
    )  # Publish instances issued through this endpoint


@dataclass(slots=True, eq=False)
class SubscriptionEndpoint:
    """One subscription registration, from `ros2:rcl_subscription_init`."""

    handle: int  # rcl subscription handle
    rmw_handle: Optional[int]  # rmw-layer subscription handle, if the trace named it
    node: Node  # Owning node
    topic: Topic  # Topic subscribed to
    queue_depth: Optional[int] = None  # Configured QoS history depth
    gid: Optional[List[int]] = None  # DDS GID, from `ros2:rmw_subscription_init`
    takes: List[Take] = dc_field(
        default_factory=list
    )  # Take instances delivered through this endpoint


@dataclass(slots=True, eq=False)
class TimerEndpoint:
    """One timer registration, from `ros2:rcl_timer_init`."""

    handle: int  # Timer handle.
    node: Optional[Node] = (
        None  # Owning node; None when no `ros2:rclcpp_timer_link_node` event linked it.
    )
    period_ns: Optional[int] = None  # Configured period in nanoseconds.


@dataclass(slots=True, eq=False)
class ServiceEndpoint:
    """One service registration, from `ros2:rcl_service_init`."""

    handle: int  # Service handle.
    name: str  # Fully-qualified service name.
    node: Optional[Node] = None  # Owning node, when the trace named it.


# What a callback invocation can be attributed to.
Endpoint = Union[SubscriptionEndpoint, TimerEndpoint, ServiceEndpoint]


@dataclass(slots=True, eq=False)
class Pub:
    """One published message instance, assembled from the rclcpp_publish/rcl_publish/
    rmw_publish events sharing the same (vpid, message) key.
    """

    id: int  # Index into FlowGraph.publishes; stable identity for report rows.
    vpid: Optional[int] = None  # Publishing process.
    vtid: Optional[int] = None  # Thread that ran the publish chain.
    procname: str = ""  # Publishing process name.
    message: Optional[int] = None  # Message object address; joins this chain's events.
    publisher_handle: Optional[int] = (
        None  # Raw rcl handle, from rcl_publish. Retained so an endpoint-less publish can still be reported by handle.
    )
    rmw_publisher_handle: Optional[int] = (
        None  # Raw rmw handle; present only on the vendored 3-arg rmw_publish tracepoint.
    )
    publisher: Optional[PublisherEndpoint] = (
        None  # Resolved endpoint; None when no rcl_publisher_init named this handle.
    )
    ts_rclcpp: Optional[int] = None  # rclcpp_publish timestamp (ns).
    ts_rcl: Optional[int] = None  # rcl_publish timestamp (ns).
    ts_rmw: Optional[int] = None  # rmw_publish timestamp (ns) -- the DDS write.
    source_timestamp: Optional[int] = (
        None  # DDS publication time; the cross-process key.
    )
    enclosing_cb: Optional[CB] = (
        None  # The callback invocation this publish was issued from.
    )
    takes: List[Take] = dc_field(
        default_factory=list
    )  # Takes that received this publish.

    @property
    def topic(self) -> Optional[str]:
        """
        The topic this publish went to.

        Returns:
            Optional[str]: The topic name, or None when the endpoint is unresolved.
        """
        return None if self.publisher is None else self.publisher.topic.name

    @property
    def topic_label(self) -> str:
        """
        This publish's topic for display, falling back to its raw handle.

        Returns:
            str: The topic name, else the unresolved publisher handle in hex.
        """
        # Unlike `topic`, never None -- keeps unresolved instances distinguishable by
        # handle in report/grouping keys instead of collapsing them under one bucket.
        return (
            self.topic
            if self.topic is not None
            else fmt_hex_or_str(self.publisher_handle)
        )

    @property
    def node(self) -> Optional[Node]:
        """
        The node that issued this publish.

        Returns:
            Optional[Node]: The owning node, or None when the endpoint is unresolved.
        """
        return None if self.publisher is None else self.publisher.node


@dataclass(slots=True, eq=False)
class Take:
    """One delivered message instance, assembled from the rmw_take/rcl_take/rclcpp_take
    events sharing the same (vpid, message) key.
    """

    id: int  # Index into FlowGraph.takes.
    vpid: Optional[int] = None  # Subscribing process.
    vtid: Optional[int] = None  # Executor thread that took the message.
    procname: str = ""  # Subscribing process name.
    message: Optional[int] = None  # Destination buffer address; joins the take chain.
    rmw_sub: Optional[int] = (
        None  # Raw rmw subscription handle, from rmw_take. Retained so an endpoint-less take can still be reported by handle.
    )
    subscription: Optional[SubscriptionEndpoint] = (
        None  # Resolved endpoint; None when no rcl_subscription_init named this handle.
    )
    source_timestamp: Optional[int] = None  # DDS publication time of the message taken.
    taken: int = 0  # 0 when the poll found no message (an empty take).
    ts_rmw: Optional[int] = None  # rmw_take timestamp (ns).
    ts_rcl: Optional[int] = None  # rcl_take timestamp (ns).
    ts_rclcpp: Optional[int] = None  # rclcpp_take timestamp (ns).
    origin_pub: Optional[Pub] = (
        None  # The publish that produced it, set by cross_link().
    )
    consumer_cb: Optional[CB] = None  # The callback invocation this take triggered.

    @property
    def topic(self) -> Optional[str]:
        """
        The topic this take was delivered on.

        Returns:
            Optional[str]: The topic name, or None when the endpoint is unresolved.
        """
        return None if self.subscription is None else self.subscription.topic.name

    @property
    def topic_label(self) -> str:
        """
        This take's topic for display, falling back to its raw handle.

        Returns:
            str: The topic name, else the unresolved rmw subscription handle in hex.
        """
        # Unlike `topic`, never None -- keeps unresolved instances distinguishable by
        # handle in report/grouping keys instead of collapsing them under one bucket.
        return self.topic if self.topic is not None else fmt_hex_or_str(self.rmw_sub)

    @property
    def node(self) -> Optional[Node]:
        """
        The node that received this take.

        Returns:
            Optional[Node]: The owning node, or None when the endpoint is unresolved.
        """
        return None if self.subscription is None else self.subscription.node


@dataclass(slots=True, eq=False)
class CB:
    """One callback invocation: either a matched callback_start/callback_end pair (C++,
    `callback` set) or an outermost Python zone begin/end pair (`zone_name` set) -- the
    two are mutually exclusive.
    """

    id: int  # Index into FlowGraph.callbacks.
    vpid: Optional[int] = None  # Process the callback ran in.
    vtid: Optional[int] = None  # Thread the callback ran on.
    procname: str = ""  # Process name.
    callback: Optional[int] = None  # rclcpp callback pointer; None for a Python zone.
    zone_name: Optional[str] = None  # Python zone name; None for a C++ callback.
    kind: str = CB_KIND_UNKNOWN  # One of the CB_KIND_* constants, stamped during identity resolution.
    owner: Optional[Endpoint] = None  # The registration this invocation belongs to.
    node: Optional[Node] = None  # Node the invocation ran in.
    target_label: str = ""  # Topic, service name, timer symbol or zone name, per kind.
    handler_label: str = UNKNOWN_HANDLER_LABEL  # Display name of the handler that ran, stamped during identity resolution.
    timer_period_ns: Optional[int] = None  # Configured period, for CB_KIND_TIMER only.
    is_intra: int = 0  # Nonzero when the message was delivered intra-process.
    ts_start: Optional[int] = None  # callback_start / zone-begin timestamp (ns).
    ts_end: Optional[int] = (
        None  # callback_end / zone-end timestamp (ns); None if open.
    )
    trigger_take: Optional[Take] = (
        None  # The take that triggered this invocation. Set only on a measured binding -- see FlowGraph's binding rule.
    )
    pubs: List[Pub] = dc_field(
        default_factory=list
    )  # Publishes issued from inside this invocation.
    linked_pubs: List[LinkedPub] = dc_field(
        default_factory=list
    )  # Link publishes declared inside this invocation.
    linked_takes: List[LinkedTake] = dc_field(
        default_factory=list
    )  # Link takes declared inside this invocation.

    @property
    def duration_ns(self) -> Optional[int]:
        """
        Wall-clock time this invocation ran for.

        Returns:
            Optional[int]: End minus start in nanoseconds, or None while either endpoint
            of the pair is missing (an invocation still open when the trace ended).
        """
        if self.ts_start is None or self.ts_end is None:
            return None
        return self.ts_end - self.ts_start

    @property
    def node_label(self) -> str:
        """
        Display label of the node this invocation ran in.

        Returns:
            str: The node's "/namespace/name", or UNKNOWN_NODE_LABEL when the
            invocation never resolved to a node.
        """
        return UNKNOWN_NODE_LABEL if self.node is None else self.node.label

    @property
    def group_key(self) -> CallbackKey:
        """
        The key every per-callback aggregation groups this invocation under.

        Reading one key off the identity FlowGraph already stamped is what keeps
        duration, timer-jitter and interarrival stats grouping invocations
        identically instead of each re-deriving its own notion of a group.

        The handler label is part of the key so two distinct callbacks that share a
        node, kind and target -- two handlers on one topic, say -- stay separate.

        Returns:
            CallbackKey: (node label, callback kind, target label, handler label).
        """
        return (self.node_label, self.kind, self.target_label, self.handler_label)


@dataclass(slots=True, eq=False)
class Edge:
    """One matched publish -> take delivery."""

    pub: Pub  # The publish that produced the message.
    take: Take  # The take that received it.

    @property
    def latency_ns(self) -> Optional[int]:
        """
        Transport latency from the DDS write to the delivery.

        Returns:
            Optional[int]: take.ts_rmw minus pub.ts_rmw in nanoseconds, or None when
            either rmw timestamp is missing.
        """
        if self.take.ts_rmw is None or self.pub.ts_rmw is None:
            return None
        return self.take.ts_rmw - self.pub.ts_rmw

    @property
    def cross_process(self) -> bool:
        """
        Whether this delivery crossed a process boundary.

        Returns:
            bool: True when the publishing and subscribing vpids differ.
        """
        return self.pub.vpid != self.take.vpid


@dataclass(slots=True, eq=False)
class LinkedPub:
    """A user-declared handoff out of one callback, correlated by uid rather than by
    timestamp. A publish into the *link* graph, not a ROS publish: it names an
    in-process dependency that no library tracepoint can observe, so the analyzer never
    has to infer it from a time window.
    """

    id: int  # Index into FlowGraph.linked_pubs.
    uid: int  # Process-local identifier this link publish declared.
    cb: Optional[CB] = None  # The invocation that published it; None when unresolvable.
    vpid: Optional[int] = None  # Declaring process. uids are unique only within one.
    vtid: Optional[int] = None  # Declaring thread.
    procname: str = ""  # Declaring process name.
    ts_ns: int = 0  # Trace timestamp of the declaration.
    linked_takes: List[LinkedTake] = dc_field(
        default_factory=list
    )  # Every link take naming this uid -- one may feed several later publishes.

    @property
    def origin_take(self) -> Optional[Take]:
        """
        The take that delivered the message this uid describes.

        This is what makes a uid traceable back to an rmw_take and onward to the
        rmw_publish behind it.

        Returns:
            Optional[Take]: The publishing callback's triggering take, or None when the
            callback is unresolved or was not itself triggered by a take.
        """
        return None if self.cb is None else self.cb.trigger_take


@dataclass(slots=True, eq=False)
class LinkedTake:
    """A user-declared consumption of one or more uids inside a callback. A take from
    the *link* graph, not a ROS take."""

    id: int  # Index into FlowGraph.linked_takes.
    uids: List[int] = dc_field(
        default_factory=list
    )  # Every uid named, in declaration order.
    origin_linked_pubs: List[LinkedPub] = dc_field(
        default_factory=list
    )  # The subset that resolved to a LinkedPub.
    unresolved_uids: List[int] = dc_field(
        default_factory=list
    )  # Named uids that never resolved -- never published, published in another process, or published after this take. Recorded rather than guessed.
    cb: Optional[CB] = None  # The invocation that took them.
    vpid: Optional[int] = None  # Declaring process.
    vtid: Optional[int] = None  # Declaring thread.
    procname: str = ""  # Declaring process name.
    ts_ns: int = 0  # Trace timestamp of the declaration.
    truncated: bool = False  # True when the emitter hit its citation cap, so the uid list here is incomplete.


@dataclass(slots=True, eq=False)
class Step:
    """One step of a chain traversal: a publish, the take that received it, and how the
    chain continued from the consuming callback.

    Indirectness is a first-class fact about the step, not something read back out of
    which optional fields happen to be set: `is_indirect` is stamped by the resolver
    (`FlowGraph.relay_step`) at the moment it decides whether the take's own callback
    republished directly or handed off through a uid link, rather than being inferred
    later from `linked_pub`/`linked_take` being non-None.
    """

    topic: str  # Topic of this step.
    next_topic: Optional[str]  # Topic of the following step; None at the sink.
    pub: Pub  # The publish that opened this step.
    take: Optional[Take]  # The take that received it; None on a pub-terminated chain.
    is_indirect: bool = False  # True when the chain continued through a uid link rather than a direct republish out of the consuming callback.
    linked_pub: Optional[LinkedPub] = (
        None  # The link declaration the chain continued through; set only when is_indirect.
    )
    linked_take: Optional[LinkedTake] = (
        None  # The link take that resolved linked_pub to the callback which republished; set only when is_indirect.
    )
    next_pub: Optional[Pub] = (
        None  # The publish this step relayed onto `next_topic`, which opens the following step; None at the sink.
    )

    @property
    def end_ts_ns(self) -> Optional[int]:
        """
        The moment this step's message reached the end of the step: its delivery, or --
        with nothing taking it -- the publish onto the wire.

        Returns:
            Optional[int]: The take's rmw timestamp in nanoseconds when a take received
            this step, else the publish's; None when whichever of the two ends the step
            never resolved its timestamp.
        """
        if self.take is not None:
            return self.take.ts_rmw
        return self.pub.ts_rmw

    @property
    def take_to_callback_start_ns(self) -> Optional[int]:
        """
        Time between this step's take landing and its consuming callback actually
        starting. This is usually negligible since these are fired synchronously as:
        rmw_take -> rcl_take -> rclcpp_take -> callback_start.

        Returns:
            Optional[int]: Nanoseconds from the take's rmw timestamp to the
            consuming callback's start, or None when there is no take, no bound
            callback, or a needed timestamp is unresolved.
        """
        if self.take is None or self.take.ts_rmw is None:
            return None
        cb = self.take.consumer_cb
        if cb is None or cb.ts_start is None:
            return None
        return cb.ts_start - self.take.ts_rmw

    @property
    def callback_time_ns(self) -> Optional[int]:
        """
        This step's own callback's processing time: from its start to the moment it
        handed the chain onward, whether that was a direct republish or declaring a
        uid link -- not to callback_end, which may run past the handoff.

        Returns:
            Optional[int]: Nanoseconds from callback start to the handoff, or None at
            the sink (which relays nothing onward) or when a needed timestamp is
            unresolved.
        """
        if self.next_topic is None or self.take is None:
            return None
        cb = self.take.consumer_cb
        if cb is None or cb.ts_start is None:
            return None
        if self.is_indirect:
            return self.linked_pub.ts_ns - cb.ts_start
        if self.next_pub is None or self.next_pub.ts_rmw is None:
            return None
        return self.next_pub.ts_rmw - cb.ts_start

    @property
    def indirect_wait_ns(self) -> Optional[int]:
        """
        Time a uid link sat before the callback that consumes it started running.

        Measured from the link being declared to the start of the callback bound to
        the take that named it -- not to when that callback's code actually reaches
        the `linked_take()` call, which happens after the callback (and any executor
        queueing before it) already began.

        Returns:
            Optional[int]: Nanoseconds from the link declaration to that callback's
            start, or None on a direct step or when the callback never resolved.
        """
        if not self.is_indirect or self.linked_take is None:
            return None
        cb = self.linked_take.cb
        if cb is None or cb.ts_start is None:
            return None
        return cb.ts_start - self.linked_pub.ts_ns

    @property
    def relay_callback_time_ns(self) -> Optional[int]:
        """
        The link-consuming callback's own processing time, ending at the republish.

        The counterpart to `callback_time_ns` for the callback on the far side of a
        uid link -- the piece of an indirect step that `indirect_wait_ns` does not
        cover.

        Returns:
            Optional[int]: Nanoseconds from that callback's start to `next_pub`, or
            None on a direct step or when a needed timestamp is unresolved.
        """
        if not self.is_indirect or self.linked_take is None:
            return None
        cb = self.linked_take.cb
        if cb is None or cb.ts_start is None:
            return None
        if self.next_pub is None or self.next_pub.ts_rmw is None:
            return None
        return self.next_pub.ts_rmw - cb.ts_start


@dataclass(slots=True, eq=False)
class Traversal:
    """One publish walked all the way through a requested topic chain."""

    steps: List[Step] = dc_field(
        default_factory=list
    )  # One entry per topic in the requested sequence, in order.
    terminator: str = CHAIN_TERMINATOR_TAKE  # The CHAIN_TERMINATOR_* the walk stamped.
    total_latency_ns: Optional[int] = (
        None  # The final step's end_ts_ns minus the origin publish's ts_rmw -- a direct subtraction on one clock, not a sum of per-step numbers.
    )
    callback_start_latency_ns: Optional[int] = (
        None  # Sink callback's start minus the origin publish's ts_rmw; None on a pub-terminated chain, or when that callback is unresolved.
    )

    @property
    def chain_latency_ns(self) -> Optional[int]:
        """
        This traversal end to end, from the chain's first publish to wherever its
        terminator says the chain ends.

        The one definition of a chain's latency, so the text and HTML reports cannot
        disagree about which point a `pub`- or `take`-terminated chain ends at.

        Returns:
            Optional[int]: Nanoseconds to the sink callback's start under
            CHAIN_TERMINATOR_TAKE, or to the final publish under CHAIN_TERMINATOR_PUB;
            None when that point never resolved.
        """
        if self.terminator == CHAIN_TERMINATOR_PUB:
            return self.total_latency_ns
        return self.callback_start_latency_ns


@dataclass(slots=True, eq=False)
class ZoneDeclaration:
    """What an instrumented Python zone declared itself to be at registration time.

    Removes the need to guess a zone's callback kind from recurrence and process
    co-location; an undeclared zone stays CB_KIND_ZONE rather than being inferred.
    """

    zone_name: str  # The instrumented method's name, matching the zone's begin/end.
    kind: str  # One of the CB_KIND_* constants.
    target: str = ""  # Topic name, service name, or "" when the kind carries none.
    period_ns: Optional[int] = None  # Timer period, for CB_KIND_TIMER only.


@dataclass(slots=True, eq=False)
class LinkDeclaration:
    """One parsed link declaration, before it is bound to a callback."""

    kind: (
        str  # LINK_KIND_PUB for a uid being declared, LINK_KIND_TAKE for uids consumed.
    )
    uids: List[int] = dc_field(
        default_factory=list
    )  # The uid published, or every uid taken in declaration order.
    truncated: bool = False  # True when the emitter hit its uid-list cap.


# (vpid, message buffer pointer): joins one message's publish or take chain. Scoped by
# vpid because the pointer is process-local and reused once the buffer is freed.
MessageKey = Tuple[Optional[int], int]
# (vpid, vtid): keys state belonging to one thread of execution.
ThreadKey = Tuple[Optional[int], Optional[int]]
# (vpid, uid): a declared uid is only unique within the process that published it.
UidKey = Tuple[Optional[int], int]


@dataclass(slots=True, eq=False)
class BuildState:
    """Scratch state for one chronological sweep: the half-assembled instances still
    awaiting their remaining events.
    """

    pending_pub: Dict[MessageKey, Pub] = dc_field(
        default_factory=dict
    )  # Publishes awaiting rcl/rmw_publish.
    pending_take: Dict[MessageKey, Take] = dc_field(
        default_factory=dict
    )  # Takes awaiting rcl/rclcpp_take.
    cb_stack: Dict[ThreadKey, List[CB]] = dc_field(
        default_factory=lambda: defaultdict(list)
    )  # Callbacks open on each thread, innermost last.
    thread_take: Dict[ThreadKey, Take] = dc_field(
        default_factory=dict
    )  # The take delivered on a thread and not yet claimed by a callback. A single slot, not a queue: within one thread of one process the executor takes a message and hands it straight to its callback, so two takes are never pending at once. A displacement is counted in Diagnostics rather than assumed impossible.
    python_zone_depth: Dict[ThreadKey, int] = dc_field(
        default_factory=lambda: defaultdict(int)
    )  # Zone nesting depth, so only the outermost zone synthesizes a CB.
    open_python_cb: Dict[ThreadKey, CB] = dc_field(
        default_factory=dict
    )  # The outermost-zone CB currently open.
    zone_decls: Dict[Tuple[Optional[int], str], ZoneDeclaration] = dc_field(
        default_factory=dict
    )  # (vpid, zone_name) -> declaration. Keyed by vpid because two processes can run the same instrumented class with different registrations.
    linked_pub_by_uid: Dict[UidKey, LinkedPub] = dc_field(
        default_factory=dict
    )  # (vpid, uid) -> the LinkedPub that published it.


@dataclass(slots=True, eq=False)
class Diagnostics:
    """Everything the graph could not resolve, reported before any statistic.

    These are independent diagnoses, not a partition -- never sum them.
    """

    unresolved_pub_handles: Set[int] = dc_field(
        default_factory=set
    )  # rcl publisher handles no init event named.
    unresolved_rmwsub_handles: Set[int] = dc_field(
        default_factory=set
    )  # rmw subscription handles no init named.
    displaced_pending_takes: int = 0  # Takes overwritten by a newer take on the same (vpid, vtid). Expected to be zero; a non-zero count means the one-take-per-thread reasoning does not hold for some executor or node.
    unbound_callbacks: int = 0  # Invocations that started with a take pending on their thread but whose owner did not match it, so no binding was made.
    unconsumed_takes: int = 0  # Non-empty takes no callback ever claimed.
    unresolved_link_takes: int = (
        0  # Link takes naming at least one uid that resolved to no LinkedPub.
    )
    truncated_link_takes: int = 0  # Link takes whose uid list hit the emitter's cap.


class QueueHealthStats(NamedTuple):
    """Per-topic pub<->sub delivery counts and ratios, computed once across
    every publish to that topic in the trace."""

    total_published: int
    delivered: int
    undelivered: int
    undelivered_pct: float
    delivery_ratio: float


class CallbackGroup(NamedTuple):
    starts: List[int]  # ts_start of every invocation, in trace order
    durations: List[int]  # ts_end - ts_start, completed invocations only
    intra_count: int  # completed invocations delivered intra-process
    timer_period_ns: Optional[int]  # configured period; None unless a declared timer


class CallbackTimingStats(NamedTuple):
    interarrival_ns: Optional[
        StatsSummary
    ]  # time between the start of one invocation and the next
    inter_callback_gap_ns: Optional[
        StatsSummary
    ]  # time between the end of one invocation and the start of the next
    instantaneous_wait_count: (
        int  # number of inter_callback gaps that were below a threshold
    )


class CallbackDurationRow(NamedTuple):
    """One aggregated callback-duration row, keyed by CB.group_key."""

    node: str  # owning node's "/namespace/name", UNKNOWN_NODE_LABEL if unresolved
    callback_type: str  # one of the CB_KIND_* constants
    topic: str  # topic, timer symbol, service name, or zone name -- per kind
    handler: str  # the handler that ran; separates two handlers on one topic
    timer_period_ns: Optional[int]  # configured period; None if non-timer or undeclared
    invocation_count: int  # completed invocations aggregated into this row
    intra_count: int  # intra-process deliveries; always 0 for Python zones
    inter_count: int  # the rest: cross-process, plus every timer/service invocation
    min_ns: float  # fastest invocation
    mean_ns: int  # arithmetic mean
    median_ns: int  # p50
    p99_ns: int  # 99th percentile
    max_ns: float  # slowest invocation
