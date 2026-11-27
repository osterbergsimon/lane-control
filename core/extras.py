"""Extras: what the owner adds to the agents' environments, per project and machine-wide (lc-7c6). Owner 2026-11-26:
"Should we somehow simplify adding 'extras'? like if someone wants skills or mcps provisioned to their
lanes/coordinators?", then "do it, centralize and improve these things", "perhaps custom lane personas, too?".

Kinds (one entry each, its id `<kind>:<name>`):
  skill       a skill from a git repo pinned at a full commit, `<git-url>@<commit>[:<path>]`
  mcp         an MCP server run by a command; secrets only as the names of environment variables, never a value
  persona     a named lane role the coordinator can brief a lane with (a prompt file, a default tier and harness)
  agent-hook  a script run on a Claude Code event (PreToolUse, PostToolUse, SessionStart, Stop); on OpenCode the
              plugin equivalent for the tool events, and none for the session ones (said, never hidden)
  permission  an allow or deny rule added to lane-control's own; an allow that would reach a secret is refused
  plugin      a Claude or OpenCode plugin, pinned
  env         a non-secret environment variable (a name or value that looks like a key or token is refused)

Scope per extra: lanes, coordinator, reviewer (reviewer and design reviewer), or all. Stored in the project's own
settings store (<config>/lane-control/projects/<key>/extras.json, beside its tier table) and, machine-wide, in
<config>/lane-control/extras.json: never in a project repo. A project's extras add to the machine's; one with the same
id takes the machine's place for that project. The built-in design skills (core/design-skills.tsv) are default
machine extras: installed for the whole user (~/.agents/skills, and ~/.claude/skills unless Claude's own plugin has
the skill) exactly as before; `machine extras remove` turns one off. Every other extra goes into the folder of the
sessions it is for, where each harness reads it:

  Claude    <folder>/.claude/settings.local.json (hooks, permissions, env, enabledPlugins, enabledMcpjsonServers),
            <folder>/.mcp.json (MCP servers; ${NAME} for each secret, filled from the session's environment), and
            <folder>/.claude/skills/<name> (a link into the pinned checkout)
  OpenCode  <folder>/.opencode/opencode.json (mcp, permission, plugin), <folder>/.opencode/skills/<name>, and
            <folder>/.opencode/plugins/lane-extras.js with lane-extras.json beside it (hooks and env)

The coordinator's folder is the project's main checkout (`project refresh` and setup); a lane's or reviewer's is its
worktree (`lane-control lane prepare`, which the coordinator runs before the worker starts, since Orca starts a
worker with no per-role settings of its own). Everything written is in the repo's own git exclude, and what was added
is recorded in <state>/lane-control/extras/<folder>.json, so a later apply takes out only its own entries and leaves
the owner's alone. Setup's onboarding agents run with no MCP server and no project settings (project-setup
agent_cmd), so they get no extras.

Read by core/scripts/lane-extras (the commands), core/scripts/project-setup (apply, render, check) and
core/scripts/lane-control (doctor)."""
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KINDS = ("skill", "mcp", "persona", "agent-hook", "permission", "plugin", "env")
SCOPES = ("lanes", "coordinator", "reviewer")
CLAUDE_EVENTS = ("PreToolUse", "PostToolUse", "SessionStart", "Stop")
OPENCODE_EVENTS = {"PreToolUse": "execute.before", "PostToolUse": "execute.after"}  # SessionStart, Stop: Claude only
BUILTIN_PERSONAS = frozenset({"coordinator", "reviewer", "design-reviewer", "debugger", "machine-coordinator",
                              "design-lead", "lanes", "lane", "all"})
TIERS = ("swift", "standard", "deep")
HARNESSES = ("claude", "opencode")
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
MCP_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
COMMIT = re.compile(r"[0-9a-f]{40}")
SPEC = re.compile(r"(?P<url>.+)@(?P<commit>[0-9a-f]{40})(?::(?P<path>.*))?")
RULE = re.compile(r"(?P<tool>[A-Za-z][A-Za-z0-9_]*)(?:\((?P<spec>[^()\n]*)\))?")
SECRET_WORDS = frozenset({"KEY", "KEYS", "TOKEN", "TOKENS", "SECRET", "SECRETS", "PASSWORD", "PASSWD", "PASS", "PWD",
                          "CREDENTIAL", "CREDENTIALS", "CRED", "CREDS", "AUTH", "PRIVATE", "COOKIE", "SESSION",
                          "SIGNATURE", "SALT", "CERT", "PAT", "BEARER", "APIKEY", "OTP"})
RESERVED_ENV = frozenset({"HOME", "PATH", "USER", "SHELL", "LANE_ROLE", "LANE_PERSONA", "XDG_CONFIG_HOME",
                          "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "CLAUDE_CONFIG_DIR",
                          "OPENCODE_CONFIG", "OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG_DIR", "LD_PRELOAD",
                          "LD_LIBRARY_PATH", "NODE_OPTIONS", "PYTHONPATH", "GIT_DIR", "GIT_WORK_TREE"})
TOKEN_VALUE = re.compile(r"(sk-|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|glpat-|xox[abposr]-|AKIA|ASIA|AIza|ya29\.|"
                         r"eyJ[A-Za-z0-9_-]{8,}\.|-----BEGIN)")
# What an allow rule may never reach: lane-control's secret and privacy line (keys, tokens, credentials, .env files).
PROTECTED = re.compile(r"(^|[/\\*.(:\s~])(\.env(\b|[.*])|\.ssh\b|\.gnupg\b|\.aws\b|\.netrc\b|\.pypirc\b|\.npmrc\b|"
                       r"\.docker/config|\.kube\b|id_rsa|id_ed25519|id_ecdsa|id_dsa|[^/]*\.pem\b|[^/]*\.key\b|"
                       r"[^/]*\.p12\b|credentials|auth\.json|\.credentials\.json|hosts\.yml|secret|token|password|"
                       r"\.git/config|\.git-credentials|keychain|keyring)", re.I)
