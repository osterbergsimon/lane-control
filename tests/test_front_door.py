"""CLI-1 (lc-35h): one front door, `lane-control project|machine|ratchet ...` (core/frontdoor.py, core/scripts/
lane-control). Every old command has a form; a form runs the same script with the same arguments, in the same order;
the old names keep working and point a person, and only a person, at the new form. `improve` is the ratchet's alias.

The dispatch runs against a scratch tree whose old scripts are stubs that print what they were given; the help and the
hints also run against the real scripts, in a scratch HOME."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
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
    ("project-setup", ["~/code/app", "trial-vps", "--check"]),
    ("project-setup", ["--refresh", "~/code/app"]),
    ("project-setup", ["~/code/app", "--refresh", "--name", "app"]),
    ("project-setup", ["--show", "~/code/app", "trial-vps"]),
    ("project-setup", ["--remove-host", "trial-vps"]),
    ("project-setup", ["--name", "x", "--remove-host", "trial-vps"]),  # review m2: an option before it
    ("project-setup", ["--no-model-check", "--remove-host=trial-vps"]),
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
    ("machine-check", ["--last"]),
    ("lane-improve", ["on", "--hours", "8", "--project", "app", "--max-items", "4", "--limits", "two lanes, no CI"]),
    ("lane-improve", ["off"]),
    ("lane-improve", ["status", "--json"]),
    ("lane-improve", ["drained", "--all"]),
    ("lane-improve", ["rest", "app"]),
    ("lane-improve", ["pr", "app", "--summary-file", "/tmp/s.md"]),
    ("lane-improve", ["cycle", "app", "--phase", "scout", "--item", "faster start"]),
]


def expected(old, argv):
    """What the new form gives the script: the same arguments in the same order, except that project-setup's mode flag
    (with --remove-host's value) comes first, and machine-check alone becomes its read-only --peek."""
    if old == "project-setup":
        for i, x in enumerate(argv):
            name, eq, val = x.partition("=")
            if name in F.MODES:
                if name == "--remove-host":
                    head = [name, val] if eq else argv[i:i + 2]
                    return head + argv[:i] + argv[i + (1 if eq else 2):]
                return [name] + argv[:i] + argv[i + 1:]
    if old == "machine-check" and not argv:
        return ["--peek"]
    return list(argv)


class Table(unittest.TestCase):
    def test_every_old_command_has_a_new_form(self):
        scripts = {s for s, *_ in F.ROUTES.values()}
        self.assertEqual(set(F.OLD) | set(F.NEW), scripts)  # lane-spawn (lc-wja) is new: no old name
        pause = (SCRIPTS / "project-pause").read_text()
        verbs = re.search(r'argv\[0\] not in \(([^)]*)\)', pause).group(1)
        for v in re.findall(r'"([a-z]+)"', verbs):
            self.assertEqual(F.ROUTES[("project", v)][:2], ("project-pause", [v]), v)
        improve = (SCRIPTS / "lane-improve").read_text()
        verbs = re.findall(r'^  ([a-z |]+)\) exec python3', improve, re.M)[0]
        for v in [x.strip() for x in verbs.split("|")]:
            self.assertEqual(F.ROUTES[("ratchet", v)][:2], ("lane-improve", [v]), v)
        setup = (SCRIPTS / "project-setup").read_text()
        for flag in F.MODES:
            self.assertIn(f'"{flag}"', setup)
            self.assertEqual(F.ROUTES[("project", flag[2:])][:2], ("project-setup", [flag]))
        self.assertEqual(F.ROUTES[("project", "setup")][:2], ("project-setup", []))
        self.assertEqual(F.ROUTES[("machine", "setup")][:2], ("machine-setup", []))
        self.assertEqual(F.ROUTES[("machine", "report")][:2], ("machine-check", ["--peek"]))
        self.assertEqual(F.route(["machine", "report", "--last"]), ("machine-check", ["--last"]))
        self.assertIn('"--peek"', (SCRIPTS / "machine-check").read_text())
        for old in (*F.OLD, *F.NEW):  # a script a form runs is in core/scripts/, beside lane-control
            self.assertTrue((SCRIPTS / old).is_file(), old)

    def test_one_meaning_per_name(self):
        # review m1: `machine check` is the Orca automation's name (machine-check); no form takes it for another job
        self.assertNotIn(("machine", "check"), F.ROUTES)
        self.assertNotIn(("machine", "changes"), F.ROUTES)  # it reads, so it is named for what it does: report
        self.assertEqual(set(F.GROUPS), {"project", "coordinator", "lane", "machine", "ratchet"})
        self.assertEqual(F.ALIASES, {"improve": "ratchet"})
        self.assertNotIn("away", F.GROUPS)  # presence is lane-away

    def test_new_form_of_an_old_command_routes_back_to_the_same_script_and_arguments_in_order(self):
        for old, argv in SAMPLES:
            new = shlex.split(F.new_form(old, argv))
            self.assertEqual(new[0], "lane-control")
            script, args = F.route(new[1:])
            self.assertEqual(script, old, (old, argv))
            self.assertEqual(args, expected(old, argv), (old, argv))  # order and value binding, not just the set
        self.assertEqual(F.route(shlex.split(F.new_form("machine-check", []))[1:]), ("machine-check", ["--peek"]))

    def test_the_alias_routes_as_the_ratchet(self):
        for v in F.RATCHET_VERBS:
            self.assertEqual(F.route(["improve", v, "x"]), F.route(["ratchet", v, "x"]))

    def test_no_route_for_an_unknown_form(self):
        for argv in (["project"], ["project", "pause", "app"], ["machine", "check"], ["away", "on"], ["install"]):
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
                                    ("project-setup", ["--name", "x", "--remove-host", "h"],
                                     "`lane-control project remove-host h --name x`"),
                                    ("machine-check", ["--last"], "`lane-control machine report --last`"),
                                    ("machine-check", [], "records what it finds; to look by hand without using that "
                                                          "up: `lane-control machine report`"),
                                    ("lane-improve", ["status"], "`lane-control ratchet status`"),
                                    ("project-pause", ["rest", "app"], "`lane-control project rest app`")):
                os.environ.pop(F.ENV, None)
                s = Tty(True)
                F.hint(old, argv, stream=s)
                self.assertEqual(s.text.count("\n"), 1, s.text)
                self.assertIn(want, s.text)
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


