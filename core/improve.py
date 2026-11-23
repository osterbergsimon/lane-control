"""Away-improve mode state (design: docs/superpowers/specs/2026-11-23-away-improve-design.md).

The state is one file, ~/.config/lane-control/improve.json (LANE_IMPROVE_FILE), written only by
`lane-improve on` (this module's `on`), which is the only thing that turns the mode on: `lane-away on`, being away,
night and a changed budget never do, and the mode never changes presence. Fields:

  started    ISO time the mode was switched on
  end        ISO time, or "until-back" (no end; `lane-improve off` ends it)
  limits     the owner's limits, free text, binding
  deny_paths globs a merge must not touch (away-merge checks the PR diff)
  projects   list of coordinator names, or "all"
  unmetered  true on a machine with no usage limits: no budget steps for improve work
  branches   {project: "away/<YYYY-MM-DD>"}
  integration {project: "main" | "develop" | ...}: the branch the project's PRs go to, worked out once, at `on`
  prs        {project: number}: the draft summary PR (away/<date> -> the integration branch), opened at `on` or, when gh
             could not then, by away-merge after the first merge; its body is the AWAY-LOG.md summary, refreshed by
             away-merge after every merge. Optional (a file without it is fine); never merged by an agent.

Every reader treats a missing, malformed or ended file as improve off; a malformed one says why on one stderr line.
lane-limits.sh has its own copy of this validation (a lone copy of that script cannot import this module):
tests/test_lane_away.py ReaderAgreementTests runs both over the same files, so keep them in step.

Ending the mode (`off`, or the end time) leaves a drain state: the running lane's PR can still merge through
away-merge, no new cycle starts, and each coordinator then opens its draft PR and runs `lane-improve drained <project>`.
After `off` the drain state is improve-drain.json (the same fields, plus `ended` and `pending`); after an end time it is
the old improve.json, which `drained` turns into improve-drain.json. The deny rules stay until `drained`.
Command line (lane-improve calls it): `improve.py on ...`, `off`, `drained`, `status`.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

UNTIL_BACK = "until-back"
DRAIN_NOTICE_HOURS = 12   # `status` says so when a drain has waited this long
REST_HOURS = 12           # `lane-improve rest` quiets a project's nudges this long, then one nudge looks for new work
FIELDS = ("started", "end", "limits", "deny_paths", "projects", "unmetered", "branches", "integration")


def path() -> Path:
    v = os.environ.get("LANE_IMPROVE_FILE")
    return Path(v) if v else Path.home() / ".config" / "lane-control" / "improve.json"


def now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def drain_path() -> Path:
    v = os.environ.get("LANE_IMPROVE_DRAIN_FILE")
    return Path(v) if v else path().with_name("improve-drain.json")


def _time_ok(v) -> bool:
    try:
        dt.datetime.fromisoformat(v)
        return True
    except (TypeError, ValueError):
        return False


def _problem(state, drain: bool = False) -> "str | None":
    """Why a parsed state is not usable, or None."""
    if not isinstance(state, dict):
        return "not a JSON object"
    missing = [k for k in FIELDS if k not in state]
    if missing:
        return "missing " + ", ".join(missing)
    if not _time_ok(state["started"]):
        return "started is not a time"
    if state["end"] != UNTIL_BACK and not _time_ok(state["end"]):
        return f'end is neither a time nor "{UNTIL_BACK}"'
    if not isinstance(state["limits"], str):
        return "limits is not text"
    if not (isinstance(state["deny_paths"], list) and all(isinstance(x, str) for x in state["deny_paths"])):
        return "deny_paths is not a list of globs"
    p = state["projects"]
    if p != "all" and not (isinstance(p, list) and all(isinstance(x, str) for x in p)):
        return 'projects is neither a list of names nor "all"'
    if not isinstance(state["unmetered"], bool):
        return "unmetered is not true or false"
    b = state["branches"]
    if not (isinstance(b, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in b.items())):
        return "branches is not {project: branch}"
    i = state["integration"]
    if not (isinstance(i, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in i.items())):
        return "integration is not {project: branch}"
    prs = state.get("prs", {})
    if not (isinstance(prs, dict) and all(isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
                                           for k, v in prs.items())):
        return "prs is not {project: number}"
    if drain:
        if not _time_ok(state.get("ended")):
            return "ended is not a time"
        pend = state.get("pending")
        if not (isinstance(pend, list) and all(isinstance(x, str) for x in pend)):
            return "pending is not a list of names"
    return None


def _read(f: Path, drain: bool, quiet: bool) -> "dict | None":
    """The parsed, valid state in `f`; None for no file or a file that cannot be used (one stderr line says why,
    unless quiet)."""
    try:
        text = f.read_text()
    except FileNotFoundError:
        return None
    except OSError as e:
        if not quiet:
            print(f"improve: {f} cannot be read ({e.strerror}); improve is off", file=sys.stderr)
        return None
    try:
        state = json.loads(text)
        why = _problem(state, drain)
    except ValueError:
        state, why = None, "not valid JSON"
    if why:
        if not quiet:
            print(f"improve: {f} ignored ({why}); improve is off", file=sys.stderr)
        return None
    return state


def _ended(state: dict) -> bool:
    return state["end"] != UNTIL_BACK and dt.datetime.fromisoformat(state["end"]).astimezone() <= now()


def load(quiet: bool = False) -> "dict | None":
    """The state while improve is on; None when it is off: no file, a file that cannot be used (one stderr line says
    why, unless quiet), or a time end that has passed (see ended_state)."""
    state = _read(path(), False, quiet)
    return None if state is None or _ended(state) else state


def ended_state() -> "dict | None":
    """The improve file's state when its time end has passed (the mode is off but not yet drained), else None."""
    state = _read(path(), False, True)
    return state if state is not None and _ended(state) else None


