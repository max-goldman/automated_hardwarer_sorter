"""Lossless frame recordings.

A recording is a folder:
    meta.json    camera mode, exposure, config and calibration snapshots, git commit, totals
    frames.bin   frames back to back, each zstd-compressed (lossless) 8-bit mono pixels
    index.bin    one fixed-size record per frame: seq, capture time, arrival time, offset, size

Decoded pixels are stored (not the camera's MJPG stream) so a recording replays to the
exact same pixels on every machine. The index is appended per frame, so a recording
cut short by a crash is still readable up to the last complete frame.
"""
from __future__ import annotations

import json
import queue
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import zstandard

from ..frames import Frame

FORMAT_VERSION = 1
INDEX_DTYPE = np.dtype(
    [("seq", "<u8"), ("t_capture_ns", "<i8"), ("t_arrival_ns", "<i8"), ("offset", "<u8"), ("size", "<u4"), ("flags", "<u4")]
)


def _git_commit(folder: Path) -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=folder, capture_output=True, text=True, timeout=2)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


class RecordingWriter:
    """Writes frames on its own thread so disk stalls cannot block capture.

    write() never blocks. If the internal queue is full the frame is not recorded and is
    counted in `dropped`, which is also written to meta.json.
    """

    def __init__(
        self,
        path: str | Path,
        width: int,
        height: int,
        fps: float,
        meta: dict[str, Any] | None = None,
        zstd_level: int = 1,
        queue_frames: int = 1200,
    ) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=False)
        self.width, self.height = width, height
        self._meta: dict[str, Any] = {
            "format_version": FORMAT_VERSION,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "width": width,
            "height": height,
            "fps": fps,
            "codec": "zstd",
            "git_commit": _git_commit(Path(__file__).resolve().parent),
            "complete": False,
            **(meta or {}),
        }
        self._write_meta()
        self._frames_fh = (self.path / "frames.bin").open("wb")
        self._index_fh = (self.path / "index.bin").open("wb")
        self._compressor = zstandard.ZstdCompressor(level=zstd_level)
        self._queue: queue.Queue[Frame | None] = queue.Queue(maxsize=queue_frames)
        self._offset = 0
        self.frames_written = 0
        self.bytes_written = 0
        self.dropped = 0
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="recorder", daemon=True)
        self._thread.start()

    def _write_meta(self) -> None:
        tmp = self.path / "meta.json.tmp"
        tmp.write_text(json.dumps(self._meta, indent=2, default=str))
        tmp.replace(self.path / "meta.json")

    def write(self, frame: Frame) -> bool:
        if frame.image.shape != (self.height, self.width) or frame.image.dtype != np.uint8:
            raise ValueError(f"frame is {frame.image.shape} {frame.image.dtype}, recording is {(self.height, self.width)} uint8")
        try:
            self._queue.put_nowait(frame)
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def _run(self) -> None:
        try:
            while True:
                frame = self._queue.get()
                if frame is None:
                    return
                blob = self._compressor.compress(np.ascontiguousarray(frame.image))
                self._frames_fh.write(blob)
                record = np.array(
                    [(frame.seq, frame.t_capture_ns, frame.t_arrival_ns, self._offset, len(blob), 0)], dtype=INDEX_DTYPE
                )
                self._index_fh.write(record.tobytes())
                self._offset += len(blob)
                self.frames_written += 1
                self.bytes_written = self._offset
        except BaseException as exc:  # noqa: BLE001 - surfaced in close()
            self._error = exc

    @property
    def backlog(self) -> int:
        return self._queue.qsize()

    def close(self, extra_meta: dict[str, Any] | None = None) -> None:
        self._queue.put(None)
        self._thread.join()
        self._frames_fh.close()
        self._index_fh.close()
        self._meta.update(
            {"complete": self._error is None, "frame_count": self.frames_written,
             "frames_not_recorded": self.dropped, "bytes": self.bytes_written, **(extra_meta or {})}
        )
        self._write_meta()
        if self._error is not None:
            raise RuntimeError(f"recording failed: {self._error}") from self._error


class RecordingReader:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        meta_file = self.path / "meta.json"
        if not meta_file.is_file():
            raise FileNotFoundError(f"not a recording (no meta.json): {self.path}")
        self.meta: dict[str, Any] = json.loads(meta_file.read_text())
        if self.meta.get("format_version") != FORMAT_VERSION:
            raise ValueError(f"unsupported recording format {self.meta.get('format_version')!r}")
        self.width, self.height, self.fps = int(self.meta["width"]), int(self.meta["height"]), float(self.meta["fps"])
        raw = (self.path / "index.bin").read_bytes()
        usable = len(raw) - len(raw) % INDEX_DTYPE.itemsize  # ignore a torn final record
        self.index = np.frombuffer(raw[:usable], dtype=INDEX_DTYPE)
        self._fh = (self.path / "frames.bin").open("rb")
        size = (self.path / "frames.bin").stat().st_size
        while len(self.index) and int(self.index[-1]["offset"]) + int(self.index[-1]["size"]) > size:
            self.index = self.index[:-1]  # frame data cut short
        self._decompressor = zstandard.ZstdDecompressor()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self.index)

    @property
    def duration_s(self) -> float:
        if len(self.index) < 2:
            return 0.0
        return float(self.index[-1]["t_capture_ns"] - self.index[0]["t_capture_ns"]) / 1e9

    def read(self, i: int) -> Frame:
        rec = self.index[i]
        with self._lock:
            self._fh.seek(int(rec["offset"]))
            blob = self._fh.read(int(rec["size"]))
        pixels = self._decompressor.decompress(blob, max_output_size=self.width * self.height)
        image = np.frombuffer(pixels, np.uint8).reshape(self.height, self.width)
        return Frame(int(rec["seq"]), int(rec["t_capture_ns"]), int(rec["t_arrival_ns"]), image)

    def __iter__(self) -> Iterator[Frame]:
        for i in range(len(self)):
            yield self.read(i)

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "RecordingReader":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
