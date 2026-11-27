#!/usr/bin/env bash
# SBX-1 spike: run a stub agent (stub-agent.sh, a shell script, never a real agent) under bubblewrap with a fake
# owner HOME and a fake floor, and show that it cannot read the floor, cannot write the config that sets its limits
# or git's program-running files, and can still write and commit in its worktree.
#
#   docs/superpowers/spikes/2026-11-27-agent-sandbox/spike.sh [scratch-dir]
#
# Three runs, each on its own fresh fixture under the scratch dir (default: a new mktemp -d):
#   control  the stub with no sandbox: must show leaks (proves the checks can see one)
#   naive    bwrap started with the owner's environment, relying on --clearenv: must show exactly one leak, the
#            token in /proc/1/environ (bwrap's own pid-1 helper keeps the environment it was started with)
#   sandbox  bwrap started from `env -i` with an allowlist, as the design says: must show no leak at all
# Nothing outside the scratch dir is read or written, and the real HOME is never mounted into the sandbox. bwrap
# comes from BWRAP (a command line, split on spaces) or `nix run nixpkgs#bubblewrap --`. Needs unprivileged user
# namespaces; no setuid helper, no privileges. Exit 0 when all three runs come out as above.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
S=${1:-$(mktemp -d "${TMPDIR:-/tmp}/sbx-spike.XXXXXX")}
mkdir -p "$S"
S=$(cd "$S" && pwd)
read -r -a BWRAP <<<"${BWRAP:-nix run nixpkgs#bubblewrap --}"
MARK="FAKE-FLOOR-$$-$RANDOM"
export FAKE_OWNER_TOKEN="fake-token-$RANDOM"   # stands for a gateway key or GH_TOKEN in the owner's environment

# fixture <dir>: a fake owner HOME with a fake floor, a project repo, this lane's worktree and another lane's.
fixture() {
  R=$1
  H="$R/home"; REPO="$R/repo"; WT="$R/lanes/sbx-1"; OTHER="$R/lanes/other"; COMMON="$REPO/.git"
  AGENTCFG="$R/agent-config/opencode.json"   # the generated agent config, kept outside the worktree
  mkdir -p "$H/.ssh" "$H/.config/lane-control" "$H/.local/share/lane-control" "$H/.config/opencode" \
           "$H/.local/share/opencode" "$H/.claude" "$H/.config/gh" "$R/agent-config"
  local f
  for f in .ssh/id_ed25519 .config/lane-control/machine.conf .local/share/lane-control/state.json \
           .config/opencode/opencode.json .local/share/opencode/auth.json .claude/.credentials.json .config/gh/hosts.yml
  do printf '%s %s\n' "$MARK" "$f" >"$H/$f"; done
  printf '{"model": "gateway/chat"}\n' >"$AGENTCFG"
  git init -q -b main "$REPO"
  printf '%s main checkout file\n' "$MARK" >"$REPO/README-not-mounted"
  git -C "$REPO" -c user.name=spike -c user.email=spike@example.invalid commit -q --allow-empty -m init
  git -C "$REPO" worktree add -q -b lane/sbx-1 "$WT"
  git -C "$REPO" worktree add -q -b lane/other "$OTHER"
  mkdir -p "$WT/fixtures/customers" "$WT/.opencode"
  printf '%s customer data\n' "$MARK" >"$WT/fixtures/customers/a.csv"        # a --private path
  printf '{"permission": {"bash": {"git push --force*": "deny"}}}\n' >"$WT/.opencode/opencode.json"
  printf '{}\n' >"$WT/opencode.json"   # root placeholder the sandbox binds read-only (lc-yw1g.1)
  ln -s "$H/.ssh/id_ed25519" "$WT/link-to-key"
  cp "$here/stub-agent.sh" "$R/stub-agent.sh"
  local floor ro
  floor="$H/.ssh/id_ed25519:$H/.config/lane-control/machine.conf:$H/.local/share/lane-control/state.json"
  floor+=":$H/.config/opencode/opencode.json:$H/.local/share/opencode/auth.json:$H/.claude/.credentials.json"
  floor+=":$H/.config/gh/hosts.yml:$WT/fixtures/customers/a.csv:$REPO/README-not-mounted"
  ro="$H/.config/opencode/opencode.json:$WT/.opencode/opencode.json:$WT/opencode.json:$COMMON/config"
  ro+=":$COMMON/hooks/pre-push:$COMMON/info/attributes:$COMMON/worktrees/other/HEAD"
  spike_env=(SPIKE_HOME="$H" SPIKE_WT="$WT" SPIKE_COMMON="$COMMON" SPIKE_OTHER_WT="$OTHER" SPIKE_MARK="$MARK"
             SPIKE_FLOOR="$floor" SPIKE_RO="$ro")
}

