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

import dataclasses
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum, Flag, auto
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, runtime_checkable

import numpy as np

if TYPE_CHECKING:  # type-only: calibration.store pulls in cv2, which contracts must not import
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


def flag_names(flags: TrackFlag) -> list[str]:
    """Names of the set flags in TrackFlag definition order (the decision-log ``flags`` form)."""
    return [f.name for f in TrackFlag if f.name and f in flags]


def parse_flags(names: Iterable[str]) -> TrackFlag:
    """Inverse of ``flag_names``: names from a logged ``flags`` list back to a TrackFlag."""
    out = TrackFlag(0)
    for n in names:
        out |= TrackFlag[n]
    return out


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
    """Belt seam observation. ``band_px`` is the (4, 2) px corner polygon of the seam band.
    ``angle_deg`` is measured in degrees from the travel axis (+x) toward +y (image y down),
    the same convention as synthetic.py and ``seam.angle_deg`` in config (default 50).
    ``x_center_px`` is the band centre at the image's vertical middle row (height / 2).
    ``width_px`` is measured perpendicular to the band (across it), like ``seam.width_in``
    (0.2 in); along x the band is 0.2 / sin(50 deg), about 0.26 in, wide."""

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


RULES = (
    "lone_confident",  # single part, confident class
    "lone_misc",  # single part, out of scope or unresolved
    "group_same_class",  # several parts, all the same confident class
    "group_all_oos",  # several parts, all out of scope
    "rework",  # anything else in a group (mixed, unresolved, merged): send to rework
)


@dataclass(frozen=True, slots=True)
class GroupDecision:
    """Output decision for one closed group of tracks (decide -> schedule).

    ``t_ref_ns`` and ``x_lead_in_at_ref`` come from the most downstream member (the one with
    the max ``belt_x_lead_in``): ``x_lead_in_at_ref`` is that member's lead edge in belt-plane
    inches (NOT belt-frame) at ``t_ref_ns`` (latency-corrected). ``t_closed_ns`` is when the
    group was closed, in the Clock domain. ``rule`` is one of ``RULES``."""

    group_id: int
    member_track_ids: tuple[int, ...]
    output: Output
    rule: str
    t_ref_ns: int
    x_lead_in_at_ref: float
    t_closed_ns: int


@dataclass(frozen=True, slots=True)
class ScheduledOutput:
    """A group decision with its send time. ``t_send_ns`` is the target send time in the Clock
    domain (when the part reaches the collector gate minus actuator lead)."""

    group_id: int
    output: Output
    member_track_ids: tuple[int, ...]
    t_send_ns: int


@dataclass(frozen=True, slots=True)
class SendRecord:
    """Result of a send. ``t_actual_ns`` (Clock domain) is when the adapter was actually called;
    on a late send it is after ``scheduled.t_send_ns`` and ``miss`` says why (LATE_DECISION when
    the decision arrived after the send time, so it was sent immediately).

    The Scheduler is the only writer of the decision-log "send" record: it writes exactly one
    per ScheduledOutput after ``adapter.send`` returns (miss=ADAPTER_ERROR if it raised).
    Adapters only deliver the output and never log the send record themselves."""

    scheduled: ScheduledOutput
    t_actual_ns: int
    miss: MissReason = MissReason.NONE


# --------------------------------------------------------------------------- protocols


@runtime_checkable
class Classifier(Protocol):
    def classify(self, fused: FusedFeatures, flags: TrackFlag) -> Classification: ...


@runtime_checkable
class OutputAdapter(Protocol):
    """Sends one collector command. Called from the scheduler thread only.

    Adapters do I/O only. They never write the decision log: the Scheduler writes the single
    "send" record (with ``miss``) for every ScheduledOutput, whichever adapter is used."""

    def send(self, o: ScheduledOutput, t_actual_ns: int) -> None: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------------- decision log
# Records go through EventLog.log(kind, **fields), which adds "t_ns" (monotonic wall time) and
# "kind". Serialise with ``to_log_dict``:
#   * enums -> .value, TrackFlag -> flag_names() list, dataclasses -> dicts, times -> int ns;
#   * numpy scalars -> Python numbers, arrays -> lists;
#   * NaN and +-inf are written as JSON null (never NaN/Infinity); readers treat null as NaN
#     (``Classification.runner_up_score`` inf therefore reads back as NaN: use ``is None or nan``).
# Determinism (invariant 10): replaying a recording must give identical records once these
# are removed: top-level ``t_ns`` and every key in DETERMINISM_IGNORE_KEYS (at any depth), and
# records of kind in DETERMINISM_IGNORE_KINDS ("log_open" carries wall_time). In session_start,
# ``session`` and ``source`` are names/paths and are also ignored (listed in the key set).
# ``t_actual_ns`` (send time under ManualClock depends on thread scheduling) is ignored too.
# The comparison is PER KIND: group the records by ``kind``, then compare the sequence of each
# kind in file order. Never diff whole files: "send" lines come from the scheduler thread and
# "part"/"group" from the pipeline thread, so their interleaving is not reproducible.
# ``normalise_for_compare`` does exactly this.
# Group membership comes only from GroupRecord.member_track_ids.

