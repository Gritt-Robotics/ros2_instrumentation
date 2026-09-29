// Copyright 2026 Gritt Robotics Inc.

#include "mock_trace_cpp/publisher_node.hpp"

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <std_msgs/msg/header.hpp>
#include <stdexcept>
#include <string>

#include "mock_trace_cpp/message_dispatch.hpp"
#include "mock_trace_cpp/parameter_names.hpp"
#include "mock_trace_cpp/time_utils.hpp"

namespace mock_trace_cpp {

MockTracePublisherNode::MockTracePublisherNode(const rclcpp::NodeOptions &options)
    : Node("mock_publisher_node", options) {
    target_hz_ = this->get_parameter(kParamTargetHz).as_double();
    topic_ = this->get_parameter(kParamTopic).as_string();
    payload_size_ = static_cast<int>(this->get_parameter(kParamPayloadSize).as_int());
    trace_id_base_ = static_cast<uint64_t>(this->get_parameter(kParamTraceIdBase).as_int());
    output_csv_ = this->get_parameter(kParamOutputCsv).as_string();
    qos_depth_ = static_cast<int>(this->get_parameter(kParamQosDepth).as_int());
    const std::string message_type_str = this->get_parameter(kParamMessageType).as_string();
    const std::string transport_str = this->get_parameter(kParamTransport).as_string();
    message_type_ = parse_message_type(message_type_str);
    transport_ = parse_transport(transport_str);

    if (target_hz_ <= 0.0) {
        throw std::runtime_error("target_hz must be > 0, got " + std::to_string(target_hz_));
    }
    if (payload_size_ < 0) {
        throw std::runtime_error("payload_size must be >= 0, got " + std::to_string(payload_size_));
    }
    if (qos_depth_ <= 0) {
        throw std::runtime_error("qos_depth must be > 0, got " + std::to_string(qos_depth_));
    }

    const std::filesystem::path csv_path(output_csv_);
    if (csv_path.has_parent_path()) {
        std::filesystem::create_directories(csv_path.parent_path());
    }

    csv_.open(output_csv_, std::ios::out | std::ios::trunc);
    if (!csv_.is_open()) {
        throw std::runtime_error("Failed to open output_csv for writing: " + output_csv_);
    }
    csv_ << "trace_id,header_stamp_ns,wall_publish_ns,topic,node,payload_len";
    if (message_type_ == TraceMessageType::kChain) {
        csv_ << ",chain_id,step_index,upstream_trace_id";
    }
    csv_ << '\n';

    msg_variable_.header.frame_id = "";
    msg_fixed_.header.frame_id = "";
    msg_chain_.header.frame_id = "";

    if (message_type_ == TraceMessageType::kVariable || message_type_ == TraceMessageType::kChain) {
        // Allocate the payload buffer once since payload_size_ never changes.
        auto &payload =
          message_type_ == TraceMessageType::kVariable ? msg_variable_.payload : msg_chain_.payload;
        payload.assign(payload_size_, 0);
    } else {
        // payload_size_ is intentionally ignored for fixed messages: the payload is
        // always exactly 1,000,000 int32s (TraceMessageFixed.msg), already
        // zero-initialized by the message's default constructor.
        RCLCPP_INFO(this->get_logger(),
                    "message_type=fixed: ignoring payload_size=%d (fixed payload is always "
                    "1000000 int32s)",
                    payload_size_);
    }

    if (message_type_ == TraceMessageType::kChain) {
        // This node is always step 0 of a chain: no upstream message exists yet. chain_id
        // is set per-message in tick() (it equals this step's own trace_id).
        msg_chain_.step_index = 0;
        msg_chain_.upstream_trace_id = 0;
        msg_chain_.upstream_header = std_msgs::msg::Header();
    }

    rclcpp::QoS qos(qos_depth_);
    std::string type_string = kTraceMessageTypeName;
    if (message_type_ == TraceMessageType::kFixed) {
        type_string = kTraceMessageFixedTypeName;
    } else if (message_type_ == TraceMessageType::kChain) {
        type_string = kTraceChainMessageTypeName;
    }

    if (transport_ == TraceTransport::kTyped) {
        if (message_type_ == TraceMessageType::kVariable) {
            pub_variable_ = this->create_publisher<trace_msgs::msg::TraceMessage>(topic_, qos);
        } else if (message_type_ == TraceMessageType::kFixed) {
            pub_fixed_ = this->create_publisher<trace_msgs::msg::TraceMessageFixed>(topic_, qos);
        } else {
            pub_chain_ = this->create_publisher<trace_msgs::msg::TraceChainMessage>(topic_, qos);
        }
    } else {
        generic_pub_ = this->create_generic_publisher(topic_, type_string, qos);
    }

    const auto period = std::chrono::duration<double>(1.0 / target_hz_);
    timer_ = this->create_wall_timer(period, [this]() { tick(); });

    RCLCPP_INFO(this->get_logger(),
                "MockTracePublisherNode started: topic=%s, hz=%.1f, id_base=%llu, message_type=%s, "
                "transport=%s",
                topic_.c_str(), target_hz_, static_cast<unsigned long long>(trace_id_base_),
                message_type_str.c_str(), transport_str.c_str());
}

MockTracePublisherNode::~MockTracePublisherNode() {
    if (csv_.is_open()) {
        csv_.flush();
        csv_.close();
    }
}

void MockTracePublisherNode::tick() {
    // Steps 1-2: record stamp immediately before filling the message
    const auto t = this->now();

    // Step 3: assign unique id from monotonic counter
    const uint64_t current_counter = counter_;
    const uint64_t trace_id = trace_id_base_ + current_counter;
    ++counter_;

    int64_t header_stamp_ns;
    size_t payload_len;

    // Step 4: payload buffer was allocated once in the constructor. Intentionally do not
    // rewrite it per tick to avoid per-publish memory writes (no resize/realloc).
    if (message_type_ == TraceMessageType::kVariable) {
        msg_variable_.header.stamp = t;
        msg_variable_.trace_id = trace_id;
        header_stamp_ns = header_stamp_to_ns(msg_variable_.header.stamp);
        payload_len = msg_variable_.payload.size();
    } else if (message_type_ == TraceMessageType::kFixed) {
        msg_fixed_.header.stamp = t;
        msg_fixed_.trace_id = trace_id;
        header_stamp_ns = header_stamp_to_ns(msg_fixed_.header.stamp);
        payload_len = msg_fixed_.payload.size();
    } else {
        msg_chain_.header.stamp = t;
        msg_chain_.trace_id = trace_id;
        // This message starts a new chain traversal: chain_id is its own trace_id.
        msg_chain_.chain_id = trace_id;
        header_stamp_ns = header_stamp_to_ns(msg_chain_.header.stamp);
        payload_len = msg_chain_.payload.size();
    }

    // Step 5: capture wall time just before the publish call
    const int64_t wall_publish_ns = monotonic_now_ns();

    // Step 6: publish
    if (transport_ == TraceTransport::kTyped) {
        if (message_type_ == TraceMessageType::kVariable) {
            pub_variable_->publish(msg_variable_);
        } else if (message_type_ == TraceMessageType::kFixed) {
            pub_fixed_->publish(msg_fixed_);
        } else {
            pub_chain_->publish(msg_chain_);
        }
    } else {
        if (message_type_ == TraceMessageType::kVariable) {
            serializer_variable_.serialize_message(&msg_variable_, &serialized_scratch_);
        } else if (message_type_ == TraceMessageType::kFixed) {
            serializer_fixed_.serialize_message(&msg_fixed_, &serialized_scratch_);
        } else {
            serializer_chain_.serialize_message(&msg_chain_, &serialized_scratch_);
        }
        generic_pub_->publish(serialized_scratch_);
    }

    // Step 7: record ground-truth CSV row
    csv_ << trace_id << ',' << header_stamp_ns << ',' << wall_publish_ns << ',' << topic_ << ','
         << this->get_name() << ',' << payload_len;
    if (message_type_ == TraceMessageType::kChain) {
        csv_ << ',' << msg_chain_.chain_id << ',' << msg_chain_.step_index << ','
             << msg_chain_.upstream_trace_id;
    }
    csv_ << '\n';

    ++log_counter_;
    if (log_counter_ % CSV_FLUSH_EVERY_N == 0) {
        csv_.flush();
    }
    if (log_counter_ % LOG_EVERY_N == 0) {
        RCLCPP_INFO(this->get_logger(), "Published %llu messages (trace_id=%llu)",
                    static_cast<unsigned long long>(log_counter_),
                    static_cast<unsigned long long>(trace_id));
    }
}

}  // namespace mock_trace_cpp
