# Agent Sandbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** OpenCode lanes and reviewers run in their own clone, under pasta + bubblewrap, where the floor does not
exist and nothing outside runs what they wrote, launched by Orca's unchanged `worker-start` through a PATH shim, with
one typed broker for Orca mail, git, gh, beads, review files and heavy slots.

**Architecture:** `lane-control lane prepare --sandbox` makes the lane's single-branch clone and a one-shot launch
ticket outside the sandbox. A shim named `opencode`/`opencode2`, first on PATH, takes the ticket, binds it to the
pane's terminal handle, starts the broker, and supervises `pasta -- bwrap -- <real agent>` from `env -i`, writing the
run record after bwrap's `--info-fd`. The clone is mounted at the Orca worktree's path; commits leave as bundles the
broker imports with fsck. Pure Python builds every argv from the ticket, so most tests need no bwrap; an adversarial
suite with real bwrap is a required local gate.

**Tech Stack:** Python 3 stdlib (`core/sandbox.py`, shim, broker, wrappers), bubblewrap and passt from nixpkgs,
unittest with `tests/stubs/`.

**Spec:** `docs/superpowers/specs/2026-11-27-agent-sandbox-design.md` (round 2; read it whole first). The spike
(`docs/superpowers/spikes/2026-11-27-agent-sandbox/spike.sh`) is the reference for the clone, mounts, network and
bundle flow.

## Global Constraints

- Never a privileged container or a setuid helper; bwrap and pasta only by absolute path from `machine.conf`
  (`SANDBOX_BWRAP`, `SANDBOX_PASTA`), only when not setuid, always from `env -i`.
- Only `/nix/store` is mounted, never `/nix/var`; `/run` and `/tmp` are tmpfs; `~/.config/orca` only the two
  read-only files the spec lists; the shared repo, the main checkout, the beads folder and other clones never.
- bwrap flags: `--die-with-parent --unshare-pid --unshare-ipc --unshare-uts --unshare-cgroup-try`, plus
  `--new-session` unless `/proc/sys/dev/tty/legacy_tiocsti` reads `0`. pasta: `--config-net -q --no-map-gw
  --dns-forward 169.254.1.53 -T <ORCA_AGENT_HOOK_PORT>[,<loopback gateway port>]`; `resolv.conf` inside names
  `169.254.1.53`.
- The environment inside is exactly the spec's list; nothing else whatever its name.
- Clones: `git clone --no-local --single-branch --branch <base> --no-tags`, in
  `~/.local/state/lane-control/sandbox/<lane-id>/repo` (reviewers: `.../<lane-id>/review-<n>/repo`); no lane-control
  code runs git in them after `prepare` hands them over; removed with `rm -rf`.
- Tickets: `~/.local/state/lane-control/sandbox/tickets/<worktree-id>/<nonce>.json`, one-shot (atomic rename into
  `used/`); `<worktree-id>` = first 16 hex of sha256 of the resolved Orca worktree path; nonce = 32 hex from
  `secrets.token_hex(16)`. Run records: `.../sandbox/runs/<terminal-handle>.json`.
