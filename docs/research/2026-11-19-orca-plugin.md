# Should lane-control ship an Orca plugin? (RES-2)

Date: 2026-11-19. Beads: lc-b3l. Research only: nothing was built, installed or turned on.

## Answer

**Not yet.** Orca's plugin system could carry a few of lane-control's jobs, and a small read-only status view
would be the first worth building. But the parts lane-control needs most are missing or not on the Orca pinned
here (1.4.250): no persona or skill shipping, no compaction event, no precheck on plugin-made automations. The API
is also growing fast. Scripts, Orca's automations CLI and Claude Code hooks already do these jobs today, so keep
them there. The section "What would change the answer" says when to look again.

## Sources

- The `orca-ide plugin` CLI help of 1.4.250 on this machine (`plugin --help`, and the `--help` of `create`,
  `types`, `enable`, `inspect`, `validate`, `release-entry`, `list`, `system on`, `dev hook`, `dev emit`), and
  `orca-ide plugin list`.
- The plugin code shipped in the 1.4.250 app bundle (`app.asar.unpacked/out/shared/plugins/`): `events/plugin-events.js`
  (events), `events/plugin-hook-payloads.js` and `contributions/plugin-hook-contribution.js` (hooks),
  `capabilities/plugin-capabilities.js`, `contributions/plugin-contribution-points.js`,
  `workers/plugin-host-api-major.js`, `manifest/plugin-schema-field-docs.js` (the "since" versions),
  `catalog/plugin-update-policy.js`, `catalog/plugin-kill-list.js`.
- Orca's docs on `main` (github.com/stablyai/orca, `docs/reference/`): `plugin-authoring.md`,
  `plugin-host-api.md` (host API 1.10), `plugin-manifest.md`, `plugin-catalog.md`, `plugin-listing-policy.md`.
- Orca releases on GitHub: 1.4.261 to 1.4.264 came out 2026-11-16 to 2026-11-19, one or more a day.
- `orca-ide automations create --help` (1.4.250), for what an automation can do without a plugin.
- In this repo: `orchestrators/native-orca/`, `core/`, `docs/superpowers/specs/`.

Note: `orca-ide plugin types` fails on this machine's build of 1.4.250 (the app bundle has no
`resources/plugins/declarative/orca.d.ts`), so the typings were read from the code and the upstream docs instead.
Anyone who builds a plugin here would hit that first.

## What Orca's plugin system is

- **A plugin** is a folder with an `orca-plugin.json` manifest (format 2), installed from a catalog, from a Git
  repository, or loaded as a development folder. It declares an **execution profile**, the lowest that covers what
  it contributes, and that decides what the owner is asked to approve:
  - `data`: themes, icons, syntaxes, translations. No approval.
  - `declarative`: commands that run Orca actions, shortcuts, VM recipes. Reviewed before it is enabled.
  - `sandboxed-ui`: panels and custom editors in a sandboxed frame, talking to Orca only through `window.orca`.
  - `trusted-worker`: a `main` that runs as a separate Node process and calls host methods its capabilities allow.
    The docs are clear that this "is not a sandbox. A worker runs as you and can read your files, saved logins and
    SSH keys like any other program."
- **Capabilities** (closed list in 1.4.250): `workspace:read`, `workspace:write`, `agent:read`, `agent:launch`,
  `terminal:send`, `terminal:read`, `notifications:show`, `storage:own`, `secrets:own`, `settings:own`. Upstream
  `main` adds `workspace:create`, `automation:read`, `automation:write`, `history:read`, `appearance:write`.
- **The host switch:** plugins are off until `orca plugin system on` (or Settings > Plugins > Turn on plugins).
  This approves nothing; each plugin that runs code still needs its own approval. On this machine the switch is
  already on: one third-party trusted-worker plugin is running and two official theme plugins are installed.

## What a plugin can hook into

