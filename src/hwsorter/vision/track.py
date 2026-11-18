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

Stationary mode (``belt.mode = "stationary"``, a ``StationaryConfig`` is passed): the belt is
stopped, so v = 0 and the belt frame is the image frame. An observation is valid when the part
is fully in view, not merged, not ``linked`` to the edge complex (a hand or fingers reaching in
from the image or belt edge, see the ``linked`` argument of ``update``), its centre moved less
than ``stationary.move_tol_in`` since the previous observation and its area is stable (no
belt-speed test). A track never finishes by leaving the view, and only a bounded window of
observations is kept so a part that sits for hours uses no more memory than one for seconds.

Identity of a measured (``lock``-ed) part, which keeps its one decision:
- Followed: while it is seen in every processed frame (no gap of ``_REAPPEAR_S``, not unseen
  under a hold), as its own blob (not merged with another track) with the measured area, each
  sighting is the same part, so the anchor (where it is, its silhouette) follows it. A part
  nudged or pushed along keeps its track and decision; a jump of more than
  ``stationary.relocate_in`` between two sightings is a move (re-measured).
- Interrupted: after a gap, a frame unseen under a hold, or a sighting that is not its own blob
  (merged, at the edge, or with a different area), identity must be re-established. The next
  clean sighting must sit within ``stationary.relocate_in`` of the anchor, overlap the anchor
  silhouette by ``_REAPPEAR_IOU`` and match its area within ``track.max_area_change``; then the
  pipeline also compares the hole kind (``take_recheck``). Anything else finishes the track (the
  blob starts a new one and is re-measured). Area-mismatched sightings that are not edge-linked
  (another part put down touching it) finish it after ``track.settle_frames`` frames; a hand or
  finger merged with it only interrupts it.
A track is finished when its part was removed (unseen for ``stationary.missed_s`` while no hold
is in force, see the ``hold`` argument of ``update``) or moved as above. An UNMEASURED track that
comes back after a gap, a hold, or a sighting that was not clean (edge, linked or merged) is
finished too: its frames may show another part, so the blob is measured afresh.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from ..calibration.store import PlaneMapper
from ..config import StationaryConfig, TrackConfig
from ..contracts import Blob, Observation, Track, TrackFlag

NS_PER_S = 1_000_000_000
# A track never seen cleanly is dropped only if it rode the belt (its belt-frame centre drifted
# less than this fraction of the belt travel over its life -- a seam tail or an edge/merge
# fragment of a real part). An image-fixed blob that did not travel with the belt drifts by the
# full belt travel and is kept, so a genuine degenerate track is still reported.
_BELT_RIDE_DRIFT_FRAC = 0.7
_REAPPEAR_S = 0.15  # a measured part unseen this long and back: is it the same part?
_REAPPEAR_IOU = 0.9  # ... yes if its silhouette overlaps the measured one at least this much
_STATIONARY_WINDOW = 60  # observations kept per track in stationary mode (a part may sit for hours)


def _edge_like(d: _Det) -> bool:
    """A sighting at the image edge or linked to the edge complex (a hand or fingers)."""
    return bool(d.blob.touches_edge or d.linked)


def _iou(a: np.ndarray, b: np.ndarray, px_per_in: float = 100.0) -> float:
    """Overlap of two filled silhouettes (contours in inches), rasterized on a shared grid."""
    lo = np.minimum(a.min(axis=0), b.min(axis=0))
    hi = np.maximum(a.max(axis=0), b.max(axis=0))
    size = np.ceil((hi - lo) * px_per_in).astype(int) + 3
    masks = []
    for c in (a, b):
        m = np.zeros((int(size[1]), int(size[0])), np.uint8)
        pts = np.round((c - lo) * px_per_in + 1).astype(np.int32)
        cv2.fillPoly(m, [pts], 1)
        masks.append(m.astype(bool))
    union = np.count_nonzero(masks[0] | masks[1])
    return float(np.count_nonzero(masks[0] & masks[1]) / union) if union else 1.0


