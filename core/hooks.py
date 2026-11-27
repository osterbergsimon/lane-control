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
  - Checked against its pin first: a missing or changed script is `refused` and does not run. It runs from a private
    copy in its run folder: a path hook's bytes that matched its hash (the original path is LANE_HOOK_SOURCE); a git
    hook's pinned commit exported whole from the commit's objects (`git archive`), its script's hash checked there.
  - No arguments, stdin closed, in a fresh empty folder of its own (removed after): never in a lane's worktree or a
    checkout. LANE_HOOK_WORKTREE and LANE_HOOK_PROJECT_ROOT say where those are.
  - Its environment is a short list (PATH, HOME, USER, the locale, XDG folders, TMPDIR, SSH_AUTH_SOCK) and its
    facts, LANE_HOOK_<FACT> (FACTS), each checked: never the caller's other variables, so never a token or key.
  - Its own process group, stopped at its timeout (default 600 s), when it ends, and when the caller is stopped
    (TERM, HUP, INT: guarded()); a child that calls setsid leaves the group.
  - Its exit code and the last 20 lines of its output (stdout and stderr together) are shown, cleaned (clean): a
    best-effort mask of the common secret shapes, not a promise.
A failing gate, lane-start or after-setup fails the command that ran it (the merge is not green, the lane is held,
setup or refresh exits 1 after doing everything else), and says the way past it (way_past: the owner fixes or removes
the hook, or a person skips it once with a reason, recorded); a failing after-merge or on-report only warns.

Orca's own features were checked first (1.4.274): its automations run an agent from a prompt on a schedule or a
GitHub/Slack event, not a script, and cannot hold a merge; `orca.yaml`'s scripts.setup is the project repo's own file,
run inside the lane's worktree; `orca agent hooks` are Orca's agent-status hooks. None runs an owner's pinned script at
these events, so lane-control does.

Read by core/scripts/lane-extras (gate, hook, prepare), core/scripts/project-setup (after-setup), away-merge and
away-watchdog."""
import contextlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unicodedata
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
OUTCOMES = ("succeeded", "failed", "blocked", "partial", "NEEDS_STANDARD", "NEEDS_DEEP", "escalation")
PATH = r"/[^\x00-\x1f\x7f]{0,4000}"
FACTS = {  # fact: (the pattern its value must match, what it is)
    "event": ("|".join(EVENTS), "the event"),
    "project": (r"[^\x00-\x1f\x7f]{1,100}", "the project's name"),
    "project_root": (PATH, "the project's main checkout"),
    "repo": (PATH, "the checkout of the repo the PR is in"),
    "pr": (r"[1-9][0-9]{0,8}", "a pull request number"),
    "head_sha": (r"[0-9a-f]{40}", "a full 40-hex commit"),
    "merge_sha": (r"[0-9a-f]{40}", "a full 40-hex commit"),
    "lane": (WORD, "a lane key (letters, digits, . _ -)"),
    "issue": (WORD, "an issue id (letters, digits, . _ -)"),
    "outcome": ("|".join(OUTCOMES), "a report's status: " + ", ".join(OUTCOMES)),
    "worktree": (PATH, "the lane's worktree"),
    "role": (r"lanes|reviewer", "lanes or reviewer"),
    "setup": (r"setup|refresh", "setup or refresh"),
}
FREE = ("project", "project_root", "repo", "worktree")  # free text: only checked for the real shapes of a secret
# The real shapes of a token or key: a known prefix at a word's start with a long run after it (so `task-tracker`,
# `desk-4ab` or `fix-disk-usage` are names, not keys), a JWT, a private key block, a webhook URL.
SECRET_SHAPE = re.compile(
    r"(?<![A-Za-z0-9])(?:sk-(?:ant-|proj-|live-|test-)?|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|glpat-|xox[abposr]-|"
    r"AKIA|ASIA|AIza|ya29\.)[A-Za-z0-9_-]{16,}"
    r"|(?<![A-Za-z0-9])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"
    r"|-----BEGIN[A-Z ]*PRIVATE KEY-----"
    r"|https://hooks\.slack\.com/services/\S+|https://discord(?:app)?\.com/api/webhooks/\S+")
ESCAPES = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]|\x9b[0-?]*[ -/]*[@-~]")
# a secret-named key or option given a value, in the common forms of output: name=value, name: value, "name": "value",
# --name value. Words that are rarely a secret in test output (PASS, PWD, SESSION, ...) do not count here.
OUTPUT_WORDS = X.SECRET_WORDS - {"PASS", "PWD", "SESSION", "SIG", "CERT", "PAT", "DSN", "PRIVATE", "AUTH", "SALT"}
KEY_VALUE = re.compile(r"""(?P<q>["']?)(?P<k>[A-Za-z_][A-Za-z0-9_.-]{0,63})(?P=q)(?P<sep>\s*[:=]\s*)"""
                       r"""(?P<v>"[^"]*"|'[^']*'|[^\s,;&}\]]+)""")
OPTION_VALUE = re.compile(r"(?P<k>--?[A-Za-z][A-Za-z0-9_-]{0,63})(?P<sep>\s+)(?P<v>[^\s-]\S*)")


class HookError(X.ExtrasError):
    """A fact refused: the message names the fact, never its value."""


def check_facts(facts: dict) -> dict:
    """The facts with their values checked (None and '' dropped); HookError names a bad one without its value. A fact
    with a strict shape (a number, a sha, a word) is checked against it alone; a name or a path only for the real
    shapes of a secret."""
    out = {}
    for k, v in facts.items():
        if v in (None, ""):
            continue
        if k not in FACTS:
            raise HookError(f"no hook fact {k!r}")
        v = str(v)
        pattern, what = FACTS[k]
        if not re.fullmatch(pattern, v) or (k in FREE and (SECRET_SHAPE.search(v) or X.USERINFO.search(v))):
            raise HookError(f"--{k.replace('_', '-')} is not {what} (not shown: it may be a secret)")
        out[k] = v
    return out


def runs_dir() -> Path:
    return X.state_home() / "lane-control" / "hooks" / "runs"


def skips_file() -> Path:
    return X.state_home() / "lane-control" / "hooks" / "skips.jsonl"


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
    """(what to run, None) after the pin check, or (None, why not). Always a private copy in the run folder `d`,
    checked there: a path hook's bytes that matched its hash; a git hook's pinned commit exported whole (`git archive`,
    the commit's own objects: nothing written, ignored or marked unchanged in its checkout counts), its script's hash
    checked in the export."""
    if e.get("url"):
        why = X.hook_problem(e, fetch_it=True)
        if why:
            return None, why
        data = X.git_bytes(X.hook_source_dir(e), "archive", "--format=tar", e["commit"])
        if data is None:
            return None, f"commit {e['commit'][:12]} could not be exported"
        src = d / "src"
        src.mkdir(mode=0o700)
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            tar.extractall(src, filter="data")
        f = src / e["path"]
        if f.is_symlink() or not f.is_file() or X.sha256(f) != e.get("sha256"):
            return None, f"{e['path']} in the export of {e['commit'][:12]} is not the script that was added"
        f.chmod(0o700)
        return f, None
    f = Path(e["script"])
    try:
        if f.is_symlink() or not f.is_file():
            return None, (f"{f} is missing" if not f.exists() else f"{f} is not a plain file") + \
                " (add the hook again: a link is pinned by its target)"
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


def stop_group(pid: int, grace: float = 2.0) -> None:
    """Stop a hook's whole process group: TERM, then KILL whatever is left after `grace` seconds."""
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 0)):
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


