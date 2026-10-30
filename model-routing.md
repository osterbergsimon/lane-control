# Model routing for coordinator lanes

Shared by all project coordinators on a machine. Tiers are **roles**; the config maps them to engines and models. Each project keeps its own **path map** in its AGENTS.md; this file holds the common rules. The ledger is `lane-ledger.tsv`.

## Tiers
| Tier | Role | Engine / model / budget |
|---|---|---|
| swift | cheap third-party lane, well-scoped work | config (may be none) |
| standard | everyday lane | config |
| deep | careful lane; default reviewer from review tier B up | config |
| top | rare second opinion, only through the machine coordinator | config (may be none, e.g. a self-hosted model elsewhere) |

A tier set to "none" is skipped: route to the next tier up, and ask the owner if no tier above is configured.

## Owner inputs (example block: the owner fills this in; nothing here is a fact)
```
Date: <date>
Subscriptions / budget per tier:
  swift:    <plan or "none">, budget source <usage feed | console | manual | unlimited>
  standard: <plan>, budget source <...>
  deep:     <plan>, budget source <...>
  top:      <plan or "none">, budget source <...>
Weekly ceiling per metered budget: <percent>, set <date>, week to <date>
Priority order of projects: <A> first, then <B>, then <C>
Caps per project (workers in flight):
  <A>: up to <n> standard + <n> deep
  <B>: up to <n> standard + <n> deep
  <C>: at most <n> standard; deep only for its coordinator and design lead
Limits to watch: <window and weekly limits, dollar caps, where usage is visible>
```
Do not over-optimize on token cost: correctness and coordinator attention are the binding constraints; a failed cheap lane costs rounds and trust. The machine coordinator may hold lanes back for memory.

## Effort, reviews and second opinions
- Run the deep tier on high effort at least: do the work correctly once instead of badly twice.
- Proper reviews always; the heaviest cloud multi-agent review only when really needed: very expensive.
- Escalating to the deep tier is cheap unless the job is huge; escalating to the top tier is expensive and is not a light decision.
- Coordinators (project and machine) are the planning lanes. Workflows (multi-agent orchestration inside a lane) are allowed where a net positive.

## Routing: by paths, decided when an issue gets its task key (`lane:` label)
1. **Take the tier from the project's path map** for the paths the task will touch. The highest tier wins.
2. **deep**, whatever the paths, if the task is: a diagnosis (cause unknown); a cross-cutting refactor; a feature with subtle text or locale handling; an open design question; a follow-up of a silent-loss or false-gate-claim finding.
3. **swift** only for prose docs, statistics docs, scripts without private data, and CI. It may port or refactor existing tests but **never add test expectations** or add/edit spec statement ids. Never a task that needs private data.
4. **When in doubt, one tier up.**
5. **The budget decides how many lanes are in flight, never the tier.** Under pressure, stop launching; do not downgrade.
6. **The gate checks the tier:** a PR whose diff touches a path above its lane's tier fails (review check in CI).

## Escalation
- **One failed round on swift:** the next round runs on standard.
- **Any silent-loss finding, or a false gate claim:** the next round runs on deep, with a Tier C review.
- **Any major finding:** the next round runs one tier up.
- **Workers stop with `NEEDS_STANDARD` (from swift) or `NEEDS_DEEP` (from standard)** instead of guessing.
- **Top tier:** only when a deep-tier agent decides it needs a stronger opinion. Every request goes to the machine coordinator (format below).

## Budget thresholds (per metered budget; ceiling = the owner's weekly percent)
| Weekly use | Action |
|---|---|
| ceiling-15 | pressure notice to all coordinators (no restriction) |
| ceiling-STEP_NO_NEW (7) | no new work on that budget anywhere |
| ceiling-STEP_WIP_STOP (4) | lanes commit WIP and stop; coordinators write their handover |
| ceiling-STEP_STOP_ALL (2) | stop all lanes on it; never past the ceiling |
- Read the usage feed and ceiling before every launch on a metered budget.
- A tier with no feed (manual or unlimited) has no automatic thresholds: its caps are the limit, and the owner reports vendor usage. The scripts skip it.
- The ceiling steps are the only budget limit: no per-day or per-night share. Day or night, caps apply.

## Unattended limits
- **Wall clock per lane:** swift 2 h, standard 4 h, deep 8 h, checked by the watch routine.
- **Stall:** no push for 2 h while running gets one ping, then the lane is stopped, its WIP committed, and it is requeued one tier up.
- **In flight:** at most 3 tasks per project, at most 1 deep worker per project at night.
- **A blocked lane parks** on a Rove gate (needs owner) instead of idling or guessing.

## Night mode
- The machine coordinator runs it. The owner says "leaving" or "back" to it (or to any coordinator, which forwards it); it sets the mode file to `night` or `day`.
- At night the hourly script routine (`scripts/machine-night-watch.sh`, no tokens) revives each project coordinator's tab and prods it to check its lanes, with a launch allowance per tier from machine state: none from ceiling-STEP_NO_NEW, under the memory minimum, or with the window at its night limit; otherwise within caps. The top tier is never launched by the watch.
- Coordinators report each prod with `rove api report`; the machine coordinator reads `rove api deliveries` for the morning report.

## Message protocol
- **Lane → coordinator:** the 5-line report in `templates/AGENTS-coordination.md`, once, at the end; mid-lane only for a blocker.
  ```
  <KEY> succeeded|failed|blocked|NEEDS_STANDARD|NEEDS_DEEP: <one line>
  branch <b> · PR #<n> · ci passing|pending|failing
  gates: <command> ok|fail, ...
  deviations: none | <items>
  filed: none | <issue ids>
  ```
- **Coordinator → machine coordinator, only on events:** `REQ <project>: <slot|top|budget> — <why>` or `FYI <project>: <fact that changes arbitration>`, one line each.
- **Machine coordinator → coordinators:** `ORDER:` or `FYI:`, one line each; no reply expected.
- **Top-tier request, self-contained in one line:** `REQ <project>: top — <question>; deep tier tried: <what failed or disagreed>; scope: <files/size>`. Answer, once: `ORDER: top yes|no — <why>`. Missing "tried" means no. Note the usage jump around each consult in the ledger.
- Batch: one message per handoff, never acknowledgements. Fewer turns save more than shorter text.

## Ledger
One line per task, appended when the coordinator closes the issue.
- **Columns (TSV):** date, project, key, category, tier_start, tier_end, review, rounds, nudges, worst, note.
  - category: one of the routing lines (path-map class or deep reason).
  - worst: none, minor, major, silent-loss or false-claim.
- **Promotion:** one bad sample (2+ rounds or major) moves the category up one tier.
- **Demotion:** only after 5 clean runs with the Tier C review kept, and never below standard for anything in a deep path.