@dataclass
class _Hist:
    t_ns: int
    cx: float  # belt-frame centre x
    area: float
    edge: bool
    merged: bool
    cy: float = 0.0  # centre y (stationary validity only)


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
    t_first_ns: int = 0
    anchor: tuple[float, float] | None = None  # stationary: where the pipeline measured the part
    displaced: bool = False  # stationary: a measured part was moved; finish it this frame
    held_unseen: bool = False  # stationary: unseen during a frame with a hand in view
    anchor_area: float = 0.0  # stationary: the measured part's silhouette area, px
    off: int = 0  # stationary: consecutive sightings whose area no longer matches anchor_area
    anchor_contour: np.ndarray | None = None  # stationary: the measured silhouette, inches
    chain: bool = (
        False  # stationary: a measured part followed sighting by sighting (see module doc)
    )
    unclean: bool = False  # stationary: the last sighting was at the edge, linked or merged
    recheck: bool = False  # stationary: identity re-established; the pipeline checks the hole kind


@dataclass
class _Det:
    blob: Blob
    contour_in: np.ndarray  # image-plane inches (not belt frame)
    lead: float  # belt frame
    trail: float
    cx: float
    cy: float
    t_ns: int
    linked: bool = False  # stationary: part of the edge complex (see Tracker.update)


