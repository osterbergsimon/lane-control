"""Permission rules, agent hooks and env values for the agents' sessions (lc-yw1g, EXT-1b): the rules lane-control
refuses or writes, and how Claude Code and OpenCode decide with them. Design: docs/superpowers/specs/
2026-11-27-extras-permissions-hooks-env-design.md. Read by core/extras.py (add, apply, check), core/hooks.py (the
secret shapes) and core/scripts/lane-agent-hook.

Claude Code (permissions.md, settings.md; 2.1.332): deny, then ask, then allow, whatever file or order a rule comes
from: an allow never carves an exception out of a deny. Read/Edit denies bind the file tools, redirections and the
file commands Claude recognises, not a program that opens files itself.
OpenCode (v2 permissions.md, policies.md; 2.2.1): `permissions` is ordered and the last match wins (default: allow
everything, ask for .env reads); `policies` with action tool.use run after every rule and saved approval and can only
turn allow or ask into deny.

So lane-control writes its floor (secrets, auth files, the agents' own settings, --private paths, credential commands)
beside the owner's deny rules, as Claude deny rules and as OpenCode deny rules and tool.use deny policies, and never
writes an allow (an allow on OpenCode would lift a deny someone else wrote; allow extras are lc-pwdq). On Claude the
floor binds Claude's file tools and the file commands it recognises; on OpenCode it is advisory: 2.2.1's grep tool, its
shell and a read through a link never present the resource the floor names (lc-mpb5)."""
import os
import re

# --- the real shapes of a secret (shared with core/hooks.py) -----------------------------------------------------

# The real shapes of a token or key: a known prefix at a word's start with a long run after it (so `task-tracker`,
# `desk-4ab` or `fix-disk-usage` are names, not keys), a JWT, a private key block, a webhook URL.
SECRET_SHAPE = re.compile(
    r"(?<![A-Za-z0-9])(?:sk-(?:ant-|proj-|live-|test-)?|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|glpat-|xox[abposr]-|"
    r"AKIA|ASIA|AIza|ya29\.)[A-Za-z0-9_-]{16,}"
    r"|(?<![A-Za-z0-9])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"
    r"|-----BEGIN[A-Z ]*PRIVATE KEY-----"
    r"|https://hooks\.slack\.com/services/\S+|https://discord(?:app)?\.com/api/webhooks/\S+")


def key_like(text: str) -> bool:
    """A real key in a name or a path: a SECRET_SHAPE match that also holds one unbroken run of 16 or more letters
    and digits with both in it (`sk-1234-add-login-validation` or `sk-learn-experiments-2` is a name; a key's body is
    one long run)."""
    for m in SECRET_SHAPE.finditer(text):
        for run in re.findall(r"[A-Za-z0-9]{16,}", m.group(0)):
            if re.search(r"[0-9]", run) and re.search(r"[A-Za-z]", run):
                return True
        if m.group(0).startswith("-----BEGIN"):
            return True
    return False


class RuleError(Exception):
    """A rule, hook or env value refused: the message says why, never the value."""


# --- the floor ----------------------------------------------------------------------------------------------------

HOME_SECRETS = (".ssh/**", ".gnupg/**", ".aws/**", ".azure/**", ".kube/**", ".config/gcloud/**", ".config/gh/**",
                ".docker/config.json", ".netrc", ".git-credentials", ".npmrc", ".pypirc", ".password-store/**",
                ".local/share/keyrings/**", ".claude/.credentials.json", ".claude.json",
                ".local/share/opencode/auth.json", ".local/share/opencode/opencode.db*", ".codex/auth.json")
REPO_SECRETS = (".env", ".env.*")  # at any depth (templates too: Claude cannot carve one out of a deny)
# what an agent could rewrite to change its own rules, hooks, MCP servers or instructions (not Claude's own memory and
# plan files under ~/.claude/projects and ~/.claude/plans, which it writes itself)
HOME_NO_EDIT = (".claude/settings.json", ".claude/settings.local.json", ".claude/CLAUDE.md", ".claude/skills/**",
                ".claude/agents/**", ".claude/commands/**", ".claude/hooks/**", ".claude/plugins/**",
                ".claude/output-styles/**", ".config/opencode/**", ".config/lane-control/**",
                ".local/share/lane-control/**", ".local/state/lane-control/**", ".agents/**")
