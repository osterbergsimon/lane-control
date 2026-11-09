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

    def agents(self):
        return sorted(a["name"] for a in json.loads(self.e.state.read_text())["agents"])

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
