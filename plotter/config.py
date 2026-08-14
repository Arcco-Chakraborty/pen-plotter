"""Persisted app settings - everything the user would hate to retype."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path

from .art import Placement
from .emit import MachineLimits, PenConfig

CONFIG_NAME = "plotter-config.json"
FRAME_NAME = "paper-frame.json"


@dataclass
class OptimizeConfig:
    enabled: bool = True
    merge_mm: float = 0.15
    simplify_mm: float = 0.05
    sort: bool = True
    quantization_mm: float = 0.05


@dataclass
class Config:
    port: str = "COM11"
    jog_step: float = 10.0
    jog_feed: float = 800.0
    probe_feed: float = 300.0
    pen: PenConfig = field(default_factory=PenConfig)
    limits: MachineLimits = field(default_factory=MachineLimits)
    placement: Placement = field(default_factory=Placement)
    optimize: OptimizeConfig = field(default_factory=OptimizeConfig)
    last_dir: str = ""
    show_travel: bool = True
    click_to_jog: bool = True

    # ------------------------------------------------------------------ files
    @classmethod
    def load(cls, path: str | Path) -> "Config":
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            return _from_dict(cls, json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 - a corrupt config must not block startup
            return cls()

    def save(self, path: str | Path) -> None:
        try:
            Path(path).write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass


def _from_dict(cls, data: dict):
    kwargs = {}
    blank = cls()
    for f in fields(cls):
        if f.name not in data:
            continue
        val = data[f.name]
        default = getattr(blank, f.name)
        if is_dataclass(default) and isinstance(val, dict):
            kwargs[f.name] = _from_dict(type(default), val)
        elif isinstance(default, tuple) and isinstance(val, list):
            kwargs[f.name] = tuple(val)
        else:
            kwargs[f.name] = val
    return cls(**kwargs)
