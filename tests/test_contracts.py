import dataclasses
import json

import numpy as np
import pytest
from fakes import (
    make_blob,
    make_classification,
    make_fused,
    make_part_result,
    make_track,
)
from pydantic import ValidationError

from hwsorter.calibration.store import RoiCalibration
from hwsorter.config import load_config
from hwsorter.contracts import (
    Classifier,
    FeatureVector,
    GroupDecision,
    MissReason,
    Observation,
    Output,
    OutputAdapter,
    PartClass,
    Prepared,
    ScheduledOutput,
    SeamObs,
    SendRecord,
    TrackFlag,
    Verdict,
)
from hwsorter.synthetic import PART_KINDS


def test_part_class_matches_synthetic_part_dims():
    assert {c.value for c in PartClass} == set(PART_KINDS)
    for c in PartClass:
        assert Output.for_class(c).value == c.value
    assert {Output.MISC, Output.REWORK} <= set(Output)


def test_construct_all_types():
    roi = RoiCalibration(top=0, bottom=800, left=0, right=1280, source="auto")
    p = Prepared(0, 10, np.ones((800, 1280), np.float32), roi, 1.0)
    assert p.flat.dtype == np.float32
    blob = make_blob()
    assert blob.on_seam is False
    seam = SeamObs(True, 5.0, 0.0, 4.0, np.zeros((4, 2), np.float32), 10)
    assert seam.present
    obs = Observation(1, 2, blob, blob.contour_px.copy())
    assert obs.valid
    t = make_track()
    assert t.finished and len(t.observations) == 3
    assert FeatureVector({"a": float("nan")}).valid
    assert make_fused().n_frames == 5
    assert make_classification().part_class is PartClass.BOLT_025
    assert make_classification(Verdict.OUT_OF_SCOPE).part_class is None
    r = make_part_result()
    d = GroupDecision(1, (1,), Output.BOLT_025, "lone", 0, 2.0, 5)
    rec = SendRecord(ScheduledOutput(1, d.output, d.member_track_ids, 100), 101)
    assert rec.miss is MissReason.NONE
    assert r.flags == TrackFlag(0)
    assert TrackFlag.MERGED | TrackFlag.ON_SEAM


def test_frozen():
    d = GroupDecision(1, (1,), Output.MISC, "x", 0, 0.0, 0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.output = Output.REWORK  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        make_part_result().track_id = 3  # type: ignore[misc]


def test_group_decision_json_serialisable():
    d = GroupDecision(1, (1, 2), Output.REWORK, "mixed", 0, 0.0, 0)
    assert '"rework"' in json.dumps(dataclasses.asdict(d))


def test_protocols():
    class C:
        def classify(self, fused, flags):
            return make_classification()

    class A:
        def send(self, o, t_actual_ns):
            pass

        def close(self):
            pass

    assert isinstance(C(), Classifier)
    assert isinstance(A(), OutputAdapter)


def test_config_new_sections():
    cfg = load_config()
    assert cfg.segment.min_area_in2 > 0
    assert cfg.seam.enabled
    assert cfg.track.gate_in > 0
    assert cfg.classify.library_path == "config/parts_library.yaml"
    assert cfg.scheduler.enabled
    assert cfg.pipeline.decision_log_dir
    assert cfg.belt.rework_gap_in == 1.0


def test_unknown_keys_rejected(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("[track]\nbogus = 1\n")
    with pytest.raises(ValidationError):
        load_config(bad)
