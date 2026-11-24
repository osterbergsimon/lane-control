"""The summary PR body lists the merged away items, read from git (lc-rjt)."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
import improve  # noqa: E402

BRANCH = "away/2026-11-23"


def git(cwd, *args, inp=None):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          capture_output=True, text=True, check=True, input=inp).stdout.strip()


class Repo:
    """A bare origin with `main` and an away branch, plus a clone to work in."""

    def __init__(self, tmp: Path):
        self.origin = tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        self.clone = tmp / "clone"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.clone)], check=True, capture_output=True)
        git(self.clone, "checkout", "-q", "-B", "main")
        self.commit("first")
        git(self.clone, "push", "-q", "origin", "main")
        git(self.clone, "checkout", "-q", "-b", BRANCH)
        self.commit("away: start 2026-11-23")
        git(self.clone, "push", "-q", "origin", BRANCH)

    def commit(self, msg: str) -> None:
        f = self.clone / "f.txt"
        f.write_text(f.read_text() + msg + "\n" if f.exists() else msg + "\n")
        git(self.clone, "add", "f.txt")
        git(self.clone, "commit", "-qm", msg)

    def merge(self, subject: str, body: str = "") -> str:
        """A merge commit on the away branch, as GitHub's merge button makes it (the item branch is a side commit)."""
        base = git(self.clone, "rev-parse", "HEAD")
        git(self.clone, "checkout", "-q", "-b", "side", base)
        self.commit("work for " + subject)
        git(self.clone, "checkout", "-q", BRANCH)
        git(self.clone, "merge", "-q", "--no-ff", "-m", subject + ("\n\n" + body if body else ""), "side")
        git(self.clone, "branch", "-q", "-D", "side")
        git(self.clone, "push", "-q", "origin", BRANCH)
        return git(self.clone, "rev-parse", "--short", "HEAD")


class MergedItemsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.r = Repo(Path(self._tmp.name))

    def items(self):
        return improve.merged_items(self.r.clone, BRANCH, "main")

    def test_no_merges_gives_an_empty_list(self):
        self.assertEqual(self.items(), [])

    def test_lists_away_merges_oldest_first_with_title_pr_and_sha(self):
        a = self.r.merge("away: Add the x feature", "Pull request #7")
        b = self.r.merge("away: Fix the y bug", "Pull request #9")
        self.assertEqual(self.items(), [("Add the x feature", 7, a, False, ""), ("Fix the y bug", 9, b, False, "")])

    def test_a_merge_without_a_recorded_pr_number_has_none(self):
        a = self.r.merge("away: Old item", "Some PR title that mentions #3 in passing")
        self.assertEqual(self.items(), [("Old item", None, a, False, "Some PR title that mentions #3 in passing")])

    def test_other_merges_and_the_start_commit_are_left_out(self):
        self.r.merge("Merge pull request #5 from someone/else", "A PR title")
        self.r.merge("Merge branch 'main' into away/2026-11-23")
        a = self.r.merge("away: Real item", "Pull request #8")
        self.assertEqual(self.items(), [("Real item", 8, a, False, "")])

    def test_an_item_whose_title_starts_with_start_is_listed(self):
        a = self.r.merge("away: start the night cap earlier", "Pull request #6")
        b = self.r.merge("away: Other", "Pull request #7")
        self.assertEqual(self.items(), [("start the night cap earlier", 6, a, False, ""), ("Other", 7, b, False, "")])

    def test_an_item_reverted_by_a_later_commit_is_marked(self):
        a = self.r.merge("away: Item X", "Pull request #5")
        b = self.r.merge("away: Item Y", "Pull request #6")
        git(self.r.clone, "revert", "-m", "1", "--no-edit", b)
        git(self.r.clone, "push", "-q", "origin", BRANCH)
        self.assertEqual(self.items(), [("Item X", 5, a, False, ""), ("Item Y", 6, b, True, "")])

    def test_an_item_reverted_inside_a_later_merge_is_marked(self):
        a = self.r.merge("away: Item X", "Pull request #5")
        c = self.r.clone
        git(c, "checkout", "-q", "-b", "drop", BRANCH)
        git(c, "revert", "-m", "1", "--no-edit", a)
        git(c, "checkout", "-q", BRANCH)
        git(c, "merge", "-q", "--no-ff", "-m", "Merge branch 'drop'", "drop")
        git(c, "push", "-q", "origin", BRANCH)
        self.assertEqual(self.items(), [("Item X", 5, a, True, "")])

    def test_a_plain_commit_titled_away_is_not_a_merge(self):
        self.r.commit("away: not a merge")
        git(self.r.clone, "push", "-q", "origin", BRANCH)
        self.assertEqual(self.items(), [])

    def test_a_merge_that_is_also_on_the_integration_branch_is_left_out(self):
        a = self.r.merge("away: Item", "Pull request #4")
        git(self.r.clone, "push", "-q", "origin", f"{BRANCH}:main")  # main caught up with the branch
        self.assertEqual(self.items(), [])
        self.assertNotEqual(a, "")

    def test_a_merge_that_came_in_through_a_side_branch_is_not_listed(self):
        # first-parent only: an away: merge inside a branch that was merged into the away branch is not an item of this one
        c = self.r.clone
        git(c, "checkout", "-q", "-b", "other", BRANCH)
        self.r.commit("on other")
        git(c, "checkout", "-q", "-b", "inner", "other")
        self.r.commit("inner work")
        git(c, "checkout", "-q", "other")
        git(c, "merge", "-q", "--no-ff", "-m", "away: Hidden\n\nPull request #2", "inner")
        git(c, "checkout", "-q", BRANCH)
        git(c, "merge", "-q", "--no-ff", "-m", "Merge branch 'other'", "other")
        git(c, "push", "-q", "origin", BRANCH)
        shown = self.r.merge("away: Shown", "Pull request #3")
        self.assertEqual(self.items(), [("Shown", 3, shown, False, "")])

    def test_unreadable_branch_gives_none(self):
        self.assertIsNone(improve.merged_items(self.r.clone, "away/2099-01-01", "main"))


