# Agent sandbox: bwrap around agent lanes (SBX-1)

Status: design, for the owner's approval. Issue lc-9r81. No product code yet: the plan is
`docs/superpowers/plans/2026-11-27-agent-sandbox.md`, and the spike is in `docs/superpowers/spikes/2026-11-27-agent-sandbox/`.

## Why

The owner, 2026-11-27: "go for sandbox". Protections written as agent config keep failing, because the agent can
write that config or go around it:

- lc-mpb5: OpenCode 2.2.1 reads floor paths (secrets, `--private` paths, auth files) through grep, the shell and
  symlinks with no allow at all. Its file-tool denies do not bind its other tools.
- lc-yw1g.1: one `allow` policy in a root `opencode.json` (which the floor lets the agent write) or in the global
  config lifts the whole floor in the same session. lc-yw1g.3: a sibling Claude `settings.local.json` turns the
  guard hook off. lc-yw1g.2, .4, .8, .10: other ways the floor misses or loses a path.
- lc-8h1.1 to .3 (LIT-2): git run by lane-control on a repo that an agent could write runs programs the repo names,
  and a gate that checks the agent's writes afterwards can be fooled.

More rules do not close this. What holds is a boundary the agent cannot write: the agent process, and everything it
starts, runs where the floor paths do not exist and the config that sets its limits is read-only.

## Threat model

**Who.** The agent process of a lane and every process it starts (tools, shell commands, plugins, MCP servers,
test runs). It runs as the owner's uid. Its model may be steered by anything it reads: the repo, issues, PR
comments, web pages, tool output. It is assumed hostile; the owner's own sessions and the coordinator are not in
scope here (they stay unsandboxed; see "What stays open").

**Must never read** (the floor):

- Secrets and auth: `~/.ssh`, `~/.gnupg`, OpenCode's `auth.json` and `mcp-auth.json`, Claude's `.credentials.json`
  and `.claude.json` (wherever `CLAUDE_CONFIG_DIR` puts them), `~/.config/gh/hosts.yml`, `~/.netrc`,
  `~/.git-credentials`, `~/.docker/config.json`, `~/.aws`, `~/.kube`, any `*.env` and key file outside the worktree.
- The project's `--private` paths, inside the worktree too.
- lane-control's own config and state (`~/.config/lane-control`, `~/.local/share/lane-control`,
  `~/.local/state/lane-control`), the beads repo's other files, Orca's settings and data (`~/.config/orca`).
- The owner's global agent config: `~/.config/opencode`, `~/.claude`, `~/.agents` (only what a lane needs from these
  is generated or bound read-only, below).
- Other lanes' worktrees, the main checkout's files, the rest of `$HOME`.
- Secret values in the environment: the owner's tokens, Orca's runtime token, other variables the agent does not
  need. The one secret a lane must have (the gateway or API key) is the exception, see "Network".

**Must never write:** the files that set its limits (the generated agent config, the project-level
`.opencode/opencode.json(c)`, a root `opencode.json(c)`, `.claude/settings*.json`), git's program-running files
(the common dir's `config`, `hooks/`, `info/`, `modules/*/config`, `modules/*/hooks`, other worktrees' admin dirs,
its own admin dir's `commondir`, `gitdir` and `config.worktree`, the worktree's `.git` file), branches that are not
its own, and anything in the must-never-read list.

**Must never lift** its own limits by starting something outside the sandbox. Every route found:

