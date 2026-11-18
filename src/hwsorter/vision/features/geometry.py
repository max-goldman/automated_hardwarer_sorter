"""Outline features from the belt-plane contour (inches): size, shape, hex harmonic, calipers."""

from __future__ import annotations

import math

import cv2
import numpy as np

from ...config import FeaturesConfig
from ...contracts import Blob, Prepared
from . import register

_CALIPER_STEP_DEG = 1.0


def poly_area(c: np.ndarray) -> float:
    x, y = c[:, 0], c[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def poly_centroid(c: np.ndarray) -> np.ndarray:
    x, y = c[:, 0], c[:, 1]
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    cr = x * y1 - x1 * y
    a = cr.sum() / 2.0
    if abs(a) < 1e-12:
        return c.mean(axis=0)
    return np.array([((x + x1) * cr).sum(), ((y + y1) * cr).sum()]) / (6.0 * a)


def minrect(c: np.ndarray) -> tuple[float, float]:
    """(long, short) sides of the minimum-area rectangle."""
    (_, _), (w, h), _ = cv2.minAreaRect(c.astype(np.float32))
    return max(w, h), min(w, h)


def is_elongated(c: np.ndarray, cfg: FeaturesConfig) -> bool:
    long_, short = minrect(c)
    return short > 0 and long_ / short > cfg.elongated_min_aspect


def resample(c: np.ndarray, n: int) -> np.ndarray:
    """Closed polygon resampled to n points uniform in arclength."""
    closed = np.vstack([c, c[:1]])
    seg = np.hypot(*np.diff(closed, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    if s[-1] <= 0:
        return np.repeat(c[:1], n, axis=0)
    t = np.linspace(0.0, s[-1], n, endpoint=False)
    return np.column_stack([np.interp(t, s, closed[:, 0]), np.interp(t, s, closed[:, 1])])


def hull_circularity(hull: np.ndarray) -> float:
    """4 pi A / P^2 of the convex hull (a regular hexagon 0.907, a circle 1.0).

    Every outline that uses circularity in the library (nut faces, washers) is convex, so the
    hull is the true outline: pixel-level outline noise, thread-like serration and notches where a
    light chamfer meets the belt with little contrast all inflate a raw perimeter (a real hex nut
    read 0.66) but cannot enlarge the hull's. The hull of a noisy convex outline is still convex,
    so the value never exceeds 1.
    """
    if len(hull) < 3:
        return math.nan
    area = poly_area(hull)
    per = float(np.hypot(*(np.roll(hull, -1, axis=0) - hull).T).sum())
    return 4.0 * math.pi * area / per**2 if per > 0 else math.nan


def hex_harmonic(c: np.ndarray, samples: int) -> float:
    """Relative amplitude of the 6th Fourier harmonic of the radial profile r(theta)."""
    dense = resample(c, max(8 * samples, 512))
    d = dense - poly_centroid(c)
    ang = np.arctan2(d[:, 1], d[:, 0])
    r = np.hypot(d[:, 0], d[:, 1])
    order = np.argsort(ang)
    grid = np.linspace(-math.pi, math.pi, samples, endpoint=False)
    prof = np.interp(grid, ang[order], r[order], period=2 * math.pi)
    spec = np.fft.rfft(prof) / samples
    mean = spec[0].real
    return float(2.0 * abs(spec[6]) / mean) if mean > 0 and len(spec) > 6 else math.nan


def calipers(c: np.ndarray) -> tuple[float, float]:
    """(min width, max Feret diameter) of the convex hull."""
    hull = cv2.convexHull(c.astype(np.float32)).reshape(-1, 2).astype(np.float64)
    ang = np.radians(np.arange(0.0, 180.0, _CALIPER_STEP_DEG))
    proj = hull @ np.vstack([np.cos(ang), np.sin(ang)])
    width = proj.max(axis=0) - proj.min(axis=0)
    diff = hull[:, None, :] - hull[None, :, :]
    return float(width.min()), float(np.sqrt((diff**2).sum(-1)).max())


@register(
    "geometry",
    provides=("area_in2", "minrect_long_in", "minrect_short_in", "hull_solidity", "circularity"),
)
def _size_shape(c: np.ndarray, prepared: Prepared, blob: Blob, cfg: FeaturesConfig):
    area = poly_area(c)
    long_, short = minrect(c)
    hull = cv2.convexHull(c.astype(np.float32)).reshape(-1, 2).astype(np.float64)
    hull_area = poly_area(hull)
    return {
        "area_in2": area,
        "minrect_long_in": long_,
        "minrect_short_in": short,
        "hull_solidity": area / hull_area if hull_area > 0 else math.nan,
        "circularity": hull_circularity(hull),
    }


@register("hex_harmonic")
def _hex(c: np.ndarray, prepared: Prepared, blob: Blob, cfg: FeaturesConfig):
    if is_elongated(c, cfg):  # a 6-fold profile means nothing on an elongated outline
        return {}
    return {"hex_harmonic": hex_harmonic(c, cfg.radial_samples)}


@register("calipers", provides=("across_flats_in", "across_corners_in"))
def _calipers(c: np.ndarray, prepared: Prepared, blob: Blob, cfg: FeaturesConfig):
    flats, corners = calipers(c)
    return {"across_flats_in": flats, "across_corners_in": corners}
