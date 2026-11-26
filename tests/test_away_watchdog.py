"""core/scripts/away-watchdog (field report B1, F3, F6) and what it reads: the cycle state, the ledger, the caps and
`status --json` of lane-improve (B3, B4, B5), and the summary PR's body written over REST (B6). Temp HOME and XDG, temp git
repos, stubs for orca-ide and gh; nothing real is sent, merged or edited."""
import json
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lane_away import ImproveTest  # noqa: E402
from test_machine_setup import write_exec  # noqa: E402

# orca-ide stand-in for the watchdog: `terminal list`, `orchestration worker-show` and `orchestration send`, answered from
# $WD_ORCA (JSON: terminals, workers {dispatch: {verdict, outcome}}, send_fail [addresses], down); every argv is logged as
# a JSON line to $WD_ORCA_LOG, and each accepted message (its body read from --body-file) is kept in state["sent"].
WD_ORCA_STUB = r"""
#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
open(os.environ["WD_ORCA_LOG"], "a").write(json.dumps(a) + "\n")
sp = os.environ["WD_ORCA"]
st = json.load(open(sp)) if os.path.exists(sp) else {}
def opt(n):
    return a[a.index(n) + 1] if n in a else None
def out(res, ok=True, msg=""):
    json.dump(st, open(sp, "w"))
    print(json.dumps({"ok": True, "result": res} if ok else {"ok": False, "error": {"message": msg}}))
    sys.exit(0 if ok else 1)
if st.get("down"):
    out(None, False, "runtime not reachable")
if a[:2] == ["terminal", "list"]:
    t = st.get("terminals", [])
    out({"terminals": t, "totalCount": len(t), "truncated": False})
if a[:2] == ["orchestration", "worker-show"]:
    w = st.get("workers", {}).get(opt("--dispatch"))
    if w is None:
        out(None, False, "no such dispatch")
    out({"projection": {"liveness": {"verdict": w["verdict"]}, "outcome": w["outcome"], "stage": {"activity": "working"}}})
if a[:2] == ["orchestration", "worker-list"]:
    out({"workers": st.get("list", [])})
if a[:2] == ["orchestration", "send"]:
    if "--body" in a:
        print("stub: a body must go through --body-file", file=sys.stderr); sys.exit(1)
    if not opt("--from"):  # like Orca 1.4.274: no --from and no terminal of its own (a precheck) has no sender
        out(None, False, "no_active_sender_terminal")
    seen = os.environ.get("WD_BACKOFF_SEEN")
    if seen:  # was the message's backoff written before the send?
        st.setdefault("backoff_seen", []).append(os.path.exists(seen))
    if opt("--to") in st.get("send_fail", []):
        out(None, False, "unknown recipient")
    st.setdefault("sent", []).append({"to": opt("--to"), "from": opt("--from"), "type": opt("--type"), "subject": opt("--subject"),
                                      "body": open(opt("--body-file"), encoding="utf-8").read()})
    out({"message": {"id": "msg_%d" % len(st["sent"])}})
print("stub: not modelled: " + " ".join(a), file=sys.stderr); sys.exit(1)
"""


