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
        kw.setdefault("hostname", "orbit.example.lan")
        kw.setdefault("user", "alice")
        kw.setdefault("projects", ["shop", "blog"])
        kw.setdefault("own", "shop")
        kw.setdefault("env", {"LANE_PRIVATE_NAMES": str(self.file)})
        return privatenames.load(**kw)

    def test_machine_names_projects_and_the_file_are_all_in_the_list(self):
        self.file.write_text("# comment\n\n  example-host  \nBravo\n")
        n = self.load()
        kinds = {(k, v) for k, v in n.entries}
        self.assertEqual(kinds, {("hostname", "orbit.example.lan"), ("hostname", "orbit"), ("username", "alice"),
                                 ("project name", "blog"), ("listed name", "example-host"), ("listed name", "Bravo")})

    def test_the_project_being_merged_is_not_a_private_name_in_its_own_repo(self):
        self.file.write_text("")
        self.assertNotIn("shop", [v for _, v in self.load().entries])

    def test_common_machine_names_are_skipped_and_the_note_says_which_kind(self):
        self.file.write_text("ab\nreal-name\n")
        for host, user in (("localhost", "root"), ("nixos", "dev"), ("main", "app"), ("ubuntu", "abc")):
            n = self.load(hostname=host, user=user)
            self.assertEqual([k for k, _ in n.entries if k in ("hostname", "username")], [], (host, user))
            self.assertIn("skipped", n.note)
            self.assertIn("hostname", n.note.split("skipped")[1])
            self.assertIn("username", n.note.split("skipped")[1])
            self.assertIn("real-name", [v for _, v in n.entries])
        self.assertNotIn("ab", [v for _, v in n.entries])  # a 2-letter listed name matches ordinary words

    def test_a_four_letter_machine_name_that_is_not_common_is_kept(self):
        self.file.write_text("")
        n = self.load(hostname="orbit", user="mary")
        self.assertEqual({v for k, v in n.entries if k in ("hostname", "username")}, {"orbit", "mary"})
        self.assertNotIn("skipped", n.note)

    def test_the_own_project_is_dropped_from_the_names_file_too(self):
        self.file.write_text("Shop\nbravo\n")
        self.assertNotIn("shop", [v.lower() for _, v in self.load().entries])
        self.assertIn("bravo", [v for _, v in self.load().entries])

    def test_hits_say_kind_and_place_and_mask_replaces_the_name(self):
        self.file.write_text("example-host\n")
        n = self.load()
        text = "ssh example-host and alice"
        hits = n.hits(text)
        self.assertEqual([k for k, _, _ in hits], ["a listed name", "a username"])
        self.assertEqual(n.mask(text), "ssh *** and ***")

    def test_mask_hides_every_occurrence_even_inside_a_longer_word(self):
        self.file.write_text("zebrabox\n")
        n = self.load()
        self.assertEqual(n.mask("docs/zebraboxnotes.md"), "docs/***notes.md")
        self.assertEqual(n.mask("zebrabox/myzebrabox.nix"), "***/my***.nix")
        self.assertEqual(n.mask("hosts/connectToZebrabox.nix"), "hosts/connectTo***.nix")
        self.assertEqual(n.mask("a.md"), "a.md")

    def test_a_name_after_an_escape_or_a_url_escape_is_still_found(self):
        self.file.write_text("example-host\n")
        n = self.load()
        for text in ("'a\\nexample-host'", "x%2Fexample-host", "a\\texample-host", "(example-host)", "a_example-host"):
            self.assertEqual(n.find(text), "a listed name", text)
        for text in ("anexample-host", "%2example-host", "xexample-hostx"):
            self.assertIsNone(n.find(text), text)

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
        self.assertIn("1 from the private-names file", n.note)
        self.assertNotIn("alice", n.note)
        self.assertNotIn("example-host", n.note)

    def test_a_default_file_that_exists_but_cannot_be_read_is_an_error(self):
        self.file.write_bytes(b"\xff\xfe not utf-8 \xff")
        with self.assertRaises(privatenames.NamesError):
            self.load(env={}, fallback=self.file)

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
