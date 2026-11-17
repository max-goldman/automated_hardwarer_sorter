"""Replay a recording as a FrameSource, in real time or as fast as the consumer reads."""
from __future__ import annotations

import time
from pathlib import Path

from ..frames import EndOfStream, Frame, FrameSource, SourceInfo, SourceStats
from .store import RecordingReader


class ReplaySource(FrameSource):
    """Frames keep their recorded sequence numbers and capture timestamps.

    realtime=True paces delivery to the recorded timing (for watching); realtime=False
    hands frames over as fast as they are read (for regression tests and benchmarks).
    loop=True restarts at the end, shifting timestamps so time keeps moving forward.
    """

    def __init__(self, path: str | Path, realtime: bool = False, loop: bool = False) -> None:
        self.reader = RecordingReader(path)
        self._realtime = realtime
        self._loop = loop
        self._i = 0
        self._lap_offset_ns = 0
        self._seq_offset = 0
        self._t0_wall: float | None = None
        self._t0_rec: int | None = None
        self._stats = SourceStats()
        self.info = SourceInfo(
            name=f"replay:{Path(path).name}", width=self.reader.width, height=self.reader.height,
            fps=self.reader.fps, live=False, details={"recording": str(path), "realtime": realtime, **self.reader.meta},
        )

    def start(self) -> None:
        self._i = 0
        self._t0_wall = None

    def read(self, timeout: float = 1.0) -> Frame | None:
        n = len(self.reader)
        if self._i >= n:
            if not self._loop or n == 0:
                raise EndOfStream
            period_ns = int(1e9 / self.reader.fps)
            span = int(self.reader.index[-1]["t_capture_ns"] - self.reader.index[0]["t_capture_ns"]) + period_ns
            self._lap_offset_ns += span
            self._seq_offset += int(self.reader.index[-1]["seq"] - self.reader.index[0]["seq"]) + 1
            self._i = 0
        frame = self.reader.read(self._i)
        self._i += 1
        if self._lap_offset_ns:
            frame = Frame(frame.seq + self._seq_offset, frame.t_capture_ns + self._lap_offset_ns,
                          frame.t_arrival_ns + self._lap_offset_ns, frame.image)
        if self._realtime:
            if self._t0_wall is None:
                self._t0_wall, self._t0_rec = time.monotonic(), frame.t_capture_ns
            due = self._t0_wall + (frame.t_capture_ns - self._t0_rec) / 1e9
            delay = due - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        self._stats.delivered += 1
        return frame

    def stop(self) -> None:
        pass

    def stats(self) -> SourceStats:
        return SourceStats(self._stats.delivered, 0, 0)
