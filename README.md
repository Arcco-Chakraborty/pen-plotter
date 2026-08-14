# 2-Axis Pen Plotter — grbl-Mega-5X + servo pen lift

**Status: firmware built, flashed and verified on COM11. Settings written to EEPROM.**
Remaining work is physical: 12 V power, servo wiring, and two calibration numbers.

```
Grbl 1.2i  [VER:1.2i.20260521:]  [AXS:3:XYZ]  [OPT:VNMGH,35,255,0]
repo: fra589/grbl-Mega-5X  branch: edge  commit: baaa1be (2026-05-21)
```

Hardware: Arduino Mega 2560 + RAMPS 1.4, two DRV8825 StepSticks in the X and Y slots,
hobby servo for pen lift, no Z motor.

Motors: **Leadshine 42CM08** — NEMA 17, 60 mm, **1.8°** (200 steps/rev), 0.8 N·m,
2.5 A/phase, 1.0 Ω, 2.4 mH, 0.5 kg each.

> The 1.8° step angle is what the steps/mm figures below assume. **Do not set Vref from
> the 2.5 A rating** — see Vref in bring-up step 1. These are heavy 60 mm motors; if one
> rides on the gantry, drop `$120`/`$121` if you see missed steps.

---

## Files

| File | What it is |
|---|---|
| `plot.ps1` | **The plotter console** — `.\plot.ps1`. Probe the sheet, drop in an SVG or G-code, plot it. See below. |
| `plotter/` | Python package behind the console |
| `tests/` | Headless checks for the geometry, the frame maths and both importers |
| `firmware/` | grbl-Mega-5X, patched for a servo pen (all edits marked `[PEN PLOTTER]`) |
| `build.ps1` | `.\build.ps1 -Upload -Port COM11` — recompile + reflash |
| `jogpad.ps1` | Older GUI jog pendant — superseded by `plot.ps1`, kept for steps/mm calibration |
| `jog.ps1` | Command-line jog. `.\jog.ps1 -Move 'X10'`, `.\jog.ps1 -Stop` |
| `calibrate.ps1` | Fix steps/mm from a measured move |
| `grbl.ps1` | Serial console. `.\grbl.ps1 -Command '$$'` |
| `grbl-settings.txt` | The `$` settings, annotated. `.\grbl.ps1 -File .\grbl-settings.txt` |
| `pen-tune.ps1` | Sweeps the servo so you can read off pen up/down values |
| `pen.toml` | vpype-gcode profile (emits no Z words — pen is pure `M3 S`) |
| `a4prep.ps1`, `stream.ps1` | Batch SVG-to-A4 and a line-at-a-time streamer. The console does both; these stay for scripting. |
| `tools/` | Portable arduino-cli + AVR core. Nothing installed system-wide. |

---

## Two things that will destroy hardware

**1. Servo signal goes to the RAMPS Servo-2 header — pin D6. Never D8.**
D8 is a 12 V MOSFET output and stock grbl puts spindle PWM there. Already switched to D6.

**2. Do not insert or remove a stepper driver with power applied.** Kills it instantly.

## Two things that will waste your evening

**3. Servo power must come from its own 5–6 V supply**, ≥2 A, with its ground tied to a
RAMPS GND. Take *only the signal wire* from the servo header. An SG90 stalls around
600 mA and an MG996R far more; pulled through the Mega's regulator that sags the rail and
resets the board mid-plot. It looks exactly like a firmware bug. It isn't. Put ~470 µF
across the servo supply at the servo end.

**4. Motors will not move on USB alone.** The main power terminal is currently
unconnected — USB powers logic only. grbl will answer every command perfectly and report
position changing while nothing physically moves. Feed 12 V into the RAMPS main terminal.

---

## How the pen lift works

Stock grbl drives spindle PWM at a few kHz; an RC servo needs a 50 Hz frame with a
1.0–2.0 ms pulse. Rather than add a servo library, the firmware retimes the PWM that's
already there. Timer4 on the RAMPS map already runs **Fast PWM mode 14 with `ICR4` as
TOP** — a genuine 16-bit mode — so it only needed new numbers:

```
16 MHz / 8 prescaler = 2 MHz   ->  1 tick = 0.5 us
ICR4 = 40000 ticks             ->  20.0 ms period  ->  50.000 Hz exactly
pulse clamped to 2000..4000    ->  1.0 ms .. 2.0 ms
```

D6 is OC4A on **Timer4**, independent of motion. D11 is OC1A on **Timer1**, which
generates step pulses — you cannot set a 50 Hz period there without wrecking motion, so
D11 is not an option regardless of it being servo header 1.

The pen is commanded with ordinary spindle G-code, and `S` is effectively an angle:

