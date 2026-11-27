# The ratchet (unattended improvement): the owner's guide

The ratchet lets each project's coordinator keep improving its own product while you are away, on one separate
branch you review when you are back. It spends your usage, so it is off unless you switch it on. This page is what
you run and what to expect; the design and its reasons are in
`docs/superpowers/specs/2026-11-23-away-improve-design.md`, and what the agents follow is the `away-improve` skill
(`orchestrators/native-orca/skills/away-improve/SKILL.md`).

The ratchet only moves forward (owner 2026-11-26). It was called away-improve mode: its files, its skill
(`away-improve`) and its script (`lane-improve`) keep that name, and `lane-control improve` still works and says the
new one.

## What it does

- After its queued work is done, a coordinator keeps starting lanes: a scout lane proposes ideas (fixes, research, new
  features, anything it judges better for the product), a build lane builds one, a reviewer on a different model
  reviews it, and the coordinator merges it into the project's away branch with `away-merge`.
- Everything lands on `away/<YYYY-MM-DD>`, cut from the project's integration branch (main or develop) plus one start
  commit, `away: start <date>`, that is empty (it adds no file). Nothing reaches the integration branch without your word,
  unless you switch on maintenance merges for docs (and, if you name them, new test files; below).
- A project has **one away branch, ever**. A second period continues the same branch; there is never a pile of
  unrelated ones.
- There is no log file on the branch. Each item's log entry (what, why, what it builds on, existing work checked, how to
  try it, how to drop it, tests) is in its build lane's PR body; `away-merge` copies it into the merge commit's message,
  and the draft summary PR is built from those messages (below).

## Switching it on and off

Improve mode is separate from presence. `lane-away on` (also `--until-back`), night, "goodnight" and a changed budget
never start it. Only `lane-control ratchet on` does, run by you or by the machine coordinator when you asked for the
improve mode in your own words.

