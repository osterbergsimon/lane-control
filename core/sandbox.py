"""The OpenCode lane sandbox, phase 1 (SBX-2, lc-35qn): the pure parts. Spec:
docs/superpowers/specs/2026-11-27-agent-sandbox-design.md; plan: docs/superpowers/plans/2026-11-27-agent-sandbox.md;
the spike (docs/superpowers/spikes/2026-11-27-agent-sandbox/) is where each step was first shown.

Here: where the sandbox keeps its state, one-shot tickets, the launch chain's argv and environment, the mount plan,
the namespace's nft rules, the host-pid lookup, the history and CI checks and the broker's read of the lane's outbox.
Nothing here starts a process except git (history and CI checks, on repositories no agent has written) and the
`ip`-free host address read from /proc. The shim (core/scripts/lane-sandbox-shim), the broker
(core/scripts/lane-broker), prepare (core/sandboxprep.py) and the inside wrappers (core/sandbox-inside/) use it.
"""
import errno
import hashlib
import ipaddress
import json
import os
import re
import secrets
import stat
import subprocess
import time
from pathlib import Path


class SandboxError(Exception):
    """A refusal: the message is one line that says what failed and, where there is one, the fix."""


DNS = "169.254.1.53"            # pasta's DNS forward address inside the namespace
OUTBOX_CAP = 64 << 20           # a push bundle, at most
REQUEST_CAP = 1 << 20           # one broker request, at most
ROLES = ("lane", "reviewer", "check")
TEMPLATES = {".env.example", ".env.sample", ".env.template", ".env.dist"}
CI_PREFIXES = (".github/workflows/", ".github/actions/", ".gitlab/", ".circleci/", ".buildkite/")
CI_FILES = {".gitlab-ci.yml", "azure-pipelines.yml", "jenkinsfile", ".travis.yml", "bitbucket-pipelines.yml"}
PRIVATE_V4 = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10")
PRIVATE_V6 = ("fc00::/7", "fe80::/10")
ETC_FILES = ("passwd", "group", "hosts", "nsswitch.conf", "ssl", "pki", "static", "localtime", "zoneinfo")
INSIDE_TOOLS = ("orca", "orca-ide", "gh", "git", "bd", "lane-heavy")
ENV_PLAIN = ("LANG", "LC_ALL", "TERM", "COLORTERM", "USER", "LOGNAME", "TZ", "TZDIR", "LOCALE_ARCHIVE",
             "SSL_CERT_FILE", "NIX_SSL_CERT_FILE", "OPENCODE_CONFIG_CONTENT", "ORCA_PANE_KEY", "ORCA_TAB_ID",
             "ORCA_WORKTREE_ID", "ORCA_TERMINAL_HANDLE")
ENV_PREFIXES = ("ORCA_AGENT_HOOK_", "ORCA_AGENT_LAUNCH", "ORCA_OPENCODE_")
# Orca variables that name the owner's own config (not Orca's overlay): never inside.
ENV_DROPPED = ("ORCA_OPENCODE_SOURCE_CONFIG_DIR",)
# Orca variables that name a path: passed only when the shim bound that path read-only (orca_binds).
ENV_PATHS = ("OPENCODE_CONFIG_DIR", "ORCA_OPENCODE_CONFIG_DIR", "ORCA_AGENT_HOOK_ENDPOINT")
BUS_VARS = ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR")
TICKET_FIELDS = ("nonce", "role", "lane", "issue", "project", "repo", "base", "branch", "worktree", "clone",
                 "lane_dir", "ticket_dir", "home", "home_files", "tool_path", "persona", "gateway_key_env", "gateway",
                 "hook_port", "review_files", "review_write", "sha", "private", "inside", "lc_tree")
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
LANE_KEY = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
NONCE = re.compile(r"[0-9a-f]{16,64}")


# --- where things live -------------------------------------------------------------------------------------------

def _xdg(var: str, default: str) -> Path:
    v = os.environ.get(var)
    return Path(v) if v and os.path.isabs(v) else Path.home() / default


