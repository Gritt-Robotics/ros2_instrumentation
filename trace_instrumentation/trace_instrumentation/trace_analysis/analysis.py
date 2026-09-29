# Copyright 2026 Gritt Robotics Inc.

"""
analysis.py
===========

TraceAnalysis: single-pass registry/count builder over a decoded event stream.
"""

from collections import Counter, defaultdict
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from trace_instrumentation.trace_analysis.ctf.reader import Event, Metadata
from trace_instrumentation.trace_analysis.event_fields import (
    FIELD_CALLBACK,
    FIELD_CLIENT_HANDLE,
    FIELD_CONTEXT_HANDLE,
    FIELD_GID,
    FIELD_GOAL_LABEL,
    FIELD_HANDLE,
    FIELD_IS_INTRA_PROCESS,
    FIELD_LOGGER_NAME,
    FIELD_MSG,
    FIELD_NAMESPACE,
    FIELD_NODE_HANDLE,
    FIELD_NODE_NAME,
    FIELD_PERIOD,
    FIELD_PUBLISHER_HANDLE,
    FIELD_QUEUE_DEPTH,
    FIELD_RMW_CLIENT_HANDLE,
    FIELD_RMW_HANDLE,
    FIELD_RMW_PUBLISHER_HANDLE,
    FIELD_RMW_SERVICE_HANDLE,
    FIELD_RMW_SUBSCRIPTION_HANDLE,
    FIELD_SERVICE_HANDLE,
    FIELD_SERVICE_NAME,
    FIELD_START_LABEL,
    FIELD_STATE_MACHINE,
    FIELD_SUBSCRIPTION,
    FIELD_SUBSCRIPTION_HANDLE,
    FIELD_SYMBOL,
    FIELD_TAKEN,
    FIELD_THREAD,
    FIELD_TIMER_HANDLE,
    FIELD_TOPIC_NAME,
    FIELD_VERSION,
)
from trace_instrumentation.trace_analysis.event_names import (
    EVT_CALLBACK_END,
    EVT_CALLBACK_START,
    EVT_LTTNG_PYTHON_EVENT,
    EVT_RCL_CLIENT_INIT,
    EVT_RCL_INIT,
    EVT_RCL_LIFECYCLE_STATE_MACHINE_INIT,
    EVT_RCL_LIFECYCLE_TRANSITION,
    EVT_RCL_NODE_INIT,
    EVT_RCL_PUBLISH,
    EVT_RCL_PUBLISHER_INIT,
    EVT_RCL_SERVICE_INIT,
    EVT_RCL_SUBSCRIPTION_INIT,
    EVT_RCL_TIMER_INIT,
    EVT_RCLCPP_CALLBACK_REGISTER,
    EVT_RCLCPP_EXECUTOR_EXECUTE,
    EVT_RCLCPP_EXECUTOR_GET_NEXT_READY,
    EVT_RCLCPP_EXECUTOR_WAIT_FOR_WORK,
    EVT_RCLCPP_INTRA_PUBLISH,
    EVT_RCLCPP_SERVICE_CALLBACK_ADDED,
    EVT_RCLCPP_SUBSCRIPTION_CALLBACK_ADDED,
    EVT_RCLCPP_SUBSCRIPTION_INIT,
    EVT_RCLCPP_TIMER_CALLBACK_ADDED,
    EVT_RCLCPP_TIMER_LINK_NODE,
    EVT_RMW_PUBLISH,
    EVT_RMW_PUBLISHER_INIT,
    EVT_RMW_SUBSCRIPTION_INIT,
    EVT_RMW_TAKE,
)
from trace_instrumentation.trace_analysis.metrics.stats import (
    fmt_hex_or_str,
)
from trace_instrumentation.trace_analysis.python_zones import (
    PYTHON_ZONE_PHASE_BEGIN,
    PYTHON_ZONE_PHASE_END,
    PythonZone,
    parse_python_zone_msg,
)

# TraceAnalysis's own registry-dict keys, distinct from the raw CTF field names:
# a key may rename its field, e.g. REGISTRY_KEY_NODE holds a FIELD_NODE_HANDLE value.
REGISTRY_KEY_NAME = "name"
REGISTRY_KEY_RMW = "rmw"
REGISTRY_KEY_NODE = "node"
REGISTRY_KEY_TOPIC = "topic"
REGISTRY_KEY_VPID = "vpid"

