"""EXT-1b (lc-yw1g): the riskier extras, agent hooks, permission rules and env values. lane-control's own denies
(secrets, auth files, the agents' settings, --private paths, credential commands) are a floor no allow can reach on
Claude (deny first) and on OpenCode (the last match wins, and tool.use policies only deny): proved per harness against
the rules each one evaluates, with wildcards, both add orders and machine/project mixes. An allow whose shape reaches
the floor another way (a shell, an interpreter, a file reader, a path out of the folder, a --private overlap) is
refused at add and again at apply. An agent hook is the owner's pinned script, run by lane-control's runner from a
checked copy with no secret in its environment; a changed one is flagged, refused at apply and blocks at run, never
dropped. An env value is never a secret, never a name lane-control or the harness runs on. Everything runs in a
scratch HOME (test_extras.Scratch)."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from test_extras import LE, SECRET, ROOT, X, Scratch, claude_reads, git

R = X.R
RUNNER = ROOT / "core" / "scripts" / "lane-agent-hook"
H, W, M = "/u/me", "/w/lane-1", "/c/app"  # HOME, a lane's worktree, the main checkout
PRIVATE = ["fixtures/customers/*", "data/"]
# Every allow add refuses, and more: the proofs show the floor holds even if one got through.
WIDE = ["Bash", "Bash(*)", "Bash(gh *)", "Bash(gh auth *)", "Bash(git *)", "Bash(cat *)", "Bash(printenv *)",
        "Bash(env)", "Read", "Read(*)", "Read(**)", "Read(//**)", "Read(~/**)", "Read(~/.ssh/**)", "Read(**/.env*)",
        "Read(fixtures/**)", "Edit", "Edit(**)", "Edit(.claude/**)", "Edit(~/.claude/**)", "Write(**)"]
FLOOR_READS = [f"{H}/.ssh/id_ed25519", f"{H}/.ssh/sub/key", f"{H}/.aws/credentials", f"{H}/.config/gh/hosts.yml",
               f"{H}/.netrc", f"{H}/.git-credentials", f"{H}/.claude/.credentials.json",
               f"{H}/.local/share/opencode/auth.json", f"{W}/.env", f"{W}/api/.env", f"{W}/a/b/.env.local",
               f"{M}/.env", f"{M}/svc/.env.production", f"{W}/fixtures/customers/acme.csv", f"{M}/data/x/y.json"]
FLOOR_EDITS = [f"{H}/.claude/settings.json", f"{H}/.claude/skills/x/SKILL.md", f"{H}/.claude.json", f"{H}/.config/opencode/opencode.json", f"{M}/.claude/settings.local.json",
               f"{H}/.config/lane-control/extras.json", f"{H}/.agents/bin/x"]
FLOOR_COMMANDS = ["gh auth token", "gh auth token --hostname github.com", "gh auth status --show-token",
                  "git credential fill", "printenv", "printenv GITHUB_TOKEN", "env", "timeout 5 gh auth token",
                  "FOO=1 printenv", "ls && gh auth token", "echo x | git credential fill"]


def oc_path(p):
    """OpenCode's read resource: relative inside the session's folder, absolute outside it."""
    return p[len(W) + 1:] if p.startswith(W + "/") else p


class FloorProofTests(unittest.TestCase):
    """The floor holds by each harness's own rules, whatever allows sit beside it."""

    def items(self, folder=W):
        return R.floor(H, folder, M, PRIVATE, [".claude/settings.json"] if folder == W else [])

    def test_claude_no_allow_reaches_a_floor_path_or_command(self):
        for folder, settings_dir in ((W, W), (M, "/store/extras/coordinator")):  # a lane; the coordinator's --settings
            perms = R.claude_permissions(WIDE, [], self.items(folder), H)
            for order in (perms, {"allow": perms["allow"][::-1], "deny": perms["deny"][::-1]}):
                for p in FLOOR_READS:
                    for tool in ("Read", "Edit", "Write"):
                        with self.subTest(folder=folder, tool=tool, path=p):
                            self.assertEqual(R.claude_decision(order, tool, p, H, folder, settings_dir), "deny")
                for p in FLOOR_EDITS + ([f"{W}/.claude/settings.json"] if folder == W else []):
                    with self.subTest(folder=folder, edit=p):
                        self.assertEqual(R.claude_decision(order, "Edit", p, H, folder, settings_dir), "deny")
                memory = f"{H}/.claude/projects/-w-lane-1/memory/notes.md"  # Claude's own memory stays writable
                self.assertNotEqual(R.claude_decision({"deny": order["deny"]}, "Edit", memory, H, folder,
                                                      settings_dir), "deny")
                for c in FLOOR_COMMANDS:
                    with self.subTest(folder=folder, command=c):
                        self.assertEqual(R.claude_decision(order, "Bash", c, H, folder, settings_dir), "deny")
        perms = R.claude_permissions(["Read(src/**)", "Bash(npm run test *)"], [], self.items(), H)
        self.assertEqual(R.claude_decision(perms, "Read", f"{W}/src/app.ts", H, W), "allow")
        self.assertEqual(R.claude_decision(perms, "Bash", "npm run test --watch", H, W), "allow")
        self.assertEqual(R.claude_decision(perms, "Read", f"{W}/src/.env", H, W), "deny")  # the floor carves it out

    def test_opencode_no_allow_reaches_a_floor_path_or_command_whatever_comes_after(self):
        for folder in (W, M):
            perms, policies = R.opencode_permissions(WIDE, [], self.items(folder), H, folder)
            later = [{"action": "*", "resource": "*", "effect": "allow"}]  # a later file's rule, a saved approval
            for rules in (perms, perms[::-1], perms + later):
                for p in FLOOR_READS:
                    res = p[len(folder) + 1:] if p.startswith(folder + "/") else p
                    for act in ("read", "edit"):
                        with self.subTest(folder=folder, act=act, path=res):
                            self.assertEqual(R.opencode_decision(rules, policies, act, res), "deny")
                for p in FLOOR_EDITS + ([f"{W}/.claude/settings.json"] if folder == W else []):
                    res = p[len(folder) + 1:] if p.startswith(folder + "/") else p
                    with self.subTest(folder=folder, edit=res):
                        self.assertEqual(R.opencode_decision(rules, policies, "edit", res), "deny")
                for c in FLOOR_COMMANDS:
                    with self.subTest(folder=folder, command=c):
                        self.assertEqual(R.opencode_decision(rules, policies, "shell", c), "deny")
            self.assertTrue(all(p["effect"] == "deny" for p in policies))  # lane-control never writes an allow policy
        self.assertEqual(R.opencode_decision(perms, policies, "read", "src/app.ts"), "allow")

    def test_opencode_every_deny_follows_every_allow_b3(self):
        cases = [  # (allows, denies, the call, what it must be)
            (["Bash(git push *)"], ["Bash(git push --force *)"], "git push --force origin main", "deny"),
            (["Bash(git push *)"], ["Bash(git push --force *)"], "git push origin lane-1", "allow"),
            (["Bash(git push *)"], ["Bash(git push *)"], "git push origin main", "deny"),
            (["Bash"], ["Bash(rm *)"], "rm -rf build", "deny"),
            (["WebFetch(domain:example.com)"], ["WebFetch"], "https://example.com/x", "deny"),
        ]
        for allows, denies, call, want in cases:
            for a, d in ((allows, denies), (allows[::-1], denies[::-1])):
                perms, policies = R.opencode_permissions(a, d, [], H, W)
                act = "webfetch" if call.startswith("http") else "shell"
                with self.subTest(allows=a, denies=d, call=call):
                    self.assertEqual(R.opencode_decision(perms, policies, act, call), want)
                    claude = R.claude_decision({"allow": a, "deny": d}, "Bash", call, H, W) if act == "shell" else want
                    self.assertEqual(claude, want)
                    i_allow = [i for i, r in enumerate(perms) if r["effect"] == "allow"]
                    i_deny = [i for i, r in enumerate(perms) if r["effect"] == "deny"]
                    self.assertTrue(not i_allow or not i_deny or max(i_allow) < min(i_deny))

    def test_wildcards_intersect_as_each_harness_matches(self):
        yes = [("gh auth *", "gh auth token *"), ("*", "x"), ("a*c", "abc"), ("a?c", "abc"), ("src/*", "src/a/.env"),
               ("fixtures/*", "fixtures/customers/*"), ("git push *", "git push --force *"), ("*.env", ".env")]
        no = [("npm run test *", "gh auth token *"), ("src/*.ts", "data/*"), ("ls", "lsof"), ("a?c", "ac")]
        for a, b in yes:
            self.assertTrue(R.glob_intersect(a, b) and R.glob_intersect(b, a), (a, b))
        for a, b in no:
            self.assertFalse(R.glob_intersect(a, b) or R.glob_intersect(b, a), (a, b))
        self.assertTrue(R.bash_matches("ls *", "ls"))
        self.assertFalse(R.bash_matches("ls *", "lsof"))
        self.assertTrue(R.bash_matches("ls*", "lsof"))
        self.assertTrue(R.bash_matches("git:*", "git log"))
        self.assertEqual(R.claude_decision({"allow": ["Bash(npm run test *)"]}, "Bash", "npm run test && rm -rf /", H,
                                           W), "default")  # every subcommand must be allowed

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


