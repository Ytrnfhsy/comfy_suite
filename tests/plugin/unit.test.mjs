// Unit tests for the plugin's pure logic: node --test tests/plugin/unit.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const dir = new URL("../../photosuite-plugin/comfy_suite/", import.meta.url);
for (const f of ["i18n.js", "styles.js", "workflow.js", "imaging.js", "inpaint.js"]) vm.runInThisContext(readFileSync(new URL(f, dir), "utf8"), { filename: f });
const { styles, workflow: W, imaging: I, inpaint: IP } = globalThis.CS;

const INFO = {
  CheckpointLoaderSimple: { input: { required: { ckpt_name: [["sd15_dreamshaper.safetensors", "sdxl_base.safetensors", "flux1-dev.safetensors"]] } } },
  LoraLoader: { input: { required: { lora_name: [["detail.safetensors"]] } } },
  ControlNetLoader: { input: { required: { control_net_name: [["control_sd15_scribble.pth", "controlnet-union-sdxl-promax.safetensors"]] } } },
  UpscaleModelLoader: { input: { required: { model_name: [["4x.pth"]] } } },
  KSampler: { input: { required: { sampler_name: [["euler", "dpmpp_2m"]], scheduler: ["COMBO", { options: ["normal", "karras"] }] } } },
  VAEEncodeTiled: { input: { required: { temporal_size: ["INT", { default: 64 }] } } },
  Canny: { input: { required: {} } },
  UNETLoader: { input: { required: { unet_name: [["flux2_dev_fp8mixed.safetensors", "flux-2-klein-4b.safetensors", "flux-2-klein-base-9b.safetensors"]], weight_dtype: [["default", "fp8_e4m3fn"]] } } },
  CLIPLoader: { input: { required: { clip_name: [["mistral_3_small_flux2_bf16.safetensors", "qwen_3_4b.safetensors", "qwen_3_8b_fp8mixed.safetensors"]], type: [["stable_diffusion", "flux2"]] } } },
  VAELoader: { input: { required: { vae_name: [["sdxl_vae.safetensors", "flux2-vae.safetensors"]] } } },
};
const MODELS = W.parseModels(INFO);
const SDXL = styles.normalize({ name: "x", architecture: "sdxl", style_prompt: "photo, {prompt}", negative_prompt: "blurry" });
const nodes = (g, t) => g.byType(t);

test("models are parsed from object_info (list and COMBO forms)", () => {
  assert.deepEqual(MODELS.schedulers, ["normal", "karras"]);
  assert.equal(MODELS.upscalers[0], "4x.pth");
});

test("styles: prompt template and checkpoint choice", () => {
  assert.equal(styles.applyPrompt(SDXL, "a cat"), "photo, a cat");
  assert.equal(styles.applyPrompt(SDXL, ""), "photo");
  assert.equal(styles.resolveCheckpoint(SDXL, MODELS), "sdxl_base.safetensors");
  assert.throws(() => styles.resolveCheckpoint({ checkpoint: "nope" }, MODELS));
  const lib = new styles.StyleLibrary([{ name: "Flux", steps: 4 }]);
  assert.equal(lib.get("Flux").steps, 4);
  assert.equal(lib.custom().length, 1);
});

test("text to image", () => {
  const g = W.buildGenerate(SDXL, MODELS, { prompt: "cat", strength: 1, seed: 7, batch: 3, width: 1024, height: 768 });
  assert.deepEqual(nodes(g, "EmptyLatentImage")[0].inputs, { width: 1024, height: 768, batch_size: 3 });
  assert.equal(nodes(g, "KSampler")[0].inputs.seed, 7);
  assert.deepEqual(nodes(g, "CLIPTextEncode").map(n => n.inputs.text), ["photo, cat", "blurry"]);
  assert.equal(nodes(g, "RepeatLatentBatch").length, 0);
});

test("refine and inpaint", () => {
  assert.throws(() => W.buildGenerate(SDXL, MODELS, { strength: 0.5, width: 8, height: 8 }));
  let g = W.buildGenerate(SDXL, MODELS, { strength: 0.4, image: "a.png", batch: 2, seed: 1, width: 512, height: 512 });
  assert.equal(nodes(g, "VAEEncode").length, 1);
  assert.equal(nodes(g, "RepeatLatentBatch")[0].inputs.amount, 2);
  assert.equal(nodes(g, "KSampler")[0].inputs.denoise, 0.4);
  g = W.buildGenerate(SDXL, MODELS, { strength: 1, image: "a.png", mask: "m.png", seed: 1, width: 512, height: 512 });
  const cond = Object.entries(g.nodes).find(([, n]) => n.class_type === "InpaintModelConditioning")[0];
  assert.deepEqual(nodes(g, "KSampler")[0].inputs.latent_image, [cond, 2]);
});