COMMANDS = ("gh auth token *", "gh auth status --show-token *", "gh auth status -t *", "git credential *",
            "git credential-store *", "git credential-cache *", "printenv *", "env", "export -p", "declare -p *",
            "declare -x *", "security find-generic-password *", "security find-internet-password *",
            "secret-tool lookup *", "secret-tool search *")
XDG = {".config/": "config", ".local/share/": "data", ".local/state/": "state", ".claude/": "claude"}


def private_glob(g: str) -> str:
    """A --private glob as a path pattern: a folder (`data/`) is everything under it."""
    g = g.strip().lstrip("/")
    return g + "**" if g.endswith("/") else g


def home_paths(home: str, rel: str, dirs: dict) -> list:
    """A path under HOME and, when it is in an XDG folder (or Claude's config folder) the environment moved, the same
    path there too: the floor follows XDG_CONFIG_HOME, XDG_DATA_HOME, XDG_STATE_HOME and CLAUDE_CONFIG_DIR."""
    out = [f"{home}/{rel}"]
    for prefix, key in XDG.items():
        if rel.startswith(prefix) and dirs.get(key):
            out.append(f"{str(dirs[key]).rstrip('/')}/{rel[len(prefix):]}")
    return list(dict.fromkeys(out))


def floor(home: str, folder: str, root: str, private: list, own: list = (), dirs: "dict | None" = None,
          part: str = "full") -> list:
    """lane-control's own denies for a session in `folder` (a lane's worktree, or the main checkout `root`), as
    neutral items: {"op": "read"|"edit", "abs": absolute glob} (a read item denies edits too), {"op": "read", "any":
    glob} (anywhere: in this folder, the main checkout and every worktree of the project, wherever Orca put it),
    {"op": "cmd", "cmd": pattern}. `own`: the files lane-control writes in that folder (relative), which the session
    may not edit. part "guard" (agent hooks with no deny rule): only the edits that would take a guard out (the files
    written here, the agents' settings, lane-control's own folders)."""
    home, folder, root = (str(x).rstrip("/") for x in (home, folder, root))
    dirs = dirs or {}
    items = []
    if part == "full":
        items += [{"op": "read", "abs": p} for s in HOME_SECRETS for p in home_paths(home, s, dirs)]
        items += [{"op": "read", "any": n} for n in REPO_SECRETS]
        for g in private or []:
            items.append({"op": "read", "abs": f"{root}/{private_glob(g)}"})
            items.append({"op": "read", "any": private_glob(g)})
    items += [{"op": "edit", "abs": p} for s in HOME_NO_EDIT for p in home_paths(home, s, dirs)]
    items.append({"op": "edit", "abs": f"{root}/.claude/settings.local.json"})
    items += [{"op": "edit", "abs": f"{folder}/{rel}"} for rel in own]
    if part == "full":
        items += [{"op": "cmd", "cmd": c} for c in COMMANDS]
    seen, out = set(), []
    for it in items:
        k = json_key(it)
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def json_key(it: dict) -> str:
    return "|".join(f"{k}={it[k]}" for k in sorted(it))


def claude_path(abs_glob: str, home: str) -> str:
    """An absolute glob as a Claude path pattern: `~/...` under HOME, else `//...` (a single `/` would anchor at the
    settings file's folder or the working directory, not the filesystem root)."""
    home = str(home).rstrip("/")
    if abs_glob.startswith(home + "/"):
        return "~/" + abs_glob[len(home) + 1:]
    return "/" + abs_glob


