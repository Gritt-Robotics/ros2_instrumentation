// Copyright 2026 Gritt Robotics Inc.

#include "mock_trace_cpp/message_dispatch.hpp"

#include <algorithm>
#include <stdexcept>

namespace mock_trace_cpp {

TraceMessageType parse_message_type(const std::string &value) {
    if (value == kMessageTypeVariable) return TraceMessageType::kVariable;
    if (value == kMessageTypeFixed) return TraceMessageType::kFixed;
    if (value == kMessageTypeChain) return TraceMessageType::kChain;
    throw std::invalid_argument("message_type must be 'variable', 'fixed', or 'chain', got: " +
                                value);
}

TraceTransport parse_transport(const std::string &value) {
    if (value == kTransportTyped) return TraceTransport::kTyped;
    if (value == kTransportSerialized) return TraceTransport::kSerialized;
    throw std::invalid_argument("transport must be 'typed' or 'serialized', got: " + value);
}

std::vector<int32_t> build_relay_payload(const std::vector<int32_t> &upstream_payload,
                                         size_t target_size) {
    std::vector<int32_t> out;
    out.reserve(target_size);
    const size_t copy_len = std::min(upstream_payload.size(), target_size);
    out.insert(out.end(), upstream_payload.begin(), upstream_payload.begin() + copy_len);
    out.resize(target_size, kPayloadFillerValue);
    return out;
}

}  // namespace mock_trace_cpp
