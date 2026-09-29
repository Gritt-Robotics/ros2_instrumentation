# Copyright 2026 Gritt Robotics Inc.

import pytest
from synthetic_events import (
    make_event,
    publish_events,
    publisher_init_event,
    python_zone_event,
    rmw_take_event,
    subscription_init_event,
    zone_declaration_event,
)
from trace_instrumentation.trace_analysis.analysis import TraceAnalysis
from trace_instrumentation.trace_analysis.ctf.reader import (
    Event,
    Metadata,
)
from trace_instrumentation.trace_analysis.event_names import EVT_RCL_NODE_INIT
from trace_instrumentation.trace_analysis.flow_graph import FlowGraph
from trace_instrumentation.trace_analysis.graph import (
    CB_KIND_TIMER,
    CHAIN_TERMINATOR_PUB,
    CHAIN_TERMINATOR_TAKE,
)
from trace_instrumentation.trace_analysis.metrics.latency import (
    INSTANTANEOUS_WAIT_THRESHOLD_MS,
)
from trace_instrumentation.trace_analysis.reports.report import (
    build_node_report,
)
from trace_instrumentation.trace_analysis.reports.schema import (
    ENDPOINT_KIND_CALLBACK_END,
    ENDPOINT_KIND_CALLBACK_START,
    ENDPOINT_KIND_LINK,
    ENDPOINT_KIND_PUBLISH,
    ENDPOINT_KIND_TAKE,
)

# Zone-declaration payload, as the instrumentation emits it at registration time. An
# undeclared zone stays CB_KIND_ZONE, so a Python subscription callback must declare it.
DECL_SUB_PAYLOAD = "trace_decl sub zone={zone_name} topic={topic}"
DECL_TIMER_PAYLOAD = "trace_decl timer zone={zone_name} period_ns={period_ns}"
PUB_LINK_PAYLOAD = "trace_link pub uid={uid}"
TAKE_LINKS_PAYLOAD = "trace_link take uids={uids}"

PRODUCER_VPID = 200
PRODUCER_VTID = 20
PRODUCER_NODE_HANDLE = 900

RELAY_VPID = 100
RELAY_VTID = 10
RELAY_NODE_HANDLE = 910

SINK_VPID = 300
SINK_VTID = 30
SINK_NODE_HANDLE = 920

BYSTANDER_VPID = 400
BYSTANDER_VTID = 40
BYSTANDER_NODE_HANDLE = 960

OBSERVER_VPID = 500
OBSERVER_VTID = 50
OBSERVER_NODE_HANDLE = 970

STEP0_TOPIC = "/step0"
STEP1_TOPIC = "/step1"
# Off-chain fixtures for the --only-topic-chains tests: a topic only the bystander
# touches, and one carried between two nodes the chain does walk.
NOISE_TOPIC = "/noise"
SIDE_CHANNEL_TOPIC = "/side_channel"
# A topic no fixture ever registers, for the chain-names-nothing-in-the-trace case.
ABSENT_TOPIC = "/absent"
ON_MESSAGE_ZONE = "_on_message"
UNTRACED_LABEL = "<untraced>"
N_TRAVERSALS = 3

# Infrastructure topic/node fixtures for the report-filtering tests.
TF_TOPIC = "/tf"
PLOT_TOPIC = "/plot_out"
INFRA_NODE_NAME = "viz_helper_node_deadbeef"
TF_SINK_VPID, TF_SINK_VTID, TF_SINK_NODE_HANDLE = 600, 60, 940
PLOT_VPID, PLOT_VTID, PLOT_NODE_HANDLE = 700, 70, 950


def _node_init_event(ts_ns, vpid, vtid, node_handle, name):
    return make_event(
        EVT_RCL_NODE_INIT,
        ts_ns,
        vpid,
        vtid,
        {
            "node_handle": node_handle,
            "node_name": name,
            "namespace": "/",
            "rmw_handle": 0,
        },
    )