class PermissionAddTests(Base):
    def test_b2_every_allow_that_reaches_the_floor_another_way_is_refused(self):
        self.private(["fixtures/customers/*"])
        for rule in ("Bash", "Bash(*)", "Bash(:*)", "Bash(cat:*)", "Bash(python3:*)", "Bash(sh -c *)",
                     "Bash(npx *)", "Bash(git *)", "Bash(git -c core.pager=x log)", "Bash(git config *)",
                     "Bash(gh auth *)", "Bash(make test && cat ~/.ssh/id_rsa)", "Bash(* --version)",
                     "Bash(/usr/bin/make test)", "Read(//**/*)", "Read(/home/**)", "Read(~/*)", "Read(~/.*)",
                     "Read(**/.*)", "Read(**/.en?)", "Read(.e*)", "Read(~/.config/**)", "Read(~/.claude/**)",
                     "Edit(.claude/**)", "Edit(.claude/settings.local.json)", "Write(~/.claude/settings.json)",
                     "Read(fixtures/**)", "Read(fixtures/customers/acme.csv)", "Read(../other/**)", "Read", "Edit(*)",
                     "Grep(src/**)", "WebFetch", "mcp__*", "TodoWrite"):
            with self.subTest(rule=rule):
                self.refused("project", "add", self.repo, "--permission", "allow", rule, says="refused")
        self.assertEqual(self.stored(), [])

    def test_plain_allows_and_any_deny_are_taken_and_what_is_not_bound_is_said(self):
        out = self.add("--permission", "allow", "Bash(npm run test *)", "--for", "lanes")
        self.assertIn("not bound by any rule", out)
        self.add("--permission", "allow", "Read(src/**)")
        self.add("--permission", "allow", "WebFetch(domain:docs.example.com)")
        self.add("--permission", "allow", "mcp__github__get_issue")
        self.add("--permission", "deny", "Bash(git push --force *)")
        out = self.add("--permission", "deny", "Agent(model:opus)")
        self.assertIn("Claude only", out)
        ids = [X.ident(e) for e in self.stored()]
        self.assertIn("permission:allow:Bash(npm run test *)", ids)
        self.assertIn("permission:deny:Agent(model:opus)", ids)
        self.ok("project", "remove", self.repo, "permission:allow:Read(src/**)")
        self.assertNotIn("permission:allow:Read(src/**)", [X.ident(e) for e in self.stored()])

    def test_a_machine_allow_is_checked_against_every_projects_private_paths(self):
        self.private(["fixtures/customers/*"])
        self.refused("machine", "add", "--permission", "allow", "Read(fixtures/**)", says="--private")
        self.ok("machine", "add", "--permission", "allow", "Read(docs/**)")

    def test_a_rule_that_carries_a_secret_is_refused_and_never_shown(self):
        for rule in (f"Bash(curl -H 'Authorization: Bearer {SECRET}' x)", f"WebFetch(domain:{SECRET})",
                     "Bash(git clone https://u:hunter2pass@host/r.git)"):
            for effect in ("allow", "deny"):
                with self.subTest(rule=rule[:20], effect=effect):
                    self.refused("project", "add", self.repo, "--permission", effect, rule, says="refused")
        self.assertEqual(self.stored(), [])