# self.callback_owner[callback_ptr] = (kind, owning_handle)
CALLBACK_OWNER_KIND_TIMER = "timer"
CALLBACK_OWNER_KIND_SUBSCRIPTION = "subscription"
CALLBACK_OWNER_KIND_SERVICE = "service"

# Keys of the CTF metadata env block, as written by the LTTng session daemon.
ENV_KEY_TRACE_NAME = "trace_name"
ENV_KEY_HOSTNAME = "hostname"
ENV_KEY_TRACE_CREATION_DATETIME = "trace_creation_datetime"
ENV_KEY_TRACER_NAME = "tracer_name"
ENV_KEY_TRACER_MAJOR = "tracer_major"
ENV_KEY_TRACER_MINOR = "tracer_minor"
ENV_KEY_ARCHITECTURE_BIT_WIDTH = "architecture_bit_width"

# Rendered in place of a value the trace didn't record.
UNKNOWN_VALUE = "?"


class LifecycleTransition(NamedTuple):
    """One `ros2:rcl_lifecycle_transition` event."""

    ts_ns: int
    state_machine: Any
    start_label: str
    goal_label: str


class TraceAnalysis:
    """Single-pass registry/count builder over a decoded event stream: nodes,
    publishers, subscriptions, timers, callbacks, Python zones, and their
    associated counts/timings."""

    def __init__(self, meta: Metadata, events: List[Event]) -> None:
        """
        Initialize empty registries/counts, ready for build().

        Args:
            meta (Metadata): Parsed CTF metadata.
            events (List[Event]): Decoded events, sorted by clock cycle.
        """
        self.meta = meta
        self.events = events
        # Registries (handle -> info). Handles come from untyped CTF fields, so they are
        # `Any`: the source data doesn't guarantee int (e.g. vpid may be None).
        self.nodes: Dict[Any, dict] = {}  # node_handle -> {name, namespace, rmw}
        self.publishers: Dict[Any, dict] = {}  # publisher_handle -> {...}
        self.subscriptions: Dict[Any, dict] = {}  # subscription_handle (rcl) -> {...}
        self.rclcpp_subs: Dict[Any, Any] = {}  # rclcpp subscription ptr -> rcl handle
        self.timers: Dict[Any, dict] = {}  # timer_handle -> {period, node, callback}
        self.services: Dict[Any, dict] = {}  # service_handle -> {name, node, callback}
        self.clients: Dict[Any, dict] = {}  # client_handle -> {name, node}
        self.callback_symbols: Dict[Any, Any] = {}  # callback ptr -> symbol
        self.callback_owner: Dict[
            Any, Tuple[str, Any]
        ] = {}  # callback ptr -> ('timer'|'subscription'|'service', handle)
        self.contexts: Dict[Any, Any] = {}  # context_handle -> version
        self.lifecycle_sms: Dict[Any, Any] = {}  # state_machine -> node_handle
        self.vpid_to_node: Dict[
            Any, Any
        ] = {}  # vpid -> node_handle (assumes one node per process)
        # Keyed by rmw handle, since that is what the rmw_*_init events carry: a GID
        # names an endpoint across processes and hosts, a handle only within one.
        self.publisher_gids: Dict[Any, List[int]] = {}  # rmw_publisher_handle -> gid
        self.subscription_gids: Dict[Any, List[int]] = {}  # rmw_sub_handle -> gid

        self.event_counts: Counter = Counter()
        self.thread_counts: Counter = Counter()  # (vpid, vtid, procname) -> count
        self.proc_counts: Counter = Counter()  # (vpid, procname) -> count

        # callback timing
        self.cb_durations: Dict[Any, List[int]] = defaultdict(
            list
        )  # callback ptr -> [ns,...]
        self.cb_intra: Counter = Counter()  # callback ptr -> intra count
        self.cb_inter: Counter = Counter()  # callback ptr -> inter count

        # publish / take counts
        self.rcl_publish: Counter = Counter()  # publisher_handle -> count
        self.intra_publish: Counter = Counter()  # publisher_handle -> count
        self.rmw_publish_count: int = 0
        self.rmw_take: Counter = Counter()  # rmw_subscription_handle -> taken count
        self.rmw_take_empty: Counter = Counter()  # taken == 0
        self.lifecycle_transitions: List[LifecycleTransition] = []

        # executor spin activity
        self.executor_execute_counts: Counter = Counter()  # handle -> dispatch count
        self.executor_wait_durations: Dict[Any, List[int]] = defaultdict(
            list
        )  # (vpid, vtid) -> [ns blocked in wait_for_work, ...]
        self.executor_dispatch_durations: Dict[Any, List[int]] = defaultdict(
            list
        )  # handle -> [ns from ready check to execute, ...]

        # Python zones (lttng_python:event)
        self.python_zones: List["PythonZone"] = []  # [PythonZone, ...]
        self._open_python_zones: Dict[
            Tuple[Optional[int], str, str, int], List["PythonZone"]
        ] = {}  # (vpid, logger_name, zone_name, thread_id) -> [PythonZone, ...] stack
        self.python_zone_durations: Dict[Any, List[int]] = defaultdict(
            list
        )  # zone_label -> [ns, ...]
        self.python_zone_counts: Counter = Counter()  # zone_label -> count

    # ---- pass 1: build registries & counts ---- #
    def build(self) -> "TraceAnalysis":
        """
        Populate all registries/counts/timings from self.events in one pass.

        Returns:
            TraceAnalysis: self, for chaining (e.g. `TraceAnalysis(meta, events).build()`).
        """
        # open callback start ts_ns keyed by (vpid, vtid, callback ptr)
        open_cb: Dict[Tuple[Any, Any, Any], Tuple[int, Any]] = {}
        # executor spin state keyed by (vpid, vtid): ts_ns of the wait_for_work awaiting
        # its ready check, and of the latest ready check awaiting its execute.
        open_wait_ns: Dict[Tuple[Any, Any], int] = {}
        open_ready_ns: Dict[Tuple[Any, Any], int] = {}

        for event in self.events:
            self.event_counts[event.name] += 1
            self.thread_counts[(event.vpid, event.vtid, event.procname)] += 1
            self.proc_counts[(event.vpid, event.procname)] += 1
            fields = event.fields
            event_name = event.name

            if event_name in (
                EVT_RCL_NODE_INIT,
                EVT_RCL_PUBLISHER_INIT,
                EVT_RCL_SUBSCRIPTION_INIT,
                EVT_RCL_TIMER_INIT,
                EVT_RCL_SERVICE_INIT,
                EVT_RCL_CLIENT_INIT,
                EVT_RCL_INIT,
                EVT_RCL_LIFECYCLE_STATE_MACHINE_INIT,
            ):
                self.handle_rcl_initializing_event(event)

            elif event_name in (EVT_RMW_PUBLISHER_INIT, EVT_RMW_SUBSCRIPTION_INIT):
                self.handle_rmw_endpoint_init(event)

            elif event_name == EVT_RCLCPP_SUBSCRIPTION_INIT:
                self.rclcpp_subs[fields[FIELD_SUBSCRIPTION]] = fields[
                    FIELD_SUBSCRIPTION_HANDLE
                ]
            elif event_name == EVT_RCLCPP_TIMER_LINK_NODE:
                self.timers.setdefault(fields[FIELD_TIMER_HANDLE], {}).update({
                    REGISTRY_KEY_NODE: fields[FIELD_NODE_HANDLE]
                })
            elif event_name == EVT_RCLCPP_TIMER_CALLBACK_ADDED:
                self.timers.setdefault(fields[FIELD_TIMER_HANDLE], {}).update({
                    FIELD_CALLBACK: fields[FIELD_CALLBACK]
                })
                self.callback_owner[fields[FIELD_CALLBACK]] = (
                    CALLBACK_OWNER_KIND_TIMER,
                    fields[FIELD_TIMER_HANDLE],
                )
            elif event_name == EVT_RCLCPP_SUBSCRIPTION_CALLBACK_ADDED:
                # Falls back to the raw rclcpp pointer when rclcpp_subscription_init
                # wasn't captured: an opaque ownership key, not the resolved rcl handle.
                rcl = self.rclcpp_subs.get(
                    fields[FIELD_SUBSCRIPTION], fields[FIELD_SUBSCRIPTION]
                )
                self.callback_owner[fields[FIELD_CALLBACK]] = (
                    CALLBACK_OWNER_KIND_SUBSCRIPTION,
                    rcl,
                )
            elif event_name == EVT_RCLCPP_SERVICE_CALLBACK_ADDED:
                self.callback_owner[fields[FIELD_CALLBACK]] = (
                    CALLBACK_OWNER_KIND_SERVICE,
                    fields[FIELD_SERVICE_HANDLE],
                )
                self.services.setdefault(fields[FIELD_SERVICE_HANDLE], {}).update({
                    FIELD_CALLBACK: fields[FIELD_CALLBACK]
                })
            elif event_name == EVT_RCLCPP_CALLBACK_REGISTER:
                self.callback_symbols[fields[FIELD_CALLBACK]] = fields[FIELD_SYMBOL]
            elif event_name == EVT_RCL_LIFECYCLE_TRANSITION:
                self.lifecycle_transitions.append(
                    LifecycleTransition(
                        ts_ns=event.ts_ns,
                        state_machine=fields[FIELD_STATE_MACHINE],
                        start_label=fields[FIELD_START_LABEL],
                        goal_label=fields[FIELD_GOAL_LABEL],
                    )
                )

            # ---- Python zones (lttng_python:event) ---- #
            elif event_name == EVT_LTTNG_PYTHON_EVENT:
                self.handle_python_event(event)

            # ---- runtime ---- #
            elif event_name in (
                EVT_RCLCPP_EXECUTOR_WAIT_FOR_WORK,
                EVT_RCLCPP_EXECUTOR_GET_NEXT_READY,
                EVT_RCLCPP_EXECUTOR_EXECUTE,
            ):
                self.handle_executor_event(event, open_wait_ns, open_ready_ns)
            elif event_name == EVT_CALLBACK_START:
                open_cb[(event.vpid, event.vtid, fields[FIELD_CALLBACK])] = (
                    event.ts_ns,
                    fields[FIELD_IS_INTRA_PROCESS],
                )
            elif event_name == EVT_CALLBACK_END:
                key = (event.vpid, event.vtid, fields[FIELD_CALLBACK])
                start = open_cb.pop(key, None)
                if start is not None:
                    start_ts_ns, is_intra_process = start
                    duration_ns = event.ts_ns - start_ts_ns
                    self.cb_durations[fields[FIELD_CALLBACK]].append(duration_ns)
                    if is_intra_process != 0:
                        self.cb_intra[fields[FIELD_CALLBACK]] += 1
                    else:
                        self.cb_inter[fields[FIELD_CALLBACK]] += 1
            elif event_name == EVT_RCL_PUBLISH:
                self.rcl_publish[fields[FIELD_PUBLISHER_HANDLE]] += 1
            elif event_name == EVT_RCLCPP_INTRA_PUBLISH:
                self.intra_publish[fields[FIELD_PUBLISHER_HANDLE]] += 1
            elif event_name == EVT_RMW_PUBLISH:
                self.rmw_publish_count += 1
            elif event_name == EVT_RMW_TAKE:
                self.rmw_take[fields[FIELD_RMW_SUBSCRIPTION_HANDLE]] += 1
                if fields.get(FIELD_TAKEN, 0) == 0:
                    self.rmw_take_empty[fields[FIELD_RMW_SUBSCRIPTION_HANDLE]] += 1

        return self

    # ---- helpers ---- #
    def node_label(self, node_handle: int) -> str:
        """
        Human-readable "/namespace/name" label for a node handle.

        Args:
            node_handle (int): The node's `rcl_node_t` handle.

        Returns:
            str: The node's namespaced name, or a placeholder if unknown.
        """
        info = self.nodes.get(node_handle)
        if info is None:
            return f"<unknown node {fmt_hex_or_str(node_handle)}>"
        namespace = info[FIELD_NAMESPACE]
        name = info[REGISTRY_KEY_NAME]
        if namespace in ("", "/"):
            return f"/{name}"
        if namespace.endswith("/"):
            return f"{namespace}{name}"
        return f"{namespace}/{name}"

    def callback_label(self, cb: int) -> str:
        """
        Human-readable label for a callback pointer.

        Args:
            cb (int): Callback object pointer.

        Returns:
            str: The owning timer/subscription/service target (e.g. "sub:/topic")
            when known, else the demangled function symbol, else the hex pointer.
        """
        symbol = self.callback_symbols.get(cb)
        owner = self.callback_owner.get(cb)
        target = ""
        if owner is not None:
            kind, handle = owner
            if kind == CALLBACK_OWNER_KIND_TIMER:
                timer = self.timers.get(handle, {})
                node = (
                    self.node_label(timer[REGISTRY_KEY_NODE])
                    if REGISTRY_KEY_NODE in timer
                    else UNKNOWN_VALUE
                )
                target = f"timer@{node}"
            elif kind == CALLBACK_OWNER_KIND_SUBSCRIPTION:
                subscription = self.subscriptions.get(handle)
                target = (
                    f"sub:{subscription[REGISTRY_KEY_TOPIC]}"
                    if subscription is not None
                    else f"sub:{fmt_hex_or_str(handle)}"
                )
            elif kind == CALLBACK_OWNER_KIND_SERVICE:
                service = self.services.get(handle, {})
                target = f"srv:{service.get(REGISTRY_KEY_NAME, fmt_hex_or_str(handle))}"
        has_target = len(target) > 0
        if symbol is not None and len(symbol) > 0:
            return f"{target}  [{symbol}]" if has_target else symbol
        if has_target:
            return target
        return fmt_hex_or_str(cb)

    def handle_rcl_initializing_event(self, event: Event) -> None:
        """
        Handle an rcl initialization trace event.

        Args:
            event (Event): The trace event to handle.
        """
        fields = event.fields
        event_name = event.name
        if event_name == EVT_RCL_NODE_INIT:
            self.nodes[fields[FIELD_NODE_HANDLE]] = {
                REGISTRY_KEY_NAME: fields[FIELD_NODE_NAME],
                FIELD_NAMESPACE: fields[FIELD_NAMESPACE],
                REGISTRY_KEY_RMW: fields[FIELD_RMW_HANDLE],
            }
            # rcl_node_init fires from within the process that owns the node -- used to
            # resolve a vpid to a node for events (e.g. Python zones) that carry no handle.
            self.vpid_to_node[event.vpid] = fields[FIELD_NODE_HANDLE]
        elif event_name == EVT_RCL_PUBLISHER_INIT:
            self.publishers[fields[FIELD_PUBLISHER_HANDLE]] = {
                REGISTRY_KEY_NODE: fields[FIELD_NODE_HANDLE],
                REGISTRY_KEY_RMW: fields[FIELD_RMW_PUBLISHER_HANDLE],
                REGISTRY_KEY_TOPIC: fields[FIELD_TOPIC_NAME],
                FIELD_QUEUE_DEPTH: fields[FIELD_QUEUE_DEPTH],
            }
        elif event_name == EVT_RCL_SUBSCRIPTION_INIT:
            self.subscriptions[fields[FIELD_SUBSCRIPTION_HANDLE]] = {
                REGISTRY_KEY_NODE: fields[FIELD_NODE_HANDLE],
                REGISTRY_KEY_RMW: fields[FIELD_RMW_SUBSCRIPTION_HANDLE],
                REGISTRY_KEY_TOPIC: fields[FIELD_TOPIC_NAME],
                FIELD_QUEUE_DEPTH: fields[FIELD_QUEUE_DEPTH],
            }
        elif event_name == EVT_RCL_TIMER_INIT:
            # rcl_timer_init fires in the owning process for C++ and Python timers, so
            # vpid is how a Python timer (no correlatable handle) recovers its period.
            self.timers.setdefault(fields[FIELD_TIMER_HANDLE], {}).update({
                FIELD_PERIOD: fields[FIELD_PERIOD],
                REGISTRY_KEY_VPID: event.vpid,
            })
        elif event_name == EVT_RCL_SERVICE_INIT:
            self.services.setdefault(fields[FIELD_SERVICE_HANDLE], {}).update({
                REGISTRY_KEY_NAME: fields[FIELD_SERVICE_NAME],
                REGISTRY_KEY_NODE: fields[FIELD_NODE_HANDLE],
                REGISTRY_KEY_RMW: fields[FIELD_RMW_SERVICE_HANDLE],
            })
        elif event_name == EVT_RCL_CLIENT_INIT:
            self.clients[fields[FIELD_CLIENT_HANDLE]] = {
                REGISTRY_KEY_NAME: fields[FIELD_SERVICE_NAME],
                REGISTRY_KEY_NODE: fields[FIELD_NODE_HANDLE],
                REGISTRY_KEY_RMW: fields[FIELD_RMW_CLIENT_HANDLE],
            }
        elif event_name == EVT_RCL_INIT:
            self.contexts[fields[FIELD_CONTEXT_HANDLE]] = fields[FIELD_VERSION]
        elif event_name == EVT_RCL_LIFECYCLE_STATE_MACHINE_INIT:
            self.lifecycle_sms[fields[FIELD_STATE_MACHINE]] = fields[FIELD_NODE_HANDLE]

    def handle_rmw_endpoint_init(self, event: Event) -> None:
        """
        Record an rmw endpoint's DDS GID against its rmw-level handle.

        Args:
            event (Event): A `ros2:rmw_publisher_init` or `ros2:rmw_subscription_init`
                event.
        """
        fields = event.fields
        if event.name == EVT_RMW_PUBLISHER_INIT:
            self.publisher_gids[fields[FIELD_RMW_PUBLISHER_HANDLE]] = fields[FIELD_GID]
        elif event.name == EVT_RMW_SUBSCRIPTION_INIT:
            self.subscription_gids[fields[FIELD_RMW_SUBSCRIPTION_HANDLE]] = fields[
                FIELD_GID
            ]

    def handle_executor_event(
        self,
        event: Event,
        open_wait_ns: Dict[Tuple[Any, Any], int],
        open_ready_ns: Dict[Tuple[Any, Any], int],
    ) -> None:
        """
        Fold one rclcpp executor spin event into the executor counts and durations.

        A spin runs get_next_ready -> (wait_for_work -> get_next_ready) -> execute.

        Args:
            event (Event): A `ros2:rclcpp_executor_*` event.
            open_wait_ns (Dict[Tuple[Any, Any], int]): Per-(vpid, vtid) ts_ns of the
                wait_for_work still awaiting its ready check; consumed in place.
            open_ready_ns (Dict[Tuple[Any, Any], int]): Per-(vpid, vtid) ts_ns of the
                latest ready check, still awaiting its execute; consumed in place.
        """
        thread_key = (event.vpid, event.vtid)
        if event.name == EVT_RCLCPP_EXECUTOR_WAIT_FOR_WORK:
            open_wait_ns[thread_key] = event.ts_ns
        elif event.name == EVT_RCLCPP_EXECUTOR_GET_NEXT_READY:
            wait_start_ns = open_wait_ns.pop(thread_key, None)
            if wait_start_ns is not None:
                self.executor_wait_durations[thread_key].append(
                    event.ts_ns - wait_start_ns
                )
            open_ready_ns[thread_key] = event.ts_ns
        elif event.name == EVT_RCLCPP_EXECUTOR_EXECUTE:
            handle = event.fields[FIELD_HANDLE]
            self.executor_execute_counts[handle] += 1
            ready_ns = open_ready_ns.pop(thread_key, None)
            if ready_ns is not None:
                self.executor_dispatch_durations[handle].append(event.ts_ns - ready_ns)

    def handle_python_event(self, event: Event) -> None:
        """
        Handle a Python-related trace event.

        Args:
            event (Event): The trace event to handle.
        """
        fields = event.fields
        msg = fields.get(FIELD_MSG, "")
        logger_name = fields.get(FIELD_LOGGER_NAME, "")
        thread_id = fields.get(FIELD_THREAD, 0)
        # Parse msg format: "<zone_name>:begin" or "<zone_name>:end"
        # where zone_name is like "_tick", "_on_message", "destroy_node"
        parsed = parse_python_zone_msg(msg)
        if parsed is None:
            return
        zone_name, phase = parsed
        # Include vpid: thread_id is a process-local threading.get_ident() value that
        # can repeat across node processes -- the same collision class as open_cb.
        zone_key = (event.vpid, logger_name, zone_name, thread_id)
        if phase == PYTHON_ZONE_PHASE_BEGIN:
            zone = PythonZone(
                id=len(self.python_zones),
                logger_name=logger_name,
                zone_name=zone_name,
                vpid=event.vpid,
                vtid=event.vtid,
                ts_begin_ns=event.ts_ns,
            )
            self.python_zones.append(zone)
            self._open_python_zones.setdefault(zone_key, []).append(zone)
        elif phase == PYTHON_ZONE_PHASE_END:
            stack = self._open_python_zones.get(zone_key)
            zone = stack.pop() if stack is not None and len(stack) > 0 else None
            if stack is not None and len(stack) == 0:
                self._open_python_zones.pop(zone_key, None)
            if zone is None:
                return
            zone.ts_end_ns = event.ts_ns
            zone.finalize()
            # Label combines logger_name (publisher/subscriber discriminator) + zone_name
            zone_label = f"{logger_name}:{zone_name}"
            self.python_zone_counts[zone_label] += 1
            if zone.duration_ns is not None:
                self.python_zone_durations[zone_label].append(zone.duration_ns)


# Internal ROS bookkeeping topics every node publishes/subscribes to; omitted from
# per-topic reports so they don't drown out application traffic.
INTERNAL_TOPICS = frozenset({"/parameter_events", "/rosout"})


def is_internal_topic(topic: Optional[str]) -> bool:
    """Whether a topic is an internal ROS bookkeeping topic to omit from a report.

    Args:
        topic (Optional[str]): Topic name, or None.

    Returns:
        bool: True if the topic is an internal ROS topic (e.g. /parameter_events).
    """
    return topic in INTERNAL_TOPICS
