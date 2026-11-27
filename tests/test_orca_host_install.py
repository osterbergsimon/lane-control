"""orca-host-install's source and version choice (--from, --bundle, --version) and its provenance line.

Everything is local: a fake ssh runs the host's commands with sh in a temporary "box" HOME, and the Orca server's slot
and node runtime are small fake files in a temporary artifacts folder. Nothing is paired (no --pair), no real host is
touched, and the real tests/test-orca-host-install.sh (which wipes a host user's Orca) is never run here."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "orchestrators" / "orca-cotal" / "scripts" / "orca-host-install"

FAKE_SSH = """#!/bin/sh
# runs the host's command in the box's HOME, stdin passed through; options and the host name are dropped
while [ $# -gt 0 ]; do
  case $1 in -F|-o) shift 2 ;; -*) shift ;; *) break ;; esac
done
shift
echo "$*" >> "$BOX.ssh-log"
printf '%s\0' "$*" >> "$BOX.ssh-cmds"  # one record per command, a multi-line script whole
# the box's login shell runs the command, as sshd does: sh, or fish with BOX_LOGIN_SHELL (its files in the box's HOME)
HOME=$BOX XDG_CONFIG_HOME=$BOX/.config XDG_DATA_HOME=$BOX/.local/share exec "${BOX_LOGIN_SHELL:-sh}" -c "$*"
"""
FAKE_NODE = "#!/bin/sh\necho v0.0.0\n"
NODE_SHA = hashlib.sha256(FAKE_NODE.encode()).hexdigest()


def target():
    """The slot folder name orca-host-install picks for this machine (the 'host' is this machine)."""
    arch = {"x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}[os.uname().machine]
    ldd = subprocess.run("(ldd --version 2>&1 || true) | head -1", shell=True, capture_output=True, text=True).stdout
    return f"linux-{arch}-" + ("musl" if "musl" in ldd else "glibc")


def arch_of(t):
    return t.split("-")[1]


def make_slot(root: Path, name: str, mtime: float = None, extra: str = "", dotversion: str = None,
              app: str = None, node_sha: str = NODE_SHA) -> Path:
    """dotversion: what the slot's .version says it is; app: the Orca app version in its out/package.json."""
    slot = root / name
    slot.mkdir(parents=True)
    (slot / "orcad.js").write_text(f"// fake orcad {name} {extra}\n")
    (slot / ".runtime-node").write_text(node_sha + "\n")
    if dotversion is not None:
        (slot / ".version").write_text(dotversion + "\n")
    if app is not None:
        (slot / "out").mkdir()
        (slot / "out" / "package.json").write_text('{"version":"%s","commit":"abc"}' % app)
    if mtime is not None:
        os.utime(slot, (mtime, mtime))
    return slot


def make_node_archive(root: Path, a: str, body: str = FAKE_NODE) -> None:
    arch_dir = root / "node" / "archives"
    arch_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(arch_dir / f"node-v0-linux-{a}.tar.gz", "w:gz") as tf:
        info = tarfile.TarInfo("node-v0-linux/bin/node")
        data = body.encode()
        info.size, info.mode = len(data), 0o755
        tf.addfile(info, io.BytesIO(data))


class HostInstallCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ohi-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.box = self.tmp / "box"
        self.bin = self.tmp / "bin"
        self.art = self.tmp / "artifacts"
        for d in (self.home, self.box, self.bin, self.art):
            d.mkdir()
        (self.bin / "ssh").write_text(FAKE_SSH)
        (self.bin / "ssh").chmod(0o755)
        self.target = target()
        make_node_archive(self.art, arch_of(self.target))
        self.env = {**os.environ, **testhome.env_for(self.home), "PATH": f"{self.bin}:{os.environ['PATH']}",
                    "BOX": str(self.box), "ORCA_ARTIFACTS": str(self.art)}
        for k in ("LANE_SSH_CONFIG", "ORCA_CLI"):
            self.env.pop(k, None)

    def slots(self, *names):
        """Slots in the desktop's artifacts folder, oldest first."""
        out = []
        for i, n in enumerate(names):
            out.append(make_slot(self.art / self.target, n, mtime=1_000_000 + i * 100))
        return out

    def install(self, *args, env=None):
        return subprocess.run(["sh", str(TOOL), "box", *args], env={**self.env, **(env or {})},
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def on_box(self, ver):
        return self.box / ".orca-remote" / f"orcad-{ver}"

    def untouched(self):
        self.assertFalse((self.box / ".orca-remote").exists(), "the box was written to")


class DefaultTests(HostInstallCase):
    def test_default_installs_the_newest_slot_and_says_where_it_came_from(self):
        old, new = self.slots("0.1.0+aaaa", "0.2.0+bbbb")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [f"installed orcad-0.2.0+bbbb - from {new}"])
        self.assertTrue((self.on_box("0.2.0+bbbb") / ".install-complete").is_file())
        self.assertFalse(self.on_box("0.1.0+aaaa").exists())

    def test_a_second_run_says_exists_with_the_same_provenance(self):
        (slot,) = self.slots("0.1.0+aaaa")
        self.install()
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [f"exists orcad-0.1.0+aaaa - from {slot}"])

    def test_version_picks_that_slot_of_the_desktops_own(self):
        old, new = self.slots("0.1.0+aaaa", "0.2.0+bbbb")
        r = self.install("--version", "0.1.0+aaaa")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [f"installed orcad-0.1.0+aaaa - from {old}"])
        self.assertTrue(self.on_box("0.1.0+aaaa").is_dir())

    def test_a_version_the_desktop_does_not_have_is_refused_and_nothing_is_copied(self):
        self.slots("0.1.0+aaaa", "0.2.0+bbbb")
        r = self.install("--version", "0.9.9+zzzz")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("0.9.9+zzzz", r.stderr)
        self.assertIn("0.1.0+aaaa", r.stderr)  # what it does have
        self.assertIn("0.2.0+bbbb", r.stderr)
        self.assertEqual(r.stdout, "")
        self.untouched()

    def test_a_version_that_could_inject_into_a_remote_command_is_refused(self):
        self.slots("0.1.0+aaaa")
        for v in ("0.1.0; touch x", "$(id)", "a b", "../x", ""):
            r = self.install("--version", v)
            self.assertEqual(r.returncode, 2, (v, r.stderr))
        self.untouched()


