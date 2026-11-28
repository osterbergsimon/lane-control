"""SBX-2 task 7 (lc-35qn): the adversarial suite. Every attack of the SBX-1 spike and of reviews r1 and r2, run
through the real launcher (core/scripts/lane-sandbox-shim with a ticket from `prepare`), the real broker and the
inside wrappers, under real pasta and bwrap --cap-drop ALL, with a stub agent (tests/sandbox_escapes/stub-agent.sh,
never a real agent or gateway); each result is checked from outside.

Runs where bwrap and pasta can run: pins from LANE_SANDBOX_PINS (`lane-control sandbox selftest` passes them), else
built with `nix build` from nixpkgs as machine setup does. Without them it is skipped and says why (CI's runner
has no nix store); under LANE_SANDBOX_SELFTEST=1 (the selftest) that is a failure instead, as is any skip.
`test_dns_and_outbound` exists only with LANE_SANDBOX_NET=1 (it needs the internet)."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
import fcntl
import hashlib
import json
import os
import pty
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import sandbox_fixture as FX

ROOT = Path(__file__).resolve().parent.parent
SHIM = ROOT / "core" / "scripts" / "lane-sandbox-shim"
LANE_SANDBOX = ROOT / "core" / "scripts" / "lane-sandbox"
STUB = ROOT / "tests" / "sandbox_escapes" / "stub-agent.sh"
SB, SP = FX.SB, FX.SP
git = FX.git
PINS = {"SANDBOX_BWRAP": ("bubblewrap", "bin/bwrap"), "SANDBOX_PASTA": ("passt", "bin/pasta"),
        "SANDBOX_NSENTER": ("util-linux.bin", "bin/nsenter"), "SANDBOX_NFT": ("nftables", "bin/nft")}
SELFTEST = os.environ.get("LANE_SANDBOX_SELFTEST") == "1"
_PINS = None


def pins() -> tuple:
    """(pins, None) or (None, why not)."""
    global _PINS
    if _PINS is not None:
        return _PINS
    given = os.environ.get("LANE_SANDBOX_PINS")
    if given:
        p = json.loads(given)
    elif not shutil.which("nix"):
        _PINS = (None, "no nix here (bwrap, pasta, nsenter and nft are pinned from nixpkgs)")
        return _PINS
    else:
        p = {}
        for k, (pkg, rel) in PINS.items():
            r = subprocess.run(["nix", "build", "--no-link", "--print-out-paths", f"nixpkgs#{pkg}"],
                               capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL)
            if r.returncode != 0:
                _PINS = (None, f"nix build nixpkgs#{pkg} failed")
                return _PINS
            p[k] = f"{r.stdout.split()[0]}/{rel}"
    tools = FX.tool_dirs()
    if not all(t.startswith("/nix/store/") for t in tools) or tools == ["/nix/store/e-git/bin",
                                                                         "/nix/store/f-python3/bin"]:
        _PINS = (None, "git, bash and python3 are not in the nix store here")
        return _PINS
    p.setdefault("SANDBOX_TOOL_PATH", ":".join(tools))
    nsdir = str(Path(p["SANDBOX_NSENTER"]).parent)
    if nsdir not in p["SANDBOX_TOOL_PATH"].split(":"):
        p["SANDBOX_TOOL_PATH"] += ":" + nsdir  # mount, umount and unshare, for the attacks
    probe = subprocess.run([p["SANDBOX_PASTA"], "--config-net", "-q", "-f", "-t", "none", "-u", "none", "-U", "none",
                            "-T", "none", "--", p["SANDBOX_BWRAP"], "--cap-drop", "ALL", "--ro-bind", "/nix/store",
                            "/nix/store", "--proc", "/proc", "--dev", "/dev",
                            SP.find_tool(p["SANDBOX_TOOL_PATH"].split(":"), "bash") or "/bin/sh", "-c", "exit 0"],
                           capture_output=True, timeout=60, env={"PATH": "/nonexistent"})
    if probe.returncode != 0:
        _PINS = (None, "pasta and bwrap cannot make their namespaces here (unprivileged user namespaces)")
        return _PINS
    _PINS = (p, None)
    return _PINS


def need_sandbox(case):
    p, why = pins()
    if p is None:
        msg = f"the adversarial sandbox suite is skipped: {why}; it runs as lane-control sandbox selftest"
        if SELFTEST:
            raise AssertionError(msg + " (and under the selftest a skip is a failure)")
        raise unittest.SkipTest(msg)
    return p


class Listeners:
    """Host-side services the stub must not reach (and the one forwarded port it must)."""

    def __init__(self):
        def tcp(host):
            s = socket.socket()
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, 0))
            s.listen(16)
            return s
        self.open, self.closed, self.host = tcp("127.0.0.1"), tcp("127.0.0.1"), tcp("0.0.0.0")
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", 0))
        self.abstract = f"lane-sbx-{os.getpid()}-{time.time_ns()}"
        self.ab = socket.socket(socket.AF_UNIX)
        self.ab.bind("\0" + self.abstract)
        self.ab.listen(8)
        self.stop = threading.Event()
        for s in (self.open, self.closed, self.host, self.ab):
            threading.Thread(target=self.accept, args=(s,), daemon=True).start()
        threading.Thread(target=self.pong, daemon=True).start()

    def port(self, s):
        return s.getsockname()[1]

    def accept(self, s):
        s.settimeout(0.5)
        while not self.stop.is_set():
            try:
                c, _ = s.accept()
                c.close()
            except OSError:
                continue

    def pong(self):
        self.udp.settimeout(0.5)
        while not self.stop.is_set():
            try:
                _, peer = self.udp.recvfrom(100)
                self.udp.sendto(b"pong", peer)
            except OSError:
                continue

    def close(self):
        self.stop.set()
        for s in (self.open, self.closed, self.host, self.udp, self.ab):
            s.close()


FAKE_ORCA = """#!{py}
import json, sys
a = sys.argv[1:]
with open({log!r}, "a") as f:
    f.write(json.dumps(a) + "\\n")
