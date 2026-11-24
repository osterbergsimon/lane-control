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
        self.assertEqual(self.items(), [("Add the x feature", 7, a), ("Fix the y bug", 9, b)])

    def test_a_merge_without_a_recorded_pr_number_has_none(self):
        a = self.r.merge("away: Old item", "Some PR title that mentions #3 in passing")
        self.assertEqual(self.items(), [("Old item", None, a)])

    def test_other_merges_and_the_start_commit_are_left_out(self):
        self.r.merge("Merge pull request #5 from someone/else", "A PR title")
        self.r.merge("Merge branch 'main' into away/2026-11-23")
        a = self.r.merge("away: Real item", "Pull request #8")
        self.assertEqual(self.items(), [("Real item", 8, a)])

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
        self.assertEqual(self.items(), [("Shown", 3, shown)])

    def test_unreadable_branch_gives_none(self):
        self.assertIsNone(improve.merged_items(self.r.clone, "away/2099-01-01", "main"))


class PrBodyItemsTests(unittest.TestCase):
    def body(self, items):
        return improve.pr_body("shop", BRANCH, "main", "## Summary\n- Done: x\n\n## Items\n", items)

    def test_each_item_has_title_pr_sha_and_its_revert_command(self):
        b = self.body([("Add the x feature", 7, "abc1234"), ("Old item", None, "def5678")])
        self.assertIn("Items merged", b)
        self.assertIn("- Add the x feature (PR #7, abc1234): `git revert -m 1 abc1234`", b)
        self.assertIn("- Old item (def5678): `git revert -m 1 def5678`", b)

    def test_the_list_is_in_merge_order_and_says_to_revert_latest_first(self):
        b = self.body([("First", 1, "aaaaaaa"), ("Second", 2, "bbbbbbb")])
        self.assertLess(b.index("First"), b.index("Second"))
        self.assertIn("latest first", b)

    def test_no_items_says_none_yet(self):
        self.assertIn("Items merged: none yet", self.body([]))

    def test_unreadable_items_say_so_and_point_to_the_log(self):
        b = self.body(None)
        self.assertIn("could not be read from git", b)
        self.assertNotIn("git revert", b)

    def test_the_list_follows_the_summary(self):
        b = self.body([("A", 1, "aaaaaaa")])
        self.assertLess(b.index("Done: x"), b.index("Items merged"))


if __name__ == "__main__":
    unittest.main()
