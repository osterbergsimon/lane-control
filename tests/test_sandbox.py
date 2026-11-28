"""SBX-2 task 2 (lc-35qn): core/sandbox.py, the pure parts of the OpenCode lane sandbox (spec
docs/superpowers/specs/2026-11-27-agent-sandbox-design.md): tickets, the launch chain's argv and environment, the
mount plan, the namespace's nft rules, the host-pid lookup, the history and CI checks and the broker's outbox read.
Real git in temp repos; no bwrap or pasta is run here (tests/test_sandbox_escapes.py runs them)."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
import importlib.util
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SB = load("lc_sandbox_t", "core/sandbox.py")
KEY = "fake-gateway-key-" + "Q7" * 12
TOKEN = "fake-hook-token-" + "R8" * 12
LAUNCH = "fake-launch-token-" + "S9" * 12


def git(*args, cwd, **kw):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *map(str, args)], cwd=cwd,
                          capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL, **kw).stdout.strip()


CONF = {"SANDBOX_BWRAP": "/nix/store/aaa-bubblewrap/bin/bwrap", "SANDBOX_PASTA": "/nix/store/bbb-passt/bin/pasta",
        "SANDBOX_NSENTER": "/nix/store/ccc-util-linux/bin/nsenter", "SANDBOX_NFT": "/nix/store/ddd-nftables/bin/nft",
        "SANDBOX_LANE_MEMORY": "6G"}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.t = Path(tmp.name).resolve()
        self.home = self.t / "home"
        self.home.mkdir()
        env = testhome.env_for(self.home)
        patch = mock.patch.dict(os.environ, env)
        patch.start()
        self.addCleanup(patch.stop)

    def ticket(self, **over):
        lane = self.t / "state" / "sandbox" / "app-1"
        td = lane / "t-abc"
        gen = td / "gen"
        t = {"nonce": "abc", "role": "lane", "lane": "app-1", "issue": "app-1x", "project": "app",
             "repo": str(self.t / "app"), "base": "main", "branch": "lane-1", "worktree": str(self.t / "wt" / "lane-1"),
             "clone": str(lane / "repo"), "lane_dir": str(lane), "ticket_dir": str(td), "home": str(self.home),
             "home_files": {"gitconfig": str(gen / "gitconfig"), "opencode.json": str(gen / "opencode.json"),
                            "resolv.conf": str(gen / "resolv.conf"), "plugins": str(gen / "plugins"),
                            "skills": str(gen / "skills")},
             "persona": str(gen / "persona.md"), "tool_path": ["/nix/store/eee-git/bin", "/nix/store/fff-py/bin"],
             "inside": str(ROOT / "core" / "sandbox-inside"), "lc_tree": str(ROOT), "gateway_key_env": "GATEWAY_KEY",
             "gateway": None, "hook_port": 4411, "review_files": [], "review_write": None, "sha": None,
             "private": ["fixtures/customers"], "agent": ["/nix/store/ggg-opencode/bin/opencode", "--standalone"],
             "orca_binds": [], "agent_dirs": [], "bash": "/nix/store/hhh-bash/bin/bash"}
        t.update(over)
        return t

    def owner_env(self):
        return {"HOME": str(self.home), "PATH": "/usr/bin", "TERM": "xterm-256color", "LANG": "C.UTF-8",
                "GATEWAY_KEY": KEY, "ORCA_AGENT_HOOK_TOKEN": TOKEN, "ORCA_AGENT_LAUNCH_TOKEN": LAUNCH,
                "ORCA_AGENT_HOOK_PORT": "4411", "ORCA_TERMINAL_HANDLE": "term_1", "ORCA_PANE_KEY": "pane-1",
                "GITHUB_TOKEN": "ghp_" + "Z9" * 18, "FAKE_OWNER_TOKEN": "owner-secret",
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/xdg-u/bus", "XDG_RUNTIME_DIR": "/tmp/xdg-u",
                "ORCA_OPENCODE_SOURCE_CONFIG_DIR": str(self.home / ".config" / "opencode"), "SSH_AUTH_SOCK": "/x"}


class ChainTests(Base):
    def chain(self, scope=True, tiocsti_ok=False, **over):
        with mock.patch.object(SB, "tiocsti_allowed", lambda: tiocsti_ok):
            return SB.chain_argv(self.ticket(**over), CONF, scope, self.owner_env())

    def test_chain_order_and_flags(self):
        argv, _ = self.chain()
        self.assertEqual(argv[0], "systemd-run")
        self.assertEqual(argv[1:7], ["--user", "--scope", "-q", "-p", "MemoryMax=6G", "--"])
        p = argv.index(CONF["SANDBOX_PASTA"])
        self.assertEqual(argv[p + 1:p + 15], ["--config-net", "-q", "-f", "--no-map-gw", "--dns-forward", "169.254.1.53",
                                             "-t", "none", "-u", "none", "-U", "none", "-T", "4411"])
        self.assertEqual(argv[p + 15], "--")
        b = argv.index(CONF["SANDBOX_BWRAP"])
        opener = argv[p + 16:b]
        self.assertTrue(opener[0].endswith("bash") and opener[1] == "-c", opener)
        self.assertIn('3>"$i"', opener[2])
        self.assertIn('4<"$b"', opener[2])
        self.assertIn('5<"$s"', opener[2])
        launch = Path(self.ticket()["ticket_dir"]) / "launch"
        self.assertEqual(opener[-3:], [str(launch / "info.json"), str(launch / "block"), str(launch / "seccomp.bpf")])
        bw = argv[b + 1:]
        self.assertEqual(bw[:2], ["--cap-drop", "ALL"])
        for f in ("--die-with-parent", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try"):
            self.assertIn(f, bw)
        self.assertEqual(bw[bw.index("--unsetenv") + 1], "DBUS_SESSION_BUS_ADDRESS")
        self.assertIn("XDG_RUNTIME_DIR", bw)
        self.assertEqual(bw[bw.index("--seccomp") + 1], "5")  # r1 M3: the filter, opened by path inside pasta
        i = bw.index("--info-fd")
        self.assertEqual(bw[i:i + 4], ["--info-fd", "3", "--block-fd", "4"])
        self.assertEqual(bw[i + 4:], ["/nix/store/ggg-opencode/bin/opencode", "--standalone"])
        self.assertNotIn("--unshare-user", bw)
        self.assertNotIn("--clearenv", argv)
        self.assertNotIn("--setenv", argv)

    def test_gateway_port_forwarded_on_loopback(self):
        argv, _ = self.chain(gateway=["127.0.0.1", 8317])
        self.assertIn("4411,8317", argv)

    def test_no_secret_in_argv(self):
        argv, env = self.chain()
        joined = "\0".join(argv)
        for secret in (KEY, TOKEN, LAUNCH, "owner-secret", "ghp_"):
            self.assertNotIn(secret, joined)
        self.assertEqual(env["GATEWAY_KEY"], KEY)
        self.assertEqual(env["ORCA_AGENT_HOOK_TOKEN"], TOKEN)
        self.assertEqual(env["ORCA_AGENT_LAUNCH_TOKEN"], LAUNCH)
        for dropped in ("GITHUB_TOKEN", "FAKE_OWNER_TOKEN", "SSH_AUTH_SOCK", "ORCA_OPENCODE_SOURCE_CONFIG_DIR"):
            self.assertNotIn(dropped, env)
        self.assertEqual(env["PATH"].split(":")[:2], [str(Path(self.ticket()["inside"]) / "bin"),
                                                      "/nix/store/eee-git/bin"])
        self.assertEqual(env["LANE_PERSONA"], "/run/lane/persona.md")
        self.assertEqual(env["LANE_BROKER"], "/run/lane/broker.sock")
        self.assertEqual(env["TMPDIR"], "/tmp")

    def test_bus_vars_only_for_scope(self):
        argv, env = self.chain(scope=True)
        self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/tmp/xdg-u/bus")
        b = argv.index(CONF["SANDBOX_BWRAP"])
        bw = argv[b:]
        for name in ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"):
            self.assertEqual(bw[bw.index(name) - 1], "--unsetenv")
        argv, env = self.chain(scope=False)
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", env)
        self.assertNotIn("XDG_RUNTIME_DIR", env)
        self.assertEqual(argv[0], CONF["SANDBOX_PASTA"])
        self.assertNotIn("systemd-run", argv)

    def test_only_nix_store(self):
        t = self.ticket()
        mounts = SB.bwrap_mounts(t)
        srcs = [mounts[i + 1] for i, x in enumerate(mounts) if x in ("--ro-bind", "--bind", "--ro-bind-try", "--bind-try")]
        forbidden = ["/nix/var", "/nix", "/run/user", str(self.home / ".config" / "orca"), t["repo"],
                     str(self.home / ".config"), str(self.home), "/", "/home"]
        for s in srcs:
            self.assertFalse(s.startswith("/nix/var"), s)
            self.assertNotIn(s, forbidden, s)
            self.assertFalse(t["repo"] == s or s.startswith(t["repo"] + "/"), s)
            self.assertFalse(s.startswith(str(self.home / ".config")), s)
        self.assertIn("/nix/store", srcs)
        self.assertNotIn("/nix", srcs)
        # Orca's two paths, when the shim found them, are bound alone and read-only
        ud = self.home / ".config" / "orca"
        t = self.ticket(orca_binds=[str(ud / "opencode-overlay"), str(ud / "agent-hooks" / "endpoint.env")])
        m = SB.bwrap_mounts(t)
        for p in t["orca_binds"]:
            self.assertEqual(m[m.index(p) - 1], "--ro-bind", p)
        with self.assertRaises(SB.SandboxError):
            SB.bwrap_mounts(self.ticket(orca_binds=[str(ud)]))

    def test_beads_not_mounted(self):
        """r1 B3: no beads folder (the shared repo's .beads, its target, any .beads) is bound; bd is the broker's."""
        t = self.ticket()
        m = SB.bwrap_mounts(t)
        self.assertFalse([x for x in m if ".beads" in x or x.endswith("-beads")])
        with self.assertRaises(SB.SandboxError):
            SB.bwrap_mounts(self.ticket(agent_dirs=[t["repo"] + "/.beads"]))

    def test_ca_and_locale_pass(self):
        env = {**self.owner_env(), "SSL_CERT_FILE": "/etc/ssl/certs/ca.pem", "LOCALE_ARCHIVE": "/nix/store/x/locale",
               "NIX_SSL_CERT_FILE": "/etc/ssl/certs/ca.pem", "TZDIR": "/etc/zoneinfo"}
        with mock.patch.object(SB, "tiocsti_allowed", lambda: False):
            _, e = SB.chain_argv(self.ticket(), CONF, False, env)
        for k in ("SSL_CERT_FILE", "LOCALE_ARCHIVE", "NIX_SSL_CERT_FILE", "TZDIR"):
            self.assertEqual(e[k], env[k])

    def test_clone_at_worktree_path(self):
        t = self.ticket()
        m = SB.bwrap_mounts(t)
        i = m.index(t["clone"])
        self.assertEqual(m[i - 1:i + 2], ["--bind", t["clone"], t["worktree"]])
        self.assertEqual(m[m.index("--chdir") + 1], t["worktree"])
        td = Path(t["ticket_dir"])
        pairs = {(m[i], m[i + 1], m[i + 2]) for i in range(len(m) - 2)}
        self.assertIn(("--bind", str(td / "data"), str(self.home / ".local/share/opencode")), pairs)
        self.assertIn(("--bind", str(td / "cache"), str(self.home / ".cache")), pairs)
        self.assertIn(("--ro-bind", str(td / "run" / "inbox"), "/run/lane/inbox"), pairs)
        self.assertIn(("--bind", str(td / "run" / "outbox"), "/run/lane/outbox"), pairs)
        self.assertIn(("--bind", str(td / "run" / "broker.sock"), "/run/lane/broker.sock"), pairs)
        self.assertIn(("--ro-bind", t["persona"], "/run/lane/persona.md"), pairs)
        self.assertIn(("--ro-bind", t["home_files"]["gitconfig"], str(self.home / ".gitconfig")), pairs)
        self.assertIn(("--ro-bind", t["lc_tree"], t["lc_tree"]), pairs)
        self.assertIn(("--ro-bind", t["inside"], str(self.home / ".agents" / "lane-inside")), pairs)
        self.assertIn(("--symlink", str(self.home / ".agents" / "lane-inside" / "bin"), str(self.home / ".agents" / "bin")),
                      pairs)  # the wrappers' links resolve inside, where personas name them
        self.assertIn(("--tmpfs", str(self.home), "--ro-bind"), pairs)

    def test_reviewer_clone_is_its_own(self):
        t = self.ticket(role="reviewer", clone=str(self.t / "state/sandbox/app-1/review-1/repo"))
        m = SB.bwrap_mounts(t)
        self.assertEqual(m[m.index(t["clone"]) + 1], t["worktree"])

    def test_etc_minimal(self):
        t = self.ticket()
        m = SB.bwrap_mounts(t)
        self.assertIn(("--tmpfs", "/etc"), set(zip(m, m[1:])))
        etc = [m[i + 2] for i, x in enumerate(m) if x in ("--ro-bind", "--ro-bind-try") and m[i + 2].startswith("/etc")]
        allowed = {"/etc/" + n for n in ("passwd", "group", "hosts", "nsswitch.conf", "ssl", "pki", "static",
                                         "localtime", "zoneinfo", "resolv.conf")}
        self.assertTrue(set(etc) <= allowed, set(etc) - allowed)
        self.assertEqual(m[m.index("/etc/resolv.conf") - 1], t["home_files"]["resolv.conf"])
        for p in ("/proc", "/dev", "/tmp", "/run"):
            self.assertTrue(any(m[i] in ("--proc", "--dev", "--tmpfs") and m[i + 1] == p for i in range(len(m) - 1)), p)

    def test_new_session_only_when_tiocsti_allowed(self):
        argv, _ = self.chain(tiocsti_ok=True)
        self.assertIn("--new-session", argv)
        argv, _ = self.chain(tiocsti_ok=False)
        self.assertNotIn("--new-session", argv)

    def test_tiocsti_reads_proc(self):
        f = self.t / "legacy_tiocsti"
        f.write_text("0\n")
        self.assertFalse(SB.tiocsti_allowed(f))
        f.write_text("1\n")
        self.assertTrue(SB.tiocsti_allowed(f))
        self.assertTrue(SB.tiocsti_allowed(self.t / "missing"))  # an old kernel: TIOCSTI works

    def test_agent_dir_bound_read_only(self):
        m = SB.bwrap_mounts(self.ticket(agent_dirs=["/opt/opencode"]))
        self.assertEqual(m[m.index("/opt/opencode") - 1], "--ro-bind")
        with self.assertRaises(SB.SandboxError):
            SB.bwrap_mounts(self.ticket(agent_dirs=[str(self.home)]))


class SeccompTests(unittest.TestCase):
    """r1 M3: the filter's program, read back instruction by instruction (the live suite runs it under bwrap)."""

    def decode(self, prog):
        import struct
        return [struct.unpack("<HBBI", prog[i:i + 8]) for i in range(0, len(prog), 8)]

    def run_bpf(self, prog, arch, nr, a0=0, a1=0):
        """A tiny interpreter for the four instructions the filter uses."""
        data = {0: nr, 4: arch, 16: a0, 24: a1}
        ins, pc, acc = self.decode(prog), 0, 0
        while True:
            code, jt, jf, k = ins[pc]
            if code == 0x20:
                acc = data[k]
                pc += 1
            elif code == 0x06:
                return k
            else:
                hit = {0x15: acc == k, 0x35: acc >= k, 0x45: bool(acc & k)}[code]
                pc += 1 + (jt if hit else jf)

    def test_filter(self):
        for m, (arch, unshare, clone, clone3, ioctl, x32) in SB.SECCOMP_ARCH.items():
            p = SB.seccomp_filter(m)
            allow, eperm, enosys = 0x7FFF0000, 0x50001, 0x50026
            self.assertEqual(self.run_bpf(p, arch, unshare, SB.CLONE_NEWUSER), eperm, m)
            self.assertEqual(self.run_bpf(p, arch, unshare, SB.CLONE_NEWUSER | 0x20000), eperm, m)
            self.assertEqual(self.run_bpf(p, arch, unshare, 0x20000), allow, m)  # a mount namespace alone: allowed
            self.assertEqual(self.run_bpf(p, arch, clone, SB.CLONE_NEWUSER | 0x11), eperm, m)
            self.assertEqual(self.run_bpf(p, arch, clone, 0x11), allow, m)  # fork-like clone
            self.assertEqual(self.run_bpf(p, arch, clone3), enosys, m)
            self.assertEqual(self.run_bpf(p, arch, ioctl, 0, SB.TIOCSTI), eperm, m)
            self.assertEqual(self.run_bpf(p, arch, ioctl, 0, SB.TIOCLINUX), eperm, m)
            self.assertEqual(self.run_bpf(p, arch, ioctl, 0, 0x5413), allow, m)  # TIOCGWINSZ
            self.assertEqual(self.run_bpf(p, 0x40000003, 1), eperm, m)  # another ABI
            self.assertEqual(self.run_bpf(p, arch, 0), allow, m)
            if x32:
                self.assertEqual(self.run_bpf(p, arch, 0x40000000 | unshare), eperm, m)
        with self.assertRaises(SB.SandboxError):
            SB.seccomp_filter("riscv64")


class NftTests(unittest.TestCase):
    def test_nft_rules(self):
        r = SB.nft_rules(["192.0.2.7", "2001:db8::7", "fe80::1"], None)
        lines = [x.strip() for x in r.splitlines()]
        dns = lines.index("ip daddr 169.254.1.53 accept")
        drops = [i for i, x in enumerate(lines) if x.endswith("drop")]
        self.assertLess(dns, min(drops))
        self.assertIn("ip daddr { 192.0.2.7 } drop", lines)
        self.assertIn("ip6 daddr { 2001:db8::7 } drop", lines)
        for rng in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10"):
            self.assertIn(rng, r)
        for rng in ("fc00::/7", "fe80::/10"):
            self.assertIn(rng, r)
        self.assertIn("type filter hook output priority 0; policy accept;", r)

    def test_gateway_accept_first(self):
        r = SB.nft_rules([], ("192.168.1.20", 8317))
        lines = [x.strip() for x in r.splitlines()]
        acc = lines.index("ip daddr 192.168.1.20 tcp dport 8317 accept")
        self.assertLess(acc, min(i for i, x in enumerate(lines) if x.endswith("drop")))
        self.assertNotIn("ip daddr {  } drop", r)
        self.assertFalse(any(x.startswith("ip daddr {") and "192.0.2" in x for x in lines))
        r = SB.nft_rules([], ("gateway.example.com", 443))  # a name on the internet: nothing to accept
        self.assertNotIn("gateway.example.com", r)

    def test_lan_prefixes_multicast_and_nd(self):
        """r1 M7: the host's on-link prefixes (a LAN with global addresses), multicast and broadcast are dropped;
        IPv6 neighbour discovery is accepted first; a named private gateway is resolved here and accepted."""
        r = SB.nft_rules(["192.0.2.7"], None, ["198.51.100.0/24", "2001:db8:1::/64"])
        lines = [x.strip() for x in r.splitlines()]
        nd = lines.index("icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-solicit } accept")
        self.assertLess(nd, min(i for i, x in enumerate(lines) if x.endswith("drop")))
        self.assertIn("ip daddr { 198.51.100.0/24 } drop", lines)
        self.assertIn("ip6 daddr { 2001:db8:1::/64 } drop", lines)
        for x in ("224.0.0.0/4", "255.255.255.255", "ff00::/8"):
            self.assertIn(x, r)
        with mock.patch("socket.getaddrinfo", lambda h, p, type=0: [(2, 1, 6, "", ("192.168.7.9", p))]):
            r = SB.nft_rules([], ("gw.lan.example", 4000))
        self.assertIn("ip daddr 192.168.7.9 tcp dport 4000 accept", r)
        with mock.patch("socket.getaddrinfo", side_effect=OSError("no name")):
            self.assertNotIn(" tcp dport 4000 accept", SB.nft_rules([], ("gw.lan.example", 4000)))
        with self.assertRaises(SB.SandboxError):
            SB.nft_rules([], None, ["not-a-net; flush ruleset"])

    def test_host_onlink_from_proc(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "net").mkdir()
            (d / "net" / "route").write_text(
                "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
                "eth0\t00000000\t0102000A\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"  # default via a gateway
                "eth0\t006433C6\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n")  # 198.51.100.0/24 on-link
            (d / "net" / "ipv6_route").write_text(
                "20010db8000100000000000000000000 40 00000000000000000000000000000000 00 "
                "00000000000000000000000000000000 00000100 00000001 00000000 00000001 eth0\n"
                "fe800000000000000000000000000000 40 00000000000000000000000000000000 00 "
                "00000000000000000000000000000000 00000100 00000001 00000000 00000001 eth0\n"
                "00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
                "fe800000000000000000000000000001 00000400 00000001 00000000 00000003 eth0\n")
            self.assertEqual(SB.host_onlink(d), ["198.51.100.0/24", "2001:db8:1::/64"])

    def test_rejects_junk_addresses(self):
        with self.assertRaises(SB.SandboxError):
            SB.nft_rules(["1.2.3.4; flush ruleset"], None)

    def test_host_addrs_from_proc(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "net").mkdir()
            (d / "net" / "fib_trie").write_text(
                "Main:\n  +-- 0.0.0.0/0 3 0 5\n     |-- 192.0.2.7\n        /32 host LOCAL\n"
                "     |-- 127.0.0.1\n        /32 host LOCAL\n     |-- 192.0.2.255\n        /32 link BROADCAST\n")
            (d / "net" / "if_inet6").write_text(
                "20010db8000000000000000000000007 02 40 00 80 eth0\n00000000000000000000000000000001 01 80 10 80 lo\n")
            self.assertEqual(SB.host_addrs(d), ["192.0.2.7", "2001:db8::7"])


class HostPidTests(unittest.TestCase):
    def proc(self, entries):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(d)]))
        for pid, (mnt, nspid) in entries.items():
            p = d / str(pid)
            (p / "ns").mkdir(parents=True)
            os.symlink(f"mnt:[{mnt}]", p / "ns" / "mnt")
            (p / "status").write_text(f"Name:\tbwrap\nNSpid:\t{' '.join(map(str, nspid))}\n")
        (d / "self").mkdir()
        return d

    def test_host_pid_unique(self):
        d = self.proc({100: (4026, [100, 2]), 101: (4026, [101, 6, 1]), 102: (999, [102, 6])})
        self.assertEqual(SB.host_pid(4026, 6, d), 101)
        with self.assertRaises(SB.SandboxError):
            SB.host_pid(4026, 7, d)
        d = self.proc({100: (4026, [100, 6, 1]), 103: (4026, [103, 6, 1])})
        with self.assertRaises(SB.SandboxError):
            SB.host_pid(4026, 6, d)


