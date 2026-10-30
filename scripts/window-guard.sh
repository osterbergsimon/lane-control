#!/usr/bin/env bash
# Window guard: watches each metered budget's short window (e.g. a 5-hour limit) and messages the
# project coordinators. Never exits on its own and spends no tokens. Run it in the background.
#   >= WINDOW_WARN_PCT: start nothing new on that budget.
#   >= WINDOW_STOP_PCT: limit imminent; have lanes commit and push WIP and update Resume notes.
#   after the reset:    rebuild from rove api context and resume stalled lanes.
# Weekly ceiling steps, once each per climb: ceiling-15 pressure on (no night launches), -10 no new
# work on the budget, -7 lanes push WIP and stop, -4 everything on the budget stops; pressure off when back under -15.
# Reset times vary: read from BUDGET_RESET (a command printing the window's reset as epoch seconds).
here=$(dirname "$0"); . "$here/lib.sh"
declare -A wstage stage reset_at handled   # handled: last reset acted on (a stale feed repeats it)
tell() {  # tell <message>: every coordinator, plain send (a busy session queues it)
  local p
  for p in "${!COORD_TASK[@]}"; do
    rove api send --task-id "${COORD_TASK[$p]}" --tab "$COORD_TAB" --prompt "$1" >/dev/null 2>&1
  done
  echo "$(date +%H:%M) $1"
}
while true; do
  for b in $(metered); do
    read -r wk ss < <(budget_usage "$b")
    rs=$(bash -c "${BUDGET_RESET[$b]:-}" 2>/dev/null); rs=${rs:-0}
    now=$(date +%s); st=${stage[$b]:-0}
    if [ "$st" -gt 0 ] && [ "${reset_at[$b]:-0}" -gt 0 ] && [ "$now" -gt $(( ${reset_at[$b]} + 60 )) ]; then
      tell "FYI (window guard): budget $b window has reset. Rebuild from rove api context (a report that arrived at the limit may be unprocessed), resume stalled lanes, launch per your caps."
      handled[$b]=${reset_at[$b]}; stage[$b]=0; reset_at[$b]=0; continue
    fi
    [ "$ss" -ge 100 ] && [ "$rs" = 0 ] && continue   # failed feed reads 100: never act on it
    ceil=$(budget_ceiling "$b"); ws=${wstage[$b]:-0}; tiers=$(budget_tiers "$b")
    if   [ "$wk" -ge $((ceil-4)) ]  && [ "$ws" -lt 4 ]; then wstage[$b]=4; tell "ORDER (budget guard): budget $b weekly ${wk}% >= ceiling-4 (${ceil}%). Stop all work on tiers ${tiers}now: lanes commit and push WIP and stop; coordinators too. Your last act before the limit: write your handover summary (${HANDOVER_HINT:-done, open, decisions for the owner, how to resume; detailed enough for a new session}). No reply."
    elif [ "$wk" -ge $((ceil-7)) ]  && [ "$ws" -lt 3 ]; then wstage[$b]=3; tell "ORDER (budget guard): budget $b weekly ${wk}% >= ceiling-7 (${ceil}%). Lanes on tiers ${tiers}commit and push WIP and stop; coordinators only review and merge. Your last act before the limit: write your handover summary (${HANDOVER_HINT:-done, open, decisions for the owner, how to resume; detailed enough for a new session}). No reply."
    elif [ "$wk" -ge $((ceil-10)) ] && [ "$ws" -lt 2 ]; then wstage[$b]=2; tell "ORDER (budget guard): budget $b weekly ${wk}% >= ceiling-10 (${ceil}%). No new work on tiers ${tiers}; running lanes finish. No reply."
    elif [ "$wk" -ge $((ceil-PRESSURE_MARGIN)) ] && [ "$ws" -lt 1 ]; then wstage[$b]=1; tell "ORDER (budget guard): PRESSURE ON, budget $b weekly ${wk}% >= ceiling-${PRESSURE_MARGIN} (${ceil}%). No night launches on tiers ${tiers}; by day launch only priority work. No reply."
    elif [ "$wk" -lt $((ceil-PRESSURE_MARGIN)) ] && [ "$ws" -gt 0 ]; then wstage[$b]=0; tell "FYI (budget guard): PRESSURE OFF, budget $b weekly ${wk}% (ceiling ${ceil}%). Normal caps apply."
    fi
    [ "$rs" -le "${handled[$b]:-0}" ] && continue    # feed not updated since the last reset
    if [ "$ss" -ge "$WINDOW_STOP_PCT" ] && [ "$st" -lt 2 ]; then
      stage[$b]=2; reset_at[$b]=$rs
      tell "ORDER (window guard): budget $b window at ${ss}%, limit imminent (reset $(date -d @"$rs" +%H:%M)). Tell your lanes on tiers $(budget_tiers "$b")to commit and push WIP now and update their Resume note. Launch nothing; work resumes after the reset. No reply."
    elif [ "$ss" -ge "$WINDOW_WARN_PCT" ] && [ "$st" -lt 1 ]; then
      stage[$b]=1; reset_at[$b]=$rs
      tell "ORDER (window guard): budget $b window at ${ss}% (reset $(date -d @"$rs" +%H:%M)). Start nothing new on tiers $(budget_tiers "$b"); running lanes continue. No reply."
    fi
  done
  sleep "$POLL_SECS"
done
