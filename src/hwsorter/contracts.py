"""Frozen M2/M3 contracts shared by every pipeline stage (task 002).

Pure stdlib + numpy: no Qt, no cv2. Changes only via the orchestrator (see board.md).

Conventions (invariants)
* Image frame: full-image pixels, x right, y down, never ROI-relative. Parts travel +x.
* Time: int nanoseconds in the injected Clock's monotonic domain. ``Frame.t_capture_ns`` is the
  raw driver timestamp (NOT latency-corrected). Only the pipeline subtracts camera latency, once;
  every ``t_capture_ns`` in the types below is the corrected value.
* Belt plane: inches via PlaneMapper, +x along travel, +y down, origin under the image centre.
* Belt frame: ``belt_x = x_in - v * (t_capture_ns - t0_ns) / 1e9``; constant per part. Larger
  belt_x is further downstream. Lead = max x, trail = min x.
* Feature units: inches, square inches, degrees or unitless; NaN means not applicable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, Flag, auto
from typing import Any, Protocol, TypedDict, runtime_checkable

import numpy as np

from .calibration.store import RoiCalibration

DECISION_LOG_SCHEMA_VERSION = 1

# --------------------------------------------------------------------------- enums


class PartClass(str, Enum):  # noqa: UP042 - str mixin keeps JSON/asdict output a plain string
    """In-scope part classes; values equal the keys of synthetic.PART_DIMS."""

    BOLT_025 = "bolt_025"
    BOLT_0375 = "bolt_0375"
    NUT_025 = "nut_025"
    NUT_0375 = "nut_0375"
    WASHER_025 = "washer_025"
    WASHER_0375 = "washer_0375"


class Output(str, Enum):  # noqa: UP042
    """Collector bins: one per PartClass (same values) plus MISC and REWORK."""

    BOLT_025 = "bolt_025"
    BOLT_0375 = "bolt_0375"
    NUT_025 = "nut_025"
    NUT_0375 = "nut_0375"
    WASHER_025 = "washer_025"
    WASHER_0375 = "washer_0375"
    MISC = "misc"
    REWORK = "rework"

    @classmethod
    def for_class(cls, part_class: PartClass) -> Output:
        return cls(part_class.value)


class Verdict(str, Enum):  # noqa: UP042
    CONFIDENT = "confident"
    OUT_OF_SCOPE = "out_of_scope"
    UNRESOLVED = "unresolved"


class TrackFlag(Flag):
    ON_SEAM = auto()
    MERGED = auto()
    UNSETTLED = auto()
    TOUCHES_EDGE = auto()
    FEW_FRAMES = auto()


class MissReason(str, Enum):  # noqa: UP042
    NONE = "none"
    LATE_DECISION = "late_decision"
    ADAPTER_ERROR = "adapter_error"


# --------------------------------------------------------------------------- vision data


@dataclass(frozen=True, slots=True, eq=False)
class Prepared:
    """A flat-fielded frame. ``flat`` is float32 (H, W) with the belt near 1.0; ``gain`` is the
    per-frame brightness gain applied. ``t_capture_ns`` is latency-corrected."""

    seq: int
    t_capture_ns: int
    flat: np.ndarray
    roi: RoiCalibration
    gain: float


@dataclass(frozen=True, slots=True, eq=False)
class Blob:
    """One segmented silhouette. ``contour_px`` is float32 (N, 2) full-image px (x, y), the
    outer silhouette at sub-pixel precision. ``mask_bbox_px`` is (x0, y0, x1, y1) px."""

    contour_px: np.ndarray
    mask_bbox_px: tuple[int, int, int, int]
    area_px: float
    touches_edge: bool
    on_seam: bool = False


@dataclass(frozen=True, slots=True, eq=False)
class SeamObs:
    """Belt seam observation. ``band_px`` is the (4, 2) px corner polygon of the seam band."""

    present: bool
    x_center_px: float
    angle_deg: float
    width_px: float
    band_px: np.ndarray
    t_capture_ns: int


@dataclass(frozen=True, slots=True, eq=False)
class Observation:
    """A blob seen in one frame. ``contour_in`` is (N, 2) belt-plane inches from
    ``PlaneMapper.px_to_in``; ``valid`` is False when the observation must not be fused."""

    seq: int
    t_capture_ns: int
    blob: Blob
    contour_in: np.ndarray
    valid: bool = True


@dataclass(eq=False)
class Track:
    """One physical part followed across frames (mutable while live). Extents are belt-frame
    inches (belt_x) except y_min_in/y_max_in, which are belt-plane y inches."""

    track_id: int  # monotonic per session
    observations: list[Observation] = field(default_factory=list)
    flags: TrackFlag = TrackFlag(0)  # noqa: RUF009 - Flag value is immutable
    belt_x_lead_in: float = 0.0  # max belt_x
    belt_x_trail_in: float = 0.0  # min belt_x
    y_min_in: float = 0.0
    y_max_in: float = 0.0
    t_first_ns: int = 0
    t_last_ns: int = 0
    finished: bool = False


@dataclass(frozen=True, slots=True, eq=False)
class FeatureVector:
    """Named scalar features of one observation (in, in^2, deg, unitless; NaN = n/a)."""

    values: dict[str, float]
    valid: bool = True


@dataclass(frozen=True, slots=True, eq=False)
class FusedFeatures:
    """Per-feature median over a track and its MAD spread, same units as the features."""

    median: dict[str, float]
    spread: dict[str, float]
    n_frames: int
    on_seam: bool = False


# --------------------------------------------------------------------------- decisions


@dataclass(frozen=True, slots=True, eq=False)
class Classification:
    """``part_class`` is set only when verdict is CONFIDENT. ``score`` is the worst-feature fit
    (0 perfect, 1 at the range edge, >1 outside); ``runner_up_score`` is the next best class."""

    verdict: Verdict
    part_class: PartClass | None = None
    pose: str | None = None
    score: float = 0.0
    runner_up_score: float = float("inf")
    worst_feature: str | None = None


@dataclass(frozen=True, slots=True, eq=False)
class PartResult:
    """Final per-track result handed to grouping. ``t_ref_ns`` is the (latency-corrected)
    reference time at which ``x_lead_in_at_ref`` (belt-plane inches) was measured."""

    track_id: int
    fused: FusedFeatures
    classification: Classification
    flags: TrackFlag
    belt_x_lead_in: float
    belt_x_trail_in: float
    t_ref_ns: int
    x_lead_in_at_ref: float
    crop_ref: str | None = None


@dataclass(frozen=True, slots=True)
class GroupDecision:
    group_id: int
    member_track_ids: tuple[int, ...]
    output: Output
    rule: str
    t_ref_ns: int
    x_lead_in_at_ref: float
    t_closed_ns: int


@dataclass(frozen=True, slots=True)
class ScheduledOutput:
    group_id: int
    output: Output
    member_track_ids: tuple[int, ...]
    t_send_ns: int


@dataclass(frozen=True, slots=True)
class SendRecord:
    scheduled: ScheduledOutput
    t_actual_ns: int
    miss: MissReason = MissReason.NONE


# --------------------------------------------------------------------------- protocols


@runtime_checkable
class Classifier(Protocol):
    def classify(self, fused: FusedFeatures, flags: TrackFlag) -> Classification: ...


@runtime_checkable
class OutputAdapter(Protocol):
    """Sends one collector command. Called from the scheduler thread only."""

    def send(self, o: ScheduledOutput, t_actual_ns: int) -> None: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------------- decision log
# Records go through EventLog.log(kind, **fields), which adds "t_ns" (wall-monotonic, excluded
# from determinism checks) and "kind". Enums serialise as their string values, TrackFlag as a
# list of flag names, times as int ns. Determinism: everything except "timings" must be
# byte-identical for the same recording.


class PartRecord(TypedDict):
    """kind="part": one finished track."""

    schema_version: int
    track_id: int
    verdict: str
    part_class: str | None
    pose: str | None
    score: float
    runner_up_score: float | None
    worst_feature: str | None
    flags: list[str]
    n_frames: int
    on_seam: bool
    median: dict[str, float | None]
    spread: dict[str, float | None]
    belt_x_lead_in: float
    belt_x_trail_in: float
    t_ref_ns: int
    x_lead_in_at_ref: float
    crop_ref: str | None
    timings: dict[str, Any]


class GroupRecord(TypedDict):
    """kind="group": one closed group and its decision."""

    schema_version: int
    group_id: int
    member_track_ids: list[int]
    output: str
    rule: str
    t_ref_ns: int
    x_lead_in_at_ref: float
    t_closed_ns: int
    timings: dict[str, Any]


class SendLogRecord(TypedDict):
    """kind="send": one collector command actually issued."""

    schema_version: int
    group_id: int
    output: str
    member_track_ids: list[int]
    t_send_ns: int
    t_actual_ns: int
    miss: str
    timings: dict[str, Any]


class SessionStartRecord(TypedDict):
    """kind="session_start"."""

    schema_version: int
    session: str
    t_start_ns: int  # t0 of the belt frame
    belt_speed_in_s: float
    rework_gap_in: float
    source: str
    config: dict[str, Any]


class SessionEndRecord(TypedDict):
    """kind="session_end"."""

    schema_version: int
    t_end_ns: int
    n_frames: int
    n_parts: int
    n_groups: int
    n_sends: int
    n_late: int
    timings: dict[str, Any]
