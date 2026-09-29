# Copyright 2026 Gritt Robotics Inc.

"""Mock trace publisher node for trace-analyzer validation."""

import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile
from rclpy.serialization import serialize_message
from std_msgs.msg import Header
from trace_instrumentation.lttng_trace import lttng_trace_methods
from trace_msgs.msg import TraceChainMessage, TraceMessage, TraceMessageFixed

from mock_trace_py.message_dispatch import (
    MESSAGE_TYPE_CHAIN,
    MESSAGE_TYPE_FIXED,
    MESSAGE_TYPE_VARIABLE,
    TRANSPORT_TYPED,
    VALID_MESSAGE_TYPES,
    VALID_TRANSPORTS,
    validate_enum_param,
)
from mock_trace_py.node_setup import (
    NANOSECONDS_PER_SECOND,
    PUBLISHER_CSV_HEADER,
    PUBLISHER_CSV_HEADER_CHAIN,
    close_ground_truth_csv,
    declare_and_load_parameters,
    open_ground_truth_csv,
    validate_non_empty_string_param,
)

# CSV flush and log throttle intervals
CSV_FLUSH_EVERY_N = 100
LOG_EVERY_N = 100

LOGGER_NAME_PUBLISHER = "mock_trace_py.publisher"

PARAM_TARGET_HZ = "target_hz"
PARAM_TOPIC = "topic"
PARAM_PAYLOAD_SIZE = "payload_size"
PARAM_TRACE_ID_BASE = "trace_id_base"
PARAM_OUTPUT_CSV = "output_csv"
PARAM_QOS_DEPTH = "qos_depth"
PARAM_MESSAGE_TYPE = "message_type"
PARAM_TRANSPORT = "transport"

PARAMS = {
    PARAM_TARGET_HZ: rclpy.Parameter.Type.DOUBLE,
    PARAM_TOPIC: rclpy.Parameter.Type.STRING,
    PARAM_PAYLOAD_SIZE: rclpy.Parameter.Type.INTEGER,
    PARAM_TRACE_ID_BASE: rclpy.Parameter.Type.INTEGER,
    PARAM_OUTPUT_CSV: rclpy.Parameter.Type.STRING,
    PARAM_QOS_DEPTH: rclpy.Parameter.Type.INTEGER,
    PARAM_MESSAGE_TYPE: rclpy.Parameter.Type.STRING,
    PARAM_TRANSPORT: rclpy.Parameter.Type.STRING,
}


