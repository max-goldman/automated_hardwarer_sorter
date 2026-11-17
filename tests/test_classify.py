from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from hwsorter.classify import RangeClassifier, derive_ranges, load_library
from hwsorter.classify.library import library_from_dict
from hwsorter.config import ClassifyConfig
from hwsorter.contracts import FusedFeatures, PartClass, TrackFlag, Verdict

NAN = math.nan
LIB_PATH = Path(__file__).resolve().parents[1] / "config" / "parts_library.yaml"

# Measured values from task 007 (synthetic, blur+noise).
NOMINAL: dict[PartClass, dict[str, float]] = {
    PartClass.BOLT_025: dict(
        area_in2=0.45,
        minrect_long_in=1.156,
        minrect_short_in=0.506,
        hull_solidity=0.9,
        circularity=0.4,
        hex_harmonic=NAN,
        across_flats_in=0.44,
        across_corners_in=1.16,
        hole_kind=0.0,
        hole_diameter_in=NAN,
        shank_width_in=0.243,
        head_width_in=0.5055,
        head_length_in=0.155,
        total_length_in=1.15,
    ),
    PartClass.BOLT_0375: dict(
        area_in2=0.6,
        minrect_long_in=1.235,
        minrect_short_in=0.6505,
        hull_solidity=0.9,
        circularity=0.4,
        hex_harmonic=NAN,
        across_flats_in=0.56,
        across_corners_in=1.24,
        hole_kind=0.0,
        hole_diameter_in=NAN,
        shank_width_in=0.367,
        head_width_in=0.6503,
        head_length_in=0.2335,
        total_length_in=1.229,
    ),
    PartClass.NUT_025: dict(
        area_in2=0.1662,
        minrect_long_in=0.505,
        minrect_short_in=0.438,
        hull_solidity=0.997,
        circularity=0.917,
        hex_harmonic=0.058,
        across_flats_in=0.4382,
        across_corners_in=0.505,
        hole_kind=2.0,
        hole_diameter_in=0.1946,
        shank_width_in=NAN,
        head_width_in=NAN,
        head_length_in=NAN,
        total_length_in=NAN,
    ),
    PartClass.NUT_0375: dict(
        area_in2=0.2747,
        minrect_long_in=0.649,
        minrect_short_in=0.563,
        hull_solidity=0.998,
        circularity=0.917,
        hex_harmonic=0.058,
        across_flats_in=0.5631,
        across_corners_in=0.649,
        hole_kind=2.0,
        hole_diameter_in=0.3059,
        shank_width_in=NAN,
        head_width_in=NAN,
        head_length_in=NAN,
        total_length_in=NAN,
    ),
    PartClass.WASHER_025: dict(
        area_in2=0.306,
        minrect_long_in=0.625,
        minrect_short_in=0.624,
        hull_solidity=0.9985,
        circularity=1.0,
        hex_harmonic=0.0,
        across_flats_in=0.624,
        across_corners_in=0.6251,
        hole_kind=1.0,
        hole_diameter_in=0.2454,
        shank_width_in=NAN,
        head_width_in=NAN,
        head_length_in=NAN,
        total_length_in=NAN,
    ),
    PartClass.WASHER_0375: dict(
        area_in2=0.5162,
        minrect_long_in=0.811,
        minrect_short_in=0.8106,
        hull_solidity=0.9987,
        circularity=1.0,
        hex_harmonic=0.0,
        across_flats_in=0.81,
        across_corners_in=0.8117,
        hole_kind=1.0,
        hole_diameter_in=0.3743,
        shank_width_in=NAN,
        head_width_in=NAN,
        head_length_in=NAN,
        total_length_in=NAN,
    ),
}


def fused(med: dict[str, float], n: int = 30) -> FusedFeatures:
    return FusedFeatures(median=dict(med), spread={k: 0.0 for k in med}, n_frames=n)


@pytest.fixture(scope="module")
def clf() -> RangeClassifier:
    return RangeClassifier(load_library(LIB_PATH), ClassifyConfig())


def test_library_loads_all_parts() -> None:
    lib = load_library(LIB_PATH)
    assert set(lib.parts) == set(PartClass)
    assert "side" in lib.parts[PartClass.NUT_025].poses