if "dispatch-show" in a:
    task = next(x.split("=", 1)[1] for x in a if x.startswith("--task="))
    mine = task == "task_mine001"
    print(json.dumps({{"ok": True, "result": {{"dispatch": {{"id": "ctx_mine001" if mine else "ctx_other01",
        "assignee_handle": {term!r} if mine else "term_someone_else"}}}}}}))
elif "worker-show" in a:
    print(json.dumps({{"ok": True, "result": {{"terminal": {term!r}}}}}))
else:
    print(json.dumps({{"ok": True, "result": {{}}}}))
"""


def sha_of(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class Machine:
    """One scratch machine with a prepared lane, the fakes and the listeners; shared by a test class."""

    def __init__(self, p: dict, terminal="term_sbx"):
        self.tmp = tempfile.TemporaryDirectory(prefix="sbx")
        self.t = Path(self.tmp.name).resolve()
        self.mark_a, self.mark_b = "FAKE-FLOOR-", f"{os.getpid()}-{time.time_ns()}"
        self.fx = FX.Fixture(self.t / "m", mark=self.mark_a + self.mark_b, conf=p)
        self.term = terminal
        self.agent = self.t / "agent"
        self.agent.mkdir()
        shutil.copyfile(STUB, self.agent / "opencode2")
        (self.agent / "opencode2").chmod(0o755)
        self.fakebin = self.t / "fakebin"
        self.fakebin.mkdir()
        self.orca_log = self.t / "orca.log"
        (self.fakebin / "orca-ide").write_text(FAKE_ORCA.format(py=sys.executable, log=str(self.orca_log),
                                                                term=terminal))
        for n in ("gh", "bd"):
            (self.fakebin / n).write_text("#!/bin/sh\necho '{}'\n")
        for f in self.fakebin.iterdir():
            f.chmod(0o755)
        (self.fx.home / ".config" / "lane-control" / "machine.conf").write_text(
            f"SANDBOX_PROJECTS=app\nSANDBOX_REAL_OPENCODE2={self.agent / 'opencode2'}\n"
            f"SANDBOX_AGENT_DIRS={self.agent}\nSANDBOX_LANE_MEMORY=4G\n")
        self.net = Listeners()
        self.key = f"fake-gateway-key-{time.time_ns()}"
        self.out_marker = self.t / "ran-outside"
        # the base moves on after the lane's clone is made: the lane takes it with git pull
        self.patch = mock.patch.dict(os.environ, self.fx.env, clear=True)
        self.patch.start()

    def close(self):
        self.patch.stop()
        self.net.close()
        self.tmp.cleanup()

    def move_base(self, path="orca.yaml", text="updated\n"):
        w = self.t / "w"
        git("clone", "-q", "--no-local", self.fx.origin, w, cwd=self.t)
        (w / path).parent.mkdir(parents=True, exist_ok=True)
        (w / path).write_text(text)
        git("add", "-f", path, cwd=w)
        git("commit", "-q", "-m", "base moves on", cwd=w)
        git("push", "-q", "origin", "main", cwd=w)
        shutil.rmtree(w)
        git("fetch", "-q", "origin", cwd=self.fx.repo)

    def ticket_dir(self, nonce) -> Path:
        return SB.lane_dir(self.fx.lane, self.fx.repo) / f"t-{nonce}"

    def stub(self, mode: str, nonce: "str | None", ro: "list | None" = None, **extra):
        td = self.ticket_dir(nonce) if nonce else None
        home = str(self.fx.home)
        floor = [str(self.fx.home / f) for f in FX.FLOOR] + [str(self.fx.repo / "fixtures/customers/a.csv"),
                                                             str(self.fx.repo / ".env"), str(self.fx.repo / "other.txt")]
        cfg = {"mark_a": self.mark_a, "mark_b": self.mark_b, "mode": mode, "home": home, "wt": str(self.fx.wt),
               "shared": str(self.fx.repo), "floor": floor, "foreign": [self.fx.other_sha, self.fx.wip_sha],
               "ro": ro if ro is not None else [f"{home}/.gitconfig", f"{home}/.config/opencode/opencode.json",
                                                 "/etc/resolv.conf", "/run/lane/persona.md",
                                                 str(ROOT / "core" / "sandbox.py"), f"{home}/.agents/bin/git",
                                                 str(self.agent / "opencode2"), "/run/lane/inbox/addrs",
                                                 self.fx.conf["SANDBOX_BWRAP"]],
               "open_port": self.net.port(self.net.open), "closed_port": self.net.port(self.net.closed),
               "udp_port": self.net.udp.getsockname()[1], "host_port": self.net.port(self.net.host),
               "abstract": self.net.abstract, "addrs": SB.host_addrs(), "nft": self.fx.conf["SANDBOX_NFT"],
               "out_marker": str(self.out_marker), "learned": self.fx.wip_sha,
               "outbox_host": str(td / "run" / "outbox") if td else str(self.t / "control-outbox"),
               "inbound_port": 0, "pushed": "", **extra}
        if cfg["inbound_port"] == 0:
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            cfg["inbound_port"] = s.getsockname()[1]
            s.close()
        (self.agent / "stub.json").write_text(json.dumps(cfg))
        if td:
            (td / "run" / "inbox").mkdir(parents=True, exist_ok=True)
            (td / "run" / "inbox" / "addrs").write_text("\n".join(cfg["addrs"]) + "\n")
        return cfg

    def pane_env(self, **over):
        e = {**self.fx.env, "PATH": f"{self.fakebin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
             "ORCA_CLI": str(self.fakebin / "orca-ide"), "ORCA_TERMINAL_HANDLE": self.term,
             "ORCA_AGENT_HOOK_PORT": str(self.net.port(self.net.open)), "ORCA_AGENT_HOOK_TOKEN": "fake-hook-token",
             "FAKE_GATEWAY_KEY": self.key, "FAKE_OWNER_TOKEN": "fake-owner-token", "TERM": "xterm"}
        for k in ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"):
            if testhome.ORIGINAL_ENV.get(k):
                e[k] = testhome.ORIGINAL_ENV[k]
        e.update(over)
        return {k: v for k, v in e.items() if v is not None}

    def prepare(self, role="lanes", sha=None):
        return self.fx.prepare(role, sha)

    def launch(self, probe=None, timeout=240):
        p = subprocess.Popen([str(SHIM), "opencode2", "--standalone"], cwd=self.fx.wt, env=self.pane_env(),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, stdin=subprocess.DEVNULL)
        extra = probe(p) if probe else None
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out, extra


class EscapeTests(unittest.TestCase):
    """One sandboxed lane run with every attack, then resume, an unpushed lane and a recreated clone."""

    @classmethod
    def setUpClass(cls):
        p = need_sandbox(cls)
        cls.m = m = Machine(p)
        try:
            cls.nonce = m.prepare()
            m.move_base()
            cls.cfg = m.stub("lane", cls.nonce)
            td = m.ticket_dir(cls.nonce)
            cls.ro_before = {f: sha_of(f) for f in (*[Path(x) for x in (td / "gen").iterdir() if x.is_file()],
                                                    ROOT / "core" / "sandbox.py", ROOT / "core/sandbox-inside/wrap.py",
                                                    m.agent / "opencode2")}

            def probe(proc):
                outbox = td / "run" / "outbox"
                for _ in range(1800):
                    if (outbox / "listening").exists() or proc.poll() is not None:
                        break
                    time.sleep(0.1)
                n = 0
                for a in ["127.0.0.1", *cls.cfg["addrs"]]:
                    try:
                        socket.create_connection((a, cls.cfg["inbound_port"]), timeout=1).close()
                        n += 1
                    except OSError:
                        pass
                hits = 0
                for pid in os.listdir("/proc"):
                    if pid.isdigit():
                        try:
                            hits += m.key.encode() in Path(f"/proc/{pid}/cmdline").read_bytes()
                        except OSError:
                            pass
                (td / "run" / "inbox" / "probed").write_text("")
                return {"inbound": n, "argv_hits": hits, "listening": (outbox / "listening").exists()}
            cls.rc, cls.out, cls.probe = m.launch(probe)
            for d in (m.fx.wt, m.fx.repo):
                subprocess.run(["git", "status", "--short"], cwd=d, capture_output=True)
            cls.review = m.t / "review"
            subprocess.run(["git", "clone", "-q", str(m.fx.origin), str(cls.review)], capture_output=True)
            subprocess.run(["git", "checkout", "-q", "lane-1"], cwd=cls.review, capture_output=True)
            subprocess.run(["git", "status", "--short"], cwd=cls.review, capture_output=True)
            cls.honest = (td / "run" / "outbox" / "honest").read_text().strip() \
                if (td / "run" / "outbox" / "honest").exists() else None
        except BaseException:
            m.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.m.close()

    def lines(self):
        return self.out.splitlines()

    def test_no_bad_line_inside(self):
        bad = [x for x in self.lines() if x.startswith("BAD")]
        self.assertEqual(bad, [], self.out)
        self.assertEqual(self.rc, 0, self.out)
        self.assertGreater(len([x for x in self.lines() if x.startswith("ok")]), 50, self.out)

    def test_capabilities_and_binds(self):
        self.assertIn("no capabilities (CapEff 0", self.out)
        self.assertEqual(sum(1 for x in self.lines() if x.startswith("ok   cannot remount or write")),
                         len(self.cfg["ro"]))
        self.assertIn("ok   cannot make a nested user namespace", self.out)
        for x in ("unshare -U is refused", "unshare -U -n -m is refused", "unshare(CLONE_NEWUSER) is refused",
                  "fork and clone still work"):
            self.assertIn(f"ok   {x}", self.out)  # r1 M3
        self.assertIn("ok   cannot change the network filter", self.out)
        after = {f: sha_of(f) for f in self.ro_before}
        self.assertEqual(after, self.ro_before)  # no host file behind a read-only bind changed

    def test_floor_and_environment(self):
        for f in FX.FLOOR:
            self.assertIn(f"ok   floor file {Path(f).name}: nothing to read", self.out)
        self.assertIn("finds no floor data", self.out)
        self.assertIn("ok   no visible process has the owner's token", self.out)
        self.assertIn("did not reach the agent", self.out)

    def test_network(self):
        for x in ("nix daemon socket cannot", "forwarded loopback port works", "not forwarded cannot",
                  "UDP port cannot", "abstract unix socket cannot", "other addresses reaches"):
            self.assertTrue(any(line.startswith("ok") and x in line for line in self.lines()), x)
        self.assertTrue(self.probe["listening"])
        self.assertEqual(self.probe["inbound"], 0)  # a port opened inside, from every host address

    def test_no_key_in_host_argv(self):
        self.assertEqual(self.probe["argv_hits"], 0)

    def test_shared_repo_and_foreign_objects(self):
        self.assertIn("ok   the shared repository is not there", self.out)
        self.assertEqual(sum(1 for x in self.lines() if "is not in the clone" in x and x.startswith("ok")), 2)

    def test_broker_refusals_inside(self):
        for x in ("another terminal's dispatch is refused", "flag-shaped bd id is refused", "repo field is refused",
                  "cannot write a review", "--type handoff are refused", "gh api is refused",
                  "own dispatch goes out"):
            self.assertTrue(any(line.startswith("ok") and x in line for line in self.lines()), x)
        sends = [json.loads(x) for x in self.m.orca_log.read_text().splitlines() if '"send"' in x]
        self.assertEqual(len(sends), 1)
        self.assertIn(f"--from={self.m.term}", sends[0])

    def test_hostile_pushes_refused_honest_taken(self):
        for x in ("link to a lane-made repo", "link to a file", "id-only bundle", "FIFO", "80 MB", "two refs",
                  "orphan", "CI workflow", "rewritten branch"):
            self.assertTrue(any(line.startswith("ok") and x in line for line in self.lines()), x)
        self.assertEqual(git("rev-parse", "refs/lanes/app-1", cwd=self.m.fx.repo), self.honest)
        self.assertEqual(git("rev-parse", "lane-1", cwd=self.m.fx.origin), self.honest)
        self.assertNotEqual(subprocess.run(["git", "cat-file", "-e", self.m.fx.wip_sha],
                                           cwd=self.m.fx.origin).returncode, 0)

    def test_work_cycle(self):
        for x in ("commit with the generated git identity", "git pull takes the base", "lane-heavy runs",
                  "the persona's paths work inside", "renamed a folder",
                  "git push -u through the broker", "up to date with origin"):
            self.assertTrue(any(line.startswith("ok") and x in line for line in self.lines()), x)
        self.assertEqual(git("log", "-1", "--format=%an", self.honest, cwd=self.m.fx.repo), "Owner")

    def test_lane_files_not_on_host(self):
        self.assertFalse(self.m.out_marker.exists())  # host git in the worktree, main checkout, a fresh checkout
        wt = self.m.fx.wt
        self.assertFalse((wt / "lane-file").exists())
        self.assertEqual(subprocess.run(["git", "status", "--porcelain"], cwd=wt, capture_output=True,
                                        text=True).stdout, "")
        self.assertEqual(git("rev-parse", "HEAD", cwd=wt), self.m.fx.base)
        self.assertFalse((self.m.fx.repo / "lane-file").exists())

    def test_zz_resume_dirty_recreate(self):
        m = self.m
        pushed = git("rev-parse", "refs/lanes/app-1", cwd=m.fx.repo)
        n = m.prepare()  # resume: the clone is reused
        m.stub("inspect", n, pushed=pushed)
        rc, out, _ = m.launch()
        self.assertIn("ok   a relaunch finds no floor data", out)
        self.assertEqual(rc, 0, out)
        self.assertIn(f"inspect head={pushed} dirty=1 unpushed=0", out)  # the stub's renamed folder, untracked
        shutil.rmtree(m.fx.clone() / "renamed-fixtures")
        n = m.prepare()
        m.stub("edit", n, pushed=pushed)
        rc, out, _ = m.launch()
        self.assertIn("dirty=1 unpushed=1", out)
        r = subprocess.run([sys.executable, str(LANE_SANDBOX), "inspect", "app-1"], env=m.pane_env(
            ORCA_TERMINAL_HANDLE=None), capture_output=True, text=True, timeout=240)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        # r1 M4: every branch counts: the edit's commit, and the stub's own unpushed `extra` branch
        self.assertIn("1 dirty file(s), 2 commit(s) not pushed (any branch), 0 stash(es)", r.stdout)
        shutil.rmtree(m.fx.clone())  # the clone is lost
        n = m.prepare()
        m.stub("inspect", n, pushed=pushed)
        rc, out, _ = m.launch()
        self.assertIn(f"inspect head={pushed} dirty=0 unpushed=0", out)  # recreated from refs/lanes, checked out inside


class AbortTests(unittest.TestCase):
    """r1 M2, with real pasta and bwrap: the launcher killed or stopped while it sets the network filter (a slow
    nsenter stands in for the window): the agent never starts, and there is no run record."""

    def machine(self):
        p = dict(need_sandbox(self))
        slow = Path(tempfile.mkdtemp(prefix="sbxns"))
        self.addCleanup(shutil.rmtree, slow, True)
        self.marker = slow / "nsenter-started"
        w = slow / "nsenter"
        w.write_text(f"#!{shutil.which('bash') and os.path.realpath(shutil.which('bash'))}\n: >{self.marker}\n"
                     f"{os.path.join(os.path.dirname(os.path.realpath(shutil.which('sleep'))), 'sleep')} 4\nexec {p['SANDBOX_NSENTER']} \"$@\"\n")
        w.chmod(0o755)
        p["SANDBOX_NSENTER"] = str(w)
        m = Machine(p)
        self.addCleanup(m.close)
        return m

    def abort_with(self, sig):
        m = self.machine()
        n = m.prepare()
        m.stub("started", n)
        outbox = m.ticket_dir(n) / "run" / "outbox"
        proc = subprocess.Popen([str(SHIM), "opencode2"], cwd=m.fx.wt, env=m.pane_env(), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, stdin=subprocess.DEVNULL)
        for _ in range(600):
            if self.marker.exists():
                break
            time.sleep(0.05)
        self.assertTrue(self.marker.exists(), "the launcher never reached the filter step")
        proc.send_signal(sig)
        out, _ = proc.communicate(timeout=60)
        time.sleep(6)  # longer than the slow nsenter: a start would have happened by now
        self.assertFalse((outbox / "started").exists(), (sig, out))
        self.assertFalse(SB.run_record(m.term).exists())
        return proc.returncode, out

    def test_sigkill_during_the_filter(self):
        self.abort_with(signal.SIGKILL)

    def test_sigquit_during_the_filter(self):
        rc, out = self.abort_with(signal.SIGQUIT)
        self.assertEqual(rc, 2, out)

    def test_sigterm_during_the_filter(self):
        rc, out = self.abort_with(signal.SIGTERM)
        self.assertEqual(rc, 2, out)
        self.assertIn("SIGTERM before the agent was released", out)


class ControlTests(unittest.TestCase):
    """The same probes with no sandbox: they do find leaks (so a clean sandbox run means something)."""

    def test_control_finds_leaks(self):
        p = need_sandbox(self)
        m = Machine(p)
        self.addCleanup(m.close)
        m.prepare()
        scratch = m.t / "scratch-ro"
        scratch.write_text("x\n")
        cfg = m.stub("control", None, ro=[str(scratch)])
        work = m.t / "control-wt"
        shutil.copytree(m.fx.wt, work, symlinks=True)
        cfg["wt"] = str(work)
        (m.agent / "stub.json").write_text(json.dumps(cfg))
        env = {**m.pane_env(), "HOME": str(m.fx.home)}
        r = subprocess.run(["bash", str(m.agent / "opencode2")], cwd=work, env=env, capture_output=True, text=True,
                           timeout=120)
        bad = [x for x in r.stdout.splitlines() if x.startswith("BAD")]
        self.assertNotEqual(r.returncode, 0)
        for x in ("read floor file", "owner's token is in the agent's environment", "wrote read-only",
                  "made a nested user namespace", "host loopback TCP port that is not forwarded"):
            self.assertTrue(any(x in line for line in bad), (x, r.stdout))


class TtyTests(unittest.TestCase):
    def test_pty_resize_and_dev_tty(self):
        p = need_sandbox(self)
        m = Machine(p)
        self.addCleanup(m.close)
        n = m.prepare()
        m.stub("tty", n)
        outbox = m.ticket_dir(n) / "run" / "outbox"
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(m.fx.wt)
            os.execve(str(SHIM), [str(SHIM), "opencode2"], m.pane_env())
        os.set_blocking(fd, False)  # the stub prints nothing: never wait on the pty for output
        try:
            for _ in range(600):
                if (outbox / "tty-ready").exists():
                    break
                try:
                    os.read(fd, 65536)
                except OSError:
                    pass
                time.sleep(0.05)
            self.assertTrue((outbox / "tty-ready").exists(), "the stub did not start under the pty")
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
            for _ in range(100):
                if (outbox / "winch").exists():
                    break
                time.sleep(0.05)
            if SB.tiocsti_allowed():  # --new-session: the documented cost (spec, "What breaks")
                self.assertEqual((outbox / "tty").read_text().strip(), "tty=no")
            else:
                self.assertEqual((outbox / "tty").read_text().strip(), "tty=yes")
                self.assertTrue((outbox / "winch").exists(), "SIGWINCH did not reach the agent")
        finally:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            os.waitpid(pid, 0)
            os.close(fd)


class HistoryTests(unittest.TestCase):
    def test_history_refusals_through_prepare(self):
        p = need_sandbox(self)
        m = Machine(p)
        self.addCleanup(m.close)
        m.move_base("fixtures/customers/new.csv", "private\n")
        with self.assertRaises(SB.SandboxError) as e:
            m.prepare()
        self.assertIn("fixtures/customers/new.csv", str(e.exception))


if os.environ.get("LANE_SANDBOX_NET") == "1":
    class NetTests(unittest.TestCase):
        def test_dns_and_outbound(self):
            p = need_sandbox(self)
            m = Machine(p)
            self.addCleanup(m.close)
            n = m.prepare()
            m.stub("net", n)
            script = m.agent / "opencode2"
            script.write_text("#!/usr/bin/env bash\npython3 -I -c 'import socket; socket.create_connection("
                              "(\"example.com\", 443), timeout=10).close(); print(\"net ok\")'\n")
            rc, out, _ = m.launch()
            self.assertIn("net ok", out)


if __name__ == "__main__":
    if sys.argv[1:] == ["--availability"]:
        p, why = pins()
        print("sandbox selftest: " + ("bwrap and pasta can run here: the adversarial suite runs" if p else
                                      f"skipped here: {why} (it runs as lane-control sandbox selftest)"))
        sys.exit(0)
    unittest.main()
