"""Coordinator liveness from the field report (lc-l78): a standing session runs only when an agent process does
(lc-l78.1), and each coordinator's folder carries a persona anchor (lc-l78.2). Stubs and a fake /proc
(LANE_PROC_DIR) in a temporary HOME."""
import json
import os
import re
import shutil
import unittest
from unittest import mock

from test_project_setup import git
from test_project_setup_native import Case, load, opt, session_record

SHELL_TERMINAL = {"handle": "term_1", "title": "✳ app coordinator", "agentIdentity": "claude"}


class Liveness(Case):
    def setup_project(self, *args):
        r = self.e.run(str(self.repo), *args)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return r

    def restart_server(self):
        """A server restart, as the field report saw it: the terminal is an empty shell, and Orca still marks it as an
        agent's (1.4.274 reads agentIdentity from the title, the launch or an old hook as well as from a process)."""
        shutil.rmtree(self.e.proc)
        self.e.proc.mkdir()

    def process(self, pid, argv, env, cwd=None):
        d = self.e.proc / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in argv))
        (d / "environ").write_bytes(b"".join(f"{k}={v}".encode() + b"\0" for k, v in env.items()))
        (d / "cwd").symlink_to(cwd or self.repo)

    def terminals(self):
        return json.loads(self.e.state.read_text())["terminals"]

    def started(self):
        return [x for x in self.e.sessions() if x["role"] == "coordinator"]

    def closes(self):
        return [c for c in self.e.calls() if c.startswith("orca terminal close")]

    def test_a_started_coordinator_is_running(self):
        self.setup_project()
        r = self.e.pause("start", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("session coordinator: exists", r.stdout)
        self.assertNotIn("recorded but dead", r.stdout)
        self.assertEqual(len(self.started()), 1)

    def test_start_after_a_server_restart_says_recorded_but_dead_and_starts_it(self):
        self.setup_project()
        old = self.e.settings(self.repo)
        self.restart_server()
        r = self.e.pause("start", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        line = next(l for l in r.stdout.splitlines() if l.startswith("session coordinator: recorded but dead"))
        self.assertIn(old["terminals"]["coordinator"], line)
        self.assertIn("session coordinator: created", r.stdout)
        self.assertEqual(len(self.started()), 2)
        new = self.e.settings(self.repo)
        self.assertNotEqual(new["terminals"]["coordinator"], old["terminals"]["coordinator"])
        self.assertNotEqual(new["runs"]["coordinator"], old["runs"]["coordinator"])
        self.assertIn(old["runs"]["coordinator"], new["old_runs"])
        self.assertEqual(self.closes(), [])  # the empty shell is named, never closed

    def test_a_title_and_orcas_agent_mark_alone_are_not_a_running_session(self):
        self.e.set_state(terminals=[{**SHELL_TERMINAL, "worktreePath": str(self.repo)}])
        r = self.setup_project()
        self.assertIn("recorded but dead", r.stdout)
        self.assertIn("orca-ide terminal close --terminal term_1", r.stdout)
        self.assertEqual(len(self.started()), 1)

    def test_a_record_whose_terminal_is_gone_is_said_too(self):
        self.setup_project()
        self.restart_server()
        self.e.set_state(terminals=[])
        r = self.e.pause("start", str(self.repo))
        self.assertIn("session coordinator: recorded but dead", r.stdout)
        self.assertEqual(len(self.started()), 2)

    def test_a_first_start_is_not_a_dead_session(self):
        self.assertNotIn("recorded but dead", self.setup_project().stdout)

    def test_status_says_recorded_but_dead_and_changes_nothing(self):
        self.setup_project()
        self.restart_server()
        before = self.e.settings(self.repo)
        r = self.e.pause("status", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("sessions running: none", r.stdout)
        self.assertIn("app coordinator: recorded but dead", r.stdout)
        self.assertIn("project-pause start", r.stdout)
        self.assertEqual(self.e.settings(self.repo), before)

    def test_stop_of_a_dead_session_clears_the_record_and_asks_nobody(self):
        self.setup_project()
        self.restart_server()
        old_run = self.e.settings(self.repo)["runs"]["coordinator"]
        r = self.e.pause("stop", str(self.repo), "--wait", "1")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("recorded but dead", r.stdout)
        st = self.e.settings(self.repo)
        self.assertEqual((st["terminals"], st["runs"], st["paused"]), ({}, {}, True))
        self.assertIn(old_run, st["old_runs"])
        self.assertNotIn("sent", json.loads(self.e.state.read_text()))
        self.assertEqual(self.closes(), [])

    def test_wake_of_a_dead_session_clears_the_record_and_stays_paused(self):
        self.setup_project()
        self.assertEqual(self.e.pause("rest", str(self.repo)).returncode, 0)
        self.restart_server()
        r = self.e.pause("wake", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("recorded but dead", r.stdout)
        self.assertIn("project-pause start", r.stdout)
        st = self.e.settings(self.repo)
        self.assertEqual((st["terminals"], st["paused"]), ({}, True))

    def test_a_start_after_a_stop_is_not_a_dead_session(self):
        self.setup_project()
        r = self.e.pause("stop", str(self.repo), "--no-handover")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        r = self.e.pause("start", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("recorded but dead", r.stdout)

    def test_an_agent_run_by_hand_in_the_recorded_terminal_counts(self):
        self.setup_project()
        self.restart_server()
        handle = self.e.settings(self.repo)["terminals"]["coordinator"]
        self.process(4001, ["claude", "--resume", "x"], {"ORCA_TERMINAL_HANDLE": handle})
        r = self.e.pause("start", str(self.repo))
        self.assertIn("session coordinator: exists", r.stdout, r.stderr)
        self.assertEqual(len(self.started()), 1)

    def test_stop_closes_the_terminal_the_agent_runs_in_not_a_dead_one_of_the_same_title(self):
        # the owner started it again by hand in a second tab; the recorded one is the empty shell
        self.setup_project()
        self.restart_server()
        shell = self.e.settings(self.repo)["terminals"]["coordinator"]
        second = {"handle": "term_55", "title": "app coordinator", "worktreePath": str(self.repo)}
        self.e.set_state(terminals=[*self.terminals(), second])
        self.process(4001, ["claude"], {"LANE_ROLE": "coordinator", "ORCA_TERMINAL_HANDLE": "term_55"})
        r = self.e.pause("stop", str(self.repo), "--no-handover")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual([t["handle"] for t in self.terminals()], [shell])

    def test_an_agent_in_another_terminal_or_of_another_role_does_not_count(self):
        self.setup_project()
        self.restart_server()
        handle = self.e.settings(self.repo)["terminals"]["coordinator"]
        self.process(4001, ["claude"], {"ORCA_TERMINAL_HANDLE": "term_other"}, cwd=self.e.home)
        self.process(4002, ["claude"], {"ORCA_TERMINAL_HANDLE": handle, "LANE_ROLE": "debugger"})
        self.assertIn("sessions running: none", self.e.pause("status", str(self.repo)).stdout)

    def test_an_unmarked_agent_in_the_checkout_but_another_terminal_is_a_doubt(self):
        # it may be the coordinator, started again by hand in a tab of another name
        self.setup_project()
        self.restart_server()
        self.process(4001, ["claude"], {"ORCA_TERMINAL_HANDLE": "term_other"})
        r = self.e.pause("start", str(self.repo))
        self.assertIn("cannot tell", r.stdout, r.stderr)
        self.assertEqual(len(self.started()), 1)

    def test_a_leftover_child_of_the_session_is_not_an_agent(self):
        # an MCP server or a dev server inherits the markers and can outlive the agent
        self.setup_project()
        self.restart_server()
        env = {"LANE_ROLE": "coordinator", "ORCA_TERMINAL_HANDLE": self.e.settings(self.repo)["terminals"]["coordinator"]}
        self.process(4001, ["node", "/opt/tools/claude-notes/server.js"], env)
        self.process(4002, ["bash", "-c", "sleep 600"], env)
        self.assertIn("sessions running: none", self.e.pause("status", str(self.repo)).stdout)

    def test_agents_installed_in_other_ways_are_recognised(self):
        self.setup_project()
        env = {"LANE_ROLE": "coordinator"}
        for argv in (["/nix/store/x/bin/.claude-wrapped"], ["opencode2", "--standalone"],
                     ["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"]):
            self.restart_server()
            self.process(4001, argv, env)
            self.assertIn("sessions running: app coordinator", self.e.pause("status", str(self.repo)).stdout, argv)

    def unreadable(self, pid, argv):
        """An agent of this user in another sandbox namespace (a second Orca, or one that outlived a restart): only
        its command line can be read."""
        self.process(pid, argv, {})
        (self.e.proc / str(pid) / "environ").chmod(0)

    @unittest.skipIf(os.geteuid() == 0, "root reads every file")
    def test_an_unreadable_agent_that_names_this_session_counts(self):
        self.setup_project()
        persona = self.repo / ".lane-control" / "coordinator.md"
        self.e.set_state(terminals=[{k: v for k, v in t.items() if k != "agentIdentity"} for t in self.terminals()])
        for argv in (["claude", "--model", "opus", "--name", "app coordinator"],
                     ["opencode2", "--standalone", "--prompt", f"Read your persona, {persona}, then AGENTS.md."],
                     ["opencode2", "--prompt", f"You are back after a pause. Reread your persona, {persona}; then"]):
            self.restart_server()
            self.unreadable(4001, argv)
            self.assertIn("sessions running: app coordinator", self.e.pause("status", str(self.repo)).stdout, argv)

    def test_without_a_process_list_it_cannot_tell_and_starts_nothing(self):
        self.setup_project()
        r = self.e.pause("start", str(self.repo), env_extra={"LANE_PROC_DIR": str(self.e.home / "no-proc")})
        self.assertIn("session coordinator: cannot tell", r.stdout, r.stderr)
        self.assertEqual((r.returncode, len(self.started())), (1, 1))


OWN, OTHER = "aaaa0000-1111-4222-8333-444455556666", "bbbb0000-1111-4222-8333-444455556666"


@unittest.skipIf(os.geteuid() == 0, "root reads every file")
class Restored(Case):
    """The shape the review saw on a real desktop (2026-11-26): after an Orca restore, a coordinator's agent runs as
    `claude --plugin-dir <dir> [--dangerously-skip-permissions] --resume <id>` under a second `orca-agents` tree, in
    another sandbox namespace. No --name, no persona in its command line, environment and working directory denied.
    Orca lists its terminal, titled and marked `claude`, with no process id. Every coordinator there looks like this."""

    setup_project = Liveness.setup_project
    process, terminals, started, closes = Liveness.process, Liveness.terminals, Liveness.started, Liveness.closes
    restart_server, unreadable = Liveness.restart_server, Liveness.unreadable

    def argv(self, sid):
        return ["claude", "--plugin-dir", "/x/orca/claude-agent-view/x/orca-agent-view",
                "--dangerously-skip-permissions", "--resume", sid]

    def transcript(self, folder, sid):
        """Claude keeps a session's transcript under the folder it runs in."""
        d = self.e.home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(folder.resolve()))
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sid}.jsonl").write_text("{}\n")

    def restore(self, sid=OWN, transcript=True, mark=True):
        """The set-up project's coordinator after the restore: the same terminal in Orca's list, a new process."""
        self.setup_project()
        self.restart_server()
        rows = [{**t, "title": "\u2733 app coordinator", "connected": True, "ptyId": "w::/x@@0000aaaa"}
                for t in self.terminals()]
        if not mark:
            rows = [{k: v for k, v in t.items() if k != "agentIdentity"} for t in rows]
        self.e.set_state(terminals=rows)
        self.unreadable(4101, self.argv(sid))
        if transcript:
            self.transcript(self.repo, sid)

    def record(self):
        st = self.e.settings(self.repo)
        return st["terminals"], st["runs"]

    def test_start_cannot_tell_and_starts_no_second_coordinator(self):
        self.restore()
        before = self.record()
        r = self.e.pause("start", str(self.repo))
        self.assertEqual(r.returncode, 1, r.stdout)
        line = next(l for l in r.stdout.splitlines() if l.startswith("session coordinator: cannot tell"))
        for needed in ("4101", "Nothing is started", before[0]["coordinator"], "empty shell"):
            self.assertIn(needed, line)
        self.assertNotIn("recorded but dead", r.stdout)
        self.assertEqual((len(self.started()), self.record()), (1, before))

    def test_setup_run_again_starts_nothing_either(self):
        self.restore()
        r = self.setup_project()
        self.assertIn("session coordinator: cannot tell", r.stdout)
        self.assertEqual(len(self.started()), 1)

    def test_doubt_starts_nothing_without_orcas_mark_or_a_transcript(self):
        for transcript, mark in ((True, False), (False, True), (False, False)):
            with self.subTest(transcript=transcript, mark=mark):
                self.setUp()
                self.restore(transcript=transcript, mark=mark)
                r = self.e.pause("start", str(self.repo))
                self.assertIn("cannot tell", r.stdout, r.stderr)
                self.assertEqual(len(self.started()), 1)

    def test_status_and_wake_say_cannot_tell_and_keep_the_record(self):
        self.restore()
        before = self.record()
        out = self.e.pause("status", str(self.repo)).stdout
        self.assertIn("app coordinator: cannot tell", out)
        self.assertNotIn("recorded but dead", out)
        self.assertNotIn("stopped", out)
        self.assertEqual(self.e.pause("rest", str(self.repo)).returncode, 0)
        r = self.e.pause("wake", str(self.repo))
        self.assertIn("cannot tell", r.stdout, r.stderr)
        self.assertEqual((self.record(), self.e.settings(self.repo)["paused"]), (before, False))

    def test_stop_asks_the_marked_terminal_for_its_handover_and_never_claims_nothing_ran(self):
        self.restore()
        r = self.e.pause("stop", str(self.repo), "--wait", "1")
        self.assertEqual(r.returncode, 1, r.stdout)  # asked, no new handover: everything stays
        self.assertNotIn("no standing session was running", r.stdout)
        self.assertEqual(len(json.loads(self.e.state.read_text())["sent"]), 1)
        self.assertEqual(self.closes(), [])
        self.e.set_state(handover_on_send={"handover-latest": "new"})
        r = self.e.pause("stop", str(self.repo), "--wait", "5")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.terminals(), [])

    def test_stop_with_no_marked_terminal_changes_nothing(self):
        self.restore(mark=False)
        before = self.record()
        r = self.e.pause("stop", str(self.repo), "--no-handover")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("cannot tell", r.stderr)
        self.assertIn("Nothing was changed", r.stderr)
        self.assertEqual((self.record(), self.closes()), (before, []))
        self.assertNotIn("paused", {k for k, v in self.e.settings(self.repo).items() if v})

    def test_the_recorded_session_id_proves_it_across_namespaces(self):
        self.restore()
        rec = session_record(self.e, self.repo, "coordinator", "claude")
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text(OWN + "\n")
        r = self.e.pause("start", str(self.repo))
        self.assertIn("session coordinator: exists", r.stdout, r.stderr)
        self.assertEqual((r.returncode, len(self.started())), (0, 1))

    def test_other_projects_restored_coordinators_do_not_hide_a_dead_one(self):
        # the field report's case on such a desktop: this shell is empty, the others' agents run
        self.setup_project()
        self.restart_server()
        for pid, name, sid in ((4102, "shop", OTHER), (4103, "blog", "cccc0000-1111-4222-8333-444455556666")):
            folder = self.e.home / "code" / name
            folder.mkdir(parents=True)
            self.transcript(folder, sid)
            self.unreadable(pid, self.argv(sid))
        r = self.e.pause("start", str(self.repo))
        self.assertIn("recorded but dead", r.stdout, r.stderr + r.stdout)
        self.assertEqual((r.returncode, len(self.started())), (0, 2))

    def test_an_unreadable_agent_that_names_another_session_is_ruled_out(self):
        self.setup_project()
        self.restart_server()
        self.unreadable(4002, ["claude", "--name", "shop coordinator"])
        self.unreadable(4003, ["opencode2", "--prompt", "Read your persona, /x/shop/.lane-control/coordinator.md, then"])
        r = self.e.pause("start", str(self.repo))
        self.assertIn("recorded but dead", r.stdout, r.stderr)
        self.assertEqual(len(self.started()), 2)

    def test_after_the_owner_closes_the_empty_shell_it_starts(self):
        # the way out of a doubt that is wrong: no terminal of Orca's can hold the coordinator any more
        self.restore()
        self.e.set_state(terminals=[])
        r = self.e.pause("start", str(self.repo))
        self.assertIn("recorded but dead", r.stdout, r.stderr)
        self.assertEqual((r.returncode, len(self.started())), (0, 2))

    def test_a_retitled_terminal_orca_marks_in_the_checkout_keeps_the_doubt(self):
        self.restore()
        self.e.set_state(terminals=[{**t, "title": "fish /x", "handle": "term_77"} for t in self.terminals()])
        r = self.e.pause("start", str(self.repo))
        self.assertIn("cannot tell", r.stdout, r.stderr)
        self.assertEqual(len(self.started()), 1)

    def test_a_folder_name_claude_would_cut_short_rules_nothing_out(self):
        # Claude shortens a long folder name, so a transcript not found under it proves nothing
        m = load()
        long = self.e.home / ("d" * 120) / ("e" * 120)
        long.mkdir(parents=True)
        self.transcript(self.e.home, OTHER)
        with mock.patch.dict(os.environ, self.e.env, clear=True):
            self.assertEqual(m.transcript_of(OTHER, self.repo), "elsewhere")
            self.assertEqual(m.transcript_of(OTHER, long), "unknown")
            self.assertEqual(m.transcript_of("not-an-id", self.repo), "unknown")
            self.assertEqual(m.transcript_of(OTHER, self.e.home), "here")


BEGIN, END = "<!-- lane-control:persona-anchor begin", "<!-- lane-control:persona-anchor end -->"


class Anchor(Case):
    """lc-l78.2: the persona hook says nothing without its markers, so a session started by hand came up as a general
    assistant. Claude reads CLAUDE.local.md in the coordinator's folder at every start."""

    def setUp(self):
        super().setUp()
        self.anchor = self.repo / "CLAUDE.local.md"
        self.persona = self.repo / ".lane-control" / "coordinator.md"

    def setup_project(self, *args, adapt=False):
        r = self.e.run(str(self.repo), *(args or ("--no-start",)), adapt=adapt)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return r

    def snapshot(self):
        return {str(p): p.read_bytes() for p in self.e.home.rglob("*")
                if p.is_file() and not p.is_symlink() and not p.name.startswith("stub.")}

    def test_setup_writes_a_small_anchor_that_names_the_persona_and_stays_out_of_git(self):
        r = self.setup_project()
        self.assertIn("persona anchor: created", r.stdout)
        text = self.anchor.read_text()
        for needed in (BEGIN, END, str(self.persona), "in full before anything else", "app coordinator",
                       str(self.repo)):
            self.assertIn(needed, text)
        self.assertLess(len(text), 1200)
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=self.repo), "")
        self.assertIn("persona anchor: exists", self.setup_project().stdout)

    def test_it_says_who_is_not_the_coordinator(self):
        self.setup_project()
        text = " ".join(self.anchor.read_text().split())
        for word in ("Task", "reviewer", "sub-agent", "headless", "another folder", "ignore this note"):
            self.assertIn(word, text)

    def test_the_owners_own_text_is_kept_and_a_changed_anchor_is_put_back(self):
        self.anchor.write_text("# Mine\nUse the dev database.\n")
        self.assertIn("persona anchor: updated", self.setup_project().stdout)
        text = self.anchor.read_text()
        self.assertTrue(text.startswith(BEGIN))
        self.assertTrue(text.endswith("# Mine\nUse the dev database.\n"), text)
        self.anchor.write_text(text.replace("in full before anything else", "sometime") + "More.\n")
        r = self.setup_project("--refresh", adapt=True)
        self.assertIn("persona anchor: updated", r.stdout)
        new = self.anchor.read_text()
        self.assertEqual((new.count(BEGIN), new.count(END)), (1, 1))
        self.assertIn("in full before anything else", new)
        self.assertTrue(new.endswith("Use the dev database.\nMore.\n"), new)

    def test_a_project_that_relies_on_agents_md_keeps_it(self):
        # Claude reads AGENTS.md by itself only while no CLAUDE.md or CLAUDE.local.md is there, so the anchor imports it
        (self.repo / "AGENTS.md").write_text("rules\n")
        self.setup_project()
        self.assertIn("\n@AGENTS.md\n", self.anchor.read_text())
        (self.repo / "CLAUDE.md").write_text("mine\n")
        self.setup_project()
        self.assertNotIn("@AGENTS.md", self.anchor.read_text())

    def test_without_an_agents_md_nothing_is_imported_and_the_persona_is_never_an_import(self):
        self.setup_project()
        self.assertNotIn("\n@", self.anchor.read_text())  # an import would load for every session in the folder

    def test_a_committed_file_or_a_link_is_left_alone_and_said(self):
        self.anchor.write_text("committed\n")
        git("add", "-f", "CLAUDE.local.md", cwd=self.repo)
        git("commit", "-q", "-m", "theirs", cwd=self.repo)
        r = self.setup_project()
        self.assertIn("persona anchor: skipped", r.stdout)
        self.assertIn("committed", r.stdout)
        self.assertEqual(self.anchor.read_text(), "committed\n")
        git("rm", "-q", "CLAUDE.local.md", cwd=self.repo)
        git("commit", "-q", "-m", "gone", cwd=self.repo)
        target = self.e.home / "elsewhere.md"
        target.write_text("shared\n")
        self.anchor.symlink_to(target)
        r = self.setup_project()
        self.assertIn("persona anchor: skipped", r.stdout)
        self.assertIn("link", r.stdout)
        self.assertEqual(target.read_text(), "shared\n")

    def test_a_half_removed_anchor_is_not_rewritten(self):
        self.anchor.write_text(BEGIN + " -->\nhalf\n")
        r = self.setup_project()
        self.assertIn("persona anchor: skipped", r.stdout)
        self.assertEqual(self.anchor.read_text(), BEGIN + " -->\nhalf\n")

    def test_check_reports_the_anchor_here_and_changes_nothing(self):
        self.setup_project()
        before = self.snapshot()
        r = self.e.run(str(self.repo), "--check", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("check persona: ok", r.stdout)
        self.assertIn("check persona anchor: ok", r.stdout)
        self.assertEqual(self.snapshot(), before)
        text = self.anchor.read_text()
        for broken, state in ((None, "missing"), (text.replace("in full", "partly"), "stale")):
            if broken is None:
                self.anchor.unlink()
            else:
                self.anchor.write_text(broken)
            before = self.snapshot()
            r = self.e.run(str(self.repo), "--check", adapt=True)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn(f"check persona anchor: {state}", r.stdout)
            self.assertIn("--refresh", r.stdout)
            self.assertEqual(self.snapshot(), before)

    def test_check_of_a_project_not_set_up_says_so(self):
        r = self.e.run(str(self.repo), "--check", adapt=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("check persona: missing", r.stdout)
        self.assertFalse(self.anchor.exists())
        self.assertFalse((self.e.store(self.repo) / "settings.json").exists())


if __name__ == "__main__":
    unittest.main()
