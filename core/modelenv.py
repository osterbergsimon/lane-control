"""The model environment (field report 2026-11-26, items E2-E4): one source, the homes generated from it, the check
that compares every home value by value, and the served-model check run before a model id is written.

The source is <config>/lane-control/model-env.conf, plain NAME=value lines, never read by a shell. Only the names in
KEYS go in it, and never a key or a token: those stay in the owner's own environment. core/scripts/model-env writes it
and generates from it a POSIX shell file, a fish mirror and the `env` block of Claude Code's user settings. A file or a
value lane-control did not write (or that was edited since) is reported and left as it is.

check_models() is what project-setup, machine-setup and model-env call before they write a model id: one request to
<gateway>/v1/models (core/tiersuggest.py). With no gateway there is nothing to ask and nothing is said.
"""
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.parse
from pathlib import Path
from typing import NamedTuple

import tiersuggest
from harnesses import load_harness

BASE = "ANTHROPIC_BASE_URL"
CLASSIFIER = "CLAUDE_CODE_AUTO_MODE_MODEL"  # Claude Code 2.1.302 or newer
NO_THINKING = "CLAUDE_CODE_DISABLE_THINKING"
KEYS = (BASE, "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_MODEL", "ANTHROPIC_DEFAULT_FABLE_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_SMALL_FAST_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL", CLASSIFIER, NO_THINKING)
MODEL_KEYS = tuple(k for k in KEYS if "MODEL" in k)
ALIASES = {n: f"ANTHROPIC_DEFAULT_{n.upper()}_MODEL" for n in ("fable", "opus", "sonnet", "haiku")}
CHOSEN_BY_CLAUDE = ("default", "best", "opusplan")  # which id these send is Claude Code's own choice
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|HEADERS", re.I)
LOOKS_LIKE_KEY = re.compile(r"(?:^|[^A-Za-z])sk-[A-Za-z0-9_-]{16,}")
MODEL_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,199}")  # nothing a shell or JSON would need quoted
URL_VALUE = re.compile(r"https?://[A-Za-z0-9._~:/\[\]%-]{1,300}")
# Claude Code's docs (permission-modes, "When your model doesn't review"): it ignores the classifier variable for these.
IGNORED_CLASSIFIER = re.compile(r"claude-(?:opus-5|sonnet-5-5)(?!\d)")
REASONING_HINT = frozenset({"reasoner", "reasoning", "thinking", "o1", "o3", "r1"})  # whole tokens of an alias
STARTUP = (".profile", ".bash_profile", ".bash_login", ".bashrc", ".zshenv", ".zprofile", ".zshrc", ".zlogin")
MARK = "# lane-control-model-env sha256="
RUNNING_NOTE = ("a running agent keeps the environment it was born with. A change here reaches processes started after "
                "it: restart each running agent session, and whatever starts them (Orca, its server on a box) when it "
                "got the old values from a shell or the systemd user manager. Claude Code's docs say a running session "
                "reads a changed settings env block again but does not unset a removed value; do not rely on either")


class SourceError(Exception):
    """The source, or a value for it, is not acceptable. The message never quotes a value."""


# --- where things are -----------------------------------------------------------------------------------------------

def home(env=os.environ) -> Path:
    return Path(env.get("HOME") or Path.home())


def config_home(env=os.environ) -> Path:
    v = env.get("XDG_CONFIG_HOME")
    return Path(v) if v and os.path.isabs(v) else home(env) / ".config"


def source_path(env=os.environ) -> Path:
    return config_home(env) / "lane-control" / "model-env.conf"


def sh_path(env=os.environ) -> Path:
    return config_home(env) / "lane-control" / "model-env.sh"


def fish_path(env=os.environ) -> Path:
    return config_home(env) / "fish" / "conf.d" / "lane-control-models.fish"


