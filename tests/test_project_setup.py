"""project-setup against stub orca/cotal executables (tests/stubs/fake.py) in a temporary HOME."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import re
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
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


def store_key(repo):
    """<folder>-<10 hex of sha256 of the real path>, as scripts/project-setup keys the store."""
    import hashlib
    return f"{Path(repo).name}-{hashlib.sha256(str(Path(repo).resolve()).encode()).hexdigest()[:10]}"


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
        self.env.pop("XDG_CONFIG_HOME", None)  # the store is under the temp HOME
        self.env.pop("OPENCODE_API_KEY", None)  # a machine set up as docs/orca.md says has it
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

    def store(self, repo):
        """The project's settings store: outside the repo, under the temp HOME."""
        return self.home / ".config" / "lane-control" / "projects" / store_key(repo)

    def settings(self, repo):
        return json.loads((self.store(repo) / "settings.json").read_text())

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


class GitlinkTests(unittest.TestCase):
    def test_a_gitlink_whose_folder_is_missing_is_skipped_not_a_crash(self):
        e = Env()
        self.addCleanup(e.close)
        repo = e.repo()
        git("update-index", "--add", "--cacheinfo", "160000,1234567890123456789012345678901234567890,sub", cwd=repo)
        m = load()
        self.assertEqual([n for n in m.repo_private_names(repo, "") if n.startswith("sub")], [])


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
        answers = {"side_effect": answer} if isinstance(answer, list) else {"return_value": answer}
        with mock.patch.dict(os.environ, self.e.env, clear=True), mock.patch("sys.stdin", Tty()), \
                mock.patch("builtins.input", **answers) as ask, contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            status = m.main([str(self.repo), "--opencode", "hosted", *args])
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
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        status, out, ask = self.in_terminal("y", "--name", "Shop")
        self.assertEqual(status, 0)
        ask.assert_called_once()
        self.assertTrue(in_order(self.e.calls(), ["claude -p --model sonnet", "claude -p --model opus",
                                                  "orca repo add", "cotal spawn coordinator"]), self.e.calls())
        self.assertEqual(sorted(self.committed_paths()), sorted([".gitignore", "AGENTS.md", ADAPTED_FILE]))
        message = git("log", "-1", "--format=%B", cwd=self.repo)
        self.assertNotRegex(message, r"(?i)co-authored|claude|generated")
        self.assertEqual(git("log", "-1", "--format=%an <%ae>", cwd=self.repo), "t <t@t>")

    def test_no_stops_and_leaves_files_uncommitted(self):
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        status, out, ask = self.in_terminal("n")
        self.assertEqual(status, 3)
        self.assertEqual(self.head_count(), 1)
        self.assertTrue((self.repo / ADAPTED_FILE).exists())
        self.assertFalse(self.orca_setup_ran())

    def test_empty_answer_is_no(self):
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal("")[0], 3)
        self.assertEqual(self.head_count(), 1)

    def test_no_terminal_never_commits_or_runs_orca_setup(self):
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn(f"added: {ADAPTED_FILE}", r.stdout)
        self.assertEqual(self.head_count(), 1)
        self.assertFalse(self.orca_setup_ran())

    def test_commit_takes_only_our_paths(self):
        (self.repo / "wip.txt").write_text("work in progress")
        (self.repo / "staged.txt").write_text("the owner's staged work")
        git("add", "staged.txt", cwd=self.repo)
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal("yes")[0], 0)
        self.assertEqual(sorted(self.committed_paths()), sorted([".gitignore", "AGENTS.md", ADAPTED_FILE]))
        status = git("status", "--porcelain", cwd=self.repo)
        self.assertIn("?? wip.txt", status)
        self.assertIn("A  staged.txt", status)

    # --- one run: orca.yaml and the push, asked after the file-list commit -------------------------------------
    def with_origin(self, reject_push=False):
        """A local bare origin that holds the repo's main, with origin/HEAD set."""
        bare = self.e.home / "origin.git"
        git("init", "-q", "--bare", "-b", "main", str(bare), cwd=self.e.home)
        git("remote", "add", "origin", str(bare), cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        git("fetch", "-q", "origin", cwd=self.repo)
        git("remote", "set-head", "origin", "main", cwd=self.repo)
        if reject_push:
            hook = bare / "hooks" / "pre-receive"
            hook.write_text("#!/bin/sh\necho refused >&2\nexit 1\n")
            hook.chmod(0o755)
        return bare

    def with_lockfile(self):
        (self.repo / "uv.lock").write_text("")
        (self.repo / "pyproject.toml").write_text("[project]\nname='a'\n")

    def origin_main(self, bare):
        return git("rev-parse", "main", cwd=bare)

    def started(self):
        return any(c.startswith("cotal spawn coordinator") for c in self.e.calls())

    def test_one_run_commits_orca_yaml_pushes_and_starts_the_agents(self):
        bare = self.with_origin()
        self.with_lockfile()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        status, out, ask = self.in_terminal(["y", "y"])
        self.assertEqual(status, 0, out)
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(self.committed_paths(), ["orca.yaml"])  # by explicit path, alone
        self.assertEqual(sorted(git("show", "--name-only", "--format=", "HEAD~1", cwd=self.repo).splitlines()),
                         sorted([".gitignore", "AGENTS.md", ADAPTED_FILE]))
        self.assertEqual(self.origin_main(bare), git("rev-parse", "HEAD", cwd=self.repo))
        self.assertIn("uv sync", out)  # its text was shown before the question
        self.assertIn("No orca.yaml", out)
        self.assertTrue(self.started())

    def test_the_orca_yaml_is_not_in_the_file_list(self):
        self.with_origin()
        self.with_lockfile()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        _, out, _ = self.in_terminal(["y", "n"])
        listed = out.split("Files added or changed", 1)[1].split("commit: created", 1)[0]
        self.assertNotIn("orca.yaml", listed)

    def test_no_to_the_second_question_commits_and_pushes_nothing_more_and_still_starts(self):
        bare = self.with_origin()
        self.with_lockfile()
        old = self.origin_main(bare)
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        status, out, ask = self.in_terminal(["y", "n"])
        self.assertEqual(status, 0, out)
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(self.origin_main(bare), old)
        self.assertEqual(sorted(self.committed_paths()), sorted([".gitignore", "AGENTS.md", ADAPTED_FILE]))
        self.assertNotIn("orca.yaml", git("ls-files", cwd=self.repo))
        self.assertIn("will not get", out)
        self.assertTrue(self.started())

    def test_no_to_the_file_list_asks_nothing_more_and_starts_nothing(self):
        bare = self.with_origin()
        self.with_lockfile()
        old = self.origin_main(bare)
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        status, _, ask = self.in_terminal(["n", "y"])
        self.assertEqual(status, 3)
        ask.assert_called_once()
        self.assertEqual(self.origin_main(bare), old)
        self.assertEqual(self.head_count(), 1)
        self.assertFalse((self.repo / "orca.yaml").exists())
        self.assertFalse(self.started())

    def test_a_differing_untracked_orca_yaml_is_left_alone_and_the_push_is_still_asked(self):
        bare = self.with_origin()
        self.with_lockfile()
        (self.repo / "orca.yaml").write_text("scripts:\n  setup: |\n    make deps\n")
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        status, out, ask = self.in_terminal(["y", "y"])
        self.assertEqual(status, 0, out)
        self.assertEqual((self.repo / "orca.yaml").read_text(), "scripts:\n  setup: |\n    make deps\n")
        self.assertNotIn("orca.yaml", git("ls-files", cwd=self.repo))
        self.assertIn("differs", out)
        self.assertEqual(self.origin_main(bare), git("rev-parse", "HEAD", cwd=self.repo))  # the onboarding commit
        self.assertTrue(self.started())

    def test_an_identical_untracked_orca_yaml_from_a_declined_run_is_offered_again(self):
        self.with_origin()
        self.with_lockfile()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.in_terminal(["y", "n"], "--write-orca-yaml")
        self.assertTrue((self.repo / "orca.yaml").exists())
        self.assertNotIn("orca.yaml", git("ls-files", cwd=self.repo))
        status, out, ask = self.in_terminal(["y"])  # the section is current: only the orca.yaml question
        self.assertEqual(status, 0, out)
        self.assertEqual(self.committed_paths(), ["orca.yaml"])

    def test_a_tracked_orca_yaml_only_asks_about_the_push(self):
        bare = self.with_origin()
        self.with_lockfile()
        (self.repo / "orca.yaml").write_text("scripts:\n  setup: |\n    make deps\n")
        git("add", "orca.yaml", cwd=self.repo)
        git("commit", "-q", "-m", "orca", cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        status, out, ask = self.in_terminal(["y", "y"])
        self.assertEqual(status, 0, out)
        self.assertEqual(ask.call_count, 2)
        self.assertNotIn("No orca.yaml", out)
        self.assertEqual(self.origin_main(bare), git("rev-parse", "HEAD", cwd=self.repo))

    def test_another_branch_is_not_pushed_and_nothing_is_asked(self):
        bare = self.with_origin()
        self.with_lockfile()
        old = self.origin_main(bare)
        git("checkout", "-q", "-b", "feature", cwd=self.repo)
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        status, out, ask = self.in_terminal(["y", "y"])
        self.assertEqual(status, 0, out)
        ask.assert_called_once()
        self.assertEqual(self.origin_main(bare), old)
        self.assertNotIn("orca.yaml", git("ls-files", cwd=self.repo))
        self.assertIn("feature", out)
        self.assertIn("will not get", out)
        self.assertTrue(self.started())

    def test_a_failed_push_still_starts_and_warns(self):
        bare = self.with_origin(reject_push=True)
        self.with_lockfile()
        old = self.origin_main(bare)
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        status, out, ask = self.in_terminal(["y", "y"])
        self.assertEqual(status, 0, out)
        self.assertEqual(self.origin_main(bare), old)
        self.assertIn("push failed", out)
        self.assertIn("will not get", out)
        self.assertTrue(self.started())

    def test_an_empty_origin_gets_the_first_push_of_the_current_branch(self):
        bare = self.e.home / "origin.git"
        git("init", "-q", "--bare", "-b", "main", str(bare), cwd=self.e.home)
        git("remote", "add", "origin", str(bare), cwd=self.repo)
        self.with_lockfile()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        status, out, ask = self.in_terminal(["y", "y"])
        self.assertEqual(status, 0, out)
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(self.committed_paths(), ["orca.yaml"])
        self.assertEqual(self.origin_main(bare), git("rev-parse", "HEAD", cwd=self.repo))
        self.assertNotIn("could not tell", out)

    def test_no_origin_offers_the_commit_alone_and_does_not_say_lanes_miss_it(self):
        self.with_lockfile()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        status, out, ask = self.in_terminal(["y", "y"])
        self.assertEqual(status, 0, out)
        self.assertEqual(ask.call_count, 2)
        self.assertNotIn("push", ask.call_args_list[1][0][0].lower())
        self.assertEqual(self.committed_paths(), ["orca.yaml"])
        self.assertNotIn("will not get", out)
        self.assertTrue(self.started())

    def test_no_origin_and_a_no_says_lanes_miss_the_setup_script(self):
        self.with_lockfile()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        status, out, _ = self.in_terminal(["y", "n"])
        self.assertEqual(status, 0, out)
        self.assertNotIn("orca.yaml", git("ls-files", cwd=self.repo))
        self.assertIn("will not get a setup script", out)
        self.assertTrue(self.started())

    def test_no_origin_and_no_draft_says_nothing_about_lanes(self):
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        ask.assert_called_once()
        self.assertNotIn("will not get", out)

    def test_another_branch_that_origin_already_has_everything_on_says_nothing(self):
        bare = self.with_origin()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)  # asks the push on main
        git("push", "-q", "origin", "main", cwd=self.repo)
        git("fetch", "-q", "origin", cwd=self.repo)
        git("checkout", "-q", "-b", "feature", cwd=self.repo)
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        ask.assert_not_called()
        self.assertNotIn("will not get", out)

    def test_no_start_still_opts_out_of_starting(self):
        self.with_origin()
        self.with_lockfile()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        status, out, _ = self.in_terminal(["y", "y"], "--no-start")
        self.assertEqual(status, 0, out)
        self.assertEqual(self.committed_paths(), ["orca.yaml"])
        self.assertFalse(self.started())

    # --- a second run after the first ----------------------------------------------------------------------------
    def committed_lockfile_origin(self, **kw):
        self.with_lockfile()
        git("add", "uv.lock", "pyproject.toml", cwd=self.repo)
        git("commit", "-q", "-m", "deps", cwd=self.repo)
        return self.with_origin(**kw)

    def test_a_second_run_of_a_finished_onboarding_changes_and_asks_nothing(self):
        bare = self.committed_lockfile_origin()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal(["y", "y"])[0], 0)
        head, remote, runs, n = (git("rev-parse", "HEAD", cwd=self.repo), self.origin_main(bare),
                                 len(self.e.agent_runs()), len(self.e.calls()))
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        ask.assert_not_called()
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), head)
        self.assertEqual(self.origin_main(bare), remote)
        self.assertEqual(git("status", "--porcelain", cwd=self.repo), "")
        self.assertEqual(len(self.e.agent_runs()), runs)
        self.assertEqual((self.repo / ".gitignore").read_text().splitlines().count(".cotal/"), 1)
        self.assertEqual((self.repo / "AGENTS.md").read_text().count(load().SECTION_BEGIN), 1)
        for verb in ("repo add", "space create", "cotal up", "svc add", "svc start", "spawn", "worktree create"):
            self.assertFalse(any(verb in c for c in self.e.calls()[n:]), f"second run called {verb}")
        self.assertNotIn("will not get", out)

    def test_an_aborted_adapter_runs_again_and_nothing_partial_is_reviewed_or_committed(self):
        self.e.script_agents(sonnet=[{"write": {ADAPTED_FILE: "half\n"}, "exit": 1}, WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.models(), ["sonnet"])  # no review of a half-written folder
        self.assertEqual(self.head_count(), 1)
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        self.assertEqual(self.models(), ["sonnet", "sonnet", "opus"])  # the adapter ran again, then the review
        self.assertIn("did not finish", out)
        self.assertEqual((self.repo / ADAPTED_FILE).read_text(), "run the tests\n")
        self.assertEqual(sorted(self.committed_paths()), sorted([".gitignore", "AGENTS.md", ADAPTED_FILE]))

    def test_an_interrupted_fix_round_runs_the_adapter_again(self):
        self.e.script_agents(sonnet=[WRITE, {"write": {ADAPTED_FILE: "half fix\n"}, "exit": 1}, WRITE],
                             opus=[FIX, PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.in_terminal("y")[0], 0)
        self.assertEqual(self.models(), ["sonnet", "opus", "sonnet", "sonnet", "opus"])

    def test_a_declined_file_list_is_reviewed_again_not_rewritten(self):
        self.e.script_agents(sonnet=[WRITE], opus=[PASS, PASS])
        self.assertEqual(self.in_terminal("n")[0], 3)
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        self.assertEqual(self.models(), ["sonnet", "opus", "opus"])
        self.assertEqual(sorted(self.committed_paths()), sorted([".gitignore", "AGENTS.md", ADAPTED_FILE]))

    def test_a_hand_fix_survives_an_unrelated_commit_before_the_re_run(self):
        self.e.script_agents(sonnet=[WRITE, WRITE], opus=[PASS, PASS])
        self.assertEqual(self.in_terminal("n")[0], 3)
        (self.repo / ADAPTED_FILE).write_text("owner's hand edit\n")
        (self.repo / "other.txt").write_text("x")
        git("add", "other.txt", cwd=self.repo)
        git("commit", "-q", "-m", "unrelated", cwd=self.repo)
        status, out, _ = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        self.assertEqual(self.models(), ["sonnet", "opus", "opus"])
        self.assertNotIn("did not finish", out)
        self.assertEqual((self.repo / ADAPTED_FILE).read_text(), "owner's hand edit\n")

    def test_owners_own_unpushed_commit_is_not_asked_about_nor_pushed(self):
        bare = self.committed_lockfile_origin()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal(["y", "y"])[0], 0)
        (self.repo / "wip.txt").write_text("wip")
        git("add", "wip.txt", cwd=self.repo)
        git("commit", "-q", "-m", "owner WIP not for origin", cwd=self.repo)
        old = self.origin_main(bare)
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        ask.assert_not_called()
        self.assertNotIn("will not get", out)
        self.assertNotIn("not pushed", out)
        self.assertEqual(self.origin_main(bare), old)

    def test_the_push_question_lists_the_commits_it_carries(self):
        self.with_origin()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        ask.assert_called()
        self.assertIn("Add lane-control files adapted", out)

    def test_write_orca_yaml_on_an_onboarded_project_commits_only_on_a_yes(self):
        bare = self.committed_lockfile_origin()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal(["y", "n"], "--write-orca-yaml")[0], 0)
        self.assertNotIn("orca.yaml", git("ls-files", cwd=self.repo))  # written, not committed
        self.assertTrue((self.repo / "orca.yaml").exists())
        self.assertEqual(self.in_terminal("n", "--write-orca-yaml")[0], 0)
        self.assertNotIn("orca.yaml", git("ls-files", cwd=self.repo))

    def test_readapt_of_a_pushed_project_asks_for_the_push_of_the_changed_files(self):
        bare = self.committed_lockfile_origin()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal(["y", "y"])[0], 0)
        self.assertEqual(self.origin_main(bare), git("rev-parse", "HEAD", cwd=self.repo))
        self.e.set_state(agent_steps={"sonnet": [{"write": {ADAPTED_FILE: "newer\n"}}], "opus": [PASS]})
        status, out, ask = self.in_terminal(["y", "y"], "--readapt")
        self.assertEqual(status, 0, out)
        self.assertEqual(ask.call_count, 2)
        self.assertTrue(ask.call_args[0][0].startswith("Push main"))
        self.assertEqual(self.origin_main(bare), git("rev-parse", "HEAD", cwd=self.repo))

    def test_a_change_to_only_one_of_the_pushed_files_is_still_offered(self):
        routing = ".lane-control/routing.md"
        bare = self.committed_lockfile_origin()
        self.e.script_agents(sonnet=[{"write": {ADAPTED_FILE: "a\n", routing: "b\n"}}], opus=[PASS])
        self.assertEqual(self.in_terminal(["y", "y"])[0], 0)
        (self.repo / routing).write_text("b changed by hand\n")
        git("add", routing, cwd=self.repo)
        git("commit", "-q", "-m", "tune routing", cwd=self.repo)
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        ask.assert_called_once()
        self.assertEqual(self.origin_main(bare), git("rev-parse", "HEAD", cwd=self.repo))

    def test_only_a_missing_orca_yaml_is_worded_as_that(self):
        self.with_origin()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal(["y", "y"])[0], 0)  # onboarding pushed, no orca.yaml
        (self.repo / "orca.yaml").write_text("scripts:\n  setup: |\n    make deps\n")
        git("add", "orca.yaml", cwd=self.repo)
        git("commit", "-q", "-m", "orca", cwd=self.repo)
        status, out, ask = self.in_terminal("n")
        self.assertEqual(status, 0, out)
        self.assertNotIn("onboarding commit is not pushed", out)
        self.assertIn("current orca.yaml", out)
        self.assertNotIn("onboarding files", out)

    def test_a_long_list_of_carried_commits_says_how_many_more(self):
        self.with_origin()
        for i in range(12):
            git("commit", "-q", "--allow-empty", "-m", f"wip {i}", cwd=self.repo)
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        status, out, ask = self.in_terminal(["y", "n"])
        self.assertRegex(out, r"\.\.\. and 3 more")

    def other_clone_pushes(self, bare, path, text, message):
        other = self.e.home / "other"
        if not other.exists():
            git("clone", "-q", str(bare), str(other), cwd=self.e.home)
        (other / path).write_text(text)
        git("add", path, cwd=other)
        git("commit", "-q", "-m", message, cwd=other)
        git("push", "-q", "origin", "main", cwd=other)
        git("fetch", "-q", "origin", cwd=self.repo)

    def test_a_checkout_behind_origin_asks_and_warns_nothing(self):
        bare = self.committed_lockfile_origin()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal(["y", "y"])[0], 0)
        self.other_clone_pushes(bare, "AGENTS.md", "# changed elsewhere\n", "agents change")
        head, remote = git("rev-parse", "HEAD", cwd=self.repo), self.origin_main(bare)
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        ask.assert_not_called()
        self.assertNotIn("will not get", out)
        self.assertNotIn("does not have", out)
        r = self.e.run(str(self.repo), adapt=True)  # and without a terminal
        self.assertNotIn("will not get", r.stdout)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), head)
        self.assertEqual(self.origin_main(bare), remote)

    def test_an_orca_yaml_only_on_origin_is_not_committed_again(self):
        bare = self.with_origin()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal(["y", "y"])[0], 0)  # onboarding pushed; no lockfile, so no draft yet
        self.with_lockfile()
        self.other_clone_pushes(bare, "orca.yaml", "scripts:\n  setup: |\n    make deps\n", "orca from elsewhere")
        head, remote = git("rev-parse", "HEAD", cwd=self.repo), self.origin_main(bare)
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        ask.assert_not_called()
        self.assertIn("behind", out)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), head)  # no commit made
        self.assertEqual(self.origin_main(bare), remote)

    def test_with_only_orca_yaml_open_the_text_names_no_onboarding_commit_and_no_empty_list(self):
        bare = self.with_origin()
        self.with_lockfile()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal(["y", "n"])[0], 0)
        git("push", "-q", "origin", "main", cwd=self.repo)
        git("fetch", "-q", "origin", cwd=self.repo)
        status, out, ask = self.in_terminal("n")
        self.assertEqual(status, 0, out)
        self.assertNotIn("onboarding commit", out)
        self.assertNotIn("whatever it is", out)

    def test_an_abort_before_the_second_question_offers_it_on_the_next_run(self):
        bare = self.committed_lockfile_origin()
        old = self.origin_main(bare)
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        with self.assertRaises(KeyboardInterrupt):
            self.in_terminal(["y", KeyboardInterrupt()])
        self.assertNotIn("orca.yaml", git("ls-files", cwd=self.repo))
        self.assertEqual(self.origin_main(bare), old)
        runs = len(self.e.agent_runs())
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        ask.assert_called_once()
        self.assertIn("orca.yaml", ask.call_args[0][0])
        self.assertEqual(len(self.e.agent_runs()), runs)
        self.assertEqual(self.committed_paths(), ["orca.yaml"])
        self.assertEqual(self.origin_main(bare), git("rev-parse", "HEAD", cwd=self.repo))
        self.assertTrue(self.started())

    def test_a_committed_orca_yaml_whose_push_failed_asks_only_about_the_push_next_time(self):
        bare = self.committed_lockfile_origin(reject_push=True)
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal(["y", "y"])[0], 0)
        self.assertEqual(self.committed_paths(), ["orca.yaml"])
        head = git("rev-parse", "HEAD", cwd=self.repo)
        (bare / "hooks" / "pre-receive").unlink()
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        ask.assert_called_once()
        self.assertTrue(ask.call_args[0][0].startswith("Push main"), ask.call_args[0][0])
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), head)  # nothing new committed
        self.assertEqual(self.origin_main(bare), head)

    # --- the list --------------------------------------------------------------------------------------------
    def test_list_comes_from_git_with_marks_and_suggestions(self):
        (self.repo / ".lane-control").mkdir()
        (self.repo / ".lane-control" / "old.md").write_text("old\n")
        git("add", ".lane-control", cwd=self.repo)
        git("commit", "-q", "-m", "earlier", cwd=self.repo)
        write = {"write": {".lane-control/old.md": "new\n", ADAPTED_FILE: "x\n",
                           ".lane-control/SUGGESTIONS.md": "Add the release checklist.\n"},
                 "say": "I only touched one file."}
        self.e.script_agents(sonnet=[write], opus=[PASS])
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
        self.e.script_agents(sonnet=[write], opus=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        self.assertNotIn(".lane-control/SUGGESTIONS.md", self.committed_paths())
        self.assertFalse((self.repo / ".lane-control" / "SUGGESTIONS.md").exists())

    def test_suggestions_leave_the_repo_before_the_review(self):
        write = {"write": {ADAPTED_FILE: "x\n", ".lane-control/SUGGESTIONS.md": "Do y.\n"}}
        self.e.script_agents(sonnet=[write], opus=[PASS])
        _, out, _ = self.in_terminal("n")
        reviewer = self.e.agent_runs()[1]
        self.assertEqual(reviewer["lane_files"], ["commands.md"])
        self.assertNotIn("Do y.", reviewer["prompt"])
        self.assertIn("Do y.", out)
        self.assertNotIn("SUGGESTIONS", git("status", "--porcelain", "-uall", cwd=self.repo))

    def test_fix_round_suggestions_are_added_to_the_earlier_ones(self):
        first = {"write": {ADAPTED_FILE: "x\n", ".lane-control/SUGGESTIONS.md": "First question"}}
        fixed = {"write": {ADAPTED_FILE: "y\n", ".lane-control/SUGGESTIONS.md": "Second question\n"}}
        self.e.script_agents(sonnet=[first, fixed], opus=[FIX, PASS])
        _, out, _ = self.in_terminal("n")
        self.assertIn("First question\n  Second question", out)
        self.assertEqual(self.e.agent_runs()[3]["lane_files"], ["commands.md"])

    def test_a_new_adapting_run_starts_with_no_old_suggestions(self):
        write = {"write": {ADAPTED_FILE: "x\n", ".lane-control/SUGGESTIONS.md": "Old question\n"}}
        self.e.script_agents(sonnet=[write], opus=[PASS])
        self.in_terminal("n")
        self.e.set_state(agent_steps={"sonnet": [{"write": {ADAPTED_FILE: "y\n"}}], "opus": [PASS]})
        self.assertNotIn("Old question", self.in_terminal("n", "--readapt")[1])

    def test_suggestion_only_readapt_shows_them(self):
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        self.e.set_state(agent_steps={"sonnet": [{"write": {".lane-control/SUGGESTIONS.md": "Only a question\n"}}]})
        status, out, _ = self.in_terminal("y", "--readapt")
        self.assertEqual(status, 0)
        self.assertIn("Only a question", out)
        self.assertEqual(out.count("Only a question"), 1)

    def test_suggestions_are_shown_again_on_a_rerun(self):
        write = {"write": {ADAPTED_FILE: "x\n", ".lane-control/SUGGESTIONS.md": "Do y.\n"}}
        self.e.script_agents(sonnet=[write], opus=[PASS])
        self.in_terminal("n")
        self.e.set_state(agent_steps={"opus": [PASS]})
        self.assertIn("Do y.", self.in_terminal("n")[1])

    # --- the reviewer ----------------------------------------------------------------------------------------
    def test_reviewer_never_sees_the_adapting_agents_account(self):
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.in_terminal("n")
        adapter, reviewer = self.e.agent_runs()
        self.assertNotIn("perfect", reviewer["prompt"])
        self.assertIn(ADAPTED_FILE, reviewer["prompt"])
        self.assertIn("VERDICT", reviewer["prompt"])

    def test_same_model_for_both_is_refused(self):
        self.e.script_agents()
        r = self.e.run(str(self.repo), "--adapter", "claude/sonnet", "--adapt-reviewer", "claude/sonnet", adapt=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.e.agent_runs(), [])

    def test_fix_round_passes_findings_back(self):
        fixed = {"write": {ADAPTED_FILE: "fixed\n"}}
        self.e.script_agents(sonnet=[WRITE, fixed], opus=[FIX, PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        self.assertEqual(self.models(), ["sonnet", "opus", "sonnet", "opus"])
        self.assertIn("wrong test command", self.e.agent_runs()[2]["prompt"])
        self.assertEqual((self.repo / ADAPTED_FILE).read_text(), "fixed\n")

    def test_a_review_that_keeps_failing_still_reaches_the_file_list_with_the_last_fix_marked(self):
        self.e.script_agents(sonnet=[WRITE, {}, {}], opus=[FIX, FIX])
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        # full review, fix, check, last fix, then the file list: no third review
        self.assertEqual(self.models(), ["sonnet", "opus", "sonnet", "opus", "sonnet"])
        ask.assert_called_once()
        listed = out.split("Files added or changed", 1)[1]
        self.assertIn("Fixed, not re-checked", listed)
        self.assertIn("wrong test command", listed)
        self.assertIn("wrong test command", self.e.agent_runs()[4]["prompt"])  # the last fix was asked for it
        self.assertEqual(sorted(self.committed_paths()), sorted([".gitignore", "AGENTS.md", ADAPTED_FILE]))

    def test_a_later_review_gets_the_earlier_findings_and_the_check_only_prompt(self):
        self.e.script_agents(sonnet=[WRITE, {}], opus=[FIX, PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        first, second = self.e.agent_runs()[1]["prompt"], self.e.agent_runs()[3]["prompt"]
        self.assertIn("Weakened doctrine", first)  # the full audit
        self.assertNotIn("not a new audit", first)
        self.assertIn("not a new audit", second)
        self.assertNotIn("Weakened doctrine", second)
        self.assertIn("wrong test command", second)
        self.assertIn(ADAPTED_FILE, second)

    def test_a_passing_check_lists_nothing_as_unchecked(self):
        self.e.script_agents(sonnet=[WRITE, {}], opus=[FIX, PASS])
        _, out, _ = self.in_terminal("y")
        self.assertNotIn("not re-checked", out)

    SAFETY_FIX = {"say": "- blocker [safety] .lane-control/project.md:3 a path under /home/ -> remove it\n"
                         "VERDICT: FIX"}

    def test_a_safety_finding_gets_one_more_check_after_the_last_fix(self):
        self.e.script_agents(sonnet=[WRITE, {}, {}], opus=[FIX, self.SAFETY_FIX, PASS])
        status, out, _ = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        self.assertEqual(self.models(), ["sonnet", "opus", "sonnet", "opus", "sonnet", "opus"])
        self.assertIn("not a new audit", self.e.agent_runs()[5]["prompt"])
        self.assertIn("[safety]", self.e.agent_runs()[5]["prompt"])
        self.assertNotIn("not re-checked", out)  # the check cleared it

    def test_a_safety_finding_still_open_after_the_extra_check_stops_with_nothing_committed(self):
        self.e.script_agents(sonnet=[WRITE, {}, {}], opus=[FIX, self.SAFETY_FIX, self.SAFETY_FIX])
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 3)
        self.assertEqual(self.models(), ["sonnet", "opus", "sonnet", "opus", "sonnet", "opus"])
        ask.assert_not_called()
        self.assertIn("a path under /home/", out)
        self.assertEqual(self.head_count(), 1)
        self.assertFalse(self.orca_setup_ran())

    def test_a_safety_check_with_no_verdict_stops_too(self):
        self.e.script_agents(sonnet=[WRITE, {}, {}], opus=[self.SAFETY_FIX, self.SAFETY_FIX,
                                                           {"say": "looks fine to me"}])
        status, _, ask = self.in_terminal("y")
        self.assertEqual(status, 3)
        ask.assert_not_called()
        self.assertEqual(self.head_count(), 1)

    def test_the_safety_mark_counts_in_any_shape(self):
        for text in ("1. blocker [safety] a:1", "- blocker: [safety] a:1", "- [safety] blocker a:1",
                     "**blocker** [SAFETY] a:1"):
            with self.subTest(text=text):
                self.assertTrue(load().has_safety(text))
        self.assertFalse(load().has_safety("- blocker a:1 a plain finding"))

    def test_a_safety_finding_in_another_shape_still_gets_the_extra_check_and_stops(self):
        odd = {"say": "1. blocker [safety] .lane-control/project.md:3 private path\nVERDICT: FIX"}
        self.e.script_agents(sonnet=[WRITE, {}, {}], opus=[odd, odd, odd])
        status, _, ask = self.in_terminal("y")
        self.assertEqual(status, 3)
        self.assertEqual(self.models(), ["sonnet", "opus", "sonnet", "opus", "sonnet", "opus"])
        ask.assert_not_called()
        self.assertEqual(self.head_count(), 1)

    def test_a_minor_safety_finding_under_a_pass_stops_when_the_extra_check_has_it(self):
        minor = {"say": "- minor [safety] .lane-control/project.md:3 still there\nVERDICT: PASS"}
        self.e.script_agents(sonnet=[WRITE, {}, {}], opus=[self.SAFETY_FIX, self.SAFETY_FIX, minor])
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 3)
        self.assertIn("still there", out)
        ask.assert_not_called()

    def test_a_minor_safety_finding_under_a_first_pass_gets_a_fix_round(self):
        minor = {"say": "- minor [safety] .lane-control/project.md:3 a path\nVERDICT: PASS"}
        self.e.script_agents(sonnet=[WRITE, {}], opus=[minor, PASS])
        status, out, _ = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        self.assertEqual(self.models(), ["sonnet", "opus", "sonnet", "opus"])

    def test_a_later_review_that_crashes_counts_as_no_verdict(self):
        self.e.script_agents(sonnet=[WRITE, {}, {}], opus=[FIX, {"exit": 1, "err": "API error"}])
        status, out, _ = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        self.assertEqual(self.models(), ["sonnet", "opus", "sonnet", "opus", "sonnet"])
        self.assertIn("gave no verdict", out)

    def test_a_crashed_safety_check_stops_the_run(self):
        self.e.script_agents(sonnet=[WRITE, {}, {}],
                             opus=[self.SAFETY_FIX, self.SAFETY_FIX, {"exit": 1, "err": "API error"}])
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 3)
        self.assertIn("gave no verdict", out)
        ask.assert_not_called()

    def test_a_check_review_gets_the_check_closing_rules(self):
        self.e.script_agents(sonnet=[WRITE, {}], opus=[FIX, PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        first, second = self.e.agent_runs()[1]["prompt"], self.e.agent_runs()[3]["prompt"]
        self.assertIn("Report every finding with its file", first)
        self.assertNotIn("Report every finding with its file", second)
        self.assertIn("Report only the earlier findings", second)

    def test_a_stopped_safety_review_reruns_without_the_adapter_and_keeps_a_hand_edit(self):
        self.e.script_agents(sonnet=[WRITE, {}, {}], opus=[self.SAFETY_FIX, self.SAFETY_FIX, self.SAFETY_FIX])
        self.assertEqual(self.in_terminal("y")[0], 3)
        (self.repo / ADAPTED_FILE).write_text("owner's hand edit\n")
        self.e.script_agents(sonnet=[], opus=[PASS])
        status, out, _ = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        self.assertEqual(self.models()[6:], ["opus"])  # no new adapter run, only the full review
        self.assertEqual((self.repo / ADAPTED_FILE).read_text(), "owner's hand edit\n")
        self.assertEqual(sorted(self.committed_paths()), sorted([".gitignore", "AGENTS.md", ADAPTED_FILE]))

    def test_a_failed_check_scope_after_the_last_fix_still_stops(self):
        outside = {"write": {"outside.txt": "x\n"}}
        self.e.script_agents(sonnet=[WRITE, {}, outside], opus=[FIX, FIX])
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(self.models(), ["sonnet", "opus", "sonnet", "opus", "sonnet"])
        self.assertEqual(self.head_count(), 1)
        self.assertFalse(self.orca_setup_ran())

    def test_review_without_a_verdict_is_not_a_pass(self):
        for reply in ("All good, I think.", "VERDICT: PASS\nbut see above", ""):
            with self.subTest(reply=reply):
                self.assertIsNone(load().verdict(reply))

    def test_a_first_review_without_a_verdict_stops_with_nothing_committed(self):
        self.e.script_agents(sonnet=[WRITE], opus=[{"say": "All good, I think."}])
        status, out, ask = self.in_terminal("y")
        self.assertEqual(status, 3)
        ask.assert_not_called()
        self.assertIn("first review gave no verdict", out)
        self.assertNotIn("still open after the last fix", out)
        self.assertEqual(self.models(), ["sonnet", "opus"])
        self.assertEqual(self.head_count(), 1)

    def test_a_later_check_without_a_verdict_is_listed_and_the_last_fix_still_runs(self):
        self.e.script_agents(sonnet=[WRITE, {}, {}], opus=[FIX, {"say": "hm"}])
        status, out, _ = self.in_terminal("y")
        self.assertEqual(status, 0, out)
        self.assertEqual(self.models(), ["sonnet", "opus", "sonnet", "opus", "sonnet"])
        self.assertIn("gave no verdict", out)

    def test_verdict_tolerates_bold(self):
        self.assertEqual(load().verdict("x\n**VERDICT: PASS**\n"), "PASS")

    # --- AGENTS.md -------------------------------------------------------------------------------------------
    def _with_agents_md(self, text="# Rules\n\nBe kind.\n"):
        (self.repo / "AGENTS.md").write_text(text)
        git("add", "AGENTS.md", cwd=self.repo)
        git("commit", "-q", "-m", "agents", cwd=self.repo)

    def test_existing_agents_md_only_gains_the_section(self):
        self._with_agents_md()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        m = load()
        text = (self.repo / "AGENTS.md").read_text()
        self.assertTrue(text.startswith("# Rules\n\nBe kind.\n"))
        self.assertEqual(text.count(m.SECTION_BEGIN), 1)
        self.assertEqual(m.without_section(text).rstrip(), "# Rules\n\nBe kind.")

    def test_no_agents_md_creates_one_with_only_the_section(self):
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
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
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        self.assertTrue((self.repo / "AGENTS.md").is_symlink())
        self.assertEqual(sorted(self.committed_paths()), sorted([".gitignore", "docs/rules.md", ADAPTED_FILE]))
        self.assertIn(load().SECTION_BEGIN, (self.repo / "docs" / "rules.md").read_text())

    def test_agents_md_linking_outside_the_repo_is_refused(self):
        outside = self.e.home / "shared.md"
        outside.write_text("# Shared\n")
        self._link_agents_md(str(outside))
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
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
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.e.agent_runs(), [])

    def test_agent_editing_agents_md_is_refused(self):
        self._with_agents_md()
        write = {"write": {ADAPTED_FILE: "x\n", "AGENTS.md": "# Rewritten\n"}}
        self.e.script_agents(sonnet=[write], opus=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.head_count(), 2)
        self.assertFalse(self.orca_setup_ran())

    def test_agent_writing_outside_its_folder_is_refused(self):
        write = {"write": {ADAPTED_FILE: "x\n", "src/app.py": "oops\n"}}
        self.e.script_agents(sonnet=[write], opus=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.head_count(), 1)
        self.assertEqual(self.models(), ["sonnet"])

    def test_owners_dirty_files_are_not_blamed_on_the_agent(self):
        (self.repo / "wip.txt").write_text("mine")
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal("n")[0], 3)

    # --- re-runs ---------------------------------------------------------------------------------------------
    def test_rerun_after_yes_starts_no_agent(self):
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
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
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        head = git("rev-parse", "HEAD", cwd=self.repo)
        self.assertTrue(load().onboarded(self.repo))
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.models(), ["sonnet", "opus"])
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo), head)

    def test_rerun_after_readapt_no_asks_again_instead_of_going_on(self):
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        self.e.set_state(agent_steps={"sonnet": [{"write": {ADAPTED_FILE: "newer\n"}}], "opus": [PASS, PASS]})
        self.assertEqual(self.in_terminal("n", "--readapt")[0], 3)
        status, out, ask = self.in_terminal("n")
        self.assertEqual(status, 3)
        ask.assert_called_once()
        self.assertNotIn("verify: ok", out)
        self.assertEqual(self.models(), ["sonnet", "opus", "sonnet", "opus", "opus"])

    def _stale_section(self):
        """An onboarded project whose committed section has wording this lane-control no longer writes."""
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
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
        self.assertEqual(self.models(), ["sonnet", "opus"])
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
        self.e.set_state(agent_steps={"sonnet": [{}]})
        self.assertEqual(self.in_terminal("y", "--readapt")[0], 0)
        self.assertEqual(self.models(), ["sonnet", "opus", "sonnet"])
        self.assertNotIn("OLD WORDING", f.read_text())
        self.assertEqual(self.committed_paths(), ["AGENTS.md"])

    def test_rerun_with_a_current_section_never_asks(self):
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        status, _, ask = self.in_terminal("n")
        self.assertEqual(status, 0)
        ask.assert_not_called()

    def test_rerun_after_no_reviews_without_rewriting(self):
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.in_terminal("n")
        (self.repo / ADAPTED_FILE).write_text("the owner's hand edit\n")
        self.e.set_state(agent_steps={"opus": [PASS]})
        self.assertEqual(self.in_terminal("y")[0], 0)
        self.assertEqual(self.models(), ["sonnet", "opus", "opus"])
        self.assertEqual((self.repo / ADAPTED_FILE).read_text(), "the owner's hand edit\n")
        self.assertEqual(self.committed_paths().count("AGENTS.md"), 1)

    def test_readapt_after_yes_keeps_one_section(self):
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        self.e.set_state(agent_steps={"sonnet": [{"write": {ADAPTED_FILE: "newer\n"}}], "opus": [PASS]})
        self.assertEqual(self.in_terminal("y", "--readapt")[0], 0)
        self.assertEqual(self.committed_paths(), [ADAPTED_FILE])
        self.assertEqual((self.repo / "AGENTS.md").read_text().count(load().SECTION_BEGIN), 1)

    # --- what an agent may touch ------------------------------------------------------------------------------
    def test_agent_putting_an_owners_dirty_file_back_is_refused(self):
        (self.repo / "wip.txt").write_text("committed\n")
        git("add", "wip.txt", cwd=self.repo)
        git("commit", "-q", "-m", "wip", cwd=self.repo)
        (self.repo / "wip.txt").write_text("the owner's edit\n")
        self.e.script_agents(sonnet=[{"write": {ADAPTED_FILE: "x\n", "wip.txt": "committed\n"}}], opus=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.models(), ["sonnet"])

    def test_agent_editing_inside_the_section_is_refused(self):
        tampered = load().SECTION.replace("read them", "ignore them") + "\n"
        self.e.script_agents(sonnet=[WRITE, {"write": {"AGENTS.md": tampered}}], opus=[FIX, PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.head_count(), 1)
        self.assertEqual(self.models(), ["sonnet", "opus", "sonnet"])

    def test_agent_changing_gitignore_layout_is_refused(self):
        (self.repo / ".gitignore").write_text("a\nb\n")
        git("add", ".gitignore", cwd=self.repo)
        git("commit", "-q", "-m", "ignore", cwd=self.repo)
        self.e.script_agents(sonnet=[{"write": {ADAPTED_FILE: "x\n", ".gitignore": "b\na\n.cotal/\n"}}], opus=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.models(), ["sonnet"])

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
            return m.main([str(self.repo), "--opencode", "hosted"]), err.getvalue()

    def test_owners_other_work_at_the_prompt_does_not_cost_the_yes(self):
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        status, err = self._yes_after(lambda: (self.repo / "notes.txt").write_text("mine"))
        self.assertEqual(status, 0, err)
        self.assertEqual(sorted(self.committed_paths()), sorted([".gitignore", "AGENTS.md", ADAPTED_FILE]))
        self.assertIn("?? notes.txt", git("status", "--porcelain", cwd=self.repo))

    def test_agents_md_edited_at_the_prompt_stops_the_commit(self):
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        f = self.repo / "AGENTS.md"
        status, err = self._yes_after(lambda: f.write_text(f.read_text() + "the owner's line\n"))
        self.assertEqual(status, 1)
        self.assertIn("AGENTS.md has uncommitted changes", err)
        self.assertNotIn("agent", err.replace("AGENTS.md", ""))
        self.assertEqual(self.head_count(), 1)

    def test_change_during_an_agent_run_does_not_claim_who_made_it(self):
        self.e.script_agents(sonnet=[{"write": {ADAPTED_FILE: "x\n", "src/app.py": "oops\n"}}])
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("src/app.py", r.stderr)
        self.assertNotIn("an agent changed", r.stderr)

    def test_gitignore_rewritten_by_an_earlier_setup_says_so(self):
        (self.repo / ".gitignore").write_bytes(b"a\r\nb\r\n")
        git("add", ".gitignore", cwd=self.repo)
        git("commit", "-q", "-m", "ignore", cwd=self.repo)
        (self.repo / ".gitignore").write_bytes(b"a\nb\n.cotal/\n")
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("earlier project-setup may have rewritten its line endings", r.stderr)
        self.assertEqual(self.e.agent_runs(), [])

    def test_agent_committing_is_refused(self):
        self.e.script_agents(sonnet=[{"write": {ADAPTED_FILE: "x\n"}, "commit": "sneaky"}], opus=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.models(), ["sonnet"])
        self.assertFalse(self.orca_setup_ran())

    # --- sizes and encodings ---------------------------------------------------------------------------------
    def test_huge_multibyte_diff_and_findings_fit_in_the_prompt(self):
        (self.repo / ".lane-control").mkdir()
        big = self.repo / ".lane-control" / "a.md"
        big.write_text("€" * 59000 + "\n")
        git("add", ".lane-control", cwd=self.repo)
        git("commit", "-q", "-m", "big", cwd=self.repo)
        huge = {"say": "€" * 59000 + "\nVERDICT: FIX"}
        self.e.script_agents(sonnet=[{"write": {".lane-control/a.md": "é" * 59000 + "\n"}}, {}],
                             opus=[huge, PASS])
        self.assertEqual(self.in_terminal("n", "--readapt")[0], 3)
        self.assertEqual(self.models(), ["sonnet", "opus", "sonnet", "opus"])
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
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 0)
        self.assertTrue((self.repo / "AGENTS.md").read_bytes().startswith(b"caf\xe9\n\n<!--"))
        self.assertEqual((self.repo / ".gitignore").read_bytes(), b"caf\xe9\n.cotal/\n")

    # --- failures and switches -------------------------------------------------------------------------------
    def test_agent_failure_stops_before_orca_setup(self):
        self.e.script_agents(sonnet=[{"exit": 1, "say": "boom"}])
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(self.orca_setup_ran())
        self.assertFalse((self.repo / "AGENTS.md").exists())

    def test_agent_writing_nothing_stops(self):
        self.e.script_agents(sonnet=[{}])
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("wrote nothing", r.stderr)

    def test_no_git_identity_stops_before_any_agent(self):
        (self.e.home / ".gitconfig").write_text("")
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
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

    # --- private files --------------------------------------------------------------------------------------
    def private_repo(self):
        r = self.repo
        (r / ".gitignore").write_text("data/\nlocal.json\n.env\n.direnv/\n")
        (r / ".env.example").write_text("A=1\n")
        (r / "shot.png").write_text("private\n")
        (r / ".env.local").write_text("S=1\n")  # untracked and not ignored
        (r / "data").mkdir()
        (r / "data" / "d.txt").write_text("x\n")
        (r / "local.json").write_text("{}\n")
        (r / ".env").write_text("S=1\n")
        (r / ".direnv").mkdir()
        (r / ".direnv" / "x").write_text("x\n")
        git("add", ".gitignore", ".env.example", "shot.png", cwd=r)
        git("commit", "-q", "-m", "files", cwd=r)
        return r

    def test_ignored_and_untracked_env_paths_are_denied_tracked_ones_are_not(self):
        r = self.private_repo()
        rules = load().private_rules(r)
        for want in ("Read(/data/**)", "Read(/local.json)", "Read(/.env)", "Read(/.direnv/**)", "Read(/.env.local)"):
            self.assertIn(want, rules)
        self.assertNotIn("Read(/.env.example)", rules)
        self.assertNotIn("Read(/shot.png)", rules)
        self.assertIn("Read(/.git/**)", rules)
        self.assertFalse(any(".lane-control" in x for x in rules), rules)

    def test_env_files_are_denied_unless_templates(self):
        r = self.private_repo()
        for n in (".env.production", ".env.example", ".env.sample", ".env.template", ".envrc"):
            (r / n).write_text("x\n")
        git("add", "-f", ".env.production", ".env.sample", ".env.template", ".envrc", cwd=r)
        rules = load().private_rules(r)
        self.assertIn("Read(/.env.production)", rules)
        for ok in (".env.example", ".env.sample", ".env.template", ".envrc"):
            self.assertNotIn(f"Read(/{ok})", rules)

    def test_submodule_and_embedded_repo_files_are_denied_and_git_at_any_depth(self):
        r = self.private_repo()
        sub = self.e.home / "subsrc"
        sub.mkdir()
        git("init", "-q", "-b", "main", cwd=sub)
        (sub / ".gitignore").write_text("data/\n.env\n")
        (sub / "a.txt").write_text("a\n")
        git("add", ".", cwd=sub)
        git("commit", "-q", "-m", "s", cwd=sub)
        git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "submod", cwd=r)
        (r / "submod" / "data").mkdir()
        (r / "submod" / "data" / "x.txt").write_text("x\n")
        (r / "submod" / ".env").write_text("S=1\n")
        emb = r / "vendor" / "foo"
        emb.mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=emb)
        (emb / ".gitignore").write_text(".env\n")
        (emb / ".env").write_text("S=1\n")
        rules = load().private_rules(r)
        for want in ("Read(/submod/data/**)", "Read(/submod/.env)", "Read(/vendor/foo/.env)", "Read(/**/.git/**)",
                     "Read(/**/.git)"):
            self.assertIn(want, rules)

    def test_plain_clone_with_an_unpopulated_submodule_still_gets_rules(self):
        r = self.private_repo()
        sub = self.e.home / "subsrc2"
        sub.mkdir()
        git("init", "-q", "-b", "main", cwd=sub)
        (sub / "a.txt").write_text("a\n")
        git("add", ".", cwd=sub)
        git("commit", "-q", "-m", "s", cwd=sub)
        git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "lib", cwd=r)
        git("commit", "-q", "-m", "sm", cwd=r)
        clone = self.e.home / "clone"
        git("clone", "-q", str(r), str(clone), cwd=self.e.home)
        self.assertEqual(list((clone / "lib").iterdir()), [])
        self.assertIn("Read(/.git/**)", load().private_rules(clone))

    def test_files_in_a_submodule_folder_without_its_git_are_denied_as_a_whole(self):
        r = self.private_repo()
        sub = self.e.home / "subsrc3"
        sub.mkdir()
        git("init", "-q", "-b", "main", cwd=sub)
        (sub / "a.txt").write_text("a\n")
        git("add", ".", cwd=sub)
        git("commit", "-q", "-m", "s", cwd=sub)
        git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "lib", cwd=r)
        git("commit", "-q", "-m", "sm", cwd=r)
        clone = self.e.home / "clone3"
        git("clone", "-q", str(r), str(clone), cwd=self.e.home)
        self.assertEqual(list((clone / "lib").iterdir()), [])
        self.assertNotIn("Read(/lib/**)", load().private_rules(clone))  # really empty: nothing to deny
        (clone / "lib" / ".env").write_text("SENTINEL\n")
        self.assertIn("Read(/lib/**)", load().private_rules(clone))

    def test_failure_message_shows_the_agents_last_line(self):
        self.e.script_agents(sonnet=[{"say": "stdout noise", "err": "the real reason", "exit": 1}])
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("the real reason", r.stderr)
        self.assertNotIn("stdout noise", r.stderr)

    def test_failure_message_falls_back_to_stdout(self):
        self.e.script_agents(sonnet=[{"say": "stdout noise", "exit": 1}])
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("stdout noise", r.stderr)

    def test_private_flag_adds_tracked_files(self):
        r = self.private_repo()
        mod = load()
        mod.PRIVATE_GLOBS[:] = ["shot*.png", "docs/private/**"]
        try:
            rules = mod.private_rules(r)
        finally:
            mod.PRIVATE_GLOBS[:] = []
        self.assertIn("Read(/shot*.png)", rules)
        self.assertIn("Read(/docs/private/**)", rules)

    def test_deny_list_reaches_both_agents_and_the_prompt_stays_last(self):
        self.private_repo()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.in_terminal("n", "--private", "shot.png")
        adapter, reviewer = self.e.agent_runs()
        for run in (adapter, reviewer):
            argv = run["argv"]
            denied = argv[argv.index("--disallowedTools") + 1:argv.index("--permission-mode")]
            self.assertIn("Read(/data/**)", denied)
            self.assertIn("Read(/shot.png)", denied)
            self.assertNotIn("Read(/.env.example)", denied)
            self.assertNotIn("Read(**/.env*)", denied)
            self.assertEqual(argv[-1], run["prompt"])
            self.assertIn("--restricted", argv)

    def test_denied_names_are_never_committed(self):
        self.private_repo()
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.assertEqual(self.in_terminal("y", "--private", "shot.png")[0], 0)
        shown = git("show", "--format=", "-p", "HEAD", cwd=self.repo)
        self.assertNotIn("Read(", shown)
        self.assertNotIn("d.txt", shown)
        self.assertNotIn("--disallowedTools", shown)

    def test_a_name_a_rule_cannot_carry_stops_setup(self):
        r = self.private_repo()
        (r / ".gitignore").write_text("data/\nodd(1).txt\n")
        (r / "odd(1).txt").write_text("x\n")
        git("add", ".gitignore", cwd=r)
        git("commit", "-q", "-m", "odd", cwd=r)
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot carry", r.stderr)
        self.assertEqual(self.e.agent_runs(), [])

    def test_bad_private_globs_are_refused(self):
        self.e.script_agents()
        for bad in ("/etc/x", "../x", "~/x", "a(b)"):
            with self.subTest(bad=bad):
                r = self.e.run(str(self.repo), "--private", bad, adapt=True)
                self.assertEqual(r.returncode, 2)
                self.assertIn("--private", r.stderr)
        self.assertEqual(self.e.agent_runs(), [])

    def test_rule_free_agent_cmd_has_no_deny_flag(self):
        cmd = load().agent_cmd("sonnet", "PROMPT", write=False)
        self.assertNotIn("--disallowedTools", cmd)

    def test_agents_have_a_hard_turn_cap(self):
        mod = load()
        for write in (True, False):
            cmd = mod.agent_cmd("sonnet", "PROMPT", write=write)
            self.assertEqual(cmd[cmd.index("--max-turns") + 1], str(mod.MAX_TURNS[write]))
            self.assertEqual(cmd[-1], "PROMPT")

    def test_running_out_of_turns_is_a_clean_fail_with_nothing_committed(self):
        self.e.script_agents(sonnet=[{"write": {ADAPTED_FILE: "half\n"}, "say": "Error: Reached max turns (80)",
                                      "exit": 1}])
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("used all 80 turns", r.stderr)
        self.assertIn("Nothing was committed", r.stderr)
        self.assertEqual(self.head_count(), 1)
        self.assertFalse(self.orca_setup_ran())

    def test_rerun_after_running_out_of_turns_restarts_the_adapter_and_commits_no_suggestions(self):
        half = {"write": {ADAPTED_FILE: "half\n", ".lane-control/SUGGESTIONS.md": "q\n"},
                "say": "Error: Reached max turns (80)", "exit": 1}
        self.e.script_agents(sonnet=[half])
        r = self.e.run(str(self.repo), adapt=True)
        self.assertIn("--readapt", r.stderr)
        self.assertFalse((self.repo / ".lane-control" / "SUGGESTIONS.md").exists())
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.in_terminal("y", "--readapt")
        self.assertEqual(self.models(), ["sonnet", "sonnet", "opus"])
        self.assertNotIn(".lane-control/SUGGESTIONS.md", self.committed_paths())
        self.assertNotEqual((self.repo / ADAPTED_FILE).read_text(), "half\n")

    def test_reviewer_running_out_of_turns_is_a_clean_fail_too(self):
        self.e.script_agents(sonnet=[WRITE], opus=[{"say": "Error: Reached max turns (60)", "exit": 1}])
        r = self.e.run(str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("the reviewer used all 60 turns", r.stderr)
        self.assertNotIn("--readapt", r.stderr)

    def test_default_models_adapter_sonnet_reviewer_opus(self):
        self.e.script_agents(sonnet=[WRITE], opus=[PASS])
        self.in_terminal("n")
        self.assertEqual(self.models(), ["sonnet", "opus"])

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
        self.e.script_agents(sonnet=[seen], opus=[PASS])
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
        self.e.script_agents(sonnet=[{"write": {ADAPTED_FILE: "x\n", ".gitignore": ".cotal/\nsecrets\n"}}], opus=[PASS])
        self.assertEqual(self.in_terminal("y")[0], 1)
        self.assertEqual(self.head_count(), 1)

    # --- prompts and --host ----------------------------------------------------------------------------------
    def test_prompt_templates_get_their_machine_values(self):
        fixed = {"write": {ADAPTED_FILE: "fixed\n"}}
        self.e.script_agents(sonnet=[WRITE, fixed], opus=[FIX, PASS])
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

    def test_host_mode_carries_the_machine_tools(self):
        for name in ("lane-heavy", "lane-room", "lane-limits.sh"):
            self.assertIn(f"scripts/{name}", load().HOST_FILES)


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

    def test_beads_note_is_plain_and_creates_nothing(self):
        repo = self.e.repo()
        self.assertIsNone(shutil.which("bd", path=self.e.env["PATH"]), "test needs a PATH without bd")
        r = self.e.run(str(repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("`bd` is not on PATH", r.stderr)
        bd = self.e.bin / "bd"
        bd.write_text("#!/bin/sh\n[ -d .beads ]\n")
        bd.chmod(0o755)
        r = self.e.run(str(repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no beads workspace", r.stderr)
        self.assertFalse((repo / ".beads").exists())
        (repo / ".beads").mkdir()
        r = self.e.run(str(repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("beads", r.stderr)

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
        self.assertIn("swift\topencode", (self.e.store(repo) / "lanes.tsv").read_text())
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

    def test_a_required_tier_without_a_sandbox_key_says_how_to_log_in(self):
        repo = self.e.repo()
        r = self.e.run(str(repo), "--name", "Shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("swift has sandbox required", r.stderr)
        self.assertRegex(r.stderr, r"tier swift pins opencode/deepseek-v4\.1-flash.*no usable key")
        self.assertIn("Once per machine", r.stderr)
        self.assertNotIn("OPENCODE_API_KEY", r.stderr)

    def test_an_own_model_on_a_setup_flag_gets_the_notice(self):
        repo = self.e.repo()
        r = self.e.run(str(repo), "--coordinator", "opencode/deepseek/deepseek-chat", "--own-model", "deepseek/deepseek-chat", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--coordinator opencode/deepseek/deepseek-chat", r.stdout)
        self.assertIn("writes no sandbox", r.stdout)
        self.assertEqual(self.e.settings(repo)["own_models"], ["deepseek/deepseek-chat"])

    def test_an_opencode_pin_on_a_setup_flag_without_an_explicit_own_choice_is_refused(self):
        repo = self.e.repo()
        for args in (["--coordinator", "opencode/deepseek/deepseek-chat"], ["--design-lead", "opencode/gw/m"],
                     ["--reviewer", "opencode/gw/m", "--own-model", "gw/other"]):
            r = self.e.run(str(repo), *args, "--no-start")
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("--own-model", r.stderr)

    def test_own_model_refuses_a_hosted_or_malformed_name(self):
        repo = self.e.repo()
        for name in ("opencode/x", "opencode-go/x", "noslash"):
            r = self.e.run(str(repo), "--own-model", name, "--no-start")
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("--own-model", r.stderr)

    def test_the_manager_starts_with_the_launch_wrapper_first_on_path(self):
        repo = self.e.repo()
        self.assertEqual(self.e.run(str(repo), "--name", "Shop").returncode, 0)
        path = json.loads(self.e.state.read_text())["mgr_path"].split(":")
        self.assertEqual(path[0], str(self.e.home / ".agents" / "bin" / "cotal-opencode"))
        link = self.e.home / ".agents" / "bin" / "cotal-opencode" / "opencode"
        self.assertEqual(os.readlink(link), "../opencode-seat")
        self.assertEqual(link.resolve().read_bytes(), (ROOT / "scripts" / "opencode-seat").read_bytes())

    def test_hosted_opencode_pins_on_setups_own_agents_are_refused(self):
        repo = self.e.repo()
        for args in (["--coordinator", "opencode/opencode/deepseek-v4.1-flash"],
                     ["--design-lead", "opencode/opencode-go/x"], ["--reviewer", "opencode/opencode/x-free"]):
            r = self.e.run(str(repo), *args)
            self.assertEqual(r.returncode, 2, args)
            self.assertIn("runs only inside the sandbox", r.stderr)
        self.assertFalse((repo / ".cotal" / "agents").exists())
        self.assertNotIn("spawn", "\n".join(self.e.calls()))

    def test_own_model_says_it_writes_no_sandbox(self):
        r = self.e.run(str(self.e.repo()), "--opencode", "own", "--opencode-model", "gateway/m1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("writes no sandbox", r.stdout)
        self.assertIn("gateway you run", r.stdout)

    def test_mgr_passes_opencode_key_by_name_only(self):
        repo = self.e.repo()
        self.e.env["OPENCODE_API_KEY"] = "sentinel-key-value"
        r = self.e.run(str(repo), "--name", "Shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (repo / ".cotal" / "mgr-service.json").read_text()
        self.assertEqual(json.loads(text)["envRefs"].get("OPENCODE_API_KEY"), "OPENCODE_API_KEY")
        self.assertNotIn("sentinel-key-value", text)
        self.assertNotIn("sentinel-key-value", r.stdout + r.stderr)
        self.assertNotIn("OPENCODE_API_KEY is not set", r.stderr)

    def test_empty_key_is_not_passed_to_mgr(self):
        repo = self.e.repo()
        self.e.env["OPENCODE_API_KEY"] = ""
        self.assertEqual(self.e.run(str(repo), "--name", "Shop").returncode, 0)
        d = json.loads((repo / ".cotal" / "mgr-service.json").read_text())
        self.assertNotIn("OPENCODE_API_KEY", d["envRefs"])
    def test_following_the_key_steps_records_the_key_on_mgr(self):
        repo = self.e.repo()
        self.assertEqual(self.e.run(str(repo), "--name", "Shop").returncode, 0)
        state = json.loads(self.e.state.read_text())  # the owner ran `cotal svc remove mgr`; the file stays behind
        del state["services"]["mgr"]
        self.e.state.write_text(json.dumps(state))
        self.e.env["OPENCODE_API_KEY"] = "sentinel-key-value"
        r = self.e.run(str(repo), "--name", "Shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads((repo / ".cotal" / "mgr-service.json").read_text())
        self.assertEqual(d["envRefs"].get("OPENCODE_API_KEY"), "OPENCODE_API_KEY")

    def _key_check_calls(self, *args):
        m = load()
        repo = self.e.repo()
        with mock.patch.dict(os.environ, self.e.env, clear=True), mock.patch.object(m, "opencode_key_check") as check, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            first = m.main([str(repo), "--skip-adapt", "--name", "Shop", *args])
            first_args = check.call_args
            second = m.main([str(repo), "--skip-adapt", "--name", "Shop", *args])
        self.assertEqual((first, second), (0, 0))
        return repo, first_args, check.call_args

    def test_setup_calls_the_key_check_with_the_projects_table(self):
        repo, first, again = self._key_check_calls()
        self.assertEqual(first.args[0], self.e.store(repo) / "lanes.tsv")
        self.assertEqual(again.args[0], self.e.store(repo) / "lanes.tsv")

    def test_key_check_also_runs_with_no_start(self):
        repo, first, _ = self._key_check_calls("--no-start")
        self.assertEqual(first.args[0], self.e.store(repo) / "lanes.tsv")
    def test_free_opencode_tier_without_key_is_quiet(self):
        repo = self.e.repo()
        tiers = self.e.home / "free-lanes.tsv"
        tiers.write_text("tier\tharness\tmodel\tpermission\nswift\topencode\topencode/x-free\t-\n")
        r = self.e.run(str(repo), "--name", "Shop", "--tiers", str(tiers))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("OPENCODE_API_KEY is not set", r.stderr)

    def test_opencode_coordinator(self):
        repo = self.e.repo()
        r = self.e.run(str(repo), "--coordinator", "opencode/gateway/deepseek-v4.1-flash", "--own-model", "gateway/deepseek-v4.1-flash")
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

    def _tools(self):
        return self.e.home / ".agents" / "bin"

    def test_machine_tools_installed_next_to_each_other(self):
        r = self.e.run(str(self.e.repo()), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"lane-tools.*created")
        for name in ("lane-heavy", "lane-room", "lane-limits.sh"):
            self.assertEqual((self._tools() / name).read_bytes(), (ROOT / "scripts" / name).read_bytes())
        p = subprocess.run([str(self._tools() / "lane-heavy"), "--help"], env=self.e.env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)  # it found lane-limits.sh beside it
        self.assertIn("LANE_HEAVY_SLOTS", p.stdout)
        self.assertTrue(os.access(self._tools() / "lane-room", os.X_OK))

    def test_whatever_installs_the_skill_installs_the_machine_tools(self):
        m = load()  # a refresh goes through skills() too, so it reinstalls them
        (self.e.home / ".agents" / "skills" / "orca-cli").mkdir(parents=True)
        with mock.patch.dict(m.os.environ, {"HOME": str(self.e.home)}), mock.patch.object(m, "lane_tools") as tools:
            m.skills()
        tools.assert_called_once_with(self.e.home)

    def test_machine_tools_rerun_changes_nothing(self):
        repo = self.e.repo()
        self.e.run(str(repo), "--no-start")
        before = (self._tools() / "lane-heavy").stat().st_ino
        r = self.e.run(str(repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"lane-tools.*exists")
        self.assertEqual((self._tools() / "lane-heavy").stat().st_ino, before)

    def test_stale_machine_tool_is_replaced_without_touching_a_running_copy(self):
        repo = self.e.repo()
        self.e.run(str(repo), "--no-start")
        tool = self._tools() / "lane-heavy"
        tool.write_text("#!/bin/sh\necho old\n")
        with open(tool) as running:  # bash reads a running script as it goes: the old file must stay whole
            r = self.e.run(str(repo), "--no-start")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertRegex(r.stdout, r"lane-tools.*updated")
            self.assertEqual(running.read(), "#!/bin/sh\necho old\n")
        self.assertEqual(tool.read_bytes(), (ROOT / "scripts" / "lane-heavy").read_bytes())
        self.assertTrue(os.access(tool, os.X_OK))
        self.assertEqual(sorted(p.name for p in self._tools().iterdir()),
                         ["cotal-opencode", "lane-heavy", "lane-limits.sh", "lane-room", "lane-tier", "opencode-sandbox.sh", "opencode-seat"])

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
        self.assertNotIn("agent_design-lead", self._closed())  # the stub's agent tab: one-line preview, no identity, no cotal- title: only the handle guard keeps it

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
        r = self.e.run(str(repo), "--reviewer", "opencode/gateway/glm-5", "--own-model", "gateway/glm-5", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (repo / ".cotal" / "agents" / "reviewer.md").read_text()
        self.assertIn("agent: opencode", text)
        self.assertIn("model: gateway/glm-5", text)
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
        r = self.e.run(str(repo), "--coordinator", "opencode/gateway/glm-5", "--own-model", "gateway/glm-5", "--no-design-lead")
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
        oc = "opencode/gateway/x"
        tiers = self.e.home / "tiers.tsv"
        tiers.write_text("tier\tharness\tmodel\tpermission\nswift\topencode\topencode/x\t-\n")
        self.assertEqual(self.e.run(str(repo), "--own-model", "gateway/x", "--coordinator", oc, "--design-lead", oc, "--reviewer", oc,
                                    "--tiers", str(tiers)).returncode, 0)
        self.assertFalse(any(c.startswith("cotal setup") for c in self.e.calls()))
        r = self.e.run(str(repo), "--own-model", "gateway/x", "--coordinator", "claude/opus", "--design-lead", oc, "--reviewer", oc,
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
        rec = self.e.settings(repo)
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
        oc = "opencode/gateway/x"
        self.assertEqual(self.e.run(str(repo), "--own-model", "gateway/x", "--coordinator", oc, "--permission-mode", "bypassPermissions").returncode, 0)
        text = (repo / ".cotal" / "agents" / "coordinator.md").read_text()
        self.assertIn("`bypassPermissions`", text)
        self.assertIn(str(self.e.store(repo) / "lanes.tsv"), text)
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

    def forget_mode(self):
        """A project whose personas are the only record of its mode (the store holds none)."""
        f = self.e.store(self.repo) / "settings.json"
        st = json.loads(f.read_text())
        st.pop("permission_mode")
        f.write_text(json.dumps(st))

    def test_refresh_reinstalls_the_machine_tools(self):
        self.setup_project()
        tool = self.e.home / ".agents" / "bin" / "lane-heavy"
        tool.write_text("#!/bin/sh\necho old\n")
        r = self.refresh()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"lane-tools.*updated")
        self.assertEqual(tool.read_bytes(), (ROOT / "scripts" / "lane-heavy").read_bytes())

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
        table = self.e.store(self.repo) / "lanes.tsv"
        table.write_text(table.read_text() + "# project's own row\n")
        own = table.read_text()
        r = self.refresh()
        self.assertEqual(table.read_text(), own)
        self.assertIn("kept, differs from the defaults", r.stdout)

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

    def test_an_agent_pinned_to_a_hosted_model_is_not_listed_for_restart(self):
        self.setup_project()
        text = self.persona().read_text()
        self.persona().write_text(re.sub(r"^agent: .*$", "agent: opencode", re.sub(r"^model: .*$", "model: opencode/old", text, flags=re.M), flags=re.M) + "stale\n")
        state = json.loads(self.e.state.read_text())
        state["agents"] = [{"name": "coordinator", "status": "running", "role": "coordinator"}]
        self.e.state.write_text(json.dumps(state))
        r = self.refresh()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Do not restart it on that model", r.stderr)
        self.assertNotIn("restart: needed", r.stdout)

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
        self.setup_project("--own-model", "gw/m", "--coordinator", "opencode/gw/m", "--design-lead", "opencode/gw/m", "--reviewer", "opencode/gw/m",
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
        self.forget_mode()
        self.refresh()
        self.assertIn('"permission-mode": "default"', rv.read_text())
        self.assertIn('"permission-mode": "auto"', self.persona().read_text())

    def test_missing_reviewer_is_made_and_the_tier_table_kept(self):
        self.setup_project()
        self.persona("reviewer").unlink()
        table = self.e.store(self.repo) / "lanes.tsv"
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
        self.e.set_state(agents=[{"name": "lane-3-review", "cwd": "/x", "role": "reviewer"}])
        r = self.refresh()
        self.assertIn("restart: needed lane-3-review (persona changed)", r.stdout)

    def test_a_lane_whose_key_ends_in_review_is_not_a_reviewer(self):
        self.setup_project()
        self.persona("reviewer").write_text(self.persona("reviewer").read_text() + "stale\n")
        self.e.set_state(agents=[{"name": "code-review", "cwd": "/x", "role": "lane"}])
        self.assertIn("restart: none", self.refresh().stdout)

    def test_abbreviated_flags_are_refused_not_ignored(self):
        self.setup_project()
        for flag in (["--perm", "bypassPermissions"], ["--coord", "claude/sonnet"], ["--no-design"]):
            r = self.refresh(*flag)
            self.assertEqual(r.returncode, 2, flag)
            self.assertIn("unrecognized", r.stderr)

    def test_opencode_answer_cannot_change_on_refresh(self):
        self.setup_project()
        r = self.refresh("--opencode", "none")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--opencode", r.stderr)

    def test_refresh_keeps_the_answer_and_the_table(self):
        self.setup_project("--opencode", "own", "--opencode-model", "gateway/m1")
        table = self.e.store(self.repo) / "lanes.tsv"
        before = table.read_text()
        r = self.refresh()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(table.read_text(), before)
        self.assertIn("lanes.tsv kept (same as the defaults)", r.stdout)

    def test_refresh_rewrites_a_missing_table_from_the_answer(self):
        self.setup_project("--opencode", "none")
        table = self.e.store(self.repo) / "lanes.tsv"
        table.unlink()
        self.assertEqual(self.refresh().returncode, 0)
        self.assertIn("swift\tnone\t-\t-\tnone", table.read_text())

    def test_refresh_says_a_table_without_the_column_counts_as_required(self):
        self.setup_project("--tiers", str(self._old_table()))
        r = self.refresh()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("swift has sandbox required", r.stderr)

    def _old_table(self):
        f = self.e.home / "old.tsv"
        f.write_text("tier\tharness\tmodel\tpermission\nswift\topencode\tgw/m\t-\ndeep\tclaude\topus\tinherit\n")
        return f

    def test_refresh_warns_about_a_persona_already_on_a_hosted_model(self):
        self.setup_project()
        text = self.persona().read_text()
        text = re.sub(r"^agent: \S+$", "agent: opencode", text, flags=re.M)
        self.persona().write_text(re.sub(r"^model: \S+$", "model: opencode/old", text, flags=re.M))
        r = self.refresh()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("persona coordinator pins opencode/old", r.stderr)

    def test_refresh_with_a_table_it_cannot_answer_on_is_a_clean_failure(self):
        self.setup_project("--opencode", "none")
        r = self.refresh("--tiers", str(self._old_table()))
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("no sandbox column", r.stderr)

    def test_non_utf8_persona_is_a_clean_failure_and_writes_nothing(self):
        self.setup_project()
        self.persona().write_bytes(self.persona().read_bytes() + b"\xff")
        before = self.persona("reviewer").read_text()
        r = self.refresh()
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("cannot read a persona", r.stderr)
        self.assertEqual(self.persona("reviewer").read_text(), before)

    def test_a_mode_no_persona_records_is_said_out_loud(self):
        self.setup_project()
        for name in ("coordinator", "design-lead", "reviewer"):
            text = self.persona(name).read_text()
            text = re.sub(r'"permission-mode": "\w+"', '"x": "y"', text)
            self.persona(name).write_text(re.sub(r"permission mode is `\w+`", "mode", text))
        self.forget_mode()
        r = self.refresh()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("permission-mode: unknown", r.stdout)

    def test_missing_tier_file_is_a_clean_refusal_before_any_write(self):
        self.setup_project()
        self.persona().write_text(self.persona().read_text() + "stale\n")
        r = self.refresh("--tiers", str(self.e.home / "nope.tsv"))
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("stale", self.persona().read_text())

    def test_non_utf8_tier_table_is_a_clean_failure(self):
        self.setup_project()
        (self.e.store(self.repo) / "lanes.tsv").write_bytes(b"\xff\xfe\n")
        r = self.refresh()
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)

    def test_tier_report_names_the_differing_rows(self):
        self.setup_project()
        table = self.e.store(self.repo) / "lanes.tsv"
        table.write_text(table.read_text() + "mine\tclaude\topus\n")
        r = self.refresh()
        self.assertIn("only in the project: mine; only in the defaults: none", r.stdout)

    def test_flags_that_would_be_ignored_are_refused(self):
        self.setup_project()
        for flag in (["--coordinator", "claude/sonnet"], ["--permission-mode=default"], ["--no-design-lead"],
                     ["--design-lead", "claude/sonnet"]):
            r = self.refresh(*flag)
            self.assertEqual(r.returncode, 2, flag)
            self.assertIn("no effect with --refresh", r.stderr)

    def test_not_a_repository_is_refused_and_nothing_cloned(self):
        r = self.e.run(str(self.e.home / "nowhere"), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("--refresh only works", r.stderr)

    def test_cannot_combine_with_readapt(self):
        r = self.e.run(str(self.repo), "--refresh", "--readapt", adapt=True)
        self.assertEqual(r.returncode, 2)


TIER_TOOL = Path(__file__).resolve().parent.parent / "scripts" / "lane-tier"


class LaneTierTests(unittest.TestCase):
    HEAD = "tier\tharness\tmodel\tpermission"

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.table = Path(d.name) / "lanes.tsv"

    def tier(self, text, tier):
        self.table.write_text(text)
        return subprocess.run([str(TIER_TOOL), str(self.table), tier], capture_output=True, text=True)

    def test_required_tier_is_refused_and_names_the_ways_out(self):
        r = self.tier(f"{self.HEAD}\tsandbox\nswift\topencode\tm/x\t-\trequired\n", "swift")
        self.assertEqual(r.returncode, 4)
        self.assertEqual(r.stdout, "")
        for word in ("Rove", "sandbox none", "cotal_spawn"):
            self.assertIn(word, r.stderr)

    def test_a_tier_cell_with_a_space_is_a_bad_table(self):
        r = self.tier(f"{self.HEAD}\tsandbox\nmy tier\tclaude\tsonnet\tinherit\tnone\n", "my tier")
        self.assertEqual(r.returncode, 2)
        self.assertIn("contains a space", r.stderr)

    def test_a_required_tier_on_another_harness_or_provider_is_refused(self):
        for row in ("swift\tclaude\topencode/x\t-\trequired", "swift\topencode\tdeepseek/x\t-\trequired"):
            r = self.tier(f"{self.HEAD}\tsandbox\n{row}\n", "swift")
            self.assertEqual(r.returncode, 4, r.stderr)

    def test_none_tier_prints_its_row(self):
        r = self.tier(f"{self.HEAD}\tsandbox\nswift\topencode\tgw/m\t-\tnone\n", "swift")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "harness=opencode model=gw/m permission=- sandbox=none\n")

    def test_no_column_is_required_for_opencode_and_none_for_claude(self):
        text = f"{self.HEAD}\nswift\topencode\tgw/m\t-\ndeep\tclaude\topus\tinherit\n"
        self.assertEqual(self.tier(text, "swift").returncode, 4)
        r = self.tier(text, "deep")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("sandbox=none", r.stdout)

    def test_an_empty_cell_counts_like_no_column(self):
        self.assertEqual(self.tier(f"{self.HEAD}\tsandbox\nswift\topencode\tgw/m\t-\t\n", "swift").returncode, 4)

    def test_an_empty_model_cell_cannot_shift_required_away(self):
        self.assertEqual(self.tier(f"{self.HEAD}\tsandbox\nswift\topencode\t\t-\trequired\n", "swift").returncode, 4)

    def test_a_hosted_model_marked_none_is_a_bad_table(self):
        for model in ("opencode/deepseek-v4.1-flash", "opencode-go/kimi"):
            r = self.tier(f"{self.HEAD}\tsandbox\nswift\topencode\t{model}\t-\tnone\n", "swift")
            self.assertEqual(r.returncode, 2, model)
            self.assertIn("hosted OpenCode model", r.stderr)

    def test_an_empty_harness_is_a_bad_table(self):
        r = self.tier(f"{self.HEAD}\tsandbox\nswift\t\tgw/m\t-\tnone\n", "swift")
        self.assertEqual(r.returncode, 2)
        self.assertIn("empty tier or harness", r.stderr)

    def test_harness_none_is_skipped(self):
        self.assertEqual(self.tier(f"{self.HEAD}\tsandbox\ntop\tnone\t-\t-\tnone\n", "top").returncode, 3)

    def test_a_bad_sandbox_value_is_refused(self):
        r = self.tier(f"{self.HEAD}\tsandbox\nswift\topencode\tgw/m\t-\tmaybe\n", "swift")
        self.assertEqual(r.returncode, 2)
        self.assertIn("required or none", r.stderr)

    def test_unknown_tier_is_an_error(self):
        self.assertEqual(self.tier(f"{self.HEAD}\nswift\topencode\tm/x\t-\n", "ultra").returncode, 2)

    def test_columns_are_found_by_name(self):
        r = self.tier("sandbox\tmodel\ttier\tpermission\tharness\nnone\tgw/m\tswift\t-\topencode\n", "swift")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("model=gw/m", r.stdout)

    def test_comments_and_the_shipped_template_parse(self):
        r = subprocess.run([str(TIER_TOOL), "--check", str(TIER_TOOL.parent.parent / "templates" / "lanes.tsv")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "swift opencode required opencode/deepseek-v4.1-flash")


class OpencodeKeyCheckTests(unittest.TestCase):
    """The key check called directly: dormant until SBX-2, because setup refuses every table that would reach it."""
    HOSTED = [("swift", "opencode", "required", "opencode/deepseek-v4.1-flash")]

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.repo = Path(d.name)
        (self.repo / ".cotal").mkdir()
        (self.repo / "lanes.tsv").write_text("mocked by check_tier_table\n")
        self.m = load()

    def check(self, rows=None, key_file=None):
        """key_file: the file's text; None: no file at all."""
        f = self.repo / "sandbox-auth.json"
        f.unlink(missing_ok=True)
        if key_file is not None:
            f.write_text(key_file)
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"OPENCODE_SANDBOX_AUTH": str(f)}), contextlib.redirect_stderr(err), \
                mock.patch.object(self.m, "check_tier_table", return_value=self.HOSTED if rows is None else rows):
            self.m.opencode_key_check(self.repo / "lanes.tsv")
        return err.getvalue()

    def test_no_key_file_warns_with_the_steps_to_log_in(self):
        err = self.check()
        self.assertRegex(err, r"tier swift pins opencode/deepseek-v4\.1-flash.*no usable key .*does not exist")
        self.assertIn("Once per machine", err)
        self.assertIn('{"opencode-go":{"type":"api","key":"<the key>"}}', err)
        self.assertIn("opencode auth login` does not reach the sandbox", err)

    def test_a_usable_key_only_notes_what_a_spent_quota_looks_like(self):
        err = self.check(key_file='{"opencode-go":{"type":"api","key":"sentinel"}}')
        self.assertNotIn("warning:", err)
        self.assertIn("silent first turn", err)
        self.assertNotIn("sentinel", err)

    def test_a_key_under_either_provider_name_is_usable(self):
        self.assertNotIn("warning:", self.check(key_file='{"opencode":{"type":"api","key":"sentinel"}}'))

    def test_a_broken_or_keyless_file_warns_and_never_prints_the_contents(self):
        for text in ("not json", '{"opencode-go":{"type":"oauth","key":"sentinel-secret"}}', '{"opencode-go":{"type":"api","key":""}}', "[]"):
            err = self.check(key_file=text)
            self.assertIn("warning:", err)
            self.assertNotIn("sentinel-secret", err)

    def test_tiers_without_a_hosted_model_need_no_key(self):
        self.assertEqual(self.check([("a", "opencode", "none", "gateway/m1"), ("b", "claude", "none", "sonnet"),
                                     ("c", "none", "required", "-")]), "")


class ManagerGateTests(unittest.TestCase):
    """lane-tier allows a required tier only when the running manager of the project starts opencode-seat."""
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name).resolve()
        self.table = self.root / ".cotal" / "lanes.tsv"
        self.table.parent.mkdir()
        self.table.write_text("tier\tharness\tmodel\tpermission\tsandbox\nswift\topencode\topencode/x\t-\trequired\n")

    def manager(self, opencode_target):
        """A fake manager process of this root whose PATH starts with a directory holding `opencode`."""
        d = self.root / "bin"
        d.mkdir(exist_ok=True)
        (d / "opencode").unlink(missing_ok=True)
        (d / "opencode").symlink_to(opencode_target)
        p = subprocess.Popen(["python3", "-c", "import time; time.sleep(60)", "__service-manager", "--root", str(self.root)],
                             env={"PATH": f"{d}:{os.environ['PATH']}"})
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        for _ in range(100):  # wait for the exec: until then /proc shows the forked test runner
            if b"__service-manager" in Path(f"/proc/{p.pid}/cmdline").read_bytes():
                break
            time.sleep(0.02)

    def tier(self):
        return subprocess.run([str(TIER_TOOL), str(self.table), "swift"], capture_output=True, text=True)

    def test_refused_when_no_manager_runs(self):
        r = self.tier()
        self.assertEqual(r.returncode, 4)
        self.assertIn("no Cotal manager is running", r.stderr)

    def test_refused_when_the_manager_has_the_real_opencode(self):
        real = self.root / "real-opencode"
        real.write_text("#!/bin/sh\n")
        real.chmod(0o755)
        self.manager(real)
        r = self.tier()
        self.assertEqual(r.returncode, 4)
        self.assertIn("would run bare", r.stderr)

    def test_allowed_when_the_manager_runs_the_wrapper(self):
        self.manager(ROOT / "scripts" / "opencode-seat")
        r = self.tier()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "harness=opencode model=opencode/x permission=- sandbox=required\n")

    def test_refused_when_the_manager_is_another_projects(self):
        self.manager(ROOT / "scripts" / "opencode-seat")
        other = self.root / "other" / ".cotal"
        other.mkdir(parents=True)
        (other / "lanes.tsv").write_text(self.table.read_text())
        r = subprocess.run([str(TIER_TOOL), str(other / "lanes.tsv"), "swift"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 4)
        self.assertIn("no Cotal manager is running", r.stderr)

    def test_a_store_table_finds_its_manager_by_the_root_in_settings_json(self):
        store = self.root / "cfg" / "lane-control" / "projects" / "p"
        store.mkdir(parents=True)
        (store / "lanes.tsv").write_text(self.table.read_text())
        (store / "settings.json").write_text(json.dumps({"root": str(self.root)}))
        r = subprocess.run([str(TIER_TOOL), str(store / "lanes.tsv"), "swift"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 4)
        self.assertIn("no Cotal manager is running", r.stderr)
        self.manager(ROOT / "scripts" / "opencode-seat")
        r = subprocess.run([str(TIER_TOOL), str(store / "lanes.tsv"), "swift"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_store_table_without_a_root_is_refused_and_says_why(self):
        store = self.root / "cfg" / "lane-control" / "projects" / "p"
        store.mkdir(parents=True)
        (store / "lanes.tsv").write_text(self.table.read_text())
        r = subprocess.run([str(TIER_TOOL), str(store / "lanes.tsv"), "swift"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 4)
        self.assertIn("settings.json", r.stderr)

    def test_a_table_anywhere_else_is_refused(self):
        other = self.root / "elsewhere" / "lanes.tsv"
        other.parent.mkdir()
        other.write_text(self.table.read_text())
        r = subprocess.run([str(TIER_TOOL), str(other), "swift"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 4)
        self.assertIn("not a project's tier table", r.stderr)


class SettingsStoreTests(unittest.TestCase):
    """The per-project store under ~/.config/lane-control/projects/<folder>/, outside .cotal/ (SET-1)."""
    def setUp(self):
        self.e = Env()
        self.addCleanup(self.e.close)
        self.repo = self.e.repo()
        self.store = self.e.store(self.repo)

    def run_setup(self, *args, **kw):
        r = self.e.run(str(self.repo), *args, **kw)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def legacy(self, table_text=None):
        """Put the project back as a setup from before the store left it: record and table in .cotal/, no store."""
        rec = self.e.settings(self.repo)
        old = {k: rec[k] for k in ("port", "opencode", "opencode_model", "claude_setup", "own_models") if k in rec}
        (self.repo / ".cotal" / "lane-control.json").write_text(json.dumps(old))
        (self.repo / ".cotal" / "lanes.tsv").write_bytes(table_text if table_text is not None else (self.store / "lanes.tsv").read_bytes())
        shutil.rmtree(self.store)
        return old

    def test_setup_writes_the_store_and_nothing_of_it_into_cotal(self):
        self.run_setup("--private", "shot*.png", "--no-design-lead", "--coordinator", "claude/sonnet", "--name", "Shop")
        self.assertTrue((self.store / "lanes.tsv").is_file())
        st = self.e.settings(self.repo)
        self.assertEqual(st["private"], ["shot*.png"])
        self.assertFalse(st["design_lead"])
        self.assertEqual(st["models"]["coordinator"], "claude/sonnet")
        self.assertEqual((st["name"], st["root"], st["permission_mode"]), ("Shop", str(self.repo), "auto"))
        self.assertFalse((self.repo / ".cotal" / "lanes.tsv").exists())
        self.assertFalse((self.repo / ".cotal" / "lane-control.json").exists())
        self.assertTrue((self.repo / ".cotal" / "agents" / "coordinator.md").is_file())
        self.assertIn(str(self.store / "lanes.tsv"), (self.repo / ".cotal" / "agents" / "coordinator.md").read_text())

    def test_the_store_is_private_to_its_owner(self):
        self.run_setup()
        self.assertEqual((self.store / "settings.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.stat().st_mode & 0o777, 0o700)

    def test_a_run_without_flags_reuses_the_store(self):
        self.run_setup("--private", "shot*.png", "--no-design-lead", "--coordinator", "claude/sonnet", "--permission-mode",
                       "bypassPermissions", "--name", "Shop")
        (self.repo / ".cotal" / "agents" / "design-lead.md").unlink(missing_ok=True)
        self.run_setup()
        st = self.e.settings(self.repo)
        self.assertEqual((st["models"]["coordinator"], st["design_lead"], st["permission_mode"], st["name"]),
                         ("claude/sonnet", False, "bypassPermissions", "Shop"))
        text = (self.repo / ".cotal" / "agents" / "coordinator.md").read_text()
        self.assertIn("model: sonnet", text)
        self.assertIn("bypassPermissions", text)
        self.assertIn("Shop", text)
        self.assertFalse((self.repo / ".cotal" / "agents" / "design-lead.md").exists())
        self.assertIn("This project has no design lead", text)

    def test_private_globs_add_up_and_a_later_run_never_drops_one(self):
        self.run_setup("--private", "a*.png")
        self.run_setup("--private", "docs/private/**", "--private", "a*.png")
        self.run_setup()
        self.assertEqual(self.e.settings(self.repo)["private"], ["a*.png", "docs/private/**"])

    def test_the_design_lead_comes_back_with_its_flag(self):
        self.run_setup("--no-design-lead")
        self.assertFalse(self.e.settings(self.repo)["design_lead"])
        self.run_setup("--design-lead", "claude/opus")
        self.assertTrue(self.e.settings(self.repo)["design_lead"])
        self.assertTrue((self.repo / ".cotal" / "agents" / "design-lead.md").is_file())

    def test_an_unsafe_stored_glob_or_mode_is_refused_and_named(self):
        self.run_setup()
        st = self.e.settings(self.repo)
        (self.store / "settings.json").write_text(json.dumps({**st, "private": ["../x"]}))
        r = self.e.run(str(self.repo))
        self.assertEqual(r.returncode, 1)
        self.assertIn("settings.json", r.stderr)
        (self.store / "settings.json").write_text(json.dumps({**st, "permission_mode": "yolo"}))
        self.assertEqual(self.e.run(str(self.repo)).returncode, 1)

    def test_a_broken_settings_file_stops_the_run_and_is_not_overwritten(self):
        self.run_setup()
        (self.store / "settings.json").write_text("{ nope")
        r = self.e.run(str(self.repo))
        self.assertEqual(r.returncode, 1)
        self.assertIn("settings.json", r.stderr)
        self.assertEqual((self.store / "settings.json").read_text(), "{ nope")

    def test_only_known_names_are_ever_written(self):
        m = load()
        with mock.patch.dict(os.environ, {"HOME": str(self.e.home), "XDG_CONFIG_HOME": ""}):
            with self.assertRaises(m.Fail):
                m.settings(self.repo, api_key="x")
            self.assertFalse((self.store / "settings.json").exists())
        for k in ("root", "name", "models", "design_lead", "permission_mode", "private", "opencode", "opencode_model",
                  "own_models", "port", "claude_setup"):
            self.assertIn(k, m.SETTING_KEYS)
        self.assertFalse([k for k in m.SETTING_KEYS if re.search(r"key|token|secret|password|auth", k)])

    def test_xdg_config_home_moves_the_store(self):
        xdg = self.e.home / "xdg"
        self.run_setup(env_extra={"XDG_CONFIG_HOME": str(xdg)})
        self.assertTrue((xdg / "lane-control" / "projects" / store_key(self.repo) / "settings.json").is_file())
        self.assertFalse(self.store.exists())

    def test_a_project_set_up_before_the_store_is_moved_once_and_says_what(self):
        self.run_setup("--port", "14777", "--name", "Shop", "--coordinator", "claude/sonnet", "--no-design-lead")
        edited = (self.store / "lanes.tsv").read_bytes() + b"# the owner's own note\n"
        self.legacy(edited)
        r = self.run_setup()
        self.assertIn("settings: moved", r.stdout)
        self.assertIn("lane-control.json", r.stdout)
        self.assertIn("lanes.tsv", r.stdout)
        self.assertEqual((self.store / "lanes.tsv").read_bytes(), edited)
        self.assertFalse((self.repo / ".cotal" / "lanes.tsv").exists())
        self.assertFalse((self.repo / ".cotal" / "lane-control.json").exists())
        st = self.e.settings(self.repo)
        self.assertEqual(st["port"], 14777)
        # what the old record never held comes from the personas, so a re-run without flags keeps it
        self.assertEqual((st["models"]["coordinator"], st["design_lead"]), ("claude/sonnet", False))
        self.assertIn("model: sonnet", (self.repo / ".cotal" / "agents" / "coordinator.md").read_text())
        self.assertNotIn("settings: moved", self.run_setup().stdout)  # once

    def test_refresh_moves_a_project_too_and_keeps_an_edited_table(self):
        self.run_setup()
        edited = (self.store / "lanes.tsv").read_bytes().replace(b"swift", b"swiftest")
        self.legacy(edited)
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("settings: moved", r.stdout)
        self.assertEqual((self.store / "lanes.tsv").read_bytes(), edited)
        self.assertEqual(self.e.settings(self.repo)["root"], str(self.repo))

    def test_a_store_table_that_differs_from_the_old_one_wins_and_the_old_stays(self):
        self.run_setup()
        mine = (self.store / "lanes.tsv").read_bytes()
        (self.repo / ".cotal" / "lanes.tsv").write_bytes(mine + b"# old\n")
        r = self.run_setup()
        self.assertEqual((self.store / "lanes.tsv").read_bytes(), mine)
        self.assertEqual((self.repo / ".cotal" / "lanes.tsv").read_bytes(), mine + b"# old\n")
        self.assertIn("left where it is", r.stderr)

    def test_a_store_that_has_the_same_table_lets_the_old_copy_go(self):
        self.run_setup()
        (self.repo / ".cotal" / "lanes.tsv").write_bytes((self.store / "lanes.tsv").read_bytes())
        self.assertIn("removed", self.run_setup().stdout)
        self.assertFalse((self.repo / ".cotal" / "lanes.tsv").exists())

    def test_store_values_win_over_the_old_record(self):
        self.run_setup("--port", "14888")
        (self.repo / ".cotal" / "lane-control.json").write_text(json.dumps({"port": 14999, "opencode": "none"}))
        self.run_setup()
        st = self.e.settings(self.repo)
        self.assertEqual(st["port"], 14888)
        self.assertFalse((self.repo / ".cotal" / "lane-control.json").exists())

    def test_refresh_respects_the_store_design_lead_and_fills_what_it_lacks(self):
        self.run_setup()
        st = self.e.settings(self.repo)
        for k in ("models", "design_lead", "permission_mode"):
            st.pop(k)
        (self.store / "settings.json").write_text(json.dumps(st))
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        st = self.e.settings(self.repo)
        self.assertEqual(st["models"]["coordinator"], "claude/opus")
        self.assertTrue(st["design_lead"])
        st["design_lead"] = False
        (self.store / "settings.json").write_text(json.dumps(st))
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertIn("This project has no design lead", (self.repo / ".cotal" / "agents" / "coordinator.md").read_text())
        self.assertNotIn("persona design-lead", r.stdout)

    def test_show_prints_the_store_and_writes_nothing(self):
        self.run_setup("--private", "shot*.png")
        (self.store / "sandbox-ports").write_text("# dev\n5432\n")
        before = {p: p.read_bytes() for p in self.store.iterdir()}
        r = self.e.run(str(self.repo), "--show", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        for needle in (str(self.store), "shot*.png", "lanes.tsv", "tiers: swift", "sandbox-ports", "5432", '"design_lead": true'):
            self.assertIn(needle, r.stdout)
        self.assertEqual({p: p.read_bytes() for p in self.store.iterdir()}, before)

    def test_show_on_an_old_project_says_it_is_not_moved_and_moves_nothing(self):
        self.run_setup()
        self.legacy()
        r = self.e.run(str(self.repo), "--show", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("not moved yet", r.stdout)
        self.assertIn("(not there yet)", r.stdout)
        self.assertTrue((self.repo / ".cotal" / "lanes.tsv").exists())
        self.assertFalse(self.store.exists())

    def test_show_on_something_that_is_not_a_repo_fails_plainly(self):
        d = self.e.home / "plain"
        d.mkdir()
        r = self.e.run(str(d), "--show", adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)

    def test_two_checkouts_with_the_same_folder_name_get_separate_stores(self):
        other = self.e.home / "work" / "app"
        other.mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=other)
        git("commit", "-q", "--allow-empty", "-m", "init", cwd=other)
        self.e.trust(other)
        self.run_setup("--private", "a*.png")
        r = self.e.run(str(other), "--private", "b*.png", "--port", "14555", "--name", "app2", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotEqual(self.e.store(self.repo), self.e.store(other))
        self.assertEqual(self.e.settings(self.repo)["private"], ["a*.png"])
        self.assertEqual(self.e.settings(other)["private"], ["b*.png"])
        self.assertTrue(self.e.store(self.repo).name.startswith("app-"))

    def test_a_store_keyed_on_the_folder_name_alone_moves_to_the_path_key(self):
        first = self.e.home / ".config" / "lane-control" / "projects" / "app"
        first.mkdir(parents=True)
        (first / "sandbox-ports").write_text("5432\n")
        r = self.run_setup()
        self.assertIn("settings: moved", r.stdout)
        self.assertFalse(first.exists())
        self.assertEqual((self.store / "sandbox-ports").read_text(), "5432\n")
        self.assertTrue((self.store / "settings.json").is_file())

    def test_a_name_only_store_of_another_checkout_stays_where_it_is(self):
        first = self.e.home / ".config" / "lane-control" / "projects" / "app"
        first.mkdir(parents=True)
        (first / "settings.json").write_text(json.dumps({"root": "/somewhere/else/app"}))
        self.run_setup()
        self.assertTrue((first / "settings.json").exists())
        self.assertFalse((self.store / "sandbox-ports").exists())

    def test_the_sandbox_and_the_seat_derive_the_same_key(self):
        key = store_key(self.repo)
        sh = subprocess.run(["sh", "-c", f'printf "%s-%s" "$(basename {shlex.quote(str(self.repo.resolve()))})" '
                             f'"$(printf %s {shlex.quote(str(self.repo.resolve()))} | sha256sum | cut -c1-10)"'],
                            capture_output=True, text=True).stdout
        self.assertEqual(sh, key)

    def test_a_normal_run_that_changes_a_running_persona_says_restart(self):
        self.run_setup("--name", "Shop")
        self.legacy()
        p = self.repo / ".cotal" / "agents" / "coordinator.md"
        p.write_text(p.read_text().replace(str(self.store / "lanes.tsv"), str(self.repo / ".cotal" / "lanes.tsv")))
        r = self.run_setup()
        self.assertIn("persona coordinator: updated", r.stdout)
        self.assertRegex(r.stdout, r"restart: needed coordinator \(persona changed\)")

    def test_a_first_run_names_no_restart(self):
        self.assertNotIn("restart:", self.run_setup().stdout)

    def test_lane_tier_on_the_old_path_says_it_moved(self):
        self.run_setup()
        r = subprocess.run([str(TIER_TOOL), str(self.repo / ".cotal" / "lanes.tsv"), "standard"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("moved to the project's settings store", r.stderr)

    def test_the_project_name_survives_the_move(self):
        self.run_setup("--name", "Shop")
        self.legacy()
        self.e.log.write_text("")
        self.run_setup()
        self.assertEqual(self.e.settings(self.repo)["name"], "Shop")
        self.assertNotIn("space create app", " ".join(self.e.calls()))
        self.assertIn("Shop coordinator;", (self.repo / ".cotal" / "agents" / "coordinator.md").read_text())

    def test_refresh_takes_the_permission_mode_from_the_store(self):
        self.run_setup()
        st = self.e.settings(self.repo)
        (self.store / "settings.json").write_text(json.dumps({**st, "permission_mode": "bypassPermissions"}))
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        for n in ("coordinator", "design-lead", "reviewer"):
            self.assertIn('"permission-mode": "bypassPermissions"', (self.repo / ".cotal" / "agents" / f"{n}.md").read_text())

    def test_personas_with_different_modes_are_left_alone_and_none_is_stored(self):
        self.run_setup("--permission-mode", "bypassPermissions")
        rv = self.repo / ".cotal" / "agents" / "reviewer.md"
        rv.write_text(rv.read_text().replace("bypassPermissions", "default"))
        before = {n: (self.repo / ".cotal" / "agents" / f"{n}.md").read_text() for n in ("coordinator", "reviewer")}
        self.legacy()
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("permission-mode: mixed", r.stdout)
        self.assertNotIn("permission_mode", self.e.settings(self.repo))
        for n, text in before.items():
            self.assertEqual((self.repo / ".cotal" / "agents" / f"{n}.md").read_text(), text)

    def test_refresh_name_is_stored(self):
        self.run_setup("--name", "A")
        self.assertEqual(self.e.run(str(self.repo), "--refresh", "--name", "B", adapt=True).returncode, 0)
        self.assertEqual(self.e.settings(self.repo)["name"], "B")
        self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertIn("B coordinator;", (self.repo / ".cotal" / "agents" / "coordinator.md").read_text())

    def test_a_moved_checkout_gets_a_new_store_and_a_warning_naming_the_old_one(self):
        self.run_setup()
        old_store = self.store
        new = self.e.home / "elsewhere" / "app"  # the checkout moved to another parent, same folder name
        new.parent.mkdir()
        self.repo.rename(new)
        r = self.e.run(str(new), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("belongs to " + str(self.repo) + ", which is gone", r.stderr)
        self.assertIn(str(old_store), r.stderr)
        self.assertNotEqual(self.e.store(new), old_store)

    def test_an_own_model_stored_earlier_allows_a_later_pin_without_the_flag(self):
        self.run_setup("--own-model", "my/x")
        r = self.e.run(str(self.repo), "--coordinator", "opencode/my/x", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.e.run(str(self.repo), "--coordinator", "opencode/my/y", "--no-start")
        self.assertEqual(r.returncode, 2)

    def test_hand_edited_values_of_the_wrong_type_are_refused_and_named(self):
        self.run_setup()
        st = self.e.settings(self.repo)
        for key, bad in (("private", "secret"), ("private", 5), ("own_models", "my/x"), ("models", "claude/opus"),
                         ("design_lead", "no"), ("private", [1])):
            (self.store / "settings.json").write_text(json.dumps({**st, key: bad}))
            r = self.e.run(str(self.repo))
            self.assertEqual(r.returncode, 1, (key, bad))
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn(key, r.stderr)

    def mixed_personas(self):
        self.run_setup("--permission-mode", "bypassPermissions")
        agents = self.repo / ".cotal" / "agents"
        for n, mode in (("reviewer", "default"), ("design-lead", "auto")):
            (agents / f"{n}.md").write_text((agents / f"{n}.md").read_text().replace("bypassPermissions", mode))
        st = self.e.settings(self.repo)
        st.pop("permission_mode")
        (self.store / "settings.json").write_text(json.dumps(st))
        return agents

    def test_a_normal_run_keeps_each_personas_mode_when_they_differ(self):
        agents = self.mixed_personas()
        before = {n: (agents / f"{n}.md").read_text() for n in ("coordinator", "design-lead", "reviewer")}
        self.run_setup("--no-start")
        for n, text in before.items():
            self.assertEqual((agents / f"{n}.md").read_text(), text, n)
        self.assertNotIn("permission_mode", self.e.settings(self.repo))
        self.run_setup("--no-start", "--permission-mode", "default")  # a flag still makes them one
        self.assertEqual(self.e.settings(self.repo)["permission_mode"], "default")

    def test_a_normal_run_with_one_recorded_mode_keeps_it_without_a_flag(self):
        self.run_setup("--permission-mode", "bypassPermissions")
        st = self.e.settings(self.repo)
        st.pop("permission_mode")
        (self.store / "settings.json").write_text(json.dumps(st))
        self.run_setup("--no-start")
        self.assertEqual(self.e.settings(self.repo)["permission_mode"], "bypassPermissions")
        self.assertIn("bypassPermissions", (self.repo / ".cotal" / "agents" / "coordinator.md").read_text())

    def test_a_renamed_checkout_warns_naming_the_old_store(self):
        self.run_setup()
        new = self.repo.parent / "renamed"
        self.repo.rename(new)
        r = self.e.run(str(new), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(str(self.store), r.stderr)

    def test_a_refused_pin_leaves_nothing_behind(self):
        d = self.e.home / "fresh"
        d.mkdir()
        r = self.e.run(str(d), "--coordinator", "opencode/other/y", "--no-start")
        self.assertEqual(r.returncode, 2)
        self.assertFalse((d / ".git").exists())

    def test_a_stored_own_model_still_allows_the_pin_on_a_project_not_yet_moved(self):
        self.run_setup()
        old = {"own_models": ["my/x"]}
        (self.repo / ".cotal" / "lane-control.json").write_text(json.dumps(old))
        r = self.e.run(str(self.repo), "--coordinator", "opencode/my/x", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_migrating_run_with_no_start_still_names_a_running_coordinator(self):
        self.run_setup()
        self.legacy()
        p = self.repo / ".cotal" / "agents" / "coordinator.md"
        p.write_text(p.read_text().replace(str(self.store / "lanes.tsv"), str(self.repo / ".cotal" / "lanes.tsv")))
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"restart: needed coordinator")

    def test_refresh_name_on_a_project_never_set_up_stores_nothing(self):
        r = self.e.run(str(self.repo), "--refresh", "--name", "Z", adapt=True)
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.store / "settings.json").exists())

    def test_models_with_a_non_string_value_are_refused_not_a_traceback(self):
        self.run_setup()
        st = self.e.settings(self.repo)
        (self.store / "settings.json").write_text(json.dumps({**st, "models": {**st["models"], "reviewer": 5}}))
        for args in ((), ("--refresh",)):
            r = self.e.run(str(self.repo), *args, adapt=bool(args))
            self.assertEqual(r.returncode, 1, args)
            self.assertNotIn("Traceback", r.stderr)

    def test_show_is_exclusive_with_refresh(self):
        self.assertEqual(self.e.run(str(self.repo), "--show", "--refresh", adapt=True).returncode, 2)


class SeatWrapperTests(unittest.TestCase):
    """scripts/opencode-seat decides where a Cotal seat's opencode runs. The sandbox and real binary are stubs."""
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.d = Path(d.name).resolve()
        tools = self.d / "tools"
        tools.mkdir()
        shutil.copy2(ROOT / "scripts" / "opencode-seat", tools / "opencode-seat")
        shutil.copy2(ROOT / "scripts" / "lane-tier", tools / "lane-tier")
        self.log = self.d / "log"
        stub = f'#!/bin/sh\nprintf "%s %s seat=%s path=%s\\n" "$(basename "$0")" "$*" "${{OPENCODE_SANDBOX_SEAT:-}}" "$PATH" >>{self.log}\n'
        (tools / "opencode-sandbox.sh").write_text(stub)
        (tools / "opencode-sandbox.sh").chmod(0o755)
        real = self.d / "real"
        real.mkdir()
        (real / "opencode").write_text(stub)
        (real / "opencode").chmod(0o755)
        self.shim = self.d / "shim"
        self.shim.mkdir()
        (self.shim / "opencode").symlink_to(tools / "opencode-seat")
        self.home = self.d / "home"
        (self.home / ".cotal").mkdir(parents=True)

    def seat(self, *args, model=None, config=None):
        env = {"PATH": f"{self.shim}:{self.d / 'real'}:{os.environ['PATH']}", "COTAL_OPENCODE_HOME": str(self.home),
               "HOME": str(self.d / "cfg")}
        if model is not None:
            config = json.dumps({"model": model})
        if config is not None:
            env["OPENCODE_CONFIG_CONTENT"] = config
        return subprocess.run([str(self.shim / "opencode"), *args], env=env, capture_output=True, text=True)

    def ran(self):
        return self.log.read_text() if self.log.exists() else ""

    SERVE = ("serve", "--hostname", "127.0.0.1", "--port", "4096")

    def test_a_hosted_model_goes_into_the_sandbox_in_seat_mode(self):
        for model in ("opencode/deepseek-v4.1-flash", "opencode-go/kimi-k2.7"):
            self.log.unlink(missing_ok=True)
            r = self.seat(*self.SERVE, model=model)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertRegex(self.ran(), r"^opencode-sandbox\.sh serve --hostname 127\.0\.0\.1 --port 4096 seat=1 ")
            self.assertNotIn("\nopencode ", self.ran())

    def test_the_sandbox_never_finds_the_wrapper_as_opencode(self):
        self.seat(*self.SERVE, model="opencode/x")
        self.assertNotIn(str(self.shim), self.ran())

    def test_a_model_declared_own_runs_the_real_binary_unwrapped(self):
        (self.home / ".cotal" / "lane-control.json").write_text('{"own_models":["gateway/m1"]}')
        r = self.seat(*self.SERVE, model="gateway/m1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(self.ran(), r"^opencode serve .* seat= ")

    def store(self):
        d = self.d / "cfg" / ".config" / "lane-control" / "projects" / store_key(self.home)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_a_model_declared_own_in_the_store_runs_the_real_binary(self):
        (self.store() / "settings.json").write_text('{"own_models":["gateway/m1"]}')
        r = self.seat(*self.SERVE, model="gateway/m1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(self.ran(), r"^opencode serve .* seat= ")

    def test_a_none_row_in_the_store_runs_unwrapped_and_the_store_beats_the_old_files(self):
        (self.store() / "lanes.tsv").write_text("tier\tharness\tmodel\tpermission\tsandbox\nswift\topencode\tgateway/m1\t-\tnone\n")
        (self.home / ".cotal" / "lane-control.json").write_text('{"own_models":["gateway/other"]}')
        self.assertEqual(self.seat(*self.SERVE, model="gateway/m1").returncode, 0)
        self.assertEqual(self.seat(*self.SERVE, model="gateway/other").returncode, 2)  # old files are not read once the store has any

    def test_a_model_outside_the_table_counts_as_required_and_is_refused(self):
        (self.home / ".cotal" / "lanes.tsv").write_text("tier\tharness\tmodel\tpermission\tsandbox\nswift\topencode\tgateway/other\t-\tnone\n")
        (self.home / ".cotal" / "lane-control.json").write_text('{"own_models":["gateway/another"]}')
        r = self.seat(*self.SERVE, model="deepseek/deepseek-chat")
        self.assertEqual(r.returncode, 2)
        self.assertIn("counts as sandbox required", r.stderr)
        self.assertEqual(self.ran(), "")

    def test_with_no_project_records_an_unknown_model_is_refused(self):
        r = self.seat(*self.SERVE, model="gateway/m1")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.ran(), "")

    def test_an_agent_on_another_model_is_refused_even_for_an_own_model(self):
        (self.home / ".cotal" / "lane-control.json").write_text('{"own_models":["gateway/mine"]}')
        for config in ({"model": "gateway/mine", "default_agent": "cotal", "agent": {"cotal": {"model": "opencode/deepseek-v4.1-flash"}}},
                       {"model": "gateway/mine", "agent": {"other": {"model": "gateway/not-mine"}}}):
            r = self.seat(*self.SERVE, config=json.dumps(config))
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("another model", r.stderr)
            self.assertEqual(self.ran(), "")

    def test_an_agent_on_the_checked_model_runs(self):
        (self.home / ".cotal" / "lane-control.json").write_text('{"own_models":["gateway/mine"]}')
        config = {"model": "gateway/mine", "default_agent": "cotal", "agent": {"cotal": {"model": "gateway/mine", "mode": "primary"}}}
        self.assertEqual(self.seat(*self.SERVE, config=json.dumps(config)).returncode, 0)
        self.assertRegex(self.ran(), r"^opencode serve")

    def test_no_or_broken_config_is_refused_and_nothing_runs(self):
        for config in (None, "not json", "{}"):
            r = self.seat(*self.SERVE, config=config)
            self.assertEqual(r.returncode, 2)
            self.assertIn("no model pinned", r.stderr)
            self.assertEqual(self.ran(), "")

    def test_a_required_row_on_an_other_provider_is_refused(self):
        (self.home / ".cotal" / "lanes.tsv").write_text("tier\tharness\tmodel\tpermission\tsandbox\nswift\topencode\tgateway/m1\t-\trequired\n")
        r = self.seat(*self.SERVE, model="gateway/m1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("sandbox required", r.stderr)
        self.assertEqual(self.ran(), "")

    def test_a_none_row_runs_unwrapped(self):
        (self.home / ".cotal" / "lanes.tsv").write_text("tier\tharness\tmodel\tpermission\tsandbox\nswift\topencode\tgateway/m1\t-\tnone\n")
        self.assertEqual(self.seat(*self.SERVE, model="gateway/m1").returncode, 0)
        self.assertRegex(self.ran(), r"^opencode serve")

    def test_what_the_connector_asks_without_a_model_is_the_real_binary(self):
        for args in (("--version",), ("models", "--pure", "--verbose"), ("--server", "http://127.0.0.1:1", "--session", "s"), ("attach", "u")):
            self.log.unlink(missing_ok=True)
            self.assertEqual(self.seat(*args).returncode, 0)
            self.assertRegex(self.ran(), r"^opencode ")

    def test_anything_else_is_refused(self):
        for args in (("run", "hi"), ()):
            r = self.seat(*args, model="opencode/x")
            self.assertEqual(r.returncode, 2)
            self.assertIn("refusing", r.stderr)
            self.assertEqual(self.ran(), "")


class OpencodeChoiceTests(unittest.TestCase):
    """The once-per-project question: how OpenCode lanes run, written to .cotal/lanes.tsv."""
    def setUp(self):
        self.e = Env()
        self.addCleanup(self.e.close)
        self.repo = self.e.repo()
        self.table = self.e.store(self.repo) / "lanes.tsv"

    def in_terminal(self, answers, *args):
        m = load()
        out = Tty()
        with mock.patch.dict(os.environ, self.e.env, clear=True), mock.patch("sys.stdin", Tty()), \
                mock.patch("builtins.input", side_effect=answers) as ask, contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            status = m.main([str(self.repo), "--skip-adapt", *args])
        return status, out.getvalue(), ask

    def models(self, *names):
        p = self.e.bin / "opencode"
        p.write_text("#!/bin/sh\nprintf '%s\\n' " + " ".join(shlex.quote(n) for n in names) + "\n")

    def row(self, tier="swift"):
        return next(l for l in self.table.read_text().splitlines() if l.startswith(tier + "\t"))

    def test_without_a_terminal_or_a_flag_it_is_hosted(self):
        r = self.e.run(str(self.repo))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.row(), "swift\topencode\topencode/deepseek-v4.1-flash\t-\trequired")
        self.assertIn("opencode: default hosted", r.stdout)
        self.assertIn("swift has sandbox required", r.stderr)

    def test_flag_none_skips_the_tier(self):
        r = self.e.run(str(self.repo), "--opencode", "none")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.row(), "swift\tnone\t-\t-\tnone")

    def test_flag_own_sets_the_model_and_no_sandbox(self):
        r = self.e.run(str(self.repo), "--opencode", "own", "--opencode-model", "gateway/m1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.row(), "swift\topencode\tgateway/m1\t-\tnone")

    def test_bad_flag_combinations_are_refused(self):
        tiers = self.e.home / "t.tsv"
        tiers.write_text("tier\tharness\tmodel\tpermission\n")
        for args in (["--opencode", "own"], ["--opencode", "own", "--opencode-model", "opencode/deepseek-v4.1-flash"],
                     ["--opencode", "own", "--opencode-model", "opencode-go/x"], ["--opencode-model", "gw/m"],
                     ["--opencode", "hosted", "--tiers", str(tiers)]):
            self.assertEqual(self.e.run(str(self.repo), *args).returncode, 2, args)
        self.assertFalse(self.table.exists())

    def test_a_bad_tiers_file_stops_setup(self):
        tiers = self.e.home / "t.tsv"
        tiers.write_text("tier\tharness\tmodel\tpermission\tsandbox\nswift\topencode\tgw/m\t-\tperhaps\n")
        r = self.e.run(str(self.repo), "--tiers", str(tiers))
        self.assertEqual(r.returncode, 1)
        self.assertIn("required or none", r.stderr)

    def test_full_tiers_file_is_used_as_it_is(self):
        tiers = self.e.home / "t.tsv"
        tiers.write_text("tier\tharness\tmodel\tpermission\nswift\topencode\tgw/m\t-\n")
        r = self.e.run(str(self.repo), "--tiers", str(tiers))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.table.read_text(), tiers.read_text())

    def test_terminal_answer_1_is_hosted(self):
        status, _, _ = self.in_terminal(["1"])
        self.assertEqual(status, 0)
        self.assertTrue(self.row().endswith("\trequired"))

    def test_terminal_answer_3_is_claude_only(self):
        status, _, _ = self.in_terminal(["3"])
        self.assertEqual(status, 0)
        self.assertEqual(self.row(), "swift\tnone\t-\t-\tnone")

    def test_terminal_answer_2_lists_own_models_without_hosted_ones(self):
        self.models("opencode/deepseek-v4.1-flash", "opencode-go/kimi", "gateway/m1", "gateway/m2")
        status, out, _ = self.in_terminal(["2", "2"])
        self.assertEqual(status, 0)
        self.assertEqual(self.row(), "swift\topencode\tgateway/m2\t-\tnone")
        self.assertIn("gateway/m1", out)
        self.assertNotIn("opencode/deepseek", out.split("lanes run")[1].split("opencode: chosen")[0])

    def test_terminal_answer_2_says_it_writes_no_sandbox(self):
        _, out, _ = self.in_terminal(["2", "local/typed"])
        self.assertIn("writes no sandbox", out)

    def test_terminal_answer_2_takes_a_typed_name(self):
        status, _, _ = self.in_terminal(["2", "local/typed"])
        self.assertEqual(status, 0)
        self.assertIn("local/typed", self.row())

    def test_a_typed_hosted_model_is_never_written_as_no_sandbox(self):
        status, _, _ = self.in_terminal(["2", "opencode/x", "opencode-go/y", "nope"])
        self.assertEqual(status, 0)
        self.assertTrue(self.row().endswith("\trequired"))
        self.assertIn("opencode/deepseek", self.row())

    def test_no_answer_stays_hosted(self):
        status, _, _ = self.in_terminal(["x", "y", "z"])
        self.assertEqual(status, 0)
        self.assertTrue(self.row().endswith("\trequired"))

    def test_a_rerun_does_not_ask_again_and_keeps_the_table(self):
        status, _, _ = self.in_terminal(["3"])
        self.assertEqual(status, 0)
        edited = self.table.read_text() + "extra\tclaude\tsonnet\tinherit\tnone\n"
        self.table.write_text(edited)
        status, _, ask = self.in_terminal([])
        self.assertEqual(status, 0)
        ask.assert_not_called()
        self.assertEqual(self.table.read_text(), edited)

    def test_opencode_flag_refuses_to_replace_an_edited_table(self):
        self.assertEqual(self.e.run(str(self.repo), "--opencode", "hosted").returncode, 0)
        edited = self.table.read_text() + "custom\tclaude\tsonnet\tinherit\tnone\n"
        self.table.write_text(edited)
        r = self.e.run(str(self.repo), "--opencode", "none")
        self.assertEqual(r.returncode, 1)
        self.assertIn("differs from the current defaults", r.stderr)
        self.assertEqual(self.table.read_text(), edited)

    def test_opencode_flag_takes_an_unedited_table_from_before_the_sandbox_column(self):
        self.assertEqual(self.e.run(str(self.repo), "--opencode", "hosted").returncode, 0)
        rows = [l.split("\t")[:-1] for l in self.table.read_text().splitlines() if l and not l.startswith("#")]
        self.table.write_text("\n".join("\t".join(r) for r in rows) + "\n")
        self.assertEqual(self.e.run(str(self.repo), "--opencode", "none").returncode, 0)
        self.assertEqual(self.row(), "swift\tnone\t-\t-\tnone")

    def test_opencode_flag_takes_a_table_with_a_comment_an_older_template_shipped(self):
        self.assertEqual(self.e.run(str(self.repo), "--opencode", "hosted").returncode, 0)
        old = "# sandbox: required = never runs bare (a hosted OpenCode model; refused under Orca + Cotal, see docs/orca.md); none = may run without it.\n"
        self.table.write_text(old + self.table.read_text())
        self.assertEqual(self.e.run(str(self.repo), "--opencode", "none").returncode, 0)
        self.assertEqual(self.row(), "swift\tnone\t-\t-\tnone")

    def test_opencode_flag_keeps_a_table_with_the_owners_own_comment(self):
        for strip_column in (False, True):
            self.assertEqual(self.e.run(str(self.repo), "--opencode", "hosted").returncode, 0)
            lines = [l.split("\t")[:-1] if strip_column and not l.startswith("#") else l.split("\t") for l in self.table.read_text().splitlines() if l]
            edited = "# owner: keep swift on flash until March\n" + "\n".join("\t".join(l) for l in lines) + "\n"
            self.table.write_text(edited)
            r = self.e.run(str(self.repo), "--opencode", "none")
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("differs from the current defaults", r.stderr)
            self.assertEqual(self.table.read_text(), edited)
            self.table.unlink()

    def test_opencode_flag_still_refuses_an_edited_table_without_the_column(self):
        self.assertEqual(self.e.run(str(self.repo), "--opencode", "hosted").returncode, 0)
        rows = [l.split("\t")[:-1] for l in self.table.read_text().splitlines() if l and not l.startswith("#")]
        rows[1][2] = "opencode/another"
        edited = "\n".join("\t".join(r) for r in rows) + "\n"
        self.table.write_text(edited)
        r = self.e.run(str(self.repo), "--opencode", "none")
        self.assertEqual(r.returncode, 1)
        self.assertIn("differs from the current defaults", r.stderr)
        self.assertEqual(self.table.read_text(), edited)

    def test_opencode_flag_can_change_an_unedited_answer(self):
        self.assertEqual(self.e.run(str(self.repo), "--opencode", "hosted").returncode, 0)
        self.assertEqual(self.e.run(str(self.repo), "--opencode", "none").returncode, 0)
        self.assertEqual(self.row(), "swift\tnone\t-\t-\tnone")

    def test_models_are_listed_from_the_project(self):
        (self.repo / "marker").write_text("x")
        p = self.e.bin / "opencode"
        p.write_text("#!/bin/sh\n[ -f marker ] && echo project/only\nexit 0\n")
        _, out, _ = self.in_terminal(["2", "1"])
        self.assertIn("project/only", out)
        self.assertEqual(self.row(), "swift\topencode\tproject/only\t-\tnone")

    def test_a_recorded_answer_is_used_if_the_table_is_gone(self):
        self.assertEqual(self.e.run(str(self.repo), "--opencode", "none").returncode, 0)
        self.table.unlink()
        status, _, ask = self.in_terminal([])
        self.assertEqual(status, 0)
        ask.assert_not_called()
        self.assertEqual(self.row(), "swift\tnone\t-\t-\tnone")

    def test_an_earlier_project_with_a_table_is_not_asked(self):
        self.table.parent.mkdir(parents=True, exist_ok=True)
        own = "tier\tharness\tmodel\tpermission\nswift\topencode\tgw/m\t-\n"
        self.table.write_text(own)
        status, _, ask = self.in_terminal([])
        self.assertEqual(status, 0)
        ask.assert_not_called()
        self.assertEqual(self.table.read_text(), own)

    def test_an_own_model_gets_no_key_warning(self):
        r = self.e.run(str(self.repo), "--opencode", "own", "--opencode-model", "gateway/m1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("OPENCODE_API_KEY", r.stderr)


if __name__ == "__main__":
    unittest.main()
