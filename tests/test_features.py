"""Feature registry, features and fusion on rendered synthetic parts of known size."""

from __future__ import annotations

import math
import time

import cv2
import numpy as np
import pytest
from fakes import make_track

from hwsorter.calibration.store import RoiCalibration
from hwsorter.contracts import Blob, FeatureVector, Observation, Prepared, TrackFlag
from hwsorter.synthetic import PART_DIMS, PART_KINDS, PartSpec, SceneConfig, SyntheticRenderer
from hwsorter.vision.features import RESERVED_FEATURES, extract, feature_names
from hwsorter.vision.fusion import fuse

PPI = 367.0
CX, CY = 640.5, 400.5  # part centre in px; half-pixel so the renderer's sub-pixel shift is exact
UP = 4  # contour upsampling for sub-pixel silhouettes
ROI = RoiCalibration(top=50, bottom=785, left=0, right=1280, source="auto")


def _render(
    kind: str, angle: float, seed: int = 0, blur: float = 1.1
) -> tuple[np.ndarray, np.ndarray]:
    """(flat image belt~1.0, ground-truth alpha silhouette in full-frame px)."""
    cfg = SceneConfig(seam=False, specks=0, seed=seed + 1, blur_sigma_px=blur)
    spec = PartSpec(kind, -CX / (cfg.speed_in_s * PPI), (CY - cfg.belt_top_px) / PPI, angle)
    rend = SyntheticRenderer(cfg, [spec])
    img = rend.render(0.0, seed).astype(np.float32)
    ref = SyntheticRenderer(cfg, []).render(0.0, seed + 1).astype(np.float32)
    flat = img / cv2.GaussianBlur(ref, (0, 0), 6.0)
    _, alpha_patch, _ = rend._patch(spec)
    size = alpha_patch.shape[0]
    x_f, y_f = CX - size / 2.0, CY - size / 2.0
    xi, yi = math.floor(x_f), math.floor(y_f)
    shift = np.float32([[1, 0, x_f - xi], [0, 1, y_f - yi]])
    a = cv2.warpAffine(alpha_patch, shift, (size + 1, size + 1), flags=cv2.INTER_LINEAR)
    alpha = np.zeros(flat.shape, np.float32)
    alpha[yi : yi + size + 1, xi : xi + size + 1] = a
    return flat.astype(np.float32), alpha


