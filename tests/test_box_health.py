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
if a[1:2] == ["is-enabled"]:
    word = st.get("enabled", {}).get(a[2])
    if word:
        print(word)
    sys.exit(0 if word == "enabled" else 1)
if a[1:2] == ["show"]:
    u = st.setdefault("units", {}).get(a[2])
    props = [a[i + 1] for i, x in enumerate(a) if x == "-p"]
    if u is None:
        u = {"LoadState": "not-found", "ActiveState": "inactive", "SubState": "dead", "NRestarts": 0, "MainPID": 0}
    elif u.get("climb"):
        u["NRestarts"] = u.get("NRestarts", 0) + 1
    json.dump(st, open(sp, "w"))
    # systemd prints in its own order, not the -p order (review B1): Unit's properties, then Service's, MainPID first
    order = ["Id", "LoadState", "ActiveState", "SubState", "UnitFileState", "MainPID", "NRestarts"]
    props = sorted(props, key=lambda p: order.index(p) if p in order else len(order))
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

HOLD = """import os, socket, subprocess, sys, time
port = int(sys.argv[1])
if os.environ.get("HOLD_CHILD"):  # a live terminal or agent under it
    subprocess.Popen(["sleep", "120"], stdin=subprocess.DEVNULL)
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
                try:
                    os.killpg(p.pid, 9)  # with any child it started
                except OSError:
                    p.kill()
            p.wait()
            p.stdout.close()

    @property
    def node(self):
        return str(self.bin / "node")

    def spawn_node(self, *words, port=0, child=False, shell=None):
        """A process whose argv[0] is node, as Orca's server and its helpers run (review m2)."""
        return self.spawn(*words, port=port, exe=self.node, child=child, shell=shell)

    def spawn(self, *words, port=0, exe=sys.executable, child=False, shell=None):
        env = {**os.environ, **({"HOLD_CHILD": "1"} if child else {}), **({"SHELL": shell} if shell else {})}
        p = subprocess.Popen([exe, "-c", HOLD, str(port), *words], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, text=True, env=env, start_new_session=True)
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
            "[Service]\nExecStart=/usr/bin/env ssh -F /trial/ssh_config -N -o BatchMode=yes -L "
            "127.0.0.1:6769:127.0.0.1:6769 user@box\n")
        (u / "lane-tunnel@.service").write_text(TEMPLATE.read_text())
        t = self.conf / "lane-control" / "tunnels"
        t.mkdir(parents=True)
        (t / "box.env").write_text("SSH_HOST=user@box\nPORT=6768\nREMOTE_PORT=6790\nSSH_AUTH_SOCK=/run/a.sock\n"
                                   "SSH_CONFIG=/trial/ssh_config\n")
        self.sstate.write_text(json.dumps({"enabled": {"lane-tunnel@box.service": "disabled"}}))
        got = {x["unit"]: x for x in self.B.tunnel_units(self.conf)}
        self.assertEqual(got["orca-tunnel-box.service"]["port"], 6769)
        self.assertEqual(got["orca-tunnel-box.service"]["host"], "user@box")
        self.assertFalse(got["orca-tunnel-box.service"]["controlpath"])  # written before the fix
        self.assertTrue(got["lane-tunnel@box.service"]["controlpath"])
        # review M1, m3: the repair keeps the port, the ssh config and a tunnel started with --no-enable
        self.assertEqual(self.B.repair(got["lane-tunnel@box.service"]),
                         "LANE_SSH_CONFIG=/trial/ssh_config lane-tunnel start --no-enable --auth-sock /run/a.sock "
                         "--remote-port 6790 user@box 6768")
        self.assertEqual(self.B.repair(got["orca-tunnel-box.service"]),
                         "LANE_SSH_CONFIG=/trial/ssh_config orca-host-install user@box --tunnel --port 6769")


