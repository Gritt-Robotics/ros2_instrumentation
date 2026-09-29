# Copyright 2026 Gritt Robotics Inc.

import json
from pathlib import Path

import pytest
from trace_instrumentation.trace_analysis.reports.model import build_report
from trace_report import bundle
from trace_report.bundle import (
    CSS_ASSET,
    CSS_LINK_TAG,
    INDEX_ASSET,
    JS_ASSET,
    JS_SCRIPT_TAG,
    PAYLOAD_EMPTY_TAG,
    encode_payload,
    read_frontend_asset,
    render_report,
    report_payload,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_trace.json"
PAYLOAD_TAG_PREFIX = '<script id="trace-report" type="application/json">'


def _report():
    with open(FIXTURE_PATH) as fh:
        return build_report(json.load(fh), title="Bundle test")


def _embedded_payload(html: str) -> dict:
    start = html.index(PAYLOAD_TAG_PREFIX) + len(PAYLOAD_TAG_PREFIX)
    return json.loads(html[start : html.index("</script>", start)])


def test_report_payload_mirrors_the_report_model():
    payload = report_payload(_report())
    assert payload["title"] == "Bundle test"
    assert payload["duration_s"] == 10.0
    assert payload["node_names"] == ["/producer", "/subscriber"]
    assert len(payload["edges"]) == 1
    edge = payload["edges"][0]
    assert (edge["from_node"], edge["to_node"], edge["topic"]) == (
        "/producer",
        "/subscriber",
        "/step0",
    )
    assert edge["publisher"]["topic"] == "/step0"
    assert edge["callback"]["callback_type"] == "subscription"


def test_report_payload_keeps_metrics_as_nested_dicts():
    payload = report_payload(_report())
    duration = payload["callbacks"][0]["duration"]
    assert duration["p50"] == 9.0
    assert duration["p90"] == 12.0


def test_encode_payload_escapes_script_terminator():
    encoded = encode_payload({"topic": "/a</script><script>alert(1)</script>"})
    assert "</script>" not in encoded
    assert "<" not in encoded


def test_encode_payload_round_trips():
    payload = report_payload(_report())
    assert json.loads(encode_payload(payload)) == payload


def test_render_report_is_a_full_document():
    html = render_report(_report())
    assert html.startswith("<!doctype html>")
    assert html.rstrip().endswith("</html>")


def test_render_report_inlines_every_asset():
    html = render_report(_report())
    assert CSS_LINK_TAG not in html
    assert JS_SCRIPT_TAG not in html
    assert PAYLOAD_EMPTY_TAG not in html
    assert "<style>" in html
    assert '<script type="module">' in html


def test_render_report_embeds_the_parseable_payload():
    payload = _embedded_payload(render_report(_report()))
    assert payload == report_payload(_report())


def test_render_report_raises_when_a_tag_is_missing(monkeypatch):
    monkeypatch.setattr(
        bundle,
        "read_frontend_asset",
        lambda name: "" if name == INDEX_ASSET else read_frontend_asset(name),
    )
    with pytest.raises(ValueError, match=CSS_ASSET):
        render_report(_report())


def test_inlined_assets_cannot_close_their_host_element():
    assert "</style>" not in read_frontend_asset(CSS_ASSET)
    assert "</script>" not in read_frontend_asset(JS_ASSET)
