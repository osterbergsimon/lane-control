# Machine limits for scripts/lane-heavy and scripts/lane-room. Optional: copy to $LANE_LIMITS_FILE (default:
# ~/.config/lane-control/limits.sh), outside any repo, and keep only what you want to change. Plain bash, one per
# machine. With no file, the defaults below are worked out from this machine's cores, memory and presence. Whole
# numbers only; $LANE_CORES and $LANE_MEM_GB are available for values that follow the machine.
#
# Presence: $LANE_PRESENCE is `day`, or `away` at night (23-07), while ~/.config/lane-control/away is younger than
# 14 h, or while ~/.config/lane-control/budget-stopped is younger than 1 h (the budget is at its stop step and agents
# sit idle until the reset, so CI gets the machine); $LANE_PRESENCE_WHY says which. $LANE_PRESENCE is for limits only:
# $LANE_OWNER_AWAY is 1 only at night or with the away file, and anything that spends budget reads that one. Defaults
# already follow presence; to set your own per presence, branch on it:
#   if [ "$LANE_PRESENCE" = away ]; then LANE_HEAVY_SLOTS=3; fi
# Settings come only from this file; the same names in the environment are ignored.
#
# Memory: lane slots, the CI slice and LANE_RESERVE_GB fit in memory together (from about 4G of memory up). Slots
# keep the machine out of OOM (mostly CI's); the budget is guarded by the agent caps and budget steps, not by slots.
# On 46G the defaults are day 2 x 12G lanes + CI 10G, night or away 3 x 8G lanes + CI 14G. A running job keeps its
# limit when presence changes; a new lane job also waits until its limit fits beside the running ones in the lane
# pool (slots x limit), so a mix of day and night jobs stays within it. CI waits for a slot only: its slice holds
# the sum. The slot directory's *.gb (a running lane job's limit) and *.admit (admission turns) files may be deleted
# when no job runs. The fit assumes the CI slice's MemoryMax is
# LANE_CI_MEM_GB; lane-heavy does not set the slice, and says so when it has none.

# --- Memory kept for the desktop and agent sessions ---
# LANE_RESERVE_GB=10         # Default: 10, or memory GB/4 if that is lower; one not below memory is refused. Two thirds of the rest go to lane slots,
#                            # one third to CI; at night or away CI also takes a fifth of the reserve (the desktop
#                            # is idle), by day it gives a fifth back.

# --- Heavy work (lane-heavy) ---
# LANE_HEAVY_SLOTS=2         # builds or full test suites running at once on this machine. Default: cores/6, or
#                            # memory GB/16 if that is lower, at least 1; one more at night or away.
# LANE_HEAVY_MEM_GB=12       # memory limit of one job; 0 = no limit. Default: the lanes' share shared between the
#                            # slots. The job may not swap: past the limit it is killed and reported, not left to thrash.
# LANE_HEAVY_POLL_SECS=2     # how often a waiting job looks for a free slot.

# --- CI's own pool (lane-heavy under GITHUB_ACTIONS=true, or LANE_HEAVY_CI=1): CI never waits behind lanes ---
# LANE_CI_SLOTS=2            # CI jobs running at once, beside the lane slots. Default 2.
# LANE_CI_SLICE=ci.slice     # systemd user slice a CI job's scope joins; its MemoryMax and CPUQuota hold all CI
#                            # together. Default ci.slice.
# LANE_CI_MEM_GB=10          # memory for all CI together: what the CI slice's MemoryMax should be, and each CI
#                            # job's own limit (one job alone may use it all; the slice holds the sum, so an overrun
#                            # hits only CI). 0 = no limit. Default: CI's share of memory.
# LANE_CI_CPU_PCT=400        # CPU for all CI together, for the slice's CPUQuota. Default: a third of the cores by
#                            # day, two thirds at night or away (100 per core). lane-heavy does not set the slice;
#                            # whatever does (a user timer, `systemctl --user set-property --runtime`) reads these two.

# --- Opening another lane (lane-room). Looser than the gate: idle agents cost little. ---
# LANE_ROOM_MIN_MEM_GB=8     # hold a new lane under this much available memory. Default: memory GB/8, at least 2.
# LANE_ROOM_MAX_LOAD=18      # hold a new lane over this many runnable tasks (CPU contention: threads waiting for or
#                            # using a core, not the load average; SCHED_IDLE and nice-19 tasks are left out, so
#                            # idle-priority CI does not count). Default: 1.5 times the cores. Name kept from when
#                            # this was the 5-minute load.
# LANE_ROOM_SAMPLES=5        # runnable tasks are counted this many times and averaged.
# LANE_ROOM_SAMPLE_SECS=1    # seconds between the counts (decimals allowed; 0 for none).
# LANE_ROOM_MAX_AGENTS=12    # hold a new lane at this many lane agents. Default: cores, or memory GB/2 if that is
#                            # lower; at least 2.
