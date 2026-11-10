# Orca + Cotal: how the parts fit, bring-up, failure modes

This page shows how the parts connect, the order to bring them up on a fresh machine (with a check after each step),
and the failures this repo has recorded. The commands and flags are in `docs/orca.md` §5b, and this page points there
for them. Versions as tested: Orca 1.4.250, Cotal 0.145.1, Claude Code 2.1.x, OpenCode 2.2.1. The Cotal facts come
from its bundled docs (`cotal_docs`), not from memory.

## 1. The parts

```
 workstation (Orca desktop app)                execution host (the same machine, or one over SSH)
 ┌────────────────────────────┐   ssh /       ┌───────────────────────────────────────────────────────────────────┐
 │ Orca GUI: sidebar of tasks │   tunnel      │ orcad (Orca runtime): worktrees, terminals (ptys), the `orca` CLI  │
 │ (worktrees), tabs          │◀─────────────▶│   └─ one pty per tab: shell, Setup tab, agent tab `cotal-<name>`   │
 └────────────────────────────┘               │                                                                   │
                                              │ per project (one Cotal space each):                               │
                                              │   stack-broker   nats-server on 127.0.0.1:<port>, JetStream on disk │
                                              │   stack-delivery delivery daemon (durable DMs and channels, wakes)  │
                                              │   mgr            manager service on the Orca runtime: spawns seats  │
                                              │                  as Orca tabs, mints and renews their credentials   │
                                              │   seats          coordinator, design lead, lanes, reviewers         │
                                              │                  (Claude via the Cotal plugin, OpenCode via         │
                                              │                   opencode-seat and the sandbox)                    │
                                              │ per machine:                                                       │
                                              │   space `machine`: machine coordinator                              │
                                              │   lane-control-window-guard.service (systemd user unit)             │
                                              └───────────────────────────────────────────────────────────────────┘
```

