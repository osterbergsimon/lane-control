# native Orca: setup

Orca's own orchestration (runs, tasks, workers, messages), with no Cotal. Status and trial results: `record.md`.
Pin the Orca version you tested: Orca ships often and its orchestration commands change (`record.md`).

## Install
- The Orca desktop app on the workstation (on Linux its CLI is `orca-ide`; plain `orca` may be another program).
  Agents run as a normal user, never root.
- Always: beads (`bd`), `gh` (authenticated), `jq`, `git`, bash, and the agent harnesses you use (Claude Code),
  signed in once as the user the agents run as.
- The machine caps: `~/.agents/bin/lane-heavy` and `lane-room` (copies of `core/scripts/`; `project-setup`
  installs them, or copy them by hand with `lane-limits.sh` and `lane-tier`).
- The lane skill, `skills/native-orca-lanes` (gate, lane, messages, report shape, review, close): `project-setup`
  installs it with the caps in `~/.agents/skills/native-orca-lanes` (with the lane brief as its `brief.md`), linked
  into `~/.claude/skills` and `~/.config/opencode/skills`; `--refresh` puts back a changed or missing file. Orca's
  own `orchestration` guide (`orca-ide skills get orchestration`) stays the command reference.
- Check: `orca-ide orchestration --help` lists the commands below.

## A project
`scripts/orch setup <project> --orchestrator native-orca` opens a PR that adds a short section
(`templates/agents-section.md`) to the project's `AGENTS.lane-control.md`, with one pointer line to it in AGENTS.md:
how the coordinator works on Orca's orchestration, the heavy-work rule and where the handovers live. The project's own
facts stay in its AGENTS.md (the policy: `../README.md`, "What lane-control writes into a project").

## How a coordinator works
- **Run and tasks:** the coordinator makes a run and its tasks (with dependencies), then starts every ready task
  itself: a run never schedules work.
- **Workers:** `orca-ide orchestration worker-start` starts a worker in its own worktree and branch, with its brief as
  the first message. Reusing a kept-open terminal is much faster than a fresh one; release kept-open terminals when
  done.
- **Messages:** `send`, `ask`, `reply` and `check` (a coordinator never uses `check --wait`: waits are lane work).
  A message reaches the agent as a one-line prompt in its composer telling it to run `check`; the content shows only
  after it does. If a worker that also coordinates its own run seems not to get a reply, nudge it to run `check`
  (`record.md`, point 3).
- **Nesting:** a worker may not start its own worker until the owner raises Settings > Orchestration > Nested worker
  depth (default 1). Prefer one coordinator level, or raise the depth knowingly.
- **Between sessions:** never type into another session's terminal (`orca-ide terminal send`): it disturbs what the
  owner types (owner, 2026-11-19). Claude sessions on one machine message each other with SendMessage; everything
  else on this machine goes to the peer's own Run (`orca-ide orchestration run-create` from its own terminal, then
  `send --to run:<id>`). A peer on another host has no `run:` route (without `--on` it goes to this Orca; with `--on` the server refuses it).
  Check that both servers are 1.4.256 or newer (`runtime.appVersion` in `orca-ide status --json` and in `orca-ide status
  --environment <environment> --json`), take the peer's handle from `orca-ide terminal list --environment <environment>
  --json` (the terminal whose `title` is its session name), and `orca-ide orchestration send --on <environment> --to
  term_<id> --subject "<subject>" --body "<message>"`. If either server is older, or no terminal has that title, the owner
  carries the message. This route is not yet run across two servers (`remote.md`, Messages to a session on the other
  host).

## Heavy work
Every build and full test suite runs through `~/.agents/bin/lane-heavy <command>`, one machine-wide gate for every
project and lane (`core/machine.md`); open a lane only when `~/.agents/bin/lane-room` exits 0. A project can route
its own heavy targets (a CI script, a make target) through `lane-heavy` so every caller gets the gate.

## An SSH host
`core/scripts/project-setup <repo> <host>` sets a project up on an SSH box, driven from this machine's Orca, and lets
the box's coordinator run lanes here with the desktop's share link (`remote.md`); `project-setup --remove-host
<host>` undoes the second part.

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
  (`lane-sweep --precheck`, report only, waking its agent only for findings new since the last run) and `machine check` every 30 minutes (`machine-check`: a budget step,
  memory or runner state changed; it also keeps the `budget-stopped` marker for the CI limits).
- The `ci-presence` user timer: the CI slice's CPU and memory follow presence (`lane-limits.sh`).
- On a machine set up by hand before: automations named `night-manager` or `lane-sweep` and an older CI timer
  (`ci-cpu`) are reported with the commands to remove them; nothing is doubled or replaced without the owner.
- The usage feed check: without `~/.claude/usage-history.jsonl` written in the last hour, budget steps are unknown
  and night check-ins stay off (the night precheck says so once a night).

The owner says they leave or return with `core/scripts/lane-away on|off|status` (or asks the machine coordinator).
A coordinator gets night check-ins only when it was started with `LANE_ROLE`/`LANE_PERSONA` (project-setup does
this); one started by hand is named on the precheck's stderr instead.
