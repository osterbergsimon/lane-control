"""Beads handling for lane-control: where a project's beads lives, whether it has an off-machine copy, and the steps that
move it to the standard layout or restore it (design: docs/superpowers/specs/2026-11-22-beads-handling-design.md).

The layout: a private GitHub repo `<owner>/<project>-beads`, checked out at `~/code/<project>-beads`; the project's
`.beads` is a git-ignored symlink to `<beads-repo>/.beads`; the Dolt remote of the database is that same GitHub repo.

bd, gh and git are only ever run as subprocesses. Every function that reports returns `say` triples
`(step, what, detail)` (project-setup's message shape, step `beads`); `what` in PROBLEMS means the owner should read it.
Nothing here creates, moves or pushes anything unless a function says so and is handed the owner's yes.
"""
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

STEP = "beads"
BD_FLOOR = (1, 0, 4)  # the bd in use when this was written; raise it only with a reason
PROBLEMS = frozenset({"missing", "warning", "broken"})
_GITHUB = re.compile(r"(?:https?://(?:[^/@]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
                     r"(?P<owner>[A-Za-z0-9][A-Za-z0-9-]*)/(?P<name>[A-Za-z0-9._-]+?)(?:\.git)?/?$")
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+")


def _run(cmd, cwd=None, timeout=60) -> subprocess.CompletedProcess:
    """A subprocess that never raises: a missing tool or a timeout is a non-zero result with the reason on stderr."""
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="surrogateescape", timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"{cmd[0]} timed out after {timeout}s")
    except OSError as e:
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]} could not start: {e.strerror or e}")


def _git_out(cwd: Path, *args: str) -> str | None:
    p = _run(["git", *args], cwd=cwd)
    return p.stdout.strip() or None if p.returncode == 0 else None


def bd_version() -> tuple[int, ...] | None:
    """The installed bd's version from `bd version` (`bd version 1.0.4 (ce242a879: HEAD@...)` -> (1, 0, 4)); None when
    bd is missing or its output cannot be read."""
    if not shutil.which("bd"):
        return None
    p = _run(["bd", "version"], timeout=30)
    m = re.search(r"\bversion\s+(\d+)\.(\d+)\.(\d+)", p.stdout) if p.returncode == 0 else None
    return tuple(int(x) for x in m.groups()) if m else None


def beads_repo_slug(repo: Path, override: str | None) -> tuple[str, str]:
    """(owner, name) of the project's beads GitHub repo: `<origin owner>/<origin repo name>-beads`, or the override
    `OWNER/NAME` as given. ValueError when there is no override and origin is not a GitHub URL (or a bad override)."""
    if override:
        if not _SLUG.fullmatch(override):
            raise ValueError(f"--beads-repo must look like OWNER/NAME, not {override!r}")
        owner, name = override.split("/", 1)
        return owner, name
    url = _git_out(repo, "remote", "get-url", "origin")
    m = _GITHUB.fullmatch(url) if url else None
    if not m:
        raise ValueError("the project's origin is not a GitHub URL, so the beads repo's name cannot be worked out; "
                         "pass --beads-repo OWNER/NAME")
    return m["owner"], f"{m['name']}-beads"


@dataclass
class BeadsState:
    kind: str  # "none" | "folder" | "link" | "other"
    beads_repo: Path | None = None  # the folder holding the linked `.beads` (a link whose target is `<dir>/.beads`)
    ignored: bool = False  # `.beads` is git-ignored in the project repo
    git_remote: str | None = None  # origin of the beads repo
    dolt_remote: str | None = None  # the database's Dolt remote URL
    hooks_path_into_beads: bool = False


def _dolt_remote(repo: Path) -> str | None:
    if not shutil.which("bd"):
        return None
    p = _run(["bd", "dolt", "remote", "list"], cwd=repo, timeout=30)
    if p.returncode != 0:
        return None
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and ("://" in parts[1] or "@" in parts[1]):
            return parts[1]
    return None


