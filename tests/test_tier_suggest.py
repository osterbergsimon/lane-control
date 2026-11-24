"""project-setup --suggest-tiers: a tier table suggested from an OpenAI-compatible gateway's model list (lc-48m).
The gateway is tests/stubs/fake_gateway.py on 127.0.0.1; HOME is a temp dir, so OpenCode's config is the test's own."""
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_project_setup import ROOT, SCRIPT, Env, load  # noqa: E402

sys.path.insert(0, str(ROOT / "core"))
import tiersuggest as T  # noqa: E402

GATEWAY = ROOT / "tests" / "stubs" / "fake_gateway.py"
KEY = "sentinel-gateway-key-4711"


class Tty(io.StringIO):
    def isatty(self):
        return True


class PickTests(unittest.TestCase):
    def test_name_hints_pick_each_tier(self):
        models = [("chat", "anthropic/claude-sonnet-4-5"), ("big", "anthropic/claude-opus-4-1"),
                  ("quick", "openai/gpt-5-mini"), ("g", "gemini/gemini-2.5-pro")]
        picks = T.suggest(models)
        self.assertEqual(picks["swift"], ("quick", "openai/gpt-5-mini"))
        self.assertEqual(picks["standard"], ("chat", "anthropic/claude-sonnet-4-5"))
        self.assertEqual(picks["deep"], ("big", "anthropic/claude-opus-4-1"))
        self.assertEqual(T.kind("g", "gemini/gemini-2.5-pro"), "deep")  # gemini is not "mini"

    def test_aliases_without_hints_fill_standard_and_deep_and_leave_swift_none(self):
        picks = T.suggest([("chat", "chat"), ("code", "code")])
        self.assertEqual(picks, {"swift": None, "standard": ("code", "code"), "deep": ("code", "code")})
        text = T.table("litellm", picks)
        rows = [l.split("\t") for l in text.splitlines() if l and not l.startswith("#")]
        self.assertEqual(rows, [["tier", "harness", "model", "permission"], ["swift", "none", "-", "-"],
                                ["standard", "opencode", "litellm/code", "-"], ["deep", "opencode", "litellm/code", "-"],
                                ["top", "none", "-", "-"]])

    def test_parameter_counts_are_hints_too(self):
        self.assertEqual(T.kind("a", "ollama/llama3.1:8b"), "swift")
        self.assertEqual(T.kind("b", "openai/llama-3.3-70b"), "deep")
        self.assertEqual(T.kind("c", "openai/qwen3-32b"), "standard")

    def test_the_suggestion_is_a_table_lane_tier_reads(self):
        f = Path(self.id().replace(".", "_") + ".tsv")
        self.addCleanup(lambda: f.unlink(missing_ok=True))
        f.write_text(T.table("gw", T.suggest([("chat", "x/y")])))
        r = subprocess.run(["bash", str(ROOT / "core" / "scripts" / "lane-tier"), str(f), "deep"],
                           capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, "harness=opencode model=gw/chat permission=-\n"), r.stderr)


