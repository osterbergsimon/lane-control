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

SYSTEMCTL_STUB = """
#!/usr/bin/env python3
# systemctl stand-in (--user only): logs argv as JSON lines to $SYSTEMCTL_LOG; `show` answers from $SYSTEMCTL_STATE.
import json, os, sys
a = sys.argv[1:]
with open(os.environ["SYSTEMCTL_LOG"], "a") as f:
    f.write(json.dumps(a) + "\\n")
sp = os.environ["SYSTEMCTL_STATE"]
state = json.load(open(sp)) if os.path.exists(sp) else {}
if state.get("down") or a[:1] != ["--user"]:
    print("Failed to connect to bus: No medium found", file=sys.stderr); sys.exit(1)
if a[1:2] == ["show"]:
    s = state.get("slices", {}).get(a[2], {"CPUQuotaPerSecUSec": "infinity", "MemoryMax": "infinity"})
    props = [a[i + 1] for i, x in enumerate(a) if x == "-p"]
    print("\\n".join(s[k] for k in props)); sys.exit(0)
sys.exit(0)
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
                    if not k.startswith(("LANE_", "XDG_", "CLAUDE_CONFIG", "GITHUB_ACTIONS", "PLAYBOOK", "ORCA_"))}
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


    def systemctl(self, state: dict = None):
        write_exec(self.bin / "systemctl", SYSTEMCTL_STUB)
        self.env.update(SYSTEMCTL_STATE=str(self.tmp / "systemctl.json"), SYSTEMCTL_LOG=str(self.tmp / "systemctl.log"))
        (self.tmp / "systemctl.json").write_text(json.dumps(state or {}))

    def systemctl_calls(self) -> list:
        f = self.tmp / "systemctl.log"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


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


# --- Task 4.3: lane-sweep ------------------------------------------------------------------------------------------

def ms(hours_ago: float) -> int:
    return int((time.time() - hours_ago * 3600) * 1000)


class LaneSweepTests(MachineTest):
    def setUp(self):
        super().setUp()
        self.env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
        self.origin = self.tmp / "origin.git"
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.main = self.tmp / "main"
        self.git(self.tmp, "clone", "-q", str(self.origin), str(self.main))
        self.commit(self.main, "base")
        self.git(self.main, "push", "-q", "origin", "HEAD:main")
        self.state = {"worktrees": [{"id": f"repo1::{self.main}", "path": str(self.main), "branch": "refs/heads/main",
                                     "isMainWorktree": True, "isArchived": False, "lastActivityAt": ms(0)}],
                      "terminals": []}
        self.prs = []
        self.orca(self.state)
        self.gh({"prs": self.prs})

    def git(self, cwd, *args, hours_ago=None) -> str:
        env = dict(self.env)
        if hours_ago is not None:
            d = f"@{int(time.time() - hours_ago * 3600)} +0000"
            env.update(GIT_AUTHOR_DATE=d, GIT_COMMITTER_DATE=d)
        return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(self, cwd, name, hours_ago=0) -> str:
        (Path(cwd) / f"{name}.txt").write_text(name)
        self.git(cwd, "add", f"{name}.txt")
        self.git(cwd, "commit", "-q", "-m", name, hours_ago=hours_ago)
        return self.git(cwd, "rev-parse", "HEAD")

    def lane(self, name, branch=None, push_as=None, hours_ago=0, activity_h=0, track=None):
        """A lane worktree with one commit; pushed (with upstream) unless push_as is False."""
        branch = branch or name
        path = self.tmp / "wt" / name
        if track:
            self.git(self.main, "fetch", "-q", "origin")
            self.git(self.main, "worktree", "add", "-q", "--track", "-b", branch, str(path), f"origin/{track}")
            head = self.git(path, "rev-parse", "HEAD")
        else:
            self.git(self.main, "worktree", "add", "-q", "-b", branch, str(path))
            head = self.commit(path, name, hours_ago)
            if push_as is not False:
                self.git(path, "push", "-q", "-u", "origin", f"{branch}:{push_as or branch}")
        self.state["worktrees"].append({"id": f"repo1::{path}", "path": str(path), "branch": f"refs/heads/{branch}",
                                        "isMainWorktree": False, "isArchived": False,
                                        "lastActivityAt": ms(activity_h)})
        self.orca(self.state)
        return path, head

    def pr(self, head_branch, state, oid, number=7):
        self.prs.append({"head": head_branch, "state": state, "number": number, "headRefOid": oid})
        self.gh({"prs": self.prs})

    def terminal(self, path, out_h=2, connected=True, handle="term_9"):
        self.state["terminals"].append({"handle": handle, "worktreePath": str(path), "lastOutputAt": ms(out_h),
                                        "connected": connected, "title": "lane"})
        self.orca(self.state)

    def agent(self, path, argv=("claude", "--model", "sonnet"), pid=4242):
        d = self.proc / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (d / "cwd").symlink_to(path)

    def sweep(self, *args):
        return self.run_script("lane-sweep", *args)

    def removed(self) -> list:
        return [c for c in self.orca_calls() if c[:2] == ["worktree", "rm"]]

    def test_nothing_found(self):
        self.lane("busy")
        r = self.sweep()
        self.assertEqual((r.returncode, r.stdout), (0, "nothing found\n"), r.stderr)

    def test_merged_clean_lane_reported(self):
        path, head = self.lane("done")
        self.pr("done", "merged", head)
        self.terminal(path)
        r = self.sweep()
        self.assertEqual(r.returncode, 10, r.stderr)
        self.assertRegex(r.stdout, rf"^MERGED {path} \(#7 merged; would close 1 terminal")
        self.assertEqual(self.removed(), [])

    def test_merged_clean_lane_removed_with_apply(self):
        path, head = self.lane("done")
        self.pr("done", "merged", head)
        self.terminal(path)
        r = self.sweep("--apply")
        self.assertEqual(r.returncode, 10, r.stderr)
        self.assertIn(f"REMOVED {path}", r.stdout)
        self.assertIn(["terminal", "close", "--terminal", "term_9"], self.orca_calls())
        self.assertEqual(self.removed(), [["worktree", "rm", "--worktree", f"id:repo1::{path}", "--delete-branch"]])

    def test_merged_kept_untracked(self):
        path, head = self.lane("done")
        self.pr("done", "merged", head)
        (path / "notes.txt").write_text("x")
        r = self.sweep("--apply")
        self.assertIn(f"MERGED-KEEP {path}", r.stdout)
        self.assertIn("uncommitted or untracked", r.stdout)
        self.assertEqual(self.removed(), [])

    def test_merged_kept_commits_after_pr_head(self):
        path, head = self.lane("done")
        self.pr("done", "merged", head)
        self.commit(path, "later")
        r = self.sweep("--apply")
        self.assertIn("commits after the merged PR head", r.stdout)
        self.assertEqual(self.removed(), [])

    def test_merged_behind_pr_head_is_removed(self):
        path, head = self.lane("done")
        self.commit(path, "later")
        self.git(path, "push", "-q")
        self.pr("done", "merged", self.git(path, "rev-parse", "HEAD"))
        self.git(path, "reset", "-q", "--hard", head)
        self.assertIn(f"REMOVED {path}", self.sweep("--apply").stdout)

    def test_merged_kept_recent_terminal_output(self):
        path, head = self.lane("done")
        self.pr("done", "merged", head)
        self.terminal(path, out_h=0.1)
        r = self.sweep("--apply")
        self.assertIn("a terminal printed output in the last 30 min", r.stdout)
        self.assertEqual(self.removed(), [])

    def test_live_agent_keeps_lane(self):
        for i, argv in enumerate((["claude"], ["node", "/x/bin/opencode", "--auto"], ["codex", "exec"])):
            with self.subTest(argv=argv):
                path, head = self.lane(f"done{i}")
                self.pr(f"done{i}", "merged", head, number=10 + i)
                self.agent(path / "sub" if i == 1 else path, argv, pid=100 + i)
                (path / "sub").mkdir(exist_ok=True)
                r = self.sweep("--apply")
                self.assertIn(f"MERGED-KEEP {path}", r.stdout)
                self.assertIn("an agent is still running in it", r.stdout)
                self.assertNotIn(f"id:repo1::{path}", json.dumps(self.removed()))
                (self.proc / str(100 + i) / "cwd").unlink()
                (self.proc / str(100 + i) / "cmdline").write_bytes(b"")
        path, head = self.lane("open")
        self.pr("open", "open", head)
        self.agent(path, ["claude"], pid=200)
        r = self.sweep("--apply")
        self.assertNotIn(f"PR-REMOVED {path}", r.stdout)
        self.assertNotIn(f"id:repo1::{path}", json.dumps(self.removed()))

    def test_open_pr_pushed_clean_is_parked(self):
        path, head = self.lane("open")
        self.pr("open", "open", head)
        self.terminal(path, connected=True)
        r = self.sweep()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"PR-PARKED {path} (#7 open", r.stdout)
        r = self.sweep("--precheck")
        self.assertEqual(r.returncode, 1)
        r = self.sweep("--apply")
        self.assertIn(f"PR-REMOVED {path}", r.stdout)
        self.assertEqual(self.removed(), [["worktree", "rm", "--worktree", f"id:repo1::{path}", "--delete-branch"]])
        self.assertIn("refs/heads/open", self.git(self.tmp, "ls-remote", str(self.origin)))  # remote branch kept
        self.assertNotIn("close", (self.tmp / "gh.log").read_text())

    def test_open_pr_with_local_commit_not_removed(self):
        path, head = self.lane("open")
        self.pr("open", "open", head)
        self.commit(path, "unpushed")
        r = self.sweep("--apply")
        self.assertNotIn("PR-", r.stdout)
        self.assertEqual(self.removed(), [])

    def test_base_branch_upstream_is_not_used(self):
        main_head = self.git(self.main, "rev-parse", "HEAD")
        self.pr("main", "merged", main_head, number=3)  # the base branch's old merged PR
        path, _ = self.lane("feat", track="main")
        r = self.sweep("--apply")
        self.assertNotIn("MERGED", r.stdout)
        self.assertEqual(self.removed(), [])

    def test_same_branch_upstream_is_used(self):
        path, head = self.lane("x", branch="user/x", push_as="x")
        self.pr("x", "merged", head)
        self.assertIn(f"MERGED {path}", self.sweep().stdout)

    def test_stuck_lane(self):
        path, _ = self.lane("old", hours_ago=7, activity_h=7)
        r = self.sweep()
        self.assertEqual(r.returncode, 10, r.stderr)
        self.assertRegex(r.stdout, rf"STUCK +{path} \(branch old, .*7 h")
        self.assertEqual(self.sweep("--stuck-hours", "8").returncode, 0)
        self.assertEqual(self.sweep("--precheck").returncode, 0)

    def test_recent_commit_is_activity(self):
        path, _ = self.lane("old", hours_ago=1, activity_h=30)
        self.assertEqual(self.sweep().returncode, 0)

    def test_recent_terminal_output_is_activity(self):
        path, _ = self.lane("old", hours_ago=9, activity_h=9)
        self.terminal(path, out_h=1)
        self.assertEqual(self.sweep().returncode, 0)

    def test_parked_lane_not_stuck(self):
        path, _ = self.lane("waiting", hours_ago=9, activity_h=9)
        self.pr("waiting", "open", "f" * 40)
        r = self.sweep()
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_hold_file_skips(self):
        path, head = self.lane("done")
        self.pr("done", "merged", head)
        (self.conf_dir / "lane-sweep.hold").write_text(f"# kept for the owner\n{path}   # until Friday\n")
        r = self.sweep("--apply")
        self.assertEqual((r.returncode, r.stdout), (0, "nothing found\n"))
        self.assertEqual(self.removed(), [])

    def test_gone_and_detached(self):
        self.state["worktrees"] += [
            {"id": "repo1::/nope", "path": str(self.tmp / "nope"), "branch": "refs/heads/x", "isMainWorktree": False,
             "isArchived": False, "lastActivityAt": ms(1)},
            {"id": "repo1::det", "path": str(self.main), "branch": None, "isMainWorktree": False,
             "isArchived": False, "lastActivityAt": ms(1)},
            {"id": "repo1::arch", "path": str(self.tmp / "arch"), "branch": "refs/heads/y", "isMainWorktree": False,
             "isArchived": True, "lastActivityAt": ms(1)}]
        self.orca(self.state)
        r = self.sweep()
        self.assertEqual(r.returncode, 10)
        self.assertIn(f"GONE   {self.tmp / 'nope'}", r.stdout)
        self.assertIn(f"DETACH {self.main}", r.stdout)
        self.assertNotIn("arch", r.stdout)

    def test_owner_named_from_machine_conf(self):
        path, head = self.lane("done")
        self.pr("done", "merged", head)
        self.machine_conf(f'COORDINATORS="\nproj {self.main} claude proj coordinator\n"\n')
        self.assertRegex(self.sweep().stdout, rf"MERGED {path} .* owner=proj\n")

    def test_orca_down_exits_2(self):
        self.state["down"] = True
        self.orca(self.state)
        self.assertEqual(self.sweep().returncode, 2)
        self.assertEqual(self.sweep("--precheck").returncode, 2)


# --- Task 4.4: ci-presence -----------------------------------------------------------------------------------------

class CiPresenceTests(MachineTest):
    def setUp(self):
        super().setUp()
        self.systemctl()

    def expected(self, env=None):
        v = self.lane_limits("LANE_CI_CPU_PCT", "LANE_CI_MEM_GB", "LANE_CI_SLICE", env=env)
        return v["LANE_CI_SLICE"], int(v["LANE_CI_CPU_PCT"]), int(v["LANE_CI_MEM_GB"])

    def sets(self):
        return [c for c in self.systemctl_calls() if c[1:2] == ["set-property"]]

    def test_day_limits_from_lane_limits(self):
        r = self.run_script("ci-presence")
        self.assertEqual(r.returncode, 0, r.stderr)
        slice_, cpu, mem = self.expected()
        self.assertEqual(slice_, "ci.slice")
        self.assertEqual(mem, 10)  # 46G, day: the owner's numbers (HEAVY-1)
        self.assertEqual(self.sets(), [["--user", "set-property", "--runtime", "ci.slice", f"CPUQuota={cpu}%",
                                        f"MemoryMax={mem}G"]])
        self.assertIn("day", r.stdout)

    def test_night_and_away_limits(self):
        night = {"LANE_HOUR": "2"}
        self.run_script("ci-presence", env=night)
        _, cpu, mem = self.expected(night)
        self.assertEqual(mem, 14)
        self.assertEqual(self.sets()[-1][-2:], [f"CPUQuota={cpu}%", "MemoryMax=14G"])
        self.assertGreater(cpu, self.expected()[1])
        (self.conf_dir / "away").touch()
        self.run_script("ci-presence")
        self.assertEqual(self.sets()[-1][-1], "MemoryMax=14G")

    def test_budget_stopped_gives_away_limits(self):
        (self.conf_dir / "budget-stopped").touch()
        r = self.run_script("ci-presence")
        self.assertEqual(self.sets()[-1][-1], "MemoryMax=14G")
        self.assertIn("budget stopped", r.stdout)

    def test_limits_file_override(self):
        (self.conf_dir / "limits.sh").write_text("LANE_CI_CPU_PCT=300\nLANE_CI_SLICE=ci-other.slice\n")
        self.run_script("ci-presence")
        self.assertEqual(self.sets()[-1][3:5], ["ci-other.slice", "CPUQuota=300%"])

    def test_no_change_no_call(self):
        slice_, cpu, mem = self.expected()
        whole, rest = divmod(cpu, 100)
        shown = f"{whole}s" + (f" {rest * 10}ms" if rest else "")
        self.systemctl({"slices": {slice_: {"CPUQuotaPerSecUSec": shown, "MemoryMax": str(mem << 30)}}})
        r = self.run_script("ci-presence")
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual(self.sets(), [])

    def test_zero_memory_is_infinity(self):
        (self.conf_dir / "limits.sh").write_text("LANE_CI_MEM_GB=0\n")
        self.run_script("ci-presence")
        self.assertEqual(self.sets()[-1][-1], "MemoryMax=infinity")

    def test_no_systemd_says_so(self):
        self.systemctl({"down": True})
        r = self.run_script("ci-presence")
        self.assertEqual(r.returncode, 1)
        self.assertIn("systemd user manager", r.stderr)

    def test_dry_run_and_usage(self):
        r = self.run_script("ci-presence", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would set", r.stdout)
        self.assertEqual(self.sets(), [])
        self.assertEqual(self.run_script("ci-presence", "--nope").returncode, 2)
        self.assertEqual(subprocess.run(["bash", "-n", str(SCRIPTS / "ci-presence")]).returncode, 0)

    def test_timer_templates(self):
        svc = (ROOT / "core" / "templates" / "ci-presence.service").read_text()
        timer = (ROOT / "core" / "templates" / "ci-presence.timer").read_text()
        self.assertIn("Type=oneshot", svc)
        self.assertIn('ExecStart=/usr/bin/env bash "@REPO@/core/scripts/ci-presence"', svc)
        self.assertIn('Environment="PATH=@PATH@"', svc)
        self.assertIn("OnCalendar=*:0/15", timer)
        self.assertIn("OnBootSec=", timer)
        self.assertIn("WantedBy=timers.target", timer)


# --- Task 4.5: budget-state, machine-check, machine-setup ----------------------------------------------------------

PLAYBOOK = """
declare -A BUDGET_USAGE=( [main]="cat {feed}" [cheap]="" )
declare -A BUDGET_RESET=( )
declare -A BUDGET_CEILING=( [main]={ceiling} )
DEFAULT_CEILING=40
TIERS="swift standard"
declare -A TIER_ENGINE=( [swift]=none [standard]=claude )
declare -A TIER_BUDGET=( [swift]=cheap [standard]=main )
STEP_NO_NEW=7
STEP_WIP_STOP=4
STEP_STOP_ALL=2
PRESSURE_MARGIN=15
WINDOW_STOP_PCT=98
{extra}
"""


class BudgetTest(MachineTest):
    def playbook(self, weekly, window, ceiling=93, extra=""):
        feed = self.tmp / "feed"
        if weekly is None:
            feed.unlink(missing_ok=True)
        else:
            feed.write_text(f"{weekly} {window}\n")
        conf = self.tmp / "playbook.conf.sh"
        conf.write_text(PLAYBOOK.format(feed=feed, ceiling=ceiling, extra=extra))
        self.env["PLAYBOOK_CONFIG"] = str(conf)

    def marker(self) -> Path:
        return self.conf_dir / "budget-stopped"


class BudgetStateTests(BudgetTest):
    def test_steps_from_ceiling(self):
        for weekly, step in ((70, "step=0 none"), (78, "step=1 pressure"), (86, "step=2 no-new-work"),
                             (89, "step=3 push-and-handover"), (91, "step=4 stop")):
            with self.subTest(weekly=weekly):
                self.playbook(weekly, 10)
                r = self.run_script("budget-state")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stdout, f"main weekly={weekly} window=10 ceiling=93 {step}\n")
        self.assertFalse(self.marker().exists())  # only --mark writes it

    def test_mark_when_stopped_and_clear(self):
        self.playbook(92, 10)
        self.assertEqual(self.run_script("budget-state", "--mark").returncode, 0)
        self.assertTrue(self.marker().is_file())
        self.assertEqual(self.lane_limits("LANE_PRESENCE_WHY")["LANE_PRESENCE_WHY"], "budget stopped")
        self.playbook(10, 10)
        self.run_script("budget-state", "--mark")
        self.assertFalse(self.marker().exists())

    def test_window_stop_marks(self):
        self.playbook(20, 98)
        self.run_script("budget-state", "--mark")
        self.assertTrue(self.marker().is_file())

    def test_failed_feed_is_unknown_not_stopped(self):
        self.marker().touch()
        self.playbook(None, None)
        r = self.run_script("budget-state", "--mark")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "main unknown (the usage feed gave no reading)\n")
        self.assertFalse(self.marker().exists())

    def test_ceiling_add_keeps_running(self):
        self.playbook(92, 10, extra='declare -A COORD_CEILING_ADD=( [p]=10 )')
        self.run_script("budget-state", "--mark")
        self.assertFalse(self.marker().exists())

    def test_no_config(self):
        self.marker().touch()
        self.env["PLAYBOOK_CONFIG"] = str(self.tmp / "none.sh")
        r = self.run_script("budget-state", "--mark")
        self.assertEqual(r.returncode, 2)
        self.assertIn("none.sh", r.stderr)
        self.assertFalse(self.marker().exists())
        self.assertEqual(subprocess.run(["bash", "-n", str(SCRIPTS / "budget-state")]).returncode, 0)


class MachineCheckTests(BudgetTest):
    def setUp(self):
        super().setUp()
        self.playbook(20, 10)
        self.gh({"runners": {}})

    def check(self, *args):
        return self.run_script("machine-check", *args)

    def test_first_run_quiet(self):
        r = self.check()
        self.assertEqual((r.returncode, r.stdout), (1, ""), r.stderr)
        self.assertEqual(self.check().returncode, 1)

    def test_step_change_wakes_once(self):
        self.check()
        self.playbook(86, 10)
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CHANGED budget main: step 2 no-new-work (weekly 86%, ceiling 93; was step 0 none)", r.stdout)
        self.assertEqual(self.check().returncode, 1)
        self.playbook(None, None)
        self.assertIn("CHANGED budget main: unknown", self.check().stdout)

    def test_memory_low_wakes(self):
        self.check()
        (self.proc / "meminfo").write_text(MEMINFO_46G.replace("30000000", "2000000"))
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"CHANGED memory: low, 1G available \(lane-room holds new lanes under \d+G\)")
        self.assertEqual(self.check().returncode, 1)
        (self.proc / "meminfo").write_text(MEMINFO_46G)
        self.assertIn("CHANGED memory: ok again", self.check().stdout)

    def test_runner_offline_wakes(self):
        self.machine_conf('COORDINATORS=""\nRUNNER_REPOS="o/r"\n')
        self.gh({"runners": {"o/r": [{"name": "r1", "status": "online"}]}})
        self.assertEqual(self.check().returncode, 1)
        self.gh({"runners": {"o/r": [{"name": "r1", "status": "offline"}]}})
        r = self.check()
        self.assertIn("CHANGED runners o/r: offline r1 (was none)", r.stdout)
        self.gh({})  # gh fails: the last known state stands, no flapping
        self.assertEqual(self.check().returncode, 1)

    def test_marks_budget_stopped(self):
        self.playbook(92, 10)
        self.check()
        self.assertTrue(self.marker().is_file())

    def test_last_prints_changes(self):
        self.check()
        self.playbook(86, 10)
        changed = self.check().stdout
        r = self.check("--last")
        self.assertEqual(r.returncode, 0)
        self.assertIn(changed.strip(), r.stdout)


class MachineSetupTests(BudgetTest):
    def setUp(self):
        super().setUp()
        self.orca()
        self.systemctl()
        self.gh({"runners": {}})
        self.folder = self.home / "machine"
        self.feed = self.home / ".claude" / "usage-history.jsonl"
        self.feed.parent.mkdir(parents=True)
        self.feed.write_text("{}\n")

    def setup_machine(self, *args):
        return self.run_script("machine-setup", "--folder", self.folder, "--trust-folder", *args)

    def creates(self, what) -> list:
        return [c for c in self.orca_calls() if c[:2] == what]

    def test_writes_machine_conf_from_example(self):
        r = self.setup_machine("--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        conf = self.conf_dir / "machine.conf"
        self.assertEqual(conf.read_text(), (ROOT / "core" / "templates" / "machine.conf.example").read_text())
        self.assertEqual(stat.S_IMODE(conf.stat().st_mode), 0o600)
        self.assertIn("machine.conf: created", r.stdout)
        conf.write_text('COORDINATORS=""\nIDLE_MIN=50\n')
        self.assertIn("machine.conf: exists", self.setup_machine("--no-start").stdout)
        self.assertIn("IDLE_MIN=50", conf.read_text())

    def test_example_conf_has_the_fixed_keys(self):
        import machineconf
        text = (ROOT / "core" / "templates" / "machine.conf.example").read_text()
        for k in ("COORDINATORS", "RUNNER_REPOS", "NIGHT_START=23", "NIGHT_END=7", "IDLE_MIN=45", "RECHECK_MIN=120",
                  "AWAY_HOURS=14"):
            self.assertIn(k, text)
        conf = machineconf.load(ROOT / "core" / "templates" / "machine.conf.example")
        self.assertEqual(machineconf.coordinators(conf), [])
        self.assertEqual((conf["IDLE_MIN"], conf["warnings"]), (45, []))

    def test_usage_feed_check(self):
        self.assertIn("usage feed: ok", self.setup_machine("--no-start").stdout)
        age(self.feed, 90)
        r = self.setup_machine("--no-start")
        self.assertIn("usage feed: stale", r.stdout)
        self.assertIn("unknown", r.stdout)
        self.feed.unlink()
        r = self.setup_machine("--no-start")
        self.assertIn("usage feed: missing", r.stdout)
        self.assertIn("status line", r.stdout)

    def test_starts_claude_machine_coordinator(self):
        r = self.setup_machine()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        persona = self.folder / ".lane-control" / "machine-coordinator.md"
        text = persona.read_text()
        self.assertNotIn("{{", text)
        self.assertIn(str(self.folder / "notes.md"), text)
        self.assertIn(str(ROOT), text)
        self.assertTrue((self.folder / "notes.md").is_file())
        settings = json.loads((self.folder / ".claude" / "settings.local.json").read_text())
        self.assertIn("persona-hook.sh", json.dumps(settings["hooks"]["SessionStart"]))
        self.assertEqual(self.creates(["repo", "add"]), [["repo", "add", "--path", str(self.folder), "--kind", "folder"]])
        (create,) = self.creates(["terminal", "create"])
        self.assertEqual(create[2:6], ["--worktree", f"path:{self.folder}", "--title", "machine coordinator"])
        import shlex
        cmd = shlex.split(create[create.index("--command") + 1])
        self.assertEqual(cmd[:3], ["env", "LANE_ROLE=machine-coordinator", f"LANE_PERSONA={persona}"])
        self.assertEqual(cmd[3:6], ["claude", "--model", "sonnet"])
        self.assertEqual(cmd[-2:], ["--name", "machine coordinator"])
        trust = json.loads((self.home / ".claude.json").read_text())
        self.assertTrue(trust["projects"][str(self.folder.resolve())]["hasTrustDialogAccepted"])

    def test_additions_appended_to_persona(self):
        add = self.folder / ".lane-control" / "additions" / "machine-coordinator.md"
        add.parent.mkdir(parents=True)
        add.write_text("## This machine\n- Runner box: trial-vps.\n")
        self.setup_machine("--no-start")
        text = (self.folder / ".lane-control" / "machine-coordinator.md").read_text()
        self.assertTrue(text.endswith("## This machine\n- Runner box: trial-vps.\n"))

    def test_opencode_machine_coordinator(self):
        r = self.setup_machine("--machine-coordinator", "opencode/anthropic/claude-sonnet-4-5")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        (create,) = self.creates(["terminal", "create"])
        self.assertIn("opencode2 --standalone --auto", create[create.index("--command") + 1])
        self.assertTrue((self.home / ".config" / "opencode" / "plugins" / "lane-persona.js").is_file())
        for bad in ("opencode/opencode-go/deepseek-v4.1-flash", "opencode/opencode/big-pickle", "pi/x", "claude/"):
            with self.subTest(bad=bad):
                r = self.setup_machine("--machine-coordinator", bad)
                self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("lane-tier", self.setup_machine("--machine-coordinator", "opencode/opencode-go/x").stderr)

    def test_running_coordinator_not_restarted(self):
        self.orca({"terminals": [{"handle": "term_1", "title": "machine coordinator", "worktreePath": str(self.folder)}]})
        r = self.setup_machine()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.creates(["terminal", "create"]), [])
        self.assertIn("machine coordinator: exists", r.stdout)

    def automations(self) -> dict:
        out = {}
        for c in self.creates(["automations", "create"]):
            out[c[c.index("--name") + 1]] = c
        return out

    def test_creates_three_automations(self):
        r = self.setup_machine("--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        a = self.automations()
        self.assertEqual(set(a), {"night manager", "lane sweep", "machine check"})
        triggers = {"night manager": "37 * * * *", "lane sweep": "17 */3 * * *", "machine check": "*/30 * * * *"}
        import shlex
        prechecks = {"night manager": shlex.quote(f"{SCRIPTS}/night-precheck"),
                     "lane sweep": shlex.quote(f"{SCRIPTS}/lane-sweep") + " --precheck",
                     "machine check": shlex.quote(f"{SCRIPTS}/machine-check")}
        for name, c in a.items():
            with self.subTest(name=name):
                def opt(k):
                    return c[c.index(k) + 1]
                self.assertEqual(opt("--trigger"), triggers[name])
                self.assertEqual(opt("--precheck"), prechecks[name])
                self.assertEqual(opt("--provider"), "claude")
                self.assertIn("--extra-agent-args=--model sonnet", c)
                self.assertEqual(opt("--workspace"), f"path:{self.folder}")
                self.assertEqual(opt("--workspace-mode"), "existing")
                self.assertIn("--enabled", c)
                self.assertNotIn("--apply", opt("--prompt"))
        self.assertIn("night-manager.md", a["night manager"][a["night manager"].index("--prompt") + 1])

    def test_rerun_is_idempotent(self):
        self.setup_machine()
        n = len(self.orca_calls())
        self.assertEqual(len(self.automations()), 3)
        r = self.setup_machine()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        later = self.orca_calls()[n:]
        self.assertEqual([c for c in later if c[1:2] in (["create"], ["add"])], [])
        for line in ("night manager: exists", "lane sweep: exists", "machine check: exists",
                     "machine coordinator: exists", "ci-presence timer: exists"):
            self.assertIn(line, r.stdout)

    def test_installs_ci_presence_timer(self):
        r = self.setup_machine("--no-start")
        units = self.home / ".config" / "systemd" / "user"
        svc = (units / "ci-presence.service").read_text()
        self.assertIn(f'ExecStart=/usr/bin/env bash "{ROOT}/core/scripts/ci-presence"', svc)
        self.assertNotIn("@REPO@", svc)
        self.assertNotIn("@PATH@", svc)
        self.assertTrue((units / "ci-presence.timer").is_file())
        calls = self.systemctl_calls()
        self.assertIn(["--user", "daemon-reload"], calls)
        self.assertIn(["--user", "enable", "--now", "ci-presence.timer"], calls)
        self.assertIn("ci-presence timer: created", r.stdout)

    def test_no_systemd_warns_and_continues(self):
        self.systemctl({"down": True})
        r = self.setup_machine("--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("ci-presence timer: skipped", r.stdout)
        self.assertEqual(len(self.automations()), 3)
        self.assertFalse((self.home / ".config" / "systemd" / "user" / "ci-presence.timer").exists())

    def test_no_start(self):
        r = self.setup_machine("--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.creates(["terminal", "create"]), [])
        self.assertIn("machine coordinator: skipped", r.stdout)
        self.assertTrue((self.folder / ".lane-control" / "machine-coordinator.md").is_file())

    def test_host_runs_there_over_ssh(self):
        write_exec(self.bin / "ssh", """
            #!/usr/bin/env python3
            import json, os, sys
            log = os.environ["SSH_STUB_LOG"]
            data = sys.stdin.buffer.read() if not sys.stdin.isatty() else b""
            n = len([x for x in os.listdir(os.path.dirname(log)) if x.startswith("ssh-in")])
            open(os.path.join(os.path.dirname(log), f"ssh-in{n}"), "wb").write(data)
            open(log, "a").write(json.dumps(sys.argv[1:]) + "\\n")
            """)
        self.env["SSH_STUB_LOG"] = str(self.tmp / "ssh.log")
        r = self.run_script("machine-setup", "--host", "user@host", "--folder", "~/machine", "--no-start")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        calls = [json.loads(x) for x in (self.tmp / "ssh.log").read_text().splitlines()]
        self.assertEqual(calls[0][0], "user@host")
        self.assertIn("tar -C ~/.local/share/lane-control -xf -", calls[0][1])
        listing = subprocess.run(["tar", "-tf", str(self.tmp / "ssh-in0")], capture_output=True, text=True).stdout
        for f in ("core/scripts/machine-setup", "core/harnesses/persona-hook.sh", ".lane-control-root",
                  "orchestrators/native-orca/personas/machine-coordinator.md"):
            self.assertIn(f, listing)
        self.assertNotIn("tests/", listing)
        self.assertEqual(calls[1][-1], "~/.local/share/lane-control/core/scripts/machine-setup --folder ~/machine --no-start")
        self.assertEqual(self.orca_calls(), [])  # nothing ran here


class NightManagerDocTests(unittest.TestCase):
    def test_night_manager_doc(self):
        text = (ROOT / "orchestrators" / "native-orca" / "night-manager.md").read_text()
        for needle in ("night-precheck", "--checked", "lane-sweep --stuck-hours 2", "SendMessage",
                       "orchestration send --to run:", "no-new-work", "machine.conf", "nothing"):
            self.assertIn(needle, text)
        self.assertNotIn("--apply", text.replace("never `--apply`", "").replace("never --apply", ""))
        for private in ("vellum", "calvin", "pasteur", "osterberg", "/home/", "10.10."):
            self.assertNotIn(private, text.lower())

if __name__ == "__main__":
    unittest.main()
