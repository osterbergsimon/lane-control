# Shared by the watch scripts: loads the config, defines budget helpers.
CONF="${PLAYBOOK_CONFIG:-$HOME/.config/rove/playbook.conf.sh}"
declare -A COORD_CEILING_ADD=( )   # default: no per-project raise (config may override)
[ -r "$CONF" ] || { echo "no config: $CONF (copy config.example.sh)" >&2; exit 2; }
# shellcheck disable=SC1090
. "$CONF"

# metered budgets: those with a usage feed
metered() { local b; for b in "${!BUDGET_USAGE[@]}"; do [ -n "${BUDGET_USAGE[$b]}" ] && echo "$b"; done; }
# budget_ceiling <budget>: percent
budget_ceiling() {
  local c=${BUDGET_CEILING[$1]:-}
  case "$c" in
    file:*) jq -r .ceiling "${c#file:}" 2>/dev/null || echo "$DEFAULT_CEILING" ;;
    '')     echo "$DEFAULT_CEILING" ;;
    *)      echo "$c" ;;
  esac
}
# budget_usage <budget>: "<weekly%> <window%>"; a failed feed reads "100 100" (fail safe: blocks night launches, wakes the watch)
budget_usage() { local o; o=$(bash -c "${BUDGET_USAGE[$1]}" 2>/dev/null) && [ -n "$o" ] && echo "$o" || echo "100 100"; }
# budget_tiers <budget>: tiers drawing on it
budget_tiers() { local t; for t in $TIERS; do [ "${TIER_BUDGET[$t]:-}" = "$1" ] && printf '%s ' "$t"; done; }
mem_avail_gb() { awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo; }
swap_used_gb() { awk '/SwapTotal/{t=$2}/SwapFree/{f=$2}END{print int((t-f)/1048576)}' /proc/meminfo; }
