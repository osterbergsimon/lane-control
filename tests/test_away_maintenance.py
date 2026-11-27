"""The ratchet's maintenance merges (lc-3g6o; docs/superpowers/specs/2026-11-27-ratchet-maintenance-merges-design.md): a PR
that changes only docs (and, when the owner names them, new test files) merges into the integration branch through
`away-merge --maintenance`, and the away branch then takes the integration branch in. gh is a stub that makes the merge for
real on a temp origin; nothing merges anywhere real."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
import hashlib
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "core"))
import maintenance  # noqa: E402
from test_away_merge import OID, OLD_OID, AwayMergeTest  # noqa: E402
from test_lane_away import git  # noqa: E402

DOCS_DIFF = "diff --git a/docs/guide.md b/docs/guide.md\n--- a/docs/guide.md\n+++ b/docs/guide.md\n@@ -1 +1 @@\n+A clearer line.\n"


def diff_of(*paths, mode=None, new=False):
    out = ""
    for p in paths:
        head = f"new file mode {mode or '100644'}\n" if new else (f"new mode {mode}\n" if mode else "")
        out += f"diff --git a/{p} b/{p}\n{head}--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n+x\n"
    return out


class MaintenanceTest(AwayMergeTest):
    KINDS = None  # bare --maintenance: docs

    def setUp(self):
        super().setUp()
        self.env["GH_REAL_MERGE"] = str(self.tmp / "gh-work")
        r = self.on("--continue", "--maintenance", *([self.KINDS] if self.KINDS else []))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.on_out = r.stdout
        self.pr(baseRefName="main", diff=DOCS_DIFF)

    def maint(self, *extra, pr=7, item="Fix the guide's wording", project="shop", env=None):
        (self.tmp / "gh.log").write_text("")
        return self.run_script("away-merge", "--maintenance", "--project", project, "--pr", pr, "--item", item, *extra,
                               env=env)

    def merge_body(self) -> str:
        return json.loads((self.tmp / "prs.json.merge").read_text())[-1]

    def period(self) -> dict:
        return json.loads((self.conf_dir / "improve-period.json").read_text())

    def settings(self, data):
        """The project's settings store (outside the repo), as machineconf.settings_file keys it."""
        real = self.shop.resolve()
        key = f"{real.name}-{hashlib.sha256(str(real).encode()).hexdigest()[:10]}"
        f = Path(self.env["XDG_CONFIG_HOME"]) / "lane-control" / "projects" / key / "settings.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"maintenance_paths": data}))

    def away_tip(self) -> str:
        return git(self.shop, "ls-remote", "origin", f"refs/heads/{self.branch}").split()[0]

    def assertFallsBack(self, r, why):
        self.assertRefused(r, "maintenance")
        self.assertIn(why, r.stderr)
        self.assertIn(f"gh pr edit 7 --base {self.branch}", r.stderr)  # the way it goes to the away branch, as today


