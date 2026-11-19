# Sourced by lane-heavy and lane-room: works out this machine's limits. Keep it next to them (they find it through
# their own resolved path); a symlink to either tool works, a copy of one alone does not.
#
# Limits: presence, then the owner's per-machine file (limits.example.sh documents it), then defaults from cores,
# memory and presence for whatever the file left unset:
#   LANE_LIMITS_FILE   the file (default ~/.config/lane-control/limits.sh; absent is fine)
#   LANE_AWAY_FILE     the owner is away while it is younger than 14 h (default ~/.config/lane-control/away)
# Presence, for the file to branch on: LANE_PRESENCE is day or away (night 23-07, or the away file), and
# LANE_PRESENCE_WHY says which. A job keeps the limits it started with.
# Not settings; for tests:
#   LANE_PROC_DIR      /proc to read (default /proc)   LANE_STATE_DIR   where slots live (default below)
#   LANE_HOUR          the hour of the day (default: now)

LANE_PROC_DIR=${LANE_PROC_DIR:-/proc}
LANE_LIMITS_FILE=${LANE_LIMITS_FILE:-$HOME/.config/lane-control/limits.sh}
LANE_AWAY_FILE=${LANE_AWAY_FILE:-$HOME/.config/lane-control/away}

# `--all`: plain nproc shrinks with OMP_NUM_THREADS and affinity, so two lanes could compute different limits.
LANE_CORES=$(nproc --all 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)
LANE_MEM_GB=$(awk '/^MemTotal:/ { print int($2 / 1048576) }' "$LANE_PROC_DIR/meminfo" 2>/dev/null)
LANE_MEM_GB=${LANE_MEM_GB:-0}
[ "$LANE_CORES" -gt 0 ] 2>/dev/null || LANE_CORES=1

lane_at_least() { if [ "$1" -gt "$2" ]; then echo "$1"; else echo "$2"; fi; }
lane_at_most() { if [ "$1" -lt "$2" ]; then echo "$1"; else echo "$2"; fi; }

# Day, or night-or-away: the owner is not using the machine, so heavy work and CI may take more of it.
lane_hour=${LANE_HOUR:-$(date +%H)}
lane_hour=$((10#${lane_hour:-12}))
LANE_PRESENCE=day LANE_PRESENCE_WHY=day
if [ -n "$(find "$LANE_AWAY_FILE" -maxdepth 0 -mmin -840 2>/dev/null)" ]; then
  LANE_PRESENCE=away LANE_PRESENCE_WHY="owner away"
elif [ "$lane_hour" -ge 23 ] || [ "$lane_hour" -lt 7 ]; then
  LANE_PRESENCE=away LANE_PRESENCE_WHY=night
fi

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

# Only the file sets these; a value from the environment would differ between a lane's shells.
unset LANE_RESERVE_GB LANE_HEAVY_SLOTS LANE_HEAVY_MEM_GB LANE_HEAVY_POLL_SECS LANE_CI_SLOTS LANE_CI_MEM_GB \
  LANE_CI_CPU_PCT LANE_CI_SLICE LANE_ROOM_MIN_MEM_GB LANE_ROOM_MAX_LOAD LANE_ROOM_MAX_AGENTS LANE_ROOM_SAMPLES \
  LANE_ROOM_SAMPLE_SECS

# The tools run with set -u; an unset variable in the file would end them, so it counts as an empty (bad) value.
set +u
if [ -r "$LANE_LIMITS_FILE" ]; then
  # shellcheck disable=SC1090
  . "$LANE_LIMITS_FILE"
fi
set -u

# lane_setting <name> <default> [min]: unset takes the default; a bad value in the file must not break the gate, so
# it falls back and says so.
lane_setting() {
  [ -n "${!1+set}" ] || { printf -v "$1" %s "$2"; return 0; }
  local value=${!1}
  case $value in
    '' | *[!0-9]*) ;;
    *) if [ "$value" -ge "${3:-0}" ] 2>/dev/null; then printf -v "$1" %d "$((10#$value))"; return 0; fi ;;   # 08 is not octal
  esac
  echo "lane-limits: ignoring $1='$value' in $LANE_LIMITS_FILE, using $2" >&2
  printf -v "$1" %s "$2"
}
lane_seconds() {   # lane_seconds <name> <default>: decimals allowed
  case ${!1-$2} in
    '' | *[!0-9.]* | *.*.* | .) printf -v "$1" %s "$2" ;;
    *) printf -v "$1" %s "${!1-$2}" ;;
  esac
}

