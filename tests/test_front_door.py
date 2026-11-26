"""CLI-1 (lc-35h): one front door, `lane-control project|machine|improve ...` (core/frontdoor.py, core/scripts/
lane-control). Every old command has a form; a form runs the same script with the same arguments; the old names keep
working and point a person, and only a person, at the new form.

The dispatch runs against a scratch tree whose old scripts are stubs that print what they were given."""
import importlib.util
import json
import os
import pty
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "core" / "scripts"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


F = load("lc_frontdoor_t", ROOT / "core" / "frontdoor.py")

STUB = """#!/usr/bin/env python3
import json, os, sys
print(json.dumps({"script": os.path.basename(sys.argv[0]), "args": sys.argv[1:],
                  "front_door": os.environ.get("LANE_CONTROL_FRONT_DOOR"), "stdin": sys.stdin.read()}))
sys.exit(7)
"""

# old command and arguments as the docs and scripts gave them, before the front door
SAMPLES = [
    ("project-setup", ["~/code/app"]),
    ("project-setup", ["git@github.com:o/app.git", "trial-vps", "--no-desktop-lanes", "--name", "app"]),
    ("project-setup", ["--check", "~/code/app"]),
    ("project-setup", ["--check", "~/code/app", "trial-vps"]),
    ("project-setup", ["--refresh", "~/code/app"]),
    ("project-setup", ["--show", "~/code/app", "trial-vps"]),
    ("project-setup", ["--remove-host", "trial-vps"]),
    ("project-setup", ["~/code/app", "--suggest-tiers", "anthropic", "--gateway", "http://127.0.0.1:4000"]),
    ("project-setup", ["~/code/app", "--readapt", "--with", "~/code/plugins", "--private", "secrets/*"]),
    ("project-pause", ["rest", "app"]),
    ("project-pause", ["wake", "."]),
    ("project-pause", ["stop", "app", "--wait", "30", "--no-handover"]),
    ("project-pause", ["start", "app"]),
    ("project-pause", ["status", "~/code/app"]),
    ("machine-setup", []),
    ("machine-setup", ["--host", "trial-vps", "--machine-coordinator", "claude/opus", "--no-start"]),
    ("machine-setup", ["--check", "--folder", "~/machine-coordinator"]),
    ("machine-check", []),
    ("machine-check", ["--last"]),
    ("lane-improve", ["on", "--hours", "8", "--project", "app", "--max-items", "4", "--limits", "two lanes, no CI"]),
    ("lane-improve", ["off"]),
    ("lane-improve", ["status", "--json"]),
    ("lane-improve", ["drained", "--all"]),
    ("lane-improve", ["rest", "app"]),
    ("lane-improve", ["pr", "app", "--summary-file", "/tmp/s.md"]),
    ("lane-improve", ["cycle", "app", "--phase", "scout", "--item", "faster start"]),
]


