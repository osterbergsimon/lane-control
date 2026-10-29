#!/usr/bin/env bash
# Exits (waking the machine coordinator) on low memory, high swap, or a metered budget reaching the next
# weekly step (half the ceiling, then ceiling-15/-10/-7/-4). Re-arm it after handling.
# Run it as a background watch of the machine coordinator. Budgets without a usage feed are skipped.
here=$(dirname "$0"); . "$here/lib.sh"
mkdir -p "$NIGHT_START_DIR"; low=0
while true; do
  avail=$(mem_avail_gb); swap=$(swap_used_gb)
  if [ "$avail" -lt "$MEM_LOW_GB" ]; then low=$((low+1)); else low=0; fi
  [ $low -ge 2 ] && { echo "MEM LOW: ${avail}G available, swap used ${swap}G, load $(cut -d' ' -f1-3 /proc/loadavg)"; exit 0; }
  [ "$swap" -ge "$SWAP_HIGH_GB" ] && { echo "SWAP HIGH: ${swap}G used, ${avail}G available"; exit 0; }
  for b in $(metered); do
    read -r wk ss < <(budget_usage "$b"); ceil=$(budget_ceiling "$b")
    # wake once per step: half the ceiling, then ceiling-15/-10/-7/-4; the state file remembers the last step
    sf="$NIGHT_START_DIR/wake-$b"; last=$(cat "$sf" 2>/dev/null || echo 0)
    [ "${wk:-0}" -lt $((ceil/2)) ] && [ "$last" != 0 ] && { echo 0 > "$sf"; last=0; }   # weekly reset
    for step in $((ceil-4)) $((ceil-7)) $((ceil-10)) $((ceil-PRESSURE_MARGIN)) $((ceil/2)); do
      if [ "${wk:-0}" -ge "$step" ]; then
        [ "$step" -gt "$last" ] && { echo "$step" > "$sf"; echo "BUDGET $b WEEKLY ${wk}% >= step ${step}% (ceiling ${ceil}%); tiers: $(budget_tiers "$b")"; exit 0; }
        break
      fi
    done
    [ "${ss:-0}" -ge "$WINDOW_WATCH_PCT" ] && { echo "BUDGET $b WINDOW ${ss}%; tiers: $(budget_tiers "$b")"; exit 0; }
  done
  sleep "$POLL_SECS"
done
