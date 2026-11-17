"""Pipeline runner: wires every vision, decision and scheduling stage into one frame loop.

Per processed frame:  Preprocessor -> segment -> SeamHandler -> Tracker -> features (per valid
observation, on the feature stride) ... and when a track finishes: fuse -> classify -> PartResult
-> Decider (Grouper) -> for each closed group send_time_ns -> Scheduler.submit.

Conventions
* Camera latency (``timing.camera_latency_ms``) is subtracted from ``Frame.t_capture_ns`` exactly
  once, here, before any stage sees the frame (invariant 2). Everything downstream, including
  every ``t_*_ns`` in the decision log, uses the corrected time.
* Strides (processing budget): a frame is processed only when ``seq % frame_stride == 0``; skipped
  frames never reach segmentation. Features are extracted on valid observations where
  ``(seq // frame_stride) % feature_stride == 0``, at most ``max_feature_frames`` per track, as
  the frames arrive.
* With a ManualClock (replay, tests, ``--fast``) the pipeline advances the clock to each frame's
  raw capture time and drives the scheduler itself (no threads, deterministic). With a real clock
  the scheduler runs its own thread.
* The decision log (``EventLog``) gets ``session_start``, one ``part`` record per finished track
  and one ``group`` record per closed group (written when the group closes, parts first), and
  ``session_end``; the Scheduler writes the single ``send`` record. Stage timings live under a
  separate ``timings`` key, which determinism comparisons ignore. No wall clock or RNG is used on
  the decision path (``perf_counter`` feeds only the ``timings`` data).
"""

from __future__ import annotations

import dataclasses
import itertools
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .calibration import Calibration, PlaneMapper, RoiCalibration, ScaleCalibration
from .calibration.store import calibration_path
from .classify import RangeClassifier, load_library
from .classify.library import PartsLibrary
from .clock import NS_PER_MS, NS_PER_S, Clock
from .config import SorterConfig, machine_name, project_root
from .contracts import (
    DECISION_LOG_SCHEMA_VERSION,
    Classification,
    FeatureVector,
    GroupDecision,
    Observation,
    Output,
    PartResult,
    ScheduledOutput,
    SeamObs,
    StageSummary,
    Track,
    TrackFlag,
    Verdict,
    flag_names,
    to_log_dict,
)
from .decide.rules import Decider
from .frames import EndOfStream, Frame, FrameSource
from .schedule import Scheduler, send_time_ns
from .vision.features import extract
from .vision.fusion import fuse
from .vision.seam import SeamHandler
from .vision.segment import Preprocessor, segment
from .vision.track import Tracker

log = logging.getLogger(__name__)

STAGES = ("preprocess", "segment", "seam", "track", "features", "fuse", "classify", "decide")


# --------------------------------------------------------------------------- snapshot (GUI view)


@dataclass(frozen=True, slots=True, eq=False)
class TrackView:
    """A live track as the GUI draws it. ``outline_px`` is the latest full-image contour."""

    track_id: int
    outline_px: np.ndarray | None
    belt_x_lead_in: float
    belt_x_trail_in: float
    flags: TrackFlag
    n_features: int
    predicted_output: Output | None  # provisional, from the features seen so far
    verdict: Verdict | None
    score: float | None  # provisional worst-feature fit (0 perfect, 1 range edge)
    rework: bool  # within rework_gap_in of a neighbouring track


@dataclass(frozen=True, slots=True)
class GapView:
    upstream_id: int
    downstream_id: int
    gap_in: float
    rework: bool


@dataclass(frozen=True, slots=True)
class PendingSend:
    group_id: int
    output: Output
    t_send_ns: int
    countdown_s: float  # until the send, in the Clock domain


@dataclass(frozen=True, slots=True, eq=False)
class PipelineSnapshot:
    """Everything the GUI needs after one frame. Frozen; safe to hand to another thread."""

    seq: int
    t_capture_ns: int  # latency-corrected
    processed: bool  # False for a frame skipped by frame_stride
    tracks: tuple[TrackView, ...] = ()
    gaps: tuple[GapView, ...] = ()
    pending: tuple[PendingSend, ...] = ()
    seam: SeamObs | None = None
    fps: float = 0.0  # input frame rate measured on capture timestamps
    n_frames: int = 0
    n_skipped: int = 0
    n_dropped: int = 0  # queue overflows (a FAIL for any run that expects none)
    latency_ms: float = 0.0  # compute time of this frame
    n_parts: int = 0
    n_groups: int = 0


