"""CLI-2 (lc-wja): spawn from the front door. `lane-control coordinator start|restart|status` uses LIV-1's live check
(running / recorded but dead / cannot tell) and never starts a second coordinator without a person's --force, which is
refused on `running` and recorded with how it was confirmed; restart closes a coordinator only on its own `ready to
stop <code>` reply; `lane-control lane new` is one request message to the coordinator's exactly-tied Run; `lane-control
lane list|status` reads Orca's worker list and shows each lane's own worker. Stub orca and a fake process table
(LANE_PROC_DIR) in a temporary HOME, as the liveness tests (tests/test_coordinator_liveness.py).

What only a person may do (lane new, --force) refuses whenever an agent may run it, and a test process has no
terminal: those paths' successes run in-process with project-setup's person check (agent_caller) patched, their
refusals as real subprocesses; agent_caller itself is tested on its own."""
import contextlib
import importlib.machinery
import importlib.util
import io
import itertools
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import test_coordinator_liveness as liveness  # a module, so its tests are not collected here a second time
import test_project_setup_native as native
from test_project_setup import Tty

ROOT = native.ROOT
SPAWN = ROOT / "core" / "scripts" / "lane-spawn"
CLI = ROOT / "core" / "scripts" / "lane-control"
AGENT_MARKS = ("LANE_ROLE", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "OPENCODE", "OPENCODE_PID", "CODEX_SANDBOX")
_n = itertools.count()


def helpers(c):
    """A test class's helpers without its tests: a TestCase bound in this module would run its tests a second time."""
    return type(c.__name__, (), {k: v for k, v in vars(c).items() if not k.startswith("test")})


L, R = helpers(liveness.Liveness), helpers(liveness.Restored)


def load(path: Path, name: str):
    loader = importlib.machinery.SourceFileLoader(f"{name}_{next(_n)}", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Spawn(native.Case):
    setup_project = L.setup_project
    process, terminals, started, closes = L.process, L.terminals, L.started, L.closes
    restart_server, unreadable = L.restart_server, L.unreadable
    restore, argv, transcript = R.restore, R.argv, R.transcript

    def env(self, extra=None):
        env = {k: v for k, v in self.e.env.items() if k not in AGENT_MARKS}  # the test's own agent session, if any
        env.update(extra or {})
        return {k: v for k, v in env.items() if v is not None}

    def spawn(self, *args, env_extra=None):
        return subprocess.run([sys.executable, str(SPAWN), *args], env=self.env(env_extra), capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, timeout=180)

    def as_person(self, path, name, args, answer="y", env_extra=None):
        """main() of a script in this process, as a person at a terminal: the person check passes, `answer` is what
        they type. (exit, stdout, stderr, the input mock)."""
        mod = load(path, name)
        ps = getattr(mod, "ps", mod)
        with mock.patch.dict(os.environ, self.env(env_extra), clear=True), \
                mock.patch.object(ps, "agent_caller", return_value=""), \
                mock.patch("sys.stdin", Tty()), \
                mock.patch("builtins.input", return_value=answer) as ask, \
                contextlib.redirect_stdout(Tty()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = mod.main(list(args))
        return code, out.getvalue(), err.getvalue(), ask

    def pause_person(self, *args, answer="y"):
        return self.as_person(ROOT / "core" / "scripts" / "project-pause", "pause_t", ["start", str(self.repo), *args],
                              answer)

    def spawn_person(self, *args, answer="y", env_extra=None):
        return self.as_person(SPAWN, "spawn_t", args, answer, env_extra)

    def state(self):
        return json.loads(self.e.state.read_text())

    def sent(self):
        return [m for m in self.state().get("sent", []) if not str(m.get("subject", "")).startswith("Re: ")]

    def forced(self):
        return self.e.settings(self.repo).get("forced_starts", [])


class PersonCheck(unittest.TestCase):
    """project-setup's agent_caller: every signal it has, and doubt refuses."""

    def setUp(self):
        self.m = native.load()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.proc = Path(tmp.name)

    def proc_entry(self, pid, argv, ppid):
        d = self.proc / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in argv))
        (d / "status").write_text(f"Name:\t{argv[0]}\nPPid:\t{ppid}\n")

    def ask(self, env=None, tty=True, place=None):
        return self.m.agent_caller(place, env={"ORCA_TERMINAL_HANDLE": "term_me", **(env or {})}, tty=tty,
                                   proc=str(self.proc), pid=500)

    def test_a_person_in_a_plain_orca_terminal_passes(self):
        self.proc_entry(500, ["/x/bin/fish"], 400)
        self.proc_entry(400, ["/x/orca-app/orca", "--type=pty-host"], 1)
        self.assertEqual(self.ask(env={"ORCA_AGENT_PANE": "1", "ORCA_AGENT_HOOK_PORT": "1"}), "")  # every Orca shell

    def test_each_agent_signal_refuses(self):
        self.proc_entry(500, ["/x/bin/fish"], 1)
        for k in AGENT_MARKS:
            self.assertIn(k, self.ask(env={k: "1"}))
        self.assertIn("no terminal", self.ask(tty=False))

    def test_an_agent_among_the_parents_refuses(self):
        self.proc_entry(500, ["/bin/bash", "-c", "lane-control lane new app x"], 400)
        self.proc_entry(400, ["claude", "--plugin-dir", "/x"], 300)
        self.proc_entry(300, ["/x/bin/fish"], 1)
        self.assertIn("parent process 400 is an agent (claude)", self.ask())

    def test_an_unreadable_process_tree_refuses(self):
        self.proc_entry(500, ["/x/bin/fish"], 400)  # 400 is not there
        self.assertIn("cannot tell a person runs it", self.ask())

    def test_orca_naming_the_terminal_a_workers_or_an_agents_refuses(self):
        self.proc_entry(500, ["/x/bin/fish"], 1)

        class Place:
            def __init__(self, terms, workers):
                self.answers = {"terminal": {"result": {"terminals": terms}},
                                "orchestration": {"result": {"workers": workers}}}

            def jrun(self, *args):
                return self.answers[args[0]]
        self.assertEqual(self.ask(place=Place([{"handle": "term_me"}], [])), "")
        self.assertIn("worker", self.ask(place=Place([], [{"terminal": "term_me"}])))
        self.assertIn("plain terminal", self.ask(place=Place([{"handle": "term_me", "agentIdentity": "claude"}], [])))


