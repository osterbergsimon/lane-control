"""night-precheck and improve mode: the fallback nudge (`DUE <name> improve`) and unmetered machines (lc-yzb).
Same temp-HOME setup as the other night-precheck tests (test_machine_setup.py); improve.json is written by hand."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_machine_setup import MachineTest, age  # noqa: E402


class PrecheckBase(MachineTest):
    def setUp(self):
        super().setUp()
        self.playbook(20, 10)  # step 0

    def conf(self, *coordinators: str, extra: str = ""):
        lines = "\n".join(f"{n} {self.claude_coordinator(n, idle)} claude {n} coordinator" for n, idle in coordinators)
        self.machine_conf(f'COORDINATORS="\n{lines}\n"\nRUNNER_REPOS=""\n{extra}')

    def improve_on(self, projects="all", unmetered=False, end="until-back", **caps):
        (self.conf_dir / "improve.json").write_text(json.dumps({
            "started": "2026-11-23T10:00:00+00:00", "end": end, "limits": "", "deny_paths": [], "projects": projects,
            "unmetered": unmetered, "branches": {}, "integration": {}, **caps}))

    def period(self, merges=None, wrapped=()):
        """away-merge's record for the period improve_on started: {project: number of merges}."""
        (self.conf_dir / "improve-period.json").write_text(json.dumps({
            "period": "2026-11-23T10:00:00+00:00", "wrapped": list(wrapped),
            "merges": {p: [{"pr": i, "item": f"i{i}", "sha": "abc", "at": "2026-11-23T11:00:00+00:00"} for i in range(n)]
                       for p, n in (merges or {}).items()}}))

    def pre(self, *args, hour="12"):
        return self.run_script("night-precheck", *args, env={"LANE_HOUR": hour})


class ImprovePrecheckTests(PrecheckBase):
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

    def test_the_nudge_does_not_wait_for_the_owner_to_be_away(self):
        # improve is separate from presence: by day, owner present, a covered project is nudged and an uncovered
        # one (idle far longer) gets no plain check-in, because the owner is not away
        self.conf(("a", 31), ("b", 90))
        self.improve_on(projects=["a"])
        r = self.pre()  # noon
        self.assertEqual(r.stdout, "DUE a improve idle=31min\n")
        night = self.pre(hour="2")  # at night the plain check-in for b comes too
        self.assertEqual(night.stdout.splitlines(), ["DUE b idle=90min", "DUE a improve idle=31min"])

    def test_runner_lines_still_wait_for_the_owner_to_be_away(self):
        # review m13: improve's nudges go on by day, but the runner check is still the away check
        self.conf(("proj", 31), extra='RUNNER_REPOS="o/r"\n')
        self.improve_on()
        r = self.pre()  # noon, owner present
        self.assertEqual(r.stdout, "DUE proj improve idle=31min\n")
        self.assertNotIn("runner", r.stderr)  # gh was not even asked

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

    def test_a_truncated_improve_file_gives_no_daytime_check_in(self):
        # review M5: lane-limits.sh and improve.py used to disagree on a truncated file, which brought a plain check-in
        # at noon (owner "away" for the limits, "off" for the nudge)
        self.conf(("proj", 50))
        self.improve_on()
        text = (self.conf_dir / "improve.json").read_text()
        (self.conf_dir / "improve.json").write_text(text[: text.index('"limits"')])
        r = self.pre()
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertIn("not valid JSON", r.stderr)
        (self.conf_dir / "improve.json").write_text('{"end": "until-back"}')
        self.assertEqual((self.pre().returncode, self.pre().stdout), (1, ""))

    def test_malformed_improve_file_is_off_and_says_why(self):
        self.conf(("proj", 40))
        (self.conf_dir / "improve.json").write_text("{nope")
        r = self.pre()
        self.assertEqual(r.returncode, 1)
        self.assertIn("improve.json", r.stderr)