class DocsMergeTests(MaintenanceTest):
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
        self.assertTrue(any(c.startswith(f"api repos/{{owner}}/{{repo}}/compare/main...{OID}") for c in self.gh_calls()))

    def test_the_root_readme_and_changelog_and_a_docs_image_are_docs(self):
        self.pr(baseRefName="main", diff=diff_of("README.md", "CHANGELOG.md", "docs/a/notes.md", "docs/a/b.rst", "docs/c.svg")
                + "diff --git a/docs/shot.png b/docs/shot.png\nnew file mode 100644\nBinary files /dev/null and b/docs/shot.png differ\n")
        r = self.maint()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_r2_M1_build_files_with_a_txt_name_are_not_docs(self):  # r2 M1: an extension allowlist, no .txt
        self.pr(baseRefName="main", diff=diff_of("docs/requirements.txt", "docs/CMakeLists.txt"))
        r = self.maint()
        self.assertFallsBack(r, "docs/requirements.txt is not a docs content file")
        self.assertIn("docs/CMakeLists.txt is not a docs content file", r.stderr)

    def test_r2_M2_plans_and_specs_agents_follow_are_never_maintenance(self):  # r2 M2
        self.pr(baseRefName="main", diff=diff_of("docs/superpowers/plans/2026-11-23-away-improve.md"))
        self.assertFallsBack(self.maint(), "is an agent instruction, CI or tooling file (**/superpowers/**)")

    def test_r2_m1_a_file_main_moved_since_the_lane_branched_is_refused(self):  # r2 m1: git follows main's renames
        work = self.tmp / "gh-work"
        subprocess.run(["git", "clone", "-q", str(self.tmp / "shop.git"), str(work)], check=True, capture_output=True)
        (work / "docs").mkdir()
        (work / "docs" / "rules.md").write_text("Rule one.\n")
        git(work, "add", "docs/rules.md")
        git(work, "commit", "-qm", "rules")
        git(work, "push", "-q", "origin", "HEAD:main")
        base = git(work, "rev-parse", "HEAD").strip()
        (work / "personas").mkdir()
        git(work, "mv", "docs/rules.md", "personas/rules.md")
        git(work, "commit", "-qm", "the owner moves the rules")
        git(work, "push", "-q", "origin", "HEAD:main")
        self.pr(baseRefName="main", diff=diff_of("docs/rules.md"), merge_base=base)
        r = self.maint()
        self.assertRefused(r, "maintenance")
        self.assertIn("docs/rules.md was moved to personas/rules.md on main", r.stderr)
        self.pr(baseRefName="main", diff=diff_of("docs/rules.md"), merge_base="0" * 40)  # a base that cannot be read: refused
        self.assertIn("merge base", self.maint().stderr)

    def test_markdown_outside_docs_is_not_maintenance(self):
        self.pr(baseRefName="main", diff=diff_of("src/notes/CHANGES.md"))
        self.assertFallsBack(self.maint(), "src/notes/CHANGES.md is not a maintenance path")

    def test_one_code_path_refuses_and_falls_back_to_the_away_branch(self):
        self.pr(baseRefName="main", diff=DOCS_DIFF + diff_of("src/app.py"))
        self.assertFallsBack(self.maint(), "src/app.py is not a maintenance path")
        self.pr(baseRefName=self.branch, diff=DOCS_DIFF + diff_of("src/app.py"))
        self.assertEqual(self.merge().returncode, 0)  # the away branch takes it as any item

    def test_a_github_path_refuses_and_falls_back(self):
        self.pr(baseRefName="main", diff=diff_of("docs/a.md", ".github/workflows/ci.yml"))
        self.assertFallsBack(self.maint(), ".github/workflows/ci.yml is an agent instruction, CI or tooling file")
        self.pr(baseRefName=self.branch, diff=diff_of("docs/a.md", ".github/workflows/ci.yml"))
        self.assertEqual(self.merge().returncode, 0)

    def test_agent_instructions_end_to_end_are_refused(self):  # B1
        for path in ("AGENTS.md", "CLAUDE.md", "docs/AGENTS.md", "docs/skills/away-improve/SKILL.md",
                     "docs/personas/reviewer.md", "docs/.claude/settings.json"):
            with self.subTest(path=path):
                self.pr(baseRefName="main", diff=diff_of(path))
                self.assertFallsBack(self.maint(), "is an agent instruction, CI or tooling file")

    def test_a_deny_path_refuses_and_the_away_branch_refuses_it_too(self):
        self.pr(baseRefName="main", diff=diff_of("docs/migrations/notes.md"))
        r = self.maint()
        self.assertRefused(r, "deny")
        self.assertIn("**/migrations/**", r.stderr)
        self.assertEqual(r.stderr.count("matches the owner's deny path"), 1, r.stderr)  # said once
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
        self.pr(baseRefName="main", diff=DOCS_DIFF,
                compare_files=[{"filename": "docs/guide.md"}, {"filename": "lib/x.py", "previous_filename": "docs/x.md"}])
        self.assertFallsBack(self.maint(), "lib/x.py is not a maintenance path")

    def test_a_compare_that_cannot_be_read_or_is_too_long_refuses(self):
        r = self.maint(env={"GH_COMPARE_FAIL": "HTTP 502"})
        self.assertRefused(r, "maintenance")
        self.pr(baseRefName="main", diff=DOCS_DIFF, compare_files=[{"filename": f"docs/{i}.md"} for i in range(300)])
        r = self.maint()
        self.assertRefused(r, "maintenance")
        self.assertIn("too many", r.stderr)

    def test_an_executable_a_symlink_and_a_binary_are_never_maintenance(self):
        for diff, word in ((diff_of("docs/run.md", mode="100755"), "an executable"),
                           (diff_of("docs/link.md", mode="120000"), "a symlink"),
                           ("diff --git a/docs/blob.md b/docs/blob.md\nBinary files a/docs/blob.md and b/docs/blob.md differ\n",
                            "a binary file")):
            with self.subTest(word=word):
                self.pr(baseRefName="main", diff=diff)
                self.assertFallsBack(self.maint(), word)

    def test_tests_are_not_maintenance_unless_named(self):  # M1: tests are opt-in
        self.pr(baseRefName="main", diff=diff_of("tests/test_new.py", new=True))
        r = self.maint()
        self.assertFallsBack(r, "do not include tests")
        self.assertIn("--maintenance docs,tests", r.stderr)

    def test_the_base_must_be_the_integration_branch(self):
        self.pr(baseRefName=self.branch, diff=DOCS_DIFF)
        self.assertRefused(self.maint(), "base")
        self.pr(baseRefName="develop", diff=DOCS_DIFF)
        self.assertRefused(self.maint(), "base")

    def test_the_summary_pr_an_away_head_and_a_fork_are_never_maintenance(self):
        self.pr(baseRefName="main", diff=DOCS_DIFF, headRefName=self.branch)
        self.assertRefused(self.maint(), "head")
        self.pr(baseRefName="main", diff=DOCS_DIFF, isCrossRepository=True)
        self.assertRefused(self.maint(), "head")
        st = self.state()
        st["prs"]["shop"] = 7
        self.improve_file().write_text(json.dumps(st))
        self.pr(baseRefName="main", diff=DOCS_DIFF)
        r = self.maint()
        self.assertRefused(r, "head")
        self.assertIn("summary PR", r.stderr)

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
        r = self.maint(env={"GH_PROTECTION": prot})
        self.assertRefused(r, "protection")
        self.assertIn("1 approving review", r.stderr)
        rules = json.dumps([{"type": "pull_request", "parameters": {"required_approving_review_count": 2}}])
        r = self.maint(env={"GH_RULES": rules})
        self.assertRefused(r, "protection")
        self.assertIn("ruleset", r.stderr)

    def test_protection_that_cannot_be_read_refuses(self):
        r = self.maint(env={"GH_PROTECTION_ERR": "gh: Bad credentials (HTTP 401)"})
        self.assertRefused(r, "protection")
        self.assertIn("cannot be read", r.stderr)

    def test_protection_without_a_review_is_fine(self):
        prot = json.dumps({"required_pull_request_reviews": {"required_approving_review_count": 0},
                           "enforce_admins": {"enabled": True}, "required_status_checks": {"contexts": ["unittest"]}})
        r = self.maint(env={"GH_PROTECTION": prot})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_projects_own_paths_only_narrow(self):  # M2
        self.settings(["docs/guide.md", "docs/howto/**"])
        r = self.maint()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.pr(n=8, baseRefName="main", diff=diff_of("docs/other.md"))
        r = self.maint(pr=8)
        self.assertRefused(r, "maintenance")
        self.assertIn("matches none of the project's maintenance_paths", r.stderr)

    def test_project_paths_that_reach_outside_the_kinds_are_refused_at_load(self):  # M2
        for globs in (["**"], ["*"], ["**/*.md"], ["core/**"], ["orchestrators/**"], ["bin/**"], ["**/*.ts"], [".github/**"],
                      ["docs/../core/**"], "docs/**", []):
            with self.subTest(globs=globs):
                self.settings(globs)
                r = self.maint()
                self.assertRefused(r, "maintenance")
                self.assertIn("maintenance_paths", r.stderr)
        self.settings(["core/**"])
        self.assertIn("only narrow", self.maint().stderr)


