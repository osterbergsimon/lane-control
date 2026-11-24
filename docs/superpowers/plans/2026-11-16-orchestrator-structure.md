# Orchestrators as swappable parts: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split lane-control into a shared core and one folder per orchestrator, with status records, one trial
checklist, a switch guide, and one command (`status`, `setup`, `strip`, `switch`) with adapters for native Orca and
Orca + Cotal.

**Architecture:** Files move with `git mv`; every old `scripts/<name>` path stays as a relative symlink to the new
home, so tests, docs and machines keep working. Scripts find the repo root by a marker file instead of their own
location. The command is one Python script that loads a small adapter module per orchestrator; an adapter declares
its actions, and a missing action fails before any change.

**Tech Stack:** bash, Python 3 (stdlib only, like `scripts/project-setup`), `git`, `gh`, `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-16-orchestrator-structure-design.md` (read it first; it is the authority
where this plan is silent).

## Global Constraints

- Public repo: no hostnames, IPs, keys, tokens, real usernames or private paths; examples use `user@host`,
  `~/code/<project>`. The project repos are private: read their PRs with `gh`, never copy their content here.
- Commits use the owner's git identity, no AI attribution. No force-push, no `git stash`, no branch switching in a
  shared checkout. Work only in your own worktree.
- `python3 -m unittest discover -s tests` passes after every task; `bash -n` / `sh -n` on every shell script touched.
- Never run `tests/test-orca-host-install.sh`; say in your report that you did not.
- Heavy runs (the full test suite takes about 5 minutes of CPU) go through `~/.agents/bin/lane-heavy <cmd>`.
- Light cleanup only: move files and take orchestrator names out of shared files; do not change what any rule says.
  Test assertions may change only where they check moved or renamed wording or paths.
- Cotal, Rove and wsp material is kept, never deleted.
- The command never stops or starts agents, never removes worktrees, never touches the project's main checkout:
  its changes go on a new branch in a new git worktree, opened as a PR with `gh`.

## Review Focus

1. A project with uncommitted changes in its main checkout: `setup`/`strip`/`switch` must leave them untouched
   (the worktree is made from the remote default branch). Test in Task 7.
2. `strip` run twice, or on a project that never had that orchestrator: no change, exit 0, says "nothing to strip".
   Test in Task 7 and Task 8.
3. A project carrying markers of two orchestrators: `status` lists both, not the first one found. Test in Task 6.
4. `switch --from a --to b` where `b` lacks `setup`: refuses before stripping anything. Test in Task 9.
5. Running the command from a symlink or from another directory: it still finds the repo root and adapters. Test in
   Task 1 and Task 6.

---

## Target layout

```
.lane-control-root                    empty marker file (Task 1)
core/
  README.md                           what is shared and why (spec section 4)
  roles.md model-routing.md issues.md reliability.md machine.md
  scripts/  lane-heavy lane-limits.sh lane-room lane-tier lib.sh park-check.sh stale-claims.sh
            window-guard.sh window-guard-install
  templates/ budget-ceiling.json window-guard.service worker-brief.md docs-review-brief.md
  limits.example.sh config.example.sh lane-ledger.tsv
orchestrators/
  README.md checklist.md switching.md
  native-orca/  record.md setup.md adapter.py templates/agents-section.md
  orca-cotal/   record.md adapter.py docs/(orca.md, orca-cotal-setup.md) personas/ skills/orca-cotal-lanes/
                scripts/(project-setup project-pause machine-coordinator-setup orca-cotal-task cotal-agent
                orca-host-install) templates/(AGENTS-coordination.md adapt/ lanes.tsv opencode.json
                project-settings.json coordinator-settings.local.json lane-brief.md)
  rove/         record.md docs/multi-machine.md scripts/(machine-night-watch.sh docs-review-launch.sh)
                templates/rove-init.sh
  wsp/          record.md docs/wsp.md scripts/wsp-owner-code.mjs
  herdr/        record.md
scripts/<name> -> relative symlink to every moved script, plus scripts/orch (the command)
README.md AGENTS.md                   stay at the root; README becomes the index
```

Keep `docs/superpowers/` where it is. `tests/` stays flat.

### Task 1: Repo-root marker and root lookup

**Files:**
- Create: `.lane-control-root` (empty)
- Modify: `scripts/lib.sh` (add `lc_root`), `scripts/project-setup:59` (`HERE`)
- Create: `core/lcroot.py`
- Test: `tests/test_layout.py`

