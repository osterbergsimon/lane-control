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
                    "host_pid": HOSTPID, "agent_rc": 7, "host_path": os.environ.get("PATH", "")}
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


class FailClosedTests(ShimCase):
    """r1 B3: with the custom command set, a worktree of a sandboxed project never starts unsandboxed: not after a
    refused prepare, not without one, not for a reviewer worktree never prepared, not with an unreadable config."""

    def refused_here(self, cwd=None, says=""):
        code, out, err = self.shim("opencode2", "--standalone", cwd=cwd)
        self.assertEqual(code, 2, err)
        self.assertIn(says, err)
        self.assertFalse(self.agent_started())
        self.assertEqual(self.calls("opencode2"), [])

    def test_no_prepare(self):
        self.refused_here(says="has no ticket")

    def test_after_prepare_refused_for_ci(self):
        with self.assertRaises(SB.SandboxError):
            self.fx.prepare(ci={"workflow": {"default_workflow_permissions": "write"}})
        self.refused_here(says="no ticket")

    def test_after_prepare_refused_for_a_stale_selftest(self):
        f = SB.selftest_file()
        f.write_text(json.dumps({**json.loads(f.read_text()), "lane_control": "0" * 40}))
        with self.assertRaises(SB.SandboxError):
            self.fx.prepare()
        self.refused_here(says="no ticket")

    def test_reviewer_worktree_never_prepared(self):
        rw = self.t / "m" / "orca" / "workspaces" / "app" / "review-1"
        FX.git("worktree", "add", "-q", "-b", "review-1", rw, "main", cwd=self.fx.repo)
        self.refused_here(cwd=rw, says="worktree of a sandboxed project")
        sub = rw / "deep"
        sub.mkdir()
        self.refused_here(cwd=sub, says="worktree of a sandboxed project")

    def test_unreadable_config(self):
        (self.fx.home / ".config" / "lane-control" / "machine.conf").write_text("SANDBOX_PROJECTS=app\nfalse\n")
        self.refused_here(says="machine.conf cannot be read")
        code, _, err = self.shim("opencode2", cwd=self.fx.repo)  # the primary checkout: the owner's
        self.assertEqual(code, 7, err)

    def test_other_projects_pass(self):
        other = self.t / "other"
        other.mkdir()
        FX.git("init", "-q", "-b", "main", cwd=other)
        (other / "f").write_text("x\n")
        FX.git("add", "f", cwd=other)
        FX.git("commit", "-q", "-m", "f", cwd=other)
        ow = self.t / "other-wt"
        FX.git("worktree", "add", "-q", "-b", "x", ow, cwd=other)
        code, _, err = self.shim("opencode2", cwd=ow)
        self.assertEqual(code, 7, err)  # not a sandboxed project: the owner's own tab


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


LS_PATH = ROOT / "core" / "scripts" / "lane-sandbox"
FAKE_ORCA = """#!{py}
import json, sys
a = sys.argv[1:]
open({log!r}, "a").write(json.dumps(a) + "\\n")
if "worker-show" in a:
    print(json.dumps({{"ok": True, "result": {{"terminal": {term!r}}}}}))
else:
    print(json.dumps({{"ok": True, "result": {{}}}}))
"""
FAKE_GH = """#!/bin/sh
echo '{{"state": "{state}"}}'
"""


class LifecycleCase(ShimCase):
    def setUp(self):
        super().setUp()
        self.orca_log = self.t / "orca.jsonl"
        (self.bin / "orca-fake").write_text(FAKE_ORCA.format(py=sys.executable, log=str(self.orca_log),
                                                             term="term_lane"))
        (self.bin / "orca-fake").chmod(0o755)
        self.gh("OPEN")

    def gh(self, state):
        (self.bin / "gh").unlink(missing_ok=True)
        (self.bin / "gh").write_text(FAKE_GH.format(state=state))
        (self.bin / "gh").chmod(0o755)

    def ls(self, *args, env=None, timeout=120):
        e = env or self.env(ORCA_CLI=str(self.bin / "orca-fake"))
        return subprocess.run([sys.executable, str(LS_PATH), *args], env=e, capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL, cwd=self.t)

    def orca_calls(self):
        return [json.loads(x) for x in self.orca_log.read_text().splitlines()] if self.orca_log.exists() else []

    def live_record(self, nonce, **over):
        """A run record as the launcher writes it, for a process that is alive in the fake /proc."""
        rec = {"nonce": nonce, "role": "lane", "lane": "app-1", "worktree": str(self.fx.wt.resolve()),
               "terminal": "term_lane", "host_pid": HOSTPID, "mntns": MNTNS, "started": time.time(), **over}
        SB.write_private(SB.run_record(rec["terminal"]), json.dumps(rec))


