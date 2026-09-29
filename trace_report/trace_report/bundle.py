# Copyright 2026 Gritt Robotics Inc.

"""
bundle.py
=========

Inlines the static frontend (`frontend/index.html`, `frontend/report.css`,
`frontend/report.js`) and one report's JSON payload into a single self-contained
HTML document.

The frontend is developed as ordinary files against `frontend/sample_report.json`,
with no Python involved. This module is only the packaging step that turns those
files plus real data into one portable artifact; it generates no markup of its own.
"""

import json
from dataclasses import asdict
from importlib.resources import files
from typing import Optional

from trace_instrumentation.trace_analysis.reports.model import Report

from trace_report.compare import Comparison

FRONTEND_DIR = "frontend"
INDEX_ASSET = "index.html"
CSS_ASSET = "report.css"
JS_ASSET = "report.js"
SAMPLE_REPORT_ASSET = "sample_report.json"

# Payload key carrying the B-trace rows in an A/B report. Absent in a single-trace
# report, which is what keeps that payload exactly the serialized `Report`.
PAYLOAD_COMPARE_KEY = "compare"

# The exact source-tree tags bundling replaces; a miss raises rather than silently
# emitting a report that fetches assets it will never find.
CSS_LINK_TAG = '<link rel="stylesheet" href="report.css">'
JS_SCRIPT_TAG = '<script type="module" src="report.js"></script>'
PAYLOAD_EMPTY_TAG = '<script id="trace-report" type="application/json">null</script>'

# `<` is escaped so a topic or node name containing "</script>" cannot close the
# payload tag early. JSON reads < back as "<", so the payload is unchanged.
JSON_UNSAFE_CHAR = "<"
JSON_ESCAPED_CHAR = "\\u003c"


def read_frontend_asset(name: str) -> str:
    """
    Read one file from the packaged `frontend/` directory.

    Args:
        name (str): The asset's file name, e.g. "report.css".

    Returns:
        str: The asset's decoded text.
    """
    return files(__package__).joinpath(FRONTEND_DIR, name).read_text(encoding="utf-8")


def report_payload(report: Report, comparison: Optional[Comparison] = None) -> dict:
    """
    Convert a report model into the plain dict the frontend consumes.

    The frontend contract is `reports.model.Report` itself -- there is no second
    schema to keep in sync. An A/B report adds one `compare` key holding the B-trace
    rows; a single-trace payload omits it entirely and so stays exactly the Report.

    Args:
        report (Report): The report model to serialize.
        comparison (Optional[Comparison]): B's rows aligned to `report`, or None for
            a single-trace report.

    Returns:
        dict: The report as nested plain dicts/lists/scalars, exactly as the frontend
        receives it.
    """
    payload = asdict(report)
    if comparison is not None:
        payload[PAYLOAD_COMPARE_KEY] = asdict(comparison)
    # asdict() preserves Chain.topic_sequence's tuples, which JSON has no type for;
    # normalizing here keeps this the payload the frontend actually sees.
    return json.loads(json.dumps(payload))


def encode_payload(payload: dict) -> str:
    """
    Serialize a payload for embedding in an HTML `<script>` element.

    Args:
        payload (dict): The report payload, e.g. from report_payload().

    Returns:
        str: JSON text safe to place inside a `<script>` element.
    """
    return json.dumps(payload).replace(JSON_UNSAFE_CHAR, JSON_ESCAPED_CHAR)


def _replace_once(document: str, tag: str, replacement: str) -> str:
    """
    Substitute one required tag in the index document.

    Args:
        document (str): The document to substitute into.
        tag (str): The exact tag text to replace.
        replacement (str): The text to put in its place.

    Returns:
        str: The document with `tag` replaced.

    Raises:
        ValueError: If `tag` does not appear in `document`.
    """
    if tag not in document:
        raise ValueError(f"{INDEX_ASSET} is missing the expected tag: {tag}")
    return document.replace(tag, replacement, 1)


def render_report(report: Report, comparison: Optional[Comparison] = None) -> str:
    """
    Assemble the full HTML document for one report.

    Args:
        report (Report): The report to render.
        comparison (Optional[Comparison]): B's rows aligned to `report`, rendered
            alongside A's numbers. None renders the ordinary single-trace report.

    Returns:
        str: The complete `<!doctype html>...</html>` document, self-contained except
        the topology diagram, which loads Mermaid from a CDN and needs network access
        to render.
    """
    document = read_frontend_asset(INDEX_ASSET)
    document = _replace_once(
        document, CSS_LINK_TAG, f"<style>{read_frontend_asset(CSS_ASSET)}</style>"
    )
    document = _replace_once(
        document,
        JS_SCRIPT_TAG,
        f'<script type="module">{read_frontend_asset(JS_ASSET)}</script>',
    )
    payload = encode_payload(report_payload(report, comparison))
    return _replace_once(
        document,
        PAYLOAD_EMPTY_TAG,
        f'<script id="trace-report" type="application/json">{payload}</script>',
    )
