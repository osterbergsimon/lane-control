# Agent Sandbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** OpenCode lanes run under bubblewrap, where the floor paths do not exist and the config that sets their
limits is read-only, launched by Orca's unchanged `worker-start` through a PATH shim, with narrow brokers for Orca
mail, git push/fetch and gh.

**Architecture:** `lane-control lane prepare --sandbox` writes a per-lane profile outside the sandbox. A shim named
`opencode`/`opencode2`, first on PATH, finds the profile for its worktree and runs the real CLI under a pinned bwrap
from `env -i`, after starting a per-lane broker on a unix socket. Inside, `orca`, `gh` and `git` wrappers send the
few allowed requests to the broker. Pure Python builds the bwrap argv from the profile, so it is unit-tested without
bwrap.

**Tech Stack:** Python 3 stdlib (`core/sandbox.py`, the shim, the broker), bash only where existing scripts are
bash, bubblewrap 0.12.0 from nixpkgs, unittest with `tests/stubs/`.

**Spec:** `docs/superpowers/specs/2026-11-27-agent-sandbox-design.md` (read it whole first). The spike
(`docs/superpowers/spikes/2026-11-27-agent-sandbox/spike.sh`) is the reference mount plan in small.

## Global Constraints

- Never a privileged container, never a setuid helper; bwrap is used only when its binary has no setuid bit.
- bwrap is run only by absolute path, `SANDBOX_BWRAP=` in `machine.conf`, from `env -i` with the allowlist; never
  started with the owner's environment (the spike's `/proc/1/environ` leak).
- bwrap flags always: `--die-with-parent --new-session --unshare-pid --unshare-ipc --unshare-uts
  --unshare-cgroup-try --proc /proc --dev /dev --tmpfs /tmp --tmpfs $HOME --clearenv`; phase 1 never `--unshare-net`.
- Profiles live in `~/.local/state/lane-control/sandbox/<worktree-id>.json` (`<worktree-id>` = sha256 of the
  resolved worktree path, first 16 hex); that folder is never mounted inside.
- Env never passed: `ORCA_CLI_COMMAND`, `ORCA_USER_DATA_PATH`, `GH_TOKEN`, `GITHUB_TOKEN`, `SSH_AUTH_SOCK`,
  `DBUS_SESSION_BUS_ADDRESS`, `DISPLAY`, `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR`, and any `*_TOKEN`, `*_KEY`,
  `*_SECRET` except the one gateway key variable the profile names.
- Never mounted: `/run/user/<uid>`, `/var/run`, `/nix/var/nix/daemon-socket`, `~/.config/orca` (except the exact
  plugin and hook files the spec lists, read-only), the owner's `~/.config/opencode`, `~/.claude`, `~/.ssh`.
- Fail closed: a worktree of a sandbox-on project, other than its primary checkout, with no valid profile is refused
  with one line naming the worktree and the fix (`lane-control lane prepare ... --sandbox`).
- Broker: push only `HEAD:refs/heads/<lane branch>` to `origin`, no `+`; git with `--git-dir` and `GIT_COMMON_DIR`
  from the profile, `-c core.hooksPath=/dev/null -c core.fsmonitor=false`; refuse when a local or worktree scope sets
  any of `core.sshCommand`, `core.gitProxy`, `core.fsmonitor`, `core.hooksPath`, `hook.*`, `remote.*.uploadpack`,
  `remote.*.receivepack`, `gpg.program`, `gpg.*.program`, `gpg.ssh.defaultKeyCommand`, `filter.*`, `diff.external`,
  `diff.*.textconv`, `credential.*`. The owner's own global and system config is left as git reads it (lc-8h1.1).