def _three_step_relay_events(sink_zone: bool = False) -> list["Event"]:
    """A producer -> relay (Python) -> sink pipeline, replayed N_TRAVERSALS times,
    with all three nodes registered via rcl_node_init.

    Args:
        sink_zone (bool): If True, also wrap the sink's take in an `_on_message`
            zone so it gets its own completed `_CB` (callback_time sample).
            Defaults to False.

    Returns:
        list[Event]: The synthetic event sequence.
    """
    events = [
        _node_init_event(
            0, PRODUCER_VPID, PRODUCER_VTID, PRODUCER_NODE_HANDLE, "producer"
        ),
        _node_init_event(0, RELAY_VPID, RELAY_VTID, RELAY_NODE_HANDLE, "relay"),
        _node_init_event(0, SINK_VPID, SINK_VTID, SINK_NODE_HANDLE, "sink"),
        publisher_init_event(
            0, PRODUCER_VPID, PRODUCER_VTID, 1, PRODUCER_NODE_HANDLE, 901, STEP0_TOPIC
        ),
        subscription_init_event(
            0, RELAY_VPID, RELAY_VTID, 10, RELAY_NODE_HANDLE, 11, STEP0_TOPIC
        ),
        zone_declaration_event(
            0,
            RELAY_VPID,
            RELAY_VTID,
            DECL_SUB_PAYLOAD.format(zone_name=ON_MESSAGE_ZONE, topic=STEP0_TOPIC),
        ),
        publisher_init_event(
            0, RELAY_VPID, RELAY_VTID, 2, RELAY_NODE_HANDLE, 12, STEP1_TOPIC
        ),
        subscription_init_event(
            0, SINK_VPID, SINK_VTID, 20, SINK_NODE_HANDLE, 21, STEP1_TOPIC
        ),
    ]
    if sink_zone:
        events.append(
            zone_declaration_event(
                0,
                SINK_VPID,
                SINK_VTID,
                DECL_SUB_PAYLOAD.format(zone_name=ON_MESSAGE_ZONE, topic=STEP1_TOPIC),
            )
        )
    for i in range(N_TRAVERSALS):
        base = 1000 + i * 5000
        events += publish_events(
            base, PRODUCER_VPID, PRODUCER_VTID, 100 + i, 1, source_timestamp=5000 + i
        )
        events.append(
            rmw_take_event(base + 1000, RELAY_VPID, RELAY_VTID, 200 + i, 11, 5000 + i)
        )
        events.append(
            python_zone_event(
                base + 1100, RELAY_VPID, RELAY_VTID, ON_MESSAGE_ZONE, "begin"
            )
        )
        events += publish_events(
            base + 1200, RELAY_VPID, RELAY_VTID, 300 + i, 2, source_timestamp=6000 + i
        )
        events.append(
            python_zone_event(
                base + 1400, RELAY_VPID, RELAY_VTID, ON_MESSAGE_ZONE, "end"
            )
        )
        events.append(
            rmw_take_event(base + 3000, SINK_VPID, SINK_VTID, 400 + i, 21, 6000 + i)
        )
        if sink_zone:
            events.append(
                python_zone_event(
                    base + 3100, SINK_VPID, SINK_VTID, ON_MESSAGE_ZONE, "begin"
                )
            )
            events.append(
                python_zone_event(
                    base + 3400, SINK_VPID, SINK_VTID, ON_MESSAGE_ZONE, "end"
                )
            )
    return events


def _build(events):
    meta = Metadata()
    analysis = TraceAnalysis(meta, events).build()
    flow = FlowGraph(meta, events, analysis).build()
    return analysis, flow


def test_node_adjacency_uses_node_topic_pairs():
    analysis, flow = _build(_three_step_relay_events())

    report = build_node_report(flow, analysis)

    assert report["nodes"]["/producer"]["to"] == [["/relay", STEP0_TOPIC]]
    assert report["nodes"]["/producer"]["from"] == []
    assert report["nodes"]["/relay"]["to"] == [["/sink", STEP1_TOPIC]]
    assert report["nodes"]["/relay"]["from"] == [["/producer", STEP0_TOPIC]]
    assert report["nodes"]["/sink"]["to"] == []
    assert report["nodes"]["/sink"]["from"] == [["/relay", STEP1_TOPIC]]


def test_node_adjacency_excludes_internal_topics_so_shared_param_events_dont_connect_all():
    # Every node also pub+subs /parameter_events (like every real rclcpp node). Without
    # the internal-topic exclusion this alone fully connects the graph.
    events = _three_step_relay_events()
    param_nodes = {
        50: (PRODUCER_VPID, PRODUCER_VTID, PRODUCER_NODE_HANDLE),
        51: (RELAY_VPID, RELAY_VTID, RELAY_NODE_HANDLE),
        52: (SINK_VPID, SINK_VTID, SINK_NODE_HANDLE),
    }
    for handle, (vpid, vtid, node_handle) in param_nodes.items():
        events.append(
            publisher_init_event(
                0, vpid, vtid, handle, node_handle, handle + 500, "/parameter_events"
            )
        )
        events.append(
            subscription_init_event(
                0,
                vpid,
                vtid,
                handle + 100,
                node_handle,
                handle + 600,
                "/parameter_events",
            )
        )
    analysis, flow = _build(events)

    report = build_node_report(flow, analysis)

    # Each node still has exactly its real chain neighbor, not all three others.
    assert report["nodes"]["/producer"]["to"] == [["/relay", STEP0_TOPIC]]
    assert report["nodes"]["/relay"]["from"] == [["/producer", STEP0_TOPIC]]
    for node in ("/producer", "/relay", "/sink"):
        for pair in report["nodes"][node]["to"] + report["nodes"][node]["from"]:
            assert pair[1] != "/parameter_events"


def test_subscription_callback_row_has_hz_and_topic_latency():
    events = _three_step_relay_events()
    analysis, flow = _build(events)

    report = build_node_report(flow, analysis)

    row = report["nodes"]["/relay"]["callbacks"][f"{ON_MESSAGE_ZONE}@{STEP0_TOPIC}"]
    assert row["callback_type"] == "subscription"
    assert row["topic"] == STEP0_TOPIC
    assert row["timer_period_ns"] is None
    assert "invocation_count" not in row
    assert "total_published" not in row
    assert "delivery_ratio" not in row
    assert "network_latency_ms" not in row
    assert row["duration_ms"]["count"] == N_TRAVERSALS
    assert row["topic_latency_ms"]["count"] == N_TRAVERSALS
    assert row["callback_hz"] == N_TRAVERSALS / report["duration_s"]


