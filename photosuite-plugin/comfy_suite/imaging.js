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
    var r = Math.max(0, Math.round((feather | 0) / 2));
    return boxBlur(boxBlur(m, w, h, r), w, h, r);
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

  function toBlob(c) {
    return new Promise(function (resolve, reject) {
      c.toBlob(function (b) { if (b) resolve(b); else reject(new Error("PNG encoding failed")); }, "image/png");
    });
  }

  CS.imaging = {
    rect: rect, clampRect: clampRect, multipleOf: multipleOf, generationExtent: generationExtent,
    inpaintContext: inpaintContext, dilate: dilate, boxBlur: boxBlur, prepareMask: prepareMask,
    maskForRegion: maskForRegion, canvas: canvas, decode: decode, crop: crop, resize: resize,
    flatten: flatten, maskCanvas: maskCanvas, withAlpha: withAlpha, toBlob: toBlob
  };
})(typeof window !== "undefined" ? (window.CS = window.CS || {}) : (globalThis.CS = globalThis.CS || {}));
