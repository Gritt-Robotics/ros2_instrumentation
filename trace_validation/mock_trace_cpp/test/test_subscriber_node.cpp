// Copyright 2026 Gritt Robotics Inc.

#include <gtest/gtest.h>

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <map>
#include <memory>
#include <rclcpp/serialization.hpp>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <trace_msgs/msg/trace_message.hpp>
#include <trace_msgs/msg/trace_message_fixed.hpp>
#include <vector>

#include "mock_trace_cpp/message_dispatch.hpp"
#include "mock_trace_cpp/parameter_names.hpp"
#include "mock_trace_cpp/subscriber_node.hpp"
#include "rclcpp/rclcpp.hpp"

namespace mock_trace_cpp {
namespace {

constexpr int SPIN_SLEEP_MS = 5;
constexpr int DISCOVERY_TIMEOUT_MS = 5000;
constexpr int RECEIVE_SPIN_MS = 400;
constexpr int TEST_QOS_DEPTH = 10;
constexpr int TEST_NUM_MESSAGES = 3;
constexpr uint64_t TEST_TRACE_ID_BASE = 500;
constexpr int64_t NS_PER_S = 1'000'000'000L;
const char *const TEST_TOPIC = "/mock_subscriber_node_test/topic";
// A distinct topic for the fixed-message-type test: TraceMessage and TraceMessageFixed
// are different wire types, so they cannot share a topic name with the fixture's
// TraceMessage publisher.
const char *const TEST_TOPIC_FIXED = "/mock_subscriber_node_test/mock_fixed";

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

class MockTraceSubscriberNodeTest : public ::testing::Test {
protected:
    void SetUp() override {
        test_dir_ = std::filesystem::temp_directory_path() / "mock_subscriber_node_test";
        if (std::filesystem::exists(test_dir_)) {
            std::filesystem::remove_all(test_dir_);
        }
        std::filesystem::create_directories(test_dir_);

        publisher_node_ = std::make_shared<rclcpp::Node>("test_mock_subscriber_node_publisher");
        pub_ = publisher_node_->create_publisher<trace_msgs::msg::TraceMessage>(
          TEST_TOPIC, rclcpp::QoS(TEST_QOS_DEPTH));
    }

    void TearDown() override {
        pub_.reset();
        publisher_node_.reset();
        if (std::filesystem::exists(test_dir_)) {
            std::filesystem::remove_all(test_dir_);
        }
    }

    std::string csv_path() const { return (test_dir_ / "received.csv").string(); }

    std::map<std::string, rclcpp::ParameterValue> default_params() const {
        std::map<std::string, rclcpp::ParameterValue> params;
        params[kParamTopic] = rclcpp::ParameterValue(std::string(TEST_TOPIC));
        params[kParamOutputCsv] = rclcpp::ParameterValue(csv_path());
        params[kParamQosDepth] = rclcpp::ParameterValue(TEST_QOS_DEPTH);
        params[kParamMessageType] = rclcpp::ParameterValue(std::string(kMessageTypeVariable));
        params[kParamTransport] = rclcpp::ParameterValue(std::string(kTransportTyped));
        return params;
    }

    std::shared_ptr<MockTraceSubscriberNode> create_node(
      const std::map<std::string, rclcpp::ParameterValue> &params) const {
        rclcpp::NodeOptions options;
        options.automatically_declare_parameters_from_overrides(true);
        for (const auto &[key, value] : params) {
            options.parameter_overrides().push_back(rclcpp::Parameter(key, value));
        }
        return std::make_shared<MockTraceSubscriberNode>(options);
    }

    bool wait_for_discovery(const std::shared_ptr<MockTraceSubscriberNode> &node) const {
        return wait_for_discovery(node, pub_);
    }

