# Launch the pen plotter console.
#
#   .\plot.ps1
#   .\plot.ps1 -Console      # keep this window, so Python tracebacks are visible
#
# Everything runs from .\.venv - nothing is installed system-wide. First run
# creates the venv and installs what is missing.
param([switch]$Console)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venv = Join-Path $root ".venv"
$py   = Join-Path $venv "Scripts\python.exe"
$pyw  = Join-Path $venv "Scripts\pythonw.exe"

if (-not (Test-Path $py)) {
    Write-Host "creating .venv ..." -ForegroundColor Cyan
    python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "could not create the venv - is python on PATH?" }
}

# vpype does the SVG flattening and path optimisation; pyserial talks to grbl.
& $py -c "import serial, vpype, numpy, shapely" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "installing dependencies (one time) ..." -ForegroundColor Cyan
    & $py -m pip install --quiet --upgrade pip
    & $py -m pip install --quiet pyserial vpype
    if ($LASTEXITCODE -ne 0) { throw "dependency install failed" }
}

if ($Console -or -not (Test-Path $pyw)) {
    & $py -m plotter $root
} else {
    & $pyw -m plotter $root
}
