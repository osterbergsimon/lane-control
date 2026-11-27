"""Lifecycle hooks: the owner's scripts run by lane-control at five events (lc-76o, EXT-2). Owner 2026-11-26: "Hooks
pre/post/into our scripts?", then "Sounds good" to:

  after-setup  at the end of `lane-control project setup` or `refresh` (project-setup)
  lane-start   when a lane's worktree is ready, before its agent starts (`lane-control lane prepare --role lanes`)
  gate         extra checks the coordinator must see green before a merge (`lane-control project gate`; away-merge)
  after-merge  after the coordinator's merge (`lane-control project hook ... after-merge`; away-merge)
  on-report    when the coordinator takes a lane's report (`hook ... on-report`), and the away watchdog's escalation

A hook is an extra of kind `hook` (core/extras.py make_hook): `lane-control project|machine extras add --hook <event>
--script <absolute path | git-url@commit:path> [--timeout S]`, the machine's for every project, a project's after them.
Never from a project repo: the owner registers each one, and a script inside a project checkout is refused (unless
--allow-repo-path). Each is pinned: a path by its hash, a git one by its commit and its hash.

How one runs (run_one):
  - Checked against its pin first: a missing or changed script is `refused` and does not run. A path hook runs as a
    private copy of the bytes just checked, so a write to the file after the check cannot change what runs (the
    original path is LANE_HOOK_SOURCE); a git hook runs in its pinned checkout, checked clean just before.
  - No arguments, stdin closed, in a fresh empty folder of its own (removed after): never in a lane's worktree or a
    checkout. LANE_HOOK_WORKTREE and LANE_HOOK_PROJECT_ROOT say where those are.
  - Its environment is a short list (PATH, HOME, USER, the locale, XDG folders, TMPDIR, SSH_AUTH_SOCK) and its
    facts, LANE_HOOK_<FACT> (FACTS), each checked: never the caller's other variables, so never a token or key.
  - A timeout (its own, default 600 s): then its whole process group is stopped; so is anything it left running.
  - Its exit code and the last 20 lines of its output (stdout and stderr together) are shown, with control
    characters taken out and anything shaped like a token, key or password replaced by [redacted].
A failing gate, lane-start or after-setup fails the command that ran it (the merge is not green, the lane is held,
setup or refresh exits 1 after doing everything else); a failing after-merge or on-report only warns.

Orca's own features were checked first (1.4.274): its automations run an agent from a prompt on a schedule or a
GitHub/Slack event, not a script, and cannot hold a merge; `orca.yaml`'s scripts.setup is the project repo's own file,
run inside the lane's worktree; `orca agent hooks` are Orca's agent-status hooks. None runs an owner's pinned script at
these events, so lane-control does.

Read by core/scripts/lane-extras (gate, hook, prepare), core/scripts/project-setup (after-setup), away-merge and
away-watchdog."""
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extras as X  # noqa: E402

EVENTS = X.EVENTS
BLOCKING = ("after-setup", "lane-start", "gate")  # a failure fails the command; after-merge and on-report warn
TAIL = 20
PASS_ENV = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "TZ",
            "TMPDIR", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
            "SSH_AUTH_SOCK")
WORD = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
FACTS = {  # fact: (the pattern its value must match, what it is)
    "event": ("|".join(EVENTS), "the event"),
    "project": (r"[^\x00-\x1f\x7f]{1,100}", "the project's name"),
    "project_root": (r"/[^\x00-\x1f\x7f]{0,4000}", "the project's main checkout"),
    "pr": (r"[1-9][0-9]{0,8}", "a pull request number"),
    "head_sha": (r"[0-9a-f]{40}", "a full 40-hex commit"),
    "merge_sha": (r"[0-9a-f]{40}", "a full 40-hex commit"),
    "lane": (WORD, "a lane key (letters, digits, . _ -)"),
    "issue": (WORD, "an issue id (letters, digits, . _ -)"),
    "outcome": (r"[A-Za-z][A-Za-z0-9_-]{0,31}", "one word"),
    "worktree": (r"/[^\x00-\x1f\x7f]{0,4000}", "the lane's worktree"),
    "role": (r"lanes|reviewer", "lanes or reviewer"),
    "setup": (r"setup|refresh", "setup or refresh"),
}
CONTROL = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]|[\x00-\x08\x0b-\x1f\x7f]")
SECRETISH = re.compile(r"(?:" + X.TOKEN_VALUE.pattern + r")\S*|" + X.USERINFO.pattern + r"|" + X.AUTH_TEXT.pattern +
                       r".*", re.I)


class HookError(X.ExtrasError):
    """A fact refused: the message names the fact, never its value."""


def check_facts(facts: dict) -> dict:
    """The facts with their values checked (None and '' dropped); HookError names a bad one without its value."""
    out = {}
    for k, v in facts.items():
        if v in (None, ""):
            continue
        if k not in FACTS:
            raise HookError(f"no hook fact {k!r}")
        v = str(v)
        pattern, what = FACTS[k]
        if not re.fullmatch(pattern, v) or X.TOKEN_VALUE.search(v) or X.USERINFO.search(v):
            raise HookError(f"--{k.replace('_', '-')} is not {what} (not shown: it may be a secret)")
        out[k] = v
    return out


