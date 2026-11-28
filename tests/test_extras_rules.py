"""EXT-1b (lc-yw1g): the riskier extras, agent hooks, deny rules and env values. lane-control never writes an allow
(allow extras are lc-pwdq: review r1 showed none can be made safe yet). Beside any deny rule, and the guard part beside
any agent hook, lane-control writes its own denies (secrets, auth files and OpenCode's credential store, the agents'
settings, --private paths, credential commands), following the XDG folders. On Claude they hold over any allow from
any file (deny first); on OpenCode every deny is also a tool.use policy, which no later rule lifts, and lane-control's
own rules lift nobody's deny, but the floor is advisory there (its grep tool, shell and links never present the path:
lc-mpb5). A problem never takes a deny, the floor or a guard hook out of a folder: only what grants goes. A PreToolUse
hook fails closed (onFailure block, a timeout margin, every runner error). Secrets are refused in env values and in
deny rules, never stored or shown. Everything runs in a scratch HOME (test_extras.Scratch)."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
import json
import os
import subprocess
import sys
import unittest

from test_extras import SECRET, ROOT, X, Scratch, claude_reads, git

R = X.R
RUNNER = ROOT / "core" / "scripts" / "lane-agent-hook"
H, W, M = "/u/me", "/w/lane-1", "/c/app"  # HOME, a lane's worktree, the main checkout
PRIVATE = ["fixtures/customers/*", "data/"]
# Allows from other files (the owner's own settings, a saved approval): the floor holds against every one.
WIDE = ["Bash", "Bash(*)", "Bash(gh *)", "Bash(gh auth *)", "Bash(git *)", "Bash(cat *)", "Bash(printenv *)",
        "Bash(env)", "Read", "Read(*)", "Read(**)", "Read(//**)", "Read(~/**)", "Read(~/.ssh/**)", "Read(**/.env*)",
        "Read(fixtures/**)", "Edit", "Edit(**)", "Edit(.claude/**)", "Edit(~/.claude/**)", "Write(**)"]
FLOOR_READS = [f"{H}/.ssh/id_ed25519", f"{H}/.ssh/sub/key", f"{H}/.aws/credentials", f"{H}/.config/gh/hosts.yml",
               f"{H}/.netrc", f"{H}/.git-credentials", f"{H}/.claude/.credentials.json", f"{H}/.claude.json",
               f"{H}/.local/share/opencode/auth.json", f"{H}/.local/share/opencode/opencode.db",
               f"{H}/.local/share/opencode/opencode.db-wal", f"{W}/.env", f"{W}/api/.env", f"{W}/a/b/.env.local",
               f"{M}/.env", f"{M}/svc/.env.production", f"{W}/fixtures/customers/acme.csv", f"{M}/data/x/y.json"]
FLOOR_EDITS = [f"{H}/.claude/settings.json", f"{H}/.claude/skills/x/SKILL.md", f"{H}/.config/opencode/opencode.json",
               f"{M}/.claude/settings.local.json", f"{H}/.config/lane-control/extras.json", f"{H}/.agents/bin/x"]
FLOOR_COMMANDS = ["gh auth token", "gh auth token --hostname github.com", "gh auth status --show-token",
                  "git credential fill", "printenv", "printenv GITHUB_TOKEN", "env", "export -p", "declare -p",
                  "timeout 5 gh auth token", "FOO=1 printenv", "ls && gh auth token", "echo x | git credential fill"]
DENIES = ["Bash(git push --force *)", "Read(secrets/**)"]


def rel_to(p, folder):
    """OpenCode's read resource: relative inside the session's folder, absolute outside it."""
    return p[len(folder) + 1:] if p.startswith(folder + "/") else p


class FloorProofTests(unittest.TestCase):
    """The floor holds by each harness's own rules (for the resources a call presents), whatever allows other files
    hold; lane-control's own rules hold no allow."""

    def items(self, folder=W, **kw):
        return R.floor(H, folder, M, PRIVATE, [".claude/settings.json"] if folder == W else [], **kw)

    def test_claude_no_allow_from_any_file_reaches_a_floor_path_or_command(self):
        for folder, settings_dir in ((W, W), (M, "/store/extras/coordinator")):  # a lane; the coordinator's --settings
            ours = R.claude_permissions(DENIES, self.items(folder), H)
            self.assertEqual(list(ours), ["deny"])  # lane-control never writes an allow
            for allows in (WIDE, WIDE[::-1]):
                merged = {"allow": allows, "deny": ours["deny"]}
                for p in FLOOR_READS:
                    for tool in ("Read", "Edit", "Write"):
                        with self.subTest(folder=folder, tool=tool, path=p):
                            self.assertEqual(R.claude_decision(merged, tool, p, H, folder, settings_dir), "deny")
                for p in FLOOR_EDITS + ([f"{W}/.claude/settings.json"] if folder == W else []):
                    with self.subTest(folder=folder, edit=p):
                        self.assertEqual(R.claude_decision(merged, "Edit", p, H, folder, settings_dir), "deny")
                for c in FLOOR_COMMANDS + ["git push --force origin main"]:
                    with self.subTest(folder=folder, command=c):
                        self.assertEqual(R.claude_decision(merged, "Bash", c, H, folder, settings_dir), "deny")
                memory = f"{H}/.claude/projects/-w-lane-1/memory/notes.md"  # Claude's own memory stays writable
                self.assertEqual(R.claude_decision(merged, "Edit", memory, H, folder, settings_dir), "allow")
        merged = {"allow": ["Read(src/**)"], "deny": R.claude_permissions([], self.items(), H)["deny"]}
        self.assertEqual(R.claude_decision(merged, "Read", f"{W}/src/app.ts", H, W), "allow")
        self.assertEqual(R.claude_decision(merged, "Read", f"{W}/src/.env", H, W), "deny")

    def test_opencode_no_later_rule_or_approval_lifts_a_floor_deny(self):
        for folder in (W, M):
            perms, policies = R.opencode_permissions(DENIES, self.items(folder), H, folder)
            self.assertEqual({p["effect"] for p in perms + policies}, {"deny"})  # never an allow
            later = [{"action": "*", "resource": "*", "effect": "allow"}]  # a later file's rule, a saved approval
            for rules in (perms, later + perms, perms + later):
                for p in FLOOR_READS:
                    for act in ("read", "edit"):
                        with self.subTest(folder=folder, act=act, path=p):
                            self.assertEqual(R.opencode_decision(rules, policies, act, rel_to(p, folder)), "deny")
                for p in FLOOR_EDITS + ([f"{W}/.claude/settings.json"] if folder == W else []):
                    with self.subTest(folder=folder, edit=p):
                        self.assertEqual(R.opencode_decision(rules, policies, "edit", rel_to(p, folder)), "deny")
                for c in FLOOR_COMMANDS + ["git push --force origin main"]:
                    with self.subTest(folder=folder, command=c):
                        self.assertEqual(R.opencode_decision(rules, policies, "shell", c), "deny")
        self.assertEqual(R.opencode_decision(perms, policies, "read", "src/app.ts"), "allow")

    def test_b3_lane_controls_rules_lift_no_one_elses_deny(self):
        perms, policies = R.opencode_permissions(["Bash(rm *)"], self.items(), H, W)
        owners_global = [{"action": "shell", "resource": "git push *", "effect": "deny"}]
        plan_agent = [{"action": "edit", "resource": "*", "effect": "deny"}]
        for before in (owners_global, plan_agent):  # 2.2.1: built-in agent rules, then global, then project files
            rules = before + perms
            self.assertEqual(R.opencode_decision(rules, policies, "shell", "git push origin main"),
                             "deny" if before is owners_global else "allow")
            self.assertEqual(R.opencode_decision(rules, policies, "edit", "src/app.ts"),
                             "deny" if before is plan_agent else "allow")

    def test_the_floor_follows_the_xdg_folders_and_claudes_config_folder(self):
        dirs = {"config": "/x/cfg", "data": "/x/data", "state": "/x/state", "claude": "/x/claude"}
        items = R.floor(H, W, M, [], [], dirs)
        perms = R.claude_permissions([], items, H)
        for path, tool in (("/x/cfg/lane-control/extras.json", "Edit"), ("/x/state/lane-control/hooks/x", "Edit"),
                           ("/x/data/opencode/opencode.db", "Read"), ("/x/data/opencode/auth.json", "Read"),
                           ("/x/claude/settings.json", "Edit"), ("/x/claude/.credentials.json", "Read"),
                           (f"{H}/.config/lane-control/extras.json", "Edit")):
            with self.subTest(path=path):
                self.assertEqual(R.claude_decision({"allow": ["Read", "Edit"], **perms}, tool, path, H, W), "deny")
        _, policies = R.opencode_permissions([], items, H, W)
        self.assertEqual(R.opencode_decision([], policies, "read", "/x/data/opencode/opencode.db-wal"), "deny")

    def test_command_patterns_match_as_each_harness_matches(self):
        self.assertTrue(R.bash_matches("ls *", "ls"))
        self.assertFalse(R.bash_matches("ls *", "lsof"))
        self.assertTrue(R.bash_matches("ls*", "lsof"))
        self.assertTrue(R.bash_matches("git:*", "git log"))
        deny = {"deny": ["Bash(git push --force *)"]}
        self.assertEqual(R.claude_decision(deny, "Bash", "cd x && git push --force", H, W), "deny")

    def test_rules_translate_to_opencode_where_they_map(self):
        self.assertEqual(R.to_opencode("Bash(npm run test:*)", H, W), [("shell", "npm run test *")])
        self.assertEqual(R.to_opencode("WebFetch(domain:example.com)", H, W),
                         [("webfetch", "*://example.com"), ("webfetch", "*://example.com/*")])
        self.assertEqual(R.to_opencode("mcp__github__get_issue", H, W), [("github_get_issue", "*")])
        self.assertIn(("read", f"{H}/.ssh/*"), R.to_opencode("Read(~/.ssh/**)", H, W))
        self.assertIn(("edit", "*/.env"), R.to_opencode("Edit(.env)", H, W))
        self.assertEqual(R.to_opencode("Agent(model:opus)", H, W), [])


