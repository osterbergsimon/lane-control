"""Beads handling for lane-control: where a project's beads lives, whether it has an off-machine copy, and the steps that
move it to the standard layout or restore it (design: docs/superpowers/specs/2026-11-22-beads-handling-design.md).

The layout: a private GitHub repo `<owner>/<project>-beads`, checked out at `~/code/<project>-beads`; the project's
`.beads` is a git-ignored symlink to `<beads-repo>/.beads`; the Dolt remote of the database is that same GitHub repo.

bd, gh and git are only ever run as subprocesses. Every function that reports returns `say` triples
`(step, what, detail)` (project-setup's message shape, step `beads`); `what` in PROBLEMS means the owner should read it.
Nothing here creates, moves or pushes anything unless a function says so and is handed the owner's yes.
"""
import os
import json
import re
import shlex
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
    url = _git_out(repo, "config", "--get", "remote.origin.url")
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
        st.git_remote = _git_out(st.beads_repo, "config", "--get", "remote.origin.url")
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


# --- the session hook -------------------------------------------------------------------------------------------------

PRIME_CANDIDATES = ("bd prime",)  # `bd prime --hook-json` does not exist in bd 1.0.4; --memories-only is for
# compact-only hooks, and ours also fires at start, so the plain command is the one


def bd_prime_command(cwd: Path | None = None) -> str | None:
    """The command for the beads SessionStart hook: the first candidate that runs once with exit 0 here, else None
    (bd missing, or too old to have `bd prime`)."""
    if not shutil.which("bd"):
        return None
    for cand in PRIME_CANDIDATES:
        if _run(shlex.split(cand), cwd=cwd, timeout=30).returncode == 0:
            return cand
    return None


def _hook_commands(settings: Path) -> list[str]:
    try:
        data = json.loads(settings.read_text())
    except (OSError, ValueError):
        return []
    out: list[str] = []
    hooks = data.get("hooks") if isinstance(data, dict) else None
    for entries in (hooks.values() if isinstance(hooks, dict) else ()):
        for e in entries if isinstance(entries, list) else ():
            for h in (e.get("hooks") if isinstance(e, dict) and isinstance(e.get("hooks"), list) else ()):
                if isinstance(h, dict) and isinstance(h.get("command"), str):
                    out.append(h["command"])
    return out


def _bd_words(command: str) -> list[str] | None:
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    return words if len(words) >= 2 and words[0] == "bd" else None


def committed_bd_hooks(repo: Path) -> list[tuple[str, bool]]:
    """Each `bd ...` command in the project's committed `.claude/settings.json` hooks, and whether bd accepts it. Only
    `bd prime` is run, with its own flags and no shell; any other bd command is probed with `bd <subcommand> --help`
    (never the command itself). Nothing in the file is changed."""
    out = []
    for command in _hook_commands(repo / ".claude" / "settings.json"):
        words = _bd_words(command)
        if not words:
            continue
        if words[1] == "prime":
            probe = words
        elif re.fullmatch(r"[a-z][a-z-]*", words[1]):
            probe = ["bd", words[1], "--help"]
        else:
            continue
        out.append((command, _run(probe, cwd=repo, timeout=30).returncode == 0))
    return out


def hook_notes(repo: Path) -> list[tuple[str, str, str]]:
    """What the committed and local Claude settings say about the beads hook, as `say` triples: a committed hook bd
    rejects (named, with the fix; it is the project's file, so it is never edited), a working one (lane-control adds
    none of its own), and a duplicate (a committed working one plus one in the uncommitted settings)."""
    step = "beads-hook"
    out: list[tuple[str, str, str]] = []
    committed = committed_bd_hooks(repo)
    for command, runs in committed:
        if not runs:
            out.append((step, "broken", f"{repo}/.claude/settings.json (committed) runs `{command}`, which this bd "
                                        "rejects, so every session start shows an error. It is the project's committed "
                                        "file, so setup does not edit it: change the command to `bd prime` in a lane."))
    working = [c for c, ok in committed if ok and (_bd_words(c) or [""] * 2)[1] == "prime"]
    if working:
        out.append((step, "exists", f"committed settings.json already runs `{working[0]}` at session start; "
                                    "lane-control adds no second one"))
        mine = [c for c in _hook_commands(repo / ".claude" / "settings.local.json") if _bd_words(c)
                and _bd_words(c)[1] == "prime"]
        if mine:
            out.append((step, "warning", f"`{mine[0]}` is also in {repo}/.claude/settings.local.json, so bd prime runs "
                                         "twice at session start; remove the local one."))
    return out


# --- moving a plain folder to the layout ------------------------------------------------------------------------------

def _line(what: str, detail: str) -> tuple[str, str, str]:
    return (STEP, what, detail)


