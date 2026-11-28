"""EXT-1 (lc-7c6): extras, centralized. `lane-control project extras add|list|remove` and `lane-control machine extras
...` keep skills, MCP servers (secrets as env var NAMES only), personas and plugins in the project's settings store and
the machine's file (never a project repo, never a secret). Each role gets its extras only where that role's sessions
read them: the coordinator's Claude extras at its launch (files in the store), a lane's or reviewer's in its own
worktree (`lane-control lane prepare`). A folder gets only files lane-control creates and owns whole, all or nothing,
never through a symlink; refresh applies every recorded worktree again; `project check` and `doctor` flag missing,
changed and failing extras. The built-in design skills are the default machine extras. Everything runs in a scratch
HOME. Agent hooks, permission rules and env values are lc-yw1g (EXT-1b)."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
import contextlib
import importlib.machinery
import importlib.util
import io
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
LE_PATH = ROOT / "core" / "scripts" / "lane-extras"
SECRET = "ghp_" + "Z9" * 18  # a fake token: it must never reach a file or the output


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


X = load("lc_extras_t", ROOT / "core" / "extras.py")
F = load("lc_frontdoor_ext_t", ROOT / "core" / "frontdoor.py")
LE = load("lc_lane_extras_t", LE_PATH)


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *map(str, args)], cwd=cwd,
                          capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL).stdout.strip()


def claude_reads(session: Path, main: Path, launch: list = ()) -> dict:
    """What a Claude Code session started in `session` (a checkout of the repo whose main checkout is `main`) loads
    from the project, by Claude's documented rules (settings.md, worktrees.md; 2.1.211+ and 2.1.277+): the main
    checkout's .claude/settings.local.json also in a linked worktree (and the worktree's own as a second file), the
    session folder's .claude/settings.json and .mcp.json, its .claude/skills or, when it has none, the main
    checkout's; and the files its launch flags name. {"text": every file's text, "skills": skill names}."""
    texts, skills = [], []
    for f in {main / ".claude" / "settings.local.json", session / ".claude" / "settings.local.json",
              session / ".claude" / "settings.json", session / ".mcp.json"}:
        if f.is_file():
            texts.append(f.read_text())
    sk = session / ".claude" / "skills"
    sk = sk if sk.is_dir() else main / ".claude" / "skills"
    skills += sorted(p.name for p in sk.glob("*")) if sk.is_dir() else []
    for flag, value in zip(launch[::2], launch[1::2]):
        if flag in ("--mcp-config", "--settings"):
            texts.append(Path(value).read_text())
        elif flag == "--plugin-dir":
            skills += sorted(p.name for p in (Path(value) / "skills").glob("*"))
    return {"text": "\n".join(texts), "skills": skills}


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
        for k in ("LANE_ROLE", "LANE_PERSONA", "LANE_CONTROL_FRONT_DOOR", "GITHUB_TOKEN", "ORCA_TERMINAL_HANDLE"):
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
        git("worktree", "add", "-q", self.wt, "-b", "lane-1", cwd=self.repo)
        self.store = X.store_for(self.repo)
        self.store.mkdir(parents=True)
        (self.store / "settings.json").write_text(json.dumps({"root": str(self.repo), "name": "app",
                                                              "orchestrator": "native-orca"}))
        self.owner = self.t / "owner"
        self.owner.mkdir()
        self.server = self.script("server.sh", "#!/bin/sh\nexit 0\n")

    def script(self, name, text, mode=0o755):
        f = self.owner / name
        f.write_text(text)
        f.chmod(mode)
        return f

    def run_le(self, *args):
        """lane-extras in-process, the person check passed (a person at a terminal): (code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(LE, "person", lambda what: None), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            try:
                code = LE.main([str(a) for a in args])
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def ok(self, *args):
        code, out, err = self.run_le(*args)
        self.assertEqual(code, 0, err + out)
        return out + err

    def refused(self, *args, says=""):
        code, out, err = self.run_le(*args)
        self.assertNotEqual(code, 0, out)
        self.assertIn(says, err)
        self.assertNotIn(SECRET, out + err)
        self.assertNotIn("hunter2", out + err)
        return err

    def add(self, *args):
        return self.ok("project", "add", self.repo, *args)

    def lc(self, *args):
        return subprocess.run([sys.executable, str(LC), *map(str, args)], env=self.env, capture_output=True,
                              text=True, stdin=subprocess.DEVNULL)

    def prepare(self, role="lanes", folder=None):
        return self.run_le("prepare", self.repo, folder or self.wt, "--role", role)

    def coordinator(self):
        """What refresh does for the coordinator (project-setup apply_extras' calls)."""
        coord = X.for_scope(X.effective(self.store), "coordinator")
        a = X.apply_launch(self.store, coord)
        b = X.apply_folder(self.repo, coord, ["coordinator"], claude=False, project=self.repo)
        return a[0] + b[0], a[1] + b[1]

    def stored(self, machine=False):
        f = X.machine_file() if machine else X.project_file(self.store)
        return json.loads(f.read_text())["extras"] if f.exists() else []

    def upstream(self, name, path, body="---\nname: x\ndescription: y\n---\nv1\n"):
        d = self.t / "upstream" / name
        (d / path).mkdir(parents=True)
        (d / path / "SKILL.md").write_text(body)
        (d / "big").mkdir()
        (d / "big" / "blob.bin").write_text("x" * 100)
        git("init", "-q", "-b", "main", cwd=d)
        git("add", "-A", cwd=d)
        git("commit", "-q", "-m", "v1", cwd=d)
        return d, git("rev-parse", "HEAD", cwd=d)

    def clean(self, folder):
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=folder), "")


class FrontDoorTests(unittest.TestCase):
    def test_the_forms_run_lane_extras(self):
        self.assertEqual(F.route(["project", "extras", "add", "app", "--skill", "x"]),
                         ("lane-extras", ["project", "add", "app", "--skill", "x"]))
        self.assertEqual(F.route(["machine", "extras", "list"]), ("lane-extras", ["machine", "list"]))
        self.assertEqual(F.route(["lane", "prepare", "app", "/w", "--role", "lanes"]),
                         ("lane-extras", ["prepare", "app", "/w", "--role", "lanes"]))
        self.assertIn("lane-extras", F.NEW)

    def test_help_fits_and_names_them(self):
        r = subprocess.run([sys.executable, str(LC), "help"], capture_output=True, text=True)
        for needle in ("lane-control project extras add|list|remove", "lane-control machine extras add|list|remove",
                       "lane-control lane prepare"):
            self.assertIn(needle, r.stdout)
        self.assertEqual([l for l in r.stdout.splitlines() if len(l) > 100], [])


