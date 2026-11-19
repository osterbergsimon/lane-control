#!/bin/sh
# persona-hook.sh: Claude Code SessionStart hook (matchers startup|resume|compact) that gives a lane-control session
# its persona back at start, on resume and after every compaction. What it prints becomes the session's context.
#
# It prints $LANE_PERSONA only when LANE_ROLE and LANE_PERSONA are both set and the file is readable. The env vars
# are the marker: a worker started in the same checkout does not have them, so it never gets this persona
# (core/reliability.md 11). It never fails the session and always exits 0.
#
# Claude keeps at most 10,000 characters of a hook's output. A longer persona is not cut silently: the hook prints
# a first line telling the session to read the whole file now, then as much of the persona as fits, cut at a line
# end (measured in bytes, so multibyte text stays within the cap too).
[ -n "${LANE_ROLE:-}" ] && [ -n "${LANE_PERSONA:-}" ] && [ -f "$LANE_PERSONA" ] && [ -r "$LANE_PERSONA" ] || exit 0

whole=9800 # bytes printed as they are; a little under the cap
size=$(wc -c <"$LANE_PERSONA" 2>/dev/null) || exit 0
if [ "$size" -le "$whole" ]; then
    cat -- "$LANE_PERSONA" 2>/dev/null
    exit 0
fi
header="[lane-control] Your persona is longer than a session-start hook can show, so only its start is below. Read the whole file now, before anything else, and follow it: $LANE_PERSONA"
budget=$((9400 - ${#header}))
printf '%s\n\n' "$header"
[ "$budget" -gt 0 ] && head -c "$budget" -- "$LANE_PERSONA" 2>/dev/null | sed '$d'
exit 0
