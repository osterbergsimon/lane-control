"""CLI-2 (lc-wja): spawn from the front door. `lane-control coordinator start|restart|status` uses LIV-1's live check
(running / recorded but dead / cannot tell) and never starts a second coordinator without the owner's --force, which is
refused on `running` and recorded; `lane-control lane new` is one Orca message to the coordinator; `lane-control lane
list|status` reads Orca's worker list. Stub orca and a fake process table (LANE_PROC_DIR) in a temporary HOME, as the
liveness tests (tests/test_coordinator_liveness.py)."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import test_coordinator_liveness as liveness  # a module, so its tests are not collected here a second time
import test_project_setup_native as native
from test_project_setup import Tty

ROOT = native.ROOT


def helpers(c):
    """A test class's helpers without its tests: a TestCase bound in this module would run its tests a second time."""
    return type(c.__name__, (), {k: v for k, v in vars(c).items() if not k.startswith("test")})


L, R = helpers(liveness.Liveness), helpers(liveness.Restored)

SPAWN = ROOT / "core" / "scripts" / "lane-spawn"
CLI = ROOT / "core" / "scripts" / "lane-control"


def load_pause():
    loader = importlib.machinery.SourceFileLoader("project_pause_spawn_t", str(ROOT / "core" / "scripts" /
                                                                            "project-pause"))
    spec = importlib.util.spec_from_loader("project_pause_spawn_t", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Spawn(native.Case):
    setup_project = L.setup_project
    process, terminals, started, closes = L.process, L.terminals, L.started, L.closes
    restart_server, unreadable = L.restart_server, L.unreadable
    restore, argv, transcript = R.restore, R.argv, R.transcript

    def spawn(self, *args, env_extra=None):
        return subprocess.run([sys.executable, str(SPAWN), *args], env={**self.e.env, **(env_extra or {})},
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)

    def sent(self):
        return json.loads(self.e.state.read_text()).get("sent", [])

    def forced(self):
        return self.e.settings(self.repo).get("forced_starts", [])


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
        self.assertEqual(len(self.started()), 2)
        self.assertEqual(self.forced(), [])

    def test_cannot_tell_starts_no_second_coordinator_without_force(self):
        self.restore()
        before = self.e.settings(self.repo)["terminals"]
        r = self.spawn("coordinator", "start", "app")  # by its name, as the owner types it
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("cannot tell", r.stdout)
        self.assertIn("4101", r.stdout)  # what it found: the process it could not place
        self.assertEqual((len(self.started()), self.e.settings(self.repo)["terminals"]), (1, before))
        self.assertEqual(self.forced(), [])

    def test_force_without_a_terminal_needs_yes_and_starts_nothing(self):
        self.restore()
        r = self.spawn("coordinator", "start", str(self.repo), "--force")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("--yes", r.stderr)
        self.assertIn("Nothing was started", r.stderr)
        self.assertIn("found: terminal", r.stdout)  # it showed what the check found before refusing
        self.assertEqual((len(self.started()), self.forced()), (1, []))

    def test_force_yes_starts_it_and_records_that_the_owner_forced_it(self):
        self.restore()
        old = self.e.settings(self.repo)
        r = self.spawn("coordinator", "start", str(self.repo), "--force", "--yes")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        found = next(l for l in r.stdout.splitlines() if l.strip().startswith("found:"))
        for needed in (old["terminals"]["coordinator"], "pid 4101", "--resume"):  # the terminal and the process
            self.assertIn(needed, found)
        self.assertIn("session coordinator: forced", r.stdout)
        self.assertNotIn("recorded but dead", r.stdout)
        self.assertEqual(len(self.started()), 2)
        (rec,) = self.forced()
        self.assertEqual((rec["role"], rec["by"]), ("coordinator", "owner (--force)"))
        self.assertIn("4101", rec["doubt"])
        self.assertEqual(rec["terminals"], [old["terminals"]["coordinator"]])
        self.assertRegex(rec["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        new = self.e.settings(self.repo)
        self.assertNotEqual(new["terminals"]["coordinator"], old["terminals"]["coordinator"])
        self.assertIn(old["runs"]["coordinator"], new["old_runs"])
        self.assertEqual(self.closes(), [])  # what it could not place is left open, never closed for the owner

    def test_force_is_refused_on_a_running_coordinator(self):
        self.setup_project()
        for extra in ((), ("--yes",)):
            r = self.spawn("coordinator", "start", str(self.repo), "--force", *extra)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("never starts a second one", r.stderr)
            self.assertIn("restart", r.stderr)
            self.assertEqual((len(self.started()), self.forced()), (1, []))

    def test_force_on_a_dead_coordinator_is_not_needed_and_records_nothing(self):
        self.setup_project()
        self.restart_server()
        r = self.spawn("coordinator", "start", str(self.repo), "--force")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("force: not needed", r.stdout)
        self.assertEqual((len(self.started()), self.forced()), (2, []))

    def test_yes_alone_is_a_usage_error(self):
        self.restore()
        self.assertEqual(self.spawn("coordinator", "start", str(self.repo), "--yes").returncode, 2)
        self.assertEqual(self.e.pause("start", str(self.repo), "--yes").returncode, 2)
        self.assertEqual(len(self.started()), 1)

    def in_terminal(self, answer, *args):
        pp = load_pause()
        with mock.patch.dict(os.environ, self.e.env, clear=True), mock.patch("sys.stdin", Tty()), \
                mock.patch("builtins.input", return_value=answer) as ask, contextlib.redirect_stdout(Tty()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = pp.main(["start", str(self.repo), *args])
        return code, out.getvalue(), err.getvalue(), ask

    def test_force_asks_once_in_a_terminal(self):
        self.restore()
        code, out, err, ask = self.in_terminal("n", "--force")
        self.assertEqual(code, 1, out + err)
        self.assertEqual(ask.call_count, 1)
        self.assertIn("second app coordinator", ask.call_args[0][0])
        self.assertIn("pid 4101", out)
        self.assertEqual((len(self.started()), self.forced()), (1, []))
        code, out, err, ask = self.in_terminal("y", "--force")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(ask.call_count, 1)
        self.assertEqual((len(self.started()), len(self.forced())), (2, 1))

    def test_status_says_what_the_check_found(self):
        self.restore()
        r = self.spawn("coordinator", "status", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("cannot tell", r.stdout)

    def test_the_front_door_runs_it(self):
        self.setup_project()
        r = subprocess.run([str(CLI), "coordinator", "start", str(self.repo)], env=self.e.env, capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("session coordinator: exists", r.stdout)
        self.assertEqual(len(self.started()), 1)


class Restart(Spawn):
    def test_restart_asks_for_the_handover_then_stops_and_starts(self):
        self.setup_project()
        run = self.e.settings(self.repo)["runs"]["coordinator"]
        old = self.e.settings(self.repo)["terminals"]["coordinator"]
        self.e.set_state(handover_on_send={"handover-latest": "new"})
        r = self.spawn("coordinator", "restart", str(self.repo), "--wait", "5")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        (msg,) = self.sent()
        self.assertEqual(msg["to"], f"run:{run}")
        for needed in ("handover-latest", "bd dolt push", "restarting"):
            self.assertIn(needed, msg["body"])
        steps = [l.split(":")[0] for l in r.stdout.splitlines() if l.startswith("restart ")]
        self.assertEqual(steps, ["restart 1/4 handover", "restart 2/4 handover", "restart 3/4 stop",
                                 "restart 4/4 start"])
        self.assertEqual(len(self.started()), 2)
        self.assertEqual(self.closes(), [f"orca terminal close --terminal {old} --json"])
        self.assertFalse(self.e.settings(self.repo).get("paused"))

    def test_no_handover_in_time_changes_nothing(self):
        self.setup_project()
        before = self.e.settings(self.repo)["terminals"]
        r = self.spawn("coordinator", "restart", str(self.repo), "--wait", "1")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("nothing was stopped", r.stderr)
        self.assertEqual((len(self.started()), self.closes()), (1, []))
        self.assertEqual(self.e.settings(self.repo)["terminals"], before)

    def test_a_running_lane_refuses_it_before_anything_is_sent(self):
        self.setup_project()
        rid = json.loads(self.e.state.read_text())["repos"][0]["id"]
        lane = self.e.home / "orca" / "workspaces" / "app-1"
        lane.mkdir(parents=True)
        self.e.set_state(worktrees=[{"repo": rid, "name": "app-1", "path": str(lane), "parent": None}],
                         terminals=[*self.terminals(), {"handle": "term_lane", "title": "lane",
                                                        "worktreePath": str(lane), "agentIdentity": "claude"}])
        r = self.spawn("coordinator", "restart", str(self.repo), "--wait", "1")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("app-1", r.stderr)
        self.assertEqual((self.sent(), len(self.started())), ([], 1))

    def test_outside_an_orca_terminal_nothing_is_sent(self):
        self.setup_project()
        r = self.spawn("coordinator", "restart", str(self.repo), env_extra={"ORCA_TERMINAL_HANDLE": ""})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("Orca terminal", r.stderr)
        self.assertEqual(self.sent(), [])

    @unittest.skipIf(os.geteuid() == 0, "root reads every file")
    def test_cannot_tell_is_not_restarted(self):
        self.restore()
        r = self.spawn("coordinator", "restart", str(self.repo))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("--force", r.stderr)
        self.assertEqual((self.sent(), len(self.started())), ([], 1))

    def test_a_dead_coordinator_is_only_started(self):
        self.setup_project()
        self.restart_server()
        r = self.spawn("coordinator", "restart", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("restart: not running", r.stdout)
        self.assertEqual((self.sent(), len(self.started())), ([], 2))


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
        self.assertIn("coordinator start ~/app --force --yes".replace("~/app", "~/code/app"), cmd)
        self.assertNotIn("--on", cmd)
        self.assertEqual(self.e.calls(), [])  # nothing of this machine's Orca

    def test_a_host_that_is_no_ssh_host_is_refused(self):
        r = self.spawn("coordinator", "status", "app", "--on", "-oProxyCommand=x")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.ssh_calls(), [])


class LaneNew(Spawn):
    def test_it_sends_one_message_to_the_coordinators_run(self):
        self.setup_project()
        run = self.e.settings(self.repo)["runs"]["coordinator"]
        r = self.spawn("lane", "new", "app", "app-7", "--tier", "deep", "--note", "keep the old API")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        (msg,) = self.sent()
        self.assertEqual(msg["to"], f"run:{run}")
        self.assertEqual(msg["subject"], "lane-control lane new: app-7")
        for needed in ("issue app-7", "deep tier", "keep the old API", "gate it", "lane-room",
                       "merge only on the owner's word", "term_owner", "approves nothing else"):
            self.assertIn(needed, msg["body"])
        handle = self.e.settings(self.repo)["terminals"]["coordinator"]
        self.assertIn(f"terminal {handle}", r.stdout)  # where its answer shows
        self.assertIn("lane-control lane list app", r.stdout)  # and where the lane shows
        self.assertEqual(len(self.started()), 1)  # nothing is started by the front door itself

    def test_without_tier_or_note_it_asks_for_the_lane_alone(self):
        self.setup_project()
        self.assertEqual(self.spawn("lane", "new", str(self.repo), "app-8").returncode, 0)
        body = self.sent()[0]["body"]
        self.assertNotIn("tier", body.split("Open it")[0])
        self.assertNotIn("note", body)

    def test_nothing_is_sent_to_a_coordinator_that_is_not_running(self):
        self.setup_project()
        self.restart_server()
        r = self.spawn("lane", "new", "app", "app-7")
        self.assertEqual(r.returncode, 1)
        self.assertIn("coordinator start", r.stderr)
        self.assertEqual(self.sent(), [])

    def test_refused_outside_an_orca_terminal_and_from_an_agents_session(self):
        self.setup_project()
        for extra, why in (({"ORCA_TERMINAL_HANDLE": ""}, "Orca terminal"), ({"LANE_ROLE": "coordinator"}, "agent")):
            r = self.spawn("lane", "new", "app", "app-7", env_extra=extra)
            self.assertEqual(r.returncode, 1, extra)
            self.assertIn(why, r.stderr)
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
                      "prs = json.load(open(os.environ['HOME'] + '/prs.json'))\n"
                      "ref = sys.argv[3]\n"
                      "sys.exit(1) if ref not in prs else print(json.dumps(prs[ref]))\n")
        gh.chmod(0o755)

    def lanes(self):
        self.setup_project()
        rid = json.loads(self.e.state.read_text())["repos"][0]["id"]
        now = int(time.time() * 1000)
        ws = self.e.home / "orca" / "workspaces"
        rows, workers, terms = [], [], []
        for i, (name, linked) in enumerate((("app-7-login", None), ("app-9-cache", 41))):
            path = ws / name
            path.mkdir(parents=True)
            rows.append({"repo": rid, "name": name, "path": str(path), "parent": None,
                         "branch": f"refs/heads/o/{name}", "lastActivityAt": now - 3 * 3600_000,
                         **({"linkedPR": linked} if linked else {})})
            workers.append({"dispatchId": f"ctx_{i}", "taskId": f"task_{i}", "state": "ready", "outcome": "in_progress",
                            "liveness": "live", "agent": "claude/opus", "terminal": f"term_w{i}",
                            "terminalState": "active", "workspace": f"{rid}::{path}"})
            terms.append({"handle": f"term_w{i}", "title": name, "worktreePath": str(path),
                          "lastOutputAt": now - (12 + 60 * i) * 60_000})
        other = ws / "shop-1"
        workers.append({"dispatchId": "ctx_x", "state": "ready", "liveness": "live", "workspace": f"repo-x::{other}"})
        workers.append({**workers[0], "dispatchId": "ctx_old", "state": "done"})  # an older worker on the same lane
        self.e.set_state(worktrees=rows, workers=workers, terminals=[*self.terminals(), *terms])
        (self.e.home / "prs.json").write_text(json.dumps({
            "o/app-7-login": {"number": 40, "title": "APP-7 login (app-7)", "state": "OPEN",
                              "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "SUCCESS"},
                                                    {"status": "COMPLETED", "conclusion": "FAILURE"}]},
            "41": {"number": 41, "title": "APP-9 cache (app-9)", "state": "OPEN",
                   "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "SUCCESS"},
                                         {"state": "PENDING"}]}}))

    def test_list_gives_lane_issue_pr_ci_and_idle_time(self):
        self.lanes()
        r = self.spawn("lane", "list", "app")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        lines = r.stdout.splitlines()
        self.assertEqual(len(lines), 3, r.stdout)  # the header and one row per lane of this project
        login = next(l for l in lines if l.startswith("app-7-login"))
        for needed in ("app-7 ", "PR #40", "failing", "12m", "ready/live"):
            self.assertIn(needed, login)
        cache = next(l for l in lines if l.startswith("app-9-cache"))
        for needed in ("app-9 ", "PR #41", "pending", "1h12m"):
            self.assertIn(needed, cache)
        self.assertNotIn("shop-1", r.stdout)
        self.assertNotIn("ctx_old", self.spawn("lane", "status", "app").stdout)

    def test_status_gives_each_lane_in_full(self):
        self.lanes()
        r = self.spawn("lane", "status", "app")
        self.assertEqual(r.returncode, 0, r.stderr)
        for needed in ("app-7-login: issue app-7 · PR #40 · ci failing · idle 12m", "worker ctx_0 (task task_0)",
                       "branch o/app-7-login", "terminal term_w1 (active)"):
            self.assertIn(needed, r.stdout)

    def test_a_lane_without_a_pr_or_gh(self):
        self.lanes()
        (self.e.home / "prs.json").write_text("{}")
        r = self.spawn("lane", "list", "app")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no PR", r.stdout)

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
        self.assertIn("machine coordinator: running", self.spawn("coordinator", "status", "machine").stdout)
        r = self.spawn("coordinator", "start", "machine")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("nothing was started", r.stdout)
        self.assertEqual(len(self.machine()), 1)
        r = self.spawn("coordinator", "start", "machine", "--force", "--yes")
        self.assertEqual(r.returncode, 1)
        self.assertIn("never starts a second one", r.stderr)
        self.assertEqual(len(self.machine()), 1)

    def test_cannot_tell_needs_force_and_the_forced_start_is_recorded(self):
        self.unreadable(4201, ["claude", "--resume", "x"])
        (self.e.proc / "4201" / "cwd").unlink()
        (self.e.proc / "4201" / "cwd").symlink_to(self.folder)
        self.e.set_state(terminals=[{"handle": "term_9", "title": "machine coordinator",
                                     "worktreePath": str(self.folder), "agentIdentity": "claude"}])
        r = self.spawn("coordinator", "start", "machine")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("cannot tell", r.stdout)
        self.assertEqual(self.machine(), [])
        r = self.spawn("coordinator", "start", "machine", "--force", "--yes")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("term_9", r.stdout)
        self.assertEqual(len(self.machine()), 1)
        (rec,) = json.loads((self.folder / ".lane-control" / "forced-starts.json").read_text())
        self.assertEqual((rec["by"], rec["terminals"]), ("owner (--force)", ["term_9"]))

    def test_no_persona_says_to_set_the_machine_up(self):
        (self.folder / ".lane-control" / "machine-coordinator.md").unlink()
        r = self.spawn("coordinator", "start", "machine")
        self.assertEqual(r.returncode, 1)
        self.assertIn("lane-control machine setup", r.stderr)


if __name__ == "__main__":
    unittest.main()