def state_root() -> Path:
    """~/.local/state/lane-control/sandbox: clones, quarantines, tickets, run records, the selftest result."""
    return _xdg("XDG_STATE_HOME", ".local/state") / "lane-control" / "sandbox"


def lane_dir(lane: str) -> Path:
    if not LANE_KEY.fullmatch(lane or ""):
        raise SandboxError(f"lane key {lane!r}: expected lower-case letters, digits, '.', '_' or '-'")
    return state_root() / "lanes" / lane


def worktree_id(wt: Path) -> str:
    """<folder name>-<12 hex of the real path's sha256>: one per Orca worktree."""
    real = Path(wt).resolve()
    name = re.sub(r"[^A-Za-z0-9._-]", "_", real.name)[:40] or "wt"
    return f"{name}-{hashlib.sha256(str(real).encode()).hexdigest()[:12]}"


def tickets_dir(wt: Path) -> Path:
    return state_root() / "tickets" / worktree_id(wt)


def binding_file(wt: Path) -> Path:
    """Written by prepare: this worktree belongs to a sandboxed lane, so a start without a ticket is refused."""
    return state_root() / "bindings" / f"{worktree_id(wt)}.json"


def runs_dir() -> Path:
    return state_root() / "runs"


def run_record(terminal: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", terminal or ""):
        raise SandboxError("no Orca terminal handle (ORCA_TERMINAL_HANDLE) to bind the run to")
    return runs_dir() / f"{terminal}.json"


def selftest_file() -> Path:
    return state_root() / "selftest.json"


def private_dir(p: Path) -> Path:
    """mkdir -p with 0700 for every folder made, and the leaf made 0700."""
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(p, 0o700)
    return p


def write_private(path: Path, text: str) -> None:
    """A 0600 file written whole (temp then rename)."""
    private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


# --- tickets -------------------------------------------------------------------------------------------------------

def write_ticket(wt: Path, t: dict) -> str:
    """A one-shot ticket for this worktree: <tickets>/<worktree-id>/<ns time>-<nonce>.json (0600). Returns the
    nonce (a new one unless t names one)."""
    nonce = t.get("nonce") or secrets.token_hex(16)
    t = {**t, "nonce": nonce, "worktree": str(Path(wt).resolve()), "created": time.time()}
    write_private(tickets_dir(wt) / f"{time.time_ns():020d}-{nonce}.json", json.dumps(t, indent=1) + "\n")
    return nonce


def check_ticket(t) -> dict:
    if not isinstance(t, dict):
        raise SandboxError("malformed ticket: not an object")
    missing = [k for k in TICKET_FIELDS if k not in t]
    if missing:
        raise SandboxError(f"malformed ticket: missing {', '.join(missing)}")
    if t["role"] not in ROLES:
        raise SandboxError(f"malformed ticket: role {t['role']!r}")
    if not NONCE.fullmatch(str(t["nonce"])):
        raise SandboxError("malformed ticket: nonce")
    if t["gateway_key_env"] is not None and not ENV_NAME.fullmatch(t["gateway_key_env"]):
        raise SandboxError("malformed ticket: gateway_key_env")
    for k in ("repo", "worktree", "clone", "lane_dir", "ticket_dir", "home", "inside", "lc_tree"):
        if not isinstance(t[k], str) or not os.path.isabs(t[k]):
            raise SandboxError(f"malformed ticket: {k} is not an absolute path")
    return t


def take_ticket(wt: Path, terminal: str) -> dict:
    """The oldest unused ticket of this worktree, taken once (atomic rename into taken/), bound to the pane's
    terminal. Raises SandboxError when there is none, it is malformed or it names another worktree."""
    if not terminal:
        raise SandboxError("no Orca terminal handle (ORCA_TERMINAL_HANDLE): a sandboxed start needs Orca's pane")
    d = tickets_dir(wt)
    real = str(Path(wt).resolve())
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".json") and not n.startswith("."))
    except FileNotFoundError:
        names = []
    taken = private_dir(d / "taken")
    for n in names:
        dst = taken / n
        try:
            os.rename(d / n, dst)  # only one taker wins
        except FileNotFoundError:
            continue
        try:
            t = check_ticket(json.loads(dst.read_text()))
        except (ValueError, OSError) as e:
            raise SandboxError(f"malformed ticket {n}: {e.__class__.__name__}; run lane prepare --sandbox again") \
                from None
        if t["worktree"] != real:
            raise SandboxError(f"ticket {n} names another worktree; run lane prepare --sandbox for this one")
        t["terminal"] = terminal
        t["taken"] = time.time()
        write_private(dst, json.dumps(t, indent=1) + "\n")
        t["ticket_file"] = str(dst)
        return t
    raise SandboxError(f"no ticket for {real}: run lane-control lane prepare <project> {real} --sandbox first")


