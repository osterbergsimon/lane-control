#!/bin/sh
# persona-hook.sh: Claude Code SessionStart hook (matchers startup|resume|compact) that gives a lane-control session
# its persona back at start, on resume and after every compaction. What it prints becomes the session's context.
#
# It prints $LANE_PERSONA only when LANE_ROLE and LANE_PERSONA are both set and the file is readable. The env vars
# are the marker: a worker started in the same checkout does not have them, so it never gets this persona
# (core/reliability.md 11). It never reads stdin, never fails the session, and always exits 0.
[ -n "${LANE_ROLE:-}" ] && [ -n "${LANE_PERSONA:-}" ] && [ -f "$LANE_PERSONA" ] && [ -r "$LANE_PERSONA" ] &&
    cat -- "$LANE_PERSONA" 2>/dev/null
exit 0
