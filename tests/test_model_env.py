"""The model environment (field report E2, E3, E4; core/modelenv.py and core/scripts/model-env): one source, the homes
generated from it, a check that compares every home value by value, and the served-model check at write time. HOME and
XDG are temp dirs; the gateway is tests/stubs/fake_gateway.py on 127.0.0.1; systemctl is a stub."""
import testhome  # noqa: F401,I001  first: temp HOME and XDG_*, and the watch on the real ones
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_project_setup_native import SETUP, NativeEnv  # noqa: E402
from test_remote_native import run_pty  # noqa: E402
from test_tier_suggest import KEY, logged, start_gateway  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "core" / "scripts" / "model-env"
sys.path.insert(0, str(ROOT / "core"))
import modelenv as M  # noqa: E402

# `systemctl --user show-environment` prints $SYSTEMD_ENV_FILE; anything else is refused (nothing here may change a unit)
SYSTEMCTL = """#!/bin/sh
[ "$*" = "--user show-environment" ] || { echo "systemctl stub: not modelled: $*" >&2; exit 1; }
cat "$SYSTEMD_ENV_FILE" 2>/dev/null
exit 0
"""
CLASSIFIER = "CLAUDE_CODE_AUTO_MODE_MODEL"


def clean_env() -> dict:
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("ANTHROPIC_", "CLAUDE_", "XDG_", "LANE_", "OPENCODE_"))}


class ModelEnvTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="model-env-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.conf = self.home / ".config"
        (self.conf / "fish").mkdir(parents=True)
        bin_ = self.home / "bin"
        bin_.mkdir()
        (bin_ / "systemctl").write_text(SYSTEMCTL)
        (bin_ / "systemctl").chmod(0o755)
        self.manager = self.home / "manager.env"
        self.env = {**clean_env(), **testhome.env_for(self.home), "XDG_CONFIG_HOME": str(self.conf),
                    "PATH": f"{bin_}:{os.environ['PATH']}", "SYSTEMD_ENV_FILE": str(self.manager),
                    "PYTHONDONTWRITEBYTECODE": "1"}
        self.source = self.conf / "lane-control" / "model-env.conf"
        self.sh = self.conf / "lane-control" / "model-env.sh"
        self.fish = self.conf / "fish" / "conf.d" / "lane-control-models.fish"
        self.settings = self.home / ".claude" / "settings.json"
        self.log = self.home / "gateway.log"

    def run_env(self, *args, env=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], env={**self.env, **(env or {})},
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=90)

    def gateway(self, models, key=KEY, **extra):
        return start_gateway(self, self.home, models, None, key, self.log, **extra)

    def put(self, base, *pairs, env=None, flags=()):
        """model-env --set for a gateway at base, with the key where Claude Code reads it."""
        args = []
        for pair in (f"ANTHROPIC_BASE_URL={base}", *pairs):
            args += ["--set", pair]
        return self.run_env(*args, *flags, env={"ANTHROPIC_AUTH_TOKEN": KEY, **(env or {})})

    def values(self) -> dict:
        return dict(l.split("=", 1) for l in self.source.read_text().splitlines() if l and not l.startswith("#"))

    def settings_env(self) -> dict:
        return json.loads(self.settings.read_text()).get("env", {})

    def sourced(self, shell_file: Path, name: str) -> str:
        return subprocess.run(["sh", "-c", f'. "$1"; printf %s "${{{name}-}}"', "sh", str(shell_file)],
                              env=clean_env(), capture_output=True, text=True, check=True).stdout


# --- E3: one source, generated homes -------------------------------------------------------------------------------

