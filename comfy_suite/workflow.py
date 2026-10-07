"""Building ComfyUI workflows (API format) from what the user asked for.

Everything here is pure: images are referred to by the names ``LoadImage`` takes after upload,
so graphs can be built and checked without a server.
"""

from __future__ import annotations

import copy
import json
import random
import re
from dataclasses import dataclass, field
from typing import Any

from .comfy import ServerModels
from .styles import Style


class WorkflowError(Exception):
    pass


# ---- graph builder -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Out:
    node: str
    index: int = 0

    def ref(self) -> list[Any]:
        return [self.node, self.index]


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self._next = 1

    def add(self, class_type: str, _title: str = "", **inputs: Any) -> "Node":
        node_id = str(self._next)
        self._next += 1
        self.nodes[node_id] = {
            "class_type": class_type,
            "inputs": {k: (v.ref() if isinstance(v, Out) else v) for k, v in inputs.items() if v is not None},
        }
        if _title:
            self.nodes[node_id]["_meta"] = {"title": _title}
        return Node(node_id)

    def to_json(self) -> dict[str, Any]:
        return copy.deepcopy(self.nodes)

    def by_type(self, class_type: str) -> list[dict[str, Any]]:
        return [n for n in self.nodes.values() if n["class_type"] == class_type]


class Node:
    def __init__(self, node_id: str):
        self.id = node_id

    def __getitem__(self, index: int) -> Out:
        return Out(self.id, index)

    @property
    def out(self) -> Out:
        return Out(self.id, 0)


def fill_defaults(graph: dict[str, Any], object_info: dict[str, Any]) -> dict[str, Any]:
    """Fill required widget inputs a graph leaves out with the server's defaults.

    Custom node packs (ControlNet preprocessors, IP-Adapter) add inputs between versions;
    filling defaults keeps the workflows here working across them. Raises when a node type is
    not installed.
    """
    missing = sorted({n["class_type"] for n in graph.values() if n["class_type"] not in object_info})
    if missing:
        raise WorkflowError("ComfyUI is missing these nodes: " + ", ".join(missing))
    for node in graph.values():
        spec = object_info[node["class_type"]].get("input", {}).get("required", {}) or {}
        inputs = node["inputs"]
        for name, entry in spec.items():
            if name in inputs or not entry:
                continue
            kind = entry[0]
            opts = entry[1] if len(entry) > 1 and isinstance(entry[1], dict) else {}
            if isinstance(kind, list):
                if kind:
                    inputs[name] = opts.get("default", kind[0])
            elif kind == "COMBO":
                choices = opts.get("options") or []
                if "default" in opts:
                    inputs[name] = opts["default"]
                elif choices:
                    inputs[name] = choices[0]
            elif "default" in opts:
                inputs[name] = opts["default"]
    return graph


# ---- requests ------------------------------------------------------------------------------------

CONTROL_MODES = ("reference", "scribble", "lineart", "softedge", "canny", "depth", "normal", "pose", "segmentation", "blur", "image")

# Preprocessor node per control mode (comfyui_controlnet_aux; Canny is built into ComfyUI). When
# the node is not installed, the layer is used as-is: it should already be the control image.
PREPROCESSORS: dict[str, tuple[str, dict[str, Any]]] = {
    "canny": ("Canny", {"low_threshold": 0.4, "high_threshold": 0.8}),
    "lineart": ("LineArtPreprocessor", {"coarse": "disable"}),
    "softedge": ("HEDPreprocessor", {"safe": "enable"}),
    "scribble": ("ScribblePreprocessor", {}),
    "depth": ("DepthAnythingV2Preprocessor", {}),
    "normal": ("BAE-NormalMapPreprocessor", {}),
    "pose": ("DWPreprocessor", {}),
    "segmentation": ("OneFormer-ADE20K-SemSegPreprocessor", {}),
}

