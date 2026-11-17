"""Calibration maths: scale and distortion, focus, exposure, belt edges, flat-field."""

import cv2
import numpy as np
import pytest

from hwsorter.calibration.exposure import flicker_stats, max_exposure_ms, motion_blur_px
from hwsorter.calibration.flatfield import apply_flatfield, build_flatfield
from hwsorter.calibration.focus import focus_verdict, measure_sharpness
from hwsorter.calibration.roi import find_belt_edges
from hwsorter.calibration.scale import detect_target, nominal_focal_px, solve_scale
from hwsorter.calibration.store import Calibration, PlaneMapper, load_calibration, save_calibration
from hwsorter.calibration.target import render_board
from hwsorter.config import CalibrationTargetConfig
from hwsorter.synthetic import PartSpec, SceneConfig, SyntheticRenderer

W, H = 1280, 800
TARGET = CalibrationTargetConfig()
FOCAL = nominal_focal_px(8.0, 3.0)
PPI = 367.0


def camera_view_of_board(angle_deg=3.0, shift=(12.0, -8.0), k1=-0.12, ppi=PPI, blur=0.9):
    """What the camera would see: the board on the belt, through a lens with radial distortion k1."""
    px_per_mm = ppi / 25.4
    board = render_board(TARGET, px_per_mm=px_per_mm * 4)  # 4x supersampled ideal board
    bw, bh = board.shape[1] / 4, board.shape[0] / 4
    K = np.array([[FOCAL, 0, (W - 1) / 2], [0, FOCAL, (H - 1) / 2], [0, 0, 1]])
    # Ideal (undistorted) pixel position of each distorted output pixel.
    grid = np.stack(
        np.meshgrid(np.arange(W, dtype=np.float64), np.arange(H, dtype=np.float64)), -1
    ).reshape(-1, 1, 2)
    ideal = cv2.undistortPoints(grid, K, np.array([k1, 0, 0, 0, 0.0]), P=K).reshape(H, W, 2)
    # Ideal pixel -> board pixel (similarity: rotation about the image centre plus a shift).
    a = np.deg2rad(angle_deg)
    cx, cy = (W - 1) / 2 + shift[0], (H - 1) / 2 + shift[1]
    dx, dy = ideal[..., 0] - cx, ideal[..., 1] - cy
    bx = (np.cos(a) * dx + np.sin(a) * dy + bw / 2) * 4
    by = (-np.sin(a) * dx + np.cos(a) * dy + bh / 2) * 4
    image = cv2.remap(
        board, bx.astype(np.float32), by.astype(np.float32), cv2.INTER_AREA, borderValue=200
    )
    image = cv2.GaussianBlur(image, (0, 0), blur)
    return (image * 0.75 + 20).astype(np.uint8)


def test_scale_and_distortion_are_recovered():
    views = [
        detect_target(camera_view_of_board(a, s), TARGET)
        for a, s in ((3.0, (12, -8)), (-14.0, (-20, 6)), (88.0, (5, 10)))
    ]
    assert all(v is not None and not v.mirrored for v in views)
    assert len(views[0]) > 100
    cal = solve_scale(views, (W, H), FOCAL)
    assert cal.px_per_in == pytest.approx(PPI, rel=0.003)
    assert cal.px_per_in_max - cal.px_per_in_min < 0.01 * PPI  # camera square to the belt
    assert cal.reprojection_rms_px < 0.4
    assert cal.dist_coeffs[0] == pytest.approx(-0.12, abs=0.03)

    # Distances measured through the mapping match the board, including near the image corners.
    mapper = PlaneMapper(cal)
    view = views[0]
    inches = mapper.px_to_in(view.corners_px)
    truth = view.object_mm[:, :2] / 25.4
    i, j = int(np.argmin(view.corners_px.sum(axis=1))), int(np.argmax(view.corners_px.sum(axis=1)))
    assert np.linalg.norm(inches[i] - inches[j]) == pytest.approx(
        np.linalg.norm(truth[i] - truth[j]), abs=0.003
    )
    # Round trip pixel -> inch -> pixel.
    assert np.abs(mapper.in_to_px(inches) - view.corners_px).max() < 0.05
    # Belt axes: +x follows image +x, +y follows image +y, origin under the image centre.
    centre, right, down = mapper.px_to_in(
        np.array(
            [
                [(W - 1) / 2, (H - 1) / 2],
                [(W - 1) / 2 + 367, (H - 1) / 2],
                [(W - 1) / 2, (H - 1) / 2 + 367],
            ]
        )
    )
    assert (
        np.abs(centre).max() < 1e-6
        and right == pytest.approx([1.0, 0.0], abs=0.01)
        and down == pytest.approx([0.0, 1.0], abs=0.01)
    )


def test_ignoring_distortion_would_be_measurably_worse():
    view = detect_target(camera_view_of_board(k1=-0.12), TARGET)
    cal = solve_scale([view], (W, H), FOCAL)
    assert cal.plane_rms_in < 0.0008
    flat = cal.model_copy(update={"dist_coeffs": [0.0] * 5})
    err = PlaneMapper(flat).px_to_in(view.corners_px) - PlaneMapper(cal).px_to_in(view.corners_px)
    assert np.abs(err).max() > 0.002


def test_printer_scale_correction():
    view = detect_target(camera_view_of_board(), TARGET)
    cal = solve_scale([view], (W, H), FOCAL, target_scale_correction=0.98)  # printed 2% small
    assert cal.px_per_in == pytest.approx(PPI / 0.98, rel=0.003)


