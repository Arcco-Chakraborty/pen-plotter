"""Pen plotter console - probe the sheet, place artwork on it, plot it.

Layout:

    grblink.py  serial link, status polling, job streaming
    frame.py    the probed paper frame and its affine transform
    art.py      SVG / G-code import, path clean-up, placement on the sheet
    emit.py     placed artwork -> machine-coordinate G-code
    config.py   persisted settings
    app.py      the Tk interface
"""

__all__ = ["app", "art", "config", "emit", "frame", "grblink"]
