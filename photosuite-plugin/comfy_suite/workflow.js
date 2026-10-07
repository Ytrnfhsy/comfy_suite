/* Building ComfyUI workflows (API format). Pure functions: images are referred to by the
   names LoadImage takes after upload, so graphs can be built and checked without a server. */
(function (CS) {
  "use strict";

  function Graph() { this.nodes = {}; this.next = 1; }
  Graph.prototype.add = function (classType, inputs, title) {
    var id = String(this.next++);
    var clean = {};
    Object.keys(inputs || {}).forEach(function (k) {
      var v = inputs[k];
      if (v === undefined || v === null) return;
      clean[k] = v;
    });
    this.nodes[id] = { class_type: classType, inputs: clean };
    if (title) this.nodes[id]._meta = { title: title };
    return { id: id, out: function (i) { return [id, i || 0]; } };
  };
  Graph.prototype.byType = function (t) {
    var self = this;
    return Object.keys(this.nodes).map(function (k) { return self.nodes[k]; }).filter(function (n) { return n.class_type === t; });
  };
  Graph.prototype.toJSON = function () { return JSON.parse(JSON.stringify(this.nodes)); };

  function choices(info, node, input) {
    try {
      var spec = info[node].input;
      var entry = (spec.required && spec.required[input]) || (spec.optional && spec.optional[input]);
      if (!entry) return [];
      if (Array.isArray(entry[0])) return entry[0].map(String);
      if (entry[0] === "COMBO" && entry[1] && entry[1].options) return entry[1].options.map(String);
    } catch (e) { /* missing node */ }
    return [];
  }

  function parseModels(info) {
    return {
      checkpoints: choices(info, "CheckpointLoaderSimple", "ckpt_name"),
      vaes: choices(info, "VAELoader", "vae_name"),
      diffusion_models: choices(info, "UNETLoader", "unet_name"),
      text_encoders: choices(info, "CLIPLoader", "clip_name"),
      loras: choices(info, "LoraLoader", "lora_name"),
      controlnets: choices(info, "ControlNetLoader", "control_net_name"),
      upscalers: choices(info, "UpscaleModelLoader", "model_name"),
      samplers: choices(info, "KSampler", "sampler_name"),
      schedulers: choices(info, "KSampler", "scheduler"),
      nodes: Object.keys(info || {}),
      info: info || {}
    };
  }

  function hasNodes(models) {
    var names = Array.prototype.slice.call(arguments, 1);
    return names.every(function (n) { return models.nodes.indexOf(n) >= 0; });
  }

  /* Fill required widget inputs the graph leaves out with the server's defaults. */
  function fillDefaults(graph, info) {
    var missing = [];
    Object.keys(graph).forEach(function (k) {
      var t = graph[k].class_type;
      if (!info[t] && missing.indexOf(t) < 0) missing.push(t);
    });
    if (missing.length) throw new Error("ComfyUI is missing these nodes: " + missing.sort().join(", "));
    Object.keys(graph).forEach(function (k) {
      var node = graph[k];
      var req = (info[node.class_type].input || {}).required || {};
      Object.keys(req).forEach(function (name) {
        if (name in node.inputs) return;
        var entry = req[name];
        if (!entry) return;
        var kind = entry[0], opts = entry[1] && typeof entry[1] === "object" && !Array.isArray(entry[1]) ? entry[1] : {};
        if (Array.isArray(kind)) { if (kind.length) node.inputs[name] = "default" in opts ? opts["default"] : kind[0]; }
        else if (kind === "COMBO") {
          if ("default" in opts) node.inputs[name] = opts["default"];
          else if (opts.options && opts.options.length) node.inputs[name] = opts.options[0];
        } else if ("default" in opts) node.inputs[name] = opts["default"];
      });
    });
    return graph;
  }

  var CONTROL_MODES = ["reference", "scribble", "lineart", "softedge", "canny", "depth", "normal", "pose", "segmentation", "blur", "image"];
  var PREPROCESSORS = {
    canny: ["Canny", { low_threshold: 0.4, high_threshold: 0.8 }],
    lineart: ["LineArtPreprocessor", { coarse: "disable" }],
    softedge: ["HEDPreprocessor", { safe: "enable" }],
    scribble: ["ScribblePreprocessor", {}],
    depth: ["DepthAnythingV2Preprocessor", {}],
    normal: ["BAE-NormalMapPreprocessor", {}],
    pose: ["DWPreprocessor", {}],
    segmentation: ["OneFormer-ADE20K-SemSegPreprocessor", {}]
  };
  var CONTROL_HINTS = {
    scribble: /scribble|sketch/i, lineart: /lineart|line_art|anyline/i, softedge: /softedge|hed|pidi/i,
    canny: /canny/i, depth: /depth/i, normal: /normal/i, pose: /pose|openpose/i, segmentation: /seg/i,
    blur: /blur|tile/i, image: /union|promax/i
  };
  var ARCH_FILE_HINTS = { sd15: /sd15|sd-?1\.?5|v11|control_v1/i, sdxl: /xl/i, flux: /flux/i };

  function pickControlnet(mode, models, arch) {
    var hint = CONTROL_HINTS[mode] || new RegExp(mode, "i");
    var archHint = ARCH_FILE_HINTS[arch] || new RegExp(arch, "i");
    var named = models.controlnets.filter(function (m) { return hint.test(m); });
    var unions = models.controlnets.filter(function (m) { return CONTROL_HINTS.image.test(m); });
    var pools = [named, unions], i, j;
    for (i = 0; i < pools.length; i++) for (j = 0; j < pools[i].length; j++) if (archHint.test(pools[i][j])) return pools[i][j];
    for (i = 0; i < pools.length; i++) if (pools[i].length) return pools[i][0];
    throw new Error("no ControlNet model for `" + mode + "` is installed; pick one in the control layer");
  }

  function resolveSeed(seed) { return seed >= 0 ? seed : Math.floor(Math.random() * 2147483647); }

  function loadModel(g, style, models) {
    if (style.architecture === "flux2") return loadFlux2(g, style, models);
    var ckpt = CS.styles.resolveCheckpoint(style, models);
    var arch = style.architecture !== "auto" ? style.architecture : (CS.styles.guessArchitecture(ckpt) || "sdxl");
    if (arch === "flux2") throw new Error("Flux 2 has no all-in-one checkpoint: set the style's architecture to flux2");
    var loader = g.add("CheckpointLoaderSimple", { ckpt_name: ckpt });
    var model = loader.out(0), clip = loader.out(1), vae = loader.out(2);
    if (style.vae) vae = g.add("VAELoader", { vae_name: style.vae }).out();
    (style.loras || []).forEach(function (l) {
      if (l.enabled === false) return;
      checkLora(models, l);
      var n = g.add("LoraLoader", { model: model, clip: clip, lora_name: l.name, strength_model: l.strength, strength_clip: l.strength });
      model = n.out(0); clip = n.out(1);
    });
    if (arch === "sd15" && style.clip_skip > 1) clip = g.add("CLIPSetLastLayer", { clip: clip, stop_at_clip_layer: -style.clip_skip }).out();
    return { model: model, clip: clip, vae: vae, arch: arch };
  }

  function checkLora(models, l) {
    if (models.loras.length && models.loras.indexOf(l.name) < 0) throw new Error("LoRA `" + l.name + "` is not installed on the server");
  }

  /* Flux 2: diffusion model + text encoder (CLIPLoader type flux2) + Flux 2 VAE, as in
     ComfyUI's own Flux.2 templates. LoRAs patch the model only. */
  function loadFlux2(g, style, models) {
    var files = CS.styles.resolveFlux2(style, models);
    var model = g.add("UNETLoader", { unet_name: files.unet, weight_dtype: "default" }).out();
    var clip = g.add("CLIPLoader", { clip_name: files.clip, type: "flux2" }).out();
    var vae = g.add("VAELoader", { vae_name: files.vae }).out();
    (style.loras || []).forEach(function (l) {
      if (l.enabled === false) return;
      checkLora(models, l);
      model = g.add("LoraLoaderModelOnly", { model: model, lora_name: l.name, strength_model: l.strength }).out();
    });
    return { model: model, clip: clip, vae: vae, arch: "flux2" };
  }

  function encodePrompts(g, m, style, prompt, negative) {
    var positive = g.add("CLIPTextEncode", { clip: m.clip, text: CS.styles.applyPrompt(style, prompt) }, "Prompt").out();
    var negText = [(style.negative_prompt || "").trim(), (negative || "").trim()].filter(Boolean).join(", ");
    var neg = g.add("CLIPTextEncode", { clip: m.clip, text: negText }, "Negative").out();
    if (m.arch === "flux" || m.arch === "flux2") positive = g.add("FluxGuidance", { conditioning: positive, guidance: style.guidance }).out();
    return [positive, neg];
  }

  /* Flux 2 reads reference images natively: their latents ride on the conditioning. */
  function addReference(g, m, positive, negative, pixels) {
    var latent = g.add("VAEEncode", { pixels: pixels, vae: m.vae }).out();
    return [g.add("ReferenceLatent", { conditioning: positive, latent: latent }).out(),
      g.add("ReferenceLatent", { conditioning: negative, latent: latent }).out()];
  }

  /* One sampling pass. SD/Flux 1 use KSampler; Flux 2 uses the custom-sampler chain of the
     ComfyUI templates (Flux2Scheduler, BasicGuider or CFGGuider when cfg > 1), with the
     schedule cut to `denoise` for refining. */
  function sample(g, m, o) {
    if (m.arch !== "flux2") {
      return g.add("KSampler", {
        model: o.model, positive: o.positive, negative: o.negative, latent_image: o.latent, seed: o.seed,
        steps: o.steps, cfg: o.cfg, sampler_name: o.sampler, scheduler: o.scheduler, denoise: o.denoise
      }).out();
    }
    var sigmas = g.add("Flux2Scheduler", { steps: o.steps, width: o.width, height: o.height }).out();
    if (o.denoise < 1) sigmas = g.add("SplitSigmasDenoise", { sigmas: sigmas, denoise: o.denoise }).out(1);
    var guider = o.cfg > 1
      ? g.add("CFGGuider", { model: o.model, positive: o.positive, negative: o.negative, cfg: o.cfg }).out()
      : g.add("BasicGuider", { model: o.model, conditioning: o.positive }).out();
    return g.add("SamplerCustomAdvanced", {
      noise: g.add("RandomNoise", { noise_seed: o.seed }).out(),
      guider: guider,
      sampler: g.add("KSamplerSelect", { sampler_name: o.sampler }).out(),
      sigmas: sigmas,
      latent_image: o.latent
    }).out(0);
  }

  function applyControls(g, m, models, controls, positive, negative, width, height) {
    var model = m.model;
    (controls || []).forEach(function (c) {
      if (!(c.strength > 0)) return;
      var image = g.add("LoadImage", { image: c.image }, "Control " + c.mode).out();
      image = g.add("ImageScale", { image: image, upscale_method: "lanczos", width: width, height: height, crop: "disabled" }).out();
      if (m.arch === "flux2") {
        if (c.mode !== "reference") throw new Error("Flux 2: ControlNet is not supported yet; use the reference mode (Flux 2 reads reference images natively)");
        var refs = addReference(g, m, positive, negative, image);
        positive = refs[0]; negative = refs[1];
        return;
      }
      if (c.mode === "reference") {
        if (!hasNodes(models, "IPAdapterUnifiedLoader", "IPAdapterAdvanced")) throw new Error("reference images need the ComfyUI_IPAdapter_plus nodes on the server");
        var loader = g.add("IPAdapterUnifiedLoader", { model: model, preset: "PLUS (high strength)" });
        model = g.add("IPAdapterAdvanced", { model: loader.out(0), ipadapter: loader.out(1), image: image, weight: c.strength, start_at: c.start, end_at: c.end }).out();
        return;
      }
      var pre = PREPROCESSORS[c.mode];
      if (c.preprocess !== false && pre && models.nodes.indexOf(pre[0]) >= 0) {
        image = g.add(pre[0], Object.assign({ image: image }, pre[1])).out();
      }
      var name = c.model || pickControlnet(c.mode, models, m.arch);
      var cn = g.add("ControlNetLoader", { control_net_name: name }).out();
      var applied = g.add("ControlNetApplyAdvanced", {
        positive: positive, negative: negative, control_net: cn, image: image,
        strength: c.strength, start_percent: c.start, end_percent: c.end, vae: m.vae
      });
      positive = applied.out(0); negative = applied.out(1);
    });
    return [model, positive, negative];
  }

  /* Text to image, refine or inpaint, depending on image / mask / strength. */
  function buildGenerate(style, models, req) {
    if (req.strength < 1 && !req.image) throw new Error("refining (strength below 100%) needs the canvas image");
    var g = new Graph();
    var m = loadModel(g, style, models);
    var p = encodePrompts(g, m, style, req.prompt, req.negative);
    var c = applyControls(g, m, models, req.controls, p[0], p[1], req.width, req.height);
    var model = c[0], positive = c[1], negative = c[2];
    var batch = Math.max(1, Math.min(16, req.batch || 1));
    var pixels = req.image ? g.add("LoadImage", { image: req.image }, "Canvas").out() : null;
    var live = !!req.live;
    var latent, denoise = Math.max(0.01, Math.min(1, req.strength)), colorMatch = null;
    if (m.arch === "flux2") {
      if (pixels) {
        // As Krita does: the region's latent is both where sampling starts (repainting only under
        // the mask) and the reference image the edit model reads the scene from.
        latent = g.add("VAEEncode", { pixels: pixels, vae: m.vae }).out();
        if (req.mask) {
          var fmask = g.add("LoadImageMask", { image: req.mask, channel: "red" }, "Mask").out();
          latent = g.add("SetLatentNoiseMask", { samples: latent, mask: fmask }).out();
          model = g.add("DifferentialDiffusion", { model: model }).out();
          colorMatch = { reference: pixels, mask: fmask };
        }
        if (req.inpaintLora) model = g.add("LoraLoaderModelOnly", { model: model, lora_name: req.inpaintLora, strength_model: 1 }).out();
        if (style.flux2_reference !== false || req.reference) {
          var refLatent = req.reference ? g.add("VAEEncode", { pixels: g.add("LoadImage", { image: req.reference }, "Reference").out(), vae: m.vae }).out() : latent;
          positive = g.add("ReferenceLatent", { conditioning: positive, latent: refLatent }).out();
          negative = g.add("ReferenceLatent", { conditioning: negative, latent: refLatent }).out();
        }
      } else {
        latent = g.add("EmptyFlux2LatentImage", { width: req.width, height: req.height, batch_size: batch }).out();
        batch = 1;
      }
    } else if (req.mask && pixels) {
      var mask = g.add("LoadImageMask", { image: req.mask, channel: "red" }, "Mask").out();
      var cond = g.add("InpaintModelConditioning", { positive: positive, negative: negative, vae: m.vae, pixels: pixels, mask: mask, noise_mask: true });
      positive = cond.out(0); negative = cond.out(1); latent = cond.out(2);
    } else if (pixels && req.strength < 1) {
      latent = g.add("VAEEncode", { pixels: pixels, vae: m.vae }).out();
    } else {
      latent = g.add(m.arch === "flux" ? "EmptySD3LatentImage" : "EmptyLatentImage", { width: req.width, height: req.height, batch_size: batch }).out();
      batch = 1;
    }
    if (batch > 1) latent = g.add("RepeatLatentBatch", { samples: latent, amount: batch }).out();
    var sampled = sample(g, m, {
      model: model, positive: positive, negative: negative, latent: latent, seed: req.seed,
      steps: live ? style.live_steps : style.steps, cfg: live ? style.live_cfg : style.cfg,
      sampler: live ? style.live_sampler : style.sampler, scheduler: live ? style.live_scheduler : style.scheduler,
      denoise: denoise, width: req.width, height: req.height
    });
    var decoded = g.add("VAEDecode", { samples: sampled, vae: m.vae }).out();
    // Match the repainted area's colours to the surroundings when comfyui-inpaint-nodes is installed.
    if (colorMatch && models.nodes.indexOf("INPAINT_ColorMatch") >= 0) {
      decoded = g.add("INPAINT_ColorMatch", { target: decoded, reference: colorMatch.reference, exclude_mask: colorMatch.mask, strength: 1 }).out();
    }
    g.add("PreviewImage", { images: decoded }, "Result");
    return g;
  }

  function buildUpscale(style, models, req) {
    var g = new Graph();
    var image = g.add("LoadImage", { image: req.image }, "Image").out();
    if (req.model) {
      var um = g.add("UpscaleModelLoader", { model_name: req.model }).out();
      image = g.add("ImageUpscaleWithModel", { upscale_model: um, image: image }).out();
    }
    image = g.add("ImageScale", { image: image, upscale_method: "lanczos", width: req.width, height: req.height, crop: "disabled" }).out();
    if (req.refine) {
      var m = loadModel(g, style, models);
      var p = encodePrompts(g, m, style, req.prompt || "", "");
      var latent = g.add("VAEEncodeTiled", { pixels: image, vae: m.vae, tile_size: 1024, overlap: 64 }).out();
      var sampled = sample(g, m, {
        model: m.model, positive: p[0], negative: p[1], latent: latent, seed: req.seed,
        steps: style.steps, cfg: style.cfg, sampler: style.sampler, scheduler: style.scheduler,
        denoise: Math.max(0.01, Math.min(1, req.strength)), width: req.width, height: req.height
      });
      image = g.add("VAEDecodeTiled", { samples: sampled, vae: m.vae, tile_size: 1024, overlap: 64 }).out();
    }
    g.add("PreviewImage", { images: image }, "Result");
    return g;
  }

  /* ---- custom workflows ---- */
  var PLACEHOLDER = /\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g;
  var WHOLE = /^\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}$/;
  var IMAGE_TITLES = { "photosuite canvas": "canvas", "photosuite mask": "mask", "photosuite selection": "mask" };

  function loadCustomWorkflow(text) {
    var data;
    try { data = JSON.parse(text); } catch (e) { throw new Error("not a JSON file: " + e.message); }
    if (data && typeof data === "object" && "nodes" in data && "links" in data) throw new Error("this is a UI workflow; in ComfyUI use Workflow › Export (API) and load that file");
    var ok = data && typeof data === "object" && !Array.isArray(data) && Object.keys(data).length &&
      Object.keys(data).every(function (k) { return data[k] && typeof data[k] === "object" && "class_type" in data[k]; });
    if (!ok) throw new Error("not a ComfyUI API workflow");
    return data;
  }

  function customPlaceholders(graph) {
    var names = [];
    function add(n) { if (names.indexOf(n) < 0) names.push(n); }
    Object.keys(graph).forEach(function (k) {
      var node = graph[k];
      var title = String((node._meta || {}).title || "").trim().toLowerCase();
      if (IMAGE_TITLES[title]) add(IMAGE_TITLES[title]);
      Object.keys(node.inputs || {}).forEach(function (i) {
        var v = node.inputs[i];
        if (typeof v !== "string") return;
        var mt; PLACEHOLDER.lastIndex = 0;
        while ((mt = PLACEHOLDER.exec(v))) add(mt[1]);
      });
    });
    return names;
  }

  function applyCustom(graph, values) {
    var out = JSON.parse(JSON.stringify(graph));
    Object.keys(out).forEach(function (k) {
      var node = out[k];
      var title = String((node._meta || {}).title || "").trim().toLowerCase();
      var slot = IMAGE_TITLES[title];
      if (slot && slot in values && (node.class_type === "LoadImage" || node.class_type === "LoadImageMask")) node.inputs.image = values[slot];
      Object.keys(node.inputs || {}).forEach(function (i) {
        var v = node.inputs[i];
        if (typeof v !== "string") return;
        var whole = WHOLE.exec(v.trim());
        if (whole) { if (whole[1] in values) node.inputs[i] = values[whole[1]]; return; }
        node.inputs[i] = v.replace(PLACEHOLDER, function (m0, name) { return name in values ? String(values[name]) : m0; });
      });
    });
    return out;
  }

  CS.workflow = {
    Graph: Graph, parseModels: parseModels, fillDefaults: fillDefaults, CONTROL_MODES: CONTROL_MODES,
    PREPROCESSORS: PREPROCESSORS, pickControlnet: pickControlnet, resolveSeed: resolveSeed,
    buildGenerate: buildGenerate, buildUpscale: buildUpscale, loadCustomWorkflow: loadCustomWorkflow,
    customPlaceholders: customPlaceholders, applyCustom: applyCustom
  };
})(typeof window !== "undefined" ? (window.CS = window.CS || {}) : (globalThis.CS = globalThis.CS || {}));
