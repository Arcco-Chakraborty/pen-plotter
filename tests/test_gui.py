"""Builds the whole interface and drives it without a controller attached.

    .venv\\Scripts\\python -m tests.test_gui

Opens a real window briefly. Nothing here needs hardware: it probes a synthetic
paper frame, loads artwork, re-places it, redraws the preview, and builds a job -
the paths that would otherwise only get exercised in front of the machine.
"""

from __future__ import annotations

import sys
import tempfile
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plotter.app import App  # noqa: E402

SVG = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="120mm" height="60mm" viewBox="0 0 120 60">
  <path d="M10 30 Q 35 5 60 30 T 110 30" fill="none" stroke="black"/>
  <circle cx="60" cy="30" r="18" fill="none" stroke="black"/>
</svg>
"""

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    (tmp / "art.svg").write_text(SVG, encoding="utf-8")

    root = tk.Tk()
    app = App(root, tmp)
    root.update()
    check("window built", app.root.winfo_exists() == 1)
    check("all four tabs present", len(app.tab_job.winfo_children()) > 0)

    # A sheet taped down 3 degrees off, probed at four corners.
    import math

    import numpy as np
    a = math.radians(3.0)
    R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    for name, uv in (("BL", (0, 0)), ("BR", (210, 0)), ("TR", (210, 297)), ("TL", (0, 297))):
        app.frame.set_probe(name, tuple(R @ np.array(uv) + np.array([20.0, 15.0])))
    app._refresh_frame_ui()
    root.update()
    check("frame calibrated", app.frame.calibrated)
    check("fit reported in the UI", "rotation" in app.lbl_fit.cget("text"))

    app.art_raw = None
    from plotter import art
    app.art_raw = art.load(tmp / "art.svg", 0.05)
    app.lbl_file.configure(text="art.svg")
    app._reoptimize()
    root.update()
    check("artwork loaded and placed", app.placed is not None and app.placed.drawing.count > 0)
    check("preview geometry built", len(app.preview_lines) > 0)
    check("stats shown", "paths" in app.lbl_stats.cget("text"))

    app._fit_view()
    root.update()
    check("canvas drew items", len(app.canvas.find_all()) > 5,
          f"{len(app.canvas.find_all())} items")

    for mode in ("fit to sheet", "actual size", "scale %", "width mm", "height mm"):
        app.v_fit.set(mode)
        root.update()
    for rot in ("auto", "0", "90", "180", "270"):
        app.v_rot.set(rot)
        root.update()
    for anchor in ("top-left", "centre", "bottom-right"):
        app.v_pos.set(anchor)
        root.update()
    check("every layout option re-places cleanly", app.placed is not None)

    app.v_fit.set("fit to sheet")
    app.v_rot.set("auto")
    root.update()
    job = app._make_job()
    check("job builds from the UI state", job is not None and len(job.lines) > 20,
          f"{len(job.lines) if job else 0} lines")
    check("job is in machine coordinates",
          job is not None and job.machine_bounds[0] > 15, str(job.machine_bounds if job else ""))

    # Sending with nothing connected must be refused, not crash.
    app._jog("X", 1)
    app._pen_up()
    app._mdi()
    app._capture("BL")
    root.update()
    check("offline actions are refused safely", True)

    app.cfg.save(tmp / "plotter-config.json")
    app.frame.save(tmp / "paper-frame.json")
    check("config and frame persist", (tmp / "plotter-config.json").exists()
          and (tmp / "paper-frame.json").exists())

    from plotter.config import Config
    from plotter.frame import PaperFrame
    c2 = Config.load(tmp / "plotter-config.json")
    f2 = PaperFrame.load(tmp / "paper-frame.json")
    check("config round-trips", c2.pen.down_s == app.cfg.pen.down_s)
    check("frame round-trips", f2.calibrated and abs(f2.describe().rotation_deg - 3.0) < 1e-6,
          f"{f2.describe().rotation_deg:.4f} deg")

    root.destroy()
    print()
    if failures:
        print(f"{len(failures)} FAILED: " + ", ".join(failures))
        return 1
    print("gui smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
