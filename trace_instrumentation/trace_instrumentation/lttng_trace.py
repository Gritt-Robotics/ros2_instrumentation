# Copyright 2026 Gritt Robotics Inc.

"""LTTng Python tracing via the real python3-lttngust agent.

The python3-lttngust 2.13 agent ignores the enabled event's
name/loglevel and simply attaches one logging.Handler to the *root* logger when any
Python event rule is enabled. Per-logger filtering is therefore not enforced lttng-side:
analysis must distinguish sources after the fact via the `logger_name` field on resulting
`lttng_python:event` trace records, by filtering for `logger_name` values
`mock_trace_py.publisher`/`mock_trace_py.subscriber`.
"""

import functools
import itertools
import logging
import threading
from contextlib import contextmanager
from typing import Any, Callable, Dict, Generator, List, Optional, Set

# Importing this activates the python3-lttngust agent for this process; nothing here
# calls it by name, so the import itself is the point.
import lttngust  # noqa: F401

from trace_instrumentation.trace_analysis.metrics.stats import (
    NANOSECONDS_PER_SECOND,
)
from trace_instrumentation.trace_analysis.python_zones import (
    build_link_publish_msg,
    build_link_take_msg,
    build_service_zone_declaration_msg,
    build_sub_zone_declaration_msg,
    build_timer_zone_declaration_msg,
)

_CONFIGURED_LOGGER_NAMES: Set[str] = set()

_REGISTRATION_METHOD_NAMES = frozenset({
    "create_subscription",
    "create_timer",
    "create_service",
})

# A marker is armed exactly while it holds an entry in this per-node map.
_MARKER_STATE_ATTRIBUTE = "_trace_link_markers"
# The link grammar rejects uid 0, so the counter starts at 1.
_FIRST_UID = 1
_UID_COUNTER = itertools.count(_FIRST_UID)
# uids are unique only within a process, so allocation and the marker state it
# feeds are guarded together -- nodes may run on a multi-threaded executor.
_LINK_STATE_LOCK = threading.Lock()


def _drop_trace_records(record: logging.LogRecord) -> bool:
    """
    Reject records fired as tracepoints, keeping every other record.

    Args:
        record (logging.LogRecord): Record a handler is about to emit.

    Returns:
        bool: False for a record from a trace logger, True otherwise.
    """
    return record.name not in _CONFIGURED_LOGGER_NAMES


def _keep_trace_records_off_console_handlers() -> None:
    """
    Stop tracepoint records from being printed by a console handler on the root logger.

    These records exist to fire lttng_python:event, not to be read, but they have to
    propagate to root for the lttngust agent's handler to see them. A dependency that
    calls logging.basicConfig() leaves a StreamHandler on root as well, which then prints
    every tracepoint. Filtering only the console handlers leaves the trace intact, since
    the agent's own handler derives from logging.Handler rather than StreamHandler, and
    logging.FileHandler is excluded since it subclasses StreamHandler too.
    """
    for handler in logging.getLogger().handlers:
        if not isinstance(handler, logging.StreamHandler):
            continue
        if isinstance(handler, logging.FileHandler):
            continue
        if _drop_trace_records not in handler.filters:
            handler.addFilter(_drop_trace_records)


def _get_logger(logger_name: str) -> logging.Logger:
    """
    Get (and lazily configure) the logger used to fire lttng_python:event tracepoints.

    Configuration is only done once per logger name. `propagate` is asserted rather than
    assumed: the python3-lttngust agent attaches its forwarding handler only to the *root*
    logger, so a logger that does not propagate silently produces no tracepoints at all
    (confirmed live: with propagate=False, zero tracepoints were captured despite the
    Python domain being fully enabled). The default cannot be trusted, because importing
    `launch` installs a logger class that creates every subsequent logger with propagation
    already off.

    Args:
        logger_name (str): Logger name identifying the source.

    Returns:
        logging.Logger: The configured logger.
    """
    logger = logging.getLogger(logger_name)
    if logger_name not in _CONFIGURED_LOGGER_NAMES:
        logger.setLevel(logging.DEBUG)
        logger.propagate = True
        _CONFIGURED_LOGGER_NAMES.add(logger_name)
        _keep_trace_records_off_console_handlers()
    return logger