class FromTests(HostInstallCase):
    def setUp(self):
        super().setUp()
        self.pinned = self.tmp / "pinned"
        self.pinned.mkdir()

    def test_from_a_slot_folder_installs_that_version_whatever_the_desktop_has(self):
        self.slots("0.2.0+bbbb")
        slot = make_slot(self.pinned, "0.1.0+aaaa", extra="pinned")
        r = self.install("--from", str(slot))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [f"installed orcad-0.1.0+aaaa - from {slot}"])
        self.assertIn("pinned", (self.on_box("0.1.0+aaaa") / "orcad.js").read_text())

    def test_from_needs_no_desktop_artifacts_at_all(self):
        slot = make_slot(self.pinned, "0.1.0+aaaa")
        shutil.copytree(self.art / "node", self.pinned / "node")  # the node runtime travels with the source
        r = self.install("--from", str(slot), env={"ORCA_ARTIFACTS": str(self.tmp / "nothing-here")})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_folder_named_orcad_dash_version_is_that_version(self):
        slot = make_slot(self.pinned, "orcad-0.1.0+aaaa")
        r = self.install("--from", str(slot))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [f"installed orcad-0.1.0+aaaa - from {slot}"])

    def test_from_a_folder_of_slots_takes_the_version_asked_for(self):
        make_slot(self.pinned / self.target, "0.1.0+aaaa", mtime=1_000_000)
        make_slot(self.pinned / self.target, "0.2.0+bbbb", mtime=1_000_100)
        r = self.install("--from", str(self.pinned), "--version", "0.1.0+aaaa")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [f"installed orcad-0.1.0+aaaa - from {self.pinned / self.target / '0.1.0+aaaa'}"])

    def test_from_a_folder_of_slots_without_a_version_takes_the_newest(self):
        make_slot(self.pinned / self.target, "0.1.0+aaaa", mtime=1_000_000)
        make_slot(self.pinned / self.target, "0.2.0+bbbb", mtime=1_000_100)
        r = self.install("--from", str(self.pinned))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("installed orcad-0.2.0+bbbb - from "), r.stdout)

    def test_a_version_that_differs_from_the_source_is_refused_before_anything_is_copied(self):
        slot = make_slot(self.pinned, "0.2.0+bbbb")
        r = self.install("--from", str(slot), "--version", "0.1.0+aaaa")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("0.1.0+aaaa", r.stderr)
        self.assertIn("0.2.0+bbbb", r.stderr)
        self.assertEqual(r.stdout, "")
        self.untouched()

    def test_the_version_that_matches_is_accepted(self):
        slot = make_slot(self.pinned, "0.2.0+bbbb")
        r = self.install("--from", str(slot), "--version", "0.2.0+bbbb")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_source_with_no_slot_is_refused(self):
        r = self.install("--from", str(self.pinned))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(str(self.pinned), r.stderr)
        self.untouched()
        r = self.install("--from", str(self.tmp / "missing"))
        self.assertNotEqual(r.returncode, 0)
        self.untouched()

    def test_from_and_bundle_together_are_refused(self):
        r = self.install("--from", str(self.pinned), "--bundle", str(self.tmp / "x.tar"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("--from", r.stderr)
        self.assertIn("--bundle", r.stderr)

    def test_a_folder_that_is_not_a_complete_slot_is_refused(self):
        slot = make_slot(self.pinned, "0.1.0+aaaa")
        (slot / ".runtime-node").unlink()
        r = self.install("--from", str(slot))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(".runtime-node", r.stderr)
        self.untouched()

    def test_a_slot_whose_node_is_not_the_one_it_names_is_still_refused(self):
        slot = make_slot(self.pinned, "0.1.0+aaaa")
        (slot / ".runtime-node").write_text("0" * 64 + "\n")
        r = self.install("--from", str(slot))
        self.assertNotEqual(r.returncode, 0)
        self.untouched()


class BundleTests(HostInstallCase):
    def bundle(self, name="bundle.tar.gz", slots=("0.1.0+aaaa",), top="", node=True, members=()):
        """A tar of slots (under <top>/<target>/ when top is a path), the node archive beside them."""
        src = self.tmp / "bundle-src"
        shutil.rmtree(src, True)
        src.mkdir()
        for s in slots:
            make_slot(src / top, s) if top else make_slot(src, s)
        if node:
            make_node_archive(src, arch_of(self.target))
        out = self.tmp / name
        with tarfile.open(out, "w:gz" if name.endswith("gz") else "w") as tf:
            for p in sorted(src.iterdir()):
                tf.add(p, arcname=p.name)
            for info, data in members:
                tf.addfile(info, io.BytesIO(data) if data is not None else None)
        return out

    def test_a_bundle_of_slots_installs_the_one_asked_for_and_names_the_bundle(self):
        tar = self.bundle(slots=("0.1.0+aaaa", "0.2.0+bbbb"))
        r = self.install("--bundle", str(tar), "--version", "0.1.0+aaaa", env={"ORCA_ARTIFACTS": str(self.tmp / "none")})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [f"installed orcad-0.1.0+aaaa - from {tar}"])
        self.assertTrue((self.on_box("0.1.0+aaaa") / ".install-complete").is_file())

    def test_a_bundle_in_a_target_folder_and_a_plain_tar_both_work(self):
        for name in ("b.tar", "b.tar.gz"):
            tar = self.bundle(name=name, slots=("0.1.0+aaaa",), top=self.target)
            shutil.rmtree(self.box / ".orca-remote", True)
            r = self.install("--bundle", str(tar))
            self.assertEqual(r.returncode, 0, (name, r.stderr))
            self.assertEqual(r.stdout.splitlines(), [f"installed orcad-0.1.0+aaaa - from {tar}"], name)

    def test_a_bundle_without_the_version_asked_for_is_refused(self):
        tar = self.bundle(slots=("0.2.0+bbbb",))
        r = self.install("--bundle", str(tar), "--version", "0.1.0+aaaa")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("0.1.0+aaaa", r.stderr)
        self.assertIn("0.2.0+bbbb", r.stderr)
        self.untouched()

    def test_a_bundle_that_is_not_there_or_not_a_tar_is_refused(self):
        self.assertNotEqual(self.install("--bundle", str(self.tmp / "missing.tar")).returncode, 0)
        junk = self.tmp / "junk.tar"
        junk.write_text("not a tar")
        r = self.install("--bundle", str(junk))
        self.assertNotEqual(r.returncode, 0)
        self.untouched()

    def test_a_bundle_cannot_write_outside_its_own_folder(self):
        evil = tarfile.TarInfo("../escaped")
        evil.size = 4
        absolute = tarfile.TarInfo(str(self.tmp / "absolute-target"))
        absolute.size = 4
        link = tarfile.TarInfo("0.1.0+aaaa/link")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc"
        for info in (evil, absolute, link):
            tar = self.bundle(members=[(info, b"evil" if info.size else None)])
            r = self.install("--bundle", str(tar))
            self.assertNotEqual(r.returncode, 0, info.name)
            self.assertFalse((self.tmp / "absolute-target").exists())
            self.untouched()

    def test_a_tar_with_one_slot_at_its_top_needs_the_version_named(self):
        src = self.tmp / "flat"
        make_slot(src.parent, "flat")
        make_node_archive(src, arch_of(self.target))
        tar = self.tmp / "flat.tar"
        with tarfile.open(tar, "w") as tf:
            for p in sorted(src.iterdir()):
                tf.add(p, arcname=p.name)
        r = self.install("--bundle", str(tar))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--version", r.stderr)
        self.untouched()
        r = self.install("--bundle", str(tar), "--version", "0.3.0+cccc")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [f"installed orcad-0.3.0+cccc - from {tar}"])


