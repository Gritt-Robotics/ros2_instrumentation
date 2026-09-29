# Copyright 2026 Gritt Robotics Inc.

import copy
import dataclasses
import json
from pathlib import Path
from typing import Optional

from trace_instrumentation.trace_analysis.reports.model import (
    Report,
    build_report,
)
from trace_report.bundle import PAYLOAD_COMPARE_KEY, report_payload
from trace_report.compare import (
    TEST_T,
    build_comparison,
    callback_key,
    chain_key,
    edge_key,
    publisher_key,
    steps_are_pairable,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_trace.json"


def _trace() -> dict:
    with open(FIXTURE_PATH) as fh:
        return json.load(fh)


def _report(trace: Optional[dict] = None) -> Report:
    return build_report(_trace() if trace is None else trace)


def _metric_dict(values: list) -> dict:
    """
    Build a Metric-shaped JSON dict, for tests needing control over the raw samples
    a significance test runs against.

    Args:
        values (list): The raw samples the metric summarizes.

    Returns:
        dict: The Metric-shaped dict.
    """
    return {
        "count": len(values),
        "min": min(values),
        "mean": sum(values) / len(values),
        "median": sorted(values)[len(values) // 2],
        "p90": max(values),
        "p99": max(values),
        "max": max(values),
        "values": values,
    }


def test_comparing_a_trace_to_itself_matches_every_row():
    control = _report()
    comparison = build_comparison(control, _report())
    assert comparison.unmatched_row_count == 0
    assert comparison.unstable_label_count == 0
    assert comparison.edges == control.edges
    assert comparison.callbacks == control.callbacks
    assert comparison.publishers == control.publishers


def test_comparison_lists_are_index_aligned_to_the_control():
    control = _report()
    comparison = build_comparison(control, _report())
    assert len(comparison.edges) == len(control.edges)
    assert len(comparison.callbacks) == len(control.callbacks)
    assert len(comparison.publishers) == len(control.publishers)
    assert len(comparison.chains) == len(control.chains)


def test_comparison_carries_the_modified_traces_own_duration():
    trace = _trace()
    trace["duration_s"] = 41.5
    comparison = build_comparison(_report(), _report(trace))
    assert comparison.duration_s == 41.5


def test_a_row_missing_from_the_modified_trace_is_none_at_its_index():
    trace = _trace()
    node = trace["nodes"]["/subscriber"]
    removed = sorted(node["callbacks"].keys())[0]
    del node["callbacks"][removed]

    control = _report()
    comparison = build_comparison(control, _report(trace))
    index = [callback_key(c) for c in control.callbacks].index(("/subscriber", removed))
    assert comparison.callbacks[index] is None
    assert comparison.unmatched_row_count == 0


def test_a_row_only_in_the_modified_trace_is_dropped_and_counted():
    trace = _trace()
    node = trace["nodes"]["/subscriber"]
    existing = sorted(node["callbacks"].keys())[0]
    node["callbacks"]["extra_callback@timer"] = copy.deepcopy(
        node["callbacks"][existing]
    )
    node["callbacks"]["extra_callback@timer"]["callback_type"] = "timer"
    node["callbacks"]["extra_callback@timer"]["topic"] = None

    control = _report()
    comparison = build_comparison(control, _report(trace))
    assert len(comparison.callbacks) == len(control.callbacks)
    assert comparison.unmatched_row_count == 1
    assert comparison.unstable_label_count == 0


def test_an_unmatched_row_named_by_a_handle_pointer_is_counted_separately():
    trace = _trace()
    node = trace["nodes"]["/subscriber"]
    existing = sorted(node["callbacks"].keys())[0]
    node["callbacks"]["sub:0x7f3c1a2b4d00@/step0"] = copy.deepcopy(
        node["callbacks"][existing]
    )

    comparison = build_comparison(_report(), _report(trace))
    assert comparison.unmatched_row_count == 1
    assert comparison.unstable_label_count == 1


def test_a_differing_wait_threshold_suppresses_the_modified_count():
    trace = _trace()
    node = trace["nodes"]["/subscriber"]
    label = sorted(node["callbacks"].keys())[0]
    node["callbacks"][label]["instantaneous_wait_threshold_ms"] = 99.0

    control = _report()
    comparison = build_comparison(control, _report(trace))
    index = [callback_key(c) for c in control.callbacks].index(("/subscriber", label))
    assert control.callbacks[index].instantaneous_wait_count is not None
    assert comparison.callbacks[index].instantaneous_wait_count is None


def test_a_matching_wait_threshold_keeps_the_modified_count():
    control = _report()
    comparison = build_comparison(control, _report())
    counts = [
        row.instantaneous_wait_count for row in comparison.callbacks if row is not None
    ]
    assert counts == [row.instantaneous_wait_count for row in control.callbacks]


def test_chain_steps_pair_when_the_two_chains_have_the_same_shape():
    control = _report()
    comparison = build_comparison(control, _report())
    for chain, compared in zip(control.chains, comparison.chains, strict=False):
        assert compared is not None
        assert compared.steps == chain.steps
        assert compared.step_endpoints == chain.step_endpoints


def test_chain_steps_are_dropped_when_an_indirect_step_became_direct():
    trace = _trace()
    chain = trace["pipeline_chain_latency"][0]
    chain["steps"][0]["is_indirect"] = not chain["steps"][0]["is_indirect"]

    control = _report()
    comparison = build_comparison(control, _report(trace))
    assert comparison.chains[0] is not None
    assert comparison.chains[0].steps is None
    assert comparison.chains[0].step_endpoints is None
    assert comparison.chains[0].traversal_count == control.chains[0].traversal_count


def test_a_chain_whose_terminator_changed_does_not_match():
    trace = _trace()
    trace["pipeline_chain_latency"][0]["terminator"] = "pub"

    control = _report()
    comparison = build_comparison(control, _report(trace))
    assert control.chains[0].terminator != "pub"
    assert comparison.chains[0] is None
    assert comparison.unmatched_row_count == 1


def test_steps_are_pairable_rejects_a_different_step_count():
    chain = _report().chains[1]
    truncated = dataclasses.replace(chain, steps=chain.steps[:-1])
    assert steps_are_pairable(chain, _report().chains[1]) is True
    assert steps_are_pairable(chain, truncated) is False


def test_keys_are_stable_across_two_builds_of_the_same_trace():
    first, second = _report(), _report()
    assert [edge_key(e) for e in first.edges] == [edge_key(e) for e in second.edges]
    assert [callback_key(c) for c in first.callbacks] == [
        callback_key(c) for c in second.callbacks
    ]
    assert [publisher_key(p) for p in first.publishers] == [
        publisher_key(p) for p in second.publishers
    ]
    assert [chain_key(c) for c in first.chains] == [chain_key(c) for c in second.chains]


def test_single_trace_payload_has_no_compare_key():
    assert PAYLOAD_COMPARE_KEY not in report_payload(_report())


def test_ab_payload_carries_the_aligned_compare_block():
    control = _report()
    payload = report_payload(control, build_comparison(control, _report()))
    compare = payload[PAYLOAD_COMPARE_KEY]
    assert len(compare["edges"]) == len(payload["edges"])
    assert len(compare["callbacks"]) == len(payload["callbacks"])
    assert len(compare["chains"]) == len(payload["chains"])
    assert compare["unmatched_row_count"] == 0


def _with_callback_duration(trace: dict, label: str, values: list) -> dict:
    trace["nodes"]["/subscriber"]["callbacks"][label]["duration_ms"] = _metric_dict(
        values
    )
    return trace


def test_significance_flags_clearly_separated_distributions():
    label = sorted(_trace()["nodes"]["/subscriber"]["callbacks"].keys())[0]
    control = _report(_with_callback_duration(_trace(), label, [1.0] * 5))
    modified = _report(_with_callback_duration(_trace(), label, [100.0] * 5))

    comparison = build_comparison(control, modified)
    index = [callback_key(c) for c in control.callbacks].index(("/subscriber", label))
    assert comparison.callbacks[index].duration.significant is True


def test_significance_flags_clearly_separated_distributions_with_ttest():
    # Varied, not exactly-constant, values -- zero variance makes a t-test unstable.
    label = sorted(_trace()["nodes"]["/subscriber"]["callbacks"].keys())[0]
    control = _report(
        _with_callback_duration(_trace(), label, [1.0, 1.1, 0.9, 1.0, 1.05])
    )
    modified = _report(
        _with_callback_duration(_trace(), label, [100.0, 101.0, 99.0, 100.0, 100.5])
    )

    comparison = build_comparison(control, modified, test=TEST_T)
    index = [callback_key(c) for c in control.callbacks].index(("/subscriber", label))
    assert comparison.callbacks[index].duration.significant is True


def test_significance_not_flagged_for_identical_distributions():
    label = sorted(_trace()["nodes"]["/subscriber"]["callbacks"].keys())[0]
    values = [10.0, 12.0, 11.0, 13.0, 9.0]
    control = _report(_with_callback_duration(_trace(), label, values))
    modified = _report(_with_callback_duration(_trace(), label, values))

    comparison = build_comparison(control, modified)
    index = [callback_key(c) for c in control.callbacks].index(("/subscriber", label))
    assert comparison.callbacks[index].duration.significant is False


def test_significance_is_none_below_minimum_sample_size():
    label = sorted(_trace()["nodes"]["/subscriber"]["callbacks"].keys())[0]
    control = _report(_with_callback_duration(_trace(), label, [1.0] * 3))
    modified = _report(_with_callback_duration(_trace(), label, [100.0] * 3))

    comparison = build_comparison(control, modified)
    index = [callback_key(c) for c in control.callbacks].index(("/subscriber", label))
    assert comparison.callbacks[index].duration.significant is None


def test_publisher_significance():
    control_trace = _trace()
    control_trace["nodes"]["/producer"]["publishers"]["/step0"][
        "callback_start_to_publish_ms"
    ] = _metric_dict([1.0] * 5)
    modified_trace = _trace()
    modified_trace["nodes"]["/producer"]["publishers"]["/step0"][
        "callback_start_to_publish_ms"
    ] = _metric_dict([100.0] * 5)

    control = _report(control_trace)
    comparison = build_comparison(control, _report(modified_trace))
    index = [publisher_key(p) for p in control.publishers].index((
        "/producer",
        "/step0",
    ))
    assert comparison.publishers[index].callback_start_to_publish.significant is True


def _edge_index(control: Report) -> int:
    return [edge_key(e) for e in control.edges].index((
        "/producer",
        "/subscriber",
        "/step0",
    ))


def test_edge_callback_gets_significance():
    """The topic-stats table reads an edge's own nested callback, not `callbacks`."""
    label = "_on_message@/step0"
    control_trace = _trace()
    control_trace["nodes"]["/subscriber"]["callbacks"][label]["topic_latency_ms"] = (
        _metric_dict([1.0] * 5)
    )
    modified_trace = _trace()
    modified_trace["nodes"]["/subscriber"]["callbacks"][label]["topic_latency_ms"] = (
        _metric_dict([100.0] * 5)
    )

    control = _report(control_trace)
    comparison = build_comparison(control, _report(modified_trace))
    edge = comparison.edges[_edge_index(control)]
    assert edge.callback.topic_latency.significant is True


def test_edge_publisher_gets_significance():
    """The topic-stats table reads an edge's own nested publisher, not `publishers`."""
    control_trace = _trace()
    control_trace["nodes"]["/producer"]["publishers"]["/step0"][
        "callback_start_to_publish_ms"
    ] = _metric_dict([1.0] * 5)
    modified_trace = _trace()
    modified_trace["nodes"]["/producer"]["publishers"]["/step0"][
        "callback_start_to_publish_ms"
    ] = _metric_dict([100.0] * 5)

    control = _report(control_trace)
    comparison = build_comparison(control, _report(modified_trace))
    edge = comparison.edges[_edge_index(control)]
    assert edge.publisher.callback_start_to_publish.significant is True


def test_edge_significance_matches_the_endpoint_row_for_the_same_metric():
    """A metric shown in two tables must not carry two different verdicts."""
    label = "_on_message@/step0"
    control_trace = _trace()
    control_trace["nodes"]["/subscriber"]["callbacks"][label]["topic_latency_ms"] = (
        _metric_dict([1.0] * 5)
    )
    modified_trace = _trace()
    modified_trace["nodes"]["/subscriber"]["callbacks"][label]["topic_latency_ms"] = (
        _metric_dict([100.0] * 5)
    )

    control = _report(control_trace)
    comparison = build_comparison(control, _report(modified_trace))
    edge = comparison.edges[_edge_index(control)]
    index = [callback_key(c) for c in control.callbacks].index(("/subscriber", label))
    assert (
        edge.callback.topic_latency.significant
        == comparison.callbacks[index].topic_latency.significant
    )


def test_chain_step_endpoints_and_latency_get_significance():
    control_trace = _trace()
    control_chain = control_trace["pipeline_chain_latency"][0]
    control_chain["step_endpoints"][0]["metric_ms"] = _metric_dict([1.0] * 5)
    control_chain["chain_latency_ms"] = _metric_dict([1.0] * 5)

    modified_trace = _trace()
    modified_chain = modified_trace["pipeline_chain_latency"][0]
    modified_chain["step_endpoints"][0]["metric_ms"] = _metric_dict([100.0] * 5)
    modified_chain["chain_latency_ms"] = _metric_dict([100.0] * 5)

    control = _report(control_trace)
    comparison = build_comparison(control, _report(modified_trace))
    assert comparison.chains[0].step_endpoints[0].metric.significant is True
    assert comparison.chains[0].latency.significant is True
