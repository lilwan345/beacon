"""Headless tests: state detection, fleet layout, sounds, the hook, the installer.

Run:  python3 -m pytest tests/
"""
import importlib.util
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import beacon as B  # noqa: E402


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "hooks" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hook = _load("beacon_hook")
installer = _load("install")


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    d = tmp_path / "sessions"
    d.mkdir()
    monkeypatch.setattr(B, "SESSIONS_DIR", d)

    def write(name, state, age=0.0, transcript_age=None):
        rec = {"state": state, "ts": time.time() - age, "session": name}
        if transcript_age is not None:
            tr = tmp_path / f"{name}.jsonl"
            tr.write_text("{}")
            m = time.time() - transcript_age
            os.utime(tr, (m, m))
            rec["transcript"] = str(tr)
        (d / f"{name}.json").write_text(json.dumps(rec))
    return write


# ---------------------------------------------------------------- detection
def test_no_sessions_is_resting(sessions):
    assert B.detect_state() == "resting"


def test_priority_asking_beats_working(sessions):
    sessions("a", "working")
    sessions("b", "asking")
    assert B.detect_state() == "asking"
    assert B.winning_session()["_key"] == "b"


def test_asking_auto_resolved_when_transcript_moves_on(sessions):
    # transcript written 5s AFTER the asking event -> permission auto-resolved
    sessions("a", "asking", age=10, transcript_age=5)
    assert B.detect_state() == "working"


def test_done_is_brief_then_idle_then_gone(sessions):
    sessions("a", "done", age=1)
    assert B.detect_state() == "done"
    sessions("a", "done", age=B.EVENT_STALE_SEC + 1)
    assert B.detect_state() == "idle"
    sessions("a", "done", age=B.IDLE_STALE_SEC + 1)
    assert B.detect_state() == "resting"


def test_corrupt_file_is_ignored(sessions):
    (B.SESSIONS_DIR / "bad.json").write_text("{not json")
    sessions("a", "working")
    assert B.detect_state() == "working"


# -------------------------------------------------------------------- fleet
def _recs(*states):
    return [{"_key": f"s{i}", "effective_state": st} for i, st in enumerate(states)]


def test_single_session_has_no_fleet():
    assert B.fleet_targets(_recs("working")) == {}


def test_asking_boat_sails_to_the_front_row():
    t = B.fleet_targets(_recs("working", "asking", "idle"))
    assert len(t) == 3
    assert t["s1"][1] == B._FRONT_Y and t["s1"][2] > t["s0"][2]   # nearer + bigger
    assert t["s0"][1] == t["s2"][1] == B._BACK_Y


def test_fleet_cap_never_drops_an_asking_session():
    recs = _recs(*(["working"] * 7 + ["asking"]))
    t = B.fleet_targets(recs)
    assert len(t) == B.MAX_BOATS and "s7" in t


def test_boats_stay_inside_the_dome():
    t = B.fleet_targets(_recs(*(["working"] * 2 + ["asking"] * 3)))
    for x, y, s, _ in t.values():
        for dx in (-10 * s, 10 * s):                    # hull ends
            assert (x + dx - B.GCX) ** 2 + (y - B.GCY) ** 2 < B.GR ** 2


def test_step_fleet_fades_in_and_out():
    boats = {}
    tg = B.fleet_targets(_recs("working", "asking"))
    for _ in range(60):
        B.step_fleet(boats, tg, 1 / 30)
    assert set(boats) == {"s0", "s1"} and all(b["a"] == 1.0 for b in boats.values())
    for _ in range(60):
        B.step_fleet(boats, {}, 1 / 30)
    assert boats == {}


def test_sound_modes():
    assert B.sound_allowed("all", "working")
    assert B.sound_allowed("alerts", "asking") and not B.sound_allowed("alerts", "working")
    assert not B.sound_allowed("off", "asking")


