"""Segmentation: flat-field a frame, then find each part's outer silhouette at sub-pixel precision.

Approach
--------
Preprocessor: divide by the flat-field (belt ~ 1.0), then by a slow per-frame gain tracker
(75th percentile of the ROI centre, so parts passing do not look like flicker or drift)
tracked with an exponential average (``gain_alpha``) to follow gradual lighting drift. Steady
lighting is assumed; flicker is deliberately not handled.

segment(): a pixel is "part" when it deviates from the belt by more than
``deviation_threshold`` (dark OR bright) or sits on a sharp edge (smoothed gradient above
``edge_gradient_threshold``). The mask is closed, then every outer contour is filled, so
holes (washer bores, dark nut bores, bright facets inside a dark head) cannot split or
shrink a part. Components are rejected when they are soft shadows (never deeper than
``shadow_max_depth`` and with no sharp edge) or smaller than ``min_area_in2``; the area limit
is converted to pixels with the local Jacobian of ``PlaneMapper`` at the image centre.

Sub-pixel contour: each integer-pixel contour point is moved along its (smoothed) outward
normal to where the lightly Gaussian-smoothed |flat - 1| profile crosses half way between the level just outside
(belt or soft shadow) and the highest level just inside (never below ``edge_threshold``).
Half way is the true edge of a blurred step, and referencing the outside level stops a
soft shadow next to the part pulling the edge out.
Offsets are median-filtered along the contour. ``area_px`` is the area of the refined contour.

``mask_bbox_px`` is (x0, y0, x1, y1) with x1/y1 exclusive.
"""

from __future__ import annotations

import cv2
import numpy as np

from ..calibration.store import PlaneMapper, RoiCalibration
from ..config import SegmentConfig
from ..contracts import Blob, Prepared
from ..frames import Frame

_SEARCH_PX = 2.5  # sub-pixel search half-range along the normal (the close can leave the mask
# edge ~2 px out on a serrated bolt thread; a wider search pulls it back to the true edge)
_STEP_PX = 0.25
_EDGE_STRIP_IN = 0.15  # belt-edge shading strips at the ROI top/bottom are thinner than this
_EDGE_STRIP_FALLBACK_PX = 12  # same, when no mapper gives the scale
_OUTLIER_GAIN = 0.4  # ignore gain updates from frames this far from the tracked gain
# Dark-head facet capture (see the facet grow in _segment_window). A bright facet painted on a
# dark bolt head reads just BELOW the belt (~0.96 of belt here), so the brighter-than-belt grow
# misses it and the head silhouette flickers frame to frame. Flood a narrow band just below belt
# -- above the darker soft-shadow halo, below the belt itself -- seeded only from pixels that
# adjoin the deep-dark head (the dilate bridges the facet/head gradient ramp). A soft shadow ring
# lies against a part's bright rim (not the deep-dark head) and is darker than the band, so it is
# never captured; the belt (above the band) is excluded, so the flood cannot escape onto it.
_FACET_BAND_LO = 0.955  # facet band lower bound (flat units, belt = 1.0); below is shadow/ramp
_FACET_BAND_HI = 0.992  # facet band upper bound; above is belt
_FACET_DARK_LEVEL = 0.50  # a facet is grown only where it adjoins a part this much darker than belt
_FACET_SEED_DILATE_PX = 9  # bridges the facet/head gradient ramp between the band and the dark head
_EDGE_BAND_FRAC = 0.12  # bright belt-edge band cleared within this fraction of the strip depth


