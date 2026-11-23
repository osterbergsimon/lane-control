# Shared by the watch scripts: loads the config, defines budget helpers.

# lc_root: the lane-control checkout of the running script, found by its marker file (symlinks resolved).
lc_root() {
  local d; d=$(dirname "$(readlink -f "$0")")
  while :; do
    [ -f "$d/.lane-control-root" ] && { echo "$d"; return 0; }
    [ "$d" = / ] && { echo "lane-control root not found" >&2; return 1; }
    d=$(dirname "$d")
  done
}
CONF="${PLAYBOOK_CONFIG:-$HOME/.config/rove/playbook.conf.sh}"
declare -A COORD_CEILING_ADD=( )   # default: no per-project raise (config may override)
declare -A COORD_REPO=( )
declare -A COORD_SPACE=( )         # project -> Cotal space of its coordinator: the window guard's only target list
declare -A COORD_SLOTS=( )         # parallel working lanes a project should keep busy (default 1)
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
# project_ceiling <project>: that project's own weekly ceiling (percent) from the ceiling file's .projects.<project>.ceiling,
# or nothing when there is none. The file is LANE_CEILING_FILE (default ~/.config/claude-usage-ceiling.json), the one the
# machine coordinator records raises in. A file that is not valid JSON gives one stderr line and nothing.
project_ceiling() {
  local f=${LANE_CEILING_FILE:-$HOME/.config/claude-usage-ceiling.json} v
  [ -r "$f" ] || return 0
  v=$(jq -r --arg p "$1" '.projects[$p].ceiling // empty' "$f" 2>/dev/null) || { echo "project ceiling: $f is not valid JSON; using the budget's ceiling" >&2; return 0; }
  [[ "$v" =~ ^[0-9]+$ ]] && echo "$v"
  return 0
}
# budget_usage <budget>: "<weekly%> <window%>"; a failed feed reads "100 100" (fail safe: blocks night launches)
budget_usage() { local o; o=$(bash -c "${BUDGET_USAGE[$1]}" 2>/dev/null) && [ -n "$o" ] && echo "$o" || echo "100 100"; }
# budget_tiers <budget>: tiers drawing on it
budget_tiers() { local t; for t in $TIERS; do [ "${TIER_BUDGET[$t]:-}" = "$1" ] && printf '%s ' "$t"; done; }
MEMINFO="${GUARD_MEMINFO:-/proc/meminfo}"
mem_avail_gb() { awk '/MemAvailable/{print int($2/1048576)}' "$MEMINFO"; }
swap_used_gb() { awk '/SwapTotal/{t=$2}/SwapFree/{f=$2}END{print int((t-f)/1048576)}' "$MEMINFO"; }
