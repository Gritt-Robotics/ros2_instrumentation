// Copyright 2026 Gritt Robotics Inc.

#pragma once

#include <builtin_interfaces/msg/time.hpp>
#include <chrono>
#include <cstdint>

namespace mock_trace_cpp {

// Converts a builtin_interfaces/Time header stamp to nanoseconds since epoch.
inline int64_t header_stamp_to_ns(const builtin_interfaces::msg::Time &stamp) {
    return static_cast<int64_t>(stamp.sec) * 1'000'000'000L + static_cast<int64_t>(stamp.nanosec);
}

// Current time from the monotonic (steady) clock, in nanoseconds. Used for
// wall_publish_ns/wall_recv_ns instead of the node clock (this->now()), which is
// ROS/sim time and can jump under use_sim_time -- staying on the monotonic clock keeps
// these timestamps in the same time domain as the LTTng trace.
inline int64_t monotonic_now_ns() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
}

}  // namespace mock_trace_cpp
