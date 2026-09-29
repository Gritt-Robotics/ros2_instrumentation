# Copyright 2026 Gritt Robotics Inc.

import os
from unittest.mock import MagicMock, patch

import pytest
from trace_instrumentation.tracing_launch_action import (
    TRACE_EVENTS_PYTHON,
    TRACE_EVENTS_UST,
    TRACE_STARTUP_DELAY_S,
    add_lttng_trace,
    build_analysis_argv,
    compute_session_dir,
    procname_filter_expression,
)

TEST_TRACE_NAME = "test_trace"
TEST_CSV_DIR = "/tmp/trace_test"
TEST_SESSION_NAME = "test_trace-20260101000000"
TEST_RAW_TRACE_DIR = f"{TEST_CSV_DIR}/{TEST_SESSION_NAME}"
TEST_CTF_DIR = f"{TEST_CSV_DIR}/ctf"
TEST_NODE_REPORT_PATH = f"{TEST_CSV_DIR}/{TEST_SESSION_NAME}_report"


def test_compute_session_dir_appends_timestamp_and_joins_csv_dir():
    with patch(
        "trace_instrumentation.tracing_launch_action.append_timestamp",
        return_value=TEST_SESSION_NAME,
    ) as mock_append_timestamp:
        session_name, session_dir = compute_session_dir(TEST_CSV_DIR, TEST_TRACE_NAME)

    mock_append_timestamp.assert_called_once_with(TEST_TRACE_NAME)
    assert session_name == TEST_SESSION_NAME
    assert session_dir == os.path.join(TEST_CSV_DIR, TEST_SESSION_NAME)


def test_compute_session_dir_resolves_relative_csv_dir_to_absolute():
    # Regression: a relative csv_dir made per-node output paths depend on the launched
    # node's working directory instead of being deterministic.
    with patch(
        "trace_instrumentation.tracing_launch_action.append_timestamp",
        return_value=TEST_SESSION_NAME,
    ):
        _, session_dir = compute_session_dir("relative_dir", TEST_TRACE_NAME)

    assert os.path.isabs(session_dir)
    assert session_dir == os.path.abspath(
        os.path.join("relative_dir", TEST_SESSION_NAME)
    )


def test_disabled_returns_entities_unchanged():
    entities = [MagicMock(name="entity_a"), MagicMock(name="entity_b")]

    with (
        patch(
            "trace_instrumentation.tracing_launch_action.os.makedirs"
        ) as mock_makedirs,
        patch("trace_instrumentation.tracing_launch_action.Trace") as mock_trace_cls,
    ):
        result = add_lttng_trace(
            entities,
            enabled=False,
            trace_name=TEST_TRACE_NAME,
            csv_dir=TEST_CSV_DIR,
        )

    assert result is entities
    mock_makedirs.assert_not_called()
    mock_trace_cls.assert_not_called()


def test_short_topic_chain_raises_before_any_trace_side_effects():
    entities = [MagicMock(name="entity_a")]

    with (
        patch(
            "trace_instrumentation.tracing_launch_action.os.makedirs"
        ) as mock_makedirs,
        patch("trace_instrumentation.tracing_launch_action.Trace") as mock_trace_cls,
        pytest.raises(ValueError, match="at least 2 topics"),
    ):
        add_lttng_trace(
            entities,
            enabled=True,
            trace_name=TEST_TRACE_NAME,
            csv_dir=TEST_CSV_DIR,
            topic_chains=[["single_topic"]],
        )

    mock_makedirs.assert_not_called()
    mock_trace_cls.assert_not_called()


def test_short_chain_among_valid_chains_still_raises():
    entities = [MagicMock(name="entity_a")]

    with (
        patch(
            "trace_instrumentation.tracing_launch_action.os.makedirs"
        ) as mock_makedirs,
        patch("trace_instrumentation.tracing_launch_action.Trace") as mock_trace_cls,
        pytest.raises(ValueError, match="at least 2 topics"),
    ):
        add_lttng_trace(
            entities,
            enabled=True,
            trace_name=TEST_TRACE_NAME,
            csv_dir=TEST_CSV_DIR,
            topic_chains=[["/step0", "/step1"], ["/lonely"]],
        )

    mock_makedirs.assert_not_called()
    mock_trace_cls.assert_not_called()


