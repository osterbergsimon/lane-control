"""The persona anchor: a few lines in the CLAUDE.local.md of a coordinator's folder that tell a session to read its persona
(lc-l78.2). The persona hook (harnesses/persona-hook.sh) says nothing without LANE_ROLE and LANE_PERSONA, so a session
started by hand, or one older than the hook, came up as a general assistant.

machine-setup writes it to the machine coordinator's folder, project-setup to the project's main checkout, both as
CLAUDE.local.md: Claude's file for one checkout, which git must not see (.git/info/exclude; the machine folder may be
the owner's own repo). Every Claude session started there reads it, so the note also says who is not the coordinator. It sits between two marker lines; every other line of the file
is the owner's and is kept. The persona is named, never imported: an import would load it into all of those sessions.
"""
import os
import subprocess
import tempfile
from pathlib import Path

FILE = "CLAUDE.local.md"
BEGIN = "<!-- lane-control:persona-anchor begin"
END = "<!-- lane-control:persona-anchor end -->"
HEAD = f"{BEGIN}: written by lane-control setup, which rewrites the lines up to the end mark -->"


class AnchorError(Exception):
    """The file is left as it is: a link, not text, or an anchor with one of its two marks gone."""


def _block(title: str, folder: Path, persona: Path, others: str, imports: tuple = ()) -> str:
    lines = [HEAD,
             f"## Coordinator of this folder: `{title}`",
             f"If you are the session the owner talks to as `{title}`, working in `{folder}`, read your persona, "
             f"`{persona}`, in full before anything else, and follow it.",
             f"{others} is not the coordinator: ignore this note."]
    if imports:  # outside backticks, or Claude would not follow them
        lines += ["This file would stop Claude reading the files below by itself, so they are imported here:",
                  *(f"@{i}" for i in imports)]
    return "\n".join((*lines, END)) + "\n"


def machine_block(folder: Path, persona: Path, title: str, imports: tuple = ()) -> str:
    return _block(title, folder, persona,
                  "A session given a job of its own (an Orca automation: night manager, lane sweep or machine check; a "
                  "worker with a Task; a sub-agent; a headless run), or one working in another folder,", imports)


def project_block(folder: Path, persona: Path, title: str, imports: tuple = ()) -> str:
    return _block(title, folder, persona,
                  "A session given a job of its own (a worker with a Task, a reviewer, a sub-agent, a headless run), or "
                  "one working in another folder,", imports)


def imports_for(folder: Path) -> tuple:
    """Claude (2.1.277 or later) reads a folder's AGENTS.md by itself only while no CLAUDE.md or CLAUDE.local.md is
    there. A folder with an AGENTS.md and no CLAUDE.md of its own would lose it to the anchor: import it."""
    if (folder / "AGENTS.md").is_file() and not any((folder / p).exists() for p in ("CLAUDE.md", ".claude/CLAUDE.md")):
        return ("AGENTS.md",)
    return ()


def hide(path: Path) -> str:
    """Keep git from seeing `path` when its folder is in a work tree: unless git already ignores it, it is listed in
    the repo's .git/info/exclude. Returns the ignore rule's file ('' outside git). AnchorError when it is committed, or
    git would still show it (a .gitignore rule that un-ignores it wins over info/exclude); nothing is left changed."""
    folder = path.parent

    def git(*args):
        try:
            return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            return subprocess.CompletedProcess(args, 128, "", str(e))

    def ignored_by():
        r = git("check-ignore", "-v", "--non-matching", "--", path.name)
        source, _, rest = r.stdout.partition(":")
        pattern = rest.partition(":")[2].split("\t")[0]
        return source if r.returncode == 0 and not pattern.startswith("!") else ""

    where = git("rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir")
    if where.returncode != 0:
        if any((p / ".git").exists() for p in (folder, *folder.parents)):
            raise AnchorError(f"{folder} is in a git repo that git cannot read ({where.stderr.strip()[:200]}), so the "
                              "anchor could show up there")
        return ""
    if git("ls-files", "--error-unmatch", "--", path.name).returncode == 0:
        raise AnchorError(f"{path} is committed in this repo, so it is not written; take it out of git to have the anchor")
    if ignored_by():
        return ignored_by()
    top, common = where.stdout.splitlines()[:2]
    rel = (folder.resolve() / path.name).relative_to(Path(top).resolve()).as_posix()
    line = "/" + "".join("\\" + c if c in "*?[\\" else c for c in rel) + "\n"
    exclude = Path(common) / "info" / "exclude"
    before = exclude.read_bytes() if exclude.exists() else None
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = before or b""
    exclude.write_bytes(text + (b"" if not text or text.endswith(b"\n") else b"\n")
                        + b"# lane-control: the coordinator's persona anchor\n" + line.encode())
    if not ignored_by():
        if before is None:
            exclude.unlink()
        else:
            exclude.write_bytes(before)
        raise AnchorError(f"git would still show {path}: a .gitignore rule un-ignores it")
    return str(exclude)


def _read(path: Path) -> "str | None":
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        raise AnchorError(f"{path}: cannot be read as text ({e})") from None


def _span(path: Path, text: str) -> "tuple | None":
    """(start, end) of the anchor in the text, or None when it has none."""
    start, end = text.find(BEGIN), text.find(END)
    if start < 0 and end < 0:
        return None
    if start < 0 or end < start or text.count(BEGIN) > 1 or text.count(END) > 1:
        raise AnchorError(f"{path}: the anchor's two marks are not one pair; take what is left of it out by hand")
    end += len(END)
    return start, end + (1 if text[end:end + 1] == "\n" else 0)


def check(path: Path, block: str) -> str:
    """'ok', 'missing' (no file or no anchor in it) or 'stale' (an anchor that says something else). Reads only."""
    text = _read(path)
    span = None if text is None else _span(path, text)
    if span is None:
        return "missing"
    return "ok" if text[span[0]:span[1]] == block else "stale"


def apply(path: Path, block: str) -> str:
    """Write the anchor: replaced where it is, else put first in the file. 'created', 'updated' or 'exists'."""
    if path.is_symlink():
        raise AnchorError(f"{path} is a link, and what it points at may be shared: add the anchor there yourself, or "
                          "put a file of its own here")
    text = _read(path)
    if text is None:
        new = block
    else:
        span = _span(path, text)
        new = block + "\n" + text if span is None else text[:span[0]] + block + text[span[1]:]
        if new == text:
            return "exists"
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if text is not None else 0o644
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(new.encode("utf-8"))
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return "created" if text is None else "updated"
