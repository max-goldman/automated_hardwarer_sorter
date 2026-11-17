"""Seam detection and handling on synthetic scenes (ground truth known)."""

import math

import cv2
import numpy as np
import pytest

from hwsorter.calibration.flatfield import apply_flatfield, build_flatfield
from hwsorter.calibration.roi import find_belt_edges
from hwsorter.calibration.store import RoiCalibration
from hwsorter.config import SeamConfig
from hwsorter.contracts import Blob, Prepared
from hwsorter.synthetic import PartSpec, SceneConfig, SyntheticRenderer, _render_part
from hwsorter.vision.seam import SeamHandler

PPI = 367.0
W, H = 1280, 800
FPS = 120.0
SPEED = 4.0  # synthetic belt speed, in/s


def nominal_area_px(kind: str, angle_deg: float) -> float:
    """True silhouette area (holes filled) from the renderer's own alpha."""
    _, alpha = _render_part(kind, angle_deg, PPI)
    solid = (alpha > 0.5).astype(np.uint8)
    cnts, _ = cv2.findContours(solid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(solid)
    cv2.drawContours(filled, cnts, -1, 1, -1)
    return float(filled.sum())


def simple_segment(p: Prepared) -> list[Blob]:
    """Minimal stand-in for the segmenter: threshold the flat-fielded frame, keep outer
    contours (holes filled). The seam stitches show up as small blobs, as they do for the
    real segmenter."""
    roi = p.roi
    dark = np.zeros(p.flat.shape, np.uint8)
    strip = 12  # belt-edge shading strips are not parts
    rows = slice(roi.top + strip, roi.bottom - strip)
    dark[rows, roi.left : roi.right] = np.abs(p.flat[rows, roi.left : roi.right] - 1.0) > 0.15
    dark = cv2.morphologyEx(
        dark, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    )
    cnts, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(dark)
    cv2.drawContours(filled, cnts, -1, 1, -1)
    cnts, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    blobs = []
    for c in cnts:
        area = float(cv2.contourArea(c))
        if area < 60:
            continue
        x, y, w, h = cv2.boundingRect(c)
        blobs.append(
            Blob(
                c.reshape(-1, 2).astype(np.float32),
                (x, y, x + w, y + h),
                area,
                False,
            )
        )
    return blobs


@pytest.fixture(scope="module")
def rig():
    scene = SceneConfig(seed=3, specks=8)
    empty = SyntheticRenderer(scene, [])
    flat = build_flatfield([empty.render(i * 0.09, i) for i in range(40)])
    top, bottom, _ = find_belt_edges(empty.render(0.0, 0))
    roi = RoiCalibration(top=top, bottom=bottom, left=0, right=W, source="auto")
    return scene, flat, roi


def seam_xc(scene: SceneConfig, t: float) -> float:
    """Seam centre-line x at y = H/2 at time t (strip geometry from synthetic.py)."""
    loop_px = round(scene.loop_length_in * scene.px_per_in)
    u0 = (-SPEED * PPI * t) % loop_px
    x_top = scene.seam_at_in * PPI - u0
    x_top %= loop_px
    return x_top + (H / 2 - scene.belt_top_px) / math.tan(math.radians(50.0))


def part_at_seam_offset(scene, kind, t, y_in, dx_px, ang=0.0) -> PartSpec:
    """Part whose centre is dx_px downstream of the seam centre line at the part's own row."""
    y_px = scene.belt_top_px + y_in * PPI
    x_seam = seam_xc(scene, t) + (y_px - H / 2) / math.tan(math.radians(50.0))
    return PartSpec(kind, t - (x_seam + dx_px) / (SPEED * PPI), y_in, ang)


_EMPTY: dict[int, SyntheticRenderer] = {}


def prep(rig, parts, t, k=0) -> Prepared:
    scene, flat, roi = rig
    if parts:
        img = SyntheticRenderer(scene, parts).render(t, k)
    else:  # building the belt strip is the slow part: reuse it for part-free frames
        r = _EMPTY.setdefault(id(scene), SyntheticRenderer(scene, []))
        img = r.render(t, k)
    t_ns = round(t * 1e9)
    return Prepared(k, t_ns, apply_flatfield(img, flat), roi, 1.0)


def new_handler() -> SeamHandler:
    h = SeamHandler(SeamConfig())
    h.set_scale(PPI)
    return h


def t_for_seam_x(rig, x_target: float) -> float:
    """A time at which the seam centre is near x_target (seam goes +x at SPEED)."""
    scene = rig[0]
    x0 = seam_xc(scene, 0.0)
    return ((x_target - x0) % (scene.loop_length_in * PPI)) / (SPEED * PPI)


def test_empty_seam_pass_removes_seam_and_reports_it(rig):
    h = new_handler()
    cfg = SeamConfig()
    n_seen = 0
    for x_target in (350, 500, 650, 800):
        t = t_for_seam_x(rig, x_target)
        p = prep(rig, [], t)
        blobs = simple_segment(p)
        out, obs = h.process(p, blobs)
        assert out == []
        assert obs is not None and obs.present
        assert abs(obs.angle_deg - cfg.angle_deg) <= cfg.angle_tol_deg
        assert abs(obs.angle_deg - 50.0) < 3.0
        assert obs.x_center_px == pytest.approx(seam_xc(rig[0], t), abs=3.0)
        assert obs.band_px.shape == (4, 2)
        n_seen += 1
    assert n_seen == 4


def test_no_seam_in_view_reports_absent_or_none(rig):
    h = new_handler()
    # Far from the seam: seam at x ~ 6 in * 367 is off-screen for this t
    scene = rig[0]
    t = t_for_seam_x(rig, 2500.0)
    p = prep(rig, [], t)
    out, obs = h.process(p, [])
    assert out == []
    assert obs is None or not obs.present
    _ = scene


@pytest.fixture(scope="module")
def learned(rig):
    """Handler that has learned its template from part-free frames; returns (handler, next k)."""
    h = new_handler()
    k = 0
    t = t_for_seam_x(rig, 300.0)
    while seam_xc(rig[0], t) < 1000.0:
        p = prep(rig, [], t, k)
        h.process(p, simple_segment(p))
        t += 1.0 / FPS
        k += 1
    assert h.template_ready
    return h, k


@pytest.mark.parametrize(
    ("kind", "ang"),
    [("washer_0375", 0.0), ("washer_025", 0.0), ("nut_0375", 20.0), ("bolt_025", 130.0)],
)
def test_part_on_seam_is_one_flagged_blob_with_nominal_area(rig, learned, kind, ang):
    h, k = learned
    t = t_for_seam_x(rig, 640.0)
    part = part_at_seam_offset(rig[0], kind, t, 1.0, 0.0, ang)  # centred on the seam
    p = prep(rig, [part], t, k)
    out, obs = h.process(p, simple_segment(p))
    assert obs is not None and obs.present
    assert len(out) == 1, (kind, len(out))
    assert out[0].on_seam
    assert out[0].area_px == pytest.approx(nominal_area_px(kind, ang), rel=0.10)


def test_part_on_seam_without_template_is_flagged(rig):
    h = new_handler()
    t = t_for_seam_x(rig, 640.0)
    part = part_at_seam_offset(rig[0], "washer_0375", t, 1.0, 0.0)
    p = prep(rig, [part], t)
    out, obs = h.process(p, simple_segment(p))
    assert obs is not None and obs.present
    assert len(out) == 1 and out[0].on_seam


def test_parts_away_from_seam_pass_through_unflagged(rig):
    h = new_handler()
    t = t_for_seam_x(rig, 640.0)
    # One part well upstream of the seam, one well downstream.
    parts = [
        part_at_seam_offset(rig[0], "nut_025", t, 1.4, -300.0),
        part_at_seam_offset(rig[0], "washer_025", t, 0.6, 250.0),
    ]
    p = prep(rig, parts, t)
    blobs = simple_segment(p)
    # blobs holding the two parts (exclude seam fragments by x distance)
    out, obs = h.process(p, blobs)
    assert obs is not None and obs.present
    assert len(out) == 2
    assert not any(b.on_seam for b in out)
    areas = sorted(b.area_px for b in out)
    assert areas[0] == pytest.approx(nominal_area_px("nut_025", 0.0), rel=0.1)


def test_belt_speed_from_seam_sequence(rig):
    cfg = SeamConfig()
    h = new_handler()
    assert h.belt_speed_in_s() is None
    # Consecutive frames at FPS; the renderer advances the belt (and seam) by SPEED*PPI px/s.
    t = t_for_seam_x(rig, 200.0)
    n = cfg.speed_window + 5
    for k in range(n):
        p = prep(rig, [], t + k / FPS, k)
        h.process(p, simple_segment(p))
    v = h.belt_speed_in_s()
    assert v is not None
    assert v == pytest.approx(SPEED, rel=0.02)
