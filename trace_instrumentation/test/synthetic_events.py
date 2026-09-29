# Copyright 2026 Gritt Robotics Inc.

"""
Hand-built `Event` fixture builders for testing `TraceAnalysis` without needing
a real CTF trace capture.
"""

from typing import Any, Dict, List, Optional

from trace_instrumentation.trace_analysis.ctf.reader import Event
from trace_instrumentation.trace_analysis.event_fields import (
    FIELD_CALLBACK,
    FIELD_CLIENT_HANDLE,
    FIELD_CONTEXT_HANDLE,
    FIELD_GID,
    FIELD_GOAL_LABEL,
    FIELD_IS_INTRA_PROCESS,
    FIELD_LOGGER_NAME,
    FIELD_MESSAGE,
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
    FIELD_SOURCE_TIMESTAMP,
    FIELD_START_LABEL,
    FIELD_STATE_MACHINE,
    FIELD_SUBSCRIPTION,
    FIELD_SUBSCRIPTION_HANDLE,
    FIELD_SYMBOL,
    FIELD_TAKEN,
    FIELD_THREAD,
    FIELD_THREAD_NAME,
    FIELD_TIMER_HANDLE,
    FIELD_TIMESTAMP,
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
    EVT_RCL_TAKE,
    EVT_RCL_TIMER_INIT,
    EVT_RCLCPP_CALLBACK_REGISTER,
    EVT_RCLCPP_PUBLISH,
    EVT_RCLCPP_SUBSCRIPTION_CALLBACK_ADDED,
    EVT_RCLCPP_SUBSCRIPTION_INIT,
    EVT_RCLCPP_TAKE,
    EVT_RCLCPP_TIMER_CALLBACK_ADDED,
    EVT_RCLCPP_TIMER_LINK_NODE,
    EVT_RMW_PUBLISH,
    EVT_RMW_PUBLISHER_INIT,
    EVT_RMW_SUBSCRIPTION_INIT,
    EVT_RMW_TAKE,
)

DEFAULT_QUEUE_DEPTH = 10
DEFAULT_PROCNAME = "test_proc"
DEFAULT_LOGGER_NAME = "test_logger"
DEFAULT_PYTHON_THREAD_ID = 1
DEFAULT_PYTHON_THREAD_NAME = "MainThread"
# rcl_node_init's rmw_handle field isn't exercised by any test yet -- a fixed
# placeholder keeps node_init_event()'s signature focused on what tests actually vary.
DEFAULT_RMW_HANDLE = 0
DEFAULT_NAMESPACE = "/"
# rclcpp_publish -> rcl_publish -> rmw_publish are emitted a few ns apart in a real
# trace; any small positive gap keeps their relative order deterministic here.
PUBLISH_TRIPLET_STEP_NS = 10


def make_event(
    name: str,
    ts_ns: int,
    vpid: int,
    vtid: int,
    fields: Dict[str, Any],
    procname: str = DEFAULT_PROCNAME,
) -> Event:
    """
    Build one synthetic Event with matching ts_ns/ts_cycles (tests never exercise
    the CTF clock-offset/frequency conversion path, so the two can be identical).

    Args:
        name (str): Tracepoint name, e.g. "ros2:rmw_take".
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        fields (Dict[str, Any]): Event-specific field values.
        procname (str): Process name. Defaults to DEFAULT_PROCNAME.

    Returns:
        Event: The constructed event.
    """
    return Event(ts_ns, ts_ns, name, vpid, vtid, procname, 0, fields)