class ContentTests(HostInstallCase):
    """The version is what the slot says it is (.version, out/package.json), not only what its folder is called."""

    def setUp(self):
        super().setUp()
        self.pinned = self.tmp / "pinned"
        self.pinned.mkdir()

    def test_an_app_version_picks_the_slot_that_records_it_and_installs_it_under_its_own_name(self):
        make_slot(self.art / self.target, "0.1.0+aaaa", mtime=1_000_000, dotversion="0.1.0+aaaa", app="1.4.251")
        make_slot(self.art / self.target, "0.2.0+bbbb", mtime=1_000_100, dotversion="0.2.0+bbbb", app="1.4.255")
        r = self.install("--version", "1.4.251")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [f"installed orcad-0.1.0+aaaa - from {self.art / self.target / '0.1.0+aaaa'}"])
        self.assertTrue(self.on_box("0.1.0+aaaa").is_dir())
        self.assertFalse(self.on_box("1.4.251").exists())

    def test_the_slot_name_still_works_and_a_listing_names_both_kinds_of_version(self):
        make_slot(self.art / self.target, "0.1.0+aaaa", dotversion="0.1.0+aaaa", app="1.4.251")
        self.assertEqual(self.install("--version", "0.1.0+aaaa").returncode, 0)
        r = self.install("--version", "9.9.9")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("0.1.0+aaaa", r.stderr)
        self.assertIn("1.4.251", r.stderr)

    def test_a_folder_named_for_one_version_that_holds_another_is_refused(self):
        slot = make_slot(self.pinned, "orcad-1.4.251", dotversion="1.4.250")
        for args in (["--from", str(slot)], ["--from", str(slot), "--version", "1.4.251"]):
            r = self.install(*args)
            self.assertNotEqual(r.returncode, 0, args)
            self.assertIn("1.4.250", r.stderr)
            self.assertIn("1.4.251", r.stderr)
            self.untouched()

    def test_a_version_the_slots_content_does_not_back_is_refused_with_what_it_says(self):
        slot = make_slot(self.pinned, "0.1.0+aaaa", dotversion="0.1.0+aaaa", app="1.4.250")
        r = self.install("--from", str(slot), "--version", "1.4.251")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("1.4.250", r.stderr)
        self.assertIn("0.1.0+aaaa", r.stderr)
        self.untouched()

    def test_a_folder_named_like_an_app_version_that_its_package_json_contradicts_is_refused(self):
        slot = make_slot(self.pinned, "orcad-1.4.251", app="1.4.250")  # no .version to settle it
        for args in (["--from", str(slot)], ["--from", str(slot), "--version", "1.4.251"],
                     ["--from", str(slot), "--version", "1.4.250"]):
            r = self.install(*args)
            self.assertNotEqual(r.returncode, 0, args)
            self.assertIn("1.4.250", r.stderr)
            self.assertIn("1.4.251", r.stderr)
            self.untouched()

    def test_a_hash_named_folder_with_only_an_app_version_on_record_is_still_taken_by_name(self):
        slot = make_slot(self.pinned, "0.1.0+aaaa", app="1.4.250")
        self.assertEqual(self.install("--from", str(slot), "--version", "0.1.0+aaaa").returncode, 0)

    def test_a_slot_with_no_record_of_its_own_is_taken_by_name(self):
        slot = make_slot(self.pinned, "0.1.0+aaaa")
        self.assertEqual(self.install("--from", str(slot), "--version", "0.1.0+aaaa").returncode, 0)

    def test_the_default_install_does_not_second_guess_the_desktops_own_slots(self):
        make_slot(self.art / self.target, "0.1.0+aaaa", dotversion="something-else-entirely")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_bare_slot_bundle_takes_its_version_from_its_own_record(self):
        src = self.tmp / "bare"
        make_slot(self.tmp, "bare", dotversion="0.3.0+cccc", app="1.4.251")
        make_node_archive(src, arch_of(self.target))
        tar = self.tmp / "bare.tar"
        with tarfile.open(tar, "w") as tf:
            for p in sorted(src.iterdir()):
                tf.add(p, arcname=p.name)
        r = self.install("--bundle", str(tar))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [f"installed orcad-0.3.0+cccc - from {tar}"])
        shutil.rmtree(self.box / ".orca-remote")
        self.assertEqual(self.install("--bundle", str(tar), "--version", "1.4.251").returncode, 0)
        shutil.rmtree(self.box / ".orca-remote")
        r = self.install("--bundle", str(tar), "--version", "0.9.9+zzzz")  # a word that the slot does not back
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("0.3.0+cccc", r.stderr)
        self.untouched()

    def test_a_bad_record_in_a_folder_of_slots_stops_the_run_even_in_a_slot_not_asked_for(self):
        make_slot(self.art / self.target, "0.1.0+aaaa", mtime=1_000_000, dotversion="0.1.0+aaaa")
        make_slot(self.art / self.target, "0.2.0+bbbb", mtime=1_000_100, dotversion="0.2.0; touch x")
        r = self.install("--version", "0.1.0+aaaa")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(".version", r.stderr)
        self.untouched()

    def test_a_record_that_cannot_be_a_version_is_refused(self):
        slot = make_slot(self.pinned, "0.1.0+aaaa", dotversion="0.1.0; touch x")
        r = self.install("--from", str(slot))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(".version", r.stderr)
        self.untouched()


