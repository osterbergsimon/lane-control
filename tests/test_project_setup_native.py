"""project-setup's native-Orca path (phase 3 of docs/superpowers/plans/2026-11-19-native-orca-setup.md) and
project-pause on it, against the stub orca executable (tests/stubs/fake.py) in a temporary HOME."""
import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from test_project_setup import Env, ROOT, Tty, git  # the shared stub environment

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


# --- Task 3.2 ------------------------------------------------------------------------------------------------------

ROLES = ("coordinator", "design-lead", "reviewer", "debugger")


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
            r = self.e.run(str(self.repo), "--coordinator", coord, "--own-model", "gw/m1", "--no-start")
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
            self.assertIn("role `design-lead`", " ".join(coordinator.split()))

    def test_no_design_lead_renders_no_design_lead_persona(self):
        self.e.run(str(self.repo), "--no-start")
        self.assertTrue(self.lane("design-lead").exists())
        r = self.e.run(str(self.repo), "--no-design-lead", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertFalse(self.lane("design-lead").exists())
        self.assertIn("This project has no design lead", " ".join(self.lane("coordinator").read_text().split()))

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

    def test_opencode_plugin_is_installed_for_an_opencode_standing_role(self):
        plugin = Path(self.e.env["XDG_CONFIG_HOME"]) / "opencode" / "plugins" / "lane-persona.js"
        self.e.run(str(self.repo), "--no-start")
        self.assertFalse(plugin.exists())  # no OpenCode role: nothing of OpenCode's is touched
        r = self.e.run(str(self.repo), "--design-lead", "opencode/gw/m1", "--own-model", "gw/m1", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(plugin.read_bytes(), (ROOT / "core" / "harnesses" / "opencode" / "lane-persona.js").read_bytes())


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
        self.assertEqual(sorted(committed), sorted(["AGENTS.md", ADDITION, ".lane-control/project.md"]))
        agents = (self.repo / "AGENTS.md").read_text()
        self.assertEqual(agents.count("<!-- lane-control:begin -->"), 1)
        self.assertEqual(agents.count("<!-- orchestrator: native-orca -->"), 1)
        adapter_prompt = self.e.agent_runs()[0]["prompt"]
        self.assertIn(".lane-control/additions/", adapter_prompt)
        self.assertIn("Read docs/plan.md.", (self.repo / ".lane-control" / "coordinator.md").read_text())


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
        objectives = {x["id"]: x["objective"] for x in json.loads(self.e.state.read_text())["runs"]}
        self.assertEqual(objectives[runs["coordinator"]], "app coordinator")
        self.assertEqual(objectives[runs["design-lead"]], "app design lead")

    def test_opencode_coordinator_starts_with_opencode2_and_its_prompt(self):
        r = self.e.run(str(self.repo), "--coordinator", "opencode/gw/m1", "--own-model", "gw/m1", "--no-design-lead")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        s = self.session("coordinator")
        self.assertEqual(s["bin"], "opencode2")
        self.assertEqual(s["argv"][:2], ["--standalone", "--auto"])
        self.assertIn(str(self.repo / ".lane-control" / "coordinator.md"), opt(s["argv"], "--prompt"))
        self.assertNotIn("--name", s["argv"])

    def test_design_lead_starts_in_its_own_worktree(self):
        r = self.e.run(str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        c = self.create("app design lead")
        wt = Path(opt(c, "--worktree").split(":", 1)[1])
        self.assertNotEqual(wt, self.repo)
        self.assertTrue(any(f"--name design-lead --parent-worktree path:{self.repo}" in x for x in self.e.calls()))
        s = self.session("design-lead")
        self.assertEqual((s["persona"], s["cwd"]), (str(self.repo / ".lane-control" / "design-lead.md"), str(wt)))
        self.assertEqual(opt(s["argv"], "--name"), "app design lead")
        hook = json.loads((wt / ".claude" / "settings.local.json").read_text())
        self.assertEqual(len(hook["hooks"]["SessionStart"]), 1)

    def test_no_design_lead_starts_only_the_coordinator(self):
        self.e.run(str(self.repo), "--no-design-lead")
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
                                     "worktreePath": str(self.repo)}])
        r = self.e.run(str(self.repo), "--no-design-lead")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.e.creates(), [])
        self.assertIn("exists", r.stdout)

    def test_where_to_run_is_one_seam(self):
        m = load()
        self.assertEqual(m.Place().argv("terminal", "list"), [m.ORCA, "terminal", "list"])
        with self.assertRaises(NotImplementedError) as e:
            m.Place("box")
        self.assertIn("phase 6", str(e.exception))
        r = self.e.run("~/code/app", "--host", "box")
        self.assertEqual(r.returncode, 2)
        self.assertIn("phase 6", r.stderr)
        self.assertEqual(self.e.calls(), [])


