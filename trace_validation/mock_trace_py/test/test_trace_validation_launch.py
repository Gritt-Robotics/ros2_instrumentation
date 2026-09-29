# Copyright 2026 Gritt Robotics Inc.

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from launch import LaunchContext
from launch.actions import DeclareLaunchArgument, OpaqueFunction

_LAUNCH_FILE = Path(__file__).parent.parent / "launch" / "trace_validation.launch.py"
_spec = importlib.util.spec_from_file_location("trace_validation_launch", _LAUNCH_FILE)
_launch_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_launch_module)

TEST_TARGET_HZ = "10.0"
TEST_PAYLOAD_SIZE = "16b"
TEST_QOS_DEPTH = "1"
TEST_CSV_DIR = "/tmp/trace_test"
TEST_SESSION_NAME = "test_trace"
TEST_STAMPED_SESSION_NAME = "test_trace-20260101000000"
TEST_SESSION_DIR = f"{TEST_CSV_DIR}/{TEST_STAMPED_SESSION_NAME}"
TEST_MESSAGE_TYPE = _launch_module.MESSAGE_TYPE_VARIABLE
TEST_TRANSPORT = _launch_module.TRANSPORT_TYPED


def _make_context(
    *,
    pub_lang: str,
    sub_lang: str,
    duration_s: str,
    enable_tracing: str,
    relay_langs: str = "",
    message_type: str = TEST_MESSAGE_TYPE,
    relay_mode: str = _launch_module.RELAY_MODE_DIRECT,
    relay_publish_divisor: str = "10",
):
    context = LaunchContext()
    context.launch_configurations[_launch_module.TARGET_HZ_ARG] = TEST_TARGET_HZ
    context.launch_configurations[_launch_module.PAYLOAD_SIZE_ARG] = TEST_PAYLOAD_SIZE
    context.launch_configurations[_launch_module.QOS_DEPTH_ARG] = TEST_QOS_DEPTH
    context.launch_configurations[_launch_module.PUB_LANG_ARG] = pub_lang
    context.launch_configurations[_launch_module.SUB_LANG_ARG] = sub_lang
    context.launch_configurations[_launch_module.RELAY_LANGS_ARG] = relay_langs
    context.launch_configurations[_launch_module.RELAY_MODE_ARG] = relay_mode
    context.launch_configurations[_launch_module.RELAY_PUBLISH_DIVISOR_ARG] = (
        relay_publish_divisor
    )
    context.launch_configurations[_launch_module.CSV_DIR_ARG] = TEST_CSV_DIR
    context.launch_configurations[_launch_module.SESSION_NAME_ARG] = TEST_SESSION_NAME
    context.launch_configurations[_launch_module.DURATION_S_ARG] = duration_s
    context.launch_configurations[_launch_module.ENABLE_TRACING_ARG] = enable_tracing
    context.launch_configurations[_launch_module.MESSAGE_TYPE_ARG] = message_type
    context.launch_configurations[_launch_module.TRANSPORT_ARG] = TEST_TRANSPORT
    return context


def _patch_session_dir():
    # compute_session_dir() stamps a real (wall-clock) timestamp; stub it so tests stay
    # hermetic/deterministic, and stub os.makedirs so nothing is actually written to disk.
    return (
        patch.object(
            _launch_module,
            "compute_session_dir",
            return_value=(TEST_STAMPED_SESSION_NAME, TEST_SESSION_DIR),
        ),
        patch.object(_launch_module, "os"),
    )


def test_generate_launch_description_returns_declared_args_plus_one_opaque_function():
    launch_description = _launch_module.generate_launch_description()
    entities = launch_description.entities

    # One DeclareLaunchArgument per TRACE_LAUNCH_PARAMS key, plus 7 explicit args
    # (pub/sub/relay langs, relay_mode, enable_tracing, message_type, transport) and one
    # OpaqueFunction.
    expected_declared_arg_count = len(_launch_module.TRACE_LAUNCH_PARAMS) + 7
    assert len(entities) == expected_declared_arg_count + 1
    assert all(isinstance(entity, DeclareLaunchArgument) for entity in entities[:-1])
    assert isinstance(entities[-1], OpaqueFunction)


