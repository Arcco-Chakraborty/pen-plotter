"""Artwork: getting it in, cleaning it up, and placing it on the sheet.

A drawing is just a list of open polylines, `(N, 2)` float arrays of millimetres
with **y pointing up**. SVG's y-down convention is flipped at import so everything
downstream - placement, preview, G-code - shares one orientation.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

PX_TO_MM = 25.4 / 96.0  # vpype reads SVG at the CSS 96 dpi reference
MM_TO_PX = 1.0 / PX_TO_MM

ROTATIONS = ("auto", "0", "90", "180", "270")
ANCHORS = (
    "top-left", "top-centre", "top-right",
    "left", "centre", "right",
    "bottom-left", "bottom-centre", "bottom-right",
)
FIT_MODES = ("fit to sheet", "actual size", "scale %", "width mm", "height mm")


# --------------------------------------------------------------------- drawing
@dataclass
class Drawing:
    lines: list[np.ndarray] = field(default_factory=list)
    source: str = ""
    note: str = ""

    def __bool__(self) -> bool:
        return bool(self.lines)

    @property
    def count(self) -> int:
        return len(self.lines)

    def bounds(self) -> tuple[float, float, float, float]:
        """(xmin, ymin, xmax, ymax) in mm. A zero box if empty."""
        if not self.lines:
            return (0.0, 0.0, 0.0, 0.0)
        allp = np.vstack(self.lines)
        return (
            float(allp[:, 0].min()),
            float(allp[:, 1].min()),
            float(allp[:, 0].max()),
            float(allp[:, 1].max()),
        )

    def size(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bounds()
        return (x1 - x0, y1 - y0)

    def draw_length(self) -> float:
        return sum(
            float(np.sum(np.hypot(*np.diff(l, axis=0).T))) for l in self.lines if len(l) > 1
        )

    def travel_length(self) -> float:
        """Pen-up distance, in the order the paths are stored."""
        if not self.lines:
            return 0.0
        total = 0.0
        cur = self.lines[0][0]
        for l in self.lines:
            total += float(np.hypot(*(l[0] - cur)))
            cur = l[-1]
        return total

    def point_count(self) -> int:
        return int(sum(len(l) for l in self.lines))

    def transformed(self, fn) -> "Drawing":
        return Drawing([fn(l) for l in self.lines], self.source, self.note)


# --------------------------------------------------------------------- import
def load(path: str | Path, quantization_mm: float = 0.05, down_s: float | None = None) -> Drawing:
    p = Path(path)
    if p.suffix.lower() == ".svg":
        return load_svg(p, quantization_mm)
    return load_gcode(p, down_s=down_s)


def load_svg(path: str | Path, quantization_mm: float = 0.05) -> Drawing:
    """Flatten an SVG to polylines via vpype (curves, arcs, transforms and all)."""
    import vpype

    lc, w, h = vpype.read_svg(
        str(path), quantization=max(quantization_mm, 0.005) * MM_TO_PX, crop=False
    )
    lines: list[np.ndarray] = []
    for line in lc:
        pts = np.column_stack([line.real, line.imag]) * PX_TO_MM
        pts[:, 1] = -pts[:, 1]  # SVG y grows downward; the plotter's does not
        if len(pts) >= 2:
            lines.append(pts.astype(float))
    note = ""
    if w and h:
        note = f"document {w * PX_TO_MM:.1f} x {h * PX_TO_MM:.1f} mm"
    return Drawing(lines, source=Path(path).name, note=note)


_WORD = re.compile(r"([A-Za-z])\s*(-?\d*\.?\d+)")


def load_gcode(path: str | Path, down_s: float | None = None, arc_tol_mm: float = 0.05) -> Drawing:
    """Recover the drawn geometry from an existing G-code file.

    Pen state is not standardised, so which `S` means "down" is inferred: whichever
    spindle state does the G1 work rather than the G0 travel is the one touching
    the paper. Pass `down_s` to override when a file does something unusual.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")

    pos = np.array([0.0, 0.0])
    absolute = True
    scale = 1.0  # G20 switches to inches
    spindle: float | None = None  # None = M5 / off
    motion = 0

    # Pass 1 collects every move tagged with the spindle state it ran under.
    moves: list[tuple[float | None, bool, np.ndarray]] = []

    for raw in text.splitlines():
        line = re.sub(r"\(.*?\)", " ", raw)
        line = line.split(";", 1)[0].strip()
        if not line:
            continue
        words = {w.upper(): float(v) for w, v in _WORD.findall(line)}
        if not words:
            continue

        for g in _codes(line, "G"):
            if g in (0, 1, 2, 3):
                motion = int(g)
            elif g == 20:
                scale = 25.4
            elif g == 21:
                scale = 1.0
            elif g == 90:
                absolute = True
            elif g == 91:
                absolute = False
        for m in _codes(line, "M"):
            if m == 3 or m == 4:
                spindle = words.get("S", spindle if spindle is not None else 0.0)
            elif m == 5:
                spindle = None
        if spindle is not None and "S" in words and not _codes(line, "M"):
            spindle = words["S"]

        has_move = any(k in words for k in ("X", "Y", "I", "J", "R"))
        if not has_move:
            continue

        target = pos.copy()
        for i, ax in enumerate(("X", "Y")):
            if ax in words:
                target[i] = words[ax] * scale if absolute else pos[i] + words[ax] * scale

        if motion in (0, 1):
            if not np.allclose(target, pos):
                moves.append((spindle, motion == 0, np.array([pos.copy(), target.copy()])))
            pos = target
        elif motion in (2, 3):
            pts = _arc(pos, target, words, scale, motion == 2, arc_tol_mm)
            if len(pts) >= 2:
                moves.append((spindle, False, pts))
            pos = target

    if not moves:
        return Drawing([], source=Path(path).name, note="no motion found")

    down = _infer_pen_down(moves, down_s)

    # Pass 2 stitches consecutive pen-down moves back into polylines.
    lines: list[np.ndarray] = []
    cur: list[np.ndarray] = []
    for sp, rapid, pts in moves:
        drawing = (sp in down) and not rapid
        if drawing:
            if cur and np.allclose(cur[-1][-1], pts[0], atol=1e-6):
                cur.append(pts[1:])
            else:
                if cur:
                    lines.append(np.vstack(cur))
                cur = [pts]
        elif cur:
            lines.append(np.vstack(cur))
            cur = []
    if cur:
        lines.append(np.vstack(cur))

    lines = [l for l in lines if len(l) >= 2]
    label = ", ".join("pen up" if s is None else f"S{s:g}" for s in sorted(down, key=lambda x: -1 if x is None else x))
    return Drawing(lines, source=Path(path).name, note=f"pen down at {label or 'nothing'}")