def record_path(env=os.environ) -> Path:
    """What lane-control itself put in each Claude settings file: only those values are ever changed again."""
    return config_home(env) / "lane-control" / "model-env.written.json"


def claude_settings_path(env=os.environ) -> Path:
    v = env.get("CLAUDE_CONFIG_DIR")
    return (Path(v) if v and os.path.isabs(v) else home(env) / ".claude") / "settings.json"


# --- values ---------------------------------------------------------------------------------------------------------

def refusal_of(name: str, value: str) -> "str | None":
    """None, or why NAME=value may not go in the source. Neither a value nor an unknown name is quoted."""
    if name not in KEYS:
        if SECRET_NAME.search(name) and re.fullmatch(r"[A-Z0-9_]{1,80}", name):
            return (f"{name} is a key or token: lane-control never writes one. Set it where your shell or login starts "
                    "agents, or use Claude Code's apiKeyHelper")
        return f"not a name lane-control manages; it takes: {', '.join(KEYS)}"
    if name == BASE:
        parts = urllib.parse.urlsplit(value)
        if parts.username or parts.password or "@" in parts.netloc:
            return f"{name} has credentials in it; give the URL without them (the key stays in your environment)"
        if parts.query or parts.fragment or not URL_VALUE.fullmatch(value):
            return f"{name} must be a plain http:// or https:// URL, with no query, spaces or quotes"
        return None
    if name == NO_THINKING:
        return None if value in ("0", "1", "true", "false") else f"{name} takes 1 or 0"
    if LOOKS_LIKE_KEY.search(value):
        return f"the value of {name} looks like an API key; it was not written"
    if not MODEL_VALUE.fullmatch(value):
        return f"the value of {name} is not a model name (letters, digits and . _ - : / @ [ ] only)"
    if name == CLASSIFIER and value.removesuffix("[1m]") in (*ALIASES, *CHOSEN_BY_CLAUDE, "inherit"):
        return f"{name} takes the model's own name (Claude Code's docs ask for a full model id, not an alias)"
    return None


def classifier_warnings(value: str) -> list:
    out = []
    if IGNORED_CLASSIFIER.search(value):
        out.append(f"Claude Code ignores {CLASSIFIER}={value} and picks the classifier model itself (its docs: Claude "
                   "Opus 5, Sonnet 5.5 and Opus 5.5)")
    if REASONING_HINT & set(re.split(r"[^a-z0-9]+", value.lower())):
        out.append(f"{CLASSIFIER}={value} sounds like a reasoning model by its name; the classifier wants a "
                   "non-reasoning one")
    return out


def shown(name: str, value: str) -> str:
    """A value as it may be printed: never credentials, a query, something key-like or an unexpected blob."""
    if value == "":
        return "''"
    if LOOKS_LIKE_KEY.search(value):
        return "(looks like a key: not shown)"
    if "://" in value:
        parts = urllib.parse.urlsplit(value)
        netloc = "***@" + parts.netloc.rsplit("@", 1)[1] if "@" in parts.netloc else parts.netloc
        value = urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", "")) + ("?..." if parts.query else "")
    if not re.fullmatch(r"[A-Za-z0-9._~:/@\[\]%*?-]{1,300}", value):
        return "(not a plain value: not shown)"
    return f"'{value}'"


# --- the source -----------------------------------------------------------------------------------------------------

def parse_source(text: str, where: str) -> dict:
    out = {}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, eq, value = line.partition("=")
        if not eq:
            raise SourceError(f"{where}:{n}: not a NAME=value line")
        name, value = name.strip(), value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        why = refusal_of(name, value)
        if why:
            raise SourceError(f"{where}:{n}: {why}")
        out[name] = value
    return out


def read_source(env=os.environ) -> "dict | None":
    """The source's values, or None when there is no source yet."""
    f = source_path(env)
    try:
        text = f.read_text()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        raise SourceError(f"cannot read {f}: {e}") from None
    return parse_source(text, str(f))


