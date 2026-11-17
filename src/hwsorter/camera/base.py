"""Camera control interface and platform dispatch.

Platform-specific code (how frames are grabbed, how exposure is set) stays behind
FrameSource and CameraControl so the rest of the program is identical everywhere.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from typing import Any

from ..config import CameraConfig
from ..frames import FrameSource


class CameraError(RuntimeError):
    pass


class CameraControl(ABC):
    """Manual exposure control. Exposure is in milliseconds."""

    @abstractmethod
    def set_manual_exposure(self, exposure_ms: float) -> float:
        """Switch to manual exposure and set it. Returns the value the camera reports back."""

    @abstractmethod
    def get_exposure_ms(self) -> float: ...

    @abstractmethod
    def set_auto_exposure(self) -> None: ...

    @abstractmethod
    def exposure_limits_ms(self) -> tuple[float, float, float]:
        """(minimum, maximum, step) in milliseconds."""

    @abstractmethod
    def describe(self) -> dict[str, Any]: ...

    def close(self) -> None:  # noqa: B027 - optional hook
        pass


class NullCameraControl(CameraControl):
    """For sources with no camera behind them (recordings, synthetic frames)."""

    def __init__(self, exposure_ms: float = 0.5) -> None:
        self._exposure_ms = exposure_ms

    def set_manual_exposure(self, exposure_ms: float) -> float:
        self._exposure_ms = exposure_ms
        return exposure_ms

    def get_exposure_ms(self) -> float:
        return self._exposure_ms

    def set_auto_exposure(self) -> None:
        pass

    def exposure_limits_ms(self) -> tuple[float, float, float]:
        return (0.1, 500.0, 0.1)

    def describe(self) -> dict[str, Any]:
        return {"backend": "none", "exposure_ms": self._exposure_ms}


def resolve_backend(cfg: CameraConfig) -> str:
    if cfg.backend != "auto":
        return cfg.backend
    if sys.platform == "darwin":
        return "avfoundation"
    if sys.platform.startswith("linux"):
        return "v4l2"
    raise CameraError(f"no camera backend for platform {sys.platform!r}")


def open_camera(cfg: CameraConfig) -> tuple[FrameSource, CameraControl]:
    """Open the live camera and its control channel for this platform."""
    backend = resolve_backend(cfg)
    if backend == "avfoundation":
        from .avfoundation import AVFoundationSource
        from .uvc import UvcControl

        source = AVFoundationSource(cfg)
        try:
            control: CameraControl = UvcControl(cfg.usb_vendor_id, cfg.usb_product_id)
        except CameraError:
            source.stop()
            raise
        return source, control
    if backend == "v4l2":
        raise CameraError(
            "the Linux V4L2 camera backend is planned for milestone 5 and is not built yet"
        )
    raise CameraError(f"unknown camera backend {backend!r}")
