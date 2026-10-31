#!/usr/bin/env bash
# Daily parked-lane check (issues.md "Parking"): per project (COORD_REPO), every `parked` issue must
# have notes starting "Resume (<date>): branch <b> @ <commit>; ...", that branch on origin containing the
# commit, and not be closed. Parked more than PARK_MAX_DAYS (7) days is reported too. Problems go to that
# project's coordinator in one message; a clean run sends nothing. Spends no tokens.
# Run by the same Rove script routine as stale-claims.sh (add it next to that one, daily).
here=$(dirname "$0"); . "$here/lib.sh"
PARK_MAX_DAYS=${PARK_MAX_DAYS:-7}
for p in "${!COORD_REPO[@]}"; do
  [ -n "${COORD_TASK[$p]:-}" ] || continue
  repo=${COORD_REPO[$p]}; probs=""; n=0
  git -C "$repo" fetch -q origin 2>/dev/null
  rows=$(cd "$repo" && bd list --label parked --all --json --limit 0 2>/dev/null |
    jq -r '(if type=="array" then . else (.issues // []) end)[] | [.id, .status, (.updated_at // "")[:10], ((.notes // "") | split("\n")[0])] | @tsv')
  while IFS=$'\t' read -r id status upd note; do
    [ -n "$id" ] || continue
    msg=""
    if [ "$status" = closed ]; then msg="closed but still labelled parked (remove the label)"
    elif [[ "$note" =~ ^Resume\ \(([^\)]*)\):\ branch\ ([^\ ]+)\ @\ ([0-9a-f]{7,40})([;\ ]|$) ]]; then
      d=${BASH_REMATCH[1]}; br=${BASH_REMATCH[2]}; c=${BASH_REMATCH[3]}
      if [ -z "$(git -C "$repo" ls-remote --heads origin "$br" 2>/dev/null)" ]; then msg="branch $br not on origin"
      elif ! git -C "$repo" branch -r --contains "$c" 2>/dev/null | grep -qx -- "[[:space:]]*origin/$br"; then msg="commit $c not on origin/$br"; fi
      ts=$(date -d "$d" +%s 2>/dev/null) || ts=$(date -d "$upd" +%s 2>/dev/null)
      if [ -n "$ts" ] && [ $(( ($(date +%s) - ts) / 86400 )) -gt "$PARK_MAX_DAYS" ]; then
        msg="${msg:+$msg; }parked since $d (over $PARK_MAX_DAYS days, resume or close)"; fi
    else msg="notes do not start with 'Resume (<date>): branch <b> @ <commit>; next: ...; open: ...'"; fi
    [ -n "$msg" ] && { probs="$probs$id: $msg"$'\n'; n=$((n+1)); }
  done <<<"$rows"
  if [ "$n" -gt 0 ]; then
    rove api send --task-id "${COORD_TASK[$p]}" --tab "$COORD_TAB" --prompt-file - >/dev/null 2>&1 <<MSG
FYI (parked-lane check): $n parked issue(s) with a problem. Per issues.md Parking: fix the Resume note / push the branch, resume, or close. No reply.
$probs
MSG
  fi
  echo "$p: $n parked problem(s)"
done
