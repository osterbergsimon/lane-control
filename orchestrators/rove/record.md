# Rove

Status: parked

The Rove daemon, its tasks, gates and routines. Setup: `setup.md`; how the shared doctrine runs on it: `notes.md`.
No adapter yet: `scripts/orch` refuses every action for Rove until one is written at a retest.

## History
- 2026-10-29 in use: lane-control starts as the playbook for Rove (tasks, gates, routines, the night watch).
- 2026-11-03 Rove 0.9.276: `spawn` is gone; lanes start with `rove api add` (nesting and base-branch rule in
  `notes.md`).
- 2026-11-08 parked: projects moved to Orca + Cotal; retest later.

## Retest when
- The owner wants routines, gates and a night watch again, which no other orchestrator here has.
- A retest writes the adapter (`status`, `setup`, `strip`) from `setup.md` step 4 and the templates.

## Trials
No checklist trial was recorded; what the repo learned on Rove is in `core/reliability.md` (entries tagged
`Orchestrator: Rove`).
- 1. Install: untested
- 2. Two workers: untested
- 3. Messages: untested
- 4. Nesting: untested
- 5. Survives: untested
- 6. Owner view: untested
- 7. Auth: untested
- 8. Telemetry: untested
- 9. Fit with the core: untested
