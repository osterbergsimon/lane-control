# Core: what every orchestrator shares

These files do not depend on which orchestrator runs the agents. A project keeps them when it switches from one
orchestrator to another; everything that does depend on one is in `orchestrators/<name>/`.

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

`core/scripts/lane-control` (a link at `scripts/lane-control`, and at `~/.agents/bin/lane-control` once installed):

- `lane-control install [--version <sha|tag>] [--from <checkout>]`: `git archive` of that commit (default the
  checkout's HEAD; no network) laid at `${XDG_DATA_HOME:-~/.local/share}/lane-control/<sha>/`, then `current` switched
  to it by renaming a new link over it. Prints `installed <sha>` or `exists <sha>`. Old trees are kept.
- `lane-control status`: the current sha, the checkout's sha, and whether they differ.
- `project-setup` and `machine-setup` run from a checkout install its HEAD first (or use the tree they run from), render
  `{{LANE_CONTROL}}` as `.../lane-control/current`, link the lane tools, and record the sha in the project's settings
  store (`lane_control`); `--show` says `lane-control <a> installed, project set up with <b>: run --refresh` when they
  differ. A box (`project-setup <repo> <host>`, `machine-setup --host`) is sent the same tree at the same sha into its
  own `.../lane-control/<sha>/` and `current`.
