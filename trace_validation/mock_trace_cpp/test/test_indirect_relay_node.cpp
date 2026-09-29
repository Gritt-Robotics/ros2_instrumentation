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
#include <trace_msgs/msg/trace_chain_message.hpp>
#include <vector>

#include "mock_trace_cpp/indirect_relay_node.hpp"
#include "mock_trace_cpp/message_dispatch.hpp"
#include "mock_trace_cpp/parameter_names.hpp"
#include "rclcpp/rclcpp.hpp"

namespace mock_trace_cpp {
namespace {

constexpr int SPIN_SLEEP_MS = 5;
constexpr int DISCOVERY_TIMEOUT_MS = 5000;
constexpr int RELAY_SPIN_MS = 400;
constexpr int TEST_QOS_DEPTH = 10;
constexpr int TEST_PAYLOAD_SIZE = 5;
constexpr double TEST_PUBLISH_HZ = 200.0;
constexpr uint64_t TEST_TRACE_ID_BASE = 3000;
constexpr uint64_t UPSTREAM_CHAIN_ID = 42;
constexpr uint32_t UPSTREAM_STEP_INDEX = 3;
constexpr uint64_t UPSTREAM_TRACE_ID = 7;
const char *const TEST_TOPIC_IN = "/mock_indirect_relay_node_test/topic_in";
const char *const TEST_TOPIC_OUT = "/mock_indirect_relay_node_test/topic_out";

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

trace_msgs::msg::TraceChainMessage make_upstream_msg(const std::vector<int32_t> &payload) {
    trace_msgs::msg::TraceChainMessage msg;
    msg.header.frame_id = "";
    msg.trace_id = UPSTREAM_TRACE_ID;
    msg.chain_id = UPSTREAM_CHAIN_ID;
    msg.step_index = UPSTREAM_STEP_INDEX;
    msg.upstream_trace_id = UPSTREAM_TRACE_ID - 1;
    msg.payload = payload;
    return msg;
}

class MockTraceIndirectRelayNodeTest : public ::testing::Test {
protected:
    void SetUp() override {
        test_dir_ = std::filesystem::temp_directory_path() / "mock_indirect_relay_node_test";
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

    std::string csv_path() const { return (test_dir_ / "relay.csv").string(); }

    std::map<std::string, rclcpp::ParameterValue> default_params() const {
        std::map<std::string, rclcpp::ParameterValue> params;
        params[kParamTopicIn] = rclcpp::ParameterValue(std::string(TEST_TOPIC_IN));
        params[kParamTopicOut] = rclcpp::ParameterValue(std::string(TEST_TOPIC_OUT));
        params[kParamPayloadSize] = rclcpp::ParameterValue(TEST_PAYLOAD_SIZE);
        params[kParamTraceIdBase] =
          rclcpp::ParameterValue(static_cast<int64_t>(TEST_TRACE_ID_BASE));
        params[kParamOutputCsv] = rclcpp::ParameterValue(csv_path());
        params[kParamQosDepth] = rclcpp::ParameterValue(TEST_QOS_DEPTH);
        params[kParamTransport] = rclcpp::ParameterValue(std::string(kTransportTyped));
        params[kParamPublishHz] = rclcpp::ParameterValue(TEST_PUBLISH_HZ);
        return params;
    }

    std::shared_ptr<MockTraceIndirectRelayNode> create_node(
      const std::map<std::string, rclcpp::ParameterValue> &params) const {
        rclcpp::NodeOptions options;
        options.automatically_declare_parameters_from_overrides(true);
        for (const auto &[key, value] : params) {
            options.parameter_overrides().push_back(rclcpp::Parameter(key, value));
        }
        return std::make_shared<MockTraceIndirectRelayNode>(options);
    }

    std::filesystem::path test_dir_;
};

// Publishes one upstream message and spins both the relay node and a plain subscriber on
// topic_out until either a relayed message arrives (the relay's publish timer fires) or the
// timeout elapses.
trace_msgs::msg::TraceChainMessage relay_one(
  const std::shared_ptr<MockTraceIndirectRelayNode> &node,
  const trace_msgs::msg::TraceChainMessage &upstream) {
    auto publisher_node = std::make_shared<rclcpp::Node>("test_mock_indirect_relay_node_publisher");
    auto pub = publisher_node->create_publisher<trace_msgs::msg::TraceChainMessage>(
      TEST_TOPIC_IN, rclcpp::QoS(TEST_QOS_DEPTH));

    auto subscriber_node =
      std::make_shared<rclcpp::Node>("test_mock_indirect_relay_node_subscriber");
    std::vector<trace_msgs::msg::TraceChainMessage> received;
    auto sub = subscriber_node->create_subscription<trace_msgs::msg::TraceChainMessage>(
      TEST_TOPIC_OUT, rclcpp::QoS(TEST_QOS_DEPTH),
      [&received](const trace_msgs::msg::TraceChainMessage::SharedPtr msg) {
          received.push_back(*msg);
      });

    const auto discovery_end =
      std::chrono::steady_clock::now() + std::chrono::milliseconds(DISCOVERY_TIMEOUT_MS);
    while (pub->get_subscription_count() == 0 && std::chrono::steady_clock::now() < discovery_end) {
        rclcpp::spin_some(node);
        rclcpp::spin_some(subscriber_node);
        std::this_thread::sleep_for(std::chrono::milliseconds(SPIN_SLEEP_MS));
    }
    pub->publish(upstream);

    const auto relay_end =
      std::chrono::steady_clock::now() + std::chrono::milliseconds(RELAY_SPIN_MS);
    while (received.empty() && std::chrono::steady_clock::now() < relay_end) {
        rclcpp::spin_some(node);
        rclcpp::spin_some(subscriber_node);
        std::this_thread::sleep_for(std::chrono::milliseconds(SPIN_SLEEP_MS));
    }

    EXPECT_FALSE(received.empty()) << "indirect relay node never republished onto topic_out";
    return received.empty() ? trace_msgs::msg::TraceChainMessage() : received.front();
}

TEST_F(MockTraceIndirectRelayNodeTest, PadsShortPayloadWithFiller) {
    auto node = create_node(default_params());
    const auto out = relay_one(node, make_upstream_msg({1, 2, 3}));
    node.reset();

    EXPECT_EQ(out.chain_id, UPSTREAM_CHAIN_ID);
    EXPECT_EQ(out.step_index, UPSTREAM_STEP_INDEX + 1);
    EXPECT_EQ(out.upstream_trace_id, UPSTREAM_TRACE_ID);
    EXPECT_EQ(out.trace_id, TEST_TRACE_ID_BASE);
    ASSERT_EQ(out.payload.size(), static_cast<size_t>(TEST_PAYLOAD_SIZE));
    const std::vector<int32_t> expected = {1, 2, 3, 0, 0};
    EXPECT_EQ(out.payload, expected);
}

TEST_F(MockTraceIndirectRelayNodeTest, TruncatesLongPayloadToTargetSize) {
    auto node = create_node(default_params());
    const auto out = relay_one(node, make_upstream_msg({1, 2, 3, 4, 5, 6, 7}));
    node.reset();

    ASSERT_EQ(out.payload.size(), static_cast<size_t>(TEST_PAYLOAD_SIZE));
    const std::vector<int32_t> expected = {1, 2, 3, 4, 5};
    EXPECT_EQ(out.payload, expected);
}

TEST_F(MockTraceIndirectRelayNodeTest, WritesCsvRowWithStepAndTimingFields) {
    auto node = create_node(default_params());
    relay_one(node, make_upstream_msg({1, 2, 3}));
    node.reset();

    const std::vector<std::string> lines = read_lines(csv_path());
    ASSERT_GE(lines.size(), 2u);
    EXPECT_EQ(lines.front(),
              "trace_id,header_stamp_ns,wall_publish_ns,chain_id,step_index,upstream_trace_id,"
              "upstream_header_stamp_ns,wall_recv_ns,topic_in,topic_out,node,payload_len_in,"
              "payload_len_out");

    const std::vector<std::string> fields = split_csv_row(lines.at(1));
    ASSERT_EQ(fields.size(), 13u);
    EXPECT_EQ(std::stoull(fields.at(0)), TEST_TRACE_ID_BASE);
    EXPECT_EQ(std::stoull(fields.at(3)), UPSTREAM_CHAIN_ID);
    EXPECT_EQ(std::stoul(fields.at(4)), UPSTREAM_STEP_INDEX + 1);
    EXPECT_EQ(std::stoull(fields.at(5)), UPSTREAM_TRACE_ID);
    EXPECT_EQ(fields.at(8), TEST_TOPIC_IN);
    EXPECT_EQ(fields.at(9), TEST_TOPIC_OUT);
}

TEST_F(MockTraceIndirectRelayNodeTest, RelaysOverTheWireWithSerializedTransport) {
    auto params = default_params();
    params[kParamTransport] = rclcpp::ParameterValue(std::string(kTransportSerialized));
    auto node = create_node(params);
    const auto out = relay_one(node, make_upstream_msg({1, 2, 3}));
    node.reset();

    EXPECT_EQ(out.chain_id, UPSTREAM_CHAIN_ID);
    EXPECT_EQ(out.step_index, UPSTREAM_STEP_INDEX + 1);
    ASSERT_EQ(out.payload.size(), static_cast<size_t>(TEST_PAYLOAD_SIZE));
}

TEST_F(MockTraceIndirectRelayNodeTest, ThrowsOnNegativePayloadSize) {
    auto params = default_params();
    params[kParamPayloadSize] = rclcpp::ParameterValue(-1);
    EXPECT_THROW(create_node(params), std::runtime_error);
}

TEST_F(MockTraceIndirectRelayNodeTest, ThrowsOnNonPositiveQosDepth) {
    auto params = default_params();
    params[kParamQosDepth] = rclcpp::ParameterValue(0);
    EXPECT_THROW(create_node(params), std::runtime_error);
}

TEST_F(MockTraceIndirectRelayNodeTest, ThrowsOnNonPositivePublishHz) {
    auto params = default_params();
    params[kParamPublishHz] = rclcpp::ParameterValue(0.0);
    EXPECT_THROW(create_node(params), std::runtime_error);
}

TEST_F(MockTraceIndirectRelayNodeTest, ThrowsOnUnsupportedTransport) {
    auto params = default_params();
    params[kParamTransport] = rclcpp::ParameterValue(std::string("bogus"));
    EXPECT_THROW(create_node(params), std::invalid_argument);
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
