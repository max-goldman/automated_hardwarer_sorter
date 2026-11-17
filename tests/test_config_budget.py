"""Frame-budget config keys: defaults, bounds, strictness, cross-field check."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hwsorter.config import PipelineConfig, SegmentConfig, SorterConfig


def test_defaults() -> None:
    p, s = PipelineConfig(), SegmentConfig()
    assert (p.frame_stride, p.feature_stride, p.max_feature_frames, p.queue_frames) == (
        2,
        3,
        10,
        120,
    )
    assert (s.coarse_scale, s.crop_pad_px) == (2, 16)


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("pipeline", "frame_stride", 0),
        ("segment", "coarse_scale", 3),
        ("segment", "crop_pad_px", 11),
    ],
)
def test_bounds_rejected(section: str, key: str, value: int) -> None:
    with pytest.raises(ValidationError):
        SorterConfig.model_validate({section: {key: value}})


def test_unknown_pipeline_key_rejected() -> None:
    with pytest.raises(ValidationError):
        SorterConfig.model_validate({"pipeline": {"frame_stridee": 2}})


def test_max_feature_frames_cross_check() -> None:
    with pytest.raises(ValidationError, match="max_feature_frames"):
        SorterConfig.model_validate({"pipeline": {"max_feature_frames": 2}})
