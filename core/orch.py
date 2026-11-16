#!/usr/bin/env python3
"""orch: set up, strip or switch a project's orchestrator (orchestrators/README.md, orchestrators/switching.md).

  orch status <project>
  orch setup  <project> --orchestrator <name> [--dry-run] [--yes]
  orch strip  <project> --orchestrator <name> [--dry-run] [--yes]
  orch switch <project> --from <a> --to <b> [--dry-run] [--yes]

<project> is the path to the project's main checkout. `status` only reads it. Every change is made on a new branch
in a new git worktree cut from the remote default branch (the main checkout and its local changes are never
touched), committed with the project's git identity and opened as one PR with `gh`; nothing is merged. `strip` and
`switch` show the change and ask before they commit (`--yes` skips the question; without a terminal it is
required). `--dry-run` shows the change and commits nothing. The only worktree the command removes is the one it
made in the same run, when there is nothing to commit, on a dry run or a no. It never stops or starts agents,
stashes, switches branches or force-pushes.

Each orchestrator's adapter (orchestrators/<name>/adapter.py) declares its ACTIONS; an action it lacks fails before
any change. An adapter may set RUNS_IN_PLACE for an action that runs on the project as it is (it then does its own
work and says so first).

Exit: 0 done or nothing to do; 2 usage; 3 action not supported; 4 git or gh failed.
"""
import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lcroot  # noqa: E402

ROOT = lcroot.find_root(Path(__file__))
ACTIONS = ("status", "setup", "strip")


class Usage(Exception):
    """Exit 2."""


class Unsupported(Exception):
    """Exit 3."""


class GitFail(Exception):
    """Exit 4."""


