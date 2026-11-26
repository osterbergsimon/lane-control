"""Suggest a project's tier table from an OpenAI-compatible gateway's model list (owner 2026-11-24, lc-48m).

served() is the same one request to <base>/v1/models, for the served-model check at write time (core/modelenv.py).
project-setup --suggest-tiers <provider> uses it: GET <base>/v1/models, and for LiteLLM <base>/model/info, which maps
each alias to the model behind it (absent elsewhere: the alias stands for itself). The base URL comes from a flag or
OpenCode's own config (provider.<id>.options.baseURL); the key only from an environment variable, named by a flag or
by the config's `{env:NAME}` reference. OpenCode's auth files are never read, and the key is never printed, logged,
stored or put in argv: it goes in one request header, in this process, to the gateway's own URL only (no redirect is
followed and no proxy is used, so it never reaches another host).
"""
import http.client
import json
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

DEADLINE = 20  # seconds for the whole of GET /v1/models, wall clock (the socket timeout is only per read)
INFO_DEADLINE = 8  # the same for /model/info, which is optional
CHAT_MODES = (None, "chat", "responses")  # LiteLLM's model_info.mode; None: the gateway does not say
ENV_REF = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
# Name hints, matched as whole tokens of the alias and the model behind it; the owner edits whatever this gets wrong.
SWIFT = frozenset({"mini", "nano", "flash", "haiku", "small", "lite", "tiny", "fast", "air", "instant"})
DEEP = frozenset({"opus", "pro", "large", "ultra", "max", "reasoner", "reasoning", "thinking", "o1", "o3", "r1"})
SIZE = re.compile(r"(\d+(?:\.\d+)?)b")  # a parameter count token: 8b, 70b, 1.5b


class GatewayError(Exception):
    pass


def strip_jsonc(text: str) -> str:
    """JSON from JSONC: // and /* */ comments outside strings, and trailing commas, removed."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def opencode_config_paths(env=os.environ) -> list:
    """OpenCode's own global config files, in the order it reads them (never its auth or data files)."""
    if env.get("OPENCODE_CONFIG"):
        return [Path(env["OPENCODE_CONFIG"]).expanduser()]
    home = Path(env.get("XDG_CONFIG_HOME") or Path(env.get("HOME", str(Path.home()))) / ".config") / "opencode"
    return [home / n for n in ("opencode.json", "opencode.jsonc", "config.json")]


def opencode_provider(provider: str, env=os.environ) -> dict:
    """What OpenCode's config says of this provider: base (its baseURL), key_env (the NAME of an {env:NAME} apiKey),
    literal_key (True when apiKey is written in the config itself, which is never used), models (the aliases listed
    under its models, or None when it lists none) and where (the file); None or "" for what it does not say."""
    out = {"base": None, "key_env": None, "literal_key": False, "models": None, "where": ""}
    for path in opencode_config_paths(env):
        try:
            conf = json.loads(strip_jsonc(path.read_text()))
        except FileNotFoundError:
            continue
        except (OSError, ValueError, UnicodeDecodeError) as e:
            raise GatewayError(f"cannot read OpenCode's config {path}: {e}")
        entry = ((conf.get("provider") or {}) if isinstance(conf, dict) else {}).get(provider)
        if not isinstance(entry, dict):
            continue
        opts = entry.get("options") if isinstance(entry.get("options"), dict) else {}
        key = opts.get("apiKey") if isinstance(opts.get("apiKey"), str) and opts.get("apiKey") else None
        ref = ENV_REF.fullmatch(key) if key else None
        listed = entry.get("models")
        return {"base": opts.get("baseURL") if isinstance(opts.get("baseURL"), str) else None,
                "key_env": ref.group(1) if ref else None, "literal_key": bool(key and not ref),
                "models": list(listed) if isinstance(listed, dict) and listed else None, "where": str(path)}
    return out


