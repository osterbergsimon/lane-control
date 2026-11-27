"""EXT-2 (lc-76o): lifecycle hooks. The owner's scripts, registered as extras of kind `hook`
(`lane-control project|machine extras add --hook <event> --script <path | git-url@commit:path>`), run at five events:
after-setup (setup or refresh), lane-start (`lane-control lane prepare --role lanes`), gate (`lane-control project gate`,
and away-merge), after-merge (`lane-control project hook ... after-merge`, and away-merge), on-report (the coordinator's
`hook ... on-report`, and the away watchdog's escalation). Each gets facts as LANE_HOOK_* variables and never a secret,
runs outside every worktree with a timeout, and its exit code and last lines are shown. A failing gate blocks the merge
(and away-merge refuses), a failing lane-start holds the lane, after-merge and on-report only warn. Scratch HOMEs only;
no hook runs against the real machine."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_extras import LE, LC, SECRET, X, F, Scratch, git  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HEAD = "0123456789abcdef0123456789abcdef01234567"
MERGE = "fedcba9876543210fedcba9876543210fedcba98"

FACTS = """#!/bin/sh
# writes its facts, its folder and whether a secret reached it, prints 30 lines and exits with its code
out="{out}"
{{ env | grep '^LANE_HOOK_' | sort; echo "PWD=$(pwd)"; echo "ARGS=$#";
   env | grep -c -E 'GITHUB_TOKEN|MY_API_TOKEN|ANTHROPIC_API_KEY' | sed 's/^/SECRETS=/'; }} > "$out"
for i in $(seq 1 30); do echo "line $i"; done
exit {code}
"""


def facts(f: Path) -> dict:
    out = {}
    for line in f.read_text().splitlines():
        k, _, v = line.partition("=")
        out[k] = v
    return out


class HookScratch(Scratch):
    def hook(self, event, code=0, name=None, machine=False, timeout=None):
        """An owner's hook script outside every checkout that records its facts in <owner>/<name>.facts."""
        name = name or event
        out = self.owner / f"{name}.facts"
        f = self.script(f"{name}.sh", FACTS.format(out=out, code=code))
        args = ["--hook", event, "--script", f, "--name", name, *(["--timeout", timeout] if timeout else [])]
        if machine:
            self.ok("machine", "add", *args)
        else:
            self.add(*args)
        return f, out


