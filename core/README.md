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
| `improve.py` | away-improve mode's state (`~/.config/lane-control/improve.json`), written by `lane-improve on` (and a PR number recorded by `away-merge`, and `pr`); every reader treats a missing or bad file as off |
| `scripts/lane-improve` | away-improve mode's switch: `on` (also opens each project's draft summary PR; optional caps `--max-items`, `--stop-at`), `off`, `drained`, `pr` (open or refresh that PR from the merge commits and `--summary-file`), `rest` (a coordinator has nothing left within the limits: idle nudges stop for 12 h), `status` (separate from `lane-away`, which is presence only); the owner's guide is `docs/away-improve.md` |
| `scripts/away-merge` | the only merge route into a project's `away/<date>` branch while improve mode is on: checks base, review marker, CI, secret scan and deny paths, then merges as `away: <item>` (`--local-suite-ok` takes the log of a passing full-suite run; the PR body's log entry is copied into the merge commit, a draft PR is marked ready first), records the merge for `--max-items` and refreshes the draft summary PR |
| `harnesses/opencode/lane-away-guard.js` | OpenCode's counterpart of Claude's deny rules in away-improve mode: a plugin hook that refuses a PR merge and a push to the integration branch in the coordinator's shell while the mode is on or draining |
| `templates/away-*.md` | the innovation prompt, the scout and build briefs and the log entry format (`away-entry.md`, the PR body's headings) of away-improve mode, installed with the `away-improve` skill (`orchestrators/native-orca/skills/away-improve`) |
| `scripts/lane-idle`; `harnesses/session-record.md` | how long a marked session has been idle, and waiting for it; the session record both read, a stable interface |
| `scripts/night-precheck`, `lane-sweep`, `machine-check`, `machine-setup`; `machineconf.py` | native Orca's machine setup: the night manager's (with away-improve's `DUE <name> improve` and `DUE <name> wrapup` nudges), lane sweep's and machine check's prechecks, the setup itself, and the `machine.conf` reader they share |
| `scripts/window-guard.sh`, `window-guard-install` | budget orders and the low-memory notice; it reads budgets (core) but sends its orders through an orchestrator (today `cotal send`, so it needs Orca + Cotal) |
| `scripts/stale-claims.sh`, `park-check.sh`, `lib.sh` | daily beads checks and the shared config and budget helpers; the two checks run as Rove routines and send through `rove api` today (`stale-claims.sh` also reads live tasks from `rove api list`), so they need Rove (`orchestrators/rove/notes.md#issues`) |
| `templates/budget-ceiling.json`, `window-guard.service` | the weekly ceiling file and the guard's systemd unit |
| `templates/machine.conf.example`, `ci-presence.service`, `ci-presence.timer` | the machine config's example and the ci-presence user units |
| `lcroot.py` | finds the lane-control checkout by its marker file, `.lane-control-root` |
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
| `core/scripts/away-merge` | the only merge into a project's `away/<date>` branch, after its checks | coordinator | [away-improve](../docs/away-improve.md) |
| `core/scripts/budget-state` | prints each metered budget's step now | coordinator, owner, automation | [budget steps](model-routing.md#budget-thresholds-per-metered-budget-ceiling--the-owners-weekly-percent) |
| `core/scripts/ci-presence` | sets CI's CPU and memory limits by whether you are there | automation | [machine caps](machine.md#machine-caps) |
| `core/scripts/design-shots` | screenshots of a UI at two widths in both themes, and a before/after page | lane | [design kit](../docs/superpowers/specs/2026-11-21-design-kit-design.md) |
| `core/scripts/lane-away` | tells the machine you are away or back | owner, coordinator (the machine one, on your word) | [a machine](../orchestrators/native-orca/setup.md#a-machine) |
| `core/scripts/lane-heavy`, `scripts/lane-heavy` | runs a build or full test suite under the machine-wide limit | lane, coordinator | [machine caps](machine.md#machine-caps) |
| `core/scripts/lane-idle` | says how long a marked session has been idle, or waits for it | owner, coordinator | [session record](harnesses/session-record.md) |
| `core/scripts/lane-improve` | switches away-improve mode on or off and keeps its state and summary PR | owner, coordinator | [away-improve](../docs/away-improve.md) |
| `core/scripts/lane-limits.sh`, `scripts/lane-limits.sh` | works out this machine's limits for the lane tools | library | [limits example](limits.example.sh) |
| `core/scripts/lane-room`, `scripts/lane-room` | says whether there is room to open another lane | coordinator | [machine caps](machine.md#machine-caps) |
| `core/scripts/lane-sweep` | reports merged, parked, stuck and broken lanes; removes some only with `--apply` | automation, owner | [lane cleanup](../docs/orca-cleanup.md) |
| `core/scripts/lane-tier`, `scripts/lane-tier` | looks a tier up in a project's tier table | coordinator, setup | [model routing](model-routing.md#tiers) |
| `core/scripts/lane-tunnel` | `lane-tunnel@<host>` (`ssh -R`, direction 2): starts, stops and checks the box's way back to the desktop | setup, owner | [direction 2](../orchestrators/native-orca/remote.md#direction-2-the-boxs-coordinator-runs-lanes-here) |
| `core/scripts/lib.sh`, `scripts/lib.sh` | the config and budget helpers `budget-state` and the watch scripts share | library | [config example](config.example.sh) |
| `core/scripts/machine-check` | finds changes in budget step, memory, runners and coordinators | automation | [a machine](../orchestrators/native-orca/setup.md#a-machine) |
| `core/scripts/machine-setup` | sets a machine up once, here or on a box | owner, setup | [a machine](../orchestrators/native-orca/setup.md#a-machine) |
| `core/scripts/night-precheck` | says which coordinators are due a night or away-improve check-in | automation | [night manager](../orchestrators/native-orca/night-manager.md) |
| `core/scripts/pairing-code-stdin.cjs` | on a box, feeds the share link to Orca's CLI from stdin, so it is in no command line | setup | [direction 2](../orchestrators/native-orca/remote.md#direction-2-the-boxs-coordinator-runs-lanes-here) |
| `core/scripts/park-check.sh`, `scripts/park-check.sh` | **parked** (needs Rove): daily check that parked lanes can be resumed | automation | [Rove notes](../orchestrators/rove/notes.md#issues) |
| `core/scripts/project-pause`, `scripts/project-pause`, `orchestrators/orca-cotal/scripts/project-pause` | rests, wakes, stops or starts a project's coordinator | owner, coordinator | [stopping](../docs/getting-started.md#stopping) |
| `core/scripts/project-setup`, `scripts/project-setup`, `orchestrators/orca-cotal/scripts/project-setup` | sets a project up, here or on a box; with `--check` lists what a box setup still needs; refreshes or removes it | owner | [native Orca setup](../orchestrators/native-orca/setup.md), [a box](../orchestrators/native-orca/remote.md) |
| `core/scripts/stale-claims.sh`, `scripts/stale-claims.sh` | **parked** (needs Rove): daily check for claimed issues with no live task | automation | [Rove notes](../orchestrators/rove/notes.md#issues) |
| `core/scripts/window-guard.sh`, `scripts/window-guard.sh` | **parked** (sends through Cotal): sends budget-window and low-memory orders | automation | [Orca + Cotal](../orchestrators/orca-cotal/docs/orca.md) |
| `core/scripts/window-guard-install`, `scripts/window-guard-install` | **parked** (with the window guard): installs it as a systemd user service | owner | [Orca + Cotal](../orchestrators/orca-cotal/docs/orca.md) |
| `core/orch.py`, `scripts/orch` | the `orch` command: a project's orchestrator status, setup, strip and switch | owner | [switching](../orchestrators/switching.md) |
| `orchestrators/orca-cotal/scripts/orca-host-install`, `scripts/orca-host-install` | puts Orca's server on a box; with `--pair` makes `orca-tunnel-<host>` (`ssh -L`, direction 1) and pairs it. **In use** by native Orca, though it sits in a parked folder | setup, owner | [direction 1](../orchestrators/native-orca/remote.md#direction-1-this-machine-drives-the-box) |
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
| `scripts/lane-heavy`, `lane-room`, `lane-tier`, `lane-limits.sh` | `core/scripts/` | docs and briefs; `project-setup` copies them to `~/.agents/bin` (from `core/scripts/` now), which project build scripts call |
| `scripts/lib.sh` | `core/scripts/lib.sh` | sourced by every watch script, wherever it is started from |
| `scripts/stale-claims.sh`, `park-check.sh` | `core/scripts/` | Rove routines (`bash <repo>/scripts/...`) |
| `scripts/window-guard-install` | `core/scripts/window-guard-install` | the install steps in the docs |
| `scripts/project-setup`, `project-pause` | `core/scripts/` (`orchestrators/orca-cotal/scripts/<name>` stays a link too) | rendered personas, `project-setup --host` and the docs; `project-setup` also reports the integration branch's protection (read only, `protection.py`) |
| `scripts/machine-coordinator-setup`, `orca-host-install`, `orca-cotal-task`, `cotal-agent` | `orchestrators/orca-cotal/scripts/` | the Orca + Cotal docs and the machine coordinator setup |
| `scripts/machine-night-watch.sh`, `docs-review-launch.sh` | `orchestrators/rove/scripts/` | Rove routines |
| `scripts/wsp-owner-code.mjs` | `orchestrators/wsp/scripts/` | the wsp notes |
| `roles.md`, `model-routing.md` | `core/` | a machine coordinator whose persona was rendered before the move reads them there |
| `model-routing.md`, `reliability.md`, `lane-ledger.tsv` | `core/` | the Rove quickstart linked them into `~/.config/rove/` |

`~/.agents/bin/lane-heavy` and `~/.agents/bin/lane-limits.sh` are copies, not links: they keep working as they are
and are refreshed from `core/scripts/` by the next `project-setup` run.