class HistoryTests(Base):
    def repo(self):
        r = self.t / "r"
        r.mkdir()
        git("init", "-q", "-b", "main", cwd=r)
        (r / "a.txt").write_text("a\n")
        git("add", ".", cwd=r)
        git("commit", "-q", "-m", "a", cwd=r)
        return r

    def commit(self, r, path, text="x\n", msg="c"):
        f = r / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        git("add", "-f", path, cwd=r)
        git("commit", "-q", "-m", msg, cwd=r)

    def test_history_hits(self):
        r = self.repo()
        base = git("rev-parse", "HEAD", cwd=r)
        self.assertEqual(SB.history_hits(r, ["--all"], ["fixtures/customers"]), [])
        git("switch", "-q", "-c", "side", cwd=r)
        self.commit(r, "fixtures/customers/b.csv")
        git("rm", "-q", "fixtures/customers/b.csv", cwd=r)
        git("commit", "-q", "-m", "rm", cwd=r)
        git("switch", "-q", "main", cwd=r)
        git("merge", "-q", "--no-ff", "--no-edit", "side", cwd=r)
        self.assertEqual(git("log", "--format=%H", "main", "--", "fixtures/customers", cwd=r), "")  # round 2's miss
        self.assertEqual(SB.history_hits(r, ["main"], ["fixtures/customers"]), ["fixtures/customers/b.csv"])
        for form in ("./fixtures/customers/", "fixtures/customers/", "FIXTURES/Customers", "./fixtures/customers"):
            self.assertEqual(SB.history_hits(r, ["main"], [form]), ["fixtures/customers/b.csv"], form)
        self.assertEqual(SB.history_hits(r, ["main", f"^{base}"], ["other"]), [])

    def test_env_files(self):
        r = self.repo()
        self.commit(r, ".env.example")
        self.commit(r, "docs/.env.sample")
        self.assertEqual(SB.history_hits(r, ["--all"], []), [])
        self.commit(r, "sub/.env.local")
        self.commit(r, ".ENV")
        self.assertEqual(SB.history_hits(r, ["--all"], []), [".ENV", "sub/.env.local"])

    def test_ci_hits(self):
        r = self.repo()
        base = git("rev-parse", "HEAD", cwd=r)
        self.commit(r, "src/x.py")
        self.assertEqual(SB.ci_hits(r, ["HEAD", f"^{base}"]), [])
        for p in (".github/workflows/x.yml", ".GitHub/Actions/a/action.yml", "Jenkinsfile", "sub/.gitlab-ci.yml",
                  ".circleci/config.yml", "azure-pipelines.yml"):
            self.commit(r, p)
        self.assertEqual(SB.ci_hits(r, ["HEAD", f"^{base}"]),
                         [".GitHub/Actions/a/action.yml", ".circleci/config.yml", ".github/workflows/x.yml",
                          "Jenkinsfile", "azure-pipelines.yml", "sub/.gitlab-ci.yml"])

    def test_quoted_names_are_seen(self):
        """r1 B1: names git would C-quote (a quote, backslash, tab, newline) and non-UTF-8 bytes are read exactly."""
        r = self.repo()
        base = git("rev-parse", "HEAD", cwd=r)
        names = ['.github/workflows/q"x.yml', ".github/workflows/b\\s.yml", ".github/workflows/t\tx.yml",
                 ".github/workflows/n\nx.yml"]
        for n in names:
            self.commit(r, n)
        self.assertEqual(SB.ci_hits(r, ["HEAD", f"^{base}"]), sorted(names))
        priv = ['fixtures/customers/a"b.csv', 'dir"x/.env', "dir\\y/.env.local", "fixtures/customers/t\tb.csv"]
        for n in priv:
            self.commit(r, n)
        self.assertEqual(SB.history_hits(r, ["HEAD"], ["fixtures/customers"]), sorted(priv))
        os.makedirs(os.fsencode(r) + b"/fixtures/customers", exist_ok=True)
        with open(os.fsencode(r) + b"/fixtures/customers/\xff.csv", "w") as f:
            f.write("x")
        subprocess.run(["git", "add", "-f", "."], cwd=r, check=True)
        git("commit", "-q", "-m", "latin1", cwd=r)
        hits = SB.history_hits(r, ["HEAD", "^HEAD~1"], ["fixtures/customers"])
        self.assertEqual(len(hits), 1)  # a non-UTF-8 name: still counted, no crash

    def test_ci_folder_as_link_or_submodule(self):
        r = self.repo()
        base = git("rev-parse", "HEAD", cwd=r)
        os.symlink("elsewhere", r / ".github")
        git("add", ".github", cwd=r)
        git("commit", "-q", "-m", "link", cwd=r)
        self.assertEqual(SB.ci_hits(r, ["HEAD", f"^{base}"]), [".github"])
        self.assertTrue(SB.is_ci_path(".GitHub/Workflows"))
        self.assertTrue(SB.is_ci_path(".github/workflows"))
        self.assertFalse(SB.is_ci_path(".github/CODEOWNERS"))

    def test_merged_base_ci_change_is_not_the_lanes(self):
        """r1 m2: merging the base's own workflow change is not flagged; an edit made in the merge itself is."""
        r = self.repo()
        base = git("rev-parse", "HEAD", cwd=r)
        git("switch", "-q", "-c", "lane", cwd=r)
        self.commit(r, "lane.txt")
        git("switch", "-q", "main", cwd=r)
        self.commit(r, ".github/workflows/test.yml", "on: push\n")
        git("switch", "-q", "lane", cwd=r)
        git("merge", "-q", "--no-edit", "main", cwd=r)
        self.assertEqual(SB.ci_hits(r, ["lane", "--not", "main", base]), [])
        git("switch", "-q", "-c", "evil", "lane~1", cwd=r)
        git("merge", "-q", "--no-commit", "main", cwd=r)
        (r / ".github/workflows/test.yml").write_text("on: push\npermissions: write-all\n")
        git("add", ".github", cwd=r)
        git("commit", "-q", "-m", "evil merge", cwd=r)
        self.assertEqual(SB.ci_hits(r, ["evil", "--not", "main", base]), [".github/workflows/test.yml"])

    def test_norm(self):
        self.assertEqual(SB.norm_private(["./x/", "x/", "x", "", "  ./Y "]), ["x", "y"])