class StoreTests(Scratch):
    def test_each_kind_is_stored_in_the_project_store_and_never_in_the_repo(self):
        prompt = self.owner / "migrations.md"
        prompt.write_text("---\ndescription: Writes database migrations\n---\nYou write migrations.\n")
        self.add("--mcp", "github", "--env", "GITHUB_TOKEN", "--", "npx", "-y", "@modelcontextprotocol/server-github")
        self.add("--mcp", "local", "--for", "lanes", "--", self.server, "--port", "4000")
        self.add("--plugin", "opencode:opencode-foo@1.2.3")
        self.add("--plugin", "claude:helper@market@2.0.1", "--for", "coordinator")
        self.add("--persona", "migrations", "--prompt-file", prompt, "--tier", "deep", "--harness", "opencode")
        ids = {X.ident(e): e for e in self.stored()}
        self.assertEqual(sorted(ids), sorted(["mcp:github", "mcp:local", "plugin:opencode:opencode-foo",
                                              "plugin:claude:helper@market", "persona:migrations"]))
        self.assertEqual(ids["mcp:github"]["env"], ["GITHUB_TOKEN"])
        self.assertEqual(ids["mcp:local"]["files"], {str(self.server): X.sha256(self.server)})
        self.assertEqual(ids["plugin:claude:helper@market"]["for"], ["coordinator"])
        self.assertEqual(ids["persona:migrations"]["tier"], "deep")
        self.assertEqual(oct(X.project_file(self.store).stat().st_mode & 0o777), "0o600")
        self.clean(self.repo)
        out = self.ok("project", "list", self.repo)
        self.assertIn("by role:", out)
        self.assertIn("lanes: mcp:github, mcp:local, plugin:opencode:opencode-foo, persona:migrations", out)
        self.assertEqual(len(json.loads(self.ok("project", "list", self.repo, "--json"))["extras"]), 5)

    def test_the_same_id_replaces_and_remove_takes_it_out(self):
        self.add("--plugin", "opencode:foo@1.0.0")
        out = self.add("--plugin", "opencode:foo@1.1.0", "--for", "lanes")
        self.assertIn("replaced plugin:opencode:foo", out)
        self.assertEqual([(e["pin"], e["for"]) for e in self.stored()], [("1.1.0", ["lanes"])])
        self.ok("project", "remove", self.repo, "plugin:opencode:foo")
        self.assertEqual(self.stored(), [])
        self.refused("project", "remove", self.repo, "plugin:opencode:foo", says="no extra")

    def test_the_kinds_left_for_ext_1b_are_refused(self):
        for args in (("--permission", "allow", "Bash(make:*)"), ("--agent-hook", "Stop", "--", self.server),
                     ("--env", "APP_MODE=fast")):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_le("project", "add", self.repo, *args)[0], 0)
        self.refused("project", "add", self.repo, "--env", "GITHUB_TOKEN", says="exactly one kind")
        self.refused("project", "add", self.repo, "--plugin", "opencode:x@1.0.0", "--env", "A", says="for --mcp only")
        self.assertEqual(self.stored(), [])

    def test_a_project_needs_to_be_set_up_and_on_native_orca(self):
        other = self.t / "other"
        other.mkdir()
        git("init", "-q", cwd=other)
        self.refused("project", "add", other, "--plugin", "opencode:foo@1.0.0", says="no project set up")
        self.refused("project", "add", "nope", "--plugin", "opencode:foo@1.0.0", says="no project set up")
        (self.store / "settings.json").write_text(json.dumps({"root": str(self.repo), "orchestrator": "orca-cotal"}))
        self.refused("project", "add", self.repo, "--plugin", "opencode:foo@1.0.0", says="native Orca only")

    def test_machine_extras_and_a_project_adds_to_them(self):
        self.ok("machine", "add", "--plugin", "opencode:foo@1.0.0")
        self.ok("machine", "add", "--plugin", "opencode:bar@1.0.0")
        self.add("--plugin", "opencode:foo@2.0.0")
        eff = {X.ident(e): e for e in X.effective(self.store)}
        self.assertEqual((eff["plugin:opencode:foo"]["pin"], eff["plugin:opencode:foo"]["origin"]),
                         ("2.0.0", "project"))
        self.assertEqual(eff["plugin:opencode:bar"]["origin"], "machine")

    def test_the_design_skills_are_default_machine_extras_and_can_be_turned_off(self):
        os.environ["LANE_DESIGN_SKILLS"] = str(ROOT / "core" / "design-skills.tsv")
        out = self.ok("machine", "list")
        for name in ("frontend-design", "ui-ux-pro-max", "modern-web-guidance"):
            self.assertRegex(out, rf"skill:{name}\s+default\s+lanes,coordinator,reviewer")
        self.assertIn("turned off the default skill:modern-web-guidance",
                      self.ok("machine", "remove", "modern-web-guidance"))
        self.assertNotIn("skill:modern-web-guidance", {X.ident(e) for e in X.effective(self.store)})
        self.assertIn("off", self.ok("machine", "list"))
        self.ok("machine", "add", "--skill",
                f"https://github.com/GoogleChrome/modern-web-guidance.git@{'a' * 40}:skills/modern-web-guidance")
        self.assertEqual({X.ident(e): e for e in X.effective(self.store)}["skill:modern-web-guidance"]["origin"],
                         "machine")


class PersonTests(Scratch):
    """M5: add and remove are a person's only (CLI-2's person check); list and lane prepare are for agents too."""

    def test_an_agent_cannot_add_or_remove(self):
        self.add("--plugin", "opencode:foo@1.0.0")
        for args in (("project", "extras", "add", self.repo, "--plugin", "opencode:bar@1.0.0"),
                     ("project", "extras", "remove", self.repo, "plugin:opencode:foo"),
                     ("machine", "extras", "add", "--plugin", "opencode:bar@1.0.0")):
            with self.subTest(args=args):
                r = self.lc(*args)  # no terminal on stdin: as an agent's tool runs it
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn("is for a person at a terminal", r.stderr)
        self.assertEqual([X.ident(e) for e in self.stored()], ["plugin:opencode:foo"])
        self.assertEqual(self.stored(machine=True), [])
        self.assertEqual(self.lc("project", "extras", "list", self.repo).returncode, 0)
        r = self.lc("lane", "prepare", self.repo, self.wt, "--role", "lanes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class SecretTests(Scratch):
    """B6: no secret is accepted, stored or printed."""

    def test_urls_and_mcp_command_lines_with_secrets_are_refused_without_showing_them(self):
        u = "https://github.com/o/r.git"
        for args, says in (
                (("--skill", f"https://x-access-token:{SECRET}@github.com/o/r.git@{'a' * 40}"), "password or token"),
                (("--skill", f"https://{SECRET}@github.com/o/r.git@{'a' * 40}"), "password or token"),
                (("--skill", f"{u}?token={SECRET}@{'a' * 40}"), "password or token"),
                (("--mcp", "a", "--", "srv", f"--api-key={SECRET}"), "carries a key"),
                (("--mcp", "a", "--", "srv", "--api-key=hunter2"), "secret-named option"),
                (("--mcp", "a", "--", "srv", "--token", "hunter2"), "secret-named option"),
                (("--mcp", "a", "--", "srv", "postgres://u:hunter2@db/x"), "carries a key"),
                (("--mcp", "a", "--", "srv", "--header", "Authorization: Bearer abcdefgh"), "carries a key"),
                (("--mcp", "a", "--", "env", "GITHUB_TOKEN=hunter2", "srv"), "secret-named option"),
                (("--mcp", "a", "--", "srv", "https://api.example.com/x?access_token=hunter2"), "carries a key"),
                (("--mcp", "a", "--", "srv", "Z9" * 20), "carries a key"),
                (("--mcp", "a", "--env", f"GITHUB_TOKEN={SECRET}", "--", "srv"), "a NAME only"),
                (("--mcp", "a", "--env", "hunter2value", "--", "srv"), "NAME in capitals")):
            with self.subTest(args=args[:2]):
                self.refused("project", "add", self.repo, *args, says=says)
        self.assertEqual(self.stored(), [])

    def test_an_unparsed_value_is_never_echoed(self):
        code, out, err = self.run_le("project", "add", self.repo, "--mcp", "z", "--env", "NAME", SECRET, "--", "srv")
        self.assertEqual(code, 2)
        self.assertNotIn(SECRET, out + err)
        self.assertIn("not shown", err)

    def test_references_and_pinned_packages_are_fine(self):
        self.add("--mcp", "gh", "--", "npx", "-y", "mcp-server-sequential-thinking@2025.12.18")  # m5
        self.add("--mcp", "api", "--", "srv", "--token", "${API_TOKEN}", "--header=Authorization=${API_AUTH}")
        e = {X.ident(e): e for e in self.stored()}["mcp:api"]
        self.assertEqual(e["env"], ["API_TOKEN", "API_AUTH"])
        self.assertEqual(X.opencode_mcp(e)["command"], ["srv", "--token", "{env:API_TOKEN}",
                                                        "--header=Authorization={env:API_AUTH}"])

    def test_a_secret_in_the_environment_never_reaches_a_file_or_the_output(self):
        self.add("--mcp", "github", "--env", "GITHUB_TOKEN", "--", "npx", "srv")
        os.environ["GITHUB_TOKEN"] = SECRET
        code, out, err = self.prepare()
        self.assertEqual(code, 0, out)
        self.coordinator()
        found = [p for p in self.t.rglob("*") if p.is_file() and not p.is_symlink()
                 and SECRET.encode() in p.read_bytes()]
        self.assertEqual(found, [])
        self.assertNotIn(SECRET, out + err)
        self.assertEqual(json.loads((self.wt / ".mcp.json").read_text())["mcpServers"]["github"]["env"],
                         {"GITHUB_TOKEN": "${GITHUB_TOKEN}"})


class ContainmentTests(Scratch):
    """M1: a file an extra names is outside every project checkout, as given and resolved."""

    def inside(self, rel, text="#!/bin/sh\nexit 0\n"):
        f = self.wt / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        f.chmod(0o755)
        return f

    def test_every_way_into_a_checkout_is_refused(self):
        script = self.inside("tools/h.sh")
        link = self.owner / "h.sh"
        link.symlink_to(script)
        nested = self.repo / "vendor" / "sub"
        nested.mkdir(parents=True)
        git("init", "-q", cwd=nested)
        (nested / "h.sh").write_text("#!/bin/sh\n")
        (nested / "h.sh").chmod(0o755)
        for args in (("--mcp", "a", "--", link), ("--mcp", "a", "--", "python3", script),
                     ("--mcp", "a", "--", nested / "h.sh"), ("--mcp", "a", "--", script)):
            with self.subTest(args=args):
                self.refused("project", "add", self.repo, *args, says="inside the project checkout")
        for args in (("--mcp", "a", "--", "python3", "server.py"), ("--mcp", "a", "--", "./srv"),
                     ("--mcp", "a", "--", "node", "dist/index.js")):
            with self.subTest(args=args):
                self.refused("project", "add", self.repo, *args, says="relative path")
        prompt = self.inside("notes/p.md", "You are a lane.\n")
        self.refused("project", "add", self.repo, "--persona", "p", "--prompt-file", prompt,
                     says="inside the project checkout")
        self.assertEqual(self.stored(), [])
        out = self.add("--mcp", "a", "--allow-repo-path", "--", script)
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("warning:")]), 1, out)


