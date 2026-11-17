"""Region of interest: find the white belt between the dark bands above and below it."""
from __future__ import annotations

import numpy as np

from ..config import RoiConfig
from .store import RoiCalibration


def find_belt_edges(image: np.ndarray, margin_px: int = 6) -> tuple[int, int, bool]:
    """Return (top, bottom, found) rows bounding the belt.

    The belt is the bright region around the image centre; the edges are where the row
    brightness falls below half the belt level, searching outward from the middle. Row
    medians are used so parts, specks and the seam do not move the result. `found` is
    False when no dark band exists on either side (the belt fills the frame).
    """
    height = image.shape[0]
    rows = np.median(image[:, :: max(image.shape[1] // 256, 1)], axis=1).astype(np.float32)
    kernel = np.ones(5, np.float32) / 5.0
    rows = np.convolve(np.pad(rows, 2, mode="edge"), kernel, mode="valid")
    belt_level = float(np.median(rows[height // 3 : 2 * height // 3]))
    threshold = 0.5 * belt_level
    dark = rows < threshold
    mid = height // 2
    above = np.nonzero(dark[:mid])[0]
    below = np.nonzero(dark[mid:])[0]
    top = int(above[-1]) + 1 if len(above) else 0
    bottom = mid + int(below[0]) if len(below) else height
    found = bool(len(above) or len(below))
    if len(above):
        top = min(top + margin_px, mid)
    if len(below):
        bottom = max(bottom - margin_px, mid)
    return top, bottom, found


def resolve_roi(image: np.ndarray, cfg: RoiConfig) -> RoiCalibration:
    width = image.shape[1]
    if cfg.mode == "manual":
        return RoiCalibration(top=cfg.top, bottom=cfg.bottom, left=0, right=width, source="manual")
    top, bottom, _ = find_belt_edges(image, cfg.margin_px)
    return RoiCalibration(top=top, bottom=bottom, left=0, right=width, source="auto")