def test_two_distinct_callbacks_on_same_node_kind_topic_are_not_merged():
    # Callback-row keys must include the handler label, not just (node, kind, topic),
    # or distinct handlers sharing that tuple get merged under one label.
    OTHER_ZONE = "_on_other_message"
    events = _three_step_relay_events()
    events.append(
        subscription_init_event(
            0, RELAY_VPID, RELAY_VTID, 13, RELAY_NODE_HANDLE, 14, STEP0_TOPIC
        )
    )
    events.append(
        zone_declaration_event(
            0,
            RELAY_VPID,
            RELAY_VTID,
            DECL_SUB_PAYLOAD.format(zone_name=OTHER_ZONE, topic=STEP0_TOPIC),
        )
    )
    events.append(rmw_take_event(90_000, RELAY_VPID, RELAY_VTID, 8888, 14, 88_000))
    events.append(
        python_zone_event(90_100, RELAY_VPID, RELAY_VTID, OTHER_ZONE, "begin")
    )
    events.append(python_zone_event(90_400, RELAY_VPID, RELAY_VTID, OTHER_ZONE, "end"))
    analysis, flow = _build(events)

    report = build_node_report(flow, analysis)
    callbacks = report["nodes"]["/relay"]["callbacks"]

    original_row = callbacks[f"{ON_MESSAGE_ZONE}@{STEP0_TOPIC}"]
    other_row = callbacks[f"{OTHER_ZONE}@{STEP0_TOPIC}"]
    assert original_row is not other_row
    assert original_row["duration_ms"]["count"] == N_TRAVERSALS
    assert other_row["duration_ms"]["count"] == 1


def test_duration_ms_includes_p90():
    events = _three_step_relay_events()
    analysis, flow = _build(events)

    report = build_node_report(flow, analysis)

    row = report["nodes"]["/relay"]["callbacks"][f"{ON_MESSAGE_ZONE}@{STEP0_TOPIC}"]
    duration = row["duration_ms"]
    assert "p90" in duration
    assert duration["median"] <= duration["p90"] <= duration["p99"]


def test_callback_hz_counts_starts_not_completed_callbacks():
    # A callback that starts but whose end event is clipped by the trace window still
    # counts toward callback_hz (a start), but not toward duration_ms (needs an end).
    events = _three_step_relay_events()
    events.append(rmw_take_event(70_000, RELAY_VPID, RELAY_VTID, 7777, 11, 77_000))
    events.append(
        python_zone_event(70_100, RELAY_VPID, RELAY_VTID, ON_MESSAGE_ZONE, "begin")
    )
    # no matching "end" -- callback started, never completed
    analysis, flow = _build(events)

    report = build_node_report(flow, analysis)
    row = report["nodes"]["/relay"]["callbacks"][f"{ON_MESSAGE_ZONE}@{STEP0_TOPIC}"]

    assert row["duration_ms"]["count"] == N_TRAVERSALS
    assert row["callback_hz"] == (N_TRAVERSALS + 1) / report["duration_s"]


def test_publisher_row_has_hz_first_and_callback_start_to_publish():
    events = _three_step_relay_events()
    analysis, flow = _build(events)

    report = build_node_report(flow, analysis)

    row = report["nodes"]["/relay"]["publishers"][STEP1_TOPIC]
    assert list(row.keys())[:3] == ["topic", "publisher_hz", "topic_total_hz"]
    assert "invocation_count" not in row
    assert "total_invocation_count" not in row
    assert "processing_ms_topic_overall" not in row
    assert "processing_ms_handler" not in row
    assert row["topic"] == STEP1_TOPIC
    assert row["publisher_hz"] == N_TRAVERSALS / report["duration_s"]
    assert row["topic_total_hz"] == row["publisher_hz"]
    assert row["callback_start_to_publish_ms"]["count"] == N_TRAVERSALS


def test_callback_row_has_interarrival_and_inter_callback_gap():
    events = _three_step_relay_events()
    analysis, flow = _build(events)

    report = build_node_report(flow, analysis)

    row = report["nodes"]["/relay"]["callbacks"][f"{ON_MESSAGE_ZONE}@{STEP0_TOPIC}"]
    assert row["interarrival_ms"]["count"] == N_TRAVERSALS - 1
    assert row["inter_callback_gap_ms"]["count"] == N_TRAVERSALS - 1
    assert row["instantaneous_wait_count"] == N_TRAVERSALS - 1
    assert row["instantaneous_wait_threshold_ms"] == INSTANTANEOUS_WAIT_THRESHOLD_MS


def test_publisher_row_has_dropped_count_and_pct():
    events = _three_step_relay_events()
    # One extra producer publish on STEP0_TOPIC with no matching take -- a genuine drop.
    events += publish_events(
        50_000, PRODUCER_VPID, PRODUCER_VTID, 999, 1, source_timestamp=9999
    )
    analysis, flow = _build(events)

    report = build_node_report(flow, analysis)

    row = report["nodes"]["/producer"]["publishers"][STEP0_TOPIC]
    assert row["dropped"]["count"] == 1
    assert row["dropped"]["pct"] == 25.0


def test_publisher_row_received_is_matched_takes_and_dropped_is_pub_minus_rcv():
    events = _three_step_relay_events()
    # One extra producer publish on STEP0_TOPIC with no matching take -- a genuine drop.
    events += publish_events(
        50_000, PRODUCER_VPID, PRODUCER_VTID, 999, 1, source_timestamp=9999
    )
    analysis, flow = _build(events)

    report = build_node_report(flow, analysis)

    row = report["nodes"]["/producer"]["publishers"][STEP0_TOPIC]
    # Rcv counts publishes matched to a take, never callback executions, so it can
    # never exceed Pub, and dropped is exactly the difference.
    assert row["published_count"] == N_TRAVERSALS + 1
    assert row["received_count"] == N_TRAVERSALS
    assert row["received_count"] <= row["published_count"]
    assert row["dropped"]["count"] == row["published_count"] - row["received_count"]


