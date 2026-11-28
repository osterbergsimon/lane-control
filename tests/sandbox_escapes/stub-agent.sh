#!/usr/bin/env bash
# The stub "agent" of the SBX-2 adversarial suite (from the SBX-1 spike's stub-agent.sh). Not an agent: it never
# starts opencode, claude or a gateway. Run by the real lane-sandbox-shim as the real OpenCode, inside the real chain,
# it tries what an agent's tools could try, does a lane's work through the inside wrappers and the broker, and leaves
# what tests/test_sandbox_escapes.py then checks from outside.
# One line per check:  ok <check> | BAD <check>. Exit 0 when every line is ok.
# Its inputs come from stub.json beside it (only the allowlist of the environment reaches an agent): see the test.
set -u
here=$(cd "$(dirname "$0")" && pwd)
cfg="$here/stub.json"
j() { python3 -I -c 'import json,sys; v=json.load(open(sys.argv[1]));
for k in sys.argv[2].split("."): v=v[k]
print(":".join(v) if isinstance(v, list) else v)' "$cfg" "$1"; }
bad=0
ok() { printf 'ok   %s\n' "$*"; }
no() { printf 'BAD  %s\n' "$*"; bad=1; }
MARK="$(j mark_a)$(j mark_b)"
MODE=$(j mode)
OUT=/run/lane/outbox
[ "$MODE" = control ] && OUT=$(j outbox_host)

inspect() {
  printf 'inspect head=%s dirty=%s unpushed=%s\n' "$(git rev-parse HEAD)" "$(git status --porcelain | wc -l)" \
    "$(git rev-list --count "$(j pushed)..HEAD" 2>/dev/null || echo unknown)"
}
case $MODE in
  inspect) inspect; exit 0 ;;
  edit) printf 'more\n' >>lane-file; git commit -qam "stub: unpushed" && printf 'wip\n' >>lane-file; inspect; exit 0 ;;
  tty)
    if { : </dev/tty; } 2>/dev/null; then echo "tty=yes" >"$OUT/tty"; else echo "tty=no" >"$OUT/tty"; fi
    trap 'echo winch >>"$OUT/winch"' WINCH
    : >"$OUT/tty-ready"
    for _ in $(seq 100); do [ -e "$OUT/winch" ] && break; sleep 0.1; done
    exit 0 ;;
esac

# 1. The floor: by path, through a link, by a search of everything visible.
IFS=: read -r -a floor <<<"$(j floor)"
for f in "${floor[@]}"; do
  if grep -qsF -- "$MARK" "$f"; then no "read floor file $f"; else ok "floor file $(basename "$f"): nothing to read"; fi
done
ln -sf "${floor[0]}" planted-link
if grep -qsF -- "$MARK" planted-link; then no "read the floor through a link"; else ok "a link to a floor file reads nothing"; fi
rm -f planted-link
hits=$(grep -rIlF -- "$MARK" "$(j home)" "$(j wt)" "$(j shared)" /tmp /etc /run 2>/dev/null | head -3)
if [ -n "$hits" ]; then no "search found floor data: $(printf '%s' "$hits" | tr '\n' ' ')"
else ok "a search of home, the working tree, the shared repo, /tmp, /etc and /run finds no floor data"; fi

# 2. The environment, also through /proc.
if env | grep -q '^FAKE_OWNER_TOKEN='; then no "the owner's token is in the agent's environment"
else ok "the owner's token is not in the agent's environment"; fi
leak=0
for e in /proc/[0-9]*/environ; do tr '\0' '\n' <"$e" 2>/dev/null | grep -q '^FAKE_OWNER_TOKEN=' && leak=1; done
if [ "$leak" = 1 ]; then no "another process's environment shows the owner's token"; else ok "no visible process has the owner's token"; fi
if [ -n "${DBUS_SESSION_BUS_ADDRESS:-}${XDG_RUNTIME_DIR:-}" ]; then no "the session bus variables reached the agent"
else ok "the session bus variables (systemd-run's only) did not reach the agent"; fi
if [ -n "${FAKE_GATEWAY_KEY:-}" ]; then ok "the gateway key is the one secret the agent has"; else no "no gateway key"; fi

# 3. Capabilities: none; read-only binds refuse remount, write and umount; no nested user namespace; no nft change.
cap=$(grep '^CapEff:' /proc/self/status | tr -d '[:space:]' | cut -d: -f2)
if [ "$cap" = 0000000000000000 ]; then ok "no capabilities (CapEff 0, uid $(id -u) inside)"; else no "capabilities left: CapEff $cap"; fi
IFS=: read -r -a ro <<<"$(j ro)"
for f in "${ro[@]}"; do
  mount -o remount,bind,rw "$f" 2>/dev/null && no "remounted $f read-write"
  if { printf 'WRITTEN-BY-SANDBOX\n' >>"$f"; } 2>/dev/null; then no "wrote read-only $f"
  else ok "cannot remount or write $f"; fi
  umount "$f" 2>/dev/null && no "unmounted $f"