class WatchTest(ImproveTest):
    def setUp(self):
        super().setUp()
        write_exec(self.bin / "orca-ide", WD_ORCA_STUB)
        self.env.update(WD_ORCA=str(self.tmp / "wd-orca.json"), WD_ORCA_LOG=str(self.tmp / "wd-orca.log"))
        self.mc = self.home / "machine-coordinator"
        self.mc.mkdir()
        self.conf(night_end=24)  # no daily summary unless a test asks for one
        self.playbook(20, 10)    # a metered budget at step 0: nudges may go out (M3)
        self.orca_state(terminals=[self.term("term_shop", self.shop), self.term("term_mc", self.mc)])
        r = self.on("--project", "shop")
        self.assertEqual(r.returncode, 0, r.stderr)

    def conf(self, night_end=24, extra=""):
        self.machine_conf('COORDINATORS="\nshop %s/shop claude shop coordinator\nblog %s/blog claude blog coordinator\n"\n'
                          'NIGHT_END=%d\n%s' % (self.tmp, self.tmp, night_end, extra))

    @staticmethod
    def term(handle, folder, title="a title"):
        return {"handle": handle, "agentIdentity": "claude", "worktreePath": str(folder), "title": title, "connected": True}

    def orca_state(self, **st):
        f = self.tmp / "wd-orca.json"
        old = json.loads(f.read_text()) if f.exists() else {}
        old.update(st)
        f.write_text(json.dumps(old))

    def sent(self) -> list:
        f = self.tmp / "wd-orca.json"
        return json.loads(f.read_text()).get("sent", []) if f.exists() else []

    def orca_calls(self) -> list:
        f = self.tmp / "wd-orca.log"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def at(self, minutes) -> dict:
        return {"LANE_NOW": str(time.time() + minutes * 60)}

    def git_heads(self, name) -> str:
        from test_lane_away import git
        return git(self.tmp / f"{name}.git", "for-each-ref", "--format=%(refname) %(objectname)")

    def watch(self, minutes=0, send=True, env=None):
        """One round as the automation runs it: the precheck, then (when it exits 0) its agent's `--send` from its own
        terminal. stdout is both; returncode the precheck's; .send the agent's run (or None)."""
        from types import SimpleNamespace
        d = self.run_script("away-watchdog", env={**self.at(minutes), **(env or {})})
        s = (self.run_script("away-watchdog", "--send", env={**self.at(minutes), "ORCA_TERMINAL_HANDLE": "term_auto",
                                                              **(env or {})}) if send and d.returncode == 0 else None)
        return SimpleNamespace(stdout=d.stdout + (s.stdout if s else ""), stderr=d.stderr + (s.stderr if s else ""),
                               returncode=d.returncode, send=s)

    def cycle(self, *args, minutes=0, env=None):
        return self.run_script("lane-improve", "cycle", "shop", *args, env={**self.at(minutes), **(env or {})})

    def build_lane(self, budget=240, live=True, address="term_shop"):
        r = self.cycle("--phase", "build", "--item", "Add the x feature", "--pr", "12", "--dispatch", "ctx_lane1",
                       "--worktree", str(self.tmp / "lane-wt"), "--budget-min", str(budget), "--address", address)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.orca_state(workers={"ctx_lane1": {"verdict": "live" if live else "gone",
                                               "outcome": "in_progress" if live else "succeeded"}})


