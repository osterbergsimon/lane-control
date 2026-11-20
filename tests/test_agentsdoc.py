"""core/agentsdoc.py: lane-control's generated coordination lives in AGENTS.lane-control.md, with exactly one pointer
line in AGENTS.md (UP-5, lc-whv). A re-run replaces marked sections in place and never appends a second block."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load():
    spec = importlib.util.spec_from_file_location("agentsdoc_t", ROOT / "core" / "agentsdoc.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B, E = "<!-- x:begin -->", "<!-- x:end -->"
BLOCK = f"{B}\n## x\nbody\n{E}"
RULES = "# Project\n\n## Checks\n- `make test`.\n"


class PointerTests(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_the_pointer_is_one_line_naming_the_file_and_carrying_its_marker(self):
        p = self.m.POINTER
        self.assertNotIn("\n", p)
        self.assertIn("`AGENTS.lane-control.md`", p)
        self.assertIn(self.m.POINTER_MARK, p)
        self.assertEqual(self.m.GENERATED, "AGENTS.lane-control.md")

    def test_pointer_added_once_after_hand_written_text(self):
        once = self.m.with_pointer(RULES)
        self.assertEqual(once, RULES + "\n" + self.m.POINTER + "\n")
        self.assertEqual(self.m.with_pointer(once), once)

    def test_pointer_in_an_empty_file_is_the_whole_file(self):
        self.assertEqual(self.m.with_pointer(""), self.m.POINTER + "\n")

    def test_an_old_pointer_line_is_replaced_in_place(self):
        old = "a\nRead the other file. " + self.m.POINTER_MARK + "\nb\n"
        self.assertEqual(self.m.with_pointer(old), "a\n" + self.m.POINTER + "\nb\n")

    def test_two_pointers_are_refused(self):
        with self.assertRaises(ValueError):
            self.m.with_pointer(self.m.POINTER + "\n" + self.m.POINTER + "\n")

    def test_without_pointer_gives_back_the_hand_written_text(self):
        self.assertEqual(self.m.without_pointer(self.m.with_pointer(RULES)), RULES)
        self.assertEqual(self.m.without_pointer(self.m.POINTER + "\n"), "")
        self.assertEqual(self.m.without_pointer(RULES), RULES)

    def test_crlf_files_keep_their_line_endings(self):
        crlf = RULES.replace("\n", "\r\n")
        out = self.m.with_pointer(crlf)
        self.assertEqual(out, crlf + "\r\n" + self.m.POINTER + "\r\n")
        self.assertEqual(self.m.without_pointer(out), crlf)


class BlockTests(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_put_appends_once_then_replaces_in_place(self):
        once = self.m.put_block(RULES, BLOCK, B, E)
        self.assertEqual(once, RULES + "\n" + BLOCK + "\n")
        self.assertEqual(self.m.put_block(once, BLOCK, B, E), once)
        newer = BLOCK.replace("body", "newer")
        self.assertEqual(self.m.put_block(once, newer, B, E), RULES + "\n" + newer + "\n")

    def test_drop_removes_the_block_and_the_blank_line_before_it(self):
        self.assertEqual(self.m.drop_block(RULES + "\n" + BLOCK + "\n", B, E), RULES)
        self.assertEqual(self.m.drop_block("top\n\n" + BLOCK + "\n\nafter\n", B, E), "top\n\nafter\n")
        self.assertEqual(self.m.drop_block(BLOCK + "\n", B, E), "")
        self.assertEqual(self.m.drop_block(RULES, B, E), RULES)
        self.assertEqual(self.m.drop_block(BLOCK + "\n# Mine\nrule\n", B, E), "# Mine\nrule\n")
        self.assertEqual(self.m.drop_block(BLOCK + "\n\n# Mine\n", B, E), "# Mine\n")

    def test_unpaired_repeated_or_reversed_markers_are_refused(self):
        for text in (B, E, BLOCK + BLOCK, E + B):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.m.put_block(text, BLOCK, B, E)
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.m.drop_block(text, B, E)


class ReplacedTests(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_replaced_names_a_block_that_differs_from_the_new_one(self):
        edited = BLOCK.replace("body", "body\nHAND EDIT")
        self.assertEqual(self.m.replaced("x\n" + edited + "\n", BLOCK, B, E), edited)
        self.assertIsNone(self.m.replaced("x\n" + BLOCK + "\n", BLOCK, B, E))
        self.assertIsNone(self.m.replaced("x\n", BLOCK, B, E))
        self.assertIsNone(self.m.replaced(BLOCK.replace("\n", "\r\n"), BLOCK, B, E))  # line endings alone are not a change

    def test_write_atomic_replaces_whole_files_and_follows_no_temp(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "AGENTS.md"
            self.m.write_atomic(f, "one\n")
            self.m.write_atomic(f, "caf\udce9\n")
            self.assertEqual(f.read_bytes(), b"caf\xe9\n")
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["AGENTS.md"])


class IgnoredTests(unittest.TestCase):
    """R2-1: a project that un-ignored the file the way the message says is not refused again."""

    def repo(self, ignore):
        import subprocess
        import tempfile
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        subprocess.run(["git", "init", "-q", d.name], check=True)
        (Path(d.name) / ".gitignore").write_text(ignore)
        return Path(d.name)

    def test_a_plain_rule_is_named(self):
        self.assertEqual(load().ignored(self.repo("AGENTS.*\n")), ".gitignore:1:AGENTS.*")

    def test_a_negated_rule_is_not_ignored(self):
        self.assertIsNone(load().ignored(self.repo("AGENTS.*\n!AGENTS.lane-control.md\n")))

    def test_no_rule_and_no_repo_are_not_ignored(self):
        self.assertIsNone(load().ignored(self.repo("*.log\n")))
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(load().ignored(Path(d)))


class GeneratedFileTests(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_a_new_generated_file_starts_with_its_header(self):
        base = self.m.generated_base("")
        self.assertEqual(base, self.m.HEADER)
        self.assertTrue(base.startswith("# AGENTS.lane-control.md\n"))
        self.assertIn("hand-written rules belong in AGENTS.md", " ".join(base.split()))
        self.assertEqual(self.m.generated_base("kept\n"), "kept\n")

    def test_holds_nothing_is_true_for_the_header_alone(self):
        self.assertTrue(self.m.holds_nothing(""))
        self.assertTrue(self.m.holds_nothing(self.m.HEADER))
        self.assertTrue(self.m.holds_nothing(self.m.HEADER + "\n"))
        self.assertFalse(self.m.holds_nothing(self.m.put_block(self.m.HEADER, BLOCK, B, E)))


class PolicyDocTests(unittest.TestCase):
    def test_the_policy_is_written_down_where_the_orchestrators_are(self):
        text = " ".join((ROOT / "orchestrators" / "README.md").read_text().split())
        policy = text.split("## What lane-control writes into a project", 1)[1].split(" ## ", 1)[0]
        for needle in ("`AGENTS.lane-control.md`", "exactly one pointer line", "`<!-- lane-control:pointer -->`",
                       "never adds a second block", "moved out on the next run", "`core/agentsdoc.py`",
                       "the owner may change it"):
            self.assertIn(needle, policy, needle)


if __name__ == "__main__":
    unittest.main()
