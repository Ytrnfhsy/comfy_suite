import json

import pytest
from fake_comfy import OBJECT_INFO

from comfy_suite.comfy import parse_models
from comfy_suite.styles import LoraRef, Style
from comfy_suite.workflow import (
    ControlInput,
    GenerateRequest,
    UpscaleRequest,
    WorkflowError,
    apply_custom,
    build_generate,
    build_upscale,
    custom_placeholders,
    fill_defaults,
    load_custom_workflow,
    pick_controlnet,
)

MODELS = parse_models(OBJECT_INFO)
SDXL = Style(architecture="sdxl", style_prompt="photo, {prompt}", negative_prompt="blurry", sampler="dpmpp_2m", scheduler="karras", steps=20, cfg=6)


def nodes(graph, class_type):
    return [n for n in graph.nodes.values() if n["class_type"] == class_type]


def assert_links_valid(graph):
    """Every link points at an existing node."""
    for node in graph.nodes.values():
        for v in node["inputs"].values():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                assert v[0] in graph.nodes, v


def test_text_to_image():
    g = build_generate(SDXL, MODELS, GenerateRequest(prompt="a cat", seed=7, batch=3, width=1024, height=768))
    assert_links_valid(g)
    (ckpt,) = nodes(g, "CheckpointLoaderSimple")
    assert ckpt["inputs"]["ckpt_name"] == "sdxl_base.safetensors"
    (latent,) = nodes(g, "EmptyLatentImage")
    assert latent["inputs"] == {"width": 1024, "height": 768, "batch_size": 3}
    assert not nodes(g, "RepeatLatentBatch")
    (ks,) = nodes(g, "KSampler")
    assert ks["inputs"]["seed"] == 7 and ks["inputs"]["denoise"] == 1.0 and ks["inputs"]["sampler_name"] == "dpmpp_2m"
    texts = [n["inputs"]["text"] for n in nodes(g, "CLIPTextEncode")]
    assert texts == ["photo, a cat", "blurry"]
    assert nodes(g, "PreviewImage")


def test_refine_encodes_the_canvas():
    g = build_generate(SDXL, MODELS, GenerateRequest(prompt="x", strength=0.4, batch=2, image="comfy-suite/a.png", width=512, height=512))
    assert_links_valid(g)
    assert nodes(g, "VAEEncode")
    assert nodes(g, "RepeatLatentBatch")[0]["inputs"]["amount"] == 2
    assert nodes(g, "KSampler")[0]["inputs"]["denoise"] == 0.4
    with pytest.raises(WorkflowError):
        build_generate(SDXL, MODELS, GenerateRequest(strength=0.5))


def test_inpaint_uses_mask_conditioning():
    g = build_generate(SDXL, MODELS, GenerateRequest(prompt="x", image="a.png", mask="m.png", width=512, height=512))
    assert_links_valid(g)
    (cond,) = nodes(g, "InpaintModelConditioning")
    (ks,) = nodes(g, "KSampler")
    cond_id = next(k for k, v in g.nodes.items() if v is cond)
    assert ks["inputs"]["positive"] == [cond_id, 0]
    assert ks["inputs"]["latent_image"] == [cond_id, 2]
    assert nodes(g, "LoadImageMask")[0]["inputs"]["image"] == "m.png"


def test_flux_uses_guidance_and_sd3_latent():
    flux = Style(architecture="flux", guidance=2.5, cfg=1)
    g = build_generate(flux, MODELS, GenerateRequest(prompt="x"))
    assert nodes(g, "FluxGuidance")[0]["inputs"]["guidance"] == 2.5
    assert nodes(g, "EmptySD3LatentImage")
    assert nodes(g, "CheckpointLoaderSimple")[0]["inputs"]["ckpt_name"].startswith("flux")


def test_loras_vae_and_clip_skip_chain():
    s = Style(architecture="sd15", vae="sdxl_vae.safetensors", clip_skip=2, loras=[LoraRef("detail.safetensors", 0.6), LoraRef("off.safetensors", 1, enabled=False)])
    g = build_generate(s, MODELS, GenerateRequest(prompt="x"))
    assert_links_valid(g)
    (lora,) = nodes(g, "LoraLoader")
    assert lora["inputs"]["strength_model"] == 0.6
    assert nodes(g, "CLIPSetLastLayer")[0]["inputs"]["stop_at_clip_layer"] == -2
    assert nodes(g, "VAELoader")
    with pytest.raises(WorkflowError):
        build_generate(Style(loras=[LoraRef("missing.safetensors")]), MODELS, GenerateRequest())