class RuntimeTests(Module):
    def test_one_server_is_ok_and_named(self):
        pid = self.spawn_node(self.slot("0.2.0+bbbb"), "--json", "--port", "6769")
        state, detail = self.B.runtimes(self.B.orcad(self.home), 6769)
        self.assertEqual(state, "ok", detail)
        self.assertIn(f"orcad-0.2.0+bbbb (pid {pid}, port 6769)", detail)

    def test_two_servers_after_an_upgrade_are_broken_and_each_is_named(self):
        new = self.spawn_node(self.slot("0.2.0+bbbb"), "--port", "6769")
        old = self.spawn_node(self.slot("0.1.0+aaaa"), "--port", "6770")
        state, detail = self.B.runtimes(self.B.orcad(self.home))
        self.assertEqual(state, "broken")
        self.assertIn("2 Orca server processes", detail)
        self.assertIn(f"pid {new} (orcad-0.2.0+bbbb port 6769)", detail)
        self.assertIn(f"pid {old} (orcad-0.1.0+aaaa port 6770)", detail)

    def test_a_helper_of_an_older_build_is_stale(self):
        self.spawn_node(self.slot("0.2.0+bbbb"), "--port", "6769")
        helper = self.spawn(str(self.home / ".orca-remote" / "orcad-0.1.0+aaaa" / "out" / "pty-host.js"),
                            exe=str(self.bin / "node"))
        state, detail = self.B.runtimes(self.B.orcad(self.home))
        self.assertEqual(state, "broken")
        self.assertIn(f"pid {helper} (orcad-0.1.0+aaaa, helper) of an older build", detail)

    def test_a_pager_on_a_slots_log_is_not_part_of_the_server(self):
        self.spawn(str(self.home / ".orca-remote" / "orcad-0.1.0+aaaa" / "orcad.log"))
        self.spawn(self.slot("0.1.0+aaaa"), "--port", "6769")  # an editor on orcad.js: not node, not a server (m2)
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
        text += (f" https://github.com/someone/lane-control/pulls docs/lane-control/notes.md and the project {co} "
                 f"or {co}/")  # review m4: a URL, a relative path and a checkout named as a project are not flagged
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
        pid = self.spawn("ssh", "-o", "ControlPath=none", port=port)
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
        self.assertFalse(any("(a3)" in t for t in todo))
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
        new = self.spawn_node(self.slot("0.2.0+bbbb"), "--port", "6769")
        old = self.spawn_node(self.slot("0.1.0+aaaa"), "--port", "6770")
        r, todo = self.doctor()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(any("2 Orca server processes" in t and str(old) in t and str(new) in t for t in todo), todo)
        self.assertTrue(any("SHELL=/bin/oldsh" in t and "set-environment" in t for t in todo), todo)
        d = json.loads(self.doctor("--json")[0].stdout)
        self.assertEqual(d["runtime"]["state"], "broken")
        self.assertEqual({p["pid"] for p in d["runtime"]["processes"]}, {new, old})

    def test_a_server_that_kept_the_old_shell_needs_a_restart_though_the_manager_has_the_new_one(self):
        import pwd
        login = pwd.getpwuid(os.getuid()).pw_shell
        u = self.conf / "systemd" / "user"
        u.mkdir(parents=True)
        (u / "orcad.service").write_text("[Service]\nExecStart=/bin/true\n")
        self.sstate.write_text(json.dumps({"shell": login}))
        self.spawn_node(self.slot("0.2.0+bbbb"), "--port", "6769", shell="/bin/oldsh")
        r, todo = self.doctor()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(any("restart needed" in t and "SHELL=/bin/oldsh" in t and "restart orcad.service" in t
                            for t in todo), todo)

    def test_the_box_doctor_reports_the_serving_pin(self):
        self.spawn_node(self.slot("0.2.0+bbbb"), "--port", "6769")
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

    def instance(self, name, controlpath=True):
        """A running lane-tunnel@<name> instance with its env file; its ssh has ControlPath=none in argv or not."""
        pid = self.spawn("ssh", *(["-o", "ControlPath=none"] if controlpath else []), "-R", "x", name)
        self.units(**{f"lane-tunnel@{name}.service": self.running(pid)})
        t = self.conf / "lane-control" / "tunnels"
        t.mkdir(parents=True, exist_ok=True)
        (t / f"{name}.env").write_text(f"SSH_HOST={name}\nPORT=6768\nSSH_CONFIG=\nPATH={self.env['PATH']}\n")
        return pid

    def restarts(self, since=0):
        return [c[2] for c in self.calls()[since:] if c[1:2] == ["restart"]]

    def test_a_changed_template_restarts_every_running_instance_and_says_so(self):
        u = self.conf / "systemd" / "user"
        u.mkdir(parents=True)
        (u / "lane-tunnel@.service").write_text("[Service]\nExecStart=/bin/sh -c 'exec ssh -N box'\n")  # before
        self.instance("a", controlpath=False)
        self.instance("b", controlpath=False)
        r = self.tunnel(TUNNEL, "start", "a", "6768")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(self.restarts()), ["lane-tunnel@a.service", "lane-tunnel@b.service"])  # review M4
        self.assertIn("restarted lane-tunnel@b.service: its ssh now runs with ControlPath=none", r.stdout)

    def test_an_instance_whose_running_ssh_is_from_before_is_restarted_by_the_next_start(self):
        self.instance("a")
        self.assertEqual(self.tunnel(TUNNEL, "start", "a", "6768").returncode, 0)  # the template is now lane-tunnel's
        self.instance("b", controlpath=False)  # its ssh started before: only that is old
        n = len(self.calls())
        r = self.tunnel(TUNNEL, "start", "a", "6768")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.restarts(n), ["lane-tunnel@b.service"])
        self.assertIn("restarted lane-tunnel@b.service", r.stdout)

    def test_doctor_judges_each_running_instance_not_the_template(self):
        u = self.conf / "systemd" / "user"
        u.mkdir(parents=True)
        (u / "lane-tunnel@.service").write_text(TEMPLATE.read_text())
        self.instance("a")
        self.instance("b", controlpath=False)
        B = load()
        old = dict(os.environ)
        os.environ.update({k: self.env[k] for k in ("PATH", "SYSTEMCTL_STATE", "SYSTEMCTL_LOG")})
        try:
            got = {t["unit"]: t["controlpath"] for t in B.tunnel_units(self.conf)}
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(got, {"lane-tunnel@a.service": True, "lane-tunnel@b.service": False})


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
        server = self.spawn_node(self.box_slot("0.2.0+bbbb"), "--json", "--port", str(self.port), port=self.port)
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

    def test_the_unit_state_is_read_by_name_in_systemds_own_order(self):
        # review B1: real systemd prints MainPID before NRestarts whatever -p says; a fresh tunnel has 0 restarts
        server = self.spawn_node(self.box_slot("0.2.0+bbbb"), "--port", str(self.port), port=self.port)
        self.units(**{"orca-tunnel-box.service": self.running(server, NRestarts=0)})
        out = subprocess.run(["systemctl", "--user", "show", "orca-tunnel-box.service", "-p", "ActiveState", "-p",
                              "SubState", "-p", "NRestarts", "-p", "MainPID"], env=self.env, capture_output=True,
                             text=True).stdout.splitlines()
        self.assertEqual([x.split("=")[0] for x in out], ["ActiveState", "SubState", "MainPID", "NRestarts"])
        r = self.run_tool("--tunnel", "--port", str(self.port))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"its ssh (pid {server}) owns 127.0.0.1:{self.port}, restarted 0 times", r.stdout)

    def test_tunnel_reads_the_port_from_the_unit_it_wrote_before(self):
        server = self.spawn_node(self.box_slot("0.2.0+bbbb"), "--port", str(self.port), port=self.port)
        self.unit().parent.mkdir(parents=True)
        self.unit().write_text(f"[Service]\nExecStart=/usr/bin/env ssh -N -L 127.0.0.1:{self.port}:127.0.0.1:"
                               f"{self.port} box\n")
        self.units(**{"orca-tunnel-box.service": self.running(server)})
        r = self.run_tool("--tunnel")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ControlPath=none", self.unit().read_text())
        self.assertIn(["--user", "restart", "orca-tunnel-box.service"], self.calls())  # the old unit was running

    def test_tunnel_keeps_the_ssh_config_the_unit_was_written_with(self):
        server = self.spawn_node(self.box_slot("0.2.0+bbbb"), "--port", str(self.port), port=self.port)
        cfg = self.tmp / "trial_ssh_config"
        cfg.write_text("")
        self.unit().parent.mkdir(parents=True)
        self.unit().write_text(f"[Service]\nExecStart=/usr/bin/env ssh -F {cfg} -N -L 127.0.0.1:{self.port}:127.0.0.1:"
                               f"{self.port} box\n")
        self.units(**{"orca-tunnel-box.service": self.running(server)})
        r = self.run_tool("--tunnel")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"ssh -F {cfg} -N", self.unit().read_text())  # review m3

    def test_tunnel_whose_port_is_held_by_another_process_fails_and_names_it(self):
        holder = self.spawn_node(self.box_slot("0.2.0+bbbb"), "--port", str(self.port), port=self.port)
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
        server = self.spawn_node(self.box_slot("0.2.0+bbbb"), "--port", str(self.port), port=self.port)
        self.units(**{"orca-tunnel-box.service": self.running(server)})
        r = self.run_tool("--tunnel", "--port", str(self.port))
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"--pair --port {self.port}", r.stderr)  # review M1: never the default port

    def audit(self, *args):
        script = subprocess.run(["sh", str(HOST_INSTALL), "--audit-script"], env=self.env, capture_output=True,
                                text=True).stdout
        return subprocess.run(["sh", "-s", "--", *args], input=script, env={**self.env, "HOME": str(self.box)},
                              capture_output=True, text=True)

    def rows(self):
        return {int(x.split()[0]): x.split()[1:] for x in self.audit("0.2.0+bbbb", str(self.port), "list").stdout
                .splitlines()}

    def alive(self, pid):
        return next(p for p in self.procs if p.pid == pid).poll() is None

    def test_list_names_each_process_with_its_children_and_whether_a_server_adopted_it(self):
        cur = self.spawn_node(self.box_slot("0.2.0+bbbb"), "--port", str(self.port), port=self.port)
        idle = self.spawn_node(self.box_slot("0.1.0+aaaa"), "--port", "6799")
        busy = self.spawn_node(str(self.box / ".orca-remote" / "orcad-0.1.0+aaaa" / "daemon-entry.js"), child=True)
        adopted = self.spawn_node(str(self.box / ".orca-remote" / "orcad-0.1.0+aaaa" / "daemon-entry.js"))
        (self.box / ".orca").mkdir()
        (self.box / ".orca" / "daemon-v3.pid").write_text(f"{adopted}\n")
        self.spawn(self.box_slot("0.1.0+aaaa"))  # not node: an editor on the file (m2)
        rows = self.rows()
        self.assertEqual(rows[cur], ["0.2.0+bbbb", "server", str(self.port), "0", "no", "no"])
        self.assertEqual(rows[idle], ["0.1.0+aaaa", "server", "6799", "0", "no", "no"])
        self.assertEqual(rows[busy][:2] + rows[busy][3:], ["0.1.0+aaaa", "helper", "1", "no", "no"])
        self.assertEqual(rows[adopted][4], "yes")
        self.assertEqual(len(rows), 4)

    def test_stop_stops_only_what_is_stale_and_unused(self):
        cur = self.spawn_node(self.box_slot("0.2.0+bbbb"), "--port", str(self.port), port=self.port)
        idle = self.spawn_node(self.box_slot("0.1.0+aaaa"), "--port", "6799")
        busy = self.spawn_node(str(self.box / ".orca-remote" / "orcad-0.1.0+aaaa" / "daemon-entry.js"), child=True)
        adopted = self.spawn_node(str(self.box / ".orca-remote" / "orcad-0.1.0+aaaa" / "daemon-entry.js"))
        (self.box / ".orca").mkdir()
        (self.box / ".orca" / "daemon-v3.pid").write_text(f"{adopted}\n")
        r = self.audit("0.2.0+bbbb", str(self.port), "stop", str(idle), str(busy), str(adopted), str(cur))
        self.assertEqual(r.returncode, 1, r.stdout)  # some were left
        self.assertIn(f"stopped orcad-0.1.0+aaaa pid {idle}", r.stdout)
        self.assertIn(f"left orcad-0.1.0+aaaa pid {busy}: in use now (1 child processes", r.stdout)
        self.assertIn(f"left orcad-0.1.0+aaaa pid {adopted}: in use now (0 child processes, adopted yes)", r.stdout)
        self.assertIn(f"left orcad-0.2.0+bbbb pid {cur}", r.stdout)  # the current slot is never stopped
        next(p for p in self.procs if p.pid == idle).wait(timeout=10)
        for pid in (busy, adopted, cur):
            self.assertTrue(self.alive(pid), pid)

    def test_prove_wants_one_server_of_this_slot_and_names_one_left_of_another(self):
        cur = self.spawn_node(self.box_slot("0.2.0+bbbb"), "--port", str(self.port), port=self.port)
        old = self.spawn_node(self.box_slot("0.1.0+aaaa"), "--port", "6799", child=True)
        r = self.audit("0.2.0+bbbb", str(self.port), "prove")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"one runtime orcad-0.2.0+bbbb pid {cur} on 127.0.0.1:{self.port}", r.stdout)
        self.assertIn(f"note: left orcad-0.1.0+aaaa pid {old}", r.stdout)
        other = self.spawn_node(self.box_slot("0.2.0+bbbb"), "--port", "6798")
        r = self.audit("0.2.0+bbbb", str(self.port), "prove")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(f"pid {other}", r.stdout)

    def test_prove_fails_when_the_server_does_not_own_the_port(self):
        audit = subprocess.run(["sh", str(HOST_INSTALL), "--audit-script"], env=self.env, capture_output=True,
                               text=True).stdout
        self.spawn_node(self.box_slot("0.2.0+bbbb"), "--port", str(self.port))  # says the port, listens on none
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
        server = self.spawn_node(self.box_slot("0.2.0+bbbb"), "--port", "6769", shell="/bin/oldsh")
        for _ in range(2):  # review M3: said on every run while the running server has the old one
            r = subprocess.run(["sh", "-s", "--", "x", "0", "shell"], input=audit, capture_output=True, text=True,
                               env={**self.env, "HOME": str(self.box)})
            self.assertIn(f"restart needed: Orca's server (pid {server}) runs with SHELL=/bin/oldsh, the login shell "
                          f"is {login}", r.stdout)
            self.assertIn("systemctl --user restart orcad.service", r.stdout)

