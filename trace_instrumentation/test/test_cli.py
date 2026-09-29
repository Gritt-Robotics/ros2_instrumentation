# Copyright 2026 Gritt Robotics Inc.

from pathlib import Path
from unittest.mock import patch

import pytest
from trace_instrumentation.trace_analysis.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "ctf_min"

# argparse exits with this status when it rejects the command line
ARGPARSE_USAGE_ERROR_EXIT_CODE = 2


def test_main_rejects_an_empty_topic_chain():
    with pytest.raises(SystemExit) as excinfo:
        main([str(FIXTURE), "--topic-chain", ""])
    assert excinfo.value.code == ARGPARSE_USAGE_ERROR_EXIT_CODE


def test_main_rejects_a_publish_terminated_chain_that_pins_a_consumer():
    with pytest.raises(SystemExit) as excinfo:
        main([str(FIXTURE), "--topic-chain", "/step0,/step1@/sink,pub"])
    assert excinfo.value.code == ARGPARSE_USAGE_ERROR_EXIT_CODE


def test_main_rejects_a_terminated_chain_with_only_one_topic():
    with pytest.raises(SystemExit) as excinfo:
        main([str(FIXTURE), "--topic-chain", "/step0,pub"])
    assert excinfo.value.code == ARGPARSE_USAGE_ERROR_EXIT_CODE


def test_main_node_report_forwards_the_chain_terminator(tmp_path):
    report_path = tmp_path / "node_report.txt"
    with (
        patch(
            "trace_instrumentation.trace_analysis.cli.build_node_report"
        ) as mock_build_node_report,
        patch("trace_instrumentation.trace_analysis.cli.write_node_report"),
    ):
        main([
            str(FIXTURE),
            "--node-report",
            str(report_path),
            "--topic-chain",
            "/step0,/step1,pub",
        ])

    assert mock_build_node_report.call_args.kwargs["topic_chains"] == [
        ["/step0", "/step1", "pub"]
    ]


def test_main_rejects_only_topic_chains_without_a_chain():
    with pytest.raises(SystemExit) as excinfo:
        main([str(FIXTURE), "--only-topic-chains"])
    assert excinfo.value.code == ARGPARSE_USAGE_ERROR_EXIT_CODE


def test_main_node_report_forwards_only_topic_chains(tmp_path):
    report_path = tmp_path / "node_report.txt"
    with (
        patch(
            "trace_instrumentation.trace_analysis.cli.build_node_report"
        ) as mock_build_node_report,
        patch("trace_instrumentation.trace_analysis.cli.write_node_report"),
    ):
        main([
            str(FIXTURE),
            "--node-report",
            str(report_path),
            "--topic-chain",
            "/step0,/step1",
            "--only-topic-chains",
        ])

    assert mock_build_node_report.call_args.kwargs["only_topic_chains"] is True


def test_main_node_report_only_topic_chains_defaults_off(tmp_path):
    report_path = tmp_path / "node_report.txt"
    with (
        patch(
            "trace_instrumentation.trace_analysis.cli.build_node_report"
        ) as mock_build_node_report,
        patch("trace_instrumentation.trace_analysis.cli.write_node_report"),
    ):
        main([str(FIXTURE), "--node-report", str(report_path)])

    assert mock_build_node_report.call_args.kwargs["only_topic_chains"] is False


def test_main_topic_chains_alias_is_equivalent_to_topic_chain(tmp_path):
    report_path = tmp_path / "node_report.txt"
    with (
        patch(
            "trace_instrumentation.trace_analysis.cli.build_node_report"
        ) as mock_build_node_report,
        patch("trace_instrumentation.trace_analysis.cli.write_node_report"),
    ):
        main([
            str(FIXTURE),
            "--node-report",
            str(report_path),
            "--topic-chains",
            "/step0,/step1",
        ])

    assert mock_build_node_report.call_args.kwargs["topic_chains"] == [
        ["/step0", "/step1"]
    ]


def test_main_accepts_several_topic_chains(tmp_path):
    report_path = tmp_path / "node_report.txt"
    with (
        patch(
            "trace_instrumentation.trace_analysis.cli.build_node_report"
        ) as mock_build_node_report,
        patch("trace_instrumentation.trace_analysis.cli.write_node_report"),
    ):
        main([
            str(FIXTURE),
            "--node-report",
            str(report_path),
            "--topic-chain",
            "/step0,/step1",
            "--topic-chain",
            "/step2,/step3",
        ])

    assert mock_build_node_report.call_args.kwargs["topic_chains"] == [
        ["/step0", "/step1"],
        ["/step2", "/step3"],
    ]


def test_main_node_report_forwards_topic_chain(tmp_path):
    # Regression: --node-report once ignored --topic-chain entirely, so the JSON's
    # pipeline_chain_latency was always empty whatever chain was requested.
    report_path = tmp_path / "node_report.txt"
    with (
        patch(
            "trace_instrumentation.trace_analysis.cli.build_node_report"
        ) as mock_build_node_report,
        patch("trace_instrumentation.trace_analysis.cli.write_node_report"),
    ):
        main([
            str(FIXTURE),
            "--node-report",
            str(report_path),
            "--topic-chain",
            "/step0,/step1",
        ])

    mock_build_node_report.assert_called_once()
    assert mock_build_node_report.call_args.kwargs["topic_chains"] == [
        ["/step0", "/step1"]
    ]


def test_main_node_report_forwards_ignore_flags(tmp_path):
    report_path = tmp_path / "node_report.txt"
    with (
        patch(
            "trace_instrumentation.trace_analysis.cli.build_node_report"
        ) as mock_build_node_report,
        patch("trace_instrumentation.trace_analysis.cli.write_node_report"),
    ):
        main([
            str(FIXTURE),
            "--node-report",
            str(report_path),
            "--ignore-nodes",
            "/foo_*",
        ])

    mock_build_node_report.assert_called_once()
    kwargs = mock_build_node_report.call_args.kwargs
    assert kwargs["ignore_nodes"] == ["/foo_*"]