# Memory: a reserve for the desktop and the agent sessions, two thirds of the rest for lane slots and one third for
# the CI slice. At night or away the desktop is idle, so CI takes a fifth of the reserve on top; by day it gives that
# fifth back. On 46G: day 2 x 12G + CI 10G, night or away 3 x 8G + CI 14G (owner 2026-11-19).
lane_setting LANE_RESERVE_GB "$(lane_at_most 10 $((LANE_MEM_GB / 4)))"
lane_rest=$(lane_at_least $((LANE_MEM_GB - LANE_RESERVE_GB)) 1)
lane_lanes_gb=$((lane_rest * 2 / 3))
lane_ci_gb=$((lane_rest - lane_lanes_gb - LANE_RESERVE_GB / 5))
[ "$LANE_PRESENCE" = day ] || lane_ci_gb=$((lane_rest - lane_lanes_gb + LANE_RESERVE_GB / 5))

# A heavy job takes every core it can get (load 23 on 12 cores was four lanes building at once). One more at night.
lane_default_slots=$(lane_at_least "$(lane_at_most $((LANE_CORES / 6)) $((LANE_MEM_GB / 16)))" 1)
[ "$LANE_PRESENCE" = day ] || lane_default_slots=$((lane_default_slots + 1))
lane_setting LANE_HEAVY_SLOTS "$lane_default_slots" 1
lane_setting LANE_HEAVY_MEM_GB "$(lane_at_least $((lane_lanes_gb / LANE_HEAVY_SLOTS)) 1)"
lane_seconds LANE_HEAVY_POLL_SECS 2
lane_setting LANE_CI_SLOTS 2 1
lane_setting LANE_CI_MEM_GB "$(lane_at_least "$lane_ci_gb" 1)"
# A third of the cores by day, two thirds at night or away; for whatever sets the CI slice's CPUQuota.
lane_ci_cpu=$((LANE_CORES * 100 / 3))
[ "$LANE_PRESENCE" = day ] || lane_ci_cpu=$((LANE_CORES * 200 / 3))
lane_setting LANE_CI_CPU_PCT "$(lane_at_least "$lane_ci_cpu" 100)" 1
LANE_CI_SLICE=${LANE_CI_SLICE-ci.slice}
# A slice unit name: systemd-run would refuse anything else, and the CI job would then run with no limit at all.
case $LANE_CI_SLICE in
  *[!A-Za-z0-9_.@:-]* | .slice) lane_bad_slice=1 ;;
  ?*.slice) lane_bad_slice= ;;
  *) lane_bad_slice=1 ;;
esac
if [ -n "$lane_bad_slice" ]; then
  echo "lane-limits: ignoring LANE_CI_SLICE='$LANE_CI_SLICE' in $LANE_LIMITS_FILE, using ci.slice" >&2
  LANE_CI_SLICE=ci.slice
fi

lane_setting LANE_ROOM_MIN_MEM_GB "$(lane_at_least $((LANE_MEM_GB / 8)) 2)"
lane_setting LANE_ROOM_MAX_LOAD $((LANE_CORES * 3 / 2))
lane_setting LANE_ROOM_MAX_AGENTS "$(lane_at_least "$(lane_at_most "$LANE_CORES" $((LANE_MEM_GB / 2)))" 2)"
lane_setting LANE_ROOM_SAMPLES 5 1
lane_seconds LANE_ROOM_SAMPLE_SECS 1