def test_live_uses_live_sampling():
    s = Style(live_sampler="euler_ancestral", live_steps=6, live_cfg=2)
    ks = nodes(build_generate(s, MODELS, GenerateRequest(image="a.png", strength=0.5, live=True)), "KSampler")[0]["inputs"]
    assert (ks["sampler_name"], ks["steps"], ks["cfg"]) == ("euler_ancestral", 6, 2)


def test_controlnet_and_preprocessor():
    req = GenerateRequest(prompt="x", controls=[ControlInput("canny", "c.png", 0.8, 0.0, 0.9), ControlInput("scribble", "s.png", 0.0)])
    g = build_generate(SDXL, MODELS, req)
    assert_links_valid(g)
    assert len(nodes(g, "Canny")) == 1  # built-in preprocessor; zero-strength control skipped
    (apply,) = nodes(g, "ControlNetApplyAdvanced")
    assert apply["inputs"]["strength"] == 0.8 and apply["inputs"]["end_percent"] == 0.9
    # No canny model installed: falls back to the union model for the architecture.
    assert nodes(g, "ControlNetLoader")[0]["inputs"]["control_net_name"] == "controlnet-union-sdxl-promax.safetensors"


def test_pick_controlnet():
    assert pick_controlnet("scribble", MODELS, "sd15") == "control_sd15_scribble.pth"
    with pytest.raises(WorkflowError):
        pick_controlnet("depth", parse_models({}), "sdxl")


def test_reference_needs_ipadapter_nodes():
    req = GenerateRequest(prompt="x", controls=[ControlInput("reference", "r.png")])
    with pytest.raises(WorkflowError, match="IPAdapter"):
        build_generate(SDXL, MODELS, req)
    info = dict(OBJECT_INFO, IPAdapterUnifiedLoader={"input": {"required": {}}}, IPAdapterAdvanced={"input": {"required": {}}})
    g = build_generate(SDXL, parse_models(info), req)
    (ks,) = nodes(g, "KSampler")
    ip_id = next(k for k, v in g.nodes.items() if v["class_type"] == "IPAdapterAdvanced")
    assert ks["inputs"]["model"] == [ip_id, 0]


def test_upscale():
    g = build_upscale(SDXL, MODELS, UpscaleRequest("a.png", 2048, 1536, model="4x-UltraSharp.pth"))
    assert_links_valid(g)
    assert nodes(g, "ImageUpscaleWithModel") and not nodes(g, "KSampler")
    g = build_upscale(SDXL, MODELS, UpscaleRequest("a.png", 2048, 1536, refine=True, strength=0.25))
    assert nodes(g, "KSampler")[0]["inputs"]["denoise"] == 0.25
    assert nodes(g, "VAEDecodeTiled")


def test_fill_defaults():
    g = build_upscale(SDXL, MODELS, UpscaleRequest("a.png", 64, 64, refine=True)).to_json()
    fill_defaults(g, OBJECT_INFO)
    enc = next(n for n in g.values() if n["class_type"] == "VAEEncodeTiled")
    assert enc["inputs"]["temporal_size"] == 64
    with pytest.raises(WorkflowError, match="missing"):
        fill_defaults({"1": {"class_type": "Nope", "inputs": {}}}, OBJECT_INFO)


