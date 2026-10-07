/* Style presets: which model to use and how to sample it. */
(function (CS) {
  "use strict";

  var BUILTIN = [
    {
      name: "Cinematic Photo (XL)", architecture: "sdxl", checkpoint: "", vae: "", loras: [],
      style_prompt: "cinematic photo, {prompt}, highly detailed, sharp focus, natural light",
      negative_prompt: "lowres, blurry, deformed, watermark, text, signature, cartoon, painting",
      sampler: "dpmpp_2m", scheduler: "karras", steps: 24, cfg: 6.0, guidance: 3.5, clip_skip: 1,
      native_resolution: 1024, live_sampler: "euler_ancestral", live_scheduler: "sgm_uniform", live_steps: 8, live_cfg: 2.0
    },
    {
      name: "Digital Artwork (XL)", architecture: "sdxl", checkpoint: "", vae: "", loras: [],
      style_prompt: "digital artwork, {prompt}, concept art, intricate details, vibrant colors",
      negative_prompt: "lowres, blurry, jpeg artifacts, watermark, text, signature, photo",
      sampler: "dpmpp_2m_sde", scheduler: "karras", steps: 24, cfg: 6.5, guidance: 3.5, clip_skip: 1,
      native_resolution: 1024, live_sampler: "euler_ancestral", live_scheduler: "sgm_uniform", live_steps: 8, live_cfg: 2.0
    },
    {
      name: "Universal (SD 1.5)", architecture: "sd15", checkpoint: "", vae: "", loras: [],
      style_prompt: "{prompt}, best quality, highly detailed",
      negative_prompt: "lowres, bad anatomy, bad hands, blurry, watermark, text",
      sampler: "dpmpp_2m", scheduler: "karras", steps: 20, cfg: 7.0, guidance: 3.5, clip_skip: 1,
      native_resolution: 512, live_sampler: "euler_ancestral", live_scheduler: "normal", live_steps: 8, live_cfg: 2.5
    },
    {
      name: "Flux", architecture: "flux", checkpoint: "", vae: "", loras: [],
      style_prompt: "{prompt}", negative_prompt: "",
      sampler: "euler", scheduler: "simple", steps: 20, cfg: 1.0, guidance: 3.5, clip_skip: 1,
      native_resolution: 1024, live_sampler: "euler", live_scheduler: "simple", live_steps: 6, live_cfg: 1.0
    }
  ];

  var DEFAULTS = {
    name: "New Style", architecture: "auto", checkpoint: "", vae: "", loras: [],
    style_prompt: "{prompt}", negative_prompt: "", sampler: "euler", scheduler: "normal",
    steps: 20, cfg: 7.0, guidance: 3.5, clip_skip: 1, native_resolution: 1024,
    live_sampler: "euler_ancestral", live_scheduler: "normal", live_steps: 8, live_cfg: 2.0
  };

  var ARCH_HINTS = {
    flux: /flux/i,
    sdxl: /xl|pony|illustrious|noob|juggernaut|realvis/i,
    sd15: /sd-?1\.?5|v1-5|sd15|dreamshaper_8|realistic.?vision|deliberate/i
  };

  function normalize(style) {
    var s = {};
    Object.keys(DEFAULTS).forEach(function (k) { s[k] = style[k] != null ? style[k] : DEFAULTS[k]; });
    s.loras = (style.loras || []).filter(function (l) { return l && l.name; }).map(function (l) {
      return { name: l.name, strength: l.strength != null ? +l.strength : 1, enabled: l.enabled !== false };
    });
    return s;
  }

  function guessArchitecture(filename) {
    var order = ["flux", "sdxl", "sd15"];
    for (var i = 0; i < order.length; i++) if (ARCH_HINTS[order[i]].test(filename || "")) return order[i];
    return null;
  }

  function applyPrompt(style, prompt) {
    prompt = (prompt || "").trim();
    var template = style.style_prompt || "{prompt}";
    if (template.indexOf("{prompt}") < 0) return [template.trim(), prompt].filter(Boolean).join(", ");
    return template.split("{prompt}").join(prompt).replace(/(,\s*){2,}/g, ", ").replace(/^[\s,]+|[\s,]+$/g, "");
  }

  function resolveCheckpoint(style, models) {
    var available = models.checkpoints || [];
    if (style.checkpoint) {
      if (!available.length || available.indexOf(style.checkpoint) >= 0) return style.checkpoint;
      throw new Error("checkpoint `" + style.checkpoint + "` is not installed on the server");
    }
    if (!available.length) throw new Error("the server has no checkpoints installed");
    if (style.architecture && style.architecture !== "auto") {
      for (var i = 0; i < available.length; i++) if (guessArchitecture(available[i]) === style.architecture) return available[i];
    }
    return available[0];
  }

  function StyleLibrary(extra) {
    this.styles = [];
    var seen = {};
    var self = this;
    BUILTIN.concat(extra || []).forEach(function (s) {
      var n = normalize(s);
      if (seen[n.name] != null) self.styles[seen[n.name]] = n;
      else { seen[n.name] = self.styles.length; self.styles.push(n); }
    });
  }
  StyleLibrary.prototype.names = function () { return this.styles.map(function (s) { return s.name; }); };
  StyleLibrary.prototype.get = function (name) {
    for (var i = 0; i < this.styles.length; i++) if (this.styles[i].name === name) return this.styles[i];
    return this.styles[0];
  };
  StyleLibrary.prototype.put = function (style) {
    var n = normalize(style);
    for (var i = 0; i < this.styles.length; i++) if (this.styles[i].name === n.name) { this.styles[i] = n; return n; }
    this.styles.push(n);
    return n;
  };
  StyleLibrary.prototype.remove = function (name) {
    this.styles = this.styles.filter(function (s) { return s.name !== name; });
    if (!this.styles.length) this.styles.push(normalize(BUILTIN[0]));
  };
  StyleLibrary.prototype.custom = function () {
    var builtin = {};
    BUILTIN.forEach(function (b) { builtin[b.name] = JSON.stringify(normalize(b)); });
    return this.styles.filter(function (s) { return builtin[s.name] !== JSON.stringify(s); });
  };

  CS.styles = {
    BUILTIN: BUILTIN, ARCHITECTURES: ["auto", "sd15", "sdxl", "flux"], normalize: normalize,
    guessArchitecture: guessArchitecture, applyPrompt: applyPrompt, resolveCheckpoint: resolveCheckpoint,
    StyleLibrary: StyleLibrary
  };
})(typeof window !== "undefined" ? (window.CS = window.CS || {}) : (globalThis.CS = globalThis.CS || {}));