class OpenCodeConfigTests(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.addCleanup(self.e.close)
        self.conf = self.e.home / ".config" / "opencode" / "opencode.jsonc"
        self.conf.parent.mkdir(parents=True)

    def test_base_url_and_key_reference_come_from_the_provider_entry(self):
        self.conf.write_text('{\n  // the owner\'s gateway\n  "provider": {"gw": {"npm": "@ai-sdk/openai-compatible",\n'
                             '    "options": {"baseURL": "http://127.0.0.1:9/v1", /* a note */ "apiKey": "{env:GW_KEY}"},},},\n}\n')
        base, env, where = T.opencode_provider("gw", {"HOME": str(self.e.home)})
        self.assertEqual((base, env, where), ("http://127.0.0.1:9/v1", "GW_KEY", str(self.conf)))
        self.assertEqual(T.opencode_provider("other", {"HOME": str(self.e.home)})[:2], (None, None))

    def test_a_key_written_in_the_config_is_never_used(self):
        self.conf.write_text(json.dumps({"provider": {"gw": {"options": {"baseURL": "http://127.0.0.1:9",
                                                                         "apiKey": KEY}}}}))
        self.assertEqual(T.opencode_provider("gw", {"HOME": str(self.e.home)})[:2], ("http://127.0.0.1:9", None))

    def test_auth_files_are_never_read(self):
        src = (ROOT / "core" / "tiersuggest.py").read_text()
        self.assertNotIn("auth.json", src)
        self.assertNotIn("subprocess", src)  # the key never reaches an argv
        self.assertEqual([p.name for p in T.opencode_config_paths({"HOME": "/h"})],
                         ["opencode.json", "opencode.jsonc", "config.json"])


class SuggestRunTests(unittest.TestCase):
    """project-setup <repo> --suggest-tiers <provider> against the fake gateway."""

    def setUp(self):
        self.e = Env()
        self.addCleanup(self.e.close)
        self.repo = self.e.repo()
        self.table = self.e.store(self.repo) / "lanes.tsv"
        self.log = self.e.home / "gateway.log"

    def gateway(self, models, info=None, key=KEY):
        spec = self.e.home / "gateway.json"
        spec.write_text(json.dumps({"key": key, "models": models, "info": info}))
        p = subprocess.Popen([sys.executable, str(GATEWAY), str(spec), str(self.log)], stdout=subprocess.PIPE, text=True)
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        return f"http://127.0.0.1:{p.stdout.readline().strip()}"

    def opencode_config(self, base, key_ref="{env:GW_KEY}"):
        f = self.e.home / ".config" / "opencode" / "opencode.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"provider": {"litellm": {"npm": "@ai-sdk/openai-compatible",
                                                          "options": {"baseURL": base + "/v1", "apiKey": key_ref}}}}))

    def requests(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def run_setup(self, *args, env_extra=None):
        return subprocess.run([sys.executable, str(SCRIPT), str(self.repo), *args],
                              env={**self.e.env, **(env_extra or {})}, capture_output=True, text=True)

    def in_terminal(self, answer, *args, env_extra=None):
        m = load()
        out, err = Tty(), io.StringIO()
        with mock.patch.dict(os.environ, {**self.e.env, **(env_extra or {})}, clear=True), \
                mock.patch("sys.stdin", Tty()), mock.patch("builtins.input", return_value=answer) as ask, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = m.main([str(self.repo), *args])
        return status, out.getvalue() + err.getvalue(), ask

    LITELLM = (["chat", "code", "quick"], {"chat": "anthropic/claude-sonnet-4-5", "code": "anthropic/claude-opus-4-1",
                                           "quick": "openai/gpt-5-mini"})

    def test_without_a_terminal_it_shows_the_suggestion_and_writes_nothing(self):
        self.opencode_config(self.gateway(*self.LITELLM))
        r = self.run_setup("--suggest-tiers", "litellm", env_extra={"GW_KEY": KEY})
        self.assertEqual(r.returncode, 3, r.stderr + r.stdout)
        for needle in ("standard\topencode\tlitellm/chat\t-", "deep\topencode\tlitellm/code\t-",
                       "swift\topencode\tlitellm/quick\t-", "anthropic/claude-opus-4-1", "openai/gpt-5-mini",
                       "--tiers <file>"):
            self.assertIn(needle, r.stdout, needle)
        self.assertFalse(self.table.exists())
        self.assertNotIn(KEY, r.stdout + r.stderr)
        self.assertEqual([(q["path"], q["auth"]) for q in self.requests()], [("/v1/models", "ok"), ("/model/info", "ok")])

    def test_yes_writes_the_suggested_table_and_nothing_else(self):
        self.opencode_config(self.gateway(*self.LITELLM))
        status, out, ask = self.in_terminal("y", "--suggest-tiers", "litellm", env_extra={"GW_KEY": KEY})
        self.assertEqual(status, 0, out)
        ask.assert_called_once()
        text = self.table.read_text()
        self.assertIn("deep\topencode\tlitellm/code\t-\n", text)
        self.assertEqual([p.name for p in self.e.store(self.repo).iterdir()], ["lanes.tsv"])
        self.assertNotIn(KEY, out)
        self.assertEqual(self.e.calls(), [])  # no orca, cotal or agent

    def test_no_writes_nothing(self):
        self.opencode_config(self.gateway(*self.LITELLM))
        status, out, _ = self.in_terminal("n", "--suggest-tiers", "litellm", env_extra={"GW_KEY": KEY})
        self.assertEqual(status, 3, out)
        self.assertFalse(self.table.exists())

    def test_yes_replaces_an_earlier_table_and_a_later_run_keeps_it(self):
        self.table.parent.mkdir(parents=True)
        self.table.write_text("tier\tharness\tmodel\tpermission\nstandard\tclaude\tsonnet\tinherit\n")
        self.opencode_config(self.gateway(*self.LITELLM))
        status, out, ask = self.in_terminal("yes", "--suggest-tiers", "litellm", env_extra={"GW_KEY": KEY})
        self.assertEqual(status, 0, out)
        self.assertIn("replaces", ask.call_args[0][0])
        mine = self.table.read_text()
        self.assertIn("litellm/chat", mine)
        r = subprocess.run([sys.executable, str(SCRIPT), str(self.repo), "--skip-adapt", "--no-start",
                            "--orchestrator", "orca-cotal"], env=self.e.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.table.read_text(), mine)

    def test_flags_give_the_url_and_key_and_a_gateway_without_model_info_is_fine(self):
        base = self.gateway(["chat", "code"], info=None)
        r = self.run_setup("--suggest-tiers", "gw", "--gateway", base, "--gateway-key-env", "MY_KEY",
                           env_extra={"MY_KEY": KEY})
        self.assertEqual(r.returncode, 3, r.stderr + r.stdout)
        self.assertIn("standard\topencode\tgw/code\t-", r.stdout)
        self.assertIn("swift\tnone\t-\t-", r.stdout)
        self.assertEqual([q["path"] for q in self.requests()], ["/v1/models", "/model/info"])
        self.assertNotIn("model/info", r.stderr)  # its absence is silent

    def test_a_named_key_variable_that_is_unset_stops_before_any_request(self):
        self.opencode_config(self.gateway(*self.LITELLM))
        r = self.run_setup("--suggest-tiers", "litellm")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("GW_KEY", r.stderr)
        self.assertEqual(self.requests(), [])

    def test_a_refused_key_is_said_without_the_key(self):
        self.opencode_config(self.gateway(*self.LITELLM))
        r = self.run_setup("--suggest-tiers", "litellm", env_extra={"GW_KEY": "not-the-key"})
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("HTTP 401", r.stderr)
        self.assertNotIn("not-the-key", r.stdout + r.stderr)
        self.assertFalse(self.table.exists())

    def test_no_url_anywhere_is_a_usage_error(self):
        r = self.run_setup("--suggest-tiers", "litellm")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--gateway", r.stderr)

    def test_gateway_flags_need_suggest_tiers(self):
        r = self.run_setup("--gateway", "http://127.0.0.1:9")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--suggest-tiers", r.stderr)


if __name__ == "__main__":
    unittest.main()
