#!/usr/bin/env python3
"""BEACON — a tiny lighthouse for your AI agent.

A glass-domed lighthouse diorama floats above all your windows and mirrors
your Claude Code state, read from the same filesystem hooks as the Wisp
(~/.ai-desktop-toy/sessions/*.json). The metaphor IS the function: a
lighthouse exists to signal "you're needed here."

    working   bright day — the lamp sweeps the sea briskly, clouds hurry
    asking    AMBER DUSK — the beam stops and TURNS TO YOU, pulsing wide;
              a signal flag hoists; a ship's bell rings
    idle      golden hour — slow sweep, gulls drift through
    done      celebration — golden sky and a rainbow over the water
    confused  a squall — rain, lightning, the beam stutters
    resting   night — lamp off, stars out, one warm window in the cottage

Design notes (vs. a character pet):
  * It's a diorama, not a creature — calm-tech: glanceable, never needy.
  * Transitions are weather: the whole palette crossfades over ~1.4s, so
    state changes melt like time-of-day instead of swapping sprites.
  * Light-background safety BY CONSTRUCTION: every glow lands on the sky
    INSIDE the dome. Nothing luminous ever touches the desktop wallpaper,
    so it can't ring/plate on white pages (a hard-learned Wisp lesson).

Keys (when focused): 1-6 force a state, 0 live, T tour (auto-cycle), ESC hide.
Click = knock on the glass (it wobbles; if Claude is asking, the click also
focuses the asking session's app). Drag to move; position persists.

Self-contained: stdlib + PySide6. Writes only to ~/.beacon/.
"""

from __future__ import annotations

import json
import math
import os
import random
import struct
import subprocess
import threading
import time
import wave as wavemod
from math import sin, cos, pi, exp
from pathlib import Path
from typing import Iterator, Optional

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import (
    QPainter, QColor, QBrush, QPen, QLinearGradient, QRadialGradient,
    QPainterPath, QFont, QPixmap, QIcon, QPolygonF, QAction,
)
from PySide6.QtWidgets import QApplication, QWidget, QSystemTrayIcon, QMenu

# ---------------------------------------------------------------- paths / geo
SESSIONS_DIR = Path.home() / ".ai-desktop-toy" / "sessions"   # shared truth
HOME = Path.home() / ".beacon"
SOUND_DIR = HOME / "sounds"
WINDOW_JSON = HOME / "window.json"

W, H = 264, 292
GCX, GCY, GR = 132.0, 142.0, 112.0          # globe center + radius
SEA_Y = GCY + GR * 0.30                      # waterline inside the globe
FRAME_MS = 33
STATES = ["idle", "working", "asking", "done", "confused", "resting"]

# ------------------------------------------------------------------ detection
# Mirrors ai-desktop-toy/detect.py semantics exactly (same session files, same
# staleness windows, same priority, same false-asking transcript guard) so the
# two products can never disagree about what Claude is doing.
TRANSCRIPT_FRESH_SEC = 15.0
HOOK_GRACE_SEC = 5.0
WORKING_FALLBACK_SEC = 300.0
ASKING_STALE_SEC = 86400.0
IDLE_STALE_SEC = 30.0
CLEANUP_AFTER_SEC = 86400.0
EVENT_STALE_SEC = 6.0
_EVENT_STATES = ("confused", "done")
_PRIORITY = {"asking": 3, "confused": 2.5, "working": 2, "done": 1.5,
             "idle": 1, "resting": 0}


def _effective_states(now: float) -> Iterator[tuple]:
    if not SESSIONS_DIR.exists():
        return
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text())
            ts = float(d["ts"])
            state = str(d["state"])
            transcript = str(d.get("transcript", ""))
        except (json.JSONDecodeError, KeyError, OSError, ValueError, TypeError):
            continue
        age = now - ts
        try:
            tmtime = os.path.getmtime(transcript) if transcript else 0.0
        except OSError:
            tmtime = 0.0
        fresh = (now - tmtime) <= TRANSCRIPT_FRESH_SEC if tmtime else False
        after_event = tmtime > ts + 0.5

        eff: Optional[str] = None
        if state == "asking":
            if age > ASKING_STALE_SEC:
                continue
            if after_event:           # auto-resolved permission → not asking
                if fresh:
                    eff = "working"
                elif age <= IDLE_STALE_SEC:
                    eff = "idle"
            else:
                eff = "asking"
        elif state == "working":
            if age <= HOOK_GRACE_SEC or fresh or age <= WORKING_FALLBACK_SEC:
                eff = "working"
            elif age <= IDLE_STALE_SEC:
                eff = "idle"
        elif state in _EVENT_STATES:
            if age <= EVENT_STALE_SEC:
                eff = state
            elif age <= IDLE_STALE_SEC:
                eff = "idle"
        else:
            if age <= IDLE_STALE_SEC:
                eff = "idle"
        if eff is not None:
            yield eff, d


def detect_state() -> str:
    now = time.time()
    effs = [e for e, _ in _effective_states(now)]
    if not effs:
        return "resting"
    return max(effs, key=lambda s: _PRIORITY.get(s, 0))


def winning_session() -> Optional[dict]:
    now = time.time()
    pairs = list(_effective_states(now))
    if not pairs:
        return None
    win = max((e for e, _ in pairs), key=lambda s: _PRIORITY.get(s, 0))
    best = max((r for e, r in pairs if e == win),
               key=lambda r: float(r.get("ts", 0)))
    best = dict(best)
    best["effective_state"] = win
    return best



# ---------------------------------------------------------------------- focus
# Ported from the Wisp's focus.py (same contract): clicking the dome jumps to
# the terminal of the session driving the current state. NON-DESTRUCTIVE: only
# raises/selects existing windows; never opens folders or creates tabs.
# Writes the SAME focus_request.json the Wisp VSCode extension watches, so the
# already-installed extension focuses the exact integrated-terminal pane for
# Beacon too.
FOCUS_REQUEST = os.path.expanduser("~/.ai-desktop-toy/focus_request.json")

