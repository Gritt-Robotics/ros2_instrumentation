# Copyright 2026 Gritt Robotics Inc.
# ruff: noqa: T201 to allow print statements for this CLI tool's report output

"""Ground-truth pipeline chain latency, computed directly from trace-validation
CSVs (no LTTng trace needed).

This is what `analyze_trace --topic-chain` is validated against: it
stitches one publisher CSV, zero or more relay CSVs, and one subscriber CSV together
via the `chain_id`/`step_index`/`upstream_trace_id` ground-truth columns those nodes
write when `message_type=chain`, and reports per-step network/processing latency plus
end-to-end latency computed purely from each row's own wall-clock timestamps.
"""

import argparse
import contextlib
import csv
import sys
from typing import Dict, List, Optional

from trace_instrumentation.trace_analysis.metrics.stats import (
    fmt_ns,
    stats,
    write_stat_row,
)

CSV_FIELD_CHAIN_ID = "chain_id"
CSV_FIELD_STEP_INDEX = "step_index"
CSV_FIELD_UPSTREAM_TRACE_ID = "upstream_trace_id"
CSV_FIELD_TRACE_ID = "trace_id"
CSV_FIELD_WALL_PUBLISH_NS = "wall_publish_ns"
CSV_FIELD_WALL_RECV_NS = "wall_recv_ns"

RESULT_KEY_TRAVERSAL_COUNT = "traversal_count"
RESULT_KEY_DROPPED_COUNT = "dropped_count"
RESULT_KEY_STEPS = "steps"
RESULT_KEY_TOTAL_LATENCY_NS = "total_latency_ns"

STEP_KEY_FROM_STEP = "from_step"
STEP_KEY_TO_STEP = "to_step"
STEP_KEY_NETWORK_LATENCY_NS = "network_latency_ns"
STEP_KEY_PROCESSING_LATENCY_NS = "processing_latency_ns"

STAT_KIND_NETWORK = "network"
STAT_KIND_PROCESSING = "processing"
STAT_KIND_END_TO_END = "end_to_end"

COL_STEP = "step"
COL_KIND = "kind"
COL_COUNT = "count"
COL_MIN_NS = "min_ns"
COL_MEAN_NS = "mean_ns"
COL_MEDIAN_NS = "median_ns"
COL_P99_NS = "p99_ns"
COL_MAX_NS = "max_ns"

CSV_OUTPUT_HEADER = [
    COL_STEP,
    COL_KIND,
    COL_COUNT,
    COL_MIN_NS,
    COL_MEAN_NS,
    COL_MEDIAN_NS,
    COL_P99_NS,
    COL_MAX_NS,
]


def load_step_rows_by_chain_id(path: str) -> Dict[int, dict]:
    """
    Load one step's CSV into a chain_id -> row lookup.

    Args:
        path (str): Path to a publisher/relay/subscriber ground-truth CSV written in
                    chain mode (message_type=chain).

    Returns:
        Dict[int, dict]: chain_id -> the CSV row for that chain instance's message at
                    this step, with numeric fields converted to int.

    Raises:
        ValueError: If the CSV doesn't have a `chain_id` column (i.e. it wasn't
                    written in chain mode).
    """
    rows_by_chain_id = {}
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        if CSV_FIELD_CHAIN_ID not in (reader.fieldnames or []):
            raise ValueError(
                f"{path} has no '{CSV_FIELD_CHAIN_ID}' column -- was it written with "
                "message_type=chain?"
            )
        for row in reader:
            for field in row:
                if row[field] != "" and row[field] is not None:
                    with contextlib.suppress(ValueError):
                        row[field] = int(row[field])
            rows_by_chain_id[row[CSV_FIELD_CHAIN_ID]] = row
    return rows_by_chain_id


def compute_chain_latency(step_csv_paths: List[str]) -> dict:
    """
    Stitch an ordered list of step CSVs into per-step and end-to-end latency stats.

    Args:
        step_csv_paths (List[str]): Ordered CSV paths, step 0 (publisher) through step N
                    (subscriber), matching the step_index each row carries.

    Returns:
        dict: keys `traversal_count` (chains present in every step), `dropped_count`
                    (chains present at step 0 but missing or mismatched downstream),
                    `steps` (list of {"from_step", "to_step", "network_latency_ns",
                    "processing_latency_ns"} stats dicts), `total_latency_ns` (stats
                    dict).

    Raises:
        ValueError: If fewer than 2 step CSV paths are given (a publisher-only "chain"
                    has no downstream step to compute latency against).
    """
    if len(step_csv_paths) < 2:
        raise ValueError(
            f"compute_chain_latency needs at least 2 step CSVs (publisher + subscriber), "
            f"got {len(step_csv_paths)}"
        )
    n_steps = len(step_csv_paths) - 1
    steps_by_index = [load_step_rows_by_chain_id(p) for p in step_csv_paths]

    step_network = [[] for _ in range(n_steps)]
    step_processing = [[] for _ in range(n_steps)]
    total_latencies = []
    dropped_count = 0
    traversal_count = 0

    for chain_id, origin_row in steps_by_index[0].items():
        rows = [origin_row]
        ok = True
        for step_index in range(1, n_steps + 1):
            row = steps_by_index[step_index].get(chain_id)
            is_sink_step = step_index == n_steps
            # Relay steps record the upstream trace_id in upstream_trace_id; the sink
            # never re-publishes, so it logs the upstream trace_id unchanged as trace_id.
            if row is None:
                ok = False
                break
            expected_field = (
                CSV_FIELD_TRACE_ID if is_sink_step else CSV_FIELD_UPSTREAM_TRACE_ID
            )
            if row[expected_field] != rows[-1][CSV_FIELD_TRACE_ID]:
                ok = False
                break
            rows.append(row)
        if not ok:
            dropped_count += 1
            continue

        traversal_count += 1
        for step_index in range(n_steps):
            upstream_row = rows[step_index]
            downstream_row = rows[step_index + 1]
            step_network[step_index].append(
                downstream_row[CSV_FIELD_WALL_RECV_NS]
                - upstream_row[CSV_FIELD_WALL_PUBLISH_NS]
            )
            if (
                CSV_FIELD_WALL_RECV_NS in upstream_row
                and CSV_FIELD_WALL_PUBLISH_NS in upstream_row
            ):
                # upstream_row is a relay's own row: it both received and published.
                step_processing[step_index].append(
                    upstream_row[CSV_FIELD_WALL_PUBLISH_NS]
                    - upstream_row[CSV_FIELD_WALL_RECV_NS]
                )
        total_latencies.append(
            rows[-1][CSV_FIELD_WALL_RECV_NS] - rows[0][CSV_FIELD_WALL_PUBLISH_NS]
        )

    steps_out = [
        {
            STEP_KEY_FROM_STEP: step_index,
            STEP_KEY_TO_STEP: step_index + 1,
            STEP_KEY_NETWORK_LATENCY_NS: stats(step_network[step_index]),
            STEP_KEY_PROCESSING_LATENCY_NS: stats(step_processing[step_index]),
        }
        for step_index in range(n_steps)
    ]

    return {
        RESULT_KEY_TRAVERSAL_COUNT: traversal_count,
        RESULT_KEY_DROPPED_COUNT: dropped_count,
        RESULT_KEY_STEPS: steps_out,
        RESULT_KEY_TOTAL_LATENCY_NS: stats(total_latencies),
    }


