"""macOS live camera through AVFoundation (PyObjC).

Chosen over OpenCV and PyAV after measurement: it delivers 1280 x 800 at 120 fps with
driver timestamps on the same host clock as time.monotonic(), and hands us the luma
plane directly (the camera is mono), costing about 0.1 ms per frame.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

import numpy as np

from ..config import CameraConfig
from ..frames import Frame, FrameSource, SourceInfo, SourceStats
from .base import CameraError

log = logging.getLogger(__name__)

_PIXEL_FORMAT_420F = 0x34323066  # '420f': bi-planar 4:2:0, full range; plane 0 is luma


def _frameworks():
    import AVFoundation as AVF
    import CoreMedia
    import dispatch
    import objc
    from Foundation import NSObject
    from Quartz import CoreVideo

    return AVF, CoreMedia, CoreVideo, dispatch, objc, NSObject


def _model_tag(vendor_id: int, product_id: int) -> str:
    return f"VendorID_{vendor_id} ProductID_{product_id}"


def _find_device(vendor_id: int, product_id: int):
    AVF, *_ = _frameworks()
    session = AVF.AVCaptureDeviceDiscoverySession.discoverySessionWithDeviceTypes_mediaType_position_(
        [AVF.AVCaptureDeviceTypeExternal], AVF.AVMediaTypeVideo, AVF.AVCaptureDevicePositionUnspecified
    )
    tag = _model_tag(vendor_id, product_id)
    for device in session.devices():
        if tag in str(device.modelID()):
            return device
    return None


def list_modes(vendor_id: int, product_id: int) -> list[dict[str, Any]]:
    """Every (size, pixel format, frame rates) the camera advertises."""
    _, CoreMedia, *_ = _frameworks()
    device = _find_device(vendor_id, product_id)
    if device is None:
        raise CameraError(f"camera {vendor_id:04x}:{product_id:04x} not found")
    modes = []
    for fmt in device.formats():
        desc = fmt.formatDescription()
        dim = CoreMedia.CMVideoFormatDescriptionGetDimensions(desc)
        fourcc = int(CoreMedia.CMFormatDescriptionGetMediaSubType(desc)).to_bytes(4, "big").decode("ascii", "replace")
        rates = sorted({round(r.maxFrameRate(), 2) for r in fmt.videoSupportedFrameRateRanges()}, reverse=True)
        modes.append({"width": int(dim.width), "height": int(dim.height), "format": fourcc, "fps": rates})
    return modes


def _ensure_camera_access() -> None:
    AVF, *_ = _frameworks()
    status = AVF.AVCaptureDevice.authorizationStatusForMediaType_(AVF.AVMediaTypeVideo)
    if status == 3:  # authorized
        return
    if status == 0:  # not determined: ask once
        done = threading.Event()
        result = {}

        def handler(granted: bool) -> None:
            result["granted"] = bool(granted)
            done.set()

        AVF.AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVF.AVMediaTypeVideo, handler)
        done.wait(120)
        if result.get("granted"):
            return
    raise CameraError(
        "camera access is not allowed for this app. Enable it in System Settings > "
        "Privacy & Security > Camera for the terminal you run the sorter from."
    )


_delegate_class = None


def _get_delegate_class():
    """Define the Objective-C delegate class once (PyObjC forbids redefinition)."""
    global _delegate_class
    if _delegate_class is not None:
        return _delegate_class
    _, CoreMedia, CV, _, objc, NSObject = _frameworks()

    class HWSorterFrameDelegate(NSObject):
        def initWithOwner_(self, owner):
            self = objc.super(HWSorterFrameDelegate, self).init()
            if self is None:
                return None
            self._owner = owner
            return self

        def captureOutput_didOutputSampleBuffer_fromConnection_(self, output, sample_buffer, connection):
            arrival_ns = time.monotonic_ns()
            owner = self._owner
            t = CoreMedia.CMSampleBufferGetPresentationTimeStamp(sample_buffer)
            pts_ns = int(t.value) * 1_000_000_000 // int(t.timescale) if t.timescale else arrival_ns
            pixel_buffer = CoreMedia.CMSampleBufferGetImageBuffer(sample_buffer)
            if pixel_buffer is None:
                return
            CV.CVPixelBufferLockBaseAddress(pixel_buffer, CV.kCVPixelBufferLock_ReadOnly)
            try:
                height = CV.CVPixelBufferGetHeightOfPlane(pixel_buffer, 0)
                width = CV.CVPixelBufferGetWidthOfPlane(pixel_buffer, 0)
                stride = CV.CVPixelBufferGetBytesPerRowOfPlane(pixel_buffer, 0)
                base = CV.CVPixelBufferGetBaseAddressOfPlane(pixel_buffer, 0)
                view = np.frombuffer(base.as_buffer(stride * height), np.uint8).reshape(height, stride)
                image = view[:: owner._step_y, :width][:, :: owner._step_x].copy()  # copy: buffer is recycled
            finally:
                CV.CVPixelBufferUnlockBaseAddress(pixel_buffer, CV.kCVPixelBufferLock_ReadOnly)
            owner._push(pts_ns, arrival_ns, image)

        def captureOutput_didDropSampleBuffer_fromConnection_(self, output, sample_buffer, connection):
            self._owner._driver_drop()

    _delegate_class = HWSorterFrameDelegate
    return _delegate_class


class AVFoundationSource(FrameSource):
    def __init__(self, cfg: CameraConfig) -> None:
        self._cfg = cfg
        self._step_x = -1 if cfg.flip_horizontal else 1
        self._step_y = -1 if cfg.flip_vertical else 1
        self._queue: deque[Frame] = deque()
        self._cond = threading.Condition()
        self._stats = SourceStats()
        self._seq = 0
        self._session = None
        self._device = None
        self._delegate = None
        self._dispatch_queue = None
        self._use_arrival_time = False
        self._running = False
        self.info = SourceInfo(
            name="camera", width=cfg.width, height=cfg.height, fps=cfg.fps, live=True,
            details={"backend": "avfoundation", "timestamps": "driver",
                     "flip_horizontal": cfg.flip_horizontal, "flip_vertical": cfg.flip_vertical},
        )

    # -- called on the capture dispatch queue ----------------------------------------
    def _push(self, pts_ns: int, arrival_ns: int, image: np.ndarray) -> None:
        if not self._use_arrival_time and not (0 <= arrival_ns - pts_ns < 500_000_000):
            # Driver timestamps should trail arrival by a few ms on the same clock. If not,
            # fall back to arrival time rather than use a timeline we cannot trust.
            log.warning("camera timestamps are not on the monotonic clock; using arrival times")
            self._use_arrival_time = True
            self.info.details["timestamps"] = "arrival"
        with self._cond:
            frame = Frame(self._seq, arrival_ns if self._use_arrival_time else pts_ns, arrival_ns, image)
            self._seq += 1
            if len(self._queue) >= self._cfg.queue_frames:
                self._queue.popleft()
                self._stats.queue_dropped += 1
            self._queue.append(frame)
            self._cond.notify()

    def _driver_drop(self) -> None:
        with self._cond:
            self._stats.driver_dropped += 1

    # -- FrameSource ------------------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        AVF, CoreMedia, CV, dispatch, _, _ = _frameworks()
        _ensure_camera_access()
        cfg = self._cfg
        device = _find_device(cfg.usb_vendor_id, cfg.usb_product_id)
        if device is None:
            raise CameraError(
                f"camera {cfg.usb_vendor_id:04x}:{cfg.usb_product_id:04x} not found. Is it plugged in?"
            )
        chosen = None
        for fmt in device.formats():
            dim = CoreMedia.CMVideoFormatDescriptionGetDimensions(fmt.formatDescription())
            if (dim.width, dim.height) != (cfg.width, cfg.height):
                continue
            for rate in fmt.videoSupportedFrameRateRanges():
                if abs(rate.maxFrameRate() - cfg.fps) < 1.0:
                    chosen = (fmt, rate)
        if chosen is None:
            modes = ", ".join(f"{m['width']}x{m['height']}@{m['fps'][0]:g}" for m in list_modes(cfg.usb_vendor_id, cfg.usb_product_id))
            raise CameraError(f"camera has no {cfg.width}x{cfg.height} at {cfg.fps:g} fps mode. Available: {modes}")
        fmt, rate = chosen

        session = AVF.AVCaptureSession.alloc().init()
        device_input, error = AVF.AVCaptureDeviceInput.deviceInputWithDevice_error_(device, None)
        if device_input is None:
            raise CameraError(f"cannot open camera: {error}")
        output = AVF.AVCaptureVideoDataOutput.alloc().init()
        output.setAlwaysDiscardsLateVideoFrames_(False)
        output.setVideoSettings_({str(CV.kCVPixelBufferPixelFormatTypeKey): _PIXEL_FORMAT_420F})
        self._delegate = _get_delegate_class().alloc().initWithOwner_(self)
        self._dispatch_queue = dispatch.dispatch_queue_create(b"hwsorter.capture", None)
        output.setSampleBufferDelegate_queue_(self._delegate, self._dispatch_queue)
        session.beginConfiguration()
        session.addInput_(device_input)
        session.addOutput_(output)
        session.commitConfiguration()
        ok, error = device.lockForConfiguration_(None)
        if not ok:
            raise CameraError(f"cannot configure camera: {error}")
        device.setActiveFormat_(fmt)
        device.setActiveVideoMinFrameDuration_(rate.minFrameDuration())
        device.setActiveVideoMaxFrameDuration_(rate.minFrameDuration())
        session.startRunning()
        self._session, self._device = session, device
        self.info.fps = float(rate.maxFrameRate())
        self._running = True

    def read(self, timeout: float = 1.0) -> Frame | None:
        with self._cond:
            if not self._queue:
                self._cond.wait(timeout)
            if not self._queue:
                return None
            self._stats.delivered += 1
            return self._queue.popleft()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._session.stopRunning()
        self._device.unlockForConfiguration()
        self._session = None

    def stats(self) -> SourceStats:
        with self._cond:
            return SourceStats(self._stats.delivered, self._stats.driver_dropped, self._stats.queue_dropped)