test("flux, loras, controlnet", () => {
  const flux = styles.normalize({ architecture: "flux", guidance: 2.5, loras: [{ name: "detail.safetensors", strength: 0.6 }] });
  let g = W.buildGenerate(flux, MODELS, { prompt: "x", strength: 1, seed: 1, width: 64, height: 64 });
  assert.equal(nodes(g, "FluxGuidance")[0].inputs.guidance, 2.5);
  assert.equal(nodes(g, "EmptySD3LatentImage").length, 1);
  assert.equal(nodes(g, "LoraLoader")[0].inputs.strength_model, 0.6);
  g = W.buildGenerate(SDXL, MODELS, { prompt: "x", strength: 1, seed: 1, width: 64, height: 64,
    controls: [{ mode: "canny", image: "c.png", strength: 0.8, start: 0, end: 1 }] });
  assert.equal(nodes(g, "Canny").length, 1);
  assert.equal(nodes(g, "ControlNetLoader")[0].inputs.control_net_name, "controlnet-union-sdxl-promax.safetensors");
  assert.equal(W.pickControlnet("scribble", MODELS, "sd15"), "control_sd15_scribble.pth");
  assert.throws(() => W.buildGenerate(SDXL, MODELS, { strength: 1, seed: 1, width: 8, height: 8, controls: [{ mode: "reference", image: "r", strength: 1 }] }), /IPAdapter/);
});

test("fill defaults and missing nodes", () => {
  const g = W.buildUpscale(SDXL, MODELS, { image: "a", width: 64, height: 64, refine: true, strength: 0.3, seed: 1 }).toJSON();
  const info = Object.assign({}, INFO);
  for (const n of Object.values(g)) if (!info[n.class_type]) info[n.class_type] = { input: { required: {} } };
  W.fillDefaults(g, info);
  assert.equal(Object.values(g).find(n => n.class_type === "VAEEncodeTiled").inputs.temporal_size, 64);
  assert.throws(() => W.fillDefaults({ 1: { class_type: "Nope", inputs: {} } }, INFO), /missing/);
});

test("custom workflows", () => {
  const wf = {
    1: { class_type: "LoadImage", inputs: { image: "x" }, _meta: { title: "PhotoSuite Canvas" } },
    2: { class_type: "CLIPTextEncode", inputs: { text: "{{prompt}}, best" } },
    3: { class_type: "KSampler", inputs: { seed: "{{seed}}" } },
  };
  assert.deepEqual(W.customPlaceholders(wf), ["canvas", "prompt", "seed"]);
  const out = W.applyCustom(wf, { canvas: "up.png", prompt: "fox", seed: 42 });
  assert.equal(out[1].inputs.image, "up.png");
  assert.equal(out[2].inputs.text, "fox, best");
  assert.equal(out[3].inputs.seed, 42);
  assert.throws(() => W.loadCustomWorkflow('{"nodes":[],"links":[]}'), /Export \(API\)/);
});

test("geometry and masks", () => {
  assert.deepEqual(I.generationExtent(256, 256, 1024), { width: 1024, height: 1024 });
  const big = I.generationExtent(6000, 4000, 1024);
  assert.ok(big.width % 16 === 0 && big.height % 16 === 0 && big.width * big.height <= 1.5 * 1024 * 1024 * 1.02);
  assert.deepEqual(I.inpaintContext(I.rect(10, 10, 100, 100), I.rect(0, 0, 500, 400)), I.rect(0, 0, 142, 142));
  const m = new Uint8Array(64 * 64); for (let y = 24; y < 40; y++) for (let x = 24; x < 40; x++) m[y * 64 + x] = 255;
  const p = I.prepareMask(m, 64, 64, 4, 4);
  assert.ok(p[32 * 64 + 21] > 150 && p[2 * 64 + 2] === 0);
  const r = I.maskForRegion(new Uint8Array([1, 2, 3, 4]), I.rect(5, 5, 2, 2), I.rect(4, 4, 4, 4));
  assert.deepEqual(Array.from(r), [0,0,0,0, 0,1,2,0, 0,3,4,0, 0,0,0,0]);
});

const FLUX2 = () => styles.normalize(styles.BUILTIN.find(s => s.name === "Flux 2 Dev"));
const KLEIN = () => styles.normalize(styles.BUILTIN.find(s => s.name === "Flux 2 Klein"));
const input = (g, t, k) => nodes(g, t)[0].inputs[k];
const linkTo = (g, t) => Object.entries(g.nodes).find(([, n]) => n.class_type === t)[0];

