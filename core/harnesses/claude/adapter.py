"""Claude Code harness adapter (core/harnesses/__init__.py has the interface).

Persona: a SessionStart hook (matchers startup|resume|clear|compact) in the checkout's uncommitted
.claude/settings.local.json runs core/harnesses/persona-hook.sh (path shell-quoted: Claude runs hook commands in a
shell), which prints $LANE_PERSONA for the session started with the markers and nothing for any other.
Idle: the newest transcript write in ~/.claude/projects/<encoded path>/.
"""
import json
import os
import re
import shlex
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import beads  # noqa: E402
import harnesses  # noqa: E402

NAME = "claude"
STATUS = "in use"
ORCA_AGENT_ID = "claude"
MODEL_FLAGS = frozenset({"model", "effort"})
MATCHER = beads.OUR_HOOK_MATCHER  # one place, so beads.hook_notes can tell our own entry from another
HOOK_TAIL = ("core", "harnesses", "persona-hook.sh")


def start_command(role: str, persona: Path, model: str, permission_mode: "str | None") -> list:
    """argv for an Orca terminal's --command: env LANE_ROLE=<role> LANE_PERSONA=<persona> claude --model <model>."""
    cmd = harnesses.env_prefix(role, persona, model) + ["claude", "--model", model]
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


def _read_settings(path: Path):
    """(data, SessionStart list) of an uncommitted settings file; ValueError (file left as it is) when it is not JSON or
    not shaped as expected."""
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text().strip() or "{}")
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}: not valid JSON ({e}); fix it by hand, it was left as it is") from None
    hooks = data.get("hooks", {}) if isinstance(data, dict) else None
    starts = hooks.get("SessionStart", []) if isinstance(hooks, dict) else None
    if not isinstance(starts, list):
        raise ValueError(f"{path}: not an object with hooks.SessionStart a list; fix it by hand, it was left as it is")
    return data, starts


def install_persona_hook(checkout: Path) -> list:
    """Add the persona hook to <checkout>/.claude/settings.local.json once, as its own SessionStart entry; every other
    setting, entry and hook is kept as it is. Our hook found anywhere else (a moved lane-control, an entry shared
    with someone else's hook, a duplicate) is taken out of that place only, so the file ends with exactly one entry
    of ours. Returns change lines."""
    path = Path(checkout) / ".claude" / "settings.local.json"
    data, starts = _read_settings(path)
    hooks = data.get("hooks", {})
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
    _write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    what = "updated" if any(holds_ours(e) for e in starts) else "added"
    return [f"{path}: {what} the SessionStart persona hook ({MATCHER})"]


def install_beads_hook(checkout: Path) -> list:
    """Add `bd prime` as a SessionStart hook (same matchers as the persona hook) to <checkout>/.claude/settings.local.json
    once, as its own entry; nothing else in the file changes. Nothing is added when bd is missing or has no working
    `bd prime`, or when any settings layer (the user's, the committed .claude/settings.json, this file) already runs
    `bd prime` as a SessionStart hook whose matcher covers `startup`, as a plain command or a shell line (two would
    run it twice: core/beads.py hook_notes says so). A `bd prime` on another event (PreCompact, Stop) or for other
    matchers only (`compact`, `resume`) does not stop it: a session would start with no beads context. Only this file is
    ever written. Returns change lines."""
    checkout = Path(checkout)
    if any(h.covers_start and h.runs is not False for h in beads.prime_hooks(checkout)):
        return []
    command = beads.bd_prime_command(checkout)
    if command is None:
        return []
    path = checkout / ".claude" / "settings.local.json"
    data, starts = _read_settings(path)

    def holds_bd(e):
        return (isinstance(e, dict) and e.get("matcher") == MATCHER and isinstance(e.get("hooks"), list)
                and any(isinstance(h, dict) and h.get("command") == command for h in e["hooks"]))
    if any(holds_bd(e) for e in starts):
        return []
    want = {"matcher": MATCHER, "hooks": [{"type": "command", "command": command}]}
    data["hooks"] = {**data.get("hooks", {}), "SessionStart": starts + [want]}
    _write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return [f"{path}: added the SessionStart beads hook `{command}` ({MATCHER})"]


BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")


