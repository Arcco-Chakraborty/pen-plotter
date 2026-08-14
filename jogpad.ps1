# Jog pendant for the pen plotter. Buttons, a live position readout, and a
# steps/mm calibrator.
#
#   .\jogpad.ps1
#   .\jogpad.ps1 -Port COM11
#
# Connect does NOT reset the board (DTR/RTS held deasserted), so machine position
# survives across connects and a calibration in progress is not destroyed.
param([string]$Port = "COM11")

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$script:sp     = $null
$script:buf    = ""
$script:lastJog = 0.0
$script:posX   = 0.0
$script:posY   = 0.0
$script:suppressInv = $true   # stops the invert boxes writing $3 while we populate them

# ---------------------------------------------------------------- serial helpers
function Log([string]$msg, [string]$kind = "info") {
    $c = switch ($kind) { "err" { "Red" } "tx" { "DeepSkyBlue" } "ok" { "LightGreen" } default { "Gainsboro" } }
    $script:txtLog.SelectionStart  = $script:txtLog.TextLength
    $script:txtLog.SelectionColor  = [System.Drawing.Color]::FromName($c)
    $script:txtLog.AppendText("$msg`r`n")
    $script:txtLog.ScrollToCaret()
}

function Is-Connected { return ($script:sp -ne $null -and $script:sp.IsOpen) }

function Send-Cmd([string]$cmd) {
    if (-not (Is-Connected)) { Log "not connected" "err"; return }
    try { $script:sp.WriteLine($cmd); Log ">> $cmd" "tx" }
    catch { Log "write failed: $($_.Exception.Message)" "err" }
}

# Blocking read used only for $$ - the poll timer is paused around it so status
# reports do not get shuffled into the settings dump.
function Query([string]$cmd, [int]$ms = 700) {
    if (-not (Is-Connected)) { return "" }
    $script:timer.Stop()
    try {
        $script:sp.DiscardInBuffer()
        $script:sp.WriteLine($cmd)
        $out = ""
        $sw = [Diagnostics.Stopwatch]::StartNew()
        while ($sw.ElapsedMilliseconds -lt $ms) {
            if ($script:sp.BytesToRead -gt 0) { $out += $script:sp.ReadExisting() }
            Start-Sleep -Milliseconds 20
        }
        return $out
    } finally { $script:timer.Start() }
}

function Do-Connect {
    if (Is-Connected) {
        $script:timer.Stop()
        try { $script:sp.Close(); $script:sp.Dispose() } catch {}
        $script:sp = $null
        $script:btnConn.Text = "Connect"
        $script:lblState.Text = "disconnected"
        $script:lblState.ForeColor = [System.Drawing.Color]::Gray
        Log "disconnected"
        return
    }
    $p = $script:cboPort.Text
    try {
        $script:sp = New-Object System.IO.Ports.SerialPort $p, 115200, ([System.IO.Ports.Parity]::None), 8, ([System.IO.Ports.StopBits]::One)
        $script:sp.NewLine     = "`n"
        $script:sp.ReadTimeout = 300
        $script:sp.DtrEnable   = $false   # must precede Open() or the Mega resets
        $script:sp.RtsEnable   = $false
        $script:sp.Open()
        $script:btnConn.Text = "Disconnect"
        Log "connected on $p (no reset)" "ok"
        $script:timer.Start()
        Read-Steps
    } catch {
        Log "open $p failed: $($_.Exception.Message)" "err"
        $script:sp = $null
    }
}

# ---------------------------------------------------------------- status polling
function Tick {
    if (-not (Is-Connected)) { return }
    try {
        $script:sp.Write("?")
        if ($script:sp.BytesToRead -gt 0) { $script:buf += $script:sp.ReadExisting() }
    } catch { return }

    while ($script:buf -match '<([^>]*)>') {
        $rep = $Matches[1]
        $script:buf = $script:buf.Substring($script:buf.IndexOf('>') + 1)
        if ($rep -match '^(\w+)') {
            $st = $Matches[1]
            $script:lblState.Text = $st
            $script:lblState.ForeColor = switch ($st) {
                "Idle"  { [System.Drawing.Color]::FromArgb(60,160,60) }
                "Jog"   { [System.Drawing.Color]::FromArgb(30,110,200) }
                "Run"   { [System.Drawing.Color]::FromArgb(30,110,200) }
                "Alarm" { [System.Drawing.Color]::FromArgb(200,40,40) }
                default { [System.Drawing.Color]::DimGray }
            }
        }
        if ($rep -match 'MPos:([-0-9.]+),([-0-9.]+),([-0-9.]+)') {
            $script:posX = [double]$Matches[1]
            $script:posY = [double]$Matches[2]
            $script:lblX.Text = "{0,10:N3}" -f $script:posX
            $script:lblY.Text = "{0,10:N3}" -f $script:posY
        }
    }
    # Anything that is not a status report is a real reply worth showing.
    if ($script:buf -match "`n") {
        $parts = $script:buf -split "`r?`n"
        $script:buf = $parts[-1]
        foreach ($l in $parts[0..($parts.Count-2)]) {
            $t = $l.Trim()
            if ($t -and $t -ne "ok") { Log "   $t" $(if ($t -match '^error|^ALARM') { "err" } else { "info" }) }
        }
    }
}

