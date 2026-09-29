// Copyright 2026 Gritt Robotics Inc.

/*
 * Renders the trace report from the payload embedded in index.html's
 * `#trace-report` script tag (dataclasses.asdict() of reports.model.Report).
 *
 * An A/B report adds one `compare` key holding a second trace's rows, aligned by
 * index to the report's own by compare.py. B contributes numbers only: every row,
 * label and ordering is A's, and each cell prints B's value beside or below A's.
 *
 * Table cells are built as DOM nodes and filled via textContent, so user-facing
 * strings are never concatenated into markup. The throughput chart is assembled as
 * an SVG string because only numbers are interpolated into it.
 *
 * The topology diagram loads Mermaid from a CDN and needs network access to render.
 */

const SAMPLE_REPORT_URL = "sample_report.json";
const DEFAULT_TITLE = "Pipeline Trace Schedule";
const EM_DASH = "—";
const ARROW = "→";
// Text rendered for a grouping column whose value repeats the row above it.
const REPEATED_CELL_TEXT = "";

// Mirrors trace_instrumentation's CHAIN_TERMINATOR_*, pinned by
// test_frontend_assets since JS cannot import it.
const CHAIN_TERMINATOR_PUB = "pub";
const PUBLISH_TERMINUS_LABEL = " publish";

// Must match report.css's ".endpoint-node" rule.
const CSS_CLASS_ENDPOINT_NODE = "endpoint-node";
// A chain's topic path, each topic over the nodes it travels between; shared by the
// summary row and the steps table's heading. Must match report.css's same-name rules.
const CSS_CLASS_CHAIN_PATH = "chain-path";
const CSS_CLASS_CHAIN_TOPIC = "chain-topic";
const CSS_CLASS_CHAIN_TOPIC_NODES = "chain-topic-nodes";
// Wraps one chain's heading and steps table, so report.css can rule off each block
// from the one before it.
const CSS_CLASS_CHAIN_BLOCK = "chain-block";

// Two-row sticky table headers. The class is index.html's; the custom property is
// read by report.css's "table.sticky-header thead tr:nth-child(2) th" rule.
const CSS_CLASS_STICKY_HEADER = "sticky-header";
const STICKY_HEADER_ROW1_HEIGHT_CSS_VAR = "--sticky-header-row1-h";

// Node-analysis "Function call" column: a fixed category per row type, not the
// handler name.
const CALLBACK_TYPE_TIMER = "timer";
const CALLBACK_TYPE_SUBSCRIPTION = "subscription";
const TIMER_FUNCTION_CALL_LABEL = "Timer callback";
const SUBSCRIBER_FUNCTION_CALL_LABEL = "Subscriber callback";
const PUBLISH_FUNCTION_CALL_LABEL = "Publish";

// Mermaid diagram constants.
const MERMAID_FLOWCHART_DIR = "LR";
const NODE_ID_PREFIX = "n_";
const EMPTY_DIAGRAM_NODE_ID = "empty";
const MERMAID_EMPTY_DIAGRAM_MESSAGE = "(no topology edges)";
// Pinned to an exact release (not a floating major version) so the report always
// renders against the same, verified build of Mermaid.
const MERMAID_CDN_URL =
  "https://cdn.jsdelivr.net/npm/mermaid@11.16.1/dist/mermaid.esm.min.mjs";

// Edge/arrowhead styling for the rendered Mermaid SVG: a gradient from each edge's
// base (source node) to its tip (target node, matching the arrowhead color).
const TOPOLOGY_EDGE_BASE_COLOR = "#E39A2A"; // orange-gold
const TOPOLOGY_EDGE_TIP_COLOR = "#FFD93D"; // yellow
// CSS var name report.css's ".topology svg marker path" rule reads via var(...);
// report.css's copy must match this name.
const TOPOLOGY_EDGE_TIP_CSS_VAR = "--topology-edge-tip";
// Mermaid's generated edge-path class names; also referenced by report.css's
// ".topology svg .edgePath .path, .topology svg .flowchart-link" rule.
const TOPOLOGY_EDGE_SELECTOR = ".edgePath .path, .flowchart-link";
const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

// One color var per report.css ".series-topic:nth-of-type(n)" palette slot -- only
// the top-N topics by peak rate get their own line.
const THROUGHPUT_TOPIC_COLOR_VARS = [
  "--s1",
  "--s2",
  "--s3",
  "--s4",
  "--s5",
  "--s6",
  "--s7",
  "--s8",
];
const THROUGHPUT_TOP_N_TOPICS = THROUGHPUT_TOPIC_COLOR_VARS.length;

// Severity thresholds: metric key -> [warnAt, criticalAt].
const METRIC_KEY_DROPPED_PCT = "dropped_pct";
const DEFAULT_THRESHOLDS = { [METRIC_KEY_DROPPED_PCT]: [6.0, 12.0] };
const LEVEL_GOOD = "good";
const LEVEL_WARN = "warn";
const LEVEL_CRITICAL = "critical";
const LEVEL_NEUTRAL = "neutral";

// Chart geometry. The left and bottom margins are the ones that carry tick labels
// and an axis title, so they are wider than the other two.
const DEFAULT_CHART_WIDTH = 760;
const DEFAULT_CHART_HEIGHT = 240;
const CHART_MARGIN_LEFT = 56;
const CHART_MARGIN_RIGHT = 16;
const CHART_MARGIN_TOP = 16;
const CHART_MARGIN_BOTTOM = 52;
// The multiples of 1/2/5 * 10^n a "clean" axis-top value is rounded up to.
const NICE_CEILING_STEPS = [1, 2, 5, 10];
// Number of gridlines above 0 on the y-axis (5 labeled positions: 0, y_max/4, ...).
const Y_AXIS_TICK_COUNT = 4;
// Labeled positions on the x-axis, counting from 0 (so 5 in all).
const X_AXIS_TICK_COUNT = 4;
// Gap between a y-axis label and the gridline/baseline it names.
const AXIS_LABEL_GAP_PX = 6;
// Distance below the x-axis baseline of its tick labels and, under them, its title.
const X_AXIS_LABEL_OFFSET_PX = 16;
const X_AXIS_TITLE_OFFSET_PX = 38;
// Distance from the chart's left edge to the rotated y-axis title.
const Y_AXIS_TITLE_OFFSET_PX = 14;
// per_topic_hz is a per-bucket message count divided by the bucket width, so the
// y-axis is a rate, not a count.
const Y_AXIS_TITLE = "messages/sec";
const X_AXIS_TITLE = "time (s)";
const CSS_CLASS_AXIS_LINE = "chart-axis-line";
const CSS_CLASS_GRIDLINE = "chart-gridline";
const CSS_CLASS_AXIS_LABEL = "chart-axis-label";
const CSS_CLASS_AXIS_TITLE = "chart-axis-title";
// Must match report.css's ".series-topic:nth-of-type(n)" rule; topic polylines are
// grouped in their own <g> (see lineChartSvg) so indices aren't shifted by other series.
const CSS_CLASS_SERIES_TOPIC = "series-topic";
const CHART_ARIA_LABEL = "Throughput over time (messages/sec)";

