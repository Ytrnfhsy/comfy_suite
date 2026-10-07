# Remembers the ComfyUI server for the ComfySuite AI panel (see photosuite-plugin/comfy_suite/set-server.ps1).
# Usage: powershell -ExecutionPolicy Bypass -File set-server.ps1 http://100.64.0.5:8188
param([Parameter(Mandatory = $true)][string]$Url)
& (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "photosuite-plugin\comfy_suite\set-server.ps1") $Url
