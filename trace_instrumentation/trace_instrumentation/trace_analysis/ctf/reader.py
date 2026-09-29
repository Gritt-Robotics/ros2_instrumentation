# Copyright 2026 Gritt Robotics Inc.

"""
reader.py
=========

bt2-backed reader for ROS 2 LTTng/CTF traces produced by `ros2 trace` (the
ros2_tracing package).

Uses babeltrace2 (`bt2`) to parse the CTF trace directly, rather than hand-decoding
its binary packet layout and TSDL metadata text. `python3-bt2` is a declared
dependency of `trace_instrumentation`, so there is no portability reason to avoid it.
"""

import os
from typing import Any, Dict, List, NamedTuple, Optional

import bt2

NANOSECONDS_PER_SECOND = 1_000_000_000
UST_DOMAIN_NAME = "ust"
CTF_METADATA_FILENAME = "metadata"
CTF_INDEX_DIRNAME = "index"

# Keys CTF itself supplies alongside each event, from lttng-ust's context config.
COMMON_CONTEXT_KEY_VPID = "vpid"
COMMON_CONTEXT_KEY_VTID = "vtid"
COMMON_CONTEXT_KEY_PROCNAME = "procname"
PACKET_CONTEXT_KEY_CPU = "cpu"

# Event's own attribute names, spelling its __slots__. Kept separate from the CTF
# keys above so renaming a CTF key can't silently rename one of our attributes.
EVENT_SLOT_TS_NS = "ts_ns"
EVENT_SLOT_TS_CYCLES = "ts_cycles"
EVENT_SLOT_NAME = "name"
EVENT_SLOT_VPID = "vpid"
EVENT_SLOT_VTID = "vtid"
EVENT_SLOT_PROCNAME = "procname"
EVENT_SLOT_CPU = "cpu"
EVENT_SLOT_FIELDS = "fields"

# bt2 publishes no public aliases for these, so the private names are a hard
# dependency: there is deliberately no fallback, so a rename fails loudly at import.
_STRING_FIELD_CONST = bt2._StringFieldConst
_ARRAY_FIELD_CONST = bt2._ArrayFieldConst
_EVENT_MESSAGE_CONST = bt2._EventMessageConst


class Metadata:
    """Trace-level metadata: environment key/values and clock parameters used to
    convert raw clock-cycle counts to nanoseconds since the epoch."""

    def __init__(self) -> None:
        """Initialize to default (empty/identity-clock) state."""
        self.env: Dict[str, str] = {}
        self.clock_offset = 0
        self.clock_freq = NANOSECONDS_PER_SECOND

    def cycles_to_ns(self, cycles: int) -> int:
        """
        Convert a raw clock cycle count to nanoseconds since the epoch.

        Args:
            cycles (int): Raw clock value, as read from an event's clock snapshot.

        Returns:
            int: Nanoseconds since the epoch.
        """
        if self.clock_freq == NANOSECONDS_PER_SECOND:
            return self.clock_offset + cycles
        return self.clock_offset + cycles * NANOSECONDS_PER_SECOND // self.clock_freq


class Event:
    """One decoded CTF event: a name, a timestamp, stream context, and its fields."""

    __slots__ = (
        EVENT_SLOT_TS_NS,
        EVENT_SLOT_TS_CYCLES,
        EVENT_SLOT_NAME,
        EVENT_SLOT_VPID,
        EVENT_SLOT_VTID,
        EVENT_SLOT_PROCNAME,
        EVENT_SLOT_CPU,
        EVENT_SLOT_FIELDS,
    )

    def __init__(
        self,
        ts_ns: int,
        ts_cycles: int,
        name: str,
        vpid: Optional[int],
        vtid: Optional[int],
        procname: str,
        cpu: Optional[int],
        fields: Dict[str, Any],
    ) -> None:
        """
        Construct one decoded event.

        Args:
            ts_ns (int): Timestamp in nanoseconds since the epoch.
            ts_cycles (int): Raw clock cycle count this event was recorded at.
            name (str): Event name, e.g. "ros2:rcl_init".
            vpid (Optional[int]): Virtual PID of the process that emitted this event.
            vtid (Optional[int]): Virtual TID of the thread that emitted this event.
            procname (str): Process name (truncated to 16 chars by the kernel/LTTng).
            cpu (Optional[int]): CPU core the event was recorded on.
            fields (Dict[str, Any]): Decoded event-specific field values by name.
        """
        self.ts_ns = ts_ns
        self.ts_cycles = ts_cycles
        self.name = name
        self.vpid = vpid
        self.vtid = vtid
        self.procname = procname
        self.cpu = cpu
        self.fields = fields


