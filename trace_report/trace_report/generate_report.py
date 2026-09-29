# Copyright 2026 Gritt Robotics Inc.

"""
generate_report.py
===================

CLI entry point: load JSON -> validate against schema -> build model -> render HTML
-> write file. No domain logic lives here -- arg parsing, orchestration, and file
IO only.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from trace_instrumentation.trace_analysis.reports.model import build_report
from trace_instrumentation.trace_analysis.reports.schema import validate_trace

from trace_report.bundle import render_report
from trace_report.compare import TEST_MANN_WHITNEY, TEST_T, build_comparison
from trace_report.markdown_report import render_markdown_report

# A test's CLI spelling is compare.py's selector value verbatim, so there is no map.
SIGNIFICANCE_TEST_CHOICES = (TEST_MANN_WHITNEY, TEST_T)

# Prefixed onto an A/B report's title so it's never mistaken for a single-trace one.
AB_REPORT_TITLE_PREFIX = "A/B Pipeline Trace Report"


def _ab_report_title(title: Optional[str]) -> str:
    """
    Build an A/B report's title, prefixing the developer-supplied title with
    AB_REPORT_TITLE_PREFIX so it's never mistaken for a single-trace report.

    Args:
        title (Optional[str]): The developer-supplied title suffix (e.g. a PR number
            or commit hash), or None to use the prefix alone.

    Returns:
        str: The full A/B report title.
    """
    if title is None:
        return AB_REPORT_TITLE_PREFIX
    return f"{AB_REPORT_TITLE_PREFIX}: {title}"


def build_report_html(
    node_report: dict,
    *,
    compare_report: Optional[dict] = None,
    title: Optional[str] = None,
    significance_test: str = TEST_T,
) -> str:
    """
    Validate a trace-analysis JSON dict, build its report model, and render the
    complete HTML document -- no file IO. The entry point for rendering in-process,
    without a JSON round-trip through disk.

    Args:
        node_report (dict): The parsed trace-analysis JSON (e.g.
            `build_node_report()`'s output). In an A/B report this is A, the control,
            and it alone decides the report's rows, order and labels.
        compare_report (Optional[dict]): A second parsed trace-analysis JSON, B, whose
            numbers are printed alongside A's wherever a row matches. None renders the
            ordinary single-trace report.
        title (Optional[str]): Report title override. In an A/B report, this is
            prefixed with AB_REPORT_TITLE_PREFIX rather than used verbatim -- see
            _ab_report_title().
        significance_test (str): Which test `build_comparison()` uses to flag a
            matched metric as significant -- TEST_T (default) or TEST_MANN_WHITNEY.
            Unused outside an A/B report.

    Returns:
        str: The complete `<!doctype html>...</html>` document.
    """
    validate_trace(node_report)
    if compare_report is not None:
        title = _ab_report_title(title)
    report = build_report(node_report, title=title)
    if compare_report is None:
        return render_report(report)
    validate_trace(compare_report)
    comparison = build_comparison(
        report, build_report(compare_report), test=significance_test
    )
    return render_report(report, comparison)


def build_report_markdown(
    node_report: dict,
    *,
    compare_report: Optional[dict] = None,
    title: Optional[str] = None,
    significance_test: str = TEST_T,
) -> str:
    """
    Validate a trace-analysis JSON dict, build its report model, and render sections
    01-03 as a Markdown document -- no file IO, no topology diagram, no throughput
    chart. See `markdown_report.render_markdown_report()`.

    Args:
        node_report (dict): The parsed trace-analysis JSON. In an A/B report this is
            A, the control, and it alone decides the report's rows, order and labels.
        compare_report (Optional[dict]): A second parsed trace-analysis JSON, B, whose
            numbers are printed alongside A's wherever a row matches. None renders the
            ordinary single-trace report.
        title (Optional[str]): Report title override. In an A/B report, this is
            prefixed with AB_REPORT_TITLE_PREFIX rather than used verbatim -- see
            _ab_report_title().
        significance_test (str): Which test `build_comparison()` uses to flag a
            matched metric as significant -- TEST_T (default) or TEST_MANN_WHITNEY.
            Unused outside an A/B report.

    Returns:
        str: The rendered Markdown document.
    """
    validate_trace(node_report)
    if compare_report is not None:
        title = _ab_report_title(title)
    report = build_report(node_report, title=title)
    if compare_report is None:
        return render_markdown_report(report)
    validate_trace(compare_report)
    comparison = build_comparison(
        report, build_report(compare_report), test=significance_test
    )
    return render_markdown_report(report, comparison)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Turn one trace's analysis JSON into a self-contained HTML report."
        )
    )
    parser.add_argument("input", help="Path to the trace-analysis JSON file")
    parser.add_argument("--out", help="Output HTML file path")
    parser.add_argument(
        "--md-out",
        help=(
            "Output Markdown file path: sections 01-03 only (topic stats, node "
            "analysis, chain latency) -- no topology diagram, no throughput chart."
        ),
    )
    parser.add_argument(
        "--compare",
        help=(
            "Path to a second trace-analysis JSON to overlay on this one. The first "
            "input is the control and fixes every row and label; only the second's "
            "numbers are added, and only where a row matches."
        ),
    )
    parser.add_argument(
        "--title",
        help=(
            "Report title override. With --compare, this names the comparison (a "
            f'PR number, commit hash, etc.) and is prefixed with "'
            f'{AB_REPORT_TITLE_PREFIX}: ".'
        ),
    )
    parser.add_argument(
        "--significance-test",
        choices=sorted(SIGNIFICANCE_TEST_CHOICES),
        default=TEST_T,
        help=(
            "Which test flags a matched --compare metric as significant. "
            "Ignored without --compare. Defaults to ttest."
        ),
    )
    args = parser.parse_args(argv)
    if args.out is None and args.md_out is None:
        parser.error("at least one of --out or --md-out is required")

    with open(args.input) as fh:
        node_report = json.load(fh)

    compare_report = None
    if args.compare is not None:
        with open(args.compare) as fh:
            compare_report = json.load(fh)

    if args.out is not None:
        html = build_report_html(
            node_report,
            compare_report=compare_report,
            title=args.title,
            significance_test=args.significance_test,
        )
        Path(args.out).write_text(html, encoding="utf-8")
    if args.md_out is not None:
        markdown = build_report_markdown(
            node_report,
            compare_report=compare_report,
            title=args.title,
            significance_test=args.significance_test,
        )
        Path(args.md_out).write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
