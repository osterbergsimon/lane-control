"""tests/testhome.py (lc-oh7l): the suite runs in a temp HOME, and fails when anything under the real lane-control
folders or ~/.agents changed while it ran. The "real" home in these tests is a temp dir too."""
import testhome  # noqa: I001  first: temp HOME and XDG_*, and the watch on the real ones

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent


class DefaultHomeTests(unittest.TestCase):
    def test_home_and_every_xdg_dir_are_temp(self):
        home = os.environ["HOME"]
        self.assertNotIn(home, [str(h) for h in testhome.REAL_HOMES])
        self.assertTrue(home.startswith(tempfile.gettempdir()), home)
        for k, v in testhome.env_for(home).items():
            self.assertEqual(os.environ[k], v, k)
        self.assertEqual(Path.home(), Path(home))

    def test_env_for_puts_all_five_under_the_home(self):
        self.assertEqual(testhome.env_for("/x"), {
            "HOME": "/x", "XDG_CONFIG_HOME": "/x/.config", "XDG_DATA_HOME": "/x/.local/share",
            "XDG_STATE_HOME": "/x/.local/state", "XDG_CACHE_HOME": "/x/.cache"})

    def test_no_file_override_from_the_shell_reaches_a_test(self):
        for k in testhome.FILE_OVERRIDES:
            self.assertNotIn(k, os.environ)
        for k in ("LANE_AWAY_FILE", "LANE_BUDGET_STOPPED_FILE", "LANE_LIMITS_FILE", "LANE_PRIVATE_NAMES"):
            self.assertIn(k, testhome.FILE_OVERRIDES)
        self.assertNotIn("LANE_TEST_WATCH_HOMES", os.environ)  # WholeRunTests' child only; never handed on

    def test_real_home_is_the_opt_out_and_is_undone(self):
        before = dict(os.environ)
        with testhome.real_home():
            self.assertEqual(os.environ.get("HOME"), testhome.ORIGINAL_ENV.get("HOME"))
            self.assertEqual(os.environ.get("XDG_CONFIG_HOME"), testhome.ORIGINAL_ENV.get("XDG_CONFIG_HOME"))
        self.assertEqual(dict(os.environ), before)

    def test_every_test_module_imports_it_before_anything_that_could_read_home(self):
        late = []
        for f in sorted(TESTS.glob("test_*.py")):
            imports = [x for x in f.read_text().splitlines() if x.startswith(("import ", "from "))]
            if not imports or not imports[0].startswith("import testhome"):
                late.append(f.name)
        self.assertEqual(late, [], "each tests/test_*.py has `import testhome` as its first import")


class WatchTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.home = Path(t.name)
        self.conf = self.home / ".config" / "lane-control"
        self.conf.mkdir(parents=True)
        (self.conf / "away").write_text("")
        os.utime(self.conf / "away", (1_000_000, 1_000_000))
        self.roots = testhome.watch_roots([self.home], {})
        self.before = testhome.snapshot(self.roots)

    def changed(self):
        return testhome.differences(self.before, testhome.snapshot(self.roots))

    def test_the_watched_folders(self):
        self.assertEqual([str(r.relative_to(self.home)) for r in self.roots],
                         [".agents", ".config/lane-control", ".local/share/lane-control", ".local/state/lane-control"])

    def test_a_real_xdg_dir_is_watched_too(self):
        roots = testhome.watch_roots([self.home], {"XDG_CONFIG_HOME": "/c", "XDG_STATE_HOME": "relative"})
        self.assertIn(Path("/c/lane-control"), roots)
        self.assertNotIn(Path("relative/lane-control"), roots)

    def test_nothing_changed(self):
        self.assertEqual(self.changed(), [])

    def test_a_moved_mtime_alone_is_named(self):  # what lc-oh7l saw
        os.utime(self.conf / "away")
        self.assertEqual(self.changed(), [f"changed: {self.conf / 'away'}"])

    def test_a_new_file_and_a_removed_one_are_named(self):
        (self.conf / "away").unlink()
        (self.conf / "new").write_text("x")
        got = self.changed()
        self.assertIn(f"removed: {self.conf / 'away'}", got)
        self.assertIn(f"new: {self.conf / 'new'}", got)

    def test_a_file_written_and_removed_again_still_shows_on_its_folder(self):
        os.utime(self.conf, (1_000_000, 1_000_000))
        self.before = testhome.snapshot(self.roots)
        (self.conf / "refused").mkdir()
        (self.conf / "refused").rmdir()
        self.assertEqual(self.changed(), [f"changed: {self.conf}"])

    def test_a_watched_folder_that_appears_is_named(self):
        (self.home / ".agents").mkdir()
        self.assertEqual(self.changed(), [f"new: {self.home / '.agents'}"])

    def test_a_link_is_not_followed(self):
        outside = self.home / "outside"
        outside.mkdir()
        (self.conf / "link").symlink_to(outside)
        self.before = testhome.snapshot(self.roots)
        (outside / "f").write_text("x")
        self.assertEqual(self.changed(), [])


class WholeRunTests(unittest.TestCase):
    """A copy of the helper and the guard module beside one leaking test, run the way the suite is run. The child's
    HOME is a temp dir standing for the real one."""

    LEAK = """import testhome  # noqa
import os, subprocess, unittest


class Leak(unittest.TestCase):
    def test_leak(self):
        # a child that is not Python (a Python audit hook would not see it), aimed at the real home
        subprocess.run(["sh", "-c", os.environ.get("LEAK", "true")], check=True,
                       env={"PATH": os.environ["PATH"], "REAL": str(testhome.ORIGINAL_ENV["HOME"])})
"""

    def run_suite(self, leak, *args):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        top = Path(t.name)
        (top / "tests").mkdir()
        (top / "real").mkdir()
        for name in ("testhome.py", "test_zz_real_home.py"):
            shutil.copy(TESTS / name, top / "tests" / name)
        (top / "tests" / "test_leak.py").write_text(self.LEAK)
        env = dict(os.environ, HOME=str(top / "real"), LEAK=leak, PYTHONDONTWRITEBYTECODE="1")
        for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
            env.pop(k)
        # only the stand-in is watched here: the machine's own folders may change while this runs
        env["LANE_TEST_WATCH_HOMES"] = str(top / "real")
        return subprocess.run([sys.executable, "-m", "unittest", *args], cwd=top, env=env, capture_output=True,
                              text=True, timeout=60), top / "real"

    def test_a_clean_run_passes(self):
        r, _ = self.run_suite("true", "discover", "-s", "tests")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Ran 2 tests", r.stderr)

    def test_a_write_by_a_child_fails_the_suite_and_names_the_path(self):
        r, real = self.run_suite('mkdir -p "$REAL/.config/lane-control" && : >"$REAL/.config/lane-control/away"',
                                 "discover", "-s", "tests")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("FAIL: test_nothing_real_changed", r.stderr)
        self.assertIn(f"new: {real / '.config' / 'lane-control'}", r.stderr)

    def test_a_run_of_one_module_fails_too(self):  # the guard module is not in this run: the check at exit
        r, real = self.run_suite('mkdir -p "$REAL/.agents"', "discover", "-s", "tests", "-p", "test_leak.py")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn(f"new: {real / '.agents'}", r.stderr)


if __name__ == "__main__":
    unittest.main()
