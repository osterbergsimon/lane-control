# project-setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command sets up an Orca project run by a Cotal coordinator and frontend design lead (OpenCode and/or Claude), on this machine or an SSH host, with lanes as child tasks in the Orca GUI.

**Architecture:** `scripts/orca-host-install` (POSIX sh, workstation side) pre-places orcad + node on a host and optionally pairs. `scripts/project-setup` (python3 stdlib, one file) runs on the execution host and drives the `orca` and `cotal` CLIs idempotently; content lives in `personas/`, `skills/orca-cotal-lanes/`, `templates/lanes.tsv`. Unit tests run project-setup against stub `orca`/`cotal` executables on PATH; end-to-end runs on trial-vps as user `dev`.

**Tech Stack:** POSIX sh, python3 (stdlib only, `unittest`), Orca 1.4.250 CLI, Cotal 0.145.1 CLI, OpenCode 2.2.x, Claude Code 2.1.x.

**Spec:** `docs/superpowers/specs/2026-11-06-project-setup-design.md`

## Global Constraints
- Agents and both scripts never run as root (orca-host-install: refuse remote root unless `--allow-root`; project-setup: refuse).
- No typing into agent terminals: no `orca terminal send`, no Orca `orchestration` verbs, anywhere in scripts, personas or skill.
- No credential copying.
- Claude permission mode default `auto`; allowed values `auto`, `bypassPermissions`, `default`.
- OpenCode agents get `events: false` (Cotal `--no-events`).
- Defaults: coordinator `claude/opus`, design-lead `claude/opus`, clone parent `~/code`, orcad port 6768, NATS port derived in 14200–14999.
- Tier defaults: swift `opencode` `opencode/deepseek-v4.1-flash`; standard `claude` `sonnet`; deep `claude` `opus`; top none.
- Versions pinned in docs: orcad `0.1.0+b086a7d15d60`, node `v24.22.1`, node exe sha `e32e2d16081cf644f88392934288086e7662529d0d43277c34f7aad2207cfafb`.
- No commits with AI attribution; the scripts never commit in the target repo.

## Review Focus
- Re-running project-setup on a fully set-up project: must change nothing and report "exists" for every step (test in Task 4).
- A directory argument that is a subdirectory of a repo, or a repo with uncommitted changes: use the repo root, never `git init` inside it, never touch the working tree (test in Task 4).
- A project name with spaces/uppercase/dots: slugged consistently for space name, worktree name and port (test in Task 4).
- Orca or Cotal not reachable (orcad down, `cotal` missing): fail before any change with a one-line cause (test in Task 4).
- Pairing when the desktop already has this host paired: treated as success, not an error (test in Task 1).

---

### Task 1: orca-host-install

**Files:**
- Create: `scripts/orca-host-install`
- Create: `tests/test-orca-host-install.sh`

**Interfaces:**
- Produces: `orca-host-install <ssh-host> [--pair] [--port N] [--allow-root] [--appimage-root DIR]`; exit 0 when the host has a complete install (and, with `--pair`, a saved environment named `<ssh-host>`). Prints `installed|exists orcad-<ver>` and `paired|already-paired <host>`.

- [ ] **Step 1: Write the test** `tests/test-orca-host-install.sh` (integration, against `${TEST_HOST:-dev@trial-vps}`): wipe `~/.orca-remote` on the host; run the script; assert remote `test -f ~/.orca-remote/orcad-0.1.0+b086a7d15d60/.install-complete`, `~/.orca-remote/runtimes/node-e32e…/bin/node --version` = `v24.22.1`, `.verified` exists; run again → output contains `exists`; with `--pair` twice → second run prints `already-paired`; squid access.log on the proxy has no `nodejs.org` or `onorca.dev` lines.
- [ ] **Step 2: Run it, expect FAIL** (script missing).
- [ ] **Step 3: Implement.** Slot source `~/.config/orca/orcad-artifacts/<target>/<ver>/` (pick the newest `<ver>` dir; target from host `ldd --version` → glibc vs musl, arch from `uname -m`); node from `<appimage-root>/resources/node-runtime/bin/node` (default: newest `~/.cache/appimage-run/*/resources`), sha must equal the slot's `.runtime-node`. Copy with `tar c | ssh tar x` into a `.stage` dir then `mv`. `--pair`: install `~/.config/systemd/user/orcad.service` on the host (ExecStart as in spec §1, `Restart=on-failure`), `systemctl --user enable --now`, poll `.orcad-readiness` for `orca_server_ready` (60 s), take `pairing.url`; workstation unit `~/.config/systemd/user/orca-tunnel-<host>.service`; `orca environment add --name <host> --pairing-code <url> --json`, map the "already paired"/"name taken" refusals to `already-paired`.
- [ ] **Step 4: Run the test, expect PASS.** Also confirm in the Orca desktop that the environment shows the host's repos (owner eyeball).
- [ ] **Step 5: Commit** `git add scripts/orca-host-install tests/test-orca-host-install.sh && git commit -m "orca-host-install: place orcad and node on a host, optional scripted pairing"`

