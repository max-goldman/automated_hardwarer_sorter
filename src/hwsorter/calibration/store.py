"""Machine-measured calibration, stored per machine in config/calibration/<machine>.json.

Only the calibration tool writes this file. Large arrays (the flat-field image) are saved
beside it as .npy files and referenced by name.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict

from ..config import machine_name, project_root

CALIBRATION_VERSION = 1


class _Part(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScaleCalibration(_Part):
    """Maps image pixels to inches on the belt plane.

    A point is first undistorted with camera_matrix and dist_coeffs (staying in pixel
    units), then mapped through `homography` to belt inches. Belt axes: +x along image +x
    (direction of travel), +y down the image, origin under the image centre.
    """

    image_width: int
    image_height: int
    camera_matrix: list[list[float]]
    dist_coeffs: list[float]
    homography: list[list[float]]
    px_per_in: float                 # at the image centre
    px_per_in_min: float             # over the view; a wide spread means the camera is tilted
    px_per_in_max: float
    reprojection_rms_px: float
    plane_rms_in: float
    views: int
    corners: int
    mirrored: bool                   # the target was only readable flipped left-right
    target_scale_correction: float   # measured printed bar length / nominal
    measured_utc: str


class RoiCalibration(_Part):
    top: int
    bottom: int
    left: int
    right: int
    source: str                      # "auto" or "manual"


class FocusCalibration(_Part):
    edge_width_px: float
    measured_utc: str


class ExposureCalibration(_Part):
    exposure_ms: float
    belt_level: float
    flicker_swing_pct: float
    blur_px: float
    measured_utc: str


class BeltCalibration(_Part):
    speed_in_s: float
    loop_length_in: float | None = None
    measured_utc: str


class Calibration(_Part):
    version: int = CALIBRATION_VERSION
    machine: str
    scale: ScaleCalibration | None = None
    roi: RoiCalibration | None = None
    focus: FocusCalibration | None = None
    exposure: ExposureCalibration | None = None
    belt: BeltCalibration | None = None
    flatfield_file: str | None = None

    def missing(self) -> list[str]:
        return [name for name in ("scale", "roi", "focus", "exposure", "belt") if getattr(self, name) is None]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def calibration_path(machine: str | None = None, root: Path | None = None) -> Path:
    return (root or project_root()) / "config" / "calibration" / f"{machine or machine_name()}.json"


def load_calibration(machine: str | None = None, root: Path | None = None) -> Calibration:
    """The saved calibration for this machine, or an empty one if none exists yet."""
    path = calibration_path(machine, root)
    if path.is_file():
        cal = Calibration.model_validate_json(path.read_text())
        if cal.version != CALIBRATION_VERSION:
            raise ValueError(f"calibration file {path} has unsupported version {cal.version}")
        return cal
    return Calibration(machine=machine or machine_name())


def save_calibration(cal: Calibration, root: Path | None = None) -> Path:
    path = calibration_path(cal.machine, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(cal.model_dump_json(indent=2))
    tmp.replace(path)
    return path


class PlaneMapper:
    """Pixel <-> belt-inch conversion from a ScaleCalibration."""

    def __init__(self, scale: ScaleCalibration) -> None:
        self.scale = scale
        self._K = np.array(scale.camera_matrix, np.float64)
        self._dist = np.array(scale.dist_coeffs, np.float64)
        self._H = np.array(scale.homography, np.float64)
        self._H_inv = np.linalg.inv(self._H)

    def px_to_in(self, points_px: np.ndarray) -> np.ndarray:
        """(N, 2) pixel coordinates -> (N, 2) inches on the belt plane."""
        pts = np.asarray(points_px, np.float64).reshape(-1, 1, 2)
        undistorted = cv2.undistortPoints(pts, self._K, self._dist, P=self._K)
        return cv2.perspectiveTransform(undistorted, self._H).reshape(-1, 2)

    def in_to_px(self, points_in: np.ndarray) -> np.ndarray:
        """(N, 2) belt inches -> (N, 2) pixel coordinates (distortion re-applied)."""
        pts = np.asarray(points_in, np.float64).reshape(-1, 1, 2)
        ideal = cv2.perspectiveTransform(pts, self._H_inv).reshape(-1, 2)
        normalized = (ideal - self._K[:2, 2]) / np.array([self._K[0, 0], self._K[1, 1]])
        obj = np.column_stack([normalized, np.ones(len(normalized))])
        projected, _ = cv2.projectPoints(obj, np.zeros(3), np.zeros(3), self._K, self._dist)
        return projected.reshape(-1, 2)
