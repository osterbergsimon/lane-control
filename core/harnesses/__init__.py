"""Harness adapters: how to start one agent harness with a lane role and persona, and keep that persona after
compaction (docs/superpowers/specs/2026-11-19-native-orca-setup-design.md, sections 6 and 12).

Each harness with code has core/harnesses/<name>/adapter.py exposing NAME, STATUS, ORCA_AGENT_ID, MODEL_FLAGS,
start_command(), install_persona_hook() and idle_minutes(); every harness, tried or not, has a dated record.md.
Import with core/ on sys.path: `import harnesses; harnesses.load_harness("claude")`.
"""
import importlib.util
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
PERSONA_HOOK = HERE / "persona-hook.sh"
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


def env_prefix(role: str, persona: Path) -> list:
    persona = check_role_and_persona(role, persona)
    return ["env", f"LANE_ROLE={role}", f"LANE_PERSONA={persona}"]
