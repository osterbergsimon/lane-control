# native Orca: setup

Orca's own orchestration (runs, tasks, workers, messages), with no Cotal. Status and trial results: `record.md`.
Pin the Orca version you tested: Orca ships often and its orchestration commands change (`record.md`).

## Install
- The Orca desktop app on the workstation (on Linux its CLI is `orca-ide`; plain `orca` may be another program).
  Agents run as a normal user, never root.
- Always: beads (`bd`), `gh` (authenticated), `jq`, `git`, bash, and the agent harnesses you use (Claude Code),
  signed in once as the user the agents run as.
- The machine caps: `~/.agents/bin/lane-heavy` and `lane-room` (copies of `core/scripts/`; `project-setup` of
  Orca + Cotal installs them, or copy them by hand with `lane-limits.sh` and `lane-tier`).
- Check: `orca-ide orchestration --help` lists the commands below.

## A project
`scripts/orch setup <project> --orchestrator native-orca` opens a PR that adds a short section to the project's
AGENTS.md (`templates/agents-section.md`): how the coordinator works on Orca's orchestration, the heavy-work rule and
where the handovers live. The project's own facts stay in its AGENTS.md.

## How a coordinator works
- **Run and tasks:** the coordinator makes a run and its tasks (with dependencies), then starts every ready task
  itself: a run never schedules work.
- **Workers:** `orca-ide orchestration worker-start` starts a worker in its own worktree and branch, with its brief as
  the first message. Reusing a kept-open terminal is much faster than a fresh one; release kept-open terminals when
  done.
- **Messages:** `send`, `ask`, `reply` and `check` (`check --wait` to block for one). A message reaches the agent as a
  one-line prompt in its composer telling it to run `check`; the content shows only after it does. If a worker that
  also coordinates its own run seems not to get a reply, nudge it to run `check` (`record.md`, point 3).
- **Nesting:** a worker may not start its own worker until the owner raises Settings > Orchestration > Nested worker
  depth (default 1). Prefer one coordinator level, or raise the depth knowingly.
- **Between sessions:** never type into another session's terminal (`orca-ide terminal send`): it disturbs what the
  owner types (owner, 2026-11-19). Claude sessions on one machine message each other with SendMessage; everything
  else goes to the peer's own Run (`orca-ide orchestration run-create` from its own terminal, then `send --to
  run:<id>`).

## Heavy work
Every build and full test suite runs through `~/.agents/bin/lane-heavy <command>`, one machine-wide gate for every
project and lane (`core/machine.md`); open a lane only when `~/.agents/bin/lane-room` exits 0. A project can route
its own heavy targets (a CI script, a make target) through `lane-heavy` so every caller gets the gate.

## A machine
`core/scripts/machine-setup [--host <h>]` sets a machine up once, here or on an SSH box (its header has the details;
every step says `exists` on a re-run):
- `~/.config/lane-control/machine.conf` from `core/templates/machine.conf.example`: this machine's coordinators
  (name, checkout, harness, terminal title), the runner repos it watches, night hours, idle and recheck minutes, and
  how long `lane-away on` lasts. Fill it in by hand; no value from it goes in a repo.
- The machine coordinator, in its own private folder (`--folder`, default `~/machine-coordinator`), started in an
  Orca terminal titled `machine coordinator` with its persona and hook.
- Three Orca automations in that folder, each behind a precheck that costs nothing when nothing is due: `night
  manager` hourly (`night-precheck`; the agent follows `night-manager.md`), `lane sweep` every 3 hours
  (`lane-sweep --precheck`, report only) and `machine check` every 30 minutes (`machine-check`: a budget step,
  memory or runner state changed; it also keeps the `budget-stopped` marker for the CI limits).
- The `ci-presence` user timer: the CI slice's CPU and memory follow presence (`lane-limits.sh`).
- The usage feed check: without `~/.claude/usage-history.jsonl` written in the last hour, budget steps are unknown.

The owner says they leave or return with `core/scripts/lane-away on|off|status` (or asks the machine coordinator).
A coordinator gets night check-ins only when it was started with `LANE_ROLE`/`LANE_PERSONA` (project-setup does
this); one started by hand is named on the precheck's stderr instead.
