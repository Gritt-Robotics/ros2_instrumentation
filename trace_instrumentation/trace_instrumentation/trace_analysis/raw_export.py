# Copyright 2026 Gritt Robotics Inc.

"""
raw_export.py
=============

Dump of the raw decoded event stream as Chrome Trace Event Format JSON, for ad-hoc
root-cause-analysis tooling that the built-in derived reports don't cover. Readable by
any viewer that consumes that format.
"""

import json
from typing import Any, Dict, Iterable, List, Optional, Set

from trace_instrumentation.trace_analysis.analysis import (
    REGISTRY_KEY_NODE,
    REGISTRY_KEY_RMW,
    TraceAnalysis,
)
from trace_instrumentation.trace_analysis.ctf.reader import Event
from trace_instrumentation.trace_analysis.event_fields import (
    FIELD_CALLBACK,
    FIELD_CLIENT_HANDLE,
    FIELD_NODE_HANDLE,
    FIELD_PUBLISHER_HANDLE,
    FIELD_RMW_PUBLISHER_HANDLE,
    FIELD_RMW_SUBSCRIPTION_HANDLE,
    FIELD_SERVICE_HANDLE,
    FIELD_SUBSCRIPTION,
    FIELD_SUBSCRIPTION_HANDLE,
    FIELD_TIMER_HANDLE,
)
from trace_instrumentation.trace_analysis.event_names import (
    EVT_CALLBACK_END,
    EVT_CALLBACK_START,
    EVT_RCL_NODE_INIT,
)
from trace_instrumentation.trace_analysis.metrics.stats import (
    NANOSECONDS_PER_MICROSECOND,
)

KEY_TRACE_EVENTS = "traceEvents"
KEY_PHASE = "ph"
KEY_NAME = "name"
KEY_PID = "pid"
KEY_TID = "tid"
KEY_TIMESTAMP_US = "ts"
KEY_SCOPE = "s"
KEY_ARGS = "args"

# `args` keys for decoded context the format itself has no field for.
ARG_TS_NS = "ts_ns"
ARG_PROCNAME = "procname"
ARG_CPU = "cpu"


PHASE_BEGIN = "B"
PHASE_END = "E"
PHASE_INSTANT = "i"
INSTANT_EVENT_SCOPE_THREAD = "t"

UNKNOWN_PID_OR_TID = 0

# Every `ts` is relative to the first event.
NO_EVENTS_TRACE_START_NS = 0

# Fields naming an entity whose owning node an event can be attributed through,
# consulted in this order. The first one the event carries decides its node.
ATTRIBUTION_FIELDS = (
    FIELD_NODE_HANDLE,
    FIELD_PUBLISHER_HANDLE,
    FIELD_RMW_PUBLISHER_HANDLE,
    FIELD_SUBSCRIPTION_HANDLE,
    FIELD_RMW_SUBSCRIPTION_HANDLE,
    FIELD_SUBSCRIPTION,
    FIELD_TIMER_HANDLE,
    FIELD_SERVICE_HANDLE,
    FIELD_CLIENT_HANDLE,
    FIELD_CALLBACK,
)


def write_events_trace_json(
    path: str,
    events: List[Event],
    analysis: TraceAnalysis,
    ignore_nodes: Optional[Iterable[str]] = None,
) -> None:
    """
    Dump the decoded events as a Chrome Trace Event Format JSON file.

    Args:
        path (str): Output .json path. Overwritten if it already exists.
        events (List[Event]): Decoded events, sorted by clock cycle.
        analysis (TraceAnalysis): Built analysis, whose registries resolve an event's
                    handle to the node that owns it.
        ignore_nodes (Optional[Iterable[str]]): Node labels to leave out, which is how a
                    large dump is cut to a size a viewer can open.
                    Omitted by default, dumping every event.
    """
    ignored = set(ignore_nodes or [])
    # Taken before any exclusion so every dump of one trace shares a time origin.
    trace_start_ns = _trace_start_ns(events)
    if len(ignored) > 0:
        events = _events_excluding_nodes(events, analysis, ignored)
    trace_events = [_to_trace_event(event, trace_start_ns) for event in events]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({KEY_TRACE_EVENTS: trace_events}, fh)


def _trace_start_ns(events: List[Event]) -> int:
    """
    Find the timestamp every dumped `ts` is measured from.

    Args:
        events (List[Event]): Decoded events, sorted by clock cycle.

    Returns:
        int: The earliest event's timestamp in nanoseconds, or
        `NO_EVENTS_TRACE_START_NS` when there are no events.
    """
    if len(events) == 0:
        return NO_EVENTS_TRACE_START_NS
    return min(event.ts_ns for event in events)


def _events_excluding_nodes(
    events: List[Event], analysis: TraceAnalysis, ignored: Set[str]
) -> List[Event]:
    """
    Drop the events belonging to any of the excluded nodes.

    Args:
        events (List[Event]): Decoded events, sorted by clock cycle.
        analysis (TraceAnalysis): Built analysis, for the handle registries.
        ignored (Set[str]): Node labels to leave out.

    Returns:
        List[Event]: The events to write, in their original order.
    """
    node_by_handle = _node_label_by_handle(analysis)
    ignored_vpids = _fully_ignored_vpids(events, analysis, ignored)
    kept = []
    for event in events:
        label = _event_node_label(event, node_by_handle)
        if label is None:
            # Nothing names this event's node (e.g. a Python zone, or a take known only
            # by its message pointer); its process decides only when no node survives it.
            if event.vpid not in ignored_vpids:
                kept.append(event)
            continue
        if label not in ignored:
            kept.append(event)
    return kept


