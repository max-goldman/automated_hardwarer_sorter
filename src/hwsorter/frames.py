"""Frame type and the FrameSource interface shared by live, replay and synthetic sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(slots=True)
class Frame:
    """One mono image with its timing.

    seq           index assigned by the source, contiguous from 0 for delivered frames
    t_capture_ns  best estimate of when the frame was taken (driver timestamp)
    t_arrival_ns  when our code received it (monotonic clock)
    image         uint8 array, shape (height, width)
    """

    seq: int
    t_capture_ns: int
    t_arrival_ns: int
    image: np.ndarray


@dataclass
class SourceInfo:
    name: str
    width: int
    height: int
    fps: float
    live: bool
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceStats:
    delivered: int = 0  # frames handed to the reader
    driver_dropped: int = 0  # drops the driver reported
    queue_dropped: int = 0  # frames discarded because the reader fell behind


class EndOfStream(Exception):
    """Raised by FrameSource.read when a finite source (a recording) is exhausted."""


class FrameSource(ABC):
    """A stream of frames. read() blocks up to `timeout` seconds and returns None on timeout."""

    info: SourceInfo

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def read(self, timeout: float = 1.0) -> Frame | None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def stats(self) -> SourceStats: ...

    def __enter__(self) -> FrameSource:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
