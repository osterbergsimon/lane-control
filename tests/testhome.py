"""The first import of every tests/test_*.py (lc-oh7l). AGENTS.md: tests write only to their own temp dirs.

On import, once per run:
- the real lane-control folders (config, data, state and cache), ~/.agents and the top of some package caches are
  listed with their mtimes (the watch);
- HOME and every XDG_*_HOME are pointed at a temp dir, and the variables that name a real file or folder are dropped,
  so a test that sets up nothing of its own still reaches nothing real. A test that sets its own HOME takes all five
  with `env_for(home)`. A test that must have the real home says so with `real_home()`: to read it, or an opt-in live
  test that needs the owner's sign-in or installed browsers. It may write nothing the watch covers.

The watch compares the folders on disk, so it sees a write by any program (sh, node, git), not only by Python.
test_zz_real_home.py, last in `discover` order, fails with every changed path; the same check runs again as the
interpreter ends, for a run of one module and for a write that comes after the last test, and makes the exit code 1.

~/.cache, by what a test or a child it runs could reach:
- lane-control/ (night-precheck, away-watchdog, machine-check, lane-sweep): the whole tree.
- uv/, pypoetry/, pnpm/, yarn/, go-build/ (the package managers in a drafted orca.yaml's setup lines; a real `uv sync`
  is the leak lc-oh7l found) and fish/ (test_box_health runs a real fish): the folder and its own entries, nothing
  deeper (uv's holds about a million files). uv makes and removes a temp folder at the top, so a run shows there.
- Not watched: claude-cli-nodejs/ and opencode/ (only the opt-in live tests start those agents, and the machine's own
  sessions write there all the time), ms-playwright/ (the opt-in real browser test), appimage-run/ (only read).
Package stores outside ~/.cache (~/.npm, ~/.cargo, ~/.bun, ~/go) are not watched.

On a busy machine its own services write some of these folders too, and the watch cannot tell. For a local run,
LANE_TEST_HOME_GUARD=warn prints the changed paths and does not fail. It is ignored when CI is set.
"""
import atexit
import contextlib
import os
import pwd
import shutil
import stat
import sys
import tempfile
from pathlib import Path

ORIGINAL_ENV = dict(os.environ)
XDG = {"XDG_CONFIG_HOME": ".config", "XDG_DATA_HOME": ".local/share", "XDG_STATE_HOME": ".local/state",
       "XDG_CACHE_HOME": ".cache"}
FILE_OVERRIDES = (
    "LANE_AWAY_FILE", "LANE_BUDGET_STOPPED_FILE", "LANE_LIMITS_FILE", "LANE_PRIVATE_NAMES", "LANE_MACHINE_CONF",
    "LANE_IMPROVE_FILE", "LANE_IMPROVE_DRAIN_FILE", "LANE_SSH_CONFIG", "GUARD_STATE_FILE", "NIGHT_START_DIR",
    "PLAYBOOK_CONFIG", "OPENCODE_CONFIG", "CLAUDE_CONFIG_DIR", "ORCA_CLAUDE_CONFIG_DIR", "CLAUDE_PROJECT_DIR")
SHALLOW_CACHES = ("uv", "pypoetry", "pnpm", "yarn", "go-build", "fish")  # why these and no others: above
OPT_OUT = "LANE_TEST_HOME_GUARD"  # =warn: report, do not fail (a local run on a busy machine; never in CI)


def env_for(home) -> dict:
    """HOME and the XDG dirs of one temp home: what a test that sets its own HOME puts in its environment."""
    return {"HOME": str(home), **{k: f"{home}/{sub}" for k, sub in XDG.items()}}


def watch_roots(homes, env) -> dict:
    """{folder no test may change: how many levels down, None for all}: under each home, and under the XDG dirs `env`
    really sets. lane-control's own folder under each XDG dir is watched whole."""
    bases = [(k, Path(home) / sub) for home in homes for k, sub in XDG.items()]
    bases += [(k, Path(env[k])) for k in XDG if os.path.isabs(env.get(k) or "")]
    roots = {Path(home) / ".agents": None for home in homes}
    for key, base in bases:
        roots[base / "lane-control"] = None
        if key == "XDG_CACHE_HOME":
            roots.update({base / name: 1 for name in SHALLOW_CACHES})
    return dict(sorted(roots.items()))


