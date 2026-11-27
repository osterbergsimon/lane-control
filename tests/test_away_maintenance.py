"""The ratchet's maintenance merges (lc-3g6o; docs/superpowers/specs/2026-11-27-ratchet-maintenance-merges-design.md): a PR
that changes only docs or tests merges into the integration branch through `away-merge --maintenance`, and the away branch then
takes the integration branch in. gh is a stub, git is temp repos; nothing merges anywhere real."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_away_merge import ENTRY, OID, OLD_OID, AwayMergeTest  # noqa: E402
from test_lane_away import git  # noqa: E402

DOCS_DIFF = "diff --git a/docs/guide.md b/docs/guide.md\n--- a/docs/guide.md\n+++ b/docs/guide.md\n@@ -1 +1 @@\n+A clearer line.\n"
TESTS_DIFF = ("diff --git a/tests/test_x.py b/tests/test_x.py\nnew file mode 100644\n--- /dev/null\n+++ b/tests/test_x.py\n"
              "@@ -0,0 +1 @@\n+def test_x(): pass\n")


def diff_of(*paths, mode=None):
    out = ""
    for p in paths:
        out += f"diff --git a/{p} b/{p}\n" + (f"new file mode {mode}\n" if mode else "") + f"--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n+x\n"
    return out


class MaintenanceTest(AwayMergeTest):
    def setUp(self):
        super().setUp()
        r = self.on("--continue", "--maintenance")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.pr(baseRefName="main", diff=DOCS_DIFF)

    def maint(self, *extra, pr=7, item="Fix the guide's wording", project="shop"):
        (self.tmp / "gh.log").write_text("")
        return self.run_script("away-merge", "--maintenance", "--project", project, "--pr", pr, "--item", item, *extra)

    def merge_body(self) -> str:
        return json.loads((self.tmp / "prs.json").with_suffix(".json.merge").read_text())[-1]

    def period(self) -> dict:
        return json.loads((self.conf_dir / "improve-period.json").read_text())

    def settings(self, data):
        """The project's settings store (outside the repo), as machineconf.settings_file keys it."""
        real = self.shop.resolve()
        key = f"{real.name}-{hashlib.sha256(str(real).encode()).hexdigest()[:10]}"
        f = Path(self.env["XDG_CONFIG_HOME"]) / "lane-control" / "projects" / key / "settings.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"maintenance_paths": data} if not isinstance(data, dict) else data))

    def main_merge(self, path="docs/guide.md", text="A clearer line.\n", pr=7, title="Fix the guide's wording") -> str:
        """A maintenance merge on origin's main, as GitHub's merge button makes it: returns its full sha."""
        work = self.tmp / "work-main"
        if not work.exists():
            subprocess.run(["git", "clone", "-q", str(self.tmp / "shop.git"), str(work)], check=True, capture_output=True)
        git(work, "fetch", "-q", "origin")
        git(work, "checkout", "-q", "-B", "main", "origin/main")
        git(work, "checkout", "-q", "-B", f"maint-{pr}")
        (work / path).parent.mkdir(parents=True, exist_ok=True)
        (work / path).write_text(text)
        git(work, "add", path)
        git(work, "commit", "-qm", title)
        git(work, "checkout", "-q", "main")
        git(work, "merge", "-q", "--no-ff", "-m",
            f"maintenance: {title}\n\nPull request #{pr}\nRatchet-maintenance: {self.branch}\n\n{ENTRY}", f"maint-{pr}")
        git(work, "push", "-q", "origin", "main")
        return git(work, "rev-parse", "HEAD").strip()

    def away_tip(self) -> str:
        return git(self.shop, "ls-remote", "origin", f"refs/heads/{self.branch}").split()[0]