class PrBodyItemsTests(unittest.TestCase):
    def body(self, items):
        return improve.pr_body("shop", BRANCH, "main", "- Done: x", items)

    def test_each_item_has_title_pr_sha_and_its_revert_command(self):
        b = self.body([("Add the x feature", 7, "abc1234", False, ""), ("Old item", None, "def5678", False, "")])
        self.assertIn("Items merged", b)
        self.assertIn("- Add the x feature (PR #7, abc1234): `git revert -m 1 abc1234`", b)
        self.assertIn("- Old item (def5678): `git revert -m 1 def5678`", b)

    def test_the_list_is_in_merge_order_and_says_to_revert_latest_first(self):
        b = self.body([("First", 1, "aaaaaaa", False, ""), ("Second", 2, "bbbbbbb", False, "")])
        self.assertLess(b.index("First"), b.index("Second"))
        self.assertIn("latest first", b)

    def test_a_reverted_item_shows_no_revert_command(self):
        b = self.body([("Gone", 3, "ccccccc", True, ""), ("Kept", 4, "ddddddd", False, "")])
        self.assertIn("- Gone (PR #3, ccccccc): already reverted", b)
        self.assertNotIn("git revert -m 1 ccccccc", b)
        self.assertIn("`git revert -m 1 ddddddd`", b)

    def test_it_says_where_to_run_the_command(self):
        b = self.body([("A", 1, "aaaaaaa", False, "")])
        self.assertIn("on the away branch", b)
        self.assertIn("name it to the coordinator", b)

    def test_the_footer_says_the_list_comes_from_git(self):
        self.assertIn("branch's merges", self.body([]))
        self.assertNotIn("AWAY-LOG", self.body([]))

    def test_no_items_says_none_yet(self):
        self.assertIn("Items merged: none yet", self.body([]))

    def test_unreadable_items_say_so_and_point_to_the_log(self):
        b = self.body(None)
        self.assertIn("could not be read from git", b)
        self.assertNotIn("AWAY-LOG", b)
        self.assertNotIn("git revert", b)

    def test_no_summary_says_none_yet(self):
        b = improve.pr_body("shop", BRANCH, "main", None, [])
        self.assertIn("No summary yet", b)
        self.assertNotIn(improve.SUMMARY_START, b)

    def test_the_list_follows_the_summary(self):
        b = self.body([("A", 1, "aaaaaaa", False, "")])
        self.assertLess(b.index("Done: x"), b.index("Items merged"))


ENTRY = """## What
Adds x.

## Why
Users asked.

## Builds on
nothing

## Existing work checked
Looked in lib/ and the issues: no x.

## How to try it
Run `x --help`.

## How to drop it
Revert the merge commit.

## Tests
Suite green.
"""


