"""Harness adapters (core/harnesses/): starting an agent with a role and persona, and the persona surviving compaction.

Temp dirs only: HOME and XDG_* point into them, so no real Claude, OpenCode or Orca state is read or written.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESSES = ROOT / "core" / "harnesses"
HOOK = HARNESSES / "persona-hook.sh"
PERSONA_TEXT = "# Coordinator\n\nYou coordinate lanes.\n"


def clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("LANE_ROLE", "LANE_PERSONA")}
    env.update(extra)
    return env


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)


class PersonaHookTests(TempDirTest):
    """Task 1.1: the Claude SessionStart hook prints $LANE_PERSONA only for the session it belongs to."""

    def setUp(self):
        super().setUp()
        self.persona = self.t / "coordinator.md"
        self.persona.write_text(PERSONA_TEXT)

    def hook(self, env, stdin=""):
        return subprocess.run([str(HOOK)], input=stdin, capture_output=True, text=True, cwd=self.t, env=env,
                              timeout=10)

    def test_hook_is_executable_and_parses(self):
        self.assertTrue(os.access(HOOK, os.X_OK))
        self.assertEqual(subprocess.run(["sh", "-n", str(HOOK)]).returncode, 0)

    def test_hook_prints_persona_with_markers(self):
        r = self.hook(clean_env(LANE_ROLE="coordinator", LANE_PERSONA=str(self.persona)))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, PERSONA_TEXT)

    def test_hook_silent_without_markers(self):
        # Review Focus 1: a worker started in the coordinator's checkout (same cwd, persona file there) gets nothing.
        for env in (clean_env(), clean_env(LANE_ROLE="coordinator"), clean_env(LANE_PERSONA=str(self.persona)),
                    clean_env(LANE_ROLE="", LANE_PERSONA=str(self.persona))):
            r = self.hook(env)
            self.assertEqual((r.returncode, r.stdout), (0, ""), env.get("LANE_ROLE"))

    def test_hook_silent_missing_file(self):
        # Review Focus 2: the session still starts, without a persona and without noise.
        (self.t / "a-dir.md").mkdir()
        for path in (self.t / "missing.md", self.t / "a-dir.md"):
            r = self.hook(clean_env(LANE_ROLE="coordinator", LANE_PERSONA=str(path)))
            self.assertEqual((r.returncode, r.stdout), (0, ""), path)

    def test_hook_exit_zero_always(self):
        unreadable = self.t / "secret.md"
        unreadable.write_text(PERSONA_TEXT)
        unreadable.chmod(0)
        self.addCleanup(unreadable.chmod, 0o600)
        r = self.hook(clean_env(LANE_ROLE="coordinator", LANE_PERSONA=str(unreadable)),
                      stdin='{"hook_event_name": "SessionStart", "source": "compact"}')
        self.assertEqual(r.returncode, 0)
        if os.geteuid() != 0:  # root reads anything
            self.assertEqual(r.stdout, "")
        r = subprocess.run([str(HOOK)], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           env=clean_env(LANE_ROLE="coordinator", LANE_PERSONA=str(self.persona)), timeout=10)
        self.assertEqual((r.returncode, r.stdout), (0, PERSONA_TEXT))


def load_harnesses_pkg():
    """core/harnesses as the package `harnesses`, the way scripts import it (core/ on sys.path)."""
    import importlib
    import sys
    core = str(ROOT / "core")
    if core not in sys.path:
        sys.path.insert(0, core)
    return importlib.import_module("harnesses")


class HomeTest(TempDirTest):
    """HOME and XDG dirs point into the temp dir for the test's length."""

    def setUp(self):
        super().setUp()
        self.home = self.t / "home"
        self.home.mkdir()
        saved = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME")}

        def restore():
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(restore)
        os.environ["HOME"] = str(self.home)
        os.environ.pop("XDG_CONFIG_HOME", None)
        os.environ.pop("XDG_DATA_HOME", None)
        self.checkout = self.t / "code" / "shop.app"
        self.checkout.mkdir(parents=True)
        self.pkg = load_harnesses_pkg()


class LoaderTests(HomeTest):
    def test_load_harness_and_harnesses(self):
        all_ = self.pkg.harnesses()
        self.assertEqual(set(all_), {"claude", "opencode"})
        self.assertEqual(self.pkg.load_harness("claude").NAME, "claude")
        self.assertEqual(self.pkg.load_harness("opencode").NAME, "opencode")
        for name in ("codex", "pi", "nope", "../claude", ""):
            with self.assertRaises(ValueError, msg=name):
                self.pkg.load_harness(name)

    def test_every_adapter_has_the_interface(self):
        for name, mod in self.pkg.harnesses().items():
            self.assertEqual(mod.NAME, name)
            self.assertIn(mod.STATUS, ("in use", "untested"))
            self.assertIsInstance(mod.ORCA_AGENT_ID, str)
            self.assertIsInstance(mod.MODEL_FLAGS, frozenset)
            for fn in ("start_command", "install_persona_hook", "idle_minutes"):
                self.assertTrue(callable(getattr(mod, fn)), (name, fn))