class RoleTests(Scratch):
    """B1: each role's extras only where that role's sessions read them, proven on what a Claude session loads."""

    def setUp(self):
        super().setUp()
        d, sha = self.upstream("coord-skill", "skills/coord")
        self.add("--skill", f"file://{d}@{sha}:skills/coord", "--for", "coordinator")
        d, sha = self.upstream("lane-skill", "skills/lanesk")
        self.add("--skill", f"file://{d}@{sha}:skills/lanesk", "--for", "lanes")
        self.add("--mcp", "coordmcp", "--for", "coordinator", "--", "npx", "coord-server")
        self.add("--mcp", "lanemcp", "--for", "lanes", "--", "npx", "lane-server")
        self.add("--mcp", "reviewmcp", "--for", "reviewer", "--", "npx", "review-server")
        self.add("--plugin", "claude:coordplug@market@1.0.0", "--for", "coordinator")
        self.add("--plugin", "opencode:laneplug@1.0.0", "--for", "lanes")
        # the main checkout's local settings, as setup writes them (the persona hook): every worktree reads it
        (self.repo / ".claude").mkdir()
        self.own = {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command",
                                                                                   "command": "/x/persona-hook.sh"}]}]}}
        (self.repo / ".claude" / "settings.local.json").write_text(json.dumps(self.own))
        info = self.repo / ".git" / "info"
        info.mkdir(exist_ok=True)
        with open(info / "exclude", "a") as f:
            f.write("/.claude/settings.local.json\n")  # as setup's own exclude block does

    def test_a_lane_never_loads_the_coordinators_extras_and_the_coordinator_never_a_lanes(self):
        lines, problems = self.coordinator()
        self.assertEqual(problems, [])
        code, out, err = self.prepare()
        self.assertEqual(code, 0, out)
        launch = X.launch_args(self.store)
        lane = claude_reads(self.wt, self.repo)
        coord = claude_reads(self.repo, self.repo, launch)
        for name in ("coordmcp", "coordplug"):
            self.assertNotIn(name, lane["text"])
            self.assertIn(name, coord["text"])
        self.assertNotIn("coord", lane["skills"])
        self.assertIn("coord", coord["skills"])
        self.assertIn("lanemcp", lane["text"])
        self.assertEqual(lane["skills"], ["lanesk"])
        for name in ("lanemcp", "reviewmcp"):
            self.assertNotIn(name, coord["text"])
        self.assertNotIn("lanesk", coord["skills"])
        self.assertNotIn("reviewmcp", lane["text"])
        # nothing of the coordinator's in the main checkout's Claude files, which worktrees read
        self.assertEqual(json.loads((self.repo / ".claude" / "settings.local.json").read_text()), self.own)
        self.assertFalse((self.repo / ".mcp.json").exists())
        self.assertFalse((self.repo / ".claude" / "skills").exists())
        # OpenCode: the coordinator's in the main checkout's .opencode, a lane's in its worktree's
        oc_main = json.loads((self.repo / ".opencode" / "opencode.json").read_text())
        oc_lane = json.loads((self.wt / ".opencode" / "opencode.json").read_text())
        self.assertEqual(sorted(oc_main["mcp"]), ["coordmcp"])
        self.assertEqual(sorted(oc_lane["mcp"]), ["lanemcp"])
        self.assertEqual(oc_lane["plugin"], ["laneplug@1.0.0"])
        self.assertTrue((self.wt / ".opencode" / "skills" / "lanesk" / "SKILL.md").is_file())
        self.assertTrue((self.repo / ".opencode" / "skills" / "coord" / "SKILL.md").is_file())
        self.assertEqual(launch[0::2], ["--mcp-config", "--settings", "--plugin-dir"])
        self.clean(self.repo)
        self.clean(self.wt)

    def test_a_reviewer_adds_its_own_and_only_lane_control_servers_are_approved(self):
        self.prepare()
        code, out, err = self.prepare("reviewer")
        self.assertEqual(code, 0, out)
        s = json.loads((self.wt / ".claude" / "settings.json").read_text())
        self.assertEqual(s["enabledMcpjsonServers"], ["lanemcp", "reviewmcp"])
        self.assertEqual(sorted(json.loads((self.wt / ".mcp.json").read_text())["mcpServers"]),
                         ["lanemcp", "reviewmcp"])
        code, out, err = self.prepare("lanes")  # the reviewer's stay while it may run (m6: documented)
        self.assertIn("reviewmcp", (self.wt / ".mcp.json").read_text(), out)


class SafetyTests(Scratch):
    """B4, B5, M2, M3: only files lane-control creates and owns whole; all or nothing; never through a link."""

    def setUp(self):
        super().setUp()
        self.add("--mcp", "github", "--", "npx", "srv")

    def test_a_planted_mcp_json_is_neither_used_nor_approved(self):
        (self.wt / ".mcp.json").write_text(json.dumps({"mcpServers": {"github": {"command": "/tmp/planted"}}}))
        code, out, err = self.prepare()
        self.assertEqual(code, 1)
        self.assertIn("is not lane-control's", out)
        self.assertFalse((self.wt / ".claude" / "settings.json").exists())
        self.assertFalse((self.wt / ".claude" / "settings.local.json").exists())

    def test_a_committed_file_is_never_changed(self):
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "settings.json").write_text("{}\n")
        git("add", ".claude/settings.json", cwd=self.repo)
        git("commit", "-q", "-m", "settings", cwd=self.repo)
        git("merge", "-q", "main", cwd=self.wt)
        code, out, err = self.prepare()
        self.assertEqual(code, 0, out)  # settings.json is the project's: the approvals go to the local file
        self.assertEqual((self.wt / ".claude" / "settings.json").read_text(), "{}\n")
        self.assertIn("github", (self.wt / ".claude" / "settings.local.json").read_text())
        self.clean(self.wt)
        (self.repo / ".mcp.json").write_text("{}\n")
        git("add", "-f", ".mcp.json", cwd=self.repo)
        git("commit", "-q", "-m", "mcp", cwd=self.repo)
        (self.wt / ".mcp.json").unlink()
        git("merge", "-q", "main", cwd=self.wt)
        code, out, err = self.prepare()
        self.assertEqual(code, 1)
        self.assertIn(".mcp.json is committed in the project", out)
        self.assertFalse((self.wt / ".claude" / "settings.local.json").exists())  # all or nothing: ours came out
        self.assertEqual((self.wt / ".mcp.json").read_text(), "{}\n")
        self.assertEqual(git("status", "--porcelain", cwd=self.wt), "")

    def test_nothing_is_written_through_a_symlink(self):
        user = self.home / ".claude"
        user.mkdir()
        (user / "settings.json").write_text('{"model": "x"}\n')
        (self.wt / ".claude").symlink_to(user)
        code, out, err = self.prepare()
        self.assertEqual(code, 1)
        self.assertIn("is a symlink", out)
        self.assertEqual((user / "settings.json").read_text(), '{"model": "x"}\n')
        (self.wt / ".claude").unlink()
        (self.wt / ".mcp.json").symlink_to(user / "settings.json")
        code, out, err = self.prepare()
        self.assertEqual(code, 1)
        self.assertEqual((user / "settings.json").read_text(), '{"model": "x"}\n')
        self.assertTrue((self.wt / ".mcp.json").is_symlink())

    def test_a_changed_mcp_file_stops_it_and_one_problem_leaves_none_of_ours(self):
        self.add("--mcp", "local", "--", self.server)
        self.assertEqual(self.prepare()[0], 0)
        self.assertTrue((self.wt / ".mcp.json").exists())
        self.server.write_text("#!/bin/sh\necho changed\n")
        code, out, err = self.prepare()
        self.assertEqual(code, 1)
        self.assertIn("changed since it was added", out)
        for rel in (".mcp.json", ".claude/settings.json", ".opencode/opencode.json"):
            self.assertFalse((self.wt / rel).exists(), rel)  # fail closed: none of lane-control's stays
        self.assertFalse(X.record_file(self.wt).exists())

    def test_a_file_changed_after_writing_is_left_and_named(self):
        self.prepare()
        (self.wt / ".mcp.json").write_text('{"mcpServers": {"github": {"command": "/tmp/lane-edit"}}}\n')
        code, out, err = self.prepare()
        self.assertEqual(code, 1)
        self.assertFalse((self.wt / ".claude" / "settings.json").exists())  # its approval is gone
        self.assertIn("lane-edit", (self.wt / ".mcp.json").read_text())


