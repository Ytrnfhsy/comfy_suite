import threading

import pytest
from fake_comfy import FakeComfy
from PIL import Image

from comfy_suite.comfy import Cancelled, ComfyClient, ComfyError, _ProgressWatcher
from comfy_suite.workflow import GenerateRequest, build_generate
from comfy_suite.styles import Style


def test_models_and_upload():
    with FakeComfy() as fake:
        c = ComfyClient(fake.url)
        m = c.models()
        assert "sdxl_base.safetensors" in m.checkpoints
        assert m.upscalers == ["4x-UltraSharp.pth"]
        assert m.samplers[0] == "euler"
        name = c.upload_image(Image.new("RGB", (10, 20), (1, 2, 3)))
        assert name.startswith("comfy-suite/")
        assert fake.uploads[name].size == (10, 20)


def test_run_returns_images():
    with FakeComfy(color=(0, 255, 0)) as fake:
        c = ComfyClient(fake.url)
        g = build_generate(Style(architecture="sdxl"), c.models(), GenerateRequest(prompt="x", batch=2, width=128, height=64)).to_json()
        steps = []
        images = c.run(g, progress=lambda v, t: steps.append(v))
        assert [im.size for im in images] == [(128, 64), (128, 64)]
        assert images[0].getpixel((0, 0)) == (0, 255, 0)
        assert steps[-1] == 1.0


def test_errors_are_reported():
    with FakeComfy(fail="out of memory") as fake:
        c = ComfyClient(fake.url)
        g = build_generate(Style(), c.models(), GenerateRequest(prompt="x")).to_json()
        with pytest.raises(ComfyError, match="out of memory"):
            c.run(g)
    with FakeComfy() as fake:
        with pytest.raises(ComfyError, match="unknown nodes"):
            ComfyClient(fake.url).queue({"1": {"class_type": "Nope", "inputs": {}}})
    with pytest.raises(ComfyError, match="cannot reach"):
        ComfyClient("http://127.0.0.1:9", timeout=1).system_stats()


def test_cancel_interrupts():
    with FakeComfy() as fake:
        fake.execute = lambda pid, graph: None  # never finishes
        c = ComfyClient(fake.url)
        ev = threading.Event()
        ev.set()
        g = build_generate(Style(), c.models(), GenerateRequest(prompt="x")).to_json()
        with pytest.raises(Cancelled):
            c.run(g, cancel=ev)
        assert fake.interrupted == 1 and len(fake.deleted) == 1


def test_progress_messages():
    seen = []
    w = _ProgressWatcher(ComfyClient(), lambda v, t: seen.append((v, t)))
    w.prompt_id = "p"
    w.handle({"type": "progress", "data": {"value": 5, "max": 10, "prompt_id": "p"}})
    w.handle({"type": "progress", "data": {"value": 9, "max": 10, "prompt_id": "other"}})
    assert seen == [(0.5, "step 5/10")]
    w.handle({"type": "execution_error", "data": {"prompt_id": "p", "node_type": "KSampler", "exception_message": "boom"}})
    assert w.done.is_set() and "boom" in w.error