FILE_TOOLS = frozenset({"Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Glob", "Grep"})
BROAD = frozenset({"", "*", "**", "/**", "//**", "~/**", "./**", "/*", "**/*", "//*"})
OC_TOOL = {"shell": "Bash", "bash": "Bash", "edit": "Edit", "write": "Write", "read": "Read", "glob": "Glob",
           "grep": "Grep", "webfetch": "WebFetch"}
EXCLUDE_BEGIN = "# lane-control extras: begin (lane-control project extras; never committed)"
EXCLUDE_END = "# lane-control extras: end"
PERSONAS_DIR = ".lane-control/personas"
OC_PLUGIN = ROOT / "core" / "harnesses" / "opencode" / "lane-extras.js"


class ExtrasError(Exception):
    """An extra refused, or a store that cannot be read: the message says why and what to do."""


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


def skill_sources() -> Path:
    return home() / ".agents" / "skill-sources"


def script_sources() -> Path:
    return home() / ".agents" / "extras-sources"


def encode(folder: Path) -> str:
    return re.sub(rb"[^A-Za-z0-9]", b"-", os.fsencode(Path(folder).resolve())).decode()


def record_file(folder: Path) -> Path:
    return state_home() / "lane-control" / "extras" / f"{encode(folder)}.json"


def store_for(repo: Path) -> Path:
    """The project's settings store, keyed as project-setup keys it (store_key): <folder name>-<10 hex of sha256 of
    the real path>."""
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


def roots() -> list:
    """Every project checkout on this machine, attached repos included: where an extra's script may not live."""
    out = []
    for root, st in known():
        out.append(root)
        out += [Path(a) for a in st.get("attached") or [] if isinstance(a, str)]
    return out


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def find_project(arg: str) -> tuple:
    """(root, settings, store) of a project named by its folder or its name; ExtrasError when there is none."""
    d = Path(arg).expanduser()
    if d.is_dir():
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=d, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        if r.returncode != 0:
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


def write_atomic(path: Path, text: str, mode: "int | None" = None) -> None:
    """Through a temp file and a rename beside it (a symlink stays a link, its target is replaced); the mode kept."""
    target = path.resolve() if path.is_symlink() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    keep = stat.S_IMODE(target.stat().st_mode) if target.exists() else mode
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if keep is not None:
            os.chmod(tmp, keep)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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
    write_atomic(path, json.dumps(body, indent=2, ensure_ascii=False) + "\n", 0o600)


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


# --- adding: parse and refuse ------------------------------------------------------------------------------------

def url_problem(url: str) -> "str | None":
    """Why a git URL is refused (an owner's extra): https, ssh, git@host:path or file only; no option or helper."""
    if not url or url.startswith("-") or any(c.isspace() or ord(c) < 32 for c in url) or "::" in url:
        return "it is not a plain git URL (no leading dash, spaces or `::` helpers)"
    if url.startswith(("https://", "ssh://", "file://")) or re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[^-].*",
                                                                          url):
        return None
    return "it is not https://, ssh://, git@host:path or file:// (http and other transports are refused)"


def path_problem(rel: str, need: bool) -> "str | None":
    if not rel:
        return "a path inside the repo is needed after the commit (`@<commit>:<path>`)" if need else None
    if rel.startswith("/") or any(p in ("", ".", "..") for p in rel.split("/")):
        return "the path in the repo is not a plain relative path"
    return None


def git_spec(text: str, need_path: bool) -> dict:
    m = SPEC.fullmatch(text or "")
    if not m:
        raise ExtrasError(f"{text!r} is not <git-url>@<full 40-hex commit>[:<path>] (a branch or short sha is not a "
                          "pin)")
    why = url_problem(m["url"]) or path_problem(m["path"] or "", need_path)
    if why:
        raise ExtrasError(f"{text!r}: {why}")
    return {"url": m["url"], "commit": m["commit"], "path": m["path"] or ""}


def secretish_name(name: str) -> bool:
    words = set(re.split(r"[_\W]+", name.upper())) - {""}
    return bool(words & SECRET_WORDS) or name.upper().endswith(("KEY", "TOKEN", "SECRET", "PASSWORD"))


def secretish_value(value: str) -> bool:
    if TOKEN_VALUE.search(value):
        return True
    compact = value.strip()
    return (len(compact) >= 32 and not re.search(r"\s", compact) and re.search(r"[0-9]", compact) is not None
            and re.search(r"[A-Za-z]", compact) is not None and "/" not in compact)


def common_dir(folder: Path) -> "Path | None":
    """The git common dir of the repo holding folder (every worktree of a repo shares it), or None."""
    d = folder if folder.is_dir() else folder.parent
    try:
        r = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=d,
                           capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return Path(r.stdout.strip()).resolve() if r.returncode == 0 and r.stdout.strip() else None


def inside_projects(path: Path, roots: list) -> "Path | None":
    """The project checkout (any worktree of it) that holds path, or None."""
    mine = common_dir(Path(path))
    if mine is None:
        return None
    for r in roots:
        if Path(r).is_dir() and common_dir(Path(r)) == mine:
            return Path(r)
    return None


def owners_file(given: str, roots: list, allow_repo: bool, what: str, executable: bool) -> tuple:
    """(absolute path, sha256, warning or '') of a file an extra names; refused when missing, or inside a project
    checkout without allow_repo."""
    p = Path(given).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    p = Path(os.path.abspath(p))
    if not p.is_file():
        raise ExtrasError(f"{what} {p} is not a file")
    if executable and not os.access(p, os.X_OK):
        raise ExtrasError(f"{what} {p} is not executable (chmod +x it)")
    inside = inside_projects(p, roots)
    warning = ""
    if inside is not None:
        if not allow_repo:
            raise ExtrasError(f"{what} {p} is inside the project checkout {inside}: a lane can change it. Keep it "
                              "outside any project repo, or pin it as <git-url>@<commit>:<path>; --allow-repo-path "
                              "takes it anyway")
        warning = (f"warning: {what} {p} is inside the project checkout {inside}; it is pinned by its hash, and a "
                   "lane that changes it makes check flag it")
    return str(p), sha256(p), warning


