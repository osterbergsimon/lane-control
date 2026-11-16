"""The orca-cotal adapter of scripts/orch: what it detects, what strip removes, and how setup hands over to project-setup.

The project is a synthetic fixture (tests/fixtures/orca-cotal-project/) carrying what project-setup adds to a repo.
"""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

from test_orch import ROOT, Env, OrchTest, git, tree_hash

FIXTURE = ROOT / "tests" / "fixtures" / "orca-cotal-project"
ADAPTER = ROOT / "orchestrators" / "orca-cotal" / "adapter.py"
PROJECT_TEXT = ("# Example project: agent rules\n\n## Checks\n- `make test` before every commit.\n\n## Heavy work\n"
                "- Builds go through `~/.agents/bin/lane-heavy make build`.\n")


def load():
    spec = importlib.util.spec_from_file_location("orca_cotal_adapter", ADAPTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fixture_files():
    return {str(p.relative_to(FIXTURE)): p.read_text() for p in FIXTURE.rglob("*") if p.is_file()}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name) / "proj"
        shutil.copytree(FIXTURE, self.p)
        self.a = load()

    def test_actions(self):
        self.assertEqual(self.a.ACTIONS, frozenset({"status", "setup", "strip"}))

    def test_detect_finds_markers(self):
        found = self.a.detect(self.p)
        self.assertIn(".lane-control/", found)
        self.assertIn("AGENTS.md lane-control section", found)
        self.assertIn(".gitignore .cotal/", found)
        self.assertNotIn(".cotal/ (local)", found)
        (self.p / ".cotal").mkdir()
        self.assertIn(".cotal/ (local)", self.a.detect(self.p))

    def test_detect_on_clean_project_is_empty(self):
        clean = Path(self.tmp.name) / "clean"
        clean.mkdir()
        (clean / "AGENTS.md").write_text("# rules\n")
        self.assertEqual(self.a.detect(clean), [])

    def test_strip_removes_markers_only(self):
        changes = self.a.strip(self.p, self.p)
        self.assertTrue(changes)
        self.assertEqual((self.p / "AGENTS.md").read_text(), PROJECT_TEXT)
        self.assertEqual((self.p / ".gitignore").read_text(), "node_modules/\n")
        self.assertFalse((self.p / ".lane-control").exists())
        for f in ("README.md", "orca.yaml"):
            self.assertEqual((self.p / f).read_text(), (FIXTURE / f).read_text(), f)
        self.assertTrue(any("project.md" in c for c in changes), changes)  # tells whoever reviews to carry its facts

    def test_strip_twice_is_noop(self):
        self.a.strip(self.p, self.p)
        before = tree_hash(self.p)
        self.assertEqual(self.a.strip(self.p, self.p), [])
        self.assertEqual(tree_hash(self.p), before)

    def test_strip_keeps_a_gitignore_that_holds_more(self):
        (self.p / ".gitignore").write_text(".cotal/\n")
        self.a.strip(self.p, self.p)
        self.assertFalse((self.p / ".gitignore").exists())  # held only our line

    def test_strip_edits_the_target_of_a_symlinked_agents_md(self):
        (self.p / "docs").mkdir()
        (self.p / "AGENTS.md").rename(self.p / "docs" / "rules.md")
        (self.p / "AGENTS.md").symlink_to("docs/rules.md")
        self.a.strip(self.p, self.p)
        self.assertTrue((self.p / "AGENTS.md").is_symlink())
        self.assertEqual((self.p / "docs" / "rules.md").read_text(), PROJECT_TEXT)

    def test_strip_refuses_unpaired_markers(self):
        (self.p / "AGENTS.md").write_text(PROJECT_TEXT + "<!-- lane-control:begin -->\nhalf\n")
        with self.assertRaises(ValueError):
            self.a.strip(self.p, self.p)


