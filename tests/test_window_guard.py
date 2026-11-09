"""window-guard.sh and its install step against stub cotal, rove and systemctl in a temp dir."""
import importlib.machinery
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "scripts" / "window-guard.sh"
INSTALL = ROOT / "scripts" / "window-guard-install"
SETUP = ROOT / "scripts" / "machine-coordinator-setup"
STUB = ROOT / "tests" / "stubs" / "fake.py"
PREFIX = "machine coordinator: "


class Guard:
    """One temp dir: stub cotal logging each send, a usage feed, a meminfo, a state file."""

    def __init__(self, test):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)
        (self.d / "bin").mkdir()
        self.log = self.d / "sends"
        self.fail = self.d / "fail-spaces"  # spaces whose sends fail, one per line
        self.exe("cotal", f'[ "$1" = send ] || exit 0\ngrep -qx "$3" "{self.fail}" 2>/dev/null && exit 1\n'
                          f'printf \'%s\\n\' "$*" >> "{self.log}"\n')
        self.exe("rove", f'printf \'%s\\n\' "$*" >> "{self.d}/rove-calls"\n')
        (self.d / "conf.sh").write_text(f'''TIERS="deep"
declare -A TIER_BUDGET=( [deep]=main )
declare -A BUDGET_USAGE=( [main]="cat {self.d}/usage" )
declare -A BUDGET_RESET=( [main]="cat {self.d}/reset" )
declare -A BUDGET_CEILING=( [main]=40 )
DEFAULT_CEILING=40
STEP_NO_NEW=7; STEP_WIP_STOP=4; STEP_STOP_ALL=2; PRESSURE_MARGIN=15
MEM_LOW_GB=4; SWAP_HIGH_GB=12; WINDOW_WARN_PCT=95; WINDOW_STOP_PCT=98; POLL_SECS=0
declare -A COORD_SPACE=( [p]=space-p [q]=space-q )
declare -A COORD_TASK=( [p]=t1 [q]=t2 [rove-only]=t3 )
COORD_TAB=tab-1
''')
        self.reset_at = 4102444800  # a reset far ahead, so the "window has reset" branch stays out of the way
        self.usage(10, 10)
        self.memory(avail_gb=30, swap_gb=0)

    def exe(self, name, body):
        p = self.d / "bin" / name
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(0o755)

    def usage(self, weekly, window):
        (self.d / "usage").write_text(f"{weekly} {window}\n")
        (self.d / "reset").write_text(f"{self.reset_at}\n")

    def memory(self, avail_gb, swap_gb):
        (self.d / "meminfo").write_text(f"MemAvailable: {avail_gb * 1048576} kB\nSwapTotal: {32 * 1048576} kB\n"
                                        f"SwapFree: {(32 - swap_gb) * 1048576} kB\n")

    def poll(self, polls=1, **env):
        return subprocess.run(["bash", str(GUARD)], capture_output=True, text=True, timeout=60, env={
            "PATH": f"{self.d}/bin:" + os.environ["PATH"], "HOME": str(self.d), "PLAYBOOK_CONFIG": str(self.d / "conf.sh"),
            "GUARD_STATE_FILE": str(self.d / "state"), "GUARD_MEMINFO": str(self.d / "meminfo"),
            "GUARD_POLLS": str(polls), **env})

    def sends(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def rove_calls(self):
        f = self.d / "rove-calls"
        return f.read_text().splitlines() if f.exists() else []

    def clear(self):
        self.log.unlink(missing_ok=True)


@unittest.skipUnless(shutil.which("bash") and shutil.which("timeout"), "needs bash and timeout")
class OrdersByCotalTests(unittest.TestCase):
    def setUp(self):
        self.g = Guard(self)

    def test_a_window_order_goes_to_each_coordinators_space_and_never_to_rove(self):
        self.g.usage(10, 96)
        r = self.g.poll()
        self.assertEqual(r.returncode, 0, r.stderr)
        sends = self.g.sends()
        self.assertEqual({s.split()[2] for s in sends}, {"space-p", "space-q"})
        for s in sends:
            self.assertIn(" -t coordinator ", s)
            self.assertIn(f"{PREFIX}ORDER (window guard)", s)
        self.assertEqual(self.g.rove_calls(), [])

    def test_a_project_only_in_the_rove_list_gets_no_order(self):
        self.g.usage(38, 96)  # weekly step and window step together
        self.g.poll()
        self.assertFalse([s for s in self.g.sends() if "rove-only" in s])
        self.assertEqual(self.g.rove_calls(), [])

    def test_the_reset_message_names_no_rove_verb(self):
        self.g.reset_at = 1000
        self.g.usage(10, 96)
        state = self.g.d / "state"
        state.write_text(f"stage main 1\nreset_at main 1000\nsaved {int(__import__('time').time())} 0\n")
        self.g.poll()
        reset = [s for s in self.g.sends() if "has reset" in s]
        self.assertEqual(len(reset), 2)
        self.assertNotIn("rove", reset[0])

    def test_weekly_orders_are_per_project_and_not_resent_after_a_restart(self):
        self.g.usage(34, 10)  # ceiling 40 - 7 = 33: no new work
        self.g.poll()
        first = self.g.sends()
        self.assertEqual(len(first), 2)
        self.assertTrue(all("No new work" in s and PREFIX in s for s in first), first)
        self.g.clear()
        self.g.poll()  # a new process, same state file
        self.assertEqual(self.g.sends(), [])

    def test_an_empty_space_list_warns_and_sends_nothing(self):
        conf = self.g.d / "conf.sh"
        conf.write_text(conf.read_text().replace("declare -A COORD_SPACE=( [p]=space-p [q]=space-q )\n", ""))
        self.g.usage(38, 96)
        r = self.g.poll()
        self.assertIn("COORD_SPACE", r.stderr)
        self.assertEqual(self.g.sends(), [])


@unittest.skipUnless(shutil.which("bash") and shutil.which("timeout"), "needs bash and timeout")
class FailedSendTests(unittest.TestCase):
    def setUp(self):
        self.g = Guard(self)

    def test_a_failed_send_is_delivered_on_a_later_poll_and_only_once(self):
        self.g.fail.write_text("space-p\n")
        self.g.usage(34, 10)
        self.g.poll()
        self.assertEqual({s.split()[2] for s in self.g.sends()}, {"space-q"})
        self.g.fail.write_text("")
        self.g.clear()
        self.g.poll()
        sends = self.g.sends()
        self.assertEqual(len(sends), 1, sends)
        self.assertIn("space-p", sends[0])
        self.assertIn("No new work", sends[0])
        self.g.clear()
        self.g.poll()
        self.assertEqual(self.g.sends(), [])

    def test_a_later_order_waits_behind_the_earlier_one(self):
        self.g.fail.write_text("space-p\n")
        self.g.usage(34, 10)
        self.g.poll()
        self.g.usage(37, 10)  # next step while the first is still undelivered
        self.g.poll()
        self.g.fail.write_text("")
        self.g.clear()
        self.g.poll()
        p = [s for s in self.g.sends() if "space-p" in s]
        self.assertEqual(len(p), 2, p)
        self.assertIn("No new work", p[0])
        self.assertIn("commit and push WIP", p[1])

    def test_an_order_undelivered_past_the_retry_time_tells_the_machine_coordinator(self):
        self.g.fail.write_text("space-p\n")
        self.g.usage(34, 10)
        self.g.poll()
        self.g.clear()
        self.g.poll(GUARD_RETRY_SECS="0")
        gave_up = [s for s in self.g.sends() if " -s machine -t machine-coordinator " in s]
        self.assertEqual(len(gave_up), 1, self.g.sends())
        self.assertIn("space-p", gave_up[0])
        self.g.clear()
        self.g.poll()
        self.assertEqual(self.g.sends(), [])

    def test_a_hanging_send_cannot_stall_the_guard(self):
        self.g.exe("cotal", "exec sleep 30\n")
        self.g.usage(34, 10)
        r = self.g.poll(GUARD_SEND_TIMEOUT="1")
        self.assertEqual(r.returncode, 0, r.stderr)


@unittest.skipUnless(shutil.which("bash") and shutil.which("timeout"), "needs bash and timeout")
class MemoryNoticeTests(unittest.TestCase):
    def setUp(self):
        self.g = Guard(self)

    def notices(self):
        return [s for s in self.g.sends() if " -s machine -t machine-coordinator " in s]

    def test_low_memory_needs_two_polls_in_a_row(self):
        self.g.memory(avail_gb=3, swap_gb=0)
        self.g.poll()
        self.assertEqual(self.notices(), [])
        self.g.memory(avail_gb=30, swap_gb=0)
        self.g.poll()
        self.g.memory(avail_gb=3, swap_gb=0)
        self.g.poll()
        self.assertEqual(self.notices(), [])
        self.g.poll()
        self.assertEqual(len(self.notices()), 1)

    def test_one_notice_per_episode_and_a_new_episode_notifies_again(self):
        self.g.memory(avail_gb=3, swap_gb=0)
        self.g.poll(polls=5)
        self.assertEqual(len(self.notices()), 1)
        self.g.memory(avail_gb=30, swap_gb=0)
        self.g.poll()  # one good poll does not end the episode
        self.g.memory(avail_gb=3, swap_gb=0)
        self.g.poll(polls=2)
        self.assertEqual(len(self.notices()), 1)
        self.g.memory(avail_gb=30, swap_gb=0)
        self.g.poll(polls=2)
        self.g.memory(avail_gb=3, swap_gb=0)
        self.g.poll(polls=2)
        self.assertEqual(len(self.notices()), 2)

    def test_high_swap_opens_an_episode_at_once_and_stays_one(self):
        self.g.memory(avail_gb=30, swap_gb=13)
        self.g.poll()
        self.assertEqual(len(self.notices()), 1)
        self.g.poll(polls=3)
        self.assertEqual(len(self.notices()), 1)

    def test_the_notice_survives_a_restart_as_one_notice(self):
        self.g.memory(avail_gb=3, swap_gb=0)
        self.g.poll(polls=2)
        self.g.clear()
        self.g.poll(polls=2)  # new process, episode still open
        self.assertEqual(self.notices(), [])

    def test_the_memory_notice_goes_only_to_the_machine_coordinator(self):
        self.g.memory(avail_gb=3, swap_gb=13)
        self.g.poll(polls=2)
        self.assertEqual(len(self.g.sends()), 1, self.g.sends())
        self.assertRegex(self.g.sends()[0], r"-s machine -t machine-coordinator .*MEMORY")


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.g = Guard(self)
        self.calls = self.g.d / "systemctl-calls"
        self.g.exe("systemctl", f'printf \'%s\\n\' "$*" >> "{self.calls}"\ncase "$*" in *is-active*) exit 3;; esac\n')
        self.g.exe("pgrep", "exit 1\n")
        self.g.exe("jq", "exit 0\n")

    def install(self, *args):
        return subprocess.run(["bash", str(INSTALL), *args], capture_output=True, text=True, timeout=60, env={
            "PATH": f"{self.g.d}/bin:" + os.environ["PATH"], "HOME": str(self.g.d),
            "PLAYBOOK_CONFIG": str(self.g.d / "conf.sh"), "XDG_CONFIG_HOME": str(self.g.d / "cfg")})

    def unit(self):
        return self.g.d / "cfg" / "systemd" / "user" / "lane-control-window-guard.service"

    def test_writes_the_unit_for_this_checkout_and_starts_it(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.unit().read_text()
        self.assertIn(f"{ROOT}/scripts/window-guard.sh", text)
        self.assertIn("Restart=always", text)
        self.assertRegex(text, r"Environment=\"PATH=.*" + re.escape(str(self.g.d)) + "/bin")
        self.assertNotIn("@", text)
        calls = self.calls.read_text()
        self.assertIn("daemon-reload", calls)
        self.assertIn("enable --now lane-control-window-guard.service", calls)

    def test_refuses_while_an_old_guard_or_watch_is_running(self):
        self.g.exe("pgrep", "echo 4242\n")
        r = self.install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("4242", r.stderr)
        self.assertFalse(self.unit().exists())
        self.assertFalse(self.calls.exists() and "enable" in self.calls.read_text())

    def test_refuses_a_config_with_no_coordinator_spaces(self):
        conf = self.g.d / "conf.sh"
        conf.write_text(conf.read_text().replace("declare -A COORD_SPACE=( [p]=space-p [q]=space-q )\n", ""))
        r = self.install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("COORD_SPACE", r.stderr)
        self.assertFalse(self.unit().exists())

    def test_reinstall_restarts_the_running_unit(self):
        self.g.exe("systemctl", f'printf \'%s\\n\' "$*" >> "{self.calls}"\n')
        self.g.exe("pgrep", "echo 77\n")  # the unit's own guard process must not block a reinstall
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("restart lane-control-window-guard.service", self.calls.read_text())


class MachineCoordinatorSetupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        for name in ("orca", "orca-ide", "cotal"):
            (self.bin / name).symlink_to(STUB)
        (self.bin / "claude").write_text("#!/bin/sh\nexit 0\n")
        (self.bin / "claude").chmod(0o755)
        self.log = self.home / "stub.log"
        tools = {str(Path(shutil.which(t)).parent) for t in ("git", "python3", "env", "sh")}
        (self.home / ".gitconfig").write_text("[user]\n\tname = t\n\temail = t@t\n")
        self.folder = self.home / "code" / "machine-coordinator"
        self.env = {**os.environ, "HOME": str(self.home), "PATH": ":".join([str(self.bin), *sorted(tools)]),
                    "STUB_LOG": str(self.log), "STUB_STATE": str(self.home / "stub.json"),
                    "GIT_CONFIG_GLOBAL": str(self.home / ".gitconfig")}
        self.env.pop("XDG_CONFIG_HOME", None)

    def run_setup(self, *args):
        return subprocess.run([sys.executable, str(SETUP), "--folder", str(self.folder), *args], env=self.env,
                              capture_output=True, text=True, timeout=120)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def persona(self):
        return (self.folder / ".cotal" / "agents" / "machine-coordinator.md").read_text()

    def test_no_start_makes_folder_space_and_persona_and_starts_nothing(self):
        r = self.run_setup("--no-start")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.folder / ".git").exists())
        self.assertTrue((self.folder / "notes.md").exists())
        self.assertIn(".cotal/", (self.folder / ".gitignore").read_text())
        self.assertNotIn("{{", self.persona())
        self.assertIn("name: machine-coordinator", self.persona())
        joined = "\n".join(self.calls())
        self.assertIn("space create machine", joined)
        self.assertNotIn("cotal spawn", joined)
        self.assertNotIn("cotal up", joined)

    def test_start_spawns_the_agent_in_its_own_folder_and_space(self):
        r = self.run_setup("--trust-folder")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        spawn = [c for c in self.calls() if c.startswith("cotal spawn machine-coordinator")]
        self.assertEqual(len(spawn), 1, self.calls())
        self.assertIn(f"--cwd {self.folder}", spawn[0])
        self.assertIn("--space machine", spawn[0])
        self.assertIn("claude", spawn[0])

    def test_a_rerun_keeps_the_notes_and_spawns_nothing_new(self):
        self.assertEqual(self.run_setup("--trust-folder").returncode, 0)
        (self.folder / "notes.md").write_text("private decision\n")
        r = self.run_setup("--trust-folder")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.folder / "notes.md").read_text(), "private decision\n")
        self.assertEqual(len([c for c in self.calls() if c.startswith("cotal spawn machine-coordinator")]), 1)

    def test_import_has_no_side_effects(self):
        loader = importlib.machinery.SourceFileLoader("mc_setup", str(SETUP))
        spec = importlib.util.spec_from_loader("mc_setup", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        self.assertEqual(mod.SPACE, "machine")


class RetiredWatchTests(unittest.TestCase):
    def test_machine_watch_is_gone_from_scripts_and_docs(self):
        self.assertFalse((ROOT / "scripts" / "machine-watch.sh").exists())
        for rel in ("README.md", "docs/multi-machine.md", "docs/orca.md", "roles.md", "config.example.sh"):
            self.assertNotIn("machine-watch", (ROOT / rel).read_text(), rel)
        self.assertNotIn("WINDOW_WATCH_PCT", (ROOT / "config.example.sh").read_text())

    def test_guard_has_no_rove_call(self):
        self.assertNotIn("rove api", GUARD.read_text())


def flat(path):
    return " ".join(path.read_text().split())


class MachineCoordinatorContentTests(unittest.TestCase):
    def test_coordinator_counts_the_order_like_the_owners_words_from_a_shell_sender_only(self):
        t = flat(ROOT / "personas" / "coordinator.md")
        self.assertIn("`machine coordinator:`", t)
        self.assertIn("like the owner's own words", t)
        self.assertIn("no limit on the kind", t)
        self.assertRegex(t, r"not (come )?from an agent in your roster")

    def test_coordinator_keeps_owner_decisions_unless_the_owner_told_the_machine_coordinator(self):
        t = flat(ROOT / "personas" / "coordinator.md")
        self.assertIn("unless the order says the owner decided it", t)

    def test_coordinator_reaches_it_by_cotal_send_and_tells_the_owner_when_that_fails(self):
        t = flat(ROOT / "personas" / "coordinator.md")
        self.assertIn("cotal send -s machine -t machine-coordinator", t)
        self.assertIn("If that send fails, tell the owner", t)

    def test_machine_persona_rules(self):
        t = flat(ROOT / "personas" / "machine-coordinator.md")
        self.assertIn('cotal send -s <space> -t coordinator "machine coordinator:', t)
        self.assertIn("{{NOTES}}", t)
        self.assertIn("{{HARNESS}}", t)
        self.assertIn("{{LANE_CONTROL}}", t)
        self.assertRegex(t, r"[Nn]ever .*public")

    def test_skill_and_docs_name_the_machine_coordinator_path(self):
        skill = flat(ROOT / "skills" / "orca-cotal-lanes" / "SKILL.md")
        self.assertIn("## The machine coordinator", skill)
        self.assertIn("machine coordinator:", skill)
        orca = flat(ROOT / "docs" / "orca.md")
        for needle in ("window-guard-install", "machine-coordinator-setup", "COORD_SPACE", "enable-linger", "--refresh"):
            self.assertIn(needle, orca)
        roles = flat(ROOT / "roles.md")
        self.assertIn("Under Orca + Cotal", roles)

    def test_config_example_lists_the_spaces(self):
        self.assertIn("COORD_SPACE", (ROOT / "config.example.sh").read_text())


if __name__ == "__main__":
    unittest.main()
