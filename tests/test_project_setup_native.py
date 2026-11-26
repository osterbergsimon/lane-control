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
sys.path.insert(0, os.environ["STUB_DIR"])
from fake_proc import keep_running
keep_running(name)  # it stays in the fake /proc, unless the test says the agent exits
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
        self.env.pop("ORCA_CLAUDE_CONFIG_DIR", None)  # transcripts are looked for under the temp HOME alone
        self.env["ORCA_TERMINAL_HANDLE"] = "term_owner"  # project-pause runs in one of Orca's terminals
        self.env.pop("LANE_ROLE", None)
        self.proc = self.home / "proc"  # the running agents, as /proc shows them (LANE_PROC_DIR)
        self.proc.mkdir()
        self.env["LANE_PROC_DIR"] = str(self.proc)
        self.env["STUB_DIR"] = str(ROOT / "tests" / "stubs")

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
# the model each run is started with: the alias under the run's own provider id, whatever the owner's (m27)
RUN_ADAPTER, RUN_REVIEWER = "lane-gateway/code", "lane-gateway/deep"
GATEWAY_URL = "http://127.0.0.1:4000/v1"
GATEWAY_FLAGS = ("--gateway-url", GATEWAY_URL, "--gateway-key-env", "LITELLM_API_KEY")
ALL_OPENCODE = ("--adapter", f"opencode/{OC_ADAPTER}", "--adapt-reviewer", f"opencode/{OC_REVIEWER}",
                "--coordinator", "opencode/litellm/chat", "--reviewer", "opencode/litellm/deep", "--no-start",
                *GATEWAY_FLAGS)
GATEWAY_KEY = "sk-example-gateway-key-0000"
# the owner's OpenCode config is never read for onboarding (round 6): a provider of the same id pointing elsewhere,
# headers, literal secrets, and everything else in it must stay out of the run (B10-B14, M7, M8)
OWNER_BLOCK = {"npm": "@ai-sdk/openai", "api": "https://api.openai.com/v1", "name": "Owner's",
               "models": {"code": {"options": {"apiKey": "sk-model-literal"}}},
               "options": {"baseURL": "http://{env:GW_HOST}/v1", "apiKey": "{env:LITELLM_API_KEY}",
                           "headers": {"Authorization": "Bearer sk-header-literal"}}}
OWNER_OPENCODE = {"provider": {"litellm": OWNER_BLOCK, "other": {"options": {"apiKey": "{env:OTHER}"}}},
                  "providers": {"litellm": {"options": {"baseURL": "https://elsewhere.example/v1"}}},
                  "formatter": True, "lsp": True, "mcp": {"x": {"type": "local", "command": ["touch", "/nonexistent"]}},
                  "plugin": ["some-plugin"], "permission": {"*": "allow"}, "default_agent": "plan",
                  "instructions": ["~/notes.md"]}


