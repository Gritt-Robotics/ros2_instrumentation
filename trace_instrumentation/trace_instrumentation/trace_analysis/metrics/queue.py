# Copyright 2026 Gritt Robotics Inc.

"""
queue.py
"""

import csv
from typing import Dict

from trace_instrumentation.trace_analysis.flow_graph import FlowGraph
from trace_instrumentation.trace_analysis.graph import QueueHealthStats

TOPIC_COLUMN = "topic"
SUB_NODE_COLUMN = "subscriber_node"
NO_SUBSCRIBER_LABEL = "<no_subscriber>"


def _queue_health_stats(flow: FlowGraph) -> Dict[tuple[str, str], QueueHealthStats]:
    """Per-(topic, node) delivery stats: real pub<->sub delivery ratios, unique to each subscriber node.

    Args:
        flow (FlowGraph): Built flow graph.

    Returns:
        Dict[tuple[str, str], QueueHealthStats]: (topic, node) -> its delivery stats.
    """
    result = {}

    for topic_name, topic in flow.topics.items():
        total = sum(len(pub_endpoint.pubs) for pub_endpoint in topic.publishers)
        for sub_endpoint in topic.subscribers:
            delivered = len(sub_endpoint.takes)
            undelivered = total - delivered
            result[(topic_name, sub_endpoint.node.name)] = QueueHealthStats(
                total_published=total,
                delivered=delivered,
                undelivered=undelivered,
                undelivered_pct=100.0 * undelivered / total,
                delivery_ratio=delivered / total,
            )
        if len(topic.subscribers) == 0:
            # no subscribers, but still report the total published count
            result[(topic_name, NO_SUBSCRIBER_LABEL)] = QueueHealthStats(
                total_published=total,
                delivered=0,
                undelivered=total,
                undelivered_pct=100.0,
                delivery_ratio=0.0,
            )

    return result


def write_queue_health_csv(path: str, flow: FlowGraph) -> None:
    """
    Report on pub<->sub delivery: matched, unmatched, and queues.

    Args:
        path (str): Output CSV path.
        flow (FlowGraph): Built flow graph.
    """
    stats_by_topic = _queue_health_stats(flow)
    with open(path, "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow([TOPIC_COLUMN, SUB_NODE_COLUMN, *QueueHealthStats._fields])
        for topic, node in sorted(stats_by_topic.keys()):
            s = stats_by_topic[(topic, node)]
            wr.writerow([
                topic,
                node,
                s.total_published,
                s.delivered,
                s.undelivered,
                f"{s.undelivered_pct:.2f}",
                f"{s.delivery_ratio:.3f}",
            ])
