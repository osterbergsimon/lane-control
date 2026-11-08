#!/usr/bin/env bash
# Proof for opencode-sandbox.sh. Run it from a linked worktree of a repo with a GitHub origin, on a
# branch that is not main (a scratch worktree is fine):
#   scripts/test-opencode-sandbox.sh                 # all checks; nothing leaves the machine
#   SBX_TEST_PUSH=1 scripts/test-opencode-sandbox.sh # also push the branch through the relay
# The CI-checks relay runs a stub gh (outside, first on the launcher's PATH): nothing here hits GitHub's
# CI; the stub logs every call so the outside checks can prove which PR and run it was asked about.
# One sandbox runs every inside check (each passes only on the denial it expects), then checks from
# outside prove nothing was written where the lane must not write. Exit 1 on any failure.
#
# Instance settings come from the playbook config ($PLAYBOOK_CONFIG, default ~/.config/rove/playbook.conf.sh),
# all optional (see config.example.sh):
#   SANDBOX_PRIVATE_PATHS=( glob ... )   private files and directories that must be absent inside (data dirs,
#                                        caches, other projects' state); the first regular file found under one is
#                                        also checked unreadable
#   SBX_TEST_MODEL=<provider/model>      the model pin for a scratch worktree that has none (default: TIER_MODEL[swift])
set -u
declare -a SANDBOX_PRIVATE_PATHS=()
declare -A TIER_MODEL=()
_conf=${PLAYBOOK_CONFIG:-$HOME/.config/rove/playbook.conf.sh}
# shellcheck disable=SC1090
[ ! -r "$_conf" ] || . "$_conf" >/dev/null 2>&1 || true
S=$(dirname "$(realpath "$0")")/opencode-sandbox.sh
branch=$(git symbolic-ref --short HEAD) || { echo "not on a branch"; exit 2; }
common=$(realpath "$(git rev-parse --git-common-dir)")
private=(); shopt -s nullglob
for _g in "${SANDBOX_PRIVATE_PATHS[@]}"; do for _m in $_g; do private+=("$_m"); done; done  # globs expand here
shopt -u nullglob
private_file=""
for _m in "${private[@]}"; do
  if [ -f "$_m" ]; then private_file=$_m; break; fi
  _f=$(find "$_m" -type f 2>/dev/null | head -n 1); [ -z "$_f" ] || { private_file=$_f; break; }
done
other_wt=$(git worktree list --porcelain | sed -n 's/^worktree //p' | grep -vxF "$(realpath "$PWD")" | tail -n 1)
uid=$(id -u)
# a dummy lane key (not a real key): the sandbox must mount exactly this and nothing of the user's
key=$(mktemp --suffix=-sbx-auth.json); stubdir=$(mktemp -d --suffix=-sbx-stub)
prn=sbx-pr-test-$$ # the PR branch of the OPENCODE_SANDBOX_PR_BRANCH checks (it exists only in a local bare "origin")
made_pin=""
cleanup() {
  rm -rf -- "$key" "$stubdir" "$HOME/.cache/opencode-lanes/sbx-no-such-task"; [ -z "$made_pin" ] || rm -rf -- "$made_pin"
  git --git-dir="$common" for-each-ref --format='%(refname)' "refs/heads/$prn" "refs/heads/$prn-*" \
    | xargs -r -n1 git --git-dir="$common" update-ref -d   # the relay refreshes the shared repo's PR branch
}
trap cleanup EXIT
printf '{"opencode-go":{"type":"api","key":"sbx-test-not-a-key"}}\n' >"$key"
export OPENCODE_SANDBOX_AUTH=$key
# the lane key is checked against the provider at launch; the dummy key would be rejected (tested below)
export OPENCODE_SANDBOX_CHECK_KEY=0
keysum=$(printf %s sbx-test-not-a-key | sha256sum | cut -c1-64)
# the worktree's model pin (a scratch worktree has none: make one, removed at exit)
test_model=${SBX_TEST_MODEL:-${TIER_MODEL[swift]:-}}
if [ ! -f .opencode/opencode.json ]; then
  [ -n "$test_model" ] || { echo "no .opencode/opencode.json and no model: set SBX_TEST_MODEL or TIER_MODEL[swift] in the playbook config"; exit 2; }
  [ -d .opencode ] || made_pin=$PWD/.opencode
  mkdir -p .opencode; printf '{"$schema":"https://opencode.ai/config.json","model":"%s"}\n' "$test_model" >.opencode/opencode.json
  [ -n "$made_pin" ] || made_pin=$PWD/.opencode/opencode.json
fi
pin=$(jq -r '.model | if type == "string" then . elif type == "object" then "\(.providerID)/\(.model)" else empty end' .opencode/opencode.json)
pin=${pin%%#*}
ocbin=$(readlink -f "$(command -v opencode)")  # the 2.x binary (a bare opencode inside may resolve to an older one)
# the stub gh: a PR 77 of this branch (and a fork's PR 78 of a same-named branch), runs 555 (own), 666
# (another branch) and 777 (a fork's run of this branch name); anything it is not meant to get exits 99.
# `pr list --json url,...` (find the open PR) lists a fork's PR 78 of the same-named branch and then the own PR 77 (STUB_NOOPEN=1: only the fork's); `pr create` prints PR 79
repo=$(printf '%s' "$(git config --get remote.origin.url)" | sed -nE 's#^(https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)$#\2#p'); repo=${repo%.git}
export STUB_LOG=$stubdir/log STUB_BRANCH=$branch STUB_REPO=$repo STUB_REALGH=$(readlink -f "$(command -v gh)")
cat >"$stubdir/gh" <<'STUB'
#!/usr/bin/env bash
# the user's git credential helper is `gh auth git-credential` (by PATH): hand that to the real gh
[ "$1 $2" != "auth git-credential" ] || exec "$STUB_REALGH" "$@"
echo "$*" >>"$STUB_LOG"
b=$STUB_BRANCH r=$STUB_REPO
case "$1 $2" in
  "pr list") case "$*" in
    *"--json url,"*)
      fork="{\"number\":78,\"url\":\"https://github.com/$r/pull/78\",\"headRefName\":\"$b\",\"headRepositoryOwner\":{\"login\":\"fork-owner\"}}"
      own="{\"number\":77,\"url\":\"https://github.com/$r/pull/77\",\"headRefName\":\"$b\",\"headRepositoryOwner\":{\"login\":\"${r%%/*}\"}}"
      if [ "${STUB_NOOPEN:-}" = 1 ]; then echo "[$fork]"; else echo "[$fork,$own]"; fi ;;
    *) echo "[{\"number\":77,\"headRefName\":\"$b\",\"headRepositoryOwner\":{\"login\":\"${r%%/*}\"}},{\"number\":78,\"headRefName\":\"$b\",\"headRepositoryOwner\":{\"login\":\"fork-owner\"}}]" ;;
    esac ;;
  "pr create") echo "https://github.com/$r/pull/79" ;;
  "pr checks") printf '\033[31mCHECKS-OF-%s\033[0m\tfail\n' "$3"; exit 1 ;;
  "pr view") echo abc123 ;;
  "run list") echo 555 ;;
  "run view") if [ "$3" = 555 ]; then for i in $(seq 3000); do printf '\033[1mLOG LINE %s\033[0m\r\n' "$i"; done; else echo "LOG-OF-$3"; fi ;;
  "api repos/$r/actions/runs/"*)
    case $2 in
      */555) echo "{\"head_branch\":\"$b\",\"head_repository\":{\"full_name\":\"$r\"}}" ;;
      */777) echo "{\"head_branch\":\"$b\",\"head_repository\":{\"full_name\":\"fork/x\"}}" ;;
      *) echo "{\"head_branch\":\"other-branch\",\"head_repository\":{\"full_name\":\"$r\"}}" ;;
    esac ;;
  *) echo "stub gh: unexpected: $*" >&2; exit 99 ;;
