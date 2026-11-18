"""The ``sorter run`` window: live video with the pipeline debug overlay and the status line.

Consumes only ``PipelineSnapshot`` (the runner's latest) and the capture service's latest frame.
Closing the window, Ctrl-C in the terminal, or the end of a finite source ends the run; the
caller then shuts the session down (which writes ``session_end``).
"""

from __future__ import annotations

import signal
import sys
import time
from collections.abc import Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow

from ..pipeline import PipelineSnapshot
from ..session import Session
from ..status import StatusReporter
from .debug_overlay import DebugOverlay
from .video import VideoWidget


class RunWindow(QMainWindow):
    def __init__(
        self,
        session: Session,
        snapshot: Callable[[], PipelineSnapshot | None],
        reporter: StatusReporter,
        *,
        runner_done: Callable[[], bool] = lambda: False,
        stop_after_s: float | None = None,
        echo: Callable[[str], None] | None = print,
    ) -> None:
        super().__init__()
        self.session, self.reporter, self._snapshot = session, reporter, snapshot
        self._runner_done, self._echo = runner_done, echo
        self._deadline = time.monotonic() + stop_after_s if stop_after_s else None
        self.setWindowTitle(f"Hardware Sorter: run ({session.source_spec})")
        self.video = VideoWidget()
        self.debug = DebugOverlay(snapshot)
        self.debug.set_enabled(True)
        self.video.overlay = self.debug
        self.setCentralWidget(self.video)
        self.status_label = QLabel("waiting for frames")
        self.statusBar().addWidget(self.status_label, 1)
        self.resize(1300, 800)
        self.lines: list[str] = []
        self._last_seq = -1
        self._t_start = time.monotonic()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(int(1000 / session.cfg.gui.display_fps))

    def tick(self) -> None:
        changed = self.debug.refresh()
        frame = self.session.capture.latest()
        if frame is not None and frame.seq != self._last_seq:
            self._last_seq = frame.seq
            self.video.set_frame(frame.image)
        elif changed:
            self.video.update()
        now = time.monotonic() - self._t_start
        if self.reporter.due(now):
            line = self.reporter.line(self._snapshot())
            self.lines.append(line)
            self.status_label.setText(line)
            if self._echo is not None:
                self._echo(line)
        ended = self.session.capture.wait_ended(0)
        timed_out = self._deadline is not None and time.monotonic() > self._deadline
        if (ended and self._runner_done()) or timed_out:
            self.close()

    def closeEvent(self, event) -> None:
        self.timer.stop()
        super().closeEvent(event)


def run_window(window_factory: Callable[[], RunWindow]) -> int:
    """Show the window and run the Qt loop until it closes. Ctrl-C closes it (clean shutdown)."""
    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = window_factory()
    window.show()
    previous = signal.signal(signal.SIGINT, lambda *_: window.close())
    pump = QTimer()  # lets the Python signal handler run while Qt owns the loop
    pump.start(200)
    pump.timeout.connect(lambda: None)
    try:
        code = app.exec()
    finally:
        pump.stop()
        signal.signal(signal.SIGINT, previous)  # Ctrl-C works again during a slow shutdown
    return code
