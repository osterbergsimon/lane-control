"""core/scripts/away-merge: the only merge route into a project's away branch. gh, git and gitleaks are stubs or temp
repos; nothing merges anywhere real."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lane_away import ImproveTest  # noqa: E402
from test_machine_setup import write_exec  # noqa: E402

GH_PR_STUB = """
#!/usr/bin/env python3
# gh stand-in for away-merge: `pr view N --json ...`, `pr diff N`, `pr merge N ...` from $GH_PRS (JSON {N: {...}}).
import json, os, sys
a = sys.argv[1:]
open(os.environ["GH_LOG"], "a").write(" ".join(a) + "\\n")
prs = json.load(open(os.environ["GH_PRS"]))
if a[:2] == ["api", "user"]:
    login = os.environ.get("GH_LOGIN", "")
    if not login:
        print("gh: not logged in", file=sys.stderr); sys.exit(1)
    print(login); sys.exit(0)
if a[:2] == ["pr", "list"]:
    print("[]"); sys.exit(0)
pr = prs.get(a[2]) if a[:1] == ["pr"] and len(a) > 2 else None
if pr is None:
    print("gh stub: no such PR / not modelled: " + " ".join(a), file=sys.stderr); sys.exit(1)
if a[:2] == ["pr", "diff"]:
    print(pr.get("diff", "")); sys.exit(0)
if a[:2] == ["pr", "view"] and "mergeCommit" in a:
    print(pr.get("merge_oid", "")); sys.exit(0)
if a[:2] == ["pr", "view"] and a[a.index("--json") + 1] == "headRefOid":
    print(json.dumps({"headRefOid": pr.get("head_after", pr["headRefOid"])})); sys.exit(0)
if a[:2] == ["pr", "view"]:
    print(json.dumps({k: pr[k] for k in ("baseRefName", "headRefOid", "state", "body", "comments", "statusCheckRollup", "files")})); sys.exit(0)
if a[:2] == ["pr", "merge"]:
    sys.exit(0)
print("gh stub: not modelled: " + " ".join(a), file=sys.stderr); sys.exit(1)
"""

GITLEAKS_STUB = """
#!/usr/bin/env python3
import os, sys
open(os.environ["GITLEAKS_LOG"], "a").write(" ".join(sys.argv[1:]) + "\\n")
src = sys.argv[sys.argv.index("--source") + 1]
text = open(os.path.join(src, "pr.diff")).read()
if "LEAKME" in text:
    print("1 leak found", file=sys.stderr); sys.exit(1)
