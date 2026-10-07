"""Geometry and pixel helpers: where to generate, at what resolution, and how to mask."""

from __future__ import annotations

import base64
import io
import math
from dataclasses import dataclass

from PIL import Image, ImageFilter


@dataclass(frozen=True)
class Bounds:
    x: int
    y: int
    width: int
    height: int

    @property
    def x1(self) -> int:
        return self.x + self.width

    @property
    def y1(self) -> int:
        return self.y + self.height

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.x1, self.y1)

    @property
    def is_empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    def grow(self, amount: int) -> "Bounds":
        return Bounds(self.x - amount, self.y - amount, self.width + 2 * amount, self.height + 2 * amount)

    def clamp(self, outer: "Bounds") -> "Bounds":
        x0, y0 = max(self.x, outer.x), max(self.y, outer.y)
        x1, y1 = min(self.x1, outer.x1), min(self.y1, outer.y1)
        return Bounds(x0, y0, max(0, x1 - x0), max(0, y1 - y0))

    def relative_to(self, outer: "Bounds") -> "Bounds":
        return Bounds(self.x - outer.x, self.y - outer.y, self.width, self.height)


def multiple_of(value: float, n: int = 8) -> int:
    return max(n, int(round(value / n)) * n)


@dataclass(frozen=True)
class Extent:
    """Sizes involved in one generation.

    ``input`` is the region size in the document, ``generate`` the size the model works at
    (scaled towards the model's native resolution, a multiple of 8).
    """

    input: tuple[int, int]
    generate: tuple[int, int]

    @property
    def scale(self) -> float:
        return self.generate[0] / max(1, self.input[0])


def generation_extent(width: int, height: int, native: int = 1024, min_side: int = 256, max_pixels_factor: float = 1.5) -> Extent:
    """Pick a generation size for a ``width × height`` region.

    Small regions are scaled up so the model sees roughly ``native²`` pixels (models produce
    poor results far below their training resolution), large ones scaled down to at most
    ``max_pixels_factor × native²``; the aspect ratio is kept and both sides are multiples of 16.
    """
    width, height = max(1, width), max(1, height)
    pixels = width * height
    target = native * native
    if pixels < target:
        s = math.sqrt(target / pixels)
    elif pixels > target * max_pixels_factor:
        s = math.sqrt(target * max_pixels_factor / pixels)
    else:
        s = 1.0
    gw, gh = width * s, height * s
    shortest = min(gw, gh)
    if shortest < min_side:
        k = min_side / shortest
        gw, gh = gw * k, gh * k
    # Multiples of 16: Flux 2 latents are 1/16 of the image (and 16 suits SD/Flux 1 too).
    return Extent((width, height), (multiple_of(gw, 16), multiple_of(gh, 16)))


def inpaint_context(selection: Bounds, canvas: Bounds, padding: float = 0.25, min_padding: int = 32) -> Bounds:
    """The region around a selection that the model sees: the selection plus surrounding
    context so the fill matches its neighbourhood."""
    pad = max(min_padding, int(padding * max(selection.width, selection.height)))
    return selection.grow(pad).clamp(canvas)


def prepare_mask(mask: Image.Image, grow: int = 8, feather: int = 8) -> Image.Image:
    """Grow (dilate) and feather a coverage mask so seams blend."""
    m = mask.convert("L")
    if grow > 0:
        size = grow * 2 + 1
        m = m.filter(ImageFilter.MaxFilter(min(size, 51)))
    if feather > 0:
        m = m.filter(ImageFilter.GaussianBlur(feather / 2))
    return m


def resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    if image.size == tuple(size):
        return image
    return image.resize(size, Image.Resampling.LANCZOS)


def flatten_on(image: Image.Image, color: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    """RGB with transparency composited over ``color`` (models don't see alpha)."""
    if image.mode == "RGB":
        return image
    rgba = image.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, color + (255,))
    return Image.alpha_composite(bg, rgba).convert("RGB")


def to_png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def from_bytes(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def to_base64(image: Image.Image) -> str:
    return base64.b64encode(to_png_bytes(image)).decode("ascii")


def apply_mask(image: Image.Image, mask: Image.Image | None) -> Image.Image:
    """``image`` with ``mask`` as its alpha (for previews of inpainting results)."""
    out = image.convert("RGBA")
    if mask is not None:
        out.putalpha(resize(mask.convert("L"), out.size))
    return out


def thumbnail(image: Image.Image, side: int = 160) -> Image.Image:
    im = image.copy()
    im.thumbnail((side, side), Image.Resampling.LANCZOS)
    return im