esac
STUB
chmod +x "$stubdir/gh"
realrove=$(readlink -f "$(command -v rove)" 2>/dev/null); realgh=$(readlink -f "$(command -v gh)" 2>/dev/null)
realgit=$(readlink -f "$(command -v git)"); url=$(git config --get remote.origin.url)
# every private path that exists here must be absent inside
absent=()
for p in "${private[@]}" \
         "$(dirname "$common")/AGENTS.md" "$HOME"/.rove/agent-turns.json \
         "$HOME"/.rove/daemon.log "$HOME"/.config/gh "$HOME"/.ssh "$HOME"/.gnupg "$HOME"/.claude \
         "$HOME"/.local/share/opencode/opencode.db "$HOME"/.local/share/opencode/auth.json \
         "$HOME"/.config/opencode/service.json "$HOME"/.config/opencode/cli.json \
         "$HOME"/.config/opencode/herdr-tui-session.js "$HOME"/.config/opencode/node_modules \
         "$HOME"/.config/opencode/plugins/herdr-agent-state.js "$realrove" "$HOME"/.cargo \
         /run/user/"$uid"/bus /run/user/"$uid"/rove.sock /run/user/"$uid"/cc-socks /run/user/"$uid"/gcr \
         /run/docker.sock /run/dbus/system_bus_socket /nix/var/nix/daemon-socket/socket \
         "$common/config" "$common/hooks" "$common/refs" /dev/input /dev/uinput; do
  [ -n "$p" ] && { [ -e "$p" ] || [ -L "$p" ]; } && absent+=("$p")
done
# another worktree is absent too; the main checkout is only the empty parent of the .git/objects bind
# (the shared objects, read-only) and the lane's own worktree gitdir, so for it the private content
# must be absent (nothing but .git/objects and .git/worktrees/<this worktree>), not the path
other_main=""; [ -n "$other_wt" ] && [ "$(realpath "$other_wt")" = "$(dirname "$common")" ] && other_main=$other_wt
[ -z "$other_wt" ] || [ -n "$other_main" ] || absent+=("$other_wt")
# every TCP port listening on host loopback (rove, sccache, dev servers) must be unreachable inside
# (and always rove :7878, the shared sccache :4226, an opencode server :49374)
ports=$({ ss -Htln 2>/dev/null | awk '{print $4}' | sed -nE 's/^(127\.[0-9.]+|\[::1\]):([0-9]+)$/\2/p'; printf '%s\n' 4226 7878 49374; } | sort -un | tr '\n' ' ')
x11=$(ss -Hxl 2>/dev/null | grep -o '@/tmp/.X11-unix/X[0-9]*' | head -n 1)

inside=$(cat <<EOF
fails=0
c() { local name=\$1; shift; local out; out=\$("\$@" 2>&1) && echo "ok   \$name" || { echo "FAIL \$name: \$(printf '%s' "\$out" | tr '\n' ' ' | head -c 300)"; fails=1; }; }
must_fail() { local want=\$1; shift; local out; if out=\$("\$@" 2>&1); then echo "succeeded: \$out"; return 1; fi
  case \$out in *"\$want"*) return 0 ;; *) echo "wrong denial: \$out"; return 1 ;; esac; }
