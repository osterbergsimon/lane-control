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
- **Nudging another tab:** `orca-ide terminal send --terminal <handle> --text "<line>" --enter` types a line into
  another agent's terminal (for example to tell a coordinator a worker is done).

## Heavy work
Every build and full test suite runs through `~/.agents/bin/lane-heavy <command>`, one machine-wide gate for every
project and lane (`core/machine.md`); open a lane only when `~/.agents/bin/lane-room` exits 0. A project can route
its own heavy targets (a CI script, a make target) through `lane-heavy` so every caller gets the gate.