def _codes(line: str, letter: str) -> list[float]:
    return [float(v) for w, v in _WORD.findall(line) if w.upper() == letter]


def _infer_pen_down(moves, down_s: float | None) -> set[float | None]:
    if down_s is not None:
        return {s for s, _, _ in moves if s is not None and s >= down_s}

    states = {s for s, _, _ in moves if s is not None}
    if not states:
        # No spindle words at all: every G1 is a drawn stroke, G0 is travel.
        return {None}
    if len(states) == 1:
        return states

    stats: dict[float, list[float]] = {s: [0.0, 0.0] for s in states}
    for s, rapid, pts in moves:
        if s is None:
            continue
        length = float(np.sum(np.hypot(*np.diff(pts, axis=0).T)))
        stats[s][1 if rapid else 0] += length

    down = {s for s, (g1, g0) in stats.items() if g1 > g0}
    if not down or len(down) == len(states):
        down = {max(stats, key=lambda s: stats[s][0])}
    return down  # type: ignore[return-value]


def _arc(start, end, words, scale, clockwise, tol) -> np.ndarray:
    if "I" in words or "J" in words:
        centre = start + np.array([words.get("I", 0.0) * scale, words.get("J", 0.0) * scale])
    elif "R" in words:
        r = words["R"] * scale
        chord = end - start
        d = float(np.hypot(*chord))
        if d < 1e-9 or d > 2 * abs(r) + 1e-9:
            return np.array([start, end])
        h = math.sqrt(max(abs(r) ** 2 - (d / 2) ** 2, 0.0))
        mid = (start + end) / 2
        perp = np.array([-chord[1], chord[0]]) / d
        # Sign convention: a negative R asks for the long way round.
        sign = 1.0 if (clockwise == (r < 0)) else -1.0
        centre = mid + perp * h * sign
    else:
        return np.array([start, end])

    r = float(np.hypot(*(start - centre)))
    if r < 1e-9:
        return np.array([start, end])
    a0 = math.atan2(*(start - centre)[::-1])
    a1 = math.atan2(*(end - centre)[::-1])
    sweep = a1 - a0
    if clockwise:
        while sweep >= 0:
            sweep -= 2 * math.pi
    else:
        while sweep <= 0:
            sweep += 2 * math.pi
    if np.allclose(start, end):
        sweep = -2 * math.pi if clockwise else 2 * math.pi

    # Segment count from sagitta: max deviation of a chord from the true arc.
    step = 2 * math.acos(max(min(1 - tol / r, 1.0), -1.0)) if r > tol else math.pi / 4
    n = max(int(math.ceil(abs(sweep) / max(step, 1e-3))), 2)
    ang = a0 + sweep * np.linspace(0.0, 1.0, n + 1)
    return np.column_stack([centre[0] + r * np.cos(ang), centre[1] + r * np.sin(ang)])