test("flux 2: model files are picked like ComfyUI's templates", () => {
  assert.equal(styles.guessArchitecture("flux2_dev_fp8mixed.safetensors"), "flux2");
  assert.equal(styles.guessArchitecture("flux-2-klein-4b.safetensors"), "flux2");
  assert.equal(styles.guessArchitecture("flux1-dev.safetensors"), "flux");
  assert.deepEqual(styles.resolveFlux2(FLUX2(), MODELS), { unet: "flux2_dev_fp8mixed.safetensors", clip: "mistral_3_small_flux2_bf16.safetensors", vae: "flux2-vae.safetensors" });
  assert.deepEqual(styles.resolveFlux2(KLEIN(), MODELS), { unet: "flux-2-klein-4b.safetensors", clip: "qwen_3_4b.safetensors", vae: "flux2-vae.safetensors" });
  const nine = Object.assign(KLEIN(), { diffusion_model: "base-9b" });
  assert.equal(styles.resolveFlux2(nine, MODELS).clip, "qwen_3_8b_fp8mixed.safetensors");
  assert.throws(() => styles.resolveFlux2(Object.assign(FLUX2(), { diffusion_model: "nope" }), MODELS), /not installed/);
});

test("flux 2: text to image uses the custom sampler chain", () => {
  const g = W.buildGenerate(FLUX2(), MODELS, { prompt: "a fox", strength: 1, seed: 5, batch: 2, width: 1024, height: 768 });
  assert.equal(nodes(g, "CheckpointLoaderSimple").length, 0);
  assert.equal(input(g, "CLIPLoader", "type"), "flux2");
  assert.deepEqual(nodes(g, "EmptyFlux2LatentImage")[0].inputs, { width: 1024, height: 768, batch_size: 2 });
  assert.deepEqual(nodes(g, "Flux2Scheduler")[0].inputs, { steps: 20, width: 1024, height: 768 });
  assert.equal(input(g, "FluxGuidance", "guidance"), 4);
  assert.equal(input(g, "RandomNoise", "noise_seed"), 5);
  assert.equal(nodes(g, "KSampler").length, 0);
  assert.deepEqual(input(g, "BasicGuider", "conditioning"), [linkTo(g, "FluxGuidance"), 0]);
  assert.deepEqual(input(g, "VAEDecode", "samples"), [linkTo(g, "SamplerCustomAdvanced"), 0]);
  // Klein base with cfg > 1 uses CFGGuider and the negative prompt.
  const base = Object.assign(KLEIN(), { cfg: 5, steps: 20 });
  const gb = W.buildGenerate(base, MODELS, { prompt: "x", negative: "blur", strength: 1, seed: 1, width: 512, height: 512 });
  assert.equal(input(gb, "CFGGuider", "cfg"), 5);
  assert.equal(nodes(gb, "BasicGuider").length, 0);
});

test("flux 2: refine and inpaint keep the canvas as reference and mask the noise", () => {
  const g = W.buildGenerate(FLUX2(), MODELS, { prompt: "x", strength: 0.5, image: "a.png", mask: "m.png", seed: 1, width: 512, height: 512 });
  assert.deepEqual(input(g, "SetLatentNoiseMask", "mask"), [linkTo(g, "LoadImageMask"), 0]);
  assert.deepEqual(input(g, "SamplerCustomAdvanced", "latent_image"), [linkTo(g, "SetLatentNoiseMask"), 0]);
  assert.equal(input(g, "SplitSigmasDenoise", "denoise"), 0.5);
  assert.deepEqual(input(g, "SamplerCustomAdvanced", "sigmas"), [linkTo(g, "SplitSigmasDenoise"), 1]);
  assert.equal(nodes(g, "ReferenceLatent").length, 2);
  assert.equal(nodes(g, "InpaintModelConditioning").length, 0);
  const noRef = Object.assign(FLUX2(), { flux2_reference: false });
  assert.equal(nodes(W.buildGenerate(noRef, MODELS, { strength: 0.5, image: "a.png", seed: 1, width: 64, height: 64 }), "ReferenceLatent").length, 0);
});

