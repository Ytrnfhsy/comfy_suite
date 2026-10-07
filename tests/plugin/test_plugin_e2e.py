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


def _png(w, h, fill=(255, 255, 255)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), fill).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _wait(cond, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.25)
    return None


def _open_document(h):
    """Open a 400×300 white document the way a plugin or a drop would: an ArrayBuffer message.
    (The sidebar, and so the panel, only shows with a document open.)"""
    h.page.evaluate(
        "(b64) => { const s = atob(b64), u = new Uint8Array(s.length);"
        " for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); window.postMessage(u.buffer, '*'); }",
        _png(400, 300),
    )
    time.sleep(2)


@pytest.fixture(params=["*", "null"], ids=["cors-any", "cors-null"])
def cors(request):
    """--enable-cors-header (any origin) and --enable-cors-header null (only sandboxed pages)."""
    return request.param


@pytest.fixture
def ps(cors):
    pytest.importorskip("playwright")
    from fake_comfy import FakeComfy
    from harness import Harness

    with FakeComfy(color=(220, 40, 40), cors=cors) as fake:
        h = Harness(Path(WEB))
        try:
            h.open()
            time.sleep(3)
            _open_document(h)
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