CUSTOM = {
    "1": {"class_type": "LoadImage", "inputs": {"image": "x.png"}, "_meta": {"title": "PhotoSuite Canvas"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}, masterpiece"}},
    "3": {"class_type": "KSampler", "inputs": {"seed": "{{seed}}", "denoise": "{{strength}}", "steps": 20}},
    "4": {"class_type": "PreviewImage", "inputs": {"images": ["1", 0]}},
}


def test_custom_workflow_placeholders():
    assert custom_placeholders(CUSTOM) == ["canvas", "prompt", "seed", "strength"]
    out = apply_custom(CUSTOM, {"canvas": "up.png", "prompt": "a fox", "seed": 42, "strength": 0.5})
    assert out["1"]["inputs"]["image"] == "up.png"
    assert out["2"]["inputs"]["text"] == "a fox, masterpiece"
    assert out["3"]["inputs"]["seed"] == 42 and out["3"]["inputs"]["denoise"] == 0.5
    assert CUSTOM["3"]["inputs"]["seed"] == "{{seed}}"  # original untouched


def test_load_custom_workflow_rejects_ui_format():
    assert load_custom_workflow(json.dumps(CUSTOM)) == CUSTOM
    with pytest.raises(WorkflowError, match="Export \\(API\\)"):
        load_custom_workflow(json.dumps({"nodes": [], "links": []}))
    with pytest.raises(WorkflowError):
        load_custom_workflow("{not json")


# ---- Flux 2 ---------------------------------------------------------------------------------------

FLUX2_INFO = dict(
    OBJECT_INFO,
    UNETLoader={"input": {"required": {"unet_name": [["flux2_dev_fp8mixed.safetensors", "flux-2-klein-4b.safetensors", "flux-2-klein-base-9b.safetensors"]]}}},
    CLIPLoader={"input": {"required": {"clip_name": [["mistral_3_small_flux2_bf16.safetensors", "qwen_3_4b.safetensors", "qwen_3_8b_fp8mixed.safetensors"]]}}},
    VAELoader={"input": {"required": {"vae_name": [["sdxl_vae.safetensors", "flux2-vae.safetensors"]]}}},
)
FLUX2_MODELS = parse_models(FLUX2_INFO)


def _link(g, class_type, index=0):
    return [next(k for k, v in g.nodes.items() if v["class_type"] == class_type), index]


def test_flux2_files_are_picked_like_the_templates():
    from comfy_suite.styles import guess_architecture

    assert guess_architecture("flux2_dev_fp8mixed.safetensors") == "flux2"
    assert guess_architecture("flux-2-klein-4b.safetensors") == "flux2"
    assert guess_architecture("flux1-dev.safetensors") == "flux"
    assert Style(architecture="flux2").resolve_flux2(FLUX2_MODELS) == ("flux2_dev_fp8mixed.safetensors", "mistral_3_small_flux2_bf16.safetensors", "flux2-vae.safetensors")
    assert Style(architecture="flux2", diffusion_model="klein").resolve_flux2(FLUX2_MODELS)[1] == "qwen_3_4b.safetensors"
    assert Style(architecture="flux2", diffusion_model="base-9b").resolve_flux2(FLUX2_MODELS)[1] == "qwen_3_8b_fp8mixed.safetensors"
    with pytest.raises(ValueError):
        Style(architecture="flux2", diffusion_model="nope").resolve_flux2(FLUX2_MODELS)


def test_flux2_text_to_image():
    s = Style(architecture="flux2", guidance=4, cfg=1, steps=20)
    g = build_generate(s, FLUX2_MODELS, GenerateRequest(prompt="fox", seed=5, batch=2, width=1024, height=768))
    assert_links_valid(g)
    assert not nodes(g, "CheckpointLoaderSimple") and not nodes(g, "KSampler")
    assert nodes(g, "CLIPLoader")[0]["inputs"]["type"] == "flux2"
    assert nodes(g, "EmptyFlux2LatentImage")[0]["inputs"] == {"width": 1024, "height": 768, "batch_size": 2}
    assert nodes(g, "Flux2Scheduler")[0]["inputs"] == {"steps": 20, "width": 1024, "height": 768}
    assert nodes(g, "BasicGuider")[0]["inputs"]["conditioning"] == _link(g, "FluxGuidance")
    assert nodes(g, "VAEDecode")[0]["inputs"]["samples"] == _link(g, "SamplerCustomAdvanced")
    g = build_generate(Style(architecture="flux2", cfg=5), FLUX2_MODELS, GenerateRequest(prompt="x"))
    assert nodes(g, "CFGGuider")[0]["inputs"]["cfg"] == 5


def test_flux2_inpaint_reference_loras_and_controls():
    s = Style(architecture="flux2", loras=[LoraRef("detail.safetensors", 0.7)])
    g = build_generate(s, FLUX2_MODELS, GenerateRequest(prompt="x", strength=0.5, image="a.png", mask="m.png", width=512, height=512))
    assert_links_valid(g)
    assert nodes(g, "SamplerCustomAdvanced")[0]["inputs"]["latent_image"] == _link(g, "SetLatentNoiseMask")
    assert nodes(g, "SamplerCustomAdvanced")[0]["inputs"]["sigmas"] == _link(g, "SplitSigmasDenoise", 1)
    assert len(nodes(g, "ReferenceLatent")) == 2 and not nodes(g, "InpaintModelConditioning")
    assert nodes(g, "LoraLoaderModelOnly")[0]["inputs"]["strength_model"] == 0.7
    ref = GenerateRequest(prompt="x", controls=[ControlInput("reference", "r.png")])
    assert len(nodes(build_generate(Style(architecture="flux2"), FLUX2_MODELS, ref), "ReferenceLatent")) == 2
    with pytest.raises(WorkflowError, match="reference"):
        build_generate(Style(architecture="flux2"), FLUX2_MODELS, GenerateRequest(controls=[ControlInput("depth", "d.png")]))
    up = build_upscale(Style(architecture="flux2"), FLUX2_MODELS, UpscaleRequest("a.png", 2048, 2048, refine=True, strength=0.3))
    assert nodes(up, "SplitSigmasDenoise")[0]["inputs"]["denoise"] == 0.3
