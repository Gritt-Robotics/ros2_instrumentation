# Copyright 2026 Gritt Robotics Inc.

"""Shared message_type/transport enum constants and validation for the mock trace nodes."""

MESSAGE_TYPE_VARIABLE = "variable"
MESSAGE_TYPE_FIXED = "fixed"
MESSAGE_TYPE_CHAIN = "chain"
VALID_MESSAGE_TYPES = (MESSAGE_TYPE_VARIABLE, MESSAGE_TYPE_FIXED, MESSAGE_TYPE_CHAIN)

TRANSPORT_TYPED = "typed"
TRANSPORT_SERIALIZED = "serialized"
VALID_TRANSPORTS = (TRANSPORT_TYPED, TRANSPORT_SERIALIZED)


def validate_enum_param(value: str, valid_values: tuple, param_name: str) -> None:
    """
    Validate that a string parameter value is one of a fixed set of allowed values.

    Args:
        value (str): The parameter value to validate.
        valid_values (tuple): The allowed values for this parameter.
        param_name (str): The parameter's name, used in the error message.

    Raises:
        ValueError: If value is not in valid_values.
    """
    if value not in valid_values:
        raise ValueError(
            f"Invalid {param_name} '{value}', expected one of {valid_values}"
        )
