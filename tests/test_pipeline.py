"""Headless checks for the parts that decide where ink lands.

    .venv\\Scripts\\python -m tests.test_pipeline

No hardware and no GUI: geometry, the paper frame, both importers, and the
emitter. If these pass, a bad plot is a mechanical problem, not a maths one.
"""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plotter import art, emit  # noqa: E402
from plotter.frame import PaperFrame  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def close(a, b, tol=1e-6) -> bool:
    return bool(np.all(np.abs(np.asarray(a) - np.asarray(b)) < tol))


# ----------------------------------------------------------------- paper frame
def test_frame_square() -> None:
    print("\npaper frame - sheet square to the machine")
    f = PaperFrame(width=210, height=297)
    f.set_probe("BL", (50.0, 30.0))
    f.set_probe("BR", (260.0, 30.0))
    rep = f.set_probe("TL", (50.0, 327.0))
    check("fits from 3 corners", rep.ok, rep.message)
    check("origin maps to BL", close(f.to_machine([[0, 0]])[0], [50, 30]))
    check("top-right maps", close(f.to_machine([[210, 297]])[0], [260, 327]))
    check("scale is 1.0", close([rep.scale_x, rep.scale_y], [1, 1], 1e-9))
    check("no rotation", abs(rep.rotation_deg) < 1e-9)
    check("no skew", abs(rep.skew_deg) < 1e-9)
    back = f.to_paper([[155.0, 178.5]])[0]
    check("inverse round-trips", close(back, [105, 148.5], 1e-9), f"{back}")


def test_frame_rotated_and_scaled() -> None:
    print("\npaper frame - sheet rotated 5 degrees, machine 2% short")
    ang = math.radians(5.0)
    R = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
    k = 0.98  # machine reports 0.98 mm for every real mm
    origin = np.array([12.0, 7.0])

    def truth(u, v):
        return R @ (np.array([u, v]) * k) + origin

    f = PaperFrame(width=210, height=297)
    for name, (u, v) in (("BL", (0, 0)), ("BR", (210, 0)), ("TL", (0, 297)), ("TR", (210, 297))):
        rep = f.set_probe(name, tuple(truth(u, v)))

    check("4-corner fit", rep.ok, rep.message)
    check("recovers rotation", abs(rep.rotation_deg - 5.0) < 1e-6, f"{rep.rotation_deg:.6f}")
    check("recovers scale", abs(rep.scale_x - k) < 1e-9, f"{rep.scale_x:.6f}")
    check("no skew reported", abs(rep.skew_deg) < 1e-6)
    check("residual ~0", rep.residual_mm < 1e-6, f"{rep.residual_mm:.2e}")
    mid = f.to_machine([[105, 148.5]])[0]
    check("centre lands right", close(mid, truth(105, 148.5), 1e-9))


def test_frame_reanchor() -> None:
    print("\npaper frame - re-anchor after the machine zero moves")
    f = PaperFrame(width=210, height=297)
    f.set_probe("BL", (50.0, 30.0))
    f.set_probe("BR", (260.0, 30.0))
    f.set_probe("TL", (50.0, 327.0))
    before = f.to_machine([[100, 100]])[0]
    f.reanchor("BL", (60.0, 25.0))  # same sheet, machine zero shifted by +10 / -5
    after = f.to_machine([[100, 100]])[0]
    check("frame translated", close(after - before, [10, -5], 1e-9), f"{after - before}")
    check("probe list moved too", close(f.probes["BR"], [270, 25], 1e-9))


def test_frame_skew() -> None:
    print("\npaper frame - non-square gantry")
    f = PaperFrame(width=210, height=297)
    f.set_probe("BL", (0.0, 0.0))
    f.set_probe("BR", (210.0, 0.0))
    rep = f.set_probe("TL", (297 * math.sin(math.radians(2.0)), 297 * math.cos(math.radians(2.0))))
    # The sheet's v edge sits 88 degrees from its u edge, so skew reads -2.
    check("skew detected", abs(rep.skew_deg + 2.0) < 1e-6, f"{rep.skew_deg:.4f} deg")


