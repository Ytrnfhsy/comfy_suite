"""Reusable widgets."""

from __future__ import annotations

from typing import Callable

from PIL import Image
from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QImage, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..generator import CANVAS, ControlSpec
from ..i18n import tr
from ..image_ops import thumbnail
from ..jobs import Job, JobState
from ..workflow import CONTROL_MODES


def to_qimage(image: Image.Image) -> QImage:
    rgba = image.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    return QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888).copy()


def to_pixmap(image: Image.Image) -> QPixmap:
    return QPixmap.fromImage(to_qimage(image))


class PercentSlider(QWidget):
    """A 0–100 % slider with its value shown beside it."""

    value_changed = pyqtSignal(float)

    def __init__(self, value: float = 1.0, minimum: int = 1, parent: QWidget | None = None):
        super().__init__(parent)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(minimum, 100)
        self.label = QLabel()
        self.label.setMinimumWidth(40)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.label)
        self.slider.valueChanged.connect(self._changed)
        self.set_value(value)

    def _changed(self, v: int) -> None:
        self.label.setText(f"{v}%")
        self.value_changed.emit(v / 100)

    def value(self) -> float:
        return self.slider.value() / 100

    def set_value(self, v: float) -> None:
        self.slider.setValue(int(round(v * 100)))
        self.label.setText(f"{self.slider.value()}%")


class SeedWidget(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.fixed = QCheckBox(tr("Fixed seed"))
        self.seed = QSpinBox()
        self.seed.setRange(0, 2**31 - 1)
        self.seed.setEnabled(False)
        self.dice = QToolButton()
        self.dice.setText("🎲")
        self.dice.clicked.connect(self.randomize)
        self.fixed.toggled.connect(self.seed.setEnabled)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.fixed)
        lay.addWidget(self.seed, 1)
        lay.addWidget(self.dice)

    def randomize(self) -> None:
        import random

        self.seed.setValue(random.randint(0, 2**31 - 1))

    def value(self) -> int:
        return self.seed.value() if self.fixed.isChecked() else -1

    def set_value(self, seed: int) -> None:
        self.fixed.setChecked(True)
        self.seed.setValue(seed)


class ControlRow(QWidget):
    """One control layer: mode, source layer, model, strength and range."""

    removed = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.mode = QComboBox()
        for m in CONTROL_MODES:
            self.mode.addItem(m.capitalize(), m)
        self.mode.setCurrentIndex(CONTROL_MODES.index("scribble"))
        self.layer = QComboBox()
        self.layer.setMinimumWidth(110)
        self.model = QComboBox()
        self.model.setToolTip(tr("Model"))
        self.preprocess = QCheckBox(tr("Preprocess"))
        self.preprocess.setChecked(True)
        self.strength = PercentSlider(1.0, 0)
        self.start = QDoubleSpinBox()
        self.end = QDoubleSpinBox()
        for sb, v in ((self.start, 0.0), (self.end, 1.0)):
            sb.setRange(0, 1)
            sb.setSingleStep(0.05)
            sb.setValue(v)
            sb.setToolTip(tr("Range"))
        remove = QToolButton()
        remove.setText("✕")
        remove.setToolTip(tr("Remove"))
        remove.clicked.connect(lambda: self.removed.emit(self))

        top = QHBoxLayout()
        top.addWidget(self.mode)
        top.addWidget(self.layer, 1)
        top.addWidget(remove)
        mid = QHBoxLayout()
        mid.addWidget(self.model, 1)
        mid.addWidget(self.preprocess)
        bottom = QHBoxLayout()
        bottom.addWidget(self.strength, 1)
        bottom.addWidget(self.start)
        bottom.addWidget(QLabel("–"))
        bottom.addWidget(self.end)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.addLayout(top)
        lay.addLayout(mid)
        lay.addLayout(bottom)

    def set_layers(self, layers: list[tuple[int, str]]) -> None:
        current = self.layer.currentData()
        self.layer.blockSignals(True)
        self.layer.clear()
        self.layer.addItem(tr("Canvas"), CANVAS)
        for lid, name in layers:
            self.layer.addItem(name, lid)
        i = self.layer.findData(current)
        self.layer.setCurrentIndex(max(0, i))
        self.layer.blockSignals(False)

    def set_models(self, controlnets: list[str]) -> None:
        current = self.model.currentData()
        self.model.clear()
        self.model.addItem(tr("Auto"), "")
        for m in controlnets:
            self.model.addItem(m, m)
        i = self.model.findData(current)
        self.model.setCurrentIndex(max(0, i))

    def spec(self) -> ControlSpec:
        return ControlSpec(
            mode=self.mode.currentData(),
            layer=self.layer.currentData() if self.layer.currentData() is not None else CANVAS,
            strength=self.strength.value(),
            start=self.start.value(),
            end=self.end.value(),
            model=self.model.currentData() or "",
            preprocess=self.preprocess.isChecked(),
        )


