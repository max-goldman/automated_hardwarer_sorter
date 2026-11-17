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

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class SegmentConfig(_Section):
    deviation_threshold: float = Field(0.12, gt=0)  # |flat - 1| above this is part (belt ~1.0)
    edge_threshold: float = Field(0.03, gt=0)  # sub-pixel edge level: |flat - 1| crossing used to
    # place the contour between pixels (NOT a hysteresis seed; the seed is deviation_threshold)
    edge_gradient_threshold: float = Field(0.08, gt=0)  # |grad flat| per px that marks a sharp edge
    shadow_max_depth: float = Field(0.10, ge=0)  # soft dark regions shallower than this are shadow
    gain_alpha: float = Field(0.05, gt=0, le=1)  # slow per-frame brightness-gain tracker rate
    subpixel_smooth_px: float = Field(0.7, ge=0)  # gaussian sigma before sub-pixel refinement
    min_area_in2: float = Field(0.01, ge=0)  # smaller blobs are dust, square inches
    close_kernel_px: int = Field(3, ge=0)  # morphological close kernel size, px
    coarse_scale: int = Field(2, ge=1, le=2)  # 1 = old full-frame path; 2 = coarse-then-crop
    crop_pad_px: int = Field(16, ge=12)  # full-res padding around the coarse blob crop, px


class SeamConfig(_Section):
    enabled: bool = True
    # Degrees measured from the travel axis (+x) toward +y (image y down), same as synthetic.py.
    angle_deg: float = 50.0
    width_in: float = Field(0.2, gt=0)  # nominal seam band width, inches (matches synthetic.py)
    angle_tol_deg: float = Field(10.0, gt=0)  # accepted deviation from angle_deg
    width_tol_in: float = Field(0.1, gt=0)  # accepted deviation from width_in
    template_frames: int = Field(30, ge=1)  # frames averaged to learn the seam template
    speed_window: int = Field(15, ge=2)  # seam sightings smoothed for the belt speed estimate


# Track frame counts (max_missed_frames, settle_frames, min_frames) count PROCESSED frames
# (after pipeline.frame_stride), not captured frames.
class TrackConfig(_Section):
    gate_in: float = Field(0.5, gt=0)  # max belt-frame distance to link a blob to a track, inches
    settle_frames: int = Field(3, ge=1)  # frames before a track counts as settled
    max_missed_frames: int = Field(3, ge=0)  # frames a track may go unseen before it finishes
    speed_tolerance: float = Field(0.15, ge=0)  # allowed fractional deviation from belt speed
    max_area_change: float = Field(0.15, gt=0)  # fractional frame-to-frame area jump = unstable
    min_frames: int = Field(5, ge=1)  # fewer valid frames sets TrackFlag.FEW_FRAMES


class FeaturesConfig(_Section):
    elongated_min_aspect: float = Field(1.8, gt=1)  # length/width above which bolt features exist
    hole_bright_threshold: float = Field(
        0.90, gt=0
    )  # flat (part/belt) above this inside a part = see-through hole (washer ~1.0)
    hole_dark_threshold: float = Field(
        0.30, gt=0
    )  # flat below this inside a part = dark bore (nut ~0.21; bodies >= 0.40)
    hole_min_diameter_in: float = Field(0.08, gt=0)  # smaller holes are noise
    min_valid_frames: int = Field(3, ge=1)  # fewer valid observations cannot be fused
    radial_samples: int = Field(180, ge=16)  # contour radial-profile samples (hex harmonic)


class ClassifyConfig(_Section):
    confidence_threshold: float = Field(1.0, gt=0)  # worst-feature score at or below = fits
    out_of_scope_threshold: float = Field(1.5, gt=0)  # best score above this = out of scope
    min_frames: int = Field(5, ge=1)  # fewer fused frames cannot be CONFIDENT
    library_path: str = "config/parts_library.yaml"  # resolved against the project root
    derive_margin: float = Field(0.1, ge=0)  # fractional range padding when deriving ranges


class SchedulerConfig(_Section):
    enabled: bool = True
    spin_ms: float = Field(4.0, ge=0)  # busy-wait window before a send time, milliseconds
    screen_log_keep: int = Field(50, ge=1)  # last N sends kept for the GUI/screen log


class PipelineConfig(_Section):
    decision_log_dir: str = "decisions"  # relative to the session folder
    crop_dir: str = "crops"  # relative to the session folder
    save_crops: bool = True
    save_full_frames: bool = True  # full-frame reference per part, for grading
    crop_pad_in: float = Field(0.15, ge=0)  # padding around a part crop, inches
    frame_stride: int = Field(2, ge=1)  # process a frame only when seq % frame_stride == 0
    feature_stride: int = Field(3, ge=1)  # feature extraction every Nth PROCESSED frame
    max_feature_frames: int = Field(10, ge=1)  # per-track cap on frames kept for features
    queue_frames: int = Field(120, ge=1)  # pipeline input queue depth, frames


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
    segment: SegmentConfig = SegmentConfig()
    seam: SeamConfig = SeamConfig()
    track: TrackConfig = TrackConfig()
    features: FeaturesConfig = FeaturesConfig()
    classify: ClassifyConfig = ClassifyConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    pipeline: PipelineConfig = PipelineConfig()

    @model_validator(mode="after")
    def _check_feature_budget(self) -> SorterConfig:
        mff = self.pipeline.max_feature_frames
        if mff < self.classify.min_frames:
            raise ValueError(
                f"pipeline.max_feature_frames ({mff}) must be >= "
                f"classify.min_frames ({self.classify.min_frames})"
            )
        if mff < self.features.min_valid_frames:
            raise ValueError(
                f"pipeline.max_feature_frames ({mff}) must be >= "
                f"features.min_valid_frames ({self.features.min_valid_frames})"
            )
        return self


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
            raw = subprocess.run(
                ["scutil", "--get", "LocalHostName"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            ).stdout.strip()
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
