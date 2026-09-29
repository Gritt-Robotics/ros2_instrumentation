# Copyright 2026 Gritt Robotics Inc.

"""Reusable LTTng tracing action for ROS2 launch files.

`add_lttng_trace()` wraps a list of launch entities (nodes, includes, timers -- anything)
with LTTng tracing: it starts a `Trace` action, delays the wrapped entities until the
trace session is guaranteed active, and registers an `OnShutdown` handler that runs
`analyze_trace` once the session has been stopped and flushed, writing a combined
report to `<csv_dir>/<trace_name>_report/trace_summary.json`. The raw CTF trace itself is
written to `<csv_dir>/ctf/`.

What that report covers is shaped by `topic_chains`, `ignore_nodes` and
`only_topic_chains`, so a launch file can declare the pipelines it cares about and the
infrastructure it does not once, instead of re-running the analyzer by hand afterwards.

`trace_name` must already be the exact, unique-enough-for-this-run name (this function
does not stamp a timestamp onto it), and `csv_dir` must already be the directory that run's
outputs should live in. Callers that want the classic "one timestamped folder per run,
CSVs co-located with the trace and report" layout should compute that folder themselves
with `compute_session_dir()` and pass its two return values in as `trace_name`/`csv_dir` --
see `trace_validation.launch.py` for the reference usage.

This function takes plain resolved values (`enabled`, `trace_name`, `csv_dir`) rather than
a launch `context` it reads `LaunchConfiguration`s from, so it makes no assumption about
what a caller's launch arguments are named. Any `launch_setup(context)` can resolve its
own flag/name/directory however it likes and pass the results in -- that is what makes
this a genuine drop-in for any launch file, not just ones that share a naming convention.

Known limitation: LTTng sessions are host-wide, not scoped to the processes started by one
launch file. If this action is used in two launch files that run concurrently (e.g. two
subsystems started as separate `ros2 launch` processes at the same time), each session
will also pick up UST tracepoints from the other's nodes, and each will write its own
possibly-overlapping report. Enable tracing on one subsystem at a time for a clean report.
"""

import os
from typing import List, Optional, Tuple

from launch.actions import OpaqueFunction, TimerAction
from launch.event_handlers import OnShutdown
from launch.launch_context import LaunchContext
from launch.launch_description_entity import LaunchDescriptionEntity
from tracetools_launch.action import Trace
from tracetools_trace.tools import names as tracetools_names
from tracetools_trace.tools.path import append_timestamp

TRACE_EVENTS_UST = tracetools_names.DEFAULT_EVENTS_UST

# python3-lttngust forwards *all* root-propagated logging regardless of the enabled
# rule's name, so use '*' and distinguish sources via the `logger_name` field instead.
TRACE_EVENTS_PYTHON = ["*"]

# Delay between starting the LTTng trace session and starting the wrapped entities.
TRACE_STARTUP_DELAY_S = 5.0

# LTTng's procname context is the kernel's comm, capped at 16 bytes including the
# terminator, so a longer executable only ever appears in a trace truncated to this.
LTTNG_PROCNAME_MAX_LEN = 15

# Characters that would terminate or escape the quoted string in a filter expression.
PROCNAME_FORBIDDEN_CHARS = '"\\'

LTTNG_PROCNAME_CONTEXT_FIELD = "$ctx.procname"
LTTNG_FILTER_OR = " || "

# Renamed to after the fact, not used from the start: the session name must stay unique
# host-wide for lttng-sessiond, so a fixed one there would collide across runs.
CTF_TRACE_DIRNAME = "ctf"


