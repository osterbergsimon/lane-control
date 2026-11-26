# Issues (beads) doctrine

Owner-approved 2026-10-30. Applies to every project; a project's AGENTS.md carries it in its own words.

## Lifecycle
- **One issue, one key, one lane.** An issue becomes work when it gets its task key (`<KEY>-<n>`), kept as the label `lane:<KEY>-<n>` on the issue; the task title starts with that key.
- **Claim at launch.** The coordinator (or the lane, first thing) runs `bd update <id> --claim`. No running lane without an in-progress issue.
- **Close at merge.** The coordinator closes every issue a merge names, then syncs beads. A lane never closes issues.
- **Close the lane after the merge.** When a lane's PR is merged and its worktree is clean, pushed and an ancestor of the branch its PR merged into, whoever opened the lane (the coordinator) closes it (stops its reviewer and agent, removes the worktree without `--force`). Anything else, an unmerged lane, uncommitted changes or unknown ignored files, goes to the owner. Branches are kept unless the owner says to delete them.
- **In progress means a lane exists.** When a lane stops without merging, its issue goes back to open (`bd update <id> --status open`) with a Resume note (`--notes`).

## Setup
- No bd git hooks (`core.hooksPath` unset, nothing bd in `.git/hooks`) and no `issues.jsonl` export lying in `.beads/`: a hook that imports an old export reopens closed issues. Keep exports in `.beads/backup/`.

## Where beads lives, backup and restore (lc-5c1)
Owner-approved 2026-11-22. Goal: a project moves between machines without losing beads, issues and memories both.
- **Layout.** Each project's beads is a private GitHub repo `<owner>/<project>-beads` (owner and name from the project's
  `origin`, or `--beads-repo OWNER/NAME`), checked out at `~/code/<project>-beads`. The project's `.beads` is a
  git-ignored symlink to `<beads-repo>/.beads`. The same GitHub repo is the Dolt remote of the database
  (`git+https://github.com/<owner>/<project>-beads.git`, recorded in `.beads/config.yaml`), so git carries the config
  and `bd dolt push` carries the database. Beads repos stay private whatever the project's visibility, and beads data
  or a path to it never enters a public project repo.
- **No hooks in the project.** The project has no bd git hooks and never a `core.hooksPath` into beads (setup reports
  one and the move unsets it). The beads repo keeps the `.beads/hooks` folder bd lays down, committed as bd made it,
  and never activates it: with the symlink layout nothing there matters, and the same setup in every beads repo is
  easier to check. The session hook is `bd prime` on SessionStart, in the checkout's uncommitted
  `.claude/settings.local.json`, owned by lane-control (`core/harnesses/claude/record.md`).
- **Backup.** At every natural stop and every handover the coordinator runs, in the beads repo, `git push`, then `bd
  dolt push` (memories live in the database, so the second is required). The night manager asks for the same with its
  budget handover. A failed backup is reported to the owner, never retried silently. `lane-control project setup` says
  "beads has no off-machine copy" when a beads repo has no git remote or the database has no Dolt remote, and warns when
  the beads repo has commits not pushed. bd 1.0.4 cannot detect an unpushed Dolt database (it does not say whether the
  database is ahead of its remote), so only unpushed beads-repo git commits are checked; the Dolt half of a backup is
  covered by the `bd dolt push` in the handover, not by a check.
- **Move to the layout.** A project whose `.beads` is a plain folder is offered the move on the owner's yes, in a terminal
  only (without one setup says what it would do): the private repo is created (its own yes), the folder copied in, the
  database checked to open the same from the new place, pushed (git and Dolt), and only then replaced by the link. The
  original folder is kept beside the repos, never deleted by setup.
- **Restore on a new machine or a box.** With no beads in the checkout and the beads repo on GitHub, `lane-control
  project setup` (on a box, its own run there) clones it, links `.beads` and restores the database with `bd bootstrap
  --yes`, which brings back issues and memories (`tests/test_beads_roundtrip.py`). It never creates a repo or a fresh
  database: a repo that is not found, a `gh` that is not signed in or a config with no Dolt remote stops it with the
  reason. A box signs in to GitHub itself, with a fine-grained token limited to the beads repos, put there by the owner.
- **One machine at a time.** Only one machine works on a project's beads at a time. Before a migration the old machine's
  last step is the backup above; two machines writing at once is not supported (no merge handling beyond what bd and
  Dolt do on pull).

## Filing
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
- **Delete the task (the orchestrator's lane) only after** all three are verified and `branch @ commit` exists on origin.
- **Resume** = a fresh lane on that branch, brief = the Resume note. Remove the `parked` label when resumed.
- **Free slots take `bd ready --label parked` first**, by priority, before other ready issues.
- `scripts/park-check.sh` checks this daily (below).

## Daily parked-lane check (script, no tokens)
`scripts/park-check.sh`, a script routine run daily next to `stale-claims.sh` (today a Rove routine: `orchestrators/rove/notes.md#issues`), lists per project the `parked` issues that lack a `Resume (` note, whose branch is not on origin or does not contain the named commit, or that are closed, plus any parked over 7 days. One message to that project's coordinator; a clean run sends nothing.

## Daily stale-claim check (script, no tokens)
`scripts/stale-claims.sh` (a daily script routine; today a Rove routine, see its notes) lists in-progress issues whose key has no live task and sends the list to that project's coordinator, who fixes the claims per Lifecycle.

## Concurrent writers
- beads runs embedded Dolt (single writer, owner's setup: no server). Lanes write rarely: one claim, notes, a follow-up; bulk writes (grooming, closing after a merge) are the coordinator's, one at a time.
- A bd write that fails on a lock: wait a few seconds and retry, up to 3 times; never delete lock files or the database. Still failing: report it as `blocked` instead of working around it.
- If lock errors become regular, the fix is server mode (`bd dolt start`, concurrent writers); that changes the owner's setup, so it is an owner decision.
