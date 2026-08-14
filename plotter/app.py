"""The plotter console: jog, probe the sheet, place artwork, plot it."""

from __future__ import annotations

import queue
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np

from . import art, emit
from .art import Drawing, Placement
from .config import CONFIG_NAME, FRAME_NAME, Config
from .frame import CORNERS, PAPER_SIZES, PaperFrame, nominal
from .grblink import RT_RESET, RT_RESUME, GrblLink

BG = "#242428"
PANEL = "#1b1b1f"
EDGE = "#3a3a42"
FG = "#dcdce0"
MUTE = "#8f8f9a"
GREEN = "#68d888"
BLUE = "#5aa0e8"
AMBER = "#e8bc68"
RED = "#e05a5a"
PAPER_FILL = "#f4f4ef"
INK = "#1a1a22"

UI = ("Segoe UI", 9)
UI_B = ("Segoe UI", 9, "bold")
MONO = ("Consolas", 9)
DRO_F = ("Consolas", 20, "bold")


class App:
    def __init__(self, root: tk.Tk, project_dir: Path) -> None:
        self.root = root
        self.dir = project_dir
        self.cfg = Config.load(self.dir / CONFIG_NAME)
        self.link = GrblLink()
        self.frame = PaperFrame()
        if (self.dir / FRAME_NAME).exists():
            try:
                self.frame = PaperFrame.load(self.dir / FRAME_NAME)
            except Exception:  # noqa: BLE001
                pass

        self.art_raw: Drawing | None = None
        self.art_opt: Drawing | None = None
        self.placed: art.PlaceResult | None = None
        self.job: emit.Job | None = None
        self.preview_lines: list[np.ndarray] = []

        self._ready = False
        self._updating = False
        self._view: tuple[float, float, float] | None = None  # scale, x offset, y offset
        self._view_auto = True  # False once the user pans or zooms
        self._drag: tuple[int, int] | None = None

        root.title("Pen Plotter Console")
        root.geometry("1340x900")
        root.minsize(1120, 700)
        root.configure(bg=BG)
        self._style()
        self._build()
        self._ready = True

        self._refresh_frame_ui()
        self._draw()
        root.after(60, self._pump)
        root.protocol("WM_DELETE_WINDOW", self._quit)
        if self.frame.calibrated:
            self.log(f"loaded paper frame from {FRAME_NAME} ({self.frame.captured_at})", "ok")
            self.log("   if the board has been reset or power-cycled since, re-anchor it.", "warn")

    # ------------------------------------------------------------------ chrome
    def _style(self) -> None:
        s = ttk.Style()
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, font=UI)
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("TLabel", background=BG, foreground=FG)
        s.configure("Mute.TLabel", background=BG, foreground=MUTE)
        s.configure("Head.TLabel", background=BG, foreground=AMBER, font=UI_B)
        s.configure("TLabelframe", background=BG, foreground=AMBER, bordercolor=EDGE)
        s.configure("TLabelframe.Label", background=BG, foreground=AMBER, font=UI_B)
        s.configure("TButton", background="#33333a", foreground=FG, borderwidth=1, focusthickness=0)
        s.map("TButton", background=[("active", "#43434c"), ("disabled", "#2a2a30")],
              foreground=[("disabled", "#5a5a62")])
        s.configure("Go.TButton", background="#2f6f3f", foreground="#eaffea", font=UI_B)
        s.map("Go.TButton", background=[("active", "#3d8a50"), ("disabled", "#2a2a30")])
        s.configure("Stop.TButton", background="#8a2f2f", foreground="#ffecec", font=UI_B)
        s.map("Stop.TButton", background=[("active", "#a83a3a")])
        s.configure("Jog.TButton", font=("Segoe UI", 11, "bold"))
        s.configure("TEntry", fieldbackground=PANEL, foreground=FG, insertcolor=FG,
                    bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE)
        s.configure("TCombobox", fieldbackground=PANEL, background="#33333a", foreground=FG,
                    arrowcolor=FG, bordercolor=EDGE)
        s.map("TCombobox", fieldbackground=[("readonly", PANEL)])
        s.configure("TCheckbutton", background=BG, foreground=FG)
        s.map("TCheckbutton", background=[("active", BG)])
        s.configure("TRadiobutton", background=BG, foreground=FG)
        s.map("TRadiobutton", background=[("active", BG)])
        s.configure("TNotebook", background=BG, bordercolor=EDGE)
        s.configure("TNotebook.Tab", background="#2c2c33", foreground=MUTE, padding=(12, 6))
        s.map("TNotebook.Tab", background=[("selected", BG)], foreground=[("selected", FG)])
        s.configure("TProgressbar", background=GREEN, troughcolor=PANEL, bordercolor=EDGE)
        s.configure("TScale", background=BG, troughcolor=PANEL)
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#3d6f9a")

    # ------------------------------------------------------------ var plumbing
    def _sv(self, value, cb=None) -> tk.StringVar:
        v = tk.StringVar(value=str(value))
        if cb:
            v.trace_add("write", lambda *_: cb())
        return v

    def _bv(self, value: bool, cb=None) -> tk.BooleanVar:
        v = tk.BooleanVar(value=value)
        if cb:
            v.trace_add("write", lambda *_: cb())
        return v

    @staticmethod
    def _f(var: tk.StringVar, default: float = 0.0) -> float:
        try:
            return float(str(var.get()).strip())
        except (ValueError, AttributeError):
            return default

    def _entry(self, parent, var, width=7, **kw):
        return ttk.Entry(parent, textvariable=var, width=width, font=MONO, **kw)

    # ------------------------------------------------------------------- build
    def _build(self) -> None:
        self._build_top()
        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        left = ttk.Frame(body, width=430)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        nb = ttk.Notebook(left)
        nb.pack(fill="both", expand=True)
        self.tab_machine = ttk.Frame(nb, padding=8)
        self.tab_paper = ttk.Frame(nb, padding=8)
        self.tab_art = ttk.Frame(nb, padding=8)
        self.tab_job = ttk.Frame(nb, padding=8)
        nb.add(self.tab_machine, text="Machine")
        nb.add(self.tab_paper, text="Paper")
        nb.add(self.tab_art, text="Artwork")
        nb.add(self.tab_job, text="Plot")

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self._build_canvas(right)

        self._build_machine()
        self._build_paper()
        self._build_art()
        self._build_job()
        self._build_log()
        self._bind_keys()

    # --------------------------------------------------------------- top strip
    def _build_top(self) -> None:
        bar = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        bar.pack(fill="x")

        ttk.Label(bar, text="Port").pack(side="left")
        self.v_port = self._sv(self.cfg.port)
        self.cbo_port = ttk.Combobox(bar, textvariable=self.v_port, width=9, font=MONO,
                                     values=GrblLink.ports())
        self.cbo_port.pack(side="left", padx=(6, 4))
        ttk.Button(bar, text="Rescan", width=7,
                   command=lambda: self.cbo_port.configure(values=GrblLink.ports())).pack(side="left")
        self.btn_conn = ttk.Button(bar, text="Connect", width=11, command=self._toggle_conn)
        self.btn_conn.pack(side="left", padx=6)

        self.lbl_state = tk.Label(bar, text="offline", bg=BG, fg=MUTE,
                                  font=("Segoe UI", 11, "bold"), width=10, anchor="w")
        self.lbl_state.pack(side="left", padx=(8, 4))

        dro = tk.Frame(bar, bg=PANEL, padx=12, pady=4)
        dro.pack(side="left", padx=8)
        self.lbl_mpos = tk.Label(dro, text="X    0.000   Y    0.000", bg=PANEL, fg=GREEN, font=DRO_F)
        self.lbl_mpos.grid(row=0, column=0, sticky="w")
        self.lbl_ppos = tk.Label(dro, text="paper  --", bg=PANEL, fg=MUTE, font=MONO)
        self.lbl_ppos.grid(row=1, column=0, sticky="w")

        ttk.Button(bar, text="STOP", style="Stop.TButton", width=8,
                   command=self._estop).pack(side="right")
        ttk.Button(bar, text="Hold", width=7, command=self.link.hold).pack(side="right", padx=4)
        ttk.Button(bar, text="Resume", width=8,
                   command=self._resume).pack(side="right")

    # ------------------------------------------------------------ machine tab
    def _build_machine(self) -> None:
        t = self.tab_machine
        jf = ttk.LabelFrame(t, text=" Jog ", padding=8)
        jf.pack(fill="x")

        row = ttk.Frame(jf)
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="Step mm").pack(side="left")
        self.v_step = self._sv(self.cfg.jog_step)
        self._entry(row, self.v_step, 6).pack(side="left", padx=(6, 10))
        for s in ("0.1", "1", "5", "10", "25", "50"):
            ttk.Button(row, text=s, width=4,
                       command=lambda v=s: self.v_step.set(v)).pack(side="left", padx=1)

        row2 = ttk.Frame(jf)
        row2.pack(fill="x", pady=(0, 8))
        ttk.Label(row2, text="Feed").pack(side="left")
        self.v_feed = self._sv(self.cfg.jog_feed)
        self._entry(row2, self.v_feed, 7).pack(side="left", padx=6)
        ttk.Label(row2, text="mm/min", style="Mute.TLabel").pack(side="left")
        ttk.Label(row2, text="arrow keys jog · PgUp/PgDn pen",
                  style="Mute.TLabel").pack(side="right")

        pad = ttk.Frame(jf)
        pad.pack()
        mk = lambda txt, r, c, cmd, st="Jog.TButton": ttk.Button(  # noqa: E731
            pad, text=txt, width=5, style=st, command=cmd
        ).grid(row=r, column=c, padx=3, pady=3, ipady=8)
        mk("Y+", 0, 1, lambda: self._jog("Y", 1))
        mk("X-", 1, 0, lambda: self._jog("X", -1))
        mk("■", 1, 1, self.link.jog_cancel)
        mk("X+", 1, 2, lambda: self._jog("X", 1))
        mk("Y-", 2, 1, lambda: self._jog("Y", -1))

        side = ttk.Frame(jf)
        side.pack(fill="x", pady=(8, 0))
        for txt, cmd in (
            ("Unlock $X", lambda: self.link.send("$X")),
            ("Home $H", lambda: self.link.send("$H")),
            ("Read $$", lambda: self.link.send("$$")),
            ("Zero work", lambda: self.link.send("G10 L20 P1 X0 Y0 Z0")),
        ):
            ttk.Button(side, text=txt, command=cmd).pack(side="left", padx=2, expand=True, fill="x")

        pf = ttk.LabelFrame(t, text=" Pen servo ", padding=8)
        pf.pack(fill="x", pady=10)
        self.v_pen = tk.IntVar(value=self.cfg.pen.down_s)
        sc = ttk.Scale(pf, from_=0, to=1000, variable=self.v_pen, orient="horizontal",
                       command=lambda _=None: self._pen_label())
        sc.pack(fill="x")
        sc.bind("<ButtonRelease-1>", lambda _e: self._pen_send())
        self.lbl_pen = ttk.Label(pf, text="", font=MONO, style="Mute.TLabel")
        self.lbl_pen.pack(anchor="w")
        self._pen_label()

        pb = ttk.Frame(pf)
        pb.pack(fill="x", pady=(6, 0))
        ttk.Button(pb, text="Send S", command=self._pen_send).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(pb, text="Pen up", command=self._pen_up).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(pb, text="Pen down", command=self._pen_down).pack(side="left", expand=True, fill="x", padx=2)
        pb2 = ttk.Frame(pf)
        pb2.pack(fill="x", pady=(4, 0))
        ttk.Button(pb2, text="↑ use as UP value",
                   command=lambda: self._pen_assign("up")).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(pb2, text="↓ use as DOWN value",
                   command=lambda: self._pen_assign("down")).pack(side="left", expand=True, fill="x", padx=2)

        rf = ttk.LabelFrame(t, text=" Recovery ", padding=8)
        rf.pack(fill="x")
        ttk.Label(rf, wraplength=380, style="Mute.TLabel",
                  text="A soft reset lifts the pen but leaves machine position uncertain. "
                       "After one, re-anchor the paper frame before plotting again.").pack(anchor="w")
        ttk.Button(rf, text="Soft reset (Ctrl-X)",
                   command=lambda: self.link.realtime(RT_RESET)).pack(anchor="w", pady=(6, 0))

    # -------------------------------------------------------------- paper tab
    def _build_paper(self) -> None:
        t = self.tab_paper
        sf = ttk.LabelFrame(t, text=" Sheet ", padding=8)
        sf.pack(fill="x")
        row = ttk.Frame(sf)
        row.pack(fill="x")
        self.v_size = self._sv(self.frame.size_name)
        cb = ttk.Combobox(row, textvariable=self.v_size, values=list(PAPER_SIZES) + ["custom"],
                          state="readonly", width=15)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda _e: self._size_pick())
        self.v_pw = self._sv(self.frame.width, self._size_edit)
        self.v_ph = self._sv(self.frame.height, self._size_edit)
        self._entry(row, self.v_pw, 6).pack(side="left", padx=(8, 2))
        ttk.Label(row, text="×").pack(side="left")
        self._entry(row, self.v_ph, 6).pack(side="left", padx=2)
        ttk.Label(row, text="mm", style="Mute.TLabel").pack(side="left")

        pf = ttk.LabelFrame(t, text=" Probe the sheet ", padding=8)
        pf.pack(fill="x", pady=10)
        ttk.Label(pf, wraplength=390, style="Mute.TLabel",
                  text="Jog the pen tip over a physical corner of the paper, then capture it. "
                       "Three corners is enough; a fourth is used as a check.").pack(anchor="w", pady=(0, 8))
        self.lbl_probe: dict[str, ttk.Label] = {}
        names = {"BL": "bottom-left", "BR": "bottom-right", "TR": "top-right", "TL": "top-left"}
        for i, c in enumerate(CORNERS):
            r = ttk.Frame(pf)
            r.pack(fill="x", pady=1)
            ttk.Button(r, text=f"Capture {c}", width=12,
                       command=lambda cc=c: self._capture(cc)).pack(side="left")
            ttk.Label(r, text=names[c], width=13, style="Mute.TLabel").pack(side="left", padx=4)
            lb = ttk.Label(r, text="—", font=MONO, width=20)
            lb.pack(side="left")
            self.lbl_probe[c] = lb
            ttk.Button(r, text="go", width=4,
                       command=lambda cc=c: self._goto_corner(cc)).pack(side="right")

        br = ttk.Frame(pf)
        br.pack(fill="x", pady=(8, 0))
        ttk.Button(br, text="Clear all", command=self._clear_probes).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(br, text="Save frame", command=self._save_frame).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(br, text="Load frame", command=self._load_frame).pack(side="left", expand=True, fill="x", padx=2)

        ff = ttk.LabelFrame(t, text=" Fit ", padding=8)
        ff.pack(fill="x")
        self.lbl_fit = ttk.Label(ff, text="not calibrated", font=MONO, justify="left", wraplength=390)
        self.lbl_fit.pack(anchor="w")

        af = ttk.LabelFrame(t, text=" Re-anchor ", padding=8)
        af.pack(fill="x", pady=10)
        ttk.Label(af, wraplength=390, style="Mute.TLabel",
                  text="After a power cycle or a reset the machine's zero moves but the sheet "
                       "does not. Re-probe one corner and the whole frame slides onto it - no "
                       "need to redo the calibration.").pack(anchor="w", pady=(0, 6))
        ar = ttk.Frame(af)
        ar.pack(fill="x")
        self.v_anchor_corner = self._sv("BL")
        ttk.Combobox(ar, textvariable=self.v_anchor_corner, values=list(CORNERS),
                     state="readonly", width=5).pack(side="left")
        ttk.Button(ar, text="Re-anchor from this corner",
                   command=self._reanchor).pack(side="left", padx=6)

    # ------------------------------------------------------------- artwork tab
    def _build_art(self) -> None:
        t = self.tab_art
        of = ttk.Frame(t)
        of.pack(fill="x")
        ttk.Button(of, text="Open SVG or G-code…", command=self._open).pack(side="left")
        self.lbl_file = ttk.Label(of, text="no file loaded", style="Mute.TLabel")
        self.lbl_file.pack(side="left", padx=8)

        lf = ttk.LabelFrame(t, text=" Layout ", padding=8)
        lf.pack(fill="x", pady=8)
        p = self.cfg.placement
        g = ttk.Frame(lf)
        g.pack(fill="x")

        ttk.Label(g, text="Size").grid(row=0, column=0, sticky="w")
        self.v_fit = self._sv(p.fit_mode, self._replace)
        ttk.Combobox(g, textvariable=self.v_fit, values=list(art.FIT_MODES), state="readonly",
                     width=13).grid(row=0, column=1, sticky="w", padx=4)
        self.v_scale = self._sv(p.scale_pct, self._replace)
        self.v_target = self._sv(p.target_mm, self._replace)
        ttk.Label(g, text="%").grid(row=0, column=2, sticky="e")
        self._entry(g, self.v_scale, 6).grid(row=0, column=3, padx=(2, 6))
        ttk.Label(g, text="mm").grid(row=0, column=4, sticky="e")
        self._entry(g, self.v_target, 6).grid(row=0, column=5, padx=2)

        ttk.Label(g, text="Margin").grid(row=1, column=0, sticky="w", pady=4)
        self.v_margin = self._sv(p.margin_mm, self._replace)
        self._entry(g, self.v_margin, 6).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(g, text="Rotate").grid(row=1, column=2, sticky="e")
        self.v_rot = self._sv(p.rotation, self._replace)
        ttk.Combobox(g, textvariable=self.v_rot, values=list(art.ROTATIONS), state="readonly",
                     width=6).grid(row=1, column=3, columnspan=2, sticky="w", padx=2)

        ttk.Label(g, text="Nudge").grid(row=2, column=0, sticky="w")
        self.v_dx = self._sv(p.offset_x, self._replace)
        self.v_dy = self._sv(p.offset_y, self._replace)
        self._entry(g, self.v_dx, 6).grid(row=2, column=1, sticky="w", padx=4)
        self._entry(g, self.v_dy, 6).grid(row=2, column=2, columnspan=2, sticky="w", padx=2)
        self.v_mirror = self._bv(p.mirror_x, self._replace)
        ttk.Checkbutton(g, text="mirror", variable=self.v_mirror).grid(row=2, column=4, columnspan=2, sticky="w")

        an = ttk.Frame(lf)
        an.pack(anchor="w", pady=(8, 0))
        ttk.Label(an, text="Anchor").grid(row=0, column=0, rowspan=3, padx=(0, 8))
        self.v_pos = self._sv(p.anchor, self._replace)
        for i, name in enumerate(art.ANCHORS):
            ttk.Radiobutton(an, text="", value=name, variable=self.v_pos).grid(
                row=i // 3, column=1 + i % 3, padx=2
            )
        self.lbl_anchor = ttk.Label(an, text=p.anchor, style="Mute.TLabel")
        self.lbl_anchor.grid(row=0, column=5, rowspan=3, padx=8)

        cf = ttk.LabelFrame(t, text=" Path clean-up ", padding=8)
        cf.pack(fill="x")
        o = self.cfg.optimize
        self.v_opt = self._bv(o.enabled)
        self.v_sort = self._bv(o.sort)
        self.v_merge = self._sv(o.merge_mm)
        self.v_simp = self._sv(o.simplify_mm)
        self.v_quant = self._sv(o.quantization_mm)
        r1 = ttk.Frame(cf)
        r1.pack(fill="x")
        ttk.Checkbutton(r1, text="enabled", variable=self.v_opt).pack(side="left")
        ttk.Checkbutton(r1, text="sort paths", variable=self.v_sort).pack(side="left", padx=10)
        ttk.Button(r1, text="Re-run", command=self._reoptimize).pack(side="right")
        r2 = ttk.Frame(cf)
        r2.pack(fill="x", pady=(6, 0))
        for lbl, var in (("merge", self.v_merge), ("simplify", self.v_simp), ("curve res", self.v_quant)):
            ttk.Label(r2, text=lbl).pack(side="left")
            self._entry(r2, var, 5).pack(side="left", padx=(4, 10))
        ttk.Label(cf, style="Mute.TLabel", wraplength=390, text=
                  "Merging joins paths that already share an endpoint, sorting cuts pen-up "
                  "travel, simplifying drops points inside the tolerance. Tolerances in mm; "
                  "curve resolution applies to SVG curves and G-code arcs at import.").pack(anchor="w", pady=(6, 0))

        st = ttk.LabelFrame(t, text=" Result ", padding=8)
        st.pack(fill="x", pady=8)
        self.lbl_stats = ttk.Label(st, text="—", font=MONO, justify="left")
        self.lbl_stats.pack(anchor="w")
        self.lbl_warn = ttk.Label(st, text="", foreground=AMBER, wraplength=390, justify="left")
        self.lbl_warn.pack(anchor="w", pady=(4, 0))

    # ----------------------------------------------------------------- job tab
    def _build_job(self) -> None:
        t = self.tab_job
        pen = self.cfg.pen
        pf = ttk.LabelFrame(t, text=" Pen and feeds ", padding=8)
        pf.pack(fill="x")
        g = ttk.Frame(pf)
        g.pack(fill="x")
        self.v_up_s = self._sv(pen.up_s)
        self.v_down_s = self._sv(pen.down_s)
        self.v_up_d = self._sv(pen.up_dwell)
        self.v_down_d = self._sv(pen.down_dwell)
        self.v_draw_f = self._sv(pen.draw_feed)
        self.v_trav_f = self._sv(pen.travel_feed)
        cells = (
            ("pen up S", self.v_up_s, 0, 0), ("dwell s", self.v_up_d, 0, 2),
            ("pen down S", self.v_down_s, 1, 0), ("dwell s", self.v_down_d, 1, 2),
            ("draw feed", self.v_draw_f, 2, 0), ("travel", self.v_trav_f, 2, 2),
        )
        for lbl, var, r, c in cells:
            ttk.Label(g, text=lbl).grid(row=r, column=c, sticky="e", padx=(0, 4), pady=2)
            self._entry(g, var, 7).grid(row=r, column=c + 1, sticky="w", padx=(0, 14))

        lf = ttk.LabelFrame(t, text=" Travel envelope ", padding=8)
        lf.pack(fill="x", pady=8)
        lim = self.cfg.limits
        self.v_lim_on = self._bv(lim.enabled, self._draw)
        ttk.Checkbutton(lf, text="refuse jobs that leave this box", variable=self.v_lim_on).pack(anchor="w")
        lr = ttk.Frame(lf)
        lr.pack(fill="x", pady=(4, 0))
        self.v_x0 = self._sv(lim.x_min, self._draw)
        self.v_x1 = self._sv(lim.x_max, self._draw)
        self.v_y0 = self._sv(lim.y_min, self._draw)
        self.v_y1 = self._sv(lim.y_max, self._draw)
        for lbl, var in (("X", self.v_x0), ("..", self.v_x1), ("  Y", self.v_y0), ("..", self.v_y1)):
            ttk.Label(lr, text=lbl).pack(side="left")
            self._entry(lr, var, 6).pack(side="left", padx=2)

        df = ttk.LabelFrame(t, text=" Dry run ", padding=8)
        df.pack(fill="x")
        ttk.Label(df, style="Mute.TLabel", wraplength=390,
                  text="Traces an outline with the pen up. Do this once after probing - it "
                       "costs nothing and catches a mis-clicked corner before ink does.").pack(anchor="w")
        dr = ttk.Frame(df)
        dr.pack(fill="x", pady=(6, 0))
        ttk.Button(dr, text="Outline the sheet",
                   command=lambda: self._dry(False)).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(dr, text="Outline the artwork",
                   command=lambda: self._dry(True)).pack(side="left", expand=True, fill="x", padx=2)

        rf = ttk.LabelFrame(t, text=" Run ", padding=8)
        rf.pack(fill="x", pady=8)
        self.btn_start = ttk.Button(rf, text="START PLOT", style="Go.TButton", command=self._start)
        self.btn_start.pack(fill="x", ipady=8)
        cr = ttk.Frame(rf)
        cr.pack(fill="x", pady=6)
        ttk.Button(cr, text="Pause at pen-up",
                   command=self.link.pause_at_break).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(cr, text="Resume", command=self._resume).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(cr, text="Abort", style="Stop.TButton",
                   command=self.link.abort_job).pack(side="left", expand=True, fill="x", padx=2)
        self.prog = ttk.Progressbar(rf, maximum=100)
        self.prog.pack(fill="x", pady=(2, 2))
        self.lbl_prog = ttk.Label(rf, text="idle", font=MONO, style="Mute.TLabel")
        self.lbl_prog.pack(anchor="w")
        ttk.Button(rf, text="Save G-code…", command=self._save_gcode).pack(anchor="w", pady=(8, 0))

    # -------------------------------------------------------------- log + MDI
    def _build_log(self) -> None:
        wrap = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        wrap.pack(fill="x", side="bottom")
        self.txt = tk.Text(wrap, height=6, bg=PANEL, fg=FG, font=MONO, insertbackground=FG,
                           relief="flat", highlightthickness=1, highlightbackground=EDGE, wrap="none")
        self.txt.pack(fill="both", side="top")
        self.txt.configure(state="disabled")
        for tag, col in (("info", FG), ("ok", GREEN), ("tx", BLUE), ("err", RED), ("warn", AMBER)):
            self.txt.tag_configure(tag, foreground=col)

        mdi = ttk.Frame(wrap)
        mdi.pack(fill="x", pady=(4, 0))
        ttk.Label(mdi, text="MDI").pack(side="left")
        self.v_mdi = self._sv("")
        e = self._entry(mdi, self.v_mdi, 40)
        e.pack(side="left", fill="x", expand=True, padx=6)
        e.bind("<Return>", lambda _ev: self._mdi())
        ttk.Button(mdi, text="Send", command=self._mdi).pack(side="left")

    # ---------------------------------------------------------------- canvas
    def _build_canvas(self, parent) -> None:
        head = ttk.Frame(parent)
        head.pack(fill="x")
        ttk.Label(head, text="Preview", style="Head.TLabel").pack(side="left")
        self.v_travel = self._bv(self.cfg.show_travel, self._draw)
        ttk.Checkbutton(head, text="travel moves", variable=self.v_travel).pack(side="left", padx=12)
        self.v_click = self._bv(self.cfg.click_to_jog)
        ttk.Checkbutton(head, text="right-click jogs here", variable=self.v_click).pack(side="left")
        ttk.Button(head, text="Fit view", command=self._fit_view).pack(side="right")

        self.canvas = tk.Canvas(parent, bg="#15151a", highlightthickness=1,
                                highlightbackground=EDGE)
        self.canvas.pack(fill="both", expand=True, pady=(4, 0))
        self.canvas.bind("<Configure>", self._resized)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<ButtonPress-1>", self._pan_start)
        self.canvas.bind("<B1-Motion>", self._pan_move)
        self.canvas.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag", None))
        self.canvas.bind("<Double-Button-1>", lambda _e: self._fit_view())
        self.canvas.bind("<Button-3>", self._click_jog)
        self.canvas.bind("<Motion>", self._hover)
        self.lbl_hover = ttk.Label(parent, text="", font=MONO, style="Mute.TLabel")
        self.lbl_hover.pack(anchor="w")

    def _bind_keys(self) -> None:
        def guard(fn):
            def h(ev):
                if isinstance(ev.widget, (tk.Entry, ttk.Entry, tk.Text)):
                    return None
                fn()
                return "break"
            return h

        self.root.bind("<Left>", guard(lambda: self._jog("X", -1)))
        self.root.bind("<Right>", guard(lambda: self._jog("X", 1)))
        self.root.bind("<Up>", guard(lambda: self._jog("Y", 1)))
        self.root.bind("<Down>", guard(lambda: self._jog("Y", -1)))
        self.root.bind("<Prior>", guard(self._pen_up))
        self.root.bind("<Next>", guard(self._pen_down))
        self.root.bind("<Escape>", lambda _e: self._estop())

    # ================================================================= actions
    def log(self, msg: str, kind: str = "info") -> None:
        self.txt.configure(state="normal")
        self.txt.insert("end", msg + "\n", kind)
        self.txt.see("end")
        if float(self.txt.index("end-1c").split(".")[0]) > 500:
            self.txt.delete("1.0", "200.0")
        self.txt.configure(state="disabled")

    def _toggle_conn(self) -> None:
        if self.link.connected:
            self.link.disconnect()
        else:
            self.cfg.port = self.v_port.get().strip()
            self.link.connect(self.cfg.port)

    def _resume(self) -> None:
        if self.link.job_running:
            self.link.resume()
        else:
            self.link.realtime(RT_RESUME)

    def _estop(self) -> None:
        if self.link.job_running:
            self.link.abort_job()
        else:
            self.link.jog_cancel()
            self.link.realtime(RT_RESET)
            self.log("soft reset - pen parked up, position now uncertain", "err")

    def _jog(self, axis: str, sign: int) -> None:
        self.link.jog(axis, self._f(self.v_step, 1.0) * sign, self._f(self.v_feed, 500.0))

    def _pen_label(self) -> None:
        v = int(self.v_pen.get())
        self.lbl_pen.configure(text=f"S{v:<5d} {1.0 + v / 1000.0:.2f} ms pulse")

    def _pen_send(self) -> None:
        self.link.send(f"M3 S{int(self.v_pen.get())}")

    def _pen_up(self) -> None:
        self.link.send(f"M3 S{int(self._f(self.v_up_s, 400))}")

    def _pen_down(self) -> None:
        self.link.send(f"M3 S{int(self._f(self.v_down_s, 1000))}")

    def _pen_assign(self, which: str) -> None:
        v = int(self.v_pen.get())
        (self.v_up_s if which == "up" else self.v_down_s).set(str(v))
        self.log(f"pen {which} value set to S{v}", "ok")

    def _mdi(self) -> None:
        cmd = self.v_mdi.get().strip()
        if cmd:
            self.link.send(cmd)
            self.v_mdi.set("")

    # ---------------------------------------------------------------- probing
    def _size_pick(self) -> None:
        name = self.v_size.get()
        if name in PAPER_SIZES:
            w, h = PAPER_SIZES[name]
            self.v_pw.set(f"{w:g}")
            self.v_ph.set(f"{h:g}")

    def _size_edit(self) -> None:
        if not self._ready or self._updating:
            return
        w, h = self._f(self.v_pw, 210), self._f(self.v_ph, 297)
        if w <= 0 or h <= 0:
            return
        name = self.v_size.get()
        if name in PAPER_SIZES and PAPER_SIZES[name] != (w, h):
            name = "custom"
            self.v_size.set(name)
        self.frame.resize(name, w, h)
        self._refresh_frame_ui()
        self._replace()

    def _capture(self, corner: str) -> None:
        if not self.link.connected:
            self.log("not connected", "err")
            return
        if self.link.status.state not in ("Idle", "Jog", "Check"):
            self.log(f"machine is {self.link.status.state} - capture from Idle", "err")
            return
        rep = self.frame.set_probe(corner, self.link.status.mpos)
        x, y = self.frame.probes[corner]
        self.log(f"{corner} captured at machine X{x:.3f} Y{y:.3f}  ({rep.message})", "ok")
        self._refresh_frame_ui()
        self._replace()

    def _clear_probes(self) -> None:
        self.frame.clear()
        self.log("probes cleared", "warn")
        self._refresh_frame_ui()
        self._replace()

    def _goto_corner(self, corner: str) -> None:
        if not self.frame.calibrated:
            self.log("no frame yet", "err")
            return
        x, y = self.frame.to_machine(np.array([nominal(corner, self.frame.width, self.frame.height)]))[0]
        self._pen_up()
        self.link.jog_to(x, y, self._f(self.v_feed, 800))

    def _reanchor(self) -> None:
        if not self.link.connected:
            self.log("not connected", "err")
            return
        c = self.v_anchor_corner.get()
        msg = self.frame.reanchor(c, self.link.status.mpos)
        self.log(msg, "ok")
        self._refresh_frame_ui()
        self._replace()

    def _save_frame(self) -> None:
        self.frame.save(self.dir / FRAME_NAME)
        self.log(f"paper frame saved to {FRAME_NAME}", "ok")

    def _load_frame(self) -> None:
        p = self.dir / FRAME_NAME
        if not p.exists():
            self.log(f"{FRAME_NAME} not found", "err")
            return
        self.frame = PaperFrame.load(p)
        self.log(f"paper frame loaded ({self.frame.captured_at})", "ok")
        self._refresh_frame_ui()
        self._replace()

    def _refresh_frame_ui(self) -> None:
        for c in CORNERS:
            if c in self.frame.probes:
                x, y = self.frame.probes[c]
                self.lbl_probe[c].configure(text=f"X{x:9.3f} Y{y:9.3f}", foreground=GREEN)
            else:
                self.lbl_probe[c].configure(text="—", foreground=MUTE)

        # Writing the size boxes fires their trace, which would resize the frame
        # and land back here; the guard keeps that from becoming a loop.
        self._updating = True
        try:
            self.v_size.set(self.frame.size_name)
            self.v_pw.set(f"{self.frame.width:g}")
            self.v_ph.set(f"{self.frame.height:g}")
        finally:
            self._updating = False

        rep = self.frame.describe()
        if not rep.ok:
            self.lbl_fit.configure(text=rep.message, foreground=AMBER)
            return
        parts = [
            rep.message,
            f"sheet rotation {rep.rotation_deg:+.2f}°    axis skew {rep.skew_deg:+.2f}°",
            f"scale  X {rep.scale_x:.4f} ({(rep.scale_x - 1) * 100:+.2f}%)"
            f"   Y {rep.scale_y:.4f} ({(rep.scale_y - 1) * 100:+.2f}%)",
        ]
        if rep.n_probes >= 4:
            parts.append(f"worst corner miss {rep.residual_mm:.3f} mm")
        s = self.link.settings
        if 100 in s and 101 in s:
            parts.append(
                f"implies $100={s[100] * rep.scale_x:.3f}  $101={s[101] * rep.scale_y:.3f}"
                "\n(informational - placement already compensates)"
            )
        self.lbl_fit.configure(text="\n".join(parts), foreground=FG)

    # ---------------------------------------------------------------- artwork
    def _open(self) -> None:
        path = filedialog.askopenfilename(
            title="Open artwork",
            initialdir=self.cfg.last_dir or str(self.dir),
            filetypes=[("Artwork", "*.svg *.nc *.gcode *.gc *.ngc *.tap"),
                       ("SVG", "*.svg"), ("G-code", "*.nc *.gcode *.gc *.ngc *.tap"),
                       ("All files", "*.*")],
        )
        if not path:
            return
        self.cfg.last_dir = str(Path(path).parent)
        try:
            self.art_raw = art.load(path, quantization_mm=self._f(self.v_quant, 0.05))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Import failed", str(exc))
            self.log(f"import failed: {exc}", "err")
            return
        w, h = self.art_raw.size()
        self.log(f"loaded {self.art_raw.source}: {self.art_raw.count} paths, "
                 f"{w:.1f} x {h:.1f} mm  {self.art_raw.note}", "ok")
        self.lbl_file.configure(text=self.art_raw.source, foreground=FG)
        self._reoptimize()

    def _reoptimize(self) -> None:
        if not self.art_raw:
            return
        if self.v_opt.get():
            before = self.art_raw.count
            self.art_opt = art.optimize(
                self.art_raw,
                merge_mm=self._f(self.v_merge, 0.15),
                simplify_mm=self._f(self.v_simp, 0.05),
                sort=self.v_sort.get(),
            )
            self.log(f"clean-up: {before} → {self.art_opt.count} paths, "
                     f"{self.art_opt.point_count()} points", "info")
        else:
            self.art_opt = self.art_raw
        self._replace()

    def _placement(self) -> Placement:
        return Placement(
            fit_mode=self.v_fit.get(),
            margin_mm=self._f(self.v_margin, 10),
            rotation=self.v_rot.get(),
            anchor=self.v_pos.get(),
            scale_pct=self._f(self.v_scale, 100),
            target_mm=self._f(self.v_target, 100),
            offset_x=self._f(self.v_dx, 0),
            offset_y=self._f(self.v_dy, 0),
            mirror_x=self.v_mirror.get(),
        )

    def _replace(self) -> None:
        """Re-place the artwork on the sheet and refresh everything downstream."""
        if not self._ready:
            return
        self.lbl_anchor.configure(text=self.v_pos.get())
        if not self.art_opt:
            self.placed = None
            self.preview_lines = []
            self._draw()
            return
        self.placed = art.place(self.art_opt, self.frame.width, self.frame.height, self._placement())
        d = self.placed.drawing
        w, h = d.size()
        self.lbl_stats.configure(text=(
            f"{d.count} paths, {d.point_count()} points\n"
            f"{w:.1f} x {h:.1f} mm at {self.placed.scale * 100:.1f}%"
            f"   rotated {self.placed.rotation_deg:.0f}°\n"
            f"draw {d.draw_length() / 1000:.2f} m    travel {d.travel_length() / 1000:.2f} m"
        ))
        self.lbl_warn.configure(text="\n".join(self.placed.warnings))
        self.preview_lines = (
            [self.frame.to_machine(l) for l in d.lines] if self.frame.calibrated else []
        )
        self._draw()

    # ------------------------------------------------------------------- jobs
    def _pen_cfg(self) -> emit.PenConfig:
        return emit.PenConfig(
            up_s=int(self._f(self.v_up_s, 400)),
            down_s=int(self._f(self.v_down_s, 1000)),
            up_dwell=self._f(self.v_up_d, 0.15),
            down_dwell=self._f(self.v_down_d, 0.25),
            draw_feed=self._f(self.v_draw_f, 1500),
            travel_feed=self._f(self.v_trav_f, 3000),
        )

    def _limits(self) -> emit.MachineLimits:
        return emit.MachineLimits(
            x_min=self._f(self.v_x0, -5), x_max=self._f(self.v_x1, 300),
            y_min=self._f(self.v_y0, -5), y_max=self._f(self.v_y1, 400),
            enabled=self.v_lim_on.get(),
        )

    def _make_job(self) -> emit.Job | None:
        if not self.placed or not self.placed.drawing.lines:
            messagebox.showwarning("Nothing to plot", "Load an SVG or G-code file first.")
            return None
        if not self.frame.calibrated:
            messagebox.showwarning("No paper frame",
                                   "Probe at least three corners of the sheet first.")
            return None
        job = emit.build(self.placed.drawing, self.frame, self._pen_cfg(), self._limits(),
                         header_note=f"{self.placed.drawing.source} via pen plotter console")
        self.job = job
        return job

    def _dry(self, artwork: bool) -> None:
        if not self.frame.calibrated:
            messagebox.showwarning("No paper frame", "Probe the sheet corners first.")
            return
        box = None
        if artwork:
            if not self.placed or not self.placed.drawing.lines:
                messagebox.showwarning("Nothing loaded", "Load artwork first.")
                return
            box = self.placed.drawing.bounds()
        job = emit.outline_job(self.frame, self._pen_cfg(), box)
        self._run(job, "dry run outline")

    def _start(self) -> None:
        job = self._make_job()
        if not job:
            return
        msg = (f"{job.n_paths} paths\n"
               f"{job.draw_mm / 1000:.2f} m drawn, {job.travel_mm / 1000:.2f} m travel\n"
               f"estimated {job.eta}\n\nPen down at S{self._pen_cfg().down_s}. Start plotting?")
        if job.warnings:
            msg = "WARNING\n" + "\n".join(job.warnings) + "\n\n" + msg
        if not messagebox.askokcancel("Start plot", msg, icon="warning" if job.warnings else "question"):
            return
        self._run(job, "plot")

    def _run(self, job: emit.Job, what: str) -> None:
        if job.warnings and what != "plot":
            for w in job.warnings:
                self.log(w, "warn")
        if not self.link.connected:
            messagebox.showerror("Not connected", "Connect to the controller first.")
            return
        if self.link.start_job(job.lines):
            self.log(f"{what}: {len(job.lines)} lines, est {job.eta}", "ok")

    def _save_gcode(self) -> None:
        job = self._make_job()
        if not job:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".nc", initialdir=self.cfg.last_dir or str(self.dir),
            filetypes=[("G-code", "*.nc *.gcode"), ("All files", "*.*")],
        )
        if not path:
            return
        Path(path).write_text(job.text, encoding="utf-8")
        self.log(f"wrote {len(job.lines)} lines to {path}", "ok")

    # ============================================================ event drain
    def _pump(self) -> None:
        for _ in range(200):  # bounded, so a flood cannot lock the UI out
            try:
                kind, payload = self.link.events.get_nowait()
            except queue.Empty:
                break
            try:
                if kind == "log":
                    self.log(payload[0], payload[1])  # type: ignore[index]
                elif kind == "status":
                    self._on_status(payload)
                elif kind == "conn":
                    self._on_conn(bool(payload))
                elif kind == "job":
                    self._on_job(payload)
                elif kind == "settings":
                    self._refresh_frame_ui()
                elif kind == "alarm":
                    self.log("ALARM - machine position is no longer trusted; "
                             "re-anchor the paper frame", "err")
            except Exception as exc:  # noqa: BLE001 - never let one event kill the pump
                self.log(f"ui error handling {kind}: {exc}", "err")
        self.root.after(60, self._pump)

    def _on_conn(self, up: bool) -> None:
        self.btn_conn.configure(text="Disconnect" if up else "Connect")
        if not up:
            self.lbl_state.configure(text="offline", fg=MUTE)

    def _on_status(self, st) -> None:
        col = {"Idle": GREEN, "Run": BLUE, "Jog": BLUE, "Hold": AMBER,
               "Alarm": RED, "Door": AMBER}.get(st.state, MUTE)
        self.lbl_state.configure(text=st.state.lower(), fg=col)
        x, y = st.mpos
        self.lbl_mpos.configure(text=f"X {x:8.3f}   Y {y:8.3f}")
        if self.frame.calibrated:
            u, v = self.frame.to_paper(np.array([[x, y]]))[0]
            inside = -1 <= u <= self.frame.width + 1 and -1 <= v <= self.frame.height + 1
            self.lbl_ppos.configure(text=f"paper  u {u:7.2f}  v {v:7.2f}",
                                    fg=GREEN if inside else MUTE)
        self._move_pen_marker(x, y)

    def _on_job(self, p) -> None:
        pct = (p.acked / p.total * 100) if p.total else 0
        self.prog.configure(value=pct)
        txt = f"{p.state}  {p.acked}/{p.total} lines  {pct:.0f}%"
        if p.note:
            txt += f"  — {p.note}"
        self.lbl_prog.configure(text=txt)
        if p.state in ("done", "aborted"):
            self.log(f"job {p.state}", "ok" if p.state == "done" else "err")

    # ================================================================= preview
    def _world_box(self) -> tuple[float, float, float, float]:
        boxes = []
        if self.v_lim_on.get():
            boxes.append((self._f(self.v_x0, 0), self._f(self.v_y0, 0),
                          self._f(self.v_x1, 300), self._f(self.v_y1, 400)))
        if self.frame.calibrated:
            c = self.frame.corners_machine()
            boxes.append((c[:, 0].min(), c[:, 1].min(), c[:, 0].max(), c[:, 1].max()))
        if self.preview_lines:
            allp = np.vstack(self.preview_lines)
            boxes.append((allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max()))
        if not boxes:
            return (0.0, 0.0, 300.0, 400.0)
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def _resized(self, _ev=None) -> None:
        # Until the view is panned or zoomed by hand it stays fitted, so the
        # preview is never left at the scale of a canvas that had not been laid
        # out yet.
        if self._view_auto:
            self._fit_view(redraw=False)
        self._draw()

    def _fit_view(self, redraw: bool = True) -> None:
        x0, y0, x1, y1 = self._world_box()
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 20 or ch < 20:
            self._view = None  # not laid out yet; refit on the next Configure
            return
        s = min(cw / max(x1 - x0, 1), ch / max(y1 - y0, 1)) * 0.92
        self._view = (s, (cw - (x1 - x0) * s) / 2 - x0 * s, ch - (ch - (y1 - y0) * s) / 2 + y0 * s)
        self._view_auto = True
        if redraw:
            self._draw()

    def _w2c(self, x, y):
        s, ox, oy = self._view or (1.0, 0.0, 0.0)
        return x * s + ox, oy - y * s

    def _c2w(self, cx, cy):
        s, ox, oy = self._view or (1.0, 0.0, 0.0)
        return (cx - ox) / s, (oy - cy) / s

    def _wheel(self, ev) -> None:
        wx, wy = self._c2w(ev.x, ev.y)
        s, _, _ = self._view
        s2 = s * (1.15 if ev.delta > 0 else 1 / 1.15)
        s2 = max(0.05, min(s2, 200.0))
        self._view = (s2, ev.x - wx * s2, ev.y + wy * s2)
        self._view_auto = False
        self._draw()

    def _pan_start(self, ev) -> None:
        self._drag = (ev.x, ev.y)

    def _pan_move(self, ev) -> None:
        if not self._drag or self._view is None:
            return
        s, ox, oy = self._view
        self._view = (s, ox + ev.x - self._drag[0], oy + ev.y - self._drag[1])
        self._view_auto = False
        self._drag = (ev.x, ev.y)
        self._draw()

    def _hover(self, ev) -> None:
        x, y = self._c2w(ev.x, ev.y)
        txt = f"machine  X {x:7.2f}  Y {y:7.2f}"
        if self.frame.calibrated:
            u, v = self.frame.to_paper(np.array([[x, y]]))[0]
            txt += f"      paper  u {u:7.2f}  v {v:7.2f}"
        self.lbl_hover.configure(text=txt)

    def _click_jog(self, ev) -> None:
        if not self.v_click.get() or not self.link.connected:
            return
        x, y = self._c2w(ev.x, ev.y)
        lim = self._limits()
        if lim.enabled and not (lim.x_min <= x <= lim.x_max and lim.y_min <= y <= lim.y_max):
            self.log(f"blocked: X{x:.1f} Y{y:.1f} is outside the travel envelope", "err")
            return
        self._pen_up()
        self.link.jog_to(x, y, self._f(self.v_feed, 800))

    def _move_pen_marker(self, x: float, y: float) -> None:
        cx, cy = self._w2c(x, y)
        r = 6
        if self.canvas.find_withtag("pen"):
            self.canvas.coords("penh", cx - r * 2, cy, cx + r * 2, cy)
            self.canvas.coords("penv", cx, cy - r * 2, cx, cy + r * 2)
            self.canvas.coords("pen", cx - r, cy - r, cx + r, cy + r)

    def _draw(self) -> None:
        if not self._ready:
            return
        c = self.canvas
        c.delete("all")
        cw, ch = c.winfo_width(), c.winfo_height()
        if cw < 20:
            return
        if self._view is None:
            self._fit_view(redraw=False)

        # travel envelope
        if self.v_lim_on.get():
            x0, y0 = self._w2c(self._f(self.v_x0, 0), self._f(self.v_y0, 0))
            x1, y1 = self._w2c(self._f(self.v_x1, 300), self._f(self.v_y1, 400))
            c.create_rectangle(x0, y0, x1, y1, outline="#3a3a48", dash=(6, 4))
            c.create_text(x0 + 4, y1 + 10, text="travel envelope", anchor="w",
                          fill="#4a4a58", font=("Segoe UI", 8))

        # machine origin
        ox, oy = self._w2c(0, 0)
        c.create_line(ox - 12, oy, ox + 12, oy, fill="#4a4a58")
        c.create_line(ox, oy - 12, ox, oy + 12, fill="#4a4a58")
        c.create_text(ox + 14, oy + 10, text="machine 0,0", anchor="w", fill="#4a4a58",
                      font=("Segoe UI", 8))

        if self.frame.calibrated:
            quad = [self._w2c(*p) for p in self.frame.corners_machine()]
            flat = [v for p in quad for v in p]
            c.create_polygon(*flat, fill=PAPER_FILL, outline="#9a9aa4")
            m = self._f(self.v_margin, 10)
            if m > 0:
                mb = self.frame.to_machine(np.array([
                    [m, m], [self.frame.width - m, m],
                    [self.frame.width - m, self.frame.height - m], [m, self.frame.height - m],
                ]))
                mflat = [v for p in mb for v in self._w2c(*p)]
                c.create_polygon(*mflat, fill="", outline="#c4c4b8", dash=(3, 3))
            labels = {"BL": quad[0], "BR": quad[1], "TR": quad[2], "TL": quad[3]}
            for name, (px, py) in labels.items():
                probed = name in self.frame.probes
                c.create_oval(px - 4, py - 4, px + 4, py + 4,
                              outline=GREEN if probed else "#b0b0b8",
                              fill=GREEN if probed else "")
                c.create_text(px, py - 12, text=name, fill="#70707a", font=("Segoe UI", 8, "bold"))

        # artwork
        if self.preview_lines:
            step = max(1, sum(len(l) for l in self.preview_lines) // 40000)
            prev = None
            for line in self.preview_lines:
                pts = line[::step] if step > 1 else line
                if len(pts) < 2:
                    pts = line[[0, -1]]
                flat = [v for p in pts for v in self._w2c(*p)]
                if self.v_travel.get() and prev is not None:
                    a = self._w2c(*prev)
                    b = self._w2c(*pts[0])
                    c.create_line(*a, *b, fill="#8d8dbb", dash=(2, 3))
                c.create_line(*flat, fill=INK, width=1)
                prev = line[-1]

        # pen crosshair, kept as separate items so status updates need no redraw
        px, py = self._w2c(*self.link.status.mpos)
        c.create_line(px - 12, py, px + 12, py, fill=GREEN, tags="penh")
        c.create_line(px, py - 12, px, py + 12, fill=GREEN, tags="penv")
        c.create_oval(px - 6, py - 6, px + 6, py + 6, outline=GREEN, tags="pen")

        # scale bar
        s = self._view[0]
        for mm in (100, 50, 20, 10, 5):
            if mm * s < cw * 0.35:
                break
        c.create_line(16, ch - 18, 16 + mm * s, ch - 18, fill=MUTE)
        c.create_line(16, ch - 22, 16, ch - 14, fill=MUTE)
        c.create_line(16 + mm * s, ch - 22, 16 + mm * s, ch - 14, fill=MUTE)
        c.create_text(16 + mm * s / 2, ch - 28, text=f"{mm} mm", fill=MUTE, font=("Segoe UI", 8))

    # ------------------------------------------------------------------- exit
    def _quit(self) -> None:
        if self.link.job_running and not messagebox.askokcancel(
            "Job running", "A plot is still running. Quit and abort it?"
        ):
            return
        self.cfg.port = self.v_port.get().strip()
        self.cfg.jog_step = self._f(self.v_step, 10)
        self.cfg.jog_feed = self._f(self.v_feed, 800)
        self.cfg.pen = self._pen_cfg()
        self.cfg.limits = self._limits()
        self.cfg.placement = self._placement()
        self.cfg.optimize = replace(
            self.cfg.optimize, enabled=self.v_opt.get(), sort=self.v_sort.get(),
            merge_mm=self._f(self.v_merge, 0.15), simplify_mm=self._f(self.v_simp, 0.05),
            quantization_mm=self._f(self.v_quant, 0.05),
        )
        self.cfg.show_travel = self.v_travel.get()
        self.cfg.click_to_jog = self.v_click.get()
        self.cfg.save(self.dir / CONFIG_NAME)
        if self.frame.calibrated:
            self.frame.save(self.dir / FRAME_NAME)
        try:
            self.link.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self.root.destroy()


def main(project_dir: str | Path | None = None) -> None:
    root = tk.Tk()
    App(root, Path(project_dir or Path(__file__).resolve().parent.parent))
    root.mainloop()