def test_launch_setup_constructs_publisher_and_subscriber_nodes():
    context = _make_context(
        pub_lang=_launch_module.LANG_CPP,
        sub_lang=_launch_module.LANG_PY,
        duration_s="30",
        enable_tracing="true",
    )

    pub_node_mock = MagicMock(name="pub_node")
    sub_node_mock = MagicMock(name="sub_node")

    mock_compute_session_dir, mock_os = _patch_session_dir()
    with (
        patch.object(_launch_module, "Node") as mock_node_cls,
        patch.object(_launch_module, "TimerAction") as mock_timer_cls,
        patch.object(_launch_module, "Shutdown") as mock_shutdown_cls,
        patch.object(_launch_module, "add_lttng_trace") as mock_add_lttng_trace,
        mock_compute_session_dir as mock_compute_session_dir_fn,
        mock_os,
    ):
        mock_node_cls.side_effect = [pub_node_mock, sub_node_mock]
        result = _launch_module.launch_setup(context)

    mock_compute_session_dir_fn.assert_called_once_with(TEST_CSV_DIR, TEST_SESSION_NAME)

    assert mock_node_cls.call_count == 2
    pub_call_kwargs = mock_node_cls.call_args_list[0].kwargs
    sub_call_kwargs = mock_node_cls.call_args_list[1].kwargs

    assert pub_call_kwargs["package"] == "mock_trace_cpp"
    assert pub_call_kwargs["executable"] == "mock_publisher_node"
    assert pub_call_kwargs["name"] == "cpp_publisher"
    pub_params = pub_call_kwargs["parameters"][0]
    assert pub_params["topic"] == _launch_module.TOPIC
    assert pub_params["trace_id_base"] == _launch_module.CPP_PUB_TRACE_ID_BASE
    assert pub_params["message_type"] == TEST_MESSAGE_TYPE
    assert pub_params["transport"] == TEST_TRANSPORT
    assert pub_params["output_csv"] == f"{TEST_SESSION_DIR}/cpp_pub.csv"

    assert sub_call_kwargs["package"] == "mock_trace_py"
    assert sub_call_kwargs["executable"] == "mock_subscriber_node"
    assert sub_call_kwargs["name"] == "py_subscriber"
    sub_params = sub_call_kwargs["parameters"][0]
    assert sub_params["topic"] == _launch_module.TOPIC
    assert sub_params["message_type"] == TEST_MESSAGE_TYPE
    assert sub_params["transport"] == TEST_TRANSPORT
    assert sub_params["output_csv"] == f"{TEST_SESSION_DIR}/py_sub.csv"

    mock_shutdown_cls.assert_called_once_with()
    mock_timer_cls.assert_called_once_with(
        period=30.0, actions=[mock_shutdown_cls.return_value]
    )
    mock_add_lttng_trace.assert_called_once()
    node_actions_arg = mock_add_lttng_trace.call_args.args[0]
    assert node_actions_arg == [
        pub_node_mock,
        sub_node_mock,
        mock_timer_cls.return_value,
    ]
    assert mock_add_lttng_trace.call_args.kwargs["enabled"] is True
    assert (
        mock_add_lttng_trace.call_args.kwargs["trace_name"] == TEST_STAMPED_SESSION_NAME
    )
    assert mock_add_lttng_trace.call_args.kwargs["csv_dir"] == TEST_SESSION_DIR
    assert result is mock_add_lttng_trace.return_value


