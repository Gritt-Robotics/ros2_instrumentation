# Copyright 2026 Gritt Robotics Inc.

"""
reports/model.py
=================

Dataclasses mirroring the real trace-analysis JSON shape (see `reports/schema.py`),
plus non-statistical *arrangement* helpers: topological ordering of nodes, and
deriving an edge list from each node's `to` adjacency (the JSON has no
top-level `edges` array -- topology is embedded per-node). No statistics: every
numeric value here is copied straight from the JSON, never computed --
`Metric.significant` is the one exception, set only by `trace_report`'s
`compare.py` when building an A/B comparison, never by `Metric.from_dict()`.

Lives in the same `reports` package as `build_node_report()` -- the producer of
this JSON -- so a downstream renderer (e.g. `trace_report`) consumes this
ergonomic model as a tool call instead of independently re-deriving it.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from trace_instrumentation.trace_analysis.reports.schema import (
    KEY_BUCKET_INTERVAL_S,
    KEY_BUCKET_STARTS_S,
    KEY_CALLBACK_HZ,
    KEY_CALLBACK_START_TO_PUBLISH_MS,
    KEY_CALLBACK_TIME_MS,
    KEY_CALLBACK_TYPE,
    KEY_CALLBACKS,
    KEY_CHAIN_LATENCY_MS,
    KEY_COUNT,
    KEY_DROPPED,
    KEY_DURATION_MS,
    KEY_DURATION_S,
    KEY_FROM,
    KEY_INDIRECT_WAIT_MS,
    KEY_INSTANTANEOUS_WAIT_COUNT,
    KEY_INSTANTANEOUS_WAIT_THRESHOLD_MS,
    KEY_INTER_CALLBACK_GAP_MS,
    KEY_INTERARRIVAL_MS,
    KEY_IS_INDIRECT,
    KEY_MAX,
    KEY_MEAN,
    KEY_MEDIAN,
    KEY_METRIC_MS,
    KEY_MIN,
    KEY_NODES,
    KEY_P90,
    KEY_P99,
    KEY_PCT,
    KEY_PER_TOPIC_HZ,
    KEY_PIPELINE_CHAIN_LATENCY,
    KEY_PUBLISHED_COUNT,
    KEY_PUBLISHER_HZ,
    KEY_PUBLISHERS,
    KEY_PUBLISHING_NODE,
    KEY_RECEIVED_COUNT,
    KEY_RELAY_CALLBACK_TIME_MS,
    KEY_RELAY_CALLBACK_TOPIC,
    KEY_STEP_ENDPOINTS,
    KEY_STEPS,
    KEY_SUBSCRIBING_NODE,
    KEY_TAKE_TO_CALLBACK_START_MS,
    KEY_TERMINATOR,
    KEY_THROUGHPUT,
    KEY_TIMER_PERIOD_NS,
    KEY_TO,
    KEY_TOPIC,
    KEY_TOPIC_HZ,
    KEY_TOPIC_LATENCY_MS,
    KEY_TOPIC_SEQUENCE,
    KEY_TOPIC_TOTAL_HZ,
    KEY_TOTAL_HZ,
    KEY_TRAVERSAL_COUNT,
    KEY_VALUES,
)


@dataclass(frozen=True)
class Metric:
    """One `p50/p90/p99/mean` stat group. `p50` is read from the JSON's `median` key
    (the parser has not renamed it yet); `p90` and `values` are optional (absent on
    JSON produced before the parser started emitting them). `significant` is set
    only by an A/B comparison (see `trace_report.compare`), never by
    `from_dict()` -- None outside a comparison, or when too few samples exist on
    either side to venture a verdict."""

    count: int
    min: float
    mean: float
    p50: float
    p99: float
    max: float
    p90: Optional[float] = None
    values: List[float] = field(default_factory=list)
    significant: Optional[bool] = None

    @staticmethod
    def from_dict(obj: Optional[dict]) -> Optional["Metric"]:
        """
        Build a Metric from a Metric-shaped JSON dict.

        Args:
            obj (Optional[dict]): The JSON dict (count/min/mean/median/p99/max,
                optionally p90/values), or None.

        Returns:
            Optional[Metric]: The parsed Metric, or None if `obj` is None.
        """
        if obj is None:
            return None
        return Metric(
            count=obj[KEY_COUNT],
            min=obj[KEY_MIN],
            mean=obj[KEY_MEAN],
            p50=obj[KEY_MEDIAN],
            p99=obj[KEY_P99],
            max=obj[KEY_MAX],
            p90=obj.get(KEY_P90),
            values=obj.get(KEY_VALUES, []),
        )


@dataclass(frozen=True)
class Callback:
    node: str
    label: str
    callback_type: str
    topic: Optional[str]
    timer_period_ns: Optional[int]
    callback_hz: float
    duration: Optional[Metric]
    topic_latency: Optional[Metric]
    interarrival: Optional[Metric]
    inter_callback_gap: Optional[Metric]
    instantaneous_wait_count: Optional[int]
    instantaneous_wait_threshold_ms: Optional[float]


@dataclass(frozen=True)
class Publisher:
    node: str
    label: str
    topic: str
    publisher_hz: float
    topic_total_hz: float
    callback_start_to_publish: Optional[Metric]
    published_count: int
    received_count: int
    dropped_count: Optional[int]
    dropped_pct: Optional[float]


@dataclass(frozen=True)
class Edge:
    """One topology edge, optionally joined to its source publisher and destination
    callback (see `_join_edges()`). Both endpoints are None on an edge straight from
    `build_edges()`, which derives topology alone."""

    from_node: str
    to_node: str
    topic: str
    publisher: Optional[Publisher] = None
    callback: Optional[Callback] = None


@dataclass(frozen=True)
class ChainStep:
    publishing_node: str
    # None on a publish-terminated chain's last step.
    subscribing_node: Optional[str]
    topic: str
    topic_hz: float
    callback_hz: float
    is_indirect: bool
    topic_latency: Optional[Metric]
    take_to_callback_start: Optional[Metric]
    callback_time: Optional[Metric]
    indirect_wait: Optional[Metric]
    relay_callback_time: Optional[Metric]
    # The relay callback's own trigger, not what it publishes -- None unless is_indirect.
    relay_callback_topic: Optional[str]


@dataclass(frozen=True)
class ChainEndpoint:
    """One moment a chain's timeline passes through: `kind` is one of `publish`,
    `take`, `callback start`, `link`, `callback end` (see `ENDPOINT_KIND_*`)."""

    topic: str
    kind: str
    node: Optional[str]


@dataclass(frozen=True)
class ChainStepRow:
    """One interval between two adjacent `ChainEndpoint`s, with the metric measuring
    it. Named `from_endpoint`/`to_endpoint` rather than `from`/`to` -- `from` is a
    Python keyword."""

    from_endpoint: ChainEndpoint
    to_endpoint: ChainEndpoint
    metric: Optional[Metric]

    @staticmethod
    def from_dict(obj: dict) -> "ChainStepRow":
        """
        Build a ChainStepRow from a `step_endpoints` row JSON dict.

        Args:
            obj (dict): The row dict ({from, to, metric_ms}).

        Returns:
            ChainStepRow: The parsed row.
        """
        return ChainStepRow(
            from_endpoint=ChainEndpoint(**obj[KEY_FROM]),
            to_endpoint=ChainEndpoint(**obj[KEY_TO]),
            metric=Metric.from_dict(obj[KEY_METRIC_MS]),
        )


@dataclass(frozen=True)
class Chain:
    topic_sequence: List[Tuple[str, str]]
    traversal_count: int
    # Which CHAIN_TERMINATOR_* the chain declared, and so where `latency` ends.
    terminator: str
    steps: List[ChainStep]
    step_endpoints: List[ChainStepRow]
    latency: Optional[Metric]


@dataclass(frozen=True)
class Throughput:
    """Publish throughput over time: per-topic publish rate (Hz) per time bucket,
    plus the aggregate `total_hz`. All lists share the length of `bucket_starts_s`."""

    bucket_interval_s: float
    bucket_starts_s: List[float]
    total_hz: List[float]
    per_topic_hz: Dict[str, List[float]]


@dataclass(frozen=True)
class Report:
    duration_s: float
    title: Optional[str]
    node_names: List[str]
    edges: List[Edge]
    callbacks: List[Callback]
    publishers: List[Publisher]
    chains: List[Chain]
    throughput: Optional[Throughput]


def build_edges(trace: dict) -> List[Edge]:
    """
    Derive the topology edge list from each node's `to` adjacency.

    Args:
        trace (dict): The parsed trace JSON.

    Returns:
        List[Edge]: One Edge per distinct (from_node, to_node, topic) triple, in
        first-seen order, with both endpoints unjoined. Use `edge_rows()` for edges
        carrying their publisher and callback.
    """
    edges = []
    seen = set()
    for node_name, node in trace[KEY_NODES].items():
        for neighbor, topic in node[KEY_TO]:
            key = (node_name, neighbor, topic)
            if key not in seen:
                seen.add(key)
                edges.append(Edge(from_node=node_name, to_node=neighbor, topic=topic))
    return edges


def topo_order(trace: dict) -> List[str]:
    """
    Topologically order the nodes via Kahn's algorithm over the edges derived from
    each node's `to` adjacency.

    Args:
        trace (dict): The parsed trace JSON.

    Returns:
        List[str]: Node names in topological order (source nodes first). Nodes
        unreachable from an indegree-0 root (isolated or part of a cycle) are
        appended at the end, sorted, rather than dropped.
    """
    node_names = list(trace[KEY_NODES].keys())
    indegree = dict.fromkeys(node_names, 0)
    adjacency: Dict[str, List[str]] = defaultdict(list)
    for edge in build_edges(trace):
        if edge.to_node in indegree:
            indegree[edge.to_node] += 1
        adjacency[edge.from_node].append(edge.to_node)

    ready = sorted(name for name in node_names if indegree[name] == 0)
    order = []
    while ready:
        name = ready.pop(0)
        order.append(name)
        for neighbor in sorted(adjacency.get(name, [])):
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                ready.append(neighbor)
        ready.sort()

    remaining = sorted(name for name in node_names if name not in order)
    return order + remaining


def callback_rows(trace: dict) -> List[Callback]:
    """
    Flatten every node's `callbacks` dict into a list, nodes in topological order.

    Args:
        trace (dict): The parsed trace JSON.

    Returns:
        List[Callback]: One row per callback, grouped by node (topo order) then
        sorted by label within a node.
    """
    rows = []
    for node_name in topo_order(trace):
        node = trace[KEY_NODES][node_name]
        for label, cb in sorted(node[KEY_CALLBACKS].items()):
            rows.append(
                Callback(
                    node=node_name,
                    label=label,
                    callback_type=cb[KEY_CALLBACK_TYPE],
                    topic=cb[KEY_TOPIC],
                    timer_period_ns=cb[KEY_TIMER_PERIOD_NS],
                    callback_hz=cb[KEY_CALLBACK_HZ],
                    duration=Metric.from_dict(cb.get(KEY_DURATION_MS)),
                    topic_latency=Metric.from_dict(cb.get(KEY_TOPIC_LATENCY_MS)),
                    interarrival=Metric.from_dict(cb.get(KEY_INTERARRIVAL_MS)),
                    inter_callback_gap=Metric.from_dict(
                        cb.get(KEY_INTER_CALLBACK_GAP_MS)
                    ),
                    instantaneous_wait_count=cb.get(KEY_INSTANTANEOUS_WAIT_COUNT),
                    instantaneous_wait_threshold_ms=cb.get(
                        KEY_INSTANTANEOUS_WAIT_THRESHOLD_MS
                    ),
                )
            )
    return rows


def publisher_rows(trace: dict) -> List[Publisher]:
    """
    Flatten every node's `publishers` dict into a list, nodes in topological order.

    Args:
        trace (dict): The parsed trace JSON.

    Returns:
        List[Publisher]: One row per publisher, grouped by node (topo order) then
        sorted by label within a node.
    """
    rows = []
    for node_name in topo_order(trace):
        node = trace[KEY_NODES][node_name]
        for label, pub in sorted(node[KEY_PUBLISHERS].items()):
            dropped = pub.get(KEY_DROPPED)
            rows.append(
                Publisher(
                    node=node_name,
                    label=label,
                    topic=pub[KEY_TOPIC],
                    publisher_hz=pub[KEY_PUBLISHER_HZ],
                    topic_total_hz=pub[KEY_TOPIC_TOTAL_HZ],
                    callback_start_to_publish=Metric.from_dict(
                        pub.get(KEY_CALLBACK_START_TO_PUBLISH_MS)
                    ),
                    published_count=pub[KEY_PUBLISHED_COUNT],
                    received_count=pub[KEY_RECEIVED_COUNT],
                    dropped_count=dropped[KEY_COUNT] if dropped else None,
                    dropped_pct=dropped[KEY_PCT] if dropped else None,
                )
            )
    return rows


def _join_edges(
    edges: List[Edge], callbacks: List[Callback], publishers: List[Publisher]
) -> List[Edge]:
    """
    Join edges to their source publisher (by (from_node, topic)) and destination
    callback (by (to_node, topic)).

    Args:
        edges (List[Edge]): Topology edges, e.g. from build_edges().
        callbacks (List[Callback]): Callback rows, e.g. from callback_rows().
        publishers (List[Publisher]): Publisher rows, e.g. from publisher_rows().

    Returns:
        List[Edge]: One joined Edge per input edge, in the same order.
    """
    pubs_by_node_topic = {(p.node, p.topic): p for p in publishers}
    cbs_by_node_topic: Dict[Tuple[str, str], Callback] = {}
    for cb in callbacks:
        if cb.topic is not None and (cb.node, cb.topic) not in cbs_by_node_topic:
            cbs_by_node_topic[(cb.node, cb.topic)] = cb

    return [
        Edge(
            from_node=edge.from_node,
            to_node=edge.to_node,
            topic=edge.topic,
            publisher=pubs_by_node_topic.get((edge.from_node, edge.topic)),
            callback=cbs_by_node_topic.get((edge.to_node, edge.topic)),
        )
        for edge in edges
    ]


def edge_rows(trace: dict) -> List[Edge]:
    """
    One row per edge: joins the edge to its source publisher (by (from_node, topic))
    and destination callback (by (to_node, topic)).

    Args:
        trace (dict): The parsed trace JSON.

    Returns:
        List[Edge]: One joined Edge per topology edge.
    """
    return _join_edges(build_edges(trace), callback_rows(trace), publisher_rows(trace))


def chain_rows(trace: dict) -> List[Chain]:
    """
    Parse the top-level `pipeline_chain_latency` list into Chain rows.

    Args:
        trace (dict): The parsed trace JSON.

    Returns:
        List[Chain]: One Chain per entry in `pipeline_chain_latency` (empty list if
        the key is absent).
    """
    chains = []
    for chain in trace.get(KEY_PIPELINE_CHAIN_LATENCY, []):
        steps = [
            ChainStep(
                publishing_node=step[KEY_PUBLISHING_NODE],
                subscribing_node=step[KEY_SUBSCRIBING_NODE],
                topic=step[KEY_TOPIC],
                topic_hz=step[KEY_TOPIC_HZ],
                callback_hz=step[KEY_CALLBACK_HZ],
                is_indirect=step[KEY_IS_INDIRECT],
                topic_latency=Metric.from_dict(step.get(KEY_TOPIC_LATENCY_MS)),
                take_to_callback_start=Metric.from_dict(
                    step.get(KEY_TAKE_TO_CALLBACK_START_MS)
                ),
                callback_time=Metric.from_dict(step.get(KEY_CALLBACK_TIME_MS)),
                indirect_wait=Metric.from_dict(step.get(KEY_INDIRECT_WAIT_MS)),
                relay_callback_time=Metric.from_dict(
                    step.get(KEY_RELAY_CALLBACK_TIME_MS)
                ),
                relay_callback_topic=step.get(KEY_RELAY_CALLBACK_TOPIC),
            )
            for step in chain[KEY_STEPS]
        ]
        chains.append(
            Chain(
                topic_sequence=[tuple(pair) for pair in chain[KEY_TOPIC_SEQUENCE]],
                traversal_count=chain[KEY_TRAVERSAL_COUNT],
                terminator=chain[KEY_TERMINATOR],
                steps=steps,
                step_endpoints=[
                    ChainStepRow.from_dict(row) for row in chain[KEY_STEP_ENDPOINTS]
                ],
                latency=Metric.from_dict(chain.get(KEY_CHAIN_LATENCY_MS)),
            )
        )
    return chains


def throughput_row(trace: dict) -> Optional[Throughput]:
    """
    Parse the optional top-level `throughput` block into a Throughput, or None.

    Args:
        trace (dict): The parsed trace JSON.

    Returns:
        Optional[Throughput]: The parsed throughput series, or None when the block is
        absent.
    """
    tp = trace.get(KEY_THROUGHPUT)
    if tp is None:
        return None
    return Throughput(
        bucket_interval_s=tp[KEY_BUCKET_INTERVAL_S],
        bucket_starts_s=tp[KEY_BUCKET_STARTS_S],
        total_hz=tp[KEY_TOTAL_HZ],
        per_topic_hz=tp[KEY_PER_TOPIC_HZ],
    )


def build_report(trace: dict, *, title: Optional[str] = None) -> Report:
    """
    Assemble the full Report from a validated trace JSON.

    Args:
        trace (dict): The parsed trace JSON.
        title (Optional[str]): Report title override.

    Returns:
        Report: The assembled report model.
    """
    callbacks = callback_rows(trace)
    publishers = publisher_rows(trace)
    return Report(
        duration_s=trace[KEY_DURATION_S],
        title=title,
        node_names=topo_order(trace),
        edges=_join_edges(build_edges(trace), callbacks, publishers),
        callbacks=callbacks,
        publishers=publishers,
        chains=chain_rows(trace),
        throughput=throughput_row(trace),
    )
