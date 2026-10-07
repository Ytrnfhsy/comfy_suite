"""The work behind each panel: read the document, run ComfyUI, hand back results.

Everything here is synchronous and Qt-free; the UI runs it on a worker thread through
``jobs.JobQueue``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from PIL import Image

from . import image_ops as ops
from .comfy import ComfyClient
from .image_ops import Bounds
from .photosuite import DocumentInfo, PhotoSuiteBridge
from .settings import Settings
from .styles import Style
from .workflow import (
    ControlInput,
    GenerateRequest,
    UpscaleRequest,
    WorkflowError,
    apply_custom,
    build_generate,
    build_upscale,
    custom_placeholders,
    fill_defaults,
    resolve_seed,
)

ProgressFn = Callable[[float, str], None]

CANVAS = -1  # control source: the visible canvas instead of one layer


@dataclass
class ControlSpec:
    """A control layer as the user configured it."""

    mode: str
    layer: int = CANVAS
    strength: float = 1.0
    start: float = 0.0
    end: float = 1.0
    model: str = ""
    preprocess: bool = True


@dataclass
class GenerateParams:
    prompt: str = ""
    negative: str = ""
    strength: float = 1.0
    seed: int = -1
    batch: int = 1
    controls: list[ControlSpec] = field(default_factory=list)
    use_selection: bool = True


@dataclass
class Result:
    """Images from one job and where they belong in the document."""

    images: list[Image.Image]
    doc_index: int
    region: Bounds
    into_selection: bool
    seed: int = 0
    prompt: str = ""
    new_document: bool = False
    mask: Image.Image | None = None  # coverage of the region, for previews


class Generator:
    def __init__(self, bridge: PhotoSuiteBridge, comfy: ComfyClient, settings: Settings):
        self.bridge = bridge
        self.comfy = comfy
        self.settings = settings

    # -- helpers ------------------------------------------------------------------------------

    def _run(self, graph: dict[str, Any], progress: ProgressFn | None, cancel: threading.Event | None) -> list[Image.Image]:
        models = self.comfy.models()
        if models.info:
            fill_defaults(graph, models.info)
        return self.comfy.run(graph, progress=progress, cancel=cancel)

    def _upload(self, image: Image.Image) -> str:
        return self.comfy.upload_image(image)

    def _controls(self, doc: DocumentInfo, canvas: Image.Image | None, specs: list[ControlSpec], region: Bounds, size: tuple[int, int]) -> list[ControlInput]:
        out = []
        for spec in specs:
            if spec.strength <= 0:
                continue
            if spec.layer == CANVAS:
                src = canvas if canvas is not None else self.bridge.export_canvas(doc)[0]
            else:
                src = self.bridge.export_layer(doc, spec.layer)
            # Line art on transparent layers: black lines on white, which control models expect.
            img = ops.resize(ops.flatten_on(src.crop(region.box)), size)
            out.append(ControlInput(spec.mode, self._upload(img), spec.strength, spec.start, spec.end, spec.model, spec.preprocess))
        return out

    # -- generate / refine / inpaint ----------------------------------------------------------

    def generate(self, style: Style, params: GenerateParams, progress: ProgressFn | None = None, cancel: threading.Event | None = None, live: bool = False) -> Result:
        doc = self.bridge.document()
        selection = doc.selection if params.use_selection else None
        needs_pixels = params.strength < 1.0 or selection is not None or any(c.layer == CANVAS for c in params.controls)
        if progress:
            progress(0.0, "reading the document")
        canvas, mask = self.bridge.export_canvas(doc, with_mask=selection is not None) if needs_pixels else (None, None)

        if selection is not None:
            region = ops.inpaint_context(selection, doc.bounds, self.settings.context_padding)
        else:
            region = doc.bounds
        extent = ops.generation_extent(region.width, region.height, style.native_resolution)
        size = extent.generate

        req = GenerateRequest(
            prompt=params.prompt,
            negative=params.negative,
            strength=params.strength,
            seed=resolve_seed(params.seed),
            batch=1 if live else params.batch,
            width=size[0],
            height=size[1],
            live=live,
        )
        region_mask = None
        if canvas is not None:
            req.image = self._upload(ops.resize(ops.flatten_on(canvas.crop(region.box)), size))
        if selection is not None and mask is not None:
            region_mask = mask.crop(region.box)
            prepared = ops.prepare_mask(region_mask, self.settings.selection_grow, self.settings.selection_feather)
            req.mask = self._upload(ops.resize(prepared, size))
        req.controls = self._controls(doc, canvas, params.controls, region, size)

        graph = build_generate(style, self.comfy.models(), req).to_json()
        images = self._run(graph, progress, cancel)
        if not images:
            raise WorkflowError("ComfyUI returned no images")
        images = [ops.resize(im.convert("RGBA"), (region.width, region.height)) for im in images]
        return Result(images, doc.index, region, selection is not None, req.seed, params.prompt, mask=region_mask)

    # -- upscale ------------------------------------------------------------------------------

    def upscale(
        self,
        style: Style,
        factor: float,
        model: str = "",
        refine: bool = False,
        strength: float = 0.3,
        prompt: str = "",
        progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ) -> Result:
        doc = self.bridge.document()
        canvas, _ = self.bridge.export_canvas(doc)
        w, h = max(1, round(doc.width * factor)), max(1, round(doc.height * factor))
        req = UpscaleRequest(self._upload(ops.flatten_on(canvas)), w, h, model, refine, strength, prompt, resolve_seed(-1))
        images = self._run(build_upscale(style, self.comfy.models(), req).to_json(), progress, cancel)
        if not images:
            raise WorkflowError("ComfyUI returned no images")
        return Result([images[0]], doc.index, Bounds(0, 0, w, h), False, req.seed, prompt, new_document=True)

    # -- custom workflow ----------------------------------------------------------------------

    def custom(
        self,
        workflow: dict[str, Any],
        values: dict[str, Any],
        progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ) -> Result:
        doc = self.bridge.document()
        names = custom_placeholders(workflow)
        values = dict(values)
        region = doc.bounds
        selection = doc.selection
        if "canvas" in names or "mask" in names:
            canvas, mask = self.bridge.export_canvas(doc, with_mask="mask" in names and selection is not None)
            values["canvas"] = self._upload(ops.flatten_on(canvas))
            if "mask" in names:
                values["mask"] = self._upload(mask if mask is not None else Image.new("L", canvas.size, 255))
        values.setdefault("width", doc.width)
        values.setdefault("height", doc.height)
        values["seed"] = resolve_seed(int(values.get("seed", -1)))
        graph = apply_custom(workflow, values)
        images = self._run(graph, progress, cancel)
        if not images:
            raise WorkflowError("the workflow produced no images (add a Preview Image or Save Image node)")
        images = [ops.resize(im.convert("RGBA"), (region.width, region.height)) for im in images]
        return Result(images, doc.index, region, False, values["seed"], str(values.get("prompt", "")))

    # -- applying -----------------------------------------------------------------------------

    def apply(self, result: Result, index: int = 0, name: str = "") -> int | None:
        image = result.images[index]
        if result.new_document:
            self.bridge.open_image(image)
            return None
        doc = self.bridge.document(result.doc_index)
        label = name or _layer_name(result.prompt, result.seed)
        return self.bridge.place_image(doc, image, result.region, label, into_selection=result.into_selection and doc.selection is not None)


def _layer_name(prompt: str, seed: int) -> str:
    text = " ".join(prompt.split())[:40] or "Generated"
    return f"[AI] {text} ({seed})"
