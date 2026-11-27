"""The ratchet's maintenance merges (design: docs/superpowers/specs/2026-11-27-ratchet-maintenance-merges-design.md).

A maintenance item is a PR whose every changed path matches the project's maintenance globs (default DEFAULT_GLOBS; a project
sets its own as `maintenance_paths` in its settings store, outside the repo) and none of NEVER: such an item merges into the
integration branch through `away-merge --maintenance`, and the away branch then takes the integration branch in (take_in), so
it does not drift. Everything here reads; take_in is the one write (a merge commit pushed to the away branch, never forced).
"""
import json
import re
import subprocess
from pathlib import Path

from textscan import HEADER, _unquote

DEFAULT_GLOBS = ("docs/**", "**/*.md", "tests/**")
# never maintenance, whatever the project's globs say: CI and the files that change how git, the scans or the tooling behave
NEVER = (".github/**", ".gitlab-ci.yml", ".gitlab/**", ".circleci/**", ".travis.yml", "**/Jenkinsfile", "Jenkinsfile",
         "azure-pipelines.yml", ".azure-pipelines/**", ".buildkite/**", "bitbucket-pipelines.yml", ".drone.yml",
         ".woodpecker.yml", ".woodpecker/**", ".forgejo/**", ".gitea/**", "appveyor.yml", ".appveyor.yml",
         ".pre-commit-config.yaml", "**/.gitattributes", ".gitmodules", ".gitleaks.toml", "**/.gitignore", ".claude/**",
         ".cotal/**", ".lane-control-root", "orca.yaml")
# a glob that matches any of these is too broad: it would make plain code maintenance
CODE_SAMPLES = ("app.py", "src/app.py", "lib/x.js", "main.go", "scripts/run.sh", "Makefile", "package.json")
FILE_LIMIT = 300  # GitHub's compare lists at most 300 files: more cannot be checked, so it is refused
TAKE_IN_PREFIX = "ratchet: take in "  # the subject of the merge that brings the integration branch into the away branch
LINE = "Ratchet-maintenance: "        # the line a maintenance merge's message carries, naming the period's away branch
SUBJECT = "maintenance: "