class AppDirTests(HostInstallCase):
    def app_dir(self):
        res = self.tmp / "app" / "resources"
        tpl = make_slot(res, "orcad-template", app="1.4.250")
        (tpl / "orcad-template.json").write_text('{"schemaVersion":4}')
        (tpl / ".runtime-node").unlink()  # a template has the node sha per target, not a slot's own
        return self.tmp / "app", res, tpl

    def test_a_packaged_app_dir_is_recognised_and_refused_clearly(self):
        app, res, tpl = self.app_dir()
        for src in (app, res, tpl):
            for extra in ([], ["--version", "1.4.250"]):
                r = self.install("--from", str(src), *extra)
                self.assertNotEqual(r.returncode, 0, (src, extra))
                self.assertIn("packaged app", r.stderr)
                self.assertIn("1.4.250", r.stderr)
                self.assertIn("ORCA_ARTIFACTS", r.stderr)  # where to look instead
                self.assertNotIn("has: template", r.stderr)
                self.assertNotIn(".runtime-node", r.stderr)  # the old, misleading message
                self.untouched()

    def test_a_bundle_of_an_app_dir_is_refused_the_same_way(self):
        app, res, tpl = self.app_dir()
        tar = self.tmp / "app.tar"
        with tarfile.open(tar, "w") as tf:
            tf.add(res, arcname="resources")
        r = self.install("--bundle", str(tar))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("packaged app", r.stderr)
        self.untouched()