RUNNING = set()  # the process groups of the hooks running now


def _on_signal(sig, frame):
    """The caller is stopped (TERM, HUP, INT) while a hook runs: stop the hook's group, flush what was printed, then
    end as that signal would have."""
    for pg in list(RUNNING):
        stop_group(pg, grace=0.5)
    RUNNING.clear()
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.flush()
    if sig == signal.SIGINT:
        raise KeyboardInterrupt
    signal.signal(sig, signal.SIG_DFL)
    os.kill(os.getpid(), sig)


@contextlib.contextmanager
def guarded():
    """While hooks run, a stop of the caller stops them too (only in the main thread, where Python takes signals)."""
    old = {}
    try:
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            old[sig] = signal.signal(sig, _on_signal)
    except ValueError:
        pass
    try:
        yield
    finally:
        for sig, h in old.items():
            signal.signal(sig, h)


def _redact_kv(m: "re.Match") -> str:
    name = m["k"].lstrip("-").replace("-", "_").replace(".", "_").upper()
    words = set(re.split(r"_+", name)) - {""}
    if not (words & OUTPUT_WORDS or name.endswith(("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD"))):
        return m.group(0)
    return m.group(0)[:m.start("v") - m.start(0)] + "[redacted]"


def clean(line: str, keep: tuple = ()) -> str:
    """One line of a hook's output as it is shown: escape sequences, control and format characters (C0, C1, bidi)
    taken out; the real shapes of a token, a password in a URL, an Authorization header, a secret-named key or option
    given a value, and a long opaque word (not a sha lane-control passed in, `keep`) replaced by [redacted]. Best
    effort: a hook must not print secrets."""
    line = ESCAPES.sub("", line.replace("\t", "  "))
    line = "".join(c for c in line if unicodedata.category(c) not in ("Cc", "Cf"))
    line = SECRET_SHAPE.sub("[redacted]", line)
    line = re.sub(X.USERINFO, "://[redacted]@", line)
    line = re.sub(r"(?i)(authorization\s*:\s*|\bbearer\s+)\S.*", r"\1[redacted]", line)
    line = re.sub(r"(?i)\b(basic\s+)[A-Za-z0-9+/]{8,}={0,2}(?![A-Za-z0-9+/=])", r"\1[redacted]", line)
    line = KEY_VALUE.sub(_redact_kv, line)
    line = OPTION_VALUE.sub(_redact_kv, line)
    words = re.split(r"(\s+)", line)
    line = "".join("[redacted]" if w not in keep and w.strip("\"'(),;") not in keep and X.opaque(w) else w
                   for w in words)
    return line[:300]


