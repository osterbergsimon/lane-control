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
    except OSError as e:
        conf["_error"] = f"{p} cannot be read ({e.strerror})"
    try:
        import machineconf
        if machineconf.path().exists():
            mc = machineconf.load()
            for k, v in mc.items():
                if k.startswith("SANDBOX_") and v not in ("", None):
                    conf[k] = v
    except Exception as e:  # noqa: BLE001  a machine.conf that exists but cannot be read: said, never ignored
        conf["_error"] = f"machine.conf cannot be read ({e})"
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


GH_URL = re.compile(r"""(?ix)^(?:
      (?:[a-z0-9._-]+@)?(?:ssh\.)?github\.com:(?P<a>[^/]+/[^/]+?)                       # scp form
    | (?:ssh|git\+ssh|git|https?)://(?:[^@/]+@)?(?:ssh\.)?github\.com(?::\d+)?/(?P<b>[^/]+/[^/]+?)  # URL forms
    )(?:\.git)?/?$""")


def parse_github(url: str) -> "str | None":
    """owner/repo from a GitHub remote URL in any of git's forms (scp, ssh, ssh.github.com:443, https with userinfo,
    git://, any case), or None."""
    m = GH_URL.match(url.strip())
    slug = m and (m.group("a") or m.group("b"))
    return slug if slug and GH_SLUG.fullmatch(slug) else None


def ssh_hostname(alias: str) -> "str | None":
    """The host an ssh alias names (`ssh -G`, which only reads the ssh config), or None."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}", alias):
        return None
    try:
        r = subprocess.run(["ssh", "-G", alias], capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in r.stdout.splitlines():
        if line.lower().startswith("hostname "):
            return line.split(None, 1)[1].strip().lower()
    return None


def resolve_url(url: str) -> "str | None":
    """owner/repo of one remote URL: GitHub's forms, or an scp-form ssh alias whose hostname is GitHub's."""
    slug = parse_github(url)
    if slug:
        return slug
    m = re.fullmatch(r"(?:[A-Za-z0-9._-]+@)?([A-Za-z0-9][A-Za-z0-9._-]*):([^/][^:]*?/[^/]+?)(?:\.git)?/?", url.strip())
    if m and ssh_hostname(m.group(1)) in ("github.com", "ssh.github.com"):
        return m.group(2) if GH_SLUG.fullmatch(m.group(2)) else None
    return None


def remote_urls(root: Path) -> list:
    """Every URL the broker's `git push origin` may use, and origin's fetch URLs (review r2 M4: a pushurl counts)."""
    out = []
    for args in (("remote", "get-url", "--push", "--all", "origin"), ("remote", "get-url", "--all", "origin")):
        try:
            out += [u for u in git("-C", root, *args).splitlines() if u.strip()]
        except SB.SandboxError:
            pass
    return list(dict.fromkeys(out))


def github_slug(root: Path) -> "str | None":
    """The one GitHub repository every origin URL names, or None (none, an unresolvable one, or two repositories)."""
    urls = remote_urls(root)
    slugs = {resolve_url(u) for u in urls}
    if not urls or None in slugs or len({x.lower() for x in slugs}) != 1:
        return None
    return slugs.pop()


def ci_target(root: Path, st: dict) -> tuple:
    """(slug or None, [(ok, line)]). A remote lane-control cannot read as GitHub refuses, unless the owner recorded
    in the project's settings store that it has no CI (`"sandbox_no_ci": true`) (review r1 M6)."""
    slug = github_slug(root)
    if slug:
        return slug, ci_check(slug)
    if st.get("sandbox_no_ci") is True:
        return None, [(True, "no GitHub repository; the owner recorded that this project has no CI")]
    return None, [(False, "origin is not one GitHub repository lane-control can name (each push and fetch URL, an "
                          "ssh alias through ssh -G), so its CI cannot be checked; when the project has no CI, record "
                          "\"sandbox_no_ci\": true in its settings store")]


