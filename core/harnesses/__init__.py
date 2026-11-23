"""Harness adapters: how to start one agent harness with a lane role and persona, and keep that persona after
compaction (docs/superpowers/specs/2026-11-19-native-orca-setup-design.md, sections 6 and 12).

Each harness with code has core/harnesses/<name>/adapter.py exposing NAME, STATUS, ORCA_AGENT_ID, MODEL_FLAGS,
start_command(), install_persona_hook(), idle_minutes(), and install_away_denies() / remove_away_denies()
(away-improve mode's deny rules; an adapter with no way to set them returns no change lines and its record.md says so); every harness, tried or not, has a dated record.md.
The session record the adapters' idle signal reads is a published interface: session-record.md.
Import with core/ on sys.path: `import harnesses; harnesses.load_harness("claude")`.
"""
import importlib.util
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
PERSONA_HOOK = HERE / "persona-hook.sh"
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,200}")
# design-lead: retired 2026-11-21 (the design kit), kept so a design lead still running keeps its session record
ROLES = frozenset({"coordinator", "design-lead", "reviewer", "debugger", "machine-coordinator"})
_cache: dict = {}


def load_harness(name: str):
    """The adapter module of one harness; ValueError for an unknown or record-only harness."""
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name or "") or not (HERE / name / "adapter.py").is_file():
        known = ", ".join(sorted(harnesses_names())) or "none"
        raise ValueError(f"no harness adapter {name!r} (adapters: {known})")
    if name not in _cache:
        spec = importlib.util.spec_from_file_location(f"harnesses.{name.replace('-', '_')}.adapter",
                                                      HERE / name / "adapter.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _cache[name] = mod
    return _cache[name]


def harnesses_names() -> list:
    return sorted(f.parent.name for f in HERE.glob("*/adapter.py"))


def harnesses() -> dict:
    """{name: adapter module} for every harness with an adapter (record-only ones are left out)."""
    return {name: load_harness(name) for name in harnesses_names()}


def check_role_and_persona(role: str, persona: Path) -> Path:
    """The shared checks of start_command: a known role and an absolute persona path."""
    if role not in ROLES:
        raise ValueError(f"unknown lane role {role!r} (roles: {', '.join(sorted(ROLES))})")
    persona = Path(persona)
    if not persona.is_absolute():
        raise ValueError(f"LANE_PERSONA must be an absolute path, got {str(persona)!r}")
    return persona


def env_prefix(role: str, persona: Path, model: str) -> list:
    """The shared start of every start command, after the shared checks (role, absolute persona, a model)."""
    persona = check_role_and_persona(role, persona)
    if not isinstance(model, str) or not model.strip():
        raise ValueError("a model is required (an empty one would start the harness on its own default)")
    return ["env", f"LANE_ROLE={role}", f"LANE_PERSONA={persona}"]


def encode_path(path: Path) -> str:
    """A path as one folder name: every character but letters and digits becomes '-' (Claude's own rule)."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def session_record(checkout: Path, role: str, harness: str) -> Path:
    """Where a marked session records its id, so the idle signal reads that session and no other in the checkout:
    $XDG_STATE_HOME/lane-control/sessions/<encoded checkout>/<role>.<harness> (default ~/.local/state). The Claude
    hook (persona-hook.sh) and the OpenCode plugin (lane-persona.js) write it; keep the three in step."""
    v = os.environ.get("XDG_STATE_HOME")
    state = Path(v) if v and os.path.isabs(v) else Path.home() / ".local" / "state"
    key = re.sub(rb"[^A-Za-z0-9]", b"-", os.fsencode(Path(checkout).resolve())).decode()  # per byte, like the writers
    return state / "lane-control" / "sessions" / key / f"{role}.{harness}"


def away_denies_record(checkout: Path, harness: str = "") -> Path:
    """Where an adapter notes what it did for away-improve mode, so turning the mode off takes out only that:
    $XDG_STATE_HOME/lane-control/away-denies/<encoded checkout>[.<harness>].json. Claude's is the deny rules it added;
    OpenCode's (`.opencode`) is the project and integration branch its guard plugin reads."""
    v = os.environ.get("XDG_STATE_HOME")
    state = Path(v) if v and os.path.isabs(v) else Path.home() / ".local" / "state"
    key = re.sub(rb"[^A-Za-z0-9]", b"-", os.fsencode(Path(checkout).resolve())).decode()
    return state / "lane-control" / "away-denies" / (f"{key}.{harness}.json" if harness else f"{key}.json")


def read_session(checkout: Path, role: str, harness: str) -> "str | None":
    """The recorded session id, or None when there is none (or it is not an id). An unreadable record raises."""
    try:
        text = session_record(checkout, role, harness).read_text().strip()
    except (FileNotFoundError, UnicodeDecodeError):
        return None
    return text if SESSION_ID.fullmatch(text) else None
