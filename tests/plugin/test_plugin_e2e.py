"""The sidebar plugin inside the real PhotoSuite 0.9.15 UI (Chromium), against a fake ComfyUI.

Skipped unless PHOTOSUITE_WEB points at a PhotoSuite 0.9.15 ``src`` folder with its vendor
submodules set up, and playwright and Chromium are available.
"""

import base64
import io
import os
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))

WEB = os.environ.get("PHOTOSUITE_WEB")
pytestmark = pytest.mark.skipif(not WEB, reason="PHOTOSUITE_WEB not set")


def _png(w, h, fill=(255, 255, 255), image=None):
    buf = io.BytesIO()
    (image or Image.new("RGB", (w, h), fill)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _wait(cond, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.25)
    return None


def _open_document(h, image=None):
    """Open a 400×300 white document (or `image`) the way a plugin or a drop would: an
    ArrayBuffer message. (The sidebar, and so the panel, only shows with a document open.)"""
    h.page.evaluate(
        "(b64) => { const s = atob(b64), u = new Uint8Array(s.length);"
        " for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); window.postMessage(u.buffer, '*'); }",
        _png(400, 300, image=image),
    )
    time.sleep(2)


@pytest.fixture(params=["*", "null"], ids=["cors-any", "cors-null"])
def cors(request):
    """--enable-cors-header (any origin) and --enable-cors-header null (only sandboxed pages)."""
    return request.param


@pytest.fixture
def document():
    """The image the test document opens with (None: 400×300 white)."""
    return None


@pytest.fixture
def ps(cors, document):
    pytest.importorskip("playwright")
    from fake_comfy import FakeComfy
    from harness import Harness

    with FakeComfy(color=(220, 40, 40), cors=cors) as fake:
        h = Harness(Path(WEB))
        try:
            h.open()
            time.sleep(3)
            _open_document(h, document)
            h.page.get_by_text("Window", exact=True).first.click()
            h.page.get_by_text("ComfySuite AI", exact=True).click()
            f = h.plugin_frame()
            f.wait_for_selector("#tab-settings")
            f.click("#tab-settings")
            f.fill("#ws-settings input[type=text]", fake.url)
            f.click("#ws-settings button.primary")
            assert _wait(lambda: "ok" in f.get_attribute("#status", "class")), f.inner_text("#message")
            f.click("#tab-generate")
            yield h, f, fake
        finally:
            h.close()


def _pixel(f, x, y):
    return f.evaluate(f"CS.host.composite().then(d => Array.from(d.canvas.getContext('2d').getImageData({x}, {y}, 1, 1).data))")


def _layers(f):
    return int(f.evaluate("CS.host.layerCount()"))


def _generate_and_apply(f, prompt):
    f.fill("#ws-generate textarea", prompt)
    f.click("#ws-generate button.primary")
    assert _wait(lambda: f.evaluate("document.querySelectorAll('#history img').length") > 0), f.inner_text("#message")
    before = _layers(f)
    f.locator("#history-actions button").first.click()
    assert _wait(lambda: _layers(f) == before + 1, 20), f.inner_text("#message")
    time.sleep(0.5)


def test_generate_whole_canvas(ps):
    h, f, fake = ps
    _generate_and_apply(f, "red sky")
    (latent,) = [n for n in fake.prompts[0].values() if n["class_type"] == "EmptyLatentImage"]
    assert latent["inputs"]["batch_size"] == 2 and latent["inputs"]["width"] % 8 == 0
    assert _pixel(f, 200, 150) == [220, 40, 40, 255]
    name = f.evaluate("CS.host.evalScript('app.activeDocument.activeLayer.name')")
    assert name.startswith("[AI] red sky (")


def test_fill_selection(ps):
    h, f, fake = ps
    f.evaluate("CS.host.runScript('app.activeDocument.selection.select([[100,50],[200,50],[200,150],[100,150]])')")
    assert _wait(lambda: f.evaluate("CS.host.selectionMask().then(s => !!s)"))
    _generate_and_apply(f, "a red ball")
    graph = fake.prompts[-1]
    kinds = {n["class_type"] for n in graph.values()}
    assert {"InpaintModelConditioning", "LoadImageMask"} <= kinds
    assert any(im.convert("L").getextrema() == (0, 255) for im in fake.uploads.values())  # the mask
    assert _pixel(f, 150, 100) == [220, 40, 40, 255]  # inside the selection
    assert _pixel(f, 20, 20) == [255, 255, 255, 255]  # outside: untouched
    assert _pixel(f, 330, 250) == [255, 255, 255, 255]


def test_refine_uploads_the_canvas(ps):
    h, f, fake = ps
    f.evaluate("document.querySelector('#ws-generate input[type=range]').value = 40;"
               "document.querySelector('#ws-generate input[type=range]').dispatchEvent(new Event('input'))")
    f.uncheck("#ws-generate input[type=checkbox] >> nth=0")
    f.fill("#ws-generate textarea", "x")
    f.click("#ws-generate button.primary")
    assert _wait(lambda: fake.prompts)
    ks = next(n for n in fake.prompts[-1].values() if n["class_type"] == "KSampler")
    assert ks["inputs"]["denoise"] == 0.4
    assert any(im.size[0] % 8 == 0 for im in fake.uploads.values())


def test_repository_clone_works_as_the_plugin_folder():
    """A clone of the whole repository in the plugins folder loads through the root plugin.json."""
    pytest.importorskip("playwright")
    from harness import Harness

    h = Harness(Path(WEB), plugin_dir=Path(__file__).resolve().parents[2])
    try:
        h.open()
        time.sleep(3)
        _open_document(h)
        h.page.get_by_text("Window", exact=True).first.click()
        h.page.get_by_text("ComfySuite AI", exact=True).click()
        f = h.plugin_frame()
        f.wait_for_selector("#tab-generate")
        assert f.evaluate("typeof CS.Generator") == "function"
    finally:
        h.close()


def test_upscale_opens_a_new_document(ps):
    h, f, fake = ps
    f.click("#tab-upscale")
    f.click("#ws-upscale button.primary")
    assert _wait(lambda: f.evaluate("CS.host.evalScript('app.documents.length')") == "2", 30), f.inner_text("#message")
    assert f.evaluate("CS.host.composite().then(d => d.width + 'x' + d.height)") == "800x600"


def test_flux2_fill_selection(ps):
    h, f, fake = ps
    f.select_option("#style", "Flux 2 Dev")
    f.evaluate("CS.host.runScript('app.activeDocument.selection.select([[100,50],[200,50],[200,150],[100,150]])')")
    assert _wait(lambda: f.evaluate("CS.host.selectionMask().then(s => !!s)"))
    _generate_and_apply(f, "a red ball")
    kinds = {n["class_type"] for n in fake.prompts[-1].values()}
    assert {"UNETLoader", "CLIPLoader", "SamplerCustomAdvanced", "Flux2Scheduler", "SetLatentNoiseMask", "ReferenceLatent"} <= kinds
    assert "KSampler" not in kinds and "CheckpointLoaderSimple" not in kinds
    latent_sizes = [im.size for im in fake.uploads.values()]
    assert all(w % 16 == 0 and hh % 16 == 0 for w, hh in latent_sizes)
    assert _pixel(f, 150, 100) == [220, 40, 40, 255]
    assert _pixel(f, 20, 20) == [255, 255, 255, 255]


def _select(f, x0, y0, x1, y1):
    f.evaluate(f"CS.host.runScript('app.activeDocument.selection.select([[{x0},{y0}],[{x1},{y0}],[{x1},{y1}],[{x0},{y1}]])')")
    assert _wait(lambda: f.evaluate("CS.host.selectionMask().then(s => !!s)"))


def _canvas_upload(fake):
    """The region image the panel uploaded (RGB, the largest)."""
    return max((im for im in fake.uploads.values() if im.mode in ("RGB", "RGBA") and im.convert("L").getextrema() != (0, 255)), key=lambda im: im.size[0] * im.size[1])


def _blue_with_red_square():
    im = Image.new("RGB", (400, 300), (0, 0, 255))
    im.paste((255, 0, 0), (140, 90, 180, 130))
    return im


@pytest.mark.parametrize("document", [_blue_with_red_square()], ids=["blue-red"])
@pytest.mark.parametrize("cors", ["*"], ids=["cors-any"])
def test_remove_object(ps):
    h, f, fake = ps
    _select(f, 130, 80, 190, 140)
    f.select_option("#ws-generate select.mode", "remove")
    f.fill("#ws-generate textarea", "")
    _generate_and_apply(f, "")
    texts = [n["inputs"]["text"] for n in fake.prompts[-1].values() if n["class_type"] == "CLIPTextEncode"]
    assert "background scenery" in texts[0]
    up = _canvas_upload(fake).convert("RGB")
    cx, cy = up.size[0] // 2, up.size[1] // 2
    r, g, b = up.getpixel((cx, cy))
    assert b > 150 and r < 100, (r, g, b)  # the red square is pre-filled from the blue around it
    assert _pixel(f, 160, 110) == [220, 40, 40, 255]  # result inside the selection
    assert _pixel(f, 20, 20) == [0, 0, 255, 255]


@pytest.mark.parametrize("cors", ["*"], ids=["cors-any"])
def test_replace_background(ps):
    h, f, fake = ps
    _select(f, 150, 100, 250, 200)  # the subject
    f.select_option("#ws-generate select.mode", "background")
    _generate_and_apply(f, "a beach")
    assert _pixel(f, 10, 10) == [220, 40, 40, 255]  # background replaced
    assert _pixel(f, 390, 290) == [220, 40, 40, 255]
    assert _pixel(f, 200, 150) == [255, 255, 255, 255]  # subject kept


def _half_transparent():
    im = Image.new("RGBA", (400, 300), (255, 255, 255, 255))
    im.paste((0, 0, 0, 0), (250, 0, 400, 300))
    return im


@pytest.mark.parametrize("document", [_half_transparent()], ids=["enlarged-canvas"])
@pytest.mark.parametrize("cors", ["*"], ids=["cors-any"])
def test_expand_into_empty_canvas_without_selection(ps):
    h, f, fake = ps
    assert f.inner_text("#ws-generate button.primary") in ("Генерувати", "Generate")
    _generate_and_apply(f, "more of the landscape")
    assert any(n["class_type"] == "InpaintModelConditioning" for n in fake.prompts[-1].values())
    assert _pixel(f, 350, 150) == [220, 40, 40, 255]  # the empty part is generated
    assert _pixel(f, 100, 150) == [255, 255, 255, 255]  # the existing image stays


@pytest.mark.parametrize("cors", ["*"], ids=["cors-any"])
def test_flux2_modes_use_instructions_and_green_reference(ps):
    h, f, fake = ps
    f.select_option("#style", "Flux 2 Dev")
    _select(f, 100, 50, 200, 150)
    f.select_option("#ws-generate select.mode", "fill")
    _generate_and_apply(f, "a pond")
    graph = fake.prompts[-1]
    text = next(n["inputs"]["text"] for n in graph.values() if n["class_type"] == "CLIPTextEncode")
    assert text.startswith("Fill the green spaces according to the image.") and "a pond" in text
    refs = [fake.uploads[n["inputs"]["image"]] for n in graph.values() if n["class_type"] == "LoadImage" and n.get("_meta", {}).get("title") == "Reference"]
    ref = refs[0].convert("RGB")
    assert ref.getpixel((ref.size[0] // 2, ref.size[1] // 2)) == (0, 255, 0)
    f.select_option("#ws-generate select.mode", "add")
    _generate_and_apply(f, "a duck")
    text = next(n["inputs"]["text"] for n in fake.prompts[-1].values() if n["class_type"] == "CLIPTextEncode")
    assert text == "Add the object to the scene. a duck"


def test_server_address_survives_a_restart(tmp_path):
    """The panel can't write files: set-server.sh writes config.local.js, which the next start reads."""
    pytest.importorskip("playwright")
    import shutil
    import subprocess

    from fake_comfy import FakeComfy
    from harness import PLUGIN_DIR, Harness

    plugin = tmp_path / "comfy_suite"
    shutil.copytree(PLUGIN_DIR, plugin, ignore=shutil.ignore_patterns("config.local.js"))
    with FakeComfy(cors="*") as fake:
        def start():
            h = Harness(Path(WEB), plugin_dir=plugin)
            h.open()
            time.sleep(3)
            _open_document(h)
            h.page.get_by_text("Window", exact=True).first.click()
            h.page.get_by_text("ComfySuite AI", exact=True).click()
            f = h.plugin_frame()
            f.wait_for_selector("#tab-settings")
            return h, f

        # First run: the default address fails; typing ours works but shows how to keep it.
        h, f = start()
        try:
            f.click("#tab-settings")
            f.fill("#ws-settings input[type=text]", fake.url)
            f.click("#ws-settings button.primary")
            assert _wait(lambda: "ok" in f.get_attribute("#status", "class"))
            command = f.input_value("#ws-settings .persist textarea")
            assert command.endswith("set-server.sh " + fake.url)
        finally:
            h.close()

        subprocess.run(["sh", str(plugin / "set-server.sh"), fake.url], check=True, capture_output=True)

        # After a restart the panel connects to the saved server straight away.
        h, f = start()
        try:
            assert _wait(lambda: "ok" in f.get_attribute("#status", "class")), f.inner_text("#message")
            f.click("#tab-settings")
            assert f.input_value("#ws-settings input[type=text]") == fake.url
            assert f.is_hidden("#ws-settings .persist")
        finally:
            h.close()
