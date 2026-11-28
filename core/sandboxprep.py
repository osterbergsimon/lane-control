"""`lane-control lane prepare <project> <worktree> --sandbox` (SBX-2 task 3, lc-35qn): the lane's own clone, its
quarantine, the history and CI checks, the generated home files and a one-shot ticket, all outside the sandbox and
before the agent exists. Spec: docs/superpowers/specs/2026-11-27-agent-sandbox-design.md, "The lane's repository".

git runs only in the shared repository and in folders no agent has written: a fresh clone is made with `git init` and
a fetch of origin's base (never the owner's local branch, which may hold unpushed work), checked out on the host only
when it holds nothing but the base. A clone that exists is reused untouched (no git in it); a clone recreated from
`refs/lanes/<lane>`, and every reviewer or check clone, is made `--no-checkout`: its files are checked out inside
the sandbox (the inside entry, core/sandbox-inside/wrap.py)."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sandbox as SB  # noqa: E402

ROLES = {"lanes": "lane", "reviewer": "reviewer", "check": "check"}
NOWHERE = "/nonexistent/lane-sandbox-origin"  # remote.origin.url in a clone: `status` can say ahead, nothing fetches
SHA = re.compile(r"[0-9a-f]{40}")
GH_SLUG = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def git(*args, cwd=None, check=True, **kw) -> str:
    r = subprocess.run(["git", *map(str, args)], cwd=cwd, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, **kw)
    if check and r.returncode != 0:
        raise SB.SandboxError(f"git {' '.join(map(str, args[:3]))}: {(r.stderr.strip().splitlines() or ['failed'])[-1]}")
    return r.stdout.strip()


# --- configuration -------------------------------------------------------------------------------------------------

def conf_file() -> Path:
    v = os.environ.get("LANE_SANDBOX_CONF")
    return Path(v) if v else SB._xdg("XDG_CONFIG_HOME", ".config") / "lane-control" / "sandbox.conf"


def read_conf(p: "Path | None" = None) -> dict:
    """sandbox.conf (written by machine setup --sandbox: pinned store paths, never sourced) plus the owner's keys in
    machine.conf (SANDBOX_PROJECTS, SANDBOX_LANE_MEMORY, SANDBOX_AGENT_DIRS, SANDBOX_REAL_OPENCODE[2])."""
    import shlex
    conf = {}
    p = p or conf_file()
    try:
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if re.fullmatch(r"SANDBOX_[A-Z0-9_]+", k):
                conf[k] = " ".join(shlex.split(v)) if v else ""
    except FileNotFoundError:
        pass
    try:
        import machineconf
        mc = machineconf.load()
        for k, v in mc.items():
            if k.startswith("SANDBOX_") and v not in ("", None):
                conf[k] = v
    except Exception:  # no machine.conf: the generated file alone
        pass
    return conf


def lc_version() -> str:
    """The lane-control commit this code is: an installed tree's folder name, else the checkout's HEAD."""
    root = HERE.parent
    if SB.NONCE.fullmatch(root.name) and len(root.name) == 40:
        return root.name
    try:
        return git("-C", root, "rev-parse", "HEAD")
    except SB.SandboxError:
        return "unknown"


PIN_KEYS = ("SANDBOX_BWRAP", "SANDBOX_PASTA", "SANDBOX_NSENTER", "SANDBOX_NFT")


def selftest_current(conf: dict) -> "str | None":
    """None when the selftest passed for this lane-control and these pins; else why not."""
    try:
        st = json.loads(SB.selftest_file().read_text())
    except FileNotFoundError:
        return "the sandbox selftest has not run on this machine"
    except (OSError, ValueError):
        return "the sandbox selftest result cannot be read"
    if not st.get("ok"):
        return "the last sandbox selftest failed"
    if st.get("lane_control") != lc_version():
        return "the sandbox selftest ran for another lane-control version"
    if any(st.get("pins", {}).get(k) != conf.get(k) for k in PIN_KEYS):
        return "the sandbox selftest ran with other pinned binaries"
    return None


# --- CI (owner 2026-11-28: read-only token, no secrets, no self-hosted runners; refuse otherwise) -----------------

def gh_json(path: str) -> dict:
    r = subprocess.run(["gh", "api", "--method=GET", path], capture_output=True, text=True, timeout=60,
                       stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise SB.SandboxError(f"gh api {path} failed")
    return json.loads(r.stdout or "{}")


def github_slug(root: Path) -> "str | None":
    try:
        url = git("-C", root, "remote", "get-url", "origin")
    except SB.SandboxError:
        return None
    m = re.match(r"(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)([^/]+/[^/]+?)(?:\.git)?/?$", url)
    return m.group(1) if m and GH_SLUG.fullmatch(m.group(1)) else None


def ci_check(slug: "str | None") -> list:
    """[(ok, line)] for the project's GitHub Actions settings; a value that cannot be read is not ok."""
    if not slug:
        return [(True, "no GitHub remote: no Actions to check")]
    out = []

    def one(what, path, good, show):
        try:
            d = gh_json(path)
            out.append((good(d), f"{what}: {show(d)}"))
        except (SB.SandboxError, ValueError, OSError, subprocess.TimeoutExpired, KeyError, TypeError):
            out.append((False, f"{what}: cannot be read (gh api {path}; needs a token that may read the repo's "
                               "Actions settings)"))
    one("default workflow token", f"repos/{slug}/actions/permissions/workflow",
        lambda d: d["default_workflow_permissions"] == "read", lambda d: d["default_workflow_permissions"])
    one("Actions secrets", f"repos/{slug}/actions/secrets", lambda d: int(d["total_count"]) == 0,
        lambda d: str(int(d["total_count"])))
    try:
        org = (gh_json(f"repos/{slug}").get("owner") or {}).get("type") == "Organization"
    except (SB.SandboxError, ValueError, OSError, subprocess.TimeoutExpired, AttributeError):
        org = True  # cannot tell: check it, and a failed read refuses
    if org:
        one("organization secrets for the repo", f"repos/{slug}/actions/organization-secrets",
            lambda d: int(d["total_count"]) == 0, lambda d: str(int(d["total_count"])))
    one("self-hosted runners", f"repos/{slug}/actions/runners", lambda d: int(d["total_count"]) == 0,
        lambda d: str(int(d["total_count"])))
    try:
        envs = [e.get("name") for e in gh_json(f"repos/{slug}/environments").get("environments") or []]
    except (SB.SandboxError, ValueError, OSError, subprocess.TimeoutExpired, AttributeError):
        envs = None
    if envs is None:
        out.append((False, "environments: cannot be read"))
    for e in envs or []:
        if isinstance(e, str) and re.fullmatch(r"[A-Za-z0-9 ._-]{1,100}", e):
            one(f"secrets of environment {e}", f"repos/{slug}/environments/{e}/secrets",
                lambda d: int(d["total_count"]) == 0, lambda d: str(int(d["total_count"])))
        else:
            out.append((False, "an environment with an unusual name: check its secrets by hand"))
    return out