def test_launch_setup_uses_py_pub_trace_id_base_for_py_publisher():
    context = _make_context(
        pub_lang=_launch_module.LANG_PY,
        sub_lang=_launch_module.LANG_CPP,
        duration_s="30",
        enable_tracing="true",
    )

    mock_compute_session_dir, mock_os = _patch_session_dir()
    with (
        patch.object(_launch_module, "Node") as mock_node_cls,
        patch.object(_launch_module, "TimerAction"),
        patch.object(_launch_module, "Shutdown"),
        patch.object(_launch_module, "add_lttng_trace"),
        mock_compute_session_dir,
        mock_os,
    ):
        _launch_module.launch_setup(context)

    pub_call_kwargs = mock_node_cls.call_args_list[0].kwargs
    pub_params = pub_call_kwargs["parameters"][0]
    assert pub_params["trace_id_base"] == _launch_module.PY_PUB_TRACE_ID_BASE


def test_launch_setup_with_zero_duration_does_not_construct_timer_action():
    context = _make_context(
        pub_lang=_launch_module.LANG_CPP,
        sub_lang=_launch_module.LANG_CPP,
        duration_s="0",
        enable_tracing="true",
    )

    pub_node_mock = MagicMock(name="pub_node")
    sub_node_mock = MagicMock(name="sub_node")

    mock_compute_session_dir, mock_os = _patch_session_dir()
    with (
        patch.object(_launch_module, "Node") as mock_node_cls,
        patch.object(_launch_module, "TimerAction") as mock_timer_cls,
        patch.object(_launch_module, "Shutdown") as mock_shutdown_cls,
        patch.object(_launch_module, "add_lttng_trace") as mock_add_lttng_trace,
        mock_compute_session_dir,
        mock_os,
    ):
        mock_node_cls.side_effect = [pub_node_mock, sub_node_mock]
        _launch_module.launch_setup(context)

    mock_timer_cls.assert_not_called()
    mock_shutdown_cls.assert_not_called()
    node_actions_arg = mock_add_lttng_trace.call_args.args[0]
    assert node_actions_arg == [pub_node_mock, sub_node_mock]


def test_generate_launch_description_widens_message_type_and_transport_choices():
    launch_description = _launch_module.generate_launch_description()
    entities_by_name = {
        entity.name: entity
        for entity in launch_description.entities
        if isinstance(entity, DeclareLaunchArgument)
    }

    message_type_arg = entities_by_name[_launch_module.MESSAGE_TYPE_ARG]
    assert message_type_arg.choices == [
        _launch_module.MESSAGE_TYPE_VARIABLE,
        _launch_module.MESSAGE_TYPE_FIXED,
        _launch_module.MESSAGE_TYPE_CHAIN,
    ]

    transport_arg = entities_by_name[_launch_module.TRANSPORT_ARG]
    assert transport_arg.choices == [
        _launch_module.TRANSPORT_TYPED,
        _launch_module.TRANSPORT_SERIALIZED,
    ]


def test_launch_setup_raises_on_fixed_message_type_with_relay_langs():
    context = _make_context(
        pub_lang=_launch_module.LANG_CPP,
        sub_lang=_launch_module.LANG_CPP,
        duration_s="30",
        enable_tracing="true",
        relay_langs="cpp",
        message_type=_launch_module.MESSAGE_TYPE_FIXED,
    )

    # Deliberately unmocked: proves launch_setup's own validation raises, before it ever
    # reaches Node() construction.
    with pytest.raises(ValueError, match="message_type=fixed is not supported"):
        _launch_module.launch_setup(context)


def test_launch_setup_raises_on_invalid_relay_langs_entry():
    context = _make_context(
        pub_lang=_launch_module.LANG_CPP,
        sub_lang=_launch_module.LANG_CPP,
        duration_s="30",
        enable_tracing="true",
        relay_langs="cpp,rust",
    )

    with pytest.raises(ValueError, match="Invalid entry 'rust'"):
        _launch_module.launch_setup(context)


