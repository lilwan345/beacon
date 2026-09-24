#!/usr/bin/env python3
"""Register (or remove) Beacon's Claude Code hook in ~/.claude/settings.json.

    python3 hooks/install.py              # add the hook for every event Beacon uses
    python3 hooks/install.py --dry-run    # show what would change, write nothing
    python3 hooks/install.py --uninstall  # remove Beacon's entries again

Idempotent, and it keeps a timestamped backup of settings.json before writing.
Hooks you already have are left alone. If another set of hooks already writes
~/.ai-desktop-toy/sessions/ (the files Beacon reads), it stops instead of
doubling up, unless you pass --force.
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys
import time
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "beacon_hook.py"
SETTINGS = Path.home() / ".claude" / "settings.json"
MARKER = "beacon_hook.py"
TOOL_EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest")
OTHER_EVENTS = ("SessionStart", "UserPromptSubmit", "Notification", "SubagentStart",
                "SubagentStop", "PreCompact", "PostCompact", "Stop", "StopFailure",
                "SessionEnd")


def _commands(settings: dict):
    for groups in settings.get("hooks", {}).values():
        for group in groups if isinstance(groups, list) else []:
            for h in group.get("hooks", []) if isinstance(group, dict) else []:
                if isinstance(h, dict):
                    yield str(h.get("command", ""))


def uninstall(settings: dict) -> int:
    removed = 0
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        kept = []
        for group in hooks[event]:
            inner = [h for h in group.get("hooks", []) if MARKER not in str(h.get("command", ""))]
            removed += len(group.get("hooks", [])) - len(inner)
            if inner:
                kept.append({**group, "hooks": inner})
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        settings.pop("hooks", None)
    return removed


def install(settings: dict) -> int:
    uninstall(settings)                        # re-install cleanly (e.g. repo moved)
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(HOOK))}"
    hooks = settings.setdefault("hooks", {})
    for event in TOOL_EVENTS + OTHER_EVENTS:
        group = {"hooks": [{"type": "command", "command": command, "timeout": 5}]}
        if event in TOOL_EVENTS:
            group = {"matcher": "*", **group}
        hooks.setdefault(event, []).append(group)
    return len(TOOL_EVENTS + OTHER_EVENTS)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="install even if other hooks already write the session files")
    args = ap.parse_args()

    settings = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    before = json.dumps(settings, indent=2, sort_keys=True)

    if args.uninstall:
        n = uninstall(settings)
        msg = f"removed {n} Beacon hook entr{'y' if n == 1 else 'ies'}"
    else:
        others = [c for c in _commands(settings) if "ai-desktop-toy" in c and MARKER not in c]
        if others and not args.force:
            print("Other hooks already write ~/.ai-desktop-toy/sessions/, which Beacon reads:")
            print("  " + others[0])
            print("Beacon will work with those as-is. Nothing changed (use --force to add Beacon's own hook too).")
            return
        n = install(settings)
        msg = f"registered {HOOK.name} for {n} events"

    after = json.dumps(settings, indent=2, sort_keys=True)
    if after == before:
        print(f"{SETTINGS} already up to date.")
        return
    if args.dry_run:
        print(f"[dry run] would write {SETTINGS} ({msg}):\n{json.dumps(settings, indent=2)}")
        return
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    if SETTINGS.exists():
        backup = SETTINGS.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(SETTINGS, backup)
        print(f"backup: {backup}")
    SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"{msg} in {SETTINGS}. New Claude Code sessions pick it up.")


if __name__ == "__main__":
    main()
