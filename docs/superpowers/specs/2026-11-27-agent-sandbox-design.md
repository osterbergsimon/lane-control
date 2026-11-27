# Agent sandbox: bwrap around agent lanes (SBX-1)

Status: design, round 2, for the owner's approval. Issue lc-9r81. No product code yet: the plan is
`docs/superpowers/plans/2026-11-27-agent-sandbox.md`; the spike is `docs/superpowers/spikes/2026-11-27-agent-sandbox/`.

Round 1's review (B1-B8, M1-M8, m1, m2, n1) showed that a lane working in a worktree of the shared repository
cannot be made safe with read-only binds: the worktree, the object store and every git command run on them later
are shared with the unsandboxed side. Round 2 changes the approach: **the lane works in its own clone**, which no
host tool ever runs git in; its commits come back as a bundle that is checked outside; it reaches Orca, GitHub,
beads and the base branch only through typed broker requests; and it has its own network namespace. The last
section answers each finding in one line.

## Why

The owner, 2026-11-27: "go for sandbox". Protections written as agent config keep failing, because the agent can
write that config or go around it: lc-mpb5 (OpenCode reads floor paths through grep, the shell and links),
lc-yw1g.1 (one `allow` policy in a writable `opencode.json` lifts the floor), lc-yw1g.2-.10, and LIT-2's lc-8h1.1-.3
(git run on an agent-written repo runs the repo's programs; a gate that checks the agent's writes afterwards can be
fooled). What holds is a boundary the agent cannot write.

## Threat model

**Who.** The agent process of a lane or a reviewer, and every process it starts (tools, shell, plugins, MCP servers,
tests). Same uid as the owner. Its model may be steered by anything it reads (repo, issues, PR comments, web pages,
tool output): assumed hostile. The coordinator and the owner's own sessions are trusted and stay unsandboxed; they
must never run what a lane wrote before it is reviewed (see "Who runs lane-written code").

**Must never read** (the floor):
- Secrets and auth anywhere on the machine: `~/.ssh`, `~/.gnupg`, OpenCode's `auth.json` and `mcp-auth.json`,
  Claude's `.credentials.json` and `.claude.json`, `~/.config/gh`, `~/.netrc`, `~/.git-credentials`,
  `~/.docker`, `~/.aws`, `~/.kube`; `.env` and `.env.*` files, including untracked ones in the project's checkout.
- The project's `--private` paths, tracked or not.
- lane-control's config and state, the beads database, Orca's settings and data (`~/.config/orca`), the owner's
  global agent config (`~/.config/opencode`, `~/.claude`, `~/.agents`) beyond the generated copies listed below.
- The shared repository: other lanes' branches and commits, the owner's unpushed refs and stashes, the main
  checkout's files; other lanes' clones; the rest of `$HOME`.
- Secret values in the environment other than the one key the lane's agent needs (see "Network").

**Must never write** anything outside its own clone, its outbox and its per-lane cache; in particular nothing that
an unsandboxed process later loads or runs: the shared repo (objects, refs, config, hooks), any worktree the host
sees, the beads folder (its `hooks/`), caches or tools another lane or a reviewer uses, Orca's or lane-control's
files.

**Must never start or steer anything outside the sandbox.** Every route found, and what closes it:

