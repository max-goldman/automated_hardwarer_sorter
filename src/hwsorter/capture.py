"""Capture service: one thread pulls frames from a source and fans them out.

Consumers (recorder, pipeline) each get their own bounded queue so a slow consumer can
never stall capture or another consumer. Nothing is dropped silently: source drops,
timestamp gaps and queue overflows are all counted and written to the event log.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .eventlog import EventLog
from .frames import EndOfStream, Frame, FrameSource, SourceStats

log = logging.getLogger(__name__)


def frame_level(image: np.ndarray) -> float:
    """Belt brightness of one frame: the 75th percentile of the image centre.

    A percentile rather than the mean, so parts passing through (always a minority of the
    area, and mostly darker than the belt) do not look like a change in lighting.
    """
    h, w = image.shape
    sample = image[h // 4 : 3 * h // 4 : 8, w // 4 : 3 * w // 4 : 8].reshape(-1)
    k = (3 * sample.size) // 4
    return float(np.partition(sample, k)[k])


class FrameQueue:
    """Bounded frame queue for one consumer. When full, the oldest frame is discarded and counted."""

    def __init__(self, name: str, max_frames: int) -> None:
        self.name = name
        self._max = max_frames
        self._items: deque[Frame | None] = deque()
        self._cond = threading.Condition()
        self.dropped = 0
        self.high_water = 0

    def put(self, frame: Frame | None) -> bool:
        """Add a frame (None marks end of stream). Returns False if an old frame was discarded."""
        with self._cond:
            ok = True
            if frame is not None and len(self._items) >= self._max:
                self._items.popleft()
                self.dropped += 1
                ok = False
            self._items.append(frame)
            self.high_water = max(self.high_water, len(self._items))
            self._cond.notify()
            return ok

    def get(self, timeout: float = 1.0) -> Frame | None:
        """Next frame, or None on timeout. Raises EndOfStream after the end marker."""
        with self._cond:
            if not self._items:
                self._cond.wait(timeout)
            if not self._items:
                return None
            item = self._items.popleft()
            if item is None:
                self._items.appendleft(None)  # keep the marker for later calls
                raise EndOfStream
            return item

    def __len__(self) -> int:
        with self._cond:
            return len(self._items)


@dataclass
class CaptureStats:
    frames: int = 0  # frames received from the source
    missing: int = 0  # frames inferred lost from timestamp gaps
    gaps: int = 0  # number of timestamp gaps
    expected_missing: int = 0  # frames lost in announced disruptions (startup, exposure change)
    fps: float = 0.0  # measured over the last second of capture timestamps
    source: SourceStats = field(default_factory=SourceStats)
    queue_dropped: dict[str, int] = field(default_factory=dict)
    ended: bool = False

    @property
    def total_dropped(self) -> int:
        return (
            self.missing
            + self.source.driver_dropped
            + self.source.queue_dropped
            + sum(self.queue_dropped.values())
        )


class CaptureService:
    def __init__(
        self, source: FrameSource, event_log: EventLog | None = None, level_history: int = 720
    ) -> None:
        self.source = source
        self.events = event_log or EventLog()
        self._queues: list[FrameQueue] = []
        self._lock = threading.Lock()
        self._latest: Frame | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ended = threading.Event()
        self._frames = 0
        self._missing = 0
        self._gaps = 0
        self._expected_missing = 0
        self._disruption_until_ns = 0
        self._disruption_reason = ""
        self._recent_ts: deque[int] = deque(maxlen=int(max(source.info.fps, 1.0)) + 1)
        # Per-frame brightness of the image centre, for flicker and exposure checks.
        self._levels: deque[tuple[int, float]] = deque(maxlen=level_history)
        self._period_ns = int(1e9 / source.info.fps) if source.info.fps > 0 else 0

    def subscribe(self, name: str, max_frames: int) -> FrameQueue:
        queue = FrameQueue(name, max_frames)
        with self._lock:
            self._queues.append(queue)
        return queue

    def unsubscribe(self, queue: FrameQueue) -> None:
        with self._lock:
            if queue in self._queues:
                self._queues.remove(queue)

    def expect_disruption(self, seconds: float, reason: str) -> None:
        """Announce that the stream may hiccup (the camera does when its exposure mode changes).

        Gaps inside the window are still logged, with the reason, but counted as expected
        rather than as unexplained missing frames.
        """
        with self._lock:
            self._disruption_until_ns = max(
                self._disruption_until_ns, time.monotonic_ns() + int(seconds * 1e9)
            )
            self._disruption_reason = reason

    def start(self) -> None:
        if self._thread is not None:
            return
        if self.source.info.live:
            self.expect_disruption(1.0, "stream startup")
        self.source.start()
        self._period_ns = int(1e9 / self.source.info.fps) if self.source.info.fps > 0 else 0
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self.source.stop()

    def wait_ended(self, timeout: float | None = None) -> bool:
        return self._ended.wait(timeout)

    def _run(self) -> None:
        prev_ts: int | None = None
        last_warn = 0.0
        while not self._stop.is_set():
            try:
                frame = self.source.read(timeout=0.2)
            except EndOfStream:
                break
            if frame is None:
                continue
            if prev_ts is not None and self._period_ns:
                dt = frame.t_capture_ns - prev_ts
                if dt > 1.5 * self._period_ns:
                    lost = max(round(dt / self._period_ns) - 1, 1)
                    with self._lock:
                        expected = frame.t_arrival_ns <= self._disruption_until_ns
                        reason = self._disruption_reason
                        if expected:
                            self._expected_missing += lost
                        else:
                            self._missing += lost
                            self._gaps += 1
                    if expected:
                        self.events.log(
                            "frame_gap_expected",
                            seq=frame.seq,
                            gap_ms=round(dt / 1e6, 3),
                            frames_missing=lost,
                            reason=reason,
                        )
                    else:
                        self.events.log(
                            "frame_gap",
                            seq=frame.seq,
                            gap_ms=round(dt / 1e6, 3),
                            frames_missing=lost,
                        )
            prev_ts = frame.t_capture_ns
            level = frame_level(frame.image)
            with self._lock:
                self._frames += 1
                self._latest = frame
                self._recent_ts.append(frame.t_capture_ns)
                self._levels.append((frame.t_capture_ns, level))
                queues = list(self._queues)
            for queue in queues:
                if not queue.put(frame):
                    self.events.log(
                        "queue_overflow",
                        queue=queue.name,
                        seq=frame.seq,
                        dropped_total=queue.dropped,
                    )
                    if time.monotonic() - last_warn > 2.0:
                        last_warn = time.monotonic()
                        log.warning(
                            "consumer %r is falling behind; %d frames discarded so far",
                            queue.name,
                            queue.dropped,
                        )
        with self._lock:
            queues = list(self._queues)
        for queue in queues:
            queue.put(None)
        self._ended.set()

    def latest(self) -> Frame | None:
        with self._lock:
            return self._latest

    def levels(self) -> np.ndarray:
        """Recent per-frame brightness as an (n, 2) array of (t_capture_ns, mean gray level)."""
        with self._lock:
            return np.array(self._levels, dtype=np.float64).reshape(-1, 2)

    def stats(self) -> CaptureStats:
        with self._lock:
            ts = list(self._recent_ts)
            fps = (len(ts) - 1) * 1e9 / (ts[-1] - ts[0]) if len(ts) > 1 and ts[-1] > ts[0] else 0.0
            return CaptureStats(
                frames=self._frames,
                missing=self._missing,
                gaps=self._gaps,
                expected_missing=self._expected_missing,
                fps=fps,
                source=self.source.stats(),
                queue_dropped={q.name: q.dropped for q in self._queues},
                ended=self._ended.is_set(),
            )
