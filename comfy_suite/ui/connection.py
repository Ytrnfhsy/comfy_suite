"""The connections to ComfyUI and PhotoSuite, shared by every workspace."""

from __future__ import annotations

import threading

from PyQt6.QtCore import QObject, pyqtSignal

from ..comfy import ComfyClient, ComfyError, ServerModels
from ..generator import Generator
from ..jobs import Job, JobQueue
from ..photosuite import ControlClient, DocumentInfo, PhotoSuiteBridge, PhotoSuiteError, read_token
from ..settings import Settings
from ..styles import StyleLibrary


class Connection(QObject):
    """Owns the clients, the job queue and the style library; emits Qt signals from any thread."""

    state_changed = pyqtSignal()  # connection state or models changed
    job_updated = pyqtSignal(object)  # Job, delivered on the GUI thread
    document_changed = pyqtSignal(object)  # DocumentInfo | None

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.styles = StyleLibrary(settings.styles_path())
        self.models = ServerModels()
        self.comfy_ok = False
        self.photosuite_ok = False
        self.comfy_error = ""
        self.photosuite_error = ""
        self.connecting = False
        self.comfy: ComfyClient | None = None
        self.bridge: PhotoSuiteBridge | None = None
        self.generator: Generator | None = None
        self.queue = JobQueue(settings.history_size)
        self.queue.subscribe(self.job_updated.emit)
        # Live painting has its own queue so it never waits behind (or blocks) normal jobs.
        self.live_queue = JobQueue(history_size=1)
        self.document: DocumentInfo | None = None

    # -- connecting ---------------------------------------------------------------------------

    def connect_all(self) -> None:
        if self.connecting:
            return
        self.connecting = True
        self.state_changed.emit()
        threading.Thread(target=self._connect, name="comfy-suite-connect", daemon=True).start()

    def _connect(self) -> None:
        s = self.settings
        comfy = ComfyClient(s.comfy_url)
        try:
            comfy.system_stats()
            self.models = comfy.models(refresh=True)
            self.comfy_ok, self.comfy_error = True, ""
        except ComfyError as e:
            self.comfy_ok, self.comfy_error = False, str(e)
        self.comfy = comfy

        try:
            token = read_token(s.photosuite_token, None if s.photosuite_token else s.token_file_path())
            client = ControlClient(s.photosuite_host, s.photosuite_port, token)
            client.connect()
            bridge = PhotoSuiteBridge(client, s.exchange_path())
            bridge.ping()
            if self.bridge is not None:
                self.bridge.client.close()
            self.bridge = bridge
            self.photosuite_ok, self.photosuite_error = True, ""
        except (PhotoSuiteError, OSError) as e:
            self.photosuite_ok, self.photosuite_error = False, str(e)

        if self.bridge is not None:
            self.generator = Generator(self.bridge, comfy, s)
        self.connecting = False
        self.state_changed.emit()
        self.refresh_document()

    @property
    def ready(self) -> bool:
        return self.comfy_ok and self.photosuite_ok and self.generator is not None

    def disconnect_all(self) -> None:
        if self.bridge is not None:
            self.bridge.client.close()
        self.photosuite_ok = False
        self.state_changed.emit()

    # -- document -----------------------------------------------------------------------------

    def refresh_document(self) -> DocumentInfo | None:
        if self.bridge is None or not self.photosuite_ok:
            self.document = None
        else:
            try:
                self.document = self.bridge.document()
            except PhotoSuiteError:
                self.document = None
        self.document_changed.emit(self.document)
        return self.document

    def revision(self) -> tuple[int | None, int] | None:
        """(active document index, revision): cheap change detection for live painting."""
        if self.bridge is None or not self.photosuite_ok:
            return None
        try:
            s = self.bridge.session()
        except PhotoSuiteError:
            return None
        active = s.get("active")
        for d in s.get("documents", []):
            if d.get("index") == active:
                return active, int(d.get("revision", 0))
        return None

    def shutdown(self) -> None:
        self.queue.shutdown()
        self.live_queue.shutdown()
        if self.bridge is not None:
            self.bridge.client.close()

    def submit(self, kind: str, title: str, work, **meta) -> Job:
        return self.queue.submit(kind, title, work, **meta)
