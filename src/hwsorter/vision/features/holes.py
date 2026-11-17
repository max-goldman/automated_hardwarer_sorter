"""Hole detectors on the flat image inside the silhouette.

Washer holes show the white belt (bright, flat >= hole_bright_threshold); nut bores image dark
(flat <= hole_dark_threshold). Elongated parts (bolts) skip both: their darkest pixels (0.28-0.29)
sit just under the dark threshold.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from ...config import FeaturesConfig
from ...contracts import Blob, Prepared
from . import register
from .geometry import is_elongated, poly_area

HOLE_NONE, HOLE_BRIGHT, HOLE_DARK = 0.0, 1.0, 2.0
_OPEN = np.ones((3, 3), np.uint8)


def silhouette_crop(
    blob: Blob, flat: np.ndarray, pad: int
) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    """(flat crop, boolean silhouette mask on the crop, (x0, y0) crop origin)."""
    h, w = flat.shape
    bx0, by0, bx1, by1 = blob.mask_bbox_px
    x0, y0 = max(bx0 - pad, 0), max(by0 - pad, 0)
    x1, y1 = min(bx1 + pad + 1, w), min(by1 + pad + 1, h)
    pts = np.round((blob.contour_px.astype(np.float64) - [x0, y0]) * 16.0).astype(np.int32)
    mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
    cv2.fillPoly(mask, [pts], 1, shift=4)
    return flat[y0:y1, x0:x1], mask.astype(bool), (x0, y0)


def _largest(cond: np.ndarray) -> int:
    cond = cv2.morphologyEx(cond.astype(np.uint8), cv2.MORPH_OPEN, _OPEN)
    n, _, stats, _ = cv2.connectedComponentsWithStats(cond, connectivity=4)
    return int(stats[1:, cv2.CC_STAT_AREA].max()) if n > 1 else 0


@register("hole", provides=("hole_kind", "hole_diameter_in"))
def _hole(c: np.ndarray, prepared: Prepared, blob: Blob, cfg: FeaturesConfig):
    if is_elongated(c, cfg):
        return {"hole_kind": HOLE_NONE, "hole_diameter_in": math.nan}
    crop, mask, _ = silhouette_crop(blob, prepared.flat, 2)
    n_mask = int(mask.sum())
    if n_mask == 0:
        return {"hole_kind": math.nan, "hole_diameter_in": math.nan}
    # keep clear of the blurred rim, scaled with part size rather than a fixed pixel count
    k = max(2, round(0.03 * math.sqrt(n_mask)))
    inner = cv2.erode(mask.astype(np.uint8), np.ones((2 * k + 1, 2 * k + 1), np.uint8)).astype(bool)
    area_in2 = poly_area(c)
    best_kind, best_px = HOLE_NONE, 0
    for kind, cond in (
        (HOLE_BRIGHT, crop >= cfg.hole_bright_threshold),
        (HOLE_DARK, crop <= cfg.hole_dark_threshold),
    ):
        px = _largest(cond & inner)
        if px > best_px:
            best_kind, best_px = kind, px
    diameter = 2.0 * math.sqrt(area_in2 * best_px / n_mask / math.pi)
    if best_px == 0 or diameter < cfg.hole_min_diameter_in:
        return {"hole_kind": HOLE_NONE, "hole_diameter_in": 0.0}
    return {"hole_kind": best_kind, "hole_diameter_in": diameter}
