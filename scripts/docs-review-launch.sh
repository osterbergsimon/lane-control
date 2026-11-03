#!/usr/bin/env bash
# Weekly docs review: launch a lane on DOCS_REVIEW_TIER as a child of a project coordinator, then nudge
# it once if the engine left the first prompt unsubmitted (known paste bug).
# Usage: docs-review-launch.sh <PROJECT>
# Needs COORD_TASK[<PROJECT>] and $BRIEF_DIR/<PROJECT>-docs-review.md (from templates/docs-review-brief.md).
set -euo pipefail
here=$(dirname "$0"); . "$here/lib.sh"
p=${1:?usage: docs-review-launch.sh <PROJECT>}
parent=${COORD_TASK[$p]:?no coordinator for $p in config}
t=$DOCS_REVIEW_TIER
[ -n "${TIER_ENGINE[$t]:-}" ] && [ "${TIER_ENGINE[$t]}" != none ] || { echo "tier $t is not configured"; exit 1; }
d=$(date +%F)
cmd=${TIER_CMD[$t]:-}
repo=${COORD_REPO[$p]:?no repo for $p in config}
# base on the default branch, not the coordinator checkout's current branch (add nests under --parent)
base=$(git -C "$repo" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##'); base=${base:-main}
R=$(rove api add --repo "$repo" --parent "$parent" --base-branch "$base" --title "Weekly docs review $d" \
      ${cmd:+--command "$cmd"} --prompt-file "$BRIEF_DIR/$p-docs-review.md")
id=$(printf '%s' "$R" | jq -r '.taskId')
rove api update --task-id "$id" --branch "docs-review-$d" >/dev/null
w=$(rove api get-task --task-id "$id" | jq -r '.task.worktreePath')
sleep "$DOCS_REVIEW_WAIT"
if [ -z "${DOCS_REVIEW_SUBMITTED_CMD:-}" ] || [ -z "$(cd "$w" && bash -c "$DOCS_REVIEW_SUBMITTED_CMD" 2>/dev/null)" ]; then
  rove api send --task-id "$id" --tab "$COORD_TAB" --plain --prompt "Start now: follow the brief above." >/dev/null
  echo "nudged"
fi
echo "launched $id ($w)"
