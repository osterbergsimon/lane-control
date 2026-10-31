# Coordinator playbook (portable)

How projects run with **coordinators**: long-lived agent main tasks (Rove) that queue work in beads, launch worker lanes, gate and review their PRs, merge, and surface only owner decisions. Project-independent: drop this directory on a new machine, fill in `config.example.sh`, adopt it per project. Keep private details (customers, deployments, task ids) out of it.

Goal: queue work, settle decisions up front, and let coordinators run unattended (nights, then days). Only decisions that need the owner surface, as Rove gates.

## Parts
- **Owner:** sets priorities, budgets and ceilings; answers gates; says "leaving" / "back".
- **Machine coordinator (optional, one per machine):** judgment only. Budget across projects, machine resources, night mode, playbook upkeep, the owner's one report, approval of top-tier requests. Needed once two or more projects share a machine or a budget; skip it for one project.
- **Project coordinator (one per project):** the Rove main task on the repo. Queues issues, writes briefs, launches and reviews lanes, merges, raises gates.
- **Worker lanes:** Rove child tasks, one issue, one branch, one PR each, on a tier (swift, standard, deep).
- **Designers / leads:** long-lived peers of a coordinator (design, architecture). They talk to the coordinator, not to workers, and are not workers.

## Tiers (roles, not model names)
swift (cheap third-party), standard, deep, top (rare second opinion via the machine coordinator). The machine config maps each to an engine, command, model and budget; any may be "none". Everything outside `config.example.sh` speaks only of tiers.

## Files
| File | What |
|---|---|
| `roles.md` | jobs and rules of the machine coordinator and a project coordinator |
| `model-routing.md` | tiers, routing by path map, escalation, limits, thresholds, night mode, message protocol; weekly/window steps, owner-decided limits, sandboxed swift lanes, context protocol, cloud sessions |
| `issues.md` | beads doctrine: claim, close, filing bar, weekly grooming, stale-claim check |
| `reliability.md` | numbered failure → fix → reuse log; read before setting up a coordinator |
| `machine.md` | what is machine-specific; check on a new box; CI runner patterns (capped slice on a shared machine, dedicated box) |
| `config.example.sh` | the one config the scripts read: tiers, budgets, thresholds, coordinators |
| `scripts/` | `machine-watch.sh` (wakes on low memory or budget pressure), `machine-night-watch.sh` (hourly night prod), `window-guard.sh` (window and weekly-step orders; state persists across restarts), `stale-claims.sh` (daily stale-claim check), `park-check.sh` (daily parked-lane check), `docs-review-launch.sh`, `opencode-sandbox.sh` (deny-by-default sandbox for third-party swift lanes; header = contract), `test-opencode-sandbox.sh` (its proof; run from a scratch linked worktree on a non-main branch), `lib.sh` |
| `templates/` | AGENTS sections, hook settings, project settings, init script, briefs, ceiling file |
| `lane-ledger.tsv` | header only; one row per finished task, used to calibrate routing |

## Principles
1. **Truth lives in files, not in a session:** AGENTS.md, beads issues and memories, this playbook. A crashed coordinator rebuilds from them.
2. **Verify, do not trust.** The coordinator reruns every deterministic gate itself, in every workspace, and checks reviewer claims as well as worker claims.
3. **Review by what a PR touches** (tiers A/B/C); **route by the paths a task touches** (swift/standard/deep). Correctness over token cost.
4. **Decisions surface as Rove gates** with a recommendation, repeated in chat. No report sits unhandled.
5. **Deterministic checks beat model reviews** wherever a finding can be made mechanical.
6. **Budget limits how many lanes run, never the tier.**
7. **Messages are few and terse:** one per handoff, no acknowledgements.
8. **Every failure with a durable fix** goes into `reliability.md`.

## Setup: new machine
- [ ] Install Rove and beads; install the Rove skill for every engine used (`rove skill install --global --agent <engine>`).
- [ ] Read `machine.md` and check every item.
- [ ] Copy this directory somewhere stable; copy `config.example.sh` to `~/.config/rove/playbook.conf.sh` (or set `PLAYBOOK_CONFIG`) and fill it in: tiers, budgets, thresholds.
- [ ] Usage feed for every metered budget (`machine.md`); a ceiling file per metered budget from `templates/budget-ceiling.json`.
- [ ] Link `model-routing.md`, `reliability.md`, `lane-ledger.tsv` into `~/.config/rove/`.
- [ ] If you run a machine coordinator: a Rove main task on a repo holding the playbook, its instructions from `roles.md`, `scripts/machine-watch.sh` and `scripts/window-guard.sh` as its background watches (re-arm machine-watch after each wake), `scripts/machine-night-watch.sh` as an hourly overnight Rove script routine with a precheck on the mode file.
- [ ] `bash -n scripts/*.sh`, then run `machine-watch.sh` once with a low threshold to see it fire.

## Setup: new project
- [ ] `rove add <path>`; the main task is the coordinator. Add it to `COORD_TASK` in the config.
- [ ] Beads: issues and memories; queue order as dependencies; a task key per issue (`<KEY_PREFIX>-<n>` as `--external-ref`).
- [ ] AGENTS.md from `templates/AGENTS-coordination.md`: write the **path map** (deep/standard/swift), gate commands, workspaces, host-acting routes, private data dirs.
- [ ] `.claude/settings.json`-style project settings from `templates/project-settings.json`. The owner installs it: auto mode blocks self-modification.
- [ ] Coordinator hook settings (gitignored, coordinator's checkout only) from `templates/coordinator-settings.local.json`, guarded by the coordinator's task id.
- [ ] `.rove/init.sh` and the third-party engine config if the project has private data or third-party lanes. Swift lanes run through `scripts/opencode-sandbox.sh` (conventions in `model-routing.md`): copy worker rules and skills into the worktree, pin the model in `.opencode/opencode.json`, give the lane its own key; list private paths in `SANDBOX_PRIVATE_PATHS` and run `scripts/test-opencode-sandbox.sh` once.
- [ ] CI: deterministic gates in one script, on a self-hosted runner if the gate needs local data. Pin runner versions.
- [ ] Tell the machine coordinator the project's caps request (`REQ`) and priority; the owner confirms.
- [ ] Routines: a weekly docs review (`scripts/docs-review-launch.sh <PROJECT>`, brief from `templates/docs-review-brief.md`); a night watch only after a supervised pilot night.
