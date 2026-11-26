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
    try:  # what the box will dial, and a hash of the rest of the offer: no secret is logged
        import base64, urllib.parse
        c = (urllib.parse.parse_qs(urllib.parse.urlsplit(code).query).get("code") or [""])[0] if code.startswith("orca://") else code
        offer = json.loads(base64.urlsafe_b64decode(c + "=" * (-len(c) % 4)))
        rec["endpoint"] = offer.pop("endpoint")
        rec["rest_sha256"] = hashlib.sha256(json.dumps(offer, sort_keys=True).encode()).hexdigest()
    except Exception:
        pass
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
if "--version" in args:
    print("systemd " + os.environ.get("STUB_SYSTEMD", "260") + " (" + os.environ.get("STUB_SYSTEMD", "260") + ")")
    sys.exit(0)
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
                          "s.bind((sys.argv[2],int(sys.argv[1])))\ns.listen()\ntime.sleep(120)", env.get("REMOTE_PORT") or env["PORT"],
                          "0.0.0.0" if os.environ.get("STUB_TUNNEL_EXPOSED") else "127.0.0.1"],
                         start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    open(pidfile(u) + (".impostor" if impostor else ""), "w").write(str(p.pid))
def enabled(u):
    return os.path.join(os.environ["HOME"], "enabled-" + u)
