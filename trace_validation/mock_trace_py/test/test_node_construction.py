# Copyright 2026 Gritt Robotics Inc.

import csv
from typing import List
from unittest.mock import MagicMock

import pytest
import rclpy
from mock_trace_py.message_dispatch import (
    MESSAGE_TYPE_CHAIN,
    MESSAGE_TYPE_FIXED,
    MESSAGE_TYPE_VARIABLE,
    TRANSPORT_SERIALIZED,
    TRANSPORT_TYPED,
)
from mock_trace_py.publisher_node import MockTracePublisherNode
from mock_trace_py.subscriber_node import MockTraceSubscriberNode
from rclpy.serialization import serialize_message
from trace_msgs.msg import TraceChainMessage, TraceMessage, TraceMessageFixed

TEST_TOPIC = "/mock_test_topic"
TEST_TARGET_HZ = 10.0
TEST_PAYLOAD_SIZE = 16
TEST_TRACE_ID_BASE = 0
TEST_QOS_DEPTH = 10
TEST_MESSAGE_TYPE = MESSAGE_TYPE_VARIABLE
TEST_TRANSPORT = TRANSPORT_TYPED
TEST_MESSAGE_TYPE_FIXED = MESSAGE_TYPE_FIXED
TEST_TRANSPORT_SERIALIZED = TRANSPORT_SERIALIZED
TEST_MESSAGE_TYPE_CHAIN = MESSAGE_TYPE_CHAIN

TEST_MESSAGE_CLASSES_BY_TYPE = {
    TEST_MESSAGE_TYPE: TraceMessage,
    TEST_MESSAGE_TYPE_FIXED: TraceMessageFixed,
}

TEST_HEADER_STAMP_SEC = 5
TEST_HEADER_STAMP_NANOSEC = 123
TEST_TRACE_ID = 42
NANOSECONDS_PER_SECOND = 1_000_000_000


def _ros_args(
    output_csv: str,
    include_publisher_only_params: bool,
    message_type: str = TEST_MESSAGE_TYPE,
    transport: str = TEST_TRANSPORT,
) -> List[str]:
    args = [
        "--ros-args",
        "-p",
        f"topic:={TEST_TOPIC}",
        "-p",
        f"output_csv:={output_csv}",
        "-p",
        f"qos_depth:={TEST_QOS_DEPTH}",
        "-p",
        f"message_type:={message_type}",
        "-p",
        f"transport:={transport}",
    ]
    if include_publisher_only_params:
        args += [
            "-p",
            f"target_hz:={TEST_TARGET_HZ}",
            "-p",
            f"payload_size:={TEST_PAYLOAD_SIZE}",
            "-p",
            f"trace_id_base:={TEST_TRACE_ID_BASE}",
        ]
    return args


def test_publisher_node_constructs_with_valid_params(tmp_path):
    output_csv = str(tmp_path / "publisher.csv")
    rclpy.init(args=_ros_args(output_csv, include_publisher_only_params=True))
    try:
        node = MockTracePublisherNode()
        try:
            assert node.get_name() == "mock_publisher_node"
        finally:
            node.destroy_node()
    finally:
        rclpy.shutdown()


def test_subscriber_node_constructs_with_valid_params(tmp_path):
    output_csv = str(tmp_path / "subscriber.csv")
    rclpy.init(args=_ros_args(output_csv, include_publisher_only_params=False))
    try:
        node = MockTraceSubscriberNode()
        try:
            assert node.get_name() == "mock_subscriber_node"
        finally:
            node.destroy_node()
    finally:
        rclpy.shutdown()


def test_publisher_node_rejects_empty_topic(tmp_path):
    output_csv = str(tmp_path / "publisher.csv")
    args = _ros_args(output_csv, include_publisher_only_params=True)
    args[args.index(f"topic:={TEST_TOPIC}")] = "topic:=''"
    rclpy.init(args=args)
    try:
        with pytest.raises(ValueError):
            MockTracePublisherNode()
    finally:
        rclpy.shutdown()


def test_publisher_node_rejects_empty_output_csv(tmp_path):
    output_csv = str(tmp_path / "publisher.csv")
    args = _ros_args(output_csv, include_publisher_only_params=True)
    args[args.index(f"output_csv:={output_csv}")] = "output_csv:=''"
    rclpy.init(args=args)
    try:
        with pytest.raises(ValueError):
            MockTracePublisherNode()
    finally:
        rclpy.shutdown()


def test_subscriber_node_rejects_empty_topic(tmp_path):
    output_csv = str(tmp_path / "subscriber.csv")
    args = _ros_args(output_csv, include_publisher_only_params=False)
    args[args.index(f"topic:={TEST_TOPIC}")] = "topic:=''"
    rclpy.init(args=args)
    try:
        with pytest.raises(ValueError):
            MockTraceSubscriberNode()
    finally:
        rclpy.shutdown()