# Words in ControlNet file names that identify their mode (for auto-picking a model).
_CONTROL_HINTS = {
    "scribble": r"scribble|sketch",
    "lineart": r"lineart|line_art|anyline",
    "softedge": r"softedge|hed|pidi",
    "canny": r"canny",
    "depth": r"depth",
    "normal": r"normal",
    "pose": r"pose|openpose",
    "segmentation": r"seg",
    "blur": r"blur|tile",
    "image": r"union|promax",
}

_ARCH_FILE_HINTS = {"sd15": r"sd15|sd-?1\.?5|v11|control_v1", "sdxl": r"xl", "flux": r"flux"}


@dataclass
class ControlInput:
    mode: str
    image: str  # uploaded image name
    strength: float = 1.0
    start: float = 0.0
    end: float = 1.0
    model: str = ""  # ControlNet file; "" picks one by name
    preprocess: bool = True


@dataclass
class GenerateRequest:
    prompt: str = ""
    negative: str = ""
    strength: float = 1.0  # 1 = generate from scratch, < 1 = refine the existing pixels
    seed: int = -1
    batch: int = 1
    width: int = 1024  # generation size (already a multiple of 8)
    height: int = 1024
    image: str | None = None  # the region's pixels (uploaded), required when strength < 1 or inpainting
    mask: str | None = None  # inpainting mask (uploaded, white = repaint)
    controls: list[ControlInput] = field(default_factory=list)
    live: bool = False


def resolve_seed(seed: int) -> int:
    return seed if seed >= 0 else random.randint(0, 2**31 - 1)


def pick_controlnet(mode: str, models: ServerModels, arch: str) -> str:
    hint = re.compile(_CONTROL_HINTS.get(mode, mode), re.I)
    arch_hint = re.compile(_ARCH_FILE_HINTS.get(arch, arch), re.I)
    named = [m for m in models.controlnets if hint.search(m)]
    unions = [m for m in models.controlnets if re.search(_CONTROL_HINTS["image"], m, re.I)]
    for pool in (named, unions):
        for m in pool:
            if arch_hint.search(m):
                return m
    for pool in (named, unions):
        if pool:
            return pool[0]
    raise WorkflowError(f"no ControlNet model for `{mode}` is installed; pick one in the control layer settings")


# ---- model loading -------------------------------------------------------------------------------


@dataclass
class Loaded:
    model: Out
    clip: Out
    vae: Out
    arch: str


def load_model(g: Graph, style: Style, models: ServerModels) -> Loaded:
    try:
        ckpt = style.resolve_checkpoint(models)
    except ValueError as e:
        raise WorkflowError(str(e)) from e
    arch = style.architecture if style.architecture != "auto" else None
    if arch is None:
        from .styles import guess_architecture

        arch = guess_architecture(ckpt) or "sdxl"
    loader = g.add("CheckpointLoaderSimple", ckpt_name=ckpt)
    model, clip, vae = loader[0], loader[1], loader[2]
    if style.vae:
        vae = g.add("VAELoader", vae_name=style.vae).out
    for lora in style.loras:
        if not lora.enabled:
            continue
        if models.loras and lora.name not in models.loras:
            raise WorkflowError(f"LoRA `{lora.name}` is not installed on the server")
        l = g.add("LoraLoader", model=model, clip=clip, lora_name=lora.name, strength_model=lora.strength, strength_clip=lora.strength)
        model, clip = l[0], l[1]
    if arch == "sd15" and style.clip_skip > 1:
        clip = g.add("CLIPSetLastLayer", clip=clip, stop_at_clip_layer=-style.clip_skip).out
    return Loaded(model, clip, vae, arch)


def encode_prompts(g: Graph, m: Loaded, style: Style, prompt: str, negative: str) -> tuple[Out, Out]:
    positive = g.add("CLIPTextEncode", "Prompt", clip=m.clip, text=style.apply_prompt(prompt)).out
    neg_text = ", ".join(x for x in (style.negative_prompt.strip(), negative.strip()) if x)
    negative_c = g.add("CLIPTextEncode", "Negative", clip=m.clip, text=neg_text).out
    if m.arch == "flux":
        positive = g.add("FluxGuidance", conditioning=positive, guidance=style.guidance).out
    return positive, negative_c


