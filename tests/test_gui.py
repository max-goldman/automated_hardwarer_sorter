"""The Qt app, driven off-screen: every panel runs on synthetic frames and on a recorded target view."""
import os
import shutil
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from hwsorter.config import load_config, project_root  # noqa: E402
from hwsorter.frames import Frame  # noqa: E402
from hwsorter.recording import RecordingReader, RecordingWriter  # noqa: E402

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
    window, session = open_window("synthetic")
    try:
        for index, panel in enumerate(window.panels):
            window.tabs.setCurrentIndex(index)
            pump(app, 0.6)
            assert not window.grab().isNull()
        live, exposure, focus, scale, belt = window.panels
        assert "synthetic" in live.fields["Source"].text() and "fps" in live.fields["Frame rate"].text()
        assert "px at" in exposure.fields["Motion blur"].text() and "0.5 ms" in exposure.fields["Recommended"].text()
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
            assert len(reader) > 10 and reader.meta["complete"] and reader.meta["config"]["camera"]["width"] == 1280
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

    window, session = open_window(str(sandbox / "target-rec"))
    try:
        scale = window.panels[3]
        window.tabs.setCurrentIndex(3)
        for _ in range(2):                       # the recording shows each target position for one second
            pump(app, 0.6)
            assert "Target found" in scale.status.text()
            scale._capture()
            pump(app, 0.5)
        scale._solve()
        assert "Saved to" in scale.result.text()
        from hwsorter.calibration import load_calibration

        saved = load_calibration().scale
        assert saved is not None and saved.views == 2 and saved.px_per_in == pytest.approx(PPI, rel=0.004)
        # Focus panel on the same sharp target.
        window.tabs.setCurrentIndex(2)
        pump(app, 0.6)
        focus = window.panels[2]
        assert focus._width is not None and focus._width < 4.0
    finally:
        window.close()
