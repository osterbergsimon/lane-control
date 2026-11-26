"""The first import of every tests/test_*.py (lc-oh7l). AGENTS.md: tests write only to their own temp dirs.

On import, once per run:
- the real lane-control folders and ~/.agents are listed with their mtimes (the watch);
- HOME and every XDG_*_HOME are pointed at a temp dir, and the variables that name a real file or folder are dropped,
  so a test that sets up nothing of its own still reaches nothing real. A test that sets its own HOME takes all five
  with `env_for(home)`. A test that must read the real home says so with `real_home()`; nothing may write there.

The watch compares the folders on disk, so it sees a write by any program (sh, node, git), not only by Python.
test_zz_real_home.py, last in `discover` order, fails with every changed path; the same check runs again as the
interpreter ends, for a run of one module and for a write that comes after the last test, and makes the exit code 1.
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
# lane-control's own folders under each XDG dir that has them (the cache is not lane-control's alone: not watched)
WATCHED_XDG = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")


def env_for(home) -> dict:
    """HOME and the XDG dirs of one temp home: what a test that sets its own HOME puts in its environment."""
    return {"HOME": str(home), **{k: f"{home}/{sub}" for k, sub in XDG.items()}}


def watch_roots(homes, env) -> list:
    """The folders no test may change: under each home, and under the XDG dirs `env` really sets."""
    roots = set()
    for home in homes:
        roots.add(Path(home) / ".agents")
        roots.update(Path(home) / XDG[k] / "lane-control" for k in WATCHED_XDG)
    roots.update(Path(env[k]) / "lane-control" for k in WATCHED_XDG if os.path.isabs(env.get(k) or ""))
    return sorted(roots)


def snapshot(roots) -> dict:
    """{path: (kind and mode, inode, mtime, size or link target)} of each root and all in it. Links are not followed."""
    seen = {}
    todo = [str(r) for r in roots]
    while todo:
        path = todo.pop()
        try:
            s = os.lstat(path)
            if stat.S_ISDIR(s.st_mode):
                with os.scandir(path) as it:
                    todo.extend(e.path for e in it)
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


REAL_HOMES = _real_homes()
ROOTS = watch_roots(REAL_HOMES, ORIGINAL_ENV)
BEFORE = snapshot(ROOTS)


def check() -> list:
    return differences(BEFORE, snapshot(ROOTS))


def report(changed: list) -> str:
    return ("the real home changed while the tests ran. AGENTS.md: tests write only to their own temp dirs.\n"
            + "\n".join(f"  {line}" for line in changed)
            + "\nFind the test: it must take HOME and XDG_* from testhome.env_for(<its temp dir>), also in a child's "
              "env (an env with no HOME falls back to the real one). The watch cannot tell who wrote: `lane-away`, a "
              "coordinator starting a session, an install, or a test run in a checkout without this guard on this "
              "machine during the run shows here too; check the paths and their times, and run again.")


reported: set = set()


@contextlib.contextmanager
def real_home():
    """The opt-out, to READ the real home (the private-names check): HOME and XDG_* as the run was started with."""
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
    print(f"\nFAILED: {report(changed)}", file=sys.stderr)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1)  # registered first, so it runs after every other exit handler


atexit.register(_at_exit)