def ordered(values: dict) -> dict:
    return {k: values[k] for k in KEYS if k in values}


def source_text(values: dict) -> str:
    return ("# lane-control's one source of the model environment: NAME=value, no shell syntax, never a key or token.\n"
            "# core/scripts/model-env writes the shell file, the fish mirror and Claude's settings env from it;\n"
            "# model-env --check compares every home with it.\n"
            + "".join(f"{k}={v}\n" for k, v in ordered(values).items()))


# --- what an agent would see ----------------------------------------------------------------------------------------

def load_json_object(path: Path) -> dict:
    """The JSON object in path ({} when there is none); ValueError when it is something else."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(text.strip() or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON ({e})") from None
    if not isinstance(data, dict) or not isinstance(data.get("env", {}), dict):
        raise ValueError("not an object with env an object")
    return data


def managed(mapping) -> dict:
    return {k: mapping[k] for k in KEYS if isinstance(mapping.get(k), str)}


def _outside_the_source(env) -> list:
    """What this process and the user's settings env set, the later over the earlier."""
    try:
        block = managed(load_json_object(claude_settings_path(env)).get("env", {}))
    except (OSError, ValueError, UnicodeDecodeError):
        block = {}
    return [managed(env), block]


def view(env=os.environ) -> dict:
    """The managed values a Claude session started on this machine would see: the source over the user's settings env
    over this process's environment. A project's own settings are never read here: a repo must not choose where the
    key is sent."""
    out = {}
    for layer in _outside_the_source(env):
        out.update(layer)
    try:
        out.update(read_source(env) or {})
    except SourceError:
        pass
    return {k: v for k, v in out.items() if v != ""}


def view_after(had: dict, values: dict, env=os.environ) -> dict:
    """The view once the source holds `values` instead of `had`. A value the source drops, still in the settings (we
    wrote it) or in a shell born from our files, is our own echo and does not count; any other one does."""
    out = {}
    for layer in _outside_the_source(env):
        out.update({k: v for k, v in layer.items() if k in values or had.get(k) != v})
    return {k: v for k, v in {**out, **values}.items() if v != ""}


def is_gateway(base: "str | None") -> bool:
    return bool(base) and urllib.parse.urlsplit(base).hostname != "api.anthropic.com"


# --- the served-model check -----------------------------------------------------------------------------------------

class Found(NamedTuple):
    """state: ok (the gateway lists it), unserved (it does not), unproven (only a wildcard matches it), unchecked."""
    label: str
    model: str
    state: str
    detail: str


def _claude_headers(env) -> dict:
    """The key as Claude Code itself sends it to ANTHROPIC_BASE_URL, from this process's environment only."""
    headers = {"anthropic-version": "2023-06-01"}
    if env.get("ANTHROPIC_AUTH_TOKEN"):
        headers["Authorization"] = f"Bearer {env['ANTHROPIC_AUTH_TOKEN']}"
    elif env.get("ANTHROPIC_API_KEY"):
        headers["x-api-key"] = env["ANTHROPIC_API_KEY"]
    return headers


def _target(harness: str, model: str, seen: dict, env):
    """(gateway base, headers, the id it would be sent) for one model; None when no gateway is involved; a str when
    one is and the id cannot be checked (why)."""
    if harness == "claude":
        base = seen.get(BASE)
        if not is_gateway(base):
            return None
        sent = model.removesuffix("[1m]")  # Claude Code's own suffix, not part of the id
        if sent == "inherit":
            return None
        if sent in ALIASES:
            behind = seen.get(ALIASES[sent])
            if not behind:
                return (f"`{sent}` is an alias: Claude Code picks the id it sends. Set {ALIASES[sent]} "
                        "(model-env --set) to the gateway's name, and it can be checked")
            sent = behind.removesuffix("[1m]")
        if sent in CHOSEN_BY_CLAUDE or sent in ALIASES:
            return f"which id `{sent}` sends is Claude Code's own choice"
        return base, _claude_headers(env), sent
    if harness == "opencode":
        provider, slash, alias = model.partition("/")
        if not slash:
            return None
        try:
            conf = tiersuggest.opencode_provider(provider, env)
        except tiersuggest.GatewayError as e:
            return str(e)
        if not conf["base"]:
            return None  # a hosted provider: OpenCode knows its own models
        key_env = conf["key_env"]
        if key_env and not env.get(key_env):
            return f"the gateway key's variable {key_env} is not set here ({conf['where']} names it)"
        return conf["base"], {"Authorization": f"Bearer {env[key_env]}"} if key_env else {}, alias
    return None