# ---------------------------------------------------------------- actions
function Do-Jog([string]$axis, [int]$sign) {
    $step = [double]$script:cboStep.Text
    $feed = [int]$script:txtFeed.Text
    $d = $step * $sign

    # Travel guard. grbl's own soft limits ($20) need homing, which needs endstops
    # this machine does not have yet - so nothing on the controller prevents a jog
    # from driving into the frame. This is the stand-in until endstops are fitted.
    if ($script:chkLimit.Checked) {
        $box = if ($axis -eq 'X') { $script:txtLimX } else { $script:txtLimY }
        $max = 0.0
        if ([double]::TryParse($box.Text, [ref]$max) -and $max -gt 0) {
            $cur = if ($axis -eq 'X') { $script:posX } else { $script:posY }
            $target = $cur + $d
            if ($target -lt -0.001 -or $target -gt $max) {
                Log ("BLOCKED: $axis would reach {0:N2} mm, outside 0..{1:N0}" -f $target, $max) "err"
                Log "   untick Limit to override, or raise the figure." "err"
                return
            }
        }
    }

    $script:lastJog = [math]::Abs($d)
    $script:txtCmd.Text = "{0:N3}" -f $script:lastJog
    Send-Cmd ("`$J=G91 $axis$("{0:N3}" -f $d) F$feed")
}

function Read-Steps {
    $dump = Query '$$' 800
    if ($dump -match '\$100=([0-9.]+)') { $script:txtSX.Text = $Matches[1] }
    if ($dump -match '\$101=([0-9.]+)') { $script:txtSY.Text = $Matches[1] }
    if ($dump -match '\$3=(\d+)') {
        $m = [int]$Matches[1]
        $script:suppressInv = $true
        $script:chkInvX.Checked = (($m -band 1) -ne 0)
        $script:chkInvY.Checked = (($m -band 2) -ne 0)
        $script:suppressInv = $false
        Log "read `$3=$m (invert X=$($script:chkInvX.Checked) Y=$($script:chkInvY.Checked))" "ok"
    }
    Log "read steps/mm: X=$($script:txtSX.Text) Y=$($script:txtSY.Text)" "ok"
}

# Writes $3 from the two checkboxes. Direction must be re-checked whenever motor
# wiring changes - a mask measured with crossed cables applies to the wrong axis.
function Apply-Invert {
    if ($script:suppressInv) { return }
    $m = 0
    if ($script:chkInvX.Checked) { $m += 1 }
    if ($script:chkInvY.Checked) { $m += 2 }
    Send-Cmd "`$3=$m"
    Log "direction mask -> `$3=$m" "ok"
}

function Apply-Steps {
    Send-Cmd ("`$100=" + $script:txtSX.Text); Start-Sleep -Milliseconds 120
    Send-Cmd ("`$101=" + $script:txtSY.Text); Start-Sleep -Milliseconds 120
    Log "applied. Update grbl-settings.txt too, or a re-push will undo it." "ok"
}

function Do-Calibrate {
    $cmdMm = 0.0; $measMm = 0.0
    if (-not [double]::TryParse($script:txtCmd.Text,  [ref]$cmdMm)  -or $cmdMm  -le 0) { Log "commanded must be > 0" "err"; return }
    if (-not [double]::TryParse($script:txtMeas.Text, [ref]$measMm) -or $measMm -le 0) { Log "measured must be > 0" "err"; return }

    $axis = $script:cboAxis.Text
    $box  = if ($axis -eq 'X') { $script:txtSX } else { $script:txtSY }
    $cur  = [double]$box.Text
    $new  = $cur * ($cmdMm / $measMm)
    $ratio = $measMm / $cmdMm

    Log ("$axis  $cur -> {0:N3} steps/mm  (ratio {1:N4}x)" -f $new, $ratio) "ok"
    foreach ($p in 0.25, 0.5, 2.0, 4.0) {
        if ([math]::Abs($ratio - $p) -lt 0.06) {
            Log ("   ~{0}x = microstepping mismatch, not belt slip." -f $p) "err"
            Log ("   Prefer the clean value {0}." -f [math]::Round($new)) "err"
        }
    }
    $box.Text = "{0:N3}" -f $new
    Log "   value staged - press 'Apply steps/mm' to write it." "info"
}

