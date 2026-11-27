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
as Claude deny rules and as OpenCode tool.use deny policies, puts every deny after every allow on OpenCode, and refuses
an allow whose shape reaches the floor by a route rules do not bind (a shell, an interpreter, a file reader)."""
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
                ".local/share/keyrings/**", ".claude/.credentials.json", ".local/share/opencode/auth.json",
                ".codex/auth.json")
REPO_SECRETS = (".env", ".env.*")  # at any depth (templates too: Claude cannot carve one out of a deny)
HOME_NO_EDIT = (".claude/**", ".claude.json", ".config/opencode/**", ".config/lane-control/**",
                ".local/share/lane-control/**", ".local/state/lane-control/**", ".agents/**")
COMMANDS = ("gh auth token *", "gh auth status --show-token *", "gh auth status -t *", "git credential *",
            "git credential-store *", "git credential-cache *", "printenv *", "env", "security find-generic-password *",
            "security find-internet-password *", "secret-tool lookup *", "secret-tool search *")


def private_glob(g: str) -> str:
    """A --private glob as a path pattern: a folder (`data/`) is everything under it."""
    g = g.strip().lstrip("/")
    return g + "**" if g.endswith("/") else g


def floor(home: str, folder: str, root: str, private: list, own: list = ()) -> list:
    """lane-control's own denies for a session in `folder` (a lane's worktree, or the main checkout `root`), as
    neutral items: {"op": "read"|"edit", "abs": absolute glob} (a read item denies edits too), {"op": "read", "any":
    glob} (anywhere: in this folder, the main checkout and every worktree of the project, wherever Orca put it),
    {"op": "cmd", "cmd": pattern}. `own`: the files lane-control writes in that folder (relative), which the session
    may not edit."""
    home, folder, root = (str(x).rstrip("/") for x in (home, folder, root))
    items = [{"op": "read", "abs": f"{home}/{s}"} for s in HOME_SECRETS]
    items += [{"op": "read", "any": n} for n in REPO_SECRETS]
    for g in private or []:
        items.append({"op": "read", "abs": f"{root}/{private_glob(g)}"})
        items.append({"op": "read", "any": private_glob(g)})
    items += [{"op": "edit", "abs": f"{home}/{s}"} for s in HOME_NO_EDIT]
    items.append({"op": "edit", "abs": f"{root}/.claude/settings.local.json"})
    items += [{"op": "edit", "abs": f"{folder}/{rel}"} for rel in own]
    items += [{"op": "cmd", "cmd": c} for c in COMMANDS]
    return items


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

EFFECTS = ("allow", "deny")
TOOL = re.compile(r"[A-Za-z*][A-Za-z0-9_*-]{0,127}")
MCP = re.compile(r"mcp__([A-Za-z0-9_-]+?)__([A-Za-z0-9_*-]+)")
FILE_TOOLS = ("Read", "Edit", "Write", "NotebookEdit")
SHELL_OPS = set(";&|<>$`\\(){}\n\r")
# Programs a Bash allow may not name: each can read (or send) any file, or run any command, by itself, which no Read
# deny binds (Claude says so for a program that opens files itself).
REFUSED_PROGRAMS = frozenset("""
sh bash zsh fish dash ksh mksh csh tcsh busybox
python python2 python3 pypy pypy3 node nodejs deno bun ruby irb perl php lua luajit tclsh wish Rscript R julia java
jshell kotlin scala groovy osascript pwsh powershell awk gawk mawk nawk sed vim vi nvim emacs ed ex nano
cat head tail less more bat tac nl od xxd hexdump strings base64 base32 grep egrep fgrep rg ag ack find fd locate
tar zip unzip gzip gunzip zcat bzip2 bzcat xz xzcat zstd 7z cpio dd cp mv ln install rsync scp sftp
curl wget nc ncat netcat socat ssh telnet ftp http https httpie aria2c
env sudo su doas xargs exec eval command builtin nohup timeout time nice ionice setsid stdbuf watch flock script
expect parallel chroot unshare nsenter strace ltrace gdb
npx bunx pnpx uvx pipx docker podman nerdctl kubectl nix nix-shell nix-build direnv devbox mise asdf
""".split())
GIT_REFUSED = ("config", "credential", "credential-store", "credential-cache", "filter-branch", "difftool",
               "mergetool", "submodule", "daemon", "upload-pack", "receive-pack", "archive")


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


def secretish(rule: str) -> bool:
    """A rule that carries what looks like a key, a password in a URL or an auth header: refused, never shown."""
    return key_like(rule) or bool(re.search(r"://[^/@\s]*:[^/@\s]*@", rule)) or \
        bool(re.search(r"(authorization\s*:|\bbearer\s+[A-Za-z0-9._~+/-]{8,})", rule, re.I))


def bash_spec(spec: str) -> str:
    """`:*` at the end is the same as ` *` (Claude: only recognised at the end)."""
    return spec[:-2] + " *" if spec.endswith(":*") else spec


def glob_intersect(a: str, b: str) -> bool:
    """Whether some text matches both wildcard patterns (`*` any run, `?` one character, the rest literal)."""
    seen, todo = set(), [(0, 0)]
    while todo:
        i, j = todo.pop()
        if (i, j) in seen:
            continue
        seen.add((i, j))
        if i == len(a) and j == len(b):
            return True
        if i < len(a) and a[i] == "*":
            todo.append((i + 1, j))
        if j < len(b) and b[j] == "*":
            todo.append((i, j + 1))
        if i < len(a) and j < len(b):
            x, y = a[i], b[j]
            if x == "*" and y == "*":
                todo += [(i, j + 1), (i + 1, j)]
            elif x == "*":
                todo.append((i, j + 1))
            elif y == "*":
                todo.append((i + 1, j))
            elif x == "?" or y == "?" or x == y:
                todo.append((i + 1, j + 1))
    return False


def command_forms(p: str) -> list:
    """A Bash pattern and, when it ends in ` *` with no other wildcard, the bare command it also matches."""
    p = bash_spec(p)
    return [p, p[:-2]] if p.endswith(" *") and p.count("*") == 1 else [p]


def allow_problem(rule: str, privates: list) -> tuple:
    """(why it is refused or None, notes) for an allow rule. privates: the --private globs of every project it
    applies to, repo-relative."""
    if secretish(rule):
        return "it looks like it carries a key, token or password", []
    tool, spec = parse(rule)
    if tool in ("Glob", "Grep"):
        return f"Claude does not match a {tool}(path) rule against a search: allow Read(<path>) instead", []
    if tool == "WebSearch":
        return None, []
    m = MCP.fullmatch(tool)
    if m:
        if "*" in m[1] or spec is not None:
            return "an MCP allow names one server: mcp__<server>__<tool> or mcp__<server>__*", []
        return None, []
    if spec is None or spec.strip("* ") == "":
        return (f"a bare {tool} (or {tool}(*)) allows every use of it, the floor's files and commands too: name what "
                "it may do"), []
    if tool == "Bash":
        return bash_problem(spec)
    if tool in FILE_TOOLS:
        return path_problem(spec, privates)
    if tool == "WebFetch":
        return (None, []) if re.fullmatch(r"domain:[A-Za-z0-9.-]+", spec) else \
            ("a WebFetch allow is WebFetch(domain:<host>)", [])
    if tool in ("Skill", "Agent"):
        return (None, []) if re.fullmatch(r"[A-Za-z0-9:_.-]+", spec) and "*" not in spec else \
            (f"a {tool} allow names one {tool.lower()}", [])
    return f"lane-control does not know what an allow for {tool} gives, so it is not taken (a deny is)", []


def bash_problem(spec: str) -> tuple:
    s = bash_spec(spec)
    if SHELL_OPS & set(s):
        return "it holds a shell operator, redirection or substitution: allow one plain command", []
    if s.startswith(("*", " ")) or "*" in s.rstrip("*").rstrip() or s.count("*") > 1 or \
            ("*" in s and not s.endswith(" *")):
        return ("a Bash allow has at most one wildcard, at the end after a space (`Bash(npm run test *)`): a "
                "wildcard before or inside the command matches other programs"), []
    words = s[:-2].split() if s.endswith(" *") else s.split()
    prog = words[0]
    if "/" in prog or prog.startswith(("~", ".")) or "=" in prog:
        return "it names a program by a path or sets a variable first: name the program on the PATH", []
    if prog in REFUSED_PROGRAMS:
        return (f"{prog} can read any file or run any command by itself, which no deny rule binds (Claude's Read "
                "denies do not reach a program's own reads): lane-control never allows it"), []
    if s.endswith(" *") and len(words) == 1:
        return f"name {prog}'s subcommand before the wildcard (`Bash({prog} <subcommand> *)`, Claude's advice)", []
    if prog == "git" and len(words) > 1 and (words[1].startswith("-") or words[1] in GIT_REFUSED):
        return f"git {words[1]} can run a program or print a credential: name a plain git subcommand", []
    for c in COMMANDS:
        if any(glob_intersect(x, y) for x in command_forms(s) for y in command_forms(c)):
            return f"it overlaps lane-control's deny of `{c}` (a command that prints a credential)", []
    return None, [f"note: Bash({spec}) runs {prog}: what that program reads or runs itself (the project's own code, "
                  "which a lane can change) is not bound by any rule"]


def path_problem(spec: str, privates: list) -> tuple:
    p = spec.strip()
    if p.startswith(("/", "~", "*", "$")) or "\\" in p:
        return ("a file allow is a plain path relative to the session's folder (`src/**`): no leading /, //, ~ or "
                "wildcard"), []
    parts = p.split("/")
    if any(x in ("", ".", "..") or x.startswith(".") for x in parts[:-1] + ([parts[-1]] if parts[-1] else [])):
        return "a file allow has no empty, `.` or `..` part and no dot-file or dot-folder (.env, .claude, .git)", []
    if parts[-1] == "":
        return "end a folder with ** (`docs/**`)", []
    pat = oc_glob(p)
    for g in privates or []:
        gp = oc_glob(private_glob(g))
        if glob_intersect(pat, gp) or glob_intersect(pat, gp + "/*") or glob_intersect(pat + "/*", gp):
            return f"it overlaps a --private path ({g}) of a project it applies to", []
    return None, []


def deny_problem(rule: str) -> "str | None":
    if secretish(rule):
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


def claude_permissions(allows: list, denies: list, items: list, home: str) -> dict:
    out = {}
    if allows:
        out["allow"] = list(dict.fromkeys(allows))
    out["deny"] = list(dict.fromkeys(list(denies) + claude_floor(items, home)))
    return out


def opencode_permissions(allows: list, denies: list, items: list, home: str, folder: str) -> tuple:
    """(permissions, policies): the owner's allows, then the owner's denies (a deny always after any allow: the last
    match wins), and every deny again, with the floor, as tool.use deny policies, which no allow can lift."""
    perms = [{"action": a, "resource": r, "effect": "allow"} for rule in allows for a, r in
             to_opencode(rule, home, folder)]
    deny_pairs = [(a, r) for rule in denies for a, r in to_opencode(rule, home, folder)]
    perms += [{"action": a, "resource": r, "effect": "deny"} for a, r in deny_pairs]
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
TOOL_NAMES = {"Bash": "shell", "Read": "read", "Edit": "edit", "Write": "write", "Glob": "glob", "Grep": "grep",
              "WebFetch": "webfetch", "WebSearch": "websearch", "Task": "task", "Agent": "task", "Skill": "skill"}
AGENT_HOOK_TIMEOUT = 60
AGENT_HOOK_TIMEOUT_MAX = 600


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


def opencode_tools(m: "str | None") -> "list | None":
    if m is None:
        return None
    out = []
    for n in m.split("|"):
        mm = MCP.fullmatch(n)
        out.append(re.sub(r"[^A-Za-z0-9_*-]", "_", f"{mm[1]}_{mm[2]}") if mm else TOOL_NAMES.get(n, n.lower()))
    return out


# --- env values ---------------------------------------------------------------------------------------------------

ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}")
RESERVED = ("HOME", "PATH", "USER", "LOGNAME", "SHELL", "TMPDIR", "BASH_ENV", "ENV", "NODE_OPTIONS", "PYTHONSTARTUP",
            "PYTHONHOME", "PROMPT_COMMAND", "IFS")
RESERVED_PREFIXES = ("LANE_", "ORCA_", "XDG_", "LD_", "DYLD_", "CLAUDE_", "ANTHROPIC_", "OPENCODE_", "GIT_", "SSH_")


def env_problem(name: str, value: str, secret_name, opaque, token_value, userinfo, secret_query,
                auth_text) -> "str | None":
    """Why an env value is refused (the checks of core/extras.py passed in), or None. Never says the value."""
    if not ENV_NAME.fullmatch(name or ""):
        return "the name is capitals, digits and _ (for example APP_MODE)"
    if name in RESERVED or name.startswith(RESERVED_PREFIXES):
        return (f"{name} is one lane-control, the harness or the shell runs on (LANE_*, ORCA_*, HOME, XDG_*, PATH, "
                "CLAUDE_*, GIT_*, LD_*, ...): it is never set by an extra")
    if secret_name(name):
        return f"{name} looks like a secret's name: a secret is never stored; keep it in the agent's own environment"
    if value is None or len(value) > 1000 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return "the value is one line of at most 1000 characters"
    if key_like(value) or token_value.search(value) or userinfo.search(value) or secret_query.search(value) or \
            auth_text.search(value) or opaque(value) or any(opaque(x) for x in re.split(r"[\s=:,;\"'{}@/]+", value)):
        return "the value looks like a key, token or password: a secret is never stored or shown"
    return None


def runner_path(root: str) -> str:
    return os.path.join(str(root), "core", "scripts", "lane-agent-hook")
