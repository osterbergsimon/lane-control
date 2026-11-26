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


def start_gateway(case, home, models, info=None, key=KEY, log=None, **extra):
    """tests/stubs/fake_gateway.py on 127.0.0.1; its base URL. log: where it writes each request."""
    log = log or home / "gateway.log"
    spec = home / f"gateway-{abs(hash((tuple(models), str(log), str(extra))))}.json"
    spec.write_text(json.dumps({"key": key, "models": models, "info": info, **extra}))
    p = subprocess.Popen([sys.executable, str(GATEWAY), str(spec), str(log)], stdout=subprocess.PIPE, text=True)
    case.addCleanup(p.stdout.close)
    case.addCleanup(p.wait)
    case.addCleanup(p.kill)
    return f"http://127.0.0.1:{p.stdout.readline().strip()}"


def logged(log):
    return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


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


class KeyNeverPrintedTests(unittest.TestCase):
    def test_a_key_a_header_cannot_carry_is_never_quoted_in_the_error(self):
        # http.client's own error quotes the whole header value; a key pasted with a line break must not reach it
        for bad in (KEY + "\nX: y", KEY + "\u00e9\u4e2d"):
            with self.subTest(bad=len(bad)):
                with self.assertRaises(T.GatewayError) as caught:
                    T.fetch_models("http://127.0.0.1:9", bad)
                self.assertNotIn(KEY, str(caught.exception))


