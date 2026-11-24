# Away log: lane-control, away/2026-11-23

Away mode started 2026-11-23 from main. The owner's limits, verbatim: nothing touching install or setup on real hosts; no hostnames, usernames, private paths or project names

This file lives at the root of the away branch and is removed when the branch is adopted. The summary is kept current;
each item adds one entry.

Projects in this period: this project only.

## Summary
- Done:
  - three small fixes: the item cap holds under concurrent merges, a clear message when a draining period loses its record, and --yes under the ! prefix (lc-jpg, lc-kbb, lc-7xh)
  - away-merge's secret scan now checks hostnames, usernames and project names too (lc-1dk)
  - the summary PR lists each merged item with its revert command (lc-rjt)
- Worth looking at first: away-merge's secret scan now checks hostnames, usernames and project names too (lc-1dk): put the names the machine cannot know in `~/.config/lane-control/private-names.txt` and read the `name scan:` line of the next merge
- Drop if unsure: nothing yet
- Skipped: nothing yet

## Items

### Three small away-improve fixes (lc-jpg, lc-kbb, lc-7xh)
- What: (1) `away-merge` now checks `--max-items` again under the improve lock and keeps the lock through the merge and its record, so two merges at once cannot both take the last slot. (2) When a period that is draining loses its record, the refusal says to wrap up with `lane-improve drained`, then `on --continue` (a drain refuses `on --continue` itself, so the old advice to run it at once could not work); `status` shows the lost record while draining too. A merge that GitHub made but whose reply failed (timeout, 5xx) is now counted, so the cap holds then too. (3) The "no terminal" refusal of `lane-improve on` and the docs say that Claude Code's `!` prefix has no terminal and needs `--yes`.
- Why: they make the caps of this run trustworthy (a cap that can overshoot is no cap), and the two messages sent the owner to a command that fails. All three were filed by the owner today.
- Builds on: the caps and the period record from lc-2nz (`core/improve.py`, `core/scripts/away-merge`).
- Existing work checked: the open and closed issues lc-jpg, lc-kbb, lc-7xh and lc-2nz; `core/improve.py` (`hard_stop`, `_locked`, `cmd_on`), `core/scripts/away-merge`, `docs/away-improve.md`, the tests for the caps in `tests/test_away_merge.py`; the open PRs on this branch (AW-1 works on the secret scan only, not touched here). Nothing for these three exists yet.
- How to try: `python3 -m unittest tests.test_away_merge tests.test_lane_away` (the race test starts two real `away-merge` processes with one slot left); `lane-improve on` with no terminal prints the `!` hint.
- How to drop: revert the merge titled `away: small fixes: item cap holds under concurrent merges, drain message, --yes under the ! prefix` (the summary PR lists its revert command); no later item depends on it yet.
- Tests: the full suite through `lane-heavy` (see the PR); each change has a test that failed before it.

### lc-1dk: away-merge checks names too

- **What:** `away-merge` has a new check, `names`. For a public repo it refuses a PR whose added lines or new file paths (added files, rename targets) hold
  a hostname or user name of this machine, the name of another project whose repo is private, or a name from an
  optional file outside the repo. A hit says its kind, file and line (a path that holds the name is shown with every
  occurrence of it masked), never the name. It runs with or without a `.gitleaks.toml`, after gitleaks.
- **Why:** the public-repo rule forbids those names, and the scan only knew keys, tokens, home paths and private
  addresses. This branch is public if the repo is.
- **What it builds on:** `core/scripts/away-merge` and `machine.conf`'s coordinators. New: `core/privatenames.py`, the
  one place that builds the list: the machine's hostname and login user, the private projects' names, and
  `$LANE_PRIVATE_NAMES`, else `~/.config/lane-control/private-names.txt` (one name per line).
