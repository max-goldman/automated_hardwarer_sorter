"""Edge sharpness: a focus/blur indicator from the image gradient on the silhouette boundary."""

from __future__ import annotations

import math

import cv2
import numpy as np

from ...config import FeaturesConfig
from ...contracts import Blob, Prepared
from . import register
from .holes import silhouette_crop


@register("edge_sharpness")
def _sharp(c: np.ndarray, prepared: Prepared, blob: Blob, cfg: FeaturesConfig):
    """Median over the contour of the peak |gradient| of ``flat`` (flat units per pixel) within 2 px."""
    crop, _, (x0, y0) = silhouette_crop(blob, prepared.flat, 4)
    if crop.shape[0] < 5 or crop.shape[1] < 5:
        return {"edge_sharpness": math.nan}
    gx = cv2.Sobel(crop, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gy = cv2.Sobel(crop, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    mag = cv2.dilate(np.hypot(gx, gy), np.ones((5, 5), np.uint8))
    xs = np.clip(np.round(blob.contour_px[:, 0] - x0).astype(int), 0, mag.shape[1] - 1)
    ys = np.clip(np.round(blob.contour_px[:, 1] - y0).astype(int), 0, mag.shape[0] - 1)
    return {"edge_sharpness": float(np.median(mag[ys, xs]))}
