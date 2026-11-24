# Native Orca setup: implementation plan (phased, one PR per phase)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement your phase task-by-task.
> Each phase is one Orca worker lane and one PR. Before coding, write your phase's detailed TDD steps (test names
> and assertions per task) at the top of your PR description, inside the interfaces fixed here. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Make native Orca painless and repeatable: personas for every role on Claude and OpenCode, restored after
compaction, one `project-setup <repo> [host]`, machine setup with night manager, lane sweep and presence-based
limits, this week's lessons in doctrine, and lanes across SSH boxes both ways.

**Architecture:** A per-harness adapter (`core/harnesses/<name>/`) knows how to start an agent with a role and
persona and how its persona survives compaction; one env-var marker (`LANE_ROLE`, `LANE_PERSONA`) selects the
session. `project-setup` moves to `core/scripts/` and gains a native-Orca path (the default) that renders personas,
installs the hook, adds the AGENTS.md section through the native-orca orchestrator adapter, and starts agents in
Orca terminals. Machine setup installs Orca automations whose prechecks keep idle hours free.

**Tech Stack:** Python 3 stdlib (like `project-setup`), bash, a small OpenCode plugin in JavaScript, `orca-ide`,
`git`, `gh`, `systemd --user`, `unittest` with stubs.

**Spec:** `docs/superpowers/specs/2026-11-19-native-orca-setup-design.md` (read it first; it decides where this plan
is silent).

## Global Constraints

- Public repo: no hostnames, IPs, usernames, repo owners, private paths or keys. Per-machine values live in
  `~/.config/lane-control/machine.conf` (example in the repo).
- Claude and OpenCode are first-class for every role, coordinator included; every feature has both forms and both
  are tested. The targets are Claude Code and OpenCode v2 (`opencode2`) (owner, 2026-11-19).
- Owner model rule: OpenCode with `opencode/` or `opencode-go/` models only where `core/scripts/lane-tier` allows
  (one project only today, by its settings); `project-setup` refuses a role/model pair lane-tier refuses.
- Never privileged containers, extra capabilities or device passthrough; ports on 127.0.0.1 only.
- Never copy credentials. The share link (phase 6) is read from hidden input, never stored or logged.
- Orca CLI is `orca-ide` (plain `orca` may be another program); tests stub it (`tests/stubs/`).
- Heavy runs only through `~/.agents/bin/lane-heavy <cmd>`, waiting outside the slot. Never run
  `tests/test-orca-host-install.sh`.
- `python3 -m unittest discover -s tests` passes after every task; `bash -n`/`sh -n` on shell changed. Existing
  Orca + Cotal tests pass unchanged (only path constants may move).
- Commits with the owner's identity, no AI attribution; no force-push, no stash, no branch switching in a shared
  checkout. PRs are reviewed and merged on the owner's word.
- Light on doctrine: new rules only where the spec states them.

## Fixed interfaces (every phase codes against these)

- Env markers: `LANE_ROLE` in `{coordinator, design-lead, reviewer, debugger, machine-coordinator}`;
  `LANE_PERSONA` = absolute path of the rendered persona file. Unset = the hook and plugin do nothing.
- `core/harnesses/<name>/adapter.py` (names `claude`, `opencode`; `codex`, `pi` are record-only), each exposing:
  - `NAME: str`, `STATUS: str` (`in use` or `untested`)
  - `start_command(role: str, persona: Path, model: str, permission_mode: str | None) -> list[str]` — argv for
    an Orca terminal's `--command`, beginning with `env LANE_ROLE=<role> LANE_PERSONA=<persona>`
  - `install_persona_hook(checkout: Path) -> list[str]` — edits files under `checkout` (or the harness's config
    dir for OpenCode), idempotent, returns change lines; empty = already in place
  - `idle_minutes(checkout: Path, now: float, role: str = "coordinator") -> int | None` — minutes since the newest
    write of that checkout's `<role>` session (the one started with the markers, recorded by the hook or plugin in
    `~/.local/state/lane-control/sessions/`) and its sub-agents; other sessions in the checkout do not count. `None`
    if no marked session is recorded; an unreadable folder or store raises (NO-1 review 1)
  - `ORCA_AGENT_ID: str` (`claude`, `opencode2`) and `MODEL_FLAGS: frozenset[str]` (what Orca accepts:
    Claude `{"model","effort"}`, OpenCode `{"model"}`)