| Route out | Handled by |
|---|---|
| Orca's CLI (`orca terminal create`, `agent launch`, `worker-start`, `terminal send` to another pane): its unix socket `~/.config/orca/o-<pid>-<id>.sock` plus `orca-runtime.json` give full Orca RPC (traced: `orca orchestration check` reads exactly these) | Not mounted. An `orca` shim inside talks to a lane broker outside that runs only `orchestration send`, `ask`, `check` and `reply` with `--from` set to this lane's terminal (below) |
| A git program run later outside the sandbox (by Orca's git status, the broker, the owner): `.git/config` `core.fsmonitor`, hooks, a rewritten `.git` file or `commondir` pointing at a git dir the agent made | Those files are read-only (spike, checks 3); the broker passes the git dir explicitly from lane-control's own record and refuses program-naming keys in repo scopes (lc-8h1.2's list) |
| The session bus (`/run/user/<uid>/bus`: `systemd-run --user`, portals), the user systemd socket, `ssh-agent`, `gpg-agent`, Wayland/X11 sockets (typing into the owner's windows), PipeWire, the docker or podman socket | `/run/user/<uid>` and `/var/run` are not mounted; `SSH_AUTH_SOCK`, `DISPLAY`, `WAYLAND_DISPLAY`, `DBUS_SESSION_BUS_ADDRESS` are not passed |
| The nix daemon socket: the owner is a trusted user here (`nix store info`: `"trusted": true`), so the socket can import unsigned store paths, which is as good as root on the next rebuild | Not mounted. `/nix/store` read-only only; the tools a lane needs are built outside before it starts |
| The environment of a process outside the sandbox through `/proc` | `--unshare-pid` and a new `/proc`; bwrap itself is started from a clean environment (the spike found that bwrap's own pid-1 helper keeps the environment bwrap was started with and the agent can read it from `/proc/1/environ`; `--clearenv` alone does not cover it) |
| Files in the worktree that something outside runs later: `orca.yaml` setup and archive scripts, `.envrc` (direnv in the owner's shell), editor tasks, `package.json` scripts, a Makefile the owner runs | Partly: the sandbox makes `orca.yaml` read-only (Orca runs it on the host). The rest is the lane's work product: it reaches the owner through a reviewed PR, as today. Listed under "What stays open" |
| A pushed branch: `git push --force`, a push to `main`, a push to another repo with the owner's key | The token and key are not in the sandbox. Push goes through the broker, which pushes only `HEAD:refs/heads/<this lane's branch>` to `origin`, fast-forward only |

**Network stays on**: an OpenCode lane needs its gateway, a Claude lane its API, and lanes fetch packages and open
PRs (through the broker). Phase 1 shares the host's network namespace. That leaves open:

- **Exfiltration of anything the sandbox can read**: the worktree (the project's code, but not `--private` paths),
  the beads issues, and the gateway or API key that the agent's own process holds. A lane on a self-hosted gateway
  can read its key from its environment and send it anywhere. Phase 2 removes the key from the sandbox with a
  key proxy (a loopback listener outside that adds the real key; the agent gets the proxy URL and a dummy key).
- **Services on the host's loopback and the LAN**: dev databases, the gateway's admin port, Orca's agent-hook port
  (it can post a false status, with the hook token the agent needs anyway), devices on the local network, and on a
  cloud box the metadata endpoint (169.254.169.254), which can hand out the box's cloud credentials.
- **Abstract unix sockets**: a shared network namespace shares the abstract socket namespace, so an abstract X11
  socket (Xwayland listens on one) or another program's abstract socket is reachable without any file.
- Phase 2 closes the last two with its own network namespace: `bwrap --unshare-net` plus `pasta` (passt,
  unprivileged) for outbound traffic, the gateway and Orca's hook port forwarded on purpose, and Landlock's
  abstract-socket scope (`LANDLOCK_SCOPE_ABSTRACT_UNIX_SOCKET`, kernel 6.12+; this kernel lists `landlock` in
  `/sys/kernel/security/lsm`). The owner decides when (open question 2).

## Approaches

| | What | For | Against |
|---|---|---|---|
| **A. bwrap wrapper on the agent command** (recommended) | Orca launches the agent as now; the binary it finds is a lane-control shim that execs the real agent under bwrap with a per-lane mount plan | Orca's own launch, env, hooks, model and readiness handling stay as they are; one process tree; works for terminal and chat launches; same on a remote box; nothing privileged | Has to win the PATH lookup (checked, below); a separate shim per agent CLI; brokers needed for Orca, git push and gh |
| B. bwrap around the whole Orca terminal | The terminal's shell runs in the sandbox | Everything typed in that pane is inside | Orca starts the terminal's login shell itself; changing it means Orca's terminal settings, which apply to every pane (the owner's too); Orca types mail and runs its shell integration inside a shell it expects to own; no gain over A, since the agent is the only thing a lane pane runs |
| B'. Orca's per-workspace environment recipes (`environmentRecipes` in `orca.yaml`, the `orca-per-workspace-env` skill) | Orca creates a fresh runtime per workspace (cloud sandbox, VM, SSH host or local container) and runs an `orca serve` in it | It is Orca's own feature, so a sandbox per workspace would be Orca's job | Heavy: a full Orca server and its own agent login per workspace, a pairing per environment, snapshots; the picker reads `orca.yaml` from the primary checkout; nothing in 1.4.274 to 1.4.280 offers a local bwrap recipe. Worth revisiting if Orca adds one |
| C. OpenCode's own sandbox | — | — | OpenCode 2.2.1 has none: `--help` offers only `--auto`; the binary has no bwrap, Landlock or seatbelt code (its `sandboxes` column is a project-table field). Its permissions and policies are config, the thing that failed |
| C'. Claude Code's own sandbox (`sandbox` settings; bwrap on Linux) | Claude wraps its Bash tool in bwrap | Built in, maintained | Bash only: Read, Edit, Grep and MCP are not inside it; set in settings files (the same lift as lc-yw1g.3); a command may ask to run unsandboxed (`dangerouslyDisableSandbox`, `unsandboxedRetry` in 2.1.332). Useful as an inner layer, not as the boundary |
| D. `systemd-run --user` | A transient unit with `ProtectHome=tmpfs`, `BindReadOnlyPaths=`, `PrivateUsers=` | Declarative; resource limits for free | A service unit is a child of the user manager, not of Orca's pane: `--pty` gives a terminal, but Orca's process-tree checks, kill-on-close and the agent's own terminal handling see a different tree; `--scope` keeps the tree but gets no mount namespace. And the user manager is the escape route a sandbox must not offer |
| E. Landlock alone (`landrun` in nixpkgs, or a small launcher) | Kernel access rules inherited by every child, unprivileged, cannot be undone | Very strong once applied; TCP-port and abstract-socket rules (ABI 4 and 6) | Cannot hide or replace paths: OpenCode reads `$HOME/.config/opencode`, so the generated config would have to be the owner's real one, or be reached by moved XDG dirs the agent's tools still look past; a deny is EACCES on a visible path. Best as phase 2's inner layer inside A |
| F. A rootless container (podman) | An image per lane | Familiar | An image to build and update, the store and tools to copy or mount anyway; no protection A lacks. A privileged container is out (owner's rule) |

**Recommendation: A**, phase 1 for OpenCode lanes, with three small brokers outside (Orca mail, git push and
fetch, gh for the PR), a clean environment, and HOME as a tmpfs. Phase 2: key proxy, network namespace with pasta,
Landlock inner layer, then Claude lanes.

## How the wrapper gets into the launch

Checked on Orca 1.4.274 (the running build), its CLI help, its bundled skills and code (read, not run), and the
release notes from `gh release list -R stablyai/orca` through v1.4.280:

- **How `worker-start --agent opencode` finds the agent.** Orca's agent catalog
  (`shared/agent-launch/catalog/tui-agent-config.js`) gives `opencode` the detect command `opencode` and
  `opencode2` the launch command `opencode2 --standalone`. Orca opens the worker's terminal on the login shell and
  runs the command line in it (this lane's own process tree: `orca-ide` → `fish` → the agent), so the binary is the
  first `opencode`/`opencode2` on that shell's PATH. A launch as a chat (the owner's setting for new agent tabs)
  takes the program from the same catalog and resolves it on Orca's own process PATH. Before a model launch Orca
  also runs `opencode --version` through the same command and refuses a 2.x older than 2.0.14
  (`native-orca-lanes`).
- **Orca's own feature for this**: Settings → Agents → a custom command per agent (`agentCmdOverrides[<agent>]`;
  `parseAgentCommand` takes leading `NAME=value` assignments, a binary and prefix arguments; the chat path
  (`structuredAgentCommandPlan`) refuses env assignments and shell syntax, so it must be a plain path). It is the
  cleanest hook, but it is an edit to Orca's settings, it applies to every launch of that agent id (the owner's own
  OpenCode tabs too), and lane-control does not edit Orca's settings. The release notes through v1.4.280 add no
  sandbox or other agent-command feature: #49396 "Honour bubblewrap..." is about Claude's own sandbox setting on root
  hosts; the environment recipes are above (B'). Orca can also install its own OpenCode when a host has none
  (1.4.266); the shim must then still come first on PATH, and the check below catches it if not.
- **The mechanism: a PATH shim.** `lane-control machine setup --sandbox` puts `opencode` and `opencode2` links to
  `core/scripts/lane-sandbox-shim` in a directory that comes before the real CLI on both PATHs. On this machine
  `~/.local/bin` comes before npm's global bin folder (where OpenCode is) in the login shell and in Orca's process
  environment alike; setup takes the first PATH directory ahead of the real binary, asks the owner before writing
  there, and never edits shell rc files. It refuses (and says why) when no such directory exists.
- **What the shim does.** It finds the worktree from its working directory (`git rev-parse --show-toplevel`,
  checked against `ORCA_WORKTREE_ID`'s path). If lane-control has a sandbox profile for that worktree
  (`~/.local/state/lane-control/sandbox/<worktree-id>.json`, written by `lane-control lane prepare --sandbox`
  outside the sandbox and never mounted inside), it runs the real binary under bwrap with that profile. A worktree
  of a project with the sandbox on, other than its primary checkout, that has no profile is refused, not run
  unsandboxed (fail closed). Anything else (the owner's own tabs, the coordinator, `opencode --version`,
  `opencode models`) is passed to the real binary unchanged. The real binary's path is recorded at setup, so the
  shim never looks itself up.
- **After start**: the shim records the sandboxed pid in the profile's run record; the coordinator's
  `lane-control lane started <worktree>` (one line in the `native-orca-lanes` skill after `worker-start`) checks
  that record within 30 s and stops the worker with `orchestration worker-stop` when the agent did not come up
  sandboxed. That catches a PATH that changed after setup.
- **Not used**: `orca.yaml`'s `scripts.setup` runs in its own terminal, beside or before the agent, and cannot
  change the agent's command or environment. A "lane-control agent profile" does not exist in Orca beyond managed
  accounts (`orca account add`: per-account XDG folders), which move data, not the command. Starting the agent in a
  terminal of our own (`orca terminal create --command "<wrapper> opencode2 ..."`, then
  `worker-start --terminal <handle>`) avoids the PATH but loses Orca's OpenCode launch (its hook plugin, model
  handling and readiness checks; `--model` cannot combine with `--terminal`): kept as the fallback the plan's first
  task measures.

Open question 1 asks whether the owner would rather set the Orca custom command once by hand.

## Mount plan (OpenCode, phase 1)

bwrap is started from `env -i` with an allowlist, with `--die-with-parent --new-session --unshare-pid --unshare-ipc
--unshare-uts --unshare-cgroup-try`, no `--unshare-net` in phase 1. Paths keep their real names, so `.git` files,
`ORCA_WORKTREE_ID` and transcripts match. Later binds override earlier ones.

| Path | Inside | Why |
|---|---|---|
| `/nix/store` | read-only | binaries and libraries; nothing writes it |
| `/usr`, `/bin`, `/lib*`, `/etc` | read-only | `/etc` holds no user secret readable by the uid; Orca's FHS environment needs its own `/usr` and `/lib` |
| `/proc`, `/dev`, `/tmp` | new proc, minimal dev, tmpfs | no other process's `/proc` entry; a private `/tmp` |
| `$HOME` | tmpfs | the floor in `$HOME` does not exist |
| `$HOME/.config/opencode/opencode.json` | read-only, generated | the lane's limits; the owner's global config is not mounted, so its `allow` policies cannot lift anything (lc-yw1g.1) |
| `$HOME/.config/opencode/plugins/` | read-only: lane-control's `lane-persona.js` and Orca's OpenCode hook plugin | persona and Orca status keep working |
| `$HOME/.local/share/opencode/` | read-write: a per-lane folder `~/.local/state/lane-control/sandbox/<id>/opencode-data` | the session store; OpenCode needs no `auth.json` when the gateway key comes from the environment (setup.md), so none is there |
| the worktree | read-write | the lane's work |
| `<wt>/.opencode/`, `<wt>/opencode.json(c)`, `<wt>/orca.yaml`, `<wt>/.git` (the file) | read-only | its limits, Orca's host-side scripts, and its git dir pointer. `lane prepare` writes a `{}` placeholder for a missing root `opencode.json` (kept out of git through `.git/info/exclude`) so the agent cannot create one |
| `--private` paths in the worktree | tmpfs (a folder) or `/dev/null` (a file) | they do not exist inside. `prepare` normalizes the forms (`./x`, `x`, `x/`, lc-yw1g.10); a pattern with no fixed folder (`**/*.pem`) cannot be a mount, stays an agent-config deny, and `prepare` says so |
| the common git dir | read-write for `objects/`, own branch's ref folder, `logs/`, `FETCH_HEAD` | commits; objects are content-addressed and harmless until a ref names them |
| common `config`, `hooks/`, `info/`, `modules/*/{config,hooks}`, `packed-refs`, `refs/` except the lane's branch folder, `worktrees/` except its own | read-only | nothing outside later runs what the agent wrote (spike) |
| own admin dir `commondir`, `gitdir`, `config.worktree` | read-only (placeholder if missing) | the git dir pointer cannot be moved to a git dir the agent made |
| the beads DB folder (`.beads` target) | read-write, that folder only | `bd` works; the beads repo's own `.git` is not mounted |
| `lane-heavy` and its slot folder | read-only script; read-write slot folder (lock files only, moved out of lane-control's state folder) | the full suite is still serialised across lanes |
| tool paths | read-only `/nix/store`; PATH built from the project's tool list, built outside by `prepare` | no nix daemon inside |
| caches (`$HOME/.cache`, npm, pip, go, cargo) | read-write per project: `~/.cache/lane-control/sandbox/<project>` | a lane cannot poison the owner's caches; lanes of one project share theirs |
| `orca` | read-only shim on PATH: writes one request to the broker socket | see the broker |
| `gh`, `git push`, `git fetch` from private remotes | shim to the broker | the token stays outside |
| the broker socket `<run dir>/broker.sock` | read-write, that socket only | the only way out, and narrow |

**Environment allowlist**: `HOME`, `PATH` (built), `LANG`, `TERM`, `COLORTERM`, `USER`, `LOGNAME`, `SHELL`,
`TMPDIR=/tmp`, the lane markers (`LANE_ROLE`, `LANE_PERSONA`), `OPENCODE_CONFIG_CONTENT` (Orca's and the adapter's
model choice), the gateway key variable that OpenCode's provider config names (phase 2: a dummy), Orca's hook
variables (`ORCA_AGENT_HOOK_*`, `ORCA_PANE_KEY`, `ORCA_TAB_ID`, `ORCA_WORKTREE_ID`, `ORCA_TERMINAL_HANDLE`,
`ORCA_AGENT_LAUNCH*`) so Orca keeps its status. Never: `ORCA_CLI_COMMAND`, `ORCA_USER_DATA_PATH`, `GH_TOKEN`,
`GITHUB_TOKEN`, `SSH_AUTH_SOCK`, `DBUS_SESSION_BUS_ADDRESS`, `DISPLAY`, `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR`, any
other `*_TOKEN`, `*_KEY`, `*_SECRET`. The env extras (`--env NAME=value`) are added on top and refused for the
names above.

**The lane broker** (`core/scripts/lane-broker`, one per sandboxed agent, started by the shim before bwrap and gone
with it). It listens on a unix socket in the lane's run folder and answers one JSON request per connection:

- `orca`: `orchestration send|ask|check|reply` only, with `--from` and the task and dispatch ids forced to this
  lane's (from the profile); `--environment`, `--pairing-code`, `--on`, `--to` another dispatch are refused. It runs
  the real Orca CLI with the owner's environment.
- `push`: `git push origin HEAD:refs/heads/<lane branch>`, no `+`, no other ref, no other remote; `fetch`:
  `git fetch origin`. Both with `--git-dir` and `GIT_COMMON_DIR` from the profile (never from the worktree),
  `core.hooksPath=/dev/null`, `core.fsmonitor=false`, the owner's own credential helpers and ssh command left as they
  are (lc-8h1.1's lesson), and a refusal when a repo scope names a program (lc-8h1.2's list: hooks, `uploadpack`,
  `receivepack`, `gpg.*.program`, `core.gitProxy`, `core.sshCommand`, filters, `diff.external`).
- `gh`: `pr create|view|edit|comment|checks|list` and `run view|list` on the lane's repo only, `--repo` forced; no
  `api`, no `auth`, no `secret`. The token never enters the sandbox; gh reads it outside as it does now.
- Anything else: refused, with a line that names the request. Every request goes to the lane's log.

**What breaks, and what is done about it**

| Breaks | Handling |
|---|---|
| Orca's transcript view and `worker-read --source transcript` read `~/.local/share/opencode/opencode*.db`; the lane's store is elsewhere | Plan task 1 measures what Orca reads (it scans `$XDG_DATA_HOME/opencode` and `opencode-*.db`). If needed, the per-lane store is bound back into the host folder as `opencode-lane-<id>.db` through `OPENCODE_DB`; the other sessions' DB is never mounted. lane-control's own idle reader is told the profile's folder |
| `nix run`, `nix build` inside a lane | No daemon inside. `prepare` builds the project's tool list outside and puts it on PATH; a lane that needs another tool asks the coordinator |
| `git push`, `gh pr create`, `orca orchestration send` | the shims and the broker, above |
| `git config`, `git branch -d` of a packed branch, `git worktree add`, `git gc` | refused by read-only files; lanes do not need them |
| SSH remotes | the push goes through the broker, outside, with the owner's ssh as now |
| Language servers, browsers (design-shots, Playwright) | inside, from the store and the per-project cache; a browser needs no display (headless) |
| The persona plugin and Orca's hook plugin | bound read-only at their usual paths |
| A test that reads `$HOME` files | gets the tmpfs, as `tests/testhome.py` already arranges |

## bwrap availability

- Not on this machine's PATH. NixOS: `nix build --no-link --print-out-paths nixpkgs#bubblewrap` at machine setup,
  recorded as an absolute store path in `machine.conf` (`SANDBOX_BWRAP=/nix/store/<hash>-bubblewrap-0.12.0/bin/bwrap`)
  and kept from garbage collection by a GC root in lane-control's data folder (`nix build --out-link`); the shim
  runs only that path. `nix run nixpkgs#bubblewrap --` gives 0.12.0 today (the spike's).
- Unprivileged user namespaces are on (`user.max_user_namespaces` 2147483647). Orca itself runs inside a buildFHSEnv
  bwrap on NixOS, and the spike ran its bwrap nested inside that one: nesting works.
- A remote box: the same pinned nix package where nix exists; else the distro's `bubblewrap`, used only when the
  binary is not setuid (`stat` shows no setuid bit). Ubuntu 23.10+ restricts unprivileged user namespaces through
  AppArmor (`kernel.apparmor_restrict_unprivileged_userns=1`); bwrap then needs the distro's AppArmor profile for it.
  `machine-check` probes with `bwrap --unshare-user --ro-bind / / true` and, when the probe fails, the project's
  sandbox stays off on that box and lanes there are refused (fail closed). Never a setuid helper or a privileged
  container installed by lane-control.

## The spike

`docs/superpowers/spikes/2026-11-27-agent-sandbox/spike.sh` builds a fake owner HOME with a fake floor, a repo with
two worktrees, a `--private` folder and a link to a fake key, and runs `stub-agent.sh` (a shell script, never an
agent) three ways. Result on this machine (2026-11-27, bubblewrap 0.12.0 via `nix run`, nested in Orca's own bwrap):

- control, no sandbox: 22 leaks (every floor file, the link, the token, every protected write).
- naive, bwrap started with the owner's environment and `--clearenv`: one leak, the token in `/proc/1/environ` (bwrap's
  pid-1 helper). Hence `env -i` before bwrap.
- sandbox, as designed: every floor file absent, the link dangling, a recursive search finds no floor data, no token
  in any visible process; generated config, `.opencode/`, root `opencode.json`, the common `config`, `hooks/`,
  `info/`, another lane's admin dir and worktree not writable; `git config --local` refused; the worktree writable and
  a commit on the lane's branch made, visible outside.

Not covered by the spike, and in the plan: Orca's launch through the shim, the brokers, OpenCode itself.

## Claude lanes (phase 3)

The same wrapper on `claude`, with these differences:

- Auth: Claude reads `.credentials.json` from its config folder, a floor file. Options: a long-lived
  `CLAUDE_CODE_OAUTH_TOKEN` passed in the environment (readable by the agent, like the gateway key), or phase 2's
  key proxy through `ANTHROPIC_BASE_URL`. Never the owner's credentials file bound in.
- Config: a generated `CLAUDE_CONFIG_DIR` per lane (settings with the floor and Orca's hooks, `.claude.json`
  without tokens), bound read-only except `projects/`, `todos/`, `shell-snapshots/`; `<wt>/.claude/settings.json`
  and `settings.local.json` read-only placeholders (lc-yw1g.3).
- Orca: Claude gets `--plugin-dir ~/.config/orca/claude-agent-view/...` and Orca's managed hooks run scripts under
  `~/.config/orca/agent-hooks/`; those exact files are bound read-only. Orca reads Claude transcripts from its
  configured Claude folder (`ORCA_CLAUDE_CONFIG_DIR`); a per-lane folder needs Orca's managed accounts or the same
  bind-back as OpenCode's store: plan task.
- Claude's own Bash sandbox can stay on inside as a second layer.

## What this closes, and what stays

| Issue | With phase 1 (OpenCode lanes) |
|---|---|
| lc-mpb5 | **Closed** for every floor path with a fixed location: grep, shell and links find nothing because nothing is there. A `--private` pattern with no fixed folder stays an agent-config deny |
| lc-yw1g.1 | **Closed** for lanes: the floor no longer lives in config; the global config is not mounted; the root and project config are read-only. A committed root `opencode.json` can still lift the owner's advisory OpenCode denies (not the floor); force-push is stopped by the broker instead |
| lc-yw1g.2 | Closed for lanes (the floor holds whatever the config says); **stays** for the coordinator's launch folder, which is not sandboxed |
| lc-yw1g.3, lc-yw1g.4 | **Stay** until Claude lanes are sandboxed (phase 3), which closes them |
| lc-yw1g.5 | **Stays**: a guard-matching bug, not a boundary problem |
| lc-yw1g.6 | Partly: code-running names run only inside the sandbox for lanes; the broker clears its environment. **Stays** for unsandboxed sessions and lane-control's own scripts |
| lc-yw1g.7, lc-yw1g.9 | **Stay**: output redaction and the plugin's EPIPE, unrelated |
| lc-yw1g.8 | Closed for lanes: no gh token or `hosts.yml` inside, whatever the flag order |
| lc-yw1g.10 | Closed for the forms with a fixed folder (`prepare` normalizes them before it mounts); pattern-only stays |
| lc-8h1.1 | **Stays** (project-setup's own git, LIT-2); the broker follows its lesson |
| lc-8h1.2 | **Stays** for project-setup on a repo configured before; a sandboxed agent can no longer plant such config. Closed for onboarding once the onboarding agent runs through the same wrapper (plan task 9) |
| lc-8h1.3 | Becomes moot once onboarding is sandboxed: the agent cannot chmod a hook or touch git's files; **stays** until then |

## What stays open

- The coordinator and the owner's own sessions are unsandboxed (they need Orca, gh, push).
- Phase 1's network (above): exfiltration of what the lane can read, including its gateway key; loopback, LAN and
  metadata services; abstract sockets.
- Worktree content run outside by someone (the owner's `make`, direnv, an editor task): the PR review stays the
  check, as today.
- Local refs inside the lane's branch folder: with Orca's `<owner>/<name>` branch names, every lane's branch is in
  the same folder, so a lane can move another lane's local branch (not `main`, and nothing pushed: the broker pushes
  only its own). Open question 3.
- A kernel or bwrap bug. bwrap is small and widely used (Flatpak); it is pinned and updated with nixpkgs.

## Open questions for the owner

1. Launch hook: the PATH shim (lane-control writes `opencode`/`opencode2` links into the first PATH folder ahead of
   the real CLI, here `~/.local/bin`, with your yes), or you set Orca's custom command for OpenCode once by hand
   (Settings → Agents) to the shim? The design works with both; the shim needs no Orca change.
2. Network: phase 1 shares the host network (gateway key readable by the agent, loopback and LAN reachable). Go
   straight on to the key proxy and pasta netns (phase 2) before Claude lanes, or after?
3. Branch folders: may lane-control name lane branches `<owner>/<lane>/<name>` so each lane writes only its own
   ref folder, or is a lane moving another lane's local branch acceptable (the broker never pushes it)?
