# Copyright 2026 Gritt Robotics Inc.

import pytest
from mock_trace_py.message_dispatch import (
    MESSAGE_TYPE_CHAIN,
    MESSAGE_TYPE_FIXED,
    MESSAGE_TYPE_VARIABLE,
    TRANSPORT_SERIALIZED,
    TRANSPORT_TYPED,
    VALID_MESSAGE_TYPES,
    VALID_TRANSPORTS,
    validate_enum_param,
)

PARAM_NAME_MESSAGE_TYPE = "message_type"
PARAM_NAME_TRANSPORT = "transport"

NEWLY_SUPPORTED_MESSAGE_TYPES = (MESSAGE_TYPE_FIXED, MESSAGE_TYPE_CHAIN)
NEWLY_SUPPORTED_TRANSPORTS = (TRANSPORT_SERIALIZED,)


def test_validate_enum_param_accepts_valid_message_type():
    validate_enum_param(
        MESSAGE_TYPE_VARIABLE, VALID_MESSAGE_TYPES, PARAM_NAME_MESSAGE_TYPE
    )


def test_validate_enum_param_accepts_valid_transport():
    validate_enum_param(TRANSPORT_TYPED, VALID_TRANSPORTS, PARAM_NAME_TRANSPORT)


def test_validate_enum_param_rejects_out_of_set_value():
    with pytest.raises(ValueError):
        validate_enum_param("bogus", VALID_MESSAGE_TYPES, PARAM_NAME_MESSAGE_TYPE)


def test_validate_enum_param_rejects_out_of_set_transport():
    with pytest.raises(ValueError):
        validate_enum_param("bogus", VALID_TRANSPORTS, PARAM_NAME_TRANSPORT)


@pytest.mark.parametrize("message_type", NEWLY_SUPPORTED_MESSAGE_TYPES)
def test_validate_enum_param_accepts_newly_supported_message_types(message_type):
    validate_enum_param(message_type, VALID_MESSAGE_TYPES, PARAM_NAME_MESSAGE_TYPE)


@pytest.mark.parametrize("transport", NEWLY_SUPPORTED_TRANSPORTS)
def test_validate_enum_param_accepts_newly_supported_transports(transport):
    validate_enum_param(transport, VALID_TRANSPORTS, PARAM_NAME_TRANSPORT)
