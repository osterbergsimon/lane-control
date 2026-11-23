"""night-precheck and improve mode: the fallback nudge (`DUE <name> improve`) and unmetered machines (lc-yzb).
Same temp-HOME setup as the other night-precheck tests (test_machine_setup.py); improve.json is written by hand."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_machine_setup import MachineTest, age  # noqa: E402


class ImprovePrecheckTests(MachineTest):
    def setUp(self):
        super().setUp()
        self.playbook(20, 10)  # step 0

    def conf(self, *coordinators: str, extra: str = ""):
        lines = "\n".join(f"{n} {self.claude_coordinator(n, idle)} claude {n} coordinator" for n, idle in coordinators)
        self.machine_conf(f'COORDINATORS="\n{lines}\n"\nRUNNER_REPOS=""\n{extra}')

    def improve_on(self, projects="all", unmetered=False, end="until-back"):
        (self.conf_dir / "improve.json").write_text(json.dumps({
            "started": "2026-11-23T10:00:00+00:00", "end": end, "limits": "", "deny_paths": [], "projects": projects,
            "unmetered": unmetered, "branches": {}, "integration": {}}))

    def pre(self, *args, hour="12"):
        return self.run_script("night-precheck", *args, env={"LANE_HOUR": hour})

    def test_improve_due_by_day(self):
        self.conf(("proj", 31))
        self.improve_on()
        r = self.pre()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "DUE proj improve idle=31min\n")

    def test_improve_not_due_when_off(self):
        self.conf(("proj", 31))
        r = self.pre()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")

    def test_improve_not_due_before_the_idle_time(self):
        self.conf(("proj", 20))
        self.improve_on()
        self.assertEqual(self.pre().returncode, 1)

    def test_improve_idle_min_from_machine_conf(self):
        self.conf(("proj", 15), extra="IMPROVE_IDLE_MIN=10\n")
        self.improve_on()
        self.assertEqual(self.pre().stdout, "DUE proj improve idle=15min\n")

    def test_improve_only_for_its_projects(self):
        self.conf(("a", 31), ("b", 31))
        self.improve_on(projects=["a"])
        self.assertEqual(self.pre().stdout, "DUE a improve idle=31min\n")

    def test_improve_replaces_the_plain_check_in(self):
        self.conf(("proj", 60))
        self.improve_on()
        self.assertEqual(self.pre(hour="2").stdout, "DUE proj improve idle=60min\n")

    def test_night_alone_no_improve(self):
        self.conf(("proj", 31))
        r = self.pre(hour="2")  # night, idle under IDLE_MIN, improve off
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("improve", r.stdout)

    def test_night_alone_plain_check_in_unchanged(self):
        self.conf(("proj", 60))
        self.assertEqual(self.pre(hour="2").stdout, "DUE proj idle=60min\n")

    def test_checked_improve_waits_one_idle_period(self):
        self.conf(("proj", 31))
        self.improve_on()
        self.assertEqual(self.pre("--checked", "proj", "improve").returncode, 0)
        self.assertEqual(self.pre().returncode, 1)
        age(Path(self.env["HOME"]) / ".cache" / "lane-control" / "night" / "proj.improve", 31)
        self.assertEqual(self.pre().stdout, "DUE proj improve idle=31min\n")

    def test_unmetered_not_unknown(self):
        self.playbook(None, None)  # no usage reading
        self.conf(("proj", 40))
        self.improve_on(unmetered=True)
        r = self.pre()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "DUE proj improve idle=40min\n")
        self.assertNotIn("unknown", r.stdout + r.stderr)

    def test_metered_unknown_says_so_and_nudges_nobody(self):
        self.playbook(None, None)
        self.conf(("proj", 40))
        self.improve_on()
        r = self.pre()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("BUDGET unknown"), r.stdout)
        self.assertNotIn("DUE", r.stdout)

    def test_unmetered_ignores_the_budget_steps(self):
        self.playbook(92, 10)  # stop step on the budget
        self.conf(("proj", 40))
        self.improve_on(unmetered=True)
        self.assertEqual(self.pre().stdout, "DUE proj improve idle=40min\n")

    def test_metered_improve_at_push_and_handover_gets_the_push_check_in(self):
        # review M6: the plain check-in says "push and hand over" there; the improve nudge must not swallow it
        self.conf(("proj", 40))
        self.improve_on()
        self.playbook(86, 10)  # ceiling 93: no-new-work
        r = self.pre()
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertTrue(lines[0].startswith("BUDGET push-and-handover"), r.stdout)
        self.assertEqual(lines[1:], ["DUE proj improve idle=40min"])
        self.playbook(89, 10)  # push-and-handover
        self.assertTrue(self.pre().stdout.splitlines()[0].startswith("BUDGET push-and-handover"))
        self.playbook(70, 10)
        self.assertEqual(self.pre().stdout, "DUE proj improve idle=40min\n")

    def test_metered_improve_at_the_stop_step_gets_nothing(self):
        self.conf(("proj", 40))
        self.improve_on()
        self.playbook(92, 10)  # stop
        r = self.pre()
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("DUE", r.stdout)
        self.assertIn("stopped", r.stderr)

    def test_metered_improve_follows_the_project_ceiling(self):
        self.conf(("proj", 40))
        self.improve_on()
        ceil = self.tmp / "ceiling.json"
        ceil.write_text(json.dumps({"ceiling": 93, "projects": {"proj": {"ceiling": 99}}}))
        self.playbook(86, 10, ceiling=f"file:{ceil}")  # no-new-work at 93, plain at 99
        self.assertEqual(self.pre().stdout, "DUE proj improve idle=40min\n")

    def test_malformed_improve_file_is_off_and_says_why(self):
        self.conf(("proj", 40))
        (self.conf_dir / "improve.json").write_text("{nope")
        r = self.pre()
        self.assertEqual(r.returncode, 1)
        self.assertIn("improve.json", r.stderr)


class NightManagerImproveDocTests(unittest.TestCase):
    def test_night_manager_knows_the_improve_line(self):
        text = " ".join((Path(__file__).resolve().parent.parent / "orchestrators" / "native-orca" / "night-manager.md")
                        .read_text().split())
        self.assertIn("`DUE <name> improve idle=<n>min`", text)
        self.assertIn("away-improve", text)
        self.assertIn("night-precheck --checked <name> improve", text)
        self.assertIn("never turn improve mode on", text)


if __name__ == "__main__":
    unittest.main()
