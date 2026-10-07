/* The work behind each workspace: read the document, run ComfyUI, return results.
   A result: { images: [canvas], region, mask (region-sized bytes | null), docWidth, docHeight,
               seed, prompt, newDocument } */
(function (CS) {
  "use strict";
  var I = CS.imaging, W = CS.workflow;

  function Generator(comfy, settings) {
    this.comfy = comfy;
    this.settings = settings;
  }

  Generator.prototype.upload = function (c) {
    var comfy = this.comfy;
    return I.toBlob(c).then(function (b) { return comfy.upload(b); });
  };

  Generator.prototype.run = function (graph, job) {
    W.fillDefaults(graph, this.comfy.models.info);
    return this.comfy.run(graph, job).then(function (blobs) {
      if (!blobs.length) throw new Error("ComfyUI returned no images");
      return Promise.all(blobs.map(function (b) { return I.decode(b); }));
    });
  };

  /* params: { prompt, negative, strength, seed, batch, useSelection, controls: [{mode, source: "canvas"|"file", image: canvas, strength, start, end, model, preprocess}] } */
  Generator.prototype.generate = function (style, params, job, live) {
    var self = this, s = this.settings;
    job.onProgress && job.onProgress(0, CS.tr("reading the document"));
    return Promise.all([CS.host.composite(), params.useSelection === false ? null : CS.host.selectionMask()]).then(function (got) {
      var doc = got[0], sel = got[1];
      var docRect = I.rect(0, 0, doc.width, doc.height);
      var region = sel ? I.inpaintContext(sel.rect, docRect, s.contextPadding) : docRect;
      if (region.width < 1 || region.height < 1) throw new Error("the selection is outside the canvas");
      var size = I.generationExtent(region.width, region.height, style.native_resolution);
      var regionMask = sel ? I.maskForRegion(sel.bytes, sel.rect, region) : null;
      var needsPixels = params.strength < 1 || !!sel;
      var seed = W.resolveSeed(params.seed);
      var req = {
        prompt: params.prompt, negative: params.negative, strength: params.strength, seed: seed,
        batch: live ? 1 : params.batch, width: size.width, height: size.height, live: !!live, controls: []
      };
      var uploads = [];
      if (needsPixels) {
        uploads.push(self.upload(I.resize(I.flatten(I.crop(doc.canvas, region)), size.width, size.height)).then(function (n) { req.image = n; }));
      }
      if (regionMask) {
        var prepared = I.prepareMask(regionMask, region.width, region.height, s.selectionGrow, s.selectionFeather);
        uploads.push(self.upload(I.resize(I.maskCanvas(prepared, region.width, region.height), size.width, size.height)).then(function (n) { req.mask = n; }));
      }
      (params.controls || []).forEach(function (c, i) {
        if (!(c.strength > 0)) return;
        var src = c.source === "file" && c.image ? I.resize(c.image, doc.width, doc.height) : doc.canvas;
        uploads.push(self.upload(I.resize(I.flatten(I.crop(src, region)), size.width, size.height)).then(function (n) {
          req.controls[i] = { mode: c.mode, image: n, strength: c.strength, start: c.start, end: c.end, model: c.model, preprocess: c.preprocess };
        }));
      });
      return Promise.all(uploads).then(function () {
        req.controls = req.controls.filter(Boolean);
        if (job.cancelled) throw new CS.comfy.Cancelled();
        var graph = W.buildGenerate(style, self.comfy.models, req).toJSON();
        return self.run(graph, job);
      }).then(function (images) {
        return {
          images: images.map(function (im) { return I.resize(im, region.width, region.height); }),
          region: region, mask: regionMask, docWidth: doc.width, docHeight: doc.height,
          seed: seed, prompt: params.prompt, newDocument: false
        };
      });
    });
  };

  Generator.prototype.upscale = function (style, opts, job) {
    var self = this;
    return CS.host.composite().then(function (doc) {
      var w = Math.max(1, Math.round(doc.width * opts.factor)), h = Math.max(1, Math.round(doc.height * opts.factor));
      return self.upload(I.flatten(doc.canvas)).then(function (name) {
        var seed = W.resolveSeed(-1);
        var graph = W.buildUpscale(style, self.comfy.models, {
          image: name, width: w, height: h, model: opts.model, refine: opts.refine, strength: opts.strength, prompt: opts.prompt, seed: seed
        }).toJSON();
        return self.run(graph, job).then(function (images) {
          return { images: [I.resize(images[0], w, h)], region: I.rect(0, 0, w, h), mask: null, docWidth: w, docHeight: h, seed: seed, prompt: opts.prompt || "", newDocument: true };
        });
      });
    });
  };

  Generator.prototype.custom = function (workflow, values, job) {
    var self = this;
    var names = W.customPlaceholders(workflow);
    var wantsImage = names.indexOf("canvas") >= 0 || names.indexOf("mask") >= 0;
    return Promise.all([CS.host.composite(), names.indexOf("mask") >= 0 ? CS.host.selectionMask() : null]).then(function (got) {
      var doc = got[0], sel = got[1];
      var docRect = I.rect(0, 0, doc.width, doc.height);
      values = Object.assign({}, values);
      var uploads = [];
      if (wantsImage) uploads.push(self.upload(I.flatten(doc.canvas)).then(function (n) { values.canvas = n; }));
      if (names.indexOf("mask") >= 0) {
        var bytes = sel ? I.maskForRegion(sel.bytes, sel.rect, docRect) : new Uint8Array(doc.width * doc.height).fill(255);
        uploads.push(self.upload(I.maskCanvas(bytes, doc.width, doc.height)).then(function (n) { values.mask = n; }));
      }
      if (!("width" in values)) values.width = doc.width;
      if (!("height" in values)) values.height = doc.height;
      values.seed = W.resolveSeed(values.seed == null ? -1 : +values.seed);
      return Promise.all(uploads).then(function () {
        return self.run(W.applyCustom(workflow, values), job);
      }).then(function (images) {
        return {
          images: images.map(function (im) { return I.resize(im, doc.width, doc.height); }),
          region: docRect, mask: null, docWidth: doc.width, docHeight: doc.height, seed: values.seed, prompt: String(values.prompt || ""), newDocument: false
        };
      });
    });
  };

  /* The image as it should land: region-sized, with the selection as alpha. */
  Generator.prototype.layerImage = function (result, index) {
    var im = result.images[index];
    return result.mask ? I.withAlpha(im, result.mask) : im;
  };

  Generator.prototype.apply = function (result, index) {
    var im = this.layerImage(result, index);
    if (result.newDocument) return CS.host.openDocument(im);
    var full = I.canvas(result.docWidth, result.docHeight);
    full.getContext("2d").drawImage(im, result.region.x, result.region.y);
    var label = "[AI] " + ((result.prompt || "").replace(/\s+/g, " ").trim().slice(0, 40) || "Generated") + " (" + result.seed + ")";
    return CS.host.placeLayer(full, label);
  };

  CS.Generator = Generator;
})(window.CS = window.CS || {});
