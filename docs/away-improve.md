# Away-improve mode: the owner's guide

Away-improve mode lets each project's coordinator keep improving its own product while you are away, on one separate
branch you review when you are back. It spends your usage, so it is off unless you switch it on. This page is what
you run and what to expect; the design and its reasons are in
`docs/superpowers/specs/2026-11-23-away-improve-design.md`, and what the agents follow is the `away-improve` skill
(`orchestrators/native-orca/skills/away-improve/SKILL.md`).

## What it does

- After its queued work is done, a coordinator keeps starting lanes: a scout lane proposes ideas (fixes, research, new
  features, anything it judges better for the product), a build lane builds one, a reviewer on a different model
  reviews it, and the coordinator merges it into the project's away branch with `away-merge`.
- Everything lands on `away/<YYYY-MM-DD>`, cut from the project's integration branch (main or develop) plus one start
  commit, `away: start <date>`, that adds the first `AWAY-LOG.md`. Nothing reaches the integration branch without your word.
- A project has **one away branch, ever**. A second period continues the same branch; there is never a pile of
  unrelated ones.
- The coordinator keeps `AWAY-LOG.md` on the branch: each item, why it was picked, what it checked already existed, how
  it was tested, and what to revert it.

## Switching it on and off

Improve mode is separate from presence. `lane-away on` (also `--until-back`), night, "goodnight" and a changed budget
never start it. Only `lane-improve on` does, run by you or by the machine coordinator when you asked for the improve
mode in your own words.

| Command | What it does |
|---|---|
| `lane-improve on [--until HH:MM\|--hours N\|--until-back] [--limits "<text>"] [--deny-paths "<glob>,..."] [--project <name>]... [--unmetered] [--stop-at <weekly %>] [--max-items N] [--continue] [--yes]` | Switches the mode on and prints what will run: projects, each branch and its draft PR, the end, your limits and caps. Without a terminal it refuses unless `--yes`; Claude Code's `!` prefix has no terminal, so there add `--yes` (`! lane-improve on --yes ...`). |
| `lane-improve off` | Ends the mode; what is running drains (below). |
| `lane-improve status` | Off, on (what runs), or draining (what is left to wrap up, and how long it has waited). |
| `lane-improve drained <project>...` or `--all` | A coordinator's wrap-up is done: its deny rules come out. Coordinators run this themselves. While the mode is on it takes only a project whose cap is reached. |
| `lane-improve pr <project>` | Opens the project's draft summary PR if it has none, brings its body up to the log's summary and prints the link. Coordinators run it at the wrap-up; `away-merge` does the same after every merge. |
| `lane-improve rest <project>` | A coordinator's note that nothing within your limits is left. It only quiets the idle nudges. |

Options worth knowing:

- `--limits` is free text the agents treat as binding ("nothing that needs database migrations"). `--deny-paths` are
  globs `away-merge` refuses to merge changes to, checked on the PR's diff.
- `--project` (repeatable) limits the mode to those coordinators; the default is all of them.
- `--unmetered` says this machine has no usage limits, so improve work has no budget steps (the machine's own limits,
  `lane-room` and `lane-heavy`, still apply). Otherwise each project follows its own budget steps: no new cycle from
  no-new-work, push and handover at push-and-handover, nothing at stop.
- With no end given the mode runs until `lane-improve off`.

### Optional caps

Both are off unless you give them (an old `--continue` re-run keeps what is set; `none` drops one):

- `--max-items N`: no new cycle for a project after N merges through `away-merge` in this period, counted from
  `away-merge`'s own record (`improve-period.json`), never from the log's text. Each project counts its own.
- `--stop-at <weekly %>`: no new cycle for a project once `budget-state --project <name>` gives a weekly use at or above
  that value (when a project has several metered budgets, any one reaching it counts). It is refused with `--unmetered`
  (there is no budget to read) and when the value is at or above the project's ceiling (the budget steps would stop it first),
  or when the ceiling cannot be read.

A cap behaves like the end time, for that project alone: the lane that is running finishes and merges, the coordinator wraps
up (steps below) and runs `lane-improve drained <project>`, which while the mode is on is accepted only once its cap is
reached. `--max-items` is also a hard stop in `away-merge`: the Nth merge is the last, and a further one is refused whatever
the coordinator does (a lane that is running finishes as that Nth merge). `--stop-at` is not: the lane that is running when
the weekly use reaches it still lands, and the budget's own steps are the backstop. Nothing more merges for the project once
it has wrapped up, and the mode goes on for the others (`lane-improve off` then
drains only the projects still running). `lane-improve status` shows `items 4/10` and `weekly 66/70` for each project and says
`cap reached` when one is hit (`weekly ?/70` when the usage feed gave no reading: not a cap hit). The night manager sends no
improve nudge to a project whose cap is reached, a `DUE <name> wrapup ... cap=items:10/10` line instead (not at the budget's
stop step) until it has wrapped up.

