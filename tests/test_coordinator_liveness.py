"""Coordinator liveness from the field report (lc-l78): a standing session runs only when an agent process does
(lc-l78.1), and each coordinator's folder carries a persona anchor (lc-l78.2). Stubs and a fake /proc
(LANE_PROC_DIR) in a temporary HOME."""
import json
import os
import shutil
import unittest

from test_project_setup import git
from test_project_setup_native import Case, opt

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

    def test_an_agent_in_another_terminal_or_of_another_role_does_not_count(self):
        self.setup_project()
        self.restart_server()
        handle = self.e.settings(self.repo)["terminals"]["coordinator"]
        self.process(4001, ["claude"], {"ORCA_TERMINAL_HANDLE": "term_other"})
        self.process(4002, ["claude"], {"ORCA_TERMINAL_HANDLE": handle, "LANE_ROLE": "debugger"})
        self.assertIn("sessions running: none", self.e.pause("status", str(self.repo)).stdout)

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
    def test_an_unreadable_agent_of_another_session_does_not_make_a_dead_one_run(self):
        self.setup_project()
        self.restart_server()
        self.unreadable(4001, ["claude", "--dangerously-skip-permissions", "--resume", "x"])
        self.unreadable(4002, ["claude", "--name", "shop coordinator"])
        r = self.e.pause("start", str(self.repo))
        self.assertIn("recorded but dead", r.stdout, r.stderr)
        self.assertEqual(len(self.started()), 2)

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

    def test_without_a_process_list_orcas_mark_is_kept_so_nothing_starts_twice(self):
        self.setup_project()
        r = self.e.pause("start", str(self.repo), env_extra={"LANE_PROC_DIR": str(self.e.home / "no-proc")})
        self.assertIn("session coordinator: exists", r.stdout, r.stderr)
        self.assertIn("could not check", r.stdout)
        self.assertEqual(len(self.started()), 1)


if __name__ == "__main__":
    unittest.main()
