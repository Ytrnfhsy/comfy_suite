/* Geometry and pixels: where to generate, at what size, and mask processing. The math is
   pure (works in node for tests); the canvas helpers need a browser. */
(function (CS) {
  "use strict";

  function rect(x, y, w, h) { return { x: x, y: y, width: w, height: h }; }

  function clampRect(r, outer) {
    var x0 = Math.max(r.x, outer.x), y0 = Math.max(r.y, outer.y);
    var x1 = Math.min(r.x + r.width, outer.x + outer.width), y1 = Math.min(r.y + r.height, outer.y + outer.height);
    return rect(x0, y0, Math.max(0, x1 - x0), Math.max(0, y1 - y0));
  }

  function multipleOf(v, n) { n = n || 8; return Math.max(n, Math.round(v / n) * n); }

  /* Generation size for a region: scaled towards native² pixels, multiples of 8. */
  function generationExtent(width, height, native, minSide, maxFactor) {
    native = native || 1024; minSide = minSide || 256; maxFactor = maxFactor || 1.5;
    width = Math.max(1, width); height = Math.max(1, height);
    var pixels = width * height, target = native * native, s = 1;
    if (pixels < target) s = Math.sqrt(target / pixels);
    else if (pixels > target * maxFactor) s = Math.sqrt(target * maxFactor / pixels);
    var gw = width * s, gh = height * s, shortest = Math.min(gw, gh);
    if (shortest < minSide) { gw *= minSide / shortest; gh *= minSide / shortest; }
    // Multiples of 16: Flux 2 latents are 1/16 of the image (and 16 suits SD/Flux 1 too).
    return { width: multipleOf(gw, 16), height: multipleOf(gh, 16) };
  }

  function inpaintContext(sel, canvas, padding, minPadding) {
    padding = padding == null ? 0.25 : padding; minPadding = minPadding || 32;
    var pad = Math.max(minPadding, Math.floor(padding * Math.max(sel.width, sel.height)));
    return clampRect(rect(sel.x - pad, sel.y - pad, sel.width + 2 * pad, sel.height + 2 * pad), canvas);
  }

  /* Separable max filter (dilation) and box blur over a w×h byte mask. */
  function dilate(src, w, h, radius) {
    if (radius <= 0) return src;
    var tmp = new Uint8Array(w * h), out = new Uint8Array(w * h), x, y, k, m;
    for (y = 0; y < h; y++) for (x = 0; x < w; x++) {
      m = 0;
      for (k = Math.max(0, x - radius); k <= Math.min(w - 1, x + radius); k++) if (src[y * w + k] > m) m = src[y * w + k];
      tmp[y * w + x] = m;
    }
    for (y = 0; y < h; y++) for (x = 0; x < w; x++) {
      m = 0;
      for (k = Math.max(0, y - radius); k <= Math.min(h - 1, y + radius); k++) if (tmp[k * w + x] > m) m = tmp[k * w + x];
      out[y * w + x] = m;
    }
    return out;
  }

  function boxBlur(src, w, h, radius) {
    if (radius <= 0) return src;
    var tmp = new Float32Array(w * h), out = new Uint8Array(w * h), x, y, sum, n, k;
    for (y = 0; y < h; y++) {
      sum = 0; n = 0;
      for (k = 0; k <= Math.min(w - 1, radius); k++) { sum += src[y * w + k]; n++; }
      for (x = 0; x < w; x++) {
        tmp[y * w + x] = sum / n;
        var add = x + radius + 1, rem = x - radius;
        if (add < w) { sum += src[y * w + add]; n++; }
        if (rem >= 0) { sum -= src[y * w + rem]; n--; }
      }
    }
    for (x = 0; x < w; x++) {
      sum = 0; n = 0;
      for (k = 0; k <= Math.min(h - 1, radius); k++) { sum += tmp[k * w + x]; n++; }
      for (y = 0; y < h; y++) {
        out[y * w + x] = Math.round(sum / n);
        var a = y + radius + 1, r = y - radius;
        if (a < h) { sum += tmp[a * w + x]; n++; }
        if (r >= 0) { sum -= tmp[r * w + x]; n--; }
      }
    }
    return out;
  }

  /* Grow and feather a coverage mask so seams blend (two box passes ≈ a soft blur). */
  function prepareMask(mask, w, h, grow, feather) {
    var m = dilate(mask, w, h, Math.min(25, grow | 0));
    // Two box passes of radius feather/4: a ~feather px wide, roughly linear ramp.
    var r = Math.max(0, Math.round((feather | 0) / 4));
    return boxBlur(boxBlur(m, w, h, r), w, h, r);
  }

  function erode(src, w, h, radius) {
    if (radius <= 0) return src;
    var inv = new Uint8Array(src.length);
    for (var i = 0; i < src.length; i++) inv[i] = 255 - src[i];
    var d = dilate(inv, w, h, radius);
    for (var j = 0; j < d.length; j++) d[j] = 255 - d[j];
    return d;
  }

  /* The result's alpha, as Krita blends it: everything the denoise mask touched, shrunk by
     blend/2 and softened by blend, never less than the selection itself. */
  function compositeMask(weight, selection, w, h, blend) {
    var m = new Uint8Array(w * h), i;
    for (i = 0; i < m.length; i++) m[i] = weight[i] > 0 ? 255 : 0;
    if (blend > 0) {
      var r = Math.max(1, Math.round(blend / 4));
      m = boxBlur(boxBlur(erode(m, w, h, Math.floor(blend / 2)), w, h, r), w, h, r);
    }
    for (i = 0; i < m.length; i++) if (selection[i] > m[i]) m[i] = selection[i];
    return m;
  }

  /* The selection's coverage (rect-sized bytes) placed into a region-sized mask. */
  function maskForRegion(selBytes, selRect, region) {
    var out = new Uint8Array(region.width * region.height);
    for (var y = 0; y < selRect.height; y++) {
      var ry = selRect.y + y - region.y;
      if (ry < 0 || ry >= region.height) continue;
      for (var x = 0; x < selRect.width; x++) {
        var rx = selRect.x + x - region.x;
        if (rx < 0 || rx >= region.width) continue;
        out[ry * region.width + rx] = selBytes[y * selRect.width + x];
      }
    }
    return out;
  }

  /* ---- pre-filling the area to repaint (pure: RGBA bytes in, RGBA bytes out) ----
     What sits under the mask steers the result even at full strength (the latent the sampler
     starts from, inpaint conditioning, Flux 2's reference), so each inpaint mode prepares it
     differently, as Krita's AI plugin does. `weight` is 0..255 per pixel: 255 = repaint. */

  /* Push-pull fill: the unknown pixels take colours diffused in from the known ones, through
     an image pyramid, like a coarse Navier-Stokes inpaint. Handles any hole size. */
  function pushPull(rgba, w, h, known) {
    var levels = [];
    var cur = { w: w, h: h, c: new Float32Array(w * h * 3), k: new Float32Array(w * h) };
    for (var i = 0; i < w * h; i++) {
      var k = known[i];
      cur.k[i] = k;
      cur.c[i * 3] = rgba[i * 4] * k; cur.c[i * 3 + 1] = rgba[i * 4 + 1] * k; cur.c[i * 3 + 2] = rgba[i * 4 + 2] * k;
    }
    levels.push(cur);
    while (cur.w > 1 || cur.h > 1) {
      var nw = Math.max(1, Math.ceil(cur.w / 2)), nh = Math.max(1, Math.ceil(cur.h / 2));
      var nxt = { w: nw, h: nh, c: new Float32Array(nw * nh * 3), k: new Float32Array(nw * nh) };
      for (var y = 0; y < cur.h; y++) for (var x = 0; x < cur.w; x++) {
        var si = y * cur.w + x, di = (y >> 1) * nw + (x >> 1);
        nxt.k[di] += cur.k[si];
        nxt.c[di * 3] += cur.c[si * 3]; nxt.c[di * 3 + 1] += cur.c[si * 3 + 1]; nxt.c[di * 3 + 2] += cur.c[si * 3 + 2];
      }
      levels.push(nxt);
      cur = nxt;
    }
    // Pull: normalise each level, filling its holes from the coarser one.
    var coarse = null;
    for (var l = levels.length - 1; l >= 0; l--) {
      var L = levels[l], col = new Float32Array(L.w * L.h * 3);
      for (var j = 0; j < L.w * L.h; j++) {
        var kk = Math.min(1, L.k[j]);
        var up = coarse ? ((j / L.w | 0) >> 1) * coarse.w + ((j % L.w) >> 1) : -1;
        for (var ch = 0; ch < 3; ch++) {
          var own = L.k[j] > 0 ? L.c[j * 3 + ch] / L.k[j] : 0;
          var from = up >= 0 ? coarse.col[up * 3 + ch] : own;
          col[j * 3 + ch] = own * kk + from * (1 - kk);
        }
      }
      coarse = { w: L.w, h: L.h, col: col };
    }
    var out = new Uint8ClampedArray(rgba.length);
    for (var p = 0; p < w * h; p++) {
      out[p * 4] = coarse.col[p * 3]; out[p * 4 + 1] = coarse.col[p * 3 + 1]; out[p * 4 + 2] = coarse.col[p * 3 + 2]; out[p * 4 + 3] = 255;
    }
    return out;
  }

  function blurRgba(rgba, w, h, radius) {
    var out = new Uint8ClampedArray(rgba.length), ch = new Uint8Array(w * h);
    for (var c = 0; c < 3; c++) {
      for (var i = 0; i < w * h; i++) ch[i] = rgba[i * 4 + c];
      var b = boxBlur(boxBlur(ch, w, h, radius), w, h, radius);
      for (var j = 0; j < w * h; j++) out[j * 4 + c] = b[j];
    }
    for (var k = 0; k < w * h; k++) out[k * 4 + 3] = 255;
    return out;
  }

  /* mode: "blur" (fill), "border" (expand, remove object), "neutral" (add object,
     replace background), "green" (Flux 2 edit prompts: "fill the green spaces"). Pixels that are
     transparent count as unknown too (an enlarged canvas). */
  function prefill(rgba, w, h, weight, mode) {
    var known = new Float32Array(w * h), i;
    for (i = 0; i < w * h; i++) known[i] = (1 - weight[i] / 255) * (rgba[i * 4 + 3] / 255);
    var fill;
    if (mode === "neutral") {
      fill = new Uint8ClampedArray(rgba.length).fill(128);
    } else if (mode === "green") {
      fill = new Uint8ClampedArray(rgba.length);
      for (i = 0; i < w * h; i++) { fill[i * 4 + 1] = 255; }
    } else {
      fill = pushPull(rgba, w, h, known);
      var radius = Math.max(2, Math.round(Math.min(w, h) / (mode === "blur" ? 16 : 40)));
      fill = blurRgba(fill, w, h, radius);
    }
    var out = new Uint8ClampedArray(rgba.length);
    for (i = 0; i < w * h; i++) {
      var k = mode === "green" ? (weight[i] > 127 || rgba[i * 4 + 3] < 128 ? 0 : 1) : known[i];
      for (var c = 0; c < 3; c++) out[i * 4 + c] = Math.round(rgba[i * 4 + c] * k + fill[i * 4 + c] * (1 - k));
      out[i * 4 + 3] = 255;
    }
    return out;
  }

  /* Share of the masked area that is transparent: an enlarged canvas waiting to be expanded. */
  function transparentShare(rgba, w, h, weight) {
    var masked = 0, empty = 0;
    for (var i = 0; i < w * h; i++) {
      if (weight[i] < 128) continue;
      masked++;
      if (rgba[i * 4 + 3] < 16) empty++;
    }
    return masked ? empty / masked : 0;
  }

  /* ---- canvas helpers (browser only) ---- */

  function canvas(w, h) {
    var c = document.createElement("canvas");
    c.width = Math.max(1, w | 0); c.height = Math.max(1, h | 0);
    return c;
  }

  function decode(bytes, mime) {
    var blob = bytes instanceof Blob ? bytes : new Blob([bytes], { type: mime || "image/png" });
    if (typeof createImageBitmap === "function") return createImageBitmap(blob).then(toCanvas);
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(blob), img = new Image();
      img.onload = function () { URL.revokeObjectURL(url); resolve(toCanvas(img)); };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error("cannot decode image")); };
      img.src = url;
    });
  }

  function toCanvas(img) {
    var c = canvas(img.width, img.height);
    c.getContext("2d").drawImage(img, 0, 0);
    return c;
  }

  function crop(src, r) {
    var c = canvas(r.width, r.height);
    c.getContext("2d").drawImage(src, r.x, r.y, r.width, r.height, 0, 0, r.width, r.height);
    return c;
  }

  function resize(src, w, h) {
    if (src.width === w && src.height === h) return src;
    // Halve repeatedly when shrinking a lot: one bilinear step aliases.
    var cur = src;
    while (cur.width / 2 >= w && cur.height / 2 >= h) {
      var half = canvas(Math.ceil(cur.width / 2), Math.ceil(cur.height / 2));
      var hctx = half.getContext("2d");
      hctx.imageSmoothingQuality = "high";
      hctx.drawImage(cur, 0, 0, half.width, half.height);
      cur = half;
    }
    var c = canvas(w, h), ctx = c.getContext("2d");
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(cur, 0, 0, w, h);
    return c;
  }

  /* RGB on white (models don't see alpha). */
  function flatten(src, color) {
    var c = canvas(src.width, src.height), ctx = c.getContext("2d");
    ctx.fillStyle = color || "#ffffff";
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.drawImage(src, 0, 0);
    return c;
  }

  function maskCanvas(bytes, w, h) {
    var c = canvas(w, h), ctx = c.getContext("2d"), img = ctx.createImageData(w, h);
    for (var i = 0; i < w * h; i++) {
      var v = bytes[i];
      img.data[i * 4] = v; img.data[i * 4 + 1] = v; img.data[i * 4 + 2] = v; img.data[i * 4 + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    return c;
  }

  /* Multiply a canvas's alpha by a same-sized byte mask. */
  function withAlpha(src, bytes) {
    var c = crop(src, rect(0, 0, src.width, src.height)), ctx = c.getContext("2d");
    var img = ctx.getImageData(0, 0, c.width, c.height);
    for (var i = 0; i < c.width * c.height; i++) img.data[i * 4 + 3] = Math.round(img.data[i * 4 + 3] * bytes[i] / 255);
    ctx.putImageData(img, 0, 0);
    return c;
  }

  function rgbaOf(c) { return c.getContext("2d").getImageData(0, 0, c.width, c.height).data; }
  function fromRgba(rgba, w, h) {
    var c = canvas(w, h), ctx = c.getContext("2d");
    ctx.putImageData(new ImageData(new Uint8ClampedArray(rgba), w, h), 0, 0);
    return c;
  }

  function toBlob(c) {
    return new Promise(function (resolve, reject) {
      c.toBlob(function (b) { if (b) resolve(b); else reject(new Error("PNG encoding failed")); }, "image/png");
    });
  }

  CS.imaging = {
    rect: rect, clampRect: clampRect, multipleOf: multipleOf, generationExtent: generationExtent,
    inpaintContext: inpaintContext, dilate: dilate, erode: erode, boxBlur: boxBlur, prepareMask: prepareMask, compositeMask: compositeMask,
    maskForRegion: maskForRegion, pushPull: pushPull, prefill: prefill, transparentShare: transparentShare, canvas: canvas, decode: decode, crop: crop, resize: resize,
    flatten: flatten, maskCanvas: maskCanvas, withAlpha: withAlpha, toBlob: toBlob,
    rgbaOf: rgbaOf, fromRgba: fromRgba
  };
})(typeof window !== "undefined" ? (window.CS = window.CS || {}) : (globalThis.CS = globalThis.CS || {}));
