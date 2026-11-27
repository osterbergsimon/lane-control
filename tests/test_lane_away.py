"""lane-improve (on, off, drained, status), the improve state (core/improve.py) and what reads it (away-improve mode, lc-yzb). Temp HOME
and temp git repos; no real away file, improve file, branch or repo is touched."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "core"))
import improve  # noqa: E402
from test_machine_setup import MachineTest, write_exec  # noqa: E402


# The summary PR model both gh stubs share (lc-2nz): $GH_SUMMARY is a JSON file {"next": n, "prs": {n: {...}}}, kept per
# checkout (cwd) so two projects with the same branch name stay apart. GH_CREATE_FAIL / GH_EDIT_FAIL make that call fail.
GH_SUMMARY_BLOCK = r'''
sf = os.environ.get("GH_SUMMARY")
if sf and a[:3] == ["api", "--method", "PATCH"] and "/pulls/" in a[3]:  # the body over REST (field report B6)
    db = json.load(open(sf)) if os.path.exists(sf) else {"next": 100, "prs": {}}
    n = a[3].rsplit("/", 1)[1]
    if a[3] != "repos/{owner}/{repo}/pulls/" + n or n not in db["prs"]:
        print("gh: Not Found (HTTP 404)", file=sys.stderr); sys.exit(1)
    if os.environ.get("GH_EDIT_FAIL") or os.environ.get("GH_PATCH_FAIL"):
        print(os.environ.get("GH_PATCH_FAIL") or os.environ["GH_EDIT_FAIL"], file=sys.stderr); sys.exit(1)
    db["prs"][n]["body"] = json.load(open(a[a.index("--input") + 1], encoding="utf-8"))["body"]
    json.dump(db, open(sf, "w")); sys.exit(0)
if sf and a[:1] == ["pr"]:
    db = json.load(open(sf)) if os.path.exists(sf) else {"next": 100, "prs": {}}
    def save():
        json.dump(db, open(sf, "w"))
    def val(flag):
        if flag == "--body" and "--body-file" in a:  # a body goes through a file, never argv
            return open(a[a.index("--body-file") + 1], encoding="utf-8").read()
        return a[a.index(flag) + 1] if flag in a else None
    if "--body" in a:
        print("gh stub: a body must go through --body-file", file=sys.stderr); sys.exit(1)
    if a[:2] == ["pr", "list"] and "--head" in a:
        if os.environ.get("GH_LIST_SHAPE") == "object":
            print("{}"); sys.exit(0)
        if os.environ.get("GH_LIST_SHAPE") == "junk":
            print("not json at all"); sys.exit(0)
        # like gh: --head matches the branch NAME only, a fork's PR with that name included
        print(json.dumps([{"number": int(n), "url": p["url"], "isCrossRepository": p.get("cross", False),
                           "baseRefName": p["base"], "isDraft": p["draft"], "body": p["body"], "headRepositoryOwner": {"login": "someone" if p.get("cross") else "acme"}}
                          for n, p in db["prs"].items()
                          if p["head"] == val("--head") and p["cwd"] == os.getcwd() and p["state"] == "OPEN"])); sys.exit(0)
    if a[:2] == ["pr", "create"]:
        if os.environ.get("GH_CREATE_FAIL"):
            print(os.environ["GH_CREATE_FAIL"], file=sys.stderr); sys.exit(1)
        if "--draft" not in a:
            print("gh stub: the summary PR must be a draft", file=sys.stderr); sys.exit(1)
        n = db["next"]; db["next"] = n + 1
        url = f"https://github.com/acme/shop/pull/{n}"
        db["prs"][str(n)] = {"head": val("--head"), "base": val("--base"), "title": val("--title"), "body": val("--body"),
                             "draft": True, "state": "OPEN", "url": url, "cwd": os.getcwd()}
        save(); print(url); sys.exit(0)
    if a[:2] == ["pr", "edit"] and a[2] in db["prs"]:
        if os.environ.get("GH_EDIT_FAIL"):
            print(os.environ["GH_EDIT_FAIL"], file=sys.stderr); sys.exit(1)
        db["prs"][a[2]]["body"] = val("--body"); save(); sys.exit(0)
    if a[:2] == ["pr", "view"] and len(a) > 2 and a[2] in db["prs"]:
        if os.environ.get("GH_VIEW_SHAPE") == "list":
            print("[]"); sys.exit(0)
        p = db["prs"][a[2]]
        print(json.dumps({"state": p["state"], "url": p["url"], "baseRefName": p["base"], "headRefName": p["head"],
                          "isDraft": p["draft"], "isCrossRepository": p.get("cross", False), "body": p["body"]})); sys.exit(0)
'''

GH_BASES_STUB = """
#!/usr/bin/env python3
# gh stand-in: `gh pr list --state merged ... --json baseRefName`, answered from $GH_BASES (a JSON list of bases), and the
# summary PR calls (pr list --head, pr create, pr edit, pr view), answered from $GH_SUMMARY.
import json, os, sys
a = sys.argv[1:]
open(os.environ["GH_LOG"], "a").write(" ".join(a).replace("\\n", " / ") + "\\n")
""" + GH_SUMMARY_BLOCK + """
if a[:2] == ["repo", "view"]:
    print("PUBLIC"); sys.exit(0)
if a[:2] == ["pr", "list"] and "baseRefName" in a:
    bases = json.load(open(os.environ["GH_BASES"])) if os.path.exists(os.environ["GH_BASES"]) else None
    if bases is None:
        print("gh: not logged in", file=sys.stderr); sys.exit(1)
    print(json.dumps([{"baseRefName": b} for b in bases])); sys.exit(0)