_ITERM_SCPT = """
on run argv
    set targetID to item 1 of argv
    tell application "iTerm2"
        repeat with w in windows
            repeat with t in tabs of w
                repeat with s in sessions of t
                    if (id of s) is targetID then
                        select s
                        select t
                        select w
                        activate
                        return true
                    end if
                end repeat
            end repeat
        end repeat
    end tell
    return false
end run
"""

_TERMINAL_SCPT = """
on run argv
    set targetTTY to item 1 of argv
    tell application "Terminal"
        repeat with w in windows
            repeat with t in tabs of w
                if (tty of t) is targetTTY then
                    set selected of t to true
                    set frontmost of w to true
                    activate
                    return true
                end if
            end repeat
        end repeat
    end tell
    return false
end run
"""


def _spawn(args) -> None:
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except Exception:
        pass


def focus_session(rec: dict) -> bool:
    """Bring the session's terminal to the front. Thread-safe; never raises."""
    if not rec:
        return False
    term = (rec.get("term") or "").strip()
    cwd = (rec.get("cwd") or "").strip()
    app_id = (rec.get("app_id") or "").strip()
    iterm_id = (rec.get("iterm_id") or "").strip()
    tty = (rec.get("tty") or "").strip()
    try:
        if term == "vscode":
            wrote = False
            try:
                os.makedirs(os.path.dirname(FOCUS_REQUEST), exist_ok=True)
                with open(FOCUS_REQUEST, "w") as f:
                    json.dump({"cwd": cwd, "ppids": rec.get("ppids", ""),
                               "session": str(rec.get("session", "")),
                               "ts": time.time()}, f)
                wrote = True
            except Exception:
                pass
            if app_id:
                _spawn(["open", "-b", app_id])
            return wrote or bool(app_id)
        if term == "iTerm.app" and iterm_id:
            _spawn(["osascript", "-e", _ITERM_SCPT, iterm_id])
            return True
        if term == "Apple_Terminal" and tty:
            _spawn(["osascript", "-e", _TERMINAL_SCPT, tty])
            return True
        if app_id:
            _spawn(["open", "-b", app_id])
            return True
    except Exception:
        return False
    return False


# --------------------------------------------------------------------- sounds
GAIN = 0.20
SR = 44100


def _write_wav(path: Path, samples) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wavemod.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        buf = bytearray(len(samples) * 2)
        for i, s in enumerate(samples):
            struct.pack_into("<h", buf, i * 2,
                             max(-32767, min(32767, int(s * 32767))))
        w.writeframes(bytes(buf))


def _env(t, dur, a, r):
    if t < a:
        return t / a
    if t > dur - r:
        return max(0.0, (dur - t) / r)
    return 1.0


def _gen_bell(dur=1.7):
    """Ship's bell, two strikes — the 'you're needed' sound."""
    n = int(SR * dur); out = [0.0] * n
    partials = [(1.0, 1.00, 2.2), (2.74, 0.45, 3.0), (4.07, 0.20, 4.2)]
    for st in (0.0, 0.5):
        for i in range(int(st * SR), n):
            t = i / SR - st
            s = sum(a * exp(-t * d) * sin(2 * pi * 620 * m * t)
                    for m, a, d in partials)
            out[i] += s * 0.55
    return [s * GAIN for s in out]


def _gen_waves(dur=1.8, swell_hz=0.9, soft=0.0):
    """Filtered noise with slow swells — the sea."""
    rng = random.Random(11); n = int(SR * dur); out = [0.0] * n; lp = 0.0
    for i in range(n):
        t = i / SR
        lp = 0.94 * lp + 0.06 * (rng.random() - 0.5)
        swell = 0.45 + 0.55 * (0.5 + 0.5 * sin(2 * pi * swell_hz * t - pi / 2))
        out[i] = lp * 2.2 * swell * _env(t, dur, 0.15, 0.3) * GAIN * (1 - soft * 0.45)
    return out


def _gen_day(dur=1.1):
    """Soft wave + two gentle ticks — the lamp machinery at work."""
    out = _gen_waves(dur, swell_hz=1.4)
    for st in (0.18, 0.52):
        for i in range(int(st * SR), min(len(out), int((st + 0.1) * SR))):
            t = i / SR - st
            out[i] += 0.25 * exp(-t * 60) * sin(2 * pi * 1900 * t) * GAIN
    return out


def _gen_done(dur=1.5):
    """Two rising chimes + a gull cry — small celebration."""
    n = int(SR * dur); out = [0.0] * n
    for st, f0 in ((0.0, 659.3), (0.18, 880.0)):
        for i in range(int(st * SR), n):
            t = i / SR - st
            out[i] += 0.4 * exp(-t * 3.2) * (sin(2 * pi * f0 * t)
                                             + 0.4 * sin(2 * pi * f0 * 2 * t))
    for i in range(int(0.5 * SR), min(n, int(1.1 * SR))):   # gull "kee-aw"
        t = i / SR - 0.5
        f = 1350 - 500 * min(1.0, t / 0.5)
        f *= 1.0 + 0.06 * sin(2 * pi * 11 * t)
        out[i] += 0.16 * sin(2 * pi * f * t) * _env(t, 0.6, 0.05, 0.2)
    return [s * GAIN for s in out]


def _gen_thunder(dur=1.7):
    rng = random.Random(5); n = int(SR * dur); out = [0.0] * n
    acc = 0.0
    for i in range(n):
        t = i / SR
        acc = 0.985 * acc + 0.015 * (rng.random() - 0.5)   # brown-ish rumble
        s = acc * 14.0 * exp(-t * 2.0) + 0.25 * exp(-t * 2.5) * sin(2 * pi * 52 * t)
        out[i] = s * _env(t, dur, 0.01, 0.4) * GAIN
    return out


def _gen_night(dur=2.1):
    out = _gen_waves(dur, swell_hz=0.55, soft=1.0)
    rng = random.Random(3); bp = 0.0; prev = 0.0
    for i in range(len(out)):                              # faint wind
        t = i / SR
        w = rng.random() - 0.5
        bp = 0.985 * bp + 0.015 * w
        hp = bp - prev; prev = bp
        out[i] += hp * 5.0 * (0.4 + 0.6 * (0.5 + 0.5 * sin(2 * pi * 0.33 * t))) * GAIN
    return out


