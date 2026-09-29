# Copyright 2026 Gritt Robotics Inc.

import json
from typing import List

from flow_graph_fixtures import build_analysis_and_flow
from synthetic_events import (
    DEFAULT_PROCNAME,
    callback_end_event,
    callback_start_event,
    node_init_event,
    publish_events,
    publisher_init_event,
    python_zone_event,
)
from trace_instrumentation.trace_analysis.ctf.reader import Event
from trace_instrumentation.trace_analysis.event_names import EVT_RCL_NODE_INIT
from trace_instrumentation.trace_analysis.python_zones import (
    PYTHON_ZONE_PHASE_BEGIN,
)
from trace_instrumentation.trace_analysis.raw_export import (
    ARG_CPU,
    ARG_PROCNAME,
    ARG_TS_NS,
    INSTANT_EVENT_SCOPE_THREAD,
    KEY_ARGS,
    KEY_NAME,
    KEY_PHASE,
    KEY_PID,
    KEY_SCOPE,
    KEY_TID,
    KEY_TIMESTAMP_US,
    KEY_TRACE_EVENTS,
    PHASE_BEGIN,
    PHASE_END,
    PHASE_INSTANT,
    write_events_trace_json,
)

VPID = 1
VTID = 1
NODE_HANDLE = 900
NODE_NAME = "some_node"
CALLBACK = 555

# A second process, so a node can be excluded without touching the first one's events.
OTHER_VPID = 2
OTHER_VTID = 2
OTHER_NODE_HANDLE = 901
OTHER_NODE_NAME = "other_node"

# Both nodes publish, so exclusion is visible as the publishes that survive.
KEPT_PUB_HANDLE = 10
KEPT_RMW_PUB_HANDLE = 110
IGNORED_PUB_HANDLE = 11
IGNORED_RMW_PUB_HANDLE = 111
KEPT_TOPIC = "/kept"
IGNORED_TOPIC = "/ignored"
KEPT_MSG = 400
IGNORED_MSG = 401
KEPT_SOURCE_TS = 8000
IGNORED_SOURCE_TS = 8100

IGNORED_NODE_LABEL = f"/{OTHER_NODE_NAME}"

ZONE_NAME = "_tick"

DUMP_FILENAME = "trace.json"

# A callback bracket 100 ns wide, sitting at the trace's origin.
NODE_INIT_TS_NS = 0
CALLBACK_START_TS_NS = 100
CALLBACK_END_TS_NS = 200
CALLBACK_START_TS_US = 0.0
CALLBACK_END_TS_US = 0.1
ZONE_TS_NS = 300

# Wall-clock-scale timestamps: a float microsecond count this large resolves only to
# ~250 ns, so without rebasing this bracket collapses onto a single `ts`.
EPOCH_TS_NS = 1_756_000_000_000_000_000
EPOCH_CALLBACK_SPAN_NS = 100


def _two_node_publish_scenario() -> List[Event]:
    """
    Two nodes in two processes, each registering a publisher and publishing once.

    Returns:
        List[Event]: The synthetic event sequence.
    """
    return [
        node_init_event(NODE_INIT_TS_NS, VPID, VTID, NODE_HANDLE, NODE_NAME),
        node_init_event(
            NODE_INIT_TS_NS, OTHER_VPID, OTHER_VTID, OTHER_NODE_HANDLE, OTHER_NODE_NAME
        ),
        publisher_init_event(
            NODE_INIT_TS_NS,
            VPID,
            VTID,
            KEPT_PUB_HANDLE,
            NODE_HANDLE,
            KEPT_RMW_PUB_HANDLE,
            KEPT_TOPIC,
        ),
        publisher_init_event(
            NODE_INIT_TS_NS,
            OTHER_VPID,
            OTHER_VTID,
            IGNORED_PUB_HANDLE,
            OTHER_NODE_HANDLE,
            IGNORED_RMW_PUB_HANDLE,
            IGNORED_TOPIC,
        ),
        *publish_events(100, VPID, VTID, KEPT_MSG, KEPT_PUB_HANDLE, KEPT_SOURCE_TS),
        *publish_events(
            200,
            OTHER_VPID,
            OTHER_VTID,
            IGNORED_MSG,
            IGNORED_PUB_HANDLE,
            IGNORED_SOURCE_TS,
        ),
    ]


def _written_events(path: str) -> List[dict]:
    """
    Read back the trace events a dump wrote.

    Args:
        path (str): The dump's path.

    Returns:
        List[dict]: The file's traceEvents array.
    """
    with open(path) as fh:
        return json.load(fh)[KEY_TRACE_EVENTS]


def test_write_events_trace_json_pairs_callback_start_end_as_begin_end(tmp_path):
    events = [
        callback_start_event(CALLBACK_START_TS_NS, VPID, VTID, CALLBACK),
        callback_end_event(CALLBACK_END_TS_NS, VPID, VTID, CALLBACK),
    ]
    analysis, _flow = build_analysis_and_flow(events)
    path = str(tmp_path / DUMP_FILENAME)

    write_events_trace_json(path, events, analysis)

    trace_events = _written_events(path)
    assert [event[KEY_PHASE] for event in trace_events] == [PHASE_BEGIN, PHASE_END]
    assert [event[KEY_TIMESTAMP_US] for event in trace_events] == [
        CALLBACK_START_TS_US,
        CALLBACK_END_TS_US,
    ]
    assert all(
        event[KEY_PID] == VPID and event[KEY_TID] == VTID for event in trace_events
    )


