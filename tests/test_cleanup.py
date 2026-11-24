"""core/cleanup.py: what Orca's workspace cleanup preview says about the merged rule (report only), and GitHub's
"Automatically delete head branches" (recommended; set unless the owner says no). Orca's CLI and gh are stub executables."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
import cleanup  # noqa: E402

REPO_ID = "r-app"


def row(name, repo="app", repo_id=REPO_ID, path=None, action="keep", cause=None, reason=None):
    ev = {"action": action}
    if cause:
        ev["cause"] = cause
    if reason:
        ev["reason"] = reason
    return {"worktreeId": f"{repo_id}::{path or name}", "repoId": repo_id, "repoName": repo, "displayName": name,
            "branch": name, "path": path or f"/w/{repo}/{name}", "evaluation": ev}


def main_row(repo="app", repo_id=REPO_ID, path="/src/app"):
    return row("main", repo=repo, repo_id=repo_id, path=path, cause="not-removable")


class Stubs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.repo = self.dir / "src" / "app"
        self.repo.mkdir(parents=True)

    def script(self, name, body):
        p = self.dir / name
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
        return str(p)

    def orca(self, rows=None, raw=None, code=0):
        """An Orca CLI that answers `workspace cleanup preview --json` with these rows (or raw text)."""
        out = raw if raw is not None else json.dumps({"id": "x", "ok": True, "result": {"evaluatedAt": 1, "rows": rows}})
        (self.dir / "preview.json").write_text(out)
        return self.script("orca-ide", f'echo "$@" >> "{self.dir}/orca.log"\ncat "{self.dir}/preview.json"\nexit {code}\n')

    def orca_calls(self):
        f = self.dir / "orca.log"
        return f.read_text().splitlines() if f.exists() else []


class OrcaRulesTests(Stubs):
    def project(self, rows, **kw):
        """The merged-rule line(s) only; `workers` below has the released-workers rule's."""
        return [l for l in cleanup.orca_report(self.orca(rows, **kw), str(self.repo)) if l[0] != cleanup.WORKERS_STEP]

    def workers(self, rows, **kw):
        return [l for l in cleanup.orca_report(self.orca(rows, **kw), str(self.repo)) if l[0] == cleanup.WORKERS_STEP]

    def test_it_only_runs_the_preview_which_removes_nothing(self):
        self.project([main_row(path=str(self.repo))])
        self.assertEqual(self.orca_calls(), ["workspace cleanup preview --json"])

    def test_rules_off_says_where_to_turn_on_the_merged_rule(self):
        (step, what, detail), = self.project([main_row(path=str(self.repo)),
                                                row("lane-1", cause="rules-off")])
        self.assertEqual((step, what), ("orca cleanup", "off"))
        self.assertIn("Settings -> Workspace Cleanup -> Cleanup rules -> 'Delete merged workspaces'", detail)
        self.assertIn("no cleanup rule is on", detail)

    def test_project_off_says_where_the_project_has_its_own_setting(self):
        (step, what, detail), = self.project([main_row(path=str(self.repo)), row("lane-1", cause="project-off")])
        self.assertEqual(what, "off")
        self.assertIn("off for this project", detail)
        self.assertIn("'Workspace cleanup'", detail)
        self.assertIn("'Use global'", detail)

    def test_a_row_the_merged_rule_matched_proves_it_is_on(self):
        (step, what, detail), = self.project([main_row(path=str(self.repo)),
                                                row("lane-1", action="delete", reason="merged")])
        self.assertEqual(what, "exists")
        self.assertIn("merged", detail)

    def test_a_rule_that_is_on_but_not_shown_to_be_the_merged_one_says_check_not_exists(self):
        (step, what, detail), = self.project([main_row(path=str(self.repo)), row("lane-1", cause="no-rule-matched")])
        self.assertEqual(what, "check")
        self.assertIn("'Delete merged workspaces'", detail)  # the owner is told which one to look for

    def test_only_the_projects_own_rows_count(self):
        rows = [main_row(path=str(self.repo)), row("lane-1", cause="no-rule-matched"),
                row("lane-9", repo="other", repo_id="r-other", cause="rules-off")]
        (step, what, detail), = self.project(rows)
        self.assertEqual(what, "check")

    def test_a_project_with_only_its_main_workspace_cannot_be_read(self):
        # Orca keeps a main workspace (cause not-removable) before it looks at any rule
        (step, what, detail), = self.project([main_row(path=str(self.repo))])
        self.assertEqual(what, "unknown")
        self.assertIn("lane", detail)

    def test_a_project_orca_does_not_list_is_unknown(self):
        (step, what, detail), = self.project([main_row(repo="other", repo_id="r-other", path="/src/other")])
        self.assertEqual(what, "unknown")
        self.assertIn("not in Orca", detail)

    def test_a_missing_cli_is_skipped_not_a_crash(self):
        (step, what, detail), = cleanup.orca_report(str(self.dir / "no-such-orca"), str(self.repo))
        self.assertEqual((step, what), ("orca cleanup", "skipped"))

    def test_a_failing_cli_is_skipped_with_its_message(self):
        raw = json.dumps({"ok": False, "error": {"message": "runtime not reachable"}})
        (step, what, detail), = self.project(None, raw=raw, code=1)
        self.assertEqual(what, "skipped")
        self.assertIn("runtime not reachable", detail)

    def test_unreadable_output_is_skipped(self):
        (step, what, detail), = self.project(None, raw="not json")
        self.assertEqual(what, "skipped")

    def test_an_older_orca_without_the_command_is_skipped(self):
        (step, what, detail), = self.project(None, raw="unknown command: workspace", code=1)
        self.assertEqual(what, "skipped")

    def test_a_hung_cli_is_skipped(self):
        slow = self.script("slow-orca", "sleep 5\n")
        (step, what, detail), = cleanup.orca_report(slow, str(self.repo), timeout=0.2)
        self.assertEqual(what, "skipped")
        self.assertIn("timed out", detail)

    def test_off_and_check_are_the_problems_to_read_unknown_and_skipped_are_not(self):
        self.assertTrue({"off", "check"} <= cleanup.PROBLEMS)
        self.assertFalse({"exists", "skipped", "unknown"} & cleanup.PROBLEMS)

    def test_a_row_without_an_evaluation_is_not_a_rule_that_is_on(self):
        bare = {"repoId": REPO_ID, "repoName": "app", "path": "/w/app/lane-1"}
        (step, what, detail), = self.project([main_row(path=str(self.repo)), bare])
        self.assertEqual(what, "unknown")