class ImproveNudgeBackoffTests(PrecheckBase):
    """lc-c6j: an idle coordinator is not nudged every IMPROVE_IDLE_MIN forever."""

    def stamp(self, minutes_ago, name="proj", kind="improve"):
        self.assertEqual(self.pre("--checked", name, kind).returncode, 0)
        age(Path(self.env["HOME"]) / ".cache" / "lane-control" / "night" / f"{name}.{kind}", minutes_ago)

    def test_the_wait_doubles_while_the_coordinator_stays_idle(self):
        self.conf(("proj", 120))
        self.improve_on()
        self.stamp(30)   # nudged when it had been idle 90 min: not again until 90 min have passed
        self.assertEqual(self.pre().returncode, 1)
        self.stamp(95)
        self.assertEqual(self.pre().stdout, "DUE proj improve idle=120min\n")

    def test_a_coordinator_that_worked_since_the_nudge_starts_over(self):
        self.conf(("proj", 31))
        self.improve_on()
        self.stamp(100)  # its session wrote after the nudge (idle 31 < 100): the plain wait applies
        self.assertEqual(self.pre().stdout, "DUE proj improve idle=31min\n")
        self.stamp(20)
        self.assertEqual(self.pre().returncode, 1)

    def set_idle(self, minutes, name="proj"):
        f = next((self.home / ".claude" / "projects").glob("*/*.jsonl"))
        age(f, minutes)

    def reset_night(self):
        import shutil
        shutil.rmtree(Path(self.env["HOME"]) / ".cache" / "lane-control" / "night", ignore_errors=True)
        shutil.rmtree(self.home / "code", ignore_errors=True)
        shutil.rmtree(self.home / ".claude", ignore_errors=True)

    def test_a_reply_to_the_nudge_is_not_work(self):
        # review M9/M11: the nudge is delivered into the session, which writes at once, and the night manager asks for a
        # one-line reply; whether the last write lands before --checked (-1: the delivered message, or a reply that was
        # quick) or after it (+1), the wait must double (31, 62, 124 min), not start over each time
        for offset in (1, -1):
            with self.subTest(last_write_minutes_after_the_stamp=offset):
                self.reset_night()
                self.conf(("proj", 31))
                self.improve_on()
                self.assertEqual(self.pre().stdout, "DUE proj improve idle=31min\n")
                stamp = Path(self.env["HOME"]) / ".cache" / "lane-control" / "night" / "proj.improve"
                for wait in (31, 62, 124):
                    self.assertEqual(self.pre("--checked", "proj", "improve").returncode, 0)
                    for minutes, due in ((wait - 1, False), (wait, True)):
                        age(stamp, minutes)
                        self.set_idle(minutes - offset)  # its last write: `offset` minutes after the stamp (negative: before)
                        r = self.pre()
                        self.assertEqual(r.returncode, 0 if due else 1,
                                         f"wait {wait}, {minutes} min after: {r.stdout}{r.stderr}")
                    self.assertEqual(self.pre().returncode, 0)

    def test_real_work_after_the_nudge_starts_the_wait_over(self):
        self.conf(("proj", 31))
        self.improve_on()
        self.pre()
        self.pre("--checked", "proj", "improve")
        age(Path(self.env["HOME"]) / ".cache" / "lane-control" / "night" / "proj.improve", 100)
        self.set_idle(50)  # it wrote 50 min after the nudge: work, not a reply
        self.assertEqual(self.pre().stdout, "DUE proj improve idle=50min\n")

    def test_the_wait_stops_growing_at_eight_hours(self):
        self.conf(("proj", 3000))
        self.improve_on()
        self.stamp(400)
        self.assertEqual(self.pre().returncode, 1)
        self.stamp(481)
        self.assertEqual(self.pre().stdout, "DUE proj improve idle=3000min\n")

    def test_rest_stops_the_nudges_and_a_new_on_or_off_clears_it(self):
        self.conf(("proj", 90))
        self.improve_on()
        r = self.run_script("lane-improve", "rest", "proj")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.pre().returncode, 1)
        self.run_script("lane-improve", "off")
        self.assertFalse((self.conf_dir / "improve-rest.json").exists())

    def test_rest_lapses_after_twelve_hours_so_new_work_is_found(self):
        self.conf(("proj", 90))
        self.improve_on()
        self.run_script("lane-improve", "rest", "proj")
        f = self.conf_dir / "improve-rest.json"
        age(f, 11 * 60)
        d = json.loads(f.read_text())
        d["proj"] = (__import__("datetime").datetime.now().astimezone()
                     - __import__("datetime").timedelta(hours=13)).isoformat(timespec="seconds")
        f.write_text(json.dumps(d))
        self.assertEqual(self.pre().stdout, "DUE proj improve idle=90min\n")

    def test_rest_needs_the_mode_on_for_that_project(self):
        self.conf(("proj", 90))
        r = self.run_script("lane-improve", "rest", "proj")
        self.assertNotEqual(r.returncode, 0)
        self.improve_on(projects=["other"])
        self.assertNotEqual(self.run_script("lane-improve", "rest", "proj").returncode, 0)
        self.assertFalse((self.conf_dir / "improve-rest.json").exists())