for u in units:
    if "is-enabled" in args:
        sys.exit(0 if os.path.exists(enabled(u)) else 1)
    if "enable" in args:
        open(enabled(u), "w").write("1")
    if "disable" in args and os.environ.get("STUB_DISABLE_FAILS") and "--now" not in args:
        sys.exit(1)
    if "disable" in args and os.path.exists(enabled(u)):
        os.unlink(enabled(u))
    if "disable" in args or "stop" in args or "restart" in args:
        stop(u)
    if ("enable" in args and "--now" in args) or "restart" in args or "start" in args:
        start(u)
    if "is-active" in args:
        if os.environ.get("STUB_TUNNEL_FLAPS"):  # up for the first check, then ssh failed and the unit is restarting
            n = os.path.join(os.environ["HOME"], "is-active-count")
            k = int(open(n).read()) if os.path.exists(n) else 0
            open(n, "w").write(str(k + 1))
            if k >= 1:
                sys.exit(3)
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
                        LANE_TUNNEL_WAIT="3", LANE_TUNNEL_SETTLE="0")
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

    def test_the_server_can_be_pinned_to_a_version_and_a_source(self):
        for flag, opt in (("--orca-from", "--from"), ("--orca-bundle", "--bundle")):
            self.e.state.write_text("{}")
            shutil.rmtree(self.e.box / ".config" / "lane-control", True)
            r = self.e.setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes", flag, "/srv/orca/pinned",
                             "--orca-version", "0.1.0+abc123")
            self.assertEqual(r.returncode, 0, (flag, r.stderr + r.stdout))
            args = self.e.jlines(".install")[-1]
            self.assertEqual(args[args.index(opt) + 1], "/srv/orca/pinned", args)
            self.assertEqual(args[args.index("--version") + 1], "0.1.0+abc123", args)
            self.assertNotIn(args[0], ("--from",))
        # only this machine installs the server: the host's own run never sees the options
        self.assertNotIn("--orca-", " ".join(" ".join(c) for c in self.e.jlines(".ssh")))

    def test_without_pin_options_orca_host_install_gets_none(self):
        r = self.e.setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        args = self.e.jlines(".install")[0]
        for opt in ("--from", "--bundle", "--version"):
            self.assertNotIn(opt, args)

    def test_a_source_given_twice_is_refused_before_anything_runs(self):
        r = self.e.setup(str(self.repo), "box", "--orca-from", "/a", "--orca-bundle", "/b")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(self.e.jlines(".install"), [])

    def test_pin_options_for_a_server_that_is_already_there_are_said_to_be_unused(self):
        self.e.saved("box")
        r = self.e.setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes", "--orca-version", "0.1.0+abc123")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.e.jlines(".install"), [])
        self.assertIn("--orca-version", r.stdout)
        self.assertIn("not used", r.stdout)

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

    def test_the_host_run_does_the_beads_restore_check_and_names_the_sign_in_it_needs(self):
        # lc-5c1: the restore runs where the project is, on the box, with the box's own GitHub sign-in
        git("remote", "add", "origin", "https://github.com/acme/shop.git", cwd=self.repo)
        for tool, body in (("bd", "#!/bin/sh\nexit 0\n"), ("gh", "#!/bin/sh\nexit 1\n")):  # gh: not signed in
            (self.e.bin / tool).write_text(body)
            (self.e.bin / tool).chmod(0o755)
        self.e.saved("box")
        r = self.e.setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("fine-grained token", r.stderr + r.stdout)
        self.assertIn("never", r.stderr + r.stdout)  # setup never reads or copies the token
        self.assertFalse((self.repo / ".beads").exists() or (self.repo / ".beads").is_symlink())

    def test_a_box_gets_the_design_kit_and_the_design_skills(self):
        # review m4 (JAI-1): the box's own setup run installs the kit and the skills in the box's HOME, not here
        up = self.e.home / "upstream" / "mwg"
        (up / "skills" / "modern-web-guidance").mkdir(parents=True)
        (up / "skills" / "modern-web-guidance" / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\nv1\n")
        git("init", "-q", "-b", "main", cwd=up)
        git("add", "-A", cwd=up)
        git("commit", "-q", "-m", "v1", cwd=up)
        sha = git("rev-parse", "HEAD", cwd=up)
        (self.e.home / "design-skills.tsv").write_text(
            f"modern-web-guidance\tfile://{up}\t{sha}\tskills/modern-web-guidance\tApache-2.0\t-\n")
        self.e.saved("box")
        r = self.e.setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        box = self.e.box
        self.assertTrue(os.access(box / ".agents" / "bin" / "design-shots", os.X_OK))
        for f in ("component-gallery/SKILL.md", "component-gallery/DESIGN.md", "native-orca-lanes/frontend-brief.md",
                  "modern-web-guidance/SKILL.md"):
            self.assertTrue((box / ".agents" / "skills" / f).is_file(), f)
        self.assertTrue((self.repo / ".lane-control" / "design-reviewer.md").is_file())
        self.assertIn("modern-web-guidance created", r.stdout)
        for here in ("bin/design-shots", "skills/component-gallery", "skill-sources"):  # nothing set up on this side
            self.assertFalse((self.e.home / ".agents" / here).exists(), here)

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
                               "tunnel_port": self.port, "tunnel_remote_port": self.port})
        self.assertEqual(node[0]["endpoint"], f"ws://127.0.0.1:{self.port}")  # one port: the link as it was made

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
        self.assertIn("--tunnel-remote-port", out)  # the way out that leaves the box's own server where it is
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

    def agent_args(self):
        w = self.e.home / "card-ssh"
        w.write_text("#!/bin/sh\nexit 0\n")
        w.chmod(0o755)
        return ["--tunnel-auth-sock", "/tmp/lane-test/card-agent.sock", "--tunnel-ssh", str(w)], str(w)

    def test_tunnel_options_reach_the_unit_and_not_the_box(self):
        args, wrapper = self.agent_args()
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", *args, "--tunnel-no-enable", answer=self.link)
        self.assertEqual(rc, 0, out)
        env = (self.e.home / ".config" / "lane-control" / "tunnels" / "box.env").read_text()
        self.assertIn("SSH_AUTH_SOCK=/tmp/lane-test/card-agent.sock", env)
        self.assertIn(f"LANE_TUNNEL_SSH={wrapper}", env)
        sysctl = self.e.lines(".systemctl")
        self.assertIn("--user start lane-tunnel@box.service", sysctl)
        self.assertFalse([l for l in sysctl if " enable" in l], sysctl)
        self.assertNotIn("--tunnel-", " ".join(" ".join(c) for c in self.e.jlines(".ssh")))  # local paths stay here

    def test_without_tunnel_options_the_unit_is_enabled_and_has_no_agent(self):
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=self.link)
        self.assertEqual(rc, 0, out)
        env = (self.e.home / ".config" / "lane-control" / "tunnels" / "box.env").read_text()
        self.assertNotIn("SSH_AUTH_SOCK", env)
        self.assertIn("--user enable --now lane-tunnel@box.service", self.e.lines(".systemctl"))

    def test_bad_tunnel_paths_are_refused_before_the_link_is_asked_for(self):
        for args in (["--tunnel-auth-sock", "agent.sock"], ["--tunnel-ssh", "ssh"], ["--tunnel-ssh", "/nonexistent/ssh"]):
            rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", *args, answer=self.link)
            self.assertEqual(rc, 2, (args, out))
            self.assertNotIn("share link", out)
            self.assertEqual(self.e.lines(".systemctl"), [])

    def test_a_tunnel_ssh_that_is_a_folder_is_refused_before_direction_one_runs(self):
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", "--tunnel-ssh", str(self.e.home), answer=self.link)
        self.assertEqual(rc, 2, out)
        self.assertEqual(self.e.jlines(".install"), [])

    def test_tunnel_paths_get_lane_tunnels_rules_before_direction_one_runs(self):
        for flag, val in (("--tunnel-auth-sock", "/run/a.sock "), ("--tunnel-auth-sock", "/run/a\\b.sock"),
                          ("--tunnel-auth-sock", "'/run/a.sock"), ("--tunnel-ssh", str(self.e.home / "x") + " ")):
            rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", flag, val, answer=self.link)
            self.assertEqual(rc, 2, (flag, val, out))
            self.assertIn(flag, out)  # named as the owner typed it, not as lane-tunnel's own option
            self.assertEqual(self.e.jlines(".install"), [], (flag, val))
            self.assertEqual(self.e.lines(".systemctl"), [])

    def test_a_failed_tunnel_start_is_stopped_and_forgotten(self):
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=self.link,
                                   env_extra={"STUB_TUNNEL_FAILS": "1"})
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.e.lines(".systemctl")[-1], "--user disable --now lane-tunnel@box.service")
        self.assertFalse((self.e.home / ".config" / "lane-control" / "tunnels" / "box.env").exists())

    def test_tunnel_options_for_a_box_that_is_already_paired_are_said_to_be_unused(self):
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=self.link)
        self.assertEqual(rc, 0, out)
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", "--tunnel-no-enable")
        self.assertEqual(rc, 0, out)
        self.assertIn("desktop lanes: exists", out)
        self.assertIn("--tunnel-no-enable", out)
        self.assertIn("not used", out)
        self.assertIn("lane-tunnel start", out)

    def test_a_tunnel_that_fails_tells_the_owner_about_the_agent_option(self):
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=self.link,
                                   env_extra={"STUB_TUNNEL_FAILS": "1"})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("--tunnel-auth-sock", out)
        self.assertIn("journalctl", out)
        self.assertEqual([x for x in self.e.jlines(".node", self.e.box) if "stdin_sha256" in x], [])

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
                     ["--remove-host", "box;id"],
                     # N9: the Orca + Cotal host path keeps its old ssh lines, so this check is what guards it
                     ["--host=-oProxyCommand=touch_x", str(self.repo), "--orchestrator", "orca-cotal"],
                     [str(self.repo), "--host", "box name", "--orchestrator", "orca-cotal"],
                     [str(self.repo), "--host", "box\nx", "--orchestrator", "orca-cotal"]):
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
                         "--coordinator", "opencode/gateway/m1",
                         env_extra={"LANE_RUN_WAIT": "0"})
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        started = [json.loads(l) for l in (self.e.box / "sessions.log").read_text().splitlines()]
        self.assertEqual([(s["bin"], s["role"]) for s in started], [("opencode2", "coordinator")])

    def test_a_hosted_model_goes_to_the_host_like_any_other(self):
        # owner 2026-11-24: no refusal by model; the owner's pin is passed on unchanged
        self.e.saved("box")
        r = self.e.setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes", "--no-machine-setup",
                         "--coordinator", "opencode/opencode-go/m1")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("lane-tier", r.stderr)
        self.assertTrue(any("opencode/opencode-go/m1" in l for l in self.e.lines(".ssh")))

    def test_the_host_run_appends_its_own_bin_folders_to_path(self):
        # N11: ~/bin and ~/.local/bin go after the system's PATH, so nothing there shadows git or python3.
        for d in ("bin", ".local/bin"):
            (self.e.box / d).mkdir(parents=True, exist_ok=True)
        probe = self.e.box / "path.txt"
        r = subprocess.run([sys.executable, "-c", "import os, runpy, sys; sys.argv = sys.argv[1:]; "
                            "m = runpy.run_path(sys.argv[0], run_name='probe'); m['main'](sys.argv[1:]); "
                            f"open({str(probe)!r}, 'w').write(os.environ['PATH'])",
                            str(SETUP), str(self.repo), "--via-host", "--which-orchestrator"],
                           env={**self.e.env, "HOME": str(self.e.box)}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        parts = probe.read_text().split(":")
        self.assertEqual(parts[-2:], [str(self.e.box / "bin"), str(self.e.box / ".local" / "bin")])

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


class CheckTests(Case):
    """PAIR-1 (lc-lxd): project-setup --check <repo> <host> lists every prerequisite and changes nothing; a row it
    could not read says so and fails the check; a set-up run says what pairing each direction has, and the steps for
    the desktop's link, before the box's server and its own run."""

    def setUp(self):
        super().setUp()
        self.e.env["LANE_DESKTOP_ORCA_PORT"] = str(free_port())  # nothing listens there: loopback only

    def snapshot(self):
        """Every file here and on the box with a hash of its content, but the stub Orca's own state and logs."""
        return {str(p.relative_to(self.e.home)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.e.home.rglob("*") if p.is_file() and not p.is_symlink() and not p.name.startswith("stub.")}

    def check(self, *args, env_extra=None, target=None):
        return subprocess.run([sys.executable, str(SETUP), "--check", target or str(self.repo), "box", *args],
                              env={**self.e.env, **(env_extra or {})}, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)

    def versions(self, here, box):
        st = self.e.stub_state()
        st["app_version"] = here
        self.e.state.write_text(json.dumps(st))
        (self.e.box / "stub.json").write_text(json.dumps({**self.e.box_state(), "app_version": box}))

    def pair(self):
        self.e.saved("box")
        link, _, _ = make_link(free_port())
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=link)
        self.assertEqual(rc, 0, out)

    def test_a_fresh_box_lists_the_steps_and_nothing_changes(self):
        before = self.snapshot()
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)  # what setup does, and the link it asks for, pass
        for line in ("check orca here: ok", "check desktop listener: ok", "check ssh: ok", "check direction 1: to do",
                     "check direction 2: to do", "check systemd here: ok", "check tunnel: to do",
                     "check agent on box: ok", "check lane-control on box: to do", "check: ok", "nothing was changed"):
            self.assertIn(line, r.stdout)
        self.assertIn('Settings > Servers > "Share this computer"', r.stdout)  # the owner's steps, in the line itself
        self.assertIn("127.0.0.1", r.stdout)
        self.assertEqual(self.snapshot(), before)  # no copy, no record, no unit, no env file, here or on the box
        self.assertEqual(self.e.jlines(".install"), [])
        self.assertEqual(self.e.lines(".systemctl"), ["--user show-environment"])
        self.assertFalse(any("tar" in c[-1] or "--box-pairing" in c[-1] for c in self.e.jlines(".ssh")))

    def test_a_paired_box_has_nothing_missing_and_nothing_changes(self):
        self.pair()
        self.versions("1.4.274", "1.4.274")
        before = self.snapshot()
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        for line in ("check direction 1: ok", "check versions: ok", "check direction 2: ok", "check tunnel: ok",
                     "check lane-control on box: ok", "check: ok"):
            self.assertIn(line, r.stdout)
        self.assertEqual(self.snapshot(), before)  # the record, the env file, the box's store: byte for byte
        self.assertEqual(len([x for x in self.e.jlines(".node", self.e.box) if "stdin_sha256" in x]), 1)  # no new link

    def test_versions_on_both_sides_are_compared_and_an_unsaid_one_is_not_ok(self):
        self.e.saved("box")
        self.versions("1.4.274", "1.4.268")
        self.assertIn("check versions: warn (here 1.4.274, box 1.4.268", self.check().stdout)
        self.versions("1.4.274", "1.4.274")
        self.assertIn("check versions: ok (here 1.4.274, box 1.4.274)", self.check().stdout)
        st = self.e.stub_state()
        st.pop("app_version")
        self.e.state.write_text(json.dumps(st))
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("check versions: could not check", r.stdout)

    def test_an_unreachable_box_says_ssh_and_reads_nothing_as_a_no(self):
        self.pair()
        r = self.check(env_extra={"STUB_SSH_DOWN": "1"})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("check ssh: missing", r.stdout)
        self.assertIn("ssh box` once by hand", r.stdout)
        for name in ("direction 2", "agent on box", "lane-control on box"):  # every row is kept, none is a guess
            self.assertIn(f"check {name}: could not check (ssh box did not answer)", r.stdout)
        self.assertNotIn("no link", r.stdout)
        self.assertIn("could not check", r.stdout.splitlines()[-1])

    def test_a_box_whose_orca_cannot_answer_is_not_ok(self):
        self.pair()
        self.versions("1.4.274", "1.4.274")
        cli = self.e.box / ".orca" / "cli" / "bin" / "orca"
        cli.write_text("#!/bin/sh\necho not json\nexit 1\n")
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("check direction 2: could not check", r.stdout)
        self.assertNotIn("has what it needs", r.stdout)

    def test_orca_here_down_makes_direction_one_unread(self):
        st = self.e.stub_state()
        st["orca_up"] = False
        self.e.state.write_text(json.dumps(st))
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("check orca here: missing", r.stdout)
        self.assertIn("check direction 1: could not check", r.stdout)

    def test_a_missing_agent_and_a_saved_but_silent_server_are_named(self):
        self.e.saved("box")
        st = self.e.stub_state()
        st["environments"][0]["reachable"] = False
        self.e.state.write_text(json.dumps(st))
        (self.e.bin / "opencode2").unlink()  # the box's PATH is the test's: no OpenCode there now
        r = self.check("--coordinator", "opencode/gw/chat")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("check direction 1: missing", r.stdout)
        self.assertIn("orca-tunnel-box.service", r.stdout)
        self.assertIn("check agent on box: missing (opencode2", r.stdout)

    def test_the_gateway_key_is_the_coordinators_providers_and_never_its_value(self):
        cfg = self.e.box / ".config" / "opencode"
        cfg.mkdir(parents=True)
        (cfg / "opencode.json").write_text(json.dumps({
            "mcp": {"tool": {"environment": {"TOKEN": "{env:MCP_TEST_TOKEN}"}}},
            "provider": {"other": {"options": {"apiKey": "{env:OTHER_TEST_KEY}"}},
                         "gw": {"options": {"baseURL": "http://127.0.0.1:4000/v1", "apiKey": "{env:GW_TEST_KEY}"}}}}))
        r = self.check("--coordinator", "opencode/gw/chat", env_extra={"GW_TEST_KEY": "sk-not-for-output-123"})
        self.assertIn("check gateway key: ok (GW_TEST_KEY is set", r.stdout)
        self.assertNotIn("sk-not-for-output-123", r.stdout + r.stderr)
        env = {k: v for k, v in self.e.env.items() if k != "GW_TEST_KEY"}
        r = subprocess.run([sys.executable, str(SETUP), "--check", str(self.repo), "box", "--coordinator",
                            "opencode/gw/chat"], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertIn("check gateway key: warn (GW_TEST_KEY", r.stdout)
        self.assertNotIn("MCP_TEST_TOKEN", r.stdout)

    def test_a_desktop_listening_beyond_loopback_is_a_warning(self):
        import socket
        with socket.socket() as s:
            s.bind(("0.0.0.0", 0))
            s.listen()
            r = self.check(env_extra={"LANE_DESKTOP_ORCA_PORT": str(s.getsockname()[1])})
        self.assertIn("check desktop listener: warn", r.stdout)
        self.assertIn("A 127.0.0.1 link does not change it", r.stdout)

    def test_check_needs_a_host_and_a_target_setup_would_take(self):
        r = subprocess.run([sys.executable, str(SETUP), "--check", str(self.repo)], env=self.e.env,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("--check takes a repo and a host", r.stderr)
        r = self.check(target="not/absolute")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("absolute (or ~/) path on the host", r.stderr)
        self.assertEqual(self.e.jlines(".ssh"), [])

    def test_check_with_a_flag_that_acts_is_refused_before_anything_runs(self):
        self.pair()
        before, ssh, sysd = self.snapshot(), len(self.e.jlines(".ssh")), len(self.e.lines(".systemctl"))
        for args in (("--remove-host", "box"), ("--refresh",), ("--suggest-tiers", "gw"), ("--trust-folder",)):
            r = subprocess.run([sys.executable, str(SETUP), "--check", *(() if args[0] == "--remove-host" else
                                                                        (str(self.repo), "box")), *args],
                               env=self.e.env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 2, (args, r.stdout, r.stderr))
            self.assertIn(f"--check changes nothing, so it does not go with {args[0]}", r.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((len(self.e.jlines(".ssh")), len(self.e.lines(".systemctl"))), (ssh, sysd))

    def test_direction_one_to_do_needs_systemd_here_even_without_desktop_lanes(self):
        r = self.check("--no-desktop-lanes", env_extra={"STUB_NO_SYSTEMD": "1"})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("check systemd here: missing", r.stdout)
        self.assertIn("check direction 2: skipped", r.stdout)

    def test_a_server_source_that_is_not_there_is_named(self):
        r = self.check("--orca-from", str(self.e.home / "no-such-slots"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("check orca source: missing", r.stdout)

    def test_the_steps_for_the_link_come_before_the_boxs_server_and_its_run(self):
        link, _, _ = make_link(free_port())
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", answer=link)
        self.assertEqual(rc, 0, out)
        self.assertIn("pairing: direction 1 to do", out)
        steps = out.index("Before setup asks")
        self.assertLess(steps, out.index("orca server: created"))
        for s in ('Settings > Servers > "Share this computer"', "127.0.0.1", "hidden prompt", "ssh's stdin",
                  "orca pair"):
            self.assertIn(s, out[steps:steps + 1200])

    def test_a_cotal_box_project_gets_no_steps_for_a_link_it_is_never_asked(self):
        (self.repo / ".cotal" / "agents").mkdir(parents=True)
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("Before setup asks", out)
        self.assertNotIn("pairing: direction", out)

    def test_a_fresh_host_run_without_a_terminal_asks_nothing(self):
        r = self.e.setup(str(self.repo), "box", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("pairing: direction 1 to do", r.stdout)
        self.assertIn("pairing: direction 2 skipped", r.stdout)
        self.assertNotIn("Before setup asks", r.stdout)


class LoopbackTests(unittest.TestCase):
    def test_only_a_non_loopback_listener_counts_as_exposed(self):
        # m6 / n1: what the box checks after the tunnel is up (Linux /proc/net/tcp*).
        import socket
        m = load()
        cases = [(socket.AF_INET, "127.0.0.1", False), (socket.AF_INET, "0.0.0.0", True)]
        if socket.has_ipv6:
            cases += [(socket.AF_INET6, "::1", False), (socket.AF_INET6, "::ffff:127.0.0.1", False),
                      (socket.AF_INET6, "::", True)]
        for family, address, exposed in cases:
            s = socket.socket(family)
            try:
                if family == socket.AF_INET6:
                    s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                try:
                    s.bind((address, 0))
                except OSError:
                    continue  # no such address here (an IPv6-less container)
                s.listen()
                self.assertEqual(m.listens_beyond_loopback(s.getsockname()[1]), exposed, address)
            finally:
                s.close()


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


def offer_of(link):
    code = link.split("code=", 1)[1] if link.startswith("orca://") else link
    return json.loads(base64.urlsafe_b64decode(code + "=" * (-len(code) % 4)))


def rest_sha(offer):
    rest = {k: v for k, v in offer.items() if k != "endpoint"}
    return hashlib.sha256(json.dumps(rest, sort_keys=True).encode()).hexdigest()


class TwoPortTests(Case):
    """TUN-1: the desktop's port (the share link's) and the box's port (what the box dials) can differ."""
    def setUp(self):
        super().setUp()
        self.e.saved("box")
        self.port, self.remote = free_port(), free_port()
        self.link, self.token, self.code = make_link(self.port)

    def records(self):
        return (json.loads((self.e.home / ".config" / "lane-control" / "hosts" / "box.json").read_text()),
                (self.e.home / ".config" / "lane-control" / "tunnels" / "box.env").read_text())

    def test_the_box_dials_its_own_port_and_the_rest_of_the_link_is_unchanged(self):
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", "--tunnel-remote-port", str(self.remote),
                                   answer=self.link)
        self.assertEqual(rc, 0, out)
        rec, env = self.records()
        self.assertEqual((rec["tunnel_port"], rec["tunnel_remote_port"]), (self.port, self.remote))
        self.assertIn(f"PORT={self.port}\n", env)
        self.assertIn(f"REMOTE_PORT={self.remote}\n", env)
        node = [x for x in self.e.jlines(".node", self.e.box) if "stdin_sha256" in x]
        self.assertEqual(len(node), 1, node)
        self.assertEqual(node[0]["endpoint"], f"ws://127.0.0.1:{self.remote}")
        self.assertEqual(node[0]["rest_sha256"], rest_sha(offer_of(self.link)))  # same token, key and the rest
        self.assertIn(f"127.0.0.1:{self.remote} on box reaches 127.0.0.1:{self.port} here", out)
        # neither the link nor the rewritten one is anywhere
        rewritten = load().link_for_port(self.link, self.port, self.remote)
        self.assertEqual(hashlib.sha256(rewritten.encode()).hexdigest(), node[0]["stdin_sha256"])
        DirectionTwoTests.assert_nowhere(self, out, self.link, self.token, self.code)
        DirectionTwoTests.assert_nowhere(self, out, rewritten, self.token, rewritten.split("code=", 1)[1])
        self.assertNotIn("--tunnel-remote-port", " ".join(" ".join(c) for c in self.e.jlines(".ssh")))  # local only

    def test_the_box_port_is_checked_not_the_desktops(self):
        import socket
        s = socket.socket()  # the box's own Orca server on the link's port: the box's end goes elsewhere
        s.bind(("127.0.0.1", self.port))
        s.listen()
        self.addCleanup(s.close)
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", "--tunnel-remote-port", str(self.remote),
                                   answer=self.link)
        self.assertEqual(rc, 0, out)

    def test_a_busy_box_port_is_refused_naming_that_port(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", self.remote))
        s.listen()
        self.addCleanup(s.close)
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", "--tunnel-remote-port", str(self.remote),
                                   answer=self.link)
        self.assertNotEqual(rc, 0, out)
        self.assertIn(f"port {self.remote} on box is in use", out)
        self.assertEqual([x for x in self.e.jlines(".node", self.e.box) if "stdin_sha256" in x], [])

    def test_a_bad_remote_port_is_refused_before_anything_runs(self):
        for bad in ("0", "81", "1023", "70000", "x"):  # below 1024: a normal user's ssh -R cannot open it (review n1)
            rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", "--tunnel-remote-port", bad,
                                       answer=self.link)
            self.assertEqual(rc, 2, (bad, out))
            self.assertNotIn("share link for", out)
            self.assertEqual(self.e.jlines(".install"), [], bad)

    def test_an_already_paired_box_names_both_ports_from_its_record_and_an_old_record_reads_as_one(self):
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", "--tunnel-remote-port", str(self.remote),
                                   answer=self.link)
        self.assertEqual(rc, 0, out)
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", "--tunnel-remote-port", "6999")
        self.assertEqual(rc, 0, out)
        self.assertIn("--tunnel-remote-port", out)
        self.assertIn("not used", out)
        self.assertIn(f"lane-tunnel start <options> --remote-port {self.remote} box {self.port}", out)
        hosts = self.e.home / ".config" / "lane-control" / "hosts" / "box.json"
        rec = json.loads(hosts.read_text())
        rec.pop("tunnel_remote_port")  # a record from before TUN-1
        hosts.write_text(json.dumps(rec))
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", "--tunnel-no-enable")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"lane-tunnel start <options> --remote-port {self.port} box {self.port}", out)

    def test_a_paired_box_whose_tunnel_is_down_gets_the_line_from_the_record_before_the_link_is_asked(self):
        # review m1: after a reboot or a locked card the box still has this machine's environment but cannot reach it
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", "--tunnel-remote-port", str(self.remote),
                                   answer=self.link)
        self.assertEqual(rc, 0, out)
        subprocess.run(["sh", str(TUNNEL), "stop", "box"], env=self.e.env, capture_output=True)
        box = self.e.box_state()
        for env in box["environments"]:
            env["reachable"] = False
        (self.e.box / "stub.json").write_text(json.dumps(box))
        rc, out = self.e.pty_setup(str(self.repo), "box", "--no-start", "--tunnel-no-enable", answer="")
        self.assertEqual(rc, 0, out)
        self.assertIn("tunnel: down", out)
        line = f"lane-tunnel start <options> --remote-port {self.remote} box {self.port}"
        self.assertIn(line, out)
        self.assertLess(out.index(line), out.index("share link for box"))  # before the question
        self.assertIn("desktop lanes: skipped", out)


class LinkRewriteTests(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_only_the_endpoints_port_changes(self):
        link, token, code = make_link(6768)
        for given in (link, code):  # the link, or the bare code
            new = self.m.link_for_port(given, 6768, 6790)
            self.assertTrue(new.startswith("orca://pair?code="), "the link form Orca writes")
            self.assertNotIn("=", new.split("code=", 1)[1])  # unpadded base64url, as Orca encodes it
            before, after = offer_of(link), offer_of(new)
            self.assertEqual(after["endpoint"], "ws://127.0.0.1:6790")
            self.assertEqual(rest_sha(before), rest_sha(after))
            self.assertEqual(list(before), list(after))  # same fields, same order
            self.assertEqual(self.m.link_port(new), 6790)

    def test_the_same_port_leaves_the_link_as_it_is(self):
        link, _, _ = make_link(6768)
        self.assertIs(self.m.link_for_port(link, 6768, 6768), link)

    def test_an_endpoint_that_is_not_plainly_this_address_and_port_is_refused_without_showing_it(self):
        for ep in ("ws://user@127.0.0.1:6768", "ws://127.0.0.1:6768/x?y=1#z"):
            payload = {"v": 2, "endpoint": ep, "deviceToken": "dvtok-abc", "publicKeyB64": "k"}
            code = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
            if ep.endswith("#z"):  # a path is kept: Orca dials the endpoint as written
                new = self.m.link_for_port(code, 6768, 6790)
                self.assertEqual(offer_of(new)["endpoint"], "ws://127.0.0.1:6790/x?y=1#z")
                continue
            with self.assertRaises(self.m.Fail) as cm:
                self.m.link_for_port(code, 6768, 6790)
            self.assertNotIn(code, str(cm.exception))
            self.assertNotIn("dvtok-abc", str(cm.exception))


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
        self.assertIn("127.0.0.1:$${REMOTE_PORT:-$${PORT}}:127.0.0.1:$${PORT}", unit)  # one port when no REMOTE_PORT
        self.assertIn("Restart=always", unit)
        env = (self.e.home / ".config" / "lane-control" / "tunnels" / "user-box.example.env").read_text()
        self.assertIn("SSH_HOST=user@box.example", env)
        self.assertIn(f"SSH_CONFIG={self.e.home}/trial/ssh_config", env)
        self.assertIn("--user enable --now lane-tunnel@user-box.example.service", self.e.lines(".systemctl"))

    def test_a_changed_port_restarts_a_running_tunnel(self):
        self.tunnel("start", "box", "6768")
        self.tunnel("start", "box", "6790")
        self.assertIn("--user restart lane-tunnel@box.service", self.e.lines(".systemctl"))

    def test_the_boxs_port_can_differ_from_this_machines(self):
        r = self.tunnel("start", "--remote-port", "6790", "box", "6768")
        self.assertEqual(r.returncode, 0, r.stderr)
        env = self.start_env()
        self.assertIn("PORT=6768\n", env)
        self.assertIn("REMOTE_PORT=6790\n", env)
        self.assertIn("127.0.0.1:6790 on box reaches 127.0.0.1:6768 here", r.stdout)
        unit = (self.e.home / ".config" / "systemd" / "user" / "lane-tunnel@.service").read_text()
        self.assertIn("127.0.0.1:$${REMOTE_PORT:-$${PORT}}:127.0.0.1:$${PORT}", unit)

    def test_one_port_writes_the_env_file_as_before(self):
        # a tunnel started before TUN-1 has no REMOTE_PORT: the same start must not change its file (or restart it)
        for args in (("box", "6768"), ("--remote-port", "6768", "box", "6768"), ("--remote-port", "06768", "box", "6768")):
            r = self.tunnel("start", *args)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("REMOTE_PORT", self.start_env())
            self.assertIn("127.0.0.1:6768 on box reaches 127.0.0.1:6768 here", r.stdout)
        self.assertNotIn("--user restart lane-tunnel@box.service", self.e.lines(".systemctl"))

    def test_a_changed_remote_port_restarts_a_running_tunnel(self):
        self.tunnel("start", "box", "6768")
        self.tunnel("start", "--remote-port", "6790", "box", "6768")
        self.assertIn("--user restart lane-tunnel@box.service", self.e.lines(".systemctl"))

    def test_a_bad_remote_port_is_refused(self):
        for p in ("0", "70000", "x", ""):
            r = self.tunnel("start", "--remote-port", p, "box", "6768")
            self.assertEqual(r.returncode, 2, p)
        self.assertEqual(self.tunnel("start", "box", "6768", "--remote-port").returncode, 2)
        self.assertEqual(self.tunnel("stop", "--remote-port", "6790", "box").returncode, 2)
        self.assertFalse((self.e.home / ".config" / "lane-control" / "tunnels").exists())

    def test_the_unit_reads_an_old_env_files_one_port_as_both(self):
        line = next(l for l in UNIT.read_text().splitlines() if l.startswith("ExecStart="))
        body = line[len("ExecStart=/bin/sh -c '"):-1].replace("$$", "$")  # systemd turns $$ into $
        log = self.e.home / "ran"
        fake = self.e.home / "fakebin"
        fake.mkdir()
        (fake / "ssh").write_text(f'#!/bin/sh\necho "$*" >> {log}\n')
        (fake / "ssh").chmod(0o755)
        base = {"PATH": f"{fake}:/usr/bin:/bin", "SSH_HOST": "box", "PORT": "6768"}
        for extra in ({}, {"REMOTE_PORT": "6790"}):
            subprocess.run(["sh", "-c", body], env={**base, **extra}, check=True)
        ran = log.read_text().splitlines()
        self.assertIn("-R 127.0.0.1:6768:127.0.0.1:6768 box", ran[0])
        self.assertIn("-R 127.0.0.1:6790:127.0.0.1:6768 box", ran[1])

    def test_status_and_stop_work_on_a_tunnel_started_before(self):
        tunnels = self.e.home / ".config" / "lane-control" / "tunnels"
        tunnels.mkdir(parents=True)
        (tunnels / "box.env").write_text(f"SSH_HOST=box\nPORT={free_port()}\nSSH_CONFIG=\nPATH=/usr/bin:/bin\n")
        self.assertEqual(self.tunnel("status", "box").returncode, 3)  # not running yet: systemctl's inactive
        subprocess.run(["systemctl", "--user", "start", "lane-tunnel@box.service"], env=self.e.env, check=True)
        self.assertEqual(self.tunnel("status", "box").returncode, 0)  # running from the old file (review n2)
        r = self.tunnel("stop", "box")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((tunnels / "box.env").exists())
        self.assertIn("--user disable --now lane-tunnel@box.service", self.e.lines(".systemctl"))
        self.assertEqual(self.tunnel("status", "box").returncode, 3)

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

    def start_env(self, name="box"):
        return (self.e.home / ".config" / "lane-control" / "tunnels" / f"{name}.env").read_text()

    def wrapper(self, name="ssh-wrapper"):
        p = self.e.home / name
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)
        return str(p)

    def test_no_agent_socket_line_unless_one_is_given(self):
        self.tunnel("start", "box", "6768")
        self.assertNotIn("SSH_AUTH_SOCK", self.start_env())
        self.assertNotIn("LANE_TUNNEL_SSH", self.start_env())

    def test_an_agent_socket_goes_in_the_env_file(self):
        r = self.tunnel("start", "--auth-sock", "/tmp/lane-test/card-agent.sock", "box", "6768")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SSH_AUTH_SOCK=/tmp/lane-test/card-agent.sock\n", self.start_env() + "\n")

    def test_the_agent_socket_can_come_from_the_environment(self):
        r = self.tunnel("start", "box", "6768", env_extra={"LANE_TUNNEL_AUTH_SOCK": "/tmp/lane-test/a.sock"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SSH_AUTH_SOCK=/tmp/lane-test/a.sock", self.start_env())

    def test_a_changed_agent_socket_restarts_a_running_tunnel(self):
        self.tunnel("start", "box", "6768")
        self.tunnel("start", "--auth-sock", "/tmp/lane-test/a.sock", "box", "6768")
        self.assertIn("--user restart lane-tunnel@box.service", self.e.lines(".systemctl"))

    def test_an_ssh_wrapper_goes_in_the_env_file(self):
        w = self.wrapper()
        r = self.tunnel("start", "--ssh", w, "box", "6768")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"LANE_TUNNEL_SSH={w}", self.start_env())

    def test_the_unit_runs_the_wrapper_when_there_is_one_and_ssh_otherwise(self):
        unit = UNIT.read_text()
        line = next(l for l in unit.splitlines() if l.startswith("ExecStart="))
        body = line[len("ExecStart=/bin/sh -c '"):-1].replace("$$", "$")  # systemd turns $$ into $
        log = self.e.home / "ran"
        fake = self.e.home / "fakebin"
        fake.mkdir()
        (fake / "ssh").write_text(f'#!/bin/sh\necho "plain $*" >> {log}\n')
        (fake / "ssh").chmod(0o755)
        w = self.e.home / "wrap"
        w.write_text(f'#!/bin/sh\necho "wrapped $*" >> {log}\n')
        w.chmod(0o755)
        base = {"PATH": f"{fake}:/usr/bin:/bin", "SSH_HOST": "box", "PORT": "6768"}
        for extra in ({}, {"LANE_TUNNEL_SSH": str(w)}):
            subprocess.run(["sh", "-c", body], env={**base, **extra}, check=True)
        ran = log.read_text().splitlines()
        self.assertTrue(ran[0].startswith("plain -N"), ran)
        self.assertTrue(ran[1].startswith("wrapped -N"), ran)
        self.assertIn("-R 127.0.0.1:6768:127.0.0.1:6768 box", ran[1])

    def test_values_that_could_add_a_line_or_name_nothing_usable_are_refused(self):
        w = self.wrapper()
        (self.e.home / "plain").write_text("x")
        cases = [("--auth-sock", "/run/a.sock\nPATH=/evil"), ("--auth-sock", "run/a.sock"), ("--auth-sock", ""),
                 ("--ssh", "/nonexistent/ssh"), ("--ssh", "ssh"), ("--ssh", str(self.e.home / "plain")),
                 ("--ssh", w + "\nPATH=/evil")]
        for flag, val in cases:
            r = self.tunnel("start", flag, val, "box", "6768")
            self.assertEqual(r.returncode, 2, (flag, val, r.stderr))
        r = self.tunnel("start", "box", "6768", env_extra={"LANE_SSH_CONFIG": "/etc/x\nPATH=/evil"})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse((self.e.home / ".config" / "lane-control" / "tunnels").exists())  # refused before writing
        self.assertEqual(self.e.lines(".systemctl"), [])

    def test_check_runs_the_same_rules_without_a_host_or_systemd(self):
        w = self.wrapper()
        good = self.tunnel("check", "--auth-sock", "/tmp/lane-test/a.sock", "--ssh", w)
        self.assertEqual(good.returncode, 0, good.stderr)
        for flag, val in (("--auth-sock", "/run/a.sock "), ("--auth-sock", "/run/a\\b.sock"), ("--auth-sock", '"/x'),
                          ("--auth-sock", "a.sock"), ("--ssh", w + " "), ("--ssh", "ssh"), ("--ssh", str(self.e.home))):
            r = self.tunnel("check", flag, val)
            self.assertEqual(r.returncode, 2, (flag, val, r.stderr))
        self.assertEqual(self.tunnel("check", env_extra={"PATH": "/usr/bin:/bin\\"}).returncode, 2)
        self.assertEqual(self.e.lines(".systemctl"), [])
        self.assertFalse((self.e.home / ".config" / "lane-control" / "tunnels").exists())

    def test_unknown_options_and_options_on_other_commands_are_refused(self):
        self.assertEqual(self.tunnel("start", "--bogus", "box", "6768").returncode, 2)
        self.assertEqual(self.tunnel("stop", "--no-enable", "box").returncode, 2)

    def test_no_enable_starts_the_unit_without_enabling_it(self):
        r = self.tunnel("start", "--no-enable", "box", "6768")
        self.assertEqual(r.returncode, 0, r.stderr)
        sysctl = self.e.lines(".systemctl")
        self.assertIn("--user start lane-tunnel@box.service", sysctl)
        self.assertFalse([l for l in sysctl if " enable" in l], sysctl)
        self.assertIn("SSH_HOST=box", self.start_env())
        self.assertIn("not enabled", r.stdout)  # says what it did, and that a login does not bring it back

    def test_no_enable_after_an_enabled_start_disables_it_so_a_login_does_not_loop(self):
        self.tunnel("start", "box", "6768")
        r = self.tunnel("start", "--no-enable", "box", "6768")
        self.assertEqual(r.returncode, 0, r.stderr)
        sysctl = self.e.lines(".systemctl")
        self.assertIn("--user disable lane-tunnel@box.service", sysctl)
        self.assertLess(sysctl.index("--user disable lane-tunnel@box.service"),
                        len(sysctl) - 1 - sysctl[::-1].index("--user start lane-tunnel@box.service"))

    def test_stop_after_a_no_enable_start_leaves_it_stopped_and_disabled(self):
        self.tunnel("start", "--no-enable", "box", "6768")
        r = self.tunnel("stop", "box")
        self.assertEqual(r.returncode, 0, r.stderr)
        sysctl = self.e.lines(".systemctl")
        self.assertEqual(sysctl[-1], "--user disable --now lane-tunnel@box.service")
        self.assertFalse([l for l in sysctl if " enable" in l], sysctl)
        self.assertFalse((self.e.home / ".config" / "lane-control" / "tunnels" / "box.env").exists())

    def test_a_start_that_does_not_come_up_points_at_the_journal(self):
        for flags in ((), ("--no-enable",)):
            r = self.tunnel("start", *flags, "box", "6768", env_extra={"STUB_TUNNEL_FAILS": "1"})
            self.assertEqual(r.returncode, 1, flags)
            self.assertIn("did not come up", r.stderr)
            self.assertIn("journalctl --user -u lane-tunnel@box.service", r.stderr)
            self.assertTrue((self.e.home / ".config" / "lane-control" / "tunnels" / "box.env").exists())  # to retry
        # only a unit that was never meant to stay is stopped again; an always-on one keeps retrying
        r = self.tunnel("start", "--no-enable", "box", "6768", env_extra={"STUB_TUNNEL_FAILS": "1"})
        self.assertEqual(self.e.lines(".systemctl")[-1], "--user disable --now lane-tunnel@box.service")

    def test_a_failure_names_the_agent_when_the_unit_had_none(self):
        r = self.tunnel("start", "box", "6768", env_extra={"STUB_TUNNEL_FAILS": "1"})
        self.assertIn("--auth-sock", r.stderr)
        r = self.tunnel("start", "--auth-sock", "/tmp/lane-test/a.sock", "box", "6768",
                        env_extra={"STUB_TUNNEL_FAILS": "1"})
        self.assertIn("/tmp/lane-test/a.sock", r.stderr)  # which agent it used, so the owner can check it is unlocked

    def test_a_unit_that_comes_up_and_then_fails_is_caught_during_the_settle_time(self):
        r = self.tunnel("start", "box", "6768", env_extra={"STUB_TUNNEL_FLAPS": "1", "LANE_TUNNEL_SETTLE": "2"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("journalctl --user -u lane-tunnel@box.service", r.stderr)

    def test_the_unit_never_gives_up_and_backs_off_to_five_minutes(self):
        unit = UNIT.read_text()
        self.assertRegex(unit.split("[Service]")[0], r"(?m)^StartLimitIntervalSec=0$")
        self.assertNotIn("StartLimitBurst", unit)
        for line in ("Restart=always", "RestartSec=10", "RestartSteps=6", "RestartMaxDelaySec=5min"):
            self.assertRegex(unit, r"(?m)^" + line + "$")
        self.assertIn("-o ConnectTimeout=15", unit)  # a dropped-packet network does not hang each try for minutes

    def test_an_older_systemd_gets_a_fixed_restart_delay_it_understands(self):
        unit = self.e.home / ".config" / "systemd" / "user" / "lane-tunnel@.service"
        self.tunnel("start", "box", "6768", env_extra={"STUB_SYSTEMD": "253"})
        old = unit.read_text()
        service = old.split("[Service]")[1]  # (the header comment names the keys)
        self.assertRegex(service, r"(?m)^RestartSec=30$")
        self.assertNotIn("RestartSteps", service)
        self.assertNotIn("RestartMaxDelaySec", service)
        self.assertRegex(old, r"(?m)^StartLimitIntervalSec=0$")
        self.tunnel("start", "box", "6768", env_extra={"STUB_SYSTEMD": "254"})
        new = unit.read_text()
        for line in ("RestartSec=10", "RestartSteps=6", "RestartMaxDelaySec=5min"):
            self.assertRegex(new, r"(?m)^" + line + "$")

    def test_a_failed_check_leaves_an_always_on_tunnel_enabled_and_says_it_keeps_retrying(self):
        self.tunnel("start", "box", "6768")  # an always-on tunnel that worked
        before = len(self.e.lines(".systemctl"))
        r = self.tunnel("start", "box", "6768", env_extra={"STUB_TUNNEL_FLAPS": "1", "LANE_TUNNEL_SETTLE": "2"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("journalctl --user -u lane-tunnel@box.service", r.stderr)
        self.assertIn("keeps retrying", r.stderr)
        self.assertIn("lane-tunnel stop box", r.stderr)
        later = self.e.lines(".systemctl")[before:]
        self.assertFalse([l for l in later if "disable" in l or " stop" in l], later)
        self.assertTrue((self.e.home / "enabled-lane-tunnel@box.service").exists())
        self.assertTrue((self.e.home / ".config" / "lane-control" / "tunnels" / "box.env").exists())

    def test_the_diagnosis_names_an_authentication_failure(self):
        r = self.tunnel("start", "--auth-sock", "/tmp/lane-test/a.sock", "box", "6768",
                        env_extra={"STUB_TUNNEL_FAILS": "1"})
        self.assertIn("could not log in", r.stderr)

    def test_values_systemd_would_read_differently_are_refused(self):
        w = self.wrapper()
        cases = [("--auth-sock", "/run/a.sock\\"), ("--auth-sock", "/run/a\\b.sock"), ("--auth-sock", '"/run/a.sock'),
                 ("--auth-sock", "'/run/a.sock"), ("--auth-sock", "/run/a.sock "), ("--auth-sock", " /run/a.sock"),
                 ("--ssh", w + " ")]
        for flag, val in cases:
            r = self.tunnel("start", flag, val, "box", "6768")
            self.assertEqual(r.returncode, 2, (flag, val, r.stderr))
        for extra in ({"PATH": "/usr/bin:/bin\\"}, {"LANE_SSH_CONFIG": "/etc/x\\"}):
            r = self.tunnel("start", "box", "6768", env_extra=extra)
            self.assertEqual(r.returncode, 2, (extra, r.stderr))
        self.assertEqual(self.e.lines(".systemctl"), [])

    def test_no_enable_that_cannot_disable_an_earlier_enable_says_so(self):
        self.tunnel("start", "box", "6768")
        r = self.tunnel("start", "--no-enable", "box", "6768", env_extra={"STUB_DISABLE_FAILS": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("still enabled", r.stderr)
        self.assertNotIn("not enabled", r.stdout)

    def test_an_agent_socket_that_is_not_a_socket_is_noted_but_not_refused(self):
        r = self.tunnel("start", "--auth-sock", "/tmp/lane-test/not-there.sock", "box", "6768")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("not a socket", r.stderr)
        r = self.tunnel("start", "box", "6768")
        self.assertNotIn("not a socket", r.stderr)


class DocTests(unittest.TestCase):
    def test_remote_doc_covers_both_directions_removal_and_revoking(self):
        text = REMOTE_DOC.read_text()
        for s in ("project-setup <repo> <host>", "orca-host-install", "Share this computer", "127.0.0.1",
                  "--remove-host", "revoke", "lane-tunnel@", "paired_environment", "never"):
            self.assertIn(s, text, s)

    def test_remote_doc_covers_the_card_tunnel_and_its_failure(self):
        text = REMOTE_DOC.read_text()
        for s in ("--auth-sock", "SSH_AUTH_SOCK", "--ssh", "--no-enable", "--tunnel-auth-sock", "--tunnel-no-enable",
                  "journalctl", "smartcard", "keeps retrying", "5 minutes", "systemctl --user restart lane-tunnel@"):
            self.assertIn(s, text, s)
        self.assertNotIn("5 times in 15 minutes", text)  # the unit never gives up now

    def test_remote_doc_says_how_to_pin_the_box_server(self):
        text = " ".join(REMOTE_DOC.read_text().split())
        for s in ("--orca-version", "--orca-from", "--orca-bundle", "orca-host-install", "known-good", "server too old",
                  "from <source>", "app version", "packaged app", "ORCA_ARTIFACTS", ".version"):
            self.assertIn(s, text, s)

    def test_remote_doc_reserves_the_orca_tunnel_ports(self):
        text = " ".join(REMOTE_DOC.read_text().split())
        self.assertRegex(text, r"[Pp]orts 6769 and up are reserved for Orca tunnels")
        self.assertIn("6769-6868", text)  # the range project-setup searches, so an estate knows where the edge is
        m = re.search(r"range\(6769, (\d+)\)", (ROOT / "core" / "scripts" / "project-setup").read_text())
        self.assertEqual(m.group(1), "6869")  # the doc's 6769-6868 and the code agree

    def test_remote_doc_says_when_the_tunnel_is_not_needed(self):
        text = " ".join(REMOTE_DOC.read_text().split())
        self.assertIn("Do you need the tunnel?", text)
        for s in ("Tailscale", "LAN", "NAT", "outbound ssh", "--tunnel-remote-port", "--remote-port",
                  "tunnel_remote_port", "refuses", "every interface (0.0.0.0)", "firewall", "not run here"):
            self.assertIn(s, text, s)  # review M1: a network link widens the desktop to every interface
        self.assertNotIn("listening on that address", text)

    def test_the_docs_put_the_check_first_and_keep_the_link_a_hand_step(self):
        # PAIR-1 (lc-lxd): orca pair widens the desktop, so the 127.0.0.1 link stays the owner's, and --check comes first
        remote = " ".join(REMOTE_DOC.read_text().split())
        for s in ("## Check first", "project-setup --check <repo> <host>", "changes nothing", "In setup today this is a hand step",
                  "`orca pair`", "--address 127.0.0.1", "refusing to widen to all interfaces",
                  "docs/research/2026-11-25-orca-pairing.md", "Orca on your SSH hosts"):
            self.assertIn(s, remote, s)
        self.assertLess(remote.index("## Check first"), remote.index("## Direction 1"))
        guide = " ".join((ROOT / "docs" / "getting-started.md").read_text().split())
        self.assertLess(guide.index("project-setup --check ~/code/<project> example-host"),
                        guide.index("Then `project-setup ~/code/<project> example-host`"))
        self.assertIn('"Share this computer", choosing 127.0.0.1', guide)
        research = ROOT / "docs" / "research" / "2026-11-25-orca-pairing.md"
        text = research.read_text()
        for s in ("ensureNetworkExposure", "this-computer", "Orca on your SSH hosts", "airgap", "not supported"):
            self.assertIn(s, text, s)

    def test_the_coordinator_persona_names_the_stored_key(self):
        text = (ROOT / "orchestrators" / "native-orca" / "personas" / "coordinator.md").read_text()
        self.assertIn("paired_environment", text)

    def test_shell_scripts_parse(self):
        self.assertEqual(subprocess.run(["sh", "-n", str(TUNNEL)]).returncode, 0)


if __name__ == "__main__":
    unittest.main()


class InstallOnBoxTests(Case):
    """INST-1 (lc-7gk): the box gets the desktop's installed tree, whole and at the same sha, and renders its own
    current path."""

    def test_the_box_gets_the_whole_tree_at_the_same_sha(self):
        r = self.e.setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes", "--no-machine-setup")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        sha = git("rev-parse", "HEAD", cwd=ROOT)
        self.assertIn(f"lane-control: copied {sha} on box", r.stdout)
        broot = self.e.box / ".local" / "share" / "lane-control"
        self.assertEqual(os.readlink(broot / "current"), sha)
        listed = set(git("ls-tree", "-r", "--name-only", sha, cwd=ROOT).split())
        laid = {str(p.relative_to(broot / sha)) for p in (broot / sha).rglob("*")
                if (p.is_file() or p.is_symlink()) and "__pycache__" not in p.parts}
        self.assertEqual(laid, listed | {".lane-control-sha"})
        text = (self.repo / ".lane-control" / "coordinator.md").read_text()
        self.assertIn(str(broot / "current"), text)
        self.assertNotIn(str(ROOT), text)
        r = self.e.setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes", "--no-machine-setup")
        self.assertIn(f"lane-control: exists {sha} on box", r.stdout)


class RemoveHostOldBoxTests(RemoveHostTests):
    """INST-1 review M2: --remove-host on a box set up before the install (a flat copy, no `current`)."""

    def setUp(self):
        super().setUp()
        broot = self.e.box / ".local" / "share" / "lane-control"
        tree = broot / os.readlink(broot / "current")
        (broot / "current").unlink()
        for p in tree.iterdir():  # the old layout: the files straight in ~/.local/share/lane-control
            shutil.move(str(p), str(broot / p.name))
        tree.rmdir()


class BoxShellTests(Case):
    """INST-1 review M3: every command setup sends a box is `sh -c '<script>'`, so a fish or csh login shell runs it."""

    def test_every_box_command_is_wrapped_in_sh(self):
        r = self.e.setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes", "--no-machine-setup")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        cmds = [c[-1] for c in self.e.jlines(".ssh")]
        self.assertTrue(cmds)
        for c in cmds:
            self.assertTrue(c.startswith("sh -c '") or c.startswith("python3 - "), c)

    def test_show_over_a_host_sends_and_switches_nothing(self):
        self.e.setup(str(self.repo), "box", "--no-start", "--no-desktop-lanes", "--no-machine-setup")
        lc = self.e.home / ".local" / "share" / "lane-control"
        before = os.readlink(lc / "current")
        n = len(self.e.jlines(".ssh"))
        r = subprocess.run([sys.executable, str(SETUP), str(self.repo), "box", "--show"], env=self.e.env,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertFalse(any("tar -C" in c[-1] for c in self.e.jlines(".ssh")[n:]))
        self.assertEqual(os.readlink(lc / "current"), before)
