"""Settings and style editor dialogs."""

from __future__ import annotations

import copy

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..comfy import ComfyClient, ComfyError
from ..i18n import tr
from ..photosuite import ControlClient, PhotoSuiteError, read_token
from ..settings import Settings
from ..styles import ARCHITECTURES, LoraRef, Style, StyleLibrary
from .connection import Connection


def _path_row(edit: QLineEdit, directory: bool, parent: QWidget) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(edit, 1)
    browse = QToolButton()
    browse.setText("…")

    def pick() -> None:
        if directory:
            p = QFileDialog.getExistingDirectory(parent, "", edit.text())
        else:
            p, _ = QFileDialog.getOpenFileName(parent, "", edit.text())
        if p:
            edit.setText(p)

    browse.clicked.connect(pick)
    lay.addWidget(browse)
    return w


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("Settings"))
        self.settings = settings
        s = settings
        self.comfy_url = QLineEdit(s.comfy_url)
        self.host = QLineEdit(s.photosuite_host)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(s.photosuite_port)
        self.token = QLineEdit(s.photosuite_token)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_file = QLineEdit(s.photosuite_token_file)
        self.token_file.setPlaceholderText(str(s.token_file_path()))
        self.exchange = QLineEdit(s.exchange_dir)
        self.exchange.setPlaceholderText(str(s.exchange_path()))
        self.executable = QLineEdit(s.photosuite_executable)
        self.grow = QSpinBox()
        self.grow.setRange(0, 200)
        self.grow.setValue(s.selection_grow)
        self.feather = QSpinBox()
        self.feather.setRange(0, 200)
        self.feather.setValue(s.selection_feather)
        self.padding = QDoubleSpinBox()
        self.padding.setRange(0, 2)
        self.padding.setSingleStep(0.05)
        self.padding.setValue(s.context_padding)
        self.live_interval = QSpinBox()
        self.live_interval.setRange(100, 10000)
        self.live_interval.setSuffix(" ms")
        self.live_interval.setValue(s.live_interval_ms)
        self.history = QSpinBox()
        self.history.setRange(1, 500)
        self.history.setValue(s.history_size)
        self.on_top = QCheckBox()
        self.on_top.setChecked(s.always_on_top)
        self.language = QComboBox()
        for code, name in (("", "System"), ("uk", "Українська"), ("en", "English")):
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(s.language)))

        form = QFormLayout()
        form.addRow(tr("ComfyUI server"), self.comfy_url)
        form.addRow("PhotoSuite host", self.host)
        form.addRow(tr("PhotoSuite control port"), self.port)
        form.addRow(tr("Token"), self.token)
        form.addRow(tr("Token file"), _path_row(self.token_file, False, self))
        form.addRow(tr("Exchange folder"), _path_row(self.exchange, True, self))
        form.addRow(tr("PhotoSuite executable"), _path_row(self.executable, False, self))
        form.addRow(tr("Selection grow (px)"), self.grow)
        form.addRow(tr("Selection feather (px)"), self.feather)
        form.addRow(tr("Context around selection"), self.padding)
        form.addRow(tr("Interval") + " (" + tr("Live") + ")", self.live_interval)
        form.addRow(tr("History size"), self.history)
        form.addRow(tr("Keep window on top"), self.on_top)
        form.addRow(tr("Language"), self.language)

        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        test = QPushButton(tr("Test connection"))
        test.clicked.connect(self.test)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(test)
        lay.addWidget(self.result_label)
        lay.addWidget(buttons)

    def collect(self) -> Settings:
        s = copy.copy(self.settings)
        s.comfy_url = self.comfy_url.text().strip() or Settings.comfy_url
        s.photosuite_host = self.host.text().strip() or "127.0.0.1"
        s.photosuite_port = self.port.value()
        s.photosuite_token = self.token.text().strip()
        s.photosuite_token_file = self.token_file.text().strip()
        s.exchange_dir = self.exchange.text().strip()
        s.photosuite_executable = self.executable.text().strip()
        s.selection_grow = self.grow.value()
        s.selection_feather = self.feather.value()
        s.context_padding = self.padding.value()
        s.live_interval_ms = self.live_interval.value()
        s.history_size = self.history.value()
        s.always_on_top = self.on_top.isChecked()
        s.language = self.language.currentData()
        return s

    def test(self) -> None:
        s = self.collect()
        lines = []
        try:
            stats = ComfyClient(s.comfy_url, timeout=5).system_stats()
            version = stats.get("system", {}).get("comfyui_version", "")
            lines.append(f"✔ ComfyUI {version}")
        except ComfyError as e:
            lines.append(f"✘ ComfyUI: {e}")
        try:
            token = read_token(s.photosuite_token, None if s.photosuite_token else s.token_file_path())
            with ControlClient(s.photosuite_host, s.photosuite_port, token, timeout=5) as c:
                n = len(c.execute("session.inspect").get("documents", []))
            lines.append(f"✔ PhotoSuite ({n} doc)")
        except (PhotoSuiteError, OSError) as e:
            lines.append(f"✘ PhotoSuite: {e}")
        self.result_label.setText("\n".join(lines))

    def accept(self) -> None:
        new = self.collect()
        for k, v in vars(new).items():
            setattr(self.settings, k, v)
        self.settings.save()
        super().accept()