def snapshot(roots: dict) -> dict:
    """{path: (kind and mode, inode, mtime, size or link target)} of each root and what is in it, as deep as the root
    says. Links are not followed."""
    seen = {}
    todo = [(str(root), depth) for root, depth in roots.items()]
    while todo:
        path, depth = todo.pop()
        try:
            s = os.lstat(path)
            if stat.S_ISDIR(s.st_mode):
                if depth != 0:
                    with os.scandir(path) as it:
                        todo.extend((e.path, None if depth is None else depth - 1) for e in it)
                what = 0
            else:
                what = os.readlink(path) if stat.S_ISLNK(s.st_mode) else s.st_size
        except FileNotFoundError:
            continue
        except OSError as e:
            seen[path] = ("unreadable", e.errno)
            continue
        seen[path] = (s.st_mode, s.st_ino, s.st_mtime_ns, what)
    return seen


def differences(before: dict, after: dict) -> list:
    """One line per path that is new, removed or changed. Under a new or removed folder only the folder is named."""
    out = []
    for path in sorted(set(before) | set(after)):
        if before.get(path) == after.get(path):
            continue
        word = "new" if path not in before else "removed" if path not in after else "changed"
        parent = os.path.dirname(path)
        if word != "changed" and ((parent in before) != (parent in after)):
            continue
        out.append(f"{word}: {path}")
    return out


def _real_homes() -> list:
    only = os.environ.pop("LANE_TEST_WATCH_HOMES", None)  # test_testhome's child run: a temp dir stands for the real one
    if only:
        return [Path(only)]
    homes = [os.environ.get("HOME")]
    try:
        homes.append(pwd.getpwuid(os.getuid()).pw_dir)  # where a child with no HOME in its environment ends up
    except KeyError:
        pass
    return [Path(h) for h in dict.fromkeys(homes) if h and os.path.isabs(h)]


_asked = os.environ.pop(OPT_OUT, None)  # popped: a child run is strict unless it is told otherwise
WARN_ONLY = _asked == "warn" and os.environ.get("CI", "").lower() in ("", "0", "false")
REAL_HOMES = _real_homes()
ROOTS = watch_roots(REAL_HOMES, ORIGINAL_ENV)
BEFORE = snapshot(ROOTS)


def check() -> list:
    return differences(BEFORE, snapshot(ROOTS))


def report(changed: list) -> str:
    return "\n".join([
        "the real home changed while the tests ran:",
        *(f"  {line}" for line in changed),
        "This is a test that leaks, or a write by this machine's own running services trips this too: a coordinator's "
        "session hook, an install, the night automations (also `lane-away`, or a test run in a checkout without this "
        "guard). The check cannot tell who wrote: look at each path above and its time.",
        "A leaking test: AGENTS.md, tests write only to their own temp dirs. It must take HOME and XDG_* from "
        "testhome.env_for(<its temp dir>), also in a child's env (an env with no HOME falls back to the real one).",
        f"The machine's own writes: run again, or on a busy machine run with {OPT_OUT}=warn, which prints the paths "
        "and does not fail. It is ignored when CI is set: CI stays strict."])


def warning(changed: list) -> str:
    return f"\nWARNING, not failed ({OPT_OUT}=warn): {report(changed)}"


reported: set = set()


@contextlib.contextmanager
def real_home():
    """The opt-out: HOME and XDG_* as the run was started with. See the module's docstring for who may use it."""
    keys = ("HOME", *XDG, *FILE_OVERRIDES)
    now = {k: os.environ.get(k) for k in keys}

    def put(values):
        for k in keys:
            if values.get(k) is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = values[k]

    put(ORIGINAL_ENV)
    try:
        yield
    finally:
        put(now)


DEFAULT = Path(tempfile.mkdtemp(prefix="lane-tests-home-"))
for _key in FILE_OVERRIDES:
    os.environ.pop(_key, None)
os.environ.update(env_for(DEFAULT / "home"), LANE_STATE_DIR=str(DEFAULT / "slots"))
(DEFAULT / "home").mkdir()
_owner = os.getpid()


def _at_exit():
    if os.getpid() != _owner:  # a fork of the run
        return
    shutil.rmtree(DEFAULT, ignore_errors=True)
    changed = check()
    if not set(changed) - reported:
        return
    if WARN_ONLY:
        print(warning(changed), file=sys.stderr)
        return
    print(f"\nFAILED: {report(changed)}", file=sys.stderr)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1)  # registered first, so it runs after every other exit handler


atexit.register(_at_exit)
