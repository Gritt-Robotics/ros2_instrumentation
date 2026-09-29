// Copyright 2026 Gritt Robotics Inc.

#include <gtest/gtest.h>

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <trace_msgs/msg/trace_message.hpp>
#include <trace_msgs/msg/trace_message_fixed.hpp>
#include <vector>

#include "mock_trace_cpp/message_dispatch.hpp"
#include "mock_trace_cpp/parameter_names.hpp"
#include "mock_trace_cpp/publisher_node.hpp"
#include "rclcpp/rclcpp.hpp"

namespace mock_trace_cpp {
namespace {

constexpr int SPIN_SLEEP_MS = 5;
constexpr int PUBLISH_SPIN_MS = 400;
constexpr double TEST_TARGET_HZ = 50.0;
constexpr int TEST_PAYLOAD_SIZE = 4;
constexpr uint64_t TEST_TRACE_ID_BASE = 1000;
constexpr int TEST_QOS_DEPTH = 10;
constexpr size_t MIN_EXPECTED_ROWS = 5;
constexpr uint64_t TRACE_MESSAGE_FIXED_PAYLOAD_SIZE = 1'000'000;
const char *const TEST_TOPIC = "/mock_publisher_node_test/topic";

std::vector<std::string> read_lines(const std::string &path) {
    std::ifstream file(path);
    std::vector<std::string> lines;
    std::string line;
    while (std::getline(file, line)) {
        lines.push_back(line);
    }
    return lines;
}

std::vector<std::string> split_csv_row(const std::string &row) {
    std::vector<std::string> fields;
    std::stringstream ss(row);
    std::string field;
    while (std::getline(ss, field, ',')) {
        fields.push_back(field);
    }
    return fields;
}

class MockTracePublisherNodeTest : public ::testing::Test {
protected:
    void SetUp() override {
        test_dir_ = std::filesystem::temp_directory_path() / "mock_publisher_node_test";
        if (std::filesystem::exists(test_dir_)) {
            std::filesystem::remove_all(test_dir_);
        }
        std::filesystem::create_directories(test_dir_);
    }

    void TearDown() override {
        if (std::filesystem::exists(test_dir_)) {
            std::filesystem::remove_all(test_dir_);
        }
    }

    std::string csv_path() const { return (test_dir_ / "ground_truth.csv").string(); }

    std::map<std::string, rclcpp::ParameterValue> default_params() const {
        std::map<std::string, rclcpp::ParameterValue> params;
        params[kParamTargetHz] = rclcpp::ParameterValue(TEST_TARGET_HZ);
        params[kParamTopic] = rclcpp::ParameterValue(std::string(TEST_TOPIC));
        params[kParamPayloadSize] = rclcpp::ParameterValue(TEST_PAYLOAD_SIZE);
        params[kParamTraceIdBase] =
          rclcpp::ParameterValue(static_cast<int64_t>(TEST_TRACE_ID_BASE));
        params[kParamOutputCsv] = rclcpp::ParameterValue(csv_path());
        params[kParamQosDepth] = rclcpp::ParameterValue(TEST_QOS_DEPTH);
        params[kParamMessageType] = rclcpp::ParameterValue(std::string(kMessageTypeVariable));
        params[kParamTransport] = rclcpp::ParameterValue(std::string(kTransportTyped));
        return params;
    }

    std::shared_ptr<MockTracePublisherNode> create_node(
      const std::map<std::string, rclcpp::ParameterValue> &params) const {
        rclcpp::NodeOptions options;
        options.automatically_declare_parameters_from_overrides(true);
        for (const auto &[key, value] : params) {
            options.parameter_overrides().push_back(rclcpp::Parameter(key, value));
        }
        return std::make_shared<MockTracePublisherNode>(options);
    }

    void spin_for(const std::shared_ptr<MockTracePublisherNode> &node,
                  std::chrono::milliseconds duration) const {
        const auto end_time = std::chrono::steady_clock::now() + duration;
        while (std::chrono::steady_clock::now() < end_time) {
            rclcpp::spin_some(node);
            std::this_thread::sleep_for(std::chrono::milliseconds(SPIN_SLEEP_MS));
        }
    }

