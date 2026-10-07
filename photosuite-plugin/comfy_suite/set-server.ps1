# Remembers the ComfyUI server for the ComfySuite AI panel across PhotoSuite restarts (Windows).
# Usage: powershell -ExecutionPolicy Bypass -File set-server.ps1 http://100.64.0.5:8188
param([Parameter(Mandatory = $true)][string]$Url)
$ErrorActionPreference = "Stop"
if ($Url -notmatch '^https?://' -or $Url -match '["\\]') { Write-Error "Usage: set-server.ps1 http://HOST:8188"; exit 2 }
$Url = $Url.TrimEnd('/')
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$content = "/* Written by set-server.ps1: your settings, kept across updates. */`nObject.assign(COMFY_SUITE_CONFIG, {`n  comfyUrl: `"$Url`"`n});`n"
Set-Content -Path (Join-Path $here "config.local.js") -Value $content -Encoding UTF8
Write-Host "Saved $Url. Restart PhotoSuite (or reopen the panel) to use it."
