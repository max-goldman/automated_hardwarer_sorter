"""Belt seam detection and handling (task 005).

The seam is a stitched diagonal band that spans the belt width at a fixed angle (degrees from the
travel axis, +x toward +y) and width. This module

* finds it by geometry from the flat-fielded frame: the image is sheared so the seam becomes
  vertical, then collapsed to a 1-D dip profile (cheap; done on a 2x downsampled ROI);
* removes the blobs the segmenter produced from the seam itself (stitches, dots) so it never
  becomes a part;
* learns a seam template (the seam's appearance in seam-registered coordinates) from part-free
  passes and refreshes it slowly;
* when a part overlaps the seam, registers the template to the seam and subtracts it from the
  overlapping blob to recover the part outline (``Blob.on_seam`` set), falling back to a plain
  flag when the template is not ready yet;
* tracks seam sightings to estimate belt speed (needs ``px_per_in``).

Coordinates are full-image pixels (x right, y down). The seam centre line is
``x(y) = x_center_px + (y - H / 2) / tan(angle)``; ``x_center_px`` is the value at ``y = H / 2``.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np

from ..config import SeamConfig
from ..contracts import Blob, Prepared, SeamObs

_DS = 2  # downsample factor for detection
_DEFAULT_PX_PER_IN = 367.0  # only sizes search windows when the scale is unknown
_MIN_CONTRAST = 0.02  # minimum seam dip (flat units, belt = 1.0) to declare it present
_DIP_CLIP = 0.2  # per-pixel dip is clipped to this so parts do not dominate the profile
_MIN_COVERAGE = 0.2  # fraction of ROI rows a profile column needs (was 0.4: too high, so the seam
# went undetected while entering/exiting the view and its blobs leaked through as spurious parts)
_SPEED_COVERAGE = 0.7  # fraction of the seam line that must be in view for a speed sighting
_SPEED_MIN_SIGHTINGS = 5
_PASS_GAP_S = 0.15  # time without a seam sighting that ends a pass
_PASS_JUMP_PX = 60.0  # backward jump of the seam centre that starts a new pass
_REPAIR_EPS = 0.08  # |flat / template - 1| below this is "explained by the seam"
_MARGIN_FRAC = 0.15  # band margin (fraction of width) that still counts as seam stitching
_SEAM_BAND_FRAC = 0.80  # a blob with this area fraction inside the band is seam, not a part
_SEAM_RESIDUE_FRAC = 0.80  # on a seam, output blobs this far inside the band are seam residue


@dataclass(frozen=True, slots=True, eq=False)
class SeamTemplate:
    """Seam appearance in seam-registered coordinates.

    ``patch[r, c]`` is the flat value (belt ~ 1.0) at image row ``top + r`` and signed x offset
    ``c - half`` pixels from the seam centre line (same row). ``angle_deg`` is the shear used."""

    patch: np.ndarray  # float32 (rows, 2 * half + 1)
    top: int
    half: int
    angle_deg: float
    n_frames: int


@dataclass(slots=True)
class _Geom:
    """Cached shear geometry for one (frame shape, ROI)."""

    key: tuple
    r: int  # downsampled crop rows
    ncols: int  # canvas columns
    sm0: int  # canvas column 0 is s_small = sm0
    m: np.ndarray  # 2x3 inverse-map shear matrix
    den: np.ndarray  # (r, ncols) warped ones
    den_col: np.ndarray
    den_top: np.ndarray
    den_bot: np.ndarray
    valid: np.ndarray  # bool (ncols,)
    roi_mask: np.ndarray  # uint8 (H, W), 1 inside the ROI


@dataclass(slots=True)
class _Det:
    present: bool
    xc: float
    angle_deg: float
    contrast: float
    coverage: float  # fraction of ROI rows where the seam centre line is inside the ROI columns


class SeamHandler:
    """Per-frame seam detection and blob repair. ``process`` is single-threaded.

    ``px_per_in`` (pixels per belt inch, from the scale calibration) is needed for the belt speed
    and for the width check; without it the handler still detects, removes and repairs."""

    def __init__(
        self,
        cfg: SeamConfig,
        template: SeamTemplate | None = None,
        px_per_in: float | None = None,
    ) -> None:
        self._cfg = cfg
        self._ppi = px_per_in
        a = math.radians(cfg.angle_deg)
        self._sin, self._cot = math.sin(a), 1.0 / math.tan(a)
        self._geom: _Geom | None = None
        self._cap = float(cfg.template_frames)
        self._sum: np.ndarray | None = None
        self._cnt: np.ndarray | None = None
        self._top = 0
        self._half = self._default_half()
        self._learned = 0
        self._ready_patch: np.ndarray | None = None
        if template is not None:
            self._half, self._top = template.half, template.top
            self._sum = template.patch.astype(np.float32).copy() * self._cap
            self._cnt = np.full(template.patch.shape, self._cap, np.float32)
            self._learned = max(template.n_frames, cfg.template_frames)
            self._ready_patch = template.patch.astype(np.float32).copy()
        self._sightings: deque[tuple[float, float]] = deque(maxlen=cfg.speed_window)
        self._speed_px_s: float | None = None
        self._last_t_s = -math.inf

    # ------------------------------------------------------------------ public

    def set_scale(self, px_per_in: float) -> None:
        self._ppi = px_per_in

    @property
    def template_ready(self) -> bool:
        return self._ready_patch is not None

    @property
    def template(self) -> SeamTemplate | None:
        if self._ready_patch is None:
            return None
        return SeamTemplate(
            self._ready_patch.copy(), self._top, self._half, self._cfg.angle_deg, self._learned
        )

    def belt_speed_in_s(self) -> float | None:
        if self._speed_px_s is None or not self._ppi:
            return None
        return self._speed_px_s / self._ppi

    def process(self, p: Prepared, blobs: list[Blob]) -> tuple[list[Blob], SeamObs | None]:
        """Drop seam-only blobs, repair blobs overlapping the seam, report the seam."""
        if not self._cfg.enabled:
            return list(blobs), None
        g = self._geometry(p)
        det = self._detect(p, g, None)
        if not det.present:
            return list(blobs), self._obs(p, det, g, present=False)

        width_px = self._width_px()
        hw = width_px / 2.0
        h_img = p.flat.shape[0]
        frag, overlap, near = self._classify(blobs, det.xc, h_img, hw)

        if overlap:
            # Re-estimate the seam position with the overlapping parts masked out.
            det2 = self._detect(p, g, [blobs[i] for i in overlap])
            if det2.present and abs(det2.xc - det.xc) > 0.05:
                det = det2
                frag, overlap, near = self._classify(blobs, det.xc, h_img, hw)

        if not self.template_ready:
            # Bootstrap: with no template the seam cannot be subtracted, so a blob that lies
            # almost entirely within the seam band is the seam itself, not a part. Drop it and
            # let the template learn from this part-free view; once the template is ready,
            # _repair subtracts the seam from overlapping blobs instead of this crude drop.
            seam_only = {
                i
                for i in range(len(blobs))
                if i not in frag
                and self._band_area_frac(blobs[i], det.xc, h_img, hw) >= _SEAM_BAND_FRAC
            }
            frag |= seam_only
            overlap -= seam_only
            near -= seam_only

        out: list[Blob] = []
        for i, b in enumerate(blobs):
            if i in frag:
                continue
            if i in overlap:
                out.extend(self._repair(p, b, det.xc, width_px))
            else:
                out.append(b)

        # A real part straddles the seam and sticks well out of the band; seam residue (including
        # the strips _repair leaves where the seam meets the belt edge at the ROI top/bottom) sits
        # within the band or hugs the ROI edge. Drop those so the seam never yields a part.
        out = [b for b in out if not self._is_seam_residue(b, det.xc, h_img, hw, p.roi)]

        if not near:
            self._learn(p, g, det.xc)
        self._update_speed(p, det)
        return out, self._obs(p, det, g, present=True)

    def _is_seam_residue(self, b: Blob, xc: float, h_img: int, hw: float, roi) -> bool:
        """A blob left over from the seam itself (within the band, or a belt-edge strip)."""
        if self._band_area_frac(b, xc, h_img, hw) >= _SEAM_RESIDUE_FRAC:
            return True
        # The seam meets the belt edge at the ROI top/bottom, where _repair leaves strips. Parts
        # travel left-right and never clip the ROI top/bottom, so a blob hugging it on a seam
        # frame is belt-edge/seam residue, not a part.
        _, y0, _, y1 = b.mask_bbox_px
        return y0 <= roi.top + 1 or y1 >= roi.bottom - 1

    # ------------------------------------------------------------------ geometry helpers

    def _width_px(self) -> float:
        return self._cfg.width_in * (self._ppi or _DEFAULT_PX_PER_IN)

    def _default_half(self) -> int:
        half_x = 0.5 * self._width_px() / self._sin
        return math.ceil(1.6 * half_x) + 4

    def _geometry(self, p: Prepared) -> _Geom:
        h_img, w_img = p.flat.shape
        roi = p.roi
        key = (h_img, w_img, roi.top, roi.bottom, roi.left, roi.right)
        if self._geom is not None and self._geom.key == key:
            return self._geom
        ds, cot = _DS, self._cot
        r = (roi.bottom - roi.top) // ds
        # seam centre positions s (x at y = H/2) for which the line meets the ROI
        s_min = roi.left - (roi.bottom - 1 - h_img / 2) * cot
        s_max = roi.right - 1 - (roi.top - h_img / 2) * cot
        off = (ds - 1) / 2
        sm0 = math.floor((s_min - roi.left - off) / ds)
        ncols = math.ceil((s_max - roi.left - off) / ds) - sm0 + 2
        m = np.array(
            [[1.0, cot, sm0 + (off + roi.top - h_img / 2) * cot / ds], [0.0, 1.0, 0.0]], np.float32
        )
        ones = np.ones((r, (roi.right - roi.left) // ds), np.float32)
        den = self._warp(ones, m, ncols, r)
        den_col = den.sum(axis=0)
        valid = den_col >= _MIN_COVERAGE * max(float(den_col.max()), 1e-6)
        roi_mask = np.zeros((h_img, w_img), np.uint8)
        roi_mask[roi.top : roi.bottom, roi.left : roi.right] = 1
        self._geom = _Geom(
            key,
            r,
            ncols,
            sm0,
            m,
            den,
            den_col,
            den[: r // 2].sum(axis=0),
            den[r // 2 :].sum(axis=0),
            valid,
            roi_mask,
        )
        return self._geom

    @staticmethod
    def _warp(img: np.ndarray, m: np.ndarray, ncols: int, rows: int) -> np.ndarray:
        return cv2.warpAffine(
            img,
            m,
            (ncols, rows),
            flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )

    def _x_at(self, xc: float, y: np.ndarray | float, h_img: int) -> np.ndarray | float:
        return xc + (y - h_img / 2.0) * self._cot

    # ------------------------------------------------------------------ detection

    def _detect(self, p: Prepared, g: _Geom, exclude: list[Blob] | None) -> _Det:
        roi = p.roi
        h_img, w_img = p.flat.shape
        crop = p.flat[roi.top : roi.bottom, roi.left : roi.right]
        small = cv2.resize(
            crop,
            ((roi.right - roi.left) // _DS, g.r),
            interpolation=cv2.INTER_AREA,
        )
        dip = np.clip(1.0 - small, -0.02, _DIP_CLIP)
        den, den_col, den_top, den_bot, valid = g.den, g.den_col, g.den_top, g.den_bot, g.valid
        if exclude:
            wgt = np.ones(small.shape, np.uint8)
            for b in exclude:
                pts = np.round((b.contour_px - [roi.left, roi.top]) / _DS * 8).astype(np.int32)
                cv2.fillPoly(wgt, [pts], 0, shift=3)
            wgt = cv2.erode(wgt, np.ones((5, 5), np.uint8))  # grow the excluded area 2 px
            wf = wgt.astype(np.float32)
            both = self._warp(np.dstack([dip * wf, wf]), g.m, g.ncols, g.r)
            num_img, den = both[..., 0], both[..., 1]
            den_col = den.sum(axis=0)
            den_top, den_bot = den[: g.r // 2].sum(axis=0), den[g.r // 2 :].sum(axis=0)
            valid = den_col >= _MIN_COVERAGE * max(float(g.den_col.max()), 1e-6)
        else:
            num_img = self._warp(dip, g.m, g.ncols, g.r)
        num_col = num_img.sum(axis=0)
        num_top, num_bot = num_img[: g.r // 2].sum(axis=0), num_img[g.r // 2 :].sum(axis=0)

        absent = _Det(False, math.nan, self._cfg.angle_deg, 0.0, 0.0)
        if not valid.any():
            return absent
        d = np.where(valid, num_col / np.maximum(den_col, 1e-6), 0.0)
        baseline = float(np.median(d[valid]))
        bw = max(3, round(0.5 * self._width_px() / self._sin / _DS * 2))
        score = np.convolve(d, np.ones(bw) / bw, mode="same")
        score[~valid] = -1.0
        k = int(np.argmax(score))
        contrast = float(score[k]) - baseline
        if contrast < _MIN_CONTRAST:
            return absent

        lo, hi = max(0, k - bw), min(len(d), k + bw + 1)
        centre = self._centroid(d, valid, baseline, lo, hi)
        angle = self._cfg.angle_deg
        a_tol = self._cfg.angle_tol_deg
        ct = self._centroid(num_top / np.maximum(den_top, 1e-6), den_top > 0, baseline, lo, hi)
        cb = self._centroid(num_bot / np.maximum(den_bot, 1e-6), den_bot > 0, baseline, lo, hi)
        if not (math.isnan(ct) or math.isnan(cb)):
            cot_true = self._cot + (cb - ct) / (g.r / 2.0)
            angle = math.degrees(math.atan2(1.0, cot_true))
            if abs(angle - self._cfg.angle_deg) > a_tol:
                return absent
        if self._ppi and not self._width_ok(score, valid, k, baseline, contrast):
            return absent

        off = (_DS - 1) / 2
        xc = roi.left + off + (centre + g.sm0) * _DS
        ys = np.array([roi.top, roi.bottom - 1], float)
        xs = self._x_at(xc, ys, h_img)
        inside = (xs >= roi.left) & (xs <= roi.right - 1)
        yy = np.arange(roi.top, roi.bottom, 4, dtype=np.float64)
        xx = self._x_at(xc, yy, h_img)
        coverage = float(np.mean((xx >= roi.left) & (xx <= roi.right - 1)))
        _ = inside, w_img
        return _Det(True, float(xc), angle, contrast, coverage)

    @staticmethod
    def _centroid(d: np.ndarray, valid: np.ndarray, baseline: float, lo: int, hi: int) -> float:
        w = np.clip(d[lo:hi] - baseline, 0.0, None) * valid[lo:hi]
        tot = float(w.sum())
        if tot <= 1e-9:
            return math.nan
        return float((w * np.arange(lo, hi)).sum() / tot)

    def _width_ok(
        self, score: np.ndarray, valid: np.ndarray, k: int, baseline: float, contrast: float
    ) -> bool:
        half_level = baseline + 0.5 * contrast
        i = k
        while i > 0 and valid[i - 1] and score[i - 1] >= half_level:
            i -= 1
        j = k
        while j < len(score) - 1 and valid[j + 1] and score[j + 1] >= half_level:
            j += 1
        if (
            i == 0
            or j == len(score) - 1
            or not (valid[max(i - 1, 0)] and valid[min(j + 1, len(score) - 1)])
        ):
            return True  # clipped by the edge of the search range: cannot judge
        fwhm_perp = (j - i + 1) * _DS * self._sin
        return abs(fwhm_perp - self._width_px()) <= self._cfg.width_tol_in * (self._ppi or 0.0) * 2

    # ------------------------------------------------------------------ blobs

    def _dist(self, contour: np.ndarray, xc: float, h_img: int) -> np.ndarray:
        """Signed perpendicular distance (px) of contour points from the seam centre line."""
        return (contour[:, 0] - self._x_at(xc, contour[:, 1], h_img)) * self._sin

    def _classify(
        self, blobs: list[Blob], xc: float, h_img: int, hw: float
    ) -> tuple[set[int], set[int], set[int]]:
        frag: set[int] = set()
        overlap: set[int] = set()
        near: set[int] = set()
        lim = hw * (1.0 + 2 * _MARGIN_FRAC)
        near_lim = self._half * self._sin
        for i, b in enumerate(blobs):
            d = self._dist(b.contour_px, xc, h_img)
            lo, hi = float(d.min()), float(d.max())
            if lo >= -lim and hi <= lim:
                frag.add(i)
            elif (lo <= hw and hi >= -hw) and (np.any(np.abs(d) <= hw) or (lo < -hw and hi > hw)):
                overlap.add(i)
                near.add(i)
            elif hi >= -near_lim and lo <= near_lim:
                near.add(i)
        return frag, overlap, near

    def _band_area_frac(self, b: Blob, xc: float, h_img: int, hw: float) -> float:
        """Fraction of the blob's filled area that lies within the seam band."""
        c = b.contour_px
        x0, y0 = np.floor(c.min(axis=0)).astype(int)
        x1, y1 = np.ceil(c.max(axis=0)).astype(int)
        m = np.zeros((int(y1 - y0) + 1, int(x1 - x0) + 1), np.uint8)
        cv2.fillPoly(m, [np.round(c - [x0, y0]).astype(np.int32)], 1)
        ys, xs = np.nonzero(m)
        if ys.size == 0:
            return 0.0
        dist = (xs + x0 - self._x_at(xc, ys + y0, h_img)) * self._sin
        lim = hw * (1.0 + 2 * _MARGIN_FRAC)
        return float(np.mean(np.abs(dist) <= lim))

    def _template_image(
        self, xc: float, x0: int, x1: int, y0: int, y1: int, h_img: int
    ) -> np.ndarray:
        """Template resampled onto image window [x0,x1) x [y0,y1); belt (1.0) where unknown."""
        assert self._ready_patch is not None
        ys = np.arange(y0, y1, dtype=np.float32)
        xs = np.arange(x0, x1, dtype=np.float32)
        map_x = xs[None, :] - self._x_at(xc, ys, h_img)[:, None].astype(np.float32) + self._half
        map_y = np.repeat((ys - self._top)[:, None], x1 - x0, axis=1)
        return cv2.remap(
            self._ready_patch,
            map_x.astype(np.float32),
            map_y.astype(np.float32),
            cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=1.0,
        )

    def _repair(self, p: Prepared, b: Blob, xc: float, width_px: float) -> list[Blob]:
        """Blob overlapping the seam: subtract the template, return the part blob(s)."""
        if self._ready_patch is None:
            return [_with_seam(b)]
        h_img, w_img = p.flat.shape
        roi = p.roi
        pad = 4
        c = b.contour_px
        x0 = max(math.floor(c[:, 0].min()) - pad, 0)
        x1 = min(math.ceil(c[:, 0].max()) + pad + 1, w_img)
        y0 = max(math.floor(c[:, 1].min()) - pad, 0)
        y1 = min(math.ceil(c[:, 1].max()) + pad + 1, h_img)
        mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
        pts = np.round((c - [x0, y0]) * 8).astype(np.int32)
        cv2.fillPoly(mask, [pts], 1, shift=3)

        tmpl = self._template_image(xc, x0, x1, y0, y1, h_img)
        ratio = p.flat[y0:y1, x0:x1] / np.maximum(tmpl, 0.3)
        ys = np.arange(y0, y1, dtype=np.float32)
        xs = np.arange(x0, x1, dtype=np.float32)
        dist = (xs[None, :] - self._x_at(xc, ys, h_img)[:, None]) * self._sin
        hw = width_px / 2.0
        inband = np.abs(dist) <= hw * (1.0 + 2 * _MARGIN_FRAC)
        explained = inband & (np.abs(ratio - 1.0) < _REPAIR_EPS)
        clean = (mask > 0) & ~explained
        clean_u8 = clean.astype(np.uint8)

        k = max(3, round(width_px) | 1)
        closed = cv2.morphologyEx(
            clean_u8, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        )
        clean_u8 = np.where(inband & (mask > 0), closed, clean_u8).astype(np.uint8)
        clean_u8 = cv2.morphologyEx(clean_u8, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

        n, labels, stats, _ = cv2.connectedComponentsWithStats(clean_u8, connectivity=8)
        if n <= 1:
            return []
        areas = stats[1:, cv2.CC_STAT_AREA].astype(np.float64)
        # A piece smaller than the smallest part the segmenter keeps is seam residue (a lone
        # stitch the template did not explain), never a part.
        min_px = self._cfg.min_piece_in2 * (self._ppi or _DEFAULT_PX_PER_IN) ** 2
        keep = [i + 1 for i in range(n - 1) if areas[i] >= max(0.2 * areas.max(), 30.0, min_px)]
        out: list[Blob] = []
        for lab in keep:
            comp = _fill_holes((labels == lab).astype(np.uint8))
            contour = _subpixel_contour(comp)
            if contour is None:
                continue
            contour = contour + np.array([x0, y0], np.float32)
            bx0, by0 = np.floor(contour.min(axis=0)).astype(int)
            bx1, by1 = np.ceil(contour.max(axis=0)).astype(int)
            out.append(
                Blob(
                    contour_px=contour,
                    mask_bbox_px=(int(bx0), int(by0), int(bx1), int(by1)),
                    area_px=float(comp.sum()),
                    touches_edge=b.touches_edge
                    or bx0 <= roi.left
                    or by0 <= roi.top
                    or bx1 >= roi.right - 1
                    or by1 >= roi.bottom - 1,
                    on_seam=True,
                )
            )
        return out

    # ------------------------------------------------------------------ template + speed

    def _learn(self, p: Prepared, g: _Geom, xc: float) -> None:
        roi = p.roi
        h_img, _ = p.flat.shape
        rows = roi.bottom - roi.top
        cols = 2 * self._half + 1
        if self._sum is None or self._sum.shape != (rows, cols):
            self._sum = np.zeros((rows, cols), np.float32)
            self._cnt = np.zeros((rows, cols), np.float32)
            self._top = roi.top
            self._learned = 0
            self._ready_patch = None
        assert self._cnt is not None
        m = np.array(
            [
                [1.0, self._cot, xc + (roi.top - h_img / 2.0) * self._cot - self._half],
                [0.0, 1.0, float(roi.top)],
            ],
            np.float32,
        )
        patch = self._warp(p.flat, m, cols, rows)
        wgt = (self._warp(g.roi_mask.astype(np.float32), m, cols, rows) > 0.999).astype(np.float32)
        self._sum += wgt * patch
        self._cnt += wgt
        over = np.maximum(self._cnt, self._cap) / self._cap  # >= 1
        self._sum /= over
        self._cnt /= over
        self._learned += 1
        if self._learned >= self._cfg.template_frames:
            row_cov = self._cnt[:, self._half]
            if float(np.percentile(row_cov, 5)) >= max(1.0, self._cap / 2):
                self._ready_patch = np.where(
                    self._cnt > 0.5, self._sum / np.maximum(self._cnt, 1e-6), 1.0
                ).astype(np.float32)

    def _update_speed(self, p: Prepared, det: _Det) -> None:
        if det.coverage < _SPEED_COVERAGE:
            return
        t = p.t_capture_ns / 1e9
        if self._sightings and (
            t - self._last_t_s > _PASS_GAP_S or det.xc < self._sightings[-1][1] - _PASS_JUMP_PX
        ):
            self._sightings.clear()
        self._sightings.append((t, det.xc))
        self._last_t_s = t
        if len(self._sightings) >= _SPEED_MIN_SIGHTINGS:
            ts = np.array([s[0] for s in self._sightings])
            xs = np.array([s[1] for s in self._sightings])
            if ts[-1] - ts[0] > 0:
                ts = ts - ts.mean()
                self._speed_px_s = float((ts * (xs - xs.mean())).sum() / (ts * ts).sum())

    # ------------------------------------------------------------------ output

    def _obs(self, p: Prepared, det: _Det, g: _Geom, present: bool) -> SeamObs:
        roi = p.roi
        h_img = p.flat.shape[0]
        if not present:
            return SeamObs(
                False,
                math.nan,
                self._cfg.angle_deg,
                self._width_px(),
                np.zeros((4, 2), np.float32),
                p.t_capture_ns,
            )
        width_px = self._width_px()
        hx = width_px / 2.0 / self._sin
        xt = self._x_at(det.xc, float(roi.top), h_img)
        xb = self._x_at(det.xc, float(roi.bottom - 1), h_img)
        band = np.array(
            [
                [xt - hx, roi.top],
                [xt + hx, roi.top],
                [xb + hx, roi.bottom - 1],
                [xb - hx, roi.bottom - 1],
            ],
            np.float32,
        )
        return SeamObs(True, det.xc, det.angle_deg, width_px, band, p.t_capture_ns)


def _with_seam(b: Blob) -> Blob:
    return Blob(b.contour_px, b.mask_bbox_px, b.area_px, b.touches_edge, on_seam=True)


def _fill_holes(comp: np.ndarray) -> np.ndarray:
    """Silhouette area: fill interior holes (a seam-subtracted hole inside the band, bores)."""
    found, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(comp)
    cv2.drawContours(filled, found, -1, 1, -1)
    return filled


def _subpixel_contour(comp: np.ndarray) -> np.ndarray | None:
    """Outer contour of a binary component at about quarter-pixel precision (2x upsample)."""
    up = cv2.resize(comp * 255, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
    up = cv2.GaussianBlur(up, (0, 0), 1.0)
    _, bw = cv2.threshold(up, 127, 255, cv2.THRESH_BINARY)
    found, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not found:
        return None
    c = max(found, key=cv2.contourArea).reshape(-1, 2).astype(np.float32)
    return (c + 0.5) / 2.0 - 0.5
