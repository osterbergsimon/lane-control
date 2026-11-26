"""The machine config, ~/.config/lane-control/machine.conf (core/templates/machine.conf.example), and what the machine
scripts (night-precheck, machine-check, lane-sweep, machine-setup) read with it: presence from lane-limits.sh and the
state of self-hosted runners.

The file is shell-sourceable KEY=value, so it is read by sourcing it in bash; COORDINATORS has one coordinator per
line, `<name> <checkout path>` (the name is a stable project key, the path may not have spaces). Its harness and
address are looked up from Orca when needed (resolve): the one agent terminal whose folder is that checkout. A third
and fourth column, `<harness> <Orca terminal title>` (the title may have spaces), stay accepted as an explicit
override of either.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
LANE_LIMITS = HERE / "scripts" / "lane-limits.sh"
EXAMPLE = HERE / "templates" / "machine.conf.example"
NUMBERS = {"NIGHT_START": 23, "NIGHT_END": 7, "IDLE_MIN": 45, "RECHECK_MIN": 120, "AWAY_HOURS": 14,
           "IMPROVE_IDLE_MIN": 30, "WATCHDOG_MIN": 45, "WATCHDOG_ESCALATE_MIN": 120}
MODELS = ("MACHINE_COORDINATOR", "AUTOMATION_MODEL")  # machine-setup's models; there is no default (lc-l78.18)
KEYS = ("COORDINATORS", "RUNNER_REPOS", "MACHINE_COORDINATOR_TO", "MACHINE_COORDINATOR_FOLDER", *MODELS, *NUMBERS)
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class ConfError(Exception):
    """The config is missing or cannot be read: the caller says why and skips (no guess, no cost)."""


class Coordinator(NamedTuple):
    """A COORDINATORS line. harness and title are "" when the line does not give them (look them up with resolve)."""
    name: str
    checkout: Path
    harness: str = ""
    title: str = ""


class Where(NamedTuple):
    """A coordinator with its harness and Orca terminal title known; handle is its Orca terminal ("" when Orca was not
    asked or has no single terminal for it, and the title was given in machine.conf)."""
    name: str
    checkout: Path
    harness: str
    title: str
    handle: str = ""


def path() -> Path:
    v = os.environ.get("LANE_MACHINE_CONF")
    return Path(v) if v else Path.home() / ".config" / "lane-control" / "machine.conf"


def load(p: "Path | None" = None) -> dict:
    """{KEY: value} for KEYS, numbers as int (a missing or junk number takes its default and says so on warnings)."""
    p = Path(p) if p else path()
    if not p.is_file():
        raise ConfError(f"{p}: no machine config (machine-setup writes one from {EXAMPLE.name}; fill in COORDINATORS)")
    dump = 'for k in "$@"; do printf "%s\\0%s\\0" "$k" "${!k-}"; done'
    env = {k: v for k, v in os.environ.items() if k not in MODELS}  # a model comes from the file, never the shell
    r = subprocess.run(["bash", "-c", f'set -e; . "$0" >/dev/null; {dump}', str(p), *KEYS],
                       capture_output=True, text=True, env={**env, "BASH_ENV": ""})
    if r.returncode != 0:
        raise ConfError(f"{p}: could not be read ({(r.stderr.strip() or 'bash failed').splitlines()[-1]})")
    parts = r.stdout.split("\0")
    conf = dict(zip(parts[0::2], parts[1::2]))
    conf["warnings"] = []
    for k, d in NUMBERS.items():
        v = conf.get(k, "").strip()
        if v == "":
            conf[k] = d
        elif v.isdigit():
            conf[k] = int(v)
        else:
            conf["warnings"].append(f"{p}: {k}={v!r} is not a whole number, using {d}")
            conf[k] = d
    return conf


def coordinators(conf: dict) -> list:
    out = []
    for line in conf.get("COORDINATORS", "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = line.split(None, 3)
        if len(f) < 2 or not NAME.fullmatch(f[0]):
            raise ConfError(f"COORDINATORS line {line!r}: expected <name> <checkout path> "
                            "[<harness> [<terminal title>]]")
        out.append(Coordinator(f[0], Path(os.path.expanduser(f[1])), *f[2:]))
    return out


def runner_repos(conf: dict) -> list:
    return conf.get("RUNNER_REPOS", "").split()


def presence() -> dict:
    """LANE_PRESENCE, LANE_PRESENCE_WHY and LANE_OWNER_AWAY as lane-limits.sh works them out. Anything that spends
    budget reads LANE_OWNER_AWAY (night or the away file), never LANE_PRESENCE (which a stopped budget also sets)."""
    names = ("LANE_PRESENCE", "LANE_PRESENCE_WHY", "LANE_OWNER_AWAY", "LANE_ROOM_MIN_MEM_GB")
    echo = "; ".join(f'printf "%s\\0" "${{{n}-}}"' for n in names)
    r = subprocess.run(["bash", "-c", f'. "{LANE_LIMITS}" 2>/dev/null; {echo}'], capture_output=True, text=True)
    if r.returncode != 0:
        raise ConfError(f"{LANE_LIMITS}: could not work out presence ({r.stderr.strip()})")
    return dict(zip(names, r.stdout.split("\0")))


def settings_file(checkout: Path) -> Path:
    """The project's settings store file, where project-setup and project-pause keep its settings (`paused` among
    them): <config>/lane-control/projects/<folder name of the real path>-<first 10 hex of sha256 of the real
    path>/settings.json, project-setup's store_key (a checkout named through a symlink keys the same store)."""
    real = Path(checkout).resolve()
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    key = f"{real.name}-{hashlib.sha256(str(real).encode()).hexdigest()[:10]}"  # as project-setup
    return config / "lane-control" / "projects" / key / "settings.json"