def test_render_with_fleet_offscreen():
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    boats = {}
    for _ in range(90):
        B.step_fleet(boats, B.fleet_targets(_recs("working", "asking")), 1 / 30)
    img = QImage(B.W, B.H, QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    B.render_scene(p, "asking", 3.0, B.Anim(), B.PALETTES["asking"], "", boats)
    p.end()
    b = boats["s1"]
    assert img.pixelColor(int(b["x"]) + 4, int(b["y"] - 6 * b["s"])).alpha() == 255
    assert img.pixelColor(2, 2).alpha() == 0          # nothing drawn outside the object


# --------------------------------------------------------------------- hook
@pytest.mark.parametrize("event,expected", [
    ({"hook_event_name": "UserPromptSubmit"}, "working"),
    ({"hook_event_name": "PermissionRequest", "tool_name": "Bash"}, "asking"),
    ({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"}, "asking"),
    ({"hook_event_name": "PreToolUse", "tool_name": "Bash"}, "working"),
    ({"hook_event_name": "Notification", "notification_type": "permission_prompt"}, "asking"),
    ({"hook_event_name": "Notification", "notification_type": "idle_prompt"}, None),
    ({"hook_event_name": "Notification", "message": "Claude needs your permission to use Bash"}, "asking"),
    ({"hook_event_name": "Stop"}, "done"),
    ({"hook_event_name": "StopFailure"}, "confused"),
    ({"hook_event_name": "PostToolUseFailure"}, "working"),
    ({"hook_event_name": "SomethingNew"}, None),
])
def test_hook_state_mapping(event, expected):
    assert hook.state_for(event) == expected


def test_hook_writes_and_session_end_removes(tmp_path):
    ev = {"session_id": "abc", "hook_event_name": "PermissionRequest",
          "transcript_path": "/t.jsonl", "cwd": "/w"}
    hook.handle(ev, tmp_path)
    rec = json.loads((tmp_path / "abc.json").read_text())
    assert rec["state"] == "asking" and rec["transcript"] == "/t.jsonl"
    assert list(tmp_path.glob("*.tmp")) == []
    hook.handle({"session_id": "abc", "hook_event_name": "SessionEnd"}, tmp_path)
    assert not (tmp_path / "abc.json").exists()


def test_hook_rejects_path_like_session_ids(tmp_path):
    hook.handle({"session_id": "../x", "hook_event_name": "Stop"}, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_hook_main_is_silent_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    with pytest.raises(SystemExit) as e:
        hook.main()
    assert e.value.code == 0
    assert capsys.readouterr() == ("", "")


# ---------------------------------------------------------------- installer
def test_install_is_idempotent_and_uninstall_restores(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    original = {"model": "x", "hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "say done"}]}]}}
    settings.write_text(json.dumps(original))
    monkeypatch.setattr(installer, "SETTINGS", settings)
    monkeypatch.setattr(sys, "argv", ["install.py"])
    installer.main()
    once = json.loads(settings.read_text())
    installer.main()
    assert json.loads(settings.read_text()) == once              # idempotent
    stop_cmds = [h["command"] for g in once["hooks"]["Stop"] for h in g["hooks"]]
    assert "say done" in stop_cmds and any("beacon_hook.py" in c for c in stop_cmds)
    assert once["hooks"]["PreToolUse"][0]["matcher"] == "*"
    monkeypatch.setattr(sys, "argv", ["install.py", "--uninstall"])
    installer.main()
    assert json.loads(settings.read_text()) == original
    assert list(tmp_path.glob("settings.json.bak-*"))


def test_install_defers_to_existing_session_hooks(tmp_path, monkeypatch, capsys):
    settings = tmp_path / "settings.json"
    other = {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "python3 ~/ai-desktop-toy/hook.py"}]}]}}
    settings.write_text(json.dumps(other))
    monkeypatch.setattr(installer, "SETTINGS", settings)
    monkeypatch.setattr(sys, "argv", ["install.py"])
    installer.main()
    assert json.loads(settings.read_text()) == other
    assert "Nothing changed" in capsys.readouterr().out
