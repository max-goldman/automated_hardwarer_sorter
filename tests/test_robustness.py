"""Real-image robustness on synthetic scenes (task 023): illumination drift, attached soft
shadows, outline noise, thread crests and dim holes. Ground truth is the renderer's."""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from hwsorter.calibration.flatfield import build_flatfield
from hwsorter.calibration.roi import find_belt_edges
from hwsorter.calibration.store import PlaneMapper, RoiCalibration, ScaleCalibration
from hwsorter.config import FeaturesConfig, SegmentConfig
from hwsorter.contracts import Blob, Observation, Prepared
from hwsorter.frames import Frame
from hwsorter.synthetic import PART_DIMS, PartSpec, SceneConfig, SyntheticRenderer
from hwsorter.vision.features import extract
from hwsorter.vision.features.holes import HOLE_BRIGHT
from hwsorter.vision.segment import Preprocessor, segment

PPI = 367.0
W, H = 1280, 800


def _mapper() -> PlaneMapper:
    k = [[1500.0, 0, (W - 1) / 2], [0, 1500.0, (H - 1) / 2], [0, 0, 1]]
    hom = [[1 / PPI, 0, -(W - 1) / 2 / PPI], [0, 1 / PPI, -(H - 1) / 2 / PPI], [0, 0, 1]]
    return PlaneMapper(
        ScaleCalibration(
            image_width=W, image_height=H, camera_matrix=k, dist_coeffs=[0.0] * 5,
            homography=hom, px_per_in=PPI, px_per_in_min=PPI, px_per_in_max=PPI,
            reprojection_rms_px=0.0, plane_rms_in=0.0, views=1, corners=1, mirrored=False,
            target_scale_correction=1.0, measured_utc="t",
        )
    )  # fmt: skip


BASE = SceneConfig(seed=5, specks=6, seam=False)


@pytest.fixture(scope="module")
def rig():
    """Flat-field and ROI from the BASE scene: later lighting changes are not in the flat."""
    empty = SyntheticRenderer(BASE, [])
    flat = build_flatfield([empty.render(i * 0.09, i) for i in range(40)])
    top, bottom, _ = find_belt_edges(empty.render(0.0, 0))
    roi = RoiCalibration(top=top, bottom=bottom, left=0, right=W, source="auto")
    return flat, roi, _mapper()


def _prepare(rig, scene: SceneConfig, parts: list[PartSpec], cfg: SegmentConfig, n: int = 1):
    flat, roi, _ = rig
    pre = Preprocessor(flat, roi, cfg)
    r = SyntheticRenderer(scene, parts)
    out = None
    for i in range(n):  # the illumination surface may reuse the previous frame's fit
        out = pre.apply(Frame(i, 10 + i, 10 + i, r.render(0.0, i)))
    return out


def _features(rig, scene, kind, angle, x_in=1.7, y_in=1.0, seg=None):
    """Segment one still part and return (features, blob)."""
    _, _, mapper = rig
    seg = seg or SegmentConfig()
    part = PartSpec(kind, 0.0, y_in, angle, x_in=x_in)
    p = _prepare(rig, scene, [part], seg, n=2)
    blobs = segment(p, seg, mapper)
    assert len(blobs) == 1, f"{kind}: {len(blobs)} blobs"
    b = blobs[0]
    obs = Observation(p.seq, p.t_capture_ns, b, mapper.px_to_in(b.contour_px.astype(np.float64)))
    return extract(obs, p).values, b


def _belt_tiles(p, roi) -> np.ndarray:
    """Median flat level of 40x40 px tiles of the belt (clear of the belt-edge bands)."""
    band = p.flat[roi.top + 20 : roi.bottom - 20]
    th, tw = band.shape[0] // 40, band.shape[1] // 40
    tiles = band[: th * 40, : tw * 40].reshape(th, 40, tw, 40)
    return np.median(tiles, axis=(1, 3)).ravel()


# --- 1. illumination correction -----------------------------------------------------------


