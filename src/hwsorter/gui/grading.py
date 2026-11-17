"""Qt grading tool: step through the parts of a session and label each one (task 012).

Keys: 1-6 = the six classes (PartClass order), 0 or O = out of scope, X = exclude (not a real
part), M = several parts in this crop (then one class key per part, O for an out-of-scope
part, Backspace removes the last, Enter finishes, Esc cancels), Enter = confirm the predicted
identity, Left/Right (or B/N) = back/next without labelling, Delete = clear the label.
Every labelling key saves at once through the label store, then moves on to the next part.
The "parts fed" box records how many parts were physically fed in the run.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..contracts import PartClass
from ..grading import (
    KIND_CLASS,
    KIND_EXCLUDED,
    KIND_MULTI,
    KIND_OUT_OF_SCOPE,
    GradingSession,
    LabelStore,
    load_session,
)

CLASS_KEYS = {
    Qt.Key_1: PartClass.BOLT_025,
    Qt.Key_2: PartClass.BOLT_0375,
    Qt.Key_3: PartClass.NUT_025,
    Qt.Key_4: PartClass.NUT_0375,
    Qt.Key_5: PartClass.WASHER_025,
    Qt.Key_6: PartClass.WASHER_0375,
}
OOS_KEYS = (Qt.Key_0, Qt.Key_O)
ENTER_KEYS = (Qt.Key_Return, Qt.Key_Enter)


class ImageLabel(QLabel):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(320, 240)
        self.setStyleSheet("background:#222;color:#aaa;")
        self._pix: QPixmap | None = None

    def show_image(self, path: Path | None, empty: str) -> None:
        pix = QPixmap(str(path)) if path else QPixmap()
        if pix.isNull():
            self._pix = None
            self.setPixmap(QPixmap())
            self.setText(empty)
            return
        self._pix = pix
        self._rescale()

    def _rescale(self) -> None:
        if self._pix is not None:
            self.setPixmap(
                self._pix.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rescale()


class GradingWindow(QMainWindow):
    def __init__(self, grading: GradingSession) -> None:
        super().__init__()
        self.grading = grading
        self.index = 0
        self.multi: list[str | None] | None = None  # identities being entered in several-parts mode
        self.setWindowTitle(f"Grade {grading.session.root.name}")
        self.crop = ImageLabel("no crop")
        self.frame = ImageLabel("no frame")
        self.info = QLabel()
        self.info.setTextFormat(Qt.PlainText)
        self.info.setStyleSheet("font-family:monospace;")
        self.status = QLabel()
        self.keys_help = QLabel(
            "1-6 class | 0/O out of scope | X exclude | M several parts | Enter confirm predicted | "
            "Left/Right back/next | Del clear"
        )
        self.keys_help.setWordWrap(True)
        self.fed = QSpinBox()
        self.fed.setRange(0, 100000)
        self.fed.setSpecialValueText("unset")
        self.fed.setFocusPolicy(Qt.ClickFocus)
        self.fed.setValue(grading.store.parts_fed or 0)
        self.fed.editingFinished.connect(self._fed_changed)
        self._fed_set = grading.store.parts_fed is not None

        images = QHBoxLayout()
        images.addWidget(self.crop, 1)
        images.addWidget(self.frame, 2)
        fed_row = QHBoxLayout()
        fed_row.addWidget(QLabel("Parts fed this run:"))
        fed_row.addWidget(self.fed)
        fed_row.addStretch(1)
        root = QVBoxLayout()
        root.addLayout(images, 1)
        root.addWidget(self.info)
        root.addWidget(self.status)
        root.addLayout(fed_row)
        root.addWidget(self.keys_help)
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)
        self.setFocusPolicy(Qt.StrongFocus)
        self.resize(1300, 700)
        self._refresh()

    # ------------------------------------------------------------------ display

    def _describe_label(self, index: int) -> str:
        lab = self.grading.label_of(index)
        if lab is None:
            return "unlabelled"
        if lab.kind == KIND_CLASS:
            return f"{lab.part_class}"
        if lab.kind == KIND_OUT_OF_SCOPE:
            return "out of scope"
        if lab.kind == KIND_EXCLUDED:
            return "excluded (not a part)"
        return "several: " + ", ".join(i or "out of scope" for i in lab.identities)

    def _refresh(self) -> None:
        g = self.grading
        if not len(g):
            self.info.setText("no parts in this session")
            self.status.setText("")
            return
        part = g.parts[self.index]
        group = g.session.group_of(part.track_id)
        score = "n/a" if part.score is None else f"{part.score:.2f}"
        n_done = sum(1 for i in range(len(g)) if g.label_of(i) is not None)
        lines = [
            f"Part {self.index + 1}/{len(g)}   track {part.track_id}   labelled {n_done}/{len(g)}",
            f"Predicted: {part.verdict} {part.part_class or ''}   score {score}   "
            f"flags {','.join(part.flags) or '-'}",
            "Group output: "
            + (
                f"{group.output} (group {group.group_id}, {len(group.member_track_ids)} parts)"
                if group
                else "none"
            ),
            f"Label: {self._describe_label(self.index)}",
        ]
        if self.multi is not None:
            lines.append("SEVERAL PARTS: " + (", ".join(i or "oos" for i in self.multi) or "-"))
        self.info.setText("\n".join(lines))
        self.crop.show_image(g.session.crop_path(part), "no crop saved")
        self.frame.show_image(g.session.frame_path(part), "no full frame saved")

    def _say(self, text: str) -> None:
        self.status.setText(text)

    # ------------------------------------------------------------------ actions

    def go(self, delta: int) -> None:
        if len(self.grading):
            self.index = min(max(self.index + delta, 0), len(self.grading) - 1)
        self.multi = None
        self._refresh()

    def _label(self, kind: str, part_class: str | None = None, identities=()) -> None:
        self.grading.set_label(self.index, kind, part_class, tuple(identities))
        self.multi = None
        self._say(f"saved: {self._describe_label(self.index)}")
        if self.index < len(self.grading) - 1:
            self.index += 1
        self._refresh()

    def _fed_changed(self) -> None:
        value = self.fed.value()
        if value == 0 and not self._fed_set:
            return
        self._fed_set = value != 0
        self.grading.set_parts_fed(value or None)
        self.setFocus()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if not len(self.grading):
            return super().keyPressEvent(event)
        if self.multi is not None:
            if key in CLASS_KEYS:
                self.multi.append(CLASS_KEYS[key].value)
            elif key in OOS_KEYS:
                self.multi.append(None)
            elif key == Qt.Key_Backspace and self.multi:
                self.multi.pop()
            elif key == Qt.Key_Escape:
                self.multi = None
                self._say("several-parts entry cancelled")
            elif key in ENTER_KEYS:
                if len(self.multi) < 2:
                    self._say("several parts needs at least two identities")
                else:
                    self._label(KIND_MULTI, identities=self.multi)
                    return
            self._refresh()
            return
        if key in CLASS_KEYS:
            self._label(KIND_CLASS, CLASS_KEYS[key].value)
        elif key in OOS_KEYS:
            self._label(KIND_OUT_OF_SCOPE)
        elif key == Qt.Key_X:
            self._label(KIND_EXCLUDED)
        elif key == Qt.Key_M:
            self.multi = []
            self._say("several parts: press a class key per part, Enter to finish, Esc to cancel")
            self._refresh()
        elif key in ENTER_KEYS:
            predicted = self.grading.predicted_label_args(self.index)
            if predicted is None:
                self._say("no prediction for this part: choose a class")
            else:
                self._label(*predicted)
        elif key in (Qt.Key_Right, Qt.Key_N):
            self.go(1)
        elif key in (Qt.Key_Left, Qt.Key_B):
            self.go(-1)
        elif key == Qt.Key_Delete:
            self.grading.clear_label(self.index)
            self._say("label cleared")
            self._refresh()
        else:
            super().keyPressEvent(event)


def open_grading(session_path: str | Path, labels: str | Path | None = None) -> GradingWindow:
    data = load_session(session_path)
    store = LabelStore(labels or data.root / "labels.jsonl")
    return GradingWindow(GradingSession(data, store))


def run_grading(session_path: str | Path, labels: str | Path | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = open_grading(session_path, labels)
    window.show()
    return int(app.exec())
