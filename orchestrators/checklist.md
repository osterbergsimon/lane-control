# Evaluation checklist (every trial and retest)

One checklist for every orchestrator, so results compare. It joins what the ORCH-1 trial of native Orca tested
(2026-11-15: setup, messages, restart, remote, cost, the owner's view) with the Orca + Cotal checklist
(`orca-cotal/docs/orca.md` section 9), without naming an orchestrator.

## Result format
Each trial adds a dated block to the orchestrator's `record.md`, under `## Trials`, one line per point:

```
### YYYY-MM-DD <trial name> (<orchestrator version>)
- 1. Install: pass|partial|fail|untested — <evidence, one line>
- 2. Two workers: ...
...
- 9. Fit with the core: ...
```

Evidence that holds private data (hostnames, paths, transcripts, logs) stays outside this public repo; the line says
where it is kept, in generic words.

## 1. Install and bring-up
A fresh machine, from the orchestrator's `setup.md`. Pass: it comes up with the documented steps only. Partial: it
comes up with manual steps the docs lack (write them down). Fail: it does not come up.

## 2. A coordinator starts two workers
Each worker in its own worktree and branch, from the coordinator's own commands. Pass: both run and report. Partial:
it works with a manual step (for example the owner starts one). Fail: no separate worktree or branch per worker.

## 3. Messages both ways
Coordinator and worker talk both ways; messages never disturb what the owner types; a worker's report wakes the
coordinator. Pass: all three. Partial: one of them fails in a known case (say which). Fail: reports are lost or the
owner's typing is broken.

## 4. Nesting
A worker starts a sub-worker (a reviewer). Pass: it works, and the depth limit is known. Partial: it works only
after a setting the owner must change. Fail: no nesting.

## 5. Survives
An app restart, a laptop sleep, a dropped link, an hour of idle. Pass: work and messages resume with nothing redone.
Partial: some survive (say which). Fail: work is lost.

## 6. Owner view
The owner sees state at a glance (which workers run, wait or are done) and can step in on any one. Pass: both.
Partial: one. Fail: neither without reading logs.

## 7. Auth
Agents stay signed in; no prompts on the owner's desktop. Pass: no sign-in loss or prompt in the trial. Partial: a
known renewal step. Fail: agents lose their sign-in or prompts appear.

## 8. Telemetry and outbound hosts
Which hosts the machine contacts, and whether telemetry can be turned off. Pass: known and off. Partial: known, not
off. Fail: unknown.

## 9. Fit with the core
Budget steps, the heavy-work gate, beads, roles (`core/`). Pass: each works without changing a core rule. Partial: a
rule needs a per-orchestrator note (`notes.md`). Fail: a core rule cannot be kept.

## Harness trials
An agent harness (Claude, OpenCode, Codex, pi, ...) is tried on its own points, with the tier rules
(`core/scripts/lane-tier`: the provider must be one the owner allows). Each trial adds a dated block to
`core/harnesses/<name>/record.md`, under `## Trials`, one line per point, in the result format above.
- H1. Start: it starts in an Orca terminal with `LANE_ROLE`/`LANE_PERSONA` and the chosen model, and Orca
  recognises it (agent id and status).
- H2. Messages: Orca's messages reach it and its replies reach the sender.
- H3. Wake: a worker's report wakes it as a coordinator, with no nudge.
- H4. Compaction: its persona is back after a compaction, and a session without the markers never gets it.
- H5. Cost: what a day of coordinating costs, and whether the usage shows in budget steps.
