"""core/beads.py: how lane-control looks at, checks, moves and restores a project's beads (design:
docs/superpowers/specs/2026-11-22-beads-handling-design.md).

Real git in temp dirs; bd, gh are stubs on a temp PATH that log every call. HOME is a temp dir. Nothing here reaches a real
beads workspace, GitHub or a real bd (that is tests/test_beads_roundtrip.py, opt-in)."""
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
  "dolt remote add") echo "$5" > "$state"; exit 0 ;;
esac
exit 0
"""

# gh stand-in: logs calls to $GH_LOG. `auth status` succeeds unless $GH_NOT_SIGNED_IN; `repo view` fails (not found)
# unless $GH_EXISTS; `repo create <o>/<n> --private` makes a bare repo $GH_REMOTES/<n>.git (git is told to send
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
        patcher = mock.patch.dict(os.environ, {"PATH": path, "HOME": str(self.home), "BD_LOG": str(self.bd_log),
                                             "GH_LOG": str(self.gh_log), "GH_REMOTES": str(self.remotes),
                                             "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"url.file://{self.remotes}/.insteadOf",
                                             "GIT_CONFIG_VALUE_0": "https://github.com/acme/",
                                             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                                             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ("BD_FAIL", "BD_FAIL_IN", "BD_DOLT_REMOTE", "BD_VERSION", "GH_NOT_SIGNED_IN", "GH_EXISTS", "GH_FAIL"):
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
        self.assertIn(".gitignore", text)
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

    def test_only_bd_prime_is_ever_run_from_a_committed_file(self):
        self.committed("bd create evil")
        self.assertEqual([c.split()[0:2] for c in self.bd_calls()], [])
        beads.committed_bd_hooks(self.repo)
        self.assertEqual(self.bd_calls(), ["create --help"])  # a --help probe, never the command itself


class AdoptLayoutTests(BeadsCase):
    """Task 4: a plain `.beads` folder moves to the layout, on the owner's yes, and never at the original's expense."""

    def setUp(self):
        super().setUp()
        self.old = self.repo / ".beads"
        self.old.mkdir()
        (self.old / "config.yaml").write_text("prefix: w\n")
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

    def test_a_server_mode_database_is_stopped_before_it_is_copied(self):
        (self.old / "metadata.json").write_text('{"dolt_mode": "server"}')
        self.adopt()
        calls = self.bd_calls()
        self.assertIn("dolt stop", calls)
        self.assertLess(calls.index("dolt stop"), calls.index("dolt push"))

    def test_failed_open_keeps_original(self):
        os.environ["BD_FAIL"] = "where"
        os.environ["BD_FAIL_IN"] = "widget-beads"  # opens from the old place, not from the new one
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


if __name__ == "__main__":
    unittest.main()