# --- the chain -----------------------------------------------------------------------------------------------------

def tiocsti_allowed(path: Path = Path("/proc/sys/dev/tty/legacy_tiocsti")) -> bool:
    """True unless the kernel says legacy TIOCSTI is off (then --new-session is not needed and the TUI keeps its
    controlling terminal)."""
    try:
        return Path(path).read_text().strip() != "0"
    except OSError:
        return True


def _inside_path(t: dict) -> str:
    return ":".join([str(Path(t["inside"]) / "bin"), *t["tool_path"]])


def _forbidden_bind(src: str, t: dict) -> "str | None":
    """Why `src` may not be bound into the sandbox, or None. The ticket's own folders and lane-control's pinned
    tree live under lane-control's state and data folders, so they are allowed by name before the floor check."""
    home = t["home"].rstrip("/")
    s = os.path.normpath(src)
    if s in ("/", "/nix", "/home", home, "/run", "/run/user") or s.startswith(("/nix/var", "/run/user/")):
        return "a parent of the floor"
    if s == t["repo"] or s.startswith(t["repo"].rstrip("/") + "/"):
        return "the shared repository"
    own = [os.path.normpath(t[k]) for k in ("ticket_dir", "lane_dir", "lc_tree")]
    if any(s == o or s.startswith(o + "/") for o in own) or s == "/nix/store" or s.startswith("/nix/store/"):
        return None
    if s == home + "/.config" or (s.startswith(home + "/.config/") and not s.startswith(home + "/.config/orca/")):
        return "the owner's config"
    for floor in (".ssh", ".gnupg", ".netrc", ".git-credentials", ".docker", ".aws", ".kube", ".claude",
                  ".claude.json", ".local/share/opencode", ".local/share/lane-control", ".local/state/lane-control",
                  ".agents", ".cache", ".local"):
        f = f"{home}/{floor}"
        if s == f or s.startswith(f + "/") or f.startswith(s + "/"):
            return "a floor path"
    return None


