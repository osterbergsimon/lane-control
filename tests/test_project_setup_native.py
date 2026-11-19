"""project-setup's native-Orca path (phase 3 of docs/superpowers/plans/2026-11-19-native-orca-setup.md) and
project-pause on it, against the stub orca executable (tests/stubs/fake.py) in a temporary HOME."""
import json
import os
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

from test_project_setup import Env, ROOT, git, load as load_cotal  # noqa: F401  (the shared stub environment)

CORE = ROOT / "core" / "scripts"
SETUP = CORE / "project-setup"
PAUSE = CORE / "project-pause"


def load():
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("project_setup_native_t", str(SETUP))
    spec = importlib.util.spec_from_loader("project_setup_native_t", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class NativeEnv(Env):
    """Env plus opencode2 and bd; runs project-setup with no orchestrator flag unless a test passes one."""

    def __init__(self):
        super().__init__()
        p = self.bin / "opencode2"
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)
        # bd: `bd recall handover-latest` prints $STUB_STATE's handover (the stub file), `bd where` fails.
        bd = self.bin / "bd"
        bd.write_text("#!/bin/sh\n[ \"$1\" = recall ] || exit 1\n"
                      f"cat '{self.home}/handover' 2>/dev/null\nexit 0\n")
        bd.chmod(0o755)
        self.env["XDG_CONFIG_HOME"] = str(self.home / ".config")
        self.env["XDG_STATE_HOME"] = str(self.home / ".local" / "state")

    def run(self, *args, adapt=False, env_extra=None, script=SETUP):
        args = [*args, *([] if adapt else ["--skip-adapt"])]
        return subprocess.run([sys.executable, str(script), *args], env={**self.env, **(env_extra or {})},
                              capture_output=True, text=True)

    def pause(self, *args, env_extra=None):
        return subprocess.run([sys.executable, str(PAUSE), *args], env={**self.env, **(env_extra or {})},
                              capture_output=True, text=True)

    def stub_state(self):
        return json.loads(self.state.read_text()) if self.state.exists() else {}

    def creates(self):
        """The argv of every `orca terminal create`, parsed back from the log."""
        return [shlex.split(c)[1:] for c in self.calls() if c.startswith("orca terminal create")]


def opt(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


class Case(unittest.TestCase):
    def setUp(self):
        self.e = NativeEnv()
        self.addCleanup(self.e.close)
        self.repo = self.e.repo()


# --- Task 3.1 ------------------------------------------------------------------------------------------------------

class MoveTests(unittest.TestCase):
    def test_scripts_live_in_core_with_links_at_the_old_paths(self):
        for name in ("project-setup", "project-pause"):
            real = CORE / name
            self.assertTrue(real.is_file() and not real.is_symlink(), name)
            self.assertTrue(os.access(real, os.X_OK), name)
            for old in (ROOT / "scripts" / name, ROOT / "orchestrators" / "orca-cotal" / "scripts" / name):
                self.assertTrue(old.is_symlink(), old)
                self.assertEqual(old.resolve(), real.resolve(), old)


class OrchestratorChoiceTests(Case):
    def test_default_is_native_orca(self):
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertFalse([c for c in self.e.calls() if c.startswith("cotal")], self.e.calls())
        self.assertEqual(self.e.settings(self.repo)["orchestrator"], "native-orca")

    def test_a_cotal_project_keeps_orca_cotal_without_the_flag(self):
        agents = self.repo / ".cotal" / "agents"
        agents.mkdir(parents=True)
        (agents / "coordinator.md").write_text("---\nagent: claude\nmodel: opus\n---\n")
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue([c for c in self.e.calls() if c.startswith("cotal space")], self.e.calls())
        self.assertEqual(self.e.settings(self.repo)["orchestrator"], "orca-cotal")

    def test_the_stored_orchestrator_is_kept_without_the_flag(self):
        r = self.e.run(str(self.repo), "--no-start", "--orchestrator", "orca-cotal")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.e.log.unlink()
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue([c for c in self.e.calls() if c.startswith("cotal space")], self.e.calls())

    def test_the_flag_wins_over_the_store(self):
        self.e.run(str(self.repo), "--no-start", "--orchestrator", "orca-cotal")
        self.e.log.unlink()
        r = self.e.run(str(self.repo), "--no-start", "--orchestrator", "native-orca")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertFalse([c for c in self.e.calls() if c.startswith("cotal")], self.e.calls())
        self.assertEqual(self.e.settings(self.repo)["orchestrator"], "native-orca")

    def test_an_unknown_orchestrator_is_a_usage_error(self):
        r = self.e.run(str(self.repo), "--orchestrator", "rove")
        self.assertEqual(r.returncode, 2)

    def test_orca_cotal_adapter_names_its_orchestrator(self):
        text = (ROOT / "orchestrators" / "orca-cotal" / "adapter.py").read_text()
        self.assertTrue('"--no-start", "--orchestrator", "orca-cotal"' in text, "setup must name orca-cotal")


class RolePinTests(Case):
    def test_hosted_opencode_role_is_refused_naming_the_rule(self):
        for flag in ("--coordinator", "--design-lead", "--reviewer"):
            for model in ("opencode/opencode-go/deepseek-v4.1-flash", "opencode/opencode/big-pickle"):
                r = self.e.run(str(self.repo), flag, model, "--no-start")
                self.assertEqual(r.returncode, 2, (flag, model, r.stderr))
                self.assertIn("lane-tier", r.stderr)
                self.assertIn("model-routing.md", r.stderr)
        self.assertEqual(self.e.calls(), [])  # refused before anything ran

    def test_an_own_opencode_model_is_taken_for_every_role(self):
        r = self.e.run(str(self.repo), "--coordinator", "opencode/gw/m1", "--design-lead", "opencode/gw/m1",
                       "--reviewer", "opencode/gw/m1", "--own-model", "gw/m1", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.e.settings(self.repo)["models"],
                         {"coordinator": "opencode/gw/m1", "design-lead": "opencode/gw/m1", "reviewer": "opencode/gw/m1"})

    def test_an_opencode_model_not_declared_own_is_refused(self):
        r = self.e.run(str(self.repo), "--coordinator", "opencode/gw/m1", "--no-start")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--own-model", r.stderr)

    def test_opencode_role_refuses_claude_only_permission_modes(self):
        for flag in ("--coordinator", "--design-lead"):
            for mode in ("acceptEdits", "plan", "dontAsk"):
                r = self.e.run(str(self.repo), flag, "opencode/gw/m1", "--own-model", "gw/m1",
                               "--permission-mode", mode)
                self.assertEqual(r.returncode, 2, (flag, mode, r.stderr))
                self.assertIn("OpenCode", r.stderr)
                self.assertIn(mode, r.stderr)
        self.assertEqual(self.e.creates(), [])

    def test_claude_roles_take_every_claude_mode(self):
        for mode in ("auto", "bypassPermissions", "default", "acceptEdits", "plan", "dontAsk"):
            r = self.e.run(str(self.repo), "--permission-mode", mode, "--no-start")
            self.assertEqual(r.returncode, 0, (mode, r.stderr))
            self.assertEqual(self.e.settings(self.repo)["permission_mode"], mode)

    def test_cotal_path_keeps_its_three_modes(self):
        r = self.e.run(str(self.repo), "--orchestrator", "orca-cotal", "--permission-mode", "plan", "--no-start")
        self.assertEqual(r.returncode, 2)
        self.assertIn("auto, bypassPermissions, default", r.stderr)


if __name__ == "__main__":
    unittest.main()