| Command | Pulse | Meaning |
|---|---|---|
| `M3 S0` | 1.0 ms | one end of travel |
| `M3 S500` | 1.5 ms | centre |
| `M3 S1000` | 2.0 ms | other end |
| `M5` | 1.4 ms | **parks pen up** (`SERVO_PARK_US`, = `S400`) |

Resolution is 1 µs per `S` unit. Trim the endpoints via `SERVO_MIN_US` / `SERVO_MAX_US`
in `config.h` — everything else derives from them. The 1.0–2.0 ms clamp is deliberate: a
mistyped `S` cannot drive the servo into its mechanical stop and stall it.

### The fail-safe bit

Stock `spindle_stop()` clears the PWM enable bit, leaving the servo limp. On a plotter
that means `M5`, a soft reset, an ALARM, or a feed-hold-abort would **abandon the pen on
the paper and smear the drawing**. The patch keeps the 50 Hz frame alive and parks at the
pen-up pulse instead, so every stop path lifts the pen — including power-on, since
`spindle_init()` calls `spindle_stop()`.

Verified live: `M3 S0` reports `A:S` (PWM still driving), `M5` clears the flag while the
frame keeps running.

---

## Pin map (grbl-Mega-5X on RAMPS 1.4)

**Steppers** — put your two drivers in the **X and Y slots** (leftmost two with USB on the left).

| Axis | Slot | STEP | DIR | ENABLE |
|---|---|---|---|---|
| X | X | A0 | A1 | D38 |
| Y | Y | A6 | A7 | A2 |
| Z *(unused)* | Z | D46 | D48 | A8 |

**Endstops** — 3-pin headers (Signal / GND / +5 V); **check the silkscreen before plugging in.**
For plain microswitches use Signal + GND only, normally-open — grbl enables the internal pull-up.

| X-min | X-max | Y-min | Y-max | Z-min | Z-max |
|---|---|---|---|---|---|
| D3 | D2 | D14 | D15 | D18 | D19 |

**Servo header** — position 1 = D11 *(Timer1, avoid)*, **position 2 = D6 ← pen servo**,
position 3 = D5 *(spindle dir)*, position 4 = D4 *(spindle enable)*.

**Free:** D8 / D9 / D10 MOSFET outputs are unused — spare capacity for a fan later.

**Motors:** the RAMPS connector is labelled `2B 2A 1A 1B`. Do **not** trust wire colours —
ohm out the coils (partners read ~2–4 Ω, cross-coil reads open). Swapping one pair
reverses that axis; fix it in `$3`, not by rewiring.

---

## Firmware changes (all marked `[PEN PLOTTER]`, guarded by `PEN_SERVO_MODE`)

| File | Change |
|---|---|
| `config.h` | `N_AXIS` 5 → 3 |
| `config.h` | `SPINDLE_PWM_ON_D8` → `SPINDLE_PWM_ON_D6` |
| `config.h` | Added `PEN_SERVO_MODE`, `SERVO_MIN_US`, `SERVO_MAX_US`, `SERVO_PARK_US` |
| `config.h` | Homing cycle: X+Y only (was Z-first — no Z motor or Z endstop exists, so it would seek full travel then throw `ALARM:8`) |
| `cpu_map.h` | Timer4 retimed to 50 Hz RC spec (`ICR4`=40000), pulse derived from the `SERVO_*_US` constants |
| `spindle_control.c` | `spindle_stop()` + `spindle_set_speed()` park at pen-up instead of killing PWM |

`build.ps1` re-mirrors `firmware/grbl` into the arduino-cli library folder on every build,
so you can't silently flash stale code.

---

## Bring-up order

Each step isolates one failure mode. Don't skip ahead.

**1. Set driver Vref** — power on, motors *disconnected*, probe between trimpot and GND.

**DRV8825: `Vref = Imax / 2`** → target 0.7–1.0 A, so **0.35–0.50 V**.
(A4988 with 0.1 Ω sense would be `Vref = Imax × 0.8`.)

**Ignore the motor's 2.5 A rating.** A bare DRV8825 cannot deliver it — 2.5 A is its
absolute max and needs a heatsink plus forced airflow; on a bare StepStick you get roughly
1.5 A before thermal shutdown starts cutting out mid-move. With 1.0 Ω / 2.4 mH windings
these motors will pull whatever you allow.

You don't need the torque: a pen gantry uses a tiny fraction of 0.8 N·m. Run them cool.
Over-current drivers thermally throttle and start skipping steps ~20 min into a plot,
drifting the origin with no error message.

**2. 12 V on, motors connected. Jog and calibrate.**

```powershell
.\grbl.ps1 -Command 'G91 G0 X50','G90'
```

Measure it. `$100`/`$101` are set to **160**, which assumes **DRV8825 with all three
microstep jumpers fitted = 1/32**. The jumpers sit *under* each driver socket.

