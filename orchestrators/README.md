# Orchestrators

What starts, runs and connects the agents. Each one has a folder here; what every orchestrator shares is in
`../core/`. Statuses: `untested`, `trial`, `in use`, `parked`, `retired`. Each status change is a dated line in that
orchestrator's `record.md`, with the reason and what would make us retest.

| Orchestrator | What it is | Status | Adapter |
|---|---|---|---|
| `native-orca` | Orca worktrees and Orca's own orchestration (run, task, worker), no Cotal | in use | status, setup, strip |
| `orca-cotal` | Orca worktrees, Cotal agents and Cotal messages | parked | status, setup, strip |
| `rove` | Rove daemon, tasks and gates | parked | none (record only) |
| `wsp` | wsp host and agents | parked | none (trial notes only) |
| `herdr` | terminal multiplexer for coding agents | untested | none (stub only) |

Known problems that parked Orca + Cotal: auth, agents losing the mesh (`orca-cotal/record.md`).

## A folder
- `record.md`: `Status: <status>` near the top, `## History` (dated lines), `## Retest when`, `## Trials` (results
  against `checklist.md`, one line per point).
- `setup.md`: setting it up on a fresh machine and for a project.
- `notes.md`: how it carries out the shared doctrine (roles, routing, machine, issues), moved out of `core/`.
- `adapter.py`: what `scripts/orch` needs (below). Scripts, docs, templates, skills and personas that only this
  orchestrator uses.

## The checklist and the switch guide
- `checklist.md`: the nine points every trial and retest records, and the result format.
- `switching.md`: moving a project from one orchestrator to another: the human steps, in order, around
  `scripts/orch switch`.

## Adding one
1. Make `orchestrators/<name>/` with `record.md` (`Status: untested`, a dated history line) and add a row above.
2. Add `adapter.py` with `ACTIONS = frozenset()`: every action then fails with "<name> has no <action> adapter yet".
3. Trial it with `checklist.md`; write the result into `record.md`.
4. When it is worth using, write the adapter's actions:
   - `NAME: str`, `ACTIONS: frozenset[str]` (some of `status`, `setup`, `strip`);
   - `detect(project: Path) -> list[str]`: the markers it finds (files, folders, instruction lines it owns);
   - `setup(project, work) -> list[str]` and `strip(project, work) -> list[str]`: edit files under `work` (a new
     worktree), return one line per change; an empty list means nothing to do.
   An adapter owns only the markers it writes: `strip` removes exactly those, and `status` detects by those.
