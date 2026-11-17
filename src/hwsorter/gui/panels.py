"""Side panels of the main window: live status and the calibration tools."""
from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..calibration import ExposureCalibration, FocusCalibration, save_calibration
from ..calibration.exposure import exposure_advice, flicker_stats, max_exposure_ms, motion_blur_px
from ..calibration.focus import focus_verdict, measure_sharpness
from ..calibration.roi import find_belt_edges, resolve_roi
from ..calibration.scale import TargetView, detect_target, nominal_focal_px, solve_scale
from ..calibration.store import utc_now
from ..capture import frame_level
from ..frames import Frame
from ..session import Session

NOMINAL_PX_PER_IN = 367.0   # estimate used only until scale is calibrated
GOOD, WARN, BAD = "#1a7f37", "#9a6700", "#cf222e"


def _note(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _pen(color: str, width: float = 2.0) -> QPen:
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCosmetic(True)   # constant on-screen width whatever the video scale
    return pen


class Panel(QWidget):
    """Base class. on_frame runs at most every `interval_s` while the panel is visible."""

    title = ""
    interval_s = 0.2

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session
        self._last_run = 0.0
        self.layout_ = QVBoxLayout(self)
        self.layout_.setAlignment(Qt.AlignmentFlag.AlignTop)

    def maybe_frame(self, frame: Frame) -> None:
        now = time.monotonic()
        if now - self._last_run >= self.interval_s:
            self._last_run = now
            self.on_frame(frame)

    def on_frame(self, frame: Frame) -> None:  # noqa: B027 - optional hook
        pass

    def overlay(self, painter: QPainter) -> None:  # noqa: B027 - optional hook
        pass

    def roi_of(self, image: np.ndarray) -> tuple[int, int, int, int]:
        """(top, bottom, left, right) of the belt: saved calibration if any, else detected now."""
        roi = self.session.calibration.roi or resolve_roi(image, self.session.cfg.roi)
        return roi.top, roi.bottom, roi.left, roi.right

    def px_per_in(self) -> tuple[float, bool]:
        scale = self.session.calibration.scale
        return (scale.px_per_in, True) if scale else (NOMINAL_PX_PER_IN, False)

    def save(self) -> str:
        return str(save_calibration(self.session.calibration))


class LivePanel(Panel):
    title = "Live"
    interval_s = 0.25

    def __init__(self, session: Session) -> None:
        super().__init__(session)
        form = QFormLayout()
        self.fields = {key: QLabel("–") for key in ("Source", "Frame rate", "Frames", "Missing frames", "Dropped in queues", "Exposure", "Timestamps", "Calibration")}
        for key, label in self.fields.items():
            label.setWordWrap(True)
            form.addRow(key + ":", label)
        self.layout_.addLayout(form)
        self.layout_.addSpacing(12)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("recording name (optional)")
        self.record_button = QPushButton("Start recording")
        self.record_button.clicked.connect(self._toggle_recording)
        self.record_status = _note("Recordings are lossless and go to the recordings folder.")
        for widget in (self.name_edit, self.record_button, self.record_status):
            self.layout_.addWidget(widget)
        self.layout_.addSpacing(12)
        self.events_label = _note()
        self.layout_.addWidget(self.events_label)

    def _toggle_recording(self) -> None:
        if self.session.recording:
            summary = self.session.stop_recording()
            lost = summary["not_recorded"] + summary["frames_missing_during_recording"] + summary["source_drops_during_recording"]
            self.record_button.setText("Start recording")
            self.record_status.setText(
                f"Saved {summary['frames']} frames ({summary['megabytes']:.0f} MB) to {summary['path']}. "
                + ("No frames lost." if not lost else f"{lost} frames were lost; see the log.")
            )
        else:
            path = self.session.start_recording(self.name_edit.text())
            self.record_button.setText("Stop recording")
            self.record_status.setText(f"Recording to {path} …")

    def on_frame(self, frame: Frame) -> None:
        stats = self.session.capture.stats()
        info = self.session.source.info
        self.fields["Source"].setText(f"{info.name}, {info.width} x {info.height}")
        self.fields["Frame rate"].setText(f"{stats.fps:.1f} fps (nominal {info.fps:g})")
        self.fields["Frames"].setText(f"{stats.frames}")
        self.fields["Missing frames"].setText(f"{stats.missing} unexplained in {stats.gaps} gaps, {stats.source.driver_dropped} reported by the driver; "
                                              f"{stats.expected_missing} during startup or exposure changes")
        queue_drops = stats.source.queue_dropped + sum(stats.queue_dropped.values())
        self.fields["Dropped in queues"].setText(str(queue_drops))
        exposure = self.session.exposure_ms
        self.fields["Exposure"].setText(f"{exposure:.1f} ms" if exposure is not None else "–")
        self.fields["Timestamps"].setText(str(info.details.get("timestamps", "source")))
        missing = self.session.calibration.missing()
        self.fields["Calibration"].setText("complete" if not missing else "not yet calibrated: " + ", ".join(missing))
        if self.session.recording:
            status = self.session.recording_status()
            self.record_status.setText(f"Recording: {status['frames']} frames, {status['megabytes']:.0f} MB, "
                                       f"{status['backlog']} waiting for disk, {status['not_recorded']} not recorded")
        gaps = self.session.events.recent("frame_gap")[-4:]
        self.events_label.setText("\n".join(f"gap of {g['gap_ms']:.1f} ms at frame {g['seq']} ({g['frames_missing']} missing)" for g in gaps))


class Histogram(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(90)
        self._counts = np.zeros(64)

    def set_image(self, image: np.ndarray) -> None:
        self._counts = np.bincount(image.reshape(-1)[::7] >> 2, minlength=64).astype(float)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(245, 245, 245))
        peak = max(float(self._counts.max()), 1.0)
        width = self.width() / 64.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(70, 70, 70))
        for i, count in enumerate(self._counts):
            height = (count / peak) ** 0.5 * (self.height() - 2)   # square-root scale shows small peaks
            painter.drawRect(QRectF(i * width, self.height() - height, max(width - 1, 1), height))


