// Copyright 2026 Gritt Robotics Inc.

#include <gtest/gtest.h>

#include <cstdint>
#include <stdexcept>
#include <vector>

#include "mock_trace_cpp/message_dispatch.hpp"

namespace mock_trace_cpp {
namespace {

TEST(MessageDispatch, ParseMessageTypeAcceptsVariable) {
    EXPECT_EQ(parse_message_type(kMessageTypeVariable), TraceMessageType::kVariable);
}

TEST(MessageDispatch, ParseMessageTypeAcceptsFixed) {
    EXPECT_EQ(parse_message_type(kMessageTypeFixed), TraceMessageType::kFixed);
}

TEST(MessageDispatch, ParseMessageTypeAcceptsChain) {
    EXPECT_EQ(parse_message_type(kMessageTypeChain), TraceMessageType::kChain);
}

TEST(MessageDispatch, ParseTransportAcceptsTyped) {
    EXPECT_EQ(parse_transport(kTransportTyped), TraceTransport::kTyped);
}

TEST(MessageDispatch, ParseTransportAcceptsSerialized) {
    EXPECT_EQ(parse_transport(kTransportSerialized), TraceTransport::kSerialized);
}

TEST(MessageDispatch, ParseMessageTypeRejectsOutOfSetValue) {
    EXPECT_THROW(parse_message_type("bogus"), std::invalid_argument);
}

TEST(MessageDispatch, ParseTransportRejectsOutOfSetValue) {
    EXPECT_THROW(parse_transport("bogus"), std::invalid_argument);
}

TEST(MessageDispatch, BuildRelayPayloadPadsShortPayloadWithFiller) {
    const std::vector<int32_t> out = build_relay_payload({1, 2, 3}, 5);
    const std::vector<int32_t> expected = {1, 2, 3, kPayloadFillerValue, kPayloadFillerValue};
    EXPECT_EQ(out, expected);
}

TEST(MessageDispatch, BuildRelayPayloadTruncatesLongPayloadToTargetSize) {
    const std::vector<int32_t> out = build_relay_payload({1, 2, 3, 4, 5, 6, 7}, 5);
    const std::vector<int32_t> expected = {1, 2, 3, 4, 5};
    EXPECT_EQ(out, expected);
}

}  // namespace
}  // namespace mock_trace_cpp
