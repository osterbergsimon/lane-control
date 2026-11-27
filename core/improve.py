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
  stop_at    weekly % (a number above 0 and below 100) or null: no new cycle for a project once `budget-state --project`
             gives weekly >= it; metered periods only (optional, null = off)
  max_items  whole number or null: no new cycle for a project after this many away-merges in the period (optional)
  max_hours  number or null: no new cycle for a project once this many hours have passed since `started`, the wall clock;
             the one cap an --unmetered period keeps by default (UNMETERED_DEFAULT_HOURS when it has no end and the owner
             gave none; `--max-hours none` drops it). Not a hard stop: the running lane still lands (optional)
  prs        {project: number}: the draft summary PR (away/<date> -> the integration branch), opened at `on` or, when gh
             could not then, by away-merge after the first merge; its body is built from the branch's merge commits (each
             carries its item's log entry) plus the coordinator's summary, refreshed by away-merge after every merge.
             Optional (a file without it is fine); never merged by an agent.
  maintenance true while the period takes maintenance merges (`on --maintenance`; optional, missing = false): a PR whose every
             path matches the project's maintenance globs (core/maintenance.py) merges into the integration branch through
             `away-merge --maintenance`, and the away branch then takes the integration branch in
             (docs/superpowers/specs/2026-11-27-ratchet-maintenance-merges-design.md)

Every reader treats a missing, malformed or ended file as improve off; a malformed one says why on one stderr line.
lane-limits.sh has its own copy of this validation (a lone copy of that script cannot import this module):
tests/test_lane_away.py ReaderAgreementTests runs both over the same files, so keep them in step.

Ending the mode (`off`, or the end time) leaves a drain state: the running lane's PR can still merge through
away-merge, no new cycle starts, and each coordinator then opens its draft PR and runs `lane-improve drained <project>`.
After `off` the drain state is improve-drain.json (the same fields, plus `ended` and `pending`); after an end time it is
the old improve.json, which `drained` turns into improve-drain.json. The deny rules stay until `drained`.
A cap reached ends the project's cycles like the end time does: the lane that is running finishes and merges, the
coordinator wraps up (the summary, `lane-improve pr <project> --summary-file <path>`, its message to the owner) and runs `lane-improve drained <project>`,
which with the mode on records the wrap-up in improve-period.json (beside the improve file; keyed to the period's `started`,
holds away-merge's record of its merges and the wrapped-up projects). The mode stays on for the other projects.
Command line (lane-improve calls it): `improve.py on ...`, `off`, `drained`, `status`.
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

UNTIL_BACK = "until-back"
DRAIN_NOTICE_HOURS = 12   # `status` says so when a drain has waited this long
REST_HOURS = 12           # `lane-improve rest` quiets a project's nudges this long, then one nudge looks for new work
MAX_HOURS_LIMIT = 24 * 366   # --max-hours: at most a year
UNMETERED_DEFAULT_HOURS = 12  # --max-hours an --unmetered period with no end gets unless the owner gives one (or `none`)
FIELDS = ("started", "end", "limits", "deny_paths", "projects", "unmetered", "branches", "integration")


def path() -> Path:
    v = os.environ.get("LANE_IMPROVE_FILE")
    return Path(v) if v else Path.home() / ".config" / "lane-control" / "improve.json"


def now() -> dt.datetime:
    """The time now; LANE_NOW (seconds since the epoch) stands in for it in tests (as LANE_HOUR does for lane-limits.sh)."""
    v = os.environ.get("LANE_NOW")
    if v:
        try:
            return dt.datetime.fromtimestamp(float(v)).astimezone()
        except (ValueError, OverflowError, OSError):
            pass
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
    sa = state.get("stop_at")
    if sa is not None and not (isinstance(sa, (int, float)) and not isinstance(sa, bool) and 0 < sa < 100):
        return "stop_at is not a weekly percentage above 0 and below 100"
    mx = state.get("max_items")
    if mx is not None and not (isinstance(mx, int) and not isinstance(mx, bool) and mx >= 1):
        return "max_items is not a whole number above 0"
    if not isinstance(state.get("max_hours_off", False), bool):
        return "max_hours_off is not true or false"
    mh = state.get("max_hours")
    if mh is not None and not (isinstance(mh, (int, float)) and not isinstance(mh, bool) and 0 < mh <= MAX_HOURS_LIMIT):
        return f"max_hours is not a number of hours above 0 and at most {MAX_HOURS_LIMIT}"
    if not isinstance(state.get("maintenance", False), bool):
        return "maintenance is not true or false"
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


def file_problem(f: Path, drain: bool) -> "str | None":
    """Why a state file that is there cannot be used, or None (no file, or a good one)."""
    try:
        text = f.read_text()
    except FileNotFoundError:
        return None
    except OSError as e:
        return f"{f.name} cannot be read ({e.strerror})"
    try:
        why = _problem(json.loads(text), drain)
    except ValueError:
        why = "not valid JSON"
    return f"{f.name}: {why}" if why else None


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


def mode_state(project: str) -> "tuple[dict | None, str]":
    """(state, mode) for `project` from the state files alone: mode "on" while the mode is on for it, "draining" after `off`
    or the end time until `drained`; (None, why) otherwise. No cap or period record is consulted (see merge_state)."""
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


def merge_state(project: str) -> "tuple[dict | None, str]":
    """(state, mode) away-merge may merge under for `project`: mode_state, unless a hard stop applies (hard_stop: the period's
    record is unreadable, the project wrapped up at its cap, or --max-items is reached); then (None, why)."""
    st, mode = mode_state(project)
    if st is None:
        return None, mode
    stop = hard_stop(st, project, draining=(mode == "draining"))
    return (None, stop) if stop else (st, mode)


def draining() -> "dict | None":
    """The drain state while a period is wrapping up (after `off` or a time end, until every project is `drained`),
    else None. A time end that nobody has turned into a drain yet counts."""
    d = load_drain(quiet=True)
    if d is None and (e := ended_state()) is not None:
        d = _to_drain(e)
    return d if d is not None and d["pending"] else None


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
    _atomic(path(), json.dumps(state, indent=2, sort_keys=True) + "\n")


def _atomic(f: Path, text: str) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f.name + ".", suffix=".tmp", dir=f.parent)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, f)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def clear() -> None:
    path().unlink(missing_ok=True)


def write_drain(state: dict) -> None:
    why = _problem(state, True)
    if why:
        raise ValueError(f"improve drain state: {why}")
    _atomic(drain_path(), json.dumps(state, indent=2, sort_keys=True) + "\n")


# --- the caps: how many items, how much of the weekly budget, per project ------------------------------------------

def period_path() -> Path:
    return path().with_name("improve-period.json")


def lock_path() -> Path:
    return path().with_name("improve.lock")


_lock_depth = 0


@contextlib.contextmanager
def _locked():
    """An exclusive lock on the improve state's lock file, around every read-modify-write of the period record and of the
    state file (away-merge runs in every coordinator at once, `lane-improve` beside them). Re-entrant in one process, so
    away-merge can hold it from the --max-items check through record_merge (which takes it again) without waiting on itself."""
    global _lock_depth
    if _lock_depth:
        _lock_depth += 1
        try:
            yield
        finally:
            _lock_depth -= 1
        return
    f = lock_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        _lock_depth = 1
        try:
            yield
        finally:
            _lock_depth = 0


def _has_caps(state: dict) -> bool:
    return bool(state.get("max_items")) or state.get("stop_at") is not None or bool(state.get("max_hours"))


def _fresh_period(state: dict) -> dict:
    return {"period": state["started"], "merges": {}, "wrapped": []}


def _read_period(state: dict) -> "tuple[dict | None, str | None]":
    """(record, None) for this period's record, or (None, why) when it cannot be trusted. A file of another period is no
    record of this one. A missing file is an empty record only while no cap is set: with a cap, `lane-improve on` made
    it, so a missing or unreadable one is a LOST record (merge counts and wrap-ups gone), never a reset."""
    f = period_path()
    try:
        text = f.read_text()
    except FileNotFoundError:
        if _has_caps(state):
            return None, f"{f.name} is missing (it holds the merge counts and wrap-ups of the period)"
        return _fresh_period(state), None
    except OSError as e:
        return None, f"{f.name} cannot be read ({e.strerror or e})"
    try:
        d = json.loads(text)
    except ValueError:
        return None, f"{f.name} is not valid JSON"
    if not (isinstance(d, dict) and isinstance(d.get("merges"), dict) and isinstance(d.get("wrapped"), list)
            and all(isinstance(x, str) for x in d["wrapped"])):
        return None, f"{f.name} is not a period record"
    if d.get("period") != state["started"]:
        return _fresh_period(state), None  # an older period's file
    return {"period": d["period"], "merges": d["merges"], "wrapped": d["wrapped"]}, None


def period_problem(state: dict) -> "str | None":
    """Why this period's record cannot be trusted, or None. While it cannot, every merge is refused and nothing is nudged:
    the caps fail closed."""
    return _read_period(state)[1]


FIX_PERIOD = ("`lane-improve on --continue` starts the record again from nothing, and puts the deny rules back on every "
              "project the period covers")


FIX_DRAINING_PERIOD = ("wrap it up with `lane-improve drained <project>` (the owner can run `lane-improve drained --all`), then "
                       "`lane-improve on --continue` starts a new period on the same away branch (its last PR can merge there)")


class PeriodError(OSError):
    pass


def _write_period(rec: dict) -> None:
    """Atomically, through a temp file of its own (two writers never share one)."""
    f = period_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f.name + ".", suffix=".tmp", dir=f.parent)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(rec, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, f)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def start_period(state: dict) -> None:
    """The record of a new period (or a lost one started again): empty."""
    with _locked():
        _write_period(_fresh_period(state))


def _modify_period(state: dict, fn) -> None:
    with _locked():
        rec, why = _read_period(state)
        if rec is None:
            raise PeriodError(why)
        fn(rec)
        _write_period(rec)


def record_merge(state: dict, project: str, pr: int, item: str, sha: str, extra: "dict | None" = None) -> None:
    """away-merge's own record of a merge it made (what --max-items counts; never read from the log's text). `extra` adds
    fields: a maintenance merge has kind "maintenance", taken_in (whether the away branch took it in), target (the commit to
    take in) and take_in (what happened). Refuses to write over a record that cannot be read (PeriodError): the next merge is
    refused for it too."""
    def add(rec):
        m = rec["merges"].setdefault(project, [])
        if not isinstance(m, list):
            m = rec["merges"][project] = []
        m.append({"pr": pr, "item": item, "sha": sha, "at": now().isoformat(timespec="seconds"), **(extra or {})})
    _modify_period(state, add)


