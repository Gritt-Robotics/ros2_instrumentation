# Copyright 2026 Gritt Robotics Inc.

"""
python_zones.py
================

Python zone analysis (lttng_python:event).
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from trace_instrumentation.trace_analysis.graph import (
    CB_KIND_SERVICE,
    CB_KIND_SUBSCRIPTION,
    CB_KIND_TIMER,
    LinkDeclaration,
    ZoneDeclaration,
)

# Declaration grammar. parse_python_zone_msg splits on the last colon and requires exactly two parts
DECL_MSG_PREFIX = "trace_decl"
DECL_FIELD_SEPARATOR = "="
DECL_FIELD_ZONE = "zone"
DECL_FIELD_TOPIC = "topic"
DECL_FIELD_NAME = "name"
DECL_FIELD_PERIOD_NS = "period_ns"

DECL_KIND_SUB = "sub"
DECL_KIND_TIMER = "timer"
DECL_KIND_SERVICE = "service"

DECL_KIND_TO_CB_KIND = {
    DECL_KIND_SUB: CB_KIND_SUBSCRIPTION,
    DECL_KIND_TIMER: CB_KIND_TIMER,
    DECL_KIND_SERVICE: CB_KIND_SERVICE,
}

# "trace_decl <kind> <field>=<value> ...": prefix plus kind, before any field.
_MIN_DECL_TOKENS = 2
_DECL_KIND_TOKEN_INDEX = 1

PYTHON_ZONE_PHASE_BEGIN = "begin"
PYTHON_ZONE_PHASE_END = "end"

# link declaration grammar. Same no-colon rule as the zone declarations above.
LINK_MSG_PREFIX = "trace_link"
LINK_KIND_PUB = "pub"
LINK_KIND_TAKE = "take"
LINK_FIELD_UID = "uid"
LINK_FIELD_UIDS = "uids"
LINK_FIELD_TRUNCATED = "truncated"
LINK_UID_SEPARATOR = ","
LINK_TRUNCATED_MARKER = "1"
_LINK_KINDS = frozenset({LINK_KIND_PUB, LINK_KIND_TAKE})
# The emitter's counter starts at 1, so 0 identifies a corrupt payload.
_MIN_VALID_UID = 1
# a link publish names exactly one uid; a link take may name several
_PUB_UID_COUNT = 1


@dataclass(slots=True)
class PythonZone:
    """One Python instrumented zone (begin/end pair from lttng_python:event)."""

    id: int  # Unique ID for this zone, used to match begin/end events.
    logger_name: str  # Logger name, e.g. "rclpy" or "rclpy.timer".
    zone_name: str  # Zone name, e.g. "_tick" or "_on_message".
    vpid: Optional[int]  # Virtual process ID, or None if not available.
    vtid: Optional[int]  # Virtual thread ID, or None if not available.
    ts_begin_ns: int  # Timestamp of the begin event.
    ts_end_ns: Optional[int] = None  # End timestamp, None until the zone closes.
    duration_ns: Optional[int] = None  # Zone duration, None until the zone closes.

    def finalize(self) -> None:
        """Compute duration after both begin/end are populated."""
        if self.ts_end_ns is not None:
            self.duration_ns = self.ts_end_ns - self.ts_begin_ns


def parse_python_zone_msg(msg: str) -> Optional[Tuple[str, str]]:
    """
    Parse an `lttng_python:event` message field into its zone name and phase.

    Args:
        msg (str): The `msg` field, formatted as "<zone_name>:begin" or
            "<zone_name>:end" (e.g. "_tick:begin", "_on_message:end").

    Returns:
        Optional[Tuple[str, str]]: (zone_name, phase), or None if `msg` doesn't
        contain the expected ":" separator or the phase isn't "begin"/"end".
    """
    parts = msg.rsplit(":", 1)
    if len(parts) != 2:
        return None
    zone_name, phase = parts
    if not zone_name:
        return None
    if phase not in (PYTHON_ZONE_PHASE_BEGIN, PYTHON_ZONE_PHASE_END):
        return None
    return zone_name, phase


def parse_zone_declaration_msg(msg: str) -> Optional[ZoneDeclaration]:
    """
    Parse a zone-declaration payload into the kind and target it declares.

    Args:
        msg (str): The `msg` field of an `lttng_python:event` record.

    Returns:
        Optional[ZoneDeclaration]: The declaration, or None when `msg` is not a
        well-formed declaration (including every ordinary zone begin/end payload).
    """
    tokens = msg.split()
    if len(tokens) < _MIN_DECL_TOKENS or tokens[0] != DECL_MSG_PREFIX:
        return None
    kind = DECL_KIND_TO_CB_KIND.get(tokens[_DECL_KIND_TOKEN_INDEX])
    if kind is None:
        return None

    fields = {}
    for token in tokens[_DECL_KIND_TOKEN_INDEX + 1 :]:
        key, separator, value = token.partition(DECL_FIELD_SEPARATOR)
        if separator == "":
            return None
        fields[key] = value

    zone_name = fields.get(DECL_FIELD_ZONE, "")
    if len(zone_name) == 0:
        return None

    period_raw = fields.get(DECL_FIELD_PERIOD_NS)
    period_ns = None
    if period_raw is not None:
        if period_raw.isdigit() is False:
            return None
        period_ns = int(period_raw)

    # check top-level fields for a target, falling back to the "name" field if "topic" is not present
    # The "name" field is used for timers and services, while the "topic" field is used for subscriptions.
    target = fields.get(DECL_FIELD_TOPIC, fields.get(DECL_FIELD_NAME, ""))
    return ZoneDeclaration(
        zone_name=zone_name, kind=kind, target=target, period_ns=period_ns
    )


def _build_decl_msg(kind: str, zone_name: str, **fields: str) -> str:
    """
    Build a "trace_decl <kind> zone=<name> <field>=<value> ..." payload -- the
    single format `parse_zone_declaration_msg` parses back.

    Args:
        kind (str): One of DECL_KIND_SUB/DECL_KIND_TIMER/DECL_KIND_SERVICE.
        zone_name (str): The instrumented method's name (its zone name).

    Returns:
        str: The declaration payload.
    """
    tokens = [
        DECL_MSG_PREFIX,
        kind,
        f"{DECL_FIELD_ZONE}{DECL_FIELD_SEPARATOR}{zone_name}",
    ]
    tokens += [f"{key}{DECL_FIELD_SEPARATOR}{value}" for key, value in fields.items()]
    return " ".join(tokens)


def build_sub_zone_declaration_msg(zone_name: str, topic: str) -> str:
    """
    Build the declaration payload that marks an instrumented Python zone as a
    topic's subscription callback.

    Args:
        zone_name (str): The subscription callback method's name (its zone
            name -- `@lttng_trace_methods` preserves it via `functools.wraps`).
        topic (str): The topic this callback subscribes to.

    Returns:
        str: The "trace_decl sub zone=<name> topic=<topic>" payload.
    """
    return _build_decl_msg(DECL_KIND_SUB, zone_name, **{DECL_FIELD_TOPIC: topic})


def build_timer_zone_declaration_msg(zone_name: str, period_ns: int) -> str:
    """
    Build the declaration payload that marks an instrumented Python zone as a
    timer callback, fired automatically by the wrapped `create_timer`.

    Args:
        zone_name (str): The timer callback method's name (its zone name).
        period_ns (int): The timer's configured period, in nanoseconds.

    Returns:
        str: The "trace_decl timer zone=<name> period_ns=<ns>" payload.
    """
    return _build_decl_msg(
        DECL_KIND_TIMER, zone_name, **{DECL_FIELD_PERIOD_NS: str(period_ns)}
    )


def build_service_zone_declaration_msg(zone_name: str, service_name: str) -> str:
    """
    Build the declaration payload that marks an instrumented Python zone as a
    service callback, fired automatically by the wrapped `create_service`.

    Args:
        zone_name (str): The service callback method's name (its zone name).
        service_name (str): The service's name.

    Returns:
        str: The "trace_decl service zone=<name> name=<service_name>" payload.
    """
    return _build_decl_msg(
        DECL_KIND_SERVICE, zone_name, **{DECL_FIELD_NAME: service_name}
    )


def parse_link_declaration_msg(msg: str) -> Optional[LinkDeclaration]:
    """
    Parse a link pub/take payload.

    Never raises on trace content: an unreadable payload is ignored, since the only ways
    to reach one are a truncated trace or grammar skew with the recording runtime.

    Args:
        msg (str): The `msg` field of an `lttng_python:event` record.

    Returns:
        Optional[LinkDeclaration]: The declaration, or None when `msg` is not a
        well-formed link declaration.
    """
    tokens = msg.split()
    if len(tokens) < _MIN_DECL_TOKENS or tokens[0] != LINK_MSG_PREFIX:
        return None
    kind = tokens[_DECL_KIND_TOKEN_INDEX]
    if kind not in _LINK_KINDS:
        return None

    fields = {}
    for token in tokens[_DECL_KIND_TOKEN_INDEX + 1 :]:
        key, separator, value = token.partition(DECL_FIELD_SEPARATOR)
        if separator == "":
            return None
        fields[key] = value

    raw = fields.get(LINK_FIELD_UID if kind == LINK_KIND_PUB else LINK_FIELD_UIDS, "")
    parts = [part for part in raw.split(LINK_UID_SEPARATOR) if len(part) > 0]
    if len(parts) == 0:
        return None
    uids = []
    for part in parts:
        if not part.isdigit():
            return None
        uid = int(part)
        if uid < _MIN_VALID_UID:
            return None
        uids.append(uid)
    if kind == LINK_KIND_PUB and len(uids) != _PUB_UID_COUNT:
        return None

    return LinkDeclaration(
        kind=kind,
        uids=uids,
        truncated=fields.get(LINK_FIELD_TRUNCATED) == LINK_TRUNCATED_MARKER,
    )


def build_link_publish_msg(uid: int) -> str:
    """
    Build the payload that publishes one uid into the link graph.

    Args:
        uid (int): Process-local identifier this link publish declares.

    Returns:
        str: The "trace_link pub uid=<uid>" payload `parse_link_declaration_msg`
            parses back.

    Raises:
        ValueError: If `uid` is below `_MIN_VALID_UID`, since
            `parse_link_declaration_msg` always rejects such a payload.
    """
    if uid < _MIN_VALID_UID:
        raise ValueError(f"uid must be >= {_MIN_VALID_UID}, got {uid}")
    return (
        f"{LINK_MSG_PREFIX} {LINK_KIND_PUB} {LINK_FIELD_UID}{DECL_FIELD_SEPARATOR}{uid}"
    )


def build_link_take_msg(uids: List[int]) -> str:
    """
    Build the payload that consumes one or more uids from the link graph.

    Args:
        uids (List[int]): The uids this link take names, in declaration order.

    Returns:
        str: The "trace_link take uids=<uid1>,<uid2>,..." payload
            `parse_link_declaration_msg` parses back.

    Raises:
        ValueError: If `uids` is empty or contains a uid below
            `_MIN_VALID_UID`, since `parse_link_declaration_msg` always
            rejects such a payload.
    """
    if len(uids) == 0:
        raise ValueError("uids must be non-empty")
    if any(uid < _MIN_VALID_UID for uid in uids):
        raise ValueError(f"all uids must be >= {_MIN_VALID_UID}, got {uids}")
    joined = LINK_UID_SEPARATOR.join(str(uid) for uid in uids)
    return (
        f"{LINK_MSG_PREFIX} {LINK_KIND_TAKE} "
        f"{LINK_FIELD_UIDS}{DECL_FIELD_SEPARATOR}{joined}"
    )
