"""A running capture session: source + camera control + capture service + optional recorder.

Shared by the command-line tools and the GUI. No Qt here.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .calibration import Calibration, load_calibration
from .camera import CameraControl, NullCameraControl, open_camera
from .capture import CaptureService, FrameQueue
from .config import SorterConfig, project_root
from .eventlog import EventLog
from .frames import EndOfStream, FrameSource
from .recording import RecordingWriter, ReplaySource


def open_source(
    cfg: SorterConfig, spec: str = "camera", realtime: bool = True, loop: bool = False
) -> tuple[FrameSource, CameraControl]:
    """spec: "camera", "synthetic", or the path of a recording."""
    if spec == "camera":
        return open_camera(cfg.camera)
    if spec == "synthetic":
        from .synthetic import SceneConfig, SyntheticSource

        scene = SceneConfig(
            width=cfg.camera.width,
            height=cfg.camera.height,
            fps=cfg.camera.fps,
            speed_in_s=cfg.belt.speed_in_s,
            loop_length_in=cfg.belt.loop_length_in,
        )
        return SyntheticSource(scene, realtime=realtime), NullCameraControl(cfg.camera.exposure_ms)
    path = Path(spec)
    if not path.is_absolute() and not path.exists():
        path = recordings_dir(cfg) / spec
    source = ReplaySource(path, realtime=realtime, loop=loop)
    return source, NullCameraControl(
        float(source.reader.meta.get("exposure_ms") or cfg.camera.exposure_ms)
    )


def recordings_dir(cfg: SorterConfig) -> Path:
    folder = Path(cfg.recording.dir)
    return folder if folder.is_absolute() else project_root() / folder


class Session:
    def __init__(
        self,
        cfg: SorterConfig,
        source_spec: str = "camera",
        realtime: bool = True,
        loop: bool = False,
        event_log: EventLog | None = None,
        source: tuple[FrameSource, CameraControl] | None = None,
    ) -> None:
        """``source``: a ready (source, control) pair used instead of opening ``source_spec``."""
        self.cfg = cfg
        self.source_spec = source_spec
        self.events = event_log or EventLog()
        self.source, self.control = source or open_source(
            cfg, source_spec, realtime=realtime, loop=loop
        )
        self.capture = CaptureService(self.source, self.events)
        self.calibration: Calibration = load_calibration()
        self.exposure_ms: float | None = None
        self._writer: RecordingWriter | None = None
        self._rec_queue: FrameQueue | None = None
        self._rec_thread: threading.Thread | None = None
        self._rec_stop = threading.Event()
        self._rec_baseline: tuple[int, int] = (0, 0)
        self.recording_path: Path | None = None
        self._runner: Any = None

    # -- lifecycle ------------------------------------------------------------------
    def start(self) -> None:
        self.capture.start()
        if self.source.info.live:
            self.set_exposure(self.cfg.camera.exposure_ms)
        else:
            self.exposure_ms = self.control.get_exposure_ms()

    def start_pipeline(self, pipeline: Any) -> Any:
        """Plug a Pipeline in as a capture consumer (own bounded queue and thread)."""
        from .pipeline import PipelineRunner

        queue = self.capture.subscribe("pipeline", self.cfg.pipeline.queue_frames)
        self._runner = PipelineRunner(pipeline, queue)
        self._runner.start()
        return self._runner

    def stop_pipeline(self, wait_s: float = 0.0) -> None:
        if self._runner is not None:
            self._runner.stop(wait_s)
            self.capture.unsubscribe(self._runner.queue)
            self._runner = None

    def stop(self) -> None:
        if self.recording:
            self.stop_recording()
        self.stop_pipeline()
        self.capture.stop()
        self.control.close()

    def set_exposure(self, exposure_ms: float) -> float:
        self.capture.expect_disruption(1.0, "exposure change")  # the camera's stream stalls briefly
        self.exposure_ms = self.control.set_manual_exposure(exposure_ms)
        self.events.log("exposure_set", exposure_ms=self.exposure_ms)
        return self.exposure_ms

    # -- recording ------------------------------------------------------------------
    @property
    def recording(self) -> bool:
        return self._writer is not None

    def start_recording(self, name: str = "", notes: str = "") -> Path:
        if self.recording:
            raise RuntimeError("already recording")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")
        path = recordings_dir(self.cfg) / (f"{stamp}-{slug}" if slug else stamp)
        info = self.source.info
        meta: dict[str, Any] = {
            "name": name,
            "notes": notes,
            "source": info.name,
            "source_details": _jsonable(info.details),
            "exposure_ms": self.exposure_ms,
            "config": self.cfg.model_dump(),
            "calibration": self.calibration.model_dump(),
        }
        self._writer = RecordingWriter(
            path,
            info.width,
            info.height,
            info.fps,
            meta=meta,
            zstd_level=self.cfg.recording.zstd_level,
            queue_frames=self.cfg.recording.queue_frames,
        )
        self._rec_queue = self.capture.subscribe("recorder", self.cfg.recording.queue_frames)
        stats = self.capture.stats()
        self._rec_baseline = (
            stats.missing,
            stats.source.driver_dropped + stats.source.queue_dropped,
        )
        self._rec_stop.clear()
        self._rec_thread = threading.Thread(
            target=self._record_loop, name="record-feed", daemon=True
        )
        self._rec_thread.start()
        self.recording_path = path
        self.events.log("recording_start", path=str(path))
        return path

    def _record_loop(self) -> None:
        assert self._writer is not None and self._rec_queue is not None
        while not self._rec_stop.is_set():
            try:
                frame = self._rec_queue.get(timeout=0.1)
            except EndOfStream:
                return
            if frame is not None:
                self._writer.write(frame)

    def recording_status(self) -> dict[str, Any]:
        if self._writer is None:
            return {}
        return {
            "frames": self._writer.frames_written,
            "megabytes": self._writer.bytes_written / 1e6,
            "backlog": self._writer.backlog,
            "not_recorded": self._writer.dropped
            + (self._rec_queue.dropped if self._rec_queue is not None else 0),
        }

    def drain_recording(self, timeout_s: float = 5.0) -> None:
        """End of a finite source: let the recorder feed thread write its backlog."""
        if self._rec_thread is not None:
            self._rec_thread.join(timeout=timeout_s)

    def stop_recording(self) -> dict[str, Any]:
        # FrameQueue has a length, so an empty queue is falsy: compare with None explicitly.
        if self._writer is None or self._rec_queue is None or self._rec_thread is None:
            return {}
        self._rec_stop.set()
        self._rec_thread.join(timeout=2.0)
        self.capture.unsubscribe(self._rec_queue)
        stats = self.capture.stats()
        summary = {
            "frames_missing_during_recording": stats.missing - self._rec_baseline[0],
            "source_drops_during_recording": stats.source.driver_dropped
            + stats.source.queue_dropped
            - self._rec_baseline[1],
            "feed_queue_dropped": self._rec_queue.dropped,
        }
        writer, self._writer = self._writer, None
        writer.close(summary)
        summary.update(
            {
                "path": str(writer.path),
                "frames": writer.frames_written,
                "megabytes": round(writer.bytes_written / 1e6, 1),
                "not_recorded": writer.dropped + self._rec_queue.dropped,
            }
        )
        self.events.log("recording_stop", **summary)
        self._rec_queue = None
        return summary


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): _jsonable(v) for k, v in value.items() if k not in ("config", "calibration")
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value if isinstance(value, (str, int, float, bool, type(None))) else str(value)