- **Existing work checked:** `core/scripts/away-merge`, `tests/test_away_merge.py`, `tests/test_content.py` (its
  deny-list is path patterns), `core/scripts/project-setup` (private-name code about git-ignored paths),
  `docs/away-improve.md`, open and recently merged PRs, and issues. No scan or shared list of names existed. A few
  tests still hold names as literals; the coordinator is following that up.
- **How to try it:** put a name in `~/.config/lane-control/private-names.txt`, open a PR that adds a line with it, and
  run `away-merge`: it refuses with `the diff adds a listed name at <file>:<line>`. A good merge prints `name scan: checked N private names ...`.
- **Limits:** a private repo is not checked (the merge says so); a repo whose visibility `gh` cannot read is refused;
  another project whose visibility cannot be read counts as private. A names file that exists but cannot be read
  (default or named by `$LANE_PRIVATE_NAMES`), or no name at all to check, refuses. With no file, only the machine's
  names and the private projects' names are checked, and the merge says so. The machine's own names are skipped when
  they are shorter than 4 letters or common words (`root`, `main`, `nixos`...), and the merge says so. camelCase
  spellings are not caught. Commit messages, PR titles and `--item` are not scanned.
- **How to drop it:** revert the merge titled `away: secret scan checks hostnames, usernames and project names on public repos` (the summary PR
  lists its revert command). Nothing later depends on it yet.
- **Tests:** `tests/test_privatenames.py` (new) and `tests/test_away_merge.py`; full suite through lane-heavy: 1755 tests, OK (9 skipped).

### lc-rjt: the summary PR lists each merged item with its revert command

- **What:** the summary PR body now has an "Items merged" list under the summary: one line per merged item with its title,
  PR number, short sha and `git revert -m 1 <sha>`, oldest first. It is read from git: the first-parent merge commits on
  the away branch that the integration branch lacks, titled `away: <item>` (the start commit has one parent, so it is not a merge). An item that a later commit on the branch reverts (`This reverts commit <sha>`) shows "already reverted", without a command. `away-merge`
  now puts `Pull request #<n>` in the merge message so the number is in git; merges made before this show no number (AW-1 and AW-2).
  The build brief, the log template, the skill and the docs now say the summary PR lists the revert command, so a lane or
  the coordinator no longer fills in a merge commit by hand.
- **Why:** on return, dropping an item is a copy and paste. Before, the "How to drop" lines said "filled in at merge", a step
  the coordinator (who never edits files) cannot do, so they stayed as `<sha>`.
- **Builds on:** `pr_body()` and `read_log()` in `core/improve.py` (every `away-merge` and `lane-improve pr` already
  refresh the body); `away-merge`'s `--subject away: <item>` merge.
- **Existing work checked:** issues lc-rjt, lc-2nz, lc-bmq, lc-509 and the open and merged PRs (#51, #52, #55, #56 and the
  summary PR #54): none lists merged items; `core/improve.py`, `core/scripts/away-merge`, the build brief, the log template,
  `docs/away-improve.md` and this log. The period record (`improve-period.json`) holds merge shas but is per period and can
  be lost, so it is not used.
- **How to try:** `python3 -m unittest tests.test_improve_items tests.test_lane_away`; or after the next merge, read the
  "Items merged" part of the summary PR (`lane-improve pr <project>` refreshes it).
- **Limits:** the two merges made before this item (AW-1, AW-2) show a sha but no PR number. If git cannot be read, the body
  says so and points to this log. Reverting an item whose later items build on it needs those reverted first (the list says
  "latest first"). A revert that is itself reverted still shows "already reverted". The summary and the list come from two
  fetches, so a merge that lands between them shows in one a refresh early; the next refresh fixes it.
- **How to drop it:** revert the merge titled `away: <item>` (the summary PR lists it with its revert command once it is
  merged); nothing later depends on it.
- **Tests:** `tests/test_improve_items.py` (new), plus changed tests in `test_away_merge`, `test_lane_away` and
  `test_content`; each failed before the change. Full suite through lane-heavy: 1777 tests, OK (9 skipped).