def node_init_event(
    ts_ns: int,
    vpid: int,
    vtid: int,
    node_handle: int,
    node_name: str,
    namespace: str = DEFAULT_NAMESPACE,
    rmw_handle: int = DEFAULT_RMW_HANDLE,
) -> Event:
    """
    Build a `ros2:rcl_node_init` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        node_handle (int): Node handle.
        node_name (str): Node name.
        namespace (str): Node namespace. Defaults to DEFAULT_NAMESPACE.
        rmw_handle (int): rmw-layer node handle. Defaults to DEFAULT_RMW_HANDLE.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_RCL_NODE_INIT,
        ts_ns,
        vpid,
        vtid,
        {
            FIELD_NODE_HANDLE: node_handle,
            FIELD_NODE_NAME: node_name,
            FIELD_NAMESPACE: namespace,
            FIELD_RMW_HANDLE: rmw_handle,
        },
    )


def publisher_init_event(
    ts_ns: int,
    vpid: int,
    vtid: int,
    publisher_handle: int,
    node_handle: int,
    rmw_publisher_handle: int,
    topic_name: str,
) -> Event:
    """
    Build a `ros2:rcl_publisher_init` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        publisher_handle (int): Publisher handle.
        node_handle (int): Owning node handle.
        rmw_publisher_handle (int): rmw-layer publisher handle.
        topic_name (str): Topic name.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_RCL_PUBLISHER_INIT,
        ts_ns,
        vpid,
        vtid,
        {
            FIELD_PUBLISHER_HANDLE: publisher_handle,
            FIELD_NODE_HANDLE: node_handle,
            FIELD_RMW_PUBLISHER_HANDLE: rmw_publisher_handle,
            FIELD_TOPIC_NAME: topic_name,
            FIELD_QUEUE_DEPTH: DEFAULT_QUEUE_DEPTH,
        },
    )


def subscription_init_event(
    ts_ns: int,
    vpid: int,
    vtid: int,
    subscription_handle: int,
    node_handle: int,
    rmw_subscription_handle: int,
    topic_name: str,
) -> Event:
    """
    Build a `ros2:rcl_subscription_init` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        subscription_handle (int): Subscription handle.
        node_handle (int): Owning node handle.
        rmw_subscription_handle (int): rmw-layer subscription handle.
        topic_name (str): Topic name.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_RCL_SUBSCRIPTION_INIT,
        ts_ns,
        vpid,
        vtid,
        {
            FIELD_SUBSCRIPTION_HANDLE: subscription_handle,
            FIELD_NODE_HANDLE: node_handle,
            FIELD_RMW_SUBSCRIPTION_HANDLE: rmw_subscription_handle,
            FIELD_TOPIC_NAME: topic_name,
            FIELD_QUEUE_DEPTH: DEFAULT_QUEUE_DEPTH,
        },
    )


def timer_events(
    ts_ns: int,
    vpid: int,
    vtid: int,
    timer_handle: int,
    node_handle: int,
    callback: int,
    period: int,
) -> List[Event]:
    """
    Build the rcl_timer_init/rclcpp_timer_link_node/rclcpp_timer_callback_added
    triplet that fully registers one C++ timer: configured period, owning node,
    and callback pointer.

    Args:
        ts_ns (int): Timestamp shared by all three events.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        timer_handle (int): Timer handle.
        node_handle (int): Owning node handle.
        callback (int): Callback object pointer.
        period (int): Configured timer period in nanoseconds.

    Returns:
        List[Event]: [rcl_timer_init, rclcpp_timer_link_node,
        rclcpp_timer_callback_added] events, in order.
    """
    return [
        timer_init_event(ts_ns, vpid, vtid, timer_handle, period),
        timer_link_node_event(ts_ns, vpid, vtid, timer_handle, node_handle),
        timer_callback_added_event(ts_ns, vpid, vtid, timer_handle, callback),
    ]


def rmw_take_event(
    ts_ns: int,
    vpid: int,
    vtid: int,
    message: int,
    rmw_subscription_handle: int,
    source_timestamp: int,
    taken: int = 1,
) -> Event:
    """
    Build a `ros2:rmw_take` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        message (int): Message pointer.
        rmw_subscription_handle (int): rmw-layer subscription handle.
        source_timestamp (int): DDS source_timestamp carried by the message.
        taken (int): Whether the take succeeded. Defaults to 1.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_RMW_TAKE,
        ts_ns,
        vpid,
        vtid,
        {
            FIELD_MESSAGE: message,
            FIELD_RMW_SUBSCRIPTION_HANDLE: rmw_subscription_handle,
            FIELD_SOURCE_TIMESTAMP: source_timestamp,
            FIELD_TAKEN: taken,
        },
    )


