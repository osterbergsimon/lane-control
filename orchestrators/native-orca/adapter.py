"""native-orca adapter for scripts/orch: one marked section in the project's AGENTS.md, from templates/agents-section.md.

The markers are the comment pair around that section; status detects by them and strip removes exactly that section
(and the blank line setup put before it). setup adds it at the end, or replaces an older copy in place. AGENTS.md is
created when missing; a symlinked AGENTS.md is edited in its target inside the repo.
"""
from pathlib import Path

NAME = "native-orca"
ACTIONS = frozenset({"status", "setup", "strip"})
BEGIN = "<!-- orchestrator: native-orca -->"
END = "<!-- /orchestrator: native-orca -->"
TEMPLATE = Path(__file__).resolve().parent / "templates" / "agents-section.md"
SECTION = "AGENTS.md native-orca section"


def read(f: Path) -> str:
    return f.read_bytes().decode(errors="surrogateescape") if f.exists() else ""


def write(f: Path, text: str):
    f.write_bytes(text.encode(errors="surrogateescape"))


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
    if text.count(BEGIN) != text.count(END) or text.count(BEGIN) > 1 or (
            BEGIN in text and text.index(END) < text.index(BEGIN)):
        raise ValueError(f"AGENTS.md has unpaired or repeated {BEGIN} markers; fix it by hand and run again")
    if BEGIN not in text:
        return text, None, None
    head, rest = text.split(BEGIN, 1)
    section, rest = rest.split(END, 1)
    return head, BEGIN + section + END, rest


def detect(project: Path) -> list:
    try:
        text = read(agents_md(project))
    except ValueError:
        text = read(project / "AGENTS.md")
    return [SECTION] if BEGIN in text or END in text else []


def setup(project: Path, work: Path) -> list:
    f = agents_md(work)
    text = read(f)
    block = TEMPLATE.read_text().rstrip("\n")
    head, section, rest = split(text)
    if section is not None:
        if section == block:
            return []
        write(f, head + block + rest)
        return [f"{f.resolve().relative_to(work.resolve())}: refreshed the native-orca section"]
    if not text:
        new = block + "\n"
    else:
        new = text + ("" if text.endswith("\n") else "\n") + "\n" + block + "\n"
    existed = f.exists()
    write(f, new)
    name = f.resolve().relative_to(work.resolve())
    return [f"{name}: added the native-orca section" + ("" if existed else " (new file)")]


def strip(project: Path, work: Path) -> list:
    f = agents_md(work)
    text = read(f)
    head, section, rest = split(text)
    if section is None:
        return []
    rest = rest[1:] if rest.startswith("\n") else rest
    if not rest.strip():
        body = head.rstrip("\n")
        new = body + "\n" if body else ""
    else:
        new = (head[:-1] if head.endswith("\n\n") and rest.startswith("\n") else head) + rest
    write(f, new)
    return [f"{f.resolve().relative_to(work.resolve())}: removed the native-orca section"]