def test_launch_setup_normalizes_whitespace_and_trailing_commas_in_relay_langs():
    context = _make_context(
        pub_lang=_launch_module.LANG_CPP,
        sub_lang=_launch_module.LANG_PY,
        duration_s="30",
        enable_tracing="true",
        relay_langs=" cpp, py,",
    )

    pub_node_mock = MagicMock(name="pub_node")
    relay0_mock = MagicMock(name="relay_0")
    relay1_mock = MagicMock(name="relay_1")
    sub_node_mock = MagicMock(name="sub_node")

    mock_compute_session_dir, mock_os = _patch_session_dir()
    with (
        patch.object(_launch_module, "Node") as mock_node_cls,
        patch.object(_launch_module, "TimerAction"),
        patch.object(_launch_module, "Shutdown"),
        patch.object(_launch_module, "add_lttng_trace") as mock_add_lttng_trace,
        mock_compute_session_dir,
        mock_os,
    ):
        mock_node_cls.side_effect = [
            pub_node_mock,
            relay0_mock,
            relay1_mock,
            sub_node_mock,
        ]
        _launch_module.launch_setup(context)

    # Same 4 Node(...) calls as "cpp,py" -- whitespace/trailing comma are normalized away.
    assert mock_node_cls.call_count == 4
    assert mock_add_lttng_trace.call_args.kwargs["topic_chains"] == [
        [
            "/trace_step0",
            "/trace_step1",
            "/trace_step2",
        ]
    ]


def test_launch_setup_with_relay_langs_inserts_relay_nodes_between_pub_and_sub():
    context = _make_context(
        pub_lang=_launch_module.LANG_CPP,
        sub_lang=_launch_module.LANG_PY,
        duration_s="30",
        enable_tracing="true",
        relay_langs="cpp,py",
    )

    pub_node_mock = MagicMock(name="pub_node")
    relay0_mock = MagicMock(name="relay_0")
    relay1_mock = MagicMock(name="relay_1")
    sub_node_mock = MagicMock(name="sub_node")

    mock_compute_session_dir, mock_os = _patch_session_dir()
    with (
        patch.object(_launch_module, "Node") as mock_node_cls,
        patch.object(_launch_module, "TimerAction"),
        patch.object(_launch_module, "Shutdown"),
        patch.object(_launch_module, "add_lttng_trace") as mock_add_lttng_trace,
        mock_compute_session_dir,
        mock_os,
    ):
        mock_node_cls.side_effect = [
            pub_node_mock,
            relay0_mock,
            relay1_mock,
            sub_node_mock,
        ]
        _launch_module.launch_setup(context)

    # pub_node + 2 relay_langs entries + sub_node = 4 Node(...) calls total.
    assert mock_node_cls.call_count == 4

    pub_call_kwargs = mock_node_cls.call_args_list[0].kwargs
    relay0_call_kwargs = mock_node_cls.call_args_list[1].kwargs
    relay1_call_kwargs = mock_node_cls.call_args_list[2].kwargs
    sub_call_kwargs = mock_node_cls.call_args_list[3].kwargs

    pub_params = pub_call_kwargs["parameters"][0]
    assert pub_params["topic"] == "trace_step0"
    assert pub_params["message_type"] == _launch_module.MESSAGE_TYPE_CHAIN

    assert relay0_call_kwargs["package"] == "mock_trace_cpp"
    assert relay0_call_kwargs["executable"] == "mock_relay_node"
    assert relay0_call_kwargs["name"] == "relay_0_cpp"
    relay0_params = relay0_call_kwargs["parameters"][0]
    assert relay0_params["topic_in"] == "trace_step0"
    assert relay0_params["topic_out"] == "trace_step1"
    assert relay0_params["trace_id_base"] == _launch_module.RELAY_TRACE_ID_BASE_START

    assert relay1_call_kwargs["package"] == "mock_trace_py"
    assert relay1_call_kwargs["executable"] == "mock_relay_node"
    assert relay1_call_kwargs["name"] == "relay_1_py"
    relay1_params = relay1_call_kwargs["parameters"][0]
    assert relay1_params["topic_in"] == "trace_step1"
    assert relay1_params["topic_out"] == "trace_step2"
    assert relay1_params["trace_id_base"] == (
        _launch_module.RELAY_TRACE_ID_BASE_START
        + _launch_module.RELAY_TRACE_ID_BASE_STEP
    )

    sub_params = sub_call_kwargs["parameters"][0]
    assert sub_params["topic"] == "trace_step2"
    assert sub_params["message_type"] == _launch_module.MESSAGE_TYPE_CHAIN

    # node_actions is [pub, *relays, sub, TimerAction] since duration_s="30" > 0.
    node_actions_arg = mock_add_lttng_trace.call_args.args[0]
    assert node_actions_arg[:4] == [
        pub_node_mock,
        relay0_mock,
        relay1_mock,
        sub_node_mock,
    ]
    assert len(node_actions_arg) == 5

    assert mock_add_lttng_trace.call_args.kwargs["topic_chains"] == [
        [
            "/trace_step0",
            "/trace_step1",
            "/trace_step2",
        ]
    ]