def _node_label_by_handle(analysis: TraceAnalysis) -> Dict[int, str]:
    """
    Map every handle an event can carry to the label of the node that owns it.

    Both rcl- and rmw-level handles are registered, since an event carries whichever
    layer fired it.

    Args:
        analysis (TraceAnalysis): Built analysis, for the handle registries.

    Returns:
        Dict[int, str]: Handle (or callback/subscription pointer) -> node label.
    """
    by_handle: Dict[int, str] = {
        node_handle: analysis.node_label(node_handle) for node_handle in analysis.nodes
    }
    owning_registries = (
        analysis.publishers,
        analysis.subscriptions,
        analysis.services,
        analysis.clients,
        analysis.timers,
    )
    for registry in owning_registries:
        for handle, info in registry.items():
            node_handle = info.get(REGISTRY_KEY_NODE)
            if node_handle is None:
                continue
            label = analysis.node_label(node_handle)
            by_handle[handle] = label
            rmw_handle = info.get(REGISTRY_KEY_RMW)
            if rmw_handle is not None:
                by_handle[rmw_handle] = label

    # An rclcpp subscription is traced by its own pointer, which only the rcl handle
    # it wraps can resolve to a node.
    for rclcpp_ptr, rcl_handle in analysis.rclcpp_subs.items():
        label = by_handle.get(rcl_handle)
        if label is not None:
            by_handle[rclcpp_ptr] = label

    # callback_start/end name only the callback pointer, so route it through the
    # entity that registered it.
    for callback_ptr, owner in analysis.callback_owner.items():
        _owner_kind, owner_handle = owner
        label = by_handle.get(owner_handle)
        if label is not None:
            by_handle[callback_ptr] = label
    return by_handle


def _fully_ignored_vpids(
    events: List[Event], analysis: TraceAnalysis, ignored: Set[str]
) -> Set[Optional[int]]:
    """
    Find the processes whose every registered node is excluded.

    `TraceAnalysis.vpid_to_node` keeps one node per process, so the node_init events are
    re-read here to see all of a process's nodes -- a process hosting both an excluded
    node and a surviving one must keep its unattributable events.

    Args:
        events (List[Event]): Decoded events, sorted by clock cycle.
        analysis (TraceAnalysis): Built analysis, for node labels.
        ignored (Set[str]): Node labels to leave out.

    Returns:
        Set[Optional[int]]: vpids whose every registered node is excluded.
    """
    labels_by_vpid: Dict[Optional[int], Set[str]] = {}
    for event in events:
        if event.name != EVT_RCL_NODE_INIT:
            continue
        node_handle = event.fields.get(FIELD_NODE_HANDLE)
        if node_handle is None:
            continue
        labels_by_vpid.setdefault(event.vpid, set()).add(
            analysis.node_label(node_handle)
        )
    return {
        vpid
        for vpid, labels in labels_by_vpid.items()
        if len(labels) > 0 and labels <= ignored
    }


def _event_node_label(event: Event, node_by_handle: Dict[int, str]) -> Optional[str]:
    """
    Resolve the node an event belongs to, through the first handle it carries.

    Args:
        event (Event): One decoded event.
        node_by_handle (Dict[int, str]): Handle -> node label, from
                    `_node_label_by_handle()`.

    Returns:
        Optional[str]: The owning node's label, or None when the event carries no
        handle that names one.
    """
    fields = event.fields
    for field in ATTRIBUTION_FIELDS:
        handle = fields.get(field)
        if handle is None:
            continue
        label = node_by_handle.get(handle)
        if label is not None:
            return label
    return None


def _to_trace_event(event: Event, trace_start_ns: int) -> Dict[str, Any]:
    """
    Convert one decoded event into a Chrome Trace Event Format entry.

    Args:
        event (Event): One decoded event.
        trace_start_ns (int): Timestamp `ts` is measured from, from
                    `_trace_start_ns()`.

    Returns:
        Dict[str, Any]: The equivalent Chrome Trace Event Format entry.
    """
    if event.name == EVT_CALLBACK_START:
        phase = PHASE_BEGIN
    elif event.name == EVT_CALLBACK_END:
        phase = PHASE_END
    else:
        phase = PHASE_INSTANT
    trace_event = {
        KEY_PHASE: phase,
        KEY_NAME: event.name,
        KEY_PID: event.vpid if event.vpid is not None else UNKNOWN_PID_OR_TID,
        KEY_TID: event.vtid if event.vtid is not None else UNKNOWN_PID_OR_TID,
        KEY_TIMESTAMP_US: (event.ts_ns - trace_start_ns) / NANOSECONDS_PER_MICROSECOND,
        KEY_ARGS: {
            **event.fields,
            ARG_TS_NS: event.ts_ns,
            ARG_PROCNAME: event.procname,
            ARG_CPU: event.cpu,
        },
    }
    if phase == PHASE_INSTANT:
        trace_event[KEY_SCOPE] = INSTANT_EVENT_SCOPE_THREAD
    return trace_event