class Base(Scratch):
    def settings(self, folder=None):
        return json.loads(((folder or self.wt) / ".claude" / "settings.json").read_text())

    def oc(self, folder=None):
        return json.loads(((folder or self.wt) / ".opencode" / "opencode.json").read_text())

    def private(self, globs):
        st = json.loads((self.store / "settings.json").read_text())
        (self.store / "settings.json").write_text(json.dumps({**st, "private": globs}))

    def hand_write(self, entry):
        """An extra written into the store by hand (or by an older lane-control): apply checks it again."""
        f = X.project_file(self.store)
        data = json.loads(f.read_text()) if f.exists() else {"version": 1, "extras": [], "off": []}
        data["extras"].append(entry)
        f.write_text(json.dumps(data))


class PermissionAddTests(Base):
    def test_allow_is_not_an_extra_in_this_pr(self):
        for rule in ("Bash(npm run test *)", "Read(src/**)", "Bash", "WebFetch(domain:example.com)"):
            with self.subTest(rule=rule):
                self.refused("project", "add", self.repo, "--permission", "allow", rule, says="lc-pwdq")
        self.refused("machine", "add", "--permission", "allow", "Read(docs/**)", says="deny rules only")
        self.assertEqual(self.stored(), [])

    def test_a_deny_is_taken_and_what_it_does_not_hold_is_said(self):
        out = self.add("--permission", "deny", "Bash(git push --force *)")
        self.assertIn("advisory", out)
        out = self.add("--permission", "deny", "Agent(model:opus)")
        self.assertIn("Claude only", out)
        ids = [X.ident(e) for e in self.stored()]
        self.assertEqual(ids, ["permission:deny:Bash(git push --force *)", "permission:deny:Agent(model:opus)"])
        self.ok("project", "remove", self.repo, "permission:deny:Agent(model:opus)")
        self.assertEqual(len(self.stored()), 1)

    def test_b5_a_deny_rule_that_carries_a_secret_is_refused_and_never_shown(self):
        for rule in ("Bash(mysql -phunter2canary db)", "Bash(sshpass -p hunter2canary ssh h)",
                     "Bash(curl --token hunter2canary https://h)", "Bash(PGPASSWORD=hunter2canary psql *)",
                     "Bash(gh login --with-token hunter2canary)", f"Bash(curl -H 'Authorization: Bearer {SECRET}' x)",
                     f"WebFetch(domain:{SECRET})", "Bash(git clone https://u:hunter2pass@host/r.git)"):
            with self.subTest(rule=rule.split("(")[1][:12]):
                err = self.refused("project", "add", self.repo, "--permission", "deny", rule, says="refused")
                self.assertNotIn("hunter2canary", err)
        self.add("--permission", "deny", "Bash(curl --token *)")  # a wildcard is no secret
        self.assertEqual([e["rule"] for e in self.stored()], ["Bash(curl --token *)"])