def _link_steps(repo: Path, state: BeadsState) -> list[tuple[str, str, str]]:
    """A project already in the layout: each step that is done says `exists`; nothing is written or pushed. What is not
    done is for `check` to name; this repairs nothing."""
    out = []
    if state.beads_repo:
        out.append(_line("exists", f"beads repo {state.beads_repo}"))
        out.append(_line("exists", f"{repo}/.beads links to {state.beads_repo}/.beads"))
    if state.ignored:
        out.append(_line("exists", ".beads is git-ignored in the project"))
    if state.git_remote:
        out.append(_line("exists", f"git remote of the beads repo: {state.git_remote}"))
    if state.dolt_remote:
        out.append(_line("exists", f"Dolt remote: {state.dolt_remote}"))
    if state.beads_repo and not state.hooks_path_into_beads:
        out.append(_line("exists", "no core.hooksPath into beads"))
    return out


def _outputs(cwd: Path, *args: str) -> str:
    p = _run(["bd", *args], cwd=cwd, timeout=60)
    return f"{p.returncode}\n{p.stdout}"


def _exclude_file(repo: Path) -> Path | None:
    p = _run(["git", "rev-parse", "--git-path", "info/exclude"], cwd=repo)
    return (repo / p.stdout.strip()) if p.returncode == 0 and p.stdout.strip() else None


def _ensure_ignored(repo: Path) -> list[tuple[str, str, str]]:
    """`.beads` git-ignored in the project: when it is not, a `/.beads` line in the uncommitted .git/info/exclude (nothing
    about beads enters the project's tracked files; a `.beads/` line in .gitignore does not match a link)."""
    if _run(["git", "check-ignore", "-q", ".beads"], cwd=repo).returncode == 0:
        return []
    ex = _exclude_file(repo)
    if ex is None:
        return [_line("warning", f"{repo}/.beads is not git-ignored and the exclude file could not be found: add the line "
                                 "`.beads` to .gitignore by hand")]
    ex.parent.mkdir(parents=True, exist_ok=True)
    with ex.open("a") as f:
        f.write("\n# lane-control: the beads link (never committed)\n/.beads\n")
    return [_line("created", f"/.beads line in {ex} (uncommitted: nothing about beads enters the project's files)")]


