# Jog the plotter and read back position.
#
#   .\jog.ps1 -Status                     # just show state + position
#   .\jog.ps1 -Move 'X10'                 # relative move, mm
#   .\jog.ps1 -Move 'X-10'
#   .\jog.ps1 -Move 'X50','Y50','X-50','Y-50'   # a square, for a first shakedown
#   .\jog.ps1 -Move 'X50' -Feed 400       # slower
#   .\jog.ps1 -Zero                       # set current position as 0,0
#   .\jog.ps1 -Stop                       # cancel jog + soft reset
#
# Uses grbl's $J= jog command, not G0/G1: jogs do not alter modal state and can be
# cancelled instantly. The port is opened with DTR/RTS deasserted so the Mega is NOT
# reset - a reset would zero MPos and ruin a calibration in progress.
param(
    [string[]]$Move,
    [int]$Feed = 800,
    [string]$Port = "COM11",
    [switch]$Status,
    [switch]$Zero,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"

$sp = New-Object System.IO.Ports.SerialPort $Port, 115200, ([System.IO.Ports.Parity]::None), 8, ([System.IO.Ports.StopBits]::One)
$sp.NewLine     = "`n"
$sp.ReadTimeout = 500
$sp.DtrEnable   = $false   # must be set before Open() or the board resets
$sp.RtsEnable   = $false

function Send-Line([string]$cmd) {
    $sp.DiscardInBuffer()
    $sp.WriteLine($cmd)
    $buf = ""
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt 3000) {
        if ($sp.BytesToRead -gt 0) { $buf += $sp.ReadExisting() }
        if ($buf -match 'ok\s*$' -or $buf -match 'error:\d+' -or $buf -match 'ALARM') { break }
        Start-Sleep -Milliseconds 15
    }
    return $buf.Trim()
}

function Get-State {
    $sp.DiscardInBuffer()
    $sp.Write("?")
    $buf = ""
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt 1500) {
        if ($sp.BytesToRead -gt 0) { $buf += $sp.ReadExisting() }
        if ($buf -match '<[^>]+>') { return $Matches[0] }
        Start-Sleep -Milliseconds 15
    }
    return "<no response>"
}

function Wait-Idle([int]$timeoutMs = 120000) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt $timeoutMs) {
        $s = Get-State
        if ($s -match '^<Idle') { return $s }
        if ($s -match '^<Alarm') { Write-Host "  !! $s" -ForegroundColor Red; return $s }
        Start-Sleep -Milliseconds 120
    }
    Write-Host "  !! timed out waiting for Idle" -ForegroundColor Red
    return (Get-State)
}

function Show-Pos([string]$s) {
    if ($s -match 'MPos:([-0-9.]+),([-0-9.]+),([-0-9.]+)') {
        Write-Host ("  X={0,9}  Y={1,9}" -f $Matches[1], $Matches[2]) -ForegroundColor Green
    } else {
        Write-Host "  $s" -ForegroundColor Gray
    }
}

try {
    $sp.Open()
    Start-Sleep -Milliseconds 150

    if ($Stop) {
        $sp.Write([char]0x85)            # jog cancel
        Start-Sleep -Milliseconds 100
        $sp.Write([char]0x18)            # soft reset (Ctrl-X)
        Start-Sleep -Milliseconds 1500
        Write-Host "Jog cancelled, grbl soft-reset. Position is now zeroed." -ForegroundColor Yellow
        return
    }

    $st = Get-State
    if ($st -match '^<Alarm') {
        Write-Host "grbl is in ALARM - clearing with `$X" -ForegroundColor Yellow
        Send-Line '$X' | Out-Null
        $st = Get-State
    }

    if ($Status -or (-not $Move -and -not $Zero)) {
        Write-Host "State: $st" -ForegroundColor Cyan
        Show-Pos $st
        return
    }

    if ($Zero) {
        Send-Line 'G10 L20 P1 X0 Y0 Z0' | Out-Null
        Write-Host "Work origin set to current position." -ForegroundColor Green
        Show-Pos (Get-State)
        return
    }

    foreach ($m in $Move) {
        $cmd = "`$J=G91 $m F$Feed"
        Write-Host ">> $cmd" -ForegroundColor Cyan
        $resp = Send-Line $cmd
        if ($resp -match 'error:(\d+)') {
            Write-Host "  !! error:$($Matches[1]) - jog rejected" -ForegroundColor Red
            Write-Host "     (error:15 = jog exceeds machine travel; error:9 = grbl locked)" -ForegroundColor Red
            break
        }
        Show-Pos (Wait-Idle)
    }
}
finally {
    if ($sp.IsOpen) { $sp.Close() }
    $sp.Dispose()
}