class PermissionApplyTests(Base):
    def test_prepare_writes_the_owners_denies_beside_the_floor_and_never_an_allow(self):
        self.private(["fixtures/customers/*"])
        self.add("--permission", "deny", "Bash(git push --force *)", "--for", "lanes")
        self.assertEqual(self.prepare()[0], 0)
        perms = self.settings()["permissions"]
        self.assertEqual(list(perms), ["deny"])
        for rule in ("Bash(git push --force *)", "Read(~/.ssh/**)", "Read(~/.claude.json)",
                     "Read(~/.local/share/opencode/opencode.db*)", "Bash(gh auth token *)",
                     f"Edit(/{self.wt}/.claude/settings.json)", "Read(//**/fixtures/customers/*)",
                     f"Read(/{self.repo}/fixtures/customers/*)", f"Edit(/{self.repo}/.claude/settings.local.json)"):
            self.assertIn(rule, perms["deny"])
        home, wt = os.environ["HOME"], str(self.wt)
        merged = {"allow": ["Read", "Edit", "Bash"], **perms}
        self.assertEqual(R.claude_decision(merged, "Read", f"{home}/.ssh/id_rsa", home, wt), "deny")
        self.assertEqual(R.claude_decision(merged, "Read", f"{wt}/fixtures/customers/a.csv", home, wt), "deny")
        self.assertEqual(R.claude_decision(merged, "Edit", f"{wt}/.claude/settings.json", home, wt), "deny")
        oc = self.oc()
        self.assertEqual({p["effect"] for p in oc["permissions"] + oc["policies"]}, {"deny"})
        resources = {p["resource"] for p in oc["policies"]}
        self.assertTrue({"shell:git push --force *", "shell:gh auth token *", f"read:{home}/.ssh/*",
                         "read:fixtures/customers/*", "edit:.claude/settings.json",
                         f"read:{home}/.local/share/opencode/opencode.db*"} <= resources)
        self.clean(self.wt)
        self.assertNotIn("git push --force", claude_reads(self.repo, self.repo)["text"])  # the lane's only

    def test_the_coordinators_floor_names_absolute_paths_and_stays_out_of_lanes(self):
        self.private(["fixtures/customers/*"])
        self.add("--permission", "deny", "Bash(make deploy *)", "--for", "coordinator")
        self.add("--env", "APP_MODE=coordinating", "--for", "coordinator")
        lines, problems = self.coordinator()
        self.assertEqual(problems, [])
        launch = X.launch_dir(self.store)
        s = json.loads((launch / "settings.json").read_text())
        self.assertEqual(s["env"], {"APP_MODE": "coordinating"})
        self.assertIn(f"Read(/{self.repo}/fixtures/customers/*)", s["permissions"]["deny"])
        home = os.environ["HOME"]
        self.assertEqual(R.claude_decision({"allow": ["Read"], **s["permissions"]}, "Read",
                                           f"{self.repo}/fixtures/customers/a.csv", home, str(self.repo),
                                           str(launch)), "deny")
        self.assertIn(f"read:{self.repo}/fixtures/customers/*",
                      {p["resource"] for p in self.oc(self.repo)["policies"]})
        self.assertIn("--settings", X.launch_args(self.store))
        self.assertEqual(self.prepare()[0], 0)
        lane = claude_reads(self.wt, self.repo)
        self.assertNotIn("coordinating", lane["text"])
        self.assertNotIn("make deploy", lane["text"])
        self.assertFalse((self.wt / ".opencode").exists())

    def test_a_machine_deny_and_a_project_deny_both_hold(self):
        self.ok("machine", "add", "--permission", "deny", "Bash(git push --force *)", "--for", "lanes")
        self.add("--permission", "deny", "Bash(rm -rf *)", "--for", "lanes")
        self.assertEqual(self.prepare()[0], 0)
        oc, perms, home = self.oc(), self.settings()["permissions"], os.environ["HOME"]
        for cmd in ("git push --force origin main", "rm -rf build"):
            self.assertEqual(R.opencode_decision(oc["permissions"], oc["policies"], "shell", cmd), "deny")
            self.assertEqual(R.claude_decision({"allow": ["Bash"], **perms}, "Bash", cmd, home, str(self.wt)), "deny")

    def test_an_allow_left_in_the_store_is_refused_and_the_denies_stay(self):
        self.add("--permission", "deny", "Bash(git push --force *)", "--for", "lanes")
        self.assertEqual(self.prepare()[0], 0)
        self.hand_write({"kind": "permission", "effect": "allow", "rule": "Bash(npm run test *)",
                         "name": "allow:Bash(npm run test *)", "for": ["lanes"]})
        code, out, _ = self.prepare()
        self.assertEqual(code, 1)
        self.assertIn("lc-pwdq", out)
        perms = self.settings()["permissions"]
        self.assertNotIn("allow", perms)
        self.assertIn("Bash(git push --force *)", perms["deny"])
        rows = {r["id"]: r for r in X.check(self.store)}
        self.assertEqual(rows["permission:allow:Bash(npm run test *)"]["state"], "failing")


