"""Machine setup for native Orca (plan phase 4; spec section 8): lane-away, night-precheck, lane-sweep, ci-presence,
budget-state, machine-check and machine-setup. Every outside tool is a stub on a temp PATH and HOME is a temp dir: no
test reaches the live machine, Orca's automations or a real systemd."""
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "core" / "scripts"
sys.path.insert(0, str(ROOT / "core"))
import harnesses  # noqa: E402

MEMINFO_46G = "MemTotal:       48234496 kB\nMemAvailable:   30000000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"


def write_exec(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip())
    path.chmod(0o755)


def age(path: Path, minutes: float):
    t = time.time() - minutes * 60
    os.utime(path, (t, t))


class MachineTest(unittest.TestCase):
    """A temp HOME, a stub bin first on PATH, a fake /proc and a fixed hour (LANE_HOUR)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="machine-setup-"))
        self.addCleanup(subprocess.run, f'chmod -R u+rwx "{self.tmp}"; rm -rf "{self.tmp}"', shell=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.proc = self.tmp / "proc"
        self.proc.mkdir()
        (self.proc / "meminfo").write_text(MEMINFO_46G)
        self.conf_dir = self.home / ".config" / "lane-control"
        self.conf_dir.mkdir(parents=True)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("LANE_", "XDG_", "CLAUDE_CONFIG", "GITHUB_ACTIONS", "PLAYBOOK"))}
        self.env.update(HOME=str(self.home), PATH=f"{self.bin}:{os.environ['PATH']}", LANE_PROC_DIR=str(self.proc),
                        LANE_STATE_DIR=str(self.tmp / "slots"), LANE_HOUR="12", PYTHONDONTWRITEBYTECODE="1")

    def run_script(self, name, *args, env=None, input=None):
        e = dict(self.env, **(env or {}))
        return subprocess.run([str(SCRIPTS / name), *map(str, args)], env=e, capture_output=True, text=True,
                              input=input, timeout=60)

    def machine_conf(self, text: str) -> Path:
        p = self.conf_dir / "machine.conf"
        p.write_text(textwrap.dedent(text).lstrip())
        return p

    def lane_limits(self, *names, env=None):
        """The values lane-limits.sh computes in this test's environment."""
        e = dict(self.env, **(env or {}))
        echo = "; ".join(f'printf "%s\\n" "${{{n}-}}"' for n in names)
        out = subprocess.run(["bash", "-c", f'. "{SCRIPTS}/lane-limits.sh"; {echo}'], env=e, capture_output=True,
                             text=True, check=True).stdout.splitlines()
        return dict(zip(names, out))


# --- Task 4.1: lane-away and the night hours -----------------------------------------------------------------------

class LaneAwayTests(MachineTest):
    def away(self) -> Path:
        return self.conf_dir / "away"

    def test_on_writes_away_file(self):
        r = self.run_script("lane-away", "on")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.away().is_file())
        s = self.run_script("lane-away", "status")
        self.assertEqual(s.returncode, 0, s.stderr)
        self.assertIn("owner away: yes", s.stdout)
        self.assertIn("away file", s.stdout)

    def test_off_removes_and_is_idempotent(self):
        self.run_script("lane-away", "on")
        r = self.run_script("lane-away", "off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.away().exists())
        self.assertEqual(self.run_script("lane-away", "off").returncode, 0)
        s = self.run_script("lane-away", "status")
        self.assertIn("owner away: no", s.stdout)
        self.assertIn("presence: day", s.stdout)

    def test_expires_after_away_hours(self):
        self.away().touch()
        age(self.away(), 15 * 60)
        s = self.run_script("lane-away", "status")
        self.assertIn("owner away: no", s.stdout)
        self.assertIn("expired", s.stdout)
        self.machine_conf("AWAY_HOURS=2\n")
        age(self.away(), 3 * 60)
        self.assertIn("owner away: no", self.run_script("lane-away", "status").stdout)
        age(self.away(), 60)
        self.assertIn("owner away: yes", self.run_script("lane-away", "status").stdout)

    def test_status_at_night(self):
        s = self.run_script("lane-away", "status", env={"LANE_HOUR": "23"})
        self.assertIn("owner away: yes", s.stdout)
        self.assertIn("night", s.stdout)

    def test_budget_stopped_is_not_owner_away(self):
        (self.conf_dir / "budget-stopped").touch()
        s = self.run_script("lane-away", "status")
        self.assertIn("presence: away (budget stopped)", s.stdout)
        self.assertIn("owner away: no", s.stdout)

    def test_lane_limits_night_hours_from_machine_conf(self):
        self.machine_conf("NIGHT_START=22\nNIGHT_END=6\n")
        self.assertEqual(self.lane_limits("LANE_PRESENCE_WHY", env={"LANE_HOUR": "22"})["LANE_PRESENCE_WHY"], "night")
        self.assertEqual(self.lane_limits("LANE_OWNER_AWAY", env={"LANE_HOUR": "6"})["LANE_OWNER_AWAY"], "")
        self.assertEqual(self.lane_limits("LANE_OWNER_AWAY", env={"LANE_HOUR": "5"})["LANE_OWNER_AWAY"], "1")
        self.machine_conf("NIGHT_START=junk\nNIGHT_END=99\n")
        self.assertEqual(self.lane_limits("LANE_OWNER_AWAY", env={"LANE_HOUR": "22"})["LANE_OWNER_AWAY"], "")
        self.assertEqual(self.lane_limits("LANE_OWNER_AWAY", env={"LANE_HOUR": "23"})["LANE_OWNER_AWAY"], "1")
        self.machine_conf("AWAY_HOURS=2\n")
        (self.conf_dir / "away").touch()
        age(self.conf_dir / "away", 3 * 60)
        self.assertEqual(self.lane_limits("LANE_OWNER_AWAY")["LANE_OWNER_AWAY"], "")
        age(self.conf_dir / "away", 60)
        self.assertEqual(self.lane_limits("LANE_OWNER_AWAY")["LANE_OWNER_AWAY"], "1")

    def test_usage(self):
        self.assertEqual(self.run_script("lane-away").returncode, 2)
        self.assertEqual(self.run_script("lane-away", "maybe").returncode, 2)
        self.assertEqual(subprocess.run(["bash", "-n", str(SCRIPTS / "lane-away")]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
