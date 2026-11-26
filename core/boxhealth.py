"""Box health that a listening port does not prove (BOX-1, field report lc-l78): a tunnel unit's substate, whether its
restart counter climbs, and whether the socket belongs to the unit's own process; one Orca server runtime per user on
a host; the systemd user manager's SHELL against the login shell in passwd; lane-control paths in Orca's automations
that name an old tree.

Read-only: nothing here starts, stops or writes anything. Standard library only, so project-setup --check can send
this file to a box as the first part of its probe. Used by lane-control doctor and project-setup --check.
LANE_CHECK_SETTLE (seconds, default 5) is how long the restart counter is watched."""
import os
import re
import subprocess
from pathlib import Path

PROC = Path("/proc")
SLOT_RE = re.compile(r"/\.orca-remote/orcad-([A-Za-z0-9._+-]+)/")


def settle() -> float:
    try:
        return max(0.0, float(os.environ.get("LANE_CHECK_SETTLE") or 5))
    except ValueError:
        return 5.0


# --- processes and sockets (Linux /proc) ----------------------------------------------------------------------------

def mine():
    """The pids of this user's processes."""
    uid = os.getuid()
    for d in PROC.glob("[0-9]*"):
        try:
            if d.stat().st_uid == uid:
                yield int(d.name)
        except (OSError, ValueError):
            continue


def argv(pid: int) -> list:
    try:
        raw = (PROC / str(pid) / "cmdline").read_bytes()
    except OSError:
        return []
    return [a.decode(errors="replace") for a in raw.split(b"\0") if a]


def comm(pid: int) -> str:
    """The start of a process's command line, on one line."""
    return " ".join(" ".join(argv(pid)).split())[:80] or "?"


def tree(pid: int) -> set:
    """pid and every process below it (a wrapper's ssh, ssh's ProxyCommand)."""
    parent = {}
    for p in mine():
        try:
            for line in (PROC / str(p) / "status").read_text().splitlines():
                if line.startswith("PPid:"):
                    parent[p] = int(line.split()[1])
                    break
        except (OSError, ValueError, IndexError):
            continue
    out, grew = {pid}, True
    while grew:
        more = {p for p, pp in parent.items() if pp in out and p not in out}
        out |= more
        grew = bool(more)
    return out


