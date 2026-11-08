"""lane-heavy and lane-room against stubbed systemd and a fake /proc; only temp dirs, and no real load."""
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEAVY = ROOT / "scripts" / "lane-heavy"
ROOM = ROOT / "scripts" / "lane-room"
LIMITS = ROOT / "scripts" / "lane-limits.sh"
EXAMPLE = ROOT / "limits.example.sh"
SANDBOX = ROOT / "scripts" / "opencode-sandbox.sh"
MARK = "# --- the machine-wide heavy-work gate"
TIMEOUT = 20

# The fake scope: the probe answers with a real memory limit, anything else runs the command after `--`. Like the real
# one it rewrites $$ to $ in the command unless told not to; old-systemd refuses that option and does not rewrite, unless
# old-expands says it does too.
SYSTEMD_RUN = """#!/usr/bin/env sh
echo "$*" >> "$STUB_DIR/systemd-run.log"
limit=1073741824
expand=1
case "$*" in *--expand-environment=no*)
  [ -e "$STUB_DIR/old-systemd" ] && { echo "unrecognized option" >&2; exit 1; }
  expand= ;;
esac
[ -e "$STUB_DIR/old-systemd" ] && [ ! -e "$STUB_DIR/old-expands" ] && expand=
case "$*" in *-probe*)
  [ -e "$STUB_DIR/no-scope" ] && exit 1
  if [ -n "$expand" ]; then echo "\\$ $limit"; else echo "\\$\\$ $limit"; fi
  exit 0 ;;
esac
while [ "$1" != "--" ]; do shift; done
shift
if [ -n "$expand" ]; then
  for a; do shift; set -- "$@" "$(printf %s "$a" | sed 's/[$][$]/$/g')"; done
fi
exec "$@"
"""
SYSTEMCTL = """#!/usr/bin/env sh
echo "$*" >> "$STUB_DIR/systemctl.log"
case "$*" in *show*) [ -e "$STUB_DIR/scope-result" ] && cat "$STUB_DIR/scope-result" ;; esac
exit 0
"""


