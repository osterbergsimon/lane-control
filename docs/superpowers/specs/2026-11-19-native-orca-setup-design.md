# Native Orca, painless and repeatable: personas, night manager and `project-setup <repo> [host]` (design)

Date: 2026-11-19. Status: draft for the owner. Section 7 (the second remote direction) is filled in from the
SPIKE-2 result (lc-8ox) before approval.

## 1. Intent

All projects now run on native Orca orchestration. Using it should be painless and easy to repeat on a new machine or
an SSH box. lane-control is where what we learn gets collected so it can be replicated (owner, 2026-11-19: "All we
learn, such as night manager, must go to lane-control").

From now on lane-control's work is native Orca (owner, 2026-11-19). The Orca + Cotal path stays as it is, parked:
kept working by its tests, not extended.

## 2. What the owner asked for

- Proper personas, at least prompts for coordinator, design lead, reviewer and debugger; the machine coordinator is
  set up the same way.
- Personas refreshed after compaction, through hooks in Claude Code and OpenCode v2.
- Claude and OpenCode are both first-class, for every role including coordinator (owner, 2026-11-19).
- Run on SSH boxes with control from the local machine (the Orca app here, Orca's server on the box).
- Let an SSH box's coordinator run lanes on this machine (for example graphics a headless server lacks), controlling
  them and getting their reports back.
- One command: `project-setup <repo> <host>`.
- The lessons and harness improvements of 2026-11-16..19 built in: budget steps, night manager, lane sweep,
  presence-based limits, and the rest of lc-7zk.

Chosen approach (owner, 2026-11-19): make `project-setup` work for native Orca (approach 1 below).

## 3. Goals, non-goals, anti-goals

Goals:
- `project-setup <repo> [<host>]` sets up a project for native Orca, here or on an SSH host: rendered personas, the
  compaction hook, the coordinator started in Orca, the lane tools, the per-project settings store. Refresh, pause and
  wake keep working.
- `project-setup --machine [<host>]` (name left to the builder) sets up a machine once: the machine coordinator, the
  night manager and lane sweep automations, the presence-based limits, the usage feed check.
- Five native-Orca personas: coordinator, design lead, reviewer, debugger, machine coordinator.
- Everything learned on 2026-11-16..19 written into doctrine, generalized for a public repo.

Non-goals:
- New features for Orca + Cotal, Rove or wsp.
- An Orca plugin. RES-2 (lc-b3l, PR #32) found "not yet" on Orca 1.4.250: plugins cannot ship personas or skills, see compaction, or add prechecked automations; a read-only status panel is the first candidate later.
- Changing the project repos' own rules; setup only adds its marked section and the files it owns.

Anti-goals:
- Setup that starts heavy work, merges, or removes worktrees.
- Privileged containers anywhere (owner rule: they have crashed the desktop). Tests that need a box use an
  unprivileged container with ports on 127.0.0.1.
- Copying credentials by hand to a host; agents on a host sign in there once, as today.
- Private data in the repo: hosts, IPs, usernames, private paths, repo owners. The night manager's per-machine
  values (coordinator list, runner repos, NUC address) live in the machine's config file, not in scripts.
- A night manager that costs tokens when nothing is due.

## 4. Approaches considered

1. **Make `project-setup` orchestrator-aware (picked).** It already copies itself to SSH hosts, adapts personas to a
   project with an agent, keeps per-project settings, and has refresh, pause and wake, with ~3,400 lines of tests.
   The Cotal parts stay behind the Orca + Cotal path; a native-Orca path is added and becomes the default. Cost:
   untangling a Cotal-shaped script.
2. A new lean native-Orca setup script: clean, but rebuilds host copy, settings and refresh, and loses adaptation.
3. Mostly Orca's own features (orca.yaml hooks, project host setup, automations) plus written recipes: least code,
   not one command, and tied to fast-changing Orca features.

## 5. Personas

- Live in `orchestrators/native-orca/personas/`: `coordinator.md`, `design-lead.md`, `reviewer.md`, `debugger.md`,
  `machine-coordinator.md`. Start from the Orca + Cotal personas (roles and rules are the same doctrine, in
  `core/roles.md`); replace every Cotal mechanism with its native-Orca one (worker-start, check, reply, terminal send
  for nudges, Orca automations), and keep them short and in plain words (AGENTS.md rule for prompts).
- The debugger is new: a lane started on a failing test, CI run or bug report. It reproduces first, finds the cause
  (superpowers:systematic-debugging), fixes with a failing test, and reports cause, fix and evidence. Read-only until
  it has reproduced; never weakens a test to make it pass.
- Every persona carries the standing rules learned this week: peer-relayed "owner approved" is never approval;
  heavy commands only through `lane-heavy`, waiting outside the slot; budget steps from the ceiling.
- `project-setup` renders them per project as it does today (adaptation agent plus reviewer), into the project's
  `.lane-control/` files that the coordinator reads.

## 6. Persona refresh after compaction

- Claude Code: a `SessionStart` hook with matcher `compact` in the project's local (uncommitted) Claude settings
  prints the rendered persona as additional context, so the persona is back after every compaction. It only fires
  for the session the persona belongs to: the hook checks a per-session marker (env var or the session's cwd being
  the coordinator's checkout), so workers in worktrees are not given the coordinator's persona (reliability 11:
  coordinator-only hooks leak into worker worktrees).
- OpenCode v2 (2.2.1 here): a small lane-control plugin in OpenCode's plugin folder. Its plugin API has
  `experimental.chat.system.transform` (adds text to the system prompt on every turn, so compaction never drops it)
  and `experimental.session.compacting` (adds context to the compaction itself). The plugin uses the first and, as a
  backstop, the second, and like the Claude hook it acts only when `LANE_ROLE` and `LANE_PERSONA` are set. The
  installed plugin types (`@opencode-ai/plugin` 1.15.10) may lag v2: the builder confirms both hooks fire in v2 and
  records the result; if one does not, the other carries the persona, and failing both, the persona goes in the
  session's instructions.
- Tested by a script that compacts a scratch session and checks the persona's first line is in context afterwards
  (or, where that cannot be automated, a written manual check in `orchestrators/native-orca/checklist`).

## 7. Remote hosts

Direction 1, the box does the work, controlled from here: the Orca app here drives Orca's server on the SSH host
(saved Orca SSH hosts; `scripts/orca-host-install` puts the server there). `project-setup <repo> <host>` clones or
uses the repo on the host, renders personas there, installs the lane tools and the hook there, and launches the
coordinator through Orca on that host.

Direction 2, a box's coordinator runs a lane here: Orca pairing both ways (owner pick, 2026-11-19). This machine
runs an Orca runtime the box pairs to (`environment add` from a pairing code, through an SSH-forwarded port or the
LAN, never a port open to the internet, never desktop control). The box's coordinator starts and steers the lane
with `--environment <this machine>`; the lane reports back with `--environment <the box>`.
(Corrected 2026-11-21: from an agent, `--environment` is refused for `send` and `worker-start` on 1.4.267 and delivers no mail on 1.4.263; lanes start with `worker-start --on`, and the one cross-host message route is `send --on`; see `orchestrators/native-orca/remote.md`, Messages to a session on the other host.)
SPIKE-2 (lc-8ox, 2026-11-19, Orca 1.4.250, an unprivileged container as the box) found:
- Direction 1 works: `scripts/orca-host-install <host> --pair --port <p>` installs and pairs Orca's server; terminal
  create, send and read with `--environment <box>` all work. A supervised worker on the box needs a real agent
  signed in there (the container had none, by rule).
- Direction 2 works at the protocol level: the box's CLI pairs with `environment add --pairing-code`, then
  `orchestration worker-start --on <this machine> --worktree ... --terminal ...` dispatches to a Claude worker here,
  with the Run on the box. The worker's `worker_done` lands in the box Run's inbox.
- A plain shell coordinator is never woken (same host or across hosts); waking needs an agent coordinator. Whether
  a cross-host report wakes an agent coordinator on the box is not yet established.
- Pairing grants `scope: runtime` only, no desktop control.
- The desktop Orca's pairing offer is GUI-only (Settings > Servers > Share this computer); there is no CLI for it in
  1.4.250. SPIKE-2 used a second headless Orca server here, with its own home folder, instead.
- Claude's "trust this folder" menu has to be answered with raw key input (Down, then Enter, as separate sends).

So `project-setup` does, for direction 2:
1. On this machine: make a lane runtime the box can pair to (see the open choice below), reachable from the box
   only through an SSH-forwarded port (`ssh -R`) or the LAN; never an internet-facing port, never desktop control.
2. On the box: `environment add` with the pairing code, and record the environment name in the project's settings
   so the box's coordinator persona knows it (`--on <name>` for lanes that need this machine).
3. Persona rule for the box's coordinator: start a lane here only for what the box lacks (graphics, a browser, a
   GPU); it works in its own clone here and pushes to GitHub as usual; reports come back as Orca messages.
4. The coordinator's persona tells it to run `orchestration check` after dispatching a cross-host lane and on each
   wake, so it does not depend on the wake reaching it.
5. First build step: confirm on a real box with a signed-in agent coordinator that a cross-host `worker_done` wakes
   it; if not, the coordinator polls with `check --wait` while such a lane runs, and the record says so.

Owner's choice (2026-11-19): lanes from a box run in the desktop Orca here, paired with its own share link. One
runtime, the lanes show in the window like any other, nothing extra to keep running. (A second headless server was
considered and dropped as more fragile: its own service, home folder and visibility workaround.)
- Orca 1.4.250 has no CLI for the desktop's pairing link, so the owner makes it once per box: Settings > Servers >
  "Share this computer", choosing the this-computer address (127.0.0.1; a network address would widen the desktop's
  bind).
- `project-setup <repo> <host>` asks for the link on its prompt (hidden input, never in arguments, logs, files or
  the repo), passes it to the box over SSH (`environment add --pairing-code` read from stdin on the box), keeps the
  reverse tunnel open (`ssh -N -R 127.0.0.1:<port>:127.0.0.1:<port>`, as a user service) and records only the
  environment's name in the settings store.
- The link lets the box run commands as the owner here (no desktop control) until revoked; the record and the
  setup output say so, and say where to revoke it. Removing a box (`project-setup --remove-host`, name left to the
  builder) removes the saved environment on the box, stops the tunnel and tells the owner to revoke the link.
- First build step: the owner makes one link, and the build confirms a box's coordinator can start a lane here that
  shows in the desktop window and reports back.

## 8. Machine setup: machine coordinator, night manager, lane sweep, limits

Generalized from the machine coordinator's working files (2026-11-16..19):
- **Machine coordinator:** a plain Orca session on Sonnet in its own private folder (not a repo that is pushed
  publicly), persona from section 5. Checks budget and machine every 30 minutes; warns coordinators once per step.
- **Night manager:** an Orca automation (`automations create --trigger "37 * * * *" --workspace <machine folder>
  --workspace-mode existing --provider claude --extra-agent-args="--model sonnet" --precheck <script>`). The
  precheck exits 0 only when the owner is away (away file younger than 14 h) or it is night (23-07 by default),
  AND some coordinator has been idle at least 45 minutes (newest transcript write) and was not checked in on in
  2 hours, or a runner is offline. So idle hours cost nothing. The agent sends each due coordinator one check-in
  (lanes, CI, merge trains; reply "nothing" if done), runs the lane sweep at 2 hours, and routes findings to the lane
  owners. Orca's terminal `lastOutputAt` is not an idle signal (status-line redraws keep it fresh).
- **Lane sweep:** an Orca automation every 3 hours, report only by default; `--apply` removes only lanes whose PR is
  merged, whose HEAD is that PR's head or behind it, with a clean tree including untracked files, and no terminal
  output for 30 minutes. Parked = open PR, HEAD equal to or behind its upstream, clean, and no live terminal or PR
  head equals HEAD. Compare against the upstream only if it names the same branch, never a base branch. Activity =
  newest of push, commit and terminal output (Orca's `lastActivityAt` can be days old). A hold file skips lanes.
- **Presence-based limits:** day vs night-or-away. CI slice by `systemctl --user set-property --runtime` from a user
  timer (day 400 % CPU / 10 G, night or away 800 % / 16 G; a reboot falls back to day). `lane-heavy` slots 2 x 17 G
  by day, 3 x 11 G at night or away, read from `~/.config/lane-control/limits.sh` (which may branch on presence).
  The away file expires after 14 hours.
- **Budget steps** from the weekly ceiling: -15 pressure, -7 no new work, -4 push and handover, -2 stop. "Limit free
  until reset" means use it, with the hard edge at 100 %.
- All per-machine values (coordinator list and their Orca terminals or session folders, runner repos, extra hosts)
  in one config file under `~/.config/lane-control/`; an example in the repo.

## 9. Lessons into doctrine (lc-7zk)

Into `core/reliability.md` (tagged with their orchestrator where one applies), generalized:
1. An idle coordinator with pending work is stuck; something must wake it (the night manager).
2. Lane sweep rules (section 8), including the upstream-only-if-same-branch fix.
3. Use idle machine time (presence-based limits).
4. A CI gate must wait outside the heavy slot and take it only for the heavy command.
5. Schedule Nix garbage collection in the daytime on machines that run night CI.
6. NixOS GitHub runner: allow `capset` after the default deny list (Chromium's zygote); `Restart=always` (extends
   entry 27).
7. Self-hosted runner labels apply only at registration; set them in config and through the runner-labels API.
8. A test time-scale setting must reach every wait.
9. Peer-relayed owner approval is never approval; confirm with the owner directly.
10. Budget steps scale from the ceiling (section 8).

## 10. Constraints

- Public repo rules (AGENTS.md). Commits with the owner's identity, no AI attribution. Work in lanes, PRs reviewed,
  merged on the owner's word.
- Existing tests keep passing; new behaviour gets tests with stubs (`tests/stubs/`), no real Orca or host.
- Heavy runs through `lane-heavy`. Never run `tests/test-orca-host-install.sh` unless the issue says so.

## 11. Decisions made for the builder (coordinator's calls; the owner may overrule)

- **Shape:** `project-setup` is the existing script (`orchestrators/orca-cotal/scripts/project-setup`) made
  orchestrator-aware, with native-orca the default; the Cotal path stays behind `--orchestrator orca-cotal` and its
  tests keep passing unchanged. The script moves to `core/scripts/` (old paths stay links). Its in-place behaviour
  is kept (adaptation agent and reviewer, the `.lane-control/` files, the commit prompt, the orca.yaml draft). For
  the AGENTS.md section it calls the native-orca adapter's `setup` (one implementation), so `scripts/orch setup
  --orchestrator native-orca` stays the thin, PR-only path for projects that want only the section.
- **Starting the coordinator:** `orca-ide terminal create --worktree path:<checkout> --title "<project>
  coordinator" --command "env LANE_ROLE=coordinator LANE_PERSONA=<checkout>/.lane-control/coordinator.md claude
  --model <model> --permission-mode <mode>"`, then one first message ("read your persona and the handovers, then
  wait for the owner"). SPIKE-2 showed Orca recognises a Claude started this way (`agentIdentity: claude`). Trust
  prompt: `--trust-folder` keeps its meaning (answer with raw key input, per SPIKE-2).
- **Persona injection, start and compaction alike:** one `SessionStart` hook (matchers `startup`, `resume`,
  `compact`) that prints `$LANE_PERSONA` when `LANE_ROLE` is set, and does nothing otherwise. The env var is the
  marker: workers started by `worker-start` do not inherit it, so they never get the coordinator's persona, even
  in the same checkout. The hook goes in the checkout's `.claude/settings.local.json` (uncommitted; on a remote
  host, written on the host). Lanes started by a coordinator get their persona (reviewer, debugger, design lead) as
  the worker spec plus `LANE_ROLE`/`LANE_PERSONA` where the launch allows an env, so compaction restores them too.
- **Claude and OpenCode are first-class for every role** (owner, 2026-11-19), coordinator included. Each persona,
  the start command, the persona hook (section 6), pause/wake/refresh, the night manager's idle signal and the
  check-in all have a Claude and an OpenCode form, and the tests cover both. Per role the harness and model come
  from the project's settings store (`--coordinator opencode/<model>` as today's flag shape). OpenCode starts as
  `env LANE_ROLE=... LANE_PERSONA=... opencode --model <provider/model> [--auto]` in an Orca terminal; Orca
  recognises OpenCode and types its wakes into it as for Claude (the builder confirms the wake for an OpenCode
  coordinator, as SPIKE-2 did for Claude workers). Idle for the night manager: the newest write of the
  coordinator's OpenCode session in OpenCode's data folder, the counterpart of Claude's transcript.
- **Model rule still holds:** the owner's "no code to China" rule limits which OpenCode providers may run unsandboxed
  (today only a project whose own tier table lists `opencode-go/deepseek-v4.1-flash` runs it, and only through its own coordinator). An OpenCode
  coordinator or lane elsewhere needs a provider the owner allows; `project-setup` refuses a role/model pair the
  tier rules (`core/scripts/lane-tier`) refuse.
- **Refresh, pause, wake without Cotal:** refresh = re-render the personas and the hook; the next compaction or
  restart picks them up, and an optional `--restart` asks the coordinator to write its handover, then relaunches it.
  Pause = the coordinator writes its handover, its terminal is closed, and the project is marked paused in the
  settings store (the night manager skips it). Wake = launch it again as above (`claude --continue` when the session
  is to be kept).
- **Usage feed check:** the machine setup checks that Claude's usage feed (the status line writing
  `~/.claude/usage-history.jsonl`) exists and was written in the last hour; if not, it says so and how to turn it on,
  and budget steps are reported as unknown rather than assumed.
- **Sources for section 8:** the machine coordinator's working files on this machine are the source; the builder's
  brief gives their location. They are generalized, never copied with their private values. `machine-night-watch.sh`
  is the Rove version and stays in the Rove folder.
- **Away file:** a small `lane-away on|off|status` command writes or removes `~/.config/lane-control/away`; the owner
  runs it or asks the machine coordinator to. Idle is read from the newest transcript in
  `~/.claude/projects/<the checkout's encoded path>/`.
- **Machine setup on a host:** the same setup runs on any machine where coordinators run, so a box gets its own
  machine coordinator, night manager and sweep for its own coordinators. Systemd user units need a user bus and
  linger; without systemd the presence-based CI limits are skipped with a warning, and the rest still works.
- **Compaction test:** an opt-in manual check, outside the default suite (it needs a real Claude session); the
  default suite tests the hook script and the rendering with stubs.
- **Cross-host wake:** build the "run `check` yourself" fallback unconditionally; confirming the wake on a real box
  is an opt-in step when a box with a signed-in agent is available, and the record says what was found.

## 12. Harnesses: Claude and OpenCode now, others tried the same way

Claude and OpenCode are first-class (section 11). Other agents are added the way orchestrators are: through a small
per-harness adapter and a dated record, so trying one is cheap and repeatable (owner, 2026-11-19: "look at other
agents too, such as pi, if they are easy to drive for us and orca has support").
- An adapter says, for one harness: how to start it in an Orca terminal (with `LANE_ROLE`/`LANE_PERSONA`), how its
  persona survives compaction (hook, plugin or system-prompt file), where its idle signal is, whether Orca wakes it,
  and which `--model`/`--effort` Orca accepts for it.
- Records live with the harness adapters (for example `core/harnesses/<name>/record.md`), status `untested`, `trial`,
  `in use`, `parked`; a trial uses the orchestrator checklist's agent points (start, messages, wake, compaction,
  cost) and the tier rules (the provider must be one the owner allows).
- Candidates from Orca 1.4.250: `codex` (installed here; Orca passes model and effort and manages its hooks) and
  `pi` (Orca's worker-start lists an OMP agent that accepts `--model` only; to confirm it is pi and how it is
  installed). Others Orca knows (gemini, cursor, amp, copilot, qwen-code, kimi and more) are not installed. This
  design builds the adapter shape and the Claude and OpenCode adapters; codex and pi get records with status
  `untested` and are tried in a later trial lane.

## 13. Left to the builder

The flag names; how `project-setup` chooses the orchestrator (default native-orca, `--orchestrator orca-cotal` for
the old path); the machine config file's format; how the work is split into PRs (suggested: personas and hook;
project-setup native path; machine setup and automations; doctrine and lessons; remote direction 2 after SPIKE-2).
