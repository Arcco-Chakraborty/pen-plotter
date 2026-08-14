# Fit any SVG to portrait A4 (210 x 297 mm), auto-orienting so the artwork's long
# side runs along Y.
#
#   .\a4prep.ps1 -In .\SVG\drawing.svg                  # -> .\SVG\drawing_a4.svg
#   .\a4prep.ps1 -In .\SVG\drawing.svg -Margin 5        # tighter border
#   .\a4prep.ps1 -In .\SVG\*.svg                        # batch
#
# Feed the OUTPUT to GRBL-Plotter. Leave GRBL-Plotter's "SVG resize" OFF - the
# geometry is already at final A4 size here, and a second rescale there would
# undo the fit.
#
# Why the explicit rotate: vpype's `layout` enforces a portrait PAGE but never
# rotates the GEOMETRY, so landscape art would fit to the 190 mm width and leave
# most of the sheet empty. We measure the bounding box and rotate 90 deg first.
param(
    [Parameter(Mandatory=$true)][string]$In,
    [string]$Out,
    [double]$Margin = 10,
    [switch]$NoRotate
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py   = Join-Path $root ".venv\Scripts\python.exe"
$vp   = Join-Path $root ".venv\Scripts\vpype.exe"

if (-not (Test-Path $vp)) { throw "vpype not found at $vp - run: python -m venv .venv; .venv\Scripts\python -m pip install vpype" }

# vpype reports bounds in px; only the ratio matters here, so no unit conversion.
# Deliberately quote-free: PowerShell strips double quotes out of a native
# command's arguments, which corrupts any f-string passed to `python -c`.
# Exit 2 signals an empty document.
$bboxProbe = @'
import sys
from vpype import read_multilayer_svg
doc = read_multilayer_svg(sys.argv[1], quantization=0.1)
b = doc.bounds()
if b is None:
    sys.exit(2)
print(b[2]-b[0], b[3]-b[1])
'@

foreach ($file in (Get-ChildItem -Path $In)) {
    $src = $file.FullName
    $dst = if ($Out) { $Out } else { Join-Path $file.DirectoryName ($file.BaseName + "_a4" + $file.Extension) }

    $dims = & $py -c $bboxProbe $src
    if ($LASTEXITCODE -eq 2) { Write-Host "SKIP  $($file.Name) - no geometry" -ForegroundColor Yellow; continue }
    if ($LASTEXITCODE -ne 0) { throw "bbox probe failed on $($file.Name)" }

    $w, $h = $dims -split '\s+' | ForEach-Object { [double]$_ }

    # Landscape artwork gets turned upright so its long side lands on Y.
    $rotate = ($w -gt $h) -and (-not $NoRotate)

    $args = @("read", $src)
    if ($rotate) { $args += @("rotate", "90") }
    $args += @("layout", "-m", "$($Margin)mm", "a4", "write", $dst)

    & $vp @args
    if ($LASTEXITCODE -ne 0) { throw "vpype failed on $($file.Name)" }

    $orient = if ($rotate) { "rotated 90 (was landscape)" } else { "already portrait" }
    Write-Host ("OK    {0} -> {1}  [{2:N1} x {3:N1} src, {4}]" -f $file.Name, (Split-Path $dst -Leaf), $w, $h, $orient) -ForegroundColor Green
}
