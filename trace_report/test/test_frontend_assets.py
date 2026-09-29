# Copyright 2026 Gritt Robotics Inc.

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Tuple

from trace_instrumentation.trace_analysis.graph import CHAIN_TERMINATOR_PUB
from trace_instrumentation.trace_analysis.reports.model import build_report
from trace_report.bundle import (
    CSS_ASSET,
    CSS_LINK_TAG,
    INDEX_ASSET,
    JS_ASSET,
    JS_SCRIPT_TAG,
    PAYLOAD_EMPTY_TAG,
    SAMPLE_REPORT_ASSET,
    read_frontend_asset,
    report_payload,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_trace.json"
# The title baked into the committed sample payload; kept in sync by the test below.
SAMPLE_REPORT_TITLE = "Sample trace"
ELEMENT_LOOKUP_PATTERN = re.compile(r"""getElementById\(["'](?P<id>[^"']+)["']\)""")
ELEMENT_ID_PATTERN = re.compile(r"""\bid=["'](?P<id>[^"']+)["']""")
JS_CHAIN_TERMINATOR_PUB_PATTERN = re.compile(
    r"""const CHAIN_TERMINATOR_PUB = ["'](?P<terminator>[^"']+)["']"""
)
# The body of a CSS rule, given its selector.
CSS_RULE_TEMPLATE = r"{selector}\s*\{{(?P<body>[^}}]*)\}}"
STICKY_SECOND_ROW_SELECTOR = r"table\.sticky-header thead tr:nth-child\(2\) th"
STICKY_HEADER_CELL_SELECTOR = r"table\.sticky-header thead th"
METRIC_CELLS_CALL = "metricCells("
# The metrics whose flagged shift has no better direction, so it stays the neutral
# tint; report.js passes POLARITY_NEUTRAL for exactly these.
NEUTRAL_METRIC_FIELDS = (".interarrival", ".inter_callback_gap")
NEUTRAL_POLARITY_CONSTANT = "POLARITY_NEUTRAL"
# A <colgroup>'s widths are percentages of the table, so they have to add up.
TOTAL_COLUMN_WIDTH_PCT = 100.0
COLUMN_WIDTH_PATTERN = re.compile(r"width:\s*(?P<pct>[0-9.]+)%")


class _TableColumnParser(HTMLParser):
    """Collects, for each `<table>` carrying a `<colgroup>`, that group's declared
    widths and the number of columns the table's first header row spans."""

    def __init__(self) -> None:
        super().__init__()
        # One (widths, header column count) pair per table with a <colgroup>.
        self.tables: List[Tuple[List[float], int]] = []
        self._widths: List[float] = []
        self._header_columns = 0
        self._in_first_header_row = False
        self._seen_header_row = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str]]) -> None:
        attributes = dict(attrs)
        if tag == "table":
            self._widths = []
            self._header_columns = 0
            self._seen_header_row = False
        elif tag == "col":
            match = COLUMN_WIDTH_PATTERN.search(attributes.get("style", ""))
            if match is not None:
                self._widths.append(float(match.group("pct")))
        elif tag == "tr" and not self._seen_header_row:
            self._in_first_header_row = True
        elif tag == "th" and self._in_first_header_row:
            self._header_columns += int(attributes.get("colspan", 1))

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr" and self._in_first_header_row:
            self._in_first_header_row = False
            self._seen_header_row = True
        elif tag == "table" and len(self._widths) > 0:
            self.tables.append((self._widths, self._header_columns))


def _js_string_constant(name: str) -> str:
    """Read a string constant's value out of report.js, which the tests cannot import."""
    pattern = re.compile(rf"""const {name} = ["'](?P<value>[^"']+)["']""")
    match = pattern.search(read_frontend_asset(JS_ASSET))
    assert match is not None, f"report.js declares no {name}"
    return match.group("value")


def _css_rule_body(selector: str) -> str:
    """Read the declarations of the report.css rule with `selector` (a regex)."""
    match = re.search(
        CSS_RULE_TEMPLATE.format(selector=selector), read_frontend_asset(CSS_ASSET)
    )
    assert match is not None, f"report.css has no rule for {selector}"
    return match.group("body")


def _metric_cells_calls() -> List[str]:
    """Read the argument text of every metricCells() call in report.js, skipping the
    function's own definition."""
    js = read_frontend_asset(JS_ASSET)
    calls = []
    index = js.find(METRIC_CELLS_CALL)
    while index != -1:
        cursor = index + len(METRIC_CELLS_CALL)
        depth = 1
        while depth > 0:
            if js[cursor] == "(":
                depth += 1
            elif js[cursor] == ")":
                depth -= 1
            cursor += 1
        if not js[:index].rstrip().endswith("function"):
            calls.append(js[index + len(METRIC_CELLS_CALL) : cursor - 1])
        index = js.find(METRIC_CELLS_CALL, cursor)
    return calls


def _column_tables() -> List[Tuple[List[float], int]]:
    parser = _TableColumnParser()
    parser.feed(read_frontend_asset(INDEX_ASSET))
    return parser.tables


