# Running lane-control on several machines (design proposal)

Status: proposal, nothing implemented. Written 2026-10-31 against Rove 0.9.274, bd 1.0.4 and the herdr and Claude Code CLIs as installed. Generic on purpose: `<ws>` is a machine that goes down at end of day or week (the workstation), `<box>` is a machine that stays up (a small always-on server). Tags: **[exists]** works today, **[build]** must be written, **[unknown]** not verifiable from the docs or the CLI; try it in the pilot.

## 0. Summary

One rule carries the design: **a project has exactly one home machine at a time.** Its coordinator, its lanes, its beads writes and its merges all happen there. A *home lease* says where. The owner's leaving and returning moves the lease for the projects that are eligible to run at night on `<box>`; everything else stays on `<ws>` and stops when `<ws>` does. Machines never share a live session or a live database; they share **git, the beads Dolt remote and a lease file**, and the hand-off is the existing cold-start protocol (handover memory, parked lanes, fresh coordinator) made explicit.

- Coordinators and lanes are **local to their home machine**; no cross-machine lane control.
- Budget stays account-wide; each machine runs its own guard for its own coordinators; one feed command merges the machines' usage samples.
- `<box>` runs scripts (watches, night watch, guard) always and a project coordinator only for leased projects. A machine coordinator on `<box>` is optional and not needed for the pilot.
- The minimal first step (section 8) is one data-free, lightly built project, handed off by hand for one night with the current scripts plus a feed and config change; the lease script comes second.

## 1. Placement

| Role | Where | Why |
|---|---|---|
| Owner-facing machine coordinator (report, budget judgment, playbook upkeep, top-tier approval) | `<ws>` (one, "global" while it is up) | The owner talks to it by day; it reads `<box>` through `--machine` (section 3). It is judgment only and may be down for a weekend. |
| Machine coordinator on `<box>` | **Optional, off at first.** Add it when `<box>` hosts two or more project coordinators overnight or over a weekend | An always-on Opus session is a standing cost; with 2 cores nothing needs arbitrating. Deterministic jobs are scripts and need no model. |
| Watches and routines (`window-guard.sh`, `machine-watch.sh`, `machine-night-watch.sh`, `stale-claims.sh`, `park-check.sh`) | **Every machine, for the coordinators leased to it** | They are token-free and message only local coordinators; each machine's config lists only its own tasks. |
| Project coordinator | Its **home machine** per the lease. One Rove main task per (project, machine); the one on the other machine is dormant | Rove tasks, sessions and worktrees are per daemon. A coordinator cannot move; the *role* moves by waking the dormant task there. |
| Lanes (all tiers) | **Local to their coordinator** | `spawn --parent` and `update --parent` refuse a parent on another machine (`REMOTE_PARENT_UNSUPPORTED`); a remote lane has no parent reply link; a lane on a machine that goes down dies with its context. `add --machine` can create a remote top-level task but is not used for lanes. |
| Private data (a corpus, local-only datasets) | Stays on `<ws>` | See section 6. |

Eligibility is a per-project fact recorded in the instance config: `HOME_ALLOWED=<ws>` (data-bound or heavy-build projects; they stop when `<ws>` stops) or `HOME_ALLOWED="<ws> <box>"` (may move). A **mixed project** (private data for some issues only) can still move if the issues that need the data carry a label such as `needs-ws`; the coordinator on `<box>` takes only `bd ready` issues without it. Code-only swift lanes (sandboxed, no private data by construction) are the natural night work on `<box>`.

Why not a global coordinator on `<box>` that drives lanes on `<ws>` by day: lanes would stop when `<ws>` sleeps, and every `send`/report would cross SSH (section 3). Why not one global machine coordinator on `<box>`: it would sit idle most of the day and pay a cold cache whenever the owner asks; the owner's day conversation belongs on `<ws>`.

## 2. Hand-off