def _gen_knock(dur=0.2):
    n = int(SR * dur); out = [0.0] * n
    for i in range(n):
        t = i / SR
        out[i] = (0.6 * exp(-t * 42) * sin(2 * pi * 1850 * t)
                  + 0.3 * exp(-t * 55) * sin(2 * pi * 2600 * t)) * GAIN
    return out


SOUND_GENS = {"idle": _gen_waves, "working": _gen_day, "asking": _gen_bell,
              "done": _gen_done, "confused": _gen_thunder,
              "resting": _gen_night, "knock": _gen_knock}


def ensure_sounds() -> dict:
    paths = {}
    for name, gen in SOUND_GENS.items():
        p = SOUND_DIR / f"{name}.wav"
        if not p.exists():
            _write_wav(p, gen())
        paths[name] = p
    return paths


def play(paths: dict, name: str) -> None:
    p = paths.get(name)
    if p is not None:
        try:
            subprocess.Popen(["afplay", str(p)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


# ------------------------------------------------------------------- palettes
# Everything that differs between states lives here and CROSSFADES — that's the
# whole transition system. Colors are (r,g,b); scalars are floats.
PALETTES = {
    "idle": dict(
        sky_top=(108, 148, 198), sky_bot=(250, 198, 148),
        sea_top=(46, 84, 124), sea_bot=(96, 142, 172), sea_hi=(196, 216, 226),
        cloud=(255, 235, 214), cloud_a=0.80, cloud_sp=0.35,
        sun_a=1.0, sun_y=0.64, sun_col=(255, 208, 138),
        star_a=0.0, beam=(255, 240, 198), beam_a=0.42, beam_sp=0.55,
        lamp_a=0.9, window_a=0.25, flag_a=0.0, rain_a=0.0, rainbow_a=0.0,
        gull=1.0, amb=1.0,
    ),
    "working": dict(
        sky_top=(116, 172, 224), sky_bot=(198, 228, 244),
        sea_top=(52, 104, 148), sea_bot=(108, 168, 194), sea_hi=(214, 236, 244),
        cloud=(255, 255, 255), cloud_a=0.90, cloud_sp=1.05,
        sun_a=0.9, sun_y=0.24, sun_col=(255, 250, 228),
        star_a=0.0, beam=(232, 248, 255), beam_a=0.55, beam_sp=1.30,
        lamp_a=1.0, window_a=0.10, flag_a=0.0, rain_a=0.0, rainbow_a=0.0,
        gull=1.0, amb=1.05,
    ),
    "asking": dict(
        sky_top=(72, 70, 112), sky_bot=(255, 168, 88),
        sea_top=(44, 54, 92), sea_bot=(138, 108, 92), sea_hi=(234, 178, 118),
        cloud=(94, 82, 112), cloud_a=0.70, cloud_sp=0.5,
        sun_a=0.45, sun_y=0.80, sun_col=(255, 158, 78),
        star_a=0.15, beam=(255, 188, 88), beam_a=0.85, beam_sp=0.0,
        lamp_a=1.3, window_a=0.5, flag_a=1.0, rain_a=0.0, rainbow_a=0.0,
        gull=0.0, amb=0.85,
    ),
    "done": dict(
        sky_top=(146, 188, 232), sky_bot=(255, 224, 168),
        sea_top=(58, 110, 150), sea_bot=(148, 188, 200), sea_hi=(238, 238, 212),
        cloud=(255, 250, 234), cloud_a=0.80, cloud_sp=0.6,
        sun_a=1.0, sun_y=0.34, sun_col=(255, 234, 168),
        star_a=0.0, beam=(255, 244, 198), beam_a=0.40, beam_sp=0.8,
        lamp_a=1.0, window_a=0.2, flag_a=0.0, rain_a=0.0, rainbow_a=1.0,
        gull=1.0, amb=1.1,
    ),
    "confused": dict(
        sky_top=(44, 54, 74), sky_bot=(96, 110, 130),
        sea_top=(24, 40, 58), sea_bot=(68, 88, 104), sea_hi=(138, 158, 168),
        cloud=(58, 68, 88), cloud_a=1.0, cloud_sp=1.7,
        sun_a=0.0, sun_y=0.3, sun_col=(255, 255, 255),
        star_a=0.0, beam=(198, 224, 255), beam_a=0.50, beam_sp=1.2,
        lamp_a=1.1, window_a=0.6, flag_a=0.0, rain_a=1.0, rainbow_a=0.0,
        gull=0.0, amb=0.6,
    ),
    "resting": dict(
        sky_top=(12, 18, 44), sky_bot=(40, 56, 96),
        sea_top=(10, 20, 40), sea_bot=(36, 56, 82), sea_hi=(72, 96, 126),
        cloud=(30, 40, 60), cloud_a=0.5, cloud_sp=0.15,
        sun_a=0.0, sun_y=0.5, sun_col=(255, 255, 255),
        star_a=1.0, beam=(255, 240, 198), beam_a=0.0, beam_sp=0.0,
        lamp_a=0.0, window_a=0.95, flag_a=0.0, rain_a=0.0, rainbow_a=0.0,
        gull=0.0, amb=0.35,
    ),
}


def _lerp(a, b, f):
    return a + (b - a) * f


def blend_pal(pa: dict, pb: dict, f: float) -> dict:
    out = {}
    for k, va in pa.items():
        vb = pb[k]
        if isinstance(va, tuple):
            out[k] = tuple(int(_lerp(va[i], vb[i], f)) for i in range(3))
        else:
            out[k] = _lerp(va, vb, f)
    return out


def C(rgb, a=255) -> QColor:
    return QColor(rgb[0], rgb[1], rgb[2], int(max(0, min(255, a))))


def mul(rgb, m) -> tuple:
    return (int(min(255, rgb[0] * m)), int(min(255, rgb[1] * m)),
            int(min(255, rgb[2] * m)))


# ----------------------------------------------------------------- anim state
ASK_TH = 0.62          # beam angle (rad, y-down screen coords) pointing at YOU


class Anim:
    """All mutable scene state. Owned by the widget; the renderer reads it."""

    def __init__(self, seed=7):
        rng = random.Random(seed)
        self.clouds = [dict(x=rng.uniform(-1, 1), y=rng.uniform(0.10, 0.42),
                            s=rng.uniform(0.6, 1.25), sp=rng.uniform(0.7, 1.3),
                            layer=rng.choice((0, 1)))
                       for _ in range(6)]
        self.stars = []
        while len(self.stars) < 46:
            x, y = rng.uniform(-0.95, 0.95), rng.uniform(-0.95, 0.1)
            if x * x + y * y < 0.92:
                self.stars.append((x, y, rng.uniform(0, 6.3)))
        self.rain = [dict(x=rng.uniform(-1, 1), y=rng.uniform(-1, 1),
                          sp=rng.uniform(330, 520), ln=rng.uniform(7, 13))
                     for _ in range(56)]
        self.gulls = []
        self.next_gull = 0.0
        self.beam_th = 2.6
        self.beam_v = 0.0
        self.flag = 0.0
        self.wobble_t0 = -9.0
        self.wobble_amp = 0.0
        self.flash_t0 = -9.0
        self.next_flash = 0.0
        self.bolt_seed = 0


def update_anim(A: Anim, state: str, t: float, dt: float, pal: dict) -> None:
    # Clouds drift; wrap around the dome.
    for c in A.clouds:
        c["x"] += dt * 0.05 * c["sp"] * (0.3 + pal["cloud_sp"])
        if c["x"] > 1.35:
            c["x"] = -1.35
    # Gulls — only in day-ish states.
    if pal["gull"] > 0.5 and t >= A.next_gull and len(A.gulls) < 2:
        d = random.choice((-1, 1))
        A.gulls.append(dict(x=-1.3 * d, dir=d, y=random.uniform(0.05, 0.30),
                            sp=random.uniform(0.16, 0.24), ph=random.uniform(0, 6)))
        A.next_gull = t + random.uniform(9, 18)
    for g in A.gulls[:]:
        g["x"] += g["dir"] * g["sp"] * dt
        if abs(g["x"]) > 1.4:
            A.gulls.remove(g)
    # Rain falls (drawn × rain_a, so it can advance unconditionally).
    for r in A.rain:
        r["y"] += r["sp"] / GR * dt * 0.5
        r["x"] -= dt * 0.18
        if r["y"] > 1.1:
            r["y"] -= 2.2
        if r["x"] < -1.1:
            r["x"] += 2.2
    # Lightning scheduling — confused only.
    if state == "confused" and t >= A.next_flash:
        A.flash_t0 = t
        A.bolt_seed += 1
        A.next_flash = t + random.uniform(1.6, 3.4)
    # The beam. asking → spring-aim AT the viewer; otherwise sweep.
    if state == "asking":
        # Critically-damped-ish spring to the "look at you" angle.
        err = (ASK_TH - A.beam_th + pi) % (2 * pi) - pi
        A.beam_v += err * 26.0 * dt - A.beam_v * 9.0 * dt
        A.beam_th += A.beam_v * dt
    else:
        sp = pal["beam_sp"]
        if state == "confused":
            sp += 1.5 * sin(t * 9.0)            # stutter, jerky
        A.beam_th = (A.beam_th + sp * dt) % (2 * pi)
        A.beam_v = 0.0
    # Signal flag hoists while asking.
    target = 1.0 if state == "asking" else 0.0
    A.flag += (target - A.flag) * min(1.0, dt * 3.2)


# ------------------------------------------------------------------- renderer
def _globe_path() -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(QPointF(GCX, GCY), GR, GR)
    return path


def render_scene(p: QPainter, state: str, t: float, A: Anim, pal: dict,
                 caption: str = "") -> None:
    """Pure draw — testable offscreen. Painter is the full W×H widget."""
    p.setPen(Qt.NoPen)

    # Stand geometry hangs off the globe bottom so dome + base always seat
    # together. GB = globe bottom y.
    GB = GCY + GR

    # Ground shadow (dark = safe on any wallpaper).
    sh = QRadialGradient(GCX, GB + 27.0, 78)
    c0 = QColor(10, 14, 20); c0.setAlphaF(0.22)
    c1 = QColor(10, 14, 20); c1.setAlphaF(0.0)
    sh.setColorAt(0, c0); sh.setColorAt(1, c1)
    p.setBrush(QBrush(sh))
    p.drawEllipse(QPointF(GCX, GB + 27.0), 78, 7)

    # Walnut foot (the brass collar is drawn AFTER the glass so it overlaps
    # and visually grips the dome bottom).
    p.setBrush(QBrush(QColor(62, 44, 34)))
    p.drawPolygon(QPolygonF([QPointF(GCX - 56, GB + 25), QPointF(GCX + 56, GB + 25),
                             QPointF(GCX + 44, GB + 9), QPointF(GCX - 44, GB + 9)]))
    p.setBrush(QBrush(QColor(46, 32, 25)))
    p.drawEllipse(QPointF(GCX, GB + 25), 58, 7)

    # ---- everything luminous lives INSIDE the dome ----
    p.save()
    p.setClipPath(_globe_path())

    # Sky.
    sky = QLinearGradient(0, GCY - GR, 0, SEA_Y)
    sky.setColorAt(0.0, C(pal["sky_top"]))
    sky.setColorAt(1.0, C(pal["sky_bot"]))
    p.setBrush(QBrush(sky))
    p.drawRect(QRectF(GCX - GR, GCY - GR, GR * 2, GR * 2))

    # Sun (disc + soft in-sky glow).
    if pal["sun_a"] > 0.02:
        sx = GCX + GR * 0.42
        sy = GCY - GR + pal["sun_y"] * (SEA_Y - (GCY - GR))
        glow = QRadialGradient(sx, sy, 34)
        glow.setColorAt(0, C(pal["sun_col"], 120 * pal["sun_a"]))
        glow.setColorAt(1, C(pal["sun_col"], 0))
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(sx, sy), 34, 34)
        p.setBrush(QBrush(C(pal["sun_col"], 235 * pal["sun_a"])))
        p.drawEllipse(QPointF(sx, sy), 8.5, 8.5)

    # Moon + stars (night).
    if pal["star_a"] > 0.02:
        a = pal["star_a"]
        if a >= 0.5:   # moon only when truly night — the crescent "bite" disc
            mx, my = GCX - GR * 0.42, GCY - GR * 0.52   # would show on dusk skies
            p.setBrush(QBrush(QColor(236, 238, 224, int(220 * a))))
            p.drawEllipse(QPointF(mx, my), 9, 9)
            p.setBrush(QBrush(C(pal["sky_top"], 255)))   # bite = crescent
            p.drawEllipse(QPointF(mx + 4.2, my - 2.4), 8.2, 8.2)
        for x, y, ph in A.stars:
            tw = 0.55 + 0.45 * sin(t * 1.7 + ph)
            p.setBrush(QBrush(QColor(255, 255, 255, int(200 * a * tw))))
            sxx, syy = GCX + x * GR, GCY + y * GR
            if syy < SEA_Y - 6:
                p.drawEllipse(QPointF(sxx, syy), 1.1, 1.1)

    # Far clouds (behind the rainbow + island).
    def draw_cloud(c, front: bool):
        if (c["layer"] == 1) != front:
            return
        cx = GCX + c["x"] * GR * 1.1
        cy = GCY - GR + c["y"] * (SEA_Y - (GCY - GR)) + 6 * sin(t * 0.3 + c["x"] * 3)
        s = c["s"] * (1.15 if front else 0.85)
        a = pal["cloud_a"] * (0.9 if front else 0.62)
        p.setBrush(QBrush(C(pal["cloud"], 200 * a)))
        for dx, dy, r in ((-14, 2, 9), (0, -3, 13), (13, 2, 9), (4, 4, 10)):
            p.drawEllipse(QPointF(cx + dx * s, cy + dy * s), r * s, r * s * 0.78)

    for c in A.clouds:
        draw_cloud(c, front=False)

    # Rainbow (done) — arcs over the sea.
    if pal["rainbow_a"] > 0.02:
        cols = [(255, 90, 90), (255, 170, 80), (255, 230, 110),
                (120, 210, 130), (110, 160, 235), (150, 110, 220)]
        ra = pal["rainbow_a"]
        p.setBrush(Qt.NoBrush)
        for i, col in enumerate(cols):
            r = GR * 0.86 - i * 4.6
            pen = QPen(C(col, 70 * ra)); pen.setWidthF(4.6)
            p.setPen(pen)
            p.drawArc(QRectF(GCX - r, SEA_Y - r, r * 2, r * 2), 25 * 16, 130 * 16)
        p.setPen(Qt.NoPen)

    # Sea.
    sea = QLinearGradient(0, SEA_Y, 0, GCY + GR)
    sea.setColorAt(0.0, C(pal["sea_top"]))
    sea.setColorAt(1.0, C(pal["sea_bot"]))
    p.setBrush(QBrush(sea))
    p.drawRect(QRectF(GCX - GR, SEA_Y, GR * 2, GR))
    # Wave strips.
    for k in range(3):
        base = SEA_Y + 7 + k * 13
        path = QPainterPath(QPointF(GCX - GR, base))
        x = GCX - GR
        while x <= GCX + GR:
            path.lineTo(x, base + 2.8 * sin(x * 0.05 + t * (1.1 + 0.3 * k) + k * 1.7))
            x += 7
        path.lineTo(GCX + GR, base + 11)
        path.lineTo(GCX - GR, base + 11)
        path.closeSubpath()
        p.setBrush(QBrush(C(pal["sea_hi"], 46 - k * 10)))
        p.drawPath(path)

    # ---- island + cottage + lighthouse ----
    amb = pal["amb"]
    ix, iy = GCX, SEA_Y + 4
    rock = QPainterPath(QPointF(ix - GR * 0.52, iy + 13))
    for px, py in ((-0.52, 0.0), (-0.38, -0.085), (-0.22, -0.05), (-0.05, -0.10),
                   (0.14, -0.06), (0.30, -0.095), (0.45, -0.03), (0.52, 0.0)):
        rock.lineTo(ix + GR * px, iy + GR * py)
    rock.lineTo(ix + GR * 0.52, iy + 13)
    rock.closeSubpath()
    p.setBrush(QBrush(C(mul((64, 70, 82), amb))))
    p.drawPath(rock)
    grass = QPainterPath(QPointF(ix - GR * 0.40, iy - GR * 0.062))
    for px, py in ((-0.40, -0.062), (-0.22, -0.075), (-0.05, -0.115),
                   (0.14, -0.082), (0.30, -0.110), (0.42, -0.045)):
        grass.lineTo(ix + GR * px, iy + GR * py)
    grass.lineTo(ix + GR * 0.42, iy - GR * 0.012)
    grass.lineTo(ix - GR * 0.40, iy - GR * 0.022)
    grass.closeSubpath()
    p.setBrush(QBrush(C(mul((96, 142, 92), amb))))
    p.drawPath(grass)
    # Cottage (left), with the warm window that carries 'resting'.
    hx, hy = ix - GR * 0.30, iy - GR * 0.10
    p.setBrush(QBrush(C(mul((168, 148, 124), amb))))
    p.drawRect(QRectF(hx - 15, hy - 16, 30, 16))
    p.setBrush(QBrush(C(mul((122, 76, 62), amb))))
    p.drawPolygon(QPolygonF([QPointF(hx - 18, hy - 16), QPointF(hx + 18, hy - 16),
                             QPointF(hx, hy - 27)]))
    if pal["window_a"] > 0.02:
        p.setBrush(QBrush(C((255, 214, 130), 235 * pal["window_a"])))
        p.drawRoundedRect(QRectF(hx - 7, hy - 12, 6.5, 7.5), 1.5, 1.5)
    p.setBrush(QBrush(C(mul((84, 60, 48), amb))))
    p.drawRoundedRect(QRectF(hx + 3, hy - 11, 6, 11), 1.5, 1.5)

    # Lighthouse tower (right of center).
    tx = ix + GR * 0.14
    base_y = iy - GR * 0.09
    top_y = base_y - GR * 0.62
    tower = QPolygonF([QPointF(tx - 13, base_y), QPointF(tx + 13, base_y),
                       QPointF(tx + 8.5, top_y), QPointF(tx - 8.5, top_y)])
    p.setBrush(QBrush(C(mul((238, 236, 228), amb))))
    p.drawPolygon(tower)
    # Two red bands (interpolate the taper).
    for frac in (0.30, 0.66):
        yb = base_y + (top_y - base_y) * frac
        hw = 13 + (8.5 - 13) * frac
        bh = GR * 0.085
        hw2 = 13 + (8.5 - 13) * min(1.0, frac + bh / (base_y - top_y))
        p.setBrush(QBrush(C(mul((196, 74, 64), amb))))
        p.drawPolygon(QPolygonF([QPointF(tx - hw, yb), QPointF(tx + hw, yb),
                                 QPointF(tx + hw2, yb - bh), QPointF(tx - hw2, yb - bh)]))
    # Gallery + lamp room.
    p.setBrush(QBrush(C(mul((70, 76, 88), amb))))
    p.drawRoundedRect(QRectF(tx - 11.5, top_y - 4, 23, 4.5), 1.5, 1.5)
    lamp_y = top_y - 10.5
    p.setBrush(QBrush(C(mul((58, 64, 76), amb))))
    p.drawRect(QRectF(tx - 7.5, top_y - 13, 15, 9.5))
    if pal["lamp_a"] > 0.02:                     # the lamp itself
        la = min(1.0, pal["lamp_a"])
        g = QRadialGradient(tx, lamp_y, 13)
        g.setColorAt(0, C(pal["beam"], 235 * la))
        g.setColorAt(1, C(pal["beam"], 0))
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(tx, lamp_y), 13, 13)
        p.setBrush(QBrush(C(mul(pal["beam"], 1.15), 245 * la)))
        p.drawEllipse(QPointF(tx, lamp_y), 4.2, 4.2)
    p.setBrush(QBrush(C(mul((180, 70, 60), amb))))   # cap
    p.drawPolygon(QPolygonF([QPointF(tx - 8.5, top_y - 13), QPointF(tx + 8.5, top_y - 13),
                             QPointF(tx, top_y - 21)]))

    # ---- THE BEAM ----
    if pal["beam_a"] > 0.02 and pal["lamp_a"] > 0.02:
        th = A.beam_th
        pulse = 1.0
        hw = 0.105
        if state == "asking":
            pulse = 0.74 + 0.26 * sin(t * 2 * pi * 1.3)
            hw = 0.165
        L = GR * 1.9
        d0 = QPointF(cos(th), sin(th))
        e1 = QPointF(cos(th + hw), sin(th + hw))
        e2 = QPointF(cos(th - hw), sin(th - hw))
        org = QPointF(tx, lamp_y)
        beam_a = pal["beam_a"] * pulse
        for ddir, fa in ((1.0, 1.0), (-1.0, 0.38)):
            if state == "asking" and ddir < 0:
                continue                          # single decisive beam at YOU
            poly = QPolygonF([org,
                              QPointF(org.x() + ddir * L * e1.x(),
                                      org.y() + ddir * L * e1.y()),
                              QPointF(org.x() + ddir * L * e2.x(),
                                      org.y() + ddir * L * e2.y())])
            grad = QLinearGradient(org, QPointF(org.x() + ddir * L * d0.x(),
                                                org.y() + ddir * L * d0.y()))
            grad.setColorAt(0.0, C(pal["beam"], 215 * beam_a * fa))
            grad.setColorAt(0.45, C(pal["beam"], 110 * beam_a * fa))
            grad.setColorAt(1.0, C(pal["beam"], 0))
            p.setBrush(QBrush(grad))
            p.drawPolygon(poly)

    # Near clouds (in front of the island for depth).
    for c in A.clouds:
        draw_cloud(c, front=True)

    # Rain (confused).
    if pal["rain_a"] > 0.02:
        pen = QPen(QColor(190, 210, 230, int(120 * pal["rain_a"])))
        pen.setWidthF(1.1)
        p.setPen(pen)
        for r in A.rain:
            rx, ry = GCX + r["x"] * GR, GCY + r["y"] * GR
            p.drawLine(QPointF(rx, ry), QPointF(rx - 2.4, ry + r["ln"]))
        p.setPen(Qt.NoPen)

    # Lightning — sky flash + one jagged bolt.
    fdt = t - A.flash_t0
    if 0.0 <= fdt <= 0.22 and pal["rain_a"] > 0.3:
        fa = exp(-fdt * 16)
        p.setBrush(QBrush(QColor(255, 255, 255, int(90 * fa))))
        p.drawRect(QRectF(GCX - GR, GCY - GR, GR * 2, GR * 2))
        rng = random.Random(A.bolt_seed)
        bx = GCX + rng.uniform(-0.55, 0.55) * GR
        pen = QPen(QColor(255, 255, 230, int(230 * fa)))
        pen.setWidthF(1.8)
        p.setPen(pen)
        y0 = GCY - GR * 0.72
        pts = [QPointF(bx, y0)]
        while pts[-1].y() < SEA_Y - 4:
            last = pts[-1]
            pts.append(QPointF(last.x() + rng.uniform(-9, 9),
                               last.y() + rng.uniform(9, 17)))
        for i in range(len(pts) - 1):
            p.drawLine(pts[i], pts[i + 1])
        p.setPen(Qt.NoPen)

    # Gulls.
    if pal["gull"] > 0.05:
        pen = QPen(C(mul((40, 50, 60), 1.0), int(210 * pal["gull"])))
        pen.setWidthF(1.6)
        p.setPen(pen)
        for g in A.gulls:
            gx = GCX + g["x"] * GR
            gy = GCY - GR * 0.45 + g["y"] * GR + 2.5 * sin(t * 2 + g["ph"])
            flap = 3.2 * sin(t * 7 + g["ph"])
            for sgn in (-1, 1):
                p.drawLine(QPointF(gx, gy),
                           QPointF(gx + sgn * 5.5, gy - 2.5 - flap * 0.4))
        p.setPen(Qt.NoPen)

    p.restore()   # ---- end dome clip ----

    # Signal flag on the gallery (hoists while asking). Drawn after the clip
    # but geometrically inside the dome.
    if A.flag > 0.03 and pal["flag_a"] > 0.02:
        fa = A.flag * pal["flag_a"]
        mast_top = top_y - 34
        p.setPen(QPen(C(mul((70, 76, 88), amb), 230), 1.4))
        p.drawLine(QPointF(tx + 9, top_y - 13), QPointF(tx + 9, mast_top))
        p.setPen(Qt.NoPen)
        hoist_y = top_y - 13 + (mast_top - (top_y - 13)) * A.flag
        wavef = sin(t * 6.0) * 2.2
        flagpoly = QPolygonF([QPointF(tx + 9, hoist_y),
                              QPointF(tx + 9 + 14, hoist_y + 2 + wavef * 0.4),
                              QPointF(tx + 9, hoist_y + 8)])
        p.setBrush(QBrush(C((255, 170, 60), 240 * fa)))
        p.drawPolygon(flagpoly)

    # Glass: rim + specular. ON the dome shape = safe over any wallpaper.
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(30, 45, 62, 120), 2.4))
    p.drawEllipse(QPointF(GCX, GCY), GR, GR)
    p.setPen(QPen(QColor(255, 255, 255, 46), 1.2))
    p.drawEllipse(QPointF(GCX, GCY), GR - 2.2, GR - 2.2)
    spec = QPainterPath()
    spec.moveTo(GCX - GR * 0.62, GCY - GR * 0.55)
    spec.quadTo(GCX - GR * 0.30, GCY - GR * 0.88,
                GCX + GR * 0.10, GCY - GR * 0.80)
    p.setPen(QPen(QColor(255, 255, 255, 60), 5.5, Qt.SolidLine, Qt.RoundCap))
    p.drawPath(spec)
    p.setPen(QPen(QColor(255, 255, 255, 30), 2.5, Qt.SolidLine, Qt.RoundCap))
    spec2 = QPainterPath()
    spec2.moveTo(GCX - GR * 0.72, GCY - GR * 0.28)
    spec2.quadTo(GCX - GR * 0.78, GCY - GR * 0.02,
                 GCX - GR * 0.62, GCY + GR * 0.30)
    p.drawPath(spec2)
    p.setPen(Qt.NoPen)

    # Brass collar — over the glass so the dome reads as seated, not floating.
    collar = QLinearGradient(GCX - 50, 0, GCX + 50, 0)
    collar.setColorAt(0.0, QColor(120, 92, 48))
    collar.setColorAt(0.5, QColor(196, 158, 92))
    collar.setColorAt(1.0, QColor(110, 84, 44))
    p.setBrush(QBrush(collar))
    p.drawRoundedRect(QRectF(GCX - 50, GB - 7, 100, 16), 6, 6)
    p.setBrush(QBrush(QColor(60, 44, 26, 90)))   # seam shading
    p.drawRoundedRect(QRectF(GCX - 50, GB - 7, 100, 4), 2, 2)

    # Caption chip (forced / tour modes only).
    if caption:
        f = QFont("Menlo", 9); f.setBold(True)
        p.setFont(f)
        wpx = len(caption) * 7 + 16
        rect = QRectF(GCX - wpx / 2, 6, wpx, 16)
        p.setBrush(QBrush(QColor(18, 28, 40, 150)))
        p.drawRoundedRect(rect, 8, 8)
        p.setPen(QColor(225, 238, 248, 220))
        p.drawText(rect, Qt.AlignCenter, caption)
        p.setPen(Qt.NoPen)


