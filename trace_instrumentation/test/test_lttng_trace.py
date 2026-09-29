# Copyright 2026 Gritt Robotics Inc.

import functools
import logging
from typing import List
from unittest.mock import MagicMock, call, patch

import pytest
from trace_instrumentation import lttng_trace
from trace_instrumentation.lttng_trace import (
    linked_pub,
    linked_take,
    lttng_trace_methods,
    lttng_zone,
)
from trace_instrumentation.trace_analysis.graph import (
    CB_KIND_SERVICE,
    CB_KIND_SUBSCRIPTION,
    CB_KIND_TIMER,
    LinkDeclaration,
)
from trace_instrumentation.trace_analysis.python_zones import (
    LINK_KIND_PUB,
    LINK_KIND_TAKE,
    parse_link_declaration_msg,
    parse_zone_declaration_msg,
)

TEST_LOGGER_NAME = "mock_trace_py.test_logger"
TEST_ZONE_NAME = "test_zone"
TEST_CLASS_LOGGER_NAME = "mock_trace_py.test_class"
TEST_TOPIC = "/object_detections"
TEST_SERVICE_NAME = "/reset"
TEST_TIMER_PERIOD_S = 0.1
TEST_TIMER_PERIOD_NS = 100_000_000
TEST_MARKER = "sensor_input"
TEST_OTHER_MARKER = "command_input"
TEST_THIRD_MARKER = "map_pose"


class LinkNode:
    """Stand-in for a node instance: the link API only needs somewhere to hang state."""


def link_declarations(mock_logger: MagicMock) -> List[LinkDeclaration]:
    """
    Parse every link declaration a mocked logger recorded, in call order.

    Args:
        mock_logger (MagicMock): The logger `_get_logger` was patched to return.

    Returns:
        List[LinkDeclaration]: The parsed declarations, skipping any payload
        that is not a link declaration.
    """
    declarations = []
    for debug_call in mock_logger.debug.call_args_list:
        declaration = parse_link_declaration_msg(debug_call.args[0])
        if declaration is not None:
            declarations.append(declaration)
    return declarations


def test_get_logger_returns_logger_with_given_name():
    logger = lttng_trace._get_logger(TEST_LOGGER_NAME)

    assert logger.name == TEST_LOGGER_NAME


def test_get_logger_sets_debug_level_only_once():
    with patch.object(lttng_trace, "_CONFIGURED_LOGGER_NAMES", set()):
        logger = lttng_trace._get_logger(TEST_LOGGER_NAME)
        assert logger.level == logging.DEBUG

        logger.setLevel(logging.WARNING)
        lttng_trace._get_logger(TEST_LOGGER_NAME)

        assert logger.level == logging.WARNING


def test_get_logger_filters_trace_records_out_of_console_handler():
    console_handler = logging.StreamHandler()
    agent_handler = logging.Handler()
    agent_handler.emit = MagicMock()
    root = logging.getLogger()
    root.addHandler(console_handler)
    root.addHandler(agent_handler)
    try:
        with patch.object(lttng_trace, "_CONFIGURED_LOGGER_NAMES", set()):
            lttng_trace._fire(TEST_LOGGER_NAME, "some:event")

            assert console_handler.filters == [lttng_trace._drop_trace_records]
            # The lttngust agent's handler must still receive the record, or the
            # tracepoint is silenced along with the console noise.
            assert len(agent_handler.filters) == 0
            assert agent_handler.emit.call_count == 1
            assert not console_handler.filter(agent_handler.emit.call_args.args[0])
    finally:
        root.removeHandler(console_handler)
        root.removeHandler(agent_handler)


def test_get_logger_does_not_filter_file_handler(tmp_path):
    file_handler = logging.FileHandler(tmp_path / "test.log")
    root = logging.getLogger()
    root.addHandler(file_handler)
    try:
        with patch.object(lttng_trace, "_CONFIGURED_LOGGER_NAMES", set()):
            lttng_trace._fire(TEST_LOGGER_NAME, "some:event")

        assert file_handler.filters == []
    finally:
        root.removeHandler(file_handler)
        file_handler.close()


def test_get_logger_forces_propagation_on(monkeypatch):
    # Importing launch installs a logger class that creates loggers with propagation
    # already off, which would silence every tracepoint.
    monkeypatch.setattr(logging.getLogger(TEST_LOGGER_NAME), "propagate", False)

    with patch.object(lttng_trace, "_CONFIGURED_LOGGER_NAMES", set()):
        logger = lttng_trace._get_logger(TEST_LOGGER_NAME)

    assert logger.propagate is True


