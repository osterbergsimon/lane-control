"""Lane cleanup: leave the removal of a merged lane's worktree to Orca's own workspace cleanup ("Delete merged
workspaces"), and let GitHub delete a merged branch (design: lc-a6d, docs/orca-cleanup.md).

Report only, for Orca: `orca-ide workspace cleanup preview --json` removes nothing, and lane-control never edits
Orca's settings or files; it says where the owner turns the rule on. GitHub's "Automatically delete head
branches" is set only when handed the owner's yes (`yes` is a function that asks; None means no terminal).
Every function returns `say` triples `(step, what, detail)` (project-setup's message shape); `what` in PROBLEMS
means the owner should read it. A missing or failing CLI is a `skipped` line, never an exception.
"""
import json
import shutil
import subprocess
from pathlib import Path

ORCA_STEP = "orca cleanup"
GITHUB_STEP = "github"
PROBLEMS = frozenset({"off", "check", "missing", "warning"})
TIMEOUT = 60

# The labels are Orca 1.4.268's own (Settings -> Workspace Cleanup).
WHERE_RULE = "Settings -> Workspace Cleanup -> Cleanup rules -> 'Delete merged workspaces'"
WHERE_PROJECT = ("the project's own page in Settings, its 'Workspace cleanup' section: 'Use global' follows "
                 "Settings -> Workspace Cleanup, 'Custom' sets rules for the project alone, 'Off' stops cleanup there")
NOT_CHANGED = "lane-control does not change Orca's settings"


def _run(cmd, cwd=None, timeout=TIMEOUT) -> subprocess.CompletedProcess:
    """A subprocess that never raises: a missing tool or a timeout is a non-zero result with the reason on stderr."""
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="surrogateescape",
                              stdin=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"timed out after {timeout}s")
    except OSError as e:
        return subprocess.CompletedProcess(cmd, 127, "", f"could not start: {e.strerror or e}")


def _failure(p: subprocess.CompletedProcess) -> str:
    """One line on why a call failed: the CLI's own error message, else the last line it printed."""
    try:
        err = (json.loads(p.stdout).get("error") or {}).get("message")
    except (ValueError, AttributeError):
        err = None
    lines = (p.stderr.strip() or p.stdout.strip()).splitlines()
    return str(err or (lines[-1] if lines else f"exit {p.returncode}"))[:200]


def _rows(orca: str, timeout: float):
    """(rows, None) from the preview, or (None, why it could not be read)."""
    p = _run([orca, "workspace", "cleanup", "preview", "--json"], timeout=timeout)
    if p.returncode != 0:
        return None, _failure(p)
    try:
        data = json.loads(p.stdout)
        rows = data["result"]["rows"]
        if data.get("ok") is False or not isinstance(rows, list):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return None, "unreadable output" if not p.stdout.strip() else _failure(p)
    return rows, None


def _evaluation(row) -> dict:
    ev = row.get("evaluation") if isinstance(row, dict) else None
    return ev if isinstance(ev, dict) else {}


def _status(rows) -> str:
    """One project's state from its rows: project-off, rules-off, merged (a row the merged rule matched), on (a rule
    is on, which one the preview does not say), or unknown (only a main workspace, which Orca keeps before it looks
    at any rule, so nothing shows the rules)."""
    watched = [ev for ev in map(_evaluation, rows) if ev and ev.get("cause") != "not-removable"]
    if not watched:
        return "unknown"
    if any(ev.get("reason") == "merged" for ev in watched):
        return "merged"
    causes = {ev.get("cause") for ev in watched}
    if "project-off" in causes:
        return "project-off"
    if causes == {"rules-off"}:
        return "rules-off"
    return "on"


def _off_detail(status: str, names: str) -> str:
    if status == "project-off":
        return (f"(cleanup is off for this project{names}, so Orca will not remove a merged lane's worktree. Turn it "
                f"on in Orca: {WHERE_PROJECT}; the rule to have on is 'Delete merged workspaces'. {NOT_CHANGED})")
    return (f"(no cleanup rule is on{names}, so Orca will not remove a merged lane's worktree. Turn it on in Orca: "
            f"{WHERE_RULE}; a project with its own rules: {WHERE_PROJECT}. {NOT_CHANGED})")


ON_DETAIL = ("(a cleanup rule is on, but the preview does not say which; 'Delete merged workspaces' must be one of "
             f"them, and a lane's row shows `reason: merged` once one has merged: {WHERE_RULE})")
MERGED_DETAIL = "(the merged rule is on: Orca removes a merged lane's worktree itself)"
UNKNOWN_DETAIL = ("(no lane worktree yet, and the preview shows no rule for a main workspace: run project-setup again "
                  f"once a lane exists, or look in Orca: {WHERE_RULE})")