| Route out | Closed by |
|---|---|
| Host git on a tree the agent wrote: a nested repo added as a gitlink whose config sets `core.fsmonitor` runs in any host `git status` (review B2; the spike's control shows it); a rewritten `.git` file, `commondir`, hooks or config | The lane works in its own clone. The host never runs git in it: Orca's worktree for the lane stays at the base and is not mounted inside; commits leave only as a bundle that the broker imports into the shared repo with fsck (no git runs in the clone); a reviewer gets a fresh clone of the pushed branch (git checkout never creates a nested `.git`). The spike checks all three and shows host git in the clone itself would run the program |
| Deleting or rewriting shared objects or refs (B4) | The shared repo is not mounted at all |
| Orca's CLI (`terminal create`, `agent launch`, `worker-start`, `send --to bot:...`, `--body-file <floor file>`) | `~/.config/orca` (its socket and `orca-runtime.json`) is not mounted. Typed broker requests only; no argv or path crosses (B1) |
| gh with file or editor flags (`--body-file`, `--attach`, `--template`, `--web`, `--editor`), other PRs | Typed broker requests; the PR is always the lane branch's own (B1) |
| beads hooks (`.beads/hooks/on_*`) that the coordinator's `bd` runs (B3) | The beads folder is not mounted; typed bd requests, DB path from lane-control's record |
| The session bus, user systemd, `ssh-agent`, `gpg-agent`, Wayland, PipeWire, docker or podman sockets | `/run`, `/var/run` and `$XDG_RUNTIME_DIR` are not mounted (`/run` is a tmpfs); their variables are not passed |
| X11's abstract socket and services on the host's loopback (an X server with XTEST is a way to type into other windows: M3) | Its own network namespace (pasta, below): no host abstract socket, no host loopback except ports forwarded on purpose. The spike checks both |
| The nix daemon: where the user is a nix trusted user, the socket can import unsigned store paths, which is root-equivalent | Only `/nix/store` is mounted, never `/nix/var` (M4; the spike checks the connect) |
| Another process's environment through `/proc`, including bwrap's own pid-1 helper, which keeps the environment bwrap was started with | `--unshare-pid` and a new `/proc`; bwrap and pasta are started from `env -i` (the spike's naive run shows the leak otherwise) |
| Typing into the pane's host shell with `TIOCSTI` | `--new-session`, unless the kernel refuses `TIOCSTI` to unprivileged callers (`dev.tty.legacy_tiocsti=0`, Linux 6.2+); then the flag is dropped so the TUI keeps its controlling terminal (M2) |
| Code another sandboxed agent runs later: a poisoned cache, a tool in a shared folder (M7) | Caches per lane, never shared; tools from `/nix/store` built outside from lane-control's own list |
| Lane-written files that an unsandboxed agent or person loads: `.claude/settings*.json` hooks, `.mcp.json`, `.envrc`, `orca.yaml` scripts, a Makefile run by a check (B7) | No host worktree ever holds lane-written files before review: Orca's worktree stays at the base; reviewers and check reruns run sandboxed on a fresh clone (or read only the PR diff); after review the owner's merge is the normal trust step |
| A pushed branch: `--force`, `main`, another repo, the owner's key | No key or token inside; the broker pushes only `refs/lanes/<lane>` to `origin`'s lane branch, fast-forward only |

**Network.** The lane needs its gateway or API, package registries and DNS. It gets its own network namespace from
pasta (passt, unprivileged): outbound traffic is NATed through the host, DNS is forwarded, host loopback is not
mapped (`--no-map-gw`), and only Orca's hook port and a loopback gateway port (if the gateway runs on the host) are
forwarded (`-T`). What stays open:
- **Exfiltration of anything the sandbox can read**: the lane's clone (the project's code at the base, not its
  private data) and the key its agent holds (the gateway or API key, and Orca's hook token). Phase 2's key proxy takes
  the gateway key out.
- **The internet and the LAN**: pasta forwards to anything the host can reach, including devices on the local
  network and, on a cloud box, the metadata endpoint (169.254.169.254), which can hand out the box's credentials.
  An egress allowlist (a filtering proxy) is phase 2 (open question 5).

## Approaches

| | What | For | Against |
|---|---|---|---|
| A1. bwrap on the agent command, lane in a worktree of the shared repo, read-only binds over what must not change (round 1) | — | Little copying; Orca's diff view works | **Rejected by review round 1; a patch does not hold.** The worktree is writable and host git runs in it (B2); the shared object store is writable (B4) or readable with every branch, stash and tracked private file (B5); path mounts can be renamed past (B6); unsandboxed agents run in the same worktree (B7) |
| **A2. bwrap on the agent command, lane in its own clone mounted at the Orca worktree's path** (recommended) | `prepare` clones the base branch alone; the shim runs the real agent under pasta + bwrap with that clone at the worktree path; commits leave as a bundle | The shared repo and the host worktree never hold agent-written bytes before review; mounts are simple (no read-only holes in a writable tree); Orca launches the agent exactly as now | A full copy per lane (disk, clone time on big repos); Orca's diff view for the lane stays empty (the PR is the view); every git exchange goes through the broker |
| B. bwrap around the whole Orca terminal | The pane's shell runs inside | — | Orca starts the pane's login shell and types into it; changing that is an Orca setting for every pane; no gain over A2 |
| B'. Orca's per-workspace environment recipes (`environmentRecipes` in `orca.yaml`) | A fresh runtime (VM, container, SSH host) per workspace with its own `orca serve` | Orca's own feature | A whole Orca server, agent login and pairing per workspace; no local bwrap recipe in 1.4.274 to 1.4.280. Revisit if Orca adds one |
| C. OpenCode's own sandbox | — | — | OpenCode 2.2.1 has none: its permissions and policies are config, the thing that failed |
| C'. Claude Code's own sandbox (bwrap on Linux) | Wraps Claude's Bash tool | Built in | Bash only (not Read, Edit, Grep, MCP); set in settings files; a command may ask to run unsandboxed. An inner layer at most |
| D. `systemd-run --user` | A transient unit with `ProtectHome=` and binds | Declarative, resource limits | A service is the user manager's child, not the pane's; `--scope` has no mount namespace. Used only for the memory cap (below) |
| E. Landlock alone | Kernel access rules, inherited, irreversible | Strong; TCP-port and abstract-socket rules | Cannot replace paths (HOME, the clone at the worktree path); a later inner layer |
| F. A rootless container | An image per lane | Familiar | An image to keep, the same mounts anyway; a privileged container is out (owner's rule) |

## How the wrapper gets into the launch

Checked on Orca 1.4.274 (its help, bundled skills and code, read not run) and the release notes through v1.4.280:

- `worker-start --agent opencode` uses Orca's catalog (`opencode`, or `opencode2 --standalone`) and runs that
  command line in the worker pane's login shell, so the binary is the first `opencode`/`opencode2` on that shell's
  PATH; a chat launch resolves the same name on Orca's own PATH. Orca also runs `opencode --version` before a model
  launch.
- Orca's own hook is Settings → Agents → custom command (`agentCmdOverrides[<agent>]`; a plain path for the chat
  path). The release notes add no sandbox or other agent-command feature.
- **Mechanism, without editing Orca's settings: a PATH shim.** `lane-control machine setup --sandbox` puts
  `opencode` and `opencode2` links to `core/scripts/lane-sandbox-shim` in the first PATH folder ahead of the real
  CLI on both PATHs (it asks first and never edits shell rc files), and records the real binary's path.
- **Prevention against detection.** The shim only works while it wins the PATH lookup. If PATH changes, the real
  agent starts unsandboxed in Orca's worktree (which holds only the base, but the agent can read the floor until
  stopped). The after-start check (below) detects that; it does not prevent it. Prevention needs an explicit path:
  Orca's custom command set once by the owner to the shim (open question 1), measured for terminal and chat
  launches in plan task 1. The shim passes the owner's own OpenCode tabs through unchanged either way.
- **Tickets, not a profile per worktree (B8, M6).** `lane-control lane prepare --sandbox --role lanes|reviewer|check`
  runs before `worker-start` and writes a one-shot launch ticket (`~/.local/state/lane-control/sandbox/tickets/
  <worktree-id>/<nonce>.json`: role, lane, issue, project, branch, clone path). The shim takes the oldest unused
  ticket for its worktree (an atomic rename), binds it to the pane's `ORCA_TERMINAL_HANDLE` (read outside, from the
  environment Orca gave the shim), starts the broker under that identity, and starts pasta + bwrap. A worktree of a
  sandbox-on project, other than the primary checkout, with no unused ticket is refused (fail closed): a relaunch
  or resume needs a new `prepare`.