class RevokeTests(Scratch):
    """M4, m2, m6: remove + refresh takes it out of every recorded worktree; the exclude names exactly what is there."""

    def test_remove_and_refresh_revoke_from_every_worktree(self):
        self.add("--mcp", "github", "--", "npx", "srv")
        self.prepare()
        exclude = self.repo / ".git" / "info" / "exclude"
        self.assertIn("/.mcp.json", exclude.read_text())
        self.assertNotIn("/.claude/skills", exclude.read_text())
        self.ok("project", "remove", self.repo, "github")
        recorded = X.recorded_worktrees(self.repo)
        self.assertEqual([wt for wt, _ in recorded], [self.wt.resolve()])
        for wt, rec in recorded:  # what refresh does after the coordinator's
            lines, problems = X.apply_folder(wt, X.prepare_entries(X.effective(self.store), rec["scopes"]),
                                             rec["scopes"], project=self.repo)
            self.assertTrue(any("removed" in l for l in lines), lines)
        for rel in (".mcp.json", ".claude/settings.json", ".opencode/opencode.json"):
            self.assertFalse((self.wt / rel).exists(), rel)
        self.assertNotIn("lane-control extras", exclude.read_text())
        self.clean(self.wt)

    def test_a_new_worktree_at_an_old_path_starts_fresh(self):
        self.add("--mcp", "rev", "--for", "reviewer", "--", "npx", "srv")
        self.prepare("reviewer")
        git("worktree", "remove", "--force", self.wt, cwd=self.repo)
        git("worktree", "add", "-q", self.wt, "-b", "lane-2", cwd=self.repo)
        self.assertEqual(X.read_record(self.wt), {})
        self.assertEqual(self.prepare("lanes")[0], 0)
        self.assertFalse((self.wt / ".mcp.json").exists())  # no reviewer scope carried over

    def test_no_extras_leaves_every_folder_exactly_as_it_was(self):
        exclude = self.repo / ".git" / "info" / "exclude"
        before = exclude.read_text()
        self.assertEqual(self.prepare()[0], 0)
        self.assertEqual(self.coordinator(), ([], []))
        self.assertEqual(exclude.read_text(), before)
        self.assertEqual(list(self.wt.glob(".claude")) + list(self.wt.glob(".opencode")), [])
        self.assertFalse(X.record_file(self.wt).exists())
        self.assertEqual(X.launch_args(self.store), [])


class SkillSourceTests(Scratch):
    def skill(self, url, sha):
        return {"kind": "skill", "name": "fd", "url": url, "commit": sha, "path": "skills/fd"}

    def test_skills_are_pinned_checked_and_kept_apart_from_the_defaults(self):
        d, sha = self.upstream("fd", "skills/fd")
        os.environ["LANE_DESIGN_SKILLS"] = str(self.t / "table.tsv")
        (self.t / "table.tsv").write_text(f"fd\tfile://{d}\t{sha}\tskills/fd\tMIT\t-\n")
        (d / "skills" / "fd" / "SKILL.md").write_text("v2\n")
        git("commit", "-q", "-am", "v2", cwd=d)
        sha2 = git("rev-parse", "HEAD", cwd=d)
        self.add("--skill", f"file://{d}@{sha2}:skills/fd", "--name", "fd", "--for", "lanes")
        self.assertEqual(self.prepare()[0], 0)
        link = self.wt / ".claude" / "skills" / "fd"
        self.assertEqual((link / "SKILL.md").read_text(), "v2\n")
        X.user_skills(X.effective(None))  # M8: the defaults' pass never removes the project's pin of that name
        self.assertEqual((link / "SKILL.md").read_text(), "v2\n")
        self.assertEqual((self.home / ".agents" / "skills" / "fd" / "SKILL.md").read_text().splitlines()[-1], "v1")
        self.assertFalse((X.source_dir(self.skill(f"file://{d}", sha2)) / "big").exists())
        # M2, m7: a pinned checkout changed by hand is flagged and stops prepare; another URL with the same name and
        # commit is its own checkout
        (X.skill_dir(self.skill(f"file://{d}", sha2)) / "SKILL.md").write_text("tampered\n")
        self.assertEqual({r["id"]: r for r in X.check(self.store)}["skill:fd"]["state"], "changed")
        self.assertEqual(self.prepare()[0], 1)
        self.assertNotEqual(X.source_dir(self.skill("file:///elsewhere", sha2)),
                            X.source_dir(self.skill(f"file://{d}", sha2)))

    def test_an_owners_symlinked_default_install_is_theirs(self):
        # M9
        d, sha = self.upstream("fd", "skills/fd")
        os.environ["LANE_DESIGN_SKILLS"] = str(self.t / "table.tsv")
        (self.t / "table.tsv").write_text(f"fd\tfile://{d}\t{sha}\tskills/fd\tMIT\t-\n")
        mine = self.t / "dotfiles" / "fd"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text("mine\n")
        (self.home / ".agents" / "skills").mkdir(parents=True)
        (self.home / ".agents" / "skills" / "fd").symlink_to(mine)
        self.assertEqual(dict(X.user_skills(X.effective(None)))["fd"], "exists (not lane-control's)")
        self.assertEqual({r["id"]: r for r in X.check(self.store)}["skill:fd"]["state"], "ok")
        shutil.rmtree(mine)
        self.assertEqual({r["id"]: r for r in X.check(self.store)}["skill:fd"]["state"], "missing")


