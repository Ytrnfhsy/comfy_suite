"""The panel window: connection status, style, workspaces, progress, preview and history."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from ..image_ops import apply_mask
from ..jobs import Job, JobState
from ..photosuite import PhotoSuiteError
from .connection import Connection
from .dialogs import SettingsDialog, StyleDialog
from .widgets import HistoryList, JobProgress, Preview
from .workspaces import CustomWorkspace, GenerateWorkspace, LiveWorkspace, UpscaleWorkspace


class MainWindow(QWidget):
    def __init__(self, conn: Connection):
        super().__init__()
        self.conn = conn
        self.setWindowTitle(f"ComfySuite — {tr('AI for PhotoSuite')}")
        self.resize(380, 820)
        if conn.settings.always_on_top:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        # -- header: workspace, connection, settings
        self.workspace = QComboBox()
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.RichText)
        connect = QToolButton()
        connect.setText("⟳")
        connect.setToolTip(tr("Connect"))
        connect.clicked.connect(conn.connect_all)
        settings = QToolButton()
        settings.setText("⚙")
        settings.setToolTip(tr("Settings"))
        settings.clicked.connect(self.open_settings)
        header = QHBoxLayout()
        header.addWidget(self.workspace, 1)
        header.addWidget(self.status)
        header.addWidget(connect)
        header.addWidget(settings)

        # -- style
        self.style_combo = QComboBox()
        edit_style = QToolButton()
        edit_style.setText("✎")
        edit_style.setToolTip(tr("Edit style"))
        edit_style.clicked.connect(self.edit_style)
        style_row = QHBoxLayout()
        style_row.addWidget(QLabel(tr("Style")))
        style_row.addWidget(self.style_combo, 1)
        style_row.addWidget(edit_style)
        self.reload_styles(conn.settings.style)
        self.style_combo.currentIndexChanged.connect(self._style_changed)

        # -- workspaces
        style_name = lambda: self.style_combo.currentData() or ""  # noqa: E731
        self.generate = GenerateWorkspace(conn, style_name)
        self.upscale = UpscaleWorkspace(conn, style_name)
        self.live = LiveWorkspace(conn, style_name)
        self.custom = CustomWorkspace(conn, style_name)
        self.stack = QStackedWidget()
        for name, ws in ((tr("Generate"), self.generate), (tr("Upscale"), self.upscale), (tr("Live"), self.live), (tr("Custom Graph"), self.custom)):
            self.workspace.addItem(name)
            self.stack.addWidget(ws)
            ws.message.connect(self.show_message)
        self.workspace.currentIndexChanged.connect(self._workspace_changed)

        # -- progress, preview, history
        self.progress = JobProgress(self.cancel_current, conn.queue.cancel_all)
        self.preview = Preview()
        self.history = HistoryList()
        self.history.apply_requested.connect(self.apply)
        self.history.preview_requested.connect(self.show_preview)
        self.history.discard_requested.connect(self.discard)
        self.history.reuse_seed.connect(self.generate.seed.set_value)
        self.history.reuse_prompt.connect(lambda p: (self.generate.prompt.setPlainText(p), QGuiApplication.clipboard().setText(p)))
        self.apply_button = QToolButton()
        self.apply_button.setText("✔ " + tr("Apply"))
        self.apply_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.apply_button.clicked.connect(self.apply_selected)
        self.results = QWidget()
        results_lay = QVBoxLayout(self.results)
        results_lay.setContentsMargins(0, 0, 0, 0)
        hist_header = QHBoxLayout()
        hist_header.addWidget(QLabel(tr("History")))
        hist_header.addStretch(1)
        hist_header.addWidget(self.apply_button)
        results_lay.addWidget(self.preview, 2)
        results_lay.addLayout(hist_header)
        results_lay.addWidget(self.history, 3)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: #d66;")

        top = QWidget()
        top_lay = QVBoxLayout(top)
        top_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.addLayout(header)
        top_lay.addLayout(style_row)
        top_lay.addWidget(self.stack)
        top_lay.addWidget(self.progress)
        top_lay.addWidget(self.message)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(self.results)
        split.setStretchFactor(1, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(split)

        conn.state_changed.connect(self.update_status)
        conn.job_updated.connect(self.on_job)
        conn.document_changed.connect(self.on_document)
        self.update_status()

        # Keep the document summary (selection, layers) fresh while the user works in PhotoSuite.
        self._last_rev = None
        self.doc_timer = QTimer(self)
        self.doc_timer.timeout.connect(self._poll_document)
        self.doc_timer.start(1500)

    # -- state --------------------------------------------------------------------------------

    def update_status(self) -> None:
        c = self.conn

        def dot(ok: bool) -> str:
            return f"<span style='color:{'#4c4' if ok else '#c44'}'>●</span>"

        if c.connecting:
            self.status.setText(tr("Connecting…"))
        else:
            self.status.setText(f"{dot(c.comfy_ok)} ComfyUI {dot(c.photosuite_ok)} PhotoSuite")
        tips = [f"ComfyUI: {c.comfy_error or (tr('Connected') if c.comfy_ok else tr('Disconnected'))}", f"PhotoSuite: {c.photosuite_error or (tr('Connected') if c.photosuite_ok else tr('Disconnected'))}"]
        self.status.setToolTip("\n".join(tips))
        if not c.connecting and (c.comfy_error or c.photosuite_error):
            self.show_message(c.comfy_error or c.photosuite_error)
        elif c.ready:
            self.show_message("")
        for ws in (self.generate, self.upscale, self.live, self.custom):
            ws.on_models()

    def _poll_document(self) -> None:
        if not self.conn.photosuite_ok or self.conn.queue.pending() > 0 or self.conn.live_queue.pending() > 0:
            return
        rev = self.conn.revision()
        if rev != self._last_rev:
            self._last_rev = rev
            self.conn.refresh_document()

    def on_document(self, doc) -> None:
        for ws in (self.generate, self.upscale, self.live, self.custom):
            ws.on_document(doc)

    def show_message(self, text: str) -> None:
        self.message.setText(text)

    def reload_styles(self, select: str = "") -> None:
        self.style_combo.blockSignals(True)
        self.style_combo.clear()
        for s in self.conn.styles.styles:
            self.style_combo.addItem(s.name, s.name)
        i = self.style_combo.findData(select)
        self.style_combo.setCurrentIndex(max(0, i))
        self.style_combo.blockSignals(False)

    def _style_changed(self) -> None:
        self.conn.settings.style = self.style_combo.currentData() or ""
        self.conn.settings.save()

    def _workspace_changed(self, index: int) -> None:
        if self.stack.currentWidget() is self.live and index != 2:
            self.live.stop()
        self.stack.setCurrentIndex(index)
        self.results.setVisible(self.stack.currentWidget() is not self.live)

    # -- jobs ---------------------------------------------------------------------------------

    def current_job(self) -> Job | None:
        running = [j for j in self.conn.queue.jobs if j.state == JobState.RUNNING]
        if running:
            return running[0]
        queued = [j for j in self.conn.queue.jobs if j.state == JobState.QUEUED]
        return queued[0] if queued else None

    def on_job(self, job: Job) -> None:
        current = self.current_job()
        self.progress.show_job(current or job, self.conn.queue.pending())
        if job.state == JobState.DONE and job.result is not None:
            if job.kind == "upscale":
                # Upscales become new documents straight away, like Image Size would.
                self.apply(job, 0)
            else:
                self.history.add_job(job)
        elif job.state == JobState.FAILED:
            self.show_message(job.error)
        if job.finished and job.state != JobState.FAILED and self.conn.queue.pending() == 0:
            self.show_message("")

    def cancel_current(self) -> None:
        job = self.current_job()
        if job is not None:
            self.conn.queue.cancel(job)

    # -- results ------------------------------------------------------------------------------

    def show_preview(self, job: Job, index: int) -> None:
        r = job.result
        if r is None or index >= len(r.images):
            return
        image = r.images[index]
        self.preview.set_image(apply_mask(image, r.mask) if r.into_selection else image)

    def apply_selected(self) -> None:
        sel = self.history.selected()
        if sel is not None:
            self.apply(*sel)

    def apply(self, job: Job, index: int) -> None:
        if job.result is None or self.conn.generator is None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.conn.generator.apply(job.result, index)
            self.show_message("")
        except PhotoSuiteError as e:
            self.show_message(str(e))
        finally:
            QApplication.restoreOverrideCursor()
        self.conn.refresh_document()

    def discard(self, job: Job) -> None:
        self.history.remove_job(job)
        self.conn.queue.remove(job)
        self.preview.set_image(None)

    # -- dialogs ------------------------------------------------------------------------------

    def open_settings(self) -> None:
        old_lang = self.conn.settings.language
        dlg = SettingsDialog(self.conn.settings, self)
        if dlg.exec():
            self.conn.queue.history_size = self.conn.settings.history_size
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.conn.settings.always_on_top)
            self.show()
            if self.conn.settings.language != old_lang:
                QMessageBox.information(self, tr("Settings"), tr("Restart required to change the language."))
            self.conn.connect_all()

    def edit_style(self) -> None:
        style = self.conn.styles.get(self.style_combo.currentData() or "")
        dlg = StyleDialog(self.conn, style, self)
        if dlg.exec():
            self.reload_styles(dlg.saved_name)
            self._style_changed()

    def closeEvent(self, e) -> None:  # noqa: N802 - Qt override
        self.live.stop()
        self.conn.shutdown()
        super().closeEvent(e)