- **After start.** The shim stays as the parent (it does not exec), reads bwrap's `--info-fd` (the sandboxed
  child's pid) and only then writes the run record `runs/<terminal>.json` (nonce, role, child pid, its mount
  namespace id). The coordinator's `lane-control lane started <worktree> --nonce <n>` (right after `worker-start`)
  checks that record against `orca-ide orchestration worker-show` (same terminal, live pid, a mount namespace other
  than the shim's) and stops the worker when it fails within 30 s.
- **Not used**: `orca.yaml` setup (its own terminal, cannot change the agent's command); Orca's managed accounts
  (move data, not the command); `terminal create --command` plus `worker-start --terminal` (loses Orca's OpenCode
  launch handling; plan task 1 measures it as the fallback).

## The lane's repository

- **Clone.** `prepare` runs, outside and before the agent exists: `git clone --no-local --single-branch --branch
  <base> --no-tags <shared repo> <clone>` (only objects reachable from the base: no other lane's commit, no stash,
  no unpushed ref; the spike checks two), then the lane branch, `origin/<base>` as a plain ref, and no remote. The
  clone lives in `~/.local/state/lane-control/sandbox/<lane>/repo` and is treated as hostile data: no lane-control
  code and no Orca view runs git in it (a test greps lane-control for git calls on that folder), and it is removed
  with `rm -rf` when the lane ends.
- **Private and secret files.** Untracked private data and untracked `.env` files never enter the clone. A
  `--private` path or a `.env`/`.env.*` file that is tracked anywhere in the base's history makes `prepare` refuse
  the lane and name the path (the spike checks it; open question 3).
- **Commits out.** Inside, the `git push` wrapper writes `git bundle create /run/lane/outbox/push.bundle <branch>
  ^origin/<base>` and asks the broker. The broker, outside, runs git only in the shared repo: `git -c
  fetch.fsckObjects=true -c transfer.fsckObjects=true fetch --no-tags --no-recurse-submodules
  --no-write-fetch-head <bundle> refs/heads/<branch>:refs/lanes/<lane>` (fast-forward only), then `git push origin
  refs/lanes/<lane>:refs/heads/<branch>` (no `+`). The bundle is data: git's pack parser reads it as it reads any
  remote. The shared repo is the owner's, never mounted in a sandbox, so its config and hooks are the owner's own:
  no refusal or override list is needed (m2).
- **The base in.** `fetch` (and the fetch half of `pull`): the broker fetches `origin/<base>` in the shared repo
  and writes a bundle of the new commits to the lane's inbox; the wrapper fetches it into `refs/remotes/origin/<base>`;
  the lane merges (lanes merge, never rebase: `pull --rebase` is refused with that line). The spike merges a base
  commit that changes `orca.yaml`.
- **Orca's worktree** for the lane stays at the base, untouched, so Orca's git status and the pane's host shell
  see nothing the agent wrote. Inside the sandbox the clone is mounted at that path, so the agent's cwd,
  `ORCA_WORKTREE_ID` and OpenCode's session folder match what Orca expects.
- **A reviewer** gets its own ticket (`--role reviewer --sha <pushed sha>`): `prepare` clones `refs/lanes/<lane>` at
  that sha from the shared repo into the reviewer's own folder, and the reviewer's sandbox mounts it at the same
  worktree path. Two agents in one Orca worktree each see their own clone.

## Who runs lane-written code (B7)

Nothing unsandboxed runs or loads a sandboxed lane's files before the owner merges a reviewed PR:
- the reviewer is sandboxed on its own fresh clone (an OpenCode reviewer in phase 1), or, if the owner allows it
  (open question 2), a Claude reviewer reads only `gh pr diff` and runs nothing from the branch;
- the coordinator's "rerun its fast checks" becomes `lane-control sandbox check <lane> --sha <sha> -- <command>`,
  a sandbox with role `check` (no broker requests but its output) on a fresh clone, or CI;
- the coordinator never checks out a lane's branch on the host; `gh pr checkout` is out for sandboxed lanes.

## Mount plan (OpenCode, phase 1)

pasta and bwrap are run by absolute store path, from `env -i`. bwrap flags: `--die-with-parent --unshare-pid
--unshare-ipc --unshare-uts --unshare-cgroup-try` (and `--new-session` unless `dev.tty.legacy_tiocsti=0`); the
network namespace is pasta's. The whole sandbox runs in `systemd-run --user --scope -p MemoryMax=<lane cap>` when a
user manager exists (a scope keeps the process tree).

| Path inside | From | Why |
|---|---|---|
| `/nix/store` | read-only | binaries; never `/nix/var` |
| `/usr`, `/bin`, `/lib`, `/lib64` | read-only, if present | system binaries; Orca's FHS environment on NixOS needs them |
| `/etc` | tmpfs, then read-only `passwd`, `group`, `hosts`, `nsswitch.conf`, `ssl`, `pki`, `static`, `localtime`, `zoneinfo`, and a generated `resolv.conf` naming pasta's DNS forward address | DNS and CAs work (M1); nothing else of `/etc` |
| `/proc`, `/dev`, `/tmp`, `/run` | new proc, minimal dev, tmpfs, tmpfs | no other process, no sockets |
| `$HOME` | tmpfs | the floor does not exist |
| `$HOME/.gitconfig` | read-only, generated: the owner's `user.name` and `user.email` only | commits work (M1); no includes, helpers or aliases |
| `$HOME/.config/opencode/opencode.json` | read-only, generated: the provider entry copied from the owner's global config without any inline key (refused if there is one), the model, lane-control's plugins | the lane's settings; the owner's config is not mounted |
| `$HOME/.config/opencode/{plugins,skills}/` | read-only: lane-control's `lane-persona.js`, the skills the extras give the lane (resolved outside) | persona and skills (M1) |
| Orca's OpenCode overlay (`OPENCODE_CONFIG_DIR` under Orca's userData) and `~/.config/orca/agent-hooks/endpoint.env` | read-only, those paths only | Orca's hooks and startup prompt (M1; plan task 1 measures which path `worker-start` takes) |
| the persona file `LANE_PERSONA` names | read-only, that file | the persona lives in the main checkout, which is not mounted |
| lane-control's pinned code tree | read-only | `lane-heavy` and other lane tools; no state |
| `$HOME/.local/share/opencode/` | read-write: the lane's own folder | OpenCode's store; no `auth.json` (the key comes from the environment) |
| `$HOME/.cache` | read-write: the lane's own folder | caches per lane, removed with it (M7) |
| the Orca worktree's path | read-write: the lane's clone | the lane's work |
| `/run/lane/inbox` | read-only | base bundles from the broker |
| `/run/lane/outbox` | read-write | push bundles to the broker |
| `/run/lane/broker.sock` | the broker's socket | the only way out |

Inside, PATH holds the tool list (lane-control's project store, never the worktree; built outside by `prepare`),
lane-control's inside wrappers `orca`, `orca-ide`, `gh`, `git`, `bd`, `lane-heavy`, and the real `git`.

**Environment**: only these names are passed: `HOME`, `PATH`, `LANG`, `LC_ALL`, `TERM`, `COLORTERM`, `USER`,
`LOGNAME`, `SHELL`, `TMPDIR`, `TZ`, `TZDIR`, `LOCALE_ARCHIVE`, `SSL_CERT_FILE`, `NIX_SSL_CERT_FILE`, `LANE_ROLE`,
`LANE_PERSONA`, `OPENCODE_CONFIG_CONTENT`, `OPENCODE_CONFIG_DIR`, the gateway key variable the provider config names,
and Orca's pane variables `ORCA_AGENT_HOOK_*`, `ORCA_PANE_KEY`, `ORCA_TAB_ID`, `ORCA_WORKTREE_ID`,
`ORCA_TERMINAL_HANDLE`, `ORCA_AGENT_LAUNCH*`, `ORCA_OPENCODE_*`. Plan task 1 measures what each Orca token
authorizes; one that reaches more than this pane's hooks is not passed. Nothing else, whatever its name (one rule:
the list, M1). The env extras are added on top only when they name none of the above and no
`*_TOKEN`/`*_KEY`/`*_SECRET`; MCP extras that need a secret are refused for sandboxed lanes in phase 1.

## The broker

One per sandboxed agent, started by the shim before bwrap and gone with it. It runs outside with the environment
the shim got from Orca minus the lane's env extras, listens on `/run/lane/broker.sock` (bound in from the lane's run
folder), and takes one JSON request per connection, up to 1 MB. **No argv and no path crosses** (B1): each request
has typed fields; text goes to the real CLI through a file the broker itself creates (mode 0600, in its own
folder), never a path the lane names; option values are passed as `--opt=value`; unknown fields are refused; every
request and answer goes to the lane's log.

