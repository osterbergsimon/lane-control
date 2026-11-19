"""OpenCode (v2) harness adapter (core/harnesses/__init__.py has the interface).

Start: `env LANE_ROLE=... LANE_PERSONA=... OPENCODE_CONFIG_CONTENT={"model": ...} opencode2 --standalone [--auto]`.
`opencode2` is the v2 package's own command (a plain `opencode` may be v1 on some PATHs) and what Orca detects as its
opencode2 agent.
v2's interactive command has no --model flag; the model goes in through OPENCODE_CONFIG_CONTENT, as Orca does for
its own opencode2 agent. --standalone runs the session in a private server started from this command, so the
markers reach the plugin; the shared background service would not see them.
Persona: lane-persona.js in OpenCode's plugin folder ($XDG_CONFIG_HOME/opencode/plugins/, default
~/.config/opencode/plugins/). Idle: the newest write of a session for the checkout in OpenCode's v2 store
($XDG_DATA_HOME/opencode/opencode.db, tables session_v2 and session_message, times in ms).
"""
import json
import os
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
    cmd = harnesses.env_prefix(role, persona) + [
        "OPENCODE_CONFIG_CONTENT=" + json.dumps({"model": model, "agents": {PRIMARY_AGENT: {"model": model}}}),
        "opencode2", "--standalone"]
    if permission_mode in AUTO_MODES:
        cmd.append("--auto")
    return cmd


def _xdg(var: str, default: str) -> Path:
    v = os.environ.get(var)
    return Path(v) if v and os.path.isabs(v) else Path.home() / default


def plugin_path() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "opencode" / "plugins" / PLUGIN.name


def install_persona_hook(checkout: Path) -> list:
    """Copy lane-persona.js into OpenCode's plugin folder (one per user, not per checkout) unless the same bytes are
    there already. Returns change lines; empty when it was in place."""
    dest = plugin_path()
    want = PLUGIN.read_bytes()
    if dest.is_file() and dest.read_bytes() == want:
        return []
    what = "updated" if dest.exists() else "installed"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(want)
    tmp.replace(dest)
    return [f"{dest}: {what} the lane-persona plugin; OpenCode loads it into every running OpenCode server of this "
            "user at once, and it acts only in one started with LANE_ROLE and LANE_PERSONA set"]


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