class StoreTests(HookScratch):
    def test_a_hook_is_stored_pinned_with_its_event_and_timeout(self):
        f, _ = self.hook("gate", name="tests")
        (e,) = self.stored()
        self.assertEqual((e["kind"], e["name"], e["event"], e["timeout"]), ("hook", "tests", "gate", 600))
        self.assertEqual((e["script"], e["sha256"]), (str(f), X.sha256(f)))
        self.assertEqual(e["for"], [])
        self.clean(self.repo)
        out = self.ok("project", "list", self.repo)
        self.assertIn("by event:", out)
        self.assertIn("gate: hook:project:tests", out)
        self.assertIn("lanes: none", out)  # a hook is no role's: it runs outside every session

    def test_m3_a_linked_script_pins_and_runs_its_target(self):
        real = self.t / "dotfiles" / "gate.sh"
        real.parent.mkdir()
        real.write_text("#!/bin/sh\necho real\n")
        real.chmod(0o755)
        link = self.owner / "gate.sh"
        link.symlink_to(real)
        out = self.add("--hook", "gate", "--script", link)
        self.assertIn(f"the file pinned and run is {real}", out)
        (e,) = self.stored()
        self.assertEqual(e["script"], str(real))
        self.assertEqual({r["id"]: r for r in X.check(self.store)}["hook:project:gate"]["state"], "ok")
        code, so, _ = self.run_le("gate", self.repo, "7", "--head", HEAD)
        self.assertEqual(code, 0, so)
        self.assertIn("| real", so)

    def test_bad_hooks_are_refused(self):
        f = self.script("h.sh", "#!/bin/sh\nexit 0\n")
        self.refused("project", "add", self.repo, "--hook", "before-lunch", "--script", f, says="takes after-setup")
        self.refused("project", "add", self.repo, "--hook", "gate", says="needs --script")
        self.refused("project", "add", self.repo, "--hook", "gate", "--script", "h.sh", says="absolute path")
        self.refused("project", "add", self.repo, "--hook", "gate", "--script", self.script("n.sh", "x", 0o644),
                     says="not executable")
        self.refused("project", "add", self.repo, "--hook", "gate", "--script", f, "--timeout", "0", says="--timeout")
        self.refused("project", "add", self.repo, "--hook", "gate", "--script", f, "--for", "lanes", says="--for")
        self.refused("project", "add", self.repo, "--hook", "gate", "--script", f, "--env", "A", says="for --mcp")
        self.refused("project", "add", self.repo, "--hook", "gate", "--script", f, "--", "x", says="for --mcp")
        self.refused("project", "add", self.repo, "--script", f, says="exactly one kind")
        self.assertEqual(self.stored(), [])

    def test_a_script_inside_a_project_checkout_is_refused(self):
        inside = self.wt / "gate.sh"
        inside.write_text("#!/bin/sh\nexit 0\n")
        inside.chmod(0o755)
        self.refused("project", "add", self.repo, "--hook", "gate", "--script", inside, says="inside the project")
        link = self.owner / "link.sh"
        link.symlink_to(inside)
        self.refused("project", "add", self.repo, "--hook", "gate", "--script", link, says="inside the project")
        self.assertEqual(self.stored(), [])
        out = self.add("--hook", "gate", "--script", inside, "--allow-repo-path")
        self.assertIn("pinned by its hash", out)

    def test_a_secret_in_a_git_url_is_refused_and_never_shown(self):
        url = f"https://x:{SECRET}@example.com/hooks.git@{'a' * 40}:gate.sh"
        self.refused("project", "add", self.repo, "--hook", "gate", "--script", url, says="password or token")
        self.assertEqual(self.stored(), [])

    def test_a_git_hook_is_fetched_at_add_and_pinned(self):
        up = self.t / "upstream" / "hooks"
        (up / "bin").mkdir(parents=True)
        s = up / "bin" / "gate.sh"
        s.write_text("#!/bin/sh\necho from git; echo \"$LANE_HOOK_PR\"\n")
        s.chmod(0o755)
        git("init", "-q", "-b", "main", cwd=up)
        git("add", "-A", cwd=up)
        git("commit", "-q", "-m", "v1", cwd=up)
        sha = git("rev-parse", "HEAD", cwd=up)
        self.refused("project", "add", self.repo, "--hook", "gate", "--script", f"file://{up}@main:bin/gate.sh",
                     says="full 40-hex commit")
        self.refused("project", "add", self.repo, "--hook", "gate", "--script", f"file://{up}@{sha}:bin/none.sh",
                     says="no bin/none.sh")
        self.add("--hook", "gate", "--script", f"file://{up}@{sha}:bin/gate.sh")
        (e,) = self.stored()
        self.assertEqual((e["name"], e["url"], e["commit"], e["path"]), ("gate", f"file://{up}", sha, "bin/gate.sh"))
        code, out, err = self.run_le("gate", self.repo, "7", "--head", HEAD)
        self.assertEqual(code, 0, out + err)
        self.assertIn("from git", out)
        self.assertEqual({r["id"]: r for r in X.check(self.store)}["hook:project:gate"]["state"], "ok")
        # review M4, M5: the checkout's working files never count; the hook runs from an export of the pinned commit
        (X.hook_source_dir(e) / "gate.sh").write_text("#!/bin/sh\necho UNCHECKED\n")  # planted in the cache
        (X.hook_source_dir(e) / "local.sh").write_text("echo planted\n")
        self.assertEqual({r["id"]: r for r in X.check(self.store)}["hook:project:gate"]["state"], "ok")
        code, out, err = self.run_le("gate", self.repo, "7", "--head", HEAD)
        self.assertEqual(code, 0, out)
        self.assertIn("from git", out)
        self.assertNotIn("UNCHECKED", out)

    def test_m4_a_git_hook_that_writes_beside_itself_runs_again_and_again(self):
        up = self.t / "upstream" / "py"
        up.mkdir(parents=True)
        (up / "lib.py").write_text("WORD = 'gate ok'\n")
        (up / "gate.py").write_text("#!/usr/bin/env python3\nimport os, sys\nsys.path.insert(0, os.path.dirname(__file__))\n"
                                    "import lib\nopen(os.path.join(os.path.dirname(__file__), 'log'), 'a').write('x')\n"
                                    "print(lib.WORD)\n")
        (up / "gate.py").chmod(0o755)
        git("init", "-q", "-b", "main", cwd=up)
        git("add", "-A", cwd=up)
        git("commit", "-q", "-m", "v1", cwd=up)
        sha = git("rev-parse", "HEAD", cwd=up)
        self.add("--hook", "gate", "--script", f"file://{up}@{sha}:gate.py")
        for _ in range(2):
            code, out, err = self.run_le("gate", self.repo, "7", "--head", HEAD)
            self.assertEqual(code, 0, out + err)
            self.assertIn("| gate ok", out)
        e = [x for x in X.effective(self.store) if x["kind"] == "hook"][0]
        self.assertFalse([p for p in X.hook_source_dir(e).rglob("log")])  # it wrote into its run folder only