class PermissionApplyTests(Base):
    def test_prepare_writes_the_owners_rules_beside_the_floor_on_both_harnesses(self):
        self.private(["fixtures/customers/*"])
        self.add("--permission", "allow", "Bash(npm run test *)", "--for", "lanes")
        self.add("--permission", "deny", "Bash(git push --force *)", "--for", "lanes")
        self.assertEqual(self.prepare()[0], 0)
        perms = self.settings()["permissions"]
        self.assertEqual(perms["allow"], ["Bash(npm run test *)"])
        for rule in ("Bash(git push --force *)", "Read(~/.ssh/**)", "Bash(gh auth token *)",
                     f"Edit(/{self.wt}/.claude/settings.json)", "Read(//**/fixtures/customers/*)",
                     f"Read(/{self.repo}/fixtures/customers/*)",
                     f"Edit(/{self.repo}/.claude/settings.local.json)"):
            self.assertIn(rule, perms["deny"])
        home, wt = os.environ["HOME"], str(self.wt)
        self.assertEqual(R.claude_decision(perms, "Read", f"{home}/.ssh/id_rsa", home, wt), "deny")
        self.assertEqual(R.claude_decision(perms, "Read", f"{wt}/fixtures/customers/a.csv", home, wt), "deny")
        self.assertEqual(R.claude_decision(perms, "Edit", f"{wt}/.claude/settings.json", home, wt), "deny")
        self.assertEqual(R.claude_decision(perms, "Bash", "npm run test", home, wt), "allow")
        oc = self.oc()
        self.assertEqual([p["effect"] for p in oc["permissions"]], ["allow", "deny"])
        resources = {p["resource"] for p in oc["policies"]}
        self.assertTrue({"shell:git push --force *", "shell:gh auth token *", f"read:{home}/.ssh/*",
                         "read:fixtures/customers/*", "edit:.claude/settings.json"} <= resources)
        for res, act in ((f"{home}/.ssh/id_rsa", "read"), ("fixtures/customers/a.csv", "read"),
                         (".opencode/opencode.json", "edit")):
            self.assertEqual(R.opencode_decision(oc["permissions"], oc["policies"], act, res), "deny")
        self.assertEqual(R.opencode_decision(oc["permissions"], oc["policies"], "shell", "git push --force x"), "deny")
        self.clean(self.wt)
        main = claude_reads(self.repo, self.repo)
        self.assertNotIn("npm run test", main["text"])  # a lane's rule never reaches the coordinator

    def test_the_coordinators_floor_names_absolute_paths_and_stays_out_of_lanes(self):
        self.private(["fixtures/customers/*"])
        self.add("--permission", "allow", "Read(docs/**)", "--for", "coordinator")
        self.add("--env", "APP_MODE=coordinating", "--for", "coordinator")
        lines, problems = self.coordinator()
        self.assertEqual(problems, [])
        launch = X.launch_dir(self.store)
        s = json.loads((launch / "settings.json").read_text())
        self.assertEqual(s["env"], {"APP_MODE": "coordinating"})
        self.assertIn(f"Read(/{self.repo}/fixtures/customers/*)", s["permissions"]["deny"])
        home = os.environ["HOME"]
        self.assertEqual(R.claude_decision(s["permissions"], "Read", f"{self.repo}/fixtures/customers/a.csv", home,
                                           str(self.repo), str(launch)), "deny")
        self.assertIn(f"read:{self.repo}/fixtures/customers/*",
                      {p["resource"] for p in self.oc(self.repo)["policies"]})
        self.assertIn("--settings", X.launch_args(self.store))
        self.assertEqual(self.prepare()[0], 0)
        lane = claude_reads(self.wt, self.repo)
        self.assertNotIn("coordinating", lane["text"])
        self.assertNotIn("docs/**", lane["text"])
        self.assertFalse((self.wt / ".opencode").exists())

    def test_machine_deny_and_project_allow_in_either_order_deny_wins(self):
        for first, second in ((("machine", "add"), ("project", "add", self.repo)),
                              (("project", "add", self.repo), ("machine", "add"))):
            with self.subTest(first=first[0]):
                deny = ["--permission", "deny", "Bash(git push --force *)", "--for", "lanes"]
                allow = ["--permission", "allow", "Bash(git push *)", "--for", "lanes"]
                self.ok(*first, *(deny if first[0] == "machine" else allow))
                self.ok(*second, *(allow if first[0] == "machine" else deny))
                self.assertEqual(self.prepare()[0], 0)
                oc, perms = self.oc(), self.settings()["permissions"]
                home = os.environ["HOME"]
                self.assertEqual(R.opencode_decision(oc["permissions"], oc["policies"], "shell",
                                                     "git push --force origin main"), "deny")
                self.assertEqual(R.opencode_decision(oc["permissions"], oc["policies"], "shell", "git push"), "allow")
                self.assertEqual(R.claude_decision(perms, "Bash", "git push --force origin main", home,
                                                   str(self.wt)), "deny")
                for f in (X.machine_file(), X.project_file(self.store)):
                    f.unlink()

    def test_a_private_path_added_later_fails_apply_and_check_never_silently(self):
        self.add("--permission", "allow", "Read(fixtures/**)", "--for", "lanes")
        self.assertEqual(self.prepare()[0], 0)
        self.private(["fixtures/customers/*"])
        code, out, err = self.prepare()
        self.assertEqual(code, 1)
        self.assertIn("--private", out)
        self.assertFalse((self.wt / ".claude" / "settings.json").exists())  # all or nothing: none of ours stays
        rows = {r["id"]: r for r in X.check(self.store)}
        self.assertEqual(rows["permission:allow:Read(fixtures/**)"]["state"], "failing")


