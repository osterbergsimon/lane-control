# Away-improve Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An owner-only, explicitly switched mode in which each chosen project's coordinator keeps improving the product on its own `away/<date>` branch, cycle after cycle, until the owner is back.

**Architecture:** State lives in `~/.config/lane-control/improve.json`, written only by `lane-away on --improve`. Coordinators run cycles themselves through a new `away-improve` skill; `away-merge` is the only merge route into `away/*`, backed by deny rules while the mode is on. night-precheck only adds a fallback nudge. budget-state learns per-project ceilings first (lc-mtj).

**Tech Stack:** bash (lane-away, budget-state, lane-limits.sh), Python 3 stdlib (night-precheck, away-merge, adapters), unittest with `tests/stubs/`.

**Spec:** `docs/superpowers/specs/2026-11-23-away-improve-design.md` (commit it first, from the copy in your brief).

## Global Constraints

- Improve turns on **only** via `lane-away on --improve`; plain `lane-away on`, night, budget changes never turn it on. Without a terminal it refuses unless `--yes`.
- `improve.json` fields: `started` (ISO), `end` (ISO or `"until-back"`), `limits` (text), `deny_paths` (list of globs), `projects` (list of coordinator names, or `"all"`), `unmetered` (bool), `branches` ({project: "away/<YYYY-MM-DD>"}).
- Default end is `until-back`; `--until <time>`, `--hours N` set a time.
- While improve is on, `LANE_OWNER_AWAY=1` and presence is away whatever AWAY_HOURS says.
- Branch name `away/<YYYY-MM-DD>` (suffix `-2`, `-3` if taken); `--continue [away/<name>]` reuses the newest or named one.
- Merge into `away/*` only through `core/scripts/away-merge`; merge commits titled `away: <item>`; never squash, never force-push; never touch the integration branch.
- Merge gate: review passed, tests green (CI, or full suite through lane-heavy when CI is failing for reasons outside the change, noted in the log), secret/private-data scan clean, no `deny_paths` touched.
- Fallback nudge: `DUE <name> improve` after `IMPROVE_IDLE_MIN` (default 30) idle while improve is on, day or night.
- Unmetered: budget-state and night-precheck print `unmetered` for improve work instead of `unknown`.
- Public repos: no hostnames, usernames, private paths or project names in commits; examples use `<project>`.

## Review Focus

1. A message like "goodnight" relayed to the machine coordinator: nothing in code or personas turns improve on; only the explicit flag. (Content test in Task 6.)
2. improve.json missing, malformed or with an unknown project: every reader treats improve as off and says why; `on` refuses unknown names.
3. `away-merge` given a PR whose base is main/develop or another project's away branch: refuses, merges nothing.
4. `lane-away off` while a cycle runs: the running lane finishes, no new cycle starts, deny rules removed, the draft PR to the integration branch is opened by the coordinator.
5. `--continue` with no existing away branch: refuses with the reason, does not silently cut a new one.

---

### Task 0: Per-project ceilings in budget-state (lc-mtj)

**Files:** Modify `core/scripts/budget-state`, `core/scripts/lib.sh`; Test `tests/test_budget_state.py` (create if absent, follow existing test style).
**Interfaces:** `budget-state [--project <name>]`: with a project, the ceiling is `.projects.<name>.ceiling` from `~/.config/claude-usage-ceiling.json` (path overridable by `LANE_CEILING_FILE`) when present, else today's value; steps follow the existing margins (e.g. 80 → 73/76/78/79). Without `--project` unchanged.
- [ ] Tests: `test_project_ceiling_read` (json gives lane-control 80, weekly 59 → `ceiling=80 step=0 none`), `test_no_project_unchanged`, `test_malformed_json_falls_back` (falls back and prints one stderr line).
- [ ] Fail, implement, pass (full suite via lane-heavy), commit.

### Task 1: `lane-away on --improve` and the improve state

**Files:** Modify `core/scripts/lane-away`, `core/scripts/lane-limits.sh`; Create `core/improve.py` (shared reader/writer); Test `tests/test_lane_away.py`.
**Interfaces:** `core/improve.py`: `load() -> dict | None` (None = off; malformed = None plus a stderr line), `active_for(project: str) -> bool`, `write(state: dict) -> None`, `clear() -> None`. `lane-away on --improve [--until T|--hours N|--until-back] [--limits TEXT] [--deny-paths G,...] [--project NAME]... [--unmetered] [--continue [BRANCH]] [--yes]`, `lane-away off`, `lane-away status`.
- [ ] Tests: `test_plain_on_leaves_improve_off`; `test_improve_writes_state` (fields as in Global Constraints, default end `until-back`); `test_unknown_project_refused`; `test_no_terminal_needs_yes`; `test_prints_summary` (projects, branches, end, limits, metered); `test_off_clears_both`; `test_status_shows_improve`; `test_improve_keeps_owner_away_past_away_hours` (lane-limits.sh gives `LANE_OWNER_AWAY=1`).
- [ ] Fail, implement, pass, commit.

