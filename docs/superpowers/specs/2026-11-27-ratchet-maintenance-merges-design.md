# Ratchet: maintenance merges to the integration branch

Status: the coordinator's calls on the owner's ask (2026-11-27, lc-3g6o): "remote also has an ask for ratchet mode, to allow
'maintenance' merges on main for docs/tests/etc, as it may stall the progress otherwise". Builds on
`2026-11-23-away-improve-design.md`; everything there stands unless said here.

## Why

The ratchet merges every item into `away/<date>` only (away-merge refuses any other base; the deny rules and the OpenCode
guard block merges and pushes to the integration branch). A typo fix in the docs or a new test then waits for the owner like
a new feature, and the away branch drifts from main while the owner's own merges land there. Small, safe items should reach
the integration branch while the owner is away, and the away branch should keep up with it.

## The calls

- **Opt-in, per period.** `lane-control ratchet on --maintenance` records `maintenance: true` in improve.json (an optional bool;
  missing is false). A `--continue` re-run keeps it unless given again; `--no-maintenance` turns it off. `status` shows it with
  each project's globs. Off by default. The OpenCode guard validates the field as improve.py does.
- **What counts as maintenance.** Every changed path (old and new, from the diff headers and from GitHub's file list of the
  compare at the head sha) matches one of the project's maintenance globs: default `docs/**`, `**/*.md`, `tests/**`; a project
  sets its own as `maintenance_paths` (a list of globs, replacing the default) in its settings store
  (`~/.config/lane-control/projects/<key>/settings.json`, outside the repo, so no PR can widen its own rule). A glob that also
  matches plain code (`app.py`, `src/app.py`) is refused as too broad, and the merge with it. Whatever the globs say, never:
  CI and repo-tooling files (`.github/**`, `.gitlab-ci.yml`, `.circleci/**`, `Jenkinsfile`, `.pre-commit-config.yaml`,
  `.gitattributes`, `.gitmodules`, `.gitleaks.toml` and the like), a symlink, a submodule or an executable file, a path a deny
  path refuses, or anything the secret or private-name scan refuses. One path outside and it is not maintenance: away-merge
  refuses, names the path and prints the way it goes to the away branch as today (`gh pr edit <n> --base away/<date>`, then a
  plain `away-merge`, whose own checks then apply).
- **The route is away-merge only.** `away-merge --maintenance --project NAME --pr N --item "<title>"`. Same gates as any ratchet
  merge: the review marker for the exact head, the entry (the PR body's headings, so a refused item moves to the away branch
  unchanged), CI green on the head (no `--local-suite-ok`: this reaches main), secrets, names, deny paths, caps, the owner's
  gate hooks, and the re-read after the gates. Plus: the base is the project's recorded integration branch; the head is not an
  `away/*` branch, not the summary PR and not from a fork; the paths are checked at the head sha from GitHub (the compare
  `integration...<head>`, at most 300 files, more is refused); the head is read again after every check and the merge is
  `--match-head-commit <head>`, so a push in between refuses. The merge is a merge commit titled `maintenance: <item>`, its body
  `Pull request #N`, a `Ratchet-maintenance: away/<date>` line, then the entry. The deny rules and the guard stay as they are:
  the coordinator still cannot merge or push by hand.
- **Protection.** Before merging, away-merge reads the integration branch's protection (core/protection.py, read-only). A
  required approving review (or code-owner review) in classic protection or a ruleset is a review the coordinator cannot give:
  refused with that reason. Protection that cannot be read refuses too (fail closed); none, or none on the plan, is fine.
- **The away branch takes the integration branch in.** Right after the merge, away-merge merges the maintenance merge commit
  into `away/<date>`: a merge (git merge-tree, commit-tree with both parents, the checkout's identity), pushed without force, so
  a branch that moved in between refuses the push. Never a rebase. Its subject, `ratchet: take in <integration> after
  maintenance #N`, is not an item (`away: `), so the summary's item list skips it. A conflict, or a push that fails, stops: nothing
  is forced, away-merge exits 3 (merged, not taken in) and says how to go on (a lane merges the integration branch into a
  branch off the away branch, resolves it, and its PR goes into the away branch through away-merge). Until the earlier
  maintenance merge is in the away branch, the next `--maintenance` merge is refused, so the drift cannot grow.
- **Counted and listed.** The merge is recorded in the period record with `kind: maintenance` (so `--max-items` counts it),
  closes the cycle into the ledger, and `status` shows `maintenance merges N`. The summary PR gets its own section, "Maintenance
  merges into <integration>", read from git (the integration branch's first-parent merges carrying this branch's
  `Ratchet-maintenance:` line), one line each with its PR link (`#N`) and sha, inside the generated items region.

## Not done

- Setting `maintenance_paths` from `project setup` (it is a hand edit of settings.json for now; validated on read).
- Resolving a take-in conflict automatically; it is a lane's work by design.
