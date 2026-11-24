"""Harness adapters (core/harnesses/): starting an agent with a role and persona, and the persona surviving compaction.

Temp dirs only: HOME and XDG_* point into them, so no real Claude, OpenCode or Orca state is read or written.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
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

    def test_hook_records_own_session(self):
        # Review M4: the marked session says which transcript is the coordinator's.
        pkg = load_harnesses_pkg()
        checkout = self.t / "shop.app"
        checkout.mkdir()
        state = self.t / "state"
        start = ('{"session_id": "4f1c2e9a-0b7d-4c55-9a8e-1d2f3a4b5c6d", "hook_event_name": "SessionStart",'
                 ' "source": "compact"}')
        env = clean_env(LANE_ROLE="coordinator", LANE_PERSONA=str(self.persona), XDG_STATE_HOME=str(state),
                        CLAUDE_PROJECT_DIR=str(checkout))
        r = self.hook(env, stdin=start)
        self.assertEqual((r.returncode, r.stdout), (0, PERSONA_TEXT))
        record = state / "lane-control" / "sessions" / pkg.encode_path(checkout.resolve()) / "coordinator.claude"
        self.assertEqual(record.read_text(), "4f1c2e9a-0b7d-4c55-9a8e-1d2f3a4b5c6d\n")
        r = self.hook(clean_env(XDG_STATE_HOME=str(self.t / "state2"), CLAUDE_PROJECT_DIR=str(checkout)), stdin=start)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertFalse((self.t / "state2").exists())  # no markers: nothing recorded
        r = self.hook(env, stdin='{"session_id": "../../x"}')
        self.assertEqual(record.read_text(), "4f1c2e9a-0b7d-4c55-9a8e-1d2f3a4b5c6d\n")  # not an id: kept

    CLAUDE_HOOK_CAP = 10_000  # Claude Code keeps at most this many characters of a hook's output

    def test_hook_persona_under_cap_printed_whole(self):
        text = "".join(f"rule {i:04d}: keep it short\n" for i in range(350))  # about 9,450 characters
        self.assertLess(len(text), 9_800)
        self.persona.write_text(text)
        r = self.hook(clean_env(LANE_ROLE="coordinator", LANE_PERSONA=str(self.persona)))
        self.assertEqual((r.returncode, r.stdout), (0, text))

    def test_hook_long_persona_points_to_file(self):
        text = "".join(f"rule {i:05d}: keep it short\n" for i in range(1000))  # about 28,000 characters
        self.persona.write_text(text)
        r = self.hook(clean_env(LANE_ROLE="coordinator", LANE_PERSONA=str(self.persona)))
        self.assertEqual(r.returncode, 0)
        self.assertLessEqual(len(r.stdout), self.CLAUDE_HOOK_CAP)
        head, _, body = r.stdout.partition("\n\n")
        self.assertIn(str(self.persona), head)
        self.assertIn("Read the whole file now", head)
        self.assertTrue(body.startswith("rule 00000"))
        self.assertTrue(text.startswith(body))
        self.assertTrue(body.endswith("\n"))  # cut at a line end
        self.assertGreater(len(body), 8_000)

    def test_hook_long_multibyte_persona_stays_valid(self):
        self.persona.write_text("Österberg möter Öland på väg.\n" * 700)  # under 22,000 characters, more bytes
        r = subprocess.run([str(HOOK)], capture_output=True, cwd=self.t, timeout=10, input=b"",
                           env=clean_env(LANE_ROLE="coordinator", LANE_PERSONA=str(self.persona)))
        out = r.stdout.decode("utf-8")  # strict: no character cut in half
        self.assertLessEqual(len(out), self.CLAUDE_HOOK_CAP)
        self.assertIn(str(self.persona), out)

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
        saved = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                                                "CLAUDE_CONFIG_DIR")}

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
        os.environ.pop("XDG_STATE_HOME", None)
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        self.checkout = self.t / "code" / "shop.app"
        self.checkout.mkdir(parents=True)
        self.pkg = load_harnesses_pkg()

    def record(self, harness, sid, role="coordinator"):
        """What the hook or plugin writes when a marked session starts or takes a turn."""
        path = self.pkg.session_record(self.checkout, role, harness)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sid + "\n")
        return path


class LoaderTests(HomeTest):
    def test_load_harness_and_harnesses(self):
        all_ = self.pkg.harnesses()
        self.assertEqual(set(all_), {"claude", "opencode"})
        self.assertEqual(self.pkg.load_harness("claude").NAME, "claude")
        self.assertEqual(self.pkg.load_harness("opencode").NAME, "opencode")
        for name in ("codex", "pi", "nope", "../claude", ""):
            with self.assertRaises(ValueError, msg=name):
                self.pkg.load_harness(name)

    def test_session_record_path(self):
        self.assertEqual(self.pkg.session_record(self.checkout, "coordinator", "claude"),
                         self.home / ".local" / "state" / "lane-control" / "sessions" /
                         self.pkg.encode_path(self.checkout.resolve()) / "coordinator.claude")
        self.assertEqual(self.pkg.encode_path(Path("/a/b.c/d_e")), "-a-b-c-d-e")
        odd = self.t / "Öland"
        odd.mkdir()
        self.assertEqual(self.pkg.session_record(odd, "coordinator", "claude").parent.name[-6:], "--land")  # per byte
        os.environ["XDG_STATE_HOME"] = str(self.t / "state")
        self.assertEqual(self.pkg.session_record(self.checkout, "reviewer", "opencode").parent.parent,
                         self.t / "state" / "lane-control" / "sessions")
        self.assertIsNone(self.pkg.read_session(self.checkout, "coordinator", "claude"))
        self.record("claude", "abc-123")
        self.assertEqual(self.pkg.read_session(self.checkout, "coordinator", "claude"), "abc-123")
        self.record("claude", "../../etc/passwd")
        self.assertIsNone(self.pkg.read_session(self.checkout, "coordinator", "claude"))  # not an id

    def test_start_command_refuses_empty_model(self):
        # Review n4: an empty model would start the harness on whatever its config says.
        for name, mod in self.pkg.harnesses().items():
            for model in ("", "  "):
                with self.assertRaises(ValueError, msg=(name, model)):
                    mod.start_command("coordinator", Path("/abs/p.md"), model, None)

    def test_every_adapter_has_the_interface(self):
        for name, mod in self.pkg.harnesses().items():
            self.assertEqual(mod.NAME, name)
            self.assertIn(mod.STATUS, ("in use", "untested"))
            self.assertIsInstance(mod.ORCA_AGENT_ID, str)
            self.assertIsInstance(mod.MODEL_FLAGS, frozenset)
            for fn in ("start_command", "install_persona_hook", "idle_minutes"):
                self.assertTrue(callable(getattr(mod, fn)), (name, fn))


BD_STUB = """#!/bin/sh
[ "$1" = prime ] && case "$*" in *--hook-json*) exit 1 ;; esac
exit 0
"""


class ClaudeBeadsHookTests(HomeTest):
    """lane-control owns the beads session hook: `bd prime` on SessionStart in the uncommitted settings."""

    def setUp(self):
        super().setUp()
        import stat
        from unittest import mock
        self.a = self.pkg.load_harness("claude")
        self.settings = self.checkout / ".claude" / "settings.local.json"
        self.bin = self.t / "bdbin"
        self.bin.mkdir()
        (self.bin / "bd").write_text(BD_STUB)
        (self.bin / "bd").chmod(0o755 | stat.S_IXUSR)
        patcher = mock.patch.dict(os.environ, {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def starts(self):
        import json
        return json.loads(self.settings.read_text())["hooks"]["SessionStart"]

    def commands(self):
        return [h["command"] for e in self.starts() for h in e["hooks"]]

    def test_claude_beads_hook_added_once(self):
        self.a.install_persona_hook(self.checkout)
        changes = self.a.install_beads_hook(self.checkout)
        self.assertEqual(len(changes), 1)
        self.assertIn("bd prime", changes[0])
        self.assertIn("bd prime", self.commands())
        self.assertIn(str(self.pkg.PERSONA_HOOK), " ".join(self.commands()))  # next to the persona hook
        mine = [e for e in self.starts() if any(h["command"] == "bd prime" for h in e["hooks"])]
        self.assertEqual([e["matcher"] for e in mine], ["startup|resume|clear|compact"])
        before = self.settings.read_bytes()
        self.assertEqual(self.a.install_beads_hook(self.checkout), [])  # project-setup says "exists"
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(self.a.install_persona_hook(self.checkout), [])  # the persona hook is not disturbed

    def test_other_settings_kept(self):
        import json
        self.settings.parent.mkdir()
        self.settings.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": []}]}}))
        self.a.install_beads_hook(self.checkout)
        data = json.loads(self.settings.read_text())
        self.assertEqual(data["model"], "opus")
        self.assertEqual(data["hooks"]["Stop"], [{"hooks": []}])

    def test_a_committed_working_hook_means_no_second_one(self):
        import json
        c = self.checkout / ".claude" / "settings.json"
        c.parent.mkdir()
        c.write_text(json.dumps({"hooks": {"SessionStart": [{"matcher": "", "hooks": [
            {"type": "command", "command": "bd prime"}]}]}}))
        self.assertEqual(self.a.install_beads_hook(self.checkout), [])
        self.assertFalse(self.settings.exists())

    def test_a_committed_broken_hook_is_left_and_ours_is_added(self):
        import json
        c = self.checkout / ".claude" / "settings.json"
        c.parent.mkdir()
        c.write_text(json.dumps({"hooks": {"SessionStart": [{"matcher": "", "hooks": [
            {"type": "command", "command": "bd prime --hook-json"}]}]}}))
        before = c.read_bytes()
        self.assertEqual(len(self.a.install_beads_hook(self.checkout)), 1)
        self.assertEqual(c.read_bytes(), before)
        self.assertIn("bd prime", self.commands())

    def prime_at(self, path, event="SessionStart", command="bd prime", matcher=""):
        import json
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"hooks": {event: [{"matcher": matcher, "hooks": [
            {"type": "command", "command": command}]}]}}))
        return path

    def test_a_prime_hook_in_the_user_settings_means_no_second_one(self):
        u = self.prime_at(self.home / ".claude" / "settings.json")
        before = u.read_bytes()
        self.assertEqual(self.a.install_beads_hook(self.checkout), [])
        self.assertFalse(self.settings.exists())
        self.assertEqual(u.read_bytes(), before)

    def test_a_committed_shell_line_that_runs_bd_prime_means_no_second_one(self):
        c = self.prime_at(self.checkout / ".claude" / "settings.json", command="bd prime 2>/dev/null || true")
        before = c.read_bytes()
        self.assertEqual(self.a.install_beads_hook(self.checkout), [])
        self.assertFalse(self.settings.exists())
        self.assertEqual(c.read_bytes(), before)

    def test_a_prime_hook_that_is_not_a_session_start_hook_does_not_stop_ours(self):
        for event, matcher in (("PreCompact", ""), ("Stop", ""), ("PostToolUse", "Bash"), ("SessionStart", "compact"),
                               ("SessionStart", "resume|clear")):
            with self.subTest(event=event, matcher=matcher):
                c = self.prime_at(self.checkout / ".claude" / "settings.json", event=event, matcher=matcher,
                                  command="bd prime --memories-only")
                before = c.read_bytes()
                self.assertEqual(len(self.a.install_beads_hook(self.checkout)), 1)
                self.assertIn("bd prime", self.commands())
                self.assertEqual(c.read_bytes(), before)  # the committed file is never touched
                self.settings.unlink()

    def test_a_user_session_start_hook_that_misses_startup_does_not_stop_ours(self):
        u = self.prime_at(self.home / ".claude" / "settings.json", matcher="compact")
        before = u.read_bytes()
        self.assertEqual(len(self.a.install_beads_hook(self.checkout)), 1)
        self.assertEqual(u.read_bytes(), before)

    def test_a_local_prime_hook_under_another_matcher_means_no_second_one(self):
        self.prime_at(self.settings, matcher="startup")
        before = self.settings.read_bytes()
        self.assertEqual(self.a.install_beads_hook(self.checkout), [])
        self.assertEqual(self.settings.read_bytes(), before)

    def test_a_local_prime_hook_that_misses_startup_does_not_stop_ours(self):
        self.prime_at(self.settings, matcher="resume")
        self.assertEqual(len(self.a.install_beads_hook(self.checkout)), 1)
        self.assertEqual(self.commands().count("bd prime"), 2)

    def test_a_shell_form_of_bd_prime_in_the_user_settings_means_no_second_one(self):
        self.prime_at(self.home / ".claude" / "settings.json", command="if command -v bd >/dev/null; then bd prime; fi")
        self.assertEqual(self.a.install_beads_hook(self.checkout), [])

    def test_a_user_hook_that_bd_rejects_does_not_stop_ours(self):
        self.prime_at(self.home / ".claude" / "settings.json", command="bd prime --hook-json")
        self.assertEqual(len(self.a.install_beads_hook(self.checkout)), 1)

    def test_nothing_added_when_bd_is_missing_or_prime_fails(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}):
            self.assertEqual(self.a.install_beads_hook(self.checkout), [])
        self.assertFalse(self.settings.exists())

    def test_invalid_settings_json_is_refused_and_left(self):
        self.settings.parent.mkdir()
        self.settings.write_text("{nope")
        with self.assertRaises(ValueError):
            self.a.install_beads_hook(self.checkout)
        self.assertEqual(self.settings.read_text(), "{nope")


class BeadsHookRecordTests(unittest.TestCase):
    """Harnesses with no beads session hook say so in their record (OpenCode, Codex, pi)."""

    def test_records_say_what_is_covered(self):
        for h in ("opencode", "codex", "pi"):
            text = (HARNESSES / h / "record.md").read_text().lower()
            self.assertIn("beads session hook: not covered", text, h)
        self.assertIn("beads session hook", (HARNESSES / "claude" / "record.md").read_text().lower())

    def test_opencode_has_no_install_beads_hook(self):
        sys.path.insert(0, str(ROOT / "core"))
        import harnesses
        self.assertFalse(hasattr(harnesses.load_harness("opencode"), "install_beads_hook"))


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
        self.assertEqual(self.entries(), [{"matcher": "startup|resume|clear|compact",
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
        self.assertEqual(self.entries(), [{"matcher": "startup|resume|clear|compact",
                                           "hooks": [{"type": "command", "command": str(HOOK)}]}])

    def ours(self):
        return {"matcher": "startup|resume|clear|compact", "hooks": [{"type": "command", "command": str(HOOK)}]}

    def write_starts(self, starts):
        import json
        self.settings.parent.mkdir(exist_ok=True)
        self.settings.write_text(json.dumps({"hooks": {"SessionStart": starts}}))

    def test_claude_hook_write_keeps_text_link_and_mode(self):
        # Review m3: non-ASCII text stays as written; a symlinked settings file stays a link; the mode is kept.
        import json
        real = self.t / "elsewhere" / "settings.json"
        real.parent.mkdir()
        real.write_text(json.dumps({"owner": "Österberg"}, ensure_ascii=False))
        real.chmod(0o600)
        self.settings.parent.mkdir()
        self.settings.symlink_to(real)
        self.assertTrue(self.a.install_persona_hook(self.checkout))
        self.assertTrue(self.settings.is_symlink())
        self.assertIn("Österberg", real.read_text())
        self.assertEqual(real.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(real.read_text())["hooks"]["SessionStart"], [self.ours()])
        self.assertEqual(sorted(p.name for p in real.parent.iterdir()), ["settings.json"])  # no temp file left

    def test_claude_hook_failed_write_leaves_file(self):
        # Review m3: a crash mid-write must not truncate a file that holds the owner's permission rules.
        from unittest import mock
        self.settings.parent.mkdir()
        self.settings.write_text('{"permissions": {"allow": ["Bash(ls)"]}}')
        before = self.settings.read_bytes()
        with mock.patch.object(self.a.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.a.install_persona_hook(self.checkout)
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual([p.name for p in self.settings.parent.iterdir()], ["settings.local.json"])

    def test_claude_hook_whitespace_file_is_empty(self):
        # Review n2: a file with only whitespace is treated like an empty one.
        self.settings.parent.mkdir()
        self.settings.write_text("  \n")
        self.assertTrue(self.a.install_persona_hook(self.checkout))
        self.assertEqual(self.entries(), [self.ours()])

    def test_claude_hook_matcher_covers_clear(self):
        # Review m1: after /clear the context is empty; the persona must come back then too.
        self.a.install_persona_hook(self.checkout)
        self.assertEqual(set(self.entries()[0]["matcher"].split("|")), {"startup", "resume", "clear", "compact"})
        self.write_starts([{"matcher": "startup|resume|compact", "hooks": [{"type": "command", "command": str(HOOK)}]}])
        self.assertTrue(self.a.install_persona_hook(self.checkout))  # an older matcher is brought up to date
        self.assertEqual(self.entries(), [self.ours()])

    def test_claude_hook_beside_foreign_same_matcher(self):
        foreign = {"matcher": "startup|resume|compact", "hooks": [{"type": "command", "command": "/x/other.sh"}]}
        self.write_starts([foreign])
        self.assertTrue(self.a.install_persona_hook(self.checkout))
        self.assertEqual(self.entries(), [foreign, self.ours()])
        self.assertEqual(self.a.install_persona_hook(self.checkout), [])

    def test_claude_hook_mixed_entry_keeps_foreign(self):
        other = {"type": "command", "command": "/x/other.sh", "timeout": 5}
        mixed = {"matcher": "startup|resume|compact", "extra": 1,
                 "hooks": [{"type": "command", "command": "/old/lane-control/core/harnesses/persona-hook.sh"}, other]}
        self.write_starts([mixed])
        changes = self.a.install_persona_hook(self.checkout)
        self.assertTrue(changes)
        self.assertEqual(self.entries(), [{"matcher": "startup|resume|compact", "extra": 1, "hooks": [other]},
                                          self.ours()])
        self.assertEqual(self.a.install_persona_hook(self.checkout), [])

    def test_claude_hook_foreign_script_same_name_kept(self):
        foreign = {"matcher": "startup", "hooks": [{"type": "command", "command": "/opt/othertool/persona-hook.sh"}]}
        self.write_starts([foreign])
        self.assertTrue(self.a.install_persona_hook(self.checkout))
        self.assertEqual(self.entries(), [foreign, self.ours()])

    def test_claude_hook_duplicates_of_ours_collapse(self):
        self.write_starts([self.ours(), self.ours()])
        self.assertTrue(self.a.install_persona_hook(self.checkout))
        self.assertEqual(self.entries(), [self.ours()])

    def test_claude_hook_command_quotes_odd_paths(self):
        import shlex
        from unittest import mock
        odd = self.t / "lane control" / "core" / "harnesses" / "persona-hook.sh"
        with mock.patch.object(self.pkg, "PERSONA_HOOK", odd):
            self.assertTrue(self.a.install_persona_hook(self.checkout))
            command = self.entries()[0]["hooks"][0]["command"]
            self.assertEqual(shlex.split(command), [str(odd)])
            self.assertEqual(self.a.install_persona_hook(self.checkout), [])  # still recognised as ours
        self.assertTrue(self.a.install_persona_hook(self.checkout))  # back to the real path: moved, not added
        self.assertEqual(len(self.entries()), 1)

    def test_claude_hook_refuses_bad_json(self):
        self.settings.parent.mkdir()
        self.settings.write_text("{not json")
        with self.assertRaises(ValueError):
            self.a.install_persona_hook(self.checkout)
        self.assertEqual(self.settings.read_text(), "{not json")

    def test_claude_project_dir_encoding(self):
        self.assertEqual(self.a.project_dir(Path("/a/b.c/d_e")),
                         self.home / ".claude" / "projects" / "-a-b-c-d-e")

    def test_claude_project_dir_follows_config_dir(self):
        # Review m5: a Claude started with CLAUDE_CONFIG_DIR keeps its transcripts there.
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.t / "profile")
        self.assertEqual(self.a.project_dir(Path("/a/b")), self.t / "profile" / "projects" / "-a-b")
        os.environ["CLAUDE_CONFIG_DIR"] = "relative/dir"  # not absolute: ignored
        self.assertEqual(self.a.project_dir(Path("/a/b")), self.home / ".claude" / "projects" / "-a-b")

    def transcripts(self, checkout):
        d = self.a.project_dir(checkout)
        d.mkdir(parents=True)
        return d

    def transcript(self, d, name, age_min, now):
        f = d / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}\n")
        os.utime(f, (now - age_min * 60, now - age_min * 60))

    def test_claude_idle_own_session_only(self):
        # Review M4: another session in the coordinator's checkout (a worker, a reviewer) does not count.
        now = 1_800_000_000.0
        d = self.transcripts(self.checkout)
        self.transcript(d, "coord-1.jsonl", 200, now)
        self.transcript(d, "worker-2.jsonl", 3, now)
        self.record("claude", "coord-1")
        self.assertEqual(self.a.idle_minutes(self.checkout, now), 200)
        self.assertEqual(self.a.idle_minutes(self.checkout, now - 200 * 60 - 30), 0)  # clock skew is not negative
        self.transcript(d, "coord-1/subagents/agent-a.jsonl", 30, now)  # its own sub-agents are its activity
        self.assertEqual(self.a.idle_minutes(self.checkout, now), 30)
        self.assertEqual(self.a.idle_minutes(self.checkout, now, role="design-lead"), None)

    @unittest.skipIf(os.geteuid() == 0, "root reads any folder")
    def test_claude_idle_unreadable_folder_raises(self):
        # Review Focus 4: an unreadable session folder is not "no session"; the night precheck must say why.
        d = self.transcripts(self.checkout)
        (d / "s.jsonl").write_text("{}\n")
        self.record("claude", "s")
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
        now = 1_800_000_000.0
        self.assertIsNone(self.a.idle_minutes(self.checkout, now))
        d = self.transcripts(self.checkout)
        self.transcript(d, "unmarked.jsonl", 5, now)  # a session that never had the markers
        self.assertIsNone(self.a.idle_minutes(self.checkout, now))
        self.record("claude", "gone")  # recorded, transcript removed since
        self.assertIsNone(self.a.idle_minutes(self.checkout, now))


PLUGIN = HARNESSES / "opencode" / "lane-persona.js"
NODE = shutil.which("node")
SCHEMA = ("CREATE TABLE session_v2 (id TEXT PRIMARY KEY, parent_id TEXT, directory TEXT NOT NULL,"
          " time_updated INTEGER NOT NULL);"
          "CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,"
          " time_updated INTEGER NOT NULL);")


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
        self.assertEqual(json.loads(conf[0].split("=", 1)[1]),
                         {"model": "opencode-go/deepseek-v4.1-flash",
                          "agents": {"build": {"model": "opencode-go/deepseek-v4.1-flash"}}})
        self.assertEqual(cmd[cmd.index(conf[0]) + 1:], ["opencode2", "--standalone"])
        self.assertEqual(cmd[cmd.index(conf[0]) + 1], self.a.ORCA_AGENT_ID)  # the v2 command, as Orca detects it
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
        self.assertIn("every running OpenCode server", changes[0])  # review m9: the owner's own sessions too
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

    def project_plugin(self):
        return self.checkout / ".opencode" / "plugins" / "lane-persona.js"

    def test_opencode_plugin_project_level_install(self):
        # UP-6: LANE_OPENCODE_PLUGIN=project puts the plugin in the project's .opencode/plugins/ instead of the user's.
        os.environ["LANE_OPENCODE_PLUGIN"] = "project"
        self.addCleanup(os.environ.pop, "LANE_OPENCODE_PLUGIN", None)
        changes = self.a.install_persona_hook(self.checkout)
        dest = self.project_plugin()
        self.assertEqual(len(changes), 1)
        self.assertIn(str(dest), changes[0])
        self.assertIn("this project", changes[0])
        self.assertEqual(dest.read_bytes(), PLUGIN.read_bytes())
        self.assertFalse(self.plugin_dir().exists())  # nothing in the user's folder
        self.assertEqual(self.a.install_persona_hook(self.checkout), [])
        dest.write_text("// an old copy\n")
        self.assertTrue(self.a.install_persona_hook(self.checkout))
        self.assertEqual(dest.read_bytes(), PLUGIN.read_bytes())
        self.assertEqual(self.a.plugin_path(self.checkout), dest)

    # A minimal plugin of an older lane-control version: our id, no dedupe. Its hash is made a "shipped" one per test.
    OLD_PLUGIN = 'export default { id: "lane-persona", setup() { return undefined; } };\n'

    def with_old_plugin_known(self):
        import hashlib
        self.a.SHIPPED_HASHES = frozenset({hashlib.sha256(self.OLD_PLUGIN.encode()).hexdigest()})
        self.addCleanup(setattr, self.a, "SHIPPED_HASHES", self.a.load_shipped_hashes())
        os.environ["LANE_OPENCODE_PLUGIN"] = "project"
        self.addCleanup(os.environ.pop, "LANE_OPENCODE_PLUGIN", None)
        user = self.plugin_dir() / "lane-persona.js"
        user.parent.mkdir(parents=True, exist_ok=True)
        return user

    def test_opencode_project_install_brings_an_old_shipped_user_copy_up_to_date(self):
        # UP-6 review M3: an older user-level copy has no dedupe, so a project install brings it to this version, but
        # only when its bytes are a version lane-control shipped (R1: never discard someone's own edits).
        user = self.with_old_plugin_known()
        user.write_text(self.OLD_PLUGIN)
        changes = self.a.install_persona_hook(self.checkout)
        self.assertEqual(user.read_bytes(), PLUGIN.read_bytes())
        self.assertEqual(self.project_plugin().read_bytes(), PLUGIN.read_bytes())
        self.assertEqual(len(changes), 2)
        self.assertTrue(any(str(user) in c and "updated" in c for c in changes), changes)
        self.assertEqual(self.a.install_persona_hook(self.checkout), [])

    def test_opencode_project_install_leaves_an_edited_user_copy_and_says_so(self):
        user = self.with_old_plugin_known()
        edited = self.OLD_PLUGIN + "// my own tweak\n"
        user.write_text(edited)
        changes = self.a.install_persona_hook(self.checkout)
        self.assertEqual(user.read_text(), edited)  # not overwritten, no backup needed
        warn = [c for c in changes if str(user) in c]
        self.assertEqual(len(warn), 1, changes)
        self.assertIn("differs from every lane-control version", warn[0])
        self.assertIn("not changed", warn[0])
        self.assertIn("twice", warn[0])
        self.assertEqual(self.project_plugin().read_bytes(), PLUGIN.read_bytes())  # the project copy is still made

    def test_opencode_project_install_never_replaces_a_symlinked_user_copy(self):
        user = self.with_old_plugin_known()
        target = self.t / "dotfiles" / "lane-persona.js"
        target.parent.mkdir()
        target.write_text(self.OLD_PLUGIN)
        user.symlink_to(target)
        changes = self.a.install_persona_hook(self.checkout)
        self.assertTrue(user.is_symlink())
        self.assertEqual(target.read_text(), self.OLD_PLUGIN)
        self.assertTrue(any(str(user) in c and "symlink" in c and "not changed" in c for c in changes), changes)

    def test_opencode_project_install_ignores_another_plugin_and_makes_no_user_copy(self):
        user = self.with_old_plugin_known()
        user.write_text("export default { id: 'someone-elses', setup() {} };\n")  # not ours: left alone
        self.assertEqual(len(self.a.install_persona_hook(self.checkout)), 1)  # only the project copy
        self.assertEqual(user.read_text(), "export default { id: 'someone-elses', setup() {} };\n")
        user.unlink()
        self.a.install_persona_hook(self.checkout)
        self.assertFalse(user.exists())  # none is made

    def test_the_shipped_hash_list_is_well_formed_and_the_current_plugin_is_pinned(self):
        # r3-1: the file names the current plugin's hash too, so changing the plugin fails here until the old hash
        # has been moved into the shipped list and the new one written as current.
        import hashlib
        import re
        text = (HARNESSES / "opencode" / "lane-persona.sha256").read_text()
        lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
        current = [l for l in lines if l.startswith("current ")]
        shipped = [l for l in lines if not l.startswith("current ")]
        self.assertTrue(shipped)
        self.assertEqual(len(current), 1)
        for l in shipped:
            self.assertRegex(l, r"^[0-9a-f]{64}( +#.*)?$")
        self.assertRegex(current[0], r"^current [0-9a-f]{64}( +#.*)?$")
        self.assertEqual(self.a.load_shipped_hashes(), frozenset(l.split()[0] for l in shipped))
        now = hashlib.sha256(PLUGIN.read_bytes()).hexdigest()
        self.assertEqual(current[0].split()[1], now,
                         "lane-persona.js changed: move the old `current` hash into the list of shipped hashes in "
                         "lane-persona.sha256, and write the new one as `current`")
        self.assertNotIn(now, self.a.load_shipped_hashes())

    def test_opencode_project_install_is_silent_about_a_symlink_to_the_current_version(self):
        # r3-3: a link to lane-control's own current file is harmless
        user = self.with_old_plugin_known()
        user.symlink_to(PLUGIN)
        self.assertEqual(self.a.install_persona_hook(self.checkout), [f"{self.project_plugin()}: installed the "
                         "lane-persona plugin; OpenCode servers started in this project load it at once, and it acts "
                         "only in one started with LANE_ROLE and LANE_PERSONA set"])
        self.assertTrue(user.is_symlink())
        copy = self.t / "elsewhere" / "lane-persona.js"  # a link to another file with the same bytes: also fine
        copy.parent.mkdir()
        copy.write_bytes(PLUGIN.read_bytes())
        user.unlink()
        user.symlink_to(copy)
        self.assertEqual(self.a.install_persona_hook(self.checkout), [])
        user.unlink()
        user.symlink_to(self.t / "nowhere.js")  # a broken link is still named
        self.assertTrue(any(str(user) in c and "symlink" in c for c in self.a.install_persona_hook(self.checkout)))

    def test_opencode_plugin_scope_defaults_to_user_and_refuses_the_unknown(self):
        os.environ.pop("LANE_OPENCODE_PLUGIN", None)
        self.assertEqual(self.a.plugin_path(self.checkout), self.plugin_dir() / "lane-persona.js")
        for scope in ("user", ""):
            os.environ["LANE_OPENCODE_PLUGIN"] = scope
            self.assertEqual(self.a.plugin_path(self.checkout), self.plugin_dir() / "lane-persona.js", scope)
        os.environ["LANE_OPENCODE_PLUGIN"] = "both"
        self.addCleanup(os.environ.pop, "LANE_OPENCODE_PLUGIN", None)
        with self.assertRaisesRegex(ValueError, "LANE_OPENCODE_PLUGIN.*user.*project"):
            self.a.install_persona_hook(self.checkout)
        self.assertFalse(self.plugin_dir().exists() or self.project_plugin().exists())

    def db(self, rows, messages=()):
        import sqlite3
        path = self.home / ".local" / "share" / "opencode" / "opencode.db"
        path.parent.mkdir(parents=True)
        c = sqlite3.connect(path)
        c.executescript(SCHEMA)
        c.executemany("INSERT INTO session_v2 VALUES (?, ?, ?, ?)", rows)
        c.executemany("INSERT INTO session_message VALUES (?, ?, ?)", messages)
        c.commit()
        c.close()
        return path

    def test_opencode_idle_own_session_and_its_children(self):
        # Review M4: only the recorded session's family (it, its root, its sub-agent sessions) counts.
        now = 1_800_000_000.0
        ms = lambda age_min: int((now - age_min * 60) * 1000)  # noqa: E731
        here = str(self.checkout.resolve())
        self.db([("coord", None, here, ms(300)), ("child", "coord", here, ms(90)), ("grand", "child", here, ms(250)),
                 ("worker", None, here, ms(3)), ("far", None, str(self.t / "other"), ms(1))],
                [("m1", "child", ms(40)), ("m2", "worker", ms(0)), ("m3", "coord", ms(280))])
        self.record("opencode", "coord")
        self.assertEqual(self.a.idle_minutes(self.checkout, now), 40)
        self.record("opencode", "grand")  # a sub-agent's turn was recorded last: same family
        self.assertEqual(self.a.idle_minutes(self.checkout, now), 40)

    def test_opencode_idle_none_without_session(self):
        now = 1_800_000_000.0
        self.assertIsNone(self.a.idle_minutes(self.checkout, now))
        self.db([("s1", None, str(self.checkout.resolve()), 1)])
        self.assertIsNone(self.a.idle_minutes(self.checkout, now))  # sessions, but none recorded
        self.record("opencode", "not-in-store")
        self.assertIsNone(self.a.idle_minutes(self.checkout, now))

    def test_opencode_idle_v1_store_says_so(self):
        # Review m6: a store with only OpenCode 1.x tables is named as such, not as an unreadable store.
        import sqlite3
        path = self.home / ".local" / "share" / "opencode" / "opencode.db"
        path.parent.mkdir(parents=True)
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, time_updated INTEGER)")
        c.commit()
        c.close()
        self.record("opencode", "s1")
        with self.assertRaisesRegex(sqlite3.DatabaseError, "not an OpenCode 2 store"):
            self.a.idle_minutes(self.checkout, 1_800_000_000.0)

    def test_opencode_idle_db_follows_xdg(self):
        self.db([("s1", None, str(self.checkout.resolve()), 1)])
        self.record("opencode", "s1")
        self.assertIsNotNone(self.a.idle_minutes(self.checkout, 1_800_000_000.0))
        os.environ["XDG_DATA_HOME"] = str(self.t / "data")
        self.assertIsNone(self.a.idle_minutes(self.checkout, 1_800_000_000.0))


@unittest.skipUnless(NODE, "node is not installed")
class OpenCodePluginTests(TempDirTest):
    """lane-persona.js under node, called the way OpenCode 2.2.1 calls a plugin: its default export's setup(ctx),
    then the hooks it registered with ctx.session.hook(name, fn). OpenCodeLiveTests checks the real thing."""

    DRIVER = """
