"""EXT-1 (lc-7c6): extras, centralized. `lane-control project extras add|list|remove` and `lane-control machine extras
...` keep skills, MCP servers, personas, agent hooks, permissions, plugins and env vars in the project's settings store
and the machine's file (never a project repo, never a secret); `project refresh` and setup put the coordinator's in its
folder and render the personas, `lane-control lane prepare` puts a lane's or reviewer's in its worktree, for Claude and
OpenCode both; `project check` and `doctor` list every extra per role and flag missing, changed and failing ones. The
built-in design skills are the default machine extras. Everything runs in a scratch HOME."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
LC = ROOT / "core" / "scripts" / "lane-control"
NODE = shutil.which("node")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


X = load("lc_extras_t", ROOT / "core" / "extras.py")
F = load("lc_frontdoor_ext_t", ROOT / "core" / "frontdoor.py")


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd,
                          capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL).stdout.strip()


class Scratch(unittest.TestCase):
    """A scratch HOME with one project set up (its settings store), a lane worktree of it and an owner's folder."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.t = Path(tmp.name).resolve()
        self.home = self.t / "home"
        self.home.mkdir()
        none = self.t / "none.tsv"
        none.write_text("# no default design skills in these tests unless one asks\n")
        self.env = {**os.environ, **testhome.env_for(self.home), "LANE_DESIGN_SKILLS": str(none)}
        for k in ("LANE_ROLE", "LANE_PERSONA", "LANE_CONTROL_FRONT_DOOR", "GITHUB_TOKEN"):
            self.env.pop(k, None)
        patch = mock.patch.dict(os.environ, self.env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.repo = self.t / "app"
        self.repo.mkdir()
        git("init", "-q", "-b", "main", cwd=self.repo)
        (self.repo / "README.md").write_text("app\n")
        git("add", "README.md", cwd=self.repo)
        git("commit", "-q", "-m", "init", cwd=self.repo)
        self.wt = self.t / "workspaces" / "lane-1"
        git("worktree", "add", "-q", str(self.wt), "-b", "lane-1", cwd=self.repo)
        self.store = X.store_for(self.repo)
        self.store.mkdir(parents=True)
        (self.store / "settings.json").write_text(json.dumps({"root": str(self.repo), "name": "app",
                                                              "private": ["fixtures/customers/*"]}))
        self.owner = self.t / "owner"
        self.owner.mkdir()
        self.hook = self.script("guard.sh", "#!/bin/sh\nexit 0\n")

    def script(self, name, text, mode=0o755):
        f = self.owner / name
        f.write_text(text)
        f.chmod(mode)
        return f

    def lc(self, *args):
        return subprocess.run([sys.executable, str(LC), *map(str, args)], env=self.env, capture_output=True,
                              text=True, stdin=subprocess.DEVNULL)

    def ok(self, *args):
        r = self.lc(*args)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return r

    def refused(self, *args, says=""):
        r = self.lc(*args)
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn(says, r.stderr)
        return r

    def stored(self, machine=False):
        f = X.machine_file() if machine else X.project_file(self.store)
        return json.loads(f.read_text())["extras"] if f.exists() else []

    def upstream(self, name, path, files):
        d = self.t / "upstream" / name
        for rel, (text, mode) in files.items():
            f = d / path / rel if path else d / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
            f.chmod(mode)
        (d / "LICENSE").write_text("MIT\n")
        git("init", "-q", "-b", "main", cwd=d)
        git("add", "-A", cwd=d)
        git("commit", "-q", "-m", "v1", cwd=d)
        return d, git("rev-parse", "HEAD", cwd=d)


class FrontDoorTests(unittest.TestCase):
    def test_the_forms_run_lane_extras(self):
        self.assertEqual(F.route(["project", "extras", "add", "app", "--env", "A=b"]),
                         ("lane-extras", ["project", "add", "app", "--env", "A=b"]))
        self.assertEqual(F.route(["machine", "extras", "list"]), ("lane-extras", ["machine", "list"]))
        self.assertEqual(F.route(["lane", "prepare", "app", "/w", "--role", "lanes"]),
                         ("lane-extras", ["prepare", "app", "/w", "--role", "lanes"]))
        self.assertIn("lane-extras", F.NEW)

    def test_help_fits_and_names_them(self):
        r = subprocess.run([sys.executable, str(LC), "help"], capture_output=True, text=True)
        self.assertIn("lane-control project extras add|list|remove", r.stdout)
        self.assertIn("lane-control machine extras add|list|remove", r.stdout)
        self.assertIn("lane-control lane prepare", r.stdout)
        self.assertEqual([l for l in r.stdout.splitlines() if len(l) > 100], [])


class StoreTests(Scratch):
    def test_each_kind_is_stored_in_the_project_store_and_never_in_the_repo(self):
        prompt = self.owner / "migrations.md"
        prompt.write_text("---\ndescription: Writes database migrations\n---\nYou write migrations.\n")
        self.ok("project", "extras", "add", "app", "--mcp", "github", "--env", "GITHUB_TOKEN", "--",
                "npx", "-y", "@modelcontextprotocol/server-github")
        self.ok("project", "extras", "add", "app", "--agent-hook", "PreToolUse", "--matcher", "Bash", "--",
                self.hook, "--for", "lanes")
        self.ok("project", "extras", "add", "app", "--permission", "allow", "Bash(npm test:*)")
        self.ok("project", "extras", "add", "app", "--permission", "deny", "Bash(git push --force:*)",
                "--for", "lanes,reviewer")
        self.ok("project", "extras", "add", "app", "--env", "APP_MODE=fast", "--for", "coordinator")
        self.ok("project", "extras", "add", "app", "--plugin", "opencode:opencode-foo@1.2.3")
        self.ok("project", "extras", "add", "app", "--plugin", "claude:helper@market@2.0.1")
        self.ok("project", "extras", "add", "app", "--persona", "migrations", "--prompt-file", prompt,
                "--tier", "deep", "--harness", "opencode")
        ids = {X.ident(e): e for e in self.stored()}
        self.assertEqual(sorted(ids), sorted([
            "mcp:github", "agent-hook:pretooluse-guard", "permission:allow Bash(npm test:*)",
            "permission:deny Bash(git push --force:*)", "env:APP_MODE", "plugin:opencode:opencode-foo",
            "plugin:claude:helper@market", "persona:migrations"]))
        self.assertEqual(ids["mcp:github"]["env"], ["GITHUB_TOKEN"])
        self.assertEqual(ids["agent-hook:pretooluse-guard"]["for"], ["lanes"])
        self.assertEqual(ids["permission:deny Bash(git push --force:*)"]["for"], ["lanes", "reviewer"])
        self.assertEqual(ids["persona:migrations"]["tier"], "deep")
        self.assertEqual(oct(X.project_file(self.store).stat().st_mode & 0o777), "0o600")
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=self.repo), "")
        r = self.ok("project", "extras", "list", "app")
        self.assertIn("by role:", r.stdout)
        self.assertIn("lanes: mcp:github, agent-hook:pretooluse-guard", r.stdout)
        self.assertRegex(r.stdout, r"coordinator: .*env:APP_MODE")
        listed = json.loads(self.ok("project", "extras", "list", "app", "--json").stdout)
        self.assertEqual(len(listed["extras"]), 8)

    def test_the_same_id_replaces_and_remove_takes_it_out(self):
        self.ok("project", "extras", "add", "app", "--env", "APP_MODE=fast")
        r = self.ok("project", "extras", "add", "app", "--env", "APP_MODE=slow", "--for", "lanes")
        self.assertIn("replaced env:APP_MODE", r.stdout)
        self.assertEqual([(e["value"], e["for"]) for e in self.stored()], [("slow", ["lanes"])])
        self.ok("project", "extras", "remove", "app", "env:APP_MODE")
        self.assertEqual(self.stored(), [])
        self.refused("project", "extras", "remove", "app", "env:APP_MODE", says="no extra")

    def test_a_project_needs_to_be_set_up(self):
        other = self.t / "other"
        other.mkdir()
        git("init", "-q", cwd=other)
        self.refused("project", "extras", "add", other, "--env", "A=b", says="no project set up")
        self.refused("project", "extras", "add", "nope", "--env", "A=b", says="no project set up")

    def test_machine_extras_go_to_the_machine_file_and_a_project_adds_to_them(self):
        self.ok("machine", "extras", "add", "--env", "APP_MODE=fast")
        self.ok("machine", "extras", "add", "--permission", "allow", "Bash(make:*)")
        self.ok("project", "extras", "add", "app", "--env", "APP_MODE=slow")
        self.assertEqual(len(self.stored(machine=True)), 2)
        eff = {X.ident(e): e for e in X.effective(self.store)}
        self.assertEqual((eff["env:APP_MODE"]["value"], eff["env:APP_MODE"]["origin"]), ("slow", "project"))
        self.assertEqual(eff["permission:allow Bash(make:*)"]["origin"], "machine")
        self.assertEqual({X.ident(e) for e in X.effective(None)}, {"env:APP_MODE", "permission:allow Bash(make:*)"})

    def test_the_design_skills_are_default_machine_extras_and_can_be_turned_off(self):
        self.env["LANE_DESIGN_SKILLS"] = str(ROOT / "core" / "design-skills.tsv")
        os.environ["LANE_DESIGN_SKILLS"] = self.env["LANE_DESIGN_SKILLS"]
        r = self.ok("machine", "extras", "list")
        for name in ("frontend-design", "ui-ux-pro-max", "modern-web-guidance"):
            self.assertRegex(r.stdout, rf"skill:{name}\s+default\s+lanes,coordinator,reviewer")
        r = self.ok("machine", "extras", "remove", "modern-web-guidance")
        self.assertIn("turned off the default skill:modern-web-guidance", r.stdout)
        self.assertNotIn("skill:modern-web-guidance", {X.ident(e) for e in X.effective(self.store)})
        self.assertIn("off", self.ok("machine", "extras", "list").stdout)
        # adding it back (any pin of the owner's) puts the owner's in the default's place
        self.ok("machine", "extras", "add", "--skill",
                f"https://github.com/GoogleChrome/modern-web-guidance.git@{'a' * 40}:skills/modern-web-guidance")
        eff = {X.ident(e): e for e in X.effective(self.store)}
        self.assertEqual(eff["skill:modern-web-guidance"]["origin"], "machine")


