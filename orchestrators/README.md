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

## What lane-control writes into a project
Generated coordination goes to its own file, never into the text the project's people write (coordinator's call
2026-11-20; the owner may change it):
- `AGENTS.lane-control.md` at the repo root holds every generated section: project-setup's lane-control section and
  the native-orca adapter's section, each between its comment markers. Nobody edits it by hand.
- `AGENTS.md` gets exactly one pointer line to that file (it ends `<!-- lane-control:pointer -->`) and nothing else;
  the project's own rules stay there, untouched. A symlinked AGENTS.md gets the line in its target.
- A re-run replaces each marked section in place and never adds a second block or a second pointer.
- A generated section still in AGENTS.md (from before this file) is moved out on the next run, on the owner's yes in
  project-setup or in the PR `scripts/orch` opens; hand-written text around it stays as it was.
- Markers that do not pair, or two pointer lines, stop the run: fix them by hand, nothing is guessed.
- `strip` removes its section; when the file holds no section any more, the file and the pointer line go too.
- Each writer moves only its own markers: `scripts/orch setup --orchestrator native-orca` moves the native-orca
  section, and a lane-control section still in AGENTS.md stays there until project-setup next runs.
- A replaced or moved section that differs from what this lane-control writes (an older version or a hand edit) is
  named: project-setup says where its old text is (`git show <commit>:<file>`), and `scripts/orch` lists it in its
  PR, whose diff keeps it.

The one implementation is `core/agentsdoc.py`, used by `core/scripts/project-setup` and both adapters.

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
