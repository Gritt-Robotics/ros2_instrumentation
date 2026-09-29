# trace_validation

A dummy ROS 2 pipeline that traces itself and records what it *should* have measured, so the
trace analyser's numbers can be checked against known-correct timings instead of taken on faith.

Nothing here runs on a robot. It exists to answer "is the analyser telling the truth?", and it
doubles as the fastest way to see the whole tracing workflow end to end — no robot, no bag, no
configuration.

## The three packages

| Package | Contents |
| --- | --- |
| `mock_trace_py` | Python publisher, subscriber, and two relay nodes; the launch file that wires them; `analyze_chain_latency`. |
| `mock_trace_cpp` | The same four nodes in C++, so a pipeline can mix languages at any step. |
| `trace_msgs` | `TraceMessage`, `TraceMessageFixed`, `TraceChainMessage` — the payloads the nodes exchange. |

## Quick start

Run in a ROS 2 environment with LTTng available. This launches publisher → C++ relay → subscriber, records LTTng
around it, shuts down after 30 seconds and analyses the result:

```bash
ros2 launch mock_trace_py trace_validation.launch.py \
  relay_langs:=cpp csv_dir:=/tmp/trace_demo
```

It writes one timestamped directory per run:

```
/tmp/trace_demo/trace-<timestamp>/
├── cpp_pub.csv, relay_0_cpp.csv, cpp_sub.csv     # ground truth, one row per message per node
├── ctf/                                          # the raw LTTng trace
└── trace-<timestamp>_report/
    └── trace_summary.json                        # the analyser's output
```

Render that JSON as HTML with `ros2 run trace_report generate_trace_report`.

## Cross-checking the analyser

This is the point of the package. Each node writes a CSV row per message with its own
wall-clock timestamps and the ids linking a message to the one that caused it.
`analyze_chain_latency` computes end-to-end and per-step latency **from those CSVs alone** — no
LTTng involved — so it is an independent answer to the same question the analyser answers from
tracepoints, and the reference to compare against when a traced number looks wrong.

Compare the latency distributions, not the counts. The two disagree at the capture boundaries by
design: the nodes log every message they handle, while a traced traversal only counts when every
tracepoint along the chain landed inside the session, so runs typically show fewer traced
traversals than ground-truth ones.

```bash
ros2 run mock_trace_py analyze_chain_latency \
  /tmp/trace_demo/trace-<timestamp>/cpp_pub.csv \
  /tmp/trace_demo/trace-<timestamp>/relay_0_cpp.csv \
  /tmp/trace_demo/trace-<timestamp>/cpp_sub.csv
```

Pass the step CSVs in pipeline order — publisher first, each relay in order, terminal subscriber
last. All steps must have run with `message_type:=chain` (which `relay_langs` selects
automatically), since that is the message carrying the chain ids.

## Launch options

| Argument | Default | What it changes |
| --- | --- | --- |
| `relay_langs` | `''` | Comma-separated `cpp`/`py`, one entry per relay step, e.g. `cpp,py` for two steps. Empty means a bare publisher/subscriber pair. |
| `relay_mode` | `direct` | `direct` republishes inside the receiving callback. `indirect` caches the message and republishes from a timer, which is the harder case for causal stitching. |
| `pub_lang`, `sub_lang` | `cpp` | Which language implements each endpoint. |
| `message_type` | `variable` | `variable` (size from `payload_size`), `fixed` (~4MB), or `chain`. Forced to `chain` when `relay_langs` is set. |
| `transport` | `typed` | `typed` uses normal pub/sub; `serialized` uses raw serialized messages. |
| `target_hz` | `10.0` | Publish rate. |
| `payload_size` | `4mb` | Payload as `<number><unit>` — `b`, `kb`, `mb`, max `256mb`. |
| `qos_depth` | `1` | Queue depth, for provoking drops. |
| `duration_s` | `30` | Auto-shutdown after this long. `0` runs until interrupted. |
| `csv_dir`, `session_name` | `trace` | Where outputs go and what the run directory is named. |
| `enable_tracing` | `true` | Set false to run the pipeline without tracing overhead. |
| `relay_publish_divisor` | `10` | With `relay_mode:=indirect`, how many times slower than `target_hz` the relay's publish timer runs. |

`ros2 launch mock_trace_py trace_validation.launch.py --show-args` lists these with full
descriptions.

## Running nodes by hand

Both packages install the same four executables, so you can wire a pipeline yourself instead of
using the launch file:

```
mock_publisher_node   mock_subscriber_node   mock_relay_node   mock_indirect_relay_node
```

`ros2 run mock_trace_py <node>` or `ros2 run mock_trace_cpp <node>`. Each takes its topics,
output CSV path, and message settings as ROS parameters; the launch file is the reference for
which parameters each one expects.

Run the tests with `pytest trace_validation` from the repository root, and the
C++ ones with `colcon test --packages-select mock_trace_cpp`.
