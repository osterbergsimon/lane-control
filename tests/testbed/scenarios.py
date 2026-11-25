"""The testbed's scenarios (tests/testbed/README.md), in order of value. Each is a function of a Ctx that raises Fail
with what went wrong, Skip with why it did not run, or returns a one-line description of what it checked. No scenario
starts a real model: the agents in them are OpenCode on the fake gateway, or plain shell commands in Orca terminals."""
import json
import shlex
import time


class Fail(Exception):
    pass


class Skip(Exception):
    pass


class Ctx:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Scenario:
    def __init__(self, name, fn, doc):
        self.name, self.fn, self.doc = name, fn, doc


ALL = []


def scenario(doc):
    def wrap(fn):
        ALL.append(Scenario(fn.__name__.replace("_", "-"), fn, doc))
        return fn
    return wrap


LC = "~/lane-control"
GATEWAY_KEY = "testbed-not-a-secret"  # the fake gateway's own key, made up; set where agents start
OPENCODE_CONFIG = {
    "$schema": "https://opencode.ai/config.json",
    "model": "litellm/chat",
    "provider": {"litellm": {"npm": "@ai-sdk/openai-compatible", "name": "testbed gateway",
                             "options": {"baseURL": "http://127.0.0.1:4000/v1", "apiKey": "{env:LITELLM_API_KEY}"},
                             "models": {"chat": {}, "code": {}}}},
}
ENV = f"export LITELLM_API_KEY={GATEWAY_KEY}; "


def run(ctx, role, script, check=True, timeout=600, input=None):
    p = ctx.ex(role, script, check=False, timeout=timeout, input=input)
    if check and p.returncode:
        raise Fail(f"{role}: `{script[:120]}` exited {p.returncode}: {(p.stderr or p.stdout).strip()[-700:]}")
    return p


def expect(cond, what):
    if not cond:
        raise Fail(what)


def tty(cmd, *replies, default=""):
    """`cmd` on a terminal, its questions answered as the owner would (image/answer): each reply is 'REGEX=>TEXT'."""
    flags = " ".join(f"--reply {shlex.quote(r)}" for r in replies)
    return f"answer {flags} --default {shlex.quote(default)} -- bash -c {shlex.quote(cmd)}"


def opencode_config(ctx, role):
    run(ctx, role, "mkdir -p ~/.config/opencode && cat > ~/.config/opencode/opencode.json",
        input=json.dumps(OPENCODE_CONFIG, indent=1))


def scratch_project(ctx, role, name="scratch", owner="acme"):
    """A project on the fake GitHub (a bare repo in the forge) and its clone in ~/code/<name> on `role`."""
    run(ctx, role, f"""
      test -d /forge/repos/{owner}/{name}.git || gh repo create {owner}/{name} >/dev/null
      if [ ! -d ~/code/{name}/.git ]; then
        git clone -q file:///forge/repos/{owner}/{name}.git ~/code/{name} 2>/dev/null
        cd ~/code/{name}
        if ! git rev-parse -q --verify origin/main >/dev/null; then
          printf '# {name}\\n\\nA scratch project for the lane-control testbed.\\n' > README.md
          printf '# {name}: agent rules\\n\\n- Run `./check.sh` before a commit.\\n' > AGENTS.md
          printf '#!/bin/sh\\necho ok\\n' > check.sh; chmod +x check.sh
          git add README.md AGENTS.md check.sh && git commit -qm "scratch project" && git push -q origin HEAD:main
        fi
      fi""")
    return f"~/code/{name}"


def settings_dir(ctx, role, name="scratch"):
    p = run(ctx, role, f"ls -d ~/.config/lane-control/projects/{name}-* 2>/dev/null | head -1", check=False)
    return p.stdout.strip()


# --- 1. onboarding --------------------------------------------------------------------------------------------------