- Tests write only to their temp dirs (`import testhome` first); real-bwrap tests skip with a reason when
  `bwrap --unshare-user --ro-bind / / true` fails (CI's Ubuntu restricts user namespaces).
- The repo is public: no hostnames, usernames or private paths; examples use `<project>`, `user@host`.
- Never run `tests/test-orca-host-install.sh`.

## Review Focus

1. The agent rewrites `<wt>/.git` (or its admin dir's `commondir`) to point at a git dir it made, then something
   outside runs git there: the files must be read-only inside, and the broker must use the profile's git dir, so
   the planted `core.fsmonitor` never runs. (Task 2 mount test; Task 4 broker test.)
2. A profile that is missing, unreadable, malformed JSON, or names a different worktree than the one the shim runs
   in: the shim refuses; it never falls back to running unsandboxed. (Task 3.)
3. PATH changed after setup so the real `opencode2` comes first: `lane started` sees no sandboxed-run record within
   30 s and stops the worker. (Task 6.)
4. A `--private` entry in each accepted form (`fixtures/customers`, `./fixtures/customers/`, `fixtures/customers/`,
   a single file, a glob with no fixed folder): the first four are hidden inside; the last is reported, not silently
   dropped. (Task 2.)
5. A request to the broker with extra or forged flags (`--from` another terminal, `--to dispatch:<other>`,
   `--environment`, `gh api`, `push --force`, a refspec to `main`): refused, logged, nothing run. (Task 4.)

---

### Task 1: Measure Orca's launch through a shim (owner's go first)

The design's one unmeasured claim. A stub, never a real agent or gateway.

**Files:**
- Create: `docs/superpowers/spikes/2026-11-27-agent-sandbox/orca-launch-probe.md` (the result, written up)
- Create: `docs/superpowers/spikes/2026-11-27-agent-sandbox/stub-opencode2` (prints `opencode v2.2.1` for
  `--version`, otherwise records argv, cwd, env names (never values) and its parent chain to a file in the scratch
  dir, then waits for input like a TUI)

- [ ] **Step 1:** Ask the coordinator for the owner's go to start one Orca worker with the stub, in a scratch
  worktree of lane-control, with the stub first on PATH through a scratch folder (no write to `~/.local/bin`).
- [ ] **Step 2:** Start it as a terminal launch and as a chat launch (the owner's tab setting decides; record which
  ran). Record: the binary Orca ran (stub or real), the env names Orca set (`OPENCODE_CONFIG_CONTENT`,
  `ORCA_AGENT_HOOK_*`, plugin paths), and whether `opencode --version` went through the stub.
- [ ] **Step 3:** With the stub run under the spike's bwrap (the shim's job later), record whether Orca still marks
  the worker ready and running, and which OpenCode store files Orca's transcript reader opens
  (`worker-read --source transcript`).
- [ ] **Step 4:** Write the findings; if the PATH shim does not reach a chat launch, say so and fall back to the
  owner's custom command (spec, open question 1). Commit by path.

### Task 2: The mount plan as a pure function

**Files:**
- Create: `core/sandbox.py`
- Test: `tests/test_sandbox.py`

**Interfaces:**
- Produces: `worktree_id(worktree: Path) -> str`; `profile_path(worktree: Path) -> Path`;
  `load_profile(worktree: Path) -> dict` (raises `SandboxError(str)` on missing, malformed, or a `worktree` field
  that is not this worktree); `private_mounts(globs: list[str], wt: Path) -> tuple[list[tuple[str, Path]], list[str]]`
  (`("tmpfs", dir)` or `("null", file)`, plus the globs that cannot be a mount); `env_for(env: dict, profile: dict)
  -> dict`; `bwrap_argv(profile: dict) -> list[str]` (everything after the bwrap path, ending with `--`).
- Profile fields: `worktree`, `admin_dir`, `common_dir`, `branch`, `project`, `agent`, `real_binary`,
  `agent_config` (path of the generated config), `plugins` (list of files), `data_dir`, `cache_dir`, `beads_dir`,
  `heavy_slots`, `tool_path` (list of store bin dirs), `private` (list of globs), `gateway_key_env`, `run_dir`.

- [ ] **Step 1: Failing tests** in `tests/test_sandbox.py`:
  `test_home_is_tmpfs_and_only_agent_config_is_back` (`--tmpfs $HOME` before `--ro-bind <agent_config>
  $HOME/.config/opencode/opencode.json`); `test_limits_and_git_pointers_are_read_only` (ro binds after the worktree's
  rw bind for `.opencode`, `opencode.json`, `opencode.jsonc`, `orca.yaml`, `.git`, common `config`, `hooks`, `info`,
  `packed-refs`, `refs`, `worktrees`, own admin `commondir`, `gitdir`, `config.worktree`, and the rw bind of the
  lane's branch folder and own admin dir after them); `test_private_forms` (the four fixed forms give one
  `("tmpfs", wt/"fixtures/customers")`, a file gives `("null", ...)`, `**/*.pem` is returned as left over);
  `test_env_allowlist` (a dict with every never-passed name plus `LITELLM_KEY` named by the profile: only allowlisted
  names and `LITELLM_KEY` remain); `test_never_mounted` (no argv element starts with `/run/user`, `/var/run`,
  `/nix/var/nix/daemon-socket`, `~/.config/orca` other than the listed files); `test_profile_for_other_worktree_is_refused`.
- [ ] **Step 2:** `python3 -m unittest tests.test_sandbox -v` → fails (no module).
- [ ] **Step 3:** Implement `core/sandbox.py`; order of binds as the spec's mount table, spike's `box_args` as the
  reference.
- [ ] **Step 4:** `test_real_bwrap_stub_agent`: when the probe works, run the spike's `stub-agent.sh` under
  `bwrap_argv` on a temp fixture (reuse the spike's fixture layout) and assert exit 0; else skip with the probe's
  reason. Run the module → PASS.
- [ ] **Step 5:** Commit `core/sandbox.py tests/test_sandbox.py`.

### Task 3: The shim

**Files:**
- Create: `core/scripts/lane-sandbox-shim`
- Test: `tests/test_sandbox_shim.py`

**Interfaces:**
- Consumes: `sandbox.load_profile`, `sandbox.bwrap_argv`, `sandbox.env_for`; `machine.conf`'s `SANDBOX_BWRAP`,
  `SANDBOX_REAL_OPENCODE`, `SANDBOX_PROJECTS` (space-separated project roots with the sandbox on).
- Produces: exit 2 and one line on refusal; on success `exec env -i <allowlist> <SANDBOX_BWRAP> <argv> <real> <args>`;
  writes `<run_dir>/started.json` (`pid`, `time`, `argv0`) before the exec.

- [ ] **Step 1: Failing tests** (fake bwrap and fake real binary from `tests/stubs/fake.py` that record argv and
  env): `test_passthrough_outside_sandbox_projects` (cwd in the main checkout, or `--version`, or `models`: the real
  binary gets the original argv and env); `test_sandboxed_lane_runs_under_bwrap` (bwrap gets the argv, env is only
  the allowlist, `started.json` written); `test_missing_profile_refused`, `test_malformed_profile_refused`,
  `test_profile_of_another_worktree_refused` (exit 2, real binary not run); `test_shim_never_finds_itself` (the shim
  as `real_binary` in machine.conf is refused).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS; `bash -n` not needed (Python).
- [ ] **Step 5:** Commit.

### Task 4: The broker and the inside wrappers

**Files:**
- Create: `core/scripts/lane-broker`, `core/sandbox-inside/orca`, `core/sandbox-inside/gh`, `core/sandbox-inside/git`
- Test: `tests/test_lane_broker.py`

**Interfaces:**
- Consumes: the profile (Task 2), plus `terminal`, `task_id`, `dispatch_id`, `repo` (owner/name) fields added by
  `prepare` (Task 5).
- Produces: `lane-broker serve <profile>` (listens on `<run_dir>/broker.sock`, one JSON request per connection:
  `{"kind": "orca"|"gh"|"push"|"fetch", "args": [...]}`, answers `{"exit": n, "stdout": s, "stderr": s}`);
  `handle(req: dict, profile: dict, run=subprocess.run) -> dict` for tests. The inside wrappers send argv and print
  the answer; `git` sends only `push` and `fetch`, and execs the real git for everything else.

- [ ] **Step 1: Failing tests:** `test_orca_mail_verbs_only` (`send`, `ask`, `check`, `reply` run with `--from`,
  `--task-id`, `--dispatch-id` forced to the profile's; `terminal create`, `worker-start`, `agent launch` refused);
  `test_orca_forged_flags_refused` (`--from term_other`, `--to dispatch:x`, `--environment`, `--pairing-code`,
  `--on`); `test_push_own_branch_only` (`push`, `push origin HEAD` → runs `git --git-dir=<admin> push origin
  HEAD:refs/heads/<branch>` with `GIT_COMMON_DIR`, `core.hooksPath=/dev/null`, `core.fsmonitor=false`;
  `--force`, `+HEAD:...`, `HEAD:main`, another remote → refused); `test_planted_git_dir_ignored` (a worktree whose
  `.git` file points at a git dir with `core.fsmonitor` naming a marker script: the push runs with the profile's
  git dir and the marker is never written); `test_repo_scope_program_key_refused` (each key in Global Constraints
  set in the local scope → refused with the key named); `test_gh_allowlist` (`pr create`, `pr view`, `pr checks`,
  `run view` with `--repo` forced; `api`, `auth token`, `secret list` refused); `test_every_request_logged`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS; `sh -n` the inside wrappers if shell.
- [ ] **Step 5:** Commit.

### Task 5: `lane prepare --sandbox` and the generated agent config

**Files:**
- Modify: `core/scripts/lane-extras` (the `lane prepare` parser near :216 and its body)
- Modify: `core/harnesses/opencode/adapter.py` (idle reader takes the profile's `data_dir` when there is one)
- Test: `tests/test_extras.py` (new class `SandboxPrepareTests`)

**Interfaces:**
- Consumes: `sandbox.worktree_id`, `sandbox.profile_path`, `sandbox.private_mounts`.
- Produces: the profile file (all fields of Task 2 and 4), the generated `opencode.json` in `run_dir`, a `{}`
  placeholder for a missing `<wt>/opencode.json` added to `.git/info/exclude`, the per-lane `data_dir`, the project
  `cache_dir`, the tool list built outside (`nix build --no-link --print-out-paths` per entry of the project's tool
  list) into `tool_path`.

- [ ] **Step 1: Failing tests:** `test_prepare_sandbox_writes_profile` (fields present, paths resolved, profile mode
  0600 in a 0700 folder); `test_placeholder_excluded_from_git`; `test_private_leftovers_reported` (a pattern-only
  glob prints one line naming it); `test_agent_config_has_no_allow_policy` (the generated config holds the owner's
  denies and lane-control's persona plugin, no `allow` anywhere); `test_prepare_refuses_without_bwrap` (no
  `SANDBOX_BWRAP` → exit 1, says to run machine setup); `test_idle_reads_profile_data_dir`.
- [ ] **Step 2–4:** FAIL, implement, PASS. **Step 5:** Commit.

### Task 6: Machine setup, check, and the after-start check

**Files:**
- Modify: `core/scripts/machine-setup`, `core/scripts/machine-check`, `core/machineconf.py`
- Modify: `core/scripts/lane-control` (new `lane started <worktree> [--timeout 30]`)
- Modify: `core/scripts/lane-heavy` (slot files in `~/.local/state/lane-control/heavy/`, out of the state folder's top)
- Test: `tests/test_machine_setup.py`, `tests/test_machine_caps.py`, `tests/test_sandbox_shim.py`

**Interfaces:**
- Produces: `machine setup --sandbox` writes `SANDBOX_BWRAP` (store path, GC root under lane-control's data folder),
  `SANDBOX_REAL_OPENCODE`, the shim links in the first PATH folder ahead of the real CLI (asks first; `--yes` for
  scripts), and refuses on a setuid bwrap or a failed probe. `machine-check` reports `sandbox: ok|off|broken
  (<why>)`, including "the shim is not first on the login shell's PATH". `lane started` exits 0 when
  `started.json` appears in time, else runs `orca-ide orchestration worker-stop` for the lane and exits 1.

- [ ] **Step 1: Failing tests:** `test_setup_pins_bwrap_store_path`, `test_setup_refuses_setuid_bwrap`,
  `test_setup_refuses_when_no_path_folder_precedes_real_cli`, `test_check_reports_shim_not_first`,
  `test_lane_started_stops_unsandboxed_worker` (stub Orca from `tests/stubs/orca-via-env` records the worker-stop),
  `test_heavy_slots_moved`.
- [ ] **Step 2–4:** FAIL, implement, PASS; `bash -n` every changed shell script. **Step 5:** Commit.

### Task 7: Coordinator doctrine and docs

**Files:**
- Modify: `orchestrators/native-orca/skills/native-orca-lanes/SKILL.md` (OpenCode lane: `lane prepare --sandbox`
  before `worker-start`, `lane started <worktree>` right after; a refusal holds the lane)
- Modify: `orchestrators/native-orca/setup.md`, `docs/how-it-fits.md`, `docs/getting-started.md` (what the
  sandbox hides, what stays open: the spec's list, in plain words)
- Test: `tests/test_content.py` (the skill names both commands in order; the docs name the three open items:
  network, coordinator unsandboxed, worktree content run outside)

- [ ] **Step 1–4:** failing content test, edit, PASS. **Step 5:** Commit.

### Task 8: Live check with the stub, then one real lane (owner's go)

- [ ] **Step 1:** Repeat Task 1's probe through the real shim, profile and broker: the stub sends `worker_done`
  through the inside `orca`, pushes its branch through the broker to a scratch remote, and the spike's stub checks
  run inside the launched pane. Record the result in the probe file.
- [ ] **Step 2:** With the owner's go, one real OpenCode lane on a small issue with the sandbox on; record in the
  lane ledger what broke. Full suite (`python3 -m unittest discover -s tests`) through `lane-heavy` before the PR.

### Task 9 (phase 2 and 3, separate issues)

File as their own issues, each with a design note before code: key proxy for the gateway key; `--unshare-net` with
pasta and Landlock's abstract-socket scope; sandboxed onboarding agent in project-setup (closes lc-8h1.2's agent
side and lc-8h1.3); Claude lanes (spec, "Claude lanes").