def test_subscriber_node_rejects_empty_output_csv(tmp_path):
    output_csv = str(tmp_path / "subscriber.csv")
    args = _ros_args(output_csv, include_publisher_only_params=False)
    args[args.index(f"output_csv:={output_csv}")] = "output_csv:=''"
    rclpy.init(args=args)
    try:
        with pytest.raises(ValueError):
            MockTraceSubscriberNode()
    finally:
        rclpy.shutdown()


def test_publisher_node_rejects_non_positive_target_hz(tmp_path):
    output_csv = str(tmp_path / "publisher.csv")
    args = _ros_args(output_csv, include_publisher_only_params=True)
    args[args.index(f"target_hz:={TEST_TARGET_HZ}")] = "target_hz:=0.0"
    rclpy.init(args=args)
    try:
        with pytest.raises(ValueError):
            MockTracePublisherNode()
    finally:
        rclpy.shutdown()


def test_publisher_node_rejects_negative_payload_size(tmp_path):
    output_csv = str(tmp_path / "publisher.csv")
    args = _ros_args(output_csv, include_publisher_only_params=True)
    args[args.index(f"payload_size:={TEST_PAYLOAD_SIZE}")] = "payload_size:=-1"
    rclpy.init(args=args)
    try:
        with pytest.raises(ValueError):
            MockTracePublisherNode()
    finally:
        rclpy.shutdown()


def test_publisher_node_rejects_non_positive_qos_depth(tmp_path):
    output_csv = str(tmp_path / "publisher.csv")
    args = _ros_args(output_csv, include_publisher_only_params=True)
    args[args.index(f"qos_depth:={TEST_QOS_DEPTH}")] = "qos_depth:=0"
    rclpy.init(args=args)
    try:
        with pytest.raises(ValueError):
            MockTracePublisherNode()
    finally:
        rclpy.shutdown()


def test_subscriber_node_rejects_non_positive_qos_depth(tmp_path):
    output_csv = str(tmp_path / "subscriber.csv")
    args = _ros_args(output_csv, include_publisher_only_params=False)
    args[args.index(f"qos_depth:={TEST_QOS_DEPTH}")] = "qos_depth:=0"
    rclpy.init(args=args)
    try:
        with pytest.raises(ValueError):
            MockTraceSubscriberNode()
    finally:
        rclpy.shutdown()


def test_publisher_node_constructs_with_fixed_message_type(tmp_path):
    output_csv = str(tmp_path / "publisher_fixed.csv")
    rclpy.init(
        args=_ros_args(
            output_csv,
            include_publisher_only_params=True,
            message_type=TEST_MESSAGE_TYPE_FIXED,
        )
    )
    try:
        node = MockTracePublisherNode()
        try:
            assert node.get_name() == "mock_publisher_node"
        finally:
            node.destroy_node()
    finally:
        rclpy.shutdown()


def test_subscriber_node_constructs_with_fixed_message_type(tmp_path):
    output_csv = str(tmp_path / "subscriber_fixed.csv")
    rclpy.init(
        args=_ros_args(
            output_csv,
            include_publisher_only_params=False,
            message_type=TEST_MESSAGE_TYPE_FIXED,
        )
    )
    try:
        node = MockTraceSubscriberNode()
        try:
            assert node.get_name() == "mock_subscriber_node"
        finally:
            node.destroy_node()
    finally:
        rclpy.shutdown()


def test_publisher_node_constructs_with_chain_message_type(tmp_path):
    output_csv = str(tmp_path / "publisher_chain.csv")
    rclpy.init(
        args=_ros_args(
            output_csv,
            include_publisher_only_params=True,
            message_type=TEST_MESSAGE_TYPE_CHAIN,
        )
    )
    try:
        node = MockTracePublisherNode()
        try:
            assert node.get_name() == "mock_publisher_node"
        finally:
            node.destroy_node()
    finally:
        rclpy.shutdown()


def test_subscriber_node_constructs_with_chain_message_type(tmp_path):
    output_csv = str(tmp_path / "subscriber_chain.csv")
    rclpy.init(
        args=_ros_args(
            output_csv,
            include_publisher_only_params=False,
            message_type=TEST_MESSAGE_TYPE_CHAIN,
        )
    )
    try:
        node = MockTraceSubscriberNode()
        try:
            assert node.get_name() == "mock_subscriber_node"
        finally:
            node.destroy_node()
    finally:
        rclpy.shutdown()


def test_publisher_tick_chain_message_type_writes_row_matching_header(tmp_path):
    output_csv = str(tmp_path / "publisher_chain_tick.csv")
    rclpy.init(
        args=_ros_args(
            output_csv,
            include_publisher_only_params=True,
            message_type=TEST_MESSAGE_TYPE_CHAIN,
        )
    )
    try:
        node = MockTracePublisherNode()
        try:
            node._pub = MagicMock()
            node._tick()
            node._csv_file.flush()
        finally:
            node.destroy_node()
    finally:
        rclpy.shutdown()

    with open(output_csv, newline="") as csv_file:
        header, row = list(csv.reader(csv_file))
    assert len(row) == len(header)
    assert header[-3:] == ["chain_id", "step_index", "upstream_trace_id"]
    assert row[header.index("step_index")] == "0"
    assert row[header.index("upstream_trace_id")] == "0"
    assert row[header.index("chain_id")] == row[header.index("trace_id")]