class StopsWithTheModeTests(MaintenanceTest):  # M3
    def test_off_stops_maintenance_merges_and_the_drain_keeps_the_away_branch(self):
        self.assertEqual(self.run_script("lane-improve", "off").returncode, 0)
        self.assertFallsBack(self.maint(), "draining")
        self.pr(baseRefName=self.branch, diff=DOCS_DIFF)
        self.assertEqual(self.merge().returncode, 0)  # the running lane's last PR still lands on the away branch

    def test_r2_m2_a_reached_max_hours_stops_them(self):  # r2 m2: the wall clock bounds writes to main too
        self.assertEqual(self.on("--continue", "--max-hours", "1").returncode, 0)
        later = {"LANE_NOW": str(time.time() + 5 * 3600)}
        self.assertIn("cap reached", self.run_script("lane-improve", "status", env=later).stdout)
        r = self.maint(env=later)
        self.assertFallsBack(r, "a cap is reached for shop (hours:")

    def test_a_passed_end_time_stops_them(self):
        st = self.state()
        st["end"] = "2020-01-01T00:00:00+00:00"
        self.improve_file().write_text(json.dumps(st))
        self.assertFallsBack(self.maint(), "draining")

    def test_switched_off_or_changed_under_the_lock_refuses(self):  # m1: the gates can run for minutes
        loader = importlib.machinery.SourceFileLoader("away_merge_mod", str(ROOT / "core" / "scripts" / "away-merge"))
        spec = importlib.util.spec_from_loader("away_merge_mod", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        st = self.state()
        self.assertEqual(mod.settings_moved(st, st, "on", "shop", True), [])
        self.assertTrue(mod.settings_moved(st, {**st, "maintenance": []}, "on", "shop", True))
        self.assertTrue(mod.settings_moved(st, {**st, "deny_paths": ["docs/**"]}, "on", "shop", True))
        self.assertTrue(mod.settings_moved(st, {**st, "deny_paths": ["docs/**"]}, "on", "shop", False))
        self.assertTrue(mod.settings_moved(st, st, "draining", "shop", True))
        self.assertTrue(mod.settings_moved(st, {**st, "integration": {"shop": "develop"}}, "on", "shop", True))


class TestsKindTests(MaintenanceTest):  # M1: tests are opt-in, and only new test files
    KINDS = "docs,tests"

    def test_on_says_plainly_that_this_is_code_nobody_reviewed(self):
        self.assertEqual(self.state()["maintenance"], ["docs", "tests"])
        self.assertIn("nobody but agents reviewed", self.on_out)
        self.assertIn("code nobody but agents reviewed", self.run_script("lane-improve", "status").stdout)

    def test_a_new_test_file_merges_into_main(self):
        self.pr(baseRefName="main", diff=diff_of("tests/test_new.py", new=True), real_file="tests/test_new.py")
        r = self.maint()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_changing_deleting_or_the_suites_machinery_is_refused(self):
        for diff, why in ((diff_of("tests/test_x.py"), "an existing test file"),
                          ("diff --git a/tests/test_x.py b/tests/test_x.py\ndeleted file mode 100644\n--- a/tests/test_x.py\n"
                           "+++ /dev/null\n@@ -1 +0,0 @@\n-x\n", "an existing test file"),
                          (diff_of("tests/conftest.py", new=True), "machinery"),
                          (diff_of("tests/testhome.py", new=True), "machinery"),
                          (diff_of("tests/stubs/test_gh.py", new=True), "machinery"),
                          (diff_of("tests/helpers/test_a.py", new=True), "machinery"),
                          (diff_of("tests/test-host.sh", new=True), "not named as a test file"),
                          (diff_of("tests/data.json", new=True), "not named as a test file")):
            with self.subTest(why=why, diff=diff[:40]):
                self.pr(baseRefName="main", diff=diff)
                self.assertFallsBack(self.maint(), why)


class TakeInTests(MaintenanceTest):
    def test_the_away_branch_merges_main_afterwards(self):
        before = self.away_tip()
        r = self.maint()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sha = json.loads((self.tmp / "prs.json").read_text())["7"]["merge_oid"]
        tip = self.away_tip()
        git(self.shop, "fetch", "-q", "origin")
        self.assertEqual(git(self.shop, "rev-list", "--parents", "-n", "1", tip).split(), [tip, before, sha])  # a merge
        self.assertTrue(git(self.shop, "log", "-1", "--format=%s", tip).startswith("ratchet: take in main after maintenance #7"))
        self.assertIn("took in main", r.stdout)
        self.assertEqual(self.period()["merges"]["shop"][-1]["taken_in"], True)

    def test_the_summary_pr_lists_the_maintenance_merges_in_their_own_section(self):
        self.assertEqual(self.maint().returncode, 0)
        body = self.summary_prs(self.shop)[str(self.state()["prs"]["shop"])]["body"]
        self.assertIn("Maintenance merges into main", body)
        self.assertIn("Fix the guide's wording (PR #7", body)
        self.assertNotIn("NOT yet in the away branch", body)
        self.assertIn("Items merged: none yet.", body)  # the take-in merge is not an item

    def conflict(self):
        self.merge_item(self.shop, self.branch)  # an item on the away branch
        work = self.tmp / "work-shop"
        git(work, "checkout", "-q", self.branch)
        (work / "docs").mkdir(exist_ok=True)
        (work / "docs" / "guide.md").write_text("The away branch's line.\n")
        git(work, "add", "docs/guide.md")
        git(work, "commit", "-qm", "away side")
        git(work, "push", "-q", "origin", self.branch)
        self.pr(baseRefName="main", diff=DOCS_DIFF, real_file="docs/guide.md", real_text="Main's line.\n")

    def test_a_conflict_stops_is_reported_and_refuses_the_next_maintenance_merge(self):
        self.conflict()
        before = self.away_tip()
        r = self.maint()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("merged PR 7 into main", r.stdout)
        self.assertIn("conflict", r.stderr)
        self.assertIn("docs/guide.md", r.stderr)
        self.assertIn("tell the owner", r.stderr)  # m3: when the resolving PR is refused
        self.assertEqual(self.away_tip(), before)  # nothing forced, nothing changed
        self.pr(n=8, baseRefName="main", diff=diff_of("docs/other.md"))
        r = self.maint(pr=8)
        self.assertRefused(r, "take-in")
        self.assertIn("PR #7", r.stderr)
        self.assertIn("not taken in", self.run_script("lane-improve", "status").stdout)
        body = self.summary_prs(self.shop)[str(self.state()["prs"]["shop"])]["body"]
        self.assertIn("NOT yet in the away branch", body)  # m2: read from git, not assumed

    def test_r2_M3_status_reads_a_resolved_take_in_as_resolved(self):  # r2 M3
        self.conflict()
        self.assertEqual(self.maint().returncode, 3)
        self.assertIn("not taken in", self.run_script("lane-improve", "status").stdout)
        # a lane resolves it: main merged into the away branch, the conflict settled, pushed (as its PR would land)
        work = self.tmp / "work-shop"
        git(work, "fetch", "-q", "origin")
        git(work, "checkout", "-q", self.branch)
        git(work, "reset", "-q", "--hard", f"origin/{self.branch}")
        subprocess.run(["git", "-C", str(work), "-c", "user.name=t", "-c", "user.email=t@example.invalid", "merge", "-q",
                        "origin/main"], capture_output=True)
        (work / "docs" / "guide.md").write_text("Both lines, settled.\n")
        git(work, "add", "docs/guide.md")
        git(work, "commit", "-qm", "away: Take in main")
        git(work, "push", "-q", "origin", self.branch)
        st = self.run_script("lane-improve", "status").stdout
        self.assertNotIn("not taken in", st)
        self.assertIn("maintenance merges 1", st)
        self.pr(n=8, baseRefName="main", diff=diff_of("docs/other.md"))
        self.assertEqual(self.maint(pr=8).returncode, 0)  # away-merge and status agree
        self.assertNotIn("not taken in", self.run_script("lane-improve", "status").stdout)

    def test_a_pending_take_in_is_read_from_git_whatever_the_period_record_says(self):  # m3
        self.conflict()
        self.assertEqual(self.maint().returncode, 3)
        (self.conf_dir / "improve-period.json").unlink()  # a lost or new period's record knows nothing of it
        self.pr(n=8, baseRefName="main", diff=diff_of("docs/other.md"))
        r = self.maint(pr=8)
        self.assertRefused(r, "take-in")
        self.assertIn("PR #7", r.stderr)

    def test_a_merge_commit_origin_does_not_serve_yet_is_not_taken_for_taken_in(self):  # M5
        self.pr(baseRefName="main", diff=DOCS_DIFF, merge_oid="f" * 40, merge_oid_fixed=True)
        before = self.away_tip()
        r = self.maint()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("not on origin's main yet", r.stderr)
        self.assertEqual(self.away_tip(), before)
        rec = self.period()["merges"]["shop"][-1]
        self.assertIs(rec["taken_in"], False)
        self.pr(n=8, baseRefName="main", diff=diff_of("docs/other.md"))
        self.assertRefused(self.maint(pr=8), "take-in")

    def test_an_unknown_merge_commit_needs_main_to_hold_the_head(self):  # M5
        self.pr(baseRefName="main", diff=DOCS_DIFF, merge_oid_fixed=True)
        r = self.maint(env={"GH_SHA_FAIL": "HTTP 502"})
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("does not hold the PR's head", r.stderr)
        self.assertEqual(self.period()["merges"]["shop"][-1]["target"], OID)  # pending() looks for the head later

    def test_status_counts_them_and_they_count_toward_max_items(self):
        self.assertEqual(self.on("--continue", "--max-items", "2").returncode, 0)
        self.assertEqual(self.maint().returncode, 0)
        st = self.run_script("lane-improve", "status").stdout
        self.assertIn("maintenance: on (docs)", st)
        self.assertIn("maintenance merges 1", st)
        self.assertIn("items 1/2", st)
        self.assertEqual(self.period()["merges"]["shop"][0]["kind"], "maintenance")
        self.pr(n=9, diff=diff_of("src/app.py"))  # an away item
        self.assertEqual(self.merge(pr=9).returncode, 0)
        self.pr(n=10, baseRefName="main", diff=diff_of("docs/more.md"))
        self.assertRefused(self.maint(pr=10), "improve")  # the cap: 2 items, one of them maintenance
        rows = [json.loads(x) for x in self.run_script("lane-improve", "status", "--json").stdout.splitlines()]
        self.assertEqual([x["maintenance_merges"] for x in rows if x["project"] == "shop"], [1])


class SwitchTests(MaintenanceTest):
    def test_on_records_docs_by_default_and_a_rerun_keeps_it(self):
        self.assertEqual(self.state()["maintenance"], ["docs"])
        r = self.on("--continue")
        self.assertEqual(self.state()["maintenance"], ["docs"])
        self.assertIn("maintenance: on (docs)", r.stdout)
        self.assertEqual(self.on("--continue", "--no-maintenance").returncode, 0)
        self.assertEqual(self.state()["maintenance"], [])
        r = self.maint()
        self.assertRefused(r, "maintenance")
        self.assertIn("--maintenance", r.stderr)

    def test_an_unknown_kind_is_a_usage_error(self):
        self.assertEqual(self.on("--continue", "--maintenance", "docs,code").returncode, 2)

    def test_a_new_period_has_it_off_by_default(self):
        self.reset()
        self.assertEqual(self.on("--project", "shop").returncode, 0)
        self.assertEqual(self.state()["maintenance"], [])
        self.assertIn("maintenance: off", self.run_script("lane-improve", "status").stdout)

    def test_the_front_door_takes_the_flag(self):
        self.reset()
        r = self.run_script("lane-control", "ratchet", "on", "--yes", "--project", "shop", "--maintenance", "docs,tests")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.state()["maintenance"], ["docs", "tests"])