class JobProgress(QWidget):
    """Progress of the running job plus queue size and cancel buttons."""

    def __init__(self, cancel: Callable[[], None], cancel_all: Callable[[], None], parent: QWidget | None = None):
        super().__init__(parent)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(True)
        self.bar.setFormat("")
        self.cancel = QToolButton()
        self.cancel.setText("■")
        self.cancel.setToolTip(tr("Cancel"))
        self.cancel.clicked.connect(cancel)
        menu = QMenu(self.cancel)
        act = QAction(tr("Cancel all"), menu)
        act.triggered.connect(cancel_all)
        menu.addAction(act)
        self.cancel.setMenu(menu)
        self.cancel.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.bar, 1)
        lay.addWidget(self.cancel)
        self.show_job(None, 0)

    def show_job(self, job: Job | None, pending: int) -> None:
        if job is None or job.finished:
            self.bar.setValue(0)
            self.bar.setFormat(job.error[:120] if job is not None and job.state == JobState.FAILED else "")
            self.cancel.setEnabled(pending > 0)
            return
        self.bar.setValue(int(job.progress * 1000))
        queued = f" (+{pending - 1})" if pending > 1 else ""
        label = tr("Queued") if job.state == JobState.QUEUED else (job.status or tr("Running"))
        self.bar.setFormat(f"{tr(label)}{queued}")
        self.cancel.setEnabled(True)


class HistoryList(QListWidget):
    """Thumbnails of finished jobs' images; activating one applies it."""

    apply_requested = pyqtSignal(object, int)  # job, image index
    preview_requested = pyqtSignal(object, int)
    discard_requested = pyqtSignal(object)
    reuse_seed = pyqtSignal(int)
    reuse_prompt = pyqtSignal(str)

    ROLE = Qt.ItemDataRole.UserRole

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(QSize(96, 96))
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setSpacing(4)
        self.setUniformItemSizes(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemDoubleClicked.connect(lambda it: self.apply_requested.emit(*it.data(self.ROLE)))
        self.currentItemChanged.connect(lambda it, _prev: it is not None and self.preview_requested.emit(*it.data(self.ROLE)))
        self._jobs: set[int] = set()

    def add_job(self, job: Job) -> None:
        if job.id in self._jobs or job.result is None:
            return
        self._jobs.add(job.id)
        for i, image in enumerate(job.result.images):
            item = QListWidgetItem(QIcon(to_pixmap(thumbnail(image, 192))), "")
            item.setToolTip(f"{job.title}\nseed {job.result.seed}")
            item.setData(self.ROLE, (job, i))
            self.insertItem(0, item)
        self.setCurrentRow(0)

    def remove_job(self, job: Job) -> None:
        for row in reversed(range(self.count())):
            j, _ = self.item(row).data(self.ROLE)
            if j is job:
                self.takeItem(row)
        self._jobs.discard(job.id)

    def selected(self) -> tuple[Job, int] | None:
        it = self.currentItem()
        return it.data(self.ROLE) if it is not None else None

    def _menu(self, pos) -> None:
        it = self.itemAt(pos)
        if it is None:
            return
        job, index = it.data(self.ROLE)
        menu = QMenu(self)
        menu.addAction(tr("Apply"), lambda: self.apply_requested.emit(job, index))
        menu.addAction(tr("Apply all"), lambda: [self.apply_requested.emit(job, i) for i in range(len(job.result.images))])
        menu.addSeparator()
        menu.addAction(tr("Copy prompt"), lambda: self.reuse_prompt.emit(job.result.prompt))
        menu.addAction(tr("Reuse seed"), lambda: self.reuse_seed.emit(job.result.seed))
        menu.addSeparator()
        menu.addAction(tr("Discard"), lambda: self.discard_requested.emit(job))
        menu.exec(self.mapToGlobal(pos))


class Preview(QLabel):
    """A large preview that scales its image to fit."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(160)
        self._image: Image.Image | None = None

    def set_image(self, image: Image.Image | None) -> None:
        self._image = image
        self._update()

    def resizeEvent(self, e) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(e)
        self._update()

    def _update(self) -> None:
        if self._image is None:
            self.clear()
            return
        pm = to_pixmap(self._image)
        self.setPixmap(pm.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))


def labelled(text: str, widget: QWidget) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    label = QLabel(text)
    label.setMinimumWidth(70)
    lay.addWidget(label)
    lay.addWidget(widget, 1)
    return w


def button(text: str, slot: Callable[[], None], tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    b.clicked.connect(slot)
    if tooltip:
        b.setToolTip(tooltip)
    return b
