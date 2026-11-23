"""The names a public repository must not carry: hostnames, usernames and project names (the public-repo rule in
AGENTS.md). away-merge's built-in scan reads them from here, so there is one list and one place that builds it. The
real values never live in the repo; they come from:

  the machine         its hostname (whole and first label) and the login user name
  machine.conf        the other coordinators' project names (the project being merged is left out: its own repo names it)
  a file outside the repo   $LANE_PRIVATE_NAMES, else ~/.config/lane-control/private-names.txt: one name per line, blank
                      lines and `#` comments ignored; for the names the machine cannot know (other hosts, other users)

A name is matched as a whole word, ignoring case. Names under MIN_LEN characters and a few generic ones (GENERIC) are
dropped, because they would match ordinary words; the note says how many. Nothing here prints a name: a hit is
reported as its kind only, since the report may land in a public place.
"""
import getpass
import os
import re
import socket
from pathlib import Path
from typing import NamedTuple

MIN_LEN = 3
GENERIC = {"localhost", "localdomain", "root", "user", "admin", "nobody", "host", "home", "runner"}
ENV = "LANE_PRIVATE_NAMES"


class NamesError(Exception):
    """The list cannot be built (or is empty): the scan must refuse, not pass as if it had checked names."""


class Names(NamedTuple):
    entries: list  # [(kind, name)]
    note: str      # one line for the owner: what was checked, and what was not

    def find(self, text: str) -> "str | None":
        """The kind of the first name in `text` ("a username", ...), never the name itself."""
        for kind, name in self.entries:
            if re.search(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", text, re.I):
                return "a " + kind
        return None


def default_path() -> Path:
    return Path.home() / ".config" / "lane-control" / "private-names.txt"


def load(projects=(), own: str = "", hostname: "str | None" = None, user: "str | None" = None, env=None,
         fallback: "Path | None" = None) -> Names:
    """The list for scanning `own`'s diff. `projects` are all coordinator names from machine.conf."""
    env = os.environ if env is None else env
    try:
        hostname = socket.gethostname() if hostname is None else hostname
    except OSError:
        hostname = ""
    try:
        user = getpass.getuser() if user is None else user
    except (OSError, KeyError, ImportError):
        user = ""
    wanted, dropped = [], set()

    def add(kind: str, name: str) -> None:
        name = name.strip()
        if not name:
            return
        if len(name) < MIN_LEN or name.lower() in GENERIC:
            dropped.add(name.lower())
        elif (kind, name) not in wanted:
            wanted.append((kind, name))

    add("hostname", hostname)
    add("hostname", hostname.split(".")[0])
    add("username", user)
    for p in projects:
        if p and p != own:
            add("project name", p)
    listed = 0
    explicit = env.get(ENV)
    path = Path(explicit) if explicit else (fallback or default_path())
    if explicit or path.is_file():
        try:
            lines = path.read_text().splitlines()
        except (OSError, UnicodeDecodeError) as e:
            raise NamesError(f"the private-names file {path.name} cannot be read ({getattr(e, 'strerror', None) or e}); "
                             "the diff cannot be checked for names") from None
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#"):
                before = len(wanted)
                add("listed name", line)
                listed += len(wanted) - before
    if not wanted:
        raise NamesError(f"no private names to check: this machine's hostname and user name are generic or unknown, "
                         f"machine.conf names no other project, and there is no private-names file ({ENV} or "
                         f"~/.config/lane-control/private-names.txt)")
    note = (f"checked {len(wanted)} private names: the machine's hostname and user, "
            f"{sum(1 for k, _ in wanted if k == 'project name')} other project names from machine.conf, "
            + (f"{listed} from the private-names file" if listed or explicit or path.is_file()
               else f"no private-names file ({ENV} or ~/.config/lane-control/private-names.txt), so other hosts and users are not checked")
            + (f"; {len(dropped)} too short or generic, ignored" if dropped else ""))
    return Names(wanted, note)