class Preprocessor:
    """Flat-field and slow gain correction. ``flat=None`` uses a constant belt level."""

    def __init__(self, flat: np.ndarray | None, roi: RoiCalibration, cfg: SegmentConfig) -> None:
        self.flat = flat
        self.roi = roi
        self.cfg = cfg
        self._gain: float | None = None
        # One multiply per frame instead of two divides.
        self._inv_flat = (1.0 / flat).astype(np.float32) if flat is not None else None

    def apply(self, frame: Frame) -> Prepared:
        img = frame.image
        h, w = img.shape
        if self._inv_flat is None:  # no flat-field: constant belt level, built once
            self._inv_flat = np.ones((h, w), np.float32)
        inv_flat = self._inv_flat
        r = self.roi
        top, bottom = max(r.top, 0), min(r.bottom, h)
        left, right = max(r.left, 0), min(r.right, w)
        ch, cw = bottom - top, right - left
        cy0, cy1 = top + ch // 4, bottom - ch // 4
        cx0, cx1 = left + cw // 4, right - cw // 4
        # Gain is measured on the (small) ROI centre only, before the full-frame multiply.
        centre = img[cy0:cy1:2, cx0:cx1:2].astype(np.float32) * inv_flat[cy0:cy1:2, cx0:cx1:2]
        measured = float(np.percentile(centre, 75)) if centre.size else 1.0
        if self._gain is None:
            self._gain = max(measured, 1e-6)
        # Slow tracker for gradual lighting drift (steady lighting is assumed; flicker is
        # not handled). Frames far from the tracked level are ignored as outliers.
        if abs(measured / self._gain - 1.0) < _OUTLIER_GAIN:
            self._gain += self.cfg.gain_alpha * (measured - self._gain)
        gain = self._gain
        out = cv2.multiply(img, inv_flat, dtype=cv2.CV_32F, scale=1.0 / gain)
        # Outside the ROI there is no belt to compare with: call it clean belt.
        out[:top] = 1.0
        out[bottom:] = 1.0
        out[:, :left] = 1.0
        out[:, right:] = 1.0
        return Prepared(frame.seq, frame.t_capture_ns, out, r, gain)


_FILL_GAP_PX = 9  # hole-wall gap bridged when filling enclosed regions (bores, shank highlights)