class Machine(unittest.TestCase):
    """A fake 12-core, 64G machine with a free slot directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.proc = self.dir / "proc"
        self.stubs = self.dir / "stubs"
        self.proc.mkdir()
        self.stubs.mkdir()
        self.set_proc(avail_gb=40, load5=2)
        (self.proc / "vmstat").write_text("oom_kill 5\n")
        self.limits = self.dir / "limits.sh"
        self.write_limits(LANE_HEAVY_SLOTS=1, LANE_HEAVY_MEM_GB=3, LANE_HEAVY_POLL_SECS=0.05)
        self.stub("nproc", "#!/usr/bin/env sh\necho 12\n")
        self.stub("systemd-run", SYSTEMD_RUN)
        self.stub("systemctl", SYSTEMCTL)
        self.env = dict(os.environ, PATH=f"{self.stubs}:{os.environ['PATH']}", STUB_DIR=str(self.stubs),
                        LANE_PROC_DIR=str(self.proc), LANE_STATE_DIR=str(self.dir / "state"),
                        LANE_LIMITS_FILE=str(self.limits))
        self.env.pop("LANE_HEAVY_HOLDS_SLOT", None)

    def stub(self, name, body):
        path = self.stubs / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def set_proc(self, avail_gb, load5):
        (self.proc / "meminfo").write_text(f"MemTotal: {64 * 1048576} kB\nMemAvailable: {avail_gb * 1048576} kB\n")
        (self.proc / "loadavg").write_text(f"1.00 {load5}.00 3.00 1/100 200\n")

    def write_limits(self, **settings):
        self.limits.write_text("".join(f"{k}={v}\n" for k, v in settings.items()))

    def limits_seen(self, cores=12, mem_gb=64):
        self.stub("nproc", f"#!/usr/bin/env sh\necho {cores}\n")
        (self.proc / "meminfo").write_text(f"MemTotal: {mem_gb * 1048576} kB\nMemAvailable: 1048576 kB\n")
        names = "LANE_HEAVY_SLOTS LANE_HEAVY_MEM_GB LANE_ROOM_MIN_MEM_GB LANE_ROOM_MAX_LOAD LANE_ROOM_MAX_AGENTS".split()
        show = "; ".join(f'echo "{n}=${n}"' for n in names)
        r = subprocess.run(["bash", "-c", f". {LIMITS}; {show}"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.stderr = r.stderr
        return {k: int(v) for k, v in (l.split("=") for l in r.stdout.split())}

    def heavy(self, *cmd, **kw):
        return subprocess.run([str(HEAVY), *cmd], env=self.env, capture_output=True, text=True, timeout=TIMEOUT, **kw)

    def start(self, *cmd):
        p = subprocess.Popen([str(HEAVY), *cmd], env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.reap, p)
        return p

    @staticmethod
    def reap(p):
        if p.poll() is None:
            p.kill()
        p.communicate()

    def wait_for(self, path, seconds=TIMEOUT):
        end = time.time() + seconds
        while not path.exists():
            self.assertLess(time.time(), end, f"{path} never appeared")
            time.sleep(0.02)

    def holder(self, name):
        """A job that holds a slot until its release file appears."""
        started, release = self.dir / f"{name}.started", self.dir / f"{name}.release"
        p = self.start("sh", "-c", f'touch {started}; until [ -e {release} ]; do sleep 0.02; done')
        self.wait_for(started)
        return p, release


class HeavyTests(Machine):
    def test_exit_code_passes_through(self):
        r = self.heavy("sh", "-c", "exit 7")
        self.assertEqual(r.returncode, 7)
        self.assertEqual(r.stderr, "")

    def test_stdin_and_stdout_are_the_commands(self):
        r = self.heavy("cat", input="through the gate\n")
        self.assertEqual((r.returncode, r.stdout), (0, "through the gate\n"))

    def test_runs_in_a_low_priority_memory_limited_scope(self):
        self.assertEqual(self.heavy("true").returncode, 0)
        run = [l for l in (self.stubs / "systemd-run.log").read_text().splitlines() if "-probe" not in l]
        self.assertEqual(len(run), 1)
        for needle in ("--user --scope", "MemoryMax=3G", "MemorySwapMax=0", "nice -n 19", "ionice -t -c 2 -n 7"):
            self.assertIn(needle, run[0])

    def job_lines(self):
        return [l for l in (self.stubs / "systemd-run.log").read_text().splitlines() if "-probe" not in l]

    DOLLARS = 'x=kept; echo "$x $$"'

    def assert_dollars_survived(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"^kept \d+\n$")

    def test_dollars_reach_the_job_unchanged(self):
        r = self.heavy("sh", "-c", self.DOLLARS)
        self.assert_dollars_survived(r)
        self.assertEqual(r.stderr, "")
        self.assertIn("--expand-environment=no", self.job_lines()[0])

    def test_systemd_too_old_for_the_option_still_gets_the_scope(self):
        (self.stubs / "old-systemd").touch()
        r = self.heavy("sh", "-c", self.DOLLARS)
        self.assert_dollars_survived(r)
        self.assertEqual(r.stderr, "")
        self.assertNotIn("--expand-environment", self.job_lines()[0])
        self.assertIn("MemoryMax=3G", self.job_lines()[0])

    def test_a_scope_that_would_rewrite_the_job_is_not_used(self):
        (self.stubs / "old-systemd").touch()
        (self.stubs / "old-expands").touch()
        r = self.heavy("sh", "-c", self.DOLLARS)
        self.assert_dollars_survived(r)
        self.assertEqual(r.stderr.count("no memory limit"), 1)
        self.assertEqual(self.job_lines(), [])

    def test_no_working_scope_still_runs_and_says_so(self):
        (self.stubs / "no-scope").touch()
        r = self.heavy("sh", "-c", "exit 3")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stderr.count("no memory limit"), 1)

    def test_scope_that_ignores_the_limit_counts_as_no_limit(self):
        # systemd-run succeeds, but the memory controller is not delegated: the scope reads back as "max" or nothing.
        self.stub("systemd-run", SYSTEMD_RUN.replace("limit=1073741824", "limit=max"))
        r = self.heavy("true")
        self.assertEqual(r.returncode, 0)
        self.assertIn("no memory limit", r.stderr)

    def test_oom_killed_scope_is_plain(self):
        (self.stubs / "scope-result").write_text("ActiveState=failed\nResult=oom-kill\n")
        r = self.heavy("sh", "-c", "exit 143")
        self.assertEqual(r.returncode, 143)
        self.assertIn("OUT OF MEMORY", r.stderr)
        self.assertNotIn("POSSIBLE", r.stderr)
        self.assertIn("reset-failed", (self.stubs / "systemctl.log").read_text())

    def test_machine_wide_oom_during_a_failed_job_is_named(self):
        self.write_limits(LANE_HEAVY_SLOTS=1, LANE_HEAVY_MEM_GB=0, LANE_HEAVY_POLL_SECS=0.05)
        r = self.heavy("sh", "-c", f'echo "oom_kill 8" > {self.proc}/vmstat; exit 137')
        self.assertEqual(r.returncode, 137)
        self.assertIn("POSSIBLE OUT OF MEMORY", r.stderr)
        self.assertIn("killed 3 process", r.stderr)

    def test_ordinary_failure_mentions_no_oom(self):
        r = self.heavy("sh", "-c", "exit 1")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("OUT OF MEMORY", r.stderr)

    def test_waits_for_a_slot_and_says_so_once(self):
        holder, release = self.holder("first")
        marker = self.dir / "second.ran"
        waiter = self.start("sh", "-c", f"touch {marker}")
        time.sleep(0.3)   # many polls: the message must not repeat, and the job must not have started
        self.assertFalse(marker.exists())
        release.touch()
        _, err = waiter.communicate(timeout=TIMEOUT)
        self.assertEqual(waiter.returncode, 0)
        self.assertTrue(marker.exists())
        self.assertEqual(err.count("waiting for a turn"), 1, err)
        self.assertEqual(holder.wait(timeout=TIMEOUT), 0)

    def test_never_more_than_the_slots(self):
        self.write_limits(LANE_HEAVY_SLOTS=2, LANE_HEAVY_MEM_GB=0, LANE_HEAVY_POLL_SECS=0.02)
        log = self.dir / "overlap.log"
        script = f'echo in >> {log}; sleep 0.15; echo out >> {log}'
        jobs = [self.start("sh", "-c", script) for _ in range(5)]
        for j in jobs:
            j.communicate(timeout=TIMEOUT)
            self.assertEqual(j.returncode, 0)
        running = peak = 0
        for line in log.read_text().split():
            running += 1 if line == "in" else -1
            peak = max(peak, running)
        self.assertEqual(peak, 2)

    def test_killed_job_frees_its_slot(self):
        pidfile = self.dir / "job.pid"
        p = self.start("sh", "-c", f'echo $$ > {pidfile}.tmp; mv {pidfile}.tmp {pidfile}; exec sleep 60')
        self.wait_for(pidfile)
        os.kill(int(pidfile.read_text()), signal.SIGKILL)
        p.communicate(timeout=TIMEOUT)
        self.assertEqual(p.returncode, 137)
        r = self.heavy("true")
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def sigkill_the_gate(self):
        """SIGKILL lane-heavy while its job runs; the job's pid."""
        pidfile = self.dir / "job.pid"
        p = self.start("sh", "-c", f'echo $$ > {pidfile}.tmp; mv {pidfile}.tmp {pidfile}; exec sleep 60')
        self.wait_for(pidfile)
        job = int(pidfile.read_text())
        self.addCleanup(lambda: self.is_running(job) and os.kill(job, signal.SIGKILL))
        p.kill()
        p.wait(timeout=TIMEOUT)
        return job

    @unittest.skipIf(subprocess.run("setpriv --pdeathsig KILL true", shell=True, capture_output=True).returncode,
                     "this host's setpriv has no --pdeathsig")
    def test_sigkilled_gate_takes_the_job_with_it(self):
        job = self.sigkill_the_gate()
        end = time.time() + TIMEOUT
        while self.is_running(job):
            self.assertLess(time.time(), end, "the job outlived the gate")
            time.sleep(0.02)
        r = self.heavy("true")
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_setpriv_without_pdeathsig_is_not_used(self):
        self.stub("setpriv", '#!/usr/bin/env sh\necho "setpriv: unrecognized option" >&2\nexit 1\n')
        r = self.heavy("echo", "ran")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "ran"))
        job = self.sigkill_the_gate()   # the limit the header states: the job lives on, and the slot still frees
        self.assertTrue(self.is_running(job))
        r = self.heavy("true")
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_term_stops_the_job_and_reports_it(self):
        self.write_limits(LANE_HEAVY_SLOTS=1, LANE_HEAVY_MEM_GB=0, LANE_HEAVY_POLL_SECS=0.05)
        mark, done = self.dir / "term.started", self.dir / "term.handled"
        p = self.start("sh", "-c", f"trap 'touch {done}; exit 143' TERM; touch {mark}; while :; do sleep 0.02; done")
        self.wait_for(mark)
        p.terminate()
        p.communicate(timeout=TIMEOUT)
        self.assertEqual(p.returncode, 143)
        self.assertTrue(done.exists())
        self.assertEqual(self.heavy("true").returncode, 0)

    def test_term_stops_every_step_of_a_multi_step_job_without_a_scope(self):
        self.write_limits(LANE_HEAVY_SLOTS=1, LANE_HEAVY_MEM_GB=0, LANE_HEAVY_POLL_SECS=0.05)
        step, after = self.dir / "step.pid", self.dir / "after"
        inner = f"echo \\$\\$ > {step}.tmp; mv {step}.tmp {step}; exec sleep 60"
        p = self.start("sh", "-c", f'sh -c "{inner}"; touch {after}')
        self.wait_for(step)
        pid = int(step.read_text())
        self.addCleanup(lambda: self.is_running(pid) and os.kill(pid, signal.SIGKILL))
        p.terminate()
        p.communicate(timeout=TIMEOUT)
        self.assertEqual(p.returncode, 143)
        end = time.time() + TIMEOUT
        while self.is_running(pid):
            self.assertLess(time.time(), end, "a step of the job outlived the gate")
            time.sleep(0.02)
        time.sleep(0.1)
        self.assertFalse(after.exists(), "the job went on to its next step")

    @staticmethod
    def is_running(pid):
        try:
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
        except OSError:
            return False

    def test_daemon_left_behind_does_not_hold_the_slot(self):
        pidfile = self.dir / "daemon.pid"
        r = self.heavy("sh", "-c", f"sleep 60 >/dev/null 2>&1 </dev/null & echo $! > {pidfile}")
        self.assertEqual(r.returncode, 0)
        daemon = int(pidfile.read_text())
        self.addCleanup(lambda: os.kill(daemon, signal.SIGKILL) if Path(f"/proc/{daemon}").exists() else None)
        r = self.heavy("true")
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_nested_call_runs_inside_the_slot(self):
        r = self.heavy(str(HEAVY), "sh", "-c", "exit 4")
        self.assertEqual(r.returncode, 4)
        self.assertNotIn("waiting", r.stderr)

    def test_missing_command_takes_no_slot_and_exits_127(self):
        self.assertEqual(self.heavy("no-such-command-here").returncode, 127)
        self.assertFalse((self.dir / "state").exists())

    def test_unusable_state_dir_still_runs_and_says_so(self):
        blocked = self.dir / "a-file"
        blocked.touch()
        self.env["LANE_STATE_DIR"] = str(blocked / "state")
        r = self.heavy("sh", "-c", "exit 6")
        self.assertEqual(r.returncode, 6)
        self.assertIn("without the machine-wide limit", r.stderr)