def bwrap_mounts(t: dict) -> list:
    """The spec's mount plan for one ticket, as bwrap arguments (without the chain's flags and the command)."""
    home, wt, td = t["home"], t["worktree"], Path(t["ticket_dir"])
    hf = t["home_files"]
    m = ["--ro-bind", "/nix/store", "/nix/store"]
    for d in ("/usr", "/bin", "/lib", "/lib64"):
        m += ["--ro-bind-try", d, d]
    m += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/run", "--dir", "/run/lane",
          "--tmpfs", "/etc"]
    for n in ETC_FILES:
        m += ["--ro-bind-try", f"/etc/{n}", f"/etc/{n}"]
    m += ["--ro-bind", hf["resolv.conf"], "/etc/resolv.conf"]
    m += ["--tmpfs", home, "--ro-bind", hf["gitconfig"], f"{home}/.gitconfig",
          "--dir", f"{home}/.config/opencode",
          "--ro-bind", hf["opencode.json"], f"{home}/.config/opencode/opencode.json"]
    for name in ("plugins", "skills"):
        if hf.get(name):
            m += ["--ro-bind-try", hf[name], f"{home}/.config/opencode/{name}"]
    ud = os.path.normpath(f"{home}/.config/orca")
    for p in t.get("orca_binds") or []:
        p = os.path.normpath(p)
        if not p.startswith(ud + "/") or p == ud:
            raise SandboxError(f"{p}: only Orca's OpenCode overlay and its hook endpoint file are bound")
        if any(x in p.split("/") for x in ("profiles", "orca-runtime.json", "agent-session-authority.key")):
            raise SandboxError(f"{p}: not one of Orca's two paths for a lane")
        m += ["--ro-bind", p, p]
    for p in t.get("agent_dirs") or []:
        why = _forbidden_bind(p, t)
        if why or not os.path.isabs(p):
            raise SandboxError(f"agent folder {p}: {why or 'not absolute'}")
        m += ["--ro-bind", p, p]
    if t.get("persona"):
        m += ["--ro-bind", t["persona"], "/run/lane/persona.md"]
    m += ["--ro-bind", t["lc_tree"], t["lc_tree"],
          "--ro-bind", t["inside"], f"{home}/.agents/lane-inside",
          "--symlink", f"{home}/.agents/lane-inside/bin", f"{home}/.agents/bin",
          "--bind", str(td / "data"), f"{home}/.local/share/opencode",
          "--bind", str(td / "cache"), f"{home}/.cache",
          "--bind", t["clone"], wt,
          "--ro-bind", str(td / "run" / "inbox"), "/run/lane/inbox",
          "--bind", str(td / "run" / "outbox"), "/run/lane/outbox",
          "--bind", str(td / "run" / "broker.sock"), "/run/lane/broker.sock",
          "--chdir", wt]
    for i, x in enumerate(m):
        if x in ("--ro-bind", "--bind", "--ro-bind-try") and not m[i + 1].startswith(("/usr", "/bin", "/lib", "/etc/")):
            why = _forbidden_bind(m[i + 1], t)
            if why:
                raise SandboxError(f"mount plan would bind {m[i + 1]} ({why})")
    return m


def inside_env(t: dict, environ: dict, scope: bool) -> dict:
    """The allowlist, as the environment bwrap and pasta start with (never as arguments)."""
    env = {"HOME": t["home"], "PATH": _inside_path(t), "SHELL": t.get("shell") or "/bin/sh", "TMPDIR": "/tmp",
           "LANE_ROLE": {"lane": "lanes"}.get(t["role"], t["role"]), "LANE_PERSONA": "/run/lane/persona.md",
           "LANE_BROKER": "/run/lane/broker.sock", "LANE_SANDBOX": "1"}
    bound = {os.path.normpath(p) for p in t.get("orca_binds") or []}
    for k, v in environ.items():
        if k in ENV_DROPPED:
            continue
        if k in ENV_PATHS:
            if os.path.normpath(v) in bound:
                env[k] = v
        elif k in ENV_PLAIN or k.startswith(ENV_PREFIXES) or k in ("USER", "LOGNAME"):
            env[k] = v
    if t.get("gateway_key_env") and t["gateway_key_env"] in environ:
        env[t["gateway_key_env"]] = environ[t["gateway_key_env"]]
    if scope:
        env.update({k: environ[k] for k in BUS_VARS if k in environ})
    return env