| Request | Runs outside | Who |
|---|---|---|
| `report {task, dispatch, subject, body, outcome, files}` | `orca-ide orchestration send --from=<bound terminal> --type=worker_done --task-id= --dispatch-id= --subject= --body-file=<own file> --outcome= [--files-modified=]`, after `dispatch-show` confirms the dispatch belongs to the bound terminal | lane, reviewer |
| `escalate {task, dispatch, subject, body}` | the same with `--type=escalation` | lane, reviewer |
| `ask {question}` | `orchestration ask --from=<bound terminal> --question=` | lane, reviewer |
| `check` | `orchestration check --terminal=<bound terminal> --json`; the message ids it returns are remembered | lane, reviewer |
| `reply {id, body}` | `orchestration reply --from=<bound terminal> --id= --body-file=`, only for an id `check` returned | lane, reviewer |
| `push` | the bundle import and push above | lane |
| `fetch` | fetch `origin/<base>` in the shared repo, bundle the new commits to the inbox | lane, reviewer |
| `pr-create {title, body}` | `gh pr create --repo=<repo> --head=<branch> --base=<base> --title= --body-file=` | lane |
| `pr-view`, `pr-checks`, `pr-diff` | on the lane branch's own PR (`gh pr list --repo --head <branch>`, same repo), fixed `--json` fields | lane, reviewer |
| `pr-comment {body}`, `pr-edit {title?, body?}` | on that PR only | lane |
| `bd-show {id}`, `bd-list-ready` | `bd` in the beads repo from lane-control's record | lane, reviewer |
| `bd-note {text}`, `bd-claim`, `bd-create {title, description, type, priority}` | on the lane's own issue; a new issue gets `discovered-from:<lane issue>` | lane |
| `review-read {name}` | read a file from the owner's review folder (configured; name `^[a-z0-9.-]+\.md$`) | lane, reviewer |
| `review-write {name, text}` | create (not overwrite) a file there | reviewer |
| `heavy-slot` | holds a `lane-heavy` slot (flock outside) for as long as the connection stays open | lane, reviewer, check |

