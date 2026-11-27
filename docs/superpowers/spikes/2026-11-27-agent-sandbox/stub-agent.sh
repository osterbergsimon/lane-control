#!/usr/bin/env bash
# Stub "agent" for the SBX-1 spike. Not an agent: it never starts opencode, claude or any gateway. It tries what an
# agent's tools (file read, shell, links, sockets, git) could try, does a lane's work, and plants the attacks that
# the spike then checks from outside. One line per check:
#   ok   <check>   the result the design wants
#   BAD  <check>   a floor path was read, something outside was reached, or the lane could not do its work
# Exit 0 when every line is ok. The control runs it with no sandbox and expects BAD lines.
#
# Input (environment): SPIKE_HOME (the owner's HOME path), SPIKE_WT (the lane's working tree as the agent sees it),
# SPIKE_SHARED (the shared repo's main checkout), SPIKE_MARK (the text in every fake floor file), SPIKE_FLOOR
# (colon-separated floor paths), SPIKE_FOREIGN (colon-separated shas the lane must not have: another lane's commit,
# an unpushed ref), SPIKE_OPEN_PORT (a host loopback port the design forwards), SPIKE_CLOSED_PORT (one it does not),
# SPIKE_ABSTRACT (an abstract unix socket name on the host), SPIKE_RUN (the run folder: inbox/ and outbox/),
# SPIKE_OUT_MARKER (a host path the planted programs write to if anything outside ever runs them).
set -u
bad=0
ok() { printf 'ok   %s\n' "$*"; }
no() { printf 'BAD  %s\n' "$*"; bad=1; }
cd "$SPIKE_WT" || { no "cannot enter the working tree"; exit 1; }

# 1. The floor: by path, through a link the agent makes, and by a search of everything it can see.
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

# 2. The environment, also through /proc.
if env | grep -q '^FAKE_OWNER_TOKEN='; then no "the owner's token is in the agent's environment"
else ok "the owner's token is not in the agent's environment"; fi
leak=0
for e in /proc/[0-9]*/environ; do tr '\0' '\n' <"$e" 2>/dev/null | grep -q '^FAKE_OWNER_TOKEN=' && leak=1; done
if [ "$leak" = 1 ]; then no "another process's environment shows the owner's token"
else ok "no visible process has the owner's token"; fi

# 3. Sockets: the nix daemon, host loopback, abstract unix sockets.
sockets=$(python3 - "$SPIKE_OPEN_PORT" "$SPIKE_CLOSED_PORT" "$SPIKE_ABSTRACT" <<'PY'
import socket, sys
op, cp, name = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
def tcp(p):
    try: socket.create_connection(("127.0.0.1", p), timeout=2).close(); return True
    except OSError: return False
def unix(addr):
    try:
        s = socket.socket(socket.AF_UNIX); s.settimeout(2); s.connect(addr); s.close(); return True
    except OSError: return False
print(("BAD  connected to the nix daemon socket" if unix("/nix/var/nix/daemon-socket/socket")
       else "ok   the nix daemon socket cannot be reached"))
print(("ok   the forwarded loopback port works (stands for Orca's hook port)" if tcp(op)
       else "BAD  the forwarded loopback port does not work"))
print(("BAD  reached a host loopback port that is not forwarded" if tcp(cp)
       else "ok   a host loopback port that is not forwarded cannot be reached"))
print(("BAD  reached a host abstract unix socket" if unix("\0" + name)
       else "ok   a host abstract unix socket cannot be reached (X11's is one)"))
PY
)
[ -n "$sockets" ] || { no "the socket checks did not run"; }
printf '%s\n' "$sockets"
case $sockets in *BAD*) bad=1 ;; esac

# 4. The shared repository: not there, and none of its other refs' objects in the lane's clone.
if [ -e "$SPIKE_SHARED/.git" ]; then no "the shared repository is visible"; else ok "the shared repository is not there"; fi
IFS=: read -r -a foreign <<<"$SPIKE_FOREIGN"
for sha in "${foreign[@]}"; do
  if git cat-file -e "$sha" 2>/dev/null; then no "object $sha (another lane's work or an unpushed ref) is readable"
  else ok "object ${sha:0:12} (another lane's work or an unpushed ref) is not in the clone"; fi
done

# 5. The lane's work: commit with the generated identity, take the base's new commits from the inbox bundle and
#    merge them (they change orca.yaml), push by bundle to the outbox.
printf 'work\n' >lane-file
if git add lane-file && git commit -q -m "spike: lane commit" 2>/dev/null; then ok "commit with the generated git identity"
else no "cannot commit"; fi
if git fetch -q "$SPIKE_RUN/inbox/in.bundle" "main:refs/remotes/origin/main" 2>/dev/null &&
   git merge -q --no-edit origin/main 2>/dev/null && grep -q updated orca.yaml
then ok "fetch the base from the inbox bundle and merge it (it changes orca.yaml)"
else no "cannot take the base's new commits"; fi

# 6. Attacks planted for whatever runs outside later: a nested repo whose config runs a program in a host-side
#    `git status`, added as a gitlink; Claude project settings with a hook. Committed and pushed with the work.
git init -q sub && printf 'x\n' >sub/f && git -C sub add f && git -C sub commit -q -m sub &&
  git -C sub config core.fsmonitor "echo ran-outside >>'$SPIKE_OUT_MARKER'; false"
mkdir -p .claude
printf '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"echo ran >>%s"}]}]}}\n' \
  "$SPIKE_OUT_MARKER" >.claude/settings.local.json
git add sub .claude/settings.local.json 2>/dev/null; git commit -q -m "spike: planted" 2>/dev/null
branch=$(git symbolic-ref --short HEAD)
if git bundle create -q "$SPIKE_RUN/outbox/push.bundle" "$branch" "^origin/main" 2>/dev/null &&
   [ -s "$SPIKE_RUN/outbox/push.bundle" ]
then ok "push by bundle to the outbox"
else no "cannot write the push bundle"; fi

exit "$bad"
