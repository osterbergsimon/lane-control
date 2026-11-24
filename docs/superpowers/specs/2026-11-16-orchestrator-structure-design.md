# Orchestrators as swappable parts: repo structure and a switch command (design)

Date: 2026-11-16. Status: draft, for the owner's approval.

## 1. Intent

lane-control started as the setup for one orchestrator (Rove), then grew a second (Orca + Cotal) and trial notes
for a third (wsp). On 2026-11-15 every project moved to native Orca orchestration in one day, by hand. Orchestrators
change fast: some feel good but are not mature yet and are worth testing again later.

So the repo becomes a home for *how we work with orchestrators*, not the setup for one. It should make it easy to:

- set up a project on any supported orchestrator;
- switch a project from one orchestrator to another without losing rules, work or history;
- try a new orchestrator the same way every time;
- retest a parked orchestrator later, starting from what we learned last time.

## 2. Goals, non-goals, anti-goals

Goals:
- A shared core that does not depend on any orchestrator, and one folder per orchestrator for everything that does.
- A status per orchestrator, with dated trial records.
- One evaluation checklist, used for every trial and retest, so results compare.
- One command shape (`setup`, `strip`, `switch`, `status`) with a small adapter per orchestrator.

Non-goals (this pass):
- Working adapters for parked orchestrators (Rove, wsp) or untested ones (Herdr). They get records and their kept
  scripts; an adapter is written when one is retested.
- Changing how the project repos (the three projects) work today. They run native Orca; this pass does not touch
  them. Using the command on them is a later, owner-approved step.
- New features in the shared core (budget, heavy-work gate). Those move, they do not change.

Anti-goals (a failure even if it works):
- Deleting a parked orchestrator's material. Cotal, Rove and wsp stay in the repo, parked, runnable as far as they
  were.
- A switch command that runs the risky steps on its own: saving uncommitted work, judging what an old handover says,
  stopping or starting agents, removing worktrees. Those stay with a coordinator and the owner; the command prints
  them as a checklist.
- A command that half-runs: an action an adapter lacks must fail before changing anything, with a clear message.
- Private data in the repo (it is public): no hostnames, usernames, private paths or keys.

## 3. Orchestrators and their status

| Orchestrator | What it is | Status now | Adapter this pass |
|---|---|---|---|
| native Orca | Orca worktrees and Orca's own orchestration (run, task, worker), no Cotal | in use | yes |
| Orca + Cotal | Orca worktrees, Cotal agents and Cotal messages | parked (retest when Cotal is more mature; known problems: auth, agents losing the mesh) | yes |
| Rove | Rove daemon, tasks and gates | parked (retest later) | no, record only |
| wsp | wsp host and agents | parked (trial notes only) | no, record only |
| Herdr | terminal multiplexer for coding agents | untested | no, stub only |

Statuses: `untested`, `trial`, `in use`, `parked`, `retired`. Each status change is a dated line in that
orchestrator's record, with the reason and what would make us retest.

## 4. What is shared and what is per orchestrator

Shared core (orchestrator-free; the orchestrator names come out of these files):
- roles: machine coordinator, project coordinator, reviewer, design lead (today `roles.md`, `personas/`);
- model routing and tiers (`model-routing.md`, `scripts/lane-tier`);
- budget ceiling and its steps, usage reading;
- heavy-work and room gates (`scripts/lane-heavy`, `scripts/lane-limits.sh`, `scripts/lane-room`,
  `limits.example.sh`);
- beads doctrine (`issues.md`, `scripts/park-check.sh`, `scripts/stale-claims.sh`);
- reliability log (`reliability.md`; entries tagged with the orchestrator they came from, when one);
- worker rules and brief templates that do not name an orchestrator.

Per orchestrator (in its folder):
- record: status, history, what worked, what broke, retest triggers;
- setup on a fresh machine;
- how a project is set up, how a coordinator starts and stops workers, how it talks to them;
- what a project carries for it (instruction lines, config folders, skills, settings) and so what `strip` removes;
- its scripts, templates, skills and personas' orchestrator-specific parts;
- its trial results against the shared checklist.

