# BEACON — a tiny lighthouse for your AI

> When Claude needs you, the beacon turns to you.
> 它需要你时,光会转向你。

<p align="center">
  <img src="assets/states.png" alt="Beacon's six states — working, asking, idle, done, confused, resting" width="860">
</p>

A glass-domed lighthouse diorama that floats above all your windows (even
fullscreen apps) and mirrors your **Claude Code** state in real time. Not a
pet — **a tiny world whose weather is your agent's state**.

| Claude Code | In the dome |
|---|---|
| **working** | bright day — the lamp sweeps briskly, clouds hurry |
| **asking** | **amber dusk — the beam stops and points AT YOU, pulsing; a signal flag hoists; a ship's bell rings** |
| **idle** | golden hour — slow sweep, gulls drift through |
| **done** | a rainbow over golden water |
| **confused** | a squall — rain, lightning, the beam stutters |
| **resting** | night — lamp off, stars out, one warm window in the cottage |

### The moment that matters — *asking*

When Claude hits a question or a permission prompt, the dome falls to amber
dusk, the lamp lights, a signal flag hoists, a ship's bell rings once — and
the beam stops sweeping and **turns out of the glass toward you**. A
lighthouse doing the one thing lighthouses do.

<p align="center">
  <img src="assets/asking.png" alt="The asking state — the beam turns to you" width="440">
</p>

### Several sessions at once — the fleet

With two or more Claude Code sessions running, each one gets a small
sailboat on the sea, its sail showing that session's own state (white =
working, amber = asking). The dome still shows the overall mood; the boats
tell you **which** session it is. When one starts asking, its boat sails to
the front row, nearest you, under a glowing amber sail. **Click a boat** to
jump straight to that session's terminal.

<p align="center">
  <img src="assets/fleet.png" alt="Three sessions: two working boats in the back, the asking one up front" width="440">
</p>

## Design

1. **The metaphor is the function.** A lighthouse exists to signal
   "you're needed here." The asking moment isn't an emote — it's the
   building doing the one thing lighthouses do.
2. **Transitions are weather.** The entire palette (sky/sea/light)
   crossfades over ~1.4s. Nothing swaps; dusk just falls.
3. **A diorama, not a creature.** Calm-tech: glanceable from the corner of
   your eye, zero neediness.
4. **Light-background safety by construction.** Every glow lands on the sky
   *inside* the dome. Nothing luminous ever touches your wallpaper, so it
   can't ring or plate light onto a white page.
5. **Reads your real agent state.** A small Claude Code hook
   (`hooks/beacon_hook.py`) writes one JSON file per session to
   `~/.ai-desktop-toy/sessions/`; Beacon aggregates across concurrent
   sessions: asking is sticky, a transcript-freshness guard kills false
   "asking", and priority runs asking > confused > working > done > idle >
   resting.
6. **Precision over recall.** Only a real permission prompt or a question to
   you becomes *asking*, and only an API error ending the turn becomes
   *confused*; a failing test or an idle reminder never cries wolf. One false
   alarm and an ambient tool gets closed for good.

## Run

**Requires:** macOS · Python 3.9+ · PySide6 (`pip install PySide6`)

```bash
python3 hooks/install.py   # once: registers the hook in ~/.claude/settings.json
./run.sh                   # or: python3 beacon.py
```

The installer backs up `settings.json` first, leaves your other hooks alone,
and is safe to re-run (`--dry-run` to preview, `--uninstall` to remove). New
Claude Code sessions pick the hook up. If other hooks already write
`~/.ai-desktop-toy/sessions/`, it leaves them in charge and changes nothing.

- **Drag** to move (position persists to `~/.beacon/window.json`)
- **Click** = knock on the glass: it wobbles, gulls scatter — and if Claude
  is asking, the click focuses the asking session's app. **Click a boat** to
  focus that session instead
- **Menu-bar lighthouse icon** (the reliable controls — an accessory window
  never gets key focus on macOS): Show/Hide · **Tour mode** (auto-cycles all
  six states, 6s each — watch the whole story hands-free) · Live ·
  **Force state** submenu · **Sound** (all · alerts only · off, remembered)
  · Quit. The current mode is ticked
- Keys (`1-6` force, `0` live, `T` tour, `ESC` hide) exist but only work in
  environments where the window can take focus

## Stopping it

```bash
pkill -f beacon.py
```

## Files

```
beacon.py   everything: state detection, sound synth, palettes, renderer,
            widget. Stdlib + PySide6 only.
hooks/      beacon_hook.py (the Claude Code hook, stdlib only) + install.py
run.sh      launcher
tools/      render_shots.py — regenerates the README images offscreen
tests/      headless tests: detection, fleet, hook, installer (pytest)
assets/     the rendered state images
~/.beacon/  its own prefs + generated sounds (the only place it writes)
```

Run the tests with `python3 -m pytest tests/` (needs `pip install pytest`).

## If this were a real product (next)

- A foghorn easter egg on long-idle nights.
- Seasons: the dome's weather drifts with your local time of day.