const DECIMAL_PLACES = 2;

// A/B report: the payload's `compare` block holds B's rows, index-aligned to A's, so
// a cell can print both. Null in an ordinary single-trace report.
let compare = null;
const CONTROL_LABEL = "A";
const MODIFIED_LABEL = "B";
// Must match report.css's same-name rules.
const CSS_CLASS_COMPARE_B = "compare-b";
const CSS_CLASS_COMPARING = "comparing";
// Tints B's value when compare.py's significance test flagged this metric.
const CSS_CLASS_SIGNIFICANT = "significant";
// Which way a flagged shift went, tinting B's value green or red. Must match
// report.css's ".compare-b.significant.<name>" rules.
const CSS_CLASS_IMPROVEMENT = "improvement";
const CSS_CLASS_REGRESSION = "regression";
// Whether a smaller value is the better one. Interarrival and inter-callback gap
// track the cadence a pipeline was asked to hold, so a shift either way is a change
// rather than an improvement, and stays the neutral tint.
const POLARITY_LOWER_IS_BETTER = "lower-is-better";
const POLARITY_NEUTRAL = "neutral";
// Where B's value sits relative to A's: on its own line in the narrow sections 01/02,
// beside it in section 03, which has the width to spare.
const COMPARE_LAYOUT_STACKED = "stacked";
const COMPARE_LAYOUT_INLINE = "inline";

/**
 * Report whether a payload value is missing.
 *
 * @param {*} value The value to test.
 * @returns {boolean} True for null and undefined alike -- an absent metric and an
 *   unmatched row reach the renderer as one or the other.
 */
function isAbsent(value) {
  return value === null || value === undefined;
}

/**
 * Format a numeric value for display, or an em-dash placeholder if absent.
 *
 * @param {?number} value The value to format, or null/undefined.
 * @returns {string} `value` to 2 decimal places, or an em-dash placeholder.
 */
function fmt(value) {
  if (isAbsent(value)) {
    return EM_DASH;
  }
  return value.toFixed(DECIMAL_PLACES);
}

/**
 * Format a duration for the title block and chart captions.
 *
 * @param {number} durationS The duration in seconds.
 * @returns {string} The duration to 2 decimal places, with its unit.
 */
function formatSeconds(durationS) {
  return `${durationS.toFixed(DECIMAL_PLACES)}s`;
}

/**
 * Classify a value into a severity level for the given metric.
 *
 * @param {string} metricKey Which metric this value belongs to.
 * @param {?number} value The value to classify, or null.
 * @returns {string} "good" | "warn" | "critical" | "neutral". Unconfigured metric
 *   keys and null values are always "neutral".
 */
function severityLevel(metricKey, value) {
  const thresholds = DEFAULT_THRESHOLDS[metricKey];
  if (value === null || value === undefined || thresholds === undefined) {
    return LEVEL_NEUTRAL;
  }
  const [warnAt, criticalAt] = thresholds;
  if (value >= criticalAt) {
    return LEVEL_CRITICAL;
  }
  if (value >= warnAt) {
    return LEVEL_WARN;
  }
  return LEVEL_GOOD;
}

/**
 * Build a table cell.
 *
 * @param {string} text Cell text, inserted via textContent.
 * @param {string} className Space-separated CSS classes, or "" for none.
 * @returns {HTMLTableCellElement} The populated `<td>`.
 */
function td(text, className = "") {
  const cell = document.createElement("td");
  cell.textContent = text;
  if (className !== "") {
    cell.className = className;
  }
  return cell;
}

/**
 * Build a row from a list of cells.
 *
 * @param {Array<HTMLTableCellElement>} cells The row's cells, in order.
 * @returns {HTMLTableRowElement} The populated `<tr>`.
 */
function tr(cells) {
  const row = document.createElement("tr");
  cells.forEach((cell) => row.appendChild(cell));
  return row;
}

/**
 * Track the grouping columns of consecutive rows so a column that repeats the row
 * above renders empty, letting a run of rows sharing a value read as one group.
 *
 * Suppression is hierarchical: a column is blanked only when it and every column to
 * its left match the previous row, so entering a new outer group always reprints the
 * inner columns even when their values happen to carry over.
 *
 * @returns {function(Array<string>): Array<string>} Call once per rendered row with
 *   that row's grouping values, outermost first; returns the text to render for each.
 *   Rows that are skipped rather than appended must not be passed.
 */
function groupingColumnSuppressor() {
  let previous = null;
  return (values) => {
    let matchesPrevious = previous !== null;
    const texts = values.map((value, index) => {
      matchesPrevious = matchesPrevious && previous[index] === value;
      return matchesPrevious ? REPEATED_CELL_TEXT : value;
    });
    previous = values;
    return texts;
  };
}

/**
 * Coerce a cell's content to a DOM node.
 *
 * @param {(string|Node)} value Plain text, or an already-built node (e.g. a severity-
 *   tinted span).
 * @returns {Node} The node to append.
 */
function contentNode(value) {
  return typeof value === "string" ? document.createTextNode(value) : value;
}

/**
 * Resolve the B-side content for one cell.
 *
 * @param {?Object} row B's matched row, or null/undefined where nothing matched.
 * @param {function(Object): string} textOf Reads the displayed value off a matched row.
 * @returns {?string} null outside an A/B report (so the cell renders A's value alone),
 *   an em-dash where B had no matching row, otherwise B's formatted value.
 */
function comparedValue(row, textOf) {
  if (compare === null) {
    return null;
  }
  return isAbsent(row) ? EM_DASH : textOf(row);
}

/**
 * Build a table cell holding A's value, and B's alongside it in an A/B report.
 *
 * @param {(string|Node)} aValue A's content.
 * @param {?(string|Node)} bValue B's content, or null to render A's value alone.
 * @param {string} className Space-separated CSS classes, or "" for none.
 * @param {string} layout COMPARE_LAYOUT_STACKED or COMPARE_LAYOUT_INLINE.
 * @param {string} bClasses Space-separated classes for B's wrapper, on top of
 *   CSS_CLASS_COMPARE_B -- see significanceClasses(). "" for none.
 * @returns {HTMLTableCellElement} The populated `<td>`.
 */
