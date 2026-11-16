"""orca-cotal adapter for scripts/orch: what project-setup leaves in a project, found and removed exactly.

Markers (the union of what the strip PRs of 2026-11-15 removed from three projects, and what project-setup adds):
- `.lane-control/`: the adapted files (coordinator, design lead, project, reviewer, routing);
- AGENTS.md's section between `<!-- lane-control:begin -->` and `<!-- lane-control:end -->` (in the symlink's target
  when AGENTS.md is a link inside the repo);
- the `.cotal/` line in `.gitignore`;
- `.cotal/` itself, Cotal's local agent state: never committed, so status reports it and strip leaves it (it goes
  when the old agents are stopped, a human step in orchestrators/switching.md).
`orca.yaml` is Orca's, used without Cotal too: not a marker. Prose a project wrote about Cotal in its own words is not
ours either; it is for whoever reviews the strip PR.

setup runs `scripts/project-setup` on the project as it is: that script asks its own questions and commits on its
own, so no PR is made and it says so first.
"""
import shutil
import subprocess
from pathlib import Path

NAME = "orca-cotal"
ACTIONS = frozenset({"status", "setup", "strip"})
RUNS_IN_PLACE = frozenset({"setup"})
ROOT = Path(__file__).resolve().parents[2]

ADAPTED = ".lane-control"
BEGIN = "<!-- lane-control:begin -->"
END = "<!-- lane-control:end -->"
IGNORE_LINES = (".cotal/", ".cotal")
SECTION = "AGENTS.md lane-control section"
WARNING = "project-setup commits on its own; no PR is made. It asks its questions in this terminal."


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
        raise ValueError("AGENTS.md links outside the repo; strip its lane-control section by hand")
    return target


def check_pairs(text: str):
    if text.count(BEGIN) != text.count(END) or text.count(BEGIN) > 1 or (
            BEGIN in text and text.index(END) < text.index(BEGIN)):
        raise ValueError(f"AGENTS.md has unpaired or repeated {BEGIN} markers; fix it by hand and run again")


def without_section(text: str) -> str:
    """text minus the section and the blank line project-setup put before it; refuses markers it cannot pair."""
    check_pairs(text)
    if BEGIN not in text:
        return text
    head, rest = text.split(BEGIN, 1)
    rest = rest.split(END, 1)[1]
    rest = rest[2:] if rest.startswith("\r\n") else rest[1:] if rest.startswith("\n") else rest
    if not rest.strip():
        body = head.rstrip("\r\n")
        nl = "\r\n" if "\r\n" in text else "\n"
        return body + nl if body else ""
    if head.endswith("\n\n") and rest.startswith("\n"):
        head = head[:-1]
    return head + rest


def ignore_lines(text: str) -> list:
    return [l for l in text.splitlines() if l.strip() in IGNORE_LINES]


def detect(project: Path) -> list:
    found = []
    if (project / ADAPTED).is_dir():
        found.append(f"{ADAPTED}/")
    try:
        text = read(agents_md(project))
    except ValueError:
        text = read(project / "AGENTS.md")
    if BEGIN in text or END in text:
        found.append(SECTION)
    if ignore_lines(read(project / ".gitignore")):
        found.append(".gitignore .cotal/")
    if (project / ".cotal").is_dir():
        found.append(".cotal/ (local)")
    return found


def strip(project: Path, work: Path) -> list:
    changes = []
    f = agents_md(work)
    text = read(f)
    new = without_section(text)
    if new != text:
        write(f, new)
        changes.append(f"{f.resolve().relative_to(work.resolve())}: removed the lane-control section")
    gi = work / ".gitignore"
    text = read(gi)
    if ignore_lines(text):
        kept = [l for l in text.splitlines(keepends=True) if l.strip() not in IGNORE_LINES]
        if "".join(kept).strip():
            write(gi, "".join(kept))
            changes.append(".gitignore: removed the .cotal/ line")
        else:
            gi.unlink()
            changes.append(".gitignore: removed (it held only the .cotal/ line)")
    d = work / ADAPTED
    if d.is_dir():
        names = sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file())
        shutil.rmtree(d)
        changes.append(f"{ADAPTED}/: removed ({', '.join(names)})")
        if "project.md" in names:
            changes.append(f"check before merging: {ADAPTED}/project.md held the adapted project facts; carry into "
                           "AGENTS.md what it lacks")
    return changes


def setup(project: Path, work, dry: bool = False) -> int:
    script = ROOT / "orchestrators" / "orca-cotal" / "scripts" / "project-setup"
    print(WARNING, flush=True)
    if dry:
        print(f"dry run: would run {script} {project}")
        return 0
    return subprocess.run([str(script), str(project)]).returncode
