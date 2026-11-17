"""Golden regression: the current pipeline reproduces the committed decision logs exactly
(timings and the other determinism-ignored keys excluded), and ``replay --compare`` flags drift.

Regenerate intentionally with ``uv run python scripts/update_goldens.py``."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from hwsorter.bench import (
    GOLDEN_SCENARIOS,
    CompareTol,
    canonical_text,
    diff_decisions,
    read_log,
    replay_decisions,
)
from hwsorter.cli import main
from hwsorter.config import load_config

GOLDEN_DIR = Path(__file__).parent / "goldens"


def run_main(argv: list[str]) -> int:
    try:
        return main(argv)
    finally:  # main() installs a root log handler; do not leak it into other tests
        logging.getLogger().handlers.clear()
        logging.getLogger().setLevel(logging.WARNING)


def test_golden_fixtures_are_small_and_present():
    files = sorted(GOLDEN_DIR.glob("*.jsonl"))
    assert {f.stem for f in files} == set(GOLDEN_SCENARIOS)
    assert sum(f.stat().st_size for f in GOLDEN_DIR.rglob("*") if f.is_file()) < 5_000_000


@pytest.mark.parametrize("name", GOLDEN_SCENARIOS)
def test_pipeline_reproduces_golden_within_tolerance(name, golden_recordings, tmp_path):
    _, records, _ = replay_decisions(
        load_config(), str(golden_recordings[name]), tmp_path / "l.jsonl"
    )
    golden = read_log(GOLDEN_DIR / f"{name}.jsonl")
    assert diff_decisions(golden, records, tol=CompareTol()) == []
    # Same decision structure at the byte level: only float digits may differ across machines.
    assert len(canonical_text(records).splitlines()) == len(golden)


def _golden_records(name):
    return read_log(GOLDEN_DIR / f"{name}.jsonl")


def test_tolerant_compare_accepts_small_float_noise_and_reports_deviation():
    recs = _golden_records("mixed_classes")
    noisy = json.loads(json.dumps(recs))
    part = next(r for r in noisy if r["kind"] == "part")
    key = next(k for k, v in part["median"].items() if isinstance(v, float))
    part["median"][key] += 2e-5
    dev = [0.0]
    assert diff_decisions(recs, noisy, tol=CompareTol(), dev=dev) == []
    assert 1e-5 < dev[0] < 1e-4
    assert diff_decisions(recs, noisy)  # strict (default) still sees it


def test_tolerant_compare_fails_on_decision_and_large_float_changes():
    recs = _golden_records("mixed_classes")
    bad = json.loads(json.dumps(recs))
    group = next(r for r in bad if r["kind"] == "group")
    group["output"] = "misc" if group["output"] != "misc" else "rework"
    assert any(".output" in p for p in diff_decisions(recs, bad, tol=CompareTol()))

    bad = json.loads(json.dumps(recs))
    part = next(r for r in bad if r["kind"] == "part")
    part["part_class"] = "something_else"
    assert diff_decisions(recs, bad, tol=CompareTol())

    bad = json.loads(json.dumps(recs))
    part = next(r for r in bad if r["kind"] == "part")
    key = next(k for k, v in part["median"].items() if isinstance(v, float))
    part["median"][key] += 0.01
    assert diff_decisions(recs, bad, tol=CompareTol())

    bad = json.loads(json.dumps(recs))
    next(r for r in bad if r["kind"] == "send")["t_send_ns"] += 5000
    assert diff_decisions(recs, bad, tol=CompareTol())
    bad = json.loads(json.dumps(recs))
    next(r for r in bad if r["kind"] == "send")["t_send_ns"] += 500
    assert diff_decisions(recs, bad, tol=CompareTol()) == []
    bad = json.loads(json.dumps(recs))
    next(r for r in bad if r["kind"] == "part")["n_frames"] += 1
    assert diff_decisions(recs, bad, tol=CompareTol())


def test_golden_scenes_cover_the_decision_kinds():
    kinds, outputs = set(), set()
    for name in GOLDEN_SCENARIOS:
        for line in (GOLDEN_DIR / f"{name}.jsonl").read_text().splitlines():
            rec = json.loads(line)
            kinds.add(rec["kind"])
            if rec["kind"] == "group":
                outputs.add(rec["output"])
    assert {"session_start", "part", "group", "send", "session_end"} <= kinds
    assert {"rework", "misc", "bolt_025", "nut_0375", "washer_025"} <= outputs


def test_replay_compare_matches_golden(golden_recordings, capsys):
    name = GOLDEN_SCENARIOS[0]
    code = run_main(
        ["replay", str(golden_recordings[name]), "--compare", str(GOLDEN_DIR / f"{name}.jsonl")]
    )
    assert code == 0
    assert "MATCH" in capsys.readouterr().out


def test_replay_compare_exits_nonzero_on_mismatch(golden_recordings, tmp_path, capsys):
    name = "mixed_classes"
    lines = (GOLDEN_DIR / f"{name}.jsonl").read_text().splitlines()
    tampered = []
    for line in lines:
        rec = json.loads(line)
        if rec["kind"] == "group" and rec["member_track_ids"] == [2]:
            rec["output"] = "misc"
        tampered.append(json.dumps(rec))
    bad = tmp_path / "bad.jsonl"
    bad.write_text("\n".join(tampered) + "\n")
    code = run_main(["replay", str(golden_recordings[name]), "--compare", str(bad)])
    out = capsys.readouterr().out
    assert code == 1
    assert "MISMATCH" in out and "group[1].output" in out


def test_replay_compare_ignores_timings_and_machine(golden_recordings, tmp_path, capsys):
    name = "mixed_classes"
    lines = []
    for line in (GOLDEN_DIR / f"{name}.jsonl").read_text().splitlines():
        rec = json.loads(line)
        rec["timings"] = {"latency_ms": 999.0}
        rec["t_ns"] = 12345
        if rec["kind"] == "session_start":
            rec["machine"] = "some-other-host"
        lines.append(json.dumps(rec))
    other = tmp_path / "other.jsonl"
    other.write_text("\n".join(lines) + "\n")
    assert run_main(["replay", str(golden_recordings[name]), "--compare", str(other)]) == 0
    assert (
        run_main(["replay", str(golden_recordings[name]), "--compare", str(tmp_path / "no")]) == 2
    )
