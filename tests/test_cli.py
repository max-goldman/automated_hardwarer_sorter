import logging

import numpy as np

from hwsorter import calibration
from hwsorter.cli import main
from hwsorter.frames import Frame
from hwsorter.recording import RecordingWriter


def test_calibrate_flatfield_saves_map(tmp_path, monkeypatch):
    monkeypatch.setenv("HWSORTER_ROOT", str(tmp_path))
    monkeypatch.setenv("HWSORTER_MACHINE", "testbench")
    (tmp_path / "config").mkdir()
    ramp = np.tile(np.linspace(100, 200, 64, dtype=np.uint8), (40, 1))
    writer = RecordingWriter(tmp_path / "empty", 64, 40, 120.0)
    for i in range(10):
        frame = Frame(
            image=ramp.copy(), t_capture_ns=i * 8_333_333, t_arrival_ns=i * 8_333_333, seq=i
        )
        assert writer.write(frame)
    writer.close()

    try:
        assert main(["calibrate", "flatfield", str(tmp_path / "empty")]) == 0
    finally:  # main() installs a root log handler; do not leak it into other tests
        logging.getLogger().handlers.clear()
        logging.getLogger().setLevel(logging.WARNING)

    cal = calibration.load_calibration(root=tmp_path)
    assert cal.flatfield_file
    flat = np.load(calibration.calibration_path(root=tmp_path).parent / cal.flatfield_file)
    assert flat.shape == (40, 64)
    assert abs(float(flat[20, 32]) - float(ramp[20, 32])) < 5