| Command | What it does |
|---|---|
| `lane-control ratchet on [--until HH:MM\|--hours N\|--until-back] [--limits "<text>"] [--deny-paths "<glob>,..."] [--project <name>]... [--unmetered] [--stop-at <weekly %>] [--max-items N] [--max-hours H] [--maintenance [docs,tests]\|--no-maintenance] [--continue] [--yes]` |
Switches the mode on and prints what will run: projects, each branch and its draft PR, the end, your limits and caps.
Without a terminal it refuses unless `--yes`; Claude Code's `!` prefix has no terminal, so there add `--yes` (`!
lane-control ratchet on --yes ...`). |
| `lane-control ratchet off` | Ends the mode; what is running drains (below). |
| `lane-control ratchet status [--json]` | Off, on (what runs, each project's cycle), or draining (what is left to wrap up, and how long it has waited). `--json` prints one line per project: `improve` (on, wrapped, draining, off), `item`, `phase`, `round`, `pr`, `deadline`, `deadline_passed`, `last_activity_age_min`, `cap`, `branch`, `maintenance_merges`. |
| `lane-control ratchet cycle <project>` | Where the project's cycle stands (phase, item, PR, review round, the lane's dispatch, worktree and deadline). Coordinators record each step with it and read it back when they resume; you can read it any time. |
| `lane-control ratchet drained <project>...` or `--all` | A coordinator's wrap-up is done: its deny rules come out. Coordinators run this themselves. While the mode is on it takes only a project whose cap is reached. |
| `lane-control ratchet pr <project>` | Opens the project's draft summary PR if it has none, rebuilds its body from the branch's merge commits and prints the link. `--summary-file <path>` sets the short summary the coordinator writes (a file kept outside the project; later refreshes keep it). Coordinators run it at the wrap-up, also after `--max-items` is reached; `away-merge` refreshes the body after every merge. |
| `lane-control ratchet rest <project>` | A coordinator's note that nothing within your limits is left. It only quiets the idle nudges. |

Options worth knowing:

- `--limits` is free text the agents treat as binding ("nothing that needs database migrations"). `--deny-paths` are
  globs `away-merge` refuses to merge changes to, checked on the PR's diff.
- `--project` (repeatable) limits the mode to those coordinators; the default is all of them.
- `--unmetered` says this machine has no usage limits, so improve work has no budget steps (the machine's own limits,
  `lane-room` and `lane-heavy`, still apply). It does not take the wall clock away: a period with no end time gets
  `--max-hours 12` unless you give another value, and `on` says so (`--max-hours none` runs it until `lane-control
  improve off`). Otherwise each project follows its own budget steps: no new cycle from no-new-work, push and handover
  at push-and-handover, nothing at stop.
- With no end given the mode runs until `lane-control ratchet off`.

### Optional caps

All are off unless you give them (an old `--continue` re-run keeps what is set; `none` drops one), except the wall-clock
bound of an `--unmetered` period with no end (above):

- `--max-items N`: no new cycle for a project after N merges through `away-merge` in this period, counted from
  `away-merge`'s own record (`improve-period.json`), never from any text in the PRs. Each project counts its own.
- `--stop-at <weekly %>`: no new cycle for a project once `budget-state --project <name>` gives a weekly use at or above
  that value (when a project has several metered budgets, any one reaching it counts). It is refused with `--unmetered`
  (there is no budget to read) and when the value is at or above the project's ceiling (the budget steps would stop it first),
  or when the ceiling cannot be read.
- `--max-hours H`: no new cycle for a project once H hours of wall clock have passed since the period started. Unlike
  `--stop-at` it reads no budget, so it works with `--unmetered`; unlike `--hours` it is a cap, not the end: the mode stays
  on for a project that has not reached it, each project wraps up on its own, and a `--continue` re-run keeps it. Not a
  hard stop either: the lane that is running when it is reached still lands. `--stop-at` alone (checked: a weekly
  percentage from `budget-state --project`, refused with `--unmetered`) gave an unmetered period no bound but its end time.

A cap behaves like the end time, for that project alone: the lane that is running finishes and merges, the coordinator wraps
up (steps below) and runs `lane-control ratchet drained <project>`, which while the mode is on is accepted only once its cap is
reached. `--max-items` is also a hard stop in `away-merge`: the Nth merge is the last, and a further one is refused whatever
the coordinator does (a lane that is running finishes as that Nth merge). `--stop-at` is not: the lane that is running when
the weekly use reaches it still lands, and the budget's own steps are the backstop. Nothing more merges for the project once
it has wrapped up, and the mode goes on for the others (`lane-control ratchet off` then
drains only the projects still running). `lane-control ratchet status` shows `items 4/10` and `weekly 66/70` for each project and says
`cap reached` when one is hit (`weekly ?/70` when the usage feed gave no reading: not a cap hit), and `hours 3.5/12` for
`--max-hours`. The night manager sends no
improve nudge to a project whose cap is reached, a `DUE <name> wrapup ... cap=items:10/10` line instead (not at the budget's
stop step) until it has wrapped up.

A wrap-up is final for the period. To resume a project after you raise its cap, name it again: `lane-control ratchet on
--continue --project <name> --max-items <more>` (or `--stop-at`). It is refused while the cap is still reached, and a
re-run that does not name the project leaves it wrapped up and says so.

The period record (`improve-period.json`, with a lock file beside it) is written under a lock, so coordinators merging at the
same moment are all counted. `away-merge` holds that lock from the `--max-items` check through the merge and its record, and
checks the cap again once it has it, so two merges at once cannot both take the last slot (the second is refused). A merge that GitHub made but whose reply gh failed (a timeout, a 5xx) is still counted: `away-merge` looks the PR up once and records it. If it goes missing or unreadable while a cap is set, the caps fail closed: `away-merge` refuses
every merge, nothing is nudged and `status` says why, instead of the counts starting again from 0. `lane-control ratchet on
--continue` starts the record again from nothing and puts the deny rules back on every project the period covers. While the
period is draining (after `off`), `on --continue` is refused; there the refusal says to wrap up with `lane-control ratchet drained
<project>` (or `--all`) and then run `lane-control ratchet on --continue`, which starts a new period on the same away
branch (a plain
`on` is refused while the project has its branch); the last PR can merge there.

### Maintenance merges (optional, off by default)

So that small fixes to docs do not wait for you, and the away branch does not drift from main while you merge there, a
period can take **maintenance merges**: `lane-control ratchet on --maintenance` (docs), or `--maintenance docs,tests` (docs
and new test files), also as `on --continue --maintenance ...` while it runs. `--no-maintenance` turns it off again, and a
re-run keeps what is set. It is recorded in `improve.json` (`maintenance: ["docs"]`), and `status` shows `maintenance: on
(docs)`, each project's maintenance paths and its maintenance merges so far. The design is
`docs/superpowers/specs/2026-11-27-ratchet-maintenance-merges-design.md`; the rules are one table in `core/maintenance.py`.

**Be clear what this means: a maintenance merge reaches main with no human review.** An agent reviewer checked it and CI
passed, nothing more. Docs are text; new test files are code that runs in your suite and in CI, and a test PR defines the tests
that turn green, so `tests` is off unless you name it.

- **docs** (the default): content files under `docs/` and the root `README.md` and `CHANGELOG.md`: `.md`, `.markdown`,
  `.txt`, `.rst`, `.adoc`, and the images `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`. No executable, build or config file
  (`docs/conf.py`, `docs/package.json`, `docs/Makefile`, `docs/*.sh` are refused). Images are binary, so the secret and name
  scans cannot read them; any other binary file is refused.
- **tests** (only when named): NEW test files under `tests/` or `test/`, named as a test (`test_*.py`, `*_test.go`,
  `*.test.ts` and the like). Changing or deleting an existing test file is refused, and so is the suite's machinery: a
  `conftest`, `testhome`, stubs, helpers, fixtures, runners, loaders, `__init__` and shell scripts. These go to the away branch.
- **Never, whatever the kinds say:** agent instructions and harness config, at any depth and in any case (`AGENTS*.md`,
  `CLAUDE*.md`, `GEMINI*.md`, `SKILL.md`, any `.claude/`, `.opencode/`, `.agents/`, `.codex/`, `.pi/`, `skills/`,
  `personas/`, `templates/`, `prompts/` folder, `.mcp.json`, `opencode.json` and the like), CI and tooling files
  (`.github/**`, `.gitlab-ci.yml`, `.circleci/**`, `Jenkinsfile`, `.pre-commit-config.yaml`, `.gitattributes`, `.gitmodules`,
  `.gitleaks.toml` and the like), a symlink, a submodule, an executable, a name with a control character, and a path your deny
  paths or the secret and name scans refuse. One path outside and the item goes to the away branch as any item.
- **A project's own paths only narrow.** A project can set `maintenance_paths`, a list of globs, in its settings store
  (`~/.config/lane-control/projects/<folder>-<hash>/settings.json`, which `lane-control project setup --show` names; it is
  outside the repo, so no PR can widen it). A path must then match one of them as well as a kind. Each glob must start inside a
  kind (`docs/`, `tests/`, `test/`) or name `README.md` or `CHANGELOG.md`: `**`, `**/*.md` or `core/**` is refused when it is
  read, and so is every maintenance merge until it is fixed.
- **One route.** The coordinator merges it with `away-merge --maintenance --project <name> --pr <n> --item "<title>"`, never
  by hand: the deny rules and the OpenCode guard still block every other merge and push to the integration branch. Every check
  of an away merge holds (the review comment for the exact head, the entry, the secret and private-name scans, your deny
  paths, the caps, your gate hooks), with the integration branch as the base and CI green on the head (no
  `--local-suite-ok`). It also reads the files at the head sha from GitHub (the compare of the integration branch with the
  head, each with its status, so a test file is new only when GitHub says it is added; 300 files or more is refused), refuses
  a PR from an away branch, the summary PR or a fork, and reads the integration branch's protection: a required approving
  review (or code owner's review), classic or in a ruleset, is a review the coordinator cannot give, so the merge is refused
  with that reason; protection that cannot be read is refused too. Under the lock, just before the merge, the mode, the kinds,
  your deny paths and the branches must still be what the checks saw. The merge is `--match-head-commit <head>`, so a push
  after the checks refuses. It is a merge commit titled `maintenance: <item>`, its message `Pull request #<n>`,
  `Ratchet-maintenance: away/<date>` and the entry.
- **Only while the mode is on.** After `lane-control ratchet off`, or once its end time has passed, maintenance merges stop at
  once: the drain is for the running lane's last PR into the away branch, and `away-merge --maintenance` refuses.
- **A refusal for a path** names the way to the away branch: `gh pr edit <n> --base away/<date>`, then a plain `away-merge`,
  whose own checks then apply (a deny path or a private name is refused there too).
- **The take-in.** Right after the merge the away branch takes in the maintenance merge commit: a merge commit, `ratchet: take
  in main after maintenance #<n>`, pushed without force (never a rebase). It is not an item. When the merge commit is not on
  origin's main yet (or is not known and main does not hold the PR's head), a conflict, or a push the branch refuses, it stops:
  nothing is forced, `away-merge` exits 3 and says so, `status` shows the take-in still to happen, and the next maintenance
  merge is refused until the away branch holds it. That is read from git (every maintenance merge on main that names this away
  branch), so a new period does not forget it. The way out is a lane that merges main into a branch cut from the away branch,
  resolves the conflict, and goes into the away branch through `away-merge`. That PR holds everything main gained, your own
  commits too, so `away-merge` may refuse it (a deny path, a private name, a reached `--max-items`): then the coordinator tells
  you, and you merge main into the away branch yourself.
- **Counted and listed.** A maintenance merge counts toward `--max-items`, closes its cycle into the ledger, and the summary PR
  lists it in its own section, "Maintenance merges into main", with its PR link and its `git revert -m 1 <sha>` (on main), and
  says of each one, from git, whether the away branch holds it.

### One branch per project

A project that already has an `away/*` branch on origin (merged or not) is refused a second one. You then choose:

- adopt it (merge it into the integration branch, then delete the branch), or drop it (delete it), then run `on`
  again; or
- `lane-control ratchet on --continue`, which carries on with the branch where there is one and cuts one where there is
  none. `--continue` takes no argument. A project with two or more branches is refused, and they are listed.

`--continue` also works while the mode is on, to change the end, the limits or the deny paths, or to add a project: what
you do not give again stays as it was. Without `--project` the re-run keeps the period's projects exactly, cuts no
branch and installs no rule, so "two more hours" (`lane-control ratchet on --continue --hours 2`) never widens a period
you limited to some projects. `lane-control ratchet on --continue --project <name>` adds a project (one set up after the
mode started, say) and keeps the others. `away-merge` says this when it finds a project without a branch.

## Ending it, and what you get back

`lane-control ratchet off` or the end time stops new cycles. The mode is then **draining**: the lane that is running
finishes and its PR can still merge through `away-merge`, and the deny rules stay. Each coordinator then:

1. merges its last PR,
2. runs `lane-control ratchet pr <project> --summary-file <path>`: the **draft PR** from `away/<date>` into the
   integration branch (open since `lane-control ratchet on`, see below) gets its final summary,
3. sends you the summary and that PR's link as its first message,
4. runs `lane-control ratchet drained <project>`.

Look at `lane-control ratchet status` when you are back. It lists what is still to wrap up and says when a drain has
waited 12 hours or more. The night manager also nudges a coordinator that has not wrapped up (`DUE <name> wrapup`), by
day or night, so a drain nobody finished does not sit there. A new `on` is refused while a period is draining; run
`lane-control ratchet drained --all` when you want to close it yourself.

Then you adopt the whole branch (merge the draft PR as usual) or name the items to drop; the coordinator runs a lane
that reverts those merges (they are titled `away: <item>`), keeping to the dependencies in the entries.

## The summary PR

`lane-control ratchet on` opens, for each project whose away branch it cuts or continues, a **draft PR** from `away/<date>` into
the integration branch, titled `away: <date> (<project>)`, and records its number in `improve.json`. Its body is built from
the branch, not from a file on it:

- a short **summary** the coordinator writes at the wrap-up (`lane-control ratchet pr <project> --summary-file <path>`;
  what was done, what to look at first, what to drop if unsure, what was skipped). Until then it says "No summary yet".
  Later refreshes keep it;
- **Items merged**: one line per merged item (title, PR number, short sha) with its `git revert -m 1 <sha>` (or "already
  reverted" when a later commit reverts it), read from git (the first-parent merges titled `away: <item>`, not the period
  record), oldest first; revert the latest first when later items build on it;
- under the list, each item's **log entry** (what, why, builds on, existing work checked, how to try it, how to drop it,
  tests), read from its merge commit message. GitHub refuses a PR body of more than 65536 characters: when the entries do
  not fit they are shortened to their first lines, then left out, and the body says so (the whole entry stays in the
  merge commit, `git show <sha>`, and in the item's PR).

The body is generated, and **a refresh overwrites it**: the summary between the `away-summary` markers, and the items and entries
between the `away-items` markers (HTML comments, invisible on the page; each start marker holds a digest of what was last
written). `away-merge` rebuilds it after every merge (and opens it first if it was not open), under the improve lock, so
nobody has to remember to. The body is written with a REST `PATCH` of the pull request (`gh api`), because `gh pr edit` also
reads the PR's classic Projects over GraphQL and fails on an organisation that still uses them; `gh pr edit` is the fallback
when the PATCH fails. **Put your own notes in a comment on the PR, not in the body.** The body is never rewritten over an
edit: when someone has changed text outside the generated parts, inside either region (the summary, the items), or has removed
or repeated a marker, the refresh leaves the body as it is, prints one warning line (`away-merge` after the merge, which stays
done; `lane-control ratchet pr`, also with `--summary-file`, exits 1) and says how to go on: copy what you want to keep (the summary
into a file), clear the PR body, then run `lane-control ratchet pr <project> --summary-file <file>`. Line endings do not count as an
edit (a browser saves CRLF). A summary of more than 8000 characters is refused the same way. An empty body is written. A summary PR opened by hand with its own text is recorded by `on` but not rewritten. It stays a
draft: **no agent merges it, marks it ready or closes it**; you merge it (to adopt the whole branch) or name the items to
drop. A PR you closed is left alone.

GitHub opens no PR for a branch with nothing beyond its base, so `on` gives each new branch one **empty** start commit,
`away: start <date>` (an empty commit rather than none: without it the PR could not open at `on`, and `--continue` and the
summary PR need nothing else). It adds no file. The branch is the integration head plus that one commit, authored as the
checkout's configured git identity. `--continue` adds none to a branch that has one (a project added with `--continue` gets
its own, as it gets its own branch). When the commit cannot be made (the checkout has no git identity) the branch is cut from
the head alone, `on` warns, and `away-merge` opens the PR after the first merged item (`lane-control ratchet pr <project>` does it by
hand). The start commit is not an item: a branch with only it counts as never given any work when you decide to adopt or drop it.

## The item PR and its entry

Each build lane opens its PR into the away branch with the entry in its body, under these headings (template
`core/templates/away-entry.md`): What, Why, Builds on, Existing work checked, How to try it, How to drop it, Tests.
`away-merge` refuses a PR whose body lacks one (one line saying which heading is absent and which is empty), copies the
entry into the merge commit's message body after the `Pull request #<n>` line (the subject stays `away: <item>`), and, when
the PR is still a draft (lane PRs open as drafts and GitHub will not merge a draft), marks it ready first. It never touches
the summary PR. If the merge then fails, the PR stays ready, and the refusal says so.

How the entry is read: a heading is a `#` heading at the start of a line (any level, any case, an optional colon), and
headings inside a code fence (` ``` ` or `~~~`) are not headings, so a shell comment in a snippet is safe, and an entry quoted
inside a fence is not the entry. HTML comments and lines that are only a `<...>` placeholder (the unfilled template) count as
empty. The copy keeps every code fence closed. Text outside the seven sections is not copied.

What is published is checked first: the entry and the `--item` title go through the same secret scan and the same names scan
as the diff (an entry with a home path, a token or a private name is refused, naming the kind and never the text), and
`lane-control ratchet pr --summary-file` runs both on the summary before it writes anything. The entry is also pinned to the review:
if the PR body was edited after the newest `Review: passed <head sha>` comment (GitHub's `lastEditedAt` against the
comment's time), the merge is refused until the reviewer has looked again and a new comment is written; a time that cannot be
read refuses too. Bodies go to `gh` through files, not the command line, so a long or non-English body is fine.

## Limits of this change

A branch or summary PR made before the log moved into PRs is not converted. Its `AWAY-LOG.md` stays on the branch (remove it
before you adopt the branch, or it reaches the integration branch), and its PR body has no markers, so a refresh leaves it
with a warning until you clear it. No such branch exists today. The installed skill folder loses the old log
template the next time `lane-control project setup` runs.

## What keeps it inside its limits

Be clear about what is enforced and what is a backstop.

- **`away-merge` is the only merge route and it checks**: improve mode is on (or draining) for the project; the PR's
  base is exactly the project's recorded away branch (never main, develop or another project's branch); a review
  comment by the gh login running the script says `Review: passed <head sha>` for the current head (the PR body never
  counts); the PR body holds the log entry (all seven headings, filled in), which is scanned like the diff and was not edited after the review; CI is green, or `--local-suite-ok <log of the full suite run through lane-heavy>` (the log must end in a pass
  and its last line is printed; use it only when CI itself is failing for reasons outside the change); a secret scan
  (gitleaks if the project has a `.gitleaks.toml`, else a built-in scan for keys, tokens, home paths and private
  addresses); for a public repo, the names it must not carry (hostnames, usernames, project names) in the added lines and
  in the paths of added and renamed-to files, with or without gitleaks: this machine's hostname and user name, the names of the other projects
  whose repo is private, and the names in `$LANE_PRIVATE_NAMES` or `~/.config/lane-control/private-names.txt` (one per
  line, kept outside every repo). A private repo is not name-checked and the merge says so; one whose visibility
  `gh` cannot read is refused. A hit says its kind, file and line, never the name. The machine's own names are skipped
  when under 4 letters or on the common-word list (`root`, `main`, `nixos`...; the merge says so); camelCase spellings
  of a name are not caught; and your deny paths. It merges pinned to the head it checked.
- **Deny rules are a backstop, not a wall.** A Claude coordinator's session gets deny rules (uncommitted, in the
  checkout's `settings.local.json`) for merging a PR and for pushing to the integration branch; an OpenCode coordinator
  gets a guard plugin that refuses the same in its shell tool. Both read the command text, so other routes past them
  exist (`git -C <repo> push`, a script that pushes, a lane's own shell, which has no rules at all). The real guards are
  `away-merge`, the persona's rule and branch protection where the repo has it. Turn on branch protection on your
  integration branches before you rely on this. lane-control's own main and the integration branches of the owner's own
  projects now require a PR for everyone, admins included (set by the owner on 2026-11-23); `lane-control project setup`
  reports where a project stands (below).
- **Branch protection report.** `lane-control project setup` reads the project's integration branch's protection (`gh api
  repos/{owner}/{repo}/branches/<branch>/protection`, read only; it never changes a setting) and says one of: protected
  for everyone (a pull request is required, admins included, no force pushes); partly, naming what is missing (for example
  "admins are exempt", so an agent running as an admin is not stopped; a ruleset that also applies is named, but who may
  bypass it is not read); none; a ruleset applies (the rules are named; who may bypass is not read); unknown when the
  rulesets cannot be read either; not available on the repository's plan (HTTP 403, a private repository on a free plan); or
  unknown (no gh, no remote, no permission, offline). Notes follow: approvals required, deletion allowed, CI required, or CI
  that exists but is not required. Your wish is a PR for all with 0 approvals, CI where it exists and no force pushes or
  deletion.
- The coordinator's own beads backup (a push to the beads repo's `main`) is deliberately not blocked.
- The agents never switch the mode on, never merge by hand and never touch the integration branch, except a maintenance
  item through `away-merge --maintenance` when you switched maintenance merges on. A request relayed by another agent is not
  your request.

## The watchdog

A lost wake once stalled a cycle for hours at a remote site: the coordinator waited for a report that had come, and the
night manager's nudges, doubling up to 8 hours, were too slow to catch it. The `away watchdog` automation (every 15
minutes, made by `lane-control machine setup`) watches each cycle from outside. It never merges, edits a lane, starts or
stops a worker.

- **Who sends.** Its precheck, `away-watchdog`, only finds what is due and writes it to an outbox; it sends nothing and
  spends no tokens. A precheck has no Orca terminal, so Orca has no sender to take a message from (read in Orca 1.4.274's
  CLI: without `--from` it takes the one terminal in the folder, or refuses). When something is due the automation's agent
  starts and runs `away-watchdog --send`, which sends each message `--from` that agent's own terminal (Orca gives every
  terminal it starts a handle). So a message costs one short agent run; a round with nothing due costs nothing. The
  coordinator does not reply: that run has ended.
- **What it reads.** The cycle the coordinator records (`lane-control ratchet cycle`: phase, item, PR, round, the lane's dispatch,
  its other workers such as the reviewer (`--worker`), its worktree, and the lane's **deadline**, set when the lane starts:
  `--budget-min`, default 240 minutes), the last merge on the away branch, and Orca's own word on the workers
  (`orchestration worker-show` for each one recorded, `orchestration worker-list` for any worker on the lane's worktree).
  A worker counts as live only when Orca says `live` and its outcome is still in progress: a terminal that exists, a port
  that answers, or a worker Orca cannot speak for is not proof, and the message says which it was.
- **Whom it tells, and when.** Nothing live and nothing merged or recorded for `WATCHDOG_MIN` (45) minutes: a message to the
  coordinator (not in the merge phase, which waits on CI and has its deadline). A lane past its deadline, live or not: a
  message to recover it, which never puts two workers on one worktree (a fresh worker only once Orca shows every worker on
  it settled or stopped; else ask, or park the item with its findings). Both repeat while it lasts, the wait doubling (45,
  90, 180 minutes) but never longer than the lane's budget. Past `WATCHDOG_ESCALATE_MIN` (120) minutes the machine
  coordinator is told as well. Each message carries its evidence: item, PR, phase, round, what Orca said of each worker, the
  idle time and what it counts from, the deadline, and how often it has been said.
- **When it keeps quiet.** A metered project at the budget's stop step or with no reading gets nothing, and from
  no-new-work no nudge for a new cycle (as the night manager's improve nudge). A paused project (`lane-control project
  rest`) and a resting one with no lane out get nothing. After a cap, `lane-control ratchet off` or the end time, a lane
  still recorded out is watched until it reports, with no ask for a new cycle.
- **Addresses are explicit.** The coordinator's is the Orca terminal it recorded itself (`lane-control ratchet cycle` keeps
  `$ORCA_TERMINAL_HANDLE`, or `--address`); when that terminal is gone, the single agent terminal in the project's checkout.
  The machine coordinator's is `MACHINE_COORDINATOR_TO` in `machine.conf`, else the single agent terminal in
  `MACHINE_COORDINATOR_FOLDER` (`~/machine-coordinator`). A title is never matched.
- **It fails loudly.** A target it cannot address (none, several, Orca down), a send Orca refuses, a crash, or a round cut
  short by its own time limit (200 s, under the automation's 300) is an `UNADDRESSED` line; what the coordinator should have
  heard goes to the machine coordinator, and the automation's agent writes the rest in front of you (its run in Orca, and
  `notes.md` in the machine coordinator's folder), at most every `WATCHDOG_ESCALATE_MIN` minutes while the same lines stay.
  A message's backoff is written before it is sent, so a crash after a send never sends it twice.
- **The ledger.** Each closed cycle is one line in `improve-ledger.jsonl`: item, PR, merge sha, metric (`away-merge
  --metric "<before -> after>"`), review rounds, the lane's minutes, and the findings of a parked item.
- **The daily summary.** At the first round after `NIGHT_END` the machine coordinator gets the cycles merged and parked
  since the last summary and a spend proxy: cycles, review rounds and lane minutes, and the weekly use of a metered project.

What Orca does by itself, and why this is still needed: Orca tells a coordinator when one of its supervised workers goes
silent (10 minutes), idle (60 minutes) or exits. Those notices go to the coordinator, so a coordinator that is itself
stuck, or that missed the wake, hears nothing more; the watchdog is the outside view, and it runs on Orca's own automations
for the schedule. On a box, `lane-control machine setup --host` makes the same automation there; a lane that a box's coordinator
started on the desktop has not been tried with it.

## Nudges and cost

While the mode is on, the night manager nudges an idle coordinator (`DUE <name> improve`), a fallback for a lost wake.
A coordinator that stays idle is nudged less and less often (30, 60, 120 minutes, at most every 8 hours). The nudge asks
for a one-line reply, so a session write within 10 minutes of a nudge (the delivered message, the reply) is not work; a write
later than that starts the wait over. A coordinator that
runs `lane-control ratchet rest <project>` because nothing within your limits is left is left alone for 12 hours, then nudged
once to look for new work. Nudges go out by day or night; the plain check-ins still wait for you to be away.

## Files and settings

| What | Where |
|---|---|
| Mode state | `~/.config/lane-control/improve.json` (`LANE_IMPROVE_FILE`); written only by `lane-control ratchet on`. A missing, unreadable or malformed file means off. |
| Drain state | `improve-drain.json` beside it (`LANE_IMPROVE_DRAIN_FILE`) |
| Rest notes | `improve-rest.json` beside it |
| Period record | `improve-period.json` beside it: `away-merge`'s record of each merge (what `--max-items` counts) and the projects that wrapped up at a cap; it belongs to one period (its `started`), so a new period counts from nothing |
| Nudge idle time | `IMPROVE_IDLE_MIN` in `machine.conf` (default 30 minutes) |
| Cycle state | `improve-cycle.json` beside it: each project's phase, item, PR, round, lane dispatch, worktree, deadline and address (`lane-control ratchet cycle`) |
| Ledger | `improve-ledger.jsonl` beside it: one line per closed cycle |
| Watchdog | `WATCHDOG_MIN`, `WATCHDOG_ESCALATE_MIN`, `MACHINE_COORDINATOR_TO`, `MACHINE_COORDINATOR_FOLDER` in `machine.conf`; its backoff and last round in `~/.cache/lane-control/watchdog/` |
| Deny rules | each coordinator checkout's uncommitted `.claude/settings.local.json`; for OpenCode a guard plugin next to the persona plugin and a record under the user's state folder |
| An item's log entry | its PR body, then its merge commit's message on `away/<date>` (template `core/templates/away-entry.md`) |
| Maintenance rules | the kinds and the never-list in `core/maintenance.py`; a project's own narrowing, `maintenance_paths`, in its settings store (`settings.json`, outside the repo) |

## What is tested, and what is not

The watchdog, the cycle state, the ledger, `status --json`, `--max-hours` and the REST body are covered with stubs for
`orca-ide` and `gh` and temporary repositories; the stub refuses a send with no `--from`, as Orca does from a precheck.
Run for real once (Orca 1.4.274, 2026-11-26): `orchestration send --from <term> --to <term> --type status --body-file
<file>` from an Orca terminal, to that terminal itself as a scratch recipient: accepted and delivered. Not run for real:
the automation's agent running `--send` in its own run, a whole watchdog round, and the REST `PATCH` against GitHub,
including the organisation whose classic Projects made `gh pr edit` fail.

The scripts, the state handling, the merge checks, the nudges and the OpenCode guard's command checks are covered by the
test suite, using temporary repositories and stubs for `gh` and `gitleaks`. The OpenCode guard is also checked against a
real `opencode2` serving a fake model on localhost, but that test is opt-in
(`LANE_LIVE_OPENCODE=1 python3 -m unittest tests.test_opencode_guard.GuardLiveTests`) and is not run by the
suite. The summary PR, the caps and the protection report are covered the same way, with a stub `gh` that models
`pr list --head`, `pr create --draft`, `pr edit`, `pr ready` and the protection answers (everyone, partly, none, ruleset, 403, an
unreadable one), and the start commit is checked against real temporary git repositories; none of the `gh` calls has run against the real GitHub, so the first real `on` is where the PR calls and the
protection wording get their test. A real away period (scout and build lanes, reviews, `away-merge` against GitHub, the draft
PR on return) has not been run end to end; the first one is the real test. Maintenance merges are tested the same way: the
merge into main and its refusals against the stub, and the take-in (a merge, a conflict that stops) against real temporary
repositories; the compare and protection calls have not run against GitHub. Watch `lane-control ratchet status` and the summary PR, and expect to
adjust the skill from what you see.
