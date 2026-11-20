"""native-orca adapter for scripts/orch: one marked section, from templates/agents-section.md, in the project's
AGENTS.lane-control.md, with one pointer line to that file in AGENTS.md (core/agentsdoc.py, the policy).

The markers are the comment pair around that section; status detects by them in either file and strip removes exactly
that section (the file too, and the pointer, once no section is left in it). setup adds it, or replaces an older copy
in place; a copy still in AGENTS.md is moved out. AGENTS.md is created when missing; a symlinked AGENTS.md is edited
in its target inside the repo.
"""
import importlib.util
from pathlib import Path

NAME = "native-orca"
ACTIONS = frozenset({"status", "setup", "strip"})
BEGIN = "<!-- orchestrator: native-orca -->"
END = "<!-- /orchestrator: native-orca -->"
TEMPLATE = Path(__file__).resolve().parent / "templates" / "agents-section.md"


def _agentsdoc():
    spec = importlib.util.spec_from_file_location("lc_agentsdoc", Path(__file__).resolve().parents[2] / "core" /
                                                  "agentsdoc.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DOC = _agentsdoc()
GENERATED = DOC.GENERATED
SECTION = f"{GENERATED} native-orca section"
OLD_SECTION = "AGENTS.md native-orca section"


def read(f: Path) -> str:
    return f.read_bytes().decode(errors="surrogateescape") if f.exists() else ""


def write(f: Path, text: str):
    DOC.write_atomic(f, text)


def agents_md(repo: Path) -> Path:
    f = repo / "AGENTS.md"
    if not f.is_symlink():
        return f
    target = f.resolve()
    try:
        target.relative_to(repo.resolve())
    except ValueError:
        raise ValueError("AGENTS.md links outside the repo; edit its native-orca section by hand")
    return target


def split(text: str):
    """(head, section, rest) or (text, None, None); refuses markers it cannot pair."""
    try:
        return DOC.split_block(text, BEGIN, END)
    except ValueError:
        raise ValueError(f"unpaired or repeated {BEGIN} markers in AGENTS.md or {GENERATED}; fix it by hand and "
                         "run again")


def detect(project: Path) -> list:
    try:
        text = read(agents_md(project))
    except ValueError:
        text = read(project / "AGENTS.md")
    gen = read(project / GENERATED)
    return ([SECTION] if BEGIN in gen or END in gen else []) + ([OLD_SECTION] if BEGIN in text or END in text else [])


def section_text(text: str) -> str:
    """The generated file's text with the native-orca section: an older copy replaced in place, else added at the end
    (a new file starts with its header). project-setup's native path uses this too, so the section has one
    implementation."""
    split(text)
    return DOC.put_block(DOC.generated_base(text), TEMPLATE.read_text().rstrip("\n"), BEGIN, END)


def pointer_text(text: str) -> str:
    """AGENTS.md's text with the section moved out and the one pointer line in."""
    split(text)
    return DOC.with_pointer(DOC.drop_block(text, BEGIN, END))


DIFFERED = ("differed from the template (an older version or a hand edit); the old text stays in this change's diff "
            "and in git history")


def setup(project: Path, work: Path) -> list:
    where = DOC.ignored(work)
    if where:
        raise ValueError(DOC.ignored_message(where))
    f, g = agents_md(work), work / GENERATED
    text, gen = read(f), read(g)
    new_text, new_gen = pointer_text(text), section_text(gen)  # both computed before either is written
    block = TEMPLATE.read_text().rstrip("\n")
    changes = []
    if new_gen != gen:
        write(g, new_gen)
        changes.append(f"{GENERATED}: " + ("refreshed the native-orca section" if split(gen)[1] is not None else
                                           "added the native-orca section" + ("" if gen else " (new file)")))
    if new_text != text:
        existed = f.exists()
        write(f, new_text)
        name = f.resolve().relative_to(work.resolve())
        moved = split(text)[1] is not None
        changes.append(f"{name}: " + (f"moved the native-orca section to {GENERATED}" if moved else
                                      f"pointer line to {GENERATED}") + ("" if existed else " (new file)"))
        if DOC.replaced(text, block, BEGIN, END):
            changes.append(f"{name}: the native-orca section moved out {DIFFERED}")
    if DOC.replaced(gen, block, BEGIN, END):
        changes.append(f"{GENERATED}: the native-orca section replaced {DIFFERED}")
    return changes


def strip(project: Path, work: Path) -> list:
    f, g = agents_md(work), work / GENERATED
    text, gen = read(f), read(g)
    split(text)
    split(gen)
    changes = []
    name = f.resolve().relative_to(work.resolve()) if f.exists() else Path("AGENTS.md")
    if split(gen)[1] is not None:
        gen = DOC.drop_block(gen, BEGIN, END)
        if DOC.holds_nothing(gen):
            g.unlink()
            changes.append(f"{GENERATED}: removed (it held only the native-orca section)")
        else:
            write(g, gen)
            changes.append(f"{GENERATED}: removed the native-orca section")
    new_text = DOC.drop_block(text, BEGIN, END)
    if new_text != text:
        changes.append(f"{name}: removed the native-orca section")
    if not g.exists() and DOC.POINTER_MARK in new_text:
        new_text = DOC.without_pointer(new_text)
        changes.append(f"{name}: removed the pointer line")
    if new_text != text:
        write(f, new_text)
    return changes
