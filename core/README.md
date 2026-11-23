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
| `improve.py` | away-improve mode's state (`~/.config/lane-control/improve.json`), written only by `lane-improve on`; every reader treats a missing or bad file as off |
| `scripts/lane-improve` | away-improve mode's switch: `on`, `off`, `drained`, `status` (separate from `lane-away`, which is presence only) |
| `scripts/away-merge` | the only merge route into a project's `away/<date>` branch while improve mode is on: checks base, review marker, CI, secret scan and deny paths, then merges as `away: <item>` |
| `harnesses/opencode/lane-away-guard.js` | OpenCode's counterpart of Claude's deny rules in away-improve mode: a plugin hook that refuses a PR merge and a push to the integration branch in the coordinator's shell while the mode is on or draining |
| `templates/away-*.md`, `templates/AWAY-LOG.md` | the innovation prompt, the scout and build briefs and the log format of away-improve mode, installed with the `away-improve` skill (`orchestrators/native-orca/skills/away-improve`) |
| `scripts/lane-idle`; `harnesses/session-record.md` | how long a marked session has been idle, and waiting for it; the session record both read, a stable interface |
| `scripts/night-precheck`, `lane-sweep`, `machine-check`, `machine-setup`; `machineconf.py` | native Orca's machine setup: the night manager's, lane sweep's and machine check's prechecks, the setup itself, and the `machine.conf` reader they share |
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
| `scripts/lane-heavy`, `lane-room`, `lane-tier`, `lane-limits.sh` | `core/scripts/` | docs and briefs; `project-setup` copies them to `~/.agents/bin` (from `core/scripts/` now), which project build scripts call |
| `scripts/lib.sh` | `core/scripts/lib.sh` | sourced by every watch script, wherever it is started from |
| `scripts/stale-claims.sh`, `park-check.sh` | `core/scripts/` | Rove routines (`bash <repo>/scripts/...`) |
| `scripts/window-guard-install` | `core/scripts/window-guard-install` | the install steps in the docs |
| `scripts/project-setup`, `project-pause` | `core/scripts/` (`orchestrators/orca-cotal/scripts/<name>` stays a link too) | rendered personas, `project-setup --host` and the docs |
| `scripts/machine-coordinator-setup`, `orca-host-install`, `orca-cotal-task`, `cotal-agent` | `orchestrators/orca-cotal/scripts/` | the Orca + Cotal docs and the machine coordinator setup |
| `scripts/machine-night-watch.sh`, `docs-review-launch.sh` | `orchestrators/rove/scripts/` | Rove routines |
| `scripts/wsp-owner-code.mjs` | `orchestrators/wsp/scripts/` | the wsp notes |
| `roles.md`, `model-routing.md` | `core/` | a machine coordinator whose persona was rendered before the move reads them there |
| `model-routing.md`, `reliability.md`, `lane-ledger.tsv` | `core/` | the Rove quickstart linked them into `~/.config/rove/` |

`~/.agents/bin/lane-heavy` and `~/.agents/bin/lane-limits.sh` are copies, not links: they keep working as they are
and are refreshed from `core/scripts/` by the next `project-setup` run.