class WriteTests(ModelEnvTest):
    def test_one_source_generates_the_shell_file_the_fish_mirror_and_the_claude_settings_env(self):
        base = self.gateway(["big", "small"])
        r = self.put(base, "ANTHROPIC_MODEL=big", f"{CLASSIFIER}=small")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        want = {"ANTHROPIC_BASE_URL": base, "ANTHROPIC_MODEL": "big", CLASSIFIER: "small"}
        self.assertEqual(self.values(), want)
        for name, value in want.items():
            self.assertEqual(self.sourced(self.sh, name), value, name)
            self.assertIn(f"set -gx {name} '{value}'", self.fish.read_text())
        self.assertEqual(self.settings_env(), want)
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), 0o600)
        self.assertIn(f". {self.sh}", r.stdout)  # the line the owner adds; nothing edits a shell startup file
        self.assertIn("keeps the environment it was born with", r.stdout)

    def test_the_classifier_model_is_in_the_generated_settings_and_shell_env(self):  # E4
        base = self.gateway(["big", "small"])
        self.assertEqual(self.put(base, f"{CLASSIFIER}=small").returncode, 0)
        self.assertEqual(self.settings_env()[CLASSIFIER], "small")
        self.assertEqual(self.sourced(self.sh, CLASSIFIER), "small")

    def test_a_gateway_without_a_classifier_model_is_refused_and_nothing_is_written(self):  # E4: no default
        r = self.put(self.gateway(["big"]), "ANTHROPIC_MODEL=big")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn(CLASSIFIER, r.stderr)
        self.assertIn("non-reasoning", r.stderr)
        for f in (self.source, self.sh, self.fish, self.settings):
            self.assertFalse(f.exists(), f)

    def test_a_gateway_already_in_the_users_settings_also_needs_the_classifier_model(self):
        self.settings.parent.mkdir()
        self.settings.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9"}}))
        r = self.run_env("--set", "ANTHROPIC_MODEL=big", "--no-model-check")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn(CLASSIFIER, r.stderr)

    def test_without_a_gateway_no_classifier_model_is_asked_for(self):
        r = self.run_env("--set", "ANTHROPIC_MODEL=claude-sonnet-4-6")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_in_a_terminal_it_asks_for_the_classifier_model(self):
        base = self.gateway(["small"])
        rc, out = run_pty([sys.executable, str(SCRIPT), "--set", f"ANTHROPIC_BASE_URL={base}"],
                          {**self.env, "ANTHROPIC_AUTH_TOKEN": KEY}, answer="small", prompt="classifier model:")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.values()[CLASSIFIER], "small")

    def test_nothing_to_set_and_no_terminal_says_how_and_writes_nothing(self):
        r = self.run_env()
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("--set", r.stderr)
        self.assertFalse(self.source.exists())

    def test_a_rerun_changes_nothing_and_says_exists(self):
        base = self.gateway(["small"])
        self.put(base, f"{CLASSIFIER}=small")
        before = {f: (f.read_bytes(), f.stat().st_mtime_ns) for f in (self.source, self.sh, self.fish, self.settings)}
        r = self.run_env("--no-model-check")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        for f, was in before.items():
            self.assertEqual((f.read_bytes(), f.stat().st_mtime_ns), was, f)
        self.assertIn("shell env: exists", r.stdout)

    def test_a_changed_value_reaches_every_home_and_an_unset_one_leaves_them(self):
        base = self.gateway(["big", "small", "other"])
        self.put(base, "ANTHROPIC_MODEL=big", f"{CLASSIFIER}=small")
        r = self.put(base, f"{CLASSIFIER}=other", flags=("--unset", "ANTHROPIC_MODEL"))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.settings_env(), {"ANTHROPIC_BASE_URL": base, CLASSIFIER: "other"})
        self.assertNotIn("ANTHROPIC_MODEL", self.sh.read_text())
        self.assertNotIn("ANTHROPIC_MODEL", self.fish.read_text())

    def test_leaving_the_gateway_is_not_held_back_by_what_we_wrote_ourselves(self):
        # the old source, the settings env we wrote and a shell born from them all still name the gateway
        base = self.gateway(["small"])
        self.put(base, f"{CLASSIFIER}=small")
        r = self.run_env("--unset", "ANTHROPIC_BASE_URL", "--unset", CLASSIFIER,
                         env={"ANTHROPIC_BASE_URL": base, CLASSIFIER: "small"})
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual((self.values(), self.settings_env()), ({}, {}))

    def test_a_gateway_still_named_by_hand_keeps_asking_for_the_classifier_model(self):
        base = self.gateway(["small"])
        self.put(base, f"{CLASSIFIER}=small")
        r = self.run_env("--unset", "ANTHROPIC_BASE_URL", "--unset", CLASSIFIER,
                         env={"ANTHROPIC_BASE_URL": "http://127.0.0.1:9"})
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_other_settings_and_env_entries_are_kept(self):
        self.settings.parent.mkdir()
        self.settings.write_text(json.dumps({"model": "x", "env": {"API_TIMEOUT_MS": "5"}, "hooks": {"a": []}}))
        base = self.gateway(["small"])
        self.assertEqual(self.put(base, f"{CLASSIFIER}=small").returncode, 0)
        data = json.loads(self.settings.read_text())
        self.assertEqual((data["model"], data["hooks"], data["env"]["API_TIMEOUT_MS"]), ("x", {"a": []}, "5"))

    def test_a_hand_written_settings_value_is_reported_and_never_edited(self):
        self.settings.parent.mkdir()
        self.settings.write_text(json.dumps({"env": {"ANTHROPIC_MODEL": "by-hand"}}))
        base = self.gateway(["big", "small"])
        r = self.put(base, "ANTHROPIC_MODEL=big", f"{CLASSIFIER}=small")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(self.settings_env()["ANTHROPIC_MODEL"], "by-hand")
        self.assertEqual(self.settings_env()[CLASSIFIER], "small")
        self.assertIn("hand-written", r.stdout)
        self.assertIn("by-hand", r.stdout)

    def test_a_value_the_owner_edited_after_us_is_theirs(self):
        base = self.gateway(["big", "small", "new"])
        self.put(base, "ANTHROPIC_MODEL=big", f"{CLASSIFIER}=small")
        data = json.loads(self.settings.read_text())
        data["env"]["ANTHROPIC_MODEL"] = "edited"
        self.settings.write_text(json.dumps(data))
        r = self.put(base, "ANTHROPIC_MODEL=new")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(self.settings_env()["ANTHROPIC_MODEL"], "edited")
        r = self.put(base, flags=("--unset", "ANTHROPIC_MODEL"))  # nor is it removed
        self.assertEqual(self.settings_env()["ANTHROPIC_MODEL"], "edited", r.stdout)

    def test_a_hand_written_or_hand_edited_shell_file_is_never_overwritten(self):
        base = self.gateway(["small", "new"])
        self.fish.parent.mkdir(parents=True)
        self.fish.write_text("set -gx ANTHROPIC_MODEL mine\n")
        self.put(base, f"{CLASSIFIER}=small")
        self.assertEqual(self.fish.read_text(), "set -gx ANTHROPIC_MODEL mine\n")
        edited = self.sh.read_text() + "export ANTHROPIC_MODEL=mine\n"
        self.sh.write_text(edited)
        r = self.put(base, f"{CLASSIFIER}=new")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(self.sh.read_text(), edited)
        self.assertIn("edited by hand", r.stdout)

    def test_settings_that_are_not_json_are_left_as_they_are(self):
        self.settings.parent.mkdir()
        self.settings.write_text("{ nope")
        r = self.put(self.gateway(["small"]), f"{CLASSIFIER}=small")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertEqual(self.settings.read_text(), "{ nope")
        self.assertIn("not valid JSON", r.stdout)

    def test_claude_config_dir_is_where_the_settings_go(self):
        other = self.home / "claude-profile"
        r = self.put(self.gateway(["small"]), f"{CLASSIFIER}=small", env={"CLAUDE_CONFIG_DIR": str(other)})
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(json.loads((other / "settings.json").read_text())["env"][CLASSIFIER], "small")
        self.assertFalse(self.settings.exists())

    def test_no_fish_folder_no_fish_file(self):
        shutil.rmtree(self.conf / "fish")
        self.assertEqual(self.put(self.gateway(["small"]), f"{CLASSIFIER}=small").returncode, 0)
        self.assertFalse((self.conf / "fish").exists())


