#!/usr/bin/env bash
# Machine night watch: a Rove script routine, hourly overnight, with a precheck that MODE_FILE says night.
# Spends no machine-coordinator tokens. Per project coordinator, in order:
#  1. Skip it when its budget is past the stop-all step (each prod costs a turn).
#  2. NEEDED check: read `rove api context --repo <repo>` (+ `bd ready`, get-task) and prod only when:
#     (a) a row is waiting-on-you / ready-for-review / landing / unknown, or an unhandled inbox item;
#     (b) a `working` lane's engine tab is dead;
#     (c) idle capacity: ready issues > 0, some tier's allowance is not "none", and fewer `working`
#         lanes than COORD_SLOTS[project] (default 1);
#     (d) a stalled lane: working/idle with last activity older than STALL_SECS (2 h; needs an age in the row).
#     Otherwise log "idle, not woken": no tab revive, no send, so a cold cache is never paid for nothing.
#     Unreadable/unrecognised context also prods (fail safe). A prod revives the tab, states which
#     criterion fired and gives tonight's launch allowance per tier.
#  3. FIRST run of the night only (marker under ${XDG_STATE_HOME:-~/.local/state}/lane-control/, keyed by
#     the night's date): each coordinator with a live tab gets a handover request (bd remember --key
#     handover-latest) and then a plain `/compact`, so its next resume loads a short summary.
#     Both are sent only when the tab is runnable (`should-run`), never into a running turn: the script
#     waits (NIGHT_COMPACT_WAIT, default 900 s) for the turn, incl. a prod from step 2, to end; on timeout
#     the /compact is skipped and logged. Run per coordinator in parallel.
# A tier on a budget without a usage feed (none/manual) is allowed "within caps" unless memory is short.
set -uo pipefail
here=$(dirname "$0"); . "$here/lib.sh"
mkdir -p "$NIGHT_START_DIR"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/lane-control"; mkdir -p "$STATE_DIR"
night_key=$(date -d '-7 hours' +%F)          # 22:05-06:05 is one night
marker="$STATE_DIR/night-compact-$night_key"
first_run=""; [ -e "$marker" ] || { first_run=1; : > "$marker"; find "$STATE_DIR" -name 'night-compact-*' -mtime +7 -delete 2>/dev/null; }
STALL_SECS=${STALL_SECS:-7200}
NIGHT_COMPACT_WAIT=${NIGHT_COMPACT_WAIT:-900}; NIGHT_POLL_SECS=${NIGHT_POLL_SECS:-15}; NIGHT_SETTLE_SECS=${NIGHT_SETTLE_SECS:-20}
avail=$(mem_avail_gb)
allowance() {  # allowance <ceiling add>: sets allow and summary
local t b a wk ss ceil; allow=""; summary=""
for t in $TIERS; do
  [ -z "${TIER_ENGINE[$t]:-}" ] || [ "${TIER_ENGINE[$t]}" = none ] && continue
  [ "$t" = top ] && continue   # top tier is never launched by the watch
  b=${TIER_BUDGET[$t]:-none}
  if [ "$avail" -lt "$NIGHT_MEM_MIN_GB" ]; then a="none: ${avail} GB memory available"
  elif [ -n "${BUDGET_USAGE[$b]:-}" ]; then
    read -r wk ss < <(budget_usage "$b"); ceil=$(( $(budget_ceiling "$b") + ${1:-0} ))
    # the only budget limit is the owner's weekly ceiling steps (no nightly share)
    if   [ "$wk" -ge $((ceil-STEP_NO_NEW)) ]; then a="none: budget $b weekly ${wk}% at ceiling-${STEP_NO_NEW} (${ceil}%)"
    elif [ "$ss" -ge "$WINDOW_NIGHT_PCT" ]; then a="none until the window resets (budget $b at ${ss}%)"
    else a="within caps"; fi
    case "$summary" in *" $b:"*) ;; *) summary="$summary $b: weekly ${wk}% (ceiling ${ceil}%), window ${ss}%;" ;; esac
  else a="within caps (no usage feed: $b)"; fi
  allow="$allow $t: $a;"