print("gh stub: not modelled: " + " ".join(a), file=sys.stderr); sys.exit(1)
"""


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          capture_output=True, text=True, check=True).stdout


def make_repo(root: Path, name: str, branch: str = "main") -> Path:
    """<root>/<name>.git (bare origin) and <root>/<name> (its clone, origin/HEAD set) with one commit on `branch`."""
    origin = root / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", branch, str(origin)], check=True)
    clone = root / name
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
    git(clone, "checkout", "-q", "-B", branch)
    (clone / "README").write_text("x\n")
    git(clone, "add", "README")
    git(clone, "commit", "-qm", "first")
    git(clone, "push", "-q", "origin", branch)
    git(clone, "remote", "set-head", "origin", branch)
    return clone


class PresenceUntilBackTests(MachineTest):
    """`lane-away on --until-back`: the away file has no 14 h expiry until `lane-away off`."""

    def away(self):
        return self.conf_dir / "away"

    def test_until_back_does_not_expire(self):
        import os, time
        r = self.run_script("lane-away", "on", "--until-back")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("until you run `lane-away off`", r.stdout)
        t = time.time() - 60 * 3600
        os.utime(self.away(), (t, t))
        v = self.lane_limits("LANE_OWNER_AWAY", "LANE_PRESENCE_WHY")
        self.assertEqual((v["LANE_OWNER_AWAY"], v["LANE_PRESENCE_WHY"]), ("1", "owner away"))
        s = self.run_script("lane-away", "status").stdout
        self.assertIn("until back", s)
        self.assertIn("owner away: yes", s)
        self.assertNotIn("expired", s)
        self.assertEqual(self.run_script("lane-away", "off").returncode, 0)
        self.assertFalse(self.away().exists())
        self.assertEqual(self.lane_limits("LANE_OWNER_AWAY")["LANE_OWNER_AWAY"], "")

    def test_a_plain_on_goes_back_to_the_timed_away(self):
        import os, time
        self.run_script("lane-away", "on", "--until-back")
        self.run_script("lane-away", "on")
        self.assertEqual(self.away().read_text(), "")
        t = time.time() - 15 * 3600
        os.utime(self.away(), (t, t))
        self.assertEqual(self.lane_limits("LANE_OWNER_AWAY")["LANE_OWNER_AWAY"], "")  # 14 h passed again
        self.assertIn("expired", self.run_script("lane-away", "status").stdout)

    def test_a_plain_away_file_still_expires_and_counts(self):
        r = self.run_script("lane-away", "on")
        self.assertIn("away for", r.stdout)
        self.assertEqual(self.lane_limits("LANE_OWNER_AWAY")["LANE_OWNER_AWAY"], "1")

    def test_other_options_are_refused(self):
        for args in (("--hours", "3"), ("--until", "10:00"), ("--limits", "x"), ("--until-back", "--hours", "2")):
            self.assertEqual(self.run_script("lane-away", "on", *args).returncode, 2, args)
        self.assertFalse(self.away().exists())


ENTRY = (
    "## What\nAdds x.\n\n## Why\nUsers asked.\n\n## Builds on\nnothing\n\n## Existing work checked\nLooked in lib/ and the "
    "issues: no x.\n\n## How to try it\nRun `x --help`.\n\n## How to drop it\nRevert the merge commit.\n\n## Tests\nSuite green.\n")


class ImproveTest(MachineTest):
    def setUp(self):
        super().setUp()
        self.shop = make_repo(self.tmp, "shop")
        self.blog = make_repo(self.tmp, "blog")
        write_exec(self.bin / "gh", GH_BASES_STUB)
        self.env.update(GH_LOG=str(self.tmp / "gh.log"), GH_BASES=str(self.tmp / "gh-bases.json"),
                        GH_SUMMARY=str(self.tmp / "gh-summary.json"),
                        GIT_AUTHOR_NAME="Away Tester", GIT_AUTHOR_EMAIL="away@example.invalid",
                        GIT_COMMITTER_NAME="Away Tester", GIT_COMMITTER_EMAIL="away@example.invalid")
        self.set_bases(["main", "main"])
        self.machine_conf('COORDINATORS="\nshop %s/shop claude shop coordinator\nblog %s/blog claude blog coordinator\n"\n'
                          % (self.tmp, self.tmp))

    def set_bases(self, bases):
        (self.tmp / "gh-bases.json").write_text(json.dumps(bases))

    def remote_branches(self, repo: Path) -> list:
        out = git(repo, "ls-remote", "--heads", "origin", "away/*")
        return sorted(l.split("refs/heads/")[1] for l in out.splitlines())

    def away(self) -> Path:
        return self.conf_dir / "away"

    def improve_file(self) -> Path:
        return self.conf_dir / "improve.json"

    def drain_file(self) -> Path:
        return self.conf_dir / "improve-drain.json"

    def state(self) -> dict:
        return json.loads(self.improve_file().read_text())

    def reset(self):
        """Back to no improve period at all: ended, wrapped up, and the owner has dropped the away branches (a new `on` is
        refused while a period is on or draining, or a project still has its away branch)."""
        self.run_script("lane-improve", "off")
        self.run_script("lane-improve", "drained", "--all")
        self.drop_branches()

    def drop_branches(self):
        for repo in (self.shop, self.blog):
            for b in self.remote_branches(repo):
                git(repo, "push", "-q", "origin", "--delete", b)

    def on(self, *args, env=None):
        return self.run_script("lane-improve", "on", "--yes", *args, env=env)

    def db(self) -> dict:
        f = self.tmp / "gh-summary.json"
        return json.loads(f.read_text())["prs"] if f.exists() else {}

    def gh_log(self) -> list:
        f = self.tmp / "gh.log"
        return f.read_text().splitlines() if f.exists() else []

    def merge_item(self, repo: Path, branch: str, title: str = "Add the x feature", pr: int = 12, entry: str = ENTRY) -> str:
        """An item merged into the project's away branch on origin, as GitHub's merge button makes it (the item branch is a
        side commit; the message is `away: <title>`, `Pull request #<n>` and the entry): returns the short sha."""
        work = self.tmp / f"work-{repo.name}"
        if not work.exists():
            subprocess.run(["git", "clone", "-q", str(self.tmp / f"{repo.name}.git"), str(work)], check=True, capture_output=True)
        git(work, "fetch", "-q", "origin")
        git(work, "checkout", "-q", branch)
        git(work, "reset", "-q", "--hard", f"origin/{branch}")
        git(work, "checkout", "-q", "-b", f"item-{pr}", branch)
        (work / f"item-{pr}.txt").write_text("x\n")
        git(work, "add", f"item-{pr}.txt")
        git(work, "commit", "-qm", title)
        git(work, "checkout", "-q", branch)
        git(work, "merge", "-q", "--no-ff", "-m", f"away: {title}\n\nPull request #{pr}\n\n{entry}", f"item-{pr}")
        git(work, "push", "-q", "origin", branch)
        return git(work, "rev-parse", "--short", "HEAD").strip()

    def assert_cut_from(self, repo: Path, branch: str, integration: str = "main", date: str = ""):
        """The branch is the integration head plus ONE commit, `away: start <date>`, that is empty: no file is added."""
        git(repo, "fetch", "-q", "origin")
        b, i = f"origin/{branch}", f"origin/{integration}"
        self.assertEqual(git(repo, "rev-list", f"{i}..{b}").split(), [git(repo, "rev-parse", b).strip()])
        self.assertEqual(git(repo, "rev-parse", f"{b}^").strip(), git(repo, "rev-parse", i).strip())
        self.assertEqual(git(repo, "diff", "--name-status", i, b).split(), [])
        self.assertEqual(git(repo, "rev-parse", f"{b}^{{tree}}").strip(), git(repo, "rev-parse", f"{i}^{{tree}}").strip())
        self.assertEqual(git(repo, "log", "-1", "--format=%s", b).strip(), f"away: start {date or branch.split('/')[1]}")

    def add_pr(self, repo: Path, head: str, base: str = "main", cross: bool = False, draft: bool = True) -> str:
        """A PR the stub already has (someone else's, or the owner's) for `repo`: returns its number."""
        f = self.tmp / "gh-summary.json"
        db = json.loads(f.read_text()) if f.exists() else {"next": 100, "prs": {}}
        n = db["next"]
        db["next"] = n + 1
        db["prs"][str(n)] = {"head": head, "base": base, "title": "theirs", "body": "THEIR BODY", "draft": draft,
                             "state": "OPEN", "url": f"https://github.com/acme/shop/pull/{n}", "cwd": str(repo), "cross": cross}
        f.write_text(json.dumps(db))
        return str(n)

    def summary_prs(self, repo: Path) -> dict:
        return {n: p for n, p in self.db().items() if p["cwd"] == str(repo)}


class ImproveOnTests(ImproveTest):
    def test_plain_on_leaves_improve_off(self):
        r = self.run_script("lane-away", "on")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.away().is_file())
        self.assertFalse(self.improve_file().exists())
        self.assertIn("improve: off", self.run_script("lane-improve", "status").stdout)
        r = self.run_script("lane-away", "on", "--until-back")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.improve_file().exists())  # being away, even until back, never starts it
        self.assertFalse(self.drain_file().exists())

    def test_night_and_budget_marker_leave_improve_off(self):
        (self.conf_dir / "budget-stopped").touch()
        self.run_script("lane-improve", "status", env={"LANE_HOUR": "2"})
        self.run_script("night-precheck", env={"LANE_HOUR": "2"})
        self.assertFalse(self.improve_file().exists())
        self.assertIn("improve: off", self.run_script("lane-improve", "status", env={"LANE_HOUR": "2"}).stdout)

    def test_improve_writes_state(self):
        r = self.on("--limits", "nothing that needs db migrations", "--deny-paths", "**/migrations/**,infra/*")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = self.state()
        self.assertEqual(set(st), {"started", "end", "limits", "deny_paths", "projects", "unmetered", "branches", "integration",
                                   "prs", "stop_at", "max_items", "max_hours", "max_hours_off"})
        self.assertEqual(st["integration"], {"blog": "main", "shop": "main"})
        self.assertEqual(st["end"], "until-back")
        self.assertEqual(st["limits"], "nothing that needs db migrations")
        self.assertEqual(st["deny_paths"], ["**/migrations/**", "infra/*"])
        self.assertEqual(st["projects"], "all")
        self.assertIs(st["unmetered"], False)
        self.assertEqual(sorted(st["branches"]), ["blog", "shop"])
        for b in st["branches"].values():
            self.assertRegex(b, r"^away/\d{4}-\d{2}-\d{2}$")
        self.assertFalse(self.away().exists())  # presence is lane-away's, not improve's

    def test_hours_and_until_set_a_time_end(self):
        self.on("--hours", "3")
        self.assertRegex(self.state()["end"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
        self.reset()
        self.on("--until", "23:30")
        self.assertRegex(self.state()["end"], r"T23:30")
        self.assertEqual(self.on("--hours", "3", "--until-back").returncode, 2)
        self.assertEqual(self.on("--hours", "x").returncode, 2)

    def test_project_flag_limits_projects(self):
        self.on("--project", "shop")
        st = self.state()
        self.assertEqual(st["projects"], ["shop"])
        self.assertEqual(list(st["branches"]), ["shop"])
        self.reset()
        self.on("--project", "shop", "--project", "blog")
        self.assertEqual(self.state()["projects"], ["shop", "blog"])

    def test_unmetered_recorded(self):
        self.on("--unmetered")
        self.assertIs(self.state()["unmetered"], True)
        self.assertEqual(self.state()["end"], "until-back")

    def test_unknown_project_refused(self):
        r = self.on("--project", "nope")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nope", r.stderr)
        self.assertIn("shop", r.stderr)  # says what is known
        self.assertFalse(self.improve_file().exists())
        self.assertFalse(self.away().exists())

    def test_no_terminal_needs_yes(self):
        r = self.run_script("lane-improve", "on")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--yes", r.stderr)
        self.assertIn("the owner adds it", r.stderr)  # an agent is not told to add it; it needs the owner's word
        self.assertIn("`!` prefix", r.stderr)  # lc-7xh: Claude Code's ! prefix has no terminal either; the fix is --yes
        self.assertFalse(self.improve_file().exists())
        self.assertFalse(self.away().exists())

    def test_prints_summary(self):
        r = self.on("--project", "shop", "--limits", "no db migrations", "--hours", "2", "--unmetered")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("improve mode on", out)
        self.assertIn("shop", out)
        self.assertRegex(out, r"away/\d{4}-\d{2}-\d{2}")
        self.assertIn("no db migrations", out)
        self.assertIn("unmetered", out)
        self.assertIn("ends", out)
        self.reset()
        r = self.on()
        self.assertIn("until back", r.stdout)
        self.assertIn("metered", r.stdout)
        self.assertIn("all set-up projects", r.stdout)

    def test_off_ends_the_mode_and_leaves_a_drain_state(self):
        self.on()
        r = self.run_script("lane-improve", "off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.improve_file().exists())
        self.assertTrue(self.drain_file().exists())
        self.assertEqual(self.run_script("lane-improve", "off").returncode, 0)  # again: harmless
        self.assertTrue(self.drain_file().exists())

    def test_status_says_the_branch_stays_until_the_owner_adopts_or_drops_it(self):
        self.on("--project", "shop")
        s = self.run_script("lane-improve", "status").stdout
        self.assertIn("one away branch per project", s)
        for word in ("adopt", "drop"):
            self.assertIn(word, s)

    def test_status_shows_improve(self):
        self.on("--project", "shop", "--limits", "no db migrations")
        s = self.run_script("lane-improve", "status").stdout
        self.assertIn("improve: on", s)
        self.assertIn("until back", s)
        self.assertIn("no db migrations", s)
        self.assertIn("shop", s)
        self.assertIn("metered", s)

    def test_usage_refuses_improve_flags_without_improve(self):
        self.assertEqual(self.run_script("lane-away", "on", "--limits", "x").returncode, 2)
        self.assertFalse(self.improve_file().exists())

    def test_lane_away_no_longer_starts_improve_and_points_to_lane_improve(self):
        for args in (("on", "--improve"), ("on", "--improve", "--yes"), ("on", "--yes", "--improve")):
            r = self.run_script("lane-away", *args)
            self.assertEqual(r.returncode, 2, args)
            self.assertIn("lane-improve", r.stderr)
            self.assertFalse(self.improve_file().exists())
            self.assertFalse(self.away().exists())

    def test_improve_and_presence_are_separate(self):
        # `lane-improve on` writes no away file; `lane-away off` does not end improve; `lane-improve off` leaves presence
        self.on("--project", "shop")
        self.assertFalse(self.away().exists())
        self.run_script("lane-away", "on")
        self.run_script("lane-away", "off")
        self.assertTrue(self.improve_file().exists())
        self.run_script("lane-away", "on")
        self.run_script("lane-improve", "off")
        self.assertTrue(self.away().exists())
        self.assertIn("owner away: yes", self.run_script("lane-away", "status").stdout)
        self.assertNotIn("improve", self.run_script("lane-away", "status").stdout)


class ImproveBranchTests(ImproveTest):
    def read(self, code: str):
        return subprocess.run([sys.executable, "-c", f"import sys, datetime; sys.path.insert(0, {str(ROOT / 'core')!r}); "
                               f"from pathlib import Path; import improve; {code}"], env=self.env, capture_output=True,
                              text=True)

    def test_cut_new_branch_pushed(self):
        r = self.read(f"print(improve.cut_branch(Path({str(self.shop)!r}), 'main', datetime.date(2026, 11, 24)))")
        self.assertEqual(r.stdout.strip(), "away/2026-11-24", r.stderr)
        self.assertEqual(self.remote_branches(self.shop), ["away/2026-11-24"])
        # cut from the integration branch's head plus the empty start commit, and the checkout was left alone
        self.assert_cut_from(self.shop, "away/2026-11-24")
        self.assertEqual(git(self.shop, "branch", "--show-current").strip(), "main")
        self.assertNotIn("away/2026-11-24", git(self.shop, "branch", "--list").split())

    def test_a_project_never_gets_a_second_away_branch(self):
        code = (f"d = datetime.date(2026, 11, 24); r = Path({str(self.shop)!r}); print(improve.cut_branch(r, 'main', d)); "
                "print(improve.cut_branch(r, 'main', datetime.date(2026, 11, 25)))")
        r = self.read(code)
        self.assertEqual(r.stdout.split(), ["away/2026-11-24"], r.stderr)
        self.assertIn("ValueError", r.stderr)
        self.assertIn("away/2026-11-24", r.stderr)
        self.assertEqual(self.remote_branches(self.shop), ["away/2026-11-24"])

    def test_continue_takes_the_one_branch(self):
        self.read(f"improve.cut_branch(Path({str(self.shop)!r}), 'main', datetime.date(2026, 11, 20))")
        r = self.read(f"print(improve.continue_branch(Path({str(self.shop)!r})))")
        self.assertEqual(r.stdout.strip(), "away/2026-11-20", r.stderr)

    def test_continue_with_two_refuses_and_lists_them(self):
        for d in ("2026-11-20", "2026-11-22"):
            git(self.shop, "push", "-q", "origin", f"main:refs/heads/away/{d}")  # as if made by hand
        r = self.read(f"improve.continue_branch(Path({str(self.shop)!r}))")
        self.assertIn("ValueError", r.stderr)
        self.assertIn("away/2026-11-20", r.stderr)
        self.assertIn("away/2026-11-22", r.stderr)

    def test_continue_none_refuses(self):
        r = self.read(f"improve.continue_branch(Path({str(self.shop)!r}))")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ValueError", r.stderr)
        self.assertEqual(self.remote_branches(self.shop), [])

    def test_continue_cuts_where_a_project_has_none(self):
        r = self.on("--project", "shop", "--continue")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.remote_branches(self.shop)), 1)
        self.assertIn("cut", r.stdout)

    def test_continue_mixes_a_project_that_has_its_branch_with_one_that_has_none(self):
        # review M8: project A continues its branch while project B (new, or left out last time) gets one
        git(self.shop, "push", "-q", "origin", "main:refs/heads/away/2026-11-20")
        r = self.on("--continue")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = self.state()
        self.assertEqual(st["branches"]["shop"], "away/2026-11-20")
        self.assertRegex(st["branches"]["blog"], r"^away/\d{4}-\d{2}-\d{2}$")
        self.assertEqual(self.remote_branches(self.shop), ["away/2026-11-20"])  # no second branch for shop
        self.assertEqual(self.remote_branches(self.blog), [st["branches"]["blog"]])
        out = r.stdout
        self.assertRegex(out, r"shop -> away/2026-11-20 \(from main; continued\)")
        self.assertRegex(out, r"blog -> away/\S+ \(from main; cut now\)")

    def test_continue_still_refuses_two_branches_of_one_project_and_cuts_nothing_for_the_others(self):
        for d in ("2026-11-20", "2026-11-22"):
            git(self.shop, "push", "-q", "origin", f"main:refs/heads/away/{d}")
        r = self.on("--continue")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.remote_branches(self.blog), [])
        self.assertFalse(self.improve_file().exists())

    def test_on_is_refused_when_the_project_already_has_its_away_branch(self):
        self.on("--project", "shop")
        first = self.state()["branches"]["shop"]
        self.run_script("lane-improve", "off")
        self.run_script("lane-improve", "drained", "--all")
        r = self.on("--project", "shop")
        self.assertNotEqual(r.returncode, 0)
        for word in ("shop", first, "--continue", "adopt", "drop", "main"):
            self.assertIn(word, r.stderr, word)
        self.assertEqual(self.remote_branches(self.shop), [first])  # nothing new was cut
        self.assertFalse(self.improve_file().exists())
        self.assertFalse(self.away().exists())

    def test_on_all_is_refused_as_a_whole_when_one_project_has_a_branch(self):
        git(self.blog, "push", "-q", "origin", "main:refs/heads/away/2026-11-01")
        r = self.on()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("blog", r.stderr)
        self.assertEqual(self.remote_branches(self.shop), [])  # shop's was not cut either
        self.assertEqual(self.on("--project", "shop").returncode, 0)

    def test_a_merged_branch_that_was_not_deleted_still_counts_and_is_named_merged(self):
        git(self.shop, "push", "-q", "origin", "main:refs/heads/away/2026-11-01")  # nothing the integration branch lacks
        r = self.on("--project", "shop")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already merged", r.stderr)
        self.assertIn("delete", r.stderr)
        self.assertIn("usual", r.stderr)  # deleting is the usual step: continuing it builds on the old base
        self.assertIn("old base", r.stderr)

    def test_the_same_date_is_fine_once_the_branch_is_gone(self):
        self.on("--project", "shop")
        first = self.state()["branches"]["shop"]
        self.reset()  # off, drained, and the owner deletes the branch
        self.assertEqual(self.remote_branches(self.shop), [])
        self.assertEqual(self.on("--project", "shop").returncode, 0)
        self.assertEqual(self.state()["branches"]["shop"], first)

    def test_continue_with_two_branches_is_refused_by_lane_away_and_lists_them(self):
        for d in ("2026-11-20", "2026-11-22"):
            git(self.shop, "push", "-q", "origin", f"main:refs/heads/away/{d}")
        r = self.on("--project", "shop", "--continue")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("away/2026-11-20", r.stderr)
        self.assertIn("away/2026-11-22", r.stderr)
        self.assertFalse(self.improve_file().exists())

    def test_continue_takes_no_branch_argument(self):
        self.on("--project", "shop")
        first = self.state()["branches"]["shop"]
        r = self.on("--project", "shop", "--continue", first)
        self.assertEqual(r.returncode, 2)

    def test_integration_branch_from_pr_bases(self):
        code = f"print(improve.integration_branch(Path({str(self.shop)!r})))"
        self.set_bases(["develop", "main", "develop", "develop", "main"])
        self.assertEqual(self.read(code).stdout.strip(), "develop")
        self.set_bases([])  # no merged PRs: origin's default branch
        self.assertEqual(self.read(code).stdout.strip(), "main")
        (self.tmp / "gh-bases.json").unlink()  # gh fails: origin's default branch
        self.assertEqual(self.read(code).stdout.strip(), "main")

    def test_lane_away_cuts_from_the_integration_branch_and_records_branches(self):
        git(self.shop, "checkout", "-q", "-b", "develop")
        (self.shop / "d").write_text("d\n")
        git(self.shop, "add", "d")
        git(self.shop, "commit", "-qm", "d")
        git(self.shop, "push", "-q", "origin", "develop")
        self.set_bases(["develop", "develop"])
        r = self.on("--project", "shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        b = self.state()["branches"]["shop"]
        self.assertEqual(self.remote_branches(self.shop), [b])
        self.assert_cut_from(self.shop, b, "develop")
        self.assertEqual(self.remote_branches(self.blog), [])  # not asked for

    def test_lane_away_continue_reuses_branch(self):
        self.on("--project", "shop")
        first = self.state()["branches"]["shop"]
        r = self.on("--project", "shop", "--continue")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.state()["branches"]["shop"], first)
        self.assertEqual(self.remote_branches(self.shop), [first])

    def test_gh_only_read_commands(self):
        self.on()
        for line in (self.tmp / "gh.log").read_text().splitlines():
            self.assertTrue(line.startswith(("pr list ", "pr create --draft ")), line)


class ImproveDenyTests(ImproveTest):
    def settings(self, repo: Path) -> Path:
        return repo / ".claude" / "settings.local.json"

    def denies(self, repo: Path) -> list:
        f = self.settings(repo)
        return json.loads(f.read_text()).get("permissions", {}).get("deny", []) if f.exists() else []

    def test_on_installs_denies_for_the_chosen_projects(self):
        git(self.shop, "checkout", "-q", "-b", "develop")
        git(self.shop, "push", "-q", "origin", "develop")
        self.set_bases(["develop", "develop"])
        r = self.on("--project", "shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Bash(gh pr merge:*)", self.denies(self.shop))
        self.assertIn("Bash(git push origin develop:*)", self.denies(self.shop))
        self.assertEqual(self.denies(self.blog), [])
        self.assertIn("deny rules", r.stdout)

    def test_off_removes_them_from_every_coordinator(self):
        self.on()
        self.assertTrue(self.denies(self.shop) and self.denies(self.blog))
        r = self.run_script("lane-improve", "off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.denies(self.shop) and self.denies(self.blog))  # kept while the last PRs drain
        r = self.run_script("lane-improve", "drained", "--all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.denies(self.shop), self.denies(self.blog)), ([], []))
        self.assertFalse(self.settings(self.shop).exists())
        self.assertFalse(self.drain_file().exists())

    def test_off_keeps_the_owners_own_settings(self):
        self.settings(self.shop).parent.mkdir()
        self.settings(self.shop).write_text(json.dumps({"model": "sonnet", "permissions": {"deny": ["Bash(rm:*)"]}}))
        self.on("--project", "shop")
        self.run_script("lane-improve", "off")
        self.run_script("lane-improve", "drained", "shop")
        self.assertEqual(json.loads(self.settings(self.shop).read_text()),
                         {"model": "sonnet", "permissions": {"deny": ["Bash(rm:*)"]}})

    def test_settings_that_cannot_take_the_rules_warn_but_do_not_block(self):
        self.settings(self.shop).parent.mkdir()
        self.settings(self.shop).write_text("{not json")
        r = self.on("--project", "shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("shop", r.stderr)
        self.assertIn("deny rules NOT installed", r.stderr)
        self.assertIn("away-merge", r.stderr)
        self.assertTrue(self.improve_file().exists())
        self.assertEqual(self.settings(self.shop).read_text(), "{not json")

    def two_columns(self, *terminals):
        """machine.conf with `<name> <checkout>` only; Orca has these agent terminals (lc-5w1)."""
        from test_night_precheck import agent_terminal
        self.machine_conf(f'COORDINATORS="\nshop {self.shop}\nblog {self.blog}\n"\n')
        self.orca({"terminals": [agent_terminal(c, title=f"{c.name} coordinator", handle=f"term_{i}")
                                 for i, c in enumerate(terminals, 1)]})

    def test_two_column_lines_get_their_denies_through_orca(self):
        self.two_columns(self.shop, self.blog)
        r = self.on()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.denies(self.shop) and self.denies(self.blog))
        self.assertNotIn("NOT installed", r.stderr)

    def test_a_coordinator_orca_cannot_find_gets_no_rules_and_the_reason(self):
        self.two_columns(self.shop)  # no agent terminal for blog
        r = self.on()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.denies(self.shop))
        self.assertEqual(self.denies(self.blog), [])
        self.assertIn(f"blog: no agent terminal in {self.blog}", r.stderr)
        self.assertIn("deny rules NOT installed", r.stderr)
        self.assertIn("Start its coordinator, then `lane-improve on --continue --project blog`", r.stderr)

    def test_orca_down_installs_none_and_says_so_once(self):
        self.two_columns(self.shop, self.blog)
        self.orca({"down": True})
        r = self.on()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.denies(self.shop), self.denies(self.blog)), ([], []))
        self.assertEqual(r.stderr.count("Orca could not be asked"), 1, r.stderr)

    def test_the_rules_come_out_even_when_orca_cannot_say_whose_they_are(self):
        self.two_columns(self.shop, self.blog)
        self.on()
        self.assertTrue(self.denies(self.shop) and self.denies(self.blog))
        self.orca({"down": True})
        self.run_script("lane-improve", "off")
        r = self.run_script("lane-improve", "drained", "--all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.denies(self.shop), self.denies(self.blog)), ([], []))

    def test_the_rules_come_out_whatever_agent_runs_there_by_then(self):
        # m2: a different agent in the checkout at the end must not leave the first one's rules behind
        from test_night_precheck import agent_terminal
        self.two_columns(self.shop, self.blog)
        self.on()
        self.assertTrue(self.denies(self.shop) and self.denies(self.blog))
        self.orca({"terminals": [agent_terminal(self.shop, agent="opencode2"), agent_terminal(self.blog, agent="opencode2", handle="t2")]})
        self.run_script("lane-improve", "off")
        r = self.run_script("lane-improve", "drained", "--all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.denies(self.shop), self.denies(self.blog)), ([], []))

    def test_an_opencode_coordinator_gets_the_guard_plugin_record_and_is_told_so(self):
        self.machine_conf('COORDINATORS="\nshop %s claude shop coordinator\nblog %s opencode blog coordinator\n"\n'
                          % (self.shop, self.blog))
        r = self.on()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("blog", r.stdout)
        self.assertIn("guard plugin", r.stdout)
        self.assertEqual(self.denies(self.blog), [])  # nothing in its checkout
        import re
        key = re.sub(r"[^A-Za-z0-9]", "-", str(self.blog.resolve()))  # the record's name, in this test's HOME
        rec = self.home / ".local" / "state" / "lane-control" / "away-denies" / f"{key}.opencode.json"
        self.assertEqual(json.loads(rec.read_text()), {"integration": "main", "project": "blog"})
        self.assertTrue(self.denies(self.shop))
        self.run_script("lane-improve", "off")
        self.assertTrue(rec.exists())  # still draining
        self.run_script("lane-improve", "drained", "--all")
        self.assertFalse(rec.exists())


class ImproveDrainTests(ImproveTest):
    """Review M1: `off` or the end time leaves a drain state, so the running lane's PR can still merge."""

    def settings(self, repo: Path) -> Path:
        return repo / ".claude" / "settings.local.json"

    def drain(self) -> dict:
        return json.loads(self.drain_file().read_text())

    def test_off_records_what_is_left_to_wrap_up(self):
        self.on("--project", "shop")
        before = self.state()
        self.run_script("lane-improve", "off")
        d = self.drain()
        self.assertEqual(d["pending"], ["shop"])
        self.assertEqual(d["branches"], before["branches"])
        self.assertEqual(d["integration"], before["integration"])
        self.assertRegex(d["ended"], r"^\d{4}-\d{2}-\d{2}T")

    def test_off_for_all_projects_lists_every_coordinator(self):
        self.on()
        self.run_script("lane-improve", "off")
        self.assertEqual(sorted(self.drain()["pending"]), ["blog", "shop"])

    def test_status_shows_the_drain(self):
        self.on("--project", "shop")
        self.run_script("lane-improve", "off")
        s = self.run_script("lane-improve", "status").stdout
        self.assertIn("improve: draining", s)
        self.assertIn("shop", s)
        self.assertIn("lane-improve drained", s)

    def test_drained_one_project_at_a_time(self):
        self.on()
        self.run_script("lane-improve", "off")
        r = self.run_script("lane-improve", "drained", "shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.drain()["pending"], ["blog"])
        self.assertEqual(json.loads(self.settings(self.shop).read_text()) if self.settings(self.shop).exists() else {}, {})
        self.assertTrue(self.settings(self.blog).exists())
        self.assertEqual(self.run_script("lane-improve", "drained", "shop").returncode, 1)  # nothing left to drain there
        self.assertEqual(self.run_script("lane-improve", "drained", "blog").returncode, 0)
        self.assertFalse(self.drain_file().exists())
        self.assertIn("improve: off", self.run_script("lane-improve", "status").stdout)

    def test_the_end_time_drains_too(self):
        self.on("--project", "shop", "--hours", "1")
        st = self.state()
        st["end"] = "2020-01-01T00:00:00+00:00"
        self.improve_file().write_text(json.dumps(st))
        s = self.run_script("lane-improve", "status").stdout
        self.assertIn("improve: draining", s)
        self.assertIn("shop", s)
        r = self.run_script("lane-improve", "drained", "shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.improve_file().exists())
        self.assertFalse(self.drain_file().exists())

    def test_on_is_refused_while_on_unless_continue(self):
        self.on("--project", "shop")
        first = self.state()["branches"]["shop"]
        r = self.on("--project", "shop")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already on", r.stderr)
        self.assertIn("--continue", r.stderr)
        self.assertEqual(self.state()["branches"]["shop"], first)
        self.assertEqual(self.on("--project", "shop", "--continue").returncode, 0)

    def test_on_is_refused_while_draining(self):
        self.on("--project", "shop")
        self.run_script("lane-improve", "off")
        r = self.on("--project", "shop")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("drained", r.stderr)
        self.assertFalse(self.improve_file().exists())

    def test_off_without_the_mode_cleans_up_leftover_rules_and_leaves_no_drain(self):
        self.on("--project", "shop")
        self.improve_file().unlink()
        self.drain_file().unlink(missing_ok=True)
        self.run_script("lane-improve", "off")
        self.assertFalse(self.settings(self.shop).exists())
        self.assertFalse(self.drain_file().exists())

    def test_a_bad_started_time_is_off_and_status_does_not_crash(self):
        self.on("--project", "shop")
        st = self.state()
        st["started"] = "yesterday"
        self.improve_file().write_text(json.dumps(st))
        r = self.run_script("lane-improve", "status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("started", r.stderr)
        self.assertIn("improve: off", r.stdout)


class ImproveRerunAndDrainAgeTests(ImproveTest):
    def test_a_continue_rerun_keeps_the_projects_it_does_not_name(self):
        # review m14: `--continue --project shop` while on for shop and blog must not strand blog's rules and guard
        self.on("--limits", "no db migrations", "--hours", "5")
        before = self.state()
        r = self.on("--continue", "--project", "shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        after = self.state()
        self.assertEqual(sorted(after["branches"]), ["blog", "shop"])
        self.assertEqual(after["branches"], before["branches"])
        self.assertEqual(after["projects"], "all")
        self.assertEqual(after["limits"], "no db migrations")  # not given again: kept
        self.assertEqual(after["end"], before["end"])
        self.assertEqual(after["started"], before["started"])
        self.assertTrue((self.blog / ".claude" / "settings.local.json").exists())

    def test_a_project_set_up_after_the_mode_started_can_join(self):
        # lc-2rk: the mode covers "all", but a coordinator added later has no branch; the owner's `--continue --project`
        # cuts and records one while the mode runs, and keeps the others
        self.on("--hours", "5")
        before = self.state()
        late = make_repo(self.tmp, "late")
        self.machine_conf('COORDINATORS="\nshop %s/shop claude shop coordinator\nblog %s/blog claude blog coordinator\n'
                          'late %s/late claude late coordinator\n"\n' % (self.tmp, self.tmp, self.tmp))
        r = self.on("--continue", "--project", "late")
        self.assertEqual(r.returncode, 0, r.stderr)
        after = self.state()
        self.assertEqual(after["projects"], "all")
        self.assertEqual(sorted(after["branches"]), ["blog", "late", "shop"])
        self.assertEqual({k: after["branches"][k] for k in ("shop", "blog")},
                         {k: before["branches"][k] for k in ("shop", "blog")})
        self.assertIn("cut now", r.stdout)
        self.assertTrue((late / ".claude" / "settings.local.json").exists())

    def test_away_merge_for_a_project_without_a_branch_names_the_way_in(self):
        self.on("--hours", "5")
        st = self.state()
        del st["branches"]["blog"]
        self.improve_file().write_text(json.dumps(st))
        r = self.run_script("away-merge", "--project", "blog", "--pr", "1", "--item", "x")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("lane-improve on --continue --project blog", r.stderr)

    def test_a_continue_rerun_without_project_keeps_a_limited_period_limited(self):
        # review M9/M10: "two more hours" must not widen `--project shop` to every coordinator or cut their branches
        self.assertEqual(self.on("--project", "shop", "--hours", "4").returncode, 0)
        before = self.state()
        r = self.on("--continue", "--hours", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        after = self.state()
        self.assertEqual(after["projects"], ["shop"])
        self.assertEqual(after["branches"], before["branches"])
        self.assertNotEqual(after["end"], before["end"])
        self.assertEqual(self.remote_branches(self.blog), [])
        self.assertFalse((self.blog / ".claude" / "settings.local.json").exists())

    def test_a_continue_rerun_without_project_keeps_all_and_cuts_nothing(self):
        self.on("--hours", "4")
        before = self.state()
        r = self.on("--continue", "--limits", "new")
        self.assertEqual(r.returncode, 0, r.stderr)
        after = self.state()
        self.assertEqual((after["projects"], after["branches"], after["limits"]), ("all", before["branches"], "new"))

    def test_a_continue_rerun_with_new_options_replaces_only_those(self):
        self.on("--project", "shop", "--limits", "old")
        r = self.on("--continue", "--project", "blog", "--limits", "new", "--deny-paths", "infra/*")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = self.state()
        self.assertEqual(st["projects"], ["shop", "blog"])
        self.assertEqual((st["limits"], st["deny_paths"]), ("new", ["infra/*"]))
        self.assertEqual(sorted(st["branches"]), ["blog", "shop"])

    def test_status_says_how_long_a_drain_has_waited(self):
        self.on("--project", "shop")
        self.run_script("lane-improve", "off")
        d = json.loads(self.drain_file().read_text())
        s = self.run_script("lane-improve", "status").stdout
        self.assertNotIn("has waited", s)
        import datetime as dt
        d["ended"] = (dt.datetime.now().astimezone() - dt.timedelta(hours=20)).isoformat(timespec="seconds")
        self.drain_file().write_text(json.dumps(d))
        s = self.run_script("lane-improve", "status").stdout
        self.assertIn("20 h", s)
        self.assertIn("has waited", s)
        self.assertIn("lane-improve drained --all", s)


class ImproveIntegrationBranchTests(ImproveTest):
    """Review M2: the integration branch is never an away branch, and it is recorded once, at `on`."""

    def read(self, code: str):
        return subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(ROOT / 'core')!r}); "
                               f"from pathlib import Path; import improve; {code}"], env=self.env, capture_output=True,
                              text=True)

    def test_away_bases_do_not_count(self):
        self.set_bases(["away/2026-11-23"] * 8 + ["main", "main"])
        r = self.read(f"print(improve.integration_branch(Path({str(self.shop)!r})))")
        self.assertEqual(r.stdout.strip(), "main", r.stderr)
        self.set_bases(["away/2026-11-23"] * 5)  # nothing else to go on: origin's default
        r = self.read(f"print(improve.integration_branch(Path({str(self.shop)!r})))")
        self.assertEqual(r.stdout.strip(), "main", r.stderr)

    def test_a_second_period_cuts_from_the_integration_branch_and_denies_it(self):
        self.on("--project", "shop")
        self.reset()  # the owner dropped the first period's branch
        self.set_bases(["away/2026-11-23"] * 9 + ["main"])  # what the first period left behind
        r = self.on("--project", "shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = self.state()
        self.assertEqual(st["integration"]["shop"], "main")
        b = st["branches"]["shop"]
        self.assert_cut_from(self.shop, b, "main")
        denies = json.loads((self.shop / ".claude" / "settings.local.json").read_text())["permissions"]["deny"]
        self.assertIn("Bash(git push origin main:*)", denies)


class SummaryPrTests(ImproveTest):
    """The draft summary PR (away/<date> -> the integration branch): opened by `on`, its number recorded in improve.json,
    its body built from the branch's merge commits and the coordinator's summary (lc-2nz, AWF-1). gh is a stub; nothing reaches a real host."""

    def test_on_opens_a_draft_pr_per_project_and_records_it(self):
        self.assertEqual(self.on().returncode, 0)
        st = self.state()
        self.assertEqual(sorted(st["prs"]), ["blog", "shop"])
        for name, repo in (("shop", self.shop), ("blog", self.blog)):
            prs = self.summary_prs(repo)
            self.assertEqual(list(prs), [str(st["prs"][name])])
            pr = prs[str(st["prs"][name])]
            date = st["branches"][name].split("/")[1]
            self.assertEqual((pr["head"], pr["base"], pr["draft"]), (st["branches"][name], "main", True))
            self.assertEqual(pr["title"], f"away: {date} ({name})")
        self.assertIn("create --draft", " ".join(self.gh_log()))

    def test_a_fresh_branch_opens_its_pr_with_no_summary_yet(self):
        r = self.on("--project", "shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        pr = self.summary_prs(self.shop)[str(self.state()["prs"]["shop"])]
        self.assertIn("draft", pr["body"].lower())
        self.assertIn("never merged by an agent", pr["body"])
        self.assertIn("No summary yet", pr["body"])
        self.assertIn("Items merged: none yet", pr["body"])
        self.assertNotIn("AWAY-LOG", pr["body"])

    def test_a_pr_that_cannot_be_opened_does_not_stop_the_mode(self):
        r = self.on("--project", "shop", env={"GH_CREATE_FAIL": "GraphQL: No commits between main and away/x"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.state().get("prs", {}), {})
        self.assertIn("shop", r.stderr)
        self.assertIn("No commits between", r.stderr)
        self.assertIn("away-merge opens it", r.stderr)
        self.assertEqual(self.summary_prs(self.shop), {})

    def test_continue_picks_up_the_open_pr_of_the_branch(self):
        self.on("--project", "shop")
        n = self.state()["prs"]["shop"]
        self.run_script("lane-improve", "off")
        self.run_script("lane-improve", "drained", "--all")
        (self.tmp / "gh.log").write_text("")
        r = self.on("--project", "shop", "--continue")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.state()["prs"]["shop"], n)
        self.assertEqual(len(self.summary_prs(self.shop)), 1)
        self.assertNotIn("pr create", " ".join(self.gh_log()))

    def test_a_continue_rerun_without_a_project_touches_no_pr(self):
        self.on("--project", "shop")
        before = self.db()
        (self.tmp / "gh.log").write_text("")
        self.assertEqual(self.on("--continue", "--hours", "2").returncode, 0)
        self.assertEqual(self.db(), before)
        self.assertEqual([c for c in self.gh_log() if c.startswith(("pr create", "pr edit", "api --method PATCH"))], [])

    def test_a_closed_recorded_pr_is_said_to_be_closed_not_to_be_opened_later(self):
        self.on("--project", "shop")
        n = str(self.state()["prs"]["shop"])
        db = json.loads((self.tmp / "gh-summary.json").read_text())
        db["prs"][n]["state"] = "CLOSED"
        (self.tmp / "gh-summary.json").write_text(json.dumps(db))
        r = self.on("--continue", "--project", "shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("closed", r.stderr)
        self.assertIn("left alone", r.stderr)
        self.assertNotIn("away-merge opens it", r.stderr)
        self.assertEqual(self.state()["prs"]["shop"], int(n))

    def test_status_names_the_pr(self):
        self.on("--project", "shop")
        out = self.run_script("lane-improve", "status").stdout
        self.assertIn(f"#{self.state()['prs']['shop']}", out)
        self.assertIn("draft", out)

    def summary_file(self, text: str = "- Done: the x feature\n- Worth looking at first: x\n", name: str = "summary.md") -> str:
        f = self.tmp / "outside" / name
        f.parent.mkdir(exist_ok=True)
        f.write_text(text)
        return str(f)

    def test_pr_command_sets_the_body_summary_from_a_file(self):
        self.on("--project", "shop")
        n = str(self.state()["prs"]["shop"])
        r = self.run_script("lane-improve", "pr", "shop", "--summary-file", self.summary_file())
        self.assertEqual(r.returncode, 0, r.stderr)
        body = self.summary_prs(self.shop)[n]["body"]
        self.assertIn("Done: the x feature", body)
        self.assertIn("Worth looking at first: x", body)
        self.assertNotIn("No summary yet", body)
        self.assertIn(f"/pull/{n}", r.stdout)

    def test_pr_command_without_a_file_keeps_the_summary_already_there(self):
        self.on("--project", "shop")
        n = str(self.state()["prs"]["shop"])
        self.run_script("lane-improve", "pr", "shop", "--summary-file", self.summary_file())
        self.merge_item(self.shop, self.state()["branches"]["shop"])
        r = self.run_script("lane-improve", "pr", "shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        body = self.summary_prs(self.shop)[n]["body"]
        self.assertIn("Done: the x feature", body)
        self.assertIn("Add the x feature (PR #12", body)

    def test_a_new_file_replaces_the_summary(self):
        self.on("--project", "shop")
        n = str(self.state()["prs"]["shop"])
        self.run_script("lane-improve", "pr", "shop", "--summary-file", self.summary_file())
        self.run_script("lane-improve", "pr", "shop", "--summary-file", self.summary_file("- Done: something else\n"))
        body = self.summary_prs(self.shop)[n]["body"]
        self.assertIn("Done: something else", body)
        self.assertNotIn("the x feature", body)

    def test_a_bad_summary_file_is_refused_and_the_body_is_left_alone(self):
        self.on("--project", "shop")
        n = str(self.state()["prs"]["shop"])
        before = self.summary_prs(self.shop)[n]["body"]
        for ref, why in ((str(self.tmp / "nope.md"), "cannot be read"), (self.summary_file("  \n", "empty.md"), "empty"),
                         (self.summary_file("x" * 9000, "big.md"), "at most")):
            r = self.run_script("lane-improve", "pr", "shop", "--summary-file", ref)
            self.assertEqual(r.returncode, 1, ref)
            self.assertIn(why, r.stderr)
            self.assertEqual(len(r.stderr.strip().splitlines()), 1)
        self.assertEqual(self.summary_prs(self.shop)[n]["body"], before)

    def set_body(self, n, text):
        db = json.loads((self.tmp / "gh-summary.json").read_text())
        db["prs"][n]["body"] = text
        (self.tmp / "gh-summary.json").write_text(json.dumps(db))

    def edits(self):
        return [c for c in self.gh_log() if c.startswith(("pr edit", "api --method PATCH"))]

    def test_a_body_saved_with_crlf_keeps_its_summary_on_the_next_refresh(self):  # R1-04
        self.on("--project", "shop")
        n = str(self.state()["prs"]["shop"])
        self.run_script("lane-improve", "pr", "shop", "--summary-file", self.summary_file())
        self.set_body(n, self.summary_prs(self.shop)[n]["body"].replace("\n", "\r\n"))
        self.assertEqual(self.run_script("lane-improve", "pr", "shop").returncode, 0)
        self.assertIn("Done: the x feature", self.summary_prs(self.shop)[n]["body"])

    def test_a_summary_edited_by_hand_is_never_replaced_silently(self):  # R2-01
        self.on("--project", "shop")
        n = str(self.state()["prs"]["shop"])
        self.run_script("lane-improve", "pr", "shop", "--summary-file", self.summary_file())
        edited = self.summary_prs(self.shop)[n]["body"].replace("the x feature", "the x feature, OWNER: fixed")
        self.set_body(n, edited)
        (self.tmp / "gh.log").write_text("")
        for extra in ((), ("--summary-file", self.summary_file("- Done: new\n", "new.md"))):
            r = self.run_script("lane-improve", "pr", "shop", *extra)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertEqual(len(r.stderr.strip().splitlines()), 1)
            self.assertIn("summary region was edited", r.stderr)
            self.assertIn("To go on:", r.stderr)
            self.assertIn("--summary-file <file>", r.stderr)
        self.assertEqual(self.summary_prs(self.shop)[n]["body"], edited)
        self.assertEqual(self.edits(), [])

    def test_an_edit_in_the_items_region_is_left_by_the_refresh_too(self):  # R2-01
        self.on("--project", "shop")
        n = str(self.state()["prs"]["shop"])
        self.merge_item(self.shop, self.state()["branches"]["shop"])
        self.assertEqual(self.run_script("lane-improve", "pr", "shop").returncode, 0)
        edited = self.summary_prs(self.shop)[n]["body"].replace("- Add the x feature", "- OWNER: DROP THIS: Add the x feature")
        self.assertNotEqual(edited, self.summary_prs(self.shop)[n]["body"])
        self.set_body(n, edited)
        r = self.run_script("lane-improve", "pr", "shop")
        self.assertEqual(r.returncode, 1)
        self.assertIn("items region was edited", r.stderr)
        self.assertEqual(self.summary_prs(self.shop)[n]["body"], edited)

    def test_a_clean_body_takes_a_new_summary(self):
        self.on("--project", "shop")
        n = str(self.state()["prs"]["shop"])
        self.run_script("lane-improve", "pr", "shop", "--summary-file", self.summary_file())
        self.assertEqual(self.run_script("lane-improve", "pr", "shop", "--summary-file",
                                         self.summary_file("- Done: next\n", "next.md")).returncode, 0)
        self.assertIn("Done: next", self.summary_prs(self.shop)[n]["body"])

    def test_text_added_outside_the_markers_is_never_rewritten_away(self):
        self.on("--project", "shop")
        n = str(self.state()["prs"]["shop"])
        self.run_script("lane-improve", "pr", "shop", "--summary-file", self.summary_file())
        edited = "OWNER: drop item 2\n\n" + self.summary_prs(self.shop)[n]["body"]
        self.set_body(n, edited)
        (self.tmp / "gh.log").write_text("")
        for extra in ((), ("--summary-file", self.summary_file("- Done: new\n", "new.md"))):
            r = self.run_script("lane-improve", "pr", "shop", *extra)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertEqual(len(r.stderr.strip().splitlines()), 1)
            self.assertIn("left as it is", r.stderr)
            self.assertIn("outside the generated parts", r.stderr)
            self.assertIn("lane-improve pr shop", r.stderr)  # how to fix it
        self.assertEqual(self.summary_prs(self.shop)[n]["body"], edited)
        self.assertEqual(self.edits(), [])

    def test_removed_or_repeated_markers_leave_the_body_alone(self):
        self.on("--project", "shop")
        n = str(self.state()["prs"]["shop"])
        good = self.summary_prs(self.shop)[n]["body"]
        for bad in (good.replace(improve.SUMMARY_END, ""), good + improve.ITEMS_START, "A body written by hand\n"):
            self.set_body(n, bad)
            r = self.run_script("lane-improve", "pr", "shop")
            self.assertEqual(r.returncode, 1)
            self.assertIn("markers are missing", r.stderr)
            self.assertEqual(self.summary_prs(self.shop)[n]["body"], bad)

    def test_an_empty_body_is_written(self):
        self.on("--project", "shop")
        n = str(self.state()["prs"]["shop"])
        self.set_body(n, "")
        self.assertEqual(self.run_script("lane-improve", "pr", "shop").returncode, 0)
        self.assertIn("Items merged", self.summary_prs(self.shop)[n]["body"])

    def test_a_pr_the_owner_opened_with_its_own_text_is_adopted_but_not_rewritten(self):
        import datetime
        branch = f"away/{datetime.date.today().isoformat()}"
        mine = self.add_pr(self.shop, branch, "main")
        r = self.on("--project", "shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(str(self.state()["prs"]["shop"]), mine)  # recorded
        self.assertEqual(self.db()[mine]["body"], "THEIR BODY")  # untouched
        self.assertIn("left as it is", r.stderr)

    def test_the_refresh_runs_under_the_improve_lock(self):
        code = (f"import sys; sys.path.insert(0, {str(ROOT / 'core')!r}); import improve\n"
                "seen = []\n"
                "def fake(*a, **k):\n    seen.append(improve._lock_depth); return (5, 'u', 'refreshed')\n"
                "improve.sync_summary_pr = fake\n"
                "improve.record_pr = lambda *a: seen.append('rec')\n"
                "improve.sync_and_record(None, {'branches': {'shop': 'away/2026-11-24'}, 'integration': {}, 'prs': {}}, 'shop')\n"
                "print(seen)\n")
        r = subprocess.run([sys.executable, "-c", code], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "[1, 'rec']", r.stderr)

    def test_a_summary_with_a_secret_or_a_name_is_refused_and_nothing_is_changed(self):  # R1-01
        self.on("--project", "shop")
        n = str(self.state()["prs"]["shop"])
        before = self.summary_prs(self.shop)[n]["body"]
        (self.conf_dir / "private-names.txt").write_text("example-host\n")
        for text, what in (("- Done: key ghp_" + "a" * 36, "token"), ("- Done: see /ho" + "me/u/code", "home path"),
                           ("- Done: ssh Example-Host", "listed name")):
            r = self.run_script("lane-improve", "pr", "shop", "--summary-file", self.summary_file(text, "bad.md"))
            self.assertEqual(r.returncode, 1, text)
            self.assertIn(what, r.stderr)
            self.assertIn("summary adds", r.stderr)
            self.assertNotIn("ghp_", r.stderr)
        self.assertEqual(self.summary_prs(self.shop)[n]["body"], before)
        self.assertEqual(self.edits(), [])

    def test_a_body_goes_through_a_file_not_argv(self):
        self.on("--project", "shop")
        calls = " ".join(self.gh_log())
        self.assertIn("--body-file", calls)
        self.assertNotIn("--body ", calls)

    def test_pr_command_edits_no_file_in_the_project(self):
        self.on("--project", "shop")
        before = git(self.shop, "status", "--porcelain", "--untracked-files=all")
        self.run_script("lane-improve", "pr", "shop", "--summary-file", self.summary_file())
        self.assertEqual(git(self.shop, "status", "--porcelain", "--untracked-files=all"), before)

    def test_pr_command_lists_the_merged_items_with_their_revert_commands(self):
        self.on("--project", "shop")
        b, n = self.state()["branches"]["shop"], str(self.state()["prs"]["shop"])
        sha = self.merge_item(self.shop, b)
        r = self.run_script("lane-improve", "pr", "shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        body = self.summary_prs(self.shop)[n]["body"]
        self.assertIn(f"- Add the x feature (PR #12, {sha}): `git revert -m 1 {sha}`", body)
        self.assertIn("<summary>Add the x feature</summary>", body)  # its entry, copied from the merge commit
        self.assertIn("Users asked.", body)

    def test_pr_command_with_no_merged_item_says_none_yet(self):
        self.on("--project", "shop")
        n = str(self.state()["prs"]["shop"])
        self.assertEqual(self.run_script("lane-improve", "pr", "shop").returncode, 0)
        self.assertIn("Items merged: none yet", self.summary_prs(self.shop)[n]["body"])

    def test_pr_command_opens_a_missing_pr_and_records_it(self):
        self.on("--project", "shop", env={"GH_CREATE_FAIL": "boom"})
        self.assertNotIn("shop", self.state().get("prs", {}))
        r = self.run_script("lane-improve", "pr", "shop", "--summary-file", self.summary_file())
        self.assertEqual(r.returncode, 0, r.stderr)
        n = self.state()["prs"]["shop"]
        self.assertEqual(self.summary_prs(self.shop)[str(n)]["draft"], True)
        self.assertIn("Done: the x feature", self.summary_prs(self.shop)[str(n)]["body"])

    def test_pr_command_while_draining_records_into_the_drain_state(self):
        self.on("--project", "shop", env={"GH_CREATE_FAIL": "boom"})
        self.run_script("lane-improve", "off")
        r = self.run_script("lane-improve", "pr", "shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(self.drain_file().read_text())
        self.assertIn("shop", d["prs"])

    def test_pr_command_refuses_a_project_outside_the_mode(self):
        self.on("--project", "shop")
        r = self.run_script("lane-improve", "pr", "blog")
        self.assertEqual(r.returncode, 1)
        self.assertIn("blog", r.stderr)

    def test_a_closed_pr_is_left_alone(self):
        self.on("--project", "shop")
        n = str(self.state()["prs"]["shop"])
        db = json.loads((self.tmp / "gh-summary.json").read_text())
        db["prs"][n]["state"] = "CLOSED"
        (self.tmp / "gh-summary.json").write_text(json.dumps(db))
        before = db["prs"][n]["body"]
        (self.tmp / "gh.log").write_text("")
        r = self.run_script("lane-improve", "pr", "shop")
        self.assertEqual(r.returncode, 1)
        self.assertIn("closed", r.stderr)
        self.assertEqual(self.summary_prs(self.shop)[n]["body"], before)
        self.assertEqual([c for c in self.gh_log() if c.startswith(("pr create", "pr edit", "api --method PATCH"))], [])

    def test_a_pr_from_a_fork_with_the_same_branch_name_is_never_adopted(self):
        """B1: `gh pr list --head` matches the branch name only, a fork's PR included."""
        import datetime
        branch = f"away/{datetime.date.today().isoformat()}"
        theirs = self.add_pr(self.shop, branch, "main", cross=True)
        self.assertEqual(self.on("--project", "shop").returncode, 0)
        mine = str(self.state()["prs"]["shop"])
        self.assertNotEqual(mine, theirs)
        db = self.db()
        self.assertEqual(db[theirs]["body"], "THEIR BODY")  # not overwritten
        self.assertFalse(db[mine].get("cross", False))
        self.assertEqual(db[mine]["base"], "main")

    def test_a_pr_of_the_same_name_into_another_base_is_not_adopted(self):
        import datetime
        branch = f"away/{datetime.date.today().isoformat()}"
        other = self.add_pr(self.shop, branch, "release")
        self.assertEqual(self.on("--project", "shop").returncode, 0)
        self.assertNotEqual(str(self.state()["prs"]["shop"]), other)
        self.assertEqual(self.db()[other]["body"], "THEIR BODY")

    def test_the_owners_own_ready_pr_of_the_branch_is_still_found(self):
        import datetime
        branch = f"away/{datetime.date.today().isoformat()}"
        mine = self.add_pr(self.shop, branch, "main", draft=False)  # the owner marked it ready: still this branch's PR
        self.assertEqual(self.on("--project", "shop").returncode, 0)
        self.assertEqual(str(self.state()["prs"]["shop"]), mine)

    def test_a_recorded_number_that_is_not_this_branchs_pr_is_left_alone(self):
        self.on("--project", "shop")
        foreign = self.add_pr(self.shop, "someone-elses-branch", "main")
        st = self.state()
        st["prs"]["shop"] = int(foreign)
        self.improve_file().write_text(json.dumps(st))
        r = self.run_script("lane-improve", "pr", "shop")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not", r.stderr)
        self.assertEqual(self.db()[foreign]["body"], "THEIR BODY")

    def test_a_recorded_number_that_is_a_fork_pr_is_left_alone(self):
        self.on("--project", "shop")
        foreign = self.add_pr(self.shop, self.state()["branches"]["shop"], "main", cross=True)
        st = self.state()
        st["prs"]["shop"] = int(foreign)
        self.improve_file().write_text(json.dumps(st))
        self.assertEqual(self.run_script("lane-improve", "pr", "shop").returncode, 1)
        self.assertEqual(self.db()[foreign]["body"], "THEIR BODY")

    def test_a_malformed_gh_answer_never_stops_on(self):
        """M1: `on` pushed the branches already; a crash before the state is written would strand them."""
        for shape in ("object", "junk"):
            r = self.on("--project", "shop", env={"GH_LIST_SHAPE": shape})
            self.assertEqual(r.returncode, 0, (shape, r.stderr))
            self.assertIn("improve: on", self.run_script("lane-improve", "status").stdout)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("shop", r.stderr)
            self.reset()

    def test_gh_writes_are_only_draft_create_and_edit_of_the_summary_pr(self):
        self.on()
        self.run_script("lane-improve", "pr", "shop")
        for c in self.gh_log():
            if c.startswith(("pr create", "pr edit", "api --method PATCH")):
                continue
            self.assertTrue(c.startswith(("pr list", "pr view")), c)
        for c in self.gh_log():
            if c.startswith("pr create"):
                self.assertIn("--draft", c)
            self.assertNotRegex(c, r"pr (merge|ready|close|reopen)")

    def test_an_old_state_file_without_prs_still_loads(self):
        self.on("--project", "shop")
        st = self.state()
        st.pop("prs", None)
        self.improve_file().write_text(json.dumps(st))
        self.assertIn("improve: on", self.run_script("lane-improve", "status").stdout)

    def test_a_malformed_prs_field_makes_the_file_unusable(self):
        self.on("--project", "shop")
        st = {**self.state(), "prs": {"shop": "twelve"}}
        self.improve_file().write_text(json.dumps(st))
        r = self.run_script("lane-improve", "status")
        self.assertIn("improve: off", r.stdout)
        self.assertIn("prs", r.stderr)



class CapOptionTests(ImproveTest):
    """--stop-at <weekly %> and --max-items N: optional, off by default, refused where they make no sense (lc-2nz)."""

    def setUp(self):
        super().setUp()
        self.playbook(60, 10, ceiling=70)

    def test_off_by_default(self):
        self.assertEqual(self.on("--project", "shop").returncode, 0)
        st = self.state()
        self.assertIsNone(st["stop_at"])
        self.assertIsNone(st["max_items"])
        out = self.run_script("lane-improve", "status").stdout
        self.assertNotIn("items", out)
        self.assertNotIn("weekly", out)

    def test_the_caps_are_recorded_and_shown(self):
        r = self.on("--project", "shop", "--stop-at", "66", "--max-items", "10")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = self.state()
        self.assertEqual((st["stop_at"], st["max_items"]), (66, 10))
        self.assertIn("66", r.stdout)
        self.assertIn("10", r.stdout)
        out = self.run_script("lane-improve", "status").stdout
        self.assertIn("items 0/10", out)
        self.assertIn("weekly 60/66", out)

    def test_stop_at_is_refused_unmetered(self):
        r = self.on("--project", "shop", "--stop-at", "66", "--unmetered")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("unmetered", r.stderr)
        self.assertFalse(self.improve_file().exists())
        self.assertEqual(self.remote_branches(self.shop), [])

    def test_max_items_is_fine_unmetered(self):
        self.assertEqual(self.on("--project", "shop", "--max-items", "5", "--unmetered").returncode, 0)
        self.assertEqual(self.state()["max_items"], 5)

    def test_stop_at_at_or_above_the_ceiling_is_refused(self):
        for v in ("70", "71", "90"):
            r = self.on("--project", "shop", "--stop-at", v)
            self.assertEqual(r.returncode, 1, (v, r.stderr))
            self.assertIn("ceiling", r.stderr)
            self.assertIn("70", r.stderr)
        self.assertFalse(self.improve_file().exists())
        self.assertEqual(self.remote_branches(self.shop), [])
        self.assertEqual(self.on("--project", "shop", "--stop-at", "69").returncode, 0)

    def test_the_projects_own_ceiling_counts(self):
        import json as j
        ceil = self.tmp / "ceilings.json"
        ceil.write_text(j.dumps({"projects": {"shop": {"ceiling": 50}}}))
        self.playbook(20, 10, ceiling=f"file:{ceil}")
        self.assertEqual(self.on("--project", "shop", "--stop-at", "60").returncode, 1)
        self.assertEqual(self.on("--project", "shop", "--stop-at", "49").returncode, 0)

    def test_stop_at_needs_a_ceiling_it_can_read(self):
        self.playbook(None, None)  # the usage feed gives no reading
        r = self.on("--project", "shop", "--stop-at", "60")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ceiling", r.stderr)
        self.assertFalse(self.improve_file().exists())

    def test_the_ceiling_message_says_the_steps_stop_it_first(self):
        r = self.on("--project", "shop", "--stop-at", "75")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no-new-work", r.stderr)

    def test_a_project_added_later_is_checked_against_the_periods_stop_at(self):
        ceil = self.tmp / "ceilings.json"
        ceil.write_text(json.dumps({"projects": {"shop": {"ceiling": 80}, "blog": {"ceiling": 50}}}))
        self.playbook(20, 10, ceiling=f"file:{ceil}")
        self.assertEqual(self.on("--project", "shop", "--stop-at", "60").returncode, 0)
        r = self.on("--continue", "--project", "blog")  # no --stop-at given: the period's 60 still applies to it
        self.assertEqual(r.returncode, 1)
        self.assertIn("blog", r.stderr)
        self.assertIn("50", r.stderr)
        self.assertEqual(list(self.state()["branches"]), ["shop"])
        self.assertEqual(self.remote_branches(self.blog), [])

    def test_bad_values_are_usage_errors(self):
        for args in (("--stop-at", "0"), ("--stop-at", "-5"), ("--stop-at", "x"), ("--stop-at", "100"),
                     ("--max-items", "0"), ("--max-items", "-1"), ("--max-items", "x"), ("--max-items", "2.5")):
            r = self.on("--project", "shop", *args)
            self.assertEqual(r.returncode, 2, (args, r.stderr))
        self.assertFalse(self.improve_file().exists())

    def test_a_continue_rerun_keeps_the_caps_unless_given(self):
        self.on("--project", "shop", "--stop-at", "66", "--max-items", "10")
        self.assertEqual(self.on("--continue", "--hours", "2").returncode, 0)
        self.assertEqual((self.state()["stop_at"], self.state()["max_items"]), (66, 10))
        self.assertEqual(self.on("--continue", "--max-items", "20").returncode, 0)
        self.assertEqual((self.state()["stop_at"], self.state()["max_items"]), (66, 20))
        self.assertEqual(self.on("--continue", "--stop-at", "68").returncode, 0)
        self.assertEqual((self.state()["stop_at"], self.state()["max_items"]), (68, 20))

    def test_a_continue_rerun_can_drop_a_cap_with_none(self):
        self.on("--project", "shop", "--stop-at", "66", "--max-items", "10")
        self.assertEqual(self.on("--continue", "--stop-at", "none", "--max-items", "none").returncode, 0)
        self.assertEqual((self.state()["stop_at"], self.state()["max_items"]), (None, None))

    def test_a_continue_rerun_checks_the_new_stop_at_too(self):
        self.on("--project", "shop")
        self.assertEqual(self.on("--continue", "--stop-at", "75").returncode, 1)
        self.assertIsNone(self.state()["stop_at"])
        self.assertEqual(self.on("--continue", "--stop-at", "60", "--unmetered").returncode, 2)

    def test_a_continue_rerun_cannot_turn_a_stop_at_period_unmetered(self):
        self.on("--project", "shop", "--stop-at", "66")
        r = self.on("--continue", "--unmetered")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIs(self.state()["unmetered"], False)

    def test_a_malformed_cap_makes_the_file_unusable(self):
        self.on("--project", "shop")
        for bad in ({"stop_at": "high"}, {"max_items": 2.5}, {"max_items": 0}, {"stop_at": True}, {"stop_at": -1}):
            self.improve_file().write_text(json.dumps({**self.state(), **bad}))
            r = self.run_script("lane-improve", "status")
            self.assertIn("improve: off", r.stdout, bad)
            self.assertIn("ignored", r.stderr)
        self.improve_file().write_text(json.dumps({k: v for k, v in self.state().items() if k not in ("stop_at", "max_items")}))
        self.assertIn("improve: on", self.run_script("lane-improve", "status").stdout)  # an older file without them is fine




class StartCommitTests(ImproveTest):
    """`lane-improve on` cuts the away branch from the integration head plus one EMPTY commit, `away: start <date>`: it adds
    no file (each item's log entry lives in its PR body and merge commit), and it gives the branch something ahead of the
    integration branch so the draft summary PR can open at once (AWF-1; an empty commit, not none, because GitHub opens no
    PR for a branch with nothing ahead)."""

    def test_on_commits_an_empty_start_commit_on_the_branch(self):
        r = self.on("--project", "shop", "--limits", "nothing that needs db migrations")
        self.assertEqual(r.returncode, 0, r.stderr)
        b = self.state()["branches"]["shop"]
        self.assert_cut_from(self.shop, b)
        self.assertEqual(git(self.shop, "log", "-1", "--format=%an <%ae>", f"origin/{b}").strip(),
                         "Away Tester <away@example.invalid>")  # the configured identity, no attribution line
        self.assertEqual(git(self.shop, "log", "-1", "--format=%b", f"origin/{b}").strip(), "")
        self.assertEqual(git(self.shop, "branch", "--show-current").strip(), "main")  # the checkout was left alone
        self.assertEqual(git(self.shop, "status", "--porcelain", "--untracked-files=no").strip(), "")
        self.assertNotIn("AWAY-LOG.md", git(self.shop, "ls-tree", "-r", "--name-only", f"origin/{b}"))

    def test_no_log_file_is_ever_added_to_the_branch(self):
        self.on("--project", "shop", "--limits", "x")
        b = self.state()["branches"]["shop"]
        self.assertEqual(git(self.shop, "ls-tree", "-r", "--name-only", f"origin/{b}").split(),
                         git(self.shop, "ls-tree", "-r", "--name-only", "origin/main").split())

    def test_a_continued_branch_gets_no_new_start_commit(self):
        self.on("--project", "shop")
        b = self.state()["branches"]["shop"]
        self.run_script("lane-improve", "off")
        self.run_script("lane-improve", "drained", "--all")
        before = git(self.shop, "ls-remote", "origin", b)
        self.assertEqual(self.on("--project", "shop", "--continue").returncode, 0)
        self.assertEqual(git(self.shop, "ls-remote", "origin", b), before)
        self.assert_cut_from(self.shop, b)  # still the one start commit

    def test_a_project_added_with_continue_gets_its_own_start_commit(self):
        self.on("--project", "shop")
        self.assertEqual(self.on("--continue", "--project", "blog").returncode, 0)
        self.assert_cut_from(self.blog, self.state()["branches"]["blog"])

    def test_a_branch_that_only_has_the_start_commit_is_named_as_never_given_work(self):
        self.on("--project", "shop")
        self.run_script("lane-improve", "off")
        self.run_script("lane-improve", "drained", "--all")
        r = self.on("--project", "shop")
        self.assertEqual(r.returncode, 1)
        self.assertIn("never given any work", r.stderr)
        self.assertIn("delete", r.stderr)
        self.assertIn("old base", r.stderr)

    def test_a_branch_with_real_work_on_it_is_not_called_unworked(self):
        self.on("--project", "shop")
        b = self.state()["branches"]["shop"]
        work = self.tmp / "work-shop"
        subprocess.run(["git", "clone", "-q", str(self.tmp / "shop.git"), str(work)], check=True, capture_output=True)
        git(work, "checkout", "-q", b)
        (work / "x").write_text("x\n")
        git(work, "add", "x")
        git(work, "commit", "-qm", "away: x")
        git(work, "push", "-q", "origin", b)
        self.run_script("lane-improve", "off")
        self.run_script("lane-improve", "drained", "--all")
        r = self.on("--project", "shop")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("never given any work", r.stderr)
        self.assertNotIn("already merged", r.stderr)

    def test_a_log_file_already_on_the_integration_branch_is_no_longer_a_problem(self):
        (self.shop / "AWAY-LOG.md").write_text("an old log\n")
        git(self.shop, "add", "AWAY-LOG.md")
        git(self.shop, "commit", "-qm", "an adopted log left behind")
        git(self.shop, "push", "-q", "origin", "main")
        r = self.on("--project", "shop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_cut_from(self.shop, self.state()["branches"]["shop"])

    def test_without_a_git_identity_the_branch_is_cut_plain_with_a_warning(self):
        for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            self.env.pop(k)
        r = self.on("--project", "shop", env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                                              "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.useConfigOnly",
                                              "GIT_CONFIG_VALUE_0": "true"})
        self.assertEqual(r.returncode, 0, r.stderr)
        b = self.state()["branches"]["shop"]
        self.assertEqual(git(self.shop, "rev-parse", f"origin/{b}").strip(), git(self.shop, "rev-parse", "origin/main").strip())
        self.assertIn("start commit", r.stderr)
        self.assertIn("identity", r.stderr)

    def test_the_draft_pr_opens_at_on_with_no_summary_yet(self):
        self.on("--project", "shop")
        pr = self.summary_prs(self.shop)[str(self.state()["prs"]["shop"])]
        self.assertIn("No summary yet", pr["body"])
        self.assertEqual(pr["state"], "OPEN")


class ReaderAgreementTests(ImproveTest):
    """The improve file's cases: what improve.py accepts. (test_opencode_guard.py runs the guard plugin over the same
    cases; lane-limits.sh no longer reads the file at all: improve never changes presence.)"""

    GOOD = {"started": "2026-11-23T10:00:00+00:00", "end": "until-back", "limits": "", "deny_paths": [],
            "projects": "all", "unmetered": False, "branches": {"shop": "away/2026-11-23"},
            "integration": {"shop": "main"}}

    def cases(self):
        g = self.GOOD
        canon = json.dumps(g, indent=2, sort_keys=True) + "\n"
        future = {**g, "end": "2999-01-01T00:00:00+00:00"}
        yield "canonical", canon
        yield "one line", json.dumps(g)
        yield "future end", json.dumps(future)
        yield "naive future end", json.dumps({**g, "end": "2999-01-01T00:00:00"})
        yield "ended", json.dumps({**g, "end": "2020-01-01T00:00:00+00:00"})
        yield "truncated", canon[: canon.index('"limits"')]
        yield "truncated after end", canon[: canon.index('"end"') + 22] + ", "
        yield "only end", '{"end": "until-back"}'
        yield "project named end", json.dumps({**g, "branches": {"end": "away/x"}, "projects": ["end"],
                                               "integration": {"end": "main"}, "end": "2020-01-01T00:00:00+00:00"})
        yield "branch named end, until back", json.dumps({**g, "branches": {"end": "away/x"}})
        yield "limits text with end", json.dumps({**g, "limits": 'no "end": "until-back" tricks'})
        for key in ("started", "end", "limits", "deny_paths", "projects", "unmetered", "branches", "integration"):
            yield f"missing {key}", json.dumps({k: v for k, v in g.items() if k != key})
        yield "end is a number", json.dumps({**g, "end": 5})
        yield "end is junk", json.dumps({**g, "end": "tomorrow"})
        yield "started is junk", json.dumps({**g, "started": "yesterday"})
        yield "started is a number", json.dumps({**g, "started": 5})
        yield "projects a string", json.dumps({**g, "projects": "shop"})
        yield "projects a list", json.dumps({**g, "projects": ["shop"]})
        yield "projects mixed", json.dumps({**g, "projects": ["shop", 3]})
        yield "unmetered a string", json.dumps({**g, "unmetered": "yes"})
        yield "deny_paths a string", json.dumps({**g, "deny_paths": "x"})
        yield "branches a list", json.dumps({**g, "branches": []})
        yield "integration a list", json.dumps({**g, "integration": []})
        yield "extra keys", json.dumps({**g, "note": 1})
        yield "prs good", json.dumps({**g, "prs": {"shop": 12}})
        yield "prs a list", json.dumps({**g, "prs": []})
        yield "prs a number as text", json.dumps({**g, "prs": {"shop": "12"}})
        yield "prs a bool", json.dumps({**g, "prs": {"shop": True}})
        yield "prs a float", json.dumps({**g, "prs": {"shop": 1.5}})
        yield "stop_at good", json.dumps({**g, "stop_at": 66})
        yield "stop_at fraction", json.dumps({**g, "stop_at": 66.5})
        yield "stop_at null", json.dumps({**g, "stop_at": None})
        yield "stop_at zero", json.dumps({**g, "stop_at": 0})
        yield "stop_at 100", json.dumps({**g, "stop_at": 100})
        yield "stop_at text", json.dumps({**g, "stop_at": "66"})
        yield "stop_at true", json.dumps({**g, "stop_at": True})
        yield "max_items good", json.dumps({**g, "max_items": 10})
        yield "max_items null", json.dumps({**g, "max_items": None})
        yield "max_items zero", json.dumps({**g, "max_items": 0})
        yield "max_items fraction", json.dumps({**g, "max_items": 2.5})
        yield "max_items true", json.dumps({**g, "max_items": True})
        yield "max_items text", json.dumps({**g, "max_items": "3"})
        yield "empty", ""
        yield "spaces", "  \n"
        yield "list", "[]"
        yield "null", "null"
        yield "text", "hello"

    ON = {"canonical", "one line", "future end", "naive future end", "ended", "branch named end, until back",
          "limits text with end", "extra keys", "projects a list", "project named end",
          "prs good", "stop_at good", "stop_at fraction", "stop_at null", "max_items good", "max_items null"}

    def python_says(self, text: str) -> bool:
        self.improve_file().write_text(text)
        r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(ROOT / 'core')!r}); "
                            "import improve; sys.exit(0 if improve.load(quiet=True) or improve.ended_state() else 1)"],
                           env=self.env)
        return r.returncode == 0

    def test_the_reader_accepts_exactly_the_complete_valid_files(self):
        got = {name for name, text in self.cases() if self.python_says(text)}
        self.assertEqual(sorted(got - self.ON), [])
        self.assertEqual(sorted(self.ON - got), [])

    def test_a_truncated_file_is_off_and_gives_no_daytime_check_in(self):
        canon = json.dumps(self.GOOD, indent=2, sort_keys=True) + "\n"
        self.improve_file().write_text(canon[: canon.index('"limits"')])
        self.assertEqual(self.lane_limits("LANE_OWNER_AWAY")["LANE_OWNER_AWAY"], "")
        s = self.run_script("lane-improve", "status")
        self.assertIn("improve: off", s.stdout)
        self.assertIn("not valid JSON", s.stderr)


class ImproveReadTests(ImproveTest):
    def read(self, code: str, env=None):
        e = dict(self.env, **(env or {}))
        return subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(ROOT / 'core')!r}); "
                               f"import improve; {code}"], env=e, capture_output=True, text=True)

    def test_improve_never_changes_presence(self):
        self.on()
        v = self.lane_limits("LANE_OWNER_AWAY", "LANE_PRESENCE", "LANE_PRESENCE_WHY")
        self.assertEqual((v["LANE_OWNER_AWAY"], v["LANE_PRESENCE"], v["LANE_PRESENCE_WHY"]), ("", "day", "day"))
        self.assertFalse(self.away().exists())
        s = self.run_script("lane-away", "status").stdout
        self.assertIn("presence: day", s)
        self.assertIn("owner away: no", s)

    def test_malformed_file_is_off_and_says_why(self):
        self.improve_file().write_text("{not json")
        r = self.read("print(improve.load())")
        self.assertEqual(r.stdout.strip(), "None")
        self.assertIn("improve.json", r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)
        self.assertEqual(self.lane_limits("LANE_OWNER_AWAY")["LANE_OWNER_AWAY"], "")

    def test_wrong_shape_is_off_and_says_why(self):
        self.improve_file().write_text(json.dumps({"started": "x", "end": 5, "projects": "shop"}))
        r = self.read("print(improve.load())")
        self.assertEqual(r.stdout.strip(), "None")
        self.assertIn("improve.json", r.stderr)

    def test_missing_file_is_off_quietly(self):
        r = self.read("print(improve.load(), improve.active_for('shop'))")
        self.assertEqual(r.stdout.strip(), "None False")
        self.assertEqual(r.stderr, "")

    def test_ended_time_is_off(self):
        self.on("--hours", "1")
        st = self.state()
        st["end"] = "2020-01-01T00:00:00+00:00"
        self.improve_file().write_text(json.dumps(st))
        r = self.read("print(improve.load())")
        self.assertEqual(r.stdout.strip(), "None")

    def test_active_for_names(self):
        self.on("--project", "shop")
        r = self.read("print(improve.active_for('shop'), improve.active_for('blog'))")
        self.assertEqual(r.stdout.strip(), "True False")
        self.reset()
        self.on()
        r = self.read("print(improve.active_for('shop'), improve.active_for('blog'))")
        self.assertEqual(r.stdout.strip(), "True True")

    def test_write_and_clear(self):
        r = self.read("improve.write({'started':'2026-01-01T00:00:00+00:00','end':'until-back','limits':'','deny_paths':[],"
                      "'projects':'all','unmetered':False,'branches':{},'integration':{}}); print(improve.active_for('x')); improve.clear(); "
                      "print(improve.load())")
        self.assertEqual(r.stdout.split(), ["True", "None"], r.stderr)


if __name__ == "__main__":
    unittest.main()
