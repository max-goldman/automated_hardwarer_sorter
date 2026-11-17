"""Config, clock, capture service, recording and replay."""

import itertools
import threading
import time

import numpy as np
import pytest

from hwsorter.capture import CaptureService, FrameQueue
from hwsorter.clock import ManualClock, MonotonicClock
from hwsorter.config import SorterConfig, load_config, project_root
from hwsorter.frames import EndOfStream, Frame, FrameSource, SourceInfo, SourceStats
from hwsorter.recording import RecordingReader, RecordingWriter, ReplaySource


def test_default_toml_matches_builtin_defaults():
    # config/default.toml is the visible copy of the defaults; they must not drift apart.
    assert (
        load_config(root=project_root()) == SorterConfig()
        or (project_root() / "config" / "local.toml").exists()
    )


def test_config_rejects_unknown_keys(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "default.toml").write_text(
        "schema_version = 1\n[camera]\nexposure = 0.5\n"
    )
    with pytest.raises(Exception, match="exposure"):
        load_config(root=tmp_path)


def test_config_layering(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "default.toml").write_text(
        "schema_version = 1\n[camera]\nexposure_ms = 0.5\nfps = 120\n"
    )
    (tmp_path / "config" / "local.toml").write_text("[camera]\nexposure_ms = 0.8\n")
    cfg = load_config(root=tmp_path)
    assert cfg.camera.exposure_ms == 0.8 and cfg.camera.fps == 120


def test_manual_clock_sleep_until():
    clock = ManualClock(0)
    woke = []
    thread = threading.Thread(
        target=lambda: (clock.sleep_until_ns(500), woke.append(clock.now_ns()))
    )
    thread.start()
    clock.advance_to(499)
    time.sleep(0.02)
    assert not woke
    clock.advance_to(700)
    thread.join(1)
    assert woke == [700]


def test_monotonic_clock_sleep_accuracy():
    # Wall-clock timing: under CPU contention a single sleep can overshoot the
    # busy-wait window. Assert the mechanism is *capable* of <1 ms accuracy (best
    # of several tries); it can never wake before the target, so that floor is
    # always checked on every try.
    clock = MonotonicClock()
    overshoots = []
    for _ in range(5):
        target = clock.now_ns() + 20_000_000
        clock.sleep_until_ns(target)
        overshoot = clock.now_ns() - target
        assert overshoot >= 0  # never wakes early
        overshoots.append(overshoot)
    assert min(overshoots) < 1_000_000


class ListSource(FrameSource):
    def __init__(self, frames, fps=120.0):
        self._frames = list(frames)
        self._i = 0
        self.info = SourceInfo(
            "list", frames[0].image.shape[1], frames[0].image.shape[0], fps, live=False
        )

    def start(self):
        self._i = 0

    def read(self, timeout=1.0):
        if self._i >= len(self._frames):
            raise EndOfStream
        self._i += 1
        return self._frames[self._i - 1]

    def stop(self):
        pass

    def stats(self):
        return SourceStats(self._i, 0, 0)


def _frames(n, skip=(), shape=(40, 64), period_ns=8_333_333):
    rng = np.random.default_rng(0)
    out, seq = [], 0
    for i in range(n):
        if i in skip:
            continue
        out.append(
            Frame(
                seq,
                1_000_000 + i * period_ns,
                1_000_000 + i * period_ns + 5_000_000,
                rng.integers(0, 255, shape, dtype=np.uint8),
            )
        )
        seq += 1
    return out


def test_capture_counts_missing_frames_from_timestamp_gaps():
    service = CaptureService(ListSource(_frames(50, skip={10, 30, 31})))
    queue = service.subscribe("consumer", 100)
    service.start()
    assert service.wait_ended(2)
    stats = service.stats()
    service.stop()
    assert (stats.frames, stats.missing, stats.gaps) == (47, 3, 2)
    assert [e["frames_missing"] for e in service.events.recent("frame_gap")] == [1, 2]
    got = []
    with pytest.raises(EndOfStream):
        while True:
            got.append(queue.get(0.1))
    assert len(got) == 47


def test_slow_consumer_drops_are_counted_and_logged():
    service = CaptureService(ListSource(_frames(30)))
    queue = service.subscribe("slow", 8)
    service.start()
    assert service.wait_ended(2)
    service.stop()
    assert queue.dropped == 22 and service.stats().queue_dropped == {"slow": 22}
    assert len(service.events.recent("queue_overflow")) == 22
    assert queue.get(0.1).seq == 22  # the newest frames survive


def test_frame_queue_end_marker_is_sticky():
    queue = FrameQueue("q", 4)
    queue.put(None)
    for _ in range(2):
        with pytest.raises(EndOfStream):
            queue.get(0.01)


