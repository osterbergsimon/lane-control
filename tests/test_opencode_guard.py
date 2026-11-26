"""lane-away-guard.js, the OpenCode half of away-improve mode's deny rules (lc-yzb): a plugin hook that refuses a shell
call to merge a PR or to push to the project's integration branch while improve mode is on for the coordinator's
project. Under node, called the way OpenCode 2.2.1 calls a plugin (probed live: ctx.tool.hook("execute.before", fn);
throwing from it stops the call and the model sees the message). The opt-in live check at the end runs the real
opencode2 against a fake model."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_harnesses import HARNESSES, NODE, HomeTest, clean_env  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GUARD = HARNESSES / "opencode" / "lane-away-guard.js"

DRIVER = """
import { pathToFileURL } from "node:url";
import { writeFileSync, unlinkSync } from "node:fs";
const [plugin, scenario] = process.argv.slice(2);
const events = JSON.parse(scenario);
const mod = await import(pathToFileURL(plugin).href);
const def = mod.default;
let hook;
const registered = [];
const ctx = { tool: { hook: async (name, fn) => { registered.push(name); hook = fn; return { dispose: async () => {} }; } } };
const dispose = await def.setup(ctx);
const out = { id: def?.id, named: Object.keys(mod).filter((k) => k !== "default"), registered, results: [] };
for (const ev of events) {
  if (ev.fs) {
    for (const [p, text] of ev.fs) { if (text === null) { try { unlinkSync(p); } catch {} } else writeFileSync(p, text); }
    continue;
  }
  const e = { tool: ev.tool ?? "shell", sessionID: "ses_1", input: { command: ev.command, ...(ev.workdir ? { workdir: ev.workdir } : {}) } };
  try { await hook?.(e); out.results.push({ ok: true }); } catch (err) { out.results.push({ ok: false, message: String(err?.message) }); }
}
if (typeof dispose === "function") await dispose();
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class GuardTests(HomeTest):
    def setUp(self):
        super().setUp()
        self.oc = self.pkg.load_harness("opencode")
        os.environ["XDG_STATE_HOME"] = str(self.t / "state")
        self.cfg = self.t / "cfg"
        self.cfg.mkdir()
        self.improve = self.cfg / "improve.json"
        self.drain = self.cfg / "improve-drain.json"
        self.beads = self.t / "code" / "shop.app-beads"
        self.beads.mkdir()
        self.driver = self.t / "driver.mjs"
        self.driver.write_text(DRIVER)
        self.copy = self.t / "lane-away-guard.mjs"  # .mjs so node loads it as a module; the shipped bytes
        if GUARD.exists():
            self.copy.write_bytes(GUARD.read_bytes())

    GOOD = {"started": "2026-11-23T10:00:00+00:00", "end": "until-back", "limits": "", "deny_paths": [],
            "projects": "all", "unmetered": False, "branches": {"shop": "away/2026-11-23"}, "integration": {"shop": "main"}}

    def state(self, **over):
        self.improve.write_text(json.dumps({**self.GOOD, **over}, indent=2, sort_keys=True) + "\n")

    def drain_state(self, pending=("shop",), **over):
        self.drain.write_text(json.dumps({**self.GOOD, "ended": "2026-11-24T08:00:00+00:00", "pending": list(pending), **over}))

    def install(self, integration="main", project="shop"):
        return self.oc.install_away_denies(self.checkout, integration, project)

    def run_guard(self, events, role="coordinator", cwd=None, **env):
        e = clean_env(**{**testhome.env_for(self.home), "LANE_IMPROVE_FILE": str(self.improve),
                         "XDG_STATE_HOME": str(self.t / "state"), **env})
        if role:
            e["LANE_ROLE"] = role
        r = subprocess.run([NODE, str(self.driver), str(self.copy), json.dumps(events)], capture_output=True, text=True,
                           cwd=cwd or self.checkout, env=e, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout), r.stderr

    def verdicts(self, commands, **kw):
        evs = [{"command": c} if isinstance(c, str) else c for c in commands]
        out, err = self.run_guard(evs, **kw)
        return [("ok" if r["ok"] else "refused") for r in out["results"]], out, err

    def on(self):
        self.state()
        self.install()

    REFUSED = [
        "gh pr merge 5 --merge", "gh -R o/r pr merge 5", "cd /somewhere && gh pr merge 5", "echo hi; gh pr merge 5",
        "gh api -X PUT repos/o/r/pulls/5/merge", "git push origin main", "git push origin HEAD:main",
        "git push origin HEAD:refs/heads/main", "git push origin +main", "git push origin feature:main",
        "git push -f origin main", "git push --force-with-lease origin main", "git push origin feature main",
        "git push -u origin main", "git -c user.name=x push origin main", "git push origin main:main",
        "bash -c 'git push origin main'", 'sh -c "gh pr merge 5"', "echo $(gh pr merge 5)", "echo `gh pr merge 5`",
        "FOO=1 gh pr merge 5", "env GH_TOKEN=x gh pr merge 5", "sudo gh pr merge 5", "time git push origin main",
        "git push", "git push origin", "git push --all", "git push -u origin HEAD", "git push origin HEAD",
        "git -C {checkout} push origin main", "cd {checkout} && git push origin main",
        "cd {beads} && cd {checkout} && git push origin main", 'cd "$SOMEWHERE" && git push origin main',
        'git -C "$(pwd)/x" push origin main', "cd {beads}; git push origin main; cd {checkout}; git push origin main",
    ]
    ALLOWED = [
        "git push origin feature-x", "git push origin HEAD:feature-x", "git status", "gh pr view 5", "gh pr create --draft",
        "gh pr comment 5 --body hi", "/opt/lc/core/scripts/away-merge --project shop --pr 5 --item x", "git fetch origin main",
        'echo "gh pr merge 5"', "echo 'git push origin main'", "bd dolt push", "ls && git log --oneline",
        "git push origin main-fix", "git push origin feature/main", "git push --dry-run origin feature",
        'cd "$SOMEWHERE" && git push', "git -C {beads} push", "git -C {beads} push origin main", "cd {beads} && git push",
        "cd {beads} && git push origin main && bd dolt push", "cd ../shop.app-beads && git push origin main",
        "cd ../shop.app-beads && git push", "git -C ../shop.app-beads push origin main",
        "git --git-dir={beads}/.git push origin main", "pushd {beads} && git push origin main",
        "cd {checkout} && cd {beads} && git push origin main",
    ]

    def fill(self, cmds):
        return [c.format(checkout=self.checkout, beads=self.beads) for c in cmds]

    def test_the_hook_is_registered_and_the_shape_is_a_v2_definition(self):
        self.on()
        _, out, _ = self.verdicts(["git status"])
        self.assertEqual((out["id"], out["named"], out["registered"]), ("lane-away-guard", [], ["execute.before"]))

    def test_refused_commands_while_improve_is_on(self):
        self.on()
        cmds = self.fill(self.REFUSED)
        got, out, _ = self.verdicts(cmds)
        self.assertEqual([c for c, g in zip(cmds, got) if g != "refused"], [])
        msg = out["results"][0]["message"]
        self.assertIn("away-merge", msg)
        self.assertIn("away-improve", msg)

    def test_allowed_commands_while_improve_is_on(self):
        self.on()
        cmds = self.fill(self.ALLOWED)
        got, _, _ = self.verdicts(cmds)
        self.assertEqual([c for c, g in zip(cmds, got) if g != "ok"], [])

    def test_the_beads_backup_is_allowed_and_a_push_to_the_integration_branch_is_refused(self):
        self.on()
        backup = [{"command": "git push", "workdir": str(self.beads)}, {"command": "git push origin main", "workdir": str(self.beads)},
                  {"command": f"cd {self.beads} && git push && bd dolt push"}, {"command": f"git -C {self.beads} push"}]
        got, _, _ = self.verdicts(backup)
        self.assertEqual(got, ["ok"] * 4)
        project = [{"command": "git push origin main"}, {"command": "git push origin main", "workdir": str(self.checkout)},
                   {"command": "git push origin main", "workdir": str(self.checkout / "sub")}]
        (self.checkout / "sub").mkdir()
        got, _, _ = self.verdicts(project)
        self.assertEqual(got, ["refused"] * 3)

    def test_the_integration_branch_is_the_recorded_one(self):
        self.state(integration={"shop": "develop"})
        self.install(integration="develop")
        got, _, _ = self.verdicts(["git push origin develop", "git push origin main", "git push origin HEAD:develop"])
        self.assertEqual(got, ["refused", "ok", "refused"])

    def test_nothing_without_the_record_or_the_coordinator_role(self):
        self.state()  # improve is on, but nothing installed for this checkout
        got, _, _ = self.verdicts(["gh pr merge 5", "git push origin main"])
        self.assertEqual(got, ["ok", "ok"])
        self.install()
        got, out, _ = self.verdicts(["gh pr merge 5"], role=None)
        self.assertEqual((got, out["registered"]), (["ok"], []))  # not a coordinator: it does not even hook in
        got, out, _ = self.verdicts(["gh pr merge 5"], role="reviewer")
        self.assertEqual(out["registered"], [])
        other = self.t / "code" / "other"
        other.mkdir()
        got, _, _ = self.verdicts(["gh pr merge 5"], cwd=other)  # another checkout: no record
        self.assertEqual(got, ["ok"])

    def test_other_tools_are_left_alone(self):
        self.on()
        out, _ = self.run_guard([{"tool": "read", "command": "gh pr merge 5"}, {"tool": "edit", "command": "git push origin main"}])
        self.assertEqual([r["ok"] for r in out["results"]], [True, True])
        out, _ = self.run_guard([{"tool": "bash", "command": "gh pr merge 5"}])  # the name in older builds
        self.assertFalse(out["results"][0]["ok"])

    def test_live_with_no_restart_on_then_off_then_on(self):
        self.on()
        evs = [{"command": "gh pr merge 5"},
               {"fs": [[str(self.improve), None]]}, {"command": "gh pr merge 5"},  # no state at all: the guard lifts
               {"fs": [[str(self.improve), json.dumps(self.GOOD)]]}, {"command": "gh pr merge 5"}]
        out, _ = self.run_guard(evs)
        self.assertEqual([r["ok"] for r in out["results"]], [False, True, False])

    def test_off_drains_and_the_guard_stays_until_drained(self):
        self.on()
        evs = [{"fs": [[str(self.improve), None], [str(self.drain), json.dumps({**self.GOOD, "ended": "2026-11-24T08:00:00+00:00", "pending": ["shop"]})]]},
               {"command": "gh pr merge 5"}, {"command": "git push origin main"}, {"command": "git status"},
               {"fs": [[str(self.drain), None]]}, {"command": "gh pr merge 5"}]
        out, _ = self.run_guard(evs)
        self.assertEqual([r["ok"] for r in out["results"]], [False, False, True, True])

    def test_the_end_time_passing_is_a_drain_too(self):
        self.on()
        self.state(end="2020-01-01T00:00:00+00:00")
        got, _, _ = self.verdicts(["gh pr merge 5"])
        self.assertEqual(got, ["refused"])

    def test_a_project_the_mode_does_not_cover_is_left_alone(self):
        self.state(projects=["blog"])
        self.install()
        got, _, _ = self.verdicts(["gh pr merge 5"])
        self.assertEqual(got, ["ok"])
        self.drain_state(pending=["blog"])
        self.improve.unlink()
        got, _, _ = self.verdicts(["gh pr merge 5"])
        self.assertEqual(got, ["ok"])

    def test_malformed_state_fails_open_and_says_why(self):
        self.install()
        for text in ("{not json", '{"end": "until-back"}', json.dumps({**self.GOOD, "end": "tomorrow"})):
            with self.subTest(text=text[:30]):
                self.improve.write_text(text)
                got, _, err = self.verdicts(["gh pr merge 5"])
                self.assertEqual(got, ["ok"])
                self.assertIn("improve.json", err)
                self.assertIn("fail", err.lower())

    def test_a_record_with_no_state_at_all_fails_open_quietly_once(self):
        self.install()  # a stale record: `lane-away drained` was never run, the files are gone
        got, _, err = self.verdicts(["gh pr merge 5", "git push origin main"])
        self.assertEqual(got, ["ok", "ok"])
        self.assertLessEqual(err.count("lane-away-guard"), 1)

    def test_the_plugin_never_throws_into_opencode_on_odd_input(self):
        self.on()
        out, _ = self.run_guard([{"command": None}, {"command": 5}, {"command": ""}, {"tool": "shell"}, {"command": "echo 'unterminated"},
                                 {"command": "x" * 100000}])
        self.assertTrue(all(r["ok"] for r in out["results"]), out["results"])

    def test_it_agrees_with_the_python_readers_on_which_state_files_count(self):
        sys.path.insert(0, str(ROOT / "core"))
        import improve
        from test_lane_away import ReaderAgreementTests
        cases = list(ReaderAgreementTests.cases(ReaderAgreementTests("test_the_reader_accepts_exactly_the_complete_valid_files")))
        self.install()
        os.environ["LANE_IMPROVE_FILE"] = str(self.improve)
        os.environ["LANE_IMPROVE_DRAIN_FILE"] = str(self.drain)
        try:
            wrong = []
            for name, text in cases:
                self.improve.write_text(text)
                py = improve.mode_state("shop")[0] is not None
                out, _ = self.run_guard([{"command": "gh pr merge 5"}])
                js = not out["results"][0]["ok"]
                if py != js:
                    wrong.append((name, "python active" if py else "python inactive", "guard active" if js else "guard inactive"))
            self.assertEqual(wrong, [])
        finally:
            os.environ.pop("LANE_IMPROVE_FILE", None)
            os.environ.pop("LANE_IMPROVE_DRAIN_FILE", None)

    def test_installing_and_removing_the_record(self):
        lines = self.install()
        self.assertTrue(lines and "lane-away-guard" in " ".join(lines))
        rec = self.pkg.away_denies_record(self.checkout, "opencode")
        self.assertEqual(json.loads(rec.read_text()), {"integration": "main", "project": "shop"})
        self.assertEqual(self.install(), [])  # again: nothing changed
        self.assertTrue(self.oc.remove_away_denies(self.checkout))
        self.assertFalse(rec.exists())
        self.assertEqual(self.oc.remove_away_denies(self.checkout), [])
        for bad in ("", "a b", "x:*)", "main\nx", "-f"):
            with self.assertRaises(ValueError, msg=bad):
                self.oc.install_away_denies(self.checkout, bad, "shop")
        self.assertEqual(list(self.checkout.iterdir()), [])  # nothing written into the checkout

    def test_install_away_guard_puts_the_plugin_beside_the_persona_plugin(self):
        os.environ["XDG_CONFIG_HOME"] = str(self.t / "config")
        lines = self.oc.install_away_guard(self.checkout)
        dest = self.t / "config" / "opencode" / "plugins" / "lane-away-guard.js"
        self.assertEqual(dest.read_bytes(), GUARD.read_bytes())
        self.assertTrue(lines)
        self.assertEqual(self.oc.install_away_guard(self.checkout), [])
        os.environ["LANE_OPENCODE_PLUGIN"] = "project"
        try:
            self.assertTrue(self.oc.install_away_guard(self.checkout))
            self.assertTrue((self.checkout / ".opencode" / "plugins" / "lane-away-guard.js").is_file())
        finally:
            os.environ.pop("LANE_OPENCODE_PLUGIN", None)

    def test_records_say_what_is_covered(self):
        rec = " ".join((HARNESSES / "opencode" / "record.md").read_text().split())
        for needle in ("lane-away-guard.js", "execute.before", "shell", "a running coordinator needs one restart",
                       "backstop", "a bare push"):
            self.assertIn(needle, rec, needle)
        self.assertNotIn("away-merge alone guards", rec)
        self.assertEqual([n for n, ln in enumerate((HARNESSES / "opencode" / "record.md").read_text().splitlines(), 1) if len(ln) > 120], [])