def test_index_declares_every_tag_the_bundler_replaces():
    index = read_frontend_asset(INDEX_ASSET)
    for tag in (CSS_LINK_TAG, JS_SCRIPT_TAG, PAYLOAD_EMPTY_TAG):
        assert index.count(tag) == 1


def test_index_is_a_full_document():
    index = read_frontend_asset(INDEX_ASSET)
    assert index.startswith("<!doctype html>")
    assert index.rstrip().endswith("</html>")


def test_report_js_keeps_the_standalone_sample_fallback():
    assert SAMPLE_REPORT_ASSET in read_frontend_asset(JS_ASSET)


def test_report_js_chain_terminator_matches_the_analyzer_constant():
    declared = JS_CHAIN_TERMINATOR_PUB_PATTERN.search(read_frontend_asset(JS_ASSET))
    assert declared is not None
    assert declared.group("terminator") == CHAIN_TERMINATOR_PUB


def test_every_element_report_js_looks_up_exists_in_index():
    declared = set(ELEMENT_ID_PATTERN.findall(read_frontend_asset(INDEX_ASSET)))
    looked_up = set(ELEMENT_LOOKUP_PATTERN.findall(read_frontend_asset(JS_ASSET)))
    # Guards against the pattern silently matching nothing and passing vacuously.
    assert len(looked_up) > 0
    assert looked_up.issubset(declared)


def test_sample_report_matches_the_current_report_model():
    with open(FIXTURE_PATH) as fh:
        expected = report_payload(
            build_report(json.load(fh), title=SAMPLE_REPORT_TITLE)
        )
    assert json.loads(read_frontend_asset(SAMPLE_REPORT_ASSET)) == expected


def test_every_colgroup_declares_one_width_per_header_column():
    tables = _column_tables()
    assert len(tables) > 0
    for widths, header_columns in tables:
        assert len(widths) == header_columns


def test_every_colgroup_width_sums_to_the_full_table():
    for widths, _ in _column_tables():
        assert sum(widths) == TOTAL_COLUMN_WIDTH_PCT


def test_css_styles_the_compared_trace_value():
    css = read_frontend_asset(CSS_ASSET)
    assert ".compare-b" in css
    assert ".comparing .table-scroll" in css


def test_css_defines_dark_and_light_theme_tokens():
    css = read_frontend_asset(CSS_ASSET)
    assert "prefers-color-scheme: dark" in css
    assert '[data-theme="dark"]' in css
    assert '[data-theme="light"]' in css


def test_css_defines_severity_tokens_distinct_from_accent():
    css = read_frontend_asset(CSS_ASSET)
    for token in ("--accent", "--good", "--warn", "--critical"):
        assert token in css


def test_css_defines_table_scroll_wrapper():
    css = read_frontend_asset(CSS_ASSET)
    assert ".table-scroll" in css
    assert "overflow-x" in css


def test_css_defines_sticky_table_header_rules():
    css = read_frontend_asset(CSS_ASSET)
    assert "table.sticky-header thead tr:first-child th" in css
    assert "table.sticky-header thead tr:nth-child(2) th" in css
    assert "position: sticky" in css
    assert "top: 0" in css


def test_second_sticky_header_row_is_offset_by_the_measured_first_row():
    body = _css_rule_body(STICKY_SECOND_ROW_SELECTOR)
    css_var = _js_string_constant("STICKY_HEADER_ROW1_HEIGHT_CSS_VAR")
    assert f"var({css_var}" in body


def test_sticky_header_cells_carry_their_own_bottom_border():
    assert "box-shadow" in _css_rule_body(STICKY_HEADER_CELL_SELECTOR)


def test_chart_names_the_quantity_on_both_axes():
    assert "messages" in _js_string_constant("Y_AXIS_TITLE")
    assert "s)" in _js_string_constant("X_AXIS_TITLE")


def test_css_styles_the_chart_axis_titles():
    axis_title_class = _js_string_constant("CSS_CLASS_AXIS_TITLE")
    assert f".{axis_title_class}" in read_frontend_asset(CSS_ASSET)


def test_css_tints_a_flagged_shift_by_direction():
    css = read_frontend_asset(CSS_ASSET)
    for constant in ("CSS_CLASS_IMPROVEMENT", "CSS_CLASS_REGRESSION"):
        css_class = _js_string_constant(constant)
        assert f".compare-b.significant.{css_class}" in css


def test_only_the_cadence_metrics_render_a_directionless_shift():
    calls = _metric_cells_calls()
    assert len(calls) > 0
    for call in calls:
        is_cadence = any(field in call for field in NEUTRAL_METRIC_FIELDS)
        assert (NEUTRAL_POLARITY_CONSTANT in call) == is_cadence, call
    neutral_calls = [c for c in calls if NEUTRAL_POLARITY_CONSTANT in c]
    assert len(neutral_calls) == len(NEUTRAL_METRIC_FIELDS)


def test_css_respects_reduced_motion():
    assert "prefers-reduced-motion" in read_frontend_asset(CSS_ASSET)


def test_css_defines_header_tooltip_hover_rule():
    css = read_frontend_asset(CSS_ASSET)
    assert "th[data-tooltip]" in css
    assert "::after" in css


def test_css_no_longer_styles_a_footnotes_block():
    assert ".footnotes" not in read_frontend_asset(CSS_ASSET)
