"""The paper frame: where the sheet actually is, in machine coordinates.

You jog the pen to the physical corners of the sheet and capture each one. From
those points this builds an affine map

    (u, v) paper mm  ->  (X, Y) machine mm

so artwork is placed relative to the paper you touched rather than to a machine
origin that only exists in RAM.

Paper coordinates are millimetres from the bottom-left corner, u to the right and
v upward - the plotter convention, not the SVG one.

Why affine and not just an origin offset: a sheet dropped on the bed is never
square to the gantry, the gantry itself is never exactly square, and steps/mm is
usually a percent or two out. An affine map absorbs all three at once. Fitting to
the corners you touched means the drawing lands inside them even when the machine's
absolute scale is wrong - which is the whole point, since this machine measures
relative distance far better than it knows absolute size.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

CORNERS = ("BL", "BR", "TR", "TL")

PAPER_SIZES = {
    "A4 portrait": (210.0, 297.0),
    "A4 landscape": (297.0, 210.0),
    "A5 portrait": (148.0, 210.0),
    "A5 landscape": (210.0, 148.0),
    "A3 portrait": (297.0, 420.0),
    "Letter portrait": (215.9, 279.4),
    "Letter landscape": (279.4, 215.9),
}


def nominal(name: str, w: float, h: float) -> tuple[float, float]:
    """Paper coordinates of a named corner on a w x h sheet."""
    return {
        "BL": (0.0, 0.0),
        "BR": (w, 0.0),
        "TR": (w, h),
        "TL": (0.0, h),
    }[name]


@dataclass
class FitReport:
    ok: bool
    message: str
    n_probes: int = 0
    scale_x: float = 1.0  # machine mm reported per paper mm, along each sheet edge
    scale_y: float = 1.0
    rotation_deg: float = 0.0  # of the sheet relative to the machine X axis
    skew_deg: float = 0.0  # departure from 90 degrees between the two edges
    residual_mm: float = 0.0  # worst corner miss (4 probes only)


@dataclass
class PaperFrame:
    width: float = 210.0
    height: float = 297.0
    size_name: str = "A4 portrait"
    probes: dict[str, tuple[float, float]] = field(default_factory=dict)
    matrix: list[list[float]] | None = None  # [[a, b, tx], [c, d, ty]]
    captured_at: str = ""

    # ------------------------------------------------------------------ status
    @property
    def calibrated(self) -> bool:
        return self.matrix is not None

    def _M(self) -> np.ndarray:
        if self.matrix is None:
            raise RuntimeError("paper frame is not calibrated")
        return np.asarray(self.matrix, dtype=float)

    # ---------------------------------------------------------------- fitting
    def set_probe(self, corner: str, machine_xy: tuple[float, float]) -> FitReport:
        if corner not in CORNERS:
            raise ValueError(corner)
        self.probes[corner] = (float(machine_xy[0]), float(machine_xy[1]))
        return self.fit()

    def clear(self) -> None:
        self.probes.clear()
        self.matrix = None
        self.captured_at = ""

    def fit(self) -> FitReport:
        """Rebuild the transform from whatever corners have been captured."""
        names = [c for c in CORNERS if c in self.probes]
        n = len(names)
        if n < 2:
            self.matrix = None
            return FitReport(False, f"{n} of 3 corners captured", n)

        src = np.array([nominal(c, self.width, self.height) for c in names], dtype=float)
        dst = np.array([self.probes[c] for c in names], dtype=float)

        if n == 2:
            M = self._fit_similarity(src, dst)
            note = "2 corners: rotation and uniform scale only - capture a third for skew"
        else:
            # [u v 1] @ P = [X Y], solved in the least-squares sense. Exact for 3.
            A = np.hstack([src, np.ones((n, 1))])
            P, *_ = np.linalg.lstsq(A, dst, rcond=None)
            M = P.T  # 2x3
            note = "3 corners: exact fit" if n == 3 else "4 corners: least-squares fit"

        self.matrix = M.tolist()
        self.captured_at = time.strftime("%Y-%m-%d %H:%M:%S")

        return self.describe(note)

    def describe(self, note: str = "") -> FitReport:
        """Diagnostics for the transform as it currently stands - no refitting.

        Kept separate from `fit` so that redrawing the UI can never quietly rebuild
        or discard a frame that was loaded from disk.
        """
        n = len(self.probes)
        if self.matrix is None:
            return FitReport(False, note or f"{n} of 3 corners captured", n)

        M = self._M()
        rep = FitReport(True, note or f"{n} corners", n)
        ex, ey = M[:, 0], M[:, 1]
        rep.scale_x = float(np.hypot(*ex))
        rep.scale_y = float(np.hypot(*ey))
        rep.rotation_deg = float(math.degrees(math.atan2(ex[1], ex[0])))
        ang = math.degrees(math.atan2(ey[1], ey[0]) - math.atan2(ex[1], ex[0]))
        rep.skew_deg = float((ang + 180) % 360 - 180) - 90.0

        if n >= 4:
            names = [c for c in CORNERS if c in self.probes]
            src = np.array([nominal(c, self.width, self.height) for c in names])
            dst = np.array([self.probes[c] for c in names])
            pred = (M[:, :2] @ src.T).T + M[:, 2]
            rep.residual_mm = float(np.max(np.hypot(*(pred - dst).T)))
        return rep

    @staticmethod
    def _fit_similarity(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
        """Rotation + uniform scale + translation from two point pairs."""
        du = src[1] - src[0]
        dx = dst[1] - dst[0]
        ang = math.atan2(dx[1], dx[0]) - math.atan2(du[1], du[0])
        s = float(np.linalg.norm(dx) / max(np.linalg.norm(du), 1e-9))
        R = s * np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
        t = dst[0] - R @ src[0]
        return np.hstack([R, t.reshape(2, 1)])

    # -------------------------------------------------------------- transforms
    def to_machine(self, pts: np.ndarray) -> np.ndarray:
        """(N, 2) paper mm -> (N, 2) machine mm."""
        M = self._M()
        pts = np.atleast_2d(np.asarray(pts, dtype=float))
        return pts @ M[:, :2].T + M[:, 2]

    def to_paper(self, pts: np.ndarray) -> np.ndarray:
        """(N, 2) machine mm -> (N, 2) paper mm."""
        M = self._M()
        pts = np.atleast_2d(np.asarray(pts, dtype=float))
        return np.linalg.solve(M[:, :2], (pts - M[:, 2]).T).T

    def corners_machine(self) -> np.ndarray:
        """The four sheet corners in machine mm, in BL, BR, TR, TL order."""
        return self.to_machine(
            np.array([nominal(c, self.width, self.height) for c in CORNERS])
        )

    # ---------------------------------------------------------------- anchoring
    def reanchor(self, corner: str, machine_xy: tuple[float, float]) -> str:
        """Slide an existing frame onto one freshly probed corner.

        After a power cycle or a soft reset the machine's zero has moved but the
        sheet has not, so the whole frame is off by a pure translation. Re-probing
        one corner recovers it without redoing the calibration.
        """
        if not self.calibrated:
            return "no frame to re-anchor - capture the corners first"
        M = self._M()
        want = np.asarray(nominal(corner, self.width, self.height), dtype=float)
        cur = M[:, :2] @ want + M[:, 2]
        delta = np.asarray(machine_xy, dtype=float) - cur
        M[:, 2] += delta
        self.matrix = M.tolist()
        self.probes = {
            c: tuple(np.asarray(p) + delta) for c, p in self.probes.items()  # type: ignore[misc]
        }
        self.probes[corner] = (float(machine_xy[0]), float(machine_xy[1]))
        return f"frame moved by X{delta[0]:+.2f} Y{delta[1]:+.2f} mm"

    def resize(self, name: str, w: float, h: float) -> FitReport:
        """Change the sheet size, keeping the probes and refitting."""
        self.size_name = name
        self.width, self.height = float(w), float(h)
        return self.fit()

    # ------------------------------------------------------------------- files
    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(
                {
                    "size_name": self.size_name,
                    "width": self.width,
                    "height": self.height,
                    "probes": {k: list(v) for k, v in self.probes.items()},
                    "matrix": self.matrix,
                    "captured_at": self.captured_at,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "PaperFrame":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        f = cls(
            width=float(d.get("width", 210.0)),
            height=float(d.get("height", 297.0)),
            size_name=d.get("size_name", "A4 portrait"),
            probes={k: tuple(v) for k, v in d.get("probes", {}).items()},
            matrix=d.get("matrix"),
            captured_at=d.get("captured_at", ""),
        )
        return f