class Tracker:
    def __init__(
        self,
        mapper: PlaneMapper,
        speed_in_s: float,
        cfg: TrackConfig,
        stationary: StationaryConfig | None = None,
    ) -> None:
        self.mapper = mapper
        self.st = stationary
        self.v = 0.0 if stationary is not None else float(speed_in_s)
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

    def lock(self, track: Track) -> None:
        """Stationary: the pipeline measured ``track``; remember where, so a later sighting far
        from there finishes it (the part was moved) instead of silently changing the measured part."""
        for s in self._states.values():
            if s.track is track:
                s.anchor = (
                    (track.belt_x_lead_in + track.belt_x_trail_in) / 2.0,
                    (track.y_min_in + track.y_max_in) / 2.0,
                )
                areas = [o.blob.area_px for o in track.observations if o.valid]
                s.anchor_area = float(np.median(areas)) if areas else 0.0
                s.off = 0
                valid = [o for o in track.observations if o.valid]
                s.anchor_contour = (valid or track.observations)[-1].contour_in
                s.chain = True

    def at_edge(self, track: Track) -> bool:
        """Stationary: the track's latest sighting was at the image/belt edge or linked to the
        edge complex (a hand or fingers): it is reported, never measured."""
        return any(s.track is track and s.unclean for s in self._states.values())

    def take_recheck(self, track: Track) -> bool:
        """Stationary: True once after a measured part's identity was re-established on reappear
        (position, silhouette and area matched); the caller may compare more (the hole kind)."""
        for s in self._states.values():
            if s.track is track and s.recheck:
                s.recheck = False
                return True
        return False

    def release(self, track: Track) -> Track | None:
        """Stationary: finish a live track now (the caller found it is not the measured part)."""
        for s in list(self._states.values()):
            if s.track is track:
                return self._finish(s)
        return None

    def settle_flags(self, track: Track) -> TrackFlag:
        """The finish-time flags (UNSETTLED / FEW_FRAMES / TOUCHES_EDGE) of a still-live track."""
        return self._flags(track) | track.flags

    def update(
        self,
        seq: int,
        t_capture_ns: int,
        blobs: list[Blob],
        hold: bool = False,
        linked: frozenset[int] | set[int] = frozenset(),
    ) -> list[Track]:
        """Link blobs to tracks and return the tracks that finished. Stationary only: ``hold``
        means a hand (or fingers) in view may cover parts, so no unseen track is finished this
        frame; ``linked`` holds the indices of blobs that belong to the edge complex (they touch
        the belt edge, or lie next to a hand or fingers): such a sighting is never clean."""
        if self._t0 is None:
            self._t0 = t_capture_ns
        t_ns = t_capture_ns
        shift = self.v * (t_ns - self._t0) / NS_PER_S
        dets = [self._detect(b, t_ns, shift, j in linked) for j, b in enumerate(blobs)]

        states = sorted(self._states.values(), key=lambda s: s.slot)
        matched: set[int] = set()  # slots
        used: set[int] = set()

        # Merge detection: predicted positions of >= 2 tracks inside one blob.
        for j, d in enumerate(dets):
            inside = [
                s for s in states if s.slot in self._states and self._predicted_inside(s, d, shift)
            ]
            if len(inside) < 2:
                continue
            used.add(j)
            confirmed = [s for s in inside if s.track.track_id != 0]
            if len(confirmed) >= 2:
                # Two or more real parts share one blob: a genuine merge. Flag them MERGED; drop
                # any never-confirmed fragment riding along (it is not a third part).
                for s in inside:
                    matched.add(s.slot)
                    if s.track.track_id != 0:
                        s.track.flags |= TrackFlag.MERGED
                        self._observe(s, seq, d, shift, merged=True)
                    else:
                        del self._states[s.slot]
            else:
                # At most one real part here; the others are duplicate/fragment pieces of this one
                # blob (a shank tip split off at the image edge, or seam residue drawn into it).
                # Keep the real part (else the oldest) and absorb the rest, so one blob is one
                # track and the part is not lost to a spurious MERGE.
                keep = confirmed[0] if confirmed else min(inside, key=lambda s: s.slot)
                for s in inside:
                    if s is not keep:
                        matched.add(s.slot)
                        del self._states[s.slot]
                matched.add(keep.slot)
                self._observe(keep, seq, d, shift, merged=False)

        # Greedy nearest assignment on remaining tracks and blobs.
        pairs = []
        for s in states:
            if s.slot in matched:
                continue
            for j, d in enumerate(dets):
                if j in used:
                    continue
                dist = self._distance(s, d)
                if s.anchor is not None and not s.chain:  # interrupted: also near the anchor
                    dist = min(dist, float(np.hypot(d.cx - s.anchor[0], d.cy - s.anchor[1])))
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
            state = _State(
                Track(track_id=0), d.lead, d.trail, d.cx, d.cy, slot=slot, t_first_ns=d.t_ns
            )
            self._states[slot] = state
            self._observe(state, seq, d, shift, merged=False)

        # Finish tracks that left the view or were missed too long. A track that never earned a
        # public id (no valid observation ever) is a seam tail or an edge/merge fragment, not a
        # part: drop it so it consumes no id and emits nothing.
        finished: list[Track] = []
        cfg = self.cfg
        for s in states:
            if s.slot in matched:
                s.missed = 0
                if s.displaced or s.off >= cfg.settle_frames:
                    # A measured part was swapped in place (a different silhouette sits where it
                    # was): finish it; the blob starts a new track on the next frame.
                    t = self._finish(s)
                    if t.track_id != 0:
                        finished.append(t)
                continue
            if self.st is not None:
                s.held_unseen = s.held_unseen or hold
                last = s.hist[-1].t_ns if s.hist else s.t_first_ns
                gone = (t_ns - last) / NS_PER_S >= self.st.missed_s and not hold
                if s.displaced or gone:
                    t = self._finish(s)
                    if t.track_id != 0:  # a never-confirmed blob was no part: emit nothing
                        finished.append(t)
                continue
            s.missed += 1
            trail_x = s.trail + shift  # image-plane x of the trailing edge now
            if trail_x > self._right_in or s.missed > self.cfg.max_missed_frames:
                t = self._finish(s)
                if t.track_id == 0 and self._rode_the_belt(s):
                    continue  # a seam tail or edge/merge fragment: drop, it is not a part
                if t.track_id == 0:
                    t.track_id = self._next_id
                    self._next_id += 1
                finished.append(t)
        return finished

    def _rode_the_belt(self, s: _State) -> bool:
        """True if this (never-confirmed) track travelled with the belt -- its belt-frame centre
        barely drifted -- rather than sitting still in the image like a stuck/degenerate blob."""
        h = s.hist
        if len(h) < 2:
            return True
        dt = (h[-1].t_ns - h[0].t_ns) / NS_PER_S
        drift = abs(h[-1].cx - h[0].cx)
        return drift <= _BELT_RIDE_DRIFT_FRAC * abs(self.v) * dt

    # ------------------------------------------------------------------ internals
    def _detect(self, blob: Blob, t_ns: int, shift: float, linked: bool = False) -> _Det:
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
            linked,
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
        # A plain in-view sighting of this part alone (may still be area-unstable). In stationary
        # mode a blob linked to the edge complex (fingers next to it) is not clean either.
        clean = not edge and not merged and not d.linked
        valid = clean
        if valid and self.st is not None:
            prev = s.hist[-1] if s.hist else None
            if prev is not None and not prev.edge and not prev.merged:
                if np.hypot(d.cx - prev.cx, d.cy - prev.cy) > self.st.move_tol_in:
                    valid = False
                elif (
                    prev.area > 0
                    and abs(d.blob.area_px - prev.area) / prev.area > cfg.max_area_change
                ):
                    valid = False
        elif valid:
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
        if self.st is not None:
            self._stationary_identity(s, d, merged, clean)
        # A track earns its public id (and is emitted) once it is seen cleanly in view at least
        # once -- a real part, even if its area never settles. A track only ever at the image
        # edge or merged (a seam tail, or an edge/merge fragment) never earns one and is dropped.
        if clean and t.track_id == 0 and not s.displaced:
            t.track_id = self._next_id
            self._next_id += 1
        t.observations.append(Observation(seq, d.t_ns, d.blob, d.contour_in, valid))
        if d.blob.on_seam:
            t.flags |= TrackFlag.ON_SEAM
        s.hist.append(_Hist(d.t_ns, d.cx, d.blob.area_px, edge or d.linked, merged, d.cy))
        if self.st is not None and len(t.observations) > _STATIONARY_WINDOW:
            del t.observations[: len(t.observations) - _STATIONARY_WINDOW]
            del s.hist[: len(s.hist) - _STATIONARY_WINDOW]
        if not merged:  # merged blobs say nothing about this part's own position
            s.lead, s.trail, s.cx, s.cy = d.lead, d.trail, d.cx, d.cy
        self._refresh_extents(s)

    def _stationary_identity(self, s: _State, d: _Det, merged: bool, clean: bool) -> None:
        """Is this sighting still the part this track measured (or is measuring)? See the module
        doc: sets ``displaced`` (finish and re-measure), follows or re-checks a measured part."""
        cfg, st = self.cfg, self.st
        assert st is not None
        gap = bool(s.hist) and (d.t_ns - s.hist[-1].t_ns) / NS_PER_S >= _REAPPEAR_S
        interrupted = gap or s.held_unseen
        s.held_unseen = False
        unclean_before, s.unclean = s.unclean, not clean
        if s.anchor_contour is None:
            # UNMEASURED: back after a gap, a hold (a quick swap under a hand), or after sightings
            # that were not its own clean blob: its frames may show another part. Re-measure.
            if s.hist and clean and (interrupted or unclean_before):
                s.displaced = True
            return
        # MEASURED.
        ratio = abs(d.blob.area_px - s.anchor_area) / s.anchor_area if s.anchor_area > 0 else 0.0
        own = not merged and ratio <= 2 * cfg.max_area_change  # its own blob, its own size
        far_off = not own and not _edge_like(d)  # another part touching it (not a finger)
        s.off = s.off + 1 if far_off else 0
        if interrupted:
            s.chain = False
        if s.chain:
            step = float(np.hypot(d.cx - s.cx, d.cy - s.cy))
            if own and step > st.relocate_in:
                s.displaced = True  # jumped between two sightings: moved, re-measure
            elif own:
                self._follow(s, d)
            else:
                s.chain = False  # merged / a finger on it / area changed: re-check when clear
            return
        if not clean or merged:
            return  # still covered or merged: wait for a clean sighting
        dist = float(np.hypot(d.cx - s.anchor[0], d.cy - s.anchor[1]))  # type: ignore[index]
        same = (
            dist <= st.relocate_in
            and ratio <= cfg.max_area_change
            and _iou(s.anchor_contour, d.contour_in) >= _REAPPEAR_IOU
        )
        if same:
            s.chain = True
            s.recheck = True
            self._follow(s, d)
        else:
            s.displaced = True  # back, but moved or a different part: re-measure

    @staticmethod
    def _follow(s: _State, d: _Det) -> None:
        """The measured part was seen again as itself (its own blob, its measured area): the
        anchor follows it."""
        s.anchor = (d.cx, d.cy)
        s.anchor_contour = d.contour_in

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
        t.t_first_ns = s.t_first_ns if self.st is not None else t.observations[0].t_capture_ns
        t.t_last_ns = t.observations[-1].t_capture_ns

    def _flags(self, t: Track) -> TrackFlag:
        cfg = self.cfg
        flags = TrackFlag(0)
        n_valid = sum(o.valid for o in t.observations)
        run = best = 0
        for o in t.observations:
            run = run + 1 if o.valid else 0
            best = max(best, run)
        if best < cfg.settle_frames:
            flags |= TrackFlag.UNSETTLED
        if n_valid < cfg.min_frames:
            flags |= TrackFlag.FEW_FRAMES
        if all(o.blob.touches_edge for o in t.observations):
            flags |= TrackFlag.TOUCHES_EDGE
        return flags

    def _finish(self, s: _State) -> Track:
        t = s.track
        t.flags |= self._flags(t)
        t.finished = True
        del self._states[s.slot]
        return t
