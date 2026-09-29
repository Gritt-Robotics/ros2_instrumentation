# Copyright 2026 Gritt Robotics Inc.

import csv
from unittest.mock import MagicMock

import rclpy
from mock_trace_py.relay_node import MockTraceRelayNode, build_relay_payload
from trace_msgs.msg import TraceChainMessage

TEST_TOPIC_IN = "/mock_test_topic_in"
TEST_TOPIC_OUT = "/mock_test_topic_out"
TEST_PAYLOAD_SIZE = 16
TEST_TRACE_ID_BASE = 10_000_000_000
TEST_QOS_DEPTH = 10
TEST_TRANSPORT = "typed"


def _ros_args(output_csv, transport: str = TEST_TRANSPORT):
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
        f"transport:={transport}",
    ]


def test_build_relay_payload_truncates_longer_upstream_payload():
    upstream_payload = list(range(20))
    result = build_relay_payload(upstream_payload, target_size=5)
    assert result == [0, 1, 2, 3, 4]
    assert len(result) == 5


def test_build_relay_payload_pads_shorter_upstream_payload():
    upstream_payload = [7, 8, 9]
    result = build_relay_payload(upstream_payload, target_size=6)
    assert result == [7, 8, 9, 0, 0, 0]
    assert len(result) == 6


def test_build_relay_payload_exact_match_length():
    upstream_payload = [1, 2, 3, 4]
    result = build_relay_payload(upstream_payload, target_size=4)
    assert result == [1, 2, 3, 4]
    assert len(result) == 4


def test_build_relay_payload_empty_upstream_pads_entirely():
    result = build_relay_payload([], target_size=3)
    assert result == [0, 0, 0]


def test_relay_node_constructs_with_valid_params(tmp_path):
    output_csv = str(tmp_path / "relay.csv")
    rclpy.init(args=_ros_args(output_csv))
    try:
        node = MockTraceRelayNode()
        try:
            assert node.get_name() == "mock_relay_node"
        finally:
            node.destroy_node()
    finally:
        rclpy.shutdown()


def test_relay_node_constructs_with_serialized_transport(tmp_path):
    output_csv = str(tmp_path / "relay_serialized.csv")
    rclpy.init(args=_ros_args(output_csv, transport="serialized"))
    try:
        node = MockTraceRelayNode()
        try:
            assert node.get_name() == "mock_relay_node"
        finally:
            node.destroy_node()
    finally:
        rclpy.shutdown()


def test_relay_node_relays_message_and_writes_correct_csv_row(tmp_path):
    output_csv = str(tmp_path / "relay_tick.csv")
    rclpy.init(args=_ros_args(output_csv))
    try:
        node = MockTraceRelayNode()
        try:
            node._pub = MagicMock()

            upstream = TraceChainMessage()
            upstream.trace_id = 100
            upstream.chain_id = 100
            upstream.step_index = 0
            upstream.upstream_trace_id = 0
            upstream.payload = [1, 2, 3]

            node._relay(upstream, wall_recv_ns=999)
            node._csv_file.flush()
        finally:
            node.destroy_node()
    finally:
        rclpy.shutdown()

    with open(output_csv, newline="") as csv_file:
        header, row = list(csv.reader(csv_file))
    assert len(row) == len(header)
    assert row[header.index("chain_id")] == "100"
    assert row[header.index("step_index")] == "1"
    assert row[header.index("upstream_trace_id")] == "100"
    assert row[header.index("payload_len_in")] == "3"
    assert row[header.index("payload_len_out")] == str(TEST_PAYLOAD_SIZE)