def test_publisher_row_with_no_drops_has_received_equal_published():
    events = _three_step_relay_events()
    analysis, flow = _build(events)

    report = build_node_report(flow, analysis)

    row = report["nodes"]["/relay"]["publishers"][STEP1_TOPIC]
    assert row["published_count"] == N_TRAVERSALS
    assert row["received_count"] == N_TRAVERSALS
    assert row["dropped"]["count"] == 0
    assert row["dropped"]["pct"] == 0.0


def test_relay_callback_and_publisher_hz_agree_on_full_trace_duration():
    events = _three_step_relay_events()
    analysis, flow = _build(events)

    report = build_node_report(flow, analysis)

    cb = report["nodes"]["/relay"]["callbacks"][f"{ON_MESSAGE_ZONE}@{STEP0_TOPIC}"]
    pub = report["nodes"]["/relay"]["publishers"][STEP1_TOPIC]
    assert cb["callback_hz"] == N_TRAVERSALS / report["duration_s"]
    assert pub["publisher_hz"] == cb["callback_hz"]


def test_untraced_publisher_row_has_no_processing_time():
    events = _three_step_relay_events()
    analysis, flow = _build(events)

    report = build_node_report(flow, analysis)

    row = report["nodes"]["/producer"]["publishers"][STEP0_TOPIC]
    assert "invocation_count" not in row
    assert row["publisher_hz"] > 0
    assert row["callback_start_to_publish_ms"] is None


def test_timer_publisher_callback_start_to_publish_uses_timer_start():
    # A timer callback (no triggering take) that publishes: processing must be measured
    # from the timer callback's own start, not left null for lack of a trigger take.
    TIMER_VPID, TIMER_VTID, TIMER_NODE, TIMER_HANDLE = 400, 40, 930, 5
    TIMER_TOPIC = "/timer_out"
    events = [
        _node_init_event(0, TIMER_VPID, TIMER_VTID, TIMER_NODE, "ticker"),
        publisher_init_event(
            0, TIMER_VPID, TIMER_VTID, TIMER_HANDLE, TIMER_NODE, 55, TIMER_TOPIC
        ),
    ]
    for i in range(N_TRAVERSALS):
        base = 1000 + i * 5000
        events.append(python_zone_event(base, TIMER_VPID, TIMER_VTID, "_tick", "begin"))
        events += publish_events(
            base + 100,
            TIMER_VPID,
            TIMER_VTID,
            600 + i,
            TIMER_HANDLE,
            source_timestamp=6000 + i,
        )
        events.append(
            python_zone_event(base + 300, TIMER_VPID, TIMER_VTID, "_tick", "end")
        )
    analysis, flow = _build(events)

    report = build_node_report(flow, analysis)
    row = report["nodes"]["/ticker"]["publishers"][TIMER_TOPIC]

    assert row["callback_start_to_publish_ms"]["count"] == N_TRAVERSALS
    # publish is 100ns after the zone begin -> ~0.0001ms, definitely sub-millisecond
    assert row["callback_start_to_publish_ms"]["mean"] < 1.0


def test_ignore_node_glob_excludes_matching_node_and_its_edges():
    analysis, flow = _build(_three_step_relay_events())

    report = build_node_report(flow, analysis, ignore_nodes=["/sink"])

    assert "/sink" not in report["nodes"]
    # /relay no longer lists /sink as a downstream neighbor.
    assert report["nodes"]["/relay"]["to"] == []


def _off_chain_events() -> list["Event"]:
    """The three-step relay plus two things the chain never walks: a /bystander node
    on its own topic, and a /side_channel edge between two chain participants.

    Returns:
        list[Event]: The synthetic event sequence.
    """
    events = _three_step_relay_events(sink_zone=True)
    events += [
        _node_init_event(
            0, BYSTANDER_VPID, BYSTANDER_VTID, BYSTANDER_NODE_HANDLE, "bystander"
        ),
        publisher_init_event(
            0,
            BYSTANDER_VPID,
            BYSTANDER_VTID,
            3,
            BYSTANDER_NODE_HANDLE,
            931,
            NOISE_TOPIC,
        ),
        # Producer and sink both survive the filter on their chain endpoints, so this
        # edge is only droppable by filtering on the topic.
        publisher_init_event(
            0,
            PRODUCER_VPID,
            PRODUCER_VTID,
            4,
            PRODUCER_NODE_HANDLE,
            941,
            SIDE_CHANNEL_TOPIC,
        ),
        subscription_init_event(
            0, SINK_VPID, SINK_VTID, 40, SINK_NODE_HANDLE, 41, SIDE_CHANNEL_TOPIC
        ),
    ]
    for i in range(N_TRAVERSALS):
        base = 2000 + i * 5000
        events += publish_events(
            base, BYSTANDER_VPID, BYSTANDER_VTID, 500 + i, 3, source_timestamp=7000 + i
        )
        events += publish_events(
            base, PRODUCER_VPID, PRODUCER_VTID, 600 + i, 4, source_timestamp=8000 + i
        )
        events.append(
            rmw_take_event(base + 500, SINK_VPID, SINK_VTID, 700 + i, 41, 8000 + i)
        )
    return events


def test_only_topic_chains_drops_nodes_with_no_endpoint_on_a_chain_topic():
    analysis, flow = _build(_off_chain_events())

    report = build_node_report(
        flow,
        analysis,
        topic_chains=[[STEP0_TOPIC, STEP1_TOPIC]],
        only_topic_chains=True,
    )

    assert sorted(report["nodes"]) == ["/producer", "/relay", "/sink"]
    assert report["only_topic_chains"] is True


