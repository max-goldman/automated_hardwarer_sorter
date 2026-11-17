"""Grading tool: the data layer (no Qt) and the Qt UI driven off-screen on a tiny synthetic session."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from hwsorter.cli import build_parser, main
from hwsorter.contracts import Output
from hwsorter.grading import (
    KIND_CLASS,
    KIND_EXCLUDED,
    KIND_MULTI,
    KIND_OUT_OF_SCOPE,
    GradingSession,
    LabelStore,
    grade_session,
    load_session,
)

NS = 1_000_000_000


def part_rec(tid, t_ref, lead, trail, verdict="confident", cls="bolt_025", crop=True):
    return {
        "t_ns": 1,
        "kind": "part",
        "track_id": tid,
        "verdict": verdict,
        "part_class": cls if verdict == "confident" else None,
        "score": 0.2,
        "flags": [],
        "t_ref_ns": t_ref,
        "x_lead_in_at_ref": 0.1,
        "belt_x_lead_in": lead,
        "belt_x_trail_in": trail,
        "crop_ref": f"crops/part_{tid:04d}.png" if crop else None,
        "frame_ref": f"crops/part_{tid:04d}_frame.png" if crop else None,
    }


def group_rec(gid, members, output, rule="x"):
    return {
        "t_ns": 1,
        "kind": "group",
        "group_id": gid,
        "member_track_ids": members,
        "output": output,
        "rule": rule,
        "t_ref_ns": 0,
        "x_lead_in_at_ref": 0.0,
        "t_closed_ns": 0,
    }


def send_rec(gid, output, miss="none"):
    return {"t_ns": 1, "kind": "send", "group_id": gid, "output": output, "miss": miss}


def write_session(root, parts, groups, sends=(), crops=True):
    (root / "decisions").mkdir(parents=True)
    lines = [{"t_ns": 0, "kind": "session_start", "session": root.name}]
    lines += parts + groups + list(sends) + [{"t_ns": 9, "kind": "session_end"}]
    (root / "decisions" / "decision_log.jsonl").write_text(
        "\n".join(json.dumps(r) for r in lines) + "\n"
    )
    if crops:
        (root / "crops").mkdir()
        for p in parts:
            if p["crop_ref"]:
                img = np.full((40, 30, 3), 128, np.uint8)
                cv2.imwrite(str(root / p["crop_ref"]), img)
                cv2.imwrite(str(root / p["frame_ref"]), np.full((60, 90, 3), 60, np.uint8))


@dataclass
class Sess:
    root: object
    data: object


@pytest.fixture
def session(tmp_path):
    """Four parts: 1 (lone confident bolt), 2+3 (a measured group: bolt + washer), 4 (lone OOS)."""
    root = tmp_path / "sess"
    parts = [
        part_rec(1, 1 * NS, 1.0, 0.5),
        part_rec(2, 2 * NS, 5.0, 4.5, cls="bolt_025"),
        part_rec(3, 2 * NS + 1000, 4.4, 3.9, cls="washer_025"),
        part_rec(4, 3 * NS, 9.0, 8.5, verdict="out_of_scope"),
    ]
    groups = [
        group_rec(1, [1], "bolt_025", "lone_confident"),
        group_rec(2, [2, 3], "rework", "rework"),
        group_rec(3, [4], "misc", "lone_misc"),
    ]
    sends = [send_rec(1, "bolt_025"), send_rec(2, "rework"), send_rec(3, "misc")]
    write_session(root, parts, groups, sends)
    return Sess(root, load_session(root))


# --------------------------------------------------------------------------- loading


def test_load_session_and_paths(session):
    d = session.data
    assert [p.track_id for p in d.parts] == [1, 2, 3, 4]
    assert d.parts[3].verdict == "out_of_scope" and d.parts[3].part_class is None
    assert d.group_of(3).member_track_ids == (2, 3)
    assert d.groups[1].sent_output == "rework"
    assert d.crop_path(d.parts[0]).is_file() and d.frame_path(d.parts[0]).is_file()
    assert d.image_path(None) is None


def test_load_accepts_log_file_session_folder_and_torn_tail(session, tmp_path):
    log = session.root / "decisions" / "decision_log.jsonl"
    with log.open("a") as fh:
        fh.write('{"kind": "part", "track_id"')  # torn last line of a live session
    for path in (session.root, log):
        d = load_session(path)
        assert len(d.parts) == 4 and d.crop_path(d.parts[0]) is not None
    with pytest.raises(FileNotFoundError):
        load_session(tmp_path / "nope")


def test_log_beside_crops_layout_and_last_run_wins(tmp_path):
    root = tmp_path / "flat"
    root.mkdir()
    (root / "crops").mkdir()
    cv2.imwrite(str(root / "crops" / "part_0001.png"), np.zeros((5, 5, 3), np.uint8))
    lines = [
        {"kind": "session_start"},
        part_rec(9, NS, 1.0, 0.5),
        {"kind": "session_start"},  # a second run appended to the same file
        part_rec(1, NS, 1.0, 0.5),
    ]
    (root / "run.jsonl").write_text("\n".join(json.dumps(r) for r in lines))
    d = load_session(root / "run.jsonl")
    assert [p.track_id for p in d.parts] == [1]
    assert d.crop_path(d.parts[0]) is not None


# --------------------------------------------------------------------------- store


def test_store_saves_every_action_and_resumes(session, tmp_path):
    path = tmp_path / "labels.jsonl"
    g = GradingSession(session.data, LabelStore(path))
    g.set_label(0, KIND_CLASS, "bolt_025")
    assert len(path.read_text().splitlines()) == 1  # saved at once
    g.set_label(1, KIND_OUT_OF_SCOPE)
    g.set_label(0, KIND_CLASS, "nut_025")  # go back and correct: appends, same key
    g.set_parts_fed(5)
    g.set_label(2, KIND_EXCLUDED)
    g.clear_label(2)
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["type"] for r in lines] == ["label", "label", "label", "run", "label", "unlabel"]
    assert all(r["v"] == 1 for r in lines)
    assert len({r["key"] for r in lines if r["type"] == "label"}) == 3 - 1 + 1  # 0 reused, 1, 2

    g2 = GradingSession(session.data, LabelStore(path))  # resume
    assert g2.label_of(0).part_class == "nut_025"
    assert g2.label_of(1).kind == KIND_OUT_OF_SCOPE
    assert g2.label_of(2) is None and g2.label_of(3) is None
    assert g2.store.parts_fed == 5
    # a torn tail does not break resuming
    with path.open("a") as fh:
        fh.write('{"v":1,"type":"lab')
    assert len(LabelStore(path)) == 2


def test_label_validation():
    from hwsorter.grading import Label

    with pytest.raises(ValueError):
        Label("k", 1, 0.0, 0.0, "bogus")
    with pytest.raises(ValueError):
        Label("k", 1, 0.0, 0.0, KIND_CLASS, part_class="gear")
    with pytest.raises(ValueError):
        Label("k", 1, 0.0, 0.0, KIND_MULTI, identities=("gear", None))


# --------------------------------------------------------------------------- anchoring / rematch


def test_labels_anchor_to_time_and_belt_position_not_track_id(session, tmp_path):
    path = tmp_path / "labels.jsonl"
    g = GradingSession(session.data, LabelStore(path))
    for i, cls in enumerate(["bolt_025", "bolt_025", "washer_025"]):
        g.set_label(i, KIND_CLASS, cls)
    g.set_label(3, KIND_OUT_OF_SCOPE)
    rec = json.loads(path.read_text().splitlines()[0])
    assert (rec["t_ref_ns"], rec["belt_x_lead_in"], rec["belt_x_trail_in"]) == (NS, 1.0, 0.5)

    # simulated re-run: new track ids, t_ref shifted a bit, one part not detected, one new one
    root2 = tmp_path / "rerun"
    parts = [
        part_rec(11, 1 * NS + 120_000_000, 1.05, 0.52),
        part_rec(12, 2 * NS - 90_000_000, 4.95, 4.48),
        part_rec(13, 2 * NS + 50_000, 4.4, 3.9),
        part_rec(14, 6 * NS, 20.0, 19.5),  # new phantom detection
    ]  # the OOS part (old track 4) is gone
    write_session(root2, parts, [group_rec(1, [11], "bolt_025"), group_rec(2, [12, 13], "rework")])
    d2 = load_session(root2)
    g2 = GradingSession(d2, LabelStore(path))
    assert [g2.label_of(i).part_class if g2.label_of(i) else None for i in range(4)] == [
        "bolt_025",
        "bolt_025",
        "washer_025",
        None,
    ]
    m = g2.match
    assert m.unlabeled_parts == [3]
    assert [lab.kind for lab in m.unmatched_labels] == [KIND_OUT_OF_SCOPE]
    # relabelling a re-matched part keeps its original anchor, so it stays matched
    g2.set_label(0, KIND_CLASS, "nut_025")
    assert g2.label_of(0).t_ref_ns == NS and len(g2.store) == 4


def test_rematch_is_one_to_one_and_respects_tolerances():
    @dataclass
    class P:
        t_ref_ns: int
        belt_x_lead_in: float
        belt_x_trail_in: float

    store = LabelStore()
    from hwsorter.grading import Label

    store.put(Label("a", 1 * NS, 2.0, 1.0, KIND_CLASS, part_class="bolt_025"))
    store.put(Label("b", 1 * NS, 2.2, 1.2, KIND_CLASS, part_class="nut_025"))
    parts = [P(1 * NS, 2.1, 1.1), P(1 * NS, 9.0, 8.0), P(5 * NS, 2.0, 1.0)]
    m = store.rematch(parts)
    assert len(m.matched) == 1 and set(m.matched.values()) == {0}  # one part, one label
    assert [lab.key for lab in m.unmatched_labels] in (["a"], ["b"])
    assert m.unlabeled_parts == [1, 2]  # far in belt-x / far in time


# --------------------------------------------------------------------------- correct output


def test_correct_output_from_labels_and_grouping(session, tmp_path):
    g = GradingSession(session.data, LabelStore())
    g.set_label(0, KIND_CLASS, "bolt_025")
    g.set_label(1, KIND_CLASS, "bolt_025")
    g.set_label(2, KIND_CLASS, "washer_025")
    g.set_label(3, KIND_OUT_OF_SCOPE)
    rep = g.report()
    assert [(x.correct, x.status) for x in rep.groups] == [
        (Output.BOLT_025, "correct"),
        (Output.REWORK, "correct"),
        (Output.MISC, "correct"),
    ]
    assert rep.complete and rep.accuracy == 1.0 and rep.n_misses == 0

    # the pipeline was wrong about part 3 (really a bolt): the group's correct bin is bolt_025
    g.set_label(2, KIND_CLASS, "bolt_025")
    r = g.report().groups[1]
    assert (r.predicted, r.correct, r.status) == (Output.REWORK, Output.BOLT_025, "wrong")
    assert g.report().accuracy == pytest.approx(2 / 3)


def test_incomplete_phantom_and_late_send(tmp_path):
    root = tmp_path / "s"
    parts = [
        part_rec(1, NS, 1.0, 0.5),
        part_rec(2, 2 * NS, 5.0, 4.5),
        part_rec(3, 3 * NS, 9.0, 8.5),
    ]
    groups = [group_rec(i, [i], "bolt_025") for i in (1, 2, 3)]
    write_session(root, parts, groups, [send_rec(1, "misc", miss="late_decision")])
    g = GradingSession(load_session(root), LabelStore())
    g.set_label(0, KIND_CLASS, "bolt_025")
    g.set_label(2, KIND_EXCLUDED)
    rep = g.report()
    assert [x.status for x in rep.groups] == ["correct", "incomplete", "phantom"]
    assert rep.groups[0].sent == Output.MISC and rep.groups[0].sent_correct is False
    assert rep.groups[1].sent_correct is None
    assert rep.n_phantom_parts == 1 and rep.n_unlabeled_parts == 1 and not rep.complete


def test_several_parts_crop_and_parts_fed_misses(session):
    g = GradingSession(session.data, LabelStore())
    g.set_label(0, KIND_CLASS, "bolt_025")
    # one crop that is really two parts (a bolt and a washer) measured as a single track
    g.set_label(1, KIND_MULTI, identities=("bolt_025", "washer_025"))
    g.set_label(2, KIND_EXCLUDED)  # the second track in that group is a phantom
    g.set_label(3, KIND_OUT_OF_SCOPE)
    rep = g.report()
    assert rep.groups[1].correct == Output.REWORK  # bolt + washer -> rework
    assert rep.groups[1].status == "correct"
    assert rep.n_detected_parts == 1 + 2 + 0 + 1 and rep.n_phantom_parts == 1

    # same class twice in one crop: its class bin
    g.set_label(1, KIND_MULTI, identities=("bolt_025", "bolt_025"))
    assert g.report().groups[1].correct == Output.BOLT_025
    # all out of scope: MISC
    g.set_label(1, KIND_MULTI, identities=(None, None))
    assert g.report().groups[1].correct == Output.MISC

    assert g.report().n_undetected is None  # parts fed not given yet
    g.set_parts_fed(7)
    rep = g.report()
    assert rep.parts_fed == 7 and rep.n_detected_parts == 4
    assert rep.n_undetected == 3  # fed 7, only 4 real parts were detected: 3 misses
    assert rep.n_misses == rep.count("wrong") + 3
    graded = rep.count("correct") + rep.count("wrong") + rep.count("phantom")
    assert rep.accuracy == pytest.approx(rep.count("correct") / (graded + 3))
    g.set_parts_fed(2)
    rep = g.report()
    assert rep.n_undetected == 0 and rep.n_overcount == 2


def test_grade_session_function_matches_session_report(session):
    store = LabelStore()
    g = GradingSession(session.data, store)
    g.set_label(0, KIND_CLASS, "bolt_025")
    assert grade_session(session.data, store).groups == g.report().groups


# --------------------------------------------------------------------------- Qt UI

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402


@pytest.fixture
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(["test"])


def open_window(session, labels):
    from hwsorter.gui.grading import open_grading

    win = open_grading(session.root, labels)
    win.show()
    return win


def stored(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_ui_keys_label_save_and_go_back(session, tmp_path, qapp):
    path = tmp_path / "ui_labels.jsonl"
    win = open_window(session, path)
    try:
        assert "Part 1/4" in win.info.text() and "bolt_025" in win.info.text()
        assert win.crop.pixmap() is not None and not win.crop.pixmap().isNull()
        assert not win.frame.pixmap().isNull()
        QTest.keyClick(win, Qt.Key_Return)  # confirm the predicted identity (bolt_025)
        assert stored(path)[-1]["part_class"] == "bolt_025" and win.index == 1
        QTest.keyClick(win, Qt.Key_5)  # washer_025 for part 2
        assert stored(path)[-1]["part_class"] == "washer_025" and win.index == 2
        QTest.keyClick(win, Qt.Key_X)  # exclude part 3
        assert stored(path)[-1]["kind"] == KIND_EXCLUDED and win.index == 3
        QTest.keyClick(win, Qt.Key_Return)  # predicted out_of_scope
        assert stored(path)[-1]["kind"] == KIND_OUT_OF_SCOPE and win.index == 3  # last part
        QTest.keyClick(win, Qt.Key_Left)  # go back, nothing written
        n = len(stored(path))
        assert win.index == 2 and len(stored(path)) == n
        QTest.keyClick(win, Qt.Key_Left)
        QTest.keyClick(win, Qt.Key_2)  # correct part 2 -> bolt_0375
        recs = stored(path)
        assert recs[-1]["part_class"] == "bolt_0375" and recs[-1]["key"] == recs[1]["key"]
        QTest.keyClick(win, Qt.Key_Left)  # now on part 2 (index 1), after the auto-advance
        QTest.keyClick(win, Qt.Key_Left)  # part 1
        QTest.keyClick(win, Qt.Key_Delete)
        assert stored(path)[-1]["type"] == "unlabel"
        assert not win.grab().isNull()
    finally:
        win.close()
    # resume in a fresh window
    win2 = open_window(session, path)
    try:
        assert win2.grading.label_of(1).part_class == "bolt_0375"
        assert win2.grading.label_of(0) is None
    finally:
        win2.close()


def test_ui_several_parts_mode_and_parts_fed(session, tmp_path, qapp):
    path = tmp_path / "ui_labels.jsonl"
    win = open_window(session, path)
    try:
        QTest.keyClick(win, Qt.Key_Right)
        QTest.keyClick(win, Qt.Key_M)
        assert win.multi == []
        QTest.keyClick(win, Qt.Key_1)
        QTest.keyClick(win, Qt.Key_Return)  # only one identity: refused
        assert win.multi == ["bolt_025"] and not path.exists()
        QTest.keyClick(win, Qt.Key_0)  # an out-of-scope part
        QTest.keyClick(win, Qt.Key_6)
        QTest.keyClick(win, Qt.Key_Backspace)
        QTest.keyClick(win, Qt.Key_Return)
        rec = stored(path)[-1]
        assert rec["kind"] == KIND_MULTI and rec["identities"] == ["bolt_025", None]
        assert win.multi is None and win.index == 2
        QTest.keyClick(win, Qt.Key_M)  # cancel leaves the store alone
        QTest.keyClick(win, Qt.Key_3)
        QTest.keyClick(win, Qt.Key_Escape)
        assert win.multi is None and len(stored(path)) == 1

        win.fed.setValue(6)
        win.fed.editingFinished.emit()
        assert stored(path)[-1] == {"v": 1, "type": "run", "parts_fed": 6}
        QTest.keyClick(win, Qt.Key_3)  # keys still reach the window after the spin box
        assert stored(path)[-1]["part_class"] == "nut_025"
        rep = win.grading.report()
        assert (
            rep.parts_fed == 6 and rep.n_detected_parts == 1 + 2 + 1 + 1 and rep.n_undetected == 1
        )
    finally:
        win.close()


def test_ui_unresolved_part_needs_explicit_label(tmp_path, qapp):
    root = tmp_path / "u"
    write_session(
        root, [part_rec(1, NS, 1.0, 0.5, verdict="unresolved")], [group_rec(1, [1], "misc")]
    )
    from hwsorter.gui.grading import open_grading

    win = open_grading(root)
    win.show()
    try:
        QTest.keyClick(win, Qt.Key_Return)
        assert not (root / "labels.jsonl").exists()
        assert "no prediction" in win.status.text()
        QTest.keyClick(win, Qt.Key_4)
        assert (root / "labels.jsonl").exists()  # default store location: <session>/labels.jsonl
    finally:
        win.close()


def test_ui_missing_images_and_empty_session(tmp_path, qapp):
    root = tmp_path / "n"
    write_session(root, [part_rec(1, NS, 1.0, 0.5, crop=False)], [group_rec(1, [1], "bolt_025")])
    win = open_window(Sess(root, None), tmp_path / "l.jsonl")
    try:
        assert "no crop" in win.crop.text()
    finally:
        win.close()
    root2 = tmp_path / "e"
    write_session(root2, [], [])
    win = open_window(Sess(root2, None), tmp_path / "l2.jsonl")
    try:
        QTest.keyClick(win, Qt.Key_1)  # nothing to label: no crash
        assert "no parts" in win.info.text()
    finally:
        win.close()


# --------------------------------------------------------------------------- CLI


def test_cli_grade_parser(session):
    args = build_parser().parse_args(["grade", str(session.root), "--labels", "x.jsonl"])
    assert args.func.__name__ == "_cmd_grade" and args.labels == "x.jsonl"


def test_cli_grade_opens_the_tool(session, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "hwsorter.gui.grading.run_grading", lambda p, labels=None: seen.update(p=p, l=labels) or 0
    )
    assert main(["grade", str(session.root)]) == 0
    assert str(seen["p"]) == str(session.root) and seen["l"] is None
