"""project-setup <repo> <host> on native Orca (phase 6 of docs/superpowers/plans/2026-11-19-native-orca-setup.md):
direction 1 (this machine's Orca drives Orca's server on an SSH host) and direction 2 (the host's coordinator runs
lanes here, paired with the desktop's share link), plus --remove-host and lane-tunnel.

The "box" is a second temporary HOME. A fake ssh runs the remote command there with sh (stdin passed through), so the
copy, the host's own project-setup run and the box-side pairing all really run, against the stub orca. On the box the
Orca CLI is a shim in the format Orca writes on a host (~/.orca/cli/bin/orca: exec '<node>' '<cli.js>' "$@"), whose
fake node logs its argv and only a hash of what it reads on stdin, then answers through the stub."""
import base64
import hashlib
import json
import os
import pty
import re
import secrets
import select
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from test_project_setup import ROOT, git
from test_project_setup_native import NativeEnv, SETUP, load

TUNNEL = ROOT / "core" / "scripts" / "lane-tunnel"
UNIT = ROOT / "core" / "templates" / "lane-tunnel@.service"
PRELOAD = ROOT / "core" / "scripts" / "pairing-code-stdin.cjs"
REMOTE_DOC = ROOT / "orchestrators" / "native-orca" / "remote.md"

FAKE_SSH = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
def _envhit(tool):
    import os as _o
    hits = [k for k, v in _o.environ.items() if "orca://" in v or "dvtok-" in v]
    if hits:
        with open(_o.environ["STUB_LOG"] + ".envhits", "a") as f:
            f.write(tool + " " + " ".join(hits) + "\n")
_envhit('ssh')
args = sys.argv[1:]
with open(os.environ["STUB_LOG"] + ".ssh", "a") as f:
    f.write(json.dumps(args) + "\n")
with open(os.environ["STUB_LOG"], "a") as f:
    f.write("ssh " + " ".join(args) + "\n")
rest, host = list(args), None
while rest:
    a = rest.pop(0)
    if a in ("-F", "-o", "-R", "-L", "-i", "-p"):
        rest.pop(0)
    elif a == "--":
        host = rest.pop(0)
        break
    elif a.startswith("-"):
        continue
    else:
        host = a
        break
down_on = os.environ.get("STUB_SSH_DOWN_ON")
if os.environ.get("STUB_SSH_DOWN") or (down_on and down_on in " ".join(rest)):
    sys.stderr.write("ssh: connect to host %s: No route to host\n" % host)
    sys.exit(255)
box = os.environ["STUB_BOX"]
env = {**os.environ, "HOME": box, "STUB_STATE": box + "/stub.json", "STUB_LOG": box + "/stub.log",
       "XDG_CONFIG_HOME": box + "/.config", "XDG_STATE_HOME": box + "/.local/state",
       "XDG_DATA_HOME": box + "/.local/share"}
env.pop("ORCA_TERMINAL_HANDLE", None)
sys.exit(subprocess.run(["sh", "-c", " ".join(rest)], env=env, cwd=box).returncode)
'''

FAKE_NODE = r'''#!/usr/bin/env python3
import hashlib, json, os, sys
def _envhit(tool):
    import os as _o
    hits = [k for k, v in _o.environ.items() if "orca://" in v or "dvtok-" in v]
    if hits:
        with open(_o.environ["STUB_LOG"] + ".envhits", "a") as f:
            f.write(tool + " " + " ".join(hits) + "\n")
_envhit('node')
args = sys.argv[1:]
rec = {"argv": args}
preload = None
if args[:1] == ["--require"]:
    preload, args = args[1], args[2:]
cli, args = args[0], args[1:]
env = dict(os.environ)
if "--pairing-code" in args and args[args.index("--pairing-code") + 1] == "-":
    if not (preload and os.path.isfile(preload) and preload.endswith("pairing-code-stdin.cjs")):
        sys.stderr.write("fake node: --pairing-code - without the stdin preload\n")
        sys.exit(3)
    code = sys.stdin.read().strip()
    rec["stdin_sha256"] = hashlib.sha256(code.encode()).hexdigest()
    rec["user_data"] = os.environ.get("ORCA_USER_DATA_PATH")
    env["STUB_PAIRED_BY_STDIN"] = "1"
    if os.environ.get("STUB_ADD_ECHOES"):  # a CLI error that repeats what it was given, on stdout and stderr
        with open(os.environ["STUB_LOG"] + ".node", "a") as f:
            f.write(json.dumps(rec) + "\n")
        print(json.dumps({"ok": False, "error": {"code": "invalid_argument", "message": "bad pairing code " + code}}))
        sys.stderr.write("cli: rejected " + code + "\n")
        sys.exit(1)
with open(os.environ["STUB_LOG"] + ".node", "a") as f:
    f.write(json.dumps(rec) + "\n")
os.execve(os.environ["STUB_ORCA"], ["orca", *args], env)
'''

FAKE_HOST_INSTALL = r'''#!/usr/bin/env python3
import json, os, re, sys
def _envhit(tool):
    import os as _o
    hits = [k for k, v in _o.environ.items() if "orca://" in v or "dvtok-" in v]
    if hits:
        with open(_o.environ["STUB_LOG"] + ".envhits", "a") as f:
            f.write(tool + " " + " ".join(hits) + "\n")
