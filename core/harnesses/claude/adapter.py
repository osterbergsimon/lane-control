"""Claude Code harness adapter (core/harnesses/__init__.py has the interface).

Persona: a SessionStart hook (matchers startup|resume|compact) in the checkout's uncommitted
.claude/settings.local.json runs core/harnesses/persona-hook.sh (path shell-quoted: Claude runs hook commands in a
shell), which prints $LANE_PERSONA for the session started with the markers and nothing for any other.
Idle: the newest transcript write in ~/.claude/projects/<encoded path>/.
"""
import json
import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import harnesses  # noqa: E402

NAME = "claude"
STATUS = "in use"
ORCA_AGENT_ID = "claude"
MODEL_FLAGS = frozenset({"model", "effort"})
MATCHER = "startup|resume|compact"
HOOK_TAIL = ("core", "harnesses", "persona-hook.sh")


def start_command(role: str, persona: Path, model: str, permission_mode: "str | None") -> list:
    """argv for an Orca terminal's --command: env LANE_ROLE=<role> LANE_PERSONA=<persona> claude --model <model>."""
    cmd = harnesses.env_prefix(role, persona) + ["claude", "--model", model]
    if permission_mode:
        cmd += ["--permission-mode", permission_mode]
    return cmd


def _ours(hook) -> bool:
    """Our hook: one command word, a path ending core/harnesses/persona-hook.sh (another tool's script of the same
    name is not ours)."""
    if not isinstance(hook, dict):
        return False
    try:
        words = shlex.split(str(hook.get("command", "")))
    except ValueError:
        return False
    return len(words) == 1 and Path(words[0]).parts[-3:] == HOOK_TAIL


def install_persona_hook(checkout: Path) -> list:
    """Add the persona hook to <checkout>/.claude/settings.local.json once, as its own SessionStart entry; every other
    setting, entry and hook is kept as it is. Our hook found anywhere else (a moved lane-control, an entry shared
    with someone else's hook, a duplicate) is taken out of that place only, so the file ends with exactly one entry
    of ours. Returns change lines."""
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
    want = {"matcher": MATCHER, "hooks": [{"type": "command", "command": shlex.quote(str(harnesses.PERSONA_HOOK))}]}

    def holds_ours(e):
        return isinstance(e, dict) and isinstance(e.get("hooks"), list) and any(_ours(h) for h in e["hooks"])
    if [e for e in starts if holds_ours(e)] == [want]:
        return []
    kept = []
    for e in starts:
        if not holds_ours(e):
            kept.append(e)
            continue
        others = [h for h in e["hooks"] if not _ours(h)]
        if others:  # someone else's hooks share this entry: keep the entry and them, take only ours out
            kept.append({**e, "hooks": others})
    data["hooks"] = {**hooks, "SessionStart": kept + [want]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    what = "updated" if any(holds_ours(e) for e in starts) else "added"
    return [f"{path}: {what} the SessionStart persona hook ({MATCHER})"]


def project_dir(checkout: Path) -> Path:
    """Claude's transcript folder for a working directory: every character but letters and digits becomes '-'."""
    return Path.home() / ".claude" / "projects" / harnesses.encode_path(checkout)


def _mtime(path: Path) -> "float | None":
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return None


def idle_minutes(checkout: Path, now: float, role: str = "coordinator") -> "int | None":
    """Minutes since the newest transcript write of the checkout's <role> session (recorded by persona-hook.sh when
    the session started with the markers), its own sub-agents' transcripts included. Other sessions in the same
    checkout do not count. None when no marked session is recorded or its transcript is gone. An unreadable folder
    or record raises OSError, so a caller can tell it from "no session"."""
    sid = harnesses.read_session(checkout, role, NAME)
    if sid is None:
        return None
    d = project_dir(Path(checkout).resolve())
    times = [_mtime(d / f"{sid}.jsonl")]
    sub = d / sid
    if sub.is_dir():
        for root, _dirs, files in os.walk(sub, onerror=_raise):
            times += [_mtime(Path(root) / f) for f in files if f.endswith(".jsonl")]
    times = [t for t in times if t is not None]
    if not times:
        return None
    return max(0, int((now - max(times)) // 60))


def _raise(err: OSError):
    raise err