@pytest.mark.parametrize("pc", list(PartClass))
def test_nominal_is_confident_and_correct(clf: RangeClassifier, pc: PartClass) -> None:
    c = clf.classify(fused(NOMINAL[pc]), TrackFlag(0))
    assert c.verdict is Verdict.CONFIDENT
    assert c.part_class is pc
    assert c.score <= 1.0 < c.runner_up_score


@pytest.mark.parametrize("scale", [0.97, 1.03])
@pytest.mark.parametrize("pc", list(PartClass))
def test_small_size_error_still_correct(clf: RangeClassifier, pc: PartClass, scale: float) -> None:
    med = {
        k: (v * scale if k in ("area_in2",) or k.endswith("_in") else v)
        for k, v in NOMINAL[pc].items()
    }
    c = clf.classify(fused(med), TrackFlag(0))
    assert c.part_class in (pc, None)  # never a wrong confident class


def test_washer_vs_nut_separable(clf: RangeClassifier) -> None:
    washer = dict(NOMINAL[PartClass.WASHER_025])
    nut = NOMINAL[PartClass.NUT_0375]
    # Same outer size: only outline and hole differ.
    for k in ("minrect_short_in", "minrect_long_in", "across_corners_in", "across_flats_in"):
        washer[k] = nut[k]
    washer["area_in2"] = nut["area_in2"]
    c = clf.classify(fused(washer), TrackFlag(0))
    assert c.part_class is not PartClass.NUT_0375
    assert clf.classify(fused(nut), TrackFlag(0)).part_class is PartClass.NUT_0375


def test_five_sixteenth_nut_never_confident(clf: RangeClassifier) -> None:
    # 5/16-18 nut: AF 0.500, AC 0.577, area ~0.217.
    med = dict(NOMINAL[PartClass.NUT_025])
    med.update(
        area_in2=0.217,
        across_flats_in=0.50,
        across_corners_in=0.577,
        minrect_short_in=0.50,
        minrect_long_in=0.577,
    )
    c = clf.classify(fused(med), TrackFlag(0))
    assert c.verdict in (Verdict.OUT_OF_SCOPE, Verdict.UNRESOLVED)
    assert c.part_class is None


def test_unknown_big_part_out_of_scope(clf: RangeClassifier) -> None:
    med = dict(NOMINAL[PartClass.WASHER_0375])
    med.update(area_in2=1.2, minrect_short_in=1.2, minrect_long_in=1.2)
    assert clf.classify(fused(med), TrackFlag(0)).verdict is Verdict.OUT_OF_SCOPE


def test_nut_on_side_pose(clf: RangeClassifier) -> None:
    side = dict(NOMINAL[PartClass.NUT_025])
    side.update(
        minrect_long_in=0.47,
        minrect_short_in=0.219,
        hole_kind=0.0,
        hex_harmonic=NAN,
        circularity=0.8,
        area_in2=0.10,
        across_flats_in=0.219,
    )
    c = clf.classify(fused(side), TrackFlag(0))
    assert c.verdict is Verdict.CONFIDENT
    assert c.part_class is PartClass.NUT_025
    assert c.pose == "side"


def test_nan_features_are_skipped(clf: RangeClassifier) -> None:
    med = dict(NOMINAL[PartClass.WASHER_025])
    assert math.isnan(med["shank_width_in"])
    c = clf.classify(fused(med), TrackFlag(0))
    assert c.verdict is Verdict.CONFIDENT
    assert c.pose == "flat"


def test_all_nan_is_unresolved(clf: RangeClassifier) -> None:
    c = clf.classify(fused({k: NAN for k in NOMINAL[PartClass.NUT_025]}), TrackFlag(0))
    assert c.verdict is Verdict.UNRESOLVED
    assert c.part_class is None


@pytest.mark.parametrize("flag", [TrackFlag.MERGED, TrackFlag.UNSETTLED])
def test_bad_flags_not_confident(clf: RangeClassifier, flag: TrackFlag) -> None:
    c = clf.classify(fused(NOMINAL[PartClass.NUT_025]), flag)
    assert c.verdict is Verdict.UNRESOLVED
    assert c.part_class is None


