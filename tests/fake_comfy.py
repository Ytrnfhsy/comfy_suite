"""A stand-in for a ComfyUI server: enough of the HTTP API to exercise the client.

"Running" a workflow produces images whose size follows the graph (the latent size, the canvas
image, or an ``ImageScale``), filled with a solid colour, so tests can check where results land.
"""

from __future__ import annotations

import io
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from PIL import Image

OBJECT_INFO: dict[str, Any] = {
    "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["sdxl_base.safetensors", "sd15_dreamshaper.safetensors", "flux1-dev-fp8.safetensors"]]}}},
    "VAELoader": {"input": {"required": {"vae_name": [["sdxl_vae.safetensors"]]}}},
    "LoraLoader": {"input": {"required": {"lora_name": [["detail.safetensors"]], "strength_model": ["FLOAT", {"default": 1.0}], "strength_clip": ["FLOAT", {"default": 1.0}]}}},
    "ControlNetLoader": {"input": {"required": {"control_net_name": [["control_sd15_scribble.pth", "controlnet-union-sdxl-promax.safetensors"]]}}},
    "UpscaleModelLoader": {"input": {"required": {"model_name": [["4x-UltraSharp.pth"]]}}},
    "KSampler": {
        "input": {
            "required": {
                "sampler_name": [["euler", "euler_ancestral", "dpmpp_2m", "dpmpp_2m_sde"]],
                "scheduler": [["normal", "karras", "sgm_uniform", "simple"]],
            }
        }
    },
    "CLIPTextEncode": {"input": {"required": {}}},
    "CLIPSetLastLayer": {"input": {"required": {}}},
    "FluxGuidance": {"input": {"required": {}}},
    "EmptyLatentImage": {"input": {"required": {}}},
    "EmptySD3LatentImage": {"input": {"required": {}}},
    "VAEEncode": {"input": {"required": {}}},
    "VAEDecode": {"input": {"required": {}}},
    "VAEEncodeTiled": {"input": {"required": {"temporal_size": ["INT", {"default": 64}], "temporal_overlap": ["INT", {"default": 8}]}}},
    "VAEDecodeTiled": {"input": {"required": {"temporal_size": ["INT", {"default": 64}], "temporal_overlap": ["INT", {"default": 8}]}}},
    "RepeatLatentBatch": {"input": {"required": {}}},
    "InpaintModelConditioning": {"input": {"required": {}}},
    "LoadImage": {"input": {"required": {"image": [[]]}}},
    "LoadImageMask": {"input": {"required": {"image": [[]], "channel": [["alpha", "red", "green", "blue"]]}}},
    "ImageScale": {"input": {"required": {}}},
    "ImageUpscaleWithModel": {"input": {"required": {}}},
    "ControlNetApplyAdvanced": {"input": {"required": {}}},
    "Canny": {"input": {"required": {}}},
    "PreviewImage": {"input": {"required": {}}},
    "SaveImage": {"input": {"required": {}}},
}