def claude_floor(items: list, home: str) -> list:
    out = []
    for it in items:
        if it["op"] == "cmd":
            out.append(f"Bash({it['cmd']})")
        elif "any" in it:
            p = f"//**/{it['any']}"
            out += [f"Read({p})", f"Edit({p})"]
        else:
            p = claude_path(it["abs"], home)
            out += [f"Read({p})", f"Edit({p})"] if it["op"] == "read" else [f"Edit({p})"]
    return list(dict.fromkeys(out))


def oc_glob(g: str) -> str:
    """Claude's `**` and `*` both become OpenCode's `*` (it matches `/` too): wider, so safe for a deny."""
    return re.sub(r"\*+", "*", g)


def opencode_floor(items: list, folder: str) -> list:
    """The floor as OpenCode tool.use deny policies (`<action>:<resource>`)."""
    folder = str(folder).rstrip("/")
    res = []
    for it in items:
        if it["op"] == "cmd":  # also after a wrapper or an assignment (timeout 5 ..., FOO=1 ...)
            res += [f"shell:{w}{f}" for f in command_forms(it["cmd"]) for w in ("", "* ")]
            continue
        acts = ("read", "edit") if it["op"] == "read" else ("edit",)
        if "any" in it:
            g = oc_glob(it["any"])
            pats = [g, f"*/{g}"]
        else:
            g = oc_glob(it["abs"])
            pats = [g] + ([g[len(folder) + 1:]] if g.startswith(folder + "/") else [])  # internal paths are relative
            if it["op"] == "read" and g.endswith("/*"):  # a secret folder: also where the shell scanner looks
                res += [f"external_directory:{g}", f"external_directory:{g[:-2]}"]
        res += [f"{a}:{p}" for a in acts for p in pats]
    return [{"action": "tool.use", "resource": r, "effect": "deny"} for r in dict.fromkeys(res)]


# --- rules: parse, refuse, translate --------------------------------------------------------------------------------

EFFECTS = ("deny",)  # allow rules are not extras: lc-pwdq
TOOL = re.compile(r"[A-Za-z*][A-Za-z0-9_*-]{0,127}")
MCP = re.compile(r"mcp__([A-Za-z0-9_-]+?)__([A-Za-z0-9_*-]+)")
FILE_TOOLS = ("Read", "Edit", "Write", "NotebookEdit")


def parse(rule: str) -> tuple:
    """(tool, specifier or None) of a Claude rule `Tool` or `Tool(specifier)`; RuleError when it is not one."""
    rule = (rule or "").strip()
    if not rule or len(rule) > 1000 or any(ord(c) < 32 for c in rule):
        raise RuleError("a rule is Tool or Tool(specifier) on one line (for example Bash(npm run test *))")
    m = re.fullmatch(r"([^()\s]+)(?:\((.*)\))?", rule, re.S)
    if not m or not (TOOL.fullmatch(m[1]) or MCP.fullmatch(m[1])):
        raise RuleError("a rule is Tool or Tool(specifier) (for example Bash(npm run test *) or Read(docs/**))")
    spec = m[2]
    return m[1], (spec.strip() if spec is not None else None)


# a secret-named key or option given a value: name=value, name: value, "name": "value", --name value, --name=value
KEY_VALUE = re.compile(r"""(?P<q>["']?)(?P<k>[A-Za-z_][A-Za-z0-9_.-]{0,63})(?P=q)(?P<sep>\s*[:=]\s*)"""
                       r"""(?P<v>"[^"]*"|'[^']*'|[^\s,;&}\]]+)""")
OPTION_VALUE = re.compile(r"(?P<k>--?[A-Za-z][A-Za-z0-9_-]{0,63})(?P<sep>\s+)(?P<v>[^\s-]\S*)")
REF_ONLY = re.compile(r"""["']?\$\{[A-Z][A-Z0-9_]{0,127}\}["']?""")
MORE_KEYS = re.compile(r"(?<![A-Za-z0-9])(?:sk_live_|sk_test_|rk_live_|rk_test_|whsec_|xapp-|npm_|pypi-|"
                       r"AGE-SECRET-KEY-)[A-Za-z0-9_-]{12,}|X-Amz-(?:Signature|Credential|Security-Token)=", re.I)
