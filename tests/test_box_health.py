"""BOX-1 (field report lc-l78): box health that a listening port does not prove.

- core/boxhealth.py: a tunnel unit's substate, a climbing restart counter, the socket owned by the unit's own ssh; one
  Orca server runtime per user; the user manager's SHELL against passwd; lane-control paths stored in Orca.
- lane-control doctor (and prune's note on a flat install's leftovers), project-setup --check <repo> <host>: the checks,
  read-only.
- lane-tunnel (through a symlink; ControlPath=none) and orca-host-install (--tunnel; stale runtimes after an upgrade).

Everything runs against stubs (systemctl, ssh, Orca's CLI) in a scratch HOME and XDG folders. The "processes" are this
test's own short-lived children, with the argv an Orca server or a tunnel's ssh would have. No real unit, host, tunnel or
Orca is touched, and tests/test-orca-host-install.sh is never run."""
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TUNNEL = ROOT / "core" / "scripts" / "lane-tunnel"
TEMPLATE = ROOT / "core" / "templates" / "lane-tunnel@.service"
HOST_INSTALL = ROOT / "orchestrators" / "orca-cotal" / "scripts" / "orca-host-install"
CLI = ROOT / "core" / "scripts" / "lane-control"


def load():
    spec = importlib.util.spec_from_file_location("lc_boxhealth_t", ROOT / "core" / "boxhealth.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# systemctl --user: logs argv as JSON lines to $SYSTEMCTL_LOG; `show` answers from $SYSTEMCTL_STATE
# ({"units": {name: {props..., "climb": true}}, "shell": "/bin/x"}); --value prints values only.
SYSTEMCTL = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
with open(os.environ["SYSTEMCTL_LOG"], "a") as f:
    f.write(json.dumps(a) + "\n")
sp = os.environ["SYSTEMCTL_STATE"]
st = json.load(open(sp)) if os.path.exists(sp) else {}
if st.get("down") or a[:1] != ["--user"]:
    print("Failed to connect to bus", file=sys.stderr); sys.exit(1)
if a[1:2] == ["show-environment"]:
    print("PATH=/usr/bin")
    if st.get("shell"):
        print("SHELL=" + st["shell"])
    sys.exit(0)
if a[1:2] == ["show"]:
    u = st.setdefault("units", {}).get(a[2])
    props = [a[i + 1] for i, x in enumerate(a) if x == "-p"]
    if u is None:
        u = {"LoadState": "not-found", "ActiveState": "inactive", "SubState": "dead", "NRestarts": 0, "MainPID": 0}
    elif u.get("climb"):
        u["NRestarts"] = u.get("NRestarts", 0) + 1
    json.dump(st, open(sp, "w"))
    print("\n".join(str(u.get(p, "")) if "--value" in a else "%s=%s" % (p, u.get(p, "")) for p in props))
    sys.exit(0)
sys.exit(0)
'''

ORCA = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
with open(os.environ["ORCA_STUB_LOG"], "a") as f:
    f.write(json.dumps(a) + "\n")
st = json.load(open(os.environ["ORCA_STUB_STATE"])) if os.path.exists(os.environ["ORCA_STUB_STATE"]) else {}
def out(r, ok=True):
    print(json.dumps({"ok": ok, "result": r})); sys.exit(0 if ok else 1)
if st.get("down"):
    out({}, False)
if a[:2] == ["automations", "list"]:
    out({"automations": st.get("automations", [])})
if a[:2] == ["environment", "list"]:
    out({"environments": [{"name": n} for n in st.get("environments", [])]})
if a[:1] == ["status"]:
    print(json.dumps({"ok": True, "result": {"runtime": {"reachable": st.get("reachable", True), "state": "ready"}},
                      "reachable": st.get("reachable", True)}, indent=1)); sys.exit(0)
print("orca stub: not modelled: " + " ".join(a), file=sys.stderr); sys.exit(1)
'''

HOLD = """import socket, sys, time
port = int(sys.argv[1])
if port:
    s = socket.socket()
    s.bind(("127.0.0.1", port))
    s.listen()
print("up", flush=True)
time.sleep(120)
"""


class Env(unittest.TestCase):
    """A scratch HOME, XDG folders and a bin of stubs; spawn() starts a child with a given argv (and a listener)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bh-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.bin = self.tmp / "bin"
        for d in (self.home, self.bin):
            d.mkdir()
        self.conf = self.home / ".config"
        self.data = self.tmp / "data"
        for name, body in (("systemctl", SYSTEMCTL), ("orca", ORCA)):
            (self.bin / name).write_text(body)
            (self.bin / name).chmod(0o755)
        (self.bin / "node").symlink_to(sys.executable)  # a helper process whose argv[0] is node
        self.sstate, self.slog = self.tmp / "systemctl.json", self.tmp / "systemctl.log"
        self.ostate, self.olog = self.tmp / "orca.json", self.tmp / "orca.log"
        self.env = {**os.environ, "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.conf),
                    "XDG_DATA_HOME": str(self.data), "PATH": f"{self.bin}:{os.environ['PATH']}",
                    "SYSTEMCTL_STATE": str(self.sstate), "SYSTEMCTL_LOG": str(self.slog),
                    "ORCA_CLI": str(self.bin / "orca"), "ORCA_STUB_STATE": str(self.ostate),
                    "ORCA_STUB_LOG": str(self.olog), "LANE_CHECK_SETTLE": "0", "LANE_TUNNEL_WAIT": "1"}
        for k in ("LANE_SSH_CONFIG", "LANE_CONTROL_SWITCH"):
            self.env.pop(k, None)
        self.procs = []
        self.addCleanup(self.reap)

    def reap(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()
            p.stdout.close()

    def spawn(self, *words, port=0, exe=sys.executable):
        p = subprocess.Popen([exe, "-c", HOLD, str(port), *words], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, text=True)
        self.procs.append(p)
        self.assertEqual(p.stdout.readline().strip(), "up")
        return p.pid

    def units(self, **units):
        st = json.loads(self.sstate.read_text()) if self.sstate.exists() else {}
        st.setdefault("units", {}).update(units)
        self.sstate.write_text(json.dumps(st))

    def running(self, pid, **kw):
        return {"LoadState": "loaded", "ActiveState": "active", "SubState": "running", "NRestarts": 0,
                "MainPID": pid, **kw}

    def slot(self, ver):
        return str(self.home / ".orca-remote" / f"orcad-{ver}" / "orcad.js")

    def calls(self):
        return [json.loads(x) for x in self.slog.read_text().splitlines()] if self.slog.exists() else []


class Module(Env):
    """core/boxhealth.py, called in this process with the stubs on PATH."""

    def setUp(self):
        super().setUp()
        self.old = dict(os.environ)
        os.environ.update({k: self.env[k] for k in ("PATH", "SYSTEMCTL_STATE", "SYSTEMCTL_LOG", "LANE_CHECK_SETTLE")})
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.old)))
        self.B = load()


class UnitHealthTests(Module):
    def test_the_units_own_ssh_owning_the_listener_is_ok(self):
        port = free_port()
        pid = self.spawn("ssh", "-N", "-L", f"127.0.0.1:{port}:127.0.0.1:{port}", "box", port=port)
        self.units(**{"orca-tunnel-box.service": self.running(pid, NRestarts=2)})
        state, detail = self.B.health({"orca-tunnel-box.service": port})["orca-tunnel-box.service"]
        self.assertEqual(state, "ok", detail)
        self.assertIn(f"owns 127.0.0.1:{port}", detail)
        self.assertIn("restarted 2 times", detail)

    def test_a_port_that_answers_from_another_process_is_not_proof(self):
        port = free_port()
        master = self.spawn("ssh", "-M", "box", port=port)  # the user's ControlMaster holds the forward
        unit_ssh = self.spawn("ssh", "-N", "-L", f"127.0.0.1:{port}:127.0.0.1:{port}", "box")
        self.units(**{"orca-tunnel-box.service": self.running(unit_ssh)})
        state, detail = self.B.health({"orca-tunnel-box.service": port})["orca-tunnel-box.service"]
        self.assertEqual(state, "broken")
        self.assertIn(f"held by pid {master}", detail)
        self.assertIn(f"pid {unit_ssh}", detail)
        self.assertIn("ControlMaster", detail)

    def test_a_climbing_restart_counter_is_broken_while_the_port_answers(self):
        port = free_port()
        pid = self.spawn("ssh", port=port)
        self.units(**{"orca-tunnel-box.service": self.running(pid, NRestarts=126, climb=True)})
        state, detail = self.B.health({"orca-tunnel-box.service": port})["orca-tunnel-box.service"]
        self.assertEqual(state, "broken")
        self.assertIn("flaps", detail)
        self.assertIn("128 in all", detail)

    def test_a_unit_waiting_to_restart_is_broken(self):
        self.units(**{"lane-tunnel@box.service": self.running(0, ActiveState="activating", SubState="auto-restart")})
        state, detail = self.B.health({"lane-tunnel@box.service": None})["lane-tunnel@box.service"]
        self.assertEqual(state, "broken")
        self.assertIn("activating/auto-restart", detail)

    def test_a_reverse_tunnel_must_hold_its_own_connection(self):
        own = self.spawn("ssh", "-R", "x", port=free_port())  # holds a TCP socket
        rider = self.spawn("ssh", "-R", "x")  # holds none: it rides a master's connection
        self.units(**{"lane-tunnel@a.service": self.running(own), "lane-tunnel@b.service": self.running(rider)})
        h = self.B.health({"lane-tunnel@a.service": None, "lane-tunnel@b.service": None})
        self.assertEqual(h["lane-tunnel@a.service"][0], "ok", h)
        self.assertEqual(h["lane-tunnel@b.service"][0], "broken")
        self.assertIn("holds no TCP connection", h["lane-tunnel@b.service"][1])

    def test_no_user_manager_is_not_checked_rather_than_a_guess(self):
        self.sstate.write_text(json.dumps({"down": True}))
        self.assertEqual(self.B.health({"lane-tunnel@a.service": None})["lane-tunnel@a.service"][0], "not checked")

    def test_tunnel_units_are_read_from_the_unit_files_and_env_files(self):
        u = self.conf / "systemd" / "user"
        u.mkdir(parents=True)
        (u / "orca-tunnel-box.service").write_text(
            "[Service]\nExecStart=/usr/bin/env ssh  -N -o BatchMode=yes -L 127.0.0.1:6769:127.0.0.1:6769 user@box\n")
        (u / "lane-tunnel@.service").write_text(TEMPLATE.read_text())
        t = self.conf / "lane-control" / "tunnels"
        t.mkdir(parents=True)
        (t / "box.env").write_text("SSH_HOST=user@box\nPORT=6768\nREMOTE_PORT=6790\nSSH_AUTH_SOCK=/run/a.sock\n")
        got = {x["unit"]: x for x in self.B.tunnel_units(self.conf)}
        self.assertEqual(got["orca-tunnel-box.service"]["port"], 6769)
        self.assertEqual(got["orca-tunnel-box.service"]["host"], "user@box")
        self.assertFalse(got["orca-tunnel-box.service"]["controlpath"])  # written before the fix
        self.assertTrue(got["lane-tunnel@box.service"]["controlpath"])
        self.assertEqual(self.B.repair(got["lane-tunnel@box.service"]),
                         "lane-tunnel start --auth-sock /run/a.sock --remote-port 6790 user@box 6768")
        self.assertEqual(self.B.repair(got["orca-tunnel-box.service"]), "orca-host-install user@box --tunnel")


class RuntimeTests(Module):
    def test_one_server_is_ok_and_named(self):
        pid = self.spawn(self.slot("0.2.0+bbbb"), "--json", "--port", "6769")
        state, detail = self.B.runtimes(self.B.orcad(self.home), 6769)
        self.assertEqual(state, "ok", detail)
        self.assertIn(f"orcad-0.2.0+bbbb (pid {pid}, port 6769)", detail)

    def test_two_servers_after_an_upgrade_are_broken_and_each_is_named(self):
        new = self.spawn(self.slot("0.2.0+bbbb"), "--port", "6769")
        old = self.spawn(self.slot("0.1.0+aaaa"), "--port", "6770")
        state, detail = self.B.runtimes(self.B.orcad(self.home))
        self.assertEqual(state, "broken")
        self.assertIn("2 Orca server processes", detail)
        self.assertIn(f"pid {new} (orcad-0.2.0+bbbb port 6769)", detail)
        self.assertIn(f"pid {old} (orcad-0.1.0+aaaa port 6770)", detail)

    def test_a_helper_of_an_older_build_is_stale(self):
        self.spawn(self.slot("0.2.0+bbbb"), "--port", "6769")
        helper = self.spawn(str(self.home / ".orca-remote" / "orcad-0.1.0+aaaa" / "out" / "pty-host.js"),
                            exe=str(self.bin / "node"))
        state, detail = self.B.runtimes(self.B.orcad(self.home))
        self.assertEqual(state, "broken")
        self.assertIn(f"pid {helper} (orcad-0.1.0+aaaa, helper) of an older build", detail)

    def test_a_pager_on_a_slots_log_is_not_part_of_the_server(self):
        self.spawn(str(self.home / ".orca-remote" / "orcad-0.1.0+aaaa" / "orcad.log"))
        self.assertEqual(self.B.orcad(self.home), [])
        self.assertEqual(self.B.runtimes([])[0], "none")

    def test_another_users_home_is_not_ours(self):
        self.spawn("/elsewhere/.orca-remote/orcad-0.1.0/orcad.js", "--port", "6769")
        self.assertEqual(self.B.orcad(self.home), [])


class ShellTests(Module):
    def test_the_managers_shell_and_the_login_shell(self):
        import pwd
        self.sstate.write_text(json.dumps({"shell": "/bin/oldsh"}))
        self.assertEqual(self.B.shells(), ("/bin/oldsh", pwd.getpwuid(os.getuid()).pw_shell))
        self.sstate.write_text(json.dumps({}))
        self.assertIsNone(self.B.shells()[0])
        self.assertIn("systemctl --user set-environment SHELL=/bin/x", self.B.shell_fix("/bin/x"))
        self.assertIn("systemctl --user restart orcad.service", self.B.shell_fix("/bin/x"))


class StalePathTests(Module):
    def test_paths_that_are_not_current_are_named(self):
        root = self.data / "lane-control"
        sha = "a" * 40
        (root / "current" / "core" / "scripts").mkdir(parents=True)
        (root / "current" / "core" / "scripts" / "night-precheck").write_text("")
        co = self.tmp / "code" / "lane-control"
        (co / "core" / "scripts").mkdir(parents=True)
        (co / ".lane-control-root").write_text("")
        (co / "core" / "scripts" / "lane-sweep").write_text("")
        text = (f"'{root}/current/core/scripts/night-precheck' and {root}/{sha}/core/scripts/x and "
                f"{root}/scripts/lane-sweep; {co}/core/scripts/lane-sweep --precheck, ~/gone/lane-control/x. "
                "~/code/lane-control-beads stays")
        got = dict(self.B.stale_paths(text, root, self.home))
        self.assertNotIn(f"{root}/current/core/scripts/night-precheck", got)
        self.assertIn("itself, not current", got[f"{root}/{sha}/core/scripts/x"])
        self.assertIn("flat copy", got[f"{root}/scripts/lane-sweep"])
        self.assertIn("names the checkout", got[f"{co}/core/scripts/lane-sweep"])
        self.assertEqual(got["~/gone/lane-control/x"], "does not exist")
        self.assertEqual(len(got), 4, got)


# --- lane-control doctor and prune ----------------------------------------------------------------------------------

class DoctorTests(Env):
    def setUp(self):
        super().setUp()
        self.sha = "b" * 40
        self.root = self.data / "lane-control"
        tree = self.root / self.sha
        shutil.copytree(ROOT / "core", tree / "core", ignore=shutil.ignore_patterns("__pycache__"))
        (tree / ".lane-control-sha").write_text(self.sha + "\n")
        (self.root / "current").symlink_to(self.sha)
        b = self.home / ".agents" / "bin"
        b.mkdir(parents=True)
        for n in ("lane-limits.sh", "lane-heavy", "lane-room", "lane-tier", "design-shots", "lane-control"):
            (b / n).symlink_to(self.root / "current" / "core" / "scripts" / n)

    def doctor(self, *args):
        r = subprocess.run([sys.executable, str(CLI), "doctor", "--no-ssh", *args], env=self.env, capture_output=True,
                           text=True)
        return r, [line for line in r.stdout.splitlines() if line.startswith("todo: ")]

    def test_a_flapping_tunnel_unit_is_a_todo_with_the_command_that_repairs_it(self):
        u = self.conf / "systemd" / "user"
        u.mkdir(parents=True)
        port = free_port()
        (u / "orca-tunnel-box.service").write_text(
            f"[Service]\nExecStart=/usr/bin/env ssh -N -o ControlPath=none -L 127.0.0.1:{port}:127.0.0.1:{port} box\n")
        pid = self.spawn("ssh", port=port)
        self.units(**{"orca-tunnel-box.service": self.running(pid, climb=True)})
        r, todo = self.doctor()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(any("orca-tunnel-box.service" in t and "flaps" in t and "orca-host-install box --tunnel" in t
                            for t in todo), todo)
        self.assertFalse(any(c[1:2] != ["show"] for c in self.calls()), self.calls())  # reads only

    def test_a_tunnel_unit_written_before_controlpath_none_is_a_todo(self):
        u = self.conf / "systemd" / "user"
        u.mkdir(parents=True)
        port = free_port()
        (u / "orca-tunnel-box.service").write_text(
            f"[Service]\nExecStart=/usr/bin/env ssh -N -L 127.0.0.1:{port}:127.0.0.1:{port} box\n")
        self.units(**{"orca-tunnel-box.service": self.running(self.spawn("ssh", port=port))})
        r, todo = self.doctor()
        self.assertTrue(any("ControlPath=none" in t and "orca-host-install box --tunnel" in t for t in todo), todo)
        d = json.loads(self.doctor("--json")[0].stdout)
        self.assertEqual(d["tunnels"][0]["state"], "ok")

    def test_automations_naming_an_old_or_flat_path_are_named_and_nothing_is_edited(self):
        self.ostate.write_text(json.dumps({"automations": [
            {"id": "a1", "name": "night manager", "prompt": f"Follow {self.root}/current/core/README.md",
             "precheck": {"command": f"'{self.root}/scripts/night-precheck'"}},
            {"id": "a2", "name": "my own", "prompt": f"run {self.root}/{'c' * 40}/core/scripts/lane-sweep",
             "precheck": None},
            {"id": "a3", "name": "fine", "prompt": "nothing", "precheck": {"command": "true"}}]}))
        r, todo = self.doctor()
        self.assertEqual(r.returncode, 1, r.stdout)
        a1 = [t for t in todo if "a1" in t]
        self.assertTrue(a1 and "flat copy" in a1[0] and "machine-setup" in a1[0], todo)
        a2 = [t for t in todo if "a2" in t]
        self.assertTrue(a2 and "itself, not current" in a2[0] and "orca-ide automations edit a2" in a2[0], todo)
        self.assertFalse(any("a3" in t for t in todo))
        calls = [json.loads(x) for x in self.olog.read_text().splitlines()]
        self.assertEqual({tuple(c[:2]) for c in calls}, {("automations", "list")})

    def test_orca_not_answering_is_said_and_is_not_a_todo(self):
        self.ostate.write_text(json.dumps({"down": True}))
        r, todo = self.doctor()
        self.assertIn("automations: not checked", r.stdout)
        self.assertEqual(todo, [])

    def test_a_box_with_two_runtimes_and_an_old_shell_says_so(self):
        u = self.conf / "systemd" / "user"
        u.mkdir(parents=True)
        (u / "orcad.service").write_text("[Service]\nExecStart=/bin/true\n")
        self.sstate.write_text(json.dumps({"shell": "/bin/oldsh"}))
        new = self.spawn(self.slot("0.2.0+bbbb"), "--port", "6769")
        old = self.spawn(self.slot("0.1.0+aaaa"), "--port", "6770")
        r, todo = self.doctor()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(any("2 Orca server processes" in t and str(old) in t and str(new) in t for t in todo), todo)
        self.assertTrue(any("SHELL=/bin/oldsh" in t and "set-environment" in t for t in todo), todo)
        d = json.loads(self.doctor("--json")[0].stdout)
        self.assertEqual(d["runtime"]["state"], "broken")
        self.assertEqual({p["pid"] for p in d["runtime"]["processes"]}, {new, old})

    def test_the_box_doctor_reports_the_serving_pin(self):
        self.spawn(self.slot("0.2.0+bbbb"), "--port", "6769")
        r, todo = self.doctor()
        self.assertEqual(todo, [], r.stdout)
        self.assertIn("orca server: one server: orcad-0.2.0+bbbb", r.stdout)

    def test_a_desktop_with_nothing_of_this_reads_nothing(self):
        r, todo = self.doctor()
        self.assertEqual(todo, [], r.stdout)
        self.assertEqual(self.calls(), [])  # no unit, no orcad.service: systemctl is not asked

    def test_prune_and_install_name_a_flat_installs_leftovers(self):
        for d in ("scripts", "core", "orchestrators"):
            (self.root / d).mkdir()
        (self.root / "README.md").write_text("")
        r = subprocess.run([sys.executable, str(CLI), "prune"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        note = [line for line in r.stdout.splitlines() if line.startswith("note:")]
        self.assertEqual(len(note), 1, r.stdout)
        for n in ("README.md", "core", "orchestrators", "scripts"):
            self.assertIn(n, note[0])
        self.assertIn("prune leaves them", note[0])
        self.assertTrue((self.root / "scripts").is_dir())


# --- lane-tunnel ----------------------------------------------------------------------------------------------------

class LaneTunnelTests(Env):
    def tunnel(self, path, *args):
        return subprocess.run(["sh", str(path), *args], env={**self.env, "LANE_TUNNEL_SETTLE": "0"},
                              capture_output=True, text=True)

    def test_the_template_never_multiplexes(self):
        line = next(x for x in TEMPLATE.read_text().splitlines() if x.startswith("ExecStart="))
        self.assertIn("-o ControlPath=none", line)
        self.assertIn("-o ControlMaster=no", line)

    def test_it_runs_through_a_symlink_as_the_agents_bin_link_does(self):
        link = self.tmp / "agents-bin" / "lane-tunnel"
        link.parent.mkdir()
        link.symlink_to(TUNNEL)
        self.units(**{"lane-tunnel@box.service": self.running(1)})
        r = subprocess.run([str(link), "start", "box", "6768"], env={**self.env, "LANE_TUNNEL_SETTLE": "0"},
                           capture_output=True, text=True)
        self.assertNotIn("can't read", r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ControlPath=none", (self.conf / "systemd" / "user" / "lane-tunnel@.service").read_text())

    def test_a_tree_without_the_template_is_refused_naming_the_path(self):
        copy = self.tmp / "tree" / "core" / "scripts"
        copy.mkdir(parents=True)
        shutil.copy(TUNNEL, copy / "lane-tunnel")
        r = self.tunnel(copy / "lane-tunnel", "start", "box", "6768")
        self.assertEqual(r.returncode, 1)
        self.assertIn(str(self.tmp / "tree" / "core" / "templates" / "lane-tunnel@.service"), r.stderr)
        self.assertFalse((self.conf / "systemd").exists())

    def test_a_changed_template_restarts_a_running_instance(self):
        self.units(**{"lane-tunnel@box.service": self.running(1)})
        u = self.conf / "systemd" / "user"
        u.mkdir(parents=True)
        (u / "lane-tunnel@.service").write_text("[Service]\nExecStart=/bin/sh -c 'exec ssh -N box'\n")  # before
        t = self.conf / "lane-control" / "tunnels"
        t.mkdir(parents=True)
        (t / "box.env").write_text(f"SSH_HOST=box\nPORT=6768\nSSH_CONFIG=\nPATH={self.env['PATH']}\n")
        r = self.tunnel(TUNNEL, "start", "box", "6768")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(["--user", "restart", "lane-tunnel@box.service"], self.calls())
        self.calls_before = len(self.calls())
        r = self.tunnel(TUNNEL, "start", "box", "6768")  # the same again: no restart
        self.assertEqual([c for c in self.calls()[self.calls_before:] if c[1:2] == ["restart"]], [])


# --- orca-host-install ----------------------------------------------------------------------------------------------

FAKE_SSH = """#!/bin/sh
# runs the host's command in the box's HOME, stdin passed through; options and the host name are dropped
while [ $# -gt 0 ]; do
  case $1 in -F|-o) shift 2 ;; -*) shift ;; *) break ;; esac