# ------------------------------------------------------------------- optimise
def optimize(
    drawing: Drawing,
    merge_mm: float = 0.1,
    simplify_mm: float = 0.05,
    sort: bool = True,
) -> Drawing:
    """Join, sort and simplify. This is where plot time and quality actually come from.

    Merging removes the pen lift between paths that already share an endpoint,
    sorting cuts the pen-up travel, and simplifying drops points that are within
    tolerance of a straight line - which matters because every retained point is a
    planner block and a potential deceleration.
    """
    import vpype

    if not drawing.lines:
        return drawing

    lc = vpype.LineCollection([l[:, 0] + 1j * l[:, 1] for l in drawing.lines])
    if merge_mm > 0:
        lc.merge(tolerance=merge_mm, flip=True)
    if simplify_mm > 0:
        lc = _simplify(lc, simplify_mm)
    if sort:
        lc = _sort(lc)

    return Drawing(
        [np.column_stack([l.real, l.imag]) for l in lc],
        source=drawing.source,
        note=drawing.note,
    )


def _simplify(lc, tolerance: float):
    import vpype
    from shapely.geometry import LineString

    out = []
    for line in lc:
        if len(line) < 3:
            out.append(line)
            continue
        simp = LineString(np.column_stack([line.real, line.imag])).simplify(tolerance)
        pts = np.asarray(simp.coords)
        out.append(pts[:, 0] + 1j * pts[:, 1])
    return vpype.LineCollection(out)


def _sort(lc):
    """Greedy nearest-neighbour ordering, allowing each path to be reversed."""
    import vpype

    if len(lc) < 2:
        return lc
    index = vpype.LineIndex(lc.lines, reverse=True)
    out = vpype.LineCollection()
    first = index.pop_front()
    if first is None:
        return lc
    out.append(first)
    while len(index) > 0:
        idx, reverse = index.find_nearest(out[-1][-1])
        if idx is None:
            break
        line = index.pop(idx)
        if line is None:
            break
        out.append(np.flip(line) if reverse else line)
    return out