class OpenCodeConfigTests(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.addCleanup(self.e.close)
        self.conf = self.e.home / ".config" / "opencode" / "opencode.jsonc"
        self.conf.parent.mkdir(parents=True)

    def test_base_url_and_key_reference_come_from_the_provider_entry(self):
        self.conf.write_text('{\n  // the owner\'s gateway\n  "provider": {"gw": {"npm": "@ai-sdk/openai-compatible",\n'
                             '    "options": {"baseURL": "http://127.0.0.1:9/v1", /* a note */ "apiKey": "{env:GW_KEY}"},},},\n}\n')
        got = T.opencode_provider("gw", {"HOME": str(self.e.home)})
        self.assertEqual((got["base"], got["key_env"], got["where"], got["literal_key"], got["models"]),
                         ("http://127.0.0.1:9/v1", "GW_KEY", str(self.conf), False, None))
        other = T.opencode_provider("other", {"HOME": str(self.e.home)})
        self.assertEqual((other["base"], other["key_env"]), (None, None))

    def test_a_key_written_in_the_config_is_never_used(self):
        self.conf.write_text(json.dumps({"provider": {"gw": {"options": {"baseURL": "http://127.0.0.1:9",
                                                                         "apiKey": KEY}}}}))
        got = T.opencode_provider("gw", {"HOME": str(self.e.home)})
        self.assertEqual((got["base"], got["key_env"], got["literal_key"]), ("http://127.0.0.1:9", None, True))

    def test_the_models_opencode_knows_are_read(self):
        self.conf.write_text(json.dumps({"provider": {"gw": {"options": {"baseURL": "http://127.0.0.1:9"},
                                                             "models": {"chat": {}, "code": {"name": "Code"}}}}}))
        self.assertEqual(T.opencode_provider("gw", {"HOME": str(self.e.home)})["models"], ["chat", "code"])

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

    def gateway(self, models, info=None, key=KEY, **extra):
        return start_gateway(self, self.e.home, models, info, key, self.log, **extra)

    def opencode_config(self, base, key_ref="{env:GW_KEY}", models=None):
        f = self.e.home / ".config" / "opencode" / "opencode.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        entry = {"npm": "@ai-sdk/openai-compatible", "options": {"baseURL": base + "/v1", "apiKey": key_ref}}
        if models is not None:
            entry["models"] = {m: {} for m in models}
        f.write_text(json.dumps({"provider": {"litellm": entry}}))

    def install_lane_tier(self, text=None):
        d = self.e.home / ".agents" / "bin"
        d.mkdir(parents=True, exist_ok=True)
        (d / "lane-tier").write_text(text if text is not None else (ROOT / "core" / "scripts" / "lane-tier").read_text())

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

    def store_gateway(self, url, key_env=None):
        """The project's stored onboarding gateway, as project-setup --gateway-url keeps it."""
        f = self.e.store(self.repo) / "settings.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"root": str(self.repo), "gateway": {"url": url, **({"key_env": key_env} if key_env
                                                                                     else {})}}))

    def test_one_gateway_flag_for_onboarding_and_the_suggestion(self):
        # M10: --gateway-url is read by --suggest-tiers too; OpenCode's config points elsewhere and gets nothing
        self.opencode_config("http://127.0.0.1:9")
        base = self.gateway(["chat", "code"], info=None)
        for flag in ("--gateway-url", "--gateway"):  # --gateway: the old name, kept hidden
            with self.subTest(flag=flag):
                self.log.unlink(missing_ok=True)
                r = self.run_setup("--suggest-tiers", "litellm", flag, base, "--gateway-key-env", "MY_KEY",
                                   env_extra={"MY_KEY": KEY})
                self.assertEqual(r.returncode, 3, r.stderr + r.stdout)
                self.assertIn(f"gateway: using {base}, key from $MY_KEY", r.stdout)
                self.assertEqual([q["path"] for q in self.requests()], ["/v1/models", "/model/info"])
        self.assertNotIn("--gateway", subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True,
                                                     text=True).stdout.replace("--gateway-url", "")
                         .replace("--gateway-key-env", ""))

    def test_the_stored_gateway_comes_before_opencodes_config_and_keeps_its_key(self):
        # M10: the flag, then the project's stored gateway (with the key variable stored for it), then OpenCode's
        self.opencode_config("http://127.0.0.1:9", key_ref="{env:OTHER_KEY}")
        base = self.gateway(*self.LITELLM)
        self.store_gateway(base, "MY_KEY")
        r = self.run_setup("--suggest-tiers", "litellm", env_extra={"MY_KEY": KEY, "OTHER_KEY": "other-secret"})
        self.assertEqual(r.returncode, 3, r.stderr + r.stdout)
        self.assertIn(f"gateway: using {base}, key from $MY_KEY", r.stdout)
        self.assertTrue(self.requests())

    def test_a_key_variable_goes_only_with_the_url_it_was_given_for(self):
        # M9 for the suggestion: a --gateway-url that is neither the stored nor OpenCode's takes no key along
        self.opencode_config("http://127.0.0.1:9", key_ref="{env:GW_KEY}")
        self.store_gateway("http://127.0.0.1:8", "MY_KEY")
        base = self.gateway(*self.LITELLM, key=None)
        r = self.run_setup("--suggest-tiers", "litellm", "--gateway-url", base,
                           env_extra={"MY_KEY": KEY, "GW_KEY": KEY})
        self.assertEqual(r.returncode, 3, r.stderr + r.stdout)
        self.assertIn(f"gateway: using {base}, no key", r.stdout)
        self.assertTrue(self.requests())
        self.assertEqual({q["auth"] for q in self.requests()}, {"missing"})

    def test_a_vendor_or_unclear_url_is_refused_for_the_suggestion_too(self):
        for url in ("https://api.openai.com/v1", "https://api.openai.com./v1", "http://127.0.0.1:9/v1?key=x"):
            with self.subTest(url=url):
                r = self.run_setup("--suggest-tiers", "litellm", "--gateway-url", url)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertEqual(self.requests(), [])


    # --- review of a088a32 ----------------------------------------------------------------------------------------
    def test_an_old_installed_lane_tier_says_to_refresh_first(self):
        # M1: a host set up earlier still has the lane-tier, skills and personas that refuse OpenCode tiers
        self.opencode_config(self.gateway(*self.LITELLM))
        for installed in (None, "old lane-tier with exit 4\n"):
            with self.subTest(installed=installed):
                if installed:
                    self.install_lane_tier(installed)
                r = self.run_setup("--suggest-tiers", "litellm", env_extra={"GW_KEY": KEY})
                self.assertEqual(r.returncode, 3, r.stderr + r.stdout)
                self.assertIn("--refresh", r.stderr)
                self.assertIn("restart", r.stderr)
                self.assertIn("lane-tier", r.stderr)
        self.install_lane_tier()
        r = self.run_setup("--suggest-tiers", "litellm", env_extra={"GW_KEY": KEY})
        self.assertNotIn("--refresh", r.stderr)

    def test_non_chat_models_are_left_out_and_said(self):
        # m3: LiteLLM's model_info.mode
        info = {"chat": {"model": "anthropic/claude-sonnet-4-5", "mode": "chat"},
                "embed-small": {"model": "openai/text-embedding-3-small", "mode": "embedding"},
                "rerank-lite": {"model": "cohere/rerank-v3.5", "mode": "rerank"},
                "quick": {"model": "openai/gpt-5-mini", "mode": "responses"}}
        self.opencode_config(self.gateway(list(info), info))
        r = self.run_setup("--suggest-tiers", "litellm", env_extra={"GW_KEY": KEY})
        self.assertEqual(r.returncode, 3, r.stderr + r.stdout)
        self.assertIn("swift\topencode\tlitellm/quick\t-", r.stdout)
        self.assertNotIn("litellm/embed-small", r.stdout)
        self.assertNotIn("litellm/rerank-lite", r.stdout)
        self.assertIn("embed-small (an embedding model)", r.stdout)

    def test_aliases_opencode_does_not_list_are_left_out_and_said(self):
        # m3: OpenCode starts only the aliases under provider.<id>.models
        self.opencode_config(self.gateway(*self.LITELLM), models=["chat", "quick"])
        r = self.run_setup("--suggest-tiers", "litellm", env_extra={"GW_KEY": KEY})
        self.assertEqual(r.returncode, 3, r.stderr + r.stdout)
        self.assertNotIn("litellm/code", r.stdout)
        self.assertIn("deep\topencode\tlitellm/chat\t-", r.stdout)
        self.assertIn("code (not in OpenCode's config: provider.litellm.models)", r.stdout)

    def test_a_literal_key_in_the_config_is_named_and_nothing_is_asked(self):
        # m4
        self.opencode_config(self.gateway(*self.LITELLM), key_ref=KEY)
        r = self.run_setup("--suggest-tiers", "litellm")
        self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
        self.assertIn("{env:NAME}", r.stderr)
        self.assertIn("--gateway-key-env", r.stderr)
        self.assertNotIn(KEY, r.stdout + r.stderr)
        self.assertEqual(self.requests(), [])

    def test_a_key_variable_name_that_is_not_one_is_refused_unechoed(self):
        # m4: a key pasted in place of the name is not printed back
        r = self.run_setup("--suggest-tiers", "gw", "--gateway", "http://127.0.0.1:9", "--gateway-key-env", KEY + "!")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn(KEY, r.stdout + r.stderr)
        self.assertIn("--gateway-key-env", r.stderr)