def load_drain(quiet: bool = False) -> "dict | None":
    return _read(drain_path(), True, quiet)


def covers(state: dict, project: str) -> bool:
    return state["projects"] == "all" or project in state["projects"]


def merge_state(project: str) -> "tuple[dict | None, str]":
    """(state, mode) away-merge may merge under for `project`: mode "on" while the mode is on for it, "draining" after
    `off` or the end time until `drained`; (None, why) otherwise."""
    s = load()
    if s and covers(s, project):
        return s, "on"
    d = load_drain()
    if d and project in d["pending"]:
        return d, "draining"
    e = ended_state()
    if e and covers(e, project):
        return e, "draining"
    return None, "improve mode is not on for " + project + (" (and nothing of it is left to wrap up)" if not s else "")


def draining() -> "dict | None":
    """The drain state while a period is wrapping up (after `off` or a time end, until every project is `drained`),
    else None. A time end that nobody has turned into a drain yet counts."""
    d = load_drain(quiet=True)
    if d is None and (e := ended_state()) is not None:
        d = _to_drain(e)
    return d


def rest_path() -> Path:
    return path().with_name("improve-rest.json")


def rested(project: str) -> bool:
    """True while `lane-improve rest <project>` is in force (REST_HOURS), so night-precheck nudges nobody."""
    try:
        t = json.loads(rest_path().read_text()).get(project)
        return now() - dt.datetime.fromisoformat(t).astimezone() < dt.timedelta(hours=REST_HOURS)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def clear_rest() -> None:
    rest_path().unlink(missing_ok=True)


def active_for(project: str) -> bool:
    s = load()
    return bool(s) and covers(s, project)


def write(state: dict) -> None:
    why = _problem(state)
    if why:
        raise ValueError(f"improve state: {why}")
    f = path()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(f)


def clear() -> None:
    path().unlink(missing_ok=True)


def write_drain(state: dict) -> None:
    why = _problem(state, True)
    if why:
        raise ValueError(f"improve drain state: {why}")
    f = drain_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(f)


# --- the command line ----------------------------------------------------------------------------------------------

def parse_until(text: str) -> dt.datetime:
    """`HH:MM` is the next time the clock shows it; otherwise an ISO date and time."""
    n = now()
    try:
        t = dt.datetime.strptime(text, "%H:%M").time()
    except ValueError:
        try:
            d = dt.datetime.fromisoformat(text.replace(" ", "T", 1))
        except ValueError:
            raise ValueError(f"--until {text!r}: use HH:MM or YYYY-MM-DD HH:MM") from None
        d = d.astimezone() if d.tzinfo is None else d
    else:
        d = n.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if d <= n:
            d += dt.timedelta(days=1)
    if d <= n:
        raise ValueError(f"--until {text!r} is already past")
    return d