# --- Task 3.4 ------------------------------------------------------------------------------------------------------

def session_record(e, checkout, role, harness):
    """Where the persona hook (Claude) or plugin (OpenCode) records a marked session's id."""
    core = str(ROOT / "core")
    if core not in sys.path:
        sys.path.insert(0, core)
    import harnesses
    with mock.patch.dict(os.environ, {"XDG_STATE_HOME": e.env["XDG_STATE_HOME"]}):
        return harnesses.session_record(checkout, role, harness)


class RefreshTests(Case):
    def test_refresh_rerenders_with_the_stored_pins_and_hooks(self):
        r = self.e.run(str(self.repo), "--coordinator", "opencode/gw/m1", "--own-model", "gw/m1", "--no-start")
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
        self.e.set_state(handover_on_send={"handover-latest": "new", "handover-design-lead": "new"})
        runs = self.e.settings(self.repo)["runs"]
        start = len(self.e.calls())  # setup closed Orca's startup shell itself; only stop's calls count here
        r = self.e.pause("stop", str(self.repo), "--wait", "5")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        sent = json.loads(self.e.state.read_text())["sent"]
        self.assertEqual(sorted(x["to"] for x in sent), sorted(f"run:{runs[k]}" for k in ("coordinator", "design-lead")))
        self.assertIn("handover", sent[0]["subject"].lower())
        bodies = {x["to"]: x["body"] for x in sent}
        self.assertIn("handover-latest", bodies[f"run:{runs['coordinator']}"])
        self.assertIn("handover-design-lead", bodies[f"run:{runs['design-lead']}"])
        self.assertNotIn("app coordinator", self.titles())
        self.assertNotIn("app design lead", self.titles())
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
        self.e.pause("stop", str(self.repo), "--no-handover")
        r = self.e.pause("start", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        coord = [x for x in self.e.sessions() if x["role"] == "coordinator"]
        self.assertEqual(len(coord), 2)
        self.assertEqual(opt(coord[-1]["argv"], "--resume"), "abc-123")
        self.assertIn("You are back after a pause", coord[-1]["argv"][-1])
        lead = [x for x in self.e.sessions() if x["role"] == "design-lead"]
        self.assertNotIn("--resume", lead[-1]["argv"])  # none recorded: a fresh session
        self.assertIn("handover-design-lead", lead[-1]["argv"][-1])  # its own handover memory (project-pause stop)
        self.assertFalse(self.e.settings(self.repo)["paused"])
        self.assertEqual(len([c for c in self.e.calls() if c.startswith("orca worktree create")]), 1)

    def test_start_resumes_an_opencode_session(self):
        r = self.e.run(str(self.repo), "--coordinator", "opencode/gw/m1", "--own-model", "gw/m1", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = session_record(self.e, self.repo, "coordinator", "opencode")
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text("ses_42\n")
        self.e.pause("stop", str(self.repo), "--no-handover")
        r = self.e.pause("start", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        coord = [x for x in self.e.sessions() if x["role"] == "coordinator"][-1]
        self.assertEqual((coord["bin"], opt(coord["argv"], "--session")), ("opencode2", "ses_42"))

    def test_start_when_running_starts_nothing(self):
        r = self.e.pause("start", str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.e.sessions()), 2)

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
        self.assertEqual(agents.count("<!-- lane-control:begin -->"), 1)
        self.assertEqual(agents.count("<!-- orchestrator: native-orca -->"), 1)
        lead_wt = Path(opt(next(c for c in self.e.creates() if opt(c, "--title") == "app design lead"),
                           "--worktree").split(":", 1)[1])
        for checkout in (self.repo, lead_wt):
            cfg = json.loads((checkout / ".claude" / "settings.local.json").read_text())
            self.assertEqual(len(cfg["hooks"]["SessionStart"]), 1, checkout)
        titles = [opt(c, "--title") for c in self.e.creates() if opt(c, "--title")]
        self.assertEqual(sorted(titles), ["app coordinator", "app design lead"])
        self.assertEqual(len(started), 2)
        self.assertIn("session coordinator: exists app coordinator", out)
        self.assertEqual((self.repo / ".git" / "info" / "exclude").read_text().count("lane-control: begin"), 1)
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=self.repo), "")