def adopt_layout(repo: Path, state: BeadsState, slug: tuple[str, str], yes) -> list[tuple[str, str, str]]:
    """Move a plain `.beads` folder to the layout, on the owner's yes. `yes` is `question -> bool`, or None when there
    is no terminal (then only the plan is said and nothing is touched). Two yeses: the move, and the GitHub repo.

    Order: private GitHub repo -> beads repo at ~/code/<name> (git init, origin) -> COPY the folder in -> the database
    must open from the new place with the same issues and memories -> Dolt remote, `bd dolt push`, then commit and
    git push (the config now records the Dolt remote) -> the
    original is renamed aside (kept, never deleted) and the symlink put in its place -> ignore line (the uncommitted
    .git/info/exclude: nothing about beads enters the project's tracked files) -> unset a core.hooksPath into beads.
    On any failure it stops with the original `.beads` exactly where it was."""
    if state.kind == "link":
        return _link_steps(repo, state)
    if state.kind != "folder":
        return []
    owner, name = slug
    full = f"{owner}/{name}"
    dest = Path.home() / "code" / name
    plan = (f"create the private GitHub repo {full}; copy {repo}/.beads into {dest}/.beads, check the database opens from "
            f"there, commit and push it, set the Dolt remote (git+https://github.com/{full}.git) and run `bd dolt push`; "
            f"then keep the original folder beside it and link {repo}/.beads to the copy")
    if yes is None:
        return [_line("would", f"{plan}. Run project-setup in a terminal to do it on your yes; nothing was changed.")]

    def stop(why: str):
        return [_line("broken", f"{why} The original {repo}/.beads was not touched.")]

    missing = [t for t in ("bd", "gh", "git") if not shutil.which(t)]
    if missing:
        return stop(f"cannot move beads to the layout: {', '.join(missing)} is not on PATH.")
    if _run(["gh", "auth", "status"], timeout=30).returncode != 0:
        return stop("cannot move beads to the layout: gh is not signed in (run `gh auth login`).")
    if dest.exists() or dest.is_symlink():
        return stop(f"{dest} already exists, so the beads repo was not created there (never overwritten); move it away "
                    "or restore from it by hand.")
    if _run(["gh", "repo", "view", full], timeout=30).returncode == 0:
        return stop(f"the GitHub repo {full} already exists, so a second copy of beads would not be pushed into it; if it "
                    "holds this project's beads, restore from it on a new machine, or pass --beads-repo OWNER/NAME.")
    if not yes(f"Move {repo}/.beads to a private repo {full} (copy to {dest}, push, link it)? [y/N] "):
        return [_line("skipped", "not moved at your answer; nothing was changed")]
    if not yes(f"Create the private GitHub repo {full} now? This publishes beads data to GitHub (private). [y/N] "):
        return [_line("skipped", "no GitHub repo was created at your answer; nothing was changed")]

    out: list[tuple[str, str, str]] = []
    old = repo / ".beads"
    created_dest = False
    try:
        p = _run(["gh", "repo", "create", full, "--private"], timeout=120)
        if p.returncode != 0:
            return stop(f"`gh repo create {full} --private` failed: {(p.stderr.strip() or p.stdout.strip())[-200:]}.")
        out.append(_line("created", f"private GitHub repo {full}"))
        dest.mkdir(parents=True)
        created_dest = True
        for cmd in (["git", "init", "-q", "-b", "main"],
                    ["git", "remote", "add", "origin", f"https://github.com/{full}.git"]):
            if _run(cmd, cwd=dest).returncode != 0:
                raise RuntimeError(f"`{' '.join(cmd[:3])}` failed in {dest}.")
        meta = old / "metadata.json"
        if meta.is_file() and '"server"' in meta.read_text(errors="replace"):
            _run(["bd", "dolt", "stop"], cwd=repo, timeout=60)  # a server owns the files: stop it before copying them
        shutil.copytree(old, dest / ".beads", symlinks=True)
        out.append(_line("created", f"{dest}/.beads (a copy of the project's folder)"))
        for sub in (("where",), ("memories",), ("list", "--all")):
            if _outputs(repo, *sub) != _outputs(dest, *sub) or _outputs(dest, *sub).startswith("1\n"):
                raise RuntimeError(f"the database does not open the same from {dest} (`bd {' '.join(sub)}` differs or fails).")
        out.append(_line("exists", "the database opens from the new place with the same issues and memories"))
        for cmd in (["bd", "dolt", "remote", "add", "origin", f"git+https://github.com/{full}.git"], ["bd", "dolt", "push"]):
            p = _run(cmd, cwd=dest, timeout=300)
            if p.returncode != 0:
                raise RuntimeError(f"`{' '.join(cmd[:4])}` failed: {(p.stderr.strip() or p.stdout.strip())[-200:]}.")
        # after the Dolt remote: it is recorded in .beads/config.yaml (sync.remote), which a restore reads from git
        for cmd in (["git", "add", ".beads"], ["git", "commit", "-q", "-m", "Beads: initial copy of the project's beads"]):
            if _run(cmd, cwd=dest).returncode != 0:
                raise RuntimeError(f"`{' '.join(cmd[:2])}` failed in {dest}.")
        p = _run(["git", "push", "-q", "-u", "origin", "main"], cwd=dest, timeout=180)
        if p.returncode != 0:
            raise RuntimeError(f"`git push` to {full} failed: {p.stderr.strip()[-200:]}.")
        out.append(_line("created", f"pushed {full}: git history and the Dolt database (issues and memories)"))
    except (RuntimeError, OSError, shutil.Error) as e:
        # the GitHub repo and the copy stay (named); the project's folder was never touched
        what = f"{dest} and the GitHub repo {full} were left as they are for you to look at." if created_dest else ""
        return out + stop(f"{e} {what}".strip())
    # switch over: the original is renamed aside, never deleted
    aside = old.parent.parent / f"{repo.name}.beads-original"
    n = 1
    while aside.exists() or aside.is_symlink():
        n += 1
        aside = old.parent.parent / f"{repo.name}.beads-original-{n}"
    try:
        shutil.move(str(old), str(aside))
    except OSError as e:
        return out + stop(f"could not set the original aside: {e}.")
    try:
        old.symlink_to(dest / ".beads")
    except OSError as e:
        shutil.move(str(aside), str(old))
        return out + stop(f"could not make the link: {e}.")
    out.append(_line("moved", f"{repo}/.beads is now a link to {dest}/.beads; the original folder is kept at {aside} "
                              "(delete it when you have seen beads work from the new place)"))
    if _run(["bd", "where"], cwd=repo, timeout=30).returncode != 0:
        old.unlink()
        shutil.move(str(aside), str(old))
        return out + stop("bd does not find the database through the link; put the folder back as it was.")
    out += _ensure_ignored(repo)
    if _run(["git", "config", "--local", "--get", "core.hooksPath"], cwd=repo).stdout.strip() and \
            inspect(repo).hooks_path_into_beads:
        _run(["git", "config", "--local", "--unset", "core.hooksPath"], cwd=repo)
        out.append(_line("moved", "core.hooksPath no longer points into beads"))
    return out


# --- restore on a new machine or a host -------------------------------------------------------------------------------