def merges_of(state: dict, project: str) -> list:
    """The period record's merges for a project ([] when there are none or the record cannot be read)."""
    rec, _ = _read_period(state)
    m = (rec or {"merges": {}})["merges"].get(project)
    return [x for x in m if isinstance(x, dict)] if isinstance(m, list) else []


def maintenance_done(state: dict, project: str) -> int:
    return sum(1 for x in merges_of(state, project) if x.get("kind") == "maintenance")


def items_done(state: dict, project: str) -> int:
    rec, _ = _read_period(state)
    m = (rec or {"merges": {}})["merges"].get(project)
    return len(m) if isinstance(m, list) else 0


def is_wrapped(state: dict, project: str) -> bool:
    """True when the project wrapped up this period while the mode was on (a cap was reached): nothing more merges. A record
    that cannot be read counts as wrapped for everyone (fail closed)."""
    rec, why = _read_period(state)
    return why is not None or project in rec["wrapped"]


def mark_wrapped(state: dict, projects: list) -> None:
    _modify_period(state, lambda rec: rec.__setitem__("wrapped", list(dict.fromkeys([*rec["wrapped"], *projects]))))


def unmark_wrapped(state: dict, projects: list) -> None:
    _modify_period(state, lambda rec: rec.__setitem__("wrapped", [p for p in rec["wrapped"] if p not in projects]))


def hard_stop(state: dict, project: str, draining: bool = False) -> "str | None":
    """Why away-merge refuses a merge for this project at all, or None. `--max-items N` is enforced here: the Nth merge is
    the last, whatever the coordinator reads or does (a lane that is running finishes as that Nth merge); a record that
    cannot be read stops everything; a project that wrapped up at its cap merges nothing more. `--stop-at` is not a hard
    stop (the lane that is running when it is reached still lands): the budget's own steps are its backstop."""
    why = period_problem(state)
    if why and draining:  # a drain refuses `on --continue` (the period is not wrapped up yet): name what does work
        return (f"{why}, so merges and caps cannot be trusted; the period is draining, so nothing more merges in it: "
                f"{FIX_DRAINING_PERIOD}")
    if why:
        return f"{why}, so merges and caps cannot be trusted; merges are refused until it is made again: {FIX_PERIOD}"
    if is_wrapped(state, project):
        return (f"{project} wrapped up its improve period (a cap was reached and it ran `lane-improve drained`): nothing more "
                "merges; raising a cap and naming it (`lane-improve on --continue --project "
                f"{project} --max-items <more>`) resumes it")
    mx = state.get("max_items")
    if mx and items_done(state, project) >= mx:
        return (f"--max-items {mx} is reached for {project} ({items_done(state, project)} merged this period): nothing more "
                f"merges; wrap up (write the summary, `lane-improve pr {project} --summary-file <path>`, tell the owner, `lane-improve drained {project}`), or the "
                "owner raises the cap")
    return None


def budget_reading(project: str) -> "tuple[int | None, int | None, str]":
    """(the highest weekly % and the lowest ceiling over the project's metered budgets, why not): `budget-state
    --project`, read-only. Both None, with the reason, when it gives no reading."""
    try:
        r = subprocess.run([str(HERE / "scripts" / "budget-state"), "--project", project], capture_output=True, text=True,
                           timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, None, f"budget-state failed ({e})"
    if r.returncode:
        return None, None, (r.stderr.strip().splitlines() or ["budget-state failed"])[-1].removeprefix("budget-state: ")
    weekly, ceil = [], []
    for line in r.stdout.splitlines():
        kv = dict(x.split("=", 1) for x in line.split()[1:] if "=" in x)
        if kv.get("weekly", "").isdigit():
            weekly.append(int(kv["weekly"]))
        if kv.get("ceiling", "").isdigit():
            ceil.append(int(kv["ceiling"]))
    if not weekly:
        return None, None, (r.stdout.strip().splitlines() or ["no metered budget in the config"])[0]
    return max(weekly), min(ceil) if ceil else None, ""


def _pct(v) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def caps(state: dict, project: str) -> "tuple[list, str | None]":
    """(parts, hit) for a project: the progress as `status` shows it (["items 4/10", "weekly 66/70"], only the caps that are
    set; weekly is `?` when the usage feed gives no reading, which is no cap hit) and the first cap reached as `items:10/10` or
    `weekly:70/70`, else None."""
    parts, hit = [], None
    mx = state.get("max_items")
    if mx:
        n = items_done(state, project)
        parts.append(f"items {n}/{mx}")
        if n >= mx:
            hit = f"items:{n}/{mx}"
    sa = state.get("stop_at")
    if sa is not None:
        w, _c, _why = budget_reading(project)
        parts.append(f"weekly {w if w is not None else '?'}/{_pct(sa)}")
        if w is not None and w >= sa and not hit:
            hit = f"weekly:{w}/{_pct(sa)}"
    mh = state.get("max_hours")
    if mh:
        h = hours_elapsed(state)
        parts.append(f"hours {h:.1f}/{_pct(mh)}")
        if h >= mh and not hit:
            hit = f"hours:{h:.1f}/{_pct(mh)}"
    return parts, hit


def hours_elapsed(state: dict) -> float:
    """Hours of wall clock since the period started (what --max-hours counts)."""
    return max((now() - dt.datetime.fromisoformat(state["started"]).astimezone()).total_seconds() / 3600, 0.0)


def cap_text(state: dict) -> str:
    bits = []
    if state.get("max_items"):
        bits.append(f"{state['max_items']} away-merges in the period")
    if state.get("stop_at") is not None:
        bits.append(f"weekly budget use at {_pct(state['stop_at'])}%")
    if state.get("max_hours"):
        bits.append(f"{_pct(state['max_hours'])} hours of wall clock since the start")
    return "no new cycle for a project after " + " or ".join(bits) if bits else ""


CLEAR = object()


def parse_hours(text: "str | None"):
    """None (not given), CLEAR (`none`) or the hours of --max-hours (a number above 0, at most MAX_HOURS_LIMIT)."""
    if text is None:
        return None
    if text.strip().lower() == "none":
        return CLEAR
    try:
        v = float(text)
    except ValueError:
        v = None
    if v is None or v != v or not 0 < v <= MAX_HOURS_LIMIT:
        raise ValueError(f"--max-hours {text!r}: use a number of hours above 0 (at most {MAX_HOURS_LIMIT}), or `none` to drop it")
    return int(v) if v.is_integer() else v


def parse_cap(flag: str, text: "str | None", whole: bool):
    """None (not given), CLEAR (`none`) or the value of --stop-at (a percentage above 0 and below 100) / --max-items (a
    whole number above 0). ValueError says what is wrong."""
    if text is None:
        return None
    if text.strip().lower() == "none":
        return CLEAR
    try:
        v = int(text) if whole else float(text)
    except ValueError:
        v = None
    if v is None or isinstance(v, float) and v != v or (whole and v < 1) or (not whole and not 0 < v < 100):
        raise ValueError(f"{flag} {text!r}: use " + ("a whole number above 0" if whole else "a weekly percentage above 0 and below 100")
                         + ", or `none` to drop it")
    return v if whole or not float(v).is_integer() else int(v)


# --- the cycle: where each project's away-improve cycle stands, and the ledger of the cycles it closed ----------------
# The coordinator writes its cycle's phase with `lane-improve cycle <project> --phase ...` at each step (field report B4: a
# coordinator restart mid-cycle lost the phase and risked an orphaned or a second lane). It reads it back on resume, and
# away-watchdog reads it for the lane's deadline, its dispatch and the coordinator's own address. One file for all
# projects, improve-cycle.json beside the improve file, keyed to the period's `started` like the period record; written under
# the improve lock. The ledger, improve-ledger.jsonl, gets one line per closed cycle (merged by away-merge, or parked).

PHASES = ("queued", "scout", "build", "review", "merge", "idle")
LANE_PHASES = ("queued", "scout", "build", "review", "merge")   # a lane is out: its deadline counts
NEW_LANE_PHASES = ("queued", "scout", "build")                  # a lane starts here: lane_started and the deadline are set
WORKERS_KEPT = 8   # the other dispatches a cycle records (`--worker`: a reviewer, a queued lane), newest last
DEFAULT_LANE_BUDGET_MIN = 240
ADDRESS = re.compile(r"(?:term_[A-Za-z0-9-]{1,80}|run:[A-Za-z0-9_-]{1,80}|dispatch:[A-Za-z0-9_-]{1,80})")
DISPATCH = re.compile(r"[A-Za-z0-9_-]{1,80}")
FINDINGS_LIMIT = 4000
ITEM_LIMIT = 200


def cycle_path() -> Path:
    return path().with_name("improve-cycle.json")


def ledger_path() -> Path:
    return path().with_name("improve-ledger.jsonl")


def read_cycles(state: dict) -> dict:
    """{project: record} of this period (a file of another period, a missing or an unreadable one: {})."""
    try:
        d = json.loads(cycle_path().read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict) or d.get("period") != state["started"] or not isinstance(d.get("projects"), dict):
        return {}
    return {k: v for k, v in d["projects"].items() if isinstance(k, str) and isinstance(v, dict)}


def cycle(state: dict, project: str) -> dict:
    return read_cycles(state).get(project, {})


def _iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="seconds")


def _when(v) -> "dt.datetime | None":
    try:
        return dt.datetime.fromisoformat(v).astimezone()
    except (TypeError, ValueError):
        return None


