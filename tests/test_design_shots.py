"""core/scripts/design-shots: headless screenshots of a project's pages at phone and desktop width in both themes, and
a before/after comparison (docs/superpowers/specs/2026-11-21-design-kit-design.md, section 7). Playwright is a stub
that records its argv and writes a file, so no browser runs; one real-browser test runs when Playwright is found."""
import json
import os
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "core" / "scripts" / "design-shots"

STUB = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as f:
    f.write(json.dumps(args) + "\\n")
mode = os.environ.get("STUB_MODE", "")
if mode == "nobrowser":
    sys.stderr.write("browserType.launch: Executable doesn't exist at /x/chrome\\n")
    sys.exit(1)
if mode == "sleep":
    import time
    time.sleep(60)
if mode == "fail" and "--color-scheme" in args and args[args.index("--color-scheme") + 1] == "dark":
    sys.exit(1)
out = args[-1]
with open(out, "w") as f:
    f.write(os.environ.get("STUB_CONTENT", "png") + " " + " ".join(args[:-1]))
"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class DesignShotsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.log = self.t / "pw.log"
        stub = self.t / "bin" / "playwright"
        stub.parent.mkdir()
        stub.write_text(STUB)
        stub.chmod(0o755)
        self.stub = stub
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("DESIGN_SHOTS", "STUB_"))}
        self.env.update(DESIGN_SHOTS_PLAYWRIGHT=str(stub), STUB_LOG=str(self.log))

    def run_shots(self, *args, env=None, cwd=None, timeout=60):
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                              env={**self.env, **(env or {})}, cwd=cwd or self.t, timeout=timeout)

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_every_page_width_and_theme_is_shot_in_its_own_browser(self):
        out = self.t / "shots"
        r = self.run_shots("shoot", "http://127.0.0.1:5173", "--out", str(out), "--page", "/gallery",
                           "--page", "/settings?tab=a")
        self.assertEqual(r.returncode, 0, r.stderr)
        names = sorted(p.name for p in out.glob("*.png"))
        self.assertEqual(names, ["gallery--1280--dark.png", "gallery--1280--light.png", "gallery--390--dark.png",
                                 "gallery--390--light.png", "settings-tab-a--1280--dark.png",
                                 "settings-tab-a--1280--light.png", "settings-tab-a--390--dark.png",
                                 "settings-tab-a--390--light.png"])
        calls = self.calls()
        self.assertEqual(len(calls), 8)  # one Playwright run per shot: a fresh browser and context each time
        for c in calls:
            self.assertEqual(c[0], "screenshot")
            self.assertIn("--full-page", c)
            self.assertIn(c[c.index("--color-scheme") + 1], ("light", "dark"))
            self.assertIn(c[c.index("--viewport-size") + 1], ("390,844", "1280,800"))
        self.assertIn("http://127.0.0.1:5173/settings?tab=a", [c[-2] for c in calls])

    def test_shots_tsv_lists_each_shot_with_its_hash(self):
        out = self.t / "shots"
        r = self.run_shots("shoot", "http://localhost:3000/", "--out", str(out), "--widths", "390",
                           "--themes", "dark")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [l.split("\t") for l in (out / "shots.tsv").read_text().splitlines()]
        self.assertEqual(rows[0], ["name", "url", "width", "theme", "file", "sha256"])
        self.assertEqual(rows[1][:5], ["index", "http://localhost:3000/", "390", "dark", "index--390--dark.png"])
        self.assertEqual(len(rows[1][5]), 64)

    def test_pages_file_names_and_paths(self):
        pages = self.t / "gallery.pages"
        pages.write_text("# what design-shots shoots\nbuttons /gallery#buttons\n/gallery/forms\n\n")
        out = self.t / "shots"
        r = self.run_shots("shoot", "http://127.0.0.1:8000", "--out", str(out), "--pages", str(pages),
                           "--widths", "1280", "--themes", "light", "--wait-for", "[data-gallery-ready]")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(p.name for p in out.glob("*.png")),
                         ["buttons--1280--light.png", "gallery-forms--1280--light.png"])
        for c in self.calls():
            self.assertEqual(c[c.index("--wait-for-selector") + 1], "[data-gallery-ready]")

    def test_only_loopback_urls(self):
        for url in ("http://example.com", "https://box.example:3000", "file:///etc/passwd", "localhost:3000"):
            r = self.run_shots("shoot", url, "--out", str(self.t / "o"))
            self.assertEqual(r.returncode, 2, url)
            self.assertIn("127.0.0.1", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_no_playwright_exits_3_and_downloads_nothing(self):
        env = {k: v for k, v in self.env.items() if k != "DESIGN_SHOTS_PLAYWRIGHT"}
        env["PATH"] = str(self.t / "empty")
        r = subprocess.run([sys.executable, str(SCRIPT), "shoot", "http://127.0.0.1:1", "--out", str(self.t / "o")],
                           capture_output=True, text=True, env=env, cwd=self.t, timeout=30)
        self.assertEqual(r.returncode, 3)
        self.assertIn("Playwright", r.stderr)
        self.assertIn("npx playwright install chromium", r.stderr)
        # review m7: a suggested DESIGN_SHOTS_PLAYWRIGHT must not fetch from npm when Playwright is absent
        self.assertNotIn("`npx playwright`", r.stderr)
        self.assertIn("`npx --no-install playwright`", r.stderr)

    def test_project_playwright_is_found_in_node_modules(self):
        env = {k: v for k, v in self.env.items() if k != "DESIGN_SHOTS_PLAYWRIGHT"}
        env["PATH"] = str(Path(sys.executable).resolve().parent)  # python for the stub, no playwright
        proj = self.t / "proj"
        (proj / "node_modules" / ".bin").mkdir(parents=True)
        shutil.copy(self.stub, proj / "node_modules" / ".bin" / "playwright")
        r = subprocess.run([sys.executable, str(SCRIPT), "shoot", "http://127.0.0.1:1", "--out", str(self.t / "o"),
                            "--widths", "390", "--themes", "light"], capture_output=True, text=True, env=env,
                           cwd=proj, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.calls()), 1)

    def test_missing_browser_exits_3(self):
        r = self.run_shots("shoot", "http://127.0.0.1:1", "--out", str(self.t / "o"), env={"STUB_MODE": "nobrowser"})
        self.assertEqual(r.returncode, 3)
        self.assertIn("npx playwright install chromium", r.stderr)

    def test_a_failed_shot_is_named_and_the_rest_still_taken(self):
        out = self.t / "shots"
        r = self.run_shots("shoot", "http://127.0.0.1:1", "--out", str(out), "--widths", "390",
                           env={"STUB_MODE": "fail"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("index--390--dark", r.stderr)
        self.assertTrue((out / "index--390--light.png").exists())

    def test_serve_starts_waits_and_stops_the_server(self):
        port = free_port()
        site = self.t / "site"
        site.mkdir()
        (site / "index.html").write_text("<p>hi</p>")
        pidfile = self.t / "server.pid"
        serve = f"echo $$ > {pidfile}; exec {sys.executable} -m http.server {port} --bind 127.0.0.1 -d {site}"
        r = self.run_shots("shoot", f"http://127.0.0.1:{port}/", "--out", str(self.t / "o"), "--widths", "390",
                           "--themes", "light", "--serve", serve, "--timeout", "20")
        self.assertEqual(r.returncode, 0, r.stderr)
        pid = int(pidfile.read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, 9)
            self.fail("the server was left running")
        with socket.socket() as s:
            self.assertNotEqual(s.connect_ex(("127.0.0.1", port)), 0)

    def test_serve_that_never_answers_exits_4_and_is_stopped(self):
        port = free_port()
        pidfile = self.t / "server.pid"
        r = self.run_shots("shoot", f"http://127.0.0.1:{port}/", "--out", str(self.t / "o"),
                           "--serve", f"echo $$ > {pidfile}; exec sleep 60", "--timeout", "2")
        self.assertEqual(r.returncode, 4)
        self.assertEqual(self.calls(), [])
        pid = int(pidfile.read_text())
        time.sleep(0.5)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def wait_gone(self, pid, what):
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        os.kill(pid, signal.SIGKILL)
        self.fail(f"{what} was left running")

    def kill_later(self, pidfile):
        def kill():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except (OSError, ValueError):
                pass
        self.addCleanup(kill)

    def serve_site(self):
        port = free_port()
        site = self.t / "site"
        site.mkdir(exist_ok=True)
        (site / "index.html").write_text("<p>hi</p>")
        return port, f"{sys.executable} -m http.server {port} --bind 127.0.0.1 -d {site}"

    def test_a_term_or_hup_mid_shoot_stops_the_server(self):
        # review M1: a lane's terminal closed mid-shoot sends HUP; a TERM from the coordinator; neither may leak it
        for sig in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(sig=sig.name):
                if self.log.exists():
                    self.log.unlink()
                port, server = self.serve_site()
                pidfile = self.t / f"server-{sig.name}.pid"
                self.kill_later(pidfile)
                p = subprocess.Popen([sys.executable, str(SCRIPT), "shoot", f"http://127.0.0.1:{port}/", "--out",
                                      str(self.t / "o"), "--serve", f"echo $$ > {pidfile}; exec {server}",
                                      "--timeout", "20"], env={**self.env, "STUB_MODE": "sleep"},
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.t)
                try:
                    for _ in range(200):  # the first shot has started, so the server is up
                        if self.log.exists() and self.log.read_text():
                            break
                        time.sleep(0.1)
                    else:
                        self.fail("no shot started")
                    p.send_signal(sig)
                    p.wait(timeout=30)
                finally:
                    if p.poll() is None:
                        p.kill()
                    p.communicate()
                self.assertNotEqual(p.returncode, 0)
                self.wait_gone(int(pidfile.read_text()), "the server")
                with socket.socket() as s:
                    self.assertNotEqual(s.connect_ex(("127.0.0.1", port)), 0)

    def test_a_serve_command_that_backgrounds_its_server_is_still_stopped(self):
        # review M1: `--serve "cmd &"`: sh exits at once, the server stays in its process group
        port, server = self.serve_site()
        pidfile = self.t / "server.pid"
        self.kill_later(pidfile)
        r = self.run_shots("shoot", f"http://127.0.0.1:{port}/", "--out", str(self.t / "o"), "--widths", "390",
                           "--themes", "light", "--serve", f"{server} & echo $! > {pidfile}", "--timeout", "20")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.wait_gone(int(pidfile.read_text()), "the backgrounded server")

    def test_compare_pairs_by_name(self):
        before, after, out = self.t / "before", self.t / "after", self.t / "cmp"
        before.mkdir()
        after.mkdir()
        (before / "a--390--light.png").write_bytes(b"same")
        (after / "a--390--light.png").write_bytes(b"same")
        (before / "b--390--light.png").write_bytes(b"old")
        (after / "b--390--light.png").write_bytes(b"new")
        (before / "gone--390--light.png").write_bytes(b"x")
        (after / "new--390--light.png").write_bytes(b"y")
        r = self.run_shots("compare", str(before), str(after), "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["same a--390--light", "changed b--390--light",
                                                 "removed gone--390--light", "added new--390--light"])
        html = (out / "index.html").read_text()
        self.assertIn(os.path.relpath(after / "b--390--light.png", out), html)
        self.assertIn(os.path.relpath(before / "b--390--light.png", out), html)
        self.assertIn("changed", html)

    def test_compare_needs_both_dirs(self):
        r = self.run_shots("compare", str(self.t / "nope"), str(self.t), "--out", str(self.t / "cmp"))
        self.assertEqual(r.returncode, 2)

    def test_help_and_usage(self):
        r = self.run_shots("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("design-shots shoot", r.stdout)
        self.assertEqual(self.run_shots().returncode, 2)


def real_playwright():
    cmd = os.environ.get("DESIGN_SHOTS_PLAYWRIGHT") or shutil.which("playwright")
    return cmd


@unittest.skipUnless(os.environ.get("DESIGN_SHOTS_REAL") and real_playwright(),
                     "real browser: set DESIGN_SHOTS_REAL=1 and have Playwright (DESIGN_SHOTS_PLAYWRIGHT or PATH)")
class RealBrowserTest(unittest.TestCase):
    def test_light_and_dark_differ_on_a_themed_page(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            site = t / "site"
            site.mkdir()
            (site / "index.html").write_text(
                "<!doctype html><meta name=color-scheme content='light dark'><style>body{background:#fff}"
                "@media (prefers-color-scheme: dark){body{background:#000}}</style><p>gallery</p>")
            port = free_port()
            out = t / "shots"
            r = subprocess.run([sys.executable, str(SCRIPT), "shoot", f"http://127.0.0.1:{port}/", "--out", str(out),
                                "--widths", "390", "--serve",
                                f"exec {sys.executable} -m http.server {port} --bind 127.0.0.1 -d {site}"],
                               capture_output=True, text=True, timeout=300, cwd=t)
            self.assertEqual(r.returncode, 0, r.stderr)
            light, dark = (out / "index--390--light.png").read_bytes(), (out / "index--390--dark.png").read_bytes()
            self.assertTrue(light.startswith(b"\x89PNG") and dark.startswith(b"\x89PNG"))
            self.assertNotEqual(light, dark)


if __name__ == "__main__":
    unittest.main()