class OpenCodeCase(Case):
    """The helpers of the OpenCode onboarding tests: the agent stub as opencode2, the owner's OpenCode config, main()
    in a terminal."""

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
        self.e.env["GW_HOST"] = "elsewhere.example"

    def owner_config(self, conf):
        """The owner's own OpenCode config (~/.config/opencode/opencode.json under the test HOME)."""
        f = Path(self.e.env["XDG_CONFIG_HOME"]) / "opencode" / "opencode.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(conf))

    def setup(self, *args, answer="y", env_extra=None, patch=None, answers=None):
        """main() in a terminal; every question answered `answer` (or the list `answers`, in turn). The coordinator
        pins' served-model check (MOD-1) is not what these tests are about: --no-model-check."""
        m = load()
        for k, v in (patch or {}).items():
            setattr(m, k, v)
        self.questions = []

        def ask(q=""):
            self.questions.append(q)
            return answers.pop(0) if answers is not None else answer
        with mock.patch.dict(os.environ, {**self.e.env, **(env_extra or {})}, clear=True), \
                mock.patch("sys.stdin", Tty()), mock.patch("builtins.input", side_effect=ask), \
                contextlib.redirect_stdout(Tty()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            status = m.main([str(self.repo), *args, "--no-model-check"])
        self.out, self.err = out.getvalue(), err.getvalue()
        return status

    def config(self, run):
        agents = json.loads(run["config"])["agents"]
        self.assertEqual(sorted(agents)[1:], ["title"])  # the run's agent, and OpenCode's title agent pinned (m27)
        name, conf = next((n, c) for n, c in agents.items() if n != "title")
        self.assertEqual(run["argv"][run["argv"].index("--agent") + 1], name)
        return conf

    def rules(self, run):
        return [(r["action"], r["resource"], r["effect"]) for r in self.config(run)["permissions"]]


class OpenCodeOnboardingTests(OpenCodeCase):
    """LIT-2 (lc-8h1): --adapter and --adapt-reviewer take opencode/<provider>/<model> and run `opencode2 run`, on a
    machine that may have no Claude at all (a self-hosted gateway only)."""

    def test_an_all_opencode_setup_onboards_with_no_claude_on_path(self):
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
        status = self.setup(*ALL_OPENCODE, env_extra={"LANE_ROLE": "coordinator", "LANE_PERSONA": "/elsewhere/c.md",
                                                      "OPENCODE_CONFIG_DIR": "/elsewhere", "SECRET_TOKEN": "x"})
        self.assertEqual(status, 0, self.err + self.out)
        self.assertNotIn("claude not on PATH", self.err)
        runs = self.e.agent_runs()
        self.assertEqual([(r["tool"], r["model"]) for r in runs], [("opencode2", RUN_ADAPTER), ("opencode2", RUN_REVIEWER)])
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
                # B15: the key reaches OpenCode under lane-control's own variable only, never under the owner's name
                # (OPENCODE_API_KEY would go to opencode.ai): never an argument, never in the config
                self.assertEqual(r["env"]["LANE_GATEWAY_KEY"], GATEWAY_KEY)
                self.assertIsNone(r["env"]["LITELLM_API_KEY"])
                self.assertNotIn(GATEWAY_KEY, " ".join(r["argv"]))
                self.assertNotIn(GATEWAY_KEY, r["config"])
                # B10: OpenCode starts from nothing: HOME and XDG folders of the run's own, one config file in them
                # holding the provider block (key by name only), formatter, lsp and MCP off, and the run's agent
                for var, d in r["dirs"].items():
                    self.assertTrue(d and not Path(d).exists() and not Path(d).is_relative_to(self.e.home), (var, d))
                self.assertEqual(r["config_dir"], ["opencode/opencode.json"])
                conf = json.loads(r["config"])
                self.assertEqual(sorted(conf), ["agents", "autoupdate", "formatter", "instructions", "lsp", "mcp",
                                                "plugin", "provider", "share"])
                self.assertEqual((conf["formatter"], conf["lsp"], conf["mcp"], conf["instructions"], conf["plugin"]),
                                 (False, False, {}, [], []))
                # written fresh from --gateway-url and --gateway-key-env: nothing of the owner's OpenCode config; under
                # the run's own provider id, so no OpenCode built-in for "litellm" (or "opencode") applies (m27)
                self.assertEqual(conf["provider"], {"lane-gateway": {
                    "npm": "@ai-sdk/openai-compatible", "name": "litellm", "models": {r["model"].split("/", 1)[1]: {}},
                    "options": {"baseURL": GATEWAY_URL, "apiKey": "{env:LANE_GATEWAY_KEY}"}}})
                # m27: OpenCode's title agent (its side request) takes the run's model, never a default of its own
                self.assertEqual(conf["agents"]["title"], {"model": r["model"]})
                for owner in ("sk-model-literal", "sk-header-literal", "api.openai.com", "elsewhere", "GW_HOST"):
                    self.assertNotIn(owner, r["config"])
                self.assertIsNone(r["env"]["OPENCODE_CONFIG"])
                self.assertIsNone(r["env"]["OPENCODE_CONFIG_CONTENT"])
                # nothing else of this environment: no marker, no owner OPENCODE_*, no other secret
                allowed = {"PATH", "HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME",
                           "LANE_GATEWAY_KEY", "OPENCODE_DISABLE_PROJECT_CONFIG",
                           "OPENCODE_DISABLE_CLAUDE_CODE", "OPENCODE_DISABLE_AUTOUPDATE", "OPENCODE_DISABLE_MODELS_FETCH",
                           "PWD", "SHLVL", "_"}
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
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
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
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
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
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
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
        self.opencode_agents({RUN_ADAPTER: [{"write": {ADDITION: "x\n", "AGENTS.md": "rewritten\n"}}]})
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
                self.opencode_agents({RUN_ADAPTER: [{"write": {ADDITION: "x\n", **write}}]})
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
                self.opencode_agents({"sonnet": [{"write": {ADDITION: "x\n", **write}}], RUN_REVIEWER: [PASS]},
                                     claude=True)
                status = self.setup("--adapter", "claude/sonnet", "--adapt-reviewer", f"opencode/{OC_REVIEWER}",
                                    "--no-start", *GATEWAY_FLAGS)
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
        self.opencode_agents({RUN_ADAPTER: [{"write": {ADDITION: "x\n", ".lane-control/project.md": "p\n"}}]})
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
                self.opencode_agents({RUN_ADAPTER: [step]})
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
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 0, self.err + self.out)
        persona = (self.repo / ".lane-control" / "reviewer.md").read_text()
        self.opencode_agents({RUN_ADAPTER: [{"write": {".lane-control/reviewer.md": "## For the reviewer\nCheck x.\n"}}],
                              RUN_REVIEWER: [PASS]})
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
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE]})
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
                self.opencode_agents({RUN_ADAPTER: [{"write": {".lane-control/project.md": "model's\n",
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
        self.opencode_agents({RUN_ADAPTER: [{"write": {".lane-control/routing.md": "model's\n", ADDITION: "x\n"}}]})
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
                self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE]})
                self.assertEqual(self.setup(*ALL_OPENCODE, "--private", g), 1)
                self.assertIn(f"--private {g!r} covers", self.err)
                self.assertIn("which onboarding writes or its reviewer must read: narrow the glob", self.err)
                self.assertEqual(self.e.agent_runs(), [])
                st = self.e.store(self.repo) / "settings.json"
                st.write_text(json.dumps({**json.loads(st.read_text()), "private": []}))

    def test_the_reviewer_writes_nothing_back(self):
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [{"write": {ADDITION: "changed\n"}, **PASS}]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 1)
        self.assertIn(f"the OpenCode reviewer changed {ADDITION} in its copy", self.err)
        self.assertEqual((self.repo / ADDITION).read_text(), WRITE_NATIVE["write"][ADDITION])

    def test_an_opencode_onboarding_agent_needs_the_gateway_given_explicitly(self):
        # round 6: no OpenCode config is read; the gateway comes from --gateway-url alone, refused before any agent
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE]})
        no_gateway = [x for x in ALL_OPENCODE if x not in GATEWAY_FLAGS]
        self.assertEqual(self.setup(*no_gateway), 1)
        self.assertIn("no --gateway-url: an OpenCode onboarding agent (litellm/code) reaches its gateway only by the URL "
                      "given to project-setup", self.err)
        self.assertEqual(self.e.agent_runs(), [])
        for url, said in (("http://{env:GW_HOST}/v1", "must be a complete http(s) URL written out"),
                          ("https:///v1", "must be a complete http(s) URL written out"),
                          ("ftp://gw.example/v1", "must be a complete http(s) URL written out"),
                          ("http://user:pw@gw.example/v1", "must not carry a user or password"),
                          ("https://api.openai.com/v1", "is a vendor's public API"),
                          ("https://x.openai.azure.com/openai", "is a vendor's public API"),
                          ("https://api.anthropic.com", "is a vendor's public API")):
            with self.subTest(url=url):
                r = self.e.run(str(self.repo), *no_gateway, "--gateway-url", url, adapt=True)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn(said, r.stderr)
        r = self.e.run(str(self.repo), *no_gateway, "--gateway-url", GATEWAY_URL, "--gateway-key-env", "A-B", adapt=True)
        self.assertIn("takes the NAME of an environment variable", r.stderr)
        # m26: a key variable that is not set is never stored, and its "name" (maybe a pasted key) never shown
        self.e.env.pop("LITELLM_API_KEY")
        self.assertEqual(self.setup(*ALL_OPENCODE), 2)
        self.assertIn("the variable --gateway-key-env names is not set here, so it was not stored (its name is not "
                      "shown, in case a key was pasted there)", self.err)
        self.assertNotIn("LITELLM_API_KEY", self.err + self.out)
        self.assertEqual(self.e.agent_runs(), [])
        self.assertNotIn("gateway", self.e.settings(self.repo))
        # a stored key variable that is unset at a later run is named: it was set when it was stored
        self.e.env["LITELLM_API_KEY"] = GATEWAY_KEY
        self.e.set_state(agent_steps={RUN_ADAPTER: [{"exit": 1, "error": "stop"}]})
        self.setup(*ALL_OPENCODE)
        self.e.env.pop("LITELLM_API_KEY")
        self.assertEqual(self.setup(*[x for x in ALL_OPENCODE if x not in GATEWAY_FLAGS]), 1)
        self.assertIn("the gateway key's environment variable LITELLM_API_KEY (--gateway-key-env) is not set", self.err)

    def test_the_gateway_is_kept_per_project_and_never_its_key(self):
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
        self.assertEqual(self.setup(*ALL_OPENCODE, answer="n"), 3, self.err + self.out)
        self.assertEqual(self.e.settings(self.repo)["gateway"], {"url": GATEWAY_URL, "key_env": "LITELLM_API_KEY"})
        self.assertNotIn(GATEWAY_KEY, (self.e.store(self.repo) / "settings.json").read_text())
        self.e.set_state(agent_steps={RUN_REVIEWER: [PASS]})
        self.assertEqual(self.setup("--no-start"), 0, self.err + self.out)  # no flags: the stored gateway and pins
        conf = json.loads(self.e.agent_runs()[-1]["config"])
        self.assertEqual(conf["provider"]["lane-gateway"]["options"]["baseURL"], GATEWAY_URL)

    def test_any_provider_id_runs_as_the_runs_own_with_only_the_given_url(self):
        # m27: a built-in or vendor-named provider id (opencode, github-copilot, openai) would bring OpenCode's own
        # defaults for it (a side model, a key it reads); the run's provider id is lane-gateway whatever is given
        for adapter, reviewer in (("openai/gpt", "anthropic/claude"), ("opencode/gpt", "github-copilot/claude")):
            with self.subTest(adapter=adapter):
                shutil.rmtree(self.repo / ".lane-control", ignore_errors=True)
                self.opencode_agents({"lane-gateway/gpt": [WRITE_NATIVE], "lane-gateway/claude": [PASS]})
                self.assertEqual(self.setup("--adapter", f"opencode/{adapter}", "--adapt-reviewer",
                                            f"opencode/{reviewer}", "--no-start", "--readapt", *GATEWAY_FLAGS, answer="n"),
                                 3, self.err + self.out)
                for r, given in zip(self.e.agent_runs()[-2:], (adapter, reviewer)):
                    conf = json.loads(r["config"])
                    self.assertEqual(list(conf["provider"]), ["lane-gateway"])
                    block = conf["provider"]["lane-gateway"]
                    self.assertEqual((block["npm"], block["options"]["baseURL"], block["name"]),
                                     ("@ai-sdk/openai-compatible", GATEWAY_URL, given.split("/")[0]))
                    self.assertEqual(r["model"], "lane-gateway/" + given.split("/")[1])

    def test_two_onboarding_agents_with_one_alias_are_refused(self):
        # the provider id is not the run's, so litellm/code and other/code are the same model at the one gateway
        self.opencode_agents({})
        self.assertEqual(self.setup("--adapter", "opencode/litellm/code", "--adapt-reviewer", "opencode/other/code",
                                    "--no-start", *GATEWAY_FLAGS), 2)
        self.assertIn("--adapter and --adapt-reviewer must be different models, both are code at the one gateway",
                      self.err)

    def test_a_bad_opencode_reviewer_stops_before_the_claude_adapter_runs(self):
        # m22: each OpenCode role is checked before any agent starts
        self.opencode_agents({"sonnet": [WRITE_NATIVE]}, claude=True)
        self.assertEqual(self.setup("--adapter", "claude/sonnet", "--adapt-reviewer", f"opencode/{OC_REVIEWER}",
                                    "--no-start"), 1)
        self.assertIn("no --gateway-url", self.err)
        self.assertEqual(self.e.agent_runs(), [])

    def test_a_system_config_folder_stops_an_opencode_run(self):
        # M6: OpenCode loads its system folder whatever HOME is, with no switch to keep it out
        system = self.e.home / "etc-opencode"
        system.mkdir()
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE]})
        self.assertEqual(self.setup(*ALL_OPENCODE, patch={"SYSTEM_CONFIG": {"linux": str(system),
                                                                             "darwin": str(system)}}), 1)
        self.assertIn(f"{system} exists: OpenCode loads that system config folder in every run", self.err)
        self.assertEqual(self.e.agent_runs(), [])

    def test_the_owners_questions_are_printed_without_control_characters(self):
        # m18, m21: the adapter's open questions go to the screen plain, and so does the text left for the
        # coordinator at the end (LEFTOVER, printed when no coordinator starts in this run)
        q = ("## AGENTS.md edits you would make\n\n## Questions for the owner\n"
             "- Which branch\x1b[8m hides the list\x1b]52;c;eA==\x07?\n")
        self.opencode_agents({RUN_ADAPTER: [{**WRITE_NATIVE, "write": {**WRITE_NATIVE["write"],
                                                                      ".lane-control/SUGGESTIONS.md": q}}],
                              RUN_REVIEWER: [PASS]})
        self.setup(*ALL_OPENCODE, answers=["", "y", "n", "n"])
        out, err = self.out, self.err
        self.assertIn("Questions the adapting agent could not settle", out)  # the question was asked
        self.assertIn("1. - Which branch[8m hides the list]52;c;eA==?", out)  # its ESC and BEL bytes gone
        self.assertIn("Left open at the commit", err)  # LEFTOVER: printed, since no coordinator started
        self.assertIn("Which branch", err)
        self.assertNotIn("\x1b", out + err)

    def test_an_empty_opencode_run_is_a_failure_with_what_it_said(self):
        # m2: exit 0 with no answer and no tool call (a help print, an empty answer) is no result
        self.opencode_agents({RUN_ADAPTER: [{"err": "Run OpenCode with a message"}]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 1)
        self.assertIn(f"opencode2 run --model {OC_ADAPTER} gave no answer and called no tool: Run OpenCode with a "
                      "message", self.err)
        self.assertNotIn("wrote nothing", self.err)

    def test_the_onboarding_pins_are_kept_per_project(self):
        # m1: a later run without the flags uses the stored ones, never silently the Claude defaults
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
        self.assertEqual(self.setup(*ALL_OPENCODE, answer="n"), 3, self.err + self.out)  # stopped before the commit
        models = self.e.settings(self.repo)["models"]
        self.assertEqual((models["adapter"], models["adapt-reviewer"]), (f"opencode/{OC_ADAPTER}", f"opencode/{OC_REVIEWER}"))
        self.e.set_state(agent_steps={RUN_REVIEWER: [PASS]})
        self.assertEqual(self.setup("--no-start"), 0, self.err + self.out)  # no flags: the stored pins, no claude
        self.assertEqual([r["model"] for r in self.e.agent_runs()], [RUN_ADAPTER, RUN_REVIEWER, RUN_REVIEWER])
        self.assertIn(f"review: running opencode/{OC_REVIEWER} (from the settings store)", self.out)

    def test_a_default_pin_says_so_where_claude_is_missing(self):
        self.opencode_agents({})
        self.assertEqual(self.setup("--no-start"), 1)
        self.assertIn("claude not on PATH, but the onboarding agents use it (--adapter, --adapt-reviewer not given and "
                      "none in the settings store, so the default; pass opencode/<provider>/<model>", self.err)
        self.assertNotIn("adapter", self.e.settings(self.repo)["models"])  # a default is never written

    def test_the_reviewer_round_runs_on_opencode_too(self):
        fix = {"say": "project.md names the wrong command.\nVERDICT: FIX"}
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE, {"write": {".lane-control/project.md": "fixed\n"}}],
                              RUN_REVIEWER: [fix, PASS]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 0, self.err + self.out)
        runs = self.e.agent_runs()
        self.assertEqual([r["model"] for r in runs], [RUN_ADAPTER, RUN_REVIEWER, RUN_ADAPTER, RUN_REVIEWER])
        self.assertIn("project.md names the wrong command", runs[2]["prompt"])  # the findings reach the fix round
        self.assertEqual((self.repo / ".lane-control" / "project.md").read_text(), "fixed\n")

    def test_a_failed_opencode_run_says_what_opencode_said(self):
        self.opencode_agents({RUN_ADAPTER: [{"error": "ECONNREFUSED: Unable to connect.", "exit": 1}]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 1)
        self.assertIn("opencode2 run --model failed: ECONNREFUSED: Unable to connect.", self.err)
        self.assertIn("Nothing was committed", self.err)

    def test_an_opencode_reviewer_out_of_steps_is_a_clean_fail(self):
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [{"steps": 60, "say": "I ran out."}]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 1)
        self.assertIn("the reviewer used all 60 turns", self.err)

    def test_opencode2_missing_is_named_before_any_agent(self):
        self.opencode_agents({})
        (self.e.bin / "opencode2").unlink()
        self.assertEqual(self.setup(*ALL_OPENCODE), 1)
        self.assertIn("opencode2 not on PATH, but the onboarding agents run on opencode", self.err)
        self.assertEqual(self.e.agent_runs(), [])

    def test_a_claude_role_still_needs_claude(self):
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE]})
        self.assertEqual(self.setup("--adapter", f"opencode/{OC_ADAPTER}", "--no-start"), 1)  # reviewer: claude/opus
        self.assertIn("claude not on PATH, but the onboarding agents use it", self.err)
        self.assertEqual(self.e.agent_runs(), [])

    def test_mixed_harnesses_each_run_their_own(self):
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], "opus": [PASS]}, claude=True)
        self.assertEqual(self.setup("--adapter", f"opencode/{OC_ADAPTER}", "--no-start", *GATEWAY_FLAGS), 0, self.err + self.out)
        self.assertEqual([(r["tool"], r["model"]) for r in self.e.agent_runs()],
                         [("opencode2", RUN_ADAPTER), ("claude", "opus")])

    def test_refresh_of_an_opencode_project_needs_no_claude(self):
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 0, self.err + self.out)
        r = self.e.run(str(self.repo), "--refresh", adapt=True)  # adapt: no --skip-adapt, which --refresh refuses
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("claude", r.stderr)
        self.assertEqual(len(self.e.agent_runs()), 2)  # a refresh adapts nothing



