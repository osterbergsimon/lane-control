<!-- orchestrator: native-orca -->
## Orchestration: native Orca

Agents in this repo are run by Orca's own orchestration (`orca-ide orchestration`; plain `orca` on some hosts).

Coordinator:
- Plan the work as a run with tasks (`run-create`, `task-create`, with dependencies), then start every ready task
  yourself: a run never schedules work on its own.
- Start each worker with `worker-start`, one task per worker, each in its own worktree and branch, briefed by the task
  and this file. Never let a worker work in the main checkout.
- Talk with `send`, `ask` and `reply`; read with `check` when a message wakes you. A worker's report arrives as a
  one-line prompt to run `check`. If a reply seems lost, ask the worker to run `check`.
- A worker cannot start its own worker unless the owner has raised the nested worker depth in Orca's settings.
- Release a worker after its merge. Merge only on the owner's word.
- Before any edit or state-changing command, write one line naming whom it is for: `LANE: <KEY>-<n>` for the lane
  you act for, or `DOCTRINE: <why>` for one of the three exceptions below. Waits are lane work: never wait, poll,
  sleep or watch yourself. Builds, CI and checks you would sit on go to a lane. The three exceptions, and no others:
  1. Merging on the owner's word; 2. Closing lanes (workers, reviewers, merged worktrees; `worker-abandon` only on
  the owner's word); 3. Your own records (beads memories, issue notes, messages).

Worker:
- Work only in your own worktree and branch. Report once at the end with `reply` (or `send`): branch, PR, gates run,
  deviations, issues filed.

Heavy work: every build and full test suite runs through `~/.agents/bin/lane-heavy <command>`, one machine-wide gate
for every project and lane; before opening a worker, `~/.agents/bin/lane-room` must exit 0.

Handovers: the latest is the beads memory `handover-latest` (`bd recall handover-latest`); refresh it at every
natural stop. After a switch from another orchestrator, its last dated handover memory holds what was carried over.
<!-- /orchestrator: native-orca -->
