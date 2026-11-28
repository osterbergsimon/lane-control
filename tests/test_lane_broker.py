"""SBX-2 task 5 (lc-35qn): the broker, core/scripts/lane-broker. Real git in temp repos for push and fetch (the
hostile outbox entries of review r2 B2 and the spike); Orca, gh and bd are a fake `run` that records every argv.
The serve loop runs as a real process on its socket for the size limit, the log and the slot."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
import contextlib
import importlib.machinery
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import sandbox_fixture as FX

ROOT = Path(__file__).resolve().parent.parent
BROKER = ROOT / "core" / "scripts" / "lane-broker"


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


LB = load("lc_lane_broker_t", BROKER)
SB = FX.SB
git = FX.git


class CP:
    def __init__(self, argv, rc=0, out="", err=""):
        self.args, self.returncode, self.stdout, self.stderr = argv, rc, out, err


class FakeRun:
    """git runs for real; every other CLI is recorded and answered by the first matching rule."""

    def __init__(self):
        self.calls, self.rules, self.git, self.cwds = [], [], [], []

    def on(self, match, rc=0, out=""):
        self.rules.insert(0, (match, rc, out))

    def __call__(self, argv, **kw):
        argv = [str(a) for a in argv]
        if Path(argv[0]).name == "git":
            self.git.append((argv, str(kw.get("cwd") or "")))
            return subprocess.run(argv, **kw)
        self.calls.append(argv)
        self.cwds.append(str(kw.get("cwd") or ""))
        for match, rc, out in self.rules:
            if match(argv):
                return CP(argv, rc, out(argv) if callable(out) else out)
        return CP(argv, 0, "{}")


def has(*words):
    return lambda argv: all(w in argv for w in words)


class BrokerCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.t = Path(tmp.name).resolve()
        self.fx = FX.Fixture(self.t)
        p = mock.patch.dict(os.environ, self.fx.env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.fx.prepare()
        self.ticket = self.fx.take("term_lane")
        self.clone = Path(self.ticket["clone"])
        self.outbox = Path(self.ticket["ticket_dir"]) / "run" / "outbox"
        self.run = FakeRun()

    def b(self, ticket=None, terminal="term_lane"):
        return LB.Broker(ticket or self.ticket, terminal, self.run)

    def req(self, request, ticket=None, terminal="term_lane", **f):
        return LB.handle({"request": request, **f}, ticket or self.ticket, terminal, self.run)

    def refused(self, request, says="", **kw):
        out = self.req(request, **kw)
        self.assertFalse(out["ok"], out)
        self.assertIn(says, out["error"])
        return out["error"]

    def lane_commit(self, name="lane-file", text="work\n"):
        (self.clone / name).write_text(text)
        git("add", name, cwd=self.clone)
        git("commit", "-q", "-m", f"lane: {name}", cwd=self.clone)
        return git("rev-parse", "HEAD", cwd=self.clone)

    def bundle(self, *refs, name="push.bundle", cwd=None):
        git("bundle", "create", "-q", self.outbox / name, *refs, cwd=cwd or self.clone)

    def refs(self):
        return (git("for-each-ref", cwd=self.fx.repo), git("for-each-ref", cwd=self.fx.origin))


class PushTests(BrokerCase):
    def test_push_honest_then_rewritten(self):
        first = self.lane_commit()
        self.bundle("lane-1", "^origin/main")
        out = self.req("push")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["sha"], first)
        self.assertEqual(git("rev-parse", "refs/lanes/app-1", cwd=self.fx.repo), first)
        self.assertEqual(git("rev-parse", "lane-1", cwd=self.fx.origin), first)
        second = self.lane_commit("more")
        self.bundle("lane-1", "^origin/main")
        self.assertEqual(self.req("push")["sha"], second)
        # Review Focus 3: a rewritten branch descends from the base but not from the last push
        git("reset", "-q", "--hard", "origin/main", cwd=self.clone)
        self.lane_commit("rewritten")
        self.bundle("lane-1", "^origin/main")
        before = self.refs()
        self.refused("push", says="not a fast-forward")
        self.assertEqual(self.refs(), before)

    def test_push_again_after_origin_refused(self):
        """r1 M1: origin refuses the first push (a pre-receive hook); the same push again really reaches origin."""
        sha = self.lane_commit()
        hook = self.fx.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.bundle("lane-1", "^origin/main")
        self.refused("push", says="push to origin failed: push again")
        hook.unlink()
        self.bundle("lane-1", "^origin/main")
        out = self.req("push")
        self.assertTrue(out["ok"], out)
        self.assertEqual(git("rev-parse", "lane-1", cwd=self.fx.origin), sha)

    def test_ci_workflow_with_a_quoted_name_refused(self):
        """r1 B1, through the broker: a workflow whose name git would quote."""
        (self.clone / ".github" / "workflows").mkdir(parents=True)
        self.lane_commit('.github/workflows/q"x.yml', "on: push\n")
        self.bundle("lane-1", "^origin/main")
        self.refused("push", says="CI configuration")
        self.assertNotEqual(subprocess.run(["git", "rev-parse", "-q", "--verify", "lane-1"], cwd=self.fx.origin,
                                           capture_output=True).returncode, 0)

    def test_push_hostile_outbox(self):
        self.lane_commit()
        wip, shared = self.fx.wip_sha, self.fx.repo
        o = self.outbox
        evil = o / "evil.git"
        git("init", "-q", "--bare", evil, cwd=self.t)
        (evil / "objects" / "info" / "alternates").write_text(f"{shared}/.git/objects\n")
        (evil / "refs" / "heads" / "lane-1").write_text(wip + "\n")
        entries = {}
        entries["a link to a lane-made repo"] = lambda p: p.symlink_to(evil)
        self.bundle("lane-1", "^origin/main", name="honest")
        entries["a link to a file"] = lambda p: p.symlink_to(o / "honest")
        pack = subprocess.run(["git", "pack-objects", "-q", "--stdout"], input=b"", capture_output=True,
                              cwd=self.clone).stdout
        entries["an id-only bundle naming the owner's unpushed commit"] = lambda p: p.write_bytes(
            f"# v2 git bundle\n{wip} refs/heads/lane-1\n\n".encode() + pack)
        entries["a FIFO"] = lambda p: os.mkfifo(p)
        entries["a 65 MB file"] = lambda p: open(p, "wb").truncate(65 << 20)
        extra = git("commit-tree", "-p", "HEAD", "-m", "extra", git("write-tree", cwd=self.clone), cwd=self.clone)
        git("update-ref", "refs/heads/extra", extra, cwd=self.clone)
        self.bundle("lane-1", "extra", "^origin/main", name="two")
        entries["two refs"] = lambda p: os.replace(o / "two", p)
        orphan = self.t / "orphan"
        git("init", "-q", "-b", "lane-1", orphan, cwd=self.t)
        git("commit", "-q", "--allow-empty", "-m", "orphan", cwd=orphan)
        entries["an orphan"] = lambda p: git("bundle", "create", "-q", p, "lane-1", cwd=orphan)
        entries["a directory"] = lambda p: p.mkdir()
        before = self.refs()
        for what, make in entries.items():
            p = o / "push.bundle"
            if p.is_dir() and not p.is_symlink():
                os.rmdir(p)
            elif p.exists() or p.is_symlink():
                p.unlink()
            make(p)
            out = self.req("push")
            self.assertFalse(out["ok"], what)
            self.assertEqual(self.refs(), before, what)
        self.assertNotEqual(subprocess.run(["git", "cat-file", "-e", wip], cwd=self.fx.origin).returncode, 0)

    def test_push_ci_change_needs_approval(self):
        self.lane_commit()
        (self.clone / ".github" / "workflows").mkdir(parents=True)
        sha = self.lane_commit(".github/workflows/x.yml", "on: push\n")
        self.lane_commit("after")  # a later commit hides nothing: every new commit is checked
        self.bundle("lane-1", "^origin/main")
        err = self.refused("push", says="CI configuration")
        tip = git("rev-parse", "HEAD", cwd=self.clone)
        self.assertIn(f"lane-control lane approve-ci app-1 {tip}", err)
        ok = Path(self.ticket["lane_dir"]) / "ci-approved"
        ok.mkdir()
        (ok / tip).write_text("owner\n")
        self.assertTrue(self.req("push")["ok"])
        del sha

    def test_push_never_runs_git_in_a_lane_path(self):
        marker = self.t / "ran-outside"
        hook = self.t / "hook.sh"
        hook.write_text(f"#!/bin/sh\necho ran >>{marker}\nexit 1\n")
        hook.chmod(0o755)
        self.lane_commit()
        self.bundle("lane-1", "^origin/main")
        # what the lane plants afterwards for any host git that would run in its clone
        git("config", "core.fsmonitor", str(hook), cwd=self.clone)
        git("config", "core.hooksPath", str(self.t), cwd=self.clone)
        (self.t / "pre-push").write_text(f"#!/bin/sh\necho hook >>{marker}\n")
        (self.t / "pre-push").chmod(0o755)
        evil = self.outbox / "evil.git"
        git("init", "-q", "--bare", evil, cwd=self.t)
        git("config", "-f", evil / "config", "core.fsmonitor", str(hook), cwd=self.t)
        self.assertTrue(self.req("push")["ok"])
        self.assertTrue(self.req("fetch")["ok"])
        self.assertFalse(marker.exists())
        lane_paths = (str(self.clone), str(self.outbox), str(Path(self.ticket["ticket_dir"]) / "run"))
        for argv, cwd in self.run.git:
            for w in [*argv, cwd]:
                self.assertFalse(any(w.startswith(p) for p in lane_paths), argv)


class TwoProjectsTests(BrokerCase):
    """r1 B2: the same lane key in two projects: each has its own clone, quarantine and base, and a push from the
    second reaches only its own origin with only its own history."""

    def test_same_key_two_projects(self):
        fx = self.fx
        pub = self.t / "code" / "pub"
        pub_origin = self.t / "pub-origin.git"
        git("init", "-q", "--bare", "-b", "main", pub_origin, cwd=self.t)
        pub.mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=pub)
        (pub / "pub.txt").write_text("public\n")
        git("add", ".", cwd=pub)
        git("commit", "-q", "-m", "pub base", cwd=pub)
        git("remote", "add", "origin", pub_origin, cwd=pub)
        git("push", "-q", "origin", "main", cwd=pub)
        git("fetch", "-q", "origin", cwd=pub)
        wt = self.t / "orca" / "workspaces" / "pub" / "lane-1"
        git("worktree", "add", "-q", "-b", "lane-1", wt, "main", cwd=pub)
        store = FX.X.store_for(pub)
        store.mkdir(parents=True)
        st = {"root": str(pub), "name": "pub", "orchestrator": "native-orca"}
        (store / "settings.json").write_text(json.dumps(st))
        (fx.home / ".config" / "lane-control" / "machine.conf").write_text("SANDBOX_PROJECTS='app pub'\n")
        ps = fx.patched()
        for p in ps[1:]:
            p.start()
        try:
            FX.SP.prepare(pub, st, wt.resolve(), "lanes", "app-1", "pub-1", say=lambda *_: None)
        finally:
            for p in reversed(ps[1:]):
                p.stop()
        t2 = SB.take_ticket(wt.resolve(), "term_pub")
        clone2 = Path(t2["clone"])
        self.assertNotEqual(clone2, self.clone)
        self.assertTrue((clone2 / "pub.txt").exists())
        self.assertFalse((clone2 / "src.txt").exists())  # not app's files
        (clone2 / "w").write_text("w\n")
        git("add", "w", cwd=clone2)
        git("commit", "-q", "-m", "pub lane", cwd=clone2)
        out2 = Path(t2["ticket_dir"]) / "run" / "outbox"
        git("bundle", "create", "-q", out2 / "push.bundle", "lane-1", "^origin/main", cwd=clone2)
        r = LB.handle({"request": "push"}, t2, "term_pub", self.run)
        self.assertTrue(r["ok"], r)
        self.assertNotEqual(subprocess.run(["git", "cat-file", "-e", self.fx.base], cwd=pub_origin).returncode, 0)
        self.assertEqual(git("rev-parse", "lane-1~1", cwd=pub_origin), git("rev-parse", "main", cwd=pub))
        # a lane folder whose record names another repository is refused, never resumed
        rec = Path(t2["lane_dir"]) / "lane.json"
        d = json.loads(rec.read_text())
        rec.write_text(json.dumps({**d, "repo": str(fx.repo)}))
        for p in ps[1:]:
            p.start()
        try:
            with self.assertRaises(SB.SandboxError) as e:
                FX.SP.prepare(pub, st, wt.resolve(), "lanes", "app-1", "pub-1", say=lambda *_: None)
        finally:
            for p in reversed(ps[1:]):
                p.stop()
        self.assertIn("another repository", str(e.exception))


class FetchTests(BrokerCase):
    def push_base(self, path, text="x\n", side=False):
        w = self.t / "w"
        git("clone", "-q", "--no-local", self.fx.origin, w, cwd=self.t)
        if side:
            git("switch", "-q", "-c", "side", cwd=w)
        f = w / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        git("add", "-f", path, cwd=w)
        git("commit", "-q", "-m", "c", cwd=w)
        if side:
            git("rm", "-q", path, cwd=w)
            git("commit", "-q", "-m", "rm", cwd=w)
            git("switch", "-q", "main", cwd=w)
            git("merge", "-q", "--no-ff", "--no-edit", "side", cwd=w)
        git("push", "-q", "origin", "main", cwd=w)
        import shutil
        shutil.rmtree(w)
        return git("rev-parse", "main", cwd=self.fx.origin)

    def test_fetch_delivers_new_base(self):
        self.assertEqual(self.req("fetch")["new"], 0)
        tip = self.push_base("orca.yaml", "updated\n")
        out = self.req("fetch")
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["sha"], out["new"]), (tip, 1))
        inbox = Path(self.ticket["ticket_dir"]) / "run" / "inbox" / "base.bundle"
        git("fetch", "-q", inbox, "refs/remotes/origin/main:refs/remotes/origin/main", cwd=self.clone)
        git("merge", "-q", "--no-edit", "origin/main", cwd=self.clone)
        self.assertEqual((self.clone / "orca.yaml").read_text(), "updated\n")
        self.assertEqual(git("rev-parse", "refs/heads/main", cwd=Path(self.ticket["lane_dir"]) / "quarantine.git"),
                         tip)

    def test_fetch_checks_history(self):
        tip = self.push_base("orca.yaml", "updated\n")
        self.assertTrue(self.req("fetch")["ok"])
        inbox = Path(self.ticket["ticket_dir"]) / "run" / "inbox" / "base.bundle"
        kept = inbox.read_bytes()
        self.push_base("fixtures/customers/b.csv", side=True)  # Review Focus 2: added and removed in a merge
        self.refused("fetch", says="fixtures/customers/b.csv")
        self.assertEqual(inbox.read_bytes(), kept)
        self.assertEqual((Path(self.ticket["lane_dir"]) / "delivered").read_text().strip(), tip)


class OrcaTests(BrokerCase):
    def dispatch(self, assignee, did="ctx_abcdef1"):
        self.run.on(has("dispatch-show"), out=json.dumps({"ok": True, "result": {"dispatch": {
            "id": did, "assignee_handle": assignee, "task_id": "task_abcdef1"}}}))

    def test_report_checks_dispatch(self):
        self.dispatch("term_other")
        err = self.refused("report", says="not this terminal's", task="task_abcdef1", dispatch="ctx_abcdef1",
                           subject="done", body="x", outcome="succeeded")
        for x in ("task task_abcdef1", "dispatch ctx_abcdef1", "dispatch-show --task=task_abcdef1"):
            self.assertIn(x, err)
        self.assertFalse(any("send" in c for c in self.run.calls))
        self.dispatch("term_lane", did="ctx_followup1")  # a follow-up dispatch on this terminal
        out = self.req("report", task="task_abcdef1", dispatch="ctx_followup1", subject="done", body="three lines",
                       outcome="succeeded", files=["core/x.py"])
        self.assertTrue(out["ok"], out)
        send = [c for c in self.run.calls if "send" in c][-1]
        self.assertIn("--from=term_lane", send)
        self.assertIn("--type=worker_done", send)
        self.assertIn("--dispatch-id=ctx_followup1", send)
        body = next(a for a in send if a.startswith("--body-file="))[12:]
        self.assertTrue(body.startswith(self.ticket["ticket_dir"]))
        self.assertEqual(Path(body).read_text(), "three lines")
        self.assertEqual(os.stat(body).st_mode & 0o777, 0o600)

    def test_no_path_or_recipient_fields(self):
        base = {"task": "task_abcdef1", "dispatch": "ctx_abcdef1", "subject": "s", "body": "b",
                "outcome": "succeeded"}
        for extra in ("to", "body_file", "file", "environment", "on", "argv", "path", "repo", "head"):
            self.refused("report", says="unknown field", **{**base, extra: "x"})
        self.refused("pr-create", says="unknown field", title="t", body="b", repo="evil/repo")
        self.refused("pr-view", says="unknown field", number="7")
        self.refused("send", says="unknown request")
        self.refused("gh-api", says="unknown request")
        self.assertEqual(self.run.calls, [])

    def test_value_as_equals_form(self):
        self.dispatch("term_lane")
        self.req("report", task="task_abcdef1", dispatch="ctx_abcdef1", subject="--to=bot:x", body="--file=/etc",
                 outcome="succeeded")
        self.req("ask", question="--to run:x?")
        self.run.on(has("pr", "create"), out="https://github.com/owner/app/pull/1")
        self.req("pr-create", title="--web", body="x")
        for c in self.run.calls:
            for a in c[1:]:
                self.assertTrue(a.startswith("--") and "=" in a or a in ("orchestration", "send", "ask",
                                                                         "dispatch-show", "pr", "create", "--json"), c)
            self.assertNotIn("--to", [a.split("=")[0] for a in c])
            self.assertNotIn("--web", [a.split("=")[0] for a in c])

    def test_reply_only_seen_ids(self):
        self.refused("reply", says="check returned", id="msg_abc123", body="x")
        self.run.on(has("check"), out=json.dumps({"ok": True, "result": {"messages": [{"id": "msg_abc123"}]}}))
        self.assertTrue(self.req("check")["ok"])
        check = [c for c in self.run.calls if "check" in c][-1]
        self.assertIn("--terminal=term_lane", check)
        self.assertTrue(self.req("reply", id="msg_abc123", body="answer")["ok"])
        self.refused("reply", says="check returned", id="msg_zzz999", body="x")


class GhTests(BrokerCase):
    def test_gh_own_pr_only(self):
        for r, extra in (("pr-view", {}), ("pr-checks", {}), ("pr-diff", {}), ("pr-comment", {"body": "b"}),
                         ("pr-edit", {"title": "t"})):
            self.assertTrue(self.req(r, **extra)["ok"], r)
        for c in self.run.calls:
            self.assertEqual(c[:2], ["gh", "pr"])
            self.assertEqual(c[3], "lane-1")
            self.assertIn("--repo=owner/app", c)
            self.assertFalse({"--web", "--editor", "--attach", "--template", "--recover", "api"} & set(c))
        self.refused("pr-edit", says="title or a body")

    def test_run_view_own_branch_only(self):
        self.run.on(has("run", "view", "--json=headBranch"), out=json.dumps({"headBranch": "main"}))
        self.refused("run-view", says="not of the lane's branch", run="123")
        self.run.on(has("run", "view", "--json=headBranch"), out=json.dumps({"headBranch": "lane-1"}))
        self.run.on(has("--log-failed"), out="failing step")
        self.assertEqual(self.req("run-view", run="123")["output"], "failing step")
        self.refused("run-view", says="pattern", run="123 --repo=x/y")


class BdTests(BrokerCase):
    def test_bd_show_lane_issue_and_links_only(self):
        self.run.on(has("show", "--id=app-1x", "--json"), out=json.dumps([{
            "id": "app-1x", "dependencies": [{"id": "app-9", "dependency_type": "discovered-from"}],
            "dependents": [{"id": "app-1x.1", "dependency_type": "parent-child"}]}]))
        for i in ("app-1x", "app-9", "app-1x.1"):
            self.assertTrue(self.req("bd-show", id=i)["ok"], i)
        self.refused("bd-show", says="links", id="app-2")
        for bad in ("--db=/tmp/x", "-C=/tmp", "--directory=/tmp", "lc-1", "app-1 --db=x", "app-1x;rm"):
            self.refused("bd-show", says="issue ids", id=bad)
        shows = [c for c in self.run.calls if c[:2] == ["bd", "show"]]
        self.assertTrue(all(c[2].startswith("--id=") for c in shows))
        # r1 B3: bd runs in the project's own checkout (its .beads), never in the clone
        self.assertEqual({cwd for c, cwd in zip(self.run.calls, self.run.cwds) if c[0] == "bd"}, {str(self.fx.repo)})

    def test_bd_writes_own_issue(self):
        self.assertTrue(self.req("bd-note", text="found x")["ok"])
        self.assertTrue(self.req("bd-claim")["ok"])
        self.assertTrue(self.req("bd-create", title="t", description="d", type="bug", priority="1")["ok"])
        self.assertEqual(self.run.calls[0], ["bd", "update", "app-1x", "--append-notes=found x"])
        self.assertIn("--deps=discovered-from:app-1x", self.run.calls[2])
        for r in ("bd-list", "bd-close", "bd-delete", "bd-remember"):
            self.refused(r, says="unknown request")


class ReviewTests(BrokerCase):
    def setUp(self):
        super().setUp()
        self.reviews = self.t / "reviews"
        self.reviews.mkdir()
        (self.fx.home / ".config" / "lane-control" / "machine.conf").write_text(
            f"SANDBOX_PROJECTS=app\nSANDBOX_REVIEW_DIR={self.reviews}\n")
        (self.reviews / "app-1-r1.md").write_text("findings\n")
        (self.reviews / "other-lane.md").write_text("another lane's review\n")

    def test_review_read_named_only(self):
        t = {**self.ticket, "review_files": ["app-1-r1.md"]}
        self.assertEqual(self.req("review-read", ticket=t, name="app-1-r1.md")["output"], "findings\n")
        self.refused("review-read", ticket=t, says="prepare named", name="other-lane.md")
        (self.reviews / "link.md").symlink_to(self.reviews / "other-lane.md")
        self.refused("review-read", ticket={**t, "review_files": ["link.md"]}, says="link", name="link.md")
        self.refused("review-read", ticket=t, says="pattern", name="../x.md")

    def test_review_write_once(self):
        t = {**self.ticket, "role": "reviewer", "review_write": "app-1-r2.md"}
        self.assertTrue(self.req("review-write", ticket=t, name="app-1-r2.md", text="r2")["ok"])
        self.refused("review-write", ticket=t, says="written once", name="app-1-r2.md", text="again")
        self.refused("review-write", ticket=t, says="one name", name="app-1-r1.md", text="x")
        self.assertEqual((self.reviews / "app-1-r1.md").read_text(), "findings\n")


class RoleTests(BrokerCase):
    def test_roles(self):
        rev = {**self.ticket, "role": "reviewer"}
        for r in ("push", "pr-create", "pr-comment", "pr-edit", "bd-note", "bd-claim", "bd-create"):
            self.refused(r, ticket=rev, says="not for a reviewer")
        chk = {**self.ticket, "role": "check"}
        for r in LB.FIELDS:
            if r != "heavy-slot":
                self.refused(r, ticket=chk, says="not for a check")
        self.refused("review-write", says="not for a lane", name="x.md", text="x")


class ServeTests(BrokerCase):
    def serve(self):
        p = subprocess.Popen([sys.executable, str(BROKER), "serve", self.ticket["ticket_file"]], env=self.fx.env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.addCleanup(lambda: (p.kill(), p.wait(), p.stderr.close()))
        sock = Path(self.ticket["ticket_dir"]) / "run" / "broker.sock"
        for _ in range(200):
            if sock.exists():
                break
            time.sleep(0.02)
        self.assertTrue(sock.exists(), p.stderr.read1() if p.poll() is not None else "no socket")
        self.assertEqual(os.stat(sock).st_mode & 0o777, 0o600)
        return sock

    @contextlib.contextmanager
    def conn(self, sock):
        here = os.getcwd()
        os.chdir(sock.parent)
        try:
            c = socket.socket(socket.AF_UNIX)
            c.connect("broker.sock")
        finally:
            os.chdir(here)
        try:
            yield c
        finally:
            c.close()

    def ask(self, sock, raw: bytes):
        with self.conn(sock) as c:
            try:
                c.sendall(raw)
                c.shutdown(socket.SHUT_WR)
            except BrokenPipeError:  # the broker stopped reading at its cap and answered
                pass
            data = b""
            while chunk := c.recv(65536):
                data += chunk
        return json.loads(data)

    def test_size_limit_and_log(self):
        sock = self.serve()
        big = json.dumps({"request": "bd-note", "text": "x" * (2 << 20)}).encode() + b"\n"
        out = self.ask(sock, big)
        self.assertFalse(out["ok"])
        self.assertIn("over", out["error"])
        self.assertFalse(self.ask(sock, b"{not json\n")["ok"])
        self.assertFalse(self.ask(sock, json.dumps({"request": "push", "argv": ["x"]}).encode() + b"\n")["ok"])
        log = (Path(self.ticket["ticket_dir"]) / "broker" / "requests.log").read_text().splitlines()
        self.assertEqual(len(log), 3)
        self.assertNotIn("xxxx", "".join(log))  # names and outcomes, never a value
        self.assertEqual([json.loads(x)["request"] for x in log], ["?", "?", "push"])

    def test_every_request_logged(self):
        sock = self.serve()
        for r in ({"request": "fetch"}, {"request": "bd-show", "id": "--db=/x"}, {"request": "nope"}):
            self.ask(sock, json.dumps(r).encode() + b"\n")
        log = [json.loads(x) for x in
               (Path(self.ticket["ticket_dir"]) / "broker" / "requests.log").read_text().splitlines()]
        self.assertEqual([x["request"] for x in log], ["fetch", "bd-show", "nope"])
        self.assertEqual(log[0]["outcome"], "ok")
        self.assertTrue(log[1]["outcome"].startswith("refused"))

    def test_one_slot_per_ticket_and_a_connection_cap(self):
        """r1 m9: a second slot from the same ticket is refused; at most MAX_CONNECTIONS at once; a silent
        connection times out."""
        sock = self.serve()
        with self.conn(sock) as c:
            c.sendall(json.dumps({"request": "heavy-slot"}).encode() + b"\n")
            self.assertTrue(json.loads(c.recv(4096))["ok"])
            out = self.ask(sock, json.dumps({"request": "heavy-slot"}).encode() + b"\n")
            self.assertFalse(out["ok"])
            self.assertIn("one at a time", out["error"])
        held = []
        for _ in range(LB.MAX_CONNECTIONS):
            with self.conn(sock) as c:
                held.append(c.detach())
        time.sleep(0.3)
        out = self.ask(sock, json.dumps({"request": "fetch"}).encode() + b"\n")
        self.assertFalse(out["ok"])
        self.assertIn("at once", out["error"])
        for fd in held:
            os.close(fd)
        time.sleep(0.5)
        self.assertTrue(self.ask(sock, json.dumps({"request": "fetch"}).encode() + b"\n")["ok"])

    def test_heavy_slot_held_while_open(self):
        sock = self.serve()
        state = Path(self.fx.env["LANE_STATE_DIR"])
        with self.conn(sock) as c:
            c.sendall(json.dumps({"request": "heavy-slot"}).encode() + b"\n")
            self.assertTrue(json.loads(c.recv(4096))["ok"])
            import fcntl
            fd = os.open(state / "heavy.0", os.O_RDONLY)
            with self.assertRaises(OSError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.close(fd)
        time.sleep(0.3)
        fd = os.open(state / "heavy.0", os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # freed once the lane's side closed
        os.close(fd)


if __name__ == "__main__":
    unittest.main()
