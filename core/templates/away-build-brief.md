## Improve mode: this lane builds one item on the away branch
This lane belongs to improve mode: the owner is away and the project improves on its own branch, <AWAY_BRANCH>. The
item (picked by <PARENT>, who says why): <ITEM>. Why it was picked: <WHY_PICKED>.

- Your base is <AWAY_BRANCH>, not the default branch: fetch and merge `origin/<AWAY_BRANCH>` first, and open your PR
  into <AWAY_BRANCH>. Never merge it, and never push to the default or integration branch. <PARENT> merges it
  after the review, with `away-merge`.
- The owner's limits, verbatim; they are binding, and an item that may clash is not built but reported as skipped:
  <LIMITS>
  Paths the PR must not touch: <DENY_PATHS>
- Duplicate check, before you design: find what already exists for this (code, issues, PRs, docs, the log) and build
  on it. If it already exists, stop and report that, instead of building a second one. Your log entry must name what
  you checked.
- A big change (a whole area rewritten, a large new subsystem) must justify itself: what is better for the user, and
  why a smaller change would not do. "It is cleaner" is not enough. If you cannot show it is a real improvement,
  stop and report `NEEDS_DEEP` with your reasoning.
- Write your entry in `AWAY-LOG.md` at the root of the branch (the branch's start commit made it; start the file from the template at
  `<LANE_CONTROL>/core/templates/AWAY-LOG.md` only if it is missing), in the same PR, and update the summary lines
  <PARENT> gives you here: <SUMMARY_UPDATE>. The entry has: what, why, what it builds on, existing work checked,
  how to try it, how to drop it (the merge titled `away: <item>`, which <PARENT> fills in with its commit once it is merged, and which later items depend on it), test results.
  Plain, short words, for an owner who was not here.
- Tests green before you report (through `lane-heavy` for the full suite). Nothing private in anything you commit
  (hostnames, usernames, private paths, keys): the branch is public if the repository is.
- The reviewer, on another model, checks correctness, the owner's limits, duplication and the big-change rule. The
  `Review: passed <head sha>` comment is written by <PARENT> alone after a clean round: never write it yourself, and
  never run `away-merge`; you open the PR and report.
