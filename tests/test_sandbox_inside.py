"""SBX-2 task 6 (lc-35qn): the inside wrappers, core/sandbox-inside/wrap.py (git, orca, orca-ide, gh, bd,
lane-heavy, lane-enter), each run as a real process against a fake broker on a temp socket that records requests;
git push and fetch with real git in a temp clone."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "core" / "sandbox-inside" / "bin"
GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@example.com"]


def git(*args, cwd):
    return subprocess.run(["git", *GIT_ID, *map(str, args)], cwd=cwd, capture_output=True, text=True, check=True,
                          stdin=subprocess.DEVNULL).stdout.strip()


class FakeBroker:
    def __init__(self, path: Path, answer):
        self.path, self.answer, self.requests, self.open_slots = path, answer, [], 0
        self.s = socket.socket(socket.AF_UNIX)
        self.s.bind(str(path))
        self.s.listen(8)
        self.closed = threading.Event()
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            try:
                c, _ = self.s.accept()
            except OSError:
                return
            threading.Thread(target=self.one, args=(c,), daemon=True).start()

    def one(self, c):
        data = b""
        while b"\n" not in data:
            chunk = c.recv(65536)
            if not chunk:
                break
            data += chunk
        req = json.loads(data.split(b"\n")[0])
        self.requests.append(req)
        c.sendall(json.dumps(self.answer(req)).encode() + b"\n")
        if req["request"] == "heavy-slot":
            self.open_slots += 1
            while c.recv(4096):
                pass
            self.open_slots -= 1
            self.closed.set()
        c.close()

    def close(self):
        self.s.close()


class InsideCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.t = Path(tmp.name).resolve()
        self.sock = Path(tempfile.mkdtemp(prefix="sb", dir="/tmp")) / "b.sock"
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.sock.parent)]))
        self.answers = {}
        self.broker = FakeBroker(self.sock, self.answer)
        self.addCleanup(self.broker.close)
        self.outbox = self.t / "outbox"
        self.outbox.mkdir()
        self.inbox = self.t / "inbox"
        self.inbox.mkdir()
        self.home = self.t / "home"
        self.home.mkdir()
        self.env = {**testhome.env_for(self.home), "PATH": f"{BIN}:{os.environ['PATH']}",
                    "LANE_BROKER": str(self.sock), "LANE_OUTBOX": str(self.outbox), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@example.com"}

    def answer(self, req):
        a = self.answers.get(req["request"], {"ok": True, "output": f"did {req['request']}"})
        return a(req) if callable(a) else a

    def run_in(self, name, *args, cwd=None, input=None):
        return subprocess.run([str(BIN / name), *args], cwd=cwd or self.t, env=self.env, capture_output=True,
                              text=True, input=input, timeout=60)

    def ok(self, name, *args, **kw):
        r = self.run_in(name, *args, **kw)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return r

    def no(self, name, *args, says="", **kw):
        r = self.run_in(name, *args, **kw)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn(says, r.stderr)
        return r

    def last(self):
        return self.broker.requests[-1]


class OrcaGhBdTests(InsideCase):
    def test_orca_send_report_and_escalate(self):
        body = self.t / "body.txt"
        body.write_text("what I did\n")
        for name in ("orca", "orca-ide"):
            self.ok(name, "orchestration", "send", "--from", "term_x", "--type", "worker_done", "--subject", "done",
                    "--body-file", str(body), "--task-id", "task_abcdef1", "--dispatch-id", "ctx_abcdef1",
                    "--outcome", "succeeded", "--files-modified", "a.py,b.py")
            self.assertEqual(self.last(), {"request": "report", "task": "task_abcdef1", "dispatch": "ctx_abcdef1",
                                           "subject": "done", "body": "what I did\n", "outcome": "succeeded",
                                           "files": ["a.py", "b.py"]})
        self.ok("orca", "orchestration", "send", "--type=escalation", "--subject=blocked", "--body=why",
                "--task-id=task_abcdef1", "--dispatch-id=ctx_abcdef1")
        self.assertEqual(self.last()["request"], "escalate")
        self.ok("orca", "orchestration", "ask", "--question", "which?")
        self.assertEqual(self.last(), {"request": "ask", "question": "which?"})
        self.ok("orca", "orchestration", "check", "--terminal", "term_x")
        self.assertEqual(self.last(), {"request": "check"})
        self.ok("orca", "orchestration", "reply", "--id", "msg_1", "--body", "yes")
        self.assertEqual(self.last(), {"request": "reply", "id": "msg_1", "body": "yes"})

    def test_unknown_verb_refused(self):
        n = len(self.broker.requests)
        self.no("orca", "orchestration", "send", "--to", "bot:x", "--subject", "s", says="--to")
        self.no("orca", "orchestration", "send", "--type", "handoff", "--subject", "s", says="worker_done")
        self.no("orca", "orchestration", "worker-start", says="not available")
        self.no("orca", "terminal", "create", says="only")
        self.no("gh", "api", "repos/x/y", says="only")
        self.no("gh", "auth", "token", says="only")
        self.no("gh", "pr", "merge", says="not available")
        self.no("gh", "pr", "create", "--web", says="--web")
        self.no("gh", "pr", "view", "7", "--repo", "x/y", says="--repo")
        self.no("bd", "list", says="only")
        self.no("bd", "close", "app-1", says="only")
        self.no("bd", "update", "app-1", "--status", "closed", says="--status")
        self.assertEqual(len(self.broker.requests), n)

    def test_gh_mappings(self):
        self.ok("gh", "pr", "create", "--title", "SBX", "--body", "b")
        self.assertEqual(self.last(), {"request": "pr-create", "title": "SBX", "body": "b"})
        for verb in ("view", "checks", "diff"):
            self.ok("gh", "pr", verb, *(["--json", "number"] if verb == "view" else []))
            self.assertEqual(self.last(), {"request": f"pr-{verb}"})
        self.ok("gh", "pr", "comment", "--body-file", "-", input="from stdin")
        self.assertEqual(self.last(), {"request": "pr-comment", "body": "from stdin"})
        self.ok("gh", "pr", "edit", "--title", "new")
        self.assertEqual(self.last(), {"request": "pr-edit", "title": "new"})
        self.answers["run-view"] = {"ok": True, "output": "the failed step's log"}
        r = self.ok("gh", "run", "view", "123", "--log-failed")
        self.assertEqual(self.last(), {"request": "run-view", "run": "123"})
        self.assertIn("the failed step's log", r.stdout)

    def test_bd_mappings(self):
        self.ok("bd", "show", "app-1x")
        self.assertEqual(self.last(), {"request": "bd-show", "id": "app-1x"})
        self.ok("bd", "update", "app-1x", "--claim")
        self.assertEqual(self.last(), {"request": "bd-claim"})
        self.ok("bd", "update", "app-1x", "--append-notes", "found")
        self.assertEqual(self.last(), {"request": "bd-note", "text": "found"})
        self.ok("bd", "create", "--title", "t", "-d", "d", "-t", "bug", "-p", "P1")
        self.assertEqual(self.last(), {"request": "bd-create", "title": "t", "description": "d", "type": "bug",
                                       "priority": "1"})

    def test_refusal_is_one_line_and_fails(self):
        self.answers["pr-view"] = {"ok": False, "error": "the project has no GitHub repository"}
        r = self.no("gh", "pr", "view", says="no GitHub repository")
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)


class GitTests(InsideCase):
    def setUp(self):
        super().setUp()
        self.origin = self.t / "origin"
        git("init", "-q", "-b", "main", self.origin, cwd=self.t)
        (self.origin / "a").write_text("a\n")
        git("add", "a", cwd=self.origin)
        git("commit", "-q", "-m", "a", cwd=self.origin)
        self.clone = self.t / "clone"
        git("clone", "-q", "--no-local", self.origin, self.clone, cwd=self.t)
        git("checkout", "-q", "-b", "lane-1", cwd=self.clone)
        git("config", "remote.origin.url", "/nonexistent/lane-sandbox-origin", cwd=self.clone)

        def push(req):
            heads = subprocess.run(["git", "bundle", "list-heads", str(self.outbox / "push.bundle")],
                                   capture_output=True, text=True, cwd=self.clone).stdout.split()
            self.pushed = heads
            return {"ok": True, "sha": heads[0], "output": f"pushed {heads[0][:12]} to origin lane-1"}
        self.answers["push"] = push

    def commit(self, name):
        (self.clone / name).write_text(name + "\n")
        git("add", name, cwd=self.clone)
        git("commit", "-q", "-m", name, cwd=self.clone)
        return git("rev-parse", "HEAD", cwd=self.clone)

    def test_push_u_sets_upstream(self):
        sha = self.commit("work")
        r = self.ok("git", "push", "-u", "origin", "lane-1", cwd=self.clone)
        self.assertEqual(self.last(), {"request": "push"})
        self.assertEqual(self.pushed, [sha, "refs/heads/lane-1"])
        self.assertIn("pushed", r.stdout)
        self.assertEqual(git("rev-parse", "origin/lane-1", cwd=self.clone), sha)
        self.assertEqual(git("config", "branch.lane-1.remote", cwd=self.clone), "origin")
        self.assertEqual(git("config", "branch.lane-1.merge", cwd=self.clone), "refs/heads/lane-1")

    def test_bundled_short_flags(self):
        """r1 n1: git push -qu works like -q -u."""
        self.commit("work")
        r = self.ok("git", "push", "-qu", cwd=self.clone)
        self.assertEqual(r.stdout, "")
        self.assertEqual(git("config", "branch.lane-1.remote", cwd=self.clone), "origin")
        self.no("git", "push", "-qf", cwd=self.clone, says="-qf")  # a bundle with an unknown flag stays refused

    def test_status_ahead_behind_after_push(self):
        self.commit("one")
        self.ok("git", "push", "-u", cwd=self.clone)
        st = self.ok("git", "status", "-sb", cwd=self.clone).stdout
        self.assertIn("## lane-1...origin/lane-1", st)
        self.assertNotIn("ahead", st.splitlines()[0])
        self.commit("two")
        st = self.ok("git", "status", "-sb", cwd=self.clone).stdout
        self.assertIn("[ahead 1]", st)
        r = self.ok("git", "push", cwd=self.clone)
        self.assertEqual(self.pushed[0], git("rev-parse", "HEAD", cwd=self.clone))
        del r

    def test_push_refusals(self):
        self.commit("x")
        n = len(self.broker.requests)
        self.no("git", "push", "--force", cwd=self.clone, says="--force")
        self.no("git", "push", "origin", "+lane-1", cwd=self.clone, says="only the lane's branch")
        self.no("git", "push", "origin", ":main", cwd=self.clone, says="only the lane's branch")
        self.no("git", "push", "upstream", cwd=self.clone, says="origin only")
        self.no("git", "push", "origin", "main", cwd=self.clone, says="only the lane's branch")
        self.assertEqual(len(self.broker.requests), n)
        self.answers["push"] = {"ok": False, "error": "not a fast-forward"}
        self.no("git", "push", cwd=self.clone, says="not a fast-forward")
        self.assertNotIn("origin/lane-1", git("branch", "-r", cwd=self.clone))

    def test_nothing_to_push(self):
        git("update-ref", "refs/remotes/origin/lane-1", "HEAD", cwd=self.clone)
        r = self.ok("git", "push", cwd=self.clone)
        self.assertIn("up-to-date", r.stdout)
        self.assertEqual(self.broker.requests, [])

    def test_fetch_and_pull_merge_from_inbox(self):
        (self.origin / "a").write_text("updated\n")
        git("commit", "-q", "-am", "base moves", cwd=self.origin)
        bundle = self.inbox / "base.bundle"
        git("bundle", "create", "-q", bundle, "main", cwd=self.origin)
        tip = git("rev-parse", "main", cwd=self.origin)
        git("update-ref", "refs/remotes/origin/main", tip, cwd=self.origin)
        git("bundle", "create", "-q", bundle, "refs/remotes/origin/main", cwd=self.origin)
        self.answers["fetch"] = {"ok": True, "new": 1, "sha": tip, "bundle": str(bundle),
                                 "ref": "refs/remotes/origin/main"}
        self.commit("mine")
        self.ok("git", "pull", cwd=self.clone)
        self.assertEqual((self.clone / "a").read_text(), "updated\n")
        self.assertEqual(git("rev-parse", "origin/main", cwd=self.clone), tip)
        self.no("git", "pull", "--rebase", cwd=self.clone, says="--rebase is refused")
        self.no("git", "fetch", "https://example.com/x.git", cwd=self.clone, says="only from origin")

    def test_other_git_is_real(self):
        r = self.ok("git", "log", "--oneline", "-1", cwd=self.clone)
        self.assertIn("a", r.stdout)
        self.assertEqual(self.broker.requests, [])


class HeavyEnterTests(InsideCase):
    def test_heavy_slot_released_on_exit(self):
        r = self.ok("lane-heavy", sys.executable, "-c", "import os; print(os.environ['LANE_HEAVY_HOLDS_SLOT'])")
        self.assertEqual(r.stdout.strip(), "1")
        self.assertTrue(self.broker.closed.wait(5))
        self.assertEqual(self.broker.open_slots, 0)
        r = self.run_in("lane-heavy", sys.executable, "-c", "import sys; sys.exit(3)")
        self.assertEqual(r.returncode, 3)

    def test_enter_checks_out_a_no_checkout_clone(self):
        src = self.t / "src"
        git("init", "-q", "-b", "main", src, cwd=self.t)
        (src / "f").write_text("file\n")
        git("add", "f", cwd=src)
        git("commit", "-q", "-m", "f", cwd=src)
        c = self.t / "c"
        git("clone", "-q", "--no-checkout", "--no-local", src, c, cwd=self.t)
        self.assertFalse((c / "f").exists())
        self.no("lane-enter", sys.executable, "-c", "print('ran')", cwd=c, says="did not release")  # r1 M2
        (self.inbox / "release").write_text("tok-1")
        self.env["LANE_INBOX"], self.env["LANE_RELEASE"] = str(self.inbox), "tok-2"
        self.no("lane-enter", sys.executable, "-c", "print('ran')", cwd=c, says="did not release")
        self.env["LANE_RELEASE"] = "tok-1"
        r = self.ok("lane-enter", sys.executable, "-c", "print(open('f').read())", cwd=c)
        self.assertEqual(r.stdout.strip(), "file")
        (c / "f").write_text("changed\n")
        self.ok("lane-enter", sys.executable, "-c", "pass", cwd=c)
        self.assertEqual((c / "f").read_text(), "changed\n")  # an existing index: nothing reset


if __name__ == "__main__":
    unittest.main()