def test_only_topic_chains_drops_an_off_chain_topic_between_two_kept_nodes():
    analysis, flow = _build(_off_chain_events())

    report = build_node_report(
        flow,
        analysis,
        topic_chains=[[STEP0_TOPIC, STEP1_TOPIC]],
        only_topic_chains=True,
    )

    # Both endpoints of /side_channel survive as nodes, so only the topic filter can
    # keep this edge and its publisher row out.
    assert report["nodes"]["/producer"]["to"] == [["/relay", STEP0_TOPIC]]
    assert report["nodes"]["/sink"]["from"] == [["/relay", STEP1_TOPIC]]
    assert sorted(report["nodes"]["/producer"]["publishers"]) == [STEP0_TOPIC]


def test_only_topic_chains_keeps_off_chain_topics_when_not_requested():
    analysis, flow = _build(_off_chain_events())

    report = build_node_report(
        flow, analysis, topic_chains=[[STEP0_TOPIC, STEP1_TOPIC]]
    )

    assert "/bystander" in report["nodes"]
    assert sorted(topic for _node, topic in report["nodes"]["/producer"]["to"]) == [
        SIDE_CHANNEL_TOPIC,
        STEP0_TOPIC,
    ]
    assert report["only_topic_chains"] is False


def test_only_topic_chains_strips_node_pin_before_matching_topics():
    analysis, flow = _build(_off_chain_events())

    report = build_node_report(
        flow,
        analysis,
        topic_chains=[[STEP0_TOPIC, f"{STEP1_TOPIC}@/sink"]],
        only_topic_chains=True,
    )

    assert sorted(report["nodes"]) == ["/producer", "/relay", "/sink"]
    assert report["nodes"]["/relay"]["to"] == [["/sink", STEP1_TOPIC]]


def test_only_topic_chains_restricts_throughput_series_and_resums_total():
    analysis, flow = _build(_off_chain_events())

    report = build_node_report(
        flow,
        analysis,
        topic_chains=[[STEP0_TOPIC, STEP1_TOPIC]],
        only_topic_chains=True,
    )

    throughput = report["throughput"]
    assert sorted(throughput["per_topic_hz"]) == [STEP0_TOPIC, STEP1_TOPIC]
    # total_hz must describe the series left beside it, not every topic in the trace.
    for i, total in enumerate(throughput["total_hz"]):
        assert total == sum(series[i] for series in throughput["per_topic_hz"].values())


def _silent_subscriber_events() -> list["Event"]:
    """The three-step relay plus a node that registers on a chain topic and never takes.

    Returns:
        list[Event]: The synthetic event sequence.
    """
    events = _three_step_relay_events()
    events += [
        _node_init_event(
            0, OBSERVER_VPID, OBSERVER_VTID, OBSERVER_NODE_HANDLE, "observer"
        ),
        subscription_init_event(
            0, OBSERVER_VPID, OBSERVER_VTID, 30, OBSERVER_NODE_HANDLE, 31, STEP1_TOPIC
        ),
    ]
    return events


def test_only_topic_chains_keeps_a_subscriber_that_never_received_a_message():
    analysis, flow = _build(_silent_subscriber_events())

    report = build_node_report(
        flow,
        analysis,
        topic_chains=[[STEP0_TOPIC, STEP1_TOPIC]],
        only_topic_chains=True,
    )

    # Membership is endpoint registration, not observed traffic: a node the chain never
    # reached is exactly the one a failed traversal needs the report to show.
    assert "/observer" in report["nodes"]
    assert report["nodes"]["/observer"]["from"] == [["/relay", STEP1_TOPIC]]
    assert len(report["nodes"]["/observer"]["callbacks"]) == 0


def test_only_topic_chains_empties_the_report_when_no_chain_topic_is_in_the_trace():
    analysis, flow = _build(_off_chain_events())

    report = build_node_report(
        flow, analysis, topic_chains=[[ABSENT_TOPIC]], only_topic_chains=True
    )

    assert len(report["nodes"]) == 0
    assert len(report["throughput"]["per_topic_hz"]) == 0
    # total_hz still describes the (now empty) series beside it, not the whole trace.
    throughput = report["throughput"]
    assert throughput["total_hz"] == [0.0] * len(throughput["bucket_starts_s"])


def test_only_topic_chains_without_any_chain_raises():
    analysis, flow = _build(_off_chain_events())

    with pytest.raises(ValueError):
        build_node_report(flow, analysis, only_topic_chains=True)


def test_only_topic_chains_with_an_empty_chain_list_raises():
    analysis, flow = _build(_off_chain_events())

    with pytest.raises(ValueError):
        build_node_report(flow, analysis, topic_chains=[], only_topic_chains=True)


def test_pipeline_chain_latency_defaults_to_empty_list():
    # build_node_report() has no topic_chains param; the key must still default to [].
    events = _three_step_relay_events()
    analysis, flow = _build(events)

    report = build_node_report(flow, analysis)

    assert report["pipeline_chain_latency"] == []