What must carry over in a switch (owner-confirmed): beads issues and memories; the project's own rules (AGENTS.md
minus orchestrator lines); the latest handover; the role rules; budget and heavy-work limits; all work pushed to
the remote. Everything else is rebuilt per orchestrator.

## 5. Layout (the builder may adjust names, not the split)

```
core/                 shared doctrine and scripts (section 4)
orchestrators/
  README.md           status table (section 3), how to add one, the checklist
  checklist.md        the evaluation checklist (section 7)
  switching.md        the switch guide: the human steps, in order (section 6)
  native-orca/        record.md, setup.md, adapter, templates
  orca-cotal/         record.md, docs moved from docs/orca*.md, scripts, skill, adapter
  rove/               record.md, docs/multi-machine.md parts, rove-init, Rove-only scripts
  wsp/                record.md, docs/wsp.md, its script
  herdr/              record.md (untested)
bin/ (or scripts/)    the command entry point
```

Moves keep git history (`git mv`). Paths other tools use today must keep working until their users are updated:
a project's `scripts/ci.sh` calls `~/.agents/bin/lane-heavy`, which links into this repo's `scripts/lane-heavy`. Leave
a symlink or wrapper at every old path a live machine uses, and list them in the PR.

## 6. The command

One entry point, one shape for every orchestrator:

```
<cmd> status  <project>                           which orchestrator the project is on, and what of it is present
<cmd> setup   <project> --orchestrator <name>     prepare the project for that orchestrator
<cmd> strip   <project> --orchestrator <name>     remove that orchestrator's instructions and config from the project
<cmd> switch  <project> --from <a> --to <b>       strip a, then set up b, then print the human checklist
```

Rules:
- Each adapter declares which actions it has. A missing one fails at once, before any change, naming the
  orchestrator and the action ("herdr has no adapter yet").
- `--dry-run` on every action prints what would change. Default for `strip` and `switch` is to show the plan and ask
  for confirmation.
- The command only edits files in the project's repo and its local config. It never stops or starts agents, never
  removes worktrees, never stashes, switches branches or force-pushes. Changes land on a new branch in a new
  worktree, as a PR, so the owner merges.
- `status` detects an orchestrator by what the project carries (its marker files and instruction lines, defined per
  adapter).
- `switch` ends by printing the human steps: save uncommitted work in every worktree (push to `wip/<name>`), read the
  old handover and carry what matters, stop the old coordinator, start a fresh one on the new orchestrator, confirm
  it has none of the old orchestrator's tools.
- Reuse the existing scripts (`project-setup`, `project-pause`, `orca-cotal-task` for Orca + Cotal); do not rewrite
  them.

References for the two adapters (the project repos are private; read them with `gh`, do not copy project
content into this public repo):
- `strip --orchestrator orca-cotal`: what the strip PRs removed: project A #360 and #362, project B #254 and #255,
  project C #173 and #174 (#142 is what project C's setup once added). From these, write down per adapter the marker
  files and instruction lines it owns; `status` detects by those and `strip` removes exactly those.
