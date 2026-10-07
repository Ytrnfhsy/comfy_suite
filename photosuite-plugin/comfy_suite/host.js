/* Talking to PhotoSuite (0.9.14+) from the sidebar panel.

   - getComposite / getSelectionMask requests read the document;
   - an action-script string places results: app.open(dataURL, null, true) adds the image
     to the active document as a smart object, centred. Every result is sent as a
     document-sized PNG that is transparent outside its region, so centring puts it exactly
     where it belongs, and the selection's coverage is baked into its alpha;
   - an ArrayBuffer opens as a new document (upscales). */
(function (CS) {
  "use strict";

  var pending = {};
  var listeners = [];

  window.addEventListener("message", function (event) {
    var data = event.data;
    if (data && typeof data === "object" && data.psPlugin === 1 && pending[data.requestId]) {
      var p = pending[data.requestId];
      delete pending[data.requestId];
      clearTimeout(p.timer);
      if (data.cmd === "error") p.reject(new Error(data.error || "PhotoSuite error"));
      else p.resolve(data);
      return;
    }
    listeners.forEach(function (fn) { try { fn(data); } catch (e) { console.error(e); } });
  });

  function request(cmd, timeoutMs) {
    var requestId = "cs-" + Date.now() + "-" + Math.random().toString(36).slice(2);
    return new Promise(function (resolve, reject) {
      pending[requestId] = {
        resolve: resolve, reject: reject,
        timer: setTimeout(function () {
          delete pending[requestId];
          reject(new Error("PhotoSuite did not answer `" + cmd + "` (needs PhotoSuite 0.9.14 or newer)"));
        }, timeoutMs || 30000)
      };
      parent.postMessage({ psPlugin: 1, cmd: cmd, requestId: requestId }, "*");
    });
  }

  function ping() { return request("ping", 3000); }

  /* { canvas, width, height } of the visible document, full resolution. */
  function composite() {
    return request("getComposite", 60000).then(function (r) {
      return CS.imaging.decode(r.png, r.mime).then(function (c) {
        return { canvas: c, width: r.sourceWidth || r.width, height: r.sourceHeight || r.height };
      });
    });
  }

  /* { rect, bytes, documentWidth, documentHeight } or null without a selection. */
  function selectionMask() {
    return request("getSelectionMask").then(function (r) {
      return { rect: r.rect, bytes: new Uint8Array(r.mask), documentWidth: r.documentWidth, documentHeight: r.documentHeight };
    }, function (e) {
      if (/no selection/i.test(e.message)) return null;
      throw e;
    });
  }

  function runScript(source) { parent.postMessage(source, "*"); }

  /* Evaluate a script expression in PhotoSuite and resolve with its value as text
     (sent back through app.echoToOE). */
  function evalScript(expression, timeoutMs) {
    var tag = "cs:" + Math.random().toString(36).slice(2) + ":";
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(function () { off(); reject(new Error("PhotoSuite did not run the script")); }, timeoutMs || 5000);
      function onMsg(data) {
        if (typeof data === "string" && data.indexOf(tag) === 0) { clearTimeout(timer); off(); resolve(data.slice(tag.length)); }
      }
      function off() { listeners = listeners.filter(function (fn) { return fn !== onMsg; }); }
      listeners.push(onMsg);
      runScript("app.echoToOE(" + JSON.stringify(tag) + " + (" + expression + "));");
    });
  }

  function layerCount() {
    return evalScript("app.documents.length ? app.activeDocument.layers.length : -1").then(function (v) { return parseInt(v, 10); });
  }

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function blobToDataUrl(blob) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(reader.result); };
      reader.onerror = function () { reject(reader.error); };
      reader.readAsDataURL(blob);
    });
  }

  /* Place a document-sized canvas into the active document as a new layer named `name`.
     Placing is asynchronous in PhotoSuite: wait for the layer to appear, then rename it. */
  function placeLayer(docCanvas, name) {
    var before = -1;
    return layerCount().catch(function () { return -1; }).then(function (n) {
      before = n;
      if (n < 0) throw new Error(CS.tr("No document open in PhotoSuite"));
      return CS.imaging.toBlob(docCanvas).then(blobToDataUrl);
    }).then(function (url) {
      runScript("app.open(" + JSON.stringify(url) + ", null, true);");
      var deadline = Date.now() + 20000;
      function wait() {
        return sleep(250).then(layerCount).then(function (n) {
          if (n > before) return true;
          if (Date.now() > deadline) return false;
          return wait();
        }, function () { return Date.now() > deadline ? false : wait(); });
      }
      return wait();
    }).then(function (placed) {
      if (placed && name) runScript("app.activeDocument.activeLayer.name = " + JSON.stringify(name) + ";");
      return placed;
    });
  }

  function openDocument(c) {
    return CS.imaging.toBlob(c).then(function (b) { return b.arrayBuffer(); }).then(function (buf) {
      parent.postMessage(buf, "*", [buf]);
    });
  }

  CS.host = {
    request: request, ping: ping, composite: composite, selectionMask: selectionMask, runScript: runScript,
    evalScript: evalScript, layerCount: layerCount,
    placeLayer: placeLayer, openDocument: openDocument, onMessage: function (fn) { listeners.push(fn); }
  };
})(window.CS = window.CS || {});