class ExposurePanel(Panel):
    title = "Exposure"

    def __init__(self, session: Session) -> None:
        super().__init__(session)
        lo, hi, step = session.control.exposure_limits_ms()
        self._step = step
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(int(round(lo / step)), int(round(min(hi, 5.0) / step)))
        self.slider.setValue(int(round((session.exposure_ms or session.cfg.camera.exposure_ms) / step)))
        self.slider.setEnabled(session.source.info.live)
        self.value_label = QLabel()
        self.slider.valueChanged.connect(lambda v: self.value_label.setText(f"{v * step:.1f} ms"))
        self.slider.sliderReleased.connect(self._apply)
        self.value_label.setText(f"{self.slider.value() * step:.1f} ms")
        row = QHBoxLayout()
        row.addWidget(QLabel("Exposure:"))
        row.addWidget(self.slider, 1)
        row.addWidget(self.value_label)
        self.layout_.addLayout(row)
        form = QFormLayout()
        self.fields = {key: QLabel("–") for key in ("Belt brightness", "Saturated", "Motion blur", "Flicker", "Recommended")}
        for key, label in self.fields.items():
            form.addRow(key + ":", label)
        self.layout_.addLayout(form)
        self.histogram = Histogram()
        self.layout_.addWidget(QLabel("Brightness histogram (0 to 255):"))
        self.layout_.addWidget(self.histogram)
        self.advice = _note()
        self.layout_.addWidget(self.advice)
        self.recommend_button = QPushButton("Use recommended exposure")
        self.recommend_button.clicked.connect(self._use_recommended)
        self.recommend_button.setEnabled(session.source.info.live)
        self.save_button = QPushButton("Save exposure to calibration")
        self.save_button.clicked.connect(self._save)
        self.layout_.addWidget(self.recommend_button)
        self.layout_.addWidget(self.save_button)
        self.result = _note()
        self.layout_.addWidget(self.result)
        self._recommended = session.cfg.camera.exposure_ms
        self._latest: tuple[float, float, float, float] | None = None
        self._belt_stats: tuple[float, float] | None = None

    def _apply(self) -> None:
        try:
            actual = self.session.set_exposure(self.slider.value() * self._step)
            self.value_label.setText(f"{actual:.1f} ms")
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self.result.setText(f"Could not set exposure: {exc}")

    def _use_recommended(self) -> None:
        self.slider.setValue(int(round(self._recommended / self._step)))
        self._apply()

    def on_frame(self, frame: Frame) -> None:
        cfg = self.session.cfg
        top, bottom, left, right = self.roi_of(frame.image)
        belt = frame.image[top:bottom, left:right]
        levels = self.session.capture.levels()[-240:, 1]
        flicker = flicker_stats(levels)
        # Brightness figures come from normally lit frames only; a frame caught in a flicker dip is skipped.
        own_level = frame_level(frame.image)
        if self._belt_stats is None or own_level >= 0.97 * flicker.plateau:
            self._belt_stats = (float(np.median(belt[::4, ::4])), float((belt >= 250).mean() * 100.0))
            self.histogram.set_image(belt)
        belt_level, saturated = self._belt_stats
        speed = self.session.calibration.belt.speed_in_s if self.session.calibration.belt else cfg.belt.speed_in_s
        ppi, calibrated = self.px_per_in()
        exposure = self.session.exposure_ms or cfg.camera.exposure_ms
        blur = motion_blur_px(exposure, speed, ppi)
        self._recommended = float(max_exposure_ms(cfg.exposure.max_blur_px, speed, ppi, self._step))
        self.fields["Belt brightness"].setText(f"{belt_level:.0f} of 255 (aim for about {cfg.exposure.belt_target_level})")
        self.fields["Saturated"].setText(f"{saturated:.1f}% of belt pixels")
        self.fields["Motion blur"].setText(f"{blur:.2f} px at {speed:g} in/s" + ("" if calibrated else " (scale estimated)"))
        self.fields["Flicker"].setText(f"{flicker.dipped_pct:.0f}% of frames dimmed, worst {flicker.swing_pct:.0f}% down")
        self.fields["Recommended"].setText(f"{self._recommended:.1f} ms or less (blur limit {cfg.exposure.max_blur_px:g} px)")
        notes = exposure_advice(exposure, belt_level, flicker, blur, self._recommended, saturated, cfg.exposure)
        self.advice.setText("\n\n".join(notes) if notes else "Exposure, brightness and lighting stability all look good.")
        self.advice.setStyleSheet(f"color: {BAD if notes else GOOD};")
        self._latest = (exposure, belt_level, flicker.swing_pct, blur)

    def _save(self) -> None:
        if self._latest is None:
            return
        exposure, level, swing, blur = self._latest
        self.session.calibration.exposure = ExposureCalibration(
            exposure_ms=exposure, belt_level=round(level, 1), flicker_swing_pct=round(swing, 1), blur_px=round(blur, 2), measured_utc=utc_now())
        self.result.setText(f"Saved to {self.save()}. To make {exposure:.1f} ms the startup default, set exposure_ms under [camera] in config/local.toml.")


