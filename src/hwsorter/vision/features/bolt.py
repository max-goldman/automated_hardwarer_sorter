"""Bolt profile from the belt-plane contour: width along the long axis (NaN if not elongated).

``shank_width_in`` is the MAJOR (thread-crest) diameter: each shank edge is detrended by a line
fit, cut into windows longer than any thread pitch in scope, and the crest level is the MEDIAN of
the per-window outermost points. A window max finds the crest whatever the thread phase (the top
and bottom crests of a side-on thread are half a pitch apart, so a per-slice width mixes crest and
root), and the median over windows ignores a few crests notched by a highlight or broken by noise.
On an unthreaded shank the window max is the edge itself. This matches the synthetic bolt, whose
``shank`` dimension is the crest diameter.
"""

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
_CREST_WINDOW_IN = 0.07  # longer than the coarsest thread pitch in scope (1/16 in), so every
# window holds at least one crest
_TIP_FRAC = 1 / 12  # the shank's last samples (chamfered tip) are left out of the shank width
_AXIS_ITERS = 2  # shank-axis refinements of the min-area-rectangle axis


def width_profile(c: np.ndarray, n: int = _SLICES) -> tuple[np.ndarray, np.ndarray]:
    """Rotate so the long axis is x; return (s sample positions, silhouette width at each s)."""
    s, top, bot = edge_profile(c, n)
    return s, np.where(np.isfinite(top) & np.isfinite(bot), top - bot, 0.0)


def long_axis(c: np.ndarray) -> float:
    """Direction (radians) of the long side of the minimum-area rectangle."""
    _, (w, h), ang = cv2.minAreaRect(c.astype(np.float32))
    if w < h:
        ang += 90.0
    return math.radians(ang)


def edge_profile(
    c: np.ndarray, n: int = _SLICES, axis: float | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rotate so ``axis`` (radians; default the min-area rectangle's long side) is x; return
    (s, top edge y, bottom edge y), NaN where empty."""
    a = long_axis(c) if axis is None else axis
    rot = np.array([[math.cos(a), math.sin(a)], [-math.sin(a), math.cos(a)]])
    p = (c - c.mean(axis=0)) @ rot.T
    s = np.linspace(float(p[:, 0].min()), float(p[:, 0].max()), n + 2)[1:-1]
    a_, b_ = p, np.roll(p, -1, axis=0)
    da, db = a_[None, :, 0] - s[:, None], b_[None, :, 0] - s[:, None]  # (n, edges)
    cross = da * db < 0
    t = da / np.where(cross, da - db, 1.0)
    y = a_[None, :, 1] + t * (b_[None, :, 1] - a_[None, :, 1])
    top = np.where(cross, y, -np.inf).max(axis=1)
    bot = np.where(cross, y, np.inf).min(axis=1)
    hit = cross.any(axis=1)
    return s, np.where(hit, top, np.nan), np.where(hit, bot, np.nan)


def _crest_offset(s: np.ndarray, edge: np.ndarray, sign: float) -> tuple[np.ndarray, float]:
    """(fitted edge line at s, median per-window outward crest offset from it)."""
    a, b = np.polyfit(s, edge, 1)
    line = a * s + b
    r = sign * (edge - line)  # outward positive
    step = float(s[1] - s[0]) if len(s) > 1 else 1.0
    w = max(math.ceil(_CREST_WINDOW_IN / max(step, 1e-9)), 1)
    k = len(r) // w
    if k < 1:
        return line, float(r.max())
    peaks = r[: k * w].reshape(k, w).max(axis=1)
    return line, float(np.median(peaks))


def shank_major(s: np.ndarray, top: np.ndarray, bot: np.ndarray) -> float:
    """Crest-to-crest (major) diameter of a shank section, robust to thread phase and notches."""
    ok = np.isfinite(top) & np.isfinite(bot)
    if ok.sum() < 4:
        return math.nan
    s, top, bot = s[ok], top[ok], bot[ok]
    lt, ct = _crest_offset(s, top, 1.0)
    lb, cb = _crest_offset(s, bot, -1.0)
    return float(np.median(lt - lb)) + ct + cb


def _oriented_profile(
    c: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    """(s, width, top, bottom) along the SHANK's own axis, head end first.

    The min-area rectangle of a whole bolt outline is not aligned with the bolt: the wide head
    tilts it (more so when a head facet is missing), and slicing across a tilted axis smears the
    head's end face along s (longer head) and cuts the head obliquely (wrong width). So the axis
    is refined: lines are fitted to the two shank edges in the current frame and the axis is
    rotated by their mean slope, twice. The result does not depend on the bolt's angle in the
    image, only on its outline.
    """
    axis = long_axis(c)
    for it in range(_AXIS_ITERS + 1):
        s, top, bot = edge_profile(c, axis=axis)
        wid = np.where(np.isfinite(top) & np.isfinite(bot), top - bot, 0.0)
        n = len(wid)
        q = max(n // 6, 3)
        head_first = wid[:q].mean() >= wid[-q:].mean()
        tip = max(int(n * _TIP_FRAC), 1)
        sh = slice(n // 4, n - tip) if head_first else slice(tip, n - n // 4)
        ok = np.isfinite(top[sh]) & np.isfinite(bot[sh])
        if it == _AXIS_ITERS or ok.sum() < 8:
            break
        ss = s[sh][ok]
        m = 0.5 * (np.polyfit(ss, top[sh][ok], 1)[0] + np.polyfit(ss, bot[sh][ok], 1)[0])
        d = math.atan(float(m))
        if abs(d) < 1e-4:
            break
        axis += d
    if not head_first:
        s, wid, top, bot = -s[::-1], wid[::-1], -bot[::-1], -top[::-1]
    return s, wid, top, bot


@register("bolt_profile", provides=NAMES)
def _bolt(c: np.ndarray, prepared: Prepared, blob: Blob, cfg: FeaturesConfig):
    if not is_elongated(c, cfg):
        return {}
    s, wid, top, bot = _oriented_profile(resample(c, _MAX_POINTS) if len(c) > _MAX_POINTS else c)
    n = len(wid)
    q = max(n // 6, 3)
    step = float(s[1] - s[0])
    sh = slice(n // 4, n - max(int(n * _TIP_FRAC), 1))
    shank = shank_major(s[sh], top[sh], bot[sh])
    if not math.isfinite(shank):
        return {}
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