def test_drop_trace_records_keeps_records_from_other_loggers():
    with patch.object(lttng_trace, "_CONFIGURED_LOGGER_NAMES", {TEST_LOGGER_NAME}):
        trace_record = logging.LogRecord(
            TEST_LOGGER_NAME, logging.DEBUG, __file__, 0, "some:event", None, None
        )
        other_record = logging.LogRecord(
            "other_package.other", logging.DEBUG, __file__, 0, "kept", None, None
        )

        assert not lttng_trace._drop_trace_records(trace_record)
        assert lttng_trace._drop_trace_records(other_record)


def test_fire_logs_debug_message_when_lttng_available():
    mock_logger = MagicMock()

    with patch.object(lttng_trace, "_get_logger", return_value=mock_logger):
        lttng_trace._fire(TEST_LOGGER_NAME, "some:event")

    mock_logger.debug.assert_called_once_with("some:event")


def test_lttng_zone_fires_begin_and_end_tracepoints_in_order():
    fired_messages = []

    with (
        patch.object(
            lttng_trace,
            "_fire",
            side_effect=lambda _logger_name, msg: fired_messages.append(msg),
        ),
        lttng_zone(TEST_LOGGER_NAME, TEST_ZONE_NAME),
    ):
        fired_messages.append("inside")

    assert fired_messages == [
        f"{TEST_ZONE_NAME}:begin",
        "inside",
        f"{TEST_ZONE_NAME}:end",
    ]


def test_lttng_zone_fires_end_tracepoint_even_when_body_raises():
    fired_messages = []

    with (
        patch.object(
            lttng_trace,
            "_fire",
            side_effect=lambda _logger_name, msg: fired_messages.append(msg),
        ),
        pytest.raises(RuntimeError),
        lttng_zone(TEST_LOGGER_NAME, TEST_ZONE_NAME),
    ):
        raise RuntimeError("boom")

    assert fired_messages == [f"{TEST_ZONE_NAME}:begin", f"{TEST_ZONE_NAME}:end"]


def test_lttng_trace_methods_wraps_plain_method_with_zone_named_after_method():
    mock_logger = MagicMock()

    @lttng_trace_methods(TEST_CLASS_LOGGER_NAME)
    class DummyNode:
        def tick(self, value):
            return value * 2

    with patch.object(lttng_trace, "_get_logger", return_value=mock_logger):
        result = DummyNode().tick(21)

    assert result == 42
    assert mock_logger.debug.call_args_list == [call("tick:begin"), call("tick:end")]


def test_lttng_trace_methods_skips_dunder_staticmethod_classmethod_and_property():
    mock_logger = MagicMock()

    @lttng_trace_methods(TEST_CLASS_LOGGER_NAME)
    class DummyNode:
        def __init__(self, value):
            self.value = value

        def __repr__(self):
            return f"DummyNode({self.value})"

        @staticmethod
        def static_method():
            return "static"

        @classmethod
        def class_method(cls):
            return "class"

        @property
        def doubled(self):
            return self.value * 2

    with patch.object(lttng_trace, "_get_logger", return_value=mock_logger):
        node = DummyNode(3)
        assert node.static_method() == "static"
        assert node.class_method() == "class"
        assert node.doubled == 6

    mock_logger.debug.assert_not_called()


def test_lttng_trace_methods_decorates_class_in_place():
    class DummyNode:
        def tick(self):
            pass

    decorated_cls = lttng_trace_methods(TEST_CLASS_LOGGER_NAME)(DummyNode)

    assert decorated_cls is DummyNode


def test_lttng_trace_methods_auto_declares_subscription_zone_on_create_subscription():
    fired_messages = []

    @lttng_trace_methods(TEST_CLASS_LOGGER_NAME)
    class DummyNode:
        def create_subscription(self, msg_type, topic, callback, qos_profile):
            return ("subscription", msg_type, topic, callback, qos_profile)

        def object_detections_callback(self, msg):
            pass

    with patch.object(
        lttng_trace,
        "_fire",
        side_effect=lambda _logger_name, msg: fired_messages.append(msg),
    ):
        node = DummyNode()
        result = node.create_subscription(
            object(), TEST_TOPIC, node.object_detections_callback, 10
        )

    assert result[0] == "subscription"
    assert len(fired_messages) == 1
    declaration = parse_zone_declaration_msg(fired_messages[0])
    assert declaration.zone_name == "object_detections_callback"
    assert declaration.kind == CB_KIND_SUBSCRIPTION
    assert declaration.target == TEST_TOPIC


def test_lttng_trace_methods_auto_declares_timer_zone_on_create_timer():
    fired_messages = []

    @lttng_trace_methods(TEST_CLASS_LOGGER_NAME)
    class DummyNode:
        def create_timer(self, timer_period_sec, callback):
            return ("timer", timer_period_sec, callback)

        def tick_callback(self):
            pass

    with patch.object(
        lttng_trace,
        "_fire",
        side_effect=lambda _logger_name, msg: fired_messages.append(msg),
    ):
        node = DummyNode()
        result = node.create_timer(TEST_TIMER_PERIOD_S, node.tick_callback)

    assert result[0] == "timer"
    assert len(fired_messages) == 1
    declaration = parse_zone_declaration_msg(fired_messages[0])
    assert declaration.zone_name == "tick_callback"
    assert declaration.kind == CB_KIND_TIMER
    assert declaration.period_ns == TEST_TIMER_PERIOD_NS