def set_cycle(state: dict, project: str, changes: dict, budget_min: "int | None" = None) -> dict:
    """Merge `changes` into the project's cycle record, under the lock, and return it. A move into scout or build with a new
    dispatch (or from another phase) is a new lane: lane_started is now and the deadline is now + its budget (`budget_min`,
    else DEFAULT_LANE_BUDGET_MIN); a new scout clears the item, PR and round it does not give. A budget given later sets the
    deadline again from now. `updated` is now."""
    with _locked():
        allc = read_cycles(state)
        rec = dict(allc.get(project, {}))
        t = now()
        phase = changes.get("phase")
        # a new lane: a scout after anything else, a build that does not follow its own build/review/merge rounds, or
        # any new dispatch; a build round after a review (the same worker fixing findings) keeps its deadline
        same_lane = (phase,) if phase in ("scout", "queued") else ("build", "review", "merge")
        new_lane = phase in NEW_LANE_PHASES and (rec.get("phase") not in same_lane or
                                                 ("dispatch" in changes and changes["dispatch"] != rec.get("dispatch")))
        worker = changes.pop("worker", None)
        if new_lane:
            if phase in ("scout", "queued"):
                for k in ("item", "pr", "round"):
                    rec.pop(k, None)
            elif "round" not in changes and changes.get("item", rec.get("item")) != rec.get("item"):
                rec["round"] = 0   # a new item; a fresh worker on the same item (a recovery) keeps its rounds
            rec.setdefault("round", 0)
            rec["lane_started"] = _iso(t)
        rec.update(changes)
        if worker:  # another dispatch working for this cycle (a reviewer, say): watched as live, never a new lane
            rec["workers"] = [w for w in rec.get("workers", []) if w != worker][-(WORKERS_KEPT - 1):] + [worker]
        if new_lane or budget_min is not None:
            b = budget_min if budget_min is not None else int(rec.get("budget_min") or DEFAULT_LANE_BUDGET_MIN)
            rec["budget_min"] = b
            rec["deadline"] = _iso(t + dt.timedelta(minutes=b))
        if rec.get("phase") == "idle":
            for k in ("deadline", "lane_started", "dispatch", "workers"):
                rec.pop(k, None)
        rec["updated"] = _iso(t)
        allc[project] = rec
        _atomic(cycle_path(), json.dumps({"period": state["started"], "projects": allc}, indent=2, sort_keys=True) + "\n")
        return rec


def close_cycle(state: dict, project: str, outcome: str, sha: str = "", metric: str = "", findings: str = "",
                pr: "int | None" = None, item: "str | None" = None) -> dict:
    """The cycle ends (`merged` by away-merge, or `parked` with its findings): one ledger line (item, PR, merge sha, metric
    delta, review rounds, parked findings, the lane's minutes), and the phase goes back to idle (the address is kept).
    Returns the ledger record."""
    with _locked():
        rec = cycle(state, project)
        started = _when(rec.get("lane_started"))
        row = {"at": _iso(now()), "period": state["started"], "project": project, "outcome": outcome,
               "item": item if item is not None else rec.get("item", ""), "pr": pr if pr is not None else rec.get("pr"),
               "sha": sha, "metric": metric, "rounds": int(rec.get("round") or 0), "findings": findings,
               "lane_minutes": int((now() - started).total_seconds() // 60) if started else None}
        f = ledger_path()
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        keep = {k: rec[k] for k in ("address",) if k in rec}
        allc = read_cycles(state)
        allc[project] = {**keep, "phase": "idle", "last_outcome": outcome, "updated": _iso(now())}
        _atomic(cycle_path(), json.dumps({"period": state["started"], "projects": allc}, indent=2, sort_keys=True) + "\n")
        return row


def read_ledger(since: "dt.datetime | None" = None, project: "str | None" = None) -> list:
    """The ledger's records, oldest first (a line that is not a record is skipped), at or after `since`, for `project`."""
    try:
        lines = ledger_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict) or (project and r.get("project") != project):
            continue
        if since is not None and ((_when(r.get("at")) or since) < since):
            continue
        out.append(r)
    return out


def last_activity(state: dict, project: str) -> dt.datetime:
    """The newest sign of the project's cycle: its cycle record's `updated`, its last away-merge in the period record, else
    the period's start."""
    times = [_when(state["started"])]
    times.append(_when(cycle(state, project).get("updated")))
    rec, _ = _read_period(state)
    m = (rec or {"merges": {}})["merges"].get(project)
    if isinstance(m, list) and m and isinstance(m[-1], dict):
        times.append(_when(m[-1].get("at")))
    return max(t for t in times if t is not None)


def cycle_text(project: str, rec: dict) -> str:
    if not rec:
        return f"{project}: no cycle recorded in this period (start one: `lane-improve cycle {project} --phase scout ...`)"
    bits = [f"phase {rec.get('phase', '?')}"]
    if rec.get("item"):
        bits.append(f"item {rec['item']!r}")
    if rec.get("pr"):
        bits.append(f"PR #{rec['pr']}")
    if rec.get("phase") in ("build", "review", "merge"):
        bits.append(f"round {rec.get('round', 0)}")
    for k in ("dispatch", "worktree", "address"):
        if rec.get(k):
            bits.append(f"{k} {rec[k]}")
    if rec.get("workers"):
        bits.append("other workers " + " ".join(rec["workers"]))
    d = _when(rec.get("deadline"))
    if d:
        bits.append(f"deadline {d.strftime('%a %d %b %H:%M')}" + (" (passed)" if d <= now() else ""))
    if rec.get("updated"):
        bits.append(f"updated {_when(rec['updated']).strftime('%a %d %b %H:%M')}")
    return f"{project}: " + ", ".join(bits)


def cmd_cycle(a) -> int:
    """`cycle <project>`: print where the cycle stands (read it on resume; --json for the record). With --phase (and the
    rest) record a step; with --close merged|parked end the cycle into the ledger. Mode on or draining only."""
    s, mode = mode_state(a.project)
    if s is None:
        print(f"lane-improve: {mode}", file=sys.stderr)
        return 1
    changes = {}
    try:
        if a.phase is not None:
            if a.phase not in PHASES:
                raise ValueError(f"--phase {a.phase!r}: one of {', '.join(PHASES)}")
            changes["phase"] = a.phase
        if a.item is not None:
            item = " ".join(a.item.split())
            if not item or len(item) > ITEM_LIMIT:
                raise ValueError(f"--item: a title of 1 to {ITEM_LIMIT} characters")
            changes["item"] = item
        if a.pr is not None:
            if a.pr < 1:
                raise ValueError("--pr: a pull request number")
            changes["pr"] = a.pr
        if a.round is not None:
            if a.round < 0:
                raise ValueError("--round: 0 or more")
            changes["round"] = a.round
        if a.dispatch is not None:
            if not DISPATCH.fullmatch(a.dispatch):
                raise ValueError("--dispatch: the lane's Orca dispatch id (ctx_...)")
            changes["dispatch"] = a.dispatch
        if a.worker is not None:
            if not DISPATCH.fullmatch(a.worker):
                raise ValueError("--worker: another Orca dispatch id (ctx_...) working for this cycle, such as its reviewer")
            changes["worker"] = a.worker
        if a.worktree is not None:
            if not os.path.isabs(a.worktree):
                raise ValueError("--worktree: the lane worktree's absolute path")
            changes["worktree"] = a.worktree
        if a.budget_min is not None and a.budget_min < 1:
            raise ValueError("--budget-min: minutes, 1 or more")
        address = a.address
        if address is None and (changes or a.close) and ADDRESS.fullmatch(os.environ.get("ORCA_TERMINAL_HANDLE", "")):
            address = os.environ["ORCA_TERMINAL_HANDLE"]  # the caller's own terminal: the coordinator's explicit address
        if address is not None:
            if not ADDRESS.fullmatch(address):
                raise ValueError("--address: term_<id>, run:<id> or dispatch:<id> (an Orca address, never a title)")
            changes["address"] = address
        findings = ""
        if a.findings_file:
            if a.close != "parked":
                raise ValueError("--findings-file goes with --close parked")
            try:
                findings = Path(a.findings_file).read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError) as e:
                raise ValueError(f"--findings-file {a.findings_file} cannot be read ({getattr(e, 'strerror', None) or e})")
            if len(findings) > FINDINGS_LIMIT:
                raise ValueError(f"--findings-file is {len(findings)} characters; at most {FINDINGS_LIMIT} (the issues hold the rest)")
        if a.close == "parked" and not findings:
            raise ValueError("--close parked needs --findings-file: a parked item carries the findings it stopped on")
        if a.metric is not None and len(a.metric) > ITEM_LIMIT:
            raise ValueError(f"--metric: at most {ITEM_LIMIT} characters")
    except ValueError as e:
        print(f"lane-improve: {e}", file=sys.stderr)
        return 2
    if a.close:
        if changes.get("address"):
            set_cycle(s, a.project, {"address": changes["address"]})
        row = close_cycle(s, a.project, a.close, metric=a.metric or "", findings=findings, pr=changes.get("pr"),
                          item=changes.get("item"))
        print(f"lane-improve: {a.project}: cycle closed, {a.close}: {row['item'] or '(no item)'}"
              + (f" (PR #{row['pr']})" if row["pr"] else "") + f", {row['rounds']} review round(s); in the ledger")
        return 0
    if not changes and a.budget_min is None:
        rec = cycle(s, a.project)
        print(json.dumps(rec, sort_keys=True) if a.json else cycle_text(a.project, rec))
        return 0
    if changes.get("phase") == "scout" and cycle(s, a.project).get("phase") != "scout":
        # a new cycle: refused once the project's cycles have ended (a cap, `off`, the end time); a lane already out finishes
        stop = ("the period is draining (`lane-improve off` or its end time)" if mode == "draining"
                else hard_stop(s, a.project) or (("a cap is reached (" + caps(s, a.project)[1] + ")") if caps(s, a.project)[1] else None))
        if stop:
            print(f"lane-improve: no new cycle for {a.project}: {stop}; wrap up (the away-improve skill, section 3)",
                  file=sys.stderr)
            return 1
    rec = set_cycle(s, a.project, changes, a.budget_min)
    print(json.dumps(rec, sort_keys=True) if a.json else "lane-improve: " + cycle_text(a.project, rec))
    return 0


