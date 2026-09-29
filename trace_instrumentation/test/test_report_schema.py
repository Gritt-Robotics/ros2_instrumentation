# Copyright 2026 Gritt Robotics Inc.

import copy
import json
from pathlib import Path

import pytest
from trace_instrumentation.trace_analysis.reports.schema import (
    SchemaError,
    validate_trace,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_trace.json"


def _load_fixture() -> dict:
    with open(FIXTURE_PATH) as fh:
        return json.load(fh)


def test_valid_fixture_passes():
    trace = _load_fixture()
    validate_trace(trace)


def test_missing_duration_s_raises():
    trace = _load_fixture()
    del trace["duration_s"]
    with pytest.raises(SchemaError, match="duration_s"):
        validate_trace(trace)


def test_missing_metric_field_raises_with_path():
    trace = _load_fixture()
    del trace["nodes"]["/producer"]["callbacks"]["_tick@timer"]["duration_ms"]["mean"]
    with pytest.raises(SchemaError, match=r"nodes\[/producer\].*duration_ms.*mean"):
        validate_trace(trace)


def test_missing_received_count_raises():
    trace = _load_fixture()
    del trace["nodes"]["/producer"]["publishers"]["/step0"]["received_count"]
    with pytest.raises(SchemaError, match="received_count"):
        validate_trace(trace)


def test_non_integer_published_count_raises():
    trace = _load_fixture()
    trace["nodes"]["/producer"]["publishers"]["/step0"]["published_count"] = 1.5
    with pytest.raises(SchemaError, match="published_count"):
        validate_trace(trace)


def test_bool_published_count_raises():
    # bool is a subclass of int in Python, so isinstance(True, int) is True --
    # explicitly reject it rather than silently accepting it as 0/1.
    trace = _load_fixture()
    trace["nodes"]["/producer"]["publishers"]["/step0"]["published_count"] = True
    with pytest.raises(SchemaError, match="published_count"):
        validate_trace(trace)


def test_negative_received_count_raises():
    trace = _load_fixture()
    trace["nodes"]["/producer"]["publishers"]["/step0"]["received_count"] = -1
    with pytest.raises(SchemaError, match="received_count"):
        validate_trace(trace)


def test_received_count_exceeding_published_count_raises():
    # Rcv is matched takes, so it can never exceed Pub.
    trace = _load_fixture()
    publisher = trace["nodes"]["/producer"]["publishers"]["/step0"]
    publisher["published_count"] = 10
    publisher["received_count"] = 11
    with pytest.raises(SchemaError, match="received_count"):
        validate_trace(trace)


def test_dropped_missing_pct_raises():
    trace = _load_fixture()
    del trace["nodes"]["/producer"]["publishers"]["/step0"]["dropped"]["pct"]
    with pytest.raises(SchemaError, match="dropped"):
        validate_trace(trace)


def test_missing_subscription_topic_latency_raises():
    trace = _load_fixture()
    del trace["nodes"]["/subscriber"]["callbacks"]["_on_message@/step0"][
        "topic_latency_ms"
    ]
    with pytest.raises(SchemaError, match="topic_latency_ms"):
        validate_trace(trace)


def test_pipeline_chain_latency_missing_steps_raises():
    trace = _load_fixture()
    del trace["pipeline_chain_latency"][0]["steps"]
    with pytest.raises(SchemaError, match="steps"):
        validate_trace(trace)


def test_pipeline_chain_latency_missing_terminator_raises():
    trace = _load_fixture()
    del trace["pipeline_chain_latency"][0]["terminator"]
    with pytest.raises(SchemaError, match="terminator"):
        validate_trace(trace)


def test_pipeline_chain_latency_unknown_terminator_raises():
    trace = _load_fixture()
    trace["pipeline_chain_latency"][0]["terminator"] = "sink"
    with pytest.raises(SchemaError, match="terminator"):
        validate_trace(trace)


def test_pipeline_chain_latency_defaults_to_absent():
    trace = _load_fixture()
    del trace["pipeline_chain_latency"]
    validate_trace(trace)  # must not raise -- chains are optional


def test_pipeline_chain_latency_missing_step_endpoints_raises():
    trace = _load_fixture()
    del trace["pipeline_chain_latency"][0]["step_endpoints"]
    with pytest.raises(SchemaError, match="step_endpoints"):
        validate_trace(trace)


def test_step_endpoint_row_missing_from_raises():
    trace = _load_fixture()
    del trace["pipeline_chain_latency"][0]["step_endpoints"][0]["from"]
    with pytest.raises(SchemaError, match="from"):
        validate_trace(trace)


def test_step_endpoint_unknown_kind_raises():
    trace = _load_fixture()
    trace["pipeline_chain_latency"][0]["step_endpoints"][0]["from"]["kind"] = "landing"
    with pytest.raises(SchemaError, match="kind"):
        validate_trace(trace)


def test_step_endpoint_row_null_metric_is_valid():
    trace = _load_fixture()
    trace["pipeline_chain_latency"][0]["step_endpoints"][0]["metric_ms"] = None
    validate_trace(trace)  # must not raise -- a row's metric may be absent


def test_mismatched_metric_array_type_raises():
    trace = _load_fixture()
    trace["nodes"]["/producer"]["callbacks"]["_tick@timer"]["duration_ms"] = (
        "not-a-dict"
    )
    with pytest.raises(SchemaError, match="duration_ms"):
        validate_trace(trace)


def test_metric_non_int_count_raises():
    trace = _load_fixture()
    trace["nodes"]["/producer"]["callbacks"]["_tick@timer"]["duration_ms"]["count"] = (
        "4"
    )
    with pytest.raises(SchemaError, match=r"duration_ms\.count.*int"):
        validate_trace(trace)


def test_metric_non_numeric_field_raises():
    trace = _load_fixture()
    trace["nodes"]["/producer"]["callbacks"]["_tick@timer"]["duration_ms"]["mean"] = (
        "not-a-number"
    )
    with pytest.raises(SchemaError, match=r"duration_ms\.mean.*number"):
        validate_trace(trace)


def test_metric_non_numeric_p90_raises():
    trace = _load_fixture()
    trace["nodes"]["/producer"]["callbacks"]["_tick@timer"]["duration_ms"]["p90"] = (
        "not-a-number"
    )
    with pytest.raises(SchemaError, match=r"duration_ms\.p90.*number"):
        validate_trace(trace)


def test_metric_missing_p90_is_valid():
    trace = _load_fixture()
    del trace["nodes"]["/producer"]["callbacks"]["_tick@timer"]["duration_ms"]["p90"]
    validate_trace(trace)  # must not raise -- p90 is optional


def test_metric_missing_values_is_valid():
    trace = _load_fixture()
    validate_trace(trace)  # must not raise -- the fixture predates `values`


def test_metric_non_list_values_raises():
    trace = _load_fixture()
    trace["nodes"]["/producer"]["callbacks"]["_tick@timer"]["duration_ms"]["values"] = (
        "not-a-list"
    )
    with pytest.raises(SchemaError, match=r"duration_ms\.values.*list"):
        validate_trace(trace)


def test_metric_non_numeric_values_entry_raises():
    trace = _load_fixture()
    trace["nodes"]["/producer"]["callbacks"]["_tick@timer"]["duration_ms"]["values"] = [
        1.0,
        "not-a-number",
    ]
    with pytest.raises(SchemaError, match=r"duration_ms\.values\[1\].*number"):
        validate_trace(trace)


def test_deep_copy_independence():
    # sanity check for the other tests: mutating a loaded copy must not affect a
    # second independently-loaded copy.
    a = _load_fixture()
    b = copy.deepcopy(a)
    del a["duration_s"]
    assert "duration_s" in b


def _valid_throughput():
    return {
        "bucket_interval_s": 1.0,
        "bucket_starts_s": [0.0, 1.0],
        "total_hz": [2.0, 1.0],
        "per_topic_hz": {"/a": [2.0, 1.0]},
    }


def test_validate_trace_accepts_valid_throughput():
    trace = {"duration_s": 2.0, "nodes": {}, "throughput": _valid_throughput()}
    validate_trace(trace)  # must not raise


def test_validate_trace_accepts_trace_without_throughput():
    validate_trace({"duration_s": 2.0, "nodes": {}})  # optional block


def test_validate_trace_rejects_ragged_throughput_series():
    bad = _valid_throughput()
    bad["per_topic_hz"]["/a"] = [2.0]  # wrong length vs bucket_starts_s
    trace = {"duration_s": 2.0, "nodes": {}, "throughput": bad}
    with pytest.raises(SchemaError):
        validate_trace(trace)