import { pathToFileURL } from "node:url";
const mod = await import(pathToFileURL(process.argv[2]).href);
const def = mod.default;
const out = { id: def?.id, setup: typeof def?.setup, named: Object.keys(mod).filter((k) => k !== "default"),
              hooks: [], turn: null, compaction: null, disposed: 0, tui: null };
const registered = {};
const ctx = { session: { hook: async (name, fn) => {
  out.hooks.push(name);
  registered[name] = fn;
  return { dispose: async () => { out.disposed++; } };
} } };
const dispose = await def.setup(ctx);
const turn = { sessionID: "ses_1", system: [{ type: "text", text: "base" }], messages: [] };
await registered.context?.(turn);
const compaction = { sessionID: "ses_1", system: [], messages: [] };
await registered.compaction?.(compaction);
await registered.context?.({ sessionID: "ses_1" });  // a shape it does not know: no throw
out.turn = turn.system;
out.compaction = compaction.system;
out.tui = typeof (await def.setup({ ui: {} }));  // OpenCode's TUI context has no session hooks
if (typeof dispose === "function") await dispose();
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

    def test_plugin_is_a_v2_definition(self):
        out = self.run_plugin(clean_env())
        self.assertEqual((out["id"], out["setup"], out["named"]), ("lane-persona", "function", []))

    def test_plugin_appends_persona_with_markers(self):
        out = self.run_plugin(clean_env(LANE_ROLE="coordinator", LANE_PERSONA=str(self.persona)))
        part = {"type": "text", "text": PERSONA_TEXT}
        self.assertEqual(out["hooks"], ["context", "compaction"])
        self.assertEqual(out["turn"], [{"type": "text", "text": "base"}, part])
        self.assertEqual(out["compaction"], [part])
        self.assertEqual(out["disposed"], 2)
        self.assertEqual(out["tui"], "undefined")

    def test_plugin_silent_without_markers(self):
        for env in (clean_env(), clean_env(LANE_ROLE="coordinator"), clean_env(LANE_PERSONA=str(self.persona))):
            out = self.run_plugin(env)
            self.assertEqual(out["hooks"], [])
            self.assertEqual((out["turn"], out["compaction"]), ([{"type": "text", "text": "base"}], []))

    def test_plugin_records_own_session(self):
        # Review M4: the marked server says which session is the coordinator's (the idle signal reads it).
        pkg = load_harnesses_pkg()
        state = self.t / "state"
        self.run_plugin(clean_env(LANE_ROLE="coordinator", LANE_PERSONA=str(self.persona), XDG_STATE_HOME=str(state)))
        record = state / "lane-control" / "sessions" / pkg.encode_path(self.t.resolve()) / "coordinator.opencode"
        self.assertEqual(record.read_text(), "ses_1\n")
        self.run_plugin(clean_env(XDG_STATE_HOME=str(self.t / "state2")))
        self.assertFalse((self.t / "state2").exists())

    TWO_COPIES = """
import { pathToFileURL } from "node:url";
// the same plugin loaded from two places (user folder and project folder), as one OpenCode server loads both
const copies = [];
for (const file of process.argv.slice(2)) copies.push((await import(pathToFileURL(file).href)).default);
const hooks = { context: [], compaction: [] };
const ctx = { session: { hook: async (name, fn) => { hooks[name].push(fn); return { dispose: async () => {} }; } } };
for (const c of copies) await c.setup(ctx);
const turn = { sessionID: "ses_1", system: [{ type: "text", text: "base" }], messages: [] };
for (const fn of hooks.context) await fn(turn);
const compaction = { sessionID: "ses_1", system: [], messages: [] };
for (const fn of hooks.compaction) await fn(compaction);
const next = { sessionID: "ses_1", system: [{ type: "text", text: "base" }], messages: [] };
for (const fn of hooks.context) await fn(next);  // the next turn is built from a fresh system prompt
console.log(JSON.stringify({ registered: hooks.context.length, turn: turn.system, compaction: compaction.system,
                             next: next.system }));
"""

    def test_plugin_two_installs_inject_the_persona_once(self):
        # UP-6: a user-level and a project-level copy are both loaded; the persona is still added once per request.
        import json
        second = self.t / "second-copy.mjs"
        second.write_bytes(PLUGIN.read_bytes())
        driver = self.t / "two.mjs"
        driver.write_text(self.TWO_COPIES)
        env = clean_env(LANE_ROLE="coordinator", LANE_PERSONA=str(self.persona), XDG_STATE_HOME=str(self.t / "state"))
        r = subprocess.run([NODE, str(driver), str(self.copy), str(second)], capture_output=True, text=True,
                           cwd=self.t, env=env, timeout=30)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        out = json.loads(r.stdout)
        part = {"type": "text", "text": PERSONA_TEXT}
        self.assertEqual(out["registered"], 2)
        self.assertEqual(out["turn"], [{"type": "text", "text": "base"}, part])
        self.assertEqual(out["compaction"], [part])
        self.assertEqual(out["next"], [{"type": "text", "text": "base"}, part])

    def test_plugin_silent_missing_file(self):
        (self.t / "a-dir.md").mkdir()
        for path in (self.t / "missing.md", self.t / "a-dir.md"):
            out = self.run_plugin(clean_env(LANE_ROLE="coordinator", LANE_PERSONA=str(path)))
            self.assertEqual((out["turn"], out["compaction"]), ([{"type": "text", "text": "base"}], []))