class NodeSearchTests(HostInstallCase):
    def test_a_bare_slot_bundle_does_not_search_outside_its_own_folder_for_node(self):
        scratch = self.tmp / "scratch"
        scratch.mkdir()
        shutil.copytree(self.art / "node", scratch / "node")  # a stray node tarball, above where the tar unpacks
        shutil.rmtree(self.art / "node")
        src = self.tmp / "flat"
        make_slot(self.tmp, "flat")
        tar = self.tmp / "flat.tar"
        with tarfile.open(tar, "w") as tf:
            for p in sorted(src.iterdir()):
                tf.add(p, arcname=p.name)
        r = self.install("--bundle", str(tar), "--version", "0.3.0+cccc", env={"TMPDIR": str(scratch)})
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("node", r.stderr)
        self.untouched()

    def test_a_runtime_node_that_is_not_a_sha_is_refused_up_front(self):
        slot = make_slot(self.tmp / "p", "0.1.0+aaaa")
        (slot / ".runtime-node").write_text("not-a-sha; touch x\n")
        r = self.install("--from", str(slot))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sha256", r.stderr)
        self.untouched()


# --pair (lc-5zn): orcad's pairing code goes to this desktop's Orca CLI on stdin, through the preload, never in argv.
ORCAD_NODE = """#!/bin/sh
case "$1" in --version) echo v0.0.0; exit 0 ;; esac
# orcad: its first stdout line is the readiness record with the pairing code (made from a seed, not kept in the env)
printf '{"type":"orca_server_ready","pairing":{"url":"orca://pair?code=%s"}}\\n' \\
  "$(printf '%s' "$STUB_ORCAD_SEED" | sha256sum | cut -c1-48)"
# then it serves: stays up as node with orcad's argv, owning 127.0.0.1:<port> (BOX-1: one runtime is proven by that)
exec python3 -c 'import os, sys
hold = "import os, socket, sys, time\\nport = int(sys.argv[sys.argv.index(\\"--port\\") + 1])\\ns = socket.socket()\\ns.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\\ns.bind((\\"127.0.0.1\\", port))\\ns.listen()\\nopen(os.environ[\\"BOX\\"] + \\".orcad-pid\\", \\"w\\").write(str(os.getpid()))\\ntime.sleep(120)\\n"
os.execv(sys.executable, [sys.argv[1], "-c", hold] + sys.argv[2:])' "$0" "$@"
"""
ORCAD_NODE_SHA = hashlib.sha256(ORCAD_NODE.encode()).hexdigest()
DESK_NODE = r"""#!/usr/bin/env python3
import hashlib, json, os, sys
args = sys.argv[1:]
rec = {"argv": args}
preload = None
if args[:1] == ["--require"]:
    preload, args = args[1], args[2:]
args = args[1:]
p = os.environ["DESK_STATE"]
st = json.load(open(p)) if os.path.exists(p) else {"environments": []}
def out(o, code=0):
    json.dump(st, open(p, "w"))
    with open(os.environ["DESK_STATE"] + ".argv", "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(o))
    sys.exit(code)
def opt(n):
    return args[args.index(n) + 1]
if args[:2] == ["environment", "list"]:
    out({"ok": True, "result": {"environments": [{"name": n} for n in st["environments"]]}})
if args[:1] == ["status"]:
    out({"ok": True, "result": {"runtime": {"state": "ready", "reachable": opt("--environment") in st["environments"]}}})
if args[:2] == ["environment", "add"]:
    if opt("--pairing-code") != "-" or not (preload and preload.endswith("pairing-code-stdin.cjs")):
        out({"ok": False, "error": {"message": "the code came in argv"}}, 1)
    rec["stdin_sha256"] = hashlib.sha256(sys.stdin.read().strip().encode()).hexdigest()
    st["environments"].append(opt("--name"))
    out({"ok": True, "result": {"environment": {"name": opt("--name")}}})
if args[:2] == ["environment", "rm"]:
    st["environments"].remove(opt("--environment"))
    out({"ok": True, "result": {}})
out({"ok": False, "error": {"message": "stub: " + " ".join(args)}}, 1)
"""