function Pen-Send { Send-Cmd ("M3 S" + [int]$script:trkPen.Value) }

# ---------------------------------------------------------------- UI
$form = New-Object System.Windows.Forms.Form
$form.Text = "Pen Plotter Jog Pad"
$form.Size = New-Object System.Drawing.Size(560, 720)
$form.StartPosition = "CenterScreen"
$form.BackColor = [System.Drawing.Color]::FromArgb(38, 38, 42)
$form.ForeColor = [System.Drawing.Color]::Gainsboro
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9)

function New-Btn($text, $x, $y, $w, $h, $onClick) {
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $text; $b.Location = New-Object System.Drawing.Point($x, $y)
    $b.Size = New-Object System.Drawing.Size($w, $h)
    $b.FlatStyle = "Flat"; $b.BackColor = [System.Drawing.Color]::FromArgb(58,58,64)
    $b.ForeColor = [System.Drawing.Color]::Gainsboro
    $b.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(90,90,96)
    $b.Add_Click($onClick); $form.Controls.Add($b); return $b
}
function New-Lbl($text, $x, $y, $w) {
    $l = New-Object System.Windows.Forms.Label
    $l.Text = $text; $l.Location = New-Object System.Drawing.Point($x, $y)
    $l.Size = New-Object System.Drawing.Size($w, 20); $l.ForeColor = [System.Drawing.Color]::Silver
    $form.Controls.Add($l); return $l
}
function New-Txt($text, $x, $y, $w) {
    $t = New-Object System.Windows.Forms.TextBox
    $t.Text = $text; $t.Location = New-Object System.Drawing.Point($x, $y)
    $t.Size = New-Object System.Drawing.Size($w, 22)
    $t.BackColor = [System.Drawing.Color]::FromArgb(28,28,32); $t.ForeColor = [System.Drawing.Color]::White
    $t.BorderStyle = "FixedSingle"; $form.Controls.Add($t); return $t
}

# --- connection row
New-Lbl "Port" 14 16 34 | Out-Null
$script:cboPort = New-Object System.Windows.Forms.ComboBox
$script:cboPort.Location = New-Object System.Drawing.Point(50, 13)
$script:cboPort.Size = New-Object System.Drawing.Size(90, 22)
$script:cboPort.DropDownStyle = "DropDown"
[System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object | ForEach-Object { $script:cboPort.Items.Add($_) | Out-Null }
$script:cboPort.Text = $Port
$form.Controls.Add($script:cboPort)
$script:btnConn = New-Btn "Connect" 148 12 90 26 { Do-Connect }
$script:lblState = New-Lbl "disconnected" 252 16 140
$script:lblState.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)

# --- DRO
$dro = New-Object System.Windows.Forms.Panel
$dro.Location = New-Object System.Drawing.Point(14, 46)
$dro.Size = New-Object System.Drawing.Size(510, 68)
$dro.BackColor = [System.Drawing.Color]::FromArgb(24,24,28)
$form.Controls.Add($dro)
$mono = New-Object System.Drawing.Font("Consolas", 22, [System.Drawing.FontStyle]::Bold)
$lx = New-Object System.Windows.Forms.Label; $lx.Text="X"; $lx.Location=New-Object System.Drawing.Point(16,18); $lx.Size=New-Object System.Drawing.Size(28,34); $lx.Font=$mono; $lx.ForeColor=[System.Drawing.Color]::DimGray; $dro.Controls.Add($lx)
$script:lblX = New-Object System.Windows.Forms.Label
$script:lblX.Text="0.000"; $script:lblX.Location=New-Object System.Drawing.Point(44,18); $script:lblX.Size=New-Object System.Drawing.Size(180,34)
$script:lblX.Font=$mono; $script:lblX.ForeColor=[System.Drawing.Color]::FromArgb(120,220,140); $script:lblX.TextAlign="MiddleRight"; $dro.Controls.Add($script:lblX)
$ly = New-Object System.Windows.Forms.Label; $ly.Text="Y"; $ly.Location=New-Object System.Drawing.Point(262,18); $ly.Size=New-Object System.Drawing.Size(28,34); $ly.Font=$mono; $ly.ForeColor=[System.Drawing.Color]::DimGray; $dro.Controls.Add($ly)
$script:lblY = New-Object System.Windows.Forms.Label
$script:lblY.Text="0.000"; $script:lblY.Location=New-Object System.Drawing.Point(290,18); $script:lblY.Size=New-Object System.Drawing.Size(180,34)
$script:lblY.Font=$mono; $script:lblY.ForeColor=[System.Drawing.Color]::FromArgb(120,220,140); $script:lblY.TextAlign="MiddleRight"; $dro.Controls.Add($script:lblY)