class WatchdogTests(WatchTest):
    def test_a_live_lane_before_its_deadline_is_left_alone(self):
        self.build_lane()
        r = self.watch(100)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.sent(), [])

    def test_no_live_worker_and_quiet_nudges_the_recorded_address_with_the_evidence(self):
        self.build_lane(live=False)
        r = self.watch(50)
        self.assertEqual(r.returncode, 0, "something to send: the automation's agent starts")
        self.assertIn("DUE NUDGE shop to term_shop", r.stdout)
        self.assertIn("SENT NUDGE shop to term_shop", r.stdout)
        self.assertEqual(r.send.returncode, 0, r.send.stdout + r.send.stderr)
        (m,) = self.sent()
        self.assertEqual((m["to"], m["from"], m["type"]), ("term_shop", "term_auto", "status"))
        for part in ("item: Add the x feature", "PR: #12", "idle: 5", "worker ctx_lane1: liveness gone, outcome succeeded",
                     "phase: build, review round 0", "merges nothing", "do not reply"):
            self.assertIn(part, m["body"])

    def test_quiet_for_less_than_the_threshold_sends_nothing(self):
        self.build_lane(live=False)
        self.assertEqual(self.watch(20).returncode, 1)
        self.assertEqual(self.sent(), [])

    def test_the_nudge_backs_off_but_never_beyond_the_lane_budget(self):
        # no lane out: the default budget (240 min) bounds the wait; a lane's own budget ends in its deadline (below)
        times = [m for m in range(50, 50 + 15 * 60, 15) if "SENT NUDGE" in self.watch(m).stdout]
        self.assertEqual([b - a for a, b in zip(times, times[1:])], [45, 90, 180, 240, 240], times)

    def test_new_activity_starts_the_backoff_over(self):
        self.build_lane(live=False)
        self.assertIn("SENT NUDGE", self.watch(50).stdout)
        self.assertNotIn("SENT", self.watch(60).stdout)
        self.cycle("--phase", "review", "--round", "1", minutes=61)
        self.assertNotIn("SENT", self.watch(70).stdout)   # 9 minutes after the new step
        self.assertIn("SENT NUDGE", self.watch(110).stdout)

    def test_a_passed_deadline_asks_for_recovery_even_with_a_live_worker(self):
        self.build_lane(budget=30, live=True)
        r = self.watch(40)
        self.assertIn("SENT DEADLINE shop to term_shop", r.stdout)
        (m,) = self.sent()
        self.assertEqual(m["type"], "escalation")
        for part in ("past its deadline", "fresh worker on the same worktree and PR", str(self.tmp / "lane-wt"),
                     "--close parked", "budget 30 min", "liveness live"):
            self.assertIn(part, m["body"])

    def test_past_the_second_threshold_the_machine_coordinator_is_told(self):
        self.build_lane(live=False)
        r = self.watch(130)
        self.assertIn("SENT NUDGE shop to term_shop", r.stdout)
        self.assertIn("SENT ESCALATE shop to term_mc", r.stdout)
        esc = [m for m in self.sent() if m["to"] == "term_mc"][0]
        self.assertIn("item: Add the x feature", esc["body"])
        self.assertIn("PR: #12", esc["body"])

    def test_an_explicit_machine_address_wins(self):
        self.conf(extra='MACHINE_COORDINATOR_TO="run:run_abc"\n')
        self.build_lane(live=False)
        self.assertIn("SENT ESCALATE shop to run:run_abc", self.watch(130).stdout)

    def test_orca_not_able_to_say_is_not_live(self):
        self.build_lane(live=True)
        self.orca_state(workers={})
        r = self.watch(50)
        self.assertIn("SENT NUDGE", r.stdout)
        self.assertIn("Orca could not say", self.sent()[0]["body"])

    def test_a_gone_recorded_terminal_falls_back_to_the_one_agent_terminal_in_the_checkout(self):
        self.build_lane(live=False, address="term_old")
        self.assertIn("SENT NUDGE shop to term_shop", self.watch(50).stdout)

    def test_a_title_is_never_matched(self):
        # the only terminal titled like the coordinator is in another folder; two agent terminals are in the checkout
        self.orca_state(terminals=[self.term("term_t", self.tmp / "elsewhere", "shop coordinator"),
                                   self.term("term_a", self.shop), self.term("term_b", self.shop), self.term("term_mc", self.mc)])
        self.build_lane(live=False, address="term_gone")
        r = self.watch(50)
        self.assertIn("UNADDRESSED shop", r.stdout)
        self.assertIn("2 agent terminals", r.stdout)
        self.assertNotIn("term_t", json.dumps(self.sent()))
        relay = [m for m in self.sent() if m["to"] == "term_mc"]
        self.assertEqual(len(relay), 1)
        self.assertIn("could not be reached", relay[0]["body"])
        self.assertEqual(r.send.returncode, 0, "told the machine coordinator: delivered")

    def test_nobody_reachable_fails_loudly_once_per_spell(self):
        self.orca_state(terminals=[])
        self.build_lane(live=False, address="term_gone")
        r = self.watch(50)
        self.assertEqual(r.returncode, 0, "the automation's agent starts: loud")
        self.assertIn("UNADDRESSED shop", r.stdout)
        self.assertIn("UNADDRESSED machine", r.stdout)
        self.assertEqual(self.run_script("away-watchdog", "--last").stdout, r.stdout)
        self.assertEqual(self.watch(65).returncode, 1, "the same problem is not made loud again at once")
        self.assertEqual(self.watch(50 + 125).returncode, 0, "but again after WATCHDOG_ESCALATE_MIN")

    def test_a_failed_send_is_not_a_delivery(self):
        self.orca_state(send_fail=["term_shop"])
        self.build_lane(live=False)
        r = self.watch(50)
        self.assertIn("UNADDRESSED shop (the send to term_shop failed (unknown recipient))", r.stdout)
        self.assertIn("SENT RELAY shop to term_mc", r.stdout)

    def test_orca_down_is_loud(self):
        self.build_lane(live=False)
        self.orca_state(down=True)
        r = self.watch(50)
        self.assertEqual(r.returncode, 0)
        self.assertIn("UNADDRESSED", r.stdout)

    def test_it_only_reads_and_sends(self):
        self.build_lane(budget=30, live=False)
        (self.tmp / "gh.log").unlink(missing_ok=True)
        heads = {b: self.git_heads(b) for b in ("shop", "blog")}
        self.watch(140)
        verbs = {tuple(c[:2]) for c in self.orca_calls()}
        self.assertLessEqual(verbs, {("terminal", "list"), ("orchestration", "worker-show"), ("orchestration", "worker-list"),
                                     ("orchestration", "send")})
        self.assertEqual(self.gh_log(), [])
        self.assertEqual({b: self.git_heads(b) for b in ("shop", "blog")}, heads)
        for c in self.orca_calls():
            if c[:2] == ["orchestration", "send"]:
                self.assertNotIn("--body", c)

    def test_a_rested_project_with_no_lane_and_a_period_that_ended_with_none_get_nothing(self):
        self.assertEqual(self.run_script("lane-improve", "rest", "shop").returncode, 0)
        self.assertEqual(self.watch(50).stdout, "")
        self.run_script("lane-improve", "off")
        self.assertEqual(self.watch(300).returncode, 1)
        self.assertEqual(self.sent(), [])

    def test_the_daily_summary_lists_merged_and_parked_and_a_spend_proxy_once(self):
        self.conf(night_end=0)
        self.build_lane()
        findings = self.tmp / "findings.md"
        findings.write_text("Round 2 still found the cache test flaky.\n")
        self.assertEqual(self.cycle("--close", "parked", "--findings-file", str(findings)).returncode, 0)
        r = self.watch(5)
        self.assertIn("SENT SUMMARY all to term_mc", r.stdout)
        (m,) = [m for m in self.sent() if "summary" in m["subject"]]
        for part in ("merged: 0", "parked: 1", "Add the x feature", "cache test flaky", "spend proxy: 1 cycles closed",
                     "review rounds"):
            self.assertIn(part, m["body"])
        self.assertNotIn("SUMMARY", self.watch(20).stdout)


