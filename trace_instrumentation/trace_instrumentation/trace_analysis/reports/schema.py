# Copyright 2026 Gritt Robotics Inc.

"""
reports/schema.py
==================

Validates the trace-analysis JSON `build_node_report()` produces today: dict-keyed
`nodes`/`callbacks`/`publishers`, per-node `to`/`from` topology adjacency, and a
`pipeline_chain_latency` list -- not an aspirational, restructured shape. Checks
presence/type of every required field and ignores unknown fields (forward-compat).
Optional fields (`p90`, `values`, `dropped`, `interarrival_ms`,
`inter_callback_gap_ms`, `instantaneous_wait_count`,
`instantaneous_wait_threshold_ms`, `pipeline_chain_latency`) are validated only
if present.

Lives in the same `reports` package as `build_node_report()` -- rather than in a
downstream renderer -- so the two can never drift apart independently.
"""

from typing import Any, Dict, Optional

from trace_instrumentation.trace_analysis.graph import CHAIN_TERMINATORS

KEY_COUNT = "count"
KEY_MIN = "min"
KEY_MEAN = "mean"
KEY_MEDIAN = "median"
KEY_P99 = "p99"
KEY_MAX = "max"
KEY_P90 = "p90"
KEY_VALUES = "values"
KEY_PCT = "pct"

KEY_CALLBACK_TYPE = "callback_type"
KEY_TOPIC = "topic"
KEY_TIMER_PERIOD_NS = "timer_period_ns"
KEY_CALLBACK_HZ = "callback_hz"
KEY_DURATION_MS = "duration_ms"
KEY_TOPIC_LATENCY_MS = "topic_latency_ms"
KEY_TAKE_TO_CALLBACK_START_MS = "take_to_callback_start_ms"
KEY_CALLBACK_TIME_MS = "callback_time_ms"
KEY_IS_INDIRECT = "is_indirect"
KEY_INDIRECT_WAIT_MS = "indirect_wait_ms"
KEY_RELAY_CALLBACK_TIME_MS = "relay_callback_time_ms"
KEY_RELAY_CALLBACK_TOPIC = "relay_callback_topic"
KEY_INTERARRIVAL_MS = "interarrival_ms"
KEY_INTER_CALLBACK_GAP_MS = "inter_callback_gap_ms"

# Not required/validated here (schema doesn't check these), but shared with
# reports/__init__.py and reports/model.py since all three describe the same JSON.
KEY_INSTANTANEOUS_WAIT_COUNT = "instantaneous_wait_count"
KEY_INSTANTANEOUS_WAIT_THRESHOLD_MS = "instantaneous_wait_threshold_ms"

KEY_PUBLISHER_HZ = "publisher_hz"
KEY_TOPIC_TOTAL_HZ = "topic_total_hz"
KEY_CALLBACK_START_TO_PUBLISH_MS = "callback_start_to_publish_ms"
KEY_PUBLISHED_COUNT = "published_count"
KEY_RECEIVED_COUNT = "received_count"
KEY_DROPPED = "dropped"
KEY_BY_CALLBACK = "by_callback"

KEY_TO = "to"
KEY_FROM = "from"
KEY_CALLBACKS = "callbacks"
KEY_PUBLISHERS = "publishers"

KEY_TOPIC_SEQUENCE = "topic_sequence"
KEY_TRAVERSAL_COUNT = "traversal_count"
KEY_STEPS = "steps"

KEY_PUBLISHING_NODE = "publishing_node"
KEY_SUBSCRIBING_NODE = "subscribing_node"
KEY_TOPIC_HZ = "topic_hz"

KEY_TERMINATOR = "terminator"
KEY_CHAIN_LATENCY_MS = "chain_latency_ms"

KEY_STEP_ENDPOINTS = "step_endpoints"
KEY_KIND = "kind"
KEY_NODE = "node"
KEY_METRIC_MS = "metric_ms"

ENDPOINT_KIND_PUBLISH = "publish"
ENDPOINT_KIND_TAKE = "take"
ENDPOINT_KIND_CALLBACK_START = "callback start"
ENDPOINT_KIND_LINK = "link"
ENDPOINT_KIND_CALLBACK_END = "callback end"
ENDPOINT_KINDS = frozenset({
    ENDPOINT_KIND_PUBLISH,
    ENDPOINT_KIND_TAKE,
    ENDPOINT_KIND_CALLBACK_START,
    ENDPOINT_KIND_LINK,
    ENDPOINT_KIND_CALLBACK_END,
})