class FakeComfy:
    def __init__(self, color: tuple[int, int, int] = (200, 30, 30), fail: str | None = None):
        self.color = color
        self.fail = fail
        self.uploads: dict[str, Image.Image] = {}
        self.prompts: list[dict[str, Any]] = []
        self.history: dict[str, Any] = {}
        self.outputs: dict[str, Image.Image] = {}
        self.interrupted = 0
        self.deleted: list[str] = []
        self.object_info = json.loads(json.dumps(OBJECT_INFO))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self) -> "FakeComfy":
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()

    # -- "execution" --------------------------------------------------------------------------

    def _size(self, graph: dict[str, Any], node_id: str, depth: int = 0) -> tuple[int, int, int]:
        """(width, height, batch) an output link produces."""
        node = graph[node_id]
        t, inp = node["class_type"], node["inputs"]
        if depth > 50:
            return (64, 64, 1)
        if t in ("EmptyLatentImage", "EmptySD3LatentImage"):
            return (inp["width"], inp["height"], inp.get("batch_size", 1))
        if t in ("ImageScale",):
            _, _, b = self._size(graph, inp["image"][0], depth + 1)
            return (inp["width"], inp["height"], b)
        if t == "LoadImage":
            im = self.uploads.get(inp["image"])
            return (im.width, im.height, 1) if im else (64, 64, 1)
        if t == "RepeatLatentBatch":
            w, h, b = self._size(graph, inp["samples"][0], depth + 1)
            return (w, h, b * inp["amount"])
        if t == "InpaintModelConditioning":
            return self._size(graph, inp["pixels"][0], depth + 1)
        if t == "ImageUpscaleWithModel":
            w, h, b = self._size(graph, inp["image"][0], depth + 1)
            return (w * 4, h * 4, b)
        for key in ("samples", "latent_image", "pixels", "images", "image"):
            if key in inp and isinstance(inp[key], list):
                return self._size(graph, inp[key][0], depth + 1)
        return (64, 64, 1)

    def execute(self, prompt_id: str, graph: dict[str, Any]) -> None:
        outputs: dict[str, Any] = {}
        if self.fail:
            self.history[prompt_id] = {
                "status": {"status_str": "error", "completed": False, "messages": [["execution_error", {"node_type": "KSampler", "exception_message": self.fail}]]},
                "outputs": {},
            }
            return
        for node_id, node in graph.items():
            if node["class_type"] in ("PreviewImage", "SaveImage"):
                w, h, b = self._size(graph, node["inputs"]["images"][0])
                images = []
                for _ in range(b):
                    name = f"out-{uuid.uuid4().hex[:8]}.png"
                    self.outputs[name] = Image.new("RGB", (w, h), self.color)
                    images.append({"filename": name, "subfolder": "", "type": "temp"})
                outputs[node_id] = {"images": images}
        self.history[prompt_id] = {"status": {"status_str": "success", "completed": True, "messages": []}, "outputs": outputs}

    # -- HTTP ---------------------------------------------------------------------------------

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def _json(self, obj: Any, code: int = 200) -> None:
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                url = urlparse(self.path)
                if url.path == "/system_stats":
                    return self._json({"system": {"comfyui_version": "fake-1.0"}})
                if url.path == "/object_info":
                    return self._json(fake.object_info)
                if url.path.startswith("/history/"):
                    pid = url.path.rsplit("/", 1)[1]
                    return self._json({pid: fake.history[pid]} if pid in fake.history else {})
                if url.path == "/view":
                    q = parse_qs(url.query)
                    im = fake.outputs.get(q["filename"][0])
                    if im is None:
                        return self._json({"error": "missing"}, 404)
                    buf = io.BytesIO()
                    im.save(buf, "PNG")
                    body = buf.getvalue()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return None
                return self._json({"error": "not found"}, 404)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                url = urlparse(self.path)
                if url.path == "/upload/image":
                    ctype = self.headers["Content-Type"]
                    boundary = ctype.split("boundary=")[1].encode()
                    name, sub, data = "", "", b""
                    for part in body.split(b"--" + boundary):
                        head, _, content = part.partition(b"\r\n\r\n")
                        content = content[:-2] if content.endswith(b"\r\n") else content
                        if b'name="image"' in head:
                            name = head.split(b'filename="')[1].split(b'"')[0].decode()
                            data = content
                        elif b'name="subfolder"' in head:
                            sub = content.decode()
                    im = Image.open(io.BytesIO(data))
                    im.load()
                    fake.uploads[f"{sub}/{name}" if sub else name] = im
                    return self._json({"name": name, "subfolder": sub, "type": "input"})
                payload = json.loads(body or b"{}")
                if url.path == "/prompt":
                    graph = payload["prompt"]
                    missing = [n["class_type"] for n in graph.values() if n["class_type"] not in fake.object_info]
                    if missing:
                        return self._json({"error": {"message": f"unknown nodes {missing}"}, "node_errors": {}}, 400)
                    pid = uuid.uuid4().hex
                    fake.prompts.append(graph)
                    fake.execute(pid, graph)
                    return self._json({"prompt_id": pid, "number": len(fake.prompts), "node_errors": {}})
                if url.path == "/interrupt":
                    fake.interrupted += 1
                    return self._json({})
                if url.path == "/queue":
                    fake.deleted += payload.get("delete", [])
                    return self._json({})
                return self._json({"error": "not found"}, 404)

        return Handler
