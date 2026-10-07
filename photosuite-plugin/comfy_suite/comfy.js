/* ComfyUI client: models, uploads, queueing, progress (WebSocket) and results.
   Uses only ComfyUI's built-in API. The panel is a sandboxed page, so ComfyUI must be
   started with --enable-cors-header for the browser to let it read the replies. */
(function (CS) {
  "use strict";

  function Cancelled() { this.message = "cancelled"; this.cancelled = true; }
  Cancelled.prototype = Object.create(Error.prototype);

  function ComfyClient(url) {
    this.url = String(url || "http://127.0.0.1:8188").replace(/\/+$/, "");
    this.clientId = "comfy-suite-" + Math.random().toString(36).slice(2);
    this.models = null;
  }

  ComfyClient.prototype.request = function (path, init) {
    var url = this.url + path;
    return fetch(url, init).catch(function (e) {
      throw new Error("cannot reach ComfyUI at " + url.split("/").slice(0, 3).join("/") +
        " (" + (e && e.message ? e.message : e) + "). Is it running with --enable-cors-header?");
    }).then(function (r) {
      if (r.ok) return r;
      return r.text().then(function (body) {
        var msg = body;
        try {
          var j = JSON.parse(body);
          msg = (j.error && (j.error.message || j.error)) || body;
          if (j.node_errors && Object.keys(j.node_errors).length) msg += " " + JSON.stringify(j.node_errors).slice(0, 500);
        } catch (e) { /* plain text */ }
        throw new Error("ComfyUI " + path.split("?")[0] + ": HTTP " + r.status + ": " + String(msg).slice(0, 600));
      });
    });
  };

  ComfyClient.prototype.json = function (path, body) {
    var init = body === undefined ? undefined : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    return this.request(path, init).then(function (r) { return r.text(); }).then(function (t) { return t.trim() ? JSON.parse(t) : null; });
  };

  ComfyClient.prototype.systemStats = function () { return this.json("/system_stats"); };

  ComfyClient.prototype.loadModels = function () {
    var self = this;
    return this.json("/object_info").then(function (info) { self.models = CS.workflow.parseModels(info); return self.models; });
  };

  ComfyClient.prototype.upload = function (blob, name) {
    name = name || "comfy-suite-" + Math.random().toString(36).slice(2, 14) + ".png";
    var form = new FormData();
    form.append("image", blob, name);
    form.append("overwrite", "true");
    form.append("type", "input");
    form.append("subfolder", "comfy-suite");
    return this.request("/upload/image", { method: "POST", body: form }).then(function (r) { return r.json(); }).then(function (j) {
      return j.subfolder ? j.subfolder + "/" + j.name : j.name;
    });
  };

  ComfyClient.prototype.view = function (im) {
    var q = "filename=" + encodeURIComponent(im.filename) + "&subfolder=" + encodeURIComponent(im.subfolder || "") + "&type=" + encodeURIComponent(im.type || "output");
    return this.request("/view?" + q).then(function (r) { return r.blob(); });
  };

  ComfyClient.prototype.queue = function (graph) {
    return this.json("/prompt", { prompt: graph, client_id: this.clientId }).then(function (r) {
      if (!r || !r.prompt_id) throw new Error("ComfyUI did not accept the workflow: " + JSON.stringify(r));
      if (r.node_errors && Object.keys(r.node_errors).length) throw new Error("workflow errors: " + JSON.stringify(r.node_errors).slice(0, 800));
      return r.prompt_id;
    });
  };

  ComfyClient.prototype.interrupt = function () { return this.json("/interrupt", {}).catch(function () {}); };

  ComfyClient.prototype.cancel = function (promptId) {
    var self = this;
    return this.json("/queue", { "delete": [promptId] }).catch(function () {}).then(function () { return self.interrupt(); });
  };

  ComfyClient.prototype.history = function (promptId) {
    return this.json("/history/" + promptId).then(function (h) { return h ? h[promptId] : null; });
  };

  function statusError(status) {
    var msgs = (status && status.messages) || [];
    for (var i = 0; i < msgs.length; i++) {
      if (msgs[i][0] === "execution_error") {
        var d = msgs[i][1] || {};
        return (d.node_type || "node") + ": " + (d.exception_message || "error");
      }
    }
    return "the workflow failed";
  }

  ComfyClient.prototype.outputs = function (promptId) {
    var self = this;
    return this.history(promptId).then(function (entry) {
      if (!entry) throw new Error("the job has no results in ComfyUI's history");
      if (entry.status && entry.status.status_str === "error") throw new Error(statusError(entry.status));
      var ids = Object.keys(entry.outputs || {}).sort(function (a, b) { return (parseInt(a, 10) || 1e9) - (parseInt(b, 10) || 1e9); });
      var images = [];
      ids.forEach(function (id) { (entry.outputs[id].images || []).forEach(function (im) { images.push(im); }); });
      return Promise.all(images.map(function (im) { return self.view(im); }));
    });
  };

  /* Queue graph, follow its progress, resolve with the output image blobs.
     job: { cancelled: bool, onProgress(fraction, text), onPreview(blob) } */
  ComfyClient.prototype.run = function (graph, job) {
    var self = this;
    job = job || {};
    var ws = null, promptId = null, done = false, failure = null;
    try {
      ws = new WebSocket(this.url.replace(/^http/, "ws") + "/ws?clientId=" + this.clientId);
      ws.binaryType = "arraybuffer";
      ws.onmessage = function (ev) {
        if (typeof ev.data !== "string") {
          var view = new DataView(ev.data);
          if (ev.data.byteLength > 8 && view.getUint32(0) === 1 && job.onPreview) job.onPreview(new Blob([ev.data.slice(8)]));
          return;
        }
        var msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
        var d = msg.data || {};
        if (!promptId || (d.prompt_id && d.prompt_id !== promptId)) return;
        if (msg.type === "progress" && job.onProgress) job.onProgress(d.value / Math.max(1, d.max), "step " + d.value + "/" + d.max);
        else if (msg.type === "executing" && d.node == null && d.prompt_id === promptId) done = true;
        else if (msg.type === "execution_success") done = true;
        else if (msg.type === "execution_error") { failure = (d.node_type || "node") + ": " + (d.exception_message || "error"); done = true; }
        else if (msg.type === "execution_interrupted") { failure = "interrupted"; done = true; }
      };
      ws.onerror = function () { ws = null; };
    } catch (e) { ws = null; }

    function close() { if (ws) try { ws.close(); } catch (e) { /* closed */ } }

    return this.queue(graph).then(function (id) {
      promptId = id;
      var lastPoll = 0, started = Date.now();
      return new Promise(function (resolve, reject) {
        function tick() {
          if (job.cancelled) { self.cancel(promptId); return reject(new Cancelled()); }
          if (failure) return reject(new Error(failure));
          if (done) return resolve();
          if (Date.now() - started > 3600e3) { self.cancel(promptId); return reject(new Error("timed out waiting for ComfyUI")); }
          // Poll the history too: the socket may be unavailable, or a cached job may finish
          // before its prompt id is known.
          if (Date.now() - lastPoll > (ws ? 3000 : 1000)) {
            lastPoll = Date.now();
            self.history(promptId).then(function (entry) {
              if (entry && entry.status && entry.status.status_str === "error") failure = statusError(entry.status);
              else if (entry && entry.outputs && Object.keys(entry.outputs).length && entry.status && entry.status.completed !== undefined) done = true;
            }).catch(function () {});
          }
          setTimeout(tick, 200);
        }
        tick();
      });
    }).then(function () {
      if (job.onProgress) job.onProgress(1, "done");
      return self.outputs(promptId);
    }).then(function (blobs) { close(); return blobs; }, function (e) { close(); throw e; });
  };

  CS.comfy = { ComfyClient: ComfyClient, Cancelled: Cancelled };
})(typeof window !== "undefined" ? (window.CS = window.CS || {}) : (globalThis.CS = globalThis.CS || {}));
