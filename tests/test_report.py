"""Accuracy and latency report: hand-built decision logs and label stores with known answers."""

from __future__ import annotations

import csv
import io
import json
import re

import pytest

from hwsorter.cli import build_parser, main
from hwsorter.contracts import PartClass
from hwsorter.grading import KIND_CLASS, KIND_OUT_OF_SCOPE, Label, LabelStore
from hwsorter.report import build_report, generate, load_stats, to_csv, to_html
from hwsorter.report.metrics import FAIL, NA, PASS, Targets

NS = 1_000_000_000
MS = 1_000_000


def part(tid, cls="bolt_025", verdict="confident", on_seam=False, lat=50.0):
    return {
        "kind": "part",
        "track_id": tid,
        "verdict": verdict,
        "part_class": cls if verdict == "confident" else None,
        "flags": ["ON_SEAM"] if on_seam else [],
        "on_seam": on_seam,
        "t_ref_ns": tid * NS,
        "belt_x_lead_in": tid * 10.0 + 1.0,
        "belt_x_trail_in": tid * 10.0,
        "timings": {"stages_ms": {"features": 0.1}, "latency_ms": lat},
    }


def group(gid, members, output):
    return {"kind": "group", "group_id": gid, "member_track_ids": members, "output": output}


def send(gid, output, miss="none", sched=10 * NS, err_ms=0.0):
    return {
        "kind": "send",
        "group_id": gid,
        "output": output,
        "miss": miss,
        "t_send_ns": sched,
        "t_actual_ns": sched + int(err_ms * MS),
    }


STAGE = {"count": 100, "mean": 1.0, "p50": 1.0, "p95": 2.0, "p99": 3.0, "max": 4.0}


def end_rec(n_dropped=0, stage_mean=1.0, n_frames=240, duration_s=2.0):
    stage = {**STAGE, "mean": stage_mean}
    return {
        "kind": "session_end",
        "n_frames": n_frames,
        "n_dropped": n_dropped,
        "duration_ns": int(duration_s * NS),
        "timings": {"stages_ms": {"segment": stage, "track": stage}, "n_skipped": 0},
    }


def make(root, parts, groups, sends, labels, parts_fed=None, end=None):
    """Write a session folder + labels. ``labels``: {track_id: PartClass value | None(oos)}."""
    (root / "decisions").mkdir(parents=True)
    lines = [{"kind": "session_start", "session": root.name}, *parts, *groups, *sends]
    lines.append(end if end is not None else end_rec())
    (root / "decisions" / "decision_log.jsonl").write_text(
        "\n".join(json.dumps(r) for r in lines) + "\n"
    )
    store = LabelStore(root / "labels.jsonl")
    for p in parts:
        tid = p["track_id"]
        if tid not in labels:
            continue
        cls = labels[tid]
        store.put(
            Label(
                key=f"{p['t_ref_ns']}:{p['belt_x_lead_in']:.3f}",
                t_ref_ns=p["t_ref_ns"],
                belt_x_lead_in=p["belt_x_lead_in"],
                belt_x_trail_in=p["belt_x_trail_in"],
                kind=KIND_OUT_OF_SCOPE if cls is None else KIND_CLASS,
                track_id=tid,
                part_class=cls,
            )
        )
    store.set_parts_fed(parts_fed if parts_fed is not None else len(labels))
    return root


def run(tmp_path, parts, groups, sends, labels, **kw):
    root = make(tmp_path / "s", parts, groups, sends, labels, **kw)
    return build_report([load_stats(root)])


def check(rep, name):
    return next(c for c in rep.checks if c.name == name)


def test_clean_run(tmp_path):
    parts = [part(1, "bolt_025"), part(2, "nut_025"), part(3, "bolt_025"), part(4, "washer_025")]
    groups = [group(i, [i], parts[i - 1]["part_class"]) for i in range(1, 5)]
    sends = [send(i, groups[i - 1]["output"]) for i in range(1, 5)]
    labels = {1: "bolt_025", 2: "nut_025", 3: "bolt_025", 4: "washer_025"}
    rep = run(tmp_path, parts, groups, sends, labels)
    assert rep.accuracy == 1.0 and rep.contamination == 0.0
    assert rep.misc_rate == 0.0 and rep.rework_rate == 0.0
    assert rep.confusion["bolt_025"]["bolt_025"] == 2
    assert rep.per_class["bolt_025"] == {"precision": 1.0, "recall": 1.0, "support": 2}
    assert rep.per_class["bolt_0312"]["precision"] is None
    assert rep.overall == PASS
    assert len(rep.confusion) == 7 and all(len(r) == 8 for r in rep.confusion.values())