PASSWORD_FLAG = re.compile(r"\b(?:sshpass\s+-p\s*\S+|(?:mysql|mysqldump|mysqladmin|mariadb)\b[^|;&]*\s-p\S+)")


def secret_text(text: str, secret_name) -> bool:
    """Best effort: text (an env value, a rule) that carries a secret: a key's real shape, a password in a URL, an
    auth header, a secret-named assignment, JSON key, option or query parameter given a value (a ${NAME} reference is
    fine), or a password after sshpass -p or mysql -p. A short password after another program's -p cannot be told from
    anything else."""
    if key_like(text) or MORE_KEYS.search(text) or PASSWORD_FLAG.search(text) or \
            re.search(r"://[^/@\s]*:[^/@\s]*@", text) or \
            re.search(r"(authorization\s*:|\bbearer\s+[A-Za-z0-9._~+/-]{8,})", text, re.I):
        return True
    for rx in (KEY_VALUE, OPTION_VALUE):
        for m in rx.finditer(text):
            name = m["k"].lstrip("-").replace("-", "_").replace(".", "_")
            if secret_name(name) and not REF_ONLY.fullmatch(m["v"]) and not re.fullmatch(r"[*?)\"']*", m["v"]):
                return True
    return False


def secretish(rule: str, secret_name=None) -> bool:
    """A rule that carries what looks like a secret: refused, never shown."""
    return secret_text(rule, secret_name or (lambda n: False))


def bash_spec(spec: str) -> str:
    """`:*` at the end is the same as ` *` (Claude: only recognised at the end)."""
    return spec[:-2] + " *" if spec.endswith(":*") else spec


def command_forms(p: str) -> list:
    """A Bash pattern and, when it ends in ` *` with no other wildcard, the bare command it also matches."""
    p = bash_spec(p)
    return [p, p[:-2]] if p.endswith(" *") and p.count("*") == 1 else [p]


def deny_problem(rule: str, secret_name=None) -> "str | None":
    if secretish(rule, secret_name):
        return "it looks like it carries a key, token or password"
    parse(rule)
    return None


def to_opencode(rule: str, home: str, folder: str) -> list:
    """[(action, resource)] OpenCode checks for what a Claude rule names; [] when it has no OpenCode form."""
    tool, spec = parse(rule)
    if tool == "Bash":
        return [("shell", "*" if spec in (None, "*", "") else bash_spec(spec))]
    if tool in FILE_TOOLS:
        act = "read" if tool == "Read" else "edit"
        return [(act, r) for r in oc_paths(spec, home, folder)]
    if tool == "WebFetch":
        if spec in (None, "*"):
            return [("webfetch", "*")]
        m = re.fullmatch(r"domain:([A-Za-z0-9.*-]+)", spec or "")
        return [("webfetch", f"*://{m[1]}"), ("webfetch", f"*://{m[1]}/*")] if m else []
    if tool == "WebSearch":
        return [("websearch", spec or "*")]
    m = MCP.fullmatch(tool)
    if m:
        name = re.sub(r"[^A-Za-z0-9_*-]", "_", f"{m[1]}_{m[2]}")
        return [(name, "*")]
    if tool in ("Skill", "Agent") and spec and ":" not in spec:
        return [("skill" if tool == "Skill" else "subagent", spec)]
    return []


def oc_paths(spec, home: str, folder: str) -> list:
    if spec in (None, "*", "**", ""):
        return ["*"]
    home, folder = str(home).rstrip("/"), str(folder).rstrip("/")
    p = spec.strip()
    if p.startswith("//"):
        return [oc_glob(p[1:])]
    if p.startswith("~/"):
        return [oc_glob(f"{home}/{p[2:]}")]
    p = p[1:] if p.startswith("/") else p[2:] if p.startswith("./") else p
    p = oc_glob(p)
    out = [p, f"{folder}/{p}"]
    if "/" not in p.rstrip("*").rstrip("/") or spec.lstrip("./").startswith("**"):  # a bare name: at any depth
        bare = p[2:] if p.startswith("*/") else p
        out += [f"*/{bare}", bare]
    return list(dict.fromkeys(out))