| Measured | You're on | Set `$100`/`$101` |
|---|---|---|
| 50 mm | 1/32 ✓ | 160 (already) |
| 100 mm | 1/16 | 80 |
| 25 mm | 1/64 | 320 |

> Confirm the chip marking on the purple StepSticks. DRV8825 and A4988 look nearly
> identical but differ by a factor of 2 here. If an axis runs backwards, flip a bit in `$3`.

**3. Servo on the bench** — signal from D6, separate 5 V supply, **not yet mounted**.
Ideally scope `M3 S0` and `M3 S1000` for clean 1.0 ms / 2.0 ms pulses at 50 Hz.

**4. Mount the servo and find the two S values:**

```powershell
.\pen-tune.ps1                               # coarse sweep S0..S1000
.\pen-tune.ps1 -From 300 -To 600 -Step 20    # narrow in
```

Note where the pen clears the paper and where it touches with slight spring pressure.
Put them in `pen.toml` and GRBL-Plotter. If the servo buzzes or warms at an extreme, back
off — it's against its stop.

**5. Plot a 50 mm calibration square,** measure it, then a circle. A circle that comes out
elliptical means your two axes have different effective steps/mm.

**6. Enable limits.** Once endstops are fitted: set `$130`/`$131` to real travel, then
`$22=1`, then `$20=1` (order matters — `$20` is rejected while `$22=0`). Re-push the file.

Leave **`$21=0`**. grbl-Mega-5X's RAMPS hard-limit support polls limit pins inside the
stepper ISR rather than using interrupts, because RAMPS endstops are spread across ports
E, J and D. It measurably degrades step performance. Upstream's own recommendation is soft
limits with correct travel lengths: home once at startup, then rely on `$20=1`.

**7. Then plot real artwork** — `.\plot.ps1`, and see "The plotter console" below.

---

## The plotter console

```powershell
.\plot.ps1
```

One window: jogging, sheet probing, artwork placement, preview and streaming. It exists
because this machine has **no endstops** — machine zero lives in RAM and moves every time
the board resets, so "plot at X=20, Y=20" means nothing on its own. Instead you tell the
machine where the paper is, and everything is placed relative to that.

### The idea

You jog the pen tip to the physical corners of the sheet and capture each one. Three
corners define an affine map from paper millimetres to machine millimetres, and that map
absorbs, in one step, the three things that are always slightly wrong:

| What's wrong | What the fit does about it |
|---|---|
| The sheet is never square to the gantry | recovers its rotation |
| The gantry itself is never exactly square | recovers the skew between the axes |
| steps/mm is a percent or two out | scales to the corners you actually touched |

That last one is the useful part. **The drawing lands inside the corners you probed even
when the machine's absolute scale is wrong**, because the fit is to measured positions,
not to a nominal 210 × 297. The Paper tab still reports the scale error it implied and the
`$100`/`$101` those numbers suggest — worth fixing eventually, but it will not spoil a
plot in the meantime.

A fourth corner is optional and is used as a check: the reported *worst corner miss* is
your mis-click detector. Under about 0.5 mm is a good frame.

### Working through it

1. **Machine tab** — connect, jog, and set the pen. Tune the servo with the slider, then
   press *use as UP value* / *use as DOWN value* rather than copying numbers by hand.
2. **Paper tab** — pick the sheet size, jog the nib over each corner, press *Capture*.
   Read the fit report.
3. **Artwork tab** — open an SVG or a G-code file. It is scaled, auto-oriented and
   centred inside the margin; the fit mode, rotation, anchor and nudge are all live and
   the preview updates as you change them.
4. **Plot tab** — *Outline the sheet* first. It traces the border with the pen up, so a
   mis-clicked corner costs nothing. Then **START PLOT**.

The preview is in machine coordinates: probed corners, margin box, travel envelope, the
travel moves, and a live crosshair following the pen. Wheel zooms, drag pans,
right-click jogs the pen to that spot (pen up first).

### Things worth knowing

- **Re-anchor after a power cycle.** The board's zero moves, the sheet does not — so the
  frame is off by a pure translation. Re-probe one corner and press *Re-anchor*; there is
  no need to redo the calibration. The same applies after any soft reset or ALARM, which
  is why both are called out in the log.
- **Pause at pen-up, not Hold.** *Hold* is a grbl feed hold: motion stops instantly with
  the nib still on the paper, which blots if you leave it. *Pause at pen-up* stops feeding
  at the next gap between strokes and lets the buffer drain, so the pen is in the air.
  Use it for ink refills and pen changes.
- **Jobs stream with character counting**, not send-and-wait-for-ok. Waiting per line
  drains grbl's planner between segments, so the machine decelerates at every point of
  every curve — slow, and it dwells long enough to blot at each stop.
- **The job header zeroes the G54 offset** (`G10 L2 P1 X0 Y0 Z0`) and cancels `G92`, so
  work coordinates equal machine coordinates. The paper frame is the coordinate system
  now; a stale work offset silently sliding the drawing across the sheet is exactly the
  failure this is meant to remove.
