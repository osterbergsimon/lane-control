"""project-pause against the stub orca/cotal executables (tests/stubs/fake.py) in a temporary HOME."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

from test_project_setup import Env

ROOT = Path(__file__).resolve().parent.parent
PAUSE = ROOT / "scripts" / "project-pause"


class PauseTest(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.addCleanup(self.e.close)
        self.e.env.pop("COTAL_NAME", None)  # the test may itself run inside an agent shell
        self.repo = self.e.repo()
        r = self.e.run(str(self.repo), "--name", "Shop")
        self.assertEqual(r.returncode, 0, r.stderr)

    def pause(self, *args, env_extra=None):
        return subprocess.run([sys.executable, str(PAUSE), *args], env={**self.e.env, **(env_extra or {})},
                              capture_output=True, text=True)

    def marker(self):
        return self.e.home / ".local" / "state" / "lane-control" / "paused" / "shop"

    def agents(self):
        return sorted(a["name"] for a in json.loads(self.e.state.read_text())["agents"])

    def test_stop_leaves_the_guard_marker_and_start_removes_it(self):
        self.assertFalse(self.marker().exists())
        self.pause("stop", str(self.repo))
        self.assertTrue(self.marker().exists())
        self.assertNotIn("no guard marker", self.pause("status", str(self.repo)).stdout)
        self.pause("start", str(self.repo))
        self.assertFalse(self.marker().exists())

    def test_rest_and_wake_stop_and_start_nothing_and_work_inside_an_agent_shell(self):
        inside = {"COTAL_NAME": "coordinator"}
        self.e.log.write_text("")
        r = self.pause("rest", str(self.repo), env_extra=inside)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.marker().exists())
        self.assertEqual(self.agents(), ["coordinator", "design-lead"])
        self.assertIn("resting", self.pause("status", str(self.repo)).stdout)
        r = self.pause("wake", str(self.repo), env_extra=inside)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("not running", r.stdout)
        self.assertFalse(self.marker().exists())
        self.assertIn("running", self.pause("status", str(self.repo)).stdout)
        self.assertFalse([c for c in self.e.calls() if c.startswith(("cotal stop", "cotal spawn", "cotal down"))])

    def test_rest_refuses_while_a_lane_runs(self):
        self.e.set_state(agents=[*json.loads(self.e.state.read_text())["agents"], {"name": "lane-1", "cwd": "/x"}])
        r = self.pause("rest", str(self.repo))
        self.assertEqual(r.returncode, 1)
        self.assertIn("lane-1", r.stderr)
        self.assertFalse(self.marker().exists())

    def test_wake_of_a_stopped_project_keeps_the_marker_for_start(self):
        self.pause("stop", str(self.repo))
        r = self.pause("wake", str(self.repo))
        self.assertEqual(r.returncode, 0)
        self.assertIn("project-pause start", r.stdout)
        self.assertTrue(self.marker().exists())
        self.pause("start", str(self.repo))
        self.assertFalse(self.marker().exists())

    def test_project_setup_clears_the_marker_when_it_starts_a_coordinator(self):
        self.pause("stop", str(self.repo))
        self.assertTrue(self.marker().exists())
        self.assertEqual(self.e.run(str(self.repo)).returncode, 0)
        self.assertFalse(self.marker().exists())
        self.pause("rest", str(self.repo))
        self.assertEqual(self.e.run(str(self.repo)).returncode, 0)  # nothing started: a resting project stays resting
        self.assertTrue(self.marker().exists())

    def test_status_warns_when_a_resting_project_has_a_lane(self):
        self.pause("rest", str(self.repo))
        self.e.set_state(agents=[*json.loads(self.e.state.read_text())["agents"], {"name": "lane-1", "cwd": "/x"}])
        out = self.pause("status", str(self.repo)).stdout
        self.assertIn("resting", out)
        self.assertIn("WARNING", out)

    def test_the_guards_slug_of_the_name_finds_the_project(self):
        self.assertEqual(self.pause("status", "shop").returncode, 0)  # set up with --name Shop
        self.assertEqual(self.pause("status", "Shop").returncode, 0)

    def test_a_refused_stop_leaves_no_marker(self):
        self.e.set_state(agents=[*json.loads(self.e.state.read_text())["agents"], {"name": "lane-1", "cwd": "/x"}])
        self.pause("stop", str(self.repo))
        self.assertFalse(self.marker().exists())

    def test_status_running_then_paused(self):
        self.assertIn("running (coordinator, design-lead)", self.pause("status", str(self.repo)).stdout)
        self.assertEqual(self.pause("stop", str(self.repo)).returncode, 0)
        self.assertIn("shop: paused", self.pause("status", str(self.repo)).stdout)

    def test_stop_stops_only_the_standing_agents_and_is_harmless_to_repeat(self):
        r = self.pause("stop", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("fresh session", r.stdout)
        self.assertEqual(self.agents(), [])
        self.assertEqual(self.pause("stop", str(self.repo)).returncode, 0)
        calls = "\n".join(self.e.calls())
        self.assertEqual(calls.count("cotal stop coordinator -s shop"), 1)
        self.assertNotIn("cotal down", calls)
        self.assertNotIn("svc stop", calls)

    def test_stop_refuses_while_a_lane_runs_and_stops_nothing(self):
        self.e.set_state(agents=[*json.loads(self.e.state.read_text())["agents"], {"name": "lane-1", "cwd": "/x"}])
        r = self.pause("stop", str(self.repo))
        self.assertEqual(r.returncode, 1)
        self.assertIn("lane-1", r.stderr)
        self.assertEqual(self.agents(), ["coordinator", "design-lead", "lane-1"])
        self.assertNotIn("cotal stop", "\n".join(self.e.calls()))

    def test_start_restarts_only_missing_agents_with_the_resume_kickoff(self):
        self.pause("stop", str(self.repo))
        self.e.log.write_text("")
        r = self.pause("start", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.agents(), ["coordinator", "design-lead"])
        spawns = [c for c in self.e.calls() if c.startswith("cotal spawn")]
        self.assertEqual(len(spawns), 2)
        self.assertIn("bd recall handover-latest", spawns[0])
        self.assertFalse(any(c.startswith(("cotal stop", "cotal down")) for c in self.e.calls()))
        self.e.log.write_text("")
        self.assertEqual(self.pause("start", str(self.repo)).returncode, 0)  # all running: nothing spawned
        self.assertFalse([c for c in self.e.calls() if c.startswith("cotal spawn")])

    def test_start_brings_back_just_the_one_that_is_missing(self):
        self.pause("stop", str(self.repo))
        self.e.set_state(agents=[{"name": "design-lead", "cwd": "/x"}])
        self.e.log.write_text("")
        self.assertEqual(self.pause("start", str(self.repo)).returncode, 0)
        spawns = [c for c in self.e.calls() if c.startswith("cotal spawn")]
        self.assertEqual(len(spawns), 1)
        self.assertIn("spawn coordinator", spawns[0])

    def test_a_bare_project_name_finds_the_project(self):
        self.assertIn("running", self.pause("status", "app").stdout)
        r = self.pause("status", "nothing")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no project set up", r.stderr)

    def test_an_unset_up_directory_is_refused(self):
        r = self.pause("stop", str(self.e.repo("other")))
        self.assertEqual(r.returncode, 1)
        self.assertIn("project-setup", r.stderr)

    def test_inside_an_agent_shell_it_says_to_use_a_plain_shell_and_calls_nothing(self):
        self.e.log.write_text("")
        for cmd in ("stop", "start"):
            r = self.pause(cmd, str(self.repo), env_extra={"COTAL_NAME": "coordinator"})
            self.assertEqual(r.returncode, 1)
            self.assertIn("plain shell", r.stderr)
        self.assertEqual(self.e.calls(), [])
        self.assertEqual(self.pause("status", str(self.repo), env_extra={"COTAL_NAME": "x"}).returncode, 0)


if __name__ == "__main__":
    unittest.main()


class AttachedRepoPauseTest(unittest.TestCase):
    """A project with an attached repo still has one space; the attached repo is not a project of its own (ONE-5)."""
    def setUp(self):
        self.e = Env()
        self.addCleanup(self.e.close)
        self.e.env.pop("COTAL_NAME", None)
        self.repo = self.e.repo("shop")
        self.plugins = self.e.repo("shop-plugins")
        r = self.e.run(str(self.repo), "--name", "Shop", "--with", str(self.plugins))
        self.assertEqual(r.returncode, 0, r.stderr)

    def pause(self, *args):
        return subprocess.run([sys.executable, str(PAUSE), *args], env=self.e.env, capture_output=True, text=True)

    def test_the_project_is_found_by_name_and_the_attached_repo_is_not_a_project(self):
        r = self.pause("status", "Shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("shop: running", r.stdout)
        by_name = self.pause("status", "shop-plugins")
        self.assertEqual(by_name.returncode, 1)
        self.assertIn("is no project set up here", by_name.stderr)
        by_path = self.pause("status", str(self.plugins))
        self.assertEqual(by_path.returncode, 1)
        self.assertIn(f"attached to the project at {self.repo}", by_path.stderr)

    def test_rest_and_start_work_on_the_one_space_and_keep_the_attached_repo(self):
        self.assertEqual(self.pause("rest", str(self.repo)).returncode, 0)
        marker = self.e.home / ".local" / "state" / "lane-control" / "paused" / "shop"
        self.assertTrue(marker.exists())
        self.assertEqual(self.pause("stop", str(self.repo)).returncode, 0)
        r = self.pause("start", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(self.e.settings(self.repo)["attached"], [str(self.plugins)])
        self.assertEqual(len([c for c in self.e.calls() if c.startswith("cotal space create")]), 1)
