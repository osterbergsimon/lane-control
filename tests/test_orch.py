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
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORCH = ROOT / "scripts" / "orch"

GH_STUB = """#!/usr/bin/env python3
import os, sys
with open(os.environ["GH_LOG"], "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["pr", "create"] and os.environ.get("GH_FAIL_PR"):
    print("pr create refused", file=sys.stderr)
    sys.exit(1)
if sys.argv[1:3] == ["pr", "create"]:
    with open(os.environ["GH_LOG"] + ".env", "w") as f:
        for k in ("GIT_TERMINAL_PROMPT", "GIT_ASKPASS", "SSH_ASKPASS", "GCM_INTERACTIVE", "GH_PROMPT_DISABLED"):
            f.write(f"{k}={os.environ.get(k, '')}\\n")
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


def load_adapters_here() -> dict:
    """This checkout's adapters, loaded the way core/orch.py loads them."""
    sys.path.insert(0, str(ROOT / "core"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("orch_t", ROOT / "core" / "orch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load_adapters(ROOT)


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

    def run(self, *args, orch=ORCH, cwd=None, stdin=None, env=None):
        return subprocess.run([str(orch), *args], capture_output=True, text=True, env={**self.env, **(env or {})},
                              cwd=cwd or self.d, input=stdin, timeout=120)

    def gh_calls(self):
        return self.gh_log.read_text().splitlines() if self.gh_log.exists() else []

    def fake_root(self, adapters: dict) -> Path:
        """A lane-control root with the real core/orch.py, core/lcroot.py and core/agentsdoc.py, and the given adapter
        sources."""
        root = self.d / "lc"
        (root / "core").mkdir(parents=True)
        (root / ".lane-control-root").write_text("")
        for f in ("orch.py", "lcroot.py", "agentsdoc.py"):
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


def orch_heads(p) -> list:
    """The orch/ branches on origin."""
    return [l.split("refs/heads/")[1] for l in git(p, "ls-remote", "--heads", "origin").stdout.splitlines()
            if "refs/heads/orch/" in l]


def switch_steps() -> str:
    """The numbered human steps of orchestrators/switching.md, as the file has them."""
    lines = (ROOT / "orchestrators" / "switching.md").read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("1. "))
    return "\n".join(lines[start:]).strip()


class SwitchTests(OrchTest):
    def fixture(self):
        from test_orch_orca_cotal import fixture_files
        return self.e.project(fixture_files())

    def assert_untouched(self, p):
        self.assertEqual(self.e.gh_calls(), [])
        self.assertNotIn("orch/", git(p, "branch", "-a").stdout)
        self.assertNotIn("orch/", git(p, "ls-remote", "--heads", "origin").stdout)
        self.assertEqual(git(p, "worktree", "list").stdout.count("\n"), 1)

    def test_switch_refuses_when_target_lacks_setup(self):
        p = self.fixture()
        before = tree_hash(p)
        r = self.e.run("switch", str(p), "--from", "orca-cotal", "--to", "herdr", "--yes")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("herdr has no setup adapter yet", r.stderr)
        self.assertEqual(tree_hash(p), before)
        self.assert_untouched(p)

    def test_switch_refuses_when_source_lacks_strip(self):
        p = self.fixture()
        r = self.e.run("switch", str(p), "--from", "rove", "--to", "native-orca", "--yes")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("rove has no strip adapter yet", r.stderr)
        self.assert_untouched(p)

    def test_switch_to_orca_cotal_refuses_and_points_to_setup(self):
        p = self.e.project()
        r = self.e.run("switch", str(p), "--from", "native-orca", "--to", "orca-cotal", "--yes")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("setup --orchestrator orca-cotal", r.stderr)
        self.assert_untouched(p)

    def test_switch_strip_then_setup_one_pr(self):
        p = self.fixture()
        before = tree_hash(p)
        r = self.e.run("switch", str(p), "--from", "orca-cotal", "--to", "native-orca", "--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tree_hash(p), before)
        prs = [c for c in self.e.gh_calls() if c.startswith("pr create")]
        self.assertEqual(len(prs), 1, self.e.gh_calls())
        heads = git(p, "ls-remote", "--heads", "origin").stdout
        self.assertIn("refs/heads/orch/switch-orca-cotal-to-native-orca-", heads)
        self.assertEqual(heads.count("orch/"), 1)
        branch = "origin/" + orch_heads(p)[0]
        git(p, "fetch", "-q", "origin")
        agents = git(p, "show", f"{branch}:AGENTS.md").stdout
        generated = git(p, "show", f"{branch}:AGENTS.lane-control.md").stdout
        self.assertNotIn("lane-control:begin", agents + generated)
        self.assertNotIn("<!-- orchestrator: native-orca -->", agents)
        self.assertEqual(agents.count("<!-- lane-control:pointer -->"), 1)
        self.assertIn("<!-- orchestrator: native-orca -->", generated)
        self.assertNotIn(".lane-control/", git(p, "ls-tree", "-r", "--name-only", branch).stdout)
        self.assertEqual(git(p, "log", "--format=%s", f"origin/main..{branch}").stdout.count("\n"), 1)

    def test_switch_prints_human_checklist(self):
        p = self.fixture()
        r = self.e.run("switch", str(p), "--from", "orca-cotal", "--to", "native-orca", "--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.rstrip().endswith(switch_steps()), r.stdout[-600:])

    def test_switch_dry_run_commits_nothing(self):
        p = self.fixture()
        r = self.e.run("switch", str(p), "--from", "orca-cotal", "--to", "native-orca", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("+<!-- orchestrator: native-orca -->", r.stdout)
        self.assertIn("-<!-- lane-control:begin -->", r.stdout)
        self.assert_untouched(p)

    def test_switch_with_nothing_to_do_prints_no_steps(self):
        native = load_adapters_here()["native-orca"]
        p = self.e.project({"AGENTS.md": native.pointer_text(""), "AGENTS.lane-control.md": native.section_text("")})
        r = self.e.run("switch", str(p), "--from", "orca-cotal", "--to", "native-orca", "--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to switch", r.stdout)
        self.assertNotIn("human steps", r.stdout)
        self.assert_untouched(p)

    def test_git_and_gh_never_prompt(self):
        p = self.fixture()
        r = self.e.run("switch", str(p), "--from", "orca-cotal", "--to", "native-orca", "--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        env = dict(l.split("=", 1) for l in Path(str(self.e.gh_log) + ".env").read_text().splitlines())
        self.assertEqual(env, {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "false", "SSH_ASKPASS": "false",
                               "GCM_INTERACTIVE": "never", "GH_PROMPT_DISABLED": "1"})

    def test_the_same_switch_twice_makes_two_branches(self):
        p = self.fixture()
        for _ in range(2):
            r = self.e.run("switch", str(p), "--from", "orca-cotal", "--to", "native-orca", "--yes")
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(git(p, "ls-remote", "--heads", "origin").stdout.count("orch/switch-"), 2)

    def test_two_branch_names_in_the_same_second_differ(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("orch_mod", ROOT / "core" / "orch.py")
        orch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(orch)
        # the same second for both; patched for this test only (time is the one shared module: lc-wja's suite run)
        with mock.patch.object(orch.time, "strftime", lambda fmt, *t: "20261116-120000"):
            self.assertNotEqual(orch.stamp(), orch.stamp())

    def test_a_failed_worktree_leaves_no_temp_dir(self):
        p = self.fixture()
        git(p, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/nosuch")
        tmp = self.e.d / "tmp"
        tmp.mkdir()
        r = self.e.run("switch", str(p), "--from", "orca-cotal", "--to", "native-orca", "--yes", env={"TMPDIR": str(tmp)})
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertEqual(list(tmp.iterdir()), [])

    def test_a_failed_pr_keeps_the_commit_and_names_the_pushed_branch(self):
        p = self.fixture()
        r = self.e.run("switch", str(p), "--from", "orca-cotal", "--to", "native-orca", "--yes",
                       env={"GH_FAIL_PR": "1"})
        self.assertEqual(r.returncode, 4, r.stderr)
        branch = orch_heads(p)[0]
        self.assertIn(branch, r.stderr)
        self.assertIn("pushed", r.stderr)
        self.assertIn("gh pr create", r.stderr)
        self.assertIn(branch, git(p, "branch", "--format=%(refname:short)").stdout.split())
        self.assertEqual(git(p, "worktree", "list").stdout.count("\n"), 1)

    def test_a_failed_push_keeps_the_commit_on_a_local_branch(self):
        p = self.fixture()
        git(p, "remote", "set-url", "--push", "origin", str(self.e.d / "no-such-remote.git"))
        r = self.e.run("switch", str(p), "--from", "orca-cotal", "--to", "native-orca", "--yes")
        self.assertEqual(r.returncode, 4, r.stderr)
        local = [b for b in git(p, "branch", "--format=%(refname:short)").stdout.split() if b.startswith("orch/")]
        self.assertEqual(len(local), 1, git(p, "branch").stdout)
        self.assertIn(local[0], r.stderr)
        self.assertIn("not pushed", r.stderr)
        self.assertIn("orchestrator: native-orca", git(p, "show", f"{local[0]}:AGENTS.lane-control.md").stdout)
        self.assertEqual(git(p, "worktree", "list").stdout.count("\n"), 1)
        self.assertEqual(self.e.gh_calls(), [])

    def test_switch_asks_without_yes(self):
        p = self.fixture()
        r = self.e.run("switch", str(p), "--from", "orca-cotal", "--to", "native-orca", stdin="")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assert_untouched(p)


if __name__ == "__main__":
    unittest.main()