def root_of(base: str) -> str:
    """http://h:4000/v1/ -> http://h:4000: OpenCode's baseURL often ends in /v1; the routes below add their own."""
    base = base.strip().rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 3xx is an answer, never followed: urllib would send the Authorization header on to the new host."""

    def redirect_request(self, *args, **kwargs):
        return None


def _get(url: str, key: "str | None", deadline: float, headers: "dict | None" = None):
    """The JSON at url, within `deadline` seconds wall clock. No proxy (the environment's would get the key, in clear
    for http://) and no redirect. A reply that is not HTTP, or none in time, is a GatewayError. headers: sent instead of
    the key's own Bearer header."""
    sent = {**({"Authorization": f"Bearer {key}"} if key else {}), **(headers or {})}
    if any(not all(" " <= c <= "~" for c in v) for v in sent.values()):  # http.client's own error quotes the value
        raise GatewayError(f"GET {url}: the key holds a line break or another character a header cannot carry; "
                           "nothing was sent")
    req = urllib.request.Request(url, headers={"Accept": "application/json", **sent})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    box = {}

    def work():
        try:
            with opener.open(req, timeout=deadline) as r:  # the owner's own gateway URL
                box["data"] = json.loads(r.read().decode("utf8"))
        except BaseException as e:  # handed to the caller's thread
            box["error"] = e
    th = threading.Thread(target=work, daemon=True)  # a gateway that drips bytes never ends a read: left behind
    th.start()
    th.join(deadline)
    if th.is_alive():
        raise GatewayError(f"GET {url}: no full answer within {deadline:g} s")
    e = box.get("error")
    if isinstance(e, urllib.error.HTTPError) and 300 <= e.code < 400:
        raise GatewayError(f"GET {url}: HTTP {e.code}, a redirect to {e.headers.get('Location') or 'elsewhere'}; it is "
                           "never followed, so the key stays with the URL you gave: give the gateway's own URL")
    if isinstance(e, http.client.HTTPException):
        raise GatewayError(f"GET {url}: not an HTTP reply ({type(e).__name__}): is it https://?")
    if e is not None:
        raise e
    return box["data"]


def _list(base: str, key: "str | None", headers: "dict | None" = None) -> tuple:
    """(root, [alias], whether the list says it has more pages): one GET <base>/v1/models, which must answer."""
    root = root_of(base)
    if not re.match(r"^https?://", root):
        raise GatewayError(f"the gateway URL must start with http:// or https://, got {base!r}")
    try:
        data = _get(root + "/v1/models", key, DEADLINE, headers)
    except urllib.error.HTTPError as e:
        raise GatewayError(f"GET {root}/v1/models: HTTP {e.code}"
                           + (" (the key was refused, or none was sent)" if e.code in (401, 403) else ""))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise GatewayError(f"GET {root}/v1/models: {getattr(e, 'reason', e)}")
    rows = data.get("data") if isinstance(data, dict) else None
    aliases = [m["id"] for m in rows or [] if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"]]
    if not aliases:
        raise GatewayError(f"GET {root}/v1/models lists no models")
    return root, aliases, bool(data.get("has_more"))


def served(base: str, headers: dict) -> tuple:
    """([id], more): the ids the gateway lists at <base>/v1/models, in one request with the caller's own headers."""
    return _list(base, None, headers)[1:]


def fetch_models(base: str, key: "str | None") -> list:
    """[(alias, model behind it, mode or None)] in the gateway's order. /v1/models must answer; /model/info (LiteLLM:
    the model behind each alias and its mode) may not, and its failure is silent."""
    root, aliases, _ = _list(base, key)
    behind, modes = {}, {}
    try:
        info = _get(root + "/model/info", key, INFO_DEADLINE)
        for m in (info.get("data") if isinstance(info, dict) else None) or []:
            params = m.get("litellm_params") if isinstance(m, dict) else None
            if isinstance(m.get("model_name"), str) and isinstance(params, dict) and isinstance(params.get("model"), str):
                behind.setdefault(m["model_name"], params["model"])
                mode = (m.get("model_info") or {}).get("mode") if isinstance(m.get("model_info"), dict) else None
                if isinstance(mode, str):
                    modes.setdefault(m["model_name"], mode)
    except (GatewayError, urllib.error.URLError, OSError, ValueError, AttributeError):
        pass  # not LiteLLM, no access to it, or too slow: each alias stands for itself
    return [(a, behind.get(a, a), modes.get(a)) for a in dict.fromkeys(aliases)]


def usable(models: list, provider: str, listed: "list | None") -> tuple:
    """(kept [(alias, real)], left out [(alias, why)]): a model whose mode is not chat (an embedding, a reranker) is
    left out, and so is an alias OpenCode's config does not list, when it lists any (OpenCode starts only those)."""
    kept, out = [], []
    for alias, real, mode in models:
        if mode not in CHAT_MODES:
            words = mode.replace("_", " ")
            out.append((alias, f"{'an' if words[:1] in 'aeiou' else 'a'} {words} model"))
        elif listed is not None and alias not in listed:
            out.append((alias, f"not in OpenCode's config: provider.{provider}.models"))
        else:
            kept.append((alias, real))
    return kept, out


def kind(alias: str, real: str) -> str:
    """swift, deep or standard, from name hints in the alias and the model behind it."""
    tokens = set(re.split(r"[^a-z0-9.]+", f"{alias} {real}".lower()))
    sizes = [float(m.group(1)) for t in tokens for m in [SIZE.fullmatch(t)] if m]
    if tokens & SWIFT or (sizes and max(sizes) <= 14):
        return "swift"
    if tokens & DEEP or (sizes and max(sizes) >= 65):
        return "deep"
    return "standard"


def suggest(models: list) -> dict:
    """{tier: (alias, real) or None}: swift only for a model that looks small; standard and deep never empty."""
    by = {"swift": [], "standard": [], "deep": []}
    for alias, real in models:
        by[kind(alias, real)].append((alias, real))

    def pick(*kinds):
        for k in kinds:
            if by[k]:  # a coding model first, else the gateway's order
                return next((m for m in by[k] if "code" in f"{m[0]} {m[1]}".lower()), by[k][0])
        return None
    standard = pick("standard", "deep", "swift")
    deep = pick("deep", "standard", "swift")
    return {"swift": pick("swift"), "standard": standard, "deep": deep}


def table(provider: str, picks: dict) -> str:
    """The tier table text: harness opencode, model <provider>/<alias>; top stays none (machine coordinator only)."""
    lines = ["# Suggested by project-setup --suggest-tiers from the gateway's model list; edit what you like.",
             "# permission: inherit = the project's Claude permission mode; \"-\" = not applicable. harness none = tier skipped.",
             "tier\tharness\tmodel\tpermission"]
    for tier in ("swift", "standard", "deep"):
        m = picks.get(tier)
        lines.append(f"{tier}\topencode\t{provider}/{m[0]}\t-" if m else f"{tier}\tnone\t-\t-")
    lines.append("top\tnone\t-\t-")
    return "\n".join(lines) + "\n"