def orca_report(orca: str, repo=None, timeout: float = TIMEOUT) -> list:
    """What Orca's cleanup rules would do for lane worktrees. With `repo` (a project's checkout) only that project;
    without it, every project Orca lists (the machine-wide part, for machine-setup)."""
    rows, why = _rows(orca, timeout)
    if rows is None:
        return [(ORCA_STEP, "skipped", f"(`orca-ide workspace cleanup preview` could not be read: {why}; the "
                                       f"merged rule is not checked. {WHERE_RULE})")]
    projects = {}  # repoId -> (name, rows)
    for r in rows:
        if isinstance(r, dict):
            projects.setdefault(r.get("repoId"), (r.get("repoName") or "?", []))[1].append(r)
    if repo is not None:
        want = Path(repo).resolve()
        mine = [rid for rid, (_, rs) in projects.items()
                if any(Path(str(r.get("path"))).resolve() == want for r in rs)]
        if not mine:
            return [(ORCA_STEP, "unknown", f"({want} is not in Orca's project list yet, so its cleanup rules cannot "
                                           f"be read; add it in Orca, then run again)")]
        status = _status(projects[mine[0]][1])
        if status in ("project-off", "rules-off"):
            return [(ORCA_STEP, "off", _off_detail(status, ""))]
        what = {"unknown": "unknown", "merged": "exists", "on": "check"}[status]
        return [(ORCA_STEP, what, {"unknown": UNKNOWN_DETAIL, "merged": MERGED_DETAIL, "on": ON_DETAIL}[status])]
    by = {}
    for name, rs in projects.values():
        by.setdefault(_status(rs), []).append(name)
    lines = []
    for status in ("rules-off", "project-off"):
        if by.get(status):
            lines.append((ORCA_STEP, "off", _off_detail(status, f" for {', '.join(sorted(by[status]))}")))
    if by.get("on"):
        lines.append((ORCA_STEP, "check", ON_DETAIL[:-1] + f"; projects: {', '.join(sorted(by['on']))})"))
    if by.get("merged"):
        lines.append((ORCA_STEP, "exists", MERGED_DETAIL))
    if by.get("unknown"):
        lines.append((ORCA_STEP, "unknown", f"({', '.join(sorted(by['unknown']))}: " + UNKNOWN_DETAIL[1:]))
    if not lines:
        lines.append((ORCA_STEP, "unknown", UNKNOWN_DETAIL))
    return lines


def github_report(repo, yes=None, gh: str = None) -> list:
    """GitHub's "Automatically delete head branches" (deleteBranchOnMerge) for the repo at `repo`: already on is
    `exists`; off is turned on unless `yes(question)` answers False (`created`; the question marks it recommended
    and defaults to yes, so only an explicit no leaves it off), and without `yes` (no terminal) it
    is `missing` with the command to run. gh missing, not signed in, offline or unclear is `skipped`."""
    gh = gh or shutil.which("gh") or "gh"
    p = _run([gh, "repo", "view", "--json", "deleteBranchOnMerge", "-q", ".deleteBranchOnMerge"], cwd=repo, timeout=30)
    seen = p.stdout.strip().lower() if p.returncode == 0 else ""
    if seen not in ("true", "false"):
        why = _failure(p) if p.returncode != 0 else f"gh answered {seen[:40]!r}"
        return [(GITHUB_STEP, "skipped", f"(delete-branch-on-merge could not be read: {why}; set it yourself in "
                                         "the repo's GitHub settings, General -> 'Automatically delete head "
                                         "branches')")]
    if seen == "true":
        return [(GITHUB_STEP, "exists", "(automatically delete head branches is on)")]
    command = "gh repo edit --delete-branch-on-merge"
    if yes is None:
        return [(GITHUB_STEP, "missing", f"(automatically delete head branches is off (recommended on: a merged "
                                         f"lane's remote branch stays otherwise); to turn it on run `{command}` in the repo, or set it in "
                                         "the repo's GitHub settings, General; needs admin rights)")]
    if not yes("Turn on GitHub's 'Automatically delete head branches' for this repo (recommended: a merged lane's "
               "branch is deleted at merge)? Enter = yes [Y/n] "):
        return [(GITHUB_STEP, "skipped", "(left off at the owner's no)")]
    e = _run([gh, "repo", "edit", "--delete-branch-on-merge"], cwd=repo, timeout=60)
    if e.returncode != 0:
        return [(GITHUB_STEP, "warning", f"(`{command}` failed: {_failure(e)}; it needs admin rights on the repo)")]
    return [(GITHUB_STEP, "created", "(automatically delete head branches is on)")]
