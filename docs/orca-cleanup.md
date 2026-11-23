# Lane cleanup through Orca's own "merged" rule

What lane-control does at lane close, and why it hand-rolls almost nothing. Findings are from Orca 1.4.268's own CLI
help, its bundled `orca-cli` skill and its shipped code, read-only; nothing in Orca's settings or files was touched.

## What Orca's workspace cleanup is

- `orca-ide workspace cleanup preview --json` shows, for every workspace, what the rules would do now and why the rest
  stay (`rows[].evaluation`: `action` and `cause` or `reason`). It removes nothing. `cleanup run` removes what the rules
  match and is the owner's; `cleanup report` lists recent runs and `nextRunAt` (null when no rule is on). The rules live
  in Orca's settings, and these commands never change them.
- Five rules, each off by default: merged, unchanged, inactive, finished automation runs, released orchestration
  workers. Settings -> Workspace Cleanup -> Cleanup rules; the rule lane-control wants is **"Delete merged
  workspaces"** ("The pull request is merged and everything in the workspace is in it"). A project can follow the
  global rules ("Use global"), set its own ("Custom") or turn cleanup off ("Off") in its own page in Settings, section
  "Workspace cleanup".
- A scheduled runner applies the rules, and every workspace is checked again right before removal; nothing is forced.

## What "merged" means

A workspace has the reason `merged` when either of these holds:

1. **The pull request is merged and contains the workspace's HEAD.** Orca reads the branch's review state from GitHub
   and compares the PR's head sha with the worktree's HEAD. This is why every lane merge pins `--match-head-commit`:
   the merged head is exactly the commit the lane's worktree is on.
2. **Git proves HEAD is in the default branch** (the base ref, else the repo's default branch) while the branch is
   ahead of it or a merged review exists. Orca checks the commits' content too, so a squash or a rebase merge counts
   as merged even when the PR was never cached. lane-control still merges with `--merge`: a merge commit keeps each
   lane's commits and its head sha is the one reviewed. After a merge commit the worktree is no longer ahead of the
   default branch, so Orca calls it merged through point 1 (the PR Orca saw merged, with the pinned head); without
   that PR state it would read the workspace as `unchanged`, a rule that is off. Not exercised live here: the
   preview of a lane merged this way is the check (`reason: merged` on its row).

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
- **project-setup** also turns on GitHub's "Automatically delete head branches" (`delete_branch_on_merge`) on the
  owner's yes in a terminal; without a terminal it only says so, and a re-run says `exists`.
- **The coordinator's lane close** (`native-orca-lanes`, section 7): merge on the owner's word with `--merge
  --match-head-commit <full head sha>`, then `bd close` and `bd dolt push`. The merged rule removes the worktree. When
  the rule is off: `orca-ide terminal close --worktree path:<W> --all --json`, then `orca-ide worktree rm --worktree
  path:<W> --delete-branch --json`, without `--force`; a dirty worktree stops it and the owner is asked. (Run once on a
  real lane: the terminal close returned ok, `worktree rm --delete-branch` removed the worktree and the merged local
  branch, and GitHub's setting removes the remote one.)
