# Machine limits for scripts/lane-heavy and scripts/lane-room. Optional: copy to $LANE_LIMITS_FILE (default:
# ~/.config/lane-control/limits.sh), outside any repo, and keep only what you want to change. Plain bash, one per
# machine. With no file, the defaults below are worked out from this machine's cores and memory. Whole numbers only;
# $LANE_CORES and $LANE_MEM_GB are available for values that follow the machine.

# --- Heavy work (lane-heavy) ---
# LANE_HEAVY_SLOTS=2         # builds or full test suites running at once on this machine.
#                            # Default: cores/6, or memory GB/16 if that is lower; at least 1.
# LANE_HEAVY_MEM_GB=24       # memory limit of one job; 0 = no limit. Default: 3/4 of memory shared between the slots.
#                            # The job may not swap: past the limit it is killed and reported, not left to thrash.
# LANE_HEAVY_POLL_SECS=2     # how often a waiting job looks for a free slot.

# --- Opening another lane (lane-room). Looser than the gate: idle agents cost little. ---
# LANE_ROOM_MIN_MEM_GB=8     # hold a new lane under this much available memory. Default: memory GB/8, at least 2.
# LANE_ROOM_MAX_LOAD=18      # hold a new lane over this 5-minute load. Default: 1.5 times the cores.
# LANE_ROOM_MAX_AGENTS=12    # hold a new lane at this many lane agents. Default: cores, or memory GB/2 if that is
#                            # lower; at least 2.
