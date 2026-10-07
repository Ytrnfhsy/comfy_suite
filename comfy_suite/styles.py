"""Style presets: which model to use and how to sample it.

Built-in styles ship with the package; user styles are JSON files in the config folder's
``styles`` directory and override built-ins of the same file name.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .comfy import ServerModels

BUILTIN_DIR = Path(__file__).parent / "styles"

ARCHITECTURES = ("auto", "sd15", "sdxl", "flux", "flux2")

# File-name hints used to guess a checkpoint's architecture and to auto-pick one.
_ARCH_HINTS = {
    "flux2": re.compile(r"flux[-_. ]?2|klein", re.I),
    "flux": re.compile(r"flux", re.I),
    "sdxl": re.compile(r"xl|pony|illustrious|noob|juggernaut|realvis", re.I),
    "sd15": re.compile(r"sd-?1\.?5|v1-5|sd15|dreamshaper_8|realistic.?vision|deliberate", re.I),
}


@dataclass
class LoraRef:
    name: str
    strength: float = 1.0
    enabled: bool = True


@dataclass
class Style:
    name: str = "New Style"
    architecture: str = "auto"
    checkpoint: str = ""
    vae: str = ""
    diffusion_model: str = ""  # Flux 2: UNETLoader file, or a fragment of its name ("klein")
    text_encoder: str = ""  # Flux 2: CLIPLoader file (Mistral for Dev, Qwen 3 for Klein)
    flux2_reference: bool = True  # Flux 2: give the canvas as a reference latent when refining
    loras: list[LoraRef] = field(default_factory=list)
    style_prompt: str = "{prompt}"
    negative_prompt: str = ""
    sampler: str = "euler"
    scheduler: str = "normal"
    steps: int = 20
    cfg: float = 7.0
    guidance: float = 3.5
    clip_skip: int = 1
    native_resolution: int = 1024
    live_sampler: str = "euler_ancestral"
    live_scheduler: str = "normal"
    live_steps: int = 8
    live_cfg: float = 2.0
    filename: str = ""

    @staticmethod
    def from_dict(d: dict[str, Any], filename: str = "") -> "Style":
        known = {f.name for f in fields(Style)}
        kw = {k: v for k, v in d.items() if k in known and k not in ("loras", "filename")}
        loras = [LoraRef(**{k: v for k, v in l.items() if k in ("name", "strength", "enabled")}) for l in d.get("loras", []) if isinstance(l, dict) and l.get("name")]
        s = Style(**kw, loras=loras)
        s.filename = filename or s.filename
        return s

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("filename", None)
        return d

    def apply_prompt(self, prompt: str) -> str:
        prompt = prompt.strip()
        template = self.style_prompt or "{prompt}"
        if "{prompt}" not in template:
            return ", ".join(x for x in (template.strip(), prompt) if x)
        out = template.replace("{prompt}", prompt)
        # Tidy the separators an empty prompt leaves behind.
        return re.sub(r"(,\s*){2,}", ", ", out).strip(" ,")

    def resolved_architecture(self) -> str:
        if self.architecture in ("sd15", "sdxl", "flux", "flux2"):
            return self.architecture
        return guess_architecture(self.checkpoint) or "sdxl"

    def resolve_checkpoint(self, models: ServerModels) -> str:
        """The configured checkpoint, or the best guess among the installed ones."""
        available = models.checkpoints
        if self.checkpoint:
            if not available or self.checkpoint in available:
                return self.checkpoint
            raise ValueError(f"checkpoint `{self.checkpoint}` is not installed on the server")
        if not available:
            raise ValueError("the server has no checkpoints installed")
        arch = self.architecture if self.architecture != "auto" else None
        if arch:
            for c in available:
                if guess_architecture(c) == arch:
                    return c
        return available[0]


    def resolve_flux2(self, models: ServerModels) -> tuple[str, str, str]:
        """(diffusion model, text encoder, VAE) for a Flux 2 style, picked among the installed
        files like ComfyUI's Flux.2 templates name them."""
        unet = _pick_file(models.diffusion_models, self.diffusion_model, [r"flux[-_. ]?2.*dev|dev.*flux[-_. ]?2", r"flux[-_. ]?2|klein"], "diffusion model")
        klein, big = bool(re.search("klein", unet, re.I)), bool(re.search(r"9b|8b", unet, re.I))
        enc_prefs = ([r"qwen.?3.?8b", r"qwen.?3"] if big else [r"qwen.?3.?4b", r"qwen.?3"]) if klein else [r"mistral.*flux.?2|flux.?2.*mistral", r"mistral"]
        clip = _pick_file(models.text_encoders, self.text_encoder, enc_prefs, "text encoder")
        vae = _pick_file(models.vaes, self.vae, [r"flux[-_. ]?2", r"full_encoder_small_decoder"], "VAE")
        return unet, clip, vae


def _pick_file(available: list[str], wanted: str, preferred: list[str], what: str) -> str:
    if wanted and wanted in available:
        return wanted
    pool = available
    if wanted:
        pool = [f for f in available if wanted.lower() in f.lower()]
        if not pool:
            if not available:
                return wanted  # the server didn't list its files: trust the style
            raise ValueError(f"{what} `{wanted}` is not installed on the server")
    for pattern in preferred:
        for f in pool:
            if re.search(pattern, f, re.I):
                return f
    if wanted and pool:
        return pool[0]
    raise ValueError(f"no {what} for Flux 2 is installed on the server")


def guess_architecture(filename: str) -> str | None:
    for arch in ("flux2", "flux", "sdxl", "sd15"):
        if _ARCH_HINTS[arch].search(filename or ""):
            return arch
    return None


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "style"


class StyleLibrary:
    def __init__(self, user_dir: Path):
        self.user_dir = Path(user_dir)
        self.styles: list[Style] = []
        self.reload()

    def reload(self) -> None:
        found: dict[str, Style] = {}
        for folder in (BUILTIN_DIR, self.user_dir):
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.json")):
                try:
                    found[path.name] = Style.from_dict(json.loads(path.read_text(encoding="utf-8")), path.name)
                except (OSError, ValueError, TypeError) as e:
                    print(f"comfy-suite: skipping style {path}: {e}")
        self.styles = sorted(found.values(), key=lambda s: s.name.lower())

    def names(self) -> list[str]:
        return [s.name for s in self.styles]

    def get(self, name: str) -> Style:
        for s in self.styles:
            if s.name == name or s.filename == name:
                return s
        if not self.styles:
            return Style()
        return self.styles[0]

    def save(self, style: Style) -> Path:
        self.user_dir.mkdir(parents=True, exist_ok=True)
        if not style.filename:
            base = _slug(style.name)
            name, i = f"{base}.json", 2
            while any(s.filename == name for s in self.styles):
                name, i = f"{base}-{i}.json", i + 1
            style.filename = name
        path = self.user_dir / style.filename
        path.write_text(json.dumps(style.to_dict(), indent=2), encoding="utf-8")
        self.reload()
        return path

    def delete(self, style: Style) -> bool:
        """Delete a user style (built-ins can't be deleted, only overridden)."""
        path = self.user_dir / style.filename
        if style.filename and path.exists():
            path.unlink()
            self.reload()
            return True
        return False
