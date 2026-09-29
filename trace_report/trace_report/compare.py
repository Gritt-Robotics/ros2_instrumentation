# Copyright 2026 Gritt Robotics Inc.

"""
compare.py
==========

Aligns a second trace's rows onto a first trace's report, so the frontend can print
both runs' numbers in one report without re-deriving any structure.

A (the control) owns everything structural: the row set, the row order, and every
string. Each of B's (the modified run's) rows is matched to an A row by a stable key
and discarded when it has no counterpart, so what renders is always A's report with
B's numbers overlaid wherever the two runs agree. The result's lists are index-aligned
to the matching lists on the A `Report`, which is the whole contract the frontend
relies on -- it pairs rows by position and never matches anything itself.

Unmatched B rows are counted rather than reported individually: with the row set fixed
to A's, the useful signal is "the two traces did not describe the same system", not
which particular rows differed. A callback label falls back to a raw handle pointer
when a trace never captured that callback's registration, and such a row can never
match, so those are counted separately.
"""

import re
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple

from scipy.stats import mannwhitneyu, ttest_ind
from trace_instrumentation.trace_analysis.reports.model import (
    Callback,
    Chain,
    ChainStep,
    ChainStepRow,
    Edge,
    Metric,
    Publisher,
    Report,
    Throughput,
)

HEX_POINTER_PATTERN = re.compile(r"0x[0-9a-fA-F]+")

# Significance test selectors for _p_value()/build_comparison().
TEST_MANN_WHITNEY = "mann-whitney"
TEST_T = "ttest"

TWO_SIDED = "two-sided"

# A row is flagged significant only when p-value AND effect size agree -- see
# _with_significance().
SIGNIFICANCE_ALPHA = 0.05
MIN_EFFECT_SIZE_PCT = 0.10
# Below this count on either side, no verdict is ventured at all (low power).
MIN_SIGNIFICANCE_SAMPLE_COUNT = 5

EdgeKey = Tuple[str, str, str]
EndpointKey = Tuple[str, str]
ChainKey = Tuple[Tuple[Tuple[str, str], ...], str]
StepShape = Tuple[str, Optional[str], str, bool]


@dataclass(frozen=True)
class ChainComparison:
    """One B chain matched to an A chain. `steps`/`step_endpoints` are None when the
    two chains' steps differ in shape, which makes them un-pairable row for row even
    though the chain itself matched; the summary numbers stay usable in that case."""

    traversal_count: int
    latency: Optional[Metric]
    steps: Optional[List[ChainStep]]
    step_endpoints: Optional[List[ChainStepRow]]


@dataclass(frozen=True)
class Comparison:
    """B's numbers, aligned to an A `Report`. Every list here is the same length as
    its namesake on that Report, holding the matched B row or None at each index."""

    duration_s: float
    edges: List[Optional[Edge]]
    callbacks: List[Optional[Callback]]
    publishers: List[Optional[Publisher]]
    chains: List[Optional[ChainComparison]]
    throughput: Optional[Throughput]
    unmatched_row_count: int
    unstable_label_count: int


def edge_key(edge: Edge) -> EdgeKey:
    """
    Identify an edge across traces.

    Args:
        edge (Edge): The edge to key.

    Returns:
        EdgeKey: The (from_node, to_node, topic) triple, which `build_edges()` already
        guarantees is unique within a report.
    """
    return (edge.from_node, edge.to_node, edge.topic)


def callback_key(callback: Callback) -> EndpointKey:
    """
    Identify a callback across traces.

    Args:
        callback (Callback): The callback to key.

    Returns:
        EndpointKey: The (node, label) pair. `label` is the analyzer's per-node
        callback key, derived from the demangled handler symbol and the target topic.
    """
    return (callback.node, callback.label)


def publisher_key(publisher: Publisher) -> EndpointKey:
    """
    Identify a publisher across traces.

    Args:
        publisher (Publisher): The publisher to key.

    Returns:
        EndpointKey: The (node, label) pair, where `label` is the published topic.
    """
    return (publisher.node, publisher.label)


def chain_key(chain: Chain) -> ChainKey:
    """
    Identify a chain across traces.

    Args:
        chain (Chain): The chain to key.

    Returns:
        ChainKey: Its (node, topic) sequence plus its terminator. The terminator is
        part of the identity because it decides where the chain's latency stops being
        measured, so two chains that disagree on it are not the same measurement.
    """
    return (tuple(tuple(pair) for pair in chain.topic_sequence), chain.terminator)


def _step_shape(step: ChainStep) -> StepShape:
    """
    Reduce a chain step to the properties that decide how it expands into endpoints.

    Args:
        step (ChainStep): The step to reduce.

    Returns:
        StepShape: The (topic, subscribing_node, publishing_node, is_indirect) tuple.
        Two steps agreeing on it produce the same endpoint sequence, and so the same
        table rows.
    """
    return (step.topic, step.subscribing_node, step.publishing_node, step.is_indirect)


