"""One versioned lane-control tree per machine and per box (INST-1).

A tree is the whole repo at one commit (git archive of that commit, no network), laid at
${XDG_DATA_HOME:-~/.local/share}/lane-control/<sha>/ with a `current` link next to it, switched by rename (atomic).
Old trees are kept: a running lane may still read one. Personas name the stable path .../lane-control/current, and
~/.agents/bin holds links into it, so nothing points at a checkout or a lane worktree that may go away.

Used by core/scripts/lane-control (install, status, doctor, prune), project-setup and machine-setup."""
import os
import re
import shutil
import subprocess
from pathlib import Path

MARK = ".lane-control-sha"  # written last into a tree: the sha it holds; a tree without it is not complete
SOURCE = "source"  # in the data root: the checkout the last install came from (lane-control install with no --from)
BIN_TOOLS = ("lane-limits.sh", "lane-heavy", "lane-room", "lane-tier", "design-shots", "lane-control")
SHA_RE = re.compile(r"[0-9a-f]{40}")
BOX_ROOT = '"${XDG_DATA_HOME:-$HOME/.local/share}/lane-control"'  # the same place on a box, for its shell


def home() -> Path:
    return Path(os.environ.get("HOME") or str(Path.home()))


def data_root() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or home() / ".local" / "share") / "lane-control"


def bin_dir() -> Path:
    return home() / ".agents" / "bin"


def short(sha) -> str:
    return sha[:12] if sha else "none"


def tree_sha(tree: Path):
    """The sha a complete tree holds (its MARK file, which must match its folder name), else None."""
    try:
        sha = (tree / MARK).read_text().strip()
    except OSError:
        return None
    return sha if SHA_RE.fullmatch(sha) and tree.name == sha else None


def installed(root: Path = None):
    """The sha `current` points at (a complete tree), else None."""
    root = root or data_root()
    try:
        target = os.readlink(root / "current")
    except OSError:
        return None
    return tree_sha(root / target)


def git(checkout: Path, *args):
    try:
        r = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def is_checkout(path: Path) -> bool:
    top = git(path, "rev-parse", "--show-toplevel")
    return bool(top) and Path(top).resolve() == Path(path).resolve() and (Path(path) / ".lane-control-root").exists()


def resolve(checkout: Path, version: str = "HEAD"):
    sha = git(checkout, "rev-parse", "--verify", "--quiet", f"{version}^{{commit}}")
    return sha if sha and SHA_RE.fullmatch(sha) else None


def dirty(checkout: Path) -> bool:
    """Tracked changes not in HEAD (what an install of HEAD leaves out)."""
    out = git(checkout, "status", "--porcelain", "--untracked-files=no")
    return bool(out)


class InstallError(Exception):
    pass


