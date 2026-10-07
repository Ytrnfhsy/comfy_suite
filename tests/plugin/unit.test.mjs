// Unit tests for the plugin's pure logic: node --test tests/plugin/unit.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const dir = new URL("../../photosuite-plugin/comfy_suite/", import.meta.url);
for (const f of ["i18n.js", "styles.js", "workflow.js", "imaging.js"]) vm.runInThisContext(readFileSync(new URL(f, dir), "utf8"), { filename: f });
const { styles, workflow: W, imaging: I } = globalThis.CS;

const INFO = {
  CheckpointLoaderSimple: { input: { required: { ckpt_name: [["sd15_dreamshaper.safetensors", "sdxl_base.safetensors", "flux1-dev.safetensors"]] } } },
  LoraLoader: { input: { required: { lora_name: [["detail.safetensors"]] } } },
  ControlNetLoader: { input: { required: { control_net_name: [["control_sd15_scribble.pth", "controlnet-union-sdxl-promax.safetensors"]] } } },
  UpscaleModelLoader: { input: { required: { model_name: [["4x.pth"]] } } },
  KSampler: { input: { required: { sampler_name: [["euler", "dpmpp_2m"]], scheduler: ["COMBO", { options: ["normal", "karras"] }] } } },
  VAEEncodeTiled: { input: { required: { temporal_size: ["INT", { default: 64 }] } } },
  Canny: { input: { required: {} } },
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
  assert.ok(big.width % 8 === 0 && big.width * big.height <= 1.5 * 1024 * 1024 * 1.02);
  assert.deepEqual(I.inpaintContext(I.rect(10, 10, 100, 100), I.rect(0, 0, 500, 400)), I.rect(0, 0, 142, 142));
  const m = new Uint8Array(64 * 64); for (let y = 24; y < 40; y++) for (let x = 24; x < 40; x++) m[y * 64 + x] = 255;
  const p = I.prepareMask(m, 64, 64, 4, 4);
  assert.ok(p[32 * 64 + 21] > 150 && p[2 * 64 + 2] === 0);
  const r = I.maskForRegion(new Uint8Array([1, 2, 3, 4]), I.rect(5, 5, 2, 2), I.rect(4, 4, 4, 4));
  assert.deepEqual(Array.from(r), [0,0,0,0, 0,1,2,0, 0,3,4,0, 0,0,0,0]);
});

test("Ukrainian strings", () => {
  globalThis.CS.setLanguage("uk");
  assert.equal(globalThis.CS.tr("Generate"), "Генерувати");
  globalThis.CS.setLanguage("en");
  assert.equal(globalThis.CS.tr("Generate"), "Generate");
});
