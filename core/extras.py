"""Extras: what the owner adds to the agents' environments, per project and machine-wide (lc-7c6). Owner 2026-11-26:
"Should we somehow simplify adding 'extras'? like if someone wants skills or mcps provisioned to their
lanes/coordinators?", then "do it, centralize and improve these things", "perhaps custom lane personas, too?".

Kinds (one entry each, its id `<kind>:<name>`):
  skill    a skill from a git repo pinned at a full commit, `<git-url>@<commit>[:<path>]`
  mcp      an MCP server run by a command; a secret only as the NAME of an environment variable, never a value
  persona  a named lane role the coordinator can brief a lane with (a prompt file, a default tier and harness)
  plugin   a Claude or OpenCode plugin, pinned
Agent hooks, permission rules and env values are not kinds yet: they need their own design (lc-yw1g, EXT-1b).

Scope per extra: lanes, coordinator, reviewer (reviewer and design reviewer), or all. Stored in the project's own
settings store (<config>/lane-control/projects/<key>/extras.json, beside its tier table) and, machine-wide, in
<config>/lane-control/extras.json: never in a project repo. A project's extras add to the machine's; one with the same
id takes the machine's place for that project. The built-in design skills (core/design-skills.tsv) are the default
machine extras: installed for the whole user (~/.agents/skills, and ~/.claude/skills unless Claude's own plugin has
the skill) exactly as before; `machine extras remove` turns one off.

Every other extra goes only where the sessions of its role read it:
  coordinator, Claude    files in the project's settings store, given at its launch: --mcp-config <mcp.json>,
                         --settings <settings.json> (enabledPlugins) and --plugin-dir <plugin> (its skills). Nothing
                         goes in the main checkout's .claude/ or .mcp.json: Claude reads the main checkout's
                         .claude/settings.local.json, and its .claude/skills, from every linked worktree.
  coordinator, OpenCode  the main checkout's .opencode/opencode.json and .opencode/skills/ (OpenCode reads a
                         project's .opencode/ from the session's own worktree up to its root; a lane's worktree is
                         not under the main checkout)
  lane, reviewer         its own worktree, written by `lane-control lane prepare` before the worker starts:
                         .mcp.json, .claude/settings.json (enabledMcpjsonServers: only the servers lane-control wrote,
                         enabledPlugins; .claude/settings.local.json when the project commits settings.json),
                         .claude/skills/<name>, .opencode/opencode.json, .opencode/skills/<name>

In a folder, lane-control writes only files it creates and owns whole: never a committed file, never a file that
was there before (or changed since it wrote it), never through a symlink. Applying is all or nothing: on any problem
(a fetch, a pin, a changed file, a committed or foreign file) the folder is left with none of lane-control's extras and
every problem is named. What was written is recorded in <state>/lane-control/extras/<folder>.json (with the
worktree's identity), refresh applies every recorded worktree of the project again (so a removed extra is really
taken out), and the repo's git exclude names exactly the files the records hold. Setup's onboarding agents run with no
MCP server and no settings files (project-setup agent_cmd), so they get no extras.

Read by core/scripts/lane-extras (the commands), core/scripts/project-setup (apply, render, launch, check) and
core/scripts/lane-control (doctor)."""
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KINDS = ("skill", "mcp", "persona", "plugin")
SCOPES = ("lanes", "coordinator", "reviewer")
BUILTIN_PERSONAS = frozenset({"coordinator", "reviewer", "design-reviewer", "debugger", "machine-coordinator",
                              "design-lead", "lanes", "lane", "all"})
TIERS = ("swift", "standard", "deep")
HARNESSES = ("claude", "opencode")
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
MCP_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}")
COMMIT = re.compile(r"[0-9a-f]{40}")
SPEC = re.compile(r"(?P<url>.+)@(?P<commit>[0-9a-f]{40})(?::(?P<path>.*))?")
REF = re.compile(r"\$\{([A-Z][A-Z0-9_]{0,127})\}")
SECRET_WORDS = frozenset({"KEY", "KEYS", "TOKEN", "TOKENS", "SECRET", "SECRETS", "PASSWORD", "PASSWD", "PASS", "PWD",
                          "CREDENTIAL", "CREDENTIALS", "CRED", "CREDS", "AUTH", "AUTHORIZATION", "PRIVATE", "COOKIE",
                          "SESSION", "SIGNATURE", "SIG", "SALT", "CERT", "PAT", "BEARER", "APIKEY", "OTP", "DSN",
                          "WEBHOOK"})
TOKEN_VALUE = re.compile(r"(sk-|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|glpat-|xox[abposr]-|AKIA|ASIA|AIza|ya29\.|"
                         r"eyJ[A-Za-z0-9_-]{8,}\.|-----BEGIN|hooks\.slack\.com/|discord(app)?\.com/api/webhooks)")
USERINFO = re.compile(r"://[^/@\s]*:[^/@\s]*@|://[^/@\s]{16,}@")  # user:password@, or a token as the user
SECRET_QUERY = re.compile(r"[?&;](access_token|token|key|api_key|apikey|secret|password|pass|sig|signature|auth|"
                          r"client_secret)=(?!\$\{)", re.I)
AUTH_TEXT = re.compile(r"(authorization\s*:|\bbearer\s+|\bbasic\s+[A-Za-z0-9+/=]{8,})", re.I)
PACKAGE_AT_VERSION = re.compile(r"(@[A-Za-z0-9._-]+/)?[A-Za-z0-9._-]+@[0-9][A-Za-z0-9._+-]*")
FILE_LIKE = re.compile(r"[^\s:]*\.(py|js|mjs|cjs|ts|sh|bash|rb|pl|php|lua|exe|bin|jar)$", re.I)
PERSONAS_DIR = ".lane-control/personas"
EXCLUDE_BEGIN = "# lane-control extras: begin (files lane-control extras wrote; never committed)"
EXCLUDE_END = "# lane-control extras: end"
PLUGIN_NAME = "lane-control-extras"  # the coordinator's skills reach Claude as this session-only plugin


class ExtrasError(Exception):
    """An extra refused, or a store that cannot be read: the message says why and what to do. It never carries a
    value an extra was refused for."""


# --- where things live -------------------------------------------------------------------------------------------

def home() -> Path:
    return Path(os.environ.get("HOME") or str(Path.home()))


def config_home() -> Path:
    v = os.environ.get("XDG_CONFIG_HOME")
    return Path(v) if v and os.path.isabs(v) else home() / ".config"


def state_home() -> Path:
    v = os.environ.get("XDG_STATE_HOME")
    return Path(v) if v and os.path.isabs(v) else home() / ".local" / "state"


def machine_file() -> Path:
    return config_home() / "lane-control" / "extras.json"


def project_file(store: Path) -> Path:
    return Path(store) / "extras.json"


def default_sources() -> Path:
    """The default design skills' checkouts (as before this mechanism)."""
    return home() / ".agents" / "skill-sources"


