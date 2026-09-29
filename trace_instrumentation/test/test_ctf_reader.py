# Copyright 2026 Gritt Robotics Inc.

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from trace_instrumentation.trace_analysis.ctf import reader
from trace_instrumentation.trace_analysis.ctf.reader import (
    CTF_INDEX_DIRNAME,
    CTF_METADATA_FILENAME,
    UST_DOMAIN_NAME,
    Event,
    Metadata,
    find_ctf_dir,
    read_trace,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ctf_min"

# Tracepoint and payload names the fixture is asserted against.
EVT_RMW_PUBLISHER_INIT = "ros2:rmw_publisher_init"
FIELD_RMW_PUBLISHER_HANDLE = "rmw_publisher_handle"
FIELD_GID = "gid"
ROS2_EVENT_PREFIX = "ros2:"

# CTF metadata env keys the fixture is asserted against.
ENV_KEY_HOSTNAME = "hostname"
ENV_KEY_TRACER_NAME = "tracer_name"
LTTNG_UST_TRACER_NAME = "lttng-ust"


def test_read_trace_returns_metadata_and_events():
    trace = read_trace(str(FIXTURE))
    assert isinstance(trace.meta, Metadata)
    assert len(trace.events) > 0
    assert all(isinstance(e, Event) for e in trace.events)


def test_read_trace_resolves_the_ctf_dir():
    trace = read_trace(str(FIXTURE))
    assert trace.ctf_dir == find_ctf_dir(str(FIXTURE))


def test_read_trace_result_unpacks_in_field_order():
    meta, ctf_dir, events = read_trace(str(FIXTURE))
    assert isinstance(meta, Metadata)
    assert ctf_dir == find_ctf_dir(str(FIXTURE))
    assert all(isinstance(e, Event) for e in events)


def test_event_timestamps_are_monotonic():
    trace = read_trace(str(FIXTURE))
    ts = [e.ts_ns for e in trace.events]
    assert ts == sorted(ts)


def test_contains_known_ros2_tracepoint():
    trace = read_trace(str(FIXTURE))
    names = {e.name for e in trace.events}
    assert any(name.startswith(ROS2_EVENT_PREFIX) for name in names)


def test_env_populated_from_real_trace():
    trace = read_trace(str(FIXTURE))
    assert trace.meta.env.get(ENV_KEY_HOSTNAME) is not None
    assert trace.meta.env.get(ENV_KEY_TRACER_NAME) == LTTNG_UST_TRACER_NAME


def test_fields_are_coerced_to_native_python_types():
    trace = read_trace(str(FIXTURE))
    publisher_init = next(e for e in trace.events if e.name == EVT_RMW_PUBLISHER_INIT)
    assert type(publisher_init.fields[FIELD_RMW_PUBLISHER_HANDLE]) is int
    assert type(publisher_init.fields[FIELD_GID]) is list
    assert all(type(byte) is int for byte in publisher_init.fields[FIELD_GID])
    assert type(publisher_init.procname) is str


def test_coerce_field_raises_for_unsupported_type():
    with pytest.raises(TypeError, match="Unsupported bt2 field type"):
        reader.coerce_field(object())


def test_event_to_ros2_event_handles_missing_context_and_payload():
    msg = MagicMock()
    msg.default_clock_snapshot.ns_from_origin = 100
    msg.default_clock_snapshot.value = 100
    msg.event.name = "some:event"
    msg.event.common_context_field = None
    msg.event.packet.context_field = None
    msg.event.payload_field = None

    event = reader.event_to_ros2_event(msg)

    assert event.vpid is None
    assert event.vtid is None
    assert event.procname == ""
    assert event.cpu is None
    assert event.fields == {}


def test_find_ctf_dir_returns_trace_dir_when_metadata_is_direct_child(tmp_path):
    (tmp_path / CTF_METADATA_FILENAME).write_bytes(b"")

    assert find_ctf_dir(str(tmp_path)) == str(tmp_path)


def test_find_ctf_dir_prefers_ust_over_kernel_domain(tmp_path):
    kernel_dir = tmp_path / "kernel"
    kernel_dir.mkdir()
    (kernel_dir / CTF_METADATA_FILENAME).write_bytes(b"")
    ust_dir = tmp_path / UST_DOMAIN_NAME / "uid" / "0" / "64-bit"
    ust_dir.mkdir(parents=True)
    (ust_dir / CTF_METADATA_FILENAME).write_bytes(b"")

    assert find_ctf_dir(str(tmp_path)) == str(ust_dir)


def test_find_ctf_dir_is_deterministic_without_a_ust_candidate(tmp_path):
    dir_a = tmp_path / "domain_a"
    dir_a.mkdir()
    (dir_a / CTF_METADATA_FILENAME).write_bytes(b"")
    dir_b = tmp_path / "domain_b"
    dir_b.mkdir()
    (dir_b / CTF_METADATA_FILENAME).write_bytes(b"")

    assert find_ctf_dir(str(tmp_path)) == str(dir_a)


def test_find_ctf_dir_ignores_index_subdirectory(tmp_path):
    index_dir = tmp_path / UST_DOMAIN_NAME / CTF_INDEX_DIRNAME
    index_dir.mkdir(parents=True)
    (index_dir / CTF_METADATA_FILENAME).write_bytes(b"")

    with pytest.raises(FileNotFoundError):
        find_ctf_dir(str(tmp_path))


def test_find_ctf_dir_raises_when_no_metadata_found(tmp_path):
    with pytest.raises(FileNotFoundError, match="No CTF 'metadata' file found"):
        find_ctf_dir(str(tmp_path))