class RoomTests(Machine):
    def room(self):
        return subprocess.run([str(ROOM)], env=self.env, capture_output=True, text=True, timeout=TIMEOUT)

    def agent(self, pid, role="lane", uid="u1"):
        d = self.proc / str(pid)
        d.mkdir()
        (d / "environ").write_bytes(
            f"HOME=/h\0COTAL_ROLE={role}\0COTAL_LIFECYCLE_UID={uid}\0COTAL_ACCEPTED_TOKEN=do-not-print\0".encode())

    def test_room_when_idle(self):
        r = self.room()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("40.0G available of 64G", r.stdout)
        self.assertIn("on 12 cores", r.stdout)
        self.assertIn("0 lane agents", r.stdout)

    def test_too_little_memory(self):
        self.set_proc(avail_gb=3, load5=2)
        r = self.room()
        self.assertEqual(r.returncode, 1)
        self.assertIn("TOO BUSY: memory 3.0G", r.stdout)

    def test_too_much_load(self):
        self.set_proc(avail_gb=40, load5=23)
        r = self.room()
        self.assertEqual(r.returncode, 1)
        self.assertIn("load 23.00", r.stdout)

    def test_agents_are_counted_once_and_only_lane_work(self):
        for pid, uid in ((1, "a"), (2, "a"), (3, "b")):
            self.agent(pid, uid=uid)
        self.agent(4, role="reviewer", uid="c")
        self.agent(5, role="coordinator", uid="d")
        (self.proc / "6").mkdir()   # a process that died, or belongs to someone else
        r = self.room()
        self.assertIn("3 lane agents", r.stdout)
        self.assertNotIn("do-not-print", r.stdout + r.stderr)

    def test_too_many_agents(self):
        self.write_limits(LANE_ROOM_MAX_AGENTS=2)
        self.agent(1, uid="a")
        self.agent(2, uid="b")
        r = self.room()
        self.assertEqual(r.returncode, 1)
        self.assertIn("2 lane agents", r.stdout.splitlines()[-1])

    def test_unreadable_machine_state_holds_the_lane(self):
        (self.proc / "loadavg").unlink()
        self.assertEqual(self.room().returncode, 2)

    def test_room_is_looser_than_the_gate(self):
        out = self.limits_seen()
        self.assertGreater(out["LANE_ROOM_MAX_AGENTS"], out["LANE_HEAVY_SLOTS"])