def publish_events(
    ts_ns: int,
    vpid: int,
    vtid: int,
    message: int,
    publisher_handle: int,
    source_timestamp: int,
    rmw_publisher_handle: Optional[int] = None,
) -> List[Event]:
    """
    Build the rclcpp_publish/rcl_publish/rmw_publish triplet for one published
    message, PUBLISH_TRIPLET_STEP_NS apart, starting at ts_ns.

    Args:
        ts_ns (int): Timestamp of the first (rclcpp_publish) event.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        message (int): Message pointer (shared join key across the triplet).
        publisher_handle (int): Publisher handle.
        source_timestamp (int): DDS source_timestamp carried by rmw_publish.
        rmw_publisher_handle (Optional[int]): rmw-layer publisher handle for the
            rmw_publish event, as the vendored fork's 3-arg tracepoint carries. None omits
            the field, matching the stock 1-arg tracepoint. Defaults to None.

    Returns:
        List[Event]: [rclcpp_publish, rcl_publish, rmw_publish] events, in order.
    """
    rmw_fields = {FIELD_MESSAGE: message, FIELD_TIMESTAMP: source_timestamp}
    if rmw_publisher_handle is not None:
        rmw_fields[FIELD_RMW_PUBLISHER_HANDLE] = rmw_publisher_handle
    return [
        make_event(EVT_RCLCPP_PUBLISH, ts_ns, vpid, vtid, {FIELD_MESSAGE: message}),
        make_event(
            EVT_RCL_PUBLISH,
            ts_ns + PUBLISH_TRIPLET_STEP_NS,
            vpid,
            vtid,
            {FIELD_MESSAGE: message, FIELD_PUBLISHER_HANDLE: publisher_handle},
        ),
        make_event(
            EVT_RMW_PUBLISH,
            ts_ns + 2 * PUBLISH_TRIPLET_STEP_NS,
            vpid,
            vtid,
            rmw_fields,
        ),
    ]


def python_zone_event(
    ts_ns: int,
    vpid: int,
    vtid: int,
    zone_name: str,
    phase: str,
    logger_name: str = DEFAULT_LOGGER_NAME,
    thread_id: int = DEFAULT_PYTHON_THREAD_ID,
    thread_name: str = DEFAULT_PYTHON_THREAD_NAME,
) -> Event:
    """
    Build an `lttng_python:event` zone begin/end event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        zone_name (str): Instrumented zone name, e.g. "_on_message".
        phase (str): "begin" or "end".
        logger_name (str): Logger name. Defaults to DEFAULT_LOGGER_NAME.
        thread_id (int): Python-level thread id (distinct from vtid). Defaults to
            DEFAULT_PYTHON_THREAD_ID.
        thread_name (str): Python thread name. Defaults to DEFAULT_PYTHON_THREAD_NAME.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_LTTNG_PYTHON_EVENT,
        ts_ns,
        vpid,
        vtid,
        {
            FIELD_MSG: f"{zone_name}:{phase}",
            FIELD_LOGGER_NAME: logger_name,
            FIELD_THREAD: thread_id,
            FIELD_THREAD_NAME: thread_name,
        },
    )


def python_annotation_event(
    ts_ns: int,
    vpid: int,
    vtid: int,
    event_name: str,
    link_id: int,
    message: int,
    logger_name: str = DEFAULT_LOGGER_NAME,
    thread_id: int = DEFAULT_PYTHON_THREAD_ID,
    thread_name: str = DEFAULT_PYTHON_THREAD_NAME,
) -> Event:
    """
    Build an `lttng_python:event` structured annotation event (a Python indirect
    relay's message_link_take/message_link_publish, see
    indirect_relay_node.py's lttng_trace_annotation() calls).

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        event_name (str): Logical event name, e.g. "ros2:message_link_publish".
        link_id (int): The causal-link id shared between the take and its
            eventual republish.
        message (int): Message identity token (id() of the Python message
            object) -- distinct from the C-layer message pointer that the
            following/preceding rcl_publish/rmw_take event carries.
        logger_name (str): Logger name. Defaults to DEFAULT_LOGGER_NAME.
        thread_id (int): Python-level thread id (distinct from vtid). Defaults to
            DEFAULT_PYTHON_THREAD_ID.
        thread_name (str): Python thread name. Defaults to DEFAULT_PYTHON_THREAD_NAME.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_LTTNG_PYTHON_EVENT,
        ts_ns,
        vpid,
        vtid,
        {
            "msg": f"{event_name}|link_id={link_id}|message={message}",
            "logger_name": logger_name,
            "thread": thread_id,
            "threadName": thread_name,
        },
    )