class WatchdogReviewTests(WatchTest):
    """Review round 1 of AWY-2: B1 the sender, M1 crashes, M2 other live workers, M3 budget, m3 paused, M4 lanes after a
    cap or off, M6 one worker per worktree."""

    def test_the_precheck_sends_nothing_and_send_needs_a_terminal_of_its_own(self):  # B1
        self.build_lane(live=False)
        d = self.watch(50, send=False)
        self.assertEqual(d.returncode, 0)
        self.assertFalse([c for c in self.orca_calls() if c[:2] == ["orchestration", "send"]])
        r = self.run_script("away-watchdog", "--send", env=self.at(50))
        self.assertEqual(r.returncode, 2)
        self.assertIn("ORCA_TERMINAL_HANDLE", r.stderr)
        self.assertEqual(self.sent(), [])
        for c in self.orca_calls():
            if c[:2] == ["orchestration", "send"]:
                self.assertIn("--from", c)

    def test_a_crash_is_loud(self):  # M1
        bad = self.tmp / "not-a-dir"
        bad.write_text("x")
        self.build_lane(live=False)
        r = self.watch(50, send=False, env={"XDG_CACHE_HOME": str(bad)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("UNADDRESSED machine (away-watchdog failed", r.stdout)

    def test_the_backoff_is_written_before_the_send_so_a_crash_never_sends_twice(self):  # M1
        self.build_lane(live=False)
        seen = self.home / ".cache" / "lane-control" / "watchdog" / "shop.nudge.json"
        self.watch(50, env={"WD_BACKOFF_SEEN": str(seen)})
        self.assertEqual(json.loads((self.tmp / "wd-orca.json").read_text())["backoff_seen"], [True])
        again = self.watch(51)
        self.assertNotIn("DUE NUDGE", again.stdout)
        self.assertEqual(len(self.sent()), 1)

    def test_a_refused_send_is_said_again_next_time(self):  # the backoff is put back when the send fails
        self.orca_state(send_fail=["term_shop", "term_mc"])
        self.build_lane(live=False)
        self.assertIn("UNADDRESSED shop", self.watch(50).stdout)
        self.orca_state(send_fail=[])
        self.assertIn("SENT NUDGE shop", self.watch(52).stdout)

    def test_a_live_reviewer_or_any_live_worker_on_the_worktree_counts(self):  # M2
        self.build_lane(live=False)
        self.cycle("--phase", "review", "--round", "1", "--worker", "ctx_rev")
        self.orca_state(workers={"ctx_lane1": {"verdict": "gone", "outcome": "succeeded"},
                                 "ctx_rev": {"verdict": "live", "outcome": "in_progress"}})
        self.assertEqual(self.watch(130).stdout, "")
        self.orca_state(workers={"ctx_lane1": {"verdict": "gone", "outcome": "succeeded"}},
                        list=[{"dispatchId": "ctx_other", "liveness": "live", "outcome": "in_progress",
                               "workspace": "repo::" + str(self.tmp / "lane-wt")}])
        self.assertEqual(self.watch(131).stdout, "")
        rec = json.loads(self.cycle("--json").stdout)
        self.assertEqual((rec["dispatch"], rec["workers"]), ("ctx_lane1", ["ctx_rev"]))

    def test_a_review_round_back_to_build_with_the_reviewer_recorded_keeps_the_deadline(self):  # M2
        self.build_lane()
        dl = json.loads(self.cycle("--json").stdout)["deadline"]
        self.cycle("--phase", "review", "--round", "1", "--worker", "ctx_rev", minutes=10)
        self.cycle("--phase", "build", minutes=20)
        self.assertEqual(json.loads(self.cycle("--json").stdout)["deadline"], dl)

    def test_the_merge_phase_waits_on_ci_without_nudges(self):  # M2
        self.build_lane(live=False)
        self.cycle("--phase", "merge")
        self.assertEqual(self.watch(130).stdout, "")

    def test_metered_at_the_stop_step_or_with_no_reading_gets_nothing(self):  # M3
        for weekly in (92, None):
            with self.subTest(weekly=weekly):
                self.playbook(weekly, 10)
                self.assertEqual(self.watch(130).stdout, "")
        self.playbook(20, 10)
        self.assertIn("SENT NUDGE", self.watch(130).stdout)

    def test_a_paused_project_gets_nothing(self):  # m3
        import machineconf
        from unittest import mock
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            os.environ.pop("XDG_CONFIG_HOME", None)
            f = machineconf.settings_file(self.shop)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"paused": True}))
        self.build_lane(live=False)
        r = self.watch(130)
        self.assertEqual(r.stdout, "")
        self.assertIn("paused", r.stderr)

    def test_a_lane_out_after_off_is_watched_until_it_reports_but_asks_no_new_cycle(self):  # M4
        self.build_lane(live=False)
        self.run_script("lane-improve", "off")
        r = self.watch(50)
        self.assertIn("SENT NUDGE shop", r.stdout)
        self.assertIn("no new cycle", self.sent()[0]["body"])
        self.assertNotIn("start the next cycle", self.sent()[0]["body"])

    def test_a_lane_out_after_a_cap_is_watched(self):  # M4
        self.reset()
        self.assertEqual(self.on("--project", "shop", "--max-hours", "1").returncode, 0)
        self.build_lane(budget=240, live=False)
        r = self.watch(300)
        self.assertIn("SENT DEADLINE shop", r.stdout)

    def test_the_deadline_names_the_live_workers_on_the_worktree_and_never_two(self):  # M6
        self.build_lane(budget=30, live=False)
        self.orca_state(list=[{"dispatchId": "ctx_zombie", "liveness": "live", "outcome": "in_progress",
                               "workspace": "repo::" + str(self.tmp / "lane-wt")}])
        self.watch(40)
        body = self.sent()[0]["body"]
        self.assertIn("live workers on the worktree (Orca's worker list): ctx_zombie", body)
        self.assertIn("only once Orca shows every worker on it settled or stopped", body)
        self.assertIn("never reuse the worktree", body)


