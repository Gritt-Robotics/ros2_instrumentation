// Copyright 2026 Gritt Robotics Inc.

#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace mock_trace_cpp {

// Selects which trace_msgs message type is published/subscribed: TraceMessage
// (variable-size payload), TraceMessageFixed (fixed 4MB payload), or TraceChainMessage
// (a step in a multi-node relay chain).
enum class TraceMessageType { kVariable, kFixed, kChain };

// Selects the pub/sub transport: typed (create_publisher<T>/create_subscription<T>) or
// serialized (create_generic_publisher/create_generic_subscription).
enum class TraceTransport { kTyped, kSerialized };

inline constexpr const char *kMessageTypeVariable = "variable";
inline constexpr const char *kMessageTypeFixed = "fixed";
inline constexpr const char *kMessageTypeChain = "chain";
inline constexpr const char *kTransportTyped = "typed";
inline constexpr const char *kTransportSerialized = "serialized";

// ROS type names the generic (serialized) publisher/subscription are created with.
inline constexpr const char *kTraceMessageTypeName = "trace_msgs/msg/TraceMessage";
inline constexpr const char *kTraceMessageFixedTypeName = "trace_msgs/msg/TraceMessageFixed";
inline constexpr const char *kTraceChainMessageTypeName = "trace_msgs/msg/TraceChainMessage";

// Parses the message_type parameter string ("variable", "fixed", or "chain"), throwing
// std::invalid_argument if it isn't a recognized value.
TraceMessageType parse_message_type(const std::string &value);

// Parses the transport parameter string, throwing std::invalid_argument if it isn't a
// recognized value.
TraceTransport parse_transport(const std::string &value);

inline constexpr int32_t kPayloadFillerValue = 0;

// Derives the outgoing payload from the received payload: a copy of upstream_payload
// (truncated if it doesn't fit) followed by filler values to reach target_size.
std::vector<int32_t> build_relay_payload(const std::vector<int32_t> &upstream_payload,
                                         size_t target_size);

}  // namespace mock_trace_cpp
