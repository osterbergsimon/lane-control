# Rove: how the shared doctrine runs on Rove

Passages moved here from the shared files in `core/` because they only apply on Rove. The rules themselves are in
`core/`; this file says how Rove carries them out. Rove is parked (`record.md`).

## Roles
From `core/roles.md`.

Machine coordinator:
- It is the main task on the playbook repo.
- Tell coordinators with `rove api send --task-id <their main> --tab <engine tab> --prompt-file -`.
- Day vs night: the mode file (`day`/`night`), set when the owner leaves or returns.
- Every project's open owner decisions: `rove api gates --repo <path>` per project. Repeat decisions in chat if the
  web inbox does not render gates.
- Escalate to the owner as gates plus chat.
- Public posts: `rove api feedback` becomes a public discussion under the owner's account (`core/reliability.md`
  section 22); it needs the owner's approval first, like any public post.

Project coordinator:
- It is the main task on the project's main checkout.
- Briefs: `orchestrators/rove/templates/worker-brief.md`.
- Launch: `rove api add`, title and branch per AGENTS.md.
- Reports: the hook injects task state each turn.
- Decisions: as Rove gates.
- Unattended: report each night prod with `rove api report`.
- Keep the coordinator's inbox hook out of committed settings and guard it with the coordinator's task id.

## Model routing
From `core/model-routing.md`.

- Wall clock per lane: also checked by the night routine.
- A blocked lane parks on a gate (needs owner).
- After a window reset, coordinators rebuild from `rove api context`.

### Night mode
Rove is the only orchestrator with a night routine (routines in the Rove daemon).
- The machine coordinator runs it. The owner says "leaving" or "back" to it (or to any coordinator, which forwards it); it sets the mode file to `night` or `day`.
- At night the hourly script routine (`scripts/machine-night-watch.sh`, no tokens) reads each project coordinator's `rove api context` and prods it only when it is needed: a row waiting-on-you / ready-for-review / landing / unknown or an unhandled inbox item, a dead engine tab of a working lane, a stalled lane (no activity for over 2 h), or idle capacity (ready issues, an allowance that is not none, fewer working lanes than `COORD_SLOTS`, default 1). Otherwise it logs "idle, not woken" (no revive, no send: no cold-cache turn). A prod names the criterion that fired and carries a launch allowance per tier from machine state: none from ceiling-STEP_NO_NEW, under the memory minimum, or with the window at its night limit; otherwise within caps. The top tier is never launched by the watch. On the first run of each night, every coordinator with a live tab is asked to refresh its handover (`bd remember --key handover-latest`) and then gets a plain `/compact`, each sent only when its turn is idle, so the next resume loads a short summary; coordinators past their stop-all step are skipped throughout.
- Coordinators report each prod with `rove api report`; the machine coordinator reads `rove api deliveries` for the morning report.

### Third-party lanes
- **Nesting and base branch:** called from inside a Rove session, `rove api add` makes the new task a child of the caller and cuts its worktree from the caller's CURRENT branch tip, not the repo default. A launcher on any other branch than the lane's base (a design lead on its own branch, a lane) passes `--base-branch <base>`; `--parent <id>` nests under another task, `--no-parent` makes a top-level task. (Older Rove had a separate `spawn` verb; it is gone.)

### Context protocol
- A coordinator starting fresh reads `rove api context` as well as its handover.
- The night watch triggers the handover refresh and compaction at night start.
- The night watch prods a coordinator only when Rove shows something for it.

## Machine
From `core/machine.md`.
- [ ] Set `NIGHT_MEM_MIN_GB` in the config from the machine's memory.
- [ ] Temp directory for the mode file and night-start snapshots (default `$TMPDIR` or `/tmp`); it must survive between routine runs, not reboots.
- [ ] Tools: `rove`.
- [ ] Clock and timezone: night mode and routine times assume the owner's local time.

## Issues
From `core/issues.md`.
- Parking: delete the Rove task only after the three parking checks are verified and `branch @ commit` exists on origin.
- `scripts/park-check.sh` and `scripts/stale-claims.sh` run as Rove script routines (one routine each, daily), and send
  their message with `rove api send`; `stale-claims.sh` reads live tasks from `rove api list`.
