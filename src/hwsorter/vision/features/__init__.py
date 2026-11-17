"""Feature registry: named scalar features of one observation (belt-plane contour + flat image).

A feature group is a function ``fn(contour_in, prepared, blob, cfg) -> dict[str, float]`` added with
``@register(name, provides=(...))``. ``extract`` runs every group on a valid observation and returns
a ``FeatureVector`` holding every name in ``feature_names()`` (NaN = not applicable or failed).
Units: inches, square inches, or unitless. ``thread_pitch_in`` is reserved and not built.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np

from ...config import FeaturesConfig
from ...contracts import Blob, FeatureVector, Observation, Prepared

FeatureFn = Callable[[np.ndarray, Prepared, Blob, FeaturesConfig], dict[str, float]]

RESERVED_FEATURES = ("thread_pitch_in",)  # name reserved for a later task; never emitted

_REGISTRY: dict[str, tuple[FeatureFn, tuple[str, ...]]] = {}


def register(name: str, provides: tuple[str, ...] | None = None):
    """Decorator registering a feature group. ``provides`` lists the output names (default: name)."""

    def deco(fn: FeatureFn) -> FeatureFn:
        if name in _REGISTRY:
            raise ValueError(f"feature group {name!r} already registered")
        _REGISTRY[name] = (fn, tuple(provides) if provides else (name,))
        return fn

    return deco


def feature_names() -> tuple[str, ...]:
    """Every feature name in registration order."""
    return tuple(n for _, outs in _REGISTRY.values() for n in outs)


def _nan_values() -> dict[str, float]:
    return {n: math.nan for n in feature_names()}


def extract(
    obs: Observation, prepared: Prepared, cfg: FeaturesConfig | None = None
) -> FeatureVector:
    """Features of one observation. An invalid observation gives an all-NaN, invalid vector."""
    values = _nan_values()
    contour = np.asarray(obs.contour_in, np.float64)
    if not obs.valid or len(contour) < 3:
        return FeatureVector(values, valid=False)
    cfg = cfg or FeaturesConfig()
    for fn, outs in _REGISTRY.values():
        got = fn(contour, prepared, obs.blob, cfg)
        for n in outs:
            values[n] = float(got.get(n, math.nan))
    return FeatureVector(values, valid=True)


from . import bolt, geometry, holes, sharpness  # noqa: E402, F401  (register the groups)