def rule_problem(action: str, rule: str, private: list) -> "str | None":
    m = RULE.fullmatch(rule or "")
    if not m and not re.fullmatch(r"mcp__[A-Za-z0-9_-]+(__[A-Za-z0-9_-]+)?", rule or ""):
        return "it is not a permission rule such as Bash(npm test:*), WebFetch(domain:example.com) or mcp__server"
    if action == "deny":
        return None  # a deny only ever narrows
    tool, spec = (m["tool"], m["spec"]) if m else (rule, None)
    if PROTECTED.search(rule):
        return "it reaches secrets (keys, tokens, credentials or .env files), which lane-control's denies keep out"
    if tool in FILE_TOOLS and (spec is None or spec.strip() in BROAD):
        return f"{tool} on every file reaches secrets too; allow the folders the agents need instead"
    for g in private:
        if g and g.strip("/*") and g.strip("/*") in (spec or ""):
            return f"it reaches {g}, one of the project's --private paths"
    return None


def parse_plugin(text: str) -> dict:
    harness, _, rest = (text or "").partition(":")
    source, _, pin = rest.rpartition("@")
    if harness not in HARNESSES or not source or not pin:
        raise ExtrasError(f"{text!r} is not claude:<plugin>@<marketplace>@<version> or opencode:<package>@<version>")
    if harness == "claude":
        if not re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9._-]+", source) or not re.fullmatch(r"[A-Za-z0-9._+-]+", pin):
            raise ExtrasError(f"{text!r}: a Claude plugin is <plugin>@<marketplace>@<version or commit>")
    elif not re.fullmatch(r"(@[a-z0-9._-]+/)?[a-z0-9._-]+", source) or \
            not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", pin):
        raise ExtrasError(f"{text!r}: an OpenCode plugin is <npm package>@<exact version> (no range, no latest)")
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


def make(kind: str, value, opts: dict, roots: list, private: list = ()) -> tuple:
    """(entry, warnings) for one `extras add`. opts: name, for, command (list), env (list of names), matcher,
    prompt_file, tier, harness, allow_repo_path."""
    scope = parse_scope(opts.get("for"))
    warnings = []
    e = {"kind": kind, "for": scope}
    name = opts.get("name")
    allow_repo = bool(opts.get("allow_repo_path"))
    if kind == "skill":
        e.update(git_spec(value, False))
        name = name or Path(e["path"] or re.sub(r"\.git$", "", e["url"].rstrip("/"))).name.lower()
        if not NAME.fullmatch(name or ""):
            raise ExtrasError(f"skill name {name!r} is not lowercase letters, digits and dashes: give --name")
    elif kind == "mcp":
        name = value
        if not MCP_NAME.fullmatch(name or ""):
            raise ExtrasError(f"MCP server name {name!r}: letters, digits, - and _ only")
        cmd = list(opts.get("command") or [])
        if not cmd:
            raise ExtrasError("an MCP server needs its command after `--`: --mcp <name> [--env NAME]... -- "
                              "<command> [args]")
        for a in cmd:
            if secretish_value(a):
                raise ExtrasError("an argument of the MCP command looks like a key or token; give the secret as "
                                  "--env NAME (its name only) and let the server read it from its environment")
        if "/" in cmd[0]:
            cmd[0], digest, warn = owners_file(cmd[0], roots, allow_repo, "the MCP command", True)
            e["sha256"] = digest
            warnings += [warn] if warn else []
        envs = []
        for n in opts.get("env") or []:
            if "=" in n:
                raise ExtrasError(f"--env {n.split('=')[0]}=...: an MCP server's --env takes a name only; the value "
                                  "is never stored, the server gets it from the agent's environment")
            if not ENV_NAME.fullmatch(n):
                raise ExtrasError(f"--env {n!r} is not an environment variable name")
            envs.append(n)
        e.update(command=cmd[0], args=cmd[1:], env=envs)
    elif kind == "persona":
        name = value
        if not NAME.fullmatch(name or "") or name in BUILTIN_PERSONAS:
            raise ExtrasError(f"persona name {name!r}: lowercase letters, digits and dashes, and not a built-in role "
                              f"({', '.join(sorted(BUILTIN_PERSONAS - {'lanes', 'lane', 'all'}))})")
        if not opts.get("prompt_file"):
            raise ExtrasError("a persona needs --prompt-file <path>")
        tier, harness = opts.get("tier") or "standard", opts.get("harness") or "claude"
        if tier not in TIERS or harness not in HARNESSES:
            raise ExtrasError(f"--tier takes {', '.join(TIERS)}; --harness takes {', '.join(HARNESSES)}")
        path, digest, _ = owners_file(opts["prompt_file"], roots, True, "the prompt file", False)
        text = Path(path).read_text()
        if not text.strip():
            raise ExtrasError(f"the prompt file {path} is empty")
        e.update(prompt_file=path, sha256=digest, tier=tier, harness=harness)
        e["for"] = ["lanes"]
    elif kind == "agent-hook":
        event = value
        if event not in CLAUDE_EVENTS:
            raise ExtrasError(f"--agent-hook {event!r}: it takes {', '.join(CLAUDE_EVENTS)}")
        cmd = list(opts.get("command") or [])
        if len(cmd) != 1:
            raise ExtrasError("an agent hook names one script after `--`: a path outside any project repo, or "
                              "<git-url>@<commit>:<path>")
        if SPEC.fullmatch(cmd[0]) and not Path(cmd[0]).exists():
            e.update(git_spec(cmd[0], True))
            base = Path(e["path"]).name
        else:
            e["script"], e["sha256"], warn = owners_file(cmd[0], roots, allow_repo, "the hook script", True)
            warnings += [warn] if warn else []
            base = Path(e["script"]).name
        matcher = opts.get("matcher") or ""
        if matcher and event not in ("PreToolUse", "PostToolUse"):
            raise ExtrasError(f"--matcher is for PreToolUse and PostToolUse only (tool names), not {event}")
        e.update(event=event, matcher=matcher)
        name = name or f"{event.lower()}-{re.sub(r'[^a-z0-9-]+', '-', Path(base).stem.lower()).strip('-') or 'hook'}"
        if not NAME.fullmatch(name):
            raise ExtrasError(f"hook name {name!r} is not lowercase letters, digits and dashes: give --name")
        if event not in OPENCODE_EVENTS:
            warnings.append(f"note: {event} is Claude only: OpenCode v2 has no session {event} hook for plugins")
    elif kind == "permission":
        action, rule = value
        if action not in ("allow", "deny"):
            raise ExtrasError("--permission takes allow or deny, then the rule")
        why = rule_problem(action, rule, list(private))
        if why:
            raise ExtrasError(f"--permission {action} {rule!r} is refused: {why}")
        e.update(action=action, rule=rule)
        name = f"{action} {rule}"
    elif kind == "plugin":
        e.update(parse_plugin(value))
        name = f"{e['harness']}:{e['source']}"
    elif kind == "env":
        n, eq, v = (value or "").partition("=")
        if not eq or not ENV_NAME.fullmatch(n):
            raise ExtrasError("--env takes NAME=value (a non-secret value; an MCP server's secret is --mcp ... --env "
                              "NAME)")
        if n.upper() in RESERVED_ENV or n.upper().startswith(("LANE_", "ORCA_", "XDG_")):
            raise ExtrasError(f"--env {n}: lane-control, Orca or the system sets it")
        if secretish_name(n) or secretish_value(v):
            raise ExtrasError(f"--env {n}: it looks like a key, token or password, and no secret is ever stored. "
                              "Put it in the agent's own environment (a secret manager, direnv) instead")
        if "\n" in v or len(v) > 4096:
            raise ExtrasError(f"--env {n}: one line of at most 4096 characters")
        e.update(value=v)
        name = n
    else:
        raise ExtrasError(f"no extra kind {kind!r}")
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
    """The entries `word` names: an id (`kind:name`) or a bare name."""
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
    if k == "agent-hook":
        src = e.get("script") or f"{e['url']}@{e['commit'][:12]}:{e['path']}"
        return f"{e['event']}{'[' + e['matcher'] + ']' if e.get('matcher') else ''} {src}"
    if k == "permission":
        return f"{e['action']} {e['rule']}"
    if k == "plugin":
        return f"{e['harness']}:{e['source']}@{e['pin']}"
    return f"{e['name']}={e['value']}"


