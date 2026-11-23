"""lane-away on --improve, the improve state (core/improve.py) and what reads it (away-improve mode, lc-yzb). Temp HOME
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

    def state(self) -> dict:
        return json.loads(self.improve_file().read_text())

    def on(self, *args, env=None):
        return self.run_script("lane-away", "on", "--improve", "--yes", *args, env=env)


class ImproveOnTests(ImproveTest):
    def test_plain_on_leaves_improve_off(self):
        r = self.run_script("lane-away", "on")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.away().is_file())
        self.assertFalse(self.improve_file().exists())
        self.assertIn("improve: off", self.run_script("lane-away", "status").stdout)

    def test_night_and_budget_marker_leave_improve_off(self):
        (self.conf_dir / "budget-stopped").touch()
        self.run_script("lane-away", "status", env={"LANE_HOUR": "2"})
        self.assertFalse(self.improve_file().exists())

    def test_improve_writes_state(self):
        r = self.on("--limits", "nothing that needs db migrations", "--deny-paths", "**/migrations/**,infra/*")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = self.state()
        self.assertEqual(set(st), {"started", "end", "limits", "deny_paths", "projects", "unmetered", "branches"})
        self.assertEqual(st["end"], "until-back")
        self.assertEqual(st["limits"], "nothing that needs db migrations")
        self.assertEqual(st["deny_paths"], ["**/migrations/**", "infra/*"])
        self.assertEqual(st["projects"], "all")
        self.assertIs(st["unmetered"], False)
        self.assertEqual(sorted(st["branches"]), ["blog", "shop"])
        for b in st["branches"].values():
            self.assertRegex(b, r"^away/\d{4}-\d{2}-\d{2}$")
        self.assertTrue(self.away().is_file())  # the owner counts as away

    def test_hours_and_until_set_a_time_end(self):
        self.on("--hours", "3")
        self.assertRegex(self.state()["end"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
        self.on("--until", "23:30")
        self.assertRegex(self.state()["end"], r"T23:30")
        self.assertEqual(self.on("--hours", "3", "--until-back").returncode, 2)
        self.assertEqual(self.on("--hours", "x").returncode, 2)

    def test_project_flag_limits_projects(self):
        self.on("--project", "shop")
        st = self.state()
        self.assertEqual(st["projects"], ["shop"])
        self.assertEqual(list(st["branches"]), ["shop"])
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
        r = self.run_script("lane-away", "on", "--improve")
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
        r = self.on()
        self.assertIn("until back", r.stdout)
        self.assertIn("metered", r.stdout)
        self.assertIn("all set-up projects", r.stdout)

    def test_off_clears_both(self):
        self.on()
        r = self.run_script("lane-away", "off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.away().exists())
        self.assertFalse(self.improve_file().exists())
        self.assertEqual(self.run_script("lane-away", "off").returncode, 0)

    def test_status_shows_improve(self):
        self.on("--project", "shop", "--limits", "no db migrations")
        s = self.run_script("lane-away", "status").stdout
        self.assertIn("improve: on", s)
        self.assertIn("until back", s)
        self.assertIn("no db migrations", s)
        self.assertIn("shop", s)
        self.assertIn("metered", s)

    def test_usage_refuses_improve_flags_without_improve(self):
        self.assertEqual(self.run_script("lane-away", "on", "--limits", "x").returncode, 2)
        self.assertFalse(self.improve_file().exists())


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

    def test_name_taken_gets_suffix(self):
        code = "d = datetime.date(2026, 11, 24); r = Path(%r); print(improve.cut_branch(r, 'main', d), improve.cut_branch(r, 'main', d), improve.cut_branch(r, 'main', d))" % str(self.shop)
        r = self.read(code)
        self.assertEqual(r.stdout.split(), ["away/2026-11-24", "away/2026-11-24-2", "away/2026-11-24-3"], r.stderr)

    def test_continue_newest(self):
        for d in ("2026-11-20", "2026-11-24", "2026-11-22"):
            self.read(f"improve.cut_branch(Path({str(self.shop)!r}), 'main', datetime.date.fromisoformat({d!r}))")
        self.read(f"improve.cut_branch(Path({str(self.shop)!r}), 'main', datetime.date(2026, 11, 24))")
        r = self.read(f"print(improve.continue_branch(Path({str(self.shop)!r}), None))")
        self.assertEqual(r.stdout.strip(), "away/2026-11-24-2", r.stderr)  # newest: later date, then later suffix
        r = self.read(f"print(improve.continue_branch(Path({str(self.shop)!r}), 'away/2026-11-22'))")
        self.assertEqual(r.stdout.strip(), "away/2026-11-22", r.stderr)

    def test_continue_none_refuses(self):
        r = self.read(f"improve.continue_branch(Path({str(self.shop)!r}), None)")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ValueError", r.stderr)
        r = self.read(f"improve.continue_branch(Path({str(self.shop)!r}), 'away/2026-01-01')")
        self.assertIn("ValueError", r.stderr)
        self.assertEqual(self.remote_branches(self.shop), [])

    def test_continue_refused_by_lane_away_without_cutting(self):
        r = self.on("--project", "shop", "--continue")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("shop", r.stderr)
        self.assertIn("no away branch", r.stderr)
        self.assertEqual(self.remote_branches(self.shop), [])
        self.assertFalse(self.improve_file().exists())
        self.assertFalse(self.away().exists())

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
        r = self.on("--project", "shop", "--continue", first)
        self.assertEqual(self.state()["branches"]["shop"], first)

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
        r = self.run_script("lane-away", "off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.denies(self.shop), self.denies(self.blog)), ([], []))
        self.assertFalse(self.settings(self.shop).exists())

    def test_off_keeps_the_owners_own_settings(self):
        self.settings(self.shop).parent.mkdir()
        self.settings(self.shop).write_text(json.dumps({"model": "sonnet", "permissions": {"deny": ["Bash(rm:*)"]}}))
        self.on("--project", "shop")
        self.run_script("lane-away", "off")
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

    def test_an_opencode_coordinator_is_told_away_merge_alone_guards_it(self):
        self.machine_conf('COORDINATORS="\nshop %s claude shop coordinator\nblog %s opencode blog coordinator\n"\n'
                          % (self.shop, self.blog))
        r = self.on()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("blog", r.stdout)
        self.assertIn("away-merge alone", r.stdout)
        self.assertEqual(self.denies(self.blog), [])
        self.assertTrue(self.denies(self.shop))


class ImproveReadTests(ImproveTest):
    def read(self, code: str, env=None):
        e = dict(self.env, **(env or {}))
        return subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(ROOT / 'core')!r}); "
                               f"import improve; {code}"], env=e, capture_output=True, text=True)

    def test_improve_keeps_owner_away_past_away_hours(self):
        self.on()
        # age the away file past AWAY_HOURS: the owner still counts as away while improve is on
        import os, time
        t = time.time() - 40 * 3600
        os.utime(self.away(), (t, t))
        v = self.lane_limits("LANE_OWNER_AWAY", "LANE_PRESENCE", "LANE_PRESENCE_WHY")
        self.assertEqual(v["LANE_OWNER_AWAY"], "1")
        self.assertEqual(v["LANE_PRESENCE"], "away")
        self.assertEqual(v["LANE_PRESENCE_WHY"], "improve")
        self.run_script("lane-away", "off")
        self.assertEqual(self.lane_limits("LANE_OWNER_AWAY")["LANE_OWNER_AWAY"], "")

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
        self.away().unlink()
        r = self.read("print(improve.load())")
        self.assertEqual(r.stdout.strip(), "None")
        self.assertEqual(self.lane_limits("LANE_PRESENCE_WHY")["LANE_PRESENCE_WHY"], "day")

    def test_active_for_names(self):
        self.on("--project", "shop")
        r = self.read("print(improve.active_for('shop'), improve.active_for('blog'))")
        self.assertEqual(r.stdout.strip(), "True False")
        self.on()
        r = self.read("print(improve.active_for('shop'), improve.active_for('blog'))")
        self.assertEqual(r.stdout.strip(), "True True")

    def test_write_and_clear(self):
        r = self.read("improve.write({'started':'2026-01-01T00:00:00+00:00','end':'until-back','limits':'','deny_paths':[],"
                      "'projects':'all','unmetered':False,'branches':{}}); print(improve.active_for('x')); improve.clear(); "
                      "print(improve.load())")
        self.assertEqual(r.stdout.split(), ["True", "None"], r.stderr)


if __name__ == "__main__":
    unittest.main()