function abCell(
  aValue,
  bValue,
  className = "",
  layout = COMPARE_LAYOUT_STACKED,
  bClasses = ""
) {
  const cell = document.createElement("td");
  if (className !== "") {
    cell.className = className;
  }
  cell.appendChild(contentNode(aValue));
  // Where neither trace measured this, one em-dash reads better than two stacked.
  if (bValue === null || (aValue === EM_DASH && bValue === EM_DASH)) {
    return cell;
  }
  const wrapper = document.createElement(
    layout === COMPARE_LAYOUT_STACKED ? "div" : "span"
  );
  wrapper.className =
    bClasses === "" ? CSS_CLASS_COMPARE_B : `${CSS_CLASS_COMPARE_B} ${bClasses}`;
  wrapper.appendChild(contentNode(bValue));
  cell.appendChild(wrapper);
  return cell;
}

/**
 * Classify a flagged shift for tinting: which way B moved, and whether that direction
 * means anything for this metric.
 *
 * @param {?Object} metric A's metric. Present whenever B's carries a verdict, since
 *   compare.py ventures one only where both sides measured the metric.
 * @param {?Object} bMetric B's matching metric, or null.
 * @param {string} polarity POLARITY_LOWER_IS_BETTER or POLARITY_NEUTRAL.
 * @returns {string} The classes for B's wrapper: "" where compare.py flagged nothing,
 *   otherwise CSS_CLASS_SIGNIFICANT alone for a neutral metric, or with
 *   CSS_CLASS_IMPROVEMENT / CSS_CLASS_REGRESSION. Direction is read off p50, the same
 *   statistic compare.py's effect-size gate uses, so a flagged shift always has one.
 */
function significanceClasses(metric, bMetric, polarity) {
  if (isAbsent(bMetric) || bMetric.significant !== true) {
    return "";
  }
  if (polarity === POLARITY_NEUTRAL) {
    return CSS_CLASS_SIGNIFICANT;
  }
  const direction =
    bMetric.p50 > metric.p50 ? CSS_CLASS_REGRESSION : CSS_CLASS_IMPROVEMENT;
  return `${CSS_CLASS_SIGNIFICANT} ${direction}`;
}

/**
 * Render the p50/p90 cell pair for one metric.
 *
 * @param {?Object} metric The metric to render, or null.
 * @param {?Object} bMetric B's matching metric in an A/B report; null both outside one
 *   and where B had no matching row, which comparedValue() tells apart.
 * @param {string} layout Where B's value sits relative to A's.
 * @param {string} polarity Whether a lower value is the better one for this metric --
 *   see significanceClasses().
 * @returns {Array<HTMLTableCellElement>} p50/p90 cells (p50 bold); em-dash
 *   placeholders stand in for whichever trace did not measure the metric. Both
 *   cells share one significance verdict -- see bMetric.significant.
 */
function metricCells(
  metric,
  bMetric = null,
  layout = COMPARE_LAYOUT_STACKED,
  polarity = POLARITY_LOWER_IS_BETTER
) {
  const p50 = isAbsent(metric) ? EM_DASH : fmt(metric.p50);
  const p90 = isAbsent(metric) ? EM_DASH : fmt(metric.p90);
  const bClasses = significanceClasses(metric, bMetric, polarity);
  return [
    abCell(
      p50,
      comparedValue(bMetric, (m) => fmt(m.p50)),
      "num p50",
      layout,
      bClasses
    ),
    abCell(
      p90,
      comparedValue(bMetric, (m) => fmt(m.p90)),
      "num",
      layout,
      bClasses
    ),
  ];
}

/**
 * Generate Mermaid flowchart source for the pipeline topology.
 *
 * @param {Object} report The report payload.
 * @returns {string} Mermaid flowchart source text (not markup). Starts with
 *   `flowchart LR`, then declares each distinct node and edge in deterministic
 *   order. If the report has no edges, returns a minimal diagram with a single
 *   empty-state node.
 */
export function topologyMermaid(report) {
  const lines = [`flowchart ${MERMAID_FLOWCHART_DIR}`];

  if (report.edges.length === 0) {
    lines.push(`    ${EMPTY_DIAGRAM_NODE_ID}["${MERMAID_EMPTY_DIAGRAM_MESSAGE}"]`);
    return lines.join("\n");
  }

  const allNodes = new Set();
  report.edges.forEach((edge) => {
    allNodes.add(edge.from_node);
    allNodes.add(edge.to_node);
  });
  const sortedNodes = Array.from(allNodes).sort();

  // Two distinct node names can sanitize to the same id (e.g. "/a/b" and "/a_b"
  // both become "n__a_b"); a numeric suffix disambiguates any collision.
  const nodeToId = new Map();
  const idOccurrences = new Map();
  sortedNodes.forEach((nodeName) => {
    const sanitized = nodeName.replace(/[^a-zA-Z0-9_]/g, "_");
    const baseId = `${NODE_ID_PREFIX}${sanitized}`;
    const occurrence = idOccurrences.get(baseId) ?? 0;
    idOccurrences.set(baseId, occurrence + 1);
    nodeToId.set(nodeName, occurrence === 0 ? baseId : `${baseId}_${occurrence}`);
  });

  sortedNodes.forEach((nodeName) => {
    lines.push(`    ${nodeToId.get(nodeName)}["${nodeName}"]`);
  });

  const sortedEdges = [...report.edges].sort(
    (a, b) =>
      a.from_node.localeCompare(b.from_node) ||
      a.to_node.localeCompare(b.to_node) ||
      a.topic.localeCompare(b.topic)
  );
  sortedEdges.forEach((edge) => {
    lines.push(
      `    ${nodeToId.get(edge.from_node)} -->|${edge.topic}| ${nodeToId.get(edge.to_node)}`
    );
  });

  return lines.join("\n");
}

/**
 * Color each Mermaid edge path with its own base-to-tip gradient, measured from the
 * laid-out path. A static objectBoundingBox gradient cannot express per-edge
 * direction, so this runs after mermaid.run() has produced real paths.
 *
 * @returns {void}
 */
