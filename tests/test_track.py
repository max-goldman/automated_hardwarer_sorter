"""Tracker tests: Blobs from fakes and from synthetic frames (local threshold helper)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from fakes import make_blob

from hwsorter.calibration.store import PlaneMapper, ScaleCalibration
from hwsorter.config import TrackConfig
from hwsorter.contracts import Blob, TrackFlag
from hwsorter.synthetic import PartSpec, SceneConfig, SyntheticRenderer
from hwsorter.vision.track import Tracker

PX_PER_IN = 367.0
W, H = 1280, 800
V = 4.0
DT_NS = 8_333_333


def make_mapper() -> PlaneMapper:
    s = PX_PER_IN
    cx, cy = W / 2, H / 2
    return PlaneMapper(
        ScaleCalibration(
            image_width=W,
            image_height=H,
            camera_matrix=[[1000.0, 0.0, cx], [0.0, 1000.0, cy], [0.0, 0.0, 1.0]],
            dist_coeffs=[0.0, 0.0, 0.0, 0.0, 0.0],
            homography=[[1 / s, 0.0, -cx / s], [0.0, 1 / s, -cy / s], [0.0, 0.0, 1.0]],
            px_per_in=s,
            px_per_in_min=s,
            px_per_in_max=s,
            reprojection_rms_px=0.0,
            plane_rms_in=0.0,
            views=1,
            corners=1,
            mirrored=False,
            target_scale_correction=1.0,
            measured_utc="2026-01-01T00:00:00Z",
        )
    )


def tracker(**kw) -> Tracker:
    return Tracker(make_mapper(), V, TrackConfig(**kw))


def fake_blob(x_px: float, y_px: float = 400.0, half: float = 20.0, **kw) -> Blob:
    edge = x_px - half <= 0 or x_px + half >= W - 1
    return make_blob(x_px, y_px, half, touches_edge=kw.pop("touches_edge", edge), **kw)


def run_fakes(parts: list[tuple[float, float]], n_frames: int, tr: Tracker | None = None):
    """parts = (x_px at frame 0, y_px). Returns (tracker, finished tracks)."""
    tr = tr or tracker()
    done = []
    for i in range(n_frames):
        t = i * DT_NS
        blobs = [fake_blob(x + V * PX_PER_IN * t / 1e9, y) for x, y in parts]
        blobs = [b for b in blobs if b.contour_px[:, 0].min() < W]
        done += tr.update(i, t, blobs)
    return tr, done


def test_single_part_one_id_and_valid() -> None:
    tr, done = run_fakes([(-60.0, 400.0)], 160)
    assert len(done) == 1 and not tr.live()
    t = done[0]
    assert t.finished and t.track_id == 1
    assert t.flags == TrackFlag(0)
    assert sum(o.valid for o in t.observations) > 80
    assert not t.observations[0].valid  # clipped at the left edge
    assert t.belt_x_lead_in - t.belt_x_trail_in == pytest.approx(40 / PX_PER_IN, abs=0.01)
    bx = [
        float(o.contour_in[:, 0].mean()) - V * o.t_capture_ns / 1e9
        for o in t.observations
        if o.valid
    ]
    assert max(bx) - min(bx) < 0.01


def test_three_parts_keep_separate_ids() -> None:
    _tr, done = run_fakes([(100.0, 150.0), (200.0, 400.0), (0.0, 650.0)], 160)
    assert sorted(t.track_id for t in done) == [1, 2, 3]
    ys = sorted(round((t.y_min_in + t.y_max_in) / 2, 1) for t in done)
    assert ys[0] < ys[1] < ys[2]
    assert all(TrackFlag.MERGED not in t.flags for t in done)


def test_merge_flagged_then_separate() -> None:
    tr = tracker()
    done = []
    sep = 70.0
    for i in range(60):
        t = i * DT_NS
        adv = V * PX_PER_IN * t / 1e9
        a, b = 300.0 + adv, 300.0 + sep + adv
        if 20 <= i < 30:  # one blob covering both parts
            mid = (a + b) / 2
            blobs = [fake_blob(mid, 400.0, half=sep / 2 + 20)]
        else:
            blobs = [fake_blob(a, 400.0), fake_blob(b, 400.0)]
        done += tr.update(i, t, blobs)
    live = tr.live()
    assert [t.track_id for t in live] == [1, 2]
    assert all(TrackFlag.MERGED in t.flags for t in live)
    assert all(not o.valid for t in live for o in t.observations[20:30])
    assert all(o.valid for t in live for o in t.observations[40:])


def test_unsettled_few_frames_and_edge() -> None:
    def short_run(n_visible: int):
        tr, done = tracker(), []
        for i in range(n_visible + 6):
            x = 500 + V * PX_PER_IN * i * DT_NS / 1e9
            blobs = [fake_blob(x, 400)] if i < n_visible else []
            done += tr.update(i, i * DT_NS, blobs)
        assert len(done) == 1
        return done[0].flags

    f3 = short_run(3)
    assert TrackFlag.FEW_FRAMES in f3 and TrackFlag.UNSETTLED not in f3
    assert TrackFlag.UNSETTLED in short_run(2)
    assert short_run(8) == TrackFlag(0)
    tr, done = tracker(), []
    for i in range(8):  # always clipped by the edge
        done += tr.update(i, i * DT_NS, [fake_blob(10, 400, touches_edge=True)])
    for i in range(8, 14):
        done += tr.update(i, i * DT_NS, [])
    assert TrackFlag.TOUCHES_EDGE in done[0].flags and TrackFlag.UNSETTLED in done[0].flags


def test_part_not_moving_with_belt_is_invalid() -> None:
    tr = tracker()
    for i in range(10):  # stuck in the image frame: moving backwards in the belt frame
        tr.update(i, i * DT_NS, [fake_blob(500, 400)])
    obs = tr.live()[0].observations
    assert not any(o.valid for o in obs[3:])


def test_area_jump_invalid() -> None:
    tr = tracker()
    for i in range(4):
        x = 500 + V * PX_PER_IN * i * DT_NS / 1e9
        tr.update(i, i * DT_NS, [fake_blob(x, 400, half=20.0)])
    x = 500 + V * PX_PER_IN * 4 * DT_NS / 1e9
    tr.update(4, 4 * DT_NS, [fake_blob(x, 400, half=26.0)])
    assert not tr.live()[0].observations[-1].valid


def test_entry_belt_x() -> None:
    tr = tracker()
    left = -W / 2 / PX_PER_IN
    assert tr.entry_belt_x_in(1_000_000) == pytest.approx(left)
    assert tr.entry_belt_x_in(1_000_000 + 1_000_000_000) == pytest.approx(left - V)


# ---------------------------------------------------------------- synthetic scenes
def blobs_from_frame(frame: np.ndarray, background: np.ndarray) -> list[Blob]:
    diff = cv2.absdiff(frame, background)
    mask = (diff > 45).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    cs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    out = []
    for c in cs:
        area = cv2.contourArea(c)
        if area < 400:
            continue
        x, y, w, h = cv2.boundingRect(c)
        out.append(
            Blob(
                contour_px=c.reshape(-1, 2).astype(np.float32),
                mask_bbox_px=(x, y, x + w, y + h),
                area_px=float(area),
                touches_edge=x <= 0 or x + w >= W or y <= 0 or y + h >= H,
            )
        )
    return out


def run_synthetic(parts: list[PartSpec], seconds: float):
    cfg = SceneConfig(width=W, height=H, px_per_in=PX_PER_IN, speed_in_s=V, seam=False, specks=0)
    scene, empty = SyntheticRenderer(cfg, parts), SyntheticRenderer(cfg, [])
    tr, done = tracker(), []
    n = int(seconds * cfg.fps)
    for i in range(n):
        t_s = i / cfg.fps
        frame = scene.render(t_s, i)
        bg = empty.render(t_s, i)
        done += tr.update(i, int(t_s * 1e9), blobs_from_frame(frame, bg))
    return tr, done


def test_synthetic_three_parts_stable_belt_x() -> None:
    parts = [
        PartSpec("nut_025", 0.10, 0.6),
        PartSpec("bolt_025", 0.50, 1.2, angle_deg=0.0),
        PartSpec("washer_0375", 0.90, 1.55),
    ]
    tr, done = run_synthetic(parts, 3.0)
    assert len(done) == 3 and not tr.live()
    for t in done:
        valid = [o for o in t.observations if o.valid]
        assert len(valid) >= 30
        assert not t.flags & (TrackFlag.MERGED | TrackFlag.UNSETTLED | TrackFlag.FEW_FRAMES)
        bx = [
            float(o.contour_in[:, 0].min() + o.contour_in[:, 0].max()) / 2
            - V * (o.t_capture_ns - 0) / 1e9
            for o in valid
        ]
        # The crude threshold segmenter lets a faint shadow edge flicker in and out under the
        # illumination falloff, so allow a few outlier frames; the noise-free fake-blob test
        # below checks the strict 0.01 in bound.
        dev = np.abs(np.array(bx) - np.median(bx))
        assert np.mean(dev < 0.01) >= 0.85 and dev.max() < 0.03
    ids = sorted(t.track_id for t in done)
    assert ids == [1, 2, 3]