def claude_permissions(denies: list, items: list, home: str) -> dict:
    """{"deny": the owner's denies and the floor}: lane-control never writes an allow."""
    return {"deny": list(dict.fromkeys(list(denies) + claude_floor(items, home)))}


def opencode_permissions(denies: list, items: list, home: str, folder: str) -> tuple:
    """(permissions, policies): the owner's denies as rules, and every deny again, with the floor, as tool.use deny
    policies (nothing a later rule or saved approval allows lifts one). lane-control never writes an allow: on
    OpenCode, whose last match wins, it would lift a deny the owner's global config or an agent wrote."""
    deny_pairs = [(a, r) for rule in denies for a, r in to_opencode(rule, home, folder)]
    perms = [{"action": a, "resource": r, "effect": "deny"} for a, r in deny_pairs]
    policies = [{"action": "tool.use", "resource": f"{a}:{r}", "effect": "deny"} for a, r in deny_pairs]
    policies += opencode_floor(items, folder)
    seen, uniq = set(), []
    for p in policies:
        if p["resource"] not in seen:
            seen.add(p["resource"])
            uniq.append(p)
    return perms, uniq


# --- how each harness decides (a model of its documented rules, for the proofs in the tests) ----------------------

WRAPPERS = ("timeout", "time", "nice", "nohup", "stdbuf", "command", "builtin", "noglob")


def subcommands(cmd: str) -> list:
    parts = [x.strip() for x in re.split(r"&&|\|\||;|\|&|\||&|\n", cmd)]
    return [x for x in parts if x]


def strip_command(c: str, deny: bool) -> str:
    """Claude strips wrappers, and for a deny any leading assignment, before matching."""
    words = c.split()
    while words:
        if deny and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", words[0]):
            words = words[1:]
        elif words[0] in WRAPPERS:
            words = words[1:]
            while words and (words[0].startswith("-") or re.fullmatch(r"[0-9.]+[smhd]?", words[0])):
                words = words[1:]
        else:
            break
    return " ".join(words)


def bash_matches(pattern: str, cmd: str) -> bool:
    if pattern in (None, "", "*"):
        return True
    for form in command_forms(pattern):
        if re.fullmatch(".*".join(re.escape(x) for x in form.split("*")), cmd, re.S):
            return True
    return False


