"""Claude Code harness adapter (core/harnesses/__init__.py has the interface).

Persona: a SessionStart hook (matchers startup|resume|compact) in the checkout's uncommitted
.claude/settings.local.json runs core/harnesses/persona-hook.sh, which prints $LANE_PERSONA for the session started
with the markers and nothing for any other. Idle: the newest transcript write in ~/.claude/projects/<encoded path>/.
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import harnesses  # noqa: E402

NAME = "claude"
STATUS = "in use"
ORCA_AGENT_ID = "claude"
MODEL_FLAGS = frozenset({"model", "effort"})
MATCHER = "startup|resume|compact"
HOOK_NAME = harnesses.PERSONA_HOOK.name


def start_command(role: str, persona: Path, model: str, permission_mode: "str | None") -> list:
    """argv for an Orca terminal's --command: env LANE_ROLE=<role> LANE_PERSONA=<persona> claude --model <model>."""
    cmd = harnesses.env_prefix(role, persona) + ["claude", "--model", model]
    if permission_mode:
        cmd += ["--permission-mode", permission_mode]
    return cmd


def _ours(hook) -> bool:
    return isinstance(hook, dict) and Path(str(hook.get("command", ""))).name == HOOK_NAME


def install_persona_hook(checkout: Path) -> list:
    """Add the persona hook to <checkout>/.claude/settings.local.json once; other settings are kept. An entry that
    points at another copy of persona-hook.sh (a moved lane-control) is updated in place. Returns change lines."""
    path = Path(checkout) / ".claude" / "settings.local.json"
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text() or "{}")
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}: not valid JSON ({e}); fix it by hand, it was left as it is") from None
    hooks = data.get("hooks", {}) if isinstance(data, dict) else None
    starts = hooks.get("SessionStart", []) if isinstance(hooks, dict) else None
    if not isinstance(starts, list):
        raise ValueError(f"{path}: not an object with hooks.SessionStart a list; fix it by hand, it was left as it is")
    want = {"matcher": MATCHER, "hooks": [{"type": "command", "command": str(harnesses.PERSONA_HOOK)}]}
    mine = [e for e in starts if isinstance(e, dict) and any(_ours(h) for h in e.get("hooks") or [])]
    if mine == [want]:
        return []
    keep = [e for e in starts if not any(e is m for m in mine)]
    data["hooks"] = {**hooks, "SessionStart": keep + [want]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    what = "updated" if mine else "added"
    return [f"{path}: {what} the SessionStart persona hook ({MATCHER})"]


def project_dir(checkout: Path) -> Path:
    """Claude's transcript folder for a working directory: every character but letters and digits becomes '-'."""
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(checkout))


def idle_minutes(checkout: Path, now: float) -> "int | None":
    """Minutes since the newest *.jsonl transcript write for the checkout; None if it has no transcript. An
    unreadable folder raises OSError, so a caller can tell it from "no session"."""
    d = project_dir(Path(checkout).resolve())
    if not d.is_dir():
        return None
    with os.scandir(d) as it:  # unlike Path.glob, raises on an unreadable folder
        newest = max((e.stat().st_mtime for e in it if e.name.endswith(".jsonl") and e.is_file()), default=None)
    if newest is None:
        return None
    return max(0, int((now - newest) // 60))
