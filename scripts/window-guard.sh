#!/usr/bin/env bash
# Window guard: watches each metered budget's short window (e.g. a 5-hour limit) and sends orders to the
# project coordinators by `cotal send`, one space per project (COORD_SPACE in the config). Never exits on its
# own and spends no tokens. Runs as a systemd user service: scripts/window-guard-install.
# Every order starts with "machine coordinator: ", which coordinators count like the owner's own words.
#   >= WINDOW_WARN_PCT: start nothing new on that budget.
#   >= WINDOW_STOP_PCT: limit imminent; have lanes commit and push WIP and update Resume notes.
#   after the reset:    rebuild state from Cotal, Orca and bd, and resume stalled lanes.
# Weekly ceiling steps, once each per climb: ceiling-15 pressure on (notice only), -STEP_NO_NEW no new
# work on the budget, -STEP_WIP_STOP lanes push WIP and stop, -STEP_STOP_ALL everything on the budget stops; pressure off when back under -17. Per project: COORD_CEILING_ADD[p] raises p's ceiling.
# Reset times vary: read from BUDGET_RESET (a command printing the window's reset as epoch seconds).
# State survives restarts: wstage/stage/reset_at/handled are kept in
# ${GUARD_STATE_FILE:-${XDG_STATE_HOME:-$HOME/.local/state}/lane-control/window-guard.state} (persistent
# dir on purpose: /tmp, where NIGHT_START_DIR lives, is wiped on reboot). Rewritten whenever a value
# changes; loaded at start so a restart does not re-send orders already sent. A file older than 7 days is
# ignored, and on the first poll of a budget its weekly steps are cleared if weekly usage is below
# ceiling/2 (the weekly reset), so a stale file from a previous week never suppresses orders.
# Memory: below MEM_LOW_GB two polls in a row, or swap at SWAP_HIGH_GB, opens a low-memory episode: one notice to
# the machine coordinator. The episode ends after two good polls in a row.
# A send that fails waits in <state file>.pending and is retried each poll, in order, for GUARD_RETRY_SECS (1 hour);
# then the machine coordinator is told if its space answers, and the journal has it either way. GUARD_POLLS=<n> stops after n polls (tests).
here=$(dirname "$0"); . "$here/lib.sh"
declare -A wstage stage reset_at handled checked mem   # handled: last reset acted on (a stale feed repeats it)
STATE_FILE="${GUARD_STATE_FILE:-${XDG_STATE_HOME:-$HOME/.local/state}/lane-control/window-guard.state}"
PENDING_FILE="$STATE_FILE.pending"; RETRY_SECS="${GUARD_RETRY_SECS:-3600}"
MACHINE_SPACE=machine; MACHINE_AGENT=machine-coordinator; ORDER_PREFIX="machine coordinator: "
mkdir -p "$(dirname "$STATE_FILE")"; last_state=""
load_state() {
  [ -r "$STATE_FILE" ] || return 0
  local kind k v sv=0
  while read -r kind k v; do [ "$kind" = saved ] && sv=$k; done < "$STATE_FILE"
  [ $(( $(date +%s) - sv )) -lt 604800 ] || return 0
  while read -r kind k v; do
    case "$kind" in
      wstage) wstage[$k]=$v ;; stage) stage[$k]=$v ;; reset_at) reset_at[$k]=$v ;; handled) handled[$k]=$v ;; mem) mem[$k]=$v ;;
    esac
  done < "$STATE_FILE"
}
save_state() {  # rewrite the file only when a value changed (atomic rename)
  local out="" k
  for k in "${!wstage[@]}"; do out+="wstage $k ${wstage[$k]}"$'\n'; done
  for k in "${!stage[@]}"; do out+="stage $k ${stage[$k]}"$'\n'; done
  for k in "${!reset_at[@]}"; do out+="reset_at $k ${reset_at[$k]}"$'\n'; done
  for k in "${!handled[@]}"; do out+="handled $k ${handled[$k]}"$'\n'; done
  for k in "${!mem[@]}"; do out+="mem $k ${mem[$k]}"$'\n'; done
  out=$(printf '%s' "$out" | sort)
  [ "$out" = "$last_state" ] && return 0
  last_state=$out
  printf '%s\nsaved %s 0\n' "$out" "$(date +%s)" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"
}
load_state
[ "${#COORD_SPACE[@]}" -gt 0 ] || echo "window-guard: COORD_SPACE is empty in $CONF: no coordinator gets orders" >&2
send() {  # send <space> <agent> <message>: true once the broker stored it
  timeout "${GUARD_SEND_TIMEOUT:-30}" cotal send -s "$1" -t "$2" "$3" >/dev/null 2>&1
}
deliver() {  # deliver <space> <agent> <message>: a space with messages waiting keeps them in order
  if ! grep -qF -- "$(printf '\t%s\t' "$1")" "$PENDING_FILE" 2>/dev/null && send "$1" "$2" "$3"; then return 0; fi
  printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$1" "$2" "$3" >> "$PENDING_FILE"
  echo "$(date +%H:%M) send to $1 failed, will retry"
}
flush_pending() {  # each waiting message, oldest first; one failure ends that space's turn this poll
  [ -s "$PENDING_FILE" ] || return 0
  local when sp ag msg now keep="" failed=" "
  now=$(date +%s)
  while IFS=$'\t' read -r when sp ag msg; do
    if [ $(( now - when )) -ge "$RETRY_SECS" ]; then
      echo "$(date +%H:%M) gave up sending to $sp: $msg"
      [ "$sp" = "$MACHINE_SPACE" ] || send "$MACHINE_SPACE" "$MACHINE_AGENT" "GUARD: could not reach space $sp for ${RETRY_SECS}s, this was not delivered: $msg" \
        || echo "$(date +%H:%M) the machine coordinator could not be told either"
      continue
    fi
    if [[ "$failed" != *" $sp "* ]] && send "$sp" "$ag" "$msg"; then continue; fi
    failed+="$sp "
    keep+="$when"$'\t'"$sp"$'\t'"$ag"$'\t'"$msg"$'\n'
  done < "$PENDING_FILE"
  printf '%s' "$keep" > "$PENDING_FILE.tmp" && mv "$PENDING_FILE.tmp" "$PENDING_FILE"
}
tell() {  # tell <message>: every coordinator
  local p
  for p in "${!COORD_SPACE[@]}"; do deliver "${COORD_SPACE[$p]}" coordinator "$ORDER_PREFIX$1"; done
  echo "$(date +%H:%M) $1"
}
tell_one() {  # tell_one <project> <message>
  deliver "${COORD_SPACE[$1]}" coordinator "$ORDER_PREFIX$2"
  echo "$(date +%H:%M) [$1] $2"
}
check_memory() {  # one notice to the machine coordinator per low-memory episode
  local avail swap healthy=1
  avail=$(mem_avail_gb); swap=$(swap_used_gb)
  if [ "$avail" -lt "$MEM_LOW_GB" ]; then mem[low]=$(( ${mem[low]:-0} < 2 ? ${mem[low]:-0} + 1 : 2 )); healthy=0; else mem[low]=0; fi   # capped: the state file changes only when a value does
  [ "$swap" -ge "$SWAP_HIGH_GB" ] && healthy=0
  if [ "$healthy" = 1 ]; then mem[good]=$(( ${mem[good]:-0} < 2 ? ${mem[good]:-0} + 1 : 2 )); else mem[good]=0; fi
  if [ "${mem[episode]:-0}" = 1 ]; then
    [ "${mem[good]}" -ge 2 ] && mem[episode]=0
  elif [ "${mem[low]}" -ge 2 ] || [ "$swap" -ge "$SWAP_HIGH_GB" ]; then
    mem[episode]=1
    local note="LOW MEMORY (window guard): ${avail}G available, swap used ${swap}G, load $(cut -d' ' -f1-3 /proc/loadavg). One notice per episode."
    deliver "$MACHINE_SPACE" "$MACHINE_AGENT" "$note"
    echo "$(date +%H:%M) $note"
  fi
}
polls=0
while true; do
  flush_pending
  check_memory
  for b in $(metered); do
    read -r wk ss < <(budget_usage "$b")
    rs=$(bash -c "${BUDGET_RESET[$b]:-}" 2>/dev/null); rs=${rs:-0}
    now=$(date +%s); st=${stage[$b]:-0}
    if [ -z "${checked[$b]:-}" ] && [ "$ss" -lt 100 -o "$wk" -lt 100 ]; then   # first good read since start
      checked[$b]=1
      [ $(( wk * 2 )) -lt "$(budget_ceiling "$b")" ] && for k in "${!wstage[@]}"; do [ "${k%%:*}" = "$b" ] && wstage[$k]=0; done
    fi
    if [ "$st" -gt 0 ] && [ "${reset_at[$b]:-0}" -gt 0 ] && [ "$now" -gt $(( ${reset_at[$b]} + 60 )) ]; then
      tell "FYI (window guard): budget $b window has reset. Rebuild from cotal_inbox, cotal who, orca worktree list and bd (a report that arrived at the limit may be unprocessed), resume stalled lanes, launch per your caps."
      handled[$b]=${reset_at[$b]}; stage[$b]=0; reset_at[$b]=0; continue
    fi
    [ "$ss" -ge 100 ] && [ "$rs" = 0 ] && continue   # failed feed reads 100: never act on it
    tiers=$(budget_tiers "$b")
    for p in "${!COORD_SPACE[@]}"; do   # per project: COORD_CEILING_ADD raises one project's ceiling
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
  polls=$(( polls + 1 ))
  [ -n "${GUARD_POLLS:-}" ] && [ "$polls" -ge "$GUARD_POLLS" ] && break
  sleep "$POLL_SECS"
done
