"""Pipeline debug overlay: draws a PipelineSnapshot over the live video.

GUI thread only. The window pulls the *latest* snapshot (a plain attribute read of an immutable
object, never blocking the pipeline thread) on its display timer, so drawing is capped at
``gui.display_fps``. With no snapshot, or when disabled, nothing is drawn.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF

from ..contracts import TrackFlag, Verdict
from ..pipeline import PipelineSnapshot, TrackView

_GOOD = QColor(80, 220, 120)
_BAD = QColor(255, 90, 80)
_WARN = QColor(255, 200, 60)
_INFO = QColor(120, 200, 255)


def _points(outline: np.ndarray) -> np.ndarray:
    return np.asarray(outline, dtype=float).reshape(-1, 2)


def _centroid(track: TrackView) -> tuple[float, float] | None:
    if track.outline_px is None or len(track.outline_px) == 0:
        return None
    pts = _points(track.outline_px)
    return float(pts[:, 0].mean()), float(pts[:, 1].mean())


def track_label(track: TrackView) -> str:
    out = track.predicted_output.value if track.predicted_output is not None else "?"
    score = f" fit {track.score:.2f}" if track.score is not None else ""
    verdict = ""
    if track.verdict is not None and track.verdict is not Verdict.CONFIDENT:
        verdict = f" [{track.verdict.value}]"
    return f"#{track.track_id} {out}{score}{verdict}"


class DebugOverlay:
    """Callable painter hook (image-pixel coordinates) plus the state it draws from."""

    def __init__(self, source: Callable[[], PipelineSnapshot | None] | None = None) -> None:
        self.source = source
        self.enabled = False
        self.snapshot: PipelineSnapshot | None = None
        self._picked_at = 0.0

    # -- state -----------------------------------------------------------------------
    def refresh(self) -> bool:
        """Pull the latest snapshot (cheap attribute read). Returns True if it changed."""
        if not self.enabled or self.source is None:
            return False
        snap = self.source()
        changed = snap is not self.snapshot
        if changed:
            self.snapshot = snap
            self._picked_at = time.monotonic()
        return changed

    def set_enabled(self, on: bool) -> None:
        self.enabled = on
        if not on:
            self.snapshot = None
        else:
            self.refresh()

    @property
    def active(self) -> bool:
        return self.enabled and self.snapshot is not None

    @property
    def track_count(self) -> int:
        return len(self.snapshot.tracks) if self.active and self.snapshot else 0

    def countdowns(self) -> list[tuple[int, str, float]]:
        """(group_id, output, seconds remaining) for each pending send, aged since pulled."""
        if not self.active or self.snapshot is None:
            return []
        age = time.monotonic() - self._picked_at
        return [
            (p.group_id, p.output.value, max(0.0, p.countdown_s - age))
            for p in self.snapshot.pending
        ]

    def hud_lines(self) -> list[str]:
        snap = self.snapshot
        if not self.active or snap is None:
            return []
        lines = [
            f"{snap.fps:5.1f} fps  {snap.latency_ms:5.1f} ms/frame  "
            f"dropped {snap.n_dropped}  skipped {snap.n_skipped}",
            f"tracks {len(snap.tracks)}  parts {snap.n_parts}  groups {snap.n_groups}",
        ]
        lines += [f"send g{g} -> {out} in {s:4.2f} s" for g, out, s in self.countdowns()]
        return lines

    # -- drawing ---------------------------------------------------------------------
    def __call__(self, painter: QPainter) -> None:
        snap = self.snapshot
        if not self.active or snap is None:
            return
        scale = max(painter.worldTransform().m11(), 1e-6)  # image px -> screen px
        font = QFont()
        font.setPixelSize(max(1, round(13 / scale)))
        painter.setFont(font)
        centres: dict[int, tuple[float, float]] = {}
        for track in snap.tracks:
            self._draw_track(painter, track, scale, centres)
        for gap in snap.gaps:
            a, b = centres.get(gap.upstream_id), centres.get(gap.downstream_id)
            if a is None or b is None:
                continue
            painter.setPen(self._pen(_BAD if gap.rework else _INFO, scale, dash=True))
            painter.drawLine(QPointF(*a), QPointF(*b))
            text = f"{gap.gap_in:.2f} in" + ("  REWORK" if gap.rework else "")
            painter.drawText(QPointF((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), text)
        painter.setPen(_INFO)
        line_h = 16 / scale
        for i, line in enumerate(self.hud_lines()):
            painter.drawText(QPointF(8 / scale, (18 + 16 * i) / scale + 0 * line_h), line)

    @staticmethod
    def _pen(color: QColor, scale: float, dash: bool = False) -> QPen:
        pen = QPen(color)
        pen.setCosmetic(True)
        pen.setWidthF(2.0)
        if dash:
            pen.setStyle(Qt.PenStyle.DashLine)
        return pen

    def _draw_track(
        self,
        painter: QPainter,
        track: TrackView,
        scale: float,
        centres: dict[int, tuple[float, float]],
    ) -> None:
        color = _BAD if track.rework else (_GOOD if track.verdict is Verdict.CONFIDENT else _WARN)
        c = _centroid(track)
        if track.outline_px is not None and len(track.outline_px):
            pts = _points(track.outline_px)
            painter.setPen(self._pen(color, scale))
            painter.drawPolyline(QPolygonF([QPointF(float(x), float(y)) for x, y in pts]))
        if c is None:
            return
        centres[track.track_id] = c
        flags = [f.name for f in TrackFlag if f.name and f in track.flags]
        meas = (
            f"belt {track.belt_x_lead_in:.1f}..{track.belt_x_trail_in:.1f} in  n={track.n_features}"
        )
        painter.setPen(color)
        x, y = c
        painter.drawText(QPointF(x, y), track_label(track))
        painter.drawText(QPointF(x, y + 15 / scale), meas)
        if flags:
            painter.drawText(QPointF(x, y + 30 / scale), " ".join(flags))
