// Copyright 2026 Gritt Robotics Inc.

#include "mock_trace_cpp/indirect_relay_node.hpp"

#include <tracetools/tracetools.h>

#include <chrono>
#include <filesystem>
#include <std_msgs/msg/header.hpp>
#include <stdexcept>
#include <string>

#include "mock_trace_cpp/parameter_names.hpp"
#include "mock_trace_cpp/time_utils.hpp"

namespace mock_trace_cpp {

MockTraceIndirectRelayNode::MockTraceIndirectRelayNode(const rclcpp::NodeOptions &options)
    : Node("mock_indirect_relay_node", options) {
    topic_in_ = this->get_parameter(kParamTopicIn).as_string();
    topic_out_ = this->get_parameter(kParamTopicOut).as_string();
    payload_size_ = static_cast<int>(this->get_parameter(kParamPayloadSize).as_int());
    trace_id_base_ = static_cast<uint64_t>(this->get_parameter(kParamTraceIdBase).as_int());
    output_csv_ = this->get_parameter(kParamOutputCsv).as_string();
    qos_depth_ = static_cast<int>(this->get_parameter(kParamQosDepth).as_int());
    publish_hz_ = this->get_parameter(kParamPublishHz).as_double();
    const std::string transport_str = this->get_parameter(kParamTransport).as_string();
    transport_ = parse_transport(transport_str);

    if (payload_size_ < 0) {
        throw std::runtime_error("payload_size must be >= 0, got " + std::to_string(payload_size_));
    }
    if (qos_depth_ <= 0) {
        throw std::runtime_error("qos_depth must be > 0, got " + std::to_string(qos_depth_));
    }
    if (publish_hz_ <= 0.0) {
        throw std::runtime_error("publish_hz must be > 0, got " + std::to_string(publish_hz_));
    }

    const std::filesystem::path csv_path(output_csv_);
    if (csv_path.has_parent_path()) {
        std::filesystem::create_directories(csv_path.parent_path());
    }

    csv_.open(output_csv_, std::ios::out | std::ios::trunc);
    if (!csv_.is_open()) {
        throw std::runtime_error("Failed to open output_csv for writing: " + output_csv_);
    }
    csv_ << "trace_id,header_stamp_ns,wall_publish_ns,chain_id,step_index,upstream_trace_id,"
            "upstream_header_stamp_ns,wall_recv_ns,topic_in,topic_out,node,payload_len_in,"
            "payload_len_out\n";

    out_msg_.header.frame_id = "";

    rclcpp::QoS qos(qos_depth_);
    if (transport_ == TraceTransport::kTyped) {
        pub_ = this->create_publisher<trace_msgs::msg::TraceChainMessage>(topic_out_, qos);
        sub_ = this->create_subscription<trace_msgs::msg::TraceChainMessage>(
          topic_in_, qos,
          [this](const trace_msgs::msg::TraceChainMessage::SharedPtr msg) { on_message(msg); });
    } else {
        generic_pub_ = this->create_generic_publisher(topic_out_, kTraceChainMessageTypeName, qos);
        generic_sub_ = this->create_generic_subscription(
          topic_in_, kTraceChainMessageTypeName, qos,
          [this](std::shared_ptr<rclcpp::SerializedMessage> msg) { on_message_raw(msg); });
    }

    const auto timer_period = std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::duration<double>(1.0 / publish_hz_));
    publish_timer_ = this->create_wall_timer(timer_period, [this]() { on_publish_timer(); });

    RCLCPP_INFO(this->get_logger(),
                "MockTraceIndirectRelayNode started: topic_in=%s, topic_out=%s, transport=%s, "
                "publish_hz=%.3f",
                topic_in_.c_str(), topic_out_.c_str(), transport_str.c_str(), publish_hz_);
}

MockTraceIndirectRelayNode::~MockTraceIndirectRelayNode() {
    if (csv_.is_open()) {
        csv_.flush();
        csv_.close();
    }
}

void MockTraceIndirectRelayNode::on_message(
  const trace_msgs::msg::TraceChainMessage::SharedPtr msg) {
    const int64_t wall_recv_ns = monotonic_now_ns();
    on_upstream_message(*msg, wall_recv_ns);
}

void MockTraceIndirectRelayNode::on_message_raw(std::shared_ptr<rclcpp::SerializedMessage> msg) {
    const int64_t wall_recv_ns = monotonic_now_ns();
    serializer_.deserialize_message(msg.get(), &decode_scratch_);
    on_upstream_message(decode_scratch_, wall_recv_ns);
}

void MockTraceIndirectRelayNode::on_upstream_message(
  const trace_msgs::msg::TraceChainMessage &upstream, int64_t wall_recv_ns) {
    latest_upstream_ = upstream;
    latest_wall_recv_ns_ = wall_recv_ns;

    // on every callback, overwrite and arm the guid
    callback_guid_++;
    armed_callback_guid_ = callback_guid_;
    armed_ = true;
    TRACEPOINT(message_link_take, armed_callback_guid_,
               static_cast<uint64_t>(reinterpret_cast<uintptr_t>(&upstream)));
}

void MockTraceIndirectRelayNode::on_publish_timer() {
    // only publish when timer is armed -- we arm only when the dependent subscriber callback
    // has received a message
    if (!armed_) {
        return;
    }

    relay(latest_upstream_, latest_wall_recv_ns_);
    armed_ = false;
}

void MockTraceIndirectRelayNode::relay(const trace_msgs::msg::TraceChainMessage &upstream,
                                       int64_t wall_recv_ns) {
    out_msg_.header.stamp = this->now();
    const uint64_t trace_id = trace_id_base_ + counter_;
    ++counter_;
    out_msg_.trace_id = trace_id;
    out_msg_.chain_id = upstream.chain_id;
    out_msg_.step_index = upstream.step_index + 1;
    out_msg_.upstream_trace_id = upstream.trace_id;
    out_msg_.upstream_header = upstream.header;
    out_msg_.payload = build_relay_payload(upstream.payload, static_cast<size_t>(payload_size_));

    const int64_t wall_publish_ns = monotonic_now_ns();

    TRACEPOINT(message_link_publish, armed_callback_guid_,
               static_cast<uint64_t>(reinterpret_cast<uintptr_t>(&out_msg_)));

    if (transport_ == TraceTransport::kTyped) {
        pub_->publish(out_msg_);
    } else {
        serializer_.serialize_message(&out_msg_, &serialized_scratch_);
        generic_pub_->publish(serialized_scratch_);
    }

    const int64_t header_stamp_ns = header_stamp_to_ns(out_msg_.header.stamp);
    const int64_t upstream_header_stamp_ns = header_stamp_to_ns(upstream.header.stamp);

    csv_ << out_msg_.trace_id << ',' << header_stamp_ns << ',' << wall_publish_ns << ','
         << out_msg_.chain_id << ',' << out_msg_.step_index << ',' << out_msg_.upstream_trace_id
         << ',' << upstream_header_stamp_ns << ',' << wall_recv_ns << ',' << topic_in_ << ','
         << topic_out_ << ',' << this->get_name() << ',' << upstream.payload.size() << ','
         << out_msg_.payload.size() << '\n';

    ++log_counter_;
    if (log_counter_ % CSV_FLUSH_EVERY_N == 0) {
        csv_.flush();
    }
    if (log_counter_ % LOG_EVERY_N == 0) {
        RCLCPP_INFO(this->get_logger(), "Relayed %llu messages (trace_id=%llu)",
                    static_cast<unsigned long long>(log_counter_),
                    static_cast<unsigned long long>(out_msg_.trace_id));
    }
}

}  // namespace mock_trace_cpp
