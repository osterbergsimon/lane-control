#!/usr/bin/env bash
# Machine night watch: a Rove script routine, hourly overnight, with a precheck that MODE_FILE says night.
# Revives each project coordinator's tab and prods it to check its lanes, with tonight's launch
# allowance per tier from machine and budget state. Spends no machine-coordinator tokens.
# A tier on a budget without a usage feed (none/manual) is allowed "within caps" unless memory is short.
set -uo pipefail
here=$(dirname "$0"); . "$here/lib.sh"
mkdir -p "$NIGHT_START_DIR"
avail=$(mem_avail_gb)
allow=""; summary=""
for t in $TIERS; do
  [ -z "${TIER_ENGINE[$t]:-}" ] || [ "${TIER_ENGINE[$t]}" = none ] && continue
  [ "$t" = top ] && continue   # top tier is never launched by the watch
  b=${TIER_BUDGET[$t]:-none}
  if [ "$avail" -lt "$NIGHT_MEM_MIN_GB" ]; then a="none: ${avail} GB memory available"
  elif [ -n "${BUDGET_USAGE[$b]:-}" ]; then
    read -r wk ss < <(budget_usage "$b"); ceil=$(budget_ceiling "$b")
    # the only budget limit is the owner's weekly ceiling steps (no nightly share)
    if   [ "$wk" -ge $((ceil-STEP_NO_NEW)) ]; then a="none: budget $b weekly ${wk}% at ceiling-${STEP_NO_NEW} (${ceil}%)"
    elif [ "$ss" -ge "$WINDOW_NIGHT_PCT" ]; then a="none until the window resets (budget $b at ${ss}%)"
    else a="within caps"; fi
    case "$summary" in *" $b:"*) ;; *) summary="$summary $b: weekly ${wk}% (ceiling ${ceil}%), window ${ss}%;" ;; esac
  else a="within caps (no usage feed: $b)"; fi
  allow="$allow $t: $a;"
done
for p in "${!COORD_TASK[@]}"; do
  t=${COORD_TASK[$p]}
  rove api tab-revive --task-id "$t" --tab "$COORD_TAB" >/dev/null 2>&1
  r=$(rove api send --if-runnable --expect-report --task-id "$t" --tab "$COORD_TAB" --prompt-file - <<MSG
[NIGHT WATCH $(date +%H:%M)] Owner away. Check your lanes (context): nudge stuck or unsubmitted first prompts, revive dead tabs, handle reports per your standing rules, enforce the wall-clock and stall limits (model-routing.md, Unattended limits). New launches per tier:${allow} Owner decisions go to gates. Report tersely.
MSG
)
  echo "$p: $(printf '%s' "$r" | jq -c '{delivered, skipped: .skipped.reason}' 2>/dev/null || echo "$r")"
done
echo "mem ${avail} GB;${summary} allowance:${allow}"