class StartedTests(LifecycleCase):
    def test_lane_started_ok(self):
        nonce = self.fx.prepare()
        self.live_record(nonce)
        r = self.ls("started", str(self.fx.wt), "--nonce", nonce, "--dispatch", "ctx_abcdef1", "--wait", "0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("started: sandboxed", r.stdout)
        self.assertFalse(any("worker-stop" in c for c in self.orca_calls()))

    def test_lane_started_after_a_real_launch(self):
        nonce = self.fx.prepare()
        self.write_cfg(agent_wait=30)
        p = self.shim("opencode2", wait=False)
        self.addCleanup(lambda: (p.kill(), p.communicate()))
        ready = Path(str(self.log) + ".agent-ready")
        for _ in range(300):
            if ready.exists():
                break
            time.sleep(0.05)
        r = self.ls("started", str(self.fx.wt), "--nonce", nonce, "--dispatch", "ctx_abcdef1", "--wait", "5")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def stops(self, nonce, why):
        r = self.ls("started", str(self.fx.wt), "--nonce", nonce, "--dispatch", "ctx_abcdef1", "--wait", "0")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("NOT sandboxed", r.stdout)
        self.assertIn(why, r.stdout)
        self.assertIn(["orchestration", "worker-stop", "--dispatch", "ctx_abcdef1", "--json"], self.orca_calls())
        self.orca_log.unlink()

    def test_lane_started_stops_unsandboxed(self):
        nonce = self.fx.prepare()
        self.stops(nonce, "no run record")  # the agent started without the launcher
        self.live_record("0" * 32)
        self.stops(nonce, "no run record")  # a stale record of another launch
        self.live_record(nonce, host_pid=99999)
        self.stops(nonce, "not running")  # a dead pid
        self.live_record(nonce, mntns=1234)
        self.stops(nonce, "mount namespace")  # a pid reused by another process
        self.live_record(nonce, terminal="term_lane", ended=time.time())
        self.stops(nonce, "already ended")
        SB.run_record("term_lane").unlink()
        self.live_record(nonce, terminal="term_other")
        self.stops(nonce, "terminal is not the dispatch's")


class CloseCheckTests(LifecycleCase):
    def lane_with_push(self):
        self.fx.prepare()
        c = self.fx.clone()
        (c / "lane-file").write_text("work\n")
        FX.git("add", "lane-file", cwd=c)
        FX.git("commit", "-q", "-m", "lane", cwd=c)
        sha = FX.git("rev-parse", "HEAD", cwd=c)
        FX.git("fetch", "-q", c, f"{sha}:refs/lanes/app-1", cwd=self.fx.repo)  # as the broker leaves it
        return sha

    def test_close_needs_merged_pr_or_word(self):
        self.lane_with_push()
        r = self.ls("close", "app-1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not merged", r.stdout)
        r = self.ls("close", "app-1", "--done")  # the owner's word, and this is no person at a terminal
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("person", r.stdout + r.stderr)
        self.assertTrue(self.fx.clone().is_dir())

    def test_close_refuses_unpushed_or_dirty(self):
        self.lane_with_push()
        self.gh("MERGED")
        c = self.fx.clone()
        (c / "dirty").write_text("x\n")
        r = self.ls("close", "app-1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("1 dirty file(s)", r.stdout)
        (c / "dirty").unlink()
        (c / "more").write_text("x\n")
        FX.git("add", "more", cwd=c)
        FX.git("commit", "-q", "-m", "unpushed", cwd=c)
        r = self.ls("close", "app-1")
        self.assertIn("1 commit(s) not pushed", r.stdout)
        self.assertTrue(c.is_dir())
        FX.git("reset", "-q", "--hard", "HEAD~1", cwd=c)
        r = self.ls("close", "app-1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(SB.lane_dir("app-1", self.fx.repo).exists())
        self.assertFalse(SB.binding_file(self.fx.wt).exists())
        self.assertIn("never its close checks", r.stdout)
        bw = [x for x in self.calls("bwrap")][-1]["argv"]
        self.assertEqual(bw[bw.index(str(c)) + 1], str(c))  # the inspect ran sandboxed, in the clone

    def test_close_discard(self):
        self.lane_with_push()
        self.gh("MERGED")
        (self.fx.clone() / "dirty").write_text("x\n")
        sys.path.insert(0, str(ROOT / "core"))
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("lc_ls_close_t", str(LS_PATH))
        spec = importlib.util.spec_from_loader("lc_ls_close_t", loader)
        LS = importlib.util.module_from_spec(spec)
        loader.exec_module(LS)
        with mock.patch.object(LS, "person", lambda what: None), \
                mock.patch.dict(os.environ, {"PATH": f"{self.bin}:{os.environ.get('PATH', '')}"}):
            import contextlib
            import io
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = LS.main(["close", "app-1", "--discard"])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertFalse(SB.lane_dir("app-1", self.fx.repo).exists())
        self.assertEqual(self.calls("bwrap"), [])  # discarded: no inspect was needed

    def test_check_runs_in_fresh_clone(self):
        sha = self.lane_with_push()
        r = self.ls("check", "app-1", "--sha", sha, "--", "bash", "-c",
                    'git rev-parse HEAD >"$LANE_OUTBOX/head"; test -f lane-file; exit 3')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("in a fresh clone, sandboxed", r.stdout)
        checks = sorted(SB.lane_dir("app-1", self.fx.repo).glob("check-*/repo"))
        self.assertEqual(len(checks), 1)
        bw = self.calls("bwrap")[-1]["argv"]
        self.assertIn(str(checks[0]), bw)
        self.assertNotIn(str(self.fx.clone()), bw)
        heads = list(SB.lane_dir("app-1", self.fx.repo).glob("t-*/run/outbox/head"))
        self.assertEqual([h.read_text().strip() for h in heads], [sha])
        self.assertNotEqual(checks[0], self.fx.clone())

    def test_approve_ci_is_a_persons(self):
        r = self.ls("approve-ci", "app-1", "a" * 40)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("person", r.stdout + r.stderr)
        r = self.ls("approve-ci", "app-1", "a" * 40, env=self.env(LANE_SANDBOX="1"))
        self.assertIn("inside a sandbox", r.stderr)


if __name__ == "__main__":
    unittest.main()