def render_report(result: dict) -> str:
    """
    Render a human-readable ground-truth pipeline chain latency report.

    Args:
        result (dict): Return value of compute_chain_latency().

    Returns:
        str: The rendered report text.
    """
    out = []
    w = out.append
    w("=" * 78)
    w("GROUND-TRUTH PIPELINE CHAIN LATENCY")
    w("=" * 78)
    w(f"  chains traversed the full pipeline: {result[RESULT_KEY_TRAVERSAL_COUNT]:,}")
    w(f"  chains dropped/mismatched mid-chain: {result[RESULT_KEY_DROPPED_COUNT]:,}")
    w("")
    w(f"  {'count':>8} {'median':>10} {'p99':>10} {'max':>10}  step")
    for step in result[RESULT_KEY_STEPS]:
        net = step[STEP_KEY_NETWORK_LATENCY_NS]
        proc = step[STEP_KEY_PROCESSING_LATENCY_NS]
        if net is not None:
            w(
                f"  {net.count:>8} {fmt_ns(net.p50):>10} {fmt_ns(net.p99):>10} "
                f"{fmt_ns(net.max):>10}  step {step[STEP_KEY_FROM_STEP]} -> "
                f"{step[STEP_KEY_TO_STEP]} ({STAT_KIND_NETWORK})"
            )
        if proc is not None:
            w(
                f"  {proc.count:>8} {fmt_ns(proc.p50):>10} {fmt_ns(proc.p99):>10} "
                f"{fmt_ns(proc.max):>10}  step {step[STEP_KEY_FROM_STEP]} ({STAT_KIND_PROCESSING})"
            )
    total = result[RESULT_KEY_TOTAL_LATENCY_NS]
    if total is not None:
        w("")
        w(
            f"  end-to-end: {total.count:>8} {fmt_ns(total.p50):>10} "
            f"{fmt_ns(total.p99):>10} {fmt_ns(total.max):>10}"
        )
    return "\n".join(out)


def write_csv(path: str, result: dict) -> None:
    """
    Write per-step and end-to-end ground-truth latency stats to CSV.

    Args:
        path (str): Output CSV path.
        result (dict): Return value of compute_chain_latency().
    """
    with open(path, "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(CSV_OUTPUT_HEADER)

        for step in result[RESULT_KEY_STEPS]:
            write_stat_row(
                wr,
                f"{step[STEP_KEY_FROM_STEP]} -> {step[STEP_KEY_TO_STEP]}",
                STAT_KIND_NETWORK,
                step[STEP_KEY_NETWORK_LATENCY_NS],
            )
            write_stat_row(
                wr,
                f"step {step[STEP_KEY_FROM_STEP]}",
                STAT_KIND_PROCESSING,
                step[STEP_KEY_PROCESSING_LATENCY_NS],
            )
        write_stat_row(
            wr,
            STAT_KIND_END_TO_END,
            STAT_KIND_END_TO_END,
            result[RESULT_KEY_TOTAL_LATENCY_NS],
        )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Ground-truth pipeline chain latency from trace-validation "
        "CSVs (validates analyze_trace --topic-chain against known-correct data)."
    )
    ap.add_argument(
        "csv_paths",
        nargs="+",
        metavar="STEP_CSV",
        help="Ordered step CSV paths: step 0's publisher CSV, then each relay's CSV in "
        "order, then the terminal subscriber's CSV. All must be written with "
        "message_type=chain.",
    )
    ap.add_argument(
        "--output", metavar="FILE", help="Write per-step stats to a CSV file"
    )
    args = ap.parse_args(argv)

    if len(args.csv_paths) < 2:
        print(
            "error: need at least 2 step CSVs (publisher + subscriber)", file=sys.stderr
        )
        return 1

    result = compute_chain_latency(args.csv_paths)
    print(render_report(result))

    if args.output:
        write_csv(args.output, result)
        print(f"# wrote pipeline chain latency -> {args.output}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
