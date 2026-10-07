"""Smoke tests for the panel, offscreen."""

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PIL import Image  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from comfy_suite.generator import Result  # noqa: E402
from comfy_suite.image_ops import Bounds  # noqa: E402
from comfy_suite.jobs import Job, JobState  # noqa: E402
from comfy_suite.settings import Settings  # noqa: E402
from photosuite_server import photosuite  # noqa: E402,F401 - fixture


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("COMFY_SUITE_CONFIG_DIR", str(tmp_path))
    from comfy_suite.ui.connection import Connection
    from comfy_suite.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    conn = Connection(Settings())
    win = MainWindow(conn)
    yield win, conn, app
    win.close()


def test_panel_builds_and_switches_workspaces(window):
    win, conn, app = window
    assert win.workspace.count() == 4
    assert win.style_combo.count() >= 4
    for i in range(4):
        win.workspace.setCurrentIndex(i)
        app.processEvents()
    assert not win.results.isVisible() or win.stack.currentWidget() is not win.live
    win.generate.add_control()
    assert win.generate.params().controls[0].mode == "scribble"
    win.generate.strength.set_value(0.5)
    assert win.generate.button.text() in ("Refine", "Доопрацювати")


def test_history_shows_finished_jobs(window):
    win, conn, app = window
    job = Job(1, "generate", "cat", lambda p, c: None, state=JobState.DONE)
    job.result = Result([Image.new("RGBA", (64, 32), (255, 0, 0, 255))] * 2, 0, Bounds(0, 0, 64, 32), False, 3, "cat")
    win.on_job(job)
    assert win.history.count() == 2
    win.discard(job)
    assert win.history.count() == 0


def test_generate_without_connection_reports(window):
    win, conn, app = window
    win.generate.generate()
    assert win.message.text()


def test_dialogs_build(window):
    win, conn, app = window
    from comfy_suite.ui.dialogs import SettingsDialog, StyleDialog

    s = SettingsDialog(conn.settings, win)
    assert s.collect().comfy_url == conn.settings.comfy_url
    d = StyleDialog(conn, conn.styles.get("Flux"), win)
    assert d.collect().architecture == "flux"


def _wait(app, cond, timeout=15.0):
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_end_to_end_generate_and_live(window, photosuite):  # noqa: F811
    from fake_comfy import FakeComfy

    win, conn, app = window
    with FakeComfy(color=(0, 0, 255)) as fake:
        conn.settings.comfy_url = fake.url
        conn.settings.photosuite_port = photosuite["port"]
        conn.settings.photosuite_token_file = str(photosuite["token_file"])
        conn.settings.exchange_dir = str(photosuite["exchange"])
        conn.connect_all()
        assert _wait(app, lambda: conn.ready), (conn.comfy_error, conn.photosuite_error)
        conn.bridge.client.call("doc.new", {"width": 160, "height": 120, "background": "white"})
        conn.refresh_document()

        win.generate.prompt.setPlainText("blue sky")
        win.generate.batch.setValue(2)
        win.generate.generate()
        assert _wait(app, lambda: win.history.count() == 2), win.message.text()
        win.apply_selected()
        doc = conn.refresh_document()
        assert doc.layers[0].name.startswith("[AI] blue sky")

        win.workspace.setCurrentIndex(2)
        win.live.strength.set_value(0.5)
        win.live.toggle.setChecked(True)
        assert _wait(app, lambda: win.live.last_job is not None), win.live.status.text()
        win.live.stop()
        win.live.apply()
        assert len(conn.refresh_document().layers) == 3