def procname_filter_expression(process_names: List[str]) -> str:
    """
    Build the LTTng filter expression that records only the named processes.

    ROS 2's `ros2:*` tracepoints are compiled into rclcpp, and an LTTng event rule is
    global to the host, so every running node emits them by default. `procname` is the
    only discriminator available to a filter -- the tracepoint payloads carry handle
    pointers, not node names -- so selection is by executable name, not by ROS node name.

    Names are truncated to `LTTNG_PROCNAME_MAX_LEN` to match what the tracer records, so
    a caller writes the executable's real name and does not have to truncate by hand.
    Truncation makes the match a prefix match: a process sharing a listed name's first
    `LTTNG_PROCNAME_MAX_LEN` characters is recorded too.

    Args:
        process_names (List[str]): Executable names of the processes to record.

    Returns:
        str: The filter expression to attach to every enabled UST event rule.

    Raises:
        ValueError: If `process_names` is not a list, is empty, holds a name that is not
                    a string, is empty, or holds a character that would break out of the
                    expression's quoted string, or if two names collide once truncated
                    (which would silently record both).
    """
    # A bare string is iterable, so without this it yields one clause per character --
    # an expression that is valid, matches no process, and records an empty trace.
    if not isinstance(process_names, list):
        raise ValueError(
            "procname_filter_expression needs a list of process names, got "
            f"{type(process_names).__name__}"
        )
    if len(process_names) == 0:
        raise ValueError("procname_filter_expression needs at least one process name")

    truncated = {}
    for name in process_names:
        if not isinstance(name, str):
            raise ValueError(f"process name {name!r} is not a string")
        if len(name) == 0:
            raise ValueError(f"empty process name in {process_names}")
        forbidden = sorted(set(name) & set(PROCNAME_FORBIDDEN_CHARS))
        if len(forbidden) > 0:
            raise ValueError(
                f"process name {name!r} holds forbidden characters {forbidden}"
            )
        comm = name[:LTTNG_PROCNAME_MAX_LEN]
        if comm in truncated and truncated[comm] != name:
            raise ValueError(
                f"process names {truncated[comm]!r} and {name!r} are indistinguishable "
                f"once truncated to the {LTTNG_PROCNAME_MAX_LEN} characters LTTng records"
            )
        truncated[comm] = name
    return LTTNG_FILTER_OR.join(
        f'{LTTNG_PROCNAME_CONTEXT_FIELD} == "{comm}"' for comm in sorted(truncated)
    )


def compute_session_dir(csv_dir: str, trace_name: str) -> Tuple[str, str]:
    """
    Compute this run's unique session name and the directory its outputs should live in.

    Args:
        csv_dir (str): Base directory under which the per-run session directory is
                    created.
        trace_name (str): Base name identifying this trace; a timestamp is appended to
                    form the unique session name.

    Returns:
        Tuple[str, str]: `(session_name, session_dir)`, where `session_name` is
                    `trace_name` with a timestamp appended, and `session_dir` is the
                    absolute path to `os.path.join(csv_dir, session_name)` -- resolved
                    to absolute here (not left to the caller or to
                    `add_lttng_trace()`'s own internal resolution) so that per-node
                    output paths built from `session_dir` land in the same place
                    regardless of a launched node's working directory. The caller is
                    responsible for creating `session_dir` (e.g. via
                    `os.makedirs(session_dir, exist_ok=True)`) before writing into it,
                    and for passing `session_name`/`session_dir` into
                    `add_lttng_trace()` as `trace_name`/`csv_dir` respectively.
    """
    session_name = append_timestamp(trace_name)
    session_dir = os.path.abspath(os.path.join(csv_dir, session_name))
    return session_name, session_dir


