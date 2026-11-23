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
    unpushed: int | None = None  # commits of the beads repo not on its upstream; None when there is no upstream
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
        n = _git_out(st.beads_repo, "rev-list", "--count", "@{u}..HEAD")
        st.unpushed = int(n) if n and n.isdigit() else None
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
            out.append((STEP, "warning", f"{repo}/.beads is not git-ignored: add the line `/.beads` to the uncommitted "
                                         f"{repo}/.git/info/exclude (or `.beads` to .gitignore; a `.beads/` line does not "
                                         "match a link), otherwise lanes cannot be removed (lc-2rx). Nothing was changed."))
        if state.beads_repo is None:
            out.append((STEP, "warning", f"the .beads link points at {os.path.realpath(repo / '.beads')}, which is not "
                                         "a `<beads-repo>/.beads` folder; not the standard layout. Nothing was changed."))
        elif not state.git_remote:
            out.append((STEP, "warning", f"beads has no off-machine copy: the beads repo {state.beads_repo} has no git "
                                         "remote to push to. Add the private GitHub repo as origin, then push."))
        elif state.unpushed is None:
            out.append((STEP, "warning", f"the beads repo {state.beads_repo} has never been pushed (no upstream branch): "
                                         "run `git push -u origin main` there, then `bd dolt push`."))
        elif state.unpushed:
            out.append((STEP, "warning", f"the beads repo {state.beads_repo} has {state.unpushed} commit(s) not pushed: "
                                         "run `git push` there, then `bd dolt push` (an unpushed backup is lost with this "
                                         "machine)."))
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


def _read_hooks(settings: Path) -> tuple[list[tuple[str, str, str]], str | None]:
    """(event, matcher, command) of every command hook in a Claude settings file, whatever the event or matcher, and why
    the file could not be read (None when it was read, or is simply not there). Reads only."""
    try:
        data = json.loads(settings.read_text())
    except FileNotFoundError:
        return [], None
    except ValueError:
        return [], "is not valid JSON"
    except OSError as e:
        return [], f"could not be read ({e.strerror or e})"
    out: list[tuple[str, str, str]] = []
    hooks = data.get("hooks") if isinstance(data, dict) else None
    for event, entries in (hooks.items() if isinstance(hooks, dict) else ()):
        for e in entries if isinstance(entries, list) else ():
            matcher = e.get("matcher") if isinstance(e, dict) and isinstance(e.get("matcher"), str) else ""
            for h in (e.get("hooks") if isinstance(e, dict) and isinstance(e.get("hooks"), list) else ()):
                if isinstance(h, dict) and isinstance(h.get("command"), str):
                    out.append((event, matcher, h["command"]))
    return out, None


def _hook_commands(settings: Path) -> list[str]:
    return [command for _, _, command in _read_hooks(settings)[0]]


def _bd_words(command: str) -> list[str] | None:
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    return words if len(words) >= 2 and words[0] == "bd" else None


def committed_bd_hooks(repo: Path, include_prime: bool = True) -> list[tuple[str, bool]]:
    """Each `bd ...` command in the project's committed `.claude/settings.json` hooks, and whether bd accepts it. Only
    `bd prime` is run, with its own flags and no shell; any other bd command is probed with `bd <subcommand> --help`
    (never the command itself); `include_prime=False` leaves `bd prime` commands out (prime_hooks judges those). Nothing
    in the file is changed."""
    out = []
    for command in _hook_commands(repo / ".claude" / "settings.json"):
        words = _bd_words(command)
        if not words or re.search(r"[|&;<>$`()\\]", command):  # a shell command line: not probed, not judged
            continue
        if words[1] == "prime":
            if not include_prime:
                continue
            probe = words
        elif re.fullmatch(r"[a-z][a-z-]*", words[1]):
            probe = ["bd", words[1], "--help"]
        else:
            continue
        out.append((command, _run(probe, cwd=repo, timeout=30).returncode == 0))
    return out


