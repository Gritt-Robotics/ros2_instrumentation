// Copyright 2026 Gritt Robotics Inc.

#pragma once

#include <fstream>
#include <memory>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp/serialization.hpp>
#include <string>
#include <trace_msgs/msg/trace_chain_message.hpp>
#include <trace_msgs/msg/trace_message.hpp>
#include <trace_msgs/msg/trace_message_fixed.hpp>

#include "mock_trace_cpp/message_dispatch.hpp"

namespace mock_trace_cpp {

// Subscribes to TraceMessage on a configurable topic and records a CSV row
// (trace_id, header stamp, wall-clock receive time) for every message received,
// for use as the receiver side of trace-analyzer validation.
//
// Parameters:
//   topic (string): topic to subscribe to for TraceMessage.
//   output_csv (string): filesystem path to write the receive-side CSV to.
//   qos_depth (int): subscription QoS history depth. Must be > 0.
//   message_type (string): "variable", "fixed", or "chain"; selects TraceMessage vs
//                          TraceMessageFixed vs TraceChainMessage (terminal chain sink).
//   transport (string): "typed" or "serialized"; selects the pub/sub transport.
class MockTraceSubscriberNode : public rclcpp::Node {
public:
    explicit MockTraceSubscriberNode(const rclcpp::NodeOptions &options);
    ~MockTraceSubscriberNode() override;

private:
    void on_message_variable(const trace_msgs::msg::TraceMessage::SharedPtr msg);
    void on_message_fixed(const trace_msgs::msg::TraceMessageFixed::SharedPtr msg);
    void on_message_chain(const trace_msgs::msg::TraceChainMessage::SharedPtr msg);
    void on_message_serialized(std::shared_ptr<rclcpp::SerializedMessage> msg);
    void log_row(uint64_t trace_id, int64_t header_stamp_ns, int64_t wall_recv_ns);
    void log_row_chain(uint64_t trace_id, int64_t header_stamp_ns, int64_t wall_recv_ns,
                       uint64_t chain_id, uint32_t step_index, uint64_t upstream_trace_id);

    rclcpp::Subscription<trace_msgs::msg::TraceMessage>::SharedPtr sub_variable_;
    rclcpp::Subscription<trace_msgs::msg::TraceMessageFixed>::SharedPtr sub_fixed_;
    rclcpp::Subscription<trace_msgs::msg::TraceChainMessage>::SharedPtr sub_chain_;

    // Serialized-transport subscription: constructed when transport_ == kSerialized, with
    // a type string matching message_type_.
    rclcpp::GenericSubscription::SharedPtr generic_sub_;

    // Deserializers used to decode incoming SerializedMessages on the serialized-transport
    // path. Stateless and cheap, so all are always constructed; only the one matching
    // message_type_ is used.
    rclcpp::Serialization<trace_msgs::msg::TraceMessage> serializer_variable_;
    rclcpp::Serialization<trace_msgs::msg::TraceMessageFixed> serializer_fixed_;
    rclcpp::Serialization<trace_msgs::msg::TraceChainMessage> serializer_chain_;

    // Reused decode scratch for the serialized-transport path. Kept as node members
    // (rather than stack locals in the callback) to avoid constructing/zero-filling a
    // fresh 4MB TraceMessageFixed on every callback, which risks a stack overflow on
    // executor threads with a default ~8MB stack.
    trace_msgs::msg::TraceMessage decode_variable_;
    trace_msgs::msg::TraceMessageFixed decode_fixed_;
    trace_msgs::msg::TraceChainMessage decode_chain_;

    std::ofstream csv_;
    uint64_t recv_counter_{0};

    std::string topic_;
    std::string output_csv_;
    int qos_depth_;
    TraceMessageType message_type_;
    TraceTransport transport_;

    static constexpr uint64_t CSV_FLUSH_EVERY_N = 50;
};

}  // namespace mock_trace_cpp
