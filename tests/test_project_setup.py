"""project-setup against stub orca/cotal executables (tests/stubs/fake.py) in a temporary HOME."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "project-setup"
STUB = ROOT / "tests" / "stubs" / "fake.py"
FAKE_AGENT = ROOT / "tests" / "stubs" / "fake_agent.py"


def load():
    loader = importlib.machinery.SourceFileLoader("project_setup", str(SCRIPT))
    spec = importlib.util.spec_from_loader("project_setup", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def git(*a, cwd):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


class Env:
    """Temp HOME with stub orca/cotal and fake harness binaries first on PATH."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        # orca-ide too: packaged installs (NixOS) put the real one in the same dir as git.
        for name in ("orca", "orca-ide", "cotal"):
            (self.bin / name).symlink_to(STUB)
        for name in ("opencode", "claude"):
            p = self.bin / name
            p.write_text("#!/bin/sh\nexit 0\n")
            p.chmod(0o755)
        self.log = self.home / "stub.log"
        self.state = self.home / "stub.json"
        # Only the stub bin plus the dirs of git and python, so a real orca/cotal/claude/opencode never leaks in.
        tools = {str(Path(shutil.which(t)).parent) for t in ("git", "python3", "env", "sh")}
        self.env = {**os.environ, "HOME": str(self.home), "PATH": ":".join([str(self.bin), *sorted(tools)]),
                    "STUB_LOG": str(self.log), "STUB_STATE": str(self.state),
                    "GIT_CONFIG_GLOBAL": str(self.home / ".gitconfig")}
        (self.home / ".gitconfig").write_text("[user]\n\tname = t\n\temail = t@t\n")

    def run(self, *args, adapt=False, env_extra=None):
        """adapt False: only today's Orca setup steps; True: the onboarding flow in front of them too."""
        args = [*args, *([] if adapt else ["--skip-adapt"])]
        return subprocess.run([sys.executable, str(SCRIPT), *args], env={**self.env, **(env_extra or {})},
                              capture_output=True, text=True)

    def script_agents(self, **steps):
        """Replace the harness binaries with the agent stub; steps: {model: [step, ...]}, see tests/stubs/fake_agent.py."""
        (self.bin / "claude").unlink()
        (self.bin / "claude").symlink_to(FAKE_AGENT)
        self.set_state(agent_steps=steps)

    def agent_runs(self):
        f = Path(str(self.log) + ".agents")
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def set_state(self, **kw):
        s = json.loads(self.state.read_text()) if self.state.exists() else {}
        s.update(kw)
        self.state.write_text(json.dumps(s))

    def repo(self, name="app"):
        d = self.home / "src" / name
        d.mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=d)
        git("commit", "-q", "--allow-empty", "-m", "init", cwd=d)
        self.trust(d)
        return d

    def trust(self, d, accepted=True):
        """Record Claude's folder-trust answer for d, as Claude Code does in ~/.claude.json."""
        f = self.home / ".claude.json"
        cfg = json.loads(f.read_text()) if f.exists() else {}
        cfg.setdefault("projects", {}).setdefault(str(d), {})["hasTrustDialogAccepted"] = accepted
        f.write_text(json.dumps(cfg))

    def close(self):
        self.tmp.cleanup()


def in_order(calls, needles):
    """True when each needle (substring) matches a call, in order."""
    i = 0
    for c in calls:
        if i < len(needles) and needles[i] in c:
            i += 1
    return i == len(needles)