def test_mirrored_image_is_detected_and_refused():
    view = detect_target(cv2.flip(camera_view_of_board(), 1), TARGET)
    assert view is not None and view.mirrored
    with pytest.raises(ValueError, match="mirrored"):
        solve_scale([view], (W, H), FOCAL)


def test_no_target_in_view():
    assert detect_target(np.full((H, W), 180, np.uint8), TARGET) is None


def test_calibration_store_round_trip(tmp_path):
    cal = load_calibration("bench", root=tmp_path)
    assert cal.missing() == ["scale", "roi", "focus", "exposure", "belt"]
    cal.scale = solve_scale([detect_target(camera_view_of_board(), TARGET)], (W, H), FOCAL)
    save_calibration(cal, root=tmp_path)
    again = load_calibration("bench", root=tmp_path)
    assert again == cal and isinstance(again, Calibration) and "scale" not in again.missing()


def _edge_image(sigma):
    img = np.full((300, 400), 60, np.float32)
    cv2.circle(img, (200, 150), 90, 200.0, -1, lineType=cv2.LINE_AA)
    img = cv2.GaussianBlur(img, (0, 0), sigma)
    return np.clip(img + np.random.default_rng(1).normal(0, 1.0, img.shape), 0, 255).astype(
        np.uint8
    )


@pytest.mark.parametrize("sigma", [0.8, 1.2, 2.0, 3.0])
def test_edge_width_tracks_blur(sigma):
    result = measure_sharpness(_edge_image(sigma))
    # A Gaussian-blurred step rises from 10% to 90% over 2.563 sigma.
    expected = 2.563 * sigma
    assert result.width_px == pytest.approx(expected, rel=0.12)
    assert result.edges > 50


def test_sharpness_ordering_and_verdicts():
    sharp, soft = measure_sharpness(_edge_image(0.7)), measure_sharpness(_edge_image(2.5))
    assert sharp.width_px < soft.width_px and sharp.energy > soft.energy
    assert focus_verdict(sharp.width_px, 2.2, 3.2)[0] == "sharp"
    assert focus_verdict(soft.width_px, 2.2, 3.2)[0] == "too soft"
    assert focus_verdict(2.8, 2.2, 3.2)[0] == "slightly soft"
    assert measure_sharpness(np.full((200, 200), 128, np.uint8)).width_px is None
    assert focus_verdict(None, 2.2, 3.2)[0] == "no edges"


def test_edge_width_on_synthetic_part_matches_scene_blur():
    scene = SceneConfig(blur_sigma_px=1.4, seam=False, specks=0)
    frame = SyntheticRenderer(scene, [PartSpec("washer_0375", 0.0, 1.0)]).render(0.45)
    result = measure_sharpness(
        frame, roi=(scene.belt_top_px + 10, scene.belt_bottom_px - 10, 0, scene.width)
    )
    assert result.width_px == pytest.approx(2.563 * 1.4, rel=0.2)


def test_motion_blur_and_exposure_limit():
    assert motion_blur_px(1.0, 4.0, 367.0) == pytest.approx(1.468)
    assert max_exposure_ms(0.75, 4.0, 367.0) == pytest.approx(0.5)
    assert max_exposure_ms(0.05, 4.0, 367.0) == pytest.approx(0.1)  # never below one camera step


def test_flicker_stats_match_measured_pattern():
    # The pattern measured on the rig at 0.5 ms: a plateau with two dips every 20 frames.
    cycle = [63] * 10 + [61, 57, 56, 60, 63, 63, 46, 21, 16, 41]
    stats = flicker_stats(np.array(cycle * 12, float))
    assert (
        stats.plateau == 63
        and stats.swing_pct == pytest.approx(74.6, abs=0.1)
        and stats.dipped_pct == pytest.approx(40.0)
    )
    steady = flicker_stats(np.random.default_rng(0).normal(180, 0.4, 240))
    assert steady.swing_pct < 1.5 and steady.dipped_pct == 0


def test_belt_edges_found_on_synthetic_frame_with_parts_and_seam():
    scene = SceneConfig(seam_at_in=50.5)
    frame = SyntheticRenderer(
        scene, [PartSpec("bolt_0375", 0.1, 1.0, 30), PartSpec("nut_025", 0.4, 0.4, 0)]
    ).render(0.86)
    top, bottom, found = find_belt_edges(frame, margin_px=0)
    assert found and abs(top - scene.belt_top_px) <= 4 and abs(bottom - scene.belt_bottom_px) <= 4
    top_m, bottom_m, _ = find_belt_edges(frame, margin_px=6)
    assert (top_m, bottom_m) == (top + 6, bottom - 6)


def test_belt_edges_when_belt_fills_the_frame():
    assert find_belt_edges(np.full((800, 1280), 150, np.uint8)) == (0, 800, False)


def test_flatfield_removes_illumination_and_ignores_moving_things():
    scene = SceneConfig(seam_at_in=51.0, noise_sd=1.0, flicker_depth=0.6)
    renderer = SyntheticRenderer(scene, [])
    frames = [
        renderer.render(t, i) for i, t in enumerate(np.linspace(0.0, 12.9, 60))
    ]  # one full lap, seam included
    flat = build_flatfield(frames)
    belt = (slice(scene.belt_top_px + 12, scene.belt_bottom_px - 12), slice(10, -10))
    clean = SyntheticRenderer(SceneConfig(seam=False, specks=0, noise_sd=0.0), []).render(0.0)
    normalised = apply_flatfield(clean, flat)[belt]
    assert abs(float(np.median(normalised)) - 1.0) < 0.01
    assert float(normalised.std()) < 0.01  # illumination gradient is gone
    assert float(clean[belt].std() / clean[belt].mean()) > 0.03  # ...and it was there to begin with
