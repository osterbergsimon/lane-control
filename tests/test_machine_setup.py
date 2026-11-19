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


GH_STUB = """
#!/usr/bin/env python3
# gh stand-in: answers `gh api repos/<r>/actions/runners` and `gh pr list --head <b> --state <s>` from $GH_STUB_STATE.
import json, os, sys
a = sys.argv[1:]
with open(os.environ["GH_STUB_LOG"], "a") as f:
    f.write(" ".join(a) + "\\n")
state = json.load(open(os.environ["GH_STUB_STATE"])) if os.path.exists(os.environ["GH_STUB_STATE"]) else {}
def opt(n, d=None):
    return a[a.index(n) + 1] if n in a else d
if a[:1] == ["api"]:
    repo = a[1].split("/actions/")[0].removeprefix("repos/")
    if repo not in state.get("runners", {}):
        print("HTTP 404: Not Found", file=sys.stderr); sys.exit(1)
    print(json.dumps({"total_count": len(state["runners"][repo]), "runners": state["runners"][repo]})); sys.exit(0)
if a[:2] == ["pr", "list"]:
    rows = [p for p in state.get("prs", []) if p["head"] == opt("--head") and p["state"] == opt("--state", "open")]
    print(json.dumps([{"number": p["number"], "headRefOid": p["headRefOid"], "url": "u"} for p in rows][:1])); sys.exit(0)
print("gh stub: not modelled: " + " ".join(a), file=sys.stderr); sys.exit(1)
"""

ORCA_STUB = """
#!/usr/bin/env python3
# orca-ide stand-in: answers from $ORCA_STUB_STATE (JSON) and logs every call, one argv per line as JSON, to
# $ORCA_STUB_LOG. Only the calls the machine scripts make are modelled.
import json, os, sys
a = sys.argv[1:]
with open(os.environ["ORCA_STUB_LOG"], "a") as f:
    f.write(json.dumps(a) + "\\n")
sp = os.environ["ORCA_STUB_STATE"]
state = json.load(open(sp)) if os.path.exists(sp) else {}
for k in ("worktrees", "terminals", "automations", "repos"):
    state.setdefault(k, [])
def opt(n, d=None):
    return a[a.index(n) + 1] if n in a else d
def out(result, code=0):
    json.dump(state, open(sp, "w"))
    print(json.dumps({"ok": code == 0, "result": result})); sys.exit(code)
if state.get("down"):
    print(json.dumps({"ok": False, "error": {"message": "runtime not reachable"}})); sys.exit(1)
cmd = " ".join(a[:2])
if cmd == "worktree list": out({"worktrees": state["worktrees"]})
if cmd == "terminal list": out({"terminals": state["terminals"]})
if cmd == "terminal close": out({"closed": opt("--terminal")})
if cmd == "worktree rm":
    if state.get("rm_fails"): out({}, 1)
    out({"removed": opt("--worktree")})
if cmd == "terminal create":
    t = {"handle": "term_%d" % (len(state["terminals"]) + 1), "title": opt("--title"),
         "worktreePath": opt("--worktree", "").split(":", 1)[-1], "connected": True, "lastOutputAt": 0}
    state["terminals"].append(t); out({"terminal": t})
if cmd == "automations list": out({"automations": state["automations"]})
if cmd == "automations create":
    x = {"id": "auto-%d" % (len(state["automations"]) + 1), "name": opt("--name")}
    state["automations"].append(x); out({"automation": x})
if cmd == "repo list": out({"repos": state["repos"]})
if cmd == "repo add":
    r = {"id": "repo-%d" % (len(state["repos"]) + 1), "path": opt("--path"), "kind": opt("--kind", "git")}
    state["repos"].append(r); out({"repo": r})
print("orca stub: not modelled: " + " ".join(a), file=sys.stderr); sys.exit(1)
"""