Never: `orchestration` verbs other than these, `--to`, `--environment`, `--on`, `--file`, gh `api`, `auth`,
`secret`, `--web`, `--editor`, `--attach`, `--template`, `--recover`, another PR, `bd close`, `bd delete`,
`bd remember`.

## What breaks, and what is done about it

| Breaks | Handling |
|---|---|
| Orca's diff and changes view for the lane is empty (its worktree stays at the base) | The PR is the view; the lane comment on the worktree card names it |
| A clone per lane and per reviewer | Disk and clone time; `prepare` says the size; a big repo is the owner's call (open question 6) |
| Orca's transcripts read `~/.local/share/opencode/opencode*.db` on the host | Plan task 1 measures; if needed the lane's store is bound back as `opencode-lane-<id>.db` through `OPENCODE_DB`; lane-control's idle reader reads the lane's folder |
| `nix run` / `nix build` inside | No daemon; the tool list is built outside; `nix` inside says so |
| `git push`, `pull`, `fetch`, `gh`, `bd`, `orca-ide` | inside wrappers to the broker |
| `git pull --rebase`, a rebased branch | refused: lanes merge; the broker pushes fast-forward only |
| Resume or relaunch of the agent | needs a new ticket: the coordinator runs `prepare` again |
| `lane-heavy`'s per-command memory cap and the owner's slot limits | slots through the broker; one memory cap for the whole lane (the scope); `machine.conf` values the lane needs are generated into a read-only file |
| MCP extras that need a token | refused for sandboxed lanes in phase 1 |
| A TUI without a controlling terminal (resize, `/dev/tty`) | only where `legacy_tiocsti` is not 0; `machine-check` says so |
| Claude reviewers of a sandboxed lane | PR diff only, or OpenCode reviewers (open question 2) |

