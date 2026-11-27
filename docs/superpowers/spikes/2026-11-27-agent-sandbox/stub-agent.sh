#!/usr/bin/env bash
# Stub "agent" for the SBX-1 spike. Not an agent: it never starts opencode, claude or any gateway. It tries what an
# agent's tools could try, does a lane's work, and leaves the attacks that spike.sh then runs against the broker.
# One line per check:  ok <check> (as the design wants) | BAD <check> (a leak, an escape, or the lane cannot work).
# Exit 0 when every line is ok. The control runs it with no sandbox and expects BAD lines.
#
# SPIKE_MODE: lane (default) | inspect (print head and dirty state for the broker, nothing else) | edit (leave one
# unpushed commit and one uncommitted change, then inspect).
# Inputs (environment): SPIKE_HOME, SPIKE_WT (the working tree as the agent sees it), SPIKE_SHARED (the shared
# repo's checkout), SPIKE_MARK (the text in every fake floor file), SPIKE_FLOOR (colon-separated floor paths),
# SPIKE_FOREIGN (shas the lane must not have), SPIKE_RO (read-only binds to attack), SPIKE_OPEN_PORT,
# SPIKE_CLOSED_PORT, SPIKE_UDP_PORT, SPIKE_HOST_PORT, SPIKE_INBOUND_PORT, SPIKE_ABSTRACT, SPIKE_RUN (inbox/, outbox/),
# SPIKE_OUT_MARKER (written if anything outside runs a planted program), SPIKE_LEARNED (a commit id of the owner's
# unpushed work that the lane has seen in a note), SPIKE_OUTBOX_HOST (the outbox's host path, readable from
# /proc/self/mountinfo), SPIKE_NFT (an nft binary in the store), SPIKE_PUSHED (the sha the broker has, for inspect).
set -u
bad=0
ok() { printf 'ok   %s\n' "$*"; }
no() { printf 'BAD  %s\n' "$*"; bad=1; }
cd "$SPIKE_WT" || { no "cannot enter the working tree"; exit 1; }
git rev-parse -q --verify HEAD >/dev/null && [ -z "$(git ls-files | head -1)" ] && git reset -q --hard HEAD  # recreated clone

inspect() {
  printf 'inspect head=%s dirty=%s unpushed=%s\n' "$(git rev-parse HEAD)" "$(git status --porcelain | wc -l)" \
    "$(git rev-list --count "${SPIKE_PUSHED:-HEAD}..HEAD" 2>/dev/null || echo unknown)"
}
case ${SPIKE_MODE:-lane} in
  inspect) inspect; exit 0 ;;
  edit) printf 'more\n' >>lane-file; git commit -qam "spike: unpushed" && printf 'wip\n' >>lane-file; inspect; exit 0 ;;
esac

# 1. The floor: by path, through a link, and by a search of everything visible.
IFS=: read -r -a floor <<<"$SPIKE_FLOOR"
for f in "${floor[@]}"; do
  if grep -qs -- "$SPIKE_MARK" "$f"; then no "read floor file $f"; else ok "floor file $f: nothing to read"; fi
done
ln -sf "${floor[0]}" planted-link
if grep -qs -- "$SPIKE_MARK" planted-link; then no "read the floor through a link"; else ok "a link to a floor file reads nothing"; fi
rm -f planted-link
hits=$(grep -rIl --exclude=stub-agent.sh -- "$SPIKE_MARK" "$SPIKE_HOME" "$SPIKE_WT" "$SPIKE_SHARED" /tmp /etc /run 2>/dev/null | head -3)
if [ -n "$hits" ]; then no "search found floor data: $(printf '%s' "$hits" | tr '\n' ' ')"
else ok "a recursive search of home, working tree, shared repo, /tmp, /etc and /run finds no floor data"; fi

# 2. The environment, also through /proc (bwrap's pid-1 helper keeps the environment it was started with).
if env | grep -q '^FAKE_OWNER_TOKEN='; then no "the owner's token is in the agent's environment"
else ok "the owner's token is not in the agent's environment"; fi
leak=0
for e in /proc/[0-9]*/environ; do tr '\0' '\n' <"$e" 2>/dev/null | grep -q '^FAKE_OWNER_TOKEN=' && leak=1; done
if [ "$leak" = 1 ]; then no "another process's environment shows the owner's token"; else ok "no visible process has the owner's token"; fi

# 3. Capabilities (review r2 B1): none; read-only binds cannot be remounted, written or unmounted; no nested user
#    namespace; the namespace's network filter cannot be changed.
cap=$(grep '^CapEff:' /proc/self/status | tr -d '[:space:]' | cut -d: -f2)
if [ "$cap" = 0000000000000000 ]; then ok "no capabilities (CapEff 0, uid $(id -u) inside)"; else no "capabilities left: CapEff $cap"; fi
IFS=: read -r -a ro <<<"$SPIKE_RO"
for f in "${ro[@]}"; do
  mount -o remount,bind,rw "$f" 2>/dev/null && no "remounted $f read-write"
  if { printf 'WRITTEN-BY-SANDBOX\n' >>"$f"; } 2>/dev/null; then no "wrote read-only $f"
  else ok "cannot remount or write $f"; fi
  umount "$f" 2>/dev/null && no "unmounted $f"
done
if unshare -Urm true 2>/dev/null; then no "made a nested user namespace"; else ok "cannot make a nested user namespace"; fi
if "$SPIKE_NFT" flush ruleset 2>/dev/null; then no "flushed the network filter"; else ok "cannot change the network filter"; fi