def chain_argv(t: dict, conf: dict, scope: bool, environ: "dict | None" = None) -> tuple:
    """(argv, env) for the launch: [systemd-run --user --scope ... --] pasta ... -- bash -c <opener> <info> <block>
    bwrap --cap-drop ALL ... --info-fd 3 --block-fd 4 <agent> <args>. Start it with exactly `env` as its
    environment (the `env -i` of the spec): no value of the allowlist is ever an argument."""
    environ = dict(os.environ if environ is None else environ)
    for k in ("SANDBOX_BWRAP", "SANDBOX_PASTA"):
        if not os.path.isabs(conf.get(k) or ""):
            raise SandboxError(f"{k} is not set to an absolute path: run lane-control machine setup --sandbox")
    launch = Path(t["ticket_dir"]) / "launch"
    ports = [str(int(t["hook_port"]))] if t.get("hook_port") else []
    gw = t.get("gateway")
    if gw and str(gw[0]) in ("127.0.0.1", "localhost", "::1"):
        ports.append(str(int(gw[1])))
    argv = []
    if scope:
        argv += ["systemd-run", "--user", "--scope", "-q", "-p", f"MemoryMax={conf.get('SANDBOX_LANE_MEMORY') or '8G'}",
                 "--"]
    argv += [conf["SANDBOX_PASTA"], "--config-net", "-q", "-f", "--no-map-gw", "--dns-forward", DNS,
             "-t", "none", "-u", "none", "-U", "none"]
    argv += ["-T", ",".join(ports) if ports else "none", "--"]
    argv += [t.get("bash") or "bash", "-c", 'i=$1 b=$2; shift 2; exec "$@" 3>"$i" 4<"$b"', "lane-sandbox",
             str(launch / "info.json"), str(launch / "block")]
    bw = [conf["SANDBOX_BWRAP"], "--cap-drop", "ALL", "--die-with-parent", "--unshare-pid", "--unshare-ipc",
          "--unshare-uts", "--unshare-cgroup-try"]
    if tiocsti_allowed():
        bw.append("--new-session")
    for k in BUS_VARS:
        bw += ["--unsetenv", k]
    bw += bwrap_mounts(t)
    bw += ["--info-fd", "3", "--block-fd", "4"]
    agent = list(t.get("agent") or [])
    if not agent:
        raise SandboxError("the ticket names no agent to run")
    argv += bw + agent
    return argv, inside_env(t, environ, scope)


# --- network -------------------------------------------------------------------------------------------------------

def _ip(a: str) -> "ipaddress._BaseAddress":
    try:
        return ipaddress.ip_address(a)
    except ValueError:
        raise SandboxError(f"not an address: {a!r}") from None


def nft_rules(host_addrs: list, gateway: "tuple | None") -> str:
    """The namespace's output filter (spec, "Route out"): pasta's DNS first, the gateway when it is on a private
    range, then drops for the host's own addresses, link-local and the private ranges."""
    addrs = [_ip(a) for a in host_addrs]
    v4 = sorted({str(a) for a in addrs if a.version == 4 and not a.is_loopback})
    v6 = sorted({str(a) for a in addrs if a.version == 6 and not a.is_loopback and not a.is_link_local})
    lines = ["table inet lane_sandbox {", "  chain out {", "    type filter hook output priority 0; policy accept;",
             f"    ip daddr {DNS} accept"]
    if gateway:
        try:
            g = ipaddress.ip_address(str(gateway[0]))
        except ValueError:
            g = None  # a name: on the internet, which stays open in phase 1
        if g is not None and not g.is_loopback:
            fam = "ip" if g.version == 4 else "ip6"
            lines.append(f"    {fam} daddr {g} tcp dport {int(gateway[1])} accept")
    if v4:
        lines.append("    ip daddr { " + ", ".join(v4) + " } drop")
    if v6:
        lines.append("    ip6 daddr { " + ", ".join(v6) + " } drop")
    lines += ["    ip daddr { " + ", ".join(PRIVATE_V4) + " } drop",
              "    ip6 daddr { " + ", ".join(PRIVATE_V6) + " } drop", "  }", "}"]
    return "\n".join(lines) + "\n"


def host_addrs(proc: Path = Path("/proc")) -> list:
    """The host's own non-loopback addresses, from /proc (no `ip` binary needed): IPv4 LOCAL entries of fib_trie,
    IPv6 from if_inet6. Never printed: a fact about the machine."""
    out = []
    try:
        last = None
        for line in (Path(proc) / "net" / "fib_trie").read_text().splitlines():
            s = line.strip()
            m = re.match(r"[|+]-- (\d+\.\d+\.\d+\.\d+)$", s)
            if m:
                last = m.group(1)
            elif s.startswith("/32 host LOCAL") and last and not last.startswith("127."):
                if last not in out:
                    out.append(last)
    except OSError:
        pass
    try:
        for line in (Path(proc) / "net" / "if_inet6").read_text().splitlines():
            hexa = line.split()[0]
            a = ipaddress.ip_address(bytes.fromhex(hexa))
            if not a.is_loopback and str(a) not in out:
                out.append(str(a))
    except (OSError, ValueError, IndexError):
        pass
    return out


