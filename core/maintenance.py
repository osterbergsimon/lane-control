"""The ratchet's maintenance merges (design: docs/superpowers/specs/2026-11-27-ratchet-maintenance-merges-design.md).

A maintenance item is a PR whose every changed path is allowed by one of the period's maintenance kinds (KINDS: `docs` by
default, `tests` only when the owner names it) and by the project's own `maintenance_paths` when it has them (which only narrow
the kinds, never widen them), and is none of NEVER (agent instructions, CI, tooling). Such an item merges into the integration
branch through `away-merge --maintenance`, and the away branch then takes the integration branch in (take_in), so it does not
drift. The policy is the tables below, in one place: KINDS, DEFAULT_KINDS, NEVER, the docs and tests rules. Everything here
reads; take_in is the one write (a merge commit pushed to the away branch, never forced).
"""
import json
import re
import subprocess
from pathlib import Path

from textscan import HEADER, _unquote

# --- the policy -------------------------------------------------------------------------------------------------------
KINDS = ("docs", "tests")   # what `ratchet on --maintenance <kinds>` may name
DEFAULT_KINDS = ("docs",)   # bare `--maintenance`
# docs: content files only, under docs/ or the root README and CHANGELOG; no executable, build or config file
DOC_ROOTS = ("docs/",)
DOC_FILES = ("README.md", "CHANGELOG.md")
# an extension ALLOWLIST (the coordinator's call, 2026-11-27): anything else under docs/ is not maintenance, whatever its name
# (no .txt: requirements.txt and CMakeLists.txt are build files)
DOC_TEXT = (".md", ".rst", ".svg")
DOC_IMAGES = (".png", ".jpg", ".jpeg", ".gif", ".webp")  # binary: not scanned for secrets or names (the guide says so)
# tests: NEW test files only, under tests/ or test/, named as a test; never a change to an existing one, and never the
# suite's own machinery (conftest, testhome, stubs, helpers, runners, fixture loaders, shell scripts)
TEST_ROOTS = ("tests/", "test/")
TEST_NAMES = re.compile(r"(?:test_[^/]*\.py|[^/]*_test\.(?:py|go|rs)|[^/]*\.(?:test|spec)\.(?:js|jsx|ts|tsx|mjs|cjs)"
                        r"|[^/]*_spec\.rb|[^/]*Test\.(?:java|kt))")
TEST_MACHINERY_DIRS = {"stubs", "stub", "mocks", "mock", "__mocks__", "__fixtures__", "__snapshots__", "helpers", "helper", "support", "fixtures", "fixture", "utils", "util",
                       "lib", "bin", "scripts", "tools", "runner", "runners", "loaders", "data"}
TEST_MACHINERY_NAMES = re.compile(r"conftest|testhome|__init__|runner|loader|helper|fixture|stub", re.I)
# never maintenance, whatever the kinds or the project's globs say; matched at any depth and in any case
NEVER = (
    # agent instructions and harness config: what agents obey must not change on main without the owner
    "**/AGENTS*.md", "**/CLAUDE*.md", "**/GEMINI*.md", "**/SKILL.md", "**/.claude/**", "**/.opencode/**", "**/.agents/**",
    "**/.codex/**", "**/.pi/**", "**/.gemini/**", "**/.cursor/**", "**/.cursorrules", "**/.windsurfrules", "**/.clinerules",
    "**/.roo/**", "**/.continue/**", "**/.aider*", "**/.mcp.json", "**/opencode.json", "**/opencode.jsonc", "**/skills/**",
    "**/personas/**", "**/templates/**", "**/prompts/**", "**/.cotal/**", "**/.lane-control-root", "**/orca.yaml",
    # plans, specs and spikes agents execute step by step, and the folder forms of the harnesses' rule files (a name list
    # stays incomplete: a project narrows further with maintenance_paths)
    "**/superpowers/**", "**/plans/**", "**/agents/**", "**/commands/**", "**/AGENT.md", "**/QWEN*.md",
    "**/*instructions*.md", "**/.windsurf/**", "**/.clinerules/**", "**/.kiro/**", "**/.junie/**",
    # CI, and the files that change how git, the scans or the tooling behave
    "**/.github/**", "**/.gitlab-ci.yml", "**/.gitlab/**", "**/.circleci/**", "**/.travis.yml", "**/Jenkinsfile",
    "**/azure-pipelines.yml", "**/.azure-pipelines/**", "**/.buildkite/**", "**/bitbucket-pipelines.yml", "**/.drone.yml",
    "**/.woodpecker.yml", "**/.woodpecker/**", "**/.forgejo/**", "**/.gitea/**", "**/appveyor.yml", "**/.appveyor.yml",
    "**/.pre-commit-config.yaml", "**/.gitattributes", "**/.gitmodules", "**/.gitleaks.toml", "**/.gitignore")
