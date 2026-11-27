#!/usr/bin/env bash
# SBX-1 spike, round 2: the design's own mounts, network and git flow, in small, with a stub agent (stub-agent.sh, a
# shell script, never a real agent or gateway).
#
#   docs/superpowers/spikes/2026-11-27-agent-sandbox/spike.sh [scratch-dir]
#
# Each run gets a fresh fixture under the scratch dir (default: a new mktemp -d): a fake owner HOME with a fake
# floor, a bare origin, the shared repo (main checkout with untracked private data and .env, another lane's branch,
# an unpushed ref) and this lane's Orca worktree of it. `prepare` (outside, as the design's `lane prepare
# --sandbox`) makes the lane's own single-branch clone, refuses when a private path is in its history, and writes the
# generated HOME files. Then:
#   control  the stub with no sandbox, in the Orca worktree as today: leaks expected, and a host `git status`
#            there runs the program the stub planted (review B2)
#   naive    the designed sandbox, but bwrap started with the owner's environment: exactly one leak expected, the
#            token in /proc/1/environ (bwrap's pid-1 helper)
#   sandbox  as designed, from `env -i`: no leak; then, outside, the broker's steps: import the push bundle with
#            fsck, push to origin fast-forward, host `git status` in the Orca worktree, the main checkout and a fresh
#            checkout of the pushed branch (the reviewer's input): none runs the planted program. Host git in the
#            lane's own clone does (shown last): why no host tool may run git there.
#   refuse   a fixture whose private file is tracked: prepare refuses the lane.
# bwrap and pasta are pinned store paths (BWRAP, PASTA, or `nix build nixpkgs#bubblewrap nixpkgs#passt`). Only
# /nix/store is mounted, never /nix/var. Needs unprivileged user namespaces; no setuid helper, no privileges.
# Exit 0 when every run comes out as above.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
S=${1:-$(mktemp -d "${TMPDIR:-/tmp}/sbx-spike.XXXXXX")}
mkdir -p "$S"; S=$(cd "$S" && pwd)
BWRAP=${BWRAP:-$(nix build --no-link --print-out-paths nixpkgs#bubblewrap)/bin/bwrap}
PASTA=${PASTA:-$(nix build --no-link --print-out-paths nixpkgs#passt)/bin/pasta}
MARK="FAKE-FLOOR-$$-$RANDOM"
export FAKE_OWNER_TOKEN="fake-token-$RANDOM"   # stands for a gateway key or GH_TOKEN in the owner's environment
OPEN_PORT=$((40000 + RANDOM % 10000)); CLOSED_PORT=$((OPEN_PORT + 1)); ABSTRACT="sbx-spike-$$"
GIT_ID=(-c user.name=spike-owner -c user.email=owner@example.invalid)
fail=0
expect() { if eval "$2"; then echo "   ok   $1"; else echo "   FAIL $1"; fail=1; fi; }

# Listeners on the host: a forwarded port (Orca's hook port in the design), one that is not, an abstract socket.
python3 -c '
import socket, sys, time
socks = []
for p in (int(sys.argv[1]), int(sys.argv[2])):
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", p)); s.listen(8)
    socks.append(s)
u = socket.socket(socket.AF_UNIX); u.bind("\0" + sys.argv[3]); u.listen(8); socks.append(u)
time.sleep(600)' "$OPEN_PORT" "$CLOSED_PORT" "$ABSTRACT" &
listener=$!
trap 'kill $listener 2>/dev/null || true' EXIT
sleep 1

# fixture <dir>: the owner's side of the world.
fixture() {
  R=$1; H="$R/home"; ORIGIN="$R/origin.git"; REPO="$R/repo"; WT="$R/orca/sbx-1"
  L="$R/state/sandbox/sbx-1"; CLONE="$L/repo"; GEN="$L/gen"; RUN="$L/run"; OUT_MARKER="$R/ran-outside"
  mkdir -p "$H/.ssh" "$H/.config/lane-control" "$H/.local/share/opencode" "$H/.claude" "$H/.config/gh" "$H/.config/orca"
  local f
  for f in .ssh/id_ed25519 .config/lane-control/machine.conf .local/share/opencode/auth.json \
           .claude/.credentials.json .config/gh/hosts.yml .config/orca/orca-runtime.json
  do printf '%s %s\n' "$MARK" "$f" >"$H/$f"; done
  printf '[user]\n\tname = spike-owner\n\temail = owner@example.invalid\n' >"$H/.gitconfig"
  git init -q --bare -b main "$ORIGIN"
  git init -q -b main "$REPO"
  printf 'base\n' >"$REPO/orca.yaml"; printf 'src\n' >"$REPO/src.txt"
  git -C "$REPO" add . && git -C "$REPO" "${GIT_ID[@]}" commit -q -m base
  git -C "$REPO" remote add origin "$ORIGIN" && git -C "$REPO" push -q origin main
  BASE=$(git -C "$REPO" rev-parse HEAD)
  mkdir -p "$REPO/fixtures/customers"
  printf '%s customer data\n' "$MARK" >"$REPO/fixtures/customers/a.csv"   # untracked private data
  printf 'KEY=%s\n' "$MARK" >"$REPO/.env"
  git -C "$REPO" switch -q -c lane/other
  printf '%s other lane\n' "$MARK" >"$REPO/other.txt"
  git -C "$REPO" add other.txt && git -C "$REPO" "${GIT_ID[@]}" commit -q -m other
  OTHER_SHA=$(git -C "$REPO" rev-parse HEAD)
  git -C "$REPO" switch -q main
  WIP_SHA=$(printf '%s unpushed\n' "$MARK" | git -C "$REPO" hash-object -w --stdin)
  git -C "$REPO" update-ref refs/owner/wip "$(git -C "$REPO" "${GIT_ID[@]}" commit-tree -m wip \
    "$(printf '100644 blob %s\twip.txt\n' "$WIP_SHA" | git -C "$REPO" mktree)")"
  git -C "$REPO" worktree add -q -b lane/sbx-1 "$WT" main
  cp "$here/stub-agent.sh" "$R/stub-agent.sh"
  PRIVATE=(fixtures/customers .env)
}

# prepare: the lane's own clone and generated files, outside the sandbox, before the agent exists.
prepare() {
  mkdir -p "$GEN" "$RUN/inbox" "$RUN/outbox"
  git clone -q --no-local --single-branch --branch main --no-tags "$REPO" "$CLONE"
  git -C "$CLONE" checkout -q -b lane/sbx-1
  git -C "$CLONE" remote remove origin
  git -C "$CLONE" update-ref refs/remotes/origin/main main
  if [ -n "$(git -C "$CLONE" log --format=%H -- "${PRIVATE[@]}" | head -1)" ]; then
    echo "   prepare: refused: a private path is in the history of the lane's base"; return 1
  fi
  printf '{"model": "gateway/chat"}\n' >"$GEN/opencode.json"
  printf '[user]\n\tname = %s\n\temail = %s\n' "$(git config -f "$H/.gitconfig" user.name)" \
    "$(git config -f "$H/.gitconfig" user.email)" >"$GEN/gitconfig"
  printf 'nameserver 169.254.1.53\n' >"$GEN/resolv.conf"
  # The base moves on after the lane started; the broker's fetch hands the new commits over as a bundle.
  printf 'updated\n' >"$REPO/orca.yaml"
  git -C "$REPO" "${GIT_ID[@]}" commit -q -am "base moves on" && git -C "$REPO" push -q origin main
  git -C "$REPO" bundle create -q "$RUN/inbox/in.bundle" main "^$BASE"
}

stub_env() {
  local floor="$H/.ssh/id_ed25519:$H/.config/lane-control/machine.conf:$H/.local/share/opencode/auth.json"
  floor+=":$H/.claude/.credentials.json:$H/.config/gh/hosts.yml:$H/.config/orca/orca-runtime.json"
  floor+=":$REPO/fixtures/customers/a.csv:$REPO/.env:$REPO/other.txt"
  spike_env=(SPIKE_HOME="$H" SPIKE_WT="$WT" SPIKE_SHARED="$REPO" SPIKE_MARK="$MARK" SPIKE_FLOOR="$floor"
             SPIKE_FOREIGN="$OTHER_SHA:$WIP_SHA" SPIKE_OPEN_PORT="$OPEN_PORT" SPIKE_CLOSED_PORT="$CLOSED_PORT"
             SPIKE_ABSTRACT="$ABSTRACT" SPIKE_RUN="$1" SPIKE_OUT_MARKER="$OUT_MARKER")
}

# box: the design's launch, exactly: pasta (own network namespace, only the hook port forwarded, no host loopback,
# DNS forwarded) around bwrap (mount plan of the spec), both from env -i.
box() {
  local path="" c d kv
  for c in bash git env grep tr head ln rm mkdir cat python3 printf; do
    d=$(dirname "$(readlink -f "$(command -v "$c")")")
    case ":$path:" in *":$d:"*) ;; *) path=${path:+$path:}$d ;; esac
  done
  args=(--die-with-parent --unshare-pid --unshare-ipc --unshare-uts --unshare-cgroup-try)
  [ "$(cat /proc/sys/dev/tty/legacy_tiocsti 2>/dev/null)" = 0 ] || args+=(--new-session)
  args+=(--ro-bind /nix/store /nix/store
         --ro-bind-try /usr /usr --ro-bind-try /bin /bin --ro-bind-try /lib /lib --ro-bind-try /lib64 /lib64
         --proc /proc --dev /dev --tmpfs /tmp --tmpfs /run --dir /run/lane
         --tmpfs /etc --ro-bind "$GEN/resolv.conf" /etc/resolv.conf)
  for c in passwd group hosts nsswitch.conf ssl pki static localtime zoneinfo; do
    args+=(--ro-bind-try "/etc/$c" "/etc/$c")
  done
  args+=(--tmpfs "$H" --ro-bind "$GEN/gitconfig" "$H/.gitconfig"
         --dir "$H/.config/opencode" --ro-bind "$GEN/opencode.json" "$H/.config/opencode/opencode.json"
         --bind "$CLONE" "$WT"
         --ro-bind "$RUN/inbox" /run/lane/inbox --bind "$RUN/outbox" /run/lane/outbox
         --ro-bind "$R/stub-agent.sh" /run/lane/stub-agent.sh
         --clearenv --setenv HOME "$H" --setenv PATH "$path" --setenv LANG C.UTF-8 --chdir "$WT")
  for kv in "${spike_env[@]}"; do args+=(--setenv "${kv%%=*}" "${kv#*=}"); done
  net=("$PASTA" --config-net -q --no-map-gw --dns-forward 169.254.1.53 -T "$OPEN_PORT" --)
}
clean_env=(env -i PATH=/nonexistent)

echo "== control: stub without a sandbox, in the Orca worktree as today (leaks expected)"
fixture "$S/control"; stub_env "$RUN"; mkdir -p "$RUN/inbox" "$RUN/outbox"
git -C "$REPO" bundle create -q "$RUN/inbox/in.bundle" main
set +e; env HOME="$H" "${spike_env[@]}" bash "$R/stub-agent.sh" >"$R/out" 2>&1; control=$?; set -e
grep -c '^BAD' "$R/out" | sed 's/^/   BAD lines: /'
git -C "$WT" status --short >/dev/null 2>&1 || true
expect "control has leaks" '[ "$control" -ne 0 ]'
expect "host git status in a worktree the agent wrote runs its planted program (review B2)" '[ -s "$OUT_MARKER" ]'

echo "== naive: designed sandbox, bwrap started with the owner's environment (one leak expected)"
fixture "$S/naive"; prepare; stub_env /run/lane; box
set +e; "${net[@]}" "$BWRAP" "${args[@]}" bash /run/lane/stub-agent.sh >"$R/out" 2>&1; set -e
grep '^BAD' "$R/out" | sed 's/^/   /'
expect "exactly the /proc/1/environ leak" \
  '[ "$(grep -c "^BAD" "$R/out")" = 1 ] && grep -q "^BAD  another process" "$R/out"'

echo "== sandbox: as designed, from env -i (no leak expected)"
fixture "$S/sandbox"; prepare; stub_env /run/lane; box
set +e; "${clean_env[@]}" "${net[@]}" "$BWRAP" "${args[@]}" bash /run/lane/stub-agent.sh >"$R/out" 2>&1; rc=$?; set -e
sed "s#$S#\$S#g; s/^/   /" "$R/out"
expect "no BAD line inside" '[ "$rc" = 0 ]'
echo "-- outside, the broker's steps and the host-side git that runs later"
expect "import the push bundle into the shared repo with fsck, fast-forward only" \
  'git -C "$REPO" -c fetch.fsckObjects=true -c transfer.fsckObjects=true fetch -q --no-tags \
     --no-recurse-submodules --no-write-fetch-head "$RUN/outbox/push.bundle" refs/heads/lane/sbx-1:refs/lanes/sbx-1'
expect "push it to origin as the lane branch, fast-forward only" \
  'git -C "$REPO" push -q origin refs/lanes/sbx-1:refs/heads/lane/sbx-1'
git -C "$WT" status --short >/dev/null 2>&1 || true
git -C "$REPO" status --short >/dev/null 2>&1 || true
git clone -q "$ORIGIN" "$R/review" && git -C "$R/review" checkout -q lane/sbx-1 && git -C "$R/review" status --short >/dev/null
expect "host git in the Orca worktree, the main checkout and a fresh checkout of the pushed branch ran nothing" \
  '[ ! -e "$OUT_MARKER" ]'
expect "the fresh checkout has the lane's work and the merged base" \
  '[ -f "$R/review/lane-file" ] && grep -q updated "$R/review/orca.yaml"'
expect "the owner's floor files are unchanged" 'grep -q "$MARK" "$H/.ssh/id_ed25519"'
git -C "$CLONE" status --short >/dev/null 2>&1 || true
expect "host git in the lane's own clone does run the planted program: no host tool may run git there" \
  '[ -s "$OUT_MARKER" ]'

echo "== refuse: a private path tracked in the base's history"
fixture "$S/refuse"
git -C "$REPO" add -f fixtures/customers/a.csv && git -C "$REPO" "${GIT_ID[@]}" commit -q -m "tracked private"
set +e; prepare; prc=$?; set -e
expect "prepare refuses the lane" '[ "$prc" != 0 ]'

echo "== result (scratch: $S)"
[ "$fail" = 0 ] && { echo PASS; exit 0; }
echo FAIL; exit 1