_envhit('orca-host-install')
with open(os.environ["STUB_LOG"] + ".install", "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
host = sys.argv[1]
name = re.sub(r"[^A-Za-z0-9._-]", "-", host)
p = os.environ["STUB_STATE"]
state = json.load(open(p)) if os.path.exists(p) else {}
state.setdefault("environments", []).append({"name": name, "state": os.environ["STUB_BOX"] + "/stub.json"})
json.dump(state, open(p, "w"))
print("installed orcad-0.1.0\nstarted orcad 6769\npaired " + name)
'''

FAKE_SYSTEMCTL = r'''#!/usr/bin/env python3
import os, signal, subprocess, sys
def _envhit(tool):
    import os as _o
    hits = [k for k, v in _o.environ.items() if "orca://" in v or "dvtok-" in v]
    if hits:
        with open(_o.environ["STUB_LOG"] + ".envhits", "a") as f:
            f.write(tool + " " + " ".join(hits) + "\n")
_envhit('systemctl')
args = sys.argv[1:]
with open(os.environ["STUB_LOG"] + ".systemctl", "a") as f:
    f.write(" ".join(args) + "\n")
if os.environ.get("STUB_NO_SYSTEMD"):
    sys.exit(1)
conf = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.environ["HOME"], ".config")
units = [a for a in args if a.startswith("lane-tunnel@")]
def pidfile(u):
    return os.path.join(os.environ["HOME"], "tunnel-" + u + ".pid")
def stop(u):
    try:
        os.kill(int(open(pidfile(u)).read()), signal.SIGTERM)
        os.unlink(pidfile(u))
    except (OSError, ValueError):
        pass