def extra_sources() -> Path:
    """The owner's pinned skills, apart from the defaults' so neither pass ever removes the other's."""
    return home() / ".agents" / "extras-sources" / "skills"


def launch_dir(store: Path) -> Path:
    """The coordinator's own Claude files, given at its launch (never read by a lane)."""
    return Path(store) / "extras" / "coordinator"


def records_dir() -> Path:
    return state_home() / "lane-control" / "extras"


def encode(folder: Path) -> str:
    return re.sub(rb"[^A-Za-z0-9]", b"-", os.fsencode(Path(folder).resolve())).decode()


def record_file(folder: Path) -> Path:
    return records_dir() / f"{encode(folder)}.json"


def store_for(repo: Path) -> Path:
    """The project's settings store, keyed as project-setup keys it (store_key)."""
    real = Path(repo).resolve()
    key = f"{real.name}-{hashlib.sha256(str(real).encode()).hexdigest()[:10]}"
    return config_home() / "lane-control" / "projects" / key


def known() -> list:
    """(root, settings) of every project set up on this machine; unreadable stores are skipped."""
    out = []
    for f in sorted((config_home() / "lane-control" / "projects").glob("*/settings.json")):
        try:
            st = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(st, dict) and isinstance(st.get("root"), str):
            out.append((Path(st["root"]), st))
    return out


def git(folder: Path, *args, timeout: int = 30):
    try:
        return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.TimeoutExpired):
        return None