class ClaudeAdapterTests(HomeTest):
    def setUp(self):
        super().setUp()
        self.a = self.pkg.load_harness("claude")
        self.settings = self.checkout / ".claude" / "settings.local.json"

    def entries(self):
        import json
        return json.loads(self.settings.read_text())["hooks"]["SessionStart"]

    def test_claude_constants(self):
        self.assertEqual((self.a.NAME, self.a.STATUS, self.a.ORCA_AGENT_ID), ("claude", "in use", "claude"))
        self.assertEqual(self.a.MODEL_FLAGS, frozenset({"model", "effort"}))

    def test_claude_start_command_shape(self):
        p = Path("/abs/.lane-control/coordinator.md")
        self.assertEqual(self.a.start_command("coordinator", p, "opus", "auto"),
                         ["env", "LANE_ROLE=coordinator", f"LANE_PERSONA={p}", "claude", "--model", "opus",
                          "--permission-mode", "auto"])
        self.assertEqual(self.a.start_command("reviewer", p, "sonnet", None),
                         ["env", "LANE_ROLE=reviewer", f"LANE_PERSONA={p}", "claude", "--model", "sonnet"])
        for role in ("design-lead", "debugger", "machine-coordinator"):
            self.assertEqual(self.a.start_command(role, p, "opus", None)[1], f"LANE_ROLE={role}")
        with self.assertRaises(ValueError):
            self.a.start_command("worker", p, "opus", None)
        with self.assertRaises(ValueError):
            self.a.start_command("coordinator", Path("rel/coordinator.md"), "opus", None)

    def test_claude_hook_added_once(self):
        changes = self.a.install_persona_hook(self.checkout)
        self.assertEqual(len(changes), 1)
        self.assertIn("settings.local.json", changes[0])
        self.assertEqual(self.entries(), [{"matcher": "startup|resume|compact",
                                           "hooks": [{"type": "command", "command": str(HOOK)}]}])
        before = self.settings.read_bytes()
        self.assertEqual(self.a.install_persona_hook(self.checkout), [])
        self.assertEqual(self.settings.read_bytes(), before)

    def test_claude_hook_keeps_other_settings(self):
        import json
        other_start = {"matcher": "startup", "hooks": [{"type": "command", "command": "/x/other.sh"}]}
        pre = [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/x/guard.sh"}]}]
        self.settings.parent.mkdir()
        self.settings.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}, "model": "opus",
                                             "hooks": {"SessionStart": [other_start], "PreToolUse": pre}}))
        self.assertTrue(self.a.install_persona_hook(self.checkout))
        data = json.loads(self.settings.read_text())
        self.assertEqual(data["permissions"], {"allow": ["Bash(ls)"]})
        self.assertEqual(data["model"], "opus")
        self.assertEqual(data["hooks"]["PreToolUse"], pre)
        self.assertEqual(data["hooks"]["SessionStart"][0], other_start)
        self.assertEqual(len(data["hooks"]["SessionStart"]), 2)

    def test_claude_hook_moves_stale_path(self):
        import json
        stale = {"matcher": "startup|resume|compact",
                 "hooks": [{"type": "command", "command": "/old/lane-control/core/harnesses/persona-hook.sh"}]}
        self.settings.parent.mkdir()
        self.settings.write_text(json.dumps({"hooks": {"SessionStart": [stale]}}))
        self.assertTrue(self.a.install_persona_hook(self.checkout))
        self.assertEqual(self.entries(), [{"matcher": "startup|resume|compact",
                                           "hooks": [{"type": "command", "command": str(HOOK)}]}])

    def test_claude_hook_refuses_bad_json(self):
        self.settings.parent.mkdir()
        self.settings.write_text("{not json")
        with self.assertRaises(ValueError):
            self.a.install_persona_hook(self.checkout)
        self.assertEqual(self.settings.read_text(), "{not json")

    def test_claude_project_dir_encoding(self):
        self.assertEqual(self.a.project_dir(Path("/a/b.c/d_e")),
                         self.home / ".claude" / "projects" / "-a-b-c-d-e")

    def transcripts(self, checkout):
        d = self.a.project_dir(checkout)
        d.mkdir(parents=True)
        return d

    def test_claude_idle_from_newest_transcript(self):
        now = 1_800_000_000.0
        d = self.transcripts(self.checkout)
        for name, age_min in (("old.jsonl", 300), ("new.jsonl", 50)):
            f = d / name
            f.write_text("{}\n")
            os.utime(f, (now - age_min * 60, now - age_min * 60))
        noise = d / "notes.txt"  # not a transcript
        noise.write_text("x")
        os.utime(noise, (now, now))
        other = self.transcripts(self.t / "code" / "other")
        f = other / "s.jsonl"
        f.write_text("{}\n")
        os.utime(f, (now - 60, now - 60))
        self.assertEqual(self.a.idle_minutes(self.checkout, now), 50)
        self.assertEqual(self.a.idle_minutes(self.checkout, now - 50 * 60 - 30), 0)  # clock skew is not negative

    @unittest.skipIf(os.geteuid() == 0, "root reads any folder")
    def test_claude_idle_unreadable_folder_raises(self):
        # Review Focus 4: an unreadable session folder is not "no session"; the night precheck must say why.
        d = self.transcripts(self.checkout)
        (d / "s.jsonl").write_text("{}\n")
        d.chmod(0)
        self.addCleanup(d.chmod, 0o700)
        with self.assertRaises(OSError):
            self.a.idle_minutes(self.checkout, 1_800_000_000.0)

    def test_claude_hook_refuses_odd_shapes(self):
        import json
        self.settings.parent.mkdir()
        for data in ([], {"hooks": []}, {"hooks": {"SessionStart": {}}}):
            self.settings.write_text(json.dumps(data))
            with self.assertRaises(ValueError, msg=data):
                self.a.install_persona_hook(self.checkout)
            self.assertEqual(json.loads(self.settings.read_text()), data)

    def test_claude_idle_none_without_session(self):
        self.assertIsNone(self.a.idle_minutes(self.checkout, 1_800_000_000.0))
        self.transcripts(self.checkout)
        self.assertIsNone(self.a.idle_minutes(self.checkout, 1_800_000_000.0))


