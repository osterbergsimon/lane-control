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
if a[:2] == ["pr", "list"]:
    print("[]"); sys.exit(0)
pr = prs.get(a[2]) if a[:1] == ["pr"] and len(a) > 2 else None
if pr is None:
    print("gh stub: no such PR / not modelled: " + " ".join(a), file=sys.stderr); sys.exit(1)
if a[:2] == ["pr", "diff"]:
    print(pr.get("diff", "")); sys.exit(0)
if a[:2] == ["pr", "view"] and "mergeCommit" in a:
    print(pr.get("merge_oid", "")); sys.exit(0)
if a[:2] == ["pr", "view"]:
    print(json.dumps({k: pr[k] for k in ("baseRefName", "state", "body", "comments", "statusCheckRollup", "files")})); sys.exit(0)
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


class AwayMergeTest(ImproveTest):
    def setUp(self):
        super().setUp()
        write_exec(self.bin / "gh", GH_PR_STUB)
        write_exec(self.bin / "gitleaks", GITLEAKS_STUB)
        self.env.update(GH_PRS=str(self.tmp / "prs.json"), GITLEAKS_LOG=str(self.tmp / "gitleaks.log"))
        self.assertEqual(self.on("--project", "shop", "--deny-paths", "**/migrations/**,infra/*").returncode, 0)
        self.branch = self.state()["branches"]["shop"]
        self.prs = {}
        self.pr()

    def pr(self, n=7, **over):
        pr = {"baseRefName": self.branch, "state": "OPEN", "body": "Adds x.\n\nReview: passed\n", "comments": [],
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
        self.assertEqual(self.merged(), ["pr merge 7 --merge --subject away: Add the x feature"])
        self.assertIn("abc1234", r.stdout)
        self.assertNotIn("--squash", " ".join(self.gh_calls()))
        self.assertNotIn("--rebase", " ".join(self.gh_calls()))

    def test_refuses_integration_base(self):
        for base in ("main", "develop"):
            self.pr(baseRefName=base)
            self.assertRefused(self.merge(), "base")

    def test_refuses_other_project_branch(self):
        self.pr(baseRefName="away/2026-01-01")
        self.assertRefused(self.merge(), "base")
        # the other project's own branch is another project's: shop's coordinator may not merge into it
        self.assertEqual(self.on("--project", "shop", "--project", "blog").returncode, 0)
        self.branch = self.state()["branches"]["shop"]
        self.assertNotEqual(self.branch, self.state()["branches"]["blog"])
        self.pr(baseRefName=self.state()["branches"]["blog"])
        self.assertRefused(self.merge(), "base")

    def test_refuses_when_improve_off(self):
        self.run_script("lane-away", "off")
        self.assertRefused(self.merge(), "improve")
        self.assertEqual(self.gh_calls(), [])  # not even read

    def test_refuses_project_not_in_the_mode(self):
        self.assertRefused(self.merge(project="blog"), "improve")

    def test_refuses_without_review(self):
        self.pr(body="Adds x.\n")
        self.assertRefused(self.merge(), "review")
        self.pr(body="Adds x.\nReview: passed but later reverted\n")
        self.assertRefused(self.merge(), "review")

    def test_review_marker_in_a_comment(self):
        self.pr(body="Adds x.\n", comments=[{"body": "ok"}, {"body": "Review: passed"}])
        self.assertEqual(self.merge().returncode, 0)

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
        for line in ("+key = 'ghp_" + "a" * 36 + "'", "+path = '/home/someone/code/x'", "+host = '192.168.1.20'",
                     "+-----BEGIN OPENSSH PRIVATE KEY-----"):
            with self.subTest(line=line):
                self.pr(diff=CLEAN_DIFF + line + "\n")
                self.assertRefused(self.merge(), "secrets")

    def test_builtin_scan_ignores_removed_and_context_lines(self):
        self.pr(diff=CLEAN_DIFF + "-path = '/home/someone/x'\n path = '/home/someone/y'\n")
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

    def test_refuses_deny_path(self):
        self.pr(files=[{"path": "x.py"}, {"path": "app/db/migrations/0002_add.sql"}])
        r = self.merge()
        self.assertRefused(r, "deny")
        self.assertIn("migrations", r.stderr)
        self.pr(files=[{"path": "migrations/0001.sql"}])
        self.assertRefused(self.merge(), "deny")
        self.pr(files=[{"path": "infra/main.tf"}])
        self.assertRefused(self.merge(), "deny")
        self.pr(files=[{"path": "infra/sub/main.tf"}, {"path": "docs/migrations.md"}])  # `*` stays in one directory
        self.assertEqual(self.merge().returncode, 0)

    def test_one_line_per_failed_check(self):
        self.pr(body="", statusCheckRollup=[], diff=CLEAN_DIFF + "+x = '/home/someone/a'\n",
                files=[{"path": "infra/x"}])
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
            self.assertTrue(c.startswith(("pr view", "pr diff", "pr merge")), c)


if __name__ == "__main__":
    unittest.main()
