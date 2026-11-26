# native Orca

Status: in use

Orca worktrees and Orca's own orchestration (run, task, worker, check), with no Cotal. Setup: `setup.md`. Adapter:
`adapter.py` (`status`, `setup`, `strip`).

## Owner decisions
- 2026-11-19: a coordinator follows the machine coordinator only on budget and machine matters, and checks the facts
  first (the usage feed, free memory, the heavy-work queue): names prove nothing (`core/roles.md`). This narrows the
  Orca + Cotal rule, where a `machine coordinator:` order counted like the owner's own words with no limit on the
  kind; the parked Cotal persona keeps that wording.
- 2026-11-19: the design lead merges only on the owner's own word, never on the coordinator's (the Orca + Cotal
  persona said the owner or the coordinator): a peer relaying approval is never approval.
- 2026-11-19: agents never type into another session's terminal (it disturbs what the owner types). Claude sessions
  on one machine message each other with SendMessage; everything else goes through Orca, to the peer's own Run.
- 2026-11-21: the long-lived design lead is retired for the design kit: the frontend brief addition, `DESIGN.md`, the
  design reviewer (on every frontend lane), the `component-gallery` skill and `design-shots`
  (`docs/superpowers/specs/2026-11-21-design-kit-design.md`). `--design-lead` is refused and `--no-design-lead` does
  nothing. OpenCode lanes may do frontend work, and setup installs the design skills for OpenCode as well as Claude.
- 2026-11-25: "lets use orca where we can": prefer Orca's own feature over lane-control code. The doctrine is in the
  README (Doctrines, "Use Orca where we can"); the audit of what overlaps, as of 1.4.274, is
  `docs/research/2026-11-25-orca-overlap.md`.

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

### 2026-11-20 NO-6: `project-setup <repo> <host>` (Orca 1.4.250, an unprivileged container as the box)
Phase 6 of the native-Orca plan (`remote.md`). The saved Orca SSH host trial-vps was unreachable (no route to host)
and logs in as root, so the box was an unprivileged container (no extra capabilities or devices, sshd on
127.0.0.1 only, no agent and no credentials in it); a second container's Orca server stood in for the desktop.
- Direction 1: pass. `orca-host-install --pair` installed and paired the box's server; `project-setup <repo> <box>
  --no-start --skip-adapt` copied lane-control there and the box's copy did the native setup (personas, hook,
  AGENTS.md exclude, settings store, the repo in the box's Orca); `machine-setup --host` made the machine
  coordinator's folder and the three automations there (removed again after the run). Starting the coordinator on
  the box was not run: no agent may run in the container.
- Direction 2, against the stand-in: pass. The link was typed at the hidden prompt (not echoed), sent on ssh's
  stdin, and saved on the box by `environment add --pairing-code -` through `pairing-code-stdin.cjs`; Orca 1.4.250's
  `environment add` takes the code only as an argument (no stdin, no file), which the preload works around inside
  the process. The box then reached the stand-in through the reverse tunnel (the unit's own ExecStart line, run by
  hand: the run used a logging systemctl so no user unit was installed here). Afterwards the token was in no file on
  the box but Orca's own pairing store, and in none here. `--remove-host` removed it and printed where to revoke.
- A share link lets the box run commands as the owner on this computer (no desktop control) until it is revoked in
  Orca here (Settings > Servers); setup says so when it asks for one and again when it removes a box.
- Not yet run, needs the owner: the desktop's own share link (Settings > Servers > "Share this computer"), a lane
  started from a box coordinator showing in the desktop window, its `worker_done` reaching the box, and whether that
  wakes an agent coordinator there (a box with a signed-in agent).

### 2026-11-20 UP-3: worker_done contract and a dispatch that can no longer report (Orca 1.4.250, one machine)
A scratch Run, plain shell terminals as the workers (started with a command that waits on a file, so nothing was typed
into any terminal), tasks made with `task-create` and dispatched with `dispatch`; no agent was started and no other
Run was touched. The Run cannot be deleted (`reset` is global), so its tasks were set to `failed`/`cancelled`.
- Missing ids: pass — `worker_done` without `--task-id` is refused in the worker's terminal with `missing_task_id`,
  without `--dispatch-id` with `missing_dispatch_id`; each also arrived in the coordinator's Run inbox as a
  `Rejected worker_done` message with the code (`check`).
- Wrong owner: pass — a report from a terminal that is not the dispatch's own is refused with
  `worker_identity_changed` ("<terminal> is not the exact process that owns Dispatch <id>"), and arrives in the
  Run inbox the same way. This reproduces the code, not its cause in the field (a runtime restart).
- Accepted report: pass — `lifecycle=completed`, the task completed, and the coordinator's session got Orca's
  one-line message prompt for it (the same prompt also woke this session for the rejected ones).
- Recovery on a plain dispatch: partial — after the worker's terminal was closed the dispatch was failed and its task
  `ready` again; `worker-abandon` said it was already settled, `worker-release` said `retained` (no terminal of its own),
  and a second dispatch of the task reported and completed. Not tested: a real runtime restart (it would have
  restarted the owner's Orca), `worker-start --retry-of` (it starts an agent), and any second Orca server.
- A `worker_done` from a lane on a paired server to a coordinator on the box, and whether it wakes an agent there:
  still not run (needs a box with a signed-in agent and the owner's share link). `remote.md` said poll-only until it
  was. 2026-11-26: a field report measured the wake on two servers at the same version; `remote.md` (Reports across
  hosts) now says so. lane-control has not repeated it.