class PairTests(HostInstallCase):
    def setUp(self):
        super().setUp()
        import socket
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.seed = os.urandom(8).hex()
        self.code = "orca://pair?code=" + hashlib.sha256(self.seed.encode()).hexdigest()[:48]
        make_node_archive(self.art, arch_of(self.target), ORCAD_NODE)
        make_slot(self.art / self.target, "0.1.0+abc", node_sha=ORCAD_NODE_SHA)
        # systemctl: no user manager on the box (orcad runs detached); here a started tunnel unit makes ss list it (here only)
        # show: the tunnel unit runs, and its MainPID is the process that owns the port (the box and this desktop are
        # one machine here, so that is the fake orcad; BOX-1 proves the tunnel by its owner, read by name)
        (self.bin / "systemctl").write_text('#!/bin/sh\necho "$*" >> "$BOX.systemctl-log"\n'
                                            'case "$*" in *show-environment*) exit 1 ;; *enable*|*restart*) '
                                            ': > "$BOX.tunnel-up" ;;\n'
                                            '  *"show orca-tunnel-"*) printf "ActiveState=active\\nSubState=running\\n'
                                            'MainPID=%s\\nNRestarts=0\\n" "$(cat "$BOX.orcad-pid" 2>/dev/null || '
                                            'echo 0)" ;; esac\nexit 0\n')
        (self.bin / "ss").write_text('#!/bin/sh\n[ "$HOME" != "$BOX" ] && [ -f "$BOX.tunnel-up" ] && '
                                     'echo "LISTEN 0 128 127.0.0.1:$STUB_PORT 0.0.0.0:*"\nexit 0\n')
        (self.bin / "desk-node").write_text(DESK_NODE)
        cli = self.tmp / "desk-cli" / "orca"
        cli.parent.mkdir()
        cli.write_text(f"#!/usr/bin/env sh\nexec '{self.bin}/desk-node' '{self.tmp}/desk-cli/cli.js' \"$@\"\n")
        for f in (self.bin / "systemctl", self.bin / "ss", self.bin / "desk-node", cli):
            f.chmod(0o755)
        self.desk = self.tmp / "desk.json"
        self.env.update(ORCA_CLI=str(cli), DESK_STATE=str(self.desk), STUB_PORT=str(self.port),
                        STUB_ORCAD_SEED=self.seed, LANE_CHECK_SETTLE="0", LANE_TUNNEL_WAIT="5")
        self.addCleanup(self.stop_orcad)

    def stop_orcad(self):
        """The fake orcad --pair started detached on the 'box' (this machine)."""
        import signal
        for d in Path("/proc").glob("[0-9]*"):
            try:
                if str(self.box) + "/.orca-remote/" in (d / "cmdline").read_bytes().decode(errors="replace"):
                    os.kill(int(d.name), signal.SIGTERM)
            except (OSError, ValueError):
                continue
        self.env.pop("ORCA_CLI_COMMAND", None)

    def calls(self):
        f = Path(str(self.desk) + ".argv")
        import json
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def test_the_boxs_code_reaches_this_desktops_cli_on_stdin_and_nowhere_else(self):
        r = self.install("--pair", "--port", str(self.port))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("paired box", r.stdout)
        adds = [c for c in self.calls() if "stdin_sha256" in c]
        self.assertEqual(len(adds), 1, self.calls())
        self.assertEqual(adds[0]["stdin_sha256"], hashlib.sha256(self.code.encode()).hexdigest())
        argv = adds[0]["argv"]
        self.assertEqual(argv[0], "--require")
        self.assertTrue(argv[1].endswith("core/scripts/pairing-code-stdin.cjs"), argv)
        self.assertEqual(argv[argv.index("--pairing-code") + 1], "-")
        self.assertNotIn(self.code, r.stdout + r.stderr)
        code_part = self.code.split("code=", 1)[1]
        for f in self.tmp.rglob("*"):  # every argv log (ssh, systemctl, the desktop CLI) and every file, but orcad's own
            if f.is_file() and not f.is_symlink() and not f.name.startswith(".orcad-readiness-"):
                self.assertNotIn(code_part.encode(), f.read_bytes(), f)

    def test_a_second_run_is_already_paired_and_sends_no_code(self):
        self.assertEqual(self.install("--pair", "--port", str(self.port)).returncode, 0)
        r = self.install("--pair", "--port", str(self.port))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("already-paired box", r.stdout)
        self.assertEqual(len([c for c in self.calls() if "stdin_sha256" in c]), 1)

    def test_a_refused_code_is_an_error_that_does_not_repeat_it(self):
        self.env["DESK_STATE"] = str(self.tmp / "no-such-dir" / "desk.json")  # the stub CLI then fails on every call
        r = self.install("--pair", "--port", str(self.port))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertNotIn(self.code.split("code=", 1)[1], r.stdout + r.stderr)