class GatewayRound7Tests(OpenCodeCase):
    """Review round 7 (lc-8h1): the key under lane-control's own name (B15), the URL and its key variable as one
    setting (M9), a URL only as it is written (M11) and not a known vendor's (m23), a key never shown or stored where
    its name goes (m26)."""

    def store_gateway(self, url, key_env):
        f = self.e.store(self.repo) / "settings.json"
        f.write_text(json.dumps({**json.loads(f.read_text()), "gateway": {"url": url, "key_env": key_env}}))

    def test_the_key_reaches_the_run_only_under_lane_controls_own_name(self):
        # B15: OPENCODE_API_KEY is OpenCode's own (it sends it to opencode.ai); never hand OpenCode the owner's name
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
        self.e.env["OPENCODE_API_KEY"] = GATEWAY_KEY
        flags = [x for x in ALL_OPENCODE if x not in GATEWAY_FLAGS]
        self.assertEqual(self.setup(*flags, "--gateway-url", GATEWAY_URL, "--gateway-key-env", "OPENCODE_API_KEY"), 0,
                         self.err + self.out)
        for r in self.e.agent_runs():
            with self.subTest(model=r["model"]):
                self.assertIsNone(r["env"]["OPENCODE_API_KEY"])
                self.assertEqual(r["env"]["LANE_GATEWAY_KEY"], GATEWAY_KEY)
                opts = json.loads(r["config"])["provider"]["lane-gateway"]["options"]
                self.assertEqual(opts["apiKey"], "{env:LANE_GATEWAY_KEY}")
                self.assertNotIn("OPENCODE_API_KEY", r["config"])
        self.assertEqual(self.e.settings(self.repo)["gateway"], {"url": GATEWAY_URL, "key_env": "OPENCODE_API_KEY"})

    def test_the_gateway_and_its_key_are_said_before_the_first_agent(self):
        # M9: where the prompts go and which key goes with them
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
        self.assertEqual(self.setup(*ALL_OPENCODE, answer="n"), 3, self.err + self.out)
        line = f"gateway: using {GATEWAY_URL}, key from $LITELLM_API_KEY\n"
        self.assertIn(line, self.out)
        self.assertLess(self.out.index(line), self.out.index("adapt: running"))
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
        self.assertEqual(self.setup("--readapt", "--gateway-key-env", "", answer="n"), 3, self.err + self.out)
        self.assertIn(f"gateway: using {GATEWAY_URL}, no key\n", self.out)  # '' is no key, and it is kept so
        self.assertEqual(self.e.settings(self.repo)["gateway"], {"url": GATEWAY_URL})
        self.assertNotIn("apiKey", self.e.agent_runs()[-1]["config"])
        self.assertIsNone(self.e.agent_runs()[-1]["env"]["LANE_GATEWAY_KEY"])
        other = "http://10.0.0.5:4000/v1"
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
        self.assertEqual(self.setup("--readapt", "--gateway-url", other, answer="n"), 3, self.err + self.out)
        self.assertIn(f"gateway: using {other}, no key; plain http: for loopback or a network you trust\n", self.out)

    def test_a_new_gateway_url_never_takes_the_old_key_along_unasked(self):
        # M9: gateway A had a key; B (a keyless local one, say) must not get A's key
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
        self.assertEqual(self.setup(*ALL_OPENCODE, answer="n"), 3, self.err + self.out)
        new = "http://127.0.0.1:5000/v1"
        # without a terminal: dropped, and said
        self.e.set_state(agent_steps={RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
        r = self.e.run(str(self.repo), "--readapt", "--gateway-url", new, "--no-start", "--no-model-check", adapt=True)
        self.assertEqual(r.returncode, 3, r.stderr + r.stdout)
        self.assertIn(f"gateway: key dropped $LITELLM_API_KEY was for {GATEWAY_URL}; pass --gateway-key-env "
                      f"LITELLM_API_KEY to send it to {new}", r.stdout)
        self.assertIn(f"gateway: using {new}, no key", r.stdout)
        self.assertEqual(self.e.settings(self.repo)["gateway"], {"url": new})
        run = self.e.agent_runs()[-1]
        self.assertIsNone(run["env"]["LANE_GATEWAY_KEY"])
        self.assertNotIn("apiKey", run["config"])
        # in a terminal: asked; no drops it, yes keeps it
        for answer, kept in (("n", False), ("y", True)):
            with self.subTest(answer=answer):
                self.store_gateway(GATEWAY_URL, "LITELLM_API_KEY")
                self.e.set_state(agent_steps={RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
                self.setup("--readapt", "--gateway-url", new, answers=[answer, "n", "n", "n", "n"])
                self.assertIn(f"The stored gateway key variable $LITELLM_API_KEY was for {GATEWAY_URL}. Send its key to "
                              f"{new} too? [y/N] ", self.questions)
                self.assertEqual(self.e.settings(self.repo)["gateway"],
                                 {"url": new, **({"key_env": "LITELLM_API_KEY"} if kept else {})})
                self.assertEqual(self.e.agent_runs()[-1]["env"]["LANE_GATEWAY_KEY"], GATEWAY_KEY if kept else None)
        # the same URL again keeps its key, asked nothing
        self.store_gateway(GATEWAY_URL, "LITELLM_API_KEY")
        self.e.set_state(agent_steps={RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
        self.setup("--readapt", "--gateway-url", GATEWAY_URL, answer="n")
        self.assertFalse([q for q in self.questions if "gateway key variable" in q])
        self.assertEqual(self.e.agent_runs()[-1]["env"]["LANE_GATEWAY_KEY"], GATEWAY_KEY)

    def test_a_key_given_where_its_name_goes_is_never_shown_or_stored(self):
        # m26: an sk- key is refused without being echoed; a key of letters and digits is no set variable
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE]})
        flags = [x for x in ALL_OPENCODE if x not in GATEWAY_FLAGS]
        for key in ("sk-live-abc123def456", "a1b2c3d4e5f6a7b8c9d0"):
            with self.subTest(key=key):
                r = self.e.run(str(self.repo), *flags, "--gateway-url", GATEWAY_URL, "--gateway-key-env", key,
                               "--no-model-check", adapt=True)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertNotIn(key, r.stdout + r.stderr)
                self.assertNotIn(key, (self.e.store(self.repo) / "settings.json").read_text()
                                 if (self.e.store(self.repo) / "settings.json").exists() else "")
                self.assertEqual(self.e.agent_runs(), [])
        r = self.e.run(str(self.repo), "--suggest-tiers", "litellm", "--gateway-url", GATEWAY_URL,
                       "--gateway-key-env", "a1b2c3d4e5f6a7b8c9d0")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("a1b2c3d4e5f6a7b8c9d0", r.stdout + r.stderr)

    def test_only_a_url_written_out_plainly_is_taken(self):
        # M11: each of these reads as one host to Python and as api.openai.com to OpenCode's WHATWG parser (or is
        # unclear); m23: vendor hosts the round-6 list missed; a key in the query string
        m = load()
        refused = ["https://api.openai.com./v1", "https://api.%6Fpenai.com/v1", "https://api.openai.com\\.gw.internal.test/v1",
                   "https://api。openai。com/v1", "https://api.open­ai.com/v1", "https://api.open​ai.com/v1",
                   "https://API.OPENAI.COM/v1", "https://ａpi.openai.com/v1", "http://gw.example/v1?key=sk-x",
                   "http://gw.example/v1#x", "http://gw..example/v1", "http://gw.example:0/v1", "http://gw.example:99999",
                   "http://[::1/v1", "http:/gw.example/v1", "http://gw.example/v%31", "http://gw.example/v1\\x",
                   "http://u@gw.example/v1", "http://@gw.example/v1", "http://u:p@gw.example/v1",
                   "https://api.together.ai/v1", "https://x.services.ai.azure.com/models",
                   "https://x.cognitiveservices.azure.com/openai", "https://models.inference.ai.azure.com",
                   "https://models.github.ai/inference", "https://api.githubcopilot.com",
                   "https://integrate.api.nvidia.com/v1", "https://api.cerebras.ai/v1", "https://api.deepinfra.com/v1",
                   "https://router.huggingface.co/v1", "https://ai-gateway.vercel.sh/v1",
                   "https://gateway.ai.cloudflare.com/v1/x/y", "https://api.cloudflare.com/client/v4",
                   "https://api.moonshot.ai/v1", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                   "https://api.z.ai/api", "https://api.sambanova.ai/v1", "https://api.replicate.com/v1",
                   "https://ollama.com/api", "https://opencode.ai/zen/v1"]
        for url in refused:
            with self.subTest(refused=ascii(url)):
                self.assertIsNotNone(m.gateway_url_refusal(url))
        for url in ("http://127.0.0.1:4000/v1", "http://[::1]:4000/v1", "https://gw.example.internal/v1",
                    "http://gw_host:4000", "http://localhost:11434/v1", "https://xn--bcher-kva.example/v1",
                    "https://litellm.corp.example/api/v1/"):
            with self.subTest(taken=url):
                self.assertIsNone(m.gateway_url_refusal(url))
        for url, said in (("https://api.openai.com./v1", "is a vendor's public API"),
                          ("https://api.openai.com\\.gw.internal.test/v1", "must be a complete http(s) URL written out"),
                          ("https://api.%6Fpenai.com/v1", "with no %, backslash"),
                          ("http://gw.example/v1?key=sk-secret", "with no query or fragment")):
            with self.subTest(url=url):
                r = self.e.run(str(self.repo), "--gateway-url", url, adapt=True)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn(said, r.stderr)
        r = self.e.run(str(self.repo), "--gateway-url", "https://api.open​ai.com/v1", adapt=True)
        self.assertIn("\\u200b", r.stderr)  # an invisible character is shown, not printed as nothing
        self.assertNotIn("sk-secret", self.e.run(str(self.repo), "--gateway-url", "http://gw.example/v1?key=sk-secret",
                                                 adapt=True).stderr)  # a key may be in a query string: never shown


class TrustGatewayTests(OpenCodeCase):
    """--trust-gateway (owner 2026-11-26): the OpenCode onboarding agents read the repo itself, after a plain warning
    and one yes (or the flag alone with no terminal); the run is otherwise the same, and its writes are checked by the
    repo's state before and after."""

    def trusted(self, steps, *extra, answer="y", answers=None):
        self.opencode_agents(steps)
        return self.setup(*ALL_OPENCODE, "--trust-gateway", *extra, answer=answer, answers=answers)

    def test_after_the_warning_and_a_yes_the_agents_read_the_repo_itself(self):
        (self.repo / ".env").write_text("SECRET=1\n")
        (self.repo / ".gitignore").write_text("data/\n")
        (self.repo / "data").mkdir()
        (self.repo / "data" / "x.csv").write_text("p\n")
        git("add", ".gitignore", cwd=self.repo)
        git("commit", "-qm", "ignore", cwd=self.repo)
        self.assertEqual(self.trusted({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]}, "--private", "data/**"), 0,
                         self.err + self.out)
        warning = self.out.index("WARNING (--trust-gateway)")
        self.assertIn(f"will run in {self.repo} itself, not in a filtered copy. The model will see every file in that "
                      "folder: ignored and private ones included", self.out)
        self.assertIn("This is meant for a model on a gateway you run yourself", self.out)
        self.assertIn(GATEWAY_URL, self.out[warning:])
        self.assertLess(warning, self.out.index("adapt: running"))
        trust = [q for q in self.questions if "every file readable" in q]
        self.assertEqual(trust, [f"Run them in {self.repo} itself, every file readable? [y/N] "])  # one yes for both
        runs = self.e.agent_runs()
        self.assertEqual([r["model"] for r in runs], [RUN_ADAPTER, RUN_REVIEWER])
        for r in runs:
            with self.subTest(model=r["model"]):
                self.assertEqual(Path(r["cwd"]).resolve(), self.repo.resolve())
                for private in (".env", "data/x.csv"):  # nothing is filtered: that is what the yes was for
                    self.assertIn(private, r["files"])
                # still in both modes: the config from nothing, the key under lane-control's name, edit on exact paths
                conf = json.loads(r["config"])
                self.assertEqual(list(conf["provider"]), ["lane-gateway"])
                self.assertEqual((conf["formatter"], conf["lsp"], conf["mcp"], conf["plugin"]), (False, False, {}, []))
                self.assertEqual(r["env"]["LANE_GATEWAY_KEY"], GATEWAY_KEY)
                self.assertIsNone(r["env"]["LITELLM_API_KEY"])
                self.assertEqual(self.rules(r)[:2], [("*", "*", "deny"), ("read", "*", "allow")])
        self.assertFalse([x for _, x, _ in self.rules(runs[0])[2:] if "*" in x])
        self.assertIn(ADDITION, git("show", "--name-only", "--format=", "HEAD", cwd=self.repo).splitlines())

    def test_a_no_runs_nothing(self):
        self.assertEqual(self.trusted({RUN_ADAPTER: [WRITE_NATIVE]}, answer="n"), 3, self.err + self.out)
        self.assertIn("adapt: stopped no --trust-gateway yes: no agent ran", self.out)
        self.assertEqual(self.e.agent_runs(), [])

    def test_without_a_terminal_the_flag_is_the_yes(self):
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
        r = self.e.run(str(self.repo), *ALL_OPENCODE, "--trust-gateway", "--no-model-check", adapt=True)
        self.assertEqual(r.returncode, 3, r.stderr + r.stdout)  # the commit still needs the owner's yes
        self.assertIn("WARNING (--trust-gateway)", r.stdout)
        self.assertIn("trust-gateway: yes --trust-gateway, and no terminal (stdin and stdout) to ask in", r.stdout)
        self.assertEqual([Path(x["cwd"]).resolve() for x in self.e.agent_runs()], [self.repo.resolve()] * 2)
        # and without the flag, the same run is in a copy
        self.e.set_state(agent_steps={RUN_REVIEWER: [PASS]})
        r = self.e.run(str(self.repo), *ALL_OPENCODE, "--no-model-check", adapt=True)
        self.assertNotIn("WARNING (--trust-gateway)", r.stdout)
        self.assertNotEqual(Path(self.e.agent_runs()[-1]["cwd"]).resolve(), self.repo.resolve())

    def test_any_write_but_the_adapters_own_files_stops_the_run(self):
        for i, (step, named) in enumerate((({"write": {ADDITION: "x\n", "AGENTS.md": "rewritten\n"}}, "AGENTS.md"),
                            ({"write": {ADDITION: "x\n", ".git/config": "[core]\n\tfsmonitor = touch PWNED\n"}},
                             ".git/config"),
                            ({"write": {".git/hooks/pre-commit": "touch PWNED\n"}}, ".git/hooks/pre-commit"),
                            ({"write": {".lane-control/.hidden.md": "x\n"}}, ".lane-control/.hidden.md"),
                            ({"write": {".lane-control/notes/x.md": "x\n"}}, ".lane-control/notes"),
                            ({"link": {".lane-control/project.md": "/etc/hostname"}}, ".lane-control/project.md (a link)"),
                            ({"write": {ADDITION: "x\n"}, "remove": ["tracked.md"]}, "tracked.md (removed)"),
                            ({"write": {"node_modules/x.js": "x\n"}}, "node_modules"))):
            with self.subTest(named=named):
                self.repo = self.e.repo(f"app{i}")  # a fresh repo each: what a refused run wrote stays in its repo
                (self.repo / "tracked.md").write_text("t\n")
                git("add", "tracked.md", cwd=self.repo)
                git("commit", "-qm", "t", cwd=self.repo)
                self.assertEqual(self.trusted({RUN_ADAPTER: [{**step, "steps": 1, "say": "done"}]}), 1, self.err)
                self.assertIn(f"the OpenCode adapting agent changed ", self.err)
                self.assertIn(named, self.err)
                self.assertIn("in the repo (--trust-gateway), where it may not write", self.err)
                self.assertIn("Setup stopped: nothing was committed and no git command ran after it", self.err)
                self.assertNotIn("git status", self.err)  # B3: never sent to a git command in there first

    def test_the_reviewer_writes_nothing(self):
        self.assertEqual(self.trusted({RUN_ADAPTER: [WRITE_NATIVE],
                                       RUN_REVIEWER: [{"write": {ADDITION: "changed\n"}, **PASS}]}), 1, self.err)
        self.assertIn(f"the OpenCode reviewer changed {ADDITION} in the repo (--trust-gateway)", self.err)

    def test_a_link_on_the_way_to_the_adapters_files_stops_it_before_it_runs(self):
        outside = self.e.home / "outside"
        outside.mkdir()
        (self.repo / ".lane-control").mkdir()
        (self.repo / ".lane-control" / "additions").symlink_to(outside)
        self.assertEqual(self.trusted({RUN_ADAPTER: [WRITE_NATIVE]}), 1)
        self.assertIn(".lane-control/additions in the repo is a link or not a folder, so the OpenCode adapter cannot "
                      "run in the repo itself (--trust-gateway)", self.err)
        self.assertEqual(self.e.agent_runs(), [])
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_failed_run_still_has_its_writes_checked(self):
        self.assertEqual(self.trusted({RUN_ADAPTER: [{"write": {"AGENTS.md": "x\n"}, "error": "fell over", "exit": 1}]}),
                         1)
        self.assertIn("changed AGENTS.md in the repo (--trust-gateway)", self.err)

    def test_the_system_config_refusal_holds_in_trusted_mode_too(self):
        system = self.e.home / "etc-opencode"
        system.mkdir()
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE]})
        self.assertEqual(self.setup(*ALL_OPENCODE, "--trust-gateway",
                                    patch={"SYSTEM_CONFIG": {"linux": str(system), "darwin": str(system)}}), 1)
        self.assertIn(f"{system} exists", self.err)
        self.assertEqual(self.e.agent_runs(), [])

    def test_it_is_refused_where_no_onboarding_agent_runs_and_unused_on_claude(self):
        for other in ("--skip-adapt", "--refresh", "--show"):
            with self.subTest(other=other):
                r = self.e.run(str(self.repo), "--trust-gateway", other, adapt=True)
                self.assertEqual(r.returncode, 2)
                self.assertIn(f"no onboarding agent runs with {other}", r.stderr)
        self.opencode_agents({"sonnet": [WRITE_NATIVE], "opus": [PASS]}, claude=True)
        self.setup("--trust-gateway", "--no-start", answer="n")
        self.assertIn("trust-gateway: unused no onboarding agent runs on OpenCode", self.out)
        self.assertNotIn("WARNING (--trust-gateway)", self.out)

    def test_it_is_not_kept(self):
        self.assertEqual(self.trusted({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]}, answer="n"), 3)
        self.assertNotIn("trust", json.dumps(self.e.settings(self.repo)))