function colorTopologyEdges() {
  let gradientCount = 0;
  document.querySelectorAll(".topology svg").forEach((svg) => {
    let defs = svg.querySelector("defs");
    if (defs === null) {
      defs = document.createElementNS(SVG_NAMESPACE, "defs");
      svg.insertBefore(defs, svg.firstChild);
    }
    svg.querySelectorAll(TOPOLOGY_EDGE_SELECTOR).forEach((path) => {
      const length = path.getTotalLength();
      if (length === 0) {
        return;
      }
      const base = path.getPointAtLength(0);
      const tip = path.getPointAtLength(length);
      const gradientId = `topology-edge-gradient-${gradientCount++}`;
      const gradient = document.createElementNS(SVG_NAMESPACE, "linearGradient");
      gradient.setAttribute("id", gradientId);
      gradient.setAttribute("gradientUnits", "userSpaceOnUse");
      gradient.setAttribute("x1", base.x);
      gradient.setAttribute("y1", base.y);
      gradient.setAttribute("x2", tip.x);
      gradient.setAttribute("y2", tip.y);
      [
        ["0%", TOPOLOGY_EDGE_BASE_COLOR],
        ["100%", TOPOLOGY_EDGE_TIP_COLOR],
      ].forEach(([offset, color]) => {
        const stop = document.createElementNS(SVG_NAMESPACE, "stop");
        stop.setAttribute("offset", offset);
        stop.setAttribute("stop-color", color);
        gradient.appendChild(stop);
      });
      defs.appendChild(gradient);
      path.style.stroke = `url(#${gradientId})`;
    });
  });
}

/**
 * Describe how to read an A/B report's cells, and what the comparison discarded.
 *
 * @returns {string} The key line shown under the capture window.
 */
function compareKeyText() {
  const parts = [
    `Each cell shows ${CONTROL_LABEL} (control) and then ${MODIFIED_LABEL}` +
      ` (modified), dimmed. Every row, label and ordering is ${CONTROL_LABEL}'s.`,
  ];
  if (compare.unmatched_row_count > 0) {
    let dropped =
      `${compare.unmatched_row_count} row(s) in ${MODIFIED_LABEL} had no` +
      ` counterpart in ${CONTROL_LABEL} and were dropped`;
    if (compare.unstable_label_count > 0) {
      dropped +=
        `; ${compare.unstable_label_count} of those name a callback by a per-run` +
        " handle, so re-trace from process start to match them";
    }
    parts.push(`${dropped}.`);
  }
  return parts.join(" ");
}

/**
 * Fill in the report title and capture window, plus the A/B key when comparing.
 *
 * @param {Object} report The report payload.
 * @returns {void}
 */
function renderTitleBlock(report) {
  const title = report.title || DEFAULT_TITLE;
  document.title = title;
  document.getElementById("report-title").textContent = title;
  const captureWindow = document.getElementById("capture-window");
  if (compare === null) {
    captureWindow.textContent =
      `Capture window: ${formatSeconds(report.duration_s)}`;
    return;
  }
  // Counts scale with the capture window, so both are named where a reader comparing
  // Pub/Rcv/Dropped/Traversals will see them.
  captureWindow.textContent =
    `Capture window: ${CONTROL_LABEL} ${formatSeconds(report.duration_s)}` +
    ` · ${MODIFIED_LABEL} ${formatSeconds(compare.duration_s)}`;
  const key = document.getElementById("compare-key");
  key.textContent = compareKeyText();
  key.hidden = false;
}

/**
 * Render the topology diagram source and expose the arrowhead color to report.css.
 *
 * @param {Object} report The report payload.
 * @returns {void}
 */
function renderTopology(report) {
  document.getElementById("topology-diagram").textContent = topologyMermaid(report);
  document
    .getElementById("topology")
    .style.setProperty(TOPOLOGY_EDGE_TIP_CSS_VAR, TOPOLOGY_EDGE_TIP_COLOR);
}

/**
 * Resolve how many of an edge's messages its destination received, as the number of
 * times that destination's callback ran.
 *
 * @param {?Object} pub The edge's source publisher, or null.
 * @param {?Object} cb The edge's destination callback, or null.
 * @returns {?number} The callback's invocation count, never above the edge's published
 *   count; null where the destination runs no traced callback.
 */
function edgeReceivedCount(pub, cb) {
  if (cb === null || cb.duration === null) {
    return null;
  }
  if (pub === null) {
    return cb.duration.count;
  }
  // The callback count covers every publisher of the topic while an edge's Pub covers
  // one, so it can exceed Pub. Capping it is a stand-in for a per-edge count, and
  // reports no drop on such an edge rather than a negative one.
  return Math.min(cb.duration.count, pub.published_count);
}

/**
 * Compute one edge's own dropped-message count and rate, as what its publisher sent
 * minus what its destination received.
 *
 * @param {?Object} pub The edge's source publisher, or null.
 * @param {?number} receivedCount The edge's received count, from edgeReceivedCount().
 * @returns {?Object} {count, pct}, or null where the edge's own share is not
 *   attributable: no publisher, no traced callback, or nothing published.
 */
function edgeDropped(pub, receivedCount) {
  if (pub === null || receivedCount === null || pub.published_count === 0) {
    return null;
  }
  const droppedCount = pub.published_count - receivedCount;
  return { count: droppedCount, pct: (droppedCount / pub.published_count) * 100 };
}

/**
 * Count one edge's published messages.
 *
 * @param {Object} edge The edge to describe.
 * @returns {string} The published count, or an em-dash where the edge has no
 *   publisher.
 */
function edgePublishedText(edge) {
  return edge.publisher === null ? EM_DASH : String(edge.publisher.published_count);
}

/**
 * Count how many of one edge's messages its destination received.
 *
 * @param {Object} edge The edge to describe.
 * @returns {string} The received count, or an em-dash where the destination runs no
 *   traced callback.
 */
function edgeReceivedText(edge) {
  const received = edgeReceivedCount(edge.publisher, edge.callback);
  return received === null ? EM_DASH : String(received);
}

/**
 * Build one edge's dropped-message content: the count and percentage, tinted by
 * severity.
 *
 * @param {?Object} edge The edge to describe, or null/undefined where nothing matched.
 * @returns {(string|Node)} A tinted `<span>`, or an em-dash where the edge's own share
 *   is not attributable.
 */
function droppedContent(edge) {
  if (isAbsent(edge)) {
    return EM_DASH;
  }
  const dropped = edgeDropped(
    edge.publisher,
    edgeReceivedCount(edge.publisher, edge.callback)
  );
  if (dropped === null) {
    return EM_DASH;
  }
  const span = document.createElement("span");
  span.className = `sev-${severityLevel(METRIC_KEY_DROPPED_PCT, dropped.pct)}`;
  span.textContent = `${dropped.count} (${fmt(dropped.pct)}%)`;
  return span;
}

/**
 * Read a metric off one edge's publisher or callback, tolerating an unmatched edge.
 *
 * @param {?Object} edge The edge to read, or null/undefined where nothing matched.
 * @param {function(Object): ?Object} metricOf Reads the metric off a present edge.
 * @returns {?Object} The metric, or null where the edge or its endpoint is absent.
 */
function edgeMetric(edge, metricOf) {
  return isAbsent(edge) ? null : metricOf(edge);
}