class CycleStateTests(WatchTest):
    def test_a_lane_gets_its_deadline_at_start_and_the_coordinators_own_address(self):
        r = self.cycle("--phase", "build", "--item", "Add y", "--dispatch", "ctx_9", "--budget-min", "90",
                       env={"ORCA_TERMINAL_HANDLE": "term_self"})
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = json.loads(self.cycle("--json").stdout)
        self.assertEqual((rec["phase"], rec["item"], rec["dispatch"], rec["address"], rec["round"]),
                         ("build", "Add y", "ctx_9", "term_self", 0))
        import datetime as dt
        left = dt.datetime.fromisoformat(rec["deadline"]) - dt.datetime.fromisoformat(rec["lane_started"])
        self.assertEqual(left, dt.timedelta(minutes=90))

    def test_resume_reads_it_back_and_a_review_round_keeps_the_deadline(self):
        self.build_lane()
        before = json.loads(self.cycle("--json").stdout)["deadline"]
        self.assertEqual(self.cycle("--phase", "review", "--round", "2", minutes=30).returncode, 0)
        rec = json.loads(self.cycle("--json").stdout)
        self.assertEqual((rec["deadline"], rec["round"], rec["pr"]), (before, 2, 12))
        out = self.cycle().stdout
        self.assertIn("phase review", out)
        self.assertIn("PR #12", out)
        self.assertIn("cycle: shop: phase review", self.run_script("lane-improve", "status").stdout)

    def test_a_build_round_after_a_review_is_the_same_lane(self):
        self.build_lane(budget=30)
        old = json.loads(self.cycle("--json").stdout)
        self.cycle("--phase", "review", "--round", "1", minutes=10)
        self.cycle("--phase", "build", "--dispatch", "ctx_lane1", minutes=15)
        rec = json.loads(self.cycle("--json").stdout)
        self.assertEqual((rec["deadline"], rec["lane_started"], rec["round"]), (old["deadline"], old["lane_started"], 1))

    def test_a_fresh_worker_on_the_same_item_gets_a_new_deadline_and_keeps_its_rounds(self):
        self.build_lane(budget=30)
        self.cycle("--phase", "review", "--round", "1", minutes=10)
        old = json.loads(self.cycle("--json").stdout)["deadline"]
        self.assertEqual(self.cycle("--phase", "build", "--dispatch", "ctx_lane2", "--budget-min", "60", minutes=40).returncode, 0)
        rec = json.loads(self.cycle("--json").stdout)
        self.assertNotEqual(rec["deadline"], old)
        self.assertEqual((rec["round"], rec["dispatch"], rec["item"]), (1, "ctx_lane2", "Add the x feature"))

    def test_refused_when_the_mode_is_off_and_for_a_bad_address(self):
        self.assertEqual(self.cycle("--phase", "scout", "--address", "shop coordinator").returncode, 2)
        self.assertEqual(self.run_script("lane-improve", "cycle", "blog", "--phase", "scout").returncode, 1)

    def test_parking_needs_findings_and_closes_the_cycle_into_the_ledger(self):
        self.build_lane()
        self.assertEqual(self.cycle("--close", "parked").returncode, 2)
        f = self.tmp / "f.md"
        f.write_text("two rounds, still red\n")
        self.assertEqual(self.cycle("--close", "parked", "--findings-file", str(f), "--metric", "none").returncode, 0)
        (row,) = [json.loads(x) for x in (self.conf_dir / "improve-ledger.jsonl").read_text().splitlines()]
        self.assertEqual((row["outcome"], row["item"], row["pr"], row["findings"], row["project"]),
                         ("parked", "Add the x feature", 12, "two rounds, still red", "shop"))
        rec = json.loads(self.cycle("--json").stdout)
        self.assertEqual((rec["phase"], rec["address"]), ("idle", "term_shop"))

    def test_a_cycle_file_of_another_period_is_not_read(self):
        self.build_lane()
        f = self.conf_dir / "improve-cycle.json"
        d = json.loads(f.read_text())
        d["period"] = "2000-01-01T00:00:00+00:00"
        f.write_text(json.dumps(d))
        self.assertEqual(json.loads(self.cycle("--json").stdout), {})


