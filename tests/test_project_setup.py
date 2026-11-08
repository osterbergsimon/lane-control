"""project-setup against stub orca/cotal executables (tests/stubs/fake.py) in a temporary HOME."""
import importlib.machinery
import importlib.util
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

    def run(self, *args, env_extra=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], env={**self.env, **(env_extra or {})}, capture_output=True, text=True)

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

    def test_paid_opencode_tier_without_key_warns(self):
        repo = self.e.repo()
        r = self.e.run(str(repo), "--name", "Shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stderr, r"swift.*opencode/deepseek-v4\.1-flash.*OPENCODE_API_KEY is not set")

    def test_free_opencode_tier_without_key_is_quiet(self):
        repo = self.e.repo()
        tiers = self.e.home / "free-lanes.tsv"
        tiers.write_text("tier\tharness\tmodel\tpermission\nswift\topencode\topencode/x-free\t-\n")
        r = self.e.run(str(repo), "--name", "Shop", "--tiers", str(tiers))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("OPENCODE_API_KEY is not set", r.stderr)

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


if __name__ == "__main__":
    unittest.main()