def sockets(pids) -> set:
    """The socket inodes the processes hold."""
    out = set()
    for p in pids:
        try:
            fds = list((PROC / str(p) / "fd").iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                m = re.fullmatch(r"socket:\[(\d+)\]", os.readlink(fd))
            except OSError:
                continue
            if m:
                out.add(m.group(1))
    return out


def tcp() -> list:
    """(local port, state, inode) of every TCP socket; state 0A is a listener."""
    rows = []
    for f in ("net/tcp", "net/tcp6"):
        try:
            lines = (PROC / f).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            c = line.split()
            if len(c) >= 10:
                try:
                    rows.append((int(c[1].rsplit(":", 1)[1], 16), c[3], c[9]))
                except (ValueError, IndexError):
                    continue
    return rows


def listeners(port: int) -> set:
    return {ino for p, st, ino in tcp() if p == port and st == "0A"}


def port_owners(port: int) -> list:
    """This user's pids that hold a listener on the port (another user's cannot be read: none named)."""
    want = listeners(port)
    return sorted(p for p in mine() if want and sockets([p]) & want) if want else []


# --- systemd user units ---------------------------------------------------------------------------------------------

PROPS = ("LoadState", "ActiveState", "SubState", "NRestarts", "MainPID")


def show(unit: str):
    """The unit's state as systemctl --user show says it, or None when there is no user manager to ask."""
    try:
        r = subprocess.run(["systemctl", "--user", "show", unit, *[x for p in PROPS for x in ("-p", p)]],
                           capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    out = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    return out if out.get("LoadState") else None


def num(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def health(units: dict) -> dict:
    """{unit: (state, detail)} for {unit: port}: with a port, the unit's process must own the listener on it (an
    `ssh -L`); with None, it must hold a TCP connection of its own (an `ssh -R`: a multiplexed ssh rides another
    ssh's connection and holds none). state: ok, broken, or "not checked" (no user manager). The restart counter is
    read twice, LANE_CHECK_SETTLE seconds apart: one that climbs is broken even while the port answers."""
    import time
    first = {u: show(u) for u in units}
    if any(s for s in first.values()) and settle():
        time.sleep(settle())
    out = {}
    for unit, port in units.items():
        a, b = first[unit], show(unit)
        if a is None or b is None:
            out[unit] = ("not checked", "no systemd user manager answered")
            continue
        n1, n2, pid = num(a.get("NRestarts")), num(b.get("NRestarts")), num(b.get("MainPID"))
        if b.get("LoadState") != "loaded":
            out[unit] = ("broken", f"{unit} is {b.get('LoadState')}: no such unit")
        elif (b.get("ActiveState"), b.get("SubState")) != ("active", "running") or not pid:
            out[unit] = ("broken", f"{unit} is {b.get('ActiveState')}/{b.get('SubState')}, not active/running "
                                   f"(restarted {n2} times)")
        elif n2 > n1:
            out[unit] = ("broken", f"{unit} restarted {n2 - n1} times in {settle():g}s ({n2} in all): it flaps "
                                   "while it looks up")
        else:
            held = sockets(tree(pid))
            if port:
                mine_ = listeners(port) & held
                if not mine_:
                    others = [p for p in port_owners(port) if p not in tree(pid)]
                    who = (f"held by pid {', '.join(map(str, others))} ({comm(others[0])})" if others else
                           "held by no process of yours" if listeners(port) else "not listening at all")
                    out[unit] = ("broken", f"127.0.0.1:{port} is {who}, not by {unit}'s ssh (pid {pid}): a "
                                           "multiplexed ssh (ControlMaster) leaves the port with the master")
                    continue
                what = f"its ssh (pid {pid}) owns 127.0.0.1:{port}"
            else:
                inos = {ino for _, _, ino in tcp()}
                if not held & inos:
                    out[unit] = ("broken", f"{unit}'s ssh (pid {pid}) holds no TCP connection of its own: it rides "
                                           "another ssh's (ControlMaster), which can drop the forward unseen")
                    continue
                what = f"its ssh (pid {pid}) holds its own connection"
            out[unit] = ("ok", f"active/running, {what}, restarted {n2} times, not climbing")
    return out


def tunnel_units(conf: Path) -> list:
    """The tunnel units this machine has written: dicts with unit, host, port (the -L listener, or None for a -R
    unit), controlpath (its ExecStart sets ControlPath=none) and env (a lane-tunnel instance's env file)."""
    units = conf / "systemd" / "user"
    out = []
    for f in sorted(units.glob("orca-tunnel-*.service")):
        try:
            text = f.read_text()
        except OSError:
            continue
        m = re.search(r"^ExecStart=.*-L 127\.0\.0\.1:(\d+):127\.0\.0\.1:\d+ (\S+)\s*$", text, re.M)
        out.append({"unit": f.name, "host": m.group(2) if m else f.name[len("orca-tunnel-"):-len(".service")],
                    "port": int(m.group(1)) if m else None, "controlpath": unmuxed(text),
                    "kind": "orca-tunnel"})
    try:
        template = (units / "lane-tunnel@.service").read_text()
    except OSError:
        template = ""
    for f in sorted((conf / "lane-control" / "tunnels").glob("*.env")):
        try:
            env = dict(line.split("=", 1) for line in f.read_text().splitlines() if "=" in line)
        except OSError:
            continue
        out.append({"unit": f"lane-tunnel@{f.stem}.service", "host": env.get("SSH_HOST") or f.stem, "port": None,
                    "controlpath": unmuxed(template), "kind": "lane-tunnel", "env": env})
    return out


def unmuxed(unit_text: str) -> bool:
    """Whether every ExecStart of a unit runs ssh with ControlPath=none (never on a ControlMaster's socket)."""
    starts = [line for line in unit_text.splitlines() if line.startswith("ExecStart=")]
    return bool(starts) and all("ControlPath=none" in line for line in starts)


def repair(t: dict, tree=None) -> str:
    """The command that rewrites and restarts one tunnel unit (with `tree`, by its path in that lane-control tree)."""
    if t["kind"] == "orca-tunnel":
        return f"{str(tree) + '/scripts/' if tree else ''}orca-host-install {t['host']} --tunnel"
    e = t.get("env") or {}
    port, rport = e.get("PORT", "<port>"), e.get("REMOTE_PORT") or e.get("PORT", "<port>")
    opts = "".join(f" {flag} {e[k]}" for flag, k in (("--auth-sock", "SSH_AUTH_SOCK"), ("--ssh", "LANE_TUNNEL_SSH"))
                   if e.get(k))
    return f"{str(tree) + '/core/scripts/' if tree else ''}lane-tunnel start{opts} --remote-port {rport} {t['host']} {port}"


# --- Orca's server runtimes on a host -------------------------------------------------------------------------------

def orcad(home=None) -> list:
    """This user's processes that run from an Orca server slot under <home>/.orca-remote: dicts with pid, slot, role
    (server: an orcad.js; helper: another node process of that slot) and port (its --port, or None)."""
    home = str(home or Path.home())
    out = []
    for pid in mine():
        if pid == os.getpid():
            continue
        a = argv(pid)
        if not a:
            continue
        try:
            cwd = os.readlink(PROC / str(pid) / "cwd") + "/"
        except OSError:
            cwd = ""
        slot = None
        for w in a + [cwd]:
            m = SLOT_RE.search(w)
            if m and w.startswith(home + "/.orca-remote/"):
                slot = m.group(1)
                break
        if not slot:
            continue
        server = any(w == "orcad.js" or w.endswith("/orcad.js") for w in a)
        if not server and os.path.basename(a[0]) != "node":
            continue  # an editor or a pager with a slot's file open is not part of the server
        port = None
        if "--port" in a[:-1]:
            port = num(a[a.index("--port") + 1]) or None
        out.append({"pid": pid, "slot": slot, "role": "server" if server else "helper", "port": port})
    return out


def runtimes(procs: list, port=None) -> tuple:
    """(state, detail) for the processes orcad() found: ok with exactly one server (on `port`, when given) and nothing
    from another slot; broken otherwise, naming each pid; none with no server at all."""
    servers = [p for p in procs if p["role"] == "server"]
    if not servers:
        if procs:
            return "broken", "no Orca server runs, but " + named(procs) + " still run from a slot"
        return "none", "no Orca server process runs from ~/.orca-remote"
    if len(servers) > 1:
        return "broken", f"{len(servers)} Orca server processes run, one is the rule: " + named(servers)
    s = servers[0]
    stale = [p for p in procs if p["slot"] != s["slot"]]
    if stale:
        return "broken", f"orcad-{s['slot']} serves (pid {s['pid']}), and {named(stale)} of an older build still run"
    if port and s["port"] != port:
        return "broken", f"the one server, orcad-{s['slot']} (pid {s['pid']}), is on port {s['port']}, not {port}"
    return "ok", f"one server: orcad-{s['slot']} (pid {s['pid']}" + (f", port {s['port']})" if s["port"] else ")")


def named(procs: list) -> str:
    return ", ".join(f"pid {p['pid']} (orcad-{p['slot']}" + (f" port {p['port']}" if p["port"] else "") +
                     ("" if p["role"] == "server" else ", helper") + ")" for p in procs)


# --- the user manager's SHELL ---------------------------------------------------------------------------------------

def shells() -> tuple:
    """(the user manager's SHELL or None, the login shell passwd names or None). A chsh changes passwd only: a user
    manager that already runs keeps the old SHELL, and every service it starts (Orca's server) launches that."""
    import pwd
    try:
        login = pwd.getpwuid(os.getuid()).pw_shell or None
    except KeyError:
        login = None
    try:
        r = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None, login
    if r.returncode != 0:
        return None, login
    manager = next((line[len("SHELL="):] for line in r.stdout.splitlines() if line.startswith("SHELL=")), None)
    return manager, login


def shell_fix(login: str) -> str:
    return (f"systemctl --user set-environment SHELL={login}, then restart Orca's server: systemctl --user restart "
            "orcad.service (its terminals end; a running agent keeps the environment it started with)")


# --- lane-control paths stored in Orca ------------------------------------------------------------------------------

PATH_RE = re.compile(r"(?:~/|/)[^\s'\"`;|&()<>,]+")


def stale_paths(text: str, root: Path, home: Path) -> list:
    """(path, why) for each lane-control path in the text that is not the installed tree's `current`: one sha's tree,
    the flat copy from before the install, a checkout, or a path that is gone."""
    out, roots = [], {str(root), str(root.resolve())}
    for m in PATH_RE.finditer(text or ""):
        p = m.group(0).rstrip(".")
        full = str(home) + p[1:] if p.startswith("~") else p
        if "/lane-control/" not in full.rstrip("/") + "/":  # a folder named lane-control, not lane-control-beads
            continue
        under = next((r for r in roots if full.startswith(r + "/")), None)
        if under:
            first = full[len(under) + 1:].split("/", 1)[0]
            if first == "current":
                if not os.path.exists(full):
                    out.append((p, "is not in the installed tree"))
            elif re.fullmatch(r"[0-9a-f]{40}", first):
                out.append((p, f"names tree {first[:12]} itself, not current"))
            else:
                out.append((p, "names the flat copy from before the versioned install"))
            continue
        if not os.path.exists(full):
            out.append((p, "does not exist"))
            continue
        d = Path(full)
        while d != d.parent:
            if (d / ".lane-control-root").exists():
                out.append((p, f"names the checkout {d}, not the installed tree"))
                break
            d = d.parent
    return out
