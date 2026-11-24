"""Scans shared by away-merge (a PR's diff, its log entry, its --item title) and lane-improve (the coordinator's summary): what
must never reach a public branch. Built-in secret patterns or gitleaks, and the public-repo names rule (core/privatenames.py)."""
import re
import subprocess
import tempfile
from pathlib import Path

import privatenames

HEADER = re.compile(r'^diff --git (?:"a/((?:[^"\\]|\\.)*)"|a/(.*?)) (?:"b/((?:[^"\\]|\\.)*)"|b/(.*))$', re.M)
# The built-in scan, for a project without a gitleaks config: what must never reach a public branch.
PRIVATE = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}"), "a GitHub token"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bAKIA[0-9A-Z]{16}\b"), "an API key or token"),
    # written in pieces so this file does not match the repo's own public-path test
    (re.compile("/ho" + "me/[a-z_][a-z0-9_-]*/|/Us" + "ers/[A-Za-z][A-Za-z0-9._-]*/|/ro" + "ot/"), "a home path"),
    (re.compile(r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"), "a private address"),
    (re.compile(r"(?i)\b(?:auth\.json|\.credentials\.json|id_(?:rsa|ed25519))\b"), "a credentials file name"),
]


class Refuse(Exception):
    pass


def gh(args: list, cwd: Path) -> str:
    try:
        r = subprocess.run(["gh", *args], cwd=cwd, capture_output=True, text=True, timeout=120)
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        raise Refuse(f"gh {args[0]} {args[1]} failed ({e})") from None
    if r.returncode:
        raise Refuse(f"gh {args[0]} {args[1]} failed ({(r.stderr.strip() or 'no output').splitlines()[-1]})")
    return r.stdout


def _unquote(path: str) -> str:
    """A git-quoted path (C escapes, octal bytes for non-ASCII) as text."""
    out, i = bytearray(), 0
    while i < len(path):
        c = path[i]
        if c == "\\" and i + 1 < len(path):
            n = path[i + 1]
            if n in "01234567" and re.fullmatch(r"[0-7]{3}", path[i + 1:i + 4]):
                out.append(int(path[i + 1:i + 4], 8))
                i += 4
                continue
            out += {"n": b"\n", "t": b"\t", '"': b'"', "\\": b"\\"}.get(n, n.encode()) if n in 'nt"\\' else n.encode()
            i += 2
            continue
        out += c.encode()
        i += 1
    return out.decode("utf-8", "replace")


def added_lines(diff: str):
    """(path, new-side line number or None, text) for every added line of a diff. Only the `---`/`+++` file headers are
    skipped (before a file's first hunk); inside a hunk a line that starts with `++` is an added line like any other."""
    path, line, in_hunk = "", None, False
    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            m = HEADER.match(raw)
            path = (_unquote(m.group(3)) if m.group(3) is not None else m.group(4)) if m else ""
            line, in_hunk = None, False
        elif raw.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", raw)
            line, in_hunk = (int(m.group(1)) if m else None), True
        elif not in_hunk and raw.startswith(("--- ", "+++ ")):
            continue
        elif raw.startswith("+"):
            yield path, line, raw[1:]
            line = line + 1 if line is not None else None
        elif raw.startswith(" ") and line is not None:
            line += 1


def scan_added(diff: str) -> "str | None":
    for _, _, text in added_lines(diff):
        for rx, what in PRIVATE:
            if rx.search(text):
                return what
    return None


NAME_HELP = "(remove the name, or ask the owner; the list is built by lane-control's core/privatenames.py)"


def new_paths(diff: str) -> set:
    """The paths a diff brings into the tree: added files and rename or copy targets. A deleted file, the old side of a
    rename and an edited file's unchanged path are not new, so cleaning up an old name is never refused for its path."""
    found = set()
    for block in re.split(r"^(?=diff --git )", diff, flags=re.M):
        m = HEADER.match(block)
        if not m:
            continue
        if re.search(r"^(?:new file mode|--- /dev/null)", block, re.M):
            found.add(_unquote(m.group(3)) if m.group(3) is not None else m.group(4))
        for t in re.findall(r"^(?:rename|copy) to (.+)$", block, re.M):
            found.add(_unquote(t[1:-1]) if t.startswith('"') else t)
    return found


def names_scan(diff: str, names: "privatenames.Names") -> "str | None":
    """None when no added line and no file path holds a private name; else where, by kind, file and line: never the name
    itself, and a path that holds one is shown with it masked."""
    found = []

    def note(text):
        if text not in found:
            found.append(text)

    for f in sorted(new_paths(diff)):
        for kind in dict.fromkeys(h[0] for h in names.hits(f)):
            note(f"{kind} in the path {names.mask(f)}")
    for path, line, text in added_lines(diff):
        for kind in dict.fromkeys(h[0] for h in names.hits(text)):
            note(f"{kind} at {names.mask(path)}" + (f":{line}" if line else ""))
    if not found:
        return None
    more = f"; and {len(found) - 5} more" if len(found) > 5 else ""
    return f"the diff adds {'; '.join(found[:5])}{more} {NAME_HELP}"


def is_public(checkout: Path) -> bool:
    """Whether the repo at `checkout` is public (internal counts as private); Refuse when gh cannot say."""
    return gh(["repo", "view", "--json", "visibility", "-q", ".visibility"], checkout).strip().upper() == "PUBLIC"


def name_check(project: str, cos: dict, diff: str) -> "tuple[str | None, str]":
    """(why not clean or None, a line for the merge output). The public-repo rule is for public repos: a private one is
    not checked, and one whose visibility cannot be read is refused. Only private projects' names count as private."""
    try:
        public = is_public(cos[project].checkout)
    except Refuse as e:
        return f"the repo's visibility cannot be read ({e}), so it is not known whether names must be checked", ""
    if not public:
        return None, f"name scan skipped: {project} is a private repo, where the public-repo rule does not apply"
    private, unread = [], 0
    for n, c in cos.items():
        if n == project:
            continue
        try:
            if not is_public(c.checkout):
                private.append(n)
        except Refuse:
            unread += 1
            private.append(n)
    try:
        names = privatenames.load(projects=private, own=project)
    except privatenames.NamesError as e:
        return str(e), ""
    line = "name scan: " + names.note + (f"; {unread} other project(s)' visibility could not be read, counted as private"
                                         if unread else "")
    return names_scan(diff, names), line


def secret_scan(checkout: Path, diff: str) -> "str | None":
    """None when clean; else why not. Names are a separate check (name_check), with or without gitleaks."""
    if (checkout / ".gitleaks.toml").is_file():
        with tempfile.TemporaryDirectory(prefix="away-merge-") as d:
            (Path(d) / "pr.diff").write_text(diff)
            try:
                r = subprocess.run(["gitleaks", "detect", "--no-git", "--no-banner", "--redact", "-c",
                                    str(checkout / ".gitleaks.toml"), "--source", d], cwd=checkout,
                                   capture_output=True, text=True, timeout=300)
            except FileNotFoundError:
                return "the project has a .gitleaks.toml but gitleaks is not installed, so the diff cannot be checked"
            except (OSError, subprocess.TimeoutExpired) as e:
                return f"gitleaks could not run ({e})"
        if r.returncode:
            return "gitleaks found a secret in the diff" if r.returncode == 1 else f"gitleaks failed (exit {r.returncode})"
        return None
    what = scan_added(diff)
    return f"the diff adds {what}" if what else None




def as_diff(texts: dict) -> str:
    """A diff that adds `texts` ({label: text}), so text that is not in a PR's diff (a log entry, a title, a summary) goes
    through the same scans as the diff does. A label has no spaces."""
    out = []
    for label, text in texts.items():
        lines = text.splitlines() or [""]
        out.append(f"diff --git a/{label} b/{label}\n--- a/{label}\n+++ b/{label}\n@@ -0,0 +1,{len(lines)} @@\n"
                   + "\n".join("+" + l for l in lines))
    return "\n".join(out) + "\n"
