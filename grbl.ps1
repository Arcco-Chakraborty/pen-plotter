# Minimal grbl serial console. No pyserial / no extra installs - uses .NET SerialPort.
#
#   .\grbl.ps1 -Command '$$'                  # dump settings
#   .\grbl.ps1 -Command '$I','$G'             # several commands
#   .\grbl.ps1 -File .\grbl-settings.txt      # push a settings file
#   .\grbl.ps1 -Command 'M3 S1000' -NoReset   # talk without rebooting grbl
#
# Opening the port asserts DTR, which resets the Mega - that is how we catch the
# banner. Pass -NoReset to keep grbl's current state (needed mid-session).
param(
    [string]$Port = "COM11",
    [string[]]$Command,
    [string]$File,
    [switch]$NoReset,
    [int]$Baud = 115200
)

$ErrorActionPreference = "Stop"

$lines = @()
# Strip inline trailing comments too, not just whole-line ones - grbl rejects
# anything after the value with error:3 (unsupported statement).
if ($File) {
    $lines += Get-Content $File |
        ForEach-Object { ($_ -split '#', 2)[0].Trim() } |
        Where-Object   { $_ }
}
if ($Command) { $lines += $Command }

$sp = New-Object System.IO.Ports.SerialPort $Port, $Baud, ([System.IO.Ports.Parity]::None), 8, ([System.IO.Ports.StopBits]::One)
$sp.NewLine      = "`n"
$sp.ReadTimeout  = 500
$sp.WriteTimeout = 1000
$sp.DtrEnable    = -not $NoReset
$sp.RtsEnable    = -not $NoReset

function Read-For([int]$ms) {
    $buf = ""
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt $ms) {
        if ($sp.BytesToRead -gt 0) { $buf += $sp.ReadExisting() } else { Start-Sleep -Milliseconds 20 }
    }
    return $buf
}

try {
    $sp.Open()
    if (-not $NoReset) {
        Start-Sleep -Milliseconds 2500   # bootloader + grbl init
        $banner = Read-For 500
        if ($banner.Trim()) { Write-Host $banner.Trim() -ForegroundColor Green }
    }
    $sp.DiscardInBuffer()

    foreach ($line in $lines) {
        $cmd = $line.Trim()
        Write-Host ">> $cmd" -ForegroundColor Cyan
        $sp.WriteLine($cmd)
        $resp = Read-For 400
        foreach ($r in ($resp -split "`r?`n")) {
            if (-not $r.Trim()) { continue }
            $color = if ($r -match '^error|^ALARM') { 'Red' } else { 'Gray' }
            Write-Host "   $($r.Trim())" -ForegroundColor $color
        }
    }
}
finally {
    if ($sp.IsOpen) { $sp.Close() }
    $sp.Dispose()
}