def start(u):
    if os.environ.get("STUB_TUNNEL_FAILS"):
        return
    impostor = os.environ.get("STUB_TUNNEL_IMPOSTOR")  # the unit dies, another program holds the port
    name = u[len("lane-tunnel@"):-len(".service")]
    env = dict(l.split("=", 1) for l in open(os.path.join(conf, "lane-control", "tunnels", name + ".env")).read().splitlines() if "=" in l)
    p = subprocess.Popen([sys.executable, "-c", "import socket,sys,time\ns=socket.socket()\ns.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
                          "s.bind((sys.argv[2],int(sys.argv[1])))\ns.listen()\ntime.sleep(120)", env["PORT"],
                          "0.0.0.0" if os.environ.get("STUB_TUNNEL_EXPOSED") else "127.0.0.1"],
                         start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    open(pidfile(u) + (".impostor" if impostor else ""), "w").write(str(p.pid))
for u in units:
    if "disable" in args or "stop" in args or "restart" in args:
        stop(u)
    if ("enable" in args and "--now" in args) or "restart" in args:
        start(u)
    if "is-active" in args:
        sys.exit(0 if os.path.exists(pidfile(u)) else 3)
'''


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


FAKE_LOGGER = '#!/bin/sh\nprintf "%s\\n" "$*" >> "$STUB_LOG.{name}"\n{tail}'


def make_link(port=None, address="127.0.0.1"):
    port = port or free_port()
    token = "dvtok-" + secrets.token_hex(16)
    payload = {"v": 2, "endpoint": f"ws://{address}:{port}", "deviceToken": token,
               "publicKeyB64": base64.b64encode(secrets.token_bytes(32)).decode(), "pairedDeviceId": "dev_1",
               "scope": "runtime", "hostDescriptor": {"kind": "desktop"}}
    code = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"orca://pair?code={code}", token, code


def run_pty(argv, env, answer=None, prompt="share link", timeout=90):
    """Run argv in a pseudo-terminal (so getpass has a real /dev/tty with echo); type `answer` at `prompt`.
    Returns (exit status, everything the terminal showed)."""
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execve(argv[0], argv, env)
        finally:
            os._exit(127)
    out, sent, end = b"", answer is None, time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.5)
        if r:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        if not sent and prompt.encode() in out:
            time.sleep(0.2)  # getpass turns echo off right after the prompt
            os.write(fd, answer.encode() + b"\r")
            sent = True
    else:
        os.kill(pid, 9)
    _, status = os.waitpid(pid, 0)
    os.close(fd)
    return os.waitstatus_to_exitcode(status), out.decode(errors="replace")


class RemoteEnv(NativeEnv):
    def __init__(self):
        super().__init__()
        self.box = self.home / "box"
        (self.box / ".config").mkdir(parents=True)
        for name, body in (("ssh", FAKE_SSH), ("node-fake", FAKE_NODE), ("orca-host-install", FAKE_HOST_INSTALL),
                           ("systemctl", FAKE_SYSTEMCTL)):
            p = self.bin / name
            p.write_text(body)
            p.chmod(0o755)
        for name, tail in (("machine-setup", "exit 0\n"),):
            p = self.bin / name
            p.write_text(FAKE_LOGGER.format(name=name, tail=tail))
            p.chmod(0o755)
        cli = self.box / ".orca" / "cli" / "bin"
        cli.mkdir(parents=True)
        shim = ("#!/usr/bin/env sh\nset -eu\n"
                f"export ORCA_USER_DATA_PATH='{self.box}/.orca'\n"
                "unset NODE_OPTIONS NODE_REPL_EXTERNAL_MODULE ELECTRON_RUN_AS_NODE\n"
                f"exec '{self.bin}/node-fake' '{self.box}/.orca-remote/orcad-0.1.0/out/cli/index.js' \"$@\"\n")
        for n in ("orca", "orca-ide"):
            (cli / n).write_text(shim)
            (cli / n).chmod(0o700)
        self.env.update(STUB_BOX=str(self.box), STUB_ORCA=str(self.bin / "orca"), LANE_ORCA_HOST_INSTALL=str(self.bin / "orca-host-install"),
                        LANE_MACHINE_SETUP=str(self.bin / "machine-setup"), LANE_PAIR_WAIT="0",
                        LANE_TUNNEL_WAIT="3")
        (self.home / "tmp").mkdir()
        self.env["TMPDIR"] = str(self.home / "tmp")  # a temp file a child writes is inside what the tests scan
        self.env.pop("ORCA_CLI", None)
        self.env.pop("LANE_SSH_CONFIG", None)

    def close(self):
        import signal
        for f in self.home.glob("**/tunnel-*.pid*"):
            try:
                os.kill(int(f.read_text()), signal.SIGTERM)
            except (OSError, ValueError):
                pass
        super().close()

    def box_repo(self, name="app"):
        d = self.box / "code" / name
        d.mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=d)
        git("commit", "-q", "--allow-empty", "-m", "init", cwd=d)
        return d

    def saved(self, env_name, state=True):
        """This machine has paired the box already (direction 1)."""
        st = self.stub_state()
        st.setdefault("environments", []).append({"name": env_name, **({"state": str(self.box / "stub.json")} if state else {})})
        self.state.write_text(json.dumps(st))

    def box_state(self):
        f = self.box / "stub.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def lines(self, suffix, where=None):
        f = Path(str((where or self.home) / "stub.log") + suffix) if where else Path(str(self.log) + suffix)
        return f.read_text().splitlines() if f.exists() else []

    def jlines(self, suffix, where=None):
        return [json.loads(l) for l in self.lines(suffix, where)]

    def setup(self, *args, env_extra=None):
        return subprocess.run([sys.executable, str(SETUP), *args, "--skip-adapt"],
                              env={**self.env, **(env_extra or {})}, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)

    def pty_setup(self, *args, answer=None, env_extra=None):
        return run_pty([sys.executable, str(SETUP), *args, "--skip-adapt"], {**self.env, **(env_extra or {})},
                       answer=answer)

    def all_files(self):
        for p in self.home.rglob("*"):
            if p.is_file() and not p.is_symlink():
                yield p


class Case(unittest.TestCase):
    def setUp(self):
        self.e = RemoteEnv()
        self.addCleanup(self.e.close)
        self.repo = self.e.box_repo()


# --- Task 6.1: direction 1 -----------------------------------------------------------------------------------------

class PlaceTests(Case):
    def test_place_for_a_host_routes_every_orca_call_to_its_environment(self):
        m = load()
        p = m.Place("user@box.example")
        self.assertEqual(p.env, "user-box.example")  # the name orca-host-install saves it under
        self.assertEqual(p.argv("terminal", "list")[1:], ["terminal", "list", "--environment", "user-box.example"])
        self.assertEqual(m.Place().argv("status")[1:], ["status"])

    def test_place_for_a_host_runs_host_commands_over_ssh(self):
        m = load()
        with mock.patch.dict(os.environ, {"LANE_SSH_CONFIG": "~/trial/ssh_config"}):
            argv = m.Place("box").ssh_argv("true")
        self.assertEqual(argv[:3], ["ssh", "-F", os.path.expanduser("~/trial/ssh_config")])
        self.assertEqual(argv[-2:], ["box", "true"])


class DirectionOneTests(Case):
    def test_a_host_without_a_reachable_server_gets_one_from_orca_host_install(self):
        r = self.e.setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        installs = self.e.jlines(".install")
        self.assertEqual(len(installs), 1, installs)
        self.assertEqual(installs[0][:2], ["box", "--pair"])
        port = int(installs[0][installs[0].index("--port") + 1])
        self.assertNotEqual(port, 6768)  # the desktop's own listener; the tunnel uses one port at both ends
        self.assertIn("orca server: created", r.stdout)

    def test_a_reachable_host_server_is_not_installed_again(self):
        self.e.saved("box")
        r = self.e.setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.e.jlines(".install"), [])
        self.assertIn("orca server: exists", r.stdout)

    def test_host_positional_and_host_flag_are_the_same(self):
        self.e.saved("box")
        for args in ((str(self.repo), "box"), (str(self.repo), "--host", "box"), ("--host=box", str(self.repo))):
            r = self.e.setup(*args, "--no-start", "--no-desktop-lanes")
            self.assertEqual(r.returncode, 0, (args, r.stderr + r.stdout))
        self.assertEqual(self.e.setup(str(self.repo), "box", "--host", "other", "--no-start").returncode, 2)

    def test_the_host_runs_the_native_steps_itself(self):
        self.e.saved("box")
        r = self.e.setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes", "--name", "Shop")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        ssh = self.e.jlines(".ssh")
        reexec = [c for c in ssh if "scripts/project-setup" in c[-1] and "--which-orchestrator" not in c[-1]
                  and "--box-pairing" not in c[-1]]
        self.assertEqual(len(reexec), 1, ssh)
        cmd = reexec[0][-1]
        self.assertIn("--via-host", cmd)
        self.assertNotIn("--host", cmd.replace("--via-host", ""))
        self.assertNotIn(" box", cmd)
        self.assertNotIn("--no-desktop-lanes", cmd)  # only this machine's side reads it
        # what phase 3 does, done on the box, in the box's files, against the box's Orca runtime
        self.assertTrue((self.repo / ".lane-control" / "coordinator.md").is_file())
        box_store = next((self.e.box / ".config" / "lane-control" / "projects").iterdir())
        st = json.loads((box_store / "settings.json").read_text())
        self.assertEqual(st["orchestrator"], "native-orca")
        self.assertEqual(st["name"], "Shop")
        self.assertTrue(any("repo add" in l for l in self.e.lines("", self.e.box)))
        self.assertFalse((self.e.home / ".config" / "lane-control" / "projects").exists())  # nothing set up here

    def test_the_host_starts_the_coordinator_and_this_machine_sees_it(self):
        self.e.saved("box")
        cfg = self.e.box / ".claude.json"
        cfg.write_text(json.dumps({"projects": {str(self.repo): {"hasTrustDialogAccepted": True}}}))
        r = self.e.setup(str(self.repo), "box", "--no-design-lead", "--no-desktop-lanes",
                         env_extra={"LANE_RUN_WAIT": "0"})
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        titles = [t.get("title") for t in self.e.box_state()["terminals"]]
        self.assertIn("app coordinator", titles)
        self.assertIn("seen here: ok", r.stdout)  # listed through --environment box from this machine

    def test_machine_setup_runs_for_the_host(self):
        self.e.saved("box")
        r = self.e.setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes", "--trust-folder")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.e.lines(".machine-setup"), ["--host box --no-start --trust-folder"])

    def test_no_machine_setup_skips_it(self):
        self.e.saved("box")
        r = self.e.setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes", "--no-machine-setup")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.e.lines(".machine-setup"), [])
        self.assertIn("machine setup: skipped", r.stdout)

    def test_a_cotal_project_on_the_host_keeps_the_cotal_path(self):
        (self.repo / ".cotal" / "agents").mkdir(parents=True)
        r = self.e.setup(str(self.repo), "box", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue([l for l in self.e.lines("", self.e.box) if l.startswith("cotal space")])
        self.assertEqual(self.e.lines(".machine-setup"), [])
        self.assertEqual(self.e.jlines(".install"), [])  # setup asks the host first; Cotal's path needs no new server
        self.assertEqual(self.e.jlines(".node", self.e.box) and
                         [x for x in self.e.jlines(".node", self.e.box) if "stdin_sha256" in x], [])

    def test_an_unreachable_host_is_a_message_and_changes_nothing_here(self):
        r = self.e.setup(str(self.repo), "box", "--no-start", env_extra={"STUB_SSH_DOWN": "1"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("box", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_a_relative_target_is_refused_before_anything_runs(self):
        r = self.e.setup("code/app", "box", "--no-start")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.e.lines(".ssh"), [])
        self.assertEqual(self.e.jlines(".install"), [])


class NoAgentYetTests(Case):
    def test_no_start_needs_no_agent_on_the_host_yet(self):
        # A box is set up before the owner signs an agent in there; only a start needs the harness.
        self.e.saved("box")
        (self.e.bin / "claude").unlink()
        r = self.e.setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue((self.repo / ".lane-control" / "coordinator.md").is_file())
        r = self.e.setup(str(self.repo), "box", "--no-desktop-lanes")
        self.assertEqual(r.returncode, 1)
        self.assertIn("claude not on PATH", r.stderr)


# --- Task 6.2: direction 2 -----------------------------------------------------------------------------------------

class DirectionTwoTests(Case):
    def setUp(self):
        super().setUp()
        self.e.saved("box")
        self.port = free_port()
        self.link, self.token, self.code = make_link(self.port)

    def secrets_seen(self, text):
        return [s for s in (self.link, self.token, self.code) if s in text]

    def test_link_not_in_argv_or_files(self):
        """Review Focus 5: the link reaches the box only by stdin, and is nowhere else."""
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=self.link)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.secrets_seen(out), [], "the terminal showed the link (echo, log or error)")
        # every argv of every program the run started that we can see: Orca here and on the box, ssh, systemctl,
        # the box CLI's node, orca-host-install, machine-setup
        for f in self.e.all_files():
            data = f.read_bytes()
            for s in (self.link, self.token, self.code):
                self.assertNotIn(s.encode(), data, f"{s[:12]}... found in {f}")
        hits = self.e.lines(".envhits") + self.e.lines(".envhits", self.e.box)
        self.assertEqual(hits, [], "a child got the link in its environment")
        node = [x for x in self.e.jlines(".node", self.e.box) if "stdin_sha256" in x]
        self.assertEqual(len(node), 1, node)
        self.assertEqual(node[0]["stdin_sha256"], hashlib.sha256(self.link.encode()).hexdigest())
        argv = node[0]["argv"]
        self.assertEqual(argv[argv.index("--pairing-code") + 1], "-")
        self.assertEqual(node[0]["user_data"], f"{self.e.box}/.orca")  # as the shim sets it
        # saved on the box under this machine's name; only that name is stored
        self.assertEqual([e["name"] for e in self.e.box_state()["environments"]], ["deskbox"])
        box_store = next((self.e.box / ".config" / "lane-control" / "projects").iterdir())
        self.assertEqual(json.loads((box_store / "settings.json").read_text())["paired_environment"], "deskbox")
        rec = json.loads((self.e.home / ".config" / "lane-control" / "hosts" / "box.json").read_text())
        self.assertEqual(rec, {"host": "box", "environment": "box", "desktop_environment": "deskbox",
                               "tunnel_port": self.port})

    def test_the_prompt_is_hidden(self):
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=self.link)
        self.assertEqual(rc, 0, out)
        self.assertIn("hidden", out)
        self.assertIn("Settings > Servers", out)  # where the owner makes the link
        self.assertIn("revoke", out.lower())  # what the link allows, and that it can be revoked
        self.assertIn("Orca's own pairing store", out)  # the one place the box keeps what the link gives

    def test_the_tunnel_is_a_user_service_on_the_links_port(self):
        port = free_port()
        link, _, _ = make_link(port)
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=link)
        self.assertEqual(rc, 0, out)
        unit = self.e.home / ".config" / "systemd" / "user" / "lane-tunnel@.service"
        self.assertIn("-R", unit.read_text())
        env = (self.e.home / ".config" / "lane-control" / "tunnels" / "box.env").read_text()
        self.assertIn(f"PORT={port}", env)
        self.assertIn("SSH_HOST=box", env)
        self.assertEqual(oct((self.e.home / ".config" / "lane-control" / "tunnels" / "box.env").stat().st_mode & 0o777),
                         "0o600")
        self.assertIn("--user enable --now lane-tunnel@box.service", self.e.lines(".systemctl"))

    def test_a_link_with_a_network_address_is_refused_without_showing_it(self):
        link, token, code = make_link(address="192.0.2.7")
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=link)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("127.0.0.1", out)
        self.assertEqual([s for s in (link, token, code, "192.0.2.7") if s in out], [])
        self.assertEqual(self.e.box_state().get("environments", []), [])
        self.assertEqual(self.e.lines(".systemctl"), [])

    def test_a_link_that_is_not_a_pairing_link_is_refused_without_showing_it(self):
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer="orca://pair?code=bm9wZQ-secretish")
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn("bm9wZQ-secretish", out)
        self.assertEqual(self.e.box_state().get("environments", []), [])

    def test_an_empty_answer_skips_desktop_lanes_and_says_how(self):
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer="")
        self.assertEqual(rc, 0, out)
        self.assertIn("desktop lanes: skipped", out)
        self.assertEqual(self.e.box_state().get("environments", []), [])
        self.assertTrue((self.repo / ".lane-control" / "coordinator.md").is_file())  # the rest still ran

    def test_without_a_terminal_nothing_is_asked(self):
        r = self.e.setup(str(self.repo), "box", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("desktop lanes: skipped", r.stdout)
        self.assertIn("in a terminal", r.stdout)

    def test_no_desktop_lanes_asks_nothing(self):
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("share link", out)

    def test_an_already_paired_box_is_not_asked_again(self):
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=self.link)
        self.assertEqual(rc, 0, out)
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("share link", out)
        self.assertIn("desktop lanes: exists", out)
        self.assertEqual(len([x for x in self.e.jlines(".node", self.e.box) if "stdin_sha256" in x]), 1)

    def assert_nowhere(self, out, link, token, code):
        self.assertEqual([s for s in (link, token, code) if s in out], [], "the link reached the output")
        for f in self.e.all_files():
            data = f.read_bytes()
            for s in (link, token, code):
                self.assertNotIn(s.encode(), data, f"{s[:12]}... found in {f}")

    def test_a_box_cli_error_that_repeats_the_code_never_shows_it(self):
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=self.link,
                                   env_extra={"STUB_ADD_ECHOES": "1"})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("invalid_argument", out)
        self.assert_nowhere(out, self.link, self.token, self.code)

    def test_a_failed_pairing_stops_the_tunnel_and_says_to_revoke(self):
        for extra in ({"STUB_ADD_ECHOES": "1"}, {"STUB_SSH_DOWN_ON": "--box-pairing add"}):
            rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=self.link, env_extra=extra)
            self.assertNotEqual(rc, 0, (extra, out))
            self.assertEqual(self.e.lines(".systemctl")[-1], "--user disable --now lane-tunnel@box.service", extra)
            self.assertFalse((self.e.home / ".config" / "lane-control" / "tunnels" / "box.env").exists(), extra)
            self.assertFalse((self.e.home / ".config" / "lane-control" / "hosts" / "box.json").exists(), extra)
            self.assertIn("revoke", out.lower())
            self.assert_nowhere(out, self.link, self.token, self.code)

    def test_a_port_already_taken_on_the_box_is_refused_before_the_link_is_sent(self):
        import socket
        s = socket.socket()  # the box's own Orca server, say, on the desktop's port (the "box" is this machine)
        s.bind(("127.0.0.1", self.port))
        s.listen()
        self.addCleanup(s.close)
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=self.link)
        self.assertNotEqual(rc, 0, out)
        self.assertIn(f"port {self.port}", out)
        self.assertEqual([x for x in self.e.jlines(".node", self.e.box) if "stdin_sha256" in x], [])
        self.assertNotIn("--user enable --now lane-tunnel@box.service", self.e.lines(".systemctl"))
        self.assert_nowhere(out, self.link, self.token, self.code)

    def test_a_tunnel_that_does_not_come_up_sends_nothing(self):
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=self.link,
                                   env_extra={"STUB_TUNNEL_FAILS": "1"})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("did not come up", out)
        self.assertIn("key", out)  # the unit has no ssh agent: it needs a key that works without one
        self.assertEqual([x for x in self.e.jlines(".node", self.e.box) if "stdin_sha256" in x], [])
        self.assertEqual(self.e.lines(".systemctl")[-1], "--user disable --now lane-tunnel@box.service")

    def test_a_terminal_that_cannot_hide_input_is_not_asked(self):
        import getpass
        import warnings
        m = load()

        def echoing(prompt=""):
            warnings.warn("Can not control echo on the terminal.", getpass.GetPassWarning)
            return self.link
        place = m.Place("box")
        with mock.patch.dict(os.environ, self.e.env, clear=True), mock.patch.object(m, "has_terminal", return_value=True), \
                mock.patch.object(m, "box_pairing_remote", return_value="missing"), \
                mock.patch("getpass.getpass", echoing), mock.patch("sys.stdout", new_callable=lambda: __import__("io").StringIO()) as out:
            with self.assertRaises(m.Fail) as e:
                m.desktop_lanes(place)
        self.assertIn("hide", str(e.exception))
        self.assertNotIn(self.link, out.getvalue() + str(e.exception))

    def test_without_systemd_here_nothing_is_paired(self):
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=self.link,
                                   env_extra={"STUB_NO_SYSTEMD": "1"})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("systemd", out)
        self.assertEqual(self.e.box_state().get("environments", []), [])
        self.assertEqual(self.secrets_seen(out), [])


# --- review round 1 ----------------------------------------------------------------------------------------------

class ReviewRound1Tests(Case):
    def test_show_over_a_host_installs_pairs_and_starts_nothing(self):
        # M1: --show writes nothing; over a host it is the copy, the box's own --show, and nothing else.
        r = self.e.setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes", "--no-machine-setup")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.e.state.write_text(json.dumps({**self.e.stub_state(), "environments": []}))  # the box's server is gone
        before = len(self.e.jlines(".install"))
        for flag in ("--show", "--refresh"):
            rc, out = run_pty([sys.executable, str(SETUP), str(self.repo), "box", flag], self.e.env)
            self.assertEqual(rc, 0, (flag, out))
            self.assertEqual(len(self.e.jlines(".install")), before, flag)
            self.assertEqual(self.e.lines(".machine-setup"), [], flag)
            self.assertNotIn("share link", out, flag)
            self.assertNotIn("seen here", out, flag)
            self.assertEqual(self.e.lines(".systemctl"), [], flag)
            if flag == "--show":
                self.assertIn("native-orca", out)  # the box's own --show printed its store

    def test_a_host_that_looks_like_an_option_is_refused_before_anything_runs(self):
        # M2: argparse stops the plain positional form, not --host=-o... or --remove-host=-o...
        for args in (["--host=-oProxyCommand=touch_x", str(self.repo)], ["--remove-host=-oProxyCommand=touch_x"],
                     [str(self.repo), "--host", "box\nx"], [str(self.repo), "--host", "box name"],
                     ["--remove-host", "box;id"]):
            r = self.e.setup(*args, "--no-start")
            self.assertEqual(r.returncode, 2, (args, r.stderr + r.stdout))
            self.assertIn("host", r.stderr)
        self.assertEqual(self.e.lines(".ssh"), [])
        self.assertEqual(self.e.jlines(".install"), [])

    def test_every_ssh_ends_its_options_before_the_host(self):
        self.e.saved("box")
        r = self.e.setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        for argv in self.e.jlines(".ssh"):
            self.assertIn("--", argv, argv)
            self.assertEqual(argv[argv.index("--") + 1], "box", argv)

    def test_setups_own_ssh_calls_never_wait_for_a_password(self):
        # M3: only the host's own run (it may ask the owner, with a terminal) is interactive.
        self.e.saved("box")
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=make_link()[0])
        self.assertEqual(rc, 0, out)
        for argv in self.e.jlines(".ssh"):
            if "-t" in argv:
                continue
            opts = " ".join(argv[:argv.index("--")])
            for o in ("BatchMode=yes", "ConnectTimeout=", "ServerAliveInterval="):
                self.assertIn(o, opts, argv)
        self.assertTrue(any("-t" in a for a in self.e.jlines(".ssh")))  # the host run keeps its terminal

    def test_an_opencode2_coordinator_starts_on_the_host(self):
        # m3: OpenCode v2 is first-class over a host too.
        self.e.saved("box")
        r = self.e.setup(str(self.repo), "box", "--no-design-lead", "--no-desktop-lanes", "--no-machine-setup",
                         "--coordinator", "opencode/gateway/m1", "--own-model", "gateway/m1",
                         env_extra={"LANE_RUN_WAIT": "0"})
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        started = [json.loads(l) for l in (self.e.box / "sessions.log").read_text().splitlines()]
        self.assertEqual([(s["bin"], s["role"]) for s in started], [("opencode2", "coordinator")])

    def test_a_hosted_model_is_refused_here_before_the_host_is_touched(self):
        r = self.e.setup(str(self.repo), "box", "--no-start", "--coordinator", "opencode/opencode-go/m1")
        self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
        self.assertIn("lane-tier", r.stderr)
        self.assertEqual(self.e.lines(".ssh"), [])

    def test_the_host_run_finds_an_agent_in_its_local_bin(self):
        # m3: a non-interactive ssh shell's PATH lacks ~/.local/bin, where agents are often installed.
        self.e.saved("box")
        local = self.e.box / ".local" / "bin"
        local.mkdir(parents=True)
        (self.e.bin / "claude").rename(local / "claude")
        cfg = self.e.box / ".claude.json"
        cfg.write_text(json.dumps({"projects": {str(self.repo): {"hasTrustDialogAccepted": True}}}))
        r = self.e.setup(str(self.repo), "box", "--no-design-lead", "--no-desktop-lanes", "--no-machine-setup",
                         env_extra={"LANE_RUN_WAIT": "0"})
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)


class ReviewRound1PairingTests(Case):
    def setUp(self):
        super().setUp()
        self.e.saved("box")
        self.port = free_port()
        self.link, self.token, self.code = make_link(self.port)

    assert_nowhere = DirectionTwoTests.assert_nowhere

    def test_a_port_held_by_another_program_after_our_unit_died_gets_no_link(self):
        # m1: something listens, but it is not our tunnel.
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=self.link,
                                   env_extra={"STUB_TUNNEL_IMPOSTOR": "1"})
        self.assertNotEqual(rc, 0, out)
        self.assertEqual([x for x in self.e.jlines(".node", self.e.box) if "stdin_sha256" in x], [])
        self.assert_nowhere(out, self.link, self.token, self.code)

    def test_a_tunnel_open_on_the_boxs_network_is_refused(self):
        # m6: GatewayPorts yes on the box binds the forward on every address, whatever -R says.
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=self.link,
                                   env_extra={"STUB_TUNNEL_EXPOSED": "1"})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("GatewayPorts", out)
        self.assertEqual([x for x in self.e.jlines(".node", self.e.box) if "stdin_sha256" in x], [])
        self.assertEqual(self.e.lines(".systemctl")[-1], "--user disable --now lane-tunnel@box.service")

    def test_a_box_cli_that_reads_stdin_does_not_take_the_link(self):
        # m4: the code is read once on the box and given to node alone.
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=self.link,
                                   env_extra={"STUB_LIST_READS_STDIN": "1"})
        self.assertEqual(rc, 0, out)
        node = [x for x in self.e.jlines(".node", self.e.box) if "stdin_sha256" in x]
        self.assertEqual([x["stdin_sha256"] for x in node], [hashlib.sha256(self.link.encode()).hexdigest()])

    def test_only_a_this_computer_address_is_taken(self):
        # N3: the tunnel binds 127.0.0.1 only, so localhost or ::1 would pair and never connect.
        for address in ("localhost", "[::1]"):
            link, token, code = make_link(self.port, address)
            rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=link)
            self.assertNotEqual(rc, 0, (address, out))
            self.assertIn("127.0.0.1", out)
            self.assert_nowhere(out, link, token, code)


class PreloadTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_the_preload_puts_the_stdin_code_in_argv_inside_the_process_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cli = Path(d) / "index.js"
            cli.write_text('const i = process.argv.indexOf("--pairing-code");\n'
                           'const eq = process.argv.find(a => a.startsWith("--pairing-code="));\n'
                           'const code = i >= 0 ? process.argv[i + 1] : eq && eq.slice("--pairing-code=".length);\n'
                           'const cmd = require("fs").readFileSync("/proc/self/cmdline", "utf8");\n'
                           'console.log(JSON.stringify({code: code, inCmdline: cmd.includes("s3cr3t")}));\n')
            r = subprocess.run(["node", "--require", str(PRELOAD), str(cli), "environment", "add", "--name", "x",
                                "--pairing-code", "-"], input="orca://pair?code=s3cr3t\n", capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout), {"code": "orca://pair?code=s3cr3t", "inCmdline": False})
            r = subprocess.run(["node", "--require", str(PRELOAD), str(cli), "--pairing-code", "-"], input="",
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            for given, stdin in ((["--pairing-code=-"], "orca://pair?code=s3cr3t"), (["--pairing-code", "-"], " \n\t")):
                r = subprocess.run(["node", "--require", str(PRELOAD), str(cli), *given], input=stdin,
                                   capture_output=True, text=True)
                if stdin.strip():
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertEqual(json.loads(r.stdout), {"code": stdin, "inCmdline": False})
                else:
                    self.assertEqual(r.returncode, 2)


class RemoveHostTests(Case):
    def setUp(self):
        super().setUp()
        self.e.saved("box")
        self.link, _, _ = make_link()
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=self.link)
        self.assertEqual(rc, 0, out)

    def test_remove_host_removes_the_box_environment_stops_the_tunnel_and_says_where_to_revoke(self):
        r = subprocess.run([sys.executable, str(SETUP), "--remove-host", "box"], env=self.e.env, capture_output=True,
                           text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.e.box_state().get("environments", []), [])
        self.assertIn("--user disable --now lane-tunnel@box.service", self.e.lines(".systemctl"))
        self.assertFalse((self.e.home / ".config" / "lane-control" / "tunnels" / "box.env").exists())
        self.assertFalse((self.e.home / ".config" / "lane-control" / "hosts" / "box.json").exists())
        box_store = next((self.e.box / ".config" / "lane-control" / "projects").iterdir())
        self.assertNotIn("paired_environment", json.loads((box_store / "settings.json").read_text()))
        self.assertIn("Settings > Servers", r.stdout)
        self.assertIn("revoke", r.stdout.lower())
        self.assertIn("orca-ide environment rm --environment box", r.stdout)  # direction 1 stays; how to drop it

    def test_remove_host_twice_is_harmless(self):
        for _ in range(2):
            r = subprocess.run([sys.executable, str(SETUP), "--remove-host", "box"], env=self.e.env,
                               capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_an_unreachable_box_still_stops_the_tunnel_and_says_what_is_left(self):
        r = subprocess.run([sys.executable, str(SETUP), "--remove-host", "box"], capture_output=True, text=True,
                           env={**self.e.env, "STUB_SSH_DOWN": "1"}, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("--user disable --now lane-tunnel@box.service", self.e.lines(".systemctl"))
        self.assertIn("environment rm --environment deskbox", r.stdout + r.stderr)  # to run on the box later
        self.assertTrue((self.e.home / ".config" / "lane-control" / "hosts" / "box.json").exists())  # for the retry
        self.assertIn("Settings > Servers", r.stdout)


class LaneTunnelTests(unittest.TestCase):
    def setUp(self):
        self.e = RemoteEnv()
        self.addCleanup(self.e.close)

    def tunnel(self, *args, env_extra=None):
        return subprocess.run(["sh", str(TUNNEL), *args], env={**self.e.env, **(env_extra or {})}, capture_output=True,
                              text=True)

    def test_start_writes_the_unit_and_its_env_and_enables_it(self):
        r = self.tunnel("start", "user@box.example", "6768", env_extra={"LANE_SSH_CONFIG": "~/trial/ssh_config"})
        self.assertEqual(r.returncode, 0, r.stderr)
        unit = (self.e.home / ".config" / "systemd" / "user" / "lane-tunnel@.service").read_text()
        self.assertIn("ExitOnForwardFailure=yes", unit)
        self.assertIn("127.0.0.1:$${PORT}:127.0.0.1:$${PORT}", unit)
        self.assertIn("Restart=always", unit)
        env = (self.e.home / ".config" / "lane-control" / "tunnels" / "user-box.example.env").read_text()
        self.assertIn("SSH_HOST=user@box.example", env)
        self.assertIn(f"SSH_CONFIG={self.e.home}/trial/ssh_config", env)
        self.assertIn("--user enable --now lane-tunnel@user-box.example.service", self.e.lines(".systemctl"))

    def test_a_changed_port_restarts_a_running_tunnel(self):
        self.tunnel("start", "box", "6768")
        self.tunnel("start", "box", "6790")
        self.assertIn("--user restart lane-tunnel@box.service", self.e.lines(".systemctl"))

    def test_a_bad_port_is_refused(self):
        for p in ("0", "70000", "x", "22 -oProxyCommand=x"):
            self.assertEqual(self.tunnel("start", "box", p).returncode, 2, p)

    def test_stop_disables_and_forgets(self):
        self.tunnel("start", "box", "6768")
        r = self.tunnel("stop", "box")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--user disable --now lane-tunnel@box.service", self.e.lines(".systemctl"))
        self.assertFalse((self.e.home / ".config" / "lane-control" / "tunnels" / "box.env").exists())

    def test_a_host_that_is_not_a_plain_name_is_refused(self):
        for host in ("box\nSSH_CONFIG=/tmp/x", "-oProxyCommand=x", "box name", "box;id"):
            self.assertEqual(self.tunnel("start", host, "6768").returncode, 2, host)
        self.assertFalse((self.e.home / ".config" / "lane-control" / "tunnels").exists())

    def test_stop_without_systemd_says_so_and_keeps_the_env(self):
        self.tunnel("start", "box", "6768")
        r = self.tunnel("stop", "box", env_extra={"STUB_NO_SYSTEMD": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("systemd", r.stderr)
        self.assertTrue((self.e.home / ".config" / "lane-control" / "tunnels" / "box.env").exists())

    def test_no_systemd_is_an_error_naming_it(self):
        r = self.tunnel("start", "box", "6768", env_extra={"STUB_NO_SYSTEMD": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("systemd", r.stderr)


class DocTests(unittest.TestCase):
    def test_remote_doc_covers_both_directions_removal_and_revoking(self):
        text = REMOTE_DOC.read_text()
        for s in ("project-setup <repo> <host>", "orca-host-install", "Share this computer", "127.0.0.1",
                  "--remove-host", "revoke", "lane-tunnel@", "paired_environment", "never"):
            self.assertIn(s, text, s)

    def test_the_coordinator_persona_names_the_stored_key(self):
        text = (ROOT / "orchestrators" / "native-orca" / "personas" / "coordinator.md").read_text()
        self.assertIn("paired_environment", text)

    def test_shell_scripts_parse(self):
        self.assertEqual(subprocess.run(["sh", "-n", str(TUNNEL)]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