/**
 * Render section 01: one row per edge. Source, Destination and Topic are each left
 * blank where they repeat the row above, independently of one another.
 *
 * @param {Object} report The report payload.
 * @returns {void}
 */
function renderTopicStats(report) {
  const body = document.getElementById("topic-stats-rows");
  // One single-column suppressor each, so a column blanks purely on its own repeat
  // rather than on the columns beside it.
  const suppressTopic = groupingColumnSuppressor();
  const suppressFromNode = groupingColumnSuppressor();
  const suppressToNode = groupingColumnSuppressor();
  report.edges.forEach((edge, index) => {
    const bEdge = compare === null ? null : compare.edges[index];

    const [topicText] = suppressTopic([edge.topic]);
    const [fromNodeText] = suppressFromNode([edge.from_node]);
    const [toNodeText] = suppressToNode([edge.to_node]);

    body.appendChild(
      tr([
        td(fromNodeText),
        td(toNodeText),
        td(topicText),
        abCell(edgePublishedText(edge), comparedValue(bEdge, edgePublishedText), "num"),
        abCell(edgeReceivedText(edge), comparedValue(bEdge, edgeReceivedText), "num"),
        abCell(
          droppedContent(edge),
          compare === null ? null : droppedContent(bEdge),
          "num"
        ),
        ...metricCells(
          edgeMetric(edge, (e) =>
            e.publisher === null ? null : e.publisher.callback_start_to_publish
          ),
          edgeMetric(bEdge, (e) =>
            e.publisher === null ? null : e.publisher.callback_start_to_publish
          )
        ),
        ...metricCells(
          edgeMetric(edge, (e) =>
            e.callback === null ? null : e.callback.topic_latency
          ),
          edgeMetric(bEdge, (e) =>
            e.callback === null ? null : e.callback.topic_latency
          )
        ),
      ])
    );
  });
}

/**
 * Count one callback's inter-callback gaps below the report's threshold.
 *
 * @param {Object} callback The callback to describe.
 * @returns {string} The count, or an em-dash where it was not measured -- which in an
 *   A/B report also covers B counting against a different threshold, since comparing
 *   those two numbers would compare two different measurements.
 */
function waitCountText(callback) {
  return callback.instantaneous_wait_count === null
    ? EM_DASH
    : String(callback.instantaneous_wait_count);
}

/**
 * Render section 02: one row per callback and per publisher, grouped by node
 * (topological order). A Node, and a Function call within the same Node, that repeats
 * the row above is left blank.
 *
 * @param {Object} report The report payload.
 * @returns {void}
 */
function renderNodeAnalysis(report) {
  const body = document.getElementById("node-analysis-rows");

  // Section 02 walks nodes rather than the payload's flat lists, so B's rows -- which
  // are aligned to those lists by index -- are keyed back by the A row they matched.
  const comparedRow = new Map();
  if (compare !== null) {
    report.callbacks.forEach((cb, index) =>
      comparedRow.set(cb, compare.callbacks[index])
    );
    report.publishers.forEach((pub, index) =>
      comparedRow.set(pub, compare.publishers[index])
    );
  }

  const callbacksByNode = new Map();
  report.callbacks.forEach((cb) => {
    if (!callbacksByNode.has(cb.node)) {
      callbacksByNode.set(cb.node, []);
    }
    callbacksByNode.get(cb.node).push(cb);
  });
  const publishersByNode = new Map();
  report.publishers.forEach((pub) => {
    if (!publishersByNode.has(pub.node)) {
      publishersByNode.set(pub.node, []);
    }
    publishersByNode.get(pub.node).push(pub);
  });

  let thresholdMs = null;
  const suppressRepeats = groupingColumnSuppressor();
  report.node_names.forEach((nodeName) => {
    (callbacksByNode.get(nodeName) ?? []).forEach((cb) => {
      let functionCall;
      if (cb.callback_type === CALLBACK_TYPE_TIMER) {
        functionCall = TIMER_FUNCTION_CALL_LABEL;
      } else if (cb.callback_type === CALLBACK_TYPE_SUBSCRIPTION) {
        functionCall = SUBSCRIBER_FUNCTION_CALL_LABEL;
      } else {
        return;
      }
      const bCb = comparedRow.get(cb);
      const [nodeText, functionCallText] = suppressRepeats([cb.node, functionCall]);
      body.appendChild(
        tr([
          td(nodeText),
          td(functionCallText),
          td(cb.topic === null ? EM_DASH : cb.topic),
          abCell(
            cb.callback_hz.toFixed(DECIMAL_PLACES),
            comparedValue(bCb, (c) => c.callback_hz.toFixed(DECIMAL_PLACES)),
            "num"
          ),
          ...metricCells(cb.duration, isAbsent(bCb) ? null : bCb.duration),
          ...metricCells(
            cb.interarrival,
            isAbsent(bCb) ? null : bCb.interarrival,
            COMPARE_LAYOUT_STACKED,
            POLARITY_NEUTRAL
          ),
          ...metricCells(
            cb.inter_callback_gap,
            isAbsent(bCb) ? null : bCb.inter_callback_gap,
            COMPARE_LAYOUT_STACKED,
            POLARITY_NEUTRAL
          ),
          abCell(
            waitCountText(cb),
            comparedValue(bCb, waitCountText)
          ),
        ])
      );
      if (thresholdMs === null) {
        thresholdMs = cb.instantaneous_wait_threshold_ms;
      }
    });
    (publishersByNode.get(nodeName) ?? []).forEach((pub) => {
      const bPub = comparedRow.get(pub);
      const [nodeText, functionCallText] = suppressRepeats([
        pub.node,
        PUBLISH_FUNCTION_CALL_LABEL,
      ]);
      body.appendChild(
        tr([
          td(nodeText),
          td(functionCallText),
          td(pub.topic),
          abCell(
            pub.publisher_hz.toFixed(DECIMAL_PLACES),
            comparedValue(bPub, (p) => p.publisher_hz.toFixed(DECIMAL_PLACES)),
            "num"
          ),
          ...metricCells(null), // Duration
          ...metricCells(null), // Interarrival
          ...metricCells(null), // Inter-callback gap
          abCell(EM_DASH, compare === null ? null : EM_DASH), // Instant waits
        ])
      );
    });
  });

  document
    .getElementById("short-gap-header")
    .setAttribute(
      "data-tooltip",
      `Number of inter-callback gaps below ${fmt(thresholdMs)}ms.`
    );
}

/**
 * Build a chain's one-line label: its arrow-joined topics, and where a pub-terminated
 * chain ends. Names no node, keeping the summary row to a single line -- the steps
 * table's heading is where a chain's nodes are spelled out.
 *
 * @param {Object} chain The chain to label.
 * @returns {string} The arrow-joined topic path.
 */