def runs_dir() -> Path:
    return X.state_home() / "lane-control" / "hooks" / "runs"


def env_for(e: dict, facts: dict) -> dict:
    env = {k: os.environ[k] for k in PASS_ENV if os.environ.get(k)}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.setdefault("HOME", str(X.home()))
    env["LANE_HOOK_NAME"] = e["name"]
    env["LANE_HOOK_SOURCE"] = e["script"] if e.get("script") else f"{e['url']}@{e['commit']}:{e['path']}"
    for k, v in facts.items():
        env[f"LANE_HOOK_{k.upper()}"] = v
    return env


def prepared(e: dict, d: Path) -> tuple:
    """(what to run, None) after the pin check, or (None, why not). A path hook: a private copy of the bytes that
    matched its hash; a git hook: its file in its pinned checkout (fetched when not there)."""
    if e.get("url"):
        why = X.hook_problem(e, fetch_it=True)
        return (None, why) if why else (X.hook_file(e), None)
    f = Path(e["script"])
    try:
        if f.is_symlink() or not f.is_file():
            return None, f"{f} is missing" if not f.exists() else f"{f} is not a plain file"
        data = f.read_bytes()
    except OSError as err:
        return None, f"{f} cannot be read ({err.strerror or err})"
    if X.digest(data) != e.get("sha256"):
        return None, f"{f} changed since it was added (add it again to accept it)"
    if not os.access(f, os.X_OK):
        return None, f"{f} is not executable any more"
    copy = d / "bin" / f.name
    copy.parent.mkdir(mode=0o700)
    fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return copy, None


def stop_group(pid: int) -> None:
    """Stop a hook's whole process group: TERM, then KILL whatever is left after 2 s."""
    for sig, wait in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 0)):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        end = time.monotonic() + wait
        while time.monotonic() < end:
            try:
                os.killpg(pid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.05)


def clean(line: str) -> str:
    line = CONTROL.sub("", line.replace("\t", "  "))
    line = SECRETISH.sub("[redacted]", line)
    words = re.split(r"(\s+)", line)
    line = "".join("[redacted]" if X.opaque(w) else w for w in words)
    return line[:300]


def tail_of(log: Path) -> list:
    try:
        with open(log, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 65536))
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    return [clean(x) for x in text.splitlines()[-TAIL:]]


def run_one(e: dict, facts: dict) -> dict:
    """Run one hook: {id, name, origin, event, state (ok | failed | timeout | refused), exit, seconds, tail, why}."""
    res = {"id": X.ident(e), "name": e["name"], "origin": e.get("origin") or "", "event": e.get("event"),
           "state": "refused", "exit": None, "seconds": 0.0, "tail": [], "why": "",
           "timeout": int(e.get("timeout") or X.HOOK_TIMEOUT)}
    base = runs_dir()
    base.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix=f"{e['name']}.", dir=base))
    t0 = time.monotonic()
    try:
        exe, why = prepared(e, d)
        if why:
            res["why"] = why
            return res
        work = d / "work"
        work.mkdir(mode=0o700)
        log = d / "output"
        with open(log, "wb") as out:
            try:
                p = subprocess.Popen([str(exe)], cwd=work, env=env_for(e, facts), stdin=subprocess.DEVNULL,
                                     stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as err:
                res.update(state="failed", why=f"it could not be started ({err.strerror or err})")
                return res
            try:
                res["exit"] = p.wait(timeout=res["timeout"])
                res["state"] = "ok" if res["exit"] == 0 else "failed"
            except subprocess.TimeoutExpired:
                stop_group(p.pid)
                p.wait()
                res["state"] = "timeout"
            finally:
                stop_group(p.pid)  # anything it left running in its group
        res["tail"] = tail_of(log)
        return res
    finally:
        res["seconds"] = round(time.monotonic() - t0, 1)
        shutil.rmtree(d, ignore_errors=True)


def run(entries: list, event: str, facts: dict) -> list:
    """Every hook of `event` among `entries` (X.effective), in order, each with the facts (and the event)."""
    hooks = X.hooks_for(entries, event)
    if not hooks:
        return []
    facts = check_facts({**facts, "event": event})
    return [run_one(e, facts) for e in hooks]


def failed(results: list) -> list:
    return [r for r in results if r["state"] != "ok"]


def report(results: list) -> list:
    """The lines that say how each hook went, its last lines under it."""
    out = []
    for r in results:
        where = f"({r['origin']}, {r['event']})" if r["origin"] else f"({r['event']})"
        if r["state"] == "ok":
            head = f"{r['id']} ok: exit 0 in {r['seconds']} s {where}"
        elif r["state"] == "failed":
            head = f"{r['id']} failed: " + (f"exit {r['exit']}" if r["exit"] is not None else r["why"]) + \
                f" in {r['seconds']} s {where}"
        elif r["state"] == "timeout":
            head = f"{r['id']} timed out after {r['timeout']} s: stopped, with everything it started {where}"
        else:
            head = f"{r['id']} refused: {r['why']}; it did not run {where}"
        out.append(head)
        if r["tail"]:
            out.append(f"  last {len(r['tail'])} line(s) of its output:")
            out += [f"  | {x}" for x in r["tail"]]
    return out