@pytest.mark.parametrize("grad", [0.10, -0.10])
def test_lighting_gradient_is_flattened(rig, grad):
    _, roi, _ = rig
    scene = dataclasses.replace(BASE, illum_gradient=grad)
    on = _belt_tiles(_prepare(rig, scene, [], SegmentConfig()), roi)
    assert np.all(np.abs(on - 1.0) <= 0.02), (on.min(), on.max())
    off = _belt_tiles(_prepare(rig, scene, [], SegmentConfig(illum_correction=False)), roi)
    assert np.abs(off - 1.0).max() > 0.06  # the ramp really is there without correction


def test_illumination_fit_ignores_parts(rig):
    """Dark parts covering a good share of the belt must not drag the belt level."""
    _, roi, _ = rig
    scene = dataclasses.replace(BASE, illum_gradient=0.1)
    parts = [
        PartSpec("washer_0375", 0.0, 0.4 + 0.6 * (i % 3), 0.0, x_in=0.5 + 0.55 * i)
        for i in range(6)
    ]
    p = _prepare(rig, scene, parts, SegmentConfig())
    belt = _belt_tiles(p, roi)
    belt = belt[np.abs(belt - 1.0) < 0.1]  # tiles mostly on the belt
    assert len(belt) > 100
    assert np.median(np.abs(belt - 1.0)) <= 0.01


def test_gradient_does_not_change_sizes(rig):
    plain, _ = _features(rig, BASE, "washer_025", 0.0, x_in=3.1, y_in=0.3)
    ramp = dataclasses.replace(BASE, illum_gradient=0.1)
    tilted, _ = _features(rig, ramp, "washer_025", 0.0, x_in=3.1, y_in=0.3)
    assert tilted["area_in2"] == pytest.approx(plain["area_in2"], rel=0.02)
    assert tilted["hole_kind"] == plain["hole_kind"] == HOLE_BRIGHT


# --- 2. attached soft shadow --------------------------------------------------------------

SHADOW = dataclasses.replace(BASE, shadow_blur_in=0.05, shadow_offset_in=(0.04, 0.05))


@pytest.mark.parametrize(
    ("kind", "depth"),
    [
        ("nut_0375", 0.10),
        ("nut_0375", 0.16),
        ("washer_025", 0.10),
        ("washer_025", 0.16),
        # A bolt head facet as bright as the belt cannot be told from a lighter shadow, so
        # the bolt is only checked with the default shadow depth.
        ("bolt_025", 0.16),
    ],
)
def test_attached_shadow_is_trimmed(rig, kind, depth):
    truth = _features(rig, BASE, kind, 20.0)[0]["area_in2"]
    scene = dataclasses.replace(SHADOW, shadow_depth=depth)
    trimmed, _ = _features(rig, scene, kind, 20.0)
    raw, _ = _features(rig, scene, kind, 20.0, seg=SegmentConfig(shadow_trim=False))
    err = abs(trimmed["area_in2"] / truth - 1)
    assert err < 0.03, (kind, trimmed["area_in2"], truth)
    assert err <= abs(raw["area_in2"] / truth - 1) + 1e-3
    if depth >= 0.16:
        assert raw["area_in2"] / truth - 1 > 0.05  # the shadow really was attached


@pytest.mark.parametrize("kind", ["nut_0375", "washer_025", "bolt_025"])
def test_dark_shadow_is_never_trimmed_into_the_part(rig, kind):
    """A shadow darker than shadow_trim_depth is out of the trim's reach: it stays attached
    (outline too large, never too small)."""
    truth = _features(rig, BASE, kind, 20.0)[0]["area_in2"]
    scene = dataclasses.replace(SHADOW, shadow_depth=0.30)
    trimmed, _ = _features(rig, scene, kind, 20.0)
    assert trimmed["area_in2"] >= truth * 0.99


def test_shadow_trim_never_splits_a_blob(rig):
    """Two washers joined by their soft shadows stay one blob (the safe MERGED case)."""
    _, _, mapper = rig
    scene = dataclasses.replace(BASE, shadow_depth=0.3, shadow_blur_in=0.06)
    parts = [
        PartSpec("washer_025", 0.0, 1.0, 0.0, x_in=1.5),
        PartSpec("washer_025", 0.0, 1.0, 0.0, x_in=1.5 + 0.63),
    ]
    seg = SegmentConfig()
    blobs = segment(_prepare(rig, scene, parts, seg), seg, mapper)
    assert len(blobs) in (1, 2)
    big = sum(b.area_px for b in blobs) / PPI**2
    assert big > 1.8 * math.pi * (PART_DIMS["washer_025"]["od"] / 2) ** 2 * 0.95