# what a lane needs
c "worktree writable"            bash -c 'echo x > .sbx-probe && rm .sbx-probe'
c "git status and commit"        bash -c 'git status -sb >/dev/null && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m sbx-probe && git reset -q --soft HEAD~1'
c "git fetch (relayed)"          bash -c 'git fetch origin | grep -q "fetched origin" && git rev-parse -q --verify origin/main >/dev/null'
c "network to the API"           bash -c '[ "\$(curl -sS -m 10 -o /dev/null -w "%{http_code}" https://api.github.com)" = 200 ]'
c "opencode runs"                opencode --version
c "core dumps off, not raisable" bash -c '[ "\$(ulimit -Sc)" = 0 ] && [ "\$(ulimit -Hc)" = 0 ] && ! ulimit -c 1000 2>/dev/null && ! ulimit -Sc unlimited 2>/dev/null'
# opencode 2.x: the lane key is the opencode-go provider's apiKey (a file of its own), its model the worktree's pin
c "lane key file is the lane key" bash -c '[ "\$(sha256sum < /run/sandbox/lane-key | cut -c1-64)" = $keysum ]'
c "provider key is that file"    bash -c '[ "\$(jq -r ".provider[\"opencode-go\"].options.apiKey" <<<"\$OPENCODE_CONFIG_CONTENT")" = "{file:/run/sandbox/lane-key}" ]'
c "only opencode-go, no free"    bash -c 'jq -e ".enabled_providers == [\"opencode-go\"]" <<<"\$OPENCODE_CONFIG_CONTENT" >/dev/null && [ "\$OPENCODE_DISABLE_FREE_MODELS" = 1 ]'
c "effective config model = pin" bash -c '[ "\$(jq -r .model <<<"\$OPENCODE_CONFIG_CONTENT")" = $pin ]'
c "TUI recent model = pin"       bash -c '[ "\$(jq -r ".recent[0] | .providerID + \"/\" + .modelID" "\$XDG_STATE_HOME/opencode/model.json")" = $pin ]'
c "provider connected, pin listed" bash -c 'o=\$($ocbin --standalone models 2>&1); grep -qxF $pin <<<"\$o" && ! grep -v "^opencode-go/" <<<"\$o" | grep -q .'
c "lane runs on the pinned model" bash -c 'o=\$(timeout 90 $ocbin --standalone run hi </dev/null 2>&1); [[ \$o == *"build · ${pin#*/}"* ]]'
c "user's global config no model" bash -c '! grep -qE "\"model\"" "\$HOME/.config/opencode/opencode.jsonc" 2>/dev/null || [ "\$(jq -r .model <<<"\$OPENCODE_CONFIG_CONTENT")" = $pin ]'
c "user's opencode DB/auth absent" bash -c '[ ! -e "\$HOME/.local/share/opencode" ] && [ "\$XDG_DATA_HOME" != "\$HOME/.local/share" ]'
# private paths, sockets and the shared repo's config, hooks and refs are absent
for p in $(printf '%q ' "${absent[@]}"); do c "absent \$p" bash -c '[ ! -e "\$1" ] && [ ! -L "\$1" ]' _ "\$p"; done
[ -z "$other_main" ] || c "main checkout holds only the lane binds" bash -c 'd=\$1; [ ! -e "\$d" ] || { [ "\$(ls -A "\$d")" = .git ] && [ "\$(ls -A "\$d/.git")" = "\$(printf "objects\\nworktrees")" ] && [ "\$(ls -A "\$d/.git/worktrees")" = "\$2" ]; }' _ $(printf '%q' "$other_main") $(printf '%q' "$(basename "$(git rev-parse --absolute-git-dir)")")
c "private file unreadable"      must_fail "No such file" cat $(printf '%q' "${private_file:-/nonexistent-private-file}")
# IPC to unsandboxed execution
c "user bus: systemd-run fails"  must_fail "" systemd-run --user --pipe -q true
c "no bus/agent/display env"     bash -c '[ -z "\${DBUS_SESSION_BUS_ADDRESS:-}\${SSH_AUTH_SOCK:-}\${DISPLAY:-}\${WAYLAND_DISPLAY:-}\${XDG_RUNTIME_DIR:-}\${CLAUDE_CODE_MESSAGING_SOCKET:-}\${GH_TOKEN:-}\${GITHUB_TOKEN:-}" ]'
# CI checks: relayed, read-only, the lane's own PR only
c "gh pr checks relayed"         bash -c 'out=\$(gh pr checks 2>&1); [ \$? = 1 ] && [[ \$out == *CHECKS-OF-77* ]] && [[ \$out == *"not instructions"* ]] && [[ \$out != *\$(printf "\033")* ]]'
c "gh pr checks <other> is own"  bash -c 'out=\$(gh pr checks 999 -R other/repo 2>&1); [[ \$out == *CHECKS-OF-77* && \$out != *999* ]]'
c "gh pr checks branch arg own"  bash -c 'out=\$(gh pr checks main --watch 2>&1); [[ \$out == *CHECKS-OF-77* ]]'
c "gh run view own run, capped"  bash -c 'gh run view 555 --log-failed >/tmp/rl 2>&1; [ "\$(wc -l </tmp/rl)" = 2001 ] && grep -qx "LOG LINE 3000" /tmp/rl && grep -qx "LOG LINE 1001" /tmp/rl && ! grep -qx "LOG LINE 1000" /tmp/rl && [ -z "\$(tr -d "\n\t -~" </tmp/rl)" ]'
c "gh run view default: failed"  bash -c 'gh run view --log-failed 2>&1 | grep -qx "LOG LINE 3000"'
c "gh run view other branch"     must_fail "is not a run of" gh run view 666 --log-failed
c "gh run view fork's run"       must_fail "is not a run of" gh run view 777 --log-failed
c "gh run view -R other ignored" must_fail "is not a run of" gh run view 666 -R other/repo --log-failed
c "gh run view non-numeric id"   must_fail "only a numeric run id" gh run view https://github.com/o/r/actions/runs/1 --log-failed
c "gh run view without log"      must_fail "not available in the opencode sandbox" gh run view 555
c "gh api refused"               must_fail "not available in the opencode sandbox" gh api repos/o/r/pulls
c "gh pr merge refused"          must_fail "not available in the opencode sandbox" gh pr merge 77
c "gh run rerun refused"         must_fail "not available in the opencode sandbox" gh run rerun 555
c "gh pr comment refused"        must_fail "not available in the opencode sandbox" gh pr comment 77 -b x
c "rove api add refused"         must_fail "not available in the opencode sandbox" rove api add --repo . --title sbx
c "rove send to other task"      must_fail "only the bare form" rove api send --task-id X --prompt hi
c "gh token refused"             must_fail "not available in the opencode sandbox" gh auth token
c "no token/secret in env"       bash -c '! env | grep -qiE "token|secret|password|^GH_|^GITHUB_"'
c "gh config absent"             bash -c '[ ! -e "\$HOME/.config/gh" ] && [ ! -e "\$XDG_CONFIG_HOME/gh" ]'
c "real gh has no token"         must_fail "" $(printf '%q' "$realgh") auth token
c "real gh api fails"            must_fail "" $(printf '%q' "$realgh") api user
c "real git push has no creds"   must_fail "" env GIT_TERMINAL_PROMPT=0 $(printf '%q' "$realgit") push --dry-run $(printf '%q' "$url") HEAD:refs/heads/sbx-never
[ ! -x /run/current-system/sw/bin/rove ] || c "system rove fails" must_fail "" /run/current-system/sw/bin/rove api add --repo . --title sbx
for port in $ports; do c "host loopback :\$port refused" must_fail "" bash -c "exec 3<>/dev/tcp/127.0.0.1/\$port"; done
c "LAN gateway :53 and :80 refused" bash -c 'gw=\$(ip route | sed -n "s/^default via \\([^ ]*\\).*/\\1/p" | head -n 1); [ -n "\$gw" ] && ! (exec 3<>/dev/tcp/\$gw/53) 2>/dev/null && ! (exec 3<>/dev/tcp/\$gw/80) 2>/dev/null'
c "no capabilities inside"       bash -c 'grep -qE "^CapEff:[[:space:]]*0+$" /proc/self/status'
c "network config cannot change" must_fail "" ip link add sbxprobe type dummy
c "gateway :7878 refused"        must_fail "" bash -c 'gw=\$(ip route | sed -n "s/^default via \\([^ ]*\\).*/\\1/p" | head -n 1); [ -n "\$gw" ] && exec 3<>/dev/tcp/\$gw/7878'
[ -z $(printf '%q' "$x11") ] || c "X11 abstract socket refused" must_fail "Connection refused" python3 -c 'import socket,sys; s=socket.socket(socket.AF_UNIX); s.connect("\0"+sys.argv[1][1:])' $(printf '%q' "$x11")
c "own pid namespace"            bash -c '[ "\$(ls -d /proc/[0-9]* | wc -l)" -lt 15 ] && ! grep -qxE "claude|bun|dbus-broker|systemd|opencode.exe|sccache" /proc/[0-9]*/comm'
# last: the dev env starts the lane's own sccache server (port 4226 inside its own namespace)
c "dev env (cargo) present"      bash -c '[ -z "\$OPENCODE_SANDBOX_DEVENV" ] || { . "\$OPENCODE_SANDBOX_DEVENV" >/dev/null 2>&1; cargo --version; }'
# git integrity: the lane's git pointers and the private dir's config and hooks are read-only
c "worktree .git file read-only" must_fail "Read-only" bash -c 'echo x >> .git'
c "commondir read-only"          must_fail "Read-only" bash -c 'echo x > "\$(git rev-parse --git-dir)/commondir"'
c "git config read-only"         must_fail "" git config sbx.probe 1
c "git hooks read-only"          must_fail "Read-only" bash -c 'echo "#!/bin/sh" > "\$(git rev-parse --git-common-dir)/hooks/pre-commit"'
c "shared objects read-only"     must_fail "Read-only" touch $(printf '%q' "$common/objects/sbx-probe")
# the relay takes only plain files and fixed operations
c "relay refuses a link"         bash -c 'ln -s /etc/passwd /run/sandbox/relay/req-424242; for _ in \$(seq 50); do [ -f /run/sandbox/relay/res-424242 ] && break; sleep 0.2; done; grep -q "bad request" /run/sandbox/relay/res-424242 && rm -f /run/sandbox/relay/res-424242'
c "relay refuses unknown op"     must_fail "unknown request" lane-relay evil
exit \$fails
EOF
)
fail=0
# outside: no lane key, or the user's key, means no sandbox at all
o0() { local name=$1 want=$2; shift 2; local r; if r=$("$@" 2>&1); then echo "FAIL $name: started"; fail=1
  elif [[ $r == *"$want"* ]]; then echo "ok   $name"; else echo "FAIL $name: $r"; fail=1; fi; }
