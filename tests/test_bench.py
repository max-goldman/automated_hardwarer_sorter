"""Bench mode, decision-log diffing and the stride guard (small scenes; no camera)."""

from __future__ import annotations

import itertools
import logging

import pytest

from hwsorter.bench import (
    BUDGET_MS,
    OUT_OF_SCOPE_KINDS,
    compare_strides,
    cpu_model,
    decision_view,
    diff_decisions,
    format_bench,
    format_strides,
    run_bench,
    scene_for_cfg,
    stride_parts,
    write_synthetic_recording,
)
from hwsorter.cli import main
from hwsorter.config import load_config
from hwsorter.synthetic import PART_KINDS, PartSpec


@pytest.fixture(scope="module")
def small_recording(tmp_path_factory):
    cfg = load_config()
    parts = [PartSpec("nut_025", 0.3, 1.0, 10.0), PartSpec("washer_0312", 1.0, 1.2, 0.0)]
    folder = tmp_path_factory.mktemp("bench") / "rec"
    return write_synthetic_recording(folder, parts, scene_for_cfg(cfg))


def test_cpu_model_is_a_nonempty_label():
    assert cpu_model().strip()


def test_bench_reports_stage_stats_and_budget(small_recording, tmp_path):
    r = run_bench(load_config(), str(small_recording), tmp_path / "log.jsonl")
    assert r.n_parts == 2 and r.n_frames > r.n_processed > 0
    assert {"preprocess", "segment", "features", "fuse", "classify"} <= set(r.stages)
    for s in r.stages.values():
        assert s["count"] > 0 and s["max"] >= s["p99"] >= s["p50"] >= 0 and s["mean"] >= 0
    assert r.stages["fuse"]["count"] == r.stages["classify"]["count"] == 2  # per part
    assert r.total_per_part_ms > 0 and r.throughput_fps > 0
    assert r.budget_ms == BUDGET_MS == 8.3
    assert r.passed == (r.per_frame["mean"] <= BUDGET_MS)
    text = format_bench(r)
    assert cpu_model() in text
    assert "not count for acceptance" in text and "ms/part" in text
    assert ("PASS" if r.passed else "FAIL") in text


def test_bench_cli(small_recording, capsys):
    try:
        code = main(["bench", str(small_recording)])
    finally:
        logging.getLogger().handlers.clear()
        logging.getLogger().setLevel(logging.WARNING)
    out = capsys.readouterr().out
    assert code in (0, 1) and "CPU:" in out and "per input frame" in out
    assert main(["bench"]) == 2  # no recording and no --compare-strides


def test_diff_decisions_ignores_timings_machine_and_flags_decisions():
    base = [
        {"kind": "log_open", "t_ns": 1, "wall_time": "x"},
        {"kind": "session_start", "machine": "a", "session": "s", "px_per_in": 367.0},
        {"kind": "group", "group_id": 1, "output": "nut_025", "timings": {"latency_ms": 1.0}},
        {"kind": "send", "group_id": 1, "t_actual_ns": 5},
    ]
    same = [
        {"kind": "session_start", "machine": "b", "session": "t", "px_per_in": 367.0},
        {"kind": "send", "group_id": 1, "t_actual_ns": 99},
        {"kind": "group", "group_id": 1, "output": "nut_025", "timings": {"latency_ms": 7.0}},
    ]
    assert diff_decisions(base, same) == []  # send/group interleaving is irrelevant
    changed = [dict(r) for r in same]
    changed[2]["output"] = "misc"
    problems = diff_decisions(base, changed)
    assert problems == ["group[0].output: expected 'nut_025', got 'misc'"]
    assert diff_decisions(base, same[:2]) == ["group: expected 1 records, got 0"]
    assert "machine" not in decision_view(base)["session_start"][0]


def test_stride_parts_deterministic_and_balanced():
    a, b = stride_parts(60), stride_parts(60)
    assert a == b and len(a) == 60
    kinds = [p.kind for p in a]
    assert all(kinds.count(k) >= 8 for k in PART_KINDS)
    assert sum(k in OUT_OF_SCOPE_KINDS for k in kinds) == 10
    gaps = [y.t_enter_s - x.t_enter_s for x, y in itertools.pairwise(a)]
    assert min(gaps) >= 0.69  # singulated well past belt.rework_gap_in


def test_compare_strides_guard_logic_on_a_small_run():
    """Same guard as the 60-part slow test, on 8 parts and the two corner configs only."""
    comp = compare_strides(load_config(), n_parts=8, configs=((1, 20), (2, 10)))
    assert (comp.baseline.frame_stride, comp.baseline.max_feature_frames) == (1, 20)
    assert (comp.default.frame_stride, comp.default.max_feature_frames) == (2, 10)
    assert comp.baseline.n_parts == 8 and not comp.baseline.error and not comp.default.error
    assert comp.default.n_contaminated == 0 and comp.baseline.n_contaminated == 0
    assert comp.default.mean_frames_used <= 10 < comp.baseline.mean_frames_used
    assert comp.accuracy_loss_pp <= 0.5 and comp.worst_error_growth_in <= 0.002
    assert comp.passed
    text = format_strides(comp)
    assert "PASS" in text and "MAD" in text and "CPU:" in text