def common_dir(folder: Path) -> "Path | None":
    """The git common dir of the repo holding folder (every worktree of a repo shares it), or None."""
    d = Path(folder)
    d = d if d.is_dir() else d.parent
    r = git(d, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(r.stdout.strip()).resolve() if r and r.returncode == 0 and r.stdout.strip() else None


def worktrees(repo: Path) -> list:
    r = git(repo, "worktree", "list", "--porcelain")
    if not r or r.returncode != 0:
        return [Path(repo)]
    return [Path(line[9:]) for line in r.stdout.splitlines() if line.startswith("worktree ")] or [Path(repo)]


def checkouts() -> list:
    """Every checkout of every project on this machine: roots, attached repos and all their worktrees."""
    out = []
    for root, st in known():
        for r in [root, *[Path(a) for a in st.get("attached") or [] if isinstance(a, str)]]:
            out += [r, *worktrees(r)] if r.is_dir() else [r]
    seen, uniq = set(), []
    for p in out:
        k = os.path.abspath(p)
        if k not in seen:
            seen.add(k)
            uniq.append(Path(k))
    return uniq


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def native(root: Path, st: dict) -> bool:
    return st.get("orchestrator", "native-orca") == "native-orca" and not (Path(root) / ".cotal").is_dir()


def find_project(arg: str) -> tuple:
    """(root, settings, store) of a project named by its folder or its name; ExtrasError when there is none."""
    d = Path(arg).expanduser()
    if d.is_dir():
        r = git(d, "rev-parse", "--show-toplevel")
        if not r or r.returncode != 0:
            raise ExtrasError(f"{d} is not a git repository")
        root = Path(r.stdout.strip()).resolve()
        for kroot, st in known():
            if kroot.resolve() == root:
                return root, st, store_for(root)
        for kroot, st in known():
            if str(root) in [str(Path(a).resolve()) for a in st.get("attached") or [] if isinstance(a, str)]:
                raise ExtrasError(f"{root} is attached to the project at {kroot}: its extras are that project's")
        raise ExtrasError(f"{root} is no project set up on this machine: run lane-control project setup {root} first")
    found = [(r, st) for r, st in known() if slug(arg) in (slug(r.name), slug(st.get("name") or ""))]
    if len(found) != 1:
        raise ExtrasError(f"{arg} is not a folder, and " + (
            f"matches {len(found)} projects: " + ", ".join(str(r) for r, _ in found) if found else
            "is no project set up on this machine"))
    return found[0][0].resolve(), found[0][1], store_for(found[0][0])


def write_file(path: Path, text: str, mode: int = 0o644) -> None:
    """Through a temp file and a rename beside it. A symlink at the path is replaced, never followed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def digest(text) -> str:
    return hashlib.sha256(text if isinstance(text, bytes) else text.encode()).hexdigest()


def sha256(path: Path) -> str:
    return digest(Path(path).read_bytes())


# --- the store -----------------------------------------------------------------------------------------------------

def load(path: Path) -> dict:
    """{"extras": [entry...], "off": [id...]} of one store file; empty when it is not there."""
    try:
        data = json.loads(Path(path).read_text()) if Path(path).exists() else {}
    except (OSError, ValueError) as e:
        raise ExtrasError(f"cannot read {path}: {e}; fix or remove it")
    if not isinstance(data, dict) or not isinstance(data.get("extras", []), list) or \
            not isinstance(data.get("off", []), list):
        raise ExtrasError(f"{path} is not an extras file (an object with an `extras` list); fix or remove it")
    return {"extras": [e for e in data.get("extras", []) if isinstance(e, dict) and e.get("kind") in KINDS
                       and isinstance(e.get("name"), str)],
            "off": [x for x in data.get("off", []) if isinstance(x, str)]}


def save(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    body = {"version": 1, "extras": data["extras"], "off": sorted(set(data.get("off", [])))}
    write_file(path, json.dumps(body, indent=2, ensure_ascii=False) + "\n", 0o600)


def ident(e: dict) -> str:
    return f"{e['kind']}:{e['name']}"


def default_rows() -> list:
    """core/design-skills.tsv (LANE_DESIGN_SKILLS replaces it): (name, url, commit, path, licence, claude plugin)."""
    f = Path(os.environ.get("LANE_DESIGN_SKILLS") or ROOT / "core" / "design-skills.tsv")
    rows = []
    for line in f.read_text().splitlines():
        parts = line.split("\t")
        if line.strip() and not line.startswith("#") and len(parts) == 6:
            rows.append(tuple(parts))
    return rows


def defaults() -> list:
    """The default machine extras: the built-in design skills, for every role."""
    return [{"kind": "skill", "name": r[0], "url": r[1], "commit": r[2], "path": r[3], "licence": r[4],
             "claude_plugin": r[5], "for": list(SCOPES), "origin": "default"} for r in default_rows()]


def effective(store: "Path | None") -> list:
    """Every extra in force for a project (store None: the machine's alone): the defaults not turned off, the
    machine's, then the project's, a later one with the same id in the earlier one's place. Each carries `origin`."""
    machine = load(machine_file())
    by_id = {}
    for e in defaults():
        if ident(e) not in machine["off"]:
            by_id[ident(e)] = e
    for origin, data in (("machine", machine), ("project", load(project_file(store)) if store else None)):
        for e in (data or {}).get("extras", []):
            by_id[ident(e)] = {**e, "origin": origin}
    return list(by_id.values())


def for_scope(entries: list, scope: str) -> list:
    return [e for e in entries if scope in (e.get("for") or SCOPES)]


def user_level(e: dict) -> bool:
    """A default design skill: installed for the whole user, as before this mechanism."""
    return e.get("origin") == "default"


# --- adding: parse and refuse (never echoing a value refused) -----------------------------------------------------

def url_problem(url: str) -> "str | None":
    """Why a git URL is refused (an owner's extra): https, ssh, git@host:path or file only; no option, helper or
    credential in it."""
    if not url or url.startswith("-") or any(c.isspace() or ord(c) < 32 for c in url) or "::" in url:
        return "it is not a plain git URL (no leading dash, spaces or `::` helpers)"
    if USERINFO.search(url) or TOKEN_VALUE.search(url) or SECRET_QUERY.search(url):
        return "it carries a password or token; give a URL without one (git's credential helper supplies it)"
    if url.startswith(("https://", "ssh://", "file://")) or re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[^-].*",
                                                                          url):
        return None
    return "it is not https://, ssh://, git@host:path or file:// (http and other transports are refused)"


def path_problem(rel: str) -> "str | None":
    if rel and (rel.startswith("/") or any(p in ("", ".", "..") for p in rel.split("/"))):
        return "the path in the repo is not a plain relative path"
    return None


def git_spec(text: str) -> dict:
    m = SPEC.fullmatch(text or "")
    if not m:
        raise ExtrasError("a skill is <git-url>@<full 40-hex commit>[:<path>] (a branch or short sha is not a pin)")
    why = url_problem(m["url"]) or path_problem(m["path"] or "")
    if why:
        raise ExtrasError(f"--skill is refused: {why}")
    return {"url": m["url"], "commit": m["commit"], "path": m["path"] or ""}


def secretish_name(name: str) -> bool:
    words = set(re.split(r"[_\W]+", name.upper())) - {""}
    return bool(words & SECRET_WORDS) or name.upper().endswith(("KEY", "TOKEN", "SECRET", "PASSWORD"))


def opaque(value: str) -> bool:
    """A long run of letters and digits with nothing else: how a key or token looks."""
    v = value.strip()
    if PACKAGE_AT_VERSION.fullmatch(v) or "/" in v or "://" in v:
        return False
    return len(v) >= 32 and not re.search(r"\s", v) and bool(re.search(r"[0-9]", v)) and \
        bool(re.search(r"[A-Za-z]", v))


def arg_problem(args: list) -> "str | None":
    """Why an MCP command line is refused for holding a secret (`${NAME}` references are fine), or None."""
    for i, a in enumerate(args):
        bare = REF.sub("", a)
        if TOKEN_VALUE.search(bare) or USERINFO.search(bare) or SECRET_QUERY.search(a) or AUTH_TEXT.search(bare) or \
                opaque(bare):
            return f"argument {i + 1} looks like it carries a key, token or password"
        m = re.fullmatch(r"(-{1,2})?([A-Za-z_][A-Za-z0-9_.-]*)(=(.*))?", a, re.S)
        if not m or not secretish_name(m[2].replace("-", "_").replace(".", "_")):
            continue
        if m[3] is not None:
            if not REF.fullmatch(m[4] or ""):
                return f"argument {i + 1} sets a secret-named option or variable to a value"
        elif m[1] and i + 1 < len(args) and not args[i + 1].startswith("-") and not REF.fullmatch(args[i + 1]):
            return f"argument {i + 2} is the value of the secret-named option {m[1]}{m[2]}"
    return None


def inside_checkout(path: Path) -> "Path | None":
    """The project checkout (any root, attached repo or worktree, by path, and by its git common dir) that holds
    path as given or as its links resolve; None when it is in none."""
    given = Path(os.path.abspath(path))
    real = Path(os.path.realpath(path))
    homes = checkouts()
    for p in (given, real):
        for c in homes:
            c_real = Path(os.path.realpath(c))
            for base in (Path(os.path.abspath(c)), c_real):
                if p == base or base in p.parents:
                    return c
    commons = {common_dir(c) for c in homes if c.is_dir()} - {None}
    for p in (given, real):
        cd = common_dir(p)
        if cd is not None and cd in commons:
            return p
    return None


def owners_file(given: str, allow_repo: bool, what: str, executable: bool) -> tuple:
    """(absolute path, sha256, warning or '') of a file an extra names; refused when missing, or inside a project
    checkout (as given or resolved) without allow_repo."""
    p = Path(given).expanduser()
    if not p.is_absolute():
        raise ExtrasError(f"{what} must be an absolute path: a relative one resolves in the session's folder, "
                          "which a lane controls")
    p = Path(os.path.abspath(p))
    if not p.is_file():
        raise ExtrasError(f"{what} {p} is not a file")
    if executable and not os.access(p, os.X_OK):
        raise ExtrasError(f"{what} {p} is not executable (chmod +x it)")
    inside = inside_checkout(p)
    warning = ""
    if inside is not None:
        if not allow_repo:
            raise ExtrasError(f"{what} {p} is inside the project checkout {inside} (as given or through a link): a "
                              "lane can change it. Keep it outside every project repo; --allow-repo-path takes it "
                              "anyway")
        warning = (f"warning: {what} {p} is inside the project checkout {inside}; it is pinned by its hash, and a "
                   "lane that changes it stops the extra until you add it again")
    return str(p), sha256(p), warning


def parse_plugin(text: str) -> dict:
    harness, _, rest = (text or "").partition(":")
    source, _, pin = rest.rpartition("@")
    if harness not in HARNESSES or not source or not pin:
        raise ExtrasError("--plugin is claude:<plugin>@<marketplace>@<version> or opencode:<package>@<version>")
    if harness == "claude":
        if not re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9._-]+", source) or not re.fullmatch(r"[A-Za-z0-9._+-]+", pin):
            raise ExtrasError("--plugin: a Claude plugin is <plugin>@<marketplace>@<version or commit>")
    elif not re.fullmatch(r"(@[a-z0-9._-]+/)?[a-z0-9._-]+", source) or \
            not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", pin):
        raise ExtrasError("--plugin: an OpenCode plugin is <npm package>@<exact version> (no range, no latest)")
    return {"harness": harness, "source": source, "pin": pin}


def parse_scope(words: list) -> list:
    out = set()
    for w in words or ["all"]:
        for x in w.split(","):
            if x == "all":
                out |= set(SCOPES)
            elif x in SCOPES:
                out.add(x)
            else:
                raise ExtrasError(f"--for {x!r}: it takes lanes, coordinator, reviewer or all")
    return [s for s in SCOPES if s in out]


