# native Orca

Status: in use

Orca worktrees and Orca's own orchestration (run, task, worker, check), with no Cotal. Setup: `setup.md`. Adapter:
`adapter.py` (`status`, `setup`, `strip`).

## History
- 2026-11-15 trial (ORCH-1, Orca 1.4.250): adopt with changes for Claude lanes.
- 2026-11-15 in use: every project moved to native Orca orchestration, by hand, the same day.
- 2026-11-19 lessons from the week's lanes written into doctrine (`core/reliability.md` entries 29-37).

## Lessons
What running lanes natively taught us, as the places they are written down:
- An idle coordinator with pending work is stuck; the night manager (phase 4 of the native-Orca plan) wakes it
  (`core/reliability.md` entries 29 and 30 hold it and the lane sweep). Idle is the newest write of the coordinator's session transcript: Orca's terminal
  `lastOutputAt` is no idle signal (status-line redraws keep it fresh), and a worktree's `lastActivityAt` can be days
  old, so the sweep uses the newest of push, commit and terminal output.
- Presence-based limits, CI waiting outside the heavy slot, the runner and Nix items, the test time-scale and
  relayed-approval rules, and budget steps from the ceiling: entries 31-37.

## Retest when
- Orca changes its orchestration commands (it ships about 1.4 desktop releases a day): pin the version and retest
  points 3 and 4 on an upgrade.
- The reply-wake bug (point 3) is fixed upstream: drop the "check your mail" nudge.

## Trials

### 2026-11-15 ORCH-1 (Orca 1.4.250, built-in orchestration, no Cotal)
Summarised from the trial's report, kept outside this repo with its evidence.
- 1. Install: pass — run, task with dependencies, worker start (about 20 s fresh, under 2 s reusing a kept-open
  terminal), task update and dispatch reuse all worked; one manual step: the coordinator must start every ready task
  itself (a run never schedules work).
- 2. Two workers: partial — the coordinator started its workers itself and they reported back, but the workers
  shared one child worktree (the trial's start records show two workers in the same workspace); a worktree and
  branch per worker was not tried.
- 3. Messages: partial — send, ask, reply, check and check-and-wait work, and every message woke plain workers; but
  replies to a worker that also coordinates its own run were never delivered, and the owner had to nudge it twice.
  The owner sees a one-line "you have a message, run check" prompt in the agent's composer, queued while it is busy.
- 4. Nesting: partial — depth 1 by default: a worker's own worker start is refused until the owner raises the nested
  worker depth in Orca's settings.
- 5. Survives: partial — app quit and reopen passed: runs, tasks, dispatches, terminals and tabs came back, agent processes
  were never killed, and a worker's report woke the coordinator after the reopen. Laptop sleep, dropped link and an
  hour idle were not tested. Quitting needs the owner (no CLI quit).
- 6. Owner view: partial — an extra worker tab per lane and one-line wake prompts in composers; message content
  shows only after the agent runs check; kept-open tabs need releasing.
- 7. Auth: untested — not checked in the trial.
- 8. Telemetry: untested
- 9. Fit with the core: untested — budget steps, the heavy-work gate and beads were not checked in the trial.

Not on the checklist: remote workers were not tried (no paired Orca server). Cost passed: idle workers spend no
tokens (nothing polls); a worker call carries about 50k of base context, mostly cache reads; each agent process uses
about 0.7% CPU idle and 360-470 MB memory. Recommendation recorded with the trial:
a single-level coordinator (or set nesting depth to 2 knowingly), report the reply-wake bug upstream and keep a
"check your mail" nudge until it is fixed, keep OpenCode lanes on lane-control's sandboxed path (the sandbox was removed later
the same day; the README's "Sandbox" section has the owner's decision), and pin the
Orca version given the daily churn.