function chainLabel(chain) {
  const path = chain.steps.map((step) => step.topic).join(` ${ARROW} `);
  if (chain.terminator === CHAIN_TERMINATOR_PUB) {
    return path + PUBLISH_TERMINUS_LABEL;
  }
  return path;
}

/**
 * Build one endpoint's table cell: the topic and moment on one line, over the node
 * it happens in, set quieter so the line that varies row to row stays the one read.
 *
 * @param {Object} endpoint One `ChainEndpoint` (payload field: `topic`, `kind`,
 *   `node`), from a `Chain.step_endpoints` row's `from_endpoint`/`to_endpoint`.
 * @returns {HTMLTableCellElement} The populated `<td>`.
 */
function endpointCell(endpoint) {
  const cell = document.createElement("td");
  cell.appendChild(
    document.createTextNode(`${endpoint.topic} ${endpoint.kind}`)
  );
  const node = document.createElement("div");
  node.className = CSS_CLASS_ENDPOINT_NODE;
  node.textContent = endpoint.node;
  cell.appendChild(node);
  return cell;
}

/**
 * Build one chain's topic path: the arrow-joined topics, each set over the nodes it
 * travels between, quieter so the topic path stays the line read.
 *
 * @param {Object} chain The chain to label.
 * @returns {HTMLDivElement} The populated path, for a heading or a table cell.
 */
function chainPath(chain) {
  const path = document.createElement("div");
  path.className = CSS_CLASS_CHAIN_PATH;
  chain.steps.forEach((step, index) => {
    if (index > 0) {
      path.appendChild(document.createTextNode(ARROW));
    }
    const topic = document.createElement("span");
    topic.className = CSS_CLASS_CHAIN_TOPIC;
    topic.appendChild(document.createTextNode(step.topic));

    const nodes = document.createElement("span");
    nodes.className = CSS_CLASS_CHAIN_TOPIC_NODES;
    // A pub-terminated chain's last topic is never taken, so it names one node.
    nodes.textContent =
      step.subscribing_node === null
        ? step.publishing_node
        : `${step.publishing_node} ${ARROW} ${step.subscribing_node}`;
    topic.appendChild(nodes);

    path.appendChild(topic);
  });
  return path;
}

/**
 * Resolve B's step_endpoints rows for one chain, paired with A's by index.
 *
 * @param {?Object} bChain B's matched chain, or null/undefined where nothing matched.
 * @returns {?Array<Object>} Rows index-aligned with `chain.step_endpoints`, or null
 *   when B has no comparable steps -- the chain went unmatched, or its steps differ
 *   in shape, which build_comparison() reports by leaving `step_endpoints` null.
 */
function comparedStepRows(bChain) {
  if (isAbsent(bChain) || bChain.step_endpoints === null) {
    return null;
  }
  return bChain.step_endpoints;
}

/**
 * Build one chain's steps table: a From/To endpoint pair plus a single p50/p90
 * metric pair, since every row in `chain.step_endpoints` carries exactly one metric.
 *
 * @param {Object} chain The chain whose steps to render.
 * @param {?Object} bChain B's matched chain in an A/B report, else null.
 * @returns {HTMLDivElement} One block holding the heading and the scrollable table.
 */
function chainStepsTable(chain, bChain) {
  const rows = chain.step_endpoints;
  const bRows = comparedStepRows(bChain);

  const heading = document.createElement("h3");
  heading.appendChild(chainPath(chain));

  const table = document.createElement("table");

  const head = document.createElement("thead");
  head.innerHTML =
    "<tr><th>From</th><th>To</th>" +
    '<th class="num p50">p50 (ms)</th><th class="num">p90 (ms)</th></tr>';
  table.appendChild(head);

  const body = document.createElement("tbody");
  rows.forEach((row, index) => {
    body.appendChild(
      tr([
        endpointCell(row.from_endpoint),
        endpointCell(row.to_endpoint),
        ...metricCells(
          row.metric,
          bRows === null ? null : bRows[index].metric,
          COMPARE_LAYOUT_INLINE
        ),
      ])
    );
  });
  table.appendChild(body);

  const scroll = document.createElement("div");
  scroll.className = "table-scroll";
  scroll.appendChild(table);

  const block = document.createElement("div");
  block.className = CSS_CLASS_CHAIN_BLOCK;
  block.appendChild(heading);
  block.appendChild(scroll);
  return block;
}

/**
 * Render section 03: a summary table (one row per chain) followed by each chain's
 * own per-step table.
 *
 * @param {Object} report The report payload.
 * @returns {void}
 */
function renderChainLatency(report) {
  if (report.chains.length === 0) {
    document.getElementById("chain-latency-empty").hidden = false;
    return;
  }
  document.getElementById("chain-latency-content").hidden = false;

  const summaryBody = document.getElementById("chain-summary-rows");
  const stepsHost = document.getElementById("chain-steps-tables");
  report.chains.forEach((chain, index) => {
    const bChain = compare === null ? null : compare.chains[index];
    summaryBody.appendChild(
      tr([
        td(chainLabel(chain)),
        abCell(
          String(chain.traversal_count),
          comparedValue(bChain, (c) => String(c.traversal_count)),
          "num",
          COMPARE_LAYOUT_INLINE
        ),
        ...metricCells(
          chain.latency,
          isAbsent(bChain) ? null : bChain.latency,
          COMPARE_LAYOUT_INLINE
        ),
      ])
    );
    stepsHost.appendChild(chainStepsTable(chain, bChain));
  });
}

/**
 * Park each two-row sticky header's second row directly beneath its first, by
 * publishing the first row's measured height to report.css. Re-measures on resize,
 * since the height changes with how the first row's labels wrap.
 *
 * @returns {void}
 */
function syncStickyHeaderOffsets() {
  document.querySelectorAll(`table.${CSS_CLASS_STICKY_HEADER}`).forEach((table) => {
    const firstRow = table.tHead.rows[0];
    // Floored so a fractional row height overlaps the row above rather than opening a
    // gap for the scrolling body to show through.
    const publish = () =>
      table.style.setProperty(
        STICKY_HEADER_ROW1_HEIGHT_CSS_VAR,
        `${Math.floor(firstRow.getBoundingClientRect().height)}px`
      );
    publish();
    new ResizeObserver(publish).observe(firstRow);
  });
}

/**
 * Round a value up to a visually clean axis-top value (a 1/2/5 * 10^n multiple).
 *
 * @param {number} value The maximum data value the axis must cover.
 * @returns {number} The rounded-up axis-top value. Returns 1.0 for non-positive input.
 */