def check_models(items: list, seen: dict, env=os.environ, cache: "dict | None" = None) -> list:
    """[Found] for each (label, harness, model) that goes through a gateway, asking each gateway once. seen: view()."""
    cache = {} if cache is None else cache
    out = []
    for label, harness, model in items:
        target = _target(harness, model, seen, env)
        if target is None:
            continue
        if isinstance(target, str):
            out.append(Found(label, model, "unchecked", target))
            continue
        base, headers, sent = target
        parts = urllib.parse.urlsplit(base)
        if "@" in parts.netloc or parts.query:
            out.append(Found(label, sent, "unchecked", "the gateway URL carries credentials or a query: not used"))
            continue
        asked = (base, tuple(sorted(headers.items())))
        if asked not in cache:
            try:
                cache[asked] = tiersuggest.served(base, headers)
            except tiersuggest.GatewayError as e:
                cache[asked] = str(e)
        answer = cache[asked]
        where = f"{tiersuggest.root_of(base)}/v1/models"
        if isinstance(answer, str):
            out.append(Found(label, sent, "unchecked", answer))
            continue
        ids, more = answer
        wild = next((p for p in ids if "*" in p and fnmatch.fnmatchcase(sent, p)), None)
        if sent in ids:
            out.append(Found(label, sent, "ok", f"listed by {where}"))
        elif wild:
            out.append(Found(label, sent, "unproven", f"{where} lists only the pattern {wild}, which proves nothing"))
        elif more:
            out.append(Found(label, sent, "unchecked", f"{where} has more pages than the one read, without it"))
        else:
            listed = ", ".join(ids[:40]) + (f" and {len(ids) - 40} more" if len(ids) > 40 else "")
            out.append(Found(label, sent, "unserved", f"{where} does not list it (it lists: {listed})"))
    return out


WORDS = {"ok": "ok", "unserved": "not served", "unproven": "not proven", "unchecked": "could not check"}


def check_lines(found: list) -> list:
    return [f"model check: {WORDS[f.state]} {f.label}={f.model} ({f.detail})" for f in found]


def refusal(found: list) -> "str | None":
    """None, or why nothing is to be written: the gateway answered and does not list a model."""
    dead = [f for f in found if f.state == "unserved"]
    if not dead:
        return None
    return ("the gateway does not serve " + "; ".join(f"{f.model} ({f.label}): {f.detail}" for f in dead)
            + ". Use a name it serves, or pass --no-model-check to write it anyway")


# --- generated shell files ------------------------------------------------------------------------------------------

def render(kind: str, values: dict, env=os.environ) -> str:
    line = "export {}='{}'\n" if kind == "sh" else "set -gx {} '{}'\n"
    body = "".join(line.format(k, v) for k, v in ordered(values).items())
    return (f"# generated by lane-control (core/scripts/model-env) from {source_path(env)}. Do not edit: change the "
            f"source with model-env --set.\n{MARK}{hashlib.sha256(body.encode()).hexdigest()}\n{body}")