def away_deny_rules(integration: str) -> list:
    """The deny rules of away-improve mode: no `gh pr merge`, no push to the integration branch. away-merge, a script
    that runs gh itself, is the only merge route, and it only merges into away/* branches."""
    if not BRANCH.fullmatch(integration or ""):
        raise ValueError(f"not a branch name: {integration!r}")
    return ["Bash(gh pr merge:*)", f"Bash(git push origin {integration}:*)",
            f"Bash(git push origin HEAD:{integration}:*)", f"Bash(git push origin HEAD:refs/heads/{integration}:*)"]


def _read_denies(path: Path):
    """(data, permissions dict, deny list) of the uncommitted settings file; ValueError (file left as it is) when it is
    not JSON or permissions.deny is not shaped as expected. Missing parts come back empty, not created."""
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text().strip() or "{}")
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}: not valid JSON ({e}); fix it by hand, it was left as it is") from None
    perms = data.get("permissions", {}) if isinstance(data, dict) else None
    deny = perms.get("deny", []) if isinstance(perms, dict) else None
    if not isinstance(deny, list):
        raise ValueError(f"{path}: not an object with permissions.deny a list; fix it by hand, it was left as it is")
    return data, perms, deny


def install_away_denies(checkout: Path, integration: str, project: "str | None" = None) -> list:
    """Add the away-improve deny rules (away_deny_rules) to permissions.deny of <checkout>/.claude/settings.local.json,
    skipping any already there. What was added, and which parts of the file did not exist before, is noted in
    harnesses.away_denies_record, so remove_away_denies takes out only ours. Returns change lines (none when nothing
    was added)."""
    rules = away_deny_rules(integration)
    path = Path(checkout) / ".claude" / "settings.local.json"
    data, perms, deny = _read_denies(path)
    missing = [r for r in rules if r not in deny]
    if not missing:
        return []
    rec_path = harnesses.away_denies_record(checkout)
    try:
        rec = json.loads(rec_path.read_text())
        added = [r for r in rec["added"] if isinstance(r, str)]
    except (OSError, ValueError, KeyError, TypeError):
        rec, added = {}, []
    rec = {"added": added + missing,
           "created_file": rec.get("created_file", not path.exists()),
           "created_permissions": rec.get("created_permissions", "permissions" not in data),
           "created_deny": rec.get("created_deny", "deny" not in perms)}
    rec_path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(rec_path, json.dumps(rec, indent=2) + "\n")  # the record first: a rule is never in place unrecorded
    data["permissions"] = {**perms, "deny": deny + missing}
    _write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return [f"{path}: added {len(missing)} away-improve deny rule(s): {', '.join(missing)}"]


def remove_away_denies(checkout: Path) -> list:
    """Take out the deny rules install_away_denies added (and the file or sections it created, if they are empty now);
    every other rule stays. Nothing recorded, nothing removed. Returns change lines."""
    rec_path = harnesses.away_denies_record(checkout)
    try:
        rec = json.loads(rec_path.read_text())
        added = [r for r in rec["added"] if isinstance(r, str)]
    except FileNotFoundError:
        return []
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError(f"{rec_path}: cannot be read; the away-improve deny rules in the checkout's settings must be "
                         "taken out by hand") from None
    path = Path(checkout) / ".claude" / "settings.local.json"
    lines = []
    if path.exists():
        data, perms, deny = _read_denies(path)
        gone = [r for r in added if r in deny]
        rest = [r for r in deny if r not in added]
        if gone:
            perms = {k: v for k, v in perms.items() if k != "deny"}
            if rest or not rec.get("created_deny"):
                perms["deny"] = rest
            data = {k: v for k, v in data.items() if k != "permissions"}
            if perms or not rec.get("created_permissions"):
                data["permissions"] = perms
            if not data and rec.get("created_file"):
                path.unlink()
            else:
                _write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            lines.append(f"{path}: removed {len(gone)} away-improve deny rule(s)")
    rec_path.unlink(missing_ok=True)
    return lines


def _write_atomic(path: Path, text: str):
    """Write through a temp file and a rename beside the real file (a symlink stays a link, its target is replaced),
    keeping the file's mode, so a crash never leaves the owner's settings cut short."""
    target = path.resolve() if path.is_symlink() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def project_dir(checkout: Path) -> Path:
    """Claude's transcript folder for a working directory: every character but letters and digits becomes '-'. The
    base is $CLAUDE_CONFIG_DIR when it is set (absolute), else ~/.claude, read from this process's environment: a
    session started with another config folder (an Orca-managed profile) keeps its transcripts there."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(base) if base and os.path.isabs(base) else Path.home() / ".claude"
    return base / "projects" / harnesses.encode_path(checkout)


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