class StatusJsonTests(WatchTest):
    def test_one_line_per_project(self):
        self.build_lane(budget=30)
        r = self.run_script("lane-improve", "status", "--json", env=self.at(45))
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = {x["project"]: x for x in map(json.loads, r.stdout.splitlines())}
        self.assertEqual(set(rows), {"shop", "blog"})
        s = rows["shop"]
        self.assertEqual((s["improve"], s["item"], s["phase"], s["pr"], s["deadline_passed"]),
                         ("on", "Add the x feature", "build", 12, True))
        self.assertGreaterEqual(s["last_activity_age_min"], 44)
        self.assertTrue(s["branch"].startswith("away/"))
        self.assertEqual((rows["blog"]["improve"], rows["blog"]["phase"]), ("off", None))

    def test_off_still_gives_a_line_per_project(self):
        self.reset()
        rows = [json.loads(x) for x in self.run_script("lane-improve", "status", "--json").stdout.splitlines()]
        self.assertEqual({(x["project"], x["improve"]) for x in rows}, {("shop", "off"), ("blog", "off")})


class MaxHoursTests(ImproveTest):
    def test_recorded_shown_and_reached(self):
        r = self.on("--project", "shop", "--max-hours", "6")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.state()["max_hours"], 6)
        self.assertIn("6 hours of wall clock", r.stdout)
        self.assertIn("hours 0.0/6", self.run_script("lane-improve", "status").stdout)
        later = {"LANE_NOW": str(time.time() + 7 * 3600)}
        out = self.run_script("lane-improve", "status", env=later).stdout
        self.assertIn("cap reached (hours:7.0/6)", out)
        self.assertEqual(self.run_script("lane-improve", "drained", "shop", env=later).returncode, 0)

    def test_unmetered_keeps_a_wall_clock_bound_by_default(self):
        r = self.on("--project", "shop", "--unmetered")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.state()["max_hours"], 12)
        self.assertIn("a bound of 12 h applies", r.stdout)

    def test_the_owner_can_drop_it_by_name_or_bound_it_with_an_end(self):
        self.assertEqual(self.on("--project", "shop", "--unmetered", "--max-hours", "none").returncode, 0)
        self.assertIsNone(self.state()["max_hours"])
        self.reset()
        self.assertEqual(self.on("--project", "shop", "--unmetered", "--hours", "3").returncode, 0)
        self.assertIsNone(self.state()["max_hours"])

    def test_a_continue_rerun_keeps_or_changes_it(self):
        self.on("--project", "shop", "--max-hours", "6")
        self.assertEqual(self.on("--continue", "--hours", "2").returncode, 0)
        self.assertEqual(self.state()["max_hours"], 6)
        self.assertEqual(self.on("--continue", "--max-hours", "8.5").returncode, 0)
        self.assertEqual(self.state()["max_hours"], 8.5)

    def test_every_unmetered_period_with_no_end_keeps_a_bound(self):  # M5
        self.assertEqual(self.on("--project", "shop", "--unmetered", "--hours", "3").returncode, 0)
        self.assertIsNone(self.state()["max_hours"])
        r = self.on("--continue", "--until-back")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.state()["unmetered"], self.state()["end"], self.state()["max_hours"]), (True, "until-back", 12))
        self.assertIn("a bound of 12 h applies", r.stdout)

    def test_none_is_kept_across_reruns_until_a_value_is_given(self):  # M5
        self.assertEqual(self.on("--project", "shop", "--unmetered", "--max-hours", "none").returncode, 0)
        self.assertTrue(self.state()["max_hours_off"])
        self.assertEqual(self.on("--continue", "--until-back").returncode, 0)
        self.assertIsNone(self.state()["max_hours"])
        self.assertEqual(self.on("--continue", "--max-hours", "5").returncode, 0)
        self.assertEqual((self.state()["max_hours"], self.state()["max_hours_off"]), (5, False))

    def test_a_new_cycle_is_refused_once_the_cap_is_reached(self):  # m1
        self.on("--project", "shop", "--max-hours", "1")
        later = {"LANE_NOW": str(time.time() + 2 * 3600)}
        r = self.run_script("lane-improve", "cycle", "shop", "--phase", "scout", "--dispatch", "ctx_new", env=later)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("a cap is reached (hours:", r.stderr)
        self.assertEqual(self.run_script("lane-improve", "cycle", "shop", "--phase", "scout").returncode, 0)  # before it
        self.run_script("lane-improve", "cycle", "shop", "--phase", "build", "--item", "x")
        self.run_script("lane-improve", "off")
        r = self.run_script("lane-improve", "cycle", "shop", "--phase", "scout")
        self.assertEqual(r.returncode, 1)
        self.assertIn("draining", r.stderr)
        self.assertEqual(self.run_script("lane-improve", "cycle", "shop", "--phase", "review", "--round", "1").returncode, 0)

    def test_a_wrap_up_at_max_hours_survives_a_lost_record_as_unknown(self):  # m2, m4
        self.on("--project", "shop", "--max-hours", "1")
        later = {"LANE_NOW": str(time.time() + 2 * 3600)}
        self.assertEqual(self.run_script("lane-improve", "drained", "shop", env=later).returncode, 0)
        (self.conf_dir / "improve-period.json").unlink()
        rows = {x["project"]: x for x in map(json.loads, self.run_script("lane-improve", "status", "--json", env=later).stdout.splitlines())}
        self.assertEqual(rows["shop"]["improve"], "unknown")
        self.assertIn("missing", rows["shop"]["error"])
        self.assertIn("period record", self.run_script("lane-improve", "status", env=later).stdout)

    def test_status_json_says_a_broken_state_file_is_unknown(self):  # m4
        self.improve_file().write_text("{junk")
        rows = [json.loads(x) for x in self.run_script("lane-improve", "status", "--json").stdout.splitlines()]
        self.assertTrue(rows)
        self.assertEqual({x["improve"] for x in rows}, {"unknown"})
        self.assertIn("not valid JSON", rows[0]["error"])

    def test_bad_values_are_refused(self):
        for v in ("0", "-1", "x", "nan", "100000"):
            self.assertEqual(self.on("--project", "shop", "--max-hours", v).returncode, 2, v)
        self.assertFalse(self.improve_file().exists())

    def test_the_night_manager_gets_a_wrapup_for_it(self):
        self.on("--project", "shop", "--max-hours", "1")
        self.claude_coordinator_at(self.shop)
        r = self.run_script("night-precheck", env={"LANE_NOW": str(time.time() + 2 * 3600)})
        self.assertIn("DUE shop wrapup", r.stdout + r.stderr)
        self.assertIn("cap=hours:", r.stdout)

    def claude_coordinator_at(self, checkout):
        import harnesses
        from test_machine_setup import TRANSCRIPT_SID, age
        d = self.home / ".claude" / "projects" / harnesses.encode_path(checkout.resolve())
        d.mkdir(parents=True)
        f = d / f"{TRANSCRIPT_SID}.jsonl"
        f.write_text("{}\n")
        age(f, 200)
        self.record_session(checkout, "claude", TRANSCRIPT_SID)


