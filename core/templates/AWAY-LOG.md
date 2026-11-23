# Away log: <project>, <away branch>

Away mode started <date> from <integration branch>. The owner's limits, verbatim: <limits, or "none given">

This file lives at the root of the away branch and is removed when the branch is adopted. The summary is kept current;
each item adds one entry.

## Summary
- Done: <what was done, in a few plain lines>
- Worth looking at first: <the items to try first, and why>
- Drop if unsure: <items that are the most speculative or the most invasive>
- Skipped: <items left out because they may clash with the limits, one line each>

## Items

### 1. <item title>
- What: <what changed, in a sentence or two>
- Why: <the gain for the product; why it was picked>
- Builds on: <earlier items on this branch, issues, or "nothing">
- Existing work checked: <what already exists nearby (files, issues, PRs, docs) and why this is not a duplicate>
- How to try: <the commands or steps to see it working>
- How to drop: <the merge commit to revert (`git revert -m 1 <sha>`), and which later items depend on it>
- Tests: <what ran and passed; CI green, or the full suite through lane-heavy when CI was failing for reasons outside the change, with the run>