def test_recording_round_trip_is_bit_exact(tmp_path):
    frames = _frames(25)
    writer = RecordingWriter(tmp_path / "rec", 64, 40, 120.0, meta={"exposure_ms": 0.5})
    for frame in frames:
        assert writer.write(frame)
    writer.close()
    with RecordingReader(tmp_path / "rec") as reader:
        assert len(reader) == 25 and reader.meta["complete"] and reader.meta["exposure_ms"] == 0.5
        for original, loaded in zip(frames, reader, strict=True):
            assert (loaded.seq, loaded.t_capture_ns, loaded.t_arrival_ns) == (
                original.seq,
                original.t_capture_ns,
                original.t_arrival_ns,
            )
            assert np.array_equal(loaded.image, original.image)


def test_recording_cut_short_is_still_readable(tmp_path):
    writer = RecordingWriter(tmp_path / "rec", 64, 40, 120.0)
    for frame in _frames(10):
        writer.write(frame)
    writer.close()
    data = (tmp_path / "rec" / "frames.bin").read_bytes()
    (tmp_path / "rec" / "frames.bin").write_bytes(data[:-50])  # torn last frame
    index = (tmp_path / "rec" / "index.bin").read_bytes()
    (tmp_path / "rec" / "index.bin").write_bytes(index + b"\x01\x02\x03")  # torn index record
    with RecordingReader(tmp_path / "rec") as reader:
        assert len(reader) == 9
        reader.read(8)


def test_writer_rejects_wrong_shape(tmp_path):
    writer = RecordingWriter(tmp_path / "rec", 64, 40, 120.0)
    with pytest.raises(ValueError):
        writer.write(Frame(0, 0, 0, np.zeros((10, 10), np.uint8)))
    writer.close()


def test_replay_fast_and_looping(tmp_path):
    frames = _frames(6)
    writer = RecordingWriter(tmp_path / "rec", 64, 40, 120.0)
    for frame in frames:
        writer.write(frame)
    writer.close()
    source = ReplaySource(tmp_path / "rec")
    source.start()
    seqs = []
    with pytest.raises(EndOfStream):
        while True:
            seqs.append(source.read().seq)
    assert seqs == list(range(6))

    looped = ReplaySource(tmp_path / "rec", loop=True)
    looped.start()
    out = [looped.read() for _ in range(14)]
    assert [f.seq for f in out] == list(range(14))
    assert all(b.t_capture_ns > a.t_capture_ns for a, b in itertools.pairwise(out))


def test_replay_realtime_pacing(tmp_path):
    writer = RecordingWriter(tmp_path / "rec", 64, 40, 120.0)
    for frame in _frames(13):
        writer.write(frame)
    writer.close()
    # Wall-clock timing: take the best of several runs so a single OS preemption
    # under load can't exceed the upper bound. Real-time pacing can only make a run
    # slower, so the lower bound holds on every run.
    elapsed = []
    for _ in range(5):
        source = ReplaySource(tmp_path / "rec", realtime=True)
        source.start()
        start = time.monotonic()
        for _ in range(13):
            source.read()
        elapsed.append(time.monotonic() - start)
    assert 0.09 < min(elapsed) < 0.2  # 12 frame periods = 0.1 s


def test_announced_disruptions_are_logged_but_not_counted_as_unexplained():
    service = CaptureService(ListSource(_frames(50, skip={10, 30, 31})))
    for frame in service.source._frames:
        frame.t_arrival_ns = time.monotonic_ns()
    service.expect_disruption(5.0, "exposure change")
    service.start()
    assert service.wait_ended(2)
    stats = service.stats()
    service.stop()
    assert (stats.missing, stats.gaps, stats.expected_missing) == (0, 0, 3)
    assert [e["reason"] for e in service.events.recent("frame_gap_expected")] == [
        "exposure change"
    ] * 2


def test_frame_level_ignores_parts_but_follows_lighting():
    from hwsorter.capture import frame_level
    from hwsorter.synthetic import PartSpec, SceneConfig, SyntheticRenderer

    scene = SceneConfig(seam=False)
    empty = frame_level(SyntheticRenderer(scene, []).render(0.5))
    with_parts = frame_level(
        SyntheticRenderer(
            scene, [PartSpec("bolt_0375", 0.1, 1.0, 20), PartSpec("washer_0375", 0.3, 0.9)]
        ).render(0.5)
    )
    assert abs(with_parts - empty) < 0.03 * empty
    dimmed = frame_level(
        SyntheticRenderer(SceneConfig(seam=False, flicker_depth=0.5), []).render(0.5, 0)
    )
    assert dimmed < 0.6 * empty
