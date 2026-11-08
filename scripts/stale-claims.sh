#!/usr/bin/env bash
# Daily stale-claim check (issues.md): in-progress beads issues whose task key (the `lane:<KEY>` label; old
# issues: external_ref) has no live Rove task go to that project's coordinator. Spends no tokens. Needs COORD_REPO in the config.
here=$(dirname "$0"); . "$here/lib.sh"
live=$(rove api list | jq -r '.tasks[].title' | grep -oE '^[A-Z]+-[0-9]+' | sort -u)
for p in "${!COORD_REPO[@]}"; do
  [ -n "${COORD_TASK[$p]:-}" ] || continue
  stale=$(cd "${COORD_REPO[$p]}" && bd list --status in_progress --json --limit 0 2>/dev/null |
    jq -r '.[] | "\(.id) \(([(.labels // [])[] | select(test("^lane:[A-Z]+-[0-9]+$")) | .[5:]][0]) // .external_ref // "-") \(.updated_at[:10]) \(.title[:60])"' |
    while read -r id key day title; do grep -qx -- "$key" <<<"$live" || echo "$id $key $day $title"; done)
  if [ -n "$stale" ]; then
    n=$(wc -l <<<"$stale")
    rove api send --task-id "${COORD_TASK[$p]}" --tab "$COORD_TAB" --prompt-file - >/dev/null 2>&1 <<MSG
FYI (stale-claim check): $n in-progress issue(s) without a live task. Per issues.md: back to open with a Resume note, or close if landed. No reply.
$stale
MSG
  fi
  echo "$p: ${n:-0} stale"; n=0
done
