"""A small ComfyUI client: model discovery, uploads, queueing, progress and results.

Uses only ComfyUI's built-in HTTP API and its ``/ws`` progress socket, so it needs no custom
nodes on the server.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from PIL import Image

from .image_ops import from_bytes, to_png_bytes


class ComfyError(Exception):
    pass


class Cancelled(ComfyError):
    pass


@dataclass
class ServerModels:
    """What the server has installed (file lists come from the loader nodes' choices)."""

    checkpoints: list[str] = field(default_factory=list)
    diffusion_models: list[str] = field(default_factory=list)
    vaes: list[str] = field(default_factory=list)
    loras: list[str] = field(default_factory=list)
    controlnets: list[str] = field(default_factory=list)
    upscalers: list[str] = field(default_factory=list)
    ipadapters: list[str] = field(default_factory=list)
    clip_vision: list[str] = field(default_factory=list)
    samplers: list[str] = field(default_factory=list)
    schedulers: list[str] = field(default_factory=list)
    nodes: set[str] = field(default_factory=set)
    info: dict[str, Any] = field(default_factory=dict, repr=False)  # raw /object_info

    def has(self, *node_types: str) -> bool:
        return all(n in self.nodes for n in node_types)


def _choices(info: dict[str, Any], node: str, input_name: str) -> list[str]:
    try:
        spec = info[node]["input"]
        entry = spec.get("required", {}).get(input_name) or spec.get("optional", {}).get(input_name)
    except (KeyError, AttributeError, TypeError):
        return []
    if not entry:
        return []
    first = entry[0]
    if isinstance(first, list):
        return [str(x) for x in first]
    # Newer ComfyUI: ["COMBO", {"options": [...]}]
    if first == "COMBO" and len(entry) > 1 and isinstance(entry[1], dict):
        return [str(x) for x in entry[1].get("options", [])]
    return []


def parse_models(info: dict[str, Any]) -> ServerModels:
    return ServerModels(
        checkpoints=_choices(info, "CheckpointLoaderSimple", "ckpt_name"),
        diffusion_models=_choices(info, "UNETLoader", "unet_name"),
        vaes=_choices(info, "VAELoader", "vae_name"),
        loras=_choices(info, "LoraLoader", "lora_name"),
        controlnets=_choices(info, "ControlNetLoader", "control_net_name"),
        upscalers=_choices(info, "UpscaleModelLoader", "model_name"),
        ipadapters=_choices(info, "IPAdapterModelLoader", "ipadapter_file"),
        clip_vision=_choices(info, "CLIPVisionLoader", "clip_name"),
        samplers=_choices(info, "KSampler", "sampler_name"),
        schedulers=_choices(info, "KSampler", "scheduler"),
        nodes=set(info.keys()),
        info=info,
    )


ProgressFn = Callable[[float, str], None]
PreviewFn = Callable[[Image.Image], None]


class ComfyClient:
    def __init__(self, url: str = "http://127.0.0.1:8188", timeout: float = 30.0):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.client_id = uuid.uuid4().hex
        self._models: ServerModels | None = None

    # -- HTTP ---------------------------------------------------------------------------------

    def _request(self, path: str, data: bytes | None = None, headers: dict[str, str] | None = None, method: str | None = None) -> bytes:
        req = urllib.request.Request(self.url + path, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise ComfyError(f"ComfyUI {path}: HTTP {e.code}: {_error_text(body)}") from e
        except (urllib.error.URLError, OSError) as e:
            raise ComfyError(f"cannot reach ComfyUI at {self.url}: {e}") from e

    def get_json(self, path: str) -> Any:
        return json.loads(self._request(path))

    def post_json(self, path: str, payload: Any) -> Any:
        raw = self._request(path, json.dumps(payload).encode("utf-8"), {"Content-Type": "application/json"}, "POST")
        return json.loads(raw) if raw.strip() else None

    # -- server info --------------------------------------------------------------------------

    def system_stats(self) -> dict[str, Any]:
        return self.get_json("/system_stats")

    def models(self, refresh: bool = False) -> ServerModels:
        if self._models is None or refresh:
            self._models = parse_models(self.get_json("/object_info"))
        return self._models

    # -- images -------------------------------------------------------------------------------

    def upload_image(self, image: Image.Image, name: str | None = None) -> str:
        """Upload to ComfyUI's input folder; returns the name ``LoadImage`` takes."""
        name = name or f"comfy-suite-{uuid.uuid4().hex[:12]}.png"
        boundary = uuid.uuid4().hex
        parts = [
            _form_field(boundary, "overwrite", "true"),
            _form_field(boundary, "type", "input"),
            _form_field(boundary, "subfolder", "comfy-suite"),
            (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
                "Content-Type: image/png\r\n\r\n"
            ).encode()
            + to_png_bytes(image)
            + b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        raw = self._request("/upload/image", b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"}, "POST")
        r = json.loads(raw)
        sub = r.get("subfolder") or ""
        return f"{sub}/{r['name']}" if sub else r["name"]

    def view(self, filename: str, subfolder: str = "", type_: str = "output") -> Image.Image:
        q = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": type_})
        return from_bytes(self._request(f"/view?{q}"))

    # -- running ------------------------------------------------------------------------------

    def queue(self, graph: dict[str, Any]) -> str:
        r = self.post_json("/prompt", {"prompt": graph, "client_id": self.client_id})
        if not r or "prompt_id" not in r:
            raise ComfyError(f"ComfyUI did not accept the workflow: {r}")
        if r.get("node_errors"):
            raise ComfyError(f"workflow errors: {json.dumps(r['node_errors'])[:800]}")
        return r["prompt_id"]

    def interrupt(self) -> None:
        self.post_json("/interrupt", {})

    def cancel(self, prompt_id: str) -> None:
        try:
            self.post_json("/queue", {"delete": [prompt_id]})
        finally:
            self.interrupt()

    def history(self, prompt_id: str) -> dict[str, Any] | None:
        h = self.get_json(f"/history/{prompt_id}")
        return h.get(prompt_id)

    def outputs(self, prompt_id: str) -> list[Image.Image]:
        entry = self.history(prompt_id)
        if not entry:
            raise ComfyError("the job has no results in ComfyUI's history")
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            raise ComfyError(_status_error(status))
        images = []
        for node_id in sorted(entry.get("outputs", {}), key=_node_order):
            for im in entry["outputs"][node_id].get("images", []):
                images.append(self.view(im["filename"], im.get("subfolder", ""), im.get("type", "output")))
        return images

    def run(
        self,
        graph: dict[str, Any],
        progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
        timeout: float = 3600.0,
    ) -> list[Image.Image]:
        """Queue ``graph``, follow its progress, and return its output images."""
        watcher = _ProgressWatcher(self, progress)
        watcher.start()
        try:
            prompt_id = self.queue(graph)
            watcher.prompt_id = prompt_id
            deadline = time.monotonic() + timeout
            poll = 0.0
            while True:
                if cancel is not None and cancel.is_set():
                    self.cancel(prompt_id)
                    raise Cancelled("cancelled")
                if watcher.error:
                    raise ComfyError(watcher.error)
                if watcher.done.wait(0.25):
                    break
                # Poll the history too: the socket may be unavailable, or a fully cached job may
                # finish before its prompt id is known to the watcher.
                if time.monotonic() >= poll:
                    poll = time.monotonic() + (3.0 if watcher.connected else 1.0)
                    entry = self.history(prompt_id)
                    if entry and entry.get("status", {}).get("completed") is not None:
                        if entry["status"].get("status_str") == "error":
                            raise ComfyError(_status_error(entry["status"]))
                        if entry.get("outputs"):
                            break
                if time.monotonic() > deadline:
                    self.cancel(prompt_id)
                    raise ComfyError("timed out waiting for ComfyUI")
            if watcher.error:
                raise ComfyError(watcher.error)
            if progress:
                progress(1.0, "done")
            return self.outputs(prompt_id)
        finally:
            watcher.stop()


class _ProgressWatcher:
    """Follows ``/ws`` messages for one prompt on a background thread."""

    def __init__(self, client: ComfyClient, progress: ProgressFn | None):
        self.client = client
        self.progress = progress
        self.prompt_id: str | None = None
        self.done = threading.Event()
        self.error: str | None = None
        self.connected = False
        self._ws = None
        self._thread: threading.Thread | None = None
        self._stop = False
        self._nodes_total = 0
        self._nodes_done = 0

    def start(self) -> None:
        try:
            import websocket  # websocket-client
        except ImportError:
            return
        parsed = urllib.parse.urlparse(self.client.url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        url = f"{scheme}://{parsed.netloc}{parsed.path}/ws?clientId={self.client.client_id}"
        try:
            self._ws = websocket.create_connection(url, timeout=5)
            self._ws.settimeout(1.0)
            self.connected = True
        except Exception:  # noqa: BLE001 - any socket failure: fall back to polling
            self._ws = None
            return
        self._thread = threading.Thread(target=self._loop, name="comfy-progress", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        import websocket

        while not self._stop:
            try:
                raw = self._ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:  # noqa: BLE001 - socket closed
                self.connected = False
                return
            if isinstance(raw, bytes):
                self._preview(raw)
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            self.handle(msg)

    def handle(self, msg: dict[str, Any]) -> None:
        kind, data = msg.get("type"), msg.get("data") or {}
        if self.prompt_id is None or data.get("prompt_id") not in (None, self.prompt_id):
            return
        if kind == "execution_start":
            self._report(0.0, "started")
        elif kind == "execution_cached":
            self._nodes_done += len(data.get("nodes") or [])
        elif kind == "progress":
            value, maximum = data.get("value", 0), max(1, data.get("max", 1))
            self._report(value / maximum, f"step {value}/{maximum}")
        elif kind == "executing":
            if data.get("node") is None and data.get("prompt_id") == self.prompt_id:
                self.done.set()
        elif kind == "execution_success":
            self.done.set()
        elif kind == "execution_error":
            self.error = f"{data.get('node_type', 'node')}: {data.get('exception_message', 'error')}".strip()
            self.done.set()
        elif kind == "execution_interrupted":
            self.error = "interrupted"
            self.done.set()

    def _report(self, value: float, text: str) -> None:
        if self.progress:
            try:
                self.progress(max(0.0, min(1.0, value)), text)
            except Exception:  # noqa: BLE001 - a UI callback must not kill the watcher
                pass

    def _preview(self, raw: bytes) -> None:
        # Binary frames: 4-byte event type (1 = preview image), 4-byte format, then JPEG/PNG.
        if len(raw) < 8 or int.from_bytes(raw[:4], "big") != 1:
            return
        cb = getattr(self.progress, "preview", None)
        if cb is None:
            return
        try:
            cb(from_bytes(raw[8:]))
        except Exception:  # noqa: BLE001
            pass


def _form_field(boundary: str, name: str, value: str) -> bytes:
    return f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()


def _node_order(node_id: str) -> tuple[int, str]:
    try:
        return (int(node_id), node_id)
    except ValueError:
        return (1 << 30, node_id)


def _error_text(body: str) -> str:
    try:
        j = json.loads(body)
    except json.JSONDecodeError:
        return body[:500]
    err = j.get("error")
    msg = err.get("message", "") if isinstance(err, dict) else str(err or "")
    details = j.get("node_errors")
    if details:
        msg += " " + json.dumps(details)[:500]
    return msg or body[:500]


def _status_error(status: dict[str, Any]) -> str:
    for kind, data in status.get("messages", []):
        if kind == "execution_error":
            return f"{data.get('node_type', 'node')}: {data.get('exception_message', 'error')}".strip()
    return "the workflow failed"