# a command line that runs `bd prime`: at the start or after a shell operator, optionally behind wrappers (`then`, `exec`,
# `timeout 10`, `nice`, `sh -c "`, `VAR=1`), with bd's global flags allowed; `echo "bd prime"` and `bd primer` are not it
_WRAPPERS = (r"(?:(?:then|do|else|elif|exec|command|nice|time|nohup|env|\w+=\S*)\s+|timeout(?:\s+-\S+)*\s+\d\S*\s+"
             r"|(?:ba|z|da)?sh\s+-\w*c\s+)*")
_RUNS_PRIME = re.compile(r"(?:^|[;&|({!`\n]|\$\()\s*" + _WRAPPERS + r"[\"']?(?:\S*/)?bd\s+(?:-\S+\s+)*prime(?![\w.-])")
_SIMPLE_MATCHER = re.compile(r"[\w|-]+")
UNCHECKED = ("Not checked: managed settings, plugin hooks and hook scripts (a second `bd prime` there would run it twice).")


def _matcher_covers_startup(matcher: str) -> bool:
    if matcher in ("", "*"):
        return True
    try:
        return re.fullmatch(matcher, "startup") is not None
    except re.error:
        return False


def _matchers_overlap(a: str, b: str) -> bool:
    if a in ("", "*") or b in ("", "*"):
        return True
    if _SIMPLE_MATCHER.fullmatch(a) and _SIMPLE_MATCHER.fullmatch(b):
        return bool(set(a.split("|")) & set(b.split("|")))
    return True  # a regex: assume the two can match the same start


@dataclass
class PrimeHook:
    layer: str  # "user" | "committed" | "local"
    path: Path
    event: str  # the hook event it is under: SessionStart, PreCompact, ...
    matcher: str  # the entry's matcher ("" matches everything)
    command: str
    runs: bool | None  # a plain `bd prime ...` command: whether bd accepts it; None when it was not judged (a shell line,
    # no bd to ask, or bd timed out)

    @property
    def covers_start(self) -> bool:
        """Whether it runs when a new session starts: a SessionStart hook whose matcher matches `startup`."""
        return self.event == "SessionStart" and _matcher_covers_startup(self.matcher)