class FocusPanel(Panel):
    title = "Focus"

    def __init__(self, session: Session) -> None:
        super().__init__(session)
        self.width_label = QLabel("–")
        font = QFont()
        font.setPointSize(40)
        font.setBold(True)
        self.width_label.setFont(font)
        self.verdict = _note("Put the calibration target (or any part) on the belt under the camera, then turn the lens.")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setFormat("%p% of the sharpest seen")
        reset = QPushButton("Reset \"sharpest seen\"")
        reset.clicked.connect(self._reset)
        save = QPushButton("Save focus to calibration")
        save.clicked.connect(self._save)
        self.result = _note()
        cfg = session.cfg.focus
        guide = _note(f"Edge width is how many pixels a sharp edge takes to go from dark to bright. "
                      f"{cfg.edge_width_good_px:g} px or less is sharp; over {cfg.edge_width_soft_px:g} px is too soft to measure parts reliably. "
                      "Turn the lens until the percentage bar peaks, then check the edge width.")
        for widget in (QLabel("Edge width:"), self.width_label, self.verdict, self.bar, reset, save, self.result, guide):
            self.layout_.addWidget(widget)
        self._best = 0.0
        self._width: float | None = None

    def _reset(self) -> None:
        self._best = 0.0

    def on_frame(self, frame: Frame) -> None:
        cfg = self.session.cfg.focus
        result = measure_sharpness(frame.image, roi=self.roi_of(frame.image))
        self._width = result.width_px
        self._best = max(self._best, result.energy)
        self.bar.setValue(int(round(100 * result.energy / self._best)) if self._best > 0 else 0)
        status, sentence = focus_verdict(result.width_px, cfg.edge_width_good_px, cfg.edge_width_soft_px)
        color = {"sharp": GOOD, "slightly soft": WARN, "too soft": BAD}.get(status, "#57606a")
        self.width_label.setText(f"{result.width_px:.1f} px" if result.width_px is not None else "–")
        self.width_label.setStyleSheet(f"color: {color};")
        self.verdict.setText(sentence)
        self.verdict.setStyleSheet(f"color: {color};")

    def _save(self) -> None:
        if self._width is None:
            self.result.setText("Nothing to save: no edges are being measured.")
            return
        self.session.calibration.focus = FocusCalibration(edge_width_px=round(self._width, 2), measured_utc=utc_now())
        self.result.setText(f"Saved to {self.save()}.")