**Interfaces:**
- Produces: bash `lc_root` (prints the absolute repo root: walk up from `$(readlink -f "$0")` until a directory
  holds `.lane-control-root`; exit 1 with "lane-control root not found" if none). Python
  `lcroot.find_root(start: Path) -> Path` (same walk from `start.resolve()`; raises `FileNotFoundError`).

- [ ] **Step 1: Write the failing tests** `test_find_root_from_nested_dir`,
  `test_find_root_through_symlink` (a symlink in a temp dir pointing at a file deep in the repo resolves to the
  repo root), `test_find_root_missing_marker_raises`.
- [ ] **Step 2:** Run `python3 -m unittest tests.test_layout -v`; expect FAIL (module missing).
- [ ] **Step 3:** Implement `find_root` and `lc_root`; set `HERE = find_root(Path(__file__))` in project-setup
  (import by path, the way `project-pause` loads project-setup).
- [ ] **Step 4:** Run the new tests, then the full suite; expect PASS.
- [ ] **Step 5:** Commit `Root marker and root lookup for the orchestrator split`.

### Task 2: Move Orca + Cotal material

**Files:** the `orchestrators/orca-cotal/` entries in the layout, `git mv` from their current places; symlinks at
each old `scripts/<name>`; path updates inside `project-setup` (`HERE / "personas"`, `"skills"`, `"templates"`,
`TEMPLATES`, the lanes.tsv default, the `LANE_TOOLS` source dir), `project-pause` and `machine-coordinator-setup`
(they load project-setup by path); `tests/*.py` path constants only.

**Interfaces:**
- Consumes: `find_root` (Task 1).
- Produces: `orchestrators/orca-cotal/...` paths above; `scripts/project-setup` etc. still work as entry points.

- [ ] **Step 1:** Add `test_old_script_paths_resolve` to `tests/test_layout.py`: for every name in a module-level
  `OLD_SCRIPT_PATHS` list (every file under `scripts/` on main before this plan), `ROOT/"scripts"/name` exists and
  is executable or sourceable. Run it: PASS now, and it must stay PASS.
- [ ] **Step 2:** `git mv` the files; create relative symlinks; update the paths listed above.
- [ ] **Step 3:** Run the full suite and `bash -n` on moved shell scripts; fix only paths until PASS.
- [ ] **Step 4:** Commit `Move Orca + Cotal material to orchestrators/orca-cotal`.

### Task 3: Move Rove, wsp and core material

**Files:** the `core/`, `orchestrators/rove/`, `orchestrators/wsp/` entries in the layout; symlinks at old
`scripts/<name>`; `lib.sh` keeps sourcing siblings (`here=$(dirname "$(readlink -f "$0")")`); scripts that read
`limits.example.sh`, `config.example.sh` or `lane-ledger.tsv` by path; tests' path constants.

- [ ] **Step 1:** Extend `OLD_SCRIPT_PATHS`/add `OLD_DOC_PATHS` checks only for paths other files link to (find
  them with `grep -rn` over the repo); for each moved doc that is linked, update the link instead of leaving a
  symlink.
- [ ] **Step 2:** `git mv`, symlinks, path updates. `lane-heavy` and `lane-room` source `lane-limits.sh` from
  their resolved directory: keep all three in `core/scripts/`.
- [ ] **Step 3:** Full suite + `bash -n`; PASS.
- [ ] **Step 4:** Inventory live paths (spec section 9, "Live paths"): grep `machine.md`, `orca-host-install`,
  `machine-coordinator-setup`, `window-guard-install`, `lib.sh` for every repo path they install, link or source;
  confirm each still resolves; list them in `core/README.md` under "Paths kept for live machines".
- [ ] **Step 5:** Commit `Move core, Rove and wsp material`.

### Task 4: Take orchestrator names out of shared docs

**Files:** `core/roles.md`, `core/model-routing.md`, `core/machine.md`, `core/reliability.md`, `README.md`,
`AGENTS.md`; destination sections in `orchestrators/<name>/record.md` or a new `orchestrators/<name>/notes.md`.

