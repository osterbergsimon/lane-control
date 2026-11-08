# Sourced by lane-heavy and lane-room: works out this machine's limits. Keep it next to them (they find it through
# their own resolved path); a symlink to either tool works, a copy of one alone does not.
#
# Limits: defaults from cores and memory, then the owner's per-machine file (limits.example.sh documents it):
#   LANE_LIMITS_FILE   the file (default ~/.config/lane-control/limits.sh; absent is fine)
# Not settings, for tests only:
#   LANE_PROC_DIR      /proc to read (default /proc)   LANE_STATE_DIR   where slots live (default below)

LANE_PROC_DIR=${LANE_PROC_DIR:-/proc}
LANE_LIMITS_FILE=${LANE_LIMITS_FILE:-$HOME/.config/lane-control/limits.sh}

# `--all`: plain nproc shrinks with OMP_NUM_THREADS and affinity, so two lanes could compute different limits.
LANE_CORES=$(nproc --all 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)
LANE_MEM_GB=$(awk '/^MemTotal:/ { print int($2 / 1048576) }' "$LANE_PROC_DIR/meminfo" 2>/dev/null)
LANE_MEM_GB=${LANE_MEM_GB:-0}
[ "$LANE_CORES" -gt 0 ] 2>/dev/null || LANE_CORES=1

lane_at_least() { if [ "$1" -gt "$2" ]; then echo "$1"; else echo "$2"; fi; }
lane_at_most() { if [ "$1" -lt "$2" ]; then echo "$1"; else echo "$2"; fi; }

# A heavy job takes every core it can get (load 23 on 12 cores was four lanes building at once).
lane_default_slots=$(lane_at_least "$(lane_at_most $((LANE_CORES / 6)) $((LANE_MEM_GB / 16)))" 1)
# Slots may not, between them, take the quarter of memory left for the owner and the agents.
lane_default_job_gb=$(lane_at_least $((LANE_MEM_GB * 3 / 4 / lane_default_slots)) 1)
lane_default_room_mem=$(lane_at_least $((LANE_MEM_GB / 8)) 2)
lane_default_room_load=$((LANE_CORES * 3 / 2))
lane_default_room_agents=$(lane_at_least "$(lane_at_most "$LANE_CORES" $((LANE_MEM_GB / 2)))" 2)

LANE_HEAVY_SLOTS=$lane_default_slots
LANE_HEAVY_MEM_GB=$lane_default_job_gb
LANE_HEAVY_POLL_SECS=2
LANE_ROOM_MIN_MEM_GB=$lane_default_room_mem
LANE_ROOM_MAX_LOAD=$lane_default_room_load
LANE_ROOM_MAX_AGENTS=$lane_default_room_agents

# Slot files live on the per-user tmpfs. Deliberately not $XDG_RUNTIME_DIR or $TMPDIR: they differ between a lane's
# shells, and two lanes that resolve different directories would not share a limit.
lane_uid=$(id -u)
if [ -z "${LANE_STATE_DIR:-}" ]; then
  if [ -d "/run/user/$lane_uid" ] && [ -w "/run/user/$lane_uid" ]; then
    LANE_STATE_DIR=/run/user/$lane_uid/lane-control
  else
    LANE_STATE_DIR=/tmp/lane-control-$lane_uid
  fi
fi

if [ -r "$LANE_LIMITS_FILE" ]; then
  # shellcheck disable=SC1090
  . "$LANE_LIMITS_FILE"
fi

# lane_setting <name> <default> [min]: a bad value in the file must not break the gate, so it falls back and says so.
lane_setting() {
  local value=${!1:-}
  case $value in
    '' | *[!0-9]*) ;;
    *) [ "$value" -ge "${3:-0}" ] && return 0 ;;
  esac
  echo "lane-limits: ignoring $1='$value' in $LANE_LIMITS_FILE, using $2" >&2
  printf -v "$1" %s "$2"
}
lane_setting LANE_HEAVY_SLOTS "$lane_default_slots" 1
lane_setting LANE_HEAVY_MEM_GB "$lane_default_job_gb"
lane_setting LANE_ROOM_MIN_MEM_GB "$lane_default_room_mem"
lane_setting LANE_ROOM_MAX_LOAD "$lane_default_room_load"
lane_setting LANE_ROOM_MAX_AGENTS "$lane_default_room_agents"
case $LANE_HEAVY_POLL_SECS in
  '' | *[!0-9.]* | *.*.* | .) LANE_HEAVY_POLL_SECS=2 ;;
esac
