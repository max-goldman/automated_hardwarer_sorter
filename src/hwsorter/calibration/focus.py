"""Focus measurement.

Two numbers:
  * edge width: the distance in pixels over which a sharp edge rises from 10% to 90%.
    It is absolute (a property of the optics, not of the scene), so the same target range
    applies to the calibration target, to parts during a run, and across sessions.
  * gradient energy: a relative score that responds smoothly while the lens is turned;
    the focus assist shows it as a percentage of the best value seen.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class EdgeSharpness:
    width_px: float | None  # median 10-90% edge width; None if no usable edges were found
    edges: int  # number of edge profiles measured
    energy: float  # brightness-normalised gradient energy (relative focus score)


def gradient_energy(image: np.ndarray) -> float:
    """Mean squared gradient, normalised by brightness so it does not track exposure."""
    img = image.astype(np.float32)
    gx = cv2.Sobel(img, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(img, cv2.CV_32F, 0, 1, ksize=3)
    mean = float(img.mean())
    return float((gx * gx + gy * gy).mean() / (mean * mean + 1e-6))


def measure_sharpness(
    image: np.ndarray,
    roi: tuple[int, int, int, int] | None = None,
    max_edges: int = 300,
    half_len_px: float = 10.0,
    min_contrast: float = 30.0,
    seed: int = 0,
) -> EdgeSharpness:
    """Measure edge sharpness in `image` (uint8 mono). roi is (top, bottom, left, right)."""
    if roi is not None:
        top, bottom, left, right = roi
        image = image[top:bottom, left:right]
    img = image.astype(np.float32)
    energy = gradient_energy(image)
    smooth = cv2.GaussianBlur(img, (0, 0), 1.5)  # direction estimate only
    gx = cv2.Sobel(smooth, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(smooth, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    border = int(half_len_px) + 2
    interior = np.zeros_like(mag, dtype=bool)
    interior[border:-border, border:-border] = True
    peak = float(mag[interior].max()) if interior.any() else 0.0
    if peak < 4.0:
        return EdgeSharpness(None, 0, energy)
    # Ridge points: strong gradient that is a local maximum in its 3x3 neighbourhood.
    ridge = (
        (mag >= 0.35 * peak) & (mag >= cv2.dilate(mag, np.ones((3, 3), np.uint8)) - 1e-6) & interior
    )
    ys, xs = np.nonzero(ridge)
    if len(xs) == 0:
        return EdgeSharpness(None, 0, energy)
    if len(xs) > max_edges:
        pick = np.random.default_rng(seed).choice(len(xs), max_edges, replace=False)
        ys, xs = ys[pick], xs[pick]
    ux, uy = gx[ys, xs] / mag[ys, xs], gy[ys, xs] / mag[ys, xs]

    step = 0.25
    offsets = np.arange(-half_len_px, half_len_px + step / 2, step, dtype=np.float32)
    map_x = (xs[:, None] + ux[:, None] * offsets[None, :]).astype(np.float32)
    map_y = (ys[:, None] + uy[:, None] * offsets[None, :]).astype(np.float32)
    profiles = cv2.remap(img, map_x, map_y, cv2.INTER_LINEAR)  # rows rise from dark to bright

    # Line-spread function: derivative of each (lightly smoothed) edge profile over 1 px.
    smooth_sigma = 0.75  # px, along the profile only; removed again below
    profiles = cv2.GaussianBlur(
        profiles, (0, 0), sigmaX=smooth_sigma / step, sigmaY=1e-6, borderType=cv2.BORDER_REPLICATE
    )
    k = round(0.5 / step)
    lsf = (profiles[:, 2 * k :] - profiles[:, : -2 * k]) / (2 * k * step)
    x = offsets[k:-k]
    centre = np.abs(x) <= 3.0
    last = len(x) - 1
    widths = []
    for row in lsf:
        peak_i = int(np.argmax(np.where(centre, row, -np.inf)))
        tails = np.abs(x - x[peak_i]) > 6.0
        if tails.sum() < 4:
            continue
        # Soft shadows put a slow brightness ramp beside real edges; remove it as a baseline.
        base = row - np.median(row[tails])
        peak = base[peak_i]
        if peak <= 0:
            continue
        above = base > 0.5 * peak
        # Half-maximum crossings, found walking out from the peak and walking in from the ends.
        # Noise biases the two searches in opposite directions, so their average is used.
        lo_out = peak_i
        while lo_out > 0 and above[lo_out]:
            lo_out -= 1
        hi_out = peak_i
        while hi_out < last and above[hi_out]:
            hi_out += 1
        if above[lo_out] or above[hi_out]:
            continue  # edge wider than the sampling window
        inside = np.nonzero(above)[0]
        lo_in, hi_in = max(inside[0] - 1, 0), min(inside[-1] + 1, last)

        def crossing(i_low: int, i_high: int, base=base, peak=peak) -> float:
            """Position where the profile crosses half maximum between a below and an above sample."""
            frac = (0.5 * peak - base[i_low]) / max(base[i_high] - base[i_low], 1e-6)
            return float(x[i_low] + (x[i_high] - x[i_low]) * frac)

        fwhm_out = crossing(hi_out, hi_out - 1) - crossing(lo_out, lo_out + 1)
        fwhm_in = crossing(hi_in, hi_in - 1) - crossing(lo_in, lo_in + 1)
        if fwhm_in > 3.0 * fwhm_out:
            continue  # a second edge inside the window
        sigma_measured = 0.5 * (fwhm_out + fwhm_in) / 2.3548
        if peak * sigma_measured * 2.5066 < min_contrast:  # edge height in gray levels
            continue
        # Remove blur added by measuring: bilinear resampling (1/6), the 1 px difference (1/12), the smoothing.
        sigma = np.sqrt(max(sigma_measured**2 - 0.25 - smooth_sigma**2, 0.01))
        widths.append(2.5631 * sigma)  # 10-90% rise of a Gaussian-blurred step
    if len(widths) < 8:
        return EdgeSharpness(None, len(widths), energy)
    # Report the sharpest quarter of edges. Shadow borders, shading gradients and features
    # standing above the belt are genuinely softer and say nothing about lens focus.
    return EdgeSharpness(float(np.percentile(widths, 25)), len(widths), energy)


def focus_verdict(width_px: float | None, good_px: float, soft_px: float) -> tuple[str, str]:
    """(short status, plain sentence) for the focus assist."""
    if width_px is None:
        return (
            "no edges",
            "Put the calibration target or a part under the camera; there is nothing sharp to measure.",
        )
    if width_px <= good_px:
        return (
            "sharp",
            f"Focus is sharp (edges {width_px:.1f} px wide; target is {good_px:.1f} px or less).",
        )
    if width_px <= soft_px:
        return (
            "slightly soft",
            f"Focus is slightly soft (edges {width_px:.1f} px wide; target is {good_px:.1f} px or less). Turn the lens a little.",
        )
    return (
        "too soft",
        f"Focus is too soft (edges {width_px:.1f} px wide; over {soft_px:.1f} px is not usable). Turn the lens.",
    )
