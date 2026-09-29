# Copyright 2026 Gritt Robotics Inc.

"""Mock trace subscriber node for trace-analyzer validation."""

import time
from typing import Union

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile
from rclpy.serialization import deserialize_message
from trace_instrumentation.lttng_trace import lttng_trace_methods
from trace_msgs.msg import TraceChainMessage, TraceMessage, TraceMessageFixed

from mock_trace_py.message_dispatch import (
    MESSAGE_TYPE_CHAIN,
    MESSAGE_TYPE_FIXED,
    TRANSPORT_TYPED,
    VALID_MESSAGE_TYPES,
    VALID_TRANSPORTS,
    validate_enum_param,
)
from mock_trace_py.node_setup import (
    NANOSECONDS_PER_SECOND,
    SUBSCRIBER_CSV_HEADER,
    SUBSCRIBER_CSV_HEADER_CHAIN,
    close_ground_truth_csv,
    declare_and_load_parameters,
    open_ground_truth_csv,
    validate_non_empty_string_param,
)

# Flush CSV every N received messages to bound data loss on crash
CSV_FLUSH_EVERY_N = 50

LOGGER_NAME_SUBSCRIBER = "mock_trace_py.subscriber"

PARAM_TOPIC = "topic"
PARAM_OUTPUT_CSV = "output_csv"
PARAM_QOS_DEPTH = "qos_depth"
PARAM_MESSAGE_TYPE = "message_type"
PARAM_TRANSPORT = "transport"

PARAMS = {
    PARAM_TOPIC: rclpy.Parameter.Type.STRING,
    PARAM_OUTPUT_CSV: rclpy.Parameter.Type.STRING,
    PARAM_QOS_DEPTH: rclpy.Parameter.Type.INTEGER,
    PARAM_MESSAGE_TYPE: rclpy.Parameter.Type.STRING,
    PARAM_TRANSPORT: rclpy.Parameter.Type.STRING,
}


@lttng_trace_methods(LOGGER_NAME_SUBSCRIBER)
class MockTraceSubscriberNode(Node):
    """
    Subscriber node that receives TraceMessage/TraceMessageFixed/TraceChainMessage and
    records ground-truth CSVs.

    Captures the wall receive time immediately in the callback so it closely brackets the
    rmw_take event recorded by the LTTng tracer. The message_type parameter selects between
    the variable-size TraceMessage, the fixed 4MB TraceMessageFixed, and TraceChainMessage
    (this node acting as the terminal sink of a chain), and the transport parameter selects
    between a typed subscription and a raw/serialized-bytes subscription.
    """

    def __init__(self):
        """
        Initialize the subscriber node.

        Raises:
            ValueError: If topic or output_csv is empty, or qos_depth <= 0.
        """
        super().__init__("mock_subscriber_node")

        declare_and_load_parameters(self, PARAMS)

        validate_non_empty_string_param(self.topic, PARAM_TOPIC)
        validate_non_empty_string_param(self.output_csv, PARAM_OUTPUT_CSV)
        if self.qos_depth <= 0:
            raise ValueError(f"qos_depth must be > 0, got {self.qos_depth}")
        validate_enum_param(self.message_type, VALID_MESSAGE_TYPES, PARAM_MESSAGE_TYPE)
        validate_enum_param(self.transport, VALID_TRANSPORTS, PARAM_TRANSPORT)

        if self.message_type == MESSAGE_TYPE_FIXED:
            self._msg_cls = TraceMessageFixed
        elif self.message_type == MESSAGE_TYPE_CHAIN:
            self._msg_cls = TraceChainMessage
        else:
            self._msg_cls = TraceMessage

        csv_header = (
            SUBSCRIBER_CSV_HEADER_CHAIN
            if self.message_type == MESSAGE_TYPE_CHAIN
            else SUBSCRIBER_CSV_HEADER
        )
        self._csv_file, self._csv_writer = open_ground_truth_csv(
            self.output_csv, csv_header
        )

        self._recv_counter = 0

        qos = QoSProfile(depth=self.qos_depth)
        if self.transport == TRANSPORT_TYPED:
            self.create_subscription(self._msg_cls, self.topic, self._on_message, qos)
        else:
            self.create_subscription(
                self._msg_cls, self.topic, self._on_message_raw, qos, raw=True
            )

        self.get_logger().info(
            f"MockTraceSubscriberNode started: topic={self.topic}, "
            f"message_type={self.message_type}, transport={self.transport}"
        )

    def _on_message(
        self, msg: Union[TraceMessage, TraceMessageFixed, TraceChainMessage]
    ) -> None:
        """Record trace_id and timing info for each typed message received.

        Args:
            msg (Union[TraceMessage, TraceMessageFixed, TraceChainMessage]): The
                        received message.
        """
        # Uses the monotonic clock (not ROS/sim time, which can jump) to match the
        # LTTng trace's time domain.
        wall_recv_ns = time.monotonic_ns()
        self._log_row(msg, wall_recv_ns)

    def _on_message_raw(self, raw: bytes) -> None:
        """Deserialize a raw message and record trace_id and timing info.

        Args:
            raw (bytes): The raw serialized message bytes received on the topic.
        """
        # Uses the monotonic clock (not ROS/sim time, which can jump) to match the
        # LTTng trace's time domain.
        wall_recv_ns = time.monotonic_ns()
        msg = deserialize_message(raw, self._msg_cls)
        self._log_row(msg, wall_recv_ns)

    def _log_row(
        self,
        msg: Union[TraceMessage, TraceMessageFixed, TraceChainMessage],
        wall_recv_ns: int,
    ) -> None:
        """Write a ground-truth CSV row for a received message.

        Args:
            msg (Union[TraceMessage, TraceMessageFixed, TraceChainMessage]): The
                        received message.
            wall_recv_ns (int): Wall-clock receive time, in nanoseconds.
        """
        header_stamp_ns = (
            msg.header.stamp.sec * NANOSECONDS_PER_SECOND + msg.header.stamp.nanosec
        )

        csv_row = [
            msg.trace_id,
            header_stamp_ns,
            wall_recv_ns,
            self.topic,
            self.get_name(),
        ]
        if self.message_type == MESSAGE_TYPE_CHAIN:
            csv_row += [msg.chain_id, msg.step_index, msg.upstream_trace_id]
        self._csv_writer.writerow(csv_row)

        self._recv_counter += 1
        if self._recv_counter % CSV_FLUSH_EVERY_N == 0:
            self._csv_file.flush()

    def destroy_node(self) -> None:
        """Flush and close CSV file on shutdown."""
        close_ground_truth_csv(self._csv_file)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = MockTraceSubscriberNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
