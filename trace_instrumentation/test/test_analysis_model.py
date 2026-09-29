# Copyright 2026 Gritt Robotics Inc.

from typing import List

from synthetic_events import (
    callback_end_event,
    callback_start_event,
    client_init_event,
    lifecycle_state_machine_init_event,
    lifecycle_transition_event,
    make_event,
    publish_events,
    publisher_init_event,
    python_zone_event,
    rcl_init_event,
    rmw_take_event,
    service_init_event,
    subscription_init_event,
)
from trace_instrumentation.trace_analysis.analysis import (
    REGISTRY_KEY_NAME,
    REGISTRY_KEY_NODE,
    TraceAnalysis,
)
from trace_instrumentation.trace_analysis.ctf.reader import Event, Metadata
from trace_instrumentation.trace_analysis.event_fields import (
    FIELD_GID,
    FIELD_HANDLE,
    FIELD_MESSAGE,
    FIELD_NAMESPACE,
    FIELD_NODE_HANDLE,
    FIELD_NODE_NAME,
    FIELD_PUBLISHER_HANDLE,
    FIELD_RMW_HANDLE,
    FIELD_RMW_PUBLISHER_HANDLE,
    FIELD_RMW_SUBSCRIPTION_HANDLE,
)
from trace_instrumentation.trace_analysis.event_names import (
    EVT_RCL_NODE_INIT,
    EVT_RCLCPP_EXECUTOR_EXECUTE,
    EVT_RCLCPP_EXECUTOR_GET_NEXT_READY,
    EVT_RCLCPP_EXECUTOR_WAIT_FOR_WORK,
    EVT_RCLCPP_INTRA_PUBLISH,
    EVT_RMW_PUBLISHER_INIT,
    EVT_RMW_SUBSCRIPTION_INIT,
)

PRODUCER_VPID = 200
PRODUCER_VTID = 20
PRODUCER_NODE_HANDLE = 900
PRODUCER_NODE_NAME = "producer"

RELAY_VPID = 100
RELAY_VTID = 10
RELAY_NODE_HANDLE = 910
RELAY_NODE_NAME = "relay"

STEP0_TOPIC = "/step0"
ON_MESSAGE_ZONE = "_on_message"
CALLBACK_PTR = 0xCAFE
ROOT_NAMESPACE = "/"
NODE_RMW_HANDLE = 0

# One-step pipeline handles. rcl and rmw handles are distinct values so a test can
# never pass by reading the wrong layer's handle.
PUBLISHER_HANDLE = 1
PRODUCER_RMW_PUB_HANDLE = 901
RELAY_SUB_HANDLE = 10
RELAY_RMW_SUB_HANDLE = 11

# Message-buffer pointers and the DDS source timestamp shared by the publish/take pair.
PRODUCER_MSG = 100
RELAY_TAKEN_MSG = 200
INTRA_MSG = 1
SOURCE_TS = 5000

# Timeline of the one-step scenario, in trace order.
INIT_TS_NS = 0
PUBLISH_TS_NS = 1000
ZONE_BEGIN_TS_NS = 2000
TAKE_TS_NS = 2100
ZONE_END_TS_NS = 2400

# Standalone callback-duration scenario.
CB_START_TS_NS = 1_000
CB_END_TS_NS = 1_400

# DDS GIDs, which rmw_*_init reports as a byte array.
PRODUCER_PUB_GID = [1, 2, 3, 4]
RELAY_SUB_GID = [5, 6, 7, 8]

# Executor spin timeline: wait_for_work -> get_next_ready -> execute.
EXEC_WAIT_TS_NS = 3_000
EXEC_READY_TS_NS = 3_500
EXEC_EXECUTE_TS_NS = 3_700

# Handles that no init event registers, to exercise the unknown-handle fallbacks.
UNKNOWN_NODE_HANDLE = 0xDEADBEEF
UNKNOWN_CALLBACK_PTR = 0xABCD