class ImproveWrapUpNudgeTests(PrecheckBase):
    """lc-maw and lc-mf7: after the end the running coordinators are still nudged to wrap up, and an old drain says so."""

    PAST = "2020-01-01T00:00:00+00:00"

    def drain_on(self, pending=("proj",), hours_ago=2):
        import datetime as dt
        ended = (dt.datetime.now().astimezone() - dt.timedelta(hours=hours_ago)).isoformat(timespec="seconds")
        (self.conf_dir / "improve-drain.json").write_text(json.dumps({
            "started": "2026-11-23T10:00:00+00:00", "end": ended, "limits": "", "deny_paths": [], "projects": list(pending),
            "unmetered": False, "branches": {}, "integration": {}, "ended": ended, "pending": list(pending)}))

    def test_a_time_end_nudges_the_coordinator_to_wrap_up(self):
        self.conf(("proj", 31))
        self.improve_on(end=self.PAST)
        r = self.pre()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"^DUE proj wrapup idle=31min waited=\d+h\n$")

    def test_a_drain_after_off_nudges_only_the_pending_projects(self):
        self.conf(("a", 31), ("b", 31))
        self.drain_on(pending=["a"])
        self.assertEqual(self.pre().stdout, "DUE a wrapup idle=31min waited=2h\n")

    def test_a_drain_that_waited_long_says_how_long(self):
        self.conf(("proj", 31))
        self.drain_on(hours_ago=15)
        self.assertEqual(self.pre().stdout, "DUE proj wrapup idle=31min waited=15h\n")

    def test_checked_wrapup_waits_one_idle_period(self):
        self.conf(("proj", 31))
        self.drain_on()
        self.assertEqual(self.pre("--checked", "proj", "wrapup").returncode, 0)
        self.assertEqual(self.pre().returncode, 1)
        age(Path(self.env["HOME"]) / ".cache" / "lane-control" / "night" / "proj.wrapup", 31)
        self.assertEqual(self.pre().stdout, "DUE proj wrapup idle=31min waited=2h\n")

    def test_wrapup_gets_nothing_at_the_stop_step(self):
        self.conf(("proj", 31))
        self.drain_on()
        self.playbook(92, 10)  # no turn is spent at the stop step, a wrap-up included
        r = self.pre()
        self.assertEqual(r.returncode, 1)
        self.assertIn("stop step", r.stderr)

    def test_no_wrapup_nudge_when_nothing_is_left(self):
        self.conf(("proj", 31))
        self.assertEqual(self.pre().returncode, 1)
        self.drain_on(pending=["other"])
        self.assertEqual(self.pre().returncode, 1)


