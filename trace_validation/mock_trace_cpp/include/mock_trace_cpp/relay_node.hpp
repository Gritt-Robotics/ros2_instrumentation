// Copyright 2026 Gritt Robotics Inc.

#pragma once

#include <fstream>
#include <memory>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp/serialization.hpp>
#include <string>
#include <trace_msgs/msg/trace_chain_message.hpp>

#include "mock_trace_cpp/message_dispatch.hpp"

namespace mock_trace_cpp {

// Relays a TraceChainMessage received on topic_in onto topic_out, deriving the outgoing
// message from the received one, and records a combined ground-truth CSV row (recv +
// publish timing) per message.
//
// Parameters:
//   topic_in (string): topic to subscribe to for TraceChainMessage.
//   topic_out (string): topic to republish the derived TraceChainMessage on.
//   payload_size (int): length of the outgoing payload. Must be >= 0.
//   trace_id_base (uint64_t): starting value for this step's monotonically increasing trace_id.
//   output_csv (string): filesystem path to write the combined recv+publish CSV to.
//   qos_depth (int): pub/sub QoS history depth. Must be > 0.
//   transport (string): "typed" or "serialized"; selects the pub/sub transport.
class MockTraceRelayNode : public rclcpp::Node {
public:
    explicit MockTraceRelayNode(const rclcpp::NodeOptions &options);
    ~MockTraceRelayNode() override;

private:
    void on_message(const trace_msgs::msg::TraceChainMessage::SharedPtr msg);
    void on_message_raw(std::shared_ptr<rclcpp::SerializedMessage> msg);
    void relay(const trace_msgs::msg::TraceChainMessage &upstream, int64_t wall_recv_ns);

    TraceTransport transport_;

    // Typed-transport pub/sub: constructed when transport_ == kTyped.
    rclcpp::Publisher<trace_msgs::msg::TraceChainMessage>::SharedPtr pub_;
    rclcpp::Subscription<trace_msgs::msg::TraceChainMessage>::SharedPtr sub_;

    // Serialized-transport pub/sub: constructed when transport_ == kSerialized.
    rclcpp::GenericPublisher::SharedPtr generic_pub_;
    rclcpp::GenericSubscription::SharedPtr generic_sub_;

    // Serializer used for both directions on the serialized-transport path (decode
    // incoming, encode outgoing); unused (but always constructed) on the typed path.
    rclcpp::Serialization<trace_msgs::msg::TraceChainMessage> serializer_;
    rclcpp::SerializedMessage serialized_scratch_;
    trace_msgs::msg::TraceChainMessage decode_scratch_;

    std::ofstream csv_;

    std::string topic_in_;
    std::string topic_out_;
    int payload_size_;
    uint64_t trace_id_base_;
    std::string output_csv_;
    int qos_depth_;

    uint64_t counter_{0};
    uint64_t log_counter_{0};

    // Reused across relays, mutated in place per receive.
    trace_msgs::msg::TraceChainMessage out_msg_;

    static constexpr uint64_t LOG_EVERY_N = 100;
    static constexpr uint64_t CSV_FLUSH_EVERY_N = 50;
};

}  // namespace mock_trace_cpp