def _fire(logger_name: str, msg: str) -> None:
    """
    Fire a single lttng_python:event UST tracepoint.

    Args:
        logger_name (str): Logger name identifying the source.
        msg (str): Message string (e.g. "tick:begin").
    """
    _get_logger(logger_name).debug(msg)


def lttng_trace_annotation(
    logger_name: str, event_name: str, link_id: int, message: int
) -> None:
    """Emit a structured annotation record via lttng_python:event.

    Args:
        logger_name (str): Logger name identifying the source.
        event_name (str): Logical event name (e.g. "ros2:message_link_take").
        link_id (int): Correlation id linking take->publish across callbacks.
        message (int): Message identity token (usually a pointer/id integer).
    """
    _fire(logger_name, f"{event_name}|link_id={link_id}|message={message}")


def _emit_link_pub(logger_name: str, uid: int) -> None:
    """Fire a link-graph publish: hands `uid` off for a later, different
    callback's link take to pick up.

    Args:
        logger_name (str): Logger name identifying the source.
        uid (int): Process-local identifier this handoff declares.
    """
    _fire(logger_name, build_link_publish_msg(uid))


def _emit_link_take(logger_name: str, uids: List[int]) -> None:
    """Fire a link-graph take: consumes uids declared by earlier link publishes,
    from the callback that republishes what they led to.

    Args:
        logger_name (str): Logger name identifying the source.
        uids (List[int]): The uids this take names, in declaration order.
    """
    _fire(logger_name, build_link_take_msg(uids))


def _marker_state(node: Any) -> Dict[str, int]:
    """
    Get (and lazily create) one node's marker -> pending uid map.

    Args:
        node (Any): The node instance owning the markers.

    Returns:
        Dict[str, int]: The node's live marker state.
    """
    state = getattr(node, _MARKER_STATE_ATTRIBUTE, None)
    if state is None:
        state = {}
        setattr(node, _MARKER_STATE_ATTRIBUTE, state)
    return state


def linked_pub(node: Any, logger_name: str, marker: str) -> None:
    """
    Arm `marker` and declare the uid it carries into the link graph.

    A marker carries one uid at a time, so arming is idempotent until a
    `linked_take` consumes it: the first publish after each take is the one a
    chain follows, and the uid can never be declared twice.

    Args:
        node (Any): The node instance the marker belongs to. Marker state is
            per-instance, so two instances of one class never share arming.
        logger_name (str): Logger name identifying the source.
        marker (str): Marker constant naming this handoff.
    """
    with _LINK_STATE_LOCK:
        state = _marker_state(node)
        if marker in state:
            return
        uid = next(_UID_COUNTER)
        state[marker] = uid
        _emit_link_pub(logger_name, uid)


def linked_take(node: Any, logger_name: str, markers: List[str]) -> None:
    """
    Consume the uids `markers` currently carry and disarm them.

    A marker that is not armed is skipped rather than guessed at, so a take that
    finds nothing armed declares nothing at all.

    Args:
        node (Any): The node instance the markers belong to.
        logger_name (str): Logger name identifying the source.
        markers (List[str]): Marker constants to consume, in the order their
            uids should be named.
    """
    with _LINK_STATE_LOCK:
        state = _marker_state(node)
        uids = []
        for marker in markers:
            uid = state.pop(marker, None)
            if uid is not None:
                uids.append(uid)
        if len(uids) == 0:
            return
        _emit_link_take(logger_name, uids)


@contextmanager
def lttng_zone(logger_name: str, zone_name: str) -> Generator[None, None, None]:
    """
    Context manager that fires lttng_python:event begin/end tracepoints.

    Args:
        logger_name (str): Logger name identifying the source
            (e.g. "mock_trace_py.publisher").
        zone_name (str): Zone name to record (e.g. "tick").
    """
    _fire(logger_name, f"{zone_name}:begin")
    try:
        yield
    finally:
        _fire(logger_name, f"{zone_name}:end")