def coordinators() -> list:
    import machineconf
    return machineconf.coordinators(machineconf.load())


def known_projects() -> list:
    return [c.name for c in coordinators()]


# --- away branches: one per project per away period, on origin, cut from the integration branch -------------------

def _git(repo: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout)


def _git_ok(repo: Path, *args: str) -> str:
    r = _git(repo, *args)
    if r.returncode:
        raise RuntimeError(f"git {args[0]} failed in {repo}: {(r.stderr.strip() or 'no output').splitlines()[-1]}")
    return r.stdout


def integration_branch(repo: Path) -> str:
    """Where the project's PRs go: the base most of its last 30 merged PRs had (read-only gh), leaving out away/* (an
    away period's own merges), else origin's default branch (develop for a project that works on develop shows up
    only through the PR bases)."""
    try:
        r = subprocess.run(["gh", "pr", "list", "--state", "merged", "--limit", "30", "--json", "baseRefName"], cwd=repo,
                           capture_output=True, text=True, timeout=60)
        bases = [x["baseRefName"] for x in json.loads(r.stdout)] if r.returncode == 0 else []
        bases = [b for b in bases if isinstance(b, str) and not b.startswith("away/")]
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        bases = []
    if bases:
        return Counter(bases).most_common(1)[0][0]
    r = _git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if r.returncode == 0 and r.stdout.strip().startswith("origin/"):
        return r.stdout.strip()[len("origin/"):]
    m = re.search(r"^ref: refs/heads/(\S+)\s+HEAD", _git(repo, "ls-remote", "--symref", "origin", "HEAD").stdout, re.M)
    if m:
        return m.group(1)
    raise RuntimeError(f"cannot tell the integration branch of {repo}: set it with `git remote set-head origin --auto`")


def _away_branches(repo: Path) -> list:
    """Every away/* branch on origin (fetched now), sorted by name."""
    _git_ok(repo, "fetch", "-q", "--prune", "origin")
    names = [l.split("refs/remotes/origin/", 1)[1] for l in
             _git_ok(repo, "for-each-ref", "--format=%(refname)", "refs/remotes/origin/away/").splitlines()]
    return sorted(n for n in names if n.startswith("away/"))


def _already_merged(repo: Path, branch: str, integration: str) -> bool:
    """Whether the branch holds nothing the integration branch lacks (adopted, or never given any work)."""
    return _git(repo, "merge-base", "--is-ancestor", f"refs/remotes/origin/{branch}",
                f"refs/remotes/origin/{integration}").returncode == 0


def _has_one_already(repo: Path, have: list, integration: str) -> str:
    b = have[0]
    merged = (f" It is already merged into {integration} (or was never given any work): deleting it is the usual step, "
              "since continuing it builds on the old base."
              if _already_merged(repo, b, integration) else "")
    return (f"it already has its away branch {b}: a project has at most one, ever. Continue it (`--continue`), or adopt it "
            f"(merge it to {integration}) or drop it (delete it) first.{merged}")


def cut_branch(repo: Path, integration: str, today: dt.date) -> str:
    """Push a new away/<today> on origin from origin/<integration>'s head; the checkout and its branches are not touched.
    A project has at most one away branch, ever: ValueError when it has one (or when this very name is taken). Returns the
    name."""
    have = _away_branches(repo)
    if have:
        raise ValueError(_has_one_already(repo, have, integration) if len(have) == 1 else _several(have))
    name = f"away/{today.isoformat()}"
    _git_ok(repo, "push", "-q", "origin", f"refs/remotes/origin/{integration}:refs/heads/{name}")
    return name


def _several(have: list) -> str:
    return (f"it has {len(have)} away branches ({', '.join(have)}); a project has at most one. Adopt (merge) or drop "
            "(delete) all but one first")


def continue_branch(repo: Path) -> str:
    """The project's one away branch, to carry on with. ValueError when there is none, or when there are several (the
    message lists them)."""
    have = _away_branches(repo)
    if not have:
        raise ValueError("no away branch on origin to continue; leave out --continue to cut one")
    if len(have) > 1:
        raise ValueError(_several(have) + "; then --continue takes the one that is left")
    return have[0]


