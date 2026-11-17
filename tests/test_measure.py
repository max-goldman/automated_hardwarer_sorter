"""`sorter measure` on a stationary synthetic belt (no camera)."""

import json
import logging

import cv2
import numpy as np
import pytest

from hwsorter import measure
from hwsorter.cli import main
from hwsorter.config import load_config
from hwsorter.pipeline import synthetic_calibration


def _rig(scene):
    cal, flat = synthetic_calibration(scene)
    return measure._rig_from_calibration(cal, scene.width, scene.height, flat)


@pytest.fixture(scope="module")
def cfg():
    return load_config(None)


@pytest.fixture(scope="module")
def still(cfg):
    frames, scene = measure.still_synthetic_frames(cfg, 12)
    return measure.run_measurement(frames, cfg, _rig(scene))


def test_every_part_found_once_left_to_right_with_right_class(still):
    parts = still["parts"]
    expected = [k for k, _, _ in sorted(measure.STILL_LAYOUT, key=lambda r: r[1])]
    assert [p["best_class"] for p in parts] == expected
    assert [p["id"] for p in parts] == list(range(1, 7))
    xs = [p["centre_in"][0] for p in parts]
    assert xs == sorted(xs)
    assert all(p["verdict"] == "CONFIDENT" and not p["edge"] for p in parts)
    assert all(r["mark"] == "OK" for p in parts for r in p["features"])


def test_dimmed_frames_are_skipped(cfg):
    frames, scene = measure.still_synthetic_frames(cfg, 20, flicker_depth=0.5, flicker_every=5)
    skipped = measure.run_measurement(frames, cfg, _rig(scene))
    assert 0 < skipped["frames"]["used"] < 20
    assert skipped["frames"]["used"] + skipped["frames"]["skipped_dimmed"] == 20
    kept = measure.run_measurement(frames, cfg, _rig(scene), include_dimmed=True)
    assert kept["frames"]["used"] == 20 and kept["frames"]["skipped_dimmed"] == 0


def test_cli_writes_outputs_and_exits_zero(tmp_path):
    out = tmp_path / "m"
    try:
        code = main(["measure", "synthetic", "--max-frames", "8", "--out", str(out)])
    finally:
        logging.getLogger().handlers.clear()
        logging.getLogger().setLevel(logging.WARNING)
    assert code == 0
    data = json.loads((out / "measure.json").read_text())
    assert len(data["parts"]) == 6
    assert data["frames"]["used"] == 8
    for name in ("overlay.png", "mask.png"):
        img = cv2.imread(str(out / name), cv2.IMREAD_UNCHANGED)
        assert img is not None and img.shape[:2] == (800, 1280)
    assert np.count_nonzero(cv2.imread(str(out / "mask.png"), cv2.IMREAD_GRAYSCALE)) > 1000