FILE_LIMIT = 300  # GitHub's compare lists at most 300 files: 300 or more cannot all be checked, so that is refused
TAKE_IN_PREFIX = "ratchet: take in "  # the subject of the merge that brings the integration branch into the away branch
LINE = "Ratchet-maintenance: "        # the line a maintenance merge's message carries, naming the period's away branch
SUBJECT = "maintenance: "
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def parse_kinds(text: "str | None") -> list:
    """The kinds `--maintenance [<kinds>]` names: none given is DEFAULT_KINDS. ValueError for an unknown one."""
    if text is None or not text.strip():
        return list(DEFAULT_KINDS)
    got = [k.strip().lower() for k in text.split(",") if k.strip()]
    bad = [k for k in got if k not in KINDS]
    if bad or not got:
        raise ValueError(f"--maintenance {text!r}: name kinds from {', '.join(KINDS)} (comma-separated); bare "
                         f"--maintenance is {', '.join(DEFAULT_KINDS)}")
    return [k for k in KINDS if k in got]


def describe(kinds) -> str:
    """What the kinds let through, in the owner's words (`on` and `status` print it)."""
    parts = []
    if "docs" in kinds:
        parts.append(f"docs (files under docs/ and the root README.md and CHANGELOG.md with one of the extensions "
                     f"{' '.join(DOC_TEXT + DOC_IMAGES)}; nothing else, whatever its name)")
    if "tests" in kinds:
        parts.append("new test files under tests/ or test/ (only new ones; never a change to an existing test or the suite's "
                     "machinery). These are code nobody but agents reviewed: they run in your suite and in CI, and a new "
                     "test file can also change how the existing tests behave")
    return "; ".join(parts)


def glob_re(glob: str, flags: int = 0) -> "re.Pattern":
    """`**` crosses directories (`**/` also matches none), `*` and `?` stay within one name. A newline is a character like
    any other (DOTALL), so an odd file name cannot slip past a `**`."""
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
    return re.compile(out, re.S | flags)


def _matches(path: str, globs, flags: int = 0) -> "str | None":
    return next((g for g in globs if glob_re(g, flags).fullmatch(path)), None)


def never(path: str) -> "str | None":
    """The NEVER glob `path` falls under (any case), or None."""
    return _matches(path, NEVER, re.I)


def bad_glob(g) -> "str | None":
    """Why `g` cannot be one of a project's maintenance_paths, or None. A project's globs only narrow the kinds, so each must
    sit inside what a kind allows: start with a kind's folder (docs/, tests/, test/) or name one of its files."""
    if not isinstance(g, str) or not g.strip() or CONTROL.search(g) or g.startswith(("/", "~", "!")) or ".." in g.split("/"):
        return f"{g!r} is not a glob relative to the repo root"
    if not (g.startswith(DOC_ROOTS + TEST_ROOTS) or g in DOC_FILES):
        return (f"{g!r} reaches outside what maintenance allows ({', '.join(DOC_ROOTS + TEST_ROOTS)} or "
                f"{', '.join(DOC_FILES)}): a project's maintenance_paths only narrow the kinds, never add paths")
    return None


