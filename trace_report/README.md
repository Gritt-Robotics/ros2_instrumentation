# trace_report

Turns a trace-analysis JSON into a single HTML report file. No assets alongside it — attach it
to a PR, drop it in a ticket, or hand it to someone else as-is. The data and styling are
embedded; the only thing it fetches when opened is the diagram library for section 2, so an
offline viewer gets every section except that one diagram.

## Usage

```bash
ros2 run trace_report generate_trace_report <analysis>.json --out report.html
```

The input is the JSON that a traced launch run writes on shutdown (`trace_summary.json`, inside
the run's `*_report/` directory), or whatever `analyze_trace` produced if you re-analysed a
captured trace by hand.

Compare two runs:

```bash
ros2 run trace_report generate_trace_report control.json \
  --compare modified.json --title "PR 1234" --out ab.html
```

| Option | Meaning |
| --- | --- |
| `--out` | Output HTML path. Required. |
| `--compare <json>` | Overlay a second run on the first. |
| `--title` | Report title. With `--compare` it names the comparison and is prefixed with "A/B Pipeline Trace Report: ". |
| `--significance-test` | `ttest` (Welch's, default) or `mann-whitney`. Only meaningful with `--compare`. |

## What the report contains

Four sections, in order:

1. **Topic stats** — per-topic publish/receive counts, drops, latency.
2. **Node analysis** — a topology diagram plus per-node callbacks, timers, and publishers.
3. **Chain latency** — for each topic chain you asked the analyser to measure, the end-to-end
   number and a per-step breakdown of where the time went.
4. **Throughput over time** — per-topic publish rate over the capture, when the analysis
   includes a throughput block.

## How A/B comparison behaves

Worth knowing before reading one, because it is deliberately asymmetric:

- **The first input is the control and fixes the structure** — the topology, the row set, the
  order, every label. The second contributes numbers only.
- **Rows are matched, not zipped.** A row present in the comparison but absent from the control
  is discarded and counted rather than rendered; a control row with no match still renders, with
  an em-dash where the second value would be. Both directions are reported so a structural
  difference between the two runs never silently disappears.
- **Significance is flagged** on a matched pair only when the p-value is below 0.05 *and* the
  medians differ by at least 10% *and* both sides have at least 5 samples. Anything short of all
  three is left unflagged rather than guessed at.
- Counts scale with capture length, so the title block always names both durations. Do not read
  a count difference as a regression without checking them.

## Two things that will bite you

- **The report needs JavaScript.** It renders itself from an embedded payload, so a viewer with
  JS disabled sees an empty page.
- **The topology diagram needs network access when the report is opened.** It loads Mermaid from
  a CDN at view time rather than embedding it, so on an offline machine every section still
  works but section 2's diagram stays blank.

## Layout

```
trace_report/
├── generate_report.py    # the CLI
├── bundle.py             # inlines the frontend + one payload into a single HTML file
├── compare.py            # all A/B row matching and significance testing
└── frontend/
    ├── index.html        # every static element: headings, table headers, column widths
    ├── report.css        # theme and table/chart styling
    ├── report.js         # fills in the tables, diagram, and chart from the payload
    └── sample_report.json  # a committed payload so index.html renders standalone
```

No Python here emits HTML — markup belongs in `index.html` or `report.js`. The row matching
lives in Python rather than JS because that is the part with real logic, and the only way to get
it under test.

To work on the frontend without regenerating a report, serve it over HTTP (`file://` blocks both
the ES module load and the payload `fetch`); `report.js` falls back to the committed sample when
no payload is embedded:

```bash
python3 -m http.server --bind 127.0.0.1 \
  -d trace_report/trace_report/frontend
```

Run the tests with `pytest trace_report` from the repository root. They cover the
payload, the bundler, the A/B matching, and the asset/markup contracts — but not `report.js`,
which has no JavaScript test runtime, so rendering changes need a browser check.
