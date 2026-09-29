# Copyright 2026 Gritt Robotics Inc.

import json
from pathlib import Path

import pytest
from trace_instrumentation.trace_analysis.reports.schema import SchemaError
from trace_report.compare import TEST_T
from trace_report.generate_report import (
    AB_REPORT_TITLE_PREFIX,
    build_report_html,
    main,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_trace.json"


def _load_fixture() -> dict:
    with open(FIXTURE_PATH) as fh:
        return json.load(fh)


def test_build_report_html_validates_and_renders():
    html = build_report_html(_load_fixture(), title="From dict")
    assert "From dict" in html
    assert html.startswith("<!doctype html>")


def test_build_report_html_raises_on_invalid_trace():
    trace = _load_fixture()
    del trace["duration_s"]
    with pytest.raises(SchemaError):
        build_report_html(trace)


def test_main_writes_html_file(tmp_path):
    out_path = tmp_path / "report.html"
    exit_code = main([str(FIXTURE_PATH), "--out", str(out_path)])
    assert exit_code == 0
    assert out_path.exists()
    assert "<!doctype html>" in out_path.read_text(encoding="utf-8")


def test_main_passes_through_title(tmp_path):
    out_path = tmp_path / "report.html"
    main([str(FIXTURE_PATH), "--out", str(out_path), "--title", "CLI Title"])
    assert "CLI Title" in out_path.read_text(encoding="utf-8")


def test_main_prefixes_ab_title(tmp_path):
    out_path = tmp_path / "report.html"
    main([
        str(FIXTURE_PATH),
        "--out",
        str(out_path),
        "--compare",
        str(FIXTURE_PATH),
        "--title",
        "candidate-b",
    ])
    assert f"{AB_REPORT_TITLE_PREFIX}: candidate-b" in out_path.read_text(
        encoding="utf-8"
    )


def test_build_report_html_prefixes_ab_title():
    html = build_report_html(
        _load_fixture(), compare_report=_load_fixture(), title="candidate-b"
    )
    assert f"{AB_REPORT_TITLE_PREFIX}: candidate-b" in html


def test_build_report_html_ab_title_falls_back_without_override():
    html = build_report_html(_load_fixture(), compare_report=_load_fixture())
    assert AB_REPORT_TITLE_PREFIX in html
    assert f"{AB_REPORT_TITLE_PREFIX}:" not in html


def test_build_report_html_single_trace_title_is_not_prefixed():
    html = build_report_html(_load_fixture(), title="From dict")
    assert AB_REPORT_TITLE_PREFIX not in html


def test_build_report_html_accepts_significance_test():
    html = build_report_html(
        _load_fixture(), compare_report=_load_fixture(), significance_test=TEST_T
    )
    assert "<!doctype html>" in html


def test_main_accepts_significance_test_flag(tmp_path):
    out_path = tmp_path / "report.html"
    exit_code = main([
        str(FIXTURE_PATH),
        "--out",
        str(out_path),
        "--compare",
        str(FIXTURE_PATH),
        "--significance-test",
        "ttest",
    ])
    assert exit_code == 0
    assert out_path.exists()


def test_main_rejects_unknown_significance_test(tmp_path):
    out_path = tmp_path / "report.html"
    with pytest.raises(SystemExit):
        main([
            str(FIXTURE_PATH),
            "--out",
            str(out_path),
            "--significance-test",
            "bogus",
        ])