QUESTION = "## AGENTS.md edits you would make\n\n## Questions for the owner\n- Which branch is the default?\n"


class TrustedRefusalTests(OpenCodeCase):
    """Review round 8 (lc-8h1): a refused --trust-gateway write stops setup in every run before any commit (B1), no git
    command runs after it (B2), a later run looks at it before its first git command (B3), and no git command of
    setup's runs the repo's own programs (hooks, core.fsmonitor, filters) at all."""

    def planted(self, what: str) -> Path:
        """A program that leaves a mark (PLANTED-RAN, with `what`) whenever something runs it; a filter passes its input
        through."""
        f = self.e.home / f"planted-{what}.sh"
        f.write_text(f"#!/bin/sh\necho {what} >> {self.e.home / 'PLANTED-RAN'}\n"
                     + ("cat\nexit 0\n" if what == "filter" else "exit 1\n"))
        f.chmod(0o755)
        return f

    def evil_config(self):
        """.git/config as it is, with the model's core.fsmonitor added."""
        return (self.repo / ".git" / "config").read_text() + f"[core]\n\tfsmonitor = {self.planted('fsmonitor')}\n"

    def evil_hook(self):
        return f"#!/bin/sh\n{self.planted('hook')}\nexit 0\n"

    def assert_stopped(self, status, *named):
        self.assertEqual(status, 1, self.err + self.out)
        for n in named:
            self.assertIn(n, self.err)
        self.assertIn("Setup stopped: nothing was committed and no git command ran after it", self.err)
        self.assertEqual(git("log", "--format=%s", cwd=self.repo).splitlines(), ["init"])  # no onboarding commit
        self.assertFalse((self.e.home / "PLANTED-RAN").exists(), "something ran the model's hook or fsmonitor")
        self.assertTrue(self.e.store(self.repo).parent.parent.joinpath("refused").is_dir())

    def test_the_git_commands_of_setup_run_none_of_the_repos_own_programs(self):
        # (1): a hook, a core.fsmonitor and a clean filter already in the repo, then an ordinary onboarding that
        # commits: git status, add and commit all run, none of the three does
        mark = self.e.home / "PLANTED-RAN"
        with (self.repo / ".git" / "config").open("a") as f:
            f.write(f"[core]\n\tfsmonitor = {self.planted('fsmonitor')}\n"
                    f"[filter \"x\"]\n\tclean = {self.planted('filter')}\n")
        (self.repo / ".git" / "info").mkdir(exist_ok=True)
        (self.repo / ".git" / "info" / "attributes").write_text("*.md filter=x\n")
        (self.repo / ".git" / "hooks").mkdir(exist_ok=True)
        (self.repo / ".git" / "hooks" / "pre-commit").write_text(self.evil_hook())
        (self.repo / ".git" / "hooks" / "pre-commit").chmod(0o755)
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 0, self.err + self.out)
        self.assertIn(ADDITION, git("show", "--name-only", "--format=", "HEAD", cwd=self.repo).splitlines())
        self.assertFalse(mark.exists(), mark.read_text() if mark.exists() else "")
        # the control: the same repo's own git runs them
        (self.repo / "new.md").write_text("x\n")
        subprocess.run(["git", "add", "new.md"], cwd=self.repo, capture_output=True, stdin=subprocess.DEVNULL)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], cwd=self.repo,
                       capture_output=True, stdin=subprocess.DEVNULL)
        self.assertEqual(set(mark.read_text().split()), {"fsmonitor", "filter", "hook"})

    def test_the_safe_settings_reach_every_git_child_and_keep_the_owners_global_programs(self):
        m = load()
        (self.e.home / ".gitconfig").write_text("[core]\n\tsshCommand = ssh -i ~/.ssh/owner\n"
                                                "[credential]\n\thelper = store\n")
        with (self.repo / ".git" / "config").open("a") as f:
            f.write("[core]\n\tsshCommand = sh -c 'touch X'\n[credential]\n\thelper = !touch Y\n"
                    "[diff \"d\"]\n\ttextconv = touch Z\n[remote \"origin\"]\n\treceivepack = touch W\n")
        with mock.patch.dict(os.environ, {**self.e.env, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.name",
                                          "GIT_CONFIG_VALUE_0": "Owner"}, clear=True):
            os.environ.pop("GIT_SSH_COMMAND", None)
            m.guard_git()
            try:
                p = subprocess.run(["git", "config", "--list"], cwd=self.repo, capture_output=True, text=True)
            finally:
                subprocess.Popen = subprocess._lane_real_popen
        conf = [l for l in p.stdout.splitlines()]
        last = {}
        for l in conf:
            k, _, v = l.partition("=")
            last.setdefault(k, []).append(v)
        self.assertEqual(last["core.hookspath"][-1], "/dev/null")
        self.assertEqual(last["core.fsmonitor"][-1], "false")
        self.assertEqual(last["core.sshcommand"][-1], "ssh -i ~/.ssh/owner")  # the owner's global one, not the repo's
        self.assertEqual(last["credential.helper"][-2:], ["", "store"])  # the list reset, then the owner's helpers
        self.assertEqual(last["diff.d.textconv"][-1], "")
        self.assertEqual(last["remote.origin.receivepack"][-1], "git-receive-pack")
        self.assertEqual(last["user.name"][-1], "Owner")  # the caller's own GIT_CONFIG_COUNT pairs are kept

    def test_after_a_refusal_no_git_command_starts_at_all(self):
        # B2, the second layer: whatever is left on the way out (a cleanup, a finally) cannot start git
        m = load()
        with mock.patch.dict(os.environ, self.e.env, clear=True):
            m.guard_git()
            try:
                m.REFUSED.append(m.RepoWrite("the model wrote .git/config"))
                with self.assertRaisesRegex(m.RepoWrite, "the model wrote .git/config"):
                    m.run(["git", "status"], cwd=self.repo)
                self.assertEqual(subprocess.run(["true"]).returncode, 0)  # anything else still runs
            finally:
                subprocess.Popen = subprocess._lane_real_popen

    def test_a_refused_write_in_the_answers_run_stops_before_the_commit(self):
        # B1: the answers run took a failed run in its stride; a refusal is no failed run
        first = {**WRITE_NATIVE, "write": {**WRITE_NATIVE["write"], ".lane-control/SUGGESTIONS.md": QUESTION}}
        answers_run = {"write": {".git/config": self.evil_config(), ".git/hooks/pre-commit": self.evil_hook()},
                       "steps": 1, "say": "done"}
        self.opencode_agents({RUN_ADAPTER: [first, answers_run], RUN_REVIEWER: [PASS]})
        status = self.setup(*ALL_OPENCODE, "--trust-gateway", answers=["y", "main", "y", "y", "y", "y"])
        self.assert_stopped(status, ".git/config", ".git/hooks/pre-commit")
        self.assertEqual(len(self.e.agent_runs()), 3)  # the adapter, the review, the answers run: nothing after it
        self.assertFalse([q for q in self.questions if "Commit these files" in q])

    def test_a_refused_write_in_a_fix_check_review_stops_before_the_commit(self):
        # B1: a fix-check review that "failed" was taken for one with no verdict, and setup went on
        fix = {"say": "project.md names the wrong command.\nVERDICT: FIX"}
        check = {"write": {".git/config": self.evil_config()}, "steps": 1, "say": "VERDICT: PASS"}
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE, {"write": {".lane-control/project.md": "fixed\n"}}],
                              RUN_REVIEWER: [fix, check]})
        self.assert_stopped(self.setup(*ALL_OPENCODE, "--trust-gateway"), "the OpenCode reviewer changed .git/config")
        self.assertEqual([r["model"] for r in self.e.agent_runs()], [RUN_ADAPTER, RUN_REVIEWER, RUN_ADAPTER, RUN_REVIEWER])

    def test_the_copy_modes_refusal_in_a_fix_check_review_stops_too(self):
        fix = {"say": "project.md names the wrong command.\nVERDICT: FIX"}
        check = {"write": {ADDITION: "changed\n"}, "steps": 1, "say": "VERDICT: PASS"}
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE, {"write": {".lane-control/project.md": "fixed\n"}}],
                              RUN_REVIEWER: [fix, check]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 1, self.err + self.out)
        self.assertIn(f"the OpenCode reviewer changed {ADDITION} in its copy", self.err)
        self.assertEqual(git("log", "--format=%s", cwd=self.repo).splitlines(), ["init"])

    def test_no_git_command_runs_after_a_refusal_not_even_the_cleanup(self):
        # B2: adapt()'s cleanup asked git whether a role file was tracked, and git ran the model's core.fsmonitor
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
        self.assertEqual(self.setup(*ALL_OPENCODE), 0, self.err + self.out)  # rendered personas exist now
        self.e.set_state(agent_steps={RUN_ADAPTER: [{"write": {".git/config": self.evil_config(),
                                                              ".lane-control/coordinator.md": "## mine\n"},
                                                    "steps": 1, "say": "done"}]})
        status = self.setup("--readapt", "--trust-gateway")
        self.assertEqual(status, 1, self.err)
        self.assertIn("changed .git/config in the repo (--trust-gateway)", self.err)
        self.assertFalse((self.e.home / "PLANTED-RAN").exists())

    def test_a_later_run_looks_at_a_refusal_before_its_first_git_command(self):
        # B3 (a): the .git/config write alone; the re-run runs nothing until the owner has looked
        self.opencode_agents({RUN_ADAPTER: [{"write": {".git/config": self.evil_config()}, "steps": 1, "say": "x"}]})
        self.assert_stopped(self.setup(*ALL_OPENCODE, "--trust-gateway"), ".git/config")
        r = self.e.run(str(self.repo), "--no-model-check", adapt=True)  # a plain re-run, no terminal
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("a --trust-gateway run there was refused for writes it may not make", r.stdout + r.stderr)
        self.assertIn("  .git/config", r.stdout)
        self.assertIn("read these in an editor", r.stdout)
        self.assertIn("nothing ran and nothing was committed", r.stderr)
        self.assertNotIn("repo: exists", r.stdout)  # it stopped before anything else
        self.assertFalse((self.e.home / "PLANTED-RAN").exists())
        self.assertEqual(len(self.e.agent_runs()), 1)
        # a no in a terminal is the same; a yes goes on
        self.e.set_state(agent_steps={RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
        self.assertEqual(self.setup(*ALL_OPENCODE, answer="n"), 1)
        self.assertTrue([q for q in self.questions if "Have you read each of them by hand" in q])
        self.assertEqual(self.setup(*ALL_OPENCODE, answers=["y", "n"]), 3, self.err + self.out)  # the yes, then a no
        self.assertFalse(list((self.e.store(self.repo).parent.parent / "refused").iterdir()))

    def test_a_refusal_whose_paths_are_back_as_they_were_clears_itself(self):
        config = (self.repo / ".git" / "config").read_text()
        self.opencode_agents({RUN_ADAPTER: [{"write": {".git/config": self.evil_config(), "stray.md": "x\n"},
                                             "steps": 1, "say": "x"}]})
        self.assert_stopped(self.setup(*ALL_OPENCODE, "--trust-gateway"), ".git/config", "stray.md")
        (self.repo / ".git" / "config").write_text(config)  # the owner puts it back, in an editor
        (self.repo / "stray.md").unlink()
        self.e.set_state(agent_steps={RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
        self.assertEqual(self.setup(*ALL_OPENCODE, answer="n"), 3, self.err + self.out)
        self.assertIn("trust-gateway: cleared", self.out)

    def test_a_reviewers_refused_file_is_never_committed_by_a_rerun(self):
        # B3 (c): a re-run took the reviewer's file for the adapter's work and committed it
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE],
                              RUN_REVIEWER: [{"write": {".lane-control/zz-by-reviewer.md": "x\n"}, "steps": 1,
                                              "say": "VERDICT: PASS"}]})
        self.assert_stopped(self.setup(*ALL_OPENCODE, "--trust-gateway"), ".lane-control/zz-by-reviewer.md")
        self.e.set_state(agent_steps={RUN_REVIEWER: [PASS]})
        self.assertEqual(self.setup(*ALL_OPENCODE, answer="n"), 1)  # asked, and a no
        self.assertEqual(git("log", "--format=%s", cwd=self.repo).splitlines(), ["init"])

    def test_a_run_that_stopped_before_its_check_is_looked_at_too(self):
        # B3, read only: a kill before the check; and Ctrl-C now runs the check on its way out
        m = load()
        with mock.patch.dict(os.environ, self.e.env, clear=True):
            f = m.refusal_file(self.repo)
            self.assertTrue(f.is_relative_to(self.e.home))
            m.keep_refusal(self.repo, {}, True)
        r = self.e.run(str(self.repo), "--no-model-check", adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("a --trust-gateway run there stopped before its writes were checked", r.stdout + r.stderr)
        self.assertIn(".git/config, .git/config.worktree, .git/hooks/ and .git/info/", r.stdout)
        f.unlink()

        def interrupted(cmd, **kw):
            if cmd[0] == "git":
                return real(cmd, **kw)
            (self.repo / ".git" / "config").write_text(self.evil_config())
            raise KeyboardInterrupt
        real = m.run
        self.e.env["LITELLM_API_KEY"] = GATEWAY_KEY
        with mock.patch.dict(os.environ, self.e.env, clear=True), mock.patch.object(m, "run", interrupted):
            m.GATEWAY.update(url=GATEWAY_URL, key_env="LITELLM_API_KEY")
            with self.assertRaisesRegex(m.RepoWrite, r"changed \.git/config"):
                m.run_opencode_in_repo(self.repo, "litellm/code", "x", True)
        self.assertTrue(f.is_file())


class RepoStateTests(Case):
    """M1, m1: what the --trust-gateway before/after check sees, with real git."""

    def changed(self, repo, act, write=True):
        m = load()
        with mock.patch.dict(os.environ, self.e.env, clear=True):  # the refusal record goes under the temp HOME
            dirs = m.git_dirs(repo)
            before = m.repo_state(repo, dirs)
            act()
            try:
                m.check_repo_writes(repo, before, m.repo_state(repo, dirs), write)
            except m.RepoWrite as e:
                self.assertTrue(m.refusal_file(repo).is_relative_to(self.e.home))
                return str(e)
        return None

    def test_a_linked_worktrees_shared_config_and_hooks_are_seen(self):
        wt = self.e.home / "wt"
        git("worktree", "add", "-q", str(wt), "-b", "lane", cwd=self.repo)
        common = self.repo / ".git"
        said = self.changed(wt, lambda: (common / "config").write_text((common / "config").read_text() + "[x]\n\ty=1\n"))
        self.assertIn(str(common / "config"), said)
        said = self.changed(wt, lambda: (common / "hooks" / "post-checkout").write_text("#!/bin/sh\n"))
        self.assertIn(str(common / "hooks" / "post-checkout"), said)
        said = self.changed(wt, lambda: (common / "worktrees" / "wt" / "config.worktree").write_text("[x]\n"))
        self.assertIn("config.worktree", said)

    def test_names_like_logs_or_refs_hide_nothing_outside_gits_own_folders(self):
        nested = self.repo / "web" / "logs"
        nested.mkdir(parents=True)
        git("init", "-q", cwd=nested)
        self.assertIn("web/logs/.git/config",
                      self.changed(self.repo, lambda: (nested / ".git" / "config").write_text("[core]\n\tx=1\n")))
        sub = self.repo / ".git" / "modules" / "refs"
        sub.mkdir(parents=True)
        git("init", "-q", "--bare", str(sub), cwd=self.repo)
        self.assertIn(".git/modules/refs/hooks/post-checkout",
                      self.changed(self.repo, lambda: (sub / "hooks" / "post-checkout").write_text("#!/bin/sh\n")))
        info = self.repo / ".git" / "objects" / "info"
        info.mkdir(parents=True, exist_ok=True)
        self.assertIn(".git/objects/info/alternates",
                      self.changed(self.repo, lambda: (info / "alternates").write_text("/elsewhere\n")))

    def test_gits_own_churn_and_another_worktrees_commit_are_not_the_models(self):
        # m1: a lane committing in another worktree during the run
        wt = self.e.home / "wt"
        git("worktree", "add", "-q", str(wt), "-b", "lane", cwd=self.repo)
        self.assertIsNone(self.changed(self.repo, lambda: (git("commit", "-q", "--allow-empty", "-m", "lane", cwd=wt),
                                                          git("status", cwd=self.repo),
                                                          git("branch", "other", cwd=self.repo))))
        # the adapter's own files and their folders are its to write
        m = load()
        self.assertIsNone(self.changed(self.repo, lambda: ((self.repo / ".lane-control" / "additions").mkdir(parents=True),
                                                          (self.repo / m.ADDITIONS / "coordinator.md").write_text("x"))))


class SkipAdaptRouteTests(OpenCodeCase):
    """The third route for a new project on a machine without a usable model: --skip-adapt sets it up (Orca, the
    coordinator), and --readapt adapts it later."""

    def test_a_new_project_set_up_with_skip_adapt_works_and_is_adapted_later(self):
        (self.e.bin / "claude").unlink(missing_ok=True)  # no Claude, and no gateway the onboarding agents could use
        r = self.e.run(str(self.repo), "--coordinator", "opencode/litellm/chat", "--reviewer", "opencode/litellm/deep",
                       "--no-model-check")  # e.run adds --skip-adapt
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("adapt: skipped --skip-adapt: no onboarding agent ran, so the project's own files are not "
                      f"written: no {GENERATED}, nothing under .lane-control/ but the rendered personas", r.stdout)
        self.assertIn("--readapt", r.stdout)
        self.assertTrue([c for c in self.e.calls() if c.startswith("orca repo add")], self.e.calls())  # Orca setup
        s = [x for x in self.e.sessions() if x["role"] == "coordinator"]
        self.assertEqual([x["bin"] for x in s], ["opencode2"])  # the coordinator started
        kickoff = opt(s[0]["argv"], "--prompt")  # m2: told the project is not adapted, never sent to a missing file
        self.assertIn("This project is not adapted to lane-control yet", kickoff)
        self.assertNotIn("then AGENTS.md and AGENTS.lane-control.md", kickoff)
        self.assertFalse((self.repo / ADDITION).exists())
        self.assertEqual(self.e.agent_runs(), [])
        # later, with a model at hand: --readapt adapts it, as a first onboarding would
        self.opencode_agents({RUN_ADAPTER: [WRITE_NATIVE], RUN_REVIEWER: [PASS]})
        self.assertEqual(self.setup("--readapt", "--adapter", f"opencode/{OC_ADAPTER}", "--adapt-reviewer",
                                    f"opencode/{OC_REVIEWER}", *GATEWAY_FLAGS, "--no-start"), 0, self.err + self.out)
        self.assertEqual([r["model"] for r in self.e.agent_runs()], [RUN_ADAPTER, RUN_REVIEWER])
        committed = git("show", "--name-only", "--format=", "HEAD", cwd=self.repo).splitlines()
        self.assertIn(ADDITION, committed)
        self.assertIn(GENERATED, committed)
        self.assertNotIn("adapt: skipped", self.out)


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
        # B11, m19: everything not for the local fake model goes to a proxy that logs it and lets nothing through
        self.proxy_log = self.t / "proxy.log"
        self.proxy_log.write_text("")
        proxy = subprocess.Popen([sys.executable, str(ROOT / "tests" / "stubs" / "logging_proxy.py"),
                                  str(self.proxy_log)], stdout=subprocess.PIPE, text=True)
        self.addCleanup(proxy.stdout.close)
        self.addCleanup(proxy.wait, 15)
        self.addCleanup(proxy.terminate)
        url = f"http://127.0.0.1:{int(proxy.stdout.readline())}"
        self.proxy_env = {"HTTPS_PROXY": url, "HTTP_PROXY": url, "https_proxy": url, "http_proxy": url,
                          "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
        copied = self.m.export_files(self.repo)
        for n in (".opencode/plugins/p.js", "opencode.json", "instr.md"):  # M3: so the guard is what keeps them out
            self.assertIn(n, copied)

    def call(self, tool: str, args: dict, write: bool, cwd: "Path | None" = None, tmpdir: "Path | None" = None,
             trusted: bool = False, model: str = "lanetest/m"):
        """One onboarding run whose model asks for `tool` with `args`; returns (tool result, run text). Fails when any
        marker reached the model, unless `trusted` (--trust-gateway and its yes: the repo itself, every file
        readable)."""
        self.m.TRUSTED.clear()
        self.m.REFUSED.clear()
        if trusted:
            self.m.TRUSTED.add(self.repo.resolve())
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
            # the owner's own block for the same provider id points elsewhere: never read for onboarding
            "provider": {"lanetest": {"npm": "@ai-sdk/openai", "api": "https://api.openai.com/v1",
                                      "options": {"baseURL": "https://elsewhere.example/v1", "apiKey": "sk-literal"}}}}))
        self.m.GATEWAY.clear()
        self.m.GATEWAY.update(url=f"http://127.0.0.1:{port}/v1", key_env="LANETEST_KEY")
        (self.t / "config" / "opencode" / "plugins").mkdir(exist_ok=True)  # an owner plugin, never loaded
        (self.t / "config" / "opencode" / "plugins" / "o.js").write_text(
            f'import fs from "fs"\nfs.writeFileSync({json.dumps(str(self.t / "owner-plugin-ran"))}, "x")\n'
            'export default { id: "o", setup() {} }\n')
        owner = {"agents": {"lane-onboard": {"mode": "primary", "permission": {"*": "allow"}}}}  # m7
        env = {"PATH": os.environ["PATH"], "HOME": str(self.t / "home"), "XDG_CONFIG_HOME": str(self.t / "config"),
               "XDG_DATA_HOME": str(self.t / "data"), "XDG_STATE_HOME": str(self.t / "state"),
               "XDG_CACHE_HOME": str(self.t / "cache"), "OPENCODE_DISABLE_MODELS_FETCH": "1",
               "OPENCODE_DISABLE_AUTOUPDATE": "1", "LANE_ROLE": "coordinator", "LANETEST_KEY": "unused",
               "OPENCODE_CONFIG_CONTENT": json.dumps(owner), "TMPDIR": str(tmpdir or self.t), **self.proxy_env}
        here = os.getcwd()
        try:
            os.chdir(cwd or self.t)
            with mock.patch.dict(os.environ, env, clear=True):
                p = self.m.run_opencode(self.repo, model, "- RUN-TOOL check", write)
        finally:
            os.chdir(here)
        text, _, error = self.m.opencode_result(p.stdout)
        self.assertEqual((p.returncode, error), (0, ""), p.stderr[-2000:])
        self.assertEqual(self.proxy_log.read_text(), "", "the run reached past the gateway")  # B11, m19
        body = log.read_text()
        leaked = {m: path for m, path in self.secrets.items() if m in body}
        if not trusted:
            self.assertEqual(leaked, {}, f"{tool} {args}: private content reached the model")
        results = [m["content"] for line in body.splitlines()
                   for m in json.loads(json.loads(line)["body"]).get("messages", []) if m.get("role") == "tool"]
        self.assertFalse((self.t / "plugin-ran").exists())  # the project's own plugin never ran
        self.assertFalse((self.t / "mcp-ran").exists())  # nor its MCP server
        for ran in ("formatter-ran", "owner-mcp-ran", "owner-plugin-ran"):  # B10: nothing of the owner's either
            self.assertFalse((self.t / ran).exists(), ran)
        return (str(results[-1]) if results else ""), text

    def gateway_run(self, model: str, key_env: str = "GW_KEY"):
        """One reviewer run for `model` (opencode/<provider>/<model> without the opencode/) against the local fake as
        the given gateway, its key in the variable `key_env`; returns (requests the gateway got, what the proxy saw).
        self.models: the model each request named."""
        log = self.t / "gateway.jsonl"
        log.write_text("")
        fake = subprocess.Popen([sys.executable, str(ROOT / "tests" / "stubs" / "fake_tool_llm.py"), str(log)],
                                stdout=subprocess.PIPE, text=True, env={**os.environ, "LANE_FAKE_CHAT_ONLY": "1"})
        self.addCleanup(fake.stdout.close)
        self.addCleanup(fake.wait, 15)
        self.addCleanup(fake.terminate)
        port = int(fake.stdout.readline())
        self.m.GATEWAY.clear()
        self.m.GATEWAY.update(url=f"http://127.0.0.1:{port}/v1", key_env=key_env)
        env = {"PATH": os.environ["PATH"], "HOME": str(self.t / "home"), "XDG_CONFIG_HOME": str(self.t / "config"),
               key_env: "dummy-gateway-key", "TMPDIR": str(self.t), **self.proxy_env}
        self.proxy_log.write_text("")
        with mock.patch.dict(os.environ, env, clear=True):
            self.m.run_opencode(self.repo, model, "RUN-TOOL check", False)
        self.models = [json.loads(json.loads(l)["body"] or "{}").get("model") for l in log.read_text().splitlines()]
        return log.read_text().count("\n"), self.proxy_log.read_text()

    @contextlib.contextmanager
    def run_config(self, change):
        """A negative control: the run's config file changed by `change(conf)` before OpenCode reads it."""
        real = self.m.opencode_agent

        @contextlib.contextmanager
        def changed(*a, **kw):
            with real(*a, **kw) as (cmd, env):
                f = Path(env["XDG_CONFIG_HOME"]) / "opencode" / "opencode.json"
                conf = json.loads(f.read_text())
                change(conf)
                f.write_text(json.dumps(conf))
                yield cmd, env
        with mock.patch.object(self.m, "opencode_agent", changed):
            yield

    def test_a_key_variable_opencode_reads_itself_never_reaches_it(self):
        # B15: OpenCode reads OPENCODE_API_KEY as its own service's key and sends it to opencode.ai; the run gets the
        # key under LANE_GATEWAY_KEY only, so a gateway key kept in that variable still goes to the gateway alone
        got, past = self.gateway_run("litellm/m", key_env="OPENCODE_API_KEY")
        self.assertEqual(past, "", "a request went past the given gateway")
        self.assertGreater(got, 0)
        # negative control: handed over under the owner's name, the proxy sees opencode.ai
        with mock.patch.object(self.m, "RUN_KEY_ENV", "OPENCODE_API_KEY"):
            _, past = self.gateway_run("litellm/m", key_env="OPENCODE_API_KEY")
        self.assertIn("opencode.ai", past)

    def test_no_side_request_names_another_model_whatever_the_provider_id(self):
        # m27: under a built-in provider id OpenCode's title request went to a model of its own (gpt-6-luna);
        # the run's provider id is lane-gateway and its title agent takes the run's model
        for model in ("opencode/m", "github-copilot/m", "opencode-go/m", "cloudflare-ai-gateway/m", "litellm/m"):
            with self.subTest(model=model):
                got, past = self.gateway_run(model)
                self.assertEqual(past, "")
                self.assertGreater(got, 0)
                self.assertEqual(set(self.models), {"m"}, self.models)
        # negative control: under the given provider id and with no title model, another model is asked

        def owner_id(conf):
            conf["provider"] = {"opencode": conf["provider"]["lane-gateway"]}
            for a in conf["agents"].values():
                a["model"] = "opencode/m"
            del conf["agents"]["title"]
        with self.run_config(owner_id):
            self.gateway_run("opencode/m")
        self.assertNotEqual(set(self.models), {"m"}, self.models)

    def test_only_the_given_gateway_is_contacted_whatever_the_provider_id(self):
        # round 6: the run's config is written from --gateway-url alone; a vendor-named provider id (whose built-in
        # SDK default is the vendor's API) and the owner's own block for it (pointing elsewhere) change nothing
        (self.t / "config" / "opencode" / "opencode.json").write_text(json.dumps({"provider": {
            "openai": {"npm": "@ai-sdk/openai", "options": {"baseURL": "https://api.openai.com/v1"}},
            "anthropic": {"npm": "@ai-sdk/anthropic", "api": "https://api.anthropic.com/v1"}}}))
        for model in ("openai/gpt-x", "anthropic/claude-x", "litellm/code"):
            with self.subTest(model=model):
                got, past = self.gateway_run(model)
                self.assertEqual(past, "", f"{model}: a request went past the given gateway")
                self.assertGreater(got, 0, f"{model}: the gateway got nothing")

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

    def test_a_leak_in_the_copy_would_be_caught(self):
        # negative control of the scratch copy's checks: with the --private filter off, a tracked private file is in
        # the copy, its marker reaches the model and call() says so
        with mock.patch.object(self.m, "export_private", lambda rel: False), \
                self.assertRaisesRegex(AssertionError, "private content reached the model"):
            self.call("read", {"path": "secrets/a.md"}, write=False)

    def test_trusted_the_agents_read_the_repo_itself_and_nothing_goes_past_the_gateway(self):
        # --trust-gateway: no copy, no filter; still nothing but the gateway is reached (call() checks the proxy)
        got, _ = self.call("read", {"path": ".env"}, write=False, trusted=True)
        self.assertIn("LIVE-SECRET", got)  # what the warning says: every file in the folder
        got, _ = self.call("read", {"path": "data/x.csv"}, write=False, trusted=True)
        self.assertIn("LIVE-SECRET", got)
        self.assertIn("root rules readable", self.call("read", {"path": "AGENTS.md"}, write=False, trusted=True)[0])
        for ran in ("plugin-ran", "mcp-ran", "formatter-ran", "owner-mcp-ran", "owner-plugin-ran"):
            self.assertFalse((self.t / ran).exists(), ran)

    def test_trusted_the_adapter_writes_only_its_files_and_opencode_writes_nothing_else(self):
        # the repo's state before and after finds nothing OpenCode itself wrote (.git included) on a plain run
        self.call("write", {"path": ".lane-control/project.md", "content": "facts"}, write=True, trusted=True)
        self.assertEqual((self.repo / ".lane-control" / "project.md").read_text(), "facts")
        for path in ("AGENTS.md", ".git/config", ".lane-control/.git/config", ".lane-control/notes.md",
                     ".lane-control/.hidden.md", "prettier.config.mjs"):
            with self.subTest(path=path):
                before = (self.repo / path).read_bytes() if (self.repo / path).exists() else None
                got, _ = self.call("write", {"path": path, "content": "[core]\n\tfsmonitor = touch PWNED\n"},
                                   write=True, trusted=True)
                self.assertIn("Permission denied", got)
                self.assertEqual((self.repo / path).read_bytes() if (self.repo / path).exists() else None, before)
        got, _ = self.call("write", {"path": ".lane-control/project.md", "content": "x"}, write=False, trusted=True)
        self.assertIn('No tool named \\"write\\"', got)  # the reviewer has no write tool at all
        self.assertEqual((self.repo / ".lane-control" / "project.md").read_text(), "facts")
        # negative control: with edit allowed everywhere, the write lands and the before/after check stops the run
        wide = lambda write: [{"action": "*", "resource": "*", "effect": "allow"}]
        with mock.patch.object(self.m, "opencode_rules", wide), \
                self.assertRaisesRegex(self.m.RepoWrite, r"changed AGENTS\.md in the repo \(--trust-gateway\)"):
            self.call("write", {"path": "AGENTS.md", "content": "changed"}, write=True, trusted=True)
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "changed")

    PATCHES = {  # m3: OpenCode offers `patch` in place of write/edit for a gpt-* alias; each is refused by its rules
        "move onto a hook": "*** Begin Patch\n*** Update File: .lane-control/project.md\n*** Move to: .git/hooks/pre-commit\n"
                            "@@\n-seed\n+#!/bin/sh\n*** End Patch",
        "move onto AGENTS.md": "*** Begin Patch\n*** Update File: .lane-control/project.md\n*** Move to: AGENTS.md\n"
                               "@@\n-seed\n+x\n*** End Patch",
        "add a hook": "*** Begin Patch\n*** Add File: .git/hooks/pre-commit\n+#!/bin/sh\n*** End Patch",
        "add .git/config": "*** Begin Patch\n*** Add File: .git/config\n+[core]\n*** End Patch",
        "add .envrc": "*** Begin Patch\n*** Add File: .envrc\n+touch PWNED\n*** End Patch",
        "add outside": "*** Begin Patch\n*** Add File: ../outside-new.md\n+x\n*** End Patch",
        "delete AGENTS.md": "*** Begin Patch\n*** Delete File: AGENTS.md\n*** End Patch",
        "update AGENTS.md": "*** Begin Patch\n*** Update File: AGENTS.md\n@@\n-root rules readable\n+changed\n*** End Patch",
    }

    def seed(self):
        (self.repo / ".lane-control").mkdir(exist_ok=True)
        (self.repo / ".lane-control" / "project.md").write_text("seed\n")

    def assert_patches_refused(self, trusted: bool):
        for name, text in self.PATCHES.items():
            with self.subTest(patch=name, trusted=trusted):
                self.seed()
                agents = (self.repo / "AGENTS.md").read_bytes()
                hooks = sorted(os.listdir(self.repo / ".git" / "hooks"))
                got, _ = self.call("patch", {"patchText": text}, write=True, trusted=trusted, model="lanetest/gpt-5.2")
                self.assertIn("Permission denied", got)  # the patch tool itself answered, and refused
                self.assertEqual((self.repo / "AGENTS.md").read_bytes(), agents, got)
                self.assertEqual(sorted(os.listdir(self.repo / ".git" / "hooks")), hooks, got)
                self.assertFalse((self.repo / ".envrc").exists(), got)
                self.assertFalse((self.t / "outside-new.md").exists(), got)
                self.assertEqual((self.repo / ".lane-control" / "project.md").read_text(), "seed\n", got)

    def test_the_patch_tool_is_held_by_the_rules_in_the_copy(self):
        self.assert_patches_refused(trusted=False)

    def test_the_patch_tool_is_held_by_the_rules_in_the_repo_and_a_delete_it_lets_through_is_stopped(self):
        self.assert_patches_refused(trusted=True)
        self.seed()  # the one patch the rules let through, a Delete File of an allowed path: the check stops it
        with self.assertRaisesRegex(self.m.RepoWrite, r"changed \.lane-control/project\.md \(removed\)"):
            self.call("patch", {"patchText": "*** Begin Patch\n*** Delete File: .lane-control/project.md\n*** End Patch"},
                      write=True, trusted=True, model="lanetest/gpt-5.2")
        self.seed()
        self.call("patch", {"patchText": "*** Begin Patch\n*** Update File: .lane-control/project.md\n@@\n-seed\n+facts\n"
                                         "*** End Patch"}, write=True, trusted=True, model="lanetest/gpt-5.2")
        self.assertEqual((self.repo / ".lane-control" / "project.md").read_text(), "facts\n")  # its own file: fine

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
        agent = self.e.proc / "4001"  # its agent runs: the title and Orca's mark alone are not enough (lc-l78.1)
        agent.mkdir()
        (agent / "cmdline").write_bytes(b"claude\0")
        (agent / "environ").write_bytes(b"LANE_ROLE=coordinator\0")
        (agent / "cwd").symlink_to(self.repo)
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
            self.assertIn('p="$d/current/scripts/project-setup"', calls[1])
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
        # its real /proc entry, and only the ones a test started: never this machine's other agents
        (self.e.proc / str(p.pid)).symlink_to(f"/proc/{p.pid}")
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


