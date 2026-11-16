#!/usr/bin/env bash
# Rove runs this once per new task worktree, in the worktree, before the agent starts
# (ROVE_TASK_ID set). A worktree has no private data (gitignored, local only): link the main
# checkout's private data dirs that exist, except for tasks whose engine sends what it reads to a
# third-party API (code-only lanes, see AGENTS.md "Third-party lanes").
set -euo pipefail

PRIVATE_DIRS=("<private-dir-1>" "<private-dir-2>")   # fill in; empty () if the project has none
THIRD_PARTY_VENDOR="<vendor>"                     # the vendor string Rove reports for third-party lanes

vendor=""
if [ -n "${ROVE_TASK_ID:-}" ] && command -v rove >/dev/null; then
  vendor=$(rove api get-task --task-id "$ROVE_TASK_ID" 2>/dev/null \
    | sed -n 's/.*"vendor":"\([^"]*\)".*/\1/p' | head -n 1) || vendor=""
fi

main=$(git worktree list --porcelain | sed -n '1s/^worktree //p')
if [ "$vendor" = "$THIRD_PARTY_VENDOR" ]; then
  echo "init.sh: third-party task, private data not linked (code-only lane)"
elif [ -n "$main" ] && [ "$main" != "$PWD" ]; then
  for d in "${PRIVATE_DIRS[@]}"; do
    if [ -d "$main/$d" ] && [ ! -e "$d" ]; then
      ln -s "$main/$d" "$d"
    fi
  done
fi
