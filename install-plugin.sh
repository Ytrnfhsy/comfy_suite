#!/bin/sh
# Installs the ComfySuite AI sidebar plugin into PhotoSuite 0.9.14–0.9.15 (Linux, macOS).
# Usage: sh install-plugin.sh
set -eu

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
echo "Installed to $target"
echo "Restart PhotoSuite and open Window > ComfySuite AI."
echo "Start ComfyUI with --enable-cors-header so the panel can reach it."
