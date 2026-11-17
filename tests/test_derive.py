"""`sorter derive-ranges`: graded sessions -> parts-library YAML, never overwriting silently."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from hwsorter.classify import load_library
from hwsorter.cli import main
from hwsorter.config import project_root
from hwsorter.contracts import PartClass
from hwsorter.grading import KIND_CLASS, Label, LabelStore

BASE = project_root() / "config" / "parts_library.yaml"
# Feature value per class (area_in2); the two examples per class are +-0.004 around it.
AREAS = {pc: 0.1 + 0.05 * n for n, pc in enumerate(PartClass)}


def make_session(root: Path, classes: list[PartClass], offset: float) -> Path:
    """A graded session: one part record per class plus a labels.jsonl labelling each."""
    (root / "decisions").mkdir(parents=True)
    lines = [{"kind": "session_start", "schema_version": 1}]
    store = LabelStore(root / "labels.jsonl")
    for i, pc in enumerate(classes):
        t_ref, lead = (i + 1) * 1_000_000_000, 1.0 + 2.0 * i
        lines.append(
            {
                "kind": "part",
                "track_id": i,
                "verdict": "confident",
                "part_class": pc.value,
                "score": 0.2,
                "flags": [],
                "n_frames": 8,
                "on_seam": False,
                "median": {"area_in2": AREAS[pc] + offset, "hole_kind": 0.0},
                "spread": {},
                "t_ref_ns": t_ref,
                "x_lead_in_at_ref": 1.0,
                "belt_x_lead_in": lead,
                "belt_x_trail_in": lead - 0.5,
            }
        )
        store.put(
            Label(
                key=store.new_key(t_ref, lead),
                t_ref_ns=t_ref,
                belt_x_lead_in=lead,
                belt_x_trail_in=lead - 0.5,
                kind=KIND_CLASS,
                track_id=i,
                part_class=pc.value,
            )
        )
    (root / "decisions" / "decision_log.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8"
    )
    return root


@pytest.fixture
def sessions(tmp_path: Path) -> list[Path]:
    classes = list(PartClass)
    return [
        make_session(tmp_path / "s1", classes, -0.004),
        make_session(tmp_path / "s2", classes, +0.004),
    ]


def run(sessions: list[Path], *extra: str) -> int:
    return main(["derive-ranges", *map(str, sessions), "--base", str(BASE), *extra])


def test_writes_loadable_library_bracketing_examples(
    sessions: list[Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "derived.yaml"
    assert run(sessions, "--out", str(out), "--margin", "0.05") == 0
    lib = load_library(out)
    for pc in PartClass:
        lo, hi = lib.parts[pc].poses["measured"]["area_in2"]
        assert lo < AREAS[pc] - 0.004 and hi > AREAS[pc] + 0.004
        assert hi - lo < 0.06  # tight: padded by the margin only
        assert lib.parts[pc].poses["measured"]["hole_kind"] == (-0.5, 0.5)
    text = capsys.readouterr().out
    assert "12 labelled parts from 2 session(s)" in text
    assert "bolt_025" in text and "2 examples" in text


def test_default_target_is_a_new_file_and_not_overwritten(
    sessions: list[Path], tmp_path: Path
) -> None:
    base = tmp_path / "parts_library.yaml"
    base.write_text(BASE.read_text(encoding="utf-8"), encoding="utf-8")
    hand_edited = base.read_text(encoding="utf-8")
    argv = [str(s) for s in sessions]
    assert main(["derive-ranges", *argv, "--base", str(base)]) == 0
    derived = tmp_path / "parts_library.derived.yaml"
    assert derived.is_file()
    assert base.read_text(encoding="utf-8") == hand_edited  # untouched
    load_library(derived)
    derived.write_text("# my hand edits\n", encoding="utf-8")
    assert main(["derive-ranges", *argv, "--base", str(base)]) == 1  # refuses
    assert derived.read_text(encoding="utf-8") == "# my hand edits\n"


def test_existing_out_refused_without_in_place_and_replaced_with_it(
    sessions: list[Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "lib.yaml"
    out.write_text("# hand edits\n", encoding="utf-8")
    assert run(sessions, "--out", str(out)) == 1
    assert "already exists" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == "# hand edits\n"
    assert run(sessions, "--out", str(out), "--in-place") == 0
    load_library(out)
    assert (
        "measured" in yaml.safe_load(out.read_text(encoding="utf-8"))["parts"]["nut_025"]["poses"]
    )


def test_unseen_parts_keep_base_entry(tmp_path: Path) -> None:
    s = make_session(tmp_path / "s", [PartClass.NUT_025, PartClass.NUT_025], 0.0)
    out = tmp_path / "lib.yaml"
    assert main(["derive-ranges", str(s), "--base", str(BASE), "--out", str(out)]) == 0
    lib = load_library(out)  # all six parts present
    assert set(lib.parts[PartClass.NUT_025].poses) == {"measured"}
    assert lib.parts[PartClass.BOLT_025].poses == load_library(BASE).parts[PartClass.BOLT_025].poses


def test_no_labels_is_an_error(tmp_path: Path) -> None:
    s = make_session(tmp_path / "s", [PartClass.NUT_025], 0.0)
    (s / "labels.jsonl").unlink()
    assert main(["derive-ranges", str(s), "--out", str(tmp_path / "x.yaml")]) == 1
    assert not (tmp_path / "x.yaml").exists()