class CapPrecheckTests(PrecheckBase):
    """A cap that is reached ends a project's cycles like the end time: no improve nudge, a wrap-up line instead (lc-2nz)."""

    def test_items_cap_reached_gives_a_wrapup_line_not_an_improve_nudge(self):
        self.conf(("proj", 31))
        self.improve_on(max_items=2, stop_at=None)
        self.period({"proj": 2})
        r = self.pre()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "DUE proj wrapup idle=31min cap=items:2/2\n")

    def test_items_cap_not_reached_nudges_as_before(self):
        self.conf(("proj", 31))
        self.improve_on(max_items=3, stop_at=None)
        self.period({"proj": 2})
        self.assertEqual(self.pre().stdout, "DUE proj improve idle=31min\n")

    def test_the_count_is_per_project(self):
        self.conf(("a", 31), ("b", 31))
        self.improve_on(max_items=2, stop_at=None)
        self.period({"a": 2, "b": 1})
        self.assertCountEqual(self.pre().stdout.splitlines(), ["DUE a wrapup idle=31min cap=items:2/2", "DUE b improve idle=31min"])

    def test_a_record_of_an_older_period_does_not_count(self):
        self.conf(("proj", 31))
        self.improve_on(max_items=1, stop_at=None)
        (self.conf_dir / "improve-period.json").write_text(json.dumps({
            "period": "2026-11-01T10:00:00+00:00", "wrapped": ["proj"], "merges": {"proj": [{"pr": 1, "item": "x", "sha": "a", "at": "t"}]}}))
        self.assertEqual(self.pre().stdout, "DUE proj improve idle=31min\n")

    def test_weekly_cap_reached_gives_a_wrapup_line(self):
        self.playbook(66, 10)
        self.conf(("proj", 31))
        self.improve_on(stop_at=66, max_items=None)
        self.assertEqual(self.pre().stdout, "DUE proj wrapup idle=31min cap=weekly:66/66\n")

    def test_weekly_cap_not_reached_nudges(self):
        self.playbook(65, 10)
        self.conf(("proj", 31))
        self.improve_on(stop_at=66, max_items=None)
        self.assertEqual(self.pre().stdout, "DUE proj improve idle=31min\n")

    def test_a_wrapped_up_project_gets_nothing(self):
        self.conf(("proj", 31))
        self.improve_on(max_items=2, stop_at=None)
        self.period({"proj": 2}, wrapped=["proj"])
        r = self.pre()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")

    def test_a_reached_cap_beats_rest(self):
        self.conf(("proj", 31))
        self.improve_on(max_items=1, stop_at=None)
        self.period({"proj": 1})
        (self.conf_dir / "improve-rest.json").write_text(json.dumps({"proj": "2999-01-01T00:00:00+00:00"}))
        self.assertEqual(self.pre().stdout, "DUE proj wrapup idle=31min cap=items:1/1\n")

    def test_checked_wrapup_waits_one_idle_period_for_a_cap_too(self):
        self.conf(("proj", 31))
        self.improve_on(max_items=1, stop_at=None)
        self.period({"proj": 1})
        self.assertEqual(self.pre("--checked", "proj", "wrapup").returncode, 0)
        self.assertEqual(self.pre().returncode, 1)
        age(Path(self.env["HOME"]) / ".cache" / "lane-control" / "night" / "proj.wrapup", 31)
        self.assertEqual(self.pre().stdout, "DUE proj wrapup idle=31min cap=items:1/1\n")

    def test_no_wrapup_nudge_at_the_stop_step(self):
        self.playbook(92, 10)
        self.conf(("proj", 31))
        self.improve_on(max_items=1, stop_at=None)
        self.period({"proj": 1})
        r = self.pre()
        self.assertEqual(r.returncode, 1)
        self.assertIn("stop step", r.stderr)

    def test_unmetered_items_cap_needs_no_budget(self):
        self.playbook(None, None)
        self.conf(("proj", 31))
        self.improve_on(unmetered=True, max_items=1, stop_at=None)
        self.period({"proj": 1})
        self.assertEqual(self.pre().stdout, "DUE proj wrapup idle=31min cap=items:1/1\n")

    def test_the_other_projects_are_not_held_back(self):
        self.conf(("a", 31), ("b", 31))
        self.improve_on(projects=["a"], max_items=1, stop_at=None)
        self.period({"a": 1})
        self.assertEqual(self.pre().stdout, "DUE a wrapup idle=31min cap=items:1/1\n")


class NightManagerImproveDocTests(unittest.TestCase):
    def test_night_manager_knows_the_improve_line(self):
        text = " ".join((Path(__file__).resolve().parent.parent / "orchestrators" / "native-orca" / "night-manager.md")
                        .read_text().split())
        self.assertIn("`DUE <name> improve idle=<n>min`", text)
        self.assertIn("away-improve", text)
        self.assertIn("night-precheck --checked <name> improve", text)
        self.assertIn("never turn improve mode on", text)

    def test_night_manager_knows_the_cap_wrapup_line(self):
        text = " ".join((Path(__file__).resolve().parent.parent / "orchestrators" / "native-orca" / "night-manager.md")
                        .read_text().split())
        self.assertIn("cap=<items|weekly>:<n>/<max>", text)


if __name__ == "__main__":
    unittest.main()