### Task 2: Spike — manager-launched agents in existing Orca worktrees (as dev)

**Files:**
- Modify: `docs/superpowers/specs/2026-11-06-project-setup-design.md` (append "Spike results")

- [ ] **Step 1:** On trial-vps as dev: `cotal telemetry off`; `spawn.env` proxy vars in `~/.config/cotal/config.json`; `cotal ext add @cotal-ai/orca`; throwaway repo `~/code/spike`; `orca repo add`; `cotal space create spike --root ~/code/spike`; `cotal up --root ~/code/spike --runtime orca --server nats://127.0.0.1:14250`.
- [ ] **Step 2:** `orca worktree create --repo id:<id> --name w1 --parent-worktree path:~/code/spike --setup skip --json`; `cotal spawn -d -n w1 --harness opencode -m opencode/deepseek-v4.1-flash-free --no-events --cwd <w1 path> -p "say hi to the roster"`.
- [ ] **Step 3: Verify:** `cotal ps --json` has w1 live within 60 s; `orca terminal list --json` shows exactly one new terminal whose worktree is w1; `orca worktree list` count unchanged except w1.
- [ ] **Step 4:** Same with `--harness claude -m sonnet` and `launchOptions` permission-mode auto (owner has logged Claude in as dev); record whether auto is accepted.
- [ ] **Step 5:** Log out all ssh sessions, reconnect: stack still up (`cotal status`)? If not, note that project-setup must install `cotal-<name>.service`.
- [ ] **Step 6:** Draft safety: type a draft into w1's Orca terminal (owner, or `orca terminal send` *only in this spike* without Enter), `cotal send -t w1 "ping"`, confirm the draft survives and w1 answers. Do for both harnesses.
- [ ] **Step 7:** Find how the cotal_spawn tool's `agent`/`model`/`cwd`/`launchOptions` are passed (call it from w1's session by prompt) and note any refusal.
- [ ] **Step 8: Record results** in the spec and commit `git commit -am "spec: spike results"`. If Step 3 fails, stop and bring the finding to the owner before Task 4.

### Task 3: Content — personas, lane skill, tier table

**Files:**
- Create: `personas/coordinator.md`, `personas/design-lead.md`
- Create: `skills/orca-cotal-lanes/SKILL.md`
- Create: `templates/lanes.tsv`, `templates/lane-brief.md`
- Test: `tests/test_content.py`

**Interfaces:**
- Produces: placeholders `{{PROJECT}}`, `{{HARNESS}}`, `{{MODEL}}`, `{{PERMISSION_MODE}}`, `{{EVENTS}}` in persona files (frontmatter keys: name, role, description, agent, model, events, capabilities, launchOptions); `lanes.tsv` columns `tier\tharness\tmodel\tpermission` with header row.

- [ ] **Step 1: Write `tests/test_content.py`:** personas parse (frontmatter YAML-ish split on `---`, keys present: `name, role, agent, model, capabilities`); coordinator `role: coordinator`, `capabilities` contains `spawn`; design-lead `role: design`; no file under personas/ or skills/ contains `rove api`, `terminal send`, `orchestration dispatch`, `task-create`; SKILL.md has frontmatter `name: orca-cotal-lanes` and mentions `--parent-worktree active`, `cotal_spawn`, `lanes.tsv`, `cotal_dm`; lanes.tsv has rows swift/standard/deep/top with the Global Constraints values.
- [ ] **Step 2: Run** `python3 -m unittest tests/test_content.py` → FAIL.
- [ ] **Step 3: Write the content.** Sources and required elements per spec §3–§5; shepherdr templates in `/tmp/claude-1000/-home-tux/263dffe9-ff24-4030-9749-9a63f3cf00d9/scratchpad/persona-research/shepherdr/`; lane brief from `templates/worker-brief.md` with `rove api` replaced by "report once by `cotal_dm` to <parent>" and the 5-line format from `templates/AGENTS-coordination.md`. Use the cotal_spawn call shape found in Task 2 Step 7.
- [ ] **Step 4: Run tests → PASS.**
- [ ] **Step 5: Commit** `git add personas skills templates/lanes.tsv templates/lane-brief.md tests/test_content.py && git commit -m "personas, orca-cotal-lanes skill, tier table"`

### Task 4: project-setup (local mode)

**Files:**
- Create: `scripts/project-setup`
- Test: `tests/test_project_setup.py`, `tests/stubs/orca`, `tests/stubs/cotal` (python stubs recording argv to `$STUB_LOG` and answering from a JSON state file `$STUB_STATE`)

