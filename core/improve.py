"""Away-improve mode state (design: docs/superpowers/specs/2026-11-23-away-improve-design.md).

The state is one file, ~/.config/lane-control/improve.json (LANE_IMPROVE_FILE), written only by
`lane-away on --improve` (this module's `on`), which is the only thing that turns the mode on: plain `lane-away on`,
night and a changed budget never do. Fields:

  started    ISO time the mode was switched on
  end        ISO time, or "until-back" (no end; `lane-away off` ends it)
  limits     the owner's limits, free text, binding
  deny_paths globs a merge must not touch (away-merge checks the PR diff)
  projects   list of coordinator names, or "all"
  unmetered  true on a machine with no usage limits: no budget steps for improve work
  branches   {project: "away/<YYYY-MM-DD>"}

Every reader treats a missing, malformed or ended file as improve off; a malformed one says why on one stderr line.
Command line (lane-away calls it): `improve.py on ...`, `off`, `status`, `is-on` (exit 0 while on, silent).
"""
import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

UNTIL_BACK = "until-back"
FIELDS = ("started", "end", "limits", "deny_paths", "projects", "unmetered", "branches")


def path() -> Path:
    v = os.environ.get("LANE_IMPROVE_FILE")
    return Path(v) if v else Path.home() / ".config" / "lane-control" / "improve.json"


def now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _problem(state) -> "str | None":
    """Why a parsed state is not usable, or None."""
    if not isinstance(state, dict):
        return "not a JSON object"
    missing = [k for k in FIELDS if k not in state]
    if missing:
        return "missing " + ", ".join(missing)
    if not isinstance(state["started"], str):
        return "started is not a time"
    if state["end"] != UNTIL_BACK:
        try:
            dt.datetime.fromisoformat(state["end"])
        except (TypeError, ValueError):
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
    return None


def load(quiet: bool = False) -> "dict | None":
    """The state while improve is on; None when it is off: no file, a file that cannot be used (one stderr line says
    why, unless quiet), or a time end that has passed."""
    f = path()
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
        why = _problem(state)
    except ValueError:
        state, why = None, "not valid JSON"
    if why:
        if not quiet:
            print(f"improve: {f} ignored ({why}); improve is off", file=sys.stderr)
        return None
    if state["end"] != UNTIL_BACK and dt.datetime.fromisoformat(state["end"]).astimezone() <= now():
        return None
    return state


def active_for(project: str) -> bool:
    s = load()
    return bool(s) and (s["projects"] == "all" or project in s["projects"])


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


def known_projects() -> list:
    import machineconf
    return [c.name for c in machineconf.coordinators(machineconf.load())]


def plan_branches(projects: list, today: dt.date) -> dict:
    return {p: f"away/{today.isoformat()}" for p in projects}


def describe(s: dict) -> list:
    """The lines `on` and `status` show: what is on, so the owner sees exactly what will run."""
    end = "until back (until you run lane-away off)" if s["end"] == UNTIL_BACK else "ends " + dt.datetime.fromisoformat(s["end"]).strftime("%a %H:%M")
    which = "all set-up projects (including ones set up later)" if s["projects"] == "all" else ", ".join(s["projects"])
    lines = [f"  runs: {end}", f"  projects: {which}"]
    for p, b in sorted(s["branches"].items()):
        lines.append(f"  branch: {p} -> {b}")
    lines.append(f"  limits: {s['limits'] or '(none given)'}")
    if s["deny_paths"]:
        lines.append(f"  never touching: {', '.join(s['deny_paths'])}")
    lines.append("  budget: " + ("unmetered (no budget steps for improve work; machine limits still apply)" if s["unmetered"]
                                 else "metered (follows the project's budget steps)"))
    return lines


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
            end = UNTIL_BACK
    except ValueError as e:
        print(f"lane-away: {e}", file=sys.stderr)
        return 2
    if not a.yes and not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("lane-away: improve mode spends tokens until it is switched off; there is no terminal here, so it needs "
              "--yes (run it yourself, or on your own explicit word)", file=sys.stderr)
        return 2
    try:
        known = known_projects()
    except Exception as e:  # machineconf.ConfError, or a missing module in a broken install
        if a.project:
            print(f"lane-away: cannot check --project names: {e}", file=sys.stderr)
            return 1
        known = []
    unknown = [p for p in a.project if p not in known]
    if unknown:
        print(f"lane-away: unknown project {', '.join(unknown)}; this machine's coordinators: "
              f"{', '.join(known) or '(none)'}", file=sys.stderr)
        return 1
    projects = list(dict.fromkeys(a.project)) if a.project else "all"
    state = {
        "started": now().isoformat(timespec="seconds"),
        "end": end,
        "limits": a.limits or "",
        "deny_paths": [g.strip() for g in (a.deny_paths or "").split(",") if g.strip()],
        "projects": projects,
        "unmetered": bool(a.unmetered),
        "branches": plan_branches(known if projects == "all" else projects, now().date()),
    }
    write(state)
    if a.away_file:
        af = Path(a.away_file)
        af.parent.mkdir(parents=True, exist_ok=True)
        af.touch()
    print("lane-away: improve mode on (the owner counts as away while it runs)")
    print("\n".join(describe(state)))
    return 0


def cmd_off(a) -> int:
    clear()
    return 0


def cmd_status(a) -> int:
    s = load()
    if not s:
        print("improve: off")
        return 0
    print(f"improve: on (since {dt.datetime.fromisoformat(s['started']).strftime('%a %H:%M')})")
    print("\n".join(describe(s)))
    return 0


def cmd_is_on(a) -> int:
    return 0 if load(quiet=True) else 1


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
    on.add_argument("--yes", action="store_true")
    on.add_argument("--away-file")
    sub.add_parser("off")
    sub.add_parser("status")
    sub.add_parser("is-on")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    return {"on": cmd_on, "off": cmd_off, "status": cmd_status, "is-on": cmd_is_on}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
