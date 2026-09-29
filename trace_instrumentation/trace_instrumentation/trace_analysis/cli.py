# Copyright 2026 Gritt Robotics Inc.
# ruff: noqa: T201 to allow print statements for this CLI tool's report output

"""
cli.py
======

Command-line entrypoint for the trace analyzer: parse a CTF trace directory and
optionally write the per-metric CSV reports and the node-based combined report
(`--node-report`, JSON only). An HTML report rendered from that same data is
available via `trace_report`'s own `generate_trace_report` tool, which this
package does not depend on.

Pipeline chain latency is reported through `--node-report` with `--topic-chain`,
which carries the same per-step numbers.

`--dump-events-json` bypasses all derived analysis and dumps the raw decoded
event stream itself as Chrome Trace Event Format JSON, for root-cause work the
built-in reports don't cover. It honours `--ignore-nodes`, which is how a large dump
is cut to a size a viewer can open.
"""

import argparse
import json
import os
import sys
from typing import Callable, List, NamedTuple, Optional, Sequence

from trace_instrumentation.trace_analysis.analysis import TraceAnalysis
from trace_instrumentation.trace_analysis.ctf.reader import read_trace
from trace_instrumentation.trace_analysis.flow_graph import (
    MIN_CHAIN_TOPICS,
    FlowGraph,
    split_chain_step,
    split_chain_terminator,
)
from trace_instrumentation.trace_analysis.graph import (
    CHAIN_TERMINATOR_PUB,
    CHAIN_TERMINATOR_TAKE,
)
from trace_instrumentation.trace_analysis.metrics.frequency import (
    write_topic_frequency_csv,
)
from trace_instrumentation.trace_analysis.metrics.latency import (
    write_callback_summary_csv,
    write_python_zone_csv,
    write_topic_latency_csv,
)
from trace_instrumentation.trace_analysis.metrics.queue import (
    write_queue_health_csv,
)
from trace_instrumentation.trace_analysis.raw_export import (
    write_events_trace_json,
)
from trace_instrumentation.trace_analysis.reports.report import (
    build_node_report,
)

# separator between topic names in --topic-chain
TOPIC_CHAIN_SEPARATOR = ","

# --node-report always writes to this basename inside the given directory.
NODE_REPORT_FILENAME = "trace_summary.json"


class CsvOutput(NamedTuple):
    """One optional CSV output, and how to produce it from a parsed trace."""

    dest: str  # argparse destination holding the requested output path
    label: str  # what the "# wrote ..." note on stderr calls it
    # Adapts each writer to one signature so main() can dispatch them in a loop.
    write: Callable[[str, FlowGraph, TraceAnalysis], None]
    # What the note reports as written; overridden when one flag writes several files.
    written: Callable[[str], str] = lambda path: path


CSV_OUTPUTS = [
    CsvOutput(
        "python_zones",
        "Python zones",
        lambda path, _flow, analysis: write_python_zone_csv(path, analysis),
    ),
    CsvOutput(
        "topic_latency",
        "topic latency",
        lambda path, flow, _analysis: write_topic_latency_csv(path, flow),
    ),
    CsvOutput(
        "callback_summary",
        "callback summary",
        lambda path, flow, _analysis: write_callback_summary_csv(path, flow),
    ),
    CsvOutput(
        "topic_frequency",
        "topic frequency",
        lambda path, flow, _analysis: write_topic_frequency_csv(path, flow),
        lambda path: f"{path}_frequency.csv, {path}_throughput.csv",
    ),
    CsvOutput(
        "queue_health",
        "queue health",
        lambda path, flow, _analysis: write_queue_health_csv(path, flow),
    ),
]