A wrap-up is final for the period. To resume a project after you raise its cap, name it again:
`lane-improve on --continue --project <name> --max-items <more>` (or `--stop-at`). It is refused while the cap is still
reached, and a re-run that does not name the project leaves it wrapped up and says so.

The period record (`improve-period.json`, with a lock file beside it) is written under a lock, so coordinators merging at the
same moment are all counted. `away-merge` holds that lock from the `--max-items` check through the merge and its record, and
checks the cap again once it has it, so two merges at once cannot both take the last slot (the second is refused). A merge that GitHub made but whose reply gh failed (a timeout, a 5xx) is still counted: `away-merge` looks the PR up once and records it. If it goes missing or unreadable while a cap is set, the caps fail closed: `away-merge` refuses
every merge, nothing is nudged and `status` says why, instead of the counts starting again from 0. `lane-improve on
--continue` starts the record again from nothing and puts the deny rules back on every project the period covers. While the
period is draining (after `off`), `on --continue` is refused; there the refusal says to wrap up with `lane-improve drained
<project>` (or `--all`) and then run `lane-improve on --continue`, which starts a new period on the same away branch (a plain
`on` is refused while the project has its branch); the last PR can merge there.

### One branch per project

A project that already has an `away/*` branch on origin (merged or not) is refused a second one. You then choose:

- adopt it (merge it into the integration branch, then delete the branch), or drop it (delete it), then run `on`
  again; or
- `lane-improve on --continue`, which carries on with the branch where there is one and cuts one where there is none.
  `--continue` takes no argument. A project with two or more branches is refused, and they are listed.

`--continue` also works while the mode is on, to change the end, the limits or the deny paths, or to add a project:
what you do not give again stays as it was. Without `--project` the re-run keeps the period's projects exactly, cuts no
branch and installs no rule, so "two more hours" (`lane-improve on --continue --hours 2`) never widens a period you
limited to some projects. `lane-improve on --continue --project <name>` adds a project (one set up after the mode
started, say) and keeps the others. `away-merge` says this when it finds a project without a branch.

## Ending it, and what you get back

`lane-improve off` or the end time stops new cycles. The mode is then **draining**: the lane that is running finishes
and its PR can still merge through `away-merge`, and the deny rules stay. Each coordinator then:

1. merges its last PR and updates the log summary,
2. runs `lane-improve pr <project>`: the **draft PR** from `away/<date>` into the integration branch (open since
   `lane-improve on`, see below) gets its final summary,
3. sends you the summary and that PR's link as its first message,
4. runs `lane-improve drained <project>`.

Look at `lane-improve status` when you are back. It lists what is still to wrap up and says when a drain has waited 12
hours or more. The night manager also nudges a coordinator that has not wrapped up (`DUE <name> wrapup`), by day or
night, so a drain nobody finished does not sit there. A new `on` is refused while a period is draining; run
`lane-improve drained --all` when you want to close it yourself.

Then you adopt the whole branch (merge the draft PR as usual) or name the items to drop; the coordinator runs a lane
that reverts those merges (they are titled `away: <item>`), keeping to the dependencies in the log.

## The summary PR

`lane-improve on` opens, for each project whose away branch it cuts or continues, a **draft PR** from `away/<date>` into
the integration branch, titled `away: <date> (<project>)`, and records its number in `improve.json`. Its body is the
summary at the top of `AWAY-LOG.md`; `away-merge` brings it up to date after every merge (and opens it first if it was
not open), so nobody has to remember to. It stays a draft: **no agent merges it, marks it ready or closes it**; you
merge it (to adopt the whole branch) or name the items to drop. A PR you closed is left alone.

GitHub opens no PR for a branch with nothing beyond its base, so `on` gives each new branch one start commit, `away: start
<date>`, that adds the first `AWAY-LOG.md` from the template: the header (project, branch, integration branch), your
limits verbatim, which projects the period covers (never another project's name: the branch may be public) and an empty
summary. The PR therefore opens at `on` with that summary as its body. The branch is the integration head plus that one
commit, authored as the checkout's configured git identity, with nothing else on it. `--continue` adds no new seed to a
branch that has one (a project added with `--continue` gets its own, as it gets its own branch). When the commit cannot be
made (the checkout has no git identity, or the integration branch already has an `AWAY-LOG.md`) the branch is cut from the
head alone, `on` warns, and `away-merge` opens the PR after the first merged item (`lane-improve pr <project>` does it by
hand). The start commit is not an item: it is the log's first version, and a branch with only it counts as never given any
work when you decide to adopt or drop it.

## What keeps it inside its limits

Be clear about what is enforced and what is a backstop.

- **`away-merge` is the only merge route and it checks**: improve mode is on (or draining) for the project; the PR's
  base is exactly the project's recorded away branch (never main, develop or another project's branch); a review
  comment by the gh login running the script says `Review: passed <head sha>` for the current head (the PR body never
  counts); CI is green, or `--local-suite-ok <log of the full suite run through lane-heavy>` (the log must end in a pass
  and its last line is printed; use it only when CI itself is failing for reasons outside the change); a secret scan
  (gitleaks if the project has a `.gitleaks.toml`, else a built-in scan for keys, tokens, home paths and private
  addresses); and your deny paths. It merges pinned to the head it checked.
