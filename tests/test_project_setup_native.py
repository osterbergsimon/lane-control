"""project-setup's native-Orca path (phase 3 of docs/superpowers/plans/2026-11-19-native-orca-setup.md) and
project-pause on it, against the stub orca executable (tests/stubs/fake.py) in a temporary HOME."""
import contextlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from test_project_setup import Env, ROOT, Tty, git  # the shared stub environment

CORE = ROOT / "core" / "scripts"
GENERATED = "AGENTS.lane-control.md"
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


SESSION = """#!/usr/bin/env python3
import json, os, sys
home = os.environ["HOME"]
name = os.path.basename(sys.argv[0])
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(f"session {name} {os.environ.get('LANE_ROLE', '-')}\\n")
with open(os.path.join(home, "sessions.log"), "a") as f:
    f.write(json.dumps({"bin": name, "argv": sys.argv[1:], "role": os.environ.get("LANE_ROLE"),
                        "persona": os.environ.get("LANE_PERSONA"), "cwd": os.getcwd()}) + "\\n")
"""


class NativeEnv(Env):
    """Env plus opencode2 and bd; runs project-setup with no orchestrator flag unless a test passes one."""

    def __init__(self):
        super().__init__()
        for name in ("claude", "opencode2"):  # a started session logs its argv, markers and folder, then ends
            p = self.bin / name
            p.unlink(missing_ok=True)
            p.write_text(SESSION)
            p.chmod(0o755)
        # bd: `bd recall <key>` prints $HOME/<key> (the stub's send writes it), `bd where` fails.
        bd = self.bin / "bd"
        bd.write_text("#!/bin/sh\n[ \"$1\" = recall ] || exit 1\n"
                      f"cat '{self.home}'/\"$2\" 2>/dev/null\nexit 0\n")
        bd.chmod(0o755)
        self.env.pop("COTAL_NAME", None)  # the tests may run inside an agent's shell
        self.env["XDG_CONFIG_HOME"] = str(self.home / ".config")
        self.env["XDG_STATE_HOME"] = str(self.home / ".local" / "state")
        self.env["XDG_DATA_HOME"] = str(self.home / ".local" / "share")
        self.env.pop("CLAUDE_CONFIG_DIR", None)
        self.env["ORCA_TERMINAL_HANDLE"] = "term_owner"  # project-pause runs in one of Orca's terminals

    def run(self, *args, adapt=False, env_extra=None, script=SETUP):
        args = [*args, *([] if adapt else ["--skip-adapt"])]
        return subprocess.run([sys.executable, str(script), *args], env={**self.env, **(env_extra or {})},
                              capture_output=True, text=True)

    def pause(self, *args, env_extra=None):
        return subprocess.run([sys.executable, str(PAUSE), *args], env={**self.env, **(env_extra or {})},
                              capture_output=True, text=True)

    def stub_state(self):
        return json.loads(self.state.read_text()) if self.state.exists() else {}

    def sessions(self):
        f = self.home / "sessions.log"
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def creates(self):
        """The argv of every `orca terminal create`, parsed back from the log."""
        f = Path(str(self.log) + ".argv")
        rows = [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []
        return [r[1:] for r in rows if r[:3] == ["orca", "terminal", "create"]]


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
    def test_any_opencode_model_is_taken_for_every_role(self):
        # owner 2026-11-24: no refusal by model or harness, and no --own-model step
        for model in ("opencode/gw/m1", "opencode/opencode-go/deepseek-v4.1-flash", "opencode/opencode/big-pickle"):
            r = self.e.run(str(self.repo), "--coordinator", model, "--reviewer", model, "--no-start")
            self.assertEqual(r.returncode, 0, (model, r.stderr + r.stdout))
            self.assertEqual(self.e.settings(self.repo)["models"], {"coordinator": model, "reviewer": model})
            self.assertNotIn("lane-tier", r.stderr)

    def test_a_claude_pin_on_any_model_name_is_taken(self):
        r = self.e.run(str(self.repo), "--coordinator", "claude/opencode-go/x", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_opencode_role_refuses_claude_only_permission_modes(self):
        for mode in ("acceptEdits", "plan", "dontAsk"):
            r = self.e.run(str(self.repo), "--coordinator", "opencode/gw/m1",
                           "--permission-mode", mode)
            self.assertEqual(r.returncode, 2, (mode, r.stderr))
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


# --- Task 3.2 ------------------------------------------------------------------------------------------------------

ROLES = ("coordinator", "reviewer", "design-reviewer", "debugger")


class RenderTests(Case):
    def lane(self, role):
        return self.repo / ".lane-control" / f"{role}.md"

    def in_process(self, *args):
        m = load()
        with mock.patch.dict(os.environ, self.e.env, clear=True), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            status = m.main([str(self.repo), *args])
        return m, status, err.getvalue()

    def test_prepare_runs_before_render_persona(self):
        seen = []
        m = load()
        real = m.render_persona

        def spy(text, *a, **kw):
            seen.append(text)
            return real(text, *a, **kw)
        with mock.patch.dict(os.environ, self.e.env, clear=True), mock.patch.object(m, "render_persona", spy), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            status = m.main([str(self.repo), "--skip-adapt", "--no-start", "--with", str(self.e.repo("api"))])
        self.assertEqual(status, 0, err.getvalue())
        self.assertEqual(len(seen), len(ROLES))
        for text in seen:  # render.prepare() filled these, so render_persona never puts the Cotal text in
            self.assertNotIn("{{DESIGN_ROUTE}}", text)
            self.assertNotIn("{{ATTACHED}}", text)

    def test_four_roles_rendered_with_native_text_for_both_harnesses(self):
        for coord in ("claude/opus", "opencode/gw/m1"):
            r = self.e.run(str(self.repo), "--coordinator", coord, "--no-start")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            h, m = coord.split("/", 1)
            for role in ROLES:
                text = self.lane(role).read_text()
                self.assertNotIn("{{", text, role)
                for cotal in ("skill's section", "role `design`)", "cotal", "orca-cotal-lanes"):
                    self.assertNotIn(cotal, text, (role, cotal))
                self.assertIn(f"name: {role}", text)
            coordinator = self.lane("coordinator").read_text()
            self.assertIn(f"agent: {h}\nmodel: {m}\n", coordinator)
            self.assertIn(f"`{self.e.store(self.repo) / 'lanes.tsv'}`", coordinator)
            self.assertIn(str(self.repo / ".lane-control" / "reviewer.md"), coordinator)
            self.assertNotIn("design lead", " ".join(coordinator.split()).lower())

    def test_no_design_lead_renders_no_design_lead_persona(self):
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertFalse(self.lane("design-lead").exists())
        self.assertNotIn("design lead", " ".join(self.lane("coordinator").read_text().split()).lower())

    def test_additions_are_appended_after_the_persona(self):
        add = self.repo / ".lane-control" / "additions"
        add.mkdir(parents=True)
        (add / "coordinator.md").write_text("---\nlane_control_template: 1\nmax_lines: 30\n---\n"
                                            "## First turn\nRead docs/plan.md first.\n")
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        text = self.lane("coordinator").read_text()
        self.assertTrue(text.rstrip().endswith("Read docs/plan.md first."), text[-300:])
        self.assertNotIn("max_lines", text)
        self.assertLess(text.index("## Rules"), text.index("Read docs/plan.md first."))
        self.assertNotIn("Read docs/plan.md first.", self.lane("reviewer").read_text())
        (add / "coordinator.md").write_text("## First turn\nRead docs/new.md first.\n")
        self.e.run(str(self.repo), "--no-start")
        text = self.lane("coordinator").read_text()
        self.assertIn("Read docs/new.md first.", text)
        self.assertNotIn("docs/plan.md", text)

    def test_rendered_personas_and_local_settings_stay_out_of_git(self):
        for _ in range(2):
            r = self.e.run(str(self.repo), "--no-start")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        exclude = (self.repo / ".git" / "info" / "exclude").read_text()
        self.assertEqual(exclude.count("lane-control: begin"), 1)
        self.assertIn("/.opencode/plugins/lane-persona.js", exclude)  # a project-level OpenCode plugin (UP-6)
        status = git("status", "--porcelain", "--untracked-files=all", cwd=self.repo)
        self.assertEqual(status, "")
        self.assertTrue((self.repo / ".claude" / "settings.local.json").is_file())

    def test_a_tracked_cotal_adapted_file_is_refused_with_the_move_to_make(self):
        self.lane("coordinator").parent.mkdir()
        self.lane("coordinator").write_text("adapted by the Orca + Cotal setup\n")
        git("add", ".lane-control/coordinator.md", cwd=self.repo)
        git("commit", "-q", "-m", "adapted", cwd=self.repo)
        r = self.e.run(str(self.repo), "--no-start", "--orchestrator", "native-orca")
        self.assertEqual(r.returncode, 1)
        self.assertIn("git mv .lane-control/coordinator.md .lane-control/additions/coordinator.md", r.stderr)
        self.assertEqual(self.lane("coordinator").read_text(), "adapted by the Orca + Cotal setup\n")

    def test_claude_persona_hook_is_installed_in_the_checkout(self):
        for _ in range(2):
            self.e.run(str(self.repo), "--no-start")
        cfg = json.loads((self.repo / ".claude" / "settings.local.json").read_text())
        starts = cfg["hooks"]["SessionStart"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["matcher"], "startup|resume|clear|compact")
        self.assertIn("persona-hook.sh", starts[0]["hooks"][0]["command"])

    def test_the_beads_session_hook_is_installed_once_next_to_the_persona_hook(self):
        bd = self.e.bin / "bd"
        bd.write_text(bd.read_text().replace('[ "$1" = recall ] || exit 1', '[ "$1" = prime ] && exit 0\n[ "$1" = recall ] || exit 1'))
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("beads-hook claude: updated", r.stdout)
        cfg = json.loads((self.repo / ".claude" / "settings.local.json").read_text())
        cmds = [h["command"] for e in cfg["hooks"]["SessionStart"] for h in e["hooks"]]
        self.assertEqual(len(cmds), 2)
        self.assertIn("bd prime", cmds)
        r = self.e.run(str(self.repo), "--no-start")
        self.assertIn("beads-hook claude: exists", r.stdout)
        cfg2 = json.loads((self.repo / ".claude" / "settings.local.json").read_text())
        self.assertEqual(cfg2, cfg)

    def test_a_plain_beads_folder_without_a_terminal_is_only_described(self):
        (self.repo / ".beads").mkdir()
        (self.repo / ".beads" / "config.yaml").write_text("x: 1\n")
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/acme/app.git"], cwd=self.repo,
                       capture_output=True)
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("beads: would", r.stdout)
        self.assertIn("private GitHub repo", r.stdout)
        self.assertTrue((self.repo / ".beads").is_dir() and not (self.repo / ".beads").is_symlink())

    def origin(self):
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/acme/app.git"], cwd=self.repo,
                       capture_output=True)

    def test_a_project_without_beads_gets_the_restore_described_without_a_terminal(self):
        self.origin()
        (self.e.bin / "gh").write_text("#!/bin/sh\nexit 0\n")
        remotes = self.e.home / "remotes"  # git reaches "GitHub" here, never the network
        subprocess.run(["git", "init", "-q", "--bare", str(remotes / "app-beads.git")], check=True)
        self.e.env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0=f"url.file://{remotes}/.insteadOf",
                          GIT_CONFIG_VALUE_0="https://github.com/acme/")
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("beads: would clone the private GitHub repo acme/app-beads", r.stdout)
        self.assertFalse((self.repo / ".beads").exists() or (self.repo / ".beads").is_symlink())

    def test_a_project_without_beads_on_a_box_not_signed_in_to_github_says_what_is_needed(self):
        self.origin()  # the stub gh exits 1: not signed in
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("fine-grained token", r.stderr)
        self.assertFalse((self.repo / ".beads").exists())

    def test_beads_repo_flag_is_kept_in_the_settings_store(self):
        (self.repo / ".beads").mkdir()
        r = self.e.run(str(self.repo), "--no-start", "--beads-repo", "acme/widget-priv-beads")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("acme/widget-priv-beads", r.stdout)
        st = json.loads((self.e.store(self.repo) / "settings.json").read_text())
        self.assertEqual(st["beads_repo"], "acme/widget-priv-beads")
        r = self.e.run(str(self.repo), "--no-start")  # remembered
        self.assertIn("acme/widget-priv-beads", r.stdout)

    def test_a_bad_beads_repo_is_refused_before_anything_changes(self):
        r = self.e.run(str(self.repo), "--no-start", "--beads-repo", "nonsense")
        self.assertEqual(r.returncode, 1)
        self.assertIn("OWNER/NAME", r.stderr)

    def test_a_broken_committed_beads_hook_is_named(self):
        bd = self.e.bin / "bd"
        bd.write_text('#!/bin/sh\n[ "$1" = prime ] && case "$*" in *--hook-json*) exit 1;; esac\n[ "$1" = prime ] && exit 0\nexit 1\n')
        f = self.repo / ".claude" / "settings.json"
        f.parent.mkdir(exist_ok=True)
        f.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "bd prime --hook-json"}]}]}}))
        before = f.read_bytes()
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("bd prime --hook-json", r.stderr)
        self.assertIn("`bd prime`", r.stderr)
        self.assertEqual(f.read_bytes(), before)

    def test_opencode_plugin_is_installed_for_an_opencode_standing_role(self):
        plugin = Path(self.e.env["XDG_CONFIG_HOME"]) / "opencode" / "plugins" / "lane-persona.js"
        self.e.run(str(self.repo), "--no-start")
        self.assertFalse(plugin.exists())  # no OpenCode role: nothing of OpenCode's is touched
        r = self.e.run(str(self.repo), "--coordinator", "opencode/gw/m1", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(plugin.read_bytes(), (ROOT / "core" / "harnesses" / "opencode" / "lane-persona.js").read_bytes())

    def test_the_away_improve_guard_goes_beside_the_persona_plugin(self):
        guard = Path(self.e.env["XDG_CONFIG_HOME"]) / "opencode" / "plugins" / "lane-away-guard.js"
        self.e.run(str(self.repo), "--no-start")
        self.assertFalse(guard.exists())  # no OpenCode role: nothing of OpenCode's is touched
        r = self.e.run(str(self.repo), "--coordinator", "opencode/gw/m1", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(guard.read_bytes(), (ROOT / "core" / "harnesses" / "opencode" / "lane-away-guard.js").read_bytes())
        self.assertIn("away-guard opencode", r.stdout)


ADDITION = ".lane-control/additions/coordinator.md"
WRITE_NATIVE = {"write": {ADDITION: "## First turn\nRead docs/plan.md.\n", ".lane-control/project.md": "facts\n"}}
PASS = {"say": "Looks right.\nVERDICT: PASS"}


class NativeOnboardingTests(Case):
    def test_onboarding_commits_the_native_section_by_the_adapter(self):
        self.e.script_agents(sonnet=[WRITE_NATIVE], opus=[PASS])
        m = load()
        with mock.patch.dict(os.environ, self.e.env, clear=True), mock.patch("sys.stdin", Tty()), \
                mock.patch("builtins.input", return_value="y"), contextlib.redirect_stdout(Tty()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            status = m.main([str(self.repo), "--no-start"])
        self.assertEqual(status, 0, err.getvalue() + out.getvalue())
        committed = git("show", "--name-only", "--format=", "HEAD", cwd=self.repo).splitlines()
        self.assertEqual(sorted(committed), sorted(["AGENTS.md", GENERATED, ADDITION, ".lane-control/project.md"]))
        agents = (self.repo / "AGENTS.md").read_text()
        generated = (self.repo / GENERATED).read_text()
        self.assertEqual(agents.count("<!-- lane-control:pointer -->"), 1)
        self.assertNotIn("<!-- lane-control:begin -->", agents)
        self.assertNotIn("<!-- orchestrator: native-orca -->", agents)
        self.assertEqual(generated.count("<!-- lane-control:begin -->"), 1)
        self.assertEqual(generated.count("<!-- orchestrator: native-orca -->"), 1)
        adapter_prompt = self.e.agent_runs()[0]["prompt"]
        self.assertIn(".lane-control/additions/", adapter_prompt)
        self.assertIn("Read docs/plan.md.", (self.repo / ".lane-control" / "coordinator.md").read_text())


FAKE_AGENT = ROOT / "tests" / "stubs" / "fake_agent.py"
OC_ADAPTER, OC_REVIEWER = "litellm/code", "litellm/deep"
ALL_OPENCODE = ("--adapter", f"opencode/{OC_ADAPTER}", "--adapt-reviewer", f"opencode/{OC_REVIEWER}",
                "--coordinator", "opencode/litellm/chat", "--reviewer", "opencode/litellm/deep", "--no-start")
GATEWAY_KEY = "sk-example-gateway-key-0000"
GATEWAY_BLOCK = {"npm": "@ai-sdk/openai-compatible", "name": "LiteLLM", "models": {"code": {}, "deep": {}},
                 "options": {"baseURL": "http://127.0.0.1:4000/v1", "apiKey": "{env:LITELLM_API_KEY}",
                             "headers": {"X-Owner": "kept out"}}}
# the owner's config holds more than the provider: none of it may reach an onboarding run (B10)
OWNER_OPENCODE = {"provider": {"litellm": GATEWAY_BLOCK, "other": {"options": {"apiKey": "{env:OTHER}"}}},
                  "formatter": True, "lsp": True, "mcp": {"x": {"type": "local", "command": ["touch", "/nonexistent"]}},
                  "plugin": ["some-plugin"], "permission": {"*": "allow"}, "default_agent": "plan",
                  "instructions": ["~/notes.md"]}


class OpenCodeOnboardingTests(Case):
    """LIT-2 (lc-8h1): --adapter and --adapt-reviewer take opencode/<provider>/<model> and run `opencode2 run`, on a
    machine that may have no Claude at all (a self-hosted gateway only)."""

    def opencode_agents(self, steps: dict, claude=False):
        """opencode2 becomes the agent stub (tests/stubs/fake_agent.py); claude is taken off PATH unless asked for."""
        (self.e.bin / "opencode2").unlink(missing_ok=True)
        (self.e.bin / "opencode2").symlink_to(FAKE_AGENT)
        (self.e.bin / "claude").unlink(missing_ok=True)
        if claude:
            (self.e.bin / "claude").symlink_to(FAKE_AGENT)
        self.e.set_state(agent_steps=steps)
        self.owner_config(OWNER_OPENCODE)
        self.e.env["LITELLM_API_KEY"] = GATEWAY_KEY

    def owner_config(self, conf):
        """The owner's own OpenCode config (~/.config/opencode/opencode.json under the test HOME)."""
        f = Path(self.e.env["XDG_CONFIG_HOME"]) / "opencode" / "opencode.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(conf))

    def setup(self, *args, answer="y", env_extra=None):
        m = load()
        with mock.patch.dict(os.environ, {**self.e.env, **(env_extra or {})}, clear=True), \
                mock.patch("sys.stdin", Tty()), mock.patch("builtins.input", return_value=answer), \
                contextlib.redirect_stdout(Tty()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            status = m.main([str(self.repo), *args])
        self.out, self.err = out.getvalue(), err.getvalue()
        return status

    def config(self, run):
        agents = json.loads(run["config"])["agents"]
        self.assertEqual(len(agents), 1)
        name, conf = next(iter(agents.items()))
        self.assertEqual(run["argv"][run["argv"].index("--agent") + 1], name)
        return conf

    def rules(self, run):
        return [(r["action"], r["resource"], r["effect"]) for r in self.config(run)["permissions"]]

    def test_an_all_opencode_setup_onboards_with_no_claude_on_path(self):
        self.opencode_agents({OC_ADAPTER: [WRITE_NATIVE], OC_REVIEWER: [PASS]})
        status = self.setup(*ALL_OPENCODE, env_extra={"LANE_ROLE": "coordinator", "LANE_PERSONA": "/elsewhere/c.md",
                                                      "OPENCODE_CONFIG_DIR": "/elsewhere", "SECRET_TOKEN": "x"})
        self.assertEqual(status, 0, self.err + self.out)
        self.assertNotIn("claude not on PATH", self.err)
        runs = self.e.agent_runs()
        self.assertEqual([(r["tool"], r["model"]) for r in runs], [("opencode2", OC_ADAPTER), ("opencode2", OC_REVIEWER)])
        names = set()
        for r in runs:
            with self.subTest(model=r["model"]):
                # OpenCode's own non-interactive form; the prompt on stdin (an argument starting with - would be
                # read as a flag); an agent whose name is new each run (m7: no owner config can name it)
                self.assertEqual(r["argv"][:4], ["run", "--standalone", "--model", r["model"]])
                self.assertEqual(r["argv"][4], "--agent")
                self.assertRegex(r["argv"][5], r"^lane-onboard-[0-9a-f]{12}$")
                self.assertEqual(r["argv"][6:], ["--format", "json"])
                names.add(r["argv"][5])
                # it runs in a scratch copy, outside the repo, removed afterwards
                cwd = Path(r["cwd"]).resolve()
                self.assertFalse(cwd.is_relative_to(self.repo.resolve()), cwd)
                self.assertFalse(cwd.exists())
                # B8: the copy is its own empty repo, so OpenCode never takes an enclosing one for the project
                self.assertEqual(Path(r["copy_git"]["toplevel"]).resolve(), cwd)
                self.assertEqual((r["copy_git"]["commits"], r["copy_git"]["hooks"]), ("0", []))
                self.assertIn("lane-control", r["prompt"])
                self.assertEqual(r["env"]["OPENCODE_DISABLE_PROJECT_CONFIG"], "1")  # no project plugin or config
                self.assertIsNone(r["env"]["LANE_ROLE"])  # never a coordinator's persona or guard
                self.assertIsNone(r["env"]["LANE_PERSONA"])
                self.assertEqual(self.config(r)["model"], r["model"])
                self.assertEqual(self.config(r)["mode"], "primary")
                # the key reaches OpenCode by its own variable only: never an argument, never in the config
                self.assertEqual(r["env"]["LITELLM_API_KEY"], GATEWAY_KEY)
                self.assertNotIn(GATEWAY_KEY, " ".join(r["argv"]))
                self.assertNotIn(GATEWAY_KEY, r["config"])
                # B10: OpenCode starts from nothing: HOME and XDG folders of the run's own, one config file in them
                # holding the provider block (key by name only), formatter, lsp and MCP off, and the run's agent
                for var, d in r["dirs"].items():
                    self.assertTrue(d and not Path(d).exists() and not Path(d).is_relative_to(self.e.home), (var, d))
                self.assertEqual(r["config_dir"], ["opencode/opencode.json"])
                conf = json.loads(r["config"])
                self.assertEqual(sorted(conf), ["agents", "autoupdate", "formatter", "instructions", "lsp", "mcp",
                                                "provider", "share"])
                self.assertEqual((conf["formatter"], conf["lsp"], conf["mcp"], conf["instructions"]), (False, False, {}, []))
                self.assertEqual(conf["provider"], {"litellm": {
                    "npm": "@ai-sdk/openai-compatible", "name": "LiteLLM", "models": {"code": {}, "deep": {}},
                    "options": {"apiKey": "{env:LITELLM_API_KEY}", "baseURL": "http://127.0.0.1:4000/v1"}}})
                self.assertIsNone(r["env"]["OPENCODE_CONFIG"])
                self.assertIsNone(r["env"]["OPENCODE_CONFIG_CONTENT"])
                # nothing else of this environment: no marker, no owner OPENCODE_*, no other secret
                allowed = {"PATH", "HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME",
                           "LITELLM_API_KEY", "OPENCODE_DISABLE_PROJECT_CONFIG", "OPENCODE_DISABLE_CLAUDE_CODE",
                           "OPENCODE_DISABLE_AUTOUPDATE", "PWD", "SHLVL", "_"}
                extra = [k for k in r["env_keys"] if k not in allowed and not re.fullmatch(r"LANG|LC_\w+|TZ", k)]
                self.assertEqual(extra, [])
        self.assertEqual(len(names), 2)
        committed = git("show", "--name-only", "--format=", "HEAD", cwd=self.repo).splitlines()
        self.assertIn(ADDITION, committed)  # the adapter's files came back from its copy

    def private_repo(self):
        """A repo with every shape of private file the reviews found (B1-B6, m5), committed where git allows."""
        outside = self.e.home / "app-beads"
        outside.mkdir()
        (outside / "config.yaml").write_text("x\n")
        files = {
            ".gitignore": "data/\n.beads\nsub/secret.key\nsub/AGENTS.md\n*.txt\n!keep.txt\n",
            "docs/.gitignore": "!*.md\n", "data/x.csv": "p\n", ".env": "SECRET=1\n", "sub/secret.key": "k\n",
            "sub/AGENTS.md": "ignored notes\n", "sub/ok.md": "ok\n", "secrets/a.md": "x\n", "private/b.md": "x\n",
            "root.pem": "x\n", "shot1.md": "x\n", "shots/f.png": "x\n", "docs/private/plan.md": "x\n",
            "#notes/a.md": "x\n", "config/keys.md": "x\n", "app/(group)/p.tsx": "x\n", "app/[id]/q.tsx": "x\n",
            "keep.txt": "readable\n", "CLAUDE.md": "# rules\n", "lib/AGENTS.md": "private nested notes\n",
        }
        for n, text in files.items():
            (self.repo / n).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / n).write_text(text)
        (self.repo / ".beads").symlink_to(outside)  # B1: an ignored link to a folder out of the repo
        (self.repo / "self").symlink_to(".")  # B2: a link to a folder holding private files
        (self.repo / "keys").symlink_to(".env")
        (self.repo / "out.md").symlink_to("/etc/hostname")
        (self.repo / "rules.md").symlink_to("CLAUDE.md")  # a link to a readable file
        git("add", "-A", cwd=self.repo)
        git("add", "-f", "keep.txt", cwd=self.repo)
        git("commit", "-qm", "files", cwd=self.repo)
        return ("secrets", "private/", "**/*.pem", "shot[0-9].md", "shots/**", "docs/private/**", "#notes/**",
                "config/**", "app/**", "lib/AGENTS.md")

    def test_the_copy_holds_only_what_the_agents_may_see(self):
        self.opencode_agents({OC_ADAPTER: [WRITE_NATIVE], OC_REVIEWER: [PASS]})
        globs = self.private_repo()
        self.assertEqual(self.setup(*ALL_OPENCODE, *[x for g in globs for x in ("--private", g)]), 0,
                         self.err + self.out)
        adapter, reviewer = self.e.agent_runs()
        for r in (adapter, reviewer):
            with self.subTest(model=r["model"]):
                files = set(r["files"])
                self.assertFalse([f for f in files if f.endswith("@")], files)  # no link at all
                for private in (".env", "data/x.csv", "sub/secret.key", "sub/AGENTS.md", "secrets/a.md",
                                "private/b.md", "root.pem", "shot1.md", "shots/f.png", "docs/private/plan.md",
                                "#notes/a.md", "config/keys.md", "app/(group)/p.tsx", "app/[id]/q.tsx",
                                "lib/AGENTS.md", "keys", "out.md", "self", ".beads"):
                    self.assertNotIn(private, files)
                self.assertFalse([f for f in files if f.startswith((".git/", ".beads/", "self/"))], files)
                for readable in ("CLAUDE.md", "rules.md", "sub/ok.md", "keep.txt", ".gitignore"):
                    self.assertIn(readable, files)
                rules = self.rules(r)
                self.assertEqual(rules[:2], [("*", "*", "deny"), ("read", "*", "allow")])  # second layer
        edits = self.rules(adapter)[2:]  # B10: edit on the adapter's exact paths only, never .lane-control/**
        self.assertEqual({e for _, _, e in edits}, {"allow"})
        self.assertIn(("edit", ADDITION, "allow"), edits)
        self.assertIn(("edit", ".lane-control/SUGGESTIONS.md", "allow"), edits)
        self.assertFalse([r for _, r, _ in edits if "*" in r or not r.endswith(".md")], edits)
        self.assertEqual(self.rules(reviewer)[2:], [])
        for ours in (".lane-control/project.md", "AGENTS.md", GENERATED):  # the reviewer sees onboarding's work
            self.assertIn(ours, reviewer["files"])
        self.assertEqual(self.config(adapter)["steps"], 81)  # 80 turns, then OpenCode's forced last answer
        self.assertEqual(self.config(reviewer)["steps"], 61)

    def test_globs_match_from_the_repo_root_whatever_the_working_folder(self):
        # B5: started from a subfolder, an anchored glob still covers its files
        self.opencode_agents({OC_ADAPTER: [WRITE_NATIVE], OC_REVIEWER: [PASS]})
        (self.repo / "config").mkdir()
        (self.repo / "config" / "keys.md").write_text("x\n")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "a.md").write_text("x\n")
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", "files", cwd=self.repo)
        here = os.getcwd()
        os.chdir(self.repo / "docs")
        self.addCleanup(os.chdir, here)
        self.assertEqual(self.setup(*ALL_OPENCODE, "--private", "config/**"), 0, self.err + self.out)
        files = self.e.agent_runs()[0]["files"]
        self.assertNotIn("config/keys.md", files)
        self.assertIn("docs/a.md", files)

    def test_a_submodule_is_copied_without_its_private_files_and_an_embedded_repo_not_at_all(self):
        self.opencode_agents({OC_ADAPTER: [WRITE_NATIVE], OC_REVIEWER: [PASS]})
        lib = self.e.home / "lib-src"
        lib.mkdir()
        git("init", "-q", "-b", "main", cwd=lib)
        for n, text in (("readme.md", "r\n"), ("key.pem", "k\n"), ("AGENTS.md", "lib notes\n"), (".env", "S=1\n")):
            (lib / n).write_text(text)
        (lib / "up").symlink_to("..")
        git("add", "-A", cwd=lib)
        git("commit", "-qm", "lib", cwd=lib)
        git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "vendor/lib", cwd=self.repo)
        git("commit", "-qm", "submodule", cwd=self.repo)
        embedded = self.repo / "tools" / "x"
        embedded.mkdir(parents=True)
        git("init", "-q", cwd=embedded)
        (embedded / "note.md").write_text("n\n")
        git("add", "-A", cwd=embedded)
        git("commit", "-qm", "x", cwd=embedded)
        self.assertEqual(self.setup(*ALL_OPENCODE, "--private", "**/*.pem", "--private", "vendor/lib/AGENTS.md"), 0,
                         self.err + self.out)
        files = set(self.e.agent_runs()[0]["files"])
        self.assertIn("vendor/lib/readme.md", files)
        for private in ("vendor/lib/key.pem", "vendor/lib/AGENTS.md", "vendor/lib/.env", "vendor/lib/up",
                        "tools/x/note.md"):
            self.assertNotIn(private, files)
        self.assertFalse([f for f in files if ".git" in f.split("/")], files)

    def test_only_lane_control_comes_back_from_the_adapter(self):
        self.opencode_agents({OC_ADAPTER: [{"write": {ADDITION: "x\n", "AGENTS.md": "rewritten\n"}}]})
        before = (self.repo / "AGENTS.md").read_text() if (self.repo / "AGENTS.md").exists() else None
        self.assertEqual(self.setup(*ALL_OPENCODE), 1)
        self.assertIn("the OpenCode adapting agent changed AGENTS.md in its copy of the project, where it may not "
                      "write (the adapter writes only its template files in .lane-control/): nothing it wrote was "
                      "copied back", self.err)
        self.assertFalse((self.repo / ADDITION).exists())
        after = (self.repo / "AGENTS.md").read_text() if (self.repo / "AGENTS.md").exists() else None
        self.assertNotEqual(after, "rewritten\n")
        self.assertEqual(before, None if after is None else before)

    def test_a_git_folder_or_dot_name_from_the_adapter_never_reaches_the_repo(self):
        # B7: a .git the model writes under .lane-control/ would let a later git command there run its config
        for write in ({".lane-control/.git/config": "[core]\n\tfsmonitor = touch PWNED\n",
                       ".lane-control/.git/HEAD": "ref: refs/heads/main\n"},
                      {".lane-control/.GIT": "gitdir: ../elsewhere\n"}, {".lane-control/.hidden.md": "x\n"},
                      {".lane-control/additions/.git/config": "x\n"}, {".lane-control/notes/x.md": "x\n"},
                      {".lane-control/x/HEAD": "ref: x\n", ".lane-control/x/objects/a": "x", ".lane-control/x/refs/b": "x"},
                      {".lane-control/other.md": "not a template\n"}):
            with self.subTest(write=sorted(write)):
                shutil.rmtree(self.repo / ".lane-control", ignore_errors=True)
                self.opencode_agents({OC_ADAPTER: [{"write": {ADDITION: "x\n", **write}}]})
                self.assertEqual(self.setup(*ALL_OPENCODE), 1)
                self.assertIn("where it may not write (the adapter writes only its template files in .lane-control/)",
                              self.err)
                self.assertFalse((self.repo / ".lane-control").exists())  # nothing came back, nothing was made

    def test_a_git_folder_from_a_claude_adapter_stops_before_any_git_command_enters_it(self):
        # B7 on the other harness: a mixed setup, Claude adapter and OpenCode reviewer, and a bare-repo layout too
        for write in ({".lane-control/.git/config": "[core]\n\tfsmonitor = touch PWNED\n"},
                      {".lane-control/x/HEAD": "ref: x\n", ".lane-control/x/objects/a": "x"},
                      # M5: HEAD and commondir, with config, objects/ and refs/ in the folder it names
                      {".lane-control/HEAD": "ref: refs/heads/main\n", ".lane-control/commondir": "store\n",
                       ".lane-control/store/config": "[core]\n\tpager = touch PWNED\n",
                       ".lane-control/store/objects/x": "", ".lane-control/store/refs/x": ""},
                      {".lane-control/sub/gitdir": "/elsewhere\n"}):
            with self.subTest(write=sorted(write)):
                shutil.rmtree(self.repo / ".lane-control", ignore_errors=True)
                self.opencode_agents({"sonnet": [{"write": {ADDITION: "x\n", **write}}], OC_REVIEWER: [PASS]},
                                     claude=True)
                status = self.setup("--adapter", "claude/sonnet", "--adapt-reviewer", f"opencode/{OC_REVIEWER}",
                                    "--no-start")
                self.assertEqual(status, 1)
                self.assertIn("which an onboarding agent must never write: a git command in there could run a "
                              "command its config names", self.err)
                self.assertEqual([r["tool"] for r in self.e.agent_runs()][-1], "claude")  # the reviewer never ran
                self.e.log.with_name(self.e.log.name + ".agents").unlink()

    def test_no_folder_is_made_through_a_link_in_the_repo(self):
        # m8: a link on the way stops the copy back before anything is made, outside the repo too
        outside = self.e.home / "outside"
        outside.mkdir()
        (self.repo / ".lane-control").mkdir()
        (self.repo / ".lane-control" / "additions").symlink_to(outside)
        self.opencode_agents({OC_ADAPTER: [{"write": {ADDITION: "x\n", ".lane-control/project.md": "p\n"}}]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 1)
        self.assertIn(".lane-control/additions in the repo is a link or not a folder", self.err)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.repo / ".lane-control" / "project.md").exists())  # checked all before copying one

    def test_private_globs_cover_at_least_what_git_matches(self):
        # M1: each form bad_glob takes, against git's own matching of the same line
        m = load()
        paths = ["shot1.md", "shotx.md", "shot.md", "a/x/b/c.md", "a/b/y/c.md", "a/b/c.md", "a/x/b/y/c.md", "a/c.md",
                 "secrets/a", "x/secrets/b", "private/b", "root.pem", "d/e.pem", "docs/private/p", "#notes/a",
                 "config/k", "app/(g)/p", "app/[id]/q", "data1/y", "dataX/y", "q/w/e", "b/c", "z/a/b/c.md",
                 "shots/2024/raw/f", "shots/raw/g", "docs/a/private/x", "docs/private/b/x"]
        globs = ["shot[^0-9].md", "shot[!0-9].md", "shot[0-9].md", "a/**/b/**/c.md", "a/**/c.md", "**/b/**",
                 "q/**/e", "**/w/**/e", "secrets", "private/", "**/*.pem", "docs/private/**", "#notes/**", "config/**",
                 "app/**", "data?/**", "*.pem", "**/shots/**/raw", "docs/**/private/**/x"]
        work = self.e.home / "globs"
        git("init", "-q", str(work), cwd=self.e.home)
        for p in paths:
            (work / p).parent.mkdir(parents=True, exist_ok=True)
            (work / p).write_text("x")
        for g in globs:
            with self.subTest(glob=g):
                self.assertFalse(m.bad_glob(g))
                by_git = set(git("ls-files", "-o", "-i", f"--exclude={g}", cwd=work).splitlines())
                self.assertTrue(by_git)
                self.assertEqual(by_git - {p for p in paths if m.private_glob(p, g)}, set())
        # POSIX classes, ./ and empty or . segments (M2): refused, with the way out, on both harnesses
        for g in ("shot[[:digit:]].md", "data[[:alpha:]]/**", "shot[![:alpha:]].txt", "shot[^[:alpha:]].txt",
                  "shot[a[:digit:]].txt", "shot[_[:digit:]].txt", "data[![:alpha:]]/**", "[![:alpha:]].key",  # M4
                  "./secrets", "./config/**", "./notes.txt",
                  "config//keys.txt", "config/./keys.txt"):
            with self.subTest(refused=g):
                self.assertTrue(m.bad_glob(g))
                r = self.e.run(str(self.repo), "--private", g, adapt=True)
                self.assertEqual(r.returncode, 2)
                self.assertIn("with no POSIX class like [[:digit:]] (write [0-9]) and no ./ or //", r.stderr)
        self.assertFalse(m.bad_glob("private/"))  # a trailing / is a folder, as in an ignore line

    def test_a_failed_run_brings_nothing_back(self):
        # m13: a write, then the gateway fails (or the steps run out): the repo keeps what it had
        (self.repo / ".lane-control").mkdir()
        (self.repo / ".lane-control" / "project.md").write_text("earlier run's\n")
        for step in ({"write": {".lane-control/project.md": "HALF\n"}, "error": "gateway fell over", "exit": 1},
                     {"write": {".lane-control/project.md": "HALF\n"}, "steps": 80, "say": "out of steps"}):
            with self.subTest(step=sorted(step)):
                self.opencode_agents({OC_ADAPTER: [step]})
                self.assertEqual(self.setup(*ALL_OPENCODE, "--readapt"), 1)
                self.assertIn("Nothing it wrote was copied back", self.err)
                self.assertEqual((self.repo / ".lane-control" / "project.md").read_text(), "earlier run's\n")

    def test_a_copy_back_that_fails_half_way_leaves_nothing(self):
        # m14: every file goes beside its place first; an error removes them all, the repo as it was
        m = load()
        top = self.e.home / "copy"
        (top / ".lane-control").mkdir(parents=True)
        (self.repo / ".lane-control").mkdir()
        (self.repo / ".lane-control" / "project.md").write_text("owner's\n")
        before = m.tree_state(top) | {".lane-control/project.md": "old"}
        (top / ".lane-control" / "project.md").write_text("new\n")
        (top / ".lane-control" / "routing.md").write_text("new\n")
        calls = []

        def copy(src, dst, **_):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError(28, "No space left on device", str(dst))
            Path(dst).write_bytes(Path(src).read_bytes())
        with mock.patch.object(m.shutil, "copyfile", copy), self.assertRaisesRegex(m.Fail, "could not be copied back"):
            m.copy_back(self.repo, top, before, True)
        self.assertEqual(sorted(p.name for p in (self.repo / ".lane-control").iterdir()), ["project.md"])
        self.assertEqual((self.repo / ".lane-control" / "project.md").read_text(), "owner's\n")

    def test_a_role_file_over_a_rendered_persona_becomes_its_additions(self):
        # m15: on a re-run the rendered persona stays in place; the adapter's file there is that role's additions
        self.opencode_agents({OC_ADAPTER: [WRITE_NATIVE], OC_REVIEWER: [PASS]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 0, self.err + self.out)
        persona = (self.repo / ".lane-control" / "reviewer.md").read_text()
        self.opencode_agents({OC_ADAPTER: [{"write": {".lane-control/reviewer.md": "## For the reviewer\nCheck x.\n"}}],
                              OC_REVIEWER: [PASS]})
        self.assertEqual(self.setup(*ALL_OPENCODE, "--readapt", answer="n"), 3, self.err + self.out)
        self.assertEqual((self.repo / ".lane-control" / "additions" / "reviewer.md").read_text(),
                         "## For the reviewer\nCheck x.\n")
        self.assertEqual((self.repo / ".lane-control" / "reviewer.md").read_text(), persona)

    def test_a_private_file_of_ours_is_named_but_never_shown_to_the_reviewer(self):
        # m16: --private covers a tracked .lane-control/ file with an uncommitted edit
        m = load()
        (self.repo / ".lane-control").mkdir()
        (self.repo / ".lane-control" / "owner-notes.md").write_text("old\n")
        git("add", "-f", ".lane-control/owner-notes.md", cwd=self.repo)
        git("commit", "-qm", "notes", cwd=self.repo)
        (self.repo / ".lane-control" / "owner-notes.md").write_text("old\nPRIVATE-NEW-LINE\n")
        m.PRIVATE_GLOBS[:] = [".lane-control/owner-notes.md"]
        prompt = m.review_prompt(self.repo, "app")
        self.assertIn(".lane-control/owner-notes.md", prompt)
        self.assertNotIn("PRIVATE-NEW-LINE", prompt)

    def test_model_text_is_printed_without_control_characters(self):
        # m17: an escape sequence in SUGGESTIONS.md could rewrite the file list above the owner's question
        m = load()
        self.assertEqual(m.plain("a\x1b[2Jb\x1b]52;c;eA==\x07c\td\ne"), "a[2Jb]52;c;eA==c\td\ne")

    def test_a_tmpdir_inside_the_repo_is_refused(self):
        # B8: the copy must never sit in the project's own work tree
        self.opencode_agents({OC_ADAPTER: [WRITE_NATIVE]})
        (self.repo / ".tmp").mkdir()
        self.assertEqual(self.setup(*ALL_OPENCODE, env_extra={"TMPDIR": str(self.repo / ".tmp")}), 1)
        self.assertIn("is inside the repo, where an OpenCode onboarding agent's copy of the project must not be", self.err)
        self.assertEqual(self.e.agent_runs(), [])

    def test_copy_back_checks_every_destination_before_writing_any(self):
        # m9: a link or a folder in the repo where a file goes stops it all, with the other files untouched
        for shape in ("link", "folder"):
            with self.subTest(shape=shape):
                shutil.rmtree(self.repo / ".lane-control", ignore_errors=True)
                (self.repo / ".lane-control").mkdir()
                (self.repo / ".lane-control" / "project.md").write_text("earlier run's\n")  # in the copy too
                target = self.repo / ".lane-control" / "routing.md"
                if shape == "link":
                    target.symlink_to(self.e.home / "elsewhere.md")
                else:
                    target.mkdir()
                self.opencode_agents({OC_ADAPTER: [{"write": {".lane-control/project.md": "model's\n",
                                                              ".lane-control/routing.md": "r\n"}}]})
                self.assertEqual(self.setup(*ALL_OPENCODE, "--readapt"), 1)
                self.assertIn(".lane-control/routing.md in the repo is a link or not a file", self.err)
                self.assertEqual((self.repo / ".lane-control" / "project.md").read_text(), "earlier run's\n")
                self.assertFalse((self.e.home / "elsewhere.md").exists())

    def test_a_repo_file_left_out_of_the_copy_is_never_overwritten(self):
        # m10: an ignored .lane-control/ file the agent never saw stays the owner's
        (self.repo / ".gitignore").write_text(".lane-control/routing.md\n")
        git("add", ".gitignore", cwd=self.repo)
        git("commit", "-qm", "ignore", cwd=self.repo)
        (self.repo / ".lane-control").mkdir()
        (self.repo / ".lane-control" / "routing.md").write_text("owner's local notes\n")
        self.opencode_agents({OC_ADAPTER: [{"write": {".lane-control/routing.md": "model's\n", ADDITION: "x\n"}}]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 1)
        self.assertIn(".lane-control/routing.md is in the repo but was left out of the OpenCode adapter's copy", self.err)
        self.assertEqual((self.repo / ".lane-control" / "routing.md").read_text(), "owner's local notes\n")
        self.assertFalse((self.repo / ADDITION).exists())

    def test_a_glob_over_onboardings_own_files_or_agents_md_is_refused(self):
        # m11, m12: a review that cannot see the adapter's files proves nothing; AGENTS.md's diff is in its prompt
        for g in ("**/*.md", "*.md", ".lane-control/**", "AGENTS.md", "CLAUDE.md"):
            with self.subTest(glob=g):
                if g == "CLAUDE.md":  # AGENTS.md -> CLAUDE.md: the file it links to counts as AGENTS.md
                    (self.repo / "CLAUDE.md").write_text("# rules\n")
                    (self.repo / "AGENTS.md").symlink_to("CLAUDE.md")
                    git("add", "CLAUDE.md", "AGENTS.md", cwd=self.repo)
                    git("commit", "-qm", "rules", cwd=self.repo)
                self.opencode_agents({OC_ADAPTER: [WRITE_NATIVE]})
                self.assertEqual(self.setup(*ALL_OPENCODE, "--private", g), 1)
                self.assertIn(f"--private {g!r} covers", self.err)
                self.assertIn("which onboarding writes or its reviewer must read: narrow the glob", self.err)
                self.assertEqual(self.e.agent_runs(), [])
                st = self.e.store(self.repo) / "settings.json"
                st.write_text(json.dumps({**json.loads(st.read_text()), "private": []}))

    def test_the_reviewer_writes_nothing_back(self):
        self.opencode_agents({OC_ADAPTER: [WRITE_NATIVE], OC_REVIEWER: [{"write": {ADDITION: "changed\n"}, **PASS}]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 1)
        self.assertIn(f"the OpenCode reviewer changed {ADDITION} in its copy", self.err)
        self.assertEqual((self.repo / ADDITION).read_text(), WRITE_NATIVE["write"][ADDITION])

    def test_the_provider_block_must_name_its_key_by_variable(self):
        # B10: the run gets only the adapter's provider block; a key only in OpenCode's auth store is never read
        cases = (
            ({"provider": {}}, "OpenCode's config has no provider 'litellm' for the onboarding agent"),
            ({"provider": {"litellm": {**GATEWAY_BLOCK, "options": {"baseURL": "http://127.0.0.1:4000/v1"}}}},
             "provider 'litellm' has no apiKey, and an OpenCode onboarding run takes the key only from an environment "
             "variable it names"),
            ({"provider": {"litellm": {**GATEWAY_BLOCK, "options": {"apiKey": "sk-literal"}}}},
             "provider 'litellm' has its apiKey written in the config"),
        )
        for conf, said in cases:
            with self.subTest(said=said[:40]):
                self.opencode_agents({OC_ADAPTER: [WRITE_NATIVE]})
                self.owner_config(conf)
                self.assertEqual(self.setup(*ALL_OPENCODE), 1)
                self.assertIn(said, self.err)
                self.assertNotIn("sk-literal", self.err)
                self.assertEqual(self.e.agent_runs(), [])
        self.opencode_agents({OC_ADAPTER: [WRITE_NATIVE]})
        self.e.env.pop("LITELLM_API_KEY")
        self.assertEqual(self.setup(*ALL_OPENCODE), 1)
        self.assertIn("the key's environment variable LITELLM_API_KEY (provider 'litellm' in", self.err)

    def test_a_provider_in_the_owners_config_content_is_found_and_the_rest_left_out(self):
        self.opencode_agents({OC_ADAPTER: [WRITE_NATIVE], OC_REVIEWER: [PASS]})
        self.owner_config({})
        owner = {"provider": {"litellm": GATEWAY_BLOCK}, "formatter": True,
                 "agents": {"lane-onboard": {"permission": {"*": "allow"}}}}
        self.assertEqual(self.setup(*ALL_OPENCODE, env_extra={"OPENCODE_CONFIG_CONTENT": json.dumps(owner)}), 0,
                         self.err + self.out)
        conf = json.loads(self.e.agent_runs()[0]["config"])
        self.assertEqual(conf["formatter"], False)
        self.assertEqual(list(conf["provider"]), ["litellm"])
        self.assertNotIn("lane-onboard", conf["agents"])

    def test_an_empty_opencode_run_is_a_failure_with_what_it_said(self):
        # m2: exit 0 with no answer and no tool call (a help print, an empty answer) is no result
        self.opencode_agents({OC_ADAPTER: [{"err": "Run OpenCode with a message"}]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 1)
        self.assertIn(f"opencode2 run --model {OC_ADAPTER} gave no answer and called no tool: Run OpenCode with a "
                      "message", self.err)
        self.assertNotIn("wrote nothing", self.err)

    def test_the_onboarding_pins_are_kept_per_project(self):
        # m1: a later run without the flags uses the stored ones, never silently the Claude defaults
        self.opencode_agents({OC_ADAPTER: [WRITE_NATIVE], OC_REVIEWER: [PASS]})
        self.assertEqual(self.setup(*ALL_OPENCODE, answer="n"), 3, self.err + self.out)  # stopped before the commit
        models = self.e.settings(self.repo)["models"]
        self.assertEqual((models["adapter"], models["adapt-reviewer"]), (f"opencode/{OC_ADAPTER}", f"opencode/{OC_REVIEWER}"))
        self.e.set_state(agent_steps={OC_REVIEWER: [PASS]})
        self.assertEqual(self.setup("--no-start"), 0, self.err + self.out)  # no flags: the stored pins, no claude
        self.assertEqual([r["model"] for r in self.e.agent_runs()], [OC_ADAPTER, OC_REVIEWER, OC_REVIEWER])
        self.assertIn(f"review: running opencode/{OC_REVIEWER} (from the settings store)", self.out)

    def test_a_default_pin_says_so_where_claude_is_missing(self):
        self.opencode_agents({})
        self.assertEqual(self.setup("--no-start"), 1)
        self.assertIn("claude not on PATH, but the onboarding agents use it (--adapter, --adapt-reviewer not given and "
                      "none in the settings store, so the default; pass opencode/<provider>/<model>", self.err)
        self.assertNotIn("adapter", self.e.settings(self.repo)["models"])  # a default is never written

    def test_the_reviewer_round_runs_on_opencode_too(self):
        fix = {"say": "project.md names the wrong command.\nVERDICT: FIX"}
        self.opencode_agents({OC_ADAPTER: [WRITE_NATIVE, {"write": {".lane-control/project.md": "fixed\n"}}],
                              OC_REVIEWER: [fix, PASS]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 0, self.err + self.out)
        runs = self.e.agent_runs()
        self.assertEqual([r["model"] for r in runs], [OC_ADAPTER, OC_REVIEWER, OC_ADAPTER, OC_REVIEWER])
        self.assertIn("project.md names the wrong command", runs[2]["prompt"])  # the findings reach the fix round
        self.assertEqual((self.repo / ".lane-control" / "project.md").read_text(), "fixed\n")

    def test_a_failed_opencode_run_says_what_opencode_said(self):
        self.opencode_agents({OC_ADAPTER: [{"error": "ECONNREFUSED: Unable to connect.", "exit": 1}]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 1)
        self.assertIn("opencode2 run --model failed: ECONNREFUSED: Unable to connect.", self.err)
        self.assertIn("Nothing was committed", self.err)

    def test_an_opencode_reviewer_out_of_steps_is_a_clean_fail(self):
        self.opencode_agents({OC_ADAPTER: [WRITE_NATIVE], OC_REVIEWER: [{"steps": 60, "say": "I ran out."}]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 1)
        self.assertIn("the reviewer used all 60 turns", self.err)

    def test_opencode2_missing_is_named_before_any_agent(self):
        self.opencode_agents({})
        (self.e.bin / "opencode2").unlink()
        self.assertEqual(self.setup(*ALL_OPENCODE), 1)
        self.assertIn("opencode2 not on PATH, but the onboarding agents run on opencode", self.err)
        self.assertEqual(self.e.agent_runs(), [])

    def test_a_claude_role_still_needs_claude(self):
        self.opencode_agents({OC_ADAPTER: [WRITE_NATIVE]})
        self.assertEqual(self.setup("--adapter", f"opencode/{OC_ADAPTER}", "--no-start"), 1)  # reviewer: claude/opus
        self.assertIn("claude not on PATH, but the onboarding agents use it", self.err)
        self.assertEqual(self.e.agent_runs(), [])

    def test_mixed_harnesses_each_run_their_own(self):
        self.opencode_agents({OC_ADAPTER: [WRITE_NATIVE], "opus": [PASS]}, claude=True)
        self.assertEqual(self.setup("--adapter", f"opencode/{OC_ADAPTER}", "--no-start"), 0, self.err + self.out)
        self.assertEqual([(r["tool"], r["model"]) for r in self.e.agent_runs()],
                         [("opencode2", OC_ADAPTER), ("claude", "opus")])

    def test_refresh_of_an_opencode_project_needs_no_claude(self):
        self.opencode_agents({OC_ADAPTER: [WRITE_NATIVE], OC_REVIEWER: [PASS]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 0, self.err + self.out)
        r = self.e.run(str(self.repo), "--refresh", adapt=True)  # adapt: no --skip-adapt, which --refresh refuses
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("claude", r.stderr)
        self.assertEqual(len(self.e.agent_runs()), 2)  # a refresh adapts nothing



@unittest.skipUnless(os.environ.get("LANE_LIVE_OPENCODE") and shutil.which("opencode2"), "opt-in: LANE_LIVE_OPENCODE=1")
class OpenCodeOnboardingLiveTests(unittest.TestCase):
    """The real opencode2 (2.x) run as project-setup runs an onboarding agent (run_opencode: the scratch copy, its argv
    and config, the prompt on stdin, the copy back), in temp HOME/XDG folders, against tests/stubs/fake_tool_llm.py on
    127.0.0.1 (no real provider, no sign-in), which asks for one tool call per run. Every private file holds a marker
    of its own, and every request the model gets is searched for all of them, so what OpenCode adds by itself (an
    AGENTS.md it attaches, B6) counts as much as a tool result. The owner's own config allows everything and names
    another default agent, the owner's OPENCODE_CONFIG_CONTENT defines an allow-all `lane-onboard` (m7), and the repo
    has its own OpenCode plugin. About 40 s. Run: LANE_LIVE_OPENCODE=1 python3 -m unittest discover -s tests -p
    test_project_setup_native.py -k OpenCodeOnboardingLiveTests"""

    GLOBS = ["secrets", "private/", "**/*.pem", "shot[0-9].md", "shots/**", "docs/private/**", "#notes/**",
             "config/**", "app/**", "lib/AGENTS.md", "shot[^0-9].md", "a/**/b/**/c.md"]

    def setUp(self):
        import tempfile
        v = subprocess.run(["opencode2", "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        if "v2." not in v and not v.startswith("2."):
            self.skipTest(f"opencode2 is not 2.x: {v}")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.t = Path(tmp.name)
        for d in ("home", "config/opencode", "data", "state", "cache", "beads"):
            (self.t / d).mkdir(parents=True)
        self.repo = self.t / "repo"
        self.repo.mkdir()
        git("init", "-q", "-b", "main", cwd=self.repo)
        self.secrets = {}

        def secret(path, base=self.repo):
            mark = f"LIVE-SECRET-{len(self.secrets):02d}"
            self.secrets[mark] = str(path)
            (base / path).parent.mkdir(parents=True, exist_ok=True)
            (base / path).write_text(mark + "\n")

        files = {"AGENTS.md": "root rules readable\n", "CLAUDE.md": "claude-rules\n", "sub/ok.md": "readable-sub\n",
                 "keep.txt": "readable-keep\n", "docs/a.md": "readable-docs\n", "lib/ok.md": "readable-lib\n",
                 # B4: a whitelist .gitignore (its negations must not switch a --private glob off), then the
                 # ignored files, after it, so no negation takes them back
                 ".gitignore": "*\n!*/\n!*.md\n!.gitignore\n!keep.txt\n!*.png\n!*.pem\n!*.tsx\n!*.js\n!opencode.json\n!package.json\n!*.mjs\n"
                               "data/\n.beads\nsub/secret.key\nsub/AGENTS.md\n",
                 "docs/.gitignore": "!*.md\n"}
        for n, text in files.items():
            (self.repo / n).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / n).write_text(text)
        for n in (".env", "data/x.csv", "sub/secret.key", "sub/AGENTS.md", "secrets/a.md", "private/b.md", "root.pem",
                  "shot1.md", "shots/f.png", "docs/private/plan.md", "#notes/a.md", "config/keys.md",
                  "app/(group)/p.tsx", "app/[id]/q.tsx", "lib/AGENTS.md", "shotx.md", "a/x/b/c.md", "a/b/y/c.md"):
            secret(n)
        secret("config.yaml", self.t / "beads")
        secret("outside.md", self.t)
        (self.repo / ".beads").symlink_to(self.t / "beads")  # B1
        (self.repo / "self").symlink_to(".")  # B2
        (self.repo / "keys").symlink_to(".env")
        (self.repo / "out.md").symlink_to(self.t / "outside.md")
        (self.repo / "lib" / "link").mkdir()
        (self.repo / "lib" / "link" / "AGENTS.md").symlink_to(self.t / "outside.md")  # B6: an AGENTS.md link out
        (self.repo / "rules.md").symlink_to("CLAUDE.md")
        # M3: the project's own OpenCode config, tracked so it is in the copy: a plugin, an MCP command and an
        # instructions file; OPENCODE_DISABLE_PROJECT_CONFIG must keep all three out
        (self.repo / ".opencode" / "plugins").mkdir(parents=True)
        (self.repo / ".opencode" / "plugins" / "p.js").write_text(
            f'import fs from "fs"\nfs.writeFileSync({json.dumps(str(self.t / "plugin-ran"))}, "x")\n'
            'export default { id: "p", setup() {} }\n')
        (self.repo / "opencode.json").write_text(json.dumps({
            "instructions": ["instr.md"],
            "mcp": {"x": {"type": "local", "command": ["touch", str(self.t / "mcp-ran")]}}}))
        secret("instr.md")  # in the copy, but only an instructions entry would send it unasked
        # B10: prettier listed and a prettier config in the copy; OpenCode would run it after a write if any config
        # it loads turned formatters on (the owner's below does)
        (self.repo / "package.json").write_text(json.dumps({"devDependencies": {"prettier": "3.3.3"}}))
        (self.repo / "prettier.config.mjs").write_text(
            f'import fs from "fs"\nfs.writeFileSync({json.dumps(str(self.t / "formatter-ran"))}, "x")\n'
            "export default {}\n")
        self.dotrepo = self.t / "dotfiles"  # B8: a HOME kept in git, and a TMPDIR inside it
        (self.dotrepo / "tmp").mkdir(parents=True)
        git("init", "-q", cwd=self.dotrepo)
        secret("private-in-home.md", self.dotrepo)
        secret("owner-instr.md", self.t)  # the owner's own instructions file: never part of an onboarding run
        git("add", "-A", cwd=self.repo)
        git("add", "-f", "keep.txt", "lib/link/AGENTS.md", "self", "keys", "out.md", "rules.md", ".opencode/plugins/p.js",
            "opencode.json", "instr.md", "package.json", "prettier.config.mjs", cwd=self.repo)
        git("commit", "-qm", "init", cwd=self.repo)
        tracked = git("ls-files", cwd=self.repo).splitlines()
        for n in ("sub/AGENTS.md", "sub/secret.key", "data/x.csv", ".beads"):  # really ignored, so never tracked
            self.assertNotIn(n, tracked)
        for n in ("secrets/a.md", "docs/private/plan.md", "#notes/a.md", "lib/AGENTS.md", "app/(group)/p.tsx", "self",
                  "lib/link/AGENTS.md"):
            self.assertIn(n, tracked)  # tracked: private only by --private, or a link
        self.m = load()
        self.m.PRIVATE_GLOBS[:] = self.GLOBS
        copied = self.m.export_files(self.repo)
        for n in (".opencode/plugins/p.js", "opencode.json", "instr.md"):  # M3: so the guard is what keeps them out
            self.assertIn(n, copied)

    def call(self, tool: str, args: dict, write: bool, cwd: "Path | None" = None, tmpdir: "Path | None" = None):
        """One onboarding run whose model asks for `tool` with `args`; returns (tool result, run text). Fails when any
        marker reached the model."""
        log = self.t / "requests.jsonl"
        log.write_text("")
        fake = subprocess.Popen([sys.executable, str(ROOT / "tests" / "stubs" / "fake_tool_llm.py"), str(log)],
                                stdout=subprocess.PIPE, text=True,
                                env={**os.environ, "LANE_FAKE_TOOL": tool, "LANE_FAKE_ARGS": json.dumps(args)})
        self.addCleanup(fake.stdout.close)
        self.addCleanup(fake.wait, 15)
        self.addCleanup(fake.terminate)
        port = int(fake.stdout.readline())
        (self.t / "config" / "opencode" / "opencode.json").write_text(json.dumps({
            "permission": {"*": "allow"}, "default_agent": "plan", "formatter": True, "lsp": True,  # B10
            "mcp": {"y": {"type": "local", "command": ["touch", str(self.t / "owner-mcp-ran")]}},
            "instructions": [str(self.t / "owner-instr.md")],
            "provider": {"lanetest": {"npm": "@ai-sdk/openai-compatible", "name": "lanetest", "models": {"m": {}},
                                      "options": {"baseURL": f"http://127.0.0.1:{port}/v1",
                                                  "apiKey": "{env:LANETEST_KEY}"}}}}))
        (self.t / "config" / "opencode" / "plugins").mkdir(exist_ok=True)  # an owner plugin, never loaded
        (self.t / "config" / "opencode" / "plugins" / "o.js").write_text(
            f'import fs from "fs"\nfs.writeFileSync({json.dumps(str(self.t / "owner-plugin-ran"))}, "x")\n'
            'export default { id: "o", setup() {} }\n')
        owner = {"agents": {"lane-onboard": {"mode": "primary", "permission": {"*": "allow"}}}}  # m7
        env = {"PATH": os.environ["PATH"], "HOME": str(self.t / "home"), "XDG_CONFIG_HOME": str(self.t / "config"),
               "XDG_DATA_HOME": str(self.t / "data"), "XDG_STATE_HOME": str(self.t / "state"),
               "XDG_CACHE_HOME": str(self.t / "cache"), "OPENCODE_DISABLE_MODELS_FETCH": "1",
               "OPENCODE_DISABLE_AUTOUPDATE": "1", "LANE_ROLE": "coordinator", "LANETEST_KEY": "unused",
               "OPENCODE_CONFIG_CONTENT": json.dumps(owner), "TMPDIR": str(tmpdir or self.t)}
        here = os.getcwd()
        try:
            os.chdir(cwd or self.t)
            with mock.patch.dict(os.environ, env, clear=True):
                p = self.m.run_opencode(self.repo, "lanetest/m", "- RUN-TOOL check", write)
        finally:
            os.chdir(here)
        text, _, error = self.m.opencode_result(p.stdout)
        self.assertEqual((p.returncode, error), (0, ""), p.stderr[-2000:])
        body = log.read_text()
        leaked = {m: path for m, path in self.secrets.items() if m in body}
        self.assertEqual(leaked, {}, f"{tool} {args}: private content reached the model")
        results = [m["content"] for line in body.splitlines()
                   for m in json.loads(json.loads(line)["body"]).get("messages", []) if m.get("role") == "tool"]
        self.assertFalse((self.t / "plugin-ran").exists())  # the project's own plugin never ran
        self.assertFalse((self.t / "mcp-ran").exists())  # nor its MCP server
        for ran in ("formatter-ran", "owner-mcp-ran", "owner-plugin-ran"):  # B10: nothing of the owner's either
            self.assertFalse((self.t / ran).exists(), ran)
        return (str(results[-1]) if results else ""), text

    def test_reads_the_repo(self):
        got, text = self.call("read", {"path": "AGENTS.md"}, write=False)
        self.assertIn("root rules readable", got)
        self.assertEqual(text, "done")  # the last message's text, as claude -p prints it
        for path, want in (("rules.md", "claude-rules"), ("sub/ok.md", "readable-sub"), ("keep.txt", "readable-keep"),
                           ("lib/ok.md", "readable-lib")):
            with self.subTest(path=path):
                self.assertIn(want, self.call("read", {"path": path}, write=False)[0])

    def test_no_private_content_reaches_the_model(self):
        repo = str(self.repo)
        for path in (".env", "data/x.csv", ".git/config", f"{repo}/.env", f"{repo}/.git/config",
                     ".beads/config.yaml", ".beads",  # B1
                     "self/.env", "self/data/x.csv", "keys", "out.md", "self/sub/secret.key",  # B2
                     "secrets/a.md", "private/b.md", "root.pem", "shot1.md", "shots/f.png",  # B3
                     "docs/private/plan.md", "#notes/a.md",  # B4: whitelist .gitignore, a leading #
                     "config/keys.md",  # B5 below runs it from a subfolder too
                     "sub/AGENTS.md", "lib/AGENTS.md", "lib/link/AGENTS.md",  # B6
                     "app/(group)/p.tsx", "app/[id]/q.tsx",  # m5
                     "shotx.md", "a/x/b/c.md", "a/b/y/c.md"):  # M1: [^...] and two **/ in one glob
            with self.subTest(path=path):
                got, _ = self.call("read", {"path": path}, write=False)
                self.assertNotIn("LIVE-SECRET", got)

    def test_folder_reads_attach_no_private_agents_md(self):
        # B6: OpenCode adds "Instructions from: <folder>/AGENTS.md" when it reads in or lists a folder; none of the
        # private ones is in the copy (call() searches every request for their markers)
        for path in (".", "sub", "lib", "lib/link", "sub/ok.md", "lib/ok.md", "docs/a.md"):
            with self.subTest(path=path):
                self.call("read", {"path": path}, write=False)

    def test_a_tmpdir_inside_another_repo_gives_none_of_it(self):
        # B8: the copy is its own empty repo, so an enclosing work tree is never the project
        home = self.dotrepo / "private-in-home.md"
        for path in ("../../private-in-home.md", str(home), "../../.git/config"):
            with self.subTest(path=path):
                got, _ = self.call("read", {"path": path}, write=False, tmpdir=self.dotrepo / "tmp")
                self.assertIn("external_directory", got)
        self.assertIn("root rules readable",
                      self.call("read", {"path": "AGENTS.md"}, write=False, tmpdir=self.dotrepo / "tmp")[0])

    def test_a_tmpdir_inside_the_repo_is_refused(self):
        (self.repo / ".tmp").mkdir()
        with self.assertRaisesRegex(self.m.Fail, "is inside the repo"):
            self.call("read", {"path": ".env"}, write=False, tmpdir=self.repo / ".tmp")

    def test_anchored_globs_hold_from_a_subfolder(self):
        # B5: project-setup started in <repo>/docs
        got, _ = self.call("read", {"path": "config/keys.md"}, write=False, cwd=self.repo / "docs")
        self.assertNotIn("LIVE-SECRET", got)
        self.assertIn("readable-docs", self.call("read", {"path": "docs/a.md"}, write=False, cwd=self.repo / "docs")[0])

    def test_no_shell_grep_glob_or_web_even_with_an_owner_lane_onboard_agent(self):
        # m7: the owner's OPENCODE_CONFIG_CONTENT defines an allow-all lane-onboard; the run's agent has another name
        for tool, args in (("shell", {"command": f"touch {self.t / 'ran'}"}), ("grep", {"pattern": "LIVE", "path": "."}),
                           ("glob", {"pattern": "**/*"}), ("webfetch", {"url": "http://127.0.0.1:9/"})):
            with self.subTest(tool=tool):
                got, _ = self.call(tool, args, write=True)
                self.assertIn("No tool named", got)
        self.assertFalse((self.t / "ran").exists())

    def test_the_adapter_cannot_put_a_git_or_a_dot_name_in_the_repo(self):
        # B7: a .lane-control/.git with core.fsmonitor would run a command at the next git call in there
        for path in (".lane-control/.git/config", ".lane-control/.git/HEAD", ".lane-control/.GIT",
                     ".lane-control/additions/.git/config", ".lane-control/.hidden.md"):
            with self.subTest(path=path):
                got, _ = self.call("write", {"path": path, "content": "[core]\n\tfsmonitor = touch PWNED\n"},
                                   write=True)
                self.assertIn("Permission denied", got)  # the second layer; copy_back refuses it too
                self.assertFalse((self.repo / ".lane-control").exists())
        got, _ = self.call("write", {"path": ".lane-control/notes.md", "content": "x"}, write=True)
        self.assertIn("Permission denied", got)  # not a template: edit is on the adapter's exact paths only
        self.assertFalse((self.repo / ".lane-control").exists())

    def test_no_formatter_runs_after_a_write(self):
        # B10: the owner's config turns formatters on and the copy has prettier and its config: a write runs nothing
        self.call("write", {"path": ".lane-control/project.md", "content": "# facts\n"}, write=True)
        self.assertEqual((self.repo / ".lane-control" / "project.md").read_text(), "# facts\n")
        got, _ = self.call("write", {"path": ".lane-control/prettier.config.mjs", "content": "export default {}\n"},
                           write=True)
        self.assertIn("Permission denied", got)  # edit only on the adapter's exact paths

    def test_the_adapter_writes_only_lane_control_and_it_comes_back(self):
        got, _ = self.call("write", {"path": "AGENTS.md", "content": "changed"}, write=True)
        self.assertIn("Permission denied", got)  # the second layer; copy_back would refuse it too
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "root rules readable\n")
        self.call("write", {"path": ".lane-control/project.md", "content": "facts"}, write=True)
        self.assertEqual((self.repo / ".lane-control" / "project.md").read_text(), "facts")
        got, _ = self.call("write", {"path": f"{self.repo}/.lane-control/x.md", "content": "x"}, write=True)
        self.assertFalse((self.repo / ".lane-control" / "x.md").exists())  # the real repo is out of reach
        got, _ = self.call("write", {"path": ".lane-control/project.md", "content": "x"}, write=False)
        self.assertIn('No tool named \\"write\\"', got)  # the reviewer has no write tool at all
        self.assertEqual((self.repo / ".lane-control" / "project.md").read_text(), "facts")

# --- Task 3.3 ------------------------------------------------------------------------------------------------------

class StartTests(Case):
    def session(self, role):
        found = [x for x in self.e.sessions() if x["role"] == role]
        self.assertEqual(len(found), 1, self.e.sessions())
        return found[0]

    def create(self, title):
        found = [c for c in self.e.creates() if opt(c, "--title") == title]
        self.assertEqual(len(found), 1, self.e.creates())
        return found[0]

    def test_coordinator_starts_in_an_orca_terminal_with_title_markers_and_name(self):
        r = self.e.run(str(self.repo), "--no-design-lead")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        c = self.create("app coordinator")
        self.assertEqual(opt(c, "--worktree"), f"path:{self.repo}")
        launcher = shlex.split(opt(c, "--command"))
        self.assertEqual(launcher[0], "sh")
        self.assertTrue(Path(launcher[1]).is_relative_to(self.e.store(self.repo)), launcher)
        s = self.session("coordinator")
        persona = self.repo / ".lane-control" / "coordinator.md"
        self.assertEqual((s["bin"], s["persona"], s["cwd"]), ("claude", str(persona), str(self.repo)))
        argv = s["argv"]
        self.assertEqual(argv[:4], ["--model", "opus", "--permission-mode", "auto"])
        self.assertEqual(opt(argv, "--name"), "app coordinator")
        self.assertIn(str(persona), argv[-1])  # the first message names the persona
        calls = self.e.calls()
        self.assertTrue(any(c.startswith("orca orchestration run-create --objective app coordinator") for c in calls))
        run_at = next(i for i, c in enumerate(calls) if "run-create" in c)
        self.assertLess(run_at, calls.index("session claude coordinator"))  # its Run before the agent, same terminal
        self.assertFalse([c for c in calls if c.startswith("orca terminal send")])

    def test_run_id_is_recorded_in_the_store(self):
        r = self.e.run(str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        runs = self.e.settings(self.repo)["runs"]
        # n1: a launcher holds the first message (it may carry the owner's open questions): gone once its Run is in
        self.assertEqual(sorted((self.e.store(self.repo) / "launch").iterdir()), [])
        objectives = {x["runId"]: x["objective"] for x in json.loads(self.e.state.read_text())["runs"]}
        self.assertEqual(objectives[runs["coordinator"]], "app coordinator")
        self.assertEqual(list(runs), ["coordinator"])

    def test_opencode_coordinator_starts_with_opencode2_and_its_prompt(self):
        r = self.e.run(str(self.repo), "--coordinator", "opencode/gw/m1", "--no-design-lead")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        s = self.session("coordinator")
        self.assertEqual(s["bin"], "opencode2")
        self.assertEqual(s["argv"][:2], ["--standalone", "--auto"])
        self.assertIn(str(self.repo / ".lane-control" / "coordinator.md"), opt(s["argv"], "--prompt"))
        self.assertNotIn("--name", s["argv"])

    def test_an_opencode_coordinator_gets_the_project_level_plugin_in_its_checkout(self):
        r = self.e.run(str(self.repo), "--coordinator", "opencode/gw/m1",
                       env_extra={"LANE_OPENCODE_PLUGIN": "project"})
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        plugin = Path(".opencode") / "plugins" / "lane-persona.js"
        self.assertTrue((self.repo / plugin).is_file())
        self.assertFalse((self.e.home / ".config" / "opencode" / "plugins").exists())  # user folder untouched
        self.assertIn("/.opencode/plugins/lane-persona.js", (self.repo / ".git" / "info" / "exclude").read_text())
        self.assertTrue((self.repo / ".opencode" / "plugins" / "lane-away-guard.js").is_file())
        self.assertIn("/.opencode/plugins/lane-away-guard.js", (self.repo / ".git" / "info" / "exclude").read_text())

    def test_a_user_level_opencode_coordinator_needs_nothing_in_its_checkout(self):
        r = self.e.run(str(self.repo), "--coordinator", "opencode/gw/m1")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertFalse((self.repo / ".opencode").exists())
        self.assertTrue((self.e.home / ".config" / "opencode" / "plugins" / "lane-persona.js").is_file())

    def test_only_the_coordinator_starts_and_no_worktree_is_made(self):
        self.e.run(str(self.repo))
        self.assertEqual([opt(c, "--title") for c in self.e.creates()], ["app coordinator"])
        self.assertFalse([c for c in self.e.calls() if c.startswith("orca worktree create")])

    def test_no_start_starts_nothing(self):
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.e.creates(), [])
        self.assertEqual(self.e.sessions(), [])

    def test_trust_folder_records_trust_before_the_start(self):
        self.e.trust(self.repo, accepted=False)
        r = self.e.run(str(self.repo))
        self.assertEqual(r.returncode, 1)
        self.assertIn("--trust-folder", r.stderr)
        self.assertEqual(self.e.creates(), [])
        r = self.e.run(str(self.repo), "--trust-folder")
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads((self.e.home / ".claude.json").read_text())
        self.assertTrue(cfg["projects"][str(self.repo)]["hasTrustDialogAccepted"])
        self.assertTrue(self.e.creates())

    def test_first_message_carries_what_the_owner_left_open(self):
        m = load()
        m.LEFTOVER = "## Questions for the owner\n- Which port does the dev server use?"
        with mock.patch.dict(os.environ, self.e.env, clear=True), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            status = m.main([str(self.repo), "--skip-adapt", "--no-design-lead"])
        self.assertEqual(status, 0, err.getvalue())
        self.assertTrue(m.LEFTOVER_DELIVERED)
        self.assertIn("Which port does the dev server use?", self.session("coordinator")["argv"][-1])

    def test_a_running_session_is_not_started_twice(self):
        self.e.set_state(terminals=[{"handle": "term_9", "title": "✳ app coordinator",
                                     "worktreePath": str(self.repo), "agentIdentity": "claude"}])
        r = self.e.run(str(self.repo), "--no-design-lead")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.e.creates(), [])
        self.assertIn("exists", r.stdout)

    def test_where_to_run_is_one_seam(self):
        m = load()
        self.assertEqual(m.Place().argv("terminal", "list"), [m.ORCA, "terminal", "list"])
        # phase 6: a host's Place routes the same calls to its paired Orca server (tests/test_remote_native.py)
        self.assertEqual(m.Place("box").argv("terminal", "list"), [m.ORCA, "terminal", "list", "--environment", "box"])


# --- Task 3.4 ------------------------------------------------------------------------------------------------------

def session_record(e, checkout, role, harness):
    """Where the persona hook (Claude) or plugin (OpenCode) records a marked session's id."""
    core = str(ROOT / "core")
    if core not in sys.path:
        sys.path.insert(0, core)
    import harnesses
    with mock.patch.dict(os.environ, {"XDG_STATE_HOME": e.env["XDG_STATE_HOME"]}):
        return harnesses.session_record(checkout, role, harness)


class NativeSkillTests(Case):
    """UP-7: the native path installs its lane skill the way the Cotal path installs orca-cotal-lanes."""

    def skill(self):
        return self.e.home / ".agents" / "skills" / "native-orca-lanes"

    def test_setup_installs_the_native_lane_skill_for_claude_and_opencode(self):
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        src = ROOT / "orchestrators" / "native-orca"
        self.assertEqual((self.skill() / "SKILL.md").read_text(),
                         (src / "skills" / "native-orca-lanes" / "SKILL.md").read_text())
        self.assertEqual((self.skill() / "brief.md").read_text(), (src / "templates" / "lane-brief.md").read_text())
        for agent_dir in (self.e.home / ".claude" / "skills", Path(self.e.env["XDG_CONFIG_HOME"]) / "opencode" / "skills"):
            link = agent_dir / "native-orca-lanes"
            self.assertTrue(link.is_symlink(), link)
            self.assertEqual(link.resolve(), self.skill().resolve())
        self.assertIn("skills: created", r.stdout)
        self.assertFalse((self.e.home / ".agents" / "skills" / "orca-cotal-lanes").exists())

    def test_setup_installs_the_away_improve_skill_with_its_templates_for_claude_and_opencode(self):
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        skill = self.e.home / ".agents" / "skills" / "away-improve"
        src = ROOT / "orchestrators" / "native-orca" / "skills" / "away-improve" / "SKILL.md"
        self.assertEqual((skill / "SKILL.md").read_text(), src.read_text())
        for name in ("away-innovation.md", "away-scout-brief.md", "away-build-brief.md", "away-entry.md"):
            self.assertEqual((skill / name).read_text(), (ROOT / "core" / "templates" / name).read_text(), name)
        for agent_dir in (self.e.home / ".claude" / "skills", Path(self.e.env["XDG_CONFIG_HOME"]) / "opencode" / "skills"):
            link = agent_dir / "away-improve"
            self.assertTrue(link.is_symlink(), link)
            self.assertEqual(link.resolve(), skill.resolve())
        self.assertIn("away-improve", r.stdout)

    def test_a_file_an_older_install_left_in_the_skill_folder_is_removed(self):  # R1-09
        self.assertEqual(self.e.run(str(self.repo), "--no-start").returncode, 0)
        stale = self.skill().parent / "away-improve" / "AWAY-LOG.md"
        stale.write_text("an old template that says the log lives at the root of the branch\n")
        (self.skill().parent / "away-improve" / "sub").mkdir()
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertFalse(stale.exists())
        self.assertTrue((self.skill().parent / "away-improve" / "away-entry.md").exists())
        self.assertTrue((self.skill().parent / "away-improve" / "sub").is_dir())  # only files go

    def test_refresh_puts_back_a_changed_or_missing_skill_file(self):
        self.assertEqual(self.e.run(str(self.repo), "--no-start").returncode, 0)
        (self.skill() / "brief.md").unlink()
        (self.skill() / "SKILL.md").write_text("old\n")
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue((self.skill() / "brief.md").exists())
        self.assertNotEqual((self.skill() / "SKILL.md").read_text(), "old\n")
        self.assertIn("skills: updated", r.stdout)


class DesignKitInstallTests(Case):
    """The design kit (docs/superpowers/specs/2026-11-21-design-kit-design.md): the gallery skill with the design
    file template, the frontend brief next to the lane brief, design-shots with the lane tools, the design reviewer."""

    def test_setup_installs_the_gallery_skill_with_the_design_template(self):
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        skill = self.e.home / ".agents" / "skills" / "component-gallery"
        self.assertEqual((skill / "SKILL.md").read_text(),
                         (ROOT / "core" / "skills" / "component-gallery" / "SKILL.md").read_text())
        self.assertEqual((skill / "DESIGN.md").read_text(), (ROOT / "core" / "templates" / "DESIGN.md").read_text())
        for agent_dir in (self.e.home / ".claude" / "skills", Path(self.e.env["XDG_CONFIG_HOME"]) / "opencode" / "skills"):
            self.assertEqual((agent_dir / "component-gallery").resolve(), skill.resolve())
        self.assertIn("component-gallery", r.stdout)

    def test_the_frontend_brief_sits_next_to_the_lane_brief(self):
        self.assertEqual(self.e.run(str(self.repo), "--no-start").returncode, 0)
        lanes = self.e.home / ".agents" / "skills" / "native-orca-lanes"
        self.assertEqual((lanes / "frontend-brief.md").read_text(),
                         (ROOT / "core" / "templates" / "frontend-brief.md").read_text())

    def test_design_shots_is_a_lane_tool(self):
        self.assertEqual(self.e.run(str(self.repo), "--no-start").returncode, 0)
        tool = self.e.home / ".agents" / "bin" / "design-shots"
        self.assertEqual(tool.read_bytes(), (CORE / "design-shots").read_bytes())
        self.assertTrue(os.access(tool, os.X_OK))

    def test_the_design_reviewer_is_rendered_pinned_like_the_reviewer_and_kept_out_of_git(self):
        r = self.e.run(str(self.repo), "--reviewer", "claude/sonnet", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        text = (self.repo / ".lane-control" / "design-reviewer.md").read_text()
        self.assertIn("name: design-reviewer\n", text)
        self.assertIn("agent: claude\nmodel: sonnet\n", text)
        self.assertNotIn("{{", text)
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=self.repo), "")


class DesignSkillsTests(Case):
    """Setup installs the three design skills for Claude and OpenCode from their upstream repos at pinned commits
    (design-kit spec, section 9). Local git repos stand in for upstream; everything is under the temp HOME."""

    def upstream(self, name, path, body="---\nname: x\ndescription: y\n---\nv1\n"):
        d = self.e.home / "upstream" / name
        (d / path).mkdir(parents=True)
        (d / path / "SKILL.md").write_text(body)
        (d / path / "scripts").mkdir()
        (d / path / "scripts" / "search.py").write_text("print(1)\n")
        (d / "screenshots").mkdir()
        (d / "screenshots" / "big.png").write_bytes(b"x" * 1000)
        (d / "LICENSE").write_text("the upstream licence\n")
        git("init", "-q", "-b", "main", cwd=d)
        git("add", "-A", cwd=d)
        git("commit", "-q", "-m", "v1", cwd=d)
        return d, git("rev-parse", "HEAD", cwd=d)

    def table(self, *rows):
        f = self.e.home / "design-skills.tsv"
        f.write_text("# name\turl\tcommit\tpath\tlicence\tclaude_plugin\n" +
                     "".join("\t".join(r) + "\n" for r in rows))

    def skills(self):
        return self.e.home / ".agents" / "skills"

    def test_installs_a_sparse_pinned_checkout_linked_for_opencode_and_claude(self):
        d, sha = self.upstream("fd", "plugins/frontend-design/skills/frontend-design")
        self.table(("frontend-design", f"file://{d}", sha, "plugins/frontend-design/skills/frontend-design",
                    "Apache-2.0", "frontend-design@claude-plugins-official"))
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        link = self.skills() / "frontend-design"
        src = self.e.home / ".agents" / "skill-sources" / f"frontend-design@{sha[:12]}"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), (src / "plugins/frontend-design/skills/frontend-design").resolve())
        self.assertEqual((link / "SKILL.md").read_text().splitlines()[-1], "v1")
        self.assertTrue((link / "scripts" / "search.py").is_file())
        self.assertFalse((src / "screenshots").exists())  # only the skill's own folder is checked out
        self.assertEqual((src / "LICENSE").read_text(), "the upstream licence\n")  # and the repo's licence (n1)
        self.assertEqual((self.e.home / ".claude" / "skills" / "frontend-design").resolve(), link.resolve())
        self.assertFalse((Path(self.e.env["XDG_CONFIG_HOME"]) / "opencode" / "skills" / "frontend-design").exists())
        self.assertIn("design-skills: created", r.stdout)
        self.assertIn("frontend-design created", r.stdout)

    def test_a_rerun_changes_nothing_and_a_new_pin_updates_it(self):
        d, sha = self.upstream("mwg", "skills/modern-web-guidance")
        self.table(("modern-web-guidance", f"file://{d}", sha, "skills/modern-web-guidance", "Apache-2.0", "-"))
        self.assertEqual(self.e.run(str(self.repo), "--no-start").returncode, 0)
        r = self.e.run(str(self.repo), "--no-start")
        self.assertIn("design-skills: exists", r.stdout)
        (d / "skills/modern-web-guidance/SKILL.md").write_text("---\nname: x\ndescription: y\n---\nv2\n")
        git("commit", "-q", "-am", "v2", cwd=d)
        sha2 = git("rev-parse", "HEAD", cwd=d)
        self.table(("modern-web-guidance", f"file://{d}", sha2, "skills/modern-web-guidance", "Apache-2.0", "-"))
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("modern-web-guidance updated", r.stdout)
        self.assertEqual((self.skills() / "modern-web-guidance" / "SKILL.md").read_text().splitlines()[-1], "v2")
        sources = sorted(p.name for p in (self.e.home / ".agents" / "skill-sources").iterdir())
        self.assertEqual(sources, [f"modern-web-guidance@{sha2[:12]}"])  # our older checkout is gone

    def test_the_owners_own_install_is_left_alone(self):
        d, sha = self.upstream("mwg", "skills/modern-web-guidance")
        self.table(("modern-web-guidance", f"file://{d}", sha, "skills/modern-web-guidance", "Apache-2.0", "-"))
        own = self.skills() / "modern-web-guidance"
        own.mkdir(parents=True)
        (own / "SKILL.md").write_text("the owner's\n")
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertFalse(own.is_symlink())
        self.assertEqual((own / "SKILL.md").read_text(), "the owner's\n")
        self.assertIn("modern-web-guidance exists (not lane-control's)", r.stdout)
        self.assertFalse((self.e.home / ".agents" / "skill-sources").exists())

    def test_no_claude_link_when_claude_has_the_plugin(self):
        d, sha = self.upstream("ux", ".claude/skills/ui-ux-pro-max")
        self.table(("ui-ux-pro-max", f"file://{d}", sha, ".claude/skills/ui-ux-pro-max", "MIT",
                    "ui-ux-pro-max@ui-ux-pro-max-skill"))
        plugins = self.e.home / ".claude" / "plugins"
        plugins.mkdir(parents=True)
        (plugins / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {"ui-ux-pro-max@ui-ux-pro-max-skill": [{"scope": "user"}]}}))
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue((self.skills() / "ui-ux-pro-max").is_symlink())  # OpenCode's
        self.assertFalse((self.e.home / ".claude" / "skills" / "ui-ux-pro-max").exists())  # Claude has its plugin

    def test_a_failed_fetch_is_named_and_setup_goes_on(self):
        self.table(("frontend-design", f"file://{self.e.home}/nowhere", "0" * 40, "skills/x", "Apache-2.0", "-"))
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("design-skills: missing", r.stdout)
        self.assertIn("frontend-design missing", r.stdout)
        self.assertFalse((self.skills() / "frontend-design").exists())
        self.assertEqual(list((self.e.home / ".agents" / "skill-sources").glob("*")), [])
        self.assertTrue((self.repo / ".lane-control" / "coordinator.md").exists())

    def test_an_existing_claude_skill_of_that_name_is_left_alone(self):
        # review m6
        d, sha = self.upstream("mwg", "skills/modern-web-guidance")
        self.table(("modern-web-guidance", f"file://{d}", sha, "skills/modern-web-guidance", "Apache-2.0", "-"))
        own = self.e.home / ".claude" / "skills" / "modern-web-guidance"
        own.mkdir(parents=True)
        (own / "SKILL.md").write_text("the owner's\n")
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue((self.skills() / "modern-web-guidance").is_symlink())  # OpenCode's is still installed
        self.assertFalse(own.is_symlink())
        self.assertEqual((own / "SKILL.md").read_text(), "the owner's\n")

    def test_a_dangling_link_of_ours_is_fetched_again(self):
        # review m6: the checkout under skill-sources was removed by hand; the link of ours points at nothing
        d, sha = self.upstream("mwg", "skills/modern-web-guidance")
        self.table(("modern-web-guidance", f"file://{d}", sha, "skills/modern-web-guidance", "Apache-2.0", "-"))
        self.assertEqual(self.e.run(str(self.repo), "--no-start").returncode, 0)
        src = self.e.home / ".agents" / "skill-sources" / f"modern-web-guidance@{sha[:12]}"
        shutil.rmtree(src)
        link = self.skills() / "modern-web-guidance"
        self.assertTrue(link.is_symlink() and not link.exists())
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("modern-web-guidance updated", r.stdout)
        self.assertEqual((link / "SKILL.md").read_text().splitlines()[-1], "v1")

    def test_a_bad_row_is_refused_before_any_git_runs(self):
        # review m2: LANE_DESIGN_SKILLS can replace the table, so the code checks each row, not only the shipped file
        d, sha = self.upstream("mwg", "skills/modern-web-guidance")
        good = ("modern-web-guidance", f"file://{d}", sha, "skills/modern-web-guidance", "Apache-2.0", "-")
        bad = {"a/b": ("a/b",) + good[1:], "star": ("*",) + good[1:], "dots": ("..",) + good[1:],
               "branch": ("bad-commit-branch", good[1], "main") + good[3:],
               "short": ("bad-commit-short", good[1], sha[:12]) + good[3:],
               "up": ("bad-path-up", good[1], sha, "skills/../../x") + good[4:],
               "abs": ("bad-path-abs", good[1], sha, "/etc") + good[4:],
               "ext": ("bad-url-ext", "ext::sh -c 'touch pwned'", sha) + good[3:],
               "http": ("bad-url-http", "http://example.com/x.git", sha) + good[3:],
               "dash": ("bad-url-dash", "-uhttps://x", sha) + good[3:]}
        self.table(*bad.values(), good)
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("modern-web-guidance created", r.stdout)
        self.assertEqual(r.stdout.count("refused:"), len(bad), r.stdout)
        sources = sorted(p.name for p in (self.e.home / ".agents" / "skill-sources").iterdir())
        self.assertEqual(sources, [f"modern-web-guidance@{sha[:12]}"])
        self.assertEqual(sorted(p.name for p in self.skills().iterdir() if not p.name.startswith(".")),
                         sorted(["modern-web-guidance", "native-orca-lanes", "component-gallery", "away-improve"]))
        self.assertEqual(list(self.e.home.rglob("pwned")), [])

    def test_the_shipped_table_needs_https(self):
        m = load()
        row = ("frontend-design", "file:///tmp/x", "0" * 40, "skills/frontend-design", "Apache-2.0", "-")
        self.assertIn("https", m.bad_skill_row(row, override=False))
        self.assertIsNone(m.bad_skill_row(row, override=True))  # a test's local stand-in for upstream
        self.assertIsNone(m.bad_skill_row(("x", "https://github.com/a/b.git", "0" * 40, "s/x", "MIT", "-"), False))

    def test_the_real_table_pins_upstream_repos_with_open_licences(self):
        rows = [l.split("\t") for l in (ROOT / "core" / "design-skills.tsv").read_text().splitlines()
                if l and not l.startswith("#")]
        self.assertEqual(sorted(r[0] for r in rows), ["frontend-design", "modern-web-guidance", "ui-ux-pro-max"])
        for name, url, commit, path, licence, plugin in rows:
            self.assertTrue(url.startswith("https://github.com/") and url.endswith(".git"), url)
            self.assertRegex(commit, r"^[0-9a-f]{40}$")
            self.assertTrue(path.endswith(name), path)
            self.assertIn(licence, ("Apache-2.0", "MIT"))
            self.assertTrue(plugin == "-" or plugin.startswith(name + "@"), plugin)


class RetireDesignLeadTests(Case):
    """A project set up with a design lead: re-running setup retires it without deleting anything silently (design-kit
    spec, section 8). The additions move into DESIGN.md only on the owner's yes; a running session is never stopped."""

    ADD = ".lane-control/additions/design-lead.md"
    BODY = "## Subject\nShoppers on phones.\n\n## Seeing it run\nnpm run dev, port 5173.\n"

    def with_old_design_lead(self):
        self.assertEqual(self.e.run(str(self.repo), "--no-start").returncode, 0)
        f = self.repo / self.ADD
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("---\nlane_control_template: 1\n---\n" + self.BODY)
        git("add", self.ADD, cwd=self.repo)
        git("commit", "-q", "-m", "design lead additions", cwd=self.repo)
        st = self.e.settings(self.repo)
        st["design_lead"] = True
        st["models"]["design-lead"] = "claude/opus"
        (self.e.store(self.repo) / "settings.json").write_text(json.dumps(st))
        (self.repo / ".lane-control" / "design-lead.md").write_text("rendered design lead persona\n")

    def in_terminal(self, answer, *args):
        m = load()
        out = Tty()
        with mock.patch.dict(os.environ, self.e.env, clear=True), mock.patch("sys.stdin", Tty()), \
                mock.patch("builtins.input", return_value=answer), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            status = m.main([str(self.repo), "--skip-adapt", "--no-start", *args])
        return status, out.getvalue() + err.getvalue()

    def leftover_session(self):
        wt = self.e.home / "orca" / "workspaces" / "design-lead"
        git("worktree", "add", "-q", "-b", "design-lead", str(wt), cwd=self.repo)
        (wt / "draft.css").write_text("body{}\n")
        s = self.e.stub_state()
        rid = next(r["id"] for r in s["repos"] if Path(r["path"]) == self.repo)
        s["worktrees"].append({"repo": rid, "name": "design-lead", "path": str(wt), "parent": f"{rid}::{self.repo}"})
        s["terminals"].append({"handle": "term_dl", "title": "app design lead", "worktreePath": str(wt),
                               "agentIdentity": "claude"})
        self.e.state.write_text(json.dumps(s))
        return wt

    def test_without_a_terminal_nothing_moves_and_it_says_what_it_would_do(self):
        self.with_old_design_lead()
        head = git("rev-parse", "HEAD", cwd=self.repo)
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("design-lead: retired", r.stdout)
        self.assertIn(f"{self.ADD} would move into DESIGN.md", r.stdout)
        self.assertTrue((self.repo / self.ADD).exists())
        self.assertFalse((self.repo / "DESIGN.md").exists())
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), head)

    def test_the_settings_store_drops_the_design_lead(self):
        self.with_old_design_lead()
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        st = self.e.settings(self.repo)
        self.assertNotIn("design_lead", st)
        self.assertNotIn("design-lead", st["models"])
        self.assertIn("dropped from the settings store", r.stdout)
        self.assertIn("design-lead=claude/opus", r.stdout)  # review m5: the dropped pin stays in the output

    def test_on_yes_the_additions_move_into_design_md_in_one_commit(self):
        self.with_old_design_lead()
        status, out = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        design = (self.repo / "DESIGN.md").read_text()
        self.assertTrue(design.startswith((ROOT / "core" / "templates" / "DESIGN.md").read_text()))
        self.assertIn("## From the design lead's additions", design)
        self.assertIn("Shoppers on phones.", design)
        self.assertNotIn("lane_control_template", design)
        self.assertFalse((self.repo / self.ADD).exists())
        self.assertEqual(git("status", "--porcelain", cwd=self.repo), "")
        files = git("show", "--name-status", "--format=%s", "HEAD", cwd=self.repo).splitlines()
        self.assertIn("design lead", files[0].lower())
        self.assertEqual(sorted(files[2:]), sorted([f"D\t{self.ADD}", "A\tDESIGN.md"]))
        self.assertNotIn("not pushed", out)  # no origin: lanes start from this branch, which has the commit

    def test_on_yes_with_an_origin_it_says_lanes_lack_the_commit_until_pushed(self):
        # review m5: Orca starts lanes from origin's default branch, so a local commit is not there yet
        self.with_old_design_lead()
        bare = self.e.home / "origin.git"
        git("init", "-q", "--bare", "-b", "main", str(bare), cwd=self.e.home)
        git("remote", "add", "origin", str(bare), cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        git("remote", "set-head", "origin", "main", cwd=self.repo)
        status, out = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        self.assertIn("DESIGN.md committed, not pushed: lanes start from origin/main", out)
        self.assertNotEqual(git("rev-parse", "HEAD", cwd=self.repo), git("rev-parse", "origin/main", cwd=self.repo))

    def test_an_existing_design_md_gets_the_additions_at_its_end(self):
        self.with_old_design_lead()
        (self.repo / "DESIGN.md").write_text("# Design\nOur own.\n")
        git("add", "DESIGN.md", cwd=self.repo)
        git("commit", "-q", "-m", "design", cwd=self.repo)
        status, out = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        design = (self.repo / "DESIGN.md").read_text()
        self.assertTrue(design.startswith("# Design\nOur own.\n"))
        self.assertIn("Shoppers on phones.", design)

    def test_on_no_nothing_moves(self):
        self.with_old_design_lead()
        head = git("rev-parse", "HEAD", cwd=self.repo)
        status, out = self.in_terminal("n")
        self.assertEqual(status, 0, out)
        self.assertTrue((self.repo / self.ADD).exists())
        self.assertFalse((self.repo / "DESIGN.md").exists())
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), head)
        self.assertEqual(git("status", "--porcelain", cwd=self.repo), "")

    def test_a_running_design_lead_is_named_never_stopped_and_keeps_its_persona(self):
        self.with_old_design_lead()
        wt = self.leftover_session()
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("app design lead", r.stdout)
        self.assertIn(str(wt), r.stdout)
        self.assertIn("1 uncommitted", r.stdout)
        self.assertIn("handover-design-lead", r.stdout)
        self.assertTrue(wt.is_dir())
        self.assertTrue((wt / "draft.css").exists())
        self.assertIn("app design lead", [x["title"] for x in self.e.stub_state()["terminals"]])
        self.assertFalse([c for c in self.e.calls() if c.startswith(("orca terminal close", "orca worktree rm"))])
        self.assertTrue((self.repo / ".lane-control" / "design-lead.md").exists())  # its compaction hook reads it

    def test_with_no_design_lead_running_its_rendered_persona_goes(self):
        self.with_old_design_lead()
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertFalse((self.repo / ".lane-control" / "design-lead.md").exists())
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=self.repo), "")

    def test_refresh_retires_it_too(self):
        self.with_old_design_lead()
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("design-lead: retired", r.stdout)
        self.assertNotIn("design_lead", self.e.settings(self.repo))

    def test_design_lead_flag_is_refused_and_no_design_lead_is_accepted(self):
        r = self.e.run(str(self.repo), "--design-lead", "claude/opus", "--no-start")
        self.assertEqual(r.returncode, 2)
        self.assertIn("design lead is retired", r.stderr)
        self.assertIn("2026-11-21-design-kit-design.md", r.stderr)
        r = self.e.run(str(self.repo), "--no-design-lead", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_a_new_project_gets_no_design_lead(self):
        r = self.e.run(str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertFalse((self.repo / ".lane-control" / "design-lead.md").exists())
        self.assertEqual([x["role"] for x in self.e.sessions()], ["coordinator"])
        self.assertFalse([w for w in self.e.stub_state()["worktrees"] if w["name"] == "design-lead"])
        self.assertNotIn("design_lead", self.e.settings(self.repo))
        self.assertNotIn("design lead", r.stdout)

    def test_pause_names_a_leftover_design_lead_and_leaves_it_alone(self):
        self.with_old_design_lead()
        self.e.run(str(self.repo), "--no-start")
        self.leftover_session()
        r = self.e.pause("rest", str(self.repo))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("design-lead", r.stderr)
        self.assertIn("retired design lead", r.stderr)
        self.assertIn("app design lead", [x["title"] for x in self.e.stub_state()["terminals"]])


class RefreshTests(Case):
    def test_refresh_rerenders_with_the_stored_pins_and_hooks(self):
        r = self.e.run(str(self.repo), "--coordinator", "opencode/gw/m1", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        add = self.repo / ".lane-control" / "additions"
        add.mkdir(parents=True)
        (add / "coordinator.md").write_text("## First turn\nRead docs/refreshed.md.\n")
        (self.repo / ".claude" / "settings.local.json").unlink(missing_ok=True)
        self.e.log.unlink()
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        text = (self.repo / ".lane-control" / "coordinator.md").read_text()
        self.assertIn("agent: opencode\nmodel: gw/m1\n", text)  # the stored pin, kept
        self.assertIn("Read docs/refreshed.md.", text)
        plugin = Path(self.e.env["XDG_CONFIG_HOME"]) / "opencode" / "plugins" / "lane-persona.js"
        self.assertTrue(plugin.is_file())
        self.assertEqual(self.e.creates(), [])  # a refresh starts nothing
        self.assertFalse([c for c in self.e.calls() if c.startswith("cotal")])

    def test_refresh_installs_the_lane_tools_design_shots_among_them(self):
        # review round 2, r2-m1: a box set up before the kit gets design-shots from a --refresh too
        self.assertEqual(self.e.run(str(self.repo), "--no-start").returncode, 0)
        tool = self.e.home / ".agents" / "bin" / "design-shots"
        tool.unlink()
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue(tool.exists(), r.stdout)

    def test_refresh_says_it_installs_the_lane_tools(self):
        doc = " ".join(load().native_refresh.__doc__.split())
        self.assertIn("install the skills and the lane tools (design-shots among them", doc)
        self.assertNotIn("not refreshed", doc)

    def test_refresh_names_running_sessions_to_restart(self):
        self.assertEqual(self.e.run(str(self.repo)).returncode, 0)
        add = self.repo / ".lane-control" / "additions"
        add.mkdir(parents=True)
        (add / "coordinator.md").write_text("## First turn\nSomething new.\n")
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        line = next(l for l in r.stdout.splitlines() if l.startswith("restart:"))
        self.assertIn("app coordinator", line)
        self.assertNotIn("design lead", line)

    def test_refresh_of_a_project_never_set_up_is_refused(self):
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not set up", r.stderr)


class PauseTests(Case):
    def setUp(self):
        super().setUp()
        r = self.e.run(str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def titles(self):
        return sorted(t.get("title") for t in json.loads(self.e.state.read_text())["terminals"])

    def test_rest_marks_the_project_paused_in_the_store(self):
        r = self.e.pause("rest", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.e.settings(self.repo)["paused"])
        self.assertIn("app coordinator", self.titles())  # rest stops nothing
        self.assertFalse([c for c in self.e.calls() if c.startswith("cotal")])

    def test_wake_unmarks_it(self):
        self.e.pause("rest", str(self.repo))
        r = self.e.pause("wake", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.e.settings(self.repo)["paused"])

    def test_wake_without_a_coordinator_keeps_it_paused_for_start(self):
        self.e.pause("stop", str(self.repo), "--no-handover")
        r = self.e.pause("wake", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.e.settings(self.repo)["paused"])
        self.assertIn("project-pause start", r.stdout)

    def test_stop_asks_for_a_handover_through_the_run_then_closes(self):
        (self.e.home / "handover-latest").write_text("old handover")
        self.e.set_state(handover_on_send={"handover-latest": "new"})
        runs = self.e.settings(self.repo)["runs"]
        start = len(self.e.calls())  # setup closed Orca's startup shell itself; only stop's calls count here
        r = self.e.pause("stop", str(self.repo), "--wait", "5")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        sent = json.loads(self.e.state.read_text())["sent"]
        self.assertEqual(sorted(x["to"] for x in sent), [f"run:{runs['coordinator']}"])
        self.assertIn("handover", sent[0]["subject"].lower())
        bodies = {x["to"]: x["body"] for x in sent}
        self.assertIn("handover-latest", bodies[f"run:{runs['coordinator']}"])
        self.assertNotIn("app coordinator", self.titles())
        self.assertTrue(self.e.settings(self.repo)["paused"])
        calls = self.e.calls()[start:]
        last_send = max(i for i, c in enumerate(calls) if c.startswith("orca orchestration send"))
        first_close = min(i for i, c in enumerate(calls) if c.startswith("orca terminal close"))
        self.assertLess(last_send, first_close)
        self.assertFalse([c for c in calls if c.startswith("orca terminal send")])

    def test_stop_keeps_the_terminal_when_no_handover_comes(self):
        (self.e.home / "handover-latest").write_text("old handover")
        start = len(self.e.calls())
        r = self.e.pause("stop", str(self.repo), "--wait", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--no-handover", r.stderr)
        self.assertIn("app coordinator", self.titles())
        self.assertFalse([c for c in self.e.calls()[start:] if c.startswith("orca terminal close")])

    def test_stop_no_handover_closes_without_asking(self):
        r = self.e.pause("stop", str(self.repo), "--no-handover")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("sent", json.loads(self.e.state.read_text()))
        self.assertNotIn("app coordinator", self.titles())

    def test_start_resumes_the_recorded_session(self):
        rec = session_record(self.e, self.repo, "coordinator", "claude")
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text("abc-123\n")
        transcripts = self.e.home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(self.repo.resolve()))
        transcripts.mkdir(parents=True)
        (transcripts / "abc-123.jsonl").write_text("{}\n")
        self.e.pause("stop", str(self.repo), "--no-handover")
        r = self.e.pause("start", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        coord = [x for x in self.e.sessions() if x["role"] == "coordinator"]
        self.assertEqual(len(coord), 2)
        self.assertEqual(opt(coord[-1]["argv"], "--resume"), "abc-123")
        self.assertIn("You are back after a pause", coord[-1]["argv"][-1])
        self.assertFalse([x for x in self.e.sessions() if x["role"] == "design-lead"])
        self.assertFalse(self.e.settings(self.repo)["paused"])
        self.assertEqual([c for c in self.e.calls() if c.startswith("orca worktree create")], [])

    def test_start_resumes_an_opencode_session(self):
        r = self.e.run(str(self.repo), "--coordinator", "opencode/gw/m1", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = session_record(self.e, self.repo, "coordinator", "opencode")
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text("ses_42\n")
        import sqlite3
        db = self.e.home / ".local" / "share" / "opencode" / "opencode.db"
        db.parent.mkdir(parents=True)
        con = sqlite3.connect(db)
        con.executescript("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, time_updated INTEGER);"
                          "CREATE TABLE session_message (session_id TEXT, time_updated INTEGER);"
                          "INSERT INTO session_v2 VALUES ('ses_42', NULL, 1);")
        con.commit()
        con.close()
        self.e.pause("stop", str(self.repo), "--no-handover")
        r = self.e.pause("start", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        coord = [x for x in self.e.sessions() if x["role"] == "coordinator"][-1]
        self.assertEqual((coord["bin"], opt(coord["argv"], "--session")), ("opencode2", "ses_42"))

    def test_start_when_running_starts_nothing(self):
        r = self.e.pause("start", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.e.sessions()), 1)

    def test_status(self):
        r = self.e.pause("status", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("running", r.stdout)
        self.e.pause("stop", str(self.repo), "--no-handover")
        r = self.e.pause("status", str(self.repo))
        self.assertIn("paused", r.stdout)


# --- Task 3.5: Review Focus 3 ----------------------------------------------------------------------------------------

class RerunTests(Case):
    def setup_in_terminal(self):
        m = load()
        with mock.patch.dict(os.environ, self.e.env, clear=True), mock.patch("sys.stdin", Tty()), \
                mock.patch("builtins.input", return_value="y"), contextlib.redirect_stdout(Tty()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            status = m.main([str(self.repo)])
        self.assertEqual(status, 0, err.getvalue() + out.getvalue())
        return out.getvalue()

    def test_a_rerun_adds_nothing_twice(self):
        self.e.script_agents(sonnet=[WRITE_NATIVE], opus=[PASS])
        self.setup_in_terminal()
        head = git("rev-parse", "HEAD", cwd=self.repo)
        out = self.setup_in_terminal()  # onboarded and running: every step finds itself done
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), head)
        # tests/stubs/fake_agent.py stands in for claude both headless (-p) and as a started session
        headless = [r for r in self.e.agent_runs() if "-p" in r["argv"]]
        started = [r for r in self.e.agent_runs() if "-p" not in r["argv"]]
        self.assertEqual(len(headless), 2)  # the first run's adapter and reviewer only
        agents = (self.repo / "AGENTS.md").read_text()
        generated = (self.repo / GENERATED).read_text()
        self.assertEqual(agents.count("<!-- lane-control:pointer -->"), 1)
        self.assertNotIn("<!-- lane-control:begin -->", agents)
        self.assertNotIn("<!-- orchestrator: native-orca -->", agents)
        self.assertEqual(generated.count("<!-- lane-control:begin -->"), 1)
        self.assertEqual(generated.count("<!-- orchestrator: native-orca -->"), 1)
        cfg = json.loads((self.repo / ".claude" / "settings.local.json").read_text())
        self.assertEqual(len(cfg["hooks"]["SessionStart"]), 1)
        titles = [opt(c, "--title") for c in self.e.creates() if opt(c, "--title")]
        self.assertEqual(sorted(titles), ["app coordinator"])
        self.assertEqual(len(started), 1)
        self.assertIn("session coordinator: exists app coordinator", out)
        self.assertEqual((self.repo / ".git" / "info" / "exclude").read_text().count("lane-control: begin"), 1)
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=self.repo), "")


    def test_a_rerun_moves_sections_left_in_agents_md_to_their_own_file_once(self):
        self.e.script_agents(sonnet=[WRITE_NATIVE], opus=[PASS])
        self.setup_in_terminal()
        hand = "# Rules\n\nWritten by hand.\n"
        generated = (self.repo / GENERATED).read_text()
        blocks = generated[generated.index("<!-- lane-control:begin -->"):]  # as an older project-setup left them
        (self.repo / "AGENTS.md").write_text(hand + "\n" + blocks)
        git("rm", "-q", GENERATED, cwd=self.repo)
        git("commit", "-qam", "an older layout", cwd=self.repo)
        head = git("rev-parse", "HEAD", cwd=self.repo)
        self.setup_in_terminal()
        self.assertEqual(git("rev-list", "--count", f"{head}..HEAD", cwd=self.repo), "1")
        self.assertEqual(git("log", "-1", "--format=%s", cwd=self.repo),
                         "Move lane-control's generated sections from AGENTS.md to AGENTS.lane-control.md")
        self.assertEqual(sorted(git("show", "--name-only", "--format=", "HEAD", cwd=self.repo).splitlines()),
                         ["AGENTS.lane-control.md", "AGENTS.md"])
        agents = (self.repo / "AGENTS.md").read_text()
        self.assertTrue(agents.startswith(hand))
        self.assertEqual(agents.count("<!-- lane-control:pointer -->"), 1)
        self.assertNotIn("<!-- lane-control:begin -->", agents)
        self.assertNotIn("<!-- orchestrator: native-orca -->", agents)
        self.assertEqual((self.repo / GENERATED).read_text(), generated)
        moved = git("rev-parse", "HEAD", cwd=self.repo)
        self.setup_in_terminal()
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), moved)

# --- final review fixes ----------------------------------------------------------------------------------------------

class ReviewFixTests(Case):
    def titles(self):
        return sorted(t.get("title") for t in json.loads(self.e.state.read_text())["terminals"])

    def test_stop_waits_for_every_roles_own_handover(self):
        # Important 1: a handover written under another name must not close the coordinator before its own.
        self.assertEqual(self.e.run(str(self.repo)).returncode, 0)
        self.e.set_state(handover_on_send={"handover-design-lead": "someone else's handover"})
        start = len(self.e.calls())
        r = self.e.pause("stop", str(self.repo), "--wait", "1")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("app coordinator", r.stderr)
        self.assertIn("app coordinator", self.titles())
        self.assertFalse([c for c in self.e.calls()[start:] if c.startswith("orca terminal close")])

    def test_a_start_whose_run_is_pending_drops_the_old_run_id(self):
        # Important 2: never keep a dead Run's id and then send the handover request to it.
        self.assertEqual(self.e.run(str(self.repo)).returncode, 0)
        self.e.pause("stop", str(self.repo), "--no-handover")
        self.e.set_state(run_create_fails=True)
        r = self.e.pause("start", str(self.repo), env_extra={"LANE_RUN_WAIT": "0"})
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.e.settings(self.repo).get("runs", {}), {})
        r = self.e.pause("stop", str(self.repo), "--wait", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--no-handover", r.stderr)
        self.assertNotIn("sent", json.loads(self.e.state.read_text()))

    def test_every_run_is_found_before_any_session_is_asked(self):
        self.assertEqual(self.e.run(str(self.repo)).returncode, 0)
        st_file = self.e.store(self.repo) / "settings.json"
        st = json.loads(st_file.read_text())
        del st["runs"]["coordinator"]
        st_file.write_text(json.dumps(st))
        state = json.loads(self.e.state.read_text())  # and no Run of its name to find either
        state["runs"] = [x for x in state["runs"] if x["objective"] != "app coordinator"]
        self.e.state.write_text(json.dumps(state))
        r = self.e.pause("stop", str(self.repo), "--wait", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("app coordinator", r.stderr)
        self.assertNotIn("sent", json.loads(self.e.state.read_text()))  # nobody was told to stop

    def test_a_session_whose_terminal_was_retitled_is_still_found(self):
        # Important 3: the shell (fish) or OpenCode retitles the terminal; the stored handle still finds it.
        self.e.set_state(retitle="fish /home")
        r = self.e.run(str(self.repo), "--coordinator", "opencode/gw/m1", "--no-design-lead")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.titles(), ["fish /home"])
        r = self.e.run(str(self.repo), "--no-design-lead")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(len([c for c in self.e.creates() if opt(c, "--title")]), 1)  # no second coordinator
        r = self.e.pause("stop", str(self.repo), "--no-handover")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.titles(), [])

    def test_role_files_written_where_the_base_prompt_says_land_in_additions(self):
        # Important 4: the adapter follows the base prompt (.lane-control/<name>); they become the additions.
        wrote = {"write": {".lane-control/coordinator.md": "## First turn\nRead docs/plan.md.\n",
                           ".lane-control/project.md": "facts\n"}}
        self.e.script_agents(sonnet=[wrote], opus=[PASS])
        m = load()
        with mock.patch.dict(os.environ, self.e.env, clear=True), mock.patch("sys.stdin", Tty()), \
                mock.patch("builtins.input", return_value="y"), contextlib.redirect_stdout(Tty()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            status = m.main([str(self.repo), "--no-start"])
        self.assertEqual(status, 0, err.getvalue() + out.getvalue())
        committed = git("show", "--name-only", "--format=", "HEAD", cwd=self.repo).splitlines()
        self.assertEqual(sorted(committed), sorted(["AGENTS.md", GENERATED, ADDITION, ".lane-control/project.md"]))
        self.assertIn("Read docs/plan.md.", (self.repo / ".lane-control" / "coordinator.md").read_text())
        review = [r for r in self.e.agent_runs() if "-p" in r["argv"]][1]["prompt"]
        self.assertIn(".lane-control/additions/", review)


# --- coordinator's review of PR #39 ----------------------------------------------------------------------------------



def store_bytes(e, repo):
    d = e.store(repo)
    return {p.name: p.read_bytes() for p in sorted(d.iterdir()) if p.is_file()} if d.is_dir() else {}


class PinCheckTests(Case):
    def test_a_refused_run_leaves_the_store_byte_identical(self):
        # M1: the refusal comes before anything is written.
        self.assertEqual(self.e.run(str(self.repo), "--no-start").returncode, 0)
        before = store_bytes(self.e, self.repo)
        for args in (["--coordinator", "opencode/gw/m1", "--permission-mode", "plan"],
                     ["--coordinator", "opencode/gw/m1", "--permission-mode", "dontAsk"]):
            r = self.e.run(str(self.repo), *args, "--no-start")
            self.assertEqual(r.returncode, 2, (args, r.stderr))
            self.assertEqual(store_bytes(self.e, self.repo), before, args)

    def edit_store(self, **kw):
        f = self.e.store(self.repo) / "settings.json"
        st = json.loads(f.read_text())
        for k, v in kw.items():
            if isinstance(v, dict):
                st.setdefault(k, {}).update(v)
            else:
                st[k] = v
        f.write_text(json.dumps(st))

    def test_stored_pins_are_checked_again_on_pause_and_refresh(self):
        # M1 and m4: a hand-edited store never starts a session the setup would refuse; a clean Fail, no traceback.
        self.assertEqual(self.e.run(str(self.repo), "--no-start").returncode, 0)
        cases = ({"models": {"coordinator": "opencode/gw/m1"}, "permission_mode": "plan"},
                 {"models": {"coordinator": "nosuch/x"}})
        for case in cases:
            self.edit_store(**case)
            for run in (lambda: self.e.pause("start", str(self.repo)),
                        lambda: self.e.run(str(self.repo), "--refresh", adapt=True)):
                r = run()
                self.assertEqual(r.returncode, 1, (case, r.stderr + r.stdout))
                self.assertNotIn("Traceback", r.stderr, case)
                self.assertIn("coordinator", r.stderr, case)
            self.assertEqual(self.e.creates(), [], case)
            self.edit_store(models={"coordinator": "claude/opus"}, permission_mode="auto")


class SeamTests(Case):
    def test_no_orca_argv_is_built_outside_place(self):
        # M2: Place is the one seam; phase 6 changes it and nothing else.
        for script in (SETUP, PAUSE):
            text = script.read_text()
            m = re.search(r"\nclass Place:.*?(?=\n\n\n)", text, re.S)
            rest = text.replace(m.group(0), "") if m else text
            bad = [l.strip() for l in rest.splitlines() if re.search(r"(?<![\w\"])ORCA\b", l)
                   and not re.search(r"^\s*(global\b|#)|\bORCA\s*=(?!=)|COTAL_ORCA_BIN", l)]
            self.assertEqual(bad, [], script.name)

    def test_the_launcher_takes_its_orca_cli_from_the_place(self):
        m = load()

        class Box(m.Place):
            def argv(self, *args):
                return ["/box/bin/orca-ide", "--environment", "box", *args]
        with mock.patch.dict(os.environ, self.e.env, clear=True):
            f = m.launcher(self.repo, "coordinator", ("claude", "opus"), "auto", self.repo, "app coordinator",
                           "hello", place=Box())
        self.assertIn("\n/box/bin/orca-ide --environment box orchestration run-create --objective 'app coordinator'",
                      f.read_text())


class AnswersTests(Case):
    def test_answers_on_native_go_to_additions_never_to_the_rendered_persona(self):
        # M3: rendered personas exist; the answers run edits the role file where the base prompt says.
        self.assertEqual(self.e.run(str(self.repo), "--no-start").returncode, 0)
        rendered = (self.repo / ".lane-control" / "coordinator.md").read_text()
        adapt = {"write": {ADDITION: "## First turn\nRead docs/plan.md.\n", ".lane-control/project.md": "facts\n",
                           ".lane-control/SUGGESTIONS.md": "## AGENTS.md edits you would make\n- none\n\n"
                                                           "## Questions for the owner\n- Which port does it use?\n"}}
        answers = {"write": {".lane-control/coordinator.md": "## First turn\nRead docs/plan.md.\nUse port 8080.\n"}}
        self.e.script_agents(sonnet=[adapt, answers], opus=[PASS])
        replies = iter(["8080"])
        m = load()
        with mock.patch.dict(os.environ, self.e.env, clear=True), mock.patch("sys.stdin", Tty()), \
                mock.patch("builtins.input", side_effect=lambda *_: next(replies, "y")), \
                contextlib.redirect_stdout(Tty()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            status = m.main([str(self.repo), "--no-start"])
        self.assertEqual(status, 0, err.getvalue() + out.getvalue())
        runs = [r for r in self.e.agent_runs() if "-p" in r["argv"]]
        self.assertEqual(len(runs), 3)  # adapter, reviewer, answers
        self.assertIn(".lane-control/additions/", runs[2]["prompt"])
        for run in (runs[0], runs[2]):  # R2-5: a running session's persona file stays readable throughout
            self.assertIn("coordinator.md", run["lane_files"])
        committed = git("show", "--name-only", "--format=", "HEAD", cwd=self.repo).splitlines()
        self.assertIn(ADDITION, committed)
        self.assertNotIn(".lane-control/coordinator.md", committed)
        added = (self.repo / ADDITION).read_text()
        self.assertIn("Use port 8080.", added)
        self.assertNotIn("## Rules", added)
        now = (self.repo / ".lane-control" / "coordinator.md").read_text()
        self.assertTrue(now.startswith(rendered.split("# Additions for this project")[0][:200]))
        self.assertIn("Use port 8080.", now)


class HostTests(Case):
    """M4: --host with --orchestrator orca-cotal is the Orca + Cotal host path as on main (phase 6: without a flag it
    is the native host path, tests/test_remote_native.py)."""

    def ssh(self):
        ssh = self.e.bin / "ssh"
        ssh.write_text('#!/bin/sh\necho "ssh $*" >> "$STUB_LOG"\ncat > /dev/null\n')
        ssh.chmod(0o755)

    def test_the_orca_md_commands_copy_and_reexec_as_before(self):
        self.ssh()
        shapes = (["https://example.com/acme/shop.git", "--host", "user@host", "--name", "Shop", "--coordinator",
                   "claude/opus", "--permission-mode", "bypassPermissions",
                   "--reviewer", "claude/opus", "--trust-folder", "--orchestrator", "orca-cotal"],
                  ["~/code/shop", "user@host", "--no-design-lead", "--no-start", "--orchestrator", "orca-cotal"])
        for args in shapes:
            self.e.log.unlink(missing_ok=True)
            r = self.e.run(*args, adapt=True)
            self.assertEqual(r.returncode, 0, (args, r.stderr))
            calls = [c for c in self.e.calls() if c.startswith("ssh ")]
            self.assertEqual(len(calls), 2, calls)
            self.assertIn(".local/share/lane-control/scripts/project-setup", calls[1])
            self.assertIn("--via-host", calls[1])
            self.assertNotIn("--host", calls[1].replace("--via-host", ""))
            self.assertNotIn(" user@host", calls[1].split(" ", 2)[2])  # the host is ssh's, not the host run's
            self.assertIn("--orchestrator orca-cotal", calls[1])

    def test_a_cotal_only_permission_mode_is_still_refused_locally(self):
        self.ssh()
        r = self.e.run("~/code/shop", "--host", "user@host", "--permission-mode", "plan", "--orchestrator", "orca-cotal")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.e.calls(), [])

    def test_on_the_host_a_cotal_project_runs_the_cotal_path(self):
        (self.repo / ".cotal" / "agents").mkdir(parents=True)
        r = self.e.run(str(self.repo), "--via-host", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue([c for c in self.e.calls() if c.startswith("cotal space")])

    def test_on_the_host_a_native_project_runs_the_native_path(self):
        self.assertEqual(self.e.run(str(self.repo), "--no-start").returncode, 0)  # set up on native there already
        self.e.log.unlink()
        r = self.e.run(str(self.repo), "--via-host", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertFalse([c for c in self.e.calls() if c.startswith("cotal")])
        self.assertEqual(self.e.settings(self.repo)["orchestrator"], "native-orca")


class NoGitTests(Case):
    def test_no_git_on_path_is_a_message_not_a_traceback(self):
        # m9: the orchestrator lookup runs before anything else and must not crash.
        import shutil
        keep = [d for d in self.e.env["PATH"].split(":") if not (Path(d) / "git").exists()]
        self.assertTrue(shutil.which("git"))
        r = self.e.run(str(self.repo), "--no-start", env_extra={"PATH": ":".join(keep)})
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("project-setup:", r.stderr)


class MinorTests(Case):
    def titles(self):
        return sorted(t.get("title") for t in json.loads(self.e.state.read_text())["terminals"])

    def started(self, role="coordinator"):
        return [x for x in self.e.sessions() if x["role"] == role]

    def test_a_run_create_failure_is_recorded_and_shown(self):
        # m1: run-create's error is kept, not sent to /dev/null.
        self.e.set_state(run_create_fails=True)
        r = self.e.run(str(self.repo), "--no-design-lead", env_extra={"LANE_RUN_WAIT": "0"})
        self.assertEqual(r.returncode, 0, r.stderr)
        line = next(l for l in r.stdout.splitlines() if l.startswith("run coordinator:"))
        self.assertIn("pending", line)
        self.assertIn("stub: run-create failed", line)

    def test_a_pending_run_hint_is_for_peers_on_this_host_only(self):
        # lc-dac: a Run found by its objective in run-list is reachable by run: only from the same Orca server.
        self.e.set_state(run_create_fails=True)
        r = self.e.run(str(self.repo), "--no-design-lead", env_extra={"LANE_RUN_WAIT": "0"})
        line = next(l for l in r.stdout.splitlines() if l.startswith("run coordinator:"))
        self.assertIn("peers on this host find it in `orca-ide orchestration run-list`", line)

    def test_stop_finds_a_sessions_own_later_run_by_its_name(self):
        # m1: no Run recorded at the start, but the session made exactly one itself: stop asks it there.
        self.e.set_state(run_create_fails=True)
        self.e.run(str(self.repo), "--no-design-lead", env_extra={"LANE_RUN_WAIT": "0"})
        state = json.loads(self.e.state.read_text())
        state.update(run_create_fails=False, runs=[{"runId": "run_00000000abcd", "objective": "app coordinator"}],
                     handover_on_send={"handover-latest": "new"})
        self.e.state.write_text(json.dumps(state))
        r = self.e.pause("stop", str(self.repo), "--wait", "2")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual([x["to"] for x in json.loads(self.e.state.read_text())["sent"]], ["run:run_00000000abcd"])

    def test_stop_with_no_run_to_find_says_the_owner_must_ask(self):
        self.e.set_state(run_create_fails=True)
        self.e.run(str(self.repo), "--no-design-lead", env_extra={"LANE_RUN_WAIT": "0"})
        r = self.e.pause("stop", str(self.repo), "--wait", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Ask app coordinator yourself", r.stderr)
        self.assertIn("--no-handover", r.stderr)
        self.assertNotIn("paused", self.e.settings(self.repo))  # nothing changed

    def test_stop_outside_an_orca_terminal_sends_nothing_and_says_how(self):
        # m2: Orca sends messages only from one of its own terminals.
        self.e.run(str(self.repo), "--no-design-lead")
        r = self.e.pause("stop", str(self.repo), "--wait", "1", env_extra={"ORCA_TERMINAL_HANDLE": ""})
        self.assertEqual(r.returncode, 1)
        self.assertIn("Orca terminal", r.stderr)
        self.assertNotIn("sent", json.loads(self.e.state.read_text()))
        self.assertNotIn("paused", self.e.settings(self.repo))
        self.assertIn("app coordinator", self.titles())

    def test_a_session_whose_agent_exited_is_not_running(self):
        # m3: the tab stays with a shell after the agent exits; that is not a running session.
        self.e.set_state(agent_exits=True)
        self.e.run(str(self.repo), "--no-design-lead")
        self.e.set_state(agent_exits=False)
        r = self.e.run(str(self.repo), "--no-design-lead")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.started()), 2)
        self.assertIn("running", self.e.pause("status", str(self.repo)).stdout)

    def add_lane(self):
        state = json.loads(self.e.state.read_text())
        path = str(self.e.home / "orca" / "workspaces" / "fix-login")
        state["worktrees"].append({"repo": "repo-1", "name": "fix-login", "path": path, "parent": f"repo-1::{self.repo}"})
        state["terminals"].append({"handle": "term_lane", "title": "Fix login", "worktreePath": path,
                                   "agentIdentity": "claude"})
        self.e.state.write_text(json.dumps(state))

    def test_rest_and_stop_refuse_while_a_lane_runs(self):
        # m6: as on the Cotal path, a running lane is named and nothing is paused or closed.
        self.e.run(str(self.repo))
        self.add_lane()
        for cmd in (["rest"], ["stop", "--no-handover"]):
            r = self.e.pause(cmd[0], str(self.repo), *cmd[1:])
            self.assertEqual(r.returncode, 1, cmd)
            self.assertIn("fix-login", r.stderr)
            self.assertNotIn("paused", self.e.settings(self.repo))
        self.assertIn("app coordinator", self.titles())

    def test_a_recorded_session_whose_transcript_is_gone_starts_fresh(self):
        # m7: --resume of a session Claude no longer has would fail at once.
        rec = session_record(self.e, self.repo, "coordinator", "claude")
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text("gone-1\n")
        self.e.run(str(self.repo), "--no-design-lead", "--no-start")
        r = self.e.pause("start", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("--resume", self.started()[-1]["argv"])

    def test_run_id_reads_orcas_own_shape(self):
        # m10: run-create --json prints runId at the top of its result.
        m = load()
        self.assertEqual(m.run_id({"ok": True, "result": {"runId": "run_0123456789ab", "objective": "x", "next": []}}),
                         "run_0123456789ab")
        self.assertEqual(m.run_id({"result": {"run": {"id": "run_00000000000f"}}}), "run_00000000000f")
        self.assertIsNone(m.run_id({"result": {"id": "msg_1"}}))


# --- coordinator's review round 2 ------------------------------------------------------------------------------------

class Round2Tests(Case):
    def titles(self):
        return sorted(t.get("title") for t in json.loads(self.e.state.read_text())["terminals"])

    def started(self, role="coordinator"):
        return [x for x in self.e.sessions() if x["role"] == role]

    def test_stop_pages_through_run_list_within_orcas_limit(self):
        # R2-1: Orca refuses --limit above 100; the session's Run may be on a later page.
        self.e.set_state(run_create_fails=True)
        self.e.run(str(self.repo), "--no-design-lead", env_extra={"LANE_RUN_WAIT": "0"})
        state = json.loads(self.e.state.read_text())
        runs = [{"runId": f"run_{i:012x}", "objective": f"other {i}"} for i in range(1, 141)]
        runs.insert(130, {"runId": "run_0000000abcde", "objective": "app coordinator"})
        state.update(run_create_fails=False, runs=runs, handover_on_send={"handover-latest": "new"})
        self.e.state.write_text(json.dumps(state))
        r = self.e.pause("stop", str(self.repo), "--wait", "2")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual([x["to"] for x in json.loads(self.e.state.read_text())["sent"]], ["run:run_0000000abcde"])
        limits = [c.split("--limit ", 1)[1].split()[0] for c in self.e.calls() if "run-list" in c]
        self.assertTrue(limits and all(int(x) <= 100 for x in limits), limits)

    def test_a_new_project_over_host_runs_the_native_path_there(self):
        # R2-2, phase 6: the host's copy has no store and no .cotal/ for it yet, so it takes the default.
        r = self.e.run(str(self.repo), "--via-host", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertFalse([c for c in self.e.calls() if c.startswith("cotal")])
        self.assertEqual(self.e.settings(self.repo)["orchestrator"], "native-orca")

    def live_agent(self, argv0, *args, role="coordinator", cwd=None):
        """A process whose argv[0] is argv0 (python, which ignores argv[0]; a multi-call coreutils would not run)."""
        p = subprocess.Popen(["bash", "-c", f"exec -a {argv0} \"$@\"", "x", *args], cwd=cwd or self.repo,
                             env={**self.e.env, "LANE_ROLE": role})
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        return p

    def test_a_live_agent_process_counts_without_agent_identity(self):
        # R2-3: Orca may not mark a terminal it did not recognise; a live agent process in the checkout still counts.
        self.e.set_state(agent_exits=True)  # the terminal never gets agentIdentity
        self.e.run(str(self.repo), "--no-design-lead")
        self.live_agent("claude", sys.executable, "-c", "import time; time.sleep(60)")
        r = self.e.run(str(self.repo), "--no-design-lead")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.started()), 1)  # not started twice
        self.assertIn("exists", r.stdout)

    def test_a_node_run_agent_counts_too(self):
        script = self.e.home / "tools" / "opencode2"
        script.parent.mkdir()
        script.write_text("import time\ntime.sleep(60)\n")
        self.e.set_state(agent_exits=True)
        self.e.run(str(self.repo), "--no-design-lead")
        self.live_agent("node", sys.executable, str(script))
        self.assertIn("sessions running: app coordinator", self.e.pause("status", str(self.repo)).stdout)

    def test_an_agent_of_another_role_or_folder_does_not_count(self):
        self.e.set_state(agent_exits=True)
        self.e.run(str(self.repo), "--no-design-lead")
        self.live_agent("claude", sys.executable, "-c", "import time; time.sleep(60)", role="design-lead")
        self.live_agent("claude", sys.executable, "-c", "import time; time.sleep(60)", cwd=self.e.home)
        self.e.run(str(self.repo), "--no-design-lead")
        self.assertEqual(len(self.started()), 2)

    def test_a_tracked_role_file_is_never_moved(self):
        # R2-4: an attached repo onboarded on Orca + Cotal keeps its committed adapted file where it is.
        m = load()
        f = self.repo / ".lane-control" / "coordinator.md"
        f.parent.mkdir()
        f.write_text("adapted\n")
        git("add", str(f), cwd=self.repo)
        git("commit", "-q", "-m", "adapted", cwd=self.repo)
        m.ORCHESTRATOR = "native-orca"
        with contextlib.redirect_stdout(io.StringIO()):
            m.move_role_files(self.repo)
        self.assertEqual(f.read_text(), "adapted\n")
        self.assertFalse((self.repo / ".lane-control" / "additions").exists())

    def test_the_store_key_reads_the_resolved_checkout_like_the_night_precheck(self):
        # R2-7: a checkout named through a symlink of another name keys the same store.
        core = str(ROOT / "core")
        if core not in sys.path:
            sys.path.insert(0, core)
        import machineconf
        link = self.e.home / "shop-link"
        link.symlink_to(self.repo)
        m = load()
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.e.env["XDG_CONFIG_HOME"], "HOME": str(self.e.home)}):
            self.assertEqual(machineconf.settings_file(link), machineconf.settings_file(self.repo))
            self.assertEqual(machineconf.settings_file(link).parent, m.store_dir(link))
            self.assertEqual(m.store_dir(link), m.store_dir(self.repo))

    def test_a_refusal_reads_as_one_sentence_end(self):
        self.assertEqual(self.e.run(str(self.repo), "--no-start").returncode, 0)
        f = self.e.store(self.repo) / "settings.json"
        st = json.loads(f.read_text())
        st["models"]["coordinator"] = "opencode/gw/m1"
        st["permission_mode"] = "plan"  # the one refusal left: a mode OpenCode does not have
        f.write_text(json.dumps(st))
        r = self.e.pause("start", str(self.repo))
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("..", r.stderr)


GH_PROTECTION = """#!/usr/bin/env python3
# gh stand-in for the branch-protection report: `gh api repos/{owner}/{repo}/branches/<b>/protection` answers from
# $GH_PROTECTION (JSON {"status": 200|403|404, "body": {...}, "message": "..."}), `gh api .../rules/branches/<b>`
# from $GH_RULES (a JSON list); `gh pr list` has no merged PRs. Every call is logged.
import json, os, sys
a = sys.argv[1:]
open(os.environ["GH_LOG"], "a").write(" ".join(a) + "\\n")
if a[:2] == ["pr", "list"]:
    print("[]"); sys.exit(0)
if a[:1] == ["api"] and a[1].endswith("/protection"):
    r = json.load(open(os.environ["GH_PROTECTION"]))
    if r["status"] == 200:
        print(json.dumps(r["body"])); sys.exit(0)
    print(f"gh: {r['message']} (HTTP {r['status']})", file=sys.stderr); sys.exit(1)
if a[:1] == ["api"] and "/rules/branches/" in a[1]:
    if os.environ.get("GH_RULES_FAIL"):
        print("gh: " + os.environ["GH_RULES_FAIL"] + " (HTTP 403)", file=sys.stderr); sys.exit(1)
    print(os.environ.get("GH_RULES", "[]")); sys.exit(0)
print("gh stub: not modelled: " + " ".join(a), file=sys.stderr); sys.exit(1)
"""

FULL = {"required_pull_request_reviews": {"required_approving_review_count": 0}, "enforce_admins": {"enabled": True},
        "allow_force_pushes": {"enabled": False}, "allow_deletions": {"enabled": False},
        "required_status_checks": {"contexts": ["unittest"], "checks": [{"context": "unittest"}]}}


class BranchProtectionTests(Case):
    """The integration branch's protection is read and reported, never changed (lc-2nz)."""

    def setUp(self):
        super().setUp()
        bare = self.e.home / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=self.repo, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "remote", "set-head", "origin", "main"], cwd=self.repo, check=True, capture_output=True)
        gh = self.e.bin / "gh"
        gh.unlink()
        gh.write_text(GH_PROTECTION)
        gh.chmod(0o755)
        self.log = self.e.home / "gh-prot.log"
        self.e.env.update(GH_LOG=str(self.log), GH_PROTECTION=str(self.e.home / "prot.json"))

    def protection(self, status=200, body=None, message="", rules=None):
        (self.e.home / "prot.json").write_text(json.dumps({"status": status, "body": body or {}, "message": message}))
        if rules is not None:
            self.e.env["GH_RULES"] = json.dumps(rules)

    def report(self):
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        lines = [l for l in r.stdout.splitlines() if l.startswith("branch-protection:")]
        self.assertEqual(len(lines), 1, r.stdout)
        return lines[0]

    def test_protected_for_everyone(self):
        self.protection(body=FULL)
        line = self.report()
        self.assertIn("everyone", line)
        self.assertIn("main", line)
        self.assertIn("admins included", line)

    def test_admins_exempt_is_partly_and_says_what_is_missing(self):
        self.protection(body={**FULL, "enforce_admins": {"enabled": False}})
        line = self.report()
        self.assertIn("partly", line)
        self.assertIn("admins are exempt", line)
        self.assertNotIn("everyone", line)

    def test_no_required_pull_request_and_force_pushes_allowed_are_both_named(self):
        body = {k: v for k, v in FULL.items() if k != "required_pull_request_reviews"}
        self.protection(body={**body, "allow_force_pushes": {"enabled": True}})
        line = self.report()
        self.assertIn("partly", line)
        self.assertIn("no pull request is required", line)
        self.assertIn("force pushes are allowed", line)

    def test_no_protection_says_none(self):
        self.protection(status=404, message="Branch not protected")
        line = self.report()
        self.assertIn("none", line)
        self.assertIn("main", line)

    def test_a_ruleset_is_not_reported_as_none(self):
        self.protection(status=404, message="Branch not protected", rules=[{"type": "pull_request"}, {"type": "non_fast_forward"}])
        line = self.report()
        self.assertIn("ruleset", line)
        self.assertIn("pull_request", line)
        self.assertNotIn("none", line)

    def test_rulesets_that_could_not_be_read_are_not_reported_as_none(self):
        self.protection(status=404, message="Branch not protected")
        self.e.env["GH_RULES_FAIL"] = "Resource not accessible"
        line = self.report()
        self.assertIn("unknown", line)
        self.assertIn("rulesets", line)
        self.assertNotIn("none", line)
        self.assertNotIn("push straight", line)

    def test_classic_protection_with_a_ruleset_on_top_says_both(self):
        self.protection(body={**FULL, "enforce_admins": {"enabled": False}},
                        rules=[{"type": "pull_request"}, {"type": "non_fast_forward"}])
        line = self.report()
        self.assertIn("partly", line)
        self.assertIn("admins are exempt", line)
        self.assertIn("ruleset also applies", line)
        self.assertIn("pull_request", line)

    def test_a_fully_protected_branch_is_labelled_everyone(self):
        self.protection(body=FULL)
        self.assertTrue(self.report().startswith("branch-protection: everyone "))

    def test_a_plan_without_protection_says_so(self):
        self.protection(status=403, message="Upgrade to GitHub Pro or make this repository public to enable this feature.")
        line = self.report()
        self.assertIn("not available", line)
        self.assertIn("plan", line)

    def test_another_failure_is_unknown_not_none(self):
        self.protection(status=403, message="Resource not accessible by personal access token")
        line = self.report()
        self.assertIn("unknown", line)
        self.assertIn("Resource not accessible", line)

    def test_ci_that_exists_but_is_not_required_is_said(self):
        wf = self.repo / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text("on: push\n")
        self.protection(body={k: v for k, v in FULL.items() if k != "required_status_checks"})
        line = self.report()
        self.assertIn("CI exists but is not required", line)

    def test_a_project_without_a_git_host_says_unknown(self):
        subprocess.run(["git", "remote", "remove", "origin"], cwd=self.repo, check=True)
        self.protection(body=FULL)
        self.assertIn("unknown", self.report())

    def test_it_only_reads(self):
        self.protection(body=FULL)
        self.report()
        calls = self.log.read_text().splitlines()
        self.assertTrue(any(c.startswith("api ") for c in calls), calls)
        for c in calls:
            self.assertTrue(c.startswith(("api ", "pr list", "repo view ")), c)  # repo view: lane cleanup's read, no edit
            self.assertNotRegex(c, r"-X|--method|-f |-F |--field|--input|PUT|PATCH|DELETE|POST")



