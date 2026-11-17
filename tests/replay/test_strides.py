"""Stride accuracy guard at full size (60 parts, ~5 min): HWSORTER_RUN_SLOW=1 to include."""

from __future__ import annotations

import pytest

from hwsorter.bench import (
    MAX_ACCURACY_LOSS_PP,
    MAX_FEATURE_ERROR_GROWTH_IN,
    compare_strides,
    format_strides,
)
from hwsorter.config import load_config


@pytest.mark.slow
def test_defaults_stride2_10frames_keep_accuracy_on_60_parts():
    comp = compare_strides(load_config(), n_parts=60)
    print(format_strides(comp))
    assert comp.baseline.n_parts >= 60
    assert not comp.baseline.error and not comp.default.error
    assert comp.accuracy_loss_pp <= MAX_ACCURACY_LOSS_PP
    assert comp.worst_error_growth_in <= MAX_FEATURE_ERROR_GROWTH_IN
    assert comp.passed
    assert comp.default.n_contaminated == 0
