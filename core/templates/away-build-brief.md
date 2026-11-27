## Improve mode: this lane builds one item on the away branch
This lane belongs to improve mode: the owner is away and the project improves on its own branch, <AWAY_BRANCH>. The
item (picked by <PARENT>, who says why): <ITEM>. Why it was picked: <WHY_PICKED>.

- Your base is <AWAY_BRANCH>, not the default branch: fetch and merge `origin/<AWAY_BRANCH>` first, and open your PR
  into <AWAY_BRANCH>. Never merge it, and never push to the default or integration branch. <PARENT> merges it
  after the review, with `away-merge`.
- Only when <PARENT> says this is a maintenance item (docs, or new test files; the owner switched maintenance merges on):
  your base is the integration branch instead, and your PR goes into it, with no human review after the agent's. Change
  only the paths <PARENT> names (<MAINTENANCE_PATHS>): no code, no CI file, no agent instruction (AGENTS.md, CLAUDE.md,
  SKILL.md, personas, skills, templates), no executable or symlink, and for tests only new test files. If the item needs
  one other path, say so in your report: it then goes to <AWAY_BRANCH> as any item.
- The owner's limits, verbatim; they are binding, and an item that may clash is not built but reported as skipped:
  <LIMITS>
  Paths the PR must not touch: <DENY_PATHS>
- Duplicate check, before you design: find what already exists for this (code, issues, PRs, docs, the merged away
  items) and build on it. If it already exists, stop and report that, instead of building a second one. Your log entry
  must name what you checked.
- A big change (a whole area rewritten, a large new subsystem) must justify itself: what is better for the user, and
  why a smaller change would not do. "It is cleaner" is not enough. If you cannot show it is a real improvement,
  stop and report `NEEDS_DEEP` with your reasoning.
- Write your log entry in your PR's body, not in a file: open the PR with the headings of the template at
  `<LANE_CONTROL>/core/templates/away-entry.md`, each filled in: What, Why, Builds on, Existing work checked, How to try
  it, How to drop it (the merge titled `away: <item>`; once it is merged the summary PR lists it with its revert command,
  so write no commit hash; say which later items depend on it), Tests. `away-merge` refuses a PR whose body lacks one
  of them, and copies the entry into the merge commit's message; keep it up to date if you push again. Add no log file
  to the branch, and leave the summary to <PARENT>. Plain, short words, for an owner who was not here.
  The entry is scanned like the diff (no secrets, private names or home paths in it), an unfilled `<...>` template line
  counts as empty, and a body edited after <PARENT>'s review needs a new review: push no late edits to it.
  Open the PR as a draft if you like: `away-merge` marks it ready before it merges.
- Tests green before you report (through `lane-heavy` for the full suite). Nothing private in anything you commit
  (hostnames, usernames, private paths, keys): the branch is public if the repository is.
- The reviewer, on another model, checks correctness, the owner's limits, duplication and the big-change rule. The
  `Review: passed <head sha>` comment is written by <PARENT> alone after a clean round: never write it yourself, and
  never run `away-merge`; you open the PR and report.
