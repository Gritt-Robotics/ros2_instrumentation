# Copyright 2026 Gritt Robotics Inc.

"""
markdown_report.py
===================

Renders sections 01 (topic stats), 02 (node analysis) and 03 (chain latency) of a
`Report` -- optionally overlaid with a second trace's `Comparison` -- as GitHub-
flavored Markdown tables. No topology diagram and no throughput chart: both are
visual-only in the HTML report (a Mermaid graph, an SVG line chart) and have no
useful plain-text form.
"""

from collections import defaultdict
from typing import Callable, Dict, List, Optional, Tuple

from trace_instrumentation.trace_analysis.graph import CHAIN_TERMINATOR_PUB
from trace_instrumentation.trace_analysis.reports.model import (
    Callback,
    Chain,
    ChainEndpoint,
    ChainStepRow,
    Edge,
    Metric,
    Report,
)

from trace_report.compare import ChainComparison, Comparison

EM_DASH = "—"
DECIMAL_PLACES = 2
DEFAULT_TITLE = "Pipeline Trace Schedule"

CALLBACK_TYPE_TIMER = "timer"
CALLBACK_TYPE_SUBSCRIPTION = "subscription"
TIMER_FUNCTION_CALL_LABEL = "Timer callback"
SUBSCRIBER_FUNCTION_CALL_LABEL = "Subscriber callback"
PUBLISH_FUNCTION_CALL_LABEL = "Publish"

CONTROL_LABEL = "A"
MODIFIED_LABEL = "B"
# Bold marks a compare.py-flagged shift; Markdown has no equivalent of report.css's
# ".significant" tint.
SIGNIFICANT_MARKER = "**"

# Table headers, one constant per rendered table -- column order must match the row
# lists each render_*() builds.
TOPIC_STATS_HEADERS = [
    "Source",
    "Destination",
    "Topic",
    "Pub",
    "Rcv",
    "Dropped",
    "Publish p50 (ms)",
    "Publish p90 (ms)",
    "Topic latency p50 (ms)",
    "Topic latency p90 (ms)",
]
NODE_ANALYSIS_HEADERS = [
    "Node",
    "Function call",
    "Topic",
    "Hz",
    "Duration p50 (ms)",
    "Duration p90 (ms)",
    "Interarrival p50 (ms)",
    "Interarrival p90 (ms)",
    "Gap p50 (ms)",
    "Gap p90 (ms)",
    "Instant waits",
]
CHAIN_LATENCY_HEADERS = ["Chain", "Traversals", "Latency p50 (ms)", "Latency p90 (ms)"]
CHAIN_STEPS_HEADERS = ["From", "To", "Latency p50 (ms)", "Latency p90 (ms)"]


def _fmt(value: Optional[float]) -> str:
    """
    Format a numeric value, or an em dash if absent.

    Args:
        value (Optional[float]): The value to format, or None.

    Returns:
        str: `value` to DECIMAL_PLACES, or EM_DASH.
    """
    if value is None:
        return EM_DASH
    return f"{value:.{DECIMAL_PLACES}f}"


def _compared_text(
    a_text: str,
    b_row: Optional[object],
    text_of: Callable[[object], str],
    compare: bool,
) -> str:
    """
    Combine A's formatted text with B's, mirroring report.js's `comparedValue()` /
    `abCell()`.

    Args:
        a_text (str): A's (control) formatted value.
        b_row (Optional[object]): B's matched row, or None where nothing matched.
        text_of (Callable[[object], str]): Reads the displayed value off a matched
            (non-None) row.
        compare (bool): Whether this is an A/B report.

    Returns:
        str: `a_text` alone outside an A/B report or when both sides are absent,
        otherwise "a_text / b_text".
    """
    if not compare:
        return a_text
    b_text = EM_DASH if b_row is None else text_of(b_row)
    if a_text == EM_DASH and b_text == EM_DASH:
        return a_text
    return f"{a_text} / {b_text}"