# --- fetching pinned sources -------------------------------------------------------------------------------------

def fetch(url: str, commit: str, path: str, dest: Path, want: str, folder: bool = True) -> "str | None":
    """A sparse checkout of `path` (a folder, or one file when not folder; the whole repo when '') at `commit` into
    dest, made by rename so never half there; `want` is the file that must be there after (relative to dest). None
    when done, else why not."""
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)

    def g(*args):
        return subprocess.run(["git", "-C", str(tmp), *args], capture_output=True, text=True, timeout=300,
                              stdin=subprocess.DEVNULL, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    sparse = [f"/{path.strip('/')}" + ("/" if folder else ""), "/LICENSE*", "/COPYING*"]
    try:
        tmp.mkdir(parents=True)
        steps = [("init", "-q"), ("remote", "add", "origin", url)]
        if path:
            steps.append(("sparse-checkout", "set", "--no-cone", *sparse))
        steps += [("fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", commit),
                  ("checkout", "-q", "--detach", "FETCH_HEAD")]
        for step in steps:
            r = g(*step)
            if r.returncode != 0:
                last = (r.stderr or r.stdout).strip().splitlines()
                return f"git {step[0]} failed: {last[-1] if last else r.returncode}"
        if g("rev-parse", "HEAD").stdout.strip() != commit:
            return f"fetched commit is not {commit[:12]}"
        if not (tmp / want).is_file():
            return f"no {want} at that commit"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(tmp, dest)
        return None
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def skill_dir(e: dict) -> Path:
    """Where a skill's pinned checkout keeps the skill (its SKILL.md)."""
    src = skill_sources() / f"{e['name']}@{e['commit'][:12]}"
    return src / e["path"] if e.get("path") else src


def ensure_skill(e: dict) -> "str | None":
    target = skill_dir(e)
    if (target / "SKILL.md").is_file():
        return None
    src = skill_sources() / f"{e['name']}@{e['commit'][:12]}"
    return fetch(e["url"], e["commit"], e.get("path", ""), src, f"{e['path']}/SKILL.md" if e.get("path") else
                 "SKILL.md")


def hook_script(e: dict) -> Path:
    if e.get("script"):
        return Path(e["script"])
    return script_sources() / f"{e['name']}@{e['commit'][:12]}" / e["path"]


def ensure_hook(e: dict) -> "str | None":
    if e.get("script") or hook_script(e).is_file():
        return None
    return fetch(e["url"], e["commit"], e["path"], script_sources() / f"{e['name']}@{e['commit'][:12]}", e["path"],
                 folder=False)


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


def user_skills(entries: list) -> list:
    """The default design skills in ~/.agents/skills (where OpenCode v2 reads them), each a link into a sparse checkout
    of its repo at the pinned commit under ~/.agents/skill-sources/; Claude gets a link in ~/.claude/skills unless its
    own plugin carries the skill. One the owner installed is left alone; a fetch that fails is named, never a stop.
    A default turned off (or replaced by an extra of the owner's) loses only the links of ours. [(name, state)]."""
    skills, sources = home() / ".agents" / "skills", skill_sources()
    plugins = set(claude_plugins())
    want = {e["name"]: e for e in entries if e["kind"] == "skill" and user_level(e)}
    override = bool(os.environ.get("LANE_DESIGN_SKILLS"))
    done = []
    for row in default_rows():
        if row[0] not in want:
            link = skills / row[0]
            if link.is_symlink() and os.readlink(link).startswith(str(sources) + os.sep):
                link.unlink()
                claude = home() / ".claude" / "skills" / row[0]
                if claude.is_symlink() and os.readlink(claude) == str(link):
                    claude.unlink()
                done.append((row[0], "removed (turned off)"))
    for e in want.values():
        row = (e["name"], e["url"], e["commit"], e["path"])
        name = e["name"]
        why = bad_default(row, override)
        if why:
            done.append((name, f"missing (refused: {why})"))
            continue
        link, target = skills / name, skill_dir(e)
        ours = link.is_symlink() and os.readlink(link).startswith(str(sources) + os.sep)
        if (link.exists() or link.is_symlink()) and not ours:
            done.append((name, "exists (not lane-control's)"))
            continue
        if ours and os.readlink(link) == str(target) and (target / "SKILL.md").is_file():
            state = "exists"
        else:
            if not (target / "SKILL.md").is_file():
                why = ensure_skill(e)
                if why:
                    done.append((name, f"missing ({why})"))
                    continue
            skills.mkdir(parents=True, exist_ok=True)
            tmp_link = skills / f".{name}.{os.getpid()}.link"
            tmp_link.unlink(missing_ok=True)
            tmp_link.symlink_to(target)
            os.replace(tmp_link, link)
            state = "updated" if ours else "created"
        for old in sources.glob(f"{name}@*"):
            if old.name != f"{name}@{e['commit'][:12]}":
                shutil.rmtree(old, ignore_errors=True)
        claude = home() / ".claude" / "skills" / name
        if e.get("claude_plugin") not in plugins and not claude.exists() and not claude.is_symlink():
            claude.parent.mkdir(parents=True, exist_ok=True)
            claude.symlink_to(link)
        done.append((name, state))
    return done


# --- applying to a session's folder --------------------------------------------------------------------------------

def tracked(folder: Path, rel: str) -> bool:
    try:
        r = subprocess.run(["git", "ls-files", "--error-unmatch", "--", rel], cwd=folder, capture_output=True,
                           timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def read_json(path: Path, what: str) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text().strip() or "{}")
    except (OSError, ValueError) as e:
        raise ExtrasError(f"{path}: not valid JSON ({e}); fix it by hand, it was left as it is")
    if not isinstance(data, dict):
        raise ExtrasError(f"{path}: not a JSON object ({what}); fix it by hand, it was left as it is")
    return data


def claude_hook(e: dict) -> dict:
    entry = {"hooks": [{"type": "command", "command": shlex.quote(str(hook_script(e)))}]}
    if e.get("matcher"):
        entry = {"matcher": e["matcher"], **entry}
    return entry


def oc_rule(e: dict):
    """(permission key, pattern or None, action) of a rule OpenCode can carry, or None (Claude only)."""
    m = RULE.fullmatch(e["rule"])
    if not m:
        return None
    tool, spec, action = m["tool"], m["spec"], e["action"]
    if tool == "Bash":
        if spec is None or spec.strip() in ("", "*"):
            return ("bash", "*", action)
        return ("bash", spec[:-2] + "*" if spec.endswith(":*") else spec, action)
    if tool in ("Edit", "Write", "MultiEdit") and spec is None:
        return ("edit", None, action)
    if tool == "WebFetch" and spec is None:
        return ("webfetch", None, action)
    return None


def plan(entries: list) -> dict:
    """What one folder gets from these entries (folder-level only: no default skill, no persona)."""
    es = [e for e in entries if not user_level(e) and e["kind"] != "persona"]
    p = {"skills": [e for e in es if e["kind"] == "skill"], "mcp": [e for e in es if e["kind"] == "mcp"],
         "hooks": [e for e in es if e["kind"] == "agent-hook"],
         "permissions": [e for e in es if e["kind"] == "permission"],
         "plugins": [e for e in es if e["kind"] == "plugin"], "env": [e for e in es if e["kind"] == "env"]}
    return p


def opencode_config(p: dict) -> dict:
    cfg = {}
    mcp = {e["name"]: {"type": "local", "command": [e["command"], *e.get("args", [])], "enabled": True,
                       **({"environment": {n: "{env:%s}" % n for n in e["env"]}} if e.get("env") else {})}
           for e in p["mcp"]}
    if mcp:
        cfg["mcp"] = mcp
    perm = {}
    for e in p["permissions"]:
        r = oc_rule(e)
        if r is None:
            continue
        key, pattern, action = r
        if pattern is None:
            perm[key] = action
        else:
            cur = perm.get(key)
            perm[key] = {**(cur if isinstance(cur, dict) else {}), pattern: action}
    if perm:
        cfg["permission"] = perm
    plugins = [f"{e['source']}@{e['pin']}" for e in p["plugins"] if e["harness"] == "opencode"]
    if plugins:
        cfg["plugin"] = plugins
    return {"$schema": "https://opencode.ai/config.json", **cfg} if cfg else {}


def opencode_sidecar(p: dict) -> dict:
    hooks = [{"event": e["event"], "matcher": e.get("matcher", ""), "command": str(hook_script(e))}
             for e in p["hooks"] if e["event"] in OPENCODE_EVENTS]
    env = {e["name"]: e["value"] for e in p["env"]}
    return {k: v for k, v in (("env", env), ("hooks", hooks)) if v}


def exclude_names(p: dict) -> list:
    names = [f"/.claude/skills/{e['name']}" for e in p["skills"]] + \
            [f"/.opencode/skills/{e['name']}" for e in p["skills"]]
    if p["mcp"]:
        names.append("/.mcp.json")
    names += ["/.claude/settings.local.json", "/.opencode/opencode.json", "/.opencode/plugins/lane-extras.js",
              "/.opencode/lane-extras.json",
              f"/{PERSONAS_DIR}/"]
    return names


def exclude(folder: Path, names: list, dry: bool) -> str:
    """Our own block in the repo's git exclude (shared by every worktree): the union of what any apply wrote."""
    common = common_dir(folder)
    if common is None:
        return "skipped"
    f = common / "info" / "exclude"
    text = f.read_text() if f.exists() else ""
    old = []
    if EXCLUDE_BEGIN in text and EXCLUDE_END in text.split(EXCLUDE_BEGIN, 1)[1]:
        head, rest = text.split(EXCLUDE_BEGIN, 1)
        body, tail = rest.split(EXCLUDE_END, 1)
        old = [x for x in body.splitlines() if x.strip()]
    else:
        head, tail = text + ("" if not text or text.endswith("\n") else "\n"), "\n"
    lines = sorted(set(old) | set(names))
    if lines == sorted(old) and old:
        return "exists"
    if dry:
        return "would update"
    block = "\n".join((EXCLUDE_BEGIN, *lines, EXCLUDE_END))
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(head + block + (tail if tail.startswith("\n") else "\n" + tail))
    return "updated" if old else "created"


def _link(link: Path, target: Path, mine: list, dry: bool) -> str:
    if link.is_symlink() and os.readlink(link) == str(target):
        mine.append(str(link))
        return "exists"
    if link.exists() or link.is_symlink():
        ours = link.is_symlink() and os.readlink(link).startswith(str(skill_sources()) + os.sep)
        if not ours:
            return "exists (not lane-control's)"
    if not dry:
        link.parent.mkdir(parents=True, exist_ok=True)
        tmp = link.with_name(f".{link.name}.{os.getpid()}.link")
        tmp.unlink(missing_ok=True)
        tmp.symlink_to(target)
        os.replace(tmp, link)
    mine.append(str(link))
    return "updated"


def apply_folder(folder: Path, entries: list, scopes: list, dry: bool = False) -> tuple:
    """Make folder hold what `entries` give a session there (Claude and OpenCode both), taking out what an earlier
    apply added and is no longer wanted. Returns (lines, problems): what changed, and what could not be done.
    dry: change nothing, and say what would change (`check`)."""
    folder = Path(folder).resolve()
    p = plan(entries)
    rec_path = record_file(folder)
    try:
        rec = json.loads(rec_path.read_text()) if rec_path.exists() else {}
    except (OSError, ValueError):
        rec = {}
    rec = rec if isinstance(rec, dict) else {}
    new = {"scopes": scopes, "claude": {}, "mcp": {}, "links": [], "opencode": {}}
    lines, problems = [], []

    # pinned sources first: a skill or hook that cannot be fetched is named, and the rest still goes in
    if not any(p.values()) and not rec:
        return [], []  # nothing to put here and nothing put before: the folder is left exactly as it is
    for e in list(p["skills"]):
        have = (skill_dir(e) / "SKILL.md").is_file()
        why = None if have else ("not fetched" if dry else ensure_skill(e))
        if why:
            problems.append(f"skill {e['name']}: {why}")
            p["skills"].remove(e)
    for e in list(p["hooks"]):
        have = hook_script(e).is_file()
        why = None if have else ("not fetched" if dry else ensure_hook(e))
        if why is None and not hook_script(e).is_file():
            why = f"{hook_script(e)} is missing"
        elif why is None and not os.access(hook_script(e), os.X_OK):
            why = f"{hook_script(e)} is not executable"
        elif why is None and e.get("sha256") and sha256(hook_script(e)) != e["sha256"]:
            why = f"{hook_script(e)} changed since it was added (add it again to accept the new text)"
        if why:
            problems.append(f"agent-hook {e['name']}: {why}")
            p["hooks"].remove(e)

    # Claude: settings.local.json
    sf = folder / ".claude" / "settings.local.json"
    sf_existed = sf.exists()
    data = read_json(sf, "Claude's settings")
    before = json.dumps(data, sort_keys=True)
    old = rec.get("claude", {}) if isinstance(rec.get("claude"), dict) else {}
    hooks = data.get("hooks", {}) if isinstance(data.get("hooks", {}), dict) else None
    perms = data.get("permissions", {}) if isinstance(data.get("permissions", {}), dict) else None
    env = data.get("env", {}) if isinstance(data.get("env", {}), dict) else None
    if hooks is None or perms is None or env is None:
        raise ExtrasError(f"{sf}: hooks, permissions or env is not an object; fix it by hand, it was left as it is")
    for ev, items in (old.get("hooks") or {}).items():
        if isinstance(hooks.get(ev), list):
            hooks[ev] = [h for h in hooks[ev] if h not in items]
            if not hooks[ev]:
                hooks.pop(ev)
    for key in ("allow", "deny"):
        if isinstance(perms.get(key), list):
            perms[key] = [r for r in perms[key] if r not in (old.get(key) or [])]
    for n, v in (old.get("env") or {}).items():
        if env.get(n) == v:
            env.pop(n)
    if isinstance(data.get("enabledPlugins"), dict):
        for k in old.get("enabledPlugins") or []:
            data["enabledPlugins"].pop(k, None)
    if isinstance(data.get("enabledMcpjsonServers"), list):
        data["enabledMcpjsonServers"] = [n for n in data["enabledMcpjsonServers"]
                                         if n not in (old.get("enabledMcpjsonServers") or [])]
    mine = {"hooks": {}, "allow": [], "deny": [], "env": {}, "enabledPlugins": [], "enabledMcpjsonServers": []}
    for e in p["hooks"]:
        h = claude_hook(e)
        lst = hooks.setdefault(e["event"], [])
        if h not in lst:
            lst.append(h)
            mine["hooks"].setdefault(e["event"], []).append(h)
    for e in p["permissions"]:
        lst = perms.setdefault(e["action"], [])
        if e["rule"] not in lst:
            lst.append(e["rule"])
            mine[e["action"]].append(e["rule"])
    for e in p["env"]:
        if e["name"] in env and env[e["name"]] != e["value"]:
            problems.append(f"env {e['name']}: {sf} already sets it to another value (not lane-control's); left so")
            continue
        if e["name"] not in env:
            env[e["name"]] = e["value"]
            mine["env"][e["name"]] = e["value"]
    claude_plugins_wanted = [e for e in p["plugins"] if e["harness"] == "claude"]
    if claude_plugins_wanted:
        ep = data.setdefault("enabledPlugins", {})
        for e in claude_plugins_wanted:
            if e["source"] not in ep:
                ep[e["source"]] = True
                mine["enabledPlugins"].append(e["source"])
    mcp_tracked = bool(p["mcp"]) and tracked(folder, ".mcp.json")
    if p["mcp"] and not mcp_tracked:
        lst = data.setdefault("enabledMcpjsonServers", [])
        for e in p["mcp"]:
            if e["name"] not in lst:
                lst.append(e["name"])
                mine["enabledMcpjsonServers"].append(e["name"])
    for k, v in (("hooks", hooks), ("permissions", perms), ("env", env)):
        if v:
            data[k] = {kk: vv for kk, vv in v.items() if vv != []} if k == "permissions" else v
        else:
            data.pop(k, None)
    for k in ("enabledPlugins", "enabledMcpjsonServers"):
        if k in data and not data[k]:
            data.pop(k)
    if isinstance(data.get("permissions"), dict) and not data["permissions"]:
        data.pop("permissions")
    if json.dumps(data, sort_keys=True) != before:
        lines.append(f"{sf}: {'would update' if dry else 'updated'} the extras (hooks, permissions, env, plugins)")
        if not dry:
            if not data and rec.get("claude_created"):
                sf.unlink(missing_ok=True)
            else:
                write_atomic(sf, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    new["claude"] = mine
    new["claude_created"] = rec.get("claude_created", not sf_existed) if "claude_created" in rec else not sf_existed

    # Claude: .mcp.json
    mf = folder / ".mcp.json"
    if tracked(folder, ".mcp.json"):
        if p["mcp"]:
            problems.append(f"mcp {', '.join(e['name'] for e in p['mcp'])}: {mf} is committed in the project, so its "
                            "MCP servers are the project's; lane-control writes no MCP extras into it")
    else:
        existed = mf.exists()
        mdata = read_json(mf, "MCP servers")
        servers = mdata.get("mcpServers", {}) if isinstance(mdata.get("mcpServers", {}), dict) else {}
        mbefore = json.dumps(mdata, sort_keys=True)
        for n, v in (rec.get("mcp") or {}).items():
            if servers.get(n) == v:
                servers.pop(n)
        mine_mcp = {}
        for e in p["mcp"]:
            want = {"type": "stdio", "command": e["command"], "args": e.get("args", []),
                    **({"env": {n: "${%s}" % n for n in e["env"]}} if e.get("env") else {})}
            if e["name"] in servers and servers[e["name"]] != want:
                problems.append(f"mcp {e['name']}: {mf} has a server of that name that is not lane-control's; "
                                "left so")
                continue
            servers[e["name"]] = want
            mine_mcp[e["name"]] = want
        if servers:
            mdata["mcpServers"] = servers
        else:
            mdata.pop("mcpServers", None)
        if json.dumps(mdata, sort_keys=True) != mbefore:
            lines.append(f"{mf}: {'would update' if dry else 'updated'} the MCP servers")
            if not dry:
                if mdata:
                    write_atomic(mf, json.dumps(mdata, indent=2, ensure_ascii=False) + "\n")
                elif mf.exists() and rec.get("mcp_created"):
                    mf.unlink()
        new["mcp"] = mine_mcp
        new["mcp_created"] = ((rec["mcp_created"] if rec.get("mcp") and "mcp_created" in rec else not existed)
                              if mine_mcp else False)

    # skills: links for both harnesses
    links = []
    for e in p["skills"]:
        for d in (".claude", ".opencode"):
            link = folder / d / "skills" / e["name"]
            if tracked(folder, f"{d}/skills/{e['name']}/SKILL.md"):
                problems.append(f"skill {e['name']}: the project commits {d}/skills/{e['name']}; left so")
                continue
            state = _link(link, skill_dir(e), links, dry)
            if state == "updated":
                lines.append(f"{link}: {'would link' if dry else 'linked'} to {skill_dir(e)}")
            elif state.startswith("exists ("):
                problems.append(f"skill {e['name']}: {link} {state}; left so")
    for old_link in rec.get("links") or []:
        lk = Path(old_link)
        if old_link not in links and lk.is_symlink() and os.readlink(lk).startswith(str(skill_sources()) + os.sep):
            lines.append(f"{lk}: {'would remove' if dry else 'removed'} (no longer an extra here)")
            if not dry:
                lk.unlink()
    new["links"] = links

    # OpenCode: .opencode/opencode.json, and the plugin with its sidecar
    oc_old = rec.get("opencode") if isinstance(rec.get("opencode"), dict) else {}
    for rel, content in ((".opencode/opencode.json", opencode_config(p)),
                         (".opencode/lane-extras.json", opencode_sidecar(p)),
                         (".opencode/plugins/lane-extras.js", OC_PLUGIN.read_text() if opencode_sidecar(p) else None)):
        f = folder / rel
        text = (json.dumps(content, indent=2, ensure_ascii=False) + "\n" if isinstance(content, dict) and content
                else content if isinstance(content, str) else None)
        ours = f.exists() and (oc_old.get(rel) == hashlib.sha256(f.read_bytes()).hexdigest() or
                               (text is not None and f.read_text() == text))
        if tracked(folder, rel):
            if text:
                problems.append(f"{rel} is committed in the project; the OpenCode extras for this folder are not "
                                "written")
            continue
        if f.exists() and not ours:
            if text:
                problems.append(f"{f} exists and is not lane-control's; the OpenCode extras it would hold are not "
                                "written")
            continue
        if text is None:
            if ours:
                lines.append(f"{f}: {'would remove' if dry else 'removed'} (no extras left for it)")
                if not dry:
                    f.unlink()
            continue
        if not (f.exists() and f.read_text() == text):
            lines.append(f"{f}: {'would write' if dry else 'wrote'} the OpenCode extras")
            if not dry:
                write_atomic(f, text)
        new["opencode"][rel] = hashlib.sha256(text.encode()).hexdigest()

    names = exclude_names(p)
    ex = exclude(folder, names, dry)
    if ex in ("created", "updated", "would update"):
        lines.append(f"git exclude: {ex} (lane-control extras block)")
    if not dry:
        rec_path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(rec_path, json.dumps({**new, "folder": str(folder)}, indent=2) + "\n")
    return lines, problems


# --- personas -----------------------------------------------------------------------------------------------------

def persona_text(e: dict, additions: str) -> "str | None":
    """The rendered text of a custom persona, or None when its prompt file is missing or changed."""
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
    """Each custom persona into <repo>/.lane-control/personas/<name>.md (with the project's additions for it, from
    additions(name)), and a persona of ours no longer an extra taken out. (lines, problems)."""
    d = Path(repo) / PERSONAS_DIR
    want = {e["name"]: e for e in entries if e["kind"] == "persona"}
    lines, problems = [], []
    if want:
        exclude(Path(repo), [f"/{PERSONAS_DIR}/"], False)
    for name, e in want.items():
        text = persona_text(e, additions(name))
        if text is None:
            f = Path(e["prompt_file"])
            problems.append(f"persona {name}: {f} " + ("is missing" if not f.is_file() else
                                                        "changed since it was added (add it again to accept it)"))
            continue
        f = d / f"{name}.md"
        if not (f.exists() and f.read_text() == text):
            d.mkdir(parents=True, exist_ok=True)
            state = "updated" if f.exists() else "created"
            f.write_text(text)
            lines.append(f"persona {name}: {state} ({f})")
    if d.is_dir():
        for f in d.glob("*.md"):
            if f.stem not in want and f.read_text().startswith("<!-- lane-control persona"):
                f.unlink()
                lines.append(f"persona {f.stem}: removed (no longer an extra)")
    return lines, problems


def coordinator_section(repo: Path, entries: list, lane_control: str) -> str:
    """The coordinator persona's part about this project's extras: its custom personas, and the prepare step when
    lanes or reviewers have folder extras. '' when there is nothing to tell (the default design skills alone)."""
    personas = [e for e in entries if e["kind"] == "persona"]
    lanes = plan(for_scope(entries, "lanes"))
    reviewers = plan(for_scope(entries, "reviewer"))
    has = {s: any(v for v in pl.values()) for s, pl in (("lanes", lanes), ("reviewer", reviewers))}
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
        out.append(f"The {who} of this project have extras (skills, MCP servers, hooks, permissions, plugins or env) "
                   "that must be in their worktree before the agent starts:")
        if has["lanes"]:
            out.append(f"- A lane: make its worktree first (`orca-ide worktree create ... --json`, as for an OpenCode "
                       f"lane, for Claude too), then `{lc} lane prepare {repo} <worktree> --role lanes`, then "
                       "`worker-start --worktree path:<worktree>`.")
        if has["reviewer"]:
            out.append(f"- A reviewer: `{lc} lane prepare {repo} <lane worktree> --role reviewer` before its "
                       "worker-start. It shares the lane's worktree, so the lane sees those extras too from its next "
                       "start.")
        out.append("- If prepare exits non-zero, hold the lane and tell the owner what it printed. A lane on another "
                   "host gets none of them (prepare runs on this machine).")
    return "\n".join(out) + "\n"


# --- checking ----------------------------------------------------------------------------------------------------

def which(cmd: str) -> bool:
    if "/" in cmd:
        return os.path.isfile(cmd) and os.access(cmd, os.X_OK)
    return shutil.which(cmd) is not None


def check_entry(e: dict) -> tuple:
    """(state, detail) of one extra, read only: ok, missing, changed, failing, or a note."""
    k = e["kind"]
    if k == "skill":
        if user_level(e):
            link = home() / ".agents" / "skills" / e["name"]
            if link.is_symlink() and os.readlink(link) == str(skill_dir(e)) and (skill_dir(e) / "SKILL.md").is_file():
                return "ok", f"installed for the user ({link})"
            if link.exists() and not link.is_symlink():
                return "ok", f"the owner's own install at {link}"
            return "missing", f"commit {e['commit'][:12]} not fetched or not linked"
        return ("ok", f"fetched at {e['commit'][:12]}") if (skill_dir(e) / "SKILL.md").is_file() else \
            ("missing", f"commit {e['commit'][:12]} not fetched")
    if k == "mcp":
        if not which(e["command"]):
            return "failing", f"the command {e['command']} is not found"
        if e.get("sha256") and sha256(Path(e["command"])) != e["sha256"]:
            return "changed", f"{e['command']} changed since it was added"
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
    if k == "agent-hook":
        f = hook_script(e)
        oc = "" if e["event"] in OPENCODE_EVENTS else "; Claude only"
        if not f.is_file():
            return "missing", (f"{f} is missing" if e.get("script") else f"commit {e['commit'][:12]} not fetched")
        if not os.access(f, os.X_OK):
            return "failing", f"{f} is not executable"
        if e.get("sha256") and sha256(f) != e["sha256"]:
            return "changed", f"{f} changed since it was added"
        return "ok", f"{f}{oc}"
    if k == "permission":
        return "ok", ("" if oc_rule(e) else "Claude only (OpenCode has no rule like it)")
    if k == "plugin":
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
    return "ok", ""


def check(store: "Path | None", folder: "Path | None" = None) -> list:
    """Every extra of a project (or the machine, store None), with its roles and state; and, with the coordinator's
    folder, whether it holds what refresh would put there. [{id, origin, for, state, detail}]."""
    rows = []
    entries = effective(store)
    for e in entries:
        state, detail = check_entry(e)
        rows.append({"id": ident(e), "origin": e.get("origin"), "for": e.get("for") or list(SCOPES),
                     "state": state, "detail": detail, "what": describe(e)})
    if folder is not None:
        try:
            lines, _ = apply_folder(folder, for_scope(entries, "coordinator"), ["coordinator"], dry=True)
        except ExtrasError as e:
            lines = [str(e)]
        if lines:
            rows.append({"id": "coordinator folder", "origin": "project", "for": ["coordinator"],
                         "state": "not applied", "detail": "; ".join(lines), "what": str(folder)})
    return rows


PROBLEMS = ("missing", "changed", "failing", "not applied")
