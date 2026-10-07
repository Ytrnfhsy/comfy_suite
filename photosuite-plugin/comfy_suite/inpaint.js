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

  // Flux 2 is an edit model: the region is its reference image and these instructions steer it
  // (the same ones Krita's AI plugin uses).
  var FLUX2_INSTRUCTIONS = {
    green: "Fill the green spaces according to the image.",
    expand: "Expand the image to fill the empty canvas.",
    add: "Add the object to the scene.",
    remove: "Remove the object.",
    background: "Replace the background while keeping the main subject."
  };

  function resolveMode(mode, transparentShare) {
    if (mode && mode !== "auto") return mode;
    return transparentShare > 0.25 ? "expand" : "fill";
  }

  /* How to prepare a request for `mode` (already resolved) at `strength` with architecture `arch`.
     opts: { context, flux2Outpaint: an outpaint LoRA for this Flux 2 model is installed }
     Returns { mode, fill: "blur"|"border"|"neutral"|"green"|"none", invert, featherScale,
               context: "auto"|"mask"|"image", instruction, defaultPrompt, outpaintLora } */
  function plan(mode, arch, strength, opts) {
    opts = opts || {};
    var p = { mode: mode, fill: "none", invert: false, featherScale: 1, context: "auto", instruction: "", defaultPrompt: "", outpaintLora: false };
    if (strength < 1) { p.mode = "refine"; return p; }
    switch (mode) {
      case "fill": p.fill = "blur"; break;
      case "expand": p.fill = "border"; break;
      case "add": p.fill = "neutral"; break;
      case "remove": p.fill = "border"; p.defaultPrompt = "background scenery"; break;
      case "background": p.fill = "neutral"; p.invert = true; p.featherScale = 0.1; p.context = "image"; break;
      case "custom": p.context = opts.context || "auto"; break;
    }
    if (arch === "flux2") {
      // Flux 2 edits from its reference (the region itself): no pre-fill, except the green
      // marking an outpaint LoRA was trained on. Without that LoRA green would just be copied.
      p.fill = "none";
      p.defaultPrompt = "";
      if ((mode === "fill" || mode === "expand") && opts.flux2Outpaint) {
        p.fill = "green";
        p.instruction = FLUX2_INSTRUCTIONS.green;
        p.outpaintLora = true;
      } else if (mode !== "fill" && FLUX2_INSTRUCTIONS[mode]) {
        p.instruction = FLUX2_INSTRUCTIONS[mode];
      }
    }
    return p;
  }

  function composePrompt(p, prompt) {
    prompt = (prompt || "").trim() || p.defaultPrompt;
    return [p.instruction, prompt].filter(Boolean).join("\n\n");
  }

  /* Mask sizes, as Krita computes them from the selection's size:
       feather = max(featherPct% of the diagonal, minFeather px) × strength (no minimum when inverted)
       grow    = growOffset + feather / 2           (denoise mask: dilate by grow, then blur by feather)
       blend   = min(blendMax, grow + feather / 2)   (result alpha: erode by blend / 2, then blur by blend)
     s: { selectionFeather (%), selectionMinFeather, selectionGrow (offset px), selectionBlend } */
  function maskSizes(s, width, height, strength, featherScale, invert) {
    var diagonal = Math.sqrt(width * width + height * height);
    var feather = Math.round((s.selectionFeather / 100) * featherScale * strength * diagonal);
    if (!invert) feather = Math.max(feather, Math.round(s.selectionMinFeather * strength));
    if (!(s.selectionFeather > 0)) feather = 0;
    var grow = s.selectionFeather > 0 ? s.selectionGrow + Math.floor(feather / 2) : 0;
    var blend = s.selectionFeather > 0 ? Math.min(s.selectionBlend, grow + Math.floor(feather / 2)) : 0;
    return { feather: feather, grow: grow, blend: blend };
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

  CS.inpaint = { MODES: MODES, FLUX2_INSTRUCTIONS: FLUX2_INSTRUCTIONS, resolveMode: resolveMode, plan: plan, composePrompt: composePrompt, maskSizes: maskSizes, transparentMask: transparentMask, invert: invert };
})(typeof window !== "undefined" ? (window.CS = window.CS || {}) : (globalThis.CS = globalThis.CS || {}));
