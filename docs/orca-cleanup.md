# Lane cleanup through Orca's own "merged" rule

What lane-control does at lane close, and why it hand-rolls almost nothing. Findings are from Orca 1.4.268's own CLI
help, its bundled `orca-cli` skill and its shipped code, read-only; nothing in Orca's settings or files was touched.

## What Orca's workspace cleanup is

- `orca-ide workspace cleanup preview --json` shows, for every workspace, what the rules would do now and why the rest
  stay (`rows[].evaluation`: `action` and `cause` or `reason`). It removes nothing. `cleanup run` removes what the rules
  match and is the owner's; `cleanup report` lists recent runs and `nextRunAt` (null when no rule is on). The rules live
  in Orca's settings, and these commands never change them.
- Five rules, each off by default: merged, unchanged, inactive, finished automation runs, released orchestration
  worker workspaces. Settings -> Workspace Cleanup -> Cleanup rules; the rule lane-control wants is **"Delete merged
  workspaces"** ("The pull request is merged and everything in the workspace is in it"), with "Delete released
  orchestration worker workspaces" recommended beside it. A project can follow the
  global rules ("Use global"), set its own ("Custom") or turn cleanup off ("Off") in its own page in Settings, section
  "Workspace cleanup".
- A scheduled runner applies the rules, and every workspace is checked again right before removal; nothing is forced.

## What "merged" means

A workspace has the reason `merged` when either of these holds:

1. **The pull request is merged and contains the workspace's HEAD.** Orca reads the branch's review state from its own
   GitHub cache (written by the app, not read from GitHub at removal time) and compares the PR's head sha with the
   worktree's HEAD. Every lane merge pins `--match-head-commit`, so the merged head is exactly the commit that was
   reviewed and the worktree is on it.
2. **Git proves HEAD is in the default branch** (the base ref, else the repo's default branch) while the branch is
   ahead of it or a merged review exists. Orca checks the commits' content too, so a squash or a rebase merge counts
   as merged even when the PR was never cached. lane-control still merges with `--merge`: a merge commit keeps each
   lane's commits and its head sha is the one reviewed. After a merge commit the worktree is no longer ahead of the
   default branch, so Orca calls it merged only through a merged PR it knows of: the pinned head matches before the
   base branch is fetched, and any merged PR for the branch counts after it. Detection of a merge commit therefore
   depends on Orca's PR cache. When that entry is stale or missing, the row reads `unchanged` or `no-rule-matched`
   even with the merged rule on. Not exercised live here: the preview of a lane merged this way is the check
   (`reason: merged` on its row).

The companion rule **"Delete released orchestration worker workspaces"** ("Workspaces an orchestration worker used,
once the coordinator has released it") is **recommended**: lane-control lanes run as supervised workers, and Orca
removes a released worker's workspace only once Git finds its HEAD in the default branch or a merged review, so an
unmerged lane's worktree stays. It needs no PR cache: it matches a workspace a worker start created, whose worker was
released and whose HEAD Git finds in the default branch, a merged review or the coordinator's branch (the row's reason
is `orchestration-worker`). The coordinator releases a lane's worker after its merge; Orca's own check, not the
release, is what keeps an unmerged lane's worktree. Turn it on beside the merged
rule; turning it on is the owner's, and lane-control only says so.

A workspace with no commits beyond the default branch is `unchanged`, a different rule, not `merged`.

## What keeps a merged workspace (so it is not removed at once)

`rows[].evaluation.cause` says why. The ones a lane meets: `rules-off` (no rule is on, lane-control's case to report),
`project-off` (cleanup off for that project), `not-removable` (a main workspace or folder project: Orca decides this
before it looks at any rule, so a project with only its main workspace shows nothing about its rules),
`recently-active` (used in the last hour), `agent-running`, `terminal-running`, `uncommitted-changes`,
`unpushed-commits`, `ignored-files` (files such as `.env`; `node_modules` and build output do not count; the setting is
"Keep workspaces with ignored files"), `pinned`. So a lane's worker is released and its reviewer stopped before the rule
can take the worktree. A merged branch is deleted with its workspace; an unmerged one is kept.

## What lane-control does

- **project-setup** and **machine-setup** read the preview and say plainly when the rules are off (`rules-off`) or off
  for the project (`project-off`), with the exact path in Orca's Settings. They report only, never change Orca's
  settings, and an Orca without the command or a failing CLI is a `skipped` line. The preview cannot say which rule is
  on, so a project shown as "on" is told to check that "Delete merged workspaces" is one of them.
- They report the recommended released-workers rule in its own line (`orca cleanup workers`): `off` when no rule is on or
  the project has cleanup off (then it is off too), `exists` when a row's reason is `orchestration-worker` (a rule that
  matched it, so it is on), and otherwise `check`: "cannot tell; turn it on at Settings -> Workspace Cleanup -> Cleanup
  rules -> 'Delete released orchestration worker workspaces'". Nothing read-only says more: the preview and `report`
  name only the rule that matched a row (merged first, so a released worker that also merged reads `merged`), a project
  with no released worker shows no row for this rule, and neither `orca-ide agent-context` nor any other command
  prints Orca's settings. (Orca 1.4.268's CLI help and shipped code, read-only.)
- **project-setup** also turns on GitHub's "Automatically delete head branches" (`delete_branch_on_merge`) on the
  owner's yes in a terminal, where it is marked recommended and Enter means yes (`[Y/n]`; Enter, y or yes turns
  it on, anything else leaves it off); without a terminal it says so ("recommended") and a re-run says `exists`.
- **The coordinator's lane close** (`native-orca-lanes`, section 7): merge on the owner's word with `--merge
  --match-head-commit <full head sha>`, then `bd close` and `bd dolt push`. The merged rule removes the worktree. The
  trigger for the manual route is the lane's own preview row, not the rule: `reason: merged` with a keep cause is left
  to Orca; a row whose reason is not `merged` is closed by hand. First `git -C <W> status --porcelain --ignored`
  (only known build output and the `.beads` link: `worktree rm` removes ignored files such as `.env` without
  `--force`), `git fetch origin` and `git -C <W> merge-base --is-ancestor HEAD origin/<base>`; then `orca-ide terminal
  close --worktree path:<W> --all --json` and `orca-ide worktree rm --worktree path:<W> --delete-branch --json`,
  without `--force`; a dirty worktree stops it and the owner is asked. (Run once on a
  real lane: the terminal close returned ok, `worktree rm --delete-branch` removed the worktree and the merged local
  branch, and GitHub's setting removes the remote one.)

Only the project's own repo is covered by the setup report and the GitHub step; a repo attached with `--with` is not
(follow-up lc-rq7).
