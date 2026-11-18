"""Hole detectors on the flat image inside the silhouette.

Washer holes show the belt (bright); nut bores image dark. Both thresholds are fractions of the
LOCAL belt level, the median of a ring just outside the outline (``hole_ring_in`` wide, starting
``hole_ring_gap_in`` out to skip the edge blur), so an uneven or drifting light or the part's own
shadow cannot push a see-through hole under an absolute level. The hole is the bright or dark
region at the part's centre (washers and nuts are centred on their hole), not the largest one: a
dark washer body is a large dark region and must not read as a dark bore. Elongated parts (bolts)
skip both: their darkest pixels sit just under the dark threshold.
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
_CENTRE_PX = 3  # the hole is the component voted most often in this radius around the centre


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


def _centre_component(cond: np.ndarray, centre: tuple[int, int]) -> int:
    """Pixel count of the opened 4-connected component of ``cond`` at (or next to) ``centre``."""
    cond = cv2.morphologyEx(cond.astype(np.uint8), cv2.MORPH_OPEN, _OPEN)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(cond, connectivity=4)
    if n <= 1:
        return 0
    cx, cy = centre
    h, w = lab.shape
    r = _CENTRE_PX
    win = lab[max(cy - r, 0) : min(cy + r + 1, h), max(cx - r, 0) : min(cx + r + 1, w)]
    ids, counts = np.unique(win[win > 0], return_counts=True)
    if not len(ids):
        return 0
    return int(stats[int(ids[np.argmax(counts)]), cv2.CC_STAT_AREA])


def belt_level(crop: np.ndarray, mask: np.ndarray, gap_px: int, ring_px: int) -> float:
    """Median flat level in a ring outside the silhouette (1.0 if the ring is empty)."""
    m = mask.astype(np.uint8)
    inner = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * gap_px + 1,) * 2))
    k = 2 * (gap_px + ring_px) + 1
    outer = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    ring = (outer > 0) & (inner == 0)
    return float(np.median(crop[ring])) if ring.any() else 1.0


@register("hole", provides=("hole_kind", "hole_diameter_in"))
def _hole(c: np.ndarray, prepared: Prepared, blob: Blob, cfg: FeaturesConfig):
    if is_elongated(c, cfg):
        return {"hole_kind": HOLE_NONE, "hole_diameter_in": math.nan}
    area_in2 = poly_area(c)
    area_px = abs(float(cv2.contourArea(blob.contour_px.astype(np.float32))))
    if area_in2 <= 0 or area_px <= 0:
        return {"hole_kind": math.nan, "hole_diameter_in": math.nan}
    ppi = math.sqrt(area_px / area_in2)
    gap_px = max(round(cfg.hole_ring_gap_in * ppi), 1)
    ring_px = max(round(cfg.hole_ring_in * ppi), 2)
    crop, mask, _ = silhouette_crop(blob, prepared.flat, gap_px + ring_px + 2)
    n_mask = int(mask.sum())
    if n_mask == 0:
        return {"hole_kind": math.nan, "hole_diameter_in": math.nan}
    level = belt_level(crop, mask, gap_px, ring_px)
    # keep clear of the blurred rim, scaled with part size rather than a fixed pixel count
    k = max(2, round(0.03 * math.sqrt(n_mask)))
    inner = cv2.erode(mask.astype(np.uint8), np.ones((2 * k + 1, 2 * k + 1), np.uint8)).astype(bool)
    m = cv2.moments(mask.astype(np.uint8), binaryImage=True)
    centre = (round(m["m10"] / m["m00"]), round(m["m01"] / m["m00"]))
    best_kind, best_px = HOLE_NONE, 0
    for kind, cond in (
        (HOLE_BRIGHT, crop >= level * cfg.hole_bright_threshold),
        (HOLE_DARK, crop <= level * cfg.hole_dark_threshold),
    ):
        px = _centre_component(cond & inner, centre)
        if px > best_px:
            best_kind, best_px = kind, px
    diameter = 2.0 * math.sqrt(area_in2 * best_px / n_mask / math.pi)
    if best_px == 0 or diameter < cfg.hole_min_diameter_in:
        return {"hole_kind": HOLE_NONE, "hole_diameter_in": 0.0}
    return {"hole_kind": best_kind, "hole_diameter_in": diameter}