export function niceCeiling(value) {
  if (value <= 0) {
    return 1.0;
  }
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const normalized = value / magnitude;
  for (const step of NICE_CEILING_STEPS) {
    if (normalized <= step) {
      return step * magnitude;
    }
  }
  return NICE_CEILING_STEPS[NICE_CEILING_STEPS.length - 1] * magnitude;
}

/**
 * Map a time value to a pixel x-coordinate within the plot area.
 *
 * @param {number} tS Time in seconds.
 * @param {number} xMaxS The trace's full duration in seconds (the axis span).
 * @param {number} plotWidth Plot area width in pixels.
 * @returns {number} Pixel x-coordinate, 0 at tS=0. 0 if xMaxS <= 0.
 */
export function xOf(tS, xMaxS, plotWidth) {
  if (xMaxS <= 0) {
    return 0.0;
  }
  return (tS / xMaxS) * plotWidth;
}

/**
 * Map a data value to a pixel y-coordinate within the plot area (inverted, since SVG
 * y grows downward: 0 maps to the plot bottom, yMax to the plot top).
 *
 * @param {number} value The data value.
 * @param {number} yMax The axis-top value (see niceCeiling).
 * @param {number} plotHeight Plot area height in pixels.
 * @returns {number} Pixel y-coordinate. plotHeight (the bottom) if yMax <= 0.
 */
export function yOf(value, yMax, plotHeight) {
  if (yMax <= 0) {
    return plotHeight;
  }
  return plotHeight - (value / yMax) * plotHeight;
}

/**
 * Format a list of (x, y) pixel points as an SVG `points` attribute value.
 *
 * @param {Array<Array<number>>} points (x, y) pixel coordinate pairs.
 * @returns {string} "x1,y1 x2,y2 ...", each coordinate to 2 decimal places.
 */
export function polyline(points) {
  return points
    .map(([x, y]) => `${x.toFixed(DECIMAL_PLACES)},${y.toFixed(DECIMAL_PLACES)}`)
    .join(" ");
}

/**
 * Format an axis tick value, dropping the fractional part when it's a whole number.
 *
 * @param {number} value The tick's data value (messages/sec, or seconds on the x-axis).
 * @returns {string} The formatted label, e.g. "20" or "2.5".
 */
function formatAxisValue(value) {
  return Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1);
}

/**
 * Build both axes: the two baselines, evenly-spaced horizontal gridlines, a tick
 * label at every labeled position on each axis, and each axis's title. Elements are
 * flat (not `<g>`-wrapped) so they don't perturb lineChartSvg()'s topic-polyline `<g>`.
 *
 * @param {number} xMaxS The trace's full duration in seconds (the x-axis span).
 * @param {number} yMax The axis-top value (see niceCeiling()).
 * @param {Object} plot The plot area in pixels, {left, top, width, height}.
 * @returns {string} The axis `<line>`/`<text>` elements, concatenated.
 */
function axesSvg(xMaxS, yMax, plot) {
  const right = plot.left + plot.width;
  const bottom = plot.top + plot.height;
  const parts = [
    `<line class="${CSS_CLASS_AXIS_LINE}" x1="${plot.left}" y1="${plot.top}" ` +
      `x2="${plot.left}" y2="${bottom}" />`,
    `<line class="${CSS_CLASS_AXIS_LINE}" x1="${plot.left}" y1="${bottom}" ` +
      `x2="${right}" y2="${bottom}" />`,
  ];
  for (let i = 0; i <= Y_AXIS_TICK_COUNT; i++) {
    const value = (yMax * i) / Y_AXIS_TICK_COUNT;
    const y = plot.top + yOf(value, yMax, plot.height);
    if (i > 0) {
      parts.push(
        `<line class="${CSS_CLASS_GRIDLINE}" x1="${plot.left}" y1="${y.toFixed(DECIMAL_PLACES)}" ` +
          `x2="${right}" y2="${y.toFixed(DECIMAL_PLACES)}" />`
      );
    }
    parts.push(
      `<text class="${CSS_CLASS_AXIS_LABEL}" x="${plot.left - AXIS_LABEL_GAP_PX}" ` +
        `y="${y.toFixed(DECIMAL_PLACES)}" text-anchor="end" dominant-baseline="middle">` +
        `${formatAxisValue(value)}</text>`
    );
  }
  for (let i = 0; i <= X_AXIS_TICK_COUNT; i++) {
    const tS = (xMaxS * i) / X_AXIS_TICK_COUNT;
    const x = plot.left + xOf(tS, xMaxS, plot.width);
    parts.push(
      `<text class="${CSS_CLASS_AXIS_LABEL}" x="${x.toFixed(DECIMAL_PLACES)}" ` +
        `y="${bottom + X_AXIS_LABEL_OFFSET_PX}" text-anchor="middle">` +
        `${formatAxisValue(tS)}</text>`
    );
  }
  const yTitleY = plot.top + plot.height / 2;
  parts.push(
    `<text class="${CSS_CLASS_AXIS_TITLE}" x="${Y_AXIS_TITLE_OFFSET_PX}" ` +
      `y="${yTitleY.toFixed(DECIMAL_PLACES)}" text-anchor="middle" ` +
      `transform="rotate(-90 ${Y_AXIS_TITLE_OFFSET_PX} ${yTitleY.toFixed(DECIMAL_PLACES)})">` +
      `${Y_AXIS_TITLE}</text>`,
    `<text class="${CSS_CLASS_AXIS_TITLE}" x="${plot.left + plot.width / 2}" ` +
      `y="${bottom + X_AXIS_TITLE_OFFSET_PX}" text-anchor="middle">${X_AXIS_TITLE}</text>`
  );
  return parts.join("");
}

/**
 * Render an inline `<svg>` line chart: one polyline per series, sharing an x-axis of
 * `bucketStartsS` over `[0, xMaxS]`.
 *
 * @param {Array<Object>} series The lines to draw, each {values, cssClass}.
 * @param {Array<number>} bucketStartsS The x-coordinate (seconds) of each sample,
 *   shared across every series.
 * @param {number} xMaxS The trace's full duration in seconds.
 * @param {number} yMax The axis-top value (see niceCeiling()).
 * @returns {string} A self-contained `<svg>...</svg>` fragment, `role="img"` with an
 *   `aria-label` for accessibility.
 */