def test_contamination(tmp_path):
    # part 2 is really a nut but went to the bolt bin; part 4 (out of scope) went to a nut bin
    parts = [part(1, "bolt_025"), part(2, "bolt_025"), part(3, "nut_025"), part(4, "nut_025")]
    groups = [group(i, [i], parts[i - 1]["part_class"]) for i in range(1, 5)]
    sends = [send(i, groups[i - 1]["output"]) for i in range(1, 5)]
    labels = {1: "bolt_025", 2: "nut_025", 3: "nut_025", 4: None}
    rep = run(tmp_path, parts, groups, sends, labels)
    assert rep.confusion["nut_025"]["bolt_025"] == 1
    assert rep.confusion["out_of_scope"]["nut_025"] == 1
    assert rep.n_contaminated == 2 and rep.contamination == 0.5
    assert rep.accuracy == 0.5
    assert rep.per_class["bolt_025"]["precision"] == 0.5
    assert (
        rep.per_class["nut_025"]["precision"] == 0.5 and rep.per_class["nut_025"]["recall"] == 0.5
    )
    assert check(rep, "Bin contamination").verdict == FAIL
    assert rep.overall == FAIL


def test_misc_and_rework_are_not_contamination(tmp_path):
    parts = [part(1, verdict="out_of_scope"), part(2, "bolt_025"), part(3, "nut_025")]
    groups = [group(1, [1], "misc"), group(2, [2, 3], "rework")]
    sends = [send(1, "misc"), send(2, "rework")]
    labels = {1: None, 2: "bolt_025", 3: "nut_025"}
    rep = run(tmp_path, parts, groups, sends, labels)
    assert rep.contamination == 0.0 and rep.accuracy == 1.0
    assert rep.misc_rate == pytest.approx(1 / 3) and rep.rework_rate == pytest.approx(2 / 3)
    assert rep.confusion["out_of_scope"]["misc"] == 1
    assert rep.confusion["bolt_025"]["rework"] == 1


def test_parts_fed_miss_counts_as_undetected(tmp_path):
    parts = [part(1), part(2)]
    groups = [group(1, [1], "bolt_025"), group(2, [2], "bolt_025")]
    sends = [send(1, "bolt_025"), send(2, "bolt_025")]
    labels = {1: "bolt_025", 2: "bolt_025"}
    rep = run(tmp_path, parts, groups, sends, labels, parts_fed=4)
    assert rep.counts["undetected"] == 2
    assert rep.accuracy == 0.5  # 2 correct / (2 + 2 undetected)
    assert rep.n_parts_fed == 4
    assert check(rep, "Accuracy").verdict == FAIL


def test_on_seam_broken_out(tmp_path):
    parts = [part(1, on_seam=True), part(2), part(3, "nut_025", on_seam=True), part(4)]
    groups = [
        group(1, [1], "bolt_025"),
        group(2, [2], "bolt_025"),
        group(3, [3], "bolt_025"),  # wrong: it is a nut
        group(4, [4], "bolt_025"),
    ]
    sends = [send(i, "bolt_025") for i in range(1, 5)]
    labels = {1: "bolt_025", 2: "bolt_025", 3: "nut_025", 4: "bolt_025"}
    rep = run(tmp_path, parts, groups, sends, labels)
    assert rep.seam["on_seam"] == {"groups": 2, "correct": 1, "accuracy": 0.5}
    assert rep.seam["off_seam"]["accuracy"] == 1.0 and rep.seam["off_seam"]["groups"] == 2
    assert rep.accuracy == 0.75


def test_deadline_miss_and_send_error(tmp_path):
    parts = [part(1), part(2)]
    groups = [group(1, [1], "bolt_025"), group(2, [2], "bolt_025")]
    sends = [send(1, "bolt_025", err_ms=2.0), send(2, "misc", miss="late_decision", err_ms=40.0)]
    labels = {1: "bolt_025", 2: "bolt_025"}
    rep = run(tmp_path, parts, groups, sends, labels)
    assert rep.n_deadline_misses == 1 and rep.n_sends == 2
    assert check(rep, "Send deadline misses").verdict == FAIL
    assert rep.send_error_ms["max"] == pytest.approx(40.0)
    assert check(rep, "Send-time error (max, suggested)").verdict == FAIL
    assert rep.overall == FAIL


def test_latency_drops_and_thresholds(tmp_path):
    def rep_for(lat, n_dropped, stage_mean, sub):
        parts = [part(1, lat=lat)]
        root = make(
            tmp_path / sub,
            parts,
            [group(1, [1], "bolt_025")],
            [send(1, "bolt_025")],
            {1: "bolt_025"},
            end=end_rec(n_dropped=n_dropped, stage_mean=stage_mean),
        )
        return build_report([load_stats(root)])

    ok = rep_for(250.0, 0, 4.15, "ok")  # 2 stages x 4.15 = 8.3 ms: exactly at the limit
    assert check(ok, "Decision latency p99").verdict == PASS
    assert check(ok, "Mean processing time per frame").verdict == PASS
    assert check(ok, "Unexplained frame drops").verdict == PASS
    assert ok.overall == PASS and ok.fps == pytest.approx(120.0)
    assert ok.stages["segment"]["p99"] == 3.0

    assert check(rep_for(251.0, 0, 1.0, "a"), "Decision latency p99").verdict == FAIL
    assert check(rep_for(10.0, 0, 4.2, "b"), "Mean processing time per frame").verdict == FAIL
    assert check(rep_for(10.0, 1, 1.0, "c"), "Unexplained frame drops").verdict == FAIL