def test_only_topic_chains_without_chains_raises_before_any_trace_side_effects():
    entities = [MagicMock(name="entity_a")]

    with (
        patch(
            "trace_instrumentation.tracing_launch_action.os.makedirs"
        ) as mock_makedirs,
        patch("trace_instrumentation.tracing_launch_action.Trace") as mock_trace_cls,
        pytest.raises(ValueError, match="only_topic_chains requires topic_chains"),
    ):
        add_lttng_trace(
            entities,
            enabled=True,
            trace_name=TEST_TRACE_NAME,
            csv_dir=TEST_CSV_DIR,
            only_topic_chains=True,
        )

    mock_makedirs.assert_not_called()
    mock_trace_cls.assert_not_called()


def test_enabled_wraps_entities_with_trace_timer_and_analysis_handler():
    entities = [MagicMock(name="entity_a"), MagicMock(name="entity_b")]

    with (
        patch(
            "trace_instrumentation.tracing_launch_action.os.makedirs"
        ) as mock_makedirs,
        patch("trace_instrumentation.tracing_launch_action.Trace") as mock_trace_cls,
        patch(
            "trace_instrumentation.tracing_launch_action.TimerAction"
        ) as mock_timer_cls,
        patch(
            "trace_instrumentation.tracing_launch_action.OpaqueFunction"
        ) as mock_opaque_function_cls,
        patch("trace_instrumentation.tracing_launch_action.OnShutdown"),
    ):
        result = add_lttng_trace(
            entities,
            enabled=True,
            trace_name=TEST_SESSION_NAME,
            csv_dir=TEST_CSV_DIR,
        )

    mock_makedirs.assert_called_once_with(TEST_CSV_DIR, exist_ok=True)
    mock_trace_cls.assert_called_once_with(
        session_name=TEST_SESSION_NAME,
        base_path=TEST_CSV_DIR,
        events_ust=TRACE_EVENTS_UST,
        events_python=TRACE_EVENTS_PYTHON,
        events_ust_filter=None,
    )
    mock_timer_cls.assert_called_once_with(
        period=TRACE_STARTUP_DELAY_S, actions=entities
    )
    assert result == [
        mock_trace_cls.return_value,
        mock_timer_cls.return_value,
        mock_opaque_function_cls.return_value,
    ]


def register_analysis_handler(entities: list) -> MagicMock:
    """
    Run the returned registration action and report the context it registered on.

    add_lttng_trace defers registering its shutdown handler to an OpaqueFunction so it
    can append rather than prepend, so a test that wants the handler has to execute
    that action first.

    Args:
        entities (list): The entities add_lttng_trace returned.

    Returns:
        MagicMock: The launch context the registration action was executed with.
    """
    context = MagicMock()
    entities[-1].execute(context)
    return context


def test_analysis_handler_is_appended_so_it_runs_after_the_trace_teardown():
    # Shutdown handlers are stored in a deque that prepends by default, so a prepended
    # analysis would read the trace before Trace's own teardown flushed it.
    entities = [MagicMock(name="entity_a")]

    with (
        patch("trace_instrumentation.tracing_launch_action.os.makedirs"),
        patch("trace_instrumentation.tracing_launch_action.Trace"),
        patch("trace_instrumentation.tracing_launch_action.TimerAction"),
        patch(
            "trace_instrumentation.tracing_launch_action.OnShutdown"
        ) as mock_on_shutdown_cls,
    ):
        result = add_lttng_trace(
            entities,
            enabled=True,
            trace_name=TEST_SESSION_NAME,
            csv_dir=TEST_CSV_DIR,
        )
        context = register_analysis_handler(result)

    context.register_event_handler.assert_called_once_with(
        mock_on_shutdown_cls.return_value, append=True
    )