def _contour_px(alpha: np.ndarray) -> np.ndarray:
    """Outer silhouette at 1/UP px precision (pixel-centre convention of OpenCV)."""
    y0, y1, x0, x1 = 100, 700, 340, 940
    up = cv2.resize(alpha[y0:y1, x0:x1], None, fx=UP, fy=UP, interpolation=cv2.INTER_LINEAR)
    cs, _ = cv2.findContours((up > 0.5).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    c = max(cs, key=cv2.contourArea).reshape(-1, 2).astype(np.float64)
    return ((c + 0.5) / UP - 0.5 + [x0, y0]).astype(np.float32)


def _contour_from_threshold(flat: np.ndarray, level: float = 0.8) -> np.ndarray:
    """The trivial segmentation: threshold, outer contour, largest."""
    m = (flat < level).astype(np.uint8)
    m[:60] = 0
    m[780:] = 0
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return max(cs, key=cv2.contourArea).reshape(-1, 2).astype(np.float32)


def _observation(
    flat: np.ndarray, contour_px: np.ndarray, seq: int = 0
) -> tuple[Observation, Prepared]:
    x0, y0 = np.floor(contour_px.min(axis=0)).astype(int)
    x1, y1 = np.ceil(contour_px.max(axis=0)).astype(int)
    blob = Blob(
        contour_px=contour_px,
        mask_bbox_px=(int(x0), int(y0), int(x1), int(y1)),
        area_px=float(cv2.contourArea(contour_px)),
        touches_edge=False,
    )
    prepared = Prepared(seq=seq, t_capture_ns=seq, flat=flat, roi=ROI, gain=1.0)
    contour_in = (contour_px.astype(np.float64) - [CX, CY]) / PPI  # px -> belt inches
    return Observation(seq, seq, blob, contour_in), prepared


def measure(kind: str, angle: float, seed: int = 0) -> dict[str, float]:
    flat, alpha = _render(kind, angle, seed)
    obs, prep = _observation(flat, _contour_px(alpha))
    return extract(obs, prep).values


def nominal(kind: str) -> dict[str, float]:
    """Ground-truth feature values from the brief's dimensions (NaN = no truth / not applicable)."""
    d = PART_DIMS[kind]
    nan = math.nan
    out = dict.fromkeys(feature_names(), nan)
    if kind.startswith("washer"):
        out |= {
            "area_in2": math.pi * (d["od"] / 2) ** 2,
            "minrect_long_in": d["od"],
            "minrect_short_in": d["od"],
            "hull_solidity": 1.0,
            "circularity": 1.0,
            "hex_harmonic": 0.0,
            "across_flats_in": d["od"],
            "across_corners_in": d["od"],
            "hole_kind": 1.0,
            "hole_diameter_in": d["id"],
        }
    elif kind.startswith("nut"):
        af = d["af"]
        out |= {
            "area_in2": math.sqrt(3) / 2 * af**2,
            "minrect_long_in": 2 * af / math.sqrt(3),
            "minrect_short_in": af,
            "hull_solidity": 1.0,
            "circularity": math.pi / (2 * math.sqrt(3)),
            "across_flats_in": af,
            "across_corners_in": 2 * af / math.sqrt(3),
            "hole_kind": 2.0,
            "hole_diameter_in": d["bore"],
        }
    else:
        out |= {
            "minrect_long_in": d["length"] + d["head_h"],
            "minrect_short_in": d["head_w"],
            "hole_kind": 0.0,
            "shank_width_in": d["shank"],
            "head_width_in": d["head_w"],
            "head_length_in": d["head_h"],
            "total_length_in": d["length"] + d["head_h"],
        }
    return out


# absolute tolerances per feature (inches / in^2 / unitless), asserted for every part and angle.
# Known biases: thread valleys make the bolt shank read ~0.007 in narrow; the 0.90 bright-hole
# threshold reads a washer hole ~0.035 in small (the part's soft shadow darkens the hole rim),
# so the bright-hole tolerance is wider than the dark bore's.
TOL = {
    "area_in2": 0.004,
    "minrect_long_in": 0.004,
    "minrect_short_in": 0.004,
    "hull_solidity": 0.01,
    "circularity": 0.015,
    "across_flats_in": 0.004,
    "across_corners_in": 0.004,
    "hole_kind": 0.0,
    "shank_width_in": 0.012,
    "head_width_in": 0.004,
    "head_length_in": 0.004,
    "total_length_in": 0.01,
}
HOLE_TOL = {1.0: 0.045, 2.0: 0.012}  # hole_diameter_in tolerance by hole kind
HEX_HARMONIC_CIRCLE_MAX = 0.01  # washer and bolt-free outlines
HEX_HARMONIC_HEX_MIN = 0.04  # a hexagon sits near 0.066
ANGLES = (0.0, 17.0, 30.0, 45.0, 90.0, 133.0)


def test_registry_names_and_reserved():
    names = feature_names()
    for n in (
        "area_in2",
        "minrect_long_in",
        "minrect_short_in",
        "hull_solidity",
        "circularity",
        "hex_harmonic",
        "across_flats_in",
        "across_corners_in",
        "hole_kind",
        "hole_diameter_in",
        "shank_width_in",
        "head_width_in",
        "head_length_in",
        "total_length_in",
        "edge_sharpness",
    ):
        assert n in names
    assert len(set(names)) == len(names)
    assert "thread_pitch_in" in RESERVED_FEATURES
    assert "thread_pitch_in" not in names


@pytest.mark.parametrize("kind", PART_KINDS)
@pytest.mark.parametrize("angle", ANGLES)
def test_features_match_nominal(kind, angle):
    got, want = measure(kind, angle), nominal(kind)
    assert set(got) == set(feature_names())
    for name, tol in TOL.items():
        if math.isnan(want[name]):
            continue
        assert got[name] == pytest.approx(want[name], abs=tol), (kind, angle, name)
    if got["hole_kind"] > 0:
        assert got["hole_diameter_in"] == pytest.approx(
            want["hole_diameter_in"], abs=HOLE_TOL[got["hole_kind"]]
        ), (kind, angle)
    if kind.startswith("washer"):
        assert got["hex_harmonic"] < HEX_HARMONIC_CIRCLE_MAX
    if kind.startswith("nut"):
        assert got["hex_harmonic"] > HEX_HARMONIC_HEX_MIN
    if kind.startswith("bolt"):  # no hole detection, no bolt-profile NaN
        assert math.isnan(got["hole_diameter_in"]) and math.isnan(got["hex_harmonic"])
    else:
        for n in ("shank_width_in", "head_width_in", "head_length_in", "total_length_in"):
            assert math.isnan(got[n])
    assert got["edge_sharpness"] > 0.0


@pytest.mark.parametrize("angle", ANGLES)
def test_washer_vs_nut_near_miss_separable(angle):
    washer, nut = measure("washer_025", angle), measure("nut_0375", angle)
    # outer size is a near miss: 0.625 OD against 0.650 across corners
    assert abs(washer["across_corners_in"] - nut["across_corners_in"]) < 0.04
    assert washer["hole_kind"] == 1.0 and nut["hole_kind"] == 2.0
    assert nut["hex_harmonic"] > 3 * washer["hex_harmonic"]
    assert nut["hex_harmonic"] > 0.04 > 0.01 > washer["hex_harmonic"]
    assert washer["circularity"] > 0.97 > 0.93 > nut["circularity"]


def test_rotation_invariance():
    for kind in ("nut_025", "washer_0375", "bolt_0375"):
        runs = [measure(kind, a) for a in (0.0, 22.0, 61.0, 118.0)]
        names = ["area_in2", "minrect_long_in", "across_flats_in"]
        if not kind.startswith("bolt"):
            names.append("hex_harmonic")  # NaN on elongated parts
        for name in names:
            vals = [r[name] for r in runs]
            assert max(vals) - min(vals) < 0.008, (kind, name)


def test_threshold_segmentation_contour_also_works():
    """The trivial threshold + findContours silhouette (pixel-quantised) gives the same story."""
    for kind, hole in (("washer_025", 1.0), ("nut_0375", 2.0)):
        flat, _ = _render(kind, 25.0)
        obs, prep = _observation(flat, _contour_from_threshold(flat))
        v = extract(obs, prep).values
        assert v["hole_kind"] == hole
        assert v["across_corners_in"] == pytest.approx(0.625 if hole == 1.0 else 0.650, abs=0.03)


def test_invalid_observation_is_all_nan():
    flat, alpha = _render("washer_025", 0.0)
    obs, prep = _observation(flat, _contour_px(alpha))
    bad = Observation(obs.seq, obs.t_capture_ns, obs.blob, obs.contour_in, valid=False)
    fv = extract(bad, prep)
    assert not fv.valid
    assert all(math.isnan(x) for x in fv.values.values())
    assert set(fv.values) == set(feature_names())


def test_hole_skipped_for_elongated_even_with_dark_pixels():
    flat, alpha = _render("bolt_0375", 40.0)
    flat = flat.copy()
    flat[alpha > 0.5] = np.minimum(flat[alpha > 0.5], 0.28)  # dark enough to trip the dark detector
    obs, prep = _observation(flat, _contour_px(alpha))
    v = extract(obs, prep).values
    assert v["hole_kind"] == 0.0 and math.isnan(v["hole_diameter_in"])


def test_edge_sharpness_drops_with_blur():
    vals = []
    for blur in (0.8, 1.6, 3.0):
        flat, alpha = _render("washer_0375", 0.0, blur=blur)
        obs, prep = _observation(flat, _contour_px(alpha))
        vals.append(extract(obs, prep).values["edge_sharpness"])
    assert vals[0] > vals[1] > vals[2] > 0.0


def test_extract_time_per_part():
    flat, alpha = _render("nut_0375", 12.0)
    obs, prep = _observation(flat, _contour_px(alpha))
    extract(obs, prep)
    n = 20
    t0 = time.perf_counter()
    for _ in range(n):
        extract(obs, prep)
    ms = (time.perf_counter() - t0) / n * 1e3
    assert ms < 50.0, f"extract() took {ms:.1f} ms"


# ----------------------------------------------------------------------------- fusion


def _fv(**vals: float) -> FeatureVector:
    return FeatureVector(dict(vals), valid=True)


def test_fuse_median_mad_ignores_nan_and_invalid():
    vecs = [
        _fv(a=1.0, b=math.nan),
        _fv(a=2.0, b=math.nan),
        _fv(a=3.0, b=math.nan),
        _fv(a=100.0, b=math.nan),  # outlier barely moves the median/MAD
        _fv(a=math.nan, b=math.nan),
        FeatureVector({"a": -50.0, "b": 1.0}, valid=False),
    ]
    f = fuse(make_track(), vecs)
    assert f.n_frames == 5
    assert f.median["a"] == 2.5 and f.spread["a"] == pytest.approx(1.0)
    assert math.isnan(f.median["b"]) and math.isnan(f.spread["b"])
    assert not f.on_seam


def test_fuse_flags_and_too_few_frames():
    t = make_track(flags=TrackFlag.ON_SEAM)
    f = fuse(t, [_fv(a=1.0), _fv(a=2.0), _fv(a=3.0)])
    assert f.on_seam and f.median["a"] == 2.0
    few = fuse(make_track(), [_fv(a=1.0), _fv(a=2.0)])  # below min_valid_frames (3)
    assert few.n_frames == 2 and math.isnan(few.median["a"])
    assert fuse(make_track(), []).n_frames == 0


def test_fuse_real_features_over_rotating_frames():
    vecs = []
    for ang in (0.0, 10.0, 20.0, 30.0, 40.0):
        flat, alpha = _render("nut_025", ang)
        obs, prep = _observation(flat, _contour_px(alpha))
        vecs.append(extract(obs, prep))
    f = fuse(make_track(), vecs)
    assert f.n_frames == 5
    assert f.median["across_flats_in"] == pytest.approx(0.438, abs=0.012)
    assert f.spread["across_flats_in"] < 0.005