def load_adapters(root: Path) -> dict:
    """{name: module} for every orchestrators/<name>/adapter.py under root."""
    out = {}
    for f in sorted((root / "orchestrators").glob("*/adapter.py")):
        name = f.parent.name
        spec = importlib.util.spec_from_file_location(f"orch_adapter_{name.replace('-', '_')}", f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out[name] = mod
    return out


def adapter_for(adapters: dict, name: str, action: str):
    if name not in adapters:
        raise Usage(f"unknown orchestrator {name!r} (known: {', '.join(sorted(adapters))})")
    mod = adapters[name]
    if action not in getattr(mod, "ACTIONS", frozenset()):
        raise Unsupported(f"{name} has no {action} adapter yet")
    return mod


def run(cmd: list, cwd=None, check=True) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"}
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise GitFail(f"{' '.join(cmd)} failed: {(r.stderr or r.stdout).strip()}")
    return r


def check_project(project: Path) -> Path:
    project = project.expanduser().resolve()
    if not project.is_dir() or run(["git", "-C", str(project), "rev-parse", "--git-dir"], check=False).returncode:
        raise Usage(f"{project} is not a git checkout")
    return project


def default_branch(project: Path) -> str:
    r = run(["git", "-C", str(project), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], check=False)
    if r.returncode == 0 and r.stdout.strip().startswith("origin/"):
        return r.stdout.strip()[len("origin/"):]
    r = run(["gh", "repo", "view", "--json", "defaultBranchRef", "-q", ".defaultBranchRef.name"], cwd=project,
            check=False)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    raise GitFail(f"cannot tell the default branch of {project}: set it with `git remote set-head origin --auto`")


def make_worktree(project: Path, branch: str) -> Path:
    """A new worktree on a new branch from origin/<default>, outside the project; the main checkout is not touched."""
    run(["git", "-C", str(project), "fetch", "-q", "origin"])
    base = default_branch(project)
    work = Path(tempfile.mkdtemp(prefix="orch-")) / project.name
    run(["git", "-C", str(project), "worktree", "add", "-q", "-b", branch, str(work), f"origin/{base}"])
    return work


def drop_worktree(project: Path, work: Path, branch: str):
    """Undo make_worktree of this run: its worktree and its branch, which hold nothing committed."""
    run(["git", "-C", str(project), "worktree", "remove", "--force", str(work)], check=False)
    run(["git", "-C", str(project), "branch", "-D", branch], check=False)
    try:
        work.parent.rmdir()
    except OSError:
        pass


def open_pr(work: Path, title: str, body: str) -> str:
    branch = run(["git", "-C", str(work), "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    run(["git", "-C", str(work), "add", "-A"])
    run(["git", "-C", str(work), "commit", "-q", "-m", title])
    run(["git", "-C", str(work), "push", "-q", "-u", "origin", branch])
    return run(["gh", "pr", "create", "--head", branch, "--title", title, "--body", body], cwd=work).stdout.strip()


def confirm(question: str, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        raise Usage("this changes the project: run it in a terminal to answer, or pass --yes")
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def show(changes: list, work: Path):
    for c in changes:
        print(f"  {c}")
    run(["git", "-C", str(work), "add", "-A", "-N"], check=False)  # new files show in the diff too
    diff = run(["git", "-C", str(work), "diff", "HEAD"], check=False).stdout
    if diff:
        print(diff, end="" if diff.endswith("\n") else "\n")


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def cmd_status(adapters: dict, project: Path) -> int:
    found = 0
    for name, mod in adapters.items():
        if "status" not in getattr(mod, "ACTIONS", frozenset()):
            continue
        markers = mod.detect(project)
        if markers:
            found += 1
            print(f"{name}: {', '.join(markers)}")
    if not found:
        print("none detected")
    return 0


def change_in_worktree(project: Path, branch: str, steps: list, title: str, body: str, dry: bool, ask: bool,
                       yes: bool, nothing: str) -> int:
    """Run each step (a function of (project, work) returning change lines) in one new worktree, then one PR."""
    work = make_worktree(project, branch)
    keep = False
    try:
        changes = []
        for step in steps:
            changes += step(project, work)
        if not changes:
            print(nothing)
            return 0
        print(f"changes for {project.name} (branch {branch}):")
        show(changes, work)
        if dry:
            print("dry run: nothing committed")
            return 0
        if ask and not confirm("commit these and open a PR?", yes):
            print("nothing committed")
            return 0
        url = open_pr(work, title, body + "\n\n" + "\n".join(f"- {c}" for c in changes))
        keep = True
        print(f"PR: {url}")
        return 0
    finally:
        if keep:
            run(["git", "-C", str(project), "worktree", "remove", "--force", str(work)], check=False)
            try:
                work.parent.rmdir()
            except OSError:
                pass
        else:
            drop_worktree(project, work, branch)


def cmd_one(adapters: dict, action: str, project: Path, name: str, dry: bool, yes: bool) -> int:
    mod = adapter_for(adapters, name, action)
    if action in getattr(mod, "RUNS_IN_PLACE", frozenset()):
        return getattr(mod, action)(project, None, dry=dry)
    verb = {"setup": "set up", "strip": "strip"}[action]
    return change_in_worktree(
        project, f"orch/{action}-{name}-{stamp()}", [getattr(mod, action)],
        f"orch: {action} {name}", f"`scripts/orch {action} --orchestrator {name}` (lane-control).",
        dry, ask=(action == "strip"), yes=yes, nothing=f"nothing to {verb}")


def parse(argv: list):
    ap = argparse.ArgumentParser(prog="orch", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="action", required=True)
    for a in ("status", "setup", "strip", "switch"):
        p = sub.add_parser(a)
        p.add_argument("project", type=Path)
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--yes", action="store_true")
        if a in ("setup", "strip"):
            p.add_argument("--orchestrator", required=True)
        if a == "switch":
            p.add_argument("--from", dest="src", required=True)
            p.add_argument("--to", dest="dst", required=True)
    return ap.parse_args(argv)


def main(argv: list) -> int:
    a = parse(argv)
    adapters = load_adapters(ROOT)
    try:
        if a.action != "status":
            names = [a.orchestrator] if a.action != "switch" else [a.src, a.dst]
            for n in names:
                if n not in adapters:
                    raise Usage(f"unknown orchestrator {n!r} (known: {', '.join(sorted(adapters))})")
        project = check_project(a.project)
        if a.action == "status":
            return cmd_status(adapters, project)
        if a.action == "switch":
            return cmd_switch(adapters, project, a.src, a.dst, a.dry_run, a.yes)
        return cmd_one(adapters, a.action, project, a.orchestrator, a.dry_run, a.yes)
    except Usage as e:
        print(f"orch: {e}", file=sys.stderr)
        return 2
    except Unsupported as e:
        print(f"orch: {e}", file=sys.stderr)
        return 3
    except GitFail as e:
        print(f"orch: {e}", file=sys.stderr)
        return 4


def cmd_switch(adapters: dict, project: Path, src: str, dst: str, dry: bool, yes: bool) -> int:
    raise Unsupported("switch is not written yet")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
