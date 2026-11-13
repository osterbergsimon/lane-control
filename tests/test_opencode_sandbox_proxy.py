"""opencode-sandbox.sh behind an HTTP(S) proxy: blocks of it run on their own with stubs; no real sandbox, nix or network.

A work site reaches its AI gateway only through a proxy, often on a private address that the sandbox's nftables rules
refuse. With a proxy set, the sandbox passes the proxy variables in and allows exactly the proxy's address and port; with
none set, the rule set and the environment are what they were. Addresses are documentation ones (RFC 5737, RFC 3849).
"""
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SANDBOX = ROOT / "scripts" / "opencode-sandbox.sh"
TIMEOUT = 20
PROXY_NAMES = ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy")

# getent: proxy.example has one IPv4 and one IPv6 address; localhost is loopback; anything else is unknown
GETENT = """#!/usr/bin/env bash
[ "$1" = ahosts ] || exit 2
case $2 in
  proxy.example) printf '192.0.2.10      STREAM proxy.example\\n192.0.2.10      DGRAM\\n2001:db8::10    STREAM\\n' ;;
  localhost) printf '::1             STREAM localhost\\n127.0.0.1       STREAM\\n' ;;
  *) exit 2 ;;
esac
"""
# ip: this host's global addresses, as `ip -o addr show scope global` prints them
IP = """#!/usr/bin/env bash
case " $* " in
  *" -4 "*) echo '2: eth0    inet 198.51.100.7/24 brd 198.51.100.255 scope global eth0' ;;
  *" -6 "*) echo '2: eth0    inet6 2001:db8:1::7/64 scope global' ;;
esac
"""
CURL = """#!/usr/bin/env bash
printf '%s\\n' "$@" >"$STUB_DIR/curl.args"
cat >/dev/null
printf 200
"""
# the rule set of a lane with no proxy, as it was before proxies were supported (with the stub ip above)
NFT_NO_PROXY = """table inet lane_out {
chain out {
type filter hook output priority 0; policy accept;
oifname "lo" accept
ip daddr { 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 169.254.0.0/16, 172.16.0.0/12, 192.0.0.0/24, 192.168.0.0/16, 198.18.0.0/15, 224.0.0.0/4, 240.0.0.0/4 } reject
ip6 daddr { ::/128, fc00::/7, fe80::/10, ff00::/8 } reject
ip daddr 198.51.100.7 reject
ip6 daddr 2001:db8:1::7 reject
}
}
"""


def block(start, end):
    text = SANDBOX.read_text()
    assert start in text, start
    rest = text.split(start, 1)[1]
    assert end in rest, end
    return start + rest.split(end, 1)[0]


DIE = 'die() { echo "opencode-sandbox: $*" >&2; exit 2; }\n'
PROXY_BLOCK = ("# --- the proxy", "keyurl=")
SHOW = 'printf "http=%s\\nhttps=%s\\nno=%s\\nports=%s\\nrules<\\n%s>\\n" "$p_http" "$p_https" "$p_no" "$proxy_ports" "$proxy_rules"\n'


