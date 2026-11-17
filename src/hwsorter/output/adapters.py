"""Output adapters: deliver one collector command. I/O only; the Scheduler writes the log."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable

from ..clock import NS_PER_MS
from ..contracts import ScheduledOutput


class ScreenLogAdapter:
    """Shows each send on screen and keeps the last ``keep`` lines for the GUI.

    ``uncalibrated`` tags every line "UNCALIBRATED" while timing values are placeholders.
    ``echo`` receives each line (default: no printing; pass ``print`` for a console)."""

    def __init__(
        self,
        keep: int = 50,
        uncalibrated: bool = False,
        echo: Callable[[str], None] | None = None,
    ) -> None:
        self._lines: deque[str] = deque(maxlen=max(1, keep))
        self._lock = threading.Lock()
        self.uncalibrated = uncalibrated
        self._echo = echo

    def send(self, o: ScheduledOutput, t_actual_ns: int) -> None:
        err_ms = (t_actual_ns - o.t_send_ns) / NS_PER_MS
        tag = " UNCALIBRATED" if self.uncalibrated else ""
        line = (
            f"SEND group {o.group_id} -> {o.output.value} "
            f"(sched {o.t_send_ns} ns, actual {t_actual_ns} ns, {err_ms:+.3f} ms){tag}"
        )
        with self._lock:
            self._lines.append(line)
        if self._echo:
            self._echo(line)

    def announce(self, o: ScheduledOutput, rule: str = "") -> None:
        """Stationary mode: show a group decision at once (the belt is stopped; nothing is sent)."""
        tail = f" ({rule})" if rule else ""
        members = ",".join(f"#{t}" for t in o.member_track_ids)
        line = f"DECISION group {o.group_id} [{members}] -> {o.output.value}{tail}"
        with self._lock:
            self._lines.append(line)
        if self._echo:
            self._echo(line)

    def recent(self) -> list[str]:
        with self._lock:
            return list(self._lines)

    def close(self) -> None:
        pass


class MockSerialAdapter:
    """Stands in for the Arduino link: records every command in ``sent``."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.sent: list[tuple[ScheduledOutput, int]] = []
        self.announced: list[tuple[ScheduledOutput, str]] = []  # stationary decisions (not sends)
        self.closed = False
        self.fail_with = fail_with  # raise this from send (tests the ADAPTER_ERROR path)

    def send(self, o: ScheduledOutput, t_actual_ns: int) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append((o, t_actual_ns))

    def announce(self, o: ScheduledOutput, rule: str = "") -> None:
        self.announced.append((o, rule))

    def close(self) -> None:
        self.closed = True