def resolve_branches(cos: list, cont: bool, today: dt.date) -> "tuple[dict, dict, dict]":
    """({project: branch}, {project: integration branch}, {project: "continued" | "cut now"}) for these coordinators.
    Without `cont` every project must have no away branch and gets a newly cut away/<today>. With `cont` a project that
    has its one away branch carries on with it, and one that has none gets one cut (so a new project can join a period
    whose others continue); a project with two or more is refused. Every repo is checked (fetched, integration branch
    found, the one-branch rule applied) before any branch is cut, so a refusal in that phase leaves origin as it was; a
    push that fails while cutting can leave the branches already cut on origin (harmless: `--continue` reuses them)."""
    out, integs, how, plan = {}, {}, {}, []
    for c in cos:
        try:
            integ = integration_branch(c.checkout)
            integs[c.name] = integ
            have = _away_branches(c.checkout)  # fetches: a repo that cannot be reached refuses before anything is cut
            if len(have) > 1:
                raise ValueError(_several(have))
            if have and cont:
                out[c.name], how[c.name] = have[0], "continued"
            elif have:
                raise ValueError(_has_one_already(c.checkout, have, integ))
            else:
                plan.append((c, integ))
        except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            raise ValueError(f"{c.name}: {e}") from None
    for c, integ in plan:
        try:
            out[c.name], how[c.name] = cut_branch(c.checkout, integ, today), "cut now"
        except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            raise ValueError(f"{c.name}: {e}") from None
    return out, integs, how


# --- the draft summary PR: away/<date> -> the integration branch, body = the log's summary ---------------------------

LOG_FILE = "AWAY-LOG.md"
GH_WRITE_TIMEOUT = 60


class PrError(Exception):
    """The summary PR could not be opened, read or refreshed (the message says why)."""


def pr_title(project: str, branch: str) -> str:
    return f"away: {branch.split('/', 1)[-1]} ({project})"