class FailedApplyTests(Base):
    """B4: a problem never takes a deny, the floor or a guard hook out of a live folder; only what grants goes."""

    def test_a_changed_hook_keeps_every_protection_in_place(self):
        guard, post = self.script("guard.sh", "#!/bin/sh\nexit 0\n"), self.script("after.sh", "#!/bin/sh\nexit 0\n")
        self.add("--permission", "deny", "Bash(git push --force *)", "--for", "lanes")
        self.add("--agent-hook", "PreToolUse", "--for", "lanes", "--", guard)
        self.add("--agent-hook", "PostToolUse", "--for", "lanes", "--", post)
        self.add("--mcp", "local", "--for", "lanes", "--", self.server)
        self.add("--env", "APP_MODE=fast", "--for", "lanes")
        self.assertEqual(self.prepare()[0], 0)
        self.assertTrue((self.wt / ".mcp.json").exists())
        post.write_text("#!/bin/sh\nexit 0 # changed\n")
        code, out, _ = self.prepare()
        self.assertEqual(code, 1)
        self.assertIn("keeps lane-control's protections", out)
        s = self.settings()
        self.assertIn("Bash(git push --force *)", s["permissions"]["deny"])
        self.assertIn("Read(~/.ssh/**)", s["permissions"]["deny"])
        self.assertEqual(len(s["hooks"]["PreToolUse"]), 1)  # the guard stays
        self.assertEqual(len(s["hooks"]["PostToolUse"]), 1)  # the changed one stays too: its runner refuses it
        self.assertNotIn("env", s)
        self.assertNotIn("enabledMcpjsonServers", s)
        self.assertFalse((self.wt / ".mcp.json").exists())  # what grants goes
        self.assertIn("shell:git push --force *", {p["resource"] for p in self.oc()["policies"]})
        js = (self.wt / X.OC_PLUGIN).read_text()
        self.assertIn("pretooluse-guard", js)
        self.assertNotIn("APP_MODE", js)
        self.assertEqual(self.coordinator()[1], [])  # the coordinator has none of these

    def test_a_foreign_file_is_left_as_it_is_and_the_denies_stay(self):
        self.add("--permission", "deny", "Bash(git push --force *)", "--for", "lanes")
        self.add("--mcp", "local", "--for", "lanes", "--", self.server)
        self.assertEqual(self.prepare()[0], 0)
        (self.wt / ".mcp.json").write_text('{"mcpServers": {}}\n')  # someone else's now
        code, out, _ = self.prepare()
        self.assertEqual(code, 1)
        self.assertIn("not lane-control's", out)
        self.assertEqual((self.wt / ".mcp.json").read_text(), '{"mcpServers": {}}\n')  # left exactly as it is
        self.assertIn("Bash(git push --force *)", self.settings()["permissions"]["deny"])

    def test_the_coordinators_launch_keeps_its_protections_on_a_problem(self):
        guard = self.script("guard.sh", "#!/bin/sh\nexit 0\n")
        self.add("--permission", "deny", "Bash(make deploy *)", "--for", "coordinator")
        self.add("--agent-hook", "PreToolUse", "--for", "coordinator", "--", guard)
        self.add("--env", "APP_MODE=x", "--for", "coordinator")
        self.assertEqual(self.coordinator()[1], [])
        guard.write_text("#!/bin/sh\nexit 0 # changed\n")
        lines, problems = self.coordinator()
        self.assertTrue(problems)
        s = json.loads((X.launch_dir(self.store) / "settings.json").read_text())
        self.assertIn("Bash(make deploy *)", s["permissions"]["deny"])
        self.assertIn("PreToolUse", s["hooks"])
        self.assertNotIn("env", s)
        self.assertIn("--settings", X.launch_args(self.store))