def run_pty(argv, env):
    """stderr on a pseudo-terminal, stdout to a file (a full pipe would block the script while stderr is read).
    (exit, stdout, stderr)"""
    master, slave = pty.openpty()
    out_file = tempfile.TemporaryFile()
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
        for old in (*F.OLD, *F.NEW):
            p = self.tree / "core" / "scripts" / old
            p.write_text(STUB)
            p.chmod(0o755)
        (self.tree / "scripts").mkdir()
        (self.tree / "scripts" / "lane-control").symlink_to("../core/scripts/lane-control")
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.env = {k: v for k, v in os.environ.items() if k != F.ENV}
        self.env.update(testhome.env_for(self.home), XDG_DATA_HOME=str(self.tmp / "data"))
        self.cli = str(self.tree / "core" / "scripts" / "lane-control")

    def tearDown(self):
        self._tmp.cleanup()

    def run_(self, argv, stdin="yes\n"):
        return subprocess.run(argv, input=stdin, capture_output=True, text=True, env=self.env, timeout=60)

    def test_each_new_form_runs_the_old_script_with_the_same_arguments_in_order(self):
        cli = str(self.tree / "scripts" / "lane-control")  # through the link, as ~/.agents/bin/lane-control is
        for old, argv in SAMPLES:
            new = shlex.split(F.new_form(old, argv))
            a = self.run_([cli, *new[1:]])
            self.assertEqual(a.returncode, 7, (new, a.stderr))  # its exit status, unchanged
            got = json.loads(a.stdout)
            self.assertEqual(a.stdout.count("\n"), 1, a.stdout)  # nothing of the front door's own on stdout
            self.assertEqual((got["script"], got["args"]), (old, expected(old, argv)), new)
            self.assertEqual(got["stdin"], "yes\n")  # stdin goes through: the owner's answers reach it
            self.assertEqual(got["front_door"], "1")  # so the script says no old-name hint
            self.assertEqual(a.stderr, "")
            b = json.loads(self.run_([str(self.tree / "core" / "scripts" / old), *argv]).stdout)
            self.assertIsNone(b["front_door"])

    def test_every_route_reaches_its_script_with_its_first_arguments_and_the_rest_unchanged(self):
        for (g, v), (script, first, *_) in F.ROUTES.items():
            rest = ["x y", "--flag", "--opt=1", "-"]
            for group in [g, *[a for a, c in F.ALIASES.items() if c == g]]:
                got = json.loads(self.run_([self.cli, group, v, *rest]).stdout)
                self.assertEqual((got["script"], got["args"]), (script, [*first, *rest]), (group, v))

    def test_the_alias_says_the_new_name_to_a_person_only(self):
        code, out, err = run_pty([self.cli, "improve", "status"], self.env)
        self.assertEqual(code, 7)
        self.assertEqual(json.loads(out)["args"], ["status"])
        self.assertIn("lane-control: note: `improve` is now `ratchet`: lane-control ratchet status", err)
        self.assertEqual(self.run_([self.cli, "improve", "status"]).stderr, "")  # no terminal: nothing
        code, _, err = run_pty([self.cli, "improve", "status"], {**self.env, F.ENV: "1"})
        self.assertNotIn("note:", err)

    def test_every_verb_help_answers(self):
        # review M1: each verb's --help gives its options: the front door's own text where the script has none
        for (g, v), (script, *_) in F.ROUTES.items():
            r = self.run_([self.cli, g, v, "--help"])
            if (g, v) in F.VERB_HELP:
                self.assertEqual(r.returncode, 0, (g, v))
                self.assertTrue(r.stdout.startswith(f"usage: {F.usage(g, v)}\n"), r.stdout)
                self.assertIn(f"Runs {script}", r.stdout)
            else:
                got = json.loads(r.stdout)
                self.assertEqual(got["args"][-1], "--help", (g, v))  # the script's own help
        stop = self.run_([self.cli, "project", "stop", "app", "--help"]).stdout
        for flag in ("--wait SECONDS", "--no-handover"):
            self.assertIn(flag, stop)

    def test_help_lists_everything_on_one_screen_grouped(self):
        out = self.run_([self.cli, "help"])
        self.assertEqual(out.returncode, 0)
        for flag in ("--help", "-h"):
            self.assertEqual(self.run_([self.cli, flag]).stdout, out.stdout)
        t = out.stdout
        self.assertLessEqual(len(t.splitlines()), 45)  # one screen (CLI-2 added the coordinator and lane groups)
        self.assertLessEqual(max(len(x) for x in t.splitlines()), 100, t)  # review n2
        for (g, v) in F.ROUTES:
            self.assertIn(f"lane-control {g} {v}", t)
        for cmd in ("install", "status", "doctor", "prune"):
            self.assertRegex(t, rf"\n  lane-control {cmd}\b")
        for old in F.OLD:
            self.assertIn(old, t)
        self.assertIn("lane-control improve (now ratchet)", t)
        groups = [t.index(h) for h in ("A project", "Coordinators:", "Lanes:", "This machine",
                                       "The ratchet, unattended improvement", "The install")]
        self.assertEqual(groups, sorted(groups))
        bare = self.run_([self.cli])
        self.assertEqual((bare.returncode, bare.stderr), (2, t))

    def test_group_help_names_each_verb_and_its_script(self):
        for g in F.GROUPS:
            for argv in ([g, "--help"], [g, "help"], ["help", g]):
                r = self.run_([self.cli, *argv])
                one = {s for (gg, _), (s, *_) in F.ROUTES.items() if gg == g}
                listing = r.stdout
                if len(one) == 1:  # the listing, then the one script's own help (its exit status): the ratchet's
                    self.assertEqual(r.returncode, 7, argv)  # whole help, lane-spawn's for its group word
                    got = json.loads(r.stdout.splitlines()[-1])
                    self.assertEqual((got["script"], got["args"]), (one.pop(), [*F.shared_first(g), "--help"]))
                    listing = r.stdout.rsplit("\n", 2)[0]
                else:
                    self.assertEqual(r.returncode, 0, argv)
                self.assertLessEqual(max(len(x) for x in listing.splitlines()), 100, listing)
                for (gg, v), (script, first, *_) in F.ROUTES.items():
                    if gg == g:
                        self.assertIn(f"lane-control {g} {v}", r.stdout)
                        self.assertIn("runs " + " ".join([script, *first]), r.stdout)
            r = self.run_([self.cli, g, "bogus"])
            self.assertEqual(r.returncode, 2)
            self.assertIn(f"no form `lane-control {g} bogus`", r.stderr)
            self.assertEqual(r.stdout, "")

    def test_an_unknown_first_word_names_every_group(self):
        # review m3: `lane-control away on` (the name first approved) must not read as if the groups did not exist
        for argv in (["away", "on"], ["help", "bogus"], ["bogus"]):
            r = self.run_([self.cli, *argv])
            self.assertEqual(r.returncode, 2, argv)
            for word in ("project", "machine", "ratchet", "install", "prune", "lane-control help"):
                self.assertIn(word, r.stderr, argv)
        self.assertIn("lane-away", self.run_([self.cli, "away", "on"]).stderr)

    def test_install_commands_are_as_before(self):
        for cmd in ("install", "status", "doctor", "prune"):
            r = self.run_([self.cli, cmd, "--help"])
            self.assertEqual(r.returncode, 0, cmd)
            self.assertIn(f"usage: lane-control {cmd}", r.stdout)
        self.assertIn("usage: lane-control prune", self.run_([self.cli, "help", "prune"]).stdout)