class EventTests(HookScratch):
    def test_gate_fires_with_the_facts_and_a_failure_is_not_green(self):
        _, out = self.hook("gate", name="tests")
        code, so, err = self.run_le("gate", self.repo, "7", "--head", HEAD, "--lane", "APP-3", "--issue", "app-x1")
        self.assertEqual(code, 0, so + err)
        got = facts(out)
        self.assertEqual(got["LANE_HOOK_EVENT"], "gate")
        self.assertEqual(got["LANE_HOOK_PR"], "7")
        self.assertEqual(got["LANE_HOOK_HEAD_SHA"], HEAD)
        self.assertEqual(got["LANE_HOOK_LANE"], "APP-3")
        self.assertEqual(got["LANE_HOOK_ISSUE"], "app-x1")
        self.assertEqual(got["LANE_HOOK_PROJECT"], "app")
        self.assertEqual(got["LANE_HOOK_PROJECT_ROOT"], str(self.repo))
        self.assertIn(f"gate: passed for PR 7 at {HEAD}", so)
        self.hook("gate", name="lint", code=3)
        code, so, err = self.run_le("gate", self.repo, "7", "--head", HEAD)
        self.assertEqual(code, 1, so)
        self.assertIn("hook:project:lint failed: exit 3", so)
        self.assertIn("not green", so)
        self.assertIn("hook:project:tests ok", so)  # every gate runs, and each is said

    def test_no_gate_hooks_pass_without_asking_github(self):
        code, out, err = self.run_le("gate", self.repo, "7")
        self.assertEqual(code, 0, out + err)
        self.assertIn("no gate hooks", out)

    def test_the_head_is_read_from_the_pr_when_not_given_and_unreadable_is_not_green(self):
        _, out = self.hook("gate")
        with mock.patch.object(LE, "pr_head", lambda root, pr: HEAD):
            self.assertEqual(self.run_le("gate", self.repo, "7")[0], 0)
        self.assertEqual(facts(out)["LANE_HOOK_HEAD_SHA"], HEAD)
        with mock.patch.object(LE, "pr_head", lambda root, pr: None):
            code, so, _ = self.run_le("gate", self.repo, "7")
        self.assertEqual(code, 1)
        self.assertIn("head", so)

    def test_after_merge_and_on_report_fire_and_a_failure_only_warns(self):
        _, am = self.hook("after-merge", code=1)
        _, rep = self.hook("on-report")
        code, out, err = self.run_le("hook", self.repo, "after-merge", "--pr", "7", "--head", HEAD,
                                     "--merge-sha", MERGE, "--lane", "APP-3", "--issue", "app-x1")
        self.assertEqual(code, 0, out + err)
        self.assertIn("warning", out)
        self.assertEqual(facts(am)["LANE_HOOK_MERGE_SHA"], MERGE)
        code, out, err = self.run_le("hook", self.repo, "on-report", "--lane", "APP-3", "--outcome", "succeeded",
                                     "--pr", "7")
        self.assertEqual(code, 0, out + err)
        got = facts(rep)
        self.assertEqual((got["LANE_HOOK_EVENT"], got["LANE_HOOK_OUTCOME"], got["LANE_HOOK_LANE"]),
                         ("on-report", "succeeded", "APP-3"))

    def test_lane_start_fires_in_prepare_and_a_failure_holds_the_lane(self):
        _, out = self.hook("lane-start")
        code, so, err = self.run_le("prepare", self.repo, self.wt, "--role", "lanes", "--lane", "APP-3",
                                    "--issue", "app-x1")
        self.assertEqual(code, 0, so + err)
        got = facts(out)
        self.assertEqual((got["LANE_HOOK_WORKTREE"], got["LANE_HOOK_ROLE"], got["LANE_HOOK_LANE"]),
                         (str(self.wt), "lanes", "APP-3"))
        out.unlink()
        self.assertEqual(self.prepare("reviewer")[0], 0)
        self.assertFalse(out.exists(), "a reviewer's prepare is no lane start")
        self.hook("lane-start", name="fails", code=4)
        code, so, err = self.prepare()
        self.assertEqual(code, 1)
        self.assertIn("hold the lane", so)

    def test_the_coordinator_persona_is_told_to_prepare_when_there_are_lane_start_hooks(self):
        self.assertEqual(X.coordinator_section(self.repo, X.effective(self.store), "/lc"), "")
        self.hook("lane-start")
        text = X.coordinator_section(self.repo, X.effective(self.store), "/lc")
        self.assertIn("lane prepare", text)
        self.assertIn("--lane <KEY> --issue <id>", text)

    def test_machine_hooks_run_for_every_project_before_the_projects_own(self):
        _, m = self.hook("gate", name="machine-gate", machine=True)
        _, p = self.hook("gate", name="project-gate")
        code, out, _ = self.run_le("gate", self.repo, "7", "--head", HEAD)
        self.assertEqual(code, 0, out)
        self.assertTrue(m.exists() and p.exists())
        self.assertLess(out.index("hook:machine:machine-gate"), out.index("hook:project:project-gate"))

    def test_facts_are_checked_and_never_echoed(self):
        self.hook("on-report")
        for flag, value in (("--pr", "7; rm -rf /"), ("--head", "abc"), ("--lane", "a b"), ("--outcome", SECRET + "\n")):
            with self.subTest(flag=flag):
                code, out, err = self.run_le("hook", self.repo, "on-report", flag, value)
                self.assertNotEqual(code, 0)
                self.assertNotIn(value.strip(), out + err)


class NameTests(HookScratch):
    """Review B1: facts are names, not secrets; only the real shapes of a key are refused."""

    def test_ordinary_names_with_sk_in_them_are_fine_and_no_hooks_is_green(self):
        tracker = self.t / "task-tracker"
        tracker.mkdir()
        git("init", "-q", "-b", "main", cwd=tracker)
        store = X.store_for(tracker)
        store.mkdir(parents=True)
        (store / "settings.json").write_text(json.dumps({"root": str(tracker), "name": "task-tracker"}))
        code, out, _ = self.run_le("gate", tracker, "7", "--head", HEAD)
        self.assertEqual(code, 0, out)
        self.assertIn("no gate hooks", out)
        self.assertEqual(self.run_le("hook", tracker, "on-report", "--lane", "desk-4ab")[0], 0)
        _, facts_out = self.hook("lane-start")
        wt = self.t / "workspaces" / "fix-disk-usage"
        git("worktree", "add", "-q", wt, "-b", "fix-disk-usage", cwd=self.repo)
        code, out, err = self.run_le("prepare", self.repo, wt, "--role", "lanes", "--lane", "TASK-1",
                                     "--issue", "desk-4ab")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(facts(facts_out)["LANE_HOOK_ISSUE"], "desk-4ab")
        H = LE.H
        for v in ("/srv/code/helpdesk-ui", "task-tracker", "risk-ask-flask"):
            self.assertEqual(H.check_facts({"project": v}), {"project": v})
        self.assertEqual(H.clean("task-list tests passed"), "task-list tests passed")
        with self.assertRaises(H.HookError):
            H.check_facts({"worktree": "/tmp/" + SECRET})

    def test_m7_the_outcome_is_a_status_never_a_lane_key(self):
        self.hook("on-report")
        code, out, err = self.run_le("hook", self.repo, "on-report", "--outcome", "APP-3")
        self.assertNotEqual(code, 0)
        self.assertNotIn("APP-3", out + err)
        for word in ("succeeded", "failed", "blocked", "NEEDS_DEEP", "escalation"):
            self.assertEqual(self.run_le("hook", self.repo, "on-report", "--outcome", word)[0], 0, word)

    def test_m2_minor_an_attached_repos_pr_names_its_repo(self):
        other = self.t / "lib"
        other.mkdir()
        git("init", "-q", "-b", "main", cwd=other)
        _, out = self.hook("gate")
        self.refused("gate", self.repo, "7", "--head", HEAD, "--repo", other, says="neither the project's")
        (self.store / "settings.json").write_text(json.dumps({"root": str(self.repo), "name": "app",
                                                              "attached": [str(other)]}))
        self.assertEqual(self.run_le("gate", self.repo, "7", "--head", HEAD, "--repo", other)[0], 0)
        got = facts(out)
        self.assertEqual((got["LANE_HOOK_REPO"], got["LANE_HOOK_PROJECT_ROOT"]), (str(other), str(self.repo)))


