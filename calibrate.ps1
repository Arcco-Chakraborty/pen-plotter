# Correct $100/$101 (steps/mm) from a measured move.
#
#   .\jog.ps1 -Move 'X50'                          # command a known distance
#   .\calibrate.ps1 -Axis X -Commanded 50 -Measured 100     # then measure and tell it
#   .\calibrate.ps1 -Axis X -Commanded 50 -Measured 100 -Apply
#
# new_steps_per_mm = current_steps_per_mm * (commanded / measured)
#
# Measure over the LONGEST distance your gantry allows - error scales with the
# measurement, so a 200 mm move gives you 4x the precision of a 50 mm one.
param(
    [Parameter(Mandatory=$true)][ValidateSet('X','Y')][string]$Axis,
    [Parameter(Mandatory=$true)][double]$Commanded,
    [Parameter(Mandatory=$true)][double]$Measured,
    [switch]$Apply,
    [string]$Port = "COM11"
)

$ErrorActionPreference = "Stop"
if ($Measured -le 0) { throw "Measured distance must be > 0" }

$setting = if ($Axis -eq 'X') { 100 } else { 101 }
$grbl = Join-Path $PSScriptRoot "grbl.ps1"

# Read the current value straight off the controller rather than trusting the file.
$dump = & $grbl -Port $Port -Command '$$' -NoReset | Out-String
if ($dump -notmatch "\`$$setting=([0-9.]+)") { throw "Could not read `$$setting from grbl" }
$current = [double]$Matches[1]

$new   = $current * ($Commanded / $Measured)
$ratio = $Measured / $Commanded

Write-Host ""
Write-Host "  axis            $Axis  (`$$setting)"
Write-Host "  current         $current steps/mm"
Write-Host "  commanded       $Commanded mm"
Write-Host "  measured        $Measured mm"
Write-Host ("  ratio           {0:N4}x" -f $ratio)
Write-Host ("  corrected       {0:N3} steps/mm" -f $new) -ForegroundColor Green

# A ratio very near a power of two almost always means the microstep jumpers are not
# what you think, rather than a genuine mechanical error.
foreach ($p in 0.25, 0.5, 2.0, 4.0) {
    if ([math]::Abs($ratio - $p) -lt 0.05) {
        Write-Host ""
        Write-Host ("  NOTE: ratio is ~{0}x - that is a microstepping mismatch, not" -f $p) -ForegroundColor Yellow
        Write-Host "        belt slip. Check the three jumpers under the driver socket." -ForegroundColor Yellow
        Write-Host ("        {0} steps/mm is a clean value; prefer it over an odd number." -f [math]::Round($new)) -ForegroundColor Yellow
    }
}
Write-Host ""

if ($Apply) {
    $val = [math]::Round($new, 3)
    Write-Host "Applying `$$setting=$val" -ForegroundColor Cyan
    & $grbl -Port $Port -Command "`$$setting=$val" -NoReset
    Write-Host "Remember to update grbl-settings.txt so a re-push does not undo this." -ForegroundColor Yellow
} else {
    Write-Host "Re-run with -Apply to write it, or set it by hand:" -ForegroundColor Gray
    Write-Host ("  .\grbl.ps1 -Command '`$$setting={0}' -NoReset" -f [math]::Round($new,3)) -ForegroundColor Gray
}