done
shift
echo "$*" >> "$BOX.ssh-log"
HOME=$BOX exec sh -c "$*"
"""


class HostInstallTests(Env):
    """orca-host-install --pair and --tunnel with the box on this machine: its 'Orca server' is a child of this test
    with the argv orcad has, and the desktop's tunnel unit is the systemctl stub's."""

    def setUp(self):
        super().setUp()
        self.box = self.tmp / "box"
        self.box.mkdir()
        (self.bin / "ssh").write_text(FAKE_SSH)
        (self.bin / "ssh").chmod(0o755)
        self.env["BOX"] = str(self.box)
        self.port = free_port()
        self.ostate.write_text(json.dumps({"environments": ["box"]}))

    def run_tool(self, *args):
        return subprocess.run(["sh", str(HOST_INSTALL), "box", *args], env=self.env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)

    def box_slot(self, ver):
        return str(self.box / ".orca-remote" / f"orcad-{ver}" / "orcad.js")

    def unit(self):
        return self.conf / "systemd" / "user" / "orca-tunnel-box.service"

    def test_tunnel_restores_only_the_direction_one_unit(self):
        server = self.spawn(self.box_slot("0.2.0+bbbb"), "--json", "--port", str(self.port), port=self.port)
        self.units(**{"orca-tunnel-box.service": self.running(server)})  # the unit's ssh holds the port
        r = self.run_tool("--tunnel", "--port", str(self.port))
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.unit().read_text()
        self.assertIn("-o ControlPath=none", text)
        self.assertIn("-o ControlMaster=no", text)
        self.assertIn(f"-L 127.0.0.1:{self.port}:127.0.0.1:{self.port} box", text)
        self.assertIn("tunnel orca-tunnel-box.service", r.stdout)
        self.assertIn("already-paired box", r.stdout)
        self.assertFalse((self.box / ".orca-remote").exists() and any((self.box / ".orca-remote").iterdir()))
        orca = [json.loads(x) for x in self.olog.read_text().splitlines()]
        self.assertFalse(any(c[:2] in (["environment", "rm"], ["environment", "add"]) for c in orca), orca)
        ssh = (self.box.parent / "box.ssh-log").read_text() if (self.box.parent / "box.ssh-log").exists() else ""
        self.assertNotIn("tar ", ssh)  # nothing installed, no server started

    def test_tunnel_reads_the_port_from_the_unit_it_wrote_before(self):
        server = self.spawn(self.box_slot("0.2.0+bbbb"), "--port", str(self.port), port=self.port)
        self.unit().parent.mkdir(parents=True)
        self.unit().write_text(f"[Service]\nExecStart=/usr/bin/env ssh -N -L 127.0.0.1:{self.port}:127.0.0.1:"
                               f"{self.port} box\n")
        self.units(**{"orca-tunnel-box.service": self.running(server)})
        r = self.run_tool("--tunnel")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ControlPath=none", self.unit().read_text())
        self.assertIn(["--user", "restart", "orca-tunnel-box.service"], self.calls())  # the old unit was running

    def test_tunnel_whose_port_is_held_by_another_process_fails_and_names_it(self):
        holder = self.spawn(self.box_slot("0.2.0+bbbb"), "--port", str(self.port), port=self.port)
        unit_ssh = self.spawn("ssh", "-N")
        self.units(**{"orca-tunnel-box.service": self.running(unit_ssh)})
        r = self.run_tool("--tunnel", "--port", str(self.port))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(f"pid {holder}", r.stderr)
        self.assertIn(f"not by orca-tunnel-box.service (pid {unit_ssh})", r.stderr)

    def test_tunnel_without_a_port_or_a_unit_asks_for_one(self):
        r = self.run_tool("--tunnel")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--port", r.stderr)

    def test_tunnel_for_a_host_never_paired_points_at_pair(self):
        self.ostate.write_text(json.dumps({"environments": []}))
        server = self.spawn(self.box_slot("0.2.0+bbbb"), "--port", str(self.port), port=self.port)
        self.units(**{"orca-tunnel-box.service": self.running(server)})
        r = self.run_tool("--tunnel", "--port", str(self.port))
        self.assertEqual(r.returncode, 1)
        self.assertIn("--pair", r.stderr)

    def test_the_audit_names_stale_runtimes_and_stops_them_but_not_a_second_current_one(self):
        audit = subprocess.run(["sh", str(HOST_INSTALL), "--audit-script"], env=self.env, capture_output=True,
                               text=True)
        self.assertEqual(audit.returncode, 0, audit.stderr)
        cur = self.spawn(self.box_slot("0.2.0+bbbb"), "--port", str(self.port), port=self.port)
        stale = self.spawn(self.box_slot("0.1.0+aaaa"), "--port", "6799")
        helper = self.spawn(str(self.box / ".orca-remote" / "orcad-0.1.0+aaaa" / "out" / "pty.js"),
                            exe=str(self.bin / "node"))
        env = {**self.env, "HOME": str(self.box)}
        r = subprocess.run(["sh", "-s", "--", "0.2.0+bbbb", str(self.port), "stop"], input=audit.stdout, env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"stopped stale orcad-0.1.0+aaaa pid {stale}", r.stdout)
        self.assertIn(f"stopped stale orcad-0.1.0+aaaa pid {helper}", r.stdout)
        for p in self.procs:
            if p.pid in (stale, helper):
                self.assertIsNotNone(p.wait(timeout=10))
        self.assertIsNone(next(p for p in self.procs if p.pid == cur).poll())
        r = subprocess.run(["sh", "-s", "--", "0.2.0+bbbb", str(self.port), "prove"], input=audit.stdout, env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"one runtime orcad-0.2.0+bbbb pid {cur} on 127.0.0.1:{self.port}", r.stdout)
        other = self.spawn(self.box_slot("0.2.0+bbbb"), "--port", "6798")  # another server of the same build
        r = subprocess.run(["sh", "-s", "--", "0.2.0+bbbb", str(self.port), "stop"], input=audit.stdout, env=env,
                           capture_output=True, text=True)
        self.assertNotIn(str(other), r.stdout)  # not stale: left alone
        r = subprocess.run(["sh", "-s", "--", "0.2.0+bbbb", str(self.port), "prove"], input=audit.stdout, env=env,
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(f"pid {other}", r.stdout + r.stderr)
        self.assertIsNone(next(p for p in self.procs if p.pid == other).poll())

    def test_prove_fails_when_the_server_does_not_own_the_port(self):
        audit = subprocess.run(["sh", str(HOST_INSTALL), "--audit-script"], env=self.env, capture_output=True,
                               text=True).stdout
        self.spawn(self.box_slot("0.2.0+bbbb"), "--port", str(self.port))  # says the port, listens on none
        holder = self.spawn("something-else", port=self.port)
        r = subprocess.run(["sh", "-s", "--", "0.2.0+bbbb", str(self.port), "prove"], input=audit,
                           env={**self.env, "HOME": str(self.box)}, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("does not own 127.0.0.1:" + str(self.port), r.stdout + r.stderr)
        del holder

    def test_the_unit_it_writes_for_pair_never_multiplexes(self):
        text = HOST_INSTALL.read_text()
        start = next(x for x in text.splitlines() if x.startswith("ExecStart=/usr/bin/env ssh"))
        self.assertIn("-o ControlPath=none", start)
        self.assertIn("-o ControlMaster=no", start)

    def test_the_shell_step_puts_the_login_shell_into_the_user_manager(self):
        import pwd
        login = pwd.getpwuid(os.getuid()).pw_shell
        audit = subprocess.run(["sh", str(HOST_INSTALL), "--audit-script"], env=self.env, capture_output=True,
                               text=True).stdout
        self.sstate.write_text(json.dumps({"shell": "/bin/oldsh"}))
        r = subprocess.run(["sh", "-s", "--", "x", "0", "shell"], input=audit, env={**self.env, "HOME": str(self.box)},
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(["--user", "set-environment", f"SHELL={login}"], self.calls())
        self.assertIn(f"SHELL {login} (was /bin/oldsh)", r.stdout)
        n = len(self.calls())
        self.sstate.write_text(json.dumps({"shell": login}))
        r = subprocess.run(["sh", "-s", "--", "x", "0", "shell"], input=audit, env={**self.env, "HOME": str(self.box)},
                           capture_output=True, text=True)
        self.assertEqual(r.stdout, "")
        self.assertFalse(any(c[1:2] == ["set-environment"] for c in self.calls()[n:]))
        self.assertIn("systemctl --user restart orcad.service", HOST_INSTALL.read_text())  # said for a running one

# --- project-setup --check <repo> <host> ----------------------------------------------------------------------------

class CheckTests(unittest.TestCase):
    """The rows --check adds, with test_remote_native's box (a second HOME behind a fake ssh)."""

    def setUp(self):
        from test_remote_native import RemoteEnv
        self.e = RemoteEnv()
        self.addCleanup(self.e.close)
        self.repo = self.e.box_repo()
        self.e.env["LANE_DESKTOP_ORCA_PORT"] = str(free_port())
        self.procs = []
        self.addCleanup(self.reap)

    reap = Env.reap

    def spawn(self, *words, port=0):
        return Env.spawn(self, *words, port=port)

    def slot(self, ver):
        return str(self.e.box / ".orca-remote" / f"orcad-{ver}" / "orcad.js")

    def check(self, *args, env_extra=None):
        from test_remote_native import SETUP
        r = subprocess.run([sys.executable, str(SETUP), "--check", str(self.repo), "box", *args],
                           env={**self.e.env, **(env_extra or {})}, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        return r, {line.split(":", 1)[0][len("check "):]: line.split(":", 1)[1].strip()
                   for line in r.stdout.splitlines() if line.startswith("check ") and ":" in line}

    def pair(self):
        from test_remote_native import make_link
        self.e.saved("box")
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=make_link(free_port())[0])
        self.assertEqual(rc, 0, out)

    def test_two_servers_on_the_box_fail_the_check_and_each_is_named(self):
        self.e.saved("box")
        new = self.spawn(self.slot("0.2.0+bbbb"), "--port", "6769")
        old = self.spawn(self.slot("0.1.0+aaaa"), "--port", "6770")
        r, rows = self.check()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(rows["runtimes on box"].startswith("missing"), rows)
        for pid in (new, old):
            self.assertIn(f"pid {pid}", rows["runtimes on box"])
        self.assertIn("orca-host-install box --pair", rows["runtimes on box"])

    def test_one_server_names_the_pin_and_a_different_pin_fails(self):
        self.e.saved("box")
        d = self.e.box / ".orca-remote" / "orcad-0.2.0+bbbb" / "out"
        d.mkdir(parents=True)
        (d / "package.json").write_text('{"version": "1.4.274"}')
        self.spawn(self.slot("0.2.0+bbbb"), "--port", "6769")
        r, rows = self.check()
        self.assertTrue(rows["runtimes on box"].startswith("ok"), rows)
        self.assertIn("orcad-0.2.0+bbbb", rows["runtimes on box"])
        self.assertIn("Orca 1.4.274", rows["runtimes on box"])
        r, rows = self.check("--orca-version", "1.4.274")
        self.assertTrue(rows["runtimes on box"].startswith("ok"), rows)
        r, rows = self.check("--orca-version", "1.4.280")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--pair --version 1.4.280", rows["runtimes on box"])

    def test_a_shell_the_user_manager_did_not_get_fails_where_orcad_runs_under_it(self):
        import pwd
        login = pwd.getpwuid(os.getuid()).pw_shell
        env = {"STUB_MANAGER_SHELL": "/bin/oldsh"}
        r, rows = self.check(env_extra=env)
        self.assertTrue(rows["shell on box"].startswith("to do"), rows)  # orca-host-install sets it before a start
        units = self.e.box / ".config" / "systemd" / "user"
        units.mkdir(parents=True)
        (units / "orcad.service").write_text("[Service]\n")
        r, rows = self.check(env_extra=env)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(rows["shell on box"].startswith("missing"), rows)
        self.assertIn(f"systemctl --user set-environment SHELL={login}", rows["shell on box"])
        r, rows = self.check(env_extra={"STUB_MANAGER_SHELL": login})
        self.assertTrue(rows["shell on box"].startswith("ok"), rows)

    def test_a_climbing_restart_counter_fails_the_tunnel_while_its_port_answers(self):
        self.pair()
        r, rows = self.check()
        self.assertTrue(rows["tunnel"].startswith("ok"), rows)
        self.assertIn("holds its own connection", rows["tunnel"])
        r, rows = self.check(env_extra={"STUB_TUNNEL_CLIMBS": "1", "STUB_NRESTARTS": "126"})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("flaps", rows["tunnel"])
        self.assertIn("lane-tunnel start", rows["tunnel"])

    def test_a_tunnel_template_from_before_controlpath_none_fails(self):
        self.pair()
        t = self.e.home / ".config" / "systemd" / "user" / "lane-tunnel@.service"
        t.write_text(t.read_text().replace(" -o ControlPath=none -o ControlMaster=no", ""))
        r, rows = self.check()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("ControlPath=none", rows["tunnel"])

    def test_the_direction_one_unit_must_own_its_port(self):
        self.e.saved("box")
        port = free_port()
        u = self.e.home / ".config" / "systemd" / "user"
        u.mkdir(parents=True, exist_ok=True)
        (u / "orca-tunnel-box.service").write_text(
            "[Service]\nExecStart=/usr/bin/env ssh -N -o BatchMode=yes -o ControlPath=none -o ControlMaster=no "
            f"-L 127.0.0.1:{port}:127.0.0.1:{port} box\n")
        own = self.spawn("ssh", port=port)
        r, rows = self.check(env_extra={"STUB_ORCA_TUNNEL_PID": str(own)})
        self.assertTrue(rows["direction 1 tunnel"].startswith("ok"), rows)
        rider = self.spawn("ssh", "-N")
        r, rows = self.check(env_extra={"STUB_ORCA_TUNNEL_PID": str(rider)})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(f"held by pid {own}", rows["direction 1 tunnel"])
        self.assertIn("orca-host-install box --tunnel", rows["direction 1 tunnel"])


if __name__ == "__main__":
    unittest.main()