def test_accuracy_and_contamination_thresholds(tmp_path):
    def rep_for(n_ok, n_wrong, sub):
        n = n_ok + n_wrong
        parts = [part(i) for i in range(1, n + 1)]
        # wrong ones are nut_025 parts decided as misc (accuracy miss, no contamination)
        groups = [group(i, [i], "bolt_025" if i <= n_ok else "misc") for i in range(1, n + 1)]
        sends = [send(i, g["output"]) for i, g in enumerate(groups, 1)]
        labels = {i: "bolt_025" if i <= n_ok else "nut_025" for i in range(1, n + 1)}
        return run(tmp_path / sub, parts, groups, sends, labels)

    assert check(rep_for(19, 1, "p"), "Accuracy").verdict == PASS  # 95% exactly
    assert check(rep_for(18, 1, "q"), "Accuracy").verdict == FAIL  # 94.7%

    # 1 contaminated of 100 parts fed -> exactly 1%: PASS; 2 of 100 -> FAIL
    def contam(n_bad, sub):
        parts = [part(i) for i in range(1, 101)]
        groups = [group(i, [i], "nut_025" if i <= n_bad else "bolt_025") for i in range(1, 101)]
        sends = [send(i, g["output"]) for i, g in enumerate(groups, 1)]
        return run(tmp_path / sub, parts, groups, sends, dict.fromkeys(range(1, 101), "bolt_025"))

    assert check(contam(1, "c1"), "Bin contamination").verdict == PASS
    assert check(contam(2, "c2"), "Bin contamination").verdict == FAIL


def test_missing_session_end_is_na(tmp_path):
    root = make(
        tmp_path / "s",
        [part(1)],
        [group(1, [1], "bolt_025")],
        [],
        {1: "bolt_025"},
        end={"kind": "x"},
    )
    rep = build_report([load_stats(root)])
    assert check(rep, "Mean processing time per frame").verdict == NA


def test_multiple_sessions_aggregate(tmp_path):
    a = make(
        tmp_path / "a",
        [part(1)],
        [group(1, [1], "bolt_025")],
        [send(1, "bolt_025")],
        {1: "bolt_025"},
    )
    b = make(
        tmp_path / "b", [part(1)], [group(1, [1], "nut_025")], [send(1, "nut_025")], {1: "bolt_025"}
    )
    rep = build_report([load_stats(a), load_stats(b)])
    assert rep.accuracy == 0.5 and rep.n_parts_fed == 2 and rep.sessions == ["a", "b"]


def test_outputs_csv_and_self_contained_html(tmp_path):
    parts = [part(1), part(2, "nut_025")]
    groups = [group(1, [1], "bolt_025"), group(2, [2], "bolt_025")]
    sends = [send(1, "bolt_025"), send(2, "bolt_025")]
    root = make(tmp_path / "s", parts, groups, sends, {1: "bolt_025", 2: "nut_025"})
    out = tmp_path / "out"
    data, csv_path, html_path = generate([root], out)
    assert csv_path.parent == out and html_path.parent == out
    rows = list(csv.DictReader(io.StringIO(csv_path.read_text())))
    assert rows and set(rows[0]) == {"section", "item", "field", "value"}
    acc = next(
        r
        for r in rows
        if r["section"] == "targets" and r["item"] == "Accuracy" and r["field"] == "verdict"
    )
    assert acc["value"] == FAIL
    cell = next(
        r
        for r in rows
        if r["section"] == "confusion" and r["item"] == "nut_025" and r["field"] == "bolt_025"
    )
    assert cell["value"] == "1"
    html = html_path.read_text()
    assert not re.search(r"https?://|<link|<script|src=|@import|url\(", html)
    assert "<style>" in html and "Confusion matrix" in html and "FAIL" in html
    assert html == to_html(data) and to_csv(data) == csv_path.read_text()


def test_default_output_is_session_folder_and_cli(tmp_path, capsys):
    root = make(
        tmp_path / "s",
        [part(1)],
        [group(1, [1], "bolt_025")],
        [send(1, "bolt_025")],
        {1: "bolt_025"},
    )
    rc = main(["report", str(root)])
    out = capsys.readouterr().out
    assert (root / "report.html").is_file() and (root / "report.csv").is_file()
    assert "overall:" in out
    assert rc in (0, 1)
    args = build_parser().parse_args(["report", "a", "b", "--rerun", "--out", "x"])
    assert args.session == ["a", "b"] and args.rerun and args.out == "x"


def test_cli_missing_session(tmp_path, capsys):
    assert main(["report", str(tmp_path / "nope")]) == 2


def test_targets_dataclass_defaults():
    t = Targets()
    assert (t.accuracy_min, t.contamination_max, t.latency_p99_ms_max, t.mean_frame_ms_max) == (
        0.95,
        0.01,
        250.0,
        8.3,
    )
    assert PartClass("bolt_025")
