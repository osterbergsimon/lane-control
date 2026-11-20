"""The machine config, ~/.config/lane-control/machine.conf (core/templates/machine.conf.example), and what the machine
scripts (night-precheck, machine-check, lane-sweep, machine-setup) read with it: presence from lane-limits.sh and the
state of self-hosted runners.

The file is shell-sourceable KEY=value, so it is read by sourcing it in bash; COORDINATORS has one coordinator per
line, `<name> <checkout path> <harness> <Orca terminal title>` (the title may have spaces, the path may not).
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
NUMBERS = {"NIGHT_START": 23, "NIGHT_END": 7, "IDLE_MIN": 45, "RECHECK_MIN": 120, "AWAY_HOURS": 14}
KEYS = ("COORDINATORS", "RUNNER_REPOS", *NUMBERS)
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class ConfError(Exception):
    """The config is missing or cannot be read: the caller says why and skips (no guess, no cost)."""


class Coordinator(NamedTuple):
    name: str
    checkout: Path
    harness: str
    title: str


def path() -> Path:
    v = os.environ.get("LANE_MACHINE_CONF")
    return Path(v) if v else Path.home() / ".config" / "lane-control" / "machine.conf"


def load(p: "Path | None" = None) -> dict:
    """{KEY: value} for KEYS, numbers as int (a missing or junk number takes its default and says so on warnings)."""
    p = Path(p) if p else path()
    if not p.is_file():
        raise ConfError(f"{p}: no machine config (machine-setup writes one from {EXAMPLE.name}; fill in COORDINATORS)")
    dump = 'for k in "$@"; do printf "%s\\0%s\\0" "$k" "${!k-}"; done'
    r = subprocess.run(["bash", "-c", f'set -e; . "$0" >/dev/null; {dump}', str(p), *KEYS],
                       capture_output=True, text=True, env={**os.environ, "BASH_ENV": ""})
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
        if len(f) < 4 or not NAME.fullmatch(f[0]):
            raise ConfError(f"COORDINATORS line {line!r}: expected <name> <checkout path> <harness> <terminal title>")
        out.append(Coordinator(f[0], Path(os.path.expanduser(f[1])), f[2], f[3]))
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
    them): <config>/lane-control/projects/<folder name as given>-<first 10 hex of sha256 of the real
    path>/settings.json, project-setup's store_key."""
    real = Path(checkout).resolve()
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    key = f"{Path(checkout).name}-{hashlib.sha256(str(real).encode()).hexdigest()[:10]}"  # as project-setup
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