def owner_of(text: str) -> str:
    """ours (as generated), edited (ours, changed by hand) or hand (not ours)."""
    lines = text.split("\n", 2)
    if len(lines) < 3 or not lines[0].startswith("# generated by lane-control (core/scripts/model-env)") \
            or not lines[1].startswith(MARK):
        return "hand"
    return "ours" if hashlib.sha256(lines[2].encode()).hexdigest() == lines[1][len(MARK):] else "edited"


def write_atomic(path: Path, text: str, mode: "int | None" = None):
    load_harness("claude")._write_atomic(path, text)  # keeps a symlink a link and the file's mode
    if mode is not None:
        os.chmod(path, mode)


def write_shell_file(kind: str, path: Path, values: dict, env=os.environ) -> tuple:
    """(what happened, whether a home is left that does not carry the source)."""
    want = render(kind, values, env)
    try:
        have = path.read_text() if path.exists() or path.is_symlink() else None
    except (OSError, UnicodeDecodeError) as e:
        return f"left as it is (cannot be read: {e})", True
    if have == want:
        return f"exists {path}", False
    if have is not None and owner_of(have) != "ours":
        how = "it was edited by hand" if owner_of(have) == "edited" else "it is hand-written"
        differs = managed(dict(_pairs(have, kind))) != ordered(values)
        return f"left as it is {path} ({how}; to let lane-control write it again, move it away and re-run)", differs
    try:
        write_atomic(path, want, 0o644)
    except OSError as e:
        return f"not written {path} ({e})", True
    return f"{'updated' if have is not None else 'created'} {path}", False


# --- Claude's user settings -----------------------------------------------------------------------------------------

def _record(env) -> dict:
    try:
        data = json.loads(record_path(env).read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def merge_settings(values: dict, env=os.environ) -> tuple:
    """(lines to say, whether a hand-written value is left that differs) after putting the source's values in the env
    block of Claude's user settings. A value there that lane-control did not write, or that changed since it did, is
    the owner's: never changed or removed. Every other setting is kept."""
    path = claude_settings_path(env)
    try:
        data = load_json_object(path)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        return [f"claude settings: left as it is {path} ({e}); fix it by hand and re-run"], True
    block = dict(data.get("env", {}))
    record = _record(env)
    mine = dict(record.get(str(path)) or {})
    added, changed, removed, theirs = [], [], [], []
    for k in KEYS:
        want, have, wrote = values.get(k), block.get(k), mine.get(k)
        ours = wrote is not None and have == wrote
        if have == want:
            if want is None:
                mine.pop(k, None)
            elif wrote is not None:
                mine[k] = want
        elif have is None:
            block[k] = mine[k] = want
            added.append(k)
        elif ours and want is None:
            del block[k]
            mine.pop(k)
            removed.append(k)
        elif ours:
            block[k] = mine[k] = want
            changed.append(k)
        else:
            mine.pop(k, None)
            if want is not None:
                theirs.append(k)
    lines = []
    if added or changed or removed:
        new_record = {**record, str(path): mine}
        text = json.dumps({**data, "env": block}, indent=2, ensure_ascii=False) + "\n"
        existed = path.exists()
        try:
            # the record first: a value is never in place without lane-control knowing it wrote it
            write_atomic(record_path(env), json.dumps(new_record, indent=2) + "\n", 0o600)
            write_atomic(path, text)
        except OSError as e:
            return [f"claude settings: not written {path} ({e})"], True
        what = ", ".join(f"{w} {', '.join(ks)}" for w, ks in (("added", added), ("changed", changed),
                                                             ("removed", removed)) if ks)
        lines.append(f"claude settings: {'updated' if existed else 'created'} {path} ({what})")
    else:
        if mine != (record.get(str(path)) or {}):
            write_atomic(record_path(env), json.dumps({**record, str(path): mine}, indent=2) + "\n", 0o600)
        lines.append(f"claude settings: {'left as it is' if theirs else 'exists'} {path}")
    for k in theirs:
        lines.append(f"hand-written: differs {k} in {path}: {shown(k, str(block[k]))} there, "
                     f"{shown(k, values[k])} in the source; left as it is")
    return lines, bool(theirs)


# --- hand-written assignments ---------------------------------------------------------------------------------------

NAMES = "|".join(KEYS)
SH_ASSIGN = re.compile(rf"""(?:^|[\s;&|({{])({NAMES})=("(?:[^"\\]|\\.)*"|'[^']*'|[^\s;&|]*)""")
FISH_SET = re.compile(rf"(?:^|[\s;(])set\s+((?:-\S+\s+)*)({NAMES})(?:\s+([^;]*))?(?:;|$)")


def _no_comment(line: str) -> str:
    quote = ""
    for i, c in enumerate(line):
        if quote:
            quote = "" if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i]
    return line