class RestBodyTests(ImproveTest):
    def edits(self):
        return [c for c in self.gh_log() if c.startswith(("pr edit", "api --method PATCH"))]

    def test_the_body_is_patched_over_rest_by_default(self):
        self.assertEqual(self.on("--project", "shop").returncode, 0)
        self.assertEqual(self.run_script("lane-improve", "pr", "shop").returncode, 0)
        (e,) = self.edits()
        self.assertTrue(e.startswith("api --method PATCH repos/{owner}/{repo}/pulls/"), e)
        self.assertIn("--input", e)
        self.assertIn("Items merged", self.db()[e.split("/pulls/")[1].split()[0]]["body"])

    def test_pr_edit_is_the_fallback_and_both_failing_says_both(self):
        self.assertEqual(self.on("--project", "shop").returncode, 0)
        r = self.run_script("lane-improve", "pr", "shop", env={"GH_PATCH_FAIL": "HTTP 502"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([e.split()[0:2] for e in self.edits()], [["api", "--method"], ["pr", "edit"]])
        r = self.run_script("lane-improve", "pr", "shop", env={"GH_PATCH_FAIL": "HTTP 502", "GH_EDIT_FAIL": "projectCards"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("REST PATCH failed", r.stderr)
        self.assertIn("projectCards", r.stderr)


if __name__ == "__main__":
    unittest.main()
