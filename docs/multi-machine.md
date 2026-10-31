# Running lane-control on several machines (design)

Status: design, nothing implemented. Written against Rove 0.9.274 (verbs checked with `rove machine --help` and `rove api schema --all`; nothing was run against a real second machine, so behaviour is marked). Tags: **[exists]** the verb or flag exists today, **[build]** must be written, **[test]** unknown until tried on two machines.

Wording: `<main>` is the owner's main machine (where the owner works); `<host>` is any other machine reached over SSH (a VPS, a server, a second workstation).

## 1. Goal and rule
The owner wants to handle all projects from one interface, while some projects run on other machines (heavier hardware, always on, different network). The rule is simple:

**A project is pinned to one machine. It does not move.** Its coordinator, lanes, beads database, checkouts and merges all live on that machine. Machines share git and nothing else live. There are no leases and no automatic hand-offs. (A deliberate one-off migration is described in section 8.)

The ergonomic layer is a single **global coordinator** on `<main>` that can see and address the coordinators on every machine.

## 2. What runs where
Each machine is a complete, standalone lane-control install:
- its project coordinators, lanes, beads databases and checkouts;
- its own copy of the config (`playbook.conf.sh`) listing only the coordinators that run there;
- its own scripts: `window-guard.sh` (messages only its local coordinators), `machine-watch.sh` (its own memory and swap), `machine-night-watch.sh`, `stale-claims.sh`, `park-check.sh` as Rove routines in its own daemon, and the sandbox if it runs third-party lanes;
- its own mode file (day or night).

Lanes are always local to their coordinator: Rove refuses a lane parent on another machine (`REMOTE_PARENT_UNSUPPORTED`) and a multi-repo task across machines (`REMOTE_EXTRA_UNSUPPORTED`).

On top, one **global coordinator** (the machine coordinator of `<main>`, with a wider view):
- reads state of coordinators on every machine and sends them orders and FYIs;
- keeps budgets (section 4), approves top-tier requests from any project, reads gates;
- writes the one owner report covering all machines.

**Optional:** no global coordinator at all. A single-project host just runs standalone with its own machine coordinator or none. Nothing in the per-machine setup depends on the global one.

## 3. Rove across machines
### 3.1 What exists
- `rove machine add <ssh-target>` registers a machine, verifying it and offering fixes (install Rove, a dedicated key); `doctor [alias]`, `list`, `remove <alias>` **[exists]**. Its tasks appear in the sidebar under a row of its own.
- A global `--machine ALIAS` flag on every `rove api` verb runs the verb in that machine's daemon, with paths meaning paths there; a `--task-id` that lives on another machine is routed there without the flag **[exists]**. So `rove api context --machine <alias> --repo <path>`, `gates`, `list`, `send`, `tab-revive` are expected to work from `<main>` with no wrapper.
- `rove machine tunnel <alias> [--install]` keeps a reverse tunnel so `<alias>` can reach `<main>` (for a `<main>` behind NAT); registration can then be symmetric **[exists]**.
- `rove machine inbound [allow|deny <class>]` limits what other machines' hubs may run on this one; default is everything **[exists]**. Use it on `<host>` (section 6).
- `add --repo <host/owner/name> --clone [--machine ALIAS]` clones a repo that machine lacks into its Rove repo directory; `rove machine primary` picks the checkout a repo id resolves to **[exists]**.
- `rove api routine-create` creates routines in the local daemon; `--machine` should place one in a registered daemon **[test]**.

### 3.2 Per-machine config and addressing
Each machine's `playbook.conf.sh` is the existing config: tiers, budgets, thresholds, `COORD_TASK`/`COORD_REPO` for the coordinators on that machine only. The global coordinator's config adds a machine map **[build, small]**:
```bash
declare -A COORD_MACHINE=( ["<PROJECT_A>"]=local ["<PROJECT_B>"]=<alias> )   # alias from `rove machine list`
declare -A MACHINES=( [<alias>]="ssh-target" )                                # for the ssh fallback
```
`COORD_TASK[<project>]` is the coordinator's task id **on its own machine**. To reach a remote coordinator:
```bash
rove api send --machine <alias> --task-id <id> --tab tab-1 --prompt-file -    # preferred
ssh <host> rove api send --task-id <id> --tab tab-1 --prompt-file -           # fallback: needs only key SSH
```
Reads use the same flag: `rove api context --machine <alias> --repo <path>`, `rove api gates --machine <alias>`.