class RefusalTests(Scratch):
    def test_no_secret_is_ever_stored(self):
        for args, says in (
                (("--env", "API_TOKEN=abc"), "looks like a key"),
                (("--env", "GH_PAT=abc"), "looks like a key"),
                (("--env", "DB_PASSWORD=abc"), "looks like a key"),
                (("--env", "MODE=ghp_0123456789abcdefghij0123456789abcdef"), "looks like a key"),
                (("--env", "MODE=sk-ant-0123456789"), "looks like a key"),
                (("--env", "HOME=/x"), "sets it"),
                (("--env", "LANE_ROLE=lane"), "sets it"),
                (("--mcp", "gh", "--env", "GITHUB_TOKEN=ghp_x", "--", "npx", "server"), "takes a name only"),
                (("--mcp", "gh", "--", "server", "--token", "ghp_0123456789abcdefghij0123456789abcdef"),
                 "looks like a key or token"),
                (("--permission", "allow", "Read(**/.env)"), "reaches secrets"),
                (("--permission", "allow", "Read(~/.ssh/**)"), "reaches secrets"),
                (("--permission", "allow", "Bash(cat ~/.aws/credentials)"), "reaches secrets"),
                (("--permission", "allow", "Read"), "every file"),
                (("--permission", "allow", "Read(**)"), "every file"),
                (("--permission", "allow", "Edit(fixtures/customers/a.csv)"), "--private"),
                (("--env", "LANE_ROLE=x"), "sets it")):
            with self.subTest(args=args):
                r = self.refused("project", "extras", "add", "app", *args, says=says)
                self.assertNotIn("ghp_0123", r.stderr + r.stdout)
        self.assertEqual(self.stored(), [])
        self.ok("project", "extras", "add", "app", "--permission", "deny", "Read(**/.env)")  # a deny only narrows

    def test_pins_and_shapes(self):
        for args, says in (
                (("--skill", "https://github.com/a/b.git@main"), "full 40-hex commit"),
                (("--skill", f"https://github.com/a/b.git@{'a' * 12}"), "full 40-hex commit"),
                (("--skill", f"http://example.com/b.git@{'a' * 40}"), "http and other"),
                (("--skill", f"ext::sh -c x@{'a' * 40}"), "plain git URL"),
                ((f"--skill=-uhttps://x@{'a' * 40}",), "plain git URL"),
                (("--skill", f"https://github.com/a/b.git@{'a' * 40}:../x"), "plain relative path"),
                (("--plugin", "opencode:foo@latest"), "exact version"),
                (("--plugin", "opencode:foo@^1.2.0"), "exact version"),
                (("--plugin", "claude:helper@1.0"), "a Claude plugin is <plugin>@<marketplace>"),
                (("--plugin", "vim:x@1.0.0"), "claude:<plugin>"),
                (("--persona", "reviewer", "--prompt-file", "/etc/hostname"), "built-in role"),
                (("--persona", "x"), "--prompt-file"),
                (("--agent-hook", "PreCompact", "--", "/bin/true"), "PreToolUse"),
                (("--agent-hook", "Stop", "--matcher", "Bash", "--", "/bin/true"), "--matcher is for"),
                (("--env", "A=b", "--skill", f"https://x/y.git@{'a' * 40}"), "exactly one kind"),
                (("--env", "A=b", "--for", "everyone"), "--for 'everyone'"),
                (("--permission", "allow", "rm -rf"), "not a permission rule")):
            with self.subTest(args=args):
                self.refused("project", "extras", "add", "app", *args, says=says)

    def test_a_script_in_a_project_checkout_needs_allow_repo_path(self):
        inside = self.wt / "tools" / "hook.sh"
        inside.parent.mkdir()
        inside.write_text("#!/bin/sh\nexit 0\n")
        inside.chmod(0o755)
        self.refused("project", "extras", "add", "app", "--agent-hook", "Stop", "--", inside,
                     says="inside the project checkout")
        self.refused("project", "extras", "add", "app", "--mcp", "local", "--", inside,
                     says="inside the project checkout")
        r = self.ok("project", "extras", "add", "app", "--agent-hook", "Stop", "--allow-repo-path", "--", inside)
        self.assertEqual(len([l for l in r.stderr.splitlines() if l.startswith("warning:")]), 1, r.stderr)
        self.assertIn("Claude only", r.stderr)  # Stop has no OpenCode plugin hook
        plain = self.script("plain.sh", "#!/bin/sh\n", 0o644)
        self.refused("project", "extras", "add", "app", "--agent-hook", "Stop", "--", plain, says="not executable")

    def test_a_secret_never_reaches_a_file_or_the_output(self):
        self.ok("project", "extras", "add", "app", "--mcp", "github", "--env", "GITHUB_TOKEN", "--", "npx", "srv")
        self.env["GITHUB_TOKEN"] = os.environ["GITHUB_TOKEN"] = "ghp_supersecretvalue0123456789abcdef"
        r = self.ok("lane", "prepare", "app", self.wt, "--role", "lanes")
        found = [p for p in (*self.t.rglob("*"), *self.home.rglob("*"))
                 if p.is_file() and not p.is_symlink() and b"supersecret" in p.read_bytes()]
        self.assertEqual(found, [])
        self.assertNotIn("supersecret", r.stdout + r.stderr)
        self.assertEqual(json.loads((self.wt / ".mcp.json").read_text())["mcpServers"]["github"]["env"],
                         {"GITHUB_TOKEN": "${GITHUB_TOKEN}"})


