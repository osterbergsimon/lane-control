"""core/beads.py: how lane-control looks at, checks, moves and restores a project's beads (design:
docs/superpowers/specs/2026-11-22-beads-handling-design.md).

Real git in temp dirs; bd, gh are stubs on a temp PATH that log every call. HOME is a temp dir. Nothing here reaches a real
beads workspace, GitHub or a real bd (that is tests/test_beads_roundtrip.py, opt-in)."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
import beads  # noqa: E402

# bd stand-in: logs each call to $BD_LOG; `dolt remote list` prints $BD_DOLT_REMOTE (one URL) when set; `where` and
# `show`/`memories` succeed unless $BD_FAIL names the subcommand; `version` prints $BD_VERSION.
BD_STUB = """
#!/bin/sh
echo "$PWD :: $*" >> "$BD_LOG"
case "$1" in
  version) echo "bd version ${BD_VERSION:-1.0.4} (abcdef0: HEAD@abcdef0)"; exit 0 ;;
esac
if [ "$BD_FAIL" = "$1" ]; then
  case "$PWD" in *"${BD_FAIL_IN:-}"*) echo "bd: $1 failed" >&2; exit 1 ;; esac
fi
[ "$1" = prime ] && case "$*" in *--hook-json*) echo "Error: unknown flag: --hook-json" >&2; exit 1 ;; esac
state="$BD_LOG.remote"
case "$1 $2 $3" in
  "dolt remote list")
    [ -n "$BD_DOLT_REMOTE" ] && echo "origin               $BD_DOLT_REMOTE"
    [ -f "$state" ] && echo "origin               $(cat "$state")"
    exit 0 ;;
  "dolt remote add")
    echo "$5" > "$state"
    [ -f .beads/config.yaml ] && echo "sync.remote: \"$5\"" >> .beads/config.yaml
    git add .beads >/dev/null 2>&1; git -c user.name=bd -c user.email=bd@example.invalid commit -qm "bd: update sync.remote" >/dev/null 2>&1
    exit 0 ;;
  "dolt push"*)
    # real bd refuses an empty git remote; a write during the push is simulated through $BD_DURING_PUSH
    [ -n "$BD_DURING_PUSH" ] && echo "written during push" >> "$BD_DURING_PUSH"
    git ls-remote --heads origin 2>/dev/null | grep -q . || { echo "git remote has no branches: initialize the repository first" >&2; exit 1; }
    exit 0 ;;
esac
case "$1" in
  where) echo "$PWD/.beads"; echo "  database: $PWD/.beads/embeddeddolt"; exit 0 ;;
  memories|list) cat "$PWD/.beads/stub-db" 2>/dev/null; exit 0 ;;
esac
exit 0
"""

# gh stand-in: logs calls to $GH_LOG. `auth status` succeeds unless $GH_NOT_SIGNED_IN; `repo view` fails (not found)
# unless $GH_EXISTS; `repo clone <o>/<n> <dir>` clones $GH_REMOTES/<n>.git; `repo create <o>/<n> --private` makes a bare repo $GH_REMOTES/<n>.git (git is told to send
# https://github.com/<o>/ there) unless $GH_FAIL names the subcommand.
GH_STUB = """
#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "auth status") [ -z "$GH_NOT_SIGNED_IN" ] && exit 0; echo "You are not logged in" >&2; exit 1 ;;
  "repo view") [ -n "$GH_EXISTS" ] && exit 0; echo "Could not resolve to a Repository" >&2; exit 1 ;;
  "repo create")
    [ "$GH_FAIL" = create ] && { echo "gh: create failed" >&2; exit 1; }
    n="${3#*/}"; git init -q --bare -b main "$GH_REMOTES/$n.git"; exit 0 ;;
  "repo clone") git clone -q "$GH_REMOTES/${3#*/}.git" "$4"; exit $? ;;
