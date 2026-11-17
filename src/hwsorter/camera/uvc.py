"""UVC exposure control over USB control requests (used on macOS).

macOS's camera API offers only "locked" and "auto" exposure for this camera, so a fixed
exposure is set by sending standard UVC class requests to the camera's control endpoint
with libusb. This needs no admin rights and works while AVFoundation is streaming,
because requests on endpoint 0 do not require claiming the video interface.

Measured on the Innomaker U20CAM-9281M (Sonix 0c45:636d):
  * exposure: 0.1 ms units, 0.2 ms is the shortest usable value (0.1 ms reads back 0)
  * the first exposure write straight after switching to manual can be ignored,
    so every write is verified by read-back and retried
  * the gain control accepts values but does not change the image
"""

from __future__ import annotations

import struct
import threading
import time
from typing import Any

from .base import CameraControl, CameraError

# UVC class-specific requests
_SET_CUR, _GET_CUR, _GET_MIN, _GET_MAX, _GET_RES, _GET_DEF = 0x01, 0x81, 0x82, 0x83, 0x84, 0x87
# Control selectors
_CT_AE_MODE, _CT_EXPOSURE_ABS = 0x02, 0x04
_PU_GAIN = 0x04
# Auto-exposure modes (bitmask values from the UVC spec)
AE_MANUAL, AE_APERTURE_PRIORITY = 1, 8
_EXPOSURE_UNIT_MS = 0.1
_MIN_USABLE_UNITS = 2


class UvcControl(CameraControl):
    def __init__(self, vendor_id: int, product_id: int) -> None:
        try:
            import libusb_package
            import usb.core
        except ImportError as exc:  # pragma: no cover - dependency error
            raise CameraError(f"USB libraries missing: {exc}") from exc
        self._usb_core = usb.core
        self._lock = threading.Lock()
        self._dev = libusb_package.find(idVendor=vendor_id, idProduct=product_id)
        if self._dev is None:
            raise CameraError(f"camera {vendor_id:04x}:{product_id:04x} not found on USB")
        # pyusb claims the interface before interface-directed control requests. The system
        # camera driver owns that interface, and endpoint-0 requests do not need the claim.
        self._dev._ctx.managed_claim_interface = lambda *a, **k: None
        self._vc_if, self._ct, self._pu = self._find_units()
        self._limits_units: tuple[int, int, int] | None = None

    def _find_units(self) -> tuple[int, int, int]:
        cfg = self._dev.get_active_configuration()
        vc = next(
            (i for i in cfg if i.bInterfaceClass == 0x0E and i.bInterfaceSubClass == 0x01), None
        )
        if vc is None:
            raise CameraError("camera has no UVC VideoControl interface")
        camera_terminal, processing_unit = 1, 2
        extra = bytes(vc.extra_descriptors)
        i = 0
        while i + 3 < len(extra) and extra[i] > 0:
            length, dtype, subtype = extra[i], extra[i + 1], extra[i + 2]
            body = extra[i : i + length]
            if dtype == 0x24 and subtype == 0x02 and len(body) >= 6:
                if struct.unpack_from("<H", body, 4)[0] == 0x0201:  # camera input terminal
                    camera_terminal = body[3]
            elif dtype == 0x24 and subtype == 0x05:
                processing_unit = body[3]
            i += length
        return vc.bInterfaceNumber, camera_terminal, processing_unit

    # -- raw requests -------------------------------------------------------------
    def _get(self, entity: int, selector: int, fmt: str, request: int = _GET_CUR) -> int:
        size = struct.calcsize(fmt)
        with self._lock:
            try:
                data = self._dev.ctrl_transfer(
                    0xA1, request, selector << 8, (entity << 8) | self._vc_if, size, timeout=1000
                )
            except self._usb_core.USBError as exc:
                raise CameraError(f"UVC read failed: {exc}") from exc
        return struct.unpack(fmt, bytes(data))[0]

    def _set(self, entity: int, selector: int, fmt: str, value: int) -> None:
        with self._lock:
            try:
                self._dev.ctrl_transfer(
                    0x21,
                    _SET_CUR,
                    selector << 8,
                    (entity << 8) | self._vc_if,
                    struct.pack(fmt, value),
                    timeout=1000,
                )
            except self._usb_core.USBError as exc:
                raise CameraError(f"UVC write failed: {exc}") from exc

    def _exposure_limits_units(self) -> tuple[int, int, int]:
        if self._limits_units is None:
            lo = self._get(self._ct, _CT_EXPOSURE_ABS, "<I", _GET_MIN)
            hi = self._get(self._ct, _CT_EXPOSURE_ABS, "<I", _GET_MAX)
            step = self._get(self._ct, _CT_EXPOSURE_ABS, "<I", _GET_RES) or 1
            self._limits_units = (max(lo, _MIN_USABLE_UNITS), hi, step)
        return self._limits_units

    # -- CameraControl ------------------------------------------------------------
    def set_manual_exposure(self, exposure_ms: float) -> float:
        lo, hi, _ = self._exposure_limits_units()
        units = min(max(round(exposure_ms / _EXPOSURE_UNIT_MS), lo), hi)
        if self._get(self._ct, _CT_AE_MODE, "<B") != AE_MANUAL:
            self._set(self._ct, _CT_AE_MODE, "<B", AE_MANUAL)
            time.sleep(0.1)
        readback = -1
        for _ in range(6):
            self._set(self._ct, _CT_EXPOSURE_ABS, "<I", units)
            time.sleep(0.05)
            readback = self._get(self._ct, _CT_EXPOSURE_ABS, "<I")
            if readback == units and self._get(self._ct, _CT_AE_MODE, "<B") == AE_MANUAL:
                return readback * _EXPOSURE_UNIT_MS
        raise CameraError(
            f"camera did not accept exposure {units * _EXPOSURE_UNIT_MS:.1f} ms "
            f"(reads back {readback * _EXPOSURE_UNIT_MS:.1f} ms)"
        )

    def get_exposure_ms(self) -> float:
        return self._get(self._ct, _CT_EXPOSURE_ABS, "<I") * _EXPOSURE_UNIT_MS

    def set_auto_exposure(self) -> None:
        self._set(self._ct, _CT_AE_MODE, "<B", AE_APERTURE_PRIORITY)

    def exposure_limits_ms(self) -> tuple[float, float, float]:
        lo, hi, step = self._exposure_limits_units()
        return (lo * _EXPOSURE_UNIT_MS, hi * _EXPOSURE_UNIT_MS, step * _EXPOSURE_UNIT_MS)

    def is_manual(self) -> bool:
        return self._get(self._ct, _CT_AE_MODE, "<B") == AE_MANUAL

    def describe(self) -> dict[str, Any]:
        lo, hi, step = self.exposure_limits_ms()
        return {
            "backend": "uvc-libusb",
            "exposure_mode": "manual" if self.is_manual() else "auto",
            "exposure_ms": round(self.get_exposure_ms(), 2),
            "exposure_min_ms": lo,
            "exposure_max_ms": hi,
            "exposure_step_ms": step,
            "gain_raw": self._get(self._pu, _PU_GAIN, "<H"),
            "gain_note": "gain control has no effect on this camera (measured)",
        }
