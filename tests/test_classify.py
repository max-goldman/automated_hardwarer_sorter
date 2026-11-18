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
        area_in2=0.3056,
        minrect_long_in=1.17,
        minrect_short_in=0.5057,
        hull_solidity=0.9,
        circularity=0.3,
        hex_harmonic=NAN,
        across_flats_in=0.5057,
        across_corners_in=1.17,
        hole_kind=0.0,
        hole_diameter_in=NAN,
        shank_width_in=0.2431,
        head_width_in=0.5054,
        head_length_in=0.1688,
        total_length_in=1.1643,
    ),
    PartClass.BOLT_0312: dict(
        area_in2=0.4029,
        minrect_long_in=1.215,
        minrect_short_in=0.5787,
        hull_solidity=0.9,
        circularity=0.338,
        hex_harmonic=NAN,
        across_flats_in=0.5787,
        across_corners_in=1.215,
        hole_kind=0.0,
        hole_diameter_in=NAN,
        shank_width_in=0.3055,
        head_width_in=0.5783,
        head_length_in=0.2116,
        total_length_in=1.2089,
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
    PartClass.NUT_0312: dict(
        area_in2=0.2161,
        minrect_long_in=0.5756,
        minrect_short_in=0.4999,
        hull_solidity=0.998,
        circularity=0.9169,
        hex_harmonic=0.058,
        across_flats_in=0.4999,
        across_corners_in=0.5756,
        hole_kind=2.0,
        hole_diameter_in=0.25,
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
        hole_diameter_in=0.2486,
        shank_width_in=NAN,
        head_width_in=NAN,
        head_length_in=NAN,
        total_length_in=NAN,
    ),
    PartClass.WASHER_0312: dict(
        area_in2=0.4407,
        minrect_long_in=0.7493,
        minrect_short_in=0.7493,
        hull_solidity=0.9987,
        circularity=1.0002,
        hex_harmonic=0.0,
        across_flats_in=0.7493,
        across_corners_in=0.7493,
        hole_kind=1.0,
        hole_diameter_in=0.3139,
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
    nut = NOMINAL[PartClass.NUT_0312]
    # Same outer size: only outline and hole differ.
    for k in ("minrect_short_in", "minrect_long_in", "across_corners_in", "across_flats_in"):
        washer[k] = nut[k]
    washer["area_in2"] = nut["area_in2"]
    c = clf.classify(fused(washer), TrackFlag(0))
    assert c.part_class is not PartClass.NUT_0312
    assert clf.classify(fused(nut), TrackFlag(0)).part_class is PartClass.NUT_0312


def test_three_eighths_nut_never_confident(clf: RangeClassifier) -> None:
    # 3/8-16 nut (out of scope, the near miss of 5/16): AF 0.563, AC 0.649, area ~0.275.
    med = dict(NOMINAL[PartClass.NUT_0312])
    med.update(
        area_in2=0.2747,
        across_flats_in=0.563,
        across_corners_in=0.649,
        minrect_short_in=0.563,
        minrect_long_in=0.649,
        hole_diameter_in=0.306,
    )
    c = clf.classify(fused(med), TrackFlag(0))
    assert c.verdict in (Verdict.OUT_OF_SCOPE, Verdict.UNRESOLVED)
    assert c.part_class is None


def test_three_eighths_washer_and_bolt_never_confident(clf: RangeClassifier) -> None:
    washer = dict(NOMINAL[PartClass.WASHER_0312])
    washer.update(
        area_in2=0.5162,
        minrect_short_in=0.8106,
        minrect_long_in=0.8106,
        across_flats_in=0.81,
        across_corners_in=0.8117,
        hole_diameter_in=0.3743,
    )
    bolt = dict(NOMINAL[PartClass.BOLT_0312])
    bolt.update(
        minrect_long_in=1.235,
        minrect_short_in=0.6508,
        shank_width_in=0.367,
        head_width_in=0.6505,
        head_length_in=0.2335,
        total_length_in=1.229,
    )
    for med in (washer, bolt):
        c = clf.classify(fused(med), TrackFlag(0))
        assert c.verdict in (Verdict.OUT_OF_SCOPE, Verdict.UNRESOLVED)
        assert c.part_class is None


def test_quarter_and_five_sixteenth_do_not_overlap(clf: RangeClassifier) -> None:
    """Each in-scope part scores > 1 against the other size of the same type."""
    for a, b in (
        (PartClass.BOLT_025, PartClass.BOLT_0312),
        (PartClass.NUT_025, PartClass.NUT_0312),
        (PartClass.WASHER_025, PartClass.WASHER_0312),
    ):
        for x, y in ((a, b), (b, a)):
            c = clf.classify(fused(NOMINAL[x]), TrackFlag(0))
            assert c.part_class is x and c.runner_up_score > 1.5, (x, y, c.runner_up_score)


def test_unknown_big_part_out_of_scope(clf: RangeClassifier) -> None:
    med = dict(NOMINAL[PartClass.WASHER_0312])
    med.update(area_in2=1.2, minrect_short_in=1.2, minrect_long_in=1.2)
    assert clf.classify(fused(med), TrackFlag(0)).verdict is Verdict.OUT_OF_SCOPE


def test_nut_on_side_pose(clf: RangeClassifier) -> None:
    side = dict(NOMINAL[PartClass.NUT_025])
    side.update(
        minrect_long_in=0.47,
        minrect_short_in=0.311,  # nyloc thickness
        hole_kind=0.0,
        hex_harmonic=NAN,
        circularity=0.8,
        area_in2=0.10,
        across_flats_in=0.311,
    )
    c = clf.classify(fused(side), TrackFlag(0))
    assert c.verdict is Verdict.CONFIDENT
    assert c.part_class is PartClass.NUT_025
    assert c.pose == "side"


def test_nyloc_side_pose_separates_the_sizes(clf: RangeClassifier) -> None:
    for pc, thick in ((PartClass.NUT_025, 0.311), (PartClass.NUT_0312, 0.347)):
        side = dict(NOMINAL[pc])
        side.update(
            minrect_long_in=NOMINAL[pc]["across_corners_in"],
            minrect_short_in=thick,
            hole_kind=0.0,
            hex_harmonic=NAN,
            circularity=0.8,
            area_in2=0.10,
            across_flats_in=thick,
        )
        c = clf.classify(fused(side), TrackFlag(0))
        assert c.verdict is Verdict.CONFIDENT and c.part_class is pc and c.pose == "side"


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
    med = dict(NOMINAL[PartClass.WASHER_0312])
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
    del raw["parts"]["nut_0312"]
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


@pytest.mark.parametrize("od,idd", [(0.705, 0.0), (0.688, 0.344)])  # coin, washer_oos_0688
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
