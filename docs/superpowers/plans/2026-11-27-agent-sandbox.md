# Agent Sandbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** OpenCode lanes and reviewers run in their own clone under the spec's chain (systemd-run scope → pasta →
bwrap `--cap-drop ALL`), where the floor does not exist, nothing outside runs what they wrote, and every exchange goes
through one typed broker; launched by Orca's unchanged `worker-start` through a PATH shim.

**Architecture:** `lane-control lane prepare --sandbox` makes or resumes the lane's clone, its quarantine and a one-shot
ticket outside the sandbox. The shim (`opencode`/`opencode2`, first on PATH) takes the ticket, starts the broker, runs
the chain, finds the host pid by mount namespace, sets the nft filter through `nsenter`, writes the run record, then
releases bwrap's block fd. Commits leave as bundles the broker reads safely, quarantines and checks. `core/sandbox.py`
builds every argv and rule from the ticket as pure functions; an adversarial suite through the real shim and broker,
with real bwrap and pasta, is a required local gate.

**Tech Stack:** Python 3 stdlib, bubblewrap, passt, util-linux (`nsenter`), nftables from nixpkgs, unittest with
`tests/stubs/`.

**Spec:** `docs/superpowers/specs/2026-11-27-agent-sandbox-design.md` (round 3; read it whole first). The spike
(`docs/superpowers/spikes/2026-11-27-agent-sandbox/`: `spike.sh`, `stub-agent.sh`, `spike_helpers.py`) is the
reference for every step below; port its logic, do not copy its shell.

## Global Constraints

- Never privileged, never setuid; bwrap, pasta, nsenter and nft only by absolute store path from `machine.conf`
  (`SANDBOX_BWRAP`, `SANDBOX_PASTA`, `SANDBOX_NSENTER`, `SANDBOX_NFT`), only when not setuid.
- The chain, exactly: `env -i <allowlist> [DBUS_SESSION_BUS_ADDRESS XDG_RUNTIME_DIR] systemd-run --user --scope -q -p
  MemoryMax=<SANDBOX_LANE_MEMORY> -- <pasta> --config-net -q -f --no-map-gw --dns-forward 169.254.1.53 -t none -u none
  -U none -T <ports> -- bash -c 'exec "$@" 3>"$i" 4<"$b"' <info> <block> <bwrap> --cap-drop ALL --unsetenv
  DBUS_SESSION_BUS_ADDRESS --unsetenv XDG_RUNTIME_DIR <mounts> --info-fd 3 --block-fd 4 <real agent> <args>`; the
  scope only when a user bus exists. bwrap flags also `--die-with-parent --unshare-pid --unshare-ipc --unshare-uts
  --unshare-cgroup-try`, and `--new-session` unless `/proc/sys/dev/tty/legacy_tiocsti` reads `0`.
- No secret in any argv: the allowlist reaches bwrap as environment; never `--clearenv`/`--setenv` with a value.
- nft output filter (spec, "Route out"): drop the host's addresses, 169.254.0.0/16 except 169.254.1.53,
  10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, fc00::/7, fe80::/10; accept the gateway's address and
  port first when it is in one of those ranges. Set before the block fd is released.
- Only `/nix/store`, never `/nix/var`; `/run`, `/tmp`, `/etc`, `$HOME` are tmpfs with the spec's read-only files.
- Clones: `~/.local/state/lane-control/sandbox/<lane>/repo`, quarantine `.../<lane>/quarantine.git`, recorded base
  `.../<lane>/base`; per ticket `.../<lane>/t-<nonce>/{cache,data,run}`; reviewers `.../<lane>/review-<n>/repo`. No
  lane-control code runs git in a clone after hand-over (a test greps for it).
- History check: `git rev-list <delivered>` piped to `git diff-tree --stdin -r -m --root --no-commit-id --name-only
  --no-renames`; match lower-cased, any depth, normalized `--private` paths and `.env`/`.env.*` except `.env.example`,
  `.env.sample`, `.env.template`, `.env.dist`. On clone, every base bundle, reviewer and check clones.
- Push: open `O_NOFOLLOW|O_NONBLOCK`, regular file, at most 64 MB, copied; `bundle list-heads` exactly
  `refs/heads/<lane branch>`; fetch into the quarantine with `fetch.fsckObjects` and `transfer.fsckObjects`; descends
  from the recorded base and the previous push; no CI path in any new commit without `approve-ci`; then quarantine →
  shared `refs/lanes/<lane>` → `origin` lane branch, no `+`.
