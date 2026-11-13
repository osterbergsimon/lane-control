"""opencode-sandbox.sh as a Cotal seat's launcher: blocks of it run on their own with stubs; no real sandbox, nix or mesh.

A seat's environment is Cotal's allow-list (PATH, HOME, locale, XDG_*: no NIX_PATH), and its Orca tab closes when the
launcher exits, so the launcher's own error is lost unless it is kept in the lane.
"""
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SANDBOX = ROOT / "scripts" / "opencode-sandbox.sh"
TIMEOUT = 20

# A nix that, like the real one, cannot resolve <nixpkgs> without NIX_PATH or an -I for it.
NIX = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$STUB_DIR/nix.log"
case " $* " in *" -I nixpkgs=flake:nixpkgs "*) ;; *)
  [ -n "${NIX_PATH:-}" ] || { echo "error: file 'nixpkgs' was not found in the Nix search path" >&2; exit 1; } ;;
esac
[ ! -e "$STUB_DIR/nix-fails" ] || { echo "error: shell.nix: something else broke" >&2; exit 1; }
[ ! -e "$STUB_DIR/nix-warns" ] || echo "warning: Git tree is dirty" >&2
echo "export FROM_SHELL_NIX=1"
"""


def block(start, end):
    text = SANDBOX.read_text()
    assert start in text and end in text, (start, end)
    return start + text.split(start, 1)[1].split(end, 1)[0]


def head():
    """The launcher's error helpers, as the script defines them."""
    text = SANDBOX.read_text()
    lines = [l for l in text.splitlines() if re.match(r"^(launch_log=|lognote\(\) |die\(\) )", l)]
    assert any(l.startswith("die() ") for l in lines), lines
    return "\n".join(lines) + "\n"