def host_pid(mntns: int, nspid: int, proc: Path = Path("/proc")) -> int:
    """The host pid of the process in mount namespace `mntns` whose second NSpid field (pasta's pid namespace) is
    `nspid`. Raises SandboxError for none or more than one: the agent is then never released."""
    want = f"mnt:[{int(mntns)}]"
    found = []
    try:
        pids = os.listdir(proc)
    except OSError:
        pids = []
    for p in pids:
        if not p.isdigit():
            continue
        try:
            if os.readlink(f"{proc}/{p}/ns/mnt") != want:
                continue
            with open(f"{proc}/{p}/status") as fh:
                status = fh.read().splitlines()
            for line in status:
                if line.startswith("NSpid:"):
                    f = line.split()[1:]
                    if len(f) >= 2 and f[1] == str(int(nspid)):
                        found.append(int(p))
        except OSError:
            continue
    if len(found) != 1:
        raise SandboxError(f"the sandbox's process was found {len(found)} times (by mount namespace and pid), "
                           "not once: the agent is not started")
    return found[0]


# --- history and CI ------------------------------------------------------------------------------------------------

def norm_private(paths: list) -> list:
    out = []
    for p in paths or []:
        p = str(p).strip().lower()
        while p.startswith("./"):
            p = p[2:]
        p = p.strip("/")
        if p and p not in out:
            out.append(p)
    return out


def changed_paths(repo: Path, revs: list) -> set:
    """Every path any delivered commit adds or changes, against each parent (-m), the root included: a file added
    and removed on a merged side branch is still seen."""
    commits = subprocess.run(["git", "-C", str(repo), "rev-list", *revs], check=True, capture_output=True,
                             text=True, stdin=subprocess.DEVNULL).stdout
    if not commits.strip():
        return set()
    out = subprocess.run(["git", "-C", str(repo), "-c", "core.quotePath=false", "diff-tree", "--stdin", "-r", "-m",
                          "--root", "--no-commit-id", "--name-only", "--no-renames"],
                         input=commits, check=True, capture_output=True, text=True).stdout
    return {line for line in out.splitlines() if line}


def history_hits(repo: Path, revs: list, private: list) -> list:
    """Paths in the delivered commits that are --private paths (any depth below them) or secret env files at any
    depth (a template name excepted); compared lower-cased."""
    priv = norm_private(private)
    hits = []
    for path in sorted(changed_paths(repo, revs)):
        low = path.lower()
        base = low.rsplit("/", 1)[-1]
        if any(low == p or low.startswith(p + "/") for p in priv):
            hits.append(path)
        elif (base == ".env" or base.startswith(".env.")) and base not in TEMPLATES:
            hits.append(path)
    return hits


def ci_hits(repo: Path, revs: list) -> list:
    hits = []
    for p in sorted(changed_paths(repo, revs)):
        low = p.lower()
        if low.startswith(CI_PREFIXES) or low.rsplit("/", 1)[-1] in CI_FILES:
            hits.append(p)
    return hits


# --- the broker's read of the outbox -------------------------------------------------------------------------------

def safe_read(path: Path, cap: int, dst: Path) -> None:
    """Copy a lane-written file into a broker-only place: opened no-follow and non-blocking, a regular file of at
    most `cap` bytes, also while it is read. Raises SandboxError with the reason; dst is then absent."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as e:
        why = {errno.ELOOP: "a link", errno.ENOENT: "missing", errno.ENXIO: "not a regular file"}.get(
            e.errno, errno.errorcode.get(e.errno, str(e.errno)))
        raise SandboxError(f"refused: {why}") from None
    tmp = Path(dst).with_name(f".{Path(dst).name}.part")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise SandboxError("refused: not a regular file")
        if st.st_size > cap:
            raise SandboxError(f"refused: {st.st_size} bytes, over the cap of {cap}")
        left = cap
        with open(tmp, "wb") as out:
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                left -= len(chunk)
                if left < 0:
                    raise SandboxError("refused: grew past the cap while read")
                out.write(chunk)
        os.replace(tmp, dst)
    finally:
        os.close(fd)
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
