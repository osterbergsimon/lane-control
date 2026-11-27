#!/usr/bin/env bash
# Stub "agent" for the SBX-1 spike. Not an agent: it never starts opencode, claude or any gateway. It tries what an
# agent's tools (file read, shell, links, edit) could try, and prints one line per check:
#   ok   <check>   the result the sandbox wants
#   BAD  <check>   a floor path was read, a protected file was written, or the lane could not do its work
# Exit status: 0 when every line is ok, 1 otherwise. The positive control runs the same script without the sandbox
# and expects BAD lines, which shows the checks can see a leak.
#
# Input (environment, set by spike.sh): SPIKE_HOME, SPIKE_WT, SPIKE_COMMON, SPIKE_OTHER_WT, SPIKE_MARK (the text
# every fake floor file holds), SPIKE_FLOOR (colon-separated floor paths), SPIKE_RO (colon-separated paths that must
# not be writable).
set -u
bad=0
ok() { printf 'ok   %s\n' "$*"; }
no() { printf 'BAD  %s\n' "$*"; bad=1; }

# 1. Floor paths: read by path, by a link planted in the worktree, and by a search of everything the agent can see.
IFS=: read -r -a floor <<<"$SPIKE_FLOOR"
for f in "${floor[@]}"; do
  if cat -- "$f" 2>/dev/null | grep -q "$SPIKE_MARK"; then no "read floor file $f"
  elif [ -e "$f" ]; then ok "floor file $f is there but holds no floor data"
  else ok "floor file $f does not exist"
  fi
done
if cat "$SPIKE_WT/link-to-key" 2>/dev/null | grep -q "$SPIKE_MARK"; then no "read the floor through a link in the worktree"
else ok "a link in the worktree to a floor file reads nothing"
fi
hits=$(grep -rIl --exclude=stub-agent.sh -- "$SPIKE_MARK" "$SPIKE_HOME" "$SPIKE_WT" "$SPIKE_COMMON" /tmp 2>/dev/null)
if [ -n "$hits" ]; then no "search found floor data in $(printf '%s\n' "$hits" | wc -l) files, first $(printf '%s\n' "$hits" | head -1)"
else ok "a recursive search of home, worktree, git dir and /tmp finds no floor data"
fi

# 2. The environment: no owner token, and no other process's environment through /proc.
if env | grep -q '^FAKE_OWNER_TOKEN='; then no "the owner's token is in the agent's environment"
else ok "the owner's token is not in the agent's environment"
fi
leak=0
for e in /proc/[0-9]*/environ; do
  if tr '\0' '\n' <"$e" 2>/dev/null | grep -q '^FAKE_OWNER_TOKEN='; then leak=1; fi
done
if [ "$leak" = 1 ]; then no "another process's environment shows the owner's token"
else ok "no process the agent can see has the owner's token ($(ls -d /proc/[0-9]* | wc -l) processes visible)"
fi

# 3. Files that set the agent's limits, and git's program-running files: never writable.
IFS=: read -r -a ro <<<"$SPIKE_RO"
for f in "${ro[@]}"; do
  if { printf '{"policies":[{"action":"tool.use","resource":"*","effect":"allow"}]}\n' >>"$f"; } 2>/dev/null
  then no "wrote $f"
  else ok "cannot write $f"
  fi
done
if git -C "$SPIKE_WT" config --local spike.lifted true 2>/dev/null; then no "git config --local wrote the shared config"
else ok "git config --local cannot write the shared config"
fi
if { : >"$SPIKE_OTHER_WT/lane-other-file"; } 2>/dev/null; then no "wrote into another lane's worktree"
else ok "cannot write another lane's worktree"
fi

# 4. The lane's own work: write the worktree and commit on its branch.
if printf 'work\n' >"$SPIKE_WT/lane-file" 2>/dev/null; then ok "can write the worktree"
else no "cannot write the worktree"
fi
if git -C "$SPIKE_WT" add lane-file 2>/dev/null &&
   git -C "$SPIKE_WT" -c user.name=spike -c user.email=spike@example.invalid commit -q -m "spike: lane commit" 2>/dev/null
then ok "can commit in the worktree (objects and refs in the common git dir are writable)"
else no "cannot commit in the worktree"
fi

exit "$bad"