## Availability of bwrap and pasta

- Pinned at machine setup: `nix build --no-link --print-out-paths nixpkgs#bubblewrap nixpkgs#passt`, absolute paths
  in `machine.conf` (`SANDBOX_BWRAP`, `SANDBOX_PASTA`), kept from garbage collection by GC roots in lane-control's data
  folder (`nix build --out-link`). The spike used bubblewrap 0.12.0 and passt 2025_09_19.
- Needs unprivileged user namespaces. On NixOS, Orca itself runs inside a buildFHSEnv bwrap; the spike ran its pasta
  and bwrap nested inside that one.
- A remote box: the same nix pins where nix exists, else the distro's packages, used only when not setuid. Ubuntu
  23.10+ restricts unprivileged user namespaces through AppArmor; bwrap then needs the distro's profile.
  `machine-check` probes (`bwrap --unshare-user --ro-bind / / true`, and pasta with one forwarded port); when either
  fails the sandbox stays off on that box and its lanes are refused. Never a setuid helper or a privileged
  container installed by lane-control.
- `lane-control sandbox selftest` (the spike's checks, grown into the plan's adversarial suite) must pass on the
  machine before `machine setup --sandbox` turns the sandbox on, and again after a pin or lane-control update; a
  skipped check counts as a failure (M8).