OPENCODE2 = shutil.which("opencode2")
LIVE = os.environ.get("LANE_LIVE_OPENCODE") == "1"


@unittest.skipUnless(LIVE and OPENCODE2, "opt-in: LANE_LIVE_OPENCODE=1 and opencode2 on PATH")
class GuardLiveTests(HomeTest):
    """The real opencode2 (2.x) with the shipped guard plugin, in temp HOME/XDG folders, talking to
    tests/stubs/fake_tool_llm.py on 127.0.0.1 (no real provider, no sign-in). The fake model asks for a shell call
    `gh pr merge 5`; `gh` is a stub on PATH that only leaves a marker file, so nothing real is ever merged. On one
    running server: improve on -> refused, no state -> runs, on again -> refused, drained -> runs. About 40 s. Run:
    LANE_LIVE_OPENCODE=1 python3 -m unittest tests.test_opencode_guard.GuardLiveTests"""

    def setUp(self):
        super().setUp()
        v = subprocess.run([OPENCODE2, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        if "v2." not in v and not v.startswith("2."):
            self.skipTest(f"opencode2 is not 2.x: {v}")
        for d in ("config", "data", "state", "cache", "bin", "cfg"):
            (self.t / d).mkdir()
        os.environ["XDG_CONFIG_HOME"] = str(self.t / "config")
        os.environ["XDG_STATE_HOME"] = str(self.t / "state")
        self.oc = self.pkg.load_harness("opencode")
        self.oc.install_away_guard(self.checkout)
        self.marker = self.t / "gh-ran"
        gh = self.t / "bin" / "gh"
        gh.write_text(f'#!/bin/sh\necho "$@" >> "{self.marker}"\n')
        gh.chmod(0o755)
        self.improve = self.t / "cfg" / "improve.json"
        self.drain = self.t / "cfg" / "improve-drain.json"
        self.reqlog = self.t / "requests.jsonl"
        self.reqlog.write_text("")
        env = dict(os.environ, LANE_FAKE_CMD="gh pr merge 5")
        self.fake = subprocess.Popen([sys.executable, str(ROOT / "tests" / "stubs" / "fake_tool_llm.py"), str(self.reqlog)],
                                     stdout=subprocess.PIPE, text=True, env=env)
        self.addCleanup(self.stop, self.fake)
        fport = int(self.fake.stdout.readline())
        self.conf = json.dumps({"model": "lanetest/m", "permission": {"bash": "allow", "shell": "allow"},
                                "provider": {"lanetest": {"npm": "@ai-sdk/openai-compatible", "name": "lanetest",
                                                          "models": {"m": {"name": "m", "tool_call": True}},
                                                          "options": {"baseURL": f"http://127.0.0.1:{fport}/v1", "apiKey": "unused"}}}})

    @staticmethod
    def stop(proc):
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()

    def serve(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        env = {"PATH": f"{self.t / 'bin'}:{os.environ['PATH']}", "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.t / "config"),
               "XDG_DATA_HOME": str(self.t / "data"), "XDG_STATE_HOME": str(self.t / "state"),
               "XDG_CACHE_HOME": str(self.t / "cache"), "OPENCODE_DISABLE_MODELS_FETCH": "1",
               "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_CLAUDE_CODE": "1", "OPENCODE_DISABLE_FREE_MODELS": "1",
               "OPENCODE_SERVER_PASSWORD": "live-check", "OPENCODE_CONFIG_CONTENT": self.conf, "LANE_ROLE": "coordinator",
               "LANE_PERSONA": str(self.t / "p.md"), "LANE_IMPROVE_FILE": str(self.improve)}
        (self.t / "p.md").write_text("# Coordinator\n")
        self.log = self.t / "serve.log"
        proc = subprocess.Popen([OPENCODE2, "serve", "--hostname", "127.0.0.1", "--port", str(self.port), "--print-logs"],
                                cwd=self.checkout, env=env, stdout=subprocess.DEVNULL, stderr=self.log.open("w"))
        self.addCleanup(self.stop, proc)
        for _ in range(120):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.5).close()
                return
            except OSError:
                time.sleep(0.5)
        self.fail("the server did not start")

    def api(self, method, path, body=None):
        import base64
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method, data=json.dumps(body or {}).encode(),
                                     headers={"content-type": "application/json", "authorization": "Basic " +
                                              base64.b64encode(b"opencode:live-check").decode()})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["data"]

    def round(self, sid, i):
        before = len(self.reqlog.read_text().splitlines())
        self.api("POST", f"/api/session/{sid}/prompt", {"text": f"please RUN-TOOL {i}"})
        for _ in range(60):
            if len(self.reqlog.read_text().splitlines()) >= before + 2 + (1 if i == 0 else 0):
                break
            time.sleep(1)
        time.sleep(1)
        return self.marker.read_text().count("pr merge 5") if self.marker.exists() else 0

    def test_live_refused_then_runs_then_refused_with_no_restart(self):
        good = {"started": "2026-11-23T10:00:00+00:00", "end": "until-back", "limits": "", "deny_paths": [], "projects": "all",
                "unmetered": False, "branches": {"shop": "away/2026-11-23"}, "integration": {"shop": "main"}}
        self.assertTrue(self.oc.install_away_denies(self.checkout, "main", "shop"))
        self.improve.write_text(json.dumps(good))
        self.serve()
        sid = self.api("POST", "/api/session")["id"]
        self.assertEqual(self.round(sid, 0), 0, "improve on: the merge must be refused")
        self.improve.unlink()
        self.assertEqual(self.round(sid, 1), 1, "no improve state: the stub gh must run")
        self.improve.write_text(json.dumps(good))
        self.assertEqual(self.round(sid, 2), 1, "improve on again: refused, the count stays")
        # `off`: the drain keeps the guard; `drained` lifts it
        self.improve.unlink()
        self.drain.write_text(json.dumps({**good, "ended": "2026-11-24T08:00:00+00:00", "pending": ["shop"]}))
        self.assertEqual(self.round(sid, 3), 1, "draining: still refused")
        self.drain.unlink()
        self.assertEqual(self.round(sid, 4), 2, "drained: runs again")
        log = self.log.read_text()
        self.assertIn("lane-away-guard.js", log)
        self.assertNotIn("failed to load plugin", log)


if __name__ == "__main__":
    unittest.main()
