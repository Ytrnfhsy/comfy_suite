/* The panel: workspaces, job queue, preview and history. */
(function (CS) {
  "use strict";
  var tr = CS.tr, W = CS.workflow;

  /* ---- settings (config.js defaults, kept per session; localStorage when the sandbox allows) ---- */
  var cfg = typeof COMFY_SUITE_CONFIG !== "undefined" ? COMFY_SUITE_CONFIG : {};
  var settings = {
    comfyUrl: cfg.comfyUrl || "http://127.0.0.1:8188", language: cfg.language != null ? cfg.language : "", theme: cfg.theme || "dark", style: cfg.style || "",
    batch: cfg.batch || 2, selectionGrow: cfg.selectionGrow != null ? cfg.selectionGrow : 8,
    selectionFeather: cfg.selectionFeather != null ? cfg.selectionFeather : 12,
    contextPadding: cfg.contextPadding != null ? cfg.contextPadding : 0.25, liveIntervalMs: cfg.liveIntervalMs || 1500
  };
  var storage = null;
  try { storage = window.localStorage; storage.getItem("x"); } catch (e) { storage = null; }
  function loadStored(key) { try { return storage ? JSON.parse(storage.getItem("comfy-suite:" + key) || "null") : null; } catch (e) { return null; } }
  function store(key, value) { try { if (storage) storage.setItem("comfy-suite:" + key, JSON.stringify(value)); } catch (e) { /* sandboxed */ } }
  Object.assign(settings, loadStored("settings") || {});
  CS.setLanguage(settings.language);
  function applyTheme() { document.documentElement.classList.toggle("light", settings.theme === "light"); }
  applyTheme();

  var library = new CS.styles.StyleLibrary((cfg.styles || []).concat(loadStored("styles") || []));
  var comfy = new CS.comfy.ComfyClient(settings.comfyUrl);
  var generator = new CS.Generator(comfy, settings);
  var connected = false, connectError = "";

  /* ---- tiny DOM helper ---- */
  function h(tag, attrs, children) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, "");
      else if (v !== false && v != null) el.setAttribute(k, v);
    });
    (children || []).forEach(function (c) { if (c != null) el.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return el;
  }
  function $(id) { return document.getElementById(id); }
  function options(select, items, current) {
    select.innerHTML = "";
    items.forEach(function (it) {
      var value = Array.isArray(it) ? it[0] : it, label = Array.isArray(it) ? it[1] : it;
      select.appendChild(h("option", { value: value, text: label, selected: value === current }));
    });
    if (current != null && items.every(function (it) { return (Array.isArray(it) ? it[0] : it) !== current; }) && current !== "") {
      select.appendChild(h("option", { value: current, text: current, selected: true }));
    }
  }
  function slider(value, min) {
    var input = h("input", { type: "range", min: min == null ? 1 : min, max: 100, value: Math.round(value * 100) });
    var label = h("span", { class: "value", text: input.value + "%" });
    input.addEventListener("input", function () { label.textContent = input.value + "%"; });
    var wrap = h("div", { class: "slider" }, [input, label]);
    wrap.value = function () { return input.value / 100; };
    wrap.set = function (v) { input.value = Math.round(v * 100); label.textContent = input.value + "%"; };
    wrap.input = input;
    return wrap;
  }
  function row(label, control) { return h("label", { class: "row" }, [h("span", { text: label }), control]); }

  /* ---- message line ---- */
  function message(text, isError) {
    var el = $("message");
    el.textContent = text || "";
    el.className = isError ? "message error" : "message";
  }

  /* ---- job queue ---- */
  var jobs = [], current = null, nextId = 1;
  function submit(title, work, onDone) {
    var job = { id: nextId++, title: title, work: work, onDone: onDone, cancelled: false, progress: 0, status: tr("Queued") };
    jobs.push(job);
    updateProgress();
    pump();
    return job;
  }
  function pump() {
    if (current || !jobs.length) return;
    current = jobs.shift();
    var job = current;
    job.status = tr("Running");
    job.onProgress = function (v, text) { job.progress = v; job.status = tr(text); updateProgress(); };
    updateProgress();
    Promise.resolve().then(function () { return job.work(job); }).then(function (result) {
      if (!job.cancelled) { message(""); if (job.onDone) job.onDone(result, job); }
    }, function (e) {
      if (!(e && e.cancelled) && !job.cancelled) { console.error(e); message(e && e.message ? e.message : String(e), true); }
    }).then(function () {
      current = null;
      updateProgress();
      pump();
    });
  }
  function cancelCurrent() {
    if (current) current.cancelled = true;
    jobs.forEach(function (j) { j.cancelled = true; });
    jobs = [];
    updateProgress();
  }
  function updateProgress() {
    var bar = $("progress-bar"), text = $("progress-text");
    if (!current) { bar.style.width = "0"; text.textContent = jobs.length ? tr("Queued") + " (" + jobs.length + ")" : ""; $("cancel").disabled = !jobs.length; return; }
    bar.style.width = Math.round(current.progress * 100) + "%";
    text.textContent = current.status + (jobs.length ? "  (+" + jobs.length + ")" : "");
    $("cancel").disabled = false;
  }

  /* ---- history ---- */
  var history = [], selected = null;
  function addHistory(result, kind) {
    result.images.forEach(function (_, i) { history.unshift({ result: result, index: i, kind: kind }); });
    if (history.length > 60) history.length = 60;
    renderHistory();
    select(history[0]);
  }
  function thumb(item) {
    var c = item.result.mask ? generator.layerImage(item.result, item.index) : item.result.images[item.index];
    return CS.imaging.resize(c, Math.max(1, Math.round(c.width * 96 / Math.max(c.width, c.height))), Math.max(1, Math.round(c.height * 96 / Math.max(c.width, c.height))));
  }
  function renderHistory() {
    var grid = $("history");
    grid.innerHTML = "";
    history.forEach(function (item) {
      if (!item.thumb) item.thumb = thumb(item).toDataURL();
      var img = h("img", { src: item.thumb, title: (item.result.prompt || "") + "\nseed " + item.result.seed, class: item === selected ? "selected" : "" });
      img.addEventListener("click", function () { select(item); });
      img.addEventListener("dblclick", function () { select(item); apply(item); });
      grid.appendChild(img);
    });
    $("history-actions").style.display = history.length ? "" : "none";
  }
  function select(item) {
    selected = item;
    var prev = $("preview");
    if (!item) { prev.removeAttribute("src"); prev.style.display = "none"; }
    else {
      var c = item.result.mask ? generator.layerImage(item.result, item.index) : item.result.images[item.index];
      prev.src = CS.imaging.resize(c, Math.min(c.width, 640), Math.round(c.height * Math.min(c.width, 640) / c.width)).toDataURL();
      prev.style.display = "";
    }
    Array.prototype.forEach.call($("history").children, function (img, i) { img.className = history[i] === item ? "selected" : ""; });
  }
  function apply(item) {
    if (!item) return;
    generator.apply(item.result, item.index).then(function () {
      message(tr(item.result.newDocument ? "Upscaled image opened as a new document." : "Result added as a layer. Press Enter to confirm its placement."));
    }, function (e) { message(e.message, true); });
  }

  /* ---- connection ---- */
  function connect() {
    comfy = new CS.comfy.ComfyClient(settings.comfyUrl);
    generator = new CS.Generator(comfy, settings);
    connected = false;
    setStatus(tr("Connecting…"), "busy");
    return comfy.systemStats().then(function () { return comfy.loadModels(); }).then(function (models) {
      connected = true; connectError = "";
      setStatus(tr("Connected"), "ok");
      message("");
      refreshModels(models);
    }, function (e) {
      connectError = e.message;
      setStatus(tr("Not connected"), "error");
      message(e.message + "\n" + tr("Start ComfyUI with --enable-cors-header so the panel can reach it."), true);
    });
  }
  function setStatus(text, state) {
    var s = $("status");
    s.title = text + (connectError ? "\n" + connectError : "");
    s.className = "status " + state;
  }
  function ready() {
    if (!connected) { message(tr("Not connected") + (connectError ? ": " + connectError : ""), true); return false; }
    return true;
  }

  /* ---- style selector ---- */
  function currentStyle() { return library.get($("style").value); }
  function renderStyles(selectName) {
    options($("style"), library.names(), selectName || settings.style || library.names()[0]);
  }

  /* ================= workspaces ================= */

  /* -- Generate -- */
  var gen = {};
  function buildGenerate() {
    gen.prompt = h("textarea", { rows: 4, placeholder: tr("Describe the image or the content to fill…") });
    gen.negative = h("textarea", { rows: 2, placeholder: tr("What to avoid…"), class: "hidden" });
    var negToggle = h("button", { class: "small", title: tr("Negative prompt"), text: "−", onclick: function () { gen.negative.classList.toggle("hidden"); } });
    gen.controls = h("div", { class: "controls" });
    gen.strength = slider(1);
    gen.strength.input.addEventListener("input", updateGenerateButton);
    gen.batch = h("input", { type: "number", min: 1, max: 16, value: settings.batch, class: "narrow" });
    gen.useSel = h("input", { type: "checkbox", checked: true });
    gen.fixed = h("input", { type: "checkbox" });
    gen.seed = h("input", { type: "number", min: 0, value: Math.floor(Math.random() * 1e9), disabled: true });
    gen.fixed.addEventListener("change", function () { gen.seed.disabled = !gen.fixed.checked; });
    gen.button = h("button", { class: "primary", text: tr("Generate"), onclick: generate });
    [gen.prompt, gen.negative].forEach(function (t) {
      t.addEventListener("keydown", function (e) { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); generate(); } });
    });
    return h("section", { id: "ws-generate" }, [
      h("div", { class: "prompt-row" }, [gen.prompt, negToggle]), gen.negative,
      gen.controls,
      h("button", { class: "link", text: "+ " + tr("Add control"), onclick: function () { addControl(); } }),
      row(tr("Strength"), gen.strength),
      h("div", { class: "inline" }, [h("span", { text: tr("Batch") }), gen.batch, h("label", {}, [gen.useSel, " " + tr("Use selection")])]),
      h("div", { class: "inline" }, [h("span", { text: tr("Seed") }), h("label", {}, [gen.fixed, " " + tr("Fixed")]), gen.seed]),
      gen.button
    ]);
  }
  function updateGenerateButton() {
    gen.button.textContent = gen.strength.value() < 1 ? tr("Refine") : tr("Generate");
  }
  var controlRows = [];
  function addControl() {
    var mode = h("select"); options(mode, W.CONTROL_MODES.map(function (m) { return [m, m.charAt(0).toUpperCase() + m.slice(1)]; }), "scribble");
    var source = h("select"); options(source, [["canvas", tr("Canvas")], ["file", tr("Image file…")]], "canvas");
    var file = h("input", { type: "file", accept: "image/*", class: "hidden" });
    var model = h("select"); options(model, [["", tr("Auto")]].concat((comfy.models ? comfy.models.controlnets : []).map(function (m) { return [m, m]; })), "");
    var pre = h("input", { type: "checkbox", checked: true });
    var strength = slider(1, 0);
    var start = h("input", { type: "number", min: 0, max: 1, step: 0.05, value: 0, class: "narrow" });
    var end = h("input", { type: "number", min: 0, max: 1, step: 0.05, value: 1, class: "narrow" });
    var ctl = { mode: mode, source: source, model: model, pre: pre, strength: strength, start: start, end: end, image: null };
    source.addEventListener("change", function () { if (source.value === "file") file.click(); });
    file.addEventListener("change", function () {
      if (!file.files[0]) return;
      CS.imaging.decode(file.files[0]).then(function (c) { ctl.image = c; source.options[1].textContent = file.files[0].name; });
    });
    var el = h("div", { class: "control" }, [
      h("div", { class: "inline" }, [mode, source, h("button", { class: "small", text: "✕", title: tr("Remove"), onclick: function () {
        controlRows = controlRows.filter(function (c) { return c !== ctl; }); el.remove();
      } })]),
      h("div", { class: "inline" }, [model, h("label", { title: tr("Preprocess") }, [pre, " " + tr("Preprocess")])]),
      h("div", { class: "inline" }, [strength, start, "–", end]), file
    ]);
    ctl.el = el;
    controlRows.push(ctl);
    gen.controls.appendChild(el);
  }
  function generate() {
    if (!ready()) return;
    var style = currentStyle();
    var params = {
      prompt: gen.prompt.value.trim(), negative: gen.negative.value.trim(), strength: gen.strength.value(),
      seed: gen.fixed.checked ? +gen.seed.value : -1, batch: Math.max(1, Math.min(16, +gen.batch.value || 1)),
      useSelection: gen.useSel.checked,
      controls: controlRows.map(function (c) {
        return { mode: c.mode.value, source: c.source.value, image: c.image, model: c.model.value, preprocess: c.pre.checked,
          strength: c.strength.value(), start: +c.start.value, end: +c.end.value };
      })
    };
    submit(params.prompt || gen.button.textContent, function (job) { return generator.generate(style, params, job, false); }, function (r) { addHistory(r, "generate"); });
  }

  /* -- Upscale -- */
  var up = {};
  function buildUpscale() {
    up.factor = h("select"); options(up.factor, [["1.5", "1.5×"], ["2", "2×"], ["3", "3×"], ["4", "4×"]], "2");
    up.model = h("select");
    up.refine = h("input", { type: "checkbox" });
    up.strength = slider(0.3);
    up.prompt = h("textarea", { rows: 2, placeholder: tr("Prompt") });
    return h("section", { id: "ws-upscale", class: "hidden" }, [
      row(tr("Factor"), up.factor), row(tr("Upscale model"), up.model),
      h("label", { class: "inline" }, [up.refine, " " + tr("Refine after upscaling")]),
      row(tr("Strength"), up.strength), up.prompt,
      h("button", { class: "primary", text: tr("Upscale"), onclick: function () {
        if (!ready()) return;
        var style = currentStyle(), opts = { factor: +up.factor.value, model: up.model.value, refine: up.refine.checked, strength: up.strength.value(), prompt: up.prompt.value.trim() };
        submit(tr("Upscale") + " " + opts.factor + "×", function (job) { return generator.upscale(style, opts, job); }, function (r) {
          addHistory(r, "upscale");
          apply(history[0]);
        });
      } })
    ]);
  }

  /* -- Live -- */
  var live = { running: false, timer: null, lastKey: null, busy: false, last: null };
  function buildLive() {
    live.prompt = h("textarea", { rows: 3, placeholder: tr("Prompt") });
    live.strength = slider(0.55);
    live.seed = Math.floor(Math.random() * 1e9);
    live.button = h("button", { class: "primary", text: "▶ " + tr("Start"), onclick: toggleLive });
    live.preview = h("img", { class: "live-preview" });
    live.apply = h("button", { text: tr("Apply"), disabled: true, onclick: function () { if (live.last) apply({ result: live.last, index: 0 }); } });
    live.status = h("div", { class: "hint", text: tr("Live painting regenerates the canvas while you paint.") });
    return h("section", { id: "ws-live", class: "hidden" }, [live.prompt, row(tr("Strength"), live.strength), live.button, live.preview, live.apply, live.status]);
  }
  function toggleLive() {
    if (!live.running && !ready()) return;
    live.running = !live.running;
    live.button.textContent = live.running ? "■ " + tr("Stop") : "▶ " + tr("Start");
    clearInterval(live.timer);
    if (live.running) { live.lastKey = null; live.timer = setInterval(liveTick, Math.max(300, settings.liveIntervalMs)); liveTick(); }
  }
  function hashCanvas(c) {
    var d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data, hsh = 2166136261;
    for (var i = 0; i < d.length; i += 97) hsh = Math.imul(hsh ^ d[i], 16777619);
    return c.width + "x" + c.height + ":" + (hsh >>> 0);
  }
  function liveTick() {
    if (!live.running || live.busy) return;
    live.busy = true;
    CS.host.composite().then(function (doc) {
      var key = [hashCanvas(doc.canvas), live.prompt.value, live.strength.value(), $("style").value].join("|");
      if (key === live.lastKey) return null;
      live.lastKey = key;
      var params = { prompt: live.prompt.value.trim(), negative: "", strength: live.strength.value(), seed: live.seed, batch: 1, controls: [] };
      var job = { onProgress: function (v, t) { live.status.textContent = tr(t); } };
      return generator.generate(currentStyle(), params, job, true).then(function (r) {
        live.last = r;
        var c = generator.layerImage(r, 0);
        live.preview.src = c.toDataURL();
        live.apply.disabled = false;
        live.status.textContent = "seed " + r.seed;
      });
    }).catch(function (e) {
      live.status.textContent = e.message;
      if (live.running) toggleLive();
    }).then(function () { live.busy = false; });
  }

  /* -- Custom graph -- */
  var custom = { workflow: null, fields: {} };
  function buildCustom() {
    custom.file = h("input", { type: "file", accept: ".json,application/json" });
    custom.text = h("textarea", { rows: 3, placeholder: tr("Or paste the API JSON here") });
    custom.form = h("div", { class: "custom-fields" });
    custom.button = h("button", { class: "primary", text: tr("Run"), disabled: true, onclick: runCustom });
    custom.file.addEventListener("change", function () {
      var f = custom.file.files[0];
      if (f) f.text().then(loadCustom);
    });
    custom.text.addEventListener("change", function () { if (custom.text.value.trim()) loadCustom(custom.text.value); });
    return h("section", { id: "ws-custom", class: "hidden" }, [row(tr("Load workflow…"), custom.file), custom.text, custom.form, custom.button]);
  }
  function loadCustom(text) {
    try { custom.workflow = W.loadCustomWorkflow(text); } catch (e) { message(e.message, true); return; }
    custom.form.innerHTML = ""; custom.fields = {};
    var auto = [];
    W.customPlaceholders(custom.workflow).forEach(function (name) {
      if (["canvas", "mask", "width", "height"].indexOf(name) >= 0) { auto.push(name); return; }
      var input = name === "prompt" || name === "negative" ? h("textarea", { rows: name === "prompt" ? 3 : 2 })
        : name === "strength" || name === "denoise" ? slider(1) : h("input", { type: name === "seed" ? "number" : "text", value: name === "seed" ? -1 : "" });
      custom.fields[name] = input;
      custom.form.appendChild(row(name, input));
    });
    if (auto.length) custom.form.appendChild(h("div", { class: "hint", text: "PhotoSuite: " + auto.join(", ") }));
    custom.button.disabled = false;
    message("");
  }
  function runCustom() {
    if (!custom.workflow || !ready()) return;
    var values = {};
    Object.keys(custom.fields).forEach(function (k) {
      var f = custom.fields[k];
      if (f.value && typeof f.value === "function") values[k] = f.value();
      else { var v = f.value; try { values[k] = JSON.parse(v); } catch (e) { values[k] = v; } }
    });
    var wf = custom.workflow;
    submit(String(values.prompt || "custom"), function (job) { return generator.custom(wf, values, job); }, function (r) { addHistory(r, "custom"); });
  }

  /* -- Settings -- */
  var set = {};
  function buildSettings() {
    set.url = h("input", { type: "text", value: settings.comfyUrl });
    set.grow = h("input", { type: "number", min: 0, max: 200, value: settings.selectionGrow, class: "narrow" });
    set.feather = h("input", { type: "number", min: 0, max: 200, value: settings.selectionFeather, class: "narrow" });
    set.padding = h("input", { type: "number", min: 0, max: 2, step: 0.05, value: settings.contextPadding, class: "narrow" });
    set.interval = h("input", { type: "number", min: 300, max: 10000, step: 100, value: settings.liveIntervalMs, class: "narrow" });
    set.lang = h("select"); options(set.lang, [["", "System"], ["uk", "Українська"], ["en", "English"]], settings.language);
    set.theme = h("select"); options(set.theme, [["dark", tr("Dark")], ["light", tr("Light")]], settings.theme);
    return h("section", { id: "ws-settings", class: "hidden" }, [
      row(tr("ComfyUI server"), set.url),
      row(tr("Selection grow (px)"), set.grow), row(tr("Selection feather (px)"), set.feather),
      row(tr("Context around selection"), set.padding), row(tr("Live interval (ms)"), set.interval), row(tr("Language"), set.lang), row(tr("Theme"), set.theme),
      h("button", { class: "primary", text: tr("Save") + " & " + tr("Connect"), onclick: function () {
        settings.comfyUrl = set.url.value.trim() || settings.comfyUrl;
        settings.selectionGrow = +set.grow.value; settings.selectionFeather = +set.feather.value;
        settings.contextPadding = +set.padding.value; settings.liveIntervalMs = +set.interval.value;
        var langChanged = settings.language !== set.lang.value;
        settings.language = set.lang.value;
        settings.theme = set.theme.value; applyTheme();
        store("settings", settings);
        if (langChanged) location.reload();
        connect();
      } }),
      h("div", { class: "hint", text: tr("Settings are kept for this session; set defaults in config.js.") }),
      h("div", { class: "hint", text: tr("Start ComfyUI with --enable-cors-header so the panel can reach it.") })
    ]);
  }

  /* -- Style editor -- */
  function buildStyleEditor() {
    var s = currentStyle(), m = comfy.models || { checkpoints: [], vaes: [], loras: [], samplers: [], schedulers: [], diffusion_models: [], text_encoders: [] };
    var samplers = m.samplers.length ? m.samplers : ["euler", "euler_ancestral", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_sde", "ddim"];
    var schedulers = m.schedulers.length ? m.schedulers : ["normal", "karras", "exponential", "sgm_uniform", "simple", "beta"];
    function sel(items, v) { var e = h("select"); options(e, items, v); return e; }
    function num(v, step) { return h("input", { type: "number", value: v, step: step || 1, class: "narrow" }); }
    var f = {
      name: h("input", { type: "text", value: s.name }),
      architecture: sel(CS.styles.ARCHITECTURES, s.architecture),
      checkpoint: sel([["", tr("Auto")]].concat(m.checkpoints), s.checkpoint),
      vae: sel([["", tr("From checkpoint")]].concat(m.vaes), s.vae),
      diffusion_model: sel([["", tr("Auto")]].concat(m.diffusion_models || []), s.diffusion_model),
      text_encoder: sel([["", tr("Auto")]].concat(m.text_encoders || []), s.text_encoder),
      flux2_reference: h("input", { type: "checkbox", checked: s.flux2_reference !== false }),
      style_prompt: h("textarea", { rows: 2 }), negative_prompt: h("textarea", { rows: 2 }),
      sampler: sel(samplers, s.sampler), scheduler: sel(schedulers, s.scheduler),
      steps: num(s.steps), cfg: num(s.cfg, 0.5), guidance: num(s.guidance, 0.5), clip_skip: num(s.clip_skip),
      native_resolution: num(s.native_resolution, 64),
      live_sampler: sel(samplers, s.live_sampler), live_scheduler: sel(schedulers, s.live_scheduler),
      live_steps: num(s.live_steps), live_cfg: num(s.live_cfg, 0.5)
    };
    f.style_prompt.value = s.style_prompt; f.negative_prompt.value = s.negative_prompt;
    var loras = h("div", { class: "loras" });
    function addLora(l) {
      var name = sel(m.loras.length ? m.loras : [l.name], l.name), strength = num(l.strength, 0.05), on = h("input", { type: "checkbox", checked: l.enabled !== false });
      var r = h("div", { class: "inline lora" }, [on, name, strength, h("button", { class: "small", text: "✕", onclick: function () { r.remove(); } })]);
      r.read = function () { return { name: name.value, strength: +strength.value, enabled: on.checked }; };
      loras.appendChild(r);
    }
    s.loras.forEach(addLora);
    var exportBox = h("textarea", { rows: 3, readonly: true, class: "hidden" });
    function collect() {
      var out = {};
      Object.keys(f).forEach(function (k) { out[k] = f[k].type === "number" ? +f[k].value : f[k].type === "checkbox" ? f[k].checked : f[k].value; });
      out.loras = Array.prototype.map.call(loras.children, function (r) { return r.read(); }).filter(function (l) { return l.name; });
      return out;
    }
    function save(asNew) {
      var st = collect();
      if (asNew && library.names().indexOf(st.name) >= 0) st.name += " 2";
      if (!asNew && st.name !== s.name) library.remove(s.name);
      library.put(st);
      store("styles", library.custom());
      renderStyles(st.name);
      exportBox.value = JSON.stringify(library.custom(), null, 1);
      exportBox.classList.remove("hidden");
      message(tr("Styles are kept for this session. To keep them, copy this into config.js (styles):"));
    }
    var editor = $("style-editor");
    editor.innerHTML = "";
    [row(tr("Name"), f.name), row(tr("Architecture"), f.architecture), row(tr("Checkpoint"), f.checkpoint), row("VAE", f.vae),
      h("div", { class: "hint", text: tr("Flux 2: separate model files (the checkpoint is not used)") }),
      row(tr("Diffusion model"), f.diffusion_model), row(tr("Text encoder"), f.text_encoder),
      h("label", { class: "inline" }, [f.flux2_reference, " " + tr("Use the canvas as a reference when refining")]),
      row(tr("Style prompt"), f.style_prompt), row(tr("Negative prompt"), f.negative_prompt),
      row(tr("Sampler"), f.sampler), row(tr("Scheduler"), f.scheduler), row(tr("Steps"), f.steps), row("CFG", f.cfg),
      row("Guidance (Flux)", f.guidance), row("CLIP skip", f.clip_skip), row(tr("Native resolution"), f.native_resolution),
      h("div", { class: "hint", text: tr("Live sampling") }), row(tr("Sampler"), f.live_sampler), row(tr("Scheduler"), f.live_scheduler),
      row(tr("Steps"), f.live_steps), row("CFG", f.live_cfg),
      h("div", { class: "hint", text: "LoRA" }), loras,
      h("button", { class: "link", text: "+ " + tr("Add LoRA"), onclick: function () { addLora({ name: m.loras[0] || "", strength: 1 }); } }),
      h("div", { class: "inline" }, [
        h("button", { class: "primary", text: tr("Save"), onclick: function () { save(false); } }),
        h("button", { text: tr("Save as new"), onclick: function () { save(true); } }),
        h("button", { text: tr("Delete"), onclick: function () { library.remove(s.name); store("styles", library.custom()); renderStyles(); editor.classList.add("hidden"); } }),
        h("button", { text: tr("Close"), onclick: function () { editor.classList.add("hidden"); } })
      ]), exportBox
    ].forEach(function (e) { editor.appendChild(e); });
    editor.classList.remove("hidden");
  }

  function refreshModels(models) {
    options(up.model, [["", tr("None (Lanczos)")]].concat(models.upscalers), models.upscalers[0] || "");
    controlRows.forEach(function (c) {
      var v = c.model.value;
      options(c.model, [["", tr("Auto")]].concat(models.controlnets.map(function (x) { return [x, x]; })), v);
    });
  }

  /* ---- layout ---- */
  var TABS = [["generate", tr("Generate")], ["upscale", tr("Upscale")], ["live", tr("Live")], ["custom", tr("Custom")], ["settings", "⚙"]];
  function showTab(id) {
    TABS.forEach(function (t) {
      $("ws-" + t[0]).classList.toggle("hidden", t[0] !== id);
      $("tab-" + t[0]).classList.toggle("active", t[0] === id);
    });
    $("results").classList.toggle("hidden", id === "live" || id === "settings");
    if (id !== "live" && live.running) toggleLive();
  }

  function init() {
    var root = $("app");
    root.appendChild(h("header", {}, [
      h("nav", {}, TABS.map(function (t) { return h("button", { id: "tab-" + t[0], text: t[1], onclick: function () { showTab(t[0]); } }); })),
      h("span", { id: "status", class: "status busy", title: tr("Connecting…"), onclick: connect })
    ]));
    var styleSel = h("select", { id: "style", onchange: function () { settings.style = styleSel.value; store("settings", settings); } });
    root.appendChild(h("div", { class: "inline style-row" }, [h("span", { text: tr("Style") }), styleSel,
      h("button", { class: "small", text: "✎", title: tr("Edit style"), onclick: buildStyleEditor })]));
    root.appendChild(h("div", { id: "style-editor", class: "editor hidden" }));
    root.appendChild(buildGenerate());
    root.appendChild(buildUpscale());
    root.appendChild(buildLive());
    root.appendChild(buildCustom());
    root.appendChild(buildSettings());
    root.appendChild(h("div", { class: "progress" }, [
      h("div", { class: "bar" }, [h("div", { id: "progress-bar" }), h("span", { id: "progress-text" })]),
      h("button", { id: "cancel", class: "small", text: "■", title: tr("Cancel"), disabled: true, onclick: cancelCurrent })
    ]));
    root.appendChild(h("div", { id: "message", class: "message" }));
    root.appendChild(h("div", { id: "results" }, [
      h("img", { id: "preview", class: "preview", style: "display:none" }),
      h("div", { class: "inline", id: "history-actions", style: "display:none" }, [
        h("span", { class: "grow", text: tr("History") }),
        h("button", { text: tr("Apply"), onclick: function () { apply(selected); } }),
        h("button", { class: "small", text: "⋯", title: tr("Reuse seed") + " / " + tr("Reuse prompt"), onclick: function () {
          if (!selected) return;
          gen.prompt.value = selected.result.prompt || gen.prompt.value;
          gen.fixed.checked = true; gen.seed.disabled = false; gen.seed.value = selected.result.seed;
        } }),
        h("button", { class: "small", text: "🗑", title: tr("Discard"), onclick: function () {
          history = history.filter(function (x) { return x !== selected; }); select(history[0] || null); renderHistory();
        } })
      ]),
      h("div", { id: "history", class: "history" })
    ]));
    renderStyles();
    showTab("generate");
    connect();
  }

  CS.app = { init: init, settings: settings, library: library, submit: submit, history: function () { return history; }, apply: apply };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})(window.CS = window.CS || {});