def callback_start_event(
    ts_ns: int,
    vpid: int,
    vtid: int,
    callback: int,
    is_intra_process: bool = False,
) -> Event:
    """
    Build a `ros2:callback_start` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        callback (int): Callback object pointer.
        is_intra_process (bool): Whether this invocation is intra-process.
            Defaults to False.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_CALLBACK_START,
        ts_ns,
        vpid,
        vtid,
        {FIELD_CALLBACK: callback, FIELD_IS_INTRA_PROCESS: is_intra_process},
    )


def callback_end_event(ts_ns: int, vpid: int, vtid: int, callback: int) -> Event:
    """
    Build a `ros2:callback_end` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        callback (int): Callback object pointer.

    Returns:
        Event: The constructed event.
    """
    return make_event(EVT_CALLBACK_END, ts_ns, vpid, vtid, {FIELD_CALLBACK: callback})


def service_init_event(
    ts_ns: int,
    vpid: int,
    vtid: int,
    service_handle: int,
    node_handle: int,
    rmw_service_handle: int,
    service_name: str,
) -> Event:
    """
    Build a `ros2:rcl_service_init` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        service_handle (int): Service handle.
        node_handle (int): Owning node handle.
        rmw_service_handle (int): rmw-layer service handle.
        service_name (str): Service name.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_RCL_SERVICE_INIT,
        ts_ns,
        vpid,
        vtid,
        {
            FIELD_SERVICE_HANDLE: service_handle,
            FIELD_NODE_HANDLE: node_handle,
            FIELD_RMW_SERVICE_HANDLE: rmw_service_handle,
            FIELD_SERVICE_NAME: service_name,
        },
    )


def client_init_event(
    ts_ns: int,
    vpid: int,
    vtid: int,
    client_handle: int,
    node_handle: int,
    rmw_client_handle: int,
    service_name: str,
) -> Event:
    """
    Build a `ros2:rcl_client_init` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        client_handle (int): Client handle.
        node_handle (int): Owning node handle.
        rmw_client_handle (int): rmw-layer client handle.
        service_name (str): Name of the service this client targets.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_RCL_CLIENT_INIT,
        ts_ns,
        vpid,
        vtid,
        {
            FIELD_CLIENT_HANDLE: client_handle,
            FIELD_NODE_HANDLE: node_handle,
            FIELD_RMW_CLIENT_HANDLE: rmw_client_handle,
            FIELD_SERVICE_NAME: service_name,
        },
    )


def rcl_init_event(
    ts_ns: int, vpid: int, vtid: int, context_handle: int, version: str
) -> Event:
    """
    Build a `ros2:rcl_init` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        context_handle (int): Context handle.
        version (str): rcl version string.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_RCL_INIT,
        ts_ns,
        vpid,
        vtid,
        {FIELD_CONTEXT_HANDLE: context_handle, FIELD_VERSION: version},
    )