def build_analysis_argv(
    session_dir: str,
    node_report_dir: str,
    topic_chains: Optional[List[List[str]]],
    ignore_nodes: Optional[List[str]] = None,
    only_topic_chains: bool = False,
) -> List[str]:
    """
    Build the analyzer's argument vector for a completed trace session.

    These are the arguments the `analyze_trace` CLI parses, with no executable
    prefix: the analysis runs in-process (see `add_lttng_trace()`), so nothing is
    shelled out to.

    Args:
        session_dir (str): Directory containing the raw CTF trace to analyze.
        node_report_dir (str): Directory the node-based JSON report
                    (`trace_summary.json`) is written into.
        topic_chains (Optional[List[List[str]]]): Chains to report, each a list of
                    ordered topic names forming a multi-step pipeline. Emitted as one
                    repeated `--topic-chain` per chain. None or empty skips
                    chain-latency analysis.
        ignore_nodes (Optional[List[str]]): Node names to drop from the report,
                    emitted as one repeated `--ignore-nodes` per node so a name is
                    never split on a separator. Defaults to None (keep every node).
        only_topic_chains (bool): Whether to narrow the report to just the chains,
                    via `--only-topic-chains`. Defaults to False.

    Returns:
        List[str]: The argv to pass to `trace_analysis.cli.main()`.
    """
    argv = [session_dir, "--node-report", node_report_dir]
    for chain in topic_chains or []:
        argv += ["--topic-chain", ",".join(chain)]
    for node in ignore_nodes or []:
        argv += ["--ignore-nodes", node]
    if only_topic_chains:
        argv += ["--only-topic-chains"]
    return argv