SIGN_IN = ("gh is not signed in here: run `gh auth login` yourself. On a box use a fine-grained token limited to the "
           "beads repos (Contents: read and write on `<project>-beads` only); put it in place yourself, setup never "
           "reads or copies a token.")


def _config_has_dolt_remote(beads_dir: Path) -> bool:
    try:
        return re.search(r"^\s*sync\.remote\s*:\s*\S", (beads_dir / "config.yaml").read_text(), re.M) is not None
    except OSError:
        return False


def restore(repo: Path, slug: tuple[str, str], yes) -> list[tuple[str, str, str]]:
    """The project has no beads here, and its beads repo exists on GitHub: clone it to ~/code/<name> (or use the clone
    already there), link `.beads` to it, and bring the database back with `bd bootstrap --yes`, which reads the Dolt remote
    from the repo's `.beads/config.yaml` and restores issues and memories (tests/test_beads_roundtrip.py). `yes` is
    `question -> bool`, or None without a terminal (then the read-only checks and the plan are said, and nothing is
    changed: a box without a terminal still learns it needs a GitHub sign-in). Never creates a GitHub repo or a fresh
    database: a repo that is not found, or a config with no Dolt remote, stops it with the reason."""
    old = repo / ".beads"
    if old.exists() or old.is_symlink():
        return []
    owner, name = slug
    full = f"{owner}/{name}"
    dest = Path.home() / "code" / name
    plan = (f"clone the private GitHub repo {full} to {dest}, link {repo}/.beads to it and restore the database (issues and "
            "memories) from its Dolt remote")

    def stop(why: str):
        return [_line("broken", f"{why} Nothing was created.")]

    missing = [t for t in ("bd", "gh", "git") if not shutil.which(t)]
    if missing:
        return stop(f"cannot restore beads: {', '.join(missing)} is not on PATH.")
    if _run(["gh", "auth", "status"], timeout=30).returncode != 0:
        return stop(f"cannot restore beads from {full}. {SIGN_IN}")
    if _run(["gh", "repo", "view", full], timeout=30).returncode != 0:
        return stop(f"the beads repo {full} was not found on GitHub for this sign-in (or GitHub could not be reached). It "
                    "is never created empty here, since one was expected: check the name (--beads-repo OWNER/NAME) and, on a "
                    "box, that the token can read it. A new project with no beads yet can start one with `bd init`.")
    reuse = dest.is_dir() and (dest / ".git").exists() and (dest / ".beads").is_dir()
    if (dest.exists() or dest.is_symlink()) and not reuse:
        return stop(f"{dest} already exists and is not a checkout of the beads repo, so it was not touched; move it away.")
    if yes is None:  # no terminal: the read-only checks above have said what is in the way; nothing is changed
        return [_line("would", f"{plan}. Run project-setup in a terminal to do it on your yes; nothing was changed.")]
    if not yes(f"Restore beads: {plan}? [y/N] "):
        return [_line("skipped", "not restored at your answer; nothing was changed")]
    out: list[tuple[str, str, str]] = []
    if reuse:
        out.append(_line("exists", f"beads repo checkout {dest}"))
    else:
        p = _run(["gh", "repo", "clone", full, str(dest)], timeout=300)
        if p.returncode != 0:
            return stop(f"`gh repo clone {full}` failed: {(p.stderr.strip() or p.stdout.strip())[-200:]}.")
        out.append(_line("created", f"{dest} (clone of {full})"))
    if not (dest / ".beads").is_dir():
        return out + stop(f"{dest} has no .beads folder, so there is nothing to link or restore.")
    if not _config_has_dolt_remote(dest / ".beads"):
        return out + stop(f"{dest}/.beads/config.yaml names no Dolt remote (sync.remote), so the database would come back "
                          "empty; it was not bootstrapped. Set the remote in the beads repo (`bd dolt remote add origin "
                          f"git+https://github.com/{full}.git`, push), then re-run.")
    try:
        old.symlink_to(dest / ".beads")
    except OSError as e:
        return out + stop(f"could not link {old}: {e}.")
    p = _run(["bd", "bootstrap", "--yes"], cwd=dest, timeout=600)
    ok = p.returncode == 0 and all(_run(["bd", *sub], cwd=repo, timeout=60).returncode == 0
                                   for sub in (["where"], ["memories"], ["list", "--all"]))
    if not ok:
        old.unlink()
        return out + stop(f"`bd bootstrap --yes` did not restore the database: {(p.stderr.strip() or p.stdout.strip())[-200:] or 'it does not open afterwards'}. "
                          f"The link was removed; the clone {dest} was kept.")
    out.append(_line("created", f"{repo}/.beads links to {dest}/.beads; the database (issues and memories) restored from the "
                                "Dolt remote"))
    return out + _ensure_ignored(repo)
