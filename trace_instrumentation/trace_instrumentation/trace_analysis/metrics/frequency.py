# Copyright 2026 Gritt Robotics Inc.

import csv
from typing import Any, Dict, List, NamedTuple

from trace_instrumentation.trace_analysis.flow_graph import FlowGraph
from trace_instrumentation.trace_analysis.metrics.stats import (
    NANOSECONDS_PER_SECOND,
)

UNKNOWN_TOPIC = "unknown"

TOPIC_KEY = "topic"
CUMULATIVE_TOPIC_KEY = "cumulative_topic_stats"
TOTAL_KEY = "total_publishes"
DURATION_KEY = "duration_s"
MEAN_HZ_KEY = "mean_hz"
BUCKET_START_KEY = "bucket_start_s"
BUCKET_COUNTS_KEY = "bucket_counts"
BUCKET_INTERVAL_KEY = "bucket_interval_s"


class TopicFrequencyStats(NamedTuple):
    """Publish rate stats for one topic, bucketed into interval_seconds-wide
    windows spanning from the first sample to the last occupied bucket."""

    total: int
    duration_s: float
    mean_hz: float


def _publish_rate_stats(
    ts_values: List[int], interval_seconds: float = 1.0
) -> TopicFrequencyStats:
    """Bucket-based publish rate stats for a set of publish timestamps: bucket them
    into interval_seconds-wide windows spanning from the first sample to the last
    occupied bucket, then compute the overall mean rate.

    Args:
        ts_values (List[int]): Publish timestamps in nanoseconds.
        interval_seconds (float): Bucket width in seconds. Defaults to 1.0.

    Returns:
        TopicFrequencyStats: All zero if ts_values is empty.
    """
    if len(ts_values) == 0:
        return TopicFrequencyStats(total=0, duration_s=0.0, mean_hz=0.0)
    interval_ns = int(interval_seconds * NANOSECONDS_PER_SECOND)
    min_ts = min(ts_values)
    max_bucket = max((ts - min_ts) // interval_ns for ts in ts_values)
    duration_s = (max_bucket + 1) * interval_seconds
    total = len(ts_values)
    mean_hz = total / duration_s if duration_s > 0 else 0.0
    return TopicFrequencyStats(total=total, duration_s=duration_s, mean_hz=mean_hz)


def _publish_timestamps_by_topic(flow: FlowGraph) -> Dict[str, List[int]]:
    """Group every publish's rmw timestamp by its resolved topic key.

    Args:
        flow (FlowGraph): Built flow graph.

    Returns:
        Dict[str, List[int]]: topic -> [source_timestamp, ...], for every publish with a
        recorded rmw timestamp.
    """
    ts_by_topic: Dict[str, List[int]] = {}
    for topic_name, topic in flow.topics.items():
        ts_by_topic[topic_name] = []
        for pub_endpoint in topic.publishers:
            target_ts = [
                pub.ts_rmw for pub in pub_endpoint.pubs if pub.ts_rmw is not None
            ]
            ts_by_topic[topic_name].extend(target_ts)

    return ts_by_topic


def _topic_frequency_stats(
    flow: FlowGraph, interval_seconds: float = 1.0
) -> Dict[str, TopicFrequencyStats]:
    """Per-topic frequency stats: real publish rates.

    Args:
        flow (FlowGraph): Built flow graph.
        interval_seconds (float): Time window for real-topic frequency bucketing.
            Defaults to 1.0.

    Returns:
        Dict[str, TopicFrequencyStats]: topic -> its frequency stats.

    Raises:
        ValueError: If interval_seconds is not > 0.
    """
    if interval_seconds <= 0:
        raise ValueError(f"interval_seconds must be > 0, got {interval_seconds}")

    ts_by_topic = _publish_timestamps_by_topic(flow)
    return {
        topic: _publish_rate_stats(ts_values, interval_seconds)
        for topic, ts_values in ts_by_topic.items()
    }


def _topic_publish_hz(flow: FlowGraph, duration_s: float) -> Dict[str, float]:
    """Total publish rate (Hz) per topic over the exact full-trace span.

    Args:
        flow (FlowGraph): Built flow graph.
        duration_s (float): Exact full-trace span in seconds.

    Returns:
        Dict[str, float]: topic name -> (total publishes / duration_s), or 0.0 for
        every topic if duration_s <= 0.
    """
    ts_by_topic = _publish_timestamps_by_topic(flow)
    return {
        topic: (len(ts_values) / duration_s if duration_s > 0 else 0.0)
        for topic, ts_values in ts_by_topic.items()
    }


def get_throughput_buckets(
    flow: FlowGraph, interval_seconds: float = 1.0
) -> Dict[str, Dict[str, Any]]:
    """Bucket every topic's publishes into interval_seconds-wide windows spanning the
    whole trace, plus a cumulative_topic_stats entry summed across topics.

    Args:
        flow (FlowGraph): Built flow graph.
        interval_seconds (float): Bucket width in seconds. Defaults to 1.0.

    Returns:
        Dict[str, Dict[str, Any]]: topic name -> {total_publishes, duration_s,
        mean_hz, bucket_start_s, bucket_counts}, plus a cumulative_topic_stats entry
        summed across topics and a bucket_interval_s entry echoing interval_seconds.

    Raises:
        ValueError: If interval_seconds is not > 0, or rounds to a non-positive
            number of nanoseconds -- either would make the bucket-index division
            below undefined or silently wrong.
    """
    if interval_seconds <= 0:
        raise ValueError(f"interval_seconds must be > 0, got {interval_seconds}")

    interval_ns = int(interval_seconds * NANOSECONDS_PER_SECOND)
    ts_by_topic = _publish_timestamps_by_topic(flow)
    all_ts = [ts for ts_values in ts_by_topic.values() for ts in ts_values]
    min_ts = min(all_ts, default=0)
    buckets_by_topic: Dict[str, Dict[str, Any]] = {}

    cum_total, cum_duration_s, cum_mean_hz = 0, 0.0, 0.0
    running_bucket_counts: List[int] = []
    for topic, ts_values in ts_by_topic.items():
        ts_values.sort()
        max_bucket = (ts_values[-1] - min_ts) // interval_ns if ts_values else 0
        bucket_counts = [0] * (max_bucket + 1)

        for ts in ts_values:
            bucket = (ts - min_ts) // interval_ns
            bucket_counts[bucket] += 1

            if bucket >= len(running_bucket_counts):
                running_bucket_counts.extend(
                    [0] * (bucket - len(running_bucket_counts) + 1)
                )
            running_bucket_counts[bucket] += 1

        start_bucket = min_ts // interval_ns if ts_values else 0
        total = len(ts_values)
        duration_s = (
            (ts_values[-1] - min_ts) / NANOSECONDS_PER_SECOND if ts_values else 0.0
        )
        mean_hz = (total / duration_s) if ts_values and duration_s > 0 else 0.0

        cum_total += total
        cum_duration_s += duration_s
        cum_mean_hz = (cum_total / cum_duration_s) if cum_duration_s > 0 else 0.0

        buckets_by_topic[topic] = {
            TOTAL_KEY: total,
            DURATION_KEY: duration_s,
            MEAN_HZ_KEY: mean_hz,
            BUCKET_START_KEY: start_bucket,
            BUCKET_COUNTS_KEY: bucket_counts,
        }

    buckets_by_topic[CUMULATIVE_TOPIC_KEY] = {
        TOTAL_KEY: cum_total,
        DURATION_KEY: cum_duration_s,
        MEAN_HZ_KEY: cum_mean_hz,
        BUCKET_START_KEY: 0,
        BUCKET_COUNTS_KEY: running_bucket_counts,
    }
    buckets_by_topic[BUCKET_INTERVAL_KEY] = interval_seconds

    return buckets_by_topic


def write_topic_frequency_csv(
    prefix: str, flow: FlowGraph, interval_seconds: float = 1.0
) -> None:
    """
    Analyze publish frequency by topic over time windows.

    Args:
        prefix (str): Path prefix; writes PREFIX_{frequency,throughput}.csv.
        flow (FlowGraph): Built flow graph.
        interval_seconds (float): Time window for real-topic frequency bucketing.
            Defaults to 1.0.
    """
    freq_by_topic = _topic_frequency_stats(flow, interval_seconds)
    with open(f"{prefix}_frequency.csv", "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow([TOPIC_KEY, TOTAL_KEY, DURATION_KEY, MEAN_HZ_KEY])
        for topic in sorted(freq_by_topic.keys()):
            data = freq_by_topic[topic]
            wr.writerow([
                topic,
                data.total,
                f"{data.duration_s:.2f}",
                f"{data.mean_hz:.2f}",
            ])

    buckets_by_topic = get_throughput_buckets(flow, interval_seconds)

    with open(f"{prefix}_throughput.csv", "w", newline="") as fh:
        wr = csv.writer(fh)
        all_buckets = set()
        for topic_buckets in buckets_by_topic.values():
            if isinstance(topic_buckets, dict) and BUCKET_COUNTS_KEY in topic_buckets:
                all_buckets.update(
                    range(
                        topic_buckets[BUCKET_START_KEY],
                        topic_buckets[BUCKET_START_KEY]
                        + len(topic_buckets[BUCKET_COUNTS_KEY]),
                    )
                )

        header = sorted(buckets_by_topic.keys())
        wr.writerow(header)
        for bucket in sorted(all_buckets):
            row = []
            for topic in sorted(buckets_by_topic.keys()):
                topic_buckets = buckets_by_topic[topic]
                if (
                    not isinstance(topic_buckets, dict)
                    or BUCKET_COUNTS_KEY not in topic_buckets
                ):
                    row.append(None)
                    continue
                start = topic_buckets[BUCKET_START_KEY]
                counts = topic_buckets[BUCKET_COUNTS_KEY]
                if bucket < start or bucket >= start + len(counts):
                    count = 0
                else:
                    count = counts[bucket - start]
                row.append(count)
            wr.writerow(row)