class SkipTests(HookScratch):
    """Review m3: the way past a broken blocking hook is said where it fails; a skip is a person's, with a reason."""

    def test_a_failing_gate_says_the_way_past_and_a_person_can_skip_it_once(self):
        self.hook("gate", name="broken", code=1)
        code, out, _ = self.run_le("gate", self.repo, "7", "--head", HEAD)
        self.assertEqual(code, 1)
        self.assertIn("lane-control project extras remove hook:broken", out)
        self.assertIn("--skip-hook project:broken --reason", out)
        self.refused("gate", self.repo, "7", "--head", HEAD, "--skip-hook", "broken", says="needs --reason")
        self.refused("gate", self.repo, "7", "--head", HEAD, "--skip-hook", "nope", "--reason", "the CI box is down",
                     says="no gate hook of that name")
        code, out, _ = self.run_le("gate", self.repo, "7", "--head", HEAD, "--skip-hook", "broken", "--reason",
                                   "the CI box is down today")
        self.assertEqual(code, 0, out)
        self.assertIn("hook:project:broken skipped by a person", out)
        (rec,) = [json.loads(x) for x in LE.H.skips_file().read_text().splitlines()]
        self.assertEqual((rec["hook"], rec["reason"], rec["pr"], rec["event"]),
                         ("hook:project:broken", "the CI box is down today", "7", "gate"))
        self.assertEqual(oct(LE.H.skips_file().stat().st_mode & 0o777), "0o600")

    def test_an_agent_cannot_skip(self):
        self.hook("gate", name="broken", code=1)

        def agent(what):
            raise LE.X.ExtrasError(f"{what} is for a person at a terminal")
        with mock.patch.object(LE, "person", agent), contextlib.redirect_stderr(io.StringIO()) as err, \
                contextlib.redirect_stdout(io.StringIO()):
            code = LE.main(["gate", str(self.repo), "7", "--head", HEAD, "--skip-hook", "broken",
                            "--reason", "because I want to"])
        self.assertIn("for a person at a terminal", err.getvalue())
        self.assertEqual(code, 1)
        self.assertFalse(LE.H.skips_file().exists())