class LimitsTests(Machine):
    def test_defaults_follow_the_machine_when_the_file_is_absent(self):
        self.limits.unlink()
        self.assertEqual(self.limits_seen(12, 64), {"LANE_HEAVY_SLOTS": 2, "LANE_HEAVY_MEM_GB": 24,
                                                    "LANE_ROOM_MIN_MEM_GB": 8, "LANE_ROOM_MAX_LOAD": 18,
                                                    "LANE_ROOM_MAX_AGENTS": 12})
        small = self.limits_seen(2, 4)
        self.assertEqual(small["LANE_HEAVY_SLOTS"], 1)
        self.assertGreaterEqual(small["LANE_HEAVY_MEM_GB"], 1)
        self.assertEqual(small["LANE_ROOM_MAX_AGENTS"], 2)

    def test_memory_can_be_what_limits_the_slots(self):
        self.limits.unlink()
        self.assertEqual(self.limits_seen(48, 16)["LANE_HEAVY_SLOTS"], 1)

    def test_the_file_overrides_and_can_follow_the_machine(self):
        self.write_limits(LANE_HEAVY_SLOTS="$((LANE_CORES / 3))", LANE_ROOM_MAX_LOAD=30)
        seen = self.limits_seen(12, 64)
        self.assertEqual((seen["LANE_HEAVY_SLOTS"], seen["LANE_ROOM_MAX_LOAD"]), (4, 30))

    def test_a_bad_value_falls_back_and_says_so(self):
        for bad in ("many", "0"):
            self.write_limits(LANE_HEAVY_SLOTS=bad)
            self.assertEqual(self.limits_seen(12, 64)["LANE_HEAVY_SLOTS"], 2)
            self.assertIn("LANE_HEAVY_SLOTS", self.stderr)


    def test_leading_zeros_are_plain_decimals(self):
        self.write_limits(LANE_HEAVY_SLOTS="08", LANE_ROOM_MIN_MEM_GB="09")
        seen = self.limits_seen(12, 64)
        self.assertEqual((seen["LANE_HEAVY_SLOTS"], seen["LANE_ROOM_MIN_MEM_GB"], self.stderr), (8, 9, ""))
        r = self.heavy("sh", "-c", "exit 9")
        self.assertEqual((r.returncode, r.stderr.count("value too great")), (9, 0))

    def test_a_broken_file_does_not_stop_the_job(self):
        for line in ("LANE_HEAVY_SLOTS=$NOPE", "LANE_HEAVY_SLOTS=$((1 / 0))", "if then"):
            self.limits.write_text(line + "\n")
            r = self.heavy("sh", "-c", "exit 9")
            self.assertEqual(r.returncode, 9, f"{line}: {r.stderr}")