PLUGIN = HARNESSES / "opencode" / "lane-persona.js"
NODE = __import__("shutil").which("node")
SCHEMA = ("CREATE TABLE session_v2 (id TEXT PRIMARY KEY, directory TEXT NOT NULL, time_updated INTEGER NOT NULL);"
          "CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, time_updated INTEGER NOT NULL);")


class OpenCodeAdapterTests(HomeTest):
    def setUp(self):
        super().setUp()
        self.a = self.pkg.load_harness("opencode")

    def test_opencode_constants(self):
        self.assertEqual((self.a.NAME, self.a.STATUS, self.a.ORCA_AGENT_ID), ("opencode", "in use", "opencode2"))
        self.assertEqual(self.a.MODEL_FLAGS, frozenset({"model"}))

    def test_opencode_start_command_shape(self):
        import json
        p = Path("/abs/.lane-control/coordinator.md")
        cmd = self.a.start_command("coordinator", p, "opencode-go/deepseek-v4.1-flash", None)
        self.assertEqual(cmd[:3], ["env", "LANE_ROLE=coordinator", f"LANE_PERSONA={p}"])
        conf = [a for a in cmd if a.startswith("OPENCODE_CONFIG_CONTENT=")]
        self.assertEqual(len(conf), 1)
        self.assertEqual(json.loads(conf[0].split("=", 1)[1]), {"model": "opencode-go/deepseek-v4.1-flash"})
        self.assertEqual(cmd[cmd.index(conf[0]) + 1:], ["opencode", "--standalone"])
        self.assertEqual(self.a.start_command("coordinator", p, "m/x", "default")[-1], "--standalone")
        for mode in ("auto", "bypassPermissions"):
            self.assertEqual(self.a.start_command("reviewer", p, "m/x", mode)[-2:], ["--standalone", "--auto"])
        with self.assertRaises(ValueError):
            self.a.start_command("reviewer", p, "m/x", "plan")
        with self.assertRaises(ValueError):
            self.a.start_command("worker", p, "m/x", None)

    def plugin_dir(self):
        return self.home / ".config" / "opencode" / "plugins"

    def test_opencode_plugin_installed_once(self):
        changes = self.a.install_persona_hook(self.checkout)
        dest = self.plugin_dir() / "lane-persona.js"
        self.assertEqual(len(changes), 1)
        self.assertIn(str(dest), changes[0])
        self.assertEqual(dest.read_bytes(), PLUGIN.read_bytes())
        self.assertEqual(self.a.install_persona_hook(self.checkout), [])
        dest.write_text("// an old copy\n")
        self.assertTrue(self.a.install_persona_hook(self.checkout))
        self.assertEqual(dest.read_bytes(), PLUGIN.read_bytes())
        self.assertEqual(list(self.checkout.iterdir()), [])  # nothing written into the project

    def test_opencode_plugin_dir_follows_xdg(self):
        os.environ["XDG_CONFIG_HOME"] = str(self.t / "xdg")
        self.assertTrue(self.a.install_persona_hook(self.checkout))
        self.assertTrue((self.t / "xdg" / "opencode" / "plugins" / "lane-persona.js").is_file())
        self.assertFalse(self.plugin_dir().exists())

    def db(self, rows, messages=()):
        import sqlite3
        path = self.home / ".local" / "share" / "opencode" / "opencode.db"
        path.parent.mkdir(parents=True)
        c = sqlite3.connect(path)
        c.executescript(SCHEMA)
        c.executemany("INSERT INTO session_v2 VALUES (?, ?, ?)", rows)
        c.executemany("INSERT INTO session_message VALUES (?, ?, ?)", messages)
        c.commit()
        c.close()
        return path

    def test_opencode_idle_from_session_store(self):
        now = 1_800_000_000.0
        ms = lambda age_min: int((now - age_min * 60) * 1000)  # noqa: E731
        here = str(self.checkout.resolve())
        self.db([("s1", here, ms(300)), ("s2", here, ms(90)), ("s3", str(self.t / "other"), ms(1))],
                [("m1", "s2", ms(40)), ("m2", "s3", ms(0))])
        self.assertEqual(self.a.idle_minutes(self.checkout, now), 40)

    def test_opencode_idle_none_without_session(self):
        self.assertIsNone(self.a.idle_minutes(self.checkout, 1_800_000_000.0))
        self.db([("s1", str(self.t / "other"), 1)])
        self.assertIsNone(self.a.idle_minutes(self.checkout, 1_800_000_000.0))

    def test_opencode_idle_db_follows_xdg(self):
        os.environ["XDG_DATA_HOME"] = str(self.t / "data")
        self.assertIsNone(self.a.idle_minutes(self.checkout, 1_800_000_000.0))