def test_merged_unresolved_even_if_off_library(clf: RangeClassifier) -> None:
    med = dict(NOMINAL[PartClass.WASHER_0375])
    med.update(area_in2=2.0, minrect_short_in=1.5, minrect_long_in=1.5)
    assert clf.classify(fused(med), TrackFlag.MERGED).verdict is Verdict.UNRESOLVED


def test_too_few_frames_not_confident(clf: RangeClassifier) -> None:
    c = clf.classify(fused(NOMINAL[PartClass.BOLT_025], n=2), TrackFlag.FEW_FRAMES)
    assert c.verdict is Verdict.UNRESOLVED
    assert c.part_class is None


def test_thresholds_come_from_config() -> None:
    lib = load_library(LIB_PATH)
    strict = RangeClassifier(lib, ClassifyConfig(min_frames=50))
    c = strict.classify(fused(NOMINAL[PartClass.NUT_025], n=30), TrackFlag(0))
    assert c.verdict is Verdict.UNRESOLVED


def test_derive_ranges_roundtrip() -> None:
    examples = []
    for pc, med in NOMINAL.items():
        for d in (-0.003, 0.0, 0.003):
            m = {k: (v + d if k in ("area_in2", "minrect_short_in") else v) for k, v in med.items()}
            examples.append((pc, fused(m)))
    raw = derive_ranges(examples, margin=0.1)
    lib = library_from_dict(yaml.safe_load(yaml.safe_dump(raw)))
    clf = RangeClassifier(lib, ClassifyConfig())
    for pc, med in NOMINAL.items():
        c = clf.classify(fused(med), TrackFlag(0))
        assert c.part_class is pc, pc


def test_library_validation_errors() -> None:
    with pytest.raises(ValueError):
        library_from_dict({"parts": {"bogus": {}}})
    raw = yaml.safe_load(LIB_PATH.read_text())
    raw["parts"]["nut_025"]["poses"]["flat"]["area_in2"] = [0.3, 0.1]
    with pytest.raises(ValueError):
        library_from_dict(raw)
    del raw["parts"]["nut_0375"]
    with pytest.raises(ValueError):
        library_from_dict(raw)


def _washer(od: float, idd: float) -> dict[str, float]:
    med = dict(NOMINAL[PartClass.WASHER_025])
    area = math.pi / 4 * (od**2 - idd**2)
    med.update(
        area_in2=area,
        minrect_short_in=od,
        minrect_long_in=od,
        across_flats_in=od,
        across_corners_in=od,
        hole_diameter_in=idd,
    )
    return med


@pytest.mark.parametrize("od,idd", [(0.705, 0.0), (0.688, 0.344)])  # coin, washer_0312
def test_synthetic_out_of_scope_items_never_confident(
    clf: RangeClassifier, od: float, idd: float
) -> None:
    c = clf.classify(fused(_washer(od, idd)), TrackFlag(0))
    assert c.verdict is not Verdict.CONFIDENT
    assert c.part_class is None


@pytest.mark.parametrize("code", [0.5, 1.5, NAN])
def test_hole_kind_split_or_nan_not_wrongly_confident(clf: RangeClassifier, code: float) -> None:
    med = dict(NOMINAL[PartClass.NUT_025])
    med["hole_kind"] = code
    c = clf.classify(fused(med), TrackFlag(0))
    assert c.part_class in (None, PartClass.NUT_025)
    if math.isnan(code):
        assert c.verdict is not Verdict.CONFIDENT
        assert math.isfinite(c.score)


def test_nan_required_feature_does_not_fit(clf: RangeClassifier) -> None:
    med = dict(NOMINAL[PartClass.BOLT_025])
    med["shank_width_in"] = NAN
    assert clf.classify(fused(med), TrackFlag(0)).verdict is not Verdict.CONFIDENT


def test_unknown_feature_name_rejected() -> None:
    raw = yaml.safe_load(LIB_PATH.read_text())
    raw["parts"]["nut_025"]["poses"]["flat"]["bogus_in"] = [0.0, 1.0]
    with pytest.raises(ValueError):
        library_from_dict(raw)
    raw = yaml.safe_load(LIB_PATH.read_text())
    raw["parts"]["nut_025"]["poses"]["flat"]["edge_sharpness"] = [0.0, 1.0]
    with pytest.raises(ValueError):
        library_from_dict(raw)