class ApplyTests(Scratch):
    def all_kinds(self):
        self.ok("project", "extras", "add", "app", "--mcp", "github", "--env", "GITHUB_TOKEN", "--", "npx", "-y",
                "@modelcontextprotocol/server-github")
        self.ok("project", "extras", "add", "app", "--agent-hook", "PreToolUse", "--matcher", "Bash", "--", self.hook)
        self.ok("project", "extras", "add", "app", "--agent-hook", "SessionStart", "--", self.hook,
                "--name", "start-guard")
        self.ok("project", "extras", "add", "app", "--permission", "allow", "Bash(npm test:*)")
        self.ok("project", "extras", "add", "app", "--permission", "deny", "WebFetch")
        self.ok("project", "extras", "add", "app", "--env", "APP_MODE=fast")
        self.ok("project", "extras", "add", "app", "--plugin", "opencode:opencode-foo@1.2.3")
        self.ok("project", "extras", "add", "app", "--plugin", "claude:helper@market@2.0.1")

    def test_prepare_puts_every_kind_where_each_harness_reads_it(self):
        self.all_kinds()
        r = self.ok("lane", "prepare", "app", self.wt, "--role", "lanes")
        self.assertIn("prepare: done", r.stdout)
        s = json.loads((self.wt / ".claude" / "settings.local.json").read_text())
        self.assertEqual(s["hooks"]["PreToolUse"], [{"matcher": "Bash", "hooks": [{"type": "command",
                                                                                   "command": str(self.hook)}]}])
        self.assertEqual(s["hooks"]["SessionStart"], [{"hooks": [{"type": "command", "command": str(self.hook)}]}])
        self.assertEqual(s["permissions"], {"allow": ["Bash(npm test:*)"], "deny": ["WebFetch"]})
        self.assertEqual(s["env"], {"APP_MODE": "fast"})
        self.assertEqual(s["enabledPlugins"], {"helper@market": True})
        self.assertEqual(s["enabledMcpjsonServers"], ["github"])
        m = json.loads((self.wt / ".mcp.json").read_text())
        self.assertEqual(m["mcpServers"]["github"], {"type": "stdio", "command": "npx", "args": [
            "-y", "@modelcontextprotocol/server-github"], "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}})
        oc = json.loads((self.wt / ".opencode" / "opencode.json").read_text())
        self.assertEqual(oc["mcp"]["github"]["command"], ["npx", "-y", "@modelcontextprotocol/server-github"])
        self.assertEqual(oc["mcp"]["github"]["environment"], {"GITHUB_TOKEN": "{env:GITHUB_TOKEN}"})
        self.assertEqual(oc["permission"], {"bash": {"npm test*": "allow"}, "webfetch": "deny"})
        self.assertEqual(oc["plugin"], ["opencode-foo@1.2.3"])
        side = json.loads((self.wt / ".opencode" / "lane-extras.json").read_text())
        self.assertEqual(side, {"env": {"APP_MODE": "fast"}, "hooks": [
            {"event": "PreToolUse", "matcher": "Bash", "command": str(self.hook)}]})  # SessionStart: Claude only
        self.assertEqual((self.wt / ".opencode" / "plugins" / "lane-extras.js").read_bytes(), X.OC_PLUGIN.read_bytes())
        # nothing shows in git: the repo's own exclude covers it, in every worktree
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=self.wt), "")
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=self.repo), "")
        # a second run changes nothing
        again = self.ok("lane", "prepare", "app", self.wt, "--role", "lanes")
        self.assertEqual([l for l in again.stdout.splitlines() if "done" not in l], [])

    def test_the_owners_entries_are_kept_and_only_ours_come_out(self):
        own = {"permissions": {"allow": ["Bash(ls:*)"], "deny": ["Bash(rm:*)"]}, "env": {"MINE": "1"},
               "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/usr/bin/true"}]}]},
               "model": "opus"}
        (self.wt / ".claude").mkdir()
        (self.wt / ".claude" / "settings.local.json").write_text(json.dumps(own))
        (self.wt / ".mcp.json").write_text(json.dumps({"mcpServers": {"mine": {"command": "my-server"}}}))
        self.all_kinds()
        self.ok("lane", "prepare", "app", self.wt, "--role", "lanes")
        for word in ("mcp:github", "agent-hook:pretooluse-guard", "agent-hook:start-guard", "permission:allow "
                     "Bash(npm test:*)", "permission:deny WebFetch", "env:APP_MODE", "plugin:opencode:opencode-foo",
                     "plugin:claude:helper@market"):
            self.ok("project", "extras", "remove", "app", word)
        r = self.ok("lane", "prepare", "app", self.wt, "--role", "lanes")
        self.assertIn("removed", r.stdout)
        self.assertEqual(json.loads((self.wt / ".claude" / "settings.local.json").read_text()), own)
        self.assertEqual(json.loads((self.wt / ".mcp.json").read_text()),
                         {"mcpServers": {"mine": {"command": "my-server"}}})
        self.assertFalse((self.wt / ".opencode" / "opencode.json").exists())
        self.assertFalse((self.wt / ".opencode" / "plugins" / "lane-extras.js").exists())

    def test_a_folder_we_created_files_in_is_left_clean(self):
        self.ok("project", "extras", "add", "app", "--mcp", "github", "--", "npx", "srv")
        self.ok("lane", "prepare", "app", self.wt, "--role", "lanes")
        self.ok("project", "extras", "remove", "app", "github")
        self.ok("lane", "prepare", "app", self.wt, "--role", "lanes")
        self.assertFalse((self.wt / ".mcp.json").exists())
        self.assertFalse((self.wt / ".claude" / "settings.local.json").exists())

    def test_a_name_the_owner_already_uses_is_left_alone_and_named(self):
        (self.wt / ".mcp.json").write_text(json.dumps({"mcpServers": {"github": {"command": "theirs"}}}))
        self.ok("project", "extras", "add", "app", "--mcp", "github", "--", "npx", "srv")
        r = self.lc("lane", "prepare", "app", self.wt, "--role", "lanes")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("not lane-control's", r.stdout)
        self.assertEqual(json.loads((self.wt / ".mcp.json").read_text())["mcpServers"]["github"],
                         {"command": "theirs"})

    def test_a_committed_mcp_json_is_the_projects(self):
        (self.repo / ".mcp.json").write_text(json.dumps({"mcpServers": {}}))
        git("add", ".mcp.json", cwd=self.repo)
        git("commit", "-q", "-m", "mcp", cwd=self.repo)
        git("merge", "-q", "main", cwd=self.wt)
        self.ok("project", "extras", "add", "app", "--mcp", "github", "--", "npx", "srv")
        r = self.lc("lane", "prepare", "app", self.wt, "--role", "lanes")
        self.assertEqual(r.returncode, 1)
        self.assertIn("is committed in the project", r.stdout)
        self.assertEqual(git("status", "--porcelain", cwd=self.wt), "")

    def test_scope_decides_who_gets_what_and_a_reviewer_adds_its_own(self):
        self.ok("project", "extras", "add", "app", "--env", "APP_LANE=1", "--for", "lanes")
        self.ok("project", "extras", "add", "app", "--env", "REVIEW_ONLY=1", "--for", "reviewer")
        self.ok("project", "extras", "add", "app", "--env", "COORD_ONLY=1", "--for", "coordinator")
        self.ok("lane", "prepare", "app", self.wt, "--role", "lanes")
        env = lambda: json.loads((self.wt / ".claude" / "settings.local.json").read_text())["env"]  # noqa: E731
        self.assertEqual(env(), {"APP_LANE": "1"})
        self.ok("lane", "prepare", "app", self.wt, "--role", "reviewer")
        self.assertEqual(env(), {"APP_LANE": "1", "REVIEW_ONLY": "1"})
        self.ok("lane", "prepare", "app", self.wt, "--role", "lanes")  # the reviewer's stay while it may run
        self.assertEqual(env(), {"APP_LANE": "1", "REVIEW_ONLY": "1"})

    def test_prepare_refuses_other_folders(self):
        r = self.lc("lane", "prepare", "app", self.repo, "--role", "lanes")
        self.assertIn("coordinator's folder", r.stderr)
        other = self.t / "other"
        other.mkdir()
        git("init", "-q", cwd=other)
        r = self.lc("lane", "prepare", "app", other, "--role", "lanes")
        self.assertIn("not a worktree of", r.stderr)
        r = self.lc("lane", "prepare", "app", self.wt, "--role", "coordinator")
        self.assertEqual(r.returncode, 2)

    def test_no_extras_leaves_the_folder_exactly_as_it_was(self):
        exclude = self.repo / ".git" / "info" / "exclude"
        before = exclude.read_text()
        self.ok("lane", "prepare", "app", self.wt, "--role", "lanes")
        self.assertEqual(exclude.read_text(), before)
        self.assertEqual(list(self.wt.glob(".claude")) + list(self.wt.glob(".opencode")), [])
        self.assertFalse(X.record_file(self.wt).exists())

    def test_a_pinned_skill_and_a_pinned_hook_from_git(self):
        d, sha = self.upstream("tools", "", {"skills/db/SKILL.md": ("---\nname: db\ndescription: d\n---\nv1\n", 0o644),
                                             "hooks/check.sh": ("#!/bin/sh\nexit 0\n", 0o755),
                                             "big/blob.bin": ("x" * 100, 0o644)})
        self.ok("project", "extras", "add", "app", "--skill", f"file://{d}@{sha}:skills/db", "--for", "lanes")
        self.ok("project", "extras", "add", "app", "--agent-hook", "PostToolUse", "--",
                f"file://{d}@{sha}:hooks/check.sh", "--name", "check")
        self.ok("lane", "prepare", "app", self.wt, "--role", "lanes")
        for harness in (".claude", ".opencode"):
            link = self.wt / harness / "skills" / "db"
            self.assertTrue(link.is_symlink(), link)
            self.assertEqual((link / "SKILL.md").read_text().splitlines()[-1], "v1")
        src = self.home / ".agents" / "skill-sources" / f"db@{sha[:12]}"
        self.assertFalse((src / "big").exists())  # only the skill's folder is checked out
        script = self.home / ".agents" / "extras-sources" / f"check@{sha[:12]}" / "hooks" / "check.sh"
        self.assertTrue(os.access(script, os.X_OK))
        s = json.loads((self.wt / ".claude" / "settings.local.json").read_text())
        self.assertEqual(s["hooks"]["PostToolUse"][0]["hooks"][0]["command"], str(script))
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=self.wt), "")
        # off the list again: our links go, the owner's file of the same name would stay
        self.ok("project", "extras", "remove", "app", "skill:db")
        self.ok("lane", "prepare", "app", self.wt, "--role", "lanes")
        self.assertFalse((self.wt / ".claude" / "skills" / "db").is_symlink())

    def test_a_skill_that_cannot_be_fetched_fails_prepare_and_the_rest_goes_in(self):
        self.ok("project", "extras", "add", "app", "--skill", f"file://{self.t}/nowhere@{'0' * 40}:s/x")
        self.ok("project", "extras", "add", "app", "--env", "APP_MODE=fast")
        r = self.lc("lane", "prepare", "app", self.wt, "--role", "lanes")
        self.assertEqual(r.returncode, 1)
        self.assertIn("problem: skill x", r.stdout)
        self.assertEqual(json.loads((self.wt / ".claude" / "settings.local.json").read_text())["env"],
                         {"APP_MODE": "fast"})


