#!/bin/sh
# Installs the ComfySuite AI sidebar plugin into PhotoSuite 0.9.14–0.9.15 (Linux, macOS).
# Usage: sh install-plugin.sh [http://COMFYUI:8188]
set -eu
backup=""

here=$(cd "$(dirname "$0")" && pwd)
case "$(uname -s)" in
  Darwin) plugins="$HOME/Library/Application Support/app.photosuite/plugins" ;;
  *)      plugins="${XDG_DATA_HOME:-$HOME/.local/share}/app.photosuite/plugins" ;;
esac
target="$plugins/comfy_suite"

case "$here/" in
  "$plugins"/*)
    echo "This checkout is already inside PhotoSuite's plugins folder ($here)."
    echo "Its plugin.json makes it the plugin: restart PhotoSuite and open Window > ComfySuite AI."
    exit 0 ;;
esac

mkdir -p "$plugins"
if [ -e "$target" ]; then
  # Outside the plugins folder, so PhotoSuite never loads the old copy as a duplicate.
  backup="$(dirname "$plugins")/comfy_suite.backup-$(date +%Y%m%d-%H%M%S)"
  echo "Moving the existing $target to $backup"
  mv "$target" "$backup"
fi
cp -R "$here/photosuite-plugin/comfy_suite" "$target"
# Keep the server address saved by set-server.sh.
if [ -n "${backup:-}" ]; then
  for old in "$backup/config.local.js" "$backup/photosuite-plugin/comfy_suite/config.local.js"; do
    [ -f "$old" ] && cp "$old" "$target/config.local.js" && echo "Kept your settings from $old" && break
  done
fi
if [ -n "${1:-}" ]; then sh "$target/set-server.sh" "$1"; fi
echo "Installed to $target"
echo "Restart PhotoSuite and open Window > ComfySuite AI."
echo "Start ComfyUI with --enable-cors-header so the panel can reach it."