@lttng_trace_methods(LOGGER_NAME_PUBLISHER)
class MockTracePublisherNode(Node):
    """
    Publisher node that emits TraceMessage/TraceMessageFixed/TraceChainMessage and
    records ground-truth CSVs.

    Each message carries a unique trace_id derived from a monotonic counter plus a
    configurable base, allowing multiple publishers on the same topic to have
    globally disjoint id ranges. The message_type parameter selects between the
    variable-size TraceMessage, the fixed 4MB TraceMessageFixed, and TraceChainMessage
    (this node acting as step 0 of a chain), and the transport parameter selects between
    a typed publisher and a serialized/raw-bytes publish.
    """

    def __init__(self):
        """
        Initialize the publisher node.

        Raises:
            ValueError: If topic or output_csv is empty, target_hz <= 0, payload_size
                < 0, or qos_depth <= 0.
        """
        super().__init__("mock_publisher_node")

        declare_and_load_parameters(self, PARAMS)

        validate_non_empty_string_param(self.topic, PARAM_TOPIC)
        validate_non_empty_string_param(self.output_csv, PARAM_OUTPUT_CSV)
        if self.target_hz <= 0:
            raise ValueError(f"target_hz must be > 0, got {self.target_hz}")
        if self.payload_size < 0:
            raise ValueError(f"payload_size must be >= 0, got {self.payload_size}")
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
            PUBLISHER_CSV_HEADER_CHAIN
            if self.message_type == MESSAGE_TYPE_CHAIN
            else PUBLISHER_CSV_HEADER
        )
        self._csv_file, self._csv_writer = open_ground_truth_csv(
            self.output_csv, csv_header
        )

        qos = QoSProfile(depth=self.qos_depth)
        # A typed publisher is used for both transports; "serialized" just publishes
        # pre-serialized bytes through it instead of a message instance.
        self._pub = self.create_publisher(self._msg_cls, self.topic, qos)

        self._counter = 0
        self._log_counter = 0

        # Persistent message object, allocated once and mutated in place in _tick().
        self._msg = self._msg_cls()
        self._msg.header = Header()

        if self.message_type in (MESSAGE_TYPE_VARIABLE, MESSAGE_TYPE_CHAIN):
            # Payload buffer is allocated once here since payload_size never changes;
            # _tick() only overwrites header/trace_id in place.
            self._payload = [0] * self.payload_size
            self._msg.payload = self._payload
        else:
            self.get_logger().info(
                f"message_type=fixed: ignoring payload_size={self.payload_size} "
                "(fixed payload is always 1000000 int32s)"
            )
            # self._msg.payload is already zero-initialized by TraceMessageFixed().

        if self.message_type == MESSAGE_TYPE_CHAIN:
            # This node is always step 0 of a chain: no upstream message exists yet.
            # chain_id is set per-message in _tick() (it equals this step's own trace_id).
            self._msg.step_index = 0
            self._msg.upstream_trace_id = 0
            self._msg.upstream_header = Header()

        self.create_timer(1.0 / self.target_hz, self._tick)

        self.get_logger().info(
            f"MockTracePublisherNode started: topic={self.topic}, hz={self.target_hz}, "
            f"id_base={self.trace_id_base}, message_type={self.message_type}, "
            f"transport={self.transport}"
        )

    def _tick(self) -> None:
        """Publish one message and record a ground-truth row to CSV."""
        # Steps 1-2: stamp header immediately before filling the message
        stamp = self.get_clock().now()
        self._msg.header.stamp = stamp.to_msg()

        # Step 3: assign unique id
        self._msg.trace_id = self.trace_id_base + self._counter

        if self.message_type == MESSAGE_TYPE_CHAIN:
            # This message starts a new chain traversal: chain_id is its own trace_id.
            self._msg.chain_id = self._msg.trace_id

        # payload is already set, don't need to re-initialize

        # Step 5: capture wall time just before publish, using the monotonic clock (not
        # ROS/sim time, which can jump) to match the LTTng trace's time domain.
        wall_publish_ns = time.monotonic_ns()

        # Step 6: publish
        if self.transport == TRANSPORT_TYPED:
            self._pub.publish(self._msg)
        else:
            self._pub.publish(serialize_message(self._msg))

        # Step 7: record ground-truth CSV row
        header_stamp_ns = (
            self._msg.header.stamp.sec * NANOSECONDS_PER_SECOND
            + self._msg.header.stamp.nanosec
        )
        csv_row = [
            self._msg.trace_id,
            header_stamp_ns,
            wall_publish_ns,
            self.topic,
            self.get_name(),
            len(self._msg.payload),
        ]
        if self.message_type == MESSAGE_TYPE_CHAIN:
            csv_row += [
                self._msg.chain_id,
                self._msg.step_index,
                self._msg.upstream_trace_id,
            ]
        self._csv_writer.writerow(csv_row)

        self._counter += 1
        self._log_counter += 1

        if self._counter % CSV_FLUSH_EVERY_N == 0:
            self._csv_file.flush()

        if self._log_counter % LOG_EVERY_N == 0:
            self.get_logger().info(
                f"Published {self._log_counter} messages (trace_id={self._msg.trace_id})"
            )

    def destroy_node(self) -> None:
        """Flush and close CSV file on shutdown."""
        close_ground_truth_csv(self._csv_file)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = MockTracePublisherNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
