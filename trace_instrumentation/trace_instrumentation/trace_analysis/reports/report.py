# Copyright 2026 Gritt Robotics Inc.

"""
reports.report
===============

Node-based combined report: one entry per node with its adjacency (`to`/`from`),
callbacks, and publishers, all in milliseconds -- the single source of truth for
the machine-readable JSON report. `reports.schema`/`reports.model` describe that
same JSON from the validation and ergonomic-object sides, respectively, so all
three stay in one package instead of drifting apart independently.
"""

from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from trace_instrumentation.trace_analysis.analysis import TraceAnalysis
from trace_instrumentation.trace_analysis.flow_graph import (
    FlowGraph,
    Pub,
    split_chain_step,
)
from trace_instrumentation.trace_analysis.graph import (
    CB,
    CB_KIND_SUBSCRIPTION,
    CB_KIND_TIMER,
    CHAIN_TERMINATOR_PUB,
    UNKNOWN_NODE_LABEL,
    CallbackKey,
    Step,
    Traversal,
)
from trace_instrumentation.trace_analysis.metrics.frequency import (
    BUCKET_COUNTS_KEY,
    BUCKET_INTERVAL_KEY,
    CUMULATIVE_TOPIC_KEY,
    DURATION_KEY,
    _topic_publish_hz,
    get_throughput_buckets,
)
from trace_instrumentation.trace_analysis.metrics.latency import (
    INSTANTANEOUS_WAIT_THRESHOLD_MS,
    callback_groups,
    callback_timing_stats,
)
from trace_instrumentation.trace_analysis.metrics.stats import (
    stats,
    stats_ms,
)
from trace_instrumentation.trace_analysis.reports.schema import (
    ENDPOINT_KIND_CALLBACK_END,
    ENDPOINT_KIND_CALLBACK_START,
    ENDPOINT_KIND_LINK,
    ENDPOINT_KIND_PUBLISH,
    ENDPOINT_KIND_TAKE,
    KEY_BUCKET_INTERVAL_S,
    KEY_BUCKET_STARTS_S,
    KEY_BY_CALLBACK,
    KEY_CALLBACK_HZ,
    KEY_CALLBACK_START_TO_PUBLISH_MS,
    KEY_CALLBACK_TIME_MS,
    KEY_CALLBACK_TYPE,
    KEY_CALLBACKS,
    KEY_CHAIN_LATENCY_MS,
    KEY_COUNT,
    KEY_DROPPED,
    KEY_DURATION_MS,
    KEY_FROM,
    KEY_INDIRECT_WAIT_MS,
    KEY_INSTANTANEOUS_WAIT_COUNT,
    KEY_INSTANTANEOUS_WAIT_THRESHOLD_MS,
    KEY_INTER_CALLBACK_GAP_MS,
    KEY_INTERARRIVAL_MS,
    KEY_IS_INDIRECT,
    KEY_KIND,
    KEY_METRIC_MS,
    KEY_NODE,
    KEY_NODES,
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
)

UNKNOWN_TOPIC_LABEL = "<unknown-topic>"

TOPIC_CHAINS_KEY = "topic_chains"
IGNORE_NODES_KEY = "ignore_nodes"
ONLY_TOPIC_CHAINS_KEY = "only_topic_chains"


