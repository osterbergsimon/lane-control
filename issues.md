# Issues (beads) doctrine

Owner-approved 2026-10-30. Applies to every project; a project's AGENTS.md carries it in its own words.

## Lifecycle
- **One issue, one key, one lane.** An issue becomes work when it gets its task key (`<KEY>-<n>`), kept as the label `lane:<KEY>-<n>` on the issue; the task title starts with that key.
- **Claim at launch.** The coordinator (or the lane, first thing) runs `bd update <id> --claim`. No running lane without an in-progress issue.
- **Close at merge.** The coordinator closes every issue a merge names, then syncs beads. A lane never closes issues.
- **Close the lane after the merge.** When a lane's PR is merged and its worktree is clean, pushed and an ancestor of the branch its PR merged into, whoever opened the lane (coordinator or design lead) closes it (stops its reviewer and agent, removes the worktree without `--force`). Anything else, an unmerged lane, uncommitted changes or unknown ignored files, goes to the owner. Branches are kept unless the owner says to delete them.
- **In progress means a lane exists.** When a lane stops without merging, its issue goes back to open (`bd update <id> --status open`) with a Resume note (`--notes`).

## Setup
- No bd git hooks (`core.hooksPath` unset, nothing bd in `.git/hooks`) and no `issues.jsonl` export lying in `.beads/`: a hook that imports an old export reopens closed issues. Keep exports in `.beads/backup/`.

## Filing
- Sandboxed lanes (third-party model lanes in the opencode sandbox) have no `bd`: they put Resume notes and follow-ups in the PR body or their report, and the coordinator claims, notes and files for them.
- Lanes file a follow-up only for a real defect or missing piece, with evidence (command, file, observed vs expected), at P2 or higher.
- Nits, ideas and "could also" go in the PR body or the issue's notes, not in beads.
- Before filing, search (`bd search`) and add to an existing issue instead of duplicating.

## Weekly grooming (each coordinator, at the weekly review; standard tier)
1. In progress with no live task: back to open with a Resume note, or close if the work landed.
2. Open and untouched for 30 days or more: close with a one-line reason (obsolete, done elsewhere, won't do) or `bd defer`; keep only what you would still schedule.
3. Merge duplicates (`bd duplicate`); fold small related issues into one.
4. Report one line to the machine coordinator: `FYI <project>: beads groomed: open <before>→<after>, closed <n>, deferred <n>`.

## Parking
Parking a lane (stopping it without a merge: budget, reboot, priorities) must be resumable; owner 2026-10-31: "we cannot park stuff without being sure of how to resume; things will be forgotten".
- **Three things, all verified:** (1) label `parked` on the issue; (2) the issue's notes begin with one line `Resume (<date>): branch <b> @ <commit>; next: <first step>; open: <questions or none>`; (3) the issue key listed in the coordinator's handover.
- **Delete the Rove task only after** all three are verified and `branch @ commit` exists on origin.
- **Resume** = a fresh lane on that branch (sandboxed lanes: `OPENCODE_SANDBOX_PR_BRANCH` when it has a PR), brief = the Resume note. Remove the `parked` label when resumed.
- **Free slots take `bd ready --label parked` first**, by priority, before other ready issues.
- `scripts/park-check.sh` checks this daily (below).

## Daily parked-lane check (script, no tokens)
`scripts/park-check.sh`, a Rove script routine run daily next to `stale-claims.sh` (add a second routine pointing at it), lists per project the `parked` issues that lack a `Resume (` note, whose branch is not on origin or does not contain the named commit, or that are closed, plus any parked over 7 days. One message to that project's coordinator; a clean run sends nothing.

## Daily stale-claim check (script, no tokens)
`scripts/stale-claims.sh` (a Rove script routine) lists in-progress issues whose key has no live task and sends the list to that project's coordinator, who fixes the claims per Lifecycle.

## Concurrent writers
- beads runs embedded Dolt (single writer, owner's setup: no server). Lanes write rarely: one claim, notes, a follow-up; bulk writes (grooming, closing after a merge) are the coordinator's, one at a time.
- A bd write that fails on a lock: wait a few seconds and retry, up to 3 times; never delete lock files or the database. Still failing: report it as `blocked` instead of working around it.
- If lock errors become regular, the fix is server mode (`bd dolt start`, concurrent writers); that changes the owner's setup, so it is an owner decision.
