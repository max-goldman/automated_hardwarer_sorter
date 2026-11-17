"""Flat-field: the belt's illumination pattern with everything that moves removed.

Built from frames of the empty, moving belt. Each pixel's median over time ignores the
seam and specks (they pass any one pixel only briefly), leaving lighting falloff and
vignetting. Dividing a frame by the flat-field makes clean belt read about 1.0 everywhere.
"""
from __future__ import annotations

from typing import Iterable

import cv2
import numpy as np


def build_flatfield(frames: Iterable[np.ndarray], max_frames: int = 80, smooth_sigma_px: float = 6.0) -> np.ndarray:
    """Return a float32 image of the belt's gray level per pixel (same size as the frames).

    Frames dimmer than 90% of the typical brightness are skipped, so lighting flicker
    does not bias the result.
    """
    frames = list(frames)
    if not frames:
        raise ValueError("no frames to build a flat-field from")
    if len(frames) > max_frames:
        idx = np.linspace(0, len(frames) - 1, max_frames).round().astype(int)
        frames = [frames[i] for i in idx]
    levels = np.array([float(f[::8, ::8].mean()) for f in frames])
    keep = levels >= 0.9 * np.median(levels)
    stack = np.stack([f for f, k in zip(frames, keep) if k]).astype(np.uint8)
    flat = np.median(stack, axis=0).astype(np.float32)
    if smooth_sigma_px > 0:
        flat = cv2.GaussianBlur(flat, (0, 0), smooth_sigma_px)
    return np.maximum(flat, 1.0)


def apply_flatfield(image: np.ndarray, flat: np.ndarray) -> np.ndarray:
    """Normalised float32 frame: clean belt is about 1.0, darker things are below it."""
    return image.astype(np.float32) / flat
