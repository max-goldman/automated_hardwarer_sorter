"""Shared fixtures for the replay regression tests; registers and gates the ``slow`` marker."""

from __future__ import annotations

import os

import pytest

from hwsorter.bench import GOLDEN_SCENARIOS, golden_parts, scene_for_cfg, write_synthetic_recording
from hwsorter.config import load_config


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "slow: minutes-long accuracy runs; set HWSORTER_RUN_SLOW=1 to include them"
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get("HWSORTER_RUN_SLOW"):
        return
    skip = pytest.mark.skip(reason="slow; set HWSORTER_RUN_SLOW=1 to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def golden_recordings(tmp_path_factory):
    """The golden scenes rendered (deterministically, fixed seeds) into recordings."""
    root = tmp_path_factory.mktemp("golden-recordings")
    cfg = load_config()
    scene = scene_for_cfg(cfg)
    return {
        n: write_synthetic_recording(root / n, golden_parts(n), scene) for n in GOLDEN_SCENARIOS
    }
