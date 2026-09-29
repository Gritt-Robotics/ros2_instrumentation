# Copyright 2026 Gritt Robotics Inc.

"""Launch file for the trace-analyzer validation system.

Starts one publisher and one subscriber, wired to each other on a single topic.
`pub_lang`/`sub_lang` independently select which language's node to launch for each
role, so all four combinations are available:
  - pub_lang=cpp, sub_lang=cpp:   C++-only pair
  - pub_lang=py,  sub_lang=py:    Python-only pair
  - pub_lang=cpp, sub_lang=py:    mixed, C++ publisher / Python subscriber
  - pub_lang=py,  sub_lang=cpp:   mixed, Python publisher / C++ subscriber

Optionally, `relay_langs` inserts a chain of relay nodes (each subscribing and
republishing TraceChainMessage on its own topic) between the publisher and subscriber,
so pub -> relay -> relay -> ... -> sub spans an arbitrary number of steps. Leaving
`relay_langs` at its default (empty string) reproduces the exact single pub/sub-pair
behavior this launch file always had.

The pair is wrapped with LTTng tracing via `add_lttng_trace` (enabled by default), and
auto-shuts-down after `duration_s` seconds.

Args:
  - duration_s:       Seconds to run before auto-shutdown. Default 30; set 0 for unlimited.
  - enable_tracing:   Whether to wrap the run with LTTng tracing. Default true; set to
                      false to run the publisher/subscriber pair without tracing overhead.
  - relay_langs:      Comma-separated list of 'cpp'/'py', one entry per relay step to
                      insert between the publisher and subscriber (e.g. "cpp,py" for two
                      relay steps). Default "" inserts no relays. When non-empty, the
                      publisher/subscriber are switched to TraceChainMessage regardless
                      of `message_type` (message_type=fixed is rejected in this case,
                      since there is no fixed-size chain message variant).
    - relay_mode:       Relay implementation for relay steps. "direct" runs
                                            mock_relay_node (take->publish in one callback), "indirect"
                                            runs mock_indirect_relay_node (take callback arms a one-shot
                                            latch, timer callback publishes and clears it).
    - relay_publish_divisor: For relay_mode:=indirect, how many times slower than
                                            target_hz the relay's publish timer runs. Sets how many
                                            upstream updates arrive per publish.

Every run's outputs -- the per-node CSVs, the raw CTF trace, and the analysis report --
are written together under one auto-generated, timestamped directory:
`<csv_dir>/<session_name>-<timestamp>/`. Within it: `py_pub.csv`/`cpp_sub.csv`/etc. (the
per-node CSVs), `ctf/` (the raw CTF trace, renamed there once the LTTng session has been
stopped and flushed), and the combined analysis report (callback summary, topic latency,
queue health, topic frequency) as `<session_name>-<timestamp>.txt` plus a
machine-readable `<session_name>-<timestamp>.json` sibling. When `relay_langs` is set,
this report also includes pipeline chain latency across the full step sequence.
"""

import os
import re
from typing import Dict, List, Tuple, Union

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    OpaqueFunction,
    Shutdown,
    TimerAction,
)
from launch.launch_context import LaunchContext
from launch.launch_description_entity import LaunchDescriptionEntity
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from trace_instrumentation.tracing_launch_action import (
    add_lttng_trace,
    compute_session_dir,
)

TARGET_HZ_ARG = "target_hz"
PAYLOAD_SIZE_ARG = "payload_size"
QOS_DEPTH_ARG = "qos_depth"
PUB_LANG_ARG = "pub_lang"
SUB_LANG_ARG = "sub_lang"
RELAY_LANGS_ARG = "relay_langs"
RELAY_MODE_ARG = "relay_mode"
RELAY_PUBLISH_DIVISOR_ARG = "relay_publish_divisor"
CSV_DIR_ARG = "csv_dir"
SESSION_NAME_ARG = "session_name"
DURATION_S_ARG = "duration_s"
ENABLE_TRACING_ARG = "enable_tracing"
MESSAGE_TYPE_ARG = "message_type"
TRANSPORT_ARG = "transport"

TOPIC_PARAM = "topic"
OUTPUT_CSV_PARAM = "output_csv"

LANG_CPP = "cpp"
LANG_PY = "py"

TRUE_STRING = "true"
MESSAGE_TYPE_VARIABLE = "variable"
MESSAGE_TYPE_FIXED = "fixed"
MESSAGE_TYPE_CHAIN = "chain"