class ContentTests(unittest.TestCase):
    def test_every_setting_is_documented(self):
        names = set(re.findall(r"^(LANE_(?:HEAVY|ROOM)_[A-Z_]+)=", LIMITS.read_text(), re.M))
        self.assertEqual(len(names), 6)
        example = EXAMPLE.read_text()
        for name in names:
            self.assertIn(f"# {name}=", example)

    def test_the_contract_is_in_the_header(self):
        for tool, needles in ((HEAVY, ("Exit:", "slot", "OUT OF MEMORY", "stderr")), (ROOM, ("Exit:", "lane agents"))):
            header = tool.read_text().split("\nset -u")[0]
            for needle in needles:
                self.assertIn(needle, header)

    def test_machine_md_has_the_machine_wide_rule(self):
        text = (ROOT / "machine.md").read_text()
        self.assertNotIn("one heavy build or run at a time per project", text)
        self.assertIn("## Machine caps", text)
        self.assertIn("~/.agents/bin/lane-heavy", text)
        self.assertIn("~/.agents/bin/lane-room", text)

    def test_the_brief_sends_heavy_work_through_the_gate(self):
        self.assertIn("lane-heavy COMMAND", (ROOT / "templates" / "lane-brief.md").read_text())

    def test_the_rove_brief_sends_heavy_work_through_the_gate(self):
        text = (ROOT / "templates" / "worker-brief.md").read_text()
        self.assertIn("~/.agents/bin/lane-heavy COMMAND", text)
        self.assertNotIn("one at a time", text)

    def test_the_header_states_what_a_killed_gate_leaves_behind(self):
        header = HEAVY.read_text().split("\nset -u")[0]
        self.assertIn("--pdeathsig", header)
        self.assertIn("outside the limit", header)

    def test_the_readme_lists_the_tools(self):
        text = (ROOT / "README.md").read_text()
        for tool in ("lane-heavy", "lane-room", "lane-limits.sh", "limits.example.sh"):
            self.assertIn(f"`{tool}`", text)

    def test_config_comments_sit_on_their_own_settings(self):
        lines = (ROOT / "config.example.sh").read_text().splitlines()
        self.assertTrue(any(l.startswith("NIGHT_MEM_MIN_GB=8") and "available memory" in l for l in lines))
        self.assertFalse(any(l.startswith("WINDOW_STOP_PCT") and "available memory" in l for l in lines))


