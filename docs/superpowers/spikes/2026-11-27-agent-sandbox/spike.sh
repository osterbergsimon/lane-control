#!/usr/bin/env bash
# SBX-1 spike, round 3: the design's launch chain, mounts, network filter, broker checks, resume and history check,
# in small, with a stub agent (stub-agent.sh, a shell script, never a real agent or gateway).
#
#   docs/superpowers/spikes/2026-11-27-agent-sandbox/spike.sh [scratch-dir]
#
# The launch is the design's, not a copy of its argv: env -i with the allowlist -> systemd-run --user --scope (when a
# user manager exists) -> pasta (own network and user namespace; -t/-u/-U none; only one port forwarded) -> bash
# opening bwrap's --info-fd and --block-fd by path (pasta closes inherited fds) -> bwrap --cap-drop ALL with the
# spec's mounts. Outside, before the block fd is released: the host pid is found by bwrap's mount namespace, and
# the namespace's nft output filter is set through nsenter. The broker's push and fetch are spike_helpers.py plus
# the git commands the spec names, run outside after the stub's run.
# Runs (each on its own fixture under the scratch dir, default a new mktemp -d):
#   control   no sandbox, in the Orca worktree as today: leaks, and host git status runs a planted program
#   caps      the design's launch without --cap-drop ALL: a read-only bind is remounted and its host file written
#   naive     round 2's start (bwrap from the owner's environment, --clearenv --setenv): the token in
#             /proc/1/environ and the gateway key in host argv
#   sandbox   the design: no BAD line; host argv never shows the key; a port opened inside is not reachable from
#             the host; every hostile outbox entry refused and the round-2 import shown taking two of them; the
#             honest push accepted; host git runs nothing; resume reuses the clone; an unpushed or dirty clone is not
#             ended; a lost clone is recreated from the pushed branch
#   refuse    history checks: a tracked private file, one added and removed on a merged side branch, a nested
#             sub/.env.local, one committed to the base after the lane started; a tracked .env.example is accepted
# Only /nix/store is mounted, never /nix/var. Needs unprivileged user namespaces; nothing privileged or setuid.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
S=${1:-$(mktemp -d "${TMPDIR:-/tmp}/sbx-spike.XXXXXX")}
mkdir -p "$S"; S=$(cd "$S" && pwd)
store() { nix build --no-link --print-out-paths "$1" | head -1; }
BWRAP=${BWRAP:-$(store nixpkgs#bubblewrap)/bin/bwrap}
PASTA=${PASTA:-$(store nixpkgs#passt)/bin/pasta}
UL=$(store nixpkgs#util-linux.bin)/bin
NFT=$(store nixpkgs#nftables)/bin/nft
IP=$(store nixpkgs#iproute2)/bin/ip
H_PY=(python3 -I "$here/spike_helpers.py")
MARK="FAKE-FLOOR-$$-$RANDOM"
export FAKE_OWNER_TOKEN="fake-token-$RANDOM"        # the owner's environment: never inside
KEY="fake-gateway-key-$$-$RANDOM"                    # the one secret the lane's agent gets, by environment only
P0=$((40000 + RANDOM % 9000)); OPEN_PORT=$P0; CLOSED_PORT=$((P0+1)); UDP_PORT=$((P0+2)); HOST_PORT=$((P0+3))
INBOUND_PORT=$((P0+4)); ABSTRACT="sbx-spike-$$"
GIT_ID=(-c user.name=spike-owner -c user.email=owner@example.invalid)
CAP=$((10 << 20))
fail=0
expect() { if eval "$2"; then echo "   ok   $1"; else echo "   FAIL $1"; fail=1; fi; }

"${H_PY[@]}" serve "$OPEN_PORT" "$CLOSED_PORT" "$UDP_PORT" "$HOST_PORT" "$ABSTRACT" &
listener=$!
trap 'kill $listener 2>/dev/null || true' EXIT
"${H_PY[@]}" host-addrs "$IP" "$S/addrs" | sed 's/^/   /'
printf '%s\n' "$KEY" >"$S/key"
sleep 1

# --- the owner's side ----------------------------------------------------------------------------------------------
fixture() {
  R=$1; H="$R/home"; ORIGIN="$R/origin.git"; REPO="$R/repo"; WT="$R/orca/sbx-1"
  L="$R/state/sandbox/sbx-1"; CLONE="$L/repo"; QUAR="$L/quarantine.git"; GEN="$L/gen"; RUN="$L/run"
  BROKER="$L/broker"; OUT_MARKER="$R/ran-outside"
  mkdir -p "$H/.ssh" "$H/.config/lane-control" "$H/.local/share/opencode" "$H/.claude" "$H/.config/gh" "$H/.config/orca"
  local f
  for f in .ssh/id_ed25519 .config/lane-control/machine.conf .local/share/opencode/auth.json \
           .claude/.credentials.json .config/gh/hosts.yml .config/orca/orca-runtime.json
  do printf '%s %s\n' "$MARK" "$f" >"$H/$f"; done
  printf '[user]\n\tname = spike-owner\n\temail = owner@example.invalid\n' >"$H/.gitconfig"
  git init -q --bare -b main "$ORIGIN"; git init -q -b main "$REPO"
  printf 'base\n' >"$REPO/orca.yaml"; printf 'src\n' >"$REPO/src.txt"
  git -C "$REPO" add . && git -C "$REPO" "${GIT_ID[@]}" commit -q -m base
  git -C "$REPO" remote add origin "$ORIGIN" && git -C "$REPO" push -q origin main
  mkdir -p "$REPO/fixtures/customers"
  printf '%s customer data\n' "$MARK" >"$REPO/fixtures/customers/a.csv"   # untracked private data
  printf 'KEY=%s\n' "$MARK" >"$REPO/.env"
  git -C "$REPO" switch -q -c lane/other
  printf '%s other lane\n' "$MARK" >"$REPO/other.txt"
  git -C "$REPO" add other.txt && git -C "$REPO" "${GIT_ID[@]}" commit -q -m other
  OTHER_SHA=$(git -C "$REPO" rev-parse HEAD); git -C "$REPO" switch -q main
  printf '%s unpushed\n' "$MARK" >"$REPO/wip.txt"; git -C "$REPO" add wip.txt
  WIP_SHA=$(git -C "$REPO" "${GIT_ID[@]}" commit-tree -p HEAD -m wip "$(git -C "$REPO" write-tree)")
  git -C "$REPO" rm -q --cached wip.txt; rm "$REPO/wip.txt"
  git -C "$REPO" update-ref refs/owner/wip "$WIP_SHA"                   # the owner's never-pushed work
  git -C "$REPO" worktree add -q -b lane/sbx-1 "$WT" main
  cp "$here/stub-agent.sh" "$R/stub-agent.sh"
  PRIVATE=(fixtures/customers)
}

# prepare: resume the lane's clone if it exists; else recreate it from the pushed branch; else clone the base alone.
# Every delivered commit's paths are checked first (all depths, merges and side branches).
prepare() {
  mkdir -p "$GEN" "$RUN/inbox" "$RUN/outbox" "$BROKER"
  if [ -d "$CLONE" ]; then echo "   prepare: resume: the lane's clone is reused, no git run in it"; return 0; fi
  if git -C "$REPO" rev-parse -q --verify refs/lanes/sbx-1 >/dev/null; then
    git clone -q --no-local --no-checkout --single-branch --branch main --no-tags "$REPO" "$CLONE"
    git -C "$CLONE" fetch -q "$REPO" refs/lanes/sbx-1:refs/heads/lane/sbx-1
    git -C "$CLONE" symbolic-ref HEAD refs/heads/lane/sbx-1
    echo "   prepare: recreated from the pushed branch (checked out inside the sandbox)"
  else
    git clone -q --no-local --single-branch --branch main --no-tags "$REPO" "$CLONE"
    git -C "$CLONE" checkout -q -b lane/sbx-1
    git clone -q --bare --no-local --single-branch --branch main --no-tags "$REPO" "$QUAR"
    git -C "$REPO" rev-parse main >"$L/base"; git -C "$REPO" rev-parse main >"$L/delivered"
  fi
  git -C "$CLONE" remote remove origin
  git -C "$CLONE" update-ref refs/remotes/origin/main main
  if ! "${H_PY[@]}" history-hits "$CLONE" --all -- "${PRIVATE[@]}" >"$L/hits"; then
    echo "   prepare: refused: $(cat "$L/hits")"; rm -rf "$CLONE"; return 1
  fi
  printf '[user]\n\tname = %s\n\temail = %s\n' "$(git config -f "$H/.gitconfig" user.name)" \
    "$(git config -f "$H/.gitconfig" user.email)" >"$GEN/gitconfig"
  printf '{"model": "gateway/chat"}\n' >"$GEN/opencode.json"
  printf 'nameserver 169.254.1.53\n' >"$GEN/resolv.conf"
  cp "$S/addrs" "$RUN/inbox/addrs"
}

# broker fetch: the base's new commits, checked like a clone, to the inbox; the quarantine learns them too.
broker_fetch() {
  local last; last=$(cat "$L/delivered")
  if ! "${H_PY[@]}" history-hits "$REPO" main "^$last" -- "${PRIVATE[@]}" >"$L/hits"; then
    echo "   fetch: refused: $(cat "$L/hits")"; return 1
  fi
  git -C "$REPO" bundle create -q "$RUN/inbox/in.bundle" main "^$last"
  git -C "$QUAR" fetch -q "$REPO" main:refs/heads/main
  git -C "$REPO" rev-parse main >"$L/delivered"
}

# broker push NAME: never hands git a lane-controlled path; quarantine first; one ref, from the recorded base,
# fast-forward, no CI change; then into the shared repo and to origin.
broker_push() {
  local name=$1 copy="$BROKER/in.bundle" heads new
  rm -f "$copy"
  "${H_PY[@]}" safe-copy "$RUN/outbox/$name" "$copy" "$CAP" >"$BROKER/why" || { cat "$BROKER/why"; return 1; }
  heads=$(git -C "$QUAR" bundle list-heads "$copy" 2>/dev/null) || { echo "refused: not a bundle"; return 1; }
  [ "$(printf '%s\n' "$heads" | wc -l)" = 1 ] && [ "${heads#* }" = refs/heads/lane/sbx-1 ] ||
    { echo "refused: the bundle must hold exactly refs/heads/lane/sbx-1"; return 1; }
  git -C "$QUAR" -c fetch.fsckObjects=true -c transfer.fsckObjects=true fetch -q --no-tags --no-write-fetch-head \
    "$copy" "+refs/heads/lane/sbx-1:refs/incoming" 2>/dev/null ||
    { echo "refused: the quarantine cannot import it (missing objects or fsck)"; return 1; }
  new=$(git -C "$QUAR" rev-parse refs/incoming)
  git -C "$QUAR" merge-base --is-ancestor "$(cat "$L/base")" "$new" ||
    { echo "refused: does not descend from the lane's base"; return 1; }
  if git -C "$QUAR" rev-parse -q --verify refs/heads/lane >/dev/null; then
    git -C "$QUAR" merge-base --is-ancestor refs/heads/lane "$new" || { echo "refused: not a fast-forward"; return 1; }
  fi
  if ! "${H_PY[@]}" ci-hits "$QUAR" "$new" --not refs/heads/main $(git -C "$QUAR" rev-parse -q --verify refs/heads/lane) \
       >"$BROKER/why"; then
    [ -e "$L/ci-approved-$new" ] || { echo "refused: changes CI configuration ($(cat "$BROKER/why"))"; return 1; }
  fi
  git -C "$QUAR" update-ref refs/heads/lane "$new"
  git -C "$REPO" fetch -q --no-tags "$QUAR" refs/heads/lane:refs/lanes/sbx-1 &&
    git -C "$REPO" push -q origin refs/lanes/sbx-1:refs/heads/lane/sbx-1 && echo "accepted ${new:0:12}"
}

stub_env() {
  local floor="$H/.ssh/id_ed25519:$H/.config/lane-control/machine.conf:$H/.local/share/opencode/auth.json"
  floor+=":$H/.claude/.credentials.json:$H/.config/gh/hosts.yml:$H/.config/orca/orca-runtime.json"
  floor+=":$REPO/fixtures/customers/a.csv:$REPO/.env:$REPO/other.txt"
  spike_env=(SPIKE_HOME="$H" SPIKE_WT="$WT" SPIKE_SHARED="$REPO" SPIKE_MARK="$MARK" SPIKE_FLOOR="$floor"
    SPIKE_FOREIGN="$OTHER_SHA:$WIP_SHA" SPIKE_RO="$H/.gitconfig:$H/.config/opencode/opencode.json:/etc/resolv.conf:/run/lane/stub-agent.sh"
    SPIKE_OPEN_PORT="$OPEN_PORT" SPIKE_CLOSED_PORT="$CLOSED_PORT" SPIKE_UDP_PORT="$UDP_PORT" SPIKE_HOST_PORT="$HOST_PORT"
    SPIKE_INBOUND_PORT="$INBOUND_PORT" SPIKE_ABSTRACT="$ABSTRACT" SPIKE_RUN=/run/lane SPIKE_OUT_MARKER="$OUT_MARKER"
    SPIKE_LEARNED="$WIP_SHA" SPIKE_OUTBOX_HOST="$RUN/outbox" SPIKE_NFT="$NFT" SPIKE_MODE="${1:-lane}"
    SPIKE_PUSHED="$(git -C "$REPO" rev-parse -q --verify refs/lanes/sbx-1 || true)")
}

# launch [nocap] [naive]: the design's chain. Prints the stub's output to $R/out.
launch() {
  local nocap=${1:-0} naive=${2:-0} path="" c d hp
  for c in bash git env grep tr head ln rm mkdir cat python3 printf id cut wc dirname mkfifo; do
    d=$(dirname "$(readlink -f "$(command -v "$c")")"); case ":$path:" in *":$d:"*) ;; *) path=${path:+$path:}$d ;; esac
  done
  path+=":$UL"
  local bw=(--die-with-parent --unshare-pid --unshare-ipc --unshare-uts --unshare-cgroup-try)
  [ "$nocap" = 1 ] || bw+=(--cap-drop ALL)
  [ "$(cat /proc/sys/dev/tty/legacy_tiocsti 2>/dev/null)" = 0 ] || bw+=(--new-session)
  bw+=(--unsetenv DBUS_SESSION_BUS_ADDRESS --unsetenv XDG_RUNTIME_DIR
       --ro-bind /nix/store /nix/store
       --ro-bind-try /usr /usr --ro-bind-try /bin /bin --ro-bind-try /lib /lib --ro-bind-try /lib64 /lib64
       --proc /proc --dev /dev --tmpfs /tmp --tmpfs /run --dir /run/lane
       --tmpfs /etc --ro-bind "$GEN/resolv.conf" /etc/resolv.conf)
  for c in passwd group hosts nsswitch.conf ssl pki static localtime zoneinfo; do bw+=(--ro-bind-try "/etc/$c" "/etc/$c"); done
  bw+=(--tmpfs "$H" --ro-bind "$GEN/gitconfig" "$H/.gitconfig"
       --dir "$H/.config/opencode" --ro-bind "$GEN/opencode.json" "$H/.config/opencode/opencode.json"
       --bind "$CLONE" "$WT"
       --ro-bind "$RUN/inbox" /run/lane/inbox --bind "$RUN/outbox" /run/lane/outbox
       --ro-bind "$R/stub-agent.sh" /run/lane/stub-agent.sh --chdir "$WT"
       --info-fd 3 --block-fd 4)
  local env_list=(HOME="$H" PATH="$path" LANG=C.UTF-8 FAKE_GATEWAY_KEY="$KEY" "${spike_env[@]}")
  local scope=()
  if [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ] && command -v systemd-run >/dev/null; then
    env_list+=(DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR")
    scope=("$(command -v systemd-run)" --user --scope -q -p MemoryMax=4G --)
  fi
  local net=("$PASTA" --config-net -q -f --no-map-gw --dns-forward 169.254.1.53 -t none -u none -U none -T "$OPEN_PORT" --)
  local opener=("$(readlink -f "$(command -v bash)")" -c 'i=$1 b=$2; shift 2; exec "$@" 3>"$i" 4<"$b"' opener)
  mkdir -p "$L/launch"; rm -f "$L/launch/info.json" "$L/launch/block"; mkfifo "$L/launch/block"
  exec 7<>"$L/launch/block"
  local start=(env -i "${env_list[@]}") kv
  if [ "$naive" = 1 ]; then                       # round 2: the owner's environment, values as bwrap arguments
    local sets=(--clearenv)
    for kv in "${env_list[@]}"; do sets+=(--setenv "${kv%%=*}" "${kv#*=}"); done
    start=(env); bw=("${sets[@]}" "${bw[@]}")
  fi
  "${start[@]}" "${scope[@]}" "${net[@]}" "${opener[@]}" "$L/launch/info.json" "$L/launch/block" \
    "$BWRAP" "${bw[@]}" bash /run/lane/stub-agent.sh >"$R/out" 2>&1 &
  local bg=$!
  for c in $(seq 150); do grep -q child-pid "$L/launch/info.json" 2>/dev/null && break; sleep 0.1; done
  read -r mnt cpid < <(python3 -I -c 'import json,sys; j=json.load(open(sys.argv[1])); print(j["mnt-namespace"], j["child-pid"])' "$L/launch/info.json")
  hp=$("${H_PY[@]}" host-pid "$mnt" "$cpid")
  "${H_PY[@]}" nft-rules "$RUN/inbox/addrs" "$L/launch/nft" >/dev/null
  "$UL/nsenter" --preserve-credentials -t "$hp" -U -n "$NFT" -f "$L/launch/nft"
  LAUNCH_SCOPE=${scope[*]:+scope}; LAUNCH_HOSTPID=$hp
  echo go >&7
  if [ "${SPIKE_PROBE:-0}" = 1 ]; then
    for c in $(seq 200); do [ -e "$RUN/outbox/listening" ] && break; sleep 0.1; done
    INBOUND=$("${H_PY[@]}" inbound "$INBOUND_PORT" "$S/addrs")
    ARGV_HITS=$("${H_PY[@]}" cmdline-scan "$S/key")
    : >"$RUN/inbox/probed"
  fi
  set +e; wait "$bg"; LAUNCH_RC=$?; set -e
  exec 7>&-
}

echo "== control: no sandbox, in the Orca worktree as today (leaks expected)"
fixture "$S/control"; prepare >/dev/null; broker_fetch >/dev/null 2>&1 || true
git -C "$REPO" bundle create -q "$RUN/inbox/in.bundle" main
stub_env; spike_env=("${spike_env[@]/SPIKE_RUN=\/run\/lane/SPIKE_RUN=$RUN}")
set +e; (cd "$WT" && env HOME="$H" "${spike_env[@]/SPIKE_WT=*/SPIKE_WT=$WT}" FAKE_GATEWAY_KEY="$KEY" bash "$R/stub-agent.sh" \
  >"$R/out" 2>&1); control=$?; set -e
grep -c '^BAD' "$R/out" | sed 's/^/   BAD lines: /'
git -C "$WT" status --short >/dev/null 2>&1 || true
expect "control has leaks" '[ "$control" -ne 0 ]'
expect "host git status in a tree the agent wrote runs its planted program (round 1 B2)" '[ -s "$OUT_MARKER" ]'

echo "== caps: the design's launch without --cap-drop ALL (round 2 B1 reproduced)"
fixture "$S/caps"; prepare >/dev/null; stub_env; launch 1
expect "a read-only bind was remounted and its host file written" 'grep -q WRITTEN-BY-SANDBOX "$GEN/gitconfig"'

echo "== naive: round 2's start, bwrap from the owner's environment with --clearenv --setenv (round 2 M2 reproduced)"
fixture "$S/naive"; prepare >/dev/null; printf 'updated\n' >"$REPO/orca.yaml"
git -C "$REPO" "${GIT_ID[@]}" commit -q -am "base moves on" && broker_fetch
stub_env; SPIKE_PROBE=1 launch 0 1
{ grep '^BAD' "$R/out" || true; } | sed 's/^/   /'
expect "the token is readable in /proc/1/environ, and nothing else leaks inside" \
  '[ "$(grep -c "^BAD" "$R/out")" = 1 ] && grep -q "^BAD  another process" "$R/out"'
expect "the gateway key is in host processes' world-readable argv ($ARGV_HITS)" '[ "$ARGV_HITS" -gt 0 ]'

echo "== sandbox: the design"
fixture "$S/sandbox"; prepare; printf 'updated\n' >"$REPO/orca.yaml"
git -C "$REPO" "${GIT_ID[@]}" commit -q -am "base moves on" && git -C "$REPO" push -q origin main && broker_fetch
stub_env; SPIKE_PROBE=1 launch
sed "s#$S#\$S#g; s/^/   /" "$R/out"
echo "   launch: ${LAUNCH_SCOPE:-no user manager, no scope}; host pid found by mount namespace: ${LAUNCH_HOSTPID:+yes}"
expect "no BAD line inside" '[ "$LAUNCH_RC" = 0 ]'
expect "the gateway key is in no host process's argv (round 2 M2)" '[ "$ARGV_HITS" = 0 ]'
expect "a port opened inside is reachable from none of the host's addresses (round 2 M1)" '[ "$INBOUND" = 0 ]'
echo "-- outside: the round-2 import, shown taking two hostile entries (round 2 B2 reproduced)"
expect "round 2's fetch follows a link to the lane's repo and imports the owner's unpushed work" \
  'git -C "$REPO" fetch -q --no-tags --no-recurse-submodules --no-write-fetch-head "$RUN/outbox/push-a" \
     refs/heads/lane/sbx-1:refs/ctl/a 2>/dev/null && [ "$(git -C "$REPO" rev-parse refs/ctl/a)" = "$WIP_SHA" ]'
expect "round 2's fetch imports an id-only bundle naming the owner's unpushed work" \
  'git -C "$REPO" -c fetch.fsckObjects=true fetch -q --no-tags --no-write-fetch-head "$RUN/outbox/push-c" \
     refs/heads/lane/sbx-1:refs/ctl/c 2>/dev/null && [ "$(git -C "$REPO" rev-parse refs/ctl/c)" = "$WIP_SHA" ]'
git -C "$REPO" update-ref -d refs/ctl/a; git -C "$REPO" update-ref -d refs/ctl/c
echo "-- outside: the design's broker"
for n in push-a push-b push-c push-d push-e push-f push-g; do
  r=$(broker_push "$n" || true); expect "$n refused: $r" 'case $r in refused*) true;; *) false;; esac'
done
r=$(broker_push push.bundle || true); expect "the honest push is accepted: $r" 'case $r in accepted*) true;; *) false;; esac'
r=$(broker_push push-h || true); expect "a push that adds a CI workflow is refused (round 2 B5): $r" 'case $r in *CI*) true;; *) false;; esac'
expect "origin's lane branch holds the lane's work and not the owner's unpushed commit" \
  'git -C "$ORIGIN" cat-file -e lane/sbx-1:lane-file && ! git -C "$ORIGIN" cat-file -e "$WIP_SHA" 2>/dev/null'
git -C "$WT" status --short >/dev/null 2>&1 || true; git -C "$REPO" status --short >/dev/null 2>&1 || true
git clone -q "$ORIGIN" "$R/review" && git -C "$R/review" checkout -q lane/sbx-1 && git -C "$R/review" status --short >/dev/null
expect "host git in the Orca worktree, the main checkout and a fresh checkout of the pushed branch ran nothing" '[ ! -e "$OUT_MARKER" ]'
echo "-- resume and lane end (round 2 B6)"
pushed=$(git -C "$REPO" rev-parse refs/lanes/sbx-1)
prepare; stub_env inspect; launch
expect "a relaunch reuses the clone: head is the pushed work, nothing unpushed" \
  'grep -q "inspect head=$pushed dirty=0 unpushed=0" "$R/out"'
stub_env edit; launch
expect "an unpushed commit and an uncommitted change are reported, so the lane is not ended" \
  'grep -q "dirty=1 unpushed=1" "$R/out"'
rm -rf "$CLONE"                                                    # the clone is lost (or the owner said discard)
prepare; stub_env inspect; launch
expect "a lost clone is recreated from the pushed branch" 'grep -q "inspect head=$pushed dirty=0 unpushed=0" "$R/out"'

echo "== refuse: history checks (round 2 B4, m2)"
fixture "$S/r1"; git -C "$REPO" add -f fixtures/customers/a.csv && git -C "$REPO" "${GIT_ID[@]}" commit -q -m tracked
set +e; out=$(prepare); rc=$?; set -e; expect "a tracked private file: $out" '[ "$rc" != 0 ]'
fixture "$S/r2"; git -C "$REPO" switch -q -c side; mkdir -p "$REPO/fixtures/customers"
printf '%s\n' "$MARK" >"$REPO/fixtures/customers/b.csv"; git -C "$REPO" add -f fixtures/customers/b.csv
git -C "$REPO" "${GIT_ID[@]}" commit -q -m add; git -C "$REPO" rm -q fixtures/customers/b.csv
git -C "$REPO" "${GIT_ID[@]}" commit -q -m remove; git -C "$REPO" switch -q main
git -C "$REPO" "${GIT_ID[@]}" merge -q --no-ff --no-edit side
expect "round 2's check (git log -- <path>) misses a private file added and removed on a merged side branch" \
  '[ -z "$(git -C "$REPO" log --format=%H main -- fixtures/customers)" ]'
set +e; out=$(prepare); rc=$?; set -e; expect "the full-history check refuses it: $out" '[ "$rc" != 0 ]'
fixture "$S/r3"; mkdir -p "$REPO/sub"; printf 'K=%s\n' "$MARK" >"$REPO/sub/.env.local"
git -C "$REPO" add -f sub/.env.local && git -C "$REPO" "${GIT_ID[@]}" commit -q -m nested
set +e; out=$(prepare); rc=$?; set -e; expect "a nested sub/.env.local: $out" '[ "$rc" != 0 ]'
fixture "$S/r4"; printf 'KEY=\n' >"$REPO/.env.example"; git -C "$REPO" add -f .env.example
git -C "$REPO" "${GIT_ID[@]}" commit -q -m template
set +e; prepare >/dev/null; rc=$?; set -e; expect "a tracked .env.example is accepted (template list)" '[ "$rc" = 0 ]'
fixture "$S/r5"; prepare >/dev/null
git -C "$REPO" add -f fixtures/customers/a.csv && git -C "$REPO" "${GIT_ID[@]}" commit -q -m "private, after prepare"
set +e; out=$(broker_fetch); rc=$?; set -e
expect "base commits after the lane started are checked before delivery: $out" '[ "$rc" != 0 ] && [ ! -s "$RUN/inbox/in.bundle" ]'

echo "== result (scratch: $S)"
[ "$fail" = 0 ] && { echo PASS; exit 0; }
echo FAIL; exit 1