class Table(unittest.TestCase):
    def test_every_old_command_has_a_new_form(self):
        scripts = {s for s, *_ in F.ROUTES.values()}
        self.assertEqual(set(F.OLD), scripts)
        pause = (SCRIPTS / "project-pause").read_text()
        verbs = re.search(r'argv\[0\] not in \(([^)]*)\)', pause).group(1)
        for v in re.findall(r'"([a-z]+)"', verbs):
            self.assertEqual(F.ROUTES[("project", v)][:2], ("project-pause", [v]), v)
        improve = (SCRIPTS / "lane-improve").read_text()
        verbs = re.findall(r'^  ([a-z |]+)\) exec python3', improve, re.M)[0]
        for v in [x.strip() for x in verbs.split("|")]:
            self.assertEqual(F.ROUTES[("improve", v)][:2], ("lane-improve", [v]), v)
        setup = (SCRIPTS / "project-setup").read_text()
        for flag, verb in (("--check", "check"), ("--refresh", "refresh"), ("--show", "show"),
                           ("--remove-host", "remove-host")):
            self.assertIn(f'"{flag}"', setup)
            self.assertEqual(F.ROUTES[("project", verb)][:2], ("project-setup", [flag]))
        self.assertEqual(F.ROUTES[("project", "setup")][:2], ("project-setup", []))
        self.assertEqual(F.ROUTES[("machine", "setup")][:2], ("machine-setup", []))
        self.assertIn('"--check"', (SCRIPTS / "machine-setup").read_text())
        self.assertEqual(F.ROUTES[("machine", "check")][:2], ("machine-setup", ["--check"]))
        self.assertEqual(F.ROUTES[("machine", "changes")][:2], ("machine-check", []))
        for old in F.OLD:  # a script a form runs is in core/scripts/, beside lane-control
            self.assertTrue((SCRIPTS / old).is_file(), old)

    def test_new_form_of_an_old_command_routes_back_to_the_same_script_and_arguments(self):
        for old, argv in SAMPLES:
            new = shlex.split(F.new_form(old, argv))
            self.assertEqual(new[0], "lane-control")
            script, args = F.route(new[1:])
            self.assertEqual(script, old, (old, argv))
            self.assertEqual(sorted(args), sorted(argv), (old, argv))
            if argv and argv[0].startswith("--") and argv[0] in ("--check", "--refresh", "--show", "--remove-host"):
                self.assertEqual(args, argv)  # a mode flag first stays first: exactly the same command line
        script, args = F.route(shlex.split(F.new_form("project-setup", ["--remove-host=trial-vps"]))[1:])
        self.assertEqual((script, args), ("project-setup", ["--remove-host", "trial-vps"]))

    def test_no_route_for_an_unknown_form(self):
        for argv in (["project"], ["project", "pause", "app"], ["machine", "bogus"], ["away", "on"], ["install"]):
            self.assertIsNone(F.route(argv), argv)

    def test_hint_is_for_a_person_only(self):
        class Tty:
            def __init__(self, tty):
                self.tty, self.text = tty, ""

            def isatty(self):
                return self.tty

            def write(self, s):
                self.text += s

            def flush(self):
                pass

        keep = os.environ.pop(F.ENV, None)
        try:
            for old, argv, want in (("project-setup", ["--check", "/srv/app"], "`lane-control project check /srv/app`"),
                                    ("machine-check", ["--last"], "`lane-control machine changes --last`"),
                                    ("project-pause", ["rest", "app"], "`lane-control project rest app`")):
                os.environ.pop(F.ENV, None)
                s = Tty(True)
                F.hint(old, argv, stream=s)
                self.assertEqual(s.text.count("\n"), 1, s.text)
                self.assertIn(want, s.text)
                self.assertIn("keeps working", s.text)
                self.assertEqual(os.environ.get(F.ENV), "1")  # what it starts is not a person
                s = Tty(True)
                F.hint(old, argv, stream=s)  # the front door (or a script) ran it
                self.assertEqual(s.text, "")
                os.environ.pop(F.ENV, None)
                s = Tty(False)
                F.hint(old, argv, stream=s)  # no terminal: an automation or a script reading the output
                self.assertEqual(s.text, "")
            for internal in ("--via-host", "--box-pairing"):  # project-setup's own run on a box
                os.environ.pop(F.ENV, None)
                s = Tty(True)
                F.hint("project-setup", ["~/code/app", internal], stream=s)
                self.assertEqual(s.text, "")
        finally:
            os.environ.pop(F.ENV, None)
            if keep is not None:
                os.environ[F.ENV] = keep

    def test_a_box_command_is_not_a_person(self):
        I = load("lc_install_fd", ROOT / "core" / "install.py")
        self.assertIn("export LANE_CONTROL_FRONT_DOOR=1;", I.box_run("scripts/project-setup", "--show"))


