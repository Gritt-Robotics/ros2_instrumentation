// Copyright 2026 Gritt Robotics Inc.

#include "mock_trace_cpp/subscriber_node.hpp"

#include <chrono>
#include <filesystem>
#include <stdexcept>
#include <string>

#include "mock_trace_cpp/message_dispatch.hpp"
#include "mock_trace_cpp/parameter_names.hpp"
#include "mock_trace_cpp/time_utils.hpp"

namespace mock_trace_cpp {

MockTraceSubscriberNode::MockTraceSubscriberNode(const rclcpp::NodeOptions &options)
    : Node("mock_subscriber_node", options) {
    topic_ = this->get_parameter(kParamTopic).as_string();
    output_csv_ = this->get_parameter(kParamOutputCsv).as_string();
    qos_depth_ = static_cast<int>(this->get_parameter(kParamQosDepth).as_int());
    const std::string message_type_str = this->get_parameter(kParamMessageType).as_string();
    const std::string transport_str = this->get_parameter(kParamTransport).as_string();
    message_type_ = parse_message_type(message_type_str);
    transport_ = parse_transport(transport_str);

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
    csv_ << "trace_id,header_stamp_ns,wall_recv_ns,topic,node";
    if (message_type_ == TraceMessageType::kChain) {
        csv_ << ",chain_id,step_index,upstream_trace_id";
    }
    csv_ << '\n';

    rclcpp::QoS qos(qos_depth_);
    std::string type_string = kTraceMessageTypeName;
    if (message_type_ == TraceMessageType::kFixed) {
        type_string = kTraceMessageFixedTypeName;
    } else if (message_type_ == TraceMessageType::kChain) {
        type_string = kTraceChainMessageTypeName;
    }

    if (transport_ == TraceTransport::kTyped) {
        if (message_type_ == TraceMessageType::kVariable) {
            sub_variable_ = this->create_subscription<trace_msgs::msg::TraceMessage>(
              topic_, qos, [this](const trace_msgs::msg::TraceMessage::SharedPtr msg) {
                  on_message_variable(msg);
              });
        } else if (message_type_ == TraceMessageType::kFixed) {
            sub_fixed_ = this->create_subscription<trace_msgs::msg::TraceMessageFixed>(
              topic_, qos, [this](const trace_msgs::msg::TraceMessageFixed::SharedPtr msg) {
                  on_message_fixed(msg);
              });
        } else {
            sub_chain_ = this->create_subscription<trace_msgs::msg::TraceChainMessage>(
              topic_, qos, [this](const trace_msgs::msg::TraceChainMessage::SharedPtr msg) {
                  on_message_chain(msg);
              });
        }
    } else {
        generic_sub_ = this->create_generic_subscription(
          topic_, type_string, qos,
          [this](std::shared_ptr<rclcpp::SerializedMessage> msg) { on_message_serialized(msg); });
    }

    RCLCPP_INFO(this->get_logger(),
                "MockTraceSubscriberNode started: topic=%s, message_type=%s, transport=%s",
                topic_.c_str(), message_type_str.c_str(), transport_str.c_str());
}

MockTraceSubscriberNode::~MockTraceSubscriberNode() {
    if (csv_.is_open()) {
        csv_.flush();
        csv_.close();
    }
}

void MockTraceSubscriberNode::on_message_variable(
  const trace_msgs::msg::TraceMessage::SharedPtr msg) {
    const int64_t wall_recv_ns = monotonic_now_ns();
    log_row(msg->trace_id, header_stamp_to_ns(msg->header.stamp), wall_recv_ns);
}

void MockTraceSubscriberNode::on_message_fixed(
  const trace_msgs::msg::TraceMessageFixed::SharedPtr msg) {
    const int64_t wall_recv_ns = monotonic_now_ns();
    log_row(msg->trace_id, header_stamp_to_ns(msg->header.stamp), wall_recv_ns);
}

void MockTraceSubscriberNode::on_message_chain(
  const trace_msgs::msg::TraceChainMessage::SharedPtr msg) {
    const int64_t wall_recv_ns = monotonic_now_ns();
    log_row_chain(msg->trace_id, header_stamp_to_ns(msg->header.stamp), wall_recv_ns, msg->chain_id,
                  msg->step_index, msg->upstream_trace_id);
}

void MockTraceSubscriberNode::on_message_serialized(
  std::shared_ptr<rclcpp::SerializedMessage> msg) {
    const int64_t wall_recv_ns = monotonic_now_ns();
    if (message_type_ == TraceMessageType::kVariable) {
        serializer_variable_.deserialize_message(msg.get(), &decode_variable_);
        log_row(decode_variable_.trace_id, header_stamp_to_ns(decode_variable_.header.stamp),
                wall_recv_ns);
    } else if (message_type_ == TraceMessageType::kFixed) {
        serializer_fixed_.deserialize_message(msg.get(), &decode_fixed_);
        log_row(decode_fixed_.trace_id, header_stamp_to_ns(decode_fixed_.header.stamp),
                wall_recv_ns);
    } else {
        serializer_chain_.deserialize_message(msg.get(), &decode_chain_);
        log_row_chain(decode_chain_.trace_id, header_stamp_to_ns(decode_chain_.header.stamp),
                      wall_recv_ns, decode_chain_.chain_id, decode_chain_.step_index,
                      decode_chain_.upstream_trace_id);
    }
}

void MockTraceSubscriberNode::log_row(uint64_t trace_id, int64_t header_stamp_ns,
                                      int64_t wall_recv_ns) {
    csv_ << trace_id << ',' << header_stamp_ns << ',' << wall_recv_ns << ',' << topic_ << ','
         << this->get_name() << '\n';

    ++recv_counter_;
    if (recv_counter_ % CSV_FLUSH_EVERY_N == 0) {
        csv_.flush();
    }
}

void MockTraceSubscriberNode::log_row_chain(uint64_t trace_id, int64_t header_stamp_ns,
                                            int64_t wall_recv_ns, uint64_t chain_id,
                                            uint32_t step_index, uint64_t upstream_trace_id) {
    csv_ << trace_id << ',' << header_stamp_ns << ',' << wall_recv_ns << ',' << topic_ << ','
         << this->get_name() << ',' << chain_id << ',' << step_index << ',' << upstream_trace_id
         << '\n';

    ++recv_counter_;
    if (recv_counter_ % CSV_FLUSH_EVERY_N == 0) {
        csv_.flush();
    }
}

}  // namespace mock_trace_cpp