# --------------------------------------------------------------------- widget
class Beacon(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(W, H)
        self.setFocusPolicy(Qt.StrongFocus)

        self._t0 = time.time()
        self._last_frame = self._t0
        self.A = Anim()
        self.sounds = ensure_sounds()

        self._live = detect_state()
        self._shown = self._live
        self._forced: Optional[str] = None
        self._tour = False
        self._tour_i = 0
        self._tour_next = 0.0
        self._pal_src = dict(PALETTES[self._shown])
        self._pal_dst = PALETTES[self._shown]
        self._fade_t0 = -9.0
        self._next_poll = 0.0

        self._press_pos = None
        self._press_global = None
        self._dragging = False

        self._restore_position()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(FRAME_MS)
        self._level_timer = QTimer(self)
        self._level_timer.timeout.connect(self._apply_macos_level)
        self._level_timer.start(500)
        self._apply_macos_level()

    # ----- macOS: float above fullscreen, every Space (same recipe as Wisp:
    # resolve OUR NSWindow via winId — nsapp.windows() can miss Tool windows —
    # then the killer pair HidesOnDeactivate/CanHide=False + orderFront).
    def _apply_macos_level(self):
        try:
            import objc
            from ctypes import c_void_p
            from AppKit import (
                NSApplication,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
                NSWindowCollectionBehaviorIgnoresCycle,
            )
        except Exception:
            return
        try:
            NSApplication.sharedApplication().setActivationPolicy_(1)
            view = objc.objc_object(c_void_p=int(self.winId()))
            nswindow = view.window()
            if nswindow is None:
                return
            nswindow.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary
                | NSWindowCollectionBehaviorIgnoresCycle)
            nswindow.setLevel_(1000)            # NSScreenSaverWindowLevel
            nswindow.setHidesOnDeactivate_(False)
            nswindow.setCanHide_(False)
            nswindow.orderFrontRegardless()
        except Exception:
            pass

    # ----- position persistence
    def _restore_position(self):
        try:
            d = json.loads(WINDOW_JSON.read_text())
            self.move(int(d["x"]), int(d["y"]))
            return
        except Exception:
            pass
        scr = QApplication.primaryScreen()
        if scr:
            g = scr.availableGeometry()
            self.move(g.right() - W - 36, g.bottom() - H - 30)

    def _save_position(self):
        try:
            HOME.mkdir(parents=True, exist_ok=True)
            WINDOW_JSON.write_text(json.dumps(
                {"x": self.x(), "y": self.y()}))
        except OSError:
            pass

    # ----- state plumbing
    def _display_state(self, now: float) -> str:
        if self._tour:
            if now >= self._tour_next:
                self._tour_i = (self._tour_i + 1) % len(STATES)
                self._tour_next = now + 6.0
            return STATES[self._tour_i]
        if self._forced:
            return self._forced
        if now >= self._next_poll:
            self._live = detect_state()
            self._next_poll = now + 0.5
        return self._live

    def _begin_fade(self, new_state: str, now: float):
        # Snapshot the CURRENT blend as the fade source → re-targeting
        # mid-fade is seamless.
        self._pal_src = self._current_pal(now)
        self._pal_dst = PALETTES[new_state]
        self._fade_t0 = now
        play(self.sounds, new_state)

    def _current_pal(self, now: float) -> dict:
        f = (now - self._fade_t0) / 1.4
        if f >= 1.0:
            return dict(self._pal_dst)
        f = max(0.0, min(1.0, f))
        f = f * f * (3 - 2 * f)
        return blend_pal(self._pal_src, self._pal_dst, f)

    # ----- interactions
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._press_pos = ev.position().toPoint()
            self._press_global = ev.globalPosition().toPoint()
            self._dragging = False

    def mouseMoveEvent(self, ev):
        if self._press_pos is None:
            return
        delta = ev.globalPosition().toPoint() - self._press_global
        if not self._dragging and delta.manhattanLength() > 4:
            self._dragging = True
        if self._dragging:
            self.move(ev.globalPosition().toPoint() - self._press_pos)

    def mouseReleaseEvent(self, ev):
        if self._press_pos is None:
            return
        if self._dragging:
            self._save_position()
            self.A.wobble_t0 = time.time() - self._t0
            self.A.wobble_amp = 2.2
        else:
            self._knock()
        self._press_pos = None
        self._dragging = False

    def _knock(self):
        now = time.time()
        self.A.wobble_t0 = now - self._t0
        self.A.wobble_amp = 4.0
        for g in self.A.gulls:               # gulls scatter
            g["sp"] *= 3.0
        play(self.sounds, "knock")
        # Jump to the terminal of the session driving the current state —
        # the asking one is exactly the terminal that needs you. Same focus
        # contract as the Wisp (iTerm UUID / Terminal tty / VSCode extension).
        rec = winning_session()
        if rec:
            threading.Thread(target=focus_session, args=(rec,),
                             daemon=True).start()

    def keyPressEvent(self, ev):
        k = ev.key()
        if k == Qt.Key_Escape:
            self.hide()
        elif Qt.Key_1 <= k <= Qt.Key_6:
            self._forced = STATES[k - Qt.Key_1]
            self._tour = False
        elif k == Qt.Key_0:
            self._forced = None
            self._tour = False
        elif k == Qt.Key_T:
            self._tour = not self._tour
            self._forced = None
            self._tour_next = 0.0

    # ----- paint
    def paintEvent(self, ev):
        now = time.time()
        t = now - self._t0
        state = self._display_state(now)
        if state != self._shown:
            self._begin_fade(state, now)
            self._shown = state
        pal = self._current_pal(now)
        dt = max(0.0, min(0.06, now - self._last_frame))
        self._last_frame = now
        update_anim(self.A, state, t, dt, pal)

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        # Snow-globe knock wobble — rotate the whole object a touch.
        wdt = t - self.A.wobble_t0
        if 0.0 <= wdt <= 1.4 and self.A.wobble_amp > 0:
            ang = self.A.wobble_amp * exp(-wdt * 3.4) * sin(wdt * 13.0)
            pivot_y = GCY + GR + 18
            p.translate(GCX, pivot_y)
            p.rotate(ang)
            p.translate(-GCX, -pivot_y)

        caption = ""
        if self._tour:
            caption = f"TOUR · {state.upper()}"
        elif self._forced:
            caption = f"FORCED · {state.upper()}"
        render_scene(p, state, t, self.A, pal, caption)
        p.end()


