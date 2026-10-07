"""Runs the plugin inside the real PhotoSuite 0.9.15 web frontend, in Chromium.

PhotoSuite 0.9.15 is a Tauri app whose UI is plain JavaScript (``src/``). The harness serves
that folder over HTTP and stands in for the Tauri bridge (``window.__TAURI__``) just enough
for PhotoSuite's own plugin loader to discover and bundle ``photosuite-plugin/comfy_suite``,
so the panel is created exactly as the desktop app creates it (sandboxed srcdoc iframe).

Needs: ``PHOTOSUITE_WEB`` = a PhotoSuite 0.9.15 checkout's ``src`` folder with its vendor
submodules set up (``scripts/submodules-setup.sh``), and the ``playwright`` package.
"""

from __future__ import annotations

import functools
import http.server
import os
import threading
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[2] / "photosuite-plugin" / "comfy_suite"
CHROMIUM = os.environ.get("CHROMIUM", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


class _WithPlugin(_Quiet):
    """The app's files, plus the plugin folder under /__plugin__/ (same origin)."""

    plugin_dir = PLUGIN_DIR

    def translate_path(self, path: str) -> str:
        if path.startswith("/__plugin__/"):
            rel = path[len("/__plugin__/"):].split("?")[0]
            return str(self.plugin_dir / rel)
        return super().translate_path(path)


def serve(directory: Path, plugin_dir: Path = PLUGIN_DIR) -> tuple[http.server.ThreadingHTTPServer, str]:
    handler = functools.partial(type("Handler", (_WithPlugin,), {"plugin_dir": plugin_dir}), directory=str(directory))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# The Tauri bridge, reduced to what plugin discovery needs. Files are read over HTTP from
# the plugin server; everything else PhotoSuite asks the backend for fails politely.
TAURI_STUB = """
(() => {
  const PLUGIN = %(plugin)s;
  const FS_ROOT = "/fs/";
  function read(path) {
    const rel = path.startsWith(FS_ROOT) ? path.slice(FS_ROOT.length) : path;
    const xhr = new XMLHttpRequest();
    xhr.open("GET", PLUGIN + "/" + rel, false);
    xhr.overrideMimeType("text/plain; charset=x-user-defined");
    xhr.send();
    if (xhr.status !== 200) throw new Error("not found: " + path);
    const s = xhr.responseText, out = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i) & 0xff;
    return out;
  }
  window.__TAURI__ = {
    core: {
      convertFileSrc: (p) => PLUGIN + "/" + p.slice(FS_ROOT.length),
      invoke: async (cmd, args) => {
        if (cmd === "ensure_plugins_directory") return FS_ROOT;
        if (cmd === "discover_sidebar_plugins_command") {
          const m = JSON.parse(new TextDecoder().decode(read(FS_ROOT + "plugin.json")));
          return [{ id: m.id, name: m.name, version: m.version, entryPath: FS_ROOT + m.entry,
                    iconPath: FS_ROOT + m.icon, width: m.width, height: m.height, themed: !!m.themed }];
        }
        if (cmd === "read_file_bytes") return Array.from(read(args.path));
        if (cmd === "read_file_base64") { let b = ""; read(args.path).forEach(c => b += String.fromCharCode(c)); return btoa(b); }
        throw new Error("not available in the test harness: " + cmd);
      }
    },
    event: { listen: async () => () => {}, emit: async () => {} },
    window: { getCurrentWindow: () => ({ listen: async () => () => {}, onCloseRequested: async () => () => {} }) },
  };
})();
"""


class Harness:
    def __init__(self, photosuite_src: Path, plugin_dir: Path = PLUGIN_DIR):
        """``plugin_dir`` is the folder that sits in PhotoSuite's plugins directory."""
        self.app_srv, self.app_url = serve(photosuite_src, plugin_dir)
        self.plugin_url = self.app_url + "/__plugin__"
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(executable_path=CHROMIUM, args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
        self.page = self.browser.new_page(viewport={"width": 1500, "height": 950}, locale="uk-UA")
        self.logs: list[str] = []
        self.page.on("console", lambda m: self.logs.append(f"{m.type}: {m.text}"))
        self.page.on("pageerror", lambda e: self.logs.append(f"pageerror: {e}"))
        self.page.add_init_script(TAURI_STUB % {"plugin": repr(self.plugin_url)})

    def open(self) -> None:
        self.page.goto(self.app_url + "/index.html")
        self.page.wait_for_function("() => document.querySelector('iframe[data-plugin-panel]') || document.readyState === 'complete'", timeout=60000)

    def plugin_frame(self):
        handle = self.page.wait_for_selector("iframe[data-plugin-panel]", state="attached", timeout=30000)
        return handle.content_frame()

    def close(self) -> None:
        self.browser.close()
        self._pw.stop()
        self.app_srv.shutdown()