# --- final review fixes ----------------------------------------------------------------------------------------------

class ReviewFixTests(Case):
    def titles(self):
        return sorted(t.get("title") for t in json.loads(self.e.state.read_text())["terminals"])

    def test_stop_waits_for_every_roles_own_handover(self):
        # Important 1: the design lead writing first must not close the coordinator before its handover.
        self.assertEqual(self.e.run(str(self.repo)).returncode, 0)
        self.e.set_state(handover_on_send={"handover-design-lead": "lead's handover"})
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
        del st["runs"]["design-lead"]
        st_file.write_text(json.dumps(st))
        r = self.e.pause("stop", str(self.repo), "--wait", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("app design lead", r.stderr)
        self.assertNotIn("sent", json.loads(self.e.state.read_text()))  # the coordinator was not told to stop either

    def test_a_session_whose_terminal_was_retitled_is_still_found(self):
        # Important 3: the shell (fish) or OpenCode retitles the terminal; the stored handle still finds it.
        self.e.set_state(retitle="fish /home")
        r = self.e.run(str(self.repo), "--coordinator", "opencode/gw/m1", "--own-model", "gw/m1", "--no-design-lead")
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
        self.assertEqual(sorted(committed), sorted(["AGENTS.md", ADDITION, ".lane-control/project.md"]))
        self.assertIn("Read docs/plan.md.", (self.repo / ".lane-control" / "coordinator.md").read_text())
        review = [r for r in self.e.agent_runs() if "-p" in r["argv"]][1]["prompt"]
        self.assertIn(".lane-control/additions/", review)


# --- coordinator's review of PR #39 ----------------------------------------------------------------------------------

LANE_TIER = ROOT / "core" / "scripts" / "lane-tier"


def store_bytes(e, repo):
    d = e.store(repo)
    return {p.name: p.read_bytes() for p in sorted(d.iterdir()) if p.is_file()} if d.is_dir() else {}


class PinCheckTests(Case):
    def test_lane_tier_judges_a_model_alone(self):
        for model, code in (("opus", 0), ("gw/m1", 0), ("opencode-go/x", 4), ("opencode/big-pickle", 4)):
            r = subprocess.run(["bash", str(LANE_TIER), "--model", model], capture_output=True, text=True)
            self.assertEqual(r.returncode, code, (model, r.stderr))
        r = subprocess.run(["bash", str(LANE_TIER), "--model"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_a_refused_run_leaves_the_store_byte_identical(self):
        # M1: the refusal comes before anything is written.
        self.assertEqual(self.e.run(str(self.repo), "--no-start").returncode, 0)
        before = store_bytes(self.e, self.repo)
        for args in (["--coordinator", "opencode/gw/m1", "--own-model", "gw/m1", "--permission-mode", "plan"],
                     ["--design-lead", "opencode/gw/m1", "--own-model", "gw/m1", "--permission-mode", "dontAsk"]):
            r = self.e.run(str(self.repo), *args, "--no-start")
            self.assertEqual(r.returncode, 2, (args, r.stderr))
            self.assertEqual(store_bytes(self.e, self.repo), before, args)

    def test_a_model_lane_tier_refuses_is_refused_whatever_the_harness(self):
        # m5: lane-tier is asked; it refuses an opencode-go/ model even with claude as the harness.
        r = self.e.run(str(self.repo), "--coordinator", "claude/opencode-go/x", "--no-start")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("lane-tier", r.stderr)
        self.assertEqual(self.e.calls(), [])

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
        cases = ({"models": {"coordinator": "opencode/gw/m1"}, "own_models": ["gw/m1"], "permission_mode": "plan"},
                 {"models": {"coordinator": "opencode/opencode-go/x"}},
                 {"models": {"coordinator": "opencode/gw/m2"}})  # not declared own
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


if __name__ == "__main__":
    unittest.main()
