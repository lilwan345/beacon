#!/usr/bin/env python3
"""Render Beacon's six states to high-res PNGs for the README.

Offscreen, deterministic, no GUI: imports Beacon's own `render_scene`, drives
each state's animation to a flattering frame, supersamples, and writes:

    assets/states.png   a labeled 3x2 grid of all six states (README hero)
    assets/asking.png   the signature "beam turns to you" moment, full size

Run:  QT_QPA_PLATFORM=offscreen python3 tools/render_shots.py
"""

from __future__ import annotations

import os
import random
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import (
    QImage, QPainter, QColor, QBrush, QLinearGradient, QFont,
)
from PySide6.QtWidgets import QApplication

import beacon as B

ASSETS = Path(__file__).resolve().parent.parent / "assets"

# state -> (label, tagline, settle seconds, force a lightning bolt)
TILES = [
    ("working",  "WORKING",  "bright day · sweeping briskly",   2.0, False),
    ("asking",   "ASKING",   "the beam turns to you",           4.6, False),
    ("idle",     "IDLE",     "golden hour · slow sweep",        2.6, False),
    ("done",     "DONE",     "a rainbow over golden water",     2.0, False),
    ("confused", "CONFUSED", "a squall · the beam stutters",    2.3, True),
    ("resting",  "RESTING",  "night · one warm window",         2.0, False),
]


def render_scene_img(state: str, scale: float, settle: float,
                     force_bolt: bool) -> QImage:
    """Step the animation to a settled frame, then supersample-render it."""
    A = B.Anim(seed=7)
    random.seed(abs(hash(state)) % 9973)        # deterministic gulls/clouds
    pal = B.PALETTES[state]
    t, dt = 0.0, 1.0 / 120.0
    for _ in range(int(settle / dt)):
        t += dt
        B.update_anim(A, state, t, dt, pal)
    if force_bolt:                              # freeze mid-flash so a bolt shows
        A.flash_t0 = t - 0.04
        A.bolt_seed = 3

    img = QImage(round(B.W * scale), round(B.H * scale),
                 QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(scale, scale)
    B.render_scene(p, state, t, A, pal, "")
    p.end()
    return img


def build_grid(scale: float = 1.8) -> QImage:
    sw, sh = round(B.W * scale), round(B.H * scale)
    label_h, gap, margin, cols = 56, 30, 44, 3
    rows = (len(TILES) + cols - 1) // cols
    cell_w, cell_h = sw, sh + label_h
    cw = margin * 2 + cell_w * cols + gap * (cols - 1)
    ch = margin * 2 + cell_h * rows + gap * (rows - 1)

    canvas = QImage(cw, ch, QImage.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)
    cp = QPainter(canvas)
    cp.setRenderHint(QPainter.Antialiasing, True)
    cp.setRenderHint(QPainter.TextAntialiasing, True)

    bg = QLinearGradient(0, 0, 0, ch)
    bg.setColorAt(0.0, QColor(20, 22, 33))
    bg.setColorAt(1.0, QColor(9, 11, 17))
    cp.setPen(Qt.NoPen)
    cp.setBrush(QBrush(bg))
    cp.drawRect(0, 0, cw, ch)

    for i, (state, name, tag, settle, bolt) in enumerate(TILES):
        c, r = i % cols, i // cols
        x = margin + c * (cell_w + gap)
        y = margin + r * (cell_h + gap)

        cp.setPen(Qt.NoPen)
        cp.setBrush(QColor(255, 255, 255, 10))          # faint card
        cp.drawRoundedRect(QRectF(x - 10, y - 8, cell_w + 20, cell_h + 12), 18, 18)

        cp.drawImage(x, y, render_scene_img(state, scale, settle, bolt))

        cp.setPen(QColor(238, 242, 250))
        f = QFont("Helvetica Neue", -1, QFont.Bold)
        f.setPixelSize(27)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 2.0)
        cp.setFont(f)
        cp.drawText(QRectF(x, y + sh + 6, cell_w, 30),
                    Qt.AlignHCenter | Qt.AlignTop, name)

        cp.setPen(QColor(150, 162, 184))
        f2 = QFont("Helvetica Neue")
        f2.setPixelSize(18)
        cp.setFont(f2)
        cp.drawText(QRectF(x, y + sh + 36, cell_w, 22),
                    Qt.AlignHCenter | Qt.AlignTop, tag)
    cp.end()
    return canvas


def build_beauty(state: str, scale: float = 2.8, pad: int = 70) -> QImage:
    settle, bolt = next((s, b) for st, _, _, s, b in TILES if st == state)
    scene = render_scene_img(state, scale, settle, bolt)
    cw, ch = scene.width() + pad * 2, scene.height() + pad * 2
    canvas = QImage(cw, ch, QImage.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)
    cp = QPainter(canvas)
    cp.setRenderHint(QPainter.Antialiasing, True)
    bg = QLinearGradient(0, 0, 0, ch)
    bg.setColorAt(0.0, QColor(22, 24, 36))
    bg.setColorAt(1.0, QColor(9, 11, 17))
    cp.setPen(Qt.NoPen)
    cp.setBrush(QBrush(bg))
    cp.drawRoundedRect(QRectF(0, 0, cw, ch), 28, 28)
    cp.drawImage(pad, pad, scene)
    cp.end()
    return canvas


def main() -> None:
    QApplication([])
    ASSETS.mkdir(exist_ok=True)
    grid = build_grid()
    grid.save(str(ASSETS / "states.png"))
    print(f"wrote assets/states.png  {grid.width()}x{grid.height()}")
    beauty = build_beauty("asking")
    beauty.save(str(ASSETS / "asking.png"))
    print(f"wrote assets/asking.png  {beauty.width()}x{beauty.height()}")


if __name__ == "__main__":
    main()