class RunTests(HookScratch):
    def test_a_hook_runs_outside_every_worktree_with_no_secret_and_no_arguments(self):
        _, out = self.hook("on-report")
        os.environ.update(GITHUB_TOKEN=SECRET, MY_API_TOKEN=SECRET, ANTHROPIC_API_KEY=SECRET)
        code, so, err = self.run_le("hook", self.repo, "on-report")
        self.assertEqual(code, 0, so + err)
        got = facts(out)
        self.assertEqual(got["SECRETS"], "0")
        self.assertEqual(got["ARGS"], "0")
        cwd = Path(got["PWD"])
        self.assertIsNone(X.inside_checkout(cwd))
        self.assertFalse(cwd.exists(), "its scratch folder is removed after")
        self.assertNotIn(SECRET, so + err)

    def test_the_exit_code_and_the_last_20_lines_are_shown(self):
        self.hook("after-merge", code=5)
        code, out, _ = self.run_le("hook", self.repo, "after-merge", "--pr", "7")
        self.assertEqual(code, 0)
        self.assertIn("hook:project:after-merge failed: exit 5", out)
        self.assertIn("line 30", out)
        self.assertIn("line 11", out)
        self.assertNotIn("line 10\n", out)

    def test_a_secret_or_control_text_in_the_output_is_not_printed(self):
        self.script("loud.sh", f"#!/bin/sh\necho 'token {SECRET}'\nprintf '\\033]0;title\\007ok\\n'\nexit 1\n")
        self.add("--hook", "on-report", "--script", self.owner / "loud.sh")
        code, out, _ = self.run_le("hook", self.repo, "on-report")
        self.assertNotIn(SECRET, out)
        self.assertIn("[redacted]", out)
        self.assertNotIn("\033", out)
        self.assertIn("hook:project:on-report-loud failed", out)  # a script's name at an event that is not in it

    def test_m6_common_secret_forms_are_masked_and_n1_control_characters_go(self):
        H = LE.H
        for line in ("password=hunter2-fake", "DB_PASSWORD: correct-horse-fake", "--password hunter2fake",
                     "key 0123456789abcdef0123456789abcdef", '{"client_secret": "Fake0123abcd"}',
                     "Authorization: Bearer abc.def", "https://u:pw@example.com/x", "token " + SECRET):
            with self.subTest(line=line):
                got = H.clean(line)
                self.assertIn("[redacted]", got)
                for bad in ("hunter2", "correct-horse", "0123456789abcdef0123", "Fake0123", "abc.def", ":pw@",
                            SECRET):
                    self.assertNotIn(bad, got)
        self.assertIn("[redacted]", H.clean("Basic dXNlcjpwYXNzd29yZA=="))
        for line in ("PASS: test_x", "ok 12 tests passed", "exit=0", "PWD=/tmp/x", "basic tests pass"):
            self.assertEqual(H.clean(line), line)
        self.assertEqual(H.clean("a\x9b31mb\u202ec\u200bd\x85e"), "abcde")  # an 8-bit CSI, bidi, zero width, C1

    def test_a_commit_in_the_output_is_shown(self):
        self.script("sha.sh", f"#!/bin/sh\necho 'tested {HEAD}'\n")
        self.add("--hook", "gate", "--script", self.owner / "sha.sh")
        code, out, _ = self.run_le("gate", self.repo, "7", "--head", HEAD)
        self.assertEqual(code, 0, out)
        self.assertIn(f"| tested {HEAD}", out)

    def test_a_timeout_stops_the_hook_and_its_children_and_fails_the_gate(self):
        pid = self.owner / "child.pid"
        self.script("slow.sh", f"#!/bin/sh\nsleep 60 &\necho $! > {pid}\nsleep 60\n")
        self.add("--hook", "gate", "--script", self.owner / "slow.sh", "--timeout", "1")
        t = time.monotonic()
        code, out, _ = self.run_le("gate", self.repo, "7", "--head", HEAD)
        self.assertLess(time.monotonic() - t, 20)
        self.assertEqual(code, 1, out)
        self.assertIn("timed out after 1 s", out)
        child = int(pid.read_text())
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)

    def test_a_changed_or_missing_script_is_not_run_and_is_flagged(self):
        f, out = self.hook("gate")
        f.write_text(f.read_text().replace("exit 0", "exit 0 # changed"))
        code, so, _ = self.run_le("gate", self.repo, "7", "--head", HEAD)
        self.assertEqual(code, 1, so)
        self.assertIn("changed since it was added", so)
        self.assertFalse(out.exists(), "a changed script never runs")
        rows = {r["id"]: r for r in X.check(self.store)}
        self.assertEqual(rows["hook:project:gate"]["state"], "changed")
        f.unlink()
        self.assertEqual({r["id"]: r for r in X.check(self.store)}["hook:project:gate"]["state"], "missing")
        self.assertEqual(self.run_le("hook", self.repo, "after-merge")[0], 0)  # no after-merge hook: nothing

    def test_a_script_swapped_after_the_check_is_not_what_runs(self):
        # the hook runs a copy checked against its pin, so a later write to the file cannot change what runs
        f, out = self.hook("on-report")
        H = LE.H
        e = [x for x in X.effective(self.store) if x["kind"] == "hook"][0]
        real = H.prepared

        def swap(entry, d):
            got = real(entry, d)
            f.write_text("#!/bin/sh\necho swapped > " + str(out) + "\n")
            return got
        with mock.patch.object(H, "prepared", swap):
            r = H.run_one(e, {"event": "on-report", "project": "app", "project_root": str(self.repo)})
        self.assertEqual(r["state"], "ok", r)
        self.assertNotIn("swapped", out.read_text())


class CheckTests(HookScratch):
    def test_doctor_lists_hooks_and_flags_a_missing_one(self):
        f, _ = self.hook("gate")
        r = self.lc("doctor", "--no-ssh")
        self.assertIn("hooks: gate hook:project:gate", r.stdout)
        f.unlink()
        d = json.loads(self.lc("doctor", "--no-ssh", "--json").stdout)
        todo = [t for t in d["todo"] if "hook:project:gate" in t]
        self.assertTrue(todo, d["todo"])
        self.assertIn("put it back or add it again", todo[0])
        self.assertNotIn("run lane-control project refresh", todo[0])


def store_hook(env: dict, checkout: Path, owner: Path, event: str, code: int = 0, name: str = None) -> Path:
    """A hook of `checkout`'s project written straight into its settings store (as `extras add` writes it), for the
    scripts that find the store by the checkout (away-merge, away-watchdog); returns its facts file."""
    name = name or event
    out = owner / f"{name}.facts"
    f = owner / f"{name}.sh"
    owner.mkdir(parents=True, exist_ok=True)
    f.write_text(FACTS.format(out=out, code=code))
    f.chmod(0o755)
    with mock.patch.dict(os.environ, env, clear=True):
        store = X.store_for(checkout)
        store.mkdir(parents=True, exist_ok=True)
        data = json.loads(X.project_file(store).read_text()) if X.project_file(store).exists() else {"extras": []}
        data["extras"].append({"kind": "hook", "name": name, "event": event, "for": [], "timeout": 60,
                               "script": str(f), "sha256": X.sha256(f)})
        X.save(X.project_file(store), data)
    return out


from test_away_merge import AwayMergeTest, OID  # noqa: E402
from test_away_watchdog import WatchTest  # noqa: E402