@unittest.skipIf(os.geteuid() == 0, "root reads every file")
class CoordinatorStart(Spawn):
    def test_a_running_coordinator_is_said_and_nothing_starts(self):
        self.setup_project()
        r = self.spawn("coordinator", "start", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("session coordinator: exists", r.stdout)
        self.assertEqual(len(self.started()), 1)

    def test_recorded_but_dead_starts_it(self):
        self.setup_project()
        self.restart_server()
        r = self.spawn("coordinator", "start", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("recorded but dead", r.stdout)
        self.assertEqual((len(self.started()), self.forced()), (2, []))

    def test_cannot_tell_starts_no_second_coordinator_without_force(self):
        self.restore()
        before = self.e.settings(self.repo)["terminals"]
        r = self.spawn("coordinator", "start", "app")  # by its name, as the owner types it
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("cannot tell", r.stdout)
        self.assertIn("4101", r.stdout)  # what it found: the process it could not place
        self.assertEqual((len(self.started()), self.e.settings(self.repo)["terminals"]), (1, before))
        self.assertEqual(self.forced(), [])

    def test_force_is_refused_when_an_agent_may_run_it(self):
        # review B2/M4: a worker has no LANE_ROLE; an agent's marker, no terminal or an agent parent each refuse
        self.restore()
        for extra in ({"CLAUDECODE": "1"}, {"LANE_ROLE": "machine-coordinator"}, {}):
            for yes in ((), ("--yes",)):
                r = self.spawn("coordinator", "start", str(self.repo), "--force", *yes, env_extra=extra)
                self.assertEqual(r.returncode, 1, (extra, r.stdout))
                self.assertIn("is for a person at a terminal", r.stderr)
                self.assertIn("Nothing was started", r.stderr)
        self.assertEqual((len(self.started()), self.forced()), (1, []))

    def test_force_yes_starts_it_and_records_how_it_was_confirmed(self):
        self.restore()
        old = self.e.settings(self.repo)
        code, out, err, ask = self.pause_person("--force", "--yes")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(ask.call_count, 0)  # --yes: not asked
        found = next(l for l in out.splitlines() if l.strip().startswith("found:"))
        for needed in (old["terminals"]["coordinator"], "pid 4101", "--resume"):  # the terminal and the process
            self.assertIn(needed, found)
        self.assertIn("session coordinator: forced", out)
        self.assertNotIn("recorded but dead", out)
        self.assertEqual(len(self.started()), 2)
        (rec,) = self.forced()
        new = self.e.settings(self.repo)
        self.assertEqual((rec["role"], rec["confirmed"], rec["from_terminal"]), ("coordinator", "--yes", "term_owner"))
        self.assertEqual(rec["outcome"], f"started in {new['terminals']['coordinator']}")
        self.assertNotIn("by", rec)  # it never claims who typed it
        self.assertIn("4101", rec["doubt"])
        self.assertEqual(rec["terminals"], [old["terminals"]["coordinator"]])
        self.assertRegex(rec["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertIn(old["runs"]["coordinator"], new["old_runs"])
        self.assertEqual(self.closes(), [])  # what it could not place is left open, never closed for the owner

    def test_a_forced_start_that_fails_says_so_and_keeps_the_old_record(self):
        # review m4: recorded after the terminal exists, with what came of it
        self.restore()
        old = self.e.settings(self.repo)
        self.e.set_state(create_fails=True)
        code, out, err, _ = self.pause_person("--force", "--yes")
        self.assertEqual(code, 1, out + err)
        (rec,) = self.forced()
        self.assertTrue(rec["outcome"].startswith("failed"), rec)
        st = self.e.settings(self.repo)
        self.assertEqual((st["terminals"], st["runs"]), (old["terminals"], old["runs"]))

    def test_force_is_refused_on_a_running_coordinator(self):
        self.setup_project()
        for extra in ((), ("--yes",)):
            code, out, err, _ = self.pause_person("--force", *extra)
            self.assertEqual(code, 1, out)
            self.assertIn("never starts a second one", err)
            self.assertIn("restart", err)
            self.assertEqual((len(self.started()), self.forced()), (1, []))

    def test_force_on_a_dead_coordinator_is_not_needed_and_records_nothing(self):
        self.setup_project()
        self.restart_server()
        code, out, err, _ = self.pause_person("--force")
        self.assertEqual(code, 0, out + err)
        self.assertIn("force: not needed", out)
        self.assertEqual((len(self.started()), self.forced()), (2, []))

    def test_yes_alone_is_a_usage_error(self):
        self.restore()
        self.assertEqual(self.spawn("coordinator", "start", str(self.repo), "--yes").returncode, 2)
        self.assertEqual(self.e.pause("start", str(self.repo), "--yes").returncode, 2)
        self.assertEqual(len(self.started()), 1)

    def test_force_asks_once_in_a_terminal(self):
        self.restore()
        code, out, err, ask = self.pause_person("--force", answer="n")
        self.assertEqual(code, 1, out + err)
        self.assertEqual(ask.call_count, 1)
        self.assertIn("second app coordinator", ask.call_args[0][0])
        self.assertIn("pid 4101", out)
        self.assertEqual((len(self.started()), self.forced()), (1, []))
        code, out, err, ask = self.pause_person("--force", answer="y")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(ask.call_count, 1)
        self.assertEqual((len(self.started()), [r["confirmed"] for r in self.forced()]), (2, ["y typed in a terminal"]))

    def test_status_says_what_the_check_found(self):
        self.restore()
        r = self.spawn("coordinator", "status", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("cannot tell", r.stdout)

    def test_the_front_door_runs_it(self):
        self.setup_project()
        r = subprocess.run([str(CLI), "coordinator", "start", str(self.repo)], env=self.env(), capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("session coordinator: exists", r.stdout)
        self.assertEqual(len(self.started()), 1)

    def test_options_are_never_abbreviated_and_waits_are_finite(self):
        # review n1, n2: `--o host` would reach the box as `--o host`; --wait nan or inf would wait forever
        for args in (["coordinator", "status", "app", "--o", "trial-vps"],
                     ["coordinator", "restart", "app", "--wait", "nan"],
                     ["coordinator", "restart", "app", "--wait", "inf"],
                     ["coordinator", "restart", "app", "--wait", "-1"]):
            self.assertEqual(self.spawn(*args).returncode, 2, args)


class Restart(Spawn):
    READY = {"lane-control restart": "ready to stop {code}"}

    def restart(self, *args, env_extra=None):
        return self.spawn("coordinator", "restart", str(self.repo), *args, env_extra=env_extra)

    def cancels(self):
        return [m for m in self.sent() if m["subject"].endswith(": carry on")]

    def test_restart_closes_it_only_after_its_own_ready_reply_then_starts_it(self):
        self.setup_project()
        run = self.e.settings(self.repo)["runs"]["coordinator"]
        old = self.e.settings(self.repo)["terminals"]["coordinator"]
        self.e.set_state(replies=self.READY)
        r = self.restart("--wait", "5")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        (order,) = self.sent()
        self.assertEqual(order["to"], f"run:{run}")
        code = order["subject"].split()[2].rstrip(":")
        for needed in (f"ready to stop {code}", "not now: <why>", "merge", "very last step", "expires at",
                       f"carry on {code}", "handover-latest", "bd dolt push"):
            self.assertIn(needed, order["body"])
        self.assertLess(order["body"].index("handover-latest"), order["body"].index("bd dolt push"))  # review m3
        steps = [l.split(":")[0] for l in r.stdout.splitlines() if l.startswith("restart ")]
        self.assertEqual(steps, ["restart 1/4 asked", "restart 2/4 ready", "restart 3/4 stop", "restart 4/4 start"])
        self.assertEqual(self.closes(), [f"orca terminal close --terminal {old} --json"])
        self.assertEqual((len(self.started()), self.cancels()), (2, []))
        self.assertFalse(self.e.settings(self.repo).get("paused"))

    def test_a_handover_change_alone_never_closes_it(self):
        # review B1/M1: beads is never read for readiness; a failing bd changes nothing either
        self.setup_project()
        (self.e.bin / "bd").write_text("#!/bin/sh\nexit 1\n")
        self.e.set_state(handover_on_send={"handover-latest": "new"})
        r = self.restart("--wait", "1")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("no reply", r.stderr)
        self.assertEqual((len(self.started()), self.closes()), (1, []))

    def test_no_reply_in_time_closes_nothing_and_calls_the_order_off(self):
        self.setup_project()
        before = self.e.settings(self.repo)["terminals"]
        r = self.restart("--wait", "1")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("nothing was stopped or started", r.stderr)
        self.assertIn("carry on", r.stderr)
        (order, cancel) = self.sent()
        self.assertEqual((cancel["to"], cancel["subject"]), (order["to"], order["subject"].split(":")[0] + ": carry on"))
        self.assertEqual((len(self.started()), self.closes()), (1, []))
        self.assertEqual(self.e.settings(self.repo)["terminals"], before)

    def test_not_now_is_shown_and_nothing_is_stopped(self):
        self.setup_project()
        self.e.set_state(replies={"lane-control restart": "not now: merging PR 7"})
        r = self.restart("--wait", "5")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("not now: merging PR 7", r.stderr)
        self.assertEqual((len(self.started()), self.closes(), self.cancels()), (1, [], []))

    def test_any_other_answer_or_another_code_calls_it_off(self):
        for answer in ("ok", "ready to stop 000000000000"):
            with self.subTest(answer=answer):
                self.setUp()
                self.setup_project()
                self.e.set_state(replies={"lane-control restart": answer})
                r = self.restart("--wait", "1")
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertEqual((len(self.started()), self.closes(), len(self.cancels())), (1, [], 1))

    def test_a_reply_from_anyone_else_is_no_answer(self):
        self.setup_project()
        self.e.set_state(replies=self.READY, reply_from="term_lane")
        r = self.restart("--wait", "1")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual((len(self.started()), self.closes(), len(self.cancels())), (1, [], 1))

    def test_a_lane_started_while_it_got_ready_calls_it_off(self):
        self.setup_project()
        run = self.e.settings(self.repo)["runs"]["coordinator"]
        self.e.set_state(replies=self.READY, workers_on_reply=[
            {"dispatchId": "ctx_9", "runId": run, "liveness": "live", "outcome": "in_progress"}])
        r = self.restart("--wait", "5")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("ctx_9", r.stderr)
        self.assertEqual((len(self.started()), self.closes(), len(self.cancels())), (1, [], 1))

    def test_a_running_lane_here_or_on_a_box_refuses_before_anything_is_sent(self):
        self.setup_project()
        rid = self.state()["repos"][0]["id"]
        lane = self.e.home / "orca" / "workspaces" / "app-1"
        lane.mkdir(parents=True)
        self.e.set_state(worktrees=[{"repo": rid, "name": "app-1", "path": str(lane), "parent": None}],
                         terminals=[*self.terminals(), {"handle": "term_lane", "title": "lane",
                                                        "worktreePath": str(lane), "agentIdentity": "claude"}])
        r = self.restart("--wait", "1")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("app-1", r.stderr)
        self.e.set_state(worktrees=[], terminals=[t for t in self.terminals() if t["handle"] != "term_lane"],
                         workers=[{"dispatchId": "ctx_box", "runId": self.e.settings(self.repo)["runs"]["coordinator"],
                                   "liveness": "live", "outcome": "in_progress", "host": "trial-vps"}])
        r = self.restart("--wait", "1")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("ctx_box (trial-vps, live)", r.stderr)  # review m5
        self.e.set_state(workers=[], worker_list_fails=True)
        r = self.restart("--wait", "1")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("worker list", r.stderr)
        self.assertEqual((self.sent(), len(self.started())), ([], 1))

    def test_an_old_process_still_exiting_starts_no_second_one(self):
        # review m6
        self.setup_project()
        self.e.set_state(replies=self.READY, close_keeps_processes=True)
        r = self.restart("--wait", "5", env_extra={"LANE_RESTART_EXIT_WAIT": "1"})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("old process is still there", r.stderr)
        self.assertEqual((len(self.started()), len(self.closes())), (1, 1))

    def test_outside_an_orca_terminal_nothing_is_sent(self):
        self.setup_project()
        r = self.restart(env_extra={"ORCA_TERMINAL_HANDLE": None})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("Orca terminal", r.stderr)
        self.assertEqual(self.sent(), [])

    @unittest.skipIf(os.geteuid() == 0, "root reads every file")
    def test_cannot_tell_is_not_restarted(self):
        self.restore()
        r = self.restart()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("--force", r.stderr)
        self.assertEqual((self.sent(), len(self.started())), ([], 1))

    def test_a_dead_coordinator_is_only_started(self):
        self.setup_project()
        self.restart_server()
        r = self.restart()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("restart: not running", r.stdout)
        self.assertEqual((self.sent(), len(self.started())), ([], 2))


class ProjectStopHandover(Spawn):
    def test_a_failed_bd_read_is_never_a_new_handover(self):
        # review B1, for project stop (the same helper): nothing is asked or closed when bd cannot be read
        self.setup_project()
        (self.e.bin / "bd").write_text("#!/bin/sh\nexit 1\n")
        self.e.set_state(handover_on_send={"handover-latest": "new"})
        r = self.e.pause("stop", str(self.repo), "--wait", "2")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("`bd recall` failed", r.stderr)
        self.assertEqual((self.sent(), self.closes()), ([], []))


class OnHost(Spawn):
    def setUp(self):
        super().setUp()
        ssh = self.e.bin / "ssh"
        ssh.write_text("#!/usr/bin/env python3\nimport json, os, sys\n"
                       "open(os.environ['HOME'] + '/ssh.log', 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n")
        ssh.chmod(0o755)

    def ssh_calls(self):
        f = self.e.home / "ssh.log"
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def test_on_runs_the_boxs_own_lane_control_there(self):
        r = self.spawn("coordinator", "start", "~/code/app", "--on", "trial-vps", "--force", "--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        (call,) = self.ssh_calls()
        self.assertEqual(call[-3:-1], ["--", "trial-vps"])
        cmd = call[-1]
        self.assertIn("current/core/scripts/lane-control", cmd)
        self.assertIn("coordinator start ~/code/app --force --yes", cmd)  # the box checks for a person itself
        self.assertNotIn("--on", cmd)
        self.assertEqual(self.e.calls(), [])  # nothing of this machine's Orca

    def test_a_host_that_is_no_ssh_host_is_refused(self):
        r = self.spawn("coordinator", "status", "app", "--on", "-oProxyCommand=x")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.ssh_calls(), [])


class LaneNew(Spawn):
    def new(self, *args, env_extra=None):
        return self.spawn_person("lane", "new", *args, env_extra=env_extra)

    def test_it_sends_one_request_to_the_coordinators_run(self):
        self.setup_project()
        run = self.e.settings(self.repo)["runs"]["coordinator"]
        code, out, err, _ = self.new("app", "app-7", "--tier", "deep", "--note", "keep the old API")
        self.assertEqual(code, 0, err + out)
        (msg,) = self.sent()
        self.assertEqual((msg["to"], msg["from"]), (f"run:{run}", "term_owner"))
        self.assertEqual(msg["subject"], "lane-control lane new: app-7")
        for needed in ("A request from `lane-control lane new`", "issue app-7", "deep tier", "keep the old API",
                       "not the owner's approval", "names prove nothing", "normal gate",
                       "Confirm it with the owner in your terminal first", "term_owner"):
            self.assertIn(needed, msg["body"])
        self.assertNotIn("The owner asks", msg["body"])
        handle = self.e.settings(self.repo)["terminals"]["coordinator"]
        self.assertIn(f"terminal {handle}", out)  # where it asks
        self.assertIn("lane-control lane list app", out)  # and where the lane shows
        self.assertEqual(len(self.started()), 1)  # nothing is started by the front door itself

    def test_without_tier_or_note_it_asks_for_the_lane_alone(self):
        self.setup_project()
        self.assertEqual(self.new(str(self.repo), "app-8")[0], 0)
        body = self.sent()[0]["body"]
        self.assertNotIn("tier", body.split("It is not")[0])
        self.assertNotIn("note", body)

    def test_an_agent_is_refused(self):
        # review B2: a worker's environment (ORCA_TERMINAL_HANDLE and CLAUDECODE, no LANE_ROLE), any agent marker,
        # or no terminal at all
        self.setup_project()
        for extra in ({"CLAUDECODE": "1"}, {"LANE_ROLE": "coordinator"}, {}):
            r = self.spawn("lane", "new", "app", "app-7", env_extra=extra)
            self.assertEqual(r.returncode, 1, extra)
            self.assertIn("is for a person at a terminal", r.stderr)
        self.assertEqual(self.sent(), [])

    def test_nothing_is_sent_to_a_coordinator_that_is_not_running(self):
        self.setup_project()
        self.restart_server()
        code, _, err, _ = self.new("app", "app-7")
        self.assertEqual(code, 1)
        self.assertIn("coordinator start", err)
        self.assertEqual(self.sent(), [])

    def test_a_stale_record_is_never_the_address(self):
        # review M3: the recorded coordinator died; the live one was started by hand in another terminal
        self.setup_project()
        self.restart_server()
        dead_run = self.e.settings(self.repo)["runs"]["coordinator"]
        self.e.set_state(terminals=[{"handle": "term_hand", "title": "\u2733 app coordinator",
                                     "worktreePath": str(self.repo), "agentIdentity": "claude"}])
        self.process(4001, ["claude", "--name", "app coordinator"], {"LANE_ROLE": "coordinator",
                                                                    "ORCA_TERMINAL_HANDLE": "term_hand"})
        code, _, err, _ = self.new("app", "app-7")
        self.assertEqual(code, 1, err)
        self.assertIn("0 Runs whose coordinator is that terminal", err)
        self.assertEqual(self.sent(), [])
        st = self.state()
        st["runs"].append({"runId": "run_hand", "objective": "app coordinator", "coordinator_handle": "term_hand"})
        self.e.state.write_text(json.dumps(st))
        code, out, err, _ = self.new("app", "app-7")
        self.assertEqual(code, 0, err + out)
        self.assertEqual([m["to"] for m in self.sent()], ["run:run_hand"])
        self.assertIn(f"recorded Run {dead_run} is not the live coordinator's", out)
        self.assertIn("terminal term_hand", out)

    def test_a_terminal_not_titled_exactly_or_a_run_of_another_name_is_no_address(self):
        self.setup_project()
        self.e.set_state(terminals=[{**t, "title": "app coordinator (old)"} for t in self.terminals()])
        code, _, err, _ = self.new("app", "app-7")
        self.assertEqual(code, 1)
        self.e.set_state(terminals=[{**t, "title": "app coordinator"} for t in self.terminals()])
        st = self.state()
        st["runs"] = [{**r, "objective": "shop coordinator"} for r in st["runs"]]
        self.e.state.write_text(json.dumps(st))
        code, _, err, _ = self.new("app", "app-7")
        self.assertEqual(code, 1)
        self.assertIn("objective 'shop coordinator'", err)
        self.assertEqual(self.sent(), [])

    def test_outside_an_orca_terminal_nothing_is_sent(self):
        self.setup_project()
        code, _, err, _ = self.new("app", "app-7", env_extra={"ORCA_TERMINAL_HANDLE": None})
        self.assertEqual(code, 1)
        self.assertIn("Orca terminal", err)
        self.assertEqual(self.sent(), [])

    def test_a_bad_issue_tier_or_note_is_a_usage_error(self):
        self.setup_project()
        for args in (["app", "-x"], ["app", "a b"], ["app", "app-7", "--tier", "huge"],
                     ["app", "app-7", "--note", "two\nlines"]):
            self.assertEqual(self.spawn("lane", "new", *args).returncode, 2, args)
        self.assertEqual(self.sent(), [])


class LaneList(Spawn):
    def setUp(self):
        super().setUp()
        gh = self.e.bin / "gh"
        gh.unlink()
        gh.write_text("#!/usr/bin/env python3\nimport json, os, sys\n"
                      "if os.environ.get('GH_BROKEN'):\n"
                      "    sys.exit(print('error connecting to api.github.com', file=sys.stderr) or 1)\n"
                      "prs = json.load(open(os.environ['HOME'] + '/prs.json'))\n"
                      "ref = sys.argv[3]\n"
                      "if ref not in prs:\n"
                      "    sys.exit(print(f'no pull requests found for branch \"{ref}\"', file=sys.stderr) or 1)\n"
                      "print(json.dumps(prs[ref]))\n")
        gh.chmod(0o755)

    def full(self, dispatch, task, rid, path, terminal, state, liveness, origin=None, run="run_c"):
        """A worker as `worker-list --full` gives it."""
        return {"dispatchId": dispatch, "taskId": task, "runId": run, "workerState": state,
                "agentTerminalHandle": terminal, "terminalState": "active",
                "resource": {"worktreeId": f"{rid}::{path}", "originDispatchId": origin or dispatch,
                             "terminalHandle": terminal},
                "projection": {"liveness": {"verdict": liveness}, "outcome": "in_progress",
                               "provider": {"id": "claude", "model": "opus"}, "workspace": {"id": f"{rid}::{path}"}}}

    def lanes(self):
        self.setup_project()
        rid = self.state()["repos"][0]["id"]
        now = int(time.time() * 1000)
        ws = self.e.home / "orca" / "workspaces"
        rows, terms, paths = [], [], {}
        for i, (name, linked) in enumerate((("app-7-login", None), ("app-9-cache", 41), ("app-11-docs", None))):
            paths[name] = path = ws / name
            path.mkdir(parents=True)
            rows.append({"repo": rid, "name": name, "path": str(path), "parent": None,
                         "branch": f"refs/heads/o/{name}", "lastActivityAt": now - 3 * 3600_000,
                         **({"linkedPR": linked} if linked else {})})
            terms.append({"handle": f"term_w{i}", "title": name, "worktreePath": str(path),
                          "lastOutputAt": now - (12 + 60 * i) * 60_000})
        terms.append({"handle": "term_rev", "title": "review", "worktreePath": str(paths["app-7-login"]),
                      "lastOutputAt": now - 180 * 60_000})
        login, cache, docs = (str(paths[n]) for n in ("app-7-login", "app-9-cache", "app-11-docs"))
        workers = [  # newest first, as Orca lists them
            self.full("ctx_rev", "task_rev", rid, login, "term_rev", "succeeded", "exited"),  # its reviewer, newer
            self.full("ctx_fix", "task_fix", rid, login, "term_w0", "ready", "live", origin="ctx_0"),  # a fix round
            self.full("ctx_1", "task_1", rid, cache, "term_w1", "ready", "live"),
            self.full("ctx_2", "task_2", rid, docs, "term_w2", "ready", "live"),
            {"dispatchId": "ctx_0", "taskId": "task_0", "runId": "run_c", "workerState": "succeeded",  # moved on
             "projection": {"liveness": {"verdict": "unverifiable"}, "workspace": {"id": f"{rid}::{login}"}}},
            {"dispatchId": "ctx_x", "state": "ready", "liveness": "live", "workspace": f"repo-x::{ws / 'shop-1'}"},
        ]
        self.e.set_state(worktrees=rows, workers=workers, terminals=[*self.terminals(), *terms],
                         tasks=[{"id": "task_2", "run_id": "run_c", "spec": "You are lane APP-11 of app. Your task: "
                                 "APP-11 (bd app-11) \"docs\"."}])
        (self.e.home / "prs.json").write_text(json.dumps({
            "o/app-7-login": {"number": 40, "title": "APP-7 login (app-7)", "state": "OPEN",
                              "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "SUCCESS"},
                                                    {"status": "COMPLETED", "conclusion": "FAILURE"}]},
            "41": {"number": 41, "title": "APP-9 cache (app-9)", "state": "OPEN",
                   "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "SUCCESS"},
                                         {"state": "PENDING"}]}}))

    def test_list_gives_lane_issue_pr_ci_idle_time_and_the_lanes_own_worker(self):
        self.lanes()
        r = self.spawn("lane", "list", "app")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        lines = r.stdout.splitlines()
        self.assertEqual(len(lines), 4, r.stdout)  # the header and one row per lane of this project
        login = next(l for l in lines if l.startswith("app-7-login"))
        for needed in ("app-7 ", "PR #40", "failing", "12m", "ready/live", "(+1 other)"):  # not its reviewer's
            self.assertIn(needed, login)
        cache = next(l for l in lines if l.startswith("app-9-cache"))
        for needed in ("app-9 ", "PR #41", "pending", "1h12m"):
            self.assertIn(needed, cache)
        docs = next(l for l in lines if l.startswith("app-11-docs"))
        for needed in ("app-11 ", "no PR"):  # review m2: the issue from its brief before there is a PR
            self.assertIn(needed, docs)
        self.assertNotIn("shop-1", r.stdout)
        self.assertEqual(r.stderr, "")

    def test_status_gives_each_lane_in_full_and_names_its_reviewer_as_such(self):
        self.lanes()
        r = self.spawn("lane", "status", "app")
        self.assertEqual(r.returncode, 0, r.stderr)
        for needed in ("app-7-login: issue app-7 · PR #40 · ci failing · idle 12m",
                       "worker ctx_fix (task task_fix): ready, live", "earlier in its chain: ctx_0",
                       "also on it (a reviewer or another worker, not the lane): ctx_rev", "branch o/app-7-login",
                       "terminal term_w1 (active)"):
            self.assertIn(needed, r.stdout)

    def test_gh_that_cannot_answer_is_not_no_pr(self):
        # review m1
        self.lanes()
        r = self.spawn("lane", "list", "app", env_extra={"GH_BROKEN": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("no PR", r.stdout)
        self.assertIn("PR ?", r.stdout)
        self.assertEqual(r.stderr.count("gh could not answer (error connecting to api.github.com)"), 1)

    def test_no_lanes(self):
        self.setup_project()
        r = self.spawn("lane", "list", "app")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "app: no lane in Orca's worker list"))


@unittest.skipIf(os.geteuid() == 0, "root reads every file")
class Machine(Spawn):
    def setUp(self):
        super().setUp()
        self.folder = self.e.home / "machine-coordinator"
        (self.folder / ".lane-control").mkdir(parents=True)
        (self.folder / ".lane-control" / "machine-coordinator.md").write_text(
            "---\nname: machine-coordinator\nagent: claude\nmodel: opus\n---\nYou are the machine coordinator.\n")
        self.e.trust(self.folder)

    def machine(self):
        return [x for x in self.e.sessions() if x["role"] == "machine-coordinator"]

    def test_start_then_status_then_start_again_starts_one(self):
        r = self.spawn("coordinator", "start", "machine")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("machine coordinator: created", r.stdout)
        self.assertIn("machine coordinator: mode auto", r.stdout)  # review m7: the mode it used, said
        self.assertIn("--permission-mode auto", " ".join(self.machine()[0]["argv"]))
        self.assertIn("machine coordinator: running", self.spawn("coordinator", "status", "machine").stdout)
        r = self.spawn("coordinator", "start", "machine")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("nothing was started", r.stdout)
        self.assertEqual(len(self.machine()), 1)
        code, out, err, _ = self.spawn_person("coordinator", "start", "machine", "--force", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("never starts a second one", err)
        self.assertEqual(len(self.machine()), 1)

    def test_the_permission_mode_is_the_given_one(self):
        r = self.spawn("coordinator", "start", "machine", "--permission-mode", "default")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("--permission-mode default", " ".join(self.machine()[0]["argv"]))
        self.assertEqual(self.spawn("coordinator", "start", "app", "--permission-mode", "default").returncode, 2)

    def doubt(self):
        self.unreadable(4201, ["claude", "--resume", "x"])
        (self.e.proc / "4201" / "cwd").unlink()
        (self.e.proc / "4201" / "cwd").symlink_to(self.folder)
        self.e.set_state(terminals=[{"handle": "term_9", "title": "machine coordinator",
                                     "worktreePath": str(self.folder), "agentIdentity": "claude"}])

    def test_cannot_tell_needs_a_persons_force_and_the_forced_start_is_recorded(self):
        self.doubt()
        r = self.spawn("coordinator", "start", "machine")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("cannot tell", r.stdout)
        r = self.spawn("coordinator", "start", "machine", "--force", "--yes", env_extra={"CLAUDECODE": "1"})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("is for a person", r.stderr)
        self.assertEqual(self.machine(), [])
        code, out, err, _ = self.spawn_person("coordinator", "start", "machine", "--force", "--yes")
        self.assertEqual(code, 0, err + out)
        self.assertIn("term_9", out)
        self.assertEqual(len(self.machine()), 1)
        (rec,) = json.loads((self.folder / ".lane-control" / "forced-starts.json").read_text())
        self.assertEqual((rec["confirmed"], rec["terminals"], rec["from_terminal"]), ("--yes", ["term_9"], "term_owner"))
        self.assertTrue(rec["outcome"].startswith("created"), rec)

    def test_no_persona_says_to_set_the_machine_up(self):
        (self.folder / ".lane-control" / "machine-coordinator.md").unlink()
        r = self.spawn("coordinator", "start", "machine")
        self.assertEqual(r.returncode, 1)
        self.assertIn("lane-control machine setup", r.stderr)


if __name__ == "__main__":
    unittest.main()