def test_explicit_empty_event_lists_are_not_overridden_by_defaults():
    entities = [MagicMock(name="entity_a")]

    with (
        patch("trace_instrumentation.tracing_launch_action.os.makedirs"),
        patch("trace_instrumentation.tracing_launch_action.Trace") as mock_trace_cls,
        patch("trace_instrumentation.tracing_launch_action.TimerAction"),
        patch("trace_instrumentation.tracing_launch_action.OnShutdown"),
    ):
        add_lttng_trace(
            entities,
            enabled=True,
            trace_name=TEST_SESSION_NAME,
            csv_dir=TEST_CSV_DIR,
            events_ust=[],
            events_python=[],
        )

    mock_trace_cls.assert_called_once_with(
        session_name=TEST_SESSION_NAME,
        base_path=TEST_CSV_DIR,
        events_ust=[],
        events_python=[],
        events_ust_filter=None,
    )


def test_analysis_handler_invokes_analyze_trace_with_node_report_path():
    entities = [MagicMock(name="entity_a")]

    with (
        patch("trace_instrumentation.tracing_launch_action.os.makedirs"),
        patch("trace_instrumentation.tracing_launch_action.Trace"),
        patch("trace_instrumentation.tracing_launch_action.TimerAction"),
        patch(
            "trace_instrumentation.tracing_launch_action.OnShutdown"
        ) as mock_on_shutdown_cls,
        patch("trace_instrumentation.tracing_launch_action.os.rename") as mock_rename,
        patch(
            "trace_instrumentation.tracing_launch_action.os.path.exists",
            return_value=False,
        ),
        patch(
            "trace_instrumentation.trace_analysis.cli.main"
        ) as mock_run_trace_analysis,
    ):
        register_analysis_handler(
            add_lttng_trace(
                entities,
                enabled=True,
                trace_name=TEST_SESSION_NAME,
                csv_dir=TEST_CSV_DIR,
            )
        )

        on_shutdown_callback = mock_on_shutdown_cls.call_args.kwargs["on_shutdown"]
        result = on_shutdown_callback(MagicMock(), MagicMock())

    # --node-report is what emits the machine-readable .json report (write_node_report()).
    mock_rename.assert_called_once_with(TEST_RAW_TRACE_DIR, TEST_CTF_DIR)
    # Empty: analysis already ran in-process, so the handler schedules nothing further.
    assert result == []
    mock_run_trace_analysis.assert_called_once_with([
        TEST_CTF_DIR,
        "--node-report",
        TEST_NODE_REPORT_PATH,
    ])


def test_analysis_handler_raises_if_ctf_dir_already_exists():
    # Regression: a rerun with the same csv_dir/trace_name used to surface a bare
    # os.rename() error from inside the shutdown handler instead of an actionable one.
    entities = [MagicMock(name="entity_a")]

    with (
        patch("trace_instrumentation.tracing_launch_action.os.makedirs"),
        patch("trace_instrumentation.tracing_launch_action.Trace"),
        patch("trace_instrumentation.tracing_launch_action.TimerAction"),
        patch(
            "trace_instrumentation.tracing_launch_action.OnShutdown"
        ) as mock_on_shutdown_cls,
        patch("trace_instrumentation.tracing_launch_action.os.rename") as mock_rename,
        patch(
            "trace_instrumentation.tracing_launch_action.os.path.exists",
            return_value=True,
        ),
    ):
        register_analysis_handler(
            add_lttng_trace(
                entities,
                enabled=True,
                trace_name=TEST_SESSION_NAME,
                csv_dir=TEST_CSV_DIR,
            )
        )

        on_shutdown_callback = mock_on_shutdown_cls.call_args.kwargs["on_shutdown"]
        with pytest.raises(FileExistsError):
            on_shutdown_callback(MagicMock(), MagicMock())

    mock_rename.assert_not_called()