def make(kind: str, value, opts: dict) -> tuple:
    """(entry, warnings) for one `extras add`. opts: name, for, command (list), env (names), prompt_file, tier,
    harness, allow_repo_path."""
    scope = parse_scope(opts.get("for"))
    warnings = []
    e = {"kind": kind, "for": scope}
    name = opts.get("name")
    allow_repo = bool(opts.get("allow_repo_path"))
    if kind == "skill":
        e.update(git_spec(value))
        name = name or Path(e["path"] or re.sub(r"\.git$", "", e["url"].rstrip("/"))).name.lower()
        if not NAME.fullmatch(name or ""):
            raise ExtrasError("the skill's name is not lowercase letters, digits and dashes: give --name")
    elif kind == "mcp":
        name = value
        if not MCP_NAME.fullmatch(name or ""):
            raise ExtrasError("an MCP server's name is letters, digits, - and _ only")
        cmd = list(opts.get("command") or [])
        if not cmd:
            raise ExtrasError("an MCP server needs its command after `--`: --mcp <name> [--env NAME]... -- "
                              "<command> [args]")
        why = arg_problem(cmd)
        if why:
            raise ExtrasError(f"--mcp is refused: {why}. Give the secret as --env NAME (its name only) and pass it "
                              "as ${NAME}, or let the server read it from its environment")
        files = {}
        for i, a in enumerate(cmd):
            if REF.search(a):
                continue
            if a.startswith("/") or a.startswith("~"):
                if i == 0 or Path(a).expanduser().exists():
                    path, h, warn = owners_file(a, allow_repo, "the MCP command" if i == 0 else "an MCP argument",
                                                i == 0)
                    cmd[i], files[path] = path, h
                    warnings += [warn] if warn else []
            elif (i == 0 and "/" in a) or (not a.startswith("-") and not PACKAGE_AT_VERSION.fullmatch(a) and
                                           "://" not in a and (a.startswith(".") or FILE_LIKE.fullmatch(a))):
                raise ExtrasError(f"argument {i + 1} of the MCP command is a relative path: it resolves in the "
                                  "session's folder, which a lane controls. Give an absolute path outside every "
                                  "project")
        envs = []
        for n in opts.get("env") or []:
            if "=" in n:
                raise ExtrasError("--env takes a NAME only (for example GITHUB_TOKEN): the value is never stored, the "
                                  "server gets it from the agent's environment")
            if not ENV_NAME.fullmatch(n) or opaque(n) or TOKEN_VALUE.search(n):
                raise ExtrasError("--env takes an environment variable NAME in capitals (for example GITHUB_TOKEN)")
            envs.append(n)
        envs += [r for a in cmd for r in REF.findall(a) if r not in envs]
        e.update(command=cmd[0], args=cmd[1:], env=envs, files=files)
        warnings.append("note: an interpreter's own module or script lookup (python3 -m x, npx x) is not pinned: "
                        "only absolute paths given on the command line are")
    elif kind == "persona":
        name = value
        if not NAME.fullmatch(name or "") or name in BUILTIN_PERSONAS:
            raise ExtrasError("a persona's name is lowercase letters, digits and dashes, and not a built-in role "
                              f"({', '.join(sorted(BUILTIN_PERSONAS - {'lanes', 'lane', 'all'}))})")
        if not opts.get("prompt_file"):
            raise ExtrasError("a persona needs --prompt-file <path>")
        tier, harness = opts.get("tier") or "standard", opts.get("harness") or "claude"
        if tier not in TIERS or harness not in HARNESSES:
            raise ExtrasError(f"--tier takes {', '.join(TIERS)}; --harness takes {', '.join(HARNESSES)}")
        path, h, warn = owners_file(opts["prompt_file"], allow_repo, "the prompt file", False)
        warnings += [warn] if warn else []
        if not Path(path).read_text().strip():
            raise ExtrasError(f"the prompt file {path} is empty")
        e.update(prompt_file=path, sha256=h, tier=tier, harness=harness)
        e["for"] = ["lanes"]
    elif kind == "plugin":
        e.update(parse_plugin(value))
        name = f"{e['harness']}:{e['source']}"
    else:
        raise ExtrasError(f"no extra kind {kind!r} (kinds: {', '.join(KINDS)})")
    e["name"] = name
    return e, warnings


def add(path: Path, entry: dict) -> str:
    """Put entry in the store file (replacing one with its id); 'added' or 'replaced'."""
    data = load(path)
    was = [e for e in data["extras"] if ident(e) == ident(entry)]
    data["extras"] = [e for e in data["extras"] if ident(e) != ident(entry)] + [entry]
    data["off"] = [x for x in data["off"] if x != ident(entry)]
    save(path, data)
    return "replaced" if was else "added"


def find(entries: list, word: str) -> list:
    return [e for e in entries if word in (ident(e), e["name"])]


def remove(path: Path, word: str, machine: bool) -> str:
    """Take an extra out of a store file; on the machine a default is turned off. What happened, in words."""
    data = load(path)
    hit = find(data["extras"], word)
    if len(hit) > 1:
        raise ExtrasError(f"{word!r} names {', '.join(ident(e) for e in hit)}: give the id")
    if hit:
        data["extras"] = [e for e in data["extras"] if ident(e) != ident(hit[0])]
        save(path, data)
        return f"removed {ident(hit[0])}"
    if machine:
        d = find(defaults(), word)
        if d:
            if ident(d[0]) in data["off"]:
                return f"{ident(d[0])} is already off"
            data["off"].append(ident(d[0]))
            save(path, data)
            return f"turned off the default {ident(d[0])} (lane-control machine extras add can bring it back)"
    raise ExtrasError(f"no extra {word!r} in {path}")


def describe(e: dict) -> str:
    k = e["kind"]
    if k == "skill":
        return f"{e['url']}@{e['commit'][:12]}" + (f":{e['path']}" if e.get("path") else "")
    if k == "mcp":
        env = f" (env {', '.join(e['env'])})" if e.get("env") else ""
        return shlex.join([e["command"], *e.get("args", [])]) + env
    if k == "persona":
        return f"{e['prompt_file']} (tier {e['tier']}, {e['harness']})"
    return f"{e['harness']}:{e['source']}@{e['pin']}"


# --- pinned skill sources ------------------------------------------------------------------------------------------