class CheckTests(Scratch):
    def rows(self):
        return {r["id"]: r for r in X.check(self.store, self.repo)}

    def test_missing_changed_and_failing_are_flagged(self):
        prompt = self.owner / "p.md"
        prompt.write_text("You are a lane.\n")
        self.add("--mcp", "gone", "--", "no-such-command-anywhere")
        self.add("--mcp", "local", "--", self.server)
        self.add("--persona", "p", "--prompt-file", prompt)
        self.add("--skill", f"file://{self.t}/x@{'1' * 40}:s/k")
        self.add("--plugin", "claude:helper@market@2.0.1")
        rows = self.rows()
        self.assertEqual(rows["mcp:gone"]["state"], "failing")
        self.assertEqual(rows["mcp:local"]["state"], "ok")
        self.assertEqual(rows["persona:p"]["state"], "ok")
        self.assertEqual(rows["skill:k"]["state"], "missing")
        self.assertEqual(rows["plugin:claude:helper@market"]["state"], "missing")
        self.assertIn("claude plugin install helper@market", rows["plugin:claude:helper@market"]["detail"])
        self.assertEqual(rows["coordinator"]["state"], "failing")  # M7: it cannot get them now, and says why
        self.server.write_text("#!/bin/sh\necho changed\n")
        prompt.write_text("Another text.\n")
        rows = self.rows()
        self.assertEqual(rows["mcp:local"]["state"], "changed")
        self.assertEqual(rows["persona:p"]["state"], "changed")
        prompt.unlink()
        self.assertEqual(self.rows()["persona:p"]["state"], "missing")

    def test_a_coordinator_file_it_cannot_write_is_failing_not_ok(self):
        # M7: a committed .opencode/opencode.json in the main checkout
        self.add("--mcp", "github", "--for", "coordinator", "--", "sh")
        (self.repo / ".opencode").mkdir()
        (self.repo / ".opencode" / "opencode.json").write_text("{}\n")
        git("add", ".opencode/opencode.json", cwd=self.repo)
        git("commit", "-q", "-m", "oc", cwd=self.repo)
        rows = self.rows()
        self.assertEqual(rows["mcp:github"]["state"], "ok")
        self.assertEqual(rows["coordinator"]["state"], "failing")
        self.assertIn("committed in the project", rows["coordinator"]["detail"])

    def test_an_applied_coordinator_is_ok_and_a_plugin_version_is_checked(self):
        self.add("--plugin", "claude:helper@market@2.0.1", "--for", "coordinator")
        self.assertEqual(self.rows()["coordinator"]["state"], "not applied")
        self.coordinator()
        self.assertNotIn("coordinator", self.rows())
        p = self.home / ".claude" / "plugins"
        p.mkdir(parents=True)
        (p / "installed_plugins.json").write_text(json.dumps({"plugins": {"helper@market": [{"version": "1.9.0"}]}}))
        self.assertEqual(self.rows()["plugin:claude:helper@market"]["state"], "changed")
        (p / "installed_plugins.json").write_text(json.dumps({"plugins": {"helper@market": [{"version": "2.0.1"}]}}))
        self.assertEqual(self.rows()["plugin:claude:helper@market"]["state"], "ok")

    def test_doctor_lists_them_and_says_what_to_do(self):
        self.add("--mcp", "gone", "--", "no-such-command-anywhere")
        d = json.loads(self.lc("doctor", "--no-ssh", "--json").stdout)
        rows = {x["id"]: x for x in d["extras"][str(self.repo)]}
        self.assertEqual(rows["mcp:gone"]["state"], "failing")
        self.assertTrue(any("extra mcp:gone failing" in t for t in d["todo"]), d["todo"])
        self.assertIn("extras", self.lc("doctor", "--no-ssh").stdout)


class PersonaTests(Scratch):
    def test_custom_personas_render_with_additions_and_the_coordinator_is_told(self):
        prompt = self.owner / "migrations.md"
        prompt.write_text("---\ndescription: Writes database migrations\n---\nYou write migrations.\n")
        self.add("--persona", "migrations", "--prompt-file", prompt, "--tier", "deep")
        self.add("--plugin", "opencode:foo@1.0.0", "--for", "lanes")
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
        self.clean(self.repo)
        self.ok("project", "remove", self.repo, "migrations")
        X.render_personas(self.repo, X.effective(self.store), lambda n: "")
        self.assertFalse((self.repo / ".lane-control" / "personas" / "migrations.md").exists())

    def test_nothing_to_tell_with_the_defaults_alone(self):
        os.environ["LANE_DESIGN_SKILLS"] = str(ROOT / "core" / "design-skills.tsv")
        self.assertEqual(X.coordinator_section(self.repo, X.effective(self.store), "/lc"), "")


class RoundTwoTests(Scratch):
    """Review round 2 (57d1849): one test per finding, with the exact case the review gave."""

    def test_b1_the_main_checkout_is_still_ours_after_ordinary_git_commands(self):
        d, sha = self.upstream("db", "skills/db")
        self.add("--mcp", "coordmcp", "--for", "coordinator", "--", "npx", "coord-server")
        self.add("--skill", f"file://{d}@{sha}:skills/db", "--for", "coordinator")
        self.assertEqual(self.coordinator()[1], [])
        oc = self.repo / ".opencode" / "opencode.json"
        self.assertIn("coordmcp", oc.read_text())
        git("commit", "-q", "--allow-empty", "-m", "between", cwd=self.repo)  # the .git folder's ctime changes
        (self.repo / "README.md").write_text("edited\n")
        git("status", "--porcelain", cwd=self.repo)
        git("add", "README.md", cwd=self.repo)
        git("commit", "-q", "-m", "edit", cwd=self.repo)
        git("worktree", "add", "-q", self.t / "workspaces" / "lane-2", "-b", "lane-2", cwd=self.repo)
        self.add("--mcp", "coordmcp", "--for", "coordinator", "--", "npx", "coord-server-2")  # changed
        lines, problems = self.coordinator()
        self.assertEqual(problems, [])
        self.assertIn("coord-server-2", oc.read_text())
        git("commit", "-q", "--allow-empty", "-m", "again", cwd=self.repo)
        self.ok("project", "remove", self.repo, "skill:db")  # swapped out: the link goes, and git never shows it
        lines, problems = self.coordinator()
        self.assertEqual(problems, [])
        self.assertFalse((self.repo / ".opencode" / "skills" / "db").is_symlink())
        self.clean(self.repo)
        git("commit", "-q", "--allow-empty", "-m", "and again", cwd=self.repo)
        self.ok("project", "remove", self.repo, "mcp:coordmcp")
        lines, problems = self.coordinator()
        self.assertEqual(problems, [])
        self.assertTrue(any("removed" in l for l in lines), lines)
        self.assertFalse(oc.exists())
        self.assertNotIn("coordinator", {r["id"] for r in X.check(self.store, self.repo)})
        self.clean(self.repo)

    def test_m1_header_query_json_and_dash_forms_of_a_secret_are_refused(self):
        tok = "Z9" * 18
        for args in ((f"--header", f"X-API-Key: {tok}"), ("--header", "X-API-Key: hunter2"),
                     ("-H", "Cookie: session=hunter2"), (f"https://api.example.com/x?access-token={tok}",),
                     ("https://api.example.com/x?apikey=hunter2",), ('{"apiKey":"hunter2"}',),
                     ('{"api_key": "hunter2"}',), ("--token", "-hunter2"), (f"abcdefghijklmnop/{tok}QRSTUV",),
                     ("Authorization: Bearer hunter2",)):
            with self.subTest(args=args):
                self.refused("project", "add", self.repo, "--mcp", "a", "--", "srv", *args, says="")
        self.assertEqual(self.stored(), [])
        self.add("--mcp", "ok", "--", "srv", "--header", "X-API-Key: ${API_KEY}", "https://h/x?apikey=${API_KEY}",
                 '{"apiKey":"${API_KEY}"}')
        self.assertEqual({X.ident(e): e for e in self.stored()}["mcp:ok"]["env"], ["API_KEY"])

    def test_m2_a_path_inside_an_option_is_refused_or_pinned_like_a_bare_one(self):
        inside = self.wt / "tools" / "h.sh"
        inside.parent.mkdir()
        inside.write_text("#!/bin/sh\n")
        self.refused("project", "add", self.repo, "--mcp", "a", "--", "node", f"--require={inside}",
                     says="inside the project checkout")
        self.refused("project", "add", self.repo, "--mcp", "a", "--", "srv", f"-I{self.repo}/lib",
                     says="inside the project checkout")
        self.refused("project", "add", self.repo, "--mcp", "a", "--", "node", "tools/server", says="relative path")
        self.refused("project", "add", self.repo, "--mcp", "a", "--", "node", "--require=tools/h.js",
                     says="relative path")
        mine = self.script("pre.js", "1\n")
        self.add("--mcp", "b", "--", "node", f"--require={mine}")
        e = {X.ident(e): e for e in self.stored()}["mcp:b"]
        self.assertEqual(e["files"], {str(mine): X.sha256(mine)})
        self.assertEqual(e["args"], [f"--require={mine}"])
        mine.write_text("2\n")
        self.assertEqual({r["id"]: r for r in X.check(self.store)}["mcp:b"]["state"], "changed")

    def test_m1_minor_a_removed_worktree_takes_its_exclude_lines_with_it(self):
        self.add("--mcp", "github", "--for", "lanes", "--", "npx", "srv")
        self.assertEqual(self.prepare()[0], 0)
        exclude = self.repo / ".git" / "info" / "exclude"
        self.assertIn("/.mcp.json", exclude.read_text())
        git("worktree", "remove", "--force", self.wt, cwd=self.repo)
        self.assertEqual(X.recorded_worktrees(self.repo), [])
        self.assertNotIn("lane-control extras", exclude.read_text())
        (self.repo / ".mcp.json").write_text("{}\n")
        self.assertIn(".mcp.json", git("status", "--porcelain", cwd=self.repo))

    def test_m2_minor_doctor_skips_a_project_not_on_native_orca(self):
        self.ok("machine", "add", "--plugin", "claude:helper@market@2.0.1", "--for", "coordinator")
        (self.store / "settings.json").write_text(json.dumps({"root": str(self.repo), "orchestrator": "orca-cotal"}))
        d = json.loads(self.lc("doctor", "--no-ssh", "--json").stdout)
        self.assertNotIn(str(self.repo), d["extras"])
        self.assertFalse([t for t in d["todo"] if "extra" in t], d["todo"])

    def test_m3_a_write_that_stops_part_way_is_a_problem_and_still_ours_after(self):
        self.add("--mcp", "github", "--", "npx", "srv")
        self.assertEqual(self.prepare()[0], 0)
        self.add("--mcp", "github", "--", "npx", "srv-2")
        real, n = X.write_file, []

        def flaky(path, text, mode=0o644):
            n.append(path)
            if len(n) == 3:  # the record first, then one file, then the disk is full
                raise OSError(28, "No space left on device")
            return real(path, text, mode)
        with mock.patch.object(LE.X, "write_file", flaky):
            code, out, err = self.prepare()
        self.assertEqual(code, 1)
        self.assertIn("could not write", out)
        self.assertNotIn("Traceback", err)
        code, out, err = self.prepare()
        self.assertEqual(code, 0, out)
        self.assertNotIn("not lane-control's", out)
        self.assertIn("srv-2", (self.wt / ".mcp.json").read_text())
        self.assertIn("srv-2", (self.wt / ".opencode" / "opencode.json").read_text())

    def test_m5_a_project_skill_with_a_default_skills_name_is_warned(self):
        os.environ["LANE_DESIGN_SKILLS"] = str(ROOT / "core" / "design-skills.tsv")
        out = self.add("--skill", f"https://github.com/o/r.git@{'a' * 40}:skills/frontend-design")
        self.assertIn("also a default design skill", out)
        self.assertIn("lane-control machine extras remove frontend-design", out)

    def test_m6_no_text_names_the_removed_kinds_as_extras(self):
        self.assertIn("skills, MCP, personas, plugins", F.ROUTES[("project", "extras")][3])
        text = (ROOT / "docs" / "how-it-fits.md").read_text()
        self.assertIn("your skills, MCP servers, personas and plugins", text)
        self.assertNotIn("hooks, permissions,\n", text)

    def test_n1_a_file_already_there_with_the_same_text_is_not_taken_over(self):
        self.add("--plugin", "opencode:foo@1.0.0", "--for", "lanes")
        want = X.opencode_files({"mcp": [], "skill": [], "plugin": [e for e in X.effective(self.store)]})
        (self.wt / ".opencode").mkdir()
        (self.wt / ".opencode" / "opencode.json").write_text(want[".opencode/opencode.json"])
        code, out, err = self.prepare()
        self.assertEqual(code, 1)
        self.assertIn("is not lane-control's", out)
        self.assertTrue((self.wt / ".opencode" / "opencode.json").exists())