def lifecycle_state_machine_init_event(
    ts_ns: int, vpid: int, vtid: int, state_machine: int, node_handle: int
) -> Event:
    """
    Build a `ros2:rcl_lifecycle_state_machine_init` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        state_machine (int): State machine handle.
        node_handle (int): Owning node handle.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_RCL_LIFECYCLE_STATE_MACHINE_INIT,
        ts_ns,
        vpid,
        vtid,
        {FIELD_STATE_MACHINE: state_machine, FIELD_NODE_HANDLE: node_handle},
    )


def lifecycle_transition_event(
    ts_ns: int,
    vpid: int,
    vtid: int,
    state_machine: int,
    start_label: str,
    goal_label: str,
) -> Event:
    """
    Build a `ros2:rcl_lifecycle_transition` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        state_machine (int): State machine handle.
        start_label (str): Label of the state being transitioned from.
        goal_label (str): Label of the state being transitioned to.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_RCL_LIFECYCLE_TRANSITION,
        ts_ns,
        vpid,
        vtid,
        {
            FIELD_STATE_MACHINE: state_machine,
            FIELD_START_LABEL: start_label,
            FIELD_GOAL_LABEL: goal_label,
        },
    )


def rmw_publisher_init_event(
    ts_ns: int, vpid: int, vtid: int, rmw_publisher_handle: int, gid: List[int]
) -> Event:
    """
    Build a `ros2:rmw_publisher_init` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        rmw_publisher_handle (int): rmw-layer publisher handle.
        gid (List[int]): DDS GID bytes.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_RMW_PUBLISHER_INIT,
        ts_ns,
        vpid,
        vtid,
        {FIELD_RMW_PUBLISHER_HANDLE: rmw_publisher_handle, FIELD_GID: gid},
    )


def rmw_subscription_init_event(
    ts_ns: int, vpid: int, vtid: int, rmw_subscription_handle: int, gid: List[int]
) -> Event:
    """
    Build a `ros2:rmw_subscription_init` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        rmw_subscription_handle (int): rmw-layer subscription handle.
        gid (List[int]): DDS GID bytes.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_RMW_SUBSCRIPTION_INIT,
        ts_ns,
        vpid,
        vtid,
        {FIELD_RMW_SUBSCRIPTION_HANDLE: rmw_subscription_handle, FIELD_GID: gid},
    )


def timer_init_event(
    ts_ns: int, vpid: int, vtid: int, timer_handle: int, period_ns: int
) -> Event:
    """
    Build a `ros2:rcl_timer_init` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        timer_handle (int): Timer handle.
        period_ns (int): Configured period in nanoseconds.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_RCL_TIMER_INIT,
        ts_ns,
        vpid,
        vtid,
        {FIELD_TIMER_HANDLE: timer_handle, FIELD_PERIOD: period_ns},
    )


def timer_link_node_event(
    ts_ns: int, vpid: int, vtid: int, timer_handle: int, node_handle: int
) -> Event:
    """
    Build a `ros2:rclcpp_timer_link_node` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        timer_handle (int): Timer handle.
        node_handle (int): Owning node handle.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_RCLCPP_TIMER_LINK_NODE,
        ts_ns,
        vpid,
        vtid,
        {FIELD_TIMER_HANDLE: timer_handle, FIELD_NODE_HANDLE: node_handle},
    )