# --- step + feed
New-Lbl "Step (mm)" 14 128 70 | Out-Null
$script:cboStep = New-Object System.Windows.Forms.ComboBox
$script:cboStep.Location = New-Object System.Drawing.Point(88, 125)
$script:cboStep.Size = New-Object System.Drawing.Size(80, 22)
$script:cboStep.DropDownStyle = "DropDownList"
"0.1","1","5","10","25","50","100","200" | ForEach-Object { $script:cboStep.Items.Add($_) | Out-Null }
$script:cboStep.SelectedIndex = 3
$form.Controls.Add($script:cboStep)
New-Lbl "Feed" 190 128 34 | Out-Null
$script:txtFeed = New-Txt "300" 228 125 70

# Travel guard - blocks jogs that would leave 0..limit, per axis. A single figure
# cannot express a sheet: A4 portrait is 210 x 297, so X and Y need separate limits.
$script:chkLimit = New-Object System.Windows.Forms.CheckBox
$script:chkLimit.Text = "Limit"
$script:chkLimit.Location = New-Object System.Drawing.Point(316, 126)
$script:chkLimit.Size = New-Object System.Drawing.Size(58, 22)
$script:chkLimit.Checked = $true
$script:chkLimit.ForeColor = [System.Drawing.Color]::FromArgb(230,190,110)
$form.Controls.Add($script:chkLimit)
$script:txtLimX = New-Txt "210" 374 125 42
New-Lbl "x" 421 128 12 | Out-Null
$script:txtLimY = New-Txt "297" 434 125 42
New-Btn "A4" 482 124 38 24 {
    $script:txtLimX.Text = "210"; $script:txtLimY.Text = "297"
    Log "limits -> A4 portrait, 210 x 297 mm" "ok"
} | Out-Null

# --- jog pad
$jx = 40; $jy = 160; $bw = 74; $bh = 40
New-Btn "Y+"  ($jx+$bw+6) $jy $bw $bh          { Do-Jog "Y"  1 } | Out-Null
New-Btn "X-"  $jx ($jy+$bh+6) $bw $bh          { Do-Jog "X" -1 } | Out-Null
New-Btn "0"   ($jx+$bw+6) ($jy+$bh+6) $bw $bh  { Send-Cmd 'G10 L20 P1 X0 Y0 Z0'; Log "work origin zeroed" "ok" } | Out-Null
New-Btn "X+"  ($jx+2*($bw+6)) ($jy+$bh+6) $bw $bh { Do-Jog "X"  1 } | Out-Null
New-Btn "Y-"  ($jx+$bw+6) ($jy+2*($bh+6)) $bw $bh { Do-Jog "Y" -1 } | Out-Null

New-Btn "Unlock (`$X)" 300 160 100 34 { Send-Cmd '$X' } | Out-Null
$btnStop = New-Btn "STOP" 300 200 100 40 {
    if (Is-Connected) { $script:sp.Write([char]0x85); Start-Sleep -Milliseconds 80; $script:sp.Write([char]0x18); Log "jog cancelled + soft reset" "err" }
}
$btnStop.BackColor = [System.Drawing.Color]::FromArgb(150,40,40)
New-Btn "Home (`$H)" 300 246 100 34 { Send-Cmd '$H' } | Out-Null

# Direction inversion - flip an axis without hunting for the $3 bitmask.
$script:chkInvX = New-Object System.Windows.Forms.CheckBox
$script:chkInvX.Text = "Invert X"; $script:chkInvX.Location = New-Object System.Drawing.Point(415, 163)
$script:chkInvX.Size = New-Object System.Drawing.Size(90, 22)
$script:chkInvX.ForeColor = [System.Drawing.Color]::FromArgb(230,190,110)
$script:chkInvX.Add_CheckedChanged({ Apply-Invert })
$form.Controls.Add($script:chkInvX)