def _node_based_callback_rows(
    flow: FlowGraph, duration_s: float
) -> Dict[str, Dict[str, dict]]:
    """Per-node, per-callback aggregated stats for the node-based combined report.

    Args:
        flow (FlowGraph): Built flow graph.
        duration_s (float): Trace span in seconds, the denominator for callback_hz.

    Returns:
        Dict[str, Dict[str, dict]]: node label -> {"<label>@<topic-or-'timer'>": row}.
        Each row: `callback_type`, `topic` (None for timers), `timer_period_ns` (None
        for non-timers), `callback_hz` (float), `duration_ms` (stats dict), and, for
        subscriptions, `topic_latency_ms` (stats dict).
    """
    # Aggregate over exactly the reported set, so the executor-idle gaps below are
    # measured between the callbacks this report shows.
    reported = [cb for cb in flow.callbacks if _is_reportable(cb)]
    groups = callback_groups(reported)
    timing = callback_timing_stats(reported)

    # Walked off the takes each subscription endpoint already holds. Only a subscription
    # callback can own one (FlowGraph's binding rule), so the kind is not re-checked.
    reported_keys = set(groups)
    topic_latency: Dict[CallbackKey, List[int]] = defaultdict(list)
    for endpoint in flow.subscription_endpoints.values():
        for take in endpoint.takes:
            cb = take.consumer_cb
            origin = take.origin_pub
            if cb is None or origin is None:
                continue
            if take.ts_rmw is None or origin.ts_rmw is None:
                continue
            key = cb.group_key
            if key in reported_keys:
                topic_latency[key].append(take.ts_rmw - origin.ts_rmw)

    result: Dict[str, Dict[str, dict]] = defaultdict(dict)
    for key, group in groups.items():
        node, kind, topic, label = key
        is_subscription = kind == CB_KIND_SUBSCRIPTION
        is_timer = kind == CB_KIND_TIMER
        start_count = len(group.starts)
        row_key = (
            f"{label}@{CB_KIND_TIMER if is_timer else (topic or UNKNOWN_TOPIC_LABEL)}"
        )
        row = {
            KEY_CALLBACK_TYPE: kind,
            KEY_TOPIC: None if is_timer else topic,
            KEY_TIMER_PERIOD_NS: group.timer_period_ns if is_timer else None,
            KEY_CALLBACK_HZ: start_count / duration_s if duration_s > 0 else 0.0,
            KEY_DURATION_MS: stats_ms(stats(group.durations)),
        }
        if is_subscription:
            row[KEY_TOPIC_LATENCY_MS] = stats_ms(stats(topic_latency.get(key, [])))
        group_timing = timing[key]
        row[KEY_INTERARRIVAL_MS] = stats_ms(group_timing.interarrival_ns)
        row[KEY_INTER_CALLBACK_GAP_MS] = stats_ms(group_timing.inter_callback_gap_ns)
        row[KEY_INSTANTANEOUS_WAIT_COUNT] = group_timing.instantaneous_wait_count
        row[KEY_INSTANTANEOUS_WAIT_THRESHOLD_MS] = INSTANTANEOUS_WAIT_THRESHOLD_MS
        result[node][row_key] = row
    return dict(result)


def _is_reportable(cb: CB) -> bool:
    """Whether an invocation belongs in the node-based report.

    Args:
        cb (CB): The invocation to test.

    Returns:
        bool: False for a callback that never started, and for an internal-topic or
        unresolved-handle (non "/") subscription callback; True otherwise.
    """
    if cb.ts_start is None:
        return False
    if cb.kind != CB_KIND_SUBSCRIPTION:
        return True
    return not _is_internal_topic(cb.target_label) and cb.target_label.startswith("/")


UNTRACED_PUBLISHER_LABEL = "<untraced>"


def _callback_start_to_publish_ns(p: Pub) -> Optional[int]:
    """How long the publishing callback ran before it issued this publish.

    Args:
        p (Pub): The publish instance.

    Returns:
        Optional[int]: Nanoseconds from the triggering take (or, for a take-less
        callback such as a timer, the callback's own start) to the DDS write; None when
        no callback enclosed the publish or a needed timestamp is missing.
    """
    cb = p.enclosing_cb
    if cb is None or p.ts_rmw is None:
        return None
    if cb.trigger_take is not None:
        take_ts_rmw = cb.trigger_take.ts_rmw
        return p.ts_rmw - take_ts_rmw if take_ts_rmw is not None else None
    return p.ts_rmw - cb.ts_start if cb.ts_start is not None else None


def _publish_group_stats(pubs: List[Pub]) -> dict:
    """The counts and processing-time stats shared by a publisher row and each of its
    by_callback entries.

    Args:
        pubs (List[Pub]): Publishes to aggregate.

    Returns:
        dict: `published_count`, `received_count` (publishes matched to at least one
        take), and `callback_start_to_publish_ms` (stats dict or None).
    """
    processing_ns = [
        ns for ns in (_callback_start_to_publish_ns(p) for p in pubs) if ns is not None
    ]
    return {
        # Counting matched publishes (not takes) is what keeps received <= published by
        # construction, so a step can never report receiving more than it sent.
        KEY_PUBLISHED_COUNT: len(pubs),
        KEY_RECEIVED_COUNT: sum(1 for p in pubs if len(p.takes) > 0),
        KEY_CALLBACK_START_TO_PUBLISH_MS: stats_ms(stats(processing_ns)),
    }


