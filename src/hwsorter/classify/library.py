"""Parts library: per part, per pose, feature -> (lo, hi) ranges, loaded from YAML."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hwsorter.contracts import FusedFeatures, PartClass

# The authoritative feature set emitted by vision/features (task 007). Library ranges are keyed
# by these names, never by position. edge_sharpness is a focus signal, not an identity feature.
FEATURE_NAMES = frozenset(
    {
        "area_in2",
        "minrect_long_in",
        "minrect_short_in",
        "hull_solidity",
        "circularity",
        "hex_harmonic",
        "across_flats_in",
        "across_corners_in",
        "shank_width_in",
        "head_width_in",
        "head_length_in",
        "total_length_in",
        "hole_kind",
        "hole_diameter_in",
        "edge_sharpness",
    }
)
DISCRETE_FEATURES = frozenset({"hole_kind"})  # coded values: ranges are +-0.5 around the code
SKIP_DERIVE = frozenset({"edge_sharpness"})  # focus/brightness dependent, not identity


@dataclass(frozen=True)
class PartSpec:
    part_class: PartClass
    display_name: str
    output_code: int
    poses: dict[str, dict[str, tuple[float, float]]]


@dataclass(frozen=True)
class PartsLibrary:
    parts: dict[PartClass, PartSpec]


def _parse_ranges(where: str, raw: Any) -> dict[str, tuple[float, float]]:
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{where}: needs a non-empty feature -> [lo, hi] mapping")
    out: dict[str, tuple[float, float]] = {}
    for feat, rng in raw.items():
        if not isinstance(rng, (list, tuple)) or len(rng) != 2:
            raise ValueError(f"{where}.{feat}: range must be [lo, hi]")
        if str(feat) not in FEATURE_NAMES:
            raise ValueError(f"{where}.{feat}: unknown feature name")
        if feat == "edge_sharpness":
            raise ValueError(f"{where}.{feat}: edge_sharpness is a quality signal, not a range")
        lo, hi = float(rng[0]), float(rng[1])
        if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
            raise ValueError(f"{where}.{feat}: need finite lo < hi, got [{lo}, {hi}]")
        out[str(feat)] = (lo, hi)
    return out


def library_from_dict(data: Any) -> PartsLibrary:
    """Validate a raw ``{"parts": {part_id: {...}}}`` mapping into a PartsLibrary."""
    raw_parts = data.get("parts") if isinstance(data, dict) else None
    if not isinstance(raw_parts, dict):
        raise ValueError("library needs a top-level 'parts' mapping")
    parts: dict[PartClass, PartSpec] = {}
    for key, raw in raw_parts.items():
        try:
            pc = PartClass(key)
        except ValueError:
            raise ValueError(f"unknown part id {key!r}") from None
        if not isinstance(raw, dict) or not isinstance(raw.get("poses"), dict):
            raise ValueError(f"{key}: needs 'poses'")
        poses = {str(p): _parse_ranges(f"{key}.{p}", r) for p, r in raw["poses"].items()}
        if not poses:
            raise ValueError(f"{key}: no poses")
        parts[pc] = PartSpec(
            part_class=pc,
            display_name=str(raw.get("display_name", key)),
            output_code=int(raw["output_code"]),
            poses=poses,
        )
    missing = [pc.value for pc in PartClass if pc not in parts]
    if missing:
        raise ValueError(f"library missing parts: {missing}")
    return PartsLibrary(parts=parts)


def load_library(path: str | Path) -> PartsLibrary:
    with open(path, encoding="utf-8") as fh:
        return library_from_dict(yaml.safe_load(fh))


def derive_ranges(
    examples: Sequence[tuple[PartClass, FusedFeatures]], margin: float
) -> dict[str, Any]:
    """Build a raw library dict (same shape as the YAML) from labelled measurements.

    Each part gets one pose, ``measured``. Per feature the range is the observed min/max
    padded by ``margin`` (fraction of the value, floored at ``margin * span``); features that
    are NaN in any example of the part are left out, and ``hole_kind`` uses +-0.5.
    """
    by_part: dict[PartClass, list[dict[str, float]]] = {}
    for pc, fused in examples:
        by_part.setdefault(pc, []).append(fused.median)
    parts: dict[str, Any] = {}
    for n, pc in enumerate(PartClass, start=1):
        meds = by_part.get(pc)
        if not meds:
            continue
        ranges: dict[str, list[float]] = {}
        for feat in meds[0]:
            if feat in SKIP_DERIVE:
                continue
            if feat not in FEATURE_NAMES:
                continue
            vals = [m.get(feat, math.nan) for m in meds]
            if not all(math.isfinite(v) for v in vals):
                continue
            lo, hi = min(vals), max(vals)
            if feat in DISCRETE_FEATURES:
                ranges[feat] = [round(lo) - 0.5, round(hi) + 0.5]
                continue
            pad = max(margin * max(abs(lo), abs(hi), hi - lo), 1e-3)
            ranges[feat] = [lo - pad, hi + pad]
        parts[pc.value] = {
            "display_name": pc.value,
            "output_code": n,
            "poses": {"measured": ranges},
        }
    return {"parts": parts}