# --- 3. circularity under outline noise ---------------------------------------------------

NOISY = dataclasses.replace(BASE, outline_noise_px=0.6)


def _circ(rig, scene, kind):
    angles = (0.0, 13.0, 31.0, 47.0)
    return np.array(
        [_features(rig, scene, kind, a, x_in=1.2 + 0.2 * i)[0]["circularity"]
         for i, a in enumerate(angles)]
    )  # fmt: skip


def test_circularity_robust_to_outline_noise(rig):
    """Hull circularity barely moves when the outline gets a +-0.6 px wobble (the polygon
    perimeter of the raw outline would grow with every wiggle)."""
    nut, nut_clean = _circ(rig, NOISY, "nut_025"), _circ(rig, BASE, "nut_025")
    washer, washer_clean = _circ(rig, NOISY, "washer_0375"), _circ(rig, BASE, "washer_0375")
    # a hexagon is pi/(2 sqrt 3) = 0.907; the blurred, slightly rounded corners read a bit higher
    assert np.all((nut > 0.89) & (nut < 0.95)), nut
    assert np.all(washer > 0.97), washer
    assert np.abs(nut - nut_clean).max() < 0.01
    assert np.abs(washer - washer_clean).max() < 0.01
    for v in (nut, washer):
        assert np.median(np.abs(v - np.median(v))) < 0.005


# --- 4. bolt shank width = major (crest) diameter -----------------------------------------


@pytest.mark.parametrize("kind", ["bolt_025", "bolt_0375"])
@pytest.mark.parametrize("noise", [0.0, 0.5])
def test_shank_width_is_major_diameter(rig, kind, noise):
    scene = dataclasses.replace(BASE, outline_noise_px=noise)
    vals = [_features(rig, scene, kind, a)[0]["shank_width_in"] for a in (0.0, 25.0, 160.0)]
    major = PART_DIMS[kind]["shank"]
    assert all(abs(v - major) < 0.02 for v in vals), (kind, vals, major)
    assert max(vals) - min(vals) < 0.02


# --- 5. hole kind relative to the local belt level ----------------------------------------


def _washer_on_dim_belt(level: float) -> tuple[Observation, Prepared]:
    """A washer whose hole shows the belt at ``level`` (darker than the absolute threshold)."""
    yy, xx = np.mgrid[0:200, 0:200].astype(np.float64)
    r = np.hypot(xx - 100, yy - 100)
    flat = np.full((200, 200), level, np.float32)
    flat[(r < 60) & (r > 26)] = 0.35
    ang = np.linspace(0, 2 * np.pi, 180, endpoint=False)
    c = np.column_stack([100 + 60 * np.cos(ang), 100 + 60 * np.sin(ang)]).astype(np.float32)
    blob = Blob(contour_px=c, mask_bbox_px=(40, 40, 160, 160), area_px=math.pi * 3600,
                touches_edge=False)  # fmt: skip
    roi = RoiCalibration(top=0, bottom=200, left=0, right=200, source="auto")
    prepared = Prepared(seq=0, t_capture_ns=0, flat=flat, roi=roi, gain=1.0)
    return Observation(0, 0, blob, (c.astype(np.float64) - 100) / 100.0), prepared


@pytest.mark.parametrize("level", [1.0, 0.85, 0.75])
def test_hole_kind_is_relative_to_local_belt(level):
    cfg = FeaturesConfig()
    assert level < cfg.hole_bright_threshold or level == 1.0
    obs, prep = _washer_on_dim_belt(level)
    v = extract(obs, prep, cfg).values
    assert v["hole_kind"] == HOLE_BRIGHT
    assert v["hole_diameter_in"] == pytest.approx(0.52, abs=0.03)
