"""Beads round trip with the real bd (opt-in: LANE_TEST_REAL_BD=1). Everything happens in temp dirs against a local
file:// Dolt remote: no real beads workspace, no GitHub. This is the gate for the restore design: issues AND memories
must come back from the Dolt remote."""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
import beads  # noqa: E402

REAL = bool(os.environ.get("LANE_TEST_REAL_BD")) and shutil.which("bd") is not None


def bd(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, BD_NON_INTERACTIVE="1")
    return subprocess.run(["bd", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)


def git(cwd: Path, *args: str):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=cwd,
                   check=True, capture_output=True, text=True)


@unittest.skipUnless(REAL, "set LANE_TEST_REAL_BD=1 (needs bd on PATH)")
class DoltRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lc-bd-rt-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.remote = self.tmp / "remote"
        self.remote.mkdir()
        self.src = self.tmp / "src"
        self.src.mkdir()
        git(self.src, "init", "-q", ".")
        self.assertEqual(bd(self.src, "init", "-p", "rt", "--quiet").returncode, 0)
        self.assertEqual(bd(self.src, "create", "round trip issue", "-p", "2").returncode, 0)
        self.assertEqual(bd(self.src, "remember", "--key", "k", "v value").returncode, 0)
        self.assertEqual(bd(self.src, "dolt", "remote", "add", "origin", f"file://{self.remote}").returncode, 0)
        pushed = bd(self.src, "dolt", "push")
        self.assertEqual(pushed.returncode, 0, pushed.stdout + pushed.stderr)

    def assert_restored(self, where: Path):
        issues = bd(where, "list", "--all")
        self.assertIn("round trip issue", issues.stdout, issues.stdout + issues.stderr)
        mems = bd(where, "memories")
        self.assertIn("k", mems.stdout)
        self.assertIn("v value", mems.stdout, mems.stdout + mems.stderr)

    def test_memories_survive_dolt_roundtrip(self):
        """The restore lane-control uses: clone the beads repo (config.yaml carries sync.remote), then bootstrap."""
        git(self.src, "add", ".beads")
        git(self.src, "commit", "-qm", "beads", "--allow-empty")
        clone = self.tmp / "clone"
        subprocess.run(["git", "clone", "-q", str(self.src), str(clone)], check=True, capture_output=True)
        self.assertFalse((clone / ".beads" / "embeddeddolt").exists(), "the database must not travel in git")
        done = bd(clone, "bootstrap", "--yes")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assert_restored(clone)

    def test_memories_survive_init_from_remote(self):
        """Fallback form: a fresh bd init that clones the Dolt remote."""
        fresh = self.tmp / "fresh"
        fresh.mkdir()
        git(fresh, "init", "-q", ".")
        done = bd(fresh, "init", "-p", "rt", "--remote", f"file://{self.remote}", "--quiet")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assert_restored(fresh)


GH_STUB = """#!/bin/sh
# gh stand-in. `repo create <o>/<n>` makes a bare repo $GH_REMOTES/<n>.git, which git reaches as https://github.com/<o>/<n>
# through url.insteadOf; `repo view` finds it there; `repo clone` clones it.
n="${3#*/}"
case "$1 $2" in
  "auth status") exit 0 ;;
  "repo view") [ -d "$GH_REMOTES/$n.git" ] && exit 0; exit 1 ;;
  "repo create") git init -q --bare -b main "$GH_REMOTES/$n.git"; exit 0 ;;
  "repo clone") git clone -q "$GH_REMOTES/$n.git" "$4"; exit $? ;;
esac
exit 0
"""


@unittest.skipUnless(REAL, "set LANE_TEST_REAL_BD=1 (needs bd on PATH)")
class AdoptAndRestoreEndToEnd(unittest.TestCase):
    """core/beads.py adopt_layout and restore with the REAL bd, on a first and a second machine (two HOMEs). GitHub is
    bare repos under a temp dir (stub gh, url.insteadOf), including the Dolt remote, which bd reaches through git."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lc-bd-e2e-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.remotes = self.tmp / "remotes"
        self.remotes.mkdir()
        (self.tmp / "bin").mkdir()
        gh = self.tmp / "bin" / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        self.env = {"PATH": f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}", "BD_NON_INTERACTIVE": "1",
                    "GH_REMOTES": str(self.remotes), "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": f"url.file://{self.remotes}/.insteadOf",
                    "GIT_CONFIG_VALUE_0": "https://github.com/acme/", "GIT_TERMINAL_PROMPT": "0",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}

    def machine(self, name: str):
        home = self.tmp / name
        (home / "code").mkdir(parents=True)
        patcher = mock.patch.dict(os.environ, {**self.env, "HOME": str(home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        repo = home / "code" / "widget"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main", ".")
        git(repo, "remote", "add", "origin", "https://github.com/acme/widget.git")
        return home, repo

    def test_move_then_restore_on_another_machine(self):
        home1, repo1 = self.machine("m1")
        self.assertEqual(bd(repo1, "init", "-p", "w", "--quiet").returncode, 0)
        self.assertEqual(bd(repo1, "create", "first issue", "-p", "2").returncode, 0)
        self.assertEqual(bd(repo1, "remember", "--key", "k", "v value").returncode, 0)
        git(repo1, "rm", "-r", "-q", "--cached", "--ignore-unmatch", ".beads")  # bd init stages them: not a tracked project
        (repo1 / ".gitignore").write_text(".beads/\n")
        slug = ("acme", "widget-beads")
        lines = beads.adopt_layout(repo1, beads.inspect(repo1), slug, lambda q: True)
        self.assertEqual([d for _, w, d in lines if w in beads.PROBLEMS], [], lines)
        state = beads.inspect(repo1)
        self.assertEqual((state.kind, state.ignored, state.hooks_path_into_beads), ("link", True, False))
        self.assertTrue(state.dolt_remote and state.git_remote)
        self.assertEqual(state.unpushed, 0)
        self.assertIn("first issue", bd(repo1, "list", "--all").stdout)  # through the link
        self.assertIn("v value", bd(repo1, "memories").stdout)
        # a re-run changes nothing and says exists
        again = beads.adopt_layout(repo1, state, slug, lambda q: self.fail("asked again"))
        self.assertEqual({w for _, w, _ in again}, {"exists"}, again)
        # the second machine restores issues and memories from the stand-in GitHub
        home2, repo2 = self.machine("m2")
        out = beads.restore(repo2, slug, lambda q: True)
        self.assertEqual([d for _, w, d in out if w in beads.PROBLEMS], [], out)
        self.assertIn("first issue", bd(repo2, "list", "--all").stdout)
        mems = bd(repo2, "memories").stdout
        self.assertIn("k", mems)
        self.assertIn("v value", mems)
        self.assertEqual(beads.inspect(repo2).kind, "link")


if __name__ == "__main__":
    unittest.main()