| Part | What it is | Where it lives | Who starts it |
|---|---|---|---|
| Orca app | the desktop GUI; on NixOS packaged as `orca-app`, with the CLI named `orca-ide` (plain `orca` is GNOME's screen reader) | workstation | the owner |
| orcad | Orca's runtime: owns worktrees and ptys; agents keep running when the GUI closes | `~/.orca/`, `~/.orca-remote/` on the host; local runtime logs in `~/.config/orca/logs/daemon.log` | Orca on first connect, or `scripts/orca-host-install` |
| `orca` CLI | talks to orcad: `orca status`, `orca worktree create/list/set`, `orca repo add` | host: `~/.orca/cli/bin/orca`; workstation: `orca-ide` | used by project-setup, the manager and the agents |
| Cotal broker | `nats-server` with JetStream, loopback only, one port per project | `<repo>/.cotal/nats/` | `cotal up --no-manager` (project-setup step 6) |
| delivery daemon | serves durable DMs and channel delivery and the membership feed; holds a lease per space | same root | `cotal up` with the broker |
| `mgr` | the space's manager as a saved Cotal service on the Orca runtime. It launches each seat as an Orca tab and mints a 24-hour seat credential, which it renews at 75% of its life. It carries `envRefs` for PATH and the proxy variables | definition `<repo>/.cotal/mgr-service.json`; log via `cotal service logs mgr -s <space>` | `cotal svc add/start mgr` (project-setup step 6) |
| seat | one agent process in an Orca tab, joined to the mesh under its own identity | its worktree; credential `<repo>/.cotal/auth/` (secret: never open) | `cotal spawn -d` (project-setup) or `cotal_spawn` (coordinator) |
| personas | the coordinator, design-lead and reviewer prompts, rendered from `personas/` | `<repo>/.cotal/agents/` (Cotal reads them there) | project-setup step 5, `--refresh` |
| Claude plugin and wake channel | Cotal's Claude Code plugin: it gives a managed session its mesh tools and wakes an idle session on a message (Claude "channels") | `~/.cotal/claude-plugin/` | `cotal setup -H claude ... --configure-only` (project-setup step 4) |
| settings store, tier table | per-project choices and `lanes.tsv`, outside the repo | `~/.config/lane-control/projects/<folder>-<hash>/` | project-setup (`--show` prints it) |
| skills | `orca-cli` and `orca-cotal-lanes`, user level, because a lane worktree carries only committed files | `~/.agents/skills/`, linked into Claude and OpenCode | project-setup step 3 |
| `opencode-seat`, sandbox | the `opencode` that the manager runs: a hosted model goes into `scripts/opencode-sandbox.sh`, anything else is refused unless marked the owner's own | `~/.agents/bin/cotal-opencode/opencode`, first on mgr's PATH | project-setup |
| window guard | budget orders and the low-memory notice by `cotal send`; spends no tokens | systemd user unit `lane-control-window-guard.service` | `scripts/window-guard-install` |
| machine coordinator | one agent per machine in the space `machine` | `~/code/machine-coordinator` | `scripts/machine-coordinator-setup` |
| `project-pause` | `rest`/`wake` (a guard marker only), `stop`/`start` (also the coordinator and design lead) | marker next to the guard's state file | the owner or a coordinator |

How a message travels: an agent calls `cotal_dm`, its seat publishes to the broker, and the delivery daemon stores
the message durably. The receiving seat's connector takes it and delivers it as its own turn. Claude gets it through
the plugin's channel. OpenCode gets it through Cotal's plugin, which injects the message through OpenCode's server.
Nothing is ever typed into a composer. Orca plays no part in delivery: it only hosts the ptys and the worktrees.

How a lane starts: the coordinator runs `orca worktree create --parent-worktree active`, waits for `orca-setup-done`,
then calls `cotal_spawn`. The manager asks orcad for a tab in that worktree and starts `claude` (with the plugin) or
`opencode` (that is, `opencode-seat`) there, with a fresh credential. The seat joins the roster, and the lane reports
by `cotal_dm`.

## 2. Bring-up on a fresh machine, with a check after each step
Run as the agent user, never root. Pass `-s <space>` to every `cotal` call (`docs/orca.md`, host gotchas).

| # | Step | Check | Healthy output |
|---|---|---|---|
| 1 | Orca on the workstation; connect the host (`docs/orca.md` §2-3), or `scripts/orca-host-install <user@host> --pair` for a host without downloads | `orca status --json` on the host (`orca-ide` on a NixOS workstation) | `result.runtime.reachable: true`, `state: "ready"` |
| 2 | `npm i -g cotal-ai` and the harnesses; `cotal telemetry off`; `cotal ext add @cotal-ai/orca`; `~/.config/cotal/config.json` with `spawn.env` naming the proxy variables and `TMPDIR` (names only, no values) | `cotal --version`; `cotal ext list` | `cotal-ai 0.145.1` and an `orca` line |
| 3 | Sign in to Claude once (`claude`); for bypass mode, accept its warning once | `claude --version` | a version and no login prompt |
| 4 | Only for hosted OpenCode tiers: `~/.config/opencode-sandbox/auth.json`, mode 600, holding a key for lanes only (never copy the owner's login) | `project-setup` warns when the file is missing or unusable | no `sandbox key` warning |
| 5 | `scripts/project-setup <repo>` (`docs/orca.md` §5b) | `cotal space` | the space `● reachable` at `nats://127.0.0.1:<port>` |
| 6 | (same run) broker, delivery daemon, `mgr` | `cotal svc -s <space>` | `mgr`, `stack-broker`, `stack-delivery` all `running` |
| 7 | (same run) coordinator and design lead | `cotal ps -s <space>` | both rows show `running` under PROCESS and `idle` or `working` under MESH. In Orca, the tab `cotal-<name>` sits in the main checkout and in the `design-lead` task |
| 8 | Manager health | `cotal manager status -s <space>`; `cotal service logs mgr -s <space>` | `Process observation: running`, `Readiness observation: endpoint-serving`; no line starting `!` that recurs |
| 9 | Credential health | `cotal doctor auth --space <space> --plain` (run in the repo; read-only without `--fix`) | live seats, `delivery.creds` and `membership-rw.creds` all `healthy`. `expired` is normal only for seats already reaped |
| 10 | Claude wake path (once per machine, by hand) | the agent's tab shows no `Cotal incoming auto-wake is unavailable` notice at startup; a `cotal send -s <space> -t coordinator "test, no reply"` wakes an idle coordinator | the coordinator takes a turn |
| 11 | Machine coordinator and window guard (`docs/orca.md`, "The machine coordinator and the window guard", in its order) | `systemctl --user status lane-control-window-guard.service` | `active (running)`; the journal shows polls |
| 12 | A first lane, from the coordinator | the lane's tab opens first in its child task; its report DM wakes the coordinator | `cotal ps` lists the lane, and later the DM arrives |

## 3. Known failure modes and their fixes
From `docs/orca.md`, `reliability.md`, the skill and git history; the last two rows are from this diagnosis.

| Symptom | Cause | Fix |
|---|---|---|
| Agents never see the proxy | `cotal up` gives its manager a fixed environment | the `mgr` service with `envRefs` (project-setup does it) |
| A `cotal` call acts on another project's broker | no `-s`, so the space last chosen with `cotal use` is used | `-s <space>` on every call |
| Cotal refuses to seed extensions | group-writable `~/.config` (Debian umask 002) | umask 022, `chmod g-w ~/.config ~/.config/cotal` |
| Cotal refuses the bundled nats-server | writable `/usr/local` after a Node tarball install | `chown -R root:root /usr/local` |
| A second user's OpenCode service collides | OpenCode 2's service port 49374 is per machine | `opencode service set port <n>` |
| orcad breaks for other users | a global `TMPDIR` in `/etc/environment` points into one home | orca-host-install falls back to `~/tmp` |
| The OpenCode TUI dies at start | a `noexec` `/tmp` | set `TMPDIR` (and pass it in `spawn.env`) |
| An agent keeps an expired key | OpenCode 2 stores credentials in each agent's database | stop it, move `.cotal/opencode/<name>/` aside, spawn again |
| A required tier is refused (exit 4) | the running `mgr` predates the launch wrapper on its PATH | at a natural stop: `cotal svc stop mgr`, `cotal svc remove mgr`, rerun project-setup |
| `<name> exited on launch` | a pinned model that the server does not list | fix the model in `lanes.tsv` |
| A lane joins but stays silent | used-up quota (`usage limit exceeded` in the lane's `opencode.log`) | another key or tier |
| A Claude agent never starts | the folder-trust prompt was not answered | `cd <repo> && claude` once, or `--trust-folder` |
| A lane misses the setup script or onboarding files | Orca reads `orca.yaml` from the worktree, made from `origin/<branch>` | commit and push (project-setup asks) |
| Two of every order | two window guards running | stop the old guard before `window-guard-install` |
| Nothing restarts after a reboot | no systemd user manager on the host | `loginctl enable-linger`, or start by hand |
| Every 24 h one agent after another stops (after a Cotal upgrade from before 0.49.0) | an old credential has no issuance, so it is never renewed | respawn the managed agents (Cotal docs, "upgrading") |
| A Claude lane shows `Cotal incoming auto-wake is unavailable`, or its Cotal tools turn into the personal `cotal_connect` set | a second Claude session took over the seat's terminal (section 4) | despawn the lane, spawn a new name in the same worktree, hand over its state (the skill's "A lane that lost the mesh") |
| `no space left on device` in the delivery log; channel delivery cannot resume | the disk was full under JetStream (`<repo>/.cotal/nats/`) | free space; the daemon retries on its own |

## 4. Diagnosis: workers losing the mesh after about an hour
**Unproven.** I read this machine's evidence read-only on 2026-11-10: Cotal manager, delivery and broker logs for all
five spaces, `cotal doctor auth`, `cotal ps`, the Orca daemon log, systemd user units, the Claude seat transcripts
and the OpenCode lane logs. Nothing in it shows a loss at about an hour, and two kinds of evidence argue against one:

- **Long lanes kept the mesh.** Since the move to Orca + Cotal (2026-11-08), 15 Claude seat sessions in Orca
  worktrees made Cotal calls after their 55th minute. All 15 sent `cotal_dm` successfully, with no Cotal tool error,
  and 13 of them also received Cotal pushes (DMs that woke them) after that point. The longest went 2 h 25 min (a lane) and 19 h 28 min (a
  design lead). A coordinator that compacted after 8 h made 114 Cotal calls afterwards, all successful.
- **Only one recorded loss, at 19 minutes.** A lane (ADP-1, 2026-11-08) started at 22:27:57Z. At 22:46:59Z (+19 min)
  a second Claude session began in the same worktree: a new transcript opening with `SessionStart:startup` and no
  Cotal identity. The plugin then wrote `Cotal incoming auto-wake is unavailable` into both transcripts. At +28 min
  the lane's `cotal_inbox` asked for a `connection_id` (the personal tool set), and `cotal_connect` answered `Cotal
  could not bind this native conversation to its local server`. The manager log has no offline or exit line for the
  seat in that window. The loss was in the Claude session, not on the mesh.

Suspects, with what rules each in or out:

| # | Suspect | For | Against | Test that settles it |
|---|---|---|---|---|
| 1 | **A second Claude session in the seat's terminal**: the built-in agent switcher, `/resume`, or a plain `claude` started in the tab. The plugin binds a managed seat to its own session id. A new session in that terminal gets no identity and falls back to the personal `cotal_connect` path (Cotal docs, "claude-connector-reference": "One Cotal path per session") | the ADP-1 timeline above, which is the only recorded loss | its timing follows a person's action, not the clock; a compaction (also a `SessionStart`) kept the mesh | in a throwaway lane: open the agent switcher at a noted time, then call `cotal_connection_status` and `cotal_dm`. Repeat with `/resume`. If the tools turn into `cotal_connect`, it is proven, and the fix is "never switch sessions in a seat's tab" |
| 2 | Credential expiry | a stop at a fixed age would fit | seat credentials last 24 h and the manager renews them at 75%. `cotal doctor auth` shows every live seat credential and `delivery.creds` issued with 24 h of life, renewed on 2026-11-10. Expired files belong only to reaped seats. No 1 h lifetime exists in this space | ruled out on this machine. On another machine: `cotal doctor auth --space <s> --plain` when a loss happens. A seat credential `expired` while its process runs proves it |
| 3 | Broker or manager stalls | the manager log shows lease-renewal timeouts (11-08 21:17, 11-09 20:08 and 23:04, 11-10 17:01) and "presence watch silent". The delivery log once hit `no space left on device` under JetStream | each recovered within seconds. "Seat offline on the mesh" appears only for two seats, each back within 5-25 s. These events are not hourly | when a loss happens: `cotal service logs mgr -s <s>` and the delivery log around that minute. A `seat offline ... process running` with no `back on the mesh` after it would prove it |
| 4 | OpenCode seats, or a host other than this one (the trial box, which has no systemd user manager) | the owner's report may come from there | none of this machine's evidence covers it: the OpenCode lane logs here are Rove-era | on that host, when a loss happens: `cotal ps --json -s <s>`, the manager log, and the seat's `opencode.log` |
| 5 | The window guard's 1-hour retry | it is the only fixed one-hour period in lane-control (`GUARD_RETRY_SECS=3600`) | it only retries the guard's own failed sends. It never touches a seat | ruled out by reading `scripts/window-guard.sh` |

What would prove it: the next time a worker seems lost, note the lane and the minute, then save four things before
touching the lane:

1. `cotal ps --json -s <space>`.
2. The lane's `cotal_connection_status` tool, if the lane still answers.
3. `cotal service logs mgr -s <space>` for that minute.
4. The lane's Claude transcript (`~/.claude/projects/<worktree path with dashes>/`): look for a second
   `SessionStart:startup` and the `auto-wake is unavailable` notice.

Suspect 1 is confirmed if the transcript shows a new session before the loss while the manager shows the seat on the
mesh. If the manager shows the seat offline with its process running, the cause is on the mesh side (suspects 2 to 4).