class RefusedValueTests(ModelEnvTest):
    SECRET = "sentinel-secret-value-99"

    def test_keys_and_tokens_are_never_written_or_printed(self):
        for pair in (f"ANTHROPIC_API_KEY={self.SECRET}", f"ANTHROPIC_AUTH_TOKEN={self.SECRET}",
                     f"ANTHROPIC_CUSTOM_HEADERS={self.SECRET}", self.SECRET,
                     f"ANTHROPIC_BASE_URL=http://user:{self.SECRET}@127.0.0.1:9",
                     f"ANTHROPIC_BASE_URL=http://127.0.0.1:9/?key={self.SECRET}",
                     "ANTHROPIC_MODEL=sk-ant-abcdefghijklmnopqrstuvwx"):
            with self.subTest(pair=pair.split("=")[0]):
                r = self.run_env("--set", pair)
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertNotIn(self.SECRET, r.stdout + r.stderr)
                self.assertNotIn("sk-ant-abcdefghij", r.stdout + r.stderr)
                self.assertFalse(self.source.exists())

    def test_a_value_a_shell_would_expand_is_refused(self):
        for bad in ("a b", "$(id)", "a'b", "`x`", "a;b", ""):
            with self.subTest(bad=bad):
                self.assertEqual(self.run_env("--set", f"ANTHROPIC_MODEL={bad}").returncode, 2)

    def test_a_name_lane_control_does_not_manage_is_refused(self):
        r = self.run_env("--set", "PATH=/tmp")
        self.assertEqual(r.returncode, 2)
        self.assertIn("ANTHROPIC_MODEL", r.stderr)  # it names what it takes

    def test_a_hand_edited_source_is_validated_too(self):
        self.source.parent.mkdir(parents=True)
        self.source.write_text("ANTHROPIC_MODEL=$(id)\n")
        r = self.run_env()
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertFalse(self.sh.exists())

    def test_a_classifier_model_claude_code_ignores_is_said(self):  # its docs: Opus 5, Sonnet 5.5, Opus 5.5
        for name in ("claude-opus-5", "claude-sonnet-5-5", "claude-opus-5-5"):
            with self.subTest(name=name):
                r = self.run_env("--set", f"{CLASSIFIER}={name}")
                self.assertIn("ignores", r.stdout, r.stderr)
        r = self.run_env("--set", f"{CLASSIFIER}=claude-sonnet-4-6")
        self.assertNotIn("ignores", r.stdout)

    def test_a_classifier_alias_that_sounds_like_a_reasoning_model_is_said(self):
        r = self.run_env("--set", f"{CLASSIFIER}=gw-reasoning")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("reasoning", r.stdout)
        self.assertIn("warning", r.stdout)


