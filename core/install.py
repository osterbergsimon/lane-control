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


def switch(root: Path, sha: str) -> None:
    """Point `current` at <sha> by renaming a new link over it: a reader sees the old tree or the new one."""
    tmp = root / f".current.{os.getpid()}.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(sha)
    os.replace(tmp, root / "current")


def lay(checkout: Path, sha: str, root: Path) -> str:
    """The tree for <sha> at root/<sha>, from the checkout's git objects: "installed", or "exists" when it was there."""
    tree = root / sha
    if tree_sha(tree) == sha:
        return "exists"
    root.mkdir(parents=True, exist_ok=True)
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
        if tree.exists() and tree_sha(tree) != sha:  # an unfinished tree from an older version: never a running one
            shutil.rmtree(tree)
        try:
            os.rename(tmp, tree)
        except OSError:
            if tree_sha(tree) == sha:  # another install of the same sha won the rename
                return "exists"
            raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return "installed"


def install(checkout: Path, version: str = "HEAD", root: Path = None) -> tuple:
    """Lay <version> of the checkout and switch `current` to it. Returns (state, sha, previous sha)."""
    root = root or data_root()
    checkout = Path(checkout).expanduser().resolve()
    if not git(checkout, "rev-parse", "--git-dir"):
        raise InstallError(f"{checkout} is not a git checkout of lane-control")
    sha = resolve(checkout, version)
    if not sha:
        raise InstallError(f"{version!r} is not a commit in {checkout} (git fetch there first?)")
    if git(checkout, "cat-file", "-e", f"{sha}:.lane-control-root") is None:
        raise InstallError(f"{short(sha)} in {checkout} is not lane-control (no .lane-control-root)")
    before = installed(root)
    state = lay(checkout, sha, root)
    switch(root, sha)
    write_text(root / SOURCE, str(checkout) + "\n")
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


class Use:
    """The tree a run of project-setup or machine-setup uses: `tree` (real folder), `stable` (the path personas and
    links name), `sha` (None when lane-control runs from a plain copy that is neither a checkout nor an install),
    `state` (installed, exists, used or none) and `notes` (plain lines to print)."""

    def __init__(self, tree, stable, sha, state, notes=()):
        self.tree, self.stable, self.sha, self.state, self.notes = Path(tree), Path(stable), sha, state, list(notes)


def use(here: Path, root: Path = None) -> Use:
    """Install (or use) the tree for this lane-control. From a checkout: its HEAD is installed and `current` switched
    to it. From an installed tree: that tree. Otherwise (an old copy): this folder, as before the install."""
    here = Path(here).resolve()
    sha = tree_sha(here)
    if sha:
        cur = here.parent / "current"
        stable = cur if cur.resolve() == here else here
        notes = [] if stable == cur else [f"running {short(sha)}, which is not current ({short(installed(here.parent))})"]
        return Use(here, stable, sha, "used", notes)
    root = root or data_root()
    if is_checkout(here):
        first = installed(root) is None
        state, sha, before = install(here, "HEAD", root)
        notes = []
        gd, common = git(here, "rev-parse", "--path-format=absolute", "--git-dir"), git(here, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if gd and common and Path(gd).resolve() != Path(common).resolve():
            notes.append(f"{here} is a linked worktree (a lane?): current is now its commit; to go back, "
                         f"lane-control install --from {Path(common).resolve().parent}")
        if dirty(here):
            notes.append(f"{here} has uncommitted changes: the install is its last commit {short(sha)}, without them")
        if first and (bin_dir() / "lane-heavy").exists() and not (bin_dir() / "lane-heavy").is_symlink():
            notes.append(f"this machine moves to the install: personas and ~/.agents/bin now point at "
                         f"{root / 'current'}, not at {here}")
        elif before and before != sha:
            notes.append(f"current was {short(before)}, now {short(sha)}")
        return Use(root / sha, root / "current", sha, state, notes)
    return Use(here, here, None, "none", [f"{here} is neither a git checkout nor an install: used as it is"])


def box_script(sha: str) -> str:
    """For a box's shell, its stdin a tar of the tree: lay it at <box root>/<sha> (unless it is there), switch
    `current` to it, print "installed <sha>" or "exists <sha>"."""
    if not SHA_RE.fullmatch(sha):
        raise ValueError(sha)
    return (f'set -e; d={BOX_ROOT}; mkdir -p "$d"; '
            f'if [ "$(cat "$d/{sha}/{MARK}" 2>/dev/null)" = {sha} ]; then cat >/dev/null; s=exists; '
            f'else t="$d/.{sha}.$$.tmp"; rm -rf "$t"; mkdir "$t"; tar -C "$t" -xf -; rm -rf "$d/{sha}"; '
            f'mv "$t" "$d/{sha}"; s=installed; fi; '
            f'ln -sfn {sha} "$d/.current.$$.tmp"; mv -fT "$d/.current.$$.tmp" "$d/current"; echo "$s {sha}"')


def tar_tree(tree: Path) -> list:
    """The command that writes a tree as a tar to stdout, for box_script."""
    return ["tar", "-C", str(tree), "--exclude=__pycache__", "-cf", "-", "."]


# --- doctor and prune (slice 2) -------------------------------------------------------------------------------------

def config_home() -> Path:
    v = os.environ.get("XDG_CONFIG_HOME")
    return Path(v) if v else home() / ".config"


def projects() -> list:
    """(root, sha the project was set up with or None) for each project's settings store on this machine."""
    out = []
    for f in sorted((config_home() / "lane-control" / "projects").glob("*/settings.json")):
        try:
            import json
            st = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(st, dict) and st.get("root"):
            out.append((st["root"], st.get("lane_control")))
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


def box_current(host: str):
    """The sha a box's `current` points at, over ssh (no password prompt), or None when it cannot be read."""
    ssh = ["ssh", "-F", os.path.expanduser(os.environ["LANE_SSH_CONFIG"])] if os.environ.get("LANE_SSH_CONFIG") else ["ssh"]
    try:
        r = subprocess.run([*ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", host,
                            f'readlink {BOX_ROOT}/current'], capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    sha = r.stdout.strip()
    return sha if r.returncode == 0 and SHA_RE.fullmatch(sha) else None


def users(tree: Path) -> list:
    """Pids of this user's processes that use a tree: their cwd, an open file or their command line in it (Linux
    /proc). A tree a running lane reads through `current` is the current one, which prune always keeps."""
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


def prune_plan(root: Path = None) -> list:
    """(sha, "keep"|"remove", why) for each tree in the data root."""
    root = root or data_root()
    cur = installed(root)
    plan = []
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and SHA_RE.fullmatch(p.name)) \
            if root.is_dir() else []:
        if d.name == cur:
            plan.append((d.name, "keep", "current"))
            continue
        pids = users(d)
        plan.append((d.name, "keep", f"in use by pid {', '.join(map(str, pids))}") if pids else
                    (d.name, "remove", "not current, no process uses it"))
    return plan
