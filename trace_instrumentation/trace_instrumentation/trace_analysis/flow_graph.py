# Copyright 2026 Gritt Robotics Inc.

"""
flow_graph.py
=============

Flow analysis: message instances, take->callback->publish chains, and
cross-process publish<->take linking.

Correlation keys:
  * `message`         : a single message buffer's heap pointer. Joins the
                        within-process publish chain (rclcpp_publish ->
                        rcl_publish -> rmw_publish) and the take chain
                        (rmw_take -> rcl_take -> rclcpp_take). Process-local
                        and reused over time, so only matched within a thread
                        run, never globally.
  * `source_timestamp`: the DDS publication time.  rmw_publish.timestamp carries
                        this value on the publisher side; rmw_take.source_timestamp
                        carries the exact same value on the subscriber side.
  * `rmw_publisher_handle`: the publisher's rmw-level handle, also traced by
                        rmw_publish. Names a publish's topic when no rcl_publish
                        preceded it, but only for a publisher that was itself
                        rcl-initialized -- the topic text exists nowhere else in
                        the trace. Present only on the vendored fork's 3-arg
                        rmw_publish tracepoint, absent on the stock 1-arg one.

This layer turns the flat event stream into:
  - publish instances  (one per published message, with topic + origin cb)
  - take    instances  (one per delivered message, with topic + consumer cb)
  - callback instances (start/end, what triggered it, what it published)
  - pub<->take edges    (cross- and intra-process, with measured latency)
  - linked pub<->takes  (user-declared uid handoffs between callbacks in one
                        process, which no library tracepoint emits)
"""

from collections import defaultdict
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from trace_instrumentation.trace_analysis.analysis import (
    CALLBACK_OWNER_KIND_SERVICE,
    CALLBACK_OWNER_KIND_SUBSCRIPTION,
    CALLBACK_OWNER_KIND_TIMER,
    REGISTRY_KEY_NAME,
    REGISTRY_KEY_NODE,
    REGISTRY_KEY_RMW,
    REGISTRY_KEY_TOPIC,
    REGISTRY_KEY_VPID,
    TraceAnalysis,
)
from trace_instrumentation.trace_analysis.ctf.reader import Event, Metadata
from trace_instrumentation.trace_analysis.event_fields import (
    FIELD_CALLBACK,
    FIELD_IS_INTRA_PROCESS,
    FIELD_MESSAGE,
    FIELD_MSG,
    FIELD_NAMESPACE,
    FIELD_PERIOD,
    FIELD_PUBLISHER_HANDLE,
    FIELD_QUEUE_DEPTH,
    FIELD_RMW_PUBLISHER_HANDLE,
    FIELD_RMW_SUBSCRIPTION_HANDLE,
    FIELD_SOURCE_TIMESTAMP,
    FIELD_TAKEN,
    FIELD_TIMESTAMP,
)
from trace_instrumentation.trace_analysis.event_names import (
    EVT_CALLBACK_END,
    EVT_CALLBACK_START,
    EVT_LTTNG_PYTHON_EVENT,
    EVT_RCL_PUBLISH,
    EVT_RCL_TAKE,
    EVT_RCLCPP_PUBLISH,
    EVT_RCLCPP_TAKE,
    EVT_RMW_PUBLISH,
    EVT_RMW_TAKE,
)
from trace_instrumentation.trace_analysis.graph import (
    CB,
    CB_KIND_SERVICE,
    CB_KIND_SUBSCRIPTION,
    CB_KIND_TIMER,
    CB_KIND_UNKNOWN,
    CB_KIND_ZONE,
    CHAIN_TERMINATOR_PUB,
    CHAIN_TERMINATOR_TAKE,
    CHAIN_TERMINATORS,
    BuildState,
    Diagnostics,
    Edge,
    LinkDeclaration,
    LinkedPub,
    LinkedTake,
    MessageKey,
    Node,
    Pub,
    PublisherEndpoint,
    ServiceEndpoint,
    Step,
    SubscriptionEndpoint,
    Take,
    ThreadKey,
    TimerEndpoint,
    Topic,
    Traversal,
)
from trace_instrumentation.trace_analysis.metrics.stats import (
    fmt_hex_or_str,
)
from trace_instrumentation.trace_analysis.python_zones import (
    LINK_KIND_PUB,
    PYTHON_ZONE_PHASE_BEGIN,
    PYTHON_ZONE_PHASE_END,
    parse_link_declaration_msg,
    parse_python_zone_msg,
    parse_zone_declaration_msg,
)

# a chain needs an origin and a sink before there is anything to walk
MIN_CHAIN_TOPICS = 2

# A chain step may pin its consuming node as "<topic>@<node>", so a topic with several
# subscribers resolves to the one the chain means rather than whichever take came first.
CHAIN_STEP_NODE_SEPARATOR = "@"

# Event groups build() dispatches on. Names are mutually exclusive, so grouping them
# keeps the per-event branch shallow without changing which handler runs.
PUBLISH_CHAIN_EVENTS = frozenset({EVT_RCLCPP_PUBLISH, EVT_RCL_PUBLISH, EVT_RMW_PUBLISH})
TAKE_CHAIN_EVENTS = frozenset({EVT_RMW_TAKE, EVT_RCL_TAKE, EVT_RCLCPP_TAKE})
CALLBACK_EVENTS = frozenset({EVT_CALLBACK_START, EVT_CALLBACK_END})