class EnvTests(Base):
    def test_secret_and_reserved_names_and_secret_values_are_refused_and_never_kept(self):
        for arg in ("GITHUB_TOKEN=abc", "API_KEY=abc", "DB_PASSWORD=x", "LANE_ROLE=lanes", "ORCA_TERMINAL_HANDLE=x",
                    "HOME=/tmp", "PATH=/tmp", "XDG_CONFIG_HOME=/tmp", "CLAUDE_CONFIG_DIR=/tmp", "GIT_DIR=/tmp",
                    "LD_PRELOAD=/tmp/x.so", "ANTHROPIC_BASE_URL=http://x", "lower=x",
                    # m1: names that redirect traffic or run code
                    "HTTPS_PROXY=http://p:3128", "HTTP_PROXY=http://p", "ALL_PROXY=socks5://p", "NO_PROXY=x",
                    "NODE_TLS_REJECT_UNAUTHORIZED=0", "NODE_EXTRA_CA_CERTS=/x", "NODE_OPTIONS=--require=/x",
                    "OPENAI_BASE_URL=http://x", "CODEX_HOME=/x", "GH_HOST=x", "GH_CONFIG_DIR=/x", "DOCKER_HOST=x",
                    "NPM_CONFIG_SCRIPT_SHELL=/x", "MAKEFLAGS=-j", "PERL5OPT=-Mx", "RUBYOPT=-rx", "PYTHONPATH=/x",
                    "PYTHONSTARTUP=/x", "JAVA_TOOL_OPTIONS=-x", "PAGER=/x", "APP_PRELOAD=/x", "FOO_BASE_URL=http://x",
                    # B5: secret values
                    f"APP_SETTING={SECRET}", "DATABASE_URL=postgres://u:hunter2pass@h/db",
                    "APP_HEADER=Bearer abcdefghijklmnop", "APP_ID=" + "aB3" * 14, "APP_MODE=line\nbreak",
                    "DB_CONN=host=db user=app password=hunter2canary", "CONN=Server=db;Pwd=hunter2canary",
                    "APP_FLAGS=--token hunter2canary", "APP_ARGS=--password=hunter2canary",
                    'APP_JSON={"api_key": "hunter2canary"}', "APP_URL=https://h/x?X-Amz-Signature=abc",
                    "APP_STRIPE=rk_live_abcdefghijklmnop12"):
            with self.subTest(arg=arg.split("=")[0]):
                err = self.refused("project", "add", self.repo, "--env", arg, says="refused")
                self.assertNotIn("hunter2canary", err)
        self.assertEqual(self.stored(), [])

    def test_a_plain_value_reaches_both_harnesses_for_its_role_only(self):
        self.add("--env", "APP_MODE=fast", "--for", "lanes")
        self.add("--env", "APP_FLAGS=--token ${APP_TOKEN} --verbose", "--for", "lanes")  # a reference is no secret
        self.assertIn("env:APP_MODE", self.ok("project", "list", self.repo))
        self.assertEqual(self.prepare()[0], 0)
        self.assertEqual(self.settings()["env"]["APP_MODE"], "fast")
        self.assertNotIn("permissions", self.settings())  # env alone brings no floor
        js = (self.wt / X.OC_PLUGIN).read_text()
        self.assertIn('"APP_MODE": "fast"', js)
        self.assertIn('ctx.shell.hook("create.before"', js)
        self.assertEqual(self.coordinator()[1], [])
        self.assertFalse((X.launch_dir(self.store) / "settings.json").exists())
        self.clean(self.wt)

    def test_mcp_env_names_are_still_names(self):
        self.add("--mcp", "github", "--env", "GITHUB_TOKEN", "--", "npx", "-y", "@modelcontextprotocol/server-github")
        self.refused("project", "add", self.repo, "--env", "A=1", "--env", "B=2", says="exactly one kind")