def claude_path_regex(pattern: str, home: str, cwd: str, settings_dir: str, effect: str) -> str:
    home, cwd = str(home).rstrip("/"), str(cwd).rstrip("/")
    p = pattern
    if p.startswith("//"):
        base, rest = "", p[1:]
    elif p.startswith("~/"):
        base, rest = home, "/" + p[2:]
    elif p.startswith("/"):
        base, rest = str(settings_dir).rstrip("/"), p
    else:
        rest = p[2:] if p.startswith("./") else p
        single = "/" not in rest.rstrip("*").rstrip("/") or (effect != "allow" and re.fullmatch(r"[^/]+/\*\*", rest))
        base, rest = cwd, ("/**/" + rest if single else "/" + rest)
    out, i = re.escape(base), 0
    while i < len(rest):
        if rest.startswith("/**/", i):
            out += "(?:/.*)?/"
            i += 4
        elif rest.startswith("**", i):
            out += ".*"
            i += 2
        elif rest[i] == "*":
            out += "[^/]*"
            i += 1
        elif rest[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(rest[i])
            i += 1
    return out


def claude_decision(perms: dict, tool: str, arg: str, home: str, cwd: str, settings_dir: "str | None" = None) -> str:
    """Claude Code's answer for one call (Bash: a command; Read/Edit/Write: an absolute path), from merged
    {"allow": [...], "deny": [...]}: deny first, then allow, else "default" (the mode decides)."""
    settings_dir = settings_dir or cwd

    def hit(rule, effect):
        t, spec = parse(rule)
        if tool == "Bash":
            if t not in ("Bash", "*"):
                return False
            subs = [strip_command(c, effect == "deny") for c in subcommands(arg)]
            if effect == "deny":
                return any(bash_matches(spec, c) for c in subs)
            return all(bash_matches(spec, c) for c in subs)
        family = {"Read": ("Read",), "Edit": ("Edit", "Read") if effect == "deny" else ("Edit",),
                  "Write": ("Edit", "Write", "Read") if effect == "deny" else ("Edit", "Write")}[tool]
        if t not in family and t != "*":
            return False
        if spec in (None, "*", "**"):
            return True
        return bool(re.fullmatch(claude_path_regex(spec, home, cwd, settings_dir, effect), arg))
    if any(hit(r, "deny") for r in perms.get("deny", [])):
        return "deny"
    if any(hit(r, "allow") for r in perms.get("allow", [])):
        return "allow"
    return "default"


OPENCODE_BASE = [{"action": "*", "resource": "*", "effect": "allow"},
                 {"action": "read", "resource": "*.env", "effect": "ask"},
                 {"action": "read", "resource": "*.env.*", "effect": "ask"},
                 {"action": "read", "resource": "*.env.example", "effect": "allow"}]


def oc_match(pattern: str, value: str) -> bool:
    rx = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern)
    if re.fullmatch(rx, value, re.S):
        return True
    return pattern.endswith(" *") and value == pattern[:-2]


def opencode_decision(permissions: list, policies: list, action: str, resource: str) -> str:
    """OpenCode's answer for one check: the default policy then the rules in order, the last match winning (a shell
    command is checked per subcommand: any deny denies, any ask asks); then tool.use policies, the last match winning,
    which can only deny."""
    resources = subcommands(resource) if action == "shell" else [resource]
    effects = []
    for res in resources:
        effect = "ask"
        for r in OPENCODE_BASE + list(permissions):
            if oc_match(r["action"], action) and oc_match(r["resource"], res):
                effect = r["effect"]
        pol = None
        for p in policies:
            if p.get("action") == "tool.use" and oc_match(p["resource"], f"{action}:{res}"):
                pol = p["effect"]
        effects.append("deny" if pol == "deny" else effect)
    return "deny" if "deny" in effects else "ask" if "ask" in effects else "allow"


# --- agent hooks ------------------------------------------------------------------------------------------------------

AGENT_EVENTS = ("PreToolUse", "PostToolUse", "SessionStart", "Stop")
OPENCODE_EVENTS = {"PreToolUse": "execute.before", "PostToolUse": "execute.after"}  # the others: Claude only
# Claude's tool names and the OpenCode 2.2.1 tools that do the same: Edit and Write also run as `patch` (2.2.1 offers
# patch instead of edit and write to some models), Task and Agent are `subagent`.
TOOL_NAMES = {"Bash": ["shell"], "Read": ["read"], "Edit": ["edit", "patch"], "MultiEdit": ["edit", "patch"],
              "Write": ["write", "patch"], "Glob": ["glob"], "Grep": ["grep"], "WebFetch": ["webfetch"],
              "WebSearch": ["websearch"], "Task": ["subagent"], "Agent": ["subagent"], "Skill": ["skill"]}
AGENT_HOOK_TIMEOUT = 60
AGENT_HOOK_TIMEOUT_MAX = 600
HOOK_MARGIN = 30  # Claude's (and the plugin's) timeout past the runner's: its start, the copy, the stop grace


def matcher(text: "str | None", event: str) -> "str | None":
    """A hook's matcher: `*` (or none) or tool names joined by `|`; only for the tool events."""
    if text in (None, "", "*"):
        return None
    if event not in ("PreToolUse", "PostToolUse"):
        raise RuleError(f"--matcher is for PreToolUse and PostToolUse only ({event} has no tool)")
    names = text.split("|")
    if not all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", n) or MCP.fullmatch(n) for n in names):
        raise RuleError("--matcher is * or tool names joined by | (Bash|Edit|Write), no regex")
    return text