def add_lttng_trace(
    entities: List[LaunchDescriptionEntity],
    *,
    enabled: bool,
    trace_name: str,
    csv_dir: str,
    events_ust: Optional[List[str]] = None,
    events_python: Optional[List[str]] = None,
    trace_processes: Optional[List[str]] = None,
    startup_delay_s: float = TRACE_STARTUP_DELAY_S,
    topic_chains: Optional[List[List[str]]] = None,
    ignore_nodes: Optional[List[str]] = None,
    only_topic_chains: bool = False,
) -> List[LaunchDescriptionEntity]:
    """
    Wrap launch entities with LTTng tracing and automatic post-shutdown analysis.

    Args:
        entities (List[LaunchDescriptionEntity]): Launch entities (nodes, includes,
                    timers, etc.) to delay until the trace session is active and run
                    under tracing. Left untouched if `enabled` is False.
        enabled (bool): Whether to actually enable tracing. If False, `entities` is
                    returned unchanged with no side effects.
        trace_name (str): The exact, already-unique-enough name for this trace/session --
                    used directly as the LTTng session name and to name the node report's
                    directory (`<csv_dir>/<trace_name>_report/trace_summary.json`). This
                    function does not append a timestamp itself; callers needing
                    per-run uniqueness (e.g. to avoid colliding trace sessions/reports
                    when running more than one traced launch file concurrently) must
                    stamp it themselves, e.g. via `compute_session_dir()`.
        csv_dir (str): Directory the node report directory and the raw CTF trace
                    (under a `ctf/` subdirectory) are written to. Created if it does
                    not already exist.
                    Relative paths are resolved to absolute before being passed to the
                    Trace action, so relative defaults like "trace" work as
                    expected.
        events_ust (Optional[List[str]]): UST tracepoint names to enable. Defaults to
                    `TRACE_EVENTS_UST` (every ros2:* tracepoint) if None.
        events_python (Optional[List[str]]): Python (lttngust) event rules to enable.
                    Defaults to `TRACE_EVENTS_PYTHON` (all root-logger events) if None.
        trace_processes (Optional[List[str]]): Executable names of the processes to
                    record UST events for, as one `procname` filter. None or empty
                    records every process on the host, so an unfiltered trace is
                    dominated by the busiest node's events. Selection is by executable name rather than ROS
                    node name -- see `procname_filter_expression()`. Defaults to None.
        startup_delay_s (float): Seconds to wait after starting the trace session before
                    starting `entities`. Defaults to `TRACE_STARTUP_DELAY_S`.
        topic_chains (Optional[List[List[str]]]): Chains to report, each a list of
                    ordered topic names forming a multi-step pipeline (e.g.
                    `[topic_0, topic_1, ..., topic_n]`). For every chain given (2 or
                    more topics each), the post-shutdown analysis walks that topic
                    sequence through the trace's detected chain hubs and reports
                    per-step and end-to-end pipeline chain latency. Omitted by default.
        ignore_nodes (Optional[List[str]]): Node names to drop from the report. Use it
                    for launch and visualization infrastructure whose callbacks would
                    otherwise crowd out the nodes under study. Defaults to None.
        only_topic_chains (bool): Whether to narrow the whole report to the topics and
                    nodes the chains name. Requires `topic_chains`. Defaults to False.

    Returns:
        List[LaunchDescriptionEntity]: `entities` unchanged if `enabled` is False,
                    otherwise `[trace_action, delayed_entities, analysis_handler]`.

    Raises:
        ValueError: If any entry in `topic_chains` contains fewer than 2 topics, or if
                    `only_topic_chains` is set without `topic_chains` (which would
                    empty the report rather than narrow it).
    """
    if not enabled:
        return entities

    for chain in topic_chains or []:
        if len(chain) < 2:
            raise ValueError(
                f"every topic chain must contain at least 2 topics, got {chain}"
            )
    # Without a chain there is no topic to keep, so the filter empties the report.
    if only_topic_chains and len(topic_chains or []) == 0:
        raise ValueError("only_topic_chains requires topic_chains")

    os.makedirs(csv_dir, exist_ok=True)
    csv_dir = os.path.abspath(csv_dir)

    # base_path roots the CTF trace under csv_dir, instead of the Trace action's
    # default ~/.ros/tracing/<session_name>, so a run's outputs stay together.
    raw_trace_dir = os.path.join(csv_dir, trace_name)
    ctf_dir = os.path.join(csv_dir, CTF_TRACE_DIRNAME)
    # Distinct from raw_trace_dir/ctf_dir so this directory's lifetime never depends on
    # the raw-trace rename below.
    node_report_dir = os.path.join(csv_dir, f"{trace_name}_report")

    trace_action = Trace(
        session_name=trace_name,
        base_path=csv_dir,
        events_ust=events_ust if events_ust is not None else TRACE_EVENTS_UST,
        events_python=(
            events_python if events_python is not None else TRACE_EVENTS_PYTHON
        ),
        events_ust_filter=(
            procname_filter_expression(trace_processes)
            if len(trace_processes or []) > 0
            else None
        ),
    )

    delayed_entities = TimerAction(period=startup_delay_s, actions=entities)

    def _run_analysis(_event, _context):
        if os.path.exists(ctf_dir):
            raise FileExistsError(
                f"{ctf_dir} already exists (a previous run's raw CTF trace) -- remove "
                "it, or use a different csv_dir, before re-running with this trace_name."
            )
        # Safe to rename now: this handler is appended, so it runs after Trace's own
        # OnShutdown stopped and flushed the session (see _register_analysis_handler).
        os.rename(raw_trace_dir, ctf_dir)
        # Lazy import: the analyzer chain pulls in bt2, which need not be importable by
        # every launch file that imports add_lttng_trace with tracing disabled.
        from trace_instrumentation.trace_analysis.cli import (  # noqa: PLC0415
            main as run_trace_analysis,
        )

        # Run in-process: OnShutdown blocks until the handler returns, so this is
        # guaranteed to finish, whereas a returned launch entity may never start.
        run_trace_analysis(
            build_analysis_argv(
                ctf_dir,
                node_report_dir,
                topic_chains,
                ignore_nodes,
                only_topic_chains,
            )
        )
        return []

    def _register_analysis_handler(context: LaunchContext):
        """
        Register the post-shutdown analysis so it runs last of all shutdown handlers.

        Args:
            context (LaunchContext): The launch context to register the handler on.
        """
        context.register_event_handler(
            OnShutdown(on_shutdown=_run_analysis), append=True
        )

    analysis_handler = OpaqueFunction(function=_register_analysis_handler)

    return [trace_action, delayed_entities, analysis_handler]