def _literal(word: str) -> "str | None":
    """The value a shell word stands for, or None when only the shell could work it out."""
    if len(word) > 1 and word[0] == word[-1] == "'":
        return word[1:-1]
    if len(word) > 1 and word[0] == word[-1] == '"':
        word = word[1:-1]
    return None if any(c in word for c in "$`\\\"'(") else word


def assignments(text: str, fish: bool = False) -> list:
    """[(line number, name, value or None when it is computed)] for every managed name a shell file sets."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        line = _no_comment(line)
        for m in SH_ASSIGN.finditer(line):
            out.append((n, m.group(1), _literal(m.group(2))))
        if not fish:
            continue
        for m in FISH_SET.finditer(line):
            flags = m.group(1).split()
            if any(f in ("--erase", "--query") or (not f.startswith("--") and set("eq") & set(f)) for f in flags):
                continue
            words = re.findall(r""""[^"]*"|'[^']*'|\S+""", (m.group(3) or "").strip())
            out.append((n, m.group(2), "" if not words else _literal(words[0]) if len(words) == 1 else None))
    return out


def _pairs(text: str, kind: str) -> list:
    return [(name, value) for _, name, value in assignments(text, fish=kind == "fish")]


def startup_files(env=os.environ) -> list:
    """[(path, is fish)]: the owner's shell startup files and environment.d, without lane-control's own fish file."""
    conf = config_home(env)
    found = [(home(env) / n, False) for n in STARTUP] + [(conf / "fish" / "config.fish", True)]
    found += [(f, True) for f in sorted((conf / "fish" / "conf.d").glob("*.fish")) if f != fish_path(env)]
    found += [(f, False) for f in sorted((conf / "environment.d").glob("*.conf"))]
    return [(f, fish) for f, fish in found if f.is_file()]


def _read(path: Path) -> str:
    return path.read_text(errors="replace")


def sources_sh(env=os.environ) -> list:
    """The startup files that name the generated shell file (outside a comment)."""
    out = []
    for f, _ in startup_files(env):
        try:
            if any(sh_path(env).name in _no_comment(l) for l in _read(f).splitlines()):
                out.append(f)
        except OSError:
            continue
    return out


def project_roots(env=os.environ) -> list:
    """The checkouts of the projects set up on this machine (their settings stores say where they are)."""
    out = []
    for f in sorted((config_home(env) / "lane-control" / "projects").glob("*/settings.json")):
        try:
            root = json.loads(f.read_text()).get("root")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(root, str) and root:
            out.append(Path(root))
    return out


# --- the check ------------------------------------------------------------------------------------------------------