SERVICE_HANDLE = 20
CLIENT_HANDLE = 21
RELAY_RMW_SERVICE_HANDLE = 22
PRODUCER_RMW_CLIENT_HANDLE = 23
SERVICE_NAME = "/example_service"
CONTEXT_HANDLE = 30
RCL_VERSION = "1.2.3"
STATE_MACHINE_HANDLE = 40
TRANSITION_TS_NS = 100
LIFECYCLE_START_LABEL = "unconfigured"
LIFECYCLE_GOAL_LABEL = "inactive"


def _node_init_event(
    ts_ns: int, vpid: int, vtid: int, node_handle: int, name: str
) -> Event:
    """
    Build an `ros2:rcl_node_init` event for a node in the root namespace.

    Args:
        ts_ns (int): Event timestamp in nanoseconds.
        vpid (int): Process id the node is created in.
        vtid (int): Thread id the node is created on.
        node_handle (int): The node's `rcl_node_t` handle.
        name (str): The node's name, without a namespace.

    Returns:
        Event: The synthetic node-init event.
    """
    return make_event(
        EVT_RCL_NODE_INIT,
        ts_ns,
        vpid,
        vtid,
        {
            FIELD_NODE_HANDLE: node_handle,
            FIELD_NODE_NAME: name,
            FIELD_NAMESPACE: ROOT_NAMESPACE,
            FIELD_RMW_HANDLE: NODE_RMW_HANDLE,
        },
    )


def _one_step_events() -> List[Event]:
    """
    A producer -> relay pipeline: one publish, one take, one Python zone wrapping
    the take, with both nodes registered via rcl_node_init.

    Returns:
        List[Event]: The synthetic events, in trace order.
    """
    events = [
        _node_init_event(
            INIT_TS_NS,
            PRODUCER_VPID,
            PRODUCER_VTID,
            PRODUCER_NODE_HANDLE,
            PRODUCER_NODE_NAME,
        ),
        _node_init_event(
            INIT_TS_NS, RELAY_VPID, RELAY_VTID, RELAY_NODE_HANDLE, RELAY_NODE_NAME
        ),
        publisher_init_event(
            INIT_TS_NS,
            PRODUCER_VPID,
            PRODUCER_VTID,
            PUBLISHER_HANDLE,
            PRODUCER_NODE_HANDLE,
            PRODUCER_RMW_PUB_HANDLE,
            STEP0_TOPIC,
        ),
        subscription_init_event(
            INIT_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            RELAY_SUB_HANDLE,
            RELAY_NODE_HANDLE,
            RELAY_RMW_SUB_HANDLE,
            STEP0_TOPIC,
        ),
    ]
    events += publish_events(
        PUBLISH_TS_NS,
        PRODUCER_VPID,
        PRODUCER_VTID,
        PRODUCER_MSG,
        PUBLISHER_HANDLE,
        SOURCE_TS,
    )
    events.append(
        python_zone_event(
            ZONE_BEGIN_TS_NS, RELAY_VPID, RELAY_VTID, ON_MESSAGE_ZONE, "begin"
        )
    )
    events.append(
        rmw_take_event(
            TAKE_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            RELAY_TAKEN_MSG,
            RELAY_RMW_SUB_HANDLE,
            SOURCE_TS,
        )
    )
    events.append(
        python_zone_event(
            ZONE_END_TS_NS, RELAY_VPID, RELAY_VTID, ON_MESSAGE_ZONE, "end"
        )
    )
    return events


def _build() -> TraceAnalysis:
    """
    Build a TraceAnalysis over the one-step pipeline events.

    Returns:
        TraceAnalysis: The built analysis, ready to query.
    """
    return TraceAnalysis(Metadata(), _one_step_events()).build()


def test_node_label_renders_namespaced_node_name():
    analysis = _build()
    assert analysis.node_label(PRODUCER_NODE_HANDLE) == "/producer"
    assert analysis.node_label(RELAY_NODE_HANDLE) == "/relay"


def test_node_label_falls_back_to_placeholder_for_unknown_handle():
    analysis = _build()
    assert "unknown node" in analysis.node_label(UNKNOWN_NODE_HANDLE)


