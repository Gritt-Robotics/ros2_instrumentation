# Copyright 2026 Gritt Robotics Inc.

from unittest.mock import patch

import rclpy
from mock_trace_py import indirect_relay_node as relay_module
from mock_trace_py.indirect_relay_node import (
    EVT_MESSAGE_LINK_PUBLISH,
    EVT_MESSAGE_LINK_TAKE,
    MockTraceIndirectRelayNode,
)
from trace_msgs.msg import TraceChainMessage

TEST_TOPIC_IN = "/mock_test_topic_in"
TEST_TOPIC_OUT = "/mock_test_topic_out"
TEST_PAYLOAD_SIZE = 16
TEST_TRACE_ID_BASE = 10_000_000_000
TEST_QOS_DEPTH = 10
TEST_TRANSPORT = "typed"
TEST_PUBLISH_HZ = 1.0

TEST_WALL_RECV_NS = 1234

# Annotation calls are (logger_name, event_name, link_id, message).
ANNOTATION_EVENT_NAME_INDEX = 1
ANNOTATION_LINK_ID_INDEX = 2

STEP_TAKE = "take"
STEP_FIRE = "fire"


def _ros_args(output_csv: str) -> list:
    return [
        "--ros-args",
        "-p",
        f"topic_in:={TEST_TOPIC_IN}",
        "-p",
        f"topic_out:={TEST_TOPIC_OUT}",
        "-p",
        f"payload_size:={TEST_PAYLOAD_SIZE}",
        "-p",
        f"trace_id_base:={TEST_TRACE_ID_BASE}",
        "-p",
        f"output_csv:={output_csv}",
        "-p",
        f"qos_depth:={TEST_QOS_DEPTH}",
        "-p",
        f"transport:={TEST_TRANSPORT}",
        "-p",
        f"publish_hz:={TEST_PUBLISH_HZ}",
    ]


def _upstream_msg(trace_id: int) -> TraceChainMessage:
    msg = TraceChainMessage()
    msg.trace_id = trace_id
    msg.chain_id = trace_id
    msg.step_index = 0
    msg.payload = [0] * TEST_PAYLOAD_SIZE
    return msg


def _link_ids(mock_annotation, event_name: str) -> list:
    return [
        call.args[ANNOTATION_LINK_ID_INDEX]
        for call in mock_annotation.call_args_list
        if call.args[ANNOTATION_EVENT_NAME_INDEX] == event_name
    ]


def _drive(output_csv: str, steps: list) -> tuple:
    """Drive the relay's callbacks directly and collect the annotations it emitted.

    Args:
        output_csv (str): Path the node writes its relay CSV to.
        steps (list): Sequence of STEP_TAKE/STEP_FIRE strings to apply in order.

    Returns:
        tuple: (take_link_ids, publish_link_ids) in emission order.
    """
    rclpy.init(args=_ros_args(output_csv))
    try:
        with patch.object(relay_module, "lttng_trace_annotation") as mock_annotation:
            node = MockTraceIndirectRelayNode()
            try:
                upstream_trace_id = 0
                for step in steps:
                    if step == STEP_TAKE:
                        upstream_trace_id += 1
                        node._on_upstream_message(
                            _upstream_msg(upstream_trace_id), TEST_WALL_RECV_NS
                        )
                    else:
                        node._on_publish_timer()
            finally:
                node.destroy_node()
        return (
            _link_ids(mock_annotation, EVT_MESSAGE_LINK_TAKE),
            _link_ids(mock_annotation, EVT_MESSAGE_LINK_PUBLISH),
        )
    finally:
        rclpy.shutdown()


def test_every_take_arms_and_overwrites_the_previous_guid(tmp_path):
    takes, publishes = _drive(
        str(tmp_path / "burst.csv"), [STEP_TAKE, STEP_TAKE, STEP_TAKE, STEP_FIRE]
    )

    # Every update annotates a take and bumps the guid, so the publish is attributed
    # to the freshest one -- the two superseded takes never reached the wire.
    assert takes == [1, 2, 3]
    assert publishes == [3]


def test_timer_fires_across_an_input_gap_do_not_republish_the_same_guid(tmp_path):
    # Without the one-shot latch, repeated fires with no intervening take would each
    # republish the same guid, an unattributable 1:3 take:publish cardinality.
    takes, publishes = _drive(
        str(tmp_path / "gap.csv"),
        [STEP_TAKE, STEP_FIRE, STEP_FIRE, STEP_FIRE, STEP_TAKE, STEP_FIRE],
    )

    assert takes == [1, 2]
    assert publishes == [1, 2]
    assert len(publishes) == len(set(publishes))


def test_timer_before_any_take_publishes_nothing(tmp_path):
    takes, publishes = _drive(str(tmp_path / "empty.csv"), [STEP_FIRE, STEP_FIRE])

    assert takes == []
    assert publishes == []


def test_each_armed_take_maps_to_exactly_one_publish(tmp_path):
    # Alternating take/fire is the 1:1 case: every take is published exactly once.
    takes, publishes = _drive(
        str(tmp_path / "alternating.csv"), [STEP_TAKE, STEP_FIRE] * 4
    )

    assert takes == [1, 2, 3, 4]
    assert publishes == [1, 2, 3, 4]
