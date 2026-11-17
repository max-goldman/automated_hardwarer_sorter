"""Scale and lens-distortion calibration from the printed target lying on the belt.

What is solved: one radial distortion coefficient (the focal length is fixed from the lens
and pixel size, the principal point at the image centre), then a homography from
undistorted pixels to the belt plane. With the target always flat on the belt, a full
camera calibration is not well determined and is not needed: every measurement the sorter
makes lies on, or just above, that one plane.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from ..config import CalibrationTargetConfig
from .store import ScaleCalibration, utc_now
from .target import make_board

MM_PER_IN = 25.4
MIN_CORNERS = 12


@dataclass
class TargetView:
    """Detected target corners in one image."""

    corners_px: np.ndarray    # (N, 2) float32, in the stored (unflipped) image
    ids: np.ndarray           # (N,) corner ids
    object_mm: np.ndarray     # (N, 3) corner positions on the board in mm, z = 0
    mirrored: bool            # detection only succeeded on the left-right flipped image

    def __len__(self) -> int:
        return len(self.ids)


def _detect(image: np.ndarray, board: "cv2.aruco.CharucoBoard") -> tuple[np.ndarray, np.ndarray] | None:
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.CharucoDetector(board, cv2.aruco.CharucoParameters(), params)
    corners, ids, _, _ = detector.detectBoard(image)
    if ids is None or len(ids) < MIN_CORNERS:
        return None
    return corners.reshape(-1, 2).astype(np.float32), ids.reshape(-1)


def detect_target(image: np.ndarray, target: CalibrationTargetConfig) -> TargetView | None:
    """Find the target in a mono image. Tries the mirrored image if the direct read fails."""
    board = make_board(target)
    all_corners = board.getChessboardCorners()
    found = _detect(image, board)
    mirrored = False
    if found is None:
        found = _detect(cv2.flip(image, 1), board)
        if found is None:
            return None
        mirrored = True
        corners, ids = found
        corners = corners.copy()
        corners[:, 0] = image.shape[1] - 1 - corners[:, 0]   # back to stored-image coordinates
        found = (corners, ids)
    corners, ids = found
    return TargetView(corners, ids, all_corners[ids].astype(np.float32), mirrored)


def solve_scale(
    views: list[TargetView],
    image_size: tuple[int, int],
    focal_px: float,
    target_scale_correction: float = 1.0,
) -> ScaleCalibration:
    """Solve distortion and the pixel-to-belt mapping from one or more target views.

    image_size is (width, height). target_scale_correction is measured bar / nominal bar,
    correcting a printer that does not print at exactly 100%.
    """
    views = [v for v in views if len(v) >= MIN_CORNERS]
    if not views:
        raise ValueError("no usable target views")
    if any(v.mirrored for v in views):
        # A mirror image is not a rotation, so no camera pose fits it; the flip must be undone first.
        raise ValueError(
            "the image is mirrored left-right. Set flip_horizontal = true under [camera] in "
            "config/local.toml, restart, and capture the target again"
        )
    width, height = image_size
    K0 = np.array([[focal_px, 0, (width - 1) / 2.0], [0, focal_px, (height - 1) / 2.0], [0, 0, 1]], np.float64)
    object_points = [v.object_mm.reshape(-1, 1, 3) * target_scale_correction for v in views]
    image_points = [v.corners_px.reshape(-1, 1, 2) for v in views]
    flags = (cv2.CALIB_USE_INTRINSIC_GUESS | cv2.CALIB_FIX_FOCAL_LENGTH | cv2.CALIB_FIX_PRINCIPAL_POINT
             | cv2.CALIB_FIX_ASPECT_RATIO | cv2.CALIB_ZERO_TANGENT_DIST | cv2.CALIB_FIX_K2 | cv2.CALIB_FIX_K3)
    rms, K, dist, _, _ = cv2.calibrateCamera(object_points, image_points, (width, height), K0, None, flags=flags)
    dist = dist.reshape(-1)

    # Reference view (the one with most corners) defines the belt plane mapping.
    ref = max(views, key=len)
    undistorted = cv2.undistortPoints(ref.corners_px.reshape(-1, 1, 2).astype(np.float64), K, dist, P=K).reshape(-1, 2)
    board_in = ref.object_mm[:, :2].astype(np.float64) * target_scale_correction / MM_PER_IN
    H_board, _ = cv2.findHomography(undistorted, board_in, method=0)
    if H_board is None:
        raise ValueError("could not fit the belt plane to the target")

    def apply(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
        return cv2.perspectiveTransform(np.asarray(pts, np.float64).reshape(-1, 1, 2), H).reshape(-1, 2)

    # Re-express the plane in belt axes: origin under the image centre, +x along image +x.
    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    centre, right, down = apply(H_board, np.array([[cx, cy], [cx + 100.0, cy], [cx, cy + 100.0]]))
    ex = (right - centre) / np.linalg.norm(right - centre)
    ey = np.array([-ex[1], ex[0]])
    if np.dot(down - centre, ey) < 0:     # board coordinates are left-handed relative to the image
        ey = -ey
    A = np.array([[ex[0], ex[1], -np.dot(ex, centre)], [ey[0], ey[1], -np.dot(ey, centre)], [0, 0, 1.0]])
    H = A @ H_board

    def local_ppi(x: float, y: float) -> float:
        p = apply(H, np.array([[x - 50.0, y], [x + 50.0, y], [x, y - 50.0], [x, y + 50.0]]))
        return 200.0 / (np.linalg.norm(p[1] - p[0]) + np.linalg.norm(p[3] - p[2]))

    samples = [local_ppi(x, y) for x in (width * 0.1, cx, width * 0.9) for y in (height * 0.15, cy, height * 0.85)]
    plane_err = apply(H_board, undistorted) - board_in
    return ScaleCalibration(
        image_width=width, image_height=height,
        camera_matrix=K.tolist(), dist_coeffs=dist.tolist(), homography=H.tolist(),
        px_per_in=float(local_ppi(cx, cy)), px_per_in_min=float(min(samples)), px_per_in_max=float(max(samples)),
        reprojection_rms_px=float(rms), plane_rms_in=float(np.sqrt((plane_err ** 2).sum(axis=1).mean())),
        views=len(views), corners=int(sum(len(v) for v in views)),
        mirrored=bool(ref.mirrored), target_scale_correction=float(target_scale_correction), measured_utc=utc_now(),
    )


def nominal_focal_px(lens_focal_mm: float, pixel_pitch_um: float) -> float:
    return lens_focal_mm * 1000.0 / pixel_pitch_um