- `core/harnesses/persona-hook.sh` — Claude `SessionStart` hook (matchers `startup|resume|clear|compact`): prints
  `$LANE_PERSONA`'s contents when both markers are set and the file exists; exit 0 always.
- `core/harnesses/opencode/lane-persona.js` — OpenCode v2 plugin (`export default { id, setup }`): `setup` registers
  `ctx.session.hook("context")` and `ctx.session.hook("compaction")`, each pushing the persona onto `input.system`;
  only with the markers set (NO-1 review 1: 2.2.1 has no `experimental.*` hooks).
- `core/harnesses/__init__.py`: `load_harness(name: str) -> module` and `harnesses() -> dict[str, module]`.
- Personas: `orchestrators/native-orca/personas/{coordinator,design-lead,reviewer,debugger,machine-coordinator}.md`,
  with the same render placeholders `project-setup` already uses for the Cotal personas.
- `project-setup` native path writes rendered personas to `<checkout>/.lane-control/<role>.md`: the persona with the
  project's additions appended. The adaptation agent writes the additions to `.lane-control/additions/<role>.md`
  (the kept source), and every setup and refresh re-renders the combined file from both (NO-2, 2026-11-19).
- Messaging (owner, 2026-11-19: typing into terminals interferes with what the owner types): Claude to Claude on
  one machine uses Claude Code's SendMessage (address = session name from ListAgents; phase 3 starts Claude with
  `--name`); OpenCode sessions and other hosts use Orca's orchestration messages (dispatch, ask, reply,
  worker_done, check, `send --to run:<id>` (same host; see remote.md)); never type into a session the owner uses.
  Each standing session
  (coordinator, design lead, machine coordinator) owns an Orca Run so peers can reach it with `--to run:<id>`.
- Peer discovery (NO-2, 2026-11-19): Orca terminal titles `<project> coordinator`, `<project> design lead`,
  `machine coordinator`. Claude overwrites a terminal's title with its session title, so phases 3, 4 and 6 set the
  title and, for Claude, also send `/rename <title>`; personas fall back to the terminal whose `worktreePath` is the
  project's main checkout.
- Machine config `~/.config/lane-control/machine.conf` (shell-sourceable `KEY=value`): `COORDINATORS` (lines of
  `<name> <checkout path> <harness> <orca terminal title>`), `RUNNER_REPOS`, `NIGHT_START=23`, `NIGHT_END=7`,
  `IDLE_MIN=45`, `RECHECK_MIN=120`, `AWAY_HOURS=14`. Example: `core/templates/machine.conf.example`.

## Review Focus

1. A worker started in the coordinator's checkout must not get the coordinator's persona (env marker absent): test
   in phase 1 (`test_hook_silent_without_markers`, same cwd).
2. `LANE_PERSONA` pointing at a missing file: the hook and plugin stay silent and the session still starts: phase 1.
3. `project-setup` re-run on an already set-up project: no duplicate hook entry, no duplicate AGENTS.md section, no
   second coordinator started if one runs (Orca terminal with the coordinator's title exists): phase 3.
4. Night precheck with an unreadable session folder or no `machine.conf`: exits non-zero (skip, no cost) and says
   why on stderr: phase 4.
5. The share link never appears in argv, logs, the settings store or test output: phase 6
   (`test_link_not_in_argv_or_files`).

---

## Phase 1: Harness adapters and persona injection (PR 1)

**Files:** create `core/harnesses/__init__.py`, `core/harnesses/persona-hook.sh`,
`core/harnesses/{claude,opencode}/adapter.py`, `core/harnesses/opencode/lane-persona.js`,
`core/harnesses/{claude,opencode,codex,pi}/record.md`; tests `tests/test_harnesses.py`.

- [ ] Task 1.1: hook script. Tests: `test_hook_prints_persona_with_markers`, `test_hook_silent_without_markers`,
  `test_hook_silent_missing_file`, `test_hook_exit_zero_always`.
- [ ] Task 1.2: Claude adapter. `install_persona_hook` adds one `SessionStart` entry (matcher
  `startup|resume|clear|compact`, command = absolute path of `persona-hook.sh`) to
  `<checkout>/.claude/settings.local.json`, keeping other keys; idempotent. `idle_minutes` reads the newest `*.jsonl`
  mtime in `~/.claude/projects/<checkout path with every character but letters and digits replaced by ->`. Tests:
  idempotence, other settings kept, idle from a fake projects dir (HOME in a temp dir), `start_command` shape.
- [ ] Task 1.3: OpenCode adapter and plugin. `install_persona_hook` copies `lane-persona.js` into OpenCode's plugin
  folder (`$XDG_CONFIG_HOME/opencode/plugins/`, default `~/.config/opencode/plugins/`), idempotent by content.
  `idle_minutes` from OpenCode's session storage for that directory (find the v2 location under
  `~/.local/share/opencode/` and record it). Tests with a temp HOME; a `node --check` syntax check of the plugin if
  node exists, skipped otherwise. Manual check (in `opencode/record.md`): in a real v2 session, both hooks fire.
