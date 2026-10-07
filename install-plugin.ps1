# Installs the ComfySuite AI sidebar plugin into PhotoSuite 0.9.14–0.9.15 (Windows).
# Usage: powershell -ExecutionPolicy Bypass -File install-plugin.ps1
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$plugins = Join-Path $env:APPDATA "app.photosuite\plugins"
$target = Join-Path $plugins "comfy_suite"

if ($here.StartsWith($plugins, [System.StringComparison]::OrdinalIgnoreCase)) {
  Write-Host "This checkout is already inside PhotoSuite's plugins folder: restart PhotoSuite and open Window > ComfySuite AI."
  exit 0
}
New-Item -ItemType Directory -Force -Path $plugins | Out-Null
if (Test-Path $target) {
  # Outside the plugins folder, so PhotoSuite never loads the old copy as a duplicate.
  $backup = (Join-Path (Split-Path -Parent $plugins) "comfy_suite.backup-") + (Get-Date -Format "yyyyMMdd-HHmmss")
  Write-Host "Moving the existing $target to $backup"
  Move-Item $target $backup
}
Copy-Item -Recurse (Join-Path $here "photosuite-plugin\comfy_suite") $target
Write-Host "Installed to $target"
Write-Host "Restart PhotoSuite and open Window > ComfySuite AI."
Write-Host "Start ComfyUI with --enable-cors-header so the panel can reach it."
