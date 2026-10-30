# Issues (beads) doctrine

Owner-approved 2026-10-30. Applies to every project; a project's AGENTS.md carries it in its own words.

## Lifecycle
- **One issue, one key, one lane.** An issue becomes work when it gets its task key (`<KEY>-<n>`, bd `--external-ref`); the task title starts with that key.
- **Claim at launch.** The coordinator (or the lane, first thing) runs `bd update <id> --claim`. No running lane without an in-progress issue.
- **Close at merge.** The coordinator closes every issue a merge names, then syncs beads. A lane never closes issues.
- **In progress means a lane exists.** When a lane stops without merging, its issue goes back to open (`bd update <id> --status open`) with a Resume note (`--notes`).

## Filing
- Lanes file a follow-up only for a real defect or missing piece, with evidence (command, file, observed vs expected), at P2 or higher.
- Nits, ideas and "could also" go in the PR body or the issue's notes, not in beads.
- Before filing, search (`bd search`) and add to an existing issue instead of duplicating.

## Weekly grooming (each coordinator, at the weekly review; standard tier)
1. In progress with no live task: back to open with a Resume note, or close if the work landed.
2. Open and untouched for 30 days or more: close with a one-line reason (obsolete, done elsewhere, won't do) or `bd defer`; keep only what you would still schedule.
3. Merge duplicates (`bd duplicate`); fold small related issues into one.
4. Report one line to the machine coordinator: `FYI <project>: beads groomed: open <before>→<after>, closed <n>, deferred <n>`.

## Daily stale-claim check (script, no tokens)
`scripts/stale-claims.sh` (a Rove script routine) lists in-progress issues whose key has no live task and sends the list to that project's coordinator, who fixes the claims per Lifecycle.

## Concurrent writers
- beads runs embedded Dolt (single writer, owner's setup: no server). Lanes write rarely: one claim, notes, a follow-up; bulk writes (grooming, closing after a merge) are the coordinator's, one at a time.
- A bd write that fails on a lock: wait a few seconds and retry, up to 3 times; never delete lock files or the database. Still failing: report it as `blocked` instead of working around it.
- If lock errors become regular, the fix is server mode (`bd dolt start`, concurrent writers); that changes the owner's setup, so it is an owner decision.