def linked_worktree(checkout: Path):
    """The main checkout's path when `checkout` is a linked worktree (a lane), else None."""
    gd = git(checkout, "rev-parse", "--path-format=absolute", "--git-dir")
    common = git(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if gd and common and Path(gd).resolve() != Path(common).resolve():
        return Path(common).resolve().parent
    return None


def default_branch(checkout: Path) -> str:
    ref = git(checkout, "rev-parse", "--abbrev-ref", "origin/HEAD")
    if ref and ref.startswith("origin/") and ref != "origin/HEAD":
        return ref.split("/", 1)[1]
    return "main" if git(checkout, "rev-parse", "--verify", "--quiet", "refs/heads/main") else "master"


def switch_refusal(checkout: Path, sha: str, cur):
    """Why a setup run from this checkout must not move the machine's `current` to <sha> by itself, else None: only the
    main checkout on its default branch, at a commit that is not older than the installed one, may (INST-1 review M5).
    `lane-control install` always may: that is the owner asking."""
    main = linked_worktree(checkout)
    if main:
        return f"{checkout} is a linked worktree (a lane), not the main checkout {main}"
    branch, want = git(checkout, "symbolic-ref", "--short", "-q", "HEAD"), default_branch(checkout)
    if branch != want:
        return f"{checkout} is on {branch or 'a detached HEAD'}, not its default branch {want}"
    if cur and git(checkout, "merge-base", "--is-ancestor", sha, cur) is not None:
        return f"its commit {short(sha)} is older than the installed {short(cur)}"
    return None


def lock(root: Path):
    """An exclusive lock on the data root for one install (two at once could interleave their renames)."""
    import fcntl
    root.mkdir(parents=True, exist_ok=True)
    f = open(root / ".lock", "w")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def switch(root: Path, sha: str) -> None:
    """Point `current` at <sha> by renaming a new link over it: a reader sees the old tree or the new one."""
    tmp = root / f".current.{os.getpid()}.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(sha)
    os.replace(tmp, root / "current")


def lay(checkout: Path, sha: str, root: Path) -> str:
    """The tree for <sha> at root/<sha>, from the checkout's git objects: "installed", or "exists" when it was there.
    The mark is written last, after tar said the whole archive was laid; the tree is renamed into place only then."""
    tree = root / sha
    if tree_sha(tree) == sha:
        return "exists"
    tmp = root / f".{sha}.{os.getpid()}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        pack = subprocess.Popen(["git", "-C", str(checkout), "archive", "--format=tar", sha], stdout=subprocess.PIPE)
        unpack = subprocess.run(["tar", "-x", "-C", str(tmp)], stdin=pack.stdout, capture_output=True, text=True)
        pack.stdout.close()
        if pack.wait() != 0 or unpack.returncode != 0:
            raise InstallError(f"git archive {short(sha)} from {checkout} failed: {unpack.stderr.strip()}".rstrip(": "))
        (tmp / MARK).write_text(sha + "\n")
        if tree.exists() and tree_sha(tree) != sha:  # an unfinished tree (only under the lock, so never a used one)
            shutil.rmtree(tree)
        os.rename(tmp, tree)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return "installed"


def install(checkout: Path, version: str = "HEAD", root: Path = None) -> tuple:
    """Lay <version> of the checkout and switch `current` to it. Returns (state, sha, previous sha). Any failure is an
    InstallError, and `current` is then as it was. The checkout is remembered (lane-control install with no --from)
    only when it is a main checkout, never a lane worktree that goes away."""
    root = root or data_root()
    checkout = Path(checkout).expanduser().resolve()
    if not git(checkout, "rev-parse", "--git-dir"):
        raise InstallError(f"{checkout} is not a git checkout of lane-control")
    sha = resolve(checkout, version)
    if not sha:
        raise InstallError(f"{version!r} is not a commit in {checkout} (git fetch there first?)")
    for need in (".lane-control-root", "core/install.py"):
        if git(checkout, "cat-file", "-e", f"{sha}:{need}") is None:
            raise InstallError(f"{short(sha)} in {checkout} has no {need}: not a lane-control that can be installed "
                               "(older than the install itself?)")
    try:
        with lock(root):
            before = installed(root)
            state = lay(checkout, sha, root)
            if not linked_worktree(checkout):
                write_text(root / SOURCE, str(checkout) + "\n")
            switch(root, sha)
    except OSError as e:
        raise InstallError(f"installing {short(sha)} in {root} failed: {e.strerror or e}") from None
    return state, sha, before


def write_text(path: Path, text: str) -> None:
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    tmp.write_text(text)
    os.replace(tmp, path)


def source(root: Path = None):
    try:
        p = Path((root or data_root()) / SOURCE).read_text().strip()
    except OSError:
        return None
    return Path(p) if p else None


def link(dst: Path, target: Path) -> str:
    """dst as a link to target, replaced by rename (a running bash script keeps the file it opened):
    "exists", "created", or "updated" (it was another link, or a copy from before the install)."""
    if dst.is_symlink() and os.readlink(dst) == str(target):
        return "exists"
    state = "updated" if (dst.is_symlink() or dst.exists()) else "created"
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.parent / f".{dst.name}.{os.getpid()}.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(target)
    os.replace(tmp, dst)
    return state


def link_tools(stable: Path, names=BIN_TOOLS) -> dict:
    """~/.agents/bin/<tool> as links into the tree at `stable` (normally .../lane-control/current)."""
    return {n: link(bin_dir() / n, stable / "core" / "scripts" / n) for n in names
            if (stable / "core" / "scripts" / n).exists()}


def old_copies() -> bool:
    """A machine set up before the install: lane tools in ~/.agents/bin that are files, not links."""
    return any((bin_dir() / n).exists() and not (bin_dir() / n).is_symlink() for n in BIN_TOOLS)


MOVES = "this machine moves to the install: personas, hooks and ~/.agents/bin now point at {current}, not at {here}"


class Use:
    """The tree a run of project-setup or machine-setup uses: `tree` (real folder), `stable` (the path personas, hooks
    and links name), `sha` (None when lane-control runs from a plain copy that is neither a checkout nor an install),
    `state` (installed, exists, kept, used or none), `dirty` (rendered text may differ from the tree) and `notes`."""

    def __init__(self, tree, stable, sha, state, notes=(), dirty=False):
        self.tree, self.stable, self.sha, self.state = Path(tree), Path(stable), sha, state
        self.notes, self.dirty = list(notes), dirty


def use(here: Path, root: Path = None, ask=None) -> Use:
    """Install (or use) the tree for this lane-control.
    - From an installed tree: that tree.
    - From a checkout: its HEAD is installed and `current` switched to it when switch_refusal() allows it, or when
      LANE_CONTROL_SWITCH=1, or when ask(question) says yes (a terminal). Otherwise the installed `current` is kept
      and used, with a note saying how to switch; with nothing installed yet that is an InstallError.
    - A plain copy (neither): this folder, as before the install."""
    here = Path(here).resolve()
    sha = tree_sha(here)
    if sha:
        cur = here.parent / "current"
        stable = cur if cur.resolve() == here else here
        notes = [] if stable == cur else [f"running {short(sha)}, which is not current ({short(installed(here.parent))})"]
        return Use(here, stable, sha, "used", notes)
    root = root or data_root()
    if not is_checkout(here):
        return Use(here, here, None, "none", [f"{here} is neither a git checkout nor an install: used as it is"])
    head, cur = resolve(here), installed(root)
    if not head:
        raise InstallError(f"{here} has no commit to install")
    dirty_note = ([f"{here} has uncommitted changes: the install is its last commit {short(head)}, without them"]
                  if dirty(here) else [])
    if head == cur:
        return Use(root / cur, root / "current", cur, "exists", dirty_note, dirty=bool(dirty_note))
    why = switch_refusal(here, head, cur)
    if why and os.environ.get("LANE_CONTROL_SWITCH") != "1" and not (
            ask and ask(f"{why}. Switch this machine's lane-control from {short(cur)} to {short(head)}? [y/N] ")):
        if not cur:
            raise InstallError(f"{why}, and no lane-control is installed yet: run scripts/lane-control install in the "
                               "main checkout first (or lane-control install --from this checkout to use this one)")
        # Rendered text from this checkout would not be the kept tree's: personas render from a differing checkout.
        return Use(root / cur, root / "current", cur, "kept",
                   [f"{why}: kept the installed {short(cur)} (to switch: lane-control install --from {here})"],
                   dirty=True)
    moved = cur is None and old_copies()
    state, sha, before = install(here, "HEAD", root)
    notes = list(dirty_note)
    if moved:
        notes.append(MOVES.format(current=root / "current", here=here))
    elif before and before != sha:
        notes.append(f"current was {short(before)}, now {short(sha)}")
    return Use(root / sha, root / "current", sha, state, notes, dirty=bool(dirty_note))


# --- a box ------------------------------------------------------------------------------------------------------------

def sh(script: str) -> str:
    """A command line any login shell runs (fish and csh too): `sh -c '<script>'`. Single quotes are the one quoting
    fish and sh agree on, as long as there is no backslash in it."""
    import shlex
    if "\\" in script:
        raise ValueError("a box command cannot hold a backslash (fish reads it inside single quotes)")
    return "sh -c " + shlex.quote(script)


def box_run(rel: str, args: str = "") -> str:
    """Run <rel> of the box's lane-control: its installed `current`, else a copy from before the install (the flat
    ~/.local/share/lane-control/<rel>), so --remove-host and --show still work on a box not refreshed yet."""
    return sh(f'd={BOX_ROOT}; p="$d/current/{rel}"; [ -e "$p" ] || p="$d/{rel}"; exec "$p" {args}'.rstrip())


def tree_count(tree: Path) -> int:
    """The entries `find <tree>` lists on the box after tar_tree's archive is laid there: the folder itself, every
    folder, file and link below it, without __pycache__ and the mark."""
    n = 1
    for top, dirs, files in os.walk(tree):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        n += len(dirs) + len([f for f in files if not (top == str(tree) and f == MARK)])
    return n


def box_script(sha: str, count: int) -> str:
    """For a box, its stdin a tar of the tree (tar_tree): lay it at <box root>/<sha> unless a complete tree is there,
    check that all <count> entries arrived, only then write the mark, rename it into place and switch `current`; print
    "installed <sha>" or "exists <sha>". A cut or short stream leaves no tree and switches nothing (exit 3), so the
    next send lays it again. Under a lock when the box has flock."""
    if not SHA_RE.fullmatch(sha):
        raise ValueError(sha)
    return sh(f'set -e; d={BOX_ROOT}; mkdir -p "$d"; '
              f'if command -v flock >/dev/null 2>&1; then exec 9>"$d/.lock"; flock 9; fi; '
              f'if [ "$(cat "$d/{sha}/{MARK}" 2>/dev/null)" = {sha} ]; then cat >/dev/null; s=exists; '
              f'else t="$d/.{sha}.$$.tmp"; rm -rf "$t"; mkdir "$t"; '
              f'if ! tar -C "$t" -xf -; then rm -rf "$t"; echo "lane-control: the tree did not arrive whole" >&2; exit 3; fi; '
              f'n=$(find "$t" | wc -l); '
              f'if [ "$n" -ne {count} ]; then rm -rf "$t"; echo "lane-control: $n of {count} entries arrived; nothing '
              f'was switched" >&2; exit 3; fi; '
              f'echo {sha} > "$t/{MARK}"; rm -rf "$d/{sha}"; mv -T "$t" "$d/{sha}"; s=installed; fi; '
              f'ln -sfn {sha} "$d/.current.$$.tmp"; mv -fT "$d/.current.$$.tmp" "$d/current"; echo "$s {sha}"')


def tar_tree(tree: Path) -> list:
    """The command that writes a tree as a tar to stdout, for box_script: without __pycache__ and without the mark,
    which the box writes itself once the whole tree is there."""
    return ["tar", "-C", str(tree), "--exclude=__pycache__", f"--exclude=./{MARK}", "-cf", "-", "."]


# --- doctor and prune (slice 2) -------------------------------------------------------------------------------------

def config_home() -> Path:
    v = os.environ.get("XDG_CONFIG_HOME")
    return Path(v) if v else home() / ".config"


def projects(with_dirty: bool = False) -> list:
    """(root, sha the project was set up with or None[, rendered from other text]) for each project's settings store
    on this machine."""
    out = []
    for f in sorted((config_home() / "lane-control" / "projects").glob("*/settings.json")):
        try:
            import json
            st = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(st, dict) and st.get("root"):
            out.append((st["root"], st.get("lane_control"), bool(st.get("lane_control_dirty")))[:3 if with_dirty else 2])
    return out


def boxes() -> list:
    """(host, sha last sent) for each box project-setup sent a tree to."""
    import json
    out = []
    for f in sorted((config_home() / "lane-control" / "boxes").glob("*.json")):
        try:
            b = json.loads(f.read_text())
            out.append((b["host"], b.get("lane_control")))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


def box_ssh(host: str, cmd: str, timeout: int = 60):
    ssh = ["ssh", "-F", os.path.expanduser(os.environ["LANE_SSH_CONFIG"])] if os.environ.get("LANE_SSH_CONFIG") else ["ssh"]
    try:
        return subprocess.run([*ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", host, cmd],
                              capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None


def box_current(host: str):
    """The sha a box's `current` points at, over ssh (no password prompt), or None when it cannot be read."""
    r = box_ssh(host, sh(f"readlink {BOX_ROOT}/current"), 30)
    sha = r.stdout.strip() if r else ""
    return sha if r and r.returncode == 0 and SHA_RE.fullmatch(sha) else None


def box_doctor(host: str):
    """The box's own `lane-control doctor --json --no-ssh` (its projects, links and skills), or None when it has none
    (a box set up before the install) or cannot be reached."""
    import json
    r = box_ssh(host, box_run("core/scripts/lane-control", "doctor --json --no-ssh"), 60)
    try:
        d = json.loads(r.stdout) if r else None
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def users(tree: Path) -> list:
    """Pids of this user's processes that use a tree: their cwd, an open file or their command line in it (Linux
    /proc)."""
    t = str(tree.resolve())
    found = []
    for d in Path("/proc").glob("[0-9]*"):
        try:
            if d.stat().st_uid != os.getuid():
                continue
            hit = os.readlink(d / "cwd").startswith(t)
            if not hit:
                hit = t in (d / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            if not hit:
                for fd in (d / "fd").iterdir():
                    try:
                        if os.readlink(fd).startswith(t):
                            hit = True
                            break
                    except OSError:
                        continue
        except OSError:
            continue
        if hit and d.name != str(os.getpid()):
            found.append(int(d.name))
    return found


def naming_files() -> list:
    """Files that may name a tree by its own path (not through `current`): each recorded project's hook settings,
    rendered personas and OpenCode plugins, the user's systemd units, ~/.agents/bin and the skills (INST-1 review B1:
    a hook runs only at start and compaction, so no process holds its tree in between)."""
    out = []
    for root, _ in projects():
        r = Path(root)
        out += [r / ".claude" / "settings.local.json", *(r / ".lane-control").glob("*.md"),
                *(r / ".opencode" / "plugins").glob("*")]
    out += [*(config_home() / "systemd" / "user").glob("*"), *(config_home() / "opencode" / "plugins").glob("*")]
    out += [*bin_dir().glob("*"), *(home() / ".agents" / "skills").glob("*/*")]
    return out


def named_by(tree: Path, files: list):
    t = str(tree)
    for f in files:
        try:
            if f.is_symlink() and os.readlink(f).startswith(t):
                return f
            if f.is_file() and f.stat().st_size < 4_000_000 and t in f.read_text(errors="replace"):
                return f
        except OSError:
            continue
    return None


def prune_plan(root: Path = None) -> list:
    """(name, "keep"|"remove", why) for each tree in the data root, and for leftovers of an install that was stopped.
    Kept: current, a tree a running process uses, a tree any hook, persona, unit or link names. With no valid
    `current` nothing is removed."""
    import time
    root = root or data_root()
    cur = installed(root)
    if not root.is_dir():
        return []
    if not cur:
        return [(p.name, "keep", "no valid current: prune removes nothing until lane-control install has run")
                for p in sorted(root.iterdir()) if p.is_dir() and not p.is_symlink() and SHA_RE.fullmatch(p.name)]
    files = naming_files()
    plan = []
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()):
        if d.name.endswith(".tmp") and d.name.startswith("."):
            old = time.time() - d.stat().st_mtime > 3600
            plan.append((d.name, "remove" if old else "keep",
                         "left by an install that stopped" if old else "an install may be running"))
            continue
        if not SHA_RE.fullmatch(d.name):
            continue
        if d.name == cur:
            plan.append((d.name, "keep", "current"))
            continue
        pids = users(d)
        f = None if pids else named_by(d, files)
        plan.append((d.name, "keep", f"in use by pid {', '.join(map(str, pids))}") if pids else
                    (d.name, "keep", f"named by {f}") if f else
                    (d.name, "remove", "not current, no process uses it, nothing names it"))
    return plan
