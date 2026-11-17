"""The printed calibration target: a ChArUco board (checkerboard with coded markers).

ChArUco is used instead of a plain checkerboard because each corner is identified by the
markers around it. The board therefore works when only part of it is in view, and it
cannot be read mirrored, which lets calibration detect a flipped image automatically.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..config import CalibrationTargetConfig

_DICTIONARY = cv2.aruco.DICT_4X4_100


def make_board(cfg: CalibrationTargetConfig) -> cv2.aruco.CharucoBoard:
    """Board with corner coordinates in millimetres."""
    if (cfg.squares_x * cfg.squares_y) // 2 > 100:
        raise ValueError("calibration target has more markers than the 100-marker dictionary")
    dictionary = cv2.aruco.getPredefinedDictionary(_DICTIONARY)
    return cv2.aruco.CharucoBoard(
        (cfg.squares_x, cfg.squares_y), cfg.square_mm, cfg.marker_mm, dictionary
    )


def board_size_mm(cfg: CalibrationTargetConfig) -> tuple[float, float]:
    return cfg.squares_x * cfg.square_mm, cfg.squares_y * cfg.square_mm


def render_board(cfg: CalibrationTargetConfig, px_per_mm: float) -> np.ndarray:
    """The board as a uint8 image at the given resolution, no margin."""
    w_mm, h_mm = board_size_mm(cfg)
    size = (round(w_mm * px_per_mm), round(h_mm * px_per_mm))
    return make_board(cfg).generateImage(size, marginSize=0, borderBits=1)


def write_target_pdf(cfg: CalibrationTargetConfig, path: str | Path) -> Path:
    """Write a US Letter PDF with the board at true size and a scale bar to check the print."""
    from PySide6.QtCore import QMarginsF, QRectF, Qt
    from PySide6.QtGui import (
        QFont,
        QGuiApplication,
        QImage,
        QPageLayout,
        QPageSize,
        QPainter,
        QPdfWriter,
        QPen,
    )

    app = QGuiApplication.instance() or QGuiApplication(["hwsorter", "-platform", "offscreen"])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    dpi = 1200
    writer = QPdfWriter(str(path))
    writer.setResolution(dpi)
    writer.setPageLayout(
        QPageLayout(
            QPageSize(QPageSize.PageSizeId.Letter),
            QPageLayout.Orientation.Portrait,
            QMarginsF(0, 0, 0, 0),
            QPageLayout.Unit.Millimeter,
        )
    )
    writer.setTitle("Hardware sorter calibration target")
    per_mm = dpi / 25.4
    w_mm, h_mm = board_size_mm(cfg)
    board = render_board(cfg, px_per_mm=40.0)  # crisp edges; drawn scaled to exact millimetres
    image = QImage(
        board.data, board.shape[1], board.shape[0], board.shape[1], QImage.Format.Format_Grayscale8
    )

    painter = QPainter(writer)
    try:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        font = QFont("Helvetica")
        font.setPixelSize(int(3.2 * per_mm))
        painter.setFont(font)
        x0, y = 20.0, 22.0

        def text(line: str, at_y: float) -> None:
            painter.drawText(
                QRectF(x0 * per_mm, at_y * per_mm, 175 * per_mm, 6 * per_mm),
                Qt.AlignmentFlag.AlignLeft,
                line,
            )

        text('Hardware sorter calibration target. Print at 100% ("Actual size"), no scaling.', y)
        text(
            f"Board: {cfg.squares_x} x {cfg.squares_y} squares of {cfg.square_mm:g} mm ({w_mm:g} x {h_mm:g} mm).",
            y + 6,
        )
        for copy in range(2):  # two copies per page, in case one is cut badly
            top = y + 18 + copy * (h_mm + 38)
            painter.drawImage(
                QRectF(x0 * per_mm, top * per_mm, w_mm * per_mm, h_mm * per_mm), image
            )
            bar_y = top + h_mm + 9
            pen = QPen(Qt.GlobalColor.black)
            pen.setWidthF(0.15 * per_mm)
            painter.setPen(pen)
            painter.drawLine(
                int(x0 * per_mm),
                int(bar_y * per_mm),
                int((x0 + cfg.scale_bar_mm) * per_mm),
                int(bar_y * per_mm),
            )
            for end in (x0, x0 + cfg.scale_bar_mm):
                painter.drawLine(
                    int(end * per_mm),
                    int((bar_y - 2.5) * per_mm),
                    int(end * per_mm),
                    int((bar_y + 2.5) * per_mm),
                )
            text(
                f"Scale bar: {cfg.scale_bar_mm:g} mm between the tick marks. Measure it and enter the result in the calibration tool.",
                bar_y + 4,
            )
    finally:
        painter.end()
    _ = app
    return path
