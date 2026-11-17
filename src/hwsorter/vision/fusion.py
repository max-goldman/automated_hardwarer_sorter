"""Per-track feature fusion: median and MAD over valid observations, ignoring NaN."""

from __future__ import annotations

import math
import warnings

import numpy as np

from ..config import FeaturesConfig
from ..contracts import FeatureVector, FusedFeatures, Track, TrackFlag


def fuse(
    track: Track, vectors: list[FeatureVector], cfg: FeaturesConfig | None = None
) -> FusedFeatures:
    """Median and MAD (median absolute deviation) of each feature over the valid vectors.

    ``n_frames`` counts valid vectors. With fewer than ``cfg.min_valid_frames`` the result is
    all NaN (too few frames to fuse). NaN values are ignored per feature; an all-NaN feature
    stays NaN."""
    cfg = cfg or FeaturesConfig()
    valid = [v for v in vectors if v.valid]
    on_seam = TrackFlag.ON_SEAM in track.flags or any(o.blob.on_seam for o in track.observations)
    names: list[str] = []
    for v in valid or vectors:
        names += [k for k in v.values if k not in names]
    median: dict[str, float] = {}
    spread: dict[str, float] = {}
    for k in names:
        col = np.array([v.values.get(k, math.nan) for v in valid], np.float64)
        if len(valid) < cfg.min_valid_frames or not np.isfinite(col).any():
            median[k] = spread[k] = math.nan
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            m = float(np.nanmedian(col))
            median[k] = m
            spread[k] = float(np.nanmedian(np.abs(col - m)))
    return FusedFeatures(median=median, spread=spread, n_frames=len(valid), on_seam=on_seam)
