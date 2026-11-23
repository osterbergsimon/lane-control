"""core/privatenames.py: the names the public-repo rule keeps out of a public branch (hostnames, usernames, project
names), read from the machine, machine.conf and a file outside the repo. Made-up names only."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import privatenames  # noqa: E402


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.file = self.tmp / "names.txt"

    def load(self, **kw):
        kw.setdefault("hostname", "box.example.lan")
        kw.setdefault("user", "alice")
        kw.setdefault("projects", ["shop", "blog"])
        kw.setdefault("own", "shop")
        kw.setdefault("env", {"LANE_PRIVATE_NAMES": str(self.file)})
        return privatenames.load(**kw)

    def test_machine_names_projects_and_the_file_are_all_in_the_list(self):
        self.file.write_text("# comment\n\n  example-host  \nBravo\n")
        n = self.load()
        kinds = {(k, v) for k, v in n.entries}
        self.assertEqual(kinds, {("hostname", "box.example.lan"), ("hostname", "box"), ("username", "alice"),
                                 ("project name", "blog"), ("listed name", "example-host"), ("listed name", "Bravo")})

    def test_the_project_being_merged_is_not_a_private_name_in_its_own_repo(self):
        self.file.write_text("")
        self.assertNotIn("shop", [v for _, v in self.load().entries])

    def test_generic_and_too_short_names_are_dropped_and_counted(self):
        self.file.write_text("ab\nlocalhost\nreal-name\n")
        n = self.load(hostname="localhost", user="root")  # the file still holds real-name
        self.assertEqual([v for _, v in n.entries if v in ("ab", "localhost", "root")], [])
        self.assertIn("real-name", [v for _, v in n.entries])
        self.assertIn("ignored", n.note)

    def test_find_matches_whole_names_ignoring_case_and_never_returns_the_value(self):
        self.file.write_text("example-host\n")
        n = self.load()
        self.assertEqual(n.find("ssh EXAMPLE-HOST now"), "a listed name")
        self.assertEqual(n.find("see blog/x"), "a project name")
        self.assertEqual(n.find("login: alice"), "a username")
        self.assertIsNone(n.find("example-hosting, alices, bloggers"))
        self.assertIsNone(n.find("nothing here"))

    def test_a_missing_default_file_is_said_in_the_note(self):
        n = self.load(env={}, fallback=self.tmp / "none.txt")
        self.assertIn("no private-names file", n.note)
        self.assertIn("LANE_PRIVATE_NAMES", n.note)

    def test_the_note_counts_what_was_checked(self):
        self.file.write_text("example-host\n")
        n = self.load()
        self.assertIn("1 from", n.note)
        self.assertNotIn("alice", n.note)
        self.assertNotIn("example-host", n.note)

    def test_a_file_named_by_the_env_var_that_is_missing_or_unreadable_is_an_error(self):
        with self.assertRaises(privatenames.NamesError):
            self.load()  # self.file does not exist
        self.file.mkdir()  # a folder is not a file
        with self.assertRaises(privatenames.NamesError):
            self.load()

    def test_nothing_to_check_is_an_error_not_a_pass(self):
        with self.assertRaises(privatenames.NamesError) as c:
            privatenames.load(hostname="localhost", user="root", projects=[], own="shop", env={},
                              fallback=self.tmp / "none.txt")
        self.assertIn("no private names", str(c.exception))


if __name__ == "__main__":
    unittest.main()