# box_args: the bwrap arguments for the current fixture (the mount plan of the design, in small).
box_args() {
  local path="" c d kv
  for c in bash git env cat grep tr ls wc head; do
    d=$(dirname "$(readlink -f "$(command -v "$c")")")
    case ":$path:" in *":$d:"*) ;; *) path=${path:+$path:}$d ;; esac
  done
  args=(--die-with-parent --new-session --unshare-pid --unshare-ipc --unshare-uts --unshare-cgroup-try
        --ro-bind /nix /nix --ro-bind-try /usr /usr --ro-bind-try /bin /bin --ro-bind-try /lib /lib
        --ro-bind-try /lib64 /lib64 --ro-bind-try /etc /etc --ro-bind-try /run/current-system /run/current-system
        --proc /proc --dev /dev --tmpfs /tmp
        # HOME is an empty tmpfs; only the generated agent config is put back, read-only.
        --tmpfs "$H" --dir "$H/.config/opencode" --ro-bind "$AGENTCFG" "$H/.config/opencode/opencode.json"
        # The worktree read-write; what sets the agent's limits read-only; the --private path hidden.
        --bind "$WT" "$WT" --ro-bind "$WT/.opencode" "$WT/.opencode" --ro-bind "$WT/opencode.json" "$WT/opencode.json"
        --tmpfs "$WT/fixtures/customers"
        # The common git dir read-write for objects and refs; config, hooks, info and other lanes' admin dirs not.
        --bind "$COMMON" "$COMMON" --ro-bind "$COMMON/config" "$COMMON/config"
        --ro-bind "$COMMON/hooks" "$COMMON/hooks" --ro-bind "$COMMON/info" "$COMMON/info"
        --ro-bind "$COMMON/worktrees" "$COMMON/worktrees" --bind "$COMMON/worktrees/sbx-1" "$COMMON/worktrees/sbx-1"
        --ro-bind "$R/stub-agent.sh" "$R/stub-agent.sh"
        --clearenv --setenv HOME "$H" --setenv PATH "$path" --setenv LANG C.UTF-8 --chdir "$WT")
  for kv in "${spike_env[@]}"; do args+=(--setenv "${kv%%=*}" "${kv#*=}"); done
}

# Only what nix needs to start bwrap; the owner's token is not passed.
clean_env=(env -i PATH="$PATH" HOME="$HOME" ${TMPDIR:+TMPDIR="$TMPDIR"} ${NIX_REMOTE:+NIX_REMOTE="$NIX_REMOTE"})

echo "== control: stub agent without the sandbox (leaks expected)"
fixture "$S/control"
set +e
env HOME="$H" "${spike_env[@]}" bash "$R/stub-agent.sh" >"$R/out" 2>&1; control=$?
set -e
grep '^BAD' "$R/out" | sed "s#$S#\$S#g; s/^/   /" | cut -c1-140

echo "== naive: bwrap started with the owner's environment, relying on --clearenv (one leak expected)"
fixture "$S/naive"; box_args
set +e
"${BWRAP[@]}" "${args[@]}" bash "$R/stub-agent.sh" >"$R/out" 2>&1; naive=$?
set -e
grep '^BAD' "$R/out" | sed "s#$S#\$S#g; s/^/   /"
naive_bad=$(grep -c '^BAD' "$R/out" || true)
naive_proc=$(grep -c "^BAD  another process's environment" "$R/out" || true)

echo "== sandbox: bwrap started from env -i with an allowlist (no leak expected)"
fixture "$S/sandbox"; box_args
set +e
"${clean_env[@]}" "${BWRAP[@]}" "${args[@]}" bash "$R/stub-agent.sh" >"$R/out" 2>&1; sandboxed=$?
set -e
sed "s#$S#\$S#g; s/^/   /" "$R/out"
echo "== after the sandboxed run, outside it"
git -C "$WT" log --oneline -1 | sed 's/^/   lane branch head: /'
grep -q "$MARK" "$H/.ssh/id_ed25519" && echo "   fake floor files unchanged on the host"
git -C "$REPO" config --local --get spike.lifted >/dev/null || echo "   shared git config unchanged"

echo "== result (scratch: $S)"
if [ "$control" -ne 0 ] && [ "$naive_bad" = 1 ] && [ "$naive_proc" = 1 ] && [ "$sandboxed" -eq 0 ]; then
  echo "PASS"; exit 0
fi
echo "FAIL: control exit $control (want non-zero); naive BAD lines $naive_bad, /proc leak $naive_proc (want 1, 1);" \
     "sandbox exit $sandboxed (want 0)"
exit 1