class ScalePanel(Panel):
    title = "Scale"
    interval_s = 0.25

    def __init__(self, session: Session) -> None:
        super().__init__(session)
        target = session.cfg.calibration_target
        intro = _note("1. Save and print the target at 100%, then measure its scale bar.\n"
                      "2. Lay the target flat on the belt under the camera.\n"
                      "3. Capture three or more views, moving and turning the target between them.\n"
                      "4. Solve and save.")
        pdf_button = QPushButton("Save target PDF…")
        pdf_button.clicked.connect(self._save_pdf)
        self.status = _note("Looking for the target…")
        self.capture_button = QPushButton("Capture view")
        self.capture_button.clicked.connect(self._capture)
        clear = QPushButton("Clear captured views")
        clear.clicked.connect(self._clear)
        self.count_label = QLabel("0 views captured")
        self.bar_spin = QDoubleSpinBox()
        self.bar_spin.setRange(target.scale_bar_mm * 0.9, target.scale_bar_mm * 1.1)
        self.bar_spin.setDecimals(2)
        self.bar_spin.setSingleStep(0.05)
        self.bar_spin.setValue(target.scale_bar_mm)
        self.bar_spin.setSuffix(" mm")
        form = QFormLayout()
        form.addRow(f"Measured length of the {target.scale_bar_mm:g} mm bar:", self.bar_spin)
        solve = QPushButton("Solve and save")
        solve.clicked.connect(self._solve)
        self.result = _note()
        for widget in (intro, pdf_button, self.status, self.capture_button, clear, self.count_label):
            self.layout_.addWidget(widget)
        self.layout_.addLayout(form)
        self.layout_.addWidget(solve)
        self.layout_.addWidget(self.result)
        self._view: TargetView | None = None
        self._views: list[TargetView] = []
        self._size = (session.source.info.width, session.source.info.height)
        self._show_existing()

    def _show_existing(self) -> None:
        scale = self.session.calibration.scale
        if scale:
            self.result.setText(f"Saved calibration: {scale.px_per_in:.1f} px/in, fit error {scale.reprojection_rms_px:.2f} px ({scale.measured_utc}).")

    def _save_pdf(self) -> None:
        from ..calibration.target import write_target_pdf

        path, _ = QFileDialog.getSaveFileName(self, "Save calibration target", "calibration-target.pdf", "PDF (*.pdf)")
        if path:
            write_target_pdf(self.session.cfg.calibration_target, path)
            self.result.setText(f"Saved {path}. Print it at 100% (\"Actual size\").")

    def on_frame(self, frame: Frame) -> None:
        self._view = detect_target(frame.image, self.session.cfg.calibration_target)
        if self._view is None:
            self.status.setText("Target not found. Is it in view, flat and in focus?")
            self.status.setStyleSheet(f"color: {WARN};")
        elif self._view.mirrored:
            self.status.setText("The image is mirrored left-right. Set flip_horizontal = true under [camera] in config/local.toml and restart.")
            self.status.setStyleSheet(f"color: {BAD};")
        else:
            self.status.setText(f"Target found: {len(self._view)} corners.")
            self.status.setStyleSheet(f"color: {GOOD};")
        self.capture_button.setEnabled(self._view is not None and not self._view.mirrored)

    def overlay(self, painter: QPainter) -> None:
        painter.setPen(_pen("#2da44e", 1.5))
        for view, radius in ((self._view, 5.0),):
            if view is not None:
                for x, y in view.corners_px:
                    painter.drawEllipse(QPointF(float(x), float(y)), radius, radius)

    def _capture(self) -> None:
        if self._view is not None:
            self._views.append(self._view)
            self.count_label.setText(f"{len(self._views)} views captured ({sum(len(v) for v in self._views)} corners)")

    def _clear(self) -> None:
        self._views.clear()
        self.count_label.setText("0 views captured")

    def _solve(self) -> None:
        if not self._views:
            self.result.setText("Capture at least one view of the target first.")
            return
        cfg = self.session.cfg
        try:
            scale = solve_scale(self._views, self._size, nominal_focal_px(cfg.camera.lens_focal_mm, cfg.camera.pixel_pitch_um),
                                target_scale_correction=self.bar_spin.value() / cfg.calibration_target.scale_bar_mm)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self.result.setText(f"Could not solve: {exc}")
            self.result.setStyleSheet(f"color: {BAD};")
            return
        self.session.calibration.scale = scale
        path = self.save()
        spread = (scale.px_per_in_max - scale.px_per_in_min) / scale.px_per_in * 100
        lines = [f"Scale: {scale.px_per_in:.1f} px/in at the centre ({scale.px_per_in_min:.1f} to {scale.px_per_in_max:.1f} across the view, {spread:.1f}% spread).",
                 f"Fit error: {scale.reprojection_rms_px:.2f} px ({scale.plane_rms_in * 1000:.1f} thousandths of an inch on the belt).",
                 f"View: {scale.image_width / scale.px_per_in:.2f} x {scale.image_height / scale.px_per_in:.2f} in.",
                 f"Saved to {path}."]
        if spread > 2.0:
            lines.insert(1, "The scale varies across the view by more than 2%: the camera is tilted relative to the belt. The mapping corrects for it, but squaring the camera is better.")
        if scale.reprojection_rms_px > 0.5:
            lines.insert(1, "The fit error is high (over 0.5 px). Check focus and that the target lies flat, then recapture.")
        self.result.setText("\n".join(lines))
        self.result.setStyleSheet(f"color: {GOOD if scale.reprojection_rms_px <= 0.5 else WARN};")