TRANSPORT_TYPED = "typed"
TRANSPORT_SERIALIZED = "serialized"

TOPIC = "trace"
RELAY_LANGS_DEFAULT = ""
RELAY_LANGS_SEPARATOR = ","
RELAY_MODE_DIRECT = "direct"
RELAY_MODE_INDIRECT = "indirect"

EXECUTABLE_RELAY_DIRECT = "mock_relay_node"
EXECUTABLE_RELAY_INDIRECT = "mock_indirect_relay_node"

BYTE_UNIT_B = "b"
BYTE_UNIT_KB = "kb"
BYTE_UNIT_MB = "mb"
BYTES_PER_KB = 1000
BYTES_PER_MB = 1_000_000
PAYLOAD_SIZE_UNIT_TO_BYTES = {
    BYTE_UNIT_B: 1,
    BYTE_UNIT_KB: BYTES_PER_KB,
    BYTE_UNIT_MB: BYTES_PER_MB,
}
MAX_PAYLOAD_SIZE_BYTES = 256 * BYTES_PER_MB
# The node's actual payload_size parameter is the parsed byte count divided by this.
PAYLOAD_SIZE_DIVISOR = 4
PAYLOAD_SIZE_PATTERN = re.compile(r"^(\d+)(b|kb|mb)$")

# Identifying id bases, kept distinct per language/role for traceability in saved CSVs.
CPP_PUB_TRACE_ID_BASE = 0
PY_PUB_TRACE_ID_BASE = 1_000_000_000
# Relay step id bases start well past the publisher bases above so a relay's ids never
# collide with either publisher's, then increment by step index.
RELAY_TRACE_ID_BASE_START = 10_000_000_000
RELAY_TRACE_ID_BASE_STEP = 1_000_000_000

PUBLISHER_NODE_CONFIG = {
    LANG_CPP: {
        "package": "mock_trace_cpp",
        "name": "cpp_publisher",
        "trace_id_base": CPP_PUB_TRACE_ID_BASE,
        "csv_name": "cpp_pub.csv",
    },
    LANG_PY: {
        "package": "mock_trace_py",
        "name": "py_publisher",
        "trace_id_base": PY_PUB_TRACE_ID_BASE,
        "csv_name": "py_pub.csv",
    },
}
SUBSCRIBER_NODE_CONFIG = {
    LANG_CPP: {
        "package": "mock_trace_cpp",
        "name": "cpp_subscriber",
        "csv_name": "cpp_sub.csv",
    },
    LANG_PY: {
        "package": "mock_trace_py",
        "name": "py_subscriber",
        "csv_name": "py_sub.csv",
    },
}
RELAY_NODE_CONFIG = {
    LANG_CPP: {"package": "mock_trace_cpp"},
    LANG_PY: {"package": "mock_trace_py"},
}

# pub_lang/sub_lang are declared separately below because _declare_launch_args has no
# `choices=` support, which those two rely on for validation.
TRACE_LAUNCH_PARAMS = {
    TARGET_HZ_ARG: "10.0",
    PAYLOAD_SIZE_ARG: (
        "4mb",
        "Payload size as '<number><unit>' (unit: b, kb=1000 bytes, mb=1,000,000 "
        "bytes; max 256mb). The node's actual payload_size parameter is set to "
        "floor(total_bytes / 4).",
    ),
    QOS_DEPTH_ARG: "1",
    # Default is relative to the directory the launch command is run from;
    # add_lttng_trace() resolves it to an absolute path either way, so an absolute
    # path also works if passed explicitly.
    RELAY_PUBLISH_DIVISOR_ARG: (
        "10",
        "How many times slower than target_hz an indirect relay's publish timer runs "
        "(publish_hz = target_hz / divisor). Must be >= 1. This ratio is what selects "
        "the indirection shape: a divisor above 1 means several upstream updates arrive "
        "per publish, so the freshest take is the one attributed to it. Ignored unless "
        "relay_mode:=indirect.",
    ),
    CSV_DIR_ARG: "trace",
    SESSION_NAME_ARG: "trace",
    DURATION_S_ARG: (
        "30",
        "Duration to run the trace in seconds. Set to 0 for unlimited runtime.",
    ),
}


