# native Orca: setup

Orca's own orchestration (runs, tasks, workers, messages), with no Cotal. Status and trial results: `record.md`.
Pin the Orca version you tested: Orca ships often and its orchestration commands change (`record.md`).

## Install
- The Orca desktop app on the workstation (on Linux its CLI is `orca-ide`; plain `orca` may be another program).
  Agents run as a normal user, never root.
- Always: beads (`bd`, bd 1.0.4 or newer: `machine-setup` checks it and says how to fix it, never installs it), `gh` (authenticated), `jq`, `git`, bash, and the agent harnesses you use (Claude Code),
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
`core/scripts/project-setup <repo> <host>` sets a project up on an SSH box, driven from this machine's Orca, and lets
the box's coordinator run lanes here with the desktop's share link (`remote.md`); `project-setup --remove-host
<host>` undoes the second part.

## OpenCode on a self-hosted LiteLLM gateway
A self-hosted gateway with any models behind it is first class, and nothing is refused by model or harness (owner
2026-11-24, `core/model-routing.md`, "Sandbox per tier"); each project's own tier table says which models it uses.
0. A host set up before this change still has the old copies: `~/.agents/bin/lane-tier`, the lane skills and the
   rendered personas there refuse an OpenCode tier until they are refreshed. Update the host's lane-control checkout,
   run `core/scripts/project-setup <repo> --refresh` from it for each project (it reinstalls the lane tools and skills
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
   `opencode/litellm/chat` for project-setup's `--coordinator` and `--reviewer`.
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
   model to OpenCode. With no usage feed (every budget's feed empty in the playbook config) no budget is metered:
   the caps are the limit, and night check-ins still go out.
4. Setting a project up on such a machine: the onboarding agents run on either harness, so pass OpenCode ones,
   `core/scripts/project-setup <repo> --adapter opencode/litellm/code --adapt-reviewer opencode/litellm/deep
   --coordinator opencode/litellm/chat --reviewer opencode/litellm/deep` (the defaults stay `claude/sonnet` and
   `claude/opus`; the two are kept in the settings store, `models.adapter` and `models.adapt-reviewer`, once given, and
   a run on a default says so; only a harness a role uses must be on PATH, so a box without Claude needs no `claude`).
   Each runs `opencode2 run --standalone --model <provider>/<model> --agent lane-onboard-<random> --format json`, the
   prompt on stdin, with the same prompts, checks and review round as Claude, but in a scratch copy of the project
   (`opencode_export`, mode 700, removed after the run), not in the repo, and with OpenCode starting from nothing
   (`opencode_agent`): HOME and the XDG folders are a scratch folder of the run's own, so no config, plugin, agent,
   formatter, LSP, MCP server, auth or session of yours is loaded (an owner formatter made OpenCode run JavaScript from
   the copy after a write: seen live), and the session stays in that folder. Its one config file holds only the
   adapter's provider block, as OpenCode resolves it from all your config sources (`~/.config/opencode/` files, the file
   `OPENCODE_CONFIG` names, `OPENCODE_CONFIG_DIR`, `OPENCODE_CONFIG_CONTENT`, merged in that order): npm, name, models,
   headers, the gateway's URL written out and the key as `"apiKey": "{env:NAME}"`, never the key itself. The URL must be
   the owner's own: `options.baseURL` or the provider's `api`, an http(s) URL written in the config or in a variable it
   names (`{env:GW_URL}`); with none, or two that differ, the run stops before any key is in its environment, since an
   SDK such as `@ai-sdk/openai` or `@ai-sdk/anthropic` would otherwise send the prompt and the key to its vendor's own
   address (seen live); formatter, LSP and MCP off; and the run's agent and rules. A provider with no such block, a key
   written in the config, or none (OpenCode would take it from its auth store, which lane-control never reads) stops the
   run with one line: add `"apiKey": "{env:NAME}"` and set NAME. The run's environment is PATH, locale, TLS and proxy
   settings and the variables the block names (its key, a header's), nothing else of yours, and no model catalog is
   fetched (`OPENCODE_DISABLE_MODELS_FETCH=1`). OpenCode also loads a system config folder whatever HOME is
   (`/etc/opencode`; `/Library/Application Support/opencode` on macOS; `%ProgramData%\opencode` on Windows), with no
   switch to keep it out: when that folder exists the run refuses to start and names it. The copy is made its own empty
   git repo (no history, remote or hooks) before anything goes in, since OpenCode takes the nearest git work tree as the
   project: a copy under a TMPDIR inside some other repo (a HOME kept in git) would otherwise give the agent all of that
   repo (seen live). A TMPDIR inside the project's repo is refused. Deny rules alone did not hold on OpenCode: it
   matches the path it is asked for, never what a symlink leads to; its wildcard is not git's; and it attaches an
   `AGENTS.md` by itself, past every read rule, when it reads in or lists that folder (each seen live). So the copy
   holds only what the agents may see: the tracked files (git's index, read from the repo's root, checked-out submodules
   included, with what is on disk now) and onboarding's own new files (`.lane-control/`, `AGENTS.lane-control.md`),
   minus `.env*` files, a tracked file a `.gitignore` line matches, and everything a `--private` glob covers. The globs
   are matched by project-setup with fnmatch from the repo root, as ignore lines are (a name alone covers a folder and
   all below it, `dir/`, each `**/` as a folder or none, `[^...]`), never by git, so no `.gitignore` negation, leading
   `#` or working folder changes them; they err towards covering more (a test checks each form against git's own
   matching); a POSIX class (`[:` anywhere: `[[:digit:]]`, `[![:alpha:]]`) and a `./`, `//` or `/./` spelling are
   refused, on both harnesses, with the way to write it. A glob may not cover the adapter's files,
   `AGENTS.lane-control.md`, `AGENTS.md` or the file it links to: a review that cannot see them proves nothing, and the
   reviewer's prompt holds AGENTS.md's diff. The copy has no `.git`, no ignored file and no symlink: a link to a file
   that is itself in the copy comes as that file's content (`AGENTS.md -> CLAUDE.md`), every other link is left out, and
   so is a file below a folder that has become a link. Untracked files, untracked embedded repos and anything ignored
   are not in it; a repo committed as a gitlink is copied like a submodule, its tracked files only, also with no
   `.gitmodules` entry (mark it `--private` if it should stay out). After a run that succeeded (a failed, empty or
   cut-off one brings nothing back) only new or changed plain files at the adapter's own paths, each template as
   `.lane-control/<its name>` or `.lane-control/additions/<its name>` and `.lane-control/SUGGESTIONS.md`, and only from
   the adapter, are copied back; anything else that changed (a file elsewhere, a dot-name or `.git`, a folder of its
   own, a deletion, a link, a link or a folder in the repo where a file goes, a file in the repo the copy left out
   (ignored or private) that it would replace, any write by the reviewer) stops the run with one line, checked before
   anything is written, and nothing is copied or made; the files go beside their places first and are all put in place
   at the end, so an error leaves none (a role file over a rendered persona on native Orca becomes that role's
   additions, as on Claude). On both harnesses, any of git's own names under `.lane-control/` (`.git`, `HEAD`,
   `commondir`, `gitdir`, `objects`, `refs`, any case, folder or file) stops the run before and after each agent, before
   any git command can go in: git would run a command its config names. A second layer, the run's rules: every tool off,
   then read on, and edit only on the adapter's exact paths above, only for the adapter, so no shell, web, grep, glob,
   sub-agent or path outside the copy, for an agent whose name is new each run. The project's own OpenCode config and
   plugins are not loaded (`OPENCODE_DISABLE_PROJECT_CONFIG=1`), nor Claude Code's (`OPENCODE_DISABLE_CLAUDE_CODE=1`). A
   run that uses all its steps (80 for the adapter, 60 for the reviewer) fails as a Claude run out of turns does, and so
   does one that exits 0 with no answer and no tool call. `--refresh` runs no agent and needs no Claude either. The
   opt-in live check (real opencode2, a fake model on 127.0.0.1, every request searched for a marker in each private
   file; the owner's config there turns formatters, LSP, an MCP server, a plugin and instructions on, and the copy has
   prettier and a prettier config that would leave a marker; a proxy that lets nothing through logs any request past the
   gateway, and the provider's URL is given in `api`, split across sources, or with a vendor's SDK):
   `LANE_LIVE_OPENCODE=1 python3 -m unittest discover -s tests -p test_project_setup_native.py -k
   OpenCodeOnboardingLiveTests`.

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