DETERMINISM_IGNORE_KEYS = frozenset(
    {
        "t_ns",
        "timings",
        "wall_time",
        "session",
        "source",
        "machine",  # hostname in session_start; ignored so a Mac-vs-Dell replay --compare matches
        "t_start_ns",
        "t_end_ns",
        "duration_ns",
        "t_actual_ns",
    }
)
DETERMINISM_IGNORE_KINDS = frozenset({"log_open"})


def _strip_ignored(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_ignored(v) for k, v in obj.items() if k not in DETERMINISM_IGNORE_KEYS}
    if isinstance(obj, list):
        return [_strip_ignored(v) for v in obj]
    return obj


def normalise_for_compare(records: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Determinism view of a decision log: drop ignored kinds and keys (at any depth), then group
    by ``kind`` keeping file order within each kind. Two runs are equal iff these dicts are equal."""
    out: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        kind = rec.get("kind")
        if kind in DETERMINISM_IGNORE_KINDS:
            continue
        out.setdefault(str(kind), []).append(_strip_ignored(rec))
    return out


def to_log_dict(obj: Any) -> Any:
    """Convert a dataclass/dict/list/enum/numpy value to plain JSON-safe data (see above)."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_log_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, TrackFlag):
        return flag_names(obj)
    if isinstance(obj, Enum):
        return to_log_dict(obj.value)
    if isinstance(obj, np.ndarray):
        return to_log_dict(obj.tolist())
    if isinstance(obj, np.generic):
        return to_log_dict(obj.item())
    if isinstance(obj, dict):
        return {str(k): to_log_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_log_dict(v) for v in obj]
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    return obj


# ``timings`` minimum keys. part: {"stages_ms": {stage: ms}, "latency_ms": float} where
# latency_ms is the last used frame's t_capture to the group decision, so a part record can only
# be written when its group closes (under ManualClock it measures belt time, not compute time).
# group/send: {"latency_ms"}, measured from the group's t_ref_ns (group) or t_send_ns (send) to
# the record being written. session_end: {"stages_ms": {stage: StageSummary}}.


class StageSummary(TypedDict):
    """Per-stage latency summary in session_end ``timings["stages_ms"]`` (all in ms)."""

    count: int
    mean: float
    p50: float
    p95: float
    p99: float
    max: float


class PartRecord(TypedDict):
    """kind="part": one finished track. Floats may be None (non-finite, see above)."""

    schema_version: int
    track_id: int
    verdict: str
    part_class: str | None
    pose: str | None
    score: float | None
    runner_up_score: float | None
    worst_feature: str | None
    flags: list[str]  # names in TrackFlag definition order
    n_frames: int
    on_seam: bool
    median: dict[str, float | None]
    spread: dict[str, float | None]
    belt_x_lead_in: float
    belt_x_trail_in: float
    y_min_in: float  # belt-plane y extent (rematching side-by-side parts)
    y_max_in: float
    t_first_ns: int  # first / last frame used (t_last_ns starts the latency metric)
    t_last_ns: int
    t_ref_ns: int
    x_lead_in_at_ref: float
    crop_ref: str | None  # session-relative path of the crop
    frame_ref: str | None  # session-relative path of the full frame
    ref_seq: int | None  # Frame.seq of the full frame
    timings: dict[str, Any]


class GroupRecord(TypedDict):
    """kind="group": one closed group and its decision."""

    schema_version: int
    group_id: int
    member_track_ids: list[int]
    output: str
    rule: str  # one of RULES
    t_ref_ns: int
    x_lead_in_at_ref: float
    t_closed_ns: int
    timings: dict[str, Any]


class SendLogRecord(TypedDict):
    """kind="send": one collector command actually issued. Written by the Scheduler (exactly one
    per ScheduledOutput, after ``adapter.send`` returns); adapters never write it."""

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
    exit_x_in: float  # belt-plane x of the collector gate
    px_per_in: float  # calibration actually used
    measured_speed_in_s: float | None
    machine: str
    source: str
    config: dict[str, Any]


class SessionEndRecord(TypedDict):
    """kind="session_end"."""

    schema_version: int
    t_end_ns: int
    duration_ns: int
    n_frames: int
    n_dropped: int
    n_parts: int
    n_groups: int
    n_sends: int
    n_late: int
    timings: dict[str, Any]