def _declare_launch_args(
    params: Dict[str, Union[str, Tuple[str, str]]],
) -> List[DeclareLaunchArgument]:
    """
    Build one DeclareLaunchArgument per entry of a launch-argument dict.

    Args:
        params (Dict[str, Union[str, Tuple[str, str]]]): Argument names mapped to
            either a default value, or a (default value, help string) pair.

    Returns:
        List[DeclareLaunchArgument]: The declarations, in the dict's order.
    """
    declarations = []
    for arg_name, value in params.items():
        default_value, description = (
            value if isinstance(value, tuple) else (value, None)
        )
        declarations.append(
            DeclareLaunchArgument(
                name=arg_name, default_value=default_value, description=description
            )
        )
    return declarations


def _launch_arg(context: LaunchContext, arg_name: str) -> str:
    """
    Resolve a declared launch argument to its string value.

    Args:
        context (LaunchContext): The launch context to resolve the argument against.
        arg_name (str): The launch argument's name.

    Returns:
        str: The argument's resolved value.
    """
    return LaunchConfiguration(arg_name).perform(context)


def parse_payload_size_to_bytes(payload_size_str: str) -> int:
    """
    Parse a unit-suffixed payload size string into a total byte count.

    Args:
        payload_size_str (str): Size string in the form "{number}{unit}", where unit
            is one of "b", "kb" (1000 bytes), "mb" (1,000,000 bytes).

    Returns:
        int: Total size in bytes.

    Raises:
        ValueError: If payload_size_str doesn't match the expected format, or if the
            resulting byte count exceeds MAX_PAYLOAD_SIZE_BYTES.
    """
    match = PAYLOAD_SIZE_PATTERN.match(payload_size_str)
    if match is None:
        raise ValueError(
            f"Invalid {PAYLOAD_SIZE_ARG} '{payload_size_str}'. Expected format "
            f"'<number><unit>' where unit is one of "
            f"{sorted(PAYLOAD_SIZE_UNIT_TO_BYTES)} (e.g. '16b', '4mb')."
        )
    number_str, unit = match.groups()
    total_bytes = int(number_str) * PAYLOAD_SIZE_UNIT_TO_BYTES[unit]
    if total_bytes > MAX_PAYLOAD_SIZE_BYTES:
        raise ValueError(
            f"{PAYLOAD_SIZE_ARG} '{payload_size_str}' resolves to {total_bytes} bytes, "
            f"which exceeds the {MAX_PAYLOAD_SIZE_BYTES}-byte (256mb) limit."
        )
    return total_bytes


