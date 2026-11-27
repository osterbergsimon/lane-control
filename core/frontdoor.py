"""The one front door: `lane-control project|coordinator|lane|machine|ratchet ...` and the scripts each form runs (owner 2026-11-26,
lc-35h). A form only names a script and the arguments it puts first; every other argument goes through unchanged, so
the script decides everything as before. The old names keep working; run by hand in a terminal, each says its new form
once on stderr (`hint`), and nothing when the front door, another script or an automation runs it.

`lane-control coordinator ...` and `lane-control lane ...` (owner 2026-11-26, lc-wja: "commands to spawn new lanes,
especially coordinators") run lane-spawn, which has no old name.

`lane-control project extras ...`, `lane-control machine extras ...` and `lane-control lane prepare ...` (owner
2026-11-26, lc-7c6: "centralize and improve these things") run lane-extras, which has no old name either.

The ratchet is unattended improvement (owner 2026-11-26: "only moves forward"), the mode lane-improve switches; its
docs and its skill still call it away-improve mode in places. `lane-control improve ...` is kept as an alias.

Read by core/scripts/lane-control (the dispatch and its help) and by each old script (the hint)."""
import os
import shlex
import sys

ENV = "LANE_CONTROL_FRONT_DOOR"  # set for the script a form runs and for everything it starts: no hint there

PAUSE_VERBS = ("rest", "wake", "stop", "start", "status")
RATCHET_VERBS = ("on", "off", "drained", "status", "rest", "pr", "cycle")
WIDTH = 62  # the usage column of the help: with the text after it, a line fits in 100 columns
MODES = ("--check", "--refresh", "--show", "--remove-host")  # project-setup's flags that have a form of their own

# (group, verb): (script in core/scripts/, the arguments it puts before the rest, usage, one line for the help)
ROUTES = {
    ("project", "setup"): ("project-setup", [], "<repo> [<host>]", "set a project up, here or on a box"),
    ("project", "check"): ("project-setup", ["--check"], "<repo> [<host>]", "what setup needs; changes nothing"),
    ("project", "refresh"): ("project-setup", ["--refresh"], "<repo> [<host>]", "render personas, skills again"),
    ("project", "show"): ("project-setup", ["--show"], "<repo> [<host>]", "print the project's settings"),
    ("project", "remove-host"): ("project-setup", ["--remove-host"], "<host>", "undo a box's desktop lanes"),
    ("project", "rest"): ("project-pause", ["rest"], "<project>", "leave it alone; no agent stops"),
    ("project", "wake"): ("project-pause", ["wake"], "<project>", "undo rest"),
    ("project", "stop"): ("project-pause", ["stop"], "<project> [--wait S] [--no-handover]",
                          "rest; close its coordinator"),
    ("project", "start"): ("project-pause", ["start"], "<project>", "start its missing agents"),
    ("project", "status"): ("project-pause", ["status"], "<project>", "running, resting or paused"),
    ("project", "extras"): ("lane-extras", ["project"], "add|list|remove <project> ...",
                            "skills, MCP, personas, hooks..."),
    ("coordinator", "start"): ("lane-spawn", ["coordinator", "start"], "<project>|machine [--force]",
                               "start it unless it runs"),
    ("coordinator", "restart"): ("lane-spawn", ["coordinator", "restart"], "<project> [--wait S]",
                                 "stop, start on its own ready reply"),
    ("coordinator", "status"): ("lane-spawn", ["coordinator", "status"], "<project>|machine",
                                "running, dead or cannot tell"),
    ("lane", "new"): ("lane-spawn", ["lane", "new"], "<project> <issue> [--tier T] [--note N]",
                      "send its coordinator a request"),
    ("lane", "list"): ("lane-spawn", ["lane", "list"], "<project>", "lane, issue, PR, CI, idle time"),
    ("lane", "status"): ("lane-spawn", ["lane", "status"], "<project>", "each lane in full"),
    ("lane", "prepare"): ("lane-extras", ["prepare"], "<project> <worktree> --role R",
                          "a lane's extras, before it starts"),
    ("machine", "setup"): ("machine-setup", [], "[--host <host>] [--check]", "set it up once; --check reads only"),
    ("machine", "report"): ("machine-check", ["--peek"], "[--last]", "budget, memory, runners; read only"),
    ("machine", "extras"): ("lane-extras", ["machine"], "add|list|remove ...", "extras for every project here"),
    ("ratchet", "on"): ("lane-improve", ["on"], "[--hours N] [--project <name>]...", "only on the owner's word"),
    ("ratchet", "off"): ("lane-improve", ["off"], "", "end it; what runs drains"),
    ("ratchet", "status"): ("lane-improve", ["status"], "[--json]", "off, on or draining, per project"),
    ("ratchet", "drained"): ("lane-improve", ["drained"], "<project>... | --all", "a wrap-up is done"),
    ("ratchet", "rest"): ("lane-improve", ["rest"], "<project>", "nothing left within the limits"),
    ("ratchet", "pr"): ("lane-improve", ["pr"], "<project> [--summary-file <f>]", "the draft summary PR"),
    ("ratchet", "cycle"): ("lane-improve", ["cycle"], "<project> [options]", "a cycle's recorded state"),
}