def test_analysis_handler_appends_topic_chain_when_given():
    entities = [MagicMock(name="entity_a")]

    with (
        patch("trace_instrumentation.tracing_launch_action.os.makedirs"),
        patch("trace_instrumentation.tracing_launch_action.Trace"),
        patch("trace_instrumentation.tracing_launch_action.TimerAction"),
        patch(
            "trace_instrumentation.tracing_launch_action.OnShutdown"
        ) as mock_on_shutdown_cls,
        patch("trace_instrumentation.tracing_launch_action.os.rename"),
        patch(
            "trace_instrumentation.tracing_launch_action.os.path.exists",
            return_value=False,
        ),
        patch(
            "trace_instrumentation.trace_analysis.cli.main"
        ) as mock_run_trace_analysis,
    ):
        register_analysis_handler(
            add_lttng_trace(
                entities,
                enabled=True,
                trace_name=TEST_SESSION_NAME,
                csv_dir=TEST_CSV_DIR,
                topic_chains=[["/step0", "/step1"]],
            )
        )
        mock_on_shutdown_cls.call_args.kwargs["on_shutdown"](MagicMock(), MagicMock())

    mock_run_trace_analysis.assert_called_once_with([
        TEST_CTF_DIR,
        "--node-report",
        TEST_NODE_REPORT_PATH,
        "--topic-chain",
        "/step0,/step1",
    ])


def test_analysis_handler_forwards_every_chain_ignore_node_and_the_filter_flag():
    entities = [MagicMock(name="entity_a")]

    with (
        patch("trace_instrumentation.tracing_launch_action.os.makedirs"),
        patch("trace_instrumentation.tracing_launch_action.Trace"),
        patch("trace_instrumentation.tracing_launch_action.TimerAction"),
        patch(
            "trace_instrumentation.tracing_launch_action.OnShutdown"
        ) as mock_on_shutdown_cls,
        patch("trace_instrumentation.tracing_launch_action.os.rename"),
        patch(
            "trace_instrumentation.tracing_launch_action.os.path.exists",
            return_value=False,
        ),
        patch(
            "trace_instrumentation.trace_analysis.cli.main"
        ) as mock_run_trace_analysis,
    ):
        register_analysis_handler(
            add_lttng_trace(
                entities,
                enabled=True,
                trace_name=TEST_SESSION_NAME,
                csv_dir=TEST_CSV_DIR,
                topic_chains=[["/step0", "/step1"], ["/step0", "/step2", "/step3"]],
                ignore_nodes=["/rviz", "/robot_state_publisher"],
                only_topic_chains=True,
            )
        )
        mock_on_shutdown_cls.call_args.kwargs["on_shutdown"](MagicMock(), MagicMock())

    mock_run_trace_analysis.assert_called_once_with([
        TEST_CTF_DIR,
        "--node-report",
        TEST_NODE_REPORT_PATH,
        "--topic-chain",
        "/step0,/step1",
        "--topic-chain",
        "/step0,/step2,/step3",
        "--ignore-nodes",
        "/rviz",
        "--ignore-nodes",
        "/robot_state_publisher",
        "--only-topic-chains",
    ])


def test_build_analysis_argv_without_topic_chains():
    argv = build_analysis_argv(TEST_CTF_DIR, TEST_NODE_REPORT_PATH, None)

    assert argv == [
        TEST_CTF_DIR,
        "--node-report",
        TEST_NODE_REPORT_PATH,
    ]


def test_build_analysis_argv_with_one_chain_appends_topic_chain_flag():
    argv = build_analysis_argv(
        TEST_CTF_DIR,
        TEST_NODE_REPORT_PATH,
        [["topic_a", "topic_b"]],
    )

    assert argv == [
        TEST_CTF_DIR,
        "--node-report",
        TEST_NODE_REPORT_PATH,
        "--topic-chain",
        "topic_a,topic_b",
    ]


def test_build_analysis_argv_repeats_the_flag_once_per_chain():
    argv = build_analysis_argv(
        TEST_CTF_DIR,
        TEST_NODE_REPORT_PATH,
        [["topic_a", "topic_b"], ["topic_a", "topic_c"]],
    )

    assert argv == [
        TEST_CTF_DIR,
        "--node-report",
        TEST_NODE_REPORT_PATH,
        "--topic-chain",
        "topic_a,topic_b",
        "--topic-chain",
        "topic_a,topic_c",
    ]