@scenario("project-setup --suggest-tiers litellm against the fake gateway: the aliases, what is behind them, the table")
def suggest_tiers(ctx):
    opencode_config(ctx, "desktop")
    repo = scratch_project(ctx, "desktop")
    ps = f"{LC}/core/scripts/project-setup"
    p = run(ctx, "desktop", f"{ENV} cd {repo} && {ps} . --suggest-tiers litellm < /dev/null", check=False)
    out = p.stdout + p.stderr
    expect(p.returncode == 3, f"without a terminal it should print and exit 3, got {p.returncode}: {out[-500:]}")
    for want in ("testbed/fake-chat-small", "testbed/fake-code-large", "litellm/chat", "left out: embed"):
        expect(want in out, f"the suggestion does not name {want}: {out[-800:]}")
    expect(GATEWAY_KEY not in out, "the gateway key was printed")
    log = run(ctx, "desktop", "cat ~/tmp/gateway.log").stdout
    expect('"/model/info"' in log and '"/v1/models"' in log, f"the gateway was not asked: {log[-300:]}")
    expect('"auth": "ok"' in log, "the key from {env:LITELLM_API_KEY} did not reach the gateway")
    p = run(ctx, "desktop", ENV + tty(f"cd {repo} && {ps} . --suggest-tiers litellm", "tier table=>y"), check=False)
    d = settings_dir(ctx, "desktop")
    tsv = run(ctx, "desktop", f"cat {d}/lanes.tsv", check=False).stdout if d else ""
    expect("opencode\tlitellm/" in tsv, f"yes in a terminal did not write the table ({p.returncode}): "
                                       f"{(p.stdout + p.stderr)[-500:]}")
    expect("embed" not in tsv, "the embedding alias went into the table")
    return "suggestion printed and exit 3 without a terminal; table written on yes; embedding alias left out"


@scenario("project-setup onboards the scratch project with no Claude: OpenCode coordinator on the fake gateway")
def onboard(ctx):
    opencode_config(ctx, "desktop")
    repo = scratch_project(ctx, "desktop")
    run(ctx, "desktop", "pkill -f opencode2 2>/dev/null; : > ~/tmp/gateway.log", check=False)
    cmd = (f"{ENV} cd {repo} && {LC}/core/scripts/project-setup {repo} --skip-adapt --no-machine-setup "
           "--coordinator opencode/litellm/chat --reviewer opencode/litellm/code")
    p = run(ctx, "desktop", tty(cmd), check=False, timeout=900)
    out = p.stdout + p.stderr
    expect(p.returncode == 0, f"project-setup exited {p.returncode}: {out[-1200:]}")
    files = run(ctx, "desktop", f"cd {repo} && ls .lane-control/").stdout.split()
    for f in ("coordinator.md", "reviewer.md"):
        expect(f in files, f".lane-control/{f} was not rendered ({files})")
    terms = run(ctx, "desktop", "orca-ide terminal list --json").stdout
    expect("scratch coordinator" in terms, f"no terminal titled 'scratch coordinator': {terms[-600:]}")
    for _ in range(60):
        log = run(ctx, "desktop", "cat ~/tmp/gateway.log").stdout
        if "chat/completions" in log:
            break
        time.sleep(2)
    expect("chat/completions" in log, "the coordinator's OpenCode never called the fake gateway")
    again = run(ctx, "desktop", tty(cmd), check=False, timeout=900)
    expect(again.returncode == 0 and "exists" in again.stdout, f"a re-run did not say exists: {again.stdout[-600:]}")
    return "personas rendered, coordinator terminal up on OpenCode, gateway called; re-run says exists"


# --- 3. direction 1: the desktop drives the box ---------------------------------------------------------------------

def proof_on(ctx, env, role, tag):
    """Through the paired environment `env`, add a folder on `role`'s Orca and run a command in a terminal there; the
    command's own output (that host's name) must land on `role`."""
    run(ctx, role, f"mkdir -p ~/code/proof-{tag} && cd ~/code/proof-{tag} && (git rev-parse 2>/dev/null || "
                   "(git init -q && git commit -q --allow-empty -m proof)); rm -f ~/tmp/proof-" + tag)
    src = "desktop" if role != "desktop" else "box"
    cli = "orca-ide" if src == "desktop" else "~/.orca/cli/bin/orca"
    run(ctx, src, f"{cli} repo add --environment {env} --path /home/lc/code/proof-{tag} --json >/dev/null")
    run(ctx, src, f"{cli} terminal create --environment {env} --worktree path:/home/lc/code/proof-{tag} "
                  f"--title proof-{tag} --command 'hostname > ~/tmp/proof-{tag}' --json >/dev/null")
    for _ in range(30):
        seen = run(ctx, role, f"cat ~/tmp/proof-{tag} 2>/dev/null", check=False).stdout.strip()
        if seen:
            break
        time.sleep(1)
    expect(seen == role, f"the terminal made through {env} did not run on {role} (saw {seen!r})")