GROUPS = {"project": "a project and its coordinator",
          "coordinator": "coordinators: start one (live check), restart it, or see whether it runs",
          "lane": "lanes: ask a project's coordinator for one, or list them", "machine": "this machine (or a box)",
          "ratchet": "the ratchet, unattended improvement (presence is lane-away)"}
ALIASES = {"improve": "ratchet"}  # the name before the owner's rename (2026-11-26): works, and says the new one
OLD = ("project-setup", "project-pause", "machine-setup", "machine-check", "lane-improve")
NEW = ("lane-spawn", "lane-extras")  # scripts only their forms run (lc-wja, lc-7c6): no old name, so no hint
INTERNAL = ("--via-host", "--box-pairing")  # project-setup run by another project-setup: never a person

# `<verb> --help` for the forms whose script has no help of its own for that verb (review M1); the others pass it on.
# <project> is the project's folder or its name; lane-control project --help lists the other verbs.
VERB_HELP = {
    ("project", "rest"): """Leaves the project alone for a while: the night manager skips it, and no agent stops.
Refuses, naming them, while a lane or reviewer runs. Runs project-pause rest <project>.""",
    ("project", "wake"): """Undoes rest: the project gets check-ins again. Runs project-pause wake <project>.""",
    ("project", "stop"): """Rests the project and closes its coordinator, after asking the session for its handover
and waiting for it. Refuses, naming them, while a lane or reviewer runs. Runs project-pause stop.

  --wait SECONDS   how long to wait for the handover (default 600)
  --no-handover    close the terminals without asking for one""",
    ("project", "start"): """Starts the project's missing standing agents, each resuming its recorded session.
Runs project-pause start <project>.

  --force   start it although the live check cannot tell (asks once; a person at a terminal only)
  --yes     with --force: do not ask""",
    ("project", "status"): """Says whether its coordinator runs, rests or is paused, and what else runs.
Runs project-pause status <project>.""",
    ("project", "remove-host"): """Undoes a box's desktop lanes (direction 2): the box forgets this machine's Orca
environment, lane-tunnel@<host> here is stopped and removed, and it says where in Orca to revoke the
link. Runs project-setup --remove-host <host>.""",
    ("machine", "report"): """What the machine check automation would find now (budget steps, memory, runners, a
coordinator Orca cannot find), read only: nothing is recorded, so the automation still finds those
changes and passes them on to the machine coordinator. Runs machine-check --peek.
Exit 0 when something changed, 1 when nothing did.

  --last   what the automation's last check found and passed on (machine-check --last)""",
}


def canonical(group: str) -> str:
    return ALIASES.get(group, group)


