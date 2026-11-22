# Beads Handling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** lane-control sets up, checks, backs up and restores every project's beads the same way (approach A), so a project moves between machines without losing issues or memories.

**Architecture:** One new focused module, `core/beads.py`, holds all beads logic (state, checks, layout move, restore, hook command); project-setup, machine-setup and the harness adapters call it. The existing `beads_note` is replaced; the lane-worktree `.beads` link step stays. bd and gh are only ever called as subprocesses.

**Tech Stack:** Python 3 stdlib, unittest, the fake binaries in `tests/stubs/`, bd 1.0.4, gh.

**Spec:** `docs/superpowers/specs/2026-11-22-beads-handling-design.md` (commit it first, from the coordinator's copy given in your brief).

## Global Constraints

- bd version floor: `1.0.4`.
- Beads repo: `~/code/<project-repo-name>-beads`, GitHub `<project-remote-owner>/<project-repo-name>-beads`, private; owner taken from the project's `origin` remote, overridden by `--beads-repo OWNER/NAME` (kept in the settings store as `beads_repo`).
- The Dolt remote is the same GitHub repo: `git+https://github.com/<owner>/<name>-beads.git`.
- The project's `.beads` is a symlink to `<beads-repo>/.beads` and git-ignored in the project repo; never a `core.hooksPath` into beads.
- Nothing is created, moved or pushed without the owner's yes in a terminal (`owner_says_yes`); without a terminal, say what would be done and do nothing.
- Never edit a project's committed `.claude/settings.json`; report a broken committed hook and its fix.
- Never handle a GitHub token; on a host, say what sign-in is needed (a fine-grained token limited to the beads repos).
- lane-control is public: examples use `<owner>`; no hostnames, usernames, private paths. Never create a real GitHub repo in a test; real-bd tests are opt-in (follow the existing `skipUnless` style).
- Messages follow project-setup's `say(step, what, detail)` style, step name `beads`.

## Review Focus

1. `.beads` is a plain folder holding a live database: the move must check the database opens from the new place before the old folder is replaced, and leave the old folder untouched on any failure.
2. `.beads` is not git-ignored in the project repo: no link is drafted, the fix is named (lc-2rx).
3. The beads repo exists locally but has no remote or no Dolt remote: reported as "no off-machine copy", not repaired.
4. Restore when the GitHub beads repo is not found or `gh` is not signed in: stops with the reason; never creates an empty repo.
5. A re-run after everything is done: every step reports `exists`, nothing is pushed or written.

---

### Task 0: Prove memories survive a Dolt round trip (gate)

**Files:**
- Create: `tests/test_beads_roundtrip.py` (opt-in: `skipUnless(os.environ.get("LANE_TEST_REAL_BD"))`)

- [ ] **Step 1:** Write `test_memories_survive_dolt_roundtrip`: in a temp dir, `bd init`, `bd create` an issue, `bd remember --key k "v"`, set a Dolt remote to a local `file://` remote, `bd dolt push`; in a second temp dir restore from that remote with the bd command bd 1.0.4's docs give; assert `bd show` finds the issue and `bd memories` lists key `k` with `v`.
- [ ] **Step 2:** Run it with `LANE_TEST_REAL_BD=1`. If memories do not come back with any bd 1.0.4 command, **stop and report to the coordinator** before any other task. Record the working restore command in the spec's "Left to the builder" answer.
- [ ] **Step 3:** Commit the spec, this plan and the test.

### Task 1: Beads state and checks (`core/beads.py`), replacing `beads_note`

**Files:**
- Create: `core/beads.py`
- Modify: `core/scripts/project-setup` (`beads_note` at :630 and its two callers at :3300, :4115)
- Test: `tests/test_beads.py`

**Interfaces:**
- Produces:
  - `beads_repo_slug(repo: Path, override: str | None) -> tuple[str, str]` → `(owner, name)`; raises `ValueError` when `origin` is not a GitHub URL and no override.
  - `@dataclass BeadsState: kind: str` (`"none" | "folder" | "link" | "other"`), `beads_repo: Path | None`, `ignored: bool`, `git_remote: str | None`, `dolt_remote: str | None`, `hooks_path_into_beads: bool`
  - `inspect(repo: Path) -> BeadsState`
  - `check(repo: Path, state: BeadsState) -> list[tuple[str, str, str]]` → `say` triples.

- [ ] **Step 1: Tests** (stubs for `bd`, `git` in `tests/stubs/`):
  - `test_slug_from_origin`: origin `https://github.com/acme/widget.git` → `("acme", "widget-beads")`; ssh form `git@github.com:acme/widget.git` same; override `"x/y"` → `("x", "y")`.
  - `test_link_layout_ok`: link + ignored + both remotes → check has one `("beads", "exists", ...)` and no warnings.
  - `test_folder_reports_no_offsite_copy`: plain folder, no Dolt remote → a line containing `"no off-machine copy"`.
  - `test_not_ignored_names_fix`: link not ignored → a line naming `.gitignore` and no link drafted (Review Focus 2).
  - `test_remote_missing_reports_only`: link, no git remote → reported; asserts no subprocess other than read-only calls ran (Review Focus 3).
  - `test_hooks_path_into_beads_reported`.
- [ ] **Step 2:** Run `python3 -m unittest tests.test_beads -v`; expect failures.
- [ ] **Step 3:** Implement; import `core/beads.py` the way project-setup imports `core/harnesses`. Replace `beads_note` calls with printing `check(...)`.
- [ ] **Step 4:** Run the test file and the full suite through `~/.agents/bin/lane-heavy`; all pass.
- [ ] **Step 5:** Commit.

### Task 2: bd version floor in machine-setup

**Files:** Modify `core/scripts/machine-setup`; Test `tests/test_machine_setup.py`
**Interfaces:** Produces `bd_version_step() -> None` in machine-setup, using `core/beads.py`'s `bd_version() -> tuple[int, ...] | None` (parse `bd version` output, e.g. `bd version 1.0.4 (ce242a8...)` → `(1, 0, 4)`).

- [ ] **Step 1: Tests:** `test_bd_missing_says_install`, `test_bd_too_old` (stub prints `bd version 1.0.3 (x)` → line names floor `1.0.4`), `test_bd_ok` (`1.0.4` → `exists`).
- [ ] **Steps 2–5:** fail, implement, pass, commit.

### Task 3: lane-control owns the beads session hook

**Files:** Modify `core/harnesses/claude/adapter.py`, `core/harnesses/opencode/adapter.py`, `core/harnesses/{codex,pi}/record.md`, `core/harnesses/claude/record.md`; Test `tests/test_harnesses.py`
**Interfaces:**
- Consumes: `core/beads.py` `bd_prime_command() -> str | None` (runs `bd prime` once with each candidate form, returns the first that exits 0: `"bd prime"` first).
- Produces: `install_beads_hook(checkout: Path) -> list[str]` in each adapter that supports it; `committed_bd_hooks(repo: Path) -> list[tuple[str, bool]]` in `core/beads.py` → each `bd …` command in committed `.claude/settings.json` hooks and whether it runs.

- [ ] **Step 1: Tests:**
  - `test_claude_beads_hook_added_once`: adds a SessionStart entry (matcher `startup|resume|clear|compact`) with `bd prime` to `.claude/settings.local.json` next to the persona hook; second run → no change, line says `exists`.
  - `test_committed_broken_hook_named`: committed `bd prime --hook-json`, stub bd rejects unknown flags → reported with fix `bd prime`; committed file byte-identical after.
  - `test_duplicate_hook_reported`: committed working `bd prime` → lane-control does not add its own, says why.
  - `test_opencode_beads_hook`: if bd offers an OpenCode integration, installed once; otherwise record.md says not covered (test pins whichever the builder finds, from bd's docs).
- [ ] **Steps 2–5:** fail, implement, pass, commit.

### Task 4: Move to the layout on the owner's yes

**Files:** Modify `core/beads.py`, `core/scripts/project-setup` (new flag `--beads-repo OWNER/NAME`, `beads_repo` added to `SETTING_KEYS`); Test `tests/test_beads.py`
**Interfaces:** Produces `adopt_layout(repo: Path, state: BeadsState, slug: tuple[str, str], yes: Callable[[str], bool]) -> list[tuple[str, str, str]]`.

Order for a plain folder: `gh repo create <owner>/<name> --private` (own yes) → `git init` the beads repo at `~/code/<name>` → move `.beads` into it → check `bd where` and `bd show`/`bd memories` work through the new path → commit, push, set Dolt remote, `bd dolt push` → replace the folder with the symlink → add the ignore line → unset `core.hooksPath` when it pointed into beads. On any failure: stop, leave the original `.beads` in place.

- [ ] **Step 1: Tests** (stub `gh`, `bd`, `git`):
  - `test_no_terminal_only_describes`: `yes` unavailable → returns the plan lines, filesystem unchanged.
  - `test_owner_says_no_nothing_changes`.
  - `test_folder_moved_and_linked`: final state `kind == "link"`, ignored, remotes set, hooksPath unset; gh called with `--private`.
  - `test_failed_open_keeps_original` (Review Focus 1): stub `bd where` fails at the new path → original folder intact, nothing pushed.
  - `test_rerun_all_exists` (Review Focus 5).
- [ ] **Steps 2–5:** fail, implement, pass, commit.

### Task 5: Restore on a new machine and on a host

**Files:** Modify `core/beads.py`, `core/scripts/project-setup` (local setup and the host path), `orchestrators/native-orca/remote.md`; Test `tests/test_beads.py`, `tests/test_remote_native.py`
**Interfaces:** Produces `restore(repo: Path, slug: tuple[str, str], yes: Callable[[str], bool]) -> list[tuple[str, str, str]]`, using the restore command proven in Task 0.

- [ ] **Step 1: Tests:**
  - `test_restore_clones_links_pulls`: no local beads, stub `gh repo view` finds the repo → cloned to `~/code/<name>`, linked, Dolt restore called.
  - `test_restore_repo_missing_stops` and `test_restore_gh_not_signed_in_stops` (Review Focus 4): stop with the reason; `gh repo create` never called.
  - `test_host_setup_runs_restore_on_host`: the host path runs the same restore on the host and, when the host's gh is not signed in, prints what sign-in is needed (a fine-grained token limited to the beads repos) without reading any token.
- [ ] **Step 2:** First check what reaches a host today (`.beads` is git-ignored, so likely nothing); write that in the PR.
- [ ] **Steps 3–6:** fail, implement, pass, commit.

### Task 6: Backup in handovers and docs

**Files:** Modify `orchestrators/native-orca/personas/coordinator.md`, `orchestrators/orca-cotal/personas/coordinator.md`, the night-manager persona, `core/issues.md`, `orchestrators/native-orca/setup.md`; Test `tests/test_content.py`
- [ ] **Step 1: Tests:** `test_coordinators_back_up_beads`: each coordinator persona's session-end steps contain `git push` in the beads repo then `bd dolt push`, and say a failed backup is reported to the owner. `test_issues_md_layout`: core/issues.md describes the layout, restore and the one-machine-at-a-time rule.
- [ ] **Steps 2–5:** fail, write, pass, commit. Then push and open a DRAFT PR saying what is tested (stubs, opt-in real bd) and what is not (a real GitHub repo create, a real host restore).