**Interfaces:**
- Consumes: Task 3 files (located relative to the script: `../personas`, `../skills`, `../templates`).
- Produces: `project-setup <git-url|dir> [--host H] [--name N] [--dest DIR] [--coordinator H/M] [--design-lead H/M] [--permission-mode M] [--port N] [--tiers FILE] [--no-start]`; functions `slug(name) -> str`, `derive_port(slug) -> int` (14200 + crc32(slug) % 800), `render_persona(text, harness, model, mode, project) -> str`; output lines `<step>: created|exists|skipped <detail>`.

- [ ] **Step 1: Write tests** (stubs on PATH, temp HOME): `test_refuses_root` (monkeypatch geteuid=0 → exit 2, no stub calls); `test_preflight_fails_when_orca_unreachable` (stub `orca status` exit 1 → exit 1, message contains "orca", no other calls); `test_url_clones` (git URL to a local bare repo → clone in `--dest/<slug>`); `test_dir_subdir_uses_repo_root`; `test_dir_not_repo_inits`; `test_dirty_repo_untouched` (`git status --porcelain` identical before/after); `test_slug_and_port` (`slug("My App.v2") == "my-app-v2"`, port in [14200,14999], stable); `test_render_persona_claude` (agent claude, `launchOptions` has `permission-mode: auto`, no `events:`); `test_render_persona_opencode` (`events: false`, no permission-mode); `test_full_run_calls` (stub log contains in order: `orca repo add`, `orca skills install --skill orca-cli --local`, `cotal space create`, `cotal up … --runtime orca`, `cotal spawn … coordinator … -d`, `orca worktree create … --name design-lead --parent-worktree path:<repo>`, `cotal spawn … design-lead … --cwd <wt>`); `test_rerun_is_noop` (state says all exist → every output line `exists`, no create/spawn calls); `test_gitignore_gets_cotal_once`.
- [ ] **Step 2: Run** `python3 -m unittest tests/test_project_setup.py` → FAIL.
- [ ] **Step 3: Implement** per spec §2 steps 1–8, with the flags confirmed in Task 2. Skill install: copy `skills/orca-cotal-lanes` to `<repo>/.agents/skills/` and symlink into `<repo>/.claude/skills/` (plus `.opencode/skills/` if Task 2 showed OpenCode needs it). Personas rendered to `<repo>/.cotal/agents/`. Supervisor unit only if Task 2 Step 5 required it.
- [ ] **Step 4: Run tests → PASS.**
- [ ] **Step 5: Commit** `git add scripts/project-setup tests && git commit -m "project-setup: Orca project with Cotal coordinator and design lead"`

### Task 5: project-setup --host

**Files:**
- Modify: `scripts/project-setup` (host mode)
- Test: `tests/test_project_setup.py` (add `test_host_mode_copies_and_reexecs`, with `ssh`/`rsync` stubs)

- [ ] **Step 1: Test:** with `--host dev@box`, stub log shows a copy of `scripts/project-setup`, `personas/`, `skills/`, `templates/` to `~/.local/share/lane-control/` then `ssh -t dev@box ~/.local/share/lane-control/scripts/project-setup <same args minus --host>`; a local dir argument with `--host` is refused (paths are host paths: allowed only if absolute, passed through).
- [ ] **Step 2: Run → FAIL. Step 3: Implement. Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `git commit -am "project-setup: --host runs on an SSH host"`

### Task 6: End to end on trial-vps

- [ ] **Step 1:** From the workstation: `scripts/orca-host-install dev@trial-vps --pair`, then `scripts/project-setup https://github.com/<owner>/<small-test-repo> --host dev@trial-vps` (or a local bare repo path on the host). Expected: summary with both agents live; Orca GUI (owner) shows the project, root checkout, design-lead nested.
- [ ] **Step 2:** Re-run → all `exists`.
- [ ] **Step 3:** Owner asks the coordinator (in its Orca terminal) for two small tasks; expected: one swift lane on OpenCode and one standard lane on Claude, each a child worktree with one terminal, finish DMs reach the coordinator; ask the design lead for a small frontend lane → nested under design-lead.
- [ ] **Step 4:** Draft safety on the coordinator (Claude) while a lane reports.
- [ ] **Step 5:** Record results in `docs/orca.md`; then remove the old root trial setup on the box (root orcad, root cotal demo space, /root/orca/workspaces/demo worktrees) after listing them to the owner.

### Task 7: Docs

**Files:**
- Modify: `docs/orca.md` (replace §5b with the working recipe; host install; Claude/root; permission modes; tier table), `README.md` (file map rows for new scripts, personas, skills, templates)

- [ ] **Step 1:** Write; every command in the doc is one that ran in Task 6.
- [ ] **Step 2: Commit and push** `git commit -am "docs: project-setup recipe" && git push -u origin feat/project-setup`, then merge to main per the owner.