class EntryTests(unittest.TestCase):
    def test_a_full_body_gives_its_entry_and_nothing_missing(self):
        entry, missing = improve.parse_entry("Intro text that is not part of the entry.\n\n" + ENTRY + "\n## Also noticed\nunrelated\n")
        self.assertEqual(missing, [])
        self.assertTrue(entry.startswith("## What\nAdds x."))
        self.assertIn("## Tests\nSuite green.", entry)
        self.assertNotIn("Intro text", entry)
        self.assertNotIn("Also noticed", entry)

    def test_missing_and_empty_sections_are_named(self):
        body = ENTRY.replace("## Why\nUsers asked.\n\n", "").replace("Suite green.", "")
        entry, missing = improve.parse_entry(body)
        self.assertEqual(missing, ["Why", "Tests"])

    def test_headings_match_loosely_on_level_case_and_a_colon(self):
        body = ENTRY.replace("## What", "### what:").replace("## How to try it", "# **How to try it**")
        self.assertEqual(improve.parse_entry(body)[1], [])

    def test_an_empty_body_misses_everything(self):
        self.assertEqual(improve.parse_entry("")[1], list(improve.ENTRY_HEADINGS))
        self.assertEqual(improve.parse_entry(None)[1], list(improve.ENTRY_HEADINGS))

    def test_a_sub_heading_stays_in_its_section(self):
        body = ENTRY.replace("Run `x --help`.", "Run `x --help`.\n\n#### Then\nlook")
        entry, missing = improve.parse_entry(body)
        self.assertEqual(missing, [])
        self.assertIn("#### Then\nlook", entry)

    def test_the_entry_comes_back_with_canonical_headings(self):
        entry, _ = improve.parse_entry(ENTRY.replace("## What", "### WHAT:"))
        self.assertTrue(entry.startswith("## What\n"))


class EntriesInTheBodyTests(unittest.TestCase):
    def test_merged_items_carry_the_entry_after_the_pr_line(self):
        with tempfile.TemporaryDirectory() as t:
            r = Repo(Path(t))
            a = r.merge("away: Add the x feature", "Pull request #7\n\n" + ENTRY)
            (title, pr, sha, reverted, entry), = improve.merged_items(r.clone, BRANCH, "main")
            self.assertEqual((title, pr, sha, reverted), ("Add the x feature", 7, a, False))
            self.assertEqual(entry, ENTRY.strip())

    def test_the_body_shows_each_entry_under_its_item(self):
        b = improve.pr_body("shop", BRANCH, "main", "- Done: x", [("A", 1, "aaaaaaa", False, ENTRY.strip())])
        self.assertIn("<details>", b)
        self.assertIn("<summary>A</summary>", b)
        self.assertIn("Adds x.", b)
        self.assertIn("#### Why", b)  # demoted: the PR page keeps its own outline
        self.assertNotIn("\n## Why", b)

    def test_a_title_is_escaped(self):
        b = improve.pr_body("shop", BRANCH, "main", "- Done: x", [("A <b>&", 1, "aaaaaaa", False, ENTRY.strip())])
        self.assertIn("<summary>A &lt;b&gt;&amp;</summary>", b)

    def test_a_long_body_is_shortened_not_failed_and_says_so(self):
        big = ENTRY.strip().replace("Adds x.", "Adds x.\n" + "line of detail\n" * 400)
        items = [(f"Item {i}", i, f"{i:07d}", False, big) for i in range(40)]
        b = improve.pr_body("shop", BRANCH, "main", "- Done: x", items)
        self.assertLessEqual(len(b), improve.BODY_LIMIT)
        self.assertLessEqual(improve.BODY_LIMIT, 65536)
        self.assertIn("shortened", b)
        for i in range(40):
            self.assertIn(f"Item {i}", b)  # every item is still listed
        self.assertIn("git revert -m 1 0000039", b)

    def test_a_huge_list_is_cut_to_the_limit_and_says_so(self):
        items = [(f"Item {i} " + "w" * 300, i, f"{i:07d}", False, "") for i in range(400)]
        b = improve.pr_body("shop", BRANCH, "main", "- Done: x", items)
        self.assertLessEqual(len(b), improve.BODY_LIMIT)
        self.assertIn("cut", b)

    def test_a_short_body_is_not_shortened(self):
        b = improve.pr_body("shop", BRANCH, "main", "- Done: x", [("A", 1, "aaaaaaa", False, ENTRY.strip())])
        self.assertNotIn("shortened", b)
        self.assertIn("Suite green.", b)


class SummaryMarkerTests(unittest.TestCase):
    def test_the_summary_is_kept_between_markers_and_read_back(self):
        b = improve.pr_body("shop", BRANCH, "main", "- Done: the x feature\n- Skipped: none", [])
        self.assertIn(improve.SUMMARY_START, b)
        self.assertEqual(improve.existing_summary(b), "- Done: the x feature\n- Skipped: none")

    def test_a_body_without_the_markers_has_no_summary(self):
        self.assertIsNone(improve.existing_summary("THEIR BODY"))
        self.assertIsNone(improve.existing_summary(None))
        self.assertIsNone(improve.existing_summary(improve.pr_body("shop", BRANCH, "main", None, [])))


if __name__ == "__main__":
    unittest.main()