class SandboxGateTests(Machine):
    """The sandbox's gate block on its own, with the bind helpers stubbed: no real sandbox, no real load."""

    def gate_block(self):
        text = SANDBOX.read_text()
        self.assertIn(MARK, text)
        return text.split(MARK)[1].split("# the provider and the model")[0]

    def run_block(self):
        lane = self.dir / "lane"
        lane.mkdir(exist_ok=True)
        script = "set -euo pipefail\n" + "".join(f'{f}() {{ echo "{f} $*"; }}\n' for f in ("ro", "rw", "setenv"))
        script += f"lane={lane}\n# " + self.gate_block()
        r = subprocess.run(["bash", "-c", script], env=dict(self.env, HOME=str(self.dir)), capture_output=True,
                           text=True, timeout=TIMEOUT)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r, lane

    def install(self):
        tools = self.dir / ".agents" / "bin"
        tools.mkdir(parents=True, exist_ok=True)
        for src in (HEAVY, ROOM, LIMITS):
            shutil.copy(src, tools / src.name)
        return tools

    def test_the_lane_gets_the_tools_and_the_real_slot_directory(self):
        tools = self.install()
        out = self.run_block()[0].stdout.splitlines()
        state = self.env["LANE_STATE_DIR"]
        self.assertIn(f"rw {state}", out)
        self.assertIn(f"setenv LANE_STATE_DIR {state}", out)   # the lane must not work out a path of its own
        for name in ("lane-heavy", "lane-room", "lane-limits.sh"):
            self.assertIn(f"ro {tools / name}", out)
        self.assertEqual(os.stat(state).st_mode & 0o777, 0o700)

    def test_the_lane_gets_the_machines_limits_not_its_own_defaults(self):
        self.install()
        self.write_limits(LANE_HEAVY_SLOTS=3, LANE_HEAVY_MEM_GB=7)
        r, lane = self.run_block()
        self.assertIn(f"ro {lane / 'limits.sh'} /run/sandbox/limits.sh", r.stdout.splitlines())
        self.assertIn("setenv LANE_LIMITS_FILE /run/sandbox/limits.sh", r.stdout.splitlines())
        written = (lane / "limits.sh").read_text()
        self.assertIn("LANE_HEAVY_SLOTS=3\n", written)
        self.assertIn("LANE_HEAVY_MEM_GB=7\n", written)

    def test_noisy_or_broken_owner_limits_do_not_stop_the_launch(self):
        self.install()
        self.limits.write_text("echo hello\nno-such-command\nLANE_HEAVY_SLOTS=2\n")
        r, lane = self.run_block()
        self.assertIn(f"rw {self.env['LANE_STATE_DIR']}", r.stdout.splitlines())
        self.assertIn("LANE_HEAVY_SLOTS=2\n", (lane / "limits.sh").read_text())

    def test_without_the_tools_it_binds_nothing_and_says_so(self):
        r, _ = self.run_block()
        self.assertEqual(r.stdout, "")
        self.assertIn("no machine-wide heavy-work gate", r.stderr)

    def test_a_slot_directory_it_cannot_trust_binds_nothing(self):
        self.install()
        real = self.dir / "elsewhere"
        real.mkdir()
        os.symlink(real, self.env["LANE_STATE_DIR"])
        r, _ = self.run_block()
        self.assertEqual(r.stdout, "")
        self.assertIn("no machine-wide heavy-work gate", r.stderr)

    def test_the_helpers_exist_where_the_block_runs(self):
        text = SANDBOX.read_text().split(MARK)[0]
        for helper in ("ro()", "rw()", "setenv()"):
            self.assertIn(helper, text)


if __name__ == "__main__":
    unittest.main()
