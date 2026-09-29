# trace_instrumentation

Reusable ROS 2 LTTng tracing tooling: instrumentation helpers for firing tracepoints from
Python nodes, and the trace analyzer that turns a recorded session into per-node/per-chain
reports. `analyze_trace --help` is the reference for the analyser's flags; this file walks
through the indirect-link API, since its calling convention isn't obvious from the code
alone.

## Indirect Links (`linked_pub` / `linked_take`)

### The problem

`lttng_trace_methods` auto-times every registered subscription/timer/service callback as its
own zone, and a normal pub→sub step is enough to stitch those zones into a causal chain: node A
publishes on a topic, node B's subscription callback for that topic starts, done.

That breaks down when the publish that *causally* follows some input isn't the direct,
synchronous output of the callback that received it — for example:

- A **timer-driven aggregator**: a subscription callback only caches the latest message: the
  publish it eventually feeds happens later, from an unrelated timer callback.
- A **cross-callback relay**: the callback that publishes is triggered by a *different*
  subscription than the one whose message it's causally reporting on.

A single zone can't express "A caused B" across either gap. `linked_pub`/`linked_take` add an
explicit link so `trace_analysis` can still stitch the chain, surfaced as an `is_indirect` step.
Such a step's timing is reported as an
`indirect_wait_ms`/`relay_callback_time_ms`/`take_to_callback_start_ms` breakdown, splitting the
wait for the link from the relay callback's own work.

### The marker contract

- `linked_pub(node, logger_name, marker)` — call it where the triggering data is received, to
  **arm** the named `marker` with a fresh uid. Idempotent: if `marker` is already armed (no
  take has consumed it yet), the call no-ops, so a chattier source can't stack up multiple
  pending links on the same marker.
- `linked_take(node, logger_name, markers)` — call it immediately before the publish that
  causally continues the chain. Pops the uid currently held by each name in `markers`
  (order preserved) and disarms them. A marker that isn't armed is skipped rather than guessed
  at; if none of `markers` are armed, nothing is declared.
- `marker` is a plain string constant (define it once at module level, like a topic name), and
  state is scoped per node instance — two instances of the same node class never share arming.

### Example: timer-driven aggregator

A node whose timer callback builds and publishes a status message at a fixed rate, not directly
off any one subscription. To trace a status publish back to the sensor reading that fed it, the
sensor callback arms a marker the timer callback later takes:

```python
MARKER_SENSOR_INPUT = "sensor_input"

def sensor_callback(self, msg: SensorReading):
    self.latest_sensor_msg = msg
    linked_pub(self, LOGGER_NAME, MARKER_SENSOR_INPUT)
    ...

def status_timer_callback(self):
    status_msg = StatusReport()
    ...
    linked_take(self, LOGGER_NAME, [MARKER_SENSOR_INPUT])
    self.status_pub.publish(status_msg)
```

### Example: cross-callback relay

A node publishes its output from one subscription callback, but the work that output belongs
to was started earlier by a *different* subscription. Marking that transition lets the chain
trace from the triggering message through to the output it eventually produced:

```python
MARKER_STAGE_STARTED = "stage_started"

def trigger_callback(self, msg: TriggerMsg):
    ...
    linked_pub(self, LOGGER_NAME, MARKER_STAGE_STARTED)

def input_callback(self, msg: InputMsg):
    output_msg = build_output_msg(msg)
    linked_take(self, LOGGER_NAME, [MARKER_STAGE_STARTED])
    self.output_pub.publish(output_msg)
```

### Multiple markers per callback

A single callback can arm different markers under different conditions, and separate downstream
takes can each pick up the marker relevant to them — useful when one input feeds more than one
distinct output chain:

```python
MARKER_SENSOR_CONDITION_TRUE = "sensor_condition_true"
MARKER_SENSOR_CONDITION_FALSE = "sensor_condition_false"
MARKER_COMMAND = "command"

def sensor_callback(self, msg):
    self.latest_sensor_msg = msg
    if <some condition>:
        linked_pub(self, LOGGER_NAME, MARKER_SENSOR_CONDITION_TRUE)
    else:
        linked_pub(self, LOGGER_NAME, MARKER_SENSOR_CONDITION_FALSE)

def command_callback(self, msg):
    self.latest_command_msg = msg
    linked_pub(self, LOGGER_NAME, MARKER_COMMAND)

def timer_callback(self):
    status_msg = build_status_msg(...)
    linked_take(self, LOGGER_NAME, [MARKER_SENSOR_CONDITION_TRUE, MARKER_COMMAND])
    self.status_pub.publish(status_msg)

def mode_callback(self, msg):
    output_msg = build_output_msg(...)
    linked_take(self, LOGGER_NAME, [MARKER_SENSOR_CONDITION_FALSE, MARKER_COMMAND])
    self.output_pub.publish(output_msg)
```

Each subscription arms its own marker the moment a message arrives; whichever fires first just
sits armed until a take consumes it. `timer_callback`'s take names both markers it cares about,
so it picks up whichever are armed at that instant — one, both, or neither — without the
callbacks needing to coordinate on a shared uid or counter. This example yields four distinct
topic chains: sensor (true) → status, command → status, sensor (false) → output, command →
output.
