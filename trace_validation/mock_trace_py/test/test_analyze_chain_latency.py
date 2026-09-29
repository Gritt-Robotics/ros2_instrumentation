# Copyright 2026 Gritt Robotics Inc.

import csv
from pathlib import Path
from typing import List

import pytest
from mock_trace_py.analyze_chain_latency import (
    compute_chain_latency,
    load_step_rows_by_chain_id,
    main,
    render_report,
    write_csv,
)

RELAY_CSV_ROWS = [
    [200, 1080, 1080, 100, 1, 100, 900, 1050, "/step0", "/step1", "relay", 4, 4],
    [201, 2050, 2050, 101, 1, 101, 1900, 2040, "/step0", "/step1", "relay", 4, 4],
]


def _write_csv(path, header, rows):
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)


def _build_step_csvs(tmp_path: Path) -> List[str]:
    """
    Build a hand-computed 3-step (publisher -> relay -> subscriber) fixture.

    Chain 100 and chain 101 fully traverse all 3 steps; chain 102 only reaches the
    publisher (dropped mid-chain).

    Args:
        tmp_path (Path): pytest tmp_path fixture directory to write the CSVs under.

    Returns:
        List[str]: [pub_csv_path, relay_csv_path, sub_csv_path].
    """
    pub_csv = tmp_path / "pub.csv"
    relay_csv = tmp_path / "relay.csv"
    sub_csv = tmp_path / "sub.csv"

    _write_csv(
        pub_csv,
        [
            "trace_id",
            "header_stamp_ns",
            "wall_publish_ns",
            "topic",
            "node",
            "payload_len",
            "chain_id",
            "step_index",
            "upstream_trace_id",
        ],
        [
            [100, 900, 1000, "/step0", "pub", 4, 100, 0, 0],
            [101, 1900, 2000, "/step0", "pub", 4, 101, 0, 0],
            [102, 2900, 3000, "/step0", "pub", 4, 102, 0, 0],
        ],
    )

    _write_csv(
        relay_csv,
        [
            "trace_id",
            "header_stamp_ns",
            "wall_publish_ns",
            "chain_id",
            "step_index",
            "upstream_trace_id",
            "upstream_header_stamp_ns",
            "wall_recv_ns",
            "topic_in",
            "topic_out",
            "node",
            "payload_len_in",
            "payload_len_out",
        ],
        RELAY_CSV_ROWS,
    )

    _write_csv(
        sub_csv,
        [
            "trace_id",
            "header_stamp_ns",
            "wall_recv_ns",
            "topic",
            "node",
            "chain_id",
            "step_index",
            "upstream_trace_id",
        ],
        [
            [200, 1080, 1150, "/step1", "sub", 100, 2, 200],
            [201, 2050, 2130, "/step1", "sub", 101, 2, 201],
        ],
    )

    return [str(pub_csv), str(relay_csv), str(sub_csv)]


def test_load_step_rows_by_chain_id_raises_on_missing_chain_id_column(tmp_path):
    non_chain_csv = tmp_path / "variable_pub.csv"
    _write_csv(
        non_chain_csv,
        [
            "trace_id",
            "header_stamp_ns",
            "wall_publish_ns",
            "topic",
            "node",
            "payload_len",
        ],
        [[1, 100, 200, "/t", "pub", 4]],
    )

    with pytest.raises(ValueError, match="chain_id"):
        load_step_rows_by_chain_id(str(non_chain_csv))


def test_load_step_rows_by_chain_id_indexes_by_chain_id(tmp_path):
    step_csvs = _build_step_csvs(tmp_path)
    rows_by_chain_id = load_step_rows_by_chain_id(step_csvs[0])

    assert set(rows_by_chain_id.keys()) == {100, 101, 102}
    assert rows_by_chain_id[100]["trace_id"] == 100
    assert rows_by_chain_id[100]["wall_publish_ns"] == 1000


def test_compute_chain_latency_raises_on_fewer_than_two_step_paths(tmp_path):
    step_csvs = _build_step_csvs(tmp_path)

    with pytest.raises(ValueError, match="at least 2 step CSVs"):
        compute_chain_latency(step_csvs[:1])


def test_compute_chain_latency_matches_hand_computed_values(tmp_path):
    step_csvs = _build_step_csvs(tmp_path)
    result = compute_chain_latency(step_csvs)

    assert result["traversal_count"] == 2
    assert result["dropped_count"] == 1

    steps = result["steps"]
    assert len(steps) == 2

    # Step 0 -> 1 (publisher -> relay): network latencies are [1050-1000, 2040-2000] =
    # [50, 40]. The publisher row has no wall_recv_ns, so step-0 processing is None.
    step0 = steps[0]
    assert step0["from_step"] == 0
    assert step0["to_step"] == 1
    net0 = step0["network_latency_ns"]
    assert net0.count == 2
    assert net0.min == 40
    assert net0.max == 50
    assert net0.mean == 45
    assert step0["processing_latency_ns"] is None

    # Step 1 -> 2 (relay -> subscriber): network latencies are [1150-1080, 2130-2050] =
    # [70, 80]. Relay processing latencies are [1080-1050, 2050-2040] = [30, 10].
    step1 = steps[1]
    assert step1["from_step"] == 1
    assert step1["to_step"] == 2
    net1 = step1["network_latency_ns"]
    assert net1.count == 2
    assert net1.min == 70
    assert net1.max == 80
    assert net1.mean == 75
    proc1 = step1["processing_latency_ns"]
    assert proc1.count == 2
    assert proc1.min == 10
    assert proc1.max == 30
    assert proc1.mean == 20

    # End-to-end: [1150-1000, 2130-2000] = [150, 130].
    total = result["total_latency_ns"]
    assert total.count == 2
    assert total.min == 130
    assert total.max == 150
    assert total.mean == 140


def test_render_report_includes_traversal_and_latency_summary(tmp_path):
    step_csvs = _build_step_csvs(tmp_path)
    result = compute_chain_latency(step_csvs)

    report = render_report(result)

    assert "chains traversed the full pipeline: 2" in report
    assert "chains dropped/mismatched mid-chain: 1" in report
    assert "step 0 -> 1 (network)" in report
    assert "step 1 (processing)" in report
    assert "end-to-end:" in report


def test_write_csv_writes_expected_header_and_row_count(tmp_path):
    step_csvs = _build_step_csvs(tmp_path)
    result = compute_chain_latency(step_csvs)
    output_csv = tmp_path / "out.csv"

    write_csv(str(output_csv), result)

    with open(output_csv, newline="") as fh:
        rows = list(csv.reader(fh))

    assert rows[0] == [
        "step",
        "kind",
        "count",
        "min_ns",
        "mean_ns",
        "median_ns",
        "p99_ns",
        "max_ns",
    ]
    # step 0->1 network, step 1 processing (step 0 has no processing row), step 1->2
    # network, step 1->2 processing, end_to_end.
    assert [row[:2] for row in rows[1:]] == [
        ["0 -> 1", "network"],
        ["1 -> 2", "network"],
        ["step 1", "processing"],
        ["end_to_end", "end_to_end"],
    ]


def test_main_returns_error_for_fewer_than_two_csv_paths(tmp_path):
    step_csvs = _build_step_csvs(tmp_path)

    assert main([step_csvs[0]]) == 1