class MaintenanceMergeTests(MaintenanceTest):
    def test_a_docs_only_pr_merges_into_main(self):
        r = self.maint()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (call,) = self.merged()
        self.assertTrue(call.startswith("pr merge 7 --merge --subject maintenance: Fix the guide's wording"), call)
        self.assertTrue(call.endswith(f"--match-head-commit {OID}"), call)
        body = self.merge_body()
        self.assertIn("Pull request #7", body)
        self.assertIn(f"Ratchet-maintenance: {self.branch}", body)
        self.assertIn("## What", body)
        self.assertIn("into main", r.stdout)
        # the paths were read at the exact head from GitHub
        self.assertTrue(any(c.startswith(f"api repos/{{owner}}/{{repo}}/compare/main...{OID}") for c in self.gh_calls()))

    def test_a_tests_only_pr_merges_into_main(self):
        self.pr(baseRefName="main", diff=TESTS_DIFF)
        r = self.maint()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.merged()), 1)

    def test_a_markdown_file_anywhere_is_maintenance(self):
        self.pr(baseRefName="main", diff=diff_of("README.md", "src/notes/CHANGES.md"))
        self.assertEqual(self.maint().returncode, 0)

    def assertFallsBack(self, r, why):
        self.assertRefused(r, "maintenance")
        self.assertIn(why, r.stderr)
        self.assertIn(f"gh pr edit 7 --base {self.branch}", r.stderr)  # the way it goes to the away branch, as today

    def test_one_code_path_refuses_and_falls_back_to_the_away_branch(self):
        self.pr(baseRefName="main", diff=DOCS_DIFF + diff_of("src/app.py"))
        self.assertFallsBack(self.maint(), "src/app.py matches no maintenance glob")
        self.pr(baseRefName=self.branch, diff=DOCS_DIFF + diff_of("src/app.py"))
        self.assertEqual(self.merge().returncode, 0)  # the away branch takes it as any item

    def test_a_github_path_refuses_and_falls_back(self):
        self.pr(baseRefName="main", diff=diff_of("docs/a.md", ".github/workflows/ci.yml"))
        self.assertFallsBack(self.maint(), ".github/workflows/ci.yml is a CI or tooling file")
        self.pr(baseRefName=self.branch, diff=diff_of("docs/a.md", ".github/workflows/ci.yml"))
        self.assertEqual(self.merge().returncode, 0)

    def test_a_github_path_is_refused_even_when_the_projects_globs_cover_it(self):
        self.settings({"maintenance_paths": ["docs/**", ".github/**"]})
        self.pr(baseRefName="main", diff=diff_of(".github/ISSUE_TEMPLATE.md"))
        self.assertFallsBack(self.maint(), "is a CI or tooling file")

    def test_a_deny_path_refuses_and_the_away_branch_refuses_it_too(self):
        self.pr(baseRefName="main", diff=diff_of("docs/migrations/notes.md"))
        r = self.maint()
        self.assertRefused(r, "deny")
        self.assertIn("**/migrations/**", r.stderr)
        self.pr(baseRefName=self.branch, diff=diff_of("docs/migrations/notes.md"))
        self.assertRefused(self.merge(), "deny")

    def test_a_private_name_refuses_and_the_away_branch_refuses_it_too(self):
        (self.conf_dir / "private-names.txt").write_text("example-host\n")
        self.pr(baseRefName="main", diff=DOCS_DIFF + "+ssh example-host\n")
        r = self.maint()
        self.assertRefused(r, "names")
        self.assertNotIn("example-host", r.stderr)
        self.pr(baseRefName=self.branch, diff=DOCS_DIFF + "+ssh example-host\n")
        self.assertRefused(self.merge(), "names")

    def test_a_head_that_moved_after_the_check_refuses(self):
        self.pr(baseRefName="main", diff=DOCS_DIFF, head_after=OLD_OID)
        self.assertRefused(self.maint(), "head")

    def test_the_paths_at_the_head_count_not_only_the_diff(self):
        # GitHub's file list at the head names a path the diff did not show: it is checked too
        self.pr(baseRefName="main", diff=DOCS_DIFF,
                compare_files=[{"filename": "docs/guide.md"}, {"filename": "lib/x.py", "previous_filename": "docs/x.py"}])
        self.assertFallsBack(self.maint(), "lib/x.py matches no maintenance glob")

    def test_a_compare_that_cannot_be_read_or_is_too_long_refuses(self):
        r = self.run_script("away-merge", "--maintenance", "--project", "shop", "--pr", "7", "--item", "x",
                            env={"GH_COMPARE_FAIL": "HTTP 502"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("refused (maintenance)", r.stderr)
        self.pr(baseRefName="main", diff=DOCS_DIFF, compare_files=[{"filename": f"docs/{i}.md"} for i in range(300)])
        r = self.maint()
        self.assertRefused(r, "maintenance")
        self.assertIn("too many", r.stderr)

    def test_an_executable_or_a_symlink_is_never_maintenance(self):
        for mode, word in (("100755", "an executable"), ("120000", "a symlink")):
            with self.subTest(mode=mode):
                self.pr(baseRefName="main", diff=diff_of("tests/run.sh", mode=mode))
                self.assertFallsBack(self.maint(), word)

    def test_it_is_off_unless_switched_on(self):
        self.assertEqual(self.on("--continue", "--no-maintenance").returncode, 0)
        r = self.maint()
        self.assertRefused(r, "maintenance")
        self.assertIn("--maintenance", r.stderr)
        self.assertFalse(self.state().get("maintenance", False))

    def test_the_base_must_be_the_integration_branch(self):
        self.pr(baseRefName=self.branch, diff=DOCS_DIFF)
        self.assertRefused(self.maint(), "base")
        self.pr(baseRefName="develop", diff=DOCS_DIFF)
        self.assertRefused(self.maint(), "base")

    def test_the_summary_pr_and_an_away_head_are_never_maintenance(self):
        self.pr(baseRefName="main", diff=DOCS_DIFF, headRefName=self.branch)
        r = self.maint()
        self.assertRefused(r, "head")
        st = self.state()
        st["prs"]["shop"] = 7  # a PR recorded as the summary PR is refused by its number too
        self.improve_file().write_text(json.dumps(st))
        self.pr(baseRefName="main", diff=DOCS_DIFF)
        r = self.maint()
        self.assertRefused(r, "head")
        self.assertIn("summary PR", r.stderr)

    def test_a_pr_from_a_fork_is_never_maintenance(self):
        self.pr(baseRefName="main", diff=DOCS_DIFF, isCrossRepository=True)
        self.assertRefused(self.maint(), "head")

    def test_same_gates_review_ci_and_entry(self):
        self.pr(baseRefName="main", diff=DOCS_DIFF, comments=[])
        self.assertRefused(self.maint(), "review")
        self.pr(baseRefName="main", diff=DOCS_DIFF, statusCheckRollup=[{"name": "unittest", "conclusion": "FAILURE"}])
        self.assertRefused(self.maint(), "ci")
        self.pr(baseRefName="main", diff=DOCS_DIFF, body="Fixes a typo.")
        self.assertRefused(self.maint(), "entry")

    def test_local_suite_ok_is_not_a_way_into_main(self):
        f = self.tmp / "suite.log"
        f.write_text("OK\n")
        self.assertEqual(self.maint("--local-suite-ok", str(f)).returncode, 2)

    def test_a_required_review_refuses_with_that_reason(self):
        prot = json.dumps({"required_pull_request_reviews": {"required_approving_review_count": 1},
                           "enforce_admins": {"enabled": True}})
        r = self.run_script("away-merge", "--maintenance", "--project", "shop", "--pr", "7", "--item", "x",
                            env={"GH_PROTECTION": prot})
        self.assertEqual(r.returncode, 1)
        self.assertIn("refused (protection)", r.stderr)
        self.assertIn("1 approving review", r.stderr)
        rules = json.dumps([{"type": "pull_request", "parameters": {"required_approving_review_count": 2}}])
        r = self.run_script("away-merge", "--maintenance", "--project", "shop", "--pr", "7", "--item", "x",
                            env={"GH_RULES": rules})
        self.assertIn("refused (protection)", r.stderr)
        self.assertIn("ruleset", r.stderr)

    def test_protection_that_cannot_be_read_refuses(self):
        r = self.run_script("away-merge", "--maintenance", "--project", "shop", "--pr", "7", "--item", "x",
                            env={"GH_PROTECTION_ERR": "gh: Bad credentials (HTTP 401)"})
        self.assertIn("refused (protection)", r.stderr)
        self.assertIn("cannot be read", r.stderr)

    def test_protection_without_a_review_is_fine(self):
        prot = json.dumps({"required_pull_request_reviews": {"required_approving_review_count": 0},
                           "enforce_admins": {"enabled": True}, "required_status_checks": {"contexts": ["unittest"]}})
        r = self.run_script("away-merge", "--maintenance", "--project", "shop", "--pr", "7", "--item", "x",
                            env={"GH_PROTECTION": prot})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_projects_own_globs_replace_the_default(self):
        self.settings({"maintenance_paths": ["handbook/**"]})
        self.pr(baseRefName="main", diff=diff_of("handbook/intro.txt"))
        self.assertEqual(self.maint().returncode, 0)
        self.pr(baseRefName="main", diff=DOCS_DIFF)
        self.assertFallsBack(self.maint(), "docs/guide.md matches no maintenance glob (handbook/**)")

    def test_a_glob_that_covers_code_is_refused(self):
        for globs in (["**"], ["*"], ["**/*.py"], "docs/**", []):
            with self.subTest(globs=globs):
                self.settings(globs)
                r = self.maint()
                self.assertRefused(r, "maintenance")
                self.assertIn("maintenance_paths", r.stderr)


class TakeInTests(MaintenanceTest):
    def test_the_away_branch_merges_main_afterwards(self):
        before = self.away_tip()
        sha = self.main_merge()
        self.pr(baseRefName="main", diff=DOCS_DIFF, merge_oid=sha)
        r = self.maint()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        tip = self.away_tip()
        git(self.shop, "fetch", "-q", "origin")
        self.assertEqual(git(self.shop, "rev-list", "--parents", "-n", "1", tip).split(), [tip, before, sha])  # a merge
        self.assertTrue(git(self.shop, "log", "-1", "--format=%s", tip).startswith("ratchet: take in main after maintenance #7"))
        self.assertIn("took in main", r.stdout)
        self.assertEqual(self.period()["merges"]["shop"][-1]["taken_in"], True)

    def test_the_summary_pr_lists_the_maintenance_merges_in_their_own_section(self):
        sha = self.main_merge()
        self.pr(baseRefName="main", diff=DOCS_DIFF, merge_oid=sha)
        self.assertEqual(self.maint().returncode, 0)
        body = self.summary_prs(self.shop)[str(self.state()["prs"]["shop"])]["body"]
        self.assertIn("Maintenance merges into main", body)
        self.assertIn("Fix the guide's wording (PR #7", body)
        self.assertIn("Items merged: none yet.", body)  # the take-in merge is not an item

    def test_a_conflict_stops_is_reported_and_refuses_the_next_maintenance_merge(self):
        self.merge_item(self.shop, self.branch)  # an item on the away branch
        work = self.tmp / "work-shop"
        git(work, "checkout", "-q", self.branch)
        (work / "docs").mkdir(exist_ok=True)
        (work / "docs" / "guide.md").write_text("The away branch's line.\n")
        git(work, "add", "docs/guide.md")
        git(work, "commit", "-qm", "away side")
        git(work, "push", "-q", "origin", self.branch)
        before = self.away_tip()
        sha = self.main_merge(text="Main's line.\n")
        self.pr(baseRefName="main", diff=DOCS_DIFF, merge_oid=sha)
        r = self.maint()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("merged PR 7 into main", r.stdout)
        self.assertIn("conflict", r.stderr)
        self.assertIn("docs/guide.md", r.stderr)
        self.assertEqual(self.away_tip(), before)  # nothing forced, nothing changed
        self.pr(n=8, baseRefName="main", diff=diff_of("docs/other.md"))
        r = self.maint(pr=8)
        self.assertRefused(r, "take-in")
        self.assertIn("PR #7", r.stderr)
        status = self.run_script("lane-improve", "status").stdout
        self.assertIn("not taken in", status)

    def test_status_counts_them_and_they_count_toward_max_items(self):
        self.assertEqual(self.on("--continue", "--max-items", "2").returncode, 0)
        self.assertEqual(self.maint().returncode, 0)
        st = self.run_script("lane-improve", "status").stdout
        self.assertIn("maintenance: on", st)
        self.assertIn("docs/**", st)
        self.assertIn("maintenance merges 1", st)
        self.assertIn("items 1/2", st)
        self.assertEqual(self.period()["merges"]["shop"][0]["kind"], "maintenance")
        self.pr(n=9, diff=diff_of("src/app.py"))  # an away item
        self.assertEqual(self.merge(pr=9).returncode, 0)
        self.pr(n=10, baseRefName="main", diff=diff_of("docs/more.md"))
        self.assertRefused(self.maint(pr=10), "improve")  # the cap: 2 items, one of them maintenance
        rows = [json.loads(x) for x in self.run_script("lane-improve", "status", "--json").stdout.splitlines()]
        self.assertEqual([x["maintenance_merges"] for x in rows if x["project"] == "shop"], [1])


class MaintenanceSwitchTests(MaintenanceTest):
    def test_on_records_it_and_says_so(self):
        self.assertIs(self.state()["maintenance"], True)
        r = self.on("--continue")
        self.assertIs(self.state()["maintenance"], True)  # a re-run keeps it
        self.assertIn("maintenance", r.stdout)

    def test_a_new_period_has_it_off_by_default(self):
        self.reset()
        self.assertEqual(self.on("--project", "shop").returncode, 0)
        self.assertFalse(self.state().get("maintenance", False))
        self.assertIn("maintenance: off", self.run_script("lane-improve", "status").stdout)

    def test_the_front_door_takes_the_flag(self):
        self.reset()
        r = self.run_script("lane-control", "ratchet", "on", "--yes", "--project", "shop", "--maintenance")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIs(self.state()["maintenance"], True)


if __name__ == "__main__":
    unittest.main()