@unittest.skipUnless(NODE, "node is not installed")
class SessionRecordInterfaceTests(HomeTest):
    """UP-6: the session record is a published, stable interface (core/harnesses/session-record.md). These tests pin
    what a reader outside lane-control may rely on, with the two real writers (the Claude hook and the OpenCode
    plugin) producing it."""

    ID = "ses_4f1c2e9a-0b7d"
    DOC = HARNESSES / "session-record.md"

    def write_with_hook(self, sid, role="coordinator"):
        env = clean_env(LANE_ROLE=role, LANE_PERSONA=str(self.t / "p.md"), CLAUDE_PROJECT_DIR=str(self.checkout))
        (self.t / "p.md").write_text(PERSONA_TEXT)
        r = subprocess.run([str(HOOK)], input='{"session_id": "%s"}' % sid, capture_output=True, text=True,
                           cwd=self.t, env=env, timeout=10)
        self.assertEqual(r.returncode, 0)

    def write_with_plugin(self, sid, role="coordinator"):
        (self.t / "p.md").write_text(PERSONA_TEXT)
        mod = self.t / "plugin.mjs"
        mod.write_bytes(PLUGIN.read_bytes())
        driver = self.t / "driver.mjs"
        driver.write_text('import { pathToFileURL } from "node:url";\n'
                          'const def = (await import(pathToFileURL(process.argv[2]).href)).default;\n'
                          'let turn;\nawait def.setup({ session: { hook: async (n, fn) => { if (n === "context") turn = fn; } } });\n'
                          'await turn({ sessionID: process.argv[3], system: [] });\n')
        r = subprocess.run([NODE, str(driver), str(mod), sid], capture_output=True, text=True, cwd=self.checkout,
                           env=clean_env(LANE_ROLE=role, LANE_PERSONA=str(self.t / "p.md")), timeout=30)
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_both_writers_make_the_same_record(self):
        # path: $XDG_STATE_HOME or ~/.local/state, lane-control/sessions/<checkout as one folder name>/<role>.<harness>
        # content: the session id and one newline, written whole (a rename), no other file left in the folder
        os.environ["XDG_STATE_HOME"] = str(self.t / "state")
        self.write_with_hook(self.ID)
        self.write_with_plugin(self.ID, role="design-lead")
        folder = self.t / "state" / "lane-control" / "sessions" / self.pkg.encode_path(self.checkout.resolve())
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["coordinator.claude", "design-lead.opencode"])
        for name in ("coordinator.claude", "design-lead.opencode"):
            self.assertEqual((folder / name).read_bytes(), self.ID.encode() + b"\n", name)
        self.assertEqual(self.pkg.session_record(self.checkout, "coordinator", "claude"), folder / "coordinator.claude")
        self.assertEqual(self.pkg.session_record(self.checkout, "design-lead", "opencode"),
                         folder / "design-lead.opencode")

    def test_a_new_session_replaces_the_record_whole(self):
        os.environ["XDG_STATE_HOME"] = str(self.t / "state")
        self.write_with_hook("first-session")
        self.write_with_hook("second")
        self.assertEqual(self.pkg.read_session(self.checkout, "coordinator", "claude"), "second")
        self.write_with_plugin("first-session")
        self.write_with_plugin("second")
        self.assertEqual(self.pkg.read_session(self.checkout, "coordinator", "opencode"), "second")

    def test_the_writers_skip_an_id_the_reader_would_refuse(self):
        # UP-6 review m2: a 201-character id must not replace a good record (the plugin already checks; the hook too).
        os.environ["XDG_STATE_HOME"] = str(self.t / "state")
        long_id = "a" * 201
        for write, harness in ((self.write_with_hook, "claude"), (self.write_with_plugin, "opencode")):
            write("good-id")
            write(long_id)
            self.assertEqual(self.pkg.read_session(self.checkout, "coordinator", harness), "good-id", harness)
            write("a" * 200)
            self.assertEqual(self.pkg.read_session(self.checkout, "coordinator", harness), "a" * 200, harness)

    def test_an_undecodable_record_is_no_record(self):
        path = self.record("claude", "x")
        path.write_bytes(b"\xff\xfe\x00abc\n")
        self.assertIsNone(self.pkg.read_session(self.checkout, "coordinator", "claude"))

    def test_reader_accepts_only_a_plain_id(self):
        self.assertEqual(self.pkg.SESSION_ID.pattern, "[A-Za-z0-9_-]{1,200}")
        for text, want in (("abc_DEF-123\n", "abc_DEF-123"), ("  abc\r\n", "abc"), ("a" * 200 + "\n", "a" * 200),
                           ("a" * 201 + "\n", None), ("", None), ("\n", None), ("a b\n", None), ("a\nb\n", None),
                           ("../x\n", None), ("a/b\n", None)):
            path = self.record("claude", "x")
            path.write_text(text)
            self.assertEqual(self.pkg.read_session(self.checkout, "coordinator", "claude"), want, repr(text))

    def test_the_interface_is_documented(self):
        text = " ".join(self.DOC.read_text().split())
        for needle in ("lane-control/sessions/<checkout>/<role>.<harness>", "$XDG_STATE_HOME", "~/.local/state",
                       "[A-Za-z0-9_-]{1,200}", "one line", "a trailing newline", "renames it into place",
                       "every byte that is not a letter or a digit becomes `-`", "no record means no marked session",
                       "SessionStart", "session_v2", "lane-idle", "not a notification", "exit 3", "the hook's working directory",
                       "A change to the path, the file format or the id pattern is a breaking change",
                       "harnesses.read_session"):
            self.assertIn(needle, text, needle)