@unittest.skipUnless(NODE, "node is not installed")
class OpenCodePluginTests(TempDirTest):
    """lane-persona.js run under node with the two hooks OpenCode calls."""

    DRIVER = """
import { pathToFileURL } from "node:url";
const mod = await import(pathToFileURL(process.argv[2]).href);
const plugins = Object.values(mod).filter((f) => typeof f === "function");
const out = { count: plugins.length, system: [], context: [] };
for (const p of plugins) {
  const hooks = await p({ directory: process.cwd() });
  await hooks["experimental.chat.system.transform"]({ model: {} }, { system: out.system });
  await hooks["experimental.session.compacting"]({ sessionID: "s" }, { context: out.context });
  await hooks["experimental.chat.system.transform"]({ model: {} }, {});  // a shape it does not know: no throw
}
console.log(JSON.stringify(out));
"""

    def setUp(self):
        super().setUp()
        self.persona = self.t / "coordinator.md"
        self.persona.write_text(PERSONA_TEXT)
        # .mjs so node loads it as a module whatever its default; same bytes as the shipped plugin.
        self.copy = self.t / "lane-persona.mjs"
        self.copy.write_bytes(PLUGIN.read_bytes())
        self.driver = self.t / "driver.mjs"
        self.driver.write_text(self.DRIVER)

    def run_plugin(self, env):
        import json
        r = subprocess.run([NODE, str(self.driver), str(self.copy)], capture_output=True, text=True, cwd=self.t,
                           env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        return json.loads(r.stdout)

    def test_plugin_syntax(self):
        r = subprocess.run([NODE, "--check", str(self.copy)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_plugin_appends_persona_with_markers(self):
        out = self.run_plugin(clean_env(LANE_ROLE="coordinator", LANE_PERSONA=str(self.persona)))
        self.assertEqual(out, {"count": 1, "system": [PERSONA_TEXT], "context": [PERSONA_TEXT]})

    def test_plugin_silent_without_markers(self):
        for env in (clean_env(), clean_env(LANE_ROLE="coordinator"), clean_env(LANE_PERSONA=str(self.persona))):
            self.assertEqual(self.run_plugin(env), {"count": 1, "system": [], "context": []})

    def test_plugin_silent_missing_file(self):
        (self.t / "a-dir.md").mkdir()
        for path in (self.t / "missing.md", self.t / "a-dir.md"):
            out = self.run_plugin(clean_env(LANE_ROLE="coordinator", LANE_PERSONA=str(path)))
            self.assertEqual(out, {"count": 1, "system": [], "context": []})


if __name__ == "__main__":
    unittest.main()