def log_summary(text: "str | None") -> "str | None":
    """The `## Summary` section of an AWAY-LOG.md (up to the next `## ` heading), or None when there is none or it is empty."""
    if not text:
        return None
    m = re.search(r"^## Summary[ \t]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    body = m.group(1).strip() if m else ""
    return body or None


def pr_body(project: str, branch: str, integration: str, log: "str | None") -> str:
    summary = log_summary(log) or ("No summary yet: the log has none until the first item is merged into the branch.")
    return (f"Away-improve branch for {project}: `{branch}` into `{integration}`. A draft, never merged by an agent: merge "
            f"this PR yourself to adopt the whole branch, or name the items to drop first.\n\n{summary}\n\n"
            f"The full log is `{LOG_FILE}` on the branch. This text is refreshed from its summary after every "
            "`away-merge`.\n")


def read_log(repo: Path, branch: str) -> "str | None":
    """AWAY-LOG.md as it is on origin's `branch` (fetched now), or None when the branch has none yet or cannot be read."""
    if _git(repo, "fetch", "-q", "origin", branch).returncode:
        return None
    r = _git(repo, "show", f"FETCH_HEAD:{LOG_FILE}")
    return r.stdout if r.returncode == 0 else None


def _gh(repo: Path, *args: str) -> str:
    try:
        r = subprocess.run(["gh", *args], cwd=repo, capture_output=True, text=True, timeout=GH_WRITE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PrError(f"gh {args[0]} {args[1]} failed ({e})") from None
    if r.returncode:
        raise PrError(f"gh {args[0]} {args[1]} failed ({(r.stderr.strip() or r.stdout.strip() or 'no output').splitlines()[-1]})")
    return r.stdout


def sync_summary_pr(repo: Path, project: str, branch: str, integration: str, number: "int | None") -> "tuple[int, str, str]":
    """(number, url, what) of the project's draft summary PR with its body brought up to the log's summary. With a
    recorded `number` that PR is read and edited (a closed or merged one is left alone: PrError, the owner's call);
    without, the open PR of the branch is found, or a draft one is opened. The only gh writes in the whole mode besides
    away-merge's merge: `pr create --draft` and `pr edit --body`. PrError says why it could not be done."""
    body = pr_body(project, branch, integration, read_log(repo, branch))
    if number is None:
        try:
            found = json.loads(_gh(repo, "pr", "list", "--head", branch, "--state", "open", "--json", "number,url") or "[]")
        except ValueError:
            raise PrError("gh pr list gave an answer that is not JSON") from None
        if found:
            number, url, what = int(found[0]["number"]), str(found[0].get("url", "")), "refreshed"
        else:
            url = _gh(repo, "pr", "create", "--draft", "--base", integration, "--head", branch, "--title",
                      pr_title(project, branch), "--body", body).strip().splitlines()[-1]
            m = re.search(r"/pull/(\d+)\s*$", url)
            if not m:
                raise PrError(f"gh pr create did not print a pull request link ({url[:80]})")
            return int(m.group(1)), url, "opened"
    else:
        try:
            info = json.loads(_gh(repo, "pr", "view", str(number), "--json", "state,url"))
        except ValueError:
            raise PrError(f"PR #{number} cannot be read") from None
        if str(info.get("state", "")).upper() != "OPEN":
            raise PrError(f"summary PR #{number} is {str(info.get('state')).lower()}, so it is left alone: "
                          "what happens to it is the owner's call")
        url, what = str(info.get("url", "")), "refreshed"
    _gh(repo, "pr", "edit", str(number), "--body", body)
    return number, url, what


def _update(fn) -> bool:
    """Apply `fn` to the state file that holds the period (the improve file, else the drain file) and write it back."""
    for f, drain, writer in ((path(), False, write), (drain_path(), True, write_drain)):
        st = _read(f, drain, True)
        if st is not None:
            fn(st)
            writer(st)
            return True
    return False


def record_pr(project: str, number: int) -> bool:
    return _update(lambda st: st.setdefault("prs", {}).__setitem__(project, number))


def sync_and_record(repo: Path, state: dict, project: str) -> "tuple[int, str, str]":
    n, url, what = sync_summary_pr(repo, project, state["branches"][project], state["integration"].get(project, "main"),
                                   state.get("prs", {}).get(project))
    if state.get("prs", {}).get(project) != n:
        record_pr(project, n)
    return n, url, what


# --- deny rules: a backstop for away-merge while the mode is on ---------------------------------------------------

def install_denies(cos: list, integrations: dict) -> "tuple[list, list]":
    """Ask each coordinator's harness adapter for away-improve deny rules. (notes, warnings): what was done or is
    not possible per harness, and what could not be done (the mode still runs: away-merge is the main guard)."""
    import harnesses
    notes, warns = [], []
    for c in cos:
        try:
            mod = harnesses.load_harness(c.harness)
            changes = mod.install_away_denies(c.checkout, integrations[c.name], c.name)
        except (ValueError, OSError) as e:
            warns.append(f"lane-improve: {c.name}: deny rules NOT installed ({e}); away-merge is the only guard on its merge route")
            continue
        how = "deny rules" if c.harness == "claude" else f"its {c.harness} guard plugin"
        if changes or c.harness != "claude":
            notes.append(f"  {how}: {c.name}: a PR merge and a push to {integrations[c.name]} are refused to its coordinator "
                         f"(a backstop; away-merge is the route)")
    return notes, warns


def remove_denies(cos: list) -> list:
    """Take the deny rules out of every coordinator's checkout; returns warnings for what could not be removed."""
    import harnesses
    warns = []
    for c in cos:
        try:
            harnesses.load_harness(c.harness).remove_away_denies(c.checkout)
        except (ValueError, OSError) as e:
            warns.append(f"lane-improve: {c.name}: deny rules not removed ({e})")
    return warns


def describe(s: dict, how: "dict | None" = None) -> list:
    """The lines `on` and `status` show: what is on, so the owner sees exactly what will run."""
    end = ("until back (until you run lane-improve off)" if s["end"] == UNTIL_BACK
           else "ends " + dt.datetime.fromisoformat(s["end"]).strftime("%a %d %b %H:%M"))
    which = "all set-up projects (including ones set up later)" if s["projects"] == "all" else ", ".join(s["projects"])
    lines = [f"  runs: {end}", f"  projects: {which}"]
    for p, b in sorted(s["branches"].items()):
        lines.append(f"  branch: {p} -> {b} (from {s['integration'].get(p, '?')}" + (f"; {how[p]}" if how and p in how else "") + ")")
    for p in sorted(s["branches"]):
        n = s.get("prs", {}).get(p)
        lines.append(f"  summary PR: {p} #{n}, a draft, never merged by an agent" if n else
                     f"  summary PR: {p} not opened yet (away-merge opens it after the first merged item)")
    lines.append("  one away branch per project, ever: it stays until the owner adopts it (merges it to the integration "
                 "branch) or drops it (deletes it); `lane-improve on --continue` carries on with it")
    lines.append(f"  limits: {s['limits'] or '(none given)'}")
    if s["deny_paths"]:
        lines.append(f"  never touching: {', '.join(s['deny_paths'])}")
    lines.append("  budget: " + ("unmetered (no budget steps for improve work; machine limits still apply)" if s["unmetered"]
                                 else "metered (follows the project's budget steps)"))
    return lines


def open_summary_prs(cos: list, branches: dict, integs: dict, known: dict) -> "tuple[dict, list]":
    """({project: PR number}, warnings) for the chosen coordinators' draft summary PRs, found or opened now. A PR gh cannot
    open (offline, no login, no commits between the branches yet: GitHub opens none for a branch with nothing on it)
    is a warning, never a refusal: away-merge opens it after the first merged item."""
    out, warns = {}, []
    for c in cos:
        try:
            n, _url, _what = sync_summary_pr(c.checkout, c.name, branches[c.name], integs[c.name], known.get(c.name))
            out[c.name] = n
        except (PrError, OSError) as e:
            warns.append(f"lane-improve: {c.name}: the draft summary PR is not open yet ({e}); away-merge opens it after "
                         f"the first merged item, or run `lane-improve pr {c.name}`")
    return out, warns


def cmd_on(a) -> int:
    try:
        if a.hours is not None and (a.until or a.until_back):
            raise ValueError("give one of --until, --hours, --until-back")
        if a.until and a.until_back:
            raise ValueError("give one of --until, --hours, --until-back")
        if a.hours is not None:
            if a.hours <= 0:
                raise ValueError("--hours must be more than 0")
            end = (now() + dt.timedelta(hours=a.hours)).isoformat(timespec="seconds")
        elif a.until:
            end = parse_until(a.until).isoformat(timespec="seconds")
        else:
            end = UNTIL_BACK if a.until_back else None  # none given: a re-run keeps the current end, a first run has none
    except ValueError as e:
        print(f"lane-improve: {e}", file=sys.stderr)
        return 2
    if not a.yes and not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("lane-improve: improve mode spends tokens until it is switched off; there is no terminal here, so it needs "
              "--yes (run it yourself, or on your own explicit word)", file=sys.stderr)
        return 2
    prev = load()
    if prev:
        if not a.cont:
            print("lane-improve: improve mode is already on; switching it on again would cut new away branches and leave "
                  "the open PRs without a home. Use `lane-improve off` first, or `--continue` to carry on with the "
                  "current branches", file=sys.stderr)
            return 1
    elif load_drain(quiet=True) or ended_state():
        print("lane-improve: the last improve period is not wrapped up yet; each coordinator runs `lane-improve drained "
              "<project>` after its draft PR (the owner can run `lane-improve drained --all`) before a new period starts",
              file=sys.stderr)
        return 1
    try:
        cos = coordinators()
    except Exception as e:  # machineconf.ConfError, or a missing module in a broken install
        if a.project:
            print(f"lane-improve: cannot check --project names: {e}", file=sys.stderr)
            return 1
        cos = []
    known = [c.name for c in cos]
    unknown = [p for p in a.project if p not in known]
    if unknown:
        print(f"lane-improve: unknown project {', '.join(unknown)}; this machine's coordinators: "
              f"{', '.join(known) or '(none)'}", file=sys.stderr)
        return 1
    if prev and not a.project:
        # a re-run that names no project keeps the period's own (it never widens it): no branch is cut, no rule installed
        projects, chosen = prev["projects"], []
    else:
        projects = list(dict.fromkeys(a.project)) if a.project else "all"
        chosen = cos if projects == "all" else [c for c in cos if c.name in projects]
    try:
        branches, integs, how = resolve_branches(chosen, a.cont, now().date())
    except ValueError as e:
        print(f"lane-improve: improve not switched on: {e}", file=sys.stderr)
        return 1
    deny = None if a.deny_paths is None else [g.strip() for g in a.deny_paths.split(",") if g.strip()]
    prs, pr_warns = open_summary_prs(chosen, branches, integs, (prev or {}).get("prs", {}))
    if prev:  # a `--continue` re-run while on: what is not named or not given again stays as it was
        keep = prev["projects"] == "all" or projects == "all"
        state = {
            "started": prev["started"],
            "end": end or prev["end"],
            "limits": prev["limits"] if a.limits is None else a.limits,
            "deny_paths": prev["deny_paths"] if deny is None else deny,
            "projects": "all" if keep else list(dict.fromkeys([*prev["projects"], *projects])),
            "unmetered": bool(a.unmetered) or prev["unmetered"],
            "branches": {**prev["branches"], **branches},
            "integration": {**prev["integration"], **integs},
            "prs": {**prev.get("prs", {}), **prs},
        }
    else:
        state = {
            "started": now().isoformat(timespec="seconds"),
            "end": end or UNTIL_BACK,
            "limits": a.limits or "",
            "deny_paths": deny or [],
            "projects": projects,
            "unmetered": bool(a.unmetered),
            "branches": branches,
            "integration": integs,
            "prs": prs,
        }
    write(state)
    clear_rest()  # a new instruction from the owner: every coordinator looks for work again
    notes, warns = install_denies(chosen, integs)
    print("lane-improve: improve mode on")
    print("\n".join(describe(state, how) + notes))
    for w in [*warns, *pr_warns]:
        print(w, file=sys.stderr)
    return 0


def _coordinator_list() -> list:
    try:
        return coordinators()
    except Exception:  # no readable machine.conf
        return []


def _to_drain(state: dict) -> dict:
    """The drain state for a state that is ending now (or has ended): every project it covers is still to wrap up."""
    names = list(state["branches"]) if state["projects"] == "all" else list(state["projects"])
    if state["projects"] == "all":
        names = list(dict.fromkeys([*names, *(c.name for c in _coordinator_list())]))
    ended = state["end"] if state["end"] != UNTIL_BACK else now().isoformat(timespec="seconds")
    if state["end"] != UNTIL_BACK and not _ended(state):
        ended = now().isoformat(timespec="seconds")
    return {**state, "ended": ended, "pending": names}


def cmd_off(a) -> int:
    """End the mode. What was running is left to drain: nothing starts, the last PRs can still merge, the deny rules stay
    until `drained`. With nothing to drain, leftover deny rules are cleaned up."""
    state = load() or ended_state()
    if state is not None:
        write_drain(_to_drain(state))
        clear()
        clear_rest()
        print("lane-improve: improve mode ended; the running lanes drain: each coordinator merges its last PR with "
              "away-merge, opens its draft PR, then runs `lane-improve drained <project>` (`lane-improve status` lists them)")
    elif load_drain(quiet=True) is None:
        for w in remove_denies(_coordinator_list()):
            print(w, file=sys.stderr)
    return 0


def cmd_drained(a) -> int:
    """`drained <project>...` or `--all`: the project's wrap-up is done; its deny rules come out and the state clears."""
    state = load_drain(quiet=True)
    if state is None:
        e = ended_state()
        if e is not None:
            state = _to_drain(e)
            write_drain(state)
            clear()
    if state is None:
        print("lane-improve: nothing is draining", file=sys.stderr)
        return 1
    names = list(state["pending"]) if a.all else a.projects
    if not names:
        print("lane-improve: say which project is wrapped up (`lane-improve drained <project>`), or --all", file=sys.stderr)
        return 2
    bad = [n for n in names if n not in state["pending"]]
    if bad:
        print(f"lane-improve: not draining: {', '.join(bad)} (draining: {', '.join(state['pending']) or 'none'})", file=sys.stderr)
        return 1
    cos = {c.name: c for c in _coordinator_list()}
    for w in remove_denies([cos[n] for n in names if n in cos]):
        print(w, file=sys.stderr)
    left = [n for n in state["pending"] if n not in names]
    if left:
        write_drain({**state, "pending": left})
    else:
        drain_path().unlink(missing_ok=True)
    print(f"lane-improve: wrapped up: {', '.join(names)}" + (f"; still draining: {', '.join(left)}" if left else
                                                          "; the improve period is closed"))
    return 0


def cmd_pr(a) -> int:
    """`pr <project>`: open the project's draft summary PR if it has none, and bring its body up to the log's summary
    (away-merge does this after every merge; this is the hand for a PR that could not be opened or refreshed, and the
    coordinator's wrap-up step). Prints the link. Never merges, never marks it ready."""
    s, mode = merge_state(a.project)
    if s is None:
        print(f"lane-improve: {mode}", file=sys.stderr)
        return 1
    if a.project not in s["branches"]:
        print(f"lane-improve: {a.project} has no away branch recorded (`lane-improve on --continue --project {a.project}`)",
              file=sys.stderr)
        return 1
    cos = {c.name: c for c in _coordinator_list()}
    if a.project not in cos:
        print(f"lane-improve: {a.project} is not a coordinator in machine.conf", file=sys.stderr)
        return 1
    try:
        n, url, what = sync_and_record(cos[a.project].checkout, s, a.project)
    except (PrError, OSError) as e:
        print(f"lane-improve: {a.project}: {e}", file=sys.stderr)
        return 1
    print(f"lane-improve: summary PR #{n} of {a.project} {what} (a draft; only the owner merges it): {url}")
    return 0


def cmd_rest(a) -> int:
    """A coordinator's note that nothing within the limits is left: its idle nudges stop for REST_HOURS (or until the
    owner switches the mode on or off again), then one nudge looks for new work. Only quiets; starts nothing."""
    s = load(quiet=True)
    if not (s and covers(s, a.project)):
        print(f"lane-improve: improve mode is not on for {a.project}; nothing to rest", file=sys.stderr)
        return 1
    try:
        d = json.loads(rest_path().read_text())
        d = d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        d = {}
    d[a.project] = now().isoformat(timespec="seconds")
    f = rest_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(d, indent=2, sort_keys=True) + "\n")
    tmp.replace(f)
    print(f"lane-improve: {a.project} rests for {REST_HOURS} h: no idle nudges until then, or until the mode is switched "
          "on or off again")
    return 0


def cmd_status(a) -> int:
    s = load()
    if s:
        print(f"improve: on (since {dt.datetime.fromisoformat(s['started']).strftime('%a %d %b %H:%M')})")
        print("\n".join(describe(s)))
        return 0
    d = load_drain(quiet=True)
    if d is None and (e := ended_state()) is not None:
        d = _to_drain(e)
    if d is not None:
        when = dt.datetime.fromisoformat(d["ended"]).strftime("%a %d %b %H:%M")
        print(f"improve: draining (ended {when}); no new cycle starts, the running lanes' PRs can still merge")
        print("  to wrap up: " + ", ".join(d["pending"]))
        waited = (now() - dt.datetime.fromisoformat(d["ended"]).astimezone()).total_seconds() / 3600
        if waited >= DRAIN_NOTICE_HOURS:
            print(f"  it has waited {int(waited)} h: nobody has run `lane-improve drained` for it; ask the coordinator, or run "
                  "`lane-improve drained --all` yourself once its last PR is dealt with")
        for p in d["pending"]:
            if p in d["branches"]:
                print(f"  {p}: away-merge the last PR into {d['branches'][p]}, open a draft PR into "
                      f"{d['integration'].get(p, '?')}, then run `lane-improve drained {p}`")
        return 0
    print("improve: off")
    return 0


def cmd_is_on(a) -> int:
    return 0 if load(quiet=True) else 1


def cmd_unmetered(a) -> int:
    """Exit 0 when the mode is on for the project and was started --unmetered (budget-state reads this)."""
    s = load(quiet=True)
    return 0 if s and covers(s, a.project) and s["unmetered"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="improve.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    on = sub.add_parser("on")
    on.add_argument("--until")
    on.add_argument("--hours", type=float)
    on.add_argument("--until-back", action="store_true")
    on.add_argument("--limits")
    on.add_argument("--deny-paths")
    on.add_argument("--project", action="append", default=[])
    on.add_argument("--unmetered", action="store_true")
    on.add_argument("--continue", dest="cont", action="store_true")
    on.add_argument("--yes", action="store_true")
    sub.add_parser("off")
    dr = sub.add_parser("drained")
    dr.add_argument("projects", nargs="*")
    dr.add_argument("--all", action="store_true")
    sub.add_parser("status")
    pr = sub.add_parser("pr")
    pr.add_argument("project")
    rs = sub.add_parser("rest")
    rs.add_argument("project")
    sub.add_parser("is-on")
    um = sub.add_parser("unmetered")
    um.add_argument("project")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    return {"on": cmd_on, "off": cmd_off, "drained": cmd_drained, "status": cmd_status, "is-on": cmd_is_on,
            "unmetered": cmd_unmetered, "rest": cmd_rest, "pr": cmd_pr}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
