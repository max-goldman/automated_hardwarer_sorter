"""Clocks. All times in the program are integer nanoseconds on one monotonic timeline.

Live runs use MonotonicClock (the same host clock the macOS camera stamps frames with).
Replay and tests use ManualClock, advanced by frame timestamps, so recorded runs replay
deterministically at any speed.
"""

from __future__ import annotations

import threading
import time
from typing import Protocol

NS_PER_S = 1_000_000_000
NS_PER_MS = 1_000_000


class Clock(Protocol):
    def now_ns(self) -> int: ...

    def sleep_until_ns(self, t_ns: int) -> None: ...


class MonotonicClock:
    """Real time. sleep_until sleeps coarsely, then spins the last stretch for accuracy."""

    def __init__(self, spin_ns: int = 4_000_000) -> None:
        self._spin_ns = spin_ns

    def now_ns(self) -> int:
        return time.monotonic_ns()

    def sleep_until_ns(self, t_ns: int) -> None:
        while True:
            remaining = t_ns - time.monotonic_ns()
            if remaining <= 0:
                return
            if remaining > self._spin_ns:
                time.sleep((remaining - self._spin_ns) / NS_PER_S)
            # else: busy-wait the final stretch


class ManualClock:
    """A clock that only moves when told to. sleep_until blocks until advanced past t."""

    def __init__(self, start_ns: int = 0) -> None:
        self._now = start_ns
        self._cond = threading.Condition()

    def now_ns(self) -> int:
        with self._cond:
            return self._now

    def advance_to(self, t_ns: int) -> None:
        with self._cond:
            if t_ns > self._now:
                self._now = t_ns
                self._cond.notify_all()

    def sleep_until_ns(self, t_ns: int) -> None:
        with self._cond:
            self._cond.wait_for(lambda: self._now >= t_ns)
