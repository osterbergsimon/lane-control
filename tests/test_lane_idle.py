"""core/scripts/lane-idle: how a coordinator asks, or waits, for another session's idle time (UP-6). Claude
transcripts in temp folders; HOME and XDG point into the temp dir, so no real session state is read."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "core" / "scripts" / "lane-idle"
sys.path.insert(0, str(ROOT / "core"))
import harnesses  # noqa: E402


class LaneIdleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.home = self.t / "home"
        self.home.mkdir()
        self.checkout = self.t / "shop.app"
        self.checkout.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("XDG_", "LANE_", "CLAUDE_"))}
        self.env["HOME"] = str(self.home)
        saved = {k: os.environ.get(k) for k in ("HOME", "XDG_STATE_HOME", "CLAUDE_CONFIG_DIR")}
        os.environ["HOME"] = str(self.home)
        os.environ.pop("XDG_STATE_HOME", None)
        os.environ.pop("CLAUDE_CONFIG_DIR", None)

        def restore():
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.addCleanup(restore)

    def session(self, sid, age_s, role="coordinator", harness="claude"):
        rec = harnesses.session_record(self.checkout, role, harness)
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text(sid + "\n")
        if harness == "claude":
            f = self.home / ".claude" / "projects" / harnesses.encode_path(self.checkout.resolve()) / f"{sid}.jsonl"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("{}\n")
            os.utime(f, (time.time() - age_s, time.time() - age_s))

    def run_idle(self, *args, timeout=30):
        return subprocess.run([sys.executable, str(SCRIPT), "--checkout", str(self.checkout), *args],
                              capture_output=True, text=True, env=self.env, timeout=timeout)

    def test_prints_the_minutes_and_nothing_else(self):
        self.session("s1", 12 * 60 + 5)
        r = self.run_idle()
        self.assertEqual((r.returncode, r.stdout.strip(), r.stderr), (0, "12", ""))

    def test_no_marked_session_is_exit_1_and_says_why(self):
        r = self.run_idle()
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertIn("LANE_ROLE", r.stderr)
        self.assertIn("coordinator", r.stderr)

    def test_the_role_is_chosen_with_role(self):
        self.session("s1", 3 * 60 + 5)
        self.session("d1", 40 * 60 + 5, role="design-lead")
        self.assertEqual(self.run_idle("--role", "design-lead").stdout.strip(), "40")
        self.assertEqual(self.run_idle().stdout.strip(), "3")

    def test_wait_returns_at_once_when_already_idle_enough(self):
        self.session("s1", 12 * 60 + 5)
        r = self.run_idle("--wait", "10", "--timeout", "5")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "12"))

    def test_wait_times_out_with_exit_3_while_the_session_is_busy(self):
        self.session("s1", 30)
        start = time.time()
        r = self.run_idle("--wait", "10", "--every", "1", "--timeout", "2")
        self.assertEqual((r.returncode, r.stdout.strip()), (3, ""))
        self.assertIn("idle 0 min", r.stderr)  # says how idle it was last
        self.assertLess(time.time() - start, 20)

    def test_wait_sees_the_session_go_idle(self):
        # the record and the transcript are read again on every look: a session that was busy and is now quiet
        self.session("s1", 30)
        f = next((self.home / ".claude").rglob("s1.jsonl"))
        p = subprocess.Popen([sys.executable, str(SCRIPT), "--checkout", str(self.checkout), "--wait", "1",
                              "--every", "1", "--timeout", "20"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env=self.env)
        time.sleep(2)
        os.utime(f, (time.time() - 125, time.time() - 125))
        out, err = p.communicate(timeout=30)
        self.assertEqual((p.returncode, out.strip()), (0, "2"), err)

    def test_one_harness_is_found_alone_two_must_be_named(self):
        self.session("c1", 7 * 60 + 5)
        self.assertEqual(self.run_idle().stdout.strip(), "7")
        self.session("o1", 1, harness="opencode")
        r = self.run_idle()
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertIn("--harness", r.stderr)
        self.assertEqual(self.run_idle("--harness", "claude").stdout.strip(), "7")

    @unittest.skipIf(os.geteuid() == 0, "root reads any folder")
    def test_an_unreadable_store_is_exit_2_not_no_session(self):
        self.session("s1", 60)
        d = next((self.home / ".claude" / "projects").iterdir())
        d.chmod(0)
        self.addCleanup(d.chmod, 0o700)
        r = self.run_idle()
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertIn("cannot read", r.stderr)

    def test_every_and_timeout_must_be_finite_and_sane(self):
        self.session("s1", 30)
        for args in (("--wait", "1", "--every", "nan"), ("--wait", "1", "--every", "inf"),
                     ("--wait", "1", "--every", "0.5"), ("--wait", "1", "--every", "1e-300"),
                     ("--wait", "1", "--every", "100000"), ("--wait", "1", "--timeout", "nan"),
                     ("--wait", "1", "--timeout", "inf"), ("--wait", "1", "--timeout", "0"),
                     ("--wait", "1", "--timeout", "-3"), ("--wait", str(10 ** 9))):
            r = self.run_idle(*args, timeout=10)
            self.assertEqual((r.returncode, r.stdout), (2, ""), args)
            self.assertNotIn("Traceback", r.stderr, args)

    def test_an_undecodable_record_is_no_session_not_a_crash(self):
        rec = harnesses.session_record(self.checkout, "coordinator", "claude")
        rec.parent.mkdir(parents=True)
        rec.write_bytes(b"\xff\xfe\x00\n")
        r = self.run_idle()
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertNotIn("Traceback", r.stderr)

    def test_an_unexpected_error_is_exit_2_never_exit_1(self):
        # exit 1 means "no marked session"; a crash must not read as that
        import contextlib
        import importlib.machinery
        import importlib.util
        import io
        loader = importlib.machinery.SourceFileLoader("lane_idle", str(SCRIPT))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("lane_idle", loader))
        loader.exec_module(mod)

        def boom(*a, **k):
            raise RuntimeError("a bug")
        mod.pick = boom
        err = io.StringIO()
        old = sys.argv
        sys.argv = ["lane-idle", "--checkout", str(self.checkout)]
        try:
            with contextlib.redirect_stderr(err):
                code = mod.cli()
        finally:
            sys.argv = old
        self.assertEqual(code, 2)
        self.assertIn("unexpected", err.getvalue())
        self.assertIn("a bug", err.getvalue())

    def test_refuses_nonsense(self):
        for args in (("--wait", "-1"), ("--wait", "x"), ("--wait", "1.5"), ("--role", "../x"), ("--harness", "pi")):
            r = self.run_idle(*args)
            self.assertEqual(r.returncode, 2, args)
            self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
