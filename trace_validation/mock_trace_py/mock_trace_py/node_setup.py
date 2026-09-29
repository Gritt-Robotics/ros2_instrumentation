# Copyright 2026 Gritt Robotics Inc.

"""Shared parameter-validation and ground-truth CSV setup for the mock trace nodes."""

import csv
import os
from typing import Any, Dict, List, TextIO, Tuple

import rclpy
from rclpy.node import Node

NANOSECONDS_PER_SECOND = 1_000_000_000

# Ground-truth CSV column names shared by the publisher and subscriber nodes.
CSV_COL_TRACE_ID = "trace_id"
CSV_COL_HEADER_STAMP_NS = "header_stamp_ns"
CSV_COL_TOPIC = "topic"
CSV_COL_NODE = "node"

# Appended to the header when message_type is MESSAGE_TYPE_CHAIN.
CSV_COL_CHAIN_ID = "chain_id"
CSV_COL_STEP_INDEX = "step_index"
CSV_COL_UPSTREAM_TRACE_ID = "upstream_trace_id"
CSV_CHAIN_COLUMNS = [CSV_COL_CHAIN_ID, CSV_COL_STEP_INDEX, CSV_COL_UPSTREAM_TRACE_ID]

# Ground-truth CSV columns specific to the publisher node.
CSV_COL_WALL_PUBLISH_NS = "wall_publish_ns"
CSV_COL_PAYLOAD_LEN = "payload_len"
PUBLISHER_CSV_HEADER = [
    CSV_COL_TRACE_ID,
    CSV_COL_HEADER_STAMP_NS,
    CSV_COL_WALL_PUBLISH_NS,
    CSV_COL_TOPIC,
    CSV_COL_NODE,
    CSV_COL_PAYLOAD_LEN,
]
PUBLISHER_CSV_HEADER_CHAIN = PUBLISHER_CSV_HEADER + CSV_CHAIN_COLUMNS

# Ground-truth CSV column specific to the subscriber node.
CSV_COL_WALL_RECV_NS = "wall_recv_ns"
SUBSCRIBER_CSV_HEADER = [
    CSV_COL_TRACE_ID,
    CSV_COL_HEADER_STAMP_NS,
    CSV_COL_WALL_RECV_NS,
    CSV_COL_TOPIC,
    CSV_COL_NODE,
]
SUBSCRIBER_CSV_HEADER_CHAIN = SUBSCRIBER_CSV_HEADER + CSV_CHAIN_COLUMNS

CSV_COL_UPSTREAM_HEADER_STAMP_NS = "upstream_header_stamp_ns"
CSV_COL_TOPIC_IN = "topic_in"
CSV_COL_TOPIC_OUT = "topic_out"
CSV_COL_PAYLOAD_LEN_IN = "payload_len_in"
CSV_COL_PAYLOAD_LEN_OUT = "payload_len_out"
RELAY_CSV_HEADER_OWN = [
    CSV_COL_TRACE_ID,
    CSV_COL_HEADER_STAMP_NS,
    CSV_COL_WALL_PUBLISH_NS,
    CSV_COL_UPSTREAM_HEADER_STAMP_NS,
    CSV_COL_WALL_RECV_NS,
    CSV_COL_TOPIC_IN,
    CSV_COL_TOPIC_OUT,
    CSV_COL_NODE,
    CSV_COL_PAYLOAD_LEN_IN,
    CSV_COL_PAYLOAD_LEN_OUT,
]
RELAY_CSV_HEADER = RELAY_CSV_HEADER_OWN + CSV_CHAIN_COLUMNS


def declare_and_load_parameters(
    node: Node, params: Dict[str, rclpy.Parameter.Type]
) -> None:
    """
    Declare parameters on a node and bind each value to an attribute of the same name.

    Declaring a type carries no default, so every parameter listed must be supplied at
    launch: rclpy raises ParameterUninitializedException on read rather than handing
    back a zero value that would pass the nodes' own range checks.

    Args:
        node (Node): The node to declare the parameters on, and whose attributes are
            set to the resolved values.
        params (Dict[str, rclpy.Parameter.Type]): Parameter names mapped to the type
            each is declared with.
    """
    node.declare_parameters(namespace="", parameters=list(params.items()))
    for param_name in params:
        setattr(node, param_name, node.get_parameter(param_name).value)


def validate_non_empty_string_param(value: str, param_name: str) -> None:
    """
    Validate that a string parameter value is non-empty.

    Args:
        value (str): The parameter value to validate.
        param_name (str): The parameter's name, used in the error message.

    Raises:
        ValueError: If value is empty.
    """
    if len(value) == 0:
        raise ValueError(f"{param_name} must be a non-empty string")


def open_ground_truth_csv(output_csv: str, header: List[str]) -> Tuple[TextIO, Any]:
    """
    Create parent directories if needed, open output_csv for writing, and write header.

    Args:
        output_csv (str): Path to the ground-truth CSV file to create.
        header (List[str]): Column names to write as the first row.

    Returns:
        Tuple[TextIO, Any]: The open file handle and its csv.writer() instance
            (the writer is untyped: the csv module exposes no public writer type).
    """
    csv_dir = os.path.dirname(output_csv)
    if len(csv_dir) > 0:
        os.makedirs(csv_dir, exist_ok=True)

    csv_file = open(output_csv, "w", newline="")  # noqa: SIM115
    csv_writer = csv.writer(csv_file)
    csv_writer.writerow(header)
    return csv_file, csv_writer


def close_ground_truth_csv(csv_file: TextIO) -> None:
    """
    Flush and close a ground-truth CSV file.

    Args:
        csv_file (TextIO): The open file handle to flush and close.
    """
    csv_file.flush()
    csv_file.close()
