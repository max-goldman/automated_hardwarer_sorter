"""Video display widget: shows a mono frame scaled to fit, with an overlay drawn in image pixels."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget


class VideoWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._array: np.ndarray | None = None
        self._image: QImage | None = None
        self.overlay: Callable[[QPainter], None] | None = None
        self.paint_count = 0
        self.setMinimumSize(480, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_frame(self, array: np.ndarray) -> None:
        self._array = np.ascontiguousarray(array)  # keep the pixels alive while Qt draws them
        height, width = self._array.shape
        self._image = QImage(
            self._array.data, width, height, width, QImage.Format.Format_Grayscale8
        )
        self.update()

    def paintEvent(self, event) -> None:
        self.paint_count += 1
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(24, 24, 24))
        if self._image is None:
            painter.setPen(QColor(200, 200, 200))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "waiting for frames…")
            return
        iw, ih = self._image.width(), self._image.height()
        scale = min(self.width() / iw, self.height() / ih)
        x0, y0 = (self.width() - iw * scale) / 2, (self.height() - ih * scale) / 2
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, scale < 1.0)
        painter.drawImage(QRectF(x0, y0, iw * scale, ih * scale), self._image)
        if self.overlay is not None:
            painter.translate(x0, y0)
            painter.scale(scale, scale)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self.overlay(painter)
