# Copyright 2026 Gritt Robotics Inc.

"""Mock trace indirect relay node for chained trace-analyzer validation.

This relay decouples receive and publish callbacks:
- subscription callback caches the latest upstream message and arms a one-shot guard
- timer callback publishes only when the guard is armed, then clears it

It emits lttng_python:event annotations carrying the same logical event names
used by C++ custom tracepoints:
- ros2:message_link_take
- ros2:message_link_publish
"""

import csv
import os
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile
from rclpy.serialization import deserialize_message, serialize_message
from std_msgs.msg import Header
from trace_instrumentation.lttng_trace import (
    lttng_trace_annotation,
    lttng_trace_methods,
)
from trace_instrumentation.trace_analysis.event_names import (
    EVT_MESSAGE_LINK_PUBLISH,
    EVT_MESSAGE_LINK_TAKE,
)
from trace_msgs.msg import TraceChainMessage

from mock_trace_py.message_dispatch import (
    TRANSPORT_TYPED,
    VALID_TRANSPORTS,
    validate_enum_param,
)
from mock_trace_py.node_setup import RELAY_CSV_HEADER, declare_and_load_parameters
from mock_trace_py.relay_node import (
    CSV_FLUSH_EVERY_N,
    LOG_EVERY_N,
    LOGGER_NAME_RELAY,
    NANOSECONDS_PER_SECOND,
    PARAM_TRANSPORT,
    PARAMS,
    build_relay_payload,
)

PARAM_PUBLISH_HZ = "publish_hz"
PARAMS_INDIRECT = dict(PARAMS)
PARAMS_INDIRECT[PARAM_PUBLISH_HZ] = rclpy.Parameter.Type.DOUBLE


@lttng_trace_methods(LOGGER_NAME_RELAY)
class MockTraceIndirectRelayNode(Node):
    """Python indirect relay: receive/cache in one callback, publish in timer callback.

    Raises:
        ValueError: If any of the parameters are invalid.
    """

    def __init__(self):
        super().__init__("mock_indirect_relay_node")

        declare_and_load_parameters(self, PARAMS_INDIRECT)

        if self.payload_size < 0:
            raise ValueError(f"payload_size must be >= 0, got {self.payload_size}")
        if self.qos_depth <= 0:
            raise ValueError(f"qos_depth must be > 0, got {self.qos_depth}")
        if self.publish_hz <= 0.0:
            raise ValueError(f"publish_hz must be > 0, got {self.publish_hz}")
        validate_enum_param(self.transport, VALID_TRANSPORTS, PARAM_TRANSPORT)

        csv_dir = os.path.dirname(self.output_csv)
        if len(csv_dir) > 0:
            os.makedirs(csv_dir, exist_ok=True)

        self._csv_file = open(self.output_csv, "w", newline="")  # noqa: SIM115
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow(RELAY_CSV_HEADER)

        self._counter = 0
        self._log_counter = 0
        self._callback_guid = 0
        self._armed_callback_guid = 0
        self._armed = False

        self._latest_upstream = None
        self._latest_wall_recv_ns = 0

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

        self.create_timer(1.0 / self.publish_hz, self._on_publish_timer)

        self.get_logger().info(
            f"MockTraceIndirectRelayNode started: topic_in={self.topic_in}, "
            f"topic_out={self.topic_out}, transport={self.transport}, "
            f"publish_hz={self.publish_hz:.3f}"
        )

    def _on_message(self, msg: TraceChainMessage) -> None:
        wall_recv_ns = time.monotonic_ns()
        self._on_upstream_message(msg, wall_recv_ns)

    def _on_message_raw(self, raw: bytes) -> None:
        wall_recv_ns = time.monotonic_ns()
        msg = deserialize_message(raw, TraceChainMessage)
        self._on_upstream_message(msg, wall_recv_ns)

    def _on_upstream_message(
        self, upstream: TraceChainMessage, wall_recv_ns: int
    ) -> None:
        self._latest_upstream = upstream
        self._latest_wall_recv_ns = wall_recv_ns

        # Arm on every update, overwriting any guid a superseded take left behind, so
        # the publish is attributed to the freshest take.
        self._callback_guid += 1
        self._armed_callback_guid = self._callback_guid
        self._armed = True
        lttng_trace_annotation(
            LOGGER_NAME_RELAY,
            EVT_MESSAGE_LINK_TAKE,
            self._armed_callback_guid,
            id(upstream),
        )

    def _on_publish_timer(self) -> None:
        #  only publish when an update has armed it since the last pub
        if not self._armed:
            return

        self._armed = False
        self._relay(self._latest_upstream, self._latest_wall_recv_ns)

    def _relay(self, upstream: TraceChainMessage, wall_recv_ns: int) -> None:
        self._out_msg.header.stamp = self.get_clock().now().to_msg()
        self._out_msg.trace_id = self.trace_id_base + self._counter
        self._out_msg.chain_id = upstream.chain_id
        self._out_msg.step_index = upstream.step_index + 1
        self._out_msg.upstream_trace_id = upstream.trace_id
        self._out_msg.upstream_header = upstream.header
        self._out_msg.payload = build_relay_payload(upstream.payload, self.payload_size)

        wall_publish_ns = time.monotonic_ns()

        lttng_trace_annotation(
            LOGGER_NAME_RELAY,
            EVT_MESSAGE_LINK_PUBLISH,
            self._armed_callback_guid,
            id(self._out_msg),
        )

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
        self._csv_file.flush()
        self._csv_file.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = MockTraceIndirectRelayNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