def opencode_tools(m: "str | None") -> tuple:
    """(OpenCode tool ids a matcher means, or None for every tool; the names it holds with no OpenCode tool)."""
    if m is None:
        return None, []
    out, none = [], []
    for n in m.split("|"):
        mm = MCP.fullmatch(n)
        if mm:
            out.append(re.sub(r"[^A-Za-z0-9_*-]", "_", f"{mm[1]}_{mm[2]}"))
        elif n in TOOL_NAMES:
            out += TOOL_NAMES[n]
        else:
            none.append(n)
    return list(dict.fromkeys(out)), none


# --- env values ---------------------------------------------------------------------------------------------------

ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}")
# names lane-control, the harness or the shell run on, or that redirect where an agent talks to or what it runs
RESERVED = ("HOME", "PATH", "USER", "LOGNAME", "SHELL", "TMPDIR", "BASH_ENV", "ENV", "PROMPT_COMMAND", "IFS", "CDPATH",
            "NO_PROXY", "MAKEFLAGS", "MFLAGS", "MAKEFILES", "PAGER", "EDITOR", "VISUAL", "BROWSER", "LESSOPEN",
            "PERL5OPT", "PERL5LIB", "PERLLIB", "RUBYOPT", "RUBYLIB", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS",
            "JDK_JAVA_OPTIONS", "CLASSPATH", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE", "GOFLAGS", "RUSTC_WRAPPER", "CC", "CXX", "LD", "PKG_CONFIG_PATH")
RESERVED_PREFIXES = ("LANE_", "ORCA_", "XDG_", "LD_", "DYLD_", "CLAUDE_", "ANTHROPIC_", "OPENCODE_", "OPENAI_",
                     "CODEX_", "GIT_", "GH_", "SSH_", "GPG_", "NODE_", "NPM_CONFIG_", "PYTHON", "DOCKER_", "AWS_",
                     "OTEL_", "BUN_", "DENO_", "CARGO_", "PIP_", "UV_", "PNPM_", "YARN_")
RESERVED_SUFFIXES = ("_PROXY", "_PRELOAD", "_BASE_URL", "_API_BASE", "_ENDPOINT", "_CA_BUNDLE", "_CA_CERTS",
                     "_OPTIONS", "_OPTS", "_SHELL", "_HOOK", "_HOOKS", "_COMMAND", "_WRAPPER")


def env_problem(name: str, value: str, secret_name, opaque, token_value, userinfo, secret_query,
                auth_text) -> "str | None":
    """Why an env value is refused (the checks of core/extras.py passed in), or None. Never says the value."""
    if not ENV_NAME.fullmatch(name or ""):
        return "the name is capitals, digits and _ (for example APP_MODE)"
    if name in RESERVED or name.startswith(RESERVED_PREFIXES) or name.endswith(RESERVED_SUFFIXES):
        return (f"{name} is one lane-control, the harness or the shell runs on, or one that redirects where an agent "
                "talks to or what it runs (LANE_*, ORCA_*, HOME, XDG_*, PATH, CLAUDE_*, GIT_*, NODE_*, PYTHON*, "
                "*_PROXY, *_PRELOAD, *_BASE_URL, ...): it is never set by an extra")
    if secret_name(name):
        return f"{name} looks like a secret's name: a secret is never stored; keep it in the agent's own environment"
    if value is None or len(value) > 1000 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return "the value is one line of at most 1000 characters"
    if secret_text(value, secret_name) or token_value.search(value) or userinfo.search(value) or \
            secret_query.search(value) or auth_text.search(value) or opaque(value) or \
            any(opaque(x) for x in re.split(r"[\s=:,;\"'{}@/]+", value)):
        return "the value looks like a key, token or password: a secret is never stored or shown"
    return None


def runner_path(root: str) -> str:
    return os.path.join(str(root), "core", "scripts", "lane-agent-hook")