class OldNames(unittest.TestCase):
    """The real scripts, in a scratch HOME: a person in a terminal is told the new form on stderr; stdout never
    changes; every verb's --help answers through the front door."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.env = {k: v for k, v in os.environ.items() if k != F.ENV}
        self.env.update(testhome.env_for(self.home), XDG_DATA_HOME=str(self.home / "data"))

    def tearDown(self):
        self._tmp.cleanup()

    def run_(self, argv, env=None):
        r = subprocess.run(argv, capture_output=True, text=True, env=env or self.env, stdin=subprocess.DEVNULL,
                           timeout=60)
        return r.returncode, r.stdout, r.stderr

    CASES = (("project-setup", ["--help"], "`lane-control project setup --help`"),
             ("project-pause", [], "`lane-control project rest|wake|stop|start|status <project>`"),
             ("machine-setup", ["--help"], "`lane-control machine setup --help`"),
             ("machine-check", ["--help"], "`lane-control machine report --help`"),
             ("lane-improve", ["--help"], "`lane-control ratchet on|off|drained|status|rest|pr|cycle`"),
             ("lane-improve", ["status"], "`lane-control ratchet status`"))

    def test_a_person_sees_one_line_with_the_new_form(self):
        for old, argv, want in self.CASES:
            code, out, err = run_pty([str(SCRIPTS / old), *argv], self.env)
            first = err.splitlines()[0] if err else ""
            self.assertEqual(first, f"{old}: note: this is now {want} (the old name keeps working)", (old, err))
            self.assertEqual(err.count("this is now"), 1)
            code2, out2, err2 = self.run_([str(SCRIPTS / old), *argv])
            self.assertEqual((code, out), (code2, out2), old)  # the same exit and stdout either way
            self.assertNotIn("this is now", err2)  # no terminal: a script or an automation runs it

    def test_quiet_when_the_front_door_runs_it(self):
        for old, argv, _ in self.CASES:
            _, _, err = run_pty([str(SCRIPTS / old), *argv], {**self.env, F.ENV: "1"})
            self.assertNotIn("this is now", err, old)
        _, _, err = run_pty([str(SCRIPTS / "lane-control"), "ratchet", "status"], self.env)
        self.assertNotIn("this is now", err)

    def test_every_verb_help_of_the_real_scripts_answers(self):
        # review M1: `lane-control <group> <verb> --help` never ends in an error
        for (g, v) in F.ROUTES:
            code, out, err = self.run_([str(SCRIPTS / "lane-control"), g, v, "--help"])
            self.assertEqual(code, 0, (g, v, err))
            self.assertIn("usage", out.lower(), (g, v))

    def test_a_closed_pipe_ends_the_script_quietly(self):
        # review n1: what the front door execs gets SIGPIPE's default, as when it is run by its own name
        r = subprocess.run(f"{shlex.quote(str(SCRIPTS / 'lane-control'))} ratchet --help | head -1", shell=True,
                           capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(r.stderr, "")


if __name__ == "__main__":
    unittest.main()