def timer_callback_added_event(
    ts_ns: int, vpid: int, vtid: int, timer_handle: int, callback: int
) -> Event:
    """
    Build a `ros2:rclcpp_timer_callback_added` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        timer_handle (int): Timer handle.
        callback (int): Callback object pointer.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_RCLCPP_TIMER_CALLBACK_ADDED,
        ts_ns,
        vpid,
        vtid,
        {FIELD_TIMER_HANDLE: timer_handle, FIELD_CALLBACK: callback},
    )


def rclcpp_subscription_init_event(
    ts_ns: int, vpid: int, vtid: int, subscription: int, subscription_handle: int
) -> Event:
    """
    Build a `ros2:rclcpp_subscription_init` event, which maps the rclcpp-layer
    subscription pointer to its rcl handle.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        subscription (int): rclcpp-layer subscription pointer.
        subscription_handle (int): rcl subscription handle.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_RCLCPP_SUBSCRIPTION_INIT,
        ts_ns,
        vpid,
        vtid,
        {
            FIELD_SUBSCRIPTION: subscription,
            FIELD_SUBSCRIPTION_HANDLE: subscription_handle,
        },
    )


def subscription_callback_added_event(
    ts_ns: int, vpid: int, vtid: int, subscription: int, callback: int
) -> Event:
    """
    Build a `ros2:rclcpp_subscription_callback_added` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        subscription (int): rclcpp-layer subscription pointer.
        callback (int): Callback object pointer.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_RCLCPP_SUBSCRIPTION_CALLBACK_ADDED,
        ts_ns,
        vpid,
        vtid,
        {FIELD_SUBSCRIPTION: subscription, FIELD_CALLBACK: callback},
    )


def callback_register_event(
    ts_ns: int, vpid: int, vtid: int, callback: int, symbol: str
) -> Event:
    """
    Build a `ros2:rclcpp_callback_register` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        callback (int): Callback object pointer.
        symbol (str): Demangled function symbol.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_RCLCPP_CALLBACK_REGISTER,
        ts_ns,
        vpid,
        vtid,
        {FIELD_CALLBACK: callback, FIELD_SYMBOL: symbol},
    )


def rcl_take_event(ts_ns: int, vpid: int, vtid: int, message: int) -> Event:
    """
    Build a `ros2:rcl_take` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        message (int): Message pointer, joining the take chain.

    Returns:
        Event: The constructed event.
    """
    return make_event(EVT_RCL_TAKE, ts_ns, vpid, vtid, {FIELD_MESSAGE: message})


def rclcpp_take_event(ts_ns: int, vpid: int, vtid: int, message: int) -> Event:
    """
    Build a `ros2:rclcpp_take` event.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        message (int): Message pointer, joining the take chain.

    Returns:
        Event: The constructed event.
    """
    return make_event(EVT_RCLCPP_TAKE, ts_ns, vpid, vtid, {FIELD_MESSAGE: message})


def zone_declaration_event(
    ts_ns: int,
    vpid: int,
    vtid: int,
    payload: str,
    logger_name: str = DEFAULT_LOGGER_NAME,
    thread_id: int = DEFAULT_PYTHON_THREAD_ID,
    thread_name: str = DEFAULT_PYTHON_THREAD_NAME,
) -> Event:
    """
    Build an `lttng_python:event` carrying a raw declaration payload.

    Takes the payload verbatim rather than assembling it from parts, so a test can feed
    a malformed declaration through the same path a real trace would.

    Args:
        ts_ns (int): Timestamp in nanoseconds.
        vpid (int): Virtual PID.
        vtid (int): Virtual TID.
        payload (str): The declaration payload, e.g. "trace_decl timer zone=_tick".
        logger_name (str): Logger name. Defaults to DEFAULT_LOGGER_NAME.
        thread_id (int): Python-level thread id. Defaults to DEFAULT_PYTHON_THREAD_ID.
        thread_name (str): Python thread name. Defaults to DEFAULT_PYTHON_THREAD_NAME.

    Returns:
        Event: The constructed event.
    """
    return make_event(
        EVT_LTTNG_PYTHON_EVENT,
        ts_ns,
        vpid,
        vtid,
        {
            FIELD_MSG: payload,
            FIELD_LOGGER_NAME: logger_name,
            FIELD_THREAD: thread_id,
            FIELD_THREAD_NAME: thread_name,
        },
    )