WORKERS = "Settings -> Workspace Cleanup -> Cleanup rules -> 'Delete released orchestration worker workspaces'"


class ReleasedWorkersRuleTests(Stubs):
    """The recommended 'Delete released orchestration worker workspaces' rule: report only, and plain about what the
    preview cannot show (a row says `reason: orchestration-worker` only when that rule matched it)."""

    def project(self, rows):
        return [l for l in cleanup.orca_report(self.orca(rows), str(self.repo)) if l[0] == cleanup.WORKERS_STEP]

    def test_a_row_the_rule_matched_proves_it_is_on(self):
        (step, what, detail), = self.project([main_row(path=str(self.repo)),
                                                row("lane-1", action="delete", reason="orchestration-worker")])
        self.assertEqual((step, what), ("orca cleanup workers", "exists"))
        self.assertIn("released-workers rule is on", detail)

    def test_a_kept_row_with_that_reason_proves_it_too(self):
        (step, what, detail), = self.project([main_row(path=str(self.repo)),
                                                row("lane-1", cause="recently-active", reason="orchestration-worker")])
        self.assertEqual(what, "exists")

    def test_rules_off_says_it_is_off_recommended_and_where(self):
        (step, what, detail), = self.project([main_row(path=str(self.repo)), row("lane-1", cause="rules-off")])
        self.assertEqual(what, "off")
        self.assertIn("recommended", detail)
        self.assertIn(WORKERS, detail)
        self.assertIn("released only after its lane's merge", detail)
        self.assertIn("lane-control does not change Orca's settings", detail)

    def test_project_off_names_the_project_page_then_the_rule(self):
        (step, what, detail), = self.project([main_row(path=str(self.repo)), row("lane-1", cause="project-off")])
        self.assertEqual(what, "off")
        self.assertIn("'Use global'", detail)
        self.assertIn(WORKERS, detail)

    def test_with_a_rule_on_but_no_released_worker_row_it_says_it_cannot_tell(self):
        # a merged row hides which rule matched; an unmatched row shows no rule at all
        for r in (row("lane-1", action="delete", reason="merged"), row("lane-1", cause="no-rule-matched")):
            (step, what, detail), = self.project([main_row(path=str(self.repo)), r])
            self.assertEqual(what, "check")
            self.assertIn("cannot tell", detail)
            self.assertIn("recommended", detail)
            self.assertIn(f"Turn it on at {WORKERS}", detail)

    def test_a_project_with_only_its_main_workspace_cannot_tell_either(self):
        (step, what, detail), = self.project([main_row(path=str(self.repo))])
        self.assertEqual(what, "check")
        self.assertIn("cannot tell", detail)

    def test_only_the_projects_own_rows_count(self):
        rows = [main_row(path=str(self.repo)), row("lane-1", cause="no-rule-matched"),
                row("lane-9", repo="other", repo_id="r-other", action="delete", reason="orchestration-worker")]
        (step, what, detail), = self.project(rows)
        self.assertEqual(what, "check")

    def test_it_is_a_problem_to_read_when_off_or_unsure_not_when_on(self):
        self.assertTrue({"off", "check"} <= cleanup.PROBLEMS)
        self.assertNotIn("exists", cleanup.PROBLEMS)

    def test_the_preview_is_read_once_and_nothing_else_runs(self):
        cleanup.orca_report(self.orca([main_row(path=str(self.repo)), row("lane-1", cause="rules-off")]),
                            str(self.repo))
        self.assertEqual(self.orca_calls(), ["workspace cleanup preview --json"])

    def test_an_unreadable_preview_is_one_skipped_line_naming_both_rules(self):
        lines = cleanup.orca_report(self.orca(raw="boom", code=2), str(self.repo))
        (step, what, detail), = lines
        self.assertEqual(what, "skipped")
        self.assertIn("'Delete merged workspaces'", detail)
        self.assertIn(WORKERS, detail)

    def test_machine_wide_it_names_the_projects_per_state(self):
        rows = [row("l1", "a", "r-a", action="delete", reason="orchestration-worker"),
                row("l2", "b", "r-b", cause="rules-off"), row("l3", "c", "r-c", cause="project-off"),
                row("l4", "d", "r-d", cause="no-rule-matched")]
        lines = [l for l in cleanup.orca_report(self.orca(rows)) if l[0] == cleanup.WORKERS_STEP]
        self.assertEqual(sorted(w for _, w, _ in lines), ["check", "exists", "off", "off"])
        text = {w: d for _, w, d in lines if w != "off"}
        self.assertIn("projects: a", text["exists"])
        self.assertIn("for d", text["check"])
        offs = [d for _, w, d in lines if w == "off"]
        self.assertTrue(any("for b" in d for d in offs) and any("for c" in d for d in offs))

    def test_machine_wide_with_no_project_at_all_still_says_it_cannot_tell(self):
        (step, what, detail), = cleanup.orca_report(self.orca([]), None)[-1:]
        self.assertEqual((step, what), ("orca cleanup workers", "check"))