def d1(ctx, host, port):
    p = run(ctx, "desktop", f"{LC}/orchestrators/orca-cotal/scripts/orca-host-install {host} --pair --port {port}",
            check=False, timeout=600)
    out = p.stdout + p.stderr
    expect(p.returncode == 0, f"orca-host-install {host} exited {p.returncode}: {out[-800:]}")
    for want in ("installed orcad-", f"started orcad {port}", f"paired {host}"):
        expect(want in out, f"orca-host-install said no '{want}': {out[-600:]}")
    st = json.loads(run(ctx, "desktop", f"orca-ide status --environment {host} --json").stdout)
    rt = st.get("result", {}).get("runtime", {})
    expect(rt.get("reachable") is True, f"environment {host} is not reachable: {rt}")
    proof_on(ctx, host, "box", host)
    again = run(ctx, "desktop", f"{LC}/orchestrators/orca-cotal/scripts/orca-host-install {host} --pair --port {port}")
    expect("exists orcad-" in again.stdout and "running orcad" in again.stdout and "already-paired" in again.stdout,
           f"a re-run is not idempotent: {again.stdout}")
    return rt.get("appVersion")


def unpair_box(ctx, host):
    """Back to a box with no Orca server, so the next pairing starts fresh."""
    run(ctx, "desktop", f"systemctl --user disable --now orca-tunnel-{host}.service; "
                        f"orca-ide environment rm --environment {host} --json >/dev/null 2>&1; true", check=False)
    run(ctx, "box", "systemctl --user disable --now orcad.service; for p in $(pgrep -f 'orcad[.]js'); do kill $p; done;"
                    " sleep 2; rm -rf ~/.orca ~/.orca-remote ~/.config/systemd/user/orcad.service; true", check=False)


@scenario("direction 1, direct: orca-host-install box --pair puts Orca's server on the box and pairs it over -L")
def d1_direct(ctx):
    unpair_box(ctx, "box")
    v = d1(ctx, "box", 6769)
    return f"box server {v} reachable through the -L tunnel; a terminal made from the desktop ran on the box; re-run idempotent"


@scenario("direction 1 via the jump host: the same through ProxyJump (ssh alias box-via-jump)")
def d1_jump(ctx):
    unpair_box(ctx, "box")
    run(ctx, "desktop", "ssh -G box-via-jump | grep -qi '^proxyjump jump'")
    v = d1(ctx, "box-via-jump", 6769)
    run(ctx, "jump", "test ! -e ~/.orca && test ! -e ~/.orca-remote")
    return f"box server {v} reachable through jump; nothing of Orca on the jump host"


# --- 4. direction 2: the box runs lanes on the desktop ---------------------------------------------------------------

def d2(ctx, host):
    """The box (paired from the desktop as `host`) gets the desktop's own server: lane-tunnel -R on the desktop, the
    desktop's pairing link on stdin to project-setup --box-pairing add on the box, then a terminal on the desktop made
    from the box."""
    st = json.loads(run(ctx, "desktop", f"orca-ide status --environment {host} --json", check=False).stdout or "{}")
    if not st.get("result", {}).get("runtime", {}).get("reachable"):
        raise Skip(f"the box is not paired as {host} (run d1-{'jump' if 'jump' in host else 'direct'} first)")
    ctx.copy_lane_control("box", dest=".local/share/lane-control")
    link = run(ctx, "desktop", "head -1 ~/.orca-desktop-readiness | jq -r .pairing.url").stdout.strip()
    expect(link.startswith("orca://"), "the desktop server's readiness line has no pairing link")
    run(ctx, "desktop", f"{LC}/core/scripts/lane-tunnel stop {host} >/dev/null 2>&1; true", check=False)
    p = run(ctx, "desktop", f"LANE_TUNNEL_SETTLE=3 {LC}/core/scripts/lane-tunnel start {host} 6768", check=False)
    expect(p.returncode == 0, f"lane-tunnel start exited {p.returncode}: {(p.stdout + p.stderr)[-600:]}")
    for _ in range(20):
        if run(ctx, "box", "ss -ltn | grep -q '127.0.0.1:6768 '", check=False).returncode == 0:
            break
        time.sleep(1)
    else:
        raise Fail("the -R tunnel's port 6768 never opened on the box's loopback")
    expect(run(ctx, "box", "ss -ltn | grep -q '0.0.0.0:6768 '", check=False).returncode != 0,
           "the tunnel's port is open on the box's network, not only its loopback")
    run(ctx, "box", "~/.orca/cli/bin/orca environment rm --environment desktop --json >/dev/null 2>&1; true", check=False)
    p = run(ctx, "box", "~/.local/share/lane-control/core/scripts/project-setup --box-pairing add desktop",
            check=False, input=link + "\n")
    out = p.stdout + p.stderr
    expect(p.returncode == 0, f"--box-pairing add exited {p.returncode}: {out[-600:]}")
    expect(link not in out, "the pairing link was printed")
    leak = run(ctx, "box", f"grep -rlF {shlex.quote(link[-24:])} ~/.config ~/.local/share/lane-control 2>/dev/null",
               check=False).stdout.strip()
    expect(not leak, f"the pairing link is in a file on the box: {leak}")
    st = json.loads(run(ctx, "box", "~/.orca/cli/bin/orca status --environment desktop --json").stdout)
    expect(st.get("result", {}).get("runtime", {}).get("reachable") is True, f"desktop not reachable from the box: {st}")
    proof_on(ctx, "desktop", "desktop", f"d2-{host}")
    return "lane-tunnel -R up on the box's loopback only; box paired with the desktop by stdin; a terminal made " \
           "from the box ran on the desktop"


