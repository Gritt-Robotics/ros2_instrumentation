# Copyright 2026 Gritt Robotics Inc.

from trace_instrumentation.trace_analysis.ctf.reader import Metadata


def test_empty_construction_has_default_state():
    meta = Metadata()
    assert meta.env == {}
    assert meta.clock_offset == 0
    assert meta.clock_freq == 1_000_000_000


def test_empty_construction_cycles_to_ns_is_identity():
    meta = Metadata()
    assert meta.cycles_to_ns(0) == 0
    assert meta.cycles_to_ns(123456789) == 123456789
