# Beads handling in lane-control

Issue: lc-5c1. Status: approved by the owner 2026-11-22; being built in lane BDS-1. A lane commits this file to
`docs/superpowers/specs/2026-11-22-beads-handling-design.md` with its first change.

## Why

Every lane-control project uses beads (bd) for issues, handovers and memories: the personas, the lane skill and
the records all assume it. But project-setup only prints a note when bd or a beads workspace is missing and creates
nothing. So each project's beads is whatever was set up by hand at the time, and it drifts:

- calvin's beads lives only on this machine: a real, git-ignored `.beads/` folder in a public repo, with no remote.
  Its issues and memories would be lost with the disk or in a move.
- calvin's SessionStart hook runs `bd prime --hook-json`, a flag the installed bd 1.0.4 does not have (calvin-w7bt).
- calvin's git `core.hooksPath` points at `.beads/hooks`, so bd's git hooks run on every calvin commit.
- lane-control, pasteur and vellum each use a private `<project>-beads` GitHub repo linked from `.beads`, pushed
  both by git and by `bd dolt push` (the same GitHub repo is the Dolt remote). Only pasteur has a `bd prime`
  session hook of its own.

The owner's goal: **move a project between machines (a migration, or onto a box) without losing beads**: issues
and memories both. Only one machine works on a project at a time.

## Goals

1. Every project's beads has a complete off-machine copy, issues **and** memories, that a new machine can restore.
2. Moving a project to another machine restores its beads as part of setup, with no hand steps beyond the owner's
   yes and their GitHub sign-in.
3. One layout, one hook setup and one version floor for every project, set up and checked by lane-control.
4. Problems are said plainly at setup time (missing bd, too-old bd, no remote, a broken hook, an unpushed database),
   not found later.

## Non-goals

- Two machines writing one project's beads at the same time (no merge or conflict handling beyond what bd and
  Dolt already do on pull).
- Deciding issue content, or wrapping bd commands: agents keep calling bd directly.
- Making beads public. Beads repos stay private, whatever the project repo's visibility.

## Anti-goals (a failure even if it works)

- Beads data, or a path to it, ending up in a public project repo (calvin and lane-control are public).
- lane-control creating a GitHub repo, a link or a workspace without the owner's yes.
- A setup re-run that silently moves, overwrites or deletes an existing beads folder.
- A restore that brings back issues but not memories.

## Approaches

- **A. One private beads repo per project holding both copies (chosen by the owner).** The project's `.beads` is a
  git-ignored symlink to `<beads-repo>/.beads`. The beads repo's git history carries config and committed files; the
  same GitHub repo is the Dolt remote, so `bd dolt push` carries the whole database, memories included. Restore =
  clone the beads repo, link it, pull the database. Already used by three of four projects.
- B. Dolt remote only, plain `.beads` folder in the project (calvin's shape plus a remote). One mechanism, but config
  and hooks are saved nowhere, and three projects would change layout. Not chosen.
- C. Commit exported issues (JSONL). Loses memories. Dropped.

## Design

### The layout (approach A)

- Beads repo: `~/code/<project>-beads` (next to the project checkout), private on GitHub as `<owner>/<project>-beads`.
  The Dolt remote of the database is that same GitHub repo (`git+https://github.com/<owner>/<project>-beads.git`).
- The project's `.beads` is a symlink to `<beads-repo>/.beads` and is git-ignored in the project repo (lc-2rx: a
  setup that drafts the link must check the ignore first, or lanes cannot be removed).
- The project repo gets no bd git hooks and no `core.hooksPath` pointing into beads. Whether the beads repo itself
  keeps bd's git hooks (vellum-beads commits `.beads/hooks/`) is for the builder to settle from bd's docs: keep them
  only if they matter for the symlink layout, and set them up the same way in every beads repo.

### Setup and check (project-setup and machine-setup)

1. **bd version.** machine-setup checks bd is on PATH and at least the floor (1.0.4, the version in use; raise it
   only with a reason). Too old or missing: say so plainly with how to install; do not install it.
