"""End to end: a real headless PhotoSuite and a fake ComfyUI."""

import threading

import pytest
from fake_comfy import FakeComfy
from photosuite_server import photosuite  # noqa: F401 - fixture

from comfy_suite.comfy import ComfyClient
from comfy_suite.generator import ControlSpec, GenerateParams, Generator
from comfy_suite.image_ops import Bounds
from comfy_suite.jobs import JobQueue, JobState
from comfy_suite.photosuite import ControlClient, PhotoSuiteBridge, read_token
from comfy_suite.settings import Settings
from comfy_suite.styles import Style

STYLE = Style(architecture="sdxl", native_resolution=512)


def _gen(ps, fake) -> Generator:
    client = ControlClient(port=ps["port"], token=read_token(token_file=ps["token_file"]))
    return Generator(PhotoSuiteBridge(client, ps["exchange"]), ComfyClient(fake.url), Settings())


def test_generate_whole_canvas(photosuite):  # noqa: F811
    with FakeComfy(color=(10, 200, 10)) as fake:
        g = _gen(photosuite, fake)
        g.bridge.client.call("doc.new", {"width": 300, "height": 200, "background": "white"})
        r = g.generate(STYLE, GenerateParams(prompt="meadow", batch=2, seed=5))
        assert r.seed == 5 and r.region == Bounds(0, 0, 300, 200) and not r.into_selection
        assert [im.size for im in r.images] == [(300, 200)] * 2
        assert not fake.uploads  # text to image needs no canvas
        (latent,) = [n for n in fake.prompts[0].values() if n["class_type"] == "EmptyLatentImage"]
        assert latent["inputs"]["width"] % 16 == 0 and latent["inputs"]["width"] > 300  # scaled to native
        g.apply(r, 1)
        doc = g.bridge.document()
        assert doc.layers[0].name.startswith("[AI] meadow")
        assert g.bridge.client.execute("document.pixel", {"x": 150, "y": 100})[:3] == pytest.approx([10 / 255, 200 / 255, 10 / 255], abs=1e-6)


def test_inpaint_selection_with_control_layer(photosuite):  # noqa: F811
    with FakeComfy(color=(250, 0, 250)) as fake:
        g = _gen(photosuite, fake)
        c = g.bridge.client
        c.call("doc.new", {"width": 400, "height": 300, "background": "white"})
        c.execute("layer.new.layer", {"name": "Sketch"})
        c.execute("paint.stroke", {"points": [[0, 0, 1], [400, 300, 1]], "size": 6, "color": "#000000"})
        c.execute("select.rect", {"x": 100, "y": 100, "width": 80, "height": 60})
        sketch = g.bridge.document().layers[0].id
        r = g.generate(STYLE, GenerateParams(prompt="a bird", controls=[ControlSpec("canny", layer=sketch, strength=0.7)]))
        assert r.into_selection
        assert r.region.x < 100 and r.region.x1 > 180  # selection plus context
        names = {n["class_type"] for n in fake.prompts[0].values()}
        assert {"InpaintModelConditioning", "LoadImageMask", "ControlNetApplyAdvanced", "Canny"} <= names
        assert len(fake.uploads) == 3  # canvas, mask, control
        mask = next(im for name, im in fake.uploads.items() if im.mode == "L")
        assert mask.getextrema() == (0, 255)
        g.apply(r)
        px = lambda x, y: c.execute("document.pixel", {"x": x, "y": y})[:3]  # noqa: E731
        assert px(140, 130) == pytest.approx([250 / 255, 0.0, 250 / 255], abs=1e-6)
        assert px(r.region.x + 1, r.region.y + 1)[1] > 0.9  # outside the selection: untouched white
        assert g.bridge.document().selection == Bounds(100, 100, 80, 60)


def test_refine_and_upscale(photosuite):  # noqa: F811
    with FakeComfy() as fake:
        g = _gen(photosuite, fake)
        g.bridge.client.call("doc.new", {"width": 120, "height": 80, "background": "white"})
        g.generate(STYLE, GenerateParams(prompt="", strength=0.4, use_selection=False))
        ks = next(n for n in fake.prompts[-1].values() if n["class_type"] == "KSampler")
        assert ks["inputs"]["denoise"] == 0.4 and len(fake.uploads) == 1
        up = g.upscale(STYLE, 2.0, model="4x-UltraSharp.pth")
        assert up.new_document and up.images[0].size == (240, 160)
        g.apply(up)
        s = g.bridge.session()
        assert s["documents"][s["active"]]["width"] == 240


def test_job_queue_runs_and_cancels():
    q = JobQueue(history_size=2)
    done = threading.Event()
    seen = []

    def listener(job):
        seen.append(job.state)
        if job.finished:
            done.set()

    q.subscribe(listener)
    gate = threading.Event()

    def slow(progress, cancel):
        progress(0.5, "half")
        gate.wait(5)
        raise RuntimeError("bad")

    job = q.submit("generate", "t", slow)
    queued = q.submit("generate", "t2", lambda p, c: None)
    q.cancel(queued)
    gate.set()
    assert done.wait(5)
    for _ in range(50):
        if job.finished:
            break
        threading.Event().wait(0.05)
    assert job.state == JobState.FAILED and job.error == "bad"
    assert queued.state == JobState.CANCELLED
    q.shutdown()