# 4. Sockets: nix daemon, host loopback TCP and UDP, abstract sockets, the host's other addresses.
sockets=$(python3 -I - "$SPIKE_OPEN_PORT" "$SPIKE_CLOSED_PORT" "$SPIKE_UDP_PORT" "$SPIKE_HOST_PORT" "$SPIKE_ABSTRACT" \
  "$SPIKE_RUN/inbox/addrs" <<'PY'
import socket, sys
op, cp, up, hp, name, addrs = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], sys.argv[6]
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
say(tcp("127.0.0.1", op), "the forwarded loopback port works (stands for Orca's hook port)")
say(not tcp("127.0.0.1", cp), "a host loopback TCP port that is not forwarded cannot be reached")
say(not udp(up), "a host loopback UDP port cannot be reached")
say(not unix("\0" + name), "a host abstract unix socket cannot be reached (X11's is one)")
hosts = [a for a in open(addrs).read().split() if a]
reached = sum(tcp(a, hp) for a in hosts)
say(reached == 0, "none of the host's other addresses reaches a host service")
PY
)
printf '%s\n' "$sockets"; case $sockets in *BAD*|"") bad=1 ;; esac

# 5. The shared repository: absent; other refs' objects not in the clone.
if [ -e "$SPIKE_SHARED/.git" ]; then no "the shared repository is visible"; else ok "the shared repository is not there"; fi
IFS=: read -r -a foreign <<<"$SPIKE_FOREIGN"
for sha in "${foreign[@]}"; do
  if git cat-file -e "$sha" 2>/dev/null; then no "object $sha is readable"
  else ok "object ${sha:0:12} (another lane's work or the owner's unpushed work) is not in the clone"; fi
done

# 6. The lane's work: commit with the generated identity, merge the base from the inbox bundle, push by bundle.
printf 'work\n' >lane-file
if git add lane-file && git commit -q -m "spike: lane commit" 2>/dev/null; then ok "commit with the generated git identity"
else no "cannot commit"; fi
if git fetch -q "$SPIKE_RUN/inbox/in.bundle" "main:refs/remotes/origin/main" 2>/dev/null &&
   git merge -q --no-edit origin/main 2>/dev/null && grep -q updated orca.yaml
then ok "fetch the base from the inbox bundle and merge it (it changes orca.yaml)"; else no "cannot take the base's new commits"; fi
# Planted for later host-side git and agents: a nested repo whose config runs a program, Claude settings with a hook.
git init -q sub && printf 'x\n' >sub/f && git -C sub add f && git -C sub commit -q -m sub &&
  git -C sub config core.fsmonitor "echo ran-outside >>'$SPIKE_OUT_MARKER'; false"
mkdir -p .claude
printf '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"echo ran >>%s"}]}]}}\n' "$SPIKE_OUT_MARKER" \
  >.claude/settings.local.json
git add sub .claude/settings.local.json 2>/dev/null; git commit -q -m "spike: planted" 2>/dev/null
branch=$(git symbolic-ref --short HEAD)
O="$SPIKE_RUN/outbox"
if git bundle create -q "$O/push.bundle" "$branch" "^origin/main" 2>/dev/null; then ok "push by bundle to the outbox"
else no "cannot write the push bundle"; fi

# 7. Hostile outbox entries for the broker (review r2 B2, B5). Each is refused outside; spike.sh checks.
git init -q --bare "$O/evil.git"                                              # a: link to a repo of the lane's
printf '%s/.git/objects\n' "$SPIKE_SHARED" >"$O/evil.git/objects/info/alternates"
mkdir -p "$(dirname "$O/evil.git/refs/heads/$branch")" && printf '%s\n' "$SPIKE_LEARNED" >"$O/evil.git/refs/heads/$branch"
ln -s "$SPIKE_OUTBOX_HOST/evil.git" "$O/push-a"
ln -s "$SPIKE_OUTBOX_HOST/push.bundle" "$O/push-b"                            # b: link to a file
{ printf '# v2 git bundle\n%s refs/heads/%s\n\n' "$SPIKE_LEARNED" "$branch"   # c: id-only bundle, empty pack
  git pack-objects -q --stdout </dev/null; } >"$O/push-c"
mkfifo "$O/push-d"                                                            # d: a FIFO
python3 -I -c 'import sys; open(sys.argv[1], "wb").truncate(20 << 20)' "$O/push-e"  # e: over the size cap
extra=$(git commit-tree -p HEAD -m extra "$(git write-tree)") && git update-ref refs/heads/extra "$extra" &&
  git bundle create -q "$O/push-f" "$branch" extra "^origin/main" 2>/dev/null  # f: two refs, both with new commits
git init -q /tmp/orphan && git -C /tmp/orphan commit -q --allow-empty -m orphan &&
  git -C /tmp/orphan branch -q -m "$branch" && git -C /tmp/orphan bundle create -q "$O/push-g" "$branch"  # g: not from the base
mkdir -p .github/workflows && printf 'on: push\n' >.github/workflows/x.yml    # h: a CI workflow change
git add .github && git commit -q -m "spike: workflow" &&
  git bundle create -q "$O/push-h" "$branch" "^origin/main" && git reset -q --hard HEAD~1
ok "hostile outbox entries written (a link to a repo, a link to a file, an id-only bundle, a FIFO, a huge file," \
   "two refs, an orphan, a workflow change)"

# 8. A port opened inside is not reachable from the host (review r2 M1); spike.sh probes while this waits.
python3 -I -c '
import socket, sys, time, os
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("0.0.0.0", int(sys.argv[1]))); s.listen(4)
open(sys.argv[2], "w").close()
end = time.time() + 20
while time.time() < end and not os.path.exists(sys.argv[3]): time.sleep(0.2)' \
  "$SPIKE_INBOUND_PORT" "$O/listening" "$SPIKE_RUN/inbox/probed"
ok "listened on a port inside while the host probed it"

exit "$bad"
