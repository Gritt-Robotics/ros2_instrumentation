// Copyright 2026 Gritt Robotics Inc.

#include <rclcpp/rclcpp.hpp>

#include "mock_trace_cpp/relay_node.hpp"

int main(int argc, char **argv) {
    rclcpp::init(argc, argv);

    rclcpp::NodeOptions options;
    options.automatically_declare_parameters_from_overrides(true);

    const auto node = std::make_shared<mock_trace_cpp::MockTraceRelayNode>(options);
    rclcpp::spin(node);
    rclcpp::shutdown();
    return 0;
}