@unittest.skipUnless(shutil.which("fish"), "fish is not installed")
class FishBoxTests(HostInstallCase):
    """lc-tuh (BOX-1 review M5): every command orca-host-install sends is `sh -c '<script>'`, so a box whose login shell
    is fish installs, starts and pairs as one with sh does."""

    def setUp(self):
        super().setUp()
        self.env["BOX_LOGIN_SHELL"] = shutil.which("fish")

    def test_a_fish_box_installs_and_every_command_is_sh_c(self):
        (slot,) = self.slots("0.1.0+aaaa")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [f"installed orcad-0.1.0+aaaa - from {slot}"])
        self.assertTrue((self.on_box("0.1.0+aaaa") / ".install-complete").is_file())
        cmds = [c for c in (self.tmp / "box.ssh-cmds").read_text().split("\0") if c]
        self.assertTrue(cmds)
        for c in cmds:
            self.assertTrue(c.startswith("sh -c '"), c)
        self.assertEqual(self.install().stdout.splitlines(), [f"exists orcad-0.1.0+aaaa - from {slot}"])


@unittest.skipUnless(shutil.which("fish"), "fish is not installed")
class FishPairTests(PairTests):
    """The --pair runs above with fish as the box's login shell (lc-tuh)."""

    def setUp(self):
        super().setUp()
        self.env["BOX_LOGIN_SHELL"] = shutil.which("fish")


class HelpTests(unittest.TestCase):
    def test_the_header_documents_the_options_and_the_provenance_line(self):
        out = subprocess.run(["sh", str(TOOL), "--help"], capture_output=True, text=True).stdout
        for s in ("--from", "--bundle", "--version", "from <source>", "app version", "packaged app"):
            self.assertIn(s, out)

    def test_it_parses(self):
        self.assertEqual(subprocess.run(["sh", "-n", str(TOOL)]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
