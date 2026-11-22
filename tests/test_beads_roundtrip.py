"""Beads round trip with the real bd (opt-in: LANE_TEST_REAL_BD=1). Everything happens in temp dirs against a local
file:// Dolt remote: no real beads workspace, no GitHub. This is the gate for the restore design: issues AND memories
must come back from the Dolt remote."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