class AwayMergeHookTests(AwayMergeTest):
    def test_a_failing_gate_refuses_the_merge(self):
        out = store_hook(self.env, self.shop, self.tmp / "owner", "gate", code=2)
        r = self.merge()
        self.assertRefused(r, "gate")
        self.assertIn("hook:project:gate failed: exit 2", r.stdout)
        got = facts(out)
        self.assertEqual((got["LANE_HOOK_PR"], got["LANE_HOOK_HEAD_SHA"], got["LANE_HOOK_PROJECT"]),
                         ("7", OID, "shop"))

    def test_a_passing_gate_merges_and_a_failing_after_merge_only_warns(self):
        self.pr(merge_oid=MERGE)
        store_hook(self.env, self.shop, self.tmp / "owner", "gate")
        am = store_hook(self.env, self.shop, self.tmp / "owner", "after-merge", code=1)
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.merged()), 1)
        self.assertIn("hook:project:gate ok", r.stdout)
        self.assertIn("warning: 1 after-merge hook(s) did not pass; the merge stands", r.stderr)
        got = facts(am)
        self.assertEqual((got["LANE_HOOK_EVENT"], got["LANE_HOOK_MERGE_SHA"], got["LANE_HOOK_PR"]),
                         ("after-merge", MERGE, "7"))


    def test_r2_m1_away_merge_says_how_long_and_the_way_past(self):
        store_hook(self.env, self.shop, self.tmp / "owner", "gate", code=2)
        r = self.merge()
        self.assertRefused(r, "gate")
        self.assertIn("up to 60 s in all", r.stdout)
        self.assertIn("lane-control project extras remove hook:gate", r.stderr)
        self.assertIn("away-merge has no --skip-hook", r.stderr)

    def test_m1_a_pr_retargeted_during_the_gates_is_not_merged(self):
        owner = self.tmp / "owner"
        owner.mkdir()
        retarget = owner / "retarget.py"
        retarget.write_text("#!/usr/bin/env python3\nimport json\n"
                            f"f = {str(self.tmp / 'prs.json')!r}\n"
                            "prs = json.load(open(f))\nprs['7']['baseRefName'] = 'main'\njson.dump(prs, open(f, 'w'))\n")
        retarget.chmod(0o755)
        with mock.patch.dict(os.environ, self.env, clear=True):
            store = X.store_for(self.shop)
            X.save(X.project_file(store), {"extras": [{"kind": "hook", "name": "retarget", "event": "gate", "for": [],
                                                       "timeout": 60, "script": str(retarget),
                                                       "sha256": X.sha256(retarget)}]})
        r = self.merge()
        self.assertRefused(r, "changed")
        self.assertIn("now goes into 'main'", r.stderr)

    def test_m2_a_stopped_away_merge_still_says_it_merged_and_stops_its_hook(self):
        owner = self.tmp / "owner"
        owner.mkdir()
        pid = owner / "hook.pid"
        slow = owner / "slow.sh"
        slow.write_text(f"#!/bin/sh\necho $$ > {pid}\nsleep 30\n")
        slow.chmod(0o755)
        with mock.patch.dict(os.environ, self.env, clear=True):
            store = X.store_for(self.shop)
            X.save(X.project_file(store), {"extras": [{"kind": "hook", "name": "slow", "event": "after-merge",
                                                       "for": [], "timeout": 60, "script": str(slow),
                                                       "sha256": X.sha256(slow)}]})
        (self.tmp / "gh.log").write_text("")
        p = subprocess.Popen([str(ROOT / "core" / "scripts" / "away-merge"), "--project", "shop", "--pr", "7",
                              "--item", "Add the x feature"], env=self.env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        stopped_hook(self, p, pid)
        out = p.stdout.read()
        self.assertIn("merged PR 7", out)
        self.assertEqual(len(self.merged()), 1)


def stopped_hook(test, p, pid: Path) -> None:
    """Wait for the hook to start, stop the command with SIGTERM, and check the hook's process group is gone."""
    end = time.monotonic() + 30
    while not (pid.exists() and pid.read_text().strip()) and time.monotonic() < end:
        time.sleep(0.05)
    test.assertTrue(pid.exists(), "the hook never started")
    hook = int(pid.read_text())
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=15)
    test.assertEqual(p.returncode, -signal.SIGTERM)
    end = time.monotonic() + 10
    while time.monotonic() < end:
        try:
            os.kill(hook, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    test.fail("the hook outlived the command that ran it")


def hook_repo(t: Path, files: dict) -> tuple:
    """A scratch git repo of hook files {path: (text, executable)}: (folder, commit)."""
    up = t / "upstream" / f"r{len(list((t / 'upstream').glob('r*'))) if (t / 'upstream').exists() else 0}"
    up.mkdir(parents=True)
    for rel, (text, ex) in files.items():
        (up / rel).parent.mkdir(parents=True, exist_ok=True)
        (up / rel).write_text(text)
        (up / rel).chmod(0o755 if ex else 0o644)
    git("init", "-q", "-b", "main", cwd=up)
    git("add", "-A", cwd=up)
    git("commit", "-q", "-m", "v1", cwd=up)
    return up, git("rev-parse", "HEAD", cwd=up)


class RoundTwoTests(HookScratch):
    """Review round 2 (6fddc8b): each finding's exact case."""

    def test_b1_a_project_hook_never_replaces_a_machine_hook(self):
        # (a) two gate.sh, default names: the machine's fails, the project's passes: both run, not green
        mdir, pdir = self.owner / "machine", self.owner / "proj"
        for d, code in ((mdir, 1), (pdir, 0)):
            d.mkdir()
            (d / "gate.sh").write_text(f"#!/bin/sh\nexit {code}\n")
            (d / "gate.sh").chmod(0o755)
        self.ok("machine", "add", "--hook", "gate", "--script", mdir / "gate.sh")
        self.add("--hook", "gate", "--script", pdir / "gate.sh")
        code, out, _ = self.run_le("gate", self.repo, "7", "--head", HEAD)
        self.assertEqual(code, 1, out)
        self.assertIn("hook:machine:gate failed: exit 1", out)
        self.assertIn("hook:project:gate ok", out)
        self.assertIn("not green", out)
        listed = self.ok("project", "list", self.repo)
        self.assertIn("gate: hook:machine:gate, hook:project:gate", listed)
        self.assertEqual(sorted(r["id"] for r in X.check(self.store) if r.get("event")),
                         ["hook:machine:gate", "hook:project:gate"])
        # (b) one name at two events of one level: refused, naming the other; the gate stays
        self.add("--hook", "gate", "--script", pdir / "gate.sh", "--name", "checks")
        self.refused("project", "add", self.repo, "--hook", "after-merge", "--script", pdir / "gate.sh", "--name",
                     "checks", says="already the gate hook here")
        self.assertEqual([e["event"] for e in self.stored() if e["name"] == "checks"], ["gate"])

    def test_m1_a_git_hooks_cache_is_not_trusted(self):
        up, sha = hook_repo(self.t, {"gate.sh": ("#!/bin/sh\n. \"$(dirname \"$0\")/lib.sh\"\n", True),
                                     "lib.sh": ("exit 1\n", False)})
        self.add("--hook", "gate", "--script", f"file://{up}@{sha}:gate.sh")
        e = [x for x in X.effective(self.store) if x["kind"] == "hook"][0]
        cache = X.hook_source_dir(e)
        self.assertEqual(self.run_le("gate", self.repo, "7", "--head", HEAD)[0], 1)  # lib.sh exits 1: not green
        lib = git("rev-parse", f"{sha}:lib.sh", cwd=up)
        # (a) a replace ref to a passing lib.sh: ignored, still not green
        good = subprocess.run(["git", "-C", str(cache), "hash-object", "-w", "--stdin"], input="exit 0\n",
                              capture_output=True, text=True, check=True).stdout.strip()
        git("-C", cache, "replace", lib, good, cwd=self.t)
        self.assertEqual(self.run_le("gate", self.repo, "7", "--head", HEAD)[0], 1)
        # (b) a planted filter (info/attributes and a smudge command that sees the caller's environment): never run
        marker = self.t / "filter-ran"
        (cache / "info").mkdir(exist_ok=True)
        (cache / "info" / "attributes").write_text("lib.sh filter=x\n")
        git("-C", cache, "config", "filter.x.smudge", f"sh -c 'env > {marker}; echo exit 0'", cwd=self.t)
        os.environ["FAKE_API_TOKEN"] = SECRET
        self.assertEqual(self.run_le("gate", self.repo, "7", "--head", HEAD)[0], 1)
        self.assertFalse(marker.exists(), "a planted filter ran")
        # (c) the lib.sh object rewritten in place: refused (its bytes are not the object of its id)
        obj = cache / "objects" / lib[:2] / lib[2:]
        if not obj.exists():  # a packed object: unpack it first
            for pack in (cache / "objects" / "pack").glob("*.pack"):
                subprocess.run(["git", "-C", str(cache), "unpack-objects", "-q"], stdin=open(pack, "rb"), check=True,
                               env={**os.environ, "GIT_DIR": str(cache)})
                pack.unlink()
                pack.with_suffix(".idx").unlink()
        import zlib
        obj.chmod(0o644)
        obj.write_bytes(zlib.compress(b"blob 7\0exit 0\n"))
        code, out, _ = self.run_le("gate", self.repo, "7", "--head", HEAD)
        self.assertEqual(code, 1, out)
        self.assertIn("refused", out)
        self.assertIn("is not the object of that id", out)
        self.assertEqual({r["id"]: r for r in X.check(self.store)}["hook:project:gate"]["state"], "changed")

    def test_m2_a_repo_with_a_link_out_of_it_is_refused_at_add(self):
        up, _ = hook_repo(self.t, {"gate.sh": ("#!/bin/sh\nexit 0\n", True)})
        (up / "etc").symlink_to("/etc/passwd")
        git("add", "etc", cwd=up)
        git("commit", "-q", "-m", "link", cwd=up)
        sha = git("rev-parse", "HEAD", cwd=up)
        self.refused("project", "add", self.repo, "--hook", "after-merge", "--script", f"file://{up}@{sha}:gate.sh",
                     says="a link that leads out of the repo")

    def test_m3_a_repointed_link_is_changed_and_refused(self):
        v1, v2 = self.owner / "v1.sh", self.owner / "v2.sh"
        v1.write_text("#!/bin/sh\nexit 0\n")
        v2.write_text("#!/bin/sh\nexit 1\n")
        v1.chmod(0o755)
        v2.chmod(0o755)
        link = self.t / "hooks-gate.sh"
        link.symlink_to(v1)
        self.add("--hook", "gate", "--script", link, "--name", "g")
        link.unlink()
        link.symlink_to(v2)
        row = {r["id"]: r for r in X.check(self.store)}["hook:project:g"]
        self.assertEqual(row["state"], "changed")
        code, out, _ = self.run_le("gate", self.repo, "7", "--head", HEAD)
        self.assertEqual(code, 1, out)
        self.assertIn("now leads elsewhere", out)

    def test_m5_skipped_gates_are_not_counted_as_passed(self):
        self.hook("gate", name="a", code=1)
        self.hook("gate", name="b")
        code, out, _ = self.run_le("gate", self.repo, "7", "--head", HEAD, "--skip-hook", "a", "--reason",
                                   "the CI box is down today")
        self.assertEqual(code, 0, out)
        self.assertIn("(1 gate hook(s) run, 1 skipped by a person: hook:project:a)", out)
        code, out, _ = self.run_le("gate", self.repo, "7", "--head", HEAD, "--skip-hook", "a", "--skip-hook", "b",
                                   "--reason", "the CI box is down today")
        self.assertIn("not run for PR 7", out)
        self.assertNotIn("passed", out.splitlines()[-1])

    def test_m6_an_ignored_sighup_stays_ignored(self):
        H = LE.H
        old = signal.signal(signal.SIGHUP, signal.SIG_IGN)
        try:
            with H.guarded():
                self.assertIs(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)
                self.assertIs(signal.getsignal(signal.SIGTERM), H._on_signal)
        finally:
            signal.signal(signal.SIGHUP, old)

    def test_m7_only_real_key_shapes_are_refused_in_names(self):
        H = LE.H
        for v in ("/srv/w/sk-1234-add-login-validation", "sk-learn-experiments-2"):
            self.assertEqual(H.check_facts({"worktree" if v.startswith("/") else "project": v}),
                             {"worktree" if v.startswith("/") else "project": v})
        for v in ("/srv/w/" + SECRET, "/srv/w/sk-ant-api03-Ab12Cd34Ef56Gh78Ij90"):
            with self.assertRaises(H.HookError):
                H.check_facts({"worktree": v})


class StopTests(HookScratch):
    def test_m2_a_stopped_command_stops_its_hook(self):
        pid = self.owner / "hook.pid"
        self.script("slow.sh", f"#!/bin/sh\necho $$ > {pid}\nsleep 30\n")
        self.add("--hook", "gate", "--script", self.owner / "slow.sh", "--timeout", "120")
        p = subprocess.Popen([sys.executable, str(LC), "project", "gate", str(self.repo), "7", "--head", HEAD],
                             env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stopped_hook(self, p, pid)
        self.assertIn("up to 120 s in all", p.stdout.read())


class WatchdogHookTests(WatchTest):
    def test_an_escalation_runs_the_on_report_hooks_and_a_failure_only_warns(self):
        out = store_hook(self.env, self.shop, self.tmp / "owner", "on-report", code=1)
        self.build_lane(live=False)
        r = self.watch(130)
        self.assertIn("SENT ESCALATE shop to term_mc", r.stdout)
        self.assertIn("HOOK shop on-report: hook:project:on-report failed: exit 1", r.stdout)
        self.assertIn("HOOK-WARN shop on-report hook:project:on-report failed", r.stdout)
        self.assertEqual(r.send.returncode, 0, "a hook only warns")
        got = facts(out)
        self.assertEqual((got["LANE_HOOK_OUTCOME"], got["LANE_HOOK_PR"], got["LANE_HOOK_PROJECT"]),
                         ("escalation", "12", "shop"))
        self.assertEqual(got["LANE_HOOK_PROJECT_ROOT"], str(self.shop))

    def test_a_nudge_runs_no_hook(self):
        out = store_hook(self.env, self.shop, self.tmp / "owner", "on-report")
        self.build_lane(live=False)
        self.assertIn("SENT NUDGE", self.watch(50).stdout)
        self.assertFalse(out.exists())


class AfterSetupTests(unittest.TestCase):
    """project-setup on native Orca against the stub orca (test_extras' RefreshTests): after-setup runs last."""

    def setUp(self):
        from test_project_setup_native import NativeEnv
        self.e = NativeEnv()
        self.addCleanup(self.e.close)
        self.repo = self.e.repo()
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        patch = mock.patch.dict(os.environ, self.e.env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.owner = self.e.home / "owner"

    def test_after_setup_runs_at_the_end_of_refresh_and_setup_and_a_failure_fails_them(self):
        out = store_hook(self.e.env, self.repo, self.owner, "after-setup")
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("hook after-setup: hook:project:after-setup ok", r.stdout)
        got = facts(out)
        self.assertEqual((got["LANE_HOOK_SETUP"], got["LANE_HOOK_PROJECT_ROOT"]), ("refresh", str(self.repo)))
        self.assertTrue(got["LANE_HOOK_PROJECT"])
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(facts(out)["LANE_HOOK_SETUP"], "setup")
        store_hook(self.e.env, self.repo, self.owner, "after-setup", code=9, name="fails")
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("hook:project:fails failed: exit 9", r.stdout)
        self.assertIn("nothing is undone", r.stdout)

    def test_a_git_hook_is_fetched_by_refresh_and_checked(self):
        up = self.e.home / "up"
        up.mkdir()
        (up / "h.sh").write_text("#!/bin/sh\nexit 0\n")
        (up / "h.sh").chmod(0o755)
        git("init", "-q", "-b", "main", cwd=up)
        git("add", "-A", cwd=up)
        git("commit", "-q", "-m", "v1", cwd=up)
        sha = git("rev-parse", "HEAD", cwd=up)
        with mock.patch.object(LE, "person", lambda what: None), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(LE.main(["project", "add", str(self.repo), "--hook", "gate", "--script",
                                      f"file://{up}@{sha}:h.sh"]), 0)
        e = [x for x in X.effective(X.store_for(self.repo)) if x["kind"] == "hook"][0]
        shutil.rmtree(X.hook_source_dir(e))
        r = self.e.run("--check", str(self.repo), adapt=True)
        self.assertIn("check extra hook:project:gate-h: missing", r.stdout)
        self.assertIn("at gate", r.stdout)
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        r = self.e.run("--check", str(self.repo), adapt=True)
        self.assertIn("check extra hook:project:gate-h: ok", r.stdout)


class FrontDoorTests(unittest.TestCase):
    def test_gate_and_hook_run_lane_extras(self):
        self.assertEqual(F.route(["project", "gate", "app", "7"]), ("lane-extras", ["gate", "app", "7"]))
        self.assertEqual(F.route(["project", "hook", "app", "on-report", "--lane", "A-1"]),
                         ("lane-extras", ["hook", "app", "on-report", "--lane", "A-1"]))


if __name__ == "__main__":
    unittest.main()
