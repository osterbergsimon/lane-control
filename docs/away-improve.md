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
- Everything lands on `away/<YYYY-MM-DD>`, cut from the project's integration branch (main or develop). Nothing
  reaches the integration branch without your word.
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
| `lane-improve on [--until HH:MM\|--hours N\|--until-back] [--limits "<text>"] [--deny-paths "<glob>,..."] [--project <name>]... [--unmetered] [--continue] [--yes]` | Switches the mode on and prints what will run: projects, each branch, the end, your limits. Without a terminal it refuses unless `--yes`. |
| `lane-improve off` | Ends the mode; what is running drains (below). |
| `lane-improve status` | Off, on (what runs), or draining (what is left to wrap up, and how long it has waited). |
| `lane-improve drained <project>...` or `--all` | A coordinator's wrap-up is done: its deny rules come out. Coordinators run this themselves. |
| `lane-improve rest <project>` | A coordinator's note that nothing within your limits is left. It only quiets the idle nudges. |

Options worth knowing:

- `--limits` is free text the agents treat as binding ("nothing that needs database migrations"). `--deny-paths` are
  globs `away-merge` refuses to merge changes to, checked on the PR's diff.
- `--project` (repeatable) limits the mode to those coordinators; the default is all of them.
- `--unmetered` says this machine has no usage limits, so improve work has no budget steps (the machine's own limits,
  `lane-room` and `lane-heavy`, still apply). Otherwise each project follows its own budget steps: no new cycle from
  no-new-work, push and handover at push-and-handover, nothing at stop.
- With no end given the mode runs until `lane-improve off`.

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
2. opens a **draft PR** from `away/<date>` into the integration branch,
3. sends you the summary and that PR's link as its first message,
4. runs `lane-improve drained <project>`.

Look at `lane-improve status` when you are back. It lists what is still to wrap up and says when a drain has waited 12
hours or more. The night manager also nudges a coordinator that has not wrapped up (`DUE <name> wrapup`), by day or
night, so a drain nobody finished does not sit there. A new `on` is refused while a period is draining; run
`lane-improve drained --all` when you want to close it yourself.

Then you adopt the whole branch (merge the draft PR as usual) or name the items to drop; the coordinator runs a lane
that reverts those merges (they are titled `away: <item>`), keeping to the dependencies in the log.

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
  integration branches before you rely on this.
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
| Nudge idle time | `IMPROVE_IDLE_MIN` in `machine.conf` (default 30 minutes) |
| Deny rules | each coordinator checkout's uncommitted `.claude/settings.local.json`; for OpenCode a guard plugin next to the persona plugin and a record under the user's state folder |
| The branch's log | `AWAY-LOG.md` on `away/<date>` (template `core/templates/AWAY-LOG.md`) |

## What is tested, and what is not

The scripts, the state handling, the merge checks, the nudges and the OpenCode guard's command checks are covered by the
test suite, using temporary repositories and stubs for `gh` and `gitleaks`. The OpenCode guard is also checked against a
real `opencode2` serving a fake model on localhost, but that test is opt-in
(`LANE_LIVE_OPENCODE=1 python3 -m unittest tests.test_opencode_guard.GuardLiveTests`) and is not run by the
suite. A real away period (scout and build lanes, reviews, `away-merge` against GitHub, the draft PR on return)
has not been run end to end; the first one is the real test. Watch `lane-improve status` and the log, and expect to
adjust the skill from what you see.
