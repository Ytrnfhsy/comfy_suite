"""The workspaces: Generate, Upscale, Live and Custom Graph."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..generator import GenerateParams
from ..i18n import tr
from ..jobs import Job, JobState
from ..photosuite import DocumentInfo, PhotoSuiteError
from ..workflow import WorkflowError, custom_placeholders, load_custom_workflow
from .connection import Connection
from .widgets import ControlRow, PercentSlider, Preview, SeedWidget, labelled


class PromptEdit(QPlainTextEdit):
    """Multi-line prompt; Ctrl+Enter submits."""

    submitted = pyqtSignal()

    def __init__(self, placeholder: str, lines: int = 3, parent: QWidget | None = None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setTabChangesFocus(True)
        self.setFixedHeight(self.fontMetrics().lineSpacing() * lines + 12)

    def keyPressEvent(self, e) -> None:  # noqa: N802 - Qt override
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.submitted.emit()
            return
        super().keyPressEvent(e)

    def text(self) -> str:
        return self.toPlainText().strip()


class Workspace(QWidget):
    """Base: knows the connection and the selected style."""

    message = pyqtSignal(str)

    def __init__(self, conn: Connection, style_name, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        self.style_name = style_name  # callable returning the selected style name

    def style(self):
        return self.conn.styles.get(self.style_name())

    def can_run(self) -> bool:
        if not self.conn.ready:
            self.message.emit(tr("Not connected"))
            return False
        if self.conn.document is None and self.conn.refresh_document() is None:
            self.message.emit(tr("No document open in PhotoSuite"))
            return False
        return True

    def on_document(self, doc: DocumentInfo | None) -> None:
        pass

    def on_models(self) -> None:
        pass


class GenerateWorkspace(Workspace):
    def __init__(self, conn: Connection, style_name, parent: QWidget | None = None):
        super().__init__(conn, style_name, parent)
        self.prompt = PromptEdit(tr("Describe the image or the content to fill…"), 4)
        self.prompt.submitted.connect(self.generate)
        self.negative = PromptEdit(tr("What to avoid…"), 2)
        self.negative.submitted.connect(self.generate)
        self.negative.setVisible(False)
        neg_toggle = QToolButton()
        neg_toggle.setText("−")
        neg_toggle.setToolTip(tr("Negative prompt"))
        neg_toggle.setCheckable(True)
        neg_toggle.toggled.connect(self.negative.setVisible)

        self.controls_box = QVBoxLayout()
        self.controls_box.setContentsMargins(0, 0, 0, 0)
        add_control = QToolButton()
        add_control.setText("+ " + tr("Add control layer"))
        add_control.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        add_control.clicked.connect(self.add_control)

        self.strength = PercentSlider(1.0)
        self.strength.value_changed.connect(lambda _v: self.update_button())
        self.batch = QSpinBox()
        self.batch.setRange(1, 16)
        self.batch.setValue(conn.settings.batch_size)
        self.seed = SeedWidget()
        self.use_selection = QCheckBox(tr("Use selection"))
        self.use_selection.setChecked(True)
        self.use_selection.toggled.connect(lambda _v: self.update_button())
        self.button = QPushButton(tr("Generate"))
        self.button.setMinimumHeight(34)
        self.button.clicked.connect(self.generate)

        prompt_row = QHBoxLayout()
        prompt_row.addWidget(self.prompt, 1)
        prompt_row.addWidget(neg_toggle, 0, Qt.AlignmentFlag.AlignTop)
        options = QHBoxLayout()
        options.addWidget(QLabel(tr("Batch")))
        options.addWidget(self.batch)
        options.addWidget(self.use_selection)
        options.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(prompt_row)
        lay.addWidget(self.negative)
        lay.addLayout(self.controls_box)
        lay.addWidget(add_control, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(labelled(tr("Strength"), self.strength))
        lay.addLayout(options)
        lay.addWidget(labelled(tr("Seed"), self.seed))
        lay.addWidget(self.button)
        self.rows: list[ControlRow] = []
        self.update_button()

    def add_control(self) -> None:
        row = ControlRow()
        row.removed.connect(self.remove_control)
        row.set_models(self.conn.models.controlnets)
        row.set_layers(self._layers())
        self.rows.append(row)
        self.controls_box.addWidget(row)

    def remove_control(self, row: ControlRow) -> None:
        self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()

    def _layers(self) -> list[tuple[int, str]]:
        doc = self.conn.document
        if doc is None:
            return []
        return [(l.id, "  " * l.depth + l.name) for l in doc.all_layers()]

    def on_document(self, doc: DocumentInfo | None) -> None:
        layers = self._layers()
        for row in self.rows:
            row.set_layers(layers)
        self.update_button()

    def on_models(self) -> None:
        for row in self.rows:
            row.set_models(self.conn.models.controlnets)

    def update_button(self) -> None:
        doc = self.conn.document
        has_sel = doc is not None and doc.selection is not None and self.use_selection.isChecked()
        if self.strength.value() < 1.0:
            self.button.setText(tr("Refine"))
        elif has_sel:
            self.button.setText(tr("Fill"))
        else:
            self.button.setText(tr("Generate"))

    def params(self) -> GenerateParams:
        return GenerateParams(
            prompt=self.prompt.text(),
            negative=self.negative.text(),
            strength=self.strength.value(),
            seed=self.seed.value(),
            batch=self.batch.value(),
            controls=[r.spec() for r in self.rows],
            use_selection=self.use_selection.isChecked(),
        )

    def generate(self) -> None:
        if not self.can_run():
            return
        style, params = self.style(), self.params()
        gen = self.conn.generator
        title = params.prompt or self.button.text()
        self.conn.submit("generate", title, lambda progress, cancel: gen.generate(style, params, progress, cancel))


class UpscaleWorkspace(Workspace):
    FACTORS = (1.5, 2.0, 3.0, 4.0)

    def __init__(self, conn: Connection, style_name, parent: QWidget | None = None):
        super().__init__(conn, style_name, parent)
        self.factor = QComboBox()
        for f in self.FACTORS:
            self.factor.addItem(f"{f:g}×", f)
        self.factor.setCurrentIndex(1)
        self.factor.currentIndexChanged.connect(lambda _i: self.on_document(self.conn.document))
        self.size_label = QLabel()
        self.model = QComboBox()
        self.refine = QCheckBox(tr("Refine after upscaling"))
        self.strength = PercentSlider(0.3)
        self.strength.setEnabled(False)
        self.refine.toggled.connect(self.strength.setEnabled)
        self.prompt = PromptEdit(tr("Prompt"), 2)
        self.button = QPushButton(tr("Upscale"))
        self.button.setMinimumHeight(34)
        self.button.clicked.connect(self.upscale)
        form = QFormLayout()
        form.addRow(tr("Factor"), self.factor)
        form.addRow(tr("Target size"), self.size_label)
        form.addRow(tr("Upscale model"), self.model)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(form)
        lay.addWidget(self.refine)
        lay.addWidget(labelled(tr("Strength"), self.strength))
        lay.addWidget(self.prompt)
        lay.addWidget(self.button)
        lay.addStretch(1)
        self.on_models()

    def on_models(self) -> None:
        current = self.model.currentData()
        self.model.clear()
        self.model.addItem(tr("None (Lanczos)"), "")
        for m in self.conn.models.upscalers:
            self.model.addItem(m, m)
        i = self.model.findData(current)
        # Prefer a real upscaler when one is installed.
        self.model.setCurrentIndex(i if i >= 0 else (1 if self.model.count() > 1 else 0))

    def on_document(self, doc: DocumentInfo | None) -> None:
        if doc is None:
            self.size_label.setText("—")
            return
        f = self.factor.currentData()
        self.size_label.setText(f"{doc.width}×{doc.height} → {round(doc.width * f)}×{round(doc.height * f)}")

    def upscale(self) -> None:
        if not self.can_run():
            return
        style, gen = self.style(), self.conn.generator
        factor, model, refine = self.factor.currentData(), self.model.currentData(), self.refine.isChecked()
        strength, prompt = self.strength.value(), self.prompt.text()
        self.conn.submit(
            "upscale",
            f"{tr('Upscale')} {factor:g}×",
            lambda progress, cancel: gen.upscale(style, factor, model, refine, strength, prompt, progress, cancel),
        )


class LiveWorkspace(Workspace):
    """Re-generates the canvas (or selection) whenever the document changes."""

    def __init__(self, conn: Connection, style_name, parent: QWidget | None = None):
        super().__init__(conn, style_name, parent)
        self.prompt = PromptEdit(tr("Prompt"), 3)
        self.strength = PercentSlider(0.55)
        self.seed = SeedWidget()
        self.seed.randomize()
        self.seed.fixed.setChecked(True)
        self.toggle = QPushButton("▶ " + tr("Start"))
        self.toggle.setCheckable(True)
        self.toggle.setMinimumHeight(34)
        self.toggle.toggled.connect(self._toggled)
        self.preview = Preview()
        self.apply_button = QPushButton(tr("Apply to document"))
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply)
        self.status = QLabel(tr("Live painting regenerates the canvas while you paint."))
        self.status.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.prompt)
        lay.addWidget(labelled(tr("Strength"), self.strength))
        lay.addWidget(labelled(tr("Seed"), self.seed))
        lay.addWidget(self.toggle)
        lay.addWidget(self.preview, 1)
        lay.addWidget(self.apply_button)
        lay.addWidget(self.status)

        self.last_key: Any = None
        self.last_job: Job | None = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        conn.live_queue.subscribe(self._job_from_thread)
        self._signal_job.connect(self._job_done)

    _signal_job = pyqtSignal(object)

    def _job_from_thread(self, job: Job) -> None:
        self._signal_job.emit(job)

    def _toggled(self, on: bool) -> None:
        self.toggle.setText(("■ " + tr("Stop")) if on else ("▶ " + tr("Start")))
        if on:
            if not self.can_run():
                self.toggle.setChecked(False)
                return
            self.last_key = None
            self.timer.start(max(100, self.conn.settings.live_interval_ms))
        else:
            self.timer.stop()

    def _tick(self) -> None:
        if self.conn.live_queue.pending() > 0 or not self.conn.ready:
            return
        rev = self.conn.revision()
        if rev is None:
            return
        key = (rev, self.prompt.text(), self.strength.value(), self.seed.value(), self.style_name())
        if key == self.last_key:
            return
        self.last_key = key
        style, gen = self.style(), self.conn.generator
        params = GenerateParams(prompt=self.prompt.text(), strength=self.strength.value(), seed=self.seed.value(), batch=1)
        self.conn.live_queue.submit("live", params.prompt or tr("Live"), lambda progress, cancel: gen.generate(style, params, progress, cancel, live=True))

    def _job_done(self, job: Job) -> None:
        if job.state == JobState.DONE and job.result is not None:
            self.last_job = job
            from ..image_ops import apply_mask

            r = job.result
            self.preview.set_image(apply_mask(r.images[0], r.mask) if r.into_selection else r.images[0])
            self.apply_button.setEnabled(True)
            self.status.setText(f"seed {r.seed}")
        elif job.state == JobState.FAILED:
            self.status.setText(job.error)
            self.toggle.setChecked(False)
        elif job.state == JobState.RUNNING:
            self.status.setText(job.status or tr("Running"))

    def apply(self) -> None:
        job = self.last_job
        if job is None or job.result is None or self.conn.generator is None:
            return
        try:
            self.conn.generator.apply(job.result, 0)
        except PhotoSuiteError as e:
            self.message.emit(str(e))

    def stop(self) -> None:
        self.toggle.setChecked(False)


class CustomWorkspace(Workspace):
    """Runs a ComfyUI workflow exported in API format, with ``{{placeholders}}``."""

    def __init__(self, conn: Connection, style_name, parent: QWidget | None = None):
        super().__init__(conn, style_name, parent)
        self.workflow: dict[str, Any] | None = None
        self.path_label = QLabel(tr("No workflow loaded"))
        self.path_label.setWordWrap(True)
        load = QPushButton(tr("Load workflow…"))
        load.clicked.connect(self.load)
        self.form = QFormLayout()
        self.fields: dict[str, QWidget] = {}
        self.button = QPushButton(tr("Run"))
        self.button.setMinimumHeight(34)
        self.button.setEnabled(False)
        self.button.clicked.connect(self.run)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(self.path_label, 1)
        row.addWidget(load)
        lay.addLayout(row)
        lay.addLayout(self.form)
        lay.addWidget(self.button)
        lay.addStretch(1)

    def load(self, path: str | None = None) -> None:
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, tr("Load workflow…"), "", "ComfyUI API workflow (*.json)")
        if not path:
            return
        try:
            self.workflow = load_custom_workflow(Path(path).read_text(encoding="utf-8"))
        except (OSError, WorkflowError) as e:
            self.message.emit(str(e))
            return
        self.path_label.setText(Path(path).name)
        self._build_form(custom_placeholders(self.workflow))
        self.button.setEnabled(True)

    def _build_form(self, names: list[str]) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.fields = {}
        for name in names:
            if name in ("canvas", "mask", "width", "height"):
                continue
            if name in ("prompt", "negative"):
                w: QWidget = PromptEdit(tr("Prompt") if name == "prompt" else tr("Negative prompt"), 3 if name == "prompt" else 2)
            elif name == "seed":
                w = SeedWidget()
            elif name in ("strength", "denoise"):
                w = PercentSlider(1.0)
            else:
                w = QLineEdit()
            self.fields[name] = w
            self.form.addRow(name, w)
        info = [n for n in names if n in ("canvas", "mask")]
        if info:
            self.form.addRow("", QLabel("PhotoSuite: " + ", ".join(info)))

    def values(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, w in self.fields.items():
            if isinstance(w, PromptEdit):
                out[name] = w.text()
            elif isinstance(w, SeedWidget):
                out[name] = w.value()
            elif isinstance(w, PercentSlider):
                out[name] = w.value()
            elif isinstance(w, QLineEdit):
                out[name] = _parse_value(w.text())
        return out

    def run(self) -> None:
        if self.workflow is None or not self.can_run():
            return
        workflow, values, gen = self.workflow, self.values(), self.conn.generator
        title = str(values.get("prompt") or self.path_label.text())
        self.conn.submit("custom", title, lambda progress, cancel: gen.custom(workflow, values, progress, cancel))


def _parse_value(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text