- **G-code import infers the pen.** Which `S` means "down" is not standardised, so
  whichever spindle state does the G1 work rather than the G0 travel is taken as the one
  touching the paper. It re-fits imported G-code to the sheet like any other artwork; the
  arcs are flattened on the way in.
- **The travel envelope is the only guard against driving into the frame.** With no
  endstops, grbl's own soft limits need homing it cannot do. A job reaching outside the
  box is refused, and the confirmation dialog tells you where it went.

```powershell
.venv\Scripts\python -m tests.test_pipeline   # geometry, frame maths, importers, emitter
.venv\Scripts\python -m tests.test_gui        # builds the whole UI, no hardware needed
```

Neither needs the controller. **`HANDOFF.md` is the cold-start guide** — setting up on a
different machine, what the clone deliberately does not carry, and the order to bring
things up in for a first plot.

---

## Other host software

**GRBL-Plotter** — https://grbl-plotter.de — Windows, free. SVG/DXF import, toolpath and
streaming in one app. Setup → Pen up/down:

- Pen up → `M3 S<up>`, pen down → `M3 S<down>` (your tuned values)
- Enable the post-pen-movement delay, ~250 ms. grbl syncs the planner on a spindle change
  but does **not** wait for the servo arm to physically arrive.

Use `M3 S<up>` for in-plot lifts; leave `M5` for end of job.

**Scriptable alternative:**

```powershell
pip install vpype vpype-gcode
vpype --config pen.toml read drawing.svg linemerge linesort reloop linesimplify `
      gwrite --profile pen out.gcode
```

`linemerge`/`linesort` matter more for plot quality and plot time than anything in the
firmware — they join paths sharing endpoints and minimise travel.

---

## Settings worth knowing

Full annotated list in `grbl-settings.txt`. The non-obvious ones:

- **`$32=0` — laser mode OFF. Mandatory.** Laser mode changes spindle output *without*
  stopping motion, so the pen would start moving before it has landed — dragged lines at
  the start of every stroke.
- **`$1=255`** — steppers never de-energise. A pen gantry that releases sags and loses
  registration between strokes.
- **`$30=1000`, `$31=0`** — defines the `S` → pulse mapping. Changing `$30` silently
  rescales your pen values.
- **`$11=0.010`** — tight junction deviation; corners matter more than speed here.
- **`$20`/`$21`/`$22=0`** — shipped off, see bring-up step 6.

---

## Where this deviates from the reference doc

- **The `uint8_t` → `uint16_t` widening is not needed.** The ref describes grbl 1.1f. This
  checkout is **1.2i**, where fra589 already widened the whole chain — verified:
  `spindle_compute_pwm_value()` returns `uint16_t` (`spindle_control.c:223`, commented
  "Mega2560 PWM register is 16-bit"), and `stepper.c:83,171` carry `uint16_t spindle_pwm`
  / `current_spindle_pwm`. No truncation at 255. Nothing to patch.
- **`DEFAULTS_RAMPS_BOARD` doesn't exist** in 1.2i's `defaults.h` — only `DEFAULTS_GENERIC`.
  Harmless: it only supplies factory values for `$RST=$`, and we write all settings explicitly.
- **Limits ship off, not `$20=1`/`$22=1`.** With no endstops fitted yet, `$22=1` makes grbl
  boot into alarm demanding `$H`, and `$20=1` is rejected while `$22=0`. Staged into step 6.
- **Built with arduino-cli, not `make` + `avrdude` directly.** arduino-cli is headless and
  scriptable — it *is* avrdude underneath — so this meets "do not rely on the Arduino IDE"
  while keeping one command for build and flash. The repo's Makefile still works if you
  prefer it.
- Ref's servo "Option A" (/1024 prescaler, ~61 Hz, ~16 positions) was skipped for Option B,
  which is what's implemented.

## Notes

- `error:7` on boot means grbl reset a corrupt EEPROM region to defaults. It appeared on
  first flash and was cleared with `$RST=#`. If it returns, re-push `grbl-settings.txt`.
- Z is a dummy axis kept only because grbl-Mega-5X requires `N_AXIS >= 3`. No motor, and no
  G-code here emits a Z word.
- Delete `tools/` to remove the toolchain entirely.

## Sources

- grbl-Mega-5X — https://github.com/fra589/grbl-Mega-5X ([pinout wiki](https://github.com/fra589/grbl-Mega-5X/wiki/grbl-Mega-5X-pinout))
- GRBL-Plotter — https://grbl-plotter.de/index.php?id=quick-guide
- vpype-gcode — https://github.com/plottertools/vpype-gcode
- `ramps14-pen-plotter-reference.md` (your brief, in Downloads)