def steps_are_pairable(control: Chain, modified: Chain) -> bool:
    """
    Decide whether two matched chains' steps can be shown row for row.

    Args:
        control (Chain): A's chain.
        modified (Chain): B's chain.

    Returns:
        bool: True when both expand into the same endpoint sequence. An indirect step
        that became direct changes the row set, so pairing by position would silently
        compare different intervals.
    """
    if len(control.steps) != len(modified.steps):
        return False
    return all(
        _step_shape(a) == _step_shape(b)
        for a, b in zip(control.steps, modified.steps, strict=False)
    )


def _aligned_callback(control: Callback, modified: Callback, test: str) -> Callback:
    """
    Suppress any of B's numbers that do not measure the same thing as A's, and
    annotate B's metrics with a significance verdict against A's.

    Args:
        control (Callback): A's callback.
        modified (Callback): B's matched callback.
        test (str): TEST_MANN_WHITNEY or TEST_T, passed to _with_significance().

    Returns:
        Callback: B's callback, with `instantaneous_wait_count` cleared when the two
        runs counted gaps against different thresholds (stacking those two counts
        would present readings of different measurements as a comparison), and
        `duration`/`topic_latency`/`interarrival`/`inter_callback_gap` each
        annotated via _with_significance().
    """
    aligned = modified
    if (
        control.instantaneous_wait_threshold_ms
        != modified.instantaneous_wait_threshold_ms
    ):
        aligned = replace(aligned, instantaneous_wait_count=None)
    return replace(
        aligned,
        duration=_with_significance(control.duration, aligned.duration, test),
        topic_latency=_with_significance(
            control.topic_latency, aligned.topic_latency, test
        ),
        interarrival=_with_significance(
            control.interarrival, aligned.interarrival, test
        ),
        inter_callback_gap=_with_significance(
            control.inter_callback_gap, aligned.inter_callback_gap, test
        ),
    )


def _aligned_publisher(control: Publisher, modified: Publisher, test: str) -> Publisher:
    """
    Annotate B's publisher metrics with a significance verdict against A's.

    Args:
        control (Publisher): A's publisher.
        modified (Publisher): B's matched publisher.
        test (str): TEST_MANN_WHITNEY or TEST_T, passed to _with_significance().

    Returns:
        Publisher: B's publisher with `callback_start_to_publish` annotated.
    """
    return replace(
        modified,
        callback_start_to_publish=_with_significance(
            control.callback_start_to_publish, modified.callback_start_to_publish, test
        ),
    )


def _aligned_edge(control: Edge, modified: Edge, test: str) -> Edge:
    """
    Annotate the endpoints a matched edge carries with significance verdicts.

    An edge holds its own references to the publisher and callback it was joined to,
    and `replace()` never mutates, so annotating `Comparison.callbacks`/`publishers`
    cannot reach them -- the topic-stats rows read these nested copies and need their
    own pass.

    Args:
        control (Edge): A's edge.
        modified (Edge): B's matched edge.
        test (str): TEST_MANN_WHITNEY or TEST_T, passed to _with_significance().

    Returns:
        Edge: B's edge with its publisher and callback annotated. An endpoint absent on
        either side is left as B recorded it, there being no counterpart to compare it
        against.
    """
    publisher = modified.publisher
    if publisher is not None and control.publisher is not None:
        publisher = _aligned_publisher(control.publisher, publisher, test)
    callback = modified.callback
    if callback is not None and control.callback is not None:
        callback = _aligned_callback(control.callback, callback, test)
    return replace(modified, publisher=publisher, callback=callback)


def _p_value(a_values: List[float], b_values: List[float], test: str) -> float:
    """
    Compute the two-sided p-value between A's and B's raw samples.

    Args:
        a_values (List[float]): A's (control) raw samples.
        b_values (List[float]): B's (modified) raw samples.
        test (str): TEST_MANN_WHITNEY or TEST_T.

    Returns:
        float: The two-sided p-value.

    Raises:
        ValueError: If `test` names neither known test.
    """
    if test == TEST_MANN_WHITNEY:
        return mannwhitneyu(a_values, b_values, alternative=TWO_SIDED).pvalue
    if test == TEST_T:
        return ttest_ind(a_values, b_values, equal_var=False).pvalue
    raise ValueError(f"unknown significance test: {test!r}")