# -------------------------------------------------------------------- placing
@dataclass
class Placement:
    fit_mode: str = "fit to sheet"
    margin_mm: float = 10.0
    rotation: str = "auto"
    anchor: str = "centre"
    scale_pct: float = 100.0
    target_mm: float = 100.0  # used by the "width mm" / "height mm" modes
    offset_x: float = 0.0
    offset_y: float = 0.0
    mirror_x: bool = False


@dataclass
class PlaceResult:
    drawing: Drawing
    scale: float
    rotation_deg: float
    size: tuple[float, float]
    origin: tuple[float, float]
    warnings: list[str] = field(default_factory=list)


def place(drawing: Drawing, paper_w: float, paper_h: float, p: Placement) -> PlaceResult:
    """Scale, orient and position artwork inside the sheet. Returns paper mm."""
    if not drawing.lines:
        return PlaceResult(drawing, 1.0, 0.0, (0.0, 0.0), (0.0, 0.0), ["nothing to place"])

    box_w = max(paper_w - 2 * p.margin_mm, 1.0)
    box_h = max(paper_h - 2 * p.margin_mm, 1.0)
    w, h = drawing.size()
    w, h = max(w, 1e-6), max(h, 1e-6)

    if p.rotation == "auto":
        # Turn the artwork upright only if that lets it be drawn larger.
        upright = min(box_w / w, box_h / h)
        turned = min(box_w / h, box_h / w)
        rot = 90.0 if turned > upright * 1.001 else 0.0
    else:
        rot = float(p.rotation)

    lines = _rotate(drawing.lines, rot)
    rw, rh = (h, w) if rot in (90.0, 270.0) else (w, h)

    if p.fit_mode == "actual size":
        s = 1.0
    elif p.fit_mode == "scale %":
        s = max(p.scale_pct, 0.01) / 100.0
    elif p.fit_mode == "width mm":
        s = max(p.target_mm, 0.01) / rw
    elif p.fit_mode == "height mm":
        s = max(p.target_mm, 0.01) / rh
    else:
        s = min(box_w / rw, box_h / rh)

    sw, sh = rw * s, rh * s
    ax, ay = _anchor_xy(p.anchor, box_w, box_h, sw, sh)
    ox = p.margin_mm + ax + p.offset_x
    oy = p.margin_mm + ay + p.offset_y

    allp = np.vstack(lines)
    lo = np.array([allp[:, 0].min(), allp[:, 1].min()])
    out = []
    for l in lines:
        q = (l - lo) * s + np.array([ox, oy])
        if p.mirror_x:
            q[:, 0] = 2 * ox + sw - q[:, 0]
        out.append(q)

    warn: list[str] = []
    placed = Drawing(out, drawing.source, drawing.note)
    x0, y0, x1, y1 = placed.bounds()
    if x0 < -0.01 or y0 < -0.01 or x1 > paper_w + 0.01 or y1 > paper_h + 0.01:
        warn.append(
            f"artwork runs off the sheet: {x0:.1f},{y0:.1f} to {x1:.1f},{y1:.1f} mm "
            f"on a {paper_w:.0f} x {paper_h:.0f} sheet"
        )
    elif x0 < p.margin_mm - 0.01 or y0 < p.margin_mm - 0.01 \
            or x1 > paper_w - p.margin_mm + 0.01 or y1 > paper_h - p.margin_mm + 0.01:
        warn.append("artwork extends into the margin")

    return PlaceResult(placed, s, rot, (sw, sh), (ox, oy), warn)


def _rotate(lines: list[np.ndarray], deg: float) -> list[np.ndarray]:
    if deg % 360 == 0:
        return [l.copy() for l in lines]
    a = math.radians(deg)
    R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    return [l @ R.T for l in lines]


def _anchor_xy(anchor: str, box_w: float, box_h: float, w: float, h: float) -> tuple[float, float]:
    horiz = 0.0 if "left" in anchor else (box_w - w if "right" in anchor else (box_w - w) / 2)
    vert = (box_h - h) if "top" in anchor else (0.0 if "bottom" in anchor else (box_h - h) / 2)
    return horiz, vert
