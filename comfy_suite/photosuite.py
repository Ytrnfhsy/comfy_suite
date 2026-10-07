"""Talking to PhotoSuite over its JSON control channel.

PhotoSuite listens on a loopback TCP port (``photosuite --control <port>``, or headless
``photosuite-cli serve --port <port>``). Every connection authenticates with a bearer token
first, then sends one JSON request per line and gets one JSON reply per line.

Pixels do not travel over the socket: PhotoSuite reads and writes files only below the
*automation roots* it was launched with. ``PhotoSuiteBridge`` therefore uses an exchange
folder that is both the read and the write root, and moves images through PNG files there.
"""

from __future__ import annotations

import itertools
import json
import socket
import threading
import uuid
from functools import wraps
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from PIL import Image

from .image_ops import Bounds


class PhotoSuiteError(Exception):
    pass


def read_token(token: str | None = None, token_file: str | Path | None = None) -> str:
    if token:
        return token.strip()
    if token_file:
        try:
            return Path(token_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError as e:
            raise PhotoSuiteError(f"cannot read the control token file {token_file}: {e}") from e
    raise PhotoSuiteError("no control token: set a token or a token file")


class ControlClient:
    """One authenticated JSON-lines connection to PhotoSuite."""

    def __init__(self, host: str = "127.0.0.1", port: int = 7878, token: str = "", timeout: float = 120.0):
        self.host = host
        self.port = port
        self.token = token
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = b""
        self._ids = itertools.count(1)
        # One request at a time; re-entrant so multi-step bridge operations can hold it.
        self.lock = threading.RLock()

    # -- connection ---------------------------------------------------------------------------

    def connect(self) -> None:
        self.close()
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise PhotoSuiteError(f"cannot connect to PhotoSuite at {self.host}:{self.port}: {e}") from e
        self._sock = sock
        self._buf = b""
        reply = self._roundtrip({"id": "auth", "method": "auth", "params": {"token": self.token}})
        if not reply.get("ok"):
            self.close()
            raise PhotoSuiteError(f"PhotoSuite refused the token: {reply.get('error', 'unknown error')}")

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def __enter__(self) -> "ControlClient":
        if not self.connected:
            self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- requests -----------------------------------------------------------------------------

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send one request and return its ``result``; raises ``PhotoSuiteError`` on failure."""
        with self.lock:
            return self._call(method, params)

    def _call(self, method: str, params: dict[str, Any] | None) -> Any:
        if self._sock is None:
            self.connect()
        req_id = next(self._ids)
        try:
            reply = self._roundtrip({"id": req_id, "method": method, "params": params or {}}, req_id)
        except (OSError, PhotoSuiteError):
            # One reconnect: the app may have been restarted or the socket timed out.
            self.connect()
            req_id = next(self._ids)
            reply = self._roundtrip({"id": req_id, "method": method, "params": params or {}}, req_id)
        if not reply.get("ok"):
            raise PhotoSuiteError(f"{method}: {reply.get('error', 'unknown error')}")
        return reply.get("result")

    def execute(self, command: str, params: dict[str, Any] | None = None) -> Any:
        return self.call("engine.execute", {"command": command, "params": params or {}})

    def _roundtrip(self, request: dict[str, Any], expect_id: Any = "auth") -> dict[str, Any]:
        sock = self._sock
        if sock is None:
            raise PhotoSuiteError("not connected")
        sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
        while True:
            line = self._read_line()
            try:
                reply = json.loads(line)
            except json.JSONDecodeError as e:
                raise PhotoSuiteError(f"malformed reply from PhotoSuite: {e}") from e
            # Skip stale replies to earlier requests that timed out.
            if reply.get("id") == expect_id:
                return reply

    def _read_line(self) -> bytes:
        sock = self._sock
        if sock is None:
            raise PhotoSuiteError("not connected")
        while b"\n" not in self._buf:
            chunk = sock.recv(1 << 16)
            if not chunk:
                self.close()
                raise PhotoSuiteError("PhotoSuite closed the connection")
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line


def _locked(fn):
    """Hold the connection for a whole multi-step operation, so another thread's requests
    can't switch the active document halfway through."""

    @wraps(fn)
    def inner(self: "PhotoSuiteBridge", *args: Any, **kwargs: Any) -> Any:
        with self.client.lock:
            return fn(self, *args, **kwargs)

    return inner


# ---- high level ----------------------------------------------------------------------------------


@dataclass
class LayerInfo:
    id: int
    name: str
    kind: str
    visible: bool
    depth: int = 0
    children: list["LayerInfo"] = field(default_factory=list)

    def walk(self) -> Iterator["LayerInfo"]:
        yield self
        for c in self.children:
            yield from c.walk()


@dataclass
class DocumentInfo:
    index: int
    name: str
    width: int
    height: int
    revision: int
    selection: Bounds | None
    active_layer: int | None
    layers: list[LayerInfo]

    @property
    def bounds(self) -> Bounds:
        return Bounds(0, 0, self.width, self.height)

    def all_layers(self) -> list[LayerInfo]:
        return [x for top in self.layers for x in top.walk()]

    def path_to(self, layer_id: int) -> list[LayerInfo]:
        """The layer and its parent groups, outermost first (empty when not found)."""

        def search(nodes: list[LayerInfo], trail: list[LayerInfo]) -> list[LayerInfo]:
            for n in nodes:
                if n.id == layer_id:
                    return trail + [n]
                found = search(n.children, trail + [n])
                if found:
                    return found
            return []

        return search(self.layers, [])


def _parse_layers(items: list[dict[str, Any]], depth: int = 0) -> list[LayerInfo]:
    out = []
    for it in items or []:
        out.append(
            LayerInfo(
                id=int(it.get("id", 0)),
                name=str(it.get("name", "")),
                kind=str(it.get("kind", "")),
                visible=bool(it.get("visible", True)),
                depth=depth,
                children=_parse_layers(it.get("children") or [], depth + 1),
            )
        )
    return out


class PhotoSuiteBridge:
    """Image round trips between PhotoSuite documents and Pillow images.

    ``mode`` is ``"desktop"`` (the app's ``app.open``/``app.save``) or ``"headless"``
    (``photosuite-cli serve``'s ``doc.open``/``doc.save``); ``"auto"`` detects it.
    """

    def __init__(self, client: ControlClient, exchange_dir: str | Path, mode: str = "auto"):
        self.client = client
        self.exchange_dir = Path(exchange_dir).expanduser()
        self.mode = mode

    # -- plumbing -----------------------------------------------------------------------------

    def _detect(self) -> str:
        if self.mode == "auto":
            try:
                methods = self.client.call("methods")
                self.mode = "headless" if isinstance(methods, (list, dict)) else "desktop"
            except PhotoSuiteError:
                self.mode = "desktop"
        return self.mode

    def _open(self, rel: str) -> None:
        self.client.call("app.open" if self._detect() == "desktop" else "doc.open", {"path": rel})

    def _save(self, rel: str) -> None:
        self.client.call("app.save" if self._detect() == "desktop" else "doc.save", {"path": rel})

    def _new_name(self, prefix: str) -> str:
        self.exchange_dir.mkdir(parents=True, exist_ok=True)
        return f"{prefix}-{uuid.uuid4().hex[:12]}.png"

    def _take(self, rel: str) -> Image.Image:
        path = self.exchange_dir / rel
        try:
            with Image.open(path) as im:
                im.load()
                return im.copy()
        except OSError as e:
            raise PhotoSuiteError(
                f"PhotoSuite saved {rel}, but it is not in {self.exchange_dir}. Launch PhotoSuite with "
                f"--automation-read-root and --automation-write-root set to the exchange folder."
            ) from e
        finally:
            path.unlink(missing_ok=True)

    # -- queries ------------------------------------------------------------------------------

    def ping(self) -> dict[str, Any]:
        return self.client.execute("session.inspect")

    def session(self) -> dict[str, Any]:
        return self.client.execute("session.inspect")

    @_locked
    def document(self, index: int | None = None) -> DocumentInfo:
        session = self.session()
        active = session.get("active") if index is None else index
        if active is None:
            raise PhotoSuiteError("no open document in PhotoSuite")
        d = self.client.execute("document.inspect", {"document": active})
        sel = d.get("selectionBounds") if d.get("hasSelection") else None
        return DocumentInfo(
            index=int(active),
            name=str(d.get("name", "")),
            width=int(d.get("width", 0)),
            height=int(d.get("height", 0)),
            revision=int(d.get("revision", 0)),
            selection=Bounds(*sel) if sel and sel[2] > 0 and sel[3] > 0 else None,
            active_layer=d.get("activeLayer"),
            layers=_parse_layers(d.get("layers") or []),
        )

    # -- export -------------------------------------------------------------------------------

    def _with_duplicate(self, doc: DocumentInfo, merged: bool, work) -> Any:
        """Run ``work(dup_index)`` on a throwaway copy of ``doc`` so the user's document (its
        path, history and saved state) is never touched, then close the copy."""
        self.client.execute("document.activate", {"document": doc.index})
        dup = self.client.execute("image.duplicate", {"name": "comfy-suite export", "mergedOnly": merged})
        dup_index = int(dup["document"])
        try:
            return work(dup_index)
        finally:
            try:
                self.client.execute("file.close", {"document": dup_index})
            finally:
                self.client.execute("document.activate", {"document": doc.index})

    @_locked
    def export_canvas(self, doc: DocumentInfo, with_mask: bool = False) -> tuple[Image.Image, Image.Image | None]:
        """The visible composite (RGBA) and, when asked and there is a selection, the selection
        as a grayscale coverage mask (``L``), both at canvas size."""

        def work(_dup: int) -> tuple[Image.Image, Image.Image | None]:
            rel = self._new_name("canvas")
            self._save(rel)
            canvas = self._take(rel).convert("RGBA")
            mask = None
            if with_mask and doc.selection is not None:
                # The copy keeps the selection: fill it white on a new layer over a hidden
                # background, and the PNG's alpha is exactly the (feathered) coverage.
                info = self.client.execute("document.inspect", {})
                bottom = [l["id"] for l in info.get("layers", [])]
                self.client.execute("layer.new.layer", {"name": "mask"})
                self.client.execute("edit.fill", {"contents": "white"})
                for lid in bottom:
                    self.client.execute("layer.setProps", {"layer": lid, "visible": False})
                rel = self._new_name("mask")
                self._save(rel)
                mask = self._take(rel).convert("RGBA").getchannel("A")
            return canvas, mask

        return self._with_duplicate(doc, merged=True, work=work)

    @_locked
    def export_layer(self, doc: DocumentInfo, layer_id: int) -> Image.Image:
        """One layer (with its parent groups' visibility forced on) composited alone."""
        path = doc.path_to(layer_id)
        if not path:
            raise PhotoSuiteError(f"layer {layer_id} is not in {doc.name}")

        def work(_dup: int) -> Image.Image:
            # The duplicate gets fresh layer ids; map by tree position instead.
            info = self.client.execute("document.inspect", {})
            dup_layers = _parse_layers(info.get("layers") or [])
            mapping = _map_by_position(doc.layers, dup_layers)
            keep = {mapping[x.id] for x in path if x.id in mapping}
            target = mapping.get(layer_id)
            for orig in doc.all_layers():
                dup_id = mapping.get(orig.id)
                if dup_id is None:
                    continue
                if dup_id in keep:
                    visible = True
                elif target is not None and _is_descendant(dup_layers, target, dup_id):
                    visible = orig.visible
                else:
                    visible = False
                if visible != orig.visible:
                    self.client.execute("layer.setProps", {"layer": dup_id, "visible": visible})
            rel = self._new_name("layer")
            self._save(rel)
            return self._take(rel).convert("RGBA")

        return self._with_duplicate(doc, merged=False, work=work)

    # -- import -------------------------------------------------------------------------------

    @_locked
    def place_image(
        self,
        doc: DocumentInfo,
        image: Image.Image,
        at: Bounds,
        name: str = "",
        into_selection: bool = False,
    ) -> int | None:
        """Add ``image`` (already sized to ``at``) as a new layer of ``doc`` at ``at``.

        With ``into_selection`` it is pasted with Paste Into, so the selection becomes its layer
        mask, the way a generative fill should land. Returns the new layer id when reported.
        """
        rel = self._new_name("result")
        image.convert("RGBA").save(self.exchange_dir / rel)
        try:
            self._open(rel)
            session = self.session()
            tmp_index = session.get("active")
            try:
                self.client.execute("select.all")
                self.client.execute("edit.copy")
            finally:
                if tmp_index is not None and tmp_index != doc.index:
                    self.client.execute("file.close", {"document": tmp_index})
            self.client.execute("document.activate", {"document": doc.index})
            center = [at.x + at.width / 2, at.y + at.height / 2]
            if into_selection and doc.selection is not None:
                r = self.client.execute("edit.pasteSpecial.pasteInto", {"center": center})
            else:
                r = self.client.execute("edit.paste", {"center": center})
            layer = r.get("layer") if isinstance(r, dict) else None
            if layer is not None and name:
                self.client.execute("layer.setProps", {"layer": layer, "name": name})
            if doc.selection is not None:
                # Pasting drops the selection; give it back so the user can generate again.
                try:
                    self.client.execute("select.reselect")
                except PhotoSuiteError:
                    pass
            return layer
        finally:
            (self.exchange_dir / rel).unlink(missing_ok=True)

    @_locked
    def open_image(self, image: Image.Image, prefix: str = "upscaled") -> None:
        """Open ``image`` as a new document (e.g. an upscaled copy)."""
        rel = self._new_name(prefix)
        image.save(self.exchange_dir / rel)
        try:
            self._open(rel)
        finally:
            (self.exchange_dir / rel).unlink(missing_ok=True)

    def delete_layer(self, layer_id: int) -> None:
        self.client.execute("layer.delete", {"layer": layer_id})


def _map_by_position(a: list[LayerInfo], b: list[LayerInfo]) -> dict[int, int]:
    out: dict[int, int] = {}
    for x, y in zip(a, b):
        out[x.id] = y.id
        out.update(_map_by_position(x.children, y.children))
    return out


def _is_descendant(layers: list[LayerInfo], ancestor: int, node: int) -> bool:
    for top in layers:
        for x in top.walk():
            if x.id == ancestor:
                return any(c.id == node for c in x.walk())
    return False