# --------------------------------------------------------------------------- helpers


def provisional_scale(width: int, height: int, px_per_in: float) -> ScaleCalibration:
    """A distortion-free, fronto-parallel scale for synthetic scenes (origin at the image centre)."""
    k = [[1500.0, 0.0, (width - 1) / 2], [0.0, 1500.0, (height - 1) / 2], [0.0, 0.0, 1.0]]
    hom = [
        [1 / px_per_in, 0.0, -(width - 1) / 2 / px_per_in],
        [0.0, 1 / px_per_in, -(height - 1) / 2 / px_per_in],
        [0.0, 0.0, 1.0],
    ]
    return ScaleCalibration(
        image_width=width,
        image_height=height,
        camera_matrix=k,
        dist_coeffs=[0.0] * 5,
        homography=hom,
        px_per_in=px_per_in,
        px_per_in_min=px_per_in,
        px_per_in_max=px_per_in,
        reprojection_rms_px=0.0,
        plane_rms_in=0.0,
        views=0,
        corners=0,
        mirrored=False,
        target_scale_correction=1.0,
        measured_utc="provisional",
    )


def synthetic_calibration(scene: Any) -> tuple[Calibration, np.ndarray]:
    """Calibration (scale + ROI) and flat-field for a ``synthetic.SceneConfig``."""
    from .calibration.flatfield import build_flatfield
    from .calibration.roi import find_belt_edges
    from .synthetic import SyntheticRenderer

    empty = SyntheticRenderer(scene, [])
    flat = build_flatfield([empty.render(i * 0.09, i) for i in range(40)])
    top, bottom, _ = find_belt_edges(empty.render(0.0, 0))
    cal = Calibration(
        machine="synthetic",
        scale=provisional_scale(scene.width, scene.height, scene.px_per_in),
        roi=RoiCalibration(top=top, bottom=bottom, left=0, right=scene.width, source="auto"),
    )
    return cal, flat