def test_callback_label_falls_back_to_hex_pointer_when_no_callback_seen():
    analysis = _build()
    assert analysis.callback_label(UNKNOWN_CALLBACK_PTR) == hex(UNKNOWN_CALLBACK_PTR)


def test_python_zone_is_recorded_with_duration():
    analysis = _build()
    assert len(analysis.python_zones) == 1
    zone = analysis.python_zones[0]
    assert zone.zone_name == ON_MESSAGE_ZONE
    assert zone.duration_ns == ZONE_END_TS_NS - ZONE_BEGIN_TS_NS


def test_callback_duration_uses_ts_ns_difference():
    events = [
        callback_start_event(CB_START_TS_NS, RELAY_VPID, RELAY_VTID, CALLBACK_PTR),
        callback_end_event(CB_END_TS_NS, RELAY_VPID, RELAY_VTID, CALLBACK_PTR),
    ]
    analysis = TraceAnalysis(Metadata(), events).build()
    assert analysis.cb_durations[CALLBACK_PTR] == [CB_END_TS_NS - CB_START_TS_NS]


def test_publish_and_take_counts_are_recorded():
    analysis = _build()
    assert analysis.rcl_publish[PUBLISHER_HANDLE] == 1
    assert analysis.rmw_publish_count == 1
    assert analysis.rmw_take[RELAY_RMW_SUB_HANDLE] == 1
    assert analysis.rmw_take_empty[RELAY_RMW_SUB_HANDLE] == 0


def test_intra_publish_is_recorded_separately_from_rcl_publish():
    events = [
        make_event(
            EVT_RCLCPP_INTRA_PUBLISH,
            INIT_TS_NS,
            PRODUCER_VPID,
            PRODUCER_VTID,
            {FIELD_MESSAGE: INTRA_MSG, FIELD_PUBLISHER_HANDLE: PUBLISHER_HANDLE},
        ),
    ]
    analysis = TraceAnalysis(Metadata(), events).build()
    assert analysis.intra_publish[PUBLISHER_HANDLE] == 1
    assert analysis.rcl_publish[PUBLISHER_HANDLE] == 0


def test_rmw_take_empty_is_recorded_when_take_fails():
    events = [
        rmw_take_event(
            INIT_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            RELAY_TAKEN_MSG,
            RELAY_RMW_SUB_HANDLE,
            SOURCE_TS,
            taken=0,
        )
    ]
    analysis = TraceAnalysis(Metadata(), events).build()
    assert analysis.rmw_take[RELAY_RMW_SUB_HANDLE] == 1
    assert analysis.rmw_take_empty[RELAY_RMW_SUB_HANDLE] == 1


def test_rmw_endpoint_init_records_gids_by_rmw_handle():
    events = [
        make_event(
            EVT_RMW_PUBLISHER_INIT,
            INIT_TS_NS,
            PRODUCER_VPID,
            PRODUCER_VTID,
            {
                FIELD_RMW_PUBLISHER_HANDLE: PRODUCER_RMW_PUB_HANDLE,
                FIELD_GID: PRODUCER_PUB_GID,
            },
        ),
        make_event(
            EVT_RMW_SUBSCRIPTION_INIT,
            INIT_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            {
                FIELD_RMW_SUBSCRIPTION_HANDLE: RELAY_RMW_SUB_HANDLE,
                FIELD_GID: RELAY_SUB_GID,
            },
        ),
    ]
    analysis = TraceAnalysis(Metadata(), events).build()
    assert analysis.publisher_gids == {PRODUCER_RMW_PUB_HANDLE: PRODUCER_PUB_GID}
    assert analysis.subscription_gids == {RELAY_RMW_SUB_HANDLE: RELAY_SUB_GID}


