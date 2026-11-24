<!-- The log entry of one away item: paste this into your PR body (the build lane's PR into the away branch), each
heading as shown, none empty, filled in plain short words for an owner who was not here. away-merge refuses a PR whose
body lacks one, and copies the entry into the merge commit's message; the draft summary PR is built from those messages.
Nothing else goes into a file on the branch. -->

## What
<what changed, in a sentence or two>

## Why
<the gain for the product; why it was picked>

## Builds on
<earlier items on this branch, issues, or "nothing">

## Existing work checked
<what already exists nearby (files, issues, PRs, docs) and why this is not a duplicate>

## How to try it
<the commands or steps to see it working>

## How to drop it
<the merge commit titled `away: <item>` (the summary PR lists its `git revert -m 1 <sha>` once it is merged; write no hash), and which later items depend on it>

## Tests
<what ran and passed; CI green, or the full suite through lane-heavy when CI was failing for reasons outside the change, with the run>