esac
exit 0
"""


def write_exec(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip())
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout


class BeadsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lc-beads-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        (self.home / "code").mkdir(parents=True)
        self.bin = self.tmp / "bin"
        self.bd_log = self.tmp / "bd.log"
        self.bd_log.write_text("")
        write_exec(self.bin / "bd", BD_STUB)
        write_exec(self.bin / "gh", GH_STUB)
        self.gh_log = self.tmp / "gh.log"
        self.gh_log.write_text("")
        self.remotes = self.tmp / "remotes"
        self.remotes.mkdir()
        path = f"{self.bin}{os.pathsep}{os.environ['PATH']}"
        patcher = mock.patch.dict(os.environ, {"PATH": path, **testhome.env_for(self.home), "BD_LOG": str(self.bd_log),
                                             "GH_LOG": str(self.gh_log), "GH_REMOTES": str(self.remotes),
                                             "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"url.file://{self.remotes}/.insteadOf",
                                             "GIT_CONFIG_VALUE_0": "https://github.com/acme/",
                                             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                                             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ("BD_FAIL", "BD_FAIL_IN", "BD_DOLT_REMOTE", "BD_VERSION", "GH_NOT_SIGNED_IN", "GH_EXISTS", "GH_FAIL",
                  "CLAUDE_CONFIG_DIR"):
            os.environ.pop(k, None)
        self.repo = self.home / "code" / "widget"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main", ".")
        git(self.repo, "remote", "add", "origin", "https://github.com/acme/widget.git")

    def gh_calls(self) -> list:
        return self.gh_log.read_text().splitlines()

    def bd_calls(self) -> list:
        return [ln.split(" :: ", 1)[1] for ln in self.bd_log.read_text().splitlines()]

    def make_beads_repo(self, remote: bool = True) -> Path:
        b = self.home / "code" / "widget-beads"
        (b / ".beads").mkdir(parents=True)
        git(b, "init", "-q", "-b", "main", ".")
        if remote:
            git(b, "remote", "add", "origin", "https://github.com/acme/widget-beads.git")
            (b / ".beads" / "config.yaml").write_text("prefix: w\n")
            git(b, "add", ".beads")
            git(b, "commit", "-qm", "beads")
            git(b, "update-ref", "refs/remotes/origin/main", "HEAD")  # as if pushed
            git(b, "config", "branch.main.remote", "origin")
            git(b, "config", "branch.main.merge", "refs/heads/main")
        return b

    def link(self, beads_repo: Path, ignored: bool = True):
        (self.repo / ".beads").symlink_to(beads_repo / ".beads")
        if ignored:
            (self.repo / ".gitignore").write_text(".beads\n")


class SlugTests(BeadsCase):
    def test_slug_from_origin(self):
        self.assertEqual(beads.beads_repo_slug(self.repo, None), ("acme", "widget-beads"))

    def test_slug_from_ssh_origin(self):
        git(self.repo, "remote", "set-url", "origin", "git@github.com:acme/widget.git")
        self.assertEqual(beads.beads_repo_slug(self.repo, None), ("acme", "widget-beads"))

    def test_override_wins(self):
        self.assertEqual(beads.beads_repo_slug(self.repo, "x/y"), ("x", "y"))

    def test_no_github_origin_and_no_override_raises(self):
        git(self.repo, "remote", "set-url", "origin", "https://example.invalid/acme/widget.git")
        with self.assertRaises(ValueError):
            beads.beads_repo_slug(self.repo, None)

    def test_bad_override_raises(self):
        with self.assertRaises(ValueError):
            beads.beads_repo_slug(self.repo, "not-a-slug")


class CheckTests(BeadsCase):
    def lines(self):
        return beads.check(self.repo, beads.inspect(self.repo))

    def problems(self, lines):
        return [d for _, what, d in lines if what in beads.PROBLEMS]

    def test_link_layout_ok(self):
        b = self.make_beads_repo()
        self.link(b)
        os.environ["BD_DOLT_REMOTE"] = "git+https://github.com/acme/widget-beads.git"
        state = beads.inspect(self.repo)
        self.assertEqual((state.kind, state.ignored), ("link", True))
        self.assertEqual(state.beads_repo, b.resolve())
        lines = beads.check(self.repo, state)
        self.assertEqual([what for _, what, _ in lines], ["exists"], lines)
        self.assertEqual(lines[0][0], "beads")

    def test_folder_reports_no_offsite_copy(self):
        (self.repo / ".beads").mkdir()
        state = beads.inspect(self.repo)
        self.assertEqual(state.kind, "folder")
        text = " ".join(self.problems(beads.check(self.repo, state)))
        self.assertIn("no off-machine copy", text)

    def test_nothing_there_says_so_and_creates_nothing(self):
        lines = self.lines()
        self.assertEqual(beads.inspect(self.repo).kind, "none")
        self.assertIn("no beads workspace", " ".join(self.problems(lines)))
        self.assertFalse((self.repo / ".beads").exists() or (self.repo / ".beads").is_symlink())

    def test_bd_missing_says_install(self):
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}):
            lines = beads.check(self.repo, beads.inspect(self.repo))
        self.assertIn("`bd` is not on PATH", " ".join(self.problems(lines)))

    def test_not_ignored_names_fix_and_drafts_no_link(self):
        b = self.make_beads_repo()
        self.link(b, ignored=False)
        os.environ["BD_DOLT_REMOTE"] = "git+https://github.com/acme/widget-beads.git"
        state = beads.inspect(self.repo)
        self.assertFalse(state.ignored)
        text = " ".join(self.problems(beads.check(self.repo, state)))
        self.assertIn("info/exclude", text)  # the uncommitted file, like the move uses (a link is not matched by `.beads/`)
        self.assertNotIn("No link was drafted", text)
        self.assertEqual((self.repo / ".beads").resolve(), (b / ".beads").resolve())  # untouched

    def test_remote_missing_reports_only(self):
        b = self.make_beads_repo(remote=False)
        self.link(b)
        before = git(b, "remote", "-v")
        state = beads.inspect(self.repo)
        self.assertIsNone(state.git_remote)
        self.assertIsNone(state.dolt_remote)
        text = " ".join(self.problems(beads.check(self.repo, state)))
        self.assertIn("no off-machine copy", text)
        self.assertEqual(git(b, "remote", "-v"), before)
        for call in self.bd_calls():  # read-only calls only
            self.assertTrue(call.startswith(("where", "dolt remote list", "version")), call)

    def test_dolt_remote_missing_alone_is_reported(self):
        b = self.make_beads_repo(remote=True)
        self.link(b)
        text = " ".join(self.problems(self.lines()))
        self.assertIn("no off-machine copy", text)
        self.assertIn("Dolt", text)

    def test_unpushed_beads_repo_commits_are_reported(self):
        b = self.make_beads_repo()
        self.link(b)
        os.environ["BD_DOLT_REMOTE"] = "git+https://github.com/acme/widget-beads.git"
        (b / ".beads" / "more").write_text("x")
        git(b, "add", ".beads")
        git(b, "commit", "-qm", "later")
        state = beads.inspect(self.repo)
        self.assertEqual(state.unpushed, 1)
        text = " ".join(self.problems(beads.check(self.repo, state)))
        self.assertIn("1 commit(s) not pushed", text)
        self.assertIn("bd dolt push", text)

    def test_a_beads_repo_never_pushed_is_reported(self):
        b = self.make_beads_repo()
        git(b, "config", "--unset", "branch.main.remote")
        git(b, "config", "--unset", "branch.main.merge")
        self.link(b)
        os.environ["BD_DOLT_REMOTE"] = "git+https://github.com/acme/widget-beads.git"
        text = " ".join(self.problems(beads.check(self.repo, beads.inspect(self.repo))))
        self.assertIn("never been pushed", text)

    def test_hooks_path_into_beads_reported(self):
        b = self.make_beads_repo()
        self.link(b)
        os.environ["BD_DOLT_REMOTE"] = "git+https://github.com/acme/widget-beads.git"
        git(self.repo, "config", "core.hooksPath", ".beads/hooks")
        state = beads.inspect(self.repo)
        self.assertTrue(state.hooks_path_into_beads)
        self.assertIn("core.hooksPath", " ".join(self.problems(beads.check(self.repo, state))))

    def test_broken_link_is_other(self):
        (self.repo / ".beads").symlink_to(self.tmp / "gone")
        state = beads.inspect(self.repo)
        self.assertEqual(state.kind, "other")
        self.assertTrue(self.problems(beads.check(self.repo, state)))


class VersionTests(BeadsCase):
    def test_parses_bd_version_output(self):
        self.assertEqual(beads.bd_version(), (1, 0, 4))
        os.environ["BD_VERSION"] = "1.12.30"
        self.assertEqual(beads.bd_version(), (1, 12, 30))

    def test_none_when_bd_missing_or_unreadable(self):
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}):
            self.assertIsNone(beads.bd_version())
        write_exec(self.bin / "bd", "#!/bin/sh\necho gibberish\n")
        self.assertIsNone(beads.bd_version())

    def test_floor_is_1_0_4(self):
        self.assertEqual(beads.BD_FLOOR, (1, 0, 4))


class SpecAgreementTests(unittest.TestCase):
    """lc-ug6 r2-n1: goal 4 promised an unpushed-database check that bd 1.0.4 cannot support; the spec and
    core/issues.md must both say so, in the same words, so the owner sees it as a call."""
    PHRASE = "cannot detect an unpushed Dolt database"

    def test_spec_and_issues_md_agree_that_only_the_git_half_is_checked(self):
        spec = (ROOT / "docs/superpowers/specs/2026-11-22-beads-handling-design.md").read_text()
        issues = (ROOT / "core/issues.md").read_text()
        flat = lambda t: " ".join(t.split())
        for name, text in (("spec", flat(spec)), ("issues.md", flat(issues))):
            with self.subTest(doc=name):
                self.assertIn(self.PHRASE, text)
                self.assertIn("bd 1.0.4", text)
                self.assertIn("beads-repo git commits", text)

    def test_goal_4_no_longer_promises_an_unpushed_database_check(self):
        spec = (ROOT / "docs/superpowers/specs/2026-11-22-beads-handling-design.md").read_text()
        goal4 = spec[spec.index("4. Problems are said plainly"):spec.index("## Non-goals")]
        self.assertNotIn("an unpushed database", goal4)
        self.assertIn("unpushed beads-repo git commits", " ".join(goal4.split()))


class HookTests(BeadsCase):
    def committed(self, command: str):
        import json
        f = self.repo / ".claude" / "settings.json"
        f.parent.mkdir(exist_ok=True)
        f.write_text(json.dumps({"hooks": {"SessionStart": [{"matcher": "", "hooks": [
            {"type": "command", "command": command}, {"type": "command", "command": "echo unrelated"}]}]}}))
        return f

    def test_bd_prime_command(self):
        self.assertEqual(beads.bd_prime_command(self.repo), "bd prime")
        os.environ["BD_FAIL"] = "prime"
        self.assertIsNone(beads.bd_prime_command(self.repo))
        os.environ.pop("BD_FAIL")
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}):
            self.assertIsNone(beads.bd_prime_command(self.repo))

    def test_committed_hooks_listed_with_whether_they_run(self):
        self.committed("bd prime --hook-json")
        self.assertEqual(beads.committed_bd_hooks(self.repo), [("bd prime --hook-json", False)])
        self.committed("bd prime")
        self.assertEqual(beads.committed_bd_hooks(self.repo), [("bd prime", True)])
        self.assertEqual(beads.committed_bd_hooks(self.tmp), [])  # no settings file: nothing

    def test_committed_broken_hook_named_and_file_untouched(self):
        f = self.committed("bd prime --hook-json")
        before = f.read_bytes()
        lines = beads.hook_notes(self.repo)
        self.assertEqual([w for _, w, _ in lines], ["broken"])
        self.assertIn("bd prime --hook-json", lines[0][2])
        self.assertIn("`bd prime`", lines[0][2])
        self.assertEqual(f.read_bytes(), before)

    def test_committed_working_hook_says_no_second_one_is_added(self):
        self.committed("bd prime")
        lines = beads.hook_notes(self.repo)
        self.assertEqual([w for _, w, _ in lines], ["exists"])
        self.assertIn("runs `bd prime`", lines[0][2])

    def test_duplicate_with_a_local_hook_is_reported(self):
        import json
        self.committed("bd prime")
        loc = self.repo / ".claude" / "settings.local.json"
        loc.write_text(json.dumps({"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [
            {"type": "command", "command": "bd prime"}]}]}}))
        self.assertIn("warning", [w for _, w, _ in beads.hook_notes(self.repo)])

    def test_a_hook_that_is_a_shell_line_is_not_probed_or_judged(self):
        self.committed("bd prime --hook-json || true")
        self.assertEqual(beads.committed_bd_hooks(self.repo), [])
        self.assertEqual(self.bd_calls(), [])

    def test_only_bd_prime_is_ever_run_from_a_committed_file(self):
        self.committed("bd create evil")
        self.assertEqual([c.split()[0:2] for c in self.bd_calls()], [])
        beads.committed_bd_hooks(self.repo)
        self.assertEqual(self.bd_calls(), ["create --help"])  # a --help probe, never the command itself

    # --- every settings layer that applies (lc-ug6: m8, r2-m1) ---

    def settings_at(self, path: Path, event: str, command: str, matcher: str = ""):
        import json
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"hooks": {event: [{"matcher": matcher, "hooks": [
            {"type": "command", "command": command}]}]}}))
        return path

    def user_settings(self):
        return self.home / ".claude" / "settings.json"

    def local_settings(self):
        return self.repo / ".claude" / "settings.local.json"

    def details(self, lines):
        return " ".join(d for _, _, d in lines)

    def test_a_prime_hook_in_the_user_settings_is_found_and_named(self):
        self.settings_at(self.user_settings(), "SessionStart", "bd prime")
        lines = beads.hook_notes(self.repo)
        self.assertEqual([w for _, w, _ in lines], ["exists"])
        self.assertIn(str(self.user_settings()), self.details(lines))
        self.assertEqual([(h.layer, h.event, h.runs) for h in beads.prime_hooks(self.repo)],
                         [("user", "SessionStart", True)])

    def test_the_user_settings_folder_follows_claude_config_dir(self):
        other = self.tmp / "profile"
        self.settings_at(other / "settings.json", "SessionStart", "bd prime")
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(other)}):
            self.assertEqual([h.layer for h in beads.prime_hooks(self.repo)], ["user"])
        self.assertEqual(beads.prime_hooks(self.repo), [])  # without it, ~/.claude is read, and holds nothing

    def test_a_shell_line_that_runs_bd_prime_counts_and_is_never_run(self):
        self.committed("bd prime 2>/dev/null || true")
        hooks = beads.prime_hooks(self.repo)
        self.assertEqual([(h.layer, h.runs) for h in hooks], [("committed", None)])  # present; not probed, not judged
        self.assertEqual(self.bd_calls(), [])
        lines = beads.hook_notes(self.repo)
        self.assertEqual([w for _, w, _ in lines], ["exists"])
        self.assertIn("bd prime 2>/dev/null || true", self.details(lines))

    def test_a_command_that_only_mentions_bd_prime_does_not_count(self):
        self.committed('echo "run bd prime by hand"')
        self.assertEqual(beads.prime_hooks(self.repo), [])
        self.assertEqual(beads.hook_notes(self.repo), [])

    def test_a_prime_hook_on_another_event_is_listed_but_is_not_a_session_start_hook(self):
        self.settings_at(self.repo / ".claude" / "settings.json", "Stop", "bd prime --memories-only")
        hooks = beads.prime_hooks(self.repo)
        self.assertEqual([(h.event, h.covers_start) for h in hooks], [("Stop", False)])
        lines = beads.hook_notes(self.repo)
        self.assertEqual([w for _, w, _ in lines], ["noted"])
        self.assertIn("Stop", self.details(lines))
        self.assertNotIn("adds no second one", self.details(lines))  # a project with only this has no start-up prime

    def test_bds_own_precompact_hook_is_listed_but_not_noted_on_every_run(self):
        self.settings_at(self.user_settings(), "PreCompact", "bd prime")
        self.assertEqual([(h.event, h.covers_start) for h in beads.prime_hooks(self.repo)], [("PreCompact", False)])
        self.assertEqual(beads.hook_notes(self.repo), [])  # bd's standard layout: accurate, but noise

    def test_which_matchers_cover_a_session_start(self):
        for matcher, covers in (("", True), ("*", True), ("startup", True), ("startup|resume", True),
                                ("startup|resume|clear|compact", True), ("resume", False), ("compact", False),
                                ("resume|clear", False), ("startupx", False), ("(", False)):
            with self.subTest(matcher=matcher):
                self.settings_at(self.user_settings(), "SessionStart", "bd prime", matcher=matcher)
                self.assertEqual([h.covers_start for h in beads.prime_hooks(self.repo)], [covers])

    def test_a_session_start_hook_that_misses_startup_is_noted_not_counted(self):
        self.settings_at(self.user_settings(), "SessionStart", "bd prime", matcher="compact")
        lines = beads.hook_notes(self.repo)
        self.assertEqual([w for _, w, _ in lines], ["noted"])
        self.assertIn("compact", self.details(lines))

    def test_a_hook_on_an_event_that_is_not_session_start_never_counts(self):
        for event in ("Stop", "PostToolUse", "PreCompact", "UserPromptSubmit"):
            with self.subTest(event=event):
                self.settings_at(self.user_settings(), event, "bd prime")
                self.assertEqual([h.covers_start for h in beads.prime_hooks(self.repo)], [False])

    def test_a_local_prime_hook_under_another_matcher_is_found(self):
        self.settings_at(self.local_settings(), "SessionStart", "bd prime", matcher="startup")
        self.assertEqual([(h.layer, h.event) for h in beads.prime_hooks(self.repo)], [("local", "SessionStart")])

    def test_the_same_event_in_two_layers_is_reported_as_a_duplicate_naming_both(self):
        self.settings_at(self.user_settings(), "SessionStart", "bd prime")
        self.committed("bd prime 2>/dev/null || true")
        lines = beads.hook_notes(self.repo)
        warn = [d for _, w, d in lines if w == "warning"]
        self.assertEqual(len(warn), 1)
        self.assertIn(str(self.user_settings()), warn[0])
        self.assertIn(".claude/settings.json", warn[0])
        self.assertIn("SessionStart", warn[0])

    def test_different_events_are_not_a_duplicate(self):
        import json
        f = self.user_settings()
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"hooks": {
            "SessionStart": [{"matcher": "", "hooks": [{"type": "command", "command": "bd prime"}]}],
            "PreCompact": [{"matcher": "", "hooks": [{"type": "command", "command": "bd prime"}]}]}}))
        self.assertNotIn("warning", [w for _, w, _ in beads.hook_notes(self.repo)])

    def test_no_settings_file_is_ever_edited(self):
        files = [self.settings_at(self.user_settings(), "SessionStart", "bd prime"), self.committed("bd prime || true"),
                 self.settings_at(self.local_settings(), "SessionStart", "bd prime", matcher="startup")]
        before = [f.read_bytes() for f in files]
        beads.hook_notes(self.repo)
        self.assertEqual([f.read_bytes() for f in files], before)

    def test_a_user_hook_that_bd_rejects_is_named_not_counted_and_not_edited(self):
        f = self.settings_at(self.user_settings(), "SessionStart", "bd prime --hook-json")
        before = f.read_bytes()
        self.assertEqual([h.runs for h in beads.prime_hooks(self.repo)], [False])
        lines = beads.hook_notes(self.repo)
        self.assertEqual([w for _, w, _ in lines], ["broken"])
        self.assertIn(str(f), self.details(lines))
        self.assertEqual(f.read_bytes(), before)

    def test_two_entries_whose_matchers_never_overlap_are_not_a_duplicate(self):
        import json
        f = self.repo / ".claude" / "settings.json"
        f.parent.mkdir(exist_ok=True)
        f.write_text(json.dumps({"hooks": {"SessionStart": [
            {"matcher": "startup", "hooks": [{"type": "command", "command": "bd prime"}]},
            {"matcher": "resume", "hooks": [{"type": "command", "command": "bd prime"}]}]}}))
        lines = beads.hook_notes(self.repo)
        self.assertNotIn("warning", [w for _, w, _ in lines])
        self.assertNotIn("more place", self.details(lines))

    def test_entries_whose_matchers_overlap_are_a_duplicate(self):
        import json
        f = self.repo / ".claude" / "settings.json"
        f.parent.mkdir(exist_ok=True)
        f.write_text(json.dumps({"hooks": {"SessionStart": [
            {"matcher": "startup|resume", "hooks": [{"type": "command", "command": "bd prime"}]},
            {"matcher": "resume|clear", "hooks": [{"type": "command", "command": "bd prime"}]},
            {"matcher": "", "hooks": [{"type": "command", "command": "bd prime"}]}]}}))
        self.assertEqual([w for _, w, _ in beads.hook_notes(self.repo)].count("warning"), 1)

    def test_the_duplicate_warning_says_which_entry_to_remove(self):
        self.settings_at(self.user_settings(), "SessionStart", "bd prime")
        self.settings_at(self.local_settings(), "SessionStart", "bd prime", matcher="startup|resume|clear|compact")
        warn = [d for _, w, d in beads.hook_notes(self.repo) if w == "warning"]
        self.assertEqual(len(warn), 1)
        self.assertIn(f"remove the entry in {self.local_settings()}", warn[0])
        self.assertIn("lane-control's own", warn[0])

    def test_the_notes_say_what_is_not_checked(self):
        self.settings_at(self.user_settings(), "SessionStart", "bd prime")
        text = self.details(beads.hook_notes(self.repo))
        for gap in ("managed settings", "plugin hooks", "hook scripts"):
            self.assertIn(gap, text)
        self.assertIn("CLAUDE_CONFIG_DIR", text)  # the user's file read is named, and a session on another folder is not
        self.assertIn(str(self.user_settings()), text)

    def test_when_only_our_own_hook_is_there_the_gap_is_still_said(self):
        self.settings_at(self.local_settings(), "SessionStart", "bd prime", matcher="startup|resume|clear|compact")
        text = self.details(beads.hook_notes(self.repo))
        for gap in ("managed settings", "plugin hooks", "hook scripts"):
            self.assertIn(gap, text)

    def test_a_malformed_settings_file_is_reported_not_crashed_on_or_rewritten(self):
        for layer, f in (("user", self.user_settings()), ("committed", self.repo / ".claude" / "settings.json")):
            with self.subTest(layer=layer):
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("{not json")
                lines = beads.hook_notes(self.repo)
                warn = [d for _, w, d in lines if w == "warning"]
                self.assertEqual(len(warn), 1)
                self.assertIn(str(f), warn[0])
                self.assertIn("not valid JSON", warn[0])
                self.assertEqual(f.read_text(), "{not json")
                f.unlink()

    def test_a_missing_settings_file_is_not_a_problem(self):
        self.assertEqual(beads.hook_notes(self.repo), [])

    def test_without_bd_nothing_is_probed_and_no_hook_is_called_rejected(self):
        self.settings_at(self.user_settings(), "SessionStart", "bd prime")
        self.settings_at(self.local_settings(), "SessionStart", "bd prime", matcher="startup|resume|clear|compact")
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}):
            hooks = beads.prime_hooks(self.repo)
            lines = beads.hook_notes(self.repo)
        self.assertEqual([h.runs for h in hooks], [None, None])  # present, not judged: there is no bd to judge
        self.assertNotIn("broken", [w for _, w, _ in lines])
        self.assertNotIn("rejects", self.details(lines))

    def test_a_rejected_hook_in_the_local_file_says_it_is_the_checkouts_uncommitted_file(self):
        self.settings_at(self.local_settings(), "SessionStart", "bd prime --hook-json", matcher="startup")
        lines = beads.hook_notes(self.repo)
        self.assertEqual([w for _, w, _ in lines], ["broken"])
        text = self.details(lines)
        self.assertIn("uncommitted", text)
        self.assertNotIn("not lane-control's file", text)

    def test_a_timeout_or_failure_to_start_is_not_a_rejection(self):
        self.settings_at(self.user_settings(), "SessionStart", "bd prime")
        for rc in (124, 127):
            with self.subTest(rc=rc), mock.patch.object(beads, "_run", return_value=subprocess.CompletedProcess([], rc, "", "x")):
                self.assertEqual([h.runs for h in beads.prime_hooks(self.repo)], [None])

    def test_common_shell_forms_of_bd_prime_are_recognised(self):
        for command in ("bd prime>/dev/null", "bd prime 2>/dev/null || true", "if command -v bd >/dev/null; then bd prime; fi",
                        "{ bd prime; }", "timeout 10 bd prime", "exec bd prime", "nice bd prime", "! bd prime",
                        "sh -c \"bd prime\"", "bash -lc 'bd prime'", "bd -q prime", "cd . && bd prime", "FOO=1 bd prime",
                        "/usr/local/bin/bd prime", "(bd prime)", "true; bd prime | cat"):
            with self.subTest(command=command):
                self.settings_at(self.user_settings(), "SessionStart", command)
                self.assertEqual([h.command for h in beads.prime_hooks(self.repo)], [command])

    def test_commands_that_do_not_run_bd_prime_are_not_recognised(self):
        for command in ('echo "bd prime"', "echo bd prime", "bd primer", "bd create prime", "abd prime", "bd show prime-1",
                        "# bd prime", "bd prime-x"):
            with self.subTest(command=command):
                self.settings_at(self.user_settings(), "SessionStart", command)
                self.assertEqual(beads.prime_hooks(self.repo), [])

    OURS = "startup|resume|clear|compact"

    def test_our_matcher_is_the_one_the_adapter_writes(self):
        self.assertEqual(beads.OUR_HOOK_MATCHER, self.OURS)

    def test_a_compact_only_overlap_never_tells_the_owner_to_remove_our_hook(self):
        self.settings_at(self.user_settings(), "SessionStart", "bd prime", matcher="compact")
        self.settings_at(self.local_settings(), "SessionStart", "bd prime", matcher=self.OURS)
        lines = beads.hook_notes(self.repo)
        self.assertNotIn("warning", [w for _, w, _ in lines])  # following that advice would leave no start-up prime
        self.assertNotIn("remove the entry", self.details(lines).lower())

    def test_a_hand_added_local_entry_next_to_ours_is_not_called_a_duplicate_of_it(self):
        import json
        f = self.local_settings()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"hooks": {"SessionStart": [
            {"matcher": "resume", "hooks": [{"type": "command", "command": "bd prime"}]},
            {"matcher": self.OURS, "hooks": [{"type": "command", "command": "bd prime"}]}]}}))
        lines = beads.hook_notes(self.repo)
        self.assertNotIn("warning", [w for _, w, _ in lines])

    def test_the_duplicate_advice_names_our_entry_by_its_matcher_when_another_still_covers_start(self):
        import json
        self.settings_at(self.user_settings(), "SessionStart", "bd prime")
        f = self.local_settings()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"hooks": {"SessionStart": [
            {"matcher": "resume", "hooks": [{"type": "command", "command": "bd prime"}]},
            {"matcher": self.OURS, "hooks": [{"type": "command", "command": "bd prime"}]}]}}))
        warn = [d for _, w, d in beads.hook_notes(self.repo) if w == "warning"]
        self.assertEqual(len(warn), 1)
        self.assertIn(f"matcher `{self.OURS}`", warn[0].split("To fix it")[1])  # the advice says which entry is ours

    def test_two_entries_that_both_miss_startup_are_still_reported_without_advice_about_ours(self):
        self.settings_at(self.user_settings(), "SessionStart", "bd prime", matcher="compact")
        self.settings_at(self.repo / ".claude" / "settings.json", "SessionStart", "bd prime", matcher="compact")
        warn = [d for _, w, d in beads.hook_notes(self.repo) if w == "warning"]
        self.assertEqual(len(warn), 1)
        self.assertNotIn("lane-control's own", warn[0])

    def test_only_our_own_entry_is_called_lane_controls_hook(self):
        self.settings_at(self.local_settings(), "SessionStart", "bd prime", matcher="startup")  # added by hand
        self.assertNotIn("lane-control's hook", self.details(beads.hook_notes(self.repo)))
        self.settings_at(self.local_settings(), "SessionStart", "bd prime", matcher=self.OURS)
        self.assertIn("lane-control's hook", self.details(beads.hook_notes(self.repo)))

    def test_bd_prime_quoted_inside_other_text_is_not_a_hook(self):
        for command in ('echo "Tip: bd ready; bd prime"', 'echo "tip: (bd prime)"', 'git commit -m "fix; bd prime"',
                        'echo "x; bd prime"', "echo 'x && bd prime'", 'echo "a \\" ; bd prime"'):
            with self.subTest(command=command):
                self.settings_at(self.user_settings(), "SessionStart", command)
                self.assertEqual(beads.prime_hooks(self.repo), [])

    def test_bd_prime_after_quoted_text_or_inside_a_shell_c_string_is_a_hook(self):
        for command in ('echo "ok"; bd prime', "echo ok; bd prime", 'sh -c "echo hi; bd prime"',
                        "bash -lc 'echo hi && bd prime'", 'echo "a b" && bd prime'):
            with self.subTest(command=command):
                self.settings_at(self.user_settings(), "SessionStart", command)
                self.assertEqual([h.command for h in beads.prime_hooks(self.repo)], [command])

    def test_a_plain_prime_command_is_run_once_per_setup_note(self):
        self.committed("bd prime")
        self.settings_at(self.user_settings(), "SessionStart", "bd prime")
        beads.hook_notes(self.repo)
        self.assertEqual([c for c in self.bd_calls() if c.startswith("prime")], ["prime"])  # the same command, one probe

    def test_the_hook_files_record_names_the_gaps_and_wraps_at_120(self):
        text = (ROOT / "core/harnesses/claude/record.md").read_text()
        flat = " ".join(text.split())
        for gap in ("managed settings", "plugin hooks", "hook scripts", "CLAUDE_CONFIG_DIR"):
            self.assertIn(gap, flat)
        self.assertEqual([n for n, ln in enumerate(text.splitlines(), 1) if len(ln) > 120], [])


class AdoptLayoutTests(BeadsCase):
    """Task 4: a plain `.beads` folder moves to the layout, on the owner's yes, and never at the original's expense."""

    def setUp(self):
        super().setUp()
        self.old = self.repo / ".beads"
        self.old.mkdir()
        (self.old / "config.yaml").write_text("prefix: w\n")
        (self.old / "stub-db").write_text("issue 1\nmemory k\n")
        (self.old / "embeddeddolt").mkdir()
        (self.old / "embeddeddolt" / "data").write_text("db\n")
        (self.repo / ".gitignore").write_text(".beads/\n")  # the folder pattern does not ignore a link
        self.slug = ("acme", "widget-beads")
        self.dest = self.home / "code" / "widget-beads"
        self.asked = []

    def yes(self, answer=True):
        def ask(question):
            self.asked.append(question)
            return answer
        return ask

    def adopt(self, yes="default"):
        yes = self.yes() if yes == "default" else yes
        return beads.adopt_layout(self.repo, beads.inspect(self.repo), self.slug, yes)

    def snapshot(self):
        return sorted((str(p.relative_to(self.repo)), p.read_text() if p.is_file() else None)
                      for p in self.repo.rglob("*") if ".git" not in p.relative_to(self.repo).parts)

    def test_no_terminal_only_describes(self):
        before = self.snapshot()
        lines = self.adopt(yes=None)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.dest.exists())
        self.assertEqual(self.gh_calls(), [])
        text = " ".join(d for _, _, d in lines)
        self.assertIn("acme/widget-beads", text)
        self.assertIn("private", text)
        self.assertFalse([w for _, w, _ in lines if w in beads.PROBLEMS], lines)

    def test_owner_says_no_nothing_changes(self):
        before = self.snapshot()
        lines = self.adopt(yes=self.yes(False))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.dest.exists())
        self.assertNotIn("repo create", " ".join(self.gh_calls()))
        self.assertEqual(lines[-1][1], "skipped")

    def test_the_github_repo_has_its_own_yes(self):
        answers = iter([True, False])
        asked = []
        def ask(q):
            asked.append(q)
            return next(answers)
        lines = self.adopt(yes=ask)
        self.assertEqual(len(asked), 2)
        self.assertIn("GitHub", asked[1])
        self.assertNotIn("repo create", " ".join(self.gh_calls()))
        self.assertTrue(self.old.is_dir() and not self.old.is_symlink())
        self.assertFalse(self.dest.exists())

    def test_folder_moved_and_linked(self):
        lines = self.adopt()
        self.assertFalse([d for _, w, d in lines if w in beads.PROBLEMS], lines)
        self.assertIn("repo create acme/widget-beads --private", self.gh_calls())
        state = beads.inspect(self.repo)
        self.assertEqual((state.kind, state.ignored), ("link", True))
        self.assertEqual(state.beads_repo, self.dest.resolve())
        self.assertEqual(state.git_remote, "https://github.com/acme/widget-beads.git")
        self.assertEqual(state.dolt_remote, "git+https://github.com/acme/widget-beads.git")
        self.assertIn("dolt push", self.bd_calls())
        self.assertEqual((self.dest / ".beads" / "embeddeddolt" / "data").read_text(), "db\n")
        # committed and pushed to the (local stand-in) GitHub repo
        self.assertIn(".beads/config.yaml", git(self.dest, "ls-tree", "-r", "--name-only", "HEAD"))
        # the committed config carries the Dolt remote: a restore reads it from there (bd bootstrap)
        self.assertIn("sync.remote", git(self.dest, "show", "HEAD:.beads/config.yaml"))
        self.assertEqual(git(self.dest, "status", "--porcelain").strip(), "")
        self.assertTrue(git(self.remotes / "widget-beads.git", "log", "--oneline", "main").strip())
        # the original is kept next to the repos, never deleted
        kept = [p for p in (self.home / "code").iterdir() if p.name.startswith("widget.beads-original")]
        self.assertEqual(len(kept), 1)
        self.assertEqual((kept[0] / "embeddeddolt" / "data").read_text(), "db\n")
        # nothing about beads in the project's tracked files
        self.assertEqual(git(self.repo, "status", "--porcelain", "--untracked-files=all").strip().replace("?? .gitignore", ""), "")

    def test_core_hooks_path_into_beads_is_unset(self):
        git(self.repo, "config", "core.hooksPath", ".beads/hooks")
        self.adopt()
        self.assertFalse(beads.inspect(self.repo).hooks_path_into_beads)
        self.assertEqual(subprocess.run(["git", "config", "core.hooksPath"], cwd=self.repo, capture_output=True,
                                        text=True).stdout.strip(), "")

    def test_a_server_mode_database_is_refused_before_anything_is_created(self):
        (self.old / "metadata.json").write_text('{"dolt_mode": "server"}')
        before = self.snapshot()
        lines = self.adopt()
        self.assertIn("server mode", " ".join(d for _, _, d in lines))
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn("repo create", " ".join(self.gh_calls()))
        self.assertFalse(self.dest.exists())
        self.assertNotIn("dolt stop", self.bd_calls())

    def test_tracked_beads_files_stop_the_move_and_name_the_fix(self):
        git(self.repo, "add", "-f", ".beads/config.yaml")
        git(self.repo, "commit", "-qm", "oops tracked")
        before = self.snapshot()
        lines = self.adopt()
        text = " ".join(d for _, _, d in lines)
        self.assertIn(".beads/config.yaml", text)
        self.assertIn("git rm -r --cached .beads", text)
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn("repo create", " ".join(self.gh_calls()))

    def test_a_write_during_the_move_stops_the_switch(self):
        os.environ["BD_DURING_PUSH"] = str(self.old / "stub-db")  # the stub's `dolt push` writes to the original
        before_db = None
        lines = self.adopt()
        os.environ.pop("BD_DURING_PUSH")
        self.assertTrue(self.old.is_dir() and not self.old.is_symlink())  # nothing was switched
        self.assertIn("changed", " ".join(d for _, _, d in lines))
        self.assertIn("written during push", (self.old / "stub-db").read_text())  # the write is where agents see it
        self.assertFalse(list((self.home / "code").glob("widget.beads-original*")))

    def test_a_failure_after_the_repo_is_made_says_how_to_try_again(self):
        os.environ["BD_FAIL"] = "dolt"
        lines = self.adopt()
        text = " ".join(d for _, _, d in lines)
        self.assertIn("delete the GitHub repo acme/widget-beads", text)
        self.assertIn(str(self.dest), text)

    def test_a_leftover_destination_is_not_called_a_restore_source(self):
        self.dest.mkdir()
        text = " ".join(d for _, _, d in self.adopt())
        self.assertIn("delete it", text)
        self.assertNotIn("restore from it", text)

    def test_the_switch_failing_names_the_stale_github_copy(self):
        real = beads._run
        def fail_where_through_the_link(cmd, cwd=None, timeout=60):
            if cmd[:2] == ["bd", "where"] and cwd == self.repo and (self.repo / ".beads").is_symlink():
                return subprocess.CompletedProcess(cmd, 1, "", "no db")
            return real(cmd, cwd, timeout)
        with mock.patch.object(beads, "_run", fail_where_through_the_link):
            lines = self.adopt()
        text = " ".join(d for _, _, d in lines)
        self.assertIn("GitHub holds a copy", text)
        self.assertTrue(self.old.is_dir() and not self.old.is_symlink())

    def test_failed_open_keeps_original(self):
        os.environ["BD_FAIL"] = "memories"
        os.environ["BD_FAIL_IN"] = "widget-beads"  # reads from the old place, not from the new one
        before = self.snapshot()
        lines = self.adopt()
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.old.is_dir() and not self.old.is_symlink())
        self.assertNotIn("dolt push", self.bd_calls())
        self.assertEqual(git(self.remotes / "widget-beads.git", "branch", "--list").strip(), "")  # nothing pushed
        self.assertIn("broken", [w for _, w, _ in lines])
        self.assertIn("original", " ".join(d for _, _, d in lines))

    def test_a_failed_push_keeps_original_too(self):
        os.environ["BD_FAIL"] = "dolt"
        before = self.snapshot()
        lines = self.adopt()
        self.assertEqual(self.snapshot(), before)
        self.assertIn("broken", [w for _, w, _ in lines])

    def test_an_existing_folder_at_the_destination_is_never_touched(self):
        self.dest.mkdir()
        (self.dest / "mine").write_text("x")
        before = self.snapshot()
        lines = self.adopt()
        self.assertEqual((self.dest / "mine").read_text(), "x")
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn("repo create", " ".join(self.gh_calls()))
        self.assertIn("broken", [w for _, w, _ in lines])

    def test_the_leftover_folder_stop_does_not_call_the_github_repo_empty(self):
        self.dest.mkdir()
        text = " ".join(d for _, _, d in self.adopt())
        self.assertNotIn("empty", text)
        self.assertIn("already exists", text)
        self.assertIn("last commit", text)  # what to look at: the earlier move may already have pushed its git copy
        self.assertIn("acme/widget-beads", text)
        self.assertIn(str(self.dest), text)

    def test_the_repo_exists_stop_does_not_call_it_empty_either(self):
        os.environ["GH_EXISTS"] = "1"
        text = " ".join(d for _, _, d in self.adopt())
        self.assertNotIn("empty", text)
        self.assertIn("last commit", text)

    def test_a_github_repo_that_already_exists_stops_the_move(self):
        os.environ["GH_EXISTS"] = "1"
        lines = self.adopt()
        self.assertNotIn("repo create", " ".join(self.gh_calls()))
        self.assertTrue(self.old.is_dir() and not self.old.is_symlink())
        self.assertIn("already exists", " ".join(d for _, _, d in lines))

    def test_gh_not_signed_in_stops_before_asking(self):
        os.environ["GH_NOT_SIGNED_IN"] = "1"
        lines = self.adopt()
        self.assertEqual(self.asked, [])
        self.assertIn("gh auth login", " ".join(d for _, _, d in lines))

    def test_rerun_all_exists(self):
        self.adopt()
        os.environ["BD_DOLT_REMOTE"] = "git+https://github.com/acme/widget-beads.git"
        calls_before = (self.bd_calls(), self.gh_calls(), self.snapshot())
        asked_before = list(self.asked)
        lines = self.adopt()
        self.assertTrue(lines)
        self.assertEqual({w for _, w, _ in lines}, {"exists"}, lines)
        self.assertEqual(self.asked, asked_before)
        self.assertEqual(self.gh_calls(), calls_before[1])
        self.assertEqual(self.snapshot(), calls_before[2])
        writes = [c for c in self.bd_calls()[len(calls_before[0]):] if not c.startswith(("dolt remote list", "where"))]
        self.assertEqual(writes, [])


class RestoreTests(BeadsCase):
    """Task 5: a new machine (or box) gets beads back from the private repo: clone, link, bootstrap the database."""

    def setUp(self):
        super().setUp()
        self.slug = ("acme", "widget-beads")
        # the first machine moves its beads to the layout, which fills the stand-in GitHub repo
        old = self.repo / ".beads"
        old.mkdir()
        (old / "config.yaml").write_text("prefix: w\n")
        lines = beads.adopt_layout(self.repo, beads.inspect(self.repo), self.slug, lambda q: True)
        self.assertFalse([d for _, w, d in lines if w in beads.PROBLEMS], lines)
        # the second machine: its own HOME, a checkout with the same origin, no beads
        self.home2 = self.tmp / "home2"
        (self.home2 / "code").mkdir(parents=True)
        self.repo2 = self.home2 / "code" / "widget"
        self.repo2.mkdir()
        git(self.repo2, "init", "-q", "-b", "main", ".")
        git(self.repo2, "remote", "add", "origin", "https://github.com/acme/widget.git")
        patcher = mock.patch.dict(os.environ, {**testhome.env_for(self.home2), "GH_EXISTS": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.dest2 = self.home2 / "code" / "widget-beads"
        self.gh_log.write_text("")
        self.bd_log.write_text("")
        self.asked = []

    def restore(self, yes="default"):
        def ask(q):
            self.asked.append(q)
            return True
        return beads.restore(self.repo2, self.slug, ask if yes == "default" else yes)

    def problems(self, lines):
        return " ".join(d for _, w, d in lines if w in beads.PROBLEMS)

    def test_restore_clones_links_bootstraps(self):
        lines = self.restore()
        self.assertEqual(self.problems(lines), "")
        self.assertEqual(self.gh_calls()[-1], f"repo clone acme/widget-beads {self.dest2}")
        state = beads.inspect(self.repo2)
        self.assertEqual((state.kind, state.ignored), ("link", True))
        self.assertEqual(state.beads_repo, self.dest2.resolve())
        self.assertIn(f"{self.dest2.resolve()} :: bootstrap --yes", self.bd_log.read_text().replace("//", "/"))
        self.assertNotIn("repo create", " ".join(self.gh_calls()))
        self.assertNotIn(" init", " ".join(self.bd_calls()))  # never a fresh database

    def test_restore_repo_missing_stops(self):
        os.environ.pop("GH_EXISTS")
        lines = self.restore()
        self.assertIn("not found", self.problems(lines))
        self.assertNotIn("repo create", " ".join(self.gh_calls()))
        self.assertNotIn("repo clone", " ".join(self.gh_calls()))
        self.assertFalse(self.dest2.exists() or (self.repo2 / ".beads").is_symlink())

    def test_restore_gh_not_signed_in_stops(self):
        os.environ["GH_NOT_SIGNED_IN"] = "1"
        lines = self.restore()
        text = self.problems(lines)
        self.assertIn("gh auth login", text)
        self.assertIn("fine-grained token", text)
        self.assertNotIn("repo create", " ".join(self.gh_calls()))
        self.assertEqual(self.asked, [])

    def test_restore_no_terminal_only_describes(self):
        lines = self.restore(yes=None)
        self.assertEqual(self.gh_calls(), ["auth status", "repo view acme/widget-beads"])  # read-only
        self.assertFalse(self.dest2.exists() or (self.repo2 / ".beads").is_symlink())
        self.assertIn("acme/widget-beads", " ".join(d for _, _, d in lines))
        self.assertEqual(self.problems(lines), "")

    def test_restore_no_terminal_and_not_signed_in_says_what_sign_in_is_needed(self):
        os.environ["GH_NOT_SIGNED_IN"] = "1"
        lines = self.restore(yes=None)
        text = self.problems(lines)
        self.assertIn("fine-grained token", text)
        self.assertIn("never", text)
        self.assertFalse(self.dest2.exists())

    def test_restore_declined_changes_nothing(self):
        lines = self.restore(yes=lambda q: False)
        self.assertNotIn("repo clone", " ".join(self.gh_calls()))
        self.assertFalse(self.dest2.exists())
        self.assertEqual(lines[-1][1], "skipped")

    def test_restore_without_a_recorded_dolt_remote_stops_before_bootstrap(self):
        # a clone whose config does not name the Dolt remote would bootstrap an EMPTY database: refused
        bare = self.remotes / "widget-beads.git"
        work = self.tmp / "work"
        subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True, capture_output=True)
        (work / ".beads" / "config.yaml").write_text("prefix: w\n")
        git(work, "commit", "-qam", "drop the remote")
        git(work, "push", "-q", "origin", "main")
        lines = self.restore()
        self.assertIn("Dolt remote", self.problems(lines))
        self.assertNotIn("bootstrap", " ".join(self.bd_calls()))
        self.assertFalse((self.repo2 / ".beads").is_symlink())

    def test_a_failed_bootstrap_removes_the_link_and_the_clone_it_made(self):
        os.environ["BD_FAIL"] = "bootstrap"
        lines = self.restore()
        text = self.problems(lines)
        self.assertIn("bootstrap", text)
        self.assertIn("was removed", text)
        self.assertNotIn("Nothing was created", text)
        self.assertFalse((self.repo2 / ".beads").is_symlink())
        self.assertFalse(self.dest2.exists())  # a leftover clone's issues.jsonl could seed an issues-only database

    def test_a_failed_bootstrap_keeps_a_clone_it_did_not_make(self):
        subprocess.run(["git", "clone", "-q", str(self.remotes / "widget-beads.git"), str(self.dest2)], check=True,
                       capture_output=True)
        git(self.dest2, "remote", "set-url", "origin", "https://github.com/acme/widget-beads.git")
        os.environ["BD_FAIL"] = "bootstrap"
        self.assertIn("was kept", self.problems(self.restore()))
        self.assertTrue(self.dest2.is_dir())

    def test_git_that_cannot_read_the_repo_over_https_names_setup_git(self):
        shutil.rmtree(self.remotes / "widget-beads.git")  # gh says the repo exists; git cannot read it
        text = self.problems(self.restore())
        self.assertIn("gh auth setup-git", text)
        self.assertNotIn("repo clone", " ".join(self.gh_calls()))
        self.assertEqual(self.asked, [])

    def test_the_sign_in_advice_names_setup_git(self):
        os.environ["GH_NOT_SIGNED_IN"] = "1"
        self.assertIn("gh auth setup-git", self.problems(self.restore()))

    def test_the_clone_is_private_to_the_owner(self):
        self.restore()
        self.assertEqual(oct((self.dest2 / ".beads").stat().st_mode & 0o777), "0o700")

    def test_an_existing_checkout_of_another_repo_is_not_reused(self):
        subprocess.run(["git", "clone", "-q", str(self.remotes / "widget-beads.git"), str(self.dest2)], check=True,
                       capture_output=True)
        git(self.dest2, "remote", "set-url", "origin", "https://github.com/other/thing.git")
        self.assertIn("not a checkout of acme/widget-beads", self.problems(self.restore()))

    def reuse_with_origin(self, url: str):
        subprocess.run(["git", "clone", "-q", str(self.remotes / "widget-beads.git"), str(self.dest2)], check=True,
                       capture_output=True)
        git(self.dest2, "remote", "set-url", "origin", url)
        lines = self.restore()
        return lines

    def test_an_existing_clone_over_ssh_is_reused(self):
        for url in ("git@github.com:acme/widget-beads.git", "git@github.com:acme/widget-beads",
                    "ssh://git@github.com/acme/widget-beads.git", "https://github.com/acme/widget-beads",
                    "https://github.com/Acme/Widget-Beads.git"):
            with self.subTest(url=url):
                shutil.rmtree(self.dest2, ignore_errors=True)
                lines = self.reuse_with_origin(url)
                self.assertEqual(self.problems(lines), "")
                self.assertNotIn("repo clone", " ".join(self.gh_calls()))
                if (self.repo2 / ".beads").is_symlink():
                    (self.repo2 / ".beads").unlink()

    def test_an_existing_ssh_clone_of_another_repo_is_not_reused(self):
        for url in ("git@github.com:other/widget-beads.git", "git@github.com:acme/thing-beads.git"):
            with self.subTest(url=url):
                shutil.rmtree(self.dest2, ignore_errors=True)
                self.assertIn("not a checkout of acme/widget-beads", self.problems(self.reuse_with_origin(url)))

    def test_a_clone_of_the_right_repo_with_no_beads_folder_says_so(self):
        subprocess.run(["git", "clone", "-q", str(self.remotes / "widget-beads.git"), str(self.dest2)], check=True,
                       capture_output=True)
        git(self.dest2, "remote", "set-url", "origin", "https://github.com/acme/widget-beads.git")
        shutil.rmtree(self.dest2 / ".beads", ignore_errors=True)
        text = self.problems(self.restore())
        self.assertIn("no .beads folder", text)
        self.assertNotIn("not recognised", text)  # the origin is right; do not contradict it

    def test_an_origin_that_is_not_recognised_is_named_in_the_refusal(self):
        for url in ("git@github-work:acme/widget-beads.git", "https://github.com/acme/widget-beads-old.git",
                    "https://tok:secret@gitlab.com/acme/widget-beads.git"):
            with self.subTest(url=url):
                shutil.rmtree(self.dest2, ignore_errors=True)
                text = self.problems(self.reuse_with_origin(url))
                self.assertIn("not a checkout of acme/widget-beads", text)
                self.assertIn("not recognised", text)
                self.assertNotIn("secret", text)  # a token in an origin URL is never printed

    def test_a_folder_in_the_way_is_never_touched(self):
        self.dest2.mkdir()
        (self.dest2 / "mine").write_text("x")
        lines = self.restore()
        self.assertEqual((self.dest2 / "mine").read_text(), "x")
        self.assertIn("already exists", self.problems(lines))

    def test_a_beads_workspace_already_there_means_nothing_to_restore(self):
        (self.repo2 / ".beads").mkdir()
        self.assertEqual(self.restore(), [])
        self.assertEqual(self.gh_calls(), [])

    def test_an_existing_clone_is_reused_not_cloned_again(self):
        subprocess.run(["git", "clone", "-q", str(self.remotes / "widget-beads.git"), str(self.dest2)], check=True,
                       capture_output=True)
        git(self.dest2, "remote", "set-url", "origin", "https://github.com/acme/widget-beads.git")
        lines = self.restore()
        self.assertEqual(self.problems(lines), "")
        self.assertNotIn("repo clone", " ".join(self.gh_calls()))
        self.assertEqual(beads.inspect(self.repo2).kind, "link")


if __name__ == "__main__":
    unittest.main()