    bool wait_for_discovery(const std::shared_ptr<MockTraceSubscriberNode> &node,
                            const rclcpp::PublisherBase::SharedPtr &pub) const {
        const auto end_time =
          std::chrono::steady_clock::now() + std::chrono::milliseconds(DISCOVERY_TIMEOUT_MS);
        while (std::chrono::steady_clock::now() < end_time) {
            if (pub->get_subscription_count() > 0) {
                return true;
            }
            rclcpp::spin_some(node);
            std::this_thread::sleep_for(std::chrono::milliseconds(SPIN_SLEEP_MS));
        }
        return false;
    }

    void spin_for(const std::shared_ptr<MockTraceSubscriberNode> &node,
                  std::chrono::milliseconds duration) const {
        const auto end_time = std::chrono::steady_clock::now() + duration;
        while (std::chrono::steady_clock::now() < end_time) {
            rclcpp::spin_some(node);
            std::this_thread::sleep_for(std::chrono::milliseconds(SPIN_SLEEP_MS));
        }
    }

    std::filesystem::path test_dir_;
    rclcpp::Node::SharedPtr publisher_node_;
    rclcpp::Publisher<trace_msgs::msg::TraceMessage>::SharedPtr pub_;
};

TEST_F(MockTraceSubscriberNodeTest, RecordsReceivedMessages) {
    auto node = create_node(default_params());
    ASSERT_TRUE(wait_for_discovery(node));

    for (int i = 0; i < TEST_NUM_MESSAGES; ++i) {
        trace_msgs::msg::TraceMessage msg;
        msg.trace_id = TEST_TRACE_ID_BASE + static_cast<uint64_t>(i);
        msg.header.stamp.sec = i;
        msg.header.stamp.nanosec = 0;
        pub_->publish(msg);
    }
    // The node only flushes its CSV every CSV_FLUSH_EVERY_N rows or on destruction, so there is
    // no observable signal to poll on short of the file itself; spin once for long enough for
    // the (in-process, same-host) callbacks to run, then read the flushed file after teardown.
    spin_for(node, std::chrono::milliseconds(RECEIVE_SPIN_MS));

    node.reset();

    const std::vector<std::string> lines = read_lines(csv_path());
    ASSERT_EQ(lines.size(), static_cast<size_t>(TEST_NUM_MESSAGES + 1));
    EXPECT_EQ(lines.front(), "trace_id,header_stamp_ns,wall_recv_ns,topic,node");

    for (int i = 0; i < TEST_NUM_MESSAGES; ++i) {
        const std::vector<std::string> fields = split_csv_row(lines.at(static_cast<size_t>(i + 1)));
        ASSERT_EQ(fields.size(), 5u);
        EXPECT_EQ(std::stoull(fields.at(0)), TEST_TRACE_ID_BASE + static_cast<uint64_t>(i));
        EXPECT_EQ(std::stoll(fields.at(1)), static_cast<int64_t>(i) * NS_PER_S);
        EXPECT_EQ(fields.at(3), TEST_TOPIC);
    }
}

TEST_F(MockTraceSubscriberNodeTest, ThrowsOnNonPositiveQosDepth) {
    auto params = default_params();
    params[kParamQosDepth] = rclcpp::ParameterValue(0);
    EXPECT_THROW(create_node(params), std::runtime_error);
}

TEST_F(MockTraceSubscriberNodeTest, ThrowsOnUnsupportedTransport) {
    auto params = default_params();
    params[kParamTransport] = rclcpp::ParameterValue(std::string("bogus"));
    EXPECT_THROW(create_node(params), std::invalid_argument);
}

TEST_F(MockTraceSubscriberNodeTest, RecordsReceivedFixedMessages) {
    auto fixed_pub = publisher_node_->create_publisher<trace_msgs::msg::TraceMessageFixed>(
      TEST_TOPIC_FIXED, rclcpp::QoS(TEST_QOS_DEPTH));

    auto params = default_params();
    params["topic"] = rclcpp::ParameterValue(std::string(TEST_TOPIC_FIXED));
    params["message_type"] = rclcpp::ParameterValue(std::string(kMessageTypeFixed));
    auto node = create_node(params);
    ASSERT_TRUE(wait_for_discovery(node, fixed_pub));

    trace_msgs::msg::TraceMessageFixed msg;
    msg.trace_id = TEST_TRACE_ID_BASE;
    msg.header.stamp.sec = 0;
    msg.header.stamp.nanosec = 0;
    fixed_pub->publish(msg);

    spin_for(node, std::chrono::milliseconds(RECEIVE_SPIN_MS));
    node.reset();

    const std::vector<std::string> lines = read_lines(csv_path());
    ASSERT_EQ(lines.size(), 2u);
    const std::vector<std::string> fields = split_csv_row(lines.at(1));
    ASSERT_EQ(fields.size(), 5u);
    EXPECT_EQ(std::stoull(fields.at(0)), TEST_TRACE_ID_BASE);
}

TEST_F(MockTraceSubscriberNodeTest, DeserializesSerializedTransportMessages) {
    auto generic_pub = publisher_node_->create_generic_publisher(TEST_TOPIC, kTraceMessageTypeName,
                                                                 rclcpp::QoS(TEST_QOS_DEPTH));

    auto params = default_params();
    params["transport"] = rclcpp::ParameterValue(std::string(kTransportSerialized));
    auto node = create_node(params);
    ASSERT_TRUE(wait_for_discovery(node, generic_pub));

    trace_msgs::msg::TraceMessage msg;
    msg.trace_id = TEST_TRACE_ID_BASE;
    msg.header.stamp.sec = 0;
    msg.header.stamp.nanosec = 0;

    rclcpp::Serialization<trace_msgs::msg::TraceMessage> serializer;
    rclcpp::SerializedMessage serialized;
    serializer.serialize_message(&msg, &serialized);
    generic_pub->publish(serialized);

    spin_for(node, std::chrono::milliseconds(RECEIVE_SPIN_MS));
    node.reset();

    const std::vector<std::string> lines = read_lines(csv_path());
    ASSERT_EQ(lines.size(), 2u);
    const std::vector<std::string> fields = split_csv_row(lines.at(1));
    ASSERT_EQ(fields.size(), 5u);
    EXPECT_EQ(std::stoull(fields.at(0)), TEST_TRACE_ID_BASE);
}

TEST_F(MockTraceSubscriberNodeTest, DeserializesSerializedTransportFixedMessages) {
    auto generic_pub = publisher_node_->create_generic_publisher(
      TEST_TOPIC_FIXED, kTraceMessageFixedTypeName, rclcpp::QoS(TEST_QOS_DEPTH));

    auto params = default_params();
    params["topic"] = rclcpp::ParameterValue(std::string(TEST_TOPIC_FIXED));
    params["message_type"] = rclcpp::ParameterValue(std::string(kMessageTypeFixed));
    params["transport"] = rclcpp::ParameterValue(std::string(kTransportSerialized));
    auto node = create_node(params);
    ASSERT_TRUE(wait_for_discovery(node, generic_pub));

    trace_msgs::msg::TraceMessageFixed msg;
    msg.trace_id = TEST_TRACE_ID_BASE;
    msg.header.stamp.sec = 0;
    msg.header.stamp.nanosec = 0;

    rclcpp::Serialization<trace_msgs::msg::TraceMessageFixed> serializer;
    rclcpp::SerializedMessage serialized;
    serializer.serialize_message(&msg, &serialized);
    generic_pub->publish(serialized);

    spin_for(node, std::chrono::milliseconds(RECEIVE_SPIN_MS));
    node.reset();

    const std::vector<std::string> lines = read_lines(csv_path());
    ASSERT_EQ(lines.size(), 2u);
    const std::vector<std::string> fields = split_csv_row(lines.at(1));
    ASSERT_EQ(fields.size(), 5u);
    EXPECT_EQ(std::stoull(fields.at(0)), TEST_TRACE_ID_BASE);
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