def _fill_enclosed(mask: np.ndarray, gap_px: int = _FILL_GAP_PX) -> np.ndarray:
    """Fill holes, including holes whose wall has a gap narrower than ``gap_px``.

    Interior regions (washer bores, bright shank highlights between dark edges) can leak
    to the outside through a one-pixel gap, which would make the external contour dive in.
    Holes are found on a heavily closed copy; the outline itself is left as it was, apart
    from closing pixels that bridge to a hole.
    """
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (gap_px, gap_px))
    big = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    cnts, _ = cv2.findContours(big, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(big)
    cv2.drawContours(filled, cnts, -1, 1, -1)
    holes = (filled > 0) & (big == 0)
    if not holes.any():
        return mask
    # Closing pixels that form a bridge (they connect to a hole) are kept too, so the hole
    # really is sealed; closing pixels in outline notches (thread valleys) are dropped.
    bridge = ((big > 0) & (mask == 0)) | holes
    _, lab = cv2.connectedComponents(bridge.astype(np.uint8), connectivity=8)
    lut = np.zeros(int(lab.max()) + 1, bool)
    lut[lab[holes]] = True
    lut[0] = False
    sealed = lut[lab]
    return (mask.astype(bool) | sealed).astype(np.uint8)


def _px2_per_in2(mapper: PlaneMapper, shape: tuple[int, int]) -> float:
    """Pixels^2 per inch^2 at the image centre, from the mapper's local Jacobian."""
    cy, cx = shape[0] / 2.0, shape[1] / 2.0
    pts = mapper.px_to_in(np.array([[cx, cy], [cx + 1, cy], [cx, cy + 1]]))
    jac = np.column_stack([pts[1] - pts[0], pts[2] - pts[0]])  # inches per px
    return 1.0 / abs(float(np.linalg.det(jac)))


def _refine(contour: np.ndarray, dev: np.ndarray, edge_threshold: float) -> np.ndarray:
    """Move integer contour points (N,2) along the outward normal to the sub-pixel edge."""
    n = len(contour)
    if n < 8:
        return contour.astype(np.float32)
    pts = contour.astype(np.float32)
    k = min(3, n // 4)
    tang = np.roll(pts, -k, axis=0) - np.roll(pts, k, axis=0)
    tang /= np.maximum(np.linalg.norm(tang, axis=1, keepdims=True), 1e-6)
    # findContours returns the outer contour counter-clockwise on screen (y down), so the
    # outward normal is the tangent rotated by (ty, -tx); verified by the sign test below.
    normal = np.column_stack([tang[:, 1], -tang[:, 0]])
    centroid = pts.mean(axis=0)
    if float(np.sum((pts - centroid) * normal)) < 0:
        normal = -normal
    s = np.arange(_SEARCH_PX, -_SEARCH_PX - 1e-6, -_STEP_PX, dtype=np.float32)  # outside -> in
    mx = pts[:, 0:1] + normal[:, 0:1] * s[None, :]
    my = pts[:, 1:2] + normal[:, 1:2] * s[None, :]
    prof = cv2.remap(
        dev,
        mx.astype(np.float32),
        my.astype(np.float32),
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    inside = s <= -1.0
    height = prof[:, inside].max(axis=1)
    base = prof[:, :3].mean(axis=1)  # just outside: belt, or the soft shadow next to the part
    level = np.maximum(base + 0.5 * (height - base), edge_threshold)[:, None]
    above = prof >= level
    first = np.argmax(above, axis=1)
    found = above[np.arange(n), first] & (first > 0)
    i1 = np.clip(first, 1, len(s) - 1)
    p0, p1 = prof[np.arange(n), i1 - 1], prof[np.arange(n), i1]
    frac = np.clip((level[:, 0] - p0) / np.maximum(p1 - p0, 1e-6), 0.0, 1.0)
    offset = s[i1 - 1] + frac * (s[i1] - s[i1 - 1])
    offset = np.where(found, offset, 0.0).astype(np.float32)
    # Median filter along the (closed) contour to suppress noise.
    w = 5
    padded = np.concatenate([offset[-w:], offset, offset[:w]])
    stack = np.stack([padded[i : i + n] for i in range(2 * w + 1)])
    offset = np.median(stack, axis=0).astype(np.float32)
    return pts + normal * offset[:, None]


def _segment_window(
    flat: np.ndarray,
    win: tuple[int, int, int, int],
    roi_lim: tuple[int, int, int, int],
    cfg: SegmentConfig,
    min_area_px: float,
    strip_px: float,
) -> list[Blob]:
    """Full-resolution segmentation of ``flat[y0:y1, x0:x1]``; blobs in full-image coords.

    ``roi_lim`` is (top, bottom, left, right) in full-image coordinates.
    """
    x0, y0, x1, y1 = win
    top, bottom, left, right = roi_lim
    sub = flat[y0:y1, x0:x1]
    sm = cv2.GaussianBlur(sub, (0, 0), cfg.subpixel_smooth_px) if cfg.subpixel_smooth_px else sub
    dev = np.abs(sm - 1.0)
    gx = cv2.Sobel(sm, cv2.CV_32F, 1, 0, ksize=3, scale=1 / 8)
    gy = cv2.Sobel(sm, cv2.CV_32F, 0, 1, ksize=3, scale=1 / 8)
    grad = cv2.magnitude(gx, gy)
    valid = np.zeros(sub.shape, bool)
    vt, vb = max(top - y0, 0), min(bottom - y0, y1 - y0)
    vl, vr = max(left - x0, 0), min(right - x0, x1 - x0)
    valid[vt:vb, vl:vr] = True
    strong = ((dev > cfg.deviation_threshold) | (grad > cfg.edge_gradient_threshold)) & valid
    mask = strong.astype(np.uint8)
    # Bright facets (as bright as the belt, on the rim of a dark head): grow into pixels
    # that are brighter than the belt by edge_threshold, but only where they touch the strong
    # mask. Darker pixels are never grown into, so soft shadows stay out.
    weak = ((sm - 1.0) > cfg.edge_threshold) & valid
    if weak.any() and mask.any():
        n_lab, lab = cv2.connectedComponents((weak | strong).astype(np.uint8), connectivity=8)
        lut = np.zeros(n_lab, bool)
        lut[lab[strong]] = True
        lut[0] = False
        grown = lut[lab] & weak
        mask = (mask | grown.astype(np.uint8)).astype(np.uint8)
    # Below-belt facet grow (see the module constants): a bright facet on a dark head reads just
    # below the belt, so the grow above misses it. Flood a narrow band just below belt, seeded
    # only from band pixels that adjoin the deep-dark head, so the head silhouette is captured
    # whole every frame without pulling in the soft-shadow halo or leaking onto the belt.
    facet = (sm > _FACET_BAND_LO) & (sm < _FACET_BAND_HI) & valid & ~strong
    # Keep the facet grow off the belt-edge shading strips at the ROI top/bottom: they sit in the
    # same below-belt band, so without this an edge-near head floods down the strip and the part
    # merges with it into one tall blob (the thin-strip drop below never fires on the merged blob).
    rows_full = np.arange(y0, y1, dtype=np.float32)[:, None]
    in_strip = (rows_full < top + strip_px) | (rows_full >= bottom - strip_px)
    facet &= ~in_strip
    deep = (strong & (sm < _FACET_DARK_LEVEL)).astype(np.uint8)
    if facet.any() and deep.any():
        ksz = _FACET_SEED_DILATE_PX
        near = cv2.dilate(deep, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksz, ksz)))
        seed = facet & (near > 0)
        if seed.any():
            n_lab, lab = cv2.connectedComponents(facet.astype(np.uint8), connectivity=8)
            lut = np.zeros(n_lab, bool)
            lut[lab[seed]] = True
            lut[0] = False
            mask = (mask | lut[lab].astype(np.uint8)).astype(np.uint8)
    # Clear the belt-edge highlight band at the ROI top/bottom before closing and filling: the
    # flat-field leaves a thin brighter-than-belt sliver right at each belt edge, and a part whose
    # silhouette reaches the edge (a bolt lying head-to-edge) otherwise fuses with this full-width
    # band into one oversized blob -- the thin-strip drop below never fires on the merged blob, so
    # its min-area rectangle gains the band's width and it misclassifies. Part silhouettes read at
    # or below belt, so clearing only the brighter-than-belt pixels in a thin margin at the very
    # boundary drops the band and leaves the part; the dark edge-shading band is still handled by
    # the thin-strip drop after contouring. The margin stays narrow so it clears just the band.
    band_px = max(round(strip_px * _EDGE_BAND_FRAC), cfg.close_kernel_px + 2)
    near_edge = (rows_full < top + band_px) | (rows_full >= bottom - band_px)
    mask[near_edge & (sm > 1.0 + cfg.edge_threshold)] = 0
    if cfg.close_kernel_px > 1:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.close_kernel_px,) * 2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    mask = _fill_enclosed(mask)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

    blobs: list[Blob] = []
    for c in contours:
        if cv2.contourArea(c) < min_area_px:
            continue
        bx, by, bw, bh = cv2.boundingRect(c)
        # Thin strip hugging the ROI top/bottom: belt-edge shading that the (smoothed)
        # flat-field does not remove, not a part.
        if bh <= strip_px and (by + y0 <= top + 1 or by + y0 + bh >= bottom - 1):
            continue
        comp = np.zeros((bh, bw), np.uint8)
        cv2.drawContours(comp, [c - [bx, by]], -1, 1, -1)
        d_reg, g_reg = dev[by : by + bh, bx : bx + bw], grad[by : by + bh, bx : bx + bw]
        sel = comp > 0
        if (
            d_reg[sel].max() < cfg.shadow_max_depth
            and g_reg[sel].max() < cfg.edge_gradient_threshold
        ):
            continue  # soft, shallow, edgeless: shadow
        pts = c.reshape(-1, 2)
        fine = _refine(pts, dev, cfg.edge_threshold)
        area = abs(float(cv2.contourArea(fine.astype(np.float32))))
        if area < min_area_px:
            continue
        fine = fine + np.array([x0, y0], np.float32)
        bx0, by0 = bx + x0, by + y0
        blobs.append(
            Blob(
                contour_px=fine.astype(np.float32),
                mask_bbox_px=(bx0, by0, bx0 + bw, by0 + bh),
                area_px=area,
                touches_edge=bool(bx0 <= left or bx0 + bw >= right),
            )
        )
    return blobs