- Broker: typed JSON requests only (the spec's table, names verbatim), at most 1 MB; no argv or path from the lane;
  text through files the broker creates (0600); option values as `--opt=value`; unknown fields refused; every request
  logged to `<lane folder>/broker.log`.
- Push: import `refs/heads/<branch>` from the bundle into `refs/lanes/<lane>` with `-c fetch.fsckObjects=true -c
  transfer.fsckObjects=true fetch --no-tags --no-recurse-submodules --no-write-fetch-head`, fast-forward only; push
  `refs/lanes/<lane>:refs/heads/<branch>` to `origin`, no `+`. Git runs only in the shared repo.
- Fail closed: no unused ticket, a bad ticket, a failed probe or a failed selftest means the agent is not started;
  one line names the worktree and the fix.
- Tests import `testhome` first and write only to their temp dirs. The real-bwrap suite runs under
  `LANE_SANDBOX_SELFTEST=1`, where a skip is a failure; CI (user namespaces restricted) skips it and says so.
- Public repo: no hostnames, usernames, private paths or machine facts; examples use `<project>`, `user@host`.
- Never run `tests/test-orca-host-install.sh`.

## Review Focus

1. A second `prepare` or relaunch while an older ticket is unused: the shim takes the oldest, binds it to this
   terminal; a ticket of another worktree or role is never taken. (Task 4.)
2. A bundle that is not a fast-forward, names extra refs, or carries a corrupt object: refused, nothing written to
   `refs/lanes` or origin, the lane told why. (Task 5.)
3. A report whose dispatch belongs to another terminal (a reviewer's, an old round's): refused; a follow-up dispatch
   of this terminal: accepted. (Task 5.)
4. A base whose history holds a tracked `--private` path in any accepted form (`x`, `./x/`, `x/`, a file) or a
   `.env.local`: `prepare` refuses and names it. (Task 3.)
5. The owner's own OpenCode tab in the primary checkout, `opencode --version`, `opencode models`: passed through
   unchanged, never sandboxed, never refused. (Task 4.)

---

### Task 1: Measure Orca's launch (owner's go first)

A stub, never a real agent or gateway.

**Files:**
- Create: `docs/superpowers/spikes/2026-11-27-agent-sandbox/orca-launch-probe.md`
- Create: `docs/superpowers/spikes/2026-11-27-agent-sandbox/stub-opencode2` (answers `--version` with
  `opencode v2.2.1`; otherwise writes argv, cwd, env names (never values), open fds and its parent chain to the scratch
  dir and waits like a TUI)

- [ ] **Step 1:** Ask the coordinator for the owner's go: one Orca worker with the stub first on PATH through a
  scratch folder (no write to `~/.local/bin`), in a scratch worktree, once as a terminal launch and once as a chat.
- [ ] **Step 2:** Record: which binary ran; the env names Orca set (`OPENCODE_CONFIG_DIR`, `ORCA_OPENCODE_*`,
  `ORCA_AGENT_HOOK_*`, `ORCA_PANE_KEY`, `ORCA_AGENT_LAUNCH*`); the files those name; whether `--version` went through
  the stub; the JSON fields of `worker-show` and `dispatch-show` that name the terminal.
- [ ] **Step 3:** For each Orca token, read Orca's docs and code for what it authorizes (hook posts only, or RPC);
  write the list. A token that reaches more than this pane's hooks goes on the spec's never-passed list.
- [ ] **Step 4:** Repeat with the stub under the spike's pasta + bwrap: Orca still marks it ready and running; mail
  typed into the pane arrives; which OpenCode store files `worker-read --source transcript` opens.
- [ ] **Step 5:** With the owner's go, the same with Orca's custom command pointing at the stub (spec open
  question 1), and the `terminal create --command` + `worker-start --terminal` fallback. Write the findings; commit.

### Task 2: `core/sandbox.py`: tickets, clone layout and argv as pure functions

**Files:**
- Create: `core/sandbox.py`
- Test: `tests/test_sandbox.py`

**Interfaces:**
- Produces: `class SandboxError(Exception)`; `worktree_id(wt: Path) -> str`; `lane_dir(lane_id: str) -> Path`;
  `write_ticket(wt: Path, t: dict) -> str` (returns the nonce); `take_ticket(wt: Path, terminal: str) -> dict` (oldest
  unused, atomic rename, raises `SandboxError`); `bwrap_argv(t: dict, tiocsti_off: bool) -> list[str]` (after the
  bwrap path, ending with `--`); `pasta_argv(t: dict, hook_port: int, gateway_port: int | None) -> list[str]`;
  `env_for(env: dict, t: dict) -> dict`.
- Ticket fields: `nonce`, `role` (`lane`|`reviewer`|`check`), `lane`, `issue`, `project`, `repo` (owner/name),
  `base`, `branch`, `worktree`, `clone`, `run_dir`, `data_dir`, `cache_dir`, `home_files` ({inside path: source}),
  `tool_path`, `persona`, `gateway_key_env`, `sha` (reviewer, check).

- [ ] **Step 1: Failing tests:** `test_only_nix_store` (`/nix/store` bound; no argv element equal to or a parent of
  `/nix/var`, `/run/user`, `/var/run`, `~/.config/orca`, the shared repo or its `.git`); `test_clone_at_worktree_path`
  (`--bind <clone> <worktree>` and nothing else under the worktree path); `test_etc_is_minimal` (`--tmpfs /etc`, then
  only the spec's names and the generated `resolv.conf`); `test_home_files_read_only` (each `home_files` entry is a
  `--ro-bind` after `--tmpfs $HOME`); `test_new_session_only_when_tiocsti_allowed`; `test_env_is_exactly_the_list`
  (an env with every never-passed name, a `FOO_TOKEN`, `GH_TOKEN`, `SSH_AUTH_SOCK`: only the spec's names remain, and
  the ticket's `gateway_key_env`); `test_pasta_args` (`--no-map-gw`, the DNS forward address, `-T` with exactly the
  hook and gateway ports); `test_take_ticket_oldest_and_once`, `test_take_ticket_other_worktree_refused`,
  `test_take_ticket_malformed_refused`.
- [ ] **Step 2:** `python3 -m unittest tests.test_sandbox -v` → FAIL (no module).
- [ ] **Step 3:** Implement; the spike's `box()` is the reference order.
- [ ] **Step 4:** Run → PASS. **Step 5:** Commit `core/sandbox.py tests/test_sandbox.py`.

### Task 3: `lane prepare --sandbox`

**Files:**
- Modify: `core/scripts/lane-extras` (the `lane prepare` parser near :216 and its body)
- Modify: `core/harnesses/opencode/adapter.py` (idle reader takes the ticket's `data_dir`)
- Test: `tests/test_extras.py` (new class `SandboxPrepareTests`)

**Interfaces:**
- Consumes: `sandbox.write_ticket`, `sandbox.lane_dir`.
- Produces: `lane-control lane prepare <project> <worktree> --sandbox --role lanes|reviewer|check [--lane KEY]
  [--issue ID] [--sha SHA]` → the clone (lanes: the base; reviewer and check: `refs/lanes/<lane>` at `--sha`), the
  generated home files (gitconfig with the owner's name and email only, `opencode.json` with the provider entry and
  no inline key, `resolv.conf`, persona, skills), the per-lane data and cache folders, the tool list (from the
  project's settings store, never the worktree) built with `nix build --no-link --print-out-paths`, and a ticket.
  Prints the nonce on its last line.

- [ ] **Step 1: Failing tests:** `test_clone_has_only_the_base` (another branch's commit and a ref under
  `refs/owner/` are absent from the clone: `git cat-file -e` fails); `test_tracked_private_refused` (each accepted
  form, and a file, tracked anywhere in the base's history → exit 1 naming the path); `test_tracked_env_refused`
  (`.env`, `.env.local`); `test_inline_key_refused` (an `apiKey` literal in the owner's provider config → exit 1, the
  value never printed); `test_tool_list_from_store_only` (a `tools` file in the worktree is ignored);
  `test_reviewer_gets_its_own_clone_at_sha`; `test_ticket_fields`; `test_refuses_without_selftest_record`
  (Task 7's record missing or older than the pins → exit 1); `test_idle_reads_ticket_data_dir`.
- [ ] **Step 2–4:** FAIL, implement, PASS. **Step 5:** Commit.

### Task 4: The shim

**Files:**
- Create: `core/scripts/lane-sandbox-shim`
- Test: `tests/test_sandbox_shim.py`

**Interfaces:**
- Consumes: `sandbox.take_ticket`, `bwrap_argv`, `pasta_argv`, `env_for`; `machine.conf` keys `SANDBOX_BWRAP`,
  `SANDBOX_PASTA`, `SANDBOX_REAL_OPENCODE`, `SANDBOX_PROJECTS`, `SANDBOX_LANE_MEMORY`.
- Produces: passthrough (`os.execv` of the real binary with the original argv and env) outside sandbox-on projects,
  in a primary checkout, and for `--version`, `models`, `-h`/`--help`; otherwise it stays the parent: starts
  `lane-broker serve <ticket>`, runs `env -i [systemd-run --user --scope -p MemoryMax=...] <pasta> ... -- <bwrap>
  --info-fd 3 ... -- <real> <args>`, reads `child-pid` from fd 3, writes `runs/<terminal>.json` (`nonce`, `role`,
  `pid`, `mntns` = `os.readlink(f"/proc/{pid}/ns/mnt")`), forwards SIGINT, SIGTERM, SIGHUP and SIGWINCH, and exits with
  the child's status. Refusal: exit 2 and one line.

- [ ] **Step 1: Failing tests** (fake bwrap, pasta and real binary from `tests/stubs/fake.py` that record argv, env
  and fds, and a fake bwrap that writes `{"child-pid": <pid>}` to fd 3): `test_passthrough_cases` (the five in
  Review Focus 5); `test_no_ticket_refused`; `test_ticket_bound_to_terminal`; `test_run_record_after_info_fd` (no
  record when the fake bwrap exits before writing fd 3); `test_env_i_before_pasta` (pasta's own env holds no
  `FAKE_OWNER_TOKEN`); `test_shim_never_its_own_real_binary`; `test_signals_forwarded`.
- [ ] **Step 2–4:** FAIL, implement, PASS. **Step 5:** Commit.

### Task 5: The broker

**Files:**
- Create: `core/scripts/lane-broker`
- Test: `tests/test_lane_broker.py`

**Interfaces:**
- Consumes: the ticket; the run record (bound terminal).
- Produces: `lane-broker serve <ticket-file>` (listens on `<run_dir>/broker.sock`); `handle(req: dict, ticket: dict,
  terminal: str, run=subprocess.run) -> dict` (`{"exit": n, "stdout": s, "stderr": s}`), one handler per request name
  in the spec's table, refusing a request the ticket's role may not make.

- [ ] **Step 1: Failing tests** (`run` is a recorder; nothing real is sent): `test_report_checks_dispatch` (a
  dispatch that `dispatch-show` gives another terminal → refused; this terminal's follow-up dispatch → accepted);
  `test_no_path_crosses` (a `body` that is a floor path is sent as text through the broker's own file, never read;
  requests with `body_file`, `file`, `attach`, `template` fields → refused); `test_no_recipient_field` (`to`,
  `environment`, `on`, `pairing_code` → refused); `test_reply_only_to_seen_ids`; `test_value_as_equals_form` (a
  subject `--to=bot:x` stays one `--subject=--to=bot:x` argument); `test_gh_own_pr_only` (the PR number comes from
  `gh pr list --head <branch>` in the ticket's repo; `number`, `repo`, `url` fields → refused; `api`, `auth`,
  `secret`, `web`, `editor` → refused); `test_push_import_with_fsck_fast_forward_only` (real git in temp repos:
  a fast-forward bundle → `refs/lanes/<lane>` and origin's branch; a non-fast-forward, a bundle with an extra ref,
  a corrupt object → refused, nothing written); `test_push_never_runs_git_in_the_clone` (a clone whose config sets
  `core.fsmonitor` to a marker script: the push leaves no marker); `test_bd_own_issue_only` (note, claim on another
  issue → refused; `close`, `delete`, `remember` → refused; the DB path comes from lane-control's record, a `.beads`
  link in the clone is ignored); `test_review_names` (`../x.md`, `x/y.md`, an existing name for `review-write` →
  refused); `test_reviewer_cannot_push`; `test_check_role_has_no_requests`; `test_size_limit`;
  `test_every_request_logged`.
- [ ] **Step 2–4:** FAIL, implement, PASS. **Step 5:** Commit.

### Task 6: The inside wrappers

**Files:**
- Create: `core/sandbox-inside/{orca,orca-ide,gh,git,bd,lane-heavy}` (one Python file, `core/sandbox-inside/wrap.py`,
  and links named after each command)
- Test: `tests/test_sandbox_inside.py`

**Interfaces:**
- Consumes: the broker socket at `/run/lane/broker.sock` (path from `LANE_BROKER`, default that).
- Produces: `orca`/`orca-ide orchestration send --type worker_done ...` → a `report` request (subject, body read from
  the given `--body` or `--body-file` inside the sandbox, outcome, files); `ask`, `check`, `reply`, `escalation`
  likewise; `gh pr create|view|checks|diff|comment|edit` → the matching request; `git push` → `git bundle create
  /run/lane/outbox/push.bundle <branch> ^origin/<base>` then `push`; `git fetch`/`pull` → `fetch`, then `git fetch
  /run/lane/inbox/in.bundle` (and `merge` for pull); `pull --rebase` refused; every other `git` verb execs the real
  git; `bd show|ready|update --claim|comment|create` → the bd requests; `lane-heavy <cmd>` → holds `heavy-slot`
  while `<cmd>` runs. Anything else: exit 2 with one line naming the broker request list.

- [ ] **Step 1: Failing tests** (a fake broker on a temp socket): one test per mapping above, and
  `test_unknown_verb_refused`, `test_pull_rebase_refused`, `test_heavy_slot_released_on_exit`.
- [ ] **Step 2–4:** FAIL, implement, PASS. **Step 5:** Commit.

### Task 7: Machine setup, check, selftest

**Files:**
- Modify: `core/scripts/machine-setup`, `core/scripts/machine-check`, `core/machineconf.py`, `core/scripts/lane-control`
- Create: `tests/sandbox_escapes/` (the adversarial stub, grown from the spike's `stub-agent.sh`), `tests/test_sandbox_escapes.py`
- Test: `tests/test_machine_setup.py`, `tests/test_machine_caps.py`

**Interfaces:**
- Produces: `machine setup --sandbox` (pins bwrap and passt, GC roots, shim links in the first PATH folder ahead of
  the real CLI after asking; refuses a setuid binary, a failed probe, or a failed selftest); `machine-check` line
  `sandbox: on|off|broken (<why>)` (probe, pins present, shim first on the login shell's PATH, `legacy_tiocsti`,
  selftest record current); `lane-control sandbox selftest` runs `tests/test_sandbox_escapes.py` with
  `LANE_SANDBOX_SELFTEST=1` and writes `~/.local/state/lane-control/sandbox/selftest.json` (pins, lane-control
  version, time, result).

- [ ] **Step 1: Failing tests** in `tests/test_sandbox_escapes.py` (real pasta + bwrap; each attack is tried inside,
  then checked from outside; skip only without `LANE_SANDBOX_SELFTEST=1`, fail when it is set and a probe fails):
  `test_floor_absent` (the spike's floor list, a link, a search); `test_no_owner_env_anywhere` (incl.
  `/proc/1/environ`); `test_sockets` (nix daemon, host loopback not forwarded, abstract socket, session bus path,
  the forwarded port works); `test_shared_repo_absent_and_foreign_objects_missing`; `test_nested_repo_gitlink`
  (B2: after the run, host `git status` in the Orca worktree, the main checkout and a fresh clone of the pushed branch
  leave no marker); `test_beads_hooks_unreachable` (B3: no `.beads` inside; a planted `.beads/hooks/on_update` in the
  clone is never run by a host `bd update` through the broker); `test_shared_objects_untouched` (B4: the shared
  repo's `git fsck` and object count before and after a run that tries `rm -rf` of every object path it can name);
  `test_tracked_private_refused` (B5); `test_two_runs_after_rename` (B6: run 1 renames folders in the clone, run 2
  still sees no private data); `test_lane_files_not_on_host` (B7: `.claude/settings.local.json`, `.mcp.json`,
  `.envrc` written inside are absent from the Orca worktree after the run); `test_follow_up_dispatch_and_second_agent`
  (B8: a reviewer ticket in the same worktree gets its own clone and cannot push); `test_pty_resize_and_dev_tty`
  (M2: under a pty, SIGWINCH reaches the child and `/dev/tty` opens when `legacy_tiocsti` is 0);
  `test_dns_and_outbound` (skipped unless `LANE_SANDBOX_NET=1`, then name lookup and a TCP connect work); setup tests
  `test_setup_pins_store_paths`, `test_setup_refuses_setuid`, `test_setup_refuses_without_selftest`,
  `test_check_reports_shim_not_first`.
- [ ] **Step 2–4:** FAIL, implement, PASS (selftest with `LANE_SANDBOX_SELFTEST=1` on this machine: every test runs,
  none skips). **Step 5:** Commit.

### Task 8: After-start check, sandboxed checks, doctrine and docs

**Files:**
- Modify: `core/scripts/lane-control` (`lane started <worktree> --nonce <n> [--timeout 30]`, `sandbox check <lane>
  --sha <sha> -- <command>`)
- Modify: `orchestrators/native-orca/skills/native-orca-lanes/SKILL.md` (sandboxed lane: `prepare --sandbox`, then
  `worker-start`, then `lane started`; reviewer: its own `prepare --sandbox --role reviewer --sha`; never rerun a
  sandboxed lane's checks on the host: `sandbox check` or CI; never check out its branch)
- Modify: `orchestrators/native-orca/personas/reviewer.md`, `.../coordinator.md` (same rules, short)
- Modify: `orchestrators/native-orca/setup.md`, `docs/how-it-fits.md`, `docs/getting-started.md`
- Test: `tests/test_sandbox_shim.py` (`lane started`), `tests/test_content.py`

- [ ] **Step 1: Failing tests:** `test_lane_started_ok` (record with the nonce, live pid, other mount namespace,
  terminal equal to `worker-show`'s); `test_lane_started_stops_unsandboxed` (no record in time, a stale record of an
  older nonce, or the shim's own mount namespace → `orchestration worker-stop` recorded by the stub Orca, exit 1);
  `test_sandbox_check_runs_in_fresh_clone`; content tests: the skill names `prepare --sandbox`, `worker-start`,
  `lane started` in that order, forbids a host rerun and `gh pr checkout` for sandboxed lanes; the docs name the open
  items (internet and LAN, the lane's key, coordinator unsandboxed, detection not prevention with the PATH shim).
- [ ] **Step 2–4:** FAIL, edit, PASS. **Step 5:** Commit.

### Task 9: Live check with the stub, then one real lane (owner's go)

- [ ] **Step 1:** Task 1's probe through the real shim, ticket, broker and wrappers: the stub receives its brief,
  resolves a name, commits, merges a new base commit, runs a test through `lane-heavy`, pushes to a scratch remote,
  opens a PR on a scratch repo, takes a follow-up dispatch, and reports `worker_done`; a reviewer ticket in the same
  worktree reviews it. Record each step in the probe file.
- [ ] **Step 2:** With the owner's go, one real OpenCode lane on a small issue with the sandbox on; record what
  broke in the lane ledger. Full suite (`python3 -m unittest discover -s tests`) through `lane-heavy` and the selftest
  before the PR.

Phases 2 and 3 are their own issues (lc-0zae: key proxy and egress allowlist; lc-pk2c: sandboxed onboarding agent;
lc-d2wk: Claude lanes), each with a design note before code.
