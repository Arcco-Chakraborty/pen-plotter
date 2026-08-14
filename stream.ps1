# Stream a G-code file to grbl.
#
#   .\stream.ps1 -File .\testplot.nc
#   .\stream.ps1 -File .\out.gcode -Port COM11
#   .\stream.ps1 -File .\testplot.nc -DryRun     # motion only, pen commands stripped
#
# Opens WITHOUT resetting the board, so the position you jogged to is preserved -
# this machine has no homing, so a reset would silently move the origin.
#
# Waits for grbl's "ok" on every line before sending the next, and aborts on the
# first error or alarm instead of continuing to push a broken job at the machine.
param(
    [Parameter(Mandatory=$true)][string]$File,
    [string]$Port = "COM11",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $File)) { throw "No such file: $File" }

$lines = Get-Content $File | ForEach-Object {
    $t = $_.Trim()
    if ($t -match '^\(') { return }        # whole-line ( comment )
    if (-not $t) { return }
    if ($DryRun -and $t -match '^(M3|M5|G4)') { return }   # strip pen + dwell
    $t
} | Where-Object { $_ }

Write-Host "streaming $($lines.Count) lines from $File" -ForegroundColor Cyan
if ($DryRun) { Write-Host "DRY RUN - pen commands stripped, motion only" -ForegroundColor Yellow }

$sp = New-Object System.IO.Ports.SerialPort $Port, 115200, ([System.IO.Ports.Parity]::None), 8, ([System.IO.Ports.StopBits]::One)
$sp.NewLine = "`n"; $sp.ReadTimeout = 500
$sp.DtrEnable = $false; $sp.RtsEnable = $false

function Wait-Ok([int]$timeoutMs = 120000) {
    $buf = ""
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt $timeoutMs) {
        if ($sp.BytesToRead -gt 0) { $buf += $sp.ReadExisting() }
        if ($buf -match 'ok') { return @($true, $buf.Trim()) }
        if ($buf -match '(error:\d+|ALARM:\d+)') { return @($false, $Matches[1]) }
        Start-Sleep -Milliseconds 10
    }
    return @($false, "timeout")
}

try {
    $sp.Open(); Start-Sleep -Milliseconds 200
    $sp.DiscardInBuffer()

    $n = 0
    foreach ($l in $lines) {
        $n++
        $sp.WriteLine($l)
        $r = Wait-Ok
        if (-not $r[0]) {
            Write-Host ""
            Write-Host "ABORTED at line $n : $l" -ForegroundColor Red
            Write-Host "  grbl said: $($r[1])" -ForegroundColor Red
            $sp.Write([char]0x18)   # soft reset to stop motion
            return
        }
        Write-Host ("  [{0,3}/{1}] {2}" -f $n, $lines.Count, $l) -ForegroundColor Gray
    }

    # Lines are acknowledged when parsed, not when the motion finishes - so wait
    # for the planner to actually drain before declaring the job done.
    Write-Host "all lines sent, waiting for motion to finish..." -ForegroundColor Cyan
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt 300000) {
        $sp.DiscardInBuffer(); $sp.Write("?")
        Start-Sleep -Milliseconds 250
        $s = $sp.ReadExisting()
        if ($s -match '<Idle') { Write-Host "DONE - machine idle" -ForegroundColor Green; break }
        if ($s -match '<Alarm') { Write-Host "ALARM during run: $s" -ForegroundColor Red; break }
    }
}
finally {
    if ($sp.IsOpen) { $sp.Close() }
    $sp.Dispose()
}