class SafeReadTests(Base):
    def test_safe_read(self):
        dst = self.t / "copy"
        good = self.t / "good.bundle"
        good.write_bytes(b"# v2 git bundle\n")
        SB.safe_read(good, 1024, dst)
        self.assertEqual(dst.read_bytes(), b"# v2 git bundle\n")
        link = self.t / "link"
        link.symlink_to(good)
        dlink = self.t / "dlink"
        dlink.symlink_to(self.t)
        fifo = self.t / "fifo"
        os.mkfifo(fifo)
        big = self.t / "big"
        with open(big, "wb") as f:
            f.truncate(65 << 20)
        for p, why in ((link, "link"), (dlink, "link"), (fifo, "regular"), (big, "cap"),
                       (self.t / "missing", "missing"), (self.t, "regular")):
            with self.assertRaises(SB.SandboxError) as e:
                SB.safe_read(p, 64 << 20, self.t / "out")
            self.assertIn(why, str(e.exception), p)

    def test_grows_while_read(self):
        f = self.t / "grow"
        f.write_bytes(b"x" * 1000)
        real_read = os.read

        def read(fd, n):
            chunk = real_read(fd, n)
            if chunk:
                with open(f, "ab") as g:
                    g.write(b"y" * 4096)
            return chunk
        with mock.patch.object(SB.os, "read", read), self.assertRaises(SB.SandboxError) as e:
            SB.safe_read(f, 3000, self.t / "out")
        self.assertIn("cap", str(e.exception))
        self.assertFalse((self.t / "out").exists())


