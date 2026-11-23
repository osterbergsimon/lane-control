# Away log: lane-control, away/2026-11-23

Away mode started 2026-11-23 from main. The owner's limits, verbatim: nothing touching install or setup on real hosts; no hostnames, usernames, private paths or project names

This file lives at the root of the away branch and is removed when the branch is adopted. The summary is kept current;
each item adds one entry.

Projects in this period: this project only.

## Summary
- Done: away-merge's secret scan now checks hostnames, usernames and project names too (lc-1dk)
- Worth looking at first: away-merge's secret scan now checks hostnames, usernames and project names too (lc-1dk): put the names the machine cannot know in `~/.config/lane-control/private-names.txt` and read the `name scan:` line of the next merge
- Drop if unsure: nothing yet
- Skipped: nothing yet

## Items

### lc-1dk: away-merge checks names too

- **What:** `away-merge` has a new check, `names`. For a public repo it refuses a PR whose added lines or file paths hold
  a hostname or user name of this machine, the name of another project whose repo is private, or a name from an
  optional file outside the repo. A hit says its kind, file and line (a path that holds the name is shown with it
  masked), never the name. It runs with or without a `.gitleaks.toml`, after gitleaks.
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
- **How to drop it:** revert the merge titled `away: <item>` (filled in by the coordinator once merged). Nothing later
  depends on it yet.
- **Tests:** `tests/test_privatenames.py` (new) and `tests/test_away_merge.py`; full suite through lane-heavy: 1747 tests, OK (9 skipped).
