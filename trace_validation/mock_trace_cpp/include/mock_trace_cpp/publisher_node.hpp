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

// Publishes TraceMessage at a fixed rate on a configurable topic and records a
// ground-truth CSV row (trace_id, header stamp, wall-clock publish time) for every
// message, for use as the source-of-truth side of trace-analyzer validation.
//
// Parameters:
//   target_hz (double): publish rate in Hz. Must be > 0.
//   topic (string): topic to publish TraceMessage on.
//   payload_size (int): number of zero-filled int32 elements in each message's
//     payload (see trace_msgs/msg/TraceMessage.msg's int32[] payload). Must be >= 0.
//   trace_id_base (uint64_t): starting value for the monotonically increasing trace_id.
//   output_csv (string): filesystem path to write the ground-truth CSV to.
//   qos_depth (int): publisher QoS history depth. Must be > 0.
//   message_type (string): "variable", "fixed", or "chain"; selects TraceMessage vs
//                          TraceMessageFixed vs TraceChainMessage (step 0 of a chain).
//   transport (string): "typed" or "serialized"; selects the pub/sub transport.
class MockTracePublisherNode : public rclcpp::Node {
public:
    explicit MockTracePublisherNode(const rclcpp::NodeOptions &options);
    ~MockTracePublisherNode() override;

private:
    void tick();

    rclcpp::Publisher<trace_msgs::msg::TraceMessage>::SharedPtr pub_variable_;
    rclcpp::Publisher<trace_msgs::msg::TraceMessageFixed>::SharedPtr pub_fixed_;
    rclcpp::Publisher<trace_msgs::msg::TraceChainMessage>::SharedPtr pub_chain_;

    // Serialized-transport publisher: constructed when transport_ == kSerialized, with a
    // type string matching message_type_.
    rclcpp::GenericPublisher::SharedPtr generic_pub_;

    // Serializers used to encode msg_variable_/msg_fixed_/msg_chain_ before a
    // serialized-transport publish. Stateless and cheap, so all are always constructed;
    // only the one matching message_type_ is used.
    rclcpp::Serialization<trace_msgs::msg::TraceMessage> serializer_variable_;
    rclcpp::Serialization<trace_msgs::msg::TraceMessageFixed> serializer_fixed_;
    rclcpp::Serialization<trace_msgs::msg::TraceChainMessage> serializer_chain_;

    // Reused scratch buffer for serialize_message() to avoid a per-tick allocation,
    // especially important for the 4MB fixed payload.
    rclcpp::SerializedMessage serialized_scratch_;

    rclcpp::TimerBase::SharedPtr timer_;

    std::ofstream csv_;

    double target_hz_;
    std::string topic_;
    int payload_size_;
    uint64_t trace_id_base_;
    std::string output_csv_;
    int qos_depth_;
    TraceMessageType message_type_;
    TraceTransport transport_;

    uint64_t counter_{0};
    uint64_t log_counter_{0};

    // Reused across ticks: payload is allocated once (sized to payload_size_ for
    // msg_variable_/msg_chain_; always 1,000,000 for msg_fixed_) since it never changes
    // size; tick() only overwrites header/trace_id/chain fields in place. Only the member
    // matching message_type_ is populated/published.
    trace_msgs::msg::TraceMessage msg_variable_;
    trace_msgs::msg::TraceMessageFixed msg_fixed_;
    trace_msgs::msg::TraceChainMessage msg_chain_;

    static constexpr uint64_t LOG_EVERY_N = 100;
    static constexpr uint64_t CSV_FLUSH_EVERY_N = 100;
};

}  // namespace mock_trace_cpp
