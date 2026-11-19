"""OpenCode (v2) harness adapter (core/harnesses/__init__.py has the interface).

Start: `env LANE_ROLE=... LANE_PERSONA=... OPENCODE_CONFIG_CONTENT={"model": ...} opencode --standalone [--auto]`.
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
AUTO_MODES = {"auto", "bypassPermissions"}
ASK_MODES = {None, "", "default"}


def start_command(role: str, persona: Path, model: str, permission_mode: "str | None") -> list:
    """argv for an Orca terminal's --command, beginning env LANE_ROLE=<role> LANE_PERSONA=<persona>."""
    if permission_mode not in AUTO_MODES | ASK_MODES:
        raise ValueError(f"OpenCode has no permission mode {permission_mode!r} "
                         f"(it takes {', '.join(sorted(AUTO_MODES))} as --auto, or default)")
    cmd = harnesses.env_prefix(role, persona) + [
        "OPENCODE_CONFIG_CONTENT=" + json.dumps({"model": model}), "opencode", "--standalone"]
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
    return [f"{dest}: {what} the lane-persona plugin (acts only with LANE_ROLE and LANE_PERSONA set)"]


def db_path() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "opencode" / "opencode.db"


NEWEST = """SELECT max(t) FROM (
  SELECT time_updated AS t FROM session_v2 WHERE directory = ?
  UNION ALL
  SELECT m.time_updated FROM session_message m JOIN session_v2 s ON s.id = m.session_id WHERE s.directory = ?)"""


def idle_minutes(checkout: Path, now: float) -> "int | None":
    """Minutes since the newest session write for the checkout; None if OpenCode has no session for it. A store
    that cannot be read raises (OSError or sqlite3.Error), so a caller can tell it from "no session"."""
    db = db_path()
    if not db.exists():
        return None
    here = str(Path(checkout).resolve())
    con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        (newest,) = con.execute(NEWEST, (here, here)).fetchone()
    finally:
        con.close()
    if newest is None:
        return None
    return max(0, int((now - newest / 1000) // 60))