def _with_significance(
    control: Optional[Metric], modified: Optional[Metric], test: str
) -> Optional[Metric]:
    """
    Annotate B's (modified) metric with a significance verdict against A's.

    Args:
        control (Optional[Metric]): A's matched metric, or None.
        modified (Optional[Metric]): B's metric to annotate, or None.
        test (str): TEST_MANN_WHITNEY or TEST_T.

    Returns:
        Optional[Metric]: `modified` with `significant` set, or `modified`
        unchanged (None stays None) when either side is absent or either has
        fewer than MIN_SIGNIFICANCE_SAMPLE_COUNT samples.
    """
    if control is None or modified is None:
        return modified
    if (
        len(control.values) < MIN_SIGNIFICANCE_SAMPLE_COUNT
        or len(modified.values) < MIN_SIGNIFICANCE_SAMPLE_COUNT
    ):
        return modified
    if control.p50 == 0:
        effect_size_met = modified.p50 != 0
    else:
        effect_size_met = (
            abs(modified.p50 - control.p50) / abs(control.p50) >= MIN_EFFECT_SIZE_PCT
        )
    p_value = _p_value(control.values, modified.values, test)
    significant = bool(p_value < SIGNIFICANCE_ALPHA and effect_size_met)
    return replace(modified, significant=significant)


def _has_unstable_label(key: EndpointKey) -> bool:
    """
    Report whether a row's key embeds a runtime pointer.

    Args:
        key (EndpointKey): The row key to inspect.

    Returns:
        bool: True if any part of the key contains a hex pointer, which varies per
        run and so guarantees the row can never match.
    """
    return any(HEX_POINTER_PATTERN.search(part) is not None for part in key)


def build_comparison(
    control: Report, modified: Report, test: str = TEST_T
) -> Comparison:
    """
    Align B's rows onto A's report.

    Args:
        control (Report): A, the control run. Fixes the row set and row order.
        modified (Report): B, the modified run. Contributes numbers only.
        test (str): Which significance test to annotate matched metrics with --
            TEST_T (default) or TEST_MANN_WHITNEY. See _with_significance().

    Returns:
        Comparison: B's rows, index-aligned to `control`'s edges, callbacks,
        publishers and chains, with None wherever B had no matching row.
    """
    edges_by_key: Dict[EdgeKey, Edge] = {edge_key(e): e for e in modified.edges}
    callbacks_by_key: Dict[EndpointKey, Callback] = {
        callback_key(c): c for c in modified.callbacks
    }
    publishers_by_key: Dict[EndpointKey, Publisher] = {
        publisher_key(p): p for p in modified.publishers
    }
    chains_by_key: Dict[ChainKey, Chain] = {chain_key(c): c for c in modified.chains}

    edges: List[Optional[Edge]] = []
    for edge in control.edges:
        matched_edge = edges_by_key.get(edge_key(edge))
        edges.append(
            None if matched_edge is None else _aligned_edge(edge, matched_edge, test)
        )
    callbacks: List[Optional[Callback]] = []
    for callback in control.callbacks:
        matched = callbacks_by_key.get(callback_key(callback))
        callbacks.append(
            None if matched is None else _aligned_callback(callback, matched, test)
        )
    publishers: List[Optional[Publisher]] = []
    for publisher in control.publishers:
        matched_publisher = publishers_by_key.get(publisher_key(publisher))
        if matched_publisher is None:
            publishers.append(None)
            continue
        publishers.append(_aligned_publisher(publisher, matched_publisher, test))

    chains: List[Optional[ChainComparison]] = []
    for chain in control.chains:
        matched_chain = chains_by_key.get(chain_key(chain))
        if matched_chain is None:
            chains.append(None)
            continue
        pairable = steps_are_pairable(chain, matched_chain)
        step_endpoints = None
        if pairable:
            step_endpoints = [
                replace(
                    b_row, metric=_with_significance(a_row.metric, b_row.metric, test)
                )
                for a_row, b_row in zip(
                    chain.step_endpoints, matched_chain.step_endpoints, strict=True
                )
            ]
        chains.append(
            ChainComparison(
                traversal_count=matched_chain.traversal_count,
                latency=_with_significance(chain.latency, matched_chain.latency, test),
                steps=matched_chain.steps if pairable else None,
                step_endpoints=step_endpoints,
            )
        )

    control_edge_keys = {edge_key(edge) for edge in control.edges}
    control_callback_keys = {callback_key(c) for c in control.callbacks}
    control_publisher_keys = {publisher_key(p) for p in control.publishers}
    control_chain_keys = {chain_key(c) for c in control.chains}

    unmatched_endpoint_keys = [
        key for key in callbacks_by_key if key not in control_callback_keys
    ] + [key for key in publishers_by_key if key not in control_publisher_keys]
    unmatched_row_count = (
        len([key for key in edges_by_key if key not in control_edge_keys])
        + len(unmatched_endpoint_keys)
        + len([key for key in chains_by_key if key not in control_chain_keys])
    )

    return Comparison(
        duration_s=modified.duration_s,
        edges=edges,
        callbacks=callbacks,
        publishers=publishers,
        chains=chains,
        throughput=modified.throughput,
        unmatched_row_count=unmatched_row_count,
        unstable_label_count=len([
            key for key in unmatched_endpoint_keys if _has_unstable_label(key)
        ]),
    )
