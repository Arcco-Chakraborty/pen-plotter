# Sweep the pen servo so you can read off the up/down S values.
#
#   .\pen-tune.ps1                    # sweep S=0..1000 in steps of 100
#   .\pen-tune.ps1 -From 550 -To 750 -Step 25 -Dwell 1500
#   .\pen-tune.ps1 -Set 640           # hold one value so you can measure/adjust
#
# S maps linearly onto the servo pulse: S0 = 1.0 ms, S500 = 1.5 ms, S1000 = 2.0 ms.
# Watch the pen arm and note the two S values you want, then put them in
# GRBL-Plotter (Setup -> Pen up/down) and in pen.toml.
#
# SAFETY: if the servo buzzes, strains, or gets warm at an extreme, it is pushing
# against its mechanical stop. Back off immediately - a stalled servo pulls >700 mA
# and will brown out the Mega.
param(
    [string]$Port = "COM11",
    [int]$From = 0,
    [int]$To = 1000,
    [int]$Step = 100,
    [int]$Dwell = 1200,
    [int]$Set = -1
)

$ErrorActionPreference = "Stop"
$grbl = Join-Path $PSScriptRoot "grbl.ps1"

if ($Set -ge 0) {
    Write-Host "Holding S$Set  (pulse = $([math]::Round(1.0 + $Set/1000.0, 3)) ms)" -ForegroundColor Yellow
    & $grbl -Port $Port -Command "M3 S$Set" -NoReset
    return
}

$seq = @()
for ($s = $From; $s -le $To; $s += $Step) { $seq += $s }

Write-Host "Sweeping S$From -> S$To  (step $Step, $Dwell ms hold)" -ForegroundColor Cyan
Write-Host "Watch the pen. Note the S value at 'clearly lifted' and at 'touching paper'.`n"

foreach ($s in $seq) {
    $ms = [math]::Round(1.0 + $s / 1000.0, 3)
    Write-Host ("  S{0,-5} -> {1} ms pulse" -f $s, $ms) -ForegroundColor Yellow
    & $grbl -Port $Port -Command "M3 S$s" -NoReset | Out-Null
    Start-Sleep -Milliseconds $Dwell
}

Write-Host "`nParking pen up (M5)." -ForegroundColor Green
& $grbl -Port $Port -Command 'M5' -NoReset | Out-Null
