"""SBX-2 task 4 (lc-35qn): the launcher, core/scripts/lane-sandbox-shim, run as a real process with fake
systemd-run, pasta, bwrap, nsenter and agent (tests/stubs/fake_sandbox.py) that record argv and environment, and the
real broker. Review Focus 1 (no info, no or two host pids: nothing starts) and 5 (no scope: said, never silent)."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import sandbox_fixture as FX

ROOT = Path(__file__).resolve().parent.parent
SHIM = ROOT / "core" / "scripts" / "lane-sandbox-shim"
FAKE = ROOT / "tests" / "stubs" / "fake_sandbox.py"
SB = FX.SB
KEY = "fake-gateway-key-" + "Q7" * 12
HOOK = "fake-hook-token-" + "R8" * 12
LAUNCH = "fake-launch-token-" + "S9" * 12
OWNER = "owner-only-" + "T1" * 12
MNTNS, NSPID, HOSTPID = 4026599999, 2, 4242


class ShimCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.t = Path(tmp.name).resolve()
        self.bin = self.t / "fakebin"
        self.bin.mkdir()
        wrapper = self.bin / "fake_sandbox.py"
        wrapper.write_text(f"#!{sys.executable}\n" + FAKE.read_text())
        wrapper.chmod(0o755)
        for n in ("systemd-run", "pasta", "bwrap", "nsenter", "nft", "opencode2", "opencode"):
            (self.bin / n).symlink_to(wrapper)
        self.log = self.t / "calls.jsonl"
        self.proc = self.t / "proc"
        self.cfg = {"log": str(self.log), "proc": str(self.proc), "mntns": MNTNS, "nspid": NSPID,
                    "host_pid": HOSTPID, "agent_rc": 7}
        self.fake_proc({HOSTPID: (MNTNS, [HOSTPID, NSPID, 1])})
        self.write_cfg()
        pins = {"SANDBOX_BWRAP": str(self.bin / "bwrap"), "SANDBOX_PASTA": str(self.bin / "pasta"),
                "SANDBOX_NSENTER": str(self.bin / "nsenter"), "SANDBOX_NFT": str(self.bin / "nft")}
        self.fx = FX.Fixture(self.t / "m", conf=pins)
        (self.fx.home / ".config" / "lane-control" / "machine.conf").write_text(
            f"SANDBOX_PROJECTS=app\nSANDBOX_REAL_OPENCODE2={self.bin / 'opencode2'}\n"
            f"SANDBOX_REAL_OPENCODE={self.bin / 'opencode'}\nSANDBOX_AGENT_DIRS={self.bin}\n")
        p = mock.patch.dict(os.environ, self.fx.env, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def write_cfg(self, **over):
        self.cfg.update(over)
        (self.bin / "fake.json").write_text(json.dumps(self.cfg))

    def fake_proc(self, entries):
        import shutil
        shutil.rmtree(self.proc, ignore_errors=True)
        for pid, (mnt, nspid) in entries.items():
            p = self.proc / str(pid)
            (p / "ns").mkdir(parents=True)
            (p / "task" / str(pid)).mkdir(parents=True)
            os.symlink(f"mnt:[{mnt}]", p / "ns" / "mnt")
            (p / "status").write_text(f"Name:\tbwrap\nNSpid:\t{' '.join(map(str, nspid))}\n")

    def env(self, bus=True, **over):
        e = {**self.fx.env, "PATH": f"{self.bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
             "ORCA_TERMINAL_HANDLE": "term_lane", "ORCA_AGENT_HOOK_PORT": "4411", "ORCA_AGENT_HOOK_TOKEN": HOOK,
             "ORCA_AGENT_LAUNCH_TOKEN": LAUNCH, "ORCA_PANE_KEY": "pane-1", "FAKE_GATEWAY_KEY": KEY,
             "FAKE_OWNER_TOKEN": OWNER, "LANE_SANDBOX_PROC": str(self.proc), "TERM": "xterm"}
        if bus:
            e.update({"DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent/bus", "XDG_RUNTIME_DIR": str(self.t)})
        e.update(over)
        return {k: v for k, v in e.items() if v is not None}

    def shim(self, *args, cwd=None, name=None, env=None, wait=True):
        argv = [str(SHIM), *args] if name is None else [str(self.bin / name), *args]
        if name is not None:  # a PATH link to the shim named like the agent
            link = self.t / "pathshim" / name
            link.parent.mkdir(exist_ok=True)
            if not link.exists():
                link.symlink_to(SHIM)
            argv = [str(link), *args]
        p = subprocess.Popen(argv, cwd=cwd or self.fx.wt, env=env or self.env(), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
        if not wait:
            return p
        out, err = p.communicate(timeout=60)
        return p.returncode, out, err

    def calls(self, tool=None):
        if not self.log.exists():
            return []
        rows = [json.loads(x) for x in self.log.read_text().splitlines()]
        return [r for r in rows if tool is None or r["tool"] == tool]

    def agent_started(self):
        return any(r.get("event") == "agent-start" for r in self.calls())

    def record(self):
        f = SB.run_record("term_lane")
        return json.loads(f.read_text()) if f.exists() else None


class PassthroughTests(ShimCase):
    def test_passthrough_cases(self):
        code, out, err = self.shim("opencode2", "--standalone", cwd=self.fx.repo)  # the primary checkout
        self.assertEqual(code, 7, err)
        self.assertEqual([r["tool"] for r in self.calls()], ["opencode2"])
        self.assertEqual(self.calls()[0]["argv"], ["--standalone"])
        self.fx.prepare()
        self.log.unlink()
        for args in (["--version"], ["-v"], ["models"], ["--help"], ["-h"]):
            code, out, err = self.shim("opencode2", *args)
            self.assertEqual(code, 7, err)
        self.assertEqual({r["tool"] for r in self.calls()}, {"opencode2"})
        self.assertIsNone(self.record())
        other = self.t / "owner-tab"
        other.mkdir()
        code, _, err = self.shim("opencode", cwd=other)  # an owner's tab in a folder no lane is bound to
        self.assertEqual(code, 7, err)

    def test_usage(self):
        code, out, err = self.shim("claude")
        self.assertEqual(code, 2)
        self.assertEqual(self.calls(), [])


class RefusalTests(ShimCase):
    def test_no_ticket_refused(self):
        self.fx.prepare()
        self.fx.take("term_other")  # the only ticket is taken
        code, out, err = self.shim("opencode2", "--standalone")
        self.assertEqual(code, 2)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertIn("no ticket", err)
        self.assertFalse(self.agent_started())

    def test_no_terminal_refused(self):
        self.fx.prepare()
        code, out, err = self.shim("opencode2", env=self.env(ORCA_TERMINAL_HANDLE=None))
        self.assertEqual(code, 2)
        self.assertIn("terminal", err)
        self.assertFalse(self.agent_started())

    def test_path_detector_refuses(self):
        self.fx.prepare()
        code, out, err = self.shim("--standalone", name="opencode2")
        self.assertEqual(code, 2)
        self.assertIn("custom command", err)
        self.assertFalse(self.agent_started())
        self.assertEqual(len(list((SB.tickets_dir(self.fx.wt) / "taken").glob("*.json")))
                         if (SB.tickets_dir(self.fx.wt) / "taken").is_dir() else 0, 0)  # the ticket stays unused

    def test_record_after_filter(self):
        self.fx.prepare()
        self.write_cfg(fail_nsenter=True)
        code, out, err = self.shim("opencode2", "--standalone")
        self.assertEqual(code, 2)
        self.assertIn("network filter", err)
        self.assertIsNone(self.record())
        self.assertFalse(self.agent_started())
        self.assertFalse(any(r.get("event") == "released" for r in self.calls()))

    def test_info_never_written(self):
        self.fx.prepare()
        self.write_cfg(no_info=True)
        code, out, err = self.shim("opencode2", "--standalone")
        self.assertEqual(code, 2)
        self.assertIn("before the agent could start", err)
        self.assertIsNone(self.record())
        self.assertFalse(self.agent_started())
        self.assertEqual(self.calls("nsenter"), [])

    def test_host_pid_ambiguous(self):
        self.fx.prepare()
        self.fake_proc({HOSTPID: (MNTNS, [HOSTPID, NSPID, 1]), HOSTPID + 1: (MNTNS, [HOSTPID + 1, NSPID, 1])})
        code, out, err = self.shim("opencode2", "--standalone")
        self.assertEqual(code, 2)
        self.assertIn("2 times", err)
        self.assertIsNone(self.record())
        self.assertFalse(self.agent_started())
        self.fake_proc({})
        self.fx.prepare()
        code, out, err = self.shim("opencode2", "--standalone")
        self.assertIn("0 times", err)
        self.assertFalse(self.agent_started())

    def test_agent_outside_store_refused(self):
        (self.fx.home / ".config" / "lane-control" / "machine.conf").write_text(
            f"SANDBOX_PROJECTS=app\nSANDBOX_REAL_OPENCODE2={self.bin / 'opencode2'}\n")
        self.fx.prepare()
        code, out, err = self.shim("opencode2")
        self.assertEqual(code, 2)
        self.assertIn("SANDBOX_AGENT_DIRS", err)


class LaunchTests(ShimCase):
    def test_launch_and_record(self):
        nonce = self.fx.prepare()
        code, out, err = self.shim("opencode2", "--standalone")
        self.assertEqual(code, 7, err)  # the agent's status
        tools = [r["tool"] for r in self.calls()]
        self.assertEqual(tools[:1], ["systemd-run"])  # the probe
        self.assertEqual(tools[1:5], ["systemd-run", "pasta", "bwrap", "nsenter"])
        self.assertIn("opencode2", tools)
        rec = self.record()
        self.assertEqual((rec["nonce"], rec["host_pid"], rec["mntns"], rec["role"]), (nonce, HOSTPID, MNTNS, "lane"))
        self.assertEqual(rec["status"], 7)
        nsenter = self.calls("nsenter")[0]["argv"]
        self.assertEqual(nsenter[:6], ["--preserve-credentials", "-t", str(HOSTPID), "-U", "-n",
                                       str(self.bin / "nft")])
        agent = next(r for r in self.calls() if r.get("event") == "agent-start")
        self.assertEqual(agent["argv"], ["--standalone"])
        self.assertEqual(agent["env"]["FAKE_GATEWAY_KEY"], KEY)
        self.assertEqual(agent["env"]["ORCA_AGENT_HOOK_TOKEN"], HOOK)
        self.assertNotIn("FAKE_OWNER_TOKEN", agent["env"])
        bw = self.calls("bwrap")[0]["argv"]
        self.assertEqual(bw[:2], ["--cap-drop", "ALL"])
        self.assertIn(str(self.fx.clone()), bw)
        self.assertTrue((Path(self.fx.clone()).parent / f"t-{nonce}" / "broker" / "stderr.log").exists())

    def test_no_scope_without_bus(self):
        self.fx.prepare()
        code, out, err = self.shim("opencode2", env=self.env(bus=False))
        self.assertEqual(code, 7, err)
        self.assertIn("no memory cap", err)
        self.assertEqual(self.calls("systemd-run"), [])
        self.assertFalse(self.record()["scope"])
        pasta = self.calls("pasta")[0]
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", pasta["env"])

    def test_scope_refused_is_said(self):
        self.fx.prepare()
        self.write_cfg(fail_scope=True)
        code, out, err = self.shim("opencode2")
        self.assertEqual(code, 7, err)
        self.assertIn("no memory cap", err)
        self.assertEqual(len(self.calls("systemd-run")), 1)  # the probe only
        self.assertEqual(self.calls()[1]["tool"], "pasta")

    def test_bus_vars_reach_only_systemd_run(self):
        self.fx.prepare()
        self.shim("opencode2")
        sr = [r for r in self.calls("systemd-run") if "true" not in r["argv"]][0]
        self.assertIn("DBUS_SESSION_BUS_ADDRESS", sr["env"])
        agent = next(r for r in self.calls() if r.get("event") == "agent-start")
        # the fake bwrap does not apply --unsetenv; the real one does (test_sandbox_escapes checks it inside)
        bw = self.calls("bwrap")[0]["argv"]
        self.assertEqual(bw[bw.index("DBUS_SESSION_BUS_ADDRESS") - 1], "--unsetenv")
        self.assertEqual(bw[bw.index("XDG_RUNTIME_DIR") - 1], "--unsetenv")
        del agent

    def test_no_secret_in_any_argv(self):
        self.fx.prepare()
        self.shim("opencode2", "--standalone")
        rows = self.calls()
        self.assertGreater(len(rows), 4)
        for r in rows:
            joined = "\0".join(r["argv"])
            for s in (KEY, HOOK, LAUNCH, OWNER):
                self.assertNotIn(s, joined, r["tool"])
        for r in rows:
            if r["tool"] in ("pasta", "bwrap"):
                self.assertNotIn("FAKE_OWNER_TOKEN", r["env"])
                self.assertNotIn("GITHUB_TOKEN", r["env"])

    def test_signals_forwarded(self):
        self.fx.prepare()
        self.write_cfg(agent_wait=30)
        p = self.shim("opencode2", wait=False)
        ready = Path(str(self.log) + ".agent-ready")
        for _ in range(300):
            if ready.exists():
                break
            time.sleep(0.05)
        self.assertTrue(ready.exists(), p.stderr.read() if p.poll() is not None else "agent not started")
        p.send_signal(signal.SIGWINCH)
        time.sleep(0.3)
        p.send_signal(signal.SIGTERM)
        out, err = p.communicate(timeout=30)
        events = [r.get("event") for r in self.calls()]
        self.assertIn("signal SIGWINCH", events)
        self.assertIn("signal SIGTERM", events)
        self.assertEqual(p.returncode, 7, err)


if __name__ == "__main__":
    unittest.main()
