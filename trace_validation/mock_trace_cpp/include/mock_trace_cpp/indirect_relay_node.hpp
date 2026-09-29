// Copyright 2026 Gritt Robotics Inc.

#pragma once

#include <cstdint>
#include <fstream>
#include <memory>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp/serialization.hpp>
#include <string>
#include <trace_msgs/msg/trace_chain_message.hpp>

#include "mock_trace_cpp/message_dispatch.hpp"

namespace mock_trace_cpp {

// Receives a TraceChainMessage on topic_in, stores the latest upstream sample, and arms
// a one-shot guard. Publishing is decoupled into a fixed-rate timer callback that relays
// only when the guard is armed, clearing it as it does.
//
// Parameters:
//   topic_in (string): topic to subscribe to for TraceChainMessage.
//   topic_out (string): topic to republish the derived TraceChainMessage on.
//   payload_size (int): length of the outgoing payload. Must be >= 0.
//   trace_id_base (uint64_t): starting value for this step's monotonically increasing trace_id.
//   output_csv (string): filesystem path to write the combined recv+publish CSV to.
//   qos_depth (int): pub/sub QoS history depth. Must be > 0.
//   transport (string): "typed" or "serialized"; selects the pub/sub transport.
//   publish_hz (double): fixed publish-timer frequency in Hz. Must be > 0.
class MockTraceIndirectRelayNode : public rclcpp::Node {
public:
    explicit MockTraceIndirectRelayNode(const rclcpp::NodeOptions &options);
    ~MockTraceIndirectRelayNode() override;

private:
    void on_message(const trace_msgs::msg::TraceChainMessage::SharedPtr msg);
    void on_message_raw(std::shared_ptr<rclcpp::SerializedMessage> msg);
    void on_upstream_message(const trace_msgs::msg::TraceChainMessage &upstream,
                             int64_t wall_recv_ns);
    void on_publish_timer();
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
    double publish_hz_;

    rclcpp::TimerBase::SharedPtr publish_timer_;
    int64_t callback_guid_{0};
    int64_t armed_callback_guid_{0};

    uint64_t counter_{0};
    uint64_t log_counter_{0};

    trace_msgs::msg::TraceChainMessage latest_upstream_;
    int64_t latest_wall_recv_ns_{0};
    // Set by every upstream take, cleared by the publish that consumes it. Doubles as
    // the "have we ever received anything" guard, since it can only be true after
    // latest_upstream_ has been populated.
    bool armed_{false};

    // Reused across relays, mutated in place per receive.
    trace_msgs::msg::TraceChainMessage out_msg_;

    static constexpr uint64_t LOG_EVERY_N = 100;
    static constexpr uint64_t CSV_FLUSH_EVERY_N = 50;
};

}  // namespace mock_trace_cpp
