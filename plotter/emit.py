"""Turning placed artwork into G-code for this machine.

Coordinates leave here as absolute machine millimetres, so the job header zeroes
the G54 work offset and cancels any G92. Once the paper frame exists it is the
coordinate system that matters, and a stale work offset silently sliding the whole
drawing across the sheet is the exact failure this app is meant to remove.

Pen moves are spindle words - `M3 S<value>` - because the pen servo is driven by
grbl's spindle PWM. Every lift and every drop is followed by a dwell: grbl syncs
the planner on a spindle change but does not wait for a servo arm to physically
arrive, so without the dwell the machine starts moving while the nib is still on
its way down and drags a line into the start of the stroke.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .art import Drawing
from .frame import PaperFrame
from .grblink import Line


@dataclass
class PenConfig:
    up_s: int = 400
    down_s: int = 1000
    up_dwell: float = 0.15
    down_dwell: float = 0.25
    draw_feed: float = 1500.0
    travel_feed: float = 3000.0  # used for the G0 time estimate only
    park_paper_xy: tuple[float, float] = (0.0, 0.0)
    park_at_end: bool = True


@dataclass
class MachineLimits:
    x_min: float = -5.0
    x_max: float = 300.0
    y_min: float = -5.0
    y_max: float = 400.0
    enabled: bool = True


@dataclass
class Job:
    lines: list[Line] = field(default_factory=list)
    n_paths: int = 0
    draw_mm: float = 0.0
    travel_mm: float = 0.0
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    machine_bounds: tuple[float, float, float, float] = (0, 0, 0, 0)

    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines) + "\n"

    @property
    def eta(self) -> str:
        m, s = divmod(int(self.seconds), 60)
        h, m = divmod(m, 60)
        return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"


def build(
    placed: Drawing,
    frame: PaperFrame,
    pen: PenConfig,
    limits: MachineLimits | None = None,
    header_note: str = "",
) -> Job:
    """Placed artwork (paper mm) -> a streamable job (machine mm)."""
    job = Job()
    if not placed.lines:
        job.warnings.append("nothing to plot")
        return job
    if not frame.calibrated:
        job.warnings.append("no paper frame - probe the sheet corners first")
        return job

    machine = [frame.to_machine(l) for l in placed.lines]
    allp = np.vstack(machine)
    job.machine_bounds = (
        float(allp[:, 0].min()), float(allp[:, 1].min()),
        float(allp[:, 0].max()), float(allp[:, 1].max()),
    )
    if limits and limits.enabled:
        x0, y0, x1, y1 = job.machine_bounds
        if x0 < limits.x_min or x1 > limits.x_max or y0 < limits.y_min or y1 > limits.y_max:
            job.warnings.append(
                f"job reaches machine X {x0:.1f}..{x1:.1f}, Y {y0:.1f}..{y1:.1f} mm, "
                f"outside the travel envelope X {limits.x_min:.0f}..{limits.x_max:.0f}, "
                f"Y {limits.y_min:.0f}..{limits.y_max:.0f}"
            )

    out: list[Line] = []

    def add(text: str, brk: bool = False) -> None:
        out.append(Line(text, brk))

    def comment(text: str) -> None:
        # grbl's line buffer is 80 characters and it rejects the whole block on
        # overflow, so a long filename in the header must not be able to error
        # out a job that is otherwise fine.
        add("(" + text[:70].replace("(", "[").replace(")", "]") + ")")

    comment(header_note or "pen plotter job")
    comment(f"paper {frame.width:g} x {frame.height:g} mm, framed {frame.captured_at}")
    add("G21")
    add("G90")
    add("G17")
    add("G92.1")                     # cancel any G92 offset
    add("G10 L2 P1 X0 Y0 Z0")        # G54 offset to zero: work == machine
    add("G54")
    add(f"F{pen.draw_feed:.0f}")
    add(f"M3 S{pen.up_s}")
    add(f"G4 P{pen.up_dwell:.2f}", brk=True)

    cur = None
    for line in machine:
        pts = _dedupe(line)
        if len(pts) < 2:
            continue
        job.n_paths += 1
        if cur is not None:
            job.travel_mm += float(np.hypot(*(pts[0] - cur)))
        add(f"G0 X{pts[0][0]:.3f} Y{pts[0][1]:.3f}")
        add(f"M3 S{pen.down_s}")
        add(f"G4 P{pen.down_dwell:.2f}")
        for i, (x, y) in enumerate(pts[1:]):
            add(f"G1 X{x:.3f} Y{y:.3f}" + (f" F{pen.draw_feed:.0f}" if i == 0 else ""))
        job.draw_mm += float(np.sum(np.hypot(*np.diff(pts, axis=0).T)))
        add(f"M3 S{pen.up_s}")
        add(f"G4 P{pen.up_dwell:.2f}", brk=True)
        cur = pts[-1]

    if pen.park_at_end:
        px, py = frame.to_machine(np.array([pen.park_paper_xy]))[0]
        add(f"G0 X{px:.3f} Y{py:.3f}")
    add("M5", brk=True)  # parks the servo at the pen-up pulse, does not go limp
    add("M2")

    job.lines = out
    job.seconds = (
        job.draw_mm / max(pen.draw_feed, 1) * 60.0
        + job.travel_mm / max(pen.travel_feed, 1) * 60.0
        + job.n_paths * (pen.up_dwell + pen.down_dwell + 0.15)
    )
    return job


def outline_job(
    frame: PaperFrame,
    pen: PenConfig,
    box: tuple[float, float, float, float] | None = None,
    feed: float = 2000.0,
) -> Job:
    """A pen-up lap of the sheet (or of the artwork's box), for a dry run.

    Watching the nib travel the outline is the cheapest way to catch a mis-clicked
    corner or a sheet that has shifted, and it costs no ink.
    """
    job = Job()
    if not frame.calibrated:
        job.warnings.append("no paper frame")
        return job
    x0, y0, x1, y1 = box if box else (0.0, 0.0, frame.width, frame.height)
    corners = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]])
    mach = frame.to_machine(corners)

    out = [
        Line("(dry run outline - pen stays up)"),
        Line("G21"), Line("G90"), Line("G17"), Line("G92.1"),
        Line("G10 L2 P1 X0 Y0 Z0"), Line("G54"),
        Line(f"M3 S{pen.up_s}"), Line(f"G4 P{pen.up_dwell:.2f}", True),
    ]
    for x, y in mach:
        out.append(Line(f"G1 X{x:.3f} Y{y:.3f} F{feed:.0f}"))
    out.append(Line("M5", True))
    job.lines = out
    job.n_paths = 1
    job.travel_mm = float(np.sum(np.hypot(*np.diff(mach, axis=0).T)))
    job.seconds = job.travel_mm / max(feed, 1) * 60.0
    job.machine_bounds = (
        float(mach[:, 0].min()), float(mach[:, 1].min()),
        float(mach[:, 0].max()), float(mach[:, 1].max()),
    )
    return job


def _dedupe(pts: np.ndarray, tol: float = 1e-4) -> np.ndarray:
    """Drop repeated points - each one is a wasted planner block."""
    if len(pts) < 2:
        return pts
    keep = np.ones(len(pts), dtype=bool)
    keep[1:] = np.hypot(*np.diff(pts, axis=0).T) > tol
    keep[-1] = True
    return pts[keep]