def test_build_analysis_argv_emits_one_ignore_nodes_flag_per_node():
    argv = build_analysis_argv(
        TEST_CTF_DIR,
        TEST_NODE_REPORT_PATH,
        None,
        ignore_nodes=["/node_a", "/node_b"],
    )

    assert argv == [
        TEST_CTF_DIR,
        "--node-report",
        TEST_NODE_REPORT_PATH,
        "--ignore-nodes",
        "/node_a",
        "--ignore-nodes",
        "/node_b",
    ]


def test_build_analysis_argv_appends_only_topic_chains_last():
    argv = build_analysis_argv(
        TEST_CTF_DIR,
        TEST_NODE_REPORT_PATH,
        [["topic_a", "topic_b"]],
        only_topic_chains=True,
    )

    assert argv[-1] == "--only-topic-chains"


def test_build_analysis_argv_omits_the_filter_flag_when_not_requested():
    argv = build_analysis_argv(
        TEST_CTF_DIR,
        TEST_NODE_REPORT_PATH,
        [["topic_a", "topic_b"]],
        only_topic_chains=False,
    )

    assert "--only-topic-chains" not in argv


def test_procname_filter_expression_ors_one_clause_per_process():
    expression = procname_filter_expression(["relay_node", "image_processor"])

    assert expression == (
        '$ctx.procname == "image_processor" || $ctx.procname == "relay_node"'
    )


def test_procname_filter_expression_truncates_to_what_the_tracer_records():
    expression = procname_filter_expression(["long_process_name_node"])

    assert expression == '$ctx.procname == "long_process_na"'


def test_procname_filter_expression_rejects_an_empty_list():
    with pytest.raises(ValueError, match="at least one process name"):
        procname_filter_expression([])


def test_procname_filter_expression_rejects_a_bare_string():
    with pytest.raises(ValueError, match="needs a list of process names"):
        procname_filter_expression("image_processor")


def test_procname_filter_expression_rejects_a_non_string_name():
    with pytest.raises(ValueError, match="is not a string"):
        procname_filter_expression(["image_processor", 7])


def test_procname_filter_expression_rejects_an_empty_name():
    with pytest.raises(ValueError, match="empty process name"):
        procname_filter_expression(["image_processor", ""])


def test_procname_filter_expression_rejects_a_name_that_escapes_the_quotes():
    with pytest.raises(ValueError, match="forbidden characters"):
        procname_filter_expression(['object" || $ctx.procname == "anything'])


def test_procname_filter_expression_rejects_names_that_collide_once_truncated():
    with pytest.raises(ValueError, match="indistinguishable"):
        procname_filter_expression(["worker_process_main", "worker_process_alt"])


def test_a_repeated_process_name_is_not_a_collision():
    expression = procname_filter_expression(["relay_node", "relay_node"])

    assert expression == '$ctx.procname == "relay_node"'


def test_trace_processes_become_the_sessions_ust_filter():
    with (
        patch("trace_instrumentation.tracing_launch_action.os.makedirs"),
        patch("trace_instrumentation.tracing_launch_action.Trace") as mock_trace_cls,
    ):
        add_lttng_trace(
            [MagicMock(name="entity_a")],
            enabled=True,
            trace_name=TEST_TRACE_NAME,
            csv_dir=TEST_CSV_DIR,
            trace_processes=["relay_node"],
        )

    assert (
        mock_trace_cls.call_args.kwargs["events_ust_filter"]
        == '$ctx.procname == "relay_node"'
    )


def test_no_trace_processes_records_every_process():
    with (
        patch("trace_instrumentation.tracing_launch_action.os.makedirs"),
        patch("trace_instrumentation.tracing_launch_action.Trace") as mock_trace_cls,
    ):
        add_lttng_trace(
            [MagicMock(name="entity_a")],
            enabled=True,
            trace_name=TEST_TRACE_NAME,
            csv_dir=TEST_CSV_DIR,
        )

    assert mock_trace_cls.call_args.kwargs["events_ust_filter"] is None