### 2.1 Home lease **[build]**
- One small file per project in the **instance repo** (private, already cloned on both machines): `leases/<project>` containing `home=<machine-id> epoch=<n> since=<iso> reason=<eod|eow|back|failover>`.
- Taking or releasing the lease is a commit plus `git push`. A push is an atomic compare-and-swap: if the other side moved the file first, the push is rejected and the taker stops. `epoch` increases on every change (fencing token).
- `scripts/lease.sh show|check|take|release <project>`. `check` is what everything else calls: "do I hold the lease and is it the newest epoch I can see (after a `git fetch`)?". When the remote is unreachable, `check` answers *held* for a lease this machine took itself (so a network blip does not stop work) and *not held* otherwise; takeover from an unreachable peer needs the owner (section 7).
- Why not a Rove or beads field: both are local databases without a compare-and-swap across machines. Git is already the shared, reachable, versioned channel. The beads `handover-latest` memory carries the content; the lease only says who may act on it.

### 2.2 End of day (`<ws>` to `<box>`), per eligible project
Trigger: the owner says "leaving" (as today); the machine coordinator on `<ws>` runs the sequence for each project in `NIGHT_PROJECTS` that has work, in parallel.
1. **Drain.** Project coordinator: no new launches; running lanes finish or are parked per `issues.md` Parking (label, `Resume (...)` note, branch pushed, handover entry; `park-check.sh` verifies). Lanes that will not finish within a short drain window (config, default 30 min) are parked, not left running. *In-flight lanes do not continue remotely*: their sessions and worktrees live on `<ws>`; the Resume note and the pushed branch are the whole state, and a fresh lane on `<box>` resumes from them (sandboxed lanes: `OPENCODE_SANDBOX_PR_BRANCH`).
2. **Persist.** The coordinator merges what is ready, then: `git push` of every branch it cares about; `bd remember ... --key handover-latest`; `bd dolt commit` and `bd dolt push` (memories live in the Dolt DB, so the push is not optional); a clean main checkout (`git status`).
3. **Verify (deterministic, a script).** `handoff-check.sh <project>`: no in-progress issue without a live task or a `parked` Resume note, `park-check.sh` clean for the project, origin has every Resume branch and commit, beads pushed (local Dolt head equals remote head), checkout clean. Any failure goes back to the coordinator; no lease change happens on a failing check.
4. **Release and take.** `lease.sh release` then `lease.sh take` from `<box>` (over `ssh`, or `rove api ... --machine` for the trigger; the take runs on `<box>`).
5. **Start cold on `<box>`.** Over SSH, `<box>` runs `git fetch`/`pull --ff-only` in the project checkout, `bd dolt pull`, then wakes the dormant project coordinator with a standard resume prompt: `lease epoch <n>: you are home; read bd recall handover-latest, rove api context, bd ready --label parked; resume parked issues first`. By the context protocol (rule 1) it starts fresh from the handover; no compaction is needed on either side because the old session simply stops being used. Pull-before-ready is the guard against a stale clone.
6. **Quiesce `<ws>`.** The dormant coordinator on `<ws>` is not prodded: `my_projects()` (section 5) filters it out of the guard, the night watch and the stale/park checks. The owner's machine can then shut down.

### 2.3 End of week and back
- End of week is the same sequence; the only difference is duration. For a weekend, the "machine coordinator on `<box>`" question (section 1) is decided by how much judgment the project needs unattended; `<box>` coordinators already handle reports, merges on a fully evidenced gate, and gates for the owner.
- **Back** (owner says "back" on `<ws>`): reverse, per project: the `<box>` coordinator drains (parks lanes; the owner may say "keep going" and leave a project on `<box>`, which is just not moving its lease), persists, verifies, releases; `<ws>` takes, pulls git and beads, wakes its dormant coordinator cold. The morning report on `<ws>` reads `<box>`'s deliveries and gates first (section 3).
- A project may also *live* on `<box>` (always-on by design, owner reaches it through the sidebar): the lease just never moves. This is the owner's "one project kept alive all night" case and costs nothing extra.
- Cost note: each hand-off is one cold coordinator start (uncached context) per project per direction. The design avoids more than two a day per project.

