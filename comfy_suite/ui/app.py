"""Starting the panel."""

from __future__ import annotations

import sys

from ..i18n import set_language
from ..settings import Settings


def run(settings: Settings | None = None, workflow: str | None = None) -> int:
    from PyQt6.QtWidgets import QApplication

    from .connection import Connection
    from .main_window import MainWindow

    settings = settings or Settings.load()
    if settings.language:
        set_language(settings.language)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("ComfySuite")
    conn = Connection(settings)
    win = MainWindow(conn)
    if workflow:
        win.custom.load(workflow)
    win.show()
    conn.connect_all()
    return app.exec()