@scenario("direction 2: the box reaches the desktop's Orca through lane-tunnel -R (after d1-jump: through the jump)")
def d2_tunnel(ctx):
    host = "box-via-jump" if json.loads(run(ctx, "desktop", "orca-ide status --environment box-via-jump --json",
                                            check=False).stdout or "{}").get("result", {}).get("runtime", {}).get(
        "reachable") else "box"
    return d2(ctx, host) + f" (ssh host {host})"


# --- 2. --refresh on a host set up before ------------------------------------------------------------------------------

OLD_REF = "48a38ab"  # main before LIT-1 (#64): its lane-tier refuses an OpenCode tier, so a refresh must replace it


@scenario(f"--refresh on a box set up by an older lane-control ({OLD_REF}, before LIT-1): the lane tools, skills and "
          "personas become this checkout's")
def refresh(ctx):
    if run(ctx, "box", "test -x ~/.orca/cli/bin/orca && ~/.orca/cli/bin/orca status --json | jq -e "
                       ".result.runtime.reachable >/dev/null", check=False).returncode:
        raise Skip("the box has no Orca server yet (run d1-direct or d1-jump first)")
    new = "~/.local/share/lane-control"
    ctx.copy_lane_control("box", dest=new[2:])
    ctx.copy_lane_control("box", ref=OLD_REF, dest="lane-control-old")
    run(ctx, "box", "rm -rf ~/.agents ~/code/oldproj ~/.config/lane-control/projects/oldproj-*", check=False)
    repo = scratch_project(ctx, "box", name="oldproj")
    p = run(ctx, "box", f"cd {repo} && ~/lane-control-old/core/scripts/project-setup {repo} --skip-adapt --no-start "
                        "--no-machine-setup < /dev/null", check=False, timeout=900)
    expect(p.returncode == 0, f"the old project-setup exited {p.returncode}: {(p.stdout + p.stderr)[-800:]}")
    same = f"cmp -s ~/.agents/bin/lane-tier {new}/core/scripts/lane-tier"
    expect(run(ctx, "box", same, check=False).returncode != 0, "the old setup already installed this checkout's lane-tier")
    p = run(ctx, "box", f"cd {repo} && {new}/core/scripts/project-setup {repo} --refresh < /dev/null", check=False,
            timeout=900)
    expect(p.returncode == 0, f"--refresh exited {p.returncode}: {(p.stdout + p.stderr)[-800:]}")
    expect(run(ctx, "box", same, check=False).returncode == 0, "after --refresh ~/.agents/bin/lane-tier is still the old one")
    run(ctx, "box", f"cmp -s ~/.agents/skills/native-orca-lanes/SKILL.md "
                    f"{new}/orchestrators/native-orca/skills/native-orca-lanes/SKILL.md")
    return f"set up with {OLD_REF}, refreshed with this checkout: lane-tier and the lane skill replaced"