def test_pipeline_chain_latency_computes_direct_chain_stats():
    events = _three_step_relay_events(sink_zone=True)
    analysis, flow = _build(events)

    report = build_node_report(
        flow, analysis, topic_chains=[[STEP0_TOPIC, STEP1_TOPIC]]
    )

    assert len(report["pipeline_chain_latency"]) == 1
    chain = report["pipeline_chain_latency"][0]
    assert chain["topic_sequence"] == [
        ["/producer", STEP0_TOPIC],
        ["/relay", STEP1_TOPIC],
    ]
    assert chain["traversal_count"] == N_TRAVERSALS
    assert chain["terminator"] == CHAIN_TERMINATOR_TAKE
    assert chain["chain_latency_ms"]["count"] == N_TRAVERSALS

    step0, step1 = chain["steps"]
    assert step0["subscribing_node"] == "/relay"
    assert step0["topic"] == STEP0_TOPIC
    assert step0["topic_hz"] == N_TRAVERSALS / report["duration_s"]
    assert step0["callback_hz"] == N_TRAVERSALS / report["duration_s"]
    assert step0["is_indirect"] is False
    assert step0["topic_latency_ms"]["count"] == N_TRAVERSALS
    # The relay's zone begins 100 ns after its rmw_take in every traversal.
    assert step0["take_to_callback_start_ms"]["count"] == N_TRAVERSALS
    assert step0["take_to_callback_start_ms"]["mean"] == 100 / 1_000_000
    assert step0["callback_time_ms"]["count"] == N_TRAVERSALS
    assert step0["indirect_wait_ms"] is None
    assert step0["relay_callback_time_ms"] is None

    assert step1["subscribing_node"] == "/sink"
    assert step1["topic"] == STEP1_TOPIC
    assert step1["is_indirect"] is False
    assert step1["topic_latency_ms"]["count"] == N_TRAVERSALS
    # The sink is the chain's terminus under the take terminator, and its own
    # take-to-callback-start gap is still reported here.
    assert step1["take_to_callback_start_ms"]["count"] == N_TRAVERSALS
    assert step1["take_to_callback_start_ms"]["mean"] == 100 / 1_000_000
    assert step1["callback_time_ms"]["count"] == N_TRAVERSALS
    assert step1["indirect_wait_ms"] is None
    assert step1["relay_callback_time_ms"] is None


def test_pipeline_chain_latency_direct_chain_step_endpoints():
    events = _three_step_relay_events(sink_zone=True)
    analysis, flow = _build(events)

    report = build_node_report(
        flow, analysis, topic_chains=[[STEP0_TOPIC, STEP1_TOPIC]]
    )

    chain = report["pipeline_chain_latency"][0]
    step0, step1 = chain["steps"]
    rows = chain["step_endpoints"]
    kinds = [(row["from"]["kind"], row["to"]["kind"]) for row in rows]
    assert kinds == [
        (ENDPOINT_KIND_PUBLISH, ENDPOINT_KIND_TAKE),
        (ENDPOINT_KIND_TAKE, ENDPOINT_KIND_CALLBACK_START),
        (ENDPOINT_KIND_CALLBACK_START, ENDPOINT_KIND_PUBLISH),
        (ENDPOINT_KIND_PUBLISH, ENDPOINT_KIND_TAKE),
        (ENDPOINT_KIND_TAKE, ENDPOINT_KIND_CALLBACK_START),
        (ENDPOINT_KIND_CALLBACK_START, ENDPOINT_KIND_CALLBACK_END),
    ]
    # The sink's take-terminated last row has no handoff, so it ends the sequence.
    assert rows[-1]["to"]["kind"] == ENDPOINT_KIND_CALLBACK_END
    assert rows[0]["metric_ms"] == step0["topic_latency_ms"]
    assert rows[1]["metric_ms"] == step0["take_to_callback_start_ms"]
    assert rows[2]["metric_ms"] == step0["callback_time_ms"]
    assert rows[3]["metric_ms"] == step1["topic_latency_ms"]
    assert rows[5]["metric_ms"] == step1["callback_time_ms"]
    assert rows[5]["to"]["node"] == "/sink"


def test_pipeline_chain_latency_reports_a_publish_terminated_chain():
    events = _three_step_relay_events(sink_zone=True)
    analysis, flow = _build(events)

    report = build_node_report(
        flow, analysis, topic_chains=[[STEP0_TOPIC, STEP1_TOPIC, CHAIN_TERMINATOR_PUB]]
    )

    chain = report["pipeline_chain_latency"][0]
    assert chain["terminator"] == CHAIN_TERMINATOR_PUB
    assert chain["traversal_count"] == N_TRAVERSALS
    # The relay republishes 1200 ns after the origin publish in every traversal.
    assert chain["chain_latency_ms"]["count"] == N_TRAVERSALS
    assert chain["chain_latency_ms"]["mean"] == 1200 / 1_000_000

    step0, step1 = chain["steps"]
    assert step0["subscribing_node"] == "/relay"
    assert step0["take_to_callback_start_ms"]["count"] == N_TRAVERSALS
    assert step0["callback_time_ms"]["count"] == N_TRAVERSALS
    # Nothing past the wire is measured, even though /sink did take it in this trace.
    assert step1["publishing_node"] == "/relay"
    assert step1["subscribing_node"] is None
    assert step1["topic"] == STEP1_TOPIC
    assert step1["callback_hz"] == 0.0
    assert step1["topic_latency_ms"] is None
    assert step1["take_to_callback_start_ms"] is None
    assert step1["callback_time_ms"] is None


