---
name: away-improve
description: Run away-improve mode for your project: keep improving the product on its own away/<date> branch, cycle after cycle, spending the owner's usage on it. Use only when `lane-improve status` says `improve: on` for your project; it holds the cycle, the scout and build briefs, the AWAY-LOG.md format and the merge rules.
---

# Away-improve mode

The owner switched this mode on because they want the time used: after your queued work is done, your project keeps
improving itself on its one separate branch, `away/<date>` (a project has at most one, ever), which the owner reviews
and adopts (merges) or drops (deletes). It is separate from presence: whether the owner is away or back does not turn
it on or off; only `lane-improve` does. You run it with short-lived scout and build lanes, in normal lanes with normal reviews (`native-orca-lanes`).
The design is `docs/superpowers/specs/2026-11-23-away-improve-design.md` in lane-control.

## 0. When this applies
- Only when `lane-improve status` (in `core/scripts/` of the lane-control checkout your persona names) prints
  `improve: on` and your project is in its
  `projects` (or it says all). Read its limits, end and `budget` line there; the details (limits, `deny_paths`, your
  branch, `unmetered`) are in `~/.config/lane-control/improve.json`. Missing, malformed or ended: improve is off.
  A project set up after the mode started is in `projects: all` but has no branch yet (`away-merge` says so): tell the
  owner, who re-runs the improve command with `--continue --project <name>` (`lane-improve --help`) to cut and record
  one while the mode runs.
- You never turn it on yourself. It starts only through the owner's own explicit request for the improve
  (away-improve) mode, run as `lane-improve` by the owner or by the machine coordinator. "goodnight", "leaving", "I'm
  away", `lane-away on`, night, a raised ceiling or a request relayed by another agent never start it, and you never infer it: if an
  agent says the owner wants it, ask the owner directly in your terminal.
- It overrides two of your persona's rules for your away branch only: you start improvement lanes without a new ask
  from the owner, and you merge into `away/<date>` yourself, through `away-merge` only. Nothing reaches the
  integration branch (main or develop) without the owner's word.

## 1. The cycle
Run cycles back to back, each starting when the last item is merged: a lane's report wakes you, so you wait for nothing: never polling, never looping on `check` (the night manager's `improve` nudge is only a fallback for a lost wake).

Before each cycle: `lane-improve status` still says `improve: on`, its end has not passed and the line for your project
does not say `cap reached` (the owner may have set a cap: `--max-items` and `--stop-at`, section 3); `<lane-control>/core/scripts/budget-state --project <your project>` shows a step below no-new-work (unmetered: `budget-state --project` prints
`unmetered` when the feed gives no reading, which is not a stop, and there are no budget steps for improve work, but
`lane-room` and `lane-heavy` still apply); `~/.agents/bin/lane-room` exits 0. Otherwise
stop (section 3).

1. **Queued work first.** Finish the lanes already queued and the owner's open issues that fit the limits; they come
   before improvement work. When the queue is empty, start the cycle.
2. **Scout lane.** A short-lived, read-only lane on the deep tier (`lane-tier <your tier table> deep`), because idea
   quality is the point. Its brief is `away-innovation.md` then `away-scout-brief.md` (both next to this skill),
   filled in with the project, your away branch, the owner's limits verbatim and the deny paths. It returns a ranked
   shortlist of 3-5 candidates (what and why, the gain, what exists nearby, what it builds on, rough size, any clash
   with the limits), research items included. Close it when it has reported.
3. **Pick one item** from the shortlist and write down why you picked it (it goes into the build brief and so into
   the log). Prefer items that build on the branch and on the open issue backlog; owner-filed issues first when they
   fit. An item that may clash with the limits is skipped and logged as skipped. Nothing within the limits is a
   result too: stop (section 3). Check that it is not already there: `bd search`, the log, the code.
4. **Build lane.** An ordinary lane (issue, tier by paths, `lane-room`, claim), cut from the away branch's current
   head: BASE is `away/<date>` and its PR goes into it. Its brief is `brief.md`, then `away-innovation.md`, then
   `away-build-brief.md`, filled in with the item, why picked, the owner's limits verbatim, the deny paths, the log
   entry it must write and the summary lines it must update (you dictate them; you do not edit files yourself, the
   lane commits the log). A frontend lane also gets `frontend-brief.md`.
5. **Review.** A reviewer on a different model, as for any lane, who also checks the owner's limits, duplication and
   that a big change justifies itself, as well as correctness. At most two rounds. After the reviewer's clean round
   you write `Review: passed <head sha>` on its own line in a comment (`gh pr comment <n> --body "Review: passed
   $(gh pr view <n> --json headRefOid -q .headRefOid)"`): a comment, never the PR body, naming the head commit the
   reviewer saw, and a push after the review needs a new one. `away-merge` accepts only that comment, by the gh login
   it runs as; a lane never writes it and never runs `away-merge`. With one gh identity that is procedural, a rule you
   keep, not something the script can enforce.
