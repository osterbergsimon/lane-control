"""The testbed's fakes (tests/testbed/fakes/): the stub gh over a local forge and the fake LiteLLM gateway. Fast, no
docker; the testbed itself (tests/testbed/run) is opt-in and never runs here."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKES = ROOT / "tests" / "testbed" / "fakes"
GH = FAKES / "gh"
GATEWAY = FAKES / "gateway.py"
IDENT = ["-c", "user.name=T", "-c", "user.email=t@example.invalid"]


@unittest.skipUnless(shutil.which("git") and shutil.which("jq"), "needs git and jq")
class FakeGh(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tb-gh-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.forge = self.tmp / "forge"
        self.env = {**os.environ, "TESTBED_FORGE": str(self.forge), "GIT_CONFIG_NOSYSTEM": "1",
                    "HOME": str(self.tmp), "GIT_AUTHOR_NAME": "T", "GIT_COMMITTER_NAME": "T",
                    "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        self.gh("repo", "create", "acme/app")
        self.work = self.tmp / "work"
        self.git("clone", "-q", f"file://{self.forge}/repos/acme/app.git", str(self.work), cwd=self.tmp)
        (self.work / "a.txt").write_text("one\n")
        self.git("add", "a.txt")
        self.git("commit", "-qm", "first")
        self.git("push", "-q", "origin", "HEAD:main")

    def git(self, *a, cwd=None):
        return subprocess.run(["git", *IDENT, *a], cwd=cwd or self.work, env=self.env, check=True,
                              capture_output=True, text=True).stdout

    def gh(self, *a, cwd=None, ok=True):
        p = subprocess.run([sys.executable, str(GH), *a], cwd=cwd or self.tmp, env=self.env, capture_output=True,
                           text=True)
        if ok:
            self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def branch(self, name, text="two\n"):
        self.git("checkout", "-qb", name, "main")
        (self.work / "b.txt").write_text(text)
        self.git("add", "b.txt")
        self.git("commit", "-qm", f"work on {name}")
        self.git("push", "-q", "origin", name)
        self.git("checkout", "-q", "main")

    def test_repo_view_reads_the_origin_and_answers_jq(self):
        out = self.gh("repo", "view", "--json", "nameWithOwner,defaultBranchRef", cwd=self.work).stdout
        self.assertEqual(json.loads(out), {"nameWithOwner": "acme/app", "defaultBranchRef": {"name": "main"}})
        self.assertEqual(self.gh("repo", "view", "--json", "visibility", "-q", ".visibility", cwd=self.work).stdout.strip(),
                         "PUBLIC")

    def test_delete_branch_on_merge_by_repo_edit_and_by_api(self):
        q = ["repo", "view", "--json", "deleteBranchOnMerge", "-q", ".deleteBranchOnMerge"]
        self.assertEqual(self.gh(*q, cwd=self.work).stdout.strip(), "false")
        self.gh("repo", "edit", "--delete-branch-on-merge", cwd=self.work)
        self.assertEqual(self.gh(*q, cwd=self.work).stdout.strip(), "true")
        self.gh("api", "-X", "PATCH", "repos/acme/app", "-F", "delete_branch_on_merge=false")
        self.assertEqual(self.gh("api", "repos/acme/app", "-q", ".delete_branch_on_merge").stdout.strip(), "false")

    def test_pr_create_view_list_ready_and_merge_into_the_bare_repo(self):
        self.branch("lane/x")
        url = self.gh("pr", "create", "--head", "lane/x", "--base", "main", "--title", "X", "--body", "b", "--draft",
                      cwd=self.work).stdout.strip()
        self.assertTrue(url.endswith("/acme/app/pull/1"), url)
        rows = json.loads(self.gh("pr", "list", "--head", "lane/x", "--json", "number,isDraft,headRefOid",
                                  cwd=self.work).stdout)
        self.assertEqual([r["number"] for r in rows], [1])
        self.assertTrue(rows[0]["isDraft"])
        p = self.gh("pr", "merge", "1", "--merge", cwd=self.work, ok=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("draft", p.stderr)
        self.gh("pr", "ready", "1", cwd=self.work)
        p = self.gh("pr", "merge", "1", "--merge", "--match-head-commit", "0" * 40, cwd=self.work, ok=False)
        self.assertIn("Head branch was modified", p.stderr)
        self.gh("pr", "merge", "1", "--merge", "--match-head-commit", rows[0]["headRefOid"], "--delete-branch",
                cwd=self.work)
        view = json.loads(self.gh("pr", "view", "1", "--json", "state,mergeCommit", cwd=self.work).stdout)
        self.assertEqual(view["state"], "MERGED")
        self.git("fetch", "-q", "--prune", "origin")
        self.assertEqual(self.git("show", "origin/main:b.txt"), "two\n")
        self.assertEqual(self.git("rev-parse", "origin/main").strip(), view["mergeCommit"]["oid"])
        self.assertNotIn("origin/lane/x", self.git("branch", "-r"))
        merged = json.loads(self.gh("pr", "list", "--state", "merged", "--json", "baseRefName", cwd=self.work).stdout)
        self.assertEqual(merged, [{"baseRefName": "main"}])

    def test_checks_fail_on_demand_and_block_the_merge(self):
        self.branch("lane/y")
        self.gh("pr", "create", "--head", "lane/y", "--title", "Y", "--body", "", cwd=self.work)
        self.assertEqual(self.gh("pr", "checks", "1", cwd=self.work).returncode, 0)
        self.gh("testbed", "checks", "1", "failure", cwd=self.work)
        self.assertEqual(self.gh("pr", "checks", "1", cwd=self.work, ok=False).returncode, 1)
        roll = json.loads(self.gh("pr", "view", "1", "--json", "statusCheckRollup", cwd=self.work).stdout)
        self.assertEqual(roll["statusCheckRollup"][0]["conclusion"], "FAILURE")
        p = self.gh("pr", "merge", "1", "--merge", cwd=self.work, ok=False)
        self.assertIn("testbed-ci", p.stderr)
        self.gh("testbed", "checks", "lane/y", "success", cwd=self.work)
        self.gh("pr", "merge", "1", "--squash", cwd=self.work)

    def test_body_file_edit_and_graphql_last_edited(self):
        self.branch("away/2026-11-25")
        body = self.tmp / "body.md"
        body.write_text("summary ✓\n")
        self.gh("pr", "create", "--draft", "--base", "main", "--head", "away/2026-11-25", "--title", "Away",
                "--body-file", str(body), cwd=self.work)
        self.assertEqual(json.loads(self.gh("pr", "view", "1", "--json", "body", cwd=self.work).stdout)["body"],
                         "summary ✓\n")
        out = json.loads(self.gh("api", "graphql", "-F", "owner={owner}", "-F", "name={repo}", "-F", "number=1",
                                 "-f", "query=q", cwd=self.work).stdout)
        self.assertIsNone(out["data"]["repository"]["pullRequest"]["lastEditedAt"])
        self.gh("pr", "edit", "1", "--body-file", str(body), cwd=self.work)
        out = json.loads(self.gh("api", "graphql", "-F", "owner={owner}", "-F", "name={repo}", "-F", "number=1",
                                 "-f", "query=q", cwd=self.work).stdout)
        self.assertTrue(out["data"]["repository"]["pullRequest"]["lastEditedAt"])
        self.assertEqual(self.gh("api", "user", "-q", ".login").stdout.strip(), "testbed-owner")

    def test_unknown_repo_and_unmodelled_commands_fail_plainly(self):
        p = self.gh("repo", "view", "acme/none", ok=False)
        self.assertIn("Could not resolve", p.stderr)
        p = self.gh("issue", "list", cwd=self.work, ok=False)
        self.assertIn("not modelled", p.stderr)
        p = self.gh("pr", "view", "--json", "number", cwd=self.tmp, ok=False)  # not in a forge clone
        self.assertNotEqual(p.returncode, 0)
        self.assertTrue((self.forge / "calls.log").read_text().strip())


class FakeGateway(unittest.TestCase):
    def start(self, *extra):
        self.log = Path(tempfile.mkdtemp(prefix="tb-gw-")) / "log"
        self.addCleanup(shutil.rmtree, self.log.parent, True)
        self.p = subprocess.Popen([sys.executable, str(GATEWAY), "--port", "0", "--log", str(self.log), *extra],
                                  stdout=subprocess.PIPE, text=True)
        self.addCleanup(self.p.stdout.close)
        self.addCleanup(self.p.wait)
        self.addCleanup(self.p.kill)
        self.base = f"http://127.0.0.1:{self.p.stdout.readline().strip()}"

    def call(self, path, body=None, key=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {key}"} if key else {})})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode()
        return raw if raw.startswith("data:") else json.loads(raw)

    def test_models_and_model_info_name_the_aliases_and_what_is_behind_them(self):
        self.start()
        ids = [m["id"] for m in self.call("/v1/models")["data"]]
        self.assertEqual(ids, ["chat", "code", "embed"])
        info = {r["model_name"]: r for r in self.call("/model/info")["data"]}
        self.assertEqual(info["code"]["litellm_params"]["model"], "testbed/fake-code-large")
        self.assertEqual(info["embed"]["model_info"]["mode"], "embedding")

    def test_chat_answers_ok_plain_and_streamed(self):
        self.start()
        r = self.call("/v1/chat/completions", {"model": "chat", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(r["choices"][0]["message"]["content"], "ok")
        s = self.call("/v1/chat/completions", {"model": "code", "stream": True,
                                               "messages": [{"role": "user", "content": "hi"}]})
        self.assertIn('"content": "ok"', s)
        self.assertTrue(s.rstrip().endswith("data: [DONE]"))

    def test_run_tool_asks_for_one_bash_call_then_answers(self):
        self.start()
        msgs = [{"role": "user", "content": "please RUN-TOOL: echo hello"}]
        r = self.call("/v1/chat/completions", {"model": "chat", "messages": msgs, "tools": [{"type": "function"}]})
        call = r["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(json.loads(call["function"]["arguments"])["command"], "echo hello")
        msgs += [{"role": "assistant", "tool_calls": [call]}, {"role": "tool", "content": "hello"}]
        r = self.call("/v1/chat/completions", {"model": "chat", "messages": msgs, "tools": [{"type": "function"}]})
        self.assertEqual(r["choices"][0]["message"]["content"], "ok")

    def test_a_worker_brief_runs_its_steps_then_its_own_worker_done(self):
        self.start()
        brief = ("Your task ID is: task_1\nRUN-TOOL: git commit -qam x\nRUN-TOOL: git push -q\n  orca orchestration send "
                 '--from term_ab --type worker_done --subject "<short status>" --body "<3-sentence summary>" '
                 "--task-id task_1 --dispatch-id ctx_9 --outcome succeeded\n")
        msgs, cmds = [{"role": "user", "content": brief}], []
        for _ in range(5):
            r = self.call("/v1/chat/completions", {"model": "chat", "messages": msgs, "tools": [{"type": "function"}]})
            m = r["choices"][0]["message"]
            if not m.get("tool_calls"):
                break
            cmds.append(json.loads(m["tool_calls"][0]["function"]["arguments"])["command"])
            msgs += [{"role": "assistant", "tool_calls": m["tool_calls"]}, {"role": "tool", "content": "done"}]
        self.assertEqual(cmds[:2], ["git commit -qam x", "git push -q"])
        self.assertEqual(len(cmds), 3)
        self.assertTrue(cmds[2].startswith("orca orchestration send --from term_ab --type worker_done --subject "), cmds[2])
        self.assertTrue(cmds[2].endswith("--task-id task_1 --dispatch-id ctx_9 --outcome succeeded"), cmds[2])
        self.assertNotIn("<short status>", cmds[2])
        self.assertEqual(m["content"], "ok")

    def test_key_is_required_when_set_and_never_logged(self):
        self.start("--key", "sk-testbed-secret", "--alias", "chat=testbed/x")
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.call("/v1/models")
        self.assertEqual(e.exception.code, 401)
        self.assertEqual([m["id"] for m in self.call("/v1/models", key="sk-testbed-secret")["data"]], ["chat"])
        with self.assertRaises(urllib.error.HTTPError):
            self.call("/v1/chat/completions", {"model": "nope", "messages": []}, key="sk-testbed-secret")
        log = self.log.read_text()
        self.assertNotIn("sk-testbed-secret", log)
        self.assertEqual([json.loads(l)["auth"] for l in log.splitlines()][:2], ["missing", "ok"])


class TestbedPin(unittest.TestCase):
    def test_orca_and_the_base_image_are_pinned_in_one_file(self):
        """Replicable elsewhere (owner 2026-11-25): one pinned Orca release with its sha256, the base image by digest."""
        import re
        pin = json.loads((ROOT / "tests" / "testbed" / "orca-pin.json").read_text())
        self.assertRegex(pin["version"], r"^\d+\.\d+\.\d+$")
        self.assertRegex(pin["sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(pin["url"].startswith("https://github.com/") and f"v{pin['version']}/" in pin["url"], pin["url"])
        self.assertRegex(pin["base_image"], r"@sha256:[0-9a-f]{64}$")
        docker = (ROOT / "tests" / "testbed" / "image" / "Dockerfile").read_text()
        self.assertFalse(re.search(r"^FROM (?!\$\{BASE\})", docker, re.M), "a FROM that is not the pinned base")
        self.assertIn(pin["version"], (ROOT / "tests" / "testbed" / "README.md").read_text())


class TestbedIsOptIn(unittest.TestCase):
    def test_the_suite_never_runs_the_testbed(self):
        """The testbed needs docker and is opt-in: no unit test may call tests/testbed/run (owner 2026-11-25)."""
        for f in (ROOT / "tests").glob("test_*.py"):
            if f.name == Path(__file__).name:
                continue
            text = f.read_text()
            self.assertNotIn("testbed/run" + '"', text, f.name)
            self.assertNotIn("testbed/" + "run up", text, f.name)
        ci = "".join(p.read_text() for p in (ROOT / ".github").rglob("*.yml")) if (ROOT / ".github").exists() else ""
        self.assertNotIn("testbed/run", ci)

    def test_run_never_asks_for_privilege_or_mounts_the_owner(self):
        text = (ROOT / "tests" / "testbed" / "run").read_text()
        for bad in ("--privileged", "SYS_ADMIN", "--cap-add", "--network=host", '"host"', "docker.sock",
                    "/.ssh:", "/.config:", "/.orca:"):
            self.assertNotIn(bad, text, bad)
        self.assertIn('"--cap-drop", "ALL"', text)
        self.assertIn('"no-new-privileges"', text)


if __name__ == "__main__":
    unittest.main()
