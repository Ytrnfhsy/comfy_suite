/* Inpaint modes, after Krita's AI plugin: what to do with a selection at full strength.

   auto        expand when the selection covers empty (transparent) canvas, else fill
   fill        generate new content that blends in (area pre-filled with a blur of its surroundings)
   expand      outpaint an enlarged canvas (area pre-filled by extending the borders)
   add         add an object described by the prompt (area pre-filled neutral grey)
   remove      remove what is selected (area pre-filled from its surroundings; empty prompt
               becomes "background scenery")
   background  replace the background: select the subject, everything else is repainted
   custom      no pre-fill; context, grow and feather as set in the panel

   Below full strength a selection is refined (img2img inside it), whatever the mode. */
(function (CS) {
  "use strict";

  var MODES = ["auto", "fill", "expand", "add", "remove", "background", "custom"];

  // Flux 2 is an edit model: these instructions, with the region as a reference image, steer it.
  var FLUX2_INSTRUCTIONS = {
    fill: "Fill the green spaces according to the image.",
    expand: "Expand the image to fill the green spaces.",
    add: "Add the object to the scene.",
    remove: "Remove the object.",
    background: "Replace the background while keeping the main subject."
  };

  function resolveMode(mode, transparentShare) {
    if (mode && mode !== "auto") return mode;
    return transparentShare > 0.25 ? "expand" : "fill";
  }

  /* How to prepare a request for `mode` (already resolved) at `strength` with architecture `arch`.
     Returns { mode, fill: "blur"|"border"|"neutral"|"none", referenceFill: "green"|null,
               invert, featherScale, context: "auto"|"mask"|"image", instruction, defaultPrompt } */
  function plan(mode, arch, strength, opts) {
    opts = opts || {};
    var flux2 = arch === "flux2";
    var p = { mode: mode, fill: "none", referenceFill: null, invert: false, featherScale: 1, context: "auto", instruction: "", defaultPrompt: "" };
    if (strength < 1) { p.mode = "refine"; return p; }
    switch (mode) {
      case "fill": p.fill = "blur"; break;
      case "expand": p.fill = "border"; p.featherScale = 0.5; break;
      case "add": p.fill = "neutral"; break;
      case "remove": p.fill = "border"; p.defaultPrompt = flux2 ? "" : "background scenery"; break;
      case "background": p.fill = "neutral"; p.invert = true; p.featherScale = 0.1; p.context = "image"; break;
      case "custom": p.context = opts.context || "auto"; break;
    }
    if (flux2 && FLUX2_INSTRUCTIONS[mode]) {
      p.instruction = FLUX2_INSTRUCTIONS[mode];
      if (mode === "fill" || mode === "expand") p.referenceFill = "green";
    }
    return p;
  }

  function composePrompt(p, prompt) {
    prompt = (prompt || "").trim() || p.defaultPrompt;
    return [p.instruction, prompt].filter(Boolean).join(" ");
  }

  /* Bytes (0..255) over w×h marking transparent pixels, and their bounding box, or null. */
  function transparentMask(rgba, w, h) {
    var bytes = new Uint8Array(w * h), x0 = w, y0 = h, x1 = -1, y1 = -1, n = 0;
    for (var y = 0; y < h; y++) for (var x = 0; x < w; x++) {
      var i = y * w + x;
      if (rgba[i * 4 + 3] < 16) {
        bytes[i] = 255; n++;
        if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
      }
    }
    if (n < w * h * 0.005) return null;
    var rect = { x: x0, y: y0, width: x1 - x0 + 1, height: y1 - y0 + 1 };
    var out = new Uint8Array(rect.width * rect.height);
    for (var yy = 0; yy < rect.height; yy++) out.set(bytes.subarray((y0 + yy) * w + x0, (y0 + yy) * w + x0 + rect.width), yy * rect.width);
    return { rect: rect, bytes: out };
  }

  function invert(bytes) {
    var out = new Uint8Array(bytes.length);
    for (var i = 0; i < bytes.length; i++) out[i] = 255 - bytes[i];
    return out;
  }

  CS.inpaint = { MODES: MODES, FLUX2_INSTRUCTIONS: FLUX2_INSTRUCTIONS, resolveMode: resolveMode, plan: plan, composePrompt: composePrompt, transparentMask: transparentMask, invert: invert };
})(typeof window !== "undefined" ? (window.CS = window.CS || {}) : (globalThis.CS = globalThis.CS || {}));
