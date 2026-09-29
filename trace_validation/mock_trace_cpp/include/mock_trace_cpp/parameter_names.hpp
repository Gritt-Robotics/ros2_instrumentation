// Copyright 2026 Gritt Robotics Inc.

#pragma once

namespace mock_trace_cpp {

// ROS parameter names shared by MockTracePublisherNode and MockTraceSubscriberNode.
inline constexpr const char *kParamTopic = "topic";
inline constexpr const char *kParamOutputCsv = "output_csv";
inline constexpr const char *kParamQosDepth = "qos_depth";
inline constexpr const char *kParamMessageType = "message_type";
inline constexpr const char *kParamTransport = "transport";

// ROS parameter names shared by MockTracePublisherNode and MockTraceRelayNode.
inline constexpr const char *kParamPayloadSize = "payload_size";
inline constexpr const char *kParamTraceIdBase = "trace_id_base";

inline constexpr const char *kParamTargetHz = "target_hz";
inline constexpr const char *kParamTopicIn = "topic_in";
inline constexpr const char *kParamTopicOut = "topic_out";
inline constexpr const char *kParamPublishHz = "publish_hz";

}  // namespace mock_trace_cpp