- **Deny rules are a backstop, not a wall.** A Claude coordinator's session gets deny rules (uncommitted, in the
  checkout's `settings.local.json`) for merging a PR and for pushing to the integration branch; an OpenCode coordinator
  gets a guard plugin that refuses the same in its shell tool. Both read the command text, so other routes past them
  exist (`git -C <repo> push`, a script that pushes, a lane's own shell, which has no rules at all). The real guards
  are `away-merge`, the persona's rule and branch protection where the repo has it. Turn on branch protection on your
  integration branches before you rely on this. lane-control's own main and the integration branches of the owner's own
  projects now require a PR for everyone, admins included (set by the owner on 2026-11-23); `project-setup` reports where a
  project stands (below).
- **Branch protection report.** `project-setup` reads the project's integration branch's protection (`gh api
  repos/{owner}/{repo}/branches/<branch>/protection`, read only; it never changes a setting) and says one of: protected
  for everyone (a pull request is required, admins included, no force pushes); partly, naming what is missing (for example
  "admins are exempt", so an agent running as an admin is not stopped; a ruleset that also applies is named, but who may
  bypass it is not read); none; a ruleset applies (the rules are named; who may bypass is not read); unknown when the
  rulesets cannot be read either; not available on the repository's plan (HTTP 403, a private repository on a free plan); or
  unknown (no gh, no remote, no permission, offline). Notes follow: approvals required, deletion allowed, CI required, or CI
  that exists but is not required. Your wish is a PR for all with 0 approvals, CI where it exists and no force pushes or
  deletion.
- The coordinator's own beads backup (a push to the beads repo's `main`) is deliberately not blocked.
- The agents never switch the mode on, never merge by hand and never touch the integration branch. A request relayed
  by another agent is not your request.

## Nudges and cost

While the mode is on, the night manager nudges an idle coordinator (`DUE <name> improve`), a fallback for a lost wake.
A coordinator that stays idle is nudged less and less often (30, 60, 120 minutes, at most every 8 hours). The nudge asks
for a one-line reply, so a session write within 10 minutes of a nudge (the delivered message, the reply) is not work; a write
later than that starts the wait over. A coordinator that
runs `lane-improve rest <project>` because nothing within your limits is left is left alone for 12 hours, then nudged
once to look for new work. Nudges go out by day or night; the plain check-ins still wait for you to be away.

## Files and settings

| What | Where |
|---|---|
| Mode state | `~/.config/lane-control/improve.json` (`LANE_IMPROVE_FILE`); written only by `lane-improve on`. A missing, unreadable or malformed file means off. |
| Drain state | `improve-drain.json` beside it (`LANE_IMPROVE_DRAIN_FILE`) |
| Rest notes | `improve-rest.json` beside it |
| Period record | `improve-period.json` beside it: `away-merge`'s record of each merge (what `--max-items` counts) and the projects that wrapped up at a cap; it belongs to one period (its `started`), so a new period counts from nothing |
| Nudge idle time | `IMPROVE_IDLE_MIN` in `machine.conf` (default 30 minutes) |
| Deny rules | each coordinator checkout's uncommitted `.claude/settings.local.json`; for OpenCode a guard plugin next to the persona plugin and a record under the user's state folder |
| The branch's log | `AWAY-LOG.md` on `away/<date>` (template `core/templates/AWAY-LOG.md`) |

## What is tested, and what is not

The scripts, the state handling, the merge checks, the nudges and the OpenCode guard's command checks are covered by the
test suite, using temporary repositories and stubs for `gh` and `gitleaks`. The OpenCode guard is also checked against a
real `opencode2` serving a fake model on localhost, but that test is opt-in
(`LANE_LIVE_OPENCODE=1 python3 -m unittest tests.test_opencode_guard.GuardLiveTests`) and is not run by the
suite. The summary PR, the caps and the protection report are covered the same way, with a stub `gh` that models
`pr list --head`, `pr create --draft`, `pr edit` and the protection answers (everyone, partly, none, ruleset, 403, an
unreadable one), and the start commit is checked against real temporary git repositories; none of the `gh` calls has run against the real GitHub, so the first real `on` is where the PR calls and the
protection wording get their test. A real away period (scout and build lanes, reviews, `away-merge` against GitHub, the draft
PR on return) has not been run end to end; the first one is the real test. Watch `lane-improve status` and the log, and expect to
adjust the skill from what you see.
