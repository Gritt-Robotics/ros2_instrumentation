# ros2_instrumentation

End-to-end LTTng tracing for ROS 2 pipelines: emit tracepoints, capture a session around a
launch file, reconstruct what actually happened, and render it as a report. The `ros2:*`
tracepoints themselves come from a vendored fork of `ros2_tracing`, tracked as a git submodule,
which adds message-link tracepoints and a Python agent domain on top of upstream.

The three packages ship together. Released under the Apache License 2.0; see `LICENSE`.

## The three packages

| Package | What it is |
| --- | --- |
| `trace_instrumentation` | The library. Helpers to fire tracepoints from Python nodes, a launch-file wrapper (`add_lttng_trace`) that records a session and analyses it on shutdown, and the CTF analyser that turns a raw trace into per-node and per-chain metrics. |
| `trace_report` | Turns one analysis JSON into a single HTML report file. Also does A/B: two traces side by side with significance flagging. |
| `trace_validation` | A dummy pub → relay → sub pipeline (C++ and Python nodes, `trace_msgs`) that traces itself and writes ground-truth CSVs, so the analyser's numbers can be checked against known timings. |

`trace_instrumentation` is the runtime library; the other two are optional tooling.

## Prerequisites

LTTng has to be present on the machine *before* anything here is built or run. None of it is
vendored — this repo declares the dependencies and expects them to already be satisfied.

**ROS 2 Jazzy.** The `ros2_tracing` fork tracks its `main` branch (Jazzy-based). This repo is only compatible with this branch of `ros2_tracing` as of now.

**LTTng**, in four pieces. Each is declared as a rosdep key in the `package.xml` that needs it,
and a missing one fails differently:

| Component | Declared as | Needed for |
| --- | --- | --- |
| LTTng-UST, with headers | `lttng-ust`, `liblttng-ust-dev` | the `ros2:*` tracepoints `rclcpp` and `tracetools` emit |
| Session daemon and CLI | `lttng-tools` | starting, stopping and flushing a recording session |
| LTTng control library headers | `liblttng-ctl-dev` | building `lttngpy`, which `tracetools_trace` drives the session through |
| LTTng Python agent | `python3-lttngust` | `lttng_python:event`, the tracepoints Python nodes fire |

**Babeltrace 2, with its Python bindings** (`python3-bt2`). The analyser reads the raw CTF trace
through `import bt2`. Without it a session still records; the analysis step is what fails.

**SciPy** (`python3-scipy`), for `trace_report` only, and only for A/B significance testing.

**The `ros2_tracing` submodule.** `tracetools`, `tracetools_trace`, `tracetools_launch` and
`lttngpy` come from the fork in `third_party/ros2_tracing` rather than a released ROS package, so
a clone without `--recurse-submodules` leaves that directory empty and nothing here builds:

```bash
git submodule update --init
```

**Where to obtain LTTng and Babeltrace, and what the packages are called on your platform, is
out of scope for this document.** They are separate upstream projects with their own packaging,
which differs across distributions and ROS installations; this repo neither installs nor vendors
them. The requirement is only that the dependencies above are satisfied before you build.

## Walkthrough

The validation harness is the quickest way to see the whole flow — it needs no robot, no bag,
and no configuration. Run everything in a ROS 2 environment with LTTng available.

**1. Capture a trace.** This launches a 3-node chain, records LTTng around it, shuts down after
60 seconds and analyses the result:

```bash
ros2 launch mock_trace_py trace_validation.launch.py \
  relay_langs:=cpp duration_s:=60 csv_dir:=/tmp/trace_demo
```

**2. Look at what it produced.**

```
/tmp/trace_demo/trace-<timestamp>/
├── cpp_pub.csv, relay_0_cpp.csv, cpp_sub.csv     # ground truth, written by the nodes
├── ctf/                                          # the raw LTTng trace
└── trace-<timestamp>_report/
    └── trace_summary.json                        # the analysis
```

`trace_summary.json` is the analysis: per-node callbacks, per-topic latency and frequency, queue
health, and — because this run had a relay step — an end-to-end `pipeline_chain_latency` block.

**3. Render it.**

```bash
ros2 run trace_report generate_trace_report \
  /tmp/trace_demo/trace-<timestamp>/trace-<timestamp>_report/trace_summary.json \
  --out /tmp/trace_demo/report.html
```

Open `report.html` in a browser. It is one file with the data and styling embedded, so it can be
attached to a PR or handed to someone else as-is. It needs JavaScript, and the topology diagram
pulls its rendering library when opened — offline, every section works except that diagram.

`analyze_trace` (from `trace_instrumentation`) re-runs step 2's analysis over a `ctf/` directory
by hand, which is what you want when tweaking flags against an already-captured trace rather
than re-running the pipeline.

## Tracing your own pipeline

Two steps, and only the second is required:

1. **Instrument the nodes you care about.** Decorate a node class with
   `@lttng_trace_methods(<logger_name>)` and every subscription, timer and service callback it
   registers is timed automatically. Python only — C++ nodes are already instrumented by
   `rclcpp`. Causal chains that cross callback boundaries (a timer publishing what a
   subscription cached) need explicit `linked_pub`/`linked_take` markers; see
   `trace_instrumentation/README.md`.

2. **Wrap your launch entities.** Pass them through `add_lttng_trace(...)` with a session name,
   an output directory, and the topic chains you want measured. The worked example is
   `trace_validation/mock_trace_py/launch/trace_validation.launch.py`, which traces the
   validation harness end to end.

Name the executables you want recorded via `trace_processes` — an LTTng event rule is host-wide,
so an unfiltered session records every ROS process on the machine.

## Further reading

Each package has its own README:

- `trace_instrumentation/README.md` — the `linked_pub`/`linked_take` calling convention for
  chains that cross callback boundaries, with worked examples.
- `trace_report/README.md` — report sections, A/B comparison rules, and the frontend layout.
- `trace_validation/README.md` — launch options and how to cross-check the analyser against
  ground truth.

`analyze_trace --help` and
`ros2 launch mock_trace_py trace_validation.launch.py --show-args` are the authoritative
reference for the analyser's flags and the harness's options.