class BeltPanel(Panel):
    title = "Belt"
    interval_s = 0.25
    EMPTY_BELT_SECONDS = 45

    def __init__(self, session: Session) -> None:
        super().__init__(session)
        self.edges_label = _note("Looking for the belt edges…")
        save = QPushButton("Save belt edges to calibration")
        save.clicked.connect(self._save)
        self.result = _note()
        self.empty_button = QPushButton(f"Record empty moving belt ({self.EMPTY_BELT_SECONDS} s)")
        self.empty_button.clicked.connect(self._record_empty)
        self.empty_status = _note("Run the belt with nothing on it, then record. This captures three laps, used to learn "
                                  "the lighting pattern, the seam and the belt speed.")
        for widget in (QLabel("Belt edges (region of interest):"), self.edges_label, save, self.result):
            self.layout_.addWidget(widget)
        self.layout_.addSpacing(16)
        for widget in (QLabel("Empty-belt recording:"), self.empty_button, self.empty_status):
            self.layout_.addWidget(widget)
        self._edges: tuple[int, int] | None = None
        self._width = session.source.info.width
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._finish_empty)

    def on_frame(self, frame: Frame) -> None:
        cfg = self.session.cfg.roi
        if cfg.mode == "manual":
            self._edges = (cfg.top, cfg.bottom)
            self.edges_label.setText(f"Manual edges from config: rows {cfg.top} to {cfg.bottom}.")
        else:
            top, bottom, found = find_belt_edges(frame.image, cfg.margin_px)
            self._edges = (top, bottom)
            if found:
                self.edges_label.setText(f"Belt found between rows {top} and {bottom} ({bottom - top} px high, {cfg.margin_px} px margin inside each edge).")
                self.edges_label.setStyleSheet(f"color: {GOOD};")
            else:
                self.edges_label.setText("No dark band found above or below the belt, so the whole frame is used. "
                                         "If the belt edges should be visible, check the camera position; otherwise set manual edges in the config.")
                self.edges_label.setStyleSheet(f"color: {WARN};")
        if self.session.recording and self._timer.isActive():
            status = self.session.recording_status()
            self.empty_status.setText(f"Recording… {self._timer.remainingTime() / 1000:.0f} s left, {status['frames']} frames.")

    def overlay(self, painter: QPainter) -> None:
        if self._edges is not None:
            painter.setPen(_pen("#d4a72c", 2.0))
            for y in self._edges:
                painter.drawLine(QPointF(0, y), QPointF(self._width, y))

    def _save(self) -> None:
        if self._edges is None:
            return
        from ..calibration import RoiCalibration

        self.session.calibration.roi = RoiCalibration(top=self._edges[0], bottom=self._edges[1], left=0, right=self._width,
                                                      source=self.session.cfg.roi.mode)
        self.result.setText(f"Saved rows {self._edges[0]} to {self._edges[1]} to {self.save()}.")

    def _record_empty(self) -> None:
        if self.session.recording:
            self.empty_status.setText("A recording is already running; stop it first.")
            return
        self.session.start_recording("empty-belt", notes="empty moving belt for flat-field, seam and belt speed")
        self.empty_button.setEnabled(False)
        self._timer.start(self.EMPTY_BELT_SECONDS * 1000)

    def _finish_empty(self) -> None:
        summary = self.session.stop_recording()
        self.empty_button.setEnabled(True)
        lost = summary.get("not_recorded", 0) + summary.get("frames_missing_during_recording", 0)
        self.empty_status.setText(f"Saved {summary.get('frames', 0)} frames to {summary.get('path', '')}."
                                  + ("" if not lost else f" {lost} frames were lost."))
