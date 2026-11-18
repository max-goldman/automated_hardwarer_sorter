"""`sorter run` visibility: status line, live window, clean shutdown, --record (offscreen Qt)."""

from __future__ import annotations

import json
import shutil

import pytest

from hwsorter.cli import main, resolve_recording
from hwsorter.config import load_config, project_root
from hwsorter.contracts import Output
from hwsorter.pipeline import PendingSend, PipelineSnapshot, TrackView
from hwsorter.recording import RecordingReader
from hwsorter.status import format_status, hand_text, last_decision

NS = 10**9


def view(tid: int, n: int, measured: bool) -> TrackView:
    return TrackView(tid, None, 0.0, 0.0, 0, n, None, None, None, False, measured=measured)


def snap(**kw) -> PipelineSnapshot:
    base = dict(seq=1, t_capture_ns=NS, processed=True, fps=119.6, stationary=True)
    base.update(kw)
    return PipelineSnapshot(**base)


class FakeLog:
    def __init__(self, groups=(), parts=()):
        self._r = {"group": list(groups), "part": list(parts)}

    def recent(self, kind=None):
        return self._r.get(kind, [])


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    for name in ("default.toml", "parts_library.yaml"):
        shutil.copy(project_root() / "config" / name, tmp_path / "config" / name)
    monkeypatch.setenv("HWSORTER_ROOT", str(tmp_path))
    monkeypatch.setenv("HWSORTER_MACHINE", "testbench")
    return tmp_path


def test_status_stationary_shows_measuring_progress():
    s = snap(tracks=(view(1, 10, True), view(2, 6, False)), feature_target=10)
    line = format_status(s, 12.3, "group 3 [#1] -> nut (lone_confident)")
    assert line.startswith("t=12.3s  in view: 2 parts (1 measured, 1 measuring #2 6/10 frames)")
    assert "hand: no" in line and "fps 120" in line and "last: group 3" in line


def test_status_stationary_hand_is_diagnosable():
    s = snap(hand=True, hand_area_in2=2.31, hand_edge="image", hand_max_in2=1.5)
    line = format_status(s, 1.0)
    assert "HAND in view: holding decisions" in line
    assert "2.31 in2 > 1.50 max" in line and "touches the image edge" in line
    assert hand_text(snap()) == ""


def test_status_moving_shows_pending_and_late():
    pending = (PendingSend(4, Output.MISC, 0, 1.25),)
    line = format_status(snap(stationary=False, pending=pending), 3.0, late=2)
    assert "pending sends: g4->misc in 1." in line
    assert "late 2" in line and "hand" not in line
    assert "waiting for frames" in format_status(None, 0.0)


def test_last_decision_names_member_classes():
    log = FakeLog(
        [{"group_id": 9, "member_track_ids": [8, 9], "output": "rework", "rule": "rework"}],
        [{"track_id": 8, "part_class": "bolt"}, {"track_id": 9, "part_class": "washer"}],
    )
    assert last_decision(log) == "group 9 [#8,#9] -> rework (bolt,washer) (rework)"
    assert last_decision(FakeLog()) == "none yet"


def records(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_window_runs_closes_and_writes_session_end(sandbox, capsys):
    log = sandbox / "out" / "log.jsonl"
    args = ["run", "--source", "synthetic", "--stationary", "--seconds", "2"]
    assert main([*args, "--decision-log", str(log)]) == 0
    assert any(r["kind"] == "session_end" for r in records(log))
    assert "t=" in capsys.readouterr().out  # the status line is printed in window mode too


def test_quiet_disables_status(sandbox, capsys):
    log = sandbox / "q" / "log.jsonl"
    args = ["run", "--source", "synthetic", "--stationary", "--headless", "--fast", "--quiet"]
    assert main([*args, "--seconds", "2", "--decision-log", str(log)]) == 0
    assert "t=" not in capsys.readouterr().out


def test_record_then_replay_gives_the_same_stationary_decisions(sandbox):
    log = sandbox / "live" / "log.jsonl"
    args = ["run", "--source", "synthetic", "--stationary", "--headless", "--seconds", "7"]
    assert main([*args, "--record", "rec", "--decision-log", str(log)]) == 0
    recs = records(log)
    start = next(r for r in recs if r["kind"] == "session_start")
    assert "rec" in start["recording"]
    assert any(r["kind"] == "session_end" for r in recs)

    with RecordingReader(resolve_recording(load_config(), "rec")) as reader:
        assert len(reader) > 100 and reader.meta.get("complete")

    again = sandbox / "again" / "log.jsonl"
    args = ["run", "--source", "rec", "--stationary", "--headless", "--fast"]
    assert main([*args, "--decision-log", str(again)]) == 0

    def decisions(path):
        return [(r["member_track_ids"], r["output"]) for r in records(path) if r["kind"] == "group"]

    assert decisions(log) and decisions(log) == decisions(again)


@pytest.mark.parametrize("pacing", ["--fast", "--realtime"])
def test_ctrl_c_ends_the_run_cleanly(sandbox, monkeypatch, pacing):
    from hwsorter.status import StatusReporter

    calls = []

    def interrupt(self, now_s):
        calls.append(now_s)
        if len(calls) > 3:
            raise KeyboardInterrupt
        return False

    monkeypatch.setattr(StatusReporter, "due", interrupt)
    log = sandbox / "c" / "log.jsonl"
    args = ["run", "--source", "synthetic", "--stationary", "--headless", pacing, "--seconds", "20"]
    assert main([*args, "--decision-log", str(log)]) == 0
    assert any(r["kind"] == "session_end" for r in records(log))
