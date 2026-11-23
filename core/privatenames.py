"""The names a public repository must not carry: hostnames, usernames and project names (the public-repo rule in
AGENTS.md). away-merge's name check reads them from here, so there is one list and one place that builds it. The real
values never live in the repo; they come from:

  the machine         its hostname (whole and first label) and the login user name
  the caller          the names of the other projects whose repo is private (away-merge works that out; a public
                      project's name is no secret, and the project being merged is left out: its own repo names it)
  a file outside the repo   $LANE_PRIVATE_NAMES, else ~/.config/lane-control/private-names.txt: one name per line, blank
                      lines and `#` comments ignored; for the names the machine cannot know (other hosts, other users)

A name is matched as a whole word, ignoring case; a name right after a backslash escape (`\\n`, `\\t`, `\\r`) or a
URL escape (`%2F`) counts as a word start; camelCase (`connectToHost`) is not caught. The machine's own names are
skipped when shorter than MIN_MACHINE_LEN or in GENERIC (`root`, `main`, `nixos`...: they are ordinary words and
would stop every merge); listed and project names are skipped under MIN_LEN. The note says how many were skipped and
of which kind. Nothing here prints a name: a hit is reported as its kind (and the caller adds the place), since the
report may land in a public place.
"""
import getpass
import os
import re
import socket
from pathlib import Path
from typing import NamedTuple

MIN_LEN = 3
MIN_MACHINE_LEN = 4
GENERIC = {"localhost", "localdomain", "root", "user", "admin", "nobody", "host", "home", "runner", "dev", "app", "main",
           "nixos", "ubuntu", "debian", "fedora", "raspberrypi", "test", "git"}
ENV = "LANE_PRIVATE_NAMES"
START = r"(?:(?<![A-Za-z0-9])|(?<=\\[ntr])|(?<=%[0-9A-Fa-f]{2}))"


class NamesError(Exception):
    """The list cannot be built (or is empty): the check must refuse, not pass as if it had checked names."""


class Names(NamedTuple):
    entries: list  # [(kind, name)]
    note: str      # one line for the owner: what was checked, and what was not

    def hits(self, text: str) -> list:
        """[(kind, start, end)] for every name in `text`, in order; the kind reads "a username", never the name."""
        found = []
        for kind, name in self.entries:
            for m in re.finditer(START + re.escape(name) + r"(?![A-Za-z0-9])", text, re.I):
                found.append(("a " + kind, m.start(), m.end()))
        return sorted(found, key=lambda h: h[1])

    def find(self, text: str) -> "str | None":
        h = self.hits(text)
        return h[0][0] if h else None

    def mask(self, text: str) -> str:
        """`text` with each name replaced by ***, for showing a path that holds one."""
        out, at = [], 0
        for _, s, e in self.hits(text):
            if s >= at:
                out += [text[at:s], "***"]
                at = e
        return "".join(out) + text[at:]


def default_path() -> Path:
    return Path.home() / ".config" / "lane-control" / "private-names.txt"


def load(projects=(), own: str = "", hostname: "str | None" = None, user: "str | None" = None, env=None,
         fallback: "Path | None" = None) -> Names:
    """The list for scanning `own`'s diff. `projects` are the names that count as private (the caller drops public ones)."""
    env = os.environ if env is None else env
    try:
        hostname = socket.gethostname() if hostname is None else hostname
    except OSError:
        hostname = ""
    try:
        user = getpass.getuser() if user is None else user
    except (OSError, KeyError, ImportError):
        user = ""
    wanted, skipped = [], []

    def add(kind: str, name: str, machine: bool = False) -> None:
        name = name.strip()
        if not name or name.lower() == own.lower():
            return
        if len(name) < (MIN_MACHINE_LEN if machine else MIN_LEN) or (machine and name.lower() in GENERIC):
            if kind not in skipped:
                skipped.append(kind)
        elif (kind, name) not in wanted:
            wanted.append((kind, name))

    add("hostname", hostname, True)
    add("hostname", hostname.split(".")[0], True)
    add("username", user, True)
    for p in projects:
        add("project name", p)
    listed = 0
    explicit = env.get(ENV)
    path = Path(explicit) if explicit else (fallback or default_path())
    has_file = bool(explicit) or path.is_file()
    if has_file:
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
        raise NamesError(f"no private names to check: this machine's hostname and user name are generic, short or unknown, "
                         f"no private project is named, and there is no private-names file ({ENV} or "
                         f"~/.config/lane-control/private-names.txt)")
    machine_kinds = [k for k in ("hostname", "username") if any(w[0] == k for w in wanted)]
    note = (f"checked {len(wanted)} private names: {' and '.join('the machine' + chr(39) + 's ' + k for k in machine_kinds) or 'none from the machine'}, "
            f"{sum(1 for k, _ in wanted if k == 'project name')} private project names, "
            + (f"{listed} from the private-names file" if has_file
               else f"no private-names file ({ENV} or ~/.config/lane-control/private-names.txt), so other hosts and users are not checked")
            + (f"; skipped as too short or a common word: {', '.join(skipped)}" if skipped else ""))
    return Names(wanted, note)