KEY_DURATION_S = "duration_s"
KEY_NODES = "nodes"
KEY_PIPELINE_CHAIN_LATENCY = "pipeline_chain_latency"
KEY_THROUGHPUT = "throughput"
KEY_TRACE = "trace"

KEY_BUCKET_INTERVAL_S = "bucket_interval_s"
KEY_BUCKET_STARTS_S = "bucket_starts_s"
KEY_TOTAL_HZ = "total_hz"
KEY_PER_TOPIC_HZ = "per_topic_hz"

CALLBACK_TYPE_SUBSCRIPTION = "subscription"

REQUIRED_METRIC_KEYS = (KEY_COUNT, KEY_MIN, KEY_MEAN, KEY_MEDIAN, KEY_P99, KEY_MAX)
NUMERIC_METRIC_KEYS = tuple(key for key in REQUIRED_METRIC_KEYS if key != KEY_COUNT)

# "Required" means present, not non-null: stats() yields null for zero samples, so these
# still go through validate_optional_metric() below.
REQUIRED_CALLBACK_KEYS = (
    KEY_CALLBACK_TYPE,
    KEY_TOPIC,
    KEY_TIMER_PERIOD_NS,
    KEY_CALLBACK_HZ,
    KEY_DURATION_MS,
)
OPTIONAL_CALLBACK_METRIC_KEYS = (KEY_INTERARRIVAL_MS, KEY_INTER_CALLBACK_GAP_MS)

REQUIRED_PUBLISHER_KEYS = (
    KEY_TOPIC,
    KEY_PUBLISHER_HZ,
    KEY_TOPIC_TOTAL_HZ,
    KEY_CALLBACK_START_TO_PUBLISH_MS,
    KEY_PUBLISHED_COUNT,
    KEY_RECEIVED_COUNT,
)

REQUIRED_NODE_KEYS = (KEY_TO, KEY_FROM, KEY_CALLBACKS, KEY_PUBLISHERS)

REQUIRED_CHAIN_KEYS = (
    KEY_TOPIC_SEQUENCE,
    KEY_TRAVERSAL_COUNT,
    KEY_TERMINATOR,
    KEY_STEPS,
    KEY_STEP_ENDPOINTS,
)

REQUIRED_CHAIN_STEP_KEYS = (
    KEY_PUBLISHING_NODE,
    KEY_SUBSCRIBING_NODE,
    KEY_TOPIC,
    KEY_TOPIC_HZ,
    KEY_CALLBACK_HZ,
    KEY_IS_INDIRECT,
)

REQUIRED_ENDPOINT_KEYS = (KEY_TOPIC, KEY_KIND, KEY_NODE)
REQUIRED_STEP_ENDPOINT_ROW_KEYS = (KEY_FROM, KEY_TO, KEY_METRIC_MS)

REQUIRED_THROUGHPUT_KEYS = (
    KEY_BUCKET_INTERVAL_S,
    KEY_BUCKET_STARTS_S,
    KEY_TOTAL_HZ,
    KEY_PER_TOPIC_HZ,
)

REQUIRED_DROPPED_KEYS = (KEY_COUNT, KEY_PCT)


class SchemaError(Exception):
    """Raised with a message naming the exact JSON path of the violation."""


def _require_dict(obj: Any, path: str) -> None:
    if not isinstance(obj, dict):
        raise SchemaError(f"{path} must be an object")


def _require_keys(obj: Dict[str, Any], required: tuple, path: str) -> None:
    missing = [key for key in required if key not in obj]
    if missing:
        raise SchemaError(f"{path} missing {missing}")


def validate_metric(obj: Any, path: str) -> None:
    """
    Validate a Metric-shaped dict: requires count/min/mean/median/p99/max, `count` an
    int and the rest numbers; `p90` is checked the same way if present.

    Args:
        obj (Any): The value to validate.
        path (str): JSON path for error messages.

    Raises:
        SchemaError: If `obj` is malformed.
    """
    _require_dict(obj, path)
    _require_keys(obj, REQUIRED_METRIC_KEYS, path)
    if not isinstance(obj[KEY_COUNT], int):
        raise SchemaError(f"{path}.{KEY_COUNT} must be an int")
    for key in NUMERIC_METRIC_KEYS:
        if not isinstance(obj[key], (int, float)):
            raise SchemaError(f"{path}.{key} must be a number")
    if KEY_P90 in obj and not isinstance(obj[KEY_P90], (int, float)):
        raise SchemaError(f"{path}.{KEY_P90} must be a number")
    if KEY_VALUES in obj:
        if not isinstance(obj[KEY_VALUES], list):
            raise SchemaError(f"{path}.{KEY_VALUES} must be a list")
        for i, value in enumerate(obj[KEY_VALUES]):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SchemaError(f"{path}.{KEY_VALUES}[{i}] must be a number")


