#!/bin/sh
# Remembers the ComfyUI server for the ComfySuite AI panel (see photosuite-plugin/comfy_suite/set-server.sh).
# Usage: sh set-server.sh http://100.64.0.5:8188
exec sh "$(cd "$(dirname "$0")" && pwd)/photosuite-plugin/comfy_suite/set-server.sh" "$@"