def split_chain_step(step: str) -> Tuple[str, Optional[str]]:
    """
    Split a chain step into its topic and the consuming node it pins, if any.

    Args:
        step (str): A chain step, either "<topic>" or "<topic>@<node>".

    Returns:
        Tuple[str, Optional[str]]: The topic, and the node label the step pins, or None
        when the step names no node.
    """
    topic, separator, node = step.partition(CHAIN_STEP_NODE_SEPARATOR)
    return topic, node if len(separator) > 0 else None


class ChainSpec(NamedTuple):
    """A requested chain split into what the walk needs: its steps and its terminator."""

    steps: List[str]  # The steps, in order, each still carrying any "@<node>" pin.
    terminator: str  # The CHAIN_TERMINATOR_* the chain declared.


def split_chain_terminator(topic_sequence: List[str]) -> ChainSpec:
    """
    Split a requested chain into its steps and the terminator its last element declares.

    A terminator token cannot be mistaken for a step: ROS topic names are fully
    qualified, so a bare "pub"/"take" is never one.

    Args:
        topic_sequence (List[str]): The requested chain, whose last element may be a
            terminator token rather than a step.

    Returns:
        ChainSpec: The steps, and the CHAIN_TERMINATOR_* the chain declared --
        CHAIN_TERMINATOR_TAKE when it declared none.
    """
    if len(topic_sequence) > 0 and topic_sequence[-1] in CHAIN_TERMINATORS:
        return ChainSpec(list(topic_sequence[:-1]), topic_sequence[-1])
    return ChainSpec(list(topic_sequence), CHAIN_TERMINATOR_TAKE)


def take_is_from_node(take: Take, node_label: Optional[str]) -> bool:
    """
    Whether a take belongs to the node its chain step pinned.

    Args:
        take (Take): The take to test.
        node_label (Optional[str]): Node label the step pinned, or None when the step
            pinned none and any subscriber on the topic matches.

    Returns:
        bool: True when the step pinned no node, or when this take's node matches it.
    """
    if node_label is None:
        return True
    return take.node is not None and take.node.label == node_label