def fetch(url: str, commit: str, path: str, dest: Path) -> "str | None":
    """A sparse checkout of `path` (the whole repo when '') at `commit` into dest, made by rename so never half
    there; SKILL.md must be in it. None when done, else why not."""
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    want = f"{path}/SKILL.md" if path else "SKILL.md"
    try:
        tmp.mkdir(parents=True)
        steps = [("init", "-q"), ("remote", "add", "origin", url)]
        if path:
            steps.append(("sparse-checkout", "set", "--no-cone", f"/{path.strip('/')}/", "/LICENSE*", "/COPYING*"))
        steps += [("fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", commit),
                  ("checkout", "-q", "--detach", "FETCH_HEAD")]
        for step in steps:
            r = git(tmp, *step, timeout=300)
            if r is None or r.returncode != 0:
                last = ((r.stderr or r.stdout).strip().splitlines() if r else []) or ["timed out"]
                return f"git {step[0]} failed: {last[-1]}"
        if git(tmp, "rev-parse", "HEAD").stdout.strip() != commit:
            return f"fetched commit is not {commit[:12]}"
        if not (tmp / want).is_file():
            return f"no {want} at that commit"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(tmp, dest)
        return None
    except OSError as e:
        return str(e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def source_dir(e: dict) -> Path:
    """The checkout of a skill: the defaults' as before; an owner's keyed by URL, commit and path."""
    if user_level(e):
        return default_sources() / f"{e['name']}@{e['commit'][:12]}"
    key = digest(f"{e['url']}\0{e['path']}")[:10]
    return extra_sources() / f"{e['name']}@{e['commit'][:12]}-{key}"


def skill_dir(e: dict) -> Path:
    src = source_dir(e)
    return src / e["path"] if e.get("path") else src


def source_state(e: dict) -> "str | None":
    """None when the skill's checkout is there at its pinned commit with nothing changed in it, else what is wrong."""
    src = source_dir(e)
    if not (skill_dir(e) / "SKILL.md").is_file():
        return f"commit {e['commit'][:12]} not fetched"
    head = git(src, "rev-parse", "HEAD")
    if head is None or head.stdout.strip() != e["commit"]:
        return f"{src} is not at {e['commit'][:12]} any more"
    st = git(src, "status", "--porcelain", "--untracked-files=all")
    if st is None or st.returncode != 0 or st.stdout.strip():
        return f"{src} changed since it was fetched"
    return None


def ensure_skill(e: dict) -> "str | None":
    """The skill's pinned checkout, fetched when it is not there; None when it is right, else what is wrong."""
    if not (skill_dir(e) / "SKILL.md").is_file():
        why = fetch(e["url"], e["commit"], e.get("path", ""), source_dir(e))
        if why:
            return why
    return source_state(e)


# --- the default design skills, for the whole user (as project-setup always installed them) ----------------------

def claude_plugins() -> dict:
    """Claude Code's installed plugins (~/.claude/plugins/installed_plugins.json): {`plugin@marketplace`: records}."""
    try:
        data = json.loads((home() / ".claude" / "plugins" / "installed_plugins.json").read_text())
    except (OSError, ValueError):
        return {}
    plugins = data.get("plugins", {}) if isinstance(data, dict) else {}
    return plugins if isinstance(plugins, dict) else {}


def bad_default(row: tuple, override: bool) -> "str | None":
    """Why a design-skills row is refused, or None. The shipped table fetches over https only; a LANE_DESIGN_SKILLS
    table (tests' local stand-ins for upstream) may also use file://."""
    name, url, commit, path = row[:4]
    if not NAME.fullmatch(name):
        return "the name is not lowercase letters, digits and dashes"
    if not COMMIT.fullmatch(commit):
        return "the commit is not a full 40-hex commit"
    if any(part in ("", ".", "..") for part in path.split("/")):
        return "the path is not a plain relative path"
    schemes = ("https://", "file://") if override else ("https://",)
    if not url.startswith(schemes):
        return f"the URL is not {' or '.join(s[:-3] for s in schemes)}"
    return None


def ours_default(link: Path) -> bool:
    return link.is_symlink() and os.readlink(link).startswith(str(default_sources()) + os.sep)


def user_skills(entries: list) -> list:
    """The default design skills in ~/.agents/skills (where OpenCode v2 reads them), each a link into a sparse checkout
    of its repo at the pinned commit under ~/.agents/skill-sources/; Claude gets a link in ~/.claude/skills unless its
    own plugin carries the skill. One the owner installed is left alone; a fetch that fails is named, never a stop.
    A default turned off (or replaced by an extra of the owner's) loses only the links of ours. [(name, state)]."""
    skills, sources = home() / ".agents" / "skills", default_sources()
    plugins = set(claude_plugins())
    want = {e["name"]: e for e in entries if e["kind"] == "skill" and user_level(e)}
    override = bool(os.environ.get("LANE_DESIGN_SKILLS"))
    done = []
    for row in default_rows():
        link = skills / row[0]
        if row[0] not in want and NAME.fullmatch(row[0]) and ours_default(link):
            link.unlink()
            claude = home() / ".claude" / "skills" / row[0]
            if claude.is_symlink() and os.readlink(claude) == str(link):
                claude.unlink()
            done.append((row[0], "removed (turned off)"))
    for e in want.values():
        name = e["name"]
        why = bad_default((name, e["url"], e["commit"], e["path"]), override)
        if why:
            done.append((name, f"missing (refused: {why})"))
            continue
        link, target = skills / name, skill_dir(e)
        ours = ours_default(link)
        if (link.exists() or link.is_symlink()) and not ours:
            done.append((name, "exists (not lane-control's)"))
            continue
        if ours and os.readlink(link) == str(target) and (target / "SKILL.md").is_file():
            state = "exists"
        else:
            if not (target / "SKILL.md").is_file():
                why = fetch(e["url"], e["commit"], e["path"], source_dir(e))
                if why:
                    done.append((name, f"missing ({why})"))
                    continue
            skills.mkdir(parents=True, exist_ok=True)
            tmp_link = skills / f".{name}.{os.getpid()}.link"
            tmp_link.unlink(missing_ok=True)
            tmp_link.symlink_to(target)
            os.replace(tmp_link, link)
            state = "updated" if ours else "created"
        for old in sources.glob(f"{name}@*"):  # the defaults' own folder only: an owner's pins live elsewhere
            if old.name != source_dir(e).name:
                shutil.rmtree(old, ignore_errors=True)
        claude = home() / ".claude" / "skills" / name
        if e.get("claude_plugin") not in plugins and not claude.exists() and not claude.is_symlink():
            claude.parent.mkdir(parents=True, exist_ok=True)
            claude.symlink_to(link)
        done.append((name, state))
    return done


# --- what a role's sessions get ---------------------------------------------------------------------------------

def mcp_problem(e: dict) -> "str | None":
    """None when an MCP server can be put in place as added: its command found, every pinned file unchanged."""
    cmd = e["command"]
    if "/" in cmd and not (os.path.isfile(cmd) and os.access(cmd, os.X_OK)):
        return f"the command {cmd} is not there or not executable"
    if "/" not in cmd and shutil.which(cmd) is None:
        return f"the command {cmd} is not found"
    for f, h in (e.get("files") or {}).items():
        if not os.path.isfile(f):
            return f"{f} is missing"
        if sha256(Path(f)) != h:
            return f"{f} changed since it was added (add it again to accept it)"
    return None


def claude_mcp(e: dict) -> dict:
    return {"type": "stdio", "command": e["command"], "args": e.get("args", []),
            **({"env": {n: "${%s}" % n for n in e["env"]}} if e.get("env") else {})}


def opencode_mcp(e: dict) -> dict:
    cmd = [REF.sub(lambda m: "{env:%s}" % m[1], a) for a in [e["command"], *e.get("args", [])]]
    return {"type": "local", "command": cmd, "enabled": True,
            **({"environment": {n: "{env:%s}" % n for n in e["env"]}} if e.get("env") else {})}


def wanted(entries: list) -> tuple:
    """(problems, by kind) for a folder's entries: what may go in, after every source and pin was checked."""
    es = [e for e in entries if not user_level(e) and e["kind"] != "persona"]
    problems = []
    for e in es:
        why = ensure_skill(e) if e["kind"] == "skill" else mcp_problem(e) if e["kind"] == "mcp" else None
        if why:
            problems.append(f"{ident(e)}: {why}")
    kinds = {k: [e for e in es if e["kind"] == k] for k in ("skill", "mcp", "plugin")}
    return problems, kinds


def json_text(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def opencode_files(kinds: dict) -> dict:
    files = {}
    cfg = {}
    if kinds["mcp"]:
        cfg["mcp"] = {e["name"]: opencode_mcp(e) for e in kinds["mcp"]}
    plugins = [f"{e['source']}@{e['pin']}" for e in kinds["plugin"] if e["harness"] == "opencode"]
    if plugins:
        cfg["plugin"] = plugins
    if cfg:
        files[".opencode/opencode.json"] = json_text({"$schema": "https://opencode.ai/config.json", **cfg})
    for e in kinds["skill"]:
        files[f".opencode/skills/{e['name']}"] = skill_dir(e)
    return files


def tracked(folder: Path, rel: str) -> bool:
    r = git(folder, "ls-files", "--", rel)
    return bool(r and r.returncode == 0 and r.stdout.strip())


def claude_worktree_files(folder: Path, kinds: dict) -> dict:
    """A lane's or reviewer's Claude files in its own worktree."""
    files = {}
    if kinds["mcp"]:
        files[".mcp.json"] = json_text({"mcpServers": {e["name"]: claude_mcp(e) for e in kinds["mcp"]}})
    settings = {}
    if kinds["mcp"]:
        settings["enabledMcpjsonServers"] = [e["name"] for e in kinds["mcp"]]  # only what this .mcp.json holds
    plugins = {e["source"]: True for e in kinds["plugin"] if e["harness"] == "claude"}
    if plugins:
        settings["enabledPlugins"] = plugins
    if settings:
        rel = ".claude/settings.json" if not tracked(folder, ".claude/settings.json") else \
            ".claude/settings.local.json"
        files[rel] = json_text(settings)
    for e in kinds["skill"]:
        files[f".claude/skills/{e['name']}"] = skill_dir(e)
    return files


def claude_launch_files(kinds: dict) -> dict:
    """The coordinator's Claude files, in its launch folder (paths relative to it)."""
    files = {}
    if kinds["mcp"]:
        files["mcp.json"] = json_text({"mcpServers": {e["name"]: claude_mcp(e) for e in kinds["mcp"]}})
    plugins = {e["source"]: True for e in kinds["plugin"] if e["harness"] == "claude"}
    if plugins:
        files["settings.json"] = json_text({"enabledPlugins": plugins})
    if kinds["skill"]:
        files["plugin/.claude-plugin/plugin.json"] = json_text({
            "name": PLUGIN_NAME, "version": "1.0.0",
            "description": "The coordinator's skill extras (lane-control project|machine extras), for this session"})
        for e in kinds["skill"]:
            files[f"plugin/skills/{e['name']}"] = skill_dir(e)
    return files


def launch_args(store: Path) -> list:
    """Claude flags that give the coordinator its own extras at launch (project-setup's launcher puts them in)."""
    d = launch_dir(store)
    out = []
    if (d / "mcp.json").is_file():
        out += ["--mcp-config", str(d / "mcp.json")]
    if (d / "settings.json").is_file():
        out += ["--settings", str(d / "settings.json")]
    if (d / "plugin" / ".claude-plugin" / "plugin.json").is_file():
        out += ["--plugin-dir", str(d / "plugin")]
    return out


def apply_launch(store: Path, entries: list, dry: bool = False) -> tuple:
    """The coordinator's Claude launch folder holding exactly what its entries give (all or nothing: on a problem
    it is emptied). (lines, problems)."""
    d = launch_dir(store)
    problems, kinds = wanted(entries)
    want = {} if problems else claude_launch_files(kinds)
    have = {}
    if d.is_dir():
        for p in d.rglob("*"):
            if p.is_symlink() or p.is_file():
                have[str(p.relative_to(d))] = Path(os.readlink(p)) if p.is_symlink() else p.read_text()
    lines = []
    for rel, content in want.items():
        if have.get(rel) != content:
            lines.append(f"{d / rel}: {'would write' if dry else 'wrote'} (the coordinator's, at its launch)")
    for rel in have:
        if rel not in want:
            lines.append(f"{d / rel}: {'would remove' if dry else 'removed'}")
    if not dry and lines:
        shutil.rmtree(d, ignore_errors=True)
        for rel, content in want.items():
            f = d / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, Path):
                f.symlink_to(content)
            else:
                write_file(f, content, 0o600)
    return lines, problems


# --- writing a folder: only files lane-control creates and owns whole ---------------------------------------------

def identity(folder: Path) -> str:
    """A worktree's identity: its .git entry's inode and change time (a new worktree at the same path is another)."""
    try:
        st = os.lstat(Path(folder) / ".git")
    except OSError:
        return ""
    return f"{st.st_ino}:{st.st_ctime_ns}"


def read_record(folder: Path) -> dict:
    try:
        rec = json.loads(record_file(folder).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(rec, dict) or rec.get("identity") != identity(folder):
        return {}  # another worktree now at this path: nothing in it is ours
    return rec


def unsafe(folder: Path, rel: str) -> "str | None":
    """Why a path under folder may not be written: a symlinked folder on the way (it would lead out of the folder)."""
    p = Path(folder)
    for part in Path(rel).parts[:-1]:
        p = p / part
        if p.is_symlink():
            return f"{p} is a symlink"
        if p.exists() and not p.is_dir():
            return f"{p} is not a folder"
    return None


def current(folder: Path, rel: str):
    """What is at a path now: a Path (a link's target), the text, or None."""
    f = Path(folder) / rel
    if f.is_symlink():
        return Path(os.readlink(f))
    if f.is_file():
        try:
            return f.read_text()
        except (OSError, UnicodeDecodeError):
            return "\0unreadable"
    return None if not f.exists() else "\0folder"


def mark(content) -> str:
    return f"link:{content}" if isinstance(content, Path) else digest(content)


def apply_folder(folder: Path, entries: list, scopes: list, claude: bool = True, dry: bool = False,
                 project: "Path | None" = None) -> tuple:
    """Make folder hold exactly what `entries` give a session there: Claude's worktree files (claude=True: a lane's
    or reviewer's) and OpenCode's. All or nothing: on any problem the folder keeps none of lane-control's files.
    (lines, problems). dry: change nothing, say what would change."""
    folder = Path(folder).resolve()
    rec = read_record(folder)
    had = {k: v for k, v in (rec.get("files") or {}).items() if isinstance(v, str)}
    problems, kinds = wanted(entries)
    want = {}
    if not problems:
        want = {**(claude_worktree_files(folder, kinds) if claude else {}), **opencode_files(kinds)}
    if not want and not had and not problems:
        return [], []
    for rel in sorted(set(want) | set(had)):
        why = unsafe(folder, rel)
        if why:
            problems.append(f"{rel}: {why}; lane-control writes nothing through it")
            continue
        now = current(folder, rel)
        if rel in want and tracked(folder, rel):
            problems.append(f"{rel} is committed in the project: lane-control never changes it")
        elif rel in want and now is not None and mark(now) != had.get(rel) and \
                not (isinstance(now, Path) and now == want[rel]) and now != want[rel]:
            problems.append(f"{folder / rel} is there and is not lane-control's (or changed since it was written); "
                            "left so")
    if problems:
        want = {}  # all or nothing: none of ours stays where something could not be done
    lines = []
    for rel in sorted(set(want) | set(had)):
        if unsafe(folder, rel):
            continue
        now = current(folder, rel)
        if rel in want:
            if now == want[rel]:
                continue
            lines.append(f"{folder / rel}: {'would write' if dry else 'wrote'}")
        elif now is not None and mark(now) == had[rel]:
            lines.append(f"{folder / rel}: {'would remove' if dry else 'removed'} (no longer an extra here)")
        elif now is not None:
            problems.append(f"{folder / rel} changed since lane-control wrote it; left so, take it out by hand")
    if dry:
        return lines, problems
    files = {rel: mark(c) for rel, c in want.items()}
    pending = {**had, **files}
    common = common_dir(folder)
    rec_body = {"folder": str(folder), "identity": identity(folder), "common": str(common or ""),
                "project": str(project or ""), "scopes": scopes}
    records_dir().mkdir(parents=True, exist_ok=True)
    write_file(record_file(folder), json_text({**rec_body, "files": pending, "pending": True}), 0o600)
    for rel in sorted(set(want) | set(had)):
        if unsafe(folder, rel):
            continue
        f, now = folder / rel, current(folder, rel)
        if rel in want and now != want[rel]:
            if f.is_symlink() or f.is_file():
                f.unlink()
            f.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(want[rel], Path):
                f.symlink_to(want[rel])
            else:
                write_file(f, want[rel])
        elif rel not in want and now is not None and mark(now) == had[rel]:
            f.unlink()
            for parent in f.parents:  # folders lane-control made and left empty
                if parent == folder or not parent.is_dir() or any(parent.iterdir()):
                    break
                parent.rmdir()
    kept = {rel: had[rel] for rel in had if rel not in want and current(folder, rel) is not None
            and mark(current(folder, rel)) == had[rel]}  # what could not be taken out stays recorded
    final = {**files, **kept}
    if final:
        write_file(record_file(folder), json_text({**rec_body, "files": final}), 0o600)
    else:
        record_file(folder).unlink(missing_ok=True)
    if common:
        exclude(common)
    return lines, problems


def exclude(common: Path) -> None:
    """Our own block in the repo's git exclude (shared by every worktree): exactly the files the records hold."""
    names = set()
    for f in records_dir().glob("*.json") if records_dir().is_dir() else []:
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("common") == str(common):
            names |= {f"/{rel}" for rel in rec.get("files") or {}}
    f = common / "info" / "exclude"
    text = f.read_text() if f.exists() else ""
    if EXCLUDE_BEGIN in text and EXCLUDE_END in text.split(EXCLUDE_BEGIN, 1)[1]:
        head, rest = text.split(EXCLUDE_BEGIN, 1)
        tail = rest.split(EXCLUDE_END, 1)[1].lstrip("\n")
    else:
        head, tail = text, ""
    head = head if not head or head.endswith("\n") else head + "\n"
    block = "\n".join((EXCLUDE_BEGIN, *sorted(names), EXCLUDE_END)) + "\n" if names else ""
    new = head + block + tail
    if new != text:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(new)


def recorded_worktrees(repo: Path) -> list:
    """(folder, record) of every worktree of the project that lane prepare wrote in; a record whose folder is gone
    or is another worktree now is dropped."""
    out = []
    for f in sorted(records_dir().glob("*.json")) if records_dir().is_dir() else []:
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("project") != str(Path(repo).resolve()) or \
                not set(rec.get("scopes") or []) & {"lanes", "reviewer"}:
            continue  # another project's, or the coordinator's own folder
        folder = Path(rec.get("folder", ""))
        if not folder.is_dir() or rec.get("identity") != identity(folder):
            f.unlink(missing_ok=True)
            continue
        out.append((folder, rec))
    return out


def prepare_entries(entries: list, scopes: list) -> list:
    chosen = {}
    for s in scopes:
        for e in for_scope(entries, s):
            chosen[ident(e)] = e
    return list(chosen.values())


# --- personas -----------------------------------------------------------------------------------------------------

def persona_text(e: dict, additions: str) -> "str | None":
    f = Path(e["prompt_file"])
    if not f.is_file() or sha256(f) != e.get("sha256"):
        return None
    return (f"<!-- lane-control persona `{e['name']}` (lane-control project|machine extras), rendered by project-setup "
            f"from {f}: edit that file and add it again, not this one -->\n\n" + f.read_text().strip() + "\n" +
            additions)


def summary(e: dict) -> str:
    """The persona's description: front matter `description:`, else its first line of text."""
    try:
        text = Path(e["prompt_file"]).read_text()
    except OSError:
        return ""
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if m:
        d = re.search(r"^description:\s*(.+)$", m.group(1), re.M)
        if d:
            return d.group(1).strip().strip("\"'")
        text = text[m.end():]
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:160]
    return ""


def render_personas(repo: Path, entries: list, additions) -> tuple:
    """Each custom persona into <repo>/.lane-control/personas/<name>.md (with the project's additions for it), and a
    persona of ours no longer an extra taken out. (lines, problems)."""
    d = Path(repo) / PERSONAS_DIR
    want = {e["name"]: e for e in entries if e["kind"] == "persona"}
    lines, problems = [], []
    if (want or d.is_dir()) and unsafe(repo, f"{PERSONAS_DIR}/x.md"):
        return [], [f"{PERSONAS_DIR}: {unsafe(repo, PERSONAS_DIR + '/x.md')}; no persona is rendered through it"]
    for name, e in want.items():
        text = persona_text(e, additions(name))
        if text is None:
            f = Path(e["prompt_file"])
            problems.append(f"persona:{name}: {f} " + ("is missing" if not f.is_file() else
                                                        "changed since it was added (add it again to accept it)"))
            continue
        f = d / f"{name}.md"
        if tracked(repo, f"{PERSONAS_DIR}/{name}.md"):
            problems.append(f"persona:{name}: {f} is committed in the project; left so")
            continue
        if f.is_symlink():
            problems.append(f"persona:{name}: {f} is a symlink; left so")
            continue
        if not (f.exists() and f.read_text() == text):
            state = "updated" if f.exists() else "created"
            write_file(f, text)
            lines.append(f"persona {name}: {state} ({f})")
    if d.is_dir():
        for f in d.glob("*.md"):
            if f.stem not in want and not f.is_symlink() and f.read_text().startswith("<!-- lane-control persona"):
                f.unlink()
                lines.append(f"persona {f.stem}: removed (no longer an extra)")
    common = common_dir(repo)
    if common:  # the personas folder is the main checkout's own; keep it out of git while it exists
        f = common / "info" / "exclude"
        text = f.read_text() if f.exists() else ""
        line = f"/{PERSONAS_DIR}/"
        if d.is_dir() and any(d.iterdir()) and line not in text.splitlines():
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text + ("" if not text or text.endswith("\n") else "\n") + line + "\n")
    return lines, problems


