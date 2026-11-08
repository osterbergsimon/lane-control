# AGENTS.md sections to adapt (Rove flow)

**Rove flow only.** The Orca flow (`project-setup`) does not paste this file into a project. It uses
`templates/adapt/` (the path map is `.lane-control/routing.md`) and `templates/lane-brief.md`. The report shape at the end
is the same 6-line shape as `templates/lane-brief.md`.

Copy into a project's AGENTS.md and replace every `<...>` (paths, components, gate commands, key prefix).
Tiers are roles (swift, standard, deep, top); the machine's config maps them to engines and models.

## Rules excerpts

- **Routing** (common rules in `model-routing.md`, results in `lane-ledger.tsv`): correctness first, do
  not over-optimize on token cost. Tier by the paths a task touches, decided when the issue gets its
  `<KEY_PREFIX>` key (deep, standard or swift), highest tier wins, in doubt one tier up; budget limits
  how many lanes run, never the tier. **Path map** (fill in):
  **deep** `<specs, parsers of untrusted input, security, data formats, fuzz targets>`; also any
  diagnosis, cross-cutting refactor, open design question or follow-up of a silent-loss finding.
  **standard** `<ordinary feature code, UI, measurements, release>`.
  **swift** `<prose docs, scripts without private data, CI config>`; tests only ported or refactored,
  never new expectations.
  A PR touching a path above its lane's tier fails review. Escalate: one failed swift round → standard;
  a silent-loss or false gate claim → deep with a Tier C review; workers stop with `NEEDS_STANDARD` /
  `NEEDS_DEEP` instead of guessing. Unattended: swift 2 h, standard 4 h, deep 8 h per lane; no push for
  2 h → ping, then stop, commit WIP, requeue one tier up; at most 1 deep worker per night.

- **Third-party lanes** (any engine that sends what it reads off the machine): simple, well-scoped work
  only. These lanes are **code only**: no task that needs private data, and the worker never opens
  `<private data dirs>` (`.rove/init.sh` does not link them for these lanes); tests use synthetic
  fixtures. Gates on private data and whole-branch reviews stay with the trusted tiers. A worker that
  finds the task needs private data or is harder than scoped stops and reports `NEEDS_STANDARD: <reason>`.

- **Host-acting code:** list here every route, script or button that acts on the host (reboot, shutdown,
  delete, network or service control). Workers never call these against a real backend; they stub them
  (e.g. in the browser test tool) and never kill processes they did not start.

- **Review tiers**, by what a PR touches, not by the model that wrote it. Every PR: the coordinator
  reruns the checks itself in every workspace (never trusts a worker's numbers; a "fails without the
  change" test is run against main), lists every new or changed spec statement and validator tolerance
  for the owner, questions files outside the issue's scope, and scans for markup sinks, unbounded reads
  and new dependencies.
  **A** docs, tests, scripts, fixtures only: that is all. **B** product or tool code (and every
  third-party lane at least): plus an independent review by the standard tier that sees the diff and the
  issue, not the worker's report. **C** formats, validators, spec statements, limits, hostile input,
  vendored parsers, security: plus an independent review by the deep tier, and spec statements approved
  by the owner before merge.

## Tasks, issues, branches

- Work runs as **Rove tasks** (Rove skill `/rove`): the coordinator is the main task on the main
  checkout; each issue is one child task with its own worktree and branch, briefed by the issue
  and this file. Names, set by the coordinator at creation
  (`rove api add --title ... --branch ...`, then `update --parent <main task>` if needed):
  every issue that becomes a task gets the next task key `<KEY_PREFIX>-<n>` (issue label `lane:<KEY_PREFIX>-<n>`
  and ` (<KEY_PREFIX>-<n>)` at the end of the issue title); task title `<KEY_PREFIX>-<n> <short title>`,
  branch `<key_prefix>-<n>-<slug>` (lower case, ≤ 40 chars); one issue, one task, one branch;
  a follow-up round on the same issue reuses the branch. Never rename a branch after its first push.
- Issues live in **beads** (`bd`; the DB may be a separate repo shared by all worktrees). Find or file
  the issue, `bd update <id> --claim`, put the id in the PR title. Issues follow `issues.md` (claim at
  launch, close at merge, follow-ups only with evidence at P2+, weekly grooming).
  Queue order is dependencies (`bd ready`). Tasks never sync beads or close the issues a merge names;
  the coordinator does, after the merge. Owner decisions and coordinator rules are bd memories.
- Work only on your task branch; commit and push it; never push to main. The coordinator gates and
  merges a finished branch on the owner's yes (or with a clean or fully evidenced gate when
  authorized), then deletes the task (`rove api delete`).
- Long runs: commit and push WIP at stopping points and keep a "Resume" note in the issue
  (`bd update <id> --notes`), so a fresh task can continue.
- **Public posts** (owner 2026-10-31): nothing goes outside the owner's own repos (`rove api feedback`,
  issues, discussions, PRs or comments on other people's repos) without the owner's approval, and it is
  worded generically: no project, host, customer, path or task-key names. `rove api feedback` publishes
  a public GitHub discussion under the owner's account.
- **Identity** (owner rule; drop this line if you want AI attribution): commits, PRs and comments carry only the owner's identity (set `git config user.name/user.email` to the owner's before the first commit, cloud sessions included); no AI attribution (no Co-Authored-By, session links or "Generated with" lines). The repo commits Claude Code settings with commit and PR attribution turned off.
- **Worker contract** (every Rove task, any engine): first `git fetch origin && git merge origin/main`;
  never `git stash` (shared across worktrees; commit instead) or switch branches; check each item of
  the brief against current code (already fixed → say so, change nothing); scoped changes only; file an
  unrelated problem with `bd create` only if it is a real defect with evidence (P2+; search first), else
  note it in the PR body. Before reporting: the "Build and test" commands for what you
  touched, in every workspace. Stop only processes you started, by the PID you saved. Commit by
  explicit path, push, open a PR to `main` (`gh pr create`, issue id in the title;
  `rove api pr-attach --url <pr>`), never merge. Last step, the bare form (no `--task-id`), with a
  heredoc, never an inline `--prompt "..."`:
  `rove api send --prompt-file - <<'EOF'` with the **report format** below; check `"delivered":true`.
  Report `failed` or `blocked` instead of guessing outside your scope.
- **Report format** (every report costs a coordinator turn). One message, at the end only; no progress,
  receipt or acknowledgement messages; mid-lane only for a blocker. At most 6 lines, no narrative, no
  file lists, no retelling of the diff; details go in the PR body or the issue notes.
  ```
  <KEY> succeeded|failed|blocked|NEEDS_STANDARD|NEEDS_DEEP: <one line>
  branch <b> · PR #<n> · ci passing|pending|failing
  gates: <command> ok|fail, ...
  deviations: none | <brief item: already fixed / deferred and why, one line each>
  filed: none | <issue ids>
  agents-md: none | <one line the next agent would need that AGENTS.md lacks; never commit it yourself>
  ```