Messages stay few, terse and event-driven; coordinators on different machines do not talk to each other, only to the global coordinator. If the target machine is down the sender treats the message as not delivered and records it as a note on the project's beads for the next wake.

### 3.3 To test (the pilot, section 7)
1. Are task ids unique across daemons, so `--task-id` alone routes correctly? Does `--machine` plus `--task-id` behave as the schema says?
2. Does the `[ROVE PEER]` prefix's reply command route back across machines? Does `--expect-report` with `report --delivery` complete across machines? If not, the remote coordinator replies with `ssh <main> rove api send ...` (needs the reverse path) or the global coordinator polls.
3. Does a gate raised on `<host>` appear in the owner's inbox on `<main>`? Do `gates --machine` and `deliveries --machine` return what the report needs? (The web inbox may fail on gates; repeating decisions in chat stays the rule.)
4. Latency and error shape of every verb while a machine is down; scripts need short timeouts either way.
5. `routine-create --machine`; and that the Rove daemon survives logout on `<host>` (user service with linger).
6. Which `inbound` classes `<host>` needs from `<main>` (start with defaults, tighten).

## 4. Budgets
The Claude plan is account-wide, so every machine's guard reaches the same decision from the same percentages.
- The usage feed is appended to a file on each machine by a running session. It is only as fresh as the last active session on that machine, so an idle `<host>` lags when `<main>` is spending. Fix, config only: a `BUDGET_USAGE` command that merges the local tail with each peer's tail over SSH and applies the existing `jq` (max of recent samples within the current reset):
  ```bash
  { tail -60 ~/.claude/usage-history.jsonl; ssh -o ConnectTimeout=5 <host> 'tail -60 ~/.claude/usage-history.jsonl' 2>/dev/null; } | jq -rs '...'   # same filter as the example config
  ```
  An unreadable feed already reads as full and blocks launches; a failed peer is skipped. A freshness warning (feed older than N minutes while a lane is working) is **[build, small]**.
- The ceiling file is the owner's; keep one copy on `<main>` and give each machine a synced copy (git or `scp`) with an expiry date; past it, scripts fall back to `DEFAULT_CEILING`. **[build, small]**
- Each machine's `window-guard.sh` messages only its local coordinators; never one guard messaging remote ones (a dead link would drop orders silently).
- The global coordinator sets each project's allowance regardless of machine, and answers `REQ ... top` from any project. A second Claude login on `<host>` is simply a second named budget.
- Third-party budgets without a feed stay manual; the owner counts lane use on every machine.

## 5. Owner report and gates
The global coordinator wakes at the start of the owner's day, pulls `rove api context`, `gates` and `list` from each machine, and writes one report: decisions first (with a recommendation), then merged, blocked, failing, budget and machine use per machine. A machine it cannot reach is listed as unreachable with the time of its last contact; its projects are not reported as idle.

