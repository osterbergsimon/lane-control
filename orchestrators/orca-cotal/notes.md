# Orca + Cotal: how the shared doctrine runs on Orca + Cotal

Passages moved here from the shared files in `core/` because they only apply on Orca + Cotal. The rules themselves
are in `core/`; this file says how Orca + Cotal carries them out. Orca + Cotal is parked (`record.md`); its setup is
`docs/orca.md` and `docs/orca-cotal-setup.md`.

## Roles
From `core/roles.md`.

### Under Orca + Cotal
- One agent, `machine-coordinator`, in the Cotal space `machine`, in its own private folder (`scripts/machine-coordinator-setup`). Its notes stay in that folder because this repo is public. It sleeps until something needs judgment.
- Each project keeps its own space and Cotal has no messages between spaces, so everything crosses by shell: `cotal send -s <space> -t coordinator "machine coordinator: <order>"` to a project, `cotal send -s machine -t machine-coordinator "<project>: <line>"` back.
- An order that starts `machine coordinator:` and does not come from an agent in the coordinator's roster counts like the owner's own words, with no limit on the kind; a quoted one does not count. A decision the coordinator's persona keeps for the owner is always confirmed by the owner in that coordinator's own tab before it is acted on; a budget or window guard order is not such a decision.
- `scripts/window-guard.sh` sends the budget orders without waking this agent and sends it one notice per low-memory episode. For every project's open owner decisions it asks each coordinator.
- Escalate to the owner in chat.

Project coordinator:
- Its own agent in the project's Cotal space.
- Briefs: `templates/lane-brief.md` (installed with the skill `orca-cotal-lanes`).
- Launch: an Orca child task with one Cotal agent (the skill `orca-cotal-lanes`).
- Reports arrive as Cotal messages.
- Decisions: in the coordinator's tab (gates are not used).

## Model routing
From `core/model-routing.md`.
- A blocked lane parks by asking through its coordinator (a DM).
- After a window reset, coordinators rebuild from the Cotal inbox and roster, `orca worktree list` and `bd`.
- There is no night routine.

## Machine
From `core/machine.md`.
- [ ] Tools: `orca` and `cotal`.
- [ ] Claude Code's "← opens agents" switcher can drop a spawned lane off the Cotal mesh (cause not proven): turn it
  off as `core/machine.md` says.
