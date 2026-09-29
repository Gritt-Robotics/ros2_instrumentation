# Copyright 2026 Gritt Robotics Inc.

"""
event_fields.py
===============

ros2_tracing / lttng_python tracepoint field-key names, as they appear as keys in
Event.fields. Shared between the reader side (analysis.py) and the synthetic-event
test fixtures (test/synthetic_events.py) so a typo on either side is a NameError at
import time rather than a silent KeyError/missing field at test run time.
"""

FIELD_NODE_HANDLE = "node_handle"
FIELD_NODE_NAME = "node_name"
FIELD_NAMESPACE = "namespace"
FIELD_RMW_HANDLE = "rmw_handle"

FIELD_PUBLISHER_HANDLE = "publisher_handle"
FIELD_RMW_PUBLISHER_HANDLE = "rmw_publisher_handle"
FIELD_TOPIC_NAME = "topic_name"
FIELD_QUEUE_DEPTH = "queue_depth"

FIELD_SUBSCRIPTION_HANDLE = "subscription_handle"
FIELD_RMW_SUBSCRIPTION_HANDLE = "rmw_subscription_handle"
FIELD_SUBSCRIPTION = "subscription"  # rclcpp-layer subscription pointer

FIELD_TIMER_HANDLE = "timer_handle"
FIELD_PERIOD = "period"

FIELD_CALLBACK = "callback"
FIELD_SYMBOL = "symbol"

FIELD_SERVICE_HANDLE = "service_handle"
FIELD_SERVICE_NAME = "service_name"
FIELD_RMW_SERVICE_HANDLE = "rmw_service_handle"

FIELD_CLIENT_HANDLE = "client_handle"
FIELD_RMW_CLIENT_HANDLE = "rmw_client_handle"

FIELD_CONTEXT_HANDLE = "context_handle"
FIELD_VERSION = "version"

FIELD_STATE_MACHINE = "state_machine"
FIELD_START_LABEL = "start_label"
FIELD_GOAL_LABEL = "goal_label"

FIELD_MSG = "msg"
FIELD_LOGGER_NAME = "logger_name"
FIELD_THREAD = "thread"
FIELD_THREAD_NAME = "threadName"

FIELD_IS_INTRA_PROCESS = "is_intra_process"

FIELD_MESSAGE = "message"
FIELD_TIMESTAMP = "timestamp"
FIELD_SOURCE_TIMESTAMP = "source_timestamp"
FIELD_TAKEN = "taken"

FIELD_GID = "gid"
FIELD_HANDLE = "handle"