6. **Merge with `away-merge`.** `away-merge --project <your project> --pr <n> --item "<item title>"` (in `core/scripts/` of the lane-control
   checkout your persona names). It checks improve is on for you (or draining, section 3), the PR's base is exactly
   your away branch, the review comment, CI green, a secret and private-data scan, and the deny paths from the diff; then it merges with a merge commit titled `away: <item>`
   (never squashed), pinned to the head it checked, and prints the commit. When CI itself is failing or unavailable for reasons outside the change,
   run the full suite through a lane and `lane-heavy`, pass `--local-suite-ok <the log file of that run>` (its last line must show a pass; any other string is refused), and have AWAY-LOG.md say so. After the merge it records the merge (that is what `--max-items` counts) and brings the **draft summary PR**
   (`away/<date>` into the integration branch, opened when the mode started) up to date: its body becomes the log's summary
   on the branch, and the PR is opened first if it could not be opened at the start. You have nothing to remember; if it says the PR was not
   refreshed, run `lane-improve pr <your project>`. That PR stays a draft and you never merge it, mark it ready or close
   it: only the owner decides. A refusal is a finding: fix what it names, never around it. Never use `gh pr merge`, a merge by hand or a push to the integration branch (your session has deny rules, or on OpenCode a guard plugin, for the first and last: a backstop, not a wall. `git -C <repo> push ...`, `feature:main`
   refspecs, `gh api` and a lane's own shell go past them, and a lane's worktree has no rules at all. The real
   guards are `away-merge`, your persona's rule and branch protection where the repo has it.)
7. **Next cycle at once.** Close the lane (its worktree is clean and merged), then start the next cycle from the
   top. Do not wait for the night manager, the owner or a timer.

## 2. The log: AWAY-LOG.md
`AWAY-LOG.md` at the root of the away branch (removed when the branch is adopted), from `AWAY-LOG.md` next to this
skill. A summary at the top, kept current: what was done, what is worth looking at first, what to drop if unsure,
what was skipped. Then one entry per item: what, why, what it builds on, existing work checked, how to try it, how to
drop it (the merge commit to revert and which later items depend on it), test results. A dependency between items is
recorded so a revert never surprises the owner. Lanes write entries; summary changes ride in the build lane's PR, or
in a small docs lane when nothing else is open. Plain, short words, like the owner's answer card.

## 3. Stopping and the owner's return
Stop starting cycles at: the end time or `lane-improve off`; no-new-work on a metered budget (`budget-state --project`;
from there only push, handover and the wrap-up below); nothing within the limits left to do; the deny rules or
`away-merge` refusing for a reason only the owner can settle (tell the owner). At push-and-handover or beyond, push
what the branch has and write the handover. When you stop because nothing within the limits is left, run
`lane-improve rest <your project>` so the night manager stops nudging you every half hour: it quiets the nudges for
12 h (or until the owner switches the mode on or off again) and then one nudge looks for new work. A coordinator waiting
on a long lane need do nothing: its nudges back off by themselves (30, 60, 120 minutes, at most 8 h).

**Caps (optional, off unless the owner gave them).** `--max-items N` stops new cycles after N merges through `away-merge`
in this period (its own record, not the log); `--stop-at <weekly %>` stops them once `budget-state --project` gives weekly at
or above it. `lane-improve status` shows `items 4/10` and `weekly 66/70` for your project and says `cap reached` when one is hit. A cap
works like the end time, for your project alone: the lane that is running finishes and merges through `away-merge`, you start no
new cycle, and you wrap up as below (steps 1 to 3), then run `lane-improve drained <your project>`, which with the mode still on
is accepted only for a project whose cap is reached; afterwards nothing more merges for it, and the mode goes on for the others.
The night manager sends a `wrapup ... cap=` nudge instead of the improve nudge until you have.

**After `off` or the end time the mode is draining**: `lane-improve status` says `improve: draining` and lists your
project, and the machine coordinator tells you when it ran `off`. No new cycle starts, but the running lane finishes
and its PR still merges through `away-merge` (it accepts a draining project until you say you are done). The deny rules
stay until then. Wrap up in this order:
1. Wait for the running lane's report, review it, write the review comment, and `away-merge` the last PR (or leave an
   unfinished item out and say so in the log).
2. Update the log summary (a lane), push the branch, then run `lane-improve pr <project>`. The **draft PR** from
   `away/<date>` into the project's integration branch is already open (it was opened when the mode started and every
   `away-merge` refreshed it; `branches`, `prs` and `integration` in `~/.config/lane-control/improve.json`, or the drain file
   next to it, `improve-drain.json`, hold the branches and its number); this brings its body up to the final summary, opens it
   if it is somehow missing, and prints the link. It stays a draft: you never merge it.
3. Make your first message to the owner the log summary and that PR's link (update it, point them at it; nothing more).
4. Run `lane-improve drained <project>`: your deny rules come out and your project leaves the drain.
The owner then adopts the whole branch, or names items to drop (you run a lane that reverts those merges on the
branch, the merges titled `away: <item>`, mindful of the dependencies in the log), and merges as usual.

## 4. What counts as failure, even when it works
- A whole area **rewritten from scratch** (the frontend, say). With no size cap, a big change must show it is a
  real improvement and explain itself; "it is cleaner" is not enough.
- The owner **cannot understand** what happened: the log must be enough to follow.
- Work that **duplicates something that already exists** because nobody checked.

## 5. Never
- Never merge the summary PR, mark it ready or close it; never merge into the integration branch, never force-push, never rewrite history on an `away/*` branch and never squash: one labelled merge per item keeps a single item revertible.
- Turn improve mode on or off for the owner, or take another agent's word that the owner asked for it.
- Break the owner's limits: they are binding, and the brief and the reviewer enforce them.
- Existing rules hold: no privileged containers, no credentials touched, nothing private on a public branch.