## The spike

`docs/superpowers/spikes/2026-11-27-agent-sandbox/spike.sh` runs the design's own launch in small: the clone,
pasta with one forwarded port, the mount plan above (without the Orca and OpenCode paths, which need a real launch),
and the broker's import and push done by hand after the run. Result (bubblewrap 0.12.0, passt 2025_09_19, nested in
Orca's FHS bwrap): PASS.
- control (no sandbox, in the Orca worktree as today): 19 leaks, and a host `git status` there runs the program the
  stub planted (B2 reproduced).
- naive (bwrap from the owner's environment): exactly the `/proc/1/environ` leak.
- sandbox: no floor file, link or search hit; no token in any visible process; the nix daemon socket, a host
  loopback port that is not forwarded and a host abstract socket unreachable, the forwarded port reachable; the
  shared repo absent, another lane's commit and an unpushed ref not in the clone; commit with the generated
  identity; base merged from an inbox bundle (it changes `orca.yaml`); push by bundle. Outside: the bundle imports
  with fsck and pushes fast-forward; host git in the Orca worktree, the main checkout and a fresh checkout of the
  pushed branch runs nothing; host git in the lane's own clone does (why no host tool may).
- refuse: a tracked private file makes prepare refuse the lane.

Not covered by the spike, and in the plan: Orca's launch through the shim, the broker's socket and its checks,
OpenCode itself, a pty.

## Claude lanes (phase 3)

The same shim, clone and broker for `claude`, plus: auth by a `CLAUDE_CODE_OAUTH_TOKEN` in the environment (readable
by the agent, like the gateway key) or phase 2's key proxy, never the credentials file; a generated
`CLAUDE_CONFIG_DIR` per lane (settings, `.claude.json` without tokens), read-write only for `projects/`, `todos/`,
`shell-snapshots/`; Orca's Claude plugin folder and hook scripts bound read-only; how Orca reads a relocated Claude
transcript folder is measured first. Claude's own Bash sandbox can stay on inside.

## What this closes, and what stays

| Issue | For sandboxed OpenCode lanes, phase 1 |
|---|---|
| lc-mpb5 | **Closed for floor paths**: nothing outside the clone is mounted; untracked private data and `.env` are not in the clone; tracked ones refuse the lane; no path crosses the broker. The agent's own gateway key and Orca hook token stay readable in its environment (lc-yw1g.8) |
| lc-yw1g.1 | **Closed for lanes**: no shared config is mounted and no unsandboxed agent loads the clone; a root `opencode.json` the agent writes changes only its own session's advisory rules. Stays for the coordinator and the owner's own sessions |
| lc-yw1g.2 | **Stays**: the coordinator's launch folder is not sandboxed |
| lc-yw1g.3 | Partly: a lane's settings no longer reach an unsandboxed Claude (the worktree stays at the base, reviewers get their own clone); **stays** for Claude sessions until phase 3 |
| lc-yw1g.4 | **Stays** until phase 3 |
| lc-yw1g.5 | **Stays**: a guard-matching bug |
| lc-yw1g.6 | Partly: code-running names run only inside a lane; the broker drops the lane's env extras. **Stays** for unsandboxed sessions |
| lc-yw1g.7, lc-yw1g.9 | **Stay**: unrelated |
| lc-yw1g.8 | Partly: no gh token, `hosts.yml` or Orca runtime token inside, whatever the flag order. **Stays** for `env -0` and `printenv`, which print the gateway key and Orca's hook token (phase 2 removes the gateway key) |
| lc-yw1g.10 | Closed for lanes: private data is not mounted by path any more; `prepare` normalizes the forms for its history check |
| lc-8h1.1 | **Stays** (project-setup's git); the broker runs the owner's git unchanged in the owner's repo |
| lc-8h1.2 | **Stays** for project-setup; a sandboxed lane cannot write the shared repo's config, and no host git runs in its clone. Onboarding is lc-pk2c's, not this plan's |
| lc-8h1.3 | **Stays**; lc-pk2c may make it moot |

## What stays open

- The coordinator and the owner's own sessions are unsandboxed.
- Exfiltration of what a lane can read (its clone, its key); the internet and the LAN, including a cloud box's
  metadata endpoint.
- A detected, not prevented, unsandboxed start while the PATH shim is the hook (open question 1).
- After the owner merges a PR, its code runs like any merged code.
- A kernel, bwrap, pasta or git pack-parser bug.

## Open questions for the owner

1. Launch: the PATH shim (no Orca change; an unsandboxed start is detected and stopped, not prevented), or Orca's
   custom command for OpenCode set once by you to the shim (prevents it; your own OpenCode tabs pass through the
   shim unchanged)?
2. Reviewers and check reruns of a sandboxed lane: must they be sandboxed (OpenCode reviewers only until phase 3),
   or may a Claude reviewer review from the PR diff without running anything?
3. `--private` and `.env` files tracked in the base's history: refuse the lane (this design), or allow it with those
   files listed as readable by the lane?
4. Beads: may a lane add notes, claim its issue and file follow-ups through the broker, or no beads writes?
5. Network: phase 1 reaches the internet and the LAN (and a cloud box's metadata endpoint). Add an egress allowlist
   before the first sandboxed lane, or in phase 2?
6. A clone per lane and per reviewer: fine for every project, or a size limit above which a project is not
   sandboxed?

## Review round 1, answered

| Finding | Where |
|---|---|
| B1 broker passes file and recipient flags | Typed requests, no argv or path crosses, bound mailbox and own PR ("The broker") |
| B2 nested repo runs in host `git status` | Own clone; no host git in it; worktree at base; fresh checkout for reviewers; spike checks it and shows the control |
| B3 beads hooks | Beads folder not mounted; typed bd requests; DB path from lane-control's record |
| B4 writable shared objects | Shared repo not mounted; bundle import with fsck |
| B5 tracked private files, other refs, stashes | Single-branch clone; tracked private and `.env` refuse the lane; spike checks both |
| B6 renamed parent of a private mount | Moot: no private path is mounted or masked; private data is never in the clone |
| B7 lane code run by unsandboxed reviewer or checks | "Who runs lane-written code"; open question 2 |
| B8 broker cannot know the dispatch; reviewer shares the profile | Tickets bound to the pane's terminal handle; dispatch checked with Orca per request; a ticket per agent and role |
| M1 DNS, identity, merges, lane-heavy, personas, Orca overlay, tokens, CAs | Mount plan, environment list, "What breaks". The spike checks identity and a merge that changes `orca.yaml`; DNS and outbound TCP through pasta's forward were checked by hand (they need the internet, so they are not in the spike); the Orca paths are plan task 1 |
| M2 `--new-session` | Dropped where `legacy_tiocsti=0`, kept otherwise; pty test in the plan |
| M3 shared network is a way out | Own network namespace in phase 1; spike checks abstract socket and loopback |
| M4 spike mounts `/nix` and writable common dir | Spike rewritten to the design's mounts; only `/nix/store`; daemon connect checked |
| M5 claim table overstated | Rewritten above |
| M6 `lane started` proves nothing | Record after bwrap's `--info-fd`, nonce per ticket, checked against `worker-show`; said to detect, not prevent |
| M7 shared caches | Per lane; tool list from lane-control's store |
| M8 no tests for the escapes | Adversarial suite and `sandbox selftest` as a required gate where a skip fails (plan) |
| m1 open questions | Rewritten above |
| m2 refusal lists differ | No list needed: the broker runs git only in the owner's repo, which no sandbox mounts |
| n1 machine fact | Removed; written as a condition |