def _node_based_publisher_rows(
    flow: FlowGraph, duration_s: float
) -> Dict[str, Dict[str, dict]]:
    """Per-node, per-topic aggregated publisher stats for the node-based combined
    report.

    Walks the publishes each PublisherEndpoint already holds rather than re-scanning
    every publish instance in the graph, so an internal topic costs one skipped endpoint
    instead of one skipped publish per message.

    Args:
        flow (FlowGraph): Built flow graph.
        duration_s (float): Exact full-trace span in seconds; denominator for
            publisher_hz / topic_total_hz.

    Returns:
        Dict[str, Dict[str, dict]]: node label -> {"<topic>": row}. Each row: `topic`,
        `publisher_hz` (this node's publishes / duration_s), `topic_total_hz` (every
        node's publishes to the topic / duration_s), the _publish_group_stats() keys,
        `dropped` (`published_count - received_count`, count and pct), and
        `by_callback` (originating handler label -> its own _publish_group_stats()).
    """
    topic_total_hz = _topic_publish_hz(flow, duration_s)

    # (node, topic) -> handler label -> its publishes. A node can register two
    # publishers on one topic, so the rows are keyed by topic, not by endpoint.
    groups: Dict[Tuple[str, str], Dict[str, List[Pub]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for endpoint in flow.publisher_endpoints.values():
        topic = endpoint.topic.name
        if _is_internal_topic(topic):
            continue
        for p in endpoint.pubs:
            if p.ts_rmw is None:
                continue
            label = (
                p.enclosing_cb.handler_label
                if p.enclosing_cb is not None
                else UNTRACED_PUBLISHER_LABEL
            )
            groups[(endpoint.node.label, topic)][label].append(p)

    result: Dict[str, Dict[str, dict]] = defaultdict(dict)
    for (node, topic), by_label in groups.items():
        every_pub = [p for pubs in by_label.values() for p in pubs]
        group_stats = _publish_group_stats(every_pub)
        published_count = group_stats[KEY_PUBLISHED_COUNT]
        dropped_count = published_count - group_stats[KEY_RECEIVED_COUNT]
        result[node][topic] = {
            KEY_TOPIC: topic,
            KEY_PUBLISHER_HZ: (published_count / duration_s if duration_s > 0 else 0.0),
            KEY_TOPIC_TOTAL_HZ: topic_total_hz.get(topic, 0.0),
            KEY_CALLBACK_START_TO_PUBLISH_MS: group_stats[
                KEY_CALLBACK_START_TO_PUBLISH_MS
            ],
            KEY_PUBLISHED_COUNT: published_count,
            KEY_RECEIVED_COUNT: group_stats[KEY_RECEIVED_COUNT],
            KEY_DROPPED: {
                KEY_COUNT: dropped_count,
                KEY_PCT: (
                    100.0 * dropped_count / published_count
                    if published_count > 0
                    else 0.0
                ),
            },
            KEY_BY_CALLBACK: {
                label: _publish_group_stats(pubs)
                for label, pubs in sorted(by_label.items())
            },
        }
    return dict(result)


INTERNAL_TOPICS = frozenset({"/parameter_events", "/rosout"})


def _is_internal_topic(topic: Optional[str]) -> bool:
    """Whether a topic is an internal ROS bookkeeping topic to omit from the report.

    Args:
        topic (Optional[str]): Topic name, or None.

    Returns:
        bool: True if the topic is an internal ROS topic (e.g. /parameter_events).
    """
    return topic in INTERNAL_TOPICS


def _node_adjacency(flow: FlowGraph) -> Dict[str, Dict[str, List[List[str]]]]:
    """Per-node upstream/downstream adjacency as unique (node, topic) pairs, walked off
    the Topic endpoints FlowGraph already materialized, excluding internal ROS topics.

    Args:
        flow (FlowGraph): Built flow graph.

    Returns:
        Dict[str, Dict[str, List[List[str]]]]: node label -> {"to": [...], "from": [...]},
        each a sorted list of [other_node_label, topic] pairs (self excluded).
    """
    pairs: Dict[str, Dict[str, Set[Tuple[str, str]]]] = defaultdict(
        lambda: {KEY_TO: set(), KEY_FROM: set()}
    )
    for topic in flow.topics.values():
        if _is_internal_topic(topic.name):
            continue
        pub_nodes = {endpoint.node.label for endpoint in topic.publishers}
        sub_nodes = {endpoint.node.label for endpoint in topic.subscribers}
        # Touched so a node that only ever publishes (or only subscribes) still gets a
        # block, rather than appearing only once some peer names it.
        for node in pub_nodes | sub_nodes:
            pairs[node]
        for pub_node in pub_nodes:
            for sub_node in sub_nodes:
                if pub_node == sub_node:
                    continue
                pairs[pub_node][KEY_TO].add((sub_node, topic.name))
                pairs[sub_node][KEY_FROM].add((pub_node, topic.name))

    return {
        node: {
            KEY_TO: [list(pair) for pair in sorted(sides[KEY_TO])],
            KEY_FROM: [list(pair) for pair in sorted(sides[KEY_FROM])],
        }
        for node, sides in pairs.items()
    }


def _chain_topics(topic_chains: List[List[str]]) -> Set[str]:
    """The distinct topic names a set of topic chains names, node pins stripped.

    Args:
        topic_chains (List[List[str]]): Zero or more ordered topic-name sequences,
            each step either "<topic>" or "<topic>@<node>".

    Returns:
        Set[str]: Every topic named by any step of any chain.
    """
    return {split_chain_step(step)[0] for chain in topic_chains for step in chain}


def _nodes_on_topics(flow: FlowGraph, topics: Set[str]) -> Set[str]:
    """Node labels holding a publisher or subscriber endpoint on one of `topics`.

    Endpoint registration, not observed traffic, is what makes a node a participant
    here -- so a node that subscribed to a chain topic and never received a message
    stays visible, which is the node a chain that failed to traverse needs to show.

    Args:
        flow (FlowGraph): Built flow graph.
        topics (Set[str]): Topic names to match endpoints against.

    Returns:
        Set[str]: Labels of the nodes with a matching endpoint.
    """
    nodes: Set[str] = set()
    for topic in flow.topics.values():
        if topic.name not in topics:
            continue
        nodes.update(endpoint.node.label for endpoint in topic.publishers)
        nodes.update(endpoint.node.label for endpoint in topic.subscribers)
    return nodes


def _adjacency_side(
    pairs: List[List[str]], excluded_nodes: Set[str], topics: Optional[Set[str]]
) -> List[List[str]]:
    """One node's `to` or `from` list, less the pairs the report is filtering out.

    Args:
        pairs (List[List[str]]): [peer_node_label, topic] pairs for one side.
        excluded_nodes (Set[str]): Node labels no pair may name.
        topics (Optional[Set[str]]): Topics to restrict pairs to, or None to keep
            every topic.

    Returns:
        List[List[str]]: The surviving pairs, in the order given.
    """
    return [
        [peer, topic]
        for peer, topic in pairs
        if peer not in excluded_nodes and (topics is None or topic in topics)
    ]


def _rows_on_topics(
    rows: Dict[str, dict], topics: Optional[Set[str]]
) -> Dict[str, dict]:
    """Restrict a node's callback or publisher rows to the given topics.

    A row with no topic -- a timer, a service, or an undeclared zone -- is kept: it
    carries no topic to filter on, and the timer among them is often what drives a
    chain's first publish.

    Args:
        rows (Dict[str, dict]): One node's callback or publisher rows.
        topics (Optional[Set[str]]): Topics to keep, or None to keep every row.

    Returns:
        Dict[str, dict]: The surviving rows.
    """
    if topics is None:
        return rows
    return {
        key: row
        for key, row in rows.items()
        if row[KEY_TOPIC] is None or row[KEY_TOPIC] in topics
    }


def _subscription_callback_hz(
    flow: FlowGraph, duration_s: float
) -> Dict[Tuple[str, str], float]:
    """Subscription-callback invocation rate per (node, topic).

    Reuses the canonical `callback_groups()` grouping -- for a subscription the group
    key's target label is the topic -- over the same `_is_reportable()`-filtered set
    `_node_based_callback_rows()` aggregates over, so this rate counts the same
    invocations every other per-callback aggregation does.

    Args:
        flow (FlowGraph): Built flow graph.
        duration_s (float): Trace span in seconds, the denominator.

    Returns:
        Dict[Tuple[str, str], float]: (node label, topic) -> invocations / duration_s.
    """
    reported = [cb for cb in flow.callbacks if _is_reportable(cb)]
    counts: Dict[Tuple[str, str], int] = defaultdict(int)
    for (node, kind, target, _handler), group in callback_groups(reported).items():
        if kind == CB_KIND_SUBSCRIPTION:
            counts[(node, target)] += len(group.starts)
    return {
        key: (count / duration_s if duration_s > 0 else 0.0)
        for key, count in counts.items()
    }


def _step_callback_time_ns(step: Step) -> Optional[int]:
    """This step's own callback time, falling back to the callback's full end-to-end
    duration at the sink, which relays nothing onward and so has no handoff moment
    for `Step.callback_time_ns` to measure to.

    Args:
        step (Step): One step of a traversal.

    Returns:
        Optional[int]: Nanoseconds from callback start to the handoff (or, at the
        sink, callback end), or None when no callback claimed the take or a needed
        timestamp is unresolved.
    """
    if step.callback_time_ns is not None:
        return step.callback_time_ns
    if step.take is None:
        return None
    cb = step.take.consumer_cb
    return None if cb is None else cb.duration_ns


class _ChainStepSamples:
    """Per-step samples gathered across every traversal of one chain."""

    def __init__(self) -> None:
        self.publishing_node = UNKNOWN_NODE_LABEL
        self.subscribing_node = UNKNOWN_NODE_LABEL
        self.topic = UNKNOWN_TOPIC_LABEL
        # True if any traversal relayed this step through a uid link -- reported once
        # per step, since a pipeline relays one step the same way on every traversal.
        self.is_indirect = False
        # The relay callback's own trigger (topic, or CB_KIND_TIMER) on an indirect
        # step -- distinct from next_topic, which is only what it publishes.
        self.relay_callback_topic: Optional[str] = None
        self.network_ns: List[int] = []
        self.take_to_callback_start_ns: List[int] = []
        self.callback_ns: List[int] = []
        self.indirect_wait_ns: List[int] = []
        self.relay_callback_ns: List[int] = []

    def add(self, step: Step) -> None:
        """
        Fold one traversal's step at this position into the samples.

        Args:
            step (Step): The step this traversal took at this position.
        """
        self.topic = step.topic
        if step.is_indirect:
            self.is_indirect = True
            relay_cb = step.linked_take.cb
            self.relay_callback_topic = (
                CB_KIND_TIMER
                if relay_cb.kind == CB_KIND_TIMER
                else relay_cb.target_label
            )
        if step.pub.node is not None:
            self.publishing_node = step.pub.node.label
        # A pub-terminated chain's sink step has no take -- nothing has consumed the
        # message yet, so the step names no subscriber at all rather than an unknown one.
        if step.take is None:
            self.subscribing_node = None
        elif step.take.node is not None:
            self.subscribing_node = step.take.node.label
        if (
            step.take is not None
            and step.take.ts_rmw is not None
            and step.pub.ts_rmw is not None
        ):
            self.network_ns.append(step.take.ts_rmw - step.pub.ts_rmw)
        if step.take_to_callback_start_ns is not None:
            self.take_to_callback_start_ns.append(step.take_to_callback_start_ns)
        callback_ns = _step_callback_time_ns(step)
        if callback_ns is not None:
            self.callback_ns.append(callback_ns)
        if step.indirect_wait_ns is not None:
            self.indirect_wait_ns.append(step.indirect_wait_ns)
        if step.relay_callback_time_ns is not None:
            self.relay_callback_ns.append(step.relay_callback_time_ns)


def _chain_step_samples(traversals: List[Traversal]) -> List[_ChainStepSamples]:
    """Gather every traversal's steps into one sample bucket per step.

    Every number here is scoped to the traversals that actually walked the chain, so a
    step's latency describes this pipeline rather than all traffic on that topic.

    Args:
        traversals (List[Traversal]): Completed traversals of one chain.

    Returns:
        List[_ChainStepSamples]: One bucket per step, in chain order.
    """
    samples = [_ChainStepSamples() for _ in range(len(traversals[0].steps))]
    for traversal in traversals:
        for index, step in enumerate(traversal.steps):
            samples[index].add(step)
    return samples


def _chain_endpoints(steps: List[dict], terminator: str) -> List[dict]:
    """Expand a chain's per-step dicts into the ordered endpoints its timeline
    passes through.

    Consecutive steps share an endpoint -- a step ends on the publish onto the next
    topic, which is where the next step begins -- so the whole chain is one unbroken
    sequence rather than a set of independently-labelled steps. This is the one place
    that derives it; `trace_report`'s `report.js` renders the result as-is.

    Args:
        steps (List[dict]): One chain's per-step dicts, in chain order (this
            function's own `KEY_STEP_ENDPOINTS` output is not itself valid input).
        terminator (str): The chain's `KEY_TERMINATOR` value.

    Returns:
        List[dict]: Endpoints in order, each {topic, kind, node, metric_ms}, where
        `metric_ms` measures the interval to the following endpoint and is None on
        the last.
    """
    last_index = len(steps) - 1
    endpoints: List[dict] = []

    def advance(topic: str, kind: str, node: Optional[str], metric_ms: Optional[dict]):
        endpoints[-1][KEY_METRIC_MS] = metric_ms
        endpoints.append({
            KEY_TOPIC: topic,
            KEY_KIND: kind,
            KEY_NODE: node,
            KEY_METRIC_MS: None,
        })

    for index, step in enumerate(steps):
        # A pub-terminated chain ends on the wire, so its take-less final step adds
        # nothing past the publish the step before it already ended on.
        if terminator == CHAIN_TERMINATOR_PUB and index == last_index:
            continue
        if len(endpoints) == 0:
            endpoints.append({
                KEY_TOPIC: step[KEY_TOPIC],
                KEY_KIND: ENDPOINT_KIND_PUBLISH,
                KEY_NODE: step[KEY_PUBLISHING_NODE],
                KEY_METRIC_MS: None,
            })
        node = step[KEY_SUBSCRIBING_NODE]
        advance(step[KEY_TOPIC], ENDPOINT_KIND_TAKE, node, step[KEY_TOPIC_LATENCY_MS])
        advance(
            step[KEY_TOPIC],
            ENDPOINT_KIND_CALLBACK_START,
            node,
            step[KEY_TAKE_TO_CALLBACK_START_MS],
        )

        next_topic = None if index == last_index else steps[index + 1][KEY_TOPIC]
        if next_topic is None:
            # The sink relays nothing onward, so its callback is measured to its own
            # end.
            advance(
                step[KEY_TOPIC],
                ENDPOINT_KIND_CALLBACK_END,
                node,
                step[KEY_CALLBACK_TIME_MS],
            )
            continue
        # Endpoints on the next topic belong to whoever publishes it, which an
        # indirect step reaches through a link the current callback only declares.
        next_node = steps[index + 1][KEY_PUBLISHING_NODE]
        if step[KEY_IS_INDIRECT]:
            advance(
                step[KEY_TOPIC], ENDPOINT_KIND_LINK, node, step[KEY_CALLBACK_TIME_MS]
            )
            # What starts the relay callback, not what it goes on to publish
            # (next_topic) -- a timer-triggered relay names no topic at all.
            advance(
                step[KEY_RELAY_CALLBACK_TOPIC],
                ENDPOINT_KIND_CALLBACK_START,
                next_node,
                step[KEY_INDIRECT_WAIT_MS],
            )
            advance(
                next_topic,
                ENDPOINT_KIND_PUBLISH,
                next_node,
                step[KEY_RELAY_CALLBACK_TIME_MS],
            )
            continue
        advance(
            next_topic, ENDPOINT_KIND_PUBLISH, next_node, step[KEY_CALLBACK_TIME_MS]
        )
    return endpoints


def _chain_step_endpoint_rows(steps: List[dict], terminator: str) -> List[dict]:
    """Pair a chain's endpoints into the rows a renderer draws, one row per interval.

    Args:
        steps (List[dict]): One chain's per-step dicts, in chain order.
        terminator (str): The chain's `KEY_TERMINATOR` value.

    Returns:
        List[dict]: One row per interval, each {from, to, metric_ms}, where `from`/
        `to` are {topic, kind, node} endpoints (their own `metric_ms` stripped, since
        it belongs to the row). Rows whose metric is None are kept, so a missing
        measurement shows as a gap rather than silently breaking the chain.
    """
    endpoints = _chain_endpoints(steps, terminator)
    return [
        {
            KEY_FROM: {k: v for k, v in endpoints[i].items() if k != KEY_METRIC_MS},
            KEY_TO: {k: v for k, v in endpoints[i + 1].items() if k != KEY_METRIC_MS},
            KEY_METRIC_MS: endpoints[i][KEY_METRIC_MS],
        }
        for i in range(len(endpoints) - 1)
    ]


def _pipeline_chain_latency_report(
    flow: FlowGraph, topic_chains: List[List[str]], duration_s: float
) -> List[dict]:
    """Node-based pipeline report: one entry per topic chain, in milliseconds.

    Args:
        flow (FlowGraph): Built flow graph.
        topic_chains (List[List[str]]): Zero or more ordered topic-name sequences.
        duration_s (float): Trace span, the denominator for the topic_hz/callback_hz
            rates.

    Returns:
        List[dict]: one dict per chain that had at least one full traversal, with keys
        `topic_sequence` (list of [publishing_node, topic]), `traversal_count`,
        `terminator`, `steps` (each: publishing_node, subscribing_node, topic,
        topic_hz, callback_hz, is_indirect, relay_callback_topic, topic_latency_ms,
        take_to_callback_start_ms, callback_time_ms, indirect_wait_ms,
        relay_callback_time_ms), `step_endpoints` (see
        `_chain_step_endpoint_rows()`), and `chain_latency_ms`.
    """
    topic_pub_hz = _topic_publish_hz(flow, duration_s)
    subscription_hz = _subscription_callback_hz(flow, duration_s)

    reports = []
    for topic_sequence in topic_chains:
        traversals = flow.walk_topic_chain(topic_sequence)
        if len(traversals) == 0:
            continue
        step_samples = _chain_step_samples(traversals)
        steps = [
            {
                KEY_PUBLISHING_NODE: sample.publishing_node,
                KEY_SUBSCRIBING_NODE: sample.subscribing_node,
                KEY_TOPIC: sample.topic,
                KEY_TOPIC_HZ: topic_pub_hz.get(sample.topic, 0.0),
                KEY_CALLBACK_HZ: subscription_hz.get(
                    (sample.subscribing_node, sample.topic), 0.0
                ),
                KEY_IS_INDIRECT: sample.is_indirect,
                KEY_RELAY_CALLBACK_TOPIC: sample.relay_callback_topic,
                KEY_TOPIC_LATENCY_MS: stats_ms(stats(sample.network_ns)),
                KEY_TAKE_TO_CALLBACK_START_MS: stats_ms(
                    stats(sample.take_to_callback_start_ns)
                ),
                KEY_CALLBACK_TIME_MS: stats_ms(stats(sample.callback_ns)),
                KEY_INDIRECT_WAIT_MS: stats_ms(stats(sample.indirect_wait_ns)),
                KEY_RELAY_CALLBACK_TIME_MS: stats_ms(stats(sample.relay_callback_ns)),
            }
            for sample in step_samples
        ]
        chain_latencies = [
            traversal.chain_latency_ns
            for traversal in traversals
            if traversal.chain_latency_ns is not None
        ]
        reports.append({
            KEY_TOPIC_SEQUENCE: [
                [sample.publishing_node, sample.topic] for sample in step_samples
            ],
            KEY_TRAVERSAL_COUNT: len(traversals),
            KEY_TERMINATOR: traversals[0].terminator,
            KEY_STEPS: steps,
            KEY_STEP_ENDPOINTS: _chain_step_endpoint_rows(
                steps, traversals[0].terminator
            ),
            KEY_CHAIN_LATENCY_MS: stats_ms(stats(chain_latencies)),
        })
    return reports


def _throughput_report(
    flow: FlowGraph, interval_s: float, topics: Optional[Set[str]] = None
) -> dict:
    """Reshape `get_throughput_buckets()`'s raw per-topic bucket counts into the
    rate-based, shared-time-axis series the node report (and its HTML renderer) expect.

    Args:
        flow (FlowGraph): Built flow graph.
        interval_s (float): Bucket width in seconds.
        topics (Optional[Set[str]]): Restrict the series to these topics, with
            `total_hz` re-summed over what survives; None to report every topic.

    Returns:
        dict: `bucket_interval_s`, `bucket_starts_s` (seconds since the trace's first
        publish), `total_hz`, and `per_topic_hz` -- each series the same length as
        `bucket_starts_s`, short topics right-padded with zero.
    """
    # get_throughput_buckets() to raise ValueError when interval_s <= 0
    buckets = get_throughput_buckets(flow, interval_s)
    cumulative_counts = buckets[CUMULATIVE_TOPIC_KEY][BUCKET_COUNTS_KEY]
    n_buckets = len(cumulative_counts)
    per_topic_hz = {}
    for topic, data in buckets.items():
        if topic in (CUMULATIVE_TOPIC_KEY, BUCKET_INTERVAL_KEY):
            continue
        if topics is not None and topic not in topics:
            continue
        counts = data[BUCKET_COUNTS_KEY]
        padded = counts + [0] * (n_buckets - len(counts))
        per_topic_hz[topic] = [count / interval_s for count in padded]
    # Re-summed rather than read off the cumulative bucket, so the total keeps
    # describing the series beside it once topics are dropped.
    total_hz = (
        [count / interval_s for count in cumulative_counts]
        if topics is None
        else [
            sum(series[i] for series in per_topic_hz.values()) for i in range(n_buckets)
        ]
    )
    return {
        KEY_BUCKET_INTERVAL_S: interval_s,
        KEY_BUCKET_STARTS_S: [i * interval_s for i in range(n_buckets)],
        KEY_TOTAL_HZ: total_hz,
        KEY_PER_TOPIC_HZ: per_topic_hz,
    }


def build_node_report(
    flow: FlowGraph,
    analysis: TraceAnalysis,
    topic_chains: Optional[List[List[str]]] = None,
    ignore_nodes: Optional[List[str]] = None,
    only_topic_chains: bool = False,
    throughput_interval_s: float = 1.0,
) -> dict:
    """Build the node-based structured data backing write_node_report().

    Args:
        flow (FlowGraph): Built flow graph.
        analysis (TraceAnalysis): Built analysis.
        topic_chains (Optional[List[List[str]]]): Zero or more ordered topic-name
            sequences, each forming a pipeline to additionally compute pipeline chain
            latency for. Omitted by default.
        ignore_nodes (Optional[List[str]]): Nodes to ignore in the report. Omitted by default.
        only_topic_chains (bool): Narrow the whole report to `topic_chains`: keep
            only the topics they name, and only the nodes holding an endpoint on
            one. A surviving node keeps its timers but loses its adjacency, its
            publishers and its subscription callbacks on every other topic, so two
            chain participants that also talk over an unrelated topic do not carry
            that topic into the report. Defaults to False.
        throughput_interval_s (float): Width of the throughput buckets, in seconds.

    Returns:
        dict: keys `duration_s` (trace span), `nodes` (node label -> {"to", "from",
        "callbacks", "publishers"}; see _node_based_callback_rows()/
        _node_based_publisher_rows() for row shapes), `topic_chains` (the sequences
        that were requested, so the renderer can tell "none asked for" apart from
        "asked for but nothing matched"), and `pipeline_chain_latency` (one entry per
        chain that had at least one full traversal; see
        _pipeline_chain_latency_report()).

    Raises:
        ValueError: If `only_topic_chains` is set but `topic_chains` names no topic.
    """
    # None rather than "every topic", so each filter below is one `is None or in`
    # test instead of a separate check of the flag that produced it.
    chain_topics = _chain_topics(topic_chains or []) if only_topic_chains else None
    if chain_topics is not None and len(chain_topics) == 0:
        raise ValueError(
            "only_topic_chains needs topic_chains to name at least one topic; with "
            "none there is nothing to keep and the filter empties the report rather "
            "than narrowing it"
        )

    duration_s = (
        (analysis.events[-1].ts_ns - analysis.events[0].ts_ns) / 1_000_000_000
        if analysis.events
        else 0.0
    )
    adjacency = _node_adjacency(flow)
    callback_rows = _node_based_callback_rows(flow, duration_s)
    publisher_rows = _node_based_publisher_rows(flow, duration_s)

    all_nodes = set(adjacency) | set(callback_rows) | set(publisher_rows)

    excluded_nodes = set(ignore_nodes or [])
    if chain_topics is not None:
        excluded_nodes |= all_nodes - _nodes_on_topics(flow, chain_topics)

    nodes = {}
    for node in sorted(all_nodes - excluded_nodes):
        adj = adjacency.get(node, {KEY_TO: [], KEY_FROM: []})
        nodes[node] = {
            KEY_TO: _adjacency_side(adj[KEY_TO], excluded_nodes, chain_topics),
            KEY_FROM: _adjacency_side(adj[KEY_FROM], excluded_nodes, chain_topics),
            KEY_CALLBACKS: _rows_on_topics(callback_rows.get(node, {}), chain_topics),
            KEY_PUBLISHERS: _rows_on_topics(publisher_rows.get(node, {}), chain_topics),
        }

    pipeline_chain_latency = (
        _pipeline_chain_latency_report(flow, topic_chains, duration_s)
        if topic_chains is not None
        else []
    )

    return {
        DURATION_KEY: duration_s,
        KEY_NODES: nodes,
        TOPIC_CHAINS_KEY: [list(chain) for chain in (topic_chains or [])],
        KEY_PIPELINE_CHAIN_LATENCY: pipeline_chain_latency,
        IGNORE_NODES_KEY: ignore_nodes or [],
        ONLY_TOPIC_CHAINS_KEY: only_topic_chains,
        KEY_THROUGHPUT: _throughput_report(flow, throughput_interval_s, chain_topics),
    }
