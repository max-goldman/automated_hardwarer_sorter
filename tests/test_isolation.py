"""Tests never see the machine's config/local.toml or calibration files."""

import os
from pathlib import Path

from hwsorter.calibration.store import calibration_path
from hwsorter.config import load_config, project_root


def test_suite_root_has_no_local_state():
    root = project_root()
    assert root == Path(os.environ["HWSORTER_ROOT"]).resolve()
    assert not (root / "config" / "local.toml").exists()
    assert not calibration_path().parent.exists()
    assert load_config().camera.exposure_ms == 0.5


def test_local_toml_still_applies_in_a_root_that_has_one(tmp_path):
    (tmp_path / "config").mkdir()
    default = project_root() / "config" / "default.toml"
    (tmp_path / "config" / "default.toml").write_text(default.read_text(encoding="utf-8"))
    (tmp_path / "config" / "local.toml").write_text("[camera]\nexposure_ms = 7.0\n")
    assert load_config(root=tmp_path).camera.exposure_ms == 7.0