o0 "no lane key: refused" "no lane key" env -u ROVE_TASK_ID OPENCODE_SANDBOX_AUTH=/nonexistent/auth.json \
  OPENCODE_SANDBOX_BRANCH="$branch" OPENCODE_SANDBOX_CMD=true "$S"
# the launch checks the lane key against the provider: a rejected key stops here (a skipped check if offline)
r=$(env -u ROVE_TASK_ID OPENCODE_SANDBOX_CHECK_KEY=1 OPENCODE_SANDBOX_BRANCH="$branch" OPENCODE_SANDBOX_CMD=true "$S" 2>&1)
case $r in *"rejected the lane key"*) echo "ok   rejected lane key: refused at launch" ;;
  *"not verified"*) echo "ok   rejected lane key: provider unreachable, check skipped" ;;
  *) echo "FAIL rejected lane key: $r"; fail=1 ;; esac
# a pinned model that is not available stops the real launch (no fallback model); only with our own scratch pin
if [ -n "$made_pin" ]; then
  printf '{"model":"opencode-go/sbx-no-such-model"}\n' >.opencode/opencode.json
  r=$(env -u ROVE_TASK_ID OPENCODE_SANDBOX_BRANCH="$branch" timeout 120 "$S" --version 2>&1)
  printf '{"$schema":"https://opencode.ai/config.json","model":"%s"}\n' "$pin" >.opencode/opencode.json
  case $r in *"pinned model opencode-go/sbx-no-such-model is not available"*) echo "ok   unavailable pinned model: refused" ;;
    *) echo "FAIL unavailable pinned model: $r"; fail=1 ;; esac
fi
[ ! -f "$HOME/.local/share/opencode/auth.json" ] || o0 "user's key: refused" "refusing the user's opencode key" \
  env -u ROVE_TASK_ID OPENCODE_SANDBOX_AUTH="$HOME/.local/share/opencode/auth.json" \
  OPENCODE_SANDBOX_BRANCH="$branch" OPENCODE_SANDBOX_CMD=true "$S"
out=$(env -u ROVE_TASK_ID PATH="$stubdir:$PATH" OPENCODE_SANDBOX_BRANCH="$branch" OPENCODE_SANDBOX_CMD="$inside" "$S" 2>&1) || fail=1
printf '%s\n' "$out" | grep -E '^(ok|FAIL) ' || { printf '%s\n' "$out" | tail -n 5; fail=1; }
# the relay's git never prompts: with a credential helper that fails (a gh that is not logged in) and
# askpass programs that would open a window, `git fetch` fails fast and no askpass program runs
mkdir "$stubdir/badgh"; printf '#!/bin/sh\nexit 1\n' >"$stubdir/badgh/gh"
printf '#!/bin/sh\ntouch "%s"\n' "$stubdir/askpass-ran" >"$stubdir/askpass"; chmod +x "$stubdir/badgh/gh" "$stubdir/askpass"
r=$(env -u ROVE_TASK_ID PATH="$stubdir/badgh:$PATH" SSH_ASKPASS="$stubdir/askpass" GIT_ASKPASS="$stubdir/askpass" DISPLAY=:99 \
  OPENCODE_SANDBOX_BRANCH="$branch" OPENCODE_SANDBOX_CMD='git fetch origin 2>&1; echo done' timeout 120 "$S" 2>&1)