class RefreshTests(unittest.TestCase):
    """project-setup on native Orca, against the stub orca of tests/stubs: refresh fetches every pin, gives the
    coordinator its extras at launch, renders the personas and applies the recorded worktrees again."""

    def setUp(self):
        from test_project_setup_native import NativeEnv
        self.e = NativeEnv()
        self.addCleanup(self.e.close)
        self.repo = self.e.repo()
        r = self.e.run(str(self.repo), "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.owner = self.e.home / "owner"
        self.owner.mkdir()
        patch = mock.patch.dict(os.environ, self.e.env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def add(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(LE, "person", lambda what: None), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = LE.main(["project", "add", str(self.repo), *map(str, args)])
        self.assertEqual(code, 0, err.getvalue())

    def test_refresh_gives_the_coordinator_its_extras_at_launch_and_fetches_every_pin(self):
        prompt = self.owner / "migrations.md"
        prompt.write_text("You write database migrations.\n")
        up = self.e.home / "up"
        (up / "skills" / "db").mkdir(parents=True)
        (up / "skills" / "db" / "SKILL.md").write_text("---\nname: db\ndescription: d\n---\nv1\n")
        git("init", "-q", "-b", "main", cwd=up)
        git("add", "-A", cwd=up)
        git("commit", "-q", "-m", "v1", cwd=up)
        sha = git("rev-parse", "HEAD", cwd=up)
        self.add("--persona", "migrations", "--prompt-file", prompt, "--tier", "deep")
        self.add("--mcp", "coordmcp", "--for", "coordinator", "--", "sh")
        self.add("--skill", f"file://{up}@{sha}:skills/db", "--for", "lanes")
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("You write database migrations.",
                      (self.repo / ".lane-control" / "personas" / "migrations.md").read_text())
        coord = (self.repo / ".lane-control" / "coordinator.md").read_text()
        self.assertIn("# Extras for this project", coord)
        self.assertIn("lane prepare", coord)
        store = X.store_for(self.repo)
        self.assertIn("coordmcp", (X.launch_dir(store) / "mcp.json").read_text())
        self.assertNotIn("coordmcp", (self.repo / ".claude" / "settings.local.json").read_text())
        self.assertFalse((self.repo / ".mcp.json").exists())
        self.assertTrue((X.skill_dir({"kind": "skill", "name": "db", "url": f"file://{up}", "commit": sha,
                                      "path": "skills/db"}) / "SKILL.md").is_file())  # M6: a lanes-only pin
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=self.repo), "")
        r = self.e.run(str(self.repo))  # setup starts the coordinator: its launch script names the files
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        argv = [x["argv"] for x in self.e.sessions() if x["role"] == "coordinator"][-1]
        self.assertEqual(argv[argv.index("--mcp-config") + 1], str(X.launch_dir(store) / "mcp.json"))
        self.assertLess(argv.index("--mcp-config"), argv.index("--name"))  # the variadic flag never takes the prompt
        r = self.e.run("--check", str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("check extra persona:migrations: ok", r.stdout)
        self.assertIn("check extra skill:db: ok (project; for lanes", r.stdout)
        prompt.write_text("changed\n")
        r = self.e.run("--check", str(self.repo), adapt=True)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("check extra persona:migrations: changed", r.stdout)
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 1, r.stdout)  # what it could not do makes refresh fail, after the rest
        self.assertIn("extras: failed", r.stdout)

    def test_no_extras_changes_nothing_for_an_existing_project(self):
        exclude = self.repo / ".git" / "info" / "exclude"
        before = exclude.read_text()
        coord = (self.repo / ".lane-control" / "coordinator.md").read_text()
        r = self.e.run(str(self.repo), "--refresh", adapt=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("extras:", r.stdout)
        self.assertEqual((self.repo / ".lane-control" / "coordinator.md").read_text(), coord)
        self.assertEqual(exclude.read_text(), before)
        self.assertFalse((self.e.home / ".local" / "state" / "lane-control" / "extras").exists())

    def test_the_onboarding_agents_get_no_mcp_and_no_extras(self):
        from test_project_setup_native import load as load_setup
        m = load_setup()
        for write in (False, True):
            cmd = m.agent_cmd("sonnet", "go", write)
            self.assertIn("--strict-mcp-config", cmd)  # no MCP server, the project's .mcp.json included
            self.assertIn("--restricted", cmd)  # no user, project or local settings files
            tools = cmd[cmd.index("--tools") + 1].split(",")
            self.assertNotIn("Skill", tools)
            self.assertNotIn("Bash", tools)
            self.assertNotIn("--mcp-config", cmd)
            self.assertNotIn("--plugin-dir", cmd)


SB_T = load("lc_sandbox_ext_t", ROOT / "core" / "sandbox.py")


class SandboxPrepareTests(Scratch):
    """SBX-2 task 3: `lane prepare --sandbox`: the lane's own clone of origin's base, resumed untouched, recreated
    from refs/lanes/<lane>, refused when broken or when history holds private data; the CI and selftest gates; the
    generated home files; a reviewer's or check's own clone and folders."""

    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(ROOT / "core"))
        import sandboxprep
        self.SP = sandboxprep
        self.SB = sandboxprep.SB
        self.origin = self.t / "origin.git"
        git("init", "-q", "--bare", "-b", "main", self.origin, cwd=self.t)
        git("remote", "add", "origin", self.origin, cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        git("fetch", "-q", "origin", cwd=self.repo)
        self.base = git("rev-parse", "main", cwd=self.repo)
        git("switch", "-q", "-c", "other", cwd=self.repo)
        (self.repo / "other.txt").write_text("another lane\n")
        git("add", "other.txt", cwd=self.repo)
        git("commit", "-q", "-m", "other", cwd=self.repo)
        self.other = git("rev-parse", "HEAD", cwd=self.repo)
        git("switch", "-q", "main", cwd=self.repo)
        (self.repo / "wip.txt").write_text("unpushed\n")
        git("add", "wip.txt", cwd=self.repo)
        git("commit", "-q", "-m", "owner's unpushed work", cwd=self.repo)  # local main is ahead of origin
        self.wip = git("rev-parse", "HEAD", cwd=self.repo)
        git("update-ref", "refs/owner/x", self.wip, cwd=self.repo)
        cfg = self.home / ".config"
        (cfg / "lane-control").mkdir(parents=True, exist_ok=True)
        self.conf = {"SANDBOX_BWRAP": "/nix/store/a-bubblewrap/bin/bwrap", "SANDBOX_PASTA": "/nix/store/b-passt/bin/pasta",
                     "SANDBOX_NSENTER": "/nix/store/c-util-linux/bin/nsenter", "SANDBOX_NFT": "/nix/store/d-nft/bin/nft",
                     "SANDBOX_TOOL_PATH": "/nix/store/e-git/bin:/nix/store/f-python3/bin"}
        (cfg / "lane-control" / "sandbox.conf").write_text("".join(f"{k}={v}\n" for k, v in self.conf.items()))
        (cfg / "lane-control" / "machine.conf").write_text("SANDBOX_PROJECTS=app\n")
        self.selftest()
        (cfg / "opencode").mkdir(parents=True, exist_ok=True)
        (cfg / "opencode" / "opencode.json").write_text(json.dumps({"model": "gw/chat", "provider": {"gw": {
            "npm": "@ai-sdk/openai-compatible", "options": {"baseURL": "http://192.168.1.20:8317/v1",
                                                            "apiKey": "{env:GATEWAY_KEY}"}}}}))
        (self.home / ".gitconfig").write_text("[user]\n\tname = Owner\n\temail = owner@example.invalid\n")
        self.ci = {"workflow": {"default_workflow_permissions": "read"}, "secrets": {"total_count": 0},
                   "organization-secrets": {"total_count": 0}, "runners": {"total_count": 0}}

        def gh_json(path):
            leaf = path.rsplit("/", 1)[-1]
            if leaf not in self.ci or self.ci[leaf] is None:
                raise self.SB.SandboxError("no")
            return self.ci[leaf]
        p = mock.patch.object(self.SP, "gh_json", gh_json)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(self.SP, "github_slug", lambda root: "owner/app")
        p.start()
        self.addCleanup(p.stop)

    def selftest(self, **over):
        st = {"ok": True, "lane_control": self.SP.lc_version() if hasattr(self, "SP") else None,
              "pins": {k: self.conf[k] for k in self.SP.PIN_KEYS}, **over}
        f = self.SB.selftest_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(st))

    def sprep(self, *extra, role="lanes", lane="app-1"):
        return self.run_le("prepare", self.repo, self.wt, "--sandbox", "--role", role, "--lane", lane, *extra)

    def ok_prep(self, *extra, **kw):
        code, out, err = self.sprep(*extra, **kw)
        self.assertEqual(code, 0, out + err)
        nonce = out.strip().splitlines()[-1]
        self.assertRegex(nonce, r"^[0-9a-f]{32}$")
        return nonce, out

    def ticket(self, nonce):
        for f in self.SB.tickets_dir(self.wt).glob(f"*-{nonce}.json"):
            return json.loads(f.read_text())
        self.fail(f"no ticket {nonce}")

    def tickets(self):
        d = self.SB.tickets_dir(self.wt)
        return sorted(d.glob("*.json")) if d.is_dir() else []

    def no_prep(self, *extra, says="", **kw):
        before = self.tickets()
        code, out, err = self.sprep(*extra, **kw)
        self.assertNotEqual(code, 0, out)
        self.assertIn("prepare: refused", out)
        self.assertIn(says, out)
        self.assertEqual(self.tickets(), before)  # a refusal hands nothing over
        return out

    def clone(self):
        return self.SB.lane_dir("app-1") / "repo"

    def test_fresh_clone_only_base(self):
        nonce, out = self.ok_prep()
        c = self.clone()
        self.assertEqual(git("rev-parse", "HEAD", cwd=c), self.base)  # origin's base, not the owner's local main
        self.assertEqual(git("symbolic-ref", "--short", "HEAD", cwd=c), "lane-1")
        refs = git("for-each-ref", "--format=%(refname)", cwd=c).split()
        self.assertEqual(sorted(refs), ["refs/heads/lane-1", "refs/remotes/origin/main"])
        for sha in (self.other, self.wip):
            r = subprocess.run(["git", "cat-file", "-e", sha], cwd=c, capture_output=True)
            self.assertNotEqual(r.returncode, 0, sha)
        self.assertEqual(git("config", "remote.origin.url", cwd=c), self.SP.NOWHERE)
        q = self.SB.lane_dir("app-1") / "quarantine.git"
        self.assertEqual(git("rev-parse", "refs/heads/main", cwd=q), self.base)
        self.assertEqual((self.SB.lane_dir("app-1") / "base").read_text().strip(), self.base)
        t = self.ticket(nonce)
        self.assertEqual((t["role"], t["clone"], t["worktree"]), ("lane", str(c), str(self.wt)))
        self.assertEqual(t["gateway_key_env"], "GATEWAY_KEY")
        self.assertEqual(t["gateway"], ["192.168.1.20", 8317])
        gen = json.loads(Path(t["home_files"]["opencode.json"]).read_text())
        self.assertEqual(gen["provider"]["gw"]["options"]["apiKey"], "{env:GATEWAY_KEY}")
        self.assertEqual(gen["model"], "gw/chat")
        self.assertNotIn("mcp", gen)
        self.assertIn("name = Owner", Path(t["home_files"]["gitconfig"]).read_text())
        self.assertEqual(Path(t["home_files"]["resolv.conf"]).read_text(), "nameserver 169.254.1.53\n")
        self.clean(self.wt)  # nothing written into the Orca worktree
        self.assertTrue(self.SB.binding_file(self.wt).is_file())

    def test_resume_reuses_clone(self):
        n1, _ = self.ok_prep()
        c = self.clone()
        (c / "lane-file").write_text("work\n")
        git("add", "lane-file", cwd=c)
        git("commit", "-q", "-m", "lane work", cwd=c)
        (c / "dirty").write_text("uncommitted\n")
        head = (c / ".git" / "HEAD").read_bytes()
        stamps = {p: os.stat(p).st_mtime_ns for p in (c, c / ".git", c / ".git" / "HEAD", c / ".git" / "index",
                                                      c / ".git" / "config")}
        real_run = subprocess.run
        ran_in_clone = []

        def spy(cmd, *a, **kw):
            words = [str(x) for x in cmd]
            if any(str(c) in w for w in words) or str(kw.get("cwd") or "").startswith(str(c)):
                ran_in_clone.append(words)
            return real_run(cmd, *a, **kw)
        with mock.patch.object(self.SP.subprocess, "run", spy):
            n2, out = self.ok_prep()
        self.assertNotEqual(n1, n2)
        self.assertIn("resume", out)
        self.assertEqual(ran_in_clone, [])
        self.assertEqual((c / ".git" / "HEAD").read_bytes(), head)
        self.assertEqual({p: os.stat(p).st_mtime_ns for p in stamps}, stamps)
        self.assertEqual((c / "dirty").read_text(), "uncommitted\n")

    def push_lane(self, text="pushed work\n"):
        """What the broker leaves after an accepted push: refs/lanes/app-1 in the shared repo."""
        w = self.t / "pusher"
        git("clone", "-q", "--no-local", self.origin, w, cwd=self.t)
        (w / "lane-file").write_text(text)
        git("add", "lane-file", cwd=w)
        git("commit", "-q", "-m", "lane", cwd=w)
        sha = git("rev-parse", "HEAD", cwd=w)
        git("fetch", "-q", w, f"{sha}:refs/lanes/app-1", cwd=self.repo)
        shutil.rmtree(w)
        return sha

    def test_recreate_from_pushed_branch(self):
        self.ok_prep()
        sha = self.push_lane()
        shutil.rmtree(self.clone())
        nonce, out = self.ok_prep()
        c = self.clone()
        self.assertIn("recreated", out)
        self.assertEqual(git("rev-parse", "HEAD", cwd=c), sha)
        self.assertEqual(git("symbolic-ref", "--short", "HEAD", cwd=c), "lane-1")
        self.assertFalse((c / "lane-file").exists())  # checked out inside, never on the host
        self.assertEqual(self.ticket(nonce)["checkout"], "lane-1")

    def test_broken_clone_refused(self):
        self.ok_prep()
        c = self.clone()
        (c / "keep").write_text("the lane's files\n")
        shutil.rmtree(c / ".git")
        out = self.no_prep(says="--discard")
        self.assertIn("refs/lanes/app-1", out)
        self.assertEqual((c / "keep").read_text(), "the lane's files\n")  # never a silent re-clone over it
        (c / ".git").symlink_to(self.repo / ".git")  # a .git that points elsewhere is not the clone
        self.no_prep(says="no longer a git repo")
        nonce, out = self.ok_prep("--discard")
        self.assertIn("discarded", out)
        self.assertEqual(git("rev-parse", "HEAD", cwd=c), self.base)

    def push_main(self, path, text="x\n", side=False):
        w = self.t / "mainpush"
        git("clone", "-q", "--no-local", self.origin, w, cwd=self.t)
        if side:
            git("switch", "-q", "-c", "side", cwd=w)
        f = w / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        git("add", "-f", path, cwd=w)
        git("commit", "-q", "-m", "add", cwd=w)
        if side:
            git("rm", "-q", path, cwd=w)
            git("commit", "-q", "-m", "remove", cwd=w)
            git("switch", "-q", "main", cwd=w)
            git("merge", "-q", "--no-ff", "--no-edit", "side", cwd=w)
        git("push", "-q", "origin", "main", cwd=w)
        shutil.rmtree(w)
        git("fetch", "-q", "origin", cwd=self.repo)

    def private(self, *paths):
        st = json.loads((self.store / "settings.json").read_text())
        st["private"] = list(paths)
        (self.store / "settings.json").write_text(json.dumps(st))

    def test_history_refusals(self):
        self.private("./fixtures/customers/")
        self.push_main("fixtures/customers/b.csv", side=True)
        out = self.no_prep(says="fixtures/customers/b.csv")
        self.assertFalse(self.clone().exists())
        self.push_main("Fixtures/Customers/a.csv")
        self.no_prep(says="Fixtures/Customers/a.csv")

    def test_history_env_files(self):
        self.push_main(".env.example")
        self.ok_prep()
        shutil.rmtree(self.SB.lane_dir("app-1"))
        self.push_main("sub/.env.local")
        self.no_prep(says="sub/.env.local")

    def test_inline_key_refused(self):
        key = "sk-" + "K3" * 16
        (self.home / ".config" / "opencode" / "opencode.json").write_text(json.dumps({"provider": {"gw": {
            "options": {"baseURL": "https://gw.example.com/v1", "apiKey": key}}}}))
        code, out, err = self.sprep()
        self.assertNotEqual(code, 0)
        self.assertIn("inline apiKey", out)
        self.assertNotIn(key, out + err)
        for f in self.SB.state_root().rglob("*"):
            if f.is_file():
                self.assertNotIn(key, f.read_text(errors="replace"), f)

    def test_tool_list_from_store_only(self):
        (self.repo / ".lane-control").mkdir(exist_ok=True)
        (self.repo / ".lane-control" / "sandbox-tools").write_text("/tmp/evil/bin\n")
        (self.wt / "flake.nix").write_text("{ evil }\n")
        st = json.loads((self.store / "settings.json").read_text())
        st["sandbox_tools"] = ["/nix/store/z-node/bin"]
        (self.store / "settings.json").write_text(json.dumps(st))
        nonce, _ = self.ok_prep()
        self.assertEqual(self.ticket(nonce)["tool_path"],
                         ["/nix/store/e-git/bin", "/nix/store/f-python3/bin", "/nix/store/z-node/bin"])
        st["sandbox_tools"] = [str(self.wt / "bin")]
        (self.store / "settings.json").write_text(json.dumps(st))
        self.no_prep(says="only /nix/store paths")

    def test_reviewer_own_folders(self):
        n1, _ = self.ok_prep()
        self.no_prep(role="reviewer", says="--sha")
        sha = self.push_lane()
        self.no_prep("--sha", self.other, role="reviewer", says="not in the lane's pushed work")
        n2, _ = self.ok_prep("--sha", sha, "--review-write", "app-1-r1.md", role="reviewer")
        n3, _ = self.ok_prep("--sha", sha, role="check")
        lane, rev, chk = self.ticket(n1), self.ticket(n2), self.ticket(n3)
        self.assertEqual((rev["role"], chk["role"]), ("reviewer", "check"))
        self.assertEqual(rev["review_write"], "app-1-r1.md")
        dirs = [{t["clone"], t["ticket_dir"]} for t in (lane, rev, chk)]
        self.assertEqual(len(set.union(*dirs)), 6)
        rc = Path(rev["clone"])
        self.assertEqual((rc / ".git" / "HEAD").read_text().strip(), sha)
        self.assertEqual(rev["checkout"], sha)
        self.assertFalse((rc / "lane-file").exists())

    def test_ci_settings_refused(self):
        for leaf, bad in (("workflow", {"default_workflow_permissions": "write"}), ("secrets", {"total_count": 1}),
                          ("organization-secrets", {"total_count": 2}), ("runners", {"total_count": 1}),
                          ("runners", None)):
            keep = self.ci[leaf]
            self.ci[leaf] = bad
            self.no_prep(says="read-only default workflow token")
            self.ci[leaf] = keep
        self.ok_prep()

    def test_refuses_without_selftest(self):
        self.SB.selftest_file().unlink()
        self.no_prep(says="lane-control sandbox selftest")
        self.selftest(ok=False)
        self.no_prep(says="failed")
        self.selftest(lane_control="0" * 40)
        self.no_prep(says="another lane-control version")
        self.selftest(pins={"SANDBOX_BWRAP": "/nix/store/other/bin/bwrap"})
        self.no_prep(says="other pinned binaries")

    def test_project_must_be_on(self):
        (self.home / ".config" / "lane-control" / "machine.conf").write_text("SANDBOX_PROJECTS=other\n")
        self.no_prep(says="SANDBOX_PROJECTS")

    def test_idle_reader_uses_ticket_data(self):
        adapter = load("lc_oc_adapter_t", ROOT / "core" / "harnesses" / "opencode" / "adapter.py")
        self.assertEqual(adapter.db_path(self.wt), self.home / ".local/share/opencode/opencode.db")  # not sandboxed
        nonce, _ = self.ok_prep()
        t = self.SB.take_ticket(self.wt, "term_1")
        self.assertEqual(adapter.db_path(self.wt), Path(t["ticket_dir"]) / "data" / "opencode.db")
        self.assertEqual(adapter.db_path(), self.home / ".local/share/opencode/opencode.db")

    def test_needs_lane_key(self):
        code, out, err = self.run_le("prepare", self.repo, self.wt, "--sandbox", "--role", "lanes")
        self.assertNotEqual(code, 0)
        self.assertIn("--lane", out)


if __name__ == "__main__":
    unittest.main()
