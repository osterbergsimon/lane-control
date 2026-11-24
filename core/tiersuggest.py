"""Suggest a project's tier table from an OpenAI-compatible gateway's model list (owner 2026-11-24, lc-48m).

project-setup --suggest-tiers <provider> uses it: GET <base>/v1/models, and for LiteLLM <base>/model/info, which maps
each alias to the model behind it (absent elsewhere: the alias stands for itself). The base URL comes from a flag or
OpenCode's own config (provider.<id>.options.baseURL); the key only from an environment variable, named by a flag or
by the config's `{env:NAME}` reference. OpenCode's auth files are never read, and the key is never printed, logged,
stored or put in argv: it goes in one request header, in this process.
"""
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT = 15
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


def opencode_provider(provider: str, env=os.environ) -> "tuple[str | None, str | None, str]":
    """(base URL, name of the key's environment variable, where it came from) for this provider in OpenCode's config;
    None for what it does not say. A key written into the config itself is never used (only an {env:NAME} reference)."""
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
        base = opts.get("baseURL") if isinstance(opts.get("baseURL"), str) else None
        ref = ENV_REF.fullmatch(opts.get("apiKey") or "") if isinstance(opts.get("apiKey"), str) else None
        return base, ref.group(1) if ref else None, str(path)
    return None, None, ""


def root_of(base: str) -> str:
    """http://h:4000/v1/ -> http://h:4000: OpenCode's baseURL often ends in /v1; the routes below add their own."""
    base = base.strip().rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _get(url: str, key: "str | None"):
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               **({"Authorization": f"Bearer {key}"} if key else {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 (the owner's own gateway URL)
        return json.loads(r.read().decode("utf8"))


def fetch_models(base: str, key: "str | None") -> list:
    """[(alias, model behind it)] in the gateway's order. /v1/models must answer; /model/info (LiteLLM) may not."""
    root = root_of(base)
    if not re.match(r"^https?://", root):
        raise GatewayError(f"the gateway URL must start with http:// or https://, got {base!r}")
    try:
        data = _get(root + "/v1/models", key)
    except urllib.error.HTTPError as e:
        raise GatewayError(f"GET {root}/v1/models: HTTP {e.code}"
                           + (" (the key was refused, or none was sent)" if e.code in (401, 403) else ""))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise GatewayError(f"GET {root}/v1/models: {getattr(e, 'reason', e)}")
    rows = data.get("data") if isinstance(data, dict) else None
    aliases = [m["id"] for m in rows or [] if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"]]
    if not aliases:
        raise GatewayError(f"GET {root}/v1/models lists no models")
    behind = {}
    try:
        info = _get(root + "/model/info", key)
        for m in (info.get("data") if isinstance(info, dict) else None) or []:
            params = m.get("litellm_params") if isinstance(m, dict) else None
            if isinstance(m.get("model_name"), str) and isinstance(params, dict) and isinstance(params.get("model"), str):
                behind.setdefault(m["model_name"], params["model"])
    except (urllib.error.URLError, OSError, ValueError, AttributeError):
        pass  # not LiteLLM, or no access to it: each alias stands for itself
    return [(a, behind.get(a, a)) for a in dict.fromkeys(aliases)]


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