- CI paths: `.github/workflows/`, `.github/actions/`, `.gitlab/`, `.circleci/`, `.buildkite/`, `.gitlab-ci.yml`,
  `azure-pipelines.yml`, `Jenkinsfile`, `.travis.yml`, `bitbucket-pipelines.yml` (case-insensitive).
- Broker: typed JSON only, at most 1 MB, the spec's request names verbatim; no argv or path from the lane; ids by
  pattern and `--id=`; unknown fields refused; every request logged.
- Fail closed: no ticket, a failed check, probe or selftest → not started; one line names the fix.
- Tests import `testhome` first and write only to temp dirs. The adversarial suite runs under `LANE_SANDBOX_SELFTEST=1`,
  where a skip is a failure; CI skips it and says so.
- Public repo: no hostnames, usernames, private paths, addresses or other machine facts in code, tests or output.
- Never run `tests/test-orca-host-install.sh`.

## Review Focus

1. A shim launch where bwrap exits before writing its info file, or the host-pid lookup finds nothing or two
   processes: no run record, the block fd never released, the agent never starts. (Task 4.)
2. A base update whose new commits add then remove a private path inside a merge: the delivery is refused and the
   inbox keeps the previous bundle. (Task 5.)
3. A second push that descends from the base but not from the previous push (a rewritten branch): refused. (Task 5.)
4. `prepare` for a lane whose clone exists but is not a git repo any more (the lane deleted `.git`): refused with the
   owner's choice named (`--discard` or recreate from `refs/lanes/<lane>`), never a silent re-clone over it. (Task 3.)
5. A host where `systemd-run --user` fails at start (no user manager, or a refused scope): the chain runs without the
   scope and `machine-check` reports "no memory cap"; never a start that drops the cap silently and says nothing.
   (Task 4.)

---

### Task 1: Measurements (lc-9r81.1, lc-9r81.2, lc-9r81.3; owner's go first)

Stubs only, never a real agent or gateway; results into `docs/superpowers/spikes/2026-11-27-agent-sandbox/` as
`orca-launch-probe.md`, `uid0-probe.md`, `ci-probe.md`.

- [ ] **Step 1 (lc-9r81.1):** with the owner's go, a stub `opencode2` (answers `--version` with `opencode v2.2.1`,
  else records argv, cwd, env names, fds and parent chain) first on PATH through a scratch folder; one Orca worker as
  a terminal launch and one as a chat. Record the binary run, the env names and files Orca sets, what each Orca token
  authorizes (docs and code), the `worker-show` and `dispatch-show --task` fields naming the terminal, which OpenCode
  store `worker-read --source transcript` opens, and `orca-ide workspace cleanup preview --json` for an unchanged lane
  worktree before and after `worker-release`. Repeat with the custom command set to the stub.
- [ ] **Step 2 (lc-9r81.2):** under the spike's chain, run `opencode --version`, `opencode models` with a fake provider
  config, node, npm, git and Playwright's headless browser as uid 0 without capabilities; and try pasta attaching to a
  bwrap namespace outside Orca's FHS env and on a remote box. Record what refuses or changes.
- [ ] **Step 3 (lc-9r81.3):** for each sandbox-candidate project, `gh api` the default workflow permissions, Actions
  secrets count, runners, and the workflows' triggers; write what a lane's branch and PR would run.
- [ ] **Step 4:** commit the three files; update the spec's open questions and children.

### Task 2: `core/sandbox.py`: tickets, argv, rules and checks as pure functions

**Files:**
- Create: `core/sandbox.py`
- Test: `tests/test_sandbox.py`

**Interfaces:**
- Produces: `class SandboxError(Exception)`; `worktree_id(wt: Path) -> str`; `write_ticket(wt: Path, t: dict) -> str`;
  `take_ticket(wt: Path, terminal: str) -> dict`; `chain_argv(t: dict, conf: dict, scope: bool) -> tuple[list[str],
  dict]` (argv and the environment for `env -i`); `bwrap_mounts(t: dict) -> list[str]`; `nft_rules(host_addrs:
  list[str], gateway: tuple[str, int] | None) -> str`; `host_pid(mntns: int, nspid: int) -> int` (raises on zero or
  two matches); `history_hits(repo: Path, revs: list[str], private: list[str]) -> list[str]`; `ci_hits(repo: Path,
  revs: list[str]) -> list[str]`; `safe_read(path: Path, cap: int, dst: Path) -> None` (raises `SandboxError` with the
  reason).
