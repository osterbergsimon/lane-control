"""scripts/orch: the orchestrator command, against stub adapters in a temporary lane-control root and real git repos.

The project under test is a clone of a bare repo in a temp dir, so worktrees, pushes and branches are real; `gh` is a
stub on PATH that logs its calls and prints a PR URL.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORCH = ROOT / "scripts" / "orch"

GH_STUB = """#!/usr/bin/env python3
import os, sys
with open(os.environ["GH_LOG"], "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["pr", "create"]:
    print("https://example.com/acme/shop/pull/7")
elif sys.argv[1:3] == ["repo", "view"]:
    sys.exit(1)
"""


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def tree_hash(path: Path) -> str:
    """Every file under path (outside .git) with its bytes: equal before and after means nothing changed."""
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if ".git" in p.relative_to(path).parts:
            continue
        h.update(str(p.relative_to(path)).encode())
        if p.is_file() and not p.is_symlink():
            h.update(p.read_bytes())
    return h.hexdigest()


class Env:
    """A temp dir with a project (clone of a bare origin), a gh stub, and helpers to run orch."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.bin = self.d / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(0o755)
        self.gh_log = self.d / "gh.log"
        self.home = self.d / "home"
        self.home.mkdir()
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "GH_LOG": str(self.gh_log),
                    "HOME": str(self.home), "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "Owner", "GIT_AUTHOR_EMAIL": "owner@example.com",
                    "GIT_COMMITTER_NAME": "Owner", "GIT_COMMITTER_EMAIL": "owner@example.com"}

    def close(self):
        self.tmp.cleanup()

    def project(self, files=None, name="shop") -> Path:
        origin = self.d / f"{name}.git"
        git(self.d, "init", "-q", "--bare", "-b", "main", str(origin))
        seed = self.d / f"{name}-seed"
        git(self.d, "clone", "-q", str(origin), str(seed))
        for rel, text in (files or {"README.md": "# shop\n"}).items():
            (seed / rel).parent.mkdir(parents=True, exist_ok=True)
            (seed / rel).write_text(text)
        git(seed, "add", "-A")
        subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=seed, check=True, env=self.env)
        git(seed, "push", "-q", "origin", "HEAD:main")
        main = self.d / name
        git(self.d, "clone", "-q", str(origin), str(main))
        shutil.rmtree(seed)
        return main

    def run(self, *args, orch=ORCH, cwd=None, stdin=None):
        return subprocess.run([str(orch), *args], capture_output=True, text=True, env=self.env,
                              cwd=cwd or self.d, input=stdin, timeout=120)

    def gh_calls(self):
        return self.gh_log.read_text().splitlines() if self.gh_log.exists() else []

    def fake_root(self, adapters: dict) -> Path:
        """A lane-control root with the real core/orch.py and core/lcroot.py, and the given adapter sources."""
        root = self.d / "lc"
        (root / "core").mkdir(parents=True)
        (root / ".lane-control-root").write_text("")
        for f in ("orch.py", "lcroot.py"):
            shutil.copy2(ROOT / "core" / f, root / "core" / f)
        for name, src in adapters.items():
            (root / "orchestrators" / name).mkdir(parents=True)
            (root / "orchestrators" / name / "adapter.py").write_text(textwrap.dedent(src))
        return root


MARKER_ADAPTER = """
    from pathlib import Path
    NAME = "{name}"
    ACTIONS = frozenset({{"status"}})
    def detect(project: Path):
        return ["{marker}"] if (project / "{marker}").exists() else []
"""


class OrchTest(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.addCleanup(self.e.close)


class CommandTests(OrchTest):
    def test_unknown_orchestrator_exit_2(self):
        p = self.e.project()
        r = self.e.run("setup", str(p), "--orchestrator", "nosuch")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("nosuch", r.stderr)

    def test_missing_action_exit_3_no_change(self):
        p = self.e.project()
        (p / "dirty.txt").write_text("owner's unsaved work\n")
        before = tree_hash(p)
        branches = git(p, "branch", "-a").stdout
        r = self.e.run("setup", str(p), "--orchestrator", "herdr")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("herdr has no setup adapter yet", r.stderr)
        self.assertEqual(tree_hash(p), before)
        self.assertEqual(git(p, "branch", "-a").stdout, branches)
        self.assertEqual(git(p, "worktree", "list").stdout.count("\n"), 1)
        self.assertEqual(self.e.gh_calls(), [])

    def test_status_lists_all_detected(self):
        root = self.e.fake_root({"alpha": MARKER_ADAPTER.format(name="alpha", marker=".alpha"),
                                 "beta": MARKER_ADAPTER.format(name="beta", marker="beta.cfg")})
        p = self.e.project({".alpha": "x\n", "beta.cfg": "y\n"})
        r = self.e.run("status", str(p), orch=root / "core" / "orch.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("alpha: .alpha", r.stdout)
        self.assertIn("beta: beta.cfg", r.stdout)

    def test_status_with_nothing_detected(self):
        root = self.e.fake_root({"alpha": MARKER_ADAPTER.format(name="alpha", marker=".alpha")})
        p = self.e.project()
        r = self.e.run("status", str(p), orch=root / "core" / "orch.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("none detected", r.stdout)

    def test_runs_via_symlink_from_other_cwd(self):
        root = self.e.fake_root({"alpha": MARKER_ADAPTER.format(name="alpha", marker=".alpha")})
        link = self.e.d / "elsewhere" / "orch"
        link.parent.mkdir()
        link.symlink_to(root / "core" / "orch.py")
        p = self.e.project({".alpha": "x\n"})
        r = self.e.run("status", str(p), orch=link, cwd=self.e.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("alpha: .alpha", r.stdout)

    def test_the_real_entry_point_is_scripts_orch(self):
        p = self.e.project()
        r = self.e.run("status", str(p), cwd=self.e.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("none detected", r.stdout)

    def test_dry_run_is_accepted_by_every_action(self):
        p = self.e.project()
        for args in (("status", str(p)), ("setup", str(p), "--orchestrator", "herdr"),
                     ("strip", str(p), "--orchestrator", "herdr"),
                     ("switch", str(p), "--from", "herdr", "--to", "wsp")):
            r = self.e.run(*args, "--dry-run")
            self.assertNotEqual(r.returncode, 2, (args, r.stderr))

    def test_not_a_git_repo_is_a_usage_error(self):
        r = self.e.run("status", str(self.e.d / "nope"))
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_parked_orchestrators_have_no_actions(self):
        p = self.e.project()
        for name in ("rove", "wsp", "herdr"):
            for action in ("setup", "strip"):
                r = self.e.run(action, str(p), "--orchestrator", name)
                self.assertEqual(r.returncode, 3, (name, action, r.stderr))
                self.assertIn(f"{name} has no {action} adapter yet", r.stderr)


if __name__ == "__main__":
    unittest.main()