# --- E3: --check ---------------------------------------------------------------------------------------------------

class CheckTests(ModelEnvTest):
    def setUp(self):
        super().setUp()
        self.base = self.gateway(["big", "small"])
        r = self.put(self.base, "ANTHROPIC_MODEL=big", f"{CLASSIFIER}=small")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.live = {"ANTHROPIC_BASE_URL": self.base, "ANTHROPIC_MODEL": "big", CLASSIFIER: "small"}

    def check(self, env=None):
        return self.run_env("--check", env=self.live if env is None else env)

    def snapshot(self):
        return {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.home.rglob("*")
                if p.is_file() and p != self.log}

    def test_all_agreeing_is_ok_and_every_value_has_its_line(self):
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for home in ("shell env", "fish env", "claude settings", "this shell"):
            for name in self.live:
                self.assertIn(f"{home}: ok {name}", r.stdout)
        self.assertIn("check: ok", r.stdout)
        self.assertIn("keeps the environment it was born with", r.stdout)

    def test_it_changes_nothing_and_asks_no_gateway(self):
        before, asked = self.snapshot(), len(logged(self.log))
        self.settings.write_text(json.dumps({"env": {"ANTHROPIC_MODEL": "other"}}))
        before = self.snapshot()
        self.assertEqual(self.check({}).returncode, 1)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(logged(self.log)), asked)

    def test_each_differing_or_missing_value_is_named_per_home(self):
        data = json.loads(self.settings.read_text())
        data["env"]["ANTHROPIC_MODEL"] = "drifted"
        del data["env"][CLASSIFIER]
        self.settings.write_text(json.dumps(data))
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertRegex(r.stdout, r"claude settings: differs ANTHROPIC_MODEL.*drifted.*big")
        self.assertRegex(r.stdout, rf"claude settings: missing {CLASSIFIER}")
        self.assertIn("shell env: ok ANTHROPIC_MODEL", r.stdout)

    def test_hand_written_blocks_in_shell_startup_files_are_reported_with_file_and_line(self):
        bashrc = self.home / ".bashrc"
        bashrc.write_text("# mine\nexport ANTHROPIC_MODEL=old   # the old one\n"
                          f'export {CLASSIFIER}="small"\n# export ANTHROPIC_MODEL=commented\n')
        fishrc = self.conf / "fish" / "config.fish"
        fishrc.write_text("set -gx ANTHROPIC_DEFAULT_HAIKU_MODEL stale\n")
        envd = self.conf / "environment.d"
        envd.mkdir()
        (envd / "10-models.conf").write_text("ANTHROPIC_BASE_URL=http://127.0.0.1:1\n")
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertRegex(r.stdout, rf"hand-written: differs ANTHROPIC_MODEL.*{bashrc}:2.*old.*big")
        self.assertRegex(r.stdout, rf"hand-written: ok {CLASSIFIER}.*{bashrc}:3")
        self.assertRegex(r.stdout, rf"hand-written: extra ANTHROPIC_DEFAULT_HAIKU_MODEL.*stale.*{fishrc}:1")
        self.assertRegex(r.stdout, r"hand-written: differs ANTHROPIC_BASE_URL.*10-models\.conf:1")
        self.assertNotIn("commented", r.stdout)
        self.assertIn("export ANTHROPIC_MODEL=old", bashrc.read_text())

    def test_a_value_only_the_shell_can_work_out_is_not_called_ok(self):
        (self.home / ".profile").write_text('export ANTHROPIC_MODEL="$(cat ~/model)"\n')
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertRegex(r.stdout, r"hand-written: not compared ANTHROPIC_MODEL")

    def test_secrets_in_the_places_it_reads_are_never_printed(self):
        secret = "sentinel-secret-value-99"
        (self.home / ".bashrc").write_text(
            f"export ANTHROPIC_API_KEY={secret}\nexport ANTHROPIC_BASE_URL=http://u:{secret}@127.0.0.1:1\n"
            f"export ANTHROPIC_MODEL=sk-ant-{secret}\n")
        self.manager.write_text(f"ANTHROPIC_AUTH_TOKEN={secret}\nANTHROPIC_BASE_URL=http://h/?token={secret}\n")
        data = json.loads(self.settings.read_text())
        data["env"]["ANTHROPIC_AUTH_TOKEN"] = secret
        self.settings.write_text(json.dumps(data))
        r = self.check({**self.live, "ANTHROPIC_AUTH_TOKEN": secret})
        self.assertNotIn(secret, r.stdout + r.stderr)

    def test_project_settings_env_is_a_home_too(self):
        repo = self.home / "code" / "app"
        (repo / ".claude").mkdir(parents=True)
        (repo / ".claude" / "settings.local.json").write_text(json.dumps({"env": {"ANTHROPIC_MODEL": "per-project"}}))
        store = self.conf / "lane-control" / "projects" / "app-0123456789"
        store.mkdir(parents=True)
        (store / "settings.json").write_text(json.dumps({"root": str(repo)}))
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertRegex(r.stdout, r"project settings: differs ANTHROPIC_MODEL.*per-project")

    def test_the_systemd_user_managers_environment_is_a_home_too(self):
        self.manager.write_text("HOME=/x\nANTHROPIC_MODEL=from-the-manager\n")
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertRegex(r.stdout, r"systemd user manager: differs ANTHROPIC_MODEL.*from-the-manager")

    def test_a_shell_born_without_the_values_says_so(self):
        r = self.check({})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertRegex(r.stdout, r"this shell: missing ANTHROPIC_MODEL")

    def test_a_generated_file_that_is_gone_is_missing(self):
        self.sh.unlink()
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertRegex(r.stdout, r"shell env: missing")

    def test_no_source_is_said_not_ok(self):
        self.source.unlink()
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertRegex(r.stdout, r"source: missing")
        self.assertNotIn("check: ok", r.stdout)

    def test_check_takes_no_set(self):
        self.assertEqual(self.run_env("--check", "--set", "ANTHROPIC_MODEL=big").returncode, 2)