def globs_for(checkout: Path) -> "tuple[list | None, str, str | None]":
    """(the project's maintenance_paths or None when it has none, where from, problem). A store that cannot be read, or a
    value that is not a list of globs inside the kinds (bad_glob), is a problem: every maintenance merge is refused, never
    guessed at."""
    import machineconf
    f = machineconf.settings_file(checkout)
    try:
        data = json.loads(f.read_text())
    except FileNotFoundError:
        return None, "the kinds alone", None
    except (OSError, ValueError) as e:
        return None, str(f), f"{f} cannot be read ({e})"
    v = data.get("maintenance_paths") if isinstance(data, dict) else None
    if v is None:
        return None, "the kinds alone", None
    if not isinstance(v, list) or not v:
        return None, str(f), f"{f}: maintenance_paths must be a non-empty list of globs"
    for g in v:
        why = bad_glob(g)
        if why:
            return None, str(f), f"{f}: maintenance_paths: {why}"
    return list(v), str(f), None


def _blocks(diff: str):
    for block in re.split(r"^(?=diff --git )", diff, flags=re.M):
        m = HEADER.match(block)
        if m:
            yield (_unquote(m.group(3)) if m.group(3) is not None else m.group(4)), block


def diff_modes(diff: str) -> dict:
    """{path: mode} for every file a diff leaves as a symlink (120000), a submodule (160000) or an executable (100755)."""
    out = {}
    for new, block in _blocks(diff):
        head = block.split("\n@@", 1)[0]
        if re.search(r"^deleted file mode", head, re.M):
            continue
        modes = re.findall(r"^(?:new file mode|new mode) (\d{6})$", head, re.M) or re.findall(
            r"^index [0-9a-f]+\.\.[0-9a-f]+ (\d{6})$", head, re.M)
        if modes and modes[-1] in ("120000", "160000", "100755"):
            out[new] = modes[-1]
    return out


def diff_binary(diff: str) -> set:
    """The paths whose change a diff shows only as binary (no text lines, so nothing for the scans to read)."""
    return {new for new, block in _blocks(diff)
            if re.search(r"^(?:Binary files .* differ|GIT binary patch)$", block.split("\n@@", 1)[0], re.M)}


def _kind_of(path: str, status: str, kinds) -> "tuple[str | None, str]":
    """(the kind that allows this change, "") or (None, why not)."""
    low = path.lower()
    if path.startswith(DOC_ROOTS) or path in DOC_FILES:
        if "docs" not in kinds:
            return None, f"{path} is docs, which this period's maintenance kinds ({', '.join(kinds)}) do not include"
        if low.endswith(DOC_TEXT + DOC_IMAGES):
            return "docs", ""
        return None, f"{path} is not a docs content file ({', '.join(DOC_TEXT + DOC_IMAGES)}): no executable, build or config file"
    if path.startswith(TEST_ROOTS):
        if "tests" not in kinds:
            return None, (f"{path} is a test file, and this period's maintenance kinds ({', '.join(kinds)}) do not include "
                          "tests (the owner names them: `ratchet on --continue --maintenance docs,tests`)")
        base = path.rsplit("/", 1)[-1]
        if any(d.lower() in TEST_MACHINERY_DIRS for d in path.split("/")[1:-1]) or TEST_MACHINERY_NAMES.search(base):
            return None, f"{path} is part of the suite's machinery (conftest, stubs, helpers, runners, fixtures), never maintenance"
        if status != "added":
            return None, f"{path} is an existing test file ({status or 'changed'}): only a new test file is maintenance"
        if not TEST_NAMES.fullmatch(base):
            return None, f"{path} is not named as a test file (test_*.py, *_test.go, *.test.ts and the like)"
        return "tests", ""
    return None, f"{path} is not a maintenance path (this period: {', '.join(kinds)})"


def problems(files: dict, kinds, project_globs, deny, modes: dict, binary: set) -> list:
    """Why these changes are not a maintenance item ([] when they are). `files` is {path: status} (added, modified, removed,
    renamed; a rename's old name is "removed"). In order, per path: a control character in its name, NEVER, a deny path, no
    kind that allows it, outside the project's maintenance_paths, a symlink, submodule or executable, a binary that is not a
    docs image."""
    out = []
    kinds_ = {"120000": "a symlink", "160000": "a submodule", "100755": "an executable file"}
    for p in sorted(files):
        if CONTROL.search(p):
            out.append(f"{p!r} has a control character in its name, never maintenance")
            continue
        n, d = never(p), _matches(p, deny)
        if n:
            out.append(f"{p} is an agent instruction, CI or tooling file ({n}), never maintenance")
            continue
        if d:
            out.append(f"{p} matches the owner's deny path {d}")
            continue
        kind, why = _kind_of(p, files[p], kinds)
        if not kind:
            out.append(why)
        elif project_globs and not _matches(p, project_globs):
            out.append(f"{p} matches none of the project's maintenance_paths ({', '.join(project_globs)})")
        elif p in modes:
            out.append(f"{p} is {kinds_[modes[p]]}, never maintenance")
        elif p in binary and not (kind == "docs" and p.lower().endswith(DOC_IMAGES)):
            out.append(f"{p} is a binary file, which the scans cannot read: never maintenance")
    return out


