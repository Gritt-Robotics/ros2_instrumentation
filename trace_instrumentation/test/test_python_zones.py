# Copyright 2026 Gritt Robotics Inc.

import pytest
from trace_instrumentation.trace_analysis.graph import (
    CB_KIND_SERVICE,
    CB_KIND_SUBSCRIPTION,
    CB_KIND_TIMER,
)
from trace_instrumentation.trace_analysis.python_zones import (
    LINK_KIND_PUB,
    LINK_KIND_TAKE,
    PythonZone,
    build_link_publish_msg,
    build_link_take_msg,
    parse_link_declaration_msg,
    parse_python_zone_msg,
    parse_zone_declaration_msg,
)

SUB_ZONE = "_on_image"
TIMER_ZONE = "_tick"
SERVICE_ZONE = "_on_request"
DECL_TOPIC = "/image"
DECL_SERVICE_NAME = "/reset"
DECL_PERIOD_NS = 100_000_000


def _make_zone(ts_begin_ns: int, ts_end_ns: int | None = None) -> PythonZone:
    return PythonZone(
        id=1,
        logger_name="test_logger",
        zone_name="_tick",
        vpid=100,
        vtid=200,
        ts_begin_ns=ts_begin_ns,
        ts_end_ns=ts_end_ns,
    )


def test_finalize_computes_duration_when_end_is_set():
    zone = _make_zone(ts_begin_ns=1_000, ts_end_ns=1_500)
    zone.finalize()
    assert zone.duration_ns == 500


def test_finalize_leaves_duration_none_when_end_is_unset():
    zone = _make_zone(ts_begin_ns=1_000)
    zone.finalize()
    assert zone.duration_ns is None


def test_parse_python_zone_msg_extracts_begin_phase():
    assert parse_python_zone_msg("_tick:begin") == ("_tick", "begin")


def test_parse_python_zone_msg_extracts_end_phase():
    assert parse_python_zone_msg("_on_message:end") == ("_on_message", "end")


def test_parse_python_zone_msg_returns_none_without_separator():
    assert parse_python_zone_msg("_tick") is None


def test_parse_python_zone_msg_returns_none_for_unknown_phase():
    assert parse_python_zone_msg("_tick:unknown") is None


def test_parse_python_zone_msg_splits_on_last_separator():
    assert parse_python_zone_msg("ns:zone:begin") == ("ns:zone", "begin")


def test_parses_subscription_declaration():
    declaration = parse_zone_declaration_msg(
        f"trace_decl sub zone={SUB_ZONE} topic={DECL_TOPIC}"
    )
    assert declaration is not None
    assert declaration.zone_name == SUB_ZONE
    assert declaration.kind == CB_KIND_SUBSCRIPTION
    assert declaration.target == DECL_TOPIC
    assert declaration.period_ns is None


def test_parses_timer_declaration_with_period():
    declaration = parse_zone_declaration_msg(
        f"trace_decl timer zone={TIMER_ZONE} period_ns={DECL_PERIOD_NS}"
    )
    assert declaration is not None
    assert declaration.kind == CB_KIND_TIMER
    assert declaration.period_ns == DECL_PERIOD_NS
    assert declaration.target == ""


def test_parses_service_declaration():
    declaration = parse_zone_declaration_msg(
        f"trace_decl service zone={SERVICE_ZONE} name={DECL_SERVICE_NAME}"
    )
    assert declaration is not None
    assert declaration.kind == CB_KIND_SERVICE
    assert declaration.target == DECL_SERVICE_NAME


def test_returns_none_for_an_ordinary_zone_payload():
    assert parse_zone_declaration_msg(f"{SUB_ZONE}:begin") is None
    assert parse_zone_declaration_msg(f"{SUB_ZONE}:end") is None


def test_returns_none_for_malformed_declarations_without_raising():
    # never raise on trace content: a corrupt or skewed payload is ignored, not fatal
    assert parse_zone_declaration_msg("") is None
    assert parse_zone_declaration_msg("trace_decl") is None
    assert parse_zone_declaration_msg("trace_decl sub") is None
    assert parse_zone_declaration_msg("trace_decl bogus zone=_x topic=/y") is None
    assert parse_zone_declaration_msg(f"trace_decl sub topic={DECL_TOPIC}") is None
    assert parse_zone_declaration_msg("trace_decl sub zone= topic=/y") is None
    assert (
        parse_zone_declaration_msg(f"trace_decl timer zone={TIMER_ZONE} period_ns=abc")
        is None
    )


def test_an_ordinary_zone_payload_still_parses_as_a_zone():
    # the two grammars must stay mutually exclusive in both directions
    assert parse_python_zone_msg(f"{SUB_ZONE}:begin") == (SUB_ZONE, "begin")
    assert parse_python_zone_msg(f"trace_decl sub zone={SUB_ZONE} topic=/image") is None


def test_parses_a_pub_link_declaration():
    declaration = parse_link_declaration_msg("trace_link pub uid=7")
    assert declaration is not None
    assert declaration.kind == LINK_KIND_PUB
    assert declaration.uids == [7]
    assert declaration.truncated is False


def test_parses_a_take_declaration_with_several_uids_in_order():
    declaration = parse_link_declaration_msg("trace_link take uids=3,4")
    assert declaration is not None
    assert declaration.kind == LINK_KIND_TAKE
    assert declaration.uids == [3, 4]


def test_parses_a_truncated_take_declaration():
    declaration = parse_link_declaration_msg("trace_link take uids=3,4 truncated=1")
    assert declaration is not None
    assert declaration.truncated is True


def test_returns_none_for_malformed_link_declarations():
    assert parse_link_declaration_msg("trace_link") is None
    assert parse_link_declaration_msg("trace_link pub") is None
    assert parse_link_declaration_msg("trace_link bogus uid=7") is None
    assert parse_link_declaration_msg("trace_link pub uid=abc") is None
    # 0 is never published, so it identifies a corrupt payload
    assert parse_link_declaration_msg("trace_link pub uid=0") is None
    assert parse_link_declaration_msg("trace_link take uids=") is None
    assert parse_link_declaration_msg("_on_image:begin") is None


def test_the_link_and_zone_grammars_stay_mutually_exclusive():
    assert parse_zone_declaration_msg("trace_link pub uid=7") is None
    assert (
        parse_link_declaration_msg(f"trace_decl sub zone={SUB_ZONE} topic={DECL_TOPIC}")
        is None
    )


def test_build_link_publish_msg_round_trips_through_the_parser():
    declaration = parse_link_declaration_msg(build_link_publish_msg(7))
    assert declaration is not None
    assert declaration.kind == LINK_KIND_PUB
    assert declaration.uids == [7]


def test_build_link_take_msg_round_trips_through_the_parser():
    declaration = parse_link_declaration_msg(build_link_take_msg([3, 4]))
    assert declaration is not None
    assert declaration.kind == LINK_KIND_TAKE
    assert declaration.uids == [3, 4]
    assert declaration.truncated is False


def test_build_link_publish_msg_rejects_a_uid_the_parser_would_reject():
    with pytest.raises(ValueError):
        build_link_publish_msg(0)


def test_build_link_take_msg_rejects_an_empty_uids_list():
    with pytest.raises(ValueError):
        build_link_take_msg([])


def test_build_link_take_msg_rejects_a_uid_the_parser_would_reject():
    with pytest.raises(ValueError):
        build_link_take_msg([3, 0])