# ----------------------------------------------------------------------- tray
def _tray_icon() -> QIcon:
    pm = QPixmap(22, 22)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(235, 235, 230)))
    p.drawPolygon(QPolygonF([QPointF(8, 19), QPointF(14, 19),
                             QPointF(12.6, 7), QPointF(9.4, 7)]))
    p.setBrush(QBrush(QColor(255, 196, 90)))
    p.drawEllipse(QPointF(11, 5), 2.6, 2.6)
    p.setBrush(QBrush(QColor(200, 80, 70)))
    p.drawRect(QRectF(8.8, 12, 4.4, 2.6))
    p.end()
    return QIcon(pm)


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    w = Beacon()
    w.show()

    tray = QSystemTrayIcon(_tray_icon())
    menu = QMenu()
    act_show = QAction("Show / Hide")
    act_show.triggered.connect(lambda: w.setVisible(not w.isVisible()))
    act_tour = QAction("Tour mode (auto-cycle states)")
    act_tour.triggered.connect(lambda: (setattr(w, "_tour", not w._tour),
                                        setattr(w, "_forced", None),
                                        setattr(w, "_tour_next", 0.0)))
    act_live = QAction("Live mode")
    act_live.triggered.connect(lambda: (setattr(w, "_forced", None),
                                        setattr(w, "_tour", False)))
    act_quit = QAction("Quit Beacon")
    act_quit.triggered.connect(app.quit)
    force_menu = QMenu("Force state")
    force_actions = []
    def _mk_force(st):
        def f():
            w._forced = st
            w._tour = False
        return f
    for st in STATES:
        fa = QAction(st.capitalize())
        fa.triggered.connect(_mk_force(st))
        force_menu.addAction(fa)
        force_actions.append(fa)
    for a in (act_show, act_tour, act_live):
        menu.addAction(a)
    menu.addMenu(force_menu)
    menu.addSeparator()
    menu.addAction(act_quit)
    tray.setContextMenu(menu)
    tray.setToolTip("Beacon — a tiny lighthouse for your AI")
    tray._refs = (act_show, act_tour, act_live, act_quit,
                  force_menu, force_actions)   # keep from GC
    tray.show()

    app.exec()


if __name__ == "__main__":
    main()
