"""OpenCode (v2) harness adapter (core/harnesses/__init__.py has the interface).

Start: `env LANE_ROLE=... LANE_PERSONA=... OPENCODE_CONFIG_CONTENT={"model": ...} opencode2 --standalone [--auto]`.
`opencode2` is the v2 package's own command (a plain `opencode` may be v1 on some PATHs) and what Orca detects as its
opencode2 agent.
v2's interactive command has no --model flag; the model goes in through OPENCODE_CONFIG_CONTENT, as Orca does for
its own opencode2 agent. --standalone runs the session in a private server started from this command, so the
markers reach the plugin; the shared background service would not see them.
Persona: lane-persona.js in OpenCode's plugin folder ($XDG_CONFIG_HOME/opencode/plugins/, default
~/.config/opencode/plugins/), or with LANE_OPENCODE_PLUGIN=project in the project's .opencode/plugins/.
Idle: the newest write of a session for the checkout in OpenCode's v2 store
($XDG_DATA_HOME/opencode/opencode.db, tables session_v2 and session_message, times in ms).
"""
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import harnesses  # noqa: E402

NAME = "opencode"
STATUS = "in use"
ORCA_AGENT_ID = "opencode2"
MODEL_FLAGS = frozenset({"model"})
PLUGIN = Path(__file__).resolve().parent / "lane-persona.js"
# project-setup's permission modes (Claude's names); OpenCode has only ask (no flag) and --auto.
# A model pinned on the primary agent in the user's own config beats the top-level one, so it is pinned there too
# (as Orca does). OpenCode's primary agent is `build` unless the user's config names another default agent.
PRIMARY_AGENT = "build"
AUTO_MODES = {"auto", "bypassPermissions"}
ASK_MODES = {None, "", "default"}


def start_command(role: str, persona: Path, model: str, permission_mode: "str | None") -> list:
    """argv for an Orca terminal's --command, beginning env LANE_ROLE=<role> LANE_PERSONA=<persona>."""
    if permission_mode not in AUTO_MODES | ASK_MODES:
        raise ValueError(f"OpenCode has no permission mode {permission_mode!r} "
                         f"(it takes {', '.join(sorted(AUTO_MODES))} as --auto, or default)")
    cmd = harnesses.env_prefix(role, persona, model) + [
        "OPENCODE_CONFIG_CONTENT=" + json.dumps({"model": model, "agents": {PRIMARY_AGENT: {"model": model}}}),
        "opencode2", "--standalone"]
    if permission_mode in AUTO_MODES:
        cmd.append("--auto")
    return cmd


def _xdg(var: str, default: str) -> Path:
    v = os.environ.get(var)
    return Path(v) if v and os.path.isabs(v) else Path.home() / default


SCOPES = ("user", "project")


def plugin_scope() -> str:
    """Where the plugin is installed: LANE_OPENCODE_PLUGIN=user (the default) or project. A project install is
    <checkout>/.opencode/plugins/, loaded for OpenCode servers started in that project only."""
    scope = os.environ.get("LANE_OPENCODE_PLUGIN") or "user"
    if scope not in SCOPES:
        raise ValueError(f"LANE_OPENCODE_PLUGIN={scope!r}: it takes {' or '.join(SCOPES)} (user: one copy in "
                         "OpenCode's own plugin folder; project: one in the project's .opencode/plugins/)")
    return scope


def plugin_path(checkout: "Path | None" = None) -> Path:
    if plugin_scope() == "project":
        if checkout is None:
            raise ValueError("a project-level plugin install needs the checkout")
        return Path(checkout) / ".opencode" / "plugins" / PLUGIN.name
    return _xdg("XDG_CONFIG_HOME", ".config") / "opencode" / "plugins" / PLUGIN.name


def _write(dest: Path, want: bytes) -> "str | None":
    """Make dest hold want; 'installed', 'updated', or None when it already did."""
    if dest.is_file() and dest.read_bytes() == want:
        return None
    what = "updated" if dest.exists() else "installed"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(want)
    tmp.replace(dest)
    return what


def _is_ours(path: Path) -> bool:
    try:
        return 'id: "lane-persona"' in path.read_text()
    except (OSError, UnicodeDecodeError):
        return False


def load_shipped_hashes() -> frozenset:
    """The SHA-256 of every earlier lane-persona.js lane-control shipped (lane-persona.sha256 beside the plugin)."""
    try:
        lines = (Path(__file__).resolve().parent / "lane-persona.sha256").read_text().splitlines()
    except OSError:
        return frozenset()
    return frozenset(l.split()[0] for l in lines if l.strip() and not l.startswith(("#", "current ")))


SHIPPED_HASHES = load_shipped_hashes()


def _refresh_user_copy(want: bytes) -> list:
    """With a project-level install, an older user-level copy of ours would still be loaded, and has no check against
    adding the persona twice. Bring it to this version only when its bytes are a version lane-control shipped;
    anything else (a local edit, a fork, a symlink) is left exactly as it is and named."""
    import hashlib
    user = _xdg("XDG_CONFIG_HOME", ".config") / "opencode" / "plugins" / PLUGIN.name
    if user.is_symlink():
        try:
            if user.read_bytes() == want:  # a link to this version (lane-control's own file, or a copy): harmless
                return []
        except OSError:
            pass  # a broken link or an unreadable target is named below
        return [f"{user}: a symlink; not changed. If it is an older lane-persona plugin, the persona may be added twice "
                "(point it at this version, or remove it)"]
    if not user.is_file() or not _is_ours(user):
        return []
    have = user.read_bytes()
    if have == want:
        return []
    if hashlib.sha256(have).hexdigest() in SHIPPED_HASHES:
        _write(user, want)
        return [f"{user}: updated the user-level lane-persona plugin from an older lane-control version, so it and the "
                "project copy never add the persona twice"]
    return [f"{user}: a lane-persona plugin that differs from every lane-control version; not changed. Both it and the "
            "project copy are loaded, and the persona may be added twice (replace it with this version, or remove it)"]


