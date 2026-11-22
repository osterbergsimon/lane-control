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
[ "$BD_FAIL" = "$1" ] && { echo "bd: $1 failed" >&2; exit 1; }
case "$1 $2 $3" in
  "dolt remote list") [ -n "$BD_DOLT_REMOTE" ] && echo "origin               $BD_DOLT_REMOTE"; exit 0 ;;
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
        path = f"{self.bin}{os.pathsep}{os.environ['PATH']}"
        patcher = mock.patch.dict(os.environ, {"PATH": path, "HOME": str(self.home), "BD_LOG": str(self.bd_log)})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ("BD_FAIL", "BD_DOLT_REMOTE", "BD_VERSION"):
            os.environ.pop(k, None)
        self.repo = self.home / "code" / "widget"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main", ".")
        git(self.repo, "remote", "add", "origin", "https://github.com/acme/widget.git")

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


if __name__ == "__main__":
    unittest.main()
