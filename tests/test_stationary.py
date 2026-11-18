"""Stationary-belt mode: synthetic still scenes (no camera) through the real pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from fakes import make_part_result

from hwsorter.cli import main
from hwsorter.clock import ManualClock
from hwsorter.config import SorterConfig, load_config, with_stationary
from hwsorter.contracts import Output, normalise_for_compare
from hwsorter.decide.rules import StationaryDecider
from hwsorter.eventlog import EventLog
from hwsorter.frames import Frame
from hwsorter.output import MockSerialAdapter
from hwsorter.pipeline import Pipeline, synthetic_calibration
from hwsorter.synthetic import (
    PART_DIMS,
    FingerSpec,
    HandSpec,
    PartSpec,
    SceneConfig,
    SyntheticRenderer,
    still_scene,
)

START_NS = 1_000_000_000
SCENE = SceneConfig(speed_in_s=0.0)


@dataclass
class Run:
    pipeline: Pipeline
    log: EventLog
    adapter: MockSerialAdapter
    snapshots: list

    def records(self, kind: str) -> list[dict]:
        return self.log.recent(kind)

    def last(self):
        return [s for s in self.snapshots if s.processed][-1]

    def groups(self) -> list[tuple[list[int], str]]:
        return [(g["member_track_ids"], g["output"]) for g in self.records("group")]


@pytest.fixture(scope="module")
def rig():
    cal, flat = synthetic_calibration(SCENE)
    return cal, flat


def stationary_cfg() -> SorterConfig:
    return with_stationary(load_config())


def run_still(
    rig,
    parts: list[PartSpec],
    hands: list[HandSpec] | None = None,
    seconds: float = 4.0,
    cfg: SorterConfig | None = None,
    session_dir: Path | None = None,
    log_path: Path | None = None,
    fingers: list[FingerSpec] | None = None,
) -> Run:
    cal, flat = rig
    cfg = cfg or stationary_cfg()
    renderer = SyntheticRenderer(SCENE, parts, hands, fingers)
    log, adapter = EventLog(log_path), MockSerialAdapter()
    pl = Pipeline(
        cfg,
        cal,
        ManualClock(0),
        adapter,
        log,
        flat=flat,
        session_dir=session_dir,
        image_size=(SCENE.width, SCENE.height),
    )
    dummy = np.zeros((SCENE.height, SCENE.width), np.uint8)
    snaps = []
    for i in range(int(seconds * SCENE.fps)):
        t = START_NS + round(i * 1e9 / SCENE.fps)
        img = renderer.render(i / SCENE.fps, i) if i % pl.frame_stride == 0 else dummy
        snaps.append(pl.process(Frame(i, t, t, img)))
    pl.close()
    log.close()
    return Run(pl, log, adapter, snaps)


def still(kind: str, t: float, x: float, off: float | None = None, angle: float = 0.0):
    return PartSpec(kind, t, 1.0, angle, x_in=x, t_off_s=off)


# --------------------------------------------------------------------------- scenes


def test_six_classes_one_at_a_time(rig):
    kinds = ("bolt_025", "nut_0312", "washer_025", "bolt_0312", "nut_025", "washer_0312")
    # Each part is taken away 0.3 s before the next is put down on the same spot: shorter than
    # stationary.missed_s, so the old track is still live and must notice the new part on reappear.
    parts = [
        still(k, 0.5 + 1.4 * i, 1.75, off=0.5 + 1.4 * i + 1.1, angle=30.0 * i)
        for i, k in enumerate(kinds)
    ]
    run = run_still(rig, parts, seconds=0.5 + 1.4 * len(kinds) + 0.5)
    assert [(m, o) for m, o in run.groups()] == [([i + 1], k) for i, k in enumerate(kinds)]
    assert len(run.records("part")) == len(kinds)
    assert [g["rule"] for g in run.records("group")] == ["lone_confident"] * len(kinds)


def test_no_sends_and_decisions_are_announced(rig):
    run = run_still(rig, [still("nut_025", 0.4, 1.75)], seconds=2.2)
    assert run.records("send") == []
    assert run.adapter.sent == []
    assert [(o.output, rule) for o, rule in run.adapter.announced] == [
        (Output.NUT_025, "lone_confident")
    ]
    (group,) = run.records("group")
    assert group["stationary"] is True
    end = run.records("session_end")[0]
    assert end["n_sends"] == 0 and end["n_groups"] == 1 and end["n_parts"] == 1
    assert run.records("session_start")[0]["belt_speed_in_s"] == 0.0


def test_a_part_is_decided_once_while_it_sits(rig):
    run = run_still(rig, [still("bolt_0312", 0.4, 1.75)], seconds=6.0)
    assert run.groups() == [([1], "bolt_0312")]
    tracks = [v for s in run.snapshots[-20:] for v in s.tracks]
    assert tracks and all(v.track_id == 1 and v.measured for v in tracks)
    assert all(v.group_output is Output.BOLT_0312 for v in tracks)


def test_two_parts_close_together_are_one_rework_group(rig):
    parts = [still("bolt_025", 0.4, 1.0), still("washer_025", 0.4, 2.5)]  # 0.6 in apart
    run = run_still(rig, parts, seconds=2.4)
    assert run.groups() == [([1, 2], "rework")]
    assert run.records("group")[0]["rule"] == "rework"
    assert len(run.records("part")) == 2


def test_removed_part_does_not_redecide_untouched_parts(rig):
    parts = [
        still("nut_0312", 0.4, 0.8),
        still("washer_025", 0.4, 2.9, off=2.4),  # taken away; far enough to be its own group
    ]
    run = run_still(rig, parts, seconds=4.2)
    assert run.groups() == [([1], "nut_0312"), ([2], "washer_025")]  # nothing new after the removal
    assert len(run.records("part")) == 2
    assert [v.track_id for v in run.last().tracks] == [1]


def test_pair_then_removal_redecides_the_remaining_part(rig):
    parts = [still("bolt_025", 0.4, 1.0), still("washer_025", 0.4, 2.5, off=2.6)]
    run = run_still(rig, parts, seconds=4.6)
    assert run.groups() == [([1, 2], "rework"), ([1], "bolt_025")]
    assert len(run.records("part")) == 2  # the bolt is not logged a second time
    ids = [g["group_id"] for g in run.records("group")]
    assert ids == [1, 2]
    assert [g["supersedes"] for g in run.records("group")] == [[], [1]]


def test_swap_before_measure_is_decided_as_the_new_part(rig):
    # A is covered by a hand while still UNMEASURED (placed 0.40 s, hand on 0.45 s, well short of
    # settle_s), taken away, and B put down in its place. B must get a fresh track.
    parts = [
        still("bolt_025", 0.40, 1.75, off=0.8),
        still("washer_0312", 0.85, 1.75),
    ]
    run = run_still(rig, parts, [HandSpec(0.45, 1.0, 1.75, y_in=1.2)], seconds=3.5)
    assert run.groups() == [([2], "washer_0312")]
    (part,) = run.records("part")
    assert part["track_id"] == 2 and part["part_class"] == "washer_0312"


def test_fast_swap_under_a_short_hand_is_a_new_track(rig):
    # The hand is out of view for less than the reappear gap, but A was unseen while it was in.
    parts = [
        still("bolt_025", 0.40, 1.75, off=0.62),
        still("washer_0312", 0.66, 1.75),
    ]
    run = run_still(rig, parts, [HandSpec(0.55, 0.64, 1.75, y_in=1.2)], seconds=3.5)
    assert run.groups() == [([2], "washer_0312")]
    assert run.records("part")[0]["track_id"] == 2


def test_quick_nut_to_washer_swap_is_remeasured(rig):
    # The nut is decided, taken away and a washer put down on the same spot 0.15 s later (less
    # than missed_s): the washer must get its own track and decision, never the nut's.
    parts = [still("nut_0312", 0.4, 1.75, off=1.6), still("washer_025", 1.75, 1.75)]
    run = run_still(rig, parts, seconds=3.4)
    assert run.groups() == [([1], "nut_0312"), ([2], "washer_025")]
    assert [p["part_class"] for p in run.records("part")] == ["nut_0312", "washer_025"]


def test_swap_with_the_same_silhouette_is_caught_by_the_hole(rig, monkeypatch):
    # A washer whose outline matches the nut's (same area, centred IoU ~0.93): only the hole kind
    # (see-through vs dark bore) tells them apart on reappear.
    monkeypatch.setitem(PART_DIMS, "washer_hx", {"od": 0.525, "id": 0.26})
    parts = [still("nut_0312", 0.4, 1.75, off=1.6), still("washer_hx", 1.75, 1.75)]
    run = run_still(rig, parts, seconds=3.4)
    (first, *rest) = run.groups()
    assert first == ([1], "nut_0312")
    assert [m for m, _ in rest] == [[2]] and rest[0][1] != "nut_0312"
    assert len(run.records("part")) == 2


def test_swap_under_a_hand_after_measuring_is_remeasured(rig):
    # The nut is measured and decided; a hand covers it, the nut is swapped for a washer under
    # the hand: on reappear the washer is a new part.
    parts = [still("nut_025", 0.4, 1.75, off=2.2), still("washer_0312", 2.3, 1.75)]
    run = run_still(rig, parts, [HandSpec(2.0, 2.6, 1.75, y_in=1.6)], seconds=4.4)
    assert run.groups() == [([1], "nut_025"), ([2], "washer_0312")]


def test_a_nudged_part_keeps_its_one_decision(rig):
    # A bolt slid 0.4 in along the belt (more than relocate_in) while in plain view, smoothly:
    # the same part, so one track and one decision.
    bolt = PartSpec("bolt_025", 0.4, 1.0, 0.0, x_in=1.4, slide=(1.6, 2.4, 0.4))
    run = run_still(rig, [bolt], seconds=3.6)
    assert run.groups() == [([1], "bolt_025")]
    assert len(run.records("part")) == 1
    assert [v.track_id for v in run.last().tracks] == [1]


def test_fingers_at_the_edge_are_never_parts(rig):
    # A bolt is decided; then fingers reach in from the right image edge (too small for a hand),
    # moving and growing, with a detached fingertip blob, and leave. No finger part or group.
    fingers = [
        FingerSpec(1.5, 3.0, 1.0, reach_in=1.0, reach0_in=0.2, grow_s=0.8, tip_gap_in=0.06),
        FingerSpec(1.7, 3.0, 0.4, reach_in=0.7, reach0_in=0.1, grow_s=0.6),
    ]
    run = run_still(rig, [still("bolt_025", 0.4, 1.0)], seconds=4.0, fingers=fingers)
    assert run.groups() == [([1], "bolt_025")]
    assert len(run.records("part")) == 1
    snaps = [s for s in run.snapshots if s.processed]
    assert any(s.n_edge >= 2 for s in snaps)  # the bar and its detached tip
    assert all(not s.hand for s in snaps)  # fingers are not a hand
    assert all(v.track_id == 1 or v.at_edge for s in snaps for v in s.tracks)


def test_moving_fingers_hold_decisions(rig):
    # Fingers reach in (1 in/s) while a nut is put down, stop short of it, rest and leave. The
    # nut is decided only once they have been still for after_hand_s; they never become a part.
    fingers = [FingerSpec(0.3, 2.6, 1.0, reach_in=1.4, reach0_in=0.2, grow_s=1.2)]
    run = run_still(rig, [still("nut_025", 0.4, 1.3)], seconds=3.4, fingers=fingers)
    assert run.groups() == [([1], "nut_025")]
    (group,) = run.records("group")
    settle = load_config().stationary.after_hand_s
    assert group["t_closed_ns"] >= START_NS + round((1.5 + settle) * 1e9) - 60_000_000
    assert any(s.edge_hold for s in run.snapshots if s.processed)


def test_a_static_part_at_the_edge_is_reported_never_decided(rig):
    # A bolt half out of view at the right image edge sits there; a nut elsewhere is still
    # decided (the static edge blob does not block decisions), the edge bolt never is.
    edge_x = SCENE.width / SCENE.px_per_in - 0.2
    parts = [still("bolt_025", 0.2, edge_x), still("nut_0312", 0.8, 1.0)]
    run = run_still(rig, parts, seconds=3.0)
    assert [o for _, o in run.groups()] == ["nut_0312"]
    assert len(run.records("part")) == 1
    assert run.last().n_edge == 1 and not run.last().edge_hold


def test_decision_waits_after_a_hand_leaves(rig):
    run = run_still(
        rig, [still("nut_025", 0.3, 1.75)], [HandSpec(0.6, 1.0, 1.75, y_in=1.2)], seconds=3.0
    )
    (group,) = run.records("group")
    after = round(load_config().stationary.after_hand_s * 1e9)
    assert group["t_closed_ns"] >= START_NS + round(1.0e9) + after - 20_000_000


def test_superseded_decisions_are_counted_once(rig, tmp_path):
    from hwsorter.grading import KIND_CLASS, Label, LabelStore, load_session
    from hwsorter.grading.grade import grade_session
    from hwsorter.report import build_report, load_stats

    parts = [still("bolt_025", 0.4, 1.0), still("washer_025", 0.4, 2.5, off=2.6)]
    log_path = tmp_path / "decisions" / "decision_log.jsonl"
    run_still(rig, parts, seconds=4.6, log_path=log_path)
    session = load_session(tmp_path)
    assert [g.output for g in session.groups] == ["bolt_025"]  # only the latest decision
    assert [g.output for g in session.history] == ["rework"]
    assert session.group_of(1).group_id == 2 and session.group_of(2) is None
    store = LabelStore(tmp_path / "labels.jsonl")
    for p, cls in zip(session.parts, ("bolt_025", "washer_025"), strict=True):
        store.put(
            Label(
                key=f"{p.t_ref_ns}:{p.belt_x_lead_in:.3f}",
                t_ref_ns=p.t_ref_ns,
                belt_x_lead_in=p.belt_x_lead_in,
                belt_x_trail_in=p.belt_x_trail_in,
                kind=KIND_CLASS,
                track_id=p.track_id,
                part_class=cls,
            )
        )
    store.set_parts_fed(2)
    assert len(grade_session(session, store).groups) == 1
    report = build_report([load_stats(tmp_path)])
    assert report.n_groups == 1 and report.accuracy == 1.0


def test_config_cross_checks():
    base = load_config().model_dump()

    def bad(**kw):
        data = {**base, "stationary": {**base["stationary"], **kw}}
        with pytest.raises(ValueError):
            SorterConfig.model_validate(data)

    bad(move_tol_in=0.2)  # not below relocate_in
    bad(relocate_in=0.6)  # not below track.gate_in
    bad(give_up_s=0.1)  # below settle_s
    bad(missed_s=0.0)


def test_moved_part_is_remeasured_as_a_new_track(rig):
    a = still("bolt_025", 0.4, 1.0, off=2.5)
    b = still("bolt_025", 2.6, 2.6)  # the same bolt put down elsewhere
    run = run_still(rig, [a, b], seconds=4.8)
    assert run.groups() == [([1], "bolt_025"), ([2], "bolt_025")]


def test_hand_blocks_decisions_and_makes_no_parts(rig):
    # an empty belt with an arm over it: nothing is tracked, measured or decided
    run = run_still(rig, [], [HandSpec(0.5, 2.5, 1.7, y_in=1.2)], seconds=3.5)
    assert run.records("part") == [] and run.records("group") == []
    assert any(s.hand for s in run.snapshots)
    assert all(not s.tracks for s in run.snapshots)


def test_part_placed_under_a_hand_is_decided_after_it_leaves(rig):
    t_hand = (0.4, 2.0)
    run = run_still(
        rig,
        [still("washer_0312", 0.4, 1.2)],
        [HandSpec(*t_hand, 1.2, y_in=1.2)],
        seconds=4.0,
    )
    assert run.groups() == [([1], "washer_0312")]
    group = run.records("group")[0]
    assert group["t_closed_ns"] >= START_NS + round(t_hand[1] * 1e9)
    assert run.records("part")[0]["track_id"] == 1


def test_hand_over_a_measured_part_changes_nothing(rig):
    run = run_still(
        rig,
        [still("nut_025", 0.4, 1.75)],
        [HandSpec(2.2, 3.2, 1.75, y_in=1.2)],
        seconds=5.0,
    )
    assert run.groups() == [([1], "nut_025")]
    assert len(run.records("part")) == 1
    final = [v for v in run.last().tracks]
    assert [v.track_id for v in final] == [1]


def test_demo_scene_decisions_and_determinism(rig):
    parts, hands, seconds = still_scene(SCENE)
    first = run_still(rig, parts, hands, seconds=seconds)
    outs = [o for _, o in first.groups()]
    assert outs == [
        "bolt_025",
        "nut_0312",
        "washer_025",
        "bolt_0312",
        "nut_025",
        "washer_0312",
        "washer_025",
        "bolt_025",
        "rework",
        "bolt_025",
    ]
    second = run_still(rig, parts, hands, seconds=seconds)
    assert normalise_for_compare(first.log.recent()) == normalise_for_compare(second.log.recent())


def test_cli_run_stationary_then_grade_and_report(tmp_path, capsys):
    from hwsorter.grading import KIND_CLASS, Label, LabelStore, load_session
    from hwsorter.grading.grade import grade_session
    from hwsorter.report import build_report, load_stats

    log_path = tmp_path / "decisions" / "decision_log.jsonl"
    code = main(
        [
            "run",
            "--source",
            "synthetic",
            "--headless",
            "--fast",
            "--stationary",
            "--seconds",
            "4.5",
            "--decision-log",
            str(log_path),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "DECISION group 1 [#1] -> bolt_025 (lone_confident)" in out
    assert "SEND" not in out
    session = load_session(tmp_path)
    assert [g.output for g in session.groups] == ["bolt_025", "nut_0312", "washer_025"]
    assert all(g.sent_output is None for g in session.groups)
    assert session.start["config"]["belt"]["mode"] == "stationary"

    truth = {1: "bolt_025", 2: "nut_0312", 3: "washer_025"}
    store = LabelStore(tmp_path / "labels.jsonl")
    for p in session.parts:
        store.put(
            Label(
                key=f"{p.t_ref_ns}:{p.belt_x_lead_in:.3f}",
                t_ref_ns=p.t_ref_ns,
                belt_x_lead_in=p.belt_x_lead_in,
                belt_x_trail_in=p.belt_x_trail_in,
                kind=KIND_CLASS,
                track_id=p.track_id,
                part_class=truth[p.track_id],
            )
        )
    store.set_parts_fed(3)
    graded = grade_session(session, store)
    assert [g.status for g in graded.groups] == ["correct"] * 3
    report = build_report([load_stats(tmp_path)])
    assert report.accuracy == 1.0 and report.contamination == 0.0
    assert report.n_sends == 0


# --------------------------------------------------------------------------- config and units


def test_moving_mode_session_start_has_no_stationary_keys(rig):
    cal, flat = rig
    log = EventLog()
    pl = Pipeline(
        load_config(),
        cal,
        ManualClock(0),
        MockSerialAdapter(),
        log,
        flat=flat,
        image_size=(SCENE.width, SCENE.height),
    )
    img = np.zeros((SCENE.height, SCENE.width), np.uint8)
    pl.process(Frame(0, START_NS, START_NS, img))
    pl.close()
    cfg = log.recent("session_start")[0]["config"]
    assert "stationary" not in cfg and "mode" not in cfg["belt"]
    assert load_config().belt.mode == "moving"


def test_stationary_decider_decides_a_group_once_per_membership():
    d = StationaryDecider(1.0)
    a = make_part_result(1, belt_x_lead_in=1.0, belt_x_trail_in=0.5)
    b = make_part_result(2, belt_x_lead_in=1.8, belt_x_trail_in=1.4)
    far = make_part_result(3, belt_x_lead_in=9.0, belt_x_trail_in=8.5)
    d.add(a)
    d.add(far)
    first = d.update(0)
    assert sorted(x.member_track_ids for x in first) == [(1,), (3,)]
    assert d.update(1) == []  # nothing changed: nothing re-decided
    d.add(b)
    again = d.update(2)
    assert [x.member_track_ids for x in again] == [(1, 2)]  # only the changed group
    d.remove(2)
    assert [x.member_track_ids for x in d.update(3)] == [(1,)]
    d.remove(1)
    d.remove(3)
    assert d.update(4) == [] and d.current() == []