def claude_user_settings() -> Path:
    """The user's Claude settings file: $CLAUDE_CONFIG_DIR/settings.json when that is set (absolute), else
    ~/.claude/settings.json (the same rule as the harness adapter's). That is project-setup's environment, not
    necessarily a session's: one started with another config folder (an Orca profile) reads that folder's file."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base and os.path.isabs(base) else Path.home() / ".claude") / "settings.json"


def _scan_prime(repo: Path) -> tuple[list[PrimeHook], list[tuple[str, Path, str]]]:
    out: list[PrimeHook] = []
    problems: list[tuple[str, Path, str]] = []
    probed: dict[str, bool | None] = {}  # one probe per distinct command per scan
    have_bd = shutil.which("bd") is not None
    layers = (("user", claude_user_settings()), ("committed", repo / ".claude" / "settings.json"),
              ("local", repo / ".claude" / "settings.local.json"))
    for layer, path in layers:
        entries, why = _read_hooks(path)
        if why:
            problems.append((layer, path, why))
        for event, matcher, command in entries:
            if not _RUNS_PRIME.search(command):
                continue
            words = _bd_words(command)
            plain = words is not None and words[1] == "prime" and not re.search(r"[|&;<>$`()\\]", command)
            runs = None
            if plain and have_bd:
                if command not in probed:
                    rc = _run(words, cwd=repo, timeout=30).returncode
                    probed[command] = None if rc in (124, 127) else rc == 0  # a timeout or no start is not a rejection
                runs = probed[command]
            out.append(PrimeHook(layer, path, event, matcher, command, runs))
    return out, problems


def prime_hooks(repo: Path) -> list[PrimeHook]:
    """Every hook that runs `bd prime`, in each Claude settings layer that applies to a session in `repo`: the user's
    settings (where `bd setup claude --global` puts it), the project's committed .claude/settings.json and the
    checkout's uncommitted .claude/settings.local.json; any event, any matcher (`covers_start` says which ones run when
    a session starts), a plain command or a shell line such as `bd prime 2>/dev/null || true`. Only a plain `bd prime
    ...` command is probed (run once, no shell); a shell line is present but not judged. Not scanned (UNCHECKED):
    managed settings, plugin hooks, hook scripts. Reads only."""
    return _scan_prime(repo)[0]


def _where(h: PrimeHook) -> str:
    return f"{h.path} ({h.event}{f', matcher `{h.matcher}`' if h.matcher else ''})"


def hook_notes(repo: Path) -> list[tuple[str, str, str]]:
    """What the Claude settings layers say about the beads hook, as `say` triples: a settings file that is not valid JSON
    (named, not checked), a hook that runs `bd prime` and that bd rejects (named, with the fix; never edited), a hook
    on an event or matcher that does not cover a session's start (noted; it does not stop lane-control's own), a working
    one already covering the start in the user's or the committed settings (lane-control adds none of its own), and a
    duplicate: overlapping entries on one event. Says what is not scanned. No settings file is changed."""
    step = "beads-hook"
    out: list[tuple[str, str, str]] = []
    for command, runs in committed_bd_hooks(repo, include_prime=False):
        if not runs:
            out.append((step, "broken", f"{repo}/.claude/settings.json (committed) runs `{command}`, which this bd "
                                        "rejects, so every session start shows an error. It is the project's committed "
                                        "file, so setup does not edit it: change the command to `bd prime` in a lane."))
    hooks, problems = _scan_prime(repo)
    for layer, path, why in problems:
        out.append((step, "warning", f"{path} {why}, so it was not checked for a bd prime hook."))
    for h in hooks:
        if h.runs is not False:
            continue
        if h.layer == "committed":
            out.append((step, "broken", f"{h.path} (committed) runs `{h.command}`, which this bd rejects, so every "
                                        "session start shows an error. It is the project's committed file, so setup does "
                                        "not edit it: change the command to `bd prime` in a lane."))
        elif h.layer == "user":
            out.append((step, "broken", f"{h.path} (your Claude settings) runs `{h.command}` ({h.event}), which this bd "
                                        "rejects, so every session start shows an error. Setup does not edit your file: "
                                        "change the command to `bd prime`."))
        else:
            out.append((step, "broken", f"{h.path} (the checkout's uncommitted settings) runs `{h.command}` "
                                        f"({h.event}), which this bd rejects, so every session start shows an error. Setup "
                                        "does not rewrite an entry it did not write: change the command to `bd prime`."))
    live = [h for h in hooks if h.runs is not False]
    for h in live:
        if not h.covers_start:
            out.append((step, "noted", f"`{h.command}` also runs from {_where(h)}; that does not cover a new session's "
                                       "start, so it does not count as the session hook."))
    covering = [h for h in live if h.covers_start]
    elsewhere = [h for h in covering if h.layer != "local"]
    checked = (f"Checked: your Claude settings ({claude_user_settings()}; a session started with another "
               "CLAUDE_CONFIG_DIR, such as an Orca profile, reads another folder), the committed .claude/settings.json "
               f"and the uncommitted settings.local.json. {UNCHECKED}")
    if elsewhere:
        h = elsewhere[0]
        more = len({x.path for x in elsewhere} - {h.path})
        out.append((step, "exists", f"{h.path} already runs `{h.command}` ({h.event}) at session start; lane-control adds "
                                    f"no second one" + (f" ({more} more file(s) run it too)" if more else "") + f". {checked}"))
    elif covering:
        out.append((step, "exists", f"bd prime runs at session start from {covering[0].path} (lane-control's hook). {checked}"))
    for event in dict.fromkeys(h.event for h in live):
        same = [h for h in live if h.event == event]
        dup = [h for h in same if any(g is not h and _matchers_overlap(h.matcher, g.matcher) for g in same)]
        if dup:
            local = next((h for h in dup if h.layer == "local"), None)
            out.append((step, "warning", f"bd prime runs more than once at {event}, from "
                                         + "; ".join(f"{_where(h)}: `{h.command}`" for h in dup)
                                         + ". Remove all but one; setup edits none of these files."
                                         + (f" To fix it, remove the entry in {local.path} (lane-control's own; it is not "
                                            "added again while another one covers session start)." if local else "")))
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


def _snapshot(cwd: Path) -> str | None:
    """What the database holds, path-independent (`bd where` prints the folder, so it differs by construction): memories
    and every issue, closed ones included. None when bd cannot read it."""
    parts = []
    for sub in (["memories"], ["list", "--all", "--limit", "0", "--json"]):
        p = _run(["bd", *sub], cwd=cwd, timeout=60)
        if p.returncode != 0:
            return None
        parts.append(p.stdout)
    return "\n--\n".join(parts)


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


def _credentials_hint() -> str:
    return ("git needs credentials for https here: run `gh auth login` and accept its offer to set up git (or run "
            "`gh auth setup-git`), or use a credential helper; a token held only by gh is not enough for git, and bd "
            "reaches the Dolt remote through git.")


def _git_sync(dest: Path, first: bool = False) -> str | None:
    """Commit what is changed under .beads in the beads repo (nothing to commit is fine) and push when ahead. Returns
    an error text, or None."""
    for cmd in (["git", "add", ".beads"],):
        if _run(cmd, cwd=dest).returncode != 0:
            return f"`git add` failed in {dest}."
    if _run(["git", "diff", "--cached", "--quiet"], cwd=dest).returncode != 0:
        p = _run(["git", "commit", "-q", "-m", "Beads: the project's beads"], cwd=dest)
        if p.returncode != 0:
            return f"`git commit` failed in {dest}: {p.stderr.strip()[-200:]}."
    ahead = _git_out(dest, "rev-list", "--count", "@{u}..HEAD") if not first else "1"
    if first or (ahead or "0") != "0":
        p = _run(["git", "push", "-q", "-u", "origin", "main"], cwd=dest, timeout=180)
        if p.returncode != 0:
            return f"`git push` failed: {p.stderr.strip()[-200:]} {_credentials_hint()}"
    return None


def adopt_layout(repo: Path, state: BeadsState, slug: tuple[str, str], yes) -> list[tuple[str, str, str]]:
    """Move a plain `.beads` folder to the layout, on the owner's yes. `yes` is `question -> bool`, or None when there
    is no terminal (then only the plan is said and nothing is touched). Two yeses: the move, and the GitHub repo.

    Stops first, before anything is created, when: a tool or the gh sign-in is missing, the destination or the GitHub
    repo exists, `.beads` files are tracked in the project's git, or the database is not embedded (a server or shared
    one is not in the folder). Then: private GitHub repo -> beads repo at ~/code/<name> -> COPY the folder in -> the
    copy must hold the same issues and memories -> git commit and push (Dolt cannot push to an empty git remote) -> Dolt
    remote, `bd dolt push` -> commit what bd changed, push -> the folder is compared again (a write made meanwhile
    stops the switch) -> the original is renamed aside (kept, never deleted) and the symlink put in its place -> ignore
    line (uncommitted .git/info/exclude) -> unset a core.hooksPath into beads. On any failure it stops with the original
    `.beads` exactly where it was."""
    if state.kind == "link":
        return _link_steps(repo, state)
    if state.kind != "folder":
        return []
    owner, name = slug
    full = f"{owner}/{name}"
    dest = Path.home() / "code" / name
    plan = (f"create the private GitHub repo {full}; copy {repo}/.beads into {dest}/.beads, check the copy holds the same "
            f"issues and memories, push it (git, then the Dolt database to git+https://github.com/{full}.git); then keep "
            f"the original folder beside it and link {repo}/.beads to the copy")
    if yes is None:
        return [_line("would", f"{plan}. Run project-setup in a terminal to do it on your yes; nothing was changed.")]

    def stop(why: str):
        return [_line("broken", f"{why} The original {repo}/.beads was not touched.")]

    old = repo / ".beads"
    missing = [t for t in ("bd", "gh", "git") if not shutil.which(t)]
    if missing:
        return stop(f"cannot move beads to the layout: {', '.join(missing)} is not on PATH.")
    if _run(["gh", "auth", "status"], timeout=30).returncode != 0:
        return stop("cannot move beads to the layout: gh is not signed in (run `gh auth login`).")
    tracked = [f for f in _run(["git", "ls-files", ".beads"], cwd=repo).stdout.splitlines() if f]
    if tracked:
        return stop(f"{len(tracked)} file(s) under .beads are tracked in this project's git ({', '.join(tracked[:3])}"
                    f"{', ...' if len(tracked) > 3 else ''}): after the move they would show as deleted, and what they "
                    "hold is already in the project's history (think about that if the repo is public). Untrack them "
                    "first (`git rm -r --cached .beads`, commit), then re-run.")
    try:
        mode = json.loads((old / "metadata.json").read_text()).get("dolt_mode", "embedded")
    except (OSError, ValueError, AttributeError):
        mode = "embedded"
    if mode != "embedded":
        return stop(f"this database runs in {mode} mode, so its data is not in the .beads folder (or a running server owns "
                    "it); only embedded databases are moved. Nothing was changed.")
    if dest.exists() or dest.is_symlink():
        return stop(f"{dest} already exists, so it was not touched (never overwritten). If it is what an earlier move of "
                    f"this folder left behind, delete it and the GitHub repo {full}, then re-run. The earlier "
                    f"move may already have pushed its git copy there, so look at that repo's last commit first.")
    if _run(["gh", "repo", "view", full], timeout=30).returncode == 0:
        return stop(f"the GitHub repo {full} already exists. Look at its last commit. If it holds this project's beads "
                    "and is current, restore from it on a new machine instead. If it is what an earlier move of this folder "
                    "left (that move may have pushed its git copy, or only part of it), delete it and the beads folder that "
                    "move made, then re-run. Or pass --beads-repo OWNER/NAME for another name.")
    if not yes(f"Move {repo}/.beads to a private repo {full} (copy to {dest}, push, link it)? Stop agents and lanes that "
               "write to beads first. [y/N] "):
        return [_line("skipped", "not moved at your answer; nothing was changed")]
    if not yes(f"Create the private GitHub repo {full} now? This publishes beads data to GitHub (private). [y/N] "):
        return [_line("skipped", "no GitHub repo was created at your answer; nothing was changed")]

    out: list[tuple[str, str, str]] = []
    created = False
    retry = f"To try again delete the GitHub repo {full} and {dest}, then re-run."
    try:
        p = _run(["gh", "repo", "create", full, "--private"], timeout=120)
        if p.returncode != 0:
            return stop(f"`gh repo create {full} --private` failed: {(p.stderr.strip() or p.stdout.strip())[-200:]}.")
        created = True
        out.append(_line("created", f"private GitHub repo {full}"))
        dest.mkdir(parents=True)
        for cmd in (["git", "init", "-q", "-b", "main"],
                    ["git", "remote", "add", "origin", f"https://github.com/{full}.git"]):
            if _run(cmd, cwd=dest).returncode != 0:
                raise RuntimeError(f"`{' '.join(cmd[:3])}` failed in {dest}.")
        before = _snapshot(repo)
        shutil.copytree(old, dest / ".beads", symlinks=True)
        out.append(_line("created", f"{dest}/.beads (a copy of the project's folder)"))
        after = _snapshot(dest)
        if before is None or after is None or before != after:
            raise RuntimeError(f"the copy in {dest} does not hold the same issues and memories as the original, or bd "
                               "cannot read one of them.")
        out.append(_line("exists", "the database opens from the new place with the same issues and memories"))
        err = _git_sync(dest, first=True)  # first: Dolt refuses to push to a git remote with no branches
        if err:
            raise RuntimeError(err)
        for cmd in (["bd", "dolt", "remote", "add", "origin", f"git+https://github.com/{full}.git"], ["bd", "dolt", "push"]):
            p = _run(cmd, cwd=dest, timeout=300)
            if p.returncode != 0:
                raise RuntimeError(f"`{' '.join(cmd[:4])}` failed: {(p.stderr.strip() or p.stdout.strip())[-250:]}. "
                                   f"{_credentials_hint()}")
        err = _git_sync(dest)  # bd's own commit of sync.remote reaches GitHub, so a restore finds the Dolt remote
        if err:
            raise RuntimeError(err)
        out.append(_line("created", f"pushed {full}: git history and the Dolt database (issues and memories)"))
        if _snapshot(repo) != before:
            raise RuntimeError("beads changed in the project while it was being copied (an agent or lane wrote to it), so "
                               "the copy is stale and nothing was switched. Stop everything that writes to beads.")
    except (RuntimeError, OSError, shutil.Error) as e:
        # the GitHub repo and the copy stay (named); the project's folder was never touched
        return out + stop(f"{e} {retry}" if created else str(e))
    # switch over: the original is renamed aside, never deleted
    aside = old.parent.parent / f"{repo.name}.beads-original"
    n = 1
    while aside.exists() or aside.is_symlink():
        n += 1
        aside = old.parent.parent / f"{repo.name}.beads-original-{n}"
    try:
        shutil.move(str(old), str(aside))
    except OSError as e:
        return out + stop(f"could not set the original aside: {e}. {retry}")
    try:
        old.symlink_to(dest / ".beads")
    except OSError as e:
        shutil.move(str(aside), str(old))
        return out + stop(f"could not make the link: {e}. {retry}")
    out.append(_line("moved", f"{repo}/.beads is now a link to {dest}/.beads; the original folder is kept at {aside} "
                              "(delete it when you have seen beads work from the new place)"))
    if _run(["bd", "where"], cwd=repo, timeout=30).returncode != 0:
        old.unlink()
        shutil.move(str(aside), str(old))
        return out + stop(f"bd does not find the database through the link; the folder was put back. GitHub holds a copy "
                          f"pushed a moment ago that will drift from it. {retry}")
    out += _ensure_ignored(repo)
    if _run(["git", "config", "--local", "--get", "core.hooksPath"], cwd=repo).stdout.strip() and \
            inspect(repo).hooks_path_into_beads:
        _run(["git", "config", "--local", "--unset", "core.hooksPath"], cwd=repo)
        out.append(_line("moved", "core.hooksPath no longer points into beads"))
    return out


# --- restore on a new machine or a host -------------------------------------------------------------------------------

SIGN_IN = ("gh is not signed in here: run `gh auth login` yourself and accept its offer to set up git (or run `gh auth "
           "setup-git`): git and bd reach GitHub over https through git's credentials, not through gh. On a box use a "
           "fine-grained token limited to the beads repos (Contents: read and write on `<project>-beads` only), put in "
           "place by you; setup never reads or copies a token.")


def _same_github_repo(origin: str | None, owner: str, name: str) -> bool:
    """Whether a git origin URL is this GitHub repo, over https or ssh (`git@github.com:owner/name(.git)`); GitHub names
    are not case-sensitive."""
    m = _GITHUB.fullmatch(origin) if origin else None
    return bool(m) and (m["owner"].lower(), m["name"].lower()) == (owner.lower(), name.lower())


def _config_has_dolt_remote(beads_dir: Path) -> bool:
    try:
        return re.search(r"^\s*sync\.remote\s*:\s*\S", (beads_dir / "config.yaml").read_text(), re.M) is not None
    except OSError:
        return False


def restore(repo: Path, slug: tuple[str, str], yes) -> list[tuple[str, str, str]]:
    """The project has no beads here, and its beads repo exists on GitHub: clone it to ~/code/<name> (or use the clone
    already there, when its origin is that repo), link `.beads` to it, and bring the database back with `bd bootstrap
    --yes`, which reads the Dolt remote from the repo's `.beads/config.yaml` and restores issues and memories
    (tests/test_beads_roundtrip.py). `yes` is `question -> bool`, or None without a terminal (then the read-only checks
    and the plan are said, and nothing is changed: a box without a terminal still learns it needs a GitHub sign-in).
    Never creates a GitHub repo or a fresh database: a repo that is not found, git that cannot read it over https, or a
    config with no Dolt remote stops it with the reason."""
    old = repo / ".beads"
    if old.exists() or old.is_symlink():
        return []
    owner, name = slug
    full = f"{owner}/{name}"
    url = f"https://github.com/{full}.git"
    dest = Path.home() / "code" / name
    plan = (f"clone the private GitHub repo {full} to {dest}, link {repo}/.beads to it and restore the database (issues and "
            "memories) from its Dolt remote")

    def stop(why: str, made: bool = False):
        return [_line("broken", why if made else f"{why} Nothing was created.")]

    missing = [t for t in ("bd", "gh", "git") if not shutil.which(t)]
    if missing:
        return stop(f"cannot restore beads: {', '.join(missing)} is not on PATH.")
    if _run(["gh", "auth", "status"], timeout=30).returncode != 0:
        return stop(f"cannot restore beads from {full}. {SIGN_IN}")
    if _run(["gh", "repo", "view", full], timeout=30).returncode != 0:
        return stop(f"the beads repo {full} was not found on GitHub for this sign-in (or GitHub could not be reached). It "
                    "is never created empty here, since one was expected: check the name (--beads-repo OWNER/NAME) and, on a "
                    "box, that the token can read it. A new project with no beads yet can start one with `bd init`.")
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        ls = subprocess.run(["git", "ls-remote", url], capture_output=True, text=True, timeout=60, env=env)
    except (OSError, subprocess.TimeoutExpired):
        ls = None
    if ls is None or ls.returncode != 0:
        return stop(f"gh can see {full} but git cannot read {url} without asking for a password. {_credentials_hint()}")
    reuse = dest.is_dir() and (dest / ".git").exists() and (dest / ".beads").is_dir() and \
        _same_github_repo(_git_out(dest, "config", "--get", "remote.origin.url"), owner, name)
    if (dest.exists() or dest.is_symlink()) and not reuse:
        origin = _git_out(dest, "config", "--get", "remote.origin.url") if (dest / ".git").exists() else None
        shown = re.sub(r"(?<=://)[^/@]*@", "", origin) if origin else None  # never print a token held in a URL
        why = (f" (its origin is {shown}, which is not recognised as {full})" if shown
               else " (it has no origin I can read)" if (dest / ".git").exists() else "")
        return stop(f"{dest} already exists and is not a checkout of {full}{why}, so it was not touched; move it away.")
    if yes is None:  # no terminal: the read-only checks above have said what is in the way; nothing is changed
        return [_line("would", f"{plan}. Run project-setup in a terminal to do it on your yes; nothing was changed.")]
    if not yes(f"Restore beads: {plan}? [y/N] "):
        return [_line("skipped", "not restored at your answer; nothing was changed")]
    out: list[tuple[str, str, str]] = []
    cloned = False
    if reuse:
        out.append(_line("exists", f"beads repo checkout {dest}"))
    else:
        p = _run(["gh", "repo", "clone", full, str(dest)], timeout=300)
        if p.returncode != 0:
            return stop(f"`gh repo clone {full}` failed: {(p.stderr.strip() or p.stdout.strip())[-200:]}.")
        cloned = True
        out.append(_line("created", f"{dest} (clone of {full})"))

    def undo_clone() -> str:
        if cloned:
            shutil.rmtree(dest, ignore_errors=True)  # cheap to redo, and its stale issues.jsonl must not seed a database
            return f"The clone {dest} was removed."
        return f"The clone {dest} was kept."

    if not (dest / ".beads").is_dir():
        return out + stop(f"{dest} has no .beads folder, so there is nothing to link or restore. {undo_clone()}", True)
    if not _config_has_dolt_remote(dest / ".beads"):
        return out + stop(f"{dest}/.beads/config.yaml names no Dolt remote (sync.remote), so the database would come back "
                          "empty; it was not bootstrapped. Set the remote in the beads repo (`bd dolt remote add origin "
                          f"git+https://github.com/{full}.git`, push), then re-run. {undo_clone()}", True)
    try:
        (dest / ".beads").chmod(0o700)  # bd warns on every command when it is group- or world-readable
        old.symlink_to(dest / ".beads")
    except OSError as e:
        return out + stop(f"could not link {old}: {e}. {undo_clone()}", True)
    p = _run(["bd", "bootstrap", "--yes"], cwd=dest, timeout=600)
    ok = p.returncode == 0 and all(_run(["bd", *sub], cwd=repo, timeout=60).returncode == 0
                                   for sub in (["where"], ["memories"], ["list", "--all"]))
    if not ok:
        old.unlink()
        return out + stop(f"`bd bootstrap --yes` did not restore the database: "
                          f"{(p.stderr.strip() or p.stdout.strip())[-200:] or 'it does not open afterwards'}. The link was "
                          f"removed. {undo_clone()}", True)
    out.append(_line("created", f"{repo}/.beads links to {dest}/.beads; the database (issues and memories) restored from the "
                                "Dolt remote"))
    return out + _ensure_ignored(repo)