# --------------------------------------------------------------------- import
SVG = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50">
  <rect x="10" y="10" width="80" height="30" fill="none" stroke="black"/>
  <path d="M 20 20 C 30 5, 50 5, 60 20" fill="none" stroke="black"/>
</svg>
"""


def test_svg() -> None:
    print("\nSVG import")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.svg"
        p.write_text(SVG, encoding="utf-8")
        d = art.load_svg(p, quantization_mm=0.05)
    check("paths found", d.count == 2, f"{d.count} paths")
    w, h = d.size()
    # The rect is 80 x 30; the bezier overshoots it upward to y = 8.75, so the
    # union is 80 x 31.25. Getting that exactly is the unit conversion working.
    check("size in mm", abs(w - 80) < 0.01 and abs(h - 31.25) < 0.05, f"{w:.2f} x {h:.2f} mm")
    check("curve flattened", d.point_count() > 20, f"{d.point_count()} points")


def test_gcode_roundtrip() -> None:
    print("\nG-code import")
    src = Path(__file__).resolve().parent.parent / "testplot.nc"
    if not src.exists():
        check("testplot.nc present", False)
        return
    d = art.load_gcode(src)
    check("geometry recovered", d.count == 2, f"{d.count} paths ({d.note})")
    w, h = d.size()
    # A 40 mm square at 20,20 plus a 40 mm circle centred at 100,40.
    check("bounds match the file", abs(w - 100) < 0.5 and abs(h - 40) < 0.5, f"{w:.2f} x {h:.2f}")
    check("arc was flattened", d.point_count() > 30, f"{d.point_count()} points")
    circle, square = sorted(
        float(np.sum(np.hypot(*np.diff(l, axis=0).T))) for l in d.lines
    )
    check("square perimeter 160", abs(square - 160) < 0.01, f"{square:.2f} mm")
    # Chords cut the corner, so a flattened arc is always a shade short.
    check("circle circumference 125.7", 0 < 40 * math.pi - circle < 0.2, f"{circle:.2f} mm")


def test_gcode_pen_inference() -> None:
    print("\nG-code import - pen state inference")
    body = "\n".join([
        "G21 G90", "M3 S400", "G0 X0 Y0", "M3 S1000", "G1 X10 Y0", "G1 X10 Y10",
        "M3 S400", "G0 X50 Y50", "M3 S1000", "G1 X60 Y50", "M5", "M2",
    ])
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.nc"
        p.write_text(body, encoding="utf-8")
        d = art.load_gcode(p)
    check("two strokes, travel excluded", d.count == 2, f"{d.count} ({d.note})")
    total = sum(float(np.sum(np.hypot(*np.diff(l, axis=0).T))) for l in d.lines)
    check("only drawn length counted", abs(total - 30) < 1e-6, f"{total:.3f} mm")


# ------------------------------------------------------------------- placement
def test_place_autorotate() -> None:
    print("\nplacement")
    wide = Drawing_box(200, 100)
    r = art.place(wide, 210, 297, art.Placement(fit_mode="fit to sheet", margin_mm=10))
    check("landscape art turned upright", r.rotation_deg == 90.0, f"{r.rotation_deg}")
    w, h = r.drawing.size()
    check("fits inside the margins", w <= 190.001 and h <= 277.001, f"{w:.2f} x {h:.2f}")
    check("touches one margin", abs(w - 190) < 0.01 or abs(h - 277) < 0.01)
    x0, y0, x1, y1 = r.drawing.bounds()
    check("centred", abs((x0 + x1) / 2 - 105) < 0.01 and abs((y0 + y1) / 2 - 148.5) < 0.01)
    check("no warnings", not r.warnings, str(r.warnings))

    r2 = art.place(wide, 210, 297, art.Placement(fit_mode="actual size", rotation="0", margin_mm=10))
    check("actual size keeps 1:1", abs(r2.scale - 1.0) < 1e-9)
    check("oversize is reported", bool(r2.warnings), str(r2.warnings))

    r3 = art.place(wide, 210, 297,
                   art.Placement(fit_mode="width mm", target_mm=100, rotation="0",
                                 anchor="bottom-left", margin_mm=10))
    w3, h3 = r3.drawing.size()
    check("explicit width honoured", abs(w3 - 100) < 1e-6, f"{w3:.3f}")
    check("bottom-left anchored", close(r3.drawing.bounds()[:2], [10, 10], 1e-6))


def Drawing_box(w: float, h: float) -> art.Drawing:
    return art.Drawing([np.array([[0, 0], [w, 0], [w, h], [0, h], [0, 0]], dtype=float)], "box")


def test_optimize() -> None:
    print("\npath clean-up")
    a = np.array([[0, 0], [10, 0]], dtype=float)
    b = np.array([[10, 0], [10, 10]], dtype=float)   # continues a
    c = np.array([[50, 50], [60, 50]], dtype=float)  # far away
    d = art.Drawing([a, c, b], "t")
    o = art.optimize(d, merge_mm=0.1, simplify_mm=0.0, sort=True)
    check("touching paths merged", o.count == 2, f"{o.count} paths")
    check("travel reduced", o.travel_length() <= d.travel_length() + 1e-9,
          f"{d.travel_length():.1f} -> {o.travel_length():.1f} mm")

    zig = np.column_stack([np.linspace(0, 100, 500), np.zeros(500)])
    s = art.optimize(art.Drawing([zig], "t"), merge_mm=0, simplify_mm=0.05, sort=False)
    check("straight line simplified", s.point_count() == 2, f"{s.point_count()} points")


# ---------------------------------------------------------------------- output
def test_emit() -> None:
    print("\nG-code out")
    f = PaperFrame(width=210, height=297)
    f.set_probe("BL", (50.0, 30.0))
    f.set_probe("BR", (260.0, 30.0))
    f.set_probe("TL", (50.0, 327.0))
    placed = art.place(Drawing_box(100, 100), 210, 297,
                       art.Placement(fit_mode="actual size", rotation="0", anchor="bottom-left",
                                     margin_mm=10))
    pen = emit.PenConfig(up_s=400, down_s=1000)
    job = emit.build(placed.drawing, f, pen, emit.MachineLimits(enabled=False))
    text = job.text

    check("work offset zeroed in header", "G10 L2 P1 X0 Y0 Z0" in text)
    check("no Z words", " Z" not in text.replace("X0 Y0 Z0", ""))
    check("pen drops after the rapid",
          text.index("G0 X60.000 Y40.000") < text.index("M3 S1000"))
    check("dwell after every pen move", text.count("G4 P") >= 2 * job.n_paths)
    check("ends parked up", text.strip().endswith("M2") and "M5" in text)
    check("bounds are machine mm",
          close(job.machine_bounds, [60, 40, 160, 140], 1e-6), str(job.machine_bounds))
    check("a pen-up pause point exists", any(l.pen_up_break for l in job.lines))
    check("time estimated", job.seconds > 0, job.eta)

    lim = emit.MachineLimits(x_min=0, x_max=100, y_min=0, y_max=100, enabled=True)
    j2 = emit.build(placed.drawing, f, pen, lim)
    check("envelope breach warned", bool(j2.warnings), str(j2.warnings))

    out = emit.outline_job(f, pen)
    check("dry run stays up", "S1000" not in out.text and "G1 " in out.text)


def test_frame_uncalibrated() -> None:
    print("\nguards")
    f = PaperFrame()
    job = emit.build(Drawing_box(10, 10), f, emit.PenConfig())
    check("refuses to emit without a frame", not job.lines and bool(job.warnings),
          str(job.warnings))


if __name__ == "__main__":
    test_frame_square()
    test_frame_rotated_and_scaled()
    test_frame_reanchor()
    test_frame_skew()
    test_svg()
    test_gcode_roundtrip()
    test_gcode_pen_inference()
    test_place_autorotate()
    test_optimize()
    test_emit()
    test_frame_uncalibrated()

    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: " + ", ".join(FAILED))
        sys.exit(1)
    print("all checks passed")