class PureTests(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def _trust_env(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home, repo = Path(tmp.name), Path(tmp.name) / "app"
        repo.mkdir()
        p = mock.patch.dict(os.environ, {"HOME": str(home)})
        p.start()
        self.addCleanup(p.stop)
        return home, repo

    def _tty(self, on):
        for stream in ("stdin", "stdout"):
            p = mock.patch.object(getattr(self.m.sys, stream), "isatty", return_value=on)
            p.start()
            self.addCleanup(p.stop)

    def test_trust_prompt_opens_claude_and_continues(self):
        home, repo = self._trust_env()
        self._tty(True)

        def claude(cmd, cwd=None, **kw):  # the owner answers "Yes, I trust this folder", then /exit
            (home / ".claude.json").write_text(json.dumps(
                {"projects": {str(cwd): {"hasTrustDialogAccepted": True}}}))
            return subprocess.CompletedProcess(cmd, 0)
        with mock.patch("builtins.input", return_value="") as ask, \
                mock.patch.object(self.m.subprocess, "run", side_effect=claude) as run:
            self.m.ensure_claude_trust(repo)
        ask.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["claude"])
        self.assertEqual(run.call_args.kwargs["cwd"], repo)

    def test_trust_prompt_declined_stops(self):
        _, repo = self._trust_env()
        self._tty(True)
        with mock.patch("builtins.input", return_value="n"), \
                mock.patch.object(self.m.subprocess, "run") as run:
            with self.assertRaises(self.m.Fail):
                self.m.ensure_claude_trust(repo)
        run.assert_not_called()

    def test_trust_still_missing_after_claude_stops(self):
        _, repo = self._trust_env()
        self._tty(True)
        with mock.patch("builtins.input", return_value="y"), \
                mock.patch.object(self.m.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            with self.assertRaises(self.m.Fail):
                self.m.ensure_claude_trust(repo)

    def test_trust_without_terminal_never_prompts(self):
        _, repo = self._trust_env()
        self._tty(False)
        with mock.patch("builtins.input") as ask:
            with self.assertRaises(self.m.Fail) as e:
                self.m.ensure_claude_trust(repo)
        ask.assert_not_called()
        self.assertIn(f"cd {repo} && claude", str(e.exception))

    def test_trust_folder_switch_records_trust_quietly(self):
        home, repo = self._trust_env()
        self._tty(True)
        cfg = {"numStartups": 7, "projects": {"/other": {"hasTrustDialogAccepted": True, "x": 1}}}
        (home / ".claude.json").write_text(json.dumps(cfg))
        (home / ".claude.json").chmod(0o600)
        with mock.patch("builtins.input") as ask, mock.patch.object(self.m.subprocess, "run") as run:
            self.m.ensure_claude_trust(repo, record=True)
        ask.assert_not_called()
        run.assert_not_called()
        out = json.loads((home / ".claude.json").read_text())
        self.assertTrue(out["projects"][str(repo)]["hasTrustDialogAccepted"])
        self.assertEqual(out["projects"]["/other"], {"hasTrustDialogAccepted": True, "x": 1})
        self.assertEqual(out["numStartups"], 7)
        self.assertEqual((home / ".claude.json").stat().st_mode & 0o777, 0o600)

    def test_trust_folder_keeps_existing_project_settings(self):
        home, repo = self._trust_env()
        (home / ".claude.json").write_text(json.dumps({"projects": {str(repo): {"allowedTools": ["Bash"]}}}))
        self.m.ensure_claude_trust(repo, record=True)
        entry = json.loads((home / ".claude.json").read_text())["projects"][str(repo)]
        self.assertEqual(entry, {"allowedTools": ["Bash"], "hasTrustDialogAccepted": True})

    def test_trust_folder_refuses_unreadable_config(self):
        # Never replace a config we could not parse: it holds the owner's login state and settings.
        home, repo = self._trust_env()
        (home / ".claude.json").write_text("{not json")
        with self.assertRaises(self.m.Fail):
            self.m.ensure_claude_trust(repo, record=True)
        self.assertEqual((home / ".claude.json").read_text(), "{not json")

    def test_host_mode_keeps_the_terminal_for_prompts(self):
        with mock.patch.object(self.m.sys.stdin, "isatty", return_value=True):
            self.assertIn("-t", self.m.reexec_ssh_args())
        with mock.patch.object(self.m.sys.stdin, "isatty", return_value=False):
            self.assertNotIn("-t", self.m.reexec_ssh_args())

    def test_slug_and_port(self):
        self.assertEqual(self.m.slug("My App.v2"), "my-app-v2")
        self.assertEqual(self.m.slug("  --Weird__Name--  "), "weird-name")
        p = self.m.derive_port("my-app-v2")
        self.assertTrue(14200 <= p <= 14999)
        self.assertEqual(p, self.m.derive_port("my-app-v2"))
        self.assertNotEqual(self.m.derive_port("a"), self.m.derive_port("b"))

    def test_render_persona_claude(self):
        text = (ROOT / "personas" / "coordinator.md").read_text()
        out = self.m.render_persona(text, "claude", "opus", "auto", "Shop")
        self.assertIn("agent: claude", out)
        self.assertIn("model: opus", out)
        # bypass in Claude's Shift+Tab mode cycle, without starting in it
        self.assertIn('launchOptions: { "permission-mode": "auto", "allow-dangerously-skip-permissions": "" }', out)
        self.assertNotIn("events:", out)
        self.assertNotIn("{{", out)
        self.assertIn("coordinator of Shop", out)

    def test_render_persona_opencode(self):
        text = (ROOT / "personas" / "design-lead.md").read_text()
        out = self.m.render_persona(text, "opencode", "opencode/deepseek-v4.1-flash", "auto", "Shop")
        self.assertIn("events: false", out)
        self.assertNotIn("permission-mode", out)
        self.assertNotIn("{{", out)

    def test_parse_harness_model(self):
        self.assertEqual(self.m.harness_model("claude/opus"), ("claude", "opus"))
        self.assertEqual(self.m.harness_model("opencode/opencode/deepseek-v4.1-flash"),
                         ("opencode", "opencode/deepseek-v4.1-flash"))
        with self.assertRaises(SystemExit):
            self.m.harness_model("vim/opus")


class Tty(io.StringIO):
    def isatty(self):
        return True


ADAPTED_FILE = ".lane-control/commands.md"
PASS = {"say": "Looks right.\nVERDICT: PASS"}
FIX = {"say": "commands.md names the wrong test command.\nVERDICT: FIX"}
WRITE = {"write": {ADAPTED_FILE: "run the tests\n"}, "say": "I checked everything and it is perfect."}


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.addCleanup(self.e.close)
        self.repo = self.e.repo()

    def in_terminal(self, answer, *args):
        """main() as if the owner sat at a terminal and answered `answer`; returns (status, stdout, input mock)."""
        m = load()
        out = Tty()
        with mock.patch.dict(os.environ, self.e.env, clear=True), mock.patch("sys.stdin", Tty()), \
                mock.patch("builtins.input", return_value=answer) as ask, contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            status = m.main([str(self.repo), *args])
        return status, out.getvalue(), ask

    def head_count(self):
        return int(git("rev-list", "--count", "HEAD", cwd=self.repo))

    def committed_paths(self):
        return git("show", "--name-only", "--format=", "HEAD", cwd=self.repo).splitlines()

    def orca_setup_ran(self):
        return any(c.startswith(("orca repo add", "cotal space create")) for c in self.e.calls())

    def models(self):
        return [r["model"] for r in self.e.agent_runs()]

    # --- the yes/no ------------------------------------------------------------------------------------------
    def test_yes_commits_then_runs_orca_setup(self):
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        status, out, ask = self.in_terminal("y", "--name", "Shop")
        self.assertEqual(status, 0)
        ask.assert_called_once()
        self.assertTrue(in_order(self.e.calls(), ["claude -p --model opus", "claude -p --model sonnet",
                                                  "orca repo add", "cotal spawn coordinator"]), self.e.calls())
        self.assertEqual(sorted(self.committed_paths()), sorted([".gitignore", "AGENTS.md", ADAPTED_FILE]))
        message = git("log", "-1", "--format=%B", cwd=self.repo)
        self.assertNotRegex(message, r"(?i)co-authored|claude|generated")
        self.assertEqual(git("log", "-1", "--format=%an <%ae>", cwd=self.repo), "t <t@t>")

    def test_no_stops_and_leaves_files_uncommitted(self):
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        status, out, ask = self.in_terminal("n")
        self.assertEqual(status, 3)
        self.assertEqual(self.head_count(), 1)
        self.assertTrue((self.repo / ADAPTED_FILE).exists())
        self.assertFalse(self.orca_setup_ran())

    def test_empty_answer_is_no(self):
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.assertEqual(self.in_terminal("")[0], 3)
        self.assertEqual(self.head_count(), 1)

    def test_no_terminal_never_commits_or_runs_orca_setup(self):
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn(f"added: {ADAPTED_FILE}", r.stdout)
        self.assertEqual(self.head_count(), 1)
        self.assertFalse(self.orca_setup_ran())

    def test_commit_takes_only_our_paths(self):
        (self.repo / "wip.txt").write_text("work in progress")
        (self.repo / "staged.txt").write_text("the owner's staged work")
        git("add", "staged.txt", cwd=self.repo)
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.assertEqual(self.in_terminal("yes")[0], 0)
        self.assertEqual(sorted(self.committed_paths()), sorted([".gitignore", "AGENTS.md", ADAPTED_FILE]))
        status = git("status", "--porcelain", cwd=self.repo)
        self.assertIn("?? wip.txt", status)
        self.assertIn("A  staged.txt", status)

    # --- the list --------------------------------------------------------------------------------------------
    def test_list_comes_from_git_with_marks_and_suggestions(self):
        (self.repo / ".lane-control").mkdir()
        (self.repo / ".lane-control" / "old.md").write_text("old\n")
        git("add", ".lane-control", cwd=self.repo)
        git("commit", "-q", "-m", "earlier", cwd=self.repo)
        write = {"write": {".lane-control/old.md": "new\n", ADAPTED_FILE: "x\n",
                           ".lane-control/SUGGESTIONS.md": "Add the release checklist.\n"},
                 "say": "I only touched one file."}
        self.e.script_agents(opus=[write], sonnet=[PASS])
        status, out, _ = self.in_terminal("n", "--readapt")
        self.assertEqual(status, 3)
        self.assertIn("changed: .lane-control/old.md", out)
        self.assertIn(f"added: {ADAPTED_FILE}", out)
        self.assertIn("added: AGENTS.md", out)
        listing, _, suggested = out.partition("Suggested AGENTS.md edits")
        self.assertIn("Add the release checklist.", suggested)
        self.assertNotIn("SUGGESTIONS", listing.split("Files added or changed")[1])

    def test_suggestions_are_never_committed(self):
        write = {"write": {ADAPTED_FILE: "x\n", ".lane-control/SUGGESTIONS.md": "Do y.\n"}}
        self.e.script_agents(opus=[write], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        self.assertNotIn(".lane-control/SUGGESTIONS.md", self.committed_paths())
        self.assertFalse((self.repo / ".lane-control" / "SUGGESTIONS.md").exists())

    def test_suggestions_leave_the_repo_before_the_review(self):
        write = {"write": {ADAPTED_FILE: "x\n", ".lane-control/SUGGESTIONS.md": "Do y.\n"}}
        self.e.script_agents(opus=[write], sonnet=[PASS])
        _, out, _ = self.in_terminal("n")
        reviewer = self.e.agent_runs()[1]
        self.assertEqual(reviewer["lane_files"], ["commands.md"])
        self.assertNotIn("Do y.", reviewer["prompt"])
        self.assertIn("Do y.", out)
        self.assertNotIn("SUGGESTIONS", git("status", "--porcelain", "-uall", cwd=self.repo))

    def test_fix_round_suggestions_are_added_to_the_earlier_ones(self):
        first = {"write": {ADAPTED_FILE: "x\n", ".lane-control/SUGGESTIONS.md": "First question"}}
        fixed = {"write": {ADAPTED_FILE: "y\n", ".lane-control/SUGGESTIONS.md": "Second question\n"}}
        self.e.script_agents(opus=[first, fixed], sonnet=[FIX, PASS])
        _, out, _ = self.in_terminal("n")
        self.assertIn("First question\n  Second question", out)
        self.assertEqual(self.e.agent_runs()[3]["lane_files"], ["commands.md"])

    def test_a_new_adapting_run_starts_with_no_old_suggestions(self):
        write = {"write": {ADAPTED_FILE: "x\n", ".lane-control/SUGGESTIONS.md": "Old question\n"}}
        self.e.script_agents(opus=[write], sonnet=[PASS])
        self.in_terminal("n")
        self.e.set_state(agent_steps={"opus": [{"write": {ADAPTED_FILE: "y\n"}}], "sonnet": [PASS]})
        self.assertNotIn("Old question", self.in_terminal("n", "--readapt")[1])

    def test_suggestion_only_readapt_shows_them(self):
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        self.e.set_state(agent_steps={"opus": [{"write": {".lane-control/SUGGESTIONS.md": "Only a question\n"}}]})
        status, out, _ = self.in_terminal("y", "--readapt")
        self.assertEqual(status, 0)
        self.assertIn("Only a question", out)
        self.assertEqual(out.count("Only a question"), 1)

    def test_suggestions_are_shown_again_on_a_rerun(self):
        write = {"write": {ADAPTED_FILE: "x\n", ".lane-control/SUGGESTIONS.md": "Do y.\n"}}
        self.e.script_agents(opus=[write], sonnet=[PASS])
        self.in_terminal("n")
        self.e.set_state(agent_steps={"sonnet": [PASS]})
        self.assertIn("Do y.", self.in_terminal("n")[1])

    # --- the reviewer ----------------------------------------------------------------------------------------
    def test_reviewer_never_sees_the_adapting_agents_account(self):
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.in_terminal("n")
        adapter, reviewer = self.e.agent_runs()
        self.assertNotIn("perfect", reviewer["prompt"])
        self.assertIn(ADAPTED_FILE, reviewer["prompt"])
        self.assertIn("VERDICT", reviewer["prompt"])

    def test_same_model_for_both_is_refused(self):
        self.e.script_agents()
        r = self.e.run(str(self.repo), "--adapter", "claude/opus", "--adapt-reviewer", "claude/opus", adapt=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.e.agent_runs(), [])

    def test_fix_round_passes_findings_back(self):
        fixed = {"write": {ADAPTED_FILE: "fixed\n"}}
        self.e.script_agents(opus=[WRITE, fixed], sonnet=[FIX, PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        self.assertEqual(self.models(), ["opus", "sonnet", "opus", "sonnet"])
        self.assertIn("wrong test command", self.e.agent_runs()[2]["prompt"])
        self.assertEqual((self.repo / ADAPTED_FILE).read_text(), "fixed\n")

    def test_two_fix_rounds_then_stops_and_commits_nothing(self):
        self.e.script_agents(opus=[WRITE, {}, {}, {}], sonnet=[FIX, FIX, FIX, PASS])
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 3)
        self.assertEqual(self.models(), ["opus", "sonnet", "opus", "sonnet", "opus", "sonnet"])
        ask.assert_not_called()
        self.assertIn("wrong test command", out)
        self.assertEqual(self.head_count(), 1)
        self.assertFalse(self.orca_setup_ran())

    def test_review_without_a_verdict_is_not_a_pass(self):
        for reply in ("All good, I think.", "VERDICT: PASS\nbut see above", ""):
            with self.subTest(reply=reply):
                self.assertIsNone(load().verdict(reply))
        self.e.script_agents(opus=[WRITE], sonnet=[{"say": "All good, I think."}])
        status, _, ask = self.in_terminal("y")
        self.assertEqual(status, 3)
        ask.assert_not_called()
        self.assertEqual(self.head_count(), 1)

    def test_verdict_tolerates_bold(self):
        self.assertEqual(load().verdict("x\n**VERDICT: PASS**\n"), "PASS")

    # --- AGENTS.md -------------------------------------------------------------------------------------------
    def _with_agents_md(self, text="# Rules\n\nBe kind.\n"):
        (self.repo / "AGENTS.md").write_text(text)
        git("add", "AGENTS.md", cwd=self.repo)
        git("commit", "-q", "-m", "agents", cwd=self.repo)

    def test_existing_agents_md_only_gains_the_section(self):
        self._with_agents_md()
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        m = load()
        text = (self.repo / "AGENTS.md").read_text()
        self.assertTrue(text.startswith("# Rules\n\nBe kind.\n"))
        self.assertEqual(text.count(m.SECTION_BEGIN), 1)
        self.assertEqual(m.without_section(text).rstrip(), "# Rules\n\nBe kind.")

    def test_no_agents_md_creates_one_with_only_the_section(self):
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.in_terminal("n")
        self.assertEqual((self.repo / "AGENTS.md").read_text(), load().SECTION + "\n")

    def _link_agents_md(self, target):
        (self.repo / "AGENTS.md").symlink_to(target)
        git("add", "AGENTS.md", cwd=self.repo)
        git("commit", "-q", "-m", "link", cwd=self.repo)

    def test_symlinked_agents_md_gets_the_section_in_its_target(self):
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "rules.md").write_text("# Rules\n")
        git("add", "docs", cwd=self.repo)
        self._link_agents_md("docs/rules.md")
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        self.assertTrue((self.repo / "AGENTS.md").is_symlink())
        self.assertEqual(sorted(self.committed_paths()), sorted([".gitignore", "docs/rules.md", ADAPTED_FILE]))
        self.assertIn(load().SECTION_BEGIN, (self.repo / "docs" / "rules.md").read_text())

    def test_agents_md_linking_outside_the_repo_is_refused(self):
        outside = self.e.home / "shared.md"
        outside.write_text("# Shared\n")
        self._link_agents_md(str(outside))
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(outside.read_text(), "# Shared\n")
        self.assertEqual(self.e.agent_runs(), [])

    def test_section_updated_in_place_never_duplicated(self):
        m = load()
        old = f"before\n{m.SECTION_BEGIN}\nstale\n{m.SECTION_END}\nafter\n"
        new = m.with_section(old)
        self.assertEqual(new, f"before\n{m.SECTION}\nafter\n")
        self.assertEqual(m.with_section(new), new)

    def test_section_keeps_crlf_and_a_missing_final_newline(self):
        m = load()
        self.assertTrue(m.with_section("a\r\nb\r\n").startswith("a\r\nb\r\n\r\n<!--"))
        self.assertIn("\r\n", m.with_section("a\r\nb\r\n").split(m.SECTION_BEGIN)[1])
        self.assertEqual(m.with_section("a"), f"a\n\n{m.SECTION}\n")

    def test_broken_markers_are_refused(self):
        m = load()
        for text in (m.SECTION_BEGIN, m.SECTION_END + m.SECTION_BEGIN, m.SECTION + m.SECTION):
            with self.subTest(text=text), self.assertRaises(m.Fail):
                m.with_section(text)

    def test_owners_uncommitted_agents_md_edit_stops_before_any_agent(self):
        self._with_agents_md()
        (self.repo / "AGENTS.md").write_text("# Rules\n\nBe very kind.\n")
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.e.agent_runs(), [])

    def test_agent_editing_agents_md_is_refused(self):
        self._with_agents_md()
        write = {"write": {ADAPTED_FILE: "x\n", "AGENTS.md": "# Rewritten\n"}}
        self.e.script_agents(opus=[write], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.head_count(), 2)
        self.assertFalse(self.orca_setup_ran())

    def test_agent_writing_outside_its_folder_is_refused(self):
        write = {"write": {ADAPTED_FILE: "x\n", "src/app.py": "oops\n"}}
        self.e.script_agents(opus=[write], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.head_count(), 1)
        self.assertEqual(self.models(), ["opus"])

    def test_owners_dirty_files_are_not_blamed_on_the_agent(self):
        (self.repo / "wip.txt").write_text("mine")
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.assertEqual(self.in_terminal("n")[0], 3)

    # --- re-runs ---------------------------------------------------------------------------------------------
    def test_rerun_after_yes_starts_no_agent(self):
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        head, runs = git("rev-parse", "HEAD", cwd=self.repo), len(self.e.agent_runs())
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("adapt: exists", r.stdout)
        self.assertEqual(len(self.e.agent_runs()), runs)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), head)

    def test_rerun_with_symlinked_agents_md_starts_no_agent(self):
        (self.repo / "CLAUDE.md").write_text("# Rules\n")
        git("add", "CLAUDE.md", cwd=self.repo)
        (self.repo / "AGENTS.md").symlink_to("CLAUDE.md")
        git("add", "AGENTS.md", cwd=self.repo)
        git("commit", "-q", "-m", "link", cwd=self.repo)
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        head = git("rev-parse", "HEAD", cwd=self.repo)
        self.assertTrue(load().onboarded(self.repo))
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.models(), ["opus", "sonnet"])
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), head)

    def test_rerun_after_readapt_no_asks_again_instead_of_going_on(self):
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        self.e.set_state(agent_steps={"opus": [{"write": {ADAPTED_FILE: "newer\n"}}], "sonnet": [PASS, PASS]})
        self.assertEqual(self.in_terminal("n", "--readapt")[0], 3)
        status, out, ask = self.in_terminal("n")
        self.assertEqual(status, 3)
        ask.assert_called_once()
        self.assertNotIn("verify: ok", out)
        self.assertEqual(self.models(), ["opus", "sonnet", "opus", "sonnet", "sonnet"])

    def _stale_section(self):
        """An onboarded project whose committed section has wording this lane-control no longer writes."""
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        m = load()
        f = self.repo / "AGENTS.md"
        f.write_text(f.read_text().replace(m.SECTION, f"{m.SECTION_BEGIN}\nOLD WORDING\n{m.SECTION_END}"))
        git("add", "AGENTS.md", cwd=self.repo)
        git("commit", "-q", "-m", "old section", cwd=self.repo)
        self.e.log.write_text("")
        return f, m

    def test_plain_rerun_updates_a_stale_section_on_yes(self):
        f, m = self._stale_section()
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 0)
        ask.assert_called_once()
        self.assertEqual(self.models(), ["opus", "sonnet"])
        self.assertNotIn("OLD WORDING", f.read_text())
        self.assertEqual(f.read_text().count(m.SECTION_BEGIN), 1)
        self.assertEqual(self.committed_paths(), ["AGENTS.md"])
        self.assertIn("changed: AGENTS.md", out)
        self.assertIn("verify: ok", out)

    def test_plain_rerun_stale_section_no_leaves_it_uncommitted(self):
        f, _ = self._stale_section()
        head = git("rev-parse", "HEAD", cwd=self.repo)
        status, out, _ = self.in_terminal("n")
        self.assertEqual(status, 3)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), head)
        self.assertNotIn("OLD WORDING", f.read_text())
        self.assertNotIn("verify: ok", out)

    def test_rerun_without_terminal_leaves_a_stale_section_and_runs_setup(self):
        f, _ = self._stale_section()
        head = git("rev-parse", "HEAD", cwd=self.repo)
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("agents-md: stale", r.stdout)
        self.assertIn("OLD WORDING", f.read_text())
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), head)
        self.assertIn("verify: ok", r.stdout)

    def test_readapt_with_no_agent_change_updates_a_stale_section(self):
        f, _ = self._stale_section()
        self.e.set_state(agent_steps={"opus": [{}]})
        self.assertEqual(self.in_terminal("y", "--readapt")[0], 0)
        self.assertEqual(self.models(), ["opus", "sonnet", "opus"])
        self.assertNotIn("OLD WORDING", f.read_text())
        self.assertEqual(self.committed_paths(), ["AGENTS.md"])

    def test_rerun_with_a_current_section_never_asks(self):
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        status, _, ask = self.in_terminal("n")
        self.assertEqual(status, 0)
        ask.assert_not_called()

    def test_rerun_after_no_reviews_without_rewriting(self):
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.in_terminal("n")
        (self.repo / ADAPTED_FILE).write_text("the owner's hand edit\n")
        self.e.set_state(agent_steps={"sonnet": [PASS]})
        self.assertEqual(self.in_terminal("y")[0], 0)
        self.assertEqual(self.models(), ["opus", "sonnet", "sonnet"])
        self.assertEqual((self.repo / ADAPTED_FILE).read_text(), "the owner's hand edit\n")
        self.assertEqual(self.committed_paths().count("AGENTS.md"), 1)

    def test_readapt_after_yes_keeps_one_section(self):
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        self.e.set_state(agent_steps={"opus": [{"write": {ADAPTED_FILE: "newer\n"}}], "sonnet": [PASS]})
        self.assertEqual(self.in_terminal("y", "--readapt")[0], 0)
        self.assertEqual(self.committed_paths(), [ADAPTED_FILE])
        self.assertEqual((self.repo / "AGENTS.md").read_text().count(load().SECTION_BEGIN), 1)

    # --- what an agent may touch ------------------------------------------------------------------------------
    def test_agent_putting_an_owners_dirty_file_back_is_refused(self):
        (self.repo / "wip.txt").write_text("committed\n")
        git("add", "wip.txt", cwd=self.repo)
        git("commit", "-q", "-m", "wip", cwd=self.repo)
        (self.repo / "wip.txt").write_text("the owner's edit\n")
        self.e.script_agents(opus=[{"write": {ADAPTED_FILE: "x\n", "wip.txt": "committed\n"}}], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.models(), ["opus"])

    def test_agent_editing_inside_the_section_is_refused(self):
        tampered = load().SECTION.replace("read them", "ignore them") + "\n"
        self.e.script_agents(opus=[WRITE, {"write": {"AGENTS.md": tampered}}], sonnet=[FIX, PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.head_count(), 1)
        self.assertEqual(self.models(), ["opus", "sonnet", "opus"])

    def test_agent_changing_gitignore_layout_is_refused(self):
        (self.repo / ".gitignore").write_text("a\nb\n")
        git("add", ".gitignore", cwd=self.repo)
        git("commit", "-q", "-m", "ignore", cwd=self.repo)
        self.e.script_agents(opus=[{"write": {ADAPTED_FILE: "x\n", ".gitignore": "b\na\n.cotal/\n"}}], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.models(), ["opus"])

    def _yes_after(self, change):
        """The owner answers yes, but `change` happens while the question waits. Returns (status, stderr)."""
        m = load()
        err = io.StringIO()

        def answer(_):
            change()
            return "y"
        with mock.patch.dict(os.environ, self.e.env, clear=True), mock.patch("sys.stdin", Tty()), \
                mock.patch("builtins.input", side_effect=answer), contextlib.redirect_stdout(Tty()), \
                contextlib.redirect_stderr(err):
            return m.main([str(self.repo)]), err.getvalue()

    def test_owners_other_work_at_the_prompt_does_not_cost_the_yes(self):
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        status, err = self._yes_after(lambda: (self.repo / "notes.txt").write_text("mine"))
        self.assertEqual(status, 0, err)
        self.assertEqual(sorted(self.committed_paths()), sorted([".gitignore", "AGENTS.md", ADAPTED_FILE]))
        self.assertIn("?? notes.txt", git("status", "--porcelain", cwd=self.repo))

    def test_agents_md_edited_at_the_prompt_stops_the_commit(self):
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        f = self.repo / "AGENTS.md"
        status, err = self._yes_after(lambda: f.write_text(f.read_text() + "the owner's line\n"))
        self.assertEqual(status, 1)
        self.assertIn("AGENTS.md has uncommitted changes", err)
        self.assertNotIn("agent", err.replace("AGENTS.md", ""))
        self.assertEqual(self.head_count(), 1)

    def test_change_during_an_agent_run_does_not_claim_who_made_it(self):
        self.e.script_agents(opus=[{"write": {ADAPTED_FILE: "x\n", "src/app.py": "oops\n"}}])
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("src/app.py", r.stderr)
        self.assertNotIn("an agent changed", r.stderr)

    def test_gitignore_rewritten_by_an_earlier_setup_says_so(self):
        (self.repo / ".gitignore").write_bytes(b"a\r\nb\r\n")
        git("add", ".gitignore", cwd=self.repo)
        git("commit", "-q", "-m", "ignore", cwd=self.repo)
        (self.repo / ".gitignore").write_bytes(b"a\nb\n.cotal/\n")
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("earlier project-setup may have rewritten its line endings", r.stderr)
        self.assertEqual(self.e.agent_runs(), [])

    def test_agent_committing_is_refused(self):
        self.e.script_agents(opus=[{"write": {ADAPTED_FILE: "x\n"}, "commit": "sneaky"}], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.models(), ["opus"])
        self.assertFalse(self.orca_setup_ran())

    # --- sizes and encodings ---------------------------------------------------------------------------------
    def test_huge_multibyte_diff_and_findings_fit_in_the_prompt(self):
        (self.repo / ".lane-control").mkdir()
        big = self.repo / ".lane-control" / "a.md"
        big.write_text("€" * 59000 + "\n")
        git("add", ".lane-control", cwd=self.repo)
        git("commit", "-q", "-m", "big", cwd=self.repo)
        huge = {"say": "€" * 59000 + "\nVERDICT: FIX"}
        self.e.script_agents(opus=[{"write": {".lane-control/a.md": "é" * 59000 + "\n"}}, {}],
                             sonnet=[huge, PASS])
        self.assertEqual(self.in_terminal("n", "--readapt")[0], 3)
        self.assertEqual(self.models(), ["opus", "sonnet", "opus", "sonnet"])
        for run in self.e.agent_runs():
            self.assertLess(len(run["prompt"].encode()), 131072)

    def test_a_program_that_cannot_start_is_a_clean_failure(self):
        m = load()
        with self.assertRaises(m.Fail):
            m.run(["true", "x" * 200000])
        with self.assertRaises(m.Fail):
            m.run(["/nonexistent/program"])

    def test_non_utf8_agents_md_and_gitignore_keep_their_bytes(self):
        (self.repo / "AGENTS.md").write_bytes(b"caf\xe9\n")
        (self.repo / ".gitignore").write_bytes(b"caf\xe9\n")
        git("add", "AGENTS.md", ".gitignore", cwd=self.repo)
        git("commit", "-q", "-m", "latin1", cwd=self.repo)
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        self.assertTrue((self.repo / "AGENTS.md").read_bytes().startswith(b"caf\xe9\n\n<!--"))
        self.assertEqual((self.repo / ".gitignore").read_bytes(), b"caf\xe9\n.cotal/\n")

    # --- failures and switches -------------------------------------------------------------------------------
    def test_agent_failure_stops_before_orca_setup(self):
        self.e.script_agents(opus=[{"exit": 1, "say": "boom"}])
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(self.orca_setup_ran())
        self.assertFalse((self.repo / "AGENTS.md").exists())

    def test_agent_writing_nothing_stops(self):
        self.e.script_agents(opus=[{}])
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("wrote nothing", r.stderr)

    def test_no_git_identity_stops_before_any_agent(self):
        (self.e.home / ".gitconfig").write_text("")
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("user.name", r.stderr)
        self.assertEqual(self.e.agent_runs(), [])

    def test_skip_adapt_runs_only_todays_setup(self):
        self.e.script_agents()
        r = self.e.run(str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.e.agent_runs(), [])
        self.assertFalse((self.repo / "AGENTS.md").exists())

    # --- how the agents are started --------------------------------------------------------------------------
    def test_claude_adapter_may_write_only_its_folder(self):
        cmd = load().agent_cmd("opus", "PROMPT", write=True)
        self.assertEqual(cmd[:2], ["claude", "-p"])
        self.assertEqual(cmd[-1], "PROMPT")
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Read,Glob,Grep,Edit,Write")
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], "Read,Glob,Grep,Edit(/.lane-control/**)")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
        self.assertIn("--restricted", cmd)

    def test_claude_reviewer_only_reads(self):
        cmd = load().agent_cmd("sonnet", "PROMPT", write=False)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Read,Glob,Grep")
        self.assertNotIn("Edit", " ".join(cmd[:-1]))

    def test_onboarding_agents_are_claude_only(self):
        # They read the whole project, and a third-party lane may not read private data.
        self.e.script_agents()
        for flag in ("--adapter", "--adapt-reviewer"):
            with self.subTest(flag=flag):
                r = self.e.run(str(self.repo), flag, "opencode/x/y", adapt=True)
                self.assertEqual(r.returncode, 2)
                self.assertIn("claude/<model>", r.stderr)
        self.assertEqual(self.e.agent_runs(), [])

    # --- .gitignore ------------------------------------------------------------------------------------------
    def test_gitignore_is_listed_and_committed_before_the_agent_runs(self):
        seen = {"write": {ADAPTED_FILE: "x\n", ".cotal/scratch.txt": "not the agent's"}}
        self.e.script_agents(opus=[seen], sonnet=[PASS])
        status, out, _ = self.in_terminal("y")
        self.assertEqual(status, 0)
        self.assertIn("added: .gitignore", out)
        self.assertEqual(sorted(self.committed_paths()), sorted([".gitignore", "AGENTS.md", ADAPTED_FILE]))
        self.assertEqual(git("show", "HEAD:.gitignore", cwd=self.repo), ".cotal/")

    def test_gitignore_keeps_other_lines_and_crlf(self):
        (self.repo / ".gitignore").write_bytes(b"a\r\nb\r\n")
        git("add", ".gitignore", cwd=self.repo)
        git("commit", "-q", "-m", "ignore", cwd=self.repo)
        self.assertEqual(load().ignore_cotal(self.repo), "updated")
        self.assertEqual((self.repo / ".gitignore").read_bytes(), b"a\r\nb\r\n.cotal/\r\n")

    def test_owners_uncommitted_gitignore_edit_stops_before_any_agent(self):
        (self.repo / ".gitignore").write_text("mine\n")
        self.e.script_agents(opus=[WRITE], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.e.agent_runs(), [])

    def test_agent_editing_gitignore_is_refused(self):
        self.e.script_agents(opus=[{"write": {ADAPTED_FILE: "x\n", ".gitignore": ".cotal/\nsecrets\n"}}], sonnet=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.head_count(), 1)

    # --- prompts and --host ----------------------------------------------------------------------------------
    def test_prompt_templates_get_their_machine_values(self):
        fixed = {"write": {ADAPTED_FILE: "fixed\n"}}
        self.e.script_agents(opus=[WRITE, fixed], sonnet=[FIX, PASS])
        self.in_terminal("n", "--name", "Shop", "--no-design-lead")
        first, review, second, _ = self.e.agent_runs()
        for run in (first, review, second):
            self.assertIn("Shop", run["prompt"])
            self.assertNotIn("{{", run["prompt"])
        self.assertIn("design lead: no", first["prompt"])
        self.assertNotIn("wrong test command", first["prompt"])
        self.assertIn("wrong test command", second["prompt"])

    def test_host_mode_carries_what_onboarding_reads(self):
        m = load()
        self.assertIn("templates", m.HOST_FILES)  # templates/adapt/ with it
        self.assertIn("model-routing.md", m.HOST_FILES)
        for path in m.HOST_FILES:
            self.assertTrue((ROOT / path).exists(), path)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def tearDown(self):
        self.e.close()

    def test_refuses_root(self):
        m = load()
        with mock.patch.object(m.os, "geteuid", return_value=0):
            self.assertEqual(m.main(["/tmp/x"]), 2)
        self.assertEqual(self.e.calls(), [])

    def test_preflight_fails_when_orca_unreachable(self):
        repo = self.e.repo()
        self.e.set_state(orca_up=False)
        r = self.e.run(str(repo))
        self.assertEqual(r.returncode, 1)
        self.assertIn("orca", r.stderr.lower())
        self.assertFalse(any("repo add" in c or "spawn" in c for c in self.e.calls()))

    def test_preflight_fails_when_harness_missing(self):
        repo = self.e.repo()
        (self.e.bin / "claude").unlink()
        r = self.e.run(str(repo))
        self.assertEqual(r.returncode, 1)
        self.assertIn("claude", r.stderr)
        self.assertFalse(any("repo add" in c for c in self.e.calls()))

    def test_url_clones(self):
        src = self.e.repo("upstream")
        bare = self.e.home / "upstream.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True)
        self.e.trust(self.e.home / "code" / "shop")
        r = self.e.run(f"file://{bare}", "--dest", str(self.e.home / "code"), "--name", "Shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.e.home / "code" / "shop" / ".git").exists())
        self.assertIn("repo: created", r.stdout)

    def test_dir_subdir_uses_repo_root(self):
        repo = self.e.repo()
        (repo / "web").mkdir()
        r = self.e.run(str(repo / "web"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any(f"repo add --path {repo}" in c for c in self.e.calls()))
        self.assertFalse((repo / "web" / ".git").exists())

    def test_dir_not_repo_inits(self):
        d = self.e.home / "plain"
        d.mkdir()
        self.e.trust(d)
        r = self.e.run(str(d))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((d / ".git").exists())
        log = subprocess.run(["git", "log", "--oneline"], cwd=d, capture_output=True, text=True).stdout
        self.assertEqual(len(log.splitlines()), 1)

    def test_dirty_repo_untouched(self):
        repo = self.e.repo()
        (repo / "wip.txt").write_text("work in progress")
        before = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
        r = self.e.run(str(repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        after = subprocess.run(["git", "status", "--porcelain", "--ignored=no"], cwd=repo, capture_output=True,
                               text=True).stdout
        # project-setup may add .gitignore lines and skill folders, never touch wip.txt or commit.
        self.assertIn("?? wip.txt", after)
        self.assertEqual((repo / "wip.txt").read_text(), "work in progress")
        log = subprocess.run(["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True).stdout
        self.assertEqual(len(log.splitlines()), 1)
        self.assertIn("?? wip.txt", before)

    def test_full_run_calls(self):
        repo = self.e.repo()
        r = self.e.run(str(repo), "--name", "Shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.e.calls()
        self.assertTrue(in_order(calls, [
            f"orca repo add --path {repo}",
            "orca skills install --skill orca-cli --agent universal",
            f"cotal space create shop --root {repo}",
            "cotal setup --destination local -H claude --runtime orca",
            "cotal up --no-manager --server nats://127.0.0.1:",
            "cotal svc add mgr --file",
            "cotal svc start mgr",
            "cotal spawn coordinator -d",
            "orca worktree create --repo id:repo-1 --name design-lead --parent-worktree path:",
            "cotal spawn design-lead -d",
        ]), "\n".join(calls))
        spawn_dl = [c for c in calls if c.startswith("cotal spawn design-lead")][0]
        self.assertIn("--cwd " + str(self.e.home / "orca" / "workspaces" / "design-lead"), spawn_dl)
        self.assertIn("--harness claude", spawn_dl)
        self.assertIn("--opt permission-mode=auto", spawn_dl)
        self.assertIn("--opt allow-dangerously-skip-permissions=", spawn_dl)
        agents = repo / ".cotal" / "agents"
        self.assertIn("model: opus", (agents / "coordinator.md").read_text())
        self.assertFalse((repo / ".agents").exists())  # skills are user-level (lane worktrees lack untracked files)
        self.assertIn("swift\topencode", (repo / ".cotal" / "lanes.tsv").read_text())
        self.assertIn("verify: ok", r.stdout)

    def test_cotal_calls_name_the_space(self):
        repo = self.e.repo()
        r = self.e.run(str(repo), "--name", "Shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        for c in self.e.calls():
            if c.startswith(("cotal svc", "cotal ps", "cotal spawn", "cotal up")):
                self.assertRegex(c, r"(-s|--space) shop\b", c)

    def test_mgr_definition_carries_proxy_env_refs(self):
        repo = self.e.repo()
        self.e.env["HTTPS_PROXY"] = "http://proxy:3128"
        r = self.e.run(str(repo), "--name", "Shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads((repo / ".cotal" / "mgr-service.json").read_text())
        self.assertEqual(d["space"], "shop")
        self.assertEqual(d["launch"]["adapter"], "manager-service")
        self.assertEqual(d["launch"]["options"], {"root": str(repo), "runtime": "orca"})
        self.assertEqual(d["envRefs"].get("HTTPS_PROXY"), "HTTPS_PROXY")
        self.assertNotIn("http_proxy", d["envRefs"])  # only names set in the caller's env

    def test_opencode_coordinator(self):
        repo = self.e.repo()
        r = self.e.run(str(repo), "--coordinator", "opencode/opencode/deepseek-v4.1-flash")
        self.assertEqual(r.returncode, 0, r.stderr)
        sp = [c for c in self.e.calls() if c.startswith("cotal spawn coordinator")][0]
        self.assertIn("--harness opencode", sp)
        self.assertIn("--no-events", sp)
        self.assertNotIn("permission-mode", sp)

    def test_rerun_is_noop(self):
        repo = self.e.repo()
        self.assertEqual(self.e.run(str(repo)).returncode, 0)
        n = len(self.e.calls())
        r = self.e.run(str(repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        new = self.e.calls()[n:]
        for verb in ("repo add", "space create", "cotal up", "svc add", "svc start", "spawn", "worktree create"):
            self.assertFalse(any(verb in c for c in new), f"rerun called {verb}: {new}")
        for line in r.stdout.splitlines():
            if ":" in line and not line.startswith(("verify", "summary", "  ")):
                self.assertRegex(line, r": (exists|skipped|updated|none)\b", line)

    def test_gitignore_gets_cotal_once(self):
        repo = self.e.repo()
        self.e.run(str(repo))
        self.e.run(str(repo))
        lines = (repo / ".gitignore").read_text().splitlines()
        self.assertEqual(lines.count(".cotal/"), 1)

    def test_no_start(self):
        repo = self.e.repo()
        r = self.e.run(str(repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(any(" up " in c or "spawn" in c or "worktree create" in c for c in self.e.calls()))

    def _ssh_stub(self):
        ssh = self.e.bin / "ssh"
        ssh.write_text("#!/bin/sh\necho \"ssh $*\" >> \"$STUB_LOG\"\ncat > /dev/null\n")
        ssh.chmod(0o755)

    def test_host_mode_copies_and_reexecs(self):
        self._ssh_stub()
        r = self.e.run("https://example.com/acme/shop.git", "--host", "dev@box", "--permission-mode",
                       "bypassPermissions")
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = [c for c in self.e.calls() if c.startswith("ssh ")]
        self.assertEqual(len(calls), 2, calls)
        self.assertIn("dev@box", calls[0])
        self.assertIn("tar -C ~/.local/share/lane-control -xf -", calls[0])
        self.assertIn("dev@box", calls[1])
        self.assertIn(".local/share/lane-control/scripts/project-setup", calls[1])
        self.assertIn("https://example.com/acme/shop.git", calls[1])
        self.assertIn("bypassPermissions", calls[1])
        self.assertNotIn("--host", calls[1])
        self.assertFalse(any(c.startswith(("orca ", "cotal ")) for c in self.e.calls()))

    def test_host_mode_uses_lane_ssh_config(self):
        # A trial or throwaway host lives in its own ssh config, not in the owner's ~/.ssh/config.
        self._ssh_stub()
        cfg = self.e.home / "trial_ssh_config"
        cfg.write_text("Host box\n")
        self.e.env["LANE_SSH_CONFIG"] = str(cfg)
        r = self.e.run("https://example.com/acme/shop.git", "--host", "dev@box")
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = [c for c in self.e.calls() if c.startswith("ssh ")]
        self.assertEqual(len(calls), 2, calls)
        for c in calls:
            self.assertTrue(c.startswith(f"ssh -F {cfg} dev@box "), c)

    def test_host_mode_refuses_relative_dir(self):
        self._ssh_stub()
        r = self.e.run("some/dir", "--host", "dev@box")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.e.calls(), [])

    def test_orca_cli_dir_added_to_path(self):
        m = load()
        d = self.e.home / ".orca" / "cli" / "bin"
        d.mkdir(parents=True)
        (d / "orca").write_text("#!/bin/sh\n")
        (d / "orca").chmod(0o755)
        with mock.patch.dict(m.os.environ, {"PATH": "/usr/bin", "HOME": str(self.e.home)}, clear=True):
            m.add_orca_cli_to_path()
            self.assertTrue(m.os.environ["PATH"].startswith(str(d) + ":"))

    def _deps_repo(self):
        repo = self.e.repo()
        (repo / "uv.lock").write_text("")
        (repo / "pyproject.toml").write_text("[project]\nname='a'\n")
        (repo / "frontend").mkdir()
        (repo / "frontend" / "package-lock.json").write_text("{}")
        return repo

    def test_setup_script_proposed_from_lockfiles(self):
        repo = self._deps_repo()
        r = self.e.run(str(repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("setup-script: proposed", r.stdout)
        text = (repo / ".cotal" / "orca.yaml.proposed").read_text()
        self.assertIn("uv sync", text)
        self.assertIn("(cd frontend && npm ci", text)
        self.assertIn("orca-setup-done", text.splitlines()[-1])
        self.assertFalse((repo / "orca.yaml").exists())  # never in the repo without being asked

    def test_committed_setup_script_is_kept(self):
        repo = self._deps_repo()
        (repo / "orca.yaml").write_text("scripts:\n  setup: |\n    make deps\n")
        git("add", "orca.yaml", cwd=repo)
        git("commit", "-q", "-m", "orca", cwd=repo)
        r = self.e.run(str(repo), "--no-start")
        self.assertIn("setup-script: exists", r.stdout)
        self.assertFalse((repo / ".cotal" / "orca.yaml.proposed").exists())

    def test_write_orca_yaml_writes_but_never_commits(self):
        repo = self._deps_repo()
        head = git("rev-parse", "HEAD", cwd=repo)
        r = self.e.run(str(repo), "--no-start", "--write-orca-yaml")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("uv sync", (repo / "orca.yaml").read_text())
        self.assertEqual(git("rev-parse", "HEAD", cwd=repo), head)
        self.assertIn("setup-script: written", r.stdout)

    def test_no_deps_no_proposal(self):
        repo = self.e.repo()
        r = self.e.run(str(repo), "--no-start")
        self.assertIn("setup-script: none", r.stdout)

    def test_design_lead_waits_for_setup(self):
        repo = self.e.repo()
        self.e.set_state(setup_tab=1)
        r = self.e.run(str(repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("setup design-lead: done", r.stdout)
        self.assertLess(r.stdout.index("setup design-lead: done"), r.stdout.index("agent design-lead"))

    def _closed(self):
        return [c.split("--terminal ")[1].split()[0] for c in self.e.calls() if c.startswith("orca terminal close")]

    def test_setup_tab_closed_after_the_marker_and_the_agent_start(self):
        repo = self.e.repo()
        self.e.set_state(setup_tab=1)
        r = self.e.run(str(repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.e.calls()
        self.assertEqual(sorted(self._closed()), ["term_1", "term_2"])  # the Setup tab and the plain shell
        i_close = min(i for i, c in enumerate(calls) if c.startswith("orca terminal close"))
        i_spawn = next(i for i, c in enumerate(calls) if c.startswith("cotal spawn") and "design-lead" in c)
        self.assertLess(i_spawn, i_close)  # never an empty worktree when the agent starts

    def test_setup_tab_found_by_runner_title_too(self):
        repo = self.e.repo()
        self.e.set_state(setup_tab="runner")
        r = self.e.run(str(repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(self._closed()), ["term_1", "term_2"])

    def test_startup_shell_closed_whatever_its_prompt_looks_like(self):
        repo = self.e.repo()
        self.e.set_state(setup_tab=1, shell_preview="app on \ue0a0 main \u276f")
        r = self.e.run(str(repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(self._closed()), ["term_1", "term_2"])

    def test_agent_tab_is_never_closed_even_without_identity(self):
        repo = self.e.repo()
        self.e.set_state(setup_tab=1)
        r = self.e.run(str(repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("agent_design-lead", self._closed())  # the stub's agent tab: empty preview, no identity, no cotal- title

    def test_shell_with_empty_preview_is_left_open(self):
        repo = self.e.repo()
        self.e.set_state(shell_preview="")
        r = self.e.run(str(repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._closed(), [])

    def test_shell_with_output_is_left_open(self):
        repo = self.e.repo()
        self.e.set_state(shell_preview="make test\nrunning...\n\u276f")
        r = self.e.run(str(repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._closed(), [])

    def test_running_setup_tab_is_kept_when_the_marker_never_appears(self):
        repo = self.e.repo()
        self.e.set_state(setup_tab="runner", setup_never_done=1)
        r = self.e.run(str(repo), env_extra={"LANE_SETUP_TIMEOUT": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("setup design-lead: timeout", r.stdout)
        # the startup shell step must not close the running setup script, and nothing closes it after the timeout
        self.assertEqual(self._closed(), ["term_2"])

    def test_reviewer_persona_rendered(self):
        repo = self.e.repo()
        r = self.e.run(str(repo), "--reviewer", "opencode/opencode/glm-5", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (repo / ".cotal" / "agents" / "reviewer.md").read_text()
        self.assertIn("agent: opencode", text)
        self.assertIn("model: opencode/glm-5", text)
        self.assertNotIn("{{", text)
        coord = (repo / ".cotal" / "agents" / "coordinator.md").read_text()
        self.assertIn(str(repo / ".cotal" / "agents" / "reviewer.md"), coord)

    def test_design_lead_tab_comes_before_the_shell(self):
        # Orca opens a shell in a new worktree; it is closed only after the agent started (an empty worktree may
        # refuse the spawn), and a fresh shell for the owner comes last.
        repo = self.e.repo()
        r = self.e.run(str(repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.e.calls()
        i_close = next(i for i, c in enumerate(calls) if c.startswith("orca terminal close"))
        i_spawn = next(i for i, c in enumerate(calls) if c.startswith("cotal spawn") and "design-lead" in c)
        i_create = next(i for i, c in enumerate(calls) if c.startswith("orca terminal create"))
        self.assertLess(i_spawn, i_close)
        self.assertLess(i_close, i_create)
        self.assertIn("--terminal term_1", calls[i_close])
        self.assertIn("design-lead", calls[i_create])

    def test_no_design_lead_starts_only_the_coordinator(self):
        repo = self.e.repo()
        r = self.e.run(str(repo), "--no-design-lead")
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.e.calls()
        self.assertTrue(any(c.startswith("cotal spawn coordinator") for c in calls))
        self.assertFalse(any("design-lead" in c for c in calls), "\n".join(calls))
        self.assertFalse((repo / ".cotal" / "agents" / "design-lead.md").exists())
        self.assertIn("verify: ok", r.stdout)
        self.assertNotIn("design-lead", r.stdout.split("verify:")[1])

    def test_no_design_lead_coordinator_routes_ui_itself(self):
        repo = self.e.repo()
        self.assertEqual(self.e.run(str(repo), "--no-design-lead", "--no-start").returncode, 0)
        coord = " ".join((repo / ".cotal" / "agents" / "coordinator.md").read_text().split())
        self.assertNotIn("route UI work and UI reviews through it", coord)
        self.assertNotIn("design lead's review", coord)
        self.assertIn("no design lead", coord)
        self.assertNotIn("{{", coord)
        with_lead = self.e.repo("other")
        self.assertEqual(self.e.run(str(with_lead), "--no-start").returncode, 0)
        coord = " ".join((with_lead / ".cotal" / "agents" / "coordinator.md").read_text().split())
        self.assertIn("route UI work and UI reviews through it", coord)
        self.assertIn("design lead's review", coord)

    def test_untrusted_folder_stops_before_claude_starts(self):
        # Claude Code asks "Do you trust this folder?" on first start there; an agent tab can't answer it.
        repo = self.e.repo()
        self.e.trust(repo, accepted=False)
        r = self.e.run(str(repo))
        self.assertEqual(r.returncode, 1)
        self.assertIn("trust", r.stderr)
        self.assertIn(f"cd {repo} && claude", r.stderr)
        self.assertFalse(any(c.startswith(("cotal spawn", "orca repo add")) for c in self.e.calls()))

    def test_trust_folder_flag_runs_without_asking(self):
        repo = self.e.repo()
        self.e.trust(repo, accepted=False)
        r = self.e.run(str(repo), "--trust-folder")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("claude-trust: created", r.stdout)
        self.assertTrue(json.loads((self.e.home / ".claude.json").read_text())
                        ["projects"][str(repo)]["hasTrustDialogAccepted"])

    def test_untrusted_folder_is_fine_without_claude_agents(self):
        repo = self.e.repo()
        self.e.trust(repo, accepted=False)
        r = self.e.run(str(repo), "--coordinator", "opencode/opencode/glm-5", "--no-design-lead")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_rerun_leaves_existing_tabs_alone(self):
        repo = self.e.repo()
        self.assertEqual(self.e.run(str(repo)).returncode, 0)
        self.e.log.write_text("")
        self.assertEqual(self.e.run(str(repo)).returncode, 0)
        self.assertFalse(any(c.startswith(("orca terminal close", "orca terminal create")) for c in self.e.calls()))

    def test_orca_ide_on_path_preferred_over_desktop_shim(self):
        # A packaged Orca (e.g. NixOS) puts its CLI on PATH as orca-ide; the shim only exists once the app has run.
        m = load()
        shim = self.e.home / ".config" / "orca" / "linux-orca-cli-shim"
        pkg = self.e.home / "pkg" / "bin"
        for d in (shim, pkg):
            d.mkdir(parents=True)
        for f in (shim / "orca", pkg / "orca-ide"):
            f.write_text("#!/bin/sh\n")
            f.chmod(0o755)
        with mock.patch.dict(m.os.environ, {"PATH": f"{pkg}:/usr/bin", "HOME": str(self.e.home)}, clear=True):
            self.assertEqual(m.orca_cli(), str(pkg / "orca-ide"))

    def test_desktop_orca_cli_dir_added_to_path(self):
        # A workstation has no ~/.orca/cli/bin; the desktop shim's folder goes first, ahead of OrcaSlicer's `orca`.
        m = load()
        d = self.e.home / ".config" / "orca" / "linux-orca-cli-shim"
        d.mkdir(parents=True)
        (d / "orca").write_text("#!/bin/sh\n")
        (d / "orca").chmod(0o755)
        with mock.patch.dict(m.os.environ, {"PATH": "/usr/bin", "HOME": str(self.e.home)}, clear=True):
            m.add_orca_cli_to_path()
            self.assertTrue(m.os.environ["PATH"].startswith(str(d) + ":"))

    def test_mgr_definition_points_cotal_at_orca_cli(self):
        # Cotal's Orca driver runs `orca-ide`, then `orca`, from PATH. A service can't be given COTAL_* names
        # (Cotal's schema reserves them), so mgr gets project-setup's PATH, which starts with the Orca CLI's folder.
        repo = self.e.repo()
        r = self.e.run(str(repo), "--name", "Shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads((repo / ".cotal" / "mgr-service.json").read_text())
        self.assertEqual(d["envRefs"].get("PATH"), "PATH")
        reserved = re.compile(r"^(COTAL_|CLAUDE_CODE_|CLAUDECODE$)")
        self.assertFalse([k for k in list(d["envRefs"]) + list(d["envRefs"].values()) if reserved.match(k)])

    # --- review fixes ---------------------------------------------------------------------------------------
    def test_orca_cli_env_wins_over_path_orca(self):
        slicer = self.e.home / "slicer"
        slicer.mkdir()
        (slicer / "orca").write_text("#!/bin/sh\necho SLICER >> \"$STUB_LOG\"\nsleep 30\n")
        (slicer / "orca").chmod(0o755)
        (self.e.bin / "orca").unlink()
        self.e.env["PATH"] = f"{slicer}:{self.e.env['PATH']}"
        self.e.env["ORCA_CLI"] = str(STUB.parent / "orca-via-env")
        link = STUB.parent / "orca-via-env"
        if not link.exists():
            link.symlink_to("fake.py")
        repo = self.e.repo()
        r = self.e.run(str(repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("SLICER", self.e.log.read_text())

    def test_failed_claude_setup_is_retried(self):
        repo = self.e.repo()
        self.e.set_state(setup_fails=1)
        self.assertNotEqual(self.e.run(str(repo)).returncode, 0)
        r = self.e.run(str(repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sum(1 for c in self.e.calls() if c.startswith("cotal setup")), 2)

    def test_claude_setup_when_project_later_uses_claude(self):
        repo = self.e.repo()
        oc = "opencode/opencode/x"
        tiers = self.e.home / "tiers.tsv"
        tiers.write_text("tier\tharness\tmodel\tpermission\nswift\topencode\topencode/x\t-\n")
        self.assertEqual(self.e.run(str(repo), "--coordinator", oc, "--design-lead", oc, "--reviewer", oc,
                                    "--tiers", str(tiers)).returncode, 0)
        self.assertFalse(any(c.startswith("cotal setup") for c in self.e.calls()))
        r = self.e.run(str(repo), "--coordinator", "claude/opus", "--design-lead", oc, "--reviewer", oc,
                       "--tiers", str(tiers))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any(c.startswith("cotal setup") for c in self.e.calls()))

    def test_skills_are_installed_for_the_user(self):
        repo = self.e.repo()
        self.assertEqual(self.e.run(str(repo)).returncode, 0)
        skill = self.e.home / ".agents" / "skills" / "orca-cotal-lanes"
        self.assertTrue((skill / "SKILL.md").exists())
        self.assertTrue((skill / "brief.md").exists())
        self.assertTrue((self.e.home / ".claude" / "skills" / "orca-cotal-lanes").is_symlink())
        self.assertTrue((self.e.home / ".config" / "opencode" / "skills" / "orca-cotal-lanes").is_symlink())
        self.assertTrue(any(c.startswith("orca skills install --skill orca-cli") and "--local" not in c
                            for c in self.e.calls()))

    def test_partial_skill_install_recovers(self):
        repo = self.e.repo()
        self.assertEqual(self.e.run(str(repo)).returncode, 0)
        (self.e.home / ".agents" / "skills" / "orca-cotal-lanes" / "brief.md").unlink()
        r = self.e.run(str(repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.e.home / ".agents" / "skills" / "orca-cotal-lanes" / "brief.md").exists())
        self.assertIn("skills: updated", r.stdout)

    def test_preflight_requires_cotal_orca_extension(self):
        repo = self.e.repo()
        self.e.set_state(orca_ext=False)
        r = self.e.run(str(repo))
        self.assertEqual(r.returncode, 1)
        self.assertIn("@cotal-ai/orca", r.stderr)
        self.assertFalse(any("repo add" in c or "space create" in c for c in self.e.calls()))
        self.assertFalse((repo / ".gitignore").exists())

    def test_missing_tiers_file_is_a_clean_error(self):
        repo = self.e.repo()
        r = self.e.run(str(repo), "--tiers", str(self.e.home / "nope.tsv"))
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_never_inits_inside_a_git_dir(self):
        repo = self.e.repo()
        r = self.e.run(str(repo / ".git"))
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((repo / ".git" / ".git").exists())
        log = subprocess.run(["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True).stdout
        self.assertEqual(len(log.splitlines()), 1)

    def test_port_is_recorded_and_reused(self):
        repo = self.e.repo()
        self.assertEqual(self.e.run(str(repo), "--port", "14999").returncode, 0)
        rec = json.loads((repo / ".cotal" / "lane-control.json").read_text())
        self.assertEqual(rec["port"], 14999)
        r = self.e.run(str(repo))
        self.assertIn("nats://127.0.0.1:14999", r.stdout)

    def test_busy_port_moves_to_next_free(self):
        import socket
        m = load()
        s = socket.socket()
        busy = m.free_port(14900)  # a free port inside the broker range, then occupy it
        s.bind(("127.0.0.1", busy))
        s.listen()
        try:
            self.assertNotEqual(m.free_port(busy), busy)
        finally:
            s.close()

    def test_personas_carry_mode_and_tier_path(self):
        repo = self.e.repo()
        oc = "opencode/opencode/x"
        self.assertEqual(self.e.run(str(repo), "--coordinator", oc, "--permission-mode", "bypassPermissions").returncode, 0)
        text = (repo / ".cotal" / "agents" / "coordinator.md").read_text()
        self.assertIn("`bypassPermissions`", text)
        self.assertIn(str(repo / ".cotal" / "lanes.tsv"), text)
        self.assertNotIn("{{", text)

    def test_host_mode_quotes_tilde_paths(self):
        self._ssh_stub()
        r = self.e.run("~/My Repo;touch x", "--host", "dev@box")
        self.assertEqual(r.returncode, 0, r.stderr)
        remote = [c for c in self.e.calls() if c.startswith("ssh ")][1]
        self.assertIn("~/'My Repo;touch x'", remote)

    def test_bad_permission_mode(self):
        repo = self.e.repo()
        r = self.e.run(str(repo), "--permission-mode", "yolo")
        self.assertEqual(r.returncode, 2)


class RefreshTests(unittest.TestCase):
    """--refresh: an onboarded project picks up current personas and skill without starting anything."""
    def setUp(self):
        self.e = Env()
        self.addCleanup(self.e.close)
        self.repo = self.e.repo()

    def setup_project(self, *args):
        r = self.e.run(str(self.repo), *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.e.log.write_text("")

    def refresh(self, *args):
        return self.e.run(str(self.repo), "--refresh", *args, adapt=True)

    def persona(self, name="coordinator"):
        return self.repo / ".cotal" / "agents" / f"{name}.md"

    def test_stale_persona_is_rewritten_and_its_running_agent_named(self):
        self.setup_project()
        self.persona().write_text(self.persona().read_text() + "old line\n")
        r = self.refresh()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("old line", self.persona().read_text())
        self.assertIn("persona coordinator: updated", r.stdout)
        self.assertIn("persona design-lead: exists", r.stdout)
        self.assertRegex(r.stdout, r"restart: needed coordinator \(persona changed\)")
        self.assertNotIn("design-lead (", r.stdout)

    def test_starts_nothing_and_commits_nothing(self):
        self.setup_project()
        self.persona().write_text(self.persona().read_text() + "stale\n")
        before = git("rev-parse", "HEAD", cwd=self.repo)
        self.refresh()
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), before)
        self.assertEqual(self.e.agent_runs(), [])
        started = [c for c in self.e.calls() if c.startswith(("orca repo add", "orca worktree", "cotal spawn",
                                                              "cotal up", "cotal space", "cotal svc start",
                                                              "cotal setup"))]
        self.assertEqual(started, [])

    def test_projects_own_tier_table_is_kept(self):
        self.setup_project()
        table = self.repo / ".cotal" / "lanes.tsv"
        table.write_text(table.read_text() + "# project's own row\n")
        own = table.read_text()
        r = self.refresh()
        self.assertEqual(table.read_text(), own)
        self.assertIn("kept (differs from the defaults", r.stdout)

    def test_nothing_changed_says_no_restart(self):
        self.setup_project()
        r = self.refresh()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("restart: none nothing changed", r.stdout)
        self.assertNotIn("cotal ps", "\n".join(self.e.calls()))

    def test_stale_persona_whose_agent_is_not_running_needs_no_restart(self):
        self.setup_project()
        self.persona("reviewer").write_text(self.persona("reviewer").read_text() + "stale\n")
        r = self.refresh()
        self.assertIn("restart: none no changed role is running", r.stdout)

    def test_changed_skill_names_coordinator_and_design_lead(self):
        self.setup_project()
        (self.e.home / ".agents" / "skills" / "orca-cotal-lanes" / "SKILL.md").write_text("old\n")
        r = self.refresh()
        self.assertIn("skills: updated", r.stdout)
        self.assertIn("coordinator (skill changed); design-lead (skill changed)", r.stdout)

    def test_keeps_each_personas_harness_model_and_mode(self):
        self.setup_project("--coordinator", "claude/sonnet", "--permission-mode", "default", "--no-design-lead")
        self.persona().write_text(self.persona().read_text() + "stale\n")
        r = self.refresh()
        text = self.persona().read_text()
        self.assertRegex(text, r"(?m)^model: sonnet$")
        self.assertIn('"permission-mode": "default"', text)
        self.assertFalse(self.persona("design-lead").exists())
        self.assertIn("claude/sonnet", r.stdout)
        self.assertIn("This project has no design lead", text)

    def test_project_not_set_up_is_refused(self):
        r = self.refresh()
        self.assertEqual(r.returncode, 1)
        self.assertIn("not set up", r.stderr)
        self.assertFalse((self.repo / ".cotal").exists())

    def test_persona_without_agent_and_model_is_refused_unchanged(self):
        self.setup_project()
        self.persona().write_text("stale\n")
        r = self.refresh()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.persona().read_text(), "stale\n")

    def test_opencode_personas_keep_their_recorded_mode(self):
        self.setup_project("--coordinator", "opencode/m", "--design-lead", "opencode/m", "--reviewer", "opencode/m",
                           "--permission-mode", "default")
        self.persona().write_text(self.persona().read_text() + "stale\n")
        r = self.refresh()
        self.assertEqual(r.returncode, 0, r.stderr)
        for n in ("coordinator", "design-lead"):
            self.assertIn("permission mode is `default`", self.persona(n).read_text())

    def test_each_persona_keeps_its_own_mode(self):
        self.setup_project()
        rv = self.persona("reviewer")
        rv.write_text(rv.read_text().replace('"permission-mode": "auto"', '"permission-mode": "default"'))
        (self.repo / ".cotal" / "agents" / "zz-extra.md").write_text('"permission-mode": "bypassPermissions"\n')
        self.refresh()
        self.assertIn('"permission-mode": "default"', rv.read_text())
        self.assertIn('"permission-mode": "auto"', self.persona().read_text())

    def test_missing_reviewer_is_made_and_the_tier_table_kept(self):
        self.setup_project()
        self.persona("reviewer").unlink()
        table = self.repo / ".cotal" / "lanes.tsv"
        table.write_text(table.read_text() + "# own row\n")
        own = table.read_text()
        r = self.refresh()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("persona reviewer: created", r.stdout)
        self.assertEqual(table.read_text(), own)

    def test_bad_persona_changes_nothing(self):
        self.setup_project()
        self.persona().write_text(self.persona().read_text() + "stale\n")
        self.persona("reviewer").write_text("broken\n")
        r = self.refresh()
        self.assertEqual(r.returncode, 1)
        self.assertIn("stale", self.persona().read_text())
        self.assertNotIn("run without --refresh", r.stderr)

    def test_running_reviewer_seat_is_named(self):
        self.setup_project()
        self.persona("reviewer").write_text(self.persona("reviewer").read_text() + "stale\n")
        self.e.set_state(agents=[{"name": "lane-3-review", "cwd": "/x"}])
        r = self.refresh()
        self.assertIn("restart: needed lane-3-review (persona changed)", r.stdout)

    def test_not_a_repository_is_refused_and_nothing_cloned(self):
        r = self.e.run(str(self.e.home / "nowhere"), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("--refresh only works", r.stderr)

    def test_cannot_combine_with_readapt(self):
        r = self.e.run(str(self.repo), "--refresh", "--readapt", adapt=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