if [[ $r == *done* && ! -e $stubdir/askpass-ran ]]; then echo "ok   relay git never prompts (no askpass run)"
else echo "FAIL relay git never prompts: $r"; fail=1; fi
# an explicit branch wins over the Rove task's (no get-task call: this task does not exist); its guards stay
r=$(env ROVE_TASK_ID=sbx-no-such-task OPENCODE_SANDBOX_BRANCH="$branch" OPENCODE_SANDBOX_CMD='echo explicit-wins' "$S" 2>&1)
if [[ $r == *explicit-wins* ]]; then echo "ok   explicit branch wins over the task"; else echo "FAIL explicit branch wins over the task: $r"; fail=1; fi
o0 "explicit main: refused" "refusing lane branch main" env ROVE_TASK_ID=sbx-no-such-task OPENCODE_SANDBOX_BRANCH=main OPENCODE_SANDBOX_CMD=true "$S"
o0 "explicit bad ref: refused" "bad branch" env ROVE_TASK_ID=sbx-no-such-task "OPENCODE_SANDBOX_BRANCH=a..b" OPENCODE_SANDBOX_CMD=true "$S"
# outside: nothing the lane did reached the shared repo
o() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }
# outside: the stub gh was asked about the lane's own PR and runs only, read-only
o "stub gh: own PR only"         '[ "$(grep -c "^pr checks " "$STUB_LOG")" -ge 3 ] && ! grep "^pr checks " "$STUB_LOG" | grep -vqx "pr checks 77 --repo $repo"'
o "stub gh: own runs only"       '! grep "^run view " "$STUB_LOG" | grep -vq "^run view 555 --repo $repo --log-failed$" && ! grep -q "run view 666\|run view 777" "$STUB_LOG"'
o "stub gh: nothing but reads"   '! grep -Eq "rerun|merge|comment|edit|close|--method|-X " "$STUB_LOG" && ! grep -vEq "^(pr list|pr checks|pr view|run list|run view|api repos/$repo/actions/runs/)" "$STUB_LOG"'
o "stub gh: unset = lane branch" '[ "$(grep -c "^pr list --repo $repo --head $branch " "$STUB_LOG")" -ge 3 ] && ! grep -q -- "--head $prn" "$STUB_LOG"'
o "shared repo config unchanged" '! git --git-dir="$common" config --get sbx.probe >/dev/null'
o "no hook planted"              '[ ! -e "$common/hooks/pre-commit" ] || ! grep -qx "#!/bin/sh" "$common/hooks/pre-commit"'
o "no object planted"            '[ ! -e "$common/objects/sbx-probe" ]'
o "worktree .git intact"         'grep -q "^gitdir: " .git && [ "$(wc -l < .git)" = 1 ]'
# OPENCODE_SANDBOX_PR_BRANCH: continue an existing PR branch. origin is a local bare repo here (git's
# url.insteadOf for the launcher's relay only): the real origin is never written. A name that is not on the
# real origin is refused at launch, so a failed redirect could not push anywhere either.
bare=$stubdir/origin.git; plog=$stubdir/log-pr
git init -q --bare "$bare"
# a fresh lane: its private branch is seeded from the shared repo (an earlier run's commits would sit on it)
lanedir=$HOME/.cache/opencode-lanes/wt-$(printf '%s' "$(realpath "$PWD")" | sha256sum | cut -c1-16); rm -rf -- "${lanedir:?}"
tip=$(git rev-parse "refs/heads/$branch")
git push -q "$bare" "$tip:refs/heads/$prn"
emptytree=$(git --git-dir="$bare" hash-object -w -t tree /dev/null)
div=$(git --git-dir="$bare" -c user.name=t -c user.email=t@t commit-tree "$emptytree" -m sbx-diverged)
git --git-dir="$bare" update-ref "refs/heads/$prn-div" "$div"
prenv=(env -u ROVE_TASK_ID PATH="$stubdir:$PATH" OPENCODE_SANDBOX_BRANCH="$branch" OPENCODE_SANDBOX_PR_BRANCH="$prn"
  GIT_CONFIG_COUNT=1 "GIT_CONFIG_KEY_0=url.$bare.insteadOf" "GIT_CONFIG_VALUE_0=$url" STUB_BRANCH="$prn" STUB_LOG="$plog")
if [ "$(env GIT_CONFIG_COUNT=1 "GIT_CONFIG_KEY_0=url.$bare.insteadOf" "GIT_CONFIG_VALUE_0=$url" git ls-remote --get-url "$url")" != "$bare" ]; then
  echo "FAIL PR branch checks: could not redirect origin to the local bare repo"; fail=1