def summarize(values: list[float]) -> StageSummary:
    a = np.asarray(values, np.float64)
    if a.size == 0:
        return {"count": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    return {
        "count": int(a.size),
        "mean": float(a.mean()),
        "p50": float(np.percentile(a, 50)),
        "p95": float(np.percentile(a, 95)),
        "p99": float(np.percentile(a, 99)),
        "max": float(a.max()),
    }


@dataclass
class _TrackState:
    vectors: list[FeatureVector] = field(default_factory=list)
    n_valid_seen: int = 0
    feature_ms: float = 0.0
    provisional: Classification | None = None
    ref_obs: Observation | None = None  # the most central observation, for the saved crop
    ref_image: np.ndarray | None = None
    ref_dist: float = float("inf")


# --------------------------------------------------------------------------- pipeline


class Pipeline:
    """One frame loop from raw frames to scheduled collector commands. Single-threaded."""

    def __init__(
        self,
        cfg: SorterConfig,
        cal: Calibration,
        clock: Clock,
        adapter: Any,
        log_: Any,
        *,
        flat: np.ndarray | None = None,
        session_dir: str | Path | None = None,
        library: PartsLibrary | None = None,
        image_size: tuple[int, int] | None = None,
        source_name: str = "",
        provisional_px_per_in: float | None = None,
    ) -> None:
        self.cfg = cfg
        self.clock = clock
        self.log = log_
        self._adapter = adapter
        self.session_dir = Path(session_dir) if session_dir is not None else None
        self.source_name = source_name
        self._manual = hasattr(clock, "advance_to")  # ManualClock: deterministic, no threads
        width, height = image_size or (cfg.camera.width, cfg.camera.height)

        scale = cal.scale
        if scale is None:
            if provisional_px_per_in is None:
                from .synthetic import SceneConfig

                provisional_px_per_in = SceneConfig().px_per_in
            log.warning("no scale calibration: using provisional %.0f px/in", provisional_px_per_in)
            scale = provisional_scale(width, height, provisional_px_per_in)
        self.mapper = PlaneMapper(scale)
        self.px_per_in = float(scale.px_per_in)
        roi = cal.roi or RoiCalibration(top=0, bottom=height, left=0, right=width, source="auto")
        if flat is None and cal.flatfield_file:
            path = calibration_path(cal.machine).parent / cal.flatfield_file
            if path.is_file():
                flat = np.load(path)
        if flat is None:
            log.warning("no flat-field: assuming a uniformly lit belt")

        edges = self.mapper.px_to_in(np.array([[0.0, height / 2.0], [float(width), height / 2.0]]))
        self.exit_x_in = float(edges[1, 0])  # downstream image edge, belt-plane inches
        pad = self.mapper.in_to_px(np.array([[0.0, 0.0], [cfg.pipeline.crop_pad_in, 0.0]]))
        self._crop_pad_px = round(float(np.linalg.norm(pad[1] - pad[0])))
        self._image_w, self._image_h = width, height

        self.speed_in_s = float(cfg.belt.speed_in_s)
        self._latency_ns = round(cfg.timing.camera_latency_ms * NS_PER_MS)
        self._pre = Preprocessor(flat, roi, cfg.segment)
        self._seam = SeamHandler(cfg.seam)
        self._seam.set_scale(self.px_per_in)  # before the first process()
        self._tracker = Tracker(self.mapper, self.speed_in_s, cfg.track)
        if library is None:
            lib_path = Path(cfg.classify.library_path)
            library = load_library(
                lib_path if lib_path.is_absolute() else project_root() / lib_path
            )
        self._classifier = RangeClassifier(library, cfg.classify)
        self._decider = Decider(cfg.belt.rework_gap_in)
        self._scheduler = Scheduler(
            clock,
            adapter,
            log_,
            spin_ms=cfg.scheduler.spin_ms,
            uncalibrated=not cfg.timing.calibrated,
            replay=self._manual,
            keep_records=cfg.scheduler.screen_log_keep,
        )

        self._states: dict[int, _TrackState] = {}
        self._parts: dict[int, tuple[PartResult, dict[str, Any]]] = {}  # awaiting group close
        self._timings: dict[str, list[float]] = {s: [] for s in STAGES}
        self._recent_ts: deque[int] = deque(maxlen=int(max(cfg.camera.fps, 1.0)) + 1)
        self._n_frames = 0
        self._n_processed = 0
        self._n_skipped = 0
        self._n_dropped = 0
        self._n_parts = 0
        self._n_groups = 0
        self._started = False
        self._session_started = False
        self._closed = False
        self._flushed = False
        self._t_start_ns = 0
        self._last_t_ns = 0
        self._last_raw_ns = 0
        self._last_seq = -1
        self._latency_ms = 0.0

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Start the scheduler thread (live clock only). Idempotent; called by process()."""
        if self._started:
            return
        self._started = True
        if not self._manual and self.cfg.scheduler.enabled:
            self._scheduler.start()

    def note_dropped(self, n: int = 1) -> None:
        """Count frames lost to a queue overflow. Any non-zero value makes the run a FAIL."""
        self._n_dropped += n

    @property
    def n_dropped(self) -> int:
        return self._n_dropped

    @property
    def scheduler(self) -> Scheduler:
        return self._scheduler

    def stage_timings(self) -> dict[str, StageSummary]:
        """Per-stage ms summaries (mean/p50/p95/p99/max) over every processed frame."""
        return {s: summarize(v) for s, v in self._timings.items() if v}

    # ------------------------------------------------------------------ per frame

    def process(self, frame: Frame) -> PipelineSnapshot:
        t_begin = time.perf_counter_ns()
        self.start()
        raw_t = int(frame.t_capture_ns)
        t_cap = raw_t - self._latency_ns  # invariant 2: the one and only latency correction
        self._n_frames += 1
        self._recent_ts.append(raw_t)
        self._last_raw_ns = raw_t
        if self._manual:
            self.clock.advance_to(raw_t)  # type: ignore[attr-defined]
            self._scheduler.advance_to(self.clock.now_ns())
        if frame.seq % self.cfg.pipeline.frame_stride != 0:
            self._n_skipped += 1  # dequeued and dropped deterministically; never segmented
            return self._snapshot(frame.seq, t_cap, False, None, t_begin)

        pc = time.perf_counter_ns
        cframe = dataclasses.replace(frame, t_capture_ns=t_cap)
        t = pc()
        prepared = self._pre.apply(cframe)
        t = self._tick("preprocess", t)
        blobs = segment(prepared, self.cfg.segment, self.mapper)
        t = self._tick("segment", t)
        blobs, seam_obs = self._seam.process(prepared, blobs)
        t = self._tick("seam", t)
        finished = self._tracker.update(frame.seq, t_cap, blobs)
        if not self._session_started:
            self._write_session_start()
        t = self._tick("track", t)
        self._n_processed += 1
        self._last_t_ns = t_cap
        self._last_seq = frame.seq

        self._extract_features(frame, prepared, finished)
        t = self._tick("features", t)
        for track in finished:
            self._finish_track(track)
        t = pc()
        self._close_groups(self.clock.now_ns())
        self._tick("decide", t)
        return self._snapshot(frame.seq, t_cap, True, seam_obs, t_begin)

    def _tick(self, stage: str, t0: int) -> int:
        now = time.perf_counter_ns()
        self._timings[stage].append((now - t0) / NS_PER_MS)
        return now

    # ------------------------------------------------------------------ features

    def _extract_features(self, frame: Frame, prepared: Any, finished: list[Track]) -> None:
        cfg = self.cfg.pipeline
        pick = (frame.seq // cfg.frame_stride) % cfg.feature_stride == 0
        for track in [*self._tracker.live(), *finished]:
            if not track.observations:
                continue
            obs = track.observations[-1]
            if obs.seq != frame.seq:
                continue  # unseen this frame
            st = self._states.setdefault(track.track_id, _TrackState())
            self._update_ref(st, obs, frame)
            if not obs.valid or not pick or len(st.vectors) >= cfg.max_feature_frames:
                continue
            t0 = time.perf_counter_ns()
            vec = extract(obs, prepared, self.cfg.features)
            st.vectors.append(vec)
            if len(st.vectors) >= self.cfg.features.min_valid_frames:
                fused = fuse(track, st.vectors, self.cfg.features)
                st.provisional = self._classifier.classify(fused, track.flags)
            st.feature_ms += (time.perf_counter_ns() - t0) / NS_PER_MS

    def _update_ref(self, st: _TrackState, obs: Observation, frame: Frame) -> None:
        """Remember the observation nearest the image centre (fully in view) for the crop."""
        x0, _, x1, _ = obs.blob.mask_bbox_px
        dist = abs((x0 + x1) / 2.0 - self._image_w / 2.0)
        if not obs.valid:
            dist += self._image_w  # any valid observation beats an invalid one
        if dist < st.ref_dist:
            st.ref_dist, st.ref_obs, st.ref_image = dist, obs, frame.image

    # ------------------------------------------------------------------ finished tracks

    def _finish_track(self, track: Track) -> None:
        st = self._states.pop(track.track_id, _TrackState())
        t = time.perf_counter_ns()
        fused = fuse(track, st.vectors, self.cfg.features)
        t = self._tick("fuse", t)
        cls = self._classifier.classify(fused, track.flags)
        self._tick("classify", t)

        valid = [o for o in track.observations if o.valid]
        ref = (valid or track.observations)[-1]
        t0 = self._tracker.t0_ns or 0
        # Lead edge in belt-plane inches at t_ref, consistent with the track's median belt_x.
        x_at_ref = track.belt_x_lead_in + self.speed_in_s * (ref.t_capture_ns - t0) / NS_PER_S
        crop_ref, frame_ref, ref_seq = self._save_crops(track, st)
        part = PartResult(
            track_id=track.track_id,
            fused=fused,
            classification=cls,
            flags=track.flags,
            belt_x_lead_in=track.belt_x_lead_in,
            belt_x_trail_in=track.belt_x_trail_in,
            t_ref_ns=ref.t_capture_ns,
            x_lead_in_at_ref=x_at_ref,
            crop_ref=crop_ref,
        )
        record: dict[str, Any] = {
            "schema_version": DECISION_LOG_SCHEMA_VERSION,
            "track_id": part.track_id,
            "verdict": cls.verdict,
            "part_class": cls.part_class,
            "pose": cls.pose,
            "score": cls.score,
            "runner_up_score": cls.runner_up_score,
            "worst_feature": cls.worst_feature,
            "flags": flag_names(track.flags),
            "n_frames": fused.n_frames,
            "on_seam": fused.on_seam,
            "median": fused.median,
            "spread": fused.spread,
            "belt_x_lead_in": part.belt_x_lead_in,
            "belt_x_trail_in": part.belt_x_trail_in,
            "y_min_in": track.y_min_in,
            "y_max_in": track.y_max_in,
            "t_first_ns": track.t_first_ns,
            "t_last_ns": track.t_last_ns,
            "t_ref_ns": part.t_ref_ns,
            "x_lead_in_at_ref": part.x_lead_in_at_ref,
            "crop_ref": crop_ref,
            "frame_ref": frame_ref,
            "ref_seq": ref_seq,
        }
        self._parts[track.track_id] = (part, record)
        self._decider.add(part)
        self._n_parts += 1
        record["_feature_ms"] = st.feature_ms

    def _save_crops(
        self, track: Track, st: _TrackState
    ) -> tuple[str | None, str | None, int | None]:
        obs, image = st.ref_obs, st.ref_image
        if obs is None or image is None:
            return None, None, None
        ref_seq = obs.seq
        if self.session_dir is None:
            return None, None, ref_seq
        cfg = self.cfg.pipeline
        crop_ref = frame_ref = None
        folder = self.session_dir / cfg.crop_dir
        params = [cv2.IMWRITE_PNG_COMPRESSION, 1]
        if cfg.save_crops:
            x0, y0, x1, y1 = obs.blob.mask_bbox_px
            pad = self._crop_pad_px
            h, w = image.shape[:2]
            crop = image[max(y0 - pad, 0) : min(y1 + pad, h), max(x0 - pad, 0) : min(x1 + pad, w)]
            folder.mkdir(parents=True, exist_ok=True)
            crop_ref = f"{cfg.crop_dir}/part_{track.track_id:04d}.png"
            cv2.imwrite(str(self.session_dir / crop_ref), crop, params)
        if cfg.save_full_frames:
            folder.mkdir(parents=True, exist_ok=True)
            frame_ref = f"{cfg.crop_dir}/part_{track.track_id:04d}_frame.png"
            cv2.imwrite(str(self.session_dir / frame_ref), image, params)
        return crop_ref, frame_ref, ref_seq

    # ------------------------------------------------------------------ groups and sends

    def _frontier(self, t_ns: int) -> float:
        """Belt-frame x that must lie a full gap behind a group before it may close.

        The entry edge alone is not enough (invariant 6): a live track whose lead edge is still
        within the gap of the group could join it, so the frontier is the most downstream of the
        entry edge and every live track's lead edge."""
        frontier = self._tracker.entry_belt_x_in(t_ns)
        for track in self._tracker.live():
            frontier = max(frontier, track.belt_x_lead_in)
        return frontier

    def _close_groups(self, now_ns: int, flush: bool = False) -> None:
        if flush:
            decisions = self._decider.flush(now_ns)
        else:
            decisions = self._decider.advance(self._frontier(self._last_t_ns), now_ns)
        for d in decisions:
            self._emit_group(d, now_ns)

    def _emit_group(self, d: GroupDecision, now_ns: int) -> None:
        for tid in d.member_track_ids:
            _part, record = self._parts.pop(tid)
            feature_ms = record.pop("_feature_ms", 0.0)
            record["timings"] = {
                "stages_ms": {"features": feature_ms},
                "latency_ms": (now_ns - record["t_last_ns"]) / NS_PER_MS,
            }
            self.log.log("part", **to_log_dict(record))
        self._n_groups += 1
        self.log.log(
            "group",
            **to_log_dict(
                {
                    "schema_version": DECISION_LOG_SCHEMA_VERSION,
                    "group_id": d.group_id,
                    "member_track_ids": list(d.member_track_ids),
                    "output": d.output,
                    "rule": d.rule,
                    "t_ref_ns": d.t_ref_ns,
                    "x_lead_in_at_ref": d.x_lead_in_at_ref,
                    "t_closed_ns": d.t_closed_ns,
                    "timings": {"latency_ms": (now_ns - d.t_ref_ns) / NS_PER_MS},
                }
            ),
        )
        if not self.cfg.scheduler.enabled:
            return
        t_send = send_time_ns(d, self.exit_x_in, self.cfg.timing, self.speed_in_s)
        self._scheduler.submit(ScheduledOutput(d.group_id, d.output, d.member_track_ids, t_send))

    # ------------------------------------------------------------------ session records

    def _write_session_start(self) -> None:
        self._session_started = True
        self._t_start_ns = self._tracker.t0_ns or 0
        self.log.log(
            "session_start",
            **to_log_dict(
                {
                    "schema_version": DECISION_LOG_SCHEMA_VERSION,
                    "session": self.session_dir.name if self.session_dir else "",
                    "t_start_ns": self._t_start_ns,
                    "belt_speed_in_s": self.speed_in_s,
                    "rework_gap_in": self.cfg.belt.rework_gap_in,
                    "exit_x_in": self.exit_x_in,
                    "px_per_in": self.px_per_in,
                    "measured_speed_in_s": None,  # the seam estimate goes in session_end timings
                    "machine": machine_name(),
                    "source": self.source_name,
                    "config": self.cfg.model_dump(),
                }
            ),
        )

    def flush(self) -> None:
        """End of input: finish every live track and close every group. In replay (ManualClock)
        also fires every queued send, so nothing is left pending. Idempotent."""
        if self._flushed:
            return
        self._flushed = True
        if not self._session_started:
            self._write_session_start()
        tracker = self._tracker
        for _ in range(self.cfg.track.max_missed_frames + 1):
            if not tracker.live():
                break
            for track in tracker.update(self._last_seq, self._last_t_ns, []):
                self._finish_track(track)
        self._close_groups(self.clock.now_ns(), flush=True)
        if self._manual:
            self._scheduler.drain()

    def wait_pending(self, timeout_s: float = 10.0) -> None:
        """Live clock: wait until every queued send has fired (or the timeout passes)."""
        end = time.monotonic() + timeout_s
        while self._scheduler.pending() and time.monotonic() < end:
            time.sleep(0.01)

    def close(self, wait_s: float = 0.0) -> None:
        """Flush, write ``session_end``, stop the scheduler and close the adapter. Idempotent."""
        if self._closed:
            return
        self._closed = True
        self.flush()
        if not self._manual and wait_s > 0:
            self.wait_pending(wait_s)
        self._scheduler.stop()
        t_end = self.clock.now_ns()
        speed = self._seam.belt_speed_in_s()
        self.log.log(
            "session_end",
            **to_log_dict(
                {
                    "schema_version": DECISION_LOG_SCHEMA_VERSION,
                    "t_end_ns": t_end,
                    "duration_ns": t_end - self._t_start_ns,
                    "n_frames": self._n_frames,
                    "n_dropped": self._n_dropped,
                    "n_parts": self._n_parts,
                    "n_groups": self._n_groups,
                    "n_sends": self._scheduler.n_sends,
                    "n_late": self._scheduler.n_late,
                    "timings": {
                        "stages_ms": self.stage_timings(),
                        "n_skipped": self._n_skipped,
                        "measured_speed_in_s": speed,
                    },
                }
            ),
        )
        try:
            self._adapter.close()
        except Exception:
            log.exception("adapter close failed")

    # ------------------------------------------------------------------ snapshot

    def _snapshot(
        self, seq: int, t_cap: int, processed: bool, seam: SeamObs | None, t_begin: int
    ) -> PipelineSnapshot:
        self._latency_ms = (time.perf_counter_ns() - t_begin) / NS_PER_MS
        ts = self._recent_ts
        fps = (len(ts) - 1) * 1e9 / (ts[-1] - ts[0]) if len(ts) > 1 and ts[-1] > ts[0] else 0.0
        if not processed:
            return PipelineSnapshot(
                seq=seq,
                t_capture_ns=t_cap,
                processed=False,
                fps=fps,
                n_frames=self._n_frames,
                n_skipped=self._n_skipped,
                n_dropped=self._n_dropped,
                latency_ms=self._latency_ms,
                n_parts=self._n_parts,
                n_groups=self._n_groups,
                pending=self._pending_view(),
            )
        gap_in = self.cfg.belt.rework_gap_in
        live = sorted(self._tracker.live(), key=lambda tr: (tr.belt_x_trail_in, tr.track_id))
        gaps: list[GapView] = []
        rework: set[int] = set()
        for up, down in itertools.pairwise(live):
            g = max(down.belt_x_trail_in - up.belt_x_lead_in, 0.0)
            near = g < gap_in
            if near:
                rework.update((up.track_id, down.track_id))
            gaps.append(GapView(up.track_id, down.track_id, g, near))
        views = []
        for tr in live:
            st = self._states.get(tr.track_id)
            prov = st.provisional if st else None
            predicted = None
            if prov is not None:
                predicted = Output.for_class(prov.part_class) if prov.part_class else Output.MISC
            views.append(
                TrackView(
                    track_id=tr.track_id,
                    outline_px=tr.observations[-1].blob.contour_px if tr.observations else None,
                    belt_x_lead_in=tr.belt_x_lead_in,
                    belt_x_trail_in=tr.belt_x_trail_in,
                    flags=tr.flags,
                    n_features=len(st.vectors) if st else 0,
                    predicted_output=predicted,
                    verdict=prov.verdict if prov else None,
                    score=prov.score if prov else None,
                    rework=tr.track_id in rework,
                )
            )
        return PipelineSnapshot(
            seq=seq,
            t_capture_ns=t_cap,
            processed=True,
            tracks=tuple(views),
            gaps=tuple(gaps),
            pending=self._pending_view(),
            seam=seam,
            fps=fps,
            n_frames=self._n_frames,
            n_skipped=self._n_skipped,
            n_dropped=self._n_dropped,
            latency_ms=self._latency_ms,
            n_parts=self._n_parts,
            n_groups=self._n_groups,
        )

    def _pending_view(self) -> tuple[PendingSend, ...]:
        now = self.clock.now_ns()
        return tuple(
            PendingSend(o.group_id, o.output, o.t_send_ns, (o.t_send_ns - now) / NS_PER_S)
            for o in self._scheduler.pending()
        )


# --------------------------------------------------------------------------- drivers


def drive(source: FrameSource, pipeline: Pipeline, limit: int | None = None) -> int:
    """Read a finite source to its end (blocking, never dropping), then flush. Returns frames fed.

    Used for replay and fast runs: the pipeline is the only consumer, so there is no queue to
    overflow."""
    n = 0
    try:
        while limit is None or n < limit:
            frame = source.read()
            if frame is None:
                continue
            pipeline.process(frame)
            n += 1
    except EndOfStream:
        pass
    pipeline.flush()
    return n


class PipelineRunner:
    """Capture consumer: a thread that feeds a Pipeline from a capture-service queue.

    The queue is bounded; when the pipeline falls behind, the capture service drops the oldest
    frame, logs ``queue_overflow``, and this runner counts it (``Pipeline.note_dropped``): a
    FAIL for any run that needs every frame."""

    def __init__(self, pipeline: Pipeline, queue: Any) -> None:
        self.pipeline = pipeline
        self.queue = queue
        self._thread = threading.Thread(target=self._run, name="pipeline", daemon=True)
        self._stop = threading.Event()
        self.snapshot: PipelineSnapshot | None = None  # latest, for a GUI to poll
        self._seen_dropped = 0

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self.queue.get(timeout=0.1)
            except EndOfStream:
                break
            if frame is None:
                continue
            if self.queue.dropped > self._seen_dropped:
                self.pipeline.note_dropped(self.queue.dropped - self._seen_dropped)
                self._seen_dropped = self.queue.dropped
            self.snapshot = self.pipeline.process(frame)

    def stop(self, wait_s: float = 0.0) -> None:
        """End of input (or shutdown): join the thread, flush and close the pipeline."""
        self._stop.set()
        self._thread.join(timeout=5.0)
        self.pipeline.close(wait_s=wait_s)

    def wait_done(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)