TRANSCRIPT_SID = "11111111-2222-3333-4444-555555555555"
OPENCODE_SCHEMA = ("CREATE TABLE session_v2 (id TEXT PRIMARY KEY, parent_id TEXT, directory TEXT NOT NULL,"
                   " time_updated INTEGER NOT NULL);"
                   "CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,"
                   " time_updated INTEGER NOT NULL);")

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

    def gh(self, state: dict):
        write_exec(self.bin / "gh", GH_STUB)
        self.env.update(GH_STUB_STATE=str(self.tmp / "gh.json"), GH_STUB_LOG=str(self.tmp / "gh.log"))
        (self.tmp / "gh.json").write_text(json.dumps(state))

    def record_session(self, checkout: Path, harness: str, sid: str, role: str = "coordinator"):
        """What the persona hook or plugin writes for a session started with the markers."""
        from unittest import mock
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            os.environ.pop("XDG_STATE_HOME", None)
            rec = harnesses.session_record(checkout, role, harness)
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text(sid + "\n")

    def claude_coordinator(self, name: str, idle_min: float, marked: bool = True) -> Path:
        checkout = self.home / "code" / name
        checkout.mkdir(parents=True)
        d = self.home / ".claude" / "projects" / harnesses.encode_path(checkout.resolve())
        d.mkdir(parents=True)
        f = d / f"{TRANSCRIPT_SID}.jsonl"
        f.write_text("{}\n")
        age(f, idle_min)
        if marked:
            self.record_session(checkout, "claude", TRANSCRIPT_SID)
        return checkout

    def opencode_coordinator(self, name: str, idle_min: float) -> Path:
        checkout = self.home / "code" / name
        checkout.mkdir(parents=True)
        db = self.home / ".local" / "share" / "opencode" / "opencode.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(db)
        c.executescript(OPENCODE_SCHEMA)
        ms = int((time.time() - idle_min * 60) * 1000)
        c.execute("INSERT INTO session_v2 VALUES (?, ?, ?, ?)", ("ses_oc1", None, str(checkout), ms))
        c.commit()
        c.close()
        self.record_session(checkout, "opencode", "ses_oc1")
        return checkout


    def orca(self, state: dict = None):
        write_exec(self.bin / "orca-ide", ORCA_STUB)
        self.env.update(ORCA_STUB_STATE=str(self.tmp / "orca.json"), ORCA_STUB_LOG=str(self.tmp / "orca.log"))
        (self.tmp / "orca.json").write_text(json.dumps(state or {}))

    def orca_calls(self) -> list:
        f = self.tmp / "orca.log"
        calls = [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []
        return [[a for a in c if a != "--json"] for c in calls]


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



# --- Task 4.2: night-precheck --------------------------------------------------------------------------------------

class NightPrecheckTests(MachineTest):
    def conf(self, coordinators: str, extra: str = ""):
        self.machine_conf(f'COORDINATORS="\n{coordinators}\n"\nRUNNER_REPOS=""\n{extra}')

    def test_no_machine_conf_exits_nonzero_with_reason(self):
        r = self.run_script("night-precheck", env={"LANE_HOUR": "2"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("machine.conf", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_day_with_owner_present_skips(self):
        co = self.claude_coordinator("proj", 90)
        self.conf(f"proj {co} claude proj coordinator")
        r = self.run_script("night-precheck")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_budget_stopped_does_not_count_as_away(self):
        co = self.claude_coordinator("proj", 90)
        self.conf(f"proj {co} claude proj coordinator")
        (self.conf_dir / "budget-stopped").touch()
        self.assertEqual(self.lane_limits("LANE_PRESENCE")["LANE_PRESENCE"], "away")  # limits see it ...
        self.assertEqual(self.run_script("night-precheck").returncode, 1)  # ... the night manager does not

    def test_due_claude_coordinator_at_night(self):
        co = self.claude_coordinator("proj", 60)
        self.conf(f"proj {co} claude proj coordinator")
        r = self.run_script("night-precheck", env={"LANE_HOUR": "2"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "DUE proj idle=60min\n")

    def test_due_opencode_coordinator(self):
        co = self.opencode_coordinator("oc", 50)
        self.conf(f"oc {co} opencode oc coordinator")
        r = self.run_script("night-precheck", env={"LANE_HOUR": "2"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "DUE oc idle=50min\n")

    def test_away_file_by_day_counts(self):
        co = self.claude_coordinator("proj", 60)
        self.conf(f"proj {co} claude proj coordinator")
        (self.conf_dir / "away").touch()
        self.assertEqual(self.run_script("night-precheck").stdout, "DUE proj idle=60min\n")

    def test_recently_idle_not_due(self):
        co = self.claude_coordinator("proj", 10)
        self.conf(f"proj {co} claude proj coordinator")
        self.assertEqual(self.run_script("night-precheck", env={"LANE_HOUR": "2"}).returncode, 1)
        self.conf(f"proj {co} claude proj coordinator", "IDLE_MIN=5\n")
        self.assertEqual(self.run_script("night-precheck", env={"LANE_HOUR": "2"}).stdout, "DUE proj idle=10min\n")

    def test_checked_in_stamp(self):
        co = self.claude_coordinator("proj", 60)
        self.conf(f"proj {co} claude proj coordinator")
        r = self.run_script("night-precheck", "--checked", "proj")
        self.assertEqual(r.returncode, 0, r.stderr)
        stamp = self.home / ".cache" / "lane-control" / "night" / "proj"
        self.assertTrue(stamp.is_file())
        self.assertEqual(self.run_script("night-precheck", env={"LANE_HOUR": "2"}).returncode, 1)
        age(stamp, 121)
        self.assertEqual(self.run_script("night-precheck", env={"LANE_HOUR": "2"}).returncode, 0)
        self.assertEqual(self.run_script("night-precheck", "--checked", "../x").returncode, 2)

    def test_unmarked_coordinator_named_on_stderr(self):
        co = self.claude_coordinator("proj", 90, marked=False)
        self.conf(f"proj {co} claude proj coordinator")
        r = self.run_script("night-precheck", env={"LANE_HOUR": "2"})
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("proj", r.stderr)
        self.assertIn("LANE_ROLE", r.stderr)

    def test_unreadable_session_folder_exits_nonzero(self):
        if os.geteuid() == 0:
            self.skipTest("root reads any folder")
        co = self.claude_coordinator("proj", 90)
        oc = self.opencode_coordinator("oc", 90)
        self.conf(f"proj {co} claude proj coordinator\noc {oc} opencode oc coordinator")
        d = self.home / ".claude" / "projects" / harnesses.encode_path(co.resolve())
        (d / TRANSCRIPT_SID).mkdir()  # a sub-agent folder it must walk
        (d / TRANSCRIPT_SID).chmod(0)
        r = self.run_script("night-precheck", env={"LANE_HOUR": "2"})
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("proj", r.stderr)
        self.assertIn("ermission", r.stderr)

    def test_offline_runner_due(self):
        self.gh({"runners": {"o/r": [{"name": "r1", "status": "offline"}, {"name": "r2", "status": "online"}]}})
        self.machine_conf('COORDINATORS=""\nRUNNER_REPOS="o/r o/gone"\n')
        r = self.run_script("night-precheck", env={"LANE_HOUR": "2"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "OFFLINE r1 repo=o/r\n")
        self.assertIn("o/gone", r.stderr)
        self.gh({"runners": {"o/r": [{"name": "r2", "status": "online"}]}})
        self.assertEqual(self.run_script("night-precheck", env={"LANE_HOUR": "2"}).returncode, 1)



if __name__ == "__main__":
    unittest.main()