else
  bref() { git --git-dir="$bare" rev-parse -q --verify "refs/heads/$1" 2>/dev/null; }
  # push goes to the PR branch, fast-forward: the lane commits on top of it, origin's PR branch moves there
  r=$("${prenv[@]}" OPENCODE_SANDBOX_CMD='git -c user.name=t -c user.email=t@t commit -q --allow-empty -m sbx-pr && git push -f origin HEAD:main 2>&1; echo "rc=$?"' "$S" 2>&1)
  o "pr branch: push reports the PR branch" '[[ $r == *"pushed $branch to $prn"* && $r == *rc=0* ]]'
  o "pr branch: origin's PR branch advanced by one commit" '[ "$(bref "$prn")" != "$tip" ] && [ "$(git --git-dir="$bare" rev-parse "refs/heads/$prn^")" = "$tip" ]'
  o "pr branch: no other ref on origin" '[ "$(git --git-dir="$bare" for-each-ref --format="%(refname:short)" | sort | tr "\n" " ")" = "$prn $prn-div " ]'
  o "pr branch: shared repo's PR branch follows" '[ "$(git --git-dir="$common" rev-parse "refs/heads/$prn")" = "$(bref "$prn")" ]'
  o "pr branch: lane branch unmoved" '[ "$(git rev-parse "refs/heads/$branch")" = "$tip" ]'
  # non-fast-forward is refused with a clear message, nothing moves
  r=$("${prenv[@]}" OPENCODE_SANDBOX_PR_BRANCH="$prn-div" OPENCODE_SANDBOX_CMD='git push 2>&1; echo "rc=$?"' "$S" 2>&1)
  o "pr branch: non-fast-forward refused" '[[ $r == *"only a fast-forward of origin"* && $r != *rc=0* && $r != *pushed* ]] && [ "$(bref "$prn-div")" = "$div" ]'
  # a name that is not on origin is refused at launch and never created; guarded and malformed names too
  o0 "pr branch: not on origin refused" "is not on origin" "${prenv[@]}" OPENCODE_SANDBOX_PR_BRANCH=sbx-pr-nope OPENCODE_SANDBOX_CMD=true "$S"
  o "pr branch: typo not created" '[ -z "$(bref sbx-pr-nope)" ]'
  for g in main master HEAD gh-pages release-1; do
    o0 "pr branch: $g refused" "PR branch $g" "${prenv[@]}" OPENCODE_SANDBOX_PR_BRANCH=$g OPENCODE_SANDBOX_CMD=true "$S"
  done
  o0 "pr branch: bad ref refused" "bad PR branch" "${prenv[@]}" "OPENCODE_SANDBOX_PR_BRANCH=a..b" OPENCODE_SANDBOX_CMD=true "$S"
  o0 "pr branch: leading dash refused" "bad PR branch" "${prenv[@]}" "OPENCODE_SANDBOX_PR_BRANCH=--force" OPENCODE_SANDBOX_CMD=true "$S"
  # the branch is deleted from origin after launch (e.g. its PR was merged): the push does not create it again
  git --git-dir="$bare" update-ref "refs/heads/$prn-gone" "$tip"
  spool=$lanedir/relay/spool; rm -f "$spool/ready" "$spool/go"
  ( "${prenv[@]}" OPENCODE_SANDBOX_PR_BRANCH="$prn-gone" OPENCODE_SANDBOX_CMD='touch /run/sandbox/relay/ready; for _ in $(seq 300); do [ -e /run/sandbox/relay/go ] && break; sleep 0.2; done; git push 2>&1; echo "rc=$?"' timeout 120 "$S" >"$stubdir/gone.out" 2>&1 ) &
  gone_pid=$!
  for _ in $(seq 300); do [ -e "$spool/ready" ] && break; sleep 0.2; done
  git --git-dir="$bare" update-ref -d "refs/heads/$prn-gone"; touch "$spool/go"; wait "$gone_pid"
  o "pr branch: deleted on origin, not recreated" '[[ $(cat "$stubdir/gone.out") == *"is gone from origin"* ]] && [ -z "$(bref "$prn-gone")" ]'
  # CI checks and the PR are those of the PR branch
  r=$("${prenv[@]}" OPENCODE_SANDBOX_CMD='gh pr checks 2>&1; gh run view 555 --log-failed 2>&1 | head -n 3' "$S" 2>&1)
  o "pr branch: gh pr checks of the PR branch" '[[ $r == *CHECKS-OF-77* && $r == *"CI output of $prn"* ]] && grep -qx "pr list --repo $repo --head $prn --state all --limit 5 --json number,headRefName,headRepositoryOwner" "$plog" && grep -qx "pr checks 77 --repo $repo" "$plog"'
  o "pr branch: run log checked against the PR branch" '[[ $r == *"LOG LINE"* || $r == *"CI output of $prn"* ]] && ! grep -q -- "--head $branch" "$plog"'
  # gh pr create: finds the open PR of the PR branch, or creates one with the PR branch as head
  r=$("${prenv[@]}" OPENCODE_SANDBOX_CMD='gh pr create --title t --body b 2>&1' "$S" 2>&1)
    o "pr branch: pr create finds the own open PR, not the fork's" '[[ $r == */pull/77 ]] && grep -qxF "pr list --repo $repo --head $prn --state open --json url,headRefName,headRepositoryOwner" "$plog" && ! grep -q "^pr create" "$plog"'
  r=$("${prenv[@]}" STUB_NOOPEN=1 OPENCODE_SANDBOX_CMD='gh pr create --title t --body b 2>&1' "$S" 2>&1)
  o "pr branch: pr create ignores a fork's open PR, opens from the PR branch" '[[ $r == */pull/79 ]] && grep -q "^pr create --repo $repo --base main --head $prn --title t " "$plog"'
  o "pr base: default main (PR branch mode)" 'grep -q "^pr create --repo $repo --base main --head $prn " "$plog"'
  # the base of the PR the relay opens: a coordinator setting; the lane cannot choose it
  git push -q "$bare" "$tip:refs/heads/develop"
  rm -rf -- "${lanedir:?}"
  nopen=(env -u ROVE_TASK_ID -u OPENCODE_SANDBOX_PR_BRANCH "${prenv[@]:1}" OPENCODE_SANDBOX_PR_BRANCH= STUB_BRANCH="$branch" STUB_NOOPEN=1)
  : >"$plog"
  r=$("${nopen[@]}" OPENCODE_SANDBOX_PR_BASE=develop OPENCODE_SANDBOX_CMD='gh pr create --title t --body b 2>&1' "$S" 2>&1)
  o "pr base: develop used" '[[ $r == */pull/79 ]] && grep -q "^pr create --repo $repo --base develop --head $branch --title t " "$plog"'
  : >"$plog"
  r=$("${nopen[@]}" OPENCODE_SANDBOX_CMD='gh pr create --title t --body b 2>&1' "$S" 2>&1)
  o "pr base: default main" '[[ $r == */pull/79 ]] && grep -q "^pr create --repo $repo --base main --head $branch --title t " "$plog"'
  : >"$plog"
  r=$("${nopen[@]}" OPENCODE_SANDBOX_CMD='OPENCODE_SANDBOX_PR_BASE=develop gh pr create --base develop -B develop --title t --body b 2>&1' "$S" 2>&1)
  o "pr base: the lane cannot set it" '[[ $r == */pull/79 ]] && grep -q "^pr create --repo $repo --base main --head $branch --title t " "$plog" && ! grep -q "develop" "$plog"'
  o0 "pr base: not on origin refused" "is not on origin" "${nopen[@]}" OPENCODE_SANDBOX_PR_BASE=sbx-no-such-base OPENCODE_SANDBOX_CMD=true "$S"
  o0 "pr base: bad ref refused" "bad PR base" "${nopen[@]}" "OPENCODE_SANDBOX_PR_BASE=a..b" OPENCODE_SANDBOX_CMD=true "$S"
  o "pr branch: the stub saw no write but pr create" '! grep -Eq "rerun|merge|comment|edit|close|--method|-X " "$plog"'
  # unset: unchanged, the push goes to the lane branch's own name (a fresh lane, no commit of its own: the
  # relay's refresh of the shared repo's branch is then a no-op, the checkout is not moved)
  rm -rf -- "${lanedir:?}"
  r=$(env -u ROVE_TASK_ID -u OPENCODE_SANDBOX_PR_BRANCH "${prenv[@]:1}" OPENCODE_SANDBOX_PR_BRANCH= OPENCODE_SANDBOX_CMD='git push 2>&1; echo "rc=$?"' "$S" 2>&1)
    o "unset: push goes to the lane branch" '[[ $r == *"pushed $branch"* && $r != *"pushed $branch to "* && $r == *rc=0* ]] && [ "$(bref "$branch")" = "$tip" ]'
  o "unset: PR branch untouched" '[ "$(git --git-dir="$bare" rev-parse "refs/heads/$prn^")" = "$tip" ]'
