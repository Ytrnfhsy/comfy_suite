from PIL import Image

from comfy_suite.image_ops import Bounds, generation_extent, inpaint_context, prepare_mask, flatten_on, apply_mask


def test_small_regions_scale_up_to_native():
    e = generation_extent(256, 256, native=1024)
    assert e.generate == (1024, 1024)
    assert e.scale == 4


def test_large_regions_scale_down_and_keep_aspect():
    e = generation_extent(6000, 4000, native=1024)
    w, h = e.generate
    assert w % 8 == 0 and h % 8 == 0
    assert w * h <= 1.5 * 1024 * 1024 * 1.02
    assert abs(w / h - 1.5) < 0.02


def test_regions_near_native_stay():
    assert generation_extent(1024, 1200, native=1024).generate == (1024, 1200)


def test_thin_regions_get_a_minimum_side():
    w, h = generation_extent(2000, 40, native=512).generate
    assert min(w, h) >= 256


def test_inpaint_context_pads_and_clamps():
    canvas = Bounds(0, 0, 500, 400)
    ctx = inpaint_context(Bounds(10, 10, 100, 100), canvas, padding=0.25)
    assert ctx == Bounds(0, 0, 142, 142)  # 32 px minimum padding
    assert inpaint_context(Bounds(450, 350, 50, 50), canvas).x1 == 500


def test_prepare_mask_grows_and_feathers():
    m = Image.new("L", (64, 64), 0)
    m.paste(255, (24, 24, 40, 40))
    out = prepare_mask(m, grow=4, feather=4)
    assert out.getpixel((21, 32)) > 150  # grown
    assert 0 < out.getpixel((18, 32)) < 255  # soft edge
    assert out.getpixel((2, 2)) == 0


def test_flatten_and_mask():
    im = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    assert flatten_on(im).getpixel((0, 0)) == (255, 255, 255)
    mask = Image.new("L", (4, 4), 128)
    assert apply_mask(Image.new("RGB", (4, 4)), mask).getpixel((1, 1))[3] == 128