class CheckTests(Scratch):
    def rows(self):
        return {r["id"]: r for r in X.check(self.store, self.repo)}

    def test_missing_changed_and_failing_are_flagged(self):
        prompt = self.owner / "p.md"
        prompt.write_text("You are a lane.\n")
        self.ok("project", "extras", "add", "app", "--mcp", "gone", "--", "no-such-command-anywhere")
        self.ok("project", "extras", "add", "app", "--agent-hook", "Stop", "--", self.hook)
        self.ok("project", "extras", "add", "app", "--persona", "p", "--prompt-file", prompt)
        self.ok("project", "extras", "add", "app", "--skill", f"file://{self.t}/x@{'1' * 40}:s/k")
        self.ok("project", "extras", "add", "app", "--plugin", "claude:helper@market@2.0.1")
        rows = self.rows()
        self.assertEqual(rows["mcp:gone"]["state"], "failing")
        self.assertEqual(rows["agent-hook:stop-guard"]["state"], "ok")
        self.assertIn("Claude only", rows["agent-hook:stop-guard"]["detail"])
        self.assertEqual(rows["persona:p"]["state"], "ok")
        self.assertEqual(rows["skill:k"]["state"], "missing")
        self.assertEqual(rows["plugin:claude:helper@market"]["state"], "missing")
        self.assertIn("claude plugin install helper@market", rows["plugin:claude:helper@market"]["detail"])
        self.assertEqual(rows["coordinator folder"]["state"], "not applied")
        self.hook.write_text("#!/bin/sh\necho changed\n")
        prompt.write_text("Another text.\n")
        rows = self.rows()
        self.assertEqual(rows["agent-hook:stop-guard"]["state"], "changed")
        self.assertEqual(rows["persona:p"]["state"], "changed")
        self.hook.chmod(0o644)
        self.assertEqual(self.rows()["agent-hook:stop-guard"]["state"], "failing")
        prompt.unlink()
        self.assertEqual(self.rows()["persona:p"]["state"], "missing")

    def test_a_claude_plugin_at_another_version_is_changed(self):
        self.ok("project", "extras", "add", "app", "--plugin", "claude:helper@market@2.0.1")
        p = self.home / ".claude" / "plugins"
        p.mkdir(parents=True)
        (p / "installed_plugins.json").write_text(json.dumps({"plugins": {"helper@market": [{"version": "1.9.0"}]}}))
        self.assertEqual(self.rows()["plugin:claude:helper@market"]["state"], "changed")
        (p / "installed_plugins.json").write_text(json.dumps({"plugins": {"helper@market": [{"version": "2.0.1"}]}}))
        self.assertEqual(self.rows()["plugin:claude:helper@market"]["state"], "ok")

    def test_an_applied_coordinator_folder_is_ok(self):
        self.ok("project", "extras", "add", "app", "--env", "APP_MODE=fast", "--for", "coordinator")
        self.assertEqual(self.rows()["coordinator folder"]["state"], "not applied")
        X.apply_folder(self.repo, X.for_scope(X.effective(self.store), "coordinator"), ["coordinator"])
        self.assertNotIn("coordinator folder", self.rows())

    def test_doctor_lists_them_and_says_what_to_do(self):
        self.ok("project", "extras", "add", "app", "--mcp", "gone", "--", "no-such-command-anywhere")
        r = self.lc("doctor", "--no-ssh", "--json")
        d = json.loads(r.stdout)
        rows = {x["id"]: x for x in d["extras"][str(self.repo)]}
        self.assertEqual(rows["mcp:gone"]["state"], "failing")
        self.assertTrue(any("extra mcp:gone failing" in t for t in d["todo"]), d["todo"])
        text = self.lc("doctor", "--no-ssh").stdout
        self.assertIn("extras", text)


