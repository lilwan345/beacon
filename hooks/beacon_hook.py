#!/usr/bin/env python3
"""Claude Code hook -> the session file Beacon reads.

Claude Code runs this on each lifecycle event with the event JSON on stdin.
It writes ~/.ai-desktop-toy/sessions/<session_id>.json:

    {"state": "working", "ts": 1727000000.0, "session": "...",
     "transcript": "/path/to/transcript.jsonl", "cwd": "...",
     "term": "iTerm.app", "iterm_id": "...", "tty": "/dev/ttys003",
     "app_id": "com.googlecode.iterm2"}

The terminal fields let a click on Beacon jump back to the right window.

Strictly side-effect free for Claude: stdlib only, prints nothing, always
exits 0, and never raises (a broken hook must not get in Claude's way).
Registered by `python3 hooks/install.py`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SESSIONS_DIR = Path.home() / ".ai-desktop-toy" / "sessions"
CLEANUP_AFTER_SEC = 86400.0

# Event -> state. Tuned for precision over recall: only a real permission
# prompt or a question to the user becomes "asking", and only an API error
# ending the turn becomes "confused" (a failing tool call is normal work).
EVENT_STATE = {
    "SessionStart": "idle",
    "UserPromptSubmit": "working",
    "PreToolUse": "working",
    "PostToolUse": "working",
    "PostToolUseFailure": "working",
    "PermissionRequest": "asking",
    "SubagentStart": "working",
    "SubagentStop": "working",
    "PreCompact": "working",
    "PostCompact": "working",
    "Stop": "done",
    "StopFailure": "confused",
}
QUESTION_TOOLS = {"AskUserQuestion"}          # Claude asking you something
ASKING_NOTIFICATIONS = {"permission_prompt", "elicitation_dialog",
                        "elicitation_url_dialog", "agent_needs_input"}


def state_for(event: dict) -> str | None:
    """The state an event implies, or None to leave the file unchanged."""
    name = event.get("hook_event_name", "")
    if name == "PreToolUse" and event.get("tool_name") in QUESTION_TOOLS:
        return "asking"
    if name == "Notification":
        kind = event.get("notification_type") or ""
        if kind in ASKING_NOTIFICATIONS:
            return "asking"
        if not kind and "permission" in str(event.get("message", "")).lower():
            return "asking"
        return None                            # idle reminders etc.: no change
    return EVENT_STATE.get(name)


def _tty() -> str:
    """The controlling terminal of the nearest ancestor that has one."""
    pid = os.getppid()
    for _ in range(6):
        try:
            out = subprocess.run(["ps", "-o", "ppid=,tty=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=1).stdout.split()
        except (OSError, subprocess.SubprocessError):
            return ""
        if len(out) < 2:
            return ""
        ppid, tty = out[0], out[1]
        if tty not in ("?", "??", "-"):
            return tty if tty.startswith("/dev/") else f"/dev/{tty}"
        pid = int(ppid)
        if pid <= 1:
            return ""
    return ""


def terminal_info() -> dict:
    env = os.environ
    info = {"term": env.get("TERM_PROGRAM", ""),
            "app_id": env.get("__CFBundleIdentifier", "")}
    iterm = env.get("ITERM_SESSION_ID", "")    # "w0t1p0:UUID"; AppleScript wants the UUID
    if iterm:
        info["iterm_id"] = iterm.split(":", 1)[-1]
    if info["term"] == "Apple_Terminal":
        info["tty"] = _tty()
    return info


def handle(event: dict, sessions_dir: Path = SESSIONS_DIR) -> None:
    session = str(event.get("session_id") or "")
    if not session or "/" in session or session.startswith("."):
        return
    path = sessions_dir / f"{session}.json"
    if event.get("hook_event_name") == "SessionEnd":
        path.unlink(missing_ok=True)
        return
    state = state_for(event)
    if state is None:
        return
    rec = {"state": state, "ts": time.time(), "session": session,
           "transcript": event.get("transcript_path", ""),
           "cwd": event.get("cwd", ""), **terminal_info()}
    sessions_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=sessions_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(rec, f)
        os.replace(tmp, path)                  # atomic: Beacon never reads half a file
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    if event.get("hook_event_name") == "SessionStart":
        _sweep(sessions_dir)


def _sweep(sessions_dir: Path) -> None:
    """Drop files left by sessions that died without a SessionEnd (>1 day old)."""
    cutoff = time.time() - CLEANUP_AFTER_SEC
    for f in sessions_dir.glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def main() -> None:
    try:
        handle(json.loads(sys.stdin.read() or "{}"))
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