def install_persona_hook(checkout: Path) -> list:
    """Copy lane-persona.js into OpenCode's plugin folder: one per user (the default) or, with
    LANE_OPENCODE_PLUGIN=project, one per project in <checkout>/.opencode/plugins/. Unless the same bytes are there
    already. Both may exist: the plugin adds the persona once per request, but only a copy of this version does, so a
    project install also updates an existing user-level copy that is an older lane-control version (never a copy with
    other bytes, another plugin's file or a symlink: those are named in the output and left alone). Returns change
    lines; empty when everything was in place."""
    want = PLUGIN.read_bytes()
    dest = plugin_path(checkout)
    lines = []
    what = _write(dest, want)
    if what:
        reach = ("OpenCode servers started in this project load it at once" if plugin_scope() == "project" else
                 "OpenCode loads it into every running OpenCode server of this user at once")
        lines.append(f"{dest}: {what} the lane-persona plugin; {reach}, and it acts only in one started with "
                     "LANE_ROLE and LANE_PERSONA set")
    if plugin_scope() == "project":
        lines += _refresh_user_copy(want)
    return lines


def db_path() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "opencode" / "opencode.db"


# The recorded session's family: up to its root, then every session below that root (sub-agent sessions).
NEWEST = """WITH RECURSIVE
  up(id, parent) AS (SELECT id, parent_id FROM session_v2 WHERE id = ?
                     UNION SELECT s.id, s.parent_id FROM session_v2 s JOIN up ON s.id = up.parent),
  family(id) AS (SELECT id FROM up WHERE parent IS NULL
                 UNION SELECT s.id FROM session_v2 s JOIN family f ON s.parent_id = f.id)
SELECT max(t) FROM (
  SELECT time_updated AS t FROM session_v2 WHERE id IN (SELECT id FROM family)
  UNION ALL
  SELECT time_updated FROM session_message WHERE session_id IN (SELECT id FROM family))"""


def idle_minutes(checkout: Path, now: float, role: str = "coordinator") -> "int | None":
    """Minutes since the newest write of the checkout's <role> session (recorded by lane-persona.js in a server
    started with the markers) and of its sub-agent sessions; other sessions in the same checkout do not count. None
    when no marked session is recorded or the store does not have it. A record or store that cannot be read raises
    (OSError or sqlite3.Error), so a caller can tell it from "no session"."""
    sid = harnesses.read_session(checkout, role, NAME)
    db = db_path()
    if sid is None or not db.exists():
        return None
    con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not {"session_v2", "session_message"} <= tables:
            raise sqlite3.DatabaseError(f"{db}: not an OpenCode 2 store (no session_v2 or session_message table)")
        (newest,) = con.execute(NEWEST, (sid,)).fetchone()
    finally:
        con.close()
    if newest is None:
        return None
    return max(0, int((now - newest / 1000) // 60))


GUARD = Path(__file__).resolve().parent / "lane-away-guard.js"
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")


def guard_path(checkout: "Path | None" = None) -> Path:
    return plugin_path(checkout).with_name(GUARD.name)


def install_away_guard(checkout: Path) -> list:
    """Copy lane-away-guard.js beside lane-persona.js (the same user or project folder, LANE_OPENCODE_PLUGIN). It does
    nothing until `lane-improve on` records this checkout (install_away_denies) and only in a session started
    with LANE_ROLE=coordinator; once loaded it follows improve.json live. A coordinator already running when it is
    first installed needs one restart to load it. Returns change lines; empty when it was in place."""
    what = _write(guard_path(checkout), GUARD.read_bytes())
    if not what:
        return []
    return [f"{guard_path(checkout)}: {what} the lane-away-guard plugin (away-improve mode's deny rules for OpenCode; "
            "a running coordinator needs one restart to load it)"]


def install_away_denies(checkout: Path, integration: str, project: "str | None" = None) -> list:
    """Away-improve mode's deny rules, for OpenCode: record this checkout's project and integration branch for the
    lane-away-guard plugin, which refuses `gh pr merge` and pushes to that branch in the coordinator's shell while
    improve mode is on (live, no restart). Nothing is written in the checkout. Returns change lines (none when the
    record was already right)."""
    if not BRANCH.fullmatch(integration or ""):
        raise ValueError(f"not a branch name: {integration!r}")
    rec = harnesses.away_denies_record(checkout, "opencode")
    want = json.dumps({"integration": integration, "project": project}, sort_keys=True) + "\n"
    try:
        if rec.read_text() == want:
            return []
    except OSError:
        pass
    rec.parent.mkdir(parents=True, exist_ok=True)
    tmp = rec.with_name(rec.name + ".tmp")
    tmp.write_text(want)
    tmp.replace(rec)
    return [f"{rec}: recorded for the lane-away-guard plugin (project {project or '?'}, integration branch {integration})"]


def remove_away_denies(checkout: Path) -> list:
    """Take the record out: the guard plugin has nothing to act on for this checkout any more. Returns change lines."""
    rec = harnesses.away_denies_record(checkout, "opencode")
    if not rec.exists():
        return []
    rec.unlink()
    return [f"{rec}: removed; the lane-away-guard plugin no longer guards this checkout"]