def coordinator_section(repo: Path, entries: list, lane_control: str) -> str:
    """The coordinator persona's part about this project's extras: its custom personas, and the prepare step when
    lanes or reviewers have extras of their own. '' when there is nothing to tell (the default design skills alone)."""
    personas = [e for e in entries if e["kind"] == "persona"]
    has = {s: any(not user_level(e) and e["kind"] != "persona" for e in for_scope(entries, s))
           for s in ("lanes", "reviewer")}
    if not personas and not any(has.values()):
        return ""
    lc = f"{lane_control}/scripts/lane-control"
    out = ["", "", "# Extras for this project",
           "<!-- from lane-control project|machine extras, rendered by project-setup: change them with those "
           "commands, not here -->", ""]
    if personas:
        out.append("Personas you may brief a lane with, beside the debugger: start the brief with the file, as for "
                   "the debugger; the tier and harness are its default (your path map still wins when it says more):")
        for e in personas:
            what = summary(e)
            out.append(f"- `{e['name']}`: `{Path(repo) / PERSONAS_DIR / (e['name'] + '.md')}`, tier {e['tier']}, "
                       f"{e['harness']}" + (f". {what}" if what else ""))
        out.append("")
    if any(has.values()):
        who = " and ".join(x for x, v in (("lanes", has["lanes"]), ("reviewers", has["reviewer"])) if v)
        out.append(f"The {who} of this project have extras (skills, MCP servers or plugins) that must be in their "
                   "worktree before the agent starts:")
        out.append(f"- A lane: make its worktree first (`orca-ide worktree create ... --json`, as for an OpenCode "
                   f"lane, for Claude too), then `{lc} lane prepare {repo} <worktree> --role lanes`, then "
                   "`worker-start --worktree path:<worktree>`.")
        out.append(f"- A reviewer: `{lc} lane prepare {repo} <lane worktree> --role reviewer` before its "
                   "worker-start. It shares the lane's worktree, so both then have both roles' extras.")
        out.append("- If prepare exits non-zero, hold the lane and tell the owner what it printed. A lane on another "
                   "host gets none of them (prepare runs on this machine).")
    return "\n".join(out) + "\n"


