"""The one front door: `lane-control project|machine|improve ...` and the scripts each form runs (owner 2026-11-26,
lc-35h). A form only names a script and the arguments it puts first; every other argument goes through unchanged, so
the script decides everything as before. The old names keep working; run by hand in a terminal, each says its new form
once on stderr (`hint`), and nothing when the front door, another script or an automation runs it.

Read by core/scripts/lane-control (the dispatch and its help) and by each old script (the hint)."""
import os
import shlex
import sys

ENV = "LANE_CONTROL_FRONT_DOOR"  # set for the script a form runs and for everything it starts: no hint there

PAUSE_VERBS = ("rest", "wake", "stop", "start", "status")
IMPROVE_VERBS = ("on", "off", "drained", "status", "rest", "pr", "cycle")

# (group, verb): (script in core/scripts/, the arguments it puts before the rest, usage, one line for the help)
ROUTES = {
    ("project", "setup"): ("project-setup", [], "<repo> [<host>]", "set a project up, here or on a box"),
    ("project", "check"): ("project-setup", ["--check"], "<repo> [<host>]", "what a setup still needs; changes nothing"),
    ("project", "refresh"): ("project-setup", ["--refresh"], "<repo> [<host>]", "render personas and skills again"),
    ("project", "show"): ("project-setup", ["--show"], "<repo> [<host>]", "print the project's settings store"),
    ("project", "remove-host"): ("project-setup", ["--remove-host"], "<host>", "undo a box's desktop lanes"),
    ("project", "rest"): ("project-pause", ["rest"], "<project>", "leave it alone a while; no agent stops"),
    ("project", "wake"): ("project-pause", ["wake"], "<project>", "undo rest"),
    ("project", "stop"): ("project-pause", ["stop"], "<project>", "rest, and close its coordinator after a handover"),
    ("project", "start"): ("project-pause", ["start"], "<project>", "start its missing standing agents again"),
    ("project", "status"): ("project-pause", ["status"], "<project>", "running, resting or paused"),
    ("machine", "setup"): ("machine-setup", [], "[--host <host>]", "set this machine (or a box) up once"),
    ("machine", "check"): ("machine-setup", ["--check"], "", "the machine coordinator's persona and anchor"),
    ("machine", "changes"): ("machine-check", [], "[--last]", "budget, memory, runner changes (an automation's)"),
    ("improve", "on"): ("lane-improve", ["on"], "[--until HH:MM] [--project <name>]...", "only on the owner's word"),
    ("improve", "off"): ("lane-improve", ["off"], "", "end it; what runs drains"),
    ("improve", "status"): ("lane-improve", ["status"], "[--json]", "off, on or draining, per project"),
    ("improve", "drained"): ("lane-improve", ["drained"], "<project>... | --all", "a wrap-up is done"),
    ("improve", "rest"): ("lane-improve", ["rest"], "<project>", "nothing left within the limits"),
    ("improve", "pr"): ("lane-improve", ["pr"], "<project> [--summary-file <path>]", "the draft summary PR"),
    ("improve", "cycle"): ("lane-improve", ["cycle"], "<project> [options]", "a cycle's recorded state"),
}

GROUPS = {"project": "a project and its coordinator", "machine": "this machine (or a box)",
          "improve": "away-improve mode (separate from presence, lane-away)"}
OLD = ("project-setup", "project-pause", "machine-setup", "machine-check", "lane-improve")
INTERNAL = ("--via-host", "--box-pairing")  # project-setup run by another project-setup: never a person


def route(argv: list):
    """`<group> <verb> rest...` -> (script, arguments), or None when no form matches."""
    if len(argv) < 2 or (argv[0], argv[1]) not in ROUTES:
        return None
    script, first = ROUTES[(argv[0], argv[1])][:2]
    return script, [*first, *argv[2:]]


def group_help(group: str) -> str:
    out = [f"lane-control {group}: {GROUPS[group]}"]
    for (g, v), (script, first, usage, what) in ROUTES.items():
        if g == group:
            out.append(f"  {f'lane-control {g} {v} {usage}'.rstrip():<58} {what}")
            out.append(f"      runs {' '.join([script, *first])}, with the rest of the arguments after it")
    out.append(f"Every option of a script works after its form: lane-control {group} <verb> --help shows them.")
    return "\n".join(out)


def new_form(old: str, argv: list) -> str:
    """The front-door form of `old argv`: the same script, the same arguments."""
    a = list(argv)
    if old == "project-setup":
        verb = "setup"
        for i, x in enumerate(a):
            name, eq, val = x.partition("=")
            if name in ("--check", "--refresh", "--show", "--remove-host"):
                verb = name[2:]
                a[i:i + 1] = [val] if eq else []
                break
        return shlex.join(["lane-control", "project", verb, *a])
    if old == "project-pause":
        return shlex.join(["lane-control", "project", *a]) if a[:1] and a[0] in PAUSE_VERBS else \
            "lane-control project rest|wake|stop|start|status <project>"
    if old == "machine-setup":
        if "--check" in a:
            a.remove("--check")
            return shlex.join(["lane-control", "machine", "check", *a])
        return shlex.join(["lane-control", "machine", "setup", *a])
    if old == "machine-check":
        return shlex.join(["lane-control", "machine", "changes", *a])
    if old == "lane-improve":
        return shlex.join(["lane-control", "improve", *a]) if a[:1] and a[0] in IMPROVE_VERBS else \
            "lane-control improve " + "|".join(IMPROVE_VERBS)
    raise KeyError(old)


def hint(old: str, argv: list, stream=None) -> None:
    """One line on stderr with the new form, only for a person: not when the front door (or a script it started) runs
    this, not without a terminal on stderr (an automation, a script reading the output, ssh without -t), not for
    project-setup's own runs on a box. Never fails the command."""
    try:
        quiet = os.environ.get(ENV) or (old == "project-setup" and any(x in INTERNAL for x in argv))
        os.environ[ENV] = "1"  # what this script starts is not a person
        stream = stream or sys.stderr
        if quiet or not stream.isatty():
            return
        print(f"{old}: note: this is now `{new_form(old, argv)}` (the old name keeps working)", file=stream,
              flush=True)
    except Exception:  # a hint is never worth a failed command
        pass