def test_executor_spin_records_wait_and_dispatch_durations():
    events = [
        make_event(
            EVT_RCLCPP_EXECUTOR_WAIT_FOR_WORK,
            EXEC_WAIT_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            {},
        ),
        make_event(
            EVT_RCLCPP_EXECUTOR_GET_NEXT_READY,
            EXEC_READY_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            {},
        ),
        make_event(
            EVT_RCLCPP_EXECUTOR_EXECUTE,
            EXEC_EXECUTE_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            {FIELD_HANDLE: RELAY_SUB_HANDLE},
        ),
    ]
    analysis = TraceAnalysis(Metadata(), events).build()
    assert analysis.executor_execute_counts[RELAY_SUB_HANDLE] == 1
    assert analysis.executor_wait_durations[(RELAY_VPID, RELAY_VTID)] == [
        EXEC_READY_TS_NS - EXEC_WAIT_TS_NS
    ]
    assert analysis.executor_dispatch_durations[RELAY_SUB_HANDLE] == [
        EXEC_EXECUTE_TS_NS - EXEC_READY_TS_NS
    ]


def test_executor_spin_without_wait_records_no_wait_duration():
    events = [
        make_event(
            EVT_RCLCPP_EXECUTOR_GET_NEXT_READY,
            EXEC_READY_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            {},
        ),
        make_event(
            EVT_RCLCPP_EXECUTOR_EXECUTE,
            EXEC_EXECUTE_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            {FIELD_HANDLE: RELAY_SUB_HANDLE},
        ),
    ]
    analysis = TraceAnalysis(Metadata(), events).build()
    assert len(analysis.executor_wait_durations) == 0
    assert analysis.executor_dispatch_durations[RELAY_SUB_HANDLE] == [
        EXEC_EXECUTE_TS_NS - EXEC_READY_TS_NS
    ]


def test_service_and_client_are_registered():
    events = [
        service_init_event(
            INIT_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            SERVICE_HANDLE,
            RELAY_NODE_HANDLE,
            RELAY_RMW_SERVICE_HANDLE,
            SERVICE_NAME,
        ),
        client_init_event(
            INIT_TS_NS,
            PRODUCER_VPID,
            PRODUCER_VTID,
            CLIENT_HANDLE,
            PRODUCER_NODE_HANDLE,
            PRODUCER_RMW_CLIENT_HANDLE,
            SERVICE_NAME,
        ),
    ]
    analysis = TraceAnalysis(Metadata(), events).build()
    assert analysis.services[SERVICE_HANDLE][REGISTRY_KEY_NAME] == SERVICE_NAME
    assert analysis.services[SERVICE_HANDLE][REGISTRY_KEY_NODE] == RELAY_NODE_HANDLE
    assert analysis.clients[CLIENT_HANDLE][REGISTRY_KEY_NAME] == SERVICE_NAME
    assert analysis.clients[CLIENT_HANDLE][REGISTRY_KEY_NODE] == PRODUCER_NODE_HANDLE


def test_context_version_is_recorded():
    events = [
        rcl_init_event(
            INIT_TS_NS, PRODUCER_VPID, PRODUCER_VTID, CONTEXT_HANDLE, RCL_VERSION
        )
    ]
    analysis = TraceAnalysis(Metadata(), events).build()
    assert analysis.contexts[CONTEXT_HANDLE] == RCL_VERSION


def test_lifecycle_transition_is_recorded():
    events = [
        _node_init_event(
            INIT_TS_NS, RELAY_VPID, RELAY_VTID, RELAY_NODE_HANDLE, RELAY_NODE_NAME
        ),
        lifecycle_state_machine_init_event(
            INIT_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            STATE_MACHINE_HANDLE,
            RELAY_NODE_HANDLE,
        ),
        lifecycle_transition_event(
            TRANSITION_TS_NS,
            RELAY_VPID,
            RELAY_VTID,
            STATE_MACHINE_HANDLE,
            LIFECYCLE_START_LABEL,
            LIFECYCLE_GOAL_LABEL,
        ),
    ]
    analysis = TraceAnalysis(Metadata(), events).build()
    assert analysis.lifecycle_sms[STATE_MACHINE_HANDLE] == RELAY_NODE_HANDLE
    assert len(analysis.lifecycle_transitions) == 1
    transition = analysis.lifecycle_transitions[0]
    assert transition.start_label == LIFECYCLE_START_LABEL
    assert transition.goal_label == LIFECYCLE_GOAL_LABEL