| Area | What exists | Where it stops |
|---|---|---|
| UI | sidebar panels, components panels (TSX, no build step), custom editors, command palette entries, shortcuts, status bar items, settings shown under the plugin | no left-sidebar cards yet (`sidebarCards` is reserved) |
| Events (worker or panel listens) | `workspace.created`, `workspace.removed`, `workspace.changed` (focus or terminals), `agent.status.changed` (working, blocked, waiting, done; the main agent's last outcome) | no compaction, session-start, orchestration (run, task, message) or automation-run events |
| Hooks (worker decides, 2 s deadline, since 1.4.248) | `terminal.send`, `workspace.create.before/after`, `agent.launch.before` (rewrite the command, add env), `agent.prompt.before` (rewrite or refuse prompts Orca submits), `agent.tool.before/after`, `editor.save.before` | prompts a person types are never hooked; `agent.tool.before` blocks only for Claude Code, and agents on SSH or WSL hosts are not hooked yet; a timeout or crash counts as passing unless `onError: "deny"` |
| Terminals | `terminal.sendText`, `terminal.create`, `terminal.read` (local and SSH); upstream adds `terminal.list`, `terminal.focus`, `terminal.close` | |
| Agent launch | `agent.launch` (since 1.4.247); upstream adds `agent.resume`, `agents.list`, `agent.listSessions` | |
| Workspaces | `workspace.list`, `workspace.open`, `workspace.readContext`; upstream adds `workspace.create`, `project.list`, `workspace.list` with agent status | no adding a project or setting up a host |
| Automations | upstream only (host API 1.8, Orca 1.4.259): `automation.list/create/update/delete` | `automation.create` takes name, prompt, agent, project or workspace, schedule and time zone, with **no precheck**; changing an automation the plugin did not create asks the owner |
| Agent skills, MCP servers, agent lifecycle hooks, harnesses | reserved contribution points only: a manifest may list them only as empty arrays | nothing ships yet |

Orca's host API on this machine is minor 1.3 (1.4.250 sits between 1.4.247, which brought 1.3, and 1.4.251,
which brought 1.4). Everything marked "upstream" above needs Orca 1.4.256 to 1.4.260 or later.

## Install, versions, trust

- **Install:** from a catalog (Settings > Plugins > Catalog sources, a Git URL and ref), from a Git repository, or a
  development folder (`orca plugin dev load <dir>`, loaded as `local.<id>`). The upstream docs name `orca plugin
  install <owner/repo[@ref]>` and `orca plugin update`; the 1.4.250 CLI has neither. The publisher is the
  repository owner (or `local` for a folder).
- **Versions:** `engines.orca` names the oldest Orca that has every field used; `pluginApi` must be `1`. A catalog
  release pins a commit and a content hash (`orca plugin release-entry`).
- **Trust:** approval is a fingerprint bound to the plugin, version, files and access (`enable --approve <fp>`).
  An update that asks for nothing new installs and runs its new code with the access already granted; one that adds
  a capability, raises the profile or adds a command, panel or event asks again. "Update plugins automatically" is
  on by default (checks at start and every 6 hours; a catalog release must be listed 12 hours first, so a takedown
  can land). Orca keeps a kill list that revokes plugins. Workers do not get Orca's tokens or display variables,
  but they do get `SSH_AUTH_SOCK` and run as the user.

## Remote Orca servers and SSH hosts

- Plugins live per Orca host. `plugin list`, `enable` and `system on` take `--environment` or `--pairing-code`, so a
  paired server (`orca serve`) has its own plugins, its own switch and its own approvals; a paired client is
  decided by that server's plugins.
- For SSH and WSL workspaces, hooks and workers run on the desktop Orca before work is sent to the host. So one
  desktop plugin covers SSH workspaces for terminal and launch hooks, but `agent.tool.before` does not reach agents
  on SSH hosts yet.
- A headless server has no window: `plugin open` and `plugin shot` refuse it, and `shell.openExternal` and
  `files.saveAs` work only in the desktop app. A status panel is a desktop-only view.
- Remote workers were never tried in ORCH-1 (no paired server), so none of this is tested here.

## How stable it is

- The docs call plugins **Alpha**. Stable, changing only by adding: manifest format 2, capability names, execution
  profiles, host API 1 (methods, params, results, errors), `window.orca`, catalog format 2. A breaking change ships
  as a new `pluginApi` major beside the old one.
- Experimental, may change without notice: the `agent.status.changed` payload, and **the worker process model**
  ("workers may be stopped at any time"), which is where a night manager or sweep would live.
- The pace: host API went from 1.3 to 1.10 between Orca 1.4.247 and 1.4.260, and hooks arrived in 1.4.248. Orca is
  14 releases ahead of the version pinned here. It only adds within major 1, so a plugin built for 1.4.250 should
  keep loading, but each new feature lane-control wanted would mean moving the pin and retesting the
  orchestration points in `orchestrators/native-orca/record.md`.

## Item by item