def ci_verdict(root: Path, st: dict, base: "str | None" = None) -> list:
    """[(ok, line)]: the one CI rule, for prepare and for machine setup --check --sandbox alike (review r2 M2): the
    repository's Actions settings (or the owner's sandbox_no_ci) and the base's workflow permissions."""
    _, lines = ci_target(root, st)
    try:
        b = base_branch(root, st, base)[0]
        writes = workflow_writes(root, b)
    except SB.SandboxError as e:
        return lines + [(False, f"the base's workflows cannot be read ({e})")]
    if writes:
        lines = lines + [(False, f"base workflows ask for a write token: {', '.join(writes[:5])}")]
    return lines


PERM_ENTRY = re.compile(r"""["']?[A-Za-z][A-Za-z-]*["']?\s*:\s*["']?(read|none)["']?""")


def _uncomment(line: str) -> str:
    return re.sub(r"(^|\s)#.*$", "", line).rstrip()


def permissions_read_only(text: str) -> bool:
    """True only when every `permissions:` value in a workflow is clearly read-only: `read-all`, `{}`, or entries that
    are all `read` or `none`, in block or flow form, any key quoting (review r2 M3). Anything else (write-all,
    write, an empty value, an alias, a form this does not read) is not: fail closed."""
    lines = text.splitlines()
    if re.search(r"\bwrite-all\b", text) or re.search(r"""["']?[A-Za-z-]+["']?\s*:\s*["']?write["']?\s*[,}]""", text):
        return False
    for i, raw in enumerate(lines):
        line = _uncomment(raw)
        m = re.match(r"""^(\s*)(?:-\s*)?["']?permissions["']?\s*:\s*(.*)$""", line)
        if not m:
            continue
        indent, val = len(m.group(1)), m.group(2).strip()
        if val:
            v = val.strip("\"'")
            if v in ("read-all", "{}"):
                continue
            if val.startswith("{") and val.endswith("}"):
                inner = val[1:-1].strip()
                if inner and not all(PERM_ENTRY.fullmatch(x.strip()) for x in inner.split(",")):
                    return False
                continue
            return False
        entries = []
        for nxt in lines[i + 1:]:
            n = _uncomment(nxt)
            if not n.strip():
                continue
            if len(n) - len(n.lstrip()) <= indent:
                break
            entries.append(n.strip())
        if not entries or not all(PERM_ENTRY.fullmatch(e) for e in entries):
            return False
    return True


def workflow_writes(root: Path, base: str) -> list:
    """Base workflow files that ask for a write token (`permissions: write-all` or `<scope>: write`), read from the
    shared repo's origin/<base> (never a lane-written tree) (review r1 m1)."""
    ref = f"refs/remotes/origin/{base}"
    try:
        names = subprocess.run(["git", "-C", str(root), "ls-tree", "-r", "-z", "--name-only", ref, "--",
                                ".github/workflows"], capture_output=True, check=True).stdout.split(b"\0")
    except subprocess.CalledProcessError:
        return []
    out = []
    for n in names:
        if not n:
            continue
        name = n.decode("utf-8", "surrogateescape")
        r = subprocess.run(["git", "-C", str(root), "cat-file", "blob", f"{ref}:{name}"], capture_output=True)
        text = r.stdout.decode("utf-8", "replace")
        if not permissions_read_only(text):
            out.append(name)
    return out


def ci_check(slug: "str | None") -> list:
    """[(ok, line)] for the project's GitHub Actions settings; a value that cannot be read is not ok."""
    if not slug:
        return [(False, "no GitHub repository named: its CI cannot be checked")]
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
        one("organization self-hosted runners", f"orgs/{slug.split('/')[0]}/actions/runners",
            lambda d: int(d["total_count"]) == 0, lambda d: str(int(d["total_count"])))
    one("self-hosted runners", f"repos/{slug}/actions/runners", lambda d: int(d["total_count"]) == 0,
        lambda d: str(int(d["total_count"])))
    try:
        d = gh_json(f"repos/{slug}/environments?per_page=100")
        envs = [e.get("name") for e in d.get("environments") or []]
        if int(d.get("total_count") or len(envs)) > len(envs):
            out.append((False, "environments: more than one page; check their secrets by hand"))
    except (SB.SandboxError, ValueError, OSError, subprocess.TimeoutExpired, AttributeError, TypeError):
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


