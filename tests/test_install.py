"""INST-1 (lc-7gk): one versioned lane-control tree per machine and per box (core/install.py, core/scripts/lane-control).

A scratch HOME and XDG_DATA_HOME, and a small lane-control repo made here (the marker, a lane tool, a persona), so a
test controls its commits."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "core" / "scripts" / "lane-control"


def load_install():
    spec = importlib.util.spec_from_file_location("lc_install_t", ROOT / "core" / "install.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(*a, cwd):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


class Scratch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.data = self.tmp / "data"
        self.env = {**os.environ, "HOME": str(self.home), "XDG_DATA_HOME": str(self.data),
                    "XDG_CONFIG_HOME": str(self.home / ".config")}
        self.old_env = dict(os.environ)
        os.environ.update(HOME=str(self.home), XDG_DATA_HOME=str(self.data))
        self.src = self.tmp / "lane-control"
        (self.src / "core" / "scripts").mkdir(parents=True)
        (self.src / ".lane-control-root").write_text("")
        tool = self.src / "core" / "scripts" / "lane-heavy"
        tool.write_text("#!/bin/sh\necho one\n")
        tool.chmod(0o755)
        (self.src / "scripts").mkdir()
        (self.src / "scripts" / "lane-heavy").symlink_to("../core/scripts/lane-heavy")
        git("init", "-q", "-b", "main", cwd=self.src)
        git("add", "-A", cwd=self.src)
        git("commit", "-qm", "one", cwd=self.src)
        self.one = git("rev-parse", "HEAD", cwd=self.src)
        self.I = load_install()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        self._tmp.cleanup()

    def commit_two(self):
        (self.src / "core" / "scripts" / "lane-heavy").write_text("#!/bin/sh\necho two\n")
        git("commit", "-qam", "two", cwd=self.src)
        return git("rev-parse", "HEAD", cwd=self.src)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *args], env=self.env, capture_output=True, text=True)

    @property
    def root(self):
        return self.data / "lane-control"


class InstallTests(Scratch):
    def test_install_lays_the_whole_tree_and_switches_current(self):
        r = self.cli("install", "--from", str(self.src))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"installed {self.one}", r.stdout)
        tree = self.root / self.one
        self.assertEqual(os.readlink(self.root / "current"), self.one)
        listed = set(git("ls-tree", "-r", "--name-only", self.one, cwd=self.src).split())
        laid = {str(p.relative_to(tree)) for p in tree.rglob("*") if p.is_file() or p.is_symlink()}
        self.assertEqual(laid, listed | {self.I.MARK})
        self.assertTrue((tree / "scripts" / "lane-heavy").is_symlink())  # git archive keeps the links
        self.assertEqual((tree / self.I.MARK).read_text().strip(), self.one)

    def test_install_twice_is_idempotent(self):
        self.cli("install", "--from", str(self.src))
        ino = (self.root / self.one).stat().st_ino
        r = self.cli("install", "--from", str(self.src))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"exists {self.one}", r.stdout)
        self.assertEqual((self.root / self.one).stat().st_ino, ino)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), sorted([self.one, "current", "source"]))

    def test_a_new_commit_switches_current_and_keeps_the_old_tree(self):
        self.cli("install", "--from", str(self.src))
        old_tool = self.root / "current" / "core" / "scripts" / "lane-heavy"
        with open(old_tool) as running:  # a running script keeps the file it opened
            two = self.commit_two()
            r = self.cli("install", "--from", str(self.src))
            self.assertIn(f"installed {two}", r.stdout)
            self.assertIn(f"was {self.one[:12]}, now {two[:12]}", r.stdout)
            self.assertEqual(running.read(), "#!/bin/sh\necho one\n")
        self.assertEqual(os.readlink(self.root / "current"), two)
        self.assertTrue((self.root / self.one).is_dir())
        r = self.cli("install", "--from", str(self.src), "--version", self.one)
        self.assertIn(f"exists {self.one}", r.stdout)
        self.assertEqual(os.readlink(self.root / "current"), self.one)

    def test_current_is_switched_by_rename(self):
        seen = []
        real = os.replace
        def spy(a, b):
            seen.append((Path(a).name, Path(b).name))
            return real(a, b)
        self.I.os.replace = spy
        try:
            self.I.install(self.src)
        finally:
            self.I.os.replace = real
        self.assertIn("current", [b for _, b in seen])
        self.assertTrue((self.root / "current").is_symlink())

    def test_an_unknown_version_installs_nothing(self):
        r = self.cli("install", "--from", str(self.src), "--version", "no-such-tag")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not a commit", r.stderr)
        self.assertFalse((self.root / "current").exists())

    def test_the_bin_tools_are_links_into_current(self):
        self.cli("install", "--from", str(self.src))
        tool = self.home / ".agents" / "bin" / "lane-heavy"
        self.assertTrue(tool.is_symlink())
        self.assertEqual(os.readlink(tool), str(self.root / "current" / "core" / "scripts" / "lane-heavy"))
        two = self.commit_two()
        self.cli("install", "--from", str(self.src))
        self.assertEqual(subprocess.run([str(tool)], capture_output=True, text=True).stdout, "two\n")
        self.assertEqual(os.readlink(self.root / "current"), two)

    def test_an_old_copy_in_bin_becomes_a_link(self):
        bin_dir = self.home / ".agents" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "lane-heavy").write_text("#!/bin/sh\necho copy\n")
        r = self.cli("install", "--from", str(self.src))
        self.assertIn("updated lane-heavy", r.stdout)
        self.assertTrue((bin_dir / "lane-heavy").is_symlink())

    def test_the_last_source_is_remembered_for_an_installed_cli(self):
        self.cli("install", "--from", str(self.src))
        self.assertEqual(self.I.source(), self.src.resolve())

    def test_status_says_whether_current_and_the_checkout_differ(self):
        r = self.cli("status", "--from", str(self.src))
        self.assertIn("not installed", r.stdout)
        self.cli("install", "--from", str(self.src))
        r = self.cli("status", "--from", str(self.src))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(f"current: {self.one}", r.stdout)
        self.assertIn("state: same", r.stdout)
        self.commit_two()
        r = self.cli("status", "--from", str(self.src))
        self.assertEqual(r.returncode, 1)
        self.assertIn("state: differ", r.stdout)


class UseTests(Scratch):
    def test_from_a_checkout_the_stable_path_is_current(self):
        u = self.I.use(self.src)
        self.assertEqual(u.stable, self.root / "current")
        self.assertEqual(u.sha, self.one)
        self.assertEqual(u.state, "installed")
        self.assertEqual(self.I.use(self.src).state, "exists")

    def test_from_an_installed_tree_it_is_used_as_it_is(self):
        self.I.install(self.src)
        u = self.I.use(self.root / self.one)
        self.assertEqual((u.state, u.sha, u.stable), ("used", self.one, self.root / "current"))

    def test_a_dirty_checkout_is_named(self):
        (self.src / "core" / "scripts" / "lane-heavy").write_text("changed\n")
        u = self.I.use(self.src)
        self.assertTrue(any("uncommitted" in n for n in u.notes), u.notes)

    def test_an_old_machine_is_told_it_moves_to_the_install(self):
        bin_dir = self.home / ".agents" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "lane-heavy").write_text("#!/bin/sh\necho copy\n")
        u = self.I.use(self.src)
        self.assertTrue(any("moves to the install" in n for n in u.notes), u.notes)

    def test_a_plain_copy_is_used_as_before(self):
        copy = self.tmp / "copy"
        copy.mkdir()
        (copy / ".lane-control-root").write_text("")
        u = self.I.use(copy)
        self.assertEqual((u.sha, u.stable), (None, copy.resolve()))


class BoxScriptTests(Scratch):
    def test_the_box_gets_the_whole_tree_at_the_same_sha(self):
        self.I.install(self.src)
        box = self.tmp / "box"
        box.mkdir()
        env = {**os.environ, "HOME": str(box), "XDG_DATA_HOME": ""}
        env.pop("XDG_DATA_HOME")
        for want in ("installed", "exists"):
            pack = subprocess.Popen(self.I.tar_tree(self.root / self.one), stdout=subprocess.PIPE)
            r = subprocess.run(["sh", "-c", self.I.box_script(self.one)], stdin=pack.stdout, env=env,
                               capture_output=True, text=True)
            pack.wait()
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), f"{want} {self.one}")
        broot = box / ".local" / "share" / "lane-control"
        self.assertEqual(os.readlink(broot / "current"), self.one)
        local = {str(p.relative_to(self.root / self.one)) for p in (self.root / self.one).rglob("*")}
        remote = {str(p.relative_to(broot / self.one)) for p in (broot / self.one).rglob("*")}
        self.assertEqual(local, remote)
        self.assertEqual(self.I.tree_sha(broot / self.one), self.one)


class DoctorPruneTests(Scratch):
    def store(self, name, **st):
        d = self.home / ".config" / "lane-control" / "projects" / name
        d.mkdir(parents=True)
        import json
        (d / "settings.json").write_text(json.dumps(st))

    def test_doctor_names_a_project_set_up_with_another_sha(self):
        self.cli("install", "--from", str(self.src))
        self.store("a-1", root="/p/a", lane_control=self.one)
        self.store("b-2", root="/p/b")
        r = self.cli("doctor", "--no-ssh")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("todo: /p/b: lane-control", r.stdout)
        self.assertIn("run project-setup /p/b --refresh", r.stdout)
        self.assertNotIn("todo: /p/a", r.stdout)
        self.assertIn("lane-heavy link", r.stdout)
        import json
        d = json.loads(self.cli("doctor", "--json", "--no-ssh").stdout)
        self.assertEqual(d["current"], self.one)
        self.assertEqual(d["links"]["lane-heavy"], "link")

    def test_doctor_names_a_box_behind(self):
        self.cli("install", "--from", str(self.src))
        b = self.home / ".config" / "lane-control" / "boxes"
        b.mkdir(parents=True)
        (b / "box.json").write_text('{"host": "user@box", "lane_control": "' + "0" * 40 + '"}')
        r = self.cli("doctor", "--no-ssh")
        self.assertIn("todo: box user@box: has 000000000000", r.stdout)

    def test_prune_is_a_dry_run_and_keeps_current(self):
        self.cli("install", "--from", str(self.src))
        two = self.commit_two()
        self.cli("install", "--from", str(self.src))
        r = self.cli("prune")
        self.assertIn(f"would remove {self.one}", r.stdout)
        self.assertIn(f"keep {two} (current)", r.stdout)
        self.assertTrue((self.root / self.one).is_dir())
        with open(self.root / self.one / "core" / "scripts" / "lane-heavy"):  # a process using the old tree
            held = subprocess.Popen(["sleep", "30"], cwd=self.root / self.one)
            try:
                r = self.cli("prune", "--apply")
                self.assertRegex(r.stdout, rf"keep {self.one} \(in use by pid [0-9, ]*\b{held.pid}\b")
            finally:
                held.kill()
                held.wait()
        r = self.cli("prune", "--apply")
        self.assertIn(f"removed {self.one}", r.stdout)
        self.assertFalse((self.root / self.one).exists())
        self.assertTrue((self.root / two).is_dir())


if __name__ == "__main__":
    unittest.main()