class Dispatch(unittest.TestCase):
    """lane-control in a scratch tree whose old scripts are stubs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.tree = self.tmp / "tree"
        (self.tree / "core" / "scripts").mkdir(parents=True)
        for f in (ROOT / "core").glob("*.py"):
            shutil.copy(f, self.tree / "core" / f.name)
        shutil.copy(SCRIPTS / "lane-control", self.tree / "core" / "scripts" / "lane-control")
        for old in F.OLD:
            p = self.tree / "core" / "scripts" / old
            p.write_text(STUB)
            p.chmod(0o755)
        (self.tree / "scripts").mkdir()
        (self.tree / "scripts" / "lane-control").symlink_to("../core/scripts/lane-control")
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.env = {k: v for k, v in os.environ.items() if k != F.ENV}
        self.env.update(HOME=str(self.home), XDG_DATA_HOME=str(self.tmp / "data"))

    def tearDown(self):
        self._tmp.cleanup()

    def run_(self, argv, stdin="yes\n"):
        return subprocess.run(argv, input=stdin, capture_output=True, text=True, env=self.env, timeout=60)

    def test_each_new_form_runs_the_old_script_with_the_same_arguments(self):
        cli = str(self.tree / "scripts" / "lane-control")  # through the link, as ~/.agents/bin/lane-control is
        for old, argv in SAMPLES:
            new = shlex.split(F.new_form(old, argv))
            a = self.run_([cli, *new[1:]])
            b = self.run_([str(self.tree / "core" / "scripts" / old), *argv])
            self.assertEqual(a.returncode, 7, (new, a.stderr))  # its exit status, unchanged
            self.assertEqual(b.returncode, 7)
            got, was = json.loads(a.stdout), json.loads(b.stdout)
            self.assertEqual(a.stdout.count("\n"), 1, a.stdout)  # nothing of the front door's own on stdout
            self.assertEqual(got["script"], old)
            self.assertEqual(sorted(got["args"]), sorted(was["args"]), new)
            self.assertEqual(got["stdin"], "yes\n")  # stdin goes through: the owner's answers reach it
            self.assertEqual(got["front_door"], "1")  # so the script says no old-name hint
            self.assertIsNone(was["front_door"])
            self.assertEqual(a.stderr, "")

    def test_every_route_reaches_its_script_with_its_first_arguments_and_the_rest_unchanged(self):
        cli = str(self.tree / "core" / "scripts" / "lane-control")
        for (g, v), (script, first, *_) in F.ROUTES.items():
            rest = ["x y", "--flag", "--opt=1", "-"]
            got = json.loads(self.run_([cli, g, v, *rest]).stdout)
            self.assertEqual((got["script"], got["args"]), (script, [*first, *rest]), (g, v))

    def test_a_verb_help_is_the_scripts_own(self):
        got = json.loads(self.run_([str(self.tree / "core" / "scripts" / "lane-control"), "project", "setup",
                                    "--help"]).stdout)
        self.assertEqual((got["script"], got["args"]), ("project-setup", ["--help"]))

    def test_help_lists_everything_on_one_screen_grouped(self):
        cli = str(self.tree / "core" / "scripts" / "lane-control")
        out = self.run_([cli, "help"])
        self.assertEqual(out.returncode, 0)
        for flag in ("--help", "-h"):
            self.assertEqual(self.run_([cli, flag]).stdout, out.stdout)
        t = out.stdout
        self.assertLessEqual(len(t.splitlines()), 40)
        for (g, v) in F.ROUTES:
            self.assertIn(f"lane-control {g} {v}", t)
        for cmd in ("install", "status", "doctor", "prune"):
            self.assertRegex(t, rf"\n  lane-control {cmd}\b")
        for old in F.OLD:
            self.assertIn(old, t)
        groups = [t.index(h) for h in ("A project", "This machine", "Away-improve mode", "The install")]
        self.assertEqual(groups, sorted(groups))
        bare = self.run_([cli])
        self.assertEqual((bare.returncode, bare.stderr), (2, t))

    def test_group_help_names_each_verb_and_its_script(self):
        cli = str(self.tree / "core" / "scripts" / "lane-control")
        for g in F.GROUPS:
            for argv in ([g, "--help"], [g, "help"], ["help", g]):
                r = self.run_([cli, *argv])
                one = {s for (gg, _), (s, *_) in F.ROUTES.items() if gg == g}
                if len(one) == 1:  # improve: the listing, then the one script's own whole help (its exit status)
                    self.assertEqual(r.returncode, 7, argv)
                    got = json.loads(r.stdout.splitlines()[-1])
                    self.assertEqual((got["script"], got["args"]), (one.pop(), ["--help"]))
                else:
                    self.assertEqual(r.returncode, 0, argv)
                for (gg, v), (script, first, *_) in F.ROUTES.items():
                    if gg == g:
                        self.assertIn(f"lane-control {g} {v}", r.stdout)
                        self.assertIn("runs " + " ".join([script, *first]), r.stdout)
            r = self.run_([cli, g, "bogus"])
            self.assertEqual(r.returncode, 2)
            self.assertIn(f"no form `lane-control {g} bogus`", r.stderr)
            self.assertEqual(r.stdout, "")

    def test_install_commands_are_as_before(self):
        cli = str(self.tree / "core" / "scripts" / "lane-control")
        for cmd in ("install", "status", "doctor", "prune"):
            r = self.run_([cli, cmd, "--help"])
            self.assertEqual(r.returncode, 0, cmd)
            self.assertIn(f"usage: lane-control {cmd}", r.stdout)
        self.assertIn("usage: lane-control prune", self.run_([cli, "help", "prune"]).stdout)


class OldNames(unittest.TestCase):
    """The real old scripts: a person in a terminal is told the new form on stderr; stdout never changes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.env = {k: v for k, v in os.environ.items() if k != F.ENV}
        self.env.update(HOME=str(self.home), XDG_DATA_HOME=str(self.home / "data"),
                        XDG_CONFIG_HOME=str(self.home / ".config"), XDG_CACHE_HOME=str(self.home / ".cache"))

    def tearDown(self):
        self._tmp.cleanup()

    def run_(self, argv, tty, env=None):
        """stderr on a pseudo-terminal when tty, else a pipe; stdout always a pipe. (exit, stdout, stderr)"""
        env = env or self.env
        if not tty:
            r = subprocess.run(argv, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)
            return r.returncode, r.stdout, r.stderr
        master, slave = pty.openpty()
        out_file = tempfile.TemporaryFile()  # not a pipe: a full pipe would block the script while stderr is read
        try:
            p = subprocess.Popen(argv, stdout=out_file, stderr=slave, stdin=subprocess.DEVNULL, env=env)
            os.close(slave)
            err = b""
            while True:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                err += chunk
            p.wait(timeout=60)
            out_file.seek(0)
            return p.returncode, out_file.read().decode(), err.decode().replace("\r\n", "\n")
        finally:
            os.close(master)
            out_file.close()

    CASES = (("project-setup", ["--help"], "`lane-control project setup --help`"),
             ("project-pause", [], "`lane-control project rest|wake|stop|start|status <project>`"),
             ("machine-setup", ["--help"], "`lane-control machine setup --help`"),
             ("machine-check", ["--help"], "`lane-control machine changes --help`"),
             ("lane-improve", ["--help"], "`lane-control improve on|off|drained|status|rest|pr|cycle`"),
             ("lane-improve", ["status"], "`lane-control improve status`"))

    def test_a_person_sees_one_line_with_the_new_form(self):
        for old, argv, want in self.CASES:
            code, out, err = self.run_([str(SCRIPTS / old), *argv], tty=True)
            first = err.splitlines()[0] if err else ""
            self.assertEqual(first, f"{old}: note: this is now {want} (the old name keeps working)", (old, err))
            self.assertEqual(err.count("this is now"), 1)
            code2, out2, err2 = self.run_([str(SCRIPTS / old), *argv], tty=False)
            self.assertEqual((code, out), (code2, out2), old)  # the same exit and stdout either way
            self.assertNotIn("this is now", err2)  # no terminal: a script or an automation runs it

    def test_quiet_when_the_front_door_runs_it(self):
        for old, argv, _ in self.CASES:
            _, _, err = self.run_([str(SCRIPTS / old), *argv], tty=True, env={**self.env, F.ENV: "1"})
            self.assertNotIn("this is now", err, old)

    def test_the_front_door_itself_is_quiet(self):
        _, _, err = self.run_([str(SCRIPTS / "lane-control"), "improve", "status"], tty=True)
        self.assertNotIn("this is now", err)


if __name__ == "__main__":
    unittest.main()