done
}
# needed <project> <task>: sets reason (empty = nothing needs the coordinator)
needed() {
  local p=$1 t=$2 repo=${COORD_REPO[$1]:-} ctx sum n ready slots working id
  reason=""
  [ -n "$repo" ] || { reason="no repo configured, cannot read context"; return; }
  ctx=$(rove api context --repo "$repo" 2>/dev/null)
  sum=$(printf '%s' "$ctx" | jq -c --argjson stall "$STALL_SECS" '
    def tasks: (.tasks // .rows // .worktrees // []);
    def g: (.group // "unknown");
    def title: (.title // .branch // .taskId // .id // "?");
    def age: ( .activitySecs // .activitySeconds // .idleSecs // .activity.secs // .activity.seconds // .activity.sinceSecs // .activity.forSecs
               // ((.lastActivityAt // .activity.since // .activitySince // null) as $ts
                    | if $ts == null then null
                      elif ($ts|type)=="number" then (now - (if $ts > 1e12 then $ts/1000 else $ts end))
                      else (try (now - ($ts | sub("\\.[0-9]+";"") | fromdateiso8601)) catch null) end) );
    if (has("tasks") or has("rows") or has("worktrees")) then
    { ok: true,
      attn: [tasks[] | select(g | IN("waiting-on-you","ready-for-review","landing","unknown")) | "\(g): \(title)"],
      inbox: ((.inbox // .attention // .unhandled // []) | if type=="array" then length elif type=="object" then ((.items // .rows // []) | length) else 0 end),
      working: [tasks[] | select(g=="working") | (.taskId // .id // "")],
      stalled: [tasks[] | select(g | IN("working","idle")) | select((age) != null and (age) > $stall) | title] }
    else {ok: false} end' 2>/dev/null)
  [ "$(printf '%s' "$sum" | jq -r '.ok // false' 2>/dev/null)" = true ] || { reason="context unreadable or unrecognised, prodding to be safe"; return; }
  n=$(printf '%s' "$sum" | jq -r '.attn|length'); inbox=$(printf '%s' "$sum" | jq -r '.inbox')
  if [ "$n" -gt 0 ]; then reason="$n row(s) need attention ($(printf '%s' "$sum" | jq -r '.attn|join("; ")'))"; return; fi
  if [ "$inbox" -gt 0 ]; then reason="$inbox unhandled inbox item(s)"; return; fi
  # (b) a running lane whose engine tab is dead
  for id in $(printf '%s' "$sum" | jq -r '.working[]'); do
    [ -n "$id" ] || continue
    if [ "$(rove api get-task --task-id "$id" 2>/dev/null | jq -r '.running | tostring' 2>/dev/null)" = false ]; then
      reason="working lane $id has a dead engine tab"; return; fi
  done
  # (d) stalled lane
  n=$(printf '%s' "$sum" | jq -r '.stalled|length')
  [ "$n" -gt 0 ] && { reason="stalled lane(s), no activity for over $((STALL_SECS/3600)) h: $(printf '%s' "$sum" | jq -r '.stalled|join("; ")')"; return; }
  # (c) idle capacity
  working=$(printf '%s' "$sum" | jq -r '.working|length'); slots=${COORD_SLOTS[$p]:-1}
  if [ "$working" -lt "$slots" ] && [[ "$allow" == *": within caps"* ]]; then
    ready=$(cd "$repo" 2>/dev/null && bd ready --json 2>/dev/null | jq -r 'if type=="array" then length else ((.issues // []) | length) end' 2>/dev/null)
    [ "${ready:-0}" -gt 0 ] && reason="free slot ($working/$slots working lanes), $ready ready issue(s)"
  fi
}
# wait_runnable <task>: poll should-run (exit 0 = a turn would be used) up to NIGHT_COMPACT_WAIT seconds
wait_runnable() {
  local t=$1 waited=0
  until rove api should-run --task-id "$t" --tab "$COORD_TAB" >/dev/null 2>&1; do
    [ "$waited" -ge "$NIGHT_COMPACT_WAIT" ] && return 1
    sleep "$NIGHT_POLL_SECS"; waited=$((waited+NIGHT_POLL_SECS))
  done
}
# compact_one <project> <task>: handover request, then a plain /compact, each only into an idle turn
compact_one() {
  local p=$1 t=$2 r
  wait_runnable "$t" || { echo "$p: compact skipped (tab busy for ${NIGHT_COMPACT_WAIT}s, no handover sent)"; return; }
  r=$(rove api send --if-runnable --task-id "$t" --tab "$COORD_TAB" --prompt-file - <<MSG
[NIGHT WATCH $(date +%H:%M)] Your context is about to be compacted so tonight's resumes stay cheap. Refresh your handover now, then stop: bd remember "<summary>" --key handover-latest. Include: ${HANDOVER_HINT}. Make no other changes this turn.
MSG
)
  [ "$(printf '%s' "$r" | jq -r '.delivered // false' 2>/dev/null)" = true ] || { echo "$p: handover not delivered, /compact skipped: $r"; return; }
  sleep "$NIGHT_SETTLE_SECS"
  wait_runnable "$t" || { echo "$p: handover sent; /compact skipped (turn still running after ${NIGHT_COMPACT_WAIT}s)"; return; }
  r=$(rove api send --if-runnable --plain --task-id "$t" --tab "$COORD_TAB" --prompt "/compact")
  echo "$p: handover + /compact sent: $(printf '%s' "$r" | jq -c '{delivered, skipped: .skipped.reason}' 2>/dev/null || echo "$r")"
}
tmpd=$(mktemp -d); pids=()
for p in "${!COORD_TASK[@]}"; do
  t=${COORD_TASK[$p]}; allowance "${COORD_CEILING_ADD[$p]:-0}"
  stopped=""   # past its stop-all step on a metered budget: do not wake it (each prod costs a turn)
  for b in $(metered); do read -r wk _ < <(budget_usage "$b")
    [ "$wk" -ge $(( $(budget_ceiling "$b") + ${COORD_CEILING_ADD[$p]:-0} - STEP_STOP_ALL )) ] && stopped=$b; done
  [ -n "$stopped" ] && { echo "$p: skipped (budget $stopped past stop-all)"; continue; }
  needed "$p" "$t"
  if [ -n "$reason" ]; then
    rove api tab-revive --task-id "$t" --tab "$COORD_TAB" >/dev/null 2>&1
    r=$(rove api send --if-runnable --expect-report --task-id "$t" --tab "$COORD_TAB" --prompt-file - <<MSG
[NIGHT WATCH $(date +%H:%M)] Owner away. Why you are woken: ${reason}. Check your lanes (context): nudge stuck or unsubmitted first prompts, revive dead tabs, handle reports per your standing rules, enforce the wall-clock and stall limits (model-routing.md, Unattended limits). New launches per tier:${allow} Owner decisions go to gates. Report tersely.
MSG
)
    echo "$p: prodded ($reason): $(printf '%s' "$r" | jq -c '{delivered, skipped: .skipped.reason}' 2>/dev/null || echo "$r")"
  else
    echo "$p: idle, not woken"
  fi
  if [ -n "$first_run" ]; then
    if [ "$(rove api get-task --task-id "$t" 2>/dev/null | jq -r --arg tab "$COORD_TAB" '[.tabs[]? | select(.id==$tab) | .alive] | first // false' 2>/dev/null)" = true ]; then
      compact_one "$p" "$t" > "$tmpd/$p" 2>&1 & pids+=($!)
    else echo "$p: tab not alive, no compact"; fi
  fi
done
[ "${#pids[@]}" -gt 0 ] && wait "${pids[@]}"
cat "$tmpd"/* 2>/dev/null; rm -rf "$tmpd"
echo "mem ${avail} GB;${summary} allowance:${allow}"
