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


if __name__ == "__main__":
    unittest.main()