test("flux 2: references, loras, no controlnet, upscale refine", () => {
  const st = Object.assign(FLUX2(), { loras: [{ name: "detail.safetensors", strength: 0.7, enabled: true }] });
  const g = W.buildGenerate(st, MODELS, { prompt: "x", strength: 1, seed: 1, width: 512, height: 512,
    controls: [{ mode: "reference", image: "r.png", strength: 1, start: 0, end: 1 }] });
  assert.equal(nodes(g, "ReferenceLatent").length, 2);
  assert.equal(input(g, "LoraLoaderModelOnly", "strength_model"), 0.7);
  assert.equal(nodes(g, "LoraLoader").length, 0);
  assert.throws(() => W.buildGenerate(FLUX2(), MODELS, { strength: 1, seed: 1, width: 64, height: 64,
    controls: [{ mode: "depth", image: "d.png", strength: 1 }] }), /reference/);
  const up = W.buildUpscale(FLUX2(), MODELS, { image: "a", width: 2048, height: 2048, refine: true, strength: 0.3, seed: 1 });
  assert.equal(input(up, "SplitSigmasDenoise", "denoise"), 0.3);
  assert.deepEqual(nodes(up, "Flux2Scheduler")[0].inputs, { steps: 20, width: 2048, height: 2048 });
});

test("inpaint modes are planned like Krita's", () => {
  assert.equal(IP.resolveMode("auto", 0), "fill");
  assert.equal(IP.resolveMode("auto", 0.9), "expand");
  assert.equal(IP.resolveMode("remove", 0.9), "remove");
  assert.equal(IP.plan("fill", "sdxl", 1).fill, "blur");
  assert.equal(IP.plan("expand", "sdxl", 1).fill, "border");
  assert.equal(IP.plan("add", "sdxl", 1).fill, "neutral");
  const rm = IP.plan("remove", "sdxl", 1);
  assert.equal(IP.composePrompt(rm, ""), "background scenery");
  assert.equal(IP.composePrompt(rm, "grass"), "grass");
  const bg = IP.plan("background", "sdxl", 1);
  assert.ok(bg.invert && bg.context === "image" && bg.featherScale < 1);
  assert.equal(IP.plan("fill", "sdxl", 0.5).mode, "refine");
  assert.equal(IP.plan("custom", "sdxl", 1, { context: "mask" }).context, "mask");
  const f2 = IP.plan("fill", "flux2", 1);
  assert.equal(f2.referenceFill, "green");
  assert.equal(IP.composePrompt(f2, "a lake"), "Fill the green spaces according to the image. a lake");
  assert.equal(IP.composePrompt(IP.plan("remove", "flux2", 1), ""), "Remove the object.");
});

test("pre-fill: blur/border take the surroundings, neutral is grey, green marks the area", () => {
  const w = 32, h = 32, rgba = new Uint8ClampedArray(w * h * 4), weight = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) { rgba.set([0, 0, 255, 255], i * 4); }
  for (let y = 12; y < 20; y++) for (let x = 12; x < 20; x++) { const i = y * w + x; rgba.set([255, 0, 0, 255], i * 4); weight[i] = 255; }
  const center = (out) => Array.from(out.slice((16 * w + 16) * 4, (16 * w + 16) * 4 + 4));
  for (const mode of ["blur", "border"]) {
    const c = center(I.prefill(rgba, w, h, weight, mode));
    assert.ok(c[2] > 200 && c[0] < 40, mode + " " + c);  // blue from around, the red object gone
  }
  assert.deepEqual(center(I.prefill(rgba, w, h, weight, "neutral")), [128, 128, 128, 255]);
  assert.deepEqual(center(I.prefill(rgba, w, h, weight, "green")), [0, 255, 0, 255]);
  const corner = Array.from(I.prefill(rgba, w, h, weight, "border").slice(0, 4));
  assert.deepEqual(corner, [0, 0, 255, 255]);  // outside the mask: untouched
});

test("transparent canvas becomes the expand mask", () => {
  const w = 10, h = 4, rgba = new Uint8ClampedArray(w * h * 4).fill(255);
  for (let y = 0; y < h; y++) for (let x = 6; x < w; x++) rgba[(y * w + x) * 4 + 3] = 0;
  const m = IP.transparentMask(rgba, w, h);
  assert.deepEqual(m.rect, { x: 6, y: 0, width: 4, height: 4 });
  assert.ok(m.bytes.every(v => v === 255));
  assert.equal(IP.transparentMask(new Uint8ClampedArray(w * h * 4).fill(255), w, h), null);
});

test("flux 2 reference image can differ from the start image", () => {
  const g = W.buildGenerate(FLUX2(), MODELS, { prompt: "x", strength: 1, image: "prefilled.png", reference: "green.png", mask: "m.png", seed: 1, width: 64, height: 64 });
  const loads = nodes(g, "LoadImage").map(n => n.inputs.image);
  assert.deepEqual(loads.sort(), ["green.png", "prefilled.png"]);
});

test("Ukrainian strings", () => {
  globalThis.CS.setLanguage("uk");
  assert.equal(globalThis.CS.tr("Generate"), "Генерувати");
  globalThis.CS.setLanguage("en");
  assert.equal(globalThis.CS.tr("Generate"), "Generate");
});