def _merge_boxes(boxes: list[list[int]]) -> list[list[int]]:
    """Union boxes (x0, y0, x1, y1) that overlap, until none do."""
    boxes = [list(b) for b in boxes]
    merged = True
    while merged:
        merged = False
        out: list[list[int]] = []
        for b in boxes:
            for o in out:
                if b[0] < o[2] and o[0] < b[2] and b[1] < o[3] and o[1] < b[3]:
                    o[0], o[1] = min(o[0], b[0]), min(o[1], b[1])
                    o[2], o[3] = max(o[2], b[2]), max(o[3], b[3])
                    merged = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


def _coarse_crops(
    flat: np.ndarray,
    win: tuple[int, int, int, int],
    cfg: SegmentConfig,
    min_area_px: float,
    strip_px: float,
) -> list[tuple[int, int, int, int]]:
    """Half-resolution detection of candidate part regions; padded full-res crop boxes."""
    x0, y0, x1, y1 = win
    half = cv2.pyrDown(flat[y0:y1, x0:x1])
    # Slightly below the full-res threshold: pyrDown averages thin detail toward belt. Much
    # lower would pick up belt-edge shading strips, which are skipped below anyway.
    cmask = (cv2.absdiff(half, 1.0) > 0.8 * cfg.deviation_threshold).astype(np.uint8)
    cmask = cv2.dilate(cmask, np.ones((3, 3), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(cmask, connectivity=8)
    min_half = max(2.0, min_area_px / 16.0)
    pad = cfg.crop_pad_px
    boxes: list[list[int]] = []
    for i in range(1, n):
        bx, by, bw, bh, area = (int(v) for v in stats[i])
        if area < min_half:
            continue
        if 2 * bh <= strip_px + 4 and (by <= 2 or by + bh >= half.shape[0] - 2):
            continue  # belt-edge shading strip hugging the ROI top/bottom
        boxes.append(
            [
                max(x0 + 2 * bx - pad, x0),
                max(y0 + 2 * by - pad, y0),
                min(x0 + 2 * (bx + bw) + pad, x1),
                min(y0 + 2 * (by + bh) + pad, y1),
            ]
        )
    return [(b[0], b[1], b[2], b[3]) for b in _merge_boxes(boxes)]


def segment(p: Prepared, cfg: SegmentConfig, mapper: PlaneMapper | None = None) -> list[Blob]:
    """Blobs (outer silhouettes) in a prepared frame, left to right.

    ``mapper`` converts ``cfg.min_area_in2`` to pixels; without it only specks under 8 px
    are dropped (the pipeline always passes it). With ``cfg.coarse_scale == 2`` a half-res
    pass finds candidate regions and the full-res code runs only on their padded crops;
    ``coarse_scale == 1`` runs it on the whole ROI (the reference path).
    """
    flat = p.flat
    h, w = flat.shape
    r = p.roi
    top, bottom = max(r.top, 0), min(r.bottom, h)
    left, right = max(r.left, 0), min(r.right, w)
    if bottom <= top or right <= left:
        return []
    pad = 4
    win = (max(left - pad, 0), max(top - pad, 0), min(right + pad, w), min(bottom + pad, h))

    min_area_px = 8.0
    strip_px = float(_EDGE_STRIP_FALLBACK_PX)
    if mapper is not None:
        px2 = _px2_per_in2(mapper, flat.shape)
        min_area_px = max(min_area_px, cfg.min_area_in2 * px2)
        strip_px = _EDGE_STRIP_IN * px2**0.5

    roi_lim = (top, bottom, left, right)
    if cfg.coarse_scale == 1:
        wins = [win]
    else:
        wins = _coarse_crops(flat, win, cfg, min_area_px, strip_px)
    blobs: list[Blob] = []
    for wn in wins:
        blobs.extend(_segment_window(flat, wn, roi_lim, cfg, min_area_px, strip_px))
    blobs.sort(key=lambda b: b.mask_bbox_px[0])
    return blobs
