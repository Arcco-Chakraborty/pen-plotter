# Build (and optionally flash) grbl-Mega-5X pen-plotter firmware.
#   .\build.ps1            -> compile only
#   .\build.ps1 -Upload    -> compile + flash to $Port
param(
    [switch]$Upload,
    [string]$Port = "COM11"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$cli  = Join-Path $root "tools\arduino-cli\arduino-cli.exe"
$fqbn = "arduino:avr:mega:cpu=atmega2560"

$env:ARDUINO_DIRECTORIES_DATA = Join-Path $root "tools\acli-data"
$env:ARDUINO_DIRECTORIES_USER = Join-Path $root "tools\acli-user"

# arduino-cli compiles grbl as a library, so mirror the patched source into the
# sketchbook library folder on every build. Otherwise you silently flash stale code.
$lib = Join-Path $env:ARDUINO_DIRECTORIES_USER "libraries\grbl"
if (Test-Path $lib) { Remove-Item -Recurse -Force $lib }
New-Item -ItemType Directory -Force $lib | Out-Null
Copy-Item (Join-Path $root "firmware\grbl\*") $lib -Recurse -Force

$sketch = Join-Path $lib "examples\grblUpload"

Write-Host "==> Compiling ($fqbn)" -ForegroundColor Cyan
& $cli compile --fqbn $fqbn --libraries (Join-Path $env:ARDUINO_DIRECTORIES_USER "libraries") $sketch
if ($LASTEXITCODE -ne 0) { throw "Compile failed" }

if ($Upload) {
    Write-Host "==> Uploading to $Port" -ForegroundColor Cyan
    & $cli upload -p $Port --fqbn $fqbn $sketch
    if ($LASTEXITCODE -ne 0) { throw "Upload failed" }
    Write-Host "==> Flashed. Open $Port at 115200 baud." -ForegroundColor Green
}