def validate_optional_metric(obj: Optional[Any], path: str) -> None:
    """
    Validate a Metric-shaped dict that may legitimately be `None`.

    Args:
        obj (Optional[Any]): The value to validate, or None.
        path (str): JSON path for error messages.
    """
    if obj is not None:
        validate_metric(obj, path)


def validate_dropped(obj: Optional[Any], path: str) -> None:
    """
    Validate a `dropped` dict: requires count/pct, or None.

    Args:
        obj (Optional[Any]): The value to validate, or None.
        path (str): JSON path for error messages.
    """
    if obj is None:
        return
    _require_dict(obj, path)
    _require_keys(obj, REQUIRED_DROPPED_KEYS, path)


def validate_callback(obj: Any, path: str) -> None:
    """
    Validate one entry of a node's `callbacks` dict.

    Args:
        obj (Any): The value to validate.
        path (str): JSON path for error messages.
    """
    _require_dict(obj, path)
    _require_keys(obj, REQUIRED_CALLBACK_KEYS, path)
    validate_optional_metric(obj[KEY_DURATION_MS], f"{path}.{KEY_DURATION_MS}")
    if obj[KEY_CALLBACK_TYPE] == CALLBACK_TYPE_SUBSCRIPTION:
        _require_keys(obj, (KEY_TOPIC_LATENCY_MS,), path)
        validate_optional_metric(
            obj[KEY_TOPIC_LATENCY_MS], f"{path}.{KEY_TOPIC_LATENCY_MS}"
        )
    for key in OPTIONAL_CALLBACK_METRIC_KEYS:
        if key in obj:
            validate_optional_metric(obj[key], f"{path}.{key}")