def launch_setup(context: LaunchContext) -> List[LaunchDescriptionEntity]:
    """
    Build the node/trace actions for the trace-analyzer validation launch.

    A malformed or oversized `payload_size` launch argument raises ValueError from
    `parse_payload_size_to_bytes`, propagated here uncaught.

    Args:
        context (LaunchContext): The launch context used to resolve launch
            configuration values.

    Returns:
        List[LaunchDescriptionEntity]: The publisher/subscriber nodes, optional
            shutdown timer, and LTTng trace wrapper to execute.

    Raises:
        ValueError: If message_type=fixed is combined with relay_langs, which has
            no fixed-size chain message variant.
    """
    target_hz = float(_launch_arg(context, TARGET_HZ_ARG))
    payload_size_str = _launch_arg(context, PAYLOAD_SIZE_ARG)
    total_payload_bytes = parse_payload_size_to_bytes(payload_size_str)
    payload_size = total_payload_bytes // PAYLOAD_SIZE_DIVISOR
    qos_depth = int(_launch_arg(context, QOS_DEPTH_ARG))
    pub_lang = _launch_arg(context, PUB_LANG_ARG)
    sub_lang = _launch_arg(context, SUB_LANG_ARG)
    relay_langs = _launch_arg(context, RELAY_LANGS_ARG)
    relay_mode = _launch_arg(context, RELAY_MODE_ARG)
    relay_publish_divisor = float(_launch_arg(context, RELAY_PUBLISH_DIVISOR_ARG))
    if relay_publish_divisor < 1.0:
        raise ValueError(
            f"{RELAY_PUBLISH_DIVISOR_ARG} must be >= 1.0, got {relay_publish_divisor}. "
            "A divisor below 1 would publish faster than updates arrive, associating one "
            "take with several publishes -- a cardinality the trace analyzer does not yet "
            "pair, so those publishes would go unattributed."
        )
    csv_dir = _launch_arg(context, CSV_DIR_ARG)
    session_name = _launch_arg(context, SESSION_NAME_ARG)
    duration_s = float(_launch_arg(context, DURATION_S_ARG))
    enable_tracing = _launch_arg(context, ENABLE_TRACING_ARG).lower() == TRUE_STRING
    message_type = _launch_arg(context, MESSAGE_TYPE_ARG)
    transport = _launch_arg(context, TRANSPORT_ARG)

    relay_langs_list = [
        entry.strip()
        for entry in relay_langs.split(RELAY_LANGS_SEPARATOR)
        if entry.strip() != ""
    ]
    for lang in relay_langs_list:
        if lang not in RELAY_NODE_CONFIG:
            raise ValueError(
                f"Invalid entry '{lang}' in {RELAY_LANGS_ARG}: expected one of "
                f"{sorted(RELAY_NODE_CONFIG)}."
            )

    if relay_mode not in {RELAY_MODE_DIRECT, RELAY_MODE_INDIRECT}:
        raise ValueError(
            f"Invalid {RELAY_MODE_ARG} '{relay_mode}': expected one of "
            f"{[RELAY_MODE_DIRECT, RELAY_MODE_INDIRECT]}."
        )

    if len(relay_langs_list) > 0:
        if message_type == MESSAGE_TYPE_FIXED:
            raise ValueError(
                "message_type=fixed is not supported together with relay_langs: there is "
                "no fixed-size chain message variant. Use message_type=variable (or omit "
                "message_type) when relay_langs is set."
            )
        # relay_langs implies a chain: pub/sub endpoints are switched to TraceChainMessage
        # regardless of the message_type arg's variable/chain value.
        endpoint_message_type = MESSAGE_TYPE_CHAIN
        step_topics = [f"{TOPIC}_step{i}" for i in range(len(relay_langs_list) + 1)]
    else:
        endpoint_message_type = message_type
        step_topics = [TOPIC]

    # Compute the per-run session directory up front so the CSVs, the raw CTF trace
    # (written by add_lttng_trace), and the analysis report all land under the same
    # timestamped folder instead of the CSVs landing in csv_dir directly. Created here
    # unconditionally since the nodes write their CSVs whether or not tracing is enabled.
    stamped_session_name, session_dir = compute_session_dir(csv_dir, session_name)
    os.makedirs(session_dir, exist_ok=True)

    pub_config = PUBLISHER_NODE_CONFIG[pub_lang]
    sub_config = SUBSCRIBER_NODE_CONFIG[sub_lang]

    pub_node = Node(
        package=pub_config["package"],
        executable="mock_publisher_node",
        name=pub_config["name"],
        output="screen",
        parameters=[
            {
                TARGET_HZ_ARG: target_hz,
                TOPIC_PARAM: step_topics[0],
                PAYLOAD_SIZE_ARG: payload_size,
                "trace_id_base": pub_config["trace_id_base"],
                OUTPUT_CSV_PARAM: f"{session_dir}/{pub_config['csv_name']}",
                QOS_DEPTH_ARG: qos_depth,
                MESSAGE_TYPE_ARG: endpoint_message_type,
                TRANSPORT_ARG: transport,
            }
        ],
    )

    relay_nodes = []
    for step_index, lang in enumerate(relay_langs_list):
        relay_config = RELAY_NODE_CONFIG[lang]
        relay_executable = EXECUTABLE_RELAY_DIRECT
        relay_name = f"relay_{step_index}_{lang}"
        relay_params = {
            "topic_in": step_topics[step_index],
            "topic_out": step_topics[step_index + 1],
            "payload_size": payload_size,
            "trace_id_base": (
                RELAY_TRACE_ID_BASE_START + step_index * RELAY_TRACE_ID_BASE_STEP
            ),
            "output_csv": f"{session_dir}/relay_{step_index}_{lang}.csv",
            "qos_depth": qos_depth,
            "transport": transport,
        }
        if relay_mode == RELAY_MODE_INDIRECT:
            relay_executable = EXECUTABLE_RELAY_INDIRECT
            relay_name = f"indirect_relay_{step_index}"
            relay_params["publish_hz"] = target_hz / relay_publish_divisor

        relay_nodes.append(
            Node(
                package=relay_config["package"],
                executable=relay_executable,
                name=relay_name,
                output="screen",
                parameters=[relay_params],
            )
        )

    sub_node = Node(
        package=sub_config["package"],
        executable="mock_subscriber_node",
        name=sub_config["name"],
        output="screen",
        parameters=[
            {
                TOPIC_PARAM: step_topics[-1],
                OUTPUT_CSV_PARAM: f"{session_dir}/{sub_config['csv_name']}",
                QOS_DEPTH_ARG: qos_depth,
                MESSAGE_TYPE_ARG: endpoint_message_type,
                TRANSPORT_ARG: transport,
            }
        ],
    )

    node_actions = [pub_node, *relay_nodes, sub_node]

    if duration_s > 0:
        # Measured from node startup (i.e. after the trace startup delay), not from
        # trace startup, so duration_s still reflects how long the nodes actually run.
        node_actions.append(TimerAction(period=duration_s, actions=[Shutdown()]))

    # LTTng-recorded topic names are the fully-resolved ROS graph names (e.g.
    # "/trace_step0"), not the relative "topic" parameter string passed to
    # create_publisher/create_subscription above -- resolve with a leading "/" so
    # --topic-chain matching in analyze_trace actually lines up.
    resolved_step_topics = [f"/{t}" for t in step_topics]

    # add_lttng_trace()'s own OnShutdown analysis handler runs after Trace's OnShutdown
    # handler has stopped/destroyed the LTTng session (registration order -- see its
    # docstring), so the CTF trace is complete/readable by the time analysis runs.
    return add_lttng_trace(
        node_actions,
        enabled=enable_tracing,
        trace_name=stamped_session_name,
        csv_dir=session_dir,
        topic_chains=[resolved_step_topics] if len(relay_langs_list) > 0 else None,
    )


