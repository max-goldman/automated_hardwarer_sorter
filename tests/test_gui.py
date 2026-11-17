"""The Qt app, driven off-screen: every panel runs on synthetic frames and on a recorded target view."""

import os
import shutil
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from hwsorter.config import load_config, project_root
from hwsorter.frames import Frame
from hwsorter.recording import RecordingReader, RecordingWriter

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A throwaway project root so tests never touch real recordings or calibration."""
    (tmp_path / "config").mkdir()
    shutil.copy(project_root() / "config" / "default.toml", tmp_path / "config" / "default.toml")
    monkeypatch.setenv("HWSORTER_ROOT", str(tmp_path))
    monkeypatch.setenv("HWSORTER_MACHINE", "testbench")
    return tmp_path


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(["test"])


def pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def open_window(source):
    from hwsorter.gui.app import MainWindow
    from hwsorter.session import Session

    session = Session(load_config(), source, realtime=True, loop=True)
    session.start()
    window = MainWindow(session)
    window.show()
    return window, session


def test_all_panels_run_on_synthetic_frames(sandbox, app):
    window, _session = open_window("synthetic")
    try:
        for index, _panel in enumerate(window.panels):
            window.tabs.setCurrentIndex(index)
            pump(app, 0.6)
            assert not window.grab().isNull()
        live, exposure, _focus, scale, belt = window.panels
        assert (
            "synthetic" in live.fields["Source"].text()
            and "fps" in live.fields["Frame rate"].text()
        )
        assert (
            "px at" in exposure.fields["Motion blur"].text()
            and "0.5 ms" in exposure.fields["Recommended"].text()
        )
        assert "Target not found" in scale.status.text()
        assert "Belt found between rows" in belt.edges_label.text()
        # Record through the Live panel and check the result on disk.
        window.tabs.setCurrentIndex(0)
        live.name_edit.setText("gui test")
        live.record_button.click()
        pump(app, 0.5)
        live.record_button.click()
        assert "No frames lost" in live.record_status.text()
        folders = list((sandbox / "recordings").iterdir())
        assert len(folders) == 1 and folders[0].name.endswith("gui-test")
        with RecordingReader(folders[0]) as reader:
            assert (
                len(reader) > 10
                and reader.meta["complete"]
                and reader.meta["config"]["camera"]["width"] == 1280
            )
        # Saving belt edges writes the calibration file for this machine.
        window.tabs.setCurrentIndex(4)
        pump(app, 0.4)
        belt._save()
        from hwsorter.calibration import load_calibration

        roi = load_calibration().roi
        assert roi is not None and 40 < roi.top < 70 and 760 < roi.bottom < 790
    finally:
        window.close()


def test_scale_panel_solves_from_a_recorded_target_view(sandbox, app):
    from test_calibration import PPI, camera_view_of_board

    images = [camera_view_of_board(a, s) for a, s in ((2.0, (10, -6)), (-12.0, (-15, 8)))]
    writer = RecordingWriter(sandbox / "target-rec", 1280, 800, 120.0)
    for i in range(240):
        writer.write(Frame(i, i * 8_333_333, i * 8_333_333, images[(i // 120) % 2]))
    writer.close()

    window, _session = open_window(str(sandbox / "target-rec"))
    try:
        scale = window.panels[3]
        window.tabs.setCurrentIndex(3)
        for _ in range(2):  # the recording shows each target position for one second
            pump(app, 0.6)
            assert "Target found" in scale.status.text()
            scale._capture()
            pump(app, 0.5)
        scale._solve()
        assert "Saved to" in scale.result.text()
        from hwsorter.calibration import load_calibration

        saved = load_calibration().scale
        assert (
            saved is not None
            and saved.views == 2
            and saved.px_per_in == pytest.approx(PPI, rel=0.004)
        )
        # Focus panel on the same sharp target.
        window.tabs.setCurrentIndex(2)
        pump(app, 0.6)
        focus = window.panels[2]
        assert focus._width is not None and focus._width < 4.0
    finally:
        window.close()


def _fake_snapshot():
    import numpy as np

    from hwsorter.contracts import Output, TrackFlag, Verdict
    from hwsorter.pipeline import GapView, PendingSend, PipelineSnapshot, TrackView

    def track(i, x, rework):
        outline = np.array([[x, 300], [x + 60, 300], [x + 60, 360], [x, 360]], dtype=np.int32)
        return TrackView(
            i, outline, 1.0 * i, 1.5 * i, TrackFlag.FEW_FRAMES, 4,
            Output.NUT_025, Verdict.CONFIDENT, 0.2, rework,
        )  # fmt: skip

    return PipelineSnapshot(
        seq=1,
        t_capture_ns=0,
        processed=True,
        tracks=(track(1, 200, True), track(2, 320, True)),
        gaps=(GapView(1, 2, 0.3, True),),
        pending=(PendingSend(7, Output.REWORK, 1, 2.5),),
        fps=118.0,
        n_dropped=1,
        latency_ms=4.2,
        n_parts=2,
        n_groups=1,
    )


def test_debug_overlay_toggle_and_snapshot(sandbox, app):
    window, _session = open_window("synthetic")
    try:
        snap = _fake_snapshot()
        window.debug.source = lambda: snap
        pump(app, 0.3)
        assert window.debug.track_count == 0 and not window.debug.hud_lines()  # off: draws nothing
        window.debug_check.setChecked(True)
        pump(app, 0.3)
        assert window.debug.track_count == 2
        ((group, out, remaining),) = window.debug.countdowns()
        assert group == 7 and out == "rework" and 0 < remaining <= 2.5
        assert any("118.0 fps" in line and "dropped 1" in line for line in window.debug.hud_lines())
        assert not window.grab().isNull()  # renders without error
        window.debug_check.setChecked(False)
        pump(app, 0.2)
        assert window.debug.track_count == 0 and window.debug.snapshot is None
        # No pipeline attached: enabling shows nothing and the live view is unaffected.
        window.debug.source = lambda: None
        window.debug_check.setChecked(True)
        pump(app, 0.3)
        assert not window.debug.active and not window.grab().isNull()
    finally:
        window.close()


def test_debug_overlay_overhead(sandbox, app):
    """Display rate with the overlay on vs off on a synthetic run (printed; must not collapse)."""
    window, _session = open_window("synthetic")
    try:
        snap = _fake_snapshot()
        window.debug.source = lambda: snap
        rates = {}
        for on in (False, True):
            window.debug_check.setChecked(on)
            pump(app, 0.3)
            n0, t0 = window.video.paint_count, time.monotonic()
            pump(app, 1.5)
            rates[on] = (window.video.paint_count - n0) / (time.monotonic() - t0)
        print(f"display updates/s: overlay off {rates[False]:.1f}, on {rates[True]:.1f}")
        assert rates[True] > 0.7 * rates[False]
    finally:
        window.close()
