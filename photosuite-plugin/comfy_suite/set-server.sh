#!/bin/sh
# Remembers the ComfyUI server for the ComfySuite AI panel across PhotoSuite restarts.
# The panel runs sandboxed and cannot write files itself, so its settings live in
# config.local.js next to this script (not tracked by git: `git pull` keeps it).
# Usage: sh set-server.sh http://100.64.0.5:8188
set -eu
url=${1:-}
case "$url" in
  http://*|https://*) ;;
  *) echo "Usage: sh $0 http://HOST:8188" >&2; exit 2 ;;
esac
url=${url%/}
here=$(cd "$(dirname "$0")" && pwd)
case "$url" in *\"*|*\\*) echo "The address must not contain quotes or backslashes" >&2; exit 2 ;; esac
cat > "$here/config.local.js" <<JS
/* Written by set-server.sh: your settings, kept across updates. */
Object.assign(COMFY_SUITE_CONFIG, {
  comfyUrl: "$url"
});
JS
echo "Saved $url to $here/config.local.js"
echo "Restart PhotoSuite (or reopen the panel) to use it."