2. **Hooks.** lane-control owns the beads session hook, the way it owns the persona hook (core/harnesses):
   - Claude: `bd prime` on SessionStart (startup, resume, clear, compact), added to the checkout's uncommitted
     `.claude/settings.local.json` next to the persona hook. The exact command is checked against the installed bd
     (for example by running it once); `--memories-only` or similar only if bd's docs say it fits a compact hook.
   - OpenCode: the equivalent through lane-control's OpenCode plugin, if bd offers one; otherwise documented as not
     covered.
   - A beads hook already in a committed `.claude/settings.json` (calvin's `bd prime --hook-json`): setup checks it
     the same way and, when it fails, names it and the fix (it is the project's committed file: setup does not edit
     it; the coordinator runs a lane). Duplicate hooks (lane-control's plus a committed one) are reported, not run
     twice silently.
3. **Layout.** When the project has no beads workspace, or has one in a plain folder (calvin), setup offers the move to
   the layout above **on the owner's yes** (in a terminal only, like the other yes/no steps; without a terminal it
   only says what it would do):
   - create the private GitHub repo with `gh repo create --private` (outward-facing: its own yes);
   - move the existing `.beads` folder into the new beads repo (never copy-and-delete without checking the database
     opens from the new place), commit, push, set the Dolt remote, `bd dolt push`;
   - replace the folder with the symlink, add the ignore line, and remove a `core.hooksPath` that pointed into it.
   Re-runs report `exists` for each step done.
4. **Remote check.** For every project with beads, setup checks the beads repo has a remote it can push to and the
   database has a Dolt remote, and reports "beads has no off-machine copy" plainly when not.

### Handover and backup

- The coordinator personas' session-end steps include the backup: in the beads repo `git push`, then `bd dolt push`
  (the line already in the owner's CLAUDE.md). Night-manager handovers too.
- A backup that fails is reported to the owner, not retried silently.

### Restore on a new machine or a box

- project-setup on a machine where the project has no beads yet, but the beads repo exists on GitHub: clone it next
  to the checkout, link `.beads`, and restore the database from the Dolt remote (builder: find the bd command that
  restores memories, and test that memories come back, not only issues).
- A remote host (`project-setup <repo> <host>`): the same restore runs on the host. Builder: check what reaches the
  host today (the `.beads` link is git-ignored, so likely nothing) and close the gap.
- **Boxes sign in to GitHub themselves (owner's choice, 2026-11-22).** The host clones the beads repo and its
  coordinator backs up straight to GitHub at each handover, exactly as on the desktop. The credential should be
  narrow: a fine-grained token limited to the beads repos, put in place by the owner (setup says what is needed and
  never handles the token itself). Copying beads over ssh from the desktop was rejected: the box's work would have no
  off-box copy until copied back.
- Before a migration, the old machine's last step is a backup (above). Two machines writing at once stays a non-goal;
  docs say so.

### Calvin

calvin moves to this layout as the first real use, on the owner's yes, after the code lands: a private
`calvin-beads` repo, its `.beads` folder moved in, `core.hooksPath` removed, and calvin-w7bt's hook fix. The calvin
coordinator is told before and after.

## Calls made after the builder check (coordinator's calls; the owner can overturn any)

- **Which GitHub repo.** The beads repo is `<project-remote-owner>/<project-repo-name>-beads`, taken from the
  project's own `origin` remote (not the `gh` login), so org-owned projects work. `--beads-repo OWNER/NAME` overrides
  it and is kept in the settings store. Restore on a new machine uses the same rule, and when that repo is not found
  it says so and stops: it never offers a fresh empty repo when one was expected.
- **Layouts that differ from the convention** (another folder name, no remote, a Dolt-only remote): setup reports
  what it found and the fix in plain words, and repairs nothing on its own.
- **Memories must come back.** The builder proves on a scratch database that the restore brings back memories as
  well as issues. If bd 1.0.4 cannot do that, the lane stops and reports before building the restore; the approach
  is revisited with the owner, with no silent fallback.
- **Existing code.** The new flow replaces `beads_note`; the lane-worktree `.beads` link step stays as it is. Hooks
  cover Claude and OpenCode; Codex and Pi get the hook where their harness supports a session-start hook, otherwise
  their record.md says it is not covered.
- **calvin-w7bt** stays calvin's own lane, already queued. This build only detects a broken committed hook and names
  it.
- **Tests** follow the existing opt-in convention in tests/ for anything needing real bd or GitHub.

## Constraints

- lane-control is public: no hostnames, usernames, private paths or keys in commits; examples use `<owner>`.
- Never print or copy credentials; `gh` uses the owner's existing sign-in.
- No git stash, no branch switching in shared checkouts, no force-push.
- Tests: TDD in tests/, opt-in for anything that needs GitHub or a real bd; never create a real GitHub repo in a test.

## Left to the builder

- The exact bd commands for restore, Dolt remote setup and hook output, from bd 1.0.4's docs and a scratch run.
- Whether the beads repo keeps bd's git hooks (see Layout).
- Where the checks live between machine-setup and project-setup, beyond the split above.
- Wording of the plain-language lines, in the style of the existing project-setup messages.

## Builder answers (Task 0, bd 1.0.4, scratch run with a local Dolt remote)

- **Restore that brings back issues and memories:** clone the beads repo (its committed `.beads/config.yaml` carries
  `sync.remote`, the Dolt remote; the database folder is git-ignored), then run `bd bootstrap --yes` in a directory
  whose `.beads` resolves to it. Fallback when there is no config: `bd init -p <prefix> --remote <url>`. Both are pinned
  by `tests/test_beads_roundtrip.py` (opt-in: `LANE_TEST_REAL_BD=1`).
- **Dolt remote setup:** `bd dolt remote add origin <url>`, then `bd dolt push`.
- **Side effect to remember:** `bd init` also installs Claude Code integration (`.claude/settings.json` with bd hooks)
  in the directory it runs in; lane-control's own hook step must not be fooled by, or double up with, that file.