- [ ] Task 1.4: records. claude, opencode `in use`; codex, pi `untested`, with what Orca 1.4.250 says (codex: model
  and effort; pi: Orca's OMP agent takes `--model` only, to confirm it is pi). Add the harness checklist points
  (start, messages, wake, compaction, cost) to `orchestrators/checklist.md`.
- [ ] Commit per task; open PR 1.

## Phase 2: Native-Orca personas (PR 2, parallel with phase 1)

**Files:** create the five persona files (interfaces above); modify `tests/test_content.py`.

- [ ] Task 2.1: coordinator, design lead, reviewer: start from `orchestrators/orca-cotal/personas/`, replace every
  Cotal mechanism with native Orca (worker-start with `--agent`, `check`, `reply`, terminal send for nudges, the
  persona path), keep roles and rules (`core/roles.md`) unchanged. Each carries: peer-relayed "owner approved" is
  never approval; heavy commands only via lane-heavy, waiting outside the slot; budget steps from the ceiling; check
  your own inbox with `orchestration check` after dispatching a lane on another host.
- [ ] Task 2.2: debugger (new; spec section 5) and machine coordinator (from the current machine-coordinator persona
  plus spec section 8's duties).
- [ ] Task 2.3: tests in `test_content.py`: each persona exists, names no Cotal command (`cotal_`, `cotal `), has the
  four standing rules above, and is harness-neutral (no Claude-only tool names in rules both harnesses follow).
- [ ] Open PR 2.

## Phase 3: `project-setup` native path (PR 3, after phases 1 and 2 merge)

**Files:** `git mv orchestrators/orca-cotal/scripts/project-setup core/scripts/project-setup` (old path stays a
link); same for `project-pause`; modify both; tests `tests/test_project_setup_native.py`; `tests/test_project_setup.py`
path constants only.

- [ ] Task 3.1: `--orchestrator {native-orca,orca-cotal}`, default `native-orca`; the Cotal path runs exactly as
  before (its tests unchanged). `--coordinator/--design-lead/--reviewer` accept `claude/<model>` and
  `opencode/<model>`; refuse what `lane-tier` refuses (exit 2, message names the rule). The OpenCode adapter takes
  only `auto`, `bypassPermissions` (both `--auto`) and `default`: map or refuse the other Claude permission modes
  (`acceptEdits`, `plan`, `dontAsk`) for an OpenCode role before calling `start_command` (NO-1 review 1).
- [ ] Task 3.2: native path renders the five personas into `.lane-control/` (adaptation agent and reviewer as
  today, run with the chosen harness), installs each chosen harness's persona hook, adds the AGENTS.md section by
  calling the native-orca orchestrator adapter's `setup`.
- [ ] Task 3.3: start the coordinator (and design lead unless `--no-design-lead`) with `orca-ide terminal create
  --worktree path:<checkout> --title "<project> coordinator" --command <start_command>` and one first message;
  skip if a terminal with that title exists; `--trust-folder` answers Claude's trust menu by raw key input
  (Down, then Enter as separate sends). `--no-start` keeps working.
- [ ] Task 3.4: refresh, pause, wake (spec section 11) for both harnesses; pause marks the project paused in the
  settings store.
- [ ] Task 3.5: Review Focus 3 tests. Open PR 3.

## Phase 4: Machine setup (PR 4, after phase 1 merges)

**Files:** create `core/scripts/{lane-away,night-precheck,lane-sweep,ci-presence,machine-setup}`,
`core/templates/machine.conf.example`, `core/templates/ci-presence.{service,timer}`,
`orchestrators/native-orca/night-manager.md`; tests `tests/test_machine_setup.py`. Source: the machine
coordinator's working files (the brief gives their location); generalize, copy no private values.

- [ ] Task 4.1: `lane-away on|off|status` (file `~/.config/lane-control/away`, expires after `AWAY_HOURS`).
- [ ] Task 4.2: `night-precheck` per spec section 8, using `idle_minutes` from the harness adapters and a
  per-coordinator check-in stamp in `~/.cache/lane-control/night/`; prints `DUE <name> idle=<n>min` and
  `OFFLINE <runners>`; exit 0 only if something is due. Review Focus 4 test.
- [ ] Task 4.3: `lane-sweep` with spec section 8's rules, including "upstream only if it names the same branch",
  newest of push/commit/terminal output, hold file `~/.config/lane-control/lane-sweep.hold`, exit 10 when it
  reports. Tests with stubbed `orca-ide`, `gh`, git repos in temp dirs, including the base-branch-upstream case.
- [ ] Task 4.4: `ci-presence` (CI slice day 400 %/10 G, night or away 800 %/16 G, `--runtime`) and the limits file
  branch (lane-heavy 2 x 17 G day, 3 x 11 G night or away) in `core/limits.example.sh`.
- [ ] Task 4.5: `machine-setup [--host <h>]`: writes `machine.conf` from the example if missing, checks the usage
  feed (spec section 11), starts the machine coordinator (persona from phase 2) with `start_command`, creates the
  night manager (`37 * * * *`, precheck, `--extra-agent-args="--model sonnet"`) and lane-sweep (every 3 h, report
  only), and machine-check (every 30 min, waking the machine coordinator's session with a precheck that exits 0
  only when a budget step, memory or runner state changed) Orca automations if absent, installs the ci-presence
  timer if systemd --user works (warn and skip if not). Idempotent. Open PR 4.

## Phase 5: Lessons into doctrine (PR 5, parallel with phases 1 and 2)

**Files:** `core/reliability.md`, `core/model-routing.md` (budget steps), `core/machine.md`,
`orchestrators/native-orca/record.md`; `tests/test_content.py`.

- [ ] Task 5.1: the ten lessons of spec section 9 as reliability entries (next numbers), each with an
  `Orchestrator:` tag where one applies; entry 27 extended (capset after the deny list, `Restart=always`).
- [ ] Task 5.2: budget steps scale from the ceiling (-15, -7, -4, -2) and "free until reset" in model-routing.
- [ ] Task 5.3: content tests pin each new entry's key sentence. Open PR 5.

## Phase 6: Remote hosts (PR 6, after phase 3 merges)

**Files:** `core/scripts/project-setup` (host path), `core/scripts/lane-tunnel` (user service wrapper),
`core/templates/lane-tunnel@.service`, `orchestrators/native-orca/remote.md`; tests `tests/test_remote_native.py`.

- [ ] Task 6.1: `project-setup <repo> <host>` native path on a host (direction 1): uses `orca-host-install` for the
  server if needed, runs the phase 3 steps there over the saved Orca SSH host, and `machine-setup --host`.
- [ ] Task 6.2: direction 2: ask for the desktop share link with hidden input; pass it to the box over SSH stdin
  to `orca-ide environment add --name <this machine> --pairing-code -` (or the box CLI's equivalent; confirm it
  reads stdin, else a 0600 temp file removed at once); start `lane-tunnel@<host>` (`ssh -N -R
  127.0.0.1:<port>:127.0.0.1:<port>`); store only the environment name. `--remove-host` undoes all and tells the
  owner where to revoke the link. Review Focus 5 test.
- [ ] Task 6.3: owner-assisted first run (not in the suite): the owner makes one link; confirm a box coordinator's
  lane shows in the desktop window and its `worker_done` reaches the box; whether it wakes an agent coordinator
  there. Record results in `orchestrators/native-orca/record.md`. Open PR 6.
