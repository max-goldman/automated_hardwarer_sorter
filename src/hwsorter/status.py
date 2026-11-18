"""One-line run status for ``sorter run`` (terminal and window). Qt-free; pure formatting.

The line answers "what is the pipeline doing right now": parts in view, measuring progress,
whether a hand is holding decisions (and why), frame rate, and the last decision.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .pipeline import PipelineSnapshot


def _name(value: Any) -> str:
    return str(getattr(value, "value", value))


def hand_text(snap: PipelineSnapshot) -> str:
    """Why a hand is (or is not) in view; the blob's size and edge make a false hand diagnosable."""
    if not snap.hand:
        return ""
    edge = f", touches the {snap.hand_edge} edge" if snap.hand_edge else ""
    return (
        f"HAND in view: holding decisions (blob {snap.hand_area_in2:.2f} in2 "
        f"> {snap.hand_max_in2:.2f} max{edge})"
    )


def format_status(
    snap: PipelineSnapshot | None, t_s: float, last: str = "none yet", late: int = 0
) -> str:
    """The status line for one snapshot. ``t_s`` is seconds since the run started."""
    head = f"t={t_s:.1f}s  "
    if snap is None:
        return head + "waiting for frames"
    n = len(snap.tracks)
    if snap.stationary:
        measured = sum(1 for tr in snap.tracks if tr.measured)
        waiting = [tr for tr in snap.tracks if not tr.measured]
        parts = f"{n} parts ({measured} measured"
        if waiting:
            target = snap.feature_target or "?"
            prog = ", ".join(f"#{tr.track_id} {tr.n_features}/{target}" for tr in waiting)
            parts += f", {len(waiting)} measuring {prog} frames"
        parts += ")"
        body = f"in view: {parts}  " + (hand_text(snap) if snap.hand else "hand: no")
    else:
        pend = ", ".join(
            f"g{p.group_id}->{p.output.value} in {p.countdown_s:.1f}s" for p in snap.pending
        )
        body = f"in view: {n} parts  pending sends: {pend or 'none'}  late {late}"
    return f"{head}{body}  fps {snap.fps:.0f}  last: {last}"


def last_decision(log: Any) -> str:
    """Describe the newest ``group`` record of an EventLog, with its members' classes."""
    groups = log.recent("group")
    if not groups:
        return "none yet"
    g = groups[-1]
    members = list(g.get("member_track_ids", ()))
    classes = {r.get("track_id"): r.get("part_class") for r in log.recent("part")}
    names = ",".join(_name(classes[m]) for m in members if classes.get(m) is not None)
    ids = ",".join(f"#{m}" for m in members)
    detail = f" ({names})" if names and names != _name(g.get("output")) else ""
    return f"group {g.get('group_id')} [{ids}] -> {_name(g.get('output'))}{detail} ({g.get('rule')})"


class StatusReporter:
    """Builds status lines for a running pipeline; ``due()`` paces them to about one a second."""

    def __init__(
        self, log: Any, late: Callable[[], int] | None = None, every_s: float = 1.0
    ) -> None:
        self.log, self._late, self.every_s = log, late, every_s
        self._t0: int | None = None
        self._next = 0.0

    def line(self, snap: PipelineSnapshot | None) -> str:
        if snap is not None and self._t0 is None:
            self._t0 = snap.t_capture_ns
        t_s = (snap.t_capture_ns - self._t0) / 1e9 if snap is not None and self._t0 else 0.0
        late = self._late() if self._late else 0
        return format_status(snap, t_s, last_decision(self.log), late)

    def due(self, now_s: float) -> bool:
        if now_s >= self._next:
            self._next = now_s + self.every_s
            return True
        return False
