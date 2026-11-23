# Away-improve mode

Status: approved by the owner 2026-11-23. A lane commits this to
`docs/superpowers/specs/2026-11-23-away-improve-design.md` with its first change.

## Why

When the owner is away and cannot use their usage limits, or the machine has no limits at all, lane-control
projects sit idle once their queue is empty: an idle coordinator waits for the owner. The owner wants that time used.
After queued work is done, each project keeps improving itself on its own (fixes, research, new features, anything
judged to make the product better), all on one separate branch that the owner reviews on return and adopts or
drops.

## What the owner decided

- **Toggled by the owner**, on when away, off on return. Optional; off by default; only ever on by an explicit
  request for this mode (never inferred from "goodnight" or "leaving").
- **At most one away branch per project, ever** (owner, 2026-11-23), so work builds on earlier work instead of a pile of
  unrelated PRs: a project that has one is refused a second until the owner continues, adopts (merges) or drops (deletes) it.
- **Strong prompting for innovation:** agents are told plainly they may and should propose and build new things.
- **No size cap.** Only the owner's limits, review and judgement bound a change.
- **Limits given when turning it on**, free text, e.g. "nothing that needs db migrations".
- **Approach A:** the project's coordinator runs it, with short-lived scout lanes for ideas.
- **It is a failure, even if it works, when:**
  - a whole area is rewritten from scratch (the owner's example: the frontend). With no size cap, a big change must
    show it is a real improvement and explain itself; "rewrite because it is cleaner" is not enough;
  - the owner cannot understand what happened;
  - work duplicates something that already exists because agents did not check.

## Goals

1. With the mode on and a project's queue empty, the coordinator keeps starting improvement lanes until the budget
   steps, the mode's end, or the owner's limits stop it.
2. Everything lands on `away/<YYYY-MM-DD>` (cut from the project's integration branch, main or develop, when the mode
   starts), never on the integration branch itself.
3. On return the owner finds a plain summary and a log they can follow, and can adopt the whole branch, or adopt it
   and drop single items.
4. No duplicated work: every item records what already exists that it checked.
5. Works with limits (budget steps) and without (an unmetered machine).

## Non-goals

- Merging anything into main or develop while the owner is away.
- New standing agents (no standing improver; the design lead was just retired).
- Choosing work across projects: each project's coordinator chooses for its own project.

## Design (coordinator's calls unless marked as the owner's)

### Turning it on and off

**Only on an explicit request (the owner, 2026-11-23: it may spend a lot of tokens).**
- It turns on only through `lane-improve on`, run by the owner, or run by the machine coordinator when the
  owner's own message explicitly asks for the improve (away-improve) mode. `lane-away on` (also `--until-back`), night, a stopped or
  raised budget, and phrases like "goodnight", "leaving" or "I'm away" never turn it on, and no agent may infer it.
- A request relayed by another agent is not enough; the agent asks the owner directly.
- On switching on, `lane-improve` prints what was turned on (projects, branch per project, end or "until back",
  limits, metered or unmetered) so the owner sees exactly what will run. Without a terminal it refuses unless
  `--yes` is given, so nothing starts it unattended by accident.
- Tests pin that `lane-away on` leaves improve off.
- **Improve is separate from presence (owner, 2026-11-23).** It has its own command, `core/scripts/lane-improve
  on|off|status|drained`; `lane-away` is presence only and refuses `--improve`. Being away does not start improve, and improve does
  not make the owner count as away: limits and the plain check-ins follow the away file and night as before, while the
  improve cycles and the improve nudge run day or night whatever presence says. `lane-away on --until-back` writes an away
  file with no expiry (until `lane-away off`) for "until I get back".

- `lane-improve on [--until <time>|--hours N|--until-back] [--limits "<text>"] [--deny-paths "<glob>,..."] [--project <name>]...
  [--unmetered] [--continue] [--yes]` writes an improve file (`~/.config/lane-control/improve.json`): start time, end (a
  time, or "until back"), limits text, which projects (all set-up projects by default), their away branches and their
  integration branches. It writes no away file.
- **Until back (the owner's requirement):** `--until-back` has no end time; the mode runs until the owner runs
  `lane-improve off`. This is the default when no end is given.
- `lane-improve off` ends the mode (the running lanes drain, see "On return"). `lane-improve status` shows the mode, its end,
  its limits and its projects, or what is still draining.
- `--unmetered` marks a machine with no usage limits: the budget steps are then off for improve work only. It may be
  combined with until-back; then only `lane-improve off`, machine limits and the owner's limits stop the work.

### The cycle (approach A)

When improve is switched on, the owner (or the machine coordinator, on the owner's word) tells each named project's
coordinator once; it finishes queued work first, then runs cycles back to back, starting each from the previous
one's end (see the calls below; the night manager is only a fallback wake).

One cycle, all in normal lanes with normal reviews:
1. **Scout lane** (short-lived, read-only, research model tier): reads the code, the issue tracker (open and
   recently closed), open and recently merged PRs, docs and specs, and the away log so far. It returns a ranked
   shortlist of 3-5 candidate improvements. For each: what and why, the gain for the product, what already exists
   nearby (with references), what it would build on from the log, rough size, and any clash with the owner's
   limits. It may include research items (a written finding or spike) as well as code.
2. **Coordinator picks** one item, preferring items that build on the branch and the open issue backlog, and writes
   why in the log.
3. **Build lane** cut from the away branch's current head, with the brief carrying the owner's limits verbatim, the
   innovation prompt, the duplicate check, and the log entry it must write. Its reviewer (a different model, as
   today) checks the limits, duplication and the "big change must justify itself" rule as well as correctness.
4. **Merge into the away branch** (the coordinator's own call, see "The branch is the coordinator's"), push the branch, close the lane.
5. Repeat until stopped.

Issues the owner already filed count as candidates and come first when they fit the limits.

### Innovation prompt (the owner: must be strong)

Scout and build briefs say plainly: you are expected to improve the product, not only fix bugs; new features,
better UX, performance, research and bold ideas are welcome; bigger changes are allowed when they are clearly better
and explain themselves; check what exists first and build on it. The exact text is a template in
`core/templates/` so it can be tuned in one place.

### The branch and the log

- `away/<YYYY-MM-DD>` per project, pushed after every merge (a backup). Public repos: the branch is public; the bar
  for every merge is tests green and nothing private (the existing public-repo rules).
- One labelled merge per item (`away: <item title>`), never squashed, so a single item can be reverted.
- `AWAY-LOG.md` at the branch root (removed when the branch is adopted): a summary at the top kept current by the
  coordinator (what was done, what is worth looking at first, what to drop if unsure), then one entry per item:
  what, why, what it builds on, what existing work it checked, how to try it, how to drop it (the merge to revert,
  and which later items depend on it), test results.
- A dependency between items is recorded in the log so a revert never surprises the owner.

### On return

`lane-improve off` ends the mode (or its end time passes) and leaves a drain state: the running lane's last PR can still merge through
`away-merge`, no new cycle starts, and each coordinator then opens the draft PR and runs `lane-improve drained <project>`. The
coordinator's first message to the owner is the log summary and a link to a
draft PR from `away/<date>` to the integration branch. The owner adopts the whole branch, or names items to drop
(the coordinator runs a lane to revert them on the branch), then merges as usual.

### Budget and limits

- With limits: improve work follows the project's budget steps as all work does (per-project ceilings, lc-mtj must
  make budget-state read them). At no-new-work no new cycle starts; at push-and-handover the branch and log are
  pushed.
- Unmetered: no budget steps for improve work, the time end is optional (until-back is allowed; then only `lane-improve off`, machine limits and the owner's limits stop it), machine limits (lane-heavy slots, CI
  slice) still apply.

## Calls made after the builder check (coordinator's calls; the owner can overturn any)

- **What drives the cycles (the owner, 2026-11-23).** The coordinator starts the next cycle itself as soon as the
  previous item is merged into the away branch (it is woken by the lane's report as usual), so the mode does not
  depend on night mode or the night manager. It stops on its own at the mode's end, a budget step, or when the owner's
  limits leave nothing to do. The night manager is only a fallback: while improve is on (day or night), a coordinator
  idle IMPROVE_IDLE_MIN (default 30) gets a `DUE <name> improve` check-in to resume, which covers a lost wake. This is
  not polling: the coordinator never loops on `check`. Night alone never starts improve work.
- **The branch is the coordinator's (the owner, 2026-11-23).** `lane-improve on` cuts `away/<YYYY-MM-DD>` per project from its
  integration branch when the project has no away branch; when it has one, it refuses unless given `--continue` (which takes no
  argument, carries on with that one branch, its log continuing, and cuts one for a project that has none, so a new project can
  join a period; two or more are refused and listed), telling the owner to continue
  it, adopt it (merge it to the integration branch) or drop it (delete it) first. The same date is fine again once the branch is gone. The coordinator owns that branch: it merges
  build lanes' PRs into it on its own, with no owner's word per item, when (1) the review passed, (2) tests are green
  (CI on the PR, or, when CI itself is failing or unavailable for reasons outside the change, the full suite through
  lane-heavy, recorded in the log), and (3) a secret and private-data scan of the diff is clean (gitleaks when the
  project has a config, otherwise lane-control's own private-data check). A small script (`core/scripts/away-merge`)
  does the merge and refuses any base that is not the project's active `away/*` branch, so the integration branch
  stays untouched; merges are merge commits titled `away: <item>`. The owner's word is still needed for anything that
  reaches the integration branch.
- **Unmetered** is per machine: `lane-improve on --unmetered` records it in improve.json; budget-state and
  night-precheck then report `unmetered` (not `unknown`), and improve check-ins are not gated by budget. Other work
  keeps its normal rules.
- **Per-project ceilings (lc-mtj)** are the first task of this work: budget-state reads the per-project ceiling the
  machine coordinator records, and takes a project argument.
- **The owner's limits** are enforced by the brief and the reviewer (LLM judgement), plus an optional mechanical
  `--deny-paths "<glob>,..."` checked by away-merge against the PR diff (e.g. `**/migrations/**`).
- **Which projects (the owner: must be limitable):** `--project <name>` (repeatable) limits the mode to those
  projects; only without any `--project` does it mean all, which then includes projects set up later. Names are the
  machine's coordinator names; an unknown name is refused when the mode is switched on. `lane-improve status` lists them.
- **Scout tier:** deep (idea quality is the point); build lanes use normal routing.
- **End:** improve ends at its own end time (none for until-back) or `lane-improve off`, independent of the away file; when it ends, the
  running cycle finishes, the log summary is updated and pushed, and no new cycle starts.

## Skill and permission rules (the owner, 2026-11-23)

- **A skill, `away-improve`,** under the native-orca skills next to `native-orca-lanes`, installed like the other
  lane-control skills for Claude and OpenCode. It holds the cycle steps, the scout brief, the build-lane brief (with
  the innovation prompt from `core/templates/` and the duplicate check), the AWAY-LOG.md format and the merge rules.
  The coordinator persona only says: when improve is on for your project, load the away-improve skill.
- **No MCP server** for now: agents already call the CLIs and scripts directly, and a server would not enforce
  anything while agents have a shell. Revisit for a harness without a shell, or for one audited action log.
- **Permission rules back up away-merge:** while improve is on, the coordinator's Claude session gets deny rules (in
  the checkout's uncommitted settings.local.json, added on and removed off) for `gh pr merge` and for `git push` to the
  integration branch, so away-merge is the only merge route. The OpenCode equivalent where its permissions allow;
  otherwise the script alone, said in the record.

## Constraints

- No merge to the integration branch, no force-push, no history rewrite on `away/*`.
- The owner's limits text is binding; when an item may clash, it is skipped and logged as skipped.
- Existing rules hold: no privileged containers, no China-region models outside the allowed project, no credentials
  touched, public-repo privacy.

## Left to the builder

- Exact file layout of improve.json and how the coordinator persona reads it.
- How the scout lane's tier and model are chosen (model-routing doc).
- The summary's wording style (plain, short, like the owner's answer card).

## Follow-ups after review (2026-11-23)

Decided while the branch was reviewed; the owner's guide (`docs/away-improve.md`) describes the result.

- **Nudges back off.** An idle coordinator is nudged when it has been idle `IMPROVE_IDLE_MIN` and the last nudge is at
  least as old as the idle time was at that nudge (30, 60, 120 minutes, capped at 8 h); a session write within 10 minutes of a nudge (the delivered message, its reply) is its reply to it, not work; a later write
  starts over. `lane-improve rest <project>` (a coordinator with nothing left within the limits) quiets a project for
  12 h; a new `on` or `off` clears it.
- **Wrap-up nudges.** After `off` or a time end, each project still to wrap up gets `DUE <name> wrapup idle=<n>min
  waited=<h>h` (by day or night, none at the stop step) until it runs `lane-improve drained`. The deny rules stay until
  then (not removed at the end time), so the rules and the last merge keep working.
- **A project set up after the mode started** joins with `lane-improve on --continue --project <name>`; `away-merge`
  names the command when it finds no branch.
- **`--local-suite-ok`** takes the path of a log of the full suite run through `lane-heavy` that ends in a pass; its last
  line is printed in the merge output.
- **Unmetered:** `budget-state --project <name>` prints `unmetered` instead of `unknown` for a project in unmetered
  improve mode when the usage feed gives no reading.
- Not done (bead lc-1dk): feed the built-in secret scan the public-repo name patterns the content test uses.

## Follow-ups after the merged mode (2026-11-23, lc-2nz)

Ordered by the owner on top of the merged mode; the owner's guide describes the result.

- **A summary PR from the start.** `lane-improve on` opens, for each branch it cuts or continues, a draft PR
  `away/<date>` -> the recorded integration branch, titled `away: <date> (<project>)`, body from the AWAY-LOG.md summary, and
  records its number in improve.json (`prs`, optional). `away-merge` refreshes the body after every merge (and opens the PR
  when `on` could not), so the coordinator remembers nothing; `lane-improve pr <project>` is the same by hand and the
  wrap-up step. The draft PR is never merged, marked ready or closed by an agent: the old "open the draft PR at off" step is
  now "update it and point the owner at it". gh writes only through these code paths (`pr create --draft`, `pr edit --body`,
  and away-merge's merge), tested with a stub gh. GitHub refuses a PR for a branch with no commits beyond its base, and a
  fresh away branch is its base's head, so at `on` the PR usually cannot open yet: that is a warning, not a refusal, and the
  first away-merge opens it. (Seeding the branch with a first commit so the PR exists from the start was weighed and left
  out: it would make the branch more than a cut of the integration head, which the one-branch rules and their tests assume.)
- **Optional caps, off by default** (the owner: a spend cap or limit could optionally be allowed). `--max-items N`: no new
  cycle for a project after N away-merges in the period, counted from away-merge's own record (`improve-period.json`, keyed
  to the period's `started`; never from the log's text). `--stop-at <weekly %>`: none once `budget-state --project` gives
  weekly at or above it (any metered budget reaching it counts); refused with `--unmetered` and when at or above the
  project's ceiling (below which the budget steps stop first). Each cap is per project, because each coordinator and each
  ceiling is. A cap behaves like the end time for that project alone: the running lane finishes and merges, the coordinator
  wraps up and runs `lane-improve drained <project>`, which while the mode is on is accepted only for a project whose cap is
  reached and then stops further merges for it (recorded in the period record, so `off` drains only the others). `status`
  shows `items 4/10` and `weekly 66/70`; night-precheck sends `DUE <name> wrapup idle=<n>min cap=<items|weekly>:<n>/<max>`
  instead of the improve nudge. A `--continue` re-run changes a cap only when given (`none` drops it).
- **Branch protection report in project-setup.** Read-only, with the integration branch's protection from `gh api`:
  protected for everyone, partly (what is missing, such as admins exempt), none, a ruleset, not available on the plan (HTTP
  403) or unknown. It never changes a setting. The guide notes it next to "a backstop, not a wall".
- Not done: seeding the away branch so the summary PR exists from `on`; a global (not per-project) item cap; a spend cap in
  money rather than the weekly budget percentage.