class CommandTests(OrchTest):
    def test_strip_on_clean_project_says_nothing_to_strip(self):
        p = self.e.project()
        r = self.e.run("strip", str(p), "--orchestrator", "orca-cotal", "--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to strip", r.stdout)
        self.assertEqual(self.e.gh_calls(), [])
        self.assertNotIn("orch/", git(p, "branch", "-a").stdout)
        self.assertEqual(git(p, "worktree", "list").stdout.count("\n"), 1)

    def test_main_checkout_untouched(self):
        p = self.e.project(fixture_files())
        (p / "AGENTS.md").write_text((p / "AGENTS.md").read_text() + "owner's unsaved line\n")
        (p / "scratch.txt").write_text("unsaved\n")
        before, head = tree_hash(p), git(p, "rev-parse", "HEAD").stdout
        r = self.e.run("strip", str(p), "--orchestrator", "orca-cotal", "--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tree_hash(p), before)
        self.assertEqual(git(p, "rev-parse", "HEAD").stdout, head)
        self.assertEqual(git(p, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "main")
        self.assertIn("PR: https://example.com/acme/shop/pull/7", r.stdout)
        pr = [c for c in self.e.gh_calls() if c.startswith("pr create")]
        self.assertEqual(len(pr), 1, self.e.gh_calls())
        branch = next(b for b in git(p, "ls-remote", "--heads", "origin").stdout.split() if "orch/strip-orca-cotal" in b)
        files = git(p, "ls-tree", "-r", "--name-only", branch).stdout.split()
        self.assertNotIn(".lane-control/project.md", files)
        self.assertEqual(git(p, "show", f"{branch}:AGENTS.md").stdout, PROJECT_TEXT)
        self.assertEqual(git(p, "worktree", "list").stdout.count("\n"), 1)  # its own worktree is gone again

    def test_strip_without_yes_and_without_a_terminal_commits_nothing(self):
        p = self.e.project(fixture_files())
        r = self.e.run("strip", str(p), "--orchestrator", "orca-cotal", stdin="")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("--yes", r.stderr)
        self.assertEqual(self.e.gh_calls(), [])
        self.assertNotIn("orch/", git(p, "ls-remote", "--heads", "origin").stdout)
        self.assertNotIn("orch/", git(p, "branch", "-a").stdout)

    def test_strip_dry_run_shows_and_commits_nothing(self):
        p = self.e.project(fixture_files())
        r = self.e.run("strip", str(p), "--orchestrator", "orca-cotal", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("-<!-- lane-control:begin -->", r.stdout)
        self.assertIn("dry run", r.stdout)
        self.assertEqual(self.e.gh_calls(), [])
        self.assertNotIn("orch/", git(p, "branch", "-a").stdout)

    def test_unpaired_markers_stop_before_any_commit(self):
        p = self.e.project({"AGENTS.md": PROJECT_TEXT + "<!-- lane-control:end -->\n"})
        r = self.e.run("strip", str(p), "--orchestrator", "orca-cotal", "--yes")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("unpaired", r.stderr)
        self.assertEqual(self.e.gh_calls(), [])
        self.assertNotIn("orch/", git(p, "branch", "-a").stdout)

    def test_setup_calls_project_setup_and_warns(self):
        root = self.e.fake_root({})
        (root / "orchestrators" / "orca-cotal" / "scripts").mkdir(parents=True)
        shutil.copy2(ADAPTER, root / "orchestrators" / "orca-cotal" / "adapter.py")
        stub = root / "orchestrators" / "orca-cotal" / "scripts" / "project-setup"
        stub.write_text('#!/bin/sh\necho "PROJECT-SETUP RAN $*"\nexit 0\n')
        stub.chmod(0o755)
        p = self.e.project()
        r = self.e.run("setup", str(p), "--orchestrator", "orca-cotal", orch=root / "core" / "orch.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        warn = "project-setup commits on its own; no PR is made"
        self.assertIn(warn, out)
        self.assertIn(f"PROJECT-SETUP RAN {p}", out)
        self.assertLess(out.index(warn), out.index("PROJECT-SETUP RAN"))
        self.assertEqual(self.e.gh_calls(), [])

    def test_setup_dry_run_runs_nothing(self):
        root = self.e.fake_root({})
        (root / "orchestrators" / "orca-cotal" / "scripts").mkdir(parents=True)
        shutil.copy2(ADAPTER, root / "orchestrators" / "orca-cotal" / "adapter.py")
        stub = root / "orchestrators" / "orca-cotal" / "scripts" / "project-setup"
        stub.write_text('#!/bin/sh\necho "PROJECT-SETUP RAN $*"\n')
        stub.chmod(0o755)
        p = self.e.project()
        r = self.e.run("setup", str(p), "--orchestrator", "orca-cotal", "--dry-run", orch=root / "core" / "orch.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("PROJECT-SETUP RAN", r.stdout)
        self.assertIn("would run", r.stdout)


if __name__ == "__main__":
    unittest.main()