def glob_re(glob: str) -> "re.Pattern":
    """`**` crosses directories (`**/` also matches none), `*` and `?` stay within one name."""
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif glob.startswith("**", i):
            out += ".*"
            i += 2
        elif glob[i] == "*":
            out += "[^/]*"
            i += 1
        elif glob[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(glob[i])
            i += 1
    return re.compile(out)


def _matches(path: str, globs) -> "str | None":
    return next((g for g in globs if glob_re(g).fullmatch(path)), None)


def bad_glob(g) -> "str | None":
    """Why `g` cannot be a maintenance glob, or None."""
    if not isinstance(g, str) or not g.strip() or g.startswith(("/", "~", "!")) or ".." in g.split("/"):
        return f"{g!r} is not a glob relative to the repo root"
    hit = _matches_any_sample(g)
    if hit:
        return f"{g!r} also matches plain code ({hit}), so it would make code maintenance"
    return None


def _matches_any_sample(g: str) -> "str | None":
    rx = glob_re(g)
    return next((s for s in CODE_SAMPLES if rx.fullmatch(s)), None)


def globs_for(checkout: Path) -> "tuple[list, str, str | None]":
    """(globs, where from, problem) for a project: its settings store's `maintenance_paths`, else DEFAULT_GLOBS. A store that
    cannot be read, or a value that is not a list of good globs, is a problem (the merge is refused, never guessed at)."""
    import machineconf
    f = machineconf.settings_file(checkout)
    try:
        data = json.loads(f.read_text())
    except FileNotFoundError:
        return list(DEFAULT_GLOBS), "the default", None
    except (OSError, ValueError) as e:
        return [], str(f), f"{f} cannot be read ({e})"
    v = data.get("maintenance_paths") if isinstance(data, dict) else None
    if v is None:
        return list(DEFAULT_GLOBS), "the default", None
    if not isinstance(v, list) or not v:
        return [], str(f), f"{f}: maintenance_paths must be a non-empty list of globs"
    for g in v:
        why = bad_glob(g)
        if why:
            return [], str(f), f"{f}: maintenance_paths: {why}"
    return list(v), str(f), None


def diff_modes(diff: str) -> dict:
    """{path: mode} for every file a diff leaves as a symlink (120000), a submodule (160000) or an executable (100755)."""
    out = {}
    for block in re.split(r"^(?=diff --git )", diff, flags=re.M):
        m = HEADER.match(block)
        if not m:
            continue
        new = _unquote(m.group(3)) if m.group(3) is not None else m.group(4)
        head = block.split("\n@@", 1)[0]
        modes = re.findall(r"^(?:new file mode|new mode) (\d{6})$", head, re.M) or re.findall(
            r"^index [0-9a-f]+\.\.[0-9a-f]+ (\d{6})$", head, re.M)
        if re.search(r"^deleted file mode", head, re.M):
            continue
        if modes and modes[-1] in ("120000", "160000", "100755"):
            out[new] = modes[-1]
    return out


def problems(paths, globs, deny, modes: dict) -> list:
    """Why these paths are not a maintenance item ([] when they are): a path in NEVER, one that matches no maintenance glob,
    one a deny path refuses, a symlink, submodule or executable."""
    out = []
    kinds = {"120000": "a symlink", "160000": "a submodule", "100755": "an executable file"}
    for p in sorted(paths):
        never = _matches(p, NEVER)
        d = _matches(p, deny)
        if never:
            out.append(f"{p} is a CI or tooling file ({never}), never maintenance")
        elif d:
            out.append(f"{p} matches the owner's deny path {d}")
        elif not _matches(p, globs):
            out.append(f"{p} matches no maintenance glob ({', '.join(globs)})")
        elif p in modes:
            out.append(f"{p} is {kinds[modes[p]]}, never maintenance")
    return out


def compare_paths(checkout: Path, gh, integration: str, head: str) -> "tuple[set | None, str]":
    """(every old and new path GitHub lists for the compare `integration...head`, why not): the files the merge brings in, read
    at the exact head sha. More than FILE_LIMIT files, or an answer that is not a file list, is a why."""
    try:
        out = json.loads(gh(["api", f"repos/{{owner}}/{{repo}}/compare/{integration}...{head}"], checkout))
    except Exception as e:  # textscan.Refuse, ValueError
        return None, f"GitHub's file list for {integration}...{head[:7]} cannot be read ({e})"
    files = out.get("files") if isinstance(out, dict) else None
    if not isinstance(files, list) or not all(isinstance(f, dict) and isinstance(f.get("filename"), str) for f in files):
        return None, "GitHub's compare gave no file list"
    if len(files) >= FILE_LIMIT:
        return None, f"the PR changes {len(files)} or more files, more than GitHub's compare lists ({FILE_LIMIT}): too many to check"
    found = set()
    for f in files:
        found.add(f["filename"])
        if isinstance(f.get("previous_filename"), str):
            found.add(f["previous_filename"])
    return found, ""


def _git(repo: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout)


def _resolve(repo: Path, rev: str) -> "str | None":
    r = _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def fetch(repo: Path, away: str, integration: str) -> bool:
    return _git(repo, "fetch", "-q", "origin", f"+refs/heads/{away}:refs/remotes/origin/{away}",
                f"+refs/heads/{integration}:refs/remotes/origin/{integration}").returncode == 0


def take_in(repo: Path, away: str, integration: str, sha: str, pr: int) -> "tuple[str, str]":
    """Merge the integration branch at the maintenance merge commit `sha` (its tip when `sha` is not known here) into `away` on
    origin: a merge commit with both parents, made with plumbing (the checkout's files, index and branches are not touched) and
    pushed without force, so an away branch that moved in between refuses the push. Never a rebase. ("merged", sha of the new
    commit) | ("up to date", target) | ("conflict", why) | ("failed", why); the target is the commit that was to be taken in."""
    try:
        if not fetch(repo, away, integration):
            return "failed", f"origin's {away} and {integration} could not be fetched"
        target = (_resolve(repo, sha) if re.fullmatch(r"[0-9a-f]{7,40}", sha or "") else None) or _resolve(
            repo, f"refs/remotes/origin/{integration}")
        tip = _resolve(repo, f"refs/remotes/origin/{away}")
        if not target or not tip:
            return "failed", f"{integration} or {away} cannot be read on origin"
        if _git(repo, "merge-base", "--is-ancestor", target, tip).returncode == 0:
            return "up to date", target
        tree = _git(repo, "merge-tree", "--write-tree", "--no-messages", tip, target)
        if tree.returncode == 1:
            files = [l for l in tree.stdout.splitlines()[1:] if l.strip()]
            names = sorted({l.split("\t", 1)[-1] for l in files if "\t" in l})
            return "conflict", (f"merging {integration} ({target[:7]}) into {away} conflicts"
                                + (f" in {', '.join(names[:5])}" if names else ""))
        if tree.returncode:
            return "failed", f"git merge-tree failed ({(tree.stderr.strip() or 'no output').splitlines()[-1]})"
        msg = (f"{TAKE_IN_PREFIX}{integration} after maintenance #{pr}\n\nThe away branch takes in {integration} after a "
               "maintenance merge, so it does not drift (a merge, never a rebase).")
        c = _git(repo, "commit-tree", tree.stdout.split()[0], "-p", tip, "-p", target, "-m", msg)
        if c.returncode:
            return "failed", f"the merge commit could not be made ({(c.stderr.strip() or 'no output').splitlines()[-1]})"
        new = c.stdout.strip()
        p = _git(repo, "push", "-q", "origin", f"{new}:refs/heads/{away}")
        if p.returncode:
            return "failed", (f"the push to {away} was refused ({(p.stderr.strip() or 'no output').splitlines()[-1]}); "
                              "nothing was forced")
        _git(repo, "update-ref", f"refs/remotes/origin/{away}", new)
        return "merged", new
    except (OSError, subprocess.SubprocessError) as e:
        return "failed", f"git could not run ({e})"


def pending(repo: Path, away: str, integration: str, merges: list) -> "str | None":
    """Why the away branch has not taken in an earlier maintenance merge yet (a take-in that conflicted or failed), or None.
    Read from git: the target each recorded maintenance merge was to bring in must be in origin's away branch."""
    todo = [m for m in merges if isinstance(m, dict) and m.get("kind") == "maintenance" and m.get("taken_in") is not True]
    if not todo:
        return None
    if not fetch(repo, away, integration):
        return f"origin's {away} cannot be fetched to see whether the earlier maintenance merges are in it"
    for m in todo:
        target = m.get("target") or m.get("sha") or ""
        full = _resolve(repo, target) if re.fullmatch(r"[0-9a-f]{7,40}", target) else None
        if not full or _git(repo, "merge-base", "--is-ancestor", full, f"refs/remotes/origin/{away}").returncode:
            return (f"{away} has not taken in the maintenance merge of PR #{m.get('pr')} ({target[:7] or 'commit unknown'}) yet"
                    f" ({m.get('take_in') or 'not taken in'})")
    return None


def items(repo: Path, integration: str, away: str) -> "list | None":
    """The maintenance merges of this away branch's periods, oldest first, as (title, PR number or None, short sha): the
    integration branch's first-parent merges whose message carries `Ratchet-maintenance: <away>`. None when git cannot be read
    (the caller has fetched)."""
    try:
        r = _git(repo, "log", "--first-parent", "--merges", "--reverse", "--fixed-strings", f"--grep={LINE}{away}",
                 "--format=%h%x1f%s%x1f%b%x1e", f"refs/remotes/origin/{integration}")
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode:
        return None
    out = []
    for rec in r.stdout.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) != 3 or not re.search(rf"^{re.escape(LINE + away)}[ \t]*$", parts[2], re.M):
            continue
        m = re.search(r"^Pull request #(\d+)[ \t]*$", parts[2], re.M)
        title = parts[1][len(SUBJECT):] if parts[1].startswith(SUBJECT) else parts[1]
        out.append((title.strip(), int(m.group(1)) if m else None, parts[0].strip()))
    return out
