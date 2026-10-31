#!/usr/bin/env bash
# Window guard: watches each metered budget's short window (e.g. a 5-hour limit) and messages the
# project coordinators. Never exits on its own and spends no tokens. Run it in the background.
#   >= WINDOW_WARN_PCT: start nothing new on that budget.
#   >= WINDOW_STOP_PCT: limit imminent; have lanes commit and push WIP and update Resume notes.
#   after the reset:    rebuild from rove api context and resume stalled lanes.
# Weekly ceiling steps, once each per climb: ceiling-15 pressure on (notice only), -STEP_NO_NEW no new
# work on the budget, -STEP_WIP_STOP lanes push WIP and stop, -STEP_STOP_ALL everything on the budget stops; pressure off when back under -17. Per project: COORD_CEILING_ADD[p] raises p's ceiling.
# Reset times vary: read from BUDGET_RESET (a command printing the window's reset as epoch seconds).
# State survives restarts: wstage/stage/reset_at/handled are kept in
# ${GUARD_STATE_FILE:-${XDG_STATE_HOME:-$HOME/.local/state}/lane-control/window-guard.state} (persistent
# dir on purpose: /tmp, where NIGHT_START_DIR lives, is wiped on reboot). Rewritten whenever a value
# changes; loaded at start so a restart does not re-send orders already sent. A file older than 7 days is
# ignored, and on the first poll of a budget its weekly steps are cleared if weekly usage is below
# ceiling/2 (the weekly reset), so a stale file from a previous week never suppresses orders.
here=$(dirname "$0"); . "$here/lib.sh"
declare -A wstage stage reset_at handled checked   # handled: last reset acted on (a stale feed repeats it)
STATE_FILE="${GUARD_STATE_FILE:-${XDG_STATE_HOME:-$HOME/.local/state}/lane-control/window-guard.state}"
mkdir -p "$(dirname "$STATE_FILE")"; last_state=""
load_state() {
  [ -r "$STATE_FILE" ] || return 0
  local kind k v sv=0
  while read -r kind k v; do [ "$kind" = saved ] && sv=$k; done < "$STATE_FILE"
  [ $(( $(date +%s) - sv )) -lt 604800 ] || return 0
  while read -r kind k v; do
    case "$kind" in
      wstage) wstage[$k]=$v ;; stage) stage[$k]=$v ;; reset_at) reset_at[$k]=$v ;; handled) handled[$k]=$v ;;
    esac
  done < "$STATE_FILE"
}
save_state() {  # rewrite the file only when a value changed (atomic rename)
  local out="" k
  for k in "${!wstage[@]}"; do out+="wstage $k ${wstage[$k]}"$'\n'; done
  for k in "${!stage[@]}"; do out+="stage $k ${stage[$k]}"$'\n'; done
  for k in "${!reset_at[@]}"; do out+="reset_at $k ${reset_at[$k]}"$'\n'; done
  for k in "${!handled[@]}"; do out+="handled $k ${handled[$k]}"$'\n'; done
  out=$(printf '%s' "$out" | sort)
  [ "$out" = "$last_state" ] && return 0
  last_state=$out
  printf '%s\nsaved %s 0\n' "$out" "$(date +%s)" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"
}
load_state
tell() {  # tell <message>: every coordinator, plain send (a busy session queues it)
  local p
  for p in "${!COORD_TASK[@]}"; do
    rove api send --task-id "${COORD_TASK[$p]}" --tab "$COORD_TAB" --prompt "$1" >/dev/null 2>&1
  done
  echo "$(date +%H:%M) $1"
}
tell_one() {  # tell_one <project> <message>
  rove api send --task-id "${COORD_TASK[$1]}" --tab "$COORD_TAB" --prompt "$2" >/dev/null 2>&1
  echo "$(date +%H:%M) [$1] $2"
}
while true; do
  for b in $(metered); do
    read -r wk ss < <(budget_usage "$b")
    rs=$(bash -c "${BUDGET_RESET[$b]:-}" 2>/dev/null); rs=${rs:-0}
    now=$(date +%s); st=${stage[$b]:-0}
    if [ -z "${checked[$b]:-}" ] && [ "$ss" -lt 100 -o "$wk" -lt 100 ]; then   # first good read since start
      checked[$b]=1
      [ $(( wk * 2 )) -lt "$(budget_ceiling "$b")" ] && for k in "${!wstage[@]}"; do [ "${k%%:*}" = "$b" ] && wstage[$k]=0; done
    fi
    if [ "$st" -gt 0 ] && [ "${reset_at[$b]:-0}" -gt 0 ] && [ "$now" -gt $(( ${reset_at[$b]} + 60 )) ]; then
      tell "FYI (window guard): budget $b window has reset. Rebuild from rove api context (a report that arrived at the limit may be unprocessed), resume stalled lanes, launch per your caps."
      handled[$b]=${reset_at[$b]}; stage[$b]=0; reset_at[$b]=0; continue
    fi
    [ "$ss" -ge 100 ] && [ "$rs" = 0 ] && continue   # failed feed reads 100: never act on it
    tiers=$(budget_tiers "$b")
    for p in "${!COORD_TASK[@]}"; do   # per project: COORD_CEILING_ADD raises one project's ceiling
      ceil=$(( $(budget_ceiling "$b") + ${COORD_CEILING_ADD[$p]:-0} )); k="$b:$p"; ws=${wstage[$k]:-0}
      if   [ "$wk" -ge $((ceil-STEP_STOP_ALL)) ]  && [ "$ws" -lt 4 ]; then wstage[$k]=4; tell_one "$p" "ORDER (budget guard): budget $b weekly ${wk}% >= ceiling-${STEP_STOP_ALL} (${ceil}%). Stop all work on tiers ${tiers}now: lanes commit and push WIP and stop; coordinators too. Your last act before the limit: write your handover summary (${HANDOVER_HINT:-done, open, decisions for the owner, how to resume; detailed enough for a new session}). No reply."
      elif [ "$wk" -ge $((ceil-STEP_WIP_STOP)) ]  && [ "$ws" -lt 3 ]; then wstage[$k]=3; tell_one "$p" "ORDER (budget guard): budget $b weekly ${wk}% >= ceiling-${STEP_WIP_STOP} (${ceil}%). Lanes on tiers ${tiers}commit and push WIP and stop; coordinators only review and merge. Your last act before the limit: write your handover summary (${HANDOVER_HINT:-done, open, decisions for the owner, how to resume; detailed enough for a new session}). No reply."
      elif [ "$wk" -ge $((ceil-STEP_NO_NEW)) ] && [ "$ws" -lt 2 ]; then wstage[$k]=2; tell_one "$p" "ORDER (budget guard): budget $b weekly ${wk}% >= ceiling-${STEP_NO_NEW} (${ceil}%). No new work on tiers ${tiers}; running lanes finish. No reply."
      elif [ "$wk" -ge $((ceil-PRESSURE_MARGIN)) ] && [ "$ws" -lt 1 ]; then wstage[$k]=1; tell_one "$p" "FYI (budget guard): PRESSURE ON, budget $b weekly ${wk}% >= ceiling-${PRESSURE_MARGIN} (${ceil}%); no new work from $((ceil-STEP_NO_NEW))%. Caps unchanged until then. No reply."
      elif [ "$wk" -lt $((ceil-PRESSURE_MARGIN-2)) ] && [ "$ws" -gt 0 ]; then wstage[$k]=0; tell_one "$p" "FYI (budget guard): PRESSURE OFF, budget $b weekly ${wk}% (ceiling ${ceil}%). Normal caps apply."
      fi
    done
    [ "$rs" -le "${handled[$b]:-0}" ] && continue    # feed not updated since the last reset
    if [ "$ss" -ge "$WINDOW_STOP_PCT" ] && [ "$st" -lt 2 ]; then
      stage[$b]=2; reset_at[$b]=$rs
      tell "ORDER (window guard): budget $b window at ${ss}%, limit imminent (reset $(date -d @"$rs" +%H:%M)). Tell your lanes on tiers $(budget_tiers "$b")to commit and push WIP now and update their Resume note. Launch nothing; work resumes after the reset. No reply."
    elif [ "$ss" -ge "$WINDOW_WARN_PCT" ] && [ "$st" -lt 1 ]; then
      stage[$b]=1; reset_at[$b]=$rs
      tell "ORDER (window guard): budget $b window at ${ss}% (reset $(date -d @"$rs" +%H:%M)). Start nothing new on tiers $(budget_tiers "$b"); running lanes continue. No reply."
    fi
  done
  save_state
  sleep "$POLL_SECS"
done