def apply_controls(
    g: Graph, m: Loaded, models: ServerModels, controls: list[ControlInput], positive: Out, negative: Out, width: int, height: int
) -> tuple[Out, Out, Out]:
    model = m.model
    for c in controls:
        if c.strength <= 0:
            continue
        image = g.add("LoadImage", f"Control {c.mode}", image=c.image).out
        image = g.add("ImageScale", image=image, upscale_method="lanczos", width=width, height=height, crop="disabled").out
        if c.mode == "reference":
            if not models.has("IPAdapterUnifiedLoader", "IPAdapterAdvanced"):
                raise WorkflowError("reference images need the ComfyUI_IPAdapter_plus nodes on the server")
            loader = g.add("IPAdapterUnifiedLoader", model=model, preset="PLUS (high strength)")
            model = g.add(
                "IPAdapterAdvanced",
                model=loader[0],
                ipadapter=loader[1],
                image=image,
                weight=c.strength,
                start_at=c.start,
                end_at=c.end,
            ).out
            continue
        if c.preprocess and c.mode in PREPROCESSORS and PREPROCESSORS[c.mode][0] in models.nodes:
            node, extra = PREPROCESSORS[c.mode]
            pre = g.add(node, image=image, **extra)
            image = pre.out
        name = c.model or pick_controlnet(c.mode, models, m.arch)
        cn = g.add("ControlNetLoader", control_net_name=name).out
        applied = g.add(
            "ControlNetApplyAdvanced",
            positive=positive,
            negative=negative,
            control_net=cn,
            image=image,
            strength=c.strength,
            start_percent=c.start,
            end_percent=c.end,
            vae=m.vae,
        )
        positive, negative = applied[0], applied[1]
    return model, positive, negative


# ---- workflows -----------------------------------------------------------------------------------


def build_generate(style: Style, models: ServerModels, req: GenerateRequest) -> Graph:
    """Text to image, refine (image to image) or inpaint, depending on ``image``/``mask``/``strength``."""
    if req.strength < 1.0 and not req.image:
        raise WorkflowError("refining (strength below 100%) needs the canvas image")
    g = Graph()
    m = load_model(g, style, models)
    positive, negative = encode_prompts(g, m, style, req.prompt, req.negative)
    model, positive, negative = apply_controls(g, m, models, req.controls, positive, negative, req.width, req.height)

    batch = max(1, min(16, req.batch))
    # The controller uploads the region and mask already resized to the generation size.
    pixels = g.add("LoadImage", "Canvas", image=req.image).out if req.image else None

    if req.mask and pixels is not None:
        mask = g.add("LoadImageMask", "Mask", image=req.mask, channel="red").out
        cond = g.add("InpaintModelConditioning", positive=positive, negative=negative, vae=m.vae, pixels=pixels, mask=mask, noise_mask=True)
        positive, negative, latent = cond[0], cond[1], cond[2]
    elif pixels is not None and req.strength < 1.0:
        latent = g.add("VAEEncode", pixels=pixels, vae=m.vae).out
    else:
        empty = "EmptySD3LatentImage" if m.arch == "flux" else "EmptyLatentImage"
        latent = g.add(empty, width=req.width, height=req.height, batch_size=batch).out
        batch = 1  # already a batch
    if batch > 1:
        latent = g.add("RepeatLatentBatch", samples=latent, amount=batch).out

    sampler = style.live_sampler if req.live else style.sampler
    scheduler = style.live_scheduler if req.live else style.scheduler
    steps = style.live_steps if req.live else style.steps
    cfg = style.live_cfg if req.live else style.cfg
    sampled = g.add(
        "KSampler",
        model=model,
        positive=positive,
        negative=negative,
        latent_image=latent,
        seed=resolve_seed(req.seed),
        steps=steps,
        cfg=cfg,
        sampler_name=sampler,
        scheduler=scheduler,
        denoise=max(0.01, min(1.0, req.strength)),
    ).out
    decoded = g.add("VAEDecode", samples=sampled, vae=m.vae).out
    g.add("PreviewImage", "Result", images=decoded)
    return g


