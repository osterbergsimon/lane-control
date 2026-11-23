"""lane-improve (on, off, drained, status), the improve state (core/improve.py) and what reads it (away-improve mode, lc-yzb). Temp HOME
and temp git repos; no real away file, improve file, branch or repo is touched."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "core"))
from test_machine_setup import MachineTest, write_exec  # noqa: E402


GH_BASES_STUB = """
#!/usr/bin/env python3
# gh stand-in: only `gh pr list --state merged ... --json baseRefName`, answered from $GH_BASES (a JSON list of bases).
import json, os, sys
a = sys.argv[1:]
open(os.environ["GH_LOG"], "a").write(" ".join(a) + "\\n")
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


class ImproveTest(MachineTest):
    def setUp(self):
        super().setUp()
        self.shop = make_repo(self.tmp, "shop")
        self.blog = make_repo(self.tmp, "blog")
        write_exec(self.bin / "gh", GH_BASES_STUB)
        self.env.update(GH_LOG=str(self.tmp / "gh.log"), GH_BASES=str(self.tmp / "gh-bases.json"))
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
        self.assertEqual(set(st), {"started", "end", "limits", "deny_paths", "projects", "unmetered", "branches", "integration"})
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
        # cut from the integration branch's head, and the checkout was left alone
        self.assertEqual(git(self.shop, "rev-parse", "origin/away/2026-11-24").strip(),
                         git(self.shop, "rev-parse", "origin/main").strip())
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
        self.assertEqual(git(self.shop, "rev-parse", f"origin/{b}").strip(), git(self.shop, "rev-parse", "origin/develop").strip())
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
            self.assertTrue(line.startswith("pr list "), line)


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
        self.assertEqual(git(self.shop, "rev-parse", f"origin/{b}").strip(), git(self.shop, "rev-parse", "origin/main").strip())
        denies = json.loads((self.shop / ".claude" / "settings.local.json").read_text())["permissions"]["deny"]
        self.assertIn("Bash(git push origin main:*)", denies)


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
        yield "empty", ""
        yield "spaces", "  \n"
        yield "list", "[]"
        yield "null", "null"
        yield "text", "hello"

    ON = {"canonical", "one line", "future end", "naive future end", "ended", "branch named end, until back",
          "limits text with end", "extra keys", "projects a list", "project named end"}

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