def _callback_zone_name(callback: Callable) -> Optional[str]:
    """
    Derive the zone name to declare for a registered rclpy callback.

    rclpy callbacks may legally be `functools.partial(...)`, which has no
    `__name__`; the wrapped function's name is used instead in that case.

    Args:
        callback (Callable): The callback passed to create_subscription/
            create_timer/create_service.

    Returns:
        Optional[str]: The zone name, or None if no usable name can be derived.
    """
    if isinstance(callback, functools.partial):
        return getattr(callback.func, "__name__", None)
    return getattr(callback, "__name__", None)


def lttng_trace_methods(logger_name: str) -> Callable:
    """
    Class decorator that wraps every non-dunder instance method with lttng_zone,
    and additionally wraps `create_subscription`/`create_timer`/`create_service`
    (inherited from `rclpy.node.Node`) to auto-declare the callback's zone the
    moment the node registers it.

    Args:
        logger_name (str): Logger name used for all tracepoints on this class
            (e.g. "mock_trace_py.publisher").

    Returns:
        Callable: A class decorator.

    Example:
        @lttng_trace_methods("mock_trace_py.publisher")
        class MockTracePublisherNode(Node):
            def _tick(self): ...  # automatically traced with zone name "_tick"
    """

    def _method_wrapper(func):
        zone_name = func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with lttng_zone(logger_name, zone_name):
                return func(*args, **kwargs)

        return wrapper

    def _wrap_create_subscription(create_subscription):
        @functools.wraps(create_subscription)
        def wrapper(self, msg_type, topic, callback, *args, **kwargs):
            subscription = create_subscription(
                self, msg_type, topic, callback, *args, **kwargs
            )
            zone_name = _callback_zone_name(callback)
            if zone_name is not None:
                _fire(logger_name, build_sub_zone_declaration_msg(zone_name, topic))
            return subscription

        return wrapper

    def _wrap_create_timer(create_timer):
        @functools.wraps(create_timer)
        def wrapper(self, timer_period_sec, callback, *args, **kwargs):
            timer = create_timer(self, timer_period_sec, callback, *args, **kwargs)
            zone_name = _callback_zone_name(callback)
            if zone_name is not None:
                period_ns = round(timer_period_sec * NANOSECONDS_PER_SECOND)
                _fire(
                    logger_name,
                    build_timer_zone_declaration_msg(zone_name, period_ns),
                )
            return timer

        return wrapper

    def _wrap_create_service(create_service):
        @functools.wraps(create_service)
        def wrapper(self, srv_type, srv_name, callback, *args, **kwargs):
            service = create_service(
                self, srv_type, srv_name, callback, *args, **kwargs
            )
            zone_name = _callback_zone_name(callback)
            if zone_name is not None:
                _fire(
                    logger_name,
                    build_service_zone_declaration_msg(zone_name, srv_name),
                )
            return service

        return wrapper

    def class_decorator(cls):
        for attr_name, attr_value in cls.__dict__.items():
            if attr_name.startswith("__") and attr_name.endswith("__"):
                continue
            if attr_name in _REGISTRATION_METHOD_NAMES:
                continue
            if callable(attr_value) and not isinstance(
                attr_value, (staticmethod, classmethod, property)
            ):
                setattr(cls, attr_name, _method_wrapper(attr_value))
        # Guarded by hasattr so this decorator still works on a plain
        # (non-Node) class, e.g. in tests.
        if hasattr(cls, "create_subscription"):
            cls.create_subscription = _wrap_create_subscription(cls.create_subscription)
        if hasattr(cls, "create_timer"):
            cls.create_timer = _wrap_create_timer(cls.create_timer)
        if hasattr(cls, "create_service"):
            cls.create_service = _wrap_create_service(cls.create_service)
        return cls

    return class_decorator