def _metric_cells(
    metric: Optional[Metric], b_metric: Optional[Metric], compare: bool
) -> Tuple[str, str]:
    """
    Render one metric's p50/p90 cells, combining A's and B's values.

    Args:
        metric (Optional[Metric]): A's metric, or None.
        b_metric (Optional[Metric]): B's matched metric in an A/B report; None both
            outside one and where B had no matching row.
        compare (bool): Whether this is an A/B report.

    Returns:
        Tuple[str, str]: (p50 cell, p90 cell), B's side bolded when
            `b_metric.significant` is True.
    """
    p50 = _fmt(metric.p50) if metric is not None else EM_DASH
    p90 = _fmt(metric.p90) if metric is not None else EM_DASH
    if not compare:
        return p50, p90
    b_p50 = EM_DASH if b_metric is None else _fmt(b_metric.p50)
    b_p90 = EM_DASH if b_metric is None else _fmt(b_metric.p90)
    if b_metric is not None and b_metric.significant is True:
        b_p50 = f"{SIGNIFICANT_MARKER}{b_p50}{SIGNIFICANT_MARKER}"
        b_p90 = f"{SIGNIFICANT_MARKER}{b_p90}{SIGNIFICANT_MARKER}"
    if p50 == EM_DASH and b_p50 == EM_DASH:
        return p50, p90
    return f"{p50} / {b_p50}", f"{p90} / {b_p90}"


