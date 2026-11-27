# Core: what every orchestrator shares

These files do not depend on which orchestrator runs the agents. A project keeps them when it switches from one
orchestrator to another; everything that does depend on one is in `orchestrators/<name>/`. New here? Read
[how the parts fit](../docs/how-it-fits.md); for one line on each script, [Scripts](#scripts) below.

| File | What |
|---|---|
| `roles.md` | jobs and rules of the machine coordinator and a project coordinator |
| `model-routing.md` | tiers, routing by path map, escalation, limits, weekly and window steps, night mode, message protocol, third-party lane conventions, context protocol, cloud sessions |
| `issues.md` | beads doctrine: claim, close, parking, filing bar, weekly grooming, stale claims |
| `reliability.md` | numbered failure, fix, reuse log; entries that came from one orchestrator say which |
| `machine.md` | what is machine-specific; checks for a new box; CI runner patterns |
| `config.example.sh` | the one config the scripts read: tiers, budgets, thresholds, coordinators |
| `limits.example.sh` | a machine's own limits for the heavy-work and room gates |
| `lane-ledger.tsv` | one row per finished task, used to calibrate routing |
| `scripts/lane-heavy`, `lane-room`, `lane-tier`, `lane-limits.sh` | the machine caps: the gate for builds and full test suites, the check before opening a lane, the tier lookup, their shared limits |
| `scripts/lane-away`, `ci-presence`, `budget-state` | presence (the away file), the CI slice's limits by presence, each budget's step (`budget-state --project <name>` with that project's own ceiling) and the `budget-stopped` marker |
| `improve.py` | the ratchet (unattended improvement)'s state (`~/.config/lane-control/improve.json`), written by `lane-improve on` (and a PR number recorded by `away-merge`, and `pr`); every reader treats a missing or bad file as off |
| `scripts/lane-improve` | the ratchet's switch (`lane-control ratchet`): `on` (also opens each project's draft summary PR; optional caps `--max-items`, `--stop-at`), `off`, `drained`, `pr` (open or refresh that PR from the merge commits and `--summary-file`), `rest` (a coordinator has nothing left within the limits: idle nudges stop for 12 h), `status` (separate from `lane-away`, which is presence only); the owner's guide is `docs/away-improve.md` |
| `scripts/away-merge` | the only merge route into a project's `away/<date>` branch while improve mode is on: checks base, review marker, CI, secret scan, deny paths and the owner's gate hooks, then merges as `away: <item>` (`--local-suite-ok` takes the log of a passing full-suite run; the PR body's log entry is copied into the merge commit, a draft PR is marked ready first), records the merge for `--max-items` and refreshes the draft summary PR |
| `harnesses/opencode/lane-away-guard.js` | OpenCode's counterpart of Claude's deny rules in the ratchet: a plugin hook that refuses a PR merge and a push to the integration branch in the coordinator's shell while the mode is on or draining |
| `templates/away-*.md` | the innovation prompt, the scout and build briefs and the log entry format (`away-entry.md`, the PR body's headings) of the ratchet, installed with the `away-improve` skill (`orchestrators/native-orca/skills/away-improve`) |
| `scripts/lane-idle`; `harnesses/session-record.md` | how long a marked session has been idle, and waiting for it; the session record both read, a stable interface |
| `scripts/model-env`; `modelenv.py` | the model environment: one source, the shell file, fish file and Claude settings `env` written from it, the value-by-value `--check`, and the served-model check run before a model id is written (`tiersuggest.py` asks the gateway) |
| `scripts/night-precheck`, `lane-sweep`, `machine-check`, `machine-setup`; `machineconf.py` | native Orca's machine setup: the night manager's (with away-improve's `DUE <name> improve` and `DUE <name> wrapup` nudges), lane sweep's and machine check's prechecks, the setup itself, and the `machine.conf` reader they share |
| `scripts/window-guard.sh`, `window-guard-install` | budget orders and the low-memory notice; it reads budgets (core) but sends its orders through an orchestrator (today `cotal send`, so it needs Orca + Cotal) |
| `scripts/stale-claims.sh`, `park-check.sh`, `lib.sh` | daily beads checks and the shared config and budget helpers; the two checks run as Rove routines and send through `rove api` today (`stale-claims.sh` also reads live tasks from `rove api list`), so they need Rove (`orchestrators/rove/notes.md#issues`) |
| `templates/budget-ceiling.json`, `window-guard.service` | the weekly ceiling file and the guard's systemd unit |
| `templates/machine.conf.example`, `ci-presence.service`, `ci-presence.timer` | the machine config's example and the ci-presence user units |
| `lcroot.py` | finds the lane-control checkout by its marker file, `.lane-control-root` |
| `frontdoor.py` | the one front door's table: each `lane-control project\|machine\|improve <verb>` form, the script it runs and the arguments it puts first (every other argument goes through unchanged), and the one-line hint an old name gives a person in a terminal (never a script, an automation or a box run) |
| `hooks.py` | lifecycle hooks (`--hook` extras): runs the owner's pinned scripts at after-setup, lane-start, gate, after-merge and on-report, with facts as `LANE_HOOK_*` variables and never a secret, in an empty folder, with a timeout; shows each exit code and the last 20 lines ([how-it-fits](../docs/how-it-fits.md#hooks)) |
| `scripts/lane-extras`; `extras.py`; `agentrules.py`; `design-skills.tsv` | the owner's extras (`lane-control project\|machine extras`, `lane-control lane prepare`): skills, MCP servers (secrets as env var names only), personas, plugins, agent hooks, permission rules (beside lane-control's own denies, `agentrules.py`) and env values, per project and machine-wide, stored outside every repo; each role's only where that role's sessions read them (the coordinator's Claude extras at its launch, a lane's or reviewer's in its own worktree), written all or nothing into files lane-control owns; listed and checked by `project check` and `doctor`. The built-in design skills (`design-skills.tsv`) are the default machine extras ([how-it-fits](../docs/how-it-fits.md#extras)) |
| `install.py` | the versioned install: lays a commit's tree, switches `current`, links the lane tools, the box copy, doctor and prune |
| `boxhealth.py` | what a listening port does not prove, read-only: a tunnel unit's state, restart counter and socket owner, one Orca server runtime per host, the user manager's SHELL, lane-control paths in Orca's automations; used by `lane-control doctor` and `project-setup --check` |
| `anchor.py` | the persona anchor: the few lines in a coordinator folder's `CLAUDE.local.md` (written by `machine-setup` and `project-setup`, kept out of git) that send a session started by hand to its persona; both setups' `--check` reads it |
| `agentsdoc.py` | where generated coordination goes in a project: `AGENTS.lane-control.md` and AGENTS.md's one pointer line (`orchestrators/README.md`) |

Why shared: roles, routing, budgets, the heavy-work gate, beads and the reliability log are how we work, whatever
starts the agents. A switch carries them over unchanged (`orchestrators/switching.md`).

## Scripts

Every script in `core/scripts/`, `scripts/` and `orchestrators/*/scripts/`, one row each. How they work together:
[docs/how-it-fits.md](../docs/how-it-fits.md). Other paths in a row are links to the first one. "Run by" is who starts
it: you (owner), a coordinator, a lane (reviewers too), an automation (an Orca automation, a timer or a routine) or
setup; "library" is only read by other scripts. A **parked** script belongs to an orchestrator that is not in use.

| Script | What it does | Run by | Doc |
|---|---|---|---|
| `core/scripts/away-merge` | the only merge into a project's `away/<date>` branch, after its checks and the owner's gate hooks (after it, the after-merge hooks, which only warn) | coordinator | [away-improve](../docs/away-improve.md) |
| `core/scripts/away-watchdog` | watches each away-improve cycle: tells a stalled coordinator, with the evidence, then the machine coordinator (an escalation also runs the owner's on-report hooks, which only warn); a daily summary; merges and changes nothing | automation | [away-improve](../docs/away-improve.md#the-watchdog) |
| `core/scripts/budget-state` | prints each metered budget's step now | coordinator, owner, automation | [budget steps](model-routing.md#budget-thresholds-per-metered-budget-ceiling--the-owners-weekly-percent) |
| `core/scripts/ci-presence` | sets CI's CPU and memory limits by whether you are there | automation | [machine caps](machine.md#machine-caps) |
| `core/scripts/design-shots` | screenshots of a UI at two widths in both themes, and a before/after page | lane | [design kit](../docs/superpowers/specs/2026-11-21-design-kit-design.md) |
| `core/scripts/lane-control`, `scripts/lane-control` | the one front door: `lane-control project\|coordinator\|lane\|machine\|ratchet <verb> ...` runs the script below that form names, with every argument unchanged (`lane-control help` lists them on one screen; `core/frontdoor.py`); and installs one versioned lane-control tree per machine (`install`, `status`, `doctor`, `prune`); `doctor` also reads each tunnel unit (state, restart counter, socket owner), the Orca server runtimes, the SHELL on a box and the paths in Orca's automations, and `prune` names a flat install's leftovers | owner, setup | [the install](#the-install-one-versioned-tree-per-machine-and-per-box) |
| `core/scripts/lane-away` | tells the machine you are away or back | owner, coordinator (the machine one, on your word) | [a machine](../orchestrators/native-orca/setup.md#a-machine) |
| `core/scripts/lane-extras` | `lane-control project extras add\|list\|remove <project>`, `lane-control machine extras add\|list\|remove`, `lane-control lane prepare <project> <worktree> --role lanes\|reviewer`, `lane-control project gate <project> <PR>` and `lane-control project hook <project> <event>`: keeps the owner's extras (skills, MCP servers, personas, plugins, lifecycle hooks, agent hooks, permission rules, env values) in the project's settings store or `~/.config/lane-control/extras.json`, refusing secrets, unpinned sources and files inside a project checkout; `add` and `remove` are a person's only; `prepare` puts a lane's or reviewer's into its worktree before its worker starts and runs a lane's lane-start hooks; `gate` runs the gate hooks before a merge (exit 1: not green); `hook` runs one event's hooks (after-merge and on-report only warn) | owner, coordinator | [extras](../docs/how-it-fits.md#extras), [hooks](../docs/how-it-fits.md#hooks) |
| `core/scripts/lane-agent-hook` | `lane-agent-hook run <project root> <machine\|project> <name> <event>`: what a Claude or OpenCode session runs for one of the owner's agent hooks (never the script itself): reads the hook from the owner's store, checks it against its pin and runs a private copy with the event's JSON on stdin and no secret in its environment; a changed, missing or removed one is refused (a PreToolUse one blocks the call) | lane, coordinator | [rules, agent hooks and env](../docs/how-it-fits.md#permission-rules-agent-hooks-and-env-values) |
| `core/scripts/lane-heavy`, `scripts/lane-heavy` | runs a build or full test suite under the machine-wide limit | lane, coordinator | [machine caps](machine.md#machine-caps) |
| `core/scripts/lane-idle` | says how long a marked session has been idle, or waits for it | owner, coordinator | [session record](harnesses/session-record.md) |
| `core/scripts/lane-improve` | `lane-control ratchet on\|off\|drained\|status\|rest\|pr\|cycle`: switches the ratchet on or off and keeps its state and summary PR | owner, coordinator | [away-improve](../docs/away-improve.md) |
| `core/scripts/lane-limits.sh`, `scripts/lane-limits.sh` | works out this machine's limits for the lane tools | library | [limits example](limits.example.sh) |
| `core/scripts/lane-room`, `scripts/lane-room` | says whether there is room to open another lane | coordinator | [machine caps](machine.md#machine-caps) |
| `core/scripts/lane-spawn` | `lane-control coordinator start\|restart\|status <project>\|machine [--on <host>]` and `lane-control lane new\|list\|status <project>`: starts a coordinator only when the live check says it is not running (`--force`, a person's only, asked once and recorded, past `cannot tell`, never past `running`), restarts one only on its own `ready to stop` reply (a person's only; any other way out sends `carry on`), sends a coordinator a request for a lane by Orca message (a person's only; the coordinator confirms with the owner), and lists each lane's own worker from Orca's worker list | owner | [starting the coordinator](../docs/getting-started.md#5-start-the-coordinator-and-give-it-a-first-issue) |
| `core/scripts/lane-sweep` | reports merged, parked, stuck and broken lanes; removes some only with `--apply` | automation, owner | [lane cleanup](../docs/orca-cleanup.md) |
| `core/scripts/lane-tier`, `scripts/lane-tier` | looks a tier up in a project's tier table | coordinator, setup | [model routing](model-routing.md#tiers) |
| `core/scripts/lane-tunnel` | `lane-tunnel@<host>` (`ssh -R`, direction 2): starts, stops and checks the box's way back to the desktop; its ssh never multiplexes (`ControlPath=none`), and it runs through a link | setup, owner | [direction 2](../orchestrators/native-orca/remote.md#direction-2-the-boxs-coordinator-runs-lanes-here) |
| `core/scripts/lib.sh`, `scripts/lib.sh` | the config and budget helpers `budget-state` and the watch scripts share | library | [config example](config.example.sh) |
| `core/scripts/machine-check` | finds changes in budget step, memory, runners and coordinators; the `machine check` automation runs it by this name (it records what it finds); `lane-control machine report` runs its read-only `--peek`, and `machine report --last` its `--last` | automation | [a machine](../orchestrators/native-orca/setup.md#a-machine) |
| `core/scripts/machine-setup` | `lane-control machine setup` (its `--check` reads only): sets a machine up once, here or on a box | owner, setup | [a machine](../orchestrators/native-orca/setup.md#a-machine) |
| `core/scripts/model-env` | writes the model environment from one source; `--check` compares every place that sets it | owner | [the model environment](../orchestrators/native-orca/setup.md#the-model-environment-claude-through-a-gateway) |
| `core/scripts/night-precheck` | says which coordinators are due a night or away-improve check-in | automation | [night manager](../orchestrators/native-orca/night-manager.md) |
| `core/scripts/pairing-code-stdin.cjs` | feeds a pairing code to Orca's CLI from stdin, so it is in no command line: on a box the desktop's link (direction 2), on the desktop the box's code (`orca-host-install`) | setup | [direction 2](../orchestrators/native-orca/remote.md#direction-2-the-boxs-coordinator-runs-lanes-here) |
| `core/scripts/park-check.sh`, `scripts/park-check.sh` | **parked** (needs Rove): daily check that parked lanes can be resumed | automation | [Rove notes](../orchestrators/rove/notes.md#issues) |
| `core/scripts/project-pause`, `scripts/project-pause`, `orchestrators/orca-cotal/scripts/project-pause` | `lane-control project rest\|wake\|stop\|start\|status <project>`: rests, wakes, stops or starts a project's coordinator; `start --force [--yes]` is what `lane-control coordinator start --force` runs | owner, coordinator | [stopping](../docs/getting-started.md#stopping) |
| `core/scripts/project-setup`, `scripts/project-setup`, `orchestrators/orca-cotal/scripts/project-setup` | `lane-control project setup\|check\|refresh\|show <repo> [<host>]` and `lane-control project remove-host <host>`: sets a project up, here or on a box (direction 2's link made with `orca pair` after a yes, or by hand with `--pair-by-hand`); with `--check` lists what a box setup still needs; refreshes or removes it | owner | [native Orca setup](../orchestrators/native-orca/setup.md), [a box](../orchestrators/native-orca/remote.md) |
| `core/scripts/stale-claims.sh`, `scripts/stale-claims.sh` | **parked** (needs Rove): daily check for claimed issues with no live task | automation | [Rove notes](../orchestrators/rove/notes.md#issues) |
| `core/scripts/window-guard.sh`, `scripts/window-guard.sh` | **parked** (sends through Cotal): sends budget-window and low-memory orders | automation | [Orca + Cotal](../orchestrators/orca-cotal/docs/orca.md) |
| `core/scripts/window-guard-install`, `scripts/window-guard-install` | **parked** (with the window guard): installs it as a systemd user service | owner | [Orca + Cotal](../orchestrators/orca-cotal/docs/orca.md) |
| `core/orch.py`, `scripts/orch` | the `orch` command: a project's orchestrator status, setup, strip and switch | owner | [switching](../orchestrators/switching.md) |
| `orchestrators/orca-cotal/scripts/orca-host-install`, `scripts/orca-host-install` | puts Orca's server on a box; with `--pair` makes `orca-tunnel-<host>` (`ssh -L`, direction 1, `ControlPath=none`), stops an older build's server processes, proves one runtime and pairs it; `--tunnel` restores only that unit. **In use** by native Orca, though it sits in a parked folder | setup, owner | [direction 1](../orchestrators/native-orca/remote.md#direction-1-this-machine-drives-the-box) |
| `orchestrators/orca-cotal/scripts/cotal-agent`, `scripts/cotal-agent` | **parked** (Orca + Cotal): runs OpenCode as a Cotal agent in this terminal | setup, coordinator | [Orca + Cotal](../orchestrators/orca-cotal/docs/orca.md) |
| `orchestrators/orca-cotal/scripts/machine-coordinator-setup`, `scripts/machine-coordinator-setup` | **parked** (Orca + Cotal): creates its machine coordinator | owner | [Orca + Cotal](../orchestrators/orca-cotal/docs/orca.md) |
| `orchestrators/orca-cotal/scripts/orca-cotal-task`, `scripts/orca-cotal-task` | **parked** (Orca + Cotal): makes an Orca task for a Cotal agent | coordinator | [Orca + Cotal](../orchestrators/orca-cotal/docs/orca.md) |
| `orchestrators/rove/scripts/docs-review-launch.sh`, `scripts/docs-review-launch.sh` | **parked** (Rove): launches the weekly docs review lane | automation, owner | [Rove setup](../orchestrators/rove/setup.md) |
| `orchestrators/rove/scripts/machine-night-watch.sh`, `scripts/machine-night-watch.sh` | **parked** (Rove): hourly night watch over its coordinators | automation | [Rove notes](../orchestrators/rove/notes.md#night-mode) |
| `orchestrators/wsp/scripts/wsp-owner-code.mjs`, `scripts/wsp-owner-code.mjs` | **parked** (wsp): makes a browser pairing code with owner rights | owner | [wsp notes](../orchestrators/wsp/docs/wsp.md) |

## Paths kept for live machines

Machines, installed units and running agents call some files by their old paths. Each old path is a relative
symlink to the file's new home, so nothing on a machine has to change. Remove one only after every machine that
uses it has been updated.

| Old path | Now | Who uses it |
|---|---|---|
| `scripts/window-guard.sh` | `core/scripts/window-guard.sh` | the installed `lane-control-window-guard.service` runs it (written by `window-guard-install`) |
| `scripts/lane-heavy`, `lane-room`, `lane-tier`, `lane-limits.sh` | `core/scripts/` | docs and briefs; `project-setup` and `lane-control install` link them into `~/.agents/bin` (from the installed tree's `core/scripts/`), which project build scripts call |
| `scripts/lib.sh` | `core/scripts/lib.sh` | sourced by every watch script, wherever it is started from |
| `scripts/stale-claims.sh`, `park-check.sh` | `core/scripts/` | Rove routines (`bash <repo>/scripts/...`) |
| `scripts/window-guard-install` | `core/scripts/window-guard-install` | the install steps in the docs |
| `scripts/project-setup`, `project-pause` | `core/scripts/` (`orchestrators/orca-cotal/scripts/<name>` stays a link too) | rendered personas, `project-setup --host` and the docs; `project-setup` also reports the integration branch's protection (read only, `protection.py`) |
| `scripts/machine-coordinator-setup`, `orca-host-install`, `orca-cotal-task`, `cotal-agent` | `orchestrators/orca-cotal/scripts/` | the Orca + Cotal docs and the machine coordinator setup |
| `scripts/machine-night-watch.sh`, `docs-review-launch.sh` | `orchestrators/rove/scripts/` | Rove routines |
| `scripts/wsp-owner-code.mjs` | `orchestrators/wsp/scripts/` | the wsp notes |
| `roles.md`, `model-routing.md` | `core/` | a machine coordinator whose persona was rendered before the move reads them there |
| `model-routing.md`, `reliability.md`, `lane-ledger.tsv` | `core/` | the Rove quickstart linked them into `~/.config/rove/` |

`~/.agents/bin/lane-heavy`, `lane-limits.sh` and the other lane tools are links into the installed tree
(`~/.local/share/lane-control/current/core/scripts/`, `core/install.py`), not into a checkout: `lane-control install`
switches `current` by rename, and a running script keeps the file it opened because old trees are kept. A machine
set up before the install had copies there; its next `project-setup` run (or `lane-control install`) replaces them with
the links.

## The install: one versioned tree per machine and per box

`core/scripts/lane-control` (a link at `scripts/lane-control`, and at `~/.agents/bin/lane-control` once installed) is
also the front door (`core/frontdoor.py`): `lane-control project|machine|improve <verb>` runs the script that form
names. The install's own commands:

- `lane-control install [--version <sha|tag>] [--from <checkout>]`: `git archive` of that commit (default the
  checkout's HEAD; no network) laid at `${XDG_DATA_HOME:-~/.local/share}/lane-control/<sha>/`, then `current` switched
  to it by renaming a new link over it. Prints `installed <sha>` or `exists <sha>`. Old trees are kept.
- `lane-control status`: the current sha, the checkout's sha, and whether they differ.
- `lane-control doctor [--json] [--no-ssh]`: the installed sha against each project's (its settings store), the
  `~/.agents/bin` links, the lane skills, and each box a tree was sent to (its `current` over ssh). Exit 1 with a
  `todo:` line saying what to run for each thing out of date.
- `lane-control prune [--apply]`: the trees that are not current, that no running process of yours uses (its cwd,
  an open file or its command line) and that no project's hook or persona, user unit, plugin or `~/.agents/bin` link
  names; a dry run unless `--apply`, under the install lock, and nothing at all without a valid `current`.
- `project-setup` and `machine-setup` run from the main checkout on its default branch install its HEAD first (or use
  the tree they run from). From a lane worktree, another branch or a commit not shown to be newer than the installed one
  (older, unrelated, or a checkout that lacks the installed commit) they keep the installed tree (`lane-control: kept
  ...`) and run that tree's own script instead, so its skills and personas are rendered, not the checkout's, unless the
  owner answers yes in a terminal or `LANE_CONTROL_SWITCH=1` is set. After `lane-control install --version <older>`,
  the next setup from the main checkout at a newer commit switches back; run setups from that older tree's
  `.../current/scripts/` to stay (the tests set it, in their scratch HOME); with nothing installed that is an error. A failed install exits 1 and
  renders nothing. They render `{{LANE_CONTROL}}` and the persona hook as `.../lane-control/current`, link the lane
  tools (a link is never turned back into a copy), and record the sha in the project's settings store (`lane_control`;
  `lane_control_dirty` when the rendered text was not the installed tree's); `--show` says `lane-control <a>
  installed, project set up with <b>: run project-setup --refresh` when they differ. A box (`project-setup <repo>
  <host>`, `machine-setup --host`) is sent the same tree at the same sha into its own `.../lane-control/<sha>/`: every
  box command goes as `sh -c '...'` (a fish login shell runs it too), the box counts the entries before it writes the
  mark and switches `current` (a short stream lays nothing), and a box from before the install still runs its old
  copy until then. `--show` sends and switches nothing.