# --- E2: the served-model check at write time ----------------------------------------------------------------------

class ServedTests(ModelEnvTest):
    def test_one_request_to_v1_models_and_an_unserved_id_writes_nothing(self):
        base = self.gateway(["big", "small"])
        r = self.put(base, "ANTHROPIC_MODEL=big", "ANTHROPIC_DEFAULT_OPUS_MODEL=dead-alias", f"{CLASSIFIER}=small")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("dead-alias", r.stdout + r.stderr)
        self.assertIn("big, small", r.stdout + r.stderr)  # what it does serve
        self.assertEqual([q["path"] for q in logged(self.log)], ["/v1/models"])
        for f in (self.source, self.sh, self.fish, self.settings):
            self.assertFalse(f.exists(), f)
        self.assertNotIn(KEY, r.stdout + r.stderr)

    def test_no_model_check_writes_without_asking_the_gateway(self):
        r = self.put(self.gateway(["big"]), f"{CLASSIFIER}=dead", flags=("--no-model-check",))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(logged(self.log), [])

    def written(self) -> list:
        return [f.name for f in (self.source, self.sh, self.fish, self.settings) if f.exists()]

    def test_a_gateway_that_does_not_answer_writes_nothing_without_a_terminal(self):  # review M1: never blindly
        r = self.put("http://127.0.0.1:9", f"{CLASSIFIER}=small")
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("model check: could not check", r.stdout)
        self.assertNotIn("model check: ok", r.stdout)
        for needle in ("--no-model-check", "terminal", "Nothing was written"):
            self.assertIn(needle, r.stderr)
        self.assertEqual(self.written(), [])
        r = self.put("http://127.0.0.1:9", f"{CLASSIFIER}=small", flags=("--no-model-check",))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_an_alias_nothing_maps_writes_nothing_without_a_terminal_and_names_the_fix(self):  # review M1
        r = self.put(self.gateway(["small"]), "ANTHROPIC_MODEL=sonnet", f"{CLASSIFIER}=small")
        self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
        self.assertIn("ANTHROPIC_DEFAULT_SONNET_MODEL", r.stderr)
        self.assertEqual(self.written(), [])

    def test_a_refused_key_writes_nothing_and_the_key_is_not_printed(self):
        r = self.put(self.gateway(["small"], key="other"), f"{CLASSIFIER}=small")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("401", r.stdout)
        self.assertNotIn(KEY, r.stdout + r.stderr)
        self.assertEqual(self.written(), [])

    def test_in_a_terminal_it_asks_and_only_a_yes_writes(self):  # review M1
        argv = [sys.executable, str(SCRIPT), "--set", "ANTHROPIC_BASE_URL=http://127.0.0.1:9", "--set",
                f"{CLASSIFIER}=small"]
        for answer in ("", "n", "maybe"):
            with self.subTest(answer=answer):
                rc, out = run_pty(argv, self.env, answer=answer, prompt="anyway? [y/N]")
                self.assertEqual(rc, 1, out)
                self.assertEqual(self.written(), [])
        rc, out = run_pty(argv, self.env, answer="y", prompt="anyway? [y/N]")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.values()[CLASSIFIER], "small")

    def test_a_dead_model_is_not_offered_as_a_question(self):
        base = self.gateway(["small"])
        rc, out = run_pty([sys.executable, str(SCRIPT), "--set", f"ANTHROPIC_BASE_URL={base}", "--set",
                           f"{CLASSIFIER}=dead"], {**self.env, "ANTHROPIC_AUTH_TOKEN": KEY}, timeout=30)
        self.assertEqual(rc, 1, out)
        self.assertNotIn("anyway? [y/N]", out)

    def test_an_api_key_goes_in_the_header_claude_code_uses(self):
        base = self.gateway(["small"])
        r = self.run_env("--set", f"ANTHROPIC_BASE_URL={base}", "--set", f"{CLASSIFIER}=small",
                         env={"ANTHROPIC_API_KEY": KEY})
        self.assertIn("model check: ok", r.stdout, r.stderr)
        self.assertEqual(logged(self.log)[-1]["auth"], "ok")