def compare_files(checkout: Path, gh, integration: str, head: str) -> "tuple[dict | None, str, str]":
    """({path: status} for every file GitHub lists for the compare `integration...head`, the merge base's sha, why not): the
    files the PR changes since its merge base, read at the exact head sha; a rename gives its new name "renamed" and its old
    one "removed". FILE_LIMIT files or more, or an answer that is not a file list, is a why."""
    try:
        out = json.loads(gh(["api", f"repos/{{owner}}/{{repo}}/compare/{integration}...{head}"], checkout))
    except Exception as e:  # textscan.Refuse, ValueError
        return None, "", f"GitHub's file list for {integration}...{head[:7]} cannot be read ({e})"
    files = out.get("files") if isinstance(out, dict) else None
    if not isinstance(files, list) or not all(isinstance(f, dict) and isinstance(f.get("filename"), str) for f in files):
        return None, "", "GitHub's compare gave no file list"
    if len(files) >= FILE_LIMIT:
        return None, "", f"the PR changes {len(files)} files, and GitHub's compare lists at most {FILE_LIMIT}: too many to check"
    base = ((out.get("merge_base_commit") or {}).get("sha") or "") if isinstance(out.get("merge_base_commit"), dict) else ""
    found = {}
    for f in files:
        found[f["filename"]] = str(f.get("status") or "")
        if isinstance(f.get("previous_filename"), str):
            found.setdefault(f["previous_filename"], "removed")
    return found, base, ""


def main_moved(repo: Path, integration: str, base: str, paths) -> list:
    """Why the merge would change other paths than the PR's own (m1): git's merge follows renames made on the integration
    branch since the merge base, so a PR's edit of a file main renamed (or deleted) lands at a path nobody checked. One line
    per such path; one line too when the merge base cannot be read here (fail closed). The caller has fetched."""
    if not re.fullmatch(r"[0-9a-f]{40}", base or "") or not _resolve(repo, base):
        return [f"the PR's merge base on {integration} ({(base or 'not given')[:12]}) cannot be read here, so what the merge "
                "changes on it cannot be checked"]
    r = _git(repo, "diff", "--name-status", "-M", "-z", base, f"refs/remotes/origin/{integration}")
    if r.returncode:
        return [f"git cannot tell what {integration} changed since the PR's merge base"]
    out, parts, i, mine = [], r.stdout.split("\0"), 0, set(paths)
    while i < len(parts) - 1:
        st = parts[i]
        if st[:1] in ("R", "C"):
            old, new, i = parts[i + 1], parts[i + 2], i + 3
            if old in mine and st[:1] == "R":
                out.append(f"{old} was moved to {new} on {integration} since the lane branched, so the merge would change "
                           f"{new}, which nothing checked: merge {integration} into the lane's branch first")
        else:
            path, i = parts[i + 1], i + 2
            if st[:1] == "D" and path in mine:
                out.append(f"{path} was deleted on {integration} since the lane branched: merge {integration} into the "
                           "lane's branch first")
    return out


def _git(repo: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout)


def _resolve(repo: Path, rev: str) -> "str | None":
    r = _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def fetch(repo: Path, away: str, integration: str) -> bool:
    return _git(repo, "fetch", "-q", "origin", f"+refs/heads/{away}:refs/remotes/origin/{away}",
                f"+refs/heads/{integration}:refs/remotes/origin/{integration}").returncode == 0