def generate_launch_description() -> LaunchDescription:
    declared_args = _declare_launch_args(TRACE_LAUNCH_PARAMS) + [
        DeclareLaunchArgument(
            PUB_LANG_ARG,
            default_value=LANG_CPP,
            choices=[LANG_CPP, LANG_PY],
            description="Which language's publisher node to launch",
        ),
        DeclareLaunchArgument(
            SUB_LANG_ARG,
            default_value=LANG_CPP,
            choices=[LANG_CPP, LANG_PY],
            description="Which language's subscriber node to launch",
        ),
        DeclareLaunchArgument(
            RELAY_LANGS_ARG,
            default_value=RELAY_LANGS_DEFAULT,
            description=(
                "Comma-separated list of 'cpp'/'py', one entry per relay step to insert "
                "between the publisher and subscriber (e.g. 'cpp,py' for two relay steps). "
                "Default '' inserts no relays, reproducing the original single pub/sub pair."
            ),
        ),
        DeclareLaunchArgument(
            RELAY_MODE_ARG,
            default_value=RELAY_MODE_DIRECT,
            choices=[RELAY_MODE_DIRECT, RELAY_MODE_INDIRECT],
            description=(
                "Relay implementation for cpp relay steps: 'direct' uses "
                "mock_relay_node, 'indirect' uses mock_indirect_relay_node."
            ),
        ),
        DeclareLaunchArgument(
            ENABLE_TRACING_ARG,
            default_value=TRUE_STRING,
            description="Enable LTTng tracing for this run. Set to false to run the "
            "publisher/subscriber pair without tracing overhead.",
        ),
        DeclareLaunchArgument(
            MESSAGE_TYPE_ARG,
            default_value=MESSAGE_TYPE_VARIABLE,
            choices=[MESSAGE_TYPE_VARIABLE, MESSAGE_TYPE_FIXED, MESSAGE_TYPE_CHAIN],
            description=(
                "Payload message type: 'variable' (TraceMessage, size set by payload_size), "
                "'fixed' (TraceMessageFixed, always 1,000,000 int32s / ~4MB), or 'chain' "
                "(TraceChainMessage; automatically selected when relay_langs is set)"
            ),
        ),
        DeclareLaunchArgument(
            TRANSPORT_ARG,
            default_value=TRANSPORT_TYPED,
            choices=[TRANSPORT_TYPED, TRANSPORT_SERIALIZED],
            description=(
                "Pub/sub transport: 'typed' (create_publisher/create_subscription) or "
                "'serialized' (generic/raw serialized pub-sub)"
            ),
        ),
    ]

    return LaunchDescription([*declared_args, OpaqueFunction(function=launch_setup)])