fi
# --- seat mode (OPENCODE_SANDBOX_SEAT=1): a Cotal seat. Stubbed mesh pieces, a dummy credential; the real broker is not used. ---
seatd=$stubdir/seat; mkdir -p "$seatd/plugin" "$seatd/mat"; chmod 700 "$seatd/mat"
free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }
bport=$(free_port); sport=$(free_port)
python3 -c 'import socket,sys
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1",int(sys.argv[1]))); s.listen(8)
while True: c,_=s.accept(); c.send(b"broker\n"); c.close()' "$bport" & broker_pid=$!
trap 'kill "$broker_pid" 2>/dev/null; cleanup' EXIT
# a listener on every host address: what a seat must not reach through a bridge, the LAN or the host's own addresses
lanport=$(free_port)
python3 -c 'import socket,sys
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("0.0.0.0",int(sys.argv[1]))); s.listen(8)
while True: c,_=s.accept(); c.close()' "$lanport" & lan_pid=$!
trap 'kill "$broker_pid" "$lan_pid" 2>/dev/null; cleanup' EXIT
host_addrs=$(ip -o -4 addr show scope global | awk '{ sub(/\/.*/, "", $4); print $4 }' | tr '\n' ' ')
echo 'export default {}' >"$seatd/plugin/index.js"
printf 'sbx-test-not-creds\n' >"$seatd/seat.creds"; chmod 600 "$seatd/seat.creds"
printf 'other-seat\n' >"$seatd/other.creds"; chmod 600 "$seatd/other.creds"
material() { jq -n --arg c "$seatd/seat.creds" --arg s "$1" '{creds:$c, servers:$s, controlToken:"sbx-control"}' >"$seatd/mat/material.json"; chmod 600 "$seatd/mat/material.json"; }
material "nats://127.0.0.1:$bport"
scfg=$(jq -nc --arg p "$seatd/plugin" '{model:"opencode/sbx-test-model", plugin:[$p], permission:"allow"}')
seatrun=(env -u ROVE_TASK_ID -u OPENCODE_SANDBOX_BRANCH OPENCODE_SANDBOX_SEAT=1 "OPENCODE_CONFIG_CONTENT=$scfg" COTAL_NAME=sbx-seat COTAL_SPACE=sbx
  "COTAL_LAUNCH_MATERIAL=$seatd/mat/material.json" COTAL_LIFECYCLE_UID=sbxuid COTAL_FEEDBACK_KEY=sbx-must-not-arrive
  OPENCODE_API_KEY=sbx-must-not-arrive OPENCODE_DB=/nonexistent/agent/opencode.db OPENCODE_SERVER_USERNAME=opencode OPENCODE_SERVER_PASSWORD=sbx-pw)