sys.exit(0)
"""

CLEAN_DIFF = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n+print('hello')\n"
OID = "0123456789abcdef0123456789abcdef01234567"
OLD_OID = "fedcba9876543210fedcba9876543210fedcba98"


class AwayMergeTest(ImproveTest):
    def setUp(self):
        super().setUp()
        write_exec(self.bin / "gh", GH_PR_STUB)
        write_exec(self.bin / "gitleaks", GITLEAKS_STUB)
        self.env.update(GH_PRS=str(self.tmp / "prs.json"), GITLEAKS_LOG=str(self.tmp / "gitleaks.log"), GH_LOGIN="coord")
        self.assertEqual(self.on("--project", "shop", "--deny-paths", "**/migrations/**,infra/*").returncode, 0)
        self.branch = self.state()["branches"]["shop"]
        self.prs = {}
        self.pr()

    def marker(self, oid=OID, login="coord", text=None):
        return {"author": {"login": login}, "body": text if text is not None else f"Review: passed {oid}"}

    def pr(self, n=7, **over):
        pr = {"baseRefName": self.branch, "headRefOid": OID, "state": "OPEN", "body": "Adds x.\n",
              "comments": [self.marker()],
              "statusCheckRollup": [{"name": "unittest", "status": "COMPLETED", "conclusion": "SUCCESS"}],
              "files": [{"path": "x.py"}], "diff": CLEAN_DIFF, "merge_oid": "abc1234"}
        pr.update(over)
        self.prs[str(n)] = pr
        (self.tmp / "prs.json").write_text(json.dumps(self.prs))

    def merge(self, *extra, pr=7, item="Add the x feature", project="shop"):
        (self.tmp / "gh.log").write_text("")
        return self.run_script("away-merge", "--project", project, "--pr", pr, "--item", item, *extra)

    def gh_calls(self):
        return (self.tmp / "gh.log").read_text().splitlines()

    def merged(self):
        return [c for c in self.gh_calls() if c.startswith("pr merge")]

    def assertRefused(self, r, check):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"refused ({check})", r.stderr)
        self.assertEqual(self.merged(), [], "something was merged")


class AwayMergeTests(AwayMergeTest):
    def test_merges_with_subject(self):
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.merged(), [f"pr merge 7 --merge --subject away: Add the x feature --match-head-commit {OID}"])
        self.assertIn("abc1234", r.stdout)
        self.assertNotIn("--squash", " ".join(self.gh_calls()))
        self.assertNotIn("--rebase", " ".join(self.gh_calls()))

    def test_refuses_integration_base(self):
        for base in ("main", "develop"):
            self.pr(baseRefName=base)
            self.assertRefused(self.merge(), "base")

    def test_refuses_other_project_branch(self):
        self.pr(baseRefName="away/2026-01-01")  # another away branch than this project's one
        self.assertRefused(self.merge(), "base")
        self.pr(baseRefName=f"{self.branch}-2")
        self.assertRefused(self.merge(), "base")

    def test_refuses_when_improve_off(self):
        self.reset()
        self.assertRefused(self.merge(), "improve")
        self.assertEqual(self.gh_calls(), [])  # not even read

    def test_merges_while_draining_but_not_after(self):
        # review M1: the running lane's PR still merges after `off`; once the project is wrapped up it does not
        self.run_script("lane-improve", "off")
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.merged()), 1)
        self.assertEqual(self.run_script("lane-improve", "drained", "shop").returncode, 0)
        self.assertRefused(self.merge(), "improve")

    def test_merges_after_the_end_time_until_drained(self):
        st = self.state()
        st["end"] = "2020-01-01T00:00:00+00:00"
        self.improve_file().write_text(json.dumps(st))
        self.assertEqual(self.merge().returncode, 0)
        self.run_script("lane-improve", "drained", "shop")
        self.assertRefused(self.merge(), "improve")

    def test_draining_still_checks_the_base(self):
        self.run_script("lane-improve", "off")
        self.pr(baseRefName="main")
        self.assertRefused(self.merge(), "base")

    def test_a_malformed_state_file_says_so_once(self):
        self.improve_file().write_text("{nope")
        r = self.merge()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stderr.count("improve.json"), 1, r.stderr)

    def test_refuses_project_not_in_the_mode(self):
        self.assertRefused(self.merge(project="blog"), "improve")

    def test_refuses_without_review(self):
        self.pr(comments=[])
        self.assertRefused(self.merge(), "review")

    def test_the_body_never_counts(self):
        # review B2: the lane writes the PR body, so a marker there is no review
        self.pr(body=f"Adds x.\nReview: passed {OID}\n", comments=[])
        self.assertRefused(self.merge(), "review")

    def test_a_marker_by_someone_else_does_not_count(self):
        self.pr(comments=[self.marker(login="a-stranger")])
        r = self.merge()
        self.assertRefused(r, "review")
        self.assertIn("coord", r.stderr)  # says whose comment it takes

    def test_a_marker_without_the_head_or_for_an_older_head_does_not_count(self):
        for text in ("Review: passed", f"Review: passed {OLD_OID}", f"Review: passed {OID[:7]}",
                     f"Review: passed {OID} and more", f"Review: failed {OID}"):
            with self.subTest(text=text):
                self.pr(comments=[self.marker(text=text)])
                self.assertRefused(self.merge(), "review")

    def test_a_push_after_the_review_needs_a_new_review(self):
        self.pr(headRefOid=OLD_OID.replace("fe", "ab", 1), comments=[self.marker()])  # marker is for OID, head moved
        self.assertRefused(self.merge(), "review")

    def test_review_marker_in_any_of_the_comments(self):
        self.pr(comments=[{"author": {"login": "x"}, "body": "ok"}, self.marker()])
        self.assertEqual(self.merge().returncode, 0)

    def test_refuses_when_the_head_moves_between_the_checks_and_the_merge(self):
        self.pr(head_after=OLD_OID)
        r = self.merge()
        self.assertRefused(r, "head")

    def test_refuses_when_the_gh_login_cannot_be_read(self):
        r = self.run_script("away-merge", "--project", "shop", "--pr", 7, "--item", "x", env={"GH_LOGIN": ""})
        self.assertEqual(r.returncode, 1)
        self.assertIn("refused (review)", r.stderr)

    def test_refuses_red_ci_without_local_ok(self):
        self.pr(statusCheckRollup=[{"name": "unittest", "status": "COMPLETED", "conclusion": "FAILURE"}])
        r = self.merge()
        self.assertRefused(r, "ci")
        self.assertIn("unittest: failure", r.stderr)
        self.pr(statusCheckRollup=[{"name": "unittest", "status": "IN_PROGRESS", "conclusion": ""}])
        self.assertRefused(self.merge(), "ci")
        self.pr(statusCheckRollup=[])
        self.assertRefused(self.merge(), "ci")

    def test_red_ci_with_local_suite_ok_merges_and_says_so(self):
        self.pr(statusCheckRollup=[{"name": "unittest", "status": "COMPLETED", "conclusion": "FAILURE"}])
        r = self.merge("--local-suite-ok", "heavy-run-42")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("heavy-run-42", r.stdout)
        self.assertEqual(len(self.merged()), 1)

    def test_refuses_secret_hit_builtin(self):
        for line in ("+key = 'ghp_" + "a" * 36 + "'", "+path = '/ho" + "me/u/code/x'", "+host = '192.168.1.20'",
                     "+-----BEGIN OPENSSH PRIVATE KEY-----"):
            with self.subTest(line=line):
                self.pr(diff=CLEAN_DIFF + line + "\n")
                self.assertRefused(self.merge(), "secrets")

    def test_builtin_scan_ignores_removed_and_context_lines(self):
        self.pr(diff=CLEAN_DIFF + "-path = '/ho" + "me/u/x'\n path = '/ho" + "me/u/y'\n")
        self.assertEqual(self.merge().returncode, 0)

    def test_refuses_secret_hit_gitleaks(self):
        (self.shop / ".gitleaks.toml").write_text("title = 'x'\n")
        self.pr(diff=CLEAN_DIFF + "+LEAKME\n")
        self.assertRefused(self.merge(), "secrets")
        self.assertIn("detect", (self.tmp / "gitleaks.log").read_text())
        self.pr(diff=CLEAN_DIFF)
        self.assertEqual(self.merge().returncode, 0)

    def test_gitleaks_config_without_gitleaks_refuses(self):
        (self.shop / ".gitleaks.toml").write_text("title = 'x'\n")
        (self.bin / "gitleaks").unlink()
        import shutil
        # only the stubs, python and git: a real gitleaks must not be found
        self.env["PATH"] = ":".join([str(self.bin), str(Path(sys.executable).parent), str(Path(shutil.which("git")).parent)])
        self.assertIsNone(shutil.which("gitleaks", path=self.env["PATH"]))
        self.assertRefused(self.merge(), "secrets")

    def test_all_skipped_checks_are_not_green(self):
        self.pr(statusCheckRollup=[{"name": "a", "status": "COMPLETED", "conclusion": "SKIPPED"}])
        self.assertRefused(self.merge(), "ci")
        self.pr(statusCheckRollup=[{"name": "a", "status": "COMPLETED", "conclusion": "SKIPPED"},
                                   {"name": "b", "status": "COMPLETED", "conclusion": "SUCCESS"}])
        self.assertEqual(self.merge().returncode, 0)

    def test_deny_paths_come_from_the_diff_not_the_file_list(self):
        # review M3: gh's file list stops at 100 files and gives only the new name of a rename
        many = "".join(f"diff --git a/f{i}.py b/f{i}.py\n--- a/f{i}.py\n+++ b/f{i}.py\n+x\n" for i in range(120))
        late = "diff --git a/db/migrations/0042.sql b/db/migrations/0042.sql\n--- /dev/null\n+++ b/db/migrations/0042.sql\n+x\n"
        self.pr(files=[{"path": f"f{i}.py"} for i in range(100)], diff=many + late)
        self.assertRefused(self.merge(), "deny")
        rename = ("diff --git a/migrations/x.sql b/other/x.sql\nsimilarity index 100%\n"
                  "rename from migrations/x.sql\nrename to other/x.sql\n")
        self.pr(files=[{"path": "other/x.sql"}], diff=rename)
        r = self.merge()
        self.assertRefused(r, "deny")
        self.assertIn("migrations/x.sql", r.stderr)
        quoted = 'diff --git "a/infra/caf\\303\\251.tf" "b/infra/caf\\303\\251.tf"\n--- "a/infra/caf\\303\\251.tf"\n'
        self.pr(files=[], diff=quoted)
        self.assertRefused(self.merge(), "deny")

    def test_a_diff_with_no_readable_paths_refuses(self):
        self.pr(files=[{"path": "x.py"}], diff="binary junk\n")
        self.assertRefused(self.merge(), "deny")

    @staticmethod
    def diff_of(*paths):
        return "".join(f"diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n+x\n" for p in paths)

    def test_refuses_deny_path(self):
        self.pr(diff=self.diff_of("x.py", "app/db/migrations/0002_add.sql"))
        r = self.merge()
        self.assertRefused(r, "deny")
        self.assertIn("migrations", r.stderr)
        self.pr(diff=self.diff_of("migrations/0001.sql"))
        self.assertRefused(self.merge(), "deny")
        self.pr(diff=self.diff_of("infra/main.tf"))
        self.assertRefused(self.merge(), "deny")
        self.pr(diff=self.diff_of("infra/sub/main.tf", "docs/migrations.md"))  # `*` stays in one directory
        self.assertEqual(self.merge().returncode, 0)

    def test_one_line_per_failed_check(self):
        self.pr(comments=[], statusCheckRollup=[], diff=self.diff_of("infra/x") + "+x = '/ho" + "me/u/a'\n")
        r = self.merge()
        self.assertEqual(r.returncode, 1)
        lines = [l for l in r.stderr.splitlines() if l.startswith("away-merge: refused")]
        self.assertEqual(len(lines), 4, r.stderr)  # review, ci, secrets, deny
        self.assertEqual(self.merged(), [])

    def test_refuses_a_closed_pr(self):
        self.pr(state="MERGED")
        self.assertRefused(self.merge(), "open")

    def test_unreadable_pr_refuses(self):
        r = self.merge(pr=99)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.merged(), [])

    def test_usage(self):
        self.assertEqual(self.run_script("away-merge").returncode, 2)
        self.assertEqual(self.run_script("away-merge", "--project", "shop", "--pr", "7", "--item", "  ").returncode, 2)

    def test_only_read_commands_besides_the_merge(self):
        self.merge()
        for c in self.gh_calls():
            self.assertTrue(c.startswith(("pr view", "pr diff", "pr merge", "api user -q")), c)


if __name__ == "__main__":
    unittest.main()