class MachineWideTests(Stubs):
    def machine(self, rows, **kw):
        return [l for l in cleanup.orca_report(self.orca(rows, **kw), None) if l[0] != cleanup.WORKERS_STEP]

    def workers(self, rows, **kw):
        return [l for l in cleanup.orca_report(self.orca(rows, **kw), None) if l[0] == cleanup.WORKERS_STEP]

    def test_every_project_off_is_one_off_line_with_the_where(self):
        rows = [main_row(), row("lane-1", cause="rules-off"),
                main_row("b", "r-b", "/src/b"), row("lane-2", "b", "r-b", cause="rules-off")]
        (step, what, detail), = self.machine(rows)
        self.assertEqual(what, "off")
        self.assertIn("Settings -> Workspace Cleanup -> Cleanup rules -> 'Delete merged workspaces'", detail)
        self.assertIn("app", detail)
        self.assertIn("b", detail)

    def test_a_project_set_off_by_itself_is_named(self):
        rows = [row("lane-1", cause="no-rule-matched"), row("lane-2", "b", "r-b", cause="project-off")]
        lines = self.machine(rows)
        off = [d for _, w, d in lines if w == "off"]
        self.assertEqual(len(off), 1)
        self.assertIn("b", off[0])
        self.assertIn("'Use global'", off[0])
        self.assertIn("check", [w for _, w, _ in lines])

    def test_all_on_is_exists(self):
        lines = self.machine([row("lane-1", action="delete", reason="merged")])
        self.assertEqual([w for _, w, _ in lines], ["exists"])

    def test_a_main_only_project_is_named_beside_the_other_lines(self):
        rows = [main_row("a", "r-a", "/src/a"), row("lane-1", "a", "r-a", cause="rules-off"),
                main_row("b", "r-b", "/src/b")]
        lines = self.machine(rows)
        unknown = [d for _, w, d in lines if w == "unknown"]
        self.assertEqual(len(unknown), 1)
        self.assertIn("b", unknown[0])
        self.assertNotIn("a,", unknown[0])
        self.assertEqual([w for _, w, _ in lines].count("off"), 1)

    def test_no_lane_worktree_anywhere_is_unknown(self):
        (step, what, detail), = self.machine([main_row()])
        self.assertEqual(what, "unknown")

    def test_a_failing_cli_is_skipped(self):
        (step, what, detail), = self.machine(None, raw="boom", code=2)
        self.assertEqual(what, "skipped")


