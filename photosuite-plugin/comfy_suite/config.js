/* ComfySuite settings. Edit and restart PhotoSuite to change the defaults.
   (The panel runs sandboxed, so changes made in its Settings tab may not survive a restart.) */
var COMFY_SUITE_CONFIG = {
  comfyUrl: "http://127.0.0.1:8188",
  language: "uk",          // "uk", "en", or "" to follow the system
  theme: "dark",           // "dark", or "light" for PhotoSuite's Pearl theme
  style: "",               // name of the default style
  batch: 2,
  selectionGrow: 8,
  selectionFeather: 12,
  contextPadding: 0.25,
  liveIntervalMs: 1500,
  styles: []               // extra styles, same shape as the built-in ones in styles.js
};