def tail_of(log: Path, keep: tuple = ()) -> list:
    try:
        with open(log, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 65536))
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    return [clean(x, keep) for x in text.splitlines()[-TAIL:]]


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
        with open(log, "wb") as out, guarded():
            try:
                p = subprocess.Popen([str(exe)], cwd=work, env=env_for(e, facts), stdin=subprocess.DEVNULL,
                                     stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as err:
                res.update(state="failed", why=f"it could not be started ({err.strerror or err})")
                return res
            RUNNING.add(p.pid)  # its own process group: a timeout, or the caller's stop, ends the whole group
            try:
                res["exit"] = p.wait(timeout=res["timeout"])
                res["state"] = "ok" if res["exit"] == 0 else "failed"
            except subprocess.TimeoutExpired:
                stop_group(p.pid)
                p.wait()
                res["state"] = "timeout"
            finally:
                stop_group(p.pid)  # anything it left running in its group
                RUNNING.discard(p.pid)
        res["tail"] = tail_of(log, tuple(v for k, v in facts.items() if k.endswith("_sha")))
        return res
    finally:
        res["seconds"] = round(time.monotonic() - t0, 1)
        shutil.rmtree(d, ignore_errors=True)


def run(entries: list, event: str, facts: dict, skip: "dict | None" = None) -> list:
    """Every hook of `event` among `entries` (X.effective), in order, each with the facts (and the event). `skip`:
    {hook name: reason} a person gave (record_skip): those are said, and not run."""
    hooks = X.hooks_for(entries, event)
    if not hooks:
        return []
    facts = check_facts({**facts, "event": event})
    out = []
    for e in hooks:
        if e["name"] in (skip or {}):
            out.append({"id": X.ident(e), "name": e["name"], "origin": e.get("origin") or "", "event": event,
                        "state": "skipped", "exit": None, "seconds": 0.0, "tail": [], "why": skip[e["name"]],
                        "timeout": 0})
            continue
        out.append(run_one(e, facts))
        for stream in (sys.stdout, sys.stderr):
            with contextlib.suppress(Exception):
                stream.flush()
    return out


def check_skip(entries: list, event: str, names: list, reason: "str | None") -> dict:
    """{name: reason} for --skip-hook: each name must be a hook of that event, and the reason real text."""
    if not names:
        return {}
    reason = " ".join((reason or "").split())
    if len(reason) < 8 or SECRET_SHAPE.search(reason) or X.USERINFO.search(reason):
        raise HookError("--skip-hook needs --reason \"<why>\" (a sentence, recorded; never a secret)")
    have = {e["name"] for e in X.hooks_for(entries, event)}
    for n in names:
        if n not in have:
            raise HookError(f"--skip-hook {n}: no {event} hook of that name ({', '.join(sorted(have)) or 'none'})")
    return {n: reason for n in names}


def record_skip(skip: dict, event: str, facts: dict) -> Path:
    """Each skip, with its reason, the facts and the time, appended to the skips record (0600)."""
    f = skips_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        for name, reason in skip.items():
            fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, "hook": name,
                                 "reason": reason, **{k: v for k, v in facts.items() if v}}) + "\n")
    return f


def failed(results: list) -> list:
    return [r for r in results if r["state"] not in ("ok", "skipped")]


def way_past(results: list, event: str, command: str) -> list:
    """For a blocking event that failed: how the owner gets past a broken hook (never the coordinator's to do)."""
    names = [r["name"] for r in failed(results)]
    if not names or event not in BLOCKING:
        return []
    out = ["the way past a broken hook is the owner's: fix the script and add it again, or remove it ("
           + ", ".join(f"lane-control {'machine' if r['origin'] == 'machine' else 'project'} extras remove "
                       f"hook:{r['name']}" for r in failed(results)) + "; a machine hook is every project's)"]
    if command:
        out.append(f"or, a person at a terminal only, once and recorded: {command} "
                   + " ".join(f"--skip-hook {n}" for n in names) + " --reason \"<why>\"")
    return out


def report(results: list) -> list:
    """The lines that say how each hook went, its last lines under it."""
    out = []
    for r in results:
        where = f"({r['origin']}, {r['event']})" if r["origin"] else f"({r['event']})"
        if r["state"] == "ok":
            head = f"{r['id']} ok: exit 0 in {r['seconds']} s {where}"
        elif r["state"] == "skipped":
            head = f"{r['id']} skipped by a person, not run: {r['why']} (recorded in {skips_file()}) {where}"
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
