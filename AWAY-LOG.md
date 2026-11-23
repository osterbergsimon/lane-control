# Away log: lane-control, away/2026-11-23

Away mode started 2026-11-23 from main. The owner's limits, verbatim: nothing touching install or setup on real hosts; no hostnames, usernames, private paths or project names

This file lives at the root of the away branch and is removed when the branch is adopted. The summary is kept current;
each item adds one entry.

Projects in this period: this project only.

## Summary
- Done: away-merge's secret scan now checks hostnames, usernames and project names too (lc-1dk)
- Worth looking at first: away-merge's secret scan now checks hostnames, usernames and project names too (lc-1dk)
- Drop if unsure: nothing yet
- Skipped: nothing yet

## Items

### lc-1dk: away-merge's secret scan checks names too

- **What:** for a project without a `.gitleaks.toml`, `away-merge` now also refuses a PR whose added lines hold a
  hostname, a username or a project name. A hit says only the kind ("a username"), never the name.
- **Why:** the public-repo rule forbids those names, and the built-in scan only knew keys, tokens, home paths and
  private addresses. This branch is public if the repo is.
- **What it builds on:** the built-in scan in `core/scripts/away-merge` and `machine.conf`'s coordinator names. New:
  `core/privatenames.py`, the one place that builds the list. The names come from the machine (hostname, login user),
  the other coordinators' project names (the merged project's own name is allowed) and an optional file outside the
  repo: `$LANE_PRIVATE_NAMES`, else `~/.config/lane-control/private-names.txt`, one name per line.
- **Existing work checked:** `core/scripts/away-merge`, `tests/test_away_merge.py`, `tests/test_content.py` (its
  deny-list is path patterns only; there is no list of names anywhere in the repo, so none could be reused),
  `core/scripts/project-setup` (its private-names code is about git-ignored paths), `docs/away-improve.md`, open and
  recently merged PRs, and issues. No second scan or list exists.
- **How to try it:** put a name in `~/.config/lane-control/private-names.txt`, open a PR that adds a line with it, and
  run `away-merge`: it refuses with `the diff adds a listed name`. A good merge prints `name scan: checked N private names ...`.
- **Limits:** with no file, only the machine's names and the other projects' names are checked, and the merge output
  says so. A file that is named by `$LANE_PRIVATE_NAMES` but cannot be read, or no name at all to check, refuses. Names
  under 3 letters and generic ones (`localhost`, `root`, `user`...) are ignored, since they match ordinary words. A
  project with a `.gitleaks.toml` is unchanged: its config decides.
- **How to drop it:** revert the merge titled `away: <item>` (filled in by the coordinator once merged). Nothing later
  depends on it yet.
- **Tests:** `tests/test_privatenames.py` (new) and `tests/test_away_merge.py`; full suite below.