# --- checking ----------------------------------------------------------------------------------------------------

def check_entry(e: dict) -> tuple:
    """(state, detail) of one extra, read only: ok, missing, changed or failing."""
    k = e["kind"]
    if k == "skill":
        if user_level(e):
            link = home() / ".agents" / "skills" / e["name"]
            if ours_default(link):
                return ("ok", f"installed for the user ({link})") if (Path(os.readlink(link)) / "SKILL.md").is_file() \
                    and os.readlink(link) == str(skill_dir(e)) else ("missing", f"{link} is not at its pinned commit")
            if link.is_symlink() and not link.exists():
                return "missing", f"{link} is a dangling link"
            if link.exists():
                return "ok", f"the owner's own install at {link}"
            return "missing", f"commit {e['commit'][:12]} not fetched or not linked"
        why = source_state(e)
        if why is None:
            return "ok", f"fetched at {e['commit'][:12]}"
        return ("missing" if "not fetched" in why else "changed"), why
    if k == "mcp":
        why = mcp_problem(e)
        if why:
            return ("changed" if "changed" in why or "missing" in why else "failing"), why
        unset = [n for n in e.get("env", []) if n not in os.environ]
        return "ok", f"{e['command']} found" + (f"; not set in this shell: {', '.join(unset)} (the agent needs them)"
                                                 if unset else "")
    if k == "persona":
        f = Path(e["prompt_file"])
        if not f.is_file():
            return "missing", f"{f} is missing"
        if sha256(f) != e.get("sha256"):
            return "changed", f"{f} changed since it was added"
        return "ok", f"{f}, tier {e['tier']}, {e['harness']}"
    if e["harness"] == "opencode":
        return "ok", "OpenCode installs it at start"
    recs = claude_plugins().get(e["source"])
    if not recs:
        return "missing", f"not installed in Claude: claude plugin install {e['source']}"
    recs = recs if isinstance(recs, list) else [recs]
    pins = {str(r.get(x)) for r in recs if isinstance(r, dict) for x in ("version", "gitCommitSha") if r.get(x)}
    if e["pin"] not in pins and not any(p.startswith(e["pin"]) for p in pins if len(e["pin"]) >= 7):
        return "changed", f"installed {', '.join(sorted(pins)) or 'with no version'}, pinned {e['pin']}"
    return "ok", f"installed at {e['pin']}"


def check(store: "Path | None", folder: "Path | None" = None) -> list:
    """Every extra of a project (or the machine, store None), with its roles and state; and, with the main checkout,
    whether the coordinator has what refresh would give it. [{id, origin, for, state, detail, what}]."""
    rows = []
    entries = effective(store)
    for e in entries:
        state, detail = check_entry(e)
        rows.append({"id": ident(e), "origin": e.get("origin"), "for": e.get("for") or list(SCOPES),
                     "state": state, "detail": detail, "what": describe(e)})
    if folder is not None and store is not None:
        coord = for_scope(entries, "coordinator")
        lines, problems = apply_launch(store, coord, dry=True)
        more, more_problems = apply_folder(folder, coord, ["coordinator"], claude=False, dry=True)
        if problems or more_problems:
            rows.append({"id": "coordinator", "origin": "project", "for": ["coordinator"], "state": "failing",
                         "detail": "; ".join(problems + more_problems), "what": str(folder)})
        elif lines or more:
            rows.append({"id": "coordinator", "origin": "project", "for": ["coordinator"], "state": "not applied",
                         "detail": "; ".join(lines + more), "what": str(folder)})
    return rows


PROBLEMS = ("missing", "changed", "failing", "not applied")
