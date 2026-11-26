# native Orca: setup

Orca's own orchestration (runs, tasks, workers, messages), with no Cotal. Status and trial results: `record.md`.
Pin the Orca version you tested: Orca ships often and its orchestration commands change (`record.md`).
Where Orca has a feature that does the job, use it rather than adding lane-control code (README, Doctrines, "Use Orca
where we can"; what overlaps today: `docs/research/2026-11-25-orca-overlap.md`).

## Install
- The Orca desktop app on the workstation (on Linux its CLI is `orca-ide`; plain `orca` may be another program).
  Agents run as a normal user, never root.
- Always: beads (`bd`, bd 1.0.4 or newer: `machine-setup` checks it and says how to fix it, never installs it), `gh` (authenticated), `jq`, `git`, bash, and the agent harnesses you use (Claude Code),
  signed in once as the user the agents run as.
- lane-control itself, installed once per machine: `scripts/lane-control install` from a checkout lays its last commit
  at `~/.local/share/lane-control/<sha>/` with a `current` link (`project-setup` does it too when run from a
  checkout; `core/README.md`, "The install"). Personas name `.../lane-control/current`, never the checkout.
- The machine caps: `~/.agents/bin/lane-heavy` and `lane-room` (links into `.../lane-control/current/core/scripts/`;
  `project-setup` and `lane-control install` make them, with `lane-limits.sh`, `lane-tier`, `design-shots` and
  `lane-control`).
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

## Beads
Where a project's beads lives, its backup and its restore: `core/issues.md`, "Where beads lives". `project-setup` checks
the layout, offers the move of a plain `.beads` folder or the restore on a new machine on your yes (a terminal), adds
`bd prime` as the session hook next to the persona hook, and names a failing committed beads hook without editing it.
`--beads-repo OWNER/NAME` overrides the beads repo's name and is kept in the settings store. On a box, setup says
when `gh` needs a sign-in (a fine-grained token limited to the beads repos) and never handles the token.

## Lane cleanup
A merged lane's worktree is removed by Orca's own cleanup rule "Delete merged workspaces", not by a script of ours
(`docs/orca-cleanup.md`). `project-setup` and `machine-setup` read `orca-ide workspace cleanup preview` and say
plainly when no rule is on or the project has cleanup off, with where to turn it on in Orca (Settings -> Workspace
Cleanup -> Cleanup rules); they also report the recommended rule "Delete released orchestration worker workspaces"
(lane-control lanes run as supervised workers; Orca removes a released worker's workspace only once Git finds its HEAD
in the default branch or a merged review, so an unmerged lane's worktree stays), and say "cannot tell" when the preview
shows no row it matched; they never change Orca's settings. `project-setup` also turns on GitHub's "Automatically
delete head branches" in a terminal (recommended: Enter, y or yes turns it on, anything else leaves it off; without a
terminal it only says so; a re-run says `exists`).

## How a coordinator works
- **Run and tasks:** the coordinator makes a run and its tasks (with dependencies), then starts every ready task
  itself: a run never schedules work.
- **Workers:** `orca-ide orchestration worker-start` starts a worker in its own worktree and branch, with its brief as
  the first message. Reusing a kept-open terminal is much faster than a fresh one; release a worker after its
  merge.
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
  carries the message. This route has run only between scratch servers (`remote.md`, Messages to a session on the other
  host).

## Heavy work
Every build and full test suite runs through `~/.agents/bin/lane-heavy <command>`, one machine-wide gate for every
project and lane (`core/machine.md`); open a lane only when `~/.agents/bin/lane-room` exits 0. A project can route
its own heavy targets (a CI script, a make target) through `lane-heavy` so every caller gets the gate.

## An SSH host
`core/scripts/project-setup <repo> <host>` sets a project up on an SSH box in two directions, each with its own
tunnel, both started on this machine (`remote.md`; the picture:
[how the parts fit](../../docs/how-it-fits.md#a-box-two-directions)):
- direction 1, this machine controls the box: `orca-tunnel-<host>` (`ssh -L`), made by `orca-host-install --pair`,
  with no click and no pairing code from the owner;
- direction 2, the box's coordinator starts lanes here: `lane-tunnel@<host>` (`ssh -R`), made by `project-setup`
  before it sends the box its link to this machine's Orca: made with `orca pair` after the owner's yes, or the share
  link the owner makes by hand (`--pair-by-hand`, which keeps this machine on loopback). `project-setup --remove-host
  <host>` undoes this direction only.

## OpenCode on a self-hosted LiteLLM gateway
A self-hosted gateway with any models behind it is first class, and nothing is refused by model or harness (owner
2026-11-24, `core/model-routing.md`, "Sandbox per tier"); each project's own tier table says which models it uses.
0. A host set up before this change still has the old copies: `~/.agents/bin/lane-tier`, the lane skills and the
   rendered personas there refuse an OpenCode tier until they are refreshed. Update the host's lane-control checkout
   and install it (`git pull && scripts/lane-control install`), run `core/scripts/project-setup <repo> --refresh`
   from it for each project (it links the lane tools, reinstalls the skills
   and re-renders the personas), then restart the coordinator (`core/scripts/project-pause stop <repo>`, then `start <repo>`), so it
   reads the new persona. The suggestion below warns while the installed lane-tier is not the checkout's.
1. OpenCode's own config on the machine that runs the agents (`~/.config/opencode/opencode.json`) names the gateway
   as a provider, with the key as a reference to an environment variable, never the key itself:
   ```json
   { "provider": { "litellm": { "npm": "@ai-sdk/openai-compatible",
       "options": { "baseURL": "http://127.0.0.1:4000/v1", "apiKey": "{env:LITELLM_API_KEY}" },
       "models": { "chat": {}, "code": {} } } } }
   ```
   Set the variable where the agents start (the session that starts Orca). The gateway's aliases are then
   `litellm/chat` in OpenCode, a tier row of harness `opencode` and model `litellm/chat`, and
   `opencode/litellm/chat` for project-setup's `--coordinator` and `--reviewer`. A lane on such a row starts on a
   worktree made first, with `orca-ide orchestration worker-start --worktree path:<that worktree> --agent opencode
   --model litellm/chat` (the `native-orca-lanes` skill has the commands and the checks); it needs the `opencode`
   command, 2.0.14 or newer.
2. `core/scripts/project-setup <repo> --suggest-tiers litellm` reads the gateway's `/v1/models` and, where it answers
   (LiteLLM), `/model/info`, which gives the model behind each alias; it shows each alias with that model and a
   suggested table (swift, standard, deep from name hints; top `none`; rows `opencode/<provider>/<alias>`). It leaves
   out, and names, a model whose LiteLLM `model_info.mode` is not chat (an embedding model, a reranker) and, where
   OpenCode's config lists the provider's models (`provider.<id>.models`), an alias it does not list, since OpenCode
   starts only those. The base
   URL is the provider's `baseURL`, or `--gateway <url>`; the key is the variable its `{env:NAME}` names, or
   `--gateway-key-env NAME`. A key written in the config itself is never used (it stops and says so). OpenCode's auth
   files are never read, and the key is never printed, logged, stored or passed as an argument; it goes to the URL
   given only: no redirect is followed and no proxy is used. In a terminal it asks before it writes the table to the project's settings store; on no,
   or without a terminal, nothing is written: edit the table it printed and pass it with `--tiers <file>`. A later
   setup run keeps the table.
3. A machine without Claude: `core/scripts/machine-setup --machine-coordinator opencode/litellm/chat` (the
   automations follow the machine coordinator's harness; `--automation-provider opencode` says it outright). The
   three automations run OpenCode v2 on OpenCode's own default model (`"model"` in its config), since Orca passes no
   model to an OpenCode automation. With no usage feed (every budget's feed empty in the playbook config) no budget
   is metered: the caps are the limit, and night check-ins still go out.

The URL, the key's variable and the model names stay in OpenCode's config and the project's settings store: no key,
host or gateway URL in any repo.

## A machine
`core/scripts/machine-setup [--host <h>]` sets a machine up once, here or on an SSH box (its header has the details;
every step says `exists` on a re-run):
- `~/.config/lane-control/machine.conf` from `core/templates/machine.conf.example`: this machine's coordinators
  (`<name> <checkout>` each: the owner's list of whom the night manager may wake; Orca gives each one's harness and
  session name, the single agent terminal in that checkout, and a harness and terminal title written after the checkout
  override it), the runner repos it watches, night hours, idle and recheck minutes, and
  how long `lane-away on` lasts. Fill it in by hand; no value from it goes in a repo. A machine set up before
  `machine-setup` existed has no `machine.conf`: create it (run `machine-setup`, which writes it from the example, or
  copy the example there by hand), since `lane-improve` and `night-precheck` need the file.
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
  and night check-ins stay off (the night precheck says so once a night), unless no budget in the playbook config has
  a feed (only a self-hosted gateway, say): then there are no steps, the caps are the limit and check-ins go on.

The owner says they leave or return with `core/scripts/lane-away on|off|status` (or asks the machine coordinator);
`lane-away on --until-back` has no 14 h expiry, until `lane-away off`.
A coordinator gets night check-ins only when it was started with `LANE_ROLE`/`LANE_PERSONA` (project-setup does
this); one started by hand is named on the precheck's stderr instead.

Away-improve mode (optional, off by default) is a separate switch from presence, the owner's own: `core/scripts/lane-improve
on [--until HH:MM | --hours N | --until-back] [--limits "<text>"] [--deny-paths "<glob>,..."] [--project <name>]...
[--unmetered] [--continue]` cuts an `away/<date>` branch for each project (at most one per project, ever; `--continue`
carries on with it and cuts one for a project that has none, or the owner adopts or drops it first) and has each coordinator keep improving its product there until
`lane-improve off`; the coordinator's `away-improve` skill (installed with the other lane skills) runs it, and
`core/scripts/away-merge` is its only merge route. `lane-away on` (even `--until-back`), night and the budget never start it,
and it does not make the owner count as away (`docs/superpowers/specs/2026-11-23-away-improve-design.md`). `IMPROVE_IDLE_MIN` in `machine.conf` (default 30) is the
idle time after which the night manager nudges a coordinator that improve mode covers.