def _md_table(headers: List[str], rows: List[List[str]]) -> str:
    """
    Render a GitHub-flavored Markdown table.

    Args:
        headers (List[str]): Column headers.
        rows (List[List[str]]): Row cells, each the same length as `headers`.

    Returns:
        str: The rendered table, one trailing newline.
    """
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def _rows_by_node(rows: List[object]) -> Dict[str, List[Tuple[int, object]]]:
    """
    Group rows by their `.node`, keeping each row's original index.

    Args:
        rows (List[object]): `Callback` or `Publisher` rows.

    Returns:
        Dict[str, List[Tuple[int, object]]]: Node name -> [(original index, row)],
        in original order within a node -- rows are already grouped by node and
        sorted by label upstream (see `model.py`'s `callback_rows()`).
    """
    grouped: Dict[str, List[Tuple[int, object]]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[row.node].append((index, row))
    return grouped


# ---- Section 01: topic stats -------------------------------------------------


def _edge_received_count(edge: Edge) -> Optional[int]:
    """
    Resolve how many of a edge's messages its destination received, as the number of
    times that destination's callback ran. Mirrors report.js's edgeReceivedCount().

    Args:
        edge (Edge): The edge to describe.

    Returns:
        Optional[int]: The callback's invocation count, capped at the edge's
        published count (one callback can serve every publisher of a topic, so its
        raw count can exceed this one edge's Pub); None if the destination runs no
        traced callback.
    """
    if edge.callback is None or edge.callback.duration is None:
        return None
    if edge.publisher is None:
        return edge.callback.duration.count
    return min(edge.callback.duration.count, edge.publisher.published_count)


def _edge_dropped(edge: Edge) -> Optional[Tuple[int, float]]:
    """
    Compute one edge's own dropped-message count and rate. Mirrors report.js's
    edgeDropped().

    Args:
        edge (Edge): The edge to describe.

    Returns:
        Optional[Tuple[int, float]]: (count, pct), or None where the edge's own share
        is not attributable (no publisher, no traced destination callback, or
        nothing published).
    """
    received = _edge_received_count(edge)
    if (
        edge.publisher is None
        or received is None
        or edge.publisher.published_count == 0
    ):
        return None
    dropped_count = edge.publisher.published_count - received
    return dropped_count, (dropped_count / edge.publisher.published_count) * 100


def _edge_published_text(edge: Edge) -> str:
    return EM_DASH if edge.publisher is None else str(edge.publisher.published_count)


def _edge_received_text(edge: Edge) -> str:
    received = _edge_received_count(edge)
    return EM_DASH if received is None else str(received)


def _edge_dropped_text(edge: Edge) -> str:
    dropped = _edge_dropped(edge)
    if dropped is None:
        return EM_DASH
    count, pct = dropped
    return f"{count} ({_fmt(pct)}%)"


def render_topic_stats(report: Report, comparison: Optional[Comparison] = None) -> str:
    """
    Render section 01 as a Markdown table: one row per edge.

    Args:
        report (Report): The report to render. In an A/B report this is A, the
            control.
        comparison (Optional[Comparison]): B's rows aligned to `report.edges`, or
            None for a single-trace report.

    Returns:
        str: The rendered "## 01 - Topic stats" section.
    """
    heading = "## 01 · Topic stats\n\n"
    if len(report.edges) == 0:
        return heading + "_No edges recorded._\n"

    compare = comparison is not None
    rows = []
    for index, edge in enumerate(report.edges):
        b_edge = comparison.edges[index] if compare else None
        pub_metric = (
            edge.publisher.callback_start_to_publish if edge.publisher else None
        )
        b_pub_metric = (
            b_edge.publisher.callback_start_to_publish
            if b_edge is not None and b_edge.publisher is not None
            else None
        )
        lat_metric = edge.callback.topic_latency if edge.callback else None
        b_lat_metric = (
            b_edge.callback.topic_latency
            if b_edge is not None and b_edge.callback is not None
            else None
        )
        pub_p50, pub_p90 = _metric_cells(pub_metric, b_pub_metric, compare)
        lat_p50, lat_p90 = _metric_cells(lat_metric, b_lat_metric, compare)
        rows.append([
            edge.from_node,
            edge.to_node,
            edge.topic,
            _compared_text(
                _edge_published_text(edge), b_edge, _edge_published_text, compare
            ),
            _compared_text(
                _edge_received_text(edge), b_edge, _edge_received_text, compare
            ),
            _compared_text(
                _edge_dropped_text(edge), b_edge, _edge_dropped_text, compare
            ),
            pub_p50,
            pub_p90,
            lat_p50,
            lat_p90,
        ])
    return heading + _md_table(TOPIC_STATS_HEADERS, rows)


# ---- Section 02: node analysis ------------------------------------------------


def _wait_count_text(callback: Callback) -> str:
    """
    Count one callback's inter-callback gaps below the report's threshold.

    Args:
        callback (Callback): The callback to describe.

    Returns:
        str: The count, or EM_DASH where it was not measured -- which in an A/B
        report also covers B counting against a different threshold (see
        compare.py's `_aligned_callback()`).
    """
    if callback.instantaneous_wait_count is None:
        return EM_DASH
    return str(callback.instantaneous_wait_count)


def render_node_analysis(
    report: Report, comparison: Optional[Comparison] = None
) -> str:
    """
    Render section 02 as a Markdown table: one row per callback and per publisher,
    grouped by node (topological order).

    Args:
        report (Report): The report to render. In an A/B report this is A, the
            control.
        comparison (Optional[Comparison]): B's rows aligned to `report.callbacks` /
            `report.publishers`, or None for a single-trace report.

    Returns:
        str: The rendered "## 02 - Node analysis" section.
    """
    heading = "## 02 · Node analysis\n\n"
    compare = comparison is not None
    callbacks_by_node = _rows_by_node(report.callbacks)
    publishers_by_node = _rows_by_node(report.publishers)

    rows: List[List[str]] = []
    for node_name in report.node_names:
        for index, callback in callbacks_by_node.get(node_name, []):
            if callback.callback_type == CALLBACK_TYPE_TIMER:
                function_call = TIMER_FUNCTION_CALL_LABEL
            elif callback.callback_type == CALLBACK_TYPE_SUBSCRIPTION:
                function_call = SUBSCRIBER_FUNCTION_CALL_LABEL
            else:
                continue
            b_callback = comparison.callbacks[index] if compare else None
            hz = _compared_text(
                _fmt(callback.callback_hz),
                b_callback,
                lambda c: _fmt(c.callback_hz),
                compare,
            )
            duration = _metric_cells(
                callback.duration, b_callback.duration if b_callback else None, compare
            )
            interarrival = _metric_cells(
                callback.interarrival,
                b_callback.interarrival if b_callback else None,
                compare,
            )
            gap = _metric_cells(
                callback.inter_callback_gap,
                b_callback.inter_callback_gap if b_callback else None,
                compare,
            )
            waits = _compared_text(
                _wait_count_text(callback), b_callback, _wait_count_text, compare
            )
            rows.append([
                node_name,
                function_call,
                callback.topic or EM_DASH,
                hz,
                *duration,
                *interarrival,
                *gap,
                waits,
            ])
        for index, publisher in publishers_by_node.get(node_name, []):
            b_publisher = comparison.publishers[index] if compare else None
            hz = _compared_text(
                _fmt(publisher.publisher_hz),
                b_publisher,
                lambda p: _fmt(p.publisher_hz),
                compare,
            )
            waits = _compared_text(EM_DASH, b_publisher, lambda _p: EM_DASH, compare)
            rows.append([
                node_name,
                PUBLISH_FUNCTION_CALL_LABEL,
                publisher.topic,
                hz,
                EM_DASH,
                EM_DASH,
                EM_DASH,
                EM_DASH,
                EM_DASH,
                EM_DASH,
                waits,
            ])

    if len(rows) == 0:
        return heading + "_No callbacks or publishers recorded._\n"
    return heading + _md_table(NODE_ANALYSIS_HEADERS, rows)


# ---- Section 03: chain latency -------------------------------------------------


def _chain_label(chain: Chain) -> str:
    """
    Build a chain's one-line label: its arrow-joined topics, plus where a
    pub-terminated chain ends.

    Args:
        chain (Chain): The chain to label.

    Returns:
        str: The arrow-joined topic path.
    """
    path = " → ".join(step.topic for step in chain.steps)
    if chain.terminator == CHAIN_TERMINATOR_PUB:
        return f"{path} publish"
    return path


def _endpoint_text(endpoint: ChainEndpoint) -> str:
    """
    Build one endpoint's cell text: its topic and moment, plus the node it happens
    in. Mirrors report.js's endpointCell().

    Args:
        endpoint (ChainEndpoint): The endpoint to describe.

    Returns:
        str: "{topic} {kind} ({node})", or without the node when absent.
    """
    if endpoint.node is None:
        return f"{endpoint.topic} {endpoint.kind}"
    return f"{endpoint.topic} {endpoint.kind} ({endpoint.node})"


def _compared_step_rows(
    b_chain: Optional[ChainComparison],
) -> Optional[List[ChainStepRow]]:
    """
    Resolve B's step_endpoints rows for one chain, paired with A's by index. Mirrors
    report.js's comparedStepRows().

    Args:
        b_chain (Optional[ChainComparison]): B's matched chain, or None.

    Returns:
        Optional[List[ChainStepRow]]: Rows index-aligned with `chain.step_endpoints`,
        or None when B has no comparable steps -- the chain went unmatched, or its
        steps differ in shape (see compare.py's `steps_are_pairable()`).
    """
    if b_chain is None or b_chain.step_endpoints is None:
        return None
    return b_chain.step_endpoints


def render_chain_steps_table(
    chain: Chain, b_chain: Optional[ChainComparison] = None
) -> str:
    """
    Render one chain's steps as a Markdown table: a From/To endpoint pair plus one
    p50/p90 metric pair per interval between them. Mirrors report.js's
    chainStepsTable().

    Args:
        chain (Chain): The chain whose steps to render. In an A/B report this is A's
            chain.
        b_chain (Optional[ChainComparison]): B's matched chain in an A/B report, else
            None.

    Returns:
        str: The chain's heading and steps table, or a placeholder if it has no
        step_endpoints.
    """
    heading = f"### {_chain_label(chain)}\n\n"
    if len(chain.step_endpoints) == 0:
        return heading + "_No steps recorded._\n"

    compare = b_chain is not None
    b_rows = _compared_step_rows(b_chain)
    rows = []
    for index, row in enumerate(chain.step_endpoints):
        b_metric = b_rows[index].metric if b_rows is not None else None
        p50, p90 = _metric_cells(row.metric, b_metric, compare)
        rows.append([
            _endpoint_text(row.from_endpoint),
            _endpoint_text(row.to_endpoint),
            p50,
            p90,
        ])
    return heading + _md_table(CHAIN_STEPS_HEADERS, rows)


def render_chain_latency(
    report: Report, comparison: Optional[Comparison] = None
) -> str:
    """
    Render section 03: a Markdown table with one summary row per chain, followed by
    each chain's own per-edge steps table.

    Args:
        report (Report): The report to render. In an A/B report this is A, the
            control.
        comparison (Optional[Comparison]): B's rows aligned to `report.chains`, or
            None for a single-trace report.

    Returns:
        str: The rendered "## 03 - Chain latency" section, including each chain's
        steps table.
    """
    heading = "## 03 · Chain latency\n\n"
    if len(report.chains) == 0:
        return heading + "_No chains configured._\n"

    compare = comparison is not None
    rows = []
    step_tables = []
    for index, chain in enumerate(report.chains):
        b_chain = comparison.chains[index] if compare else None
        traversals = _compared_text(
            str(chain.traversal_count),
            b_chain,
            lambda c: str(c.traversal_count),
            compare,
        )
        b_latency = b_chain.latency if b_chain is not None else None
        latency_p50, latency_p90 = _metric_cells(chain.latency, b_latency, compare)
        rows.append([_chain_label(chain), traversals, latency_p50, latency_p90])
        step_tables.append(render_chain_steps_table(chain, b_chain))
    return (
        heading + _md_table(CHAIN_LATENCY_HEADERS, rows) + "\n" + "\n".join(step_tables)
    )


# ---- Assembly -------------------------------------------------------------


def _capture_window_line(report: Report, comparison: Optional[Comparison]) -> str:
    """
    Build the capture-window line, naming both durations in an A/B report.

    Args:
        report (Report): A's report.
        comparison (Optional[Comparison]): B's comparison, or None.

    Returns:
        str: The capture-window line.
    """
    if comparison is None:
        return f"Capture window: {_fmt(report.duration_s)}s"
    return (
        f"Capture window: {CONTROL_LABEL} {_fmt(report.duration_s)}s"
        f" · {MODIFIED_LABEL} {_fmt(comparison.duration_s)}s"
    )


def _compare_key_line(comparison: Comparison) -> str:
    """
    Describe how to read an A/B report's cells, and what the comparison discarded.
    Mirrors report.js's compareKeyText().

    Args:
        comparison (Comparison): The comparison being rendered.

    Returns:
        str: The key line shown under the capture window.
    """
    parts = [
        f"Each cell shows {CONTROL_LABEL} (control) and then {MODIFIED_LABEL} "
        f"(modified); a bolded {MODIFIED_LABEL} value is a statistically and "
        "practically significant shift."
    ]
    if comparison.unmatched_row_count > 0:
        dropped = (
            f"{comparison.unmatched_row_count} row(s) in {MODIFIED_LABEL} had no "
            f"counterpart in {CONTROL_LABEL} and were dropped"
        )
        if comparison.unstable_label_count > 0:
            dropped += (
                f"; {comparison.unstable_label_count} of those name a callback by a "
                "per-run handle, so re-trace from process start to match them"
            )
        parts.append(f"{dropped}.")
    return " ".join(parts)


def render_markdown_report(
    report: Report, comparison: Optional[Comparison] = None
) -> str:
    """
    Render a Report -- optionally overlaid with a Comparison -- as a single Markdown
    document covering sections 01-03. No topology diagram and no throughput chart.

    Args:
        report (Report): The report to render. In an A/B report this is A, the
            control, which alone fixes every row and label -- same contract as
            `bundle.render_report()`.
        comparison (Optional[Comparison]): B's rows aligned to `report`, or None for
            a single-trace report.

    Returns:
        str: The rendered Markdown document.
    """
    lines = [
        f"# {report.title or DEFAULT_TITLE}",
        "",
        _capture_window_line(report, comparison),
    ]
    if comparison is not None:
        lines += ["", _compare_key_line(comparison)]
    lines += [
        "",
        render_topic_stats(report, comparison),
        render_node_analysis(report, comparison),
        render_chain_latency(report, comparison),
    ]
    return "\n".join(lines) + "\n"
