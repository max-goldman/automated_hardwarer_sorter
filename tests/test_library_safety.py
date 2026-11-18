"""Library safety: a size reading that is off by a few percent must never turn one in-scope class
into another (contamination). It may go UNRESOLVED / OUT_OF_SCOPE. This guards every future
edit of config/parts_library.yaml."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml
from test_classify import NAN, NOMINAL, fused

from hwsorter.classify import RangeClassifier, load_library
from hwsorter.config import ClassifyConfig
from hwsorter.contracts import PartClass, TrackFlag, Verdict

LIB_PATH = Path(__file__).resolve().parents[1] / "config" / "parts_library.yaml"
SAFE_BAND = 0.07  # no wrong CONFIDENT within +-7% scale error
STEPS = [round(0.90 + 0.005 * i, 3) for i in range(41)]  # 0.90 .. 1.10


def _side(pc: PartClass, thick: float) -> dict[str, float]:
    med = dict(NOMINAL[pc])
    med.update(
        minrect_long_in=NOMINAL[pc]["across_corners_in"],
        minrect_short_in=thick,
        hole_kind=0.0,
        hex_harmonic=NAN,
        circularity=0.8,
        area_in2=0.10,
        across_flats_in=thick,
    )
    return med


CASES: list[tuple[PartClass, str, dict[str, float]]] = [
    (pc, "main", NOMINAL[pc]) for pc in PartClass
] + [
    (PartClass.NUT_025, "side", _side(PartClass.NUT_025, 0.311)),
    (PartClass.NUT_0312, "side", _side(PartClass.NUT_0312, 0.347)),
]


def scaled(med: dict[str, float], s: float) -> dict[str, float]:
    out = {}
    for k, v in med.items():
        if k == "area_in2":
            out[k] = v * s * s
        elif k.endswith("_in") and not math.isnan(v):
            out[k] = v * s
        else:
            out[k] = v
    return out


@pytest.fixture(scope="module")
def clf() -> RangeClassifier:
    return RangeClassifier(load_library(LIB_PATH), ClassifyConfig())


def flip_points(clf: RangeClassifier, pc: PartClass, med: dict[str, float]):
    """(first scale below 1 and first above 1 at which pc is CONFIDENT as another class)."""
    below = above = None
    for s in STEPS:
        c = clf.classify(fused(scaled(med, s)), TrackFlag(0))
        if c.verdict is Verdict.CONFIDENT and c.part_class is not pc:
            if s < 1.0:
                below = s  # keeps the one nearest 1.0
            elif above is None:
                above = s
    return below, above


@pytest.mark.parametrize("pc,pose,med", CASES, ids=[f"{c[0].value}-{c[1]}" for c in CASES])
def test_scale_error_never_gives_a_wrong_confident_class(clf, pc, pose, med):
    below, above = flip_points(clf, pc, med)
    for s in STEPS:
        if abs(s - 1.0) > SAFE_BAND + 1e-9:
            continue
        c = clf.classify(fused(scaled(med, s)), TrackFlag(0))
        assert not (c.verdict is Verdict.CONFIDENT and c.part_class is not pc), (
            f"{pc.value}/{pose} read x{s} is CONFIDENT {c.part_class}"
        )
    print(f"flip {pc.value}/{pose}: below={below} above={above}")


# Features that must separate the two sizes (a gap between the ranges), per type and pose.
# Bolt lengths and head length overlap by design and are not listed.
SEPARATING = {
    ("bolt", "side"): ("shank_width_in", "head_width_in", "minrect_short_in"),
    ("nut", "flat"): ("area_in2", "across_flats_in"),
    ("nut", "side"): ("minrect_short_in",),
    ("washer", "flat"): ("area_in2", "minrect_short_in"),
}


@pytest.mark.parametrize("kind", ("bolt", "nut", "washer"))
def test_adjacent_sizes_leave_a_gap_in_the_separating_features(kind):
    raw = yaml.safe_load(LIB_PATH.read_text())["parts"]
    small, big = raw[f"{kind}_025"]["poses"], raw[f"{kind}_0312"]["poses"]
    checked = 0
    for pose, feats in ((p, f) for (k, p), f in SEPARATING.items() if k == kind):
        for feat in feats:
            _lo_s, hi_s = small[pose][feat]
            lo_b, _hi_b = big[pose][feat]
            assert lo_b > hi_s, f"{kind}/{pose}/{feat}: 5/16 {lo_b} must exceed 1/4 {hi_s}"
            checked += 1
    assert checked >= 1