class PersonaTests(Scratch):
    def test_custom_personas_render_with_additions_and_the_coordinator_is_told(self):
        prompt = self.owner / "migrations.md"
        prompt.write_text("---\ndescription: Writes database migrations\n---\nYou write migrations.\n")
        self.ok("project", "extras", "add", "app", "--persona", "migrations", "--prompt-file", prompt, "--tier", "deep")
        self.ok("project", "extras", "add", "app", "--env", "APP_THING=1", "--for", "lanes")
        entries = X.effective(self.store)
        lines, problems = X.render_personas(self.repo, entries, lambda n: "\n\n# Additions\nproject words\n")
        self.assertEqual(problems, [])
        text = (self.repo / ".lane-control" / "personas" / "migrations.md").read_text()
        self.assertIn("You write migrations.", text)
        self.assertIn("project words", text)
        section = X.coordinator_section(self.repo, entries, "/lc")
        self.assertIn("`migrations`", section)
        self.assertIn("tier deep, claude. Writes database migrations", section)
        self.assertIn(f"/lc/scripts/lane-control lane prepare {self.repo} <worktree> --role lanes", section)
        self.assertNotIn("--role reviewer", section)
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=self.repo), "")
        self.ok("project", "extras", "remove", "app", "migrations")
        X.render_personas(self.repo, X.effective(self.store), lambda n: "")
        self.assertFalse((self.repo / ".lane-control" / "personas" / "migrations.md").exists())

    def test_nothing_to_tell_with_the_defaults_alone(self):
        self.env["LANE_DESIGN_SKILLS"] = os.environ["LANE_DESIGN_SKILLS"] = str(ROOT / "core" / "design-skills.tsv")
        self.assertEqual(X.coordinator_section(self.repo, X.effective(self.store), "/lc"), "")