| Item | What a plugin could do | Better as | Why |
|---|---|---|---|
| Personas (coordinator, design lead, reviewer, debugger, machine coordinator) | `agent.launch.before` can rewrite the launch command (for example add a system prompt flag) or add env for agents Orca starts; `agent.prompt.before` can prefix prompts Orca submits | prompts in the repo, rendered into AGENTS.md or the brief, as now | `skills` is reserved and ships nothing; rewriting a CLI command line is fragile across agents and versions; hooks fail open after 2 s |
| Re-inject personas after compaction | nothing: no compaction or session-start event, and prompts typed in the agent are never hooked | the agent's own hook (Claude Code `SessionStart`, as in `core/reliability.md`) plus the `handover-latest` memory | compaction happens inside the agent, out of Orca's sight |
| Night manager (automation with a precheck that wakes idle coordinators with pending work) | with Orca 1.4.259 or later, `automation.create` plus `agent.status.changed`, or a worker that watches status and sends a line to idle coordinators | `orca-ide automations create --precheck ...` and a script, which works on 1.4.250 today | the plugin API has no precheck, so every run would wake an agent and spend tokens; a long-running worker is the experimental part |
| Lane sweep | a worker on a timer reading `workspace.list` and `terminal.read`, sending nudges | a script run from an automation precheck, or by hand | same as above; a script is testable and shared with other orchestrators |
| Budget steps from the weekly ceiling | a status bar item showing the step, a notification at ceiling-15 and ceiling-10, `terminal.sendText` orders to coordinators | `window-guard.sh` and its unit, sending through `orca-ide terminal send` | the budget is core and must survive an orchestrator switch; a plugin would tie it to Orca. A status bar read-out of the same file is a fair later add-on |
| Heavy-work gate (`lane-heavy`) | an `agent.tool.before` guard that refuses build or test commands not run through `lane-heavy` | the script, as now | the hook covers only local Claude Code agents, not SSH or other agents, and fails open unless set to deny; a pattern guard on shell text is easy to get wrong. Worth a trial only as an extra warning |
| Project setup here and on SSH hosts | upstream `workspace.create` and `project.list`; nothing adds a project or sets up a host | `scripts/orch setup` and `project-setup --host` | setup opens PRs, copies scripts to hosts and edits AGENTS.md: work for scripts with review, not a desktop worker |
| Status view for the owner | a components panel or status bar item: workspaces and agent states (`workspace.list`, `agents.list`), with a worker reading beads or the ledger | **the best fit**, later | read-only, needs only `workspace:read` and `agent:read` (plus a worker if it reads beads), and covers what Orca's own view lacks (`record.md`, point 6) |

## Risks

- **API churn:** daily Orca releases; the features lane-control would use are 1.4.256 to 1.4.260 or newer; the
  worker model is experimental. Mitigation if built: target the pinned version, check `orca.host.has` before newer
  methods, retest on each pin move.
- **Trust:** anything useful needs `trusted-worker`, which runs as the owner with their SSH agent. Auto-update runs
  new code with old approvals, so a lane-control plugin from a catalog would need the setting off, or install from
  a local folder, and its own review on every change. `terminal:send` in a plugin is the same power as a worker
  typing into any coordinator.
- **Remote hosts:** a paired server needs its own install and approval; SSH agents are not covered by tool hooks; a
  status panel is desktop only. Not tested here.
- **Lock-in:** budget, gates and personas are shared core by design (`docs/superpowers/specs/2026-11-16-orchestrator-structure-design.md`).
  Moving them into an Orca plugin would undo the split.
- **Fail-open hooks:** a guard that times out lets the action through unless `onError: "deny"`, and a deny-on-error
  guard can block all work if the worker crashes.

## What would change the answer

- Orca on this machine pinned at 1.4.260 or later (host API 1.8+): automations, `workspace.create` and agent
  status in `workspace.list` become usable.
- `automation.create` gains a precheck (or an event when an automation run is skipped or finishes): then the night
  manager could move into a plugin without spending tokens on empty runs.
- The `skills` and agent `hooks` contribution points ship, or Orca adds a compaction or session-start event: then
  personas and re-injection could travel with Orca, for every agent and host.
- `agent.tool.before` covers SSH hosts and agents other than Claude Code: then a heavy-work guard becomes worth a
  trial.
- The worker process model is declared stable.
- The owner asks for a status view inside Orca: build that first, as a small read-only plugin loaded from a local
  folder, with no `terminal:send`.