def status_rows() -> list:
    """One record per project for `status --json` (field report B5): this machine's coordinators and the projects the state
    files name. improve: on | wrapped | draining | off, or unknown with `error` saying why (an improve or drain file that
    cannot be used, a period record that cannot be trusted); with the cycle's item, phase, round, PR, deadline and the
    minutes since its last activity (cycle record, last away-merge, period start), and the cap reached, if any."""
    broken = file_problem(path(), False) or file_problem(drain_path(), True)
    s = load(quiet=True)
    d = None if s else (load_drain(quiet=True) or (_to_drain(e) if (e := ended_state()) is not None else None))
    names = [c.name for c in _coordinator_list()]
    for st in (s, d):
        if st:
            names += list(st["branches"]) + ([] if st["projects"] == "all" else list(st["projects"]))
    rows = []
    for p in dict.fromkeys(names):
        row = {"project": p, "improve": "off", "item": None, "phase": None, "round": None, "pr": None, "deadline": None,
               "deadline_passed": False, "last_activity_age_min": None, "cap": None, "branch": None, "error": None,
               "maintenance_merges": 0}
        st = s if s and covers(s, p) else (d if d and p in d["pending"] else None)
        if st is None and broken:
            row.update(improve="unknown", error=broken)
        if st is not None:
            lost = period_problem(st)
            row["improve"] = "draining" if st is d else ("unknown" if lost else "wrapped" if is_wrapped(st, p) else "on")
            row["error"] = lost
            row["branch"] = st["branches"].get(p)
            row["maintenance_merges"] = maintenance_done(st, p)
            rec = cycle(st, p)
            row.update({k: rec.get(k) for k in ("item", "phase", "round", "pr", "deadline")})
            dl = _when(rec.get("deadline"))
            row["deadline_passed"] = bool(dl and dl <= now() and rec.get("phase") in LANE_PHASES)
            row["last_activity_age_min"] = int((now() - last_activity(st, p)).total_seconds() // 60)
            if row["improve"] == "on" and not period_problem(st):
                row["cap"] = caps(st, p)[1]
        rows.append(row)
    return rows


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


START_PREFIX = "away: start "  # the subject of the one commit `on` adds to a new away branch (the start commit)
TAKE_IN_PREFIX = "ratchet: take in "  # a maintenance merge's take-in (core/maintenance.py): no item either


def _unworked(repo: Path, branch: str, integration: str) -> bool:
    """Whether the branch holds at most the start commit `on` made: no item was ever merged into it."""
    r = _git(repo, "log", "--format=%s", f"refs/remotes/origin/{integration}..refs/remotes/origin/{branch}")
    return r.returncode == 0 and all(l.startswith((START_PREFIX, TAKE_IN_PREFIX)) for l in r.stdout.splitlines())


def _has_one_already(repo: Path, have: list, integration: str) -> str:
    b = have[0]
    merged = (f" It is already merged into {integration} (or was never given any work): deleting it is the usual step, "
              "since continuing it builds on the old base."
              if _already_merged(repo, b, integration) or _unworked(repo, b, integration) else "")
    return (f"it already has its away branch {b}: a project has at most one, ever. Continue it (`--continue`), or adopt it "
            f"(merge it to {integration}) or drop it (delete it) first.{merged}")


def _start_commit(repo: Path, integration: str, today: dt.date) -> str:
    """An EMPTY commit on top of origin/<integration>, `away: start <today>`: same tree as its parent, built with plumbing
    (the checkout, its index and its branches are not touched). It gives the branch one commit ahead of the integration
    branch, which GitHub needs to open the draft summary PR at `on`; it adds no file (the log of each item lives in its PR
    body and merge commit). Authored as the repo's configured git identity, no attribution line. RuntimeError says why not."""
    base = f"refs/remotes/origin/{integration}"
    cmd = ["git", "-C", str(repo), "commit-tree", f"{base}^{{tree}}", "-p", base, "-m", f"{START_PREFIX}{today.isoformat()}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise RuntimeError((r.stderr.strip() or "no output").splitlines()[-1])
    return r.stdout.strip()


def cut_branch(repo: Path, integration: str, today: dt.date, project: str = "", warns: "list | None" = None) -> str:
    """Push a new away/<today> on origin: origin/<integration>'s head plus one empty commit, `away: start <today>` (so the
    summary PR can be opened from the start); the checkout and its branches are not touched. When that commit cannot be made
    (no git identity) the branch is cut from the head alone and a warning goes into `warns`. A project has at most one away
    branch, ever: ValueError when it has one (or when this very name is taken). Returns the name."""
    have = _away_branches(repo)
    if have:
        raise ValueError(_has_one_already(repo, have, integration) if len(have) == 1 else _several(have))
    name = f"away/{today.isoformat()}"
    tip = f"refs/remotes/origin/{integration}"
    try:
        tip = _start_commit(repo, integration, today)
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        if warns is not None:
            warns.append(f"lane-improve: {project or repo.name}: the start commit was not made ({e}); the branch is cut from "
                         f"{integration}'s head alone, so the draft summary PR cannot open until the first item is merged "
                         "(away-merge opens it). A git identity for the checkout (user.name, user.email) is what a start "
                         "commit needs")
    _git_ok(repo, "push", "-q", "origin", f"{tip}:refs/heads/{name}")
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


def resolve_branches(cos: list, cont: bool, today: dt.date, warns: "list | None" = None) -> "tuple[dict, dict, dict]":
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
            out[c.name], how[c.name] = cut_branch(c.checkout, integ, today, c.name, warns), "cut now"
        except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            raise ValueError(f"{c.name}: {e}") from None
    return out, integs, how


# --- the draft summary PR: away/<date> -> the integration branch -------------------------------------------------------
# Its body is built from the branch's merge commits (each carries its item's log entry, copied from the lane's PR body by
# away-merge) plus a short summary the coordinator writes (`lane-improve pr <project> --summary-file <path>`). No file is
# kept on the branch.

GH_WRITE_TIMEOUT = 60
BODY_LIMIT = 60000        # GitHub refuses a PR body of more than 65536 characters; this keeps a margin
SUMMARY_LIMIT = 8000      # the coordinator's summary is short; a longer one is refused, not cut
# The generated regions of the body sit between markers. A start marker carries a digest of what lane-improve wrote between
# them (`<!-- away-items sha256=... -->`), so a refresh can tell that someone edited inside a region.
SUMMARY_START = "<!-- away-summary"   # the start of the marker; the full one is `<!-- away-summary sha256=<16 hex> -->`
SUMMARY_END = "<!-- /away-summary -->"
ITEMS_START = "<!-- away-items"
ITEMS_END = "<!-- /away-items -->"
_ANY_MARKER = re.compile(r"<!--[ \t]*/?away-(?:summary|items)\b[^>]*(?:-->)?")
NO_SUMMARY = ("No summary yet: the coordinator writes one at wrap-up (`lane-improve pr <project> --summary-file <path>`).")
# The headings every item PR's body must have; away-merge copies them into the merge commit's message.
ENTRY_HEADINGS = ("What", "Why", "Builds on", "Existing work checked", "How to try it", "How to drop it", "Tests")


class PrError(Exception):
    """The summary PR could not be opened, read or refreshed (the message says why)."""


def pr_title(project: str, branch: str) -> str:
    return f"away: {branch.split('/', 1)[-1]} ({project})"


def _heading(line: str) -> "tuple[int, str] | None":
    """(level, text) of a markdown heading line, the text lowercased without markup or a trailing colon; None for any other line."""
    m = re.match(r"^(#{1,6})[ \t]+(.*?)[ \t#]*$", line)
    if not m:
        return None
    return len(m.group(1)), re.sub(r"[*_`]", "", m.group(2)).strip().rstrip(":").strip().lower()


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_TAGS = {"a", "b", "br", "code", "details", "div", "em", "hr", "i", "img", "kbd", "li", "ol", "p", "pre", "span", "strong",
         "sub", "summary", "sup", "table", "td", "th", "tr", "ul", "h1", "h2", "h3", "h4", "h5", "h6"}


def _placeholder(line: str) -> bool:
    """A line that is only a `<...>` placeholder of the entry template (several words in angle brackets, not an HTML tag)."""
    t = line.strip()
    m = re.match(r"^<([A-Za-z][\w-]*)[ ,;:].*>$", t)
    # several words: a line that is only a link in angle brackets (`<https://...>`, `<name@host>`) is content
    return bool(m) and m.group(1).lower() not in _TAGS and bool(re.search(r"\s", t))


def _fence_close(open_fence: str, line: str) -> bool:
    m = _FENCE.match(line)
    return bool(m) and m.group(1)[0] == open_fence[0] and len(m.group(1)) >= len(open_fence) and not line.strip().strip(open_fence[0]).strip()


def entry_problems(body: "str | None") -> "tuple[str, list, list]":
    """(entry, absent, empty) for an item PR's body. The log entry is the sections whose headings are ENTRY_HEADINGS (a `#`
    heading at the start of a line, any level, any case, an optional colon). Headings inside a code fence are not headings;
    HTML comments and `<...>` placeholder lines (the unfilled template) count as nothing. `entry` is the sections that have
    text, in canonical order under `## <Name>` headings, with every code fence closed; `absent` lists the headings with no
    section, `empty` those whose section has no text. A section ends at the next heading of its own level or above (outside
    a fence); deeper headings stay inside it."""
    wanted = {h.lower(): h for h in ENTRY_HEADINGS}
    found, cur, level, fence, in_comment = {}, None, 0, None, False
    for line in (body or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").split("\n"):
        if fence:
            if _fence_close(fence, line):
                fence = None
            if cur:
                found[cur].append(line)
            continue
        m = _FENCE.match(line)
        if m and not in_comment:
            fence = m.group(1)
            if cur:
                found[cur].append(line)
            continue
        if in_comment:
            if "-->" not in line:
                continue
            line, in_comment = line.split("-->", 1)[1], False
        line = re.sub(r"<!--.*?-->", "", line)
        if "<!--" in line:
            line, in_comment = line.split("<!--", 1)[0], True
        h = _heading(line)
        if h and h[1] in wanted:
            cur, level = wanted[h[1]], h[0]
            found.setdefault(cur, [])
            continue
        if h and (cur is None or h[0] <= level):
            cur = None
            continue
        if cur and not _placeholder(line):
            found[cur].append(line)
    parts, absent, empty = [], [], []
    for name in ENTRY_HEADINGS:
        if name not in found:
            absent.append(name)
        elif not "\n".join(found[name]).strip():
            empty.append(name)
    for name in ENTRY_HEADINGS:
        text = re.sub(r"\A(?:[ \t]*\n)+", "", "\n".join(found.get(name, []))).rstrip()  # blank lines go, indentation stays
        if text.strip():
            if _open_fence(text):
                text += "\n" + _open_fence(text)
            parts.append(f"## {name}\n{text}")
    return "\n\n".join(parts), absent, empty


def _open_fence(text: str) -> str:
    """The fence that is still open at the end of `text` ("" when every fence is closed)."""
    fence = None
    for line in text.split("\n"):
        if fence:
            if _fence_close(fence, line):
                fence = None
        else:
            m = _FENCE.match(line)
            if m:
                fence = m.group(1)
    return fence or ""


def parse_entry(body: "str | None") -> "tuple[str, list]":
    """(entry, missing): entry_problems with the absent and the empty headings together, in the order of ENTRY_HEADINGS."""
    entry, absent, empty = entry_problems(body)
    return entry, [h for h in ENTRY_HEADINGS if h in absent or h in empty]


def _neutral(text: str) -> str:
    """Text that came from a PR (a title, an entry) with the body's own markers made harmless: an entry's text never counts as
    a marker."""
    return text.replace("<!--", "&lt;!--")


def generated_parts(project: str, branch: str, integration: str) -> "tuple[str, str]":
    """(head, foot): the fixed text before and after the generated regions of a summary PR's body."""
    head = (f"Away-improve branch for {project}: `{branch}` into `{integration}`. A draft, never merged by an agent: merge "
            f"this PR yourself to adopt the whole branch, or name the items to drop first.")
    foot = ("Each item's log entry is in its merge commit message on the branch (and in its PR's body). This text is "
            "refreshed after every `away-merge` from the branch's merges, read from git, and keeps the coordinator's summary.")
    return head, foot


class BodyKept(PrError):
    """The summary PR's body was changed by hand in a way a refresh would lose: it is left as it is (the message says how to
    fix it). `number` and `url` are the PR's."""

    def __init__(self, msg: str, number: "int | None" = None, url: str = ""):
        super().__init__(msg)
        self.number, self.url = number, url


def _digest(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()[:16]


def _start(prefix: str, region: str) -> str:
    """A start marker holding the digest of `region`, the text written between it and its end marker."""
    return f"{prefix} sha256={_digest(region)} -->"


def read_body(body: "str | None", project: str, branch: str, integration: str) -> "tuple[str | None, str | None]":
    """(summary, problem) from a summary PR's current body. An empty body is fine (nothing there to lose). Otherwise it must be
    the body this module writes: each marker once, in order, nothing but the fixed head and foot outside the regions, and each
    region still what was last written (its digest, in its start marker, matches): text added or changed anywhere would be
    overwritten by the next refresh, so the body is then not to be rewritten, and `problem` says what and how to go on. Line
    endings are normalised first: a browser saves CRLF."""
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return None, None
    starts = [re.search(re.escape(SUMMARY_START) + r" sha256=([0-9a-f]{16}) -->", text),
              re.search(re.escape(ITEMS_START) + r" sha256=([0-9a-f]{16}) -->", text)]
    ends = [text.find(SUMMARY_END), text.find(ITEMS_END)]
    if (any(m is None for m in starts) or text.count(SUMMARY_START) != 1 or text.count(ITEMS_START) != 1
            or text.count(SUMMARY_END) != 1 or text.count(ITEMS_END) != 1
            or not (starts[0].start() < ends[0] < starts[1].start() < ends[1])):
        return None, ("its markers are missing, repeated or out of order (a body from before the log moved into PRs, or one "
                      "edited by hand)")
    head, foot = generated_parts(project, branch, integration)
    outside = (text[:starts[0].start()], text[ends[0] + len(SUMMARY_END):starts[1].start()], text[ends[1] + len(ITEMS_END):])
    norm = lambda t: " ".join(t.split())  # noqa: E731
    if norm(outside[0]) != norm(head) or norm(outside[1]) or norm(outside[2]) != norm(foot):
        return None, "it has text outside the generated parts"
    summary = text[starts[0].end():ends[0]].strip()
    items_region = text[starts[1].end():ends[1]]
    for what, region, m in (("summary", summary, starts[0]), ("items", items_region, starts[1])):
        if _digest(region) != m.group(1):
            return None, (f"its {what} region was edited since lane-improve wrote it, and a refresh would overwrite the edit")
    if not summary or summary == NO_SUMMARY.replace("<project>", project):
        return None, None
    return summary, None


def existing_summary(body: "str | None") -> "str | None":
    """The coordinator's summary as it stands in a summary PR's body (between the markers; line endings normalised), or None."""
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    m = re.search(re.escape(SUMMARY_START) + r"[^>]*-->\n(.*?)\n" + re.escape(SUMMARY_END), text, re.S)
    got = (m.group(1).strip() or None) if m else None
    return None if got and got.startswith("No summary yet:") else got


def strip_markers(text: str) -> str:
    """`text` without any of this body's markers (a summary or an entry that holds one must not become one)."""
    return _ANY_MARKER.sub("", text)


def pr_body(project: str, branch: str, integration: str, summary: "str | None", items: "list | None",
            maint: "list | None" = None) -> str:
    """The summary PR's text: the coordinator's summary (between markers), the merged items (`merged_items`: a list, or None
    when git could not be read), the maintenance merges into the integration branch (`maintenance.items`, their own section,
    shown when there is one) and each item's log entry (between markers). Never longer than BODY_LIMIT: entries are
    shortened first (and it says so), then the list is cut."""
    summary = strip_markers(summary or "").strip() or NO_SUMMARY.replace("<project>", project)
    head, foot = generated_parts(project, branch, integration)
    top = f"{head}\n\n{_start(SUMMARY_START, summary)}\n{summary}\n{SUMMARY_END}\n\n"
    bottom = f"\n{ITEMS_END}\n\n{foot}\n"
    room = BODY_LIMIT - len(top) - len(bottom) - len(_start(ITEMS_START, "")) - 1

    def whole(inner: str) -> str:
        return f"{top}{_start(ITEMS_START, inner)}\n{inner}{bottom}"

    listing = items_text(items) + maintenance_text(maint, integration)
    entries = [(t, e) for t, _pr, _sha, _rev, e in items or [] if e]
    for cap in (None, 700, 200, 0):
        text = _entries_text(entries, cap)
        notice = ""
        if entries and cap is not None:
            notice = (f"\n\nThe entries are {'left out' if cap == 0 else 'shortened (to their first lines)'} to fit GitHub's "
                      "65536-character limit on a PR body; the whole entry is in each merge commit message (`git show <sha>`) "
                      "and in the item's PR.")
        inner = listing + notice + (("\n\n" + text) if text else "")
        if len(inner) <= room:
            return whole(inner)
    cut = ("\n\n(The list is cut here to fit GitHub's 65536-character limit on a PR body: the rest of the items are the "
           "branch's merge commits, `git log --first-parent --merges`.)")
    return whole(listing[:max(room - len(cut), 0)].rstrip() + cut)


def _cut(text: str, cap: int) -> str:
    """`text` cut to about `cap` characters at a line end, else at a word end; every code fence it opened is closed."""
    if len(text) <= cap:
        return text
    head = text[:cap]
    by_line = head.rsplit("\n", 1)[0] if "\n" in head else ""
    if len(by_line) >= cap // 2:  # a line end that keeps at least half of what fits; else a long line is cut at a word
        head = by_line
    elif " " in head:
        head = head.rsplit(" ", 1)[0]
    head = head.rstrip()
    fence = _open_fence(head)
    return head + (f"\n{fence}" if fence else "") + "\n\n(shortened)"


def _demote(text: str) -> str:
    """The entry's `## ` headings one outline level down (`#### `), outside code fences."""
    out, fence = [], None
    for line in text.split("\n"):
        if fence:
            if _fence_close(fence, line):
                fence = None
        else:
            m = _FENCE.match(line)
            if m:
                fence = m.group(1)
            elif line.startswith("## "):
                line = "#### " + line[3:]
        out.append(line)
    return "\n".join(out)


def _entries_text(entries: list, cap: "int | None") -> str:
    """The items' entries as collapsed blocks, each cut to `cap` characters when `cap` is given; cap 0 leaves them out."""
    if cap == 0 or not entries:
        return ""
    out = []
    for title, entry in entries:
        text = _demote(entry.strip())
        if cap is not None:
            text = _cut(text, cap)
        out.append(f"<details>\n<summary>{html.escape(title)}</summary>\n\n{_neutral(text)}\n\n</details>")
    return "\n\n".join(out)


def items_text(items: "list | None") -> str:
    """The "Items merged" part of the PR body: one line per item with its revert command (oldest merge first)."""
    if items is None:
        return "Items merged: could not be read from git just now. The next `away-merge` tries again."
    if not items:
        return "Items merged: none yet."
    lines = ["Items merged, oldest first. To drop one, run its command on the away branch and push, or name it to the "
             "coordinator (latest first if later items build on it):"]
    for title, pr, sha, reverted, _entry in items:
        what = "already reverted" if reverted else f"`git revert -m 1 {sha}`"
        lines.append(f"- {_neutral(title)} ({f'PR #{pr}, ' if pr else ''}{sha}): {what}")
    return "\n".join(lines)


def maintenance_text(maint: "list | None", integration: str) -> str:
    """The "Maintenance merges" section: one line per maintenance merge into the integration branch, with its PR link (#N) and
    sha; "" when there is none (or git could not be read)."""
    if not maint:
        return ""
    lines = [f"\n\nMaintenance merges into {integration} (docs and tests only, merged by `away-merge --maintenance` and already "
             f"on {integration}; the away branch took each in. To undo one, revert it on {integration}):"]
    for title, pr, sha in maint:
        lines.append(f"- {_neutral(title)} ({f'PR #{pr}, ' if pr else ''}{sha}): `git revert -m 1 {sha}` on {integration}")
    return "\n".join(lines)


def merged_items(repo: Path, branch: str, integration: str) -> "list | None":
    """The items merged into origin's `branch` so far, oldest first, as (title, PR number or None, short sha, reverted,
    entry): the first-parent merge commits on the branch that the integration branch does not have, whose subject is
    `away: <title>` (the start commit has one parent, so it is not a merge). The PR number is the `Pull request #<n>` line
    `away-merge` puts in the merge message (None for a merge made without it); `entry` is the rest of the message body (the
    item's log entry, "" when there is none). `reverted` is true when a commit on the branch says `This reverts commit
    <the merge's sha>` (a plain `git revert`, alone or inside a merged lane PR). Reading git, not the period record, keeps
    this right across periods and after a lost record. None when git cannot be read."""
    ref, base = f"refs/remotes/origin/{branch}", f"refs/remotes/origin/{integration}"
    try:
        if _git(repo, "fetch", "-q", "origin", f"+refs/heads/{branch}:{ref}", f"+refs/heads/{integration}:{base}").returncode:
            return None
        r = _git(repo, "log", "--first-parent", "--merges", "--reverse", "--format=%H%x1f%h%x1f%s%x1f%b%x1e", ref, "--not", base)
        every = _git(repo, "log", "--format=%B%x1e", ref, "--not", base)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode or every.returncode:
        return None
    undone = set(re.findall(r"^This reverts commit ([0-9a-f]{40})", every.stdout, re.M))
    items = []
    for rec in r.stdout.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) != 4 or not parts[2].startswith("away: "):
            continue
        m = re.search(r"^Pull request #(\d+)[ \t]*$", parts[3], re.M)
        entry = (parts[3][m.end():] if m else parts[3]).strip()
        items.append((parts[2][len("away: "):].strip(), int(m.group(1)) if m else None, parts[1].strip(),
                      parts[0].strip() in undone, entry))
    return items


def _gh(repo: Path, *args: str) -> str:
    try:
        r = subprocess.run(["gh", *args], cwd=repo, capture_output=True, text=True, timeout=GH_WRITE_TIMEOUT)
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        raise PrError(f"gh {args[0]} {args[1]} failed ({e})") from None
    if r.returncode:
        raise PrError(f"gh {args[0]} {args[1]} failed ({(r.stderr.strip() or r.stdout.strip() or 'no output').splitlines()[-1]})")
    return r.stdout


def _gh_body(repo: Path, args: list, body: str) -> str:
    """`gh <args> --body-file <file holding body>`: a body goes through a file, never argv (a long or non-ASCII body would hit
    the argument size limit)."""
    with tempfile.NamedTemporaryFile("w", prefix="away-body-", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(body.replace("\x00", ""))
    try:
        return _gh(repo, *args, "--body-file", fh.name)
    finally:
        Path(fh.name).unlink(missing_ok=True)


def _gh_json(repo: Path, kind: type, *args: str):
    """gh's answer as JSON of the expected kind (a list or a dict); PrError otherwise, so a changed gh never crashes a caller."""
    try:
        v = json.loads(_gh(repo, *args) or "[]")
    except ValueError:
        raise PrError(f"gh {args[0]} {args[1]} gave an answer that is not JSON") from None
    if not isinstance(v, kind):
        raise PrError(f"gh {args[0]} {args[1]} gave an answer of an unexpected shape")
    return v


class ClosedPr(PrError):
    """The recorded summary PR was closed or merged: it is left alone (the owner's call)."""


def sync_summary_pr(repo: Path, project: str, branch: str, integration: str, number: "int | None",
                    summary: "str | None" = None) -> "tuple[int, str, str]":
    """(number, url, what) of the project's draft summary PR with its body rebuilt from the branch's merge commits and the
    coordinator's summary: `summary` when given, else the one already in the PR's body (a refresh keeps it). With a
    recorded `number` that PR is read, checked to be this branch's own (same repository, head `branch`, base the integration
    branch: a number that points elsewhere is never edited) and edited; a closed or merged one is left alone (ClosedPr, the
    owner's call). Without a number the open PR of the branch is looked for, and only a PR from this repository into the
    integration branch counts (`gh pr list --head` matches the branch NAME, forks' PRs included), else a draft one is
    opened. The only gh writes in the whole mode besides away-merge's merge: `pr create --draft` and the body (edit_body:
    a REST PATCH, `pr edit --body-file` when that fails).
    PrError says why it could not be done."""
    items = merged_items(repo, branch, integration)
    try:
        import maintenance
        maint = maintenance.items(repo, integration, branch) if items is not None else None
    except Exception:  # the summary never fails for the maintenance section
        maint = None
    old_body = None
    if number is None:
        found = _gh_json(repo, list, "pr", "list", "--head", branch, "--state", "open", "--json",
                         "number,url,isCrossRepository,baseRefName,isDraft,body")
        mine = [x for x in found if isinstance(x, dict) and x.get("isCrossRepository") is False
                and x.get("baseRefName") == integration and isinstance(x.get("number"), int)]
        if mine:
            number, url, what = mine[0]["number"], str(mine[0].get("url", "")), "refreshed"
            old_body = mine[0].get("body")
        else:
            body = pr_body(project, branch, integration, summary, items, maint)
            url = _gh_body(repo, ["pr", "create", "--draft", "--base", integration, "--head", branch, "--title",
                                  pr_title(project, branch)], body).strip().splitlines()[-1:] or [""]
            url = url[0]
            m = re.search(r"/pull/(\d+)\s*$", url)
            if not m:
                raise PrError(f"gh pr create did not print a pull request link ({url[:80]})")
            return int(m.group(1)), url, "opened"
    else:
        info = _gh_json(repo, dict, "pr", "view", str(number), "--json", "state,url,headRefName,baseRefName,isCrossRepository,body")
        if info.get("headRefName") != branch or info.get("baseRefName") != integration or info.get("isCrossRepository") is not False:
            raise PrError(f"PR #{number} is not {project}'s summary PR ({info.get('headRefName')} into {info.get('baseRefName')}"
                          f"{', from a fork' if info.get('isCrossRepository') else ''}, not {branch} into {integration}): it is "
                          "left alone; fix `prs` in the improve file or remove the entry")
        if str(info.get("state", "")).upper() != "OPEN":
            raise ClosedPr(f"summary PR #{number} is {str(info.get('state')).lower()}, so it is left alone: what happens to "
                           "it is the owner's call")
        url, what = str(info.get("url", "")), "refreshed"
        old_body = info.get("body")
    kept, problem = read_body(old_body if isinstance(old_body, str) else None, project, branch, integration)
    if problem:
        raise BodyKept(f"summary PR #{number}'s body is left as it is: {problem}. To go on: copy what you want to keep (the "
                       "summary into a file; notes belong in a comment on the PR), clear the PR body, then run "
                       f"`lane-improve pr {project} --summary-file <file>`", number, url)
    summary = summary or kept
    if summary and len(summary) > SUMMARY_LIMIT:
        raise BodyKept(f"summary PR #{number}'s body is left as it is: its summary is {len(summary)} characters, over the "
                       f"limit of {SUMMARY_LIMIT}; shorten it", number, url)
    edit_body(repo, number, pr_body(project, branch, integration, summary, items, maint))
    return number, url, what


def edit_body(repo: Path, number: int, body: str) -> str:
    """Write a PR's body: a REST PATCH of the pull request by default (`gh api --method PATCH repos/{owner}/{repo}/pulls/<n>
    --input <file>`, the body in a JSON file, never argv), because `gh pr edit` also reads the PR's Projects (classic) over
    GraphQL and fails on an organisation that still uses them (field report B6). `gh pr edit --body-file` is the fallback
    when the PATCH fails. Returns "rest" or "pr edit"; PrError names both failures."""
    with tempfile.NamedTemporaryFile("w", prefix="away-body-", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump({"body": body.replace("\x00", "")}, fh)
    try:
        _gh(repo, "api", "--method", "PATCH", f"repos/{{owner}}/{{repo}}/pulls/{number}", "--input", fh.name, "--silent")
        return "rest"
    except PrError as e:
        rest = str(e)
    finally:
        Path(fh.name).unlink(missing_ok=True)
    try:
        _gh_body(repo, ["pr", "edit", str(number)], body)
    except PrError as e:
        raise PrError(f"PR #{number}'s body could not be written: the REST PATCH failed {rest.split(' failed ', 1)[-1]}, "
                      f"and then {e}") from None
    return "pr edit"


def _update(fn) -> bool:
    """Apply `fn` to the state file that holds the period (the improve file, else the drain file) and write it back, under the
    lock (a second writer waits, so a PR number recorded here is never lost to another process's write)."""
    with _locked():
        for f, drain, writer in ((path(), False, write), (drain_path(), True, write_drain)):
            st = _read(f, drain, True)
            if st is not None:
                fn(st)
                writer(st)
                return True
    return False


def record_pr(project: str, number: int) -> bool:
    return _update(lambda st: st.setdefault("prs", {}).__setitem__(project, number))


def sync_and_record(repo: Path, state: dict, project: str, summary: "str | None" = None) -> "tuple[int, str, str]":
    """sync_summary_pr, and the PR's number recorded. Under the improve lock, so a refresh that read the body before
    `lane-improve pr --summary-file` wrote its summary cannot overwrite it."""
    with _locked():
        try:
            n, url, what = sync_summary_pr(repo, project, state["branches"][project],
                                           state["integration"].get(project, "main"), state.get("prs", {}).get(project), summary)
        except BodyKept as e:
            if e.number and state.get("prs", {}).get(project) != e.number:
                record_pr(project, e.number)
            raise
        if state.get("prs", {}).get(project) != n:
            record_pr(project, n)
        return n, url, what


# --- deny rules: a backstop for away-merge while the mode is on ---------------------------------------------------

def install_denies(cos: list, integrations: dict) -> "tuple[list, list]":
    """Ask each coordinator's harness adapter for away-improve deny rules. (notes, warnings): what was done or is
    not possible per harness, and what could not be done (the mode still runs: away-merge is the main guard). A
    harness machine.conf does not give is looked up from Orca; one that cannot be found gets no rules, and says why."""
    import harnesses
    import machineconf
    notes, warns = [], []
    found, problems = machineconf.resolve(cos, titles=False)
    for pr in problems:
        again = " ".join(f"--project {n}" for n in pr.names)
        warns.append(f"lane-improve: deny rules NOT installed ({pr.text}); away-merge is the only guard on its merge "
                     f"route. Start its coordinator, then `lane-improve on --continue {again}`")
    for c in found:
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
    """Take the deny rules out of every coordinator's checkout; returns warnings for what could not be removed. Every
    harness adapter is asked, whatever harness runs there now (it may have changed since the rules went in): each
    removes only what lane-control recorded for that checkout and harness, and has nothing to do without its record."""
    import harnesses
    warns = []
    for c in cos:
        for name in harnesses.harnesses_names():
            try:
                harnesses.load_harness(name).remove_away_denies(c.checkout)
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
    lines.append("  maintenance: " + ("on: a PR that changes only the project's maintenance paths (docs, tests) merges into the "
                                      "integration branch through `away-merge --maintenance`, and the away branch takes it in"
                                      if s.get("maintenance") else "off (every item goes to the away branch)"))
    if cap_text(s):
        lines.append(f"  caps: {cap_text(s)}; the lane that is running finishes, then the project wraps up (`lane-improve status` shows each project's progress)")
    return lines


def maintenance_lines(s: dict, cos: list) -> list:
    """Per project, while maintenance is on: its maintenance globs (and where they come from), its maintenance merges this period,
    and one whose take-in is still to happen (from the record; away-merge checks git)."""
    if not s.get("maintenance"):
        return []
    import maintenance
    out = []
    for c in cos:
        if not covers(s, c.name) or c.name not in s["branches"]:
            continue
        globs, where, why = maintenance.globs_for(c.checkout)
        n = maintenance_done(s, c.name)
        line = (f"  {c.name}: maintenance paths {', '.join(globs)} ({where})" if not why else
                f"  {c.name}: maintenance paths cannot be used ({why}): every maintenance merge is refused")
        line += f"; maintenance merges {n}"
        todo = [m for m in merges_of(s, c.name) if m.get("kind") == "maintenance" and m.get("taken_in") is not True]
        if todo:
            line += (f"; {s['branches'][c.name]} has not taken in #{todo[-1].get('pr')} ({todo[-1].get('take_in') or 'not taken in'})"
                     ": no maintenance merge until it has")
        out.append(line)
    return out


def open_summary_prs(cos: list, branches: dict, integs: dict, known: dict) -> "tuple[dict, list]":
    """({project: PR number}, warnings) for the chosen coordinators' draft summary PRs, found or opened now. A PR gh cannot
    open or read (offline, no login, a changed answer) is a warning, never a refusal, and nothing here may raise: the branches
    are pushed already, and a crash before the state is written would strand them. away-merge opens it after the first merged
    item. A recorded PR the owner closed stays as it is."""
    out, warns = {}, []
    for c in cos:
        try:
            n, _url, _what = sync_summary_pr(c.checkout, c.name, branches[c.name], integs[c.name], known.get(c.name))
            out[c.name] = n
        except ClosedPr as e:
            out[c.name] = known[c.name]
            warns.append(f"lane-improve: {c.name}: {e}")
        except BodyKept as e:
            if e.number:
                out[c.name] = e.number
            warns.append(f"lane-improve: {c.name}: {e}")
        except Exception as e:  # best effort after the real work: any failure is a warning
            warns.append(f"lane-improve: {c.name}: the draft summary PR is not open yet ({e}); away-merge opens it after "
                         f"the first merged item, or run `lane-improve pr {c.name}`")
    return out, warns


def stop_at_refusal(stop_at, names: list) -> "str | None":
    """Why --stop-at cannot be accepted for these projects, or None: it must be below each project's ceiling (new work stops
    at the budget's no-new-work step, a little below it, so a cap at or above the ceiling would never fire), and the ceiling
    must be readable."""
    for name in names:
        weekly, ceiling, why = budget_reading(name)
        if ceiling is None:
            return (f"--stop-at needs {name}'s weekly ceiling, which budget-state could not read ({why}); try again when "
                    "the usage feed answers")
        if stop_at >= ceiling:
            return (f"--stop-at {_pct(stop_at)} is at or above {name}'s ceiling {ceiling}: the budget's no-new-work step, "
                    f"below the ceiling, stops new cycles first, so the cap would never fire; give a value below {ceiling}")
    return None


def _unfinished_end() -> bool:
    """A time end that passed with something still to wrap up. One with nothing left (every project wrapped up at its cap)
    is cleared here."""
    e = ended_state()
    if e is None:
        return False
    if _to_drain(e)["pending"]:
        return True
    clear()
    return False


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
        if a.maintenance and a.no_maintenance:
            raise ValueError("give one of --maintenance, --no-maintenance")
        stop_at = parse_cap("--stop-at", a.stop_at, False)
        max_items = parse_cap("--max-items", a.max_items, True)
        max_hours = parse_hours(a.max_hours)
    except ValueError as e:
        print(f"lane-improve: {e}", file=sys.stderr)
        return 2
    if not a.yes and not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("lane-improve: improve mode spends tokens until it is switched off; there is no terminal here (Claude Code's "
              "`!` prefix has none either), so it needs --yes: the owner adds it under `!` (`! lane-improve on --yes ...`); "
              "an agent adds it only on the owner's own explicit word", file=sys.stderr)
        return 2
    prev = load()
    if prev:
        if not a.cont:
            print("lane-improve: improve mode is already on; switching it on again would cut new away branches and leave "
                  "the open PRs without a home. Use `lane-improve off` first, or `--continue` to carry on with the "
                  "current branches", file=sys.stderr)
            return 1
    elif load_drain(quiet=True) or _unfinished_end():
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
    if stop_at is not CLEAR and (stop_at is not None or (prev or {}).get("stop_at") is not None) and (
            a.unmetered or (prev or {}).get("unmetered")):
        print("lane-improve: --stop-at reads the usage budget, and an --unmetered period has none: give one of them "
              "(--stop-at none drops the cap)", file=sys.stderr)
        return 2
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
    if prev:  # what a re-run leaves as it was, and what it changes
        eff_stop = prev.get("stop_at") if stop_at is None else (None if stop_at is CLEAR else stop_at)
        eff_max = prev.get("max_items") if max_items is None else (None if max_items is CLEAR else max_items)
        eff_hours = prev.get("max_hours") if max_hours is None else (None if max_hours is CLEAR else max_hours)
        keep = prev["projects"] == "all" or projects == "all"
        period_projects = "all" if keep else list(dict.fromkeys([*prev["projects"], *projects]))
    else:
        eff_stop = None if stop_at in (None, CLEAR) else stop_at
        eff_max = None if max_items in (None, CLEAR) else max_items
        eff_hours = None if max_hours in (None, CLEAR) else max_hours
        period_projects = projects
    # a project that wrapped up at its cap stays wrapped up (a wrap-up is final for the period) unless the owner names it
    # again with the cap raised: then it resumes. Named with the cap still reached, it is refused, before anything is cut.
    resumes, still_wrapped = [], []
    if prev:
        wrapped_now = [p for p in prev["branches"] if covers(prev, p) and is_wrapped(prev, p)]
        later = {**prev, "stop_at": eff_stop, "max_items": eff_max, "max_hours": eff_hours}
        for p in wrapped_now:
            if p in [c.name for c in chosen]:
                hit = caps(later, p)[1]
                if hit:
                    print(f"lane-improve: improve not switched on: {p} wrapped up at its cap and the cap is still reached "
                          f"({hit}): raise it (--max-items or --stop-at) to resume {p}, or leave {p} wrapped up",
                          file=sys.stderr)
                    return 1
                resumes.append(p)
            else:
                still_wrapped.append(p)
    # --unmetered takes the budget steps away, never the wall clock: every run that leaves the period unmetered with no end
    # and no --max-hours gives it the default, unless the owner dropped it by name (`--max-hours none`), which is kept as
    # max_hours_off so a later re-run does not put it back; a value given later clears that.
    hours_default = ""
    hours_off = max_hours is CLEAR or (max_hours is None and bool((prev or {}).get("max_hours_off")))
    eff_unmetered = bool(a.unmetered) or bool((prev or {}).get("unmetered"))
    eff_end = end or (prev or {}).get("end") or UNTIL_BACK
    if eff_unmetered and eff_end == UNTIL_BACK and eff_hours is None and not hours_off:
        eff_hours = UNMETERED_DEFAULT_HOURS
        hours_default = (f"  wall clock: --unmetered has no budget steps, so a bound of {UNMETERED_DEFAULT_HOURS} h applies "
                         "(--max-hours; give another value, or `--max-hours none` to run until `lane-improve off`)")
    cut_warns: list = []
    added = [c.name for c in chosen if prev is None or c.name not in prev["branches"]]
    if eff_stop is not None and (stop_at is not None or added):
        names = ([c.name for c in cos] if period_projects == "all" else list(period_projects)) if stop_at is not None else added
        why = stop_at_refusal(eff_stop, names)
        if why:
            print(f"lane-improve: improve not switched on: {why}", file=sys.stderr)
            return 1
    try:
        branches, integs, how = resolve_branches(chosen, a.cont, now().date(), cut_warns)
    except ValueError as e:
        print(f"lane-improve: improve not switched on: {e}", file=sys.stderr)
        return 1
    deny = None if a.deny_paths is None else [g.strip() for g in a.deny_paths.split(",") if g.strip()]
    prs, pr_warns = open_summary_prs(chosen, branches, integs, (prev or {}).get("prs", {}))
    if prev:  # a `--continue` re-run while on: what is not named or not given again stays as it was
        state = {
            "started": prev["started"],
            "end": end or prev["end"],
            "limits": prev["limits"] if a.limits is None else a.limits,
            "deny_paths": prev["deny_paths"] if deny is None else deny,
            "projects": period_projects,
            "unmetered": bool(a.unmetered) or prev["unmetered"],
            "branches": {**prev["branches"], **branches},
            "integration": {**prev["integration"], **integs},
            "prs": {**prev.get("prs", {}), **prs},
            "stop_at": eff_stop,
            "max_items": eff_max,
            "max_hours": eff_hours,
            "max_hours_off": bool(hours_off and eff_hours is None),
            "maintenance": False if a.no_maintenance else bool(a.maintenance) or bool(prev.get("maintenance")),
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
            "stop_at": eff_stop,
            "max_items": eff_max,
            "max_hours": eff_hours,
            "max_hours_off": bool(hours_off and eff_hours is None),
            "maintenance": bool(a.maintenance),
        }
    lost = period_problem(state) if prev else None  # the period's record cannot be trusted: this run makes it again
    write(state)
    if not prev or lost:
        start_period(state)  # a new period counts from nothing
    elif resumes:
        unmark_wrapped(state, resumes)
    clear_rest()  # a new instruction from the owner: every coordinator looks for work again
    targets, tinteg = chosen, integs
    if lost:  # nothing is known about who wrapped up: every project the period covers is active again, rules included
        targets = [c for c in cos if covers(state, c.name) and c.name in state["integration"]]
        tinteg = state["integration"]
    notes, warns = install_denies(targets, tinteg)
    print("lane-improve: improve mode on")
    print("\n".join(describe(state, how) + maintenance_lines(state, cos) + notes))
    if hours_default:
        print(hours_default)
    for p in resumes:
        print(f"lane-improve: {p} resumes (its cap is no longer reached; its deny rules are back)")
    if still_wrapped:
        print(f"  still wrapped up: {', '.join(still_wrapped)} (a wrap-up is final for the period; raise a cap and name the "
              f"project, `lane-improve on --continue --project {still_wrapped[0]} --max-items <more>`, to resume it)")
    if lost:
        warns.append(f"lane-improve: the period record was unusable ({lost}); it is made again from nothing, so merge counts "
                     "are 0 and no project counts as wrapped up (their deny rules are back)")
    for w in [*warns, *cut_warns, *pr_warns]:
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
    done = (_read_period(state)[0] or {"wrapped": []})["wrapped"]  # an unreadable record: everyone is asked to wrap up
    names = [n for n in names if n not in done]  # a project that wrapped up at a cap has nothing to wrap up
    ended = state["end"] if state["end"] != UNTIL_BACK else now().isoformat(timespec="seconds")
    if state["end"] != UNTIL_BACK and not _ended(state):
        ended = now().isoformat(timespec="seconds")
    return {**state, "ended": ended, "pending": names}


def cmd_off(a) -> int:
    """End the mode. What was running is left to drain: nothing starts, the last PRs can still merge, the deny rules stay
    until `drained`. With nothing to drain, leftover deny rules are cleaned up."""
    state = load() or ended_state()
    if state is not None:
        d = _to_drain(state)
        if d["pending"]:
            write_drain(d)
        clear()
        clear_rest()
        if not d["pending"]:  # everything wrapped up at its cap: no `drained` will follow, so no rule may be left behind
            for w in remove_denies(_coordinator_list()):
                print(w, file=sys.stderr)
        if d["pending"]:
            print("lane-improve: improve mode ended; the running lanes drain: each coordinator merges its last PR with "
                  "away-merge, writes its summary (`lane-improve pr <project> --summary-file <path>`) and points you at it, then runs "
                  "`lane-improve drained <project>` (`lane-improve status` lists them)")
        else:
            print("lane-improve: improve mode ended; every project had already wrapped up at its cap, so nothing is left to drain")
    elif load_drain(quiet=True) is None:
        for w in remove_denies(_coordinator_list()):
            print(w, file=sys.stderr)
    return 0


def cmd_drained(a) -> int:
    """`drained <project>...` or `--all`: the project's wrap-up is done; its deny rules come out and the state clears. While the
    mode is still on, only a project that has reached a cap can be wrapped up this way (the mode stays on for the rest)."""
    on_state = load(quiet=True)
    if on_state is not None:
        return _drained_capped(on_state, a)
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


def read_summary_file(ref: str) -> str:
    """The coordinator's summary from a file kept outside the project; ValueError says why it cannot be used."""
    f = Path(ref)
    try:
        text = f.read_text().strip()
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"--summary-file {ref} cannot be read ({getattr(e, 'strerror', None) or e})") from None
    if not text:
        raise ValueError(f"--summary-file {ref} is empty")
    if len(text) > SUMMARY_LIMIT:
        raise ValueError(f"--summary-file {ref} is {len(text)} characters; the summary is short (at most {SUMMARY_LIMIT}): "
                         "the items' entries are listed below it already")
    return strip_markers(text)


def cmd_pr(a) -> int:
    """`pr <project> [--summary-file PATH]`: open the project's draft summary PR if it has none, and rebuild its body from the
    branch's merge commits and the summary (away-merge does this after every merge, keeping the summary; this is the hand for
    a PR that could not be opened or refreshed, and the coordinator's wrap-up step, which also sets the summary). Works while
    the mode is on, after a cap is reached (that is when the wrap-up runs it) and while draining. Prints the link. Never
    merges, never marks it ready, never edits a file in the project."""
    s, mode = mode_state(a.project)
    if s is None:
        print(f"lane-improve: {mode}", file=sys.stderr)
        return 1
    if a.project not in s["branches"]:
        print(f"lane-improve: {a.project} has no away branch recorded (`lane-improve on --continue --project {a.project}`)",
              file=sys.stderr)
        return 1
    summary = None
    if a.summary_file:
        try:
            summary = read_summary_file(a.summary_file)
        except ValueError as e:
            print(f"lane-improve: {e}", file=sys.stderr)
            return 1
    cos = {c.name: c for c in _coordinator_list()}
    if a.project not in cos:
        print(f"lane-improve: {a.project} is not a coordinator in machine.conf", file=sys.stderr)
        return 1
    if summary:  # the summary goes into a public PR: the same secret and names scans as a merge's diff
        import textscan
        published = textscan.as_diff({"summary": summary})
        hit = textscan.secret_scan(cos[a.project].checkout, published) or textscan.name_check(
            a.project, cos, published)[0]
        if hit:
            print(f"lane-improve: --summary-file {a.summary_file} is refused, nothing was changed: "
                  + hit.replace("the diff adds", "the summary adds"), file=sys.stderr)
            return 1
    try:
        n, url, what = sync_and_record(cos[a.project].checkout, s, a.project, summary)
    except Exception as e:  # PrError, or a changed gh/git answer: say it, never a traceback
        print(f"lane-improve: {a.project}: {e}", file=sys.stderr)
        return 1
    print(f"lane-improve: summary PR #{n} of {a.project} {what} (a draft; only the owner merges it): {url}")
    return 0


def _drained_capped(s: dict, a) -> int:
    """`drained` while the mode is on: the projects that have reached a cap wrap up now (their deny rules come out, nothing
    more merges), and the mode goes on for the others."""
    why = period_problem(s)
    if why:
        print(f"lane-improve: {why}, so nothing can be wrapped up by cap; {FIX_PERIOD}", file=sys.stderr)
        return 1
    hit = {p: caps(s, p)[1] for p in s["branches"] if covers(s, p) and not is_wrapped(s, p)}
    names = [p for p, h in hit.items() if h] if a.all else list(a.projects)
    if not names:
        if not a.all:
            print("lane-improve: say which project is wrapped up (`lane-improve drained <project>`), or --all", file=sys.stderr)
            return 2
        print("lane-improve: nothing is draining, and no project has reached a cap (`lane-improve status` shows each one's "
              "progress; `lane-improve off` ends the mode)", file=sys.stderr)
        return 1
    bad = [n for n in names if not hit.get(n)]
    if bad:
        print(f"lane-improve: not wrapped up: {', '.join(bad)}. The mode is on, so a project wraps up only once its cap is "
              "reached (or after `lane-improve off`)", file=sys.stderr)
        return 1
    mark_wrapped(s, names)
    cos = {c.name: c for c in _coordinator_list()}
    for w in remove_denies([cos[n] for n in names if n in cos]):
        print(w, file=sys.stderr)
    print(f"lane-improve: wrapped up at its cap: {', '.join(names)}; the mode stays on for the other projects")
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
    if getattr(a, "json", False):
        for row in status_rows():
            print(json.dumps(row, sort_keys=True))
        return 0
    s = load()
    if s:
        print(f"improve: on (since {dt.datetime.fromisoformat(s['started']).strftime('%a %d %b %H:%M')})")
        print("\n".join(describe(s) + maintenance_lines(s, _coordinator_list())))
        why = period_problem(s)
        if why:
            print(f"  period record: {why}; counts and wrap-ups are unknown and merges are refused. {FIX_PERIOD}")
        elif s.get("max_items") or s.get("stop_at") is not None or s.get("max_hours"):
            for p in sorted(s["branches"]):
                if not covers(s, p):
                    continue
                parts, hit = caps(s, p)
                if is_wrapped(s, p):
                    tail = (", wrapped up (a cap was reached); raise a cap and name it with `lane-improve on --continue "
                            f"--project {p}` to resume it")
                elif hit:
                    tail = (f": cap reached ({hit}): no new cycle; the running lane finishes, then wrap up and run "
                            f"`lane-improve drained {p}`")
                else:
                    tail = ""
                print(f"  {p}: {', '.join(parts)}{tail}")
        cyc = read_cycles(s)
        for p in sorted(cyc):
            if covers(s, p):
                print(f"  cycle: {cycle_text(p, cyc[p])}")
        return 0
    d = load_drain(quiet=True)
    if d is None and (e := ended_state()) is not None:
        d = _to_drain(e)
    if d is not None and d["pending"]:
        when = dt.datetime.fromisoformat(d["ended"]).strftime("%a %d %b %H:%M")
        print(f"improve: draining (ended {when}); no new cycle starts, the running lanes' PRs can still merge")
        print("  to wrap up: " + ", ".join(d["pending"]))
        lost = period_problem(d)
        if lost:  # away-merge is refused (`lane-improve pr` is not): only the wrap-up and a new period remain
            print(f"  period record: {lost}; nothing more merges in this period. {FIX_DRAINING_PERIOD}")
        waited = (now() - dt.datetime.fromisoformat(d["ended"]).astimezone()).total_seconds() / 3600
        if waited >= DRAIN_NOTICE_HOURS:
            print(f"  it has waited {int(waited)} h: nobody has run `lane-improve drained` for it; ask the coordinator, or run "
                  "`lane-improve drained --all` yourself once its last PR is dealt with")
        for p in d["pending"]:
            if p in d["branches"]:
                first = "" if lost else f"away-merge the last PR into {d['branches'][p]}, "
                print(f"  {p}: {first}run `lane-improve pr {p} --summary-file <path>` (the draft PR into "
                      f"{d['integration'].get(p, '?')} gets the final summary) and point the owner at it, then run "
                      f"`lane-improve drained {p}`")
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
    on.add_argument("--stop-at", dest="stop_at")
    on.add_argument("--max-items", dest="max_items")
    on.add_argument("--max-hours", dest="max_hours")
    on.add_argument("--continue", dest="cont", action="store_true")
    on.add_argument("--maintenance", action="store_true")
    on.add_argument("--no-maintenance", dest="no_maintenance", action="store_true")
    on.add_argument("--yes", action="store_true")
    sub.add_parser("off")
    dr = sub.add_parser("drained")
    dr.add_argument("projects", nargs="*")
    dr.add_argument("--all", action="store_true")
    stp = sub.add_parser("status")
    stp.add_argument("--json", action="store_true")
    cy = sub.add_parser("cycle")
    cy.add_argument("project")
    cy.add_argument("--phase")
    cy.add_argument("--item")
    cy.add_argument("--pr", type=int)
    cy.add_argument("--round", type=int)
    cy.add_argument("--dispatch")
    cy.add_argument("--worker")
    cy.add_argument("--worktree")
    cy.add_argument("--budget-min", dest="budget_min", type=int)
    cy.add_argument("--address")
    cy.add_argument("--close", choices=("merged", "parked"))
    cy.add_argument("--findings-file", dest="findings_file")
    cy.add_argument("--metric")
    cy.add_argument("--json", action="store_true")
    pr = sub.add_parser("pr")
    pr.add_argument("project")
    pr.add_argument("--summary-file", dest="summary_file")
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
            "unmetered": cmd_unmetered, "rest": cmd_rest, "pr": cmd_pr, "cycle": cmd_cycle}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