def test_launch_setup_with_empty_relay_langs_omits_topic_chain():
    context = _make_context(
        pub_lang=_launch_module.LANG_CPP,
        sub_lang=_launch_module.LANG_CPP,
        duration_s="30",
        enable_tracing="true",
        relay_langs="",
    )

    mock_compute_session_dir, mock_os = _patch_session_dir()
    with (
        patch.object(_launch_module, "Node"),
        patch.object(_launch_module, "TimerAction"),
        patch.object(_launch_module, "Shutdown"),
        patch.object(_launch_module, "add_lttng_trace") as mock_add_lttng_trace,
        mock_compute_session_dir,
        mock_os,
    ):
        _launch_module.launch_setup(context)

    assert mock_add_lttng_trace.call_args.kwargs["topic_chains"] is None


@pytest.mark.parametrize(
    ("payload_size_str", "expected_bytes"),
    [
        ("16b", 16),
        ("4kb", 4000),
        ("2mb", 2_000_000),
        ("256mb", 256_000_000),
    ],
)
def test_parse_payload_size_to_bytes_converts_units(payload_size_str, expected_bytes):
    assert (
        _launch_module.parse_payload_size_to_bytes(payload_size_str) == expected_bytes
    )


def test_parse_payload_size_to_bytes_raises_when_over_256mb_limit():
    with pytest.raises(ValueError, match="exceeds the .* limit"):
        _launch_module.parse_payload_size_to_bytes("257mb")


@pytest.mark.parametrize("payload_size_str", ["16", "16gb", "abcb", "", "16 b"])
def test_parse_payload_size_to_bytes_raises_on_invalid_format(payload_size_str):
    with pytest.raises(ValueError, match="Invalid payload_size"):
        _launch_module.parse_payload_size_to_bytes(payload_size_str)


def test_launch_setup_sets_node_payload_size_to_parsed_bytes_floor_divided_by_four():
    context = _make_context(
        pub_lang=_launch_module.LANG_CPP,
        sub_lang=_launch_module.LANG_PY,
        duration_s="30",
        enable_tracing="true",
    )
    context.launch_configurations[_launch_module.PAYLOAD_SIZE_ARG] = "4kb"

    mock_compute_session_dir, mock_os = _patch_session_dir()
    with (
        patch.object(_launch_module, "Node") as mock_node_cls,
        patch.object(_launch_module, "TimerAction"),
        patch.object(_launch_module, "Shutdown"),
        patch.object(_launch_module, "add_lttng_trace"),
        mock_compute_session_dir,
        mock_os,
    ):
        _launch_module.launch_setup(context)

    pub_call_kwargs = mock_node_cls.call_args_list[0].kwargs
    sub_call_kwargs = mock_node_cls.call_args_list[1].kwargs
    assert pub_call_kwargs["parameters"][0]["payload_size"] == 1000
    assert "payload_size" not in sub_call_kwargs["parameters"][0]