class GithubTests(Stubs):
    """gh stub: `repo view` prints $STUB_VIEW (exit 1 when unset); `repo edit` logs and exits $STUB_EDIT_EXIT."""

    def gh(self, view=None, edit_exit=0):
        body = (f'echo "$@" >> "{self.dir}/gh.log"\n'
                'case "$1 $2" in\n'
                f'  "repo view") {"echo " + view if view is not None else "exit 1"} ;;\n'
                f'  "repo edit") echo "stub: gh edit failed (HTTP 403)" >&2; exit {edit_exit} ;;\n'
                'esac\n')
        return self.script("gh", body)

    def calls(self):
        f = self.dir / "gh.log"
        return f.read_text().splitlines() if f.exists() else []

    def report(self, gh, yes=None):
        return cleanup.github_report(self.repo, yes, gh=gh)

    def test_already_on_is_exists_and_nothing_is_asked_or_changed(self):
        asked = []
        (step, what, detail), = self.report(self.gh("true"), lambda q: asked.append(q) or True)
        self.assertEqual((step, what), ("github", "exists"))
        self.assertEqual(asked, [])
        self.assertEqual([c for c in self.calls() if c.startswith("repo edit")], [])

    def test_off_with_the_owners_yes_turns_it_on(self):
        asked = []
        (step, what, detail), = self.report(self.gh("false"), lambda q: asked.append(q) or True)
        self.assertEqual(what, "created")
        self.assertEqual(len(asked), 1)
        self.assertIn("Automatically delete head branches", asked[0])
        self.assertIn("recommended", asked[0])
        self.assertTrue(asked[0].rstrip().endswith("[Y/n]"), asked[0])
        self.assertIn("repo edit --delete-branch-on-merge", self.calls())

    def test_off_with_the_owners_no_changes_nothing(self):
        (step, what, detail), = self.report(self.gh("false"), lambda q: False)
        self.assertEqual(what, "skipped")
        self.assertEqual([c for c in self.calls() if c.startswith("repo edit")], [])

    def test_without_a_terminal_it_only_says_so(self):
        (step, what, detail), = self.report(self.gh("false"), None)
        self.assertEqual(what, "missing")
        self.assertIn("gh repo edit --delete-branch-on-merge", detail)
        self.assertIn("recommended", detail)
        self.assertEqual([c for c in self.calls() if c.startswith("repo edit")], [])

    def test_a_refused_edit_is_a_warning_with_gh_s_message(self):
        (step, what, detail), = self.report(self.gh("false", edit_exit=1), lambda q: True)
        self.assertEqual(what, "warning")
        self.assertIn("403", detail)

    def test_gh_that_cannot_say_is_skipped_and_asks_nothing(self):
        asked = []
        (step, what, detail), = self.report(self.gh(None), lambda q: asked.append(q) or True)
        self.assertEqual(what, "skipped")
        self.assertEqual(asked, [])

    def test_no_gh_is_skipped(self):
        (step, what, detail), = self.report(str(self.dir / "no-such-gh"), None)
        self.assertEqual(what, "skipped")

    def test_unreadable_answer_is_skipped(self):
        (step, what, detail), = self.report(self.gh("maybe"), lambda q: True)
        self.assertEqual(what, "skipped")
        self.assertEqual([c for c in self.calls() if c.startswith("repo edit")], [])

    def test_the_view_reads_only_that_one_field_in_the_project(self):
        self.report(self.gh("true"))
        self.assertEqual(self.calls(), ["repo view --json deleteBranchOnMerge -q .deleteBranchOnMerge"])


if __name__ == "__main__":
    unittest.main()