### 2.4 What goes through which channel
| State | Channel | Notes |
|---|---|---|
| Code, branches, PRs, AGENTS.md, project settings | git (origin) | Pushed before release; pulled `--ff-only` after take. |
| Issues, memories, handover, parked labels | beads Dolt remote (`bd dolt push` / `pull`) | Promotes the current *backup* remote to the sync channel. Cell-level merges exist in Dolt, but with a single home at a time every pull should fast-forward; a non-fast-forward is a split-brain alarm (section 7), not something to merge silently. How a fresh machine bootstraps its first copy (clone of the beads repo, `bd dolt pull` into it) is **[unknown]**; the pilot tests it. |
| Who is home | lease file in the instance repo | Section 2.1. |
| Playbook, config examples, scripts | lane-control git | Same checkout on every machine. |
| Instance files kept outside git today (`~/.config/rove` project init scripts, worker rules, routing and reliability links, the ceiling file) | **[build]** move into the instance repo and symlink on each machine | Today they exist only on `<ws>`; a second machine cannot run a project without its init script and worker rules. |
| Sessions, transcripts, engine conversations | Not moved | The handover memory is the only carrier; this is deliberate (Principle 1). |

## 3. Rove across machines

### 3.1 What exists (cited from `rove machine --help`, `rove api schema --all`, `references/api-flags.md`)
- **Register a machine over SSH [exists]:** `rove machine add <ssh-target>` (verifies it and offers to install Rove there), `doctor`, `list`, `remove`. Its tasks appear in the sidebar under a row of their own.
- **Global `--machine ALIAS` on every verb [exists]:** "Run the verb in that registered machine's daemon; paths are paths there. A `--task-id` on another machine goes there without it." So `rove api send --task-id <remote coordinator> ...` and `rove api context --machine <box> --repo <path on box>` should work from `<ws>` with no wrapper. Individual verbs say so: `tab-revive` ("a task on another registered machine runs there, like every verb"), `worktree-files`, `repo-list --machine`, `github-status --machine`.
- **Reverse tunnel [exists]:** `rove machine tunnel <alias> [--install]` keeps an SSH reverse tunnel so `<alias>` can reach *this* machine (for a `<ws>` behind NAT); then on `<alias>`: `machine add <you>@127.0.0.1:<port>`. Registration can therefore be symmetric (each side drives the other), which `<box>` needs to message `<ws>` coordinators.
- **What a peer hub may run here [exists]:** `rove machine inbound [allow|deny <class>]`; classes seen: discover, read, create, drive, edit, issues, workitems, routine, lifecycle, worktree, feedback, land, credentials (default: all allowed). Use it to harden `<box>` (section 6).
- **Repo placement [exists]:** `add --repo <host/owner/name> --clone` (with `--machine`) clones into `~/.rove/repos/<host>/<owner>/<name>` when absent; `NO_REMOTE_CHECKOUT`, `FORK_MATCH`; `rove machine primary` picks the checkout a repo id resolves to.
- **Landing from a remote task [exists]:** `publish` pushes the branch and opens the PR ("what `land` does for a task on another machine").
- **GitHub identity [exists]:** `github-status`, `github-login`, and `github-sync --to <alias>` (copies this machine's `gh` token to a registered machine). Convenient but copies the owner's full token; see section 6.
- **Limits [exists]:** parent/child links and multi-repo tasks do not cross machines (`REMOTE_PARENT_UNSUPPORTED`, `REMOTE_EXTRA_UNSUPPORTED`); `watch` is CLI-only.
- **Routines [exists, locally]:** `routine-create` takes a five-field cron "in the daemon host's local time", `--script` (no tokens), `--precheck`, `--target-task/--target-tab`. The night watch and the daily checks are installed on *each* machine's own daemon; set `<box>`'s timezone to the owner's.

### 3.2 Unknown, to test in the pilot
1. `send` to a remote task: does the `[ROVE PEER]` prefix's baked-in reply command (`--task-id <sender> --tab <tab>`) route back across machines, and does `--expect-report` / `report --delivery` work across them? Are task ids unique across daemons?
2. Does a remote `gate-raise` show in the owner's inbox on `<ws>` at all (the web inbox already errors on gates, Rove discussion #1536; chat repetition stays the rule), and do `gates --machine <box>` and `deliveries --machine <box>` return what the morning report needs?
3. `routine-create --machine <box>` (create the routine in `<box>`'s daemon from `<ws>`) versus running it once over `ssh`.
4. Behaviour of every verb while a machine is down (how long a call hangs, what error). Scripts need short timeouts either way.
5. Rove daemon "keeps the daemon alive" for enabled routines; confirm it survives logout on NixOS (user service plus linger).

### 3.3 Coordinator messaging across machines
Principle: **cross-machine messages are rare, terse and pull-based.** Coordinators on different machines never need to talk in steady state (a project is homed on one). The cross-machine traffic is: hand-off orders, budget orders from a local guard (local, not cross-machine), the morning pull, `REQ ... top` to the global machine coordinator.
Options, in order of preference:
1. **`rove api send --task-id <id>`** (cross-machine routing by task id, or `--machine`) **[exists, reply path unknown]**. Same protocol, same provenance, same `deliveries`.
2. **`ssh <box> rove api send --task-id <local id> ...`** **[exists]**: the remote daemon's local API, needing only key SSH. Robust fallback and what the lease/hand-off scripts use, because it does not depend on machine registration or cross-daemon task ids.
3. **Mailbox by git/beads** **[exists]**: an issue or a handover entry is read at the next wake. Right for anything that can wait for a cold start (e.g. a `REQ` when the global machine coordinator is down).
4. **herdr** **[exists on this workstation]**: `herdr --machine <label> agent prompt <name> --wait` drives agents on a saved SSH machine (the owner's earlier setup). An alternative transport for panes the owner runs in herdr; not a Rove task channel, so provenance and delivery tracking are lost. Keep as a fallback, not the design.
5. **Claude Code Remote Control** (`claude --remote-control [name]`, session name prefix defaults to the hostname) **[exists]** lets the owner reach a running session from claude.ai or a phone. It is an owner-to-session channel, not an agent-to-agent one. Whether a Rove-hosted engine tab can be launched with it (via the tier's engine command) is **[unknown]**. Good for "check the `<box>` coordinator from the phone".

If a message targets a machine that is down: the sender treats it as not delivered (scripts already ignore failed sends) and writes the item to the project's beads (`FYI`/note) so the next wake sees it.

## 4. Shared state and budgets

### 4.1 The Claude feed
- The plan is account-wide. The feed is written by the status line from the `rate_limits` field of a running session's own response **[exists]**: it is the *account's* weekly and window percentages as of that session's last API response, appended to the *local* `~/.claude/usage-history.jsonl`. So every machine's file holds the same underlying numbers, but only as fresh as the last active session **on that machine**; an idle machine's feed lags real usage when another machine is spending.
- `BUDGET_USAGE` is already "a command printing `<weekly%> <window%>`" and already takes the max of recent samples within the current reset. So the fix is **config only [build, small]**: a feed command that concatenates the local tail and each peer's tail (`ssh -o ConnectTimeout=5 <peer> 'tail -60 ~/.claude/usage-history.jsonl'`, failing to nothing), then applies the same `jq`. Weekly use is monotonic within a weekly reset, so max over the union is correct; the window figure uses the sample with the newest `session_reset`. The failure behaviour stays: an unreadable feed reads `100 100` and blocks launches.
- Add a **freshness guard [build, small]**: `budget_usage` also returns the sample age; if the newest sample is older than a set time while a lane is `working`, say so in the prod ("feed stale") instead of trusting it.
- A machine with a *different* Claude login (a second account on `<box>`) is simply a second named budget (`[box]=...`), with its own feed and ceiling, mapped to the tiers that use it. No script change.

### 4.2 Ceilings and steps
- The ceiling file is owner-written weekly. Keep it **in the instance repo** and `git pull` it at the start of the guard loop and of each night watch run **[build, small]**; the file gains `week_to=<epoch>`; past that date, scripts fall back to `DEFAULT_CEILING` (lower than the owner's raise, so a stale file errs on the safe side). Without this, a ceiling lowered on `<ws>` is not seen by a `<box>` that has been up all week.
- The steps (`STEP_*`, window steps) apply unchanged: because the percentages are account-wide, each machine's guard reaches the same decision on its own, and each only messages the coordinators leased to it. No cross-machine budget orders are needed.
- **Allowance** (project caps, priorities) stays with the project, not the machine: the caps in the routing file already say how many lanes per project; a project lives on one machine at a time, so per-project caps are unchanged and the sum across machines is the sum of the caps of the projects that are up. Cross-project arbitration happens by *placement* (who is eligible to run on `<box>` at night); there is no runtime negotiation between machines. If priorities must change while `<ws>` is down, the `<box>` coordinators follow the routing file as last synced.
- Cheap-tier budgets with no feed (the third-party plan) remain manual; add `<box>` lane use to what the owner reads from the vendor console.

### 4.3 Who runs the guard
Every machine runs its own `window-guard.sh` (state file is already local and persistent under the XDG state dir) for `my_projects()`. Never one guard messaging remote coordinators: a down link would silently drop orders. Duplicate orders are impossible because each coordinator is leased to exactly one machine.

### 4.4 Keys and secrets
| Secret | Rule |
|---|---|
| Third-party lane key (opencode provider, `~/.config/opencode-sandbox/auth.json`) | **One key per machine** if the vendor allows several (revocation and attribution per box); else the same key delivered out of band. Never in git or in a message. Delivery: `scp` once, or a NixOS secrets mechanism (sops-nix or agenix) so the key is a root-managed file mode 600 and a `<box>` rebuild reproduces it. The sandbox already reads the bare key as a file of its own. |
| Claude login | Per machine, interactive login once, or a long-lived token from `claude setup-token` **[unknown, verify the flow in the CLI first]**. Not copied from `<ws>`'s `~/.claude`. |
| GitHub access | Prefer a fine-grained token or per-repo deploy keys limited to the pilot repos over `github-sync` (which copies the owner's full `gh` token). |
| Dolt remote access | A credential scoped to the beads remote; same rule. |
| SSH between machines | Dedicated keys, `<ws>` to `<box>` (and `<box>` to `<ws>` only through the reverse tunnel if wanted), not the owner's personal key; key-only, no agent forwarding by default. |
| Anything in committed files | No credentials, hostnames or addresses; the instance repo is private but stays generic where this repo is concerned. |
Treat `<box>` as lower trust than `<ws>`: it is up all the time and reachable from the network. Do not give it the owner's whole account.

## 5. Watches, routines and modes per machine

- **Mode file per machine.** `MODE_FILE` is machine-local. On `<ws>` it keeps its meaning (owner present or away). On `<box>`, the owner is never at the keyboard, so `<box>` runs `night` whenever it hosts a leased project **[build, trivial]**: the lease script writes `night` to `<box>`'s mode file on take and removes the unattended regime on release. In the daytime that costs only the hourly script, which prods only when something needs a coordinator. The owner's "leaving"/"back" is a `<ws>` event that triggers hand-offs; it is no longer a single global switch.
- **`my_projects()` [build, small].** One function in `lib.sh` returning the projects whose lease is held here (from `lease.sh check`), or all configured projects when no lease files exist (single-machine behaviour unchanged). `window-guard.sh`, `machine-night-watch.sh`, `stale-claims.sh` and `park-check.sh` iterate it instead of `${!COORD_TASK[@]}` and `${!COORD_REPO[@]}`. This is the whole change that stops a dormant coordinator being prodded.
- **Per-machine config.** Same `config.example.sh`, one instance file per machine: its own `COORD_TASK` (task ids differ per daemon), `COORD_REPO`, `COORD_SLOTS` (1 on a 2-core box), `MEM_LOW_GB` (e.g. 1), `SWAP_HIGH_GB`, `NIGHT_MEM_MIN_GB` (2-4 on a small box), tier mapping (swift only on `<box>` to start). Thresholds and ceilings are otherwise shared.
- **`machine-watch.sh` on `<box>`** has no machine coordinator to wake. Replace the single wake (`exit 0`) with an optional `WATCH_NOTIFY=coordinators` mode **[build, small]**: message the leased coordinators with a one-line "memory low: pause your heaviest lane" order, then keep watching (re-arm itself).
- **Night watch on `<box>`** works as is: it reads `rove api context` of local repos, prods only when needed, runs the compact-before-cold step on its first run each night. After a lease take the first-run marker would skip the compact for a coordinator that has just started cold; harmless, because it has nothing to compact.
- **Cron time zone.** Rove cron runs "in the daemon host's local time"; `<box>` is set to the owner's time zone so the hourly night window and the `-7 hours` night key line up.
- **Daily checks** (`stale-claims.sh`, `park-check.sh`) run on each machine over its own projects. `park-check.sh` additionally is the verifier the hand-off uses (section 2.2).
- **Morning report** (machine coordinator on `<ws>`): add to its first-wake routine: for each project homed on `<box>`, `rove api gates --machine <box> --repo <path>`, `rove api deliveries --machine <box>`, `rove api context --machine <box> --repo <path> --text`, and the night's logs from `<box>` (over `ssh`). Report per project with the machine in the heading.

## 6. Runtime requirements on a remote box

- **OS.** NixOS is the best fit: `opencode-sandbox.sh` assumes it (binds `/nix/store`, `/run/current-system/sw/bin`, `/etc/static`, `/etc/profiles`, nix-ld paths, `NIX_SSL_CERT_FILE`). Any other distribution needs the script ported (its binds and `PATH` are NixOS-specific); that is a separate piece of work, and the test script `test-opencode-sandbox.sh` is the acceptance proof. Declare the box as code: a flake that installs the tool list below; the existing dedicated-box notes in `machine.md` (key-only SSH, non-root admin user, closed firewall, flake deploy over SSH) apply as written.
- **Packages and services.** `bubblewrap`, `passt` (`pasta`, the sandbox's network), user `systemd` with linger (`loginctl enable-linger`) so the Rove daemon, routines and the guard survive logout; `systemd-run --user --scope` (memory cap `OPENCODE_SANDBOX_MEM`) requires a working user manager; unprivileged user namespaces enabled (default on NixOS, off in some hardened profiles); `nix-ld` for downloaded dynamic binaries; `git`, `gh`, `jq`, `bd`, `rove`, `claude`, `opencode`; Node or Bun for Rove if installed through npm. `/etc/resolv.conf` must **not** name a loopback resolver, or the sandbox refuses to start (a stub resolver such as systemd-resolved's must be bypassed with a real upstream list). Use `#!/usr/bin/env bash` (already the rule).
- **Memory.** 4 GB is tight for a coordinator plus one lane plus compile; 8 to 16 GB is comfortable for docs, scripts, light builds. Put swap on zram plus a small swapfile; set `OPENCODE_SANDBOX_MEM` and `MEM_LOW_GB` from `nproc` and `free -g` (`machine.md`). Two cores: one lane at a time, no heavy builds; a project whose gates need long builds should stay `HOME_ALLOWED=<ws>` or run its gates on a CI runner, not on `<box>`.
- **Repo checkouts.** One main checkout per eligible project on `<box>` (`rove add <path>` on `<box>` creates the saved project and its main task; `add --repo <id> --clone --machine <box>` can create the checkout). Worktrees are per machine and never synced. The project's `AGENTS.md`, `.rove/init.sh` and settings come through git; the **coordinator hook settings are gitignored and guarded by the coordinator's task id** (README), so they are installed per machine with that machine's id. The `.beads` entry is a gitignored symlink to the beads repo; recreate it on `<box>` for the pilot project only.
- **Private data must not move.** Keep a per-project list (`SANDBOX_PRIVATE_PATHS` already exists for the sandbox test) plus `HOME_ALLOWED`. Make it mechanical: the hand-off check refuses a project whose `HOME_ALLOWED` lacks the target; `rove-init.sh` already skips linking private dirs for third-party lanes, and on `<box>` those dirs simply do not exist. Do not rely on "the file is not there": run `test-opencode-sandbox.sh` on `<box>` once, and make sure no job on `<box>` mounts or syncs `<ws>` paths.
- **Network.** `<box>` outbound: git hosts, the Claude and lane-vendor APIs, the Dolt remote. Inbound: SSH only. Prefer its own network segment with no route to the owner's LAN (`machine.md`, dedicated box).
- **Hardening via Rove.** `rove machine inbound deny credentials` (and review `land`, `lifecycle`) on `<box>` so a registered hub cannot push credentials or merge remotely unless you mean it. Which classes `<box>` actually needs inbound from `<ws>` is **[unknown]**; start with the defaults and tighten after the pilot.

## 7. Failure modes and guards

| Failure | Effect | Guard |
|---|---|---|
| `<box>` down during the night | Its projects stall; nothing is lost beyond in-flight lane context | Everything of record is pushed (git, Dolt, handover) before hand-off; coordinators push at natural stops. `<ws>` can take the lease back after the owner confirms the box is dead (below). A box-down alarm is out of band (the owner's phone, an external uptime check); a down machine cannot report itself. |
| `<ws>` down unexpectedly (crash, update) while it is home | Same, other direction | Nothing to do until it is back; the `<box>` side is dormant. Handover is refreshed at every natural stop (existing rule), so a takeover from `<box>` resumes close to the failure point. |
| **Split brain** (two coordinators of one project active) | Duplicate lanes, divergent beads, two merges | (1) The lease with epoch; (2) every coordinator turn and every guard/watch iteration calls `lease.sh check <project>`, and a coordinator that does not hold the lease posts one line and stops (`UserPromptSubmit`/SessionStart hook, same file as the inbox hook); (3) a takeover from an unreachable holder is **never automatic**: it is a Rove gate to the owner ("`<box>` unreachable since T; take over? Lanes on `<box>` may still be running"), because a partitioned `<box>` may be fine and still working; (4) after a forced takeover the old holder, when it reconnects, sees a newer epoch and parks itself; its parked work arrives as pushed branches and a diverged Dolt head, resolved by the owner. |
| Beads conflict (a non-fast-forward `bd dolt pull`) | Two writers existed | Never auto-merge: refuse to continue and open a gate. Dolt can merge, but the evidence says the single-home rule was broken; the owner decides, and `reliability.md` gets a row. Keep `Concurrent writers` in `issues.md`: one writer per database. |
| Beads schema or version drift between machines | Pull fails or corrupts | Same `bd` version on both machines (pinned in the flake and in `machine.md`); `bd version` is part of `handoff-check.sh`. |
| Rove version drift | `--machine` verbs refuse or misbehave | Same pinned Rove; `rove machine doctor` in the check. |
| Stale clone on take (pull skipped) | Work on an old base, duplicate issues | Take step runs `git pull --ff-only` and `bd dolt pull` and verifies the heads against the handover note (`origin/<branch> @ <commit>` already recorded in Resume notes). |
| Stale or missing usage feed on `<box>` | Wrong steps | Merged feed (section 4.1), freshness warning, fail-safe `100 100`. |
| Stale ceiling file | A lowered ceiling not seen | Repo-synced file with `week_to`, safe fallback (section 4.2). |
| Credentials on `<box>` leaked or compromised | Account or repo exposure | Scoped tokens, per-machine keys, revoke one; hardened NixOS; no owner-wide token (section 4.4). |
| Messages to a down machine | Lost orders | Messages are events; the receiving side rebuilds from `rove api context` and the handover anyway (existing rule 5). Scripts ignore failed sends and log them. |
| Both machines run the same routine on the same project | Duplicate prods | `my_projects()` filters by lease. |
| Owner works on `<ws>` while a project is homed on `<box>` | Confusion over which checkout is truth | `<ws>` checkout is read-only by convention for that project until the lease returns; a hand-back is the way to take it. A local `lease.sh check` in the project's shell prompt/hook could warn. |

## 8. Minimal first step

Goal: prove one night's hand-off with existing tooling and as few new moving parts as possible.

### 8.1 Choose the pilot
One project with no private data, light builds, a healthy `bd ready` queue and a few parked-able issues; swift-tier lanes if the sandbox is validated on `<box>`, otherwise standard-tier lanes only (no sandbox needed, no third-party key to deploy). Keep it a single project, one night.

### 8.2 Prepare `<box>` (once)
1. NixOS flake: the tool list of section 6, linger, key-only SSH, owner time zone, zram, non-loopback resolver, firewall closed except SSH. Deploy from `<ws>`.
2. Install `claude`, `rove`, `bd`, `opencode` pinned to the same versions as `<ws>`. Log in to Claude on `<box>` once, and set up the status line writer for `~/.claude/usage-history.jsonl` (copy the script and the one settings entry).
3. `rove machine add <box>` from `<ws>`; `rove machine doctor`; run the unknowns in 3.2 (a `send` both ways; `gates`/`context --machine`; a routine). If `<box>` must message `<ws>`: `rove machine tunnel <box> --install` on `<ws>` and `machine add` on `<box>`.
4. Clone lane-control and the instance repo on `<box>`; create the `<box>` instance config from `config.example.sh`: its own `COORD_TASK`/`COORD_REPO`, `COORD_SLOTS=1`, small-box `MEM_*`, a `MODE_FILE` of its own, `BUDGET_USAGE` (the merged form of 4.1, ssh to `<ws>` guarded by a short timeout), the ceiling file copied (or pointed at the synced path).
5. Pilot project on `<box>`: clone; install `AGENTS.md`-driven files from git, the per-project init script and worker rules (copied by hand for the pilot), coordinator hook settings with `<box>`'s task id; `rove add <path>` for the main task; the beads copy (clone the beads repo; `bd dolt pull`; symlink `.beads`); `bd ready` returns what `<ws>` shows.
6. Run `test-opencode-sandbox.sh` on `<box>` (if swift lanes are in scope).
7. Start `window-guard.sh` and `machine-watch.sh` on `<box>` with the box config; install the night-watch and the daily checks as routines on `<box>`'s own daemon.

### 8.3 The night (manual hand-off, script-light)
- Evening: owner says "leaving". On `<ws>` the coordinator drains, parks, persists and verifies per section 2.2 by hand, using `park-check.sh` as the verifier; the owner (or the machine coordinator) edits `leases/<project>` in the instance repo and pushes; on `<box>` pull, `bd dolt pull`, set the mode file to `night`, send the resume prompt to the `<box>` coordinator.
- Night: the hourly night watch on `<box>` prods only when needed; lane count 1; the guard orders are local.
- Morning: "back"; reverse by hand. Measure: tokens of the two cold starts, time to resume, whether beads converged, what the morning report on `<ws>` needed from `<box>`, anything the coordinator did not know on `<box>` (each is a missing file in git or in the handover).

### 8.4 Success criteria
No split brain; Dolt heads equal after each direction; at least one parked lane resumed on `<box>` and one resumed back on `<ws>`; no budget step missed or duplicated; no credential outside its scoped place; the owner's morning report names `<box>`'s results and gates.

### 8.5 Changes the pilot needs
- Config only: instance files per machine; merged `BUDGET_USAGE`.
- Hand-run: lease file, hand-off checklist, resume prompt.
- Nothing in the scripts yet. If the pilot works, build in this order:

| Order | Item | Size |
|---|---|---|
| 1 | `lease.sh` (show/check/take/release), `my_projects()` in `lib.sh`, scripts iterate it | small |
| 2 | `handoff-check.sh` (preconditions of 2.2, step 3) and a `handoff.sh <project> <to>` that runs check, release/take, remote pull, wake with the standard prompt; the model-facing steps (drain, park, handover) stay with the coordinator | medium |
| 3 | Instance-repo layout for the files that live only in `~/.config/rove` today, with a `link-instance.sh` | small |
| 4 | Ceiling file in the instance repo with `week_to`; pull-before-read; feed freshness | small |
| 5 | `machine-watch.sh` `WATCH_NOTIFY=coordinators` mode for a box with no machine coordinator | small |
| 6 | Morning-report additions in `roles.md` (read the other machines; machine in each heading); `roles.md`/`model-routing.md` text for the lease and `HOME_ALLOWED` | small |
| 7 | Lease check in the coordinator hook template (`templates/coordinator-settings.local.json`) | small |
| 8 | Optional: machine coordinator on `<box>`, with its own `roles.md` variant (no owner report, no playbook upkeep) | medium |

## 9. Open questions for the owner
1. Which projects are eligible to move (any private data or heavy builds that make a project `<ws>`-only), and which one is the pilot?
2. Is `<box>` allowed to hold a Claude login, a repo-scoped GitHub credential and a lane key, and is the same account (the same weekly ceiling) acceptable? If not, a second named budget with its own ceiling.
3. Should "leaving" move all eligible projects automatically, or does the owner pick per night? (Proposed: automatic for the `NIGHT_PROJECTS` list, with an opt-out per project.)
4. Is a machine coordinator on `<box>` wanted from the start (a standing Opus session), or scripts only until a weekend run proves it is needed?
5. Should some projects live permanently on `<box>` (always-on), reached through the sidebar or Remote Control, rather than moving at all?
6. The beads Dolt remote becomes the sync channel, not just the backup: which remote, and does the owner accept that a non-fast-forward stops work and raises a gate?
7. Takeover from an unreachable machine is a gate to the owner. If the owner is not reachable either (travelling), should the other machine stay dormant until contact (proposed), or may it take over after a stated delay?
8. Remote Control and phone access to `<box>` sessions: wanted, and is a long-lived headless Claude token acceptable?
