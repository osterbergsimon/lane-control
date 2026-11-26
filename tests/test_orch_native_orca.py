"""The native-orca adapter of scripts/orch: one marked AGENTS.md section from its template, added once, removed exactly."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
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
GENERATED = "AGENTS.lane-control.md"


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

    def doc(self):
        spec = importlib.util.spec_from_file_location("agentsdoc_t", ROOT / "core" / "agentsdoc.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def gen(self):
        f = self.p / GENERATED
        return f.read_text() if f.exists() else None

    def test_setup_writes_the_section_to_its_own_file_and_one_pointer_line(self):
        (self.p / "AGENTS.md").write_text(RULES)
        self.assertTrue(self.a.setup(self.p, self.p))
        d = self.doc()
        self.assertEqual((self.p / "AGENTS.md").read_text(), RULES + "\n" + d.POINTER + "\n")
        self.assertEqual(self.gen(), d.HEADER + "\n" + TEMPLATE.read_text())
        once = (self.gen(), (self.p / "AGENTS.md").read_text())
        self.assertEqual(self.a.setup(self.p, self.p), [])
        self.assertEqual((self.gen(), (self.p / "AGENTS.md").read_text()), once)

    def test_setup_creates_agents_md_if_missing(self):
        changes = self.a.setup(self.p, self.p)
        self.assertTrue(changes)
        self.assertEqual((self.p / "AGENTS.md").read_text(), self.doc().POINTER + "\n")
        self.assertEqual(self.gen().count(BEGIN), 1)

    def test_setup_moves_a_section_out_of_agents_md(self):
        (self.p / "AGENTS.md").write_text(RULES + "\n" + BEGIN + "\nold text\n" + END + "\n\n## After\nkept\n")
        changes = self.a.setup(self.p, self.p)
        text = (self.p / "AGENTS.md").read_text()
        self.assertEqual(text, RULES + "\n## After\nkept\n\n" + self.doc().POINTER + "\n")
        self.assertNotIn("old text", self.gen())
        self.assertEqual(self.gen().count(BEGIN), 1)
        self.assertIn("moved", " ".join(changes))
        self.assertEqual(self.a.setup(self.p, self.p), [])

    def test_setup_refreshes_an_older_section_in_the_generated_file_in_place(self):
        (self.p / "AGENTS.md").write_text(RULES)
        self.a.setup(self.p, self.p)
        f = self.p / GENERATED
        f.write_text(f.read_text().replace("Coordinator:", "OLD WORDING") + "\n## Kept\nhand text\n")
        self.a.setup(self.p, self.p)
        self.assertNotIn("OLD WORDING", self.gen())
        self.assertEqual(self.gen().count(BEGIN), 1)
        self.assertTrue(self.gen().endswith("## Kept\nhand text\n"))

    def test_detect_by_markers_in_either_file(self):
        (self.p / "AGENTS.md").write_text(RULES)
        self.assertEqual(self.a.detect(self.p), [])
        self.a.setup(self.p, self.p)
        self.assertEqual(self.a.detect(self.p), [f"{GENERATED} native-orca section"])
        (self.p / GENERATED).unlink()
        (self.p / "AGENTS.md").write_text(RULES + "\n" + TEMPLATE.read_text())
        self.assertEqual(self.a.detect(self.p), ["AGENTS.md native-orca section"])

    def test_strip_removes_section_file_and_pointer(self):
        (self.p / "AGENTS.md").write_text(RULES)
        self.a.setup(self.p, self.p)
        self.assertTrue(self.a.strip(self.p, self.p))
        self.assertEqual((self.p / "AGENTS.md").read_text(), RULES)
        self.assertFalse((self.p / GENERATED).exists())
        self.assertEqual(self.a.strip(self.p, self.p), [])

    def test_strip_keeps_the_file_and_pointer_while_another_section_is_in_it(self):
        (self.p / "AGENTS.md").write_text(RULES)
        self.a.setup(self.p, self.p)
        other = "<!-- lane-control:begin -->\nlc\n<!-- lane-control:end -->"
        f = self.p / GENERATED
        f.write_text(f.read_text() + "\n" + other + "\n")
        self.a.strip(self.p, self.p)
        self.assertNotIn(BEGIN, f.read_text())
        self.assertIn(other, f.read_text())
        self.assertIn(self.doc().POINTER_MARK, (self.p / "AGENTS.md").read_text())

    def test_strip_removes_an_old_section_left_in_agents_md(self):
        (self.p / "AGENTS.md").write_text(RULES + "\n" + TEMPLATE.read_text())
        self.assertTrue(self.a.strip(self.p, self.p))
        self.assertEqual((self.p / "AGENTS.md").read_text(), RULES)

    def test_setup_says_when_the_section_it_replaces_differed(self):
        (self.p / "AGENTS.md").write_text(RULES + "\n" + TEMPLATE.read_text().replace("Coordinator:", "HAND EDIT"))
        changes = self.a.setup(self.p, self.p)
        self.assertIn("AGENTS.md: the native-orca section moved out differed from the template (an older version or "
                      "a hand edit); the old text stays in this change's diff and in git history", changes)
        self.assertFalse([c for c in self.a.setup(self.p, self.p) if "differed" in c])

    def test_setup_refuses_a_gitignored_generated_file(self):
        git(self.p, "init", "-q")
        (self.p / ".gitignore").write_text("AGENTS.lane-control.md\n")
        with self.assertRaises(ValueError) as cm:
            self.a.setup(self.p, self.p)
        self.assertIn("AGENTS.lane-control.md is gitignored here (.gitignore:1:AGENTS.lane-control.md)",
                      str(cm.exception))
        self.assertFalse((self.p / GENERATED).exists())

    def test_setup_takes_a_generated_file_the_project_un_ignored(self):
        git(self.p, "init", "-q")
        (self.p / ".gitignore").write_text("AGENTS.*\n!AGENTS.lane-control.md\n")
        self.assertTrue(self.a.setup(self.p, self.p))
        self.assertTrue((self.p / GENERATED).exists())

    def test_unpaired_markers_refused(self):
        for name in ("AGENTS.md", GENERATED):
            with self.subTest(name=name):
                (self.p / GENERATED).unlink(missing_ok=True)
                (self.p / "AGENTS.md").write_text(RULES)
                (self.p / name).write_text(RULES + BEGIN + "\n")
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
        self.assertIn(BEGIN, git(p, "show", f"{branch}:{GENERATED}").stdout)
        self.assertIn("lane-control:pointer", git(p, "show", f"{branch}:AGENTS.md").stdout)
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
