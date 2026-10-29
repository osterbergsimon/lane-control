#!/usr/bin/env bash
# Exits (waking the machine coordinator) on low memory, high swap or a metered budget at pressure.
# Run it as a background watch of the machine coordinator. Budgets without a usage feed are skipped.
here=$(dirname "$0"); . "$here/lib.sh"
low=0
while true; do
  avail=$(mem_avail_gb); swap=$(swap_used_gb)
  if [ "$avail" -lt "$MEM_LOW_GB" ]; then low=$((low+1)); else low=0; fi
  [ $low -ge 2 ] && { echo "MEM LOW: ${avail}G available, swap used ${swap}G, load $(cut -d' ' -f1-3 /proc/loadavg)"; exit 0; }
  [ "$swap" -ge "$SWAP_HIGH_GB" ] && { echo "SWAP HIGH: ${swap}G used, ${avail}G available"; exit 0; }
  for b in $(metered); do
    read -r wk ss < <(budget_usage "$b"); ceil=$(budget_ceiling "$b")
    [ "${wk:-0}" -ge $((ceil-PRESSURE_MARGIN)) ] && { echo "BUDGET $b WEEKLY ${wk}% >= ceiling-${PRESSURE_MARGIN} ($ceil); tiers: $(budget_tiers "$b")"; exit 0; }
    [ "${ss:-0}" -ge "$WINDOW_WATCH_PCT" ] && { echo "BUDGET $b WINDOW ${ss}%; tiers: $(budget_tiers "$b")"; exit 0; }
  done
  sleep "$POLL_SECS"
done