def inspect(repo: Path) -> BeadsState:
    """Reads only: the filesystem, `git config`, `git check-ignore`, `git remote get-url`, `bd dolt remote list`."""
    beads = repo / ".beads"
    if beads.is_symlink():
        target = Path(os.path.realpath(beads))
        kind = "link" if target.is_dir() else "other"
    elif beads.is_dir():
        kind, target = "folder", beads
    elif beads.exists():
        kind, target = "other", beads
    else:
        kind, target = "none", beads
    st = BeadsState(kind)
    hp = _git_out(repo, "config", "core.hooksPath")
    if hp:
        hp_abs = Path(os.path.realpath(repo / hp)) if not os.path.isabs(hp) else Path(os.path.realpath(hp))
        st.hooks_path_into_beads = ".beads" in Path(hp).parts or (kind in ("link", "folder") and hp_abs.is_relative_to(target))
    if kind == "none":
        return st
    st.ignored = _run(["git", "check-ignore", "-q", ".beads"], cwd=repo).returncode == 0
    if kind == "link" and target.name == ".beads":
        st.beads_repo = target.parent
        st.git_remote = _git_out(st.beads_repo, "remote", "get-url", "origin")
    if kind in ("link", "folder"):
        st.dolt_remote = _dolt_remote(repo)
    return st


def check(repo: Path, state: BeadsState) -> list[tuple[str, str, str]]:
    """What the owner should know about this project's beads, as `say` triples. Reads only."""
    if not shutil.which("bd"):
        return [(STEP, "missing", "`bd` is not on PATH; the coordinator and lanes use beads for issues and handovers "
                                  "(see issues.md). Install bd (machine-setup names the version), then re-run.")]
    out: list[tuple[str, str, str]] = []
    if state.kind == "none":
        out.append((STEP, "missing", f"{repo} has no beads workspace; agents will find no issue tracker. Run "
                                     "project-setup in a terminal: it offers to set beads up in the standard layout "
                                     "(a private beads repo on GitHub, linked as .beads) or to restore it, on your yes."))
    elif state.kind == "other":
        out.append((STEP, "broken", f"{repo}/.beads is not a usable beads folder (a dangling link or a file); nothing "
                                    "was changed. Point it at a beads folder or remove it, then re-run."))
    elif state.kind == "folder":
        out.append((STEP, "warning", f"{repo}/.beads is a plain folder in the project, not the standard layout (a link "
                                     "to a private <project>-beads repo): project-setup offers the move in a terminal."))
        if not state.dolt_remote:
            out.append((STEP, "warning", "beads has no off-machine copy: the database has no Dolt remote, so its issues "
                                         "and memories exist only on this machine."))
    else:  # link
        if not state.ignored:
            out.append((STEP, "warning", f"{repo}/.beads is not git-ignored: add the line `.beads` to {repo}/.gitignore "
                                         "(otherwise lanes cannot be removed, lc-2rx). No link was drafted."))
        if state.beads_repo is None:
            out.append((STEP, "warning", f"the .beads link points at {os.path.realpath(repo / '.beads')}, which is not "
                                         "a `<beads-repo>/.beads` folder; not the standard layout. Nothing was changed."))
        elif not state.git_remote:
            out.append((STEP, "warning", f"beads has no off-machine copy: the beads repo {state.beads_repo} has no git "
                                         "remote to push to. Add the private GitHub repo as origin, then push."))
        if not state.dolt_remote:
            out.append((STEP, "warning", "beads has no off-machine copy: the database has no Dolt remote "
                                         "(`bd dolt remote add origin git+https://github.com/<owner>/<project>-beads.git`)."))
    if state.hooks_path_into_beads:
        out.append((STEP, "warning", f"git's core.hooksPath in {repo} points into .beads, so bd's git hooks run on every "
                                     "commit of this project; unset it (`git config --unset core.hooksPath`)."))
    if not out:
        out.append((STEP, "exists", f"{repo}/.beads -> {state.beads_repo}/.beads, with git and Dolt remotes"))
    return out