def validate_publisher(obj: Any, path: str) -> None:
    """
    Validate one entry of a node's `publishers` dict.

    Args:
        obj (Any): The value to validate.
        path (str): JSON path for error messages.

    Raises:
        SchemaError: If `obj` is malformed, a count field is not a non-negative
            integer, or `received_count` exceeds `published_count` (Rcv is
            matched takes, so it can never exceed Pub).
    """
    _require_dict(obj, path)
    _require_keys(obj, REQUIRED_PUBLISHER_KEYS, path)
    validate_optional_metric(
        obj[KEY_CALLBACK_START_TO_PUBLISH_MS],
        f"{path}.{KEY_CALLBACK_START_TO_PUBLISH_MS}",
    )
    for count_key in (KEY_PUBLISHED_COUNT, KEY_RECEIVED_COUNT):
        value = obj[count_key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SchemaError(f"{path}.{count_key} must be a non-negative integer")
    if obj[KEY_RECEIVED_COUNT] > obj[KEY_PUBLISHED_COUNT]:
        raise SchemaError(
            f"{path}.{KEY_RECEIVED_COUNT} must not exceed {KEY_PUBLISHED_COUNT}"
        )
    if KEY_DROPPED in obj:
        validate_dropped(obj[KEY_DROPPED], f"{path}.{KEY_DROPPED}")
    if KEY_BY_CALLBACK in obj:
        _require_dict(obj[KEY_BY_CALLBACK], f"{path}.{KEY_BY_CALLBACK}")
        for label, source in obj[KEY_BY_CALLBACK].items():
            source_path = f"{path}.{KEY_BY_CALLBACK}[{label}]"
            _require_dict(source, source_path)
            for count_key in (KEY_PUBLISHED_COUNT, KEY_RECEIVED_COUNT):
                if not isinstance(source[count_key], int):
                    raise SchemaError(f"{source_path}.{count_key} must be an integer")
            validate_optional_metric(
                source[KEY_CALLBACK_START_TO_PUBLISH_MS],
                f"{source_path}.{KEY_CALLBACK_START_TO_PUBLISH_MS}",
            )


def validate_node(obj: Any, path: str) -> None:
    """
    Validate one entry of the top-level `nodes` dict.

    Args:
        obj (Any): The value to validate.
        path (str): JSON path for error messages.

    Raises:
        SchemaError: If `obj` is malformed.
    """
    _require_dict(obj, path)
    _require_keys(obj, REQUIRED_NODE_KEYS, path)
    if not isinstance(obj[KEY_TO], list) or not isinstance(obj[KEY_FROM], list):
        raise SchemaError(f"{path}.{KEY_TO}/{KEY_FROM} must be lists")
    _require_dict(obj[KEY_CALLBACKS], f"{path}.{KEY_CALLBACKS}")
    for label, callback in obj[KEY_CALLBACKS].items():
        validate_callback(callback, f"{path}.{KEY_CALLBACKS}[{label}]")
    _require_dict(obj[KEY_PUBLISHERS], f"{path}.{KEY_PUBLISHERS}")
    for label, publisher in obj[KEY_PUBLISHERS].items():
        validate_publisher(publisher, f"{path}.{KEY_PUBLISHERS}[{label}]")


def validate_chain_step(obj: Any, path: str) -> None:
    """
    Validate one entry of a chain's `steps` list.

    Args:
        obj (Any): The value to validate.
        path (str): JSON path for error messages.

    Raises:
        SchemaError: If `obj` is malformed.
    """
    _require_dict(obj, path)
    _require_keys(obj, REQUIRED_CHAIN_STEP_KEYS, path)
    if not isinstance(obj[KEY_IS_INDIRECT], bool):
        raise SchemaError(f"{path}.{KEY_IS_INDIRECT} must be a bool")
    if KEY_TOPIC_LATENCY_MS in obj:
        validate_optional_metric(
            obj[KEY_TOPIC_LATENCY_MS], f"{path}.{KEY_TOPIC_LATENCY_MS}"
        )
    if KEY_TAKE_TO_CALLBACK_START_MS in obj:
        validate_optional_metric(
            obj[KEY_TAKE_TO_CALLBACK_START_MS],
            f"{path}.{KEY_TAKE_TO_CALLBACK_START_MS}",
        )
    if KEY_CALLBACK_TIME_MS in obj:
        validate_optional_metric(
            obj[KEY_CALLBACK_TIME_MS], f"{path}.{KEY_CALLBACK_TIME_MS}"
        )
    if KEY_INDIRECT_WAIT_MS in obj:
        validate_optional_metric(
            obj[KEY_INDIRECT_WAIT_MS], f"{path}.{KEY_INDIRECT_WAIT_MS}"
        )
    if KEY_RELAY_CALLBACK_TIME_MS in obj:
        validate_optional_metric(
            obj[KEY_RELAY_CALLBACK_TIME_MS], f"{path}.{KEY_RELAY_CALLBACK_TIME_MS}"
        )
    if KEY_RELAY_CALLBACK_TOPIC in obj:
        value = obj[KEY_RELAY_CALLBACK_TOPIC]
        if value is not None and not isinstance(value, str):
            raise SchemaError(f"{path}.{KEY_RELAY_CALLBACK_TOPIC} must be a string")


def validate_endpoint(obj: Any, path: str) -> None:
    """
    Validate one `step_endpoints` row's `from`/`to` endpoint.

    Args:
        obj (Any): The value to validate.
        path (str): JSON path for error messages.

    Raises:
        SchemaError: If `obj` is malformed.
    """
    _require_dict(obj, path)
    _require_keys(obj, REQUIRED_ENDPOINT_KEYS, path)
    if not isinstance(obj[KEY_KIND], str) or obj[KEY_KIND] not in ENDPOINT_KINDS:
        raise SchemaError(f"{path}.{KEY_KIND} must be one of {sorted(ENDPOINT_KINDS)}")


def validate_step_endpoint_row(obj: Any, path: str) -> None:
    """
    Validate one entry of a chain's `step_endpoints` list.

    Every check is delegated, so a malformed row surfaces as the `SchemaError` raised
    by whichever helper rejected it.

    Args:
        obj (Any): The value to validate.
        path (str): JSON path for error messages.
    """
    _require_dict(obj, path)
    _require_keys(obj, REQUIRED_STEP_ENDPOINT_ROW_KEYS, path)
    validate_endpoint(obj[KEY_FROM], f"{path}.{KEY_FROM}")
    validate_endpoint(obj[KEY_TO], f"{path}.{KEY_TO}")
    validate_optional_metric(obj[KEY_METRIC_MS], f"{path}.{KEY_METRIC_MS}")


def validate_chain(obj: Any, path: str) -> None:
    """
    Validate one entry of the top-level `pipeline_chain_latency` list.

    Args:
        obj (Any): The value to validate.
        path (str): JSON path for error messages.

    Raises:
        SchemaError: If `obj` is malformed.
    """
    _require_dict(obj, path)
    _require_keys(obj, REQUIRED_CHAIN_KEYS, path)
    if not isinstance(obj[KEY_STEPS], list):
        raise SchemaError(f"{path}.{KEY_STEPS} must be a list")
    if not isinstance(obj[KEY_STEP_ENDPOINTS], list):
        raise SchemaError(f"{path}.{KEY_STEP_ENDPOINTS} must be a list")
    if obj[KEY_TERMINATOR] not in CHAIN_TERMINATORS:
        raise SchemaError(
            f"{path}.{KEY_TERMINATOR} must be one of {sorted(CHAIN_TERMINATORS)}"
        )
    for i, step in enumerate(obj[KEY_STEPS]):
        validate_chain_step(step, f"{path}.{KEY_STEPS}[{i}]")
    for i, row in enumerate(obj[KEY_STEP_ENDPOINTS]):
        validate_step_endpoint_row(row, f"{path}.{KEY_STEP_ENDPOINTS}[{i}]")
    if KEY_CHAIN_LATENCY_MS in obj:
        validate_optional_metric(
            obj[KEY_CHAIN_LATENCY_MS], f"{path}.{KEY_CHAIN_LATENCY_MS}"
        )


def _require_number_list(obj: Any, path: str, expected_len: int) -> None:
    """
    Validate that `obj` is a list of numbers of an exact expected length.

    Args:
        obj (Any): The value to validate.
        path (str): JSON path for error messages.
        expected_len (int): The exact length `obj` must have.

    Raises:
        SchemaError: If `obj` is not a list, has the wrong length, or contains a
            non-number (bools are explicitly rejected) entry.
    """
    if not isinstance(obj, list):
        raise SchemaError(f"{path} must be a list")
    if len(obj) != expected_len:
        raise SchemaError(f"{path} must have length {expected_len}, got {len(obj)}")
    for i, value in enumerate(obj):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SchemaError(f"{path}[{i}] must be a number")


def validate_throughput(obj: Any, path: str) -> None:
    """
    Validate the top-level `throughput` block: bucket_interval_s a positive number,
    bucket_starts_s / total_hz equal-length number lists, and per_topic_hz a mapping of
    topic -> number list of the same length.

    Args:
        obj (Any): The value to validate.
        path (str): JSON path for error messages.

    Raises:
        SchemaError: If `obj` is malformed.
    """
    _require_dict(obj, path)
    _require_keys(obj, REQUIRED_THROUGHPUT_KEYS, path)
    interval = obj[KEY_BUCKET_INTERVAL_S]
    if (
        isinstance(interval, bool)
        or not isinstance(interval, (int, float))
        or interval <= 0
    ):
        raise SchemaError(f"{path}.{KEY_BUCKET_INTERVAL_S} must be a positive number")
    if not isinstance(obj[KEY_BUCKET_STARTS_S], list):
        raise SchemaError(f"{path}.{KEY_BUCKET_STARTS_S} must be a list")
    n_buckets = len(obj[KEY_BUCKET_STARTS_S])
    _require_number_list(
        obj[KEY_BUCKET_STARTS_S], f"{path}.{KEY_BUCKET_STARTS_S}", n_buckets
    )
    _require_number_list(obj[KEY_TOTAL_HZ], f"{path}.{KEY_TOTAL_HZ}", n_buckets)
    _require_dict(obj[KEY_PER_TOPIC_HZ], f"{path}.{KEY_PER_TOPIC_HZ}")
    for topic, series in obj[KEY_PER_TOPIC_HZ].items():
        _require_number_list(series, f"{path}.{KEY_PER_TOPIC_HZ}[{topic}]", n_buckets)


def validate_trace(trace: Any) -> None:
    """
    Validate the complete trace-analysis JSON.

    Args:
        trace (Any): The parsed JSON document.

    Raises:
        SchemaError: If `trace` is malformed, naming the exact path and field.
    """
    _require_dict(trace, KEY_TRACE)
    _require_keys(trace, (KEY_DURATION_S, KEY_NODES), KEY_TRACE)
    _require_dict(trace[KEY_NODES], f"trace.{KEY_NODES}")
    for name, node in trace[KEY_NODES].items():
        validate_node(node, f"{KEY_NODES}[{name}]")
    if KEY_PIPELINE_CHAIN_LATENCY in trace:
        if not isinstance(trace[KEY_PIPELINE_CHAIN_LATENCY], list):
            raise SchemaError(
                f"{KEY_TRACE}.{KEY_PIPELINE_CHAIN_LATENCY} must be a list"
            )
        for i, chain in enumerate(trace[KEY_PIPELINE_CHAIN_LATENCY]):
            validate_chain(chain, f"{KEY_PIPELINE_CHAIN_LATENCY}[{i}]")
    if KEY_THROUGHPUT in trace:
        validate_throughput(trace[KEY_THROUGHPUT], f"{KEY_TRACE}.{KEY_THROUGHPUT}")