def test_pipeline_chain_latency_publish_terminated_step_endpoints():
    events = _three_step_relay_events(sink_zone=True)
    analysis, flow = _build(events)

    report = build_node_report(
        flow, analysis, topic_chains=[[STEP0_TOPIC, STEP1_TOPIC, CHAIN_TERMINATOR_PUB]]
    )

    chain = report["pipeline_chain_latency"][0]
    step0 = chain["steps"][0]
    rows = chain["step_endpoints"]
    kinds = [(row["from"]["kind"], row["to"]["kind"]) for row in rows]
    assert kinds == [
        (ENDPOINT_KIND_PUBLISH, ENDPOINT_KIND_TAKE),
        (ENDPOINT_KIND_TAKE, ENDPOINT_KIND_CALLBACK_START),
        (ENDPOINT_KIND_CALLBACK_START, ENDPOINT_KIND_PUBLISH),
    ]
    # The take-less final step contributes only its publish endpoint, never a metric.
    assert rows[-1]["to"]["topic"] == STEP1_TOPIC
    assert rows[-1]["to"]["node"] == "/relay"
    assert rows[-1]["metric_ms"] == step0["callback_time_ms"]


def _linked_relay_events() -> list[Event]:
    """A producer -> relay -> sink pipeline where the relay's take publishes a uid
    link and a later timer invocation takes that uid and republishes, rather than
    republishing directly inside the take's own callback.

    Returns:
        list[Event]: The synthetic event sequence.
    """
    timer_zone = "_republish_tick"
    uid = 1
    return [
        _node_init_event(
            0, PRODUCER_VPID, PRODUCER_VTID, PRODUCER_NODE_HANDLE, "producer"
        ),
        _node_init_event(0, RELAY_VPID, RELAY_VTID, RELAY_NODE_HANDLE, "relay"),
        _node_init_event(0, SINK_VPID, SINK_VTID, SINK_NODE_HANDLE, "sink"),
        publisher_init_event(
            0, PRODUCER_VPID, PRODUCER_VTID, 1, PRODUCER_NODE_HANDLE, 901, STEP0_TOPIC
        ),
        subscription_init_event(
            0, RELAY_VPID, RELAY_VTID, 10, RELAY_NODE_HANDLE, 11, STEP0_TOPIC
        ),
        zone_declaration_event(
            0,
            RELAY_VPID,
            RELAY_VTID,
            DECL_SUB_PAYLOAD.format(zone_name=ON_MESSAGE_ZONE, topic=STEP0_TOPIC),
        ),
        zone_declaration_event(
            0,
            RELAY_VPID,
            RELAY_VTID,
            DECL_TIMER_PAYLOAD.format(zone_name=timer_zone, period_ns=1_000_000),
        ),
        publisher_init_event(
            0, RELAY_VPID, RELAY_VTID, 2, RELAY_NODE_HANDLE, 12, STEP1_TOPIC
        ),
        subscription_init_event(
            0, SINK_VPID, SINK_VTID, 20, SINK_NODE_HANDLE, 21, STEP1_TOPIC
        ),
        zone_declaration_event(
            0,
            SINK_VPID,
            SINK_VTID,
            DECL_SUB_PAYLOAD.format(zone_name=ON_MESSAGE_ZONE, topic=STEP1_TOPIC),
        ),
        *publish_events(
            1000, PRODUCER_VPID, PRODUCER_VTID, 100, 1, source_timestamp=5000
        ),
        rmw_take_event(1100, RELAY_VPID, RELAY_VTID, 200, 11, 5000),
        python_zone_event(1150, RELAY_VPID, RELAY_VTID, ON_MESSAGE_ZONE, "begin"),
        zone_declaration_event(
            1160, RELAY_VPID, RELAY_VTID, PUB_LINK_PAYLOAD.format(uid=uid)
        ),
        python_zone_event(1250, RELAY_VPID, RELAY_VTID, ON_MESSAGE_ZONE, "end"),
        python_zone_event(1450, RELAY_VPID, RELAY_VTID, timer_zone, "begin"),
        zone_declaration_event(
            1460, RELAY_VPID, RELAY_VTID, TAKE_LINKS_PAYLOAD.format(uids=uid)
        ),
        *publish_events(1500, RELAY_VPID, RELAY_VTID, 300, 2, source_timestamp=6000),
        python_zone_event(1550, RELAY_VPID, RELAY_VTID, timer_zone, "end"),
        rmw_take_event(1600, SINK_VPID, SINK_VTID, 400, 21, 6000),
        python_zone_event(1650, SINK_VPID, SINK_VTID, ON_MESSAGE_ZONE, "begin"),
        python_zone_event(1700, SINK_VPID, SINK_VTID, ON_MESSAGE_ZONE, "end"),
    ]


def test_pipeline_chain_latency_decomposes_an_indirect_step():
    analysis, flow = _build(_linked_relay_events())

    report = build_node_report(
        flow, analysis, topic_chains=[[STEP0_TOPIC, STEP1_TOPIC]]
    )

    assert len(report["pipeline_chain_latency"]) == 1
    step0, step1 = report["pipeline_chain_latency"][0]["steps"]
    # step0's callback time now stops at the link declaration, not callback_end.
    assert step0["is_indirect"] is True
    assert step0["take_to_callback_start_ms"]["count"] == 1
    assert step0["take_to_callback_start_ms"]["mean"] == (1150 - 1100) / 1_000_000
    assert step0["callback_time_ms"]["count"] == 1
    assert step0["callback_time_ms"]["mean"] == (1160 - 1150) / 1_000_000
    assert step0["indirect_wait_ms"]["count"] == 1
    assert step0["indirect_wait_ms"]["mean"] == (1450 - 1160) / 1_000_000
    assert step0["relay_callback_time_ms"]["count"] == 1
    assert step0["relay_callback_time_ms"]["mean"] == (1520 - 1450) / 1_000_000
    # /step1 (relay -> sink) is a direct step and keeps its own network/callback timing.
    assert step1["is_indirect"] is False
    assert step1["indirect_wait_ms"] is None
    assert step1["relay_callback_time_ms"] is None
    assert step1["topic_latency_ms"]["count"] == 1
    assert step1["take_to_callback_start_ms"]["count"] == 1
    assert step1["take_to_callback_start_ms"]["mean"] == (1650 - 1600) / 1_000_000


