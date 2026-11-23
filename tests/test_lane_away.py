"""lane-away on --improve, the improve state (core/improve.py) and what reads it (away-improve mode, lc-yzb). Temp HOME
and temp git repos; no real away file, improve file, branch or repo is touched."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "core"))
from test_machine_setup import MachineTest  # noqa: E402


class ImproveTest(MachineTest):
    def setUp(self):
        super().setUp()
        self.machine_conf('COORDINATORS="\nshop %s/shop claude shop coordinator\nblog %s/blog claude blog coordinator\n"\n'
                          % (self.tmp, self.tmp))

    def away(self) -> Path:
        return self.conf_dir / "away"

    def improve_file(self) -> Path:
        return self.conf_dir / "improve.json"

    def state(self) -> dict:
        return json.loads(self.improve_file().read_text())

    def on(self, *args, env=None):
        return self.run_script("lane-away", "on", "--improve", "--yes", *args, env=env)


class ImproveOnTests(ImproveTest):
    def test_plain_on_leaves_improve_off(self):
        r = self.run_script("lane-away", "on")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.away().is_file())
        self.assertFalse(self.improve_file().exists())
        self.assertIn("improve: off", self.run_script("lane-away", "status").stdout)

    def test_night_and_budget_marker_leave_improve_off(self):
        (self.conf_dir / "budget-stopped").touch()
        self.run_script("lane-away", "status", env={"LANE_HOUR": "2"})
        self.assertFalse(self.improve_file().exists())

    def test_improve_writes_state(self):
        r = self.on("--limits", "nothing that needs db migrations", "--deny-paths", "**/migrations/**,infra/*")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = self.state()
        self.assertEqual(set(st), {"started", "end", "limits", "deny_paths", "projects", "unmetered", "branches"})
        self.assertEqual(st["end"], "until-back")
        self.assertEqual(st["limits"], "nothing that needs db migrations")
        self.assertEqual(st["deny_paths"], ["**/migrations/**", "infra/*"])
        self.assertEqual(st["projects"], "all")
        self.assertIs(st["unmetered"], False)
        self.assertEqual(sorted(st["branches"]), ["blog", "shop"])
        for b in st["branches"].values():
            self.assertRegex(b, r"^away/\d{4}-\d{2}-\d{2}$")
        self.assertTrue(self.away().is_file())  # the owner counts as away

    def test_hours_and_until_set_a_time_end(self):
        self.on("--hours", "3")
        self.assertRegex(self.state()["end"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
        self.on("--until", "23:30")
        self.assertRegex(self.state()["end"], r"T23:30")
        self.assertEqual(self.on("--hours", "3", "--until-back").returncode, 2)
        self.assertEqual(self.on("--hours", "x").returncode, 2)

    def test_project_flag_limits_projects(self):
        self.on("--project", "shop")
        st = self.state()
        self.assertEqual(st["projects"], ["shop"])
        self.assertEqual(list(st["branches"]), ["shop"])
        self.on("--project", "shop", "--project", "blog")
        self.assertEqual(self.state()["projects"], ["shop", "blog"])

    def test_unmetered_recorded(self):
        self.on("--unmetered")
        self.assertIs(self.state()["unmetered"], True)
        self.assertEqual(self.state()["end"], "until-back")

    def test_unknown_project_refused(self):
        r = self.on("--project", "nope")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nope", r.stderr)
        self.assertIn("shop", r.stderr)  # says what is known
        self.assertFalse(self.improve_file().exists())
        self.assertFalse(self.away().exists())

    def test_no_terminal_needs_yes(self):
        r = self.run_script("lane-away", "on", "--improve")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--yes", r.stderr)
        self.assertFalse(self.improve_file().exists())
        self.assertFalse(self.away().exists())

    def test_prints_summary(self):
        r = self.on("--project", "shop", "--limits", "no db migrations", "--hours", "2", "--unmetered")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("improve mode on", out)
        self.assertIn("shop", out)
        self.assertRegex(out, r"away/\d{4}-\d{2}-\d{2}")
        self.assertIn("no db migrations", out)
        self.assertIn("unmetered", out)
        self.assertIn("ends", out)
        r = self.on()
        self.assertIn("until back", r.stdout)
        self.assertIn("metered", r.stdout)
        self.assertIn("all set-up projects", r.stdout)

    def test_off_clears_both(self):
        self.on()
        r = self.run_script("lane-away", "off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.away().exists())
        self.assertFalse(self.improve_file().exists())
        self.assertEqual(self.run_script("lane-away", "off").returncode, 0)

    def test_status_shows_improve(self):
        self.on("--project", "shop", "--limits", "no db migrations")
        s = self.run_script("lane-away", "status").stdout
        self.assertIn("improve: on", s)
        self.assertIn("until back", s)
        self.assertIn("no db migrations", s)
        self.assertIn("shop", s)
        self.assertIn("metered", s)

    def test_usage_refuses_improve_flags_without_improve(self):
        self.assertEqual(self.run_script("lane-away", "on", "--limits", "x").returncode, 2)
        self.assertFalse(self.improve_file().exists())


class ImproveReadTests(ImproveTest):
    def read(self, code: str, env=None):
        e = dict(self.env, **(env or {}))
        return subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(ROOT / 'core')!r}); "
                               f"import improve; {code}"], env=e, capture_output=True, text=True)

    def test_improve_keeps_owner_away_past_away_hours(self):
        self.on()
        # age the away file past AWAY_HOURS: the owner still counts as away while improve is on
        import os, time
        t = time.time() - 40 * 3600
        os.utime(self.away(), (t, t))
        v = self.lane_limits("LANE_OWNER_AWAY", "LANE_PRESENCE", "LANE_PRESENCE_WHY")
        self.assertEqual(v["LANE_OWNER_AWAY"], "1")
        self.assertEqual(v["LANE_PRESENCE"], "away")
        self.assertEqual(v["LANE_PRESENCE_WHY"], "improve")
        self.run_script("lane-away", "off")
        self.assertEqual(self.lane_limits("LANE_OWNER_AWAY")["LANE_OWNER_AWAY"], "")

    def test_malformed_file_is_off_and_says_why(self):
        self.improve_file().write_text("{not json")
        r = self.read("print(improve.load())")
        self.assertEqual(r.stdout.strip(), "None")
        self.assertIn("improve.json", r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)
        self.assertEqual(self.lane_limits("LANE_OWNER_AWAY")["LANE_OWNER_AWAY"], "")

    def test_wrong_shape_is_off_and_says_why(self):
        self.improve_file().write_text(json.dumps({"started": "x", "end": 5, "projects": "shop"}))
        r = self.read("print(improve.load())")
        self.assertEqual(r.stdout.strip(), "None")
        self.assertIn("improve.json", r.stderr)

    def test_missing_file_is_off_quietly(self):
        r = self.read("print(improve.load(), improve.active_for('shop'))")
        self.assertEqual(r.stdout.strip(), "None False")
        self.assertEqual(r.stderr, "")

    def test_ended_time_is_off(self):
        self.on("--hours", "1")
        st = self.state()
        st["end"] = "2020-01-01T00:00:00+00:00"
        self.improve_file().write_text(json.dumps(st))
        self.away().unlink()
        r = self.read("print(improve.load())")
        self.assertEqual(r.stdout.strip(), "None")
        self.assertEqual(self.lane_limits("LANE_PRESENCE_WHY")["LANE_PRESENCE_WHY"], "day")

    def test_active_for_names(self):
        self.on("--project", "shop")
        r = self.read("print(improve.active_for('shop'), improve.active_for('blog'))")
        self.assertEqual(r.stdout.strip(), "True False")
        self.on()
        r = self.read("print(improve.active_for('shop'), improve.active_for('blog'))")
        self.assertEqual(r.stdout.strip(), "True True")

    def test_write_and_clear(self):
        r = self.read("improve.write({'started':'2026-01-01T00:00:00+00:00','end':'until-back','limits':'','deny_paths':[],"
                      "'projects':'all','unmetered':False,'branches':{}}); print(improve.active_for('x')); improve.clear(); "
                      "print(improve.load())")
        self.assertEqual(r.stdout.split(), ["True", "None"], r.stderr)


if __name__ == "__main__":
    unittest.main()