# --- the clone -----------------------------------------------------------------------------------------------------

def looks_like_repo(clone: Path) -> bool:
    """A clone that is still a git repo, judged by its files alone (no git runs in it)."""
    g = clone / ".git"
    return (not g.is_symlink() and g.is_dir() and (g / "HEAD").is_file() and not (g / "HEAD").is_symlink()
            and (g / "objects").is_dir() and (g / "config").is_file())


def lane_branch(repo: Path, wt: Path) -> str:
    """The Orca worktree's branch, from the shared repo's worktree list (no git in the worktree)."""
    cur = None
    for line in git("-C", repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            cur = Path(line[9:]).resolve()
        elif line.startswith("branch refs/heads/") and cur == wt:
            return line[len("branch refs/heads/"):]
    raise SB.SandboxError(f"{wt} has no branch in {repo}'s worktree list: give the lane's own Orca worktree")


def base_branch(repo: Path, st: dict, given: "str | None") -> tuple:
    base = given or st.get("base") or ""
    if not base:
        try:
            base = git("-C", repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").split("/", 1)[1]
        except (SB.SandboxError, IndexError):
            base = "main"
    try:
        sha = git("-C", repo, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{base}^{{commit}}")
    except SB.SandboxError:
        raise SB.SandboxError(f"{repo} has no origin/{base}: fetch it there first (git fetch origin {base})") from None
    return base, sha


def init_clone(clone: Path, repo: Path, base: str, base_sha: str) -> None:
    SB.private_dir(clone.parent)
    git("init", "-q", clone)
    git("-C", clone, "fetch", "-q", "--no-tags", "--no-write-fetch-head", repo,
        f"{base_sha}:refs/remotes/origin/{base}")
    git("-C", clone, "config", "remote.origin.url", NOWHERE)
    git("-C", clone, "config", "remote.origin.fetch", f"+refs/heads/*:refs/remotes/origin/*")


def make_quarantine(q: Path, repo: Path, base: str, base_sha: str, pushed: "str | None") -> None:
    git("init", "-q", "--bare", q)
    git("-C", q, "fetch", "-q", "--no-tags", "--no-write-fetch-head", repo, f"{base_sha}:refs/heads/{base}")
    if pushed:
        git("-C", q, "fetch", "-q", "--no-tags", "--no-write-fetch-head", repo, f"{pushed}:refs/heads/lane")


def history_or_refuse(clone: Path, private: list, what: str) -> None:
    hits = SB.history_hits(clone, ["--all"], private)
    if hits:
        raise SB.SandboxError(f"{what} refused: its history holds private or secret-env paths: {', '.join(hits[:8])}"
                              + (f" and {len(hits) - 8} more" if len(hits) > 8 else "")
                              + " (a --private path or a .env file; a sandboxed lane cannot be given this history)")


def lane_ref(repo: Path, lane: str) -> "str | None":
    try:
        return git("-C", repo, "rev-parse", "--verify", "-q", f"refs/lanes/{lane}^{{commit}}") or None
    except SB.SandboxError:
        return None


# --- generated home files ------------------------------------------------------------------------------------------

def owner_opencode() -> dict:
    d = SB._xdg("XDG_CONFIG_HOME", ".config") / "opencode"
    for n in ("opencode.json", "opencode.jsonc"):
        f = d / n
        if f.is_file():
            text = f.read_text()
            if n.endswith("c"):
                text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
            try:
                return json.loads(text)
            except ValueError:
                raise SB.SandboxError(f"{f} cannot be read as JSON") from None
    return {}


def lane_opencode(owner: dict, model: "str | None") -> tuple:
    """(config, key env name, gateway (host, port) or None): the owner's provider entries without any inline key
    (one with an inline key refuses), the model, nothing else (no MCP, no permission rules, no plugins list)."""
    providers, key_env, gateway = {}, None, None
    for name, p in (owner.get("provider") or {}).items():
        if not isinstance(p, dict):
            continue
        opts = dict(p.get("options") or {})
        for k in list(opts):
            if k.lower() in ("apikey", "api_key", "token", "authorization", "headers"):
                v = opts[k]
                m = re.fullmatch(r"\{env:([A-Z_][A-Z0-9_]{0,63})\}", v) if isinstance(v, str) else None
                if not m:
                    raise SB.SandboxError(f"OpenCode provider {name!r} has an inline {k} in the owner's config: "
                                          "a sandboxed lane takes a key only by an environment variable "
                                          "({env:NAME}); nothing was prepared")
                key_env = key_env or m.group(1)
        base = opts.get("baseURL")
        if isinstance(base, str) and not gateway:
            u = urlparse(base)
            if u.hostname:
                gateway = (u.hostname, u.port or (443 if u.scheme == "https" else 80))
        providers[name] = {**{k: v for k, v in p.items() if k != "options"}, "options": opts}
    cfg = {"$schema": "https://opencode.ai/config.json", "autoupdate": False, "share": "disabled"}
    if providers:
        cfg["provider"] = providers
    if model or owner.get("model"):
        cfg["model"] = model or owner.get("model")
    return cfg, key_env, gateway


def owner_identity() -> tuple:
    def get(k):
        try:
            return git("config", "--global", "--get", k)
        except SB.SandboxError:
            return ""
    name, email = get("user.name"), get("user.email")
    if not name or not email:
        raise SB.SandboxError("no global git user.name and user.email: a sandboxed lane commits with them")
    return name, email


# --- prepare -------------------------------------------------------------------------------------------------------

def tool_path(conf: dict, st: dict) -> list:
    """lane-control's list (built outside at machine setup) and the project's own store paths from its settings
    store, never anything in the worktree."""
    out = []
    for p in (conf.get("SANDBOX_TOOL_PATH") or "").split(":") + list(st.get("sandbox_tools") or []):
        if not p:
            continue
        if not (isinstance(p, str) and p.startswith("/nix/store/") and ".." not in p):
            raise SB.SandboxError(f"tool path {p!r}: only /nix/store paths (lane-control's list or the project's "
                                  "settings store)")
        if p not in out:
            out.append(p)
    if not out:
        raise SB.SandboxError("no tool path: run lane-control machine setup --sandbox")
    return out


def find_tool(path: list, name: str) -> "str | None":
    for d in path:
        f = Path(d) / name
        if f.is_file() and os.access(f, os.X_OK):
            return str(f)
    return None


def projects_on(conf: dict) -> list:
    return (conf.get("SANDBOX_PROJECTS") or "").split()


def prepare(root: Path, st: dict, wt: Path, role: str, lane: "str | None", issue: "str | None" = None,
            sha: "str | None" = None, review_files: "list | None" = None, review_write: "str | None" = None,
            base: "str | None" = None, discard: bool = False, model: "str | None" = None, say=print) -> str:
    """Make or resume the clone and write a ticket. Returns the nonce; raises SandboxError on any refusal (nothing
    is handed over then)."""
    if role not in ROLES:
        raise SB.SandboxError(f"--role {role}: lanes, reviewer or check")
    trole = ROLES[role]
    conf = read_conf()
    name = st.get("name") or root.name
    if name not in projects_on(conf) and str(root) not in projects_on(conf):
        raise SB.SandboxError(f"{name} is not a sandboxed project: add it to SANDBOX_PROJECTS in machine.conf "
                              "(after lane-control machine check --sandbox passes)")
    why = selftest_current(conf)
    if why:
        raise SB.SandboxError(f"{why}: run lane-control sandbox selftest (a skip counts as a failure)")
    missing = [k for k in PIN_KEYS if not os.path.isabs(conf.get(k) or "")]
    if missing:
        raise SB.SandboxError(f"{', '.join(missing)} not pinned: run lane-control machine setup --sandbox")
    if not lane:
        raise SB.SandboxError("--lane KEY is needed with --sandbox (the lane's identity, not the worktree's)")
    ld = SB.lane_dir(lane)
    slug = github_slug(root)
    bad = [line for ok, line in ci_check(slug) if not ok]
    if bad:
        raise SB.SandboxError(f"CI of {slug} runs a lane's code with more than a read-only token: "
                              + "; ".join(bad) + ". A sandboxed project needs a read-only default workflow token, "
                              "no Actions secrets and no self-hosted runners (lane-control machine check --sandbox)")
    tools = tool_path(conf, st)
    base, base_sha = base_branch(root, st, base)
    branch = lane_branch(root, wt)
    private = list(st.get("private") or [])
    SB.private_dir(ld)
    clone, quar = ld / "repo", ld / "quarantine.git"
    checkout = None
    pushed = lane_ref(root, lane)
    if trole == "lane":
        if clone.exists() and not looks_like_repo(clone):
            if not discard:
                raise SB.SandboxError(
                    f"the lane's clone {clone} is no longer a git repo (its .git is gone or changed): nothing was "
                    f"done. Either throw it away (--discard: the clone's unpushed work is lost) and prepare again, "
                    f"which recreates it from refs/lanes/{lane}" + (" (pushed)" if pushed else " (none pushed: a "
                                                                    "fresh clone of the base)") +
                    ", or look at it first")
            shutil.rmtree(clone)
            say(f"prepare: discarded {clone} (the owner's --discard)")
        elif clone.exists() and discard:
            raise SB.SandboxError(f"--discard is for a broken clone; {clone} is a repo: end the lane with "
                                  f"lane-control sandbox close {lane} --discard instead")
        if clone.exists():
            say(f"prepare: resume: {clone} is reused as it is (no git runs in it)")
        elif pushed:
            init_clone(clone, root, base, base_sha)
            git("-C", clone, "fetch", "-q", "--no-tags", "--no-write-fetch-head", root, f"{pushed}:refs/heads/{branch}")
            git("-C", clone, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
            checkout = branch
            try:
                history_or_refuse(clone, private, f"lane {lane}")
            except SB.SandboxError:
                shutil.rmtree(clone)
                raise
            if not quar.exists():
                make_quarantine(quar, root, base, (ld / "base").read_text().strip() if (ld / "base").is_file()
                                else base_sha, pushed)
            say(f"prepare: recreated {clone} from refs/lanes/{lane} ({pushed[:12]}); its files are checked out inside")
        else:
            init_clone(clone, root, base, base_sha)
            try:
                history_or_refuse(clone, private, f"lane {lane}")
            except SB.SandboxError:
                shutil.rmtree(clone)
                raise
            git("-C", clone, "checkout", "-q", "-b", branch, f"refs/remotes/origin/{base}")
            if quar.exists():
                shutil.rmtree(quar)
            make_quarantine(quar, root, base, base_sha, None)
            SB.write_private(ld / "base", base_sha + "\n")
            SB.write_private(ld / "delivered", base_sha + "\n")
            say(f"prepare: cloned origin/{base} ({base_sha[:12]}) into {clone} as {branch}")
    else:
        if not sha or not SHA.fullmatch(sha):
            raise SB.SandboxError(f"--role {role} needs --sha <full sha> of the lane's pushed work")
        if not pushed:
            raise SB.SandboxError(f"lane {lane} has pushed nothing (no refs/lanes/{lane}): nothing to {role}")
        if subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", sha, pushed],
                          capture_output=True, stdin=subprocess.DEVNULL).returncode:
            raise SB.SandboxError(f"{sha[:12]} is not in the lane's pushed work (refs/lanes/{lane})")
        n = 1
        while (ld / f"{role}-{n}").exists():
            n += 1
        clone = ld / f"{role}-{n}" / "repo"
        init_clone(clone, root, base, base_sha)
        git("-C", clone, "fetch", "-q", "--no-tags", "--no-write-fetch-head", root, f"{pushed}:refs/lanes/{lane}")
        try:
            history_or_refuse(clone, private, f"{role} clone")
        except SB.SandboxError:
            shutil.rmtree(clone.parent)
            raise
        (clone / ".git" / "HEAD").write_text(sha + "\n")  # detached at the sha; files checked out inside
        checkout = sha
        say(f"prepare: {role} clone {clone} of {sha[:12]}; its files are checked out inside")
    nonce = os.urandom(16).hex()
    td = ld / f"t-{nonce}"
    for sub in ("cache", "data", "run/inbox", "run/outbox", "launch", "broker", "gen/plugins", "gen/skills"):
        SB.private_dir(td / sub)
    gen = td / "gen"
    uname, email = owner_identity()
    SB.write_private(gen / "gitconfig", f"[user]\n\tname = {uname}\n\temail = {email}\n"
                     "[safe]\n\tdirectory = *\n[advice]\n\tdetachedHead = false\n")
    cfg, key_env, gateway = lane_opencode(owner_opencode(), model)
    SB.write_private(gen / "opencode.json", json.dumps(cfg, indent=1) + "\n")
    SB.write_private(gen / "resolv.conf", f"nameserver {SB.DNS}\n")
    plugin = HERE / "harnesses" / "opencode" / "lane-persona.js"
    if plugin.is_file():
        shutil.copyfile(plugin, gen / "plugins" / plugin.name)
    persona_src = root / ".lane-control" / {"lane": "lane", "reviewer": "reviewer", "check": "check"}[trole]
    persona_src = persona_src.with_suffix(".md")
    persona = gen / "persona.md"
    text = persona_src.read_text() if persona_src.is_file() and not persona_src.is_symlink() else \
        f"# {trole} of {name}\n\nYour brief is your persona; the sandbox's rules: {SB.__name__} (spec).\n"
    SB.write_private(persona, text)
    rf = [x for x in (review_files or [])]
    for x in rf + ([review_write] if review_write else []):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.md", x):
            raise SB.SandboxError(f"review file {x!r}: a plain .md name in the owner's review folder")
    lane_rec = {"lane": lane, "project": str(root), "name": name, "repo": str(root), "base": base, "branch": branch,
                "worktree": str(wt),
                "issue": issue, "gh_repo": slug, "private": private, "bd_prefix": st.get("bd_prefix") or
                (issue.split("-")[0] if issue and "-" in issue else None)}
    old = json.loads((ld / "lane.json").read_text()) if (ld / "lane.json").is_file() else {}
    if trole != "lane":  # a reviewer's or check's ticket does not rebind the lane
        lane_rec = {k: v for k, v in lane_rec.items() if k not in ("worktree", "branch") or k not in old}
    lane_rec["worktrees"] = sorted(set(old.get("worktrees") or []) | {str(wt)})
    SB.write_private(ld / "lane.json", json.dumps({**old, **lane_rec}, indent=1) + "\n")
    t = {"nonce": nonce, "role": trole, "lane": lane, "issue": issue, "project": name, "repo": str(root),
         "base": base, "branch": branch, "worktree": str(wt), "clone": str(clone), "lane_dir": str(ld),
         "ticket_dir": str(td), "home": str(Path.home()),
         "home_files": {"gitconfig": str(gen / "gitconfig"), "opencode.json": str(gen / "opencode.json"),
                        "resolv.conf": str(gen / "resolv.conf"), "plugins": str(gen / "plugins"),
                        "skills": str(gen / "skills")},
         "tool_path": tools, "bash": find_tool(tools, "bash") or conf.get("SANDBOX_BASH") or "bash",
         "persona": str(persona), "gateway_key_env": key_env, "gateway": list(gateway) if gateway else None,
         "hook_port": None, "review_files": rf, "review_write": review_write if trole == "reviewer" else None,
         "sha": sha, "private": private, "inside": str(HERE / "sandbox-inside"), "lc_tree": str(HERE.parent),
         "checkout": checkout, "gh_repo": slug, "base_sha": base_sha, "bd_prefix": lane_rec["bd_prefix"], "agent_dirs":
         [p for p in (conf.get("SANDBOX_AGENT_DIRS") or "").split(":") if p], "created": time.time()}
    SB.write_ticket(wt, t)
    SB.write_private(SB.binding_file(wt), json.dumps({"lane": lane, "project": str(root)}) + "\n")
    return nonce
