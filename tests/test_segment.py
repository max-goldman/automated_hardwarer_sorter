"""Segmentation on synthetic scenes (ground-truth sizes known)."""

import time

import cv2
import numpy as np
import pytest

from hwsorter.calibration.flatfield import build_flatfield
from hwsorter.calibration.roi import find_belt_edges
from hwsorter.calibration.store import PlaneMapper, RoiCalibration, ScaleCalibration
from hwsorter.config import SegmentConfig
from hwsorter.frames import Frame
from hwsorter.synthetic import PART_KINDS, PartSpec, SceneConfig, SyntheticRenderer, _render_part
from hwsorter.vision.segment import Preprocessor, segment

PPI = 367.0
W, H = 1280, 800


def make_mapper() -> PlaneMapper:
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


def nominal_area_in2(kind: str, angle_deg: float) -> float:
    """True silhouette area: the synthetic renderer's own alpha, with holes filled."""
    _, alpha = _render_part(kind, angle_deg, PPI)
    solid = (alpha > 0.5).astype(np.uint8)
    cnts, _ = cv2.findContours(solid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(solid)
    cv2.drawContours(filled, cnts, -1, 1, -1)
    return float(filled.sum()) / PPI**2


@pytest.fixture(scope="module")
def rig():
    scene = SceneConfig(seed=3, specks=8)
    empty = SyntheticRenderer(scene, [])
    flat = build_flatfield([empty.render(i * 0.09, i) for i in range(40)])
    top, bottom, _ = find_belt_edges(empty.render(0.0, 0))
    roi = RoiCalibration(top=top, bottom=bottom, left=0, right=W, source="auto")
    return scene, flat, roi, make_mapper()


def run(rig, parts, t, frame_index=0, cfg=None):
    scene, flat, roi, mapper = rig
    cfg = cfg or SegmentConfig()
    r = SyntheticRenderer(scene, parts)
    pre = Preprocessor(flat, roi, cfg)
    img = r.render(t, frame_index)
    p = pre.apply(Frame(0, 10, 10, img))
    return segment(p, cfg, mapper), p


@pytest.mark.parametrize("kind", PART_KINDS)
def test_each_part_is_one_blob_with_nominal_area(rig, kind):
    parts = [PartSpec(kind, 0.0, 1.0, angle_deg=20.0)]
    blobs, _ = run(rig, parts, t=600 / (4.0 * PPI))  # part centre at x=600
    assert len(blobs) == 1
    b = blobs[0]
    area_in2 = b.area_px / PPI**2
    assert area_in2 == pytest.approx(nominal_area_in2(kind, 20.0), rel=0.05)
    assert not b.touches_edge
    assert b.contour_px.dtype == np.float32 and b.contour_px.shape[1] == 2
    # Sub-pixel: contour is not integer-valued.
    assert np.abs(b.contour_px - np.round(b.contour_px)).max() > 0.05


def test_bolt_head_facet_does_not_split_bolt(rig):
    for angle in (0.0, 90.0, 200.0):
        blobs, _ = run(rig, [PartSpec("bolt_0375", 0.0, 1.0, angle)], t=0.4)
        assert len(blobs) == 1


def test_specks_and_shadows_rejected(rig):
    roi, mapper = rig[2], rig[3]
    # Empty belt with specks and seam: nothing found at any phase.
    for t in (0.0, 0.7, 1.3, 2.9):
        blobs, _ = run(rig, [], t=t)
        assert blobs == []
    # A part's shadow does not enlarge it noticeably (covered by area test); and a pure
    # soft dark region is dropped.
    cfg = SegmentConfig()
    flat_img = np.ones((H, W), np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    flat_img -= 0.08 * np.exp(-(((xx - 600) ** 2 + (yy - 400) ** 2) / (2 * 40.0**2))).astype(
        np.float32
    )
    from hwsorter.contracts import Prepared

    assert segment(Prepared(0, 0, flat_img, roi, 1.0), cfg, mapper) == []


def test_two_separated_parts_give_two_blobs(rig):
    parts = [PartSpec("nut_025", 0.0, 1.0), PartSpec("washer_025", 0.0, 1.0)]
    # Apart in x by differing enter times.
    parts[1] = PartSpec("washer_025", 0.4, 1.9)
    blobs, _ = run(rig, parts, t=0.75)
    assert len(blobs) == 2
    assert blobs[0].mask_bbox_px[0] < blobs[1].mask_bbox_px[0]


def test_touches_edge(rig):
    blobs, _ = run(rig, [PartSpec("washer_0375", 0.0, 1.0)], t=0.02)
    assert len(blobs) == 1 and blobs[0].touches_edge


def test_gain_tracker_follows_slow_drift(rig):
    scene, flat, roi, _ = rig
    cfg = SegmentConfig()
    r = SyntheticRenderer(scene, [])
    pre = Preprocessor(flat, roi, cfg)
    img = r.render(0.0, 0)
    for k in range(120):
        dim = (img.astype(np.float32) * (1.0 - 0.0005 * k)).astype(np.uint8)
        p = pre.apply(Frame(k, k, k, dim))
    assert p.gain == pytest.approx(1.0 - 0.0005 * 119, abs=0.05)
    centre = p.flat[300:500, 400:800]
    assert np.median(centre) == pytest.approx(1.0, abs=0.02)


def _match(b1, b2):
    """Pair blobs by nearest centroid; returns (area_rel_err, centroid_dist) per pair."""
    c1 = [b.contour_px.mean(axis=0) for b in b1]
    c2 = [b.contour_px.mean(axis=0) for b in b2]
    out = []
    for i, c in enumerate(c1):
        j = int(np.argmin([np.linalg.norm(c - d) for d in c2]))
        out.append((abs(b1[i].area_px / b2[j].area_px - 1.0), float(np.linalg.norm(c - c2[j]))))
    return out


EQUIV_SCENES = {
    "two_parts": ([PartSpec("bolt_025", 0.0, 1.0, 30.0), PartSpec("nut_0375", 0.1, 2.0)], 0.5),
    "three_parts": (
        [
            PartSpec("bolt_025", 0.0, 1.0, 30.0),
            PartSpec("nut_0375", 0.1, 2.0),
            PartSpec("washer_025", 0.5, 1.5),
        ],
        0.8,
    ),
    "near_edge": ([PartSpec("washer_0375", 0.0, 1.0), PartSpec("bolt_025", 0.3, 1.8)], 0.02),
}


@pytest.mark.parametrize("name", list(EQUIV_SCENES))
def test_coarse_scale_matches_full_frame(rig, name):
    parts, t = EQUIV_SCENES[name]
    ref, _ = run(rig, parts, t, cfg=SegmentConfig(coarse_scale=1))
    fast, _ = run(rig, parts, t, cfg=SegmentConfig(coarse_scale=2))
    assert len(ref) >= 1
    assert len(fast) == len(ref)
    assert [b.touches_edge for b in fast] == [b.touches_edge for b in ref]
    for area_err, dist in _match(fast, ref):
        assert area_err < 0.02
        assert dist < 1.0


def test_performance_report(rig):
    scene, flat, roi, mapper = rig
    r = SyntheticRenderer(
        scene, [PartSpec("bolt_025", 0.0, 1.0, 30.0), PartSpec("nut_0375", 0.1, 2.0)]
    )
    img = r.render(0.5, 0)
    n = 30
    for cs in (1, 2):
        cfg = SegmentConfig(coarse_scale=cs)
        pre = Preprocessor(flat, roi, cfg)
        for k in range(5):
            segment(pre.apply(Frame(k, k, k, img)), cfg, mapper)
        t0 = time.perf_counter()
        for k in range(n):
            segment(pre.apply(Frame(k, k, k, img)), cfg, mapper)
        ms = (time.perf_counter() - t0) / n * 1000
        print(f"coarse_scale={cs} mean ms/frame: {ms:.2f}")
        assert ms < 50  # gross-regression guard only; the strict budget gate is the bench
