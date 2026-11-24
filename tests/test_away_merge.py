"""core/scripts/away-merge: the only merge route into a project's away branch. gh, git and gitleaks are stubs or temp
repos; nothing merges anywhere real."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lane_away import GH_SUMMARY_BLOCK, ImproveTest, git  # noqa: E402
from test_machine_setup import write_exec  # noqa: E402

GH_PR_STUB = """
#!/usr/bin/env python3
# gh stand-in for away-merge: `pr view N --json ...`, `pr diff N`, `pr merge N ...` from $GH_PRS (JSON {N: {...}}).
import json, os, sys, time
a = sys.argv[1:]
open(os.environ["GH_LOG"], "a").write(" ".join(a).replace("\\n", " / ") + "\\n")
""" + GH_SUMMARY_BLOCK + """
prs = json.load(open(os.environ["GH_PRS"]))
if a[:2] == ["repo", "view"]:
    here = os.path.basename(os.getcwd())
    if here in os.environ.get("GH_VIS_FAIL", "").split(","):
        print("gh: could not resolve repository", file=sys.stderr); sys.exit(1)
    print("PRIVATE" if here in os.environ.get("GH_PRIVATE", "").split(",") else "PUBLIC"); sys.exit(0)
if a[:2] == ["api", "user"]:
    login = os.environ.get("GH_LOGIN", "")
    if not login:
        print("gh: not logged in", file=sys.stderr); sys.exit(1)
    print(login); sys.exit(0)
if a[:2] == ["pr", "list"]:
    print("[]"); sys.exit(0)
if a[:2] == ["api", "graphql"]:
    n = [x.split("=", 1)[1] for x in a if x.startswith("number=")][0]
    if os.environ.get("GH_EDITED_FAIL"):
        print("gh: " + os.environ["GH_EDITED_FAIL"], file=sys.stderr); sys.exit(1)
    print(json.dumps({"data": {"repository": {"pullRequest": {"lastEditedAt": prs[n].get("lastEditedAt")}}}})); sys.exit(0)
pr = prs.get(a[2]) if a[:1] == ["pr"] and len(a) > 2 else None
if pr is None:
    print("gh stub: no such PR / not modelled: " + " ".join(a), file=sys.stderr); sys.exit(1)
if a[:2] == ["pr", "diff"]:
    print(pr.get("diff", "")); sys.exit(0)
if a[:2] == ["pr", "view"] and "state,mergeCommit" in a:  # the one look after a failed `pr merge`
    print(json.dumps({"state": os.environ.get("GH_STATE_AFTER", "MERGED"), "mergeCommit": {"oid": pr.get("merge_oid", "")}})); sys.exit(0)
if a[:2] == ["pr", "view"] and "mergeCommit" in a:
    if os.environ.get("GH_SHA_FAIL"):
        print("gh: " + os.environ["GH_SHA_FAIL"], file=sys.stderr); sys.exit(1)
    print(pr.get("merge_oid", "")); sys.exit(0)
if a[:2] == ["pr", "view"] and a[a.index("--json") + 1] == "headRefOid":
    print(json.dumps({"headRefOid": pr.get("head_after", pr["headRefOid"])})); sys.exit(0)
if a[:2] == ["pr", "view"]:
    print(json.dumps({k: pr[k] for k in ("baseRefName", "headRefOid", "state", "body", "comments", "statusCheckRollup", "files")}
                     | {"isDraft": pr.get("isDraft", False)})); sys.exit(0)
if a[:2] == ["pr", "ready"]:
    if os.environ.get("GH_READY_FAIL"):
        print("gh: " + os.environ["GH_READY_FAIL"], file=sys.stderr); sys.exit(1)
    pr["isDraft"] = False
    prs[a[2]] = pr
    json.dump(prs, open(os.environ["GH_PRS"], "w")); sys.exit(0)
if a[:2] == ["pr", "merge"]:
    if "--body" in a:
        print("gh stub: a body must go through --body-file", file=sys.stderr); sys.exit(1)
    msg = open(a[a.index("--body-file") + 1], encoding="utf-8").read() if "--body-file" in a else None
    json.dump(a + ["--body", msg], open(os.environ["GH_PRS"] + ".merge", "w"))
    if pr.get("isDraft"):  # GitHub refuses to merge a draft
        print("gh: Pull request is still a draft", file=sys.stderr); sys.exit(1)
    time.sleep(float(os.environ.get("GH_MERGE_SLEEP", "0")))  # a slow merge: lets a second away-merge run beside it
    if os.environ.get("GH_MERGE_FAIL"):  # gh itself failed (a timeout, a 5xx); GH_STATE_AFTER says what GitHub did
        print("gh: " + os.environ["GH_MERGE_FAIL"], file=sys.stderr); sys.exit(1)
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