# --- lc-a6d: Orca's merged cleanup rule (report only) and GitHub's delete-branch-on-merge ----------------------------

class CleanupSetupTests(Case):
    """The stub orca answers `workspace cleanup preview` from the state: `cleanup_cause` is the cause of the lane
    worktree row it adds beside each repo's main workspace; `cleanup_fails` makes the call fail."""

    def setUp(self):
        super().setUp()
        (self.e.bin / "gh").write_text(
            '#!/bin/sh\necho "$@" >> "$STUB_LOG.gh"\ncase "$1 $2" in\n'
            '  "repo view") [ -n "$STUB_DELETE_BRANCH" ] && echo "$STUB_DELETE_BRANCH" || exit 1;;\n'
            '  "repo edit") [ -z "$STUB_EDIT_FAILS" ] || { echo "HTTP 403" >&2; exit 1; };;\nesac\n')

    def gh_calls(self):
        f = Path(str(self.e.log) + ".gh")
        return f.read_text().splitlines() if f.exists() else []

    def setup(self, **env):
        return self.e.run(str(self.repo), "--no-start", env_extra=env)

    def test_rules_off_is_reported_with_where_to_turn_the_merged_rule_on(self):
        self.e.set_state(cleanup_cause="rules-off")
        r = self.setup()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        text = r.stdout + r.stderr
        self.assertIn("orca cleanup: off", text)
        self.assertIn("Settings -> Workspace Cleanup -> Cleanup rules -> 'Delete merged workspaces'", text)

    def test_cleanup_off_for_this_project_is_reported(self):
        self.e.set_state(cleanup_cause="project-off")
        r = self.setup()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("off for this project", r.stdout + r.stderr)
        self.assertIn("'Use global'", r.stdout + r.stderr)

    def test_a_rule_that_is_on_but_not_shown_to_be_the_merged_one_is_a_note_not_exists(self):
        self.e.set_state(cleanup_cause="no-rule-matched")
        r = self.setup()
        self.assertIn("note: orca cleanup: check", r.stderr)
        self.assertNotIn("orca cleanup: exists", r.stdout)
        self.assertNotIn("orca cleanup: off", r.stdout + r.stderr)

    def test_the_released_workers_rule_is_reported_recommended_with_where(self):
        self.e.set_state(cleanup_cause="rules-off")
        r = self.setup()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        text = r.stdout + r.stderr
        self.assertIn("orca cleanup workers: off", text)
        self.assertIn("recommended", text)
        self.assertIn("Cleanup rules -> 'Delete released orchestration worker workspaces'", text)

    def test_the_released_workers_rule_that_the_preview_cannot_show_says_so_as_a_note(self):
        self.e.set_state(cleanup_cause="no-rule-matched")
        r = self.setup()
        self.assertIn("note: orca cleanup workers: check", r.stderr)
        self.assertIn("cannot tell", r.stderr)
        self.assertNotIn("orca cleanup workers: exists", r.stdout)

    def test_it_never_runs_a_cleanup_or_changes_orca(self):
        self.e.set_state(cleanup_cause="rules-off")
        self.setup()
        calls = [c for c in self.e.calls() if "cleanup" in c or c.startswith("orca settings")]
        self.assertEqual(calls, ["orca workspace cleanup preview --json"])

    def test_a_failing_orca_cleanup_command_does_not_stop_setup(self):
        self.e.set_state(cleanup_fails=True)
        r = self.setup()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("orca cleanup: skipped", r.stdout)

    def test_the_check_also_runs_with_no_start_before_the_early_return(self):
        self.e.set_state(cleanup_cause="rules-off")
        r = self.setup()
        self.assertIn("start: skipped", r.stdout)
        self.assertIn("orca cleanup", r.stdout + r.stderr)

    def test_without_a_terminal_delete_branch_off_is_only_said(self):
        r = self.setup(STUB_DELETE_BRANCH="false")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("gh repo edit --delete-branch-on-merge", r.stdout + r.stderr)
        self.assertFalse([c for c in self.gh_calls() if c.startswith("repo edit")])

    def test_a_rerun_with_it_on_says_exists(self):
        r = self.setup(STUB_DELETE_BRANCH="true")
        self.assertIn("github: exists", r.stdout)
        self.assertFalse([c for c in self.gh_calls() if c.startswith("repo edit")])

    def test_a_host_gets_the_cleanup_module_with_the_copy(self):
        self.assertIn("core/cleanup.py", load().HOST_FILES)

    def test_gh_that_cannot_answer_is_skipped(self):
        r = self.setup()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("github: skipped", r.stdout)

    def terminal_run(self, answer):
        m = load()
        env = {**self.e.env, "STUB_DELETE_BRANCH": "false"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stdin", Tty()), \
                mock.patch("builtins.input", return_value=answer) as ask, contextlib.redirect_stdout(Tty()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            status = m.main([str(self.repo), "--no-start", "--skip-adapt"])
        self.assertEqual(status, 0, err.getvalue() + out.getvalue())
        return out.getvalue(), ask

    def test_the_owners_yes_in_a_terminal_turns_delete_branch_on(self):
        out, ask = self.terminal_run("y")
        self.assertIn("github: created", out)
        self.assertIn("repo edit --delete-branch-on-merge", self.gh_calls())
        self.assertTrue(any("Automatically delete head branches" in c.args[0] for c in ask.call_args_list))

    def test_the_owners_no_in_a_terminal_changes_nothing(self):
        out, ask = self.terminal_run("n")
        self.assertIn("github: skipped", out)
        self.assertFalse([c for c in self.gh_calls() if c.startswith("repo edit")])

    def test_enter_in_a_terminal_means_yes_for_the_recommended_step(self):
        out, ask = self.terminal_run("")
        self.assertIn("github: created", out)
        self.assertIn("repo edit --delete-branch-on-merge", self.gh_calls())
        q = [c.args[0] for c in ask.call_args_list if "Automatically delete head branches" in c.args[0]][0]
        self.assertIn("[Y/n]", q)
        self.assertIn("recommended", q)

    def test_anything_but_enter_y_yes_leaves_it_off(self):
        for answer in ("no", "N", " n ", "nope", "q", "no thanks", "0"):
            self.setUp()
            out, _ = self.terminal_run(answer)
            self.assertIn("github: skipped", out, answer)
            self.assertFalse([c for c in self.gh_calls() if c.startswith("repo edit")], answer)

    def test_yes_answers_are_enter_y_yes_any_case_trimmed(self):
        m = load()
        for answer in ("", " ", "y", "Y", " yes ", "YES"):
            with mock.patch.dict(os.environ, self.e.env, clear=True), mock.patch("sys.stdin", Tty()), \
                    contextlib.redirect_stdout(Tty()), mock.patch("builtins.input", return_value=answer):
                self.assertTrue(m.owner_says_yes("x [Y/n] ", "github", default=True), repr(answer))

    def test_eof_is_a_no_even_with_a_yes_default(self):
        m = load()
        with mock.patch.dict(os.environ, self.e.env, clear=True), mock.patch("sys.stdin", Tty()), \
                contextlib.redirect_stdout(Tty()), mock.patch("builtins.input", side_effect=EOFError):
            self.assertFalse(m.owner_says_yes("x [Y/n] ", "github", default=True))

    def test_other_steps_keep_enter_meaning_no(self):
        m = load()
        with mock.patch.dict(os.environ, self.e.env, clear=True), mock.patch("sys.stdin", Tty()), \
                contextlib.redirect_stdout(Tty()), mock.patch("builtins.input", return_value=""):
            self.assertFalse(m.owner_says_yes("Commit orca.yaml? [y/N] ", "publish"))
            self.assertTrue(m.owner_says_yes("x [Y/n] ", "github", default=True))


if __name__ == "__main__":
    unittest.main()