class TicketTests(Base):
    def setUp(self):
        super().setUp()
        self.wt = self.t / "wt" / "lane-1"
        self.wt.mkdir(parents=True)

    def test_worktree_id(self):
        a = SB.worktree_id(self.wt)
        self.assertRegex(a, r"^lane-1-[0-9a-f]{12}$")
        self.assertEqual(a, SB.worktree_id(self.wt / "." ))
        other = self.t / "wt2" / "lane-1"
        other.mkdir(parents=True)
        self.assertNotEqual(a, SB.worktree_id(other))

    def test_take_ticket_oldest(self):
        n1 = SB.write_ticket(self.wt, self.ticket(nonce=None, worktree=str(self.wt)))
        time.sleep(0.01)
        n2 = SB.write_ticket(self.wt, self.ticket(nonce=None, worktree=str(self.wt)))
        self.assertNotEqual(n1, n2)
        t = SB.take_ticket(self.wt, "term_1")
        self.assertEqual(t["nonce"], n1)
        self.assertEqual(t["terminal"], "term_1")
        self.assertEqual(SB.take_ticket(self.wt, "term_2")["nonce"], n2)

    def test_take_ticket_once(self):
        SB.write_ticket(self.wt, self.ticket(nonce=None, worktree=str(self.wt)))
        SB.take_ticket(self.wt, "term_1")
        with self.assertRaises(SB.SandboxError) as e:
            SB.take_ticket(self.wt, "term_1")
        self.assertIn("no ticket", str(e.exception))

    def test_take_ticket_races_once(self):
        SB.write_ticket(self.wt, self.ticket(nonce=None, worktree=str(self.wt)))
        got, errs = [], []

        def take():
            try:
                got.append(SB.take_ticket(self.wt, "term_x"))
            except SB.SandboxError as e:
                errs.append(e)
        ts = [threading.Thread(target=take) for _ in range(8)]
        for x in ts:
            x.start()
        for x in ts:
            x.join()
        self.assertEqual(len(got), 1)
        self.assertEqual(len(errs), 7)

    def test_take_ticket_other_worktree(self):
        other = self.t / "wt" / "lane-2"
        other.mkdir()
        SB.write_ticket(other, self.ticket(nonce=None, worktree=str(other)))
        with self.assertRaises(SB.SandboxError):
            SB.take_ticket(self.wt, "term_1")
        # a ticket file moved by hand into this worktree's folder still names the other worktree: refused
        src = next(SB.tickets_dir(other).glob("*.json"))
        SB.tickets_dir(self.wt).mkdir(parents=True, exist_ok=True)
        src.rename(SB.tickets_dir(self.wt) / src.name)
        with self.assertRaises(SB.SandboxError) as e:
            SB.take_ticket(self.wt, "term_1")
        self.assertIn("another worktree", str(e.exception))

    def test_take_ticket_malformed(self):
        d = SB.tickets_dir(self.wt)
        d.mkdir(parents=True)
        (d / "0001-bad.json").write_text("{not json")
        with self.assertRaises(SB.SandboxError) as e:
            SB.take_ticket(self.wt, "term_1")
        self.assertIn("malformed", str(e.exception))
        (d / "0002-bad.json").write_text(json.dumps({"nonce": "x"}))
        with self.assertRaises(SB.SandboxError):
            SB.take_ticket(self.wt, "term_1")

    def test_terminal_required(self):
        SB.write_ticket(self.wt, self.ticket(nonce=None, worktree=str(self.wt)))
        with self.assertRaises(SB.SandboxError):
            SB.take_ticket(self.wt, "")

    def test_ticket_file_private(self):
        SB.write_ticket(self.wt, self.ticket(nonce=None, worktree=str(self.wt)))
        f = next(SB.tickets_dir(self.wt).glob("*.json"))
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        self.assertEqual(SB.tickets_dir(self.wt).stat().st_mode & 0o077, 0)


if __name__ == "__main__":
    unittest.main()