done
if unshare -Urm true 2>/dev/null; then no "made a nested user namespace"; else ok "cannot make a nested user namespace"; fi
if "$(j nft)" flush ruleset 2>/dev/null; then no "flushed the network filter"; else ok "cannot change the network filter"; fi

# 4. Sockets: the nix daemon, host loopback TCP and UDP, abstract sockets, the host's other addresses.
sockets=$(python3 -I - "$cfg" <<'PY'
import json, socket, sys
c = json.load(open(sys.argv[1]))
def tcp(a, p):
    try: socket.create_connection((a, p), timeout=2).close(); return True
    except OSError: return False
def unix(addr):
    try:
        s = socket.socket(socket.AF_UNIX); s.settimeout(2); s.connect(addr); s.close(); return True
    except OSError: return False
def udp(p):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(2); s.sendto(b"ping", ("127.0.0.1", p))
        return s.recvfrom(10)[0] == b"pong"
    except OSError: return False
say = lambda good, text: print(("ok   " if good else "BAD  ") + text)
say(not unix("/nix/var/nix/daemon-socket/socket"), "the nix daemon socket cannot be reached")
say(tcp("127.0.0.1", c["open_port"]), "the forwarded loopback port works (Orca's hook port)")
say(not tcp("127.0.0.1", c["closed_port"]), "a host loopback TCP port that is not forwarded cannot be reached")
say(not udp(c["udp_port"]), "a host loopback UDP port cannot be reached")
say(not unix("\0" + c["abstract"]), "a host abstract unix socket cannot be reached (X11's is one)")
reached = sum(tcp(a, c["host_port"]) for a in c["addrs"])
say(reached == 0, f"none of the host's {len(c['addrs'])} other addresses reaches a host service")
PY
)
printf '%s\n' "$sockets"; case $sockets in *BAD*|"") bad=1 ;; esac

# 5. The shared repository is absent; other refs' objects are not in the clone.
if [ -e "$(j shared)/.git" ]; then no "the shared repository is visible"; else ok "the shared repository is not there"; fi
IFS=: read -r -a foreign <<<"$(j foreign)"
for sha in "${foreign[@]}"; do
  if git cat-file -e "$sha" 2>/dev/null; then no "object $sha is readable"
  else ok "object ${sha:0:12} (another lane's or the owner's unpushed work) is not in the clone"; fi
done

[ "$MODE" = control ] && exit "$bad"  # the control (no sandbox) stops here: everything after writes