- `setup --orchestrator native-orca`: lane-control owns a new template for it (none exists yet). It holds the
  minimum the fresh 2026-11-15 coordinators needed: a short AGENTS.md section for a coordinator on Orca's
  orchestration (run, task, worker start, check; workers in their own worktrees), the heavy-work rule (builds and
  test suites through `lane-heavy`; project A #363 is an example of routing a project's heavy targets through it), and
  where the handovers live (beads memories `handover-latest` and the old orchestrator's dated handover). Generic
  wording only; the projects' own facts stay in their AGENTS.md.

Wrapping existing scripts (my call, see section 9):
- `orca-cotal` `setup` calls `scripts/project-setup` as it is, interactive flow and all. Because that script commits
  on its own, the "new branch, new worktree, PR" rule does not apply to it; the command says so before it runs.
  `orca-cotal` is parked, so this is for switching back, not for daily use.
- `native-orca` `setup` and every `strip` follow the PR rule.

Project and worktree handling (my call): `<project>` is a path to the project's main checkout. The command makes
its branch in a new git worktree from the remote default branch (`git worktree add`, plain git), so local changes
in the main checkout are never touched, and opens the PR with `gh`. Tests use stubs, like the existing ones in
`tests/stubs/`.

## 7. Evaluation checklist (for every trial and retest)

ORCH-1 (2026-11-15, native Orca, no Cotal) tested: setup, messages, restart, remote, cost, and the owner's view.
Its report is outside the repo (the ORCH-1 worktree's `evidence/REPORT.md`); summarise it, without private data,
as native Orca's first trial record. Combined with `docs/orca.md` section 9 and made orchestrator-neutral:
1. Install and bring-up on a fresh machine; what breaks.
2. A coordinator starts two workers, each in its own worktree and branch.
3. Coordinator and worker talk both ways; messages never disturb what the owner types; a worker report wakes the
   coordinator.
4. Nesting: a worker can start a sub-worker (reviewer), and how deep.
5. Survives: an app restart, a laptop sleep, a dropped link, an hour of idle.
6. Owner view: can the owner see state at a glance and step in.
7. Auth: agents stay signed in; no prompts on the owner's desktop.
8. Telemetry and outbound hosts.
9. Fit with the shared core: budget steps, heavy-work gate, beads, roles.

Each trial writes a dated result into the orchestrator's record, one line per point (pass, fail, partial, untested),
with evidence kept out of the public repo where it holds private data.

## 8. Constraints

- Public repo: no private data (section 2).
- Commits carry the owner's identity and no AI attribution.
- Work is done by a worker lane on a branch and PR; reviewed before merge; merged only on the owner's word.
- The existing test suite keeps passing; tests move with the scripts they cover.
- Cotal material stays (owner, 2026-11-15: "not strip cotal completely from lane-control").

## 9. Decisions made for the builder (not the owner's; the owner may overrule)

- **Core cleanup depth:** move files and take orchestrator names out of the shared files; do not rewrite doctrine or
  change what coordinators do. An orchestrator-specific passage in a shared file (for example "Under Orca + Cotal"
  in `roles.md`) moves to that orchestrator's folder, linked from where it was. Test assertions may change when
  they check moved or de-named wording or paths; an assertion that checks behaviour must keep passing unchanged.
- **Live paths:** treat every path that `machine.md`, `scripts/orca-host-install`,
  `scripts/machine-coordinator-setup`, `scripts/window-guard-install` and `scripts/lib.sh` install, link or source
  as live, plus `~/.agents/bin/lane-heavy` and `~/.agents/bin/lane-limits.sh` (two projects' build
  scripts call lane-heavy). Each gets a symlink at its old path. List them in the PR.
- **Classification:** `scripts/wsp-owner-code.mjs` goes with wsp. `cotal-agent`, `orca-cotal-task`,
  `project-setup`, `project-pause`, `machine-coordinator-setup`, `skills/orca-cotal-lanes` and the Cotal-flavoured
  templates (`AGENTS-coordination.md`, `adapt/`, `lanes.tsv`, `opencode.json`) go with Orca + Cotal.
  `machine-night-watch.sh`, `docs-review-launch.sh`, `templates/rove-init.sh` and the Rove parts of
  `multi-machine.md` go with Rove. `window-guard*` reads budgets (core) but sends orders through an orchestrator:
  core, with its sending part noted as orchestrator-specific. `lane-ledger.tsv` and `config.example.sh` stay in
  core. Where a script turns out to serve more than one orchestrator, it stays in core.
- **Records without evidence:** never invent history. Rove's and wsp's records take what the repo's docs already
  say (status, dates, problems found); checklist points nobody tested are `untested`. Herdr's record is a stub:
  status `untested`, no results.

## 10. Left to the builder

File and folder names inside section 5's split; the command's name and language (bash or Python, matching the
scripts it wraps); how adapters declare their actions; how the PR is split (moves first, then the command, is
suggested); which reliability entries get which orchestrator tag.