function lineChartSvg(series, bucketStartsS, xMaxS, yMax) {
  const width = DEFAULT_CHART_WIDTH;
  const height = DEFAULT_CHART_HEIGHT;
  const plot = {
    left: CHART_MARGIN_LEFT,
    top: CHART_MARGIN_TOP,
    width: width - CHART_MARGIN_LEFT - CHART_MARGIN_RIGHT,
    height: height - CHART_MARGIN_TOP - CHART_MARGIN_BOTTOM,
  };

  const polylines = [];
  const topicPolylines = [];
  series.forEach((s) => {
    // An empty series still emits its (empty) polyline, so a topic missing from one
    // trace does not shift the colors of the ones after it.
    const points =
      s.values.length === 0
        ? []
        : bucketStartsS.map((t, i) => [
            plot.left + xOf(t, xMaxS, plot.width),
            plot.top + yOf(s.values[i], yMax, plot.height),
          ]);
    const line =
      `<polyline class="${s.cssClass}" fill="none" points="${polyline(points)}" />`;
    if (s.cssClass === CSS_CLASS_SERIES_TOPIC) {
      topicPolylines.push(line);
    } else {
      polylines.push(line);
    }
  });

  // Grouped in their own <g> so report.css's nth-of-type CSS counts only among
  // themselves (see CSS_CLASS_SERIES_TOPIC).
  const topicGroup =
    topicPolylines.length > 0 ? `<g>${topicPolylines.join("")}</g>` : "";

  return (
    `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${CHART_ARIA_LABEL}">` +
    axesSvg(xMaxS, yMax, plot) +
    polylines.join("") +
    topicGroup +
    "</svg>"
  );
}

/**
 * Pick the topics with the highest peak publish rate, for the chart's limited color
 * palette.
 *
 * @param {Object} throughput The parsed throughput series.
 * @param {number} n Max number of topics to select.
 * @returns {Array<string>} Up to `n` topic names, ordered by descending peak rate
 *   (ties broken by topic name for determinism).
 */
function topThroughputTopics(throughput, n) {
  const peakOf = (topic) => {
    const values = throughput.per_topic_hz[topic];
    return values.length === 0 ? 0.0 : Math.max(...values);
  };
  return Object.keys(throughput.per_topic_hz)
    .sort((a, b) => peakOf(b) - peakOf(a) || a.localeCompare(b))
    .slice(0, n);
}

/**
 * Build one `.chart-legend-item` entry, its swatch tinted to match the line it labels.
 *
 * @param {string} label The series label (a topic name).
 * @param {string} colorVar The CSS custom property the matching polyline is colored with.
 * @returns {HTMLSpanElement} The legend entry.
 */
function throughputLegendItem(label, colorVar) {
  const item = document.createElement("span");
  item.className = "chart-legend-item";
  const swatch = document.createElement("span");
  swatch.className = "chart-legend-swatch";
  swatch.style.background = `var(${colorVar})`;
  item.appendChild(swatch);
  item.appendChild(document.createTextNode(label));
  return item;
}

/**
 * Build one trace's chart series: one line per named topic.
 *
 * @param {Object} throughput The parsed throughput series.
 * @param {Array<string>} topics The topics to plot, in palette order. A topic the
 *   trace never published yields an empty line, so a color names the same topic in
 *   both charts of an A/B report.
 * @returns {Array<Object>} The lines to draw, each {values, cssClass}.
 */
function throughputSeries(throughput, topics) {
  const series = [];
  topics.forEach((topic) => {
    const values = throughput.per_topic_hz[topic];
    series.push({
      values: values === undefined ? [] : values,
      cssClass: CSS_CLASS_SERIES_TOPIC,
    });
  });
  return series;
}

/**
 * Find the highest value any of a chart's series reaches.
 *
 * @param {Array<Object>} series The lines to measure, each {values, cssClass}.
 * @returns {number} The peak value, or 0 when every series is empty.
 */
function seriesPeak(series) {
  return Math.max(
    ...series.filter((s) => s.values.length > 0).map((s) => Math.max(...s.values)),
    0
  );
}

/**
 * Render section 04: an SVG line chart of per-topic publish rate over time, with a
 * color-swatch legend below it. An A/B report draws B's own chart below A's rather
 * than overlaying it.
 *
 * @param {Object} report The report payload.
 * @returns {void}
 */
function renderThroughputChart(report) {
  const throughput = report.throughput;
  if (isAbsent(throughput)) {
    document.getElementById("throughput-empty").hidden = false;
    return;
  }
  document.getElementById("throughput-content").hidden = false;

  const topTopics = topThroughputTopics(throughput, THROUGHPUT_TOP_N_TOPICS);
  const legend = document.getElementById("throughput-legend");
  topTopics.forEach((topic, i) =>
    legend.appendChild(throughputLegendItem(topic, THROUGHPUT_TOPIC_COLOR_VARS[i]))
  );

  const series = throughputSeries(throughput, topTopics);
  const bThroughput = compare === null ? null : compare.throughput;
  const bSeries = isAbsent(bThroughput)
    ? null
    : throughputSeries(bThroughput, topTopics);
  // Both charts share a y-axis: on independent scales two different rates would draw
  // the same shape.
  const yMax = niceCeiling(
    Math.max(seriesPeak(series), bSeries === null ? 0 : seriesPeak(bSeries))
  );

  document.getElementById("throughput-chart").innerHTML = lineChartSvg(
    series,
    throughput.bucket_starts_s,
    report.duration_s,
    yMax
  );
  if (bSeries === null) {
    return;
  }

  const caption = document.getElementById("throughput-caption");
  caption.textContent = `${CONTROL_LABEL} — ${formatSeconds(report.duration_s)}`;
  caption.hidden = false;
  document.getElementById("throughput-compare-caption").textContent =
    `${MODIFIED_LABEL} — ${formatSeconds(compare.duration_s)}`;
  document.getElementById("throughput-compare-chart").innerHTML = lineChartSvg(
    bSeries,
    bThroughput.bucket_starts_s,
    compare.duration_s,
    yMax
  );
  document.getElementById("throughput-compare").hidden = false;
}

/**
 * Read the embedded report payload, falling back to the checked-in sample so
 * index.html can be opened directly while working on the frontend.
 *
 * @returns {Promise<Object>} The report payload.
 */
async function loadReport() {
  const embedded = JSON.parse(document.getElementById("trace-report").textContent);
  if (embedded !== null) {
    return embedded;
  }
  const response = await fetch(SAMPLE_REPORT_URL);
  return response.json();
}

const report = await loadReport();
compare = report.compare ?? null;
if (compare !== null) {
  document.getElementById("sheet").classList.add(CSS_CLASS_COMPARING);
}
renderTitleBlock(report);
renderTopology(report);
renderTopicStats(report);
renderNodeAnalysis(report);
renderChainLatency(report);
renderThroughputChart(report);
syncStickyHeaderOffsets();

// startOnLoad is disabled in favor of an awaited run() so edge-coloring happens only
// once the SVG paths exist to measure.
const mermaid = (await import(MERMAID_CDN_URL)).default;
mermaid.initialize({ startOnLoad: false });
await mermaid.run();
colorTopologyEdges();