- [ ] **Step 1:** For each passage that only applies to one orchestrator (for example `roles.md` "Under Orca +
  Cotal", the `rove api` lines in Jobs, model-routing's Rove commands): move it to that orchestrator's folder and
  leave one line "On <orchestrator>: see orchestrators/<name>/notes.md#<anchor>". Generic sentences that name an
  orchestrator only as an example: replace the name with "the orchestrator". Do not change any rule.
- [ ] **Step 2:** Reliability entries: add a tag line `Orchestrator: <name>` under each entry that came from one
  (entries 7, 11, 12, 19, 22, 25 and others whose text names one); entries that apply to all get none.
- [ ] **Step 3:** Rewrite `README.md` as the index: what lane-control is (orchestrator-neutral), the core, the
  status table (copy from `orchestrators/README.md`), how to use the command, links. Keep the Requirements per
  orchestrator in each orchestrator's `setup.md`.
- [ ] **Step 4:** Update `AGENTS.md`'s layout paragraph to the new layout.
- [ ] **Step 5:** Fix `tests/test_content.py` assertions that pinned moved wording to the new file; full suite PASS.
- [ ] **Step 6:** Commit `Shared docs name no orchestrator; passages moved to their folders`.

### Task 5: Records, checklist, switch guide

**Files:** `orchestrators/README.md`, `orchestrators/checklist.md`, `orchestrators/switching.md`,
`orchestrators/<each>/record.md`, `orchestrators/native-orca/setup.md`; test in `tests/test_content.py`.

- [ ] **Step 1:** Add `test_every_orchestrator_has_record`: each directory under `orchestrators/` has
  `record.md` whose first lines contain `Status: ` followed by one of `untested`, `trial`, `in use`, `parked`,
  `retired`, and a `## History` section with at least one dated line (`- YYYY-MM-DD`). Also
  `test_status_table_matches_records`: the table in `orchestrators/README.md` lists every folder with the same
  status. Run: FAIL.
- [ ] **Step 2:** Write `checklist.md` with the nine points of spec section 7, each with what pass, partial and
  fail mean, and the result format (`point: pass|partial|fail|untested — evidence`).
- [ ] **Step 3:** Write the records. Statuses and reasons from spec section 3. native-orca: first trial =
  ORCH-1 2026-11-15, summarised from its report (the coordinator gives you the text; strip private data):
  setup pass with nested depth 1 by default, messages partial (replies to a dual-role worker not delivered),
  restart pass, remote untested, cost pass, owner view as reported. orca-cotal: history from `docs/orca.md`,
  `docs/orca-cotal-setup.md` section 4, and 2026-11-15 "parked: removed from all projects; retest when more mature;
  known problems: auth, agents losing the mesh". Rove, wsp: only what their docs say; untested points `untested`.
  herdr: `Status: untested`, history `- 2026-11-16 added as a candidate`.
- [ ] **Step 4:** Write `switching.md`: the human steps in order (spec section 6 `switch` list), each with the
  command or check, then "run `scripts/orch switch`" in its place in the sequence.
- [ ] **Step 5:** Write `native-orca/setup.md`: install, the orchestration commands a coordinator uses, nested
  worker depth setting, how a coordinator nudges another tab, the heavy-work rule.
- [ ] **Step 6:** Tests PASS; commit `Orchestrator records, checklist and switch guide`.

### Task 6: The command and the adapter interface, with `status`

**Files:** Create `core/orch.py`, `scripts/orch` (symlink), `orchestrators/rove/adapter.py`,
`orchestrators/wsp/adapter.py`, `orchestrators/herdr/adapter.py` (each `ACTIONS = frozenset()`); test
`tests/test_orch.py`.

**Interfaces:**
- Produces, per adapter module `orchestrators/<name>/adapter.py`:
  - `NAME: str`, `ACTIONS: frozenset[str]` (subset of `{"status", "setup", "strip"}`)
  - `detect(project: Path) -> list[str]` — the markers found (empty if none)
  - `setup(project: Path, work: Path) -> list[str]` and `strip(project: Path, work: Path) -> list[str]` — edit
    files under `work` (the new worktree), return the changes made, one line each; empty list = nothing to do.
- `core/orch.py`: `main(argv: list[str]) -> int`; `load_adapters(root: Path) -> dict[str, module]`;
  `make_worktree(project: Path, branch: str) -> Path` (`git -C project fetch origin`, then
  `git worktree add -b <branch> <tmp> origin/<default>`; default from `gh repo view --json defaultBranchRef` or
  `git symbolic-ref refs/remotes/origin/HEAD`); `open_pr(work: Path, title: str, body: str) -> str`.
- Exit codes: 0 done or nothing to do; 2 usage; 3 action not supported (message
  `<name> has no <action> adapter yet`); 4 git/gh failure.

- [ ] **Step 1:** Tests: `test_unknown_orchestrator_exit_2`, `test_missing_action_exit_3_no_change` (herdr setup:
  exit 3, the project dir is byte-identical after), `test_status_lists_all_detected` (a temp project carrying
  markers of two stub adapters prints both names), `test_runs_via_symlink_from_other_cwd`. Stub adapters live in
  the test's temp root (root lookup by marker makes that possible). Run: FAIL.
- [ ] **Step 2:** Implement `main`, `load_adapters`, `status`. `status` with no adapter detecting anything prints
  `none detected`. `--dry-run` accepted by every action.
- [ ] **Step 3:** Tests PASS; commit `scripts/orch: adapters and status`.

### Task 7: Orca + Cotal adapter (`status`, `strip`, `setup`)

**Files:** `orchestrators/orca-cotal/adapter.py`; test `tests/test_orch_orca_cotal.py`; fixtures
`tests/fixtures/orca-cotal-project/` (a synthetic project, no real project content).

- [ ] **Step 1:** Read the strip PRs with `gh pr diff`: project A #360 #362, project B #254 #255, project C #173 #174,
  and project C #142 (what setup added). Write the union of what they removed as a `MARKERS` list in the adapter:
  file and folder paths (for example `.cotal/`, `.lane-control/` and its settings files, the `.gitignore` lines)
  and the AGENTS.md section headings. Generic names only; no project-specific text.
- [ ] **Step 2:** Tests: `test_detect_finds_markers`, `test_strip_removes_markers_only` (other files and other
  AGENTS.md sections unchanged), `test_strip_twice_is_noop`, `test_strip_on_clean_project_says_nothing_to_strip`,
  `test_main_checkout_untouched` (a dirty file in the fixture's main checkout is unchanged after strip, via
  `main()`), `test_setup_calls_project_setup_and_warns` (stub `project-setup`; the warning
  "project-setup commits on its own; no PR is made" is printed before it runs). Run: FAIL.
- [ ] **Step 3:** Implement. `setup` runs `scripts/project-setup` on the project as it is, after the warning; it
  does not use `make_worktree`.
- [ ] **Step 4:** Tests PASS; commit `orca-cotal adapter`.

### Task 8: Native Orca adapter and its template

**Files:** `orchestrators/native-orca/adapter.py`, `orchestrators/native-orca/templates/agents-section.md`; test
`tests/test_orch_native_orca.py`.

- [ ] **Step 1:** Write the template (spec section 6, `setup --orchestrator native-orca`), wrapped in
  `<!-- orchestrator: native-orca -->` and `<!-- /orchestrator: native-orca -->`. Under 40 lines, generic.
- [ ] **Step 2:** Tests: `test_setup_appends_section_once` (twice = one section), `test_detect_by_markers`,
  `test_strip_removes_section_only`, `test_setup_creates_agents_md_if_missing`. Run: FAIL.
- [ ] **Step 3:** Implement; `MARKERS` = the comment pair.
- [ ] **Step 4:** Tests PASS; commit `native-orca adapter and AGENTS.md section`.

### Task 9: `switch`

**Files:** `core/orch.py`; tests in `tests/test_orch.py`.

- [ ] **Step 1:** Tests: `test_switch_refuses_when_target_lacks_setup` (exit 3, nothing stripped),
  `test_switch_strip_then_setup_one_pr` (stub `gh`; one branch `orch/switch-<from>-to-<to>`, one PR),
  `test_switch_prints_human_checklist` (output ends with the steps from `orchestrators/switching.md`, read from
  that file, not duplicated in code). Run: FAIL.
- [ ] **Step 2:** Implement: check both adapters' actions first; one worktree; strip, then setup; commit; PR;
  print checklist. `switch` to `orca-cotal` refuses with exit 3 and points to running `setup` separately (its
  setup commits on its own).
- [ ] **Step 3:** Tests PASS; full suite PASS; commit `scripts/orch switch`.

### Task 10: Finish

- [ ] **Step 1:** `grep -rn` the whole repo for old paths (`docs/orca.md`, `docs/wsp.md`, `personas/`,
  `templates/`, `skills/`) and fix stale links.
- [ ] **Step 2:** Private-data scan of the diff: no hostnames, usernames, private paths, keys.
- [ ] **Step 3:** Full suite via lane-heavy; `bash -n` on all shell scripts.
- [ ] **Step 4:** Push the branch; open one PR. Body: the layout, the live paths kept as symlinks, the test count
  before and after, "did not run tests/test-orca-host-install.sh". Report to the coordinator; do not merge.