@dataclass
class UpscaleRequest:
    image: str
    width: int
    height: int
    model: str = ""  # upscale model file; "" = plain Lanczos
    refine: bool = False
    strength: float = 0.3
    prompt: str = ""
    seed: int = -1
    tile_size: int = 1024


def build_upscale(style: Style, models: ServerModels, req: UpscaleRequest) -> Graph:
    g = Graph()
    image = g.add("LoadImage", "Image", image=req.image).out
    if req.model:
        um = g.add("UpscaleModelLoader", model_name=req.model).out
        image = g.add("ImageUpscaleWithModel", upscale_model=um, image=image).out
    image = g.add("ImageScale", image=image, upscale_method="lanczos", width=req.width, height=req.height, crop="disabled").out
    if req.refine:
        m = load_model(g, style, models)
        positive, negative = encode_prompts(g, m, style, req.prompt, "")
        latent = g.add("VAEEncodeTiled", pixels=image, vae=m.vae, tile_size=req.tile_size, overlap=64).out
        sampled = g.add(
            "KSampler",
            model=m.model,
            positive=positive,
            negative=negative,
            latent_image=latent,
            seed=resolve_seed(req.seed),
            steps=style.steps,
            cfg=style.cfg,
            sampler_name=style.sampler,
            scheduler=style.scheduler,
            denoise=max(0.01, min(1.0, req.strength)),
        ).out
        image = g.add("VAEDecodeTiled", samples=sampled, vae=m.vae, tile_size=req.tile_size, overlap=64).out
    g.add("PreviewImage", "Result", images=image)
    return g


# ---- custom workflows ----------------------------------------------------------------------------

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
IMAGE_TITLES = {"photosuite canvas": "canvas", "photosuite mask": "mask", "photosuite selection": "mask"}


def load_custom_workflow(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise WorkflowError(f"not a JSON file: {e}") from e
    if isinstance(data, dict) and "nodes" in data and "links" in data:
        raise WorkflowError("this is a UI workflow; in ComfyUI use Workflow › Export (API) and load that file")
    if not isinstance(data, dict) or not all(isinstance(v, dict) and "class_type" in v for v in data.values()):
        raise WorkflowError("not a ComfyUI API workflow")
    return data


def custom_placeholders(graph: dict[str, Any]) -> list[str]:
    """Placeholder names (``{{name}}``) used in a workflow, plus image slots found by title."""
    names: list[str] = []
    for node in graph.values():
        title = str(node.get("_meta", {}).get("title", "")).strip().lower()
        if title in IMAGE_TITLES and IMAGE_TITLES[title] not in names:
            names.append(IMAGE_TITLES[title])
        for v in node.get("inputs", {}).values():
            if isinstance(v, str):
                for n in PLACEHOLDER.findall(v):
                    if n not in names:
                        names.append(n)
    return names


def apply_custom(graph: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Substitute placeholders. A value that is exactly ``{{name}}`` takes the value's type
    (so ``"{{seed}}"`` becomes a number); inside longer text it is formatted as text."""
    out = copy.deepcopy(graph)
    for node in out.values():
        title = str(node.get("_meta", {}).get("title", "")).strip().lower()
        slot = IMAGE_TITLES.get(title)
        if slot and slot in values and node.get("class_type") in ("LoadImage", "LoadImageMask"):
            node["inputs"]["image"] = values[slot]
        for key, v in list(node.get("inputs", {}).items()):
            if not isinstance(v, str):
                continue
            whole = PLACEHOLDER.fullmatch(v.strip())
            if whole:
                if whole.group(1) in values:
                    node["inputs"][key] = values[whole.group(1)]
                continue
            node["inputs"][key] = PLACEHOLDER.sub(lambda mt: str(values.get(mt.group(1), mt.group(0))), v)
    return out