def test_pipeline_chain_latency_indirect_step_endpoints():
    analysis, flow = _build(_linked_relay_events())

    report = build_node_report(
        flow, analysis, topic_chains=[[STEP0_TOPIC, STEP1_TOPIC]]
    )

    chain = report["pipeline_chain_latency"][0]
    step0, step1 = chain["steps"]
    rows = chain["step_endpoints"]
    kinds = [(row["from"]["kind"], row["to"]["kind"]) for row in rows]
    assert kinds == [
        (ENDPOINT_KIND_PUBLISH, ENDPOINT_KIND_TAKE),
        (ENDPOINT_KIND_TAKE, ENDPOINT_KIND_CALLBACK_START),
        (ENDPOINT_KIND_CALLBACK_START, ENDPOINT_KIND_LINK),
        # An indirect step's link and relay endpoints sit on the next topic.
        (ENDPOINT_KIND_LINK, ENDPOINT_KIND_CALLBACK_START),
        (ENDPOINT_KIND_CALLBACK_START, ENDPOINT_KIND_PUBLISH),
        (ENDPOINT_KIND_PUBLISH, ENDPOINT_KIND_TAKE),
        (ENDPOINT_KIND_TAKE, ENDPOINT_KIND_CALLBACK_START),
        (ENDPOINT_KIND_CALLBACK_START, ENDPOINT_KIND_CALLBACK_END),
    ]
    assert rows[2]["metric_ms"] == step0["callback_time_ms"]
    assert rows[3]["metric_ms"] == step0["indirect_wait_ms"]
    assert rows[4]["metric_ms"] == step0["relay_callback_time_ms"]
    assert rows[5]["metric_ms"] == step1["topic_latency_ms"]
    # The link endpoint belongs to step0's own topic; the callback-start endpoint the
    # link advances into names the relay's own trigger (here a timer), not STEP1_TOPIC.
    assert rows[2]["to"]["topic"] == STEP0_TOPIC
    assert rows[3]["to"]["topic"] == CB_KIND_TIMER
    assert rows[3]["to"]["node"] == "/relay"
    assert step1["callback_time_ms"]["count"] == 1


def test_publisher_rows_aggregate_per_topic_with_by_callback_breakdown():
    # One node publishing the SAME topic from two contexts (subscription and timer)
    # aggregates into one (node, topic) row plus a by_callback breakdown per source.
    OUT_TOPIC, IN_TOPIC = "/out", "/in"
    MULTI_VPID, MULTI_VTID, MULTI_NODE = 300, 30, 930
    OUT_PUB_HANDLE, IN_SUB_HANDLE, IN_RMW_SUB = 5, 50, 51
    events = [
        _node_init_event(
            0, PRODUCER_VPID, PRODUCER_VTID, PRODUCER_NODE_HANDLE, "producer"
        ),
        _node_init_event(0, MULTI_VPID, MULTI_VTID, MULTI_NODE, "multi"),
        publisher_init_event(
            0, PRODUCER_VPID, PRODUCER_VTID, 1, PRODUCER_NODE_HANDLE, 901, IN_TOPIC
        ),
        subscription_init_event(
            0, MULTI_VPID, MULTI_VTID, IN_SUB_HANDLE, MULTI_NODE, IN_RMW_SUB, IN_TOPIC
        ),
        publisher_init_event(
            0, MULTI_VPID, MULTI_VTID, OUT_PUB_HANDLE, MULTI_NODE, 905, OUT_TOPIC
        ),
    ]
    # producer -> /in; multi's _on_message subscription callback republishes to /out
    events += publish_events(
        1000, PRODUCER_VPID, PRODUCER_VTID, 100, 1, source_timestamp=5000
    )
    events.append(rmw_take_event(2000, MULTI_VPID, MULTI_VTID, 100, IN_RMW_SUB, 5000))
    events.append(
        python_zone_event(2100, MULTI_VPID, MULTI_VTID, ON_MESSAGE_ZONE, "begin")
    )
    events += publish_events(
        2200, MULTI_VPID, MULTI_VTID, 200, OUT_PUB_HANDLE, source_timestamp=6000
    )
    events.append(
        python_zone_event(2400, MULTI_VPID, MULTI_VTID, ON_MESSAGE_ZONE, "end")
    )
    # multi's _tick timer also publishes /out
    events.append(python_zone_event(3000, MULTI_VPID, MULTI_VTID, "_tick", "begin"))
    events += publish_events(
        3100, MULTI_VPID, MULTI_VTID, 300, OUT_PUB_HANDLE, source_timestamp=7000
    )
    events.append(python_zone_event(3300, MULTI_VPID, MULTI_VTID, "_tick", "end"))

    analysis, flow = _build(events)
    report = build_node_report(flow, analysis)

    pubs = report["nodes"]["/multi"]["publishers"]
    # One aggregated row for (/multi, /out), NOT one row per source context.
    assert list(pubs.keys()) == [OUT_TOPIC]
    row = pubs[OUT_TOPIC]
    assert row["published_count"] == 2
    assert set(row["by_callback"].keys()) == {ON_MESSAGE_ZONE, "_tick"}
    assert row["by_callback"][ON_MESSAGE_ZONE]["published_count"] == 1
    assert row["by_callback"]["_tick"]["published_count"] == 1
