# Handoff — resuming on another machine

State as of **2026-08-14**, commit `3e20448` on `main`.

The plotter console is written, tested headlessly, and pushed. **It has never talked to
the actual controller.** Everything below is about getting from a fresh clone to a first
real plot.

---

## 1. Get it running

```powershell
git clone https://github.com/Arcco-Chakraborty/pen-plotter.git
cd pen-plotter
.\plot.ps1
```

`plot.ps1` creates `.venv` and installs `pyserial` and `vpype` on first run, so the only
prerequisite is **Python 3.10+ on PATH, with tkinter**. The python.org installer includes
tkinter; some stripped-down and conda builds do not — check with:

```powershell
python -c "import tkinter; print(tkinter.TkVersion)"
```

If PowerShell refuses to run the script, it is the execution policy, not the script:

```powershell
powershell -ExecutionPolicy Bypass -File .\plot.ps1
```

Use `.\plot.ps1 -Console` while bringing things up — it keeps the terminal attached so a
Python traceback is visible instead of the window just vanishing.

**The COM port will be different on the laptop.** Press *Rescan* in the top bar and pick
it; the choice is remembered from then on.

Sanity check before touching hardware, ~2 seconds, no controller needed:

```powershell
.venv\Scripts\python -m tests.test_pipeline
.venv\Scripts\python -m tests.test_gui
```

---

## 2. What the clone does not bring

| Missing | Why | What to do |
|---|---|---|
| `.venv/` | ignored, ~200 MB | `plot.ps1` rebuilds it automatically |
| `plotter-config.json`, `paper-frame.json` | ignored on purpose | They describe *that* machine and *that* sheet. A paper frame is only valid until the board resets, so carrying one to another session would hand you a calibration that is already wrong. Probe again. |
| `tools/` | ignored, 482 MB of arduino-cli + AVR core | Only needed to **rebuild firmware**. The board is already flashed, so skip it unless you are changing `firmware/`. To restore: drop `arduino-cli.exe` in `tools\arduino-cli\`, then `arduino-cli core install arduino:avr` with `ARDUINO_DIRECTORIES_DATA=tools\acli-data` and `ARDUINO_DIRECTORIES_USER=tools\acli-user` — the two paths `build.ps1` expects. |

Everything else — firmware source, settings, the console, the tests — is in the repo.

---

## 3. Where things stand

**Written and verified without hardware** (49 checks in `tests/test_pipeline.py`, plus 15
in a UI smoke test that builds every widget and exercises every layout option):

- the affine paper frame, against synthetic sheets that are rotated, scaled and skewed —
  it recovers 5.000° rotation and 0.980 scale exactly, and re-anchoring translates cleanly
- SVG import through vpype, with correct mm units and curve flattening
- G-code import, including arc flattening and inferring which `S` means pen-down; round
  trips `testplot.nc` back to a 160 mm square and a 125.7 mm circle
- placement: auto-orient, all five fit modes, all nine anchors, margin overflow warnings
- the emitter: header, dwell placement, pen-up pause points, envelope refusal, ETA

**Not exercised at all** — everything that needs the controller:

- opening the port without resetting the board
- status polling and `MPos` parsing on real traffic
- capturing a probe from a live status report
- the character-counting streamer under real `ok` flow
- pause-at-pen-up, feed hold, abort
- whether the servo dwells (0.15 s up, 0.25 s down) are long enough for your arm

---

## 4. First real plot

Do these in order. Each one fails cheaply if the previous one was wrong.

1. **Connect.** The state pill should show `idle`. If it shows `alarm`, press *Unlock $X*.
2. **Set the travel envelope** — Plot tab. It currently holds placeholders
   (`X -5..300`, `Y -5..400`). Put your machine's real reachable travel there. With no
   endstops this box is the *only* thing standing between a bad number and the pen
   driving into the frame.
3. **Set the pen values** — Machine tab. Drag the slider until the nib just clears the
   paper, press *use as UP value*; press it down to slight spring pressure, press *use as
   DOWN value*. Do not retype them anywhere else.
4. **Tape down an A4 sheet** and jog the nib over each corner, capturing BL, BR, TR, TL.
   Watch the fit report: rotation and skew should be small, and with four corners the
   *worst corner miss* should be well under 0.5 mm. If it is not, you mis-clicked a
   corner — clear and redo.
5. **Outline the sheet** (Plot tab). The nib traces the border with the pen up. It should
   follow the paper edge. This is the moment to catch a bad frame.
6. **Load artwork**, check the preview sits inside the margin box, then **Outline the
   artwork** for a second free dry run.
7. **START PLOT.**

Keep `$32=0` (laser mode off). With it on, spindle changes do not stop motion, so the pen
starts moving before it has landed and every stroke begins with a dragged line.

---

## 5. If something goes wrong

| Symptom | Cause |
|---|---|
| grbl answers everything, position changes, nothing moves | 12 V is not connected. USB powers logic only. |
| Job refused, "reaches machine X … outside the travel envelope" | Envelope numbers in the Plot tab are still the placeholders — set your real travel. |
| Board resets when the console connects | Should not happen; the port is opened with DTR/RTS deasserted. If it does, something else has the port open too. |
| Blob at the start of each stroke | Pen-down dwell too long, or feed too slow off the mark. |
| First few mm of each stroke missing, or a dragged line into it | Pen-down dwell too short — the servo has not landed. Raise `dwell s` on the pen-down row. |
| Drawing comes out mirrored or upside down | The corners were captured in a different physical arrangement than intended. BL/BR/TR/TL are whichever corners you *call* that; the artwork's "up" follows the BL→TL direction. There is a *mirror* toggle in the Artwork tab. |
| Machine stops mid-plot and drifts afterwards | Drivers thermally throttling. Vref should be 0.35–0.50 V, not set from the motor's 2.5 A rating. |
| Pen parked on the paper after a pause | You used *Hold*, not *Pause at pen-up*. Hold is an instant feed hold. |
| After a power cycle the drawing lands in the wrong place | The board's zero moved. Re-probe one corner and press *Re-anchor* — no need to redo the calibration. |

---

## 6. Deliberately not built

- **Typed text in a handwriting font.** Offered and declined — the console takes files,
  not text. If you want the machine to write typed text later, that is a Hershey
  single-stroke font engine feeding the same placement pipeline, and it is a clean
  addition rather than a rework.
- **Homing and hard limits.** No endstops fitted. `$21` should stay 0 even once they are —
  grbl-Mega-5X polls limit pins inside the stepper ISR on RAMPS, which measurably degrades
  step timing. Use soft limits with real `$130`/`$131` instead, per bring-up step 6 in the
  README.
- **Multi-pen / layer support.** Every path plots with one pen. Layer-aware plotting with
  a pause for a pen change would build on the existing pause-at-pen-up mechanism.

---

## Map of the code

| File | What it owns |
|---|---|
| `plotter/grblink.py` | Serial link: no-reset open, reader and poller threads, character-counting streamer, feed hold / pause-at-break / abort |
| `plotter/frame.py` | The probed paper frame — affine fit, diagnostics, re-anchor, JSON |
| `plotter/art.py` | SVG and G-code import, merge/sort/simplify, placement on the sheet |
| `plotter/emit.py` | Placed artwork → machine-coordinate G-code, envelope check, dry-run outlines |
| `plotter/app.py` | The Tk console — every widget, the preview canvas, event pump |
| `plotter/config.py` | Persisted settings |
| `tests/` | Both test suites; neither needs hardware |

The reasoning behind the non-obvious choices — why character counting, why the job header
zeroes G54, why the fit is affine — is in the README under *The plotter console*, and in
the module docstrings.
