import json
import socket
import threading

import pytest
from photosuite_server import photosuite  # noqa: F401 - fixture
from PIL import Image

from comfy_suite.image_ops import Bounds
from comfy_suite.photosuite import ControlClient, PhotoSuiteBridge, PhotoSuiteError, read_token


class FakeControl:
    """Accepts one connection: checks the auth frame, then answers every request."""

    def __init__(self, token: str = "t" * 64):
        self.token = token
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.requests = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        conn, _ = self.sock.accept()
        f = conn.makefile("rwb")
        for line in f:
            req = json.loads(line)
            self.requests.append(req)
            if req["method"] == "auth":
                ok = req["params"]["token"] == self.token
                reply = {"id": "auth", "ok": ok, "result": None} if ok else {"id": "auth", "ok": False, "error": "bad token"}
            elif req["method"] == "boom":
                reply = {"id": req["id"], "ok": False, "error": "it broke"}
            else:
                # A stale reply first: the client must skip it.
                f.write(json.dumps({"id": -1, "ok": True, "result": "stale"}).encode() + b"\n")
                reply = {"id": req["id"], "ok": True, "result": {"echo": req["method"], "params": req["params"]}}
            f.write(json.dumps(reply).encode() + b"\n")
            f.flush()
            if not reply["ok"] and req["method"] == "auth":
                break
        conn.close()


def test_client_auth_and_calls():
    srv = FakeControl()
    with ControlClient(port=srv.port, token=srv.token) as c:
        assert c.execute("document.inspect", {"document": 0}) == {"echo": "engine.execute", "params": {"command": "document.inspect", "params": {"document": 0}}}
        with pytest.raises(PhotoSuiteError, match="it broke"):
            c.call("boom")
    assert srv.requests[0]["method"] == "auth"


def test_client_rejects_bad_token():
    srv = FakeControl()
    with pytest.raises(PhotoSuiteError, match="refused"):
        ControlClient(port=srv.port, token="wrong").connect()


def test_read_token(tmp_path):
    (tmp_path / "t").write_text("abc\n")
    assert read_token(token_file=tmp_path / "t") == "abc"
    assert read_token(" x ") == "x"
    with pytest.raises(PhotoSuiteError):
        read_token(token_file=tmp_path / "missing")


# ---- against the real engine --------------------------------------------------------------------


def _bridge(ps) -> PhotoSuiteBridge:
    client = ControlClient(port=ps["port"], token=read_token(token_file=ps["token_file"]))
    return PhotoSuiteBridge(client, ps["exchange"])


def test_round_trip_with_selection(photosuite):  # noqa: F811
    b = _bridge(photosuite)
    c = b.client
    c.call("doc.new", {"width": 200, "height": 120, "background": "white"})
    c.execute("layer.new.layer", {"name": "Ink"})
    c.execute("paint.stroke", {"points": [[10, 10, 1], [190, 110, 1]], "size": 20, "color": "#ff0000"})
    c.execute("select.rect", {"x": 50, "y": 20, "width": 60, "height": 40})
    doc = b.document()
    assert doc.selection == Bounds(50, 20, 60, 40)
    assert [l.name for l in doc.layers] == ["Ink", "Background"]

    canvas, mask = b.export_canvas(doc, with_mask=True)
    assert canvas.size == (200, 120) and canvas.getpixel((10, 10))[:3] == (255, 0, 0)
    assert mask.getpixel((80, 40)) == 255 and mask.getpixel((10, 10)) == 0

    ink = b.export_layer(doc, doc.layers[0].id)
    assert ink.getpixel((10, 10))[3] == 255 and ink.getpixel((5, 110))[3] == 0  # background hidden

    # Exports work on a throwaway copy: the document is unchanged and still active.
    assert b.session()["active"] == 0 and len(b.session()["documents"]) == 1
    assert b.document().revision == doc.revision

    layer = b.place_image(doc, Image.new("RGBA", (80, 60), (0, 0, 255, 255)), Bounds(40, 10, 80, 60), "AI", into_selection=True)
    after = b.document()
    assert after.layers[0].name == "AI" and after.layers[0].id == layer
    assert after.selection == doc.selection  # restored after pasting
    assert c.execute("document.pixel", {"x": 80, "y": 40})[:3] == [0.0, 0.0, 1.0]  # inside the selection
    assert c.execute("document.pixel", {"x": 45, "y": 15})[:3] == [1.0, 1.0, 1.0]  # masked outside
    assert list(photosuite["exchange"].iterdir()) == []  # temp files cleaned up


def test_place_without_selection_and_open(photosuite):  # noqa: F811
    b = _bridge(photosuite)
    b.client.call("doc.new", {"width": 64, "height": 64, "background": "white"})
    doc = b.document()
    b.place_image(doc, Image.new("RGBA", (32, 32), (0, 255, 0, 255)), Bounds(32, 32, 32, 32))
    assert b.client.execute("document.pixel", {"x": 40, "y": 40})[:3] == [0.0, 1.0, 0.0]
    assert b.client.execute("document.pixel", {"x": 10, "y": 10})[:3] == [1.0, 1.0, 1.0]
    b.open_image(Image.new("RGB", (128, 96), (9, 9, 9)))
    s = b.session()
    assert len(s["documents"]) == 2 and s["documents"][s["active"]]["width"] == 128