class AgentHookTests(Base):
    def hook_script(self, name="guard.sh", body="#!/bin/sh\nexit 0\n"):
        return self.script(name, body)

    def runner(self, event="PreToolUse", name=None, stdin="{}", level="project", extra_env=None, harness=None):
        env = {**self.env, **(extra_env or {})}
        args = [sys.executable, "-I", str(RUNNER), "run", str(self.repo), level, name, event]
        return subprocess.run(args + (["--harness", harness] if harness else []), input=stdin, env=env,
                              capture_output=True, text=True, cwd=self.wt, timeout=60)

    def plugin_hooks(self, folder=None):
        js = ((folder or self.wt) / X.OC_PLUGIN).read_text()
        return json.loads(js.split("const HOOKS = ", 1)[1].split(";\n", 1)[0])

    def test_the_session_runs_lane_controls_runner_and_a_guard_fails_closed(self):
        s = self.hook_script()
        out = self.add("--agent-hook", "PreToolUse", "--matcher", "Bash|Edit", "--timeout", "20", "--for", "lanes",
                       "--", s)
        self.assertIn("fails closed", out)
        e = [x for x in self.stored() if x["kind"] == "agent-hook"][0]
        self.assertEqual((e["name"], e["sha256"], e["matcher"]), ("pretooluse-guard", X.sha256(s), "Bash|Edit"))
        self.assertEqual(self.prepare()[0], 0)
        h = self.settings()["hooks"]["PreToolUse"][0]
        self.assertEqual(h["matcher"], "Bash|Edit")
        cmd = h["hooks"][0]
        self.assertEqual(cmd["onFailure"], "block")  # M1: a timeout or a runner that cannot start blocks
        self.assertEqual(cmd["timeout"], 20 + R.HOOK_MARGIN)  # past the runner's own timeout and its stop grace
        self.assertIn("lane-agent-hook run", cmd["command"])
        self.assertNotIn(str(s), cmd["command"])
        self.assertEqual([(x["event"], x["tools"]) for x in self.plugin_hooks()],
                         [("PreToolUse", ["shell", "edit", "patch"])])
        self.assertIn("if (r.code !== 0)", (self.wt / X.OC_PLUGIN).read_text())

    def test_m2_matchers_name_the_opencode_tools_that_really_run(self):
        out = self.add("--agent-hook", "PreToolUse", "--matcher", "Agent|Write", "--name", "a", "--", self.hook_script())
        self.assertIn("subagent", out)
        out = self.add("--agent-hook", "PreToolUse", "--matcher", "NotebookEdit", "--name", "b", "--",
                       self.hook_script("b.sh"))
        self.assertIn("Claude only", out)
        self.assertEqual(self.prepare()[0], 0)
        self.assertEqual([(x["id"], x["tools"]) for x in self.plugin_hooks()],
                         [("agent-hook:project:a", ["subagent", "write", "patch"])])
        self.assertEqual(len(self.settings()["hooks"]["PreToolUse"]), 2)

    def test_m2_minor_a_guard_alone_protects_its_own_entry(self):
        self.add("--agent-hook", "PreToolUse", "--for", "lanes", "--", self.hook_script())
        self.assertEqual(self.prepare()[0], 0)
        deny = self.settings()["permissions"]["deny"]
        self.assertIn(f"Edit(/{self.wt}/.claude/settings.json)", deny)
        self.assertIn(f"Edit(/{self.wt}/{X.OC_PLUGIN})", deny)
        self.assertIn("Edit(~/.config/lane-control/**)", deny)
        self.assertNotIn("Read(~/.ssh/**)", deny)  # the guard part only: no read denies without a deny rule
        self.assertIn(f"edit:{X.OC_PLUGIN}", {p["resource"] for p in self.oc()["policies"]})

    def test_session_start_and_stop_are_claude_only_and_said(self):
        out = self.add("--agent-hook", "Stop", "--", self.hook_script("notify.sh"))
        self.assertIn("Claude only", out)
        self.refused("project", "add", self.repo, "--agent-hook", "Stop", "--matcher", "Bash", "--",
                     self.hook_script("n2.sh"), says="PreToolUse and PostToolUse only")
        self.assertEqual(self.prepare()[0], 0)
        stop = self.settings()["hooks"]["Stop"][0]["hooks"][0]
        self.assertNotIn("onFailure", stop)
        self.assertFalse((self.wt / X.OC_PLUGIN).exists())

    def test_the_owners_script_only_never_from_a_repo_never_with_arguments(self):
        inside = self.wt / "hook.sh"
        inside.write_text("#!/bin/sh\nexit 0\n")
        inside.chmod(0o755)
        self.refused("project", "add", self.repo, "--agent-hook", "PreToolUse", "--", inside, says="inside the project")
        self.refused("project", "add", self.repo, "--agent-hook", "PreToolUse", "--", self.hook_script(), "--x",
                     says="no arguments")
        self.refused("project", "add", self.repo, "--agent-hook", "Notification", "--", self.hook_script(),
                     says="--agent-hook takes")
        self.refused("project", "add", self.repo, "--agent-hook", "PreToolUse", "--matcher", "Bash.*", "--",
                     self.hook_script(), says="no regex")

    def test_the_runner_runs_a_checked_copy_with_the_input_and_no_secret(self):
        s = self.hook_script("probe.sh", '#!/bin/sh\necho "self=$0"\nenv\necho "in=$(cat)"\nexit 3\n')
        self.add("--agent-hook", "PostToolUse", "--", s)
        r = self.runner("PostToolUse", "posttooluse-probe", stdin='{"tool_name": "Bash"}',
                        extra_env={"GITHUB_TOKEN": SECRET, "ANTHROPIC_API_KEY": SECRET,
                                   "CLAUDE_PROJECT_DIR": str(self.wt)})
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn('in={"tool_name": "Bash"}', r.stdout)
        self.assertIn("LANE_AGENT_HOOK_EVENT=PostToolUse", r.stdout)
        self.assertIn(f"CLAUDE_PROJECT_DIR={self.wt}", r.stdout)
        self.assertNotIn(SECRET, r.stdout + r.stderr)
        self.assertNotIn(f"self={s}", r.stdout)  # a private copy, never the owner's file itself
        runs = X.state_home() / "lane-control" / "hooks" / "runs"  # hooks.runs_dir: the copy is gone after
        self.assertFalse(any(runs.glob("agent-*")))

    def test_m1_m4_a_timeout_or_any_runner_error_blocks_a_guard(self):
        self.add("--agent-hook", "PreToolUse", "--timeout", "1", "--name", "slow", "--",
                 self.hook_script("slow.sh", "#!/bin/sh\nsleep 20\n"))
        r = self.runner("PreToolUse", "slow")
        self.assertEqual(r.returncode, 2)
        self.assertIn("timeout", r.stderr)
        f = X.project_file(self.store)
        data = json.loads(f.read_text())
        data["extras"][0]["timeout"] = "soon"  # a hand-edited store
        f.write_text(json.dumps(data))
        self.assertEqual(self.runner("PreToolUse", "slow").returncode, 2)
        f.write_text("{not json")
        r = self.runner("PreToolUse", "slow")
        self.assertEqual(r.returncode, 2)
        self.assertIn("refused", r.stderr)
        self.assertEqual(self.runner("PostToolUse", "slow").returncode, 1)  # the other events report, never block

    def test_a_changed_hook_is_flagged_refused_at_apply_and_blocks_at_run(self):
        s = self.hook_script()
        post = self.hook_script("after.sh")
        self.add("--agent-hook", "PreToolUse", "--for", "lanes", "--", s)
        self.add("--agent-hook", "PostToolUse", "--for", "lanes", "--", post)
        self.assertEqual(self.prepare()[0], 0)
        s.write_text("#!/bin/sh\necho changed\nexit 0\n")
        post.write_text("#!/bin/sh\nexit 0 # changed\n")
        rows = {r["id"]: r for r in X.check(self.store)}
        self.assertEqual(rows["agent-hook:project:pretooluse-guard"]["state"], "changed")
        self.assertIn("add it again", rows["agent-hook:project:pretooluse-guard"]["fix"])
        code, out, _ = self.prepare()
        self.assertEqual(code, 1)
        self.assertIn("never dropped silently", out)
        self.assertIn("PreToolUse", self.settings()["hooks"])  # B4: kept, and it refuses to run
        r = self.runner("PreToolUse", "pretooluse-guard")
        self.assertEqual(r.returncode, 2)  # fail closed: the call is blocked
        self.assertIn("refused", r.stderr)
        self.assertNotIn("changed\n", r.stdout)
        self.assertEqual(self.runner("PostToolUse", "posttooluse-after").returncode, 1)

    def test_a_removed_hook_runs_nothing_and_refresh_takes_it_out(self):
        self.add("--agent-hook", "PreToolUse", "--for", "lanes", "--", self.hook_script())
        self.assertEqual(self.prepare()[0], 0)
        self.ok("project", "remove", self.repo, "agent-hook:pretooluse-guard")
        r = self.runner("PreToolUse", "pretooluse-guard")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no such extra", r.stderr)
        for wt, rec in X.recorded_worktrees(self.repo):
            X.apply_folder(wt, X.prepare_entries(X.effective(self.store), rec["scopes"]), rec["scopes"],
                           project=self.repo)
        self.assertFalse((self.wt / ".claude" / "settings.json").exists())

    def test_a_git_pinned_hook_runs_from_its_pinned_commit(self):
        d = self.t / "upstream" / "hooks"
        d.mkdir(parents=True)
        (d / "guard.sh").write_text("#!/bin/sh\necho pinned-guard\nexit 0\n")
        (d / "guard.sh").chmod(0o755)
        git("init", "-q", "-b", "main", cwd=d)
        git("add", "-A", cwd=d)
        git("commit", "-q", "-m", "v1", cwd=d)
        sha = git("rev-parse", "HEAD", cwd=d)
        self.add("--agent-hook", "PreToolUse", "--", f"file://{d}@{sha}:guard.sh")
        (d / "guard.sh").write_text("#!/bin/sh\necho moved-on\nexit 0\n")  # the repo moves on; the pin does not
        r = self.runner("PreToolUse", "pretooluse-guard")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pinned-guard", r.stdout)

    def test_the_machines_and_the_projects_both_run(self):
        self.ok("machine", "add", "--agent-hook", "PreToolUse", "--name", "guard", "--", self.hook_script("m.sh"))
        self.add("--agent-hook", "PreToolUse", "--name", "guard", "--", self.hook_script("p.sh"))
        self.assertEqual(self.prepare()[0], 0)
        cmds = [h["hooks"][0]["command"] for h in self.settings()["hooks"]["PreToolUse"]]
        self.assertEqual(len(cmds), 2)
        self.assertTrue(any(" machine guard " in c for c in cmds) and any(" project guard " in c for c in cmds))
        self.assertEqual(self.runner("PreToolUse", "guard", level="machine").returncode, 0)


if __name__ == "__main__":
    unittest.main()