def topic_chain_error(chain: List[str]) -> Optional[str]:
    """
    Check one --topic-chain value for a spec the chain walk cannot honour.

    Args:
        chain (List[str]): One chain's comma-separated parts, as given on the command
            line, terminator token included.

    Returns:
        Optional[str]: The message to fail with, or None when the chain is usable.
    """
    spec = split_chain_terminator(chain)
    given = TOPIC_CHAIN_SEPARATOR.join(chain)
    if len(spec.steps) < MIN_CHAIN_TOPICS:
        # Only name a terminator the user actually wrote.
        before_terminator = (
            f" before its '{spec.terminator}' terminator"
            if len(spec.steps) < len(chain)
            else ""
        )
        return (
            f"--topic-chain '{given}' names {len(spec.steps)} topic(s)"
            f"{before_terminator}; a chain needs at least {MIN_CHAIN_TOPICS}"
        )
    sink_topic, sink_node = split_chain_step(spec.steps[-1])
    if spec.terminator == CHAIN_TERMINATOR_PUB and sink_node is not None:
        return (
            f"--topic-chain '{given}' ends at the publish onto '{sink_topic}', so "
            f"'@{sink_node}' pins a consumer that is not part of the chain: nothing has "
            f"taken the message yet. Drop the pin, or end the chain with "
            f"'{CHAIN_TERMINATOR_TAKE}' to measure through that node's take"
        )
    return None


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Analyze a ROS 2 LTTng/CTF trace and write its JSON/CSV reports."
    )
    parser.add_argument(
        "trace_dir",
        nargs="?",
        default=".",
        help="Path to the trace directory (session dir or the dir containing "
        "'metadata' directly). Defaults to the current directory.",
    )
    parser.add_argument(
        "--topic-chain",
        "--topic-chains",
        dest="topic_chain",
        action="append",
        default=None,
        metavar=f"TOPIC0,TOPIC1,...[,{CHAIN_TERMINATOR_PUB}|,{CHAIN_TERMINATOR_TAKE}]",
        help="Ordered, comma-separated topic names forming a multi-step pipeline. A "
        "step may pin its consuming node as TOPIC@NODE (e.g. "
        "'/topic_a,/topic_b@/consumer_node'), which is how a "
        "topic with several subscribers resolves to the one the chain means. A trailing "
        f"'{CHAIN_TERMINATOR_PUB}' or '{CHAIN_TERMINATOR_TAKE}' says where a traversal "
        f"completes: at the publish onto the last topic, or (the default, "
        f"'{CHAIN_TERMINATOR_TAKE}') at a subscriber's take of it. May "
        "be given more than once to report several chains; --node-report covers "
        "every chain given.",
    )
    parser.add_argument(
        "--topic-latency", metavar="FILE", help="Write topic latency stats to FILE"
    )
    parser.add_argument(
        "--callback-duration",
        metavar="FILE",
        help="Write callback duration stats to FILE",
    )
    parser.add_argument(
        "--callback-summary",
        metavar="FILE",
        help="Write per-(node, callback_type, topic) callback duration summary to FILE",
    )
    parser.add_argument(
        "--python-zones",
        metavar="FILE",
        help="Write Python zone duration stats to FILE",
    )
    parser.add_argument(
        "--timer-period",
        metavar="FILE",
        help="Write timer observed vs configured period jitter to FILE",
    )
    parser.add_argument(
        "--topic-frequency",
        metavar="PREFIX",
        help="Write topic frequency analysis to PREFIX_{frequency,throughput}.csv",
    )
    parser.add_argument(
        "--queue-health", metavar="FILE", help="Write subscriber queue health to FILE"
    )
    parser.add_argument(
        "--node-report",
        metavar="DIR",
        help="Write the node-based combined report as JSON to "
        f"DIR/{NODE_REPORT_FILENAME}. DIR is created if it doesn't already exist.",
    )
    parser.add_argument(
        "--ignore-nodes",
        dest="ignore_nodes",
        action="append",
        default=None,
        metavar="NODE0,NODE1,...",
        help="Removes a node from the node-based report and from --dump-events-json. "
        "Can be specified multiple times.",
    )
    parser.add_argument(
        "--only-topic-chains",
        dest="only_topic_chains",
        action="store_true",
        help="Narrow the node-based report to --topic-chain: keep only the topics "
        "the chains name and only the nodes with an endpoint on one. A kept node "
        "keeps its timers but loses its edges, publishers and subscription callbacks "
        "on every other topic. Requires --topic-chain.",
    )
    parser.add_argument(
        "--dump-events-json",
        dest="dump_events_json",
        metavar="FILE",
        help="Write every decoded event as Chrome Trace Event Format JSON to FILE, "
        "for any viewer that reads that format.",
    )
    parser.add_argument(
        "--throughput-interval",
        dest="throughput_interval",
        type=float,
        default=1.0,
        help="Time window in seconds for throughput bucketing in the node-based report. Defaults to 1.0.",
    )
    args = parser.parse_args(argv)

    if args.topic_chain is not None and any(len(tc) == 0 for tc in args.topic_chain):
        parser.error("--topic-chain requires a topic name, got an empty string")
    if args.ignore_nodes is not None and any(len(n) == 0 for n in args.ignore_nodes):
        parser.error("--ignore-nodes requires a node name, got an empty string")
    if args.dump_events_json is not None and len(args.dump_events_json) == 0:
        parser.error("--dump-events-json requires a path, got an empty string")
    # Without a chain there is no topic to keep, so the filter would empty the report
    # rather than narrow it.
    if args.only_topic_chains and args.topic_chain is None:
        parser.error("--only-topic-chains requires --topic-chain")

    topic_chains = (
        [tc.split(TOPIC_CHAIN_SEPARATOR) for tc in args.topic_chain]
        if args.topic_chain is not None
        else None
    )
    for chain in topic_chains or []:
        error = topic_chain_error(chain)
        if error is not None:
            parser.error(error)

    meta, _ctf_dir, events = read_trace(args.trace_dir)
    analysis = TraceAnalysis(meta, events).build()
    flow = FlowGraph(meta, events, analysis).build()

    ignore_nodes = [
        node
        for spec in (args.ignore_nodes or [])
        for node in spec.split(TOPIC_CHAIN_SEPARATOR)
    ]

    for output in CSV_OUTPUTS:
        path = getattr(args, output.dest)
        if path:
            output.write(path, flow, analysis)
            print(f"# wrote {output.label} -> {output.written(path)}", file=sys.stderr)

    if args.dump_events_json is not None:
        write_events_trace_json(args.dump_events_json, events, analysis, ignore_nodes)
        print(f"# wrote event dump -> {args.dump_events_json}", file=sys.stderr)

    if args.node_report:
        node_report_data = build_node_report(
            flow,
            analysis,
            topic_chains=topic_chains,
            ignore_nodes=ignore_nodes,
            only_topic_chains=args.only_topic_chains,
            throughput_interval_s=args.throughput_interval,
        )
        report_path = write_node_report(args.node_report, node_report_data)
        print(f"# wrote node report -> {report_path}", file=sys.stderr)


def write_node_report(directory: str, data: dict) -> str:
    """Write the node-based report as JSON to `directory`/`NODE_REPORT_FILENAME`,
    creating `directory` if it doesn't already exist.

    Takes already-built data rather than building it, so the JSON report and the
    HTML report both render one `build_node_report()` result and cannot drift.

    Args:
        directory (str): Directory the report is written into.
        data (dict): `build_node_report()` output.

    Returns:
        str: The path the report was written to.
    """
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, NODE_REPORT_FILENAME)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=2, default=str))
    return path


if __name__ == "__main__":
    main()