def take_in(repo: Path, away: str, integration: str, sha: str, pr: int, head: str = "") -> "tuple[str, str]":
    """Merge the maintenance merge commit `sha` into `away` on origin: a merge commit with both parents, made with plumbing (the
    checkout's files, index and branches are not touched) and pushed without force, so an away branch that moved in between
    refuses the push. Never a rebase. Without a known `sha`, origin's integration tip is taken in only when it holds the PR's
    `head` (so it holds the merge); a known sha that origin does not serve yet is a failure, never a guess.
    ("merged", sha of the new commit) | ("up to date", target) | ("conflict", why) | ("failed", why)."""
    try:
        if not fetch(repo, away, integration):
            return "failed", f"origin's {away} and {integration} could not be fetched"
        tip = _resolve(repo, f"refs/remotes/origin/{away}")
        main = _resolve(repo, f"refs/remotes/origin/{integration}")
        if not tip or not main:
            return "failed", f"{integration} or {away} cannot be read on origin"
        if re.fullmatch(r"[0-9a-f]{7,40}", sha or ""):
            target = _resolve(repo, sha)
            if not target or _git(repo, "merge-base", "--is-ancestor", target, main).returncode:
                return "failed", f"the merge commit {sha[:12]} is not on origin's {integration} yet; nothing was taken in"
        elif re.fullmatch(r"[0-9a-f]{40}", head or "") and _resolve(repo, head) and _git(
                repo, "merge-base", "--is-ancestor", head, main).returncode == 0:
            target = main
        else:
            return "failed", (f"the merge commit is not known and origin's {integration} does not hold the PR's head yet; "
                              "nothing was taken in")
        if _git(repo, "merge-base", "--is-ancestor", target, tip).returncode == 0:
            return "up to date", target
        tree = _git(repo, "merge-tree", "--write-tree", "--no-messages", tip, target)
        if tree.returncode == 1:
            files = [l for l in tree.stdout.splitlines()[1:] if l.strip()]
            names = sorted({l.split("\t", 1)[-1] for l in files if "\t" in l})
            return "conflict", (f"merging {integration} ({target[:7]}) into {away} conflicts"
                                + (f" in {', '.join(names[:5])}" if names else ""))
        if tree.returncode or not tree.stdout.split():
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
    """Why the away branch has not taken in an earlier maintenance merge yet, or None. Read from git, whatever the period:
    every maintenance merge on the integration branch that names this away branch must be in it (items); and from the
    period record, a merge git may not show yet: its recorded target (the merge commit, else the PR's head) must be in it."""
    if not fetch(repo, away, integration):
        return f"origin's {away} cannot be fetched to see whether the earlier maintenance merges are in it"
    listed = items(repo, integration, away)
    if listed is None:  # git could not be read: never "no earlier merges"
        return f"the maintenance merges on {integration} cannot be read from git, so the away branch's take-in cannot be checked"
    for title, pr, sha, in_away in listed:
        if not in_away:
            return f"{away} has not taken in the maintenance merge of PR #{pr} ({sha}) yet"
    for m in merges:
        if not (isinstance(m, dict) and m.get("kind") == "maintenance" and m.get("taken_in") is not True):
            continue
        target = m.get("target") or m.get("sha") or ""
        full = _resolve(repo, target) if re.fullmatch(r"[0-9a-f]{7,40}", target) else None
        if not full or _git(repo, "merge-base", "--is-ancestor", full, f"refs/remotes/origin/{away}").returncode:
            return (f"{away} has not taken in the maintenance merge of PR #{m.get('pr')} ({target[:7] or 'commit unknown'}) yet"
                    f" ({m.get('take_in') or 'not taken in'})")
    return None


def items(repo: Path, integration: str, away: str) -> "list | None":
    """The maintenance merges of this away branch's periods, oldest first, as (title, PR number or None, short sha, in the away
    branch): the integration branch's first-parent merges whose message carries `Ratchet-maintenance: <away>`, each marked by
    whether origin's away branch holds it (taken in). None when git cannot be read (the caller has fetched)."""
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
        held = _git(repo, "merge-base", "--is-ancestor", parts[0].strip(), f"refs/remotes/origin/{away}").returncode == 0
        out.append((title.strip(), int(m.group(1)) if m else None, parts[0].strip(), held))
    return out