- Ticket fields: `nonce`, `role` (`lane`|`reviewer`|`check`), `lane`, `issue`, `project`, `repo`, `base`, `branch`,
  `worktree`, `clone`, `ticket_dir`, `home_files`, `tool_path`, `persona`, `gateway_key_env`, `gateway`, `hook_port`,
  `review_files`, `sha`.

- [ ] **Step 1: Failing tests:** `test_chain_order_and_flags` (the Global Constraints chain, `--cap-drop ALL`,
  `-t none -u none -U none`, the info and block opener); `test_no_secret_in_argv` (a ticket and env with a gateway key
  and Orca tokens: no value appears in the argv; all appear in the env dict); `test_bus_vars_only_for_scope` (present
  with `scope=True`, bwrap unsets both by name); `test_only_nix_store` (no argv element is or contains `/nix/var`,
  `/run/user`, `~/.config/orca` except the two spec files, the shared repo); `test_clone_at_worktree_path`;
  `test_etc_minimal`; `test_new_session_only_when_tiocsti_allowed`; `test_nft_rules` (each range, the DNS accept first,
  the gateway accept before the drops, no host address when the list is empty); `test_host_pid_unique` (fake /proc
  tree: one match, none, two); `test_history_hits` (side branch added and removed then merged, nested `sub/.env.local`,
  `./x/` and `X/` forms, `.env.example` not a hit); `test_ci_hits`; `test_safe_read` (a link, a link to a directory, a
  FIFO, a 65 MB sparse file, a file that grows while read, a regular file); `test_take_ticket_*` (oldest, once, other
  worktree, malformed).
- [ ] **Step 2:** `python3 -m unittest tests.test_sandbox -v` → FAIL. **Step 3:** implement from the spike. **Step 4:**
  PASS. **Step 5:** commit `core/sandbox.py tests/test_sandbox.py`.

### Task 3: `lane prepare --sandbox`: clone, resume, quarantine, checks

**Files:**
- Modify: `core/scripts/lane-extras` (`lane prepare`, near :216)
- Modify: `core/harnesses/opencode/adapter.py` (idle reader takes the ticket's data folder)
- Test: `tests/test_extras.py` (class `SandboxPrepareTests`)

**Interfaces:**
- Consumes: `sandbox.write_ticket`, `history_hits`.
- Produces: `lane-control lane prepare <project> <worktree> --sandbox --role lanes|reviewer|check [--lane KEY]
  [--issue ID] [--sha SHA] [--review-file NAME]... [--discard]`: fresh clone + quarantine + recorded base; or resume
  (clone exists: untouched); or recreate from `refs/lanes/<lane>` (`--no-checkout`); the generated home files; the
  project's CI check (`gh api` default workflow permissions `read`, Actions secrets 0, runners 0, else refuse unless
  the project's settings store records the owner's acceptance); a ticket with its own folders. Last line: the nonce.