$script:chkInvY = New-Object System.Windows.Forms.CheckBox
$script:chkInvY.Text = "Invert Y"; $script:chkInvY.Location = New-Object System.Drawing.Point(415, 189)
$script:chkInvY.Size = New-Object System.Drawing.Size(90, 22)
$script:chkInvY.ForeColor = [System.Drawing.Color]::FromArgb(230,190,110)
$script:chkInvY.Add_CheckedChanged({ Apply-Invert })
$form.Controls.Add($script:chkInvY)

# --- steps/mm
New-Lbl "steps/mm" 14 300 70 | Out-Null
New-Lbl "X" 90 300 14 | Out-Null;  $script:txtSX = New-Txt "160" 106 297 70
New-Lbl "Y" 186 300 14 | Out-Null; $script:txtSY = New-Txt "160" 202 297 70
New-Btn "Read" 284 295 66 26 { Read-Steps } | Out-Null
New-Btn "Apply steps/mm" 356 295 130 26 { Apply-Steps } | Out-Null

# --- calibrator
$grp = New-Object System.Windows.Forms.Label
$grp.Text = "Calibrate:  jog a known distance, measure it, press Compute"
$grp.Location = New-Object System.Drawing.Point(14, 336); $grp.Size = New-Object System.Drawing.Size(500, 20)
$grp.ForeColor = [System.Drawing.Color]::FromArgb(230,190,110)
$form.Controls.Add($grp)
New-Lbl "Axis" 14 364 34 | Out-Null
$script:cboAxis = New-Object System.Windows.Forms.ComboBox
$script:cboAxis.Location = New-Object System.Drawing.Point(52, 361); $script:cboAxis.Size = New-Object System.Drawing.Size(50, 22)
$script:cboAxis.DropDownStyle = "DropDownList"; "X","Y" | ForEach-Object { $script:cboAxis.Items.Add($_) | Out-Null }
$script:cboAxis.SelectedIndex = 0; $form.Controls.Add($script:cboAxis)
New-Lbl "Commanded" 116 364 76 | Out-Null; $script:txtCmd  = New-Txt "10" 194 361 60
New-Lbl "Measured"  266 364 66 | Out-Null; $script:txtMeas = New-Txt ""   334 361 60
New-Btn "Compute" 404 359 82 26 { Do-Calibrate } | Out-Null

# --- pen
New-Lbl "Pen  S" 14 404 50 | Out-Null
$script:trkPen = New-Object System.Windows.Forms.TrackBar
$script:trkPen.Location = New-Object System.Drawing.Point(66, 398)
$script:trkPen.Size = New-Object System.Drawing.Size(260, 40)
$script:trkPen.Minimum = 0; $script:trkPen.Maximum = 1000; $script:trkPen.TickFrequency = 100
$script:trkPen.Value = 1000
$form.Controls.Add($script:trkPen)
$script:lblPen = New-Lbl "S1000  (2.00 ms)" 332 404 130
$script:trkPen.Add_ValueChanged({
    $v = [int]$script:trkPen.Value
    $script:lblPen.Text = "S$v  ({0:N2} ms)" -f (1.0 + $v/1000.0)
})
$script:trkPen.Add_MouseUp({ Pen-Send })      # send on release, not on every pixel
$script:trkPen.Add_KeyUp({ Pen-Send })
New-Btn "Send S" 14 442 70 26 { Pen-Send } | Out-Null
New-Btn "Pen UP (M5)" 92 442 110 26 { Send-Cmd 'M5' } | Out-Null
New-Btn "Pen DOWN" 210 442 110 26 { Send-Cmd ("M3 S" + [int]$script:trkPen.Value) } | Out-Null

# --- log
$script:txtLog = New-Object System.Windows.Forms.RichTextBox
$script:txtLog.Location = New-Object System.Drawing.Point(14, 482)
$script:txtLog.Size = New-Object System.Drawing.Size(510, 180)
$script:txtLog.BackColor = [System.Drawing.Color]::FromArgb(20,20,24)
$script:txtLog.ForeColor = [System.Drawing.Color]::Gainsboro
$script:txtLog.Font = New-Object System.Drawing.Font("Consolas", 9)
$script:txtLog.ReadOnly = $true; $script:txtLog.BorderStyle = "FixedSingle"
$form.Controls.Add($script:txtLog)

# --- poll timer
$script:timer = New-Object System.Windows.Forms.Timer
$script:timer.Interval = 250
$script:timer.Add_Tick({ Tick })

$form.Add_Shown({ Do-Connect })
$form.Add_FormClosing({
    $script:timer.Stop()
    if (Is-Connected) { try { $script:sp.Close(); $script:sp.Dispose() } catch {} }
})

[void]$form.ShowDialog()