class FetchTests(unittest.TestCase):
    """core/tiersuggest.fetch_models in this process: redirects, proxies, replies that are not HTTP, deadlines."""

    def setUp(self):
        self.e = Env()
        self.addCleanup(self.e.close)

    def test_a_redirect_is_never_followed_so_the_key_stays_with_the_gateway(self):
        # m1
        other_log = self.e.home / "other.log"
        other = start_gateway(self, self.e.home, ["chat"], log=other_log)
        base = start_gateway(self, self.e.home, ["chat"], redirect=other.replace("127.0.0.1", "localhost"))
        with self.assertRaises(T.GatewayError) as c:
            T.fetch_models(base, KEY)
        self.assertIn("redirect", str(c.exception))
        self.assertNotIn(KEY, str(c.exception))
        self.assertEqual(logged(other_log), [])

    def test_a_proxy_in_the_environment_is_never_used(self):
        # m1: the key would go to the proxy, in clear for http://
        proxy_log = self.e.home / "proxy.log"
        proxy = start_gateway(self, self.e.home, ["chat"], log=proxy_log, key=None)
        base = start_gateway(self, self.e.home, ["chat"])
        with mock.patch.dict(os.environ, {"http_proxy": proxy, "HTTP_PROXY": proxy, "no_proxy": "", "NO_PROXY": ""}):
            self.assertEqual(T.fetch_models(base, KEY)[0][:2], ("chat", "chat"))
        self.assertEqual(logged(proxy_log), [])

    def test_a_reply_that_is_not_http_is_a_plain_error(self):
        # m2
        base = start_gateway(self, self.e.home, ["chat"], garbage=True)
        with self.assertRaises(T.GatewayError) as c:
            T.fetch_models(base, KEY)
        self.assertIn("not an HTTP reply", str(c.exception))

    def test_a_gateway_that_drips_hits_the_wall_clock_deadline(self):
        # m2: the socket timeout is per read; the deadline is for the whole call
        import time
        base = start_gateway(self, self.e.home, ["chat"], drip=True)
        with mock.patch.object(T, "DEADLINE", 2):
            t0 = time.monotonic()
            with self.assertRaises(T.GatewayError) as c:
                T.fetch_models(base, KEY)
        self.assertLess(time.monotonic() - t0, 6)
        self.assertIn("no full answer within 2 s", str(c.exception))

    def test_model_info_has_a_shorter_deadline_and_its_absence_is_silent(self):
        self.assertLess(T.INFO_DEADLINE, T.DEADLINE)


if __name__ == "__main__":
    unittest.main()
