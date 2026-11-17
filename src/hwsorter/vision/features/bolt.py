"""Bolt profile from the belt-plane contour: width along the long axis (NaN if not elongated)."""

from __future__ import annotations

import math

import cv2
import numpy as np

from ...config import FeaturesConfig
from ...contracts import Blob, Prepared
from . import register
from .geometry import is_elongated, resample

NAMES = ("shank_width_in", "head_width_in", "head_length_in", "total_length_in")
_SLICES = 200
_MAX_POINTS = 1200  # contours longer than this are resampled (the profile cost grows with it)


def width_profile(c: np.ndarray, n: int = _SLICES) -> tuple[np.ndarray, np.ndarray]:
    """Rotate so the long axis is x; return (s sample positions, silhouette width at each s)."""
    (cx, cy), (w, h), ang = cv2.minAreaRect(c.astype(np.float32))
    if w < h:
        ang += 90.0
    a = math.radians(ang)
    rot = np.array([[math.cos(a), math.sin(a)], [-math.sin(a), math.cos(a)]])
    p = (c - [cx, cy]) @ rot.T
    s = np.linspace(float(p[:, 0].min()), float(p[:, 0].max()), n + 2)[1:-1]
    a_, b_ = p, np.roll(p, -1, axis=0)
    da, db = a_[None, :, 0] - s[:, None], b_[None, :, 0] - s[:, None]  # (n, edges)
    cross = da * db < 0
    t = da / np.where(cross, da - db, 1.0)
    y = a_[None, :, 1] + t * (b_[None, :, 1] - a_[None, :, 1])
    top = np.where(cross, y, -np.inf).max(axis=1)
    bot = np.where(cross, y, np.inf).min(axis=1)
    return s, np.where(cross.any(axis=1), top - bot, 0.0)


@register("bolt_profile", provides=NAMES)
def _bolt(c: np.ndarray, prepared: Prepared, blob: Blob, cfg: FeaturesConfig):
    if not is_elongated(c, cfg):
        return {}
    s, wid = width_profile(resample(c, _MAX_POINTS) if len(c) > _MAX_POINTS else c)
    q = max(len(wid) // 6, 3)
    if wid[:q].mean() < wid[-q:].mean():
        s, wid = -s[::-1], wid[::-1]  # head end first
    n = len(wid)
    step = float(s[1] - s[0])
    shank = float(np.percentile(wid[n // 4 :], 90))
    head = float(np.percentile(wid[:q], 90))
    mid = 0.5 * (shank + head)
    # Head length = from the outer head end to where the head plateau drops to the shank. Walk
    # forward from the widest head sample (the plateau), so a tapered or blurred head tip (whose
    # first samples sit below the midpoint) cannot read as a zero-length head.
    i0 = int(np.argmax(wid[: max(n // 2, 1)]))
    drop = np.nonzero(wid[i0:] < mid)[0]
    head_len = float(i0 + drop[0] if drop.size else n) * step
    return {
        "shank_width_in": shank,
        "head_width_in": head,
        "head_length_in": head_len,
        "total_length_in": float(s[-1] - s[0]) + step,
    }
