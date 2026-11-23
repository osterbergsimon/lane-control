# Away log: lane-control, away/2026-11-23

Away mode started 2026-11-23 from main. The owner's limits, verbatim: nothing touching install or setup on real hosts; no hostnames, usernames, private paths or project names

This file lives at the root of the away branch and is removed when the branch is adopted. The summary is kept current;
each item adds one entry.

Projects in this period: this project only.

## Summary
- Done:
  - three small fixes: the item cap holds under concurrent merges, a clear message when a draining period loses its record, and --yes under the ! prefix (lc-jpg, lc-kbb, lc-7xh)
- Worth looking at first: nothing yet
- Drop if unsure: nothing yet
- Skipped: nothing yet

## Items

### Three small away-improve fixes (lc-jpg, lc-kbb, lc-7xh)
- What: (1) `away-merge` now checks `--max-items` again under the improve lock and keeps the lock through the merge and its record, so two merges at once cannot both take the last slot. (2) When a period that is draining loses its record, the refusal says to wrap up with `lane-improve drained` and start a new period, not `on --continue` (which a drain refuses). (3) The "no terminal" refusal of `lane-improve on` and the docs say that Claude Code's `!` prefix has no terminal and needs `--yes`.
- Why: they make the caps of this run trustworthy (a cap that can overshoot is no cap), and the two messages sent the owner to a command that fails. All three were filed by the owner today.
- Builds on: the caps and the period record from lc-2nz (`core/improve.py`, `core/scripts/away-merge`).
- Existing work checked: the open and closed issues lc-jpg, lc-kbb, lc-7xh and lc-2nz; `core/improve.py` (`hard_stop`, `_locked`, `cmd_on`), `core/scripts/away-merge`, `docs/away-improve.md`, the tests for the caps in `tests/test_away_merge.py`; the open PRs on this branch (AW-1 works on the secret scan only, not touched here). Nothing for these three exists yet.
- How to try: `python3 -m unittest tests.test_away_merge tests.test_lane_away` (the race test starts two real `away-merge` processes with one slot left); `lane-improve on` with no terminal prints the `!` hint.
- How to drop: revert the merge titled `away: three small away-improve fixes` (<sha>, filled in at merge); no later item depends on it yet.
- Tests: the full suite through `lane-heavy` (see the PR); each change has a test that failed before it.