class CheckModelsTests(ModelEnvTest):
    """modelenv.check_models, which project-setup and machine-setup call before they write a model id."""

    def states(self, items, view, env=None):
        return [(f.state, f.model) for f in M.check_models(items, view, {"HOME": str(self.home), **(env or {})})]

    def claude(self, base, **view):
        return {"ANTHROPIC_BASE_URL": base, **view}

    def test_served_and_unserved(self):
        base = self.gateway(["big"], key=None)
        self.assertEqual(self.states([("a", "claude", "big"), ("b", "claude", "gone")], self.claude(base)),
                         [("ok", "big"), ("unserved", "gone")])
        self.assertEqual(len(logged(self.log)), 1)

    def test_the_1m_suffix_is_claude_codes_own(self):
        base = self.gateway(["big"], key=None)
        self.assertEqual(self.states([("a", "claude", "big[1m]")], self.claude(base)), [("ok", "big")])

    def test_an_alias_is_checked_through_its_default_model_variable_or_not_at_all(self):
        base = self.gateway(["gw-sonnet"], key=None)
        view = self.claude(base, ANTHROPIC_DEFAULT_SONNET_MODEL="gw-sonnet")
        found = M.check_models([("a", "claude", "sonnet"), ("b", "claude", "opus")], view, {"HOME": str(self.home)})
        self.assertEqual([(f.state, f.model) for f in found], [("ok", "gw-sonnet"), ("unchecked", "opus")])
        self.assertIn("ANTHROPIC_DEFAULT_OPUS_MODEL", found[1].detail)

    def test_a_wildcard_listing_proves_nothing_either_way(self):
        base = self.gateway(["vendor/*"], key=None)
        self.assertEqual(self.states([("a", "claude", "vendor/anything")], self.claude(base)),
                         [("unproven", "vendor/anything")])

    def test_a_paged_list_never_calls_a_model_unserved(self):
        base = self.gateway(["big"], key=None, more=True)
        self.assertEqual(self.states([("a", "claude", "other")], self.claude(base)), [("unchecked", "other")])

    def test_the_gate_never_lets_an_unconfirmed_model_through_without_a_yes(self):  # review M1
        base = self.gateway(["vendor/*"], key=None)
        for view, model in ((self.claude("http://127.0.0.1:9"), "m"), (self.claude(base), "sonnet"),
                            (self.claude(base), "vendor/x")):
            with self.subTest(model=model):
                found = M.check_models([("a", "claude", model)], view, {"HOME": str(self.home)})
                why = M.gate(found, None, set())
                self.assertIn("--no-model-check", why or "")
                self.assertIn("--no-model-check", M.gate(found, lambda q: False, set()) or "")
                asked, approved = [], set()
                yes = lambda q: asked.append(q) or True
                self.assertIsNone(M.gate(found, yes, approved))
                self.assertIsNone(M.gate(found, yes, approved))  # one question for the same doubt
                self.assertEqual(len(asked), 1)

    def test_the_gate_passes_what_the_gateway_lists_and_never_asks_about_what_it_does_not(self):
        ok = [M.Found("a", "x", "ok", "listed")]
        dead = [M.Found("a", "y", "unserved", "not listed")]
        asked = []
        self.assertIsNone(M.gate(ok, None, set()))
        self.assertIn("does not serve", M.gate(dead, lambda q: asked.append(q) or True, set()))
        self.assertEqual(asked, [])

    def test_a_key_is_not_sent_to_a_url_other_than_the_one_this_shell_names(self):  # review m4
        base = self.gateway(["big"])
        shell = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9", "ANTHROPIC_AUTH_TOKEN": KEY}
        for token in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
            with self.subTest(token=token):
                env = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9", token: KEY, "HOME": str(self.home)}
                (found,) = M.check_models([("a", "claude", "big")], self.claude(base), env)
                self.assertEqual(found.state, "unchecked")
                self.assertIn("another URL", found.detail)
                self.assertNotIn(KEY, found.detail)
        self.assertEqual(logged(self.log), [])  # nothing was sent at all
        for own in (base, base + "/", base + "/v1"):  # the same gateway, however it is written
            with self.subTest(own=own):
                self.assertEqual(self.states([("a", "claude", "big")], self.claude(base),
                                             {**shell, "ANTHROPIC_BASE_URL": own}), [("ok", "big")])
        self.assertEqual(self.states([("a", "claude", "big")], self.claude(base),
                                     {"ANTHROPIC_AUTH_TOKEN": KEY}), [("ok", "big")])  # the shell names no URL

    def test_no_gateway_means_nothing_to_check(self):
        for view in ({}, {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}):
            self.assertEqual(self.states([("a", "claude", "opus")], view), [])

    def test_an_opencode_model_is_checked_against_its_providers_gateway(self):
        base = self.gateway(["chat"])
        f = self.conf / "opencode" / "opencode.json"
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"provider": {"gw": {"options": {"baseURL": base + "/v1",
                                                                 "apiKey": "{env:GW_KEY}"}}}}))
        env = {"XDG_CONFIG_HOME": str(self.conf), "GW_KEY": KEY}
        self.assertEqual(self.states([("a", "opencode", "gw/chat"), ("b", "opencode", "gw/gone"),
                                      ("c", "opencode", "hosted/model")], {}, env),
                         [("ok", "chat"), ("unserved", "gone")])
        found = M.check_models([("a", "opencode", "gw/chat")], {}, {"XDG_CONFIG_HOME": str(self.conf)})
        self.assertEqual(found[0].state, "unchecked")
        self.assertIn("GW_KEY", found[0].detail)

    def test_the_view_is_the_source_over_the_users_settings_over_the_shell(self):
        self.settings.parent.mkdir()
        self.settings.write_text(json.dumps({"env": {"ANTHROPIC_MODEL": "from-settings",
                                                     "ANTHROPIC_BASE_URL": "http://settings"}}))
        self.source.parent.mkdir(parents=True)
        self.source.write_text("ANTHROPIC_BASE_URL=http://source\n")
        env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.conf), "ANTHROPIC_MODEL": "from-shell",
               "CLAUDE_CODE_SUBAGENT_MODEL": "from-shell"}
        view = M.view(env)
        self.assertEqual((view["ANTHROPIC_BASE_URL"], view["ANTHROPIC_MODEL"], view["CLAUDE_CODE_SUBAGENT_MODEL"]),
                         ("http://source", "from-settings", "from-shell"))


