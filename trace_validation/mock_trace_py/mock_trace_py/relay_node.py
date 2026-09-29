# Copyright 2026 Gritt Robotics Inc.

"""Mock trace relay node for chained trace-analyzer validation.

A relay node is the step in a chain that both subscribes and publishes: it receives a
TraceChainMessage on `topic_in` and, within the same callback, republishes a derived
TraceChainMessage on `topic_out`. This callback shape -- take then publish, in one
invocation -- is exactly the "chain hub" pattern `analyze_trace` already detects
from LTTng tracepoints alone, so chaining relay nodes together is directly observable by
the existing trace analyzer.
"""

import csv
import os
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile
from rclpy.serialization import deserialize_message, serialize_message
from std_msgs.msg import Header
from trace_instrumentation.lttng_trace import lttng_trace_methods
from trace_msgs.msg import TraceChainMessage

from mock_trace_py.message_dispatch import (
    TRANSPORT_TYPED,
    VALID_TRANSPORTS,
    validate_enum_param,
)
from mock_trace_py.node_setup import RELAY_CSV_HEADER, declare_and_load_parameters

# CSV flush and log throttle intervals
CSV_FLUSH_EVERY_N = 50
LOG_EVERY_N = 100

NANOSECONDS_PER_SECOND = 1_000_000_000
FILLER_VALUE = 0

LOGGER_NAME_RELAY = "mock_trace_py.relay"

PARAM_TOPIC_IN = "topic_in"
PARAM_TOPIC_OUT = "topic_out"
PARAM_PAYLOAD_SIZE = "payload_size"
PARAM_TRACE_ID_BASE = "trace_id_base"
PARAM_OUTPUT_CSV = "output_csv"
PARAM_QOS_DEPTH = "qos_depth"
PARAM_TRANSPORT = "transport"

PARAMS = {
    PARAM_TOPIC_IN: rclpy.Parameter.Type.STRING,
    PARAM_TOPIC_OUT: rclpy.Parameter.Type.STRING,
    PARAM_PAYLOAD_SIZE: rclpy.Parameter.Type.INTEGER,
    PARAM_TRACE_ID_BASE: rclpy.Parameter.Type.INTEGER,
    PARAM_OUTPUT_CSV: rclpy.Parameter.Type.STRING,
    PARAM_QOS_DEPTH: rclpy.Parameter.Type.INTEGER,
    PARAM_TRANSPORT: rclpy.Parameter.Type.STRING,
}


def build_relay_payload(upstream_payload: list, target_size: int) -> list:
    """
    Derive an outgoing payload from an upstream payload, embedding it at the front.

    Args:
        upstream_payload (list): The payload received from the previous step.
        target_size (int): The desired length of the outgoing payload.

    Returns:
        list: A list of length `target_size`: a copy of `upstream_payload` (truncated
                    if it doesn't fit), followed by filler values to reach `target_size`.
    """
    copied = list(upstream_payload[:target_size])
    filler_len = target_size - len(copied)
    return copied + [FILLER_VALUE] * filler_len


