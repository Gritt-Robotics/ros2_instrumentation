# Copyright 2026 Gritt Robotics Inc.

from synthetic_events import (
    callback_register_event,
    node_init_event,
    rcl_take_event,
    rclcpp_subscription_init_event,
    rclcpp_take_event,
    rmw_publisher_init_event,
    rmw_subscription_init_event,
    subscription_callback_added_event,
    timer_callback_added_event,
    timer_init_event,
    timer_link_node_event,
)
from trace_instrumentation.trace_analysis.event_fields import (
    FIELD_CALLBACK,
    FIELD_GID,
    FIELD_MESSAGE,
    FIELD_NAMESPACE,
    FIELD_NODE_HANDLE,
    FIELD_NODE_NAME,
    FIELD_PERIOD,
    FIELD_RMW_PUBLISHER_HANDLE,
    FIELD_SUBSCRIPTION,
    FIELD_SUBSCRIPTION_HANDLE,
    FIELD_SYMBOL,
    FIELD_TIMER_HANDLE,
)
from trace_instrumentation.trace_analysis.event_names import (
    EVT_RCL_NODE_INIT,
    EVT_RCL_TAKE,
    EVT_RCL_TIMER_INIT,
    EVT_RCLCPP_SUBSCRIPTION_CALLBACK_ADDED,
    EVT_RCLCPP_TAKE,
    EVT_RMW_PUBLISHER_INIT,
)

TS_NS = 100
VPID = 100
VTID = 10
NODE_HANDLE = 900
NODE_NAME = "producer"
NAMESPACE = "/"
RMW_NODE_HANDLE = 901
PUB_HANDLE = 1
SUB_HANDLE = 2
TIMER_HANDLE = 3
RMW_PUB_HANDLE = 101
RMW_SUB_HANDLE = 102
RCLCPP_SUB_PTR = 201
CALLBACK_PTR = 501
PERIOD_NS = 100_000_000
MESSAGE_PTR = 333
GID = [7, 8, 9]
SYMBOL = "Producer::on_image"


def test_node_init_event_carries_name_namespace_and_handle():
    event = node_init_event(
        TS_NS, VPID, VTID, NODE_HANDLE, NODE_NAME, NAMESPACE, RMW_NODE_HANDLE
    )
    assert event.name == EVT_RCL_NODE_INIT
    assert event.fields[FIELD_NODE_HANDLE] == NODE_HANDLE
    assert event.fields[FIELD_NODE_NAME] == NODE_NAME
    assert event.fields[FIELD_NAMESPACE] == NAMESPACE


def test_rmw_publisher_init_event_carries_gid():
    event = rmw_publisher_init_event(TS_NS, VPID, VTID, RMW_PUB_HANDLE, GID)
    assert event.name == EVT_RMW_PUBLISHER_INIT
    assert event.fields[FIELD_RMW_PUBLISHER_HANDLE] == RMW_PUB_HANDLE
    assert event.fields[FIELD_GID] == GID


def test_rmw_subscription_init_event_carries_gid():
    event = rmw_subscription_init_event(TS_NS, VPID, VTID, RMW_SUB_HANDLE, GID)
    assert event.fields[FIELD_GID] == GID


def test_timer_init_event_carries_period():
    init = timer_init_event(TS_NS, VPID, VTID, TIMER_HANDLE, PERIOD_NS)
    assert init.name == EVT_RCL_TIMER_INIT
    assert init.fields[FIELD_PERIOD] == PERIOD_NS


def test_timer_link_node_event_carries_node_handle():
    link = timer_link_node_event(TS_NS, VPID, VTID, TIMER_HANDLE, NODE_HANDLE)
    assert link.fields[FIELD_NODE_HANDLE] == NODE_HANDLE


def test_timer_callback_added_event_carries_callback():
    added = timer_callback_added_event(TS_NS, VPID, VTID, TIMER_HANDLE, CALLBACK_PTR)
    assert added.fields[FIELD_TIMER_HANDLE] == TIMER_HANDLE
    assert added.fields[FIELD_CALLBACK] == CALLBACK_PTR


def test_rclcpp_subscription_init_event_carries_handle():
    rclcpp_init = rclcpp_subscription_init_event(
        TS_NS, VPID, VTID, RCLCPP_SUB_PTR, SUB_HANDLE
    )
    assert rclcpp_init.fields[FIELD_SUBSCRIPTION] == RCLCPP_SUB_PTR
    assert rclcpp_init.fields[FIELD_SUBSCRIPTION_HANDLE] == SUB_HANDLE


def test_subscription_callback_added_event_carries_callback():
    added = subscription_callback_added_event(
        TS_NS, VPID, VTID, RCLCPP_SUB_PTR, CALLBACK_PTR
    )
    assert added.name == EVT_RCLCPP_SUBSCRIPTION_CALLBACK_ADDED
    assert added.fields[FIELD_CALLBACK] == CALLBACK_PTR


def test_callback_register_event_carries_symbol():
    event = callback_register_event(TS_NS, VPID, VTID, CALLBACK_PTR, SYMBOL)
    assert event.fields[FIELD_SYMBOL] == SYMBOL


def test_take_chain_tail_events_share_the_message_key():
    rcl = rcl_take_event(TS_NS, VPID, VTID, MESSAGE_PTR)
    rclcpp = rclcpp_take_event(TS_NS + 10, VPID, VTID, MESSAGE_PTR)
    assert rcl.name == EVT_RCL_TAKE
    assert rclcpp.name == EVT_RCLCPP_TAKE
    assert rcl.fields[FIELD_MESSAGE] == rclcpp.fields[FIELD_MESSAGE] == MESSAGE_PTR