class SeatLaunch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.stubs = self.dir / "stubs"
        self.stubs.mkdir()
        self.lane = self.dir / "lane"
        self.lane.mkdir()
        self.main = self.dir / "main"
        (self.main / ".git").mkdir(parents=True)
        (self.main / "shell.nix").write_text("{ pkgs ? import <nixpkgs> { } }: pkgs.mkShell { }\n")
        nix = self.stubs / "nix"
        nix.write_text(NIX)
        nix.chmod(nix.stat().st_mode | stat.S_IXUSR)
        # what Cotal's connector forwards to a seat: no NIX_PATH
        self.env = {"PATH": f"{self.stubs}:/usr/bin:/bin:/run/current-system/sw/bin", "HOME": str(self.dir),
                    "STUB_DIR": str(self.stubs)}

    def run_bash(self, script, env=None):
        return subprocess.run(["bash", "-c", "set -euo pipefail\n" + script], env=env or self.env,
                              capture_output=True, text=True, timeout=TIMEOUT)

    def dev_env(self, env=None, log=""):
        script = head() + f"launch_log={log}\nlane={self.lane}\ncommon={self.main}/.git\n"
        script += block("# --- the dev environment of the main checkout's shell.nix", "# --- shims inside")
        script += 'echo "devenv=$devenv"\n'
        return self.run_bash(script, env)

    # --- the shell.nix dev environment ---

    def test_a_seat_without_nix_path_still_gets_the_dev_environment(self):
        r = self.dev_env()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"devenv={self.lane}/dev-env.sh", r.stdout)
        self.assertEqual((self.lane / "dev-env.sh").read_text(), "export FROM_SHELL_NIX=1\n")
        self.assertIn("-I nixpkgs=flake:nixpkgs", (self.stubs / "nix.log").read_text())

    def test_a_lane_with_nix_path_keeps_its_own(self):
        r = self.dev_env(env=dict(self.env, NIX_PATH="nixpkgs=/somewhere"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("-I nixpkgs", (self.stubs / "nix.log").read_text())

    def test_a_broken_shell_nix_stops_the_launch_with_nix_s_words_kept_in_the_lane(self):
        (self.stubs / "nix-fails").touch()
        log = self.lane / "seat-launch.log"
        r = self.dev_env(log=log)
        self.assertEqual(r.returncode, 2)
        self.assertIn("opencode-sandbox: ", r.stderr)
        self.assertIn("shell.nix", r.stderr)
        self.assertIn("something else broke", r.stderr)
        self.assertIn("something else broke", log.read_text())

    def test_nix_s_warnings_still_reach_the_terminal(self):
        (self.stubs / "nix-warns").touch()
        r = self.dev_env()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("warning: Git tree is dirty", r.stderr)
        self.assertEqual((self.lane / "dev-env.sh").read_text(), "export FROM_SHELL_NIX=1\n")

    def test_the_nix_path_choice_is_explained(self):
        self.assertIn("wins over a\n  # nix-path in nix.conf", SANDBOX.read_text())

    def test_no_shell_nix_no_dev_environment(self):
        (self.main / "shell.nix").unlink()
        r = self.dev_env()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("devenv=\n", r.stdout)
        self.assertFalse((self.stubs / "nix.log").exists())

    # --- errors outlive the seat's tab ---

    def test_die_appends_to_the_launch_log_only_when_there_is_one(self):
        log = self.lane / "seat-launch.log"
        r = self.run_bash(head() + f'launch_log={log}\nCOTAL_NAME=s1\ndie "no plugin at /x"\n')
        self.assertEqual(r.returncode, 2)
        self.assertRegex(log.read_text(), r"^\S+ s1 opencode-sandbox: no plugin at /x\n$")
        r = self.run_bash(head() + 'die "plain lane"\n')
        self.assertEqual((r.returncode, r.stderr), (2, "opencode-sandbox: plain lane\n"))

    def test_an_unexpected_failure_is_logged_too(self):
        log = self.lane / "seat-launch.log"
        trap = block("# an unexpected failure", "# the day/night file")
        r = self.run_bash(head() + f"launch_log={log}\n" + trap + "\nfalse\necho not-reached\n")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("not-reached", r.stdout)
        self.assertIn("false", log.read_text())

    def test_a_failing_sandbox_leaves_its_exit_code_in_the_launch_log(self):
        log = self.lane / "seat-launch.log"
        line = [l for l in SANDBOX.read_text().splitlines() if l.startswith('[ "$rc" = 0 ] || lognote')]
        self.assertEqual(len(line), 1)
        r = self.run_bash(head() + f"launch_log={log}\nCOTAL_NAME=s1\nrc=1\n" + line[0] + "\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("exited with 1", log.read_text())
        log.unlink()
        self.run_bash(head() + f"launch_log={log}\nrc=0\n" + line[0] + "\n")
        self.assertFalse(log.exists())

    def test_the_seat_keeps_its_launch_log_in_the_lane_and_bounded(self):
        text = SANDBOX.read_text()
        self.assertIn('launch_log=$lane/seat-launch.log', text)

    # --- secrets stay off every command line ---

    def env_block(self, **extra):
        seatdir = self.lane / "seat" / "s1" / "1"
        seatdir.mkdir(parents=True)
        env = dict(self.env, COTAL_NAME="s1", OPENCODE_SERVER_USERNAME="opencode",
                   OPENCODE_SERVER_PASSWORD="pw 'with' $pecial\nchars", COTAL_ACCEPTED_TOKEN="tok-123", **extra)
        script = head() + "launch_log=\nargs=()\nro() { :; }\nrw() { :; }\n"
        script += f"seat=1 task= name=s1 seatdir={seatdir} lane={self.lane} oc=/x/bin/opencode devenv= work={self.dir} p_http= p_https= p_no=\n"
        script += block("# environment: an allow-list", "# --- the machine-wide heavy-work gate")
        script += 'printf "%s\\n" "${args[@]}"\n'
        return self.run_bash(script, env), seatdir

    def test_the_server_password_and_the_token_never_reach_an_argument(self):
        r, seatdir = self.env_block()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("tok-123", r.stdout)
        self.assertNotIn("with", r.stdout)
        self.assertNotIn("OPENCODE_SERVER_PASSWORD", r.stdout)
        envfile = seatdir / "env"
        self.assertEqual(stat.S_IMODE(envfile.stat().st_mode), 0o600)
        # sourced inside, it gives back the exact values
        out = subprocess.run(["bash", "-c", f'. {envfile}; printf "%s|%s|%s" "$OPENCODE_SERVER_PASSWORD" '
                              '"$COTAL_ACCEPTED_TOKEN" "$COTAL_NAME"'], capture_output=True, text=True, env={"PATH": os.environ["PATH"]})
        self.assertEqual(out.stdout, "pw 'with' $pecial\nchars|tok-123|s1")

    def test_the_seat_command_reads_its_environment_file(self):
        text = SANDBOX.read_text()
        self.assertIn(". /run/sandbox/seat/env", text)


if __name__ == "__main__":
    unittest.main()