def test_lttng_trace_methods_auto_declares_service_zone_on_create_service():
    fired_messages = []

    @lttng_trace_methods(TEST_CLASS_LOGGER_NAME)
    class DummyNode:
        def create_service(self, srv_type, srv_name, callback):
            return ("service", srv_type, srv_name, callback)

        def reset_callback(self, request, response):
            return response

    with patch.object(
        lttng_trace,
        "_fire",
        side_effect=lambda _logger_name, msg: fired_messages.append(msg),
    ):
        node = DummyNode()
        result = node.create_service(object(), TEST_SERVICE_NAME, node.reset_callback)

    assert result[0] == "service"
    assert len(fired_messages) == 1
    declaration = parse_zone_declaration_msg(fired_messages[0])
    assert declaration.zone_name == "reset_callback"
    assert declaration.kind == CB_KIND_SERVICE
    assert declaration.target == TEST_SERVICE_NAME


def test_lttng_trace_methods_auto_declares_subscription_zone_for_partial_callback():
    fired_messages = []

    @lttng_trace_methods(TEST_CLASS_LOGGER_NAME)
    class DummyNode:
        def create_subscription(self, msg_type, topic, callback, qos_profile):
            return ("subscription", msg_type, topic, callback, qos_profile)

        def object_detections_callback(self, msg, extra_arg):
            pass

    with patch.object(
        lttng_trace,
        "_fire",
        side_effect=lambda _logger_name, msg: fired_messages.append(msg),
    ):
        node = DummyNode()
        partial_callback = functools.partial(node.object_detections_callback, "extra")
        result = node.create_subscription(object(), TEST_TOPIC, partial_callback, 10)

    assert result[0] == "subscription"
    assert len(fired_messages) == 1
    declaration = parse_zone_declaration_msg(fired_messages[0])
    assert declaration.zone_name == "object_detections_callback"


def test_lttng_trace_methods_auto_declares_timer_zone_for_partial_callback():
    fired_messages = []

    @lttng_trace_methods(TEST_CLASS_LOGGER_NAME)
    class DummyNode:
        def create_timer(self, timer_period_sec, callback):
            return ("timer", timer_period_sec, callback)

        def tick_callback(self, extra_arg):
            pass

    with patch.object(
        lttng_trace,
        "_fire",
        side_effect=lambda _logger_name, msg: fired_messages.append(msg),
    ):
        node = DummyNode()
        partial_callback = functools.partial(node.tick_callback, "extra")
        result = node.create_timer(TEST_TIMER_PERIOD_S, partial_callback)

    assert result[0] == "timer"
    assert len(fired_messages) == 1
    declaration = parse_zone_declaration_msg(fired_messages[0])
    assert declaration.zone_name == "tick_callback"


def test_lttng_trace_methods_auto_declares_service_zone_for_partial_callback():
    fired_messages = []

    @lttng_trace_methods(TEST_CLASS_LOGGER_NAME)
    class DummyNode:
        def create_service(self, srv_type, srv_name, callback):
            return ("service", srv_type, srv_name, callback)

        def reset_callback(self, request, response, extra_arg):
            return response

    with patch.object(
        lttng_trace,
        "_fire",
        side_effect=lambda _logger_name, msg: fired_messages.append(msg),
    ):
        node = DummyNode()
        partial_callback = functools.partial(node.reset_callback, "extra")
        result = node.create_service(object(), TEST_SERVICE_NAME, partial_callback)

    assert result[0] == "service"
    assert len(fired_messages) == 1
    declaration = parse_zone_declaration_msg(fired_messages[0])
    assert declaration.zone_name == "reset_callback"


def test_lttng_trace_methods_skips_declaration_when_partial_callback_has_no_name():
    fired_messages = []

    @lttng_trace_methods(TEST_CLASS_LOGGER_NAME)
    class DummyNode:
        def create_subscription(self, msg_type, topic, callback, qos_profile):
            return ("subscription", msg_type, topic, callback, qos_profile)

    with patch.object(
        lttng_trace,
        "_fire",
        side_effect=lambda _logger_name, msg: fired_messages.append(msg),
    ):
        node = DummyNode()
        nameless_callback = functools.partial(MagicMock(spec=[]))
        result = node.create_subscription(object(), TEST_TOPIC, nameless_callback, 10)

    assert result[0] == "subscription"
    assert fired_messages == []