def test_write_events_trace_json_rebases_wall_clock_timestamps(tmp_path):
    events = [
        callback_start_event(EPOCH_TS_NS, VPID, VTID, CALLBACK),
        callback_end_event(EPOCH_TS_NS + EPOCH_CALLBACK_SPAN_NS, VPID, VTID, CALLBACK),
    ]
    analysis, _flow = build_analysis_and_flow(events)
    path = str(tmp_path / DUMP_FILENAME)

    write_events_trace_json(path, events, analysis)

    trace_events = _written_events(path)
    # Un-rebased, both of these round to the same float and the bracket has no width.
    assert [event[KEY_TIMESTAMP_US] for event in trace_events] == [
        CALLBACK_START_TS_US,
        CALLBACK_END_TS_US,
    ]
    assert [event[KEY_ARGS][ARG_TS_NS] for event in trace_events] == [
        EPOCH_TS_NS,
        EPOCH_TS_NS + EPOCH_CALLBACK_SPAN_NS,
    ]


def test_write_events_trace_json_marks_non_callback_events_as_instant(tmp_path):
    events = [node_init_event(NODE_INIT_TS_NS, VPID, VTID, NODE_HANDLE, NODE_NAME)]
    analysis, _flow = build_analysis_and_flow(events)
    path = str(tmp_path / DUMP_FILENAME)

    write_events_trace_json(path, events, analysis)

    trace_events = _written_events(path)
    assert trace_events[0][KEY_PHASE] == PHASE_INSTANT
    assert trace_events[0][KEY_SCOPE] == INSTANT_EVENT_SCOPE_THREAD
    assert trace_events[0][KEY_NAME] == EVT_RCL_NODE_INIT


def test_write_events_trace_json_carries_every_decoded_field_into_args(tmp_path):
    events = [node_init_event(NODE_INIT_TS_NS, VPID, VTID, NODE_HANDLE, NODE_NAME)]
    analysis, _flow = build_analysis_and_flow(events)
    path = str(tmp_path / DUMP_FILENAME)

    write_events_trace_json(path, events, analysis)

    args = _written_events(path)[0][KEY_ARGS]
    for field, value in events[0].fields.items():
        assert args[field] == value
    assert args[ARG_TS_NS] == NODE_INIT_TS_NS
    assert args[ARG_PROCNAME] == DEFAULT_PROCNAME
    assert args[ARG_CPU] == events[0].cpu


def test_an_ignored_node_loses_its_events_and_the_other_node_keeps_its_own(tmp_path):
    events = _two_node_publish_scenario()
    analysis, _flow = build_analysis_and_flow(events)
    path = str(tmp_path / DUMP_FILENAME)

    write_events_trace_json(path, events, analysis, [IGNORED_NODE_LABEL])

    pids = {event[KEY_PID] for event in _written_events(path)}
    assert pids == {VPID}


def test_ignoring_nothing_writes_every_event(tmp_path):
    events = _two_node_publish_scenario()
    analysis, _flow = build_analysis_and_flow(events)
    path = str(tmp_path / DUMP_FILENAME)

    write_events_trace_json(path, events, analysis, [])

    assert len(_written_events(path)) == len(events)


def test_an_unattributable_event_goes_with_its_process_when_no_node_survives(tmp_path):
    # A Python zone carries no handle naming its node, so only its process can place it.
    events = [
        *_two_node_publish_scenario(),
        python_zone_event(
            ZONE_TS_NS, OTHER_VPID, OTHER_VTID, ZONE_NAME, PYTHON_ZONE_PHASE_BEGIN
        ),
    ]
    analysis, _flow = build_analysis_and_flow(events)
    path = str(tmp_path / DUMP_FILENAME)

    write_events_trace_json(path, events, analysis, [IGNORED_NODE_LABEL])

    assert {event[KEY_PID] for event in _written_events(path)} == {VPID}


def test_an_unattributable_event_survives_when_its_process_hosts_a_kept_node(tmp_path):
    # Both nodes share one process here, so excluding one must not take the zone with it.
    events = [
        node_init_event(NODE_INIT_TS_NS, VPID, VTID, NODE_HANDLE, NODE_NAME),
        node_init_event(
            NODE_INIT_TS_NS, VPID, VTID, OTHER_NODE_HANDLE, OTHER_NODE_NAME
        ),
        python_zone_event(ZONE_TS_NS, VPID, VTID, ZONE_NAME, PYTHON_ZONE_PHASE_BEGIN),
    ]
    analysis, _flow = build_analysis_and_flow(events)
    path = str(tmp_path / DUMP_FILENAME)

    write_events_trace_json(path, events, analysis, [IGNORED_NODE_LABEL])

    written = _written_events(path)
    # The excluded node's own node_init goes; the unattributable zone stays.
    assert EVT_RCL_NODE_INIT in {event[KEY_NAME] for event in written}
    assert len(written) == 2