ENTRY = (
    "## What\nAdds x.\n\n## Why\nUsers asked.\n\n## Builds on\nnothing\n\n## Existing work checked\nLooked in lib/ and the "
    "issues: no x.\n\n## How to try it\nRun `x --help`.\n\n## How to drop it\nRevert the merge commit.\n\n## Tests\nSuite green.\n")
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
        pr = {"baseRefName": self.branch, "headRefOid": OID, "state": "OPEN", "body": "Adds x.\n\n" + ENTRY,
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
        self.assertEqual([c.split(" --body-file ")[0] for c in self.merged()], ["pr merge 7 --merge --subject away: Add the x feature"])
        self.assertTrue(self.merged()[0].endswith(f"--match-head-commit {OID}"))
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

    def test_refuses_a_branch_that_is_not_an_away_branch(self):
        # review M7: improve.json is a plain file; a branch it names must still be an away/<date> branch, and never the
        # project's integration branch
        for branches, integration in (({"shop": "main"}, {"shop": "main"}), ({"shop": "develop"}, {"shop": "main"}),
                                      ({"shop": "away/2026-11-23"}, {"shop": "away/2026-11-23"}),
                                      ({"shop": "away/x"}, {"shop": "main"}), ({"shop": "away/2026-11-23/../main"}, {"shop": "main"})):
            with self.subTest(branches=branches, integration=integration):
                st = self.state()
                st["branches"], st["integration"] = branches, integration
                self.improve_file().write_text(json.dumps(st))
                self.pr(baseRefName=branches["shop"])
                r = self.merge()
                self.assertRefused(r, "improve")
                self.assertIn("away", r.stderr)

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

    RED = [{"name": "unittest", "status": "COMPLETED", "conclusion": "FAILURE"}]

    def suite_log(self, text="Ran 1530 tests in 412.0s\n\nOK (skipped=9)\n"):
        f = Path(self.tmp) / "suite.log"
        f.write_text(text)
        return str(f)

    def test_red_ci_with_local_suite_ok_merges_and_says_so(self):
        self.pr(statusCheckRollup=self.RED)
        log = self.suite_log()
        r = self.merge("--local-suite-ok", log)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(log, r.stdout)
        self.assertIn("OK (skipped=9)", r.stdout)  # the log's last line is printed, so the PR body's Tests section can quote it
        self.assertEqual(len(self.merged()), 1)

    def test_local_suite_ok_must_be_a_real_log_with_a_passing_end(self):
        # lc-372: any string used to bypass red CI
        self.pr(statusCheckRollup=self.RED)
        for ref in ("heavy-run-42", "yes", "", str(Path(self.tmp) / "missing.log"), str(self.tmp),
                    self.suite_log(""), self.suite_log("Ran 3 tests in 0.1s\n\nFAILED (failures=1)\n")):
            with self.subTest(ref=ref):
                self.assertRefused(self.merge("--local-suite-ok", ref), "ci")
        self.assertEqual(self.merged(), [])

    def test_refuses_secret_hit_builtin(self):
        for line in ("+key = 'ghp_" + "a" * 36 + "'", "+path = '/ho" + "me/u/code/x'", "+host = '192.168.1.20'",
                     "+-----BEGIN OPENSSH PRIVATE KEY-----"):
            with self.subTest(line=line):
                self.pr(diff=CLEAN_DIFF + line + "\n")
                self.assertRefused(self.merge(), "secrets")

    def test_builtin_scan_ignores_removed_and_context_lines(self):
        self.pr(diff=CLEAN_DIFF + "-path = '/ho" + "me/u/x'\n path = '/ho" + "me/u/y'\n")
        self.assertEqual(self.merge().returncode, 0)

    # --- public-repo names (lc-1dk): hostnames, usernames and project names, from the machine and a file outside the repo ---
    def names_file(self, text, name="private-names.txt"):
        f = self.conf_dir / name
        f.write_text(text)
        return f

    def test_a_listed_name_in_an_added_line_refuses_with_file_and_line_but_without_the_name(self):
        self.names_file("example-host\n")
        self.pr(diff=CLEAN_DIFF.replace("+print", "@@ -1,2 +10,3 @@\n keep\n+print") + "+ssh Example-Host\n")
        r = self.merge()
        self.assertRefused(r, "names")
        self.assertIn("a listed name at x.py:12", r.stderr)
        self.assertNotIn("example-host", r.stderr.lower())
        self.assertIn("ask the owner", r.stderr)

    def test_only_private_projects_names_count_and_the_own_name_never_does(self):
        self.pr(diff=CLEAN_DIFF + "+# copied from blog, see the shop docs\n")
        self.assertEqual(self.merge().returncode, 0)  # blog is a public repo
        self.env["GH_PRIVATE"] = "blog"
        r = self.merge()
        self.assertRefused(r, "names")
        self.assertIn("a project name", r.stderr)
        self.pr(diff=CLEAN_DIFF + "+# see the shop docs\n")
        self.assertEqual(self.merge().returncode, 0)
        self.names_file("shop\n")  # the own name in the names file is no secret either
        self.assertEqual(self.merge().returncode, 0)

    def test_the_machines_own_hostname_and_username_refuse(self):
        import getpass
        import socket
        sys.path.insert(0, str(ROOT / "core"))
        import privatenames
        hit = False
        for kind, name in (("a hostname", socket.gethostname().split(".")[0]), ("a username", getpass.getuser())):
            if name.lower() in privatenames.GENERIC or len(name) < privatenames.MIN_MACHINE_LEN:
                continue
            hit = True
            with self.subTest(kind=kind):
                self.pr(diff=CLEAN_DIFF + f"+ssh {name}\n")
                r = self.merge()
                self.assertRefused(r, "names")
                self.assertIn(kind, r.stderr)
                self.assertNotIn(name, r.stderr, msg=f"the refusal printed {kind}")
        if not hit:
            self.skipTest("this machine's hostname and user are both short or common words")

    def test_names_in_removed_and_context_lines_and_longer_words_pass(self):
        self.names_file("example-host\n")
        self.pr(diff=CLEAN_DIFF.replace("+print", "@@ -1,2 +1,3 @@\n+print") +
                "-ssh example-host\n ssh example-host\n+example-hosting and blogger\n")
        self.assertEqual(self.merge().returncode, 0)

    def test_a_name_in_a_file_path_or_a_rename_refuses_and_the_path_is_masked(self):
        self.names_file("example-host\n")
        new = "diff --git a/hosts/example-host.nix b/hosts/example-host.nix\nnew file mode 100644\n--- /dev/null\n+++ b/hosts/example-host.nix\n+x = 1\n"
        ren = ("diff --git a/a.md b/example-host.md\nsimilarity index 100%\nrename from a.md\nrename to example-host.md\n")
        for diff in (new, ren):
            with self.subTest(diff=diff[:40]):
                self.pr(diff=diff, files=[{"path": "x"}])
                r = self.merge()
                self.assertRefused(r, "names")
                self.assertIn("a listed name in the path ", r.stderr)
                self.assertIn("***", r.stderr)
                self.assertNotIn("example-host", r.stderr.lower())

    def test_a_name_inside_a_longer_word_is_masked_in_every_printed_path(self):
        self.names_file("zebrabox\n")
        diffs = (
            "diff --git a/docs/zebraboxnotes.md b/docs/zebraboxnotes.md\n--- a/docs/zebraboxnotes.md\n+++ b/docs/zebraboxnotes.md\n@@ -1 +1 @@\n+ssh zebrabox\n",
            "diff --git a/zebrabox/myzebrabox.nix b/zebrabox/myzebrabox.nix\nnew file mode 100644\n--- /dev/null\n+++ b/zebrabox/myzebrabox.nix\n@@ -0,0 +1 @@\n+x = 1\n",
            "diff --git a/hosts/connectToZebrabox.nix b/hosts/connectToZebrabox.nix\n--- a/hosts/connectToZebrabox.nix\n+++ b/hosts/connectToZebrabox.nix\n@@ -1 +1 @@\n+ssh zebrabox\n",
        )
        for diff in diffs:
            with self.subTest(diff=diff[:50]):
                self.pr(diff=diff, files=[{"path": "x"}])
                r = self.merge()
                self.assertRefused(r, "names")
                self.assertNotIn("zebrabox", r.stderr.lower(), msg="a refusal printed the name")
                self.assertIn("***", r.stderr)

    def test_only_new_paths_are_checked_not_deleted_old_or_edited_ones(self):
        self.names_file("example-host\n")
        diffs = (
            "diff --git a/hosts/example-host.nix b/hosts/example-host.nix\ndeleted file mode 100644\n--- a/hosts/example-host.nix\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n",
            "diff --git a/example-host.md b/notes.md\nsimilarity index 100%\nrename from example-host.md\nrename to notes.md\n",
            "diff --git a/hosts/example-host.nix b/hosts/example-host.nix\n--- a/hosts/example-host.nix\n+++ b/hosts/example-host.nix\n@@ -1 +1 @@\n+x = 2\n",
        )
        for diff in diffs:
            with self.subTest(diff=diff[:50]):
                self.pr(diff=diff, files=[{"path": "x"}])
                r = self.merge()
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_an_added_line_that_starts_with_plus_plus_is_still_scanned(self):
        self.names_file("example-host\n")
        self.pr(diff=CLEAN_DIFF.replace("+print", "@@ -1 +1,2 @@\n+print") + "++ example-host\n+++ example-host\n")
        r = self.merge()
        self.assertRefused(r, "names")
        self.assertIn("x.py:2", r.stderr)
        self.assertIn("x.py:3", r.stderr)

    def test_the_merge_says_what_the_name_scan_checked(self):
        self.pr()
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("name scan", r.stdout)
        self.assertIn("no private-names file", r.stdout)
        self.names_file("example-host\nsecond-name\n")
        self.pr()
        r = self.merge()
        self.assertIn("2 from the private-names file", r.stdout)

    def test_a_private_repo_is_not_name_checked_and_the_merge_says_so(self):
        self.names_file("example-host\n")
        self.env["GH_PRIVATE"] = "shop"
        self.pr(diff=CLEAN_DIFF + "+ssh example-host\n")
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("name scan skipped", r.stdout)
        self.assertIn("private", r.stdout)

    def test_a_repo_whose_visibility_cannot_be_read_refuses(self):
        self.env["GH_VIS_FAIL"] = "shop"
        r = self.merge()
        self.assertRefused(r, "names")
        self.assertIn("visibility", r.stderr)

    def test_another_project_whose_visibility_cannot_be_read_counts_as_private_and_is_said(self):
        self.env["GH_VIS_FAIL"] = "blog"
        self.pr(diff=CLEAN_DIFF + "+# copied from blog\n")
        self.assertRefused(self.merge(), "names")
        self.pr()
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("could not be read", r.stdout)

    def test_the_env_var_names_the_file_and_a_missing_one_refuses(self):
        other = self.names_file("elsewhere-host\n", "other.txt")
        self.env["LANE_PRIVATE_NAMES"] = str(other)
        self.pr(diff=CLEAN_DIFF + "+elsewhere-host\n")
        self.assertRefused(self.merge(), "names")
        self.env["LANE_PRIVATE_NAMES"] = str(self.conf_dir / "gone.txt")
        self.pr()
        r = self.merge()
        self.assertRefused(r, "names")
        self.assertIn("cannot be read", r.stderr)

    def test_a_gitleaks_project_gets_the_name_check_after_gitleaks(self):
        (self.shop / ".gitleaks.toml").write_text("title = 'x'\n")
        self.names_file("example-host\n")
        self.pr(diff=CLEAN_DIFF + "+ssh example-host\n")
        r = self.merge()
        self.assertRefused(r, "names")
        self.assertIn("detect", (self.tmp / "gitleaks.log").read_text())
        self.pr(diff=CLEAN_DIFF + "+LEAKME example-host\n")
        r = self.merge()
        self.assertIn("refused (secrets)", r.stderr)
        self.assertIn("refused (names)", r.stderr)
        self.pr()
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("name scan", r.stdout)

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
            self.assertTrue(c.startswith(("pr view", "pr diff", "pr merge", "pr ready", "repo view", "api user -q", "api graphql", "pr list --head", "pr edit",
                                          "pr create --draft")), c)


class AwayMergeEntryTests(AwayMergeTest):
    """The item's log entry lives in its PR body; away-merge copies it into the merge commit's message and refuses a body
    without it (AWF-1)."""

    def merge_args(self) -> list:
        return json.loads((self.tmp / "prs.json.merge").read_text())

    def merge_body(self) -> str:
        a = self.merge_args()
        return a[a.index("--body") + 1]

    def test_the_merge_message_body_is_the_pr_line_then_the_entry(self):
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        a = self.merge_args()
        self.assertEqual(a[a.index("--subject") + 1], "away: Add the x feature")
        body = self.merge_body()
        self.assertTrue(body.startswith("Pull request #7\n\n## What\nAdds x.\n"), body)
        self.assertIn("## Tests\nSuite green.", body)
        self.assertNotRegex(body, r"(?m)^Pull request #7\n.*Pull request #7")
        self.assertIn("--match-head-commit", a)

    def test_text_outside_the_entry_headings_is_not_copied(self):
        self.pr(body="Intro that is only for reviewers.\n\n" + ENTRY + "\n## Also noticed\nunrelated filing\n")
        self.assertEqual(self.merge().returncode, 0)
        self.assertNotIn("Intro", self.merge_body())
        self.assertNotIn("unrelated", self.merge_body())

    def test_refuses_a_body_without_the_entry_and_names_what_is_missing(self):
        self.pr(body=ENTRY.replace("## Why\nUsers asked.\n\n", "").replace("Suite green.", ""))
        r = self.merge()
        self.assertRefused(r, "entry")
        line = [l for l in r.stderr.splitlines() if "refused (entry)" in l]
        self.assertEqual(len(line), 1, r.stderr)
        self.assertIn("Why", line[0])
        self.assertIn("Tests", line[0])
        self.assertNotIn("Builds on", line[0])  # only what is missing

    def test_refuses_an_empty_body(self):
        self.pr(body="")
        self.assertRefused(self.merge(), "entry")

    def test_the_summary_pr_lists_the_item_from_its_merge_message_not_a_file(self):
        # the stub merges nothing in git: the list is read from the branch's merge commits, so seed one as GitHub would make it
        work = self.tmp / "work-shop"
        subprocess.run(["git", "clone", "-q", str(self.tmp / "shop.git"), str(work)], check=True, capture_output=True)
        git(work, "checkout", "-q", "-b", "item", "origin/" + self.branch)
        (work / "x.txt").write_text("x\n")
        git(work, "add", "x.txt")
        git(work, "commit", "-qm", "the x feature")
        git(work, "checkout", "-q", self.branch)
        git(work, "merge", "-q", "--no-ff", "-m", "away: Add the x feature\n\nPull request #7\n\n" + ENTRY, "item")
        git(work, "push", "-q", "origin", self.branch)
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        body = self.summary_prs(self.shop)[str(self.state()["prs"]["shop"])]["body"]
        self.assertIn("Add the x feature (PR #7", body)
        self.assertIn("Adds x.", body)
        self.assertIn("Suite green.", body)


class AwayMergeEntryScanTests(AwayMergeTest):
    """R1-01: what away-merge publishes itself (the entry in the merge message, the --item title) goes through the same scans
    as the diff, before anything is merged; and the body is pinned to the review."""

    def entry_with(self, section, text):
        return ENTRY.replace({"Tests": "Suite green.", "How to try it": "Run `x --help`."}[section], text)

    def test_a_home_path_in_the_entry_refuses(self):
        self.pr(body=self.entry_with("How to try it", "cd /ho" + "me/u/code/x && run"))
        r = self.merge()
        self.assertRefused(r, "entry")
        self.assertIn("the --item title or the PR body entry adds a home path", r.stderr)

    def test_a_token_in_the_entry_refuses_and_is_not_printed(self):
        tok = "ghp_" + "a" * 36
        self.pr(body=self.entry_with("Tests", f"ran with {tok}"))
        r = self.merge()
        self.assertRefused(r, "entry")
        self.assertIn("token", r.stderr)
        self.assertNotIn(tok, r.stderr)

    def test_a_listed_name_in_the_entry_refuses_without_printing_it(self):
        self.names_file("example-host\n")
        self.pr(body=self.entry_with("Tests", "ssh Example-Host"))
        r = self.merge()
        self.assertRefused(r, "entry")
        self.assertIn("a listed name", r.stderr)
        self.assertNotIn("example-host", r.stderr.lower())

    def test_a_private_project_name_in_the_item_title_refuses(self):
        self.env["GH_PRIVATE"] = "blog"
        r = self.merge(item="Port the blog importer")
        self.assertRefused(r, "entry")
        self.assertIn("item-title", r.stderr)

    def test_a_secret_in_the_item_title_refuses(self):
        r = self.merge(item="Use key AKIA" + "A" * 16)
        self.assertRefused(r, "entry")

    def test_text_in_a_comment_or_outside_the_entry_is_neither_scanned_nor_published(self):
        self.pr(body=ENTRY + "\n## Also noticed\ncd /ho" + "me/u/x\n<!-- ssh /ho" + "me/u/y -->\n")
        self.assertEqual(self.merge().returncode, 0)
        self.assertNotIn("/ho" + "me/", (self.tmp / "prs.json.merge").read_text())

    def test_a_clean_entry_still_merges(self):
        self.assertEqual(self.merge().returncode, 0)

    def stamped(self, edited, review_at="2026-11-24T10:00:00Z", **extra):
        c = self.marker()
        c["createdAt"] = review_at
        self.pr(comments=[c], lastEditedAt=edited, **extra)

    def test_a_body_never_edited_merges(self):
        self.stamped(None)
        self.assertEqual(self.merge().returncode, 0)

    def test_a_body_edited_before_the_review_merges(self):
        self.stamped("2026-11-24T09:59:59Z")
        self.assertEqual(self.merge().returncode, 0)

    def test_a_body_edited_after_the_review_refuses_and_says_so(self):
        self.stamped("2026-11-24T10:00:01Z")
        r = self.merge()
        self.assertRefused(r, "entry")
        self.assertIn("edited at 2026-11-24T10:00:01Z", r.stderr)
        self.assertIn("new `Review: passed", r.stderr)

    def test_a_new_review_after_the_edit_lets_it_merge(self):
        old, new = self.marker(), self.marker()
        old["createdAt"], new["createdAt"] = "2026-11-24T09:00:00Z", "2026-11-24T11:00:00Z"
        self.pr(comments=[old, new], lastEditedAt="2026-11-24T10:00:00Z")
        self.assertEqual(self.merge().returncode, 0)

    def test_an_edit_time_that_cannot_be_read_refuses(self):
        self.stamped(None)
        (self.tmp / "gh.log").write_text("")
        r = self.run_script("away-merge", "--project", "shop", "--pr", "7", "--item", "Add the x feature",
                            env={"GH_EDITED_FAIL": "HTTP 502"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot be read", r.stderr)
        self.assertEqual(self.merged(), [])

    def test_an_edit_with_no_readable_review_time_refuses(self):
        self.pr(lastEditedAt="2026-11-24T10:00:00Z")  # the marker has no createdAt
        self.assertRefused(self.merge(), "entry")

    def test_a_large_non_ascii_entry_goes_through_a_file_not_argv(self):
        big = ENTRY.replace("Adds x.", "Adds " + "\u00e9" * 70000)
        self.pr(body=big)
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("--body ", " ".join(self.merged()))
        a = json.loads((self.tmp / "prs.json.merge").read_text())
        self.assertIn("\u00e9" * 70000, a[a.index("--body") + 1])

    def test_a_nul_in_the_body_is_dropped_not_a_traceback(self):
        self.pr(body=ENTRY.replace("Adds x.", "Adds\x00 x."))
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_the_unfilled_template_refuses_and_says_placeholder(self):
        self.pr(body=(ROOT / "core" / "templates" / "away-entry.md").read_text())
        r = self.merge()
        self.assertRefused(r, "entry")
        self.assertIn("placeholder", r.stderr)

    def test_a_missing_and_an_empty_heading_are_told_apart(self):
        self.pr(body=ENTRY.replace("## Why\nUsers asked.\n\n", "").replace("Suite green.", ""))
        r = self.merge()
        self.assertRefused(r, "entry")
        self.assertIn("no such heading: Why", r.stderr)
        self.assertIn("empty, or still the template's placeholder: Tests", r.stderr)
        self.assertIn("`#` heading at the start of a line", r.stderr)

    def test_a_fenced_shell_comment_does_not_cut_the_entry_in_the_merge_message(self):
        self.pr(body=ENTRY.replace("Run `x --help`.", "```bash\n# scratch first\nx --help\n```"))
        self.assertEqual(self.merge().returncode, 0)
        a = json.loads((self.tmp / "prs.json.merge").read_text())
        self.assertIn("# scratch first\nx --help\n```\n\n## How to drop it", a[a.index("--body") + 1])

    names_file = AwayMergeTests.names_file


class AwayMergeDraftTests(AwayMergeTest):
    """lc-509: GitHub refuses to merge a draft, so away-merge marks the item PR ready first; never the summary PR."""

    def calls(self):
        return [c.split(" --")[0] for c in self.gh_calls() if c.startswith(("pr ready", "pr merge"))]

    def test_a_draft_item_pr_is_marked_ready_then_merged(self):
        self.pr(isDraft=True)
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), ["pr ready 7", "pr merge 7"])
        self.assertIn("ready", r.stdout)

    def test_a_ready_item_pr_is_not_touched(self):
        self.pr(isDraft=False)
        self.assertEqual(self.merge().returncode, 0)
        self.assertEqual(self.calls(), ["pr merge 7"])

    def test_nothing_is_marked_ready_when_a_check_refuses(self):
        self.pr(isDraft=True, comments=[])
        self.assertRefused(self.merge(), "review")
        self.assertEqual(self.calls(), [])

    def test_a_merge_that_fails_after_ready_says_the_pr_stays_ready(self):
        self.pr(isDraft=True)
        (self.tmp / "gh.log").write_text("")
        r = self.run_script("away-merge", "--project", "shop", "--pr", "7", "--item", "Add the x feature",
                            env={"GH_MERGE_FAIL": "HTTP 405", "GH_STATE_AFTER": "OPEN"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("was marked ready before and stays ready", r.stderr)
        self.assertIn("ready", r.stdout)
        # a PR that was not a draft says nothing of the kind
        self.pr(isDraft=False)
        r = self.run_script("away-merge", "--project", "shop", "--pr", "7", "--item", "Add the x feature",
                            env={"GH_MERGE_FAIL": "HTTP 405", "GH_STATE_AFTER": "OPEN"})
        self.assertNotIn("stays ready", r.stderr)

    def test_a_ready_that_fails_refuses_the_merge(self):
        self.pr(isDraft=True)
        (self.tmp / "gh.log").write_text("")
        r = self.run_script("away-merge", "--project", "shop", "--pr", "7", "--item", "Add the x feature",
                            env={"GH_READY_FAIL": "HTTP 502"})
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("ready", r.stderr)
        self.assertEqual(self.merged(), [])

    def test_the_summary_pr_is_never_marked_ready_or_merged(self):
        # its base is the integration branch: the base check refuses it before anything is touched
        self.pr(n=8, isDraft=True, baseRefName="main")
        self.assertRefused(self.merge(pr=8), "base")
        self.assertEqual(self.calls(), [])
        # and were a PR recorded as the summary PR somehow based on the away branch, it is refused by its number
        st = self.state()
        st["prs"]["shop"] = 7
        self.improve_file().write_text(json.dumps(st))
        self.pr(isDraft=True)
        r = self.merge()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("summary PR", r.stderr)
        self.assertEqual(self.calls(), [])


class AwayMergeSummaryTests(AwayMergeTest):
    """After a merge the summary PR's body is rebuilt from the branch's merge commits (its summary kept) by away-merge itself, so the
    coordinator has nothing to remember. The summary PR is a draft and is never merged here (lc-2nz)."""

    def number(self) -> str:
        return str(self.state()["prs"]["shop"])

    def body(self) -> str:
        return self.summary_prs(self.shop)[self.number()]["body"]

    def test_a_merge_refreshes_the_summary_pr_and_keeps_the_coordinators_summary(self):
        f = self.tmp / "summary.md"
        f.write_text("- Done: the x feature\n")
        self.assertEqual(self.run_script("lane-improve", "pr", "shop", "--summary-file", str(f)).returncode, 0)
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Done: the x feature", self.body())
        self.assertIn("Items merged", self.body())
        self.assertNotIn("AWAY-LOG", self.body())
        self.assertIn(f"/pull/{self.number()}", r.stdout)

    def test_the_summary_pr_is_never_merged_and_stays_a_draft(self):
        self.merge()
        self.assertEqual(len(self.merged()), 1)
        self.assertTrue(self.summary_prs(self.shop)[self.number()]["draft"])
        self.assertNotRegex(" ".join(self.gh_calls()), r"pr (ready|close)")

    def test_a_refusal_leaves_the_summary_pr_alone(self):
        before = self.body()
        self.pr(comments=[])
        self.assertRefused(self.merge(), "review")
        self.assertEqual(self.body(), before)
        self.assertEqual([c for c in self.gh_calls() if c.startswith("pr edit")], [])

    def test_a_refresh_that_fails_does_not_undo_the_merge(self):
        r = self.run_script("away-merge", "--project", "shop", "--pr", "7", "--item", "Add the x feature",
                            env={"GH_EDIT_FAIL": "HTTP 502"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("lane-improve pr shop", r.stderr)
        self.assertIn("merged PR 7", r.stdout)

    def test_a_missing_summary_pr_is_opened_and_recorded(self):
        # as if it could not be opened at `on`: nothing recorded, nothing open
        st = self.state()
        st.pop("prs")
        self.improve_file().write_text(json.dumps(st))
        (self.tmp / "gh-summary.json").unlink()
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(self.number(), self.summary_prs(self.shop))
        self.assertIn("Items merged", self.body())
        self.assertTrue(self.summary_prs(self.shop)[self.number()]["draft"])

    def test_a_closed_summary_pr_is_left_alone(self):
        db = json.loads((self.tmp / "gh-summary.json").read_text())
        db["prs"][self.number()]["state"] = "CLOSED"
        (self.tmp / "gh-summary.json").write_text(json.dumps(db))
        before = self.body()
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("closed", r.stderr)
        self.assertEqual(self.body(), before)
        self.assertEqual([c for c in self.gh_calls() if c.startswith(("pr edit", "pr create"))], [])

    def test_a_merge_while_draining_refreshes_too(self):
        f = self.tmp / "summary.md"
        f.write_text("- Done: the x feature\n")
        self.run_script("lane-improve", "pr", "shop", "--summary-file", str(f))
        number = self.number()
        self.run_script("lane-improve", "off")
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Done: the x feature", self.summary_prs(self.shop)[number]["body"])


class AwayMergeCapTests(AwayMergeTest):
    """The caps (lc-2nz): items are counted from away-merge's own record, a cap reached ends the project's cycles like the
    end time does, and the project then wraps up (`lane-improve drained <project>`) while the mode stays on for the rest."""

    def setUp(self):
        super().setUp()
        self.playbook(60, 10, ceiling=70)
        self.assertEqual(self.on("--continue", "--max-items", "2", "--stop-at", "66").returncode, 0)

    def period(self) -> dict:
        return json.loads((self.conf_dir / "improve-period.json").read_text())

    def status(self) -> str:
        return self.run_script("lane-improve", "status").stdout

    def test_a_merge_is_recorded_by_away_merge_not_read_from_the_summary(self):
        r = self.merge(item="Add the x feature")
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = self.period()["merges"]["shop"]
        self.assertEqual([(m["pr"], m["item"], m["sha"]) for m in rec], [(7, "Add the x feature", "abc1234")])
        self.assertEqual(self.period()["period"], self.state()["started"])
        self.assertIn("items 1/2", self.status())

    def test_a_refused_merge_is_not_counted(self):
        self.pr(comments=[])
        self.assertRefused(self.merge(), "review")
        self.assertIn("items 0/2", self.status())

    def test_the_count_survives_a_continue_rerun_and_starts_over_in_a_new_period(self):
        self.merge()
        self.assertEqual(self.on("--continue", "--hours", "3").returncode, 0)
        self.assertIn("items 1/2", self.status())
        self.run_script("lane-improve", "off")
        self.run_script("lane-improve", "drained", "--all")
        self.drop_branches()
        self.assertEqual(self.on("--project", "shop", "--max-items", "2").returncode, 0)
        self.assertIn("items 0/2", self.status())

    def test_status_says_when_a_cap_is_reached(self):
        self.merge(item="one")
        self.assertNotIn("reached", self.status())
        self.merge(item="two")
        line = [l for l in self.status().splitlines() if l.strip().startswith("shop:")][0]
        self.assertIn("items 2/2", line)
        self.assertIn("reached", line)
        self.assertIn("lane-improve drained shop", line)

    def test_two_merges_at_once_cannot_overshoot_max_items(self):
        """lc-jpg: the count is read and the merge recorded under one lock, so with one slot left only one of two
        simultaneous away-merges lands (the other waits, reads the new count, and is refused)."""
        self.merge(item="one")  # 1 of 2 used: one slot left
        self.pr(8)
        (self.tmp / "gh.log").write_text("")
        env = dict(self.env, GH_MERGE_SLEEP="1.5")
        procs = [subprocess.Popen([str(ROOT / "core" / "scripts" / "away-merge"), "--project", "shop", "--pr", n, "--item",
                                   f"item {n}"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for n in ("7", "8")]
        outs = [p.communicate(timeout=60) for p in procs]
        codes = sorted(p.returncode for p in procs)
        self.assertEqual(codes, [0, 1], outs)
        self.assertEqual(len(self.merged()), 1, self.merged())
        refused = [err for _, err in outs if "refused" in err]
        self.assertEqual(len(refused), 1, outs)
        self.assertIn("--max-items 2", refused[0])
        self.assertEqual(len(self.period()["merges"]["shop"]), 2)
        self.assertIn("items 2/2", self.status())

    # --- M2: a gh failure after the merge must not leave the merge uncounted

    def test_a_failed_sha_lookup_after_the_merge_still_counts_it(self):
        r = self.run_script("away-merge", "--project", "shop", "--pr", "7", "--item", "one", env={"GH_SHA_FAIL": "HTTP 502"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("did not go through", r.stderr)
        self.assertIn("commit", r.stderr)  # says the merge commit is unknown
        self.assertEqual([(m["pr"], m["sha"]) for m in self.period()["merges"]["shop"]], [(7, "")])
        self.assertIn("items 1/2", self.status())

    def test_a_merge_call_that_failed_after_github_merged_is_counted(self):
        r = self.run_script("away-merge", "--project", "shop", "--pr", "7", "--item", "one",
                            env={"GH_MERGE_FAIL": "timeout", "GH_STATE_AFTER": "MERGED"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("merged", r.stdout + r.stderr)
        self.assertEqual([m["pr"] for m in self.period()["merges"]["shop"]], [7])
        self.assertIn("items 1/2", self.status())

    def test_a_merge_call_that_failed_and_did_not_merge_is_not_counted(self):
        r = self.run_script("away-merge", "--project", "shop", "--pr", "7", "--item", "one",
                            env={"GH_MERGE_FAIL": "HTTP 502", "GH_STATE_AFTER": "OPEN"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("did not go through", r.stderr)
        self.assertIn("items 0/2", self.status())

    def test_a_weekly_cap_is_reached_at_the_value(self):
        self.playbook(65, 10, ceiling=70)
        self.assertNotIn("reached", self.status())
        self.playbook(66, 10, ceiling=70)
        line = [l for l in self.status().splitlines() if l.strip().startswith("shop:")][0]
        self.assertIn("weekly 66/66", line)
        self.assertIn("reached", line)

    def test_an_unreadable_feed_shows_a_question_mark_and_is_no_cap_hit(self):
        self.playbook(None, None)
        line = [l for l in self.status().splitlines() if l.strip().startswith("shop:")][0]
        self.assertIn("weekly ?/66", line)
        self.assertNotIn("reached", line)

    def test_items_cap_is_a_hard_stop_in_away_merge(self):
        self.merge(item="one")
        self.merge(item="two")
        r = self.merge(item="three")
        self.assertRefused(r, "improve")
        self.assertIn("--max-items 2", r.stderr)
        self.assertIn("wrap up", r.stderr)
        self.assertIn("--summary-file", r.stderr)  # the step the refusal names carries the summary
        self.assertIn("items 2/2", self.status())  # the refused merge is not counted

    def test_the_weekly_cap_does_not_refuse_the_lane_that_is_running(self):
        self.playbook(66, 10, ceiling=70)  # weekly at the cap: the lane that was running when it was reached still lands
        self.assertEqual(self.merge(item="one").returncode, 0)

    def test_a_cap_reached_while_draining_is_a_hard_stop_too(self):
        self.merge(item="one")
        self.merge(item="two")
        self.run_script("lane-improve", "off")
        self.assertRefused(self.merge(item="three"), "improve")

    def test_drained_needs_a_reached_cap_while_the_mode_is_on(self):
        r = self.run_script("lane-improve", "drained", "shop")
        self.assertEqual(r.returncode, 1)
        self.assertIn("cap", r.stderr)
        self.assertEqual(self.run_script("lane-improve", "drained", "--all").returncode, 1)
        self.assertIn("improve: on", self.status())

    def test_a_project_that_hit_its_cap_wraps_up_and_the_mode_stays_on(self):
        self.merge(item="one")
        self.merge(item="two")
        r = self.run_script("lane-improve", "drained", "shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("improve: on", self.status())
        self.assertIn("shop", self.period()["wrapped"])
        self.assertIn("wrapped up", [l for l in self.status().splitlines() if l.strip().startswith("shop:")][0])
        again = self.merge(item="three")  # nothing merges after the wrap-up
        self.assertEqual(again.returncode, 1)
        self.assertIn("wrapped up", again.stderr)
        self.assertEqual(self.run_script("lane-improve", "drained", "shop").returncode, 1)

    def test_the_wrap_up_order_works_at_the_cap_pr_then_drained(self):
        """lc-o8i: the cap is reached, `lane-improve pr` still runs (its own refusal and the skill tell the coordinator to),
        then `drained`; only after that does the project refuse everything."""
        self.merge(item="one")
        self.merge(item="two")
        self.assertIn("items 2/2", self.status())
        self.assertRefused(self.merge(item="three"), "improve")  # the cap is a hard stop for merges
        f = self.tmp / "summary.md"
        f.write_text("- Done: one and two\n")
        r = self.run_script("lane-improve", "pr", "shop", "--summary-file", str(f))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("summary PR", r.stdout)
        self.assertIn("Done: one and two", self.summary_prs(self.shop)[str(self.state()["prs"]["shop"])]["body"])
        self.assertEqual(self.run_script("lane-improve", "drained", "shop").returncode, 0)
        self.assertIn("shop", self.period()["wrapped"])
        self.assertIn("improve: on", self.status())

    def test_pr_refuses_a_project_that_is_not_in_the_period_even_at_a_cap(self):
        r = self.run_script("lane-improve", "pr", "blog")
        self.assertEqual(r.returncode, 1)
        self.assertIn("blog", r.stderr)

    def test_off_after_a_wrap_up_leaves_only_the_others_to_drain(self):
        self.assertEqual(self.on("--continue", "--project", "blog").returncode, 0)
        self.merge(item="one")
        self.merge(item="two")
        self.run_script("lane-improve", "drained", "shop")
        self.run_script("lane-improve", "off")
        d = json.loads(self.drain_file().read_text())
        self.assertNotIn("shop", d["pending"])
        self.assertIn("blog", d["pending"])

    def test_off_when_every_project_has_wrapped_up_closes_the_period(self):
        self.merge(item="one")
        self.merge(item="two")
        self.run_script("lane-improve", "drained", "shop")
        r = self.run_script("lane-improve", "off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.drain_file().exists())
        self.assertFalse(self.improve_file().exists())
        self.assertIn("improve: off", self.status())

    def test_the_deny_rules_come_out_with_the_wrap_up(self):
        settings = self.shop / ".claude" / "settings.local.json"
        self.assertIn("deny", settings.read_text())
        self.merge(item="one")
        self.merge(item="two")
        self.run_script("lane-improve", "drained", "shop")
        text = settings.read_text() if settings.exists() else ""
        self.assertNotIn("gh pr merge", text)

    def test_a_continue_rerun_naming_a_wrapped_project_whose_cap_is_still_reached_is_refused(self):
        self.merge(item="one")
        self.merge(item="two")
        self.run_script("lane-improve", "drained", "shop")
        settings = self.shop / ".claude" / "settings.local.json"
        r = self.on("--continue", "--project", "shop")
        self.assertEqual(r.returncode, 1)
        self.assertIn("wrapped up", r.stderr)
        self.assertIn("raise", r.stderr)
        text = settings.read_text() if settings.exists() else ""
        self.assertNotIn("gh pr merge", text)  # no rules came back for a project that cannot merge
        self.assertIn("shop", self.period()["wrapped"])

    def test_raising_the_cap_and_naming_the_project_resumes_it(self):
        self.merge(item="one")
        self.merge(item="two")
        self.run_script("lane-improve", "drained", "shop")
        r = self.on("--continue", "--project", "shop", "--max-items", "5")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("resumes", r.stdout)
        self.assertEqual(self.period()["wrapped"], [])
        self.assertIn("gh pr merge", (self.shop / ".claude" / "settings.local.json").read_text())  # its rules are back
        self.assertEqual(self.merge(item="three").returncode, 0)
        self.assertIn("items 3/5", self.status())

    def test_raising_the_cap_without_naming_the_project_leaves_it_wrapped_and_says_so(self):
        self.merge(item="one")
        self.merge(item="two")
        self.run_script("lane-improve", "drained", "shop")
        r = self.on("--continue", "--max-items", "5")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("still wrapped up", r.stdout)
        self.assertIn("--project shop", r.stdout)
        self.assertIn("wrapped up", [l for l in self.status().splitlines() if l.strip().startswith("shop:")][0])
        settings = self.shop / ".claude" / "settings.local.json"
        self.assertNotIn("gh pr merge", settings.read_text() if settings.exists() else "")

    def test_off_with_everything_wrapped_up_still_takes_leftover_rules_out(self):
        self.merge(item="one")
        self.merge(item="two")
        self.run_script("lane-improve", "drained", "shop")
        settings = self.shop / ".claude" / "settings.local.json"
        # a rule installed by hand through the adapter after the wrap-up (what a stray re-add would leave behind)
        code = (f"import sys; sys.path.insert(0, {str(ROOT / 'core')!r}); import harnesses; from pathlib import Path; "
                f"harnesses.load_harness('claude').install_away_denies(Path({str(self.shop)!r}), 'main', 'shop')")
        self.assertEqual(subprocess.run([sys.executable, "-c", code], env=self.env).returncode, 0)
        self.assertIn("gh pr merge", settings.read_text())
        self.assertEqual(self.run_script("lane-improve", "off").returncode, 0)
        self.assertNotIn("gh pr merge", settings.read_text() if settings.exists() else "")

    # --- the period record: locked, never reset by an unreadable file

    def test_many_away_merges_at_once_are_all_counted(self):
        code = ("import sys; sys.path.insert(0, %r); import improve; s = improve.load(); "
                "[improve.record_merge(s, sys.argv[1], i, 'x', 'abc') for i in range(6)]" % str(ROOT / "core"))
        procs = [subprocess.Popen([sys.executable, "-c", code, name], env=self.env) for name in ("shop", "blog") * 4]
        for pr in procs:
            self.assertEqual(pr.wait(), 0)
        self.assertEqual({k: len(v) for k, v in self.period()["merges"].items()}, {"shop": 24, "blog": 24})
        self.assertEqual([f.name for f in self.conf_dir.glob("improve-period*") if f.name != "improve-period.json"
                          and f.suffix != ".lock"], [])  # no stray temp files

    def test_an_unreadable_period_record_stops_merges_instead_of_resetting_the_caps(self):
        self.merge(item="one")
        self.merge(item="two")
        self.run_script("lane-improve", "drained", "shop")
        (self.conf_dir / "improve-period.json").write_text("{not json")
        r = self.merge(item="three")
        self.assertRefused(r, "improve")
        self.assertIn("improve-period.json", r.stderr)
        self.assertIn("--continue", r.stderr)  # the way out is named
        st = self.status()
        self.assertNotIn("items 0/2", st)
        self.assertIn("improve-period.json", st)
        self.assertEqual(self.run_script("lane-improve", "drained", "shop").returncode, 1)

    def test_a_lost_period_record_is_not_an_empty_one(self):
        self.merge(item="one")
        (self.conf_dir / "improve-period.json").unlink()
        self.assertRefused(self.merge(item="two"), "improve")
        self.assertNotIn("items 0/2", self.status())

    def test_a_lost_record_while_draining_names_what_works(self):
        """lc-kbb: a drain refuses `on --continue`, so the message must not send the owner there."""
        self.merge(item="one")
        self.run_script("lane-improve", "off")
        (self.conf_dir / "improve-period.json").unlink()
        r = self.merge(item="two")
        self.assertRefused(r, "improve")
        self.assertIn("improve-period.json", r.stderr)
        self.assertIn("lane-improve drained", r.stderr)
        self.assertIn("draining", r.stderr)
        self.assertIn("`lane-improve on --continue` starts a new period on the same away branch", r.stderr)
        # status says it too, and the last PR is not told to be merged by away-merge meanwhile
        st = self.status()
        self.assertIn("improve-period.json", st)
        self.assertNotIn("away-merge the last PR", st)
        # and what it names works, in the order it names: the drain closes, then `on --continue` (a plain `on` is refused
        # while the project has its away branch) starts a new period on the same branch, where the last PR can merge
        self.assertEqual(self.run_script("lane-improve", "drained", "--all").returncode, 0)
        self.assertNotEqual(self.on("--project", "shop").returncode, 0)
        self.assertEqual(self.on("--continue", "--project", "shop").returncode, 0)
        self.assertEqual(self.merge(item="two").returncode, 0)

    def test_a_continue_rerun_makes_a_fresh_period_record_and_says_so(self):
        self.merge(item="one")
        (self.conf_dir / "improve-period.json").write_text("{not json")
        r = self.on("--continue", "--hours", "5")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("record", r.stderr + r.stdout)
        self.assertIn("items 0/2", self.status())
        self.assertEqual(self.merge(item="again").returncode, 0)

    def test_on_starts_the_period_record(self):
        self.reset()
        self.assertEqual(self.on("--project", "shop", "--max-items", "3").returncode, 0)
        self.assertEqual(self.period()["period"], self.state()["started"])
        self.assertEqual(self.period()["merges"], {})

    def test_nothing_is_nudged_when_the_record_is_unreadable(self):
        (self.conf_dir / "improve-period.json").write_text("garbage")
        r = self.run_script("night-precheck", env={"LANE_HOUR": "12"})
        self.assertNotIn("improve", r.stdout)

    # --- M1: nothing after the merge may turn it into a refusal

    def test_a_malformed_gh_answer_after_the_merge_still_exits_0(self):
        st = self.state()
        st["prs"].pop("shop")  # the lookup path
        self.improve_file().write_text(json.dumps(st))
        for shape in ("object", "junk"):
            r = self.run_script("away-merge", "--project", "shop", "--pr", "7", "--item", "x",
                                env={"GH_LIST_SHAPE": shape})
            self.assertEqual(r.returncode, 0, (shape, r.stderr))
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("lane-improve pr shop", r.stderr)

    def test_a_malformed_view_answer_for_the_recorded_pr_still_exits_0(self):
        r = self.run_script("away-merge", "--project", "shop", "--pr", "7", "--item", "x", env={"GH_VIEW_SHAPE": "list"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("lane-improve pr shop", r.stderr)

    def test_whatever_breaks_after_the_merge_is_a_warning(self):
        import contextlib
        import importlib.machinery
        import importlib.util
        import io
        loader = importlib.machinery.SourceFileLoader("away_merge_t", str(ROOT / "core" / "scripts" / "away-merge"))
        spec = importlib.util.spec_from_loader("away_merge_t", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        for exc in (subprocess.TimeoutExpired("git", 120), KeyError(0), AttributeError("list"),
                    ValueError("improve state: x"), MemoryError()):
            def boom(*a, _e=exc, **k):
                raise _e
            err = io.StringIO()
            orig = mod.improve.sync_and_record
            mod.improve.sync_and_record = boom
            try:
                with contextlib.redirect_stderr(err):
                    mod.refresh_summary_pr(Path("."), {}, "shop")
            finally:
                mod.improve.sync_and_record = orig
            self.assertIn("lane-improve pr shop", err.getvalue(), repr(exc))

    def test_a_git_that_hangs_reading_the_items_gives_none(self):
        code = (f"import sys, subprocess; sys.path.insert(0, {str(ROOT / 'core')!r}); import improve\n"
                "def boom(*a, **k):\n    raise subprocess.TimeoutExpired('git', 120)\n"
                "improve._git = boom\n"
                "from pathlib import Path\nprint(improve.merged_items(Path('.'), 'away/2026-11-23', 'main'))\n")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "None"), r.stderr)

    def test_the_items_are_read_from_their_own_refs_not_from_fetch_head(self):
        code = (f"import sys, subprocess; sys.path.insert(0, {str(ROOT / 'core')!r}); import improve\n"
                "calls = []\n"
                "def fake(repo, *args, **k):\n    calls.append(args)\n"
                "    return subprocess.CompletedProcess(args, 0, '', '')\n"
                "improve._git = fake\n"
                "from pathlib import Path\nimprove.merged_items(Path('.'), 'away/2026-11-23', 'main')\nprint(calls)\n")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout
        self.assertNotIn("FETCH_HEAD", out)
        self.assertIn("+refs/heads/away/2026-11-23:refs/remotes/origin/away/2026-11-23", out)


if __name__ == "__main__":
    unittest.main()