OPENCODE2 = shutil.which("opencode2")
LIVE = os.environ.get("LANE_LIVE_OPENCODE") == "1"


@unittest.skipUnless(LIVE and OPENCODE2, "opt-in: LANE_LIVE_OPENCODE=1 and opencode2 on PATH")
class OpenCodeLiveTests(HomeTest):
    """The real opencode2 (2.x) with the plugin installed by the adapter, in temp HOME/XDG folders, talking to
    tests/stubs/fake_llm.py on 127.0.0.1 (no real provider, no sign-in). Checks the plugin loads without error and
    the persona is in the system prompt of a turn, of the compaction request, and of the turn after it; and that a
    server without the markers never gets it. About 30 s. Run: LANE_LIVE_OPENCODE=1 python3 -m unittest
    tests.test_harnesses.OpenCodeLiveTests"""

    MARK = "LANE-PERSONA-LIVE-CHECK"

    def setUp(self):
        super().setUp()
        import json
        v = subprocess.run([OPENCODE2, "--version"], capture_output=True, text=True, timeout=30).stdout
        if "v2." not in v and not v.strip().startswith("2."):
            self.skipTest(f"opencode2 is not 2.x: {v.strip()}")
        self.version = v.strip()
        for d in ("config", "data", "state", "cache"):
            (self.t / d).mkdir()
        self.persona = self.t / "coordinator.md"
        self.persona.write_text(f"# Coordinator\n{self.MARK}\n")
        os.environ["XDG_CONFIG_HOME"] = str(self.t / "config")
        self.assertTrue(self.pkg.load_harness("opencode").install_persona_hook(self.checkout))
        self.reqlog = self.t / "requests.jsonl"
        self.reqlog.write_text("")
        self.fake = subprocess.Popen([sys.executable, str(ROOT / "tests" / "stubs" / "fake_llm.py"), str(self.reqlog)],
                                     stdout=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, self.fake)
        port = int(self.fake.stdout.readline())
        cmd = self.pkg.load_harness("opencode").start_command("coordinator", self.persona, "lanetest/m", None)
        from_start = json.loads([a for a in cmd if a.startswith("OPENCODE_CONFIG_CONTENT=")][0].split("=", 1)[1])
        self.conf = json.dumps({**from_start, "provider": {"lanetest": {
            "npm": "@ai-sdk/openai-compatible", "name": "lanetest", "models": {"m": {"name": "m"}, "pinned": {}},
            "options": {"baseURL": f"http://127.0.0.1:{port}/v1", "apiKey": "unused"}}}})

    @staticmethod
    def stop(proc):
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()

    def serve(self, markers: bool):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        env = {"PATH": os.environ["PATH"], "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.t / "config"),
               "XDG_DATA_HOME": str(self.t / "data"), "XDG_STATE_HOME": str(self.t / "state"),
               "XDG_CACHE_HOME": str(self.t / "cache"), "OPENCODE_DISABLE_MODELS_FETCH": "1",
               "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_CLAUDE_CODE": "1",
               "OPENCODE_DISABLE_FREE_MODELS": "1", "OPENCODE_SERVER_PASSWORD": "live-check",
               "OPENCODE_CONFIG_CONTENT": self.conf}
        if markers:
            env.update(LANE_ROLE="coordinator", LANE_PERSONA=str(self.persona))
        self.log = self.t / ("serve-markers.log" if markers else "serve-plain.log")
        proc = subprocess.Popen([OPENCODE2, "serve", "--hostname", "127.0.0.1", "--port", str(port), "--print-logs"],
                                cwd=self.checkout, env=env, stdout=subprocess.DEVNULL, stderr=self.log.open("w"))
        self.addCleanup(self.stop, proc)
        for _ in range(120):
            try:
                socket.create_connection(("127.0.0.1", port), 0.5).close()
                break
            except OSError:
                time.sleep(0.5)
        self.port = port
        return proc

    def api(self, method, path, body=None):
        import base64
        import json
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     data=json.dumps(body or {}).encode(),
                                     headers={"content-type": "application/json", "authorization": "Basic " +
                                              base64.b64encode(b"opencode:live-check").decode()})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["data"]

    def requests(self):
        import json
        out = []
        for line in self.reqlog.read_text().splitlines():
            msgs = json.loads(json.loads(line)["body"]).get("messages", [])
            out.append((json.dumps(msgs[:1]), msgs[-1].get("content", "") if msgs else ""))
        return out

    def wait_for(self, last_content_has, n_before):
        for _ in range(120):
            new = self.requests()[n_before:]
            if any(last_content_has in last for _, last in new):
                return
            time.sleep(0.5)
        self.fail(f"no model request with {last_content_has!r}; log: {self.log.read_text()[-2000:]}")

    def session_round(self):
        sid = self.api("POST", "/api/session")["id"]
        n = len(self.requests())
        self.api("POST", f"/api/session/{sid}/prompt", {"text": "first turn"})
        self.wait_for("first turn", n)
        n = len(self.requests())
        self.api("POST", f"/api/session/{sid}/compact")
        self.wait_for("summar", n)
        time.sleep(1)
        n = len(self.requests())
        self.api("POST", f"/api/session/{sid}/prompt", {"text": "after compaction"})
        self.wait_for("after compaction", n)
        self.sid = sid
        return {last.split("\n")[0][:40]: self.MARK in system for system, last in self.requests()}

    def test_live_plugin_loads_and_persona_survives_compaction(self):
        self.serve(markers=True)
        seen = self.session_round()
        log = self.log.read_text()
        self.assertIn("lane-persona.js", log)
        self.assertNotIn("failed to load plugin", log)
        self.assertTrue(seen.get("first turn"), seen)
        self.assertTrue([v for k, v in seen.items() if "summar" in k.lower()][0], seen)
        self.assertTrue(seen.get("after compaction"), seen)
        # the idle signal reads the session the plugin recorded, from the store the server wrote
        os.environ["XDG_STATE_HOME"] = str(self.t / "state")
        os.environ["XDG_DATA_HOME"] = str(self.t / "data")
        self.assertEqual(self.pkg.read_session(self.checkout, "coordinator", "opencode"), self.sid)
        self.assertIn(self.pkg.load_harness("opencode").idle_minutes(self.checkout, time.time()), (0, 1))

    def test_live_model_wins_over_a_pinned_agent_model(self):
        # Review m4: a model pinned on the primary agent in the user's own config must not win over the chosen one.
        import json
        (self.t / "config" / "opencode" / "opencode.json").write_text(
            json.dumps({"agents": {"build": {"model": "lanetest/pinned"}}}))
        self.serve(markers=True)
        self.session_round()
        models = {json.loads(json.loads(line)["body"]).get("model") for line in self.reqlog.read_text().splitlines()}
        self.assertNotIn("pinned", models)
        self.assertIn("m", models)

    def test_live_no_persona_without_markers(self):
        self.serve(markers=False)
        seen = self.session_round()
        self.assertFalse(any(seen.values()), seen)


