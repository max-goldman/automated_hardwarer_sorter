"""Versioned configuration: hand-edited tunables from TOML, validated with pydantic.

Layering: built-in defaults < config/default.toml < config/local.toml < explicit file.
Calibration results live separately (see hwsorter.calibration.store) so tools never
rewrite hand-edited files.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo in a key is an error, not a silent default


class CameraConfig(_Section):
    backend: Literal["auto", "avfoundation", "v4l2"] = "auto"
    usb_vendor_id: int = 0x0C45
    usb_product_id: int = 0x636D
    width: int = 1280
    height: int = 800
    fps: float = 120.0
    exposure_ms: float = Field(0.5, gt=0)
    flip_horizontal: bool = False
    flip_vertical: bool = False
    queue_frames: int = Field(240, ge=8)
    lens_focal_mm: float = 8.0
    pixel_pitch_um: float = 3.0


class BeltConfig(_Section):
    speed_in_s: float = Field(4.0, gt=0)
    loop_length_in: float = Field(52.0, gt=0)
    rework_gap_in: float = Field(1.0, ge=0)


class TimingConfig(_Section):
    calibrated: bool = False
    exit_to_belt_end_in: float = 12.0
    collector_lead_ms: float = 500.0
    camera_latency_ms: float = 20.0


class RecordingConfig(_Section):
    dir: str = "recordings"
    zstd_level: int = Field(1, ge=1, le=19)
    queue_frames: int = Field(1200, ge=8)


class RoiConfig(_Section):
    mode: Literal["auto", "manual"] = "auto"
    top: int = 0
    bottom: int = 800
    margin_px: int = Field(6, ge=0)


class FocusConfig(_Section):
    edge_width_good_px: float = 2.2
    edge_width_soft_px: float = 3.2


class ExposureConfig(_Section):
    max_blur_px: float = 0.75
    belt_target_level: int = 180
    belt_min_level: int = 90
    flicker_warn_pct: float = 3.0


class CalibrationTargetConfig(_Section):
    squares_x: int = 16
    squares_y: int = 10
    square_mm: float = 5.0
    marker_mm: float = 3.5
    scale_bar_mm: float = 50.0


class GuiConfig(_Section):
    display_fps: int = Field(30, ge=1, le=120)


class SorterConfig(_Section):
    schema_version: int = SCHEMA_VERSION
    camera: CameraConfig = CameraConfig()
    belt: BeltConfig = BeltConfig()
    timing: TimingConfig = TimingConfig()
    recording: RecordingConfig = RecordingConfig()
    roi: RoiConfig = RoiConfig()
    focus: FocusConfig = FocusConfig()
    exposure: ExposureConfig = ExposureConfig()
    calibration_target: CalibrationTargetConfig = CalibrationTargetConfig()
    gui: GuiConfig = GuiConfig()


def project_root() -> Path:
    """Folder holding config/default.toml: $HWSORTER_ROOT, else found by walking up."""
    env = os.environ.get("HWSORTER_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    for start in (Path.cwd(), Path(__file__).resolve()):
        for folder in (start, *start.parents):
            if (folder / "config" / "default.toml").is_file():
                return folder
    return Path.cwd()


def machine_name() -> str:
    """Stable per-machine key for calibration files; override with $HWSORTER_MACHINE."""
    raw = os.environ.get("HWSORTER_MACHINE")
    if not raw and sys.platform == "darwin":
        # platform.node() on macOS can be a network-assigned name that changes between networks.
        try:
            raw = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=2).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            raw = ""
    raw = raw or platform.node() or "machine"
    return re.sub(r"[^A-Za-z0-9_-]+", "-", raw.split(".")[0]).strip("-").lower() or "machine"


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path | None = None, root: Path | None = None) -> SorterConfig:
    """Load and validate the config. `path` is an extra override file applied last."""
    root = root or project_root()
    data: dict[str, Any] = {}
    files = [root / "config" / "default.toml", root / "config" / "local.toml"]
    if path is not None:
        files.append(Path(path))
    for file in files:
        if file.is_file():
            with file.open("rb") as fh:
                data = _deep_merge(data, tomllib.load(fh))
        elif path is not None and file == Path(path):
            raise FileNotFoundError(f"config file not found: {file}")
    cfg = SorterConfig.model_validate(data)
    if cfg.schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"config schema_version {cfg.schema_version} is not supported (expected {SCHEMA_VERSION})"
        )
    return cfg