def default_branch(repo: Path) -> list:
    try:
        return [git("-C", repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").split("/", 1)[1]]
    except (SB.SandboxError, IndexError):
        return []


def init_clone(clone: Path, repo: Path, base: str, base_sha: str) -> None:
    SB.private_dir(clone.parent)
    git("init", "-q", clone)
    git("-C", clone, "fetch", "-q", "--no-tags", "--no-write-fetch-head", repo,
        f"{base_sha}:refs/remotes/origin/{base}")
    git("-C", clone, "config", "remote.origin.url", NOWHERE)
    git("-C", clone, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")


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


ENV_REF = re.compile(r"\{env:([A-Z_][A-Z0-9_]{0,63})\}")
SECRETY = re.compile(r"(?i)(key|token|secret|passw|auth|credential|cookie|header|bearer|session|signature)")
PROVIDER_FIELDS = ("npm", "name", "api", "id")
OPTION_FIELDS = ("baseURL", "apiKey", "timeout")
MODEL_FIELDS = ("id", "name", "limit", "modalities", "reasoning", "tool_call", "attachment", "temperature", "cost",
                "release_date", "options", "variants")


MODEL_OPTION_FIELDS = {"thinking", "type", "budgetTokens", "reasoningEffort", "reasoningSummary", "textVerbosity",
                       "temperature", "topP", "topK", "maxTokens", "max_tokens", "include", "store", "effort",
                       "enabled", "parallelToolCalls"}


def _allow(obj):
    """Only the known model option keys, at any depth (review r2 m4); everything else is dropped."""
    if isinstance(obj, dict):
        return {k: _allow(v) for k, v in obj.items() if k in MODEL_OPTION_FIELDS}
    return obj if isinstance(obj, (str, int, float, bool, list)) or obj is None else None


def model_copy(m: dict) -> dict:
    out = {k: m[k] for k in MODEL_FIELDS if k in m and k not in ("options", "variants")}
    if isinstance(m.get("options"), dict):
        out["options"] = _allow(m["options"])
    if isinstance(m.get("variants"), dict):
        out["variants"] = {name: _allow(v) for name, v in m["variants"].items() if isinstance(v, dict)}
    return out


def _no_secrets(obj, where: str, envs: set) -> None:
    """Refuse a secret-looking key whose value is not an {env:NAME} reference, at any depth, and credentials in a
    URL (review r1 M9). Every env name referenced is collected in envs."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{where}.{k}"
            if SECRETY.search(str(k)) and not isinstance(v, (int, float, bool)) and v is not None:
                m = ENV_REF.fullmatch(v) if isinstance(v, str) else None
                if not m:
                    raise SB.SandboxError(f"the owner's OpenCode config has an inline secret at {here}: a sandboxed "
                                          "lane takes a key only by an environment variable ({env:NAME}); nothing "
                                          "was prepared")
                envs.add(m.group(1))
            else:
                _no_secrets(v, here, envs)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _no_secrets(v, f"{where}[{i}]", envs)
    elif isinstance(obj, str):
        for m in ENV_REF.finditer(obj):
            envs.add(m.group(1))
        if re.search(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s@]+@", obj):
            raise SB.SandboxError(f"the owner's OpenCode config has credentials in a URL at {where}: nothing was "
                                  "prepared")
        if re.match(r"(?i)[a-z][a-z0-9+.-]*://", obj) and urlparse(obj).query + urlparse(obj).fragment:
            raise SB.SandboxError(f"the owner's OpenCode config has a URL with a query or fragment at {where} (it may "
                                  "carry a token): nothing was prepared")


def lane_opencode(owner: dict, model: "str | None") -> tuple:
    """(config, key env name, gateway (host, port) or None): an allowlist copy of the owner's provider entries
    (npm, name, api, baseURL, an {env:} apiKey, timeout, the models' own fields), refused when any secret is inline
    anywhere in them; the model; nothing else (no MCP, no permission rules, no plugins list)."""
    providers, envs, gateway = {}, set(), None
    for name, p in (owner.get("provider") or {}).items():
        if not isinstance(p, dict):
            continue
        _no_secrets(p, f"provider.{name}", envs)
        opts = p.get("options") or {}
        copy = {k: p[k] for k in PROVIDER_FIELDS if k in p}
        copy["options"] = {k: opts[k] for k in OPTION_FIELDS if k in opts}
        models = p.get("models") or {}
        if isinstance(models, dict) and models:
            copy["models"] = {mid: model_copy(m) for mid, m in models.items() if isinstance(m, dict)}
        base = opts.get("baseURL")
        if isinstance(base, str) and not gateway:
            u = urlparse(base)
            if u.hostname:
                gateway = (u.hostname, u.port or (443 if u.scheme == "https" else 80))
        providers[name] = copy
    if len(envs) > 1:
        raise SB.SandboxError(f"the owner's OpenCode providers name {len(envs)} key variables ({', '.join(sorted(envs))}): "
                              "a sandboxed lane gets one; nothing was prepared")
    cfg = {"$schema": "https://opencode.ai/config.json", "autoupdate": False, "share": "disabled"}
    if providers:
        cfg["provider"] = providers
    if model or owner.get("model"):
        cfg["model"] = model or owner.get("model")
    return cfg, (next(iter(envs)) if envs else None), gateway


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

def second_clone(root: Path, ld: Path, lane: str, role: str, sha: "str | None", base: str, base_sha: str,
                 pushed: "str | None", private: list) -> Path:
    """A reviewer's or check's own clone of the lane's pushed sha, history-checked, made --no-checkout with HEAD at
    the sha (its files are checked out inside)."""
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
    (clone / ".git" / "HEAD").write_text(sha + "\n")
    return clone


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


def effective_extras(root: Path) -> list:
    try:
        import extras as X
        return [e for e in X.effective(X.store_for(root)) if e.get("kind") != "hook"]
    except Exception:  # noqa: BLE001  only for the line prepare prints
        return []


def host_bash() -> str:
    """The opener runs in pasta's namespace on the host's file system, so any host bash will do."""
    return os.path.realpath(shutil.which("bash") or "/bin/bash")


def projects_on(conf: dict) -> list:
    return (conf.get("SANDBOX_PROJECTS") or "").split()


def sandbox_projects(conf: dict) -> tuple:
    """(the checkouts SANDBOX_PROJECTS names, problems). Every project store is read: one that cannot be read or
    parsed, and an entry that matches no project (a name with no store, a comma list, a path that is no project),
    is a problem, and callers fail closed on any problem (review r2 M1)."""
    names = projects_on(conf)
    roots, by_name, problems = set(), {}, []
    if not names:
        return roots, problems
    stores = SB._xdg("XDG_CONFIG_HOME", ".config") / "lane-control" / "projects"
    try:
        files = sorted(stores.glob("*/settings.json"))
    except OSError as e:
        return roots, [f"the project stores cannot be listed ({e.strerror})"]
    known = set()
    for f in files:
        try:
            st = json.loads(f.read_text())
            if not isinstance(st, dict) or not isinstance(st.get("root"), str):
                raise ValueError("no root")
        except (OSError, ValueError) as e:
            problems.append(f"project store {f.parent.name} cannot be read ({e.__class__.__name__})")
            continue
        r = Path(st["root"]).resolve()
        known.add(r)
        by_name.setdefault(st.get("name") or r.name, set()).add(r)
    for e in names:
        if e.startswith("/"):
            r = Path(e).resolve()
            if r in known:
                roots.add(r)
            else:
                problems.append(f"SANDBOX_PROJECTS entry {e!r} is no project set up here")
        elif e in by_name:
            roots |= by_name[e]
        else:
            problems.append(f"SANDBOX_PROJECTS entry {e!r} matches no project (names are space-separated)")
    return roots, problems


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
    roots, problems = sandbox_projects(conf)
    if Path(root).resolve() not in roots:
        raise SB.SandboxError(f"{name} is not a sandboxed project: add it to SANDBOX_PROJECTS in machine.conf "
                              "(after lane-control machine setup --check --sandbox passes)"
                              + (f"; also: {'; '.join(problems)}" if problems else ""))
    if not lane:
        raise SB.SandboxError("--lane KEY is needed with --sandbox (the lane's identity, not the worktree's)")
    ld = SB.lane_dir(lane, root)
    bf = SB.binding_file(wt)
    if bf.is_file() and (json.loads(bf.read_text()).get("lane"), json.loads(bf.read_text()).get("project")) != \
            (lane, str(root)):
        raise SB.SandboxError(f"{wt} is bound to another sandboxed lane: give this lane its own worktree")
    # bound before any check that can refuse: the launcher then refuses this worktree until a prepare succeeds
    SB.write_private(bf, json.dumps({"lane": lane, "project": str(root)}) + "\n")
    if conf.get("_error"):
        raise SB.SandboxError(conf["_error"])
    why = selftest_current(conf)
    if why:
        raise SB.SandboxError(f"{why}: run lane-control sandbox selftest (a skip counts as a failure)")
    missing = [k for k in PIN_KEYS if not os.path.isabs(conf.get(k) or "")]
    if missing:
        raise SB.SandboxError(f"{', '.join(missing)} not pinned: run lane-control machine setup --sandbox")
    slug = github_slug(root)
    bad = [line for ok, line in ci_verdict(root, st, base) if not ok]
    if bad:
        raise SB.SandboxError(f"CI of {slug or name} runs a lane's code with more than a read-only token: "
                              + "; ".join(bad) + ". A sandboxed project needs a read-only default workflow token, "
                              "no Actions secrets and no self-hosted runners (lane-control machine setup --check --sandbox)")
    tools = tool_path(conf, st)
    base, base_sha = base_branch(root, st, base)
    private = list(st.get("private") or [])
    try:
        known = json.loads((ld / "lane.json").read_text())
    except (OSError, ValueError):
        known = {}
    if known and known.get("repo") != str(root):
        raise SB.SandboxError(f"lane state {ld} is of another repository ({known.get('repo')}): nothing was done")
    if not issue:
        issue = known.get("issue")  # review r1 m4, r2 m2: a resume or a reviewer keeps the lane's own issue
    if trole == "lane":
        branch = known.get("branch") or lane_branch(root, wt)  # r1 M5: the lane's branch from its first prepare
        protected = {base, *default_branch(root)}
        if branch in protected:  # review r2 m1: a lane never pushes the base (or origin's default) itself
            raise SB.SandboxError(f"the worktree is on {branch}, the base or origin's default branch: a lane gets a "
                                  "branch of its own; nothing was done")
    else:
        branch = known.get("branch")
        if not branch:
            raise SB.SandboxError(f"lane {lane} was never prepared here: prepare the lane first")
        if known.get("worktree") == str(wt):
            raise SB.SandboxError(f"a {role} gets its own worktree, never the lane's ({wt}): one-shot tickets are taken "
                                  "per worktree, so a relaunch could take the other role's")
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
            if not (ld / "base").is_file() or not known:
                raise SB.SandboxError(
                    f"refs/lanes/{lane} exists in {root}, but this lane's own record is gone (a closed lane, or its "
                    f"state removed by hand): nothing was done. Use a new lane key, or, if that pushed work is this "
                    f"lane's, ask the owner (git -C {root} update-ref -d refs/lanes/{lane} starts it fresh)")
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
        clone = second_clone(root, ld, lane, role, sha, base, base_sha, pushed, private)
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
        (f"# The {trole} of {name}\n\nYour brief (the task Orca gave you) is your spec. You run in a sandbox: your "
         "own clone, no network to this machine or its LAN; git push and pull, gh pr, gh run view, orca-ide "
         "orchestration send|ask|check|reply, bd show|update|create and lane-heavy work through its broker.\n")
    SB.write_private(persona, text)
    rf = [x for x in (review_files or [])]
    for x in rf + ([review_write] if review_write else []):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.md", x):
            raise SB.SandboxError(f"review file {x!r}: a plain .md name in the owner's review folder")
    # review r1 m5: what a sandboxed lane does not get in phase 1, said every time
    left = [e for e in effective_extras(root)]
    say("prepare: not given to a sandboxed lane in phase 1: the project's extras (skills, MCP servers, plugins, "
        "personas)" + (f": {len(left)} of them here" if left else "") + ", and the idle and watchdog signals")
    prefix = st.get("bd_prefix") or known.get("bd_prefix") or (issue.split("-")[0] if issue and "-" in issue else None)
    lane_rec = {"lane": lane, "project": str(root), "name": name, "repo": str(root), "base": base, "branch": branch,
                "worktree": str(wt),
                "issue": issue, "gh_repo": slug, "private": private, "bd_prefix": st.get("bd_prefix") or
                (issue.split("-")[0] if issue and "-" in issue else None)}
    old = json.loads((ld / "lane.json").read_text()) if (ld / "lane.json").is_file() else {}
    if trole != "lane":  # review r1 m4: a reviewer's or check's prepare never rewrites the lane's own fields
        lane_rec = {k: v for k, v in lane_rec.items() if k not in old}
    lane_rec["worktrees"] = sorted(set(old.get("worktrees") or []) | {str(wt)})
    SB.write_private(ld / "lane.json", json.dumps({**old, **lane_rec}, indent=1) + "\n")
    t = {"nonce": nonce, "role": trole, "lane": lane, "issue": issue, "project": name, "repo": str(root),
         "base": base, "branch": branch, "worktree": str(wt), "clone": str(clone), "lane_dir": str(ld),
         "ticket_dir": str(td), "home": str(Path.home()),
         "home_files": {"gitconfig": str(gen / "gitconfig"), "opencode.json": str(gen / "opencode.json"),
                        "resolv.conf": str(gen / "resolv.conf"), "plugins": str(gen / "plugins"),
                        "skills": str(gen / "skills")},
         "tool_path": tools, "bash": find_tool(tools, "bash") or conf.get("SANDBOX_BASH") or host_bash(),
         "persona": str(persona), "gateway_key_env": key_env, "gateway": list(gateway) if gateway else None,
         "hook_port": None, "review_files": rf, "review_write": review_write if trole == "reviewer" else None,
         "sha": sha, "private": private, "inside": str(HERE / "sandbox-inside"), "lc_tree": str(HERE.parent),
         "checkout": checkout, "gh_repo": slug, "base_sha": base_sha,
         "protected": sorted({base, *default_branch(root)}), "bd_prefix": prefix, "agent_dirs":
         [p for p in (conf.get("SANDBOX_AGENT_DIRS") or "").split(":") if p], "created": time.time()}
    stale = 0  # r1 m3: a ticket prepare wrote before and no start took is never taken instead of this one
    for f in SB.tickets_dir(wt).glob("*.json") if SB.tickets_dir(wt).is_dir() else []:
        f.unlink()
        stale += 1
    if stale:
        say(f"prepare: removed {stale} unused older ticket(s) of {wt}")
    SB.write_ticket(wt, t)
    return nonce
