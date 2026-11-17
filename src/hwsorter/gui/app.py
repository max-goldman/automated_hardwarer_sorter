"""Main window: live video on the left, live status and calibration tools on the right."""

from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
)

from ..config import SorterConfig
from ..session import Session
from .debug_overlay import DebugOverlay
from .panels import BeltPanel, ExposurePanel, FocusPanel, LivePanel, Panel, ScalePanel
from .video import VideoWidget


class MainWindow(QMainWindow):
    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session
        self.setWindowTitle("Hardware Sorter")
        self.video = VideoWidget()
        self.tabs = QTabWidget()
        self.panels: list[Panel] = [
            cls(session) for cls in (LivePanel, ExposurePanel, FocusPanel, ScalePanel, BeltPanel)
        ]
        for panel in self.panels:
            self.tabs.addTab(panel, panel.title)
        self.tabs.setMinimumWidth(380)
        self.tabs.currentChanged.connect(self._tab_changed)
        splitter = QSplitter()
        splitter.addWidget(self.video)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)
        self.debug = DebugOverlay(lambda: getattr(self.session._runner, "snapshot", None))
        self.debug_check = QCheckBox("Pipeline debug overlay")
        self.debug_check.toggled.connect(self._debug_toggled)
        self.statusBar().addWidget(self.debug_check)
        self.status_label = QLabel()
        self.statusBar().addPermanentWidget(self.status_label, 1)
        self.resize(1500, 800)
        self._last_seq = -1
        self._tab_changed(0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(int(1000 / session.cfg.gui.display_fps))

    def _tab_changed(self, index: int) -> None:
        panel = self.panels[index]

        def overlay(painter) -> None:
            panel.overlay(painter)
            self.debug(painter)  # draws nothing unless enabled and a snapshot is attached

        self.video.overlay = overlay

    def _debug_toggled(self, on: bool) -> None:
        self.debug.set_enabled(on)
        self.video.update()

    def _tick(self) -> None:
        frame = self.session.capture.latest()
        stats = self.session.capture.stats()
        lost = stats.total_dropped
        exposure = self.session.exposure_ms
        self.status_label.setText(
            f"{stats.fps:5.1f} fps   |   {stats.frames} frames   |   {lost} lost   |   "
            f"exposure {exposure:.1f} ms" + ("   |   ● REC" if self.session.recording else "")
            if exposure is not None
            else f"{stats.fps:5.1f} fps   |   {stats.frames} frames   |   {lost} lost"
        )
        fresh_snapshot = self.debug.refresh()
        if frame is None or frame.seq == self._last_seq:
            if fresh_snapshot:
                self.video.update()  # new snapshot but no new frame
            return
        self._last_seq = frame.seq
        self.video.set_frame(frame.image)
        self.panels[self.tabs.currentIndex()].maybe_frame(frame)

    def closeEvent(self, event) -> None:
        self.timer.stop()
        self.session.stop()
        super().closeEvent(event)


def run(cfg: SorterConfig, source_spec: str = "camera") -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    try:
        session = Session(cfg, source_spec, realtime=True, loop=True)
        session.start()
    except Exception as exc:
        QMessageBox.critical(
            None, "Hardware Sorter", f"Could not start the {source_spec} source:\n\n{exc}"
        )
        return 1
    window = MainWindow(session)
    window.show()
    return app.exec()