# 6. The broker refuses what is not the lane's (r1 B1, B8; r2 M4) and takes what is.
broker() { python3 -I -c 'import json,socket,sys
s=socket.socket(socket.AF_UNIX); s.connect("/run/lane/broker.sock"); s.sendall(sys.argv[1].encode()+b"\n"); s.shutdown(1)
d=b""
while (c:=s.recv(65536)): d+=c
print(json.loads(d).get("ok"))' "$1"; }
[ "$(broker '{"request":"report","task":"task_other01","dispatch":"ctx_other01","subject":"s","body":"b","outcome":"succeeded"}')" = False ] \
  && ok "a report on another terminal's dispatch is refused" || no "the broker reported for another terminal"
[ "$(broker '{"request":"bd-show","id":"--db=/tmp/x"}')" = False ] && ok "a flag-shaped bd id is refused" || no "flag-shaped bd id taken"
[ "$(broker '{"request":"pr-create","title":"t","body":"b","repo":"evil/repo"}')" = False ] && ok "a path or repo field is refused" || no "an unknown field was taken"
[ "$(broker '{"request":"review-write","name":"x.md","text":"x"}')" = False ] && ok "a lane cannot write a review" || no "a lane wrote a review"
if orca orchestration send --to bot:x --type handoff --subject s 2>/dev/null; then no "orca send --to went out"
else ok "orca send --to and --type handoff are refused inside"; fi
if gh api user 2>/dev/null; then no "gh api ran"; else ok "gh api is refused inside"; fi
[ "$(broker '{"request":"report","task":"task_mine001","dispatch":"ctx_mine001","subject":"done","body":"b","outcome":"succeeded"}')" = True ] \
  && ok "a report on this terminal's own dispatch goes out" || no "the lane cannot report"

# 7. The lane's work: commit with the generated identity, take the base, push.
printf 'work\n' >lane-file
if git add lane-file && git commit -q -m "stub: lane commit" 2>/dev/null; then ok "commit with the generated git identity"
else no "cannot commit"; fi
if git pull -q 2>/dev/null && grep -q updated orca.yaml; then ok "git pull takes the base's new commit (it changes orca.yaml) and merges"
else no "cannot take the base's new commits"; fi
if lane-heavy true; then ok "lane-heavy runs a command in a slot"; else no "lane-heavy failed"; fi
git init -q sub && printf 'x\n' >sub/f && git -C sub add f && git -C sub commit -q -m sub &&
  git -C sub config core.fsmonitor "echo ran-outside >>'$(j out_marker)'; false"
mkdir -p .claude
printf '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"echo ran >>%s"}]}]}}\n' "$(j out_marker)" \
  >.claude/settings.local.json
git add sub .claude/settings.local.json 2>/dev/null; git commit -q -m "stub: planted" 2>/dev/null
if git push -q -u 2>/dev/null; then ok "git push -u through the broker"; else no "cannot push"; fi
if git status -sb | head -1 | grep -q '\.\.\.origin/' && ! git status -sb | head -1 | grep -q ahead; then
  ok "git status says up to date with origin after the push"; else no "no tracking ref after the push"; fi
git rev-parse HEAD >"$OUT/honest"

# 8. Hostile outbox entries, each sent to the broker's push (r2 B2, B5).
branch=$(git symbolic-ref --short HEAD)
O=$OUT
push() { rm -rf "$O/push.bundle"; "$@"; r=$(broker '{"request":"push"}'); rm -rf "$O/push.bundle"; [ "$r" = False ]; }
git init -q --bare "$O/evil.git"
printf '%s/.git/objects\n' "$(j shared)" >"$O/evil.git/objects/info/alternates"
mkdir -p "$(dirname "$O/evil.git/refs/heads/$branch")" && printf '%s\n' "$(j learned)" >"$O/evil.git/refs/heads/$branch"
host=$(j outbox_host)
git bundle create -q "$O/honest.bundle" "$branch" "^origin/main" 2>/dev/null
push ln -s "$host/evil.git" "$O/push.bundle" && ok "a link to a lane-made repo is refused" || no "a link to a repo was taken"
push ln -s "$host/honest.bundle" "$O/push.bundle" && ok "a link to a file is refused" || no "a link to a file was taken"
push sh -c "{ printf '# v2 git bundle\n%s refs/heads/%s\n\n' '$(j learned)' '$branch'; git pack-objects -q --stdout </dev/null; } >'$O/push.bundle'" \
  && ok "an id-only bundle naming the owner's unpushed commit is refused" || no "the id-only bundle was taken"
push mkfifo "$O/push.bundle" && ok "a FIFO is refused" || no "a FIFO was taken"
push python3 -I -c 'import sys; open(sys.argv[1], "wb").truncate(80 << 20)' "$O/push.bundle" && ok "an 80 MB file is refused" || no "a huge file was taken"
extra=$(git commit-tree -p HEAD -m extra "$(git write-tree)") && git update-ref refs/heads/extra "$extra"
push git bundle create -q "$O/push.bundle" "$branch" extra "^origin/main" && ok "a bundle with two refs is refused" || no "two refs taken"
git init -q /tmp/orphan && git -C /tmp/orphan commit -q --allow-empty -m orphan && git -C /tmp/orphan branch -q -m "$branch"
push git -C /tmp/orphan bundle create -q "$O/push.bundle" "$branch" && ok "an orphan is refused" || no "an orphan was taken"
mkdir -p .github/workflows && printf 'on: push\n' >.github/workflows/x.yml && git add .github && git commit -q -m "stub: workflow"
push git bundle create -q "$O/push.bundle" "$branch" "^origin/main" && ok "a push that adds a CI workflow is refused" || no "a CI change was pushed"
git reset -q --hard "$(cat "$O/honest")"
before=$(git rev-parse HEAD); git reset -q --hard HEAD~1; printf 'rewritten\n' >rw && git add rw && git commit -q -m rewrite
push git bundle create -q "$O/push.bundle" "$branch" "^origin/main" && ok "a rewritten branch (not a fast-forward) is refused" || no "a rewrite was pushed"
git reset -q --hard "$before"

# 9. A port opened inside is not reachable from the host; the test probes while this waits.
python3 -I -c '
import socket, sys, time, os
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("0.0.0.0", int(sys.argv[1]))); s.listen(4)
open(sys.argv[2], "w").close()
end = time.time() + 30
while time.time() < end and not os.path.exists(sys.argv[3]): time.sleep(0.2)' \
  "$(j inbound_port)" "$O/listening" /run/lane/inbox/probed
ok "listened on a port inside while the host probed it"
exit "$bad"
