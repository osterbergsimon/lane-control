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
#
# A marked session also records its own id (from the hook's JSON on stdin) in
# ${XDG_STATE_HOME:-~/.local/state}/lane-control/sessions/<checkout, each byte not a letter or digit as ->/
# <role>.claude, so the idle signal (claude/adapter.py idle_minutes) reads that session's transcript and no other.
[ -n "${LANE_ROLE:-}" ] && [ -n "${LANE_PERSONA:-}" ] || exit 0

record_session() {
    [ -t 0 ] && return 0
    sid=$(cat 2>/dev/null | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9_-]*\)".*/\1/p' | head -n 1)
    [ -n "$sid" ] || return 0
    case "$LANE_ROLE" in *[!A-Za-z0-9-]* | "") return 0 ;; esac
    dir=$(cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null && pwd -P) || return 0
    case "${XDG_STATE_HOME:-}" in /*) state=$XDG_STATE_HOME ;; *) state=$HOME/.local/state ;; esac
    folder=$state/lane-control/sessions/$(printf '%s' "$dir" | LC_ALL=C tr -c 'A-Za-z0-9' '-')
    mkdir -p "$folder" 2>/dev/null || return 0
    printf '%s\n' "$sid" >"$folder/.$LANE_ROLE.claude.$$" 2>/dev/null &&
        mv -f "$folder/.$LANE_ROLE.claude.$$" "$folder/$LANE_ROLE.claude" 2>/dev/null
    return 0
}
record_session

[ -f "$LANE_PERSONA" ] && [ -r "$LANE_PERSONA" ] || exit 0

whole=9800 # bytes printed as they are; a little under the cap
size=$(wc -c <"$LANE_PERSONA" 2>/dev/null) || exit 0
if [ "$size" -le "$whole" ]; then
    cat -- "$LANE_PERSONA" 2>/dev/null
    exit 0
fi
header="[lane-control] Your persona is longer than a session-start hook can show, so only its start is below."
header="$header Read the whole file now, before anything else, and follow it: $LANE_PERSONA"
budget=$((9400 - ${#header}))
printf '%s\n\n' "$header"
[ "$budget" -gt 0 ] && head -c "$budget" -- "$LANE_PERSONA" 2>/dev/null | sed '$d'
exit 0