class PairTests(HostInstallTests):
    """orca-host-install --pair end to end, from a fake slot, against a box whose 'servers' are this test's children
    (review B2, M1, M2, m1): everything of an older slot is named before anything stops; only what is unused stops, and
    only with --stop-stale or a yes; a running server is never replaced or doubled without being asked."""

    def setUp(self):
        super().setUp()
        from test_orca_host_install import arch_of, make_node_archive, make_slot, target
        self.art = self.tmp / "artifacts"
        make_node_archive(self.art, arch_of(target()))
        make_slot(self.art / target(), "0.2.0+bbbb")
        self.env["ORCA_ARTIFACTS"] = str(self.art)

    def pair(self, *args):
        return self.run_tool("--pair", "--port", str(self.port), *args)

    def test_an_older_builds_processes_are_named_first_and_only_the_unused_stop_and_only_when_asked(self):
        cur = self.spawn_node(self.box_slot("0.2.0+bbbb"), "--port", str(self.port), port=self.port)
        idle = self.spawn_node(self.box_slot("0.1.0+aaaa"), "--port", "6799")
        busy = self.spawn_node(str(self.box / ".orca-remote" / "orcad-0.1.0+aaaa" / "daemon-entry.js"), child=True)
        self.units(**{"orca-tunnel-box.service": self.running(cur)})
        r = self.pair()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        out = r.stdout
        self.assertIn(f"pid {idle} orcad-0.1.0+aaaa server port 6799: 0 child processes: stop", out)
        self.assertIn(f"pid {busy} orcad-0.1.0+aaaa helper: 1 child processes: leave (in use)", out)
        self.assertIn("left them: run again with --stop-stale", out)
        self.assertLess(out.index(f"pid {idle}"), out.index("running orcad"))  # named before anything else
        self.assertTrue(self.alive(idle) and self.alive(busy))
        self.assertIn(f"one runtime orcad-0.2.0+bbbb pid {cur}", out)
        self.assertIn("already-paired box", out)
        r = self.pair("--stop-stale")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn(f"stopped orcad-0.1.0+aaaa pid {idle}", r.stdout)
        next(p for p in self.procs if p.pid == idle).wait(timeout=10)
        self.assertTrue(self.alive(busy) and self.alive(cur))

    def test_a_serving_older_build_is_not_replaced_without_a_version_and_a_yes(self):
        old = self.spawn_node(self.box_slot("0.1.0+aaaa"), "--port", str(self.port), port=self.port)
        r = self.pair()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("--version 0.2.0+bbbb --stop-stale", r.stderr)
        self.assertIn("--version 0.1.0+aaaa", r.stderr)
        self.assertIn("Nothing was changed", r.stderr)
        self.assertFalse((self.box / ".orca-remote" / "orcad-0.2.0+bbbb").exists())
        self.assertTrue(self.alive(old))
        r = self.pair("--version", "0.2.0+bbbb")  # a version is not a yes
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("--stop-stale", r.stderr)
        self.assertTrue(self.alive(old))

    def test_a_serving_older_build_with_live_terminals_is_never_replaced(self):
        old = self.spawn_node(self.box_slot("0.1.0+aaaa"), "--port", str(self.port), port=self.port, child=True)
        r = self.pair("--stop-stale")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("is in use, so it is not replaced", r.stderr)
        self.assertTrue(self.alive(old))
        self.assertFalse((self.box / ".orca-remote" / "orcad-0.2.0+bbbb").exists())

    def test_a_server_already_on_another_port_stops_the_run_before_anything_changes(self):
        other = self.spawn_node(self.box_slot("0.2.0+bbbb"), "--port", "6799")
        r = self.pair("--stop-stale")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(f"pid {other} on port 6799", r.stderr)
        self.assertIn("--port 6799 --version 0.2.0+bbbb", r.stderr)
        self.assertIn(f"kill {other}", r.stderr)
        self.assertFalse((self.box / ".orca-remote" / "orcad-0.2.0+bbbb" / ".install-complete").exists())
        next(p for p in self.procs if p.pid == other).kill()
        old = self.spawn_node(self.box_slot("0.1.0+aaaa"), "--port", "6798")
        self.units(**{"orcad.service": self.running(old)})
        r = self.pair()
        self.assertIn("orcad.service on", r.stderr)


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

    def spawn(self, *words, port=0, exe=sys.executable, child=False, shell=None):
        return Env.spawn(self, *words, port=port, exe=exe, child=child, shell=shell)

    def spawn_node(self, *words, port=0, shell=None):
        node = self.e.home / "nodebin" / "node"  # not on PATH
        if not node.exists():
            node.parent.mkdir(exist_ok=True)
            node.symlink_to(sys.executable)
        return self.spawn(*words, port=port, exe=str(node), shell=shell)

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
        new = self.spawn_node(self.slot("0.2.0+bbbb"), "--port", "6769")
        old = self.spawn_node(self.slot("0.1.0+aaaa"), "--port", "6770")
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
        self.spawn_node(self.slot("0.2.0+bbbb"), "--port", "6769")
        r, rows = self.check()
        self.assertTrue(rows["runtimes on box"].startswith("ok"), rows)
        self.assertIn("orcad-0.2.0+bbbb", rows["runtimes on box"])
        self.assertIn("Orca 1.4.274", rows["runtimes on box"])
        r, rows = self.check("--orca-version", "1.4.274")
        self.assertTrue(rows["runtimes on box"].startswith("ok"), rows)
        r, rows = self.check("--orca-version", "1.4.280")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--pair --port 6769 --version 1.4.280", rows["runtimes on box"])

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
        self.spawn_node(self.slot("0.2.0+bbbb"), "--port", "6769", shell="/bin/oldsh")  # review M3
        r, rows = self.check(env_extra={"STUB_MANAGER_SHELL": login})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("restart needed", rows["shell on box"])
        self.assertIn("systemctl --user restart orcad.service", rows["shell on box"])

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
        own = self.spawn("ssh", "-o", "ControlPath=none", port=port)
        r, rows = self.check(env_extra={"STUB_ORCA_TUNNEL_PID": str(own)})
        self.assertTrue(rows["direction 1 tunnel"].startswith("ok"), rows)
        rider = self.spawn("ssh", "-N")
        r, rows = self.check(env_extra={"STUB_ORCA_TUNNEL_PID": str(rider)})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(f"held by pid {own}", rows["direction 1 tunnel"])
        self.assertIn("orca-host-install box --tunnel", rows["direction 1 tunnel"])


if __name__ == "__main__":
    unittest.main()
