# Rove: setup

Moved from the README. Rove is parked (`record.md`): this is for retesting it. About half an hour for one project.
Everything marked **planned** is manual today.

## Requirements
Always: beads (`bd`), `gh` (authenticated), `jq`, `git`, bash, and Claude Code (or whichever engines your tiers use),
logged in. Then Rove, daemon running, with its skill installed for each engine you use
(`rove skill install --global --agent <engine>`).

## Quickstart
1. **Clone and configure.**
   ```bash
   git clone https://github.com/osterbergsimon/lane-control ~/code/lane-control
   mkdir -p ~/.config/rove
   cp ~/code/lane-control/core/config.example.sh ~/.config/rove/playbook.conf.sh
   $EDITOR ~/.config/rove/playbook.conf.sh    # tiers, budgets, thresholds, coordinators
   bash -n ~/code/lane-control/scripts/*.sh
   ```
   A budget with a usage feed needs a ceiling: copy `core/templates/budget-ceiling.json` and point `BUDGET_CEILING` at it (`core/machine.md` explains feeds). Link `core/model-routing.md`, `core/reliability.md` and `core/lane-ledger.tsv` into `~/.config/rove/` if coordinators should find them there.
2. **Start the window guard.** It sends the window and weekly-step orders and one notice per low-memory episode, by `cotal send` to the spaces in `COORD_SPACE`, so it needs the Orca + Cotal path; Rove coordinators get no orders from it. Install it as a service with `scripts/window-guard-install` (`orchestrators/orca-cotal/docs/orca.md`, "The machine coordinator and the window guard").
3. **Create the routines.** They run scripts inside the Rove daemon and spend no tokens; times are the daemon host's local time.
   ```bash
   LC=~/code/lane-control
   rove api routine-create --repo "$LC" --name "machine night watch" \
     --schedule '5 22-23,0-6 * * *' \
     --precheck 'grep -qx night "${TMPDIR:-/tmp}/coordinator-mode"' \
     --script "bash $LC/scripts/machine-night-watch.sh"
   rove api routine-create --repo "$LC" --name "beads stale-claim check" \
     --schedule '30 9 * * *' --script "bash $LC/scripts/stale-claims.sh"
   rove api routine-create --repo "$LC" --name "beads park check" \
     --schedule '35 9 * * *' --script "bash $LC/scripts/park-check.sh"
   ```
   The night watch runs only while the mode file (`MODE_FILE` in the config) says `night`: write `night` when you leave and `day` when you return. Run a supervised pilot night before leaving it unattended. An optional weekly docs review: `scripts/docs-review-launch.sh <PROJECT>` with a brief from `orchestrators/rove/templates/docs-review-brief.md`.
4. **Set up a project.**
   - `rove add <path>`: the repo's main task is its coordinator. Put its task id in `COORD_TASK` and the checkout in `COORD_REPO` in the config.
   - `bd init` in the checkout; no bd git hooks (`core/issues.md`).
   - Write the project's **path map** (which paths need which tier, and which review tier) in `.lane-control/routing.md`, from `orchestrators/orca-cotal/templates/adapt/routing.md`; gate commands, private data directories and the other project facts go in `.lane-control/project.md`. Also paste `orchestrators/rove/templates/AGENTS-coordination.md` into AGENTS.md.
   - Install `orchestrators/rove/templates/project-settings.json` as the project's agent settings, `orchestrators/rove/templates/coordinator-settings.local.json` as the coordinator's gitignored local settings (its hooks need `ROVE_TASK_ID` and `rove api context`) and `orchestrators/rove/templates/rove-init.sh` as `.rove/init.sh`.
5. **Start the coordinators.** Open the machine coordinator's main task on a repo holding your instance notes (owner decisions, caps, priorities) and have it read `README.md`, `core/roles.md`, `core/model-routing.md`, `core/reliability.md`, `orchestrators/rove/notes.md` and the config. Open each project coordinator on its repo, pointing at its `AGENTS.md`. Queue issues, then tell it to go.

**Planned:** a bootstrap script for steps 1 to 3 and the per-project files of step 4, and a doctor command that checks an install against `core/machine.md`.

## Files
`scripts/machine-night-watch.sh` (the night routine), `scripts/docs-review-launch.sh` (the optional weekly docs
review), `templates/` (the AGENTS section, project and coordinator settings, the init script, worker and docs-review
briefs), `docs/multi-machine.md` (design, not implemented: projects pinned to machines, one global coordinator over
SSH). The daily beads checks `scripts/stale-claims.sh` and `scripts/park-check.sh` are in `core/` but send through
Rove today (`notes.md`).