@unittest.skipUnless(NODE, "node is not installed")
class OpenCodePluginTests(Scratch):
    DRIVER = """
import { pathToFileURL } from "node:url";
const [plugin, events] = process.argv.slice(2);
const mod = await import(pathToFileURL(plugin).href);
const hooks = {};
const ctx = { tool: { hook: async (name, fn) => { hooks[name] = fn; return { dispose: async () => {} }; } } };
await mod.default.setup(ctx);
const out = { id: mod.default.id, registered: Object.keys(hooks), env: process.env.APP_MODE ?? null, results: [] };
for (const ev of JSON.parse(events)) {
  try { await hooks[ev.hook]?.({ tool: ev.tool, sessionID: "ses_1", input: ev.input }); out.results.push("ran"); }
  catch (err) { out.results.push("blocked: " + err.message); }
}
console.log(JSON.stringify(out));
"""

    def drive(self, sidecar, events):
        d = self.t / "oc" / ".opencode"
        (d / "plugins").mkdir(parents=True)
        shutil.copy(X.OC_PLUGIN, d / "plugins" / "lane-extras.mjs")  # .mjs: node loads it as a module
        (d / "lane-extras.json").write_text(json.dumps(sidecar))
        driver = self.t / "driver.mjs"
        driver.write_text(self.DRIVER)
        r = subprocess.run([NODE, str(driver), str(d / "plugins" / "lane-extras.mjs"), json.dumps(events)],
                           capture_output=True, text=True, env=self.env, cwd=self.t)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_env_and_a_pre_tool_hook_that_blocks_with_exit_2(self):
        log = self.t / "hook.log"
        block = self.script("block.sh", f"#!/bin/sh\ncat >> {log}\necho >> {log}\necho 'no npm here' >&2\nexit 2\n")
        out = self.drive({"env": {"APP_MODE": "fast"}, "hooks": [
            {"event": "PreToolUse", "matcher": "Bash", "command": str(block)},
            {"event": "PostToolUse", "matcher": "", "command": str(self.hook)}]},
            [{"hook": "execute.before", "tool": "shell", "input": {"command": "npm i"}},
             {"hook": "execute.before", "tool": "read", "input": {"filePath": "x"}},
             {"hook": "execute.after", "tool": "shell", "input": {"command": "ls"}}])
        self.assertEqual(out["id"], "lane-extras")
        self.assertEqual(sorted(out["registered"]), ["execute.after", "execute.before"])
        self.assertEqual(out["env"], "fast")
        self.assertEqual(out["results"], ["blocked: no npm here", "ran", "ran"])
        seen = json.loads(log.read_text().splitlines()[0])
        self.assertEqual((seen["hook_event_name"], seen["tool_name"], seen["tool_input"]),
                         ("PreToolUse", "Bash", {"command": "npm i"}))

    def test_no_sidecar_registers_nothing(self):
        d = self.t / "oc2" / ".opencode" / "plugins"
        d.mkdir(parents=True)
        shutil.copy(X.OC_PLUGIN, d / "lane-extras.mjs")
        driver = self.t / "driver.mjs"
        driver.write_text(self.DRIVER)
        r = subprocess.run([NODE, str(driver), str(d / "lane-extras.mjs"), "[]"], capture_output=True, text=True,
                           env=self.env)
        self.assertEqual(json.loads(r.stdout)["registered"], [])


if __name__ == "__main__":
    unittest.main()