class Report:
    """One line per home and value; `bad` counts what differs, is missing or could not be compared."""

    def __init__(self, source: dict):
        self.source, self.lines, self.bad = source, [], 0

    def say(self, home_name: str, state: str, rest: str, bad: bool):
        self.lines.append(f"{home_name}: {state} {rest}")
        self.bad += bad

    def value(self, home_name: str, name: str, value, where: str = "", note: str = ""):
        want = self.source.get(name)
        tail = (f" at {where}" if where else "") + (f" ({note})" if note else "")
        if value is None:
            self.say(home_name, "not compared", f"{name}{tail}: only the shell can work its value out", True)
        elif want is None:
            self.say(home_name, "extra", f"{name}={shown(name, value)}{tail}: not in the source", True)
        elif value == want:
            self.say(home_name, "ok", f"{name}={shown(name, value)}{tail}", False)
        else:
            self.say(home_name, "differs", f"{name}{tail}: {shown(name, value)} there, {shown(name, want)} in the "
                                           "source", True)

    def whole(self, home_name: str, have: dict, where: str, required: bool, note_of=lambda k: ""):
        """A home that should carry every value of the source."""
        for k in KEYS:
            if k in have:
                self.value(home_name, k, have[k], where, note_of(k))
            elif required and k in self.source:
                self.say(home_name, "missing", f"{k}{f' in {where}' if where else ''}", True)


def _systemd_environment(env) -> "dict | None":
    if not shutil.which("systemctl", path=env.get("PATH")):
        return None
    try:
        r = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True, timeout=30,
                           env=dict(env))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return managed(dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l))


def compare(env=os.environ) -> Report:
    """Every home of the model environment against the source, value by value. Reads only; asks no gateway."""
    source = read_source(env)
    rep = Report(source or {})
    if source is None:
        rep.say("source", "missing", f"{source_path(env)}: model-env writes it", True)
    else:
        rep.say("source", "ok", f"{source_path(env)} ({len(source)} values)", False)
        if is_gateway(view(env).get(BASE)) and CLASSIFIER not in source:
            rep.say("source", "missing", f"{CLASSIFIER}: with a gateway the auto-mode classifier model is set "
                                         "explicitly (model-env asks for it)", True)

    for name, kind, path, wanted in (("shell env", "sh", sh_path(env), True),
                                     ("fish env", "fish", fish_path(env), (config_home(env) / "fish").is_dir())):
        if not path.is_file():
            if wanted and source:
                rep.say(name, "missing", f"{path}: model-env writes it", True)
            continue
        try:
            text = _read(path)
        except OSError as e:
            rep.say(name, "not compared", f"{path}: {e}", True)
            continue
        by = {"ours": "", "edited": "edited by hand", "hand": "hand-written"}[owner_of(text)]
        rep.whole(name, dict(_pairs(text, kind)), str(path), source is not None, lambda k, by=by: by)

    user = claude_settings_path(env)
    try:
        block = managed(load_json_object(user).get("env", {}))
    except (OSError, ValueError, UnicodeDecodeError) as e:
        rep.say("claude settings", "not compared", f"{user}: {e}", True)
    else:
        mine = _record(env).get(str(user)) or {}
        rep.whole("claude settings", block, str(user), source is not None,
                  lambda k: "" if mine.get(k) == block[k] else "hand-written")

    for f, fish in startup_files(env):
        try:
            found = assignments(_read(f), fish)
        except OSError as e:
            rep.say("hand-written", "not compared", f"{f}: {e}", True)
            continue
        for line, k, v in found:
            rep.value("hand-written", k, v, f"{f}:{line}")

    for root in project_roots(env):
        for f in (root / ".claude" / "settings.json", root / ".claude" / "settings.local.json"):
            if f.resolve() == user.resolve():
                continue
            try:
                block = managed(load_json_object(f).get("env", {}))
            except (OSError, ValueError, UnicodeDecodeError) as e:
                rep.say("project settings", "not compared", f"{f}: {e}", True)
                continue
            for k, v in block.items():
                rep.value("project settings", k, v, str(f), "hand-written")

    manager = _systemd_environment(env)
    if manager is None:
        rep.say("systemd user manager", "not checked", "(no systemd user manager answered)", False)
    else:
        for k, v in manager.items():
            rep.value("systemd user manager", k, v)

    rep.whole("this shell", managed(env), "", source is not None)
    return rep