def paused(checkout: Path) -> bool:
    """Whether project-pause marked the project paused. No settings file: not paused. One that cannot be read raises
    ConfError, so a caller can skip rather than guess."""
    f = settings_file(checkout)
    try:
        data = json.loads(f.read_text())
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as e:
        raise ConfError(f"{f}: cannot read the project's settings ({e})") from None
    return bool(isinstance(data, dict) and data.get("paused"))


class RunnerError(Exception):
    pass


def offline_runners(repo: str) -> list:
    """Names of the repo's self-hosted runners that are not online; RunnerError when gh cannot tell."""
    try:
        r = subprocess.run(["gh", "api", f"repos/{repo}/actions/runners?per_page=100"], capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RunnerError(f"{repo}: gh failed ({e})") from None
    if r.returncode != 0:
        raise RunnerError(f"{repo}: gh api failed ({(r.stderr.strip() or 'no output').splitlines()[-1]})")
    try:
        runners = json.loads(r.stdout).get("runners", [])
    except (ValueError, AttributeError):
        raise RunnerError(f"{repo}: gh api gave no runner list") from None
    return [x.get("name", "?") for x in runners if x.get("status") != "online"]


def orca_cli() -> str:
    """The Orca CLI: ORCA_CLI, the Orca host CLI (~/.orca/cli/bin/orca), `orca-ide` on PATH, then the desktop shim.
    Plain `orca` may be another program, so it is never guessed."""
    home = Path.home()
    for c in (os.environ.get("ORCA_CLI"), home / ".orca" / "cli" / "bin" / "orca", shutil.which("orca-ide"),
              home / ".config" / "orca" / "linux-orca-cli-shim" / "orca"):
        if c and os.access(str(c), os.X_OK):
            return str(c)
    return "orca-ide"


class OrcaError(Exception):
    """Orca could not say what terminals there are: the caller says so once and wakes nobody it cannot find."""


def agent_terminals() -> list:
    """The agent terminals on this machine's Orca, from `terminal list --json` (1.4.268: `agentIdentity` is the agent
    running in it, `worktreePath` its folder, `title` the terminal title, `handle` the address). A plain shell, an
    orphaned or disconnected terminal and one on another host are not agents here. OrcaError when Orca is missing,
    down, answers something else or cuts the list short (a cut list could hide the one terminal a project has)."""
    cli = orca_cli()
    try:
        r = subprocess.run([cli, "terminal", "list", "--limit", "100000", "--json"], capture_output=True, text=True,
                           timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise OrcaError(f"{Path(cli).name} did not run ({e})") from None
    try:
        data = json.loads(r.stdout)
    except ValueError:
        data = None
    res = data.get("result") if isinstance(data, dict) else None
    terms = res.get("terminals") if isinstance(res, dict) else None
    if r.returncode != 0 or not isinstance(data, dict) or data.get("ok") is False or not isinstance(terms, list):
        err = data.get("error") if isinstance(data, dict) else None
        msg = err.get("message") if isinstance(err, dict) else None
        raise OrcaError(msg or (r.stderr.strip() or "no usable answer").splitlines()[-1])
    if res.get("truncated"):
        raise OrcaError(f"the terminal list is cut short ({res.get('totalCount', '?')} terminals)")
    return [t for t in terms if isinstance(t, dict) and t.get("agentIdentity") and t.get("worktreePath")
            and not t.get("orphaned") and t.get("connected") is not False
            and t.get("executionHostId", "local") == "local"]


def harness_of(agent: str) -> "str | None":
    """The lane-control harness adapter for an Orca agent id (its name, or the id its adapter starts it with)."""
    import harnesses
    for name, mod in harnesses.harnesses().items():
        if agent in (name, getattr(mod, "ORCA_AGENT_ID", None)):
            return name
    return None


def _same_dir(a, b) -> bool:
    return os.path.realpath(os.path.expanduser(str(a))) == os.path.realpath(os.path.expanduser(str(b)))


class Problem(NamedTuple):
    """A project (or several, when Orca itself failed) that cannot be named: `why` is short (for a line a reader sees
    beside the project's name), `text` the whole sentence, with what follows from it."""
    names: tuple
    why: str
    text: str


def resolve(coords: list, handles: bool = False, titles: bool = True) -> "tuple[list, list]":
    """(found, problems): each coordinator as a Where, and a Problem for each project that cannot be named.

    A line with both harness and title is taken as it is and Orca is not asked (unless `handles`, which wants the
    terminal handle too and is happy without it; with `titles=False` only the harness is needed, so a line with
    just that is enough, and Where.title may be ""). Otherwise the harness and title come from the single agent
    terminal whose folder is the checkout; none, or several, or Orca failing leaves the project out, with why:
    nothing is guessed and the caller wakes nobody for it. Orca failing is one Problem for all it affects. A title
    in machine.conf pins the handle: only a terminal with that title counts for it."""
    found, problems, blind = [], [], []
    terms = None
    orca_failed = ""
    for c in coords:
        need = not (c.harness and (c.title or not titles))
        if not need and not handles:
            found.append(Where(c.name, c.checkout, c.harness, c.title))
            continue
        if terms is None and not orca_failed:
            try:
                terms = agent_terminals()
            except OrcaError as e:
                orca_failed = str(e)
        if orca_failed:
            if need:
                blind.append(c.name)
            else:
                found.append(Where(c.name, c.checkout, c.harness, c.title))
            continue
        here = [t for t in terms if _same_dir(t["worktreePath"], c.checkout)]
        if c.title:  # the file names the session: only a terminal with that title is its handle, none is no handle
            pinned = [t for t in here if t.get("title") == c.title]
            handle = str(pinned[0].get("handle") or "") if len(pinned) == 1 else ""
            found.append(Where(c.name, c.checkout, c.harness, c.title, handle))
            continue
        if not need and len(here) != 1:  # nothing to look up, and no single terminal to take a handle from
            found.append(Where(c.name, c.checkout, c.harness, c.title))
            continue
        if len(here) == 1:
            t = here[0]
            harness = c.harness or harness_of(str(t["agentIdentity"]))
            title = str(t.get("title") or "")
            if not harness:
                why = f"the agent in {c.checkout} is {str(t['agentIdentity'])!r}, which has no harness adapter here"
                problems.append(Problem((c.name,), why, f"{c.name}: {why}; nobody is woken for it (give its harness "
                                                        "and title in machine.conf)"))
            elif not title and titles:
                why = f"its agent terminal in {c.checkout} has no title in Orca"
                problems.append(Problem((c.name,), why, f"{c.name}: {why}; nobody is woken for it (give its harness "
                                                        "and title in machine.conf)"))
            else:
                found.append(Where(c.name, c.checkout, harness, title, str(t.get("handle") or "")))
        elif not here:
            why = f"no agent terminal in {c.checkout}"
            problems.append(Problem((c.name,), why, f"{c.name}: {why} in Orca; nobody is woken for it (start its "
                                                    "coordinator there, or give its harness and title in machine.conf)"))
        else:
            seen = ", ".join(repr(str(t.get("title") or "?")) for t in here)
            why = f"{len(here)} agent terminals in {c.checkout} ({seen})"
            problems.append(Problem((c.name,), why, f"{c.name}: {why} in Orca; which one is its coordinator is not "
                                                    "guessed, so nobody is woken for it (close the others, or give its "
                                                    "harness and title in machine.conf)"))
    if blind:
        problems.insert(0, Problem(tuple(blind), f"Orca could not be asked ({orca_failed})",
                                   f"Orca could not be asked ({orca_failed}), so {', '.join(blind)} cannot be found "
                                   "there and nobody is woken for them (or give their harness and title in machine.conf)"))
    order = {c.name: i for i, c in enumerate(coords)}
    return sorted(found, key=lambda w: order[w.name]), problems
