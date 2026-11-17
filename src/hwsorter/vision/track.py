"""Frame-to-frame tracking of parts on the belt.

Parts are stationary in the belt frame (``belt_x = x_in - v*(t - t0)``), so each live track
predicts "same belt position" and a blob is linked to the nearest track inside
``track.gate_in``. An observation is ``valid`` (fit to be fused) only when the part is fully
in view, moves at belt speed within ``track.speed_tolerance`` and its outline area is stable.

Design choices
- Gating compares belt-frame x using the best of centre, lead edge and trail edge (a part
  entering/leaving is clipped on one side only) plus the centre y; assignment is greedy by
  ascending distance.
- MERGED: if the predicted position of two or more live tracks falls inside one blob, the
  blob becomes an invalid observation of every one of those tracks and each is flagged. The
  tracks keep their ids, so they re-link by gate when the blobs separate again.
- A track finishes when it has left the view (its predicted trailing edge passed the
  downstream image edge) or after ``max_missed_frames`` consecutive misses.
- At finish: UNSETTLED if no run of ``settle_frames`` consecutive valid observations,
  FEW_FRAMES if fewer than ``min_frames`` valid observations, TOUCHES_EDGE if the part was
  never seen fully inside the image. ON_SEAM is copied from ``Blob.on_seam``.
- Track extents come from valid observations (all observations if none are valid).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from ..calibration.store import PlaneMapper
from ..config import TrackConfig
from ..contracts import Blob, Observation, Track, TrackFlag

NS_PER_S = 1_000_000_000


@dataclass
class _Hist:
    t_ns: int
    cx: float  # belt-frame centre x
    area: float
    edge: bool
    merged: bool


@dataclass
class _State:
    track: Track
    lead: float
    trail: float
    cx: float
    cy: float
    slot: int = 0  # internal live-track key; the public track_id is assigned lazily (see Tracker)
    missed: int = 0
    hist: list[_Hist] = field(default_factory=list)


@dataclass
class _Det:
    blob: Blob
    contour_in: np.ndarray  # image-plane inches (not belt frame)
    lead: float  # belt frame
    trail: float
    cx: float
    cy: float
    t_ns: int


class Tracker:
    def __init__(self, mapper: PlaneMapper, speed_in_s: float, cfg: TrackConfig) -> None:
        self.mapper = mapper
        self.v = float(speed_in_s)
        self.cfg = cfg
        w, h = mapper.scale.image_width, mapper.scale.image_height
        edges = mapper.px_to_in(np.array([[0.0, h / 2.0], [float(w), h / 2.0]]))
        self._left_in = float(edges[0, 0])
        self._right_in = float(edges[1, 0])
        self._t0: int | None = None
        self._next_id = 1  # public track_id, assigned to a track on its first valid observation
        self._next_slot = 1  # internal live-track key, assigned on creation
        self._states: dict[int, _State] = {}  # keyed by slot

    # ------------------------------------------------------------------ public
    @property
    def t0_ns(self) -> int | None:
        """Belt-frame time origin (the first time seen), or None before the first frame."""
        return self._t0

    def entry_belt_x_in(self, t_ns: int) -> float:
        """belt_x of the upstream (left) image edge at time ``t_ns``."""
        if self._t0 is None:
            self._t0 = t_ns
        return self._left_in - self.v * (t_ns - self._t0) / NS_PER_S

    def live(self) -> list[Track]:
        return [s.track for s in self._states.values()]

    def update(self, seq: int, t_capture_ns: int, blobs: list[Blob]) -> list[Track]:
        if self._t0 is None:
            self._t0 = t_capture_ns
        t_ns = t_capture_ns
        shift = self.v * (t_ns - self._t0) / NS_PER_S
        dets = [self._detect(b, t_ns, shift) for b in blobs]

        states = sorted(self._states.values(), key=lambda s: s.slot)
        matched: set[int] = set()  # slots
        used: set[int] = set()

        # Merge detection: predicted positions of >= 2 tracks inside one blob.
        for j, d in enumerate(dets):
            inside = [s for s in states if self._predicted_inside(s, d, shift)]
            if len(inside) >= 2:
                used.add(j)
                for s in inside:
                    matched.add(s.slot)
                    s.track.flags |= TrackFlag.MERGED
                    self._observe(s, seq, d, shift, merged=True)

        # Greedy nearest assignment on remaining tracks and blobs.
        pairs = []
        for s in states:
            if s.slot in matched:
                continue
            for j, d in enumerate(dets):
                if j in used:
                    continue
                dist = self._distance(s, d)
                if dist <= self.cfg.gate_in:
                    pairs.append((dist, s.slot, j))
        pairs.sort()
        for _dist, slot, j in pairs:
            if slot in matched or j in used:
                continue
            matched.add(slot)
            used.add(j)
            self._observe(self._states[slot], seq, dets[j], shift, merged=False)

        # Unmatched blobs start new tracks (public track_id assigned lazily, on first valid frame).
        for j, d in enumerate(dets):
            if j in used:
                continue
            slot = self._next_slot
            self._next_slot += 1
            state = _State(Track(track_id=0), d.lead, d.trail, d.cx, d.cy, slot=slot)
            self._states[slot] = state
            self._observe(state, seq, d, shift, merged=False)

        # Finish tracks that left the view or were missed too long. A track that never earned a
        # public id (no valid observation ever) is a seam tail or an edge/merge fragment, not a
        # part: drop it so it consumes no id and emits nothing.
        finished: list[Track] = []
        for s in states:
            if s.slot in matched:
                s.missed = 0
                continue
            s.missed += 1
            trail_x = s.trail + shift  # image-plane x of the trailing edge now
            if trail_x > self._right_in or s.missed > self.cfg.max_missed_frames:
                t = self._finish(s)
                if t.track_id != 0:
                    finished.append(t)
        return finished

    # ------------------------------------------------------------------ internals
    def _detect(self, blob: Blob, t_ns: int, shift: float) -> _Det:
        c_in = self.mapper.px_to_in(blob.contour_px)
        x0, y0 = c_in.min(axis=0)
        x1, y1 = c_in.max(axis=0)
        return _Det(
            blob,
            c_in,
            float(x1) - shift,
            float(x0) - shift,
            (float(x0) + float(x1)) / 2 - shift,
            (float(y0) + float(y1)) / 2,
            t_ns,
        )

    def _distance(self, s: _State, d: _Det) -> float:
        dx = min(abs(d.cx - s.cx), abs(d.lead - s.lead), abs(d.trail - s.trail))
        return float(np.hypot(dx, d.cy - s.cy))

    def _predicted_inside(self, s: _State, d: _Det, shift: float) -> bool:
        pt = (float(s.cx + shift), float(s.cy))
        return cv2.pointPolygonTest(d.contour_in.astype(np.float32), pt, False) >= 0

    def _observe(self, s: _State, seq: int, d: _Det, shift: float, merged: bool) -> None:
        cfg = self.cfg
        edge = d.blob.touches_edge
        valid = not edge and not merged
        if valid:
            ref = [h for h in s.hist[-cfg.settle_frames :] if not h.edge and not h.merged]
            if ref:
                dt_s = (d.t_ns - ref[0].t_ns) / NS_PER_S
                if dt_s > 0 and abs(d.cx - ref[0].cx) > cfg.speed_tolerance * self.v * dt_s:
                    valid = False
            prev = s.hist[-1] if s.hist else None
            if (
                valid
                and prev is not None
                and not prev.edge
                and not prev.merged
                and prev.area > 0
                and abs(d.blob.area_px - prev.area) / prev.area > cfg.max_area_change
            ):
                valid = False
        t = s.track
        if valid and t.track_id == 0:  # first valid frame: this track is a real part now
            t.track_id = self._next_id
            self._next_id += 1
        t.observations.append(Observation(seq, d.t_ns, d.blob, d.contour_in, valid))
        if d.blob.on_seam:
            t.flags |= TrackFlag.ON_SEAM
        s.hist.append(_Hist(d.t_ns, d.cx, d.blob.area_px, edge, merged))
        if not merged:  # merged blobs say nothing about this part's own position
            s.lead, s.trail, s.cx, s.cy = d.lead, d.trail, d.cx, d.cy
        self._refresh_extents(s)

    def _refresh_extents(self, s: _State) -> None:
        t = s.track
        obs = [o for o in t.observations if o.valid] or t.observations
        xs0, xs1, ys0, ys1 = [], [], [], []
        for o in obs:
            sh = self.v * (o.t_capture_ns - self._t0) / NS_PER_S  # type: ignore[operator]
            xs0.append(float(o.contour_in[:, 0].min()) - sh)
            xs1.append(float(o.contour_in[:, 0].max()) - sh)
            ys0.append(float(o.contour_in[:, 1].min()))
            ys1.append(float(o.contour_in[:, 1].max()))
        t.belt_x_trail_in = float(np.median(xs0))
        t.belt_x_lead_in = float(np.median(xs1))
        t.y_min_in = float(np.median(ys0))
        t.y_max_in = float(np.median(ys1))
        t.t_first_ns = t.observations[0].t_capture_ns
        t.t_last_ns = t.observations[-1].t_capture_ns

    def _finish(self, s: _State) -> Track:
        t = s.track
        cfg = self.cfg
        n_valid = sum(o.valid for o in t.observations)
        run = best = 0
        for o in t.observations:
            run = run + 1 if o.valid else 0
            best = max(best, run)
        if best < cfg.settle_frames:
            t.flags |= TrackFlag.UNSETTLED
        if n_valid < cfg.min_frames:
            t.flags |= TrackFlag.FEW_FRAMES
        if all(o.blob.touches_edge for o in t.observations):
            t.flags |= TrackFlag.TOUCHES_EDGE
        t.finished = True
        del self._states[s.slot]
        return t
