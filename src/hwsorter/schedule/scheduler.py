"""Send-time formula and the Scheduler.

Two modes share one queue ordered by ``(t_send_ns, group_id)``:

* live (``start()``): a thread sleeps until ``spin_ms`` before the head send time, then spins on
  the injected Clock to hit it;
* fast replay (``replay=True``, driven by ManualClock): no thread and no sleeping. The pipeline
  advances the clock and calls ``advance_to(t)``; due sends fire on the caller's thread in
  ``(t_send_ns, group_id)`` order, so replays give identical send records.

Every send log record carries ``timings["uncalibrated"]`` while timing values are placeholders.
"""

from __future__ import annotations

import heapq
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..clock import NS_PER_MS, NS_PER_S, Clock
from ..config import TimingConfig
from ..contracts import (
    DECISION_LOG_SCHEMA_VERSION,
    GroupDecision,
    MissReason,
    Output,
    OutputAdapter,
    ScheduledOutput,
    SendLogRecord,
    SendRecord,
    to_log_dict,
)

_log = logging.getLogger(__name__)

_POLL_CAP_S = 0.02  # longest coarse wait, so a ManualClock advance is noticed promptly


def send_time_ns(
    d: GroupDecision, exit_x_in: float, timing: TimingConfig, speed_in_s: float
) -> int:
    """Invariant 9: ``t_send = t_ref + d_remaining/v - collector_lead`` with
    ``d_remaining = (exit_x_in - x_lead_in_at_ref) + timing.exit_to_belt_end_in``."""
    if speed_in_s <= 0:
        raise ValueError("belt speed must be positive")
    d_remaining = (exit_x_in - d.x_lead_in_at_ref) + timing.exit_to_belt_end_in
    travel_ns = round(d_remaining / speed_in_s * NS_PER_S)
    return d.t_ref_ns + travel_ns - round(timing.collector_lead_ms * NS_PER_MS)


@dataclass(order=True)
class _Item:
    t_send_ns: int
    group_id: int
    seq: int  # tie-break for duplicate group ids: submission order
    sched: ScheduledOutput = field(compare=False)  # what is actually sent (MISC when late)
    miss: MissReason = field(compare=False, default=MissReason.NONE)
    late_now_ns: int = field(compare=False, default=0)  # submit time of a late item


class Scheduler:
    """Queues ScheduledOutputs and sends each at its time; writes the single "send" log record."""

    def __init__(
        self,
        clock: Clock,
        adapter: OutputAdapter,
        log: Any,
        *,
        spin_ms: float = 4.0,
        uncalibrated: bool = False,
        replay: bool = False,
        keep_records: int = 1000,
    ) -> None:
        self._clock = clock
        self._adapter = adapter
        self._log = log
        self._spin_ns = round(spin_ms * NS_PER_MS)
        self.uncalibrated = uncalibrated
        self.replay = replay
        self._heap: list[_Item] = []
        self._cond = threading.Condition()
        self._seq = 0
        self._thread: threading.Thread | None = None
        self._stopping = False
        self.records: deque[SendRecord] = deque(maxlen=keep_records)
        self.n_sends = 0
        self.n_late = 0

    # ---- public API

    def submit(self, o: ScheduledOutput) -> None:
        """Queue ``o``. If its send time has already passed, send MISC at once (LATE_DECISION)."""
        now = self._clock.now_ns()
        if o.t_send_ns < now:
            sched = ScheduledOutput(o.group_id, Output.MISC, o.member_track_ids, o.t_send_ns)
            item = _Item(o.t_send_ns, o.group_id, 0, sched, MissReason.LATE_DECISION, now)
        else:
            item = _Item(o.t_send_ns, o.group_id, 0, o)
        with self._cond:
            self._seq += 1
            item.seq = self._seq
            heapq.heappush(self._heap, item)
            self._cond.notify_all()
        if self.replay:
            self.advance_to(now)

    def pending(self) -> list[ScheduledOutput]:
        with self._cond:
            return [i.sched for i in sorted(self._heap)]

    def advance_to(self, t_ns: int) -> int:
        """Fast replay: fire every item with ``t_send_ns <= t_ns`` in order; returns the count."""
        n = 0
        while True:
            with self._cond:
                if not self._heap or self._heap[0].t_send_ns > t_ns:
                    return n
                item = heapq.heappop(self._heap)
            self._fire(item, self._replay_actual(item))
            n += 1

    def drain(self) -> int:
        """Fast replay: fire everything still queued, in order (end of a recording)."""
        with self._cond:
            last = max((i.t_send_ns for i in self._heap), default=0)
        return self.advance_to(last)

    def start(self) -> None:
        if self.replay:
            raise RuntimeError("a replay-mode Scheduler has no thread; use advance_to()")
        if self._thread is not None:
            return
        self._stopping = False
        self._thread = threading.Thread(target=self._run, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    # ---- internals

    @staticmethod
    def _replay_actual(item: _Item) -> int:
        # Simulated instant of the send: its target, or the submit time for a late one.
        return item.late_now_ns if item.miss is MissReason.LATE_DECISION else item.t_send_ns

    def _run(self) -> None:
        while True:
            with self._cond:
                while True:
                    if self._stopping:
                        return
                    if not self._heap:
                        self._cond.wait(_POLL_CAP_S * 5)
                        continue
                    remaining = self._heap[0].t_send_ns - self._clock.now_ns()
                    if remaining <= self._spin_ns:
                        break
                    self._cond.wait(min((remaining - self._spin_ns) / NS_PER_S, _POLL_CAP_S))
                target = self._heap[0].t_send_ns
            while self._clock.now_ns() < target:  # spin the final stretch
                if self._stopping:
                    return
            with self._cond:
                if not self._heap or self._heap[0].t_send_ns > self._clock.now_ns():
                    continue
                item = heapq.heappop(self._heap)
            try:
                self._fire(item, self._clock.now_ns())
            except Exception:
                _log.exception("scheduler: error firing group %s", item.group_id)

    def _fire(self, item: _Item, t_actual_ns: int) -> None:
        miss = item.miss
        try:
            self._adapter.send(item.sched, t_actual_ns)
        except Exception:
            miss = MissReason.ADAPTER_ERROR
        self.records.append(SendRecord(item.sched, t_actual_ns, miss))
        self.n_sends += 1
        if miss is MissReason.LATE_DECISION:
            self.n_late += 1
        end_ns = t_actual_ns if self.replay else self._clock.now_ns()
        entry: SendLogRecord = {
            "schema_version": DECISION_LOG_SCHEMA_VERSION,
            "group_id": item.sched.group_id,
            "output": item.sched.output.value,
            "member_track_ids": list(item.sched.member_track_ids),
            "t_send_ns": item.sched.t_send_ns,
            "t_actual_ns": t_actual_ns,
            "miss": miss.value,
            "timings": {
                "latency_ms": (end_ns - item.sched.t_send_ns) / NS_PER_MS,
                "uncalibrated": self.uncalibrated,
            },
        }
        self._log.log("send", **to_log_dict(entry))