class SandboxProxy(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.stubs = self.dir / "stubs"
        self.stubs.mkdir()
        self.lane = self.dir / "lane"
        self.lane.mkdir()
        for name, text in (("getent", GETENT), ("ip", IP), ("curl", CURL)):
            f = self.stubs / name
            f.write_text(text)
            f.chmod(f.stat().st_mode | stat.S_IXUSR)
        self.env = {"PATH": f"{self.stubs}:/usr/bin:/bin:/run/current-system/sw/bin", "HOME": str(self.dir),
                    "STUB_DIR": str(self.stubs)}

    def run_bash(self, script, **env):
        return subprocess.run(["bash", "-c", "set -euo pipefail\n" + DIE + script], env=dict(self.env, **env),
                              capture_output=True, text=True, timeout=TIMEOUT)

    def proxy(self, seat="", sport="", **env):
        return self.run_bash(f"seat={seat} sport={sport}\n" + block(*PROXY_BLOCK) + SHOW, **env)

    def fields(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        head, rules = r.stdout.split("rules<\n", 1)
        out = dict(l.split("=", 1) for l in head.splitlines())
        out["rules"] = rules.rsplit(">\n", 1)[0]
        return out

    def network(self, seat="", extra_ports="", **env):
        """The proxy block, then the network block: pasta's port options and the nft rule set."""
        script = f"seat={seat} sport=4100 bport=4222 extra_ports={extra_ports} lane={self.lane} ipcmd=ip\n"
        script += block(*PROXY_BLOCK)
        script += block("# the network: ", "systemd-run --user --scope")
        script += 'printf "net=%s\\n" "${net[*]}"\n'
        r = self.run_bash(script, **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip(), (self.lane / "net.nft").read_text()

    def env_args(self, **env):
        """The environment allow-list block of a Rove lane: bwrap's --setenv arguments, one per line."""
        script = f"seat= sport=\n" + block(*PROXY_BLOCK)
        script += f"args=()\nro() {{ :; }}\nrw() {{ :; }}\ntask= lane={self.lane} oc=/x/bin/opencode devenv= work={self.dir}\n"
        script += block("# environment: an allow-list", "# --- the machine-wide heavy-work gate")
        script += 'printf "%s\\n" "${args[@]}"\n'
        r = self.run_bash(script, **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    @staticmethod
    def setenvs(args):
        return {args[i + 1]: args[i + 2] for i in range(len(args) - 2) if args[i] == "--setenv"}

    # --- no proxy: nothing changes ---

    def test_no_proxy_keeps_the_rule_set_and_the_ports(self):
        net, nft = self.network()
        self.assertEqual(net, "net=-t none -T none")
        self.assertEqual(nft, NFT_NO_PROXY)
        net, nft = self.network(seat="1", extra_ports="5432")
        self.assertEqual(net, "net=-t 127.0.0.1/4100 -T 4222,5432")
        self.assertEqual(nft, NFT_NO_PROXY)

    def test_no_proxy_puts_no_proxy_variable_in_the_sandbox(self):
        names = self.setenvs(self.env_args())
        self.assertFalse(set(names) & set(PROXY_NAMES), names)
        # a NO_PROXY alone is no proxy: it is not passed either
        self.assertFalse(set(self.setenvs(self.env_args(NO_PROXY="corp.example"))) & set(PROXY_NAMES))

    # --- a proxy: its variables go in, both cases, with the seat's own addresses kept off it ---

    def test_the_proxy_variables_reach_the_sandbox_in_both_cases(self):
        env = self.setenvs(self.env_args(HTTPS_PROXY="http://192.0.2.10:3128", http_proxy="http://192.0.2.10:3128",
                                         no_proxy="gateway.example, .corp.example"))
        for v in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
            self.assertEqual(env[v], "http://192.0.2.10:3128", v)
        for v in ("NO_PROXY", "no_proxy"):
            self.assertEqual(env[v], "gateway.example,.corp.example,127.0.0.1,localhost,::1", v)

    def test_only_the_scheme_that_is_set_is_passed(self):
        env = self.setenvs(self.env_args(HTTPS_PROXY="proxy.example:3128"))
        self.assertEqual((env["HTTPS_PROXY"], env["https_proxy"]), ("proxy.example:3128",) * 2)
        self.assertNotIn("HTTP_PROXY", env)
        self.assertNotIn("http_proxy", env)
        self.assertEqual(env["NO_PROXY"], "127.0.0.1,localhost,::1")

    def test_no_proxy_entries_are_merged_from_both_cases_once(self):
        f = self.fields(self.proxy(HTTPS_PROXY="http://192.0.2.10:3128", NO_PROXY="a.example,localhost",
                                   no_proxy="a.example,b.example"))
        self.assertEqual(f["no"], "a.example,localhost,b.example,127.0.0.1,::1")

    # --- a proxy: exactly its address and port open ---

    def test_a_private_proxy_address_and_port_are_allowed_and_nothing_else(self):
        net, nft = self.network(HTTPS_PROXY="http://192.0.2.10:3128", HTTP_PROXY="http://192.0.2.10:3128")
        self.assertEqual(net, "net=-t none -T none")
        rule = "ip daddr 192.0.2.10 tcp dport 3128 accept\n"
        self.assertEqual(nft.count(rule), 1)
        # before every reject, and the rejects themselves untouched
        self.assertLess(nft.index(rule), nft.index("reject"))
        self.assertEqual(nft.replace(rule, ""), NFT_NO_PROXY)

    def test_a_proxy_hostname_is_resolved_at_launch_to_each_of_its_addresses(self):
        net, nft = self.network(HTTPS_PROXY="http://proxy.example:3128")
        added = "ip daddr 192.0.2.10 tcp dport 3128 accept\nip6 daddr 2001:db8::10 tcp dport 3128 accept\n"
        self.assertIn('oifname "lo" accept\n' + added, nft)
        self.assertEqual(nft.replace(added, ""), NFT_NO_PROXY)

    def test_an_ipv6_literal_proxy(self):
        f = self.fields(self.proxy(https_proxy="http://[2001:db8::10]:8080/"))
        self.assertEqual(f["rules"], "ip6 daddr 2001:db8::10 tcp dport 8080 accept")

    def test_two_proxies_open_both_and_no_more(self):
        f = self.fields(self.proxy(HTTP_PROXY="http://192.0.2.10:3128", HTTPS_PROXY="https://192.0.2.11:8443"))
        self.assertEqual(f["rules"], "ip daddr 192.0.2.10 tcp dport 3128 accept\nip daddr 192.0.2.11 tcp dport 8443 accept")

    def test_a_proxy_on_host_loopback_is_forwarded_like_the_broker(self):
        net, nft = self.network(HTTPS_PROXY="http://127.0.0.1:3128")
        self.assertEqual(net, "net=-t none -T 3128")
        self.assertEqual(nft, NFT_NO_PROXY)
        net, nft = self.network(seat="1", extra_ports="5432", HTTPS_PROXY="http://localhost:3128",
                                HTTP_PROXY="http://localhost:3128")
        self.assertEqual(net, "net=-t 127.0.0.1/4100 -T 4222,5432,3128")
        self.assertEqual(nft, NFT_NO_PROXY)

    def test_a_loopback_proxy_on_a_port_already_forwarded_is_forwarded_once(self):
        net, _ = self.network(seat="1", extra_ports="3128", HTTPS_PROXY="http://127.0.0.1:3128")
        self.assertEqual(net, "net=-t 127.0.0.1/4100 -T 4222,3128")

    def test_a_loopback_proxy_on_the_seat_s_server_port_is_refused(self):
        r = self.proxy(seat="1", sport="4100", HTTPS_PROXY="http://127.0.0.1:4100")
        self.assertEqual(r.returncode, 2)
        self.assertIn("server port", r.stderr)

    # --- a bad proxy fails the launch and opens nothing ---

    def assert_refused(self, words, **env):
        r = self.proxy(**env)
        self.assertEqual(r.returncode, 2, (env, r.stdout))
        self.assertIn("opencode-sandbox: ", r.stderr)
        self.assertIn(words, r.stderr)
        self.assertNotIn("rules<", r.stdout)

    def test_malformed_proxy_urls_stop_the_launch(self):
        for bad in ("http://proxy.example", "proxy.example", "http://192.0.2.10:0", "http://192.0.2.10:70000",
                    "http://192.0.2.10:3128/path", "http://192.0.2.300:3128", "http://[2001:db8::zz]:3128",
                    "http://proxy example:3128", "http://192.0.2.10:3128\nhttp://192.0.2.11:1"):
            self.assert_refused("proxy", HTTPS_PROXY=bad)

    def test_a_socks_proxy_is_refused(self):
        self.assert_refused("http:// or https://", HTTPS_PROXY="socks5://192.0.2.10:1080")

    def test_a_proxy_with_credentials_in_its_url_is_refused(self):
        self.assert_refused("credentials", HTTPS_PROXY="http://user:secret@192.0.2.10:3128")

    def test_a_proxy_host_that_does_not_resolve_is_refused(self):
        self.assert_refused("resolve", HTTPS_PROXY="http://nowhere.example:3128")

    def test_an_unspecified_proxy_address_is_refused(self):
        self.assert_refused("address", HTTPS_PROXY="http://0.0.0.0:3128")

    def test_upper_and_lower_case_that_disagree_are_refused(self):
        self.assert_refused("disagree", HTTPS_PROXY="http://192.0.2.10:3128", https_proxy="http://192.0.2.11:3128")

    # --- the key check goes through the proxy ---

    def key_check(self, **env):
        script = f"seat= sport=\n" + block(*PROXY_BLOCK)
        script += f"curl={self.stubs}/curl lanekey=k auth=/x/auth.json prov=opencode-go\n"
        script += block("keyurl=", 'mkdir -p "$lane"/{oc')
        r = self.run_bash(script, **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return (self.stubs / "curl.args").read_text().splitlines()

    def test_the_key_check_uses_the_https_proxy(self):
        args = self.key_check(HTTPS_PROXY="http://proxy.example:3128", NO_PROXY="corp.example")
        self.assertIn("--proxy", args)
        self.assertEqual(args[args.index("--proxy") + 1], "http://proxy.example:3128")
        self.assertEqual(args[args.index("--noproxy") + 1], "corp.example,127.0.0.1,localhost,::1")

    def test_the_key_check_without_a_proxy_is_unchanged(self):
        args = self.key_check()
        self.assertEqual(args, ["-sS", "-m", "15", "-o", "/dev/null", "-w", "%{http_code}", "-K", "-",
                                "https://opencode.ai/zen/go/v1/models"])

    # --- the docs say what a proxy needs ---

    def test_the_docs_say_a_changed_proxy_address_needs_a_relaunch(self):
        docs = (ROOT / "docs" / "orca.md").read_text()
        self.assertIn("HTTPS_PROXY", docs)
        self.assertIn("relaunch", docs)
        self.assertIn("proxy", (ROOT / "docs" / "orca-cotal-setup.md").read_text().split("## 3.", 1)[1])


if __name__ == "__main__":
    unittest.main()
