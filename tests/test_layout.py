"""The repo layout: the root marker, the root lookup (Python and bash), and the old script paths kept for machines."""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Every file under scripts/ before the orchestrator split: machines, units and docs still call them by these paths.
OLD_SCRIPT_PATHS = ("cotal-agent", "docs-review-launch.sh", "lane-heavy", "lane-limits.sh", "lane-room", "lane-tier",
                    "lib.sh", "machine-coordinator-setup", "machine-night-watch.sh", "orca-cotal-task",
                    "orca-host-install", "park-check.sh", "project-pause", "project-setup", "stale-claims.sh",
                    "window-guard-install", "window-guard.sh", "wsp-owner-code.mjs")
SOURCED_ONLY = ("lane-limits.sh", "lib.sh", "window-guard.sh", "wsp-owner-code.mjs")  # read, not run (as on main)


def load_lcroot():
    spec = importlib.util.spec_from_file_location("lcroot", ROOT / "core" / "lcroot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RootLookupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)

    def test_find_root_from_nested_dir(self):
        lcroot = load_lcroot()
        self.assertEqual(lcroot.find_root(ROOT / "tests" / "stubs"), ROOT)

    def test_find_root_through_symlink(self):
        lcroot = load_lcroot()
        link = self.t / "link"
        link.symlink_to(ROOT / "tests" / "stubs" / "fake.py")
        self.assertEqual(lcroot.find_root(link), ROOT)

    def test_find_root_missing_marker_raises(self):
        lcroot = load_lcroot()
        (self.t / "a" / "b").mkdir(parents=True)
        with self.assertRaises(FileNotFoundError):
            lcroot.find_root(self.t / "a" / "b")

    def test_lc_root_in_bash_through_symlink(self):
        conf = self.t / "conf.sh"
        conf.write_text("")
        fake = self.t / "fake-root"
        (fake / "deep" / "er").mkdir(parents=True)
        (fake / ".lane-control-root").write_text("")
        script = fake / "deep" / "er" / "probe"
        script.write_text(f'#!/usr/bin/env bash\n. "{ROOT}/scripts/lib.sh"\nlc_root\n')
        script.chmod(0o755)
        link = self.t / "probe-link"
        link.symlink_to(script)
        r = subprocess.run([str(link)], capture_output=True, text=True, cwd=self.t,
                           env={**os.environ, "PLAYBOOK_CONFIG": str(conf)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), str(fake.resolve()))

    def test_lc_root_in_bash_without_marker_fails(self):
        conf = self.t / "conf.sh"
        conf.write_text("")
        script = self.t / "probe"
        script.write_text(f'#!/usr/bin/env bash\n. "{ROOT}/scripts/lib.sh"\nlc_root\n')
        script.chmod(0o755)
        r = subprocess.run([str(script)], capture_output=True, text=True, cwd=self.t,
                           env={**os.environ, "PLAYBOOK_CONFIG": str(conf)})
        self.assertEqual(r.returncode, 1)
        self.assertIn("lane-control root not found", r.stderr)


class OldPathsTest(unittest.TestCase):
    def test_old_script_paths_resolve(self):
        for name in OLD_SCRIPT_PATHS:
            p = ROOT / "scripts" / name
            self.assertTrue(p.is_file(), name)  # follows a symlink to its new home
            if name not in SOURCED_ONLY:
                self.assertTrue(os.access(p, os.X_OK), name)


if __name__ == "__main__":
    unittest.main()