class StyleDialog(QDialog):
    def __init__(self, conn: Connection, style: Style, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("Edit style"))
        self.conn = conn
        self.library: StyleLibrary = conn.styles
        self.original = style
        self.saved_name = style.name
        m = conn.models

        self.name = QLineEdit(style.name)
        self.arch = QComboBox()
        for a in ARCHITECTURES:
            self.arch.addItem(a, a)
        self.arch.setCurrentIndex(max(0, self.arch.findData(style.architecture)))
        self.checkpoint = self._combo([("", tr("Auto"))] + [(c, c) for c in m.checkpoints], style.checkpoint)
        self.vae = self._combo([("", tr("From checkpoint"))] + [(v, v) for v in m.vaes], style.vae)
        self.style_prompt = QPlainTextEdit(style.style_prompt)
        self.style_prompt.setFixedHeight(60)
        self.negative = QPlainTextEdit(style.negative_prompt)
        self.negative.setFixedHeight(60)
        samplers = m.samplers or ["euler", "euler_ancestral", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_sde", "ddim"]
        schedulers = m.schedulers or ["normal", "karras", "exponential", "sgm_uniform", "simple", "beta"]
        self.sampler = self._combo([(x, x) for x in samplers], style.sampler)
        self.scheduler = self._combo([(x, x) for x in schedulers], style.scheduler)
        self.steps = self._spin(1, 200, style.steps)
        self.cfg = self._dspin(0, 30, style.cfg)
        self.guidance = self._dspin(0, 30, style.guidance)
        self.clip_skip = self._spin(1, 12, style.clip_skip)
        self.native = self._spin(256, 4096, style.native_resolution, 64)
        self.live_sampler = self._combo([(x, x) for x in samplers], style.live_sampler)
        self.live_scheduler = self._combo([(x, x) for x in schedulers], style.live_scheduler)
        self.live_steps = self._spin(1, 100, style.live_steps)
        self.live_cfg = self._dspin(0, 30, style.live_cfg)

        self.loras = QTableWidget(0, 3)
        self.loras.setHorizontalHeaderLabels(["LoRA", tr("Strength (LoRA)"), ""])
        self.loras.horizontalHeader().setStretchLastSection(False)
        self.loras.setColumnWidth(0, 260)
        for l in style.loras:
            self._add_lora(l)
        add_lora = QPushButton(tr("Add LoRA"))
        add_lora.clicked.connect(lambda: self._add_lora(LoraRef(m.loras[0] if m.loras else "", 1.0)))

        form = QFormLayout()
        form.addRow(tr("Name"), self.name)
        form.addRow(tr("Architecture"), self.arch)
        form.addRow(tr("Checkpoint"), self.checkpoint)
        form.addRow(tr("VAE"), self.vae)
        form.addRow(tr("Style prompt"), self.style_prompt)
        form.addRow(tr("Negative prompt"), self.negative)
        form.addRow(tr("Sampler"), self.sampler)
        form.addRow(tr("Scheduler"), self.scheduler)
        form.addRow(tr("Steps"), self.steps)
        form.addRow("CFG", self.cfg)
        form.addRow(tr("Guidance (Flux)"), self.guidance)
        form.addRow(tr("CLIP skip"), self.clip_skip)
        form.addRow(tr("Native resolution"), self.native)
        form.addRow(tr("Live sampler"), self.live_sampler)
        form.addRow(tr("Scheduler") + " (" + tr("Live") + ")", self.live_scheduler)
        form.addRow(tr("Live steps"), self.live_steps)
        form.addRow(tr("Live CFG"), self.live_cfg)

        save = QPushButton(tr("Save"))
        save.clicked.connect(lambda: self.save(False))
        save_new = QPushButton(tr("Save as new"))
        save_new.clicked.connect(lambda: self.save(True))
        delete = QPushButton(tr("Remove"))
        delete.clicked.connect(self.delete)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(delete)
        buttons.addStretch(1)
        buttons.addWidget(save_new)
        buttons.addWidget(save)
        buttons.addWidget(close)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.loras)
        lay.addWidget(add_lora)
        lay.addLayout(buttons)

    # -- helpers ------------------------------------------------------------------------------

    @staticmethod
    def _combo(items: list[tuple[str, str]], current: str) -> QComboBox:
        c = QComboBox()
        c.setEditable(False)
        for data, text in items:
            c.addItem(text, data)
        i = c.findData(current)
        if i < 0 and current:
            c.addItem(current, current)
            i = c.count() - 1
        c.setCurrentIndex(max(0, i))
        return c

    @staticmethod
    def _spin(lo: int, hi: int, v: int, step: int = 1) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setValue(int(v))
        return s

    @staticmethod
    def _dspin(lo: float, hi: float, v: float) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(0.5)
        s.setValue(float(v))
        return s

    def _add_lora(self, lora: LoraRef) -> None:
        row = self.loras.rowCount()
        self.loras.insertRow(row)
        names = self.conn.models.loras or ([lora.name] if lora.name else [])
        self.loras.setCellWidget(row, 0, self._combo([(n, n) for n in names], lora.name))
        self.loras.setCellWidget(row, 1, self._dspin(-5, 5, lora.strength))
        enabled = QCheckBox()
        enabled.setChecked(lora.enabled)
        self.loras.setCellWidget(row, 2, enabled)
        self.loras.setItem(row, 0, QTableWidgetItem())

    def collect(self) -> Style:
        loras = []
        for row in range(self.loras.rowCount()):
            name = self.loras.cellWidget(row, 0).currentData()
            if name:
                loras.append(LoraRef(name, self.loras.cellWidget(row, 1).value(), self.loras.cellWidget(row, 2).isChecked()))
        return Style(
            name=self.name.text().strip() or "Style",
            architecture=self.arch.currentData(),
            checkpoint=self.checkpoint.currentData() or "",
            vae=self.vae.currentData() or "",
            loras=loras,
            style_prompt=self.style_prompt.toPlainText().strip() or "{prompt}",
            negative_prompt=self.negative.toPlainText().strip(),
            sampler=self.sampler.currentData(),
            scheduler=self.scheduler.currentData(),
            steps=self.steps.value(),
            cfg=self.cfg.value(),
            guidance=self.guidance.value(),
            clip_skip=self.clip_skip.value(),
            native_resolution=self.native.value(),
            live_sampler=self.live_sampler.currentData(),
            live_scheduler=self.live_scheduler.currentData(),
            live_steps=self.live_steps.value(),
            live_cfg=self.live_cfg.value(),
            filename="",
        )

    def save(self, as_new: bool) -> None:
        style = self.collect()
        if not as_new:
            # Editing a built-in writes a user copy with the same file name, which overrides it.
            style.filename = self.original.filename
        self.library.save(style)
        self.saved_name = style.name
        self.accept()

    def delete(self) -> None:
        if QMessageBox.question(self, tr("Remove"), self.original.name + "?") != QMessageBox.StandardButton.Yes:
            return
        self.library.delete(self.original)
        self.saved_name = ""
        self.accept()
