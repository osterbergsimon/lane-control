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

Revised after review round 1 (B1, M1-M5, m1-m4, n1); the coordinator's calls for B1 and M1 are marked, and the owner may
change them: each is one entry in the policy tables of `core/maintenance.py` (KINDS, DEFAULT_KINDS, NEVER, the docs and tests
rules).

- **Opt-in, per period, by kind.** `lane-control ratchet on --maintenance [<kinds>]` records `maintenance: [<kinds>]` in
  improve.json (missing or `[]` is off); bare `--maintenance` is `docs`, `--maintenance docs,tests` adds tests. A `--continue`
  re-run keeps it unless given again; `--no-maintenance` turns it off. `status` shows it with each project's paths. The OpenCode
  guard validates the field as improve.py does.
- **Never agent instructions (coordinator, B1).** NEVER, matched at any depth and in any case: `AGENTS*.md`, `CLAUDE*.md`,
  `GEMINI*.md`, `SKILL.md`, any `.claude/`, `.opencode/`, `.agents/`, `.codex/`, `.pi/`, `skills/`, `personas/`, `templates/`,
  `prompts/` folder and the files harnesses load as instructions or config (`.mcp.json`, `opencode.json`, `.cursorrules` and
  the like), plus CI and tooling files. Not in the globs: in the never-list, which nothing overrides.
- **docs, the default, is content only (coordinator, M1).** Under `docs/`, and the root `README.md` and `CHANGELOG.md`: `.md`,
  `.markdown`, `.txt`, `.rst`, `.adoc` and the images `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`. No executable, build or config
  file. Images are binary and cannot be scanned (said in the guide); any other binary is refused (m4).
- **tests is opt-in and new files only (coordinator, M1).** Under `tests/` or `test/`, only a file GitHub's compare lists as
  added and named as a test; a modified, deleted or renamed test file, and the suite's machinery (conftest, testhome, stubs,
  helpers, fixtures, runners, loaders, `__init__`, shell scripts) are refused. `on` and the guide say plainly that these are
  code nobody but agents reviewed.
- **A project's paths only narrow (M2).** `maintenance_paths` in the project's settings store (outside the repo) is a second
  filter: a path must match a kind and one of them. A glob that does not start inside a kind (`docs/`, `tests/`, `test/`) or
  name `README.md`/`CHANGELOG.md` is refused when it is read, and every maintenance merge with it.
- **Odd names (M4).** Globs match across newlines (DOTALL), a path with a control character is refused, NEVER ignores case.
- **The route is away-merge only, and only while the mode is on (M3).** `away-merge --maintenance --project NAME --pr N --item
  "<title>"`, refused while draining (after `off` or the end time). Same gates as any ratchet merge: the review marker for the
  exact head, the entry, CI green on the head (no `--local-suite-ok`), secrets, names, deny paths, caps, the owner's gate hooks,
  and the re-read after the gates. Plus: the base is the recorded integration branch; the head is not an `away/*` branch, the
  summary PR or a fork; the files are read with their status at the head sha from GitHub (the compare `integration...<head>`;
  300 files or more is refused) and every path of the diff is checked too; the head is read again after the diff, and the merge
  is `--match-head-commit <head>`, so a push in between refuses (n1). Under the lock, the mode, the kinds, the deny paths and
  the project's branches must still be what the checks saw (m1). The merge commit is titled `maintenance: <item>`, its body
  `Pull request #N`, `Ratchet-maintenance: away/<date>`, then the entry.
- **Protection.** A required approving review (or code-owner review), classic or in a ruleset, is a review the coordinator
  cannot give: refused with that reason. Protection that cannot be read refuses too.
- **The take-in (M5).** Right after the merge, the away branch takes in the maintenance merge commit: git merge-tree and
  commit-tree with both parents, pushed without force, never a rebase; subject `ratchet: take in <integration> after
  maintenance #N`, not an item. A merge commit origin does not serve yet is a failure, never a guess; with no known merge commit,
  origin's integration tip is taken in only when it holds the PR's head. A failure, a conflict or a refused push stops: nothing
  is forced, exit 3, and the next maintenance merge is refused until the away branch holds every maintenance merge, read from git
  (every merge on the integration branch that names this away branch, whatever the period, m3) and from the record. The way
  out is a lane PR into the away branch; when away-merge refuses that (it carries all of main's new commits), the owner merges
  (m3).
- **Counted and listed.** Recorded with `kind: maintenance` (so `--max-items` counts it), closes the cycle into the ledger, and
  `status` shows `maintenance merges N`. The summary PR's own section, "Maintenance merges into <integration>", is read from
  git, one line each with its PR link and sha, marked when the away branch does not hold it (m2).

## Not done

- Setting `maintenance_paths` from `project setup` (lc-d2bq; a hand edit of settings.json for now, validated on read).
- Resolving a take-in conflict automatically; it is a lane's work, or the owner's.