class PolicyTests(unittest.TestCase):
    """core/maintenance.py's rules, path by path."""

    def bad(self, path, kinds=("docs",), status="modified", globs=None, deny=(), modes=None, binary=()):
        return maintenance.problems({path: status}, list(kinds), globs, list(deny), modes or {}, set(binary))

    def test_agent_instructions_are_never_maintenance_at_any_depth_or_case(self):  # B1, M4
        for p in ("AGENTS.md", "docs/AGENTS.lane-control.md", "docs/CLAUDE.md", "docs/GEMINI.md", "docs/x/SKILL.md",
                  "docs/.claude/settings.json", "docs/.Claude/commands/x.md", "docs/.opencode/agents/a.md",
                  "docs/.agents/skills/x.md", "docs/.codex/a.md", "docs/skills/a.md", "docs/personas/reviewer.md",
                  "docs/templates/away-build-brief.md", "docs/prompts/x.md", "docs/.github/copilot-instructions.md",
                  "docs/.cursorrules", "docs/.mcp.json"):
            with self.subTest(p=p):
                (why,) = self.bad(p)
                self.assertIn("agent instruction", why)

    def test_docs_are_an_extension_allowlist(self):  # M1, r2 M1
        for p in ("docs/conf.py", "docs/package.json", "docs/Makefile", "docs/gen.sh", "docs/x.yml", "docs/a.txt",
                  "docs/requirements.txt", "docs/CMakeLists.txt", "docs/a.adoc", "docs/a.markdown", "docs/a.html"):
            with self.subTest(p=p):
                (why,) = self.bad(p)
                self.assertIn("not a docs content file", why)
        for p in ("docs/a.md", "docs/a/b.MD", "docs/a.rst", "docs/a.svg", "docs/a.png", "docs/a.webp", "README.md",
                  "CHANGELOG.md"):
            with self.subTest(p=p):
                self.assertEqual(self.bad(p), [])

    def test_r2_M2_agent_followed_files_under_docs(self):  # r2 M2
        for p in ("docs/superpowers/plans/x.md", "docs/superpowers/specs/x.md", "docs/plans/x.md", "docs/agents/reviewer.md",
                  "docs/commands/x.md", "docs/AGENT.md", "docs/QWEN.md", "docs/copilot-instructions.md",
                  "docs/.windsurf/rules/a.md", "docs/.clinerules/x.md", "docs/.kiro/steering/a.md", "docs/.junie/guidelines.md"):
            with self.subTest(p=p):
                (why,) = self.bad(p, status="added")
                self.assertIn("agent instruction", why)

    def test_r2_m4_jest_mocks_and_the_wording(self):  # r2 m4
        (why,) = self.bad("tests/__mocks__/a.test.js", kinds=("tests",), status="added")
        self.assertIn("machinery", why)
        text = maintenance.describe(["tests"])
        self.assertIn("tests/ or test/", text)
        self.assertIn("change how the existing tests behave", text)

    def test_r2_m3_unreadable_git_is_a_reason_and_the_lock_reads_again(self):  # r2 m3
        with mock.patch.object(maintenance, "fetch", lambda *a: True), mock.patch.object(maintenance, "items", lambda *a: None):
            self.assertIn("cannot be read from git", maintenance.pending(Path("."), "away/x", "main", []))
        loader = importlib.machinery.SourceFileLoader("away_merge_r2", str(ROOT / "core" / "scripts" / "away-merge"))
        spec = importlib.util.spec_from_loader("away_merge_r2", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        P = mock.patch.object
        with P(mod.maintenance, "globs_for", lambda c: (None, "the kinds alone", None)), \
                P(mod.protection, "review_needed", lambda c, b: None), P(mod.improve, "merges_of", lambda s, p: []):
            with P(mod.maintenance, "pending", lambda *a: None):
                self.assertEqual(mod.maintenance_again(Path("."), {}, "shop", "away/x", "main", None), [])
                self.assertTrue(mod.maintenance_again(Path("."), {}, "shop", "away/x", "main", ["docs/guide/**"]))  # narrowed
            with P(mod.maintenance, "pending", lambda *a: "away/x has not taken in the maintenance merge of PR #7 (abc) yet"):
                self.assertIn("PR #7", mod.maintenance_again(Path("."), {}, "shop", "away/x", "main", None)[0])
                with P(mod.protection, "review_needed", lambda c, b: "main's protection requires 1 approving review(s)"):
                    self.assertEqual(len(mod.maintenance_again(Path("."), {}, "shop", "away/x", "main", None)), 2)

    def test_a_control_character_and_odd_names(self):  # M4
        (why,) = self.bad("docs/a\nb.md")
        self.assertIn("control character", why)
        self.assertTrue(maintenance.glob_re("**/migrations/**").fullmatch("docs/migrations/a\nb.md"))  # DOTALL
        self.assertTrue(maintenance.never(".Claude/commands/x.md"))

    def test_tests_need_the_kind_a_new_file_and_a_test_name(self):  # M1
        self.assertEqual(self.bad("tests/test_a.py", kinds=("docs", "tests"), status="added"), [])
        self.assertEqual(self.bad("tests/sub/a_test.go", kinds=("docs", "tests"), status="added"), [])
        self.assertEqual(self.bad("tests/test_harnesses.py", kinds=("tests",), status="added"), [])  # a test, not machinery
        self.assertIn("do not include tests", self.bad("tests/test_a.py", status="added")[0])
        for status in ("modified", "removed", "renamed"):
            self.assertIn("existing test file", self.bad("tests/test_a.py", kinds=("tests",), status=status)[0])
        self.assertIn("docs, which this period", self.bad("docs/a.md", kinds=("tests",))[0])

    def test_project_globs_only_narrow(self):  # M2
        for g in ("core/**", "**/*.ts", "**", "*", "**/*.md", "bin/**", "orchestrators/**", "../x", "/abs", "docs/a\nb"):
            with self.subTest(g=g):
                self.assertTrue(maintenance.bad_glob(g))
        for g in ("docs/**", "docs/guide/*.md", "tests/**", "README.md"):
            self.assertIsNone(maintenance.bad_glob(g))
        (why,) = self.bad("docs/a.md", globs=["docs/guide/**"])
        self.assertIn("maintenance_paths", why)

    def test_binary_only_as_a_docs_image(self):  # m4
        self.assertEqual(self.bad("docs/a.png", binary={"docs/a.png"}), [])
        self.assertIn("binary", self.bad("docs/a.md", binary={"docs/a.md"})[0])

    def test_kinds_parse(self):
        self.assertEqual(maintenance.parse_kinds(""), ["docs"])
        self.assertEqual(maintenance.parse_kinds("tests,docs"), ["docs", "tests"])
        with self.assertRaises(ValueError):
            maintenance.parse_kinds("docs,code")


if __name__ == "__main__":
    unittest.main()