def coerce_field(value: Any) -> Any:
    """
    Convert one decoded bt2 field value into a native Python type.

    Args:
        value (Any): A bt2 field-const value (string, integer, or array), as read
            from an event's payload, common context, or packet context.

    Returns:
        Any: The equivalent native `str`, `int`, or `List[int]`. bt2's field-const
            wrapper types support `==`/arithmetic against native types but are not
            `isinstance`-compatible with them, so callers that use decoded values as
            dict keys or in boolean context need real native types, not the wrapper.

    Raises:
        TypeError: If `value` is not a bt2 string, array, or integer field-const.
    """
    if isinstance(value, _STRING_FIELD_CONST):
        return str(value)
    if isinstance(value, _ARRAY_FIELD_CONST):
        return [coerce_field(v) for v in value]
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise TypeError(
            f"Unsupported bt2 field type {type(value)!r} for value {value!r}"
        ) from e


def event_to_ros2_event(msg: Any) -> Event:
    """
    Translate one bt2 event message into an `Event`.

    Args:
        msg (Any): One event message from a `bt2.TraceCollectionMessageIterator`.

    Returns:
        Event: The decoded event.
    """
    ev = msg.event
    clock_snapshot = msg.default_clock_snapshot

    vpid = vtid = None
    procname = ""
    common_context = ev.common_context_field
    if common_context is not None:
        for key in common_context:
            value = coerce_field(common_context[key])
            if key == COMMON_CONTEXT_KEY_VPID:
                vpid = value
            elif key == COMMON_CONTEXT_KEY_VTID:
                vtid = value
            elif key == COMMON_CONTEXT_KEY_PROCNAME:
                procname = value

    cpu = None
    packet_context = ev.packet.context_field
    if packet_context is not None and PACKET_CONTEXT_KEY_CPU in packet_context:
        cpu = coerce_field(packet_context[PACKET_CONTEXT_KEY_CPU])

    fields: Dict[str, Any] = {}
    if ev.payload_field is not None:
        for key in ev.payload_field:
            fields[key] = coerce_field(ev.payload_field[key])

    return Event(
        clock_snapshot.ns_from_origin,
        clock_snapshot.value,
        ev.name,
        vpid,
        vtid,
        procname,
        cpu,
        fields,
    )


def find_ctf_dir(trace_dir: str) -> str:
    """
    Locate the directory that actually contains the `metadata` file.

    Args:
        trace_dir (str): Path to the trace. May be the session dir (containing
            ust/uid/.../metadata) or the directory holding `metadata` directly.

    Returns:
        str: The directory directly containing `metadata`. If multiple candidates
        exist (e.g. both a `ust/` and `kernel/` domain), the UST one is preferred;
        otherwise the lexicographically first candidate is returned, so the choice
        is deterministic regardless of filesystem walk order.

    Raises:
        FileNotFoundError: If no `metadata` file is found under trace_dir.
    """
    if os.path.isfile(os.path.join(trace_dir, CTF_METADATA_FILENAME)):
        return trace_dir
    candidates = sorted(
        root
        for root, _dirs, files in os.walk(trace_dir)
        if CTF_METADATA_FILENAME in files
        and os.path.basename(root) != CTF_INDEX_DIRNAME
    )
    if len(candidates) == 0:
        raise FileNotFoundError(f"No CTF 'metadata' file found under {trace_dir!r}")
    ust_candidates = [
        candidate
        for candidate in candidates
        if UST_DOMAIN_NAME in os.path.normpath(candidate).split(os.sep)
    ]
    return ust_candidates[0] if len(ust_candidates) > 0 else candidates[0]


class TraceData(NamedTuple):
    """Everything one CTF trace directory decodes to."""

    meta: Metadata
    ctf_dir: str
    events: List[Event]


def read_trace(trace_dir: str) -> TraceData:
    """
    Parse a CTF trace directory into a sorted, in-memory event list.

    Args:
        trace_dir (str): Path to the trace. May be the session dir (containing
            ust/uid/.../metadata) or the directory holding `metadata` directly.

    Returns:
        TraceData: The parsed metadata, the resolved CTF directory, and all decoded
        events sorted by clock cycle.
    """
    ctf_dir = find_ctf_dir(trace_dir)
    meta = Metadata()
    trace_seen = False

    events = []
    for msg in bt2.TraceCollectionMessageIterator(ctf_dir):
        if not isinstance(msg, _EVENT_MESSAGE_CONST):
            continue
        if not trace_seen:
            trace_seen = True
            trace = msg.event.stream.trace
            meta.env = {k: str(v) for k, v in trace.environment.items()}
            clock_class = msg.default_clock_snapshot.clock_class
            meta.clock_freq = clock_class.frequency
            meta.clock_offset = (
                clock_class.offset.seconds * NANOSECONDS_PER_SECOND
                + clock_class.offset.cycles
                * NANOSECONDS_PER_SECOND
                // clock_class.frequency
            )
        events.append(event_to_ros2_event(msg))

    events.sort(key=lambda e: e.ts_cycles)
    return TraceData(meta, ctf_dir, events)