    std::filesystem::path test_dir_;
};

TEST_F(MockTracePublisherNodeTest, PublishesRowsWithExpectedContent) {
    auto node = create_node(default_params());
    spin_for(node, std::chrono::milliseconds(PUBLISH_SPIN_MS));
    node.reset();

    const std::vector<std::string> lines = read_lines(csv_path());
    ASSERT_GE(lines.size(), MIN_EXPECTED_ROWS + 1);
    EXPECT_EQ(lines.front(), "trace_id,header_stamp_ns,wall_publish_ns,topic,node,payload_len");

    uint64_t expected_trace_id = TEST_TRACE_ID_BASE;
    for (size_t i = 1; i < lines.size(); ++i) {
        const std::vector<std::string> fields = split_csv_row(lines.at(i));
        ASSERT_EQ(fields.size(), 6u);
        EXPECT_EQ(std::stoull(fields.at(0)), expected_trace_id);
        EXPECT_EQ(fields.at(3), TEST_TOPIC);
        EXPECT_EQ(fields.at(4), "mock_publisher_node");
        EXPECT_EQ(fields.at(5), std::to_string(TEST_PAYLOAD_SIZE));
        ++expected_trace_id;
    }
}

TEST_F(MockTracePublisherNodeTest, ThrowsOnNonPositiveTargetHz) {
    auto params = default_params();
    params[kParamTargetHz] = rclcpp::ParameterValue(0.0);
    EXPECT_THROW(create_node(params), std::runtime_error);
}

TEST_F(MockTracePublisherNodeTest, ThrowsOnNegativePayloadSize) {
    auto params = default_params();
    params[kParamPayloadSize] = rclcpp::ParameterValue(-1);
    EXPECT_THROW(create_node(params), std::runtime_error);
}

TEST_F(MockTracePublisherNodeTest, ThrowsOnNonPositiveQosDepth) {
    auto params = default_params();
    params[kParamQosDepth] = rclcpp::ParameterValue(0);
    EXPECT_THROW(create_node(params), std::runtime_error);
}

TEST_F(MockTracePublisherNodeTest, ThrowsOnUnsupportedMessageType) {
    auto params = default_params();
    params[kParamMessageType] = rclcpp::ParameterValue(std::string("bogus"));
    EXPECT_THROW(create_node(params), std::invalid_argument);
}

TEST_F(MockTracePublisherNodeTest, PublishesRowsWithFixedMessageType) {
    auto params = default_params();
    params["message_type"] = rclcpp::ParameterValue(std::string(kMessageTypeFixed));
    auto node = create_node(params);
    spin_for(node, std::chrono::milliseconds(PUBLISH_SPIN_MS));
    node.reset();

    const std::vector<std::string> lines = read_lines(csv_path());
    ASSERT_GE(lines.size(), MIN_EXPECTED_ROWS + 1);
    const std::vector<std::string> fields = split_csv_row(lines.at(1));
    ASSERT_EQ(fields.size(), 6u);
    EXPECT_EQ(fields.at(5), std::to_string(TRACE_MESSAGE_FIXED_PAYLOAD_SIZE));
}

TEST_F(MockTracePublisherNodeTest, PublishesOverTheWireWithSerializedTransport) {
    auto params = default_params();
    params["transport"] = rclcpp::ParameterValue(std::string(kTransportSerialized));
    auto node = create_node(params);

    // A plain typed subscription can decode a serialized-transport publish: the bytes
    // on the wire are the same TraceMessage encoding either way, so a successful
    // receive here confirms the publisher serialized and published a well-formed
    // message rather than raw garbage.
    auto subscriber_node = std::make_shared<rclcpp::Node>("test_mock_publisher_node_subscriber");
    std::vector<uint64_t> received_ids;
    auto sub = subscriber_node->create_subscription<trace_msgs::msg::TraceMessage>(
      TEST_TOPIC, rclcpp::QoS(TEST_QOS_DEPTH),
      [&received_ids](const trace_msgs::msg::TraceMessage::SharedPtr msg) {
          received_ids.push_back(msg->trace_id);
      });

    const auto end_time =
      std::chrono::steady_clock::now() + std::chrono::milliseconds(PUBLISH_SPIN_MS);
    while (std::chrono::steady_clock::now() < end_time) {
        rclcpp::spin_some(node);
        rclcpp::spin_some(subscriber_node);
        std::this_thread::sleep_for(std::chrono::milliseconds(SPIN_SLEEP_MS));
    }
    node.reset();

    ASSERT_GE(received_ids.size(), MIN_EXPECTED_ROWS);
    EXPECT_EQ(received_ids.front(), TEST_TRACE_ID_BASE);
}

TEST_F(MockTracePublisherNodeTest, PublishesOverTheWireWithSerializedTransportAndFixedMessageType) {
    auto params = default_params();
    params["message_type"] = rclcpp::ParameterValue(std::string(kMessageTypeFixed));
    params["transport"] = rclcpp::ParameterValue(std::string(kTransportSerialized));
    auto node = create_node(params);

    // Same rationale as PublishesOverTheWireWithSerializedTransport, but crossed with
    // message_type=fixed to cover the serializer_fixed_ branch of tick().
    auto subscriber_node = std::make_shared<rclcpp::Node>("test_mock_publisher_node_subscriber");
    std::vector<uint64_t> received_ids;
    auto sub = subscriber_node->create_subscription<trace_msgs::msg::TraceMessageFixed>(
      TEST_TOPIC, rclcpp::QoS(TEST_QOS_DEPTH),
      [&received_ids](const trace_msgs::msg::TraceMessageFixed::SharedPtr msg) {
          received_ids.push_back(msg->trace_id);
      });

    const auto end_time =
      std::chrono::steady_clock::now() + std::chrono::milliseconds(PUBLISH_SPIN_MS);
    while (std::chrono::steady_clock::now() < end_time) {
        rclcpp::spin_some(node);
        rclcpp::spin_some(subscriber_node);
        std::this_thread::sleep_for(std::chrono::milliseconds(SPIN_SLEEP_MS));
    }
    node.reset();

    ASSERT_GE(received_ids.size(), MIN_EXPECTED_ROWS);
    EXPECT_EQ(received_ids.front(), TEST_TRACE_ID_BASE);
}

}  // namespace
}  // namespace mock_trace_cpp

int main(int argc, char **argv) {
    ::testing::InitGoogleTest(&argc, argv);
    rclcpp::init(argc, argv);
    const int result = RUN_ALL_TESTS();
    rclcpp::shutdown();
    return result;
}
