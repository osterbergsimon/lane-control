"""The native-orca adapter of scripts/orch: one marked AGENTS.md section from its template, added once, removed exactly."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from test_orch import ROOT, OrchTest, git

ADAPTER = ROOT / "orchestrators" / "native-orca" / "adapter.py"
TEMPLATE = ROOT / "orchestrators" / "native-orca" / "templates" / "agents-section.md"
BEGIN = "<!-- orchestrator: native-orca -->"
END = "<!-- /orchestrator: native-orca -->"
RULES = "# Example project\n\n## Checks\n- `make test`.\n"


def load():
    spec = importlib.util.spec_from_file_location("native_orca_adapter", ADAPTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TemplateTests(unittest.TestCase):
    def test_template_is_short_marked_and_generic(self):
        text = TEMPLATE.read_text()
        lines = text.splitlines()
        self.assertLess(len(lines), 40)
        self.assertEqual(lines[0], BEGIN)
        self.assertEqual(lines[-1], END)
        flat = " ".join(text.split())
        for needle in ("orchestration", "worker-start", "own worktree", "lane-heavy", "handover-latest", "check"):
            self.assertIn(needle, flat, needle)
        for private in ("/home/", "/mnt/", "cotal", "~/code/"):
            self.assertNotIn(private, text.lower(), private)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name)
        self.a = load()

    def test_actions(self):
        self.assertEqual(self.a.ACTIONS, frozenset({"status", "setup", "strip"}))

    def test_setup_appends_section_once(self):
        (self.p / "AGENTS.md").write_text(RULES)
        self.assertTrue(self.a.setup(self.p, self.p))
        once = (self.p / "AGENTS.md").read_text()
        self.assertTrue(once.startswith(RULES))
        self.assertEqual(once.count(BEGIN), 1)
        self.assertEqual(self.a.setup(self.p, self.p), [])
        self.assertEqual((self.p / "AGENTS.md").read_text(), once)

    def test_setup_creates_agents_md_if_missing(self):
        changes = self.a.setup(self.p, self.p)
        self.assertTrue(changes)
        text = (self.p / "AGENTS.md").read_text()
        self.assertEqual(text, TEMPLATE.read_text())

    def test_setup_refreshes_an_older_section_in_place(self):
        (self.p / "AGENTS.md").write_text(RULES + "\n" + BEGIN + "\nold text\n" + END + "\n\n## After\nkept\n")
        self.a.setup(self.p, self.p)
        text = (self.p / "AGENTS.md").read_text()
        self.assertNotIn("old text", text)
        self.assertEqual(text.count(BEGIN), 1)
        self.assertTrue(text.endswith("## After\nkept\n"))

    def test_detect_by_markers(self):
        (self.p / "AGENTS.md").write_text(RULES)
        self.assertEqual(self.a.detect(self.p), [])
        self.a.setup(self.p, self.p)
        self.assertEqual(self.a.detect(self.p), ["AGENTS.md native-orca section"])

    def test_strip_removes_section_only(self):
        (self.p / "AGENTS.md").write_text(RULES)
        self.a.setup(self.p, self.p)
        self.assertTrue(self.a.strip(self.p, self.p))
        self.assertEqual((self.p / "AGENTS.md").read_text(), RULES)
        self.assertEqual(self.a.strip(self.p, self.p), [])

    def test_unpaired_markers_refused(self):
        (self.p / "AGENTS.md").write_text(RULES + BEGIN + "\n")
        with self.assertRaises(ValueError):
            self.a.setup(self.p, self.p)
        with self.assertRaises(ValueError):
            self.a.strip(self.p, self.p)


class CommandTests(OrchTest):
    def test_setup_opens_one_pr_and_leaves_the_main_checkout(self):
        p = self.e.project({"AGENTS.md": RULES})
        r = self.e.run("setup", str(p), "--orchestrator", "native-orca")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((p / "AGENTS.md").read_text(), RULES)
        self.assertEqual(len([c for c in self.e.gh_calls() if c.startswith("pr create")]), 1)
        branch = next(b for b in git(p, "ls-remote", "--heads", "origin").stdout.split() if "orch/setup-native-orca" in b)
        self.assertIn(BEGIN, git(p, "show", f"{branch}:AGENTS.md").stdout)
        r = self.e.run("status", str(p))
        self.assertIn("none detected", r.stdout)  # the main checkout changes only when the PR is merged and pulled

    def test_strip_on_a_project_without_it_says_nothing_to_strip(self):
        p = self.e.project({"AGENTS.md": RULES})
        r = self.e.run("strip", str(p), "--orchestrator", "native-orca", "--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to strip", r.stdout)
        self.assertEqual(self.e.gh_calls(), [])


if __name__ == "__main__":
    unittest.main()
