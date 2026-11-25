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
    called = '"path": "/v1/chat/completions", "auth": "ok"'
    for _ in range(60):
        log = run(ctx, "desktop", "cat ~/tmp/gateway.log").stdout
        if called in log:
            break
        time.sleep(2)
    expect(called in log, "the coordinator's OpenCode never called the fake gateway with its key (the key must be "
                          f"in Orca's environment): {log[-300:]}")
    again = run(ctx, "desktop", tty(cmd), check=False, timeout=900)
    expect(again.returncode == 0 and "exists" in again.stdout, f"a re-run did not say exists: {again.stdout[-600:]}")
    return "personas rendered, coordinator terminal up on OpenCode, gateway called with the key; re-run says exists"


# --- 3. direction 1: the desktop drives the box ---------------------------------------------------------------------

def proof_on(ctx, env, role, tag):
    """Through the paired environment `env`, add a folder on `role`'s Orca and run a command in a terminal there; the
    command's own output (that host's name) must land on `role`."""
    run(ctx, role, f"mkdir -p ~/code/proof-{tag} && cd ~/code/proof-{tag} && (git rev-parse 2>/dev/null || "
                   "(git init -q && git commit -q --allow-empty -m proof)); rm -f ~/tmp/proof-" + tag)
    src = "desktop" if role != "desktop" else "box"
    cli = "orca-ide" if src == "desktop" else "~/.orca/cli/bin/orca"
    run(ctx, src, f"{cli} repo add --environment {env} --path /home/u/code/proof-{tag} --json >/dev/null")
    run(ctx, src, f"{cli} terminal create --environment {env} --worktree path:/home/u/code/proof-{tag} "
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

def box_cli_ready(ctx):
    return run(ctx, "box", "test -x ~/.orca/cli/bin/orca && ~/.orca/cli/bin/orca status --json | jq -e "
                           "'.result.runtime.state == \"ready\"' >/dev/null", check=False).returncode == 0


def fresh_desktop_link(ctx):
    """A pairing link the desktop's server has not given out yet: its readiness line's, after a restart of the server
    when that one was used (a link pairs once)."""
    used = run(ctx, "desktop", "test -f ~/tmp/desktop-link-used", check=False).returncode == 0
    if used:
        run(ctx, "desktop", "rm -f ~/.orca-desktop-readiness; for p in $(pgrep -f 'orcad[.]js .*--port 6768'); do "
                            "kill $p; done")
        for _ in range(90):
            if run(ctx, "desktop", "head -1 ~/.orca-desktop-readiness 2>/dev/null | grep -q orca_server_ready && "
                                   "orca-ide status --json | jq -e '.result.runtime.state == \"ready\"' >/dev/null",
                   check=False).returncode == 0:
                break
            time.sleep(1)
        else:
            raise Fail("the desktop's Orca server did not come back after a restart")
    link = run(ctx, "desktop", "head -1 ~/.orca-desktop-readiness | jq -r .pairing.url").stdout.strip()
    expect(link.startswith("orca://"), "the desktop server's readiness line has no pairing link")
    run(ctx, "desktop", "touch ~/tmp/desktop-link-used")
    return link


def d2(ctx, host, remote_port=None):
    """lane-tunnel -R on the desktop to ssh host `host` (6768 here; `remote_port` on the box when given, TUN-1), the
    desktop's pairing link (pointed at the box's port by project-setup's own link_for_port) on stdin to project-setup
    --box-pairing add on the box, then a terminal on the desktop made from the box."""
    if not box_cli_ready(ctx):
        raise Skip("the box has no Orca server yet (run d1-direct or d1-jump first)")
    rport = remote_port or 6768
    ctx.copy_lane_control("box", dest=".local/share/lane-control")
    link = fresh_desktop_link(ctx)
    if rport != 6768:  # the box dials its own end of the tunnel: the link's endpoint moves there, as project-setup does
        link = run(ctx, "desktop", "python3 -c 'import importlib.machinery as m, sys; ps = m.SourceFileLoader(\"ps\", "
                                   "sys.argv[1]).load_module(); print(ps.link_for_port(sys.stdin.read().strip(), 6768, "
                                   f"{rport}))' ~/lane-control/core/scripts/project-setup", input=link).stdout.strip()
    for h in ("box", "box-via-jump"):
        run(ctx, "desktop", f"{LC}/core/scripts/lane-tunnel stop {h} >/dev/null 2>&1; true", check=False)
    extra = f"--remote-port {rport} " if remote_port else ""
    p = run(ctx, "desktop", f"LANE_TUNNEL_SETTLE=3 {LC}/core/scripts/lane-tunnel start {extra}{host} 6768", check=False)
    expect(p.returncode == 0, f"lane-tunnel start exited {p.returncode}: {(p.stdout + p.stderr)[-600:]}")
    for _ in range(20):
        if run(ctx, "box", f"ss -ltn | grep -q '127.0.0.1:{rport} '", check=False).returncode == 0:
            break
        time.sleep(1)
    else:
        raise Fail(f"the -R tunnel's port {rport} never opened on the box's loopback")
    expect(run(ctx, "box", f"ss -ltn | grep -q '0.0.0.0:{rport} '", check=False).returncode != 0,
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
    proof_on(ctx, "desktop", "desktop", f"d2-{host}-{rport}")
    ports = f"6768 here, {rport} on the box" if remote_port else "6768 at both ends"
    return f"lane-tunnel -R to {host} ({ports}) open on the box's loopback only; box paired with the desktop by " \
           "stdin; a terminal made from the box ran on the desktop"


@scenario("direction 2 via the jump host: lane-tunnel -R to box-via-jump, the box pairs with the desktop's server")
def d2_tunnel(ctx):
    return d2(ctx, "box-via-jump")


# --- 5. lanes across hosts ----------------------------------------------------------------------------------------------

CLI = {"desktop": "orca-ide", "box": "~/.orca/cli/bin/orca"}


def lane_across(ctx, coord, env, target, tag):
    """A coordinator on `coord` runs a lane on `target` (its paired environment `env`): worker-start --on env
    --worktree new-top-level --repo; the lane's worktree and branch are on `target`; send --to dispatch:<id> reaches it
    and it acts; its worker_done reaches the coordinator's check (poll: remote.md, Reports across hosts);
    worker-release closes its terminal; the worktree is removed. The worker is OpenCode on the target's fake gateway."""
    cli = CLI[coord]
    st = json.loads(run(ctx, coord, f"{cli} status --environment {env} --json", check=False).stdout or "{}")
    if not st.get("result", {}).get("runtime", {}).get("reachable"):
        raise Skip(f"{coord} has no reachable environment {env} (run the scenario that pairs it first)")
    cfg = dict(OPENCODE_CONFIG, permission={"bash": "allow", "edit": "allow", "external_directory": "allow"})
    run(ctx, target, "mkdir -p ~/.config/opencode && cat > ~/.config/opencode/opencode.json", input=json.dumps(cfg))
    repo = scratch_project(ctx, target)
    run(ctx, coord, f"{cli} repo add --environment {env} --path /home/u/code/scratch --json >/dev/null")
    run(ctx, coord, f"mkdir -p ~/code/coord && cd ~/code/coord && (git rev-parse 2>/dev/null || (git init -q && git commit "
                    f"-q --allow-empty -m coord)); {cli} repo add --path /home/u/code/coord --json >/dev/null")
    t0 = json.loads(run(ctx, coord, f"{cli} terminal create --worktree path:/home/u/code/coord --title 'tb {tag} "
                                    "coordinator' --command bash --json").stdout)["result"]["terminal"]["handle"]
    run(ctx, coord, f"{cli} orchestration run-create --objective 'testbed {tag}' --from {t0} --json >/dev/null")
    retried = ""
    for attempt in (1, 2):
        name = f"tb-{tag}-{int(time.time()) % 100000}"
        spec = (f"Testbed lane {tag}.\nRUN-TOOL: git rev-parse --abbrev-ref HEAD > ~/tmp/{name}-branch; pwd > "
                f"~/tmp/{name}-pwd; hostname > ~/tmp/{name}-host\nWAIT-FOR-MESSAGE\n")
        run(ctx, coord, f"cat > ~/tmp/{name}.md", input=spec)
        p = run(ctx, coord, f"{cli} orchestration worker-start --on {env} --worktree new-top-level --repo "
                            f"path:/home/u/code/scratch --name {name} --agent opencode --spec-file ~/tmp/{name}.md "
                            f"--from {t0} --json", check=False, timeout=300)
        try:
            ws = json.loads(p.stdout)
        except ValueError:
            ws = {}
        res = ws.get("result") or {}
        expect(ws.get("ok") and res.get("dispatchId"), f"worker-start --on {env} failed: {(p.stdout + p.stderr)[-700:]}")
        disp, term = res["dispatchId"], res.get("terminal")
        for _ in range(45 if attempt == 1 else 90):
            seen = run(ctx, target, f"cat ~/tmp/{name}-host ~/tmp/{name}-branch ~/tmp/{name}-pwd 2>/dev/null",
                       check=False).stdout.split()
            if len(seen) == 3:
                break
            time.sleep(2)
        if len(seen) == 3:
            break
        # Seen once: OpenCode up with an empty composer, the brief never delivered (lc-7dd). Say so, start once more.
        retried = f" (first start's brief never arrived, lc-7dd; retried)"
        run(ctx, coord, f"{cli} orchestration worker-release --dispatch {disp} --json >/dev/null 2>&1", check=False)
    expect(len(seen) == 3, f"the lane never ran its first step on {target}")
    expect(seen[0] == target, f"the lane ran on {seen[0]}, not {target}")
    expect(name in seen[1] and seen[2].startswith("/home/u/orca/workspaces/"),
           f"the lane's branch or worktree is not its own: {seen}")
    expect(run(ctx, target, f"git -C {seen[2]} rev-parse --abbrev-ref HEAD").stdout.strip() == seen[1],
           "the worktree's branch changed")
    run(ctx, coord, f"{cli} orchestration send --to dispatch:{disp} --subject act --body 'RUN-TOOL: echo acted > "
                    f"~/tmp/{name}-acted' --from {t0} --json >/dev/null")
    for _ in range(90):
        if run(ctx, target, f"test -s ~/tmp/{name}-acted", check=False).returncode == 0:
            break
        time.sleep(2)
    else:
        raise Fail(f"send --to dispatch:{disp} never made the lane act")
    done = False
    for _ in range(60):
        out = run(ctx, coord, f"{cli} orchestration check --terminal {t0} --json", check=False).stdout
        try:
            msgs = json.loads(out).get("result", {}).get("messages", [])
        except ValueError:
            msgs = []
        if any(m.get("type") == "worker_done" and term and m.get("from") == term for m in msgs) or \
                any(m.get("type") == "worker_done" and disp in json.dumps(m) for m in msgs):
            done = True
            break
        time.sleep(2)
    expect(done, f"the lane's worker_done never reached {coord}'s check")
    rel = run(ctx, coord, f"{cli} orchestration worker-release --dispatch {disp} --json", check=False)
    expect(rel.returncode == 0, f"worker-release failed: {(rel.stdout + rel.stderr)[-400:]}")
    for _ in range(30):
        terms = run(ctx, coord, f"{cli} terminal list --environment {env} --json", check=False).stdout
        if term not in terms:
            break
        time.sleep(1)
    expect(term not in terms, f"the lane's terminal {term} is still open after worker-release")
    run(ctx, coord, f"{cli} worktree rm --environment {env} --worktree path:{seen[2]} --force --json >/dev/null",
        check=False)
    expect(run(ctx, target, f"test ! -e {seen[2]}", check=False).returncode == 0, f"{seen[2]} is still on {target}")
    return (f"worker-start --on {env}: worktree and branch {seen[1]} on {target}; a message made it act; worker_done "
            f"reached {coord}'s check; released (terminal closed); worktree removed{retried}")


@scenario("lane A, direct: the desktop's coordinator runs and controls a lane on the box (--on box)")
def lane_a_direct(ctx):
    return lane_across(ctx, "desktop", "box", "box", "a-direct")


@scenario("lane A via the jump host: the same with the box paired through ProxyJump (--on box-via-jump)")
def lane_a_jump(ctx):
    return lane_across(ctx, "desktop", "box-via-jump", "box", "a-jump")


@scenario("lane B via the jump host: the box's coordinator runs a lane on the desktop through lane-tunnel -R")
def lane_b_jump(ctx):
    return lane_across(ctx, "box", "desktop", "desktop", "b-jump")


@scenario("direction 2, direct, with other ports (TUN-1): lane-tunnel --remote-port 6870 to box, 6768 on the desktop")
def d2_ports(ctx):
    return d2(ctx, "box", remote_port=6870)


@scenario("lane B, direct: the box's coordinator runs a lane on the desktop through the tunnel with other ports")
def lane_b_direct(ctx):
    return lane_across(ctx, "box", "desktop", "desktop", "b-direct")


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


# --- 4b. direction 2 without lane-tunnel --------------------------------------------------------------------------------

def listeners(ctx, role, port):
    return sorted(set(run(ctx, role, f"ss -ltnH 'sport = :{port}' | awk '{{print $4}}'", check=False).stdout.split()))


@scenario("direction 2 without lane-tunnel: the box pairs with the desktop's network address (orca pair --address), "
          "then a lane from the box on the desktop; records what the desktop's Orca then listens on")
def d2_no_tunnel(ctx):
    """Is lane-tunnel still needed? (owner 2026-11-25). Driven with the Orca CLI directly: project-setup refuses a link
    that is not 127.0.0.1. Afterwards the desktop's server is restarted, back on loopback, and the box forgets it."""
    if not box_cli_ready(ctx):
        raise Skip("the box has no Orca server yet (run d1-direct or d1-jump first)")
    ip = run(ctx, "desktop", "hostname -i | awk '{print $1}'").stdout.strip()
    before = listeners(ctx, "desktop", 6768)
    p = run(ctx, "desktop", f"orca-ide pair --address {ip} --json", check=False)
    try:
        link = json.loads(p.stdout).get("result", {}).get("url") or ""
    except ValueError:
        link = ""
    if not link.startswith("orca://"):
        link = next((w for w in (p.stdout + p.stderr).split() if w.startswith("orca://")), "")
    if not link and "network_exposure_failed" in p.stdout:
        why = run(ctx, "desktop", "grep -o 'Runtime bind address is pinned to [0-9.]*; refusing to widen to all interfaces' "
                  "~/.orca-desktop.log | tail -1",
                  check=False).stdout.strip()
        raise Skip(f"orca pair --address {ip}: network_exposure_failed; headless orcad will not widen ({why or 'see '
                   '~/.orca-desktop.log'}), even started without --bind; only the desktop app widens itself. The "
                   f"desktop kept listening on {', '.join(before)}. Not verified here: needs the desktop app, or orcad "
                   "started with --bind 0.0.0.0 (next slice)")
    expect(link, f"orca pair --address {ip} gave no link ({p.returncode}): {(p.stdout + p.stderr)[-500:]}")
    time.sleep(2)
    after = listeners(ctx, "desktop", 6768)
    try:
        run(ctx, "box", "~/.orca/cli/bin/orca environment rm --environment desktop-net --json >/dev/null 2>&1; true",
            check=False)
        # The link reaches the box on stdin, through the same preload project-setup uses (pairing-code-stdin.cjs).
        ctx.copy_lane_control("box", dest=".local/share/lane-control")
        add = run(ctx, "box", "cli=$(readlink -f ~/.orca/cli/bin/orca); "
                              "node=$(grep -o '/[^ \"]*/bin/node' \"$cli\" | head -1); "
                              "idx=$(grep -o '/[^ \"]*/out/cli/index.js' \"$cli\" | head -1); "
                              "$node --require ~/.local/share/lane-control/core/scripts/pairing-code-stdin.cjs $idx "
                              "environment add --name desktop-net --pairing-code - --json", check=False, input=link + "\n")
        if add.returncode:  # the launcher's shape differs: the CLI's own argument (a throwaway container's link)
            add = run(ctx, "box", "~/.orca/cli/bin/orca environment add --name desktop-net --pairing-code "
                                  f"{shlex.quote(link)} --json", check=False)
        expect(add.returncode == 0, f"environment add on the box failed: {(add.stdout + add.stderr)[-400:]}")
        st = json.loads(run(ctx, "box", "~/.orca/cli/bin/orca status --environment desktop-net --json").stdout)
        expect(st.get("result", {}).get("runtime", {}).get("reachable") is True,
               f"the desktop at {ip} is not reachable from the box without a tunnel: {st}")
        lane = lane_across(ctx, "box", "desktop-net", "desktop", "no-tunnel")
    finally:
        run(ctx, "box", "~/.orca/cli/bin/orca environment rm --environment desktop-net --json >/dev/null 2>&1; true",
            check=False)
        run(ctx, "desktop", "for p in $(pgrep -f 'orcad[.]js .*--port 6768'); do kill $p; done", check=False)
        for _ in range(90):
            if run(ctx, "desktop", "orca-ide status --json | jq -e '.result.runtime.state == \"ready\"' >/dev/null",
                   check=False).returncode == 0:
                break
            time.sleep(1)
    restored = listeners(ctx, "desktop", 6768)
    return (f"without a tunnel: {lane}. The desktop's Orca listened on {', '.join(before) or 'nothing'} before, on "
            f"{', '.join(after)} after orca pair (exposure: every interface of the desktop); after a restart on "
            f"{', '.join(restored)}")


# The order `all` runs them in: each lane right after the pairing it uses (d1-jump resets the box's server).
ORDER = ["suggest-tiers", "onboard", "d1-direct", "lane-a-direct", "d1-jump", "lane-a-jump", "d2-tunnel", "lane-b-jump",
         "d2-ports", "lane-b-direct", "d2-no-tunnel", "refresh"]
ALL.sort(key=lambda s: ORDER.index(s.name) if s.name in ORDER else len(ORDER))