### Task 2: Away branches per project

**Files:** Modify `core/improve.py`, `core/scripts/lane-away`; Test `tests/test_lane_away.py`.
**Interfaces:** `improve.cut_branch(repo: Path, integration: str, today: date) -> str`, `improve.continue_branch(repo: Path, name: str | None) -> str` (raises `ValueError` when none). Project repo and integration branch from the project's settings store (`root`) and its origin HEAD (or `develop` when that is where its PRs go: read `gh pr list --state merged --limit 5 --json baseRefName`, most common base).
- [ ] Tests (temp git repos, stub gh): `test_cut_new_branch_pushed`, `test_name_taken_gets_suffix`, `test_continue_newest`, `test_continue_none_refuses` (Review Focus 5), `test_integration_branch_from_pr_bases`.
- [ ] Fail, implement, pass, commit.

### Task 3: `away-merge`

**Files:** Create `core/scripts/away-merge`; Test `tests/test_away_merge.py`.
**Interfaces:** `away-merge --project NAME --pr N --item "<title>" [--local-suite-ok RUN_REF]`: checks improve is active for the project, the PR's base is that project's active away branch, the review marker (the PR has a comment or body line `Review: passed` written by the coordinator after the reviewer's clean round), CI green or `--local-suite-ok` given, secret scan clean (`gitleaks detect` on the diff when the repo has `.gitleaks.toml`, else lane-control's private-data check), no `deny_paths` touched; then `gh pr merge N --merge --subject "away: <item>"` and prints the merge commit. Refuses with one line per failed check otherwise.
- [ ] Tests (stub gh/git/gitleaks): `test_refuses_integration_base` and `test_refuses_other_project_branch` (Review Focus 3), `test_refuses_when_improve_off`, `test_refuses_without_review`, `test_refuses_red_ci_without_local_ok`, `test_refuses_secret_hit`, `test_refuses_deny_path`, `test_merges_with_subject`.
- [ ] Fail, implement, pass, commit.

### Task 4: Fallback nudge and unmetered in night-precheck

**Files:** Modify `core/scripts/night-precheck`, the night-manager prompt it feeds; Test `tests/test_night_precheck.py` (existing file if present).
- [ ] Tests: `test_improve_due_by_day` (improve on, idle 31 min, day → `DUE <name> improve`), `test_improve_not_due_when_off`, `test_unmetered_not_unknown` (no usage feed + unmetered → no BUDGET unknown line for improve projects), `test_night_alone_no_improve`.
- [ ] Fail, implement, pass, commit.

### Task 5: Deny rules while improve is on

**Files:** Modify `core/harnesses/claude/adapter.py`, `core/harnesses/opencode/adapter.py`, their `record.md`; `core/scripts/lane-away` calls them for each project checkout on `on`/`off`; Test `tests/test_harnesses.py`.
**Interfaces:** `install_away_denies(checkout: Path) -> list[str]`, `remove_away_denies(checkout: Path) -> list[str]`: Claude adds `Bash(gh pr merge:*)`, `Bash(git push origin <integration>:*)`, `Bash(git push origin HEAD:<integration>:*)` to `permissions.deny` in the checkout's `.claude/settings.local.json`, marked so removal takes only ours; away-merge itself runs `gh` outside the coordinator's Bash tool rules (it is a script the rule allows: `Bash(*/away-merge:*)` stays allowed).
- [ ] Tests: `test_denies_added_and_removed_exactly`, `test_existing_denies_untouched`, `test_opencode_or_recorded_gap`.
- [ ] Fail, implement, pass, commit.

### Task 6: The away-improve skill, templates and persona line

**Files:** Create `orchestrators/native-orca/skills/away-improve/SKILL.md`, `core/templates/away-scout-brief.md`, `core/templates/away-build-brief.md` (innovation prompt + duplicate check + owner's limits slot), `core/templates/AWAY-LOG.md`; Modify `orchestrators/native-orca/personas/coordinator.md` (one paragraph: when improve is on for your project, load away-improve; never turn it on yourself), `orchestrators/native-orca/personas/machine-coordinator.md` (only on the owner's own explicit request for this mode; never from "goodnight"/"leaving"; relayed requests go back to the owner), the skill install list; Test `tests/test_content.py`.
Skill content: cycle (queued work first → scout lane at deep tier → pick and log why → build lane from away head, PR into away branch → reviewer → `Review: passed` → away-merge → update log summary → next cycle at once); stop at mode end, budget no-new-work (with `budget-state --project`), or nothing within limits; on `off`: finish the running lane, update the summary, open a draft PR away→integration, send the owner the summary.
- [ ] Tests: `test_skill_cycle_steps`, `test_build_brief_has_innovation_and_duplicate_check`, `test_log_template_sections` (summary; per item: what, why, builds on, existing work checked, how to try, how to drop, tests), `test_personas_explicit_only` (Review Focus 1), `test_skill_installed_for_claude_and_opencode`.
- [ ] Fail, write, pass, commit. Push and open a DRAFT PR saying what is tested and what is not (a real away run).