## 6. What must be on `<host>`
- Rove (same version as `<main>`; `rove machine doctor` checks), a logged-in Claude Code (an interactive login or a scoped token; never a copy of `<main>`'s credential directory), `git`, `gh`, `jq`, `bd` (same version), the engines of the tiers it runs.
- `gh` limited to the repos that live there (fine-grained token or per-repo deploy keys). Rove's `github-sync` copies the whole token; avoid it. A credential for the beads remote, scoped the same way.
- Beads for each pinned project, a checkout, `AGENTS.md` and `.rove/init.sh` via git. The coordinator's gitignored local hook settings are installed per machine, guarded by that machine's coordinator task id.
- User systemd with linger (`loginctl enable-linger`) so the Rove daemon, routines and watches survive logout.
- If it runs sandboxed lanes: everything in `machine.md` and the README requirements (bubblewrap, pasta, unprivileged user namespaces, a real upstream resolver in `/etc/resolv.conf`, NixOS-tested), then `scripts/test-opencode-sandbox.sh` once as the acceptance proof.
- Secrets (lane vendor key, tokens) as root- or user-only files, mode 600, per machine, never in git or in a message. Prefer a distinct key per machine so one can be revoked.
- SSH: key-only, dedicated keys between machines (not the owner's personal key), no agent forwarding. `rove machine inbound deny credentials` and review `land` and `lifecycle`. Treat `<host>` as lower trust than `<main>` if it is internet-facing.
- Private data stays on the machine that owns it; a project that needs it is pinned there.

## 7. Failure modes
| Failure | Effect | Guard |
|---|---|---|
| `<host>` unreachable | The global coordinator cannot read or message it. The project keeps running locally: its own guard, watches and routines do not need `<main>`. | Report it as unreachable with last contact; queue messages as beads notes; the owner decides on a dead host. A host cannot report itself down; an external uptime check helps. |
| `<main>` off | No global coordinator, no owner report. Hosts continue standalone. | Each host's coordinators already handle reports, evidenced merges and gates for later. |
| Budget feed stale | Wrong steps from a lagging feed | Merged feed, freshness warning, fail-safe full reading. |
| Clock skew | Routines fire at the wrong hour; feed samples compare wrongly; ceiling expiry misfires | NTP on every machine; set each host's timezone to the owner's; the feed merge compares reset epochs, not local times. |
| Rove or bd version drift | `--machine` verbs refuse or misbehave; beads schema mismatch | Pin versions; `rove machine doctor` and `bd version` in the pilot check. |
| Message to a down or stalled coordinator | Lost order | Messages are events; a coordinator rebuilds from `rove api context` and its handover anyway. |
| Stale ceiling file on a host | A lowered ceiling not seen | Expiry date and safe fallback. |
| Credential compromise on `<host>` | Repo or account exposure | Scoped tokens, per-machine keys, revoke one. |

## 8. Moving a project once (deliberate migration)
Pinned does not mean stuck. When the owner decides "run this project on that machine instead", it is a rare, owner-triggered migration, done once, in one direction. There are no leases, no automatic hand-off, and no ping-pong between machines. **Planned:** `scripts/move-project.sh` to run this checklist; today it is manual, run by the old and new coordinators and the global coordinator.

1. **On the old machine: stop cleanly.** No new launches. Park every lane per `issues.md` Parking (label, `Resume (...)` note, branch pushed, key in the handover; `park-check.sh` clean). Merge what is ready. Push all branches. `bd dolt push` (issues and memories). The coordinator writes its handover memory, then stops.
2. **Retire the old coordinator.** Stop or remove its Rove main task. Keep the checkout and beads until step 6 verifies. Remove the project from the old machine's config (`COORD_TASK`, `COORD_REPO`) and from anything scheduled there, so no routine or guard wakes it. Confirm the old machine is silent.
3. **On the new machine: set up.** Clone the repo (`rove api add --repo <host/owner/name> --clone --machine <alias>` if it fits **[test]**, else `git clone` and `rove add`). Pull beads from the Dolt remote and check that issue and memory counts match the old side. Install AGENTS.md sections, project init, coordinator local settings and the config entry; add the project to the routines' config; scope secrets to this repo; add sandbox prerequisites if it runs third-party lanes.
4. **Start fresh.** Start the coordinator from its handover (`bd recall handover-latest`, `rove api context`). It resumes parked lanes first (`bd ready --label parked`).
5. **Re-point the global coordinator.** Update its project-to-machine map (`COORD_MACHINE`) and confirm one round trip: a message sent, the reply received, one gate raised and seen.
6. **Verify, then clean up.** Verification list:
   - issue and memory counts equal on both sides; no issue left in progress on the old side;
   - every parked issue's Resume branch exists on origin and contains the named commit;
   - the handover memory is present and is what the new coordinator read;
   - the old machine is silent: no routine, guard message or coordinator activity for that project over a full cycle;
   - the new machine's guard and night watch list the project; the old machine's do not.
   Only then remove the old checkout and archive the old beads database.

**Rollback** before step 6 completes: the old checkout and beads are intact, so stop the new coordinator, restore the old config entries and routines, pull any beads changes back with `bd dolt pull`, and restart the old coordinator from its handover. After step 6 a rollback is a second migration.

## 9. Minimal first step
1. Pick one small, data-free project and one SSH host. Prepare the host per section 6 (without sandbox first).
2. `rove machine add <ssh-target> --alias <alias>` on `<main>`; `rove machine list` and `rove machine doctor`.
3. On the host: config with its one coordinator, the guard, machine-watch and the three routines; create the project's coordinator there.
4. From `<main>`, address it: `rove api list --machine <alias>`, `rove api context --machine <alias> --repo <path>`, `send` one FYI, ask for a reply.
5. Check the test list in 3.3: send and reply round trip, `report --delivery`, a gate raised on the host seen from `<main>`, the merged usage feed, the owner report including the host, and behaviour with the host stopped and the network cut.
6. Add the sandbox on the host only after the above passes, with `test-opencode-sandbox.sh`.
Success: the project runs a night on the host unattended, the next morning's report from `<main>` shows its state and decisions, and a down host is reported rather than missed.