def test_lttng_trace_methods_skips_registration_wrapping_on_plain_class():
    @lttng_trace_methods(TEST_CLASS_LOGGER_NAME)
    class DummyNode:
        def tick(self):
            return "ticked"

    assert DummyNode().tick() == "ticked"
    assert not hasattr(DummyNode, "create_subscription")


def test_linked_pub_declares_a_uid_for_the_armed_marker():
    node = LinkNode()
    mock_logger = MagicMock()

    with patch.object(lttng_trace, "_get_logger", return_value=mock_logger):
        linked_pub(node, TEST_LOGGER_NAME, TEST_MARKER)

    declarations = link_declarations(mock_logger)
    assert len(declarations) == 1
    assert declarations[0].kind == LINK_KIND_PUB
    assert len(declarations[0].uids) == 1


def test_linked_pub_declares_nothing_while_the_marker_is_already_armed():
    node = LinkNode()
    mock_logger = MagicMock()

    with patch.object(lttng_trace, "_get_logger", return_value=mock_logger):
        linked_pub(node, TEST_LOGGER_NAME, TEST_MARKER)
        linked_pub(node, TEST_LOGGER_NAME, TEST_MARKER)

    assert len(link_declarations(mock_logger)) == 1


def test_linked_pub_declares_distinct_uids_across_node_instances():
    first_node = LinkNode()
    second_node = LinkNode()
    mock_logger = MagicMock()

    with patch.object(lttng_trace, "_get_logger", return_value=mock_logger):
        linked_pub(first_node, TEST_LOGGER_NAME, TEST_MARKER)
        linked_pub(second_node, TEST_LOGGER_NAME, TEST_MARKER)

    declarations = link_declarations(mock_logger)
    assert len(declarations) == 2
    first_uid = declarations[0].uids[0]
    second_uid = declarations[1].uids[0]
    assert second_uid > first_uid


def test_linked_take_names_armed_uids_in_marker_order():
    node = LinkNode()
    mock_logger = MagicMock()

    with patch.object(lttng_trace, "_get_logger", return_value=mock_logger):
        linked_pub(node, TEST_LOGGER_NAME, TEST_MARKER)
        linked_pub(node, TEST_LOGGER_NAME, TEST_OTHER_MARKER)
        linked_take(node, TEST_LOGGER_NAME, [TEST_OTHER_MARKER, TEST_MARKER])

    declarations = link_declarations(mock_logger)
    assert len(declarations) == 3
    marker_uid = declarations[0].uids[0]
    other_marker_uid = declarations[1].uids[0]
    assert declarations[2].kind == LINK_KIND_TAKE
    assert declarations[2].uids == [other_marker_uid, marker_uid]


def test_linked_take_names_only_the_armed_markers():
    node = LinkNode()
    mock_logger = MagicMock()

    with patch.object(lttng_trace, "_get_logger", return_value=mock_logger):
        linked_pub(node, TEST_LOGGER_NAME, TEST_MARKER)
        linked_take(
            node,
            TEST_LOGGER_NAME,
            [TEST_OTHER_MARKER, TEST_MARKER, TEST_THIRD_MARKER],
        )

    declarations = link_declarations(mock_logger)
    assert len(declarations) == 2
    assert declarations[1].uids == [declarations[0].uids[0]]


def test_linked_take_disarms_the_markers_it_consumed():
    node = LinkNode()
    mock_logger = MagicMock()

    with patch.object(lttng_trace, "_get_logger", return_value=mock_logger):
        linked_pub(node, TEST_LOGGER_NAME, TEST_MARKER)
        linked_take(node, TEST_LOGGER_NAME, [TEST_MARKER])
        linked_take(node, TEST_LOGGER_NAME, [TEST_MARKER])

    declarations = link_declarations(mock_logger)
    assert len(declarations) == 2
    assert declarations[1].kind == LINK_KIND_TAKE


def test_linked_pub_rearms_the_marker_with_a_new_uid_after_a_take():
    node = LinkNode()
    mock_logger = MagicMock()

    with patch.object(lttng_trace, "_get_logger", return_value=mock_logger):
        linked_pub(node, TEST_LOGGER_NAME, TEST_MARKER)
        linked_take(node, TEST_LOGGER_NAME, [TEST_MARKER])
        linked_pub(node, TEST_LOGGER_NAME, TEST_MARKER)

    declarations = link_declarations(mock_logger)
    assert len(declarations) == 3
    assert declarations[2].kind == LINK_KIND_PUB
    assert declarations[2].uids[0] > declarations[0].uids[0]


def test_linked_take_declares_nothing_when_no_marker_is_armed():
    node = LinkNode()
    mock_logger = MagicMock()

    with patch.object(lttng_trace, "_get_logger", return_value=mock_logger):
        linked_take(node, TEST_LOGGER_NAME, [TEST_MARKER])

    assert len(link_declarations(mock_logger)) == 0