def test_subscriber_on_message_chain_message_type_writes_row_matching_header(tmp_path):
    output_csv = str(tmp_path / "subscriber_chain_tick.csv")
    rclpy.init(
        args=_ros_args(
            output_csv,
            include_publisher_only_params=False,
            message_type=TEST_MESSAGE_TYPE_CHAIN,
        )
    )
    try:
        node = MockTraceSubscriberNode()
        try:
            msg = TraceChainMessage()
            msg.header.stamp.sec = TEST_HEADER_STAMP_SEC
            msg.header.stamp.nanosec = TEST_HEADER_STAMP_NANOSEC
            msg.trace_id = TEST_TRACE_ID
            msg.chain_id = TEST_TRACE_ID
            msg.step_index = 2
            msg.upstream_trace_id = TEST_TRACE_ID - 1

            node._on_message(msg)
            node._csv_file.flush()
        finally:
            node.destroy_node()
    finally:
        rclpy.shutdown()

    with open(output_csv, newline="") as csv_file:
        header, row = list(csv.reader(csv_file))
    assert len(row) == len(header)
    assert header[-3:] == ["chain_id", "step_index", "upstream_trace_id"]
    assert row[header.index("chain_id")] == str(TEST_TRACE_ID)
    assert row[header.index("step_index")] == "2"
    assert row[header.index("upstream_trace_id")] == str(TEST_TRACE_ID - 1)


def test_publisher_node_constructs_with_serialized_transport(tmp_path):
    output_csv = str(tmp_path / "publisher_serialized.csv")
    rclpy.init(
        args=_ros_args(
            output_csv,
            include_publisher_only_params=True,
            transport=TEST_TRANSPORT_SERIALIZED,
        )
    )
    try:
        node = MockTracePublisherNode()
        try:
            assert node.get_name() == "mock_publisher_node"
        finally:
            node.destroy_node()
    finally:
        rclpy.shutdown()


def test_subscriber_node_constructs_with_serialized_transport(tmp_path):
    output_csv = str(tmp_path / "subscriber_serialized.csv")
    rclpy.init(
        args=_ros_args(
            output_csv,
            include_publisher_only_params=False,
            transport=TEST_TRANSPORT_SERIALIZED,
        )
    )
    try:
        node = MockTraceSubscriberNode()
        try:
            assert node.get_name() == "mock_subscriber_node"
        finally:
            node.destroy_node()
    finally:
        rclpy.shutdown()


def test_publisher_tick_serialized_transport_publishes_bytes(tmp_path):
    output_csv = str(tmp_path / "publisher_tick_serialized.csv")
    rclpy.init(
        args=_ros_args(
            output_csv,
            include_publisher_only_params=True,
            transport=TEST_TRANSPORT_SERIALIZED,
        )
    )
    try:
        node = MockTracePublisherNode()
        try:
            node._pub = MagicMock()
            node._tick()
            node._pub.publish.assert_called_once()
            (published,), _ = node._pub.publish.call_args
            assert isinstance(published, bytes)
        finally:
            node.destroy_node()
    finally:
        rclpy.shutdown()

    with open(output_csv, newline="") as csv_file:
        rows = list(csv.reader(csv_file))
    assert len(rows) == 2


@pytest.mark.parametrize("message_type", [TEST_MESSAGE_TYPE, TEST_MESSAGE_TYPE_FIXED])
def test_subscriber_on_message_raw_deserializes_and_logs(tmp_path, message_type):
    output_csv = str(tmp_path / "subscriber_on_message_raw.csv")
    rclpy.init(
        args=_ros_args(
            output_csv,
            include_publisher_only_params=False,
            message_type=message_type,
            transport=TEST_TRANSPORT_SERIALIZED,
        )
    )
    try:
        node = MockTraceSubscriberNode()
        try:
            msg = TEST_MESSAGE_CLASSES_BY_TYPE[message_type]()
            msg.header.stamp.sec = TEST_HEADER_STAMP_SEC
            msg.header.stamp.nanosec = TEST_HEADER_STAMP_NANOSEC
            msg.trace_id = TEST_TRACE_ID
            raw = serialize_message(msg)

            node._on_message_raw(raw)
            node._csv_file.flush()
        finally:
            node.destroy_node()
    finally:
        rclpy.shutdown()

    with open(output_csv, newline="") as csv_file:
        rows = list(csv.reader(csv_file))
    assert rows[1][0] == str(TEST_TRACE_ID)
    assert rows[1][1] == str(
        TEST_HEADER_STAMP_SEC * NANOSECONDS_PER_SECOND + TEST_HEADER_STAMP_NANOSEC
    )