CLAUDE = shutil.which("claude")
LIVE_CLAUDE = os.environ.get("LANE_LIVE_CLAUDE") == "1"


@unittest.skipUnless(LIVE_CLAUDE and CLAUDE, "opt-in: LANE_LIVE_CLAUDE=1 and claude on PATH")
class ClaudeLiveTests(TempDirTest):
    """One real non-interactive Claude run (Haiku, the owner's own sign-in) in a temp checkout with the hook
    installed by the adapter: the persona reaches the session, the hook records the session, and the idle signal
    reads its transcript. It leaves that one transcript in ~/.claude/projects/. Run: LANE_LIVE_CLAUDE=1 python3 -m
    unittest tests.test_harnesses.ClaudeLiveTests"""

    MARK = "LANE-PERSONA-LIVE-CLAUDE-4821"

    def test_live_persona_session_record_and_idle(self):
        pkg = load_harnesses_pkg()
        a = pkg.load_harness("claude")
        checkout = self.t / "checkout"
        checkout.mkdir()
        subprocess.run(["git", "init", "-q", str(checkout)], check=True)
        self.assertTrue(a.install_persona_hook(checkout))
        persona = checkout / ".lane-control" / "coordinator.md"
        persona.parent.mkdir()
        persona.write_text(f"# Persona {self.MARK}\n\nYou are a test coordinator. Answer in one line.\n")
        state = self.t / "state"
        env = clean_env(LANE_ROLE="coordinator", LANE_PERSONA=str(persona), XDG_STATE_HOME=str(state))
        r = subprocess.run([CLAUDE, "-p", "--model", "haiku", "Quote the first line of your persona exactly, "
                            "nothing else."], cwd=checkout, env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn(self.MARK, r.stdout)
        saved = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = str(state)
        try:
            sid = pkg.read_session(checkout, "coordinator", "claude")
            self.assertIsNotNone(sid, sorted(p.name for p in state.rglob("*")))
            self.assertTrue((a.project_dir(checkout.resolve()) / f"{sid}.jsonl").is_file())
            self.assertIn(a.idle_minutes(checkout, time.time()), (0, 1))
        finally:
            if saved is None:
                os.environ.pop("XDG_STATE_HOME")
            else:
                os.environ["XDG_STATE_HOME"] = saved


class RecordTests(unittest.TestCase):
    """Task 1.4: every harness has a dated record; the checklist has the harness points."""

    def status(self, name):
        import re
        text = (HARNESSES / name / "record.md").read_text()
        m = re.search(r"^Status: (.+)$", text, re.M)
        self.assertIsNotNone(m, name)
        return m.group(1).strip(), text

    def test_records_exist_with_status(self):
        pkg = load_harnesses_pkg()
        for name in ("claude", "opencode"):
            status, text = self.status(name)
            self.assertEqual(status, "in use", name)
            self.assertEqual(pkg.load_harness(name).STATUS, status, name)
            self.assertIn("## Trials", text)
        for name in ("codex", "pi"):
            status, text = self.status(name)
            self.assertEqual(status, "untested", name)
            self.assertFalse((HARNESSES / name / "adapter.py").exists(), name)
            self.assertIn("1.4.250", text, name)
        oc = self.status("opencode")[1]
        for needle in ("opencode2 --standalone", "LANE_LIVE_OPENCODE=1", "export default", '"context"',
                       '"compaction"', "### 2026-11-19 live check (OpenCode 2.2.1)"):
            self.assertIn(needle, " ".join(oc.split()), needle)
        self.assertNotIn("experimental.", oc)  # the v1 hook names do not exist in v2
        cl = " ".join(self.status("claude")[1].split())
        for needle in ("10,000 characters", "lane-control/sessions/", "<role>.claude", "subagents",
                       "LANE_LIVE_CLAUDE=1", "### 2026-11-19 live check (Claude Code"):
            self.assertIn(needle, cl, needle)
        self.assertIn("<role>.opencode", " ".join(oc.split()))
        pi = " ".join(self.status("pi")[1].split())
        self.assertIn("Unconfirmed: that omp is a separate program built on pi", pi)  # review m8: inference marked

    def test_records_are_public_safe(self):
        for f in HARNESSES.glob("*/record.md"):
            text = f.read_text()
            for private in ("/home/", "/mnt/", "/nix/store", "~/code/"):  # machine and project names: test_content.PrivateNamesTests
                self.assertNotIn(private, text, (f, private))

    def test_checklist_has_harness_points(self):
        text = (ROOT / "orchestrators" / "checklist.md").read_text()
        self.assertIn("## Harness trials", text)
        section = text.split("## Harness trials", 1)[1].lower()
        for point in ("start", "messages", "wake", "compaction", "cost"):
            self.assertIn(point, section, point)
        self.assertIn("core/harnesses/<name>/record.md", section)
        self.assertIn("inherit", section)  # review m7: what a marked session starts gets the markers too


class AwayDenyTests(HomeTest):
    """Away-improve mode: deny rules that back up away-merge while improve is on (lc-yzb, task 5)."""

    def setUp(self):
        super().setUp()
        self.a = self.pkg.load_harness("claude")
        self.settings = self.checkout / ".claude" / "settings.local.json"

    def data(self):
        import json
        return json.loads(self.settings.read_text())

    def write(self, obj):
        import json
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(json.dumps(obj, indent=2) + "\n")

    OURS = ["Bash(gh pr merge:*)", "Bash(git push origin develop:*)", "Bash(git push origin HEAD:develop:*)",
            "Bash(git push origin HEAD:refs/heads/develop:*)"]

    def test_denies_added_and_removed_exactly(self):
        before = {"permissions": {"allow": ["Bash(ls:*)"]}, "model": "sonnet", "hooks": {"SessionStart": []}}
        self.write(before)
        lines = self.a.install_away_denies(self.checkout, "develop")
        self.assertTrue(lines and all(isinstance(l, str) for l in lines))
        d = self.data()
        self.assertEqual(d["permissions"]["deny"], self.OURS)
        self.assertEqual(d["permissions"]["allow"], ["Bash(ls:*)"])
        self.assertEqual(d["model"], "sonnet")
        self.assertTrue(self.a.remove_away_denies(self.checkout))
        self.assertEqual(self.data(), before)

    def test_no_file_before_none_after(self):
        self.a.install_away_denies(self.checkout, "main")
        self.assertIn("Bash(git push origin main:*)", self.data()["permissions"]["deny"])
        self.a.remove_away_denies(self.checkout)
        self.assertFalse(self.settings.exists())

    def test_install_twice_adds_once(self):
        self.a.install_away_denies(self.checkout, "develop")
        self.assertEqual(self.a.install_away_denies(self.checkout, "develop"), [])
        self.assertEqual(self.data()["permissions"]["deny"], self.OURS)
        self.a.remove_away_denies(self.checkout)
        self.assertFalse(self.settings.exists())

    def test_existing_denies_untouched(self):
        self.write({"permissions": {"deny": ["Bash(rm:*)", "Bash(gh pr merge:*)"]}})
        self.a.install_away_denies(self.checkout, "develop")
        self.assertEqual(self.data()["permissions"]["deny"], ["Bash(rm:*)", "Bash(gh pr merge:*)", *self.OURS[1:]])
        self.a.remove_away_denies(self.checkout)
        # the owner's own entries stay, including the one that was theirs before ours
        self.assertEqual(self.data(), {"permissions": {"deny": ["Bash(rm:*)", "Bash(gh pr merge:*)"]}})

    def test_denies_the_owner_added_after_stay(self):
        self.a.install_away_denies(self.checkout, "develop")
        d = self.data()
        d["permissions"]["deny"].append("Bash(curl:*)")
        self.write(d)
        self.a.remove_away_denies(self.checkout)
        self.assertEqual(self.data(), {"permissions": {"deny": ["Bash(curl:*)"]}})

    def test_remove_without_install_does_nothing(self):
        self.write({"permissions": {"deny": ["Bash(gh pr merge:*)"]}})
        self.assertEqual(self.a.remove_away_denies(self.checkout), [])
        self.assertEqual(self.data(), {"permissions": {"deny": ["Bash(gh pr merge:*)"]}})

    def test_a_branch_name_cannot_smuggle_a_rule(self):
        for bad in ("", "a b", "x:*)", "main\nBash(rm:*)", "-f"):
            with self.assertRaises(ValueError, msg=bad):
                self.a.install_away_denies(self.checkout, bad)
        self.assertFalse(self.settings.exists())

    def test_invalid_settings_refused_and_left(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("{not json")
        with self.assertRaises(ValueError):
            self.a.install_away_denies(self.checkout, "main")
        self.assertEqual(self.settings.read_text(), "{not json")
        self.write({"permissions": ["odd"]})
        with self.assertRaises(ValueError):
            self.a.install_away_denies(self.checkout, "main")

    @staticmethod
    def blocked(rules, command):
        """Whether a Bash deny rule `Bash(<prefix>:*)` matches any part of a compound command: Claude checks each
        subcommand of `a && b; c | d` on its own, by prefix, with no notion of the repository or a `cd` before it."""
        import re
        parts = [p.strip() for p in re.split(r"&&|\|\||;|\||\n", command)]
        prefixes = [r[len("Bash("):-len(":*)")] for r in rules if r.startswith("Bash(") and r.endswith(":*)")]
        return any(p == x or p.startswith(x + " ") for p in parts for x in prefixes)

    def test_the_beads_backup_is_not_blocked_but_an_integration_push_is(self):
        rules = self.a.away_deny_rules("main")
        for ok in ("git -C /srv/proj-beads push", "git push", "bd dolt push", "cd ../proj-beads && git push && bd dolt push",
                   "git -C ../proj-beads push origin main", "git push origin feature-x", "gh pr view 3"):
            self.assertFalse(self.blocked(rules, ok), ok)
        for bad in ("git push origin main", "git push origin HEAD:main", "git push origin HEAD:refs/heads/main",
                    "gh pr merge 5 --merge", "cd /x && git push origin main"):
            self.assertTrue(self.blocked(rules, bad), bad)
        # the recorded gaps (claude/record.md, the skill): a prefix rule gets past `-C`, other refspecs and `-f`, and it
        # cannot tell the beads repo's `main` from the project's, which is why the persona pushes it with `git -C`
        for gap in ("git -C /srv/proj push origin main", "git push origin feature:main", "git push -f origin main"):
            self.assertFalse(self.blocked(rules, gap), gap)
        self.assertTrue(self.blocked(rules, "cd ../proj-beads && git push origin main"))

    def test_opencode_has_a_guard_not_a_gap(self):
        # OpenCode's counterpart is the lane-away-guard plugin (tests/test_opencode_guard.py): the adapter records the
        # project and its integration branch for it, and writes nothing in the checkout
        oc = self.pkg.load_harness("opencode")
        self.assertTrue(oc.install_away_denies(self.checkout, "main", "shop"))
        self.assertEqual(oc.install_away_denies(self.checkout, "main", "shop"), [])
        self.assertTrue(oc.remove_away_denies(self.checkout))
        self.assertEqual(oc.remove_away_denies(self.checkout), [])
        self.assertEqual(list(self.checkout.iterdir()), [])
        self.assertIn("away-improve", " ".join((HARNESSES / "claude" / "record.md").read_text().split()))

    def test_every_adapter_has_the_deny_functions(self):
        for name, mod in self.pkg.harnesses().items():
            for fn in ("install_away_denies", "remove_away_denies"):
                self.assertTrue(callable(getattr(mod, fn)), (name, fn))


if __name__ == "__main__":
    unittest.main()