class FlowGraph:
    """Reconstructs message-level flow from the chronological event stream."""

    def __init__(
        self, meta: Metadata, events: List[Event], analysis: TraceAnalysis
    ) -> None:
        """
        Set up empty registries, ready for build().

        Args:
            meta (Metadata): Parsed CTF metadata.
            events (List[Event]): Decoded events, sorted by clock cycle.
            analysis (TraceAnalysis): Built analysis, for node/publisher/subscription
                registries and callback ownership.
        """
        self.meta = meta  # parsed CTF metadata
        self.events = events  # chronological event stream build() sweeps
        self.trace_analysis = (
            analysis  # registries used to resolve handles and callback labels
        )
        self.publishes: List[Pub] = []  # indexed by Pub.id
        self.takes: List[Take] = []  # indexed by Take.id
        self.callbacks: List[CB] = []  # indexed by CB.id
        self.edges: List[Edge] = []  # one entry per linked pub->take pair
        self.linked_pubs: List[LinkedPub] = []  # indexed by LinkedPub.id
        self.linked_takes: List[LinkedTake] = []  # indexed by LinkedTake.id

        # Static topology, resolves raw handles from TraceAnalysis to objects
        self.nodes: Dict[int, Node] = {}  # node handle -> Node
        self.topics: Dict[str, Topic] = {}  # topic name -> Topic
        self.publisher_endpoints: Dict[int, PublisherEndpoint] = {}  # rcl handle -> ep
        self.subscription_endpoints: Dict[int, SubscriptionEndpoint] = {}
        self.timer_endpoints: Dict[int, TimerEndpoint] = {}
        self.service_endpoints: Dict[int, ServiceEndpoint] = {}
        # rmw handle -> endpoint. rmw_publish/rmw_take carry only the rmw handle, so
        # these are how an instance reaches its endpoint when the rcl handle is absent.
        self.rmwpub_to_endpoint: Dict[int, PublisherEndpoint] = {}
        self.rmwsub_to_endpoint: Dict[int, SubscriptionEndpoint] = {}
        self.diagnostics = Diagnostics()

    # ---- pass 1: assemble instances in one chronological sweep ---- #
    def build(self) -> "FlowGraph":
        """
        Assemble publish/take/callback instances in one chronological sweep over
        self.events, then name every instance's topic and link each take to the
        publish that produced it.

        Returns:
            FlowGraph: self, for chaining (e.g. `FlowGraph(meta, events, an).build()`).
        """
        self.build_topology()
        state = BuildState()
        for e in self.events:
            nm = e.name
            if nm in PUBLISH_CHAIN_EVENTS:
                self.handle_publish_event(state, e)
            elif nm in TAKE_CHAIN_EVENTS:
                self.handle_take_event(state, e)
            elif nm in CALLBACK_EVENTS:
                self.handle_callback_event(state, e)
            elif nm == EVT_LTTNG_PYTHON_EVENT:
                self.handle_python_zone_event(state, e)

        self.resolve_instances()
        self.cross_link()
        self.tally_link_diagnostics()
        return self

    def pub_for_message(self, state: BuildState, e: Event, remove: bool) -> Pub:
        """
        Fetch the publish instance this event belongs to, creating it if this is the
        first event of the chain seen (a trace can start mid-chain).

        Args:
            state (BuildState): Sweep state holding the pending publishes.
            e (Event): The publish-chain event being handled.
            remove (bool): True on the last event of the chain (rmw_publish), which
                completes the instance and so stops it awaiting further events.

        Returns:
            Pub: The publish instance, already appended to self.publishes.
        """
        key: MessageKey = (e.vpid, e.fields[FIELD_MESSAGE])
        p = state.pending_pub.pop(key, None) if remove else state.pending_pub.get(key)
        if p is not None:
            return p
        p = Pub(
            len(self.publishes),
            vpid=e.vpid,
            vtid=e.vtid,
            procname=e.procname,
            message=e.fields[FIELD_MESSAGE],
        )
        self.publishes.append(p)
        if not remove:
            state.pending_pub[key] = p
        return p

    def bind_pending_take(self, state: BuildState, cb: CB) -> None:
        """
        Bind the take pending on this callback's thread, if it is this callback's own.

        The pending take is consumed only when the callback's resolved owner is that
        take's subscription endpoint. A timer or service callback that happens to start
        while a subscription's message is pending therefore leaves it alone, and an
        association that cannot be confirmed is recorded as unbound rather than guessed.

        Args:
            state (BuildState): Sweep state holding the per-thread pending take.
            cb (CB): The callback invocation that just started, with its identity already
                resolved by resolve_cb_identity().
        """
        tkey: ThreadKey = (cb.vpid, cb.vtid)
        t = state.thread_take.get(tkey)
        if t is None:
            return
        if cb.kind != CB_KIND_SUBSCRIPTION or cb.owner is not t.subscription:
            self.diagnostics.unbound_callbacks += 1
            return
        del state.thread_take[tkey]
        cb.trigger_take = t
        t.consumer_cb = cb

    def handle_publish_event(self, state: BuildState, e: Event) -> None:
        """
        Fold one rclcpp_publish/rcl_publish/rmw_publish event into its Pub instance.

        Args:
            state (BuildState): Sweep state.
            e (Event): The publish-chain event.
        """
        f = e.fields
        if e.name == EVT_RCLCPP_PUBLISH:
            p = self.pub_for_message(state, e, remove=False)
            p.ts_rclcpp = e.ts_ns
        elif e.name == EVT_RCL_PUBLISH:
            p = self.pub_for_message(state, e, remove=False)
            p.publisher_handle = f[FIELD_PUBLISHER_HANDLE]
            p.ts_rcl = e.ts_ns
        elif e.name == EVT_RMW_PUBLISH:
            p = self.pub_for_message(state, e, remove=True)
            p.ts_rmw = e.ts_ns
            # absent on the stock 1-arg tracepoint, present on the vendored 3-arg one
            p.rmw_publisher_handle = f.get(FIELD_RMW_PUBLISHER_HANDLE)
            p.source_timestamp = source_timestamp_or_none(f, FIELD_TIMESTAMP)
            # attribute to the callback currently running on this thread
            tkey: ThreadKey = (e.vpid, e.vtid)
            if len(state.cb_stack[tkey]) > 0:
                cb = state.cb_stack[tkey][-1]
                p.enclosing_cb = cb
                cb.pubs.append(p)

    def handle_take_event(self, state: BuildState, e: Event) -> None:
        """
        Fold one rmw_take/rcl_take/rclcpp_take event into its Take instance.

        Args:
            state (BuildState): Sweep state.
            e (Event): The take-chain event.
        """
        f = e.fields
        key: MessageKey = (e.vpid, f[FIELD_MESSAGE])
        if e.name == EVT_RMW_TAKE:
            rmw_sub = f[FIELD_RMW_SUBSCRIPTION_HANDLE]
            t = Take(
                len(self.takes),
                vpid=e.vpid,
                vtid=e.vtid,
                procname=e.procname,
                message=f[FIELD_MESSAGE],
                rmw_sub=rmw_sub,
                # resolved here, not in a later pass: the binding rule below compares
                # this endpoint against the starting callback's owner
                subscription=self.rmwsub_to_endpoint.get(rmw_sub),
                source_timestamp=source_timestamp_or_none(f, FIELD_SOURCE_TIMESTAMP),
                taken=f.get(FIELD_TAKEN, 0),
                ts_rmw=e.ts_ns,
            )
            self.takes.append(t)
            if t.subscription is not None:
                t.subscription.takes.append(t)
            if t.taken != 0:
                state.pending_take[key] = t
                tkey = (e.vpid, e.vtid)
                if tkey in state.thread_take:
                    self.diagnostics.displaced_pending_takes += 1
                state.thread_take[tkey] = t
        elif e.name == EVT_RCL_TAKE:
            t = state.pending_take.get(key)
            if t is not None:
                t.ts_rcl = e.ts_ns
        elif e.name == EVT_RCLCPP_TAKE:
            t = state.pending_take.pop(key, None)
            if t is not None:
                t.ts_rclcpp = e.ts_ns

    def handle_callback_event(self, state: BuildState, e: Event) -> None:
        """
        Open a CB on callback_start, close the innermost one on callback_end.

        Args:
            state (BuildState): Sweep state.
            e (Event): The callback_start/callback_end event.
        """
        tkey: ThreadKey = (e.vpid, e.vtid)
        if e.name == EVT_CALLBACK_START:
            cb = CB(
                len(self.callbacks),
                vpid=e.vpid,
                vtid=e.vtid,
                procname=e.procname,
                callback=e.fields[FIELD_CALLBACK],
                is_intra=e.fields.get(FIELD_IS_INTRA_PROCESS, 0),
                ts_start=e.ts_ns,
            )
            self.callbacks.append(cb)
            # identity first: the binding rule compares this callback's resolved owner
            # against the pending take's subscription endpoint
            self.resolve_cb_identity(cb, state)
            self.bind_pending_take(state, cb)
            state.cb_stack[tkey].append(cb)
        elif e.name == EVT_CALLBACK_END:
            if len(state.cb_stack[tkey]) > 0:
                cb = state.cb_stack[tkey].pop()
                cb.ts_end = e.ts_ns

    def handle_python_zone_event(self, state: BuildState, e: Event) -> None:
        """
        Synthesize a CB per outermost Python zone, so rclpy callbacks (which emit no
        callback_start/end) still appear as callback instances.

        Args:
            state (BuildState): Sweep state.
            e (Event): The lttng_python:event zone begin/end event.
        """
        # A declaration carries no ":" separator, so parse_python_zone_msg would reject
        # it -- it has to be recognized before the begin/end grammar rejects the payload.
        msg = e.fields.get(FIELD_MSG, "")
        declaration = parse_zone_declaration_msg(msg)
        if declaration is not None:
            state.zone_decls[(e.vpid, declaration.zone_name)] = declaration
            return
        link_declaration = parse_link_declaration_msg(msg)
        if link_declaration is not None:
            self.handle_link_declaration(state, e, link_declaration)
            return
        # Keyed by (vpid, vtid), not the Python thread_id TraceAnalysis.build() uses.
        parsed = parse_python_zone_msg(msg)
        if parsed is None:
            return
        zone_name, phase = parsed
        tkey: ThreadKey = (e.vpid, e.vtid)
        if phase == PYTHON_ZONE_PHASE_BEGIN:
            if state.python_zone_depth[tkey] == 0:
                cb = CB(
                    len(self.callbacks),
                    vpid=e.vpid,
                    vtid=e.vtid,
                    procname=e.procname,
                    zone_name=zone_name,
                    ts_start=e.ts_ns,
                )
                self.callbacks.append(cb)
                self.resolve_cb_identity(cb, state)
                self.bind_pending_take(state, cb)
                state.cb_stack[tkey].append(cb)
                state.open_python_cb[tkey] = cb
            state.python_zone_depth[tkey] += 1
        elif phase == PYTHON_ZONE_PHASE_END:
            if state.python_zone_depth[tkey] == 0:
                return
            state.python_zone_depth[tkey] -= 1
            if state.python_zone_depth[tkey] > 0:
                return
            cb = state.open_python_cb.pop(tkey, None)
            if (
                cb is not None
                and len(state.cb_stack[tkey]) > 0
                and state.cb_stack[tkey][-1] is cb
            ):
                state.cb_stack[tkey].pop()
                cb.ts_end = e.ts_ns

    def handle_link_declaration(
        self, state: BuildState, e: Event, declaration: LinkDeclaration
    ) -> None:
        """
        Fold one link publish or link take into the link graph.

        Args:
            state (BuildState): Sweep state holding the per-process uid index.
            e (Event): The lttng_python:event carrying the declaration.
            declaration (LinkDeclaration): The parsed link declaration.
        """
        tkey: ThreadKey = (e.vpid, e.vtid)
        stack = state.cb_stack[tkey]
        cb = stack[-1] if len(stack) > 0 else None

        if declaration.kind == LINK_KIND_PUB:
            uid = declaration.uids[0]
            linked_pub = LinkedPub(
                id=len(self.linked_pubs),
                uid=uid,
                cb=cb,
                vpid=e.vpid,
                vtid=e.vtid,
                procname=e.procname,
                ts_ns=e.ts_ns,
            )
            self.linked_pubs.append(linked_pub)
            if cb is not None:
                cb.linked_pubs.append(linked_pub)
            # uids are process-local, so the index is keyed by vpid as well
            state.linked_pub_by_uid[(e.vpid, uid)] = linked_pub
            return

        linked_take = LinkedTake(
            id=len(self.linked_takes),
            uids=list(declaration.uids),
            cb=cb,
            vpid=e.vpid,
            vtid=e.vtid,
            procname=e.procname,
            ts_ns=e.ts_ns,
            truncated=declaration.truncated,
        )
        self.linked_takes.append(linked_take)
        if cb is not None:
            cb.linked_takes.append(linked_take)
        for uid in declaration.uids:
            linked_pub = state.linked_pub_by_uid.get((e.vpid, uid))
            if linked_pub is None:
                linked_take.unresolved_uids.append(uid)
                continue
            linked_take.origin_linked_pubs.append(linked_pub)
            linked_pub.linked_takes.append(linked_take)

    def resolve_cb_identity(self, cb: CB, state: BuildState) -> None:
        """
        Stamp kind, owner, node, target label and timer period onto an invocation.

        Args:
            cb (CB): The invocation to resolve, already carrying callback/zone_name.
            state (BuildState): Sweep state holding the zone declarations seen so far.
        """
        if cb.callback is not None:
            self.resolve_cpp_cb_identity(cb)
            return

        # A zone names its own handler, whether or not it declared what it is.
        if cb.zone_name is not None:
            cb.handler_label = cb.zone_name

        declaration = state.zone_decls.get((cb.vpid, cb.zone_name))
        if declaration is None:
            cb.kind = CB_KIND_ZONE
            cb.target_label = cb.zone_name if cb.zone_name is not None else ""
            cb.node = self.node_for_vpid(cb.vpid)
            return
        cb.kind = declaration.kind
        # A timer declaration names no target, so the zone itself is the label -- an
        # empty one would merge every declared timer in the process into one group.
        cb.target_label = (
            declaration.target if len(declaration.target) > 0 else cb.handler_label
        )
        cb.timer_period_ns = declaration.period_ns
        cb.node = self.node_for_vpid(cb.vpid)
        if declaration.kind == CB_KIND_SUBSCRIPTION:
            cb.owner = self.subscription_for(cb.node, declaration.target)
        elif declaration.kind == CB_KIND_SERVICE:
            cb.owner = self.service_for(cb.node, declaration.target)
        elif declaration.kind == CB_KIND_TIMER:
            cb.owner = self.timer_for(cb.vpid, declaration.period_ns)

    def resolve_cpp_cb_identity(self, cb: CB) -> None:
        """
        Stamp identity onto a C++ invocation from its callback registration.

        Args:
            cb (CB): The invocation to resolve; `cb.callback` must be set.
        """
        cb.handler_label = self.trace_analysis.callback_label(cb.callback)
        owner = self.trace_analysis.callback_owner.get(cb.callback)
        if owner is None:
            cb.kind = CB_KIND_UNKNOWN
            cb.node = self.node_for_vpid(cb.vpid)
            return
        kind, handle = owner
        if kind == CALLBACK_OWNER_KIND_SUBSCRIPTION:
            endpoint = self.subscription_endpoints.get(handle)
            cb.kind = CB_KIND_SUBSCRIPTION
            cb.owner = endpoint
            cb.target_label = "" if endpoint is None else endpoint.topic.name
        elif kind == CALLBACK_OWNER_KIND_TIMER:
            endpoint = self.timer_endpoints.get(handle)
            cb.kind = CB_KIND_TIMER
            cb.owner = endpoint
            cb.timer_period_ns = None if endpoint is None else endpoint.period_ns
            # Falls back to the handle so two unsymboled timers on one node stay
            # distinguishable -- an empty label would merge them into one group.
            cb.target_label = self.trace_analysis.callback_symbols.get(
                cb.callback
            ) or fmt_hex_or_str(handle)
        elif kind == CALLBACK_OWNER_KIND_SERVICE:
            endpoint = self.service_endpoints.get(handle)
            cb.kind = CB_KIND_SERVICE
            cb.owner = endpoint
            cb.target_label = "" if endpoint is None else endpoint.name
        else:
            cb.kind = CB_KIND_UNKNOWN
        cb.node = None if cb.owner is None else cb.owner.node
        if cb.node is None:
            cb.node = self.node_for_vpid(cb.vpid)

    def node_for_vpid(self, vpid: Optional[int]) -> Optional[Node]:
        """
        Resolve the node a process belongs to, for events that carry no handle.

        Args:
            vpid (Optional[int]): Process id.

        Returns:
            Optional[Node]: The node, or None when the process registered none.
        """
        handle = self.trace_analysis.vpid_to_node.get(vpid)
        return None if handle is None else self.nodes.get(handle)

    def subscription_for(
        self, node: Optional[Node], topic: str
    ) -> Optional[SubscriptionEndpoint]:
        """
        Find a node's subscription on a topic.

        Args:
            node (Optional[Node]): The owning node, or None.
            topic (str): Topic name the declaration named.

        Returns:
            Optional[SubscriptionEndpoint]: The matching endpoint, or None.
        """
        if node is None:
            return None
        return next(
            (
                endpoint
                for endpoint in node.subscriptions
                if endpoint.topic.name == topic
            ),
            None,
        )

    def service_for(self, node: Optional[Node], name: str) -> Optional[ServiceEndpoint]:
        """
        Find a node's service by name.

        Args:
            node (Optional[Node]): The owning node, or None.
            name (str): Service name the declaration named.

        Returns:
            Optional[ServiceEndpoint]: The matching endpoint, or None.
        """
        if node is None:
            return None
        return next(
            (endpoint for endpoint in node.services if endpoint.name == name), None
        )

    def timer_for(
        self, vpid: Optional[int], period_ns: Optional[int]
    ) -> Optional[TimerEndpoint]:
        """
        Find the timer a declared Python zone belongs to, by process and period.

        Args:
            vpid (Optional[int]): Process the invocation ran in.
            period_ns (Optional[int]): Period the declaration reported, in nanoseconds.

        Returns:
            Optional[TimerEndpoint]: The single matching endpoint, or None when none or
            more than one matched.
        """
        if vpid is None or period_ns is None:
            return None
        matches = [
            self.timer_endpoints[handle]
            for handle, info in self.trace_analysis.timers.items()
            if info.get(REGISTRY_KEY_VPID) == vpid
            and info.get(FIELD_PERIOD) == period_ns
            and handle in self.timer_endpoints
        ]
        return matches[0] if len(matches) == 1 else None

    # ---- pass 3: resolve instances to endpoints, recording what did not ---- #
    def resolve_instances(self) -> None:
        """Attach every publish to its endpoint and record what could not be resolved.

        A publish resolves through its rcl handle first and falls back to the rmw handle
        that rmw_publish carries. The fallback recovers a publish whose rcl_publish was
        lost, not one issued directly at the rmw layer, and it only works for a publisher
        that was itself rcl-initialized -- the topic text exists nowhere else in the
        trace.

        Takes already resolved their subscription during the sweep, where the binding
        rule needed it, so this only records the handles that named none and counts the
        deliveries no callback claimed.
        """
        for p in self.publishes:
            endpoint = None
            if p.publisher_handle is not None:
                endpoint = self.publisher_endpoints.get(p.publisher_handle)
            if endpoint is None and p.rmw_publisher_handle is not None:
                endpoint = self.rmwpub_to_endpoint.get(p.rmw_publisher_handle)
            p.publisher = endpoint
            if endpoint is not None:
                endpoint.pubs.append(p)
            # A publish with no rcl handle at all is DDS graph traffic issued at the rmw
            # layer, not a missing init event, so only a named handle counts as missing.
            elif p.publisher_handle is not None:
                self.diagnostics.unresolved_pub_handles.add(p.publisher_handle)

        for t in self.takes:
            if t.subscription is None and t.rmw_sub is not None:
                self.diagnostics.unresolved_rmwsub_handles.add(t.rmw_sub)
            if t.taken != 0 and t.consumer_cb is None:
                self.diagnostics.unconsumed_takes += 1

    # ---- pass 4: link takes to the publish that produced them ---- #
    def cross_link(self):
        """Link each taken Take to the Pub that produced it: same exact DDS
        source_timestamp, and the same topic on both sides, as resolved by
        resolve_instances."""
        pubs_by_ts_and_topic: Dict[Tuple[int, str], List[Pub]] = defaultdict(list)
        for p in self.publishes:
            if p.source_timestamp is not None and p.topic is not None:
                pubs_by_ts_and_topic[(p.source_timestamp, p.topic)].append(p)

        for t in self.takes:
            # a take that can't be directly reoslved will not be linked
            if t.taken == 0 or t.source_timestamp is None or t.topic is None:
                continue
            candidates = pubs_by_ts_and_topic.get((t.source_timestamp, t.topic))
            if candidates is None:
                continue
            best = candidates[0]
            t.origin_pub = best
            best.takes.append(t)
            self.edges.append(Edge(pub=best, take=t))

    # ---- pass 5: tally what the uid links could not resolve ---- #
    def tally_link_diagnostics(self) -> None:
        """Count the link takes that resolved to nothing and the truncated ones.

        The links themselves resolve during the chronological sweep, so this pass is
        only the diagnostic tally.
        """
        for linked_take in self.linked_takes:
            if len(linked_take.unresolved_uids) > 0:
                self.diagnostics.unresolved_link_takes += 1
            if linked_take.truncated is True:
                self.diagnostics.truncated_link_takes += 1

    # ---- chain traversal ---- #
    def walk_topic_chain(self, topic_sequence: List[str]) -> List[Traversal]:
        """
        Walk every publish on the first topic through the rest of the sequence.

        Args:
            topic_sequence (List[str]): Ordered steps, origin first, sink last, and
                optionally a trailing CHAIN_TERMINATOR_* token declaring where a
                traversal ends. Each step is a topic name, optionally pinning the
                consuming node as "<topic>@<node>". Must have at least two steps.

        Returns:
            List[Traversal]: One entry per publish on the first step's topic that
            reached the sink. Publishes that did not complete the chain are omitted,
            including those whose take on a pinned step belonged to another node.
        """
        spec = split_chain_terminator(topic_sequence)
        if len(spec.steps) < MIN_CHAIN_TOPICS:
            return []

        step_specs = [split_chain_step(step) for step in spec.steps]
        origin_topic = step_specs[0][0]
        traversals: List[Traversal] = []
        for origin_pub in self.publishes:
            if origin_pub.topic != origin_topic:
                continue
            steps = self.walk_one(origin_pub, step_specs, spec.terminator)
            if steps is None:
                continue
            traversals.append(self.finish_traversal(origin_pub, steps, spec.terminator))
        return traversals

    def walk_one(
        self,
        origin_pub: Pub,
        step_specs: List[Tuple[str, Optional[str]]],
        terminator: str,
    ) -> Optional[List[Step]]:
        """
        Walk one origin publish through the whole sequence.

        Args:
            origin_pub (Pub): The publish on the first topic.
            step_specs (List[Tuple[str, Optional[str]]]): The requested sequence, each
                entry a (topic, pinned consuming node or None) pair.
            terminator (str): The CHAIN_TERMINATOR_* deciding what completes the last
                step.

        Returns:
            Optional[List[Step]]: One Step per requested step, or None when the chain
            broke.
        """
        steps: List[Step] = []
        current_pub = origin_pub
        last_index = len(step_specs) - 1
        for step_index, (this_topic, consumer_node) in enumerate(step_specs):
            if step_index == last_index:
                sink_step = self.sink_step(
                    current_pub, this_topic, terminator, consumer_node
                )
                if sink_step is None:
                    return None
                steps.append(sink_step)
                continue

            step = self.relay_step(
                current_pub, this_topic, consumer_node, step_specs[step_index + 1][0]
            )
            if step is None:
                return None
            steps.append(step)
            # the relayed publish opens the next step
            current_pub = step.next_pub
        return steps

    def sink_step(
        self,
        current_pub: Pub,
        this_topic: str,
        terminator: str,
        consumer_node: Optional[str] = None,
    ) -> Optional[Step]:
        """
        Build the step for the last topic, which ends the chain rather than relaying it.

        Under CHAIN_TERMINATOR_PUB the chain is complete once the message is on the
        wire, so the step carries no take and no subscriber has to exist -- which is
        how a chain whose last topic nothing consumes still reports its latency. With
        no take to match, only the publish ties the step to its request, so that mode
        asserts `current_pub` publishes onto `this_topic`.

        Args:
            current_pub (Pub): The publish that opened this step -- under
                CHAIN_TERMINATOR_PUB, the publish that ends the chain.
            this_topic (str): The sink topic.
            terminator (str): The CHAIN_TERMINATOR_* deciding what completes this step.
            consumer_node (Optional[str]): Node label the step pinned as the consumer,
                or None to accept whichever subscriber took it. Pinning it is what makes
                a sink with several subscribers report the one the chain meant; a
                CHAIN_TERMINATOR_PUB chain has no consumer to pin and ignores it.

        Returns:
            Optional[Step]: The step, or None when the chain did not reach its end:
            nothing took this publish on that topic (including when the step pinned a
            node that never took it), or, under CHAIN_TERMINATOR_PUB, the publish never
            reached the wire.
        """
        if terminator == CHAIN_TERMINATOR_PUB:
            # With no take to match, only the publish ties this step to its request.
            assert current_pub.topic == this_topic, (
                f"a {CHAIN_TERMINATOR_PUB}-terminated chain must end at a publish onto "
                f"'{this_topic}', got one onto '{current_pub.topic}'"
            )
            if current_pub.ts_rmw is None:
                return None
            return Step(topic=this_topic, next_topic=None, pub=current_pub, take=None)

        take = next(
            (
                t
                for t in current_pub.takes
                if t.topic == this_topic and take_is_from_node(t, consumer_node)
            ),
            None,
        )
        if take is None or take.ts_rmw is None:
            return None
        return Step(topic=this_topic, next_topic=None, pub=current_pub, take=take)

    def relay_step(
        self,
        current_pub: Pub,
        this_topic: str,
        consumer_node: Optional[str],
        next_topic: str,
    ) -> Optional[Step]:
        """
        Build the step for a non-sink topic, choosing the take whose callback relayed on.

        Args:
            current_pub (Pub): The publish that opened this step.
            this_topic (str): This step's topic.
            consumer_node (Optional[str]): Node label the step pinned as the consumer,
                or None to accept whichever subscriber relayed onward.
            next_topic (str): The following step's topic.

        Returns:
            Optional[Step]: The step, or None when no take on this topic relayed onward.
        """
        candidates = [
            take
            for take in current_pub.takes
            if take.topic == this_topic
            and take.consumer_cb is not None
            and take_is_from_node(take, consumer_node)
        ]
        for take in candidates:
            direct_pub = self.next_step_direct(take.consumer_cb, next_topic)
            if direct_pub is not None:
                return Step(
                    topic=this_topic,
                    next_topic=next_topic,
                    pub=current_pub,
                    take=take,
                    next_pub=direct_pub,
                )
        for take in candidates:
            linked = self.next_step_linked(take.consumer_cb, next_topic)
            if linked is None:
                continue
            relayed_pub, linked_pub, linked_take = linked
            return Step(
                topic=this_topic,
                next_topic=next_topic,
                pub=current_pub,
                take=take,
                is_indirect=True,
                linked_pub=linked_pub,
                linked_take=linked_take,
                next_pub=relayed_pub,
            )
        return None

    def next_step_direct(self, cb: CB, next_topic: str) -> Optional[Pub]:
        """
        Find a publish the consuming callback made itself on the next topic.

        Args:
            cb (CB): The callback that consumed this step's take.
            next_topic (str): Topic the chain continues on.

        Returns:
            Optional[Pub]: The publish, or None when this callback did not republish
            there.
        """
        return next((p for p in cb.pubs if p.topic == next_topic), None)

    def next_step_linked(
        self, cb: CB, next_topic: str
    ) -> Optional[Tuple[Pub, LinkedPub, LinkedTake]]:
        """
        Find a publish a *different* callback made on the next topic, tied to this one
        by a user-declared uid.

        Args:
            cb (CB): The callback that consumed this step's take and published a uid
                into the link graph.
            next_topic (str): Topic the chain continues on.

        Returns:
            Optional[Tuple[Pub, LinkedPub, LinkedTake]]: The publish, the link that
            declared the uid, and the link take that resolved it to the callback which
            produced that publish; None when no declared link leads to `next_topic`.
        """
        for linked_pub in cb.linked_pubs:
            for linked_take in linked_pub.linked_takes:
                if linked_take.cb is None:
                    continue
                pub = next(
                    (p for p in linked_take.cb.pubs if p.topic == next_topic), None
                )
                if pub is not None:
                    return pub, linked_pub, linked_take
        return None

    def finish_traversal(
        self, origin_pub: Pub, steps: List[Step], terminator: str
    ) -> Traversal:
        """
        Compute a completed traversal's end-to-end latencies.

        Both are direct subtractions of two points on one trace clock, not sums of the
        per-step numbers, so neither accumulates per-step rounding.

        Args:
            origin_pub (Pub): The publish that opened the chain.
            steps (List[Step]): The completed steps, in order.
            terminator (str): The CHAIN_TERMINATOR_* the chain declared.

        Returns:
            Traversal: The traversal with both latencies filled in where resolvable.
        """
        sink_step = steps[-1]
        total_latency_ns = None
        if sink_step.end_ts_ns is not None and origin_pub.ts_rmw is not None:
            total_latency_ns = sink_step.end_ts_ns - origin_pub.ts_rmw
        callback_start_latency_ns = None
        sink_cb = None if sink_step.take is None else sink_step.take.consumer_cb
        if (
            sink_cb is not None
            and sink_cb.ts_start is not None
            and origin_pub.ts_rmw is not None
        ):
            callback_start_latency_ns = sink_cb.ts_start - origin_pub.ts_rmw
        return Traversal(
            steps=steps,
            terminator=terminator,
            total_latency_ns=total_latency_ns,
            callback_start_latency_ns=callback_start_latency_ns,
        )

    def topic_for(self, name: str) -> Topic:
        """
        Fetch the Topic for a name, creating it on first use.

        One Topic instance per name is what lets a chain walk compare steps by identity
        rather than by string.

        Args:
            name (str): Fully-qualified topic name.

        Returns:
            Topic: The shared Topic object for that name.
        """
        topic = self.topics.get(name)
        if topic is None:
            topic = Topic(name=name)
            self.topics[name] = topic
        return topic

    def build_topology(self) -> None:
        """Materialize Node/Topic/*Endpoint objects from the TraceAnalysis registries.

        An endpoint whose owning node has no `rcl_node_init` in the trace is skipped.
        """

        self.build_nodes()

        self.build_publishers()

        self.build_subscriptions()

        self.build_timers()

        self.build_services()

    def build_nodes(self) -> None:
        """Materialize Node objects from the TraceAnalysis registries."""
        for node_handle, info in self.trace_analysis.nodes.items():
            self.nodes[node_handle] = Node(
                handle=node_handle,
                name=info[REGISTRY_KEY_NAME],
                namespace=info[FIELD_NAMESPACE],
                label=self.trace_analysis.node_label(node_handle),
                vpid=next(
                    (
                        vpid
                        for vpid, handle in self.trace_analysis.vpid_to_node.items()
                        if handle == node_handle
                    ),
                    None,
                ),
            )

    def build_subscriptions(self) -> None:
        """Materialize SubscriptionEndpoint objects from the TraceAnalysis registries."""
        for handle, info in self.trace_analysis.subscriptions.items():
            node = self.nodes.get(info[REGISTRY_KEY_NODE])
            if node is None:
                continue
            rmw_handle = info[REGISTRY_KEY_RMW]
            endpoint = SubscriptionEndpoint(
                handle=handle,
                rmw_handle=rmw_handle,
                node=node,
                topic=self.topic_for(info[REGISTRY_KEY_TOPIC]),
                queue_depth=info.get(FIELD_QUEUE_DEPTH),
                gid=self.trace_analysis.subscription_gids.get(rmw_handle),
            )
            self.subscription_endpoints[handle] = endpoint
            node.subscriptions.append(endpoint)
            endpoint.topic.subscribers.append(endpoint)
            if rmw_handle is not None:
                self.rmwsub_to_endpoint[rmw_handle] = endpoint

    def build_publishers(self) -> None:
        """Materialize PublisherEndpoint objects from the TraceAnalysis registries."""
        for handle, info in self.trace_analysis.publishers.items():
            node = self.nodes.get(info.get(REGISTRY_KEY_NODE))
            if node is None:
                continue
            rmw_handle = info[REGISTRY_KEY_RMW]
            endpoint = PublisherEndpoint(
                handle=handle,
                rmw_handle=rmw_handle,
                node=node,
                topic=self.topic_for(info[REGISTRY_KEY_TOPIC]),
                queue_depth=info.get(FIELD_QUEUE_DEPTH),
                gid=self.trace_analysis.publisher_gids.get(rmw_handle),
            )
            self.publisher_endpoints[handle] = endpoint
            node.publishers.append(endpoint)
            endpoint.topic.publishers.append(endpoint)
            if rmw_handle is not None:
                self.rmwpub_to_endpoint[rmw_handle] = endpoint

    def build_timers(self) -> None:
        """Materialize TimerEndpoint objects from the TraceAnalysis registries."""
        for handle, info in self.trace_analysis.timers.items():
            node = self.nodes.get(info.get(REGISTRY_KEY_NODE))
            if node is None:
                continue
            endpoint = TimerEndpoint(
                handle=handle, node=node, period_ns=info.get(FIELD_PERIOD)
            )
            self.timer_endpoints[handle] = endpoint
            node.timers.append(endpoint)

    def build_services(self) -> None:
        """Materialize ServiceEndpoint objects from the TraceAnalysis registries."""
        for handle, info in self.trace_analysis.services.items():
            node = self.nodes.get(info.get(REGISTRY_KEY_NODE))
            if node is None:
                continue
            endpoint = ServiceEndpoint(
                handle=handle,
                name=info.get(REGISTRY_KEY_NAME, ""),
                node=node,
            )
            self.service_endpoints[handle] = endpoint
            node.services.append(endpoint)


def source_timestamp_or_none(fields: Dict[str, Any], key: str) -> Optional[int]:
    """
    Read a DDS source_timestamp field, treating the tracepoint's 0 as "unavailable".

    Args:
        fields (Dict[str, Any]): The event's decoded fields.
        key (str): Field name holding the timestamp.

    Returns:
        Optional[int]: The timestamp, or None if absent or zero.
    """
    raw = fields.get(key, 0)
    return raw if raw != 0 else None