class EnvTests(Base):
    def test_secret_and_reserved_names_and_secret_values_are_refused_and_never_kept(self):
        for arg in ("GITHUB_TOKEN=abc", "API_KEY=abc", "DB_PASSWORD=x", "LANE_ROLE=lanes", "ORCA_TERMINAL_HANDLE=x",
                    "HOME=/tmp", "PATH=/tmp", "XDG_CONFIG_HOME=/tmp", "CLAUDE_CONFIG_DIR=/tmp", "GIT_DIR=/tmp",
                    "LD_PRELOAD=/tmp/x.so", "ANTHROPIC_BASE_URL=http://x", "lower=x", f"APP_SETTING={SECRET}",
                    "DATABASE_URL=postgres://u:hunter2pass@h/db", "APP_HEADER=Bearer abcdefghijklmnop",
                    "APP_ID=" + "aB3" * 14, "APP_MODE=line\nbreak"):
            with self.subTest(arg=arg.split("=")[0]):
                self.refused("project", "add", self.repo, "--env", arg, says="refused")
        self.assertEqual(self.stored(), [])
        self.assertNotIn("hunter2", X.project_file(self.store).read_text() if X.project_file(self.store).exists()
                         else "")

    def test_a_plain_value_reaches_both_harnesses_for_its_role_only(self):
        self.add("--env", "APP_MODE=fast", "--for", "lanes")
        self.assertIn("env:APP_MODE", self.ok("project", "list", self.repo))
        self.assertEqual(self.prepare()[0], 0)
        self.assertEqual(self.settings()["env"], {"APP_MODE": "fast"})
        js = (self.wt / X.OC_PLUGIN).read_text()
        self.assertIn('{"APP_MODE": "fast"}', js)
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

    def test_the_session_runs_lane_controls_runner_never_the_script(self):
        s = self.hook_script()
        out = self.add("--agent-hook", "PreToolUse", "--matcher", "Bash|Edit", "--for", "lanes", "--", s)
        e = [x for x in self.stored() if x["kind"] == "agent-hook"][0]
        self.assertEqual((e["name"], e["sha256"], e["matcher"]), ("pretooluse-guard", X.sha256(s), "Bash|Edit"))
        self.assertIn("agent-hook:pretooluse-guard", out)
        self.assertEqual(self.prepare()[0], 0)
        h = self.settings()["hooks"]["PreToolUse"][0]
        self.assertEqual(h["matcher"], "Bash|Edit")
        cmd = h["hooks"][0]["command"]
        self.assertIn("lane-agent-hook run", cmd)
        self.assertNotIn(str(s), cmd)
        js = (self.wt / X.OC_PLUGIN).read_text()
        hooks = json.loads(js.split("const HOOKS = ", 1)[1].split(";\n", 1)[0])
        self.assertEqual([(h["event"], h["tools"]) for h in hooks], [("PreToolUse", ["shell", "edit"])])
        self.assertIn('ctx.tool.hook("execute.before"', js)
        self.assertNotIn(str(s), js)

    def test_session_start_and_stop_are_claude_only_and_said(self):
        out = self.add("--agent-hook", "Stop", "--", self.hook_script("notify.sh"))
        self.assertIn("Claude only", out)
        self.refused("project", "add", self.repo, "--agent-hook", "Stop", "--matcher", "Bash", "--",
                     self.hook_script("n2.sh"), says="PreToolUse and PostToolUse only")
        self.assertEqual(self.prepare()[0], 0)
        self.assertIn("Stop", self.settings()["hooks"])
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
                        extra_env={"GITHUB_TOKEN": SECRET, "ANTHROPIC_API_KEY": SECRET, "CLAUDE_PROJECT_DIR": str(self.wt)})
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn('in={"tool_name": "Bash"}', r.stdout)
        self.assertIn("LANE_AGENT_HOOK_EVENT=PostToolUse", r.stdout)
        self.assertIn(f"CLAUDE_PROJECT_DIR={self.wt}", r.stdout)
        self.assertNotIn(SECRET, r.stdout + r.stderr)
        self.assertNotIn(f"self={s}", r.stdout)  # a private copy, never the owner's file itself
        runs = X.state_home() / "lane-control" / "hooks" / "runs"  # hooks.runs_dir: the copy is gone after
        self.assertFalse(any(runs.glob("agent-*")))

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
        self.assertFalse((self.wt / ".claude" / "settings.json").exists())  # refused, all or nothing: not dropped
        r = self.runner("PreToolUse", "pretooluse-guard")
        self.assertEqual(r.returncode, 2)  # a session that still has it: the call is blocked, fail closed
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