@lttng_trace_methods(LOGGER_NAME_RELAY)
class MockTraceRelayNode(Node):
    """
    Relay node: subscribes to a TraceChainMessage on `topic_in`, republishes a derived
    TraceChainMessage on `topic_out`.

    Propagates `chain_id` unchanged from the received message so all steps of one chain
    traversal share a single ground-truth correlation key, increments `step_index`, and
    records `upstream_trace_id`/`upstream_header` so a downstream analyzer can walk the
    chain backwards purely from ground-truth CSV columns. The outgoing payload embeds a
    copy of the received payload (see `build_relay_payload`), so there is a real data
    dependency between steps, not just a timing relay.
    """

    def __init__(self):
        """
        Initialize the relay node.

        Raises:
            ValueError: If payload_size < 0 or qos_depth <= 0.
        """
        super().__init__("mock_relay_node")

        declare_and_load_parameters(self, PARAMS)

        if self.payload_size < 0:
            raise ValueError(f"payload_size must be >= 0, got {self.payload_size}")
        if self.qos_depth <= 0:
            raise ValueError(f"qos_depth must be > 0, got {self.qos_depth}")
        validate_enum_param(self.transport, VALID_TRANSPORTS, PARAM_TRANSPORT)

        csv_dir = os.path.dirname(self.output_csv)
        if len(csv_dir) > 0:
            os.makedirs(csv_dir, exist_ok=True)

        self._csv_file = open(self.output_csv, "w", newline="")  # noqa: SIM115
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow(RELAY_CSV_HEADER)

        self._counter = 0
        self._log_counter = 0

        # Persistent outgoing message object: header/id/chain fields are mutated in
        # place per receive, same rationale as the publisher node. The payload field is
        # still rebuilt each step (see build_relay_payload) since it must be derived
        # from whatever payload the upstream step sent.
        self._out_msg = TraceChainMessage()
        self._out_msg.header = Header()

        qos = QoSProfile(depth=self.qos_depth)
        self._pub = self.create_publisher(TraceChainMessage, self.topic_out, qos)
        if self.transport == TRANSPORT_TYPED:
            self.create_subscription(
                TraceChainMessage, self.topic_in, self._on_message, qos
            )
        else:
            self.create_subscription(
                TraceChainMessage, self.topic_in, self._on_message_raw, qos, raw=True
            )

        self.get_logger().info(
            f"MockTraceRelayNode started: topic_in={self.topic_in}, "
            f"topic_out={self.topic_out}, transport={self.transport}"
        )

    def _on_message(self, msg: TraceChainMessage) -> None:
        """Relay a typed message received on `topic_in`.

        Args:
            msg (TraceChainMessage): The received message.
        """
        # Uses the monotonic clock, not self.get_clock() (ROS/sim time, which can jump
        # under use_sim_time), so this stays in the same time domain as the LTTng
        # trace's monotonic timestamps.
        wall_recv_ns = time.monotonic_ns()
        self._relay(msg, wall_recv_ns)

    def _on_message_raw(self, raw: bytes) -> None:
        """Deserialize and relay a message received on `topic_in`.

        Args:
            raw (bytes): The raw serialized message bytes received on `topic_in`.
        """
        # Uses the monotonic clock, not self.get_clock() (ROS/sim time, which can jump
        # under use_sim_time), so this stays in the same time domain as the LTTng
        # trace's monotonic timestamps.
        wall_recv_ns = time.monotonic_ns()
        msg = deserialize_message(raw, TraceChainMessage)
        self._relay(msg, wall_recv_ns)

    def _relay(self, upstream: TraceChainMessage, wall_recv_ns: int) -> None:
        """Build, publish, and log the outgoing message derived from an upstream message.

        Args:
            upstream (TraceChainMessage): The message received from the previous step.
            wall_recv_ns (int): Wall-clock receive time, in nanoseconds.
        """
        self._out_msg.header.stamp = self.get_clock().now().to_msg()
        self._out_msg.trace_id = self.trace_id_base + self._counter
        self._out_msg.chain_id = upstream.chain_id
        self._out_msg.step_index = upstream.step_index + 1
        self._out_msg.upstream_trace_id = upstream.trace_id
        self._out_msg.upstream_header = upstream.header
        self._out_msg.payload = build_relay_payload(upstream.payload, self.payload_size)

        # Uses the monotonic clock, not self.get_clock() (ROS/sim time, which can jump
        # under use_sim_time), so this stays in the same time domain as the LTTng
        # trace's monotonic timestamps.
        wall_publish_ns = time.monotonic_ns()

        if self.transport == TRANSPORT_TYPED:
            self._pub.publish(self._out_msg)
        else:
            self._pub.publish(serialize_message(self._out_msg))

        header_stamp_ns = (
            self._out_msg.header.stamp.sec * NANOSECONDS_PER_SECOND
            + self._out_msg.header.stamp.nanosec
        )
        upstream_header_stamp_ns = (
            upstream.header.stamp.sec * NANOSECONDS_PER_SECOND
            + upstream.header.stamp.nanosec
        )
        self._csv_writer.writerow([
            self._out_msg.trace_id,
            header_stamp_ns,
            wall_publish_ns,
            upstream_header_stamp_ns,
            wall_recv_ns,
            self.topic_in,
            self.topic_out,
            self.get_name(),
            len(upstream.payload),
            len(self._out_msg.payload),
            self._out_msg.chain_id,
            self._out_msg.step_index,
            self._out_msg.upstream_trace_id,
        ])

        self._counter += 1
        self._log_counter += 1

        if self._counter % CSV_FLUSH_EVERY_N == 0:
            self._csv_file.flush()

        if self._log_counter % LOG_EVERY_N == 0:
            self.get_logger().info(
                f"Relayed {self._log_counter} messages (trace_id={self._out_msg.trace_id})"
            )

    def destroy_node(self) -> None:
        """Flush and close CSV file on shutdown."""
        self._csv_file.flush()
        self._csv_file.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = MockTraceRelayNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