class InstallTests(Case):
    """INST-1 (lc-7gk): project-setup installs this checkout's HEAD as the machine's tree and names its stable path."""

    def lc(self):
        return self.e.home / ".local" / "share" / "lane-control"

    def lane(self, role):
        return self.repo / ".lane-control" / f"{role}.md"

    def head(self):
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()

    def test_the_persona_names_the_installed_current_path_not_the_checkout(self):
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn(f"lane-control: installed {self.head()} at {self.lc() / 'current'}", r.stdout)
        text = self.lane("coordinator").read_text()
        self.assertIn(str(self.lc() / "current"), text)
        self.assertNotIn(str(ROOT), text)
        self.assertEqual(os.readlink(self.lc() / "current"), self.head())
        tool = self.e.home / ".agents" / "bin" / "lane-heavy"
        self.assertTrue(tool.is_symlink())
        self.assertEqual(os.readlink(tool), str(self.lc() / "current" / "core" / "scripts" / "lane-heavy"))
        r = self.e.run(str(self.repo), "--no-start")
        self.assertIn(f"lane-control: exists {self.head()}", r.stdout)

    def test_the_sha_is_recorded_and_show_names_a_newer_install(self):
        self.e.run(str(self.repo), "--no-start")
        st = json.loads((self.e.store(self.repo) / "settings.json").read_text())
        self.assertEqual(st["lane_control"], self.head())
        r = self.e.run(str(self.repo), "--show", adapt=True)
        self.assertNotIn("run project-setup --refresh", r.stdout)
        other = "f" * 40  # a later install: its tree and the switched link
        (self.lc() / other).mkdir()
        (self.lc() / other / ".lane-control-sha").write_text(other + "\n")
        (self.lc() / "current").unlink()
        (self.lc() / "current").symlink_to(other)
        r = self.e.run(str(self.repo), "--show", adapt=True)
        self.assertIn(f"lane-control {other[:12]} installed, project set up with {self.head()[:12]}: "
                      "run project-setup --refresh", r.stdout)

    def test_a_machine_set_up_before_moves_to_the_install_with_one_line(self):
        self.e.run(str(self.repo), "--no-start")
        shutil.rmtree(self.lc())  # as before INST-1: copies in ~/.agents/bin, no install, no sha in the store
        tool = self.e.home / ".agents" / "bin" / "lane-heavy"
        tool.unlink()
        tool.write_text("#!/bin/sh\necho copy\n")
        f = self.e.store(self.repo) / "settings.json"
        st = json.loads(f.read_text())
        st.pop("lane_control")
        f.write_text(json.dumps(st))
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("this machine moves to the install", r.stderr)
        self.assertIn("lane-control: moved", r.stdout)
        self.assertTrue(tool.is_symlink())
        self.assertIn(str(self.lc() / "current"), self.lane("coordinator").read_text())

    def test_the_persona_hook_names_current_not_the_checkout(self):
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        hook = (self.repo / ".claude" / "settings.local.json").read_text()  # INST-1 review B1
        self.assertIn(str(self.lc() / "current" / "core" / "harnesses" / "persona-hook.sh"), hook)
        self.assertNotIn(str(ROOT), hook)

    def test_a_failed_install_exits_1_and_changes_nothing(self):
        self.e.run(str(self.repo), "--no-start")
        other = "e" * 40  # the machine is on another install; this run would switch, but tar fails
        shutil.rmtree(self.lc() / self.head())
        (self.lc() / other).mkdir()
        (self.lc() / other / ".lane-control-sha").write_text(other + "\n")
        (self.lc() / "current").unlink()
        (self.lc() / "current").symlink_to(other)
        tar = self.e.bin / "tar"
        tar.write_text("#!/bin/sh\necho 'tar: No space left on device' >&2\nexit 2\n")
        tar.chmod(0o755)
        persona = self.lane("coordinator").read_text()
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("nothing was changed", r.stderr)
        self.assertEqual(os.readlink(self.lc() / "current"), other)
        self.assertTrue((self.e.home / ".agents" / "bin" / "lane-heavy").is_symlink())
        self.assertEqual(self.lane("coordinator").read_text(), persona)

    def test_a_lane_worktree_keeps_the_installed_tree_and_renders_from_it(self):
        self.e.run(str(self.repo), "--no-start")  # installs this checkout's HEAD (the tests say yes)
        head = self.head()
        clone, lane = Path(self.e.home) / "lc-main", Path(self.e.home) / "lc-lane"  # n2: its own clone and worktree
        subprocess.run(["git", "clone", "-q", str(ROOT), str(clone)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(clone), "worktree", "add", "-q", "-b", "lane", str(lane)], check=True,
                       capture_output=True)
        skill = Path("orchestrators/native-orca/skills/native-orca-lanes/SKILL.md")
        persona = Path("orchestrators/native-orca/personas/coordinator.md")
        for rel in (skill, persona):
            (lane / rel).write_text((lane / rel).read_text() + "\nUNREVIEWED LANE RULE\n")
        subprocess.run(["git", "-C", str(lane), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "lane"],
                       check=True, capture_output=True)
        r = self.e.run(str(self.repo), "--refresh", adapt=True, env_extra={"LANE_CONTROL_SWITCH": ""},
                       script=lane / "core" / "scripts" / "project-setup")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("lane-control: kept", r.stdout)
        self.assertIn("the kept install", r.stderr)  # M7: the kept tree's own setup ran
        self.assertEqual(os.readlink(self.lc() / "current"), head)
        installed = self.e.home / ".agents" / "skills" / "native-orca-lanes" / "SKILL.md"
        self.assertNotIn("UNREVIEWED LANE RULE", installed.read_text())
        self.assertEqual(installed.read_text(), (self.lc() / head / skill).read_text())
        self.assertNotIn("UNREVIEWED LANE RULE", self.lane("coordinator").read_text())
        self.assertIn(str(self.lc() / "current"), self.lane("coordinator").read_text())

    def test_tests_never_install_into_a_real_xdg_data_home(self):
        sentinel = Path(self.e.home) / "sentinel-data"  # INST-1 review M1: stands in for a real XDG_DATA_HOME
        sentinel.mkdir(exist_ok=True)
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(sentinel)}):
            e = NativeEnv()
        self.addCleanup(e.close)
        self.assertNotEqual(e.env["XDG_DATA_HOME"], str(sentinel))
        r = e.run(str(e.repo()), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(list(sentinel.iterdir()), [])