# --- E2: project-setup's write points -------------------------------------------------------------------------------

class ProjectSetupTests(unittest.TestCase):
    """project-setup on native Orca writes role pins into the store and a tier table beside it."""

    def setUp(self):
        self.e = NativeEnv()
        self.addCleanup(self.e.close)
        self.repo = self.e.repo()
        self.log = self.e.home / "gateway.log"
        self.base = start_gateway(self, self.e.home, ["gw-big", "gw-small", "chat"], None, None, self.log)
        self.user = self.e.home / ".claude" / "settings.json"

    def use_gateway(self, **env):
        self.user.parent.mkdir(parents=True, exist_ok=True)
        self.user.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": self.base, **env}}))

    def tiers(self, *rows) -> Path:
        f = self.e.home / "tiers.tsv"
        f.write_text("tier\tharness\tmodel\tpermission\n" + "".join("\t".join(r) + "\n" for r in rows))
        return f

    def setup(self, *args):
        return self.e.run(str(self.repo), "--no-start", *args)

    def pins(self) -> dict:
        f = self.e.store(self.repo) / "settings.json"
        return json.loads(f.read_text()).get("models", {}) if f.exists() else {}

    def test_a_pin_the_gateway_does_not_serve_is_not_written(self):
        self.use_gateway()
        r = self.setup("--coordinator", "claude/gw-dead", "--reviewer", "claude/gw-big")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        for needle in ("gw-dead", "gw-big, gw-small", "--no-model-check"):
            self.assertIn(needle, r.stderr)
        self.assertEqual(self.pins(), {})

    def test_a_tier_row_the_gateway_does_not_serve_is_not_written(self):
        self.use_gateway()
        pins = ("--coordinator", "claude/gw-big", "--reviewer", "claude/gw-big")
        r = self.setup(*pins, "--tiers", str(self.tiers(("standard", "claude", "gw-dead", "inherit"))))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("gw-dead", r.stderr)
        self.assertFalse((self.e.store(self.repo) / "lanes.tsv").exists())

    def test_served_models_are_written_with_one_request(self):
        self.use_gateway()
        r = self.setup("--coordinator", "claude/gw-big", "--reviewer", "claude/gw-small",
                       "--tiers", str(self.tiers(("standard", "claude", "gw-small", "inherit"))))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("model check: ok", r.stdout)
        self.assertEqual(self.pins()["coordinator"], "claude/gw-big")
        self.assertEqual(len(logged(self.log)), 1, logged(self.log))

    def test_default_aliases_without_a_default_model_variable_are_not_written_blindly(self):  # review M1
        self.use_gateway()
        r = self.setup()
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("model check: could not check", r.stdout)
        for needle in ("ANTHROPIC_DEFAULT_OPUS_MODEL", "--no-model-check"):
            self.assertIn(needle, r.stderr)
        self.assertEqual(self.pins(), {})

    def test_a_gateway_that_is_down_writes_no_pin_without_a_terminal(self):  # review M1
        self.base = "http://127.0.0.1:9"
        self.use_gateway()
        r = self.setup("--coordinator", "claude/gw-big", "--reviewer", "claude/gw-big")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.pins(), {})

    def test_in_a_terminal_it_asks_before_it_writes_unconfirmed_models(self):  # review M1
        self.base = "http://127.0.0.1:9"
        self.use_gateway()
        argv = [sys.executable, str(SETUP), str(self.repo), "--no-start", "--skip-adapt", "--coordinator",
                "claude/gw-big", "--reviewer", "claude/gw-big", "--tiers",
                str(self.tiers(("standard", "claude", "gw-big", "inherit")))]
        rc, out = run_pty(argv, self.e.env, answer="n", prompt="anyway? [y/N]")
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.pins(), {})
        rc, out = run_pty(argv, self.e.env, answer="y", prompt="anyway? [y/N]")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.pins()["coordinator"], "claude/gw-big")
        self.assertEqual(out.count("anyway? [y/N]"), 1, out)  # the same doubt is asked once in a run
        self.assertTrue((self.e.store(self.repo) / "lanes.tsv").exists())

    def test_an_opencode_row_is_checked_with_its_providers_gateway(self):
        f = self.e.home / ".config" / "opencode" / "opencode.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"provider": {"litellm": {"options": {"baseURL": self.base + "/v1"}}}}))
        r = self.setup("--tiers", str(self.tiers(("standard", "opencode", "litellm/gone", "-"))))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("gone", r.stderr)
        r = self.setup("--tiers", str(self.tiers(("standard", "opencode", "litellm/chat", "-"))))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_no_model_check_writes_it(self):
        self.use_gateway()
        r = self.setup("--coordinator", "claude/gw-dead", "--no-model-check")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.pins()["coordinator"], "claude/gw-dead")
        self.assertEqual(logged(self.log), [])

    def test_a_rerun_that_writes_no_model_asks_nothing(self):
        r = self.setup("--coordinator", "claude/gw-big", "--reviewer", "claude/gw-big",
                       "--tiers", str(self.tiers(("standard", "claude", "gw-small", "inherit"))))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.use_gateway()
        for args in ((), ("--tiers", str(self.e.home / "tiers.tsv")), ("--coordinator", "claude/gw-big")):
            self.assertEqual(self.setup(*args).returncode, 0, args)
        self.assertEqual(logged(self.log), [])

    def test_a_repos_own_settings_never_choose_where_the_key_goes(self):
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": self.base}}))
        r = self.e.run(str(self.repo), "--no-start", "--coordinator", "claude/gw-dead",
                       env_extra={"ANTHROPIC_AUTH_TOKEN": KEY})
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(logged(self.log), [])


if __name__ == "__main__":
    unittest.main()