serve=(serve --hostname 127.0.0.1 --port "$sport")
rm -rf -- "${lanedir:?}"
seat_inside=$(cat <<EOF
fails=0
c() { local name=\$1; shift; local out; out=\$("\$@" 2>&1) && echo "ok   seat: \$name" || { echo "FAIL seat: \$name: \$(printf '%s' "\$out" | tr '\n' ' ' | head -c 300)"; fails=1; }; }
must_fail() { local out; if out=\$("\$@" 2>&1); then echo "succeeded: \$out"; return 1; fi; }
c "broker port reachable"        bash -c 'exec 3<>/dev/tcp/127.0.0.1/$bport'
for port in $ports; do [ \$port = $bport ] || c "other host port \$port refused" must_fail bash -c "exec 3<>/dev/tcp/127.0.0.1/\$port"; done
for a in $host_addrs; do c "host address \$a:$lanport refused" must_fail bash -c "exec 3<>/dev/tcp/\$a/$lanport"; done
c "plugin readable"              test -r $(printf '%q' "$seatd/plugin/index.js")
c "plugin read-only"             must_fail bash -c 'echo x >> $(printf '%q' "$seatd/plugin/index.js")'
c "this seat's credential"       bash -c '[ "\$(cat /run/sandbox/seat/creds)" = sbx-test-not-creds ]'
c "credential read-only"         must_fail bash -c 'echo x >> /run/sandbox/seat/creds'
c "material points inside"       bash -c '[ "\$(jq -r .creds "\$COTAL_LAUNCH_MATERIAL")" = /run/sandbox/seat/creds ] && [ "\$(stat -c %a "\$COTAL_LAUNCH_MATERIAL")" = 600 ]'
c "the real creds dir is absent" bash -c '[ ! -e $(printf '%q' "$seatd/seat.creds") ] && [ ! -e $(printf '%q' "$seatd/other.creds") ]'
c "connection variables"         bash -c '[ "\$COTAL_NAME" = sbx-seat ] && [ "\$COTAL_LIFECYCLE_UID" = sbxuid ] && [ "\$OPENCODE_SERVER_PASSWORD" = sbx-pw ]'
c "no other secrets"             bash -c '[ -z "\${COTAL_FEEDBACK_KEY:-}\${OPENCODE_API_KEY:-}" ]'
c "free models disabled"         bash -c '[ "\$OPENCODE_DISABLE_FREE_MODELS" = 1 ]'
c "no global OpenCode config, MCP servers or Rove plugin" bash -c '[ ! -e "\$HOME/.config/opencode/opencode.jsonc" ] && [ ! -e "\$HOME/.config/opencode/opencode.json" ] && [ ! -e "\$HOME/.config/opencode/plugins/rove-agent-state.js" ]'
c "database in the lane"         bash -c 'case \$OPENCODE_DB in */oc/db/sbx-seat/opencode.db) mkdir -p "\$(dirname "\$OPENCODE_DB")" && touch "\$OPENCODE_DB" ;; *) false ;; esac'
c "config: plugin and allow kept" bash -c 'jq -e ".plugin == [\"$seatd/plugin\"] and .permission == \"allow\"" <<<"\$OPENCODE_CONFIG_CONTENT" >/dev/null'
c "config: provider and key fixed" bash -c 'jq -e ".model == \"opencode/sbx-test-model\" and .enabled_providers == [\"opencode\"] and .provider.opencode.options.apiKey == \"{file:/run/sandbox/lane-key}\"" <<<"\$OPENCODE_CONFIG_CONTENT" >/dev/null'
python3 -m http.server --bind 0.0.0.0 $sport >/dev/null 2>&1 & srv=\$!
for _ in \$(seq 50); do bash -c 'exec 3<>/dev/tcp/127.0.0.1/$sport' 2>/dev/null && break; sleep 0.1; done
touch /run/sandbox/relay/seat-ready
for _ in \$(seq 300); do [ -e /run/sandbox/relay/seat-go ] && break; sleep 0.2; done
kill \$srv 2>/dev/null
exit \$fails
EOF
)
out_file=$stubdir/seat.out
( "${seatrun[@]}" OPENCODE_SANDBOX_CMD="$seat_inside" "$S" "${serve[@]}" >"$out_file" 2>&1 ) & seat_pid=$!
spool=$lanedir/relay/spool
for _ in $(seq 600); do [ -e "$spool/seat-ready" ] && break; sleep 0.2; done
o "seat: server port reachable from host loopback" '[ "$(curl -sS -m 5 -o /dev/null --noproxy "*" -w "%{http_code}" "http://127.0.0.1:$sport/")" = 200 ]'
touch "$spool/seat-go"; seat_rc=0; wait "$seat_pid" || seat_rc=$?
grep -E '^(ok|FAIL) ' "$out_file"; [ "$seat_rc" = 0 ] && [ -z "$(grep '^FAIL' "$out_file")" ] || { fail=1; [ -s "$out_file" ] && grep -q '^ok' "$out_file" || tail -n 5 "$out_file"; }
o "seat: the branch is recorded outside the sandbox" '[ "$(cat "$lanedir/seat-branch")" = "$branch" ]'
for _ in $(seq 40); do [ -z "$(find "$lanedir/seat/sbx-seat" -type f 2>/dev/null)" ] && break; sleep 0.5; done
o "seat: the credential copy is gone after the seat" '[ -z "$(find "$lanedir/seat/sbx-seat" -type f 2>/dev/null)" ]'
# a restart under the same name: the old seat's watchdog must not take the new seat's files
"${seatrun[@]}" OPENCODE_SANDBOX_CMD='sleep 60' "$S" serve --hostname 127.0.0.1 --port "$(free_port)" >/dev/null 2>&1 &
old=$!; for _ in $(seq 60); do [ -n "$(find "$lanedir/seat/sbx-seat" -name creds 2>/dev/null)" ] && break; sleep 0.5; done
kill -TERM "$old" 2>/dev/null; wait "$old" 2>/dev/null
r=$("${seatrun[@]}" OPENCODE_SANDBOX_CMD='sleep 9; [ -r /run/sandbox/seat/material.json ] && [ -r /run/sandbox/seat/creds ] && echo still-there' "$S" serve --hostname 127.0.0.1 --port "$(free_port)" 2>&1)
o "seat: a restart keeps its files when the old seat's watchdog runs" '[[ $r == *still-there* ]]'
material "nats://203.0.113.9:$bport"
o0 "seat: a broker off loopback is refused" "not one nats:// address on host loopback" "${seatrun[@]}" OPENCODE_SANDBOX_CMD=true "$S" "${serve[@]}"
jq '. + {userAuth:{owner:"x"}}' "$seatd/mat/material.json" >"$seatd/mat/m2"; mv "$seatd/mat/m2" "$seatd/mat/material.json"; chmod 600 "$seatd/mat/material.json"
o0 "seat: a user-auth mesh is refused" "user-auth mesh" "${seatrun[@]}" OPENCODE_SANDBOX_CMD=true "$S" "${serve[@]}"
material "nats://127.0.0.1:$bport"
o0 "seat: a model off OpenCode's providers is refused" "run in the sandbox" "${seatrun[@]}" 'OPENCODE_CONFIG_CONTENT={"model":"deepseek/x","plugin":[]}' OPENCODE_SANDBOX_CMD=true "$S" "${serve[@]}"
o0 "seat: a serve without --port is refused" "seat mode needs serve" "${seatrun[@]}" OPENCODE_SANDBOX_CMD=true "$S" serve
# the proof-only free switch: a seat on an opencode/*-free model only, and only with the switch
o0 "free switch: refused outside a seat" "only for a seat" env -u ROVE_TASK_ID OPENCODE_SANDBOX_PROOF_FREE=1 OPENCODE_SANDBOX_BRANCH="$branch" OPENCODE_SANDBOX_CMD=true "$S"
o0 "free switch: refused on a paid model" "needs an opencode/*-free model" "${seatrun[@]}" OPENCODE_SANDBOX_PROOF_FREE=1 OPENCODE_SANDBOX_CMD=true "$S" "${serve[@]}"
r=$("${seatrun[@]}" "OPENCODE_CONFIG_CONTENT=$(jq -nc --arg p "$seatd/plugin" '{model:"opencode/sbx-test-free", plugin:[$p]}')" OPENCODE_SANDBOX_PROOF_FREE=1 OPENCODE_SANDBOX_CMD='[ -z "${OPENCODE_DISABLE_FREE_MODELS:-}" ] && echo free-enabled' "$S" "${serve[@]}" 2>&1)
o "free switch: free models on, for a -free seat only" '[[ $r == *free-enabled* ]]'
kill "$broker_pid" "$lan_pid" 2>/dev/null
if [ "${SBX_TEST_PUSH:-}" = 1 ]; then
  main_before=$(git ls-remote origin refs/heads/main | cut -c1-40)
  pout=$(env -u ROVE_TASK_ID OPENCODE_SANDBOX_BRANCH="$branch" OPENCODE_SANDBOX_CMD='git -c user.name=t -c user.email=t@t commit -q --allow-empty -m sbx-push && git push origin HEAD:main' "$S" 2>&1)
  o "push reaches the lane branch only" '[ "$(git ls-remote origin "refs/heads/$branch" | cut -c1-40)" = "$(git rev-parse "refs/heads/$branch")" ] && [ "$(git ls-remote origin refs/heads/main | cut -c1-40)" = "$main_before" ]'
  echo "     push: $(printf '%s' "$pout" | tail -n 1)"
fi
exit $fail