- [ ] **Step 1: Failing tests:** `test_fresh_clone_only_base` (another branch's commit and `refs/owner/*` absent);
  `test_resume_reuses_clone` (a second prepare after commits in the clone: same folder, its mtime and HEAD file
  unchanged, a new ticket); `test_recreate_from_pushed_branch`; `test_broken_clone_refused` (Review Focus 4);
  `test_history_refusals` (tracked private, side branch, nested `.env.local`; `.env.example` accepted);
  `test_inline_key_refused`; `test_tool_list_from_store_only`; `test_reviewer_own_folders` (clone, cache, data, run
  differ from the lane's); `test_ci_settings_refused` (stub `gh api` answers write permissions, a secret, a runner:
  each refuses; the owner's acceptance in the store lets it through); `test_refuses_without_selftest`.
- [ ] **Step 2–4:** FAIL, implement, PASS. **Step 5:** commit.

### Task 4: The shim

**Files:**
- Create: `core/scripts/lane-sandbox-shim`
- Test: `tests/test_sandbox_shim.py`

**Interfaces:**
- Consumes: Task 2's functions; `machine.conf` keys `SANDBOX_BWRAP`, `SANDBOX_PASTA`, `SANDBOX_NSENTER`, `SANDBOX_NFT`,
  `SANDBOX_REAL_OPENCODE`, `SANDBOX_PROJECTS`, `SANDBOX_LANE_MEMORY`.
- Produces: passthrough for the owner's tabs, the primary checkout, `--version`, `models`, `--help`; otherwise the
  spec's chain steps 1-6, the run record `runs/<terminal>.json` (`nonce`, `role`, `host_pid`, `mntns`) written only
  after the nft filter is set, signals forwarded, the agent's exit status returned. Refusal: exit 2, one line.

- [ ] **Step 1: Failing tests** (fake systemd-run, pasta and bwrap from `tests/stubs/fake.py` that record argv and
  env; the fake bwrap writes an info file and waits on its block fd): `test_passthrough_cases`;
  `test_no_ticket_refused`; `test_record_after_filter` (no record, no release when nsenter fails);
  `test_info_never_written` and `test_host_pid_ambiguous` (Review Focus 1); `test_no_scope_without_bus` (Review
  Focus 5); `test_no_secret_in_any_argv` (every recorded argv); `test_signals_forwarded`.
- [ ] **Step 2–4:** FAIL, implement, PASS. **Step 5:** commit.

### Task 5: The broker

**Files:**
- Create: `core/scripts/lane-broker`
- Test: `tests/test_lane_broker.py`

**Interfaces:**
- Consumes: the ticket and run record; Task 2's `safe_read`, `history_hits`, `ci_hits`.
- Produces: `lane-broker serve <ticket-file>`; `handle(req: dict, ticket: dict, terminal: str, run=subprocess.run)
  -> dict`; one handler per request in the spec's table; `lane-control lane approve-ci <lane> <sha>` (owner only:
  refuses inside a sandbox, where `ORCA_TERMINAL_HANDLE` names a sandboxed run record).

- [ ] **Step 1: Failing tests:** push with real git in temp repos: `test_push_hostile_outbox` (a link to a lane-made
  repo with alternates and a ref naming an unpushed commit, a link to a file, an id-only bundle, a FIFO, a 65 MB file,
  two refs, an orphan: each refused, the shared repo's refs and origin unchanged); `test_push_honest_then_rewritten`
  (Review Focus 3); `test_push_ci_change_needs_approval`; `test_push_never_runs_git_in_a_lane_path` (marker scripts in
  the clone's config and in a linked repo: none runs); `test_fetch_checks_history` (Review Focus 2);
  `test_report_checks_dispatch` (another terminal's dispatch refused; this terminal's follow-up accepted);
  `test_no_path_or_recipient_fields`; `test_value_as_equals_form`; `test_reply_only_seen_ids`; `test_gh_own_pr_only`;
  `test_run_view_own_branch_only`; `test_bd_show_lane_issue_and_links_only` (another id, a flag-shaped id such as
  `--db=/tmp/x` or `-C=/tmp`: refused); `test_review_read_named_only`; `test_review_write_once`; `test_roles` (reviewer
  cannot push or create; check has only `heavy-slot`); `test_size_limit`; `test_every_request_logged`.
- [ ] **Step 2–4:** FAIL, implement, PASS. **Step 5:** commit.

### Task 6: The inside wrappers

**Files:**
- Create: `core/sandbox-inside/wrap.py` and links `orca`, `orca-ide`, `gh`, `git`, `bd`, `lane-heavy`
- Test: `tests/test_sandbox_inside.py`

**Interfaces:**
- Consumes: the broker socket (`LANE_BROKER`, default `/run/lane/broker.sock`).
- Produces: the mappings of the spec ("The broker"); `git push [-u]` → bundle, `push`, then the tracking ref and
  upstream config on success; `git fetch`/`pull` → `fetch`, then from the inbox bundle (`pull --rebase` refused);
  other `git` verbs exec the real git; `gh run view <id>` → `run-view`; `lane-heavy <cmd>` holds `heavy-slot`.

- [ ] **Step 1: Failing tests** (a fake broker on a temp socket): one per mapping, `test_push_u_sets_upstream`,
  `test_status_ahead_behind_after_push`, `test_unknown_verb_refused`, `test_heavy_slot_released_on_exit`.
- [ ] **Step 2–4:** FAIL, implement, PASS. **Step 5:** commit.

### Task 7: Machine setup, check, and the adversarial selftest

**Files:**
- Modify: `core/scripts/machine-setup`, `core/scripts/machine-check`, `core/machineconf.py`, `core/scripts/lane-control`
- Create: `tests/sandbox_escapes/stub-agent.sh` (from the spike's), `tests/test_sandbox_escapes.py`
- Test: `tests/test_machine_setup.py`, `tests/test_machine_caps.py`

**Interfaces:**
- Produces: `machine setup --sandbox` (pins and GC roots for bubblewrap, passt, util-linux, nftables; shim links after
  asking; refuses setuid binaries, a failed chain probe or selftest); `machine-check` line `sandbox: on|off|broken
  (<why>)` (pins, chain probe, shim first on the login shell's PATH, `legacy_tiocsti`, user manager, selftest
  current); `lane-control sandbox selftest` → `~/.local/state/lane-control/sandbox/selftest.json`.

- [ ] **Step 1: Failing tests** in `tests/test_sandbox_escapes.py`, each through the real shim (`lane-sandbox-shim`
  with a ticket from `prepare`) and the real broker, each checked from outside: every probe of the spike's `sandbox`
  run (floor, environment, capabilities and remount of every read-only bind, nested user namespace, `nft flush`, nix
  daemon, loopback TCP and UDP, abstract socket, every host address, inbound from every host address, host argv
  scan, shared repo and foreign objects); every hostile outbox entry of the spike; resume, dirty and recreate; the
  history refusals; `test_pty_resize_and_dev_tty` (under a pty: SIGWINCH reaches the agent; `/dev/tty` opens when
  `legacy_tiocsti` is 0); `test_lane_files_not_on_host`; `test_dns_and_outbound` (only with `LANE_SANDBOX_NET=1`).
  Setup tests: `test_setup_pins_store_paths`, `test_setup_refuses_setuid`, `test_setup_refuses_without_selftest`,
  `test_check_reports_shim_not_first`, `test_check_reports_no_user_manager`.
- [ ] **Step 2–4:** FAIL, implement, PASS (`LANE_SANDBOX_SELFTEST=1 python3 -m unittest tests.test_sandbox_escapes`
  on this machine: every test runs, none skips). **Step 5:** commit.

### Task 8: Lane lifecycle commands, doctrine and docs

**Files:**
- Modify: `core/scripts/lane-control` (`lane started <worktree> --nonce <n>`, `sandbox inspect <lane>`, `sandbox close
  <lane> [--discard]`, `sandbox check <lane> --sha <sha> -- <cmd>`, `lane approve-ci <lane> <sha>`)
- Modify: `orchestrators/native-orca/skills/native-orca-lanes/SKILL.md` (a sandboxed lane: `prepare --sandbox`,
  `worker-start`, `lane started`; reviewer `prepare --role reviewer --sha`; relaunch is `prepare` again; never the
  worktree close checks, never a host rerun or `gh pr checkout`; close with `sandbox close`)
- Modify: `orchestrators/native-orca/personas/reviewer.md`, `coordinator.md` (persona at `/run/lane/persona.md`, the
  wrappers' paths, the close flow)
- Modify: `orchestrators/native-orca/setup.md`, `docs/how-it-fits.md`, `docs/getting-started.md`
- Test: `tests/test_sandbox_shim.py`, `tests/test_content.py`

- [ ] **Step 1: Failing tests:** `test_lane_started_ok`, `test_lane_started_stops_unsandboxed` (no record, a stale
  nonce, a dead pid, a mount namespace other than the record's: `worker-stop` recorded by the stub Orca, exit 1);
  `test_close_refuses_unpushed_or_dirty`, `test_close_discard`, `test_close_needs_merged_pr_or_word`;
  `test_check_runs_in_fresh_clone`; content tests: the skill names the commands in order and forbids the worktree
  close checks for sandboxed lanes; the docs name the open items (internet, the lane's key, CI, coordinator
  unsandboxed, detection not prevention).
- [ ] **Step 2–4:** FAIL, edit, PASS. **Step 5:** commit.

### Task 9: Live check with the stub, then one real lane (owner's go)

- [ ] **Step 1:** through Orca, the shim, a ticket, the broker and the wrappers, the stub agent: receives its brief,
  resolves a name, commits, merges a new base commit, runs a test through `lane-heavy`, pushes to a scratch remote,
  opens a PR on a scratch repo, reads a failed run's log, takes a follow-up dispatch, is relaunched (resume), and
  reports `worker_done`; a reviewer ticket in the same worktree reviews it; `sandbox close` ends it. Record each step.
- [ ] **Step 2:** with the owner's go and lc-9r81.4 answered, one real OpenCode lane on a small issue; record what
  broke. Full suite through `lane-heavy` and the selftest before the PR.

Phases 2 and 3 are their own issues (lc-0zae: key proxy and egress allowlist; lc-pk2c: sandboxed onboarding agent;
lc-d2wk: Claude lanes), each with a design note before code.