def route(argv: list):
    """`<group> <verb> rest...` -> (script, arguments), or None when no form matches."""
    if len(argv) < 2 or (canonical(argv[0]), argv[1]) not in ROUTES:
        return None
    script, first = ROUTES[(canonical(argv[0]), argv[1])][:2]
    rest = argv[2:]
    if script == "machine-check" and rest == ["--last"]:
        return script, rest  # what the last check found: --last alone (machine-check takes one of the two)
    return script, [*first, *rest]


def shared_first(group: str) -> list:
    """The first arguments every form of the group puts first (lane-spawn's group word; none for the ratchet)."""
    firsts = [first for (g, _), (_, first, *_) in ROUTES.items() if g == group]
    out = []
    for words in zip(*firsts):
        if len(set(words)) != 1:
            break
        out.append(words[0])
    return out


def usage(group: str, verb: str) -> str:
    return f"lane-control {group} {verb} {ROUTES[(group, verb)][2]}".rstrip()


def verb_help(argv: list):
    """The front door's own help text for `<group> <verb> ... --help`, or None to pass --help to the script."""
    key = (canonical(argv[0]), argv[1]) if len(argv) > 1 else None
    if key not in VERB_HELP or not any(x in ("-h", "--help") for x in argv[2:]):
        return None
    return f"usage: {usage(*key)}\n\n{VERB_HELP[key]}"


def group_help(group: str) -> str:
    group = canonical(group)
    out = [f"lane-control {group}: {GROUPS[group]}"]
    for (g, v), (script, first, _, what) in ROUTES.items():
        if g == group:
            out.append(f"  {usage(g, v):<{WIDTH}} {what}")
            out.append(f"      runs {' '.join([script, *first])} with the rest of the arguments")
    out.append(f"lane-control {group} <verb> --help: that verb's options.")
    return "\n".join(out)


def new_form(old: str, argv: list) -> str:
    """The front-door form of `old argv`: the same script, the same arguments (machine-check alone: its read-only
    form, since the automation's own check records what it finds)."""
    a = list(argv)
    if old == "project-setup":
        verb, first = "setup", []
        for i, x in enumerate(a):
            name, eq, val = x.partition("=")
            if name in MODES:
                verb = name[2:]
                if name == "--remove-host":  # its value stays right after it (review m2)
                    first = [val] if eq else a[i + 1:i + 2]
                    del a[i:i + (1 if eq else 2)]
                else:
                    del a[i]
                break
        return shlex.join(["lane-control", "project", verb, *first, *a])
    if old == "project-pause":
        return shlex.join(["lane-control", "project", *a]) if a[:1] and a[0] in PAUSE_VERBS else \
            "lane-control project rest|wake|stop|start|status <project>"
    if old == "machine-setup":
        return shlex.join(["lane-control", "machine", "setup", *a])
    if old == "machine-check":
        return shlex.join(["lane-control", "machine", "report", *a])
    if old == "lane-improve":
        return shlex.join(["lane-control", "ratchet", *a]) if a[:1] and a[0] in RATCHET_VERBS else \
            "lane-control ratchet " + "|".join(RATCHET_VERBS)
    raise KeyError(old)


def for_a_person(stream=None) -> bool:
    """True when a person runs this in a terminal: not under the front door or a script it started, and with a terminal
    on stderr (not an automation, a script reading the output, ssh without -t)."""
    return not os.environ.get(ENV) and (stream or sys.stderr).isatty()


def hint(old: str, argv: list, stream=None) -> None:
    """One line on stderr with the new form, only for a person (for_a_person), never for project-setup's own runs on a
    box. Never fails the command."""
    try:
        stream = stream or sys.stderr
        person = for_a_person(stream) and not (old == "project-setup" and any(x in INTERNAL for x in argv))
        os.environ[ENV] = "1"  # what this script starts is not a person
        if not person:
            return
        if old == "machine-check" and not argv:  # the automation's own check: it records what it finds
            text = ("this is the machine check automation's own check, and it records what it finds; to look by hand "
                    "without using that up: `lane-control machine report`")
        else:
            text = f"this is now `{new_form(old, argv)}` (the old name keeps working)"
        print(f"{old}: note: {text}", file=stream, flush=True)
    except Exception:  # a hint is never worth a failed command
        pass
