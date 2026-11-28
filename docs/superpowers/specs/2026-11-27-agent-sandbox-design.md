# Agent sandbox: bwrap around agent lanes (SBX-1)

Status: round 3, approved by the owner 2026-11-28 ("recs approved"; lc-9r81.4); phase 1 built in SBX-2 (lc-35qn,
plan tasks 1-8): `core/sandbox.py`, `core/sandboxprep.py`, `core/scripts/lane-sandbox-shim`, `core/scripts/lane-broker`,
`core/scripts/lane-sandbox`, `core/sandbox-inside/`, and the adversarial suite `tests/test_sandbox_escapes.py`. Issue
lc-9r81. The plan is `docs/superpowers/plans/2026-11-27-agent-sandbox.md`; the spike and the task 1 measurements are
in `docs/superpowers/spikes/2026-11-27-agent-sandbox/`. The owner's answers are under "Open questions"; what the build
changed is under "As built (SBX-2)". A live lane (plan task 9) waits for the owner's go.

Round 1 moved the lane into its own clone. Round 2's review (B1-B6, M1-M6, m1-m4) found that the launch kept
capabilities, the broker trusted the lane's outbox, the launch could not start as written, the history check missed
side branches, CI runs lane code, and a relaunch lost work. Round 3 fixes each one; every fix is a spike probe or a
line here with its reason, and the last two sections answer round 1 and round 2 one finding per line.

## Why

The owner, 2026-11-27: "go for sandbox". Protections written as agent config keep failing, because the agent can
write that config or go around it: lc-mpb5 (OpenCode reads floor paths through grep, the shell and links),
lc-yw1g.1 (one `allow` policy in a writable `opencode.json` lifts the floor), lc-yw1g.2-.10, and LIT-2's lc-8h1.1-.3.
What holds is a boundary the agent cannot write.

## Threat model

**Who.** The agent process of a lane or a reviewer, and every process it starts. Same host uid as the owner. Its
model may be steered by anything it reads: assumed hostile. The coordinator and the owner's own sessions are trusted
and stay unsandboxed; they must never run what a lane wrote before review ("Who runs lane-written code").

**Must never read** (the floor):
- Secrets and auth anywhere on the machine (`~/.ssh`, `~/.gnupg`, OpenCode's `auth.json` and `mcp-auth.json`,
  Claude's `.credentials.json` and `.claude.json`, `~/.config/gh`, `~/.netrc`, `~/.git-credentials`, `~/.docker`,
  `~/.aws`, `~/.kube`), and `.env`/`.env.*` files at any depth, tracked or not, except the template names in
  "The lane's repository".
- The project's `--private` paths, tracked or not, anywhere in the delivered history.
- lane-control's config and state, Orca's settings and data, the owner's global agent config beyond the generated
  copies below; the beads database beyond the lane's own issue and the issues it lists as links; the owner's review
  folder beyond the files `prepare` names for this ticket.
- The shared repository (other lanes' branches and commits, the owner's unpushed refs and stashes, the main
  checkout), other lanes' clones, the rest of `$HOME`.
- Secret values in the environment other than the one key its agent needs, and nothing secret through host argv.

**Must never write** anything outside its own clone, its outbox and its own cache and data folders; in particular
nothing an unsandboxed process later loads or runs, and no read-only bind (remount included).

**Must never start or steer anything outside.** Every route found, what closes it, and the check:

| Route out | Closed by | Checked by |
|---|---|---|
| Remount a read-only bind and write the host file (r2 B1: under pasta the agent kept `CAP_SYS_ADMIN`) | `bwrap --cap-drop ALL`: CapEff 0, no remount, no umount, no nested user namespace | spike `caps` (without the flag: the host file is written) and `sandbox` (with it: refused) |
| Host git on a tree the agent wrote (r1 B2: a nested repo's `core.fsmonitor`) | the lane's own clone; no host git ever runs in it; Orca's worktree stays at the base | spike `control` (runs) and `sandbox` (Orca worktree, main checkout, fresh checkout of the pushed branch: nothing runs) |
| The broker handed a lane-made path (r2 B2: a link to a lane-made repo, an id-only bundle naming the owner's unpushed commit) | the outbox file is opened no-follow, non-blocking, regular, under a size cap, and copied; it is imported first into a broker-only quarantine repo that holds only the lane's base and earlier pushes; exactly the lane ref, descending from the recorded base, fast-forward | spike: round 2's import takes entries a and c; the design refuses a-g and accepts the honest push |
| CI running lane-written code or workflow files with the repo's token and secrets (r2 B5) | the broker refuses a push whose new commits change CI configuration unless the owner approves that sha; existing workflows still run lane code: open (question 7, lc-9r81.3) | spike: entry h refused |
| Shared objects and refs (r1 B4) | the shared repo is not mounted | spike: the shared repo is absent |
| Orca's CLI and socket, gh's token, beads hooks (r1 B1, B3) | not mounted; typed broker requests only | broker tests (plan task 5) |
| Session bus, user systemd, agent sockets, Wayland, docker | `/run` and `$XDG_RUNTIME_DIR` not mounted; systemd-run gets the bus variables, bwrap unsets them by name | spike: `/run` search; the stub sees no bus variable |
| The nix daemon (where the user is a nix trusted user, root-equivalent) | only `/nix/store` is mounted | spike: connect refused |
| Host loopback TCP and UDP, abstract sockets (X11), ports opened inside, the host's other addresses, the LAN, a cloud box's metadata endpoint (r1 M3, r2 M1) | pasta's own network namespace with `-t none -u none -U none`, only Orca's hook port forwarded; an nft output filter, set from outside before the agent starts, that drops the host's addresses, link-local (the metadata endpoint included; pasta's DNS address excepted) and private ranges (10/8, 172.16/12, 192.168/16, 100.64/10, fc00::/7, fe80::/10), with the gateway's address and port allowed when it is on the LAN | spike: UDP and TCP loopback, abstract socket, every host address, inbound from every host address; the stub's `nft flush` fails |
| Another process's environment through `/proc`; secrets in host argv (r2 M2) | bwrap and pasta start from `env -i` with the allowlist as environment, never as arguments (no `--clearenv --setenv`) | spike `naive` (round 2's start: the token in `/proc/1/environ`, the key in host argv) and `sandbox` (neither) |
| `TIOCSTI` into the pane's shell | `--new-session` unless `dev.tty.legacy_tiocsti=0` | `machine-check` (plan task 7) |
| A poisoned cache or tool used by a reviewer or another lane | caches, data and run folders per ticket; tools from `/nix/store`, built outside from lane-control's list | plan task 3 |
| A pushed branch: force, `main`, another repo | no key or token inside; the broker pushes only its quarantined lane ref, fast-forward, no `+` | spike: origin holds the lane's work and not the owner's commit |

**Network, what stays open**: anything on the internet the host can reach (exfiltration of the clone, the agent's
key and Orca's hook token, which the agent holds). Phase 2 (lc-0zae) adds a key proxy and an egress allowlist.

## Approaches

| | What | For | Against |
|---|---|---|---|
| A1. Lane in a worktree of the shared repo with read-only binds (round 1) | — | — | Rejected by round 1's review: host git runs in it (B2), the shared store is writable or readable (B4, B5), path mounts can be renamed past (B6), unsandboxed agents share it (B7) |
| **A2. Own clone mounted at the Orca worktree's path; pasta outside, bwrap inside with `--cap-drop ALL`** (recommended) | below | The shared repo and host worktrees never hold agent-written bytes before review; no capability inside; one launch chain the spike runs | A copy per lane; Orca's diff view empty; every git exchange through the broker; the agent is uid 0 (no capabilities) inside pasta's user namespace (lc-9r81.2) |
| A2'. bwrap with its own user and network namespace, pasta attached from outside | uid 1000 inside, bwrap's `--info-fd` gives the host pid directly | Cleaner uid and pid | pasta refused to join bwrap's namespaces here ("Couldn't switch to pasta namespaces: Operation not permitted", by PID and by `--netns`/`--userns`), nested in Orca's FHS env; `nsenter` could. Measured again in lc-9r81.2; if pasta can attach elsewhere, A2' replaces A2's chain |
| B. bwrap around the whole Orca terminal | — | — | Orca owns the pane's shell; an Orca setting for every pane |
| B'. Orca's environment recipes | a runtime per workspace | Orca's feature | A whole Orca server per workspace; no local bwrap recipe up to 1.4.280 |
| C, C'. OpenCode's or Claude's own sandbox | — | — | OpenCode 2.2.1 has none; Claude's covers Bash only and lives in settings |
| D. `systemd-run --user` as the sandbox | — | — | Not the pane's child; used only for the memory scope |
| E. Landlock alone | — | — | Cannot replace paths; a later inner layer |
| F. Rootless container | — | — | Same mounts, an image to keep |

## The launch

**Hook** (owner 2026-11-28, question 1): Orca's custom command for OpenCode, which the owner sets once by hand in
Orca (Settings -> Agents -> OpenCode 2, and OpenCode) to the installed launcher:
`<data>/lane-control/current/core/scripts/lane-sandbox-shim opencode2 --standalone` (and `... lane-sandbox-shim
opencode`). lane-control never edits Orca's settings and uses no internal RPC; `lane-control machine setup --check --sandbox`
prints the exact text and says whether it is set, reading Orca's settings document read-only. Orca's catalog command is
replaced whole by a custom command, and a customized agent's chat runs through the same command (lc-9r81.1,
`orca-launch-probe.md`). The PATH shim stays only as a detector: `machine setup --sandbox` offers links named
`opencode`/`opencode2` to the same launcher first on PATH (after asking); started under one of those names in a
sandboxed lane's worktree it refuses, so a start that did not come through the custom command never runs unsandboxed
there, and `lane started` stops the worker.

**Tickets**: `lane-control lane prepare <project> <worktree> --sandbox --role lanes|reviewer|check` writes a one-shot
ticket (`~/.local/state/lane-control/sandbox/tickets/<worktree-id>/<nonce>.json`) naming the lane, role, clone and
this ticket's own cache, data and run folders. The shim takes the oldest unused ticket for its worktree (atomic
rename), binds it to the pane's `ORCA_TERMINAL_HANDLE`, and refuses without one. The owner's own tabs, the primary
checkout, `--version`, `models` and `--help` pass through unchanged.

**Chain** (what the spike runs, in this order):
1. The shim makes `<run>/launch/block` (a FIFO it holds open) and starts the broker.
2. `env -i <allowlist, as environment> [DBUS_SESSION_BUS_ADDRESS XDG_RUNTIME_DIR] systemd-run --user --scope -q -p
   MemoryMax=<lane cap> -- pasta --config-net -q -f --no-map-gw --dns-forward 169.254.1.53 -t none -u none -U none -T
   <hook port>[,<loopback gateway port>] -- bash -c 'exec "$@" 3>"$info" 4<"$block"' <info> <block> bwrap
   --cap-drop ALL --unsetenv DBUS_SESSION_BUS_ADDRESS --unsetenv XDG_RUNTIME_DIR <mounts> --info-fd 3 --block-fd 4
   <real agent> <args>`. pasta closes inherited descriptors, so the info file and the block FIFO are opened by path
   inside pasta. Without a user manager the scope is skipped and `machine-check` says the lane has no memory cap.
3. bwrap writes `child-pid` (pasta's pid namespace) and `mnt-namespace` and waits on fd 4. The shim finds the host
   pid: the process whose mount namespace is that id and whose second `NSpid` field is `child-pid`.
4. The shim sets the nft filter: `nsenter --preserve-credentials -t <host pid> -U -n nft -f <rules>` (the owner's uid
   owns pasta's user namespace; the agent, with no capabilities, cannot change it: spike).
5. The shim writes the run record (`runs/<terminal>.json`: nonce, role, host pid, mount namespace id), then writes to
   the FIFO: bwrap starts the agent.
6. The shim stays the parent, forwards SIGINT, SIGTERM, SIGHUP and SIGWINCH, and exits with the agent's status.

**After start**: `lane-control lane started <worktree> --nonce <n>` (the coordinator, right after `worker-start`) checks
the run record: same terminal as `orca-ide orchestration worker-show` gives, host pid alive and in the recorded mount
namespace. Without it in 30 s it stops the worker. Detection, not prevention.

**Inside the agent is uid 0 with no capabilities** (pasta's user namespace maps the owner's uid to 0; bwrap cannot
map 1000 there: "setting up uid map: Operation not permitted"). node, npm, git and python run as uid 0 without
capabilities; nothing can be chowned or setuid to another uid (lc-9r81.2, `uid0-probe.md`). OpenCode itself as uid 0
is measured in the live lane (task 9).

## The lane's repository

- **Clone** (`prepare`, before the agent exists, git only in the shared repo and in a folder no agent has touched):
  `git clone --no-local --single-branch --branch <base> --no-tags`, then the lane branch, `refs/remotes/origin/<base>`,
  and `remote.origin.url` set to a path that does not exist (so `status` can say "ahead of origin"; the wrappers handle
  every command that would contact it). A broker-only bare **quarantine** clone of the base sits beside it. The
  recorded base is the base's sha at this point.
- **History check** (r2 B4), on the clone before hand-over, on every base bundle, and on reviewer and check clones:
  every path any delivered commit adds or changes against each parent (`git rev-list` of what is delivered, piped to
  `git diff-tree --stdin -r -m --root --name-only --no-renames`), so a file added and removed on a merged side branch
  is seen; matched case-insensitively at any depth against the normalized `--private` paths (`./x/`, `x/`, `x` are one
  path) and against `.env`/`.env.*` other than `.env.example`, `.env.sample`, `.env.template`, `.env.dist`. A hit
  refuses the lane or the delivery and names the path. A renamed or re-encoded copy of private data under another name
  is not detected (stays open).
- **Push** (r2 B2): the inside `git push` writes `/run/lane/outbox/push.bundle` and asks the broker, which (1) opens it
  `O_NOFOLLOW|O_NONBLOCK`, requires a regular file of at most 64 MB and copies it into its own folder; (2) requires
  `git bundle list-heads` to give exactly `refs/heads/<lane branch>`; (3) fetches it into the quarantine with fsck
  (objects it lacks make the fetch fail, so an id-only bundle cannot name the owner's commits); (4) requires the new
  tip to descend from the recorded base and, after the first push, from the previous push; (5) refuses when any new
  commit changes CI configuration (`.github/workflows/`, `.github/actions/`, `.gitlab-ci.yml`, `.gitlab/`,
  `.circleci/`, `.buildkite/`, `azure-pipelines.yml`, `Jenkinsfile`, `.travis.yml`, `bitbucket-pipelines.yml`) unless
  the owner ran `lane-control lane approve-ci <lane> <sha>`; (6) only then fetches from the quarantine into the shared
  repo's `refs/lanes/<lane>` and pushes that to `origin`'s lane branch without `+`. git never reads a lane-made path.
- **Base in**: the broker fetches `origin/<base>` in the shared repo, history-checks the new commits, bundles them to
  the inbox and updates the quarantine's base; the inside `git fetch`/`pull` takes the bundle (merge only; `pull
  --rebase` refused).
- **Resume** (r2 B6): `prepare` for a lane whose clone exists reuses it, untouched (no host git in it), with a new
  ticket. A lane whose clone is gone but whose `refs/lanes/<lane>` exists gets a clone recreated from that ref
  (`--no-checkout` from the shared repo; the checkout runs inside the sandbox). The lane identity is the lane key, not
  the Orca worktree: `prepare` can bind the clone to a new Orca worktree of the same lane.
- **Lane end**: `lane-control sandbox inspect <lane>` runs a sandboxed step in the clone that reports head, dirty files
  and commits not in `refs/lanes/<lane>`; the clone, quarantine and folders are removed only on a clean report with
  nothing unpushed, or with the owner's `--discard`. The report comes from the lane's own clone, so a hostile clone
  can lie about itself; that loses only its own work.

## Who runs lane-written code

- **Reviewers and check reruns** of a sandboxed lane always run sandboxed (owner 2026-11-28, question 2), each with
  its own ticket and clone of the pushed sha, cache, data and run folders.
- **The coordinator** never checks out a sandboxed lane's branch and never reruns its checks on the host
  (`lane-control sandbox check <lane> --sha <sha> -- <command>` instead).
- **CI does, before any review** (r2 B5). On `pull_request`, a same-repo branch's workflows run with the branch's own
  workflow files, the repository's token (whatever `permissions:` and the repo's default allow) and the secrets and
  runners the repository exposes to them. In this repo, `test.yml` runs the branch's test suite on `ubuntu-latest`
  with `contents: read`. The broker refuses a push that changes CI configuration without the owner's approval; it
  cannot stop an existing workflow from running lane code. So `prepare` checks with `gh api` that the repository's
  default workflow token is read-only, that it has no Actions secrets (nor organization or environment secrets) and
  no self-hosted runners, and refuses a sandboxed lane otherwise; `machine setup --check --sandbox` reports the same per
  project (owner 2026-11-28, question 7; `ci-probe.md`).

## Mount plan (OpenCode, phase 1)

| Path inside | From | Why |
|---|---|---|
| `/nix/store` | read-only | binaries; never `/nix/var` |
| `/usr`, `/bin`, `/lib`, `/lib64` | read-only, if present | system binaries (Orca's FHS environment) |
| `/etc` | tmpfs, then read-only `passwd`, `group`, `hosts`, `nsswitch.conf`, `ssl`, `pki`, `static`, `localtime`, `zoneinfo`, a generated `resolv.conf` naming pasta's DNS address | DNS and CAs, nothing else |
| `/proc`, `/dev`, `/tmp`, `/run` | new proc, minimal dev, tmpfs, tmpfs | no other process, no sockets |
| `$HOME` | tmpfs | the floor does not exist |
| `$HOME/.gitconfig` | read-only, generated: the owner's name and email only | commits work |
| `$HOME/.config/opencode/opencode.json`, `plugins/`, `skills/` | read-only, generated: the provider entry without any inline key (refused if there is one), the model, lane-control's plugin, the lane's skills | the lane's settings |
| Orca's OpenCode overlay folder and `agent-hooks/endpoint.env` | read-only, those paths only | Orca's hooks and startup prompt (lc-9r81.1) |
| `/run/lane/persona.md` | read-only, the rendered persona | personas name this path for sandboxed lanes, not the main checkout (m1) |
| lane-control's pinned code tree; the inside wrappers also at `~/.agents/bin/<name>` | read-only | `lane-heavy` and lane tools by the paths personas use (m1) |
| `$HOME/.local/share/opencode/`, `$HOME/.cache` | read-write, this ticket's own folders | the agent's store and caches, never shared (m3) |
| the Orca worktree's path | read-write: the lane's clone (a reviewer: its own clone) | the work |
| `/run/lane/inbox` | read-only | base bundles, the files `prepare` names |
| `/run/lane/outbox` | read-write | push bundles |
| `/run/lane/broker.sock` | this ticket's broker socket | the only way out |

None of these is writable by remount: `--cap-drop ALL` (spike). PATH inside holds the tool list (lane-control's
project store, never the worktree, built outside), the inside wrappers `orca`, `orca-ide`, `gh`, `git`, `bd`,
`lane-heavy`, and the real `git`.

**Environment**: only `HOME`, `PATH`, `LANG`, `LC_ALL`, `TERM`, `COLORTERM`, `USER`, `LOGNAME`, `SHELL`, `TMPDIR`,
`TZ`, `TZDIR`, `LOCALE_ARCHIVE`, `SSL_CERT_FILE`, `NIX_SSL_CERT_FILE`, `LANE_ROLE`, `LANE_PERSONA`,
`OPENCODE_CONFIG_CONTENT`, `OPENCODE_CONFIG_DIR`, the gateway key variable the provider config names, and Orca's pane
variables `ORCA_AGENT_HOOK_*`, `ORCA_PANE_KEY`, `ORCA_TAB_ID`, `ORCA_WORKTREE_ID`, `ORCA_TERMINAL_HANDLE`,
`ORCA_AGENT_LAUNCH*`, `ORCA_OPENCODE_*` (each Orca token's reach is measured in lc-9r81.1; one that reaches more than
this pane's hooks is dropped). They reach bwrap as its environment from `env -i`, never as arguments; bwrap's own
pid 1 then holds only what the agent has anyway. MCP extras that need a secret are refused for sandboxed lanes.

## The broker

One per ticket, outside, with the environment the shim got from Orca minus the lane's env extras. One JSON request per
connection on `/run/lane/broker.sock`, at most 1 MB, typed fields only: no argv and no path from the lane; text goes
through files the broker creates (0600); option values as `--opt=value`; ids are matched against their pattern and
passed as `--id=<id>` where the CLI has it; unknown fields refused; every request logged.

| Request | Runs outside | Roles |
|---|---|---|
| `report {task, dispatch, subject, body, outcome, files}` | `orca-ide orchestration send --from=<bound terminal> --type=worker_done ...`, after `orchestration dispatch-show --task=<task> --json` shows that dispatch on the bound terminal | lane, reviewer |
| `escalate`, `ask {question}`, `check`, `reply {id, body}` | the same verbs bound to the terminal; `reply` only to ids `check` returned | lane, reviewer |
| `push` | "Push" above | lane |
| `fetch` | "Base in" above | lane, reviewer |
| `pr-create {title, body}` | `gh pr create --repo=<repo> --head=<lane branch> --base=<base> --title= --body-file=` | lane |
| `pr-view`, `pr-checks`, `pr-diff` | the lane branch's own PR, fixed `--json` fields | lane, reviewer |
| `pr-comment {body}`, `pr-edit {title?, body?}` | that PR only | lane |
| `run-view {run}` | `gh run view --repo=<repo> <run> --log-failed`, after `--json headBranch` shows the lane branch (m1) | lane, reviewer |
| `bd-show {id}` | `bd show --id=<id>`, only the lane's issue and issues it lists as parent, child or dependency; ids match `^<prefix>-[a-z0-9]+(\.[0-9]+)*$` (M4) | lane, reviewer |
| `bd-note {text}`, `bd-claim`, `bd-create {title, description, type, priority}` | on the lane's own issue; a new issue gets `discovered-from:<lane issue>` | lane |
| `review-read {name}` | a file in the owner's review folder that `prepare` named for this ticket (M4) | lane, reviewer |
| `review-write {name, text}` | create (not overwrite) the one name `prepare` gave a reviewer ticket | reviewer |
| `heavy-slot` | holds a `lane-heavy` slot (flock outside) while the connection is open | lane, reviewer, check |

Never: other Orca verbs, `--to`, `--environment`, `--on`, `--file`; gh `api`, `auth`, `secret`, `--web`, `--editor`,
`--attach`, `--template`, `--recover`, another PR or run; `bd` list, close, delete, remember, or any flag-shaped id.

## What breaks, and what is done about it

| Breaks | Handling |
|---|---|
| Orca's diff view for the lane is empty | the PR is the view |
| **Closing and keeping a sandboxed lane** (r2 M3): its Orca worktree is always at the base, so Orca's rule "Delete released orchestration worker workspaces" and the skill's fallback close checks (`status --porcelain --ignored`, `merge-base --is-ancestor HEAD origin/<base>`) always pass | for a sandboxed lane the coordinator never uses those checks; `lane-control sandbox close <lane>` checks the PR (merged, or the owner's word), runs `sandbox inspect` (clean, nothing unpushed), then removes the clone, quarantine and folders, then the worktree. If Orca removes the worktree earlier, nothing is lost: the work is in the clone and `refs/lanes/<lane>`, and `prepare` binds the clone to a new worktree of the lane. How Orca's rules treat it is measured in lc-9r81.1 |
| A clone per lane and reviewer | disk and time; question 6 |
| Orca's transcripts | measured in lc-9r81.1; the lane's store may be bound back as `opencode-lane-<id>.db` |
| `nix` inside | no daemon; tools built outside |
| `git push -u`, "up to date with origin" (m1) | the push wrapper updates `refs/remotes/origin/<branch>` and the upstream config in the clone after the broker accepts |
| A rebased branch | refused: lanes merge; pushes are fast-forward |
| Resume or relaunch | `prepare` again: reuses the clone, or recreates it from `refs/lanes/<lane>` |
| `lane-heavy`'s per-command memory cap | one cap for the lane (the scope); slots through the broker |
| A host with no user manager | no memory cap; `machine-check` says so |
| uid 0 inside | lc-9r81.2 |
| MCP extras with a secret | refused for sandboxed lanes |
| Failed CI logs | `run-view` |
| A TUI without a controlling terminal | only where `legacy_tiocsti` is not 0 |

## Availability

- Pinned at machine setup: `nix build --no-link --print-out-paths nixpkgs#bubblewrap nixpkgs#passt
  nixpkgs#util-linux.bin nixpkgs#nftables`, absolute paths in `machine.conf`, GC roots in lane-control's data folder.
  The spike used bubblewrap 0.12.0, passt 2025_09_19, util-linux 2.42.3, nftables 1.1.6.
- Needs unprivileged user namespaces. Orca on NixOS runs inside a buildFHSEnv bwrap; the spike's chain runs nested in it.
- A remote box: the same pins where nix exists, else the distro's packages, only when not setuid; Ubuntu 23.10+ needs
  its AppArmor profile for bwrap. `machine-check` probes the whole chain; a failure keeps the sandbox off on that box.
- `lane-control sandbox selftest` (the spike's probes, grown into the plan's adversarial suite, run through the real
  shim and broker) must pass before `machine setup --sandbox` turns it on and after any pin or lane-control update; a
  skip counts as a failure.

## The spike

`spike.sh` runs the chain above with the stub as the agent, and the broker's push and fetch as the commands above
(`spike_helpers.py` does the outbox read, history and CI checks, host-pid lookup and nft rules). Result (2026-11-27,
the pins above, nested in Orca's FHS env): **PASS**.
- `control` (no sandbox): 24 leaks; host `git status` runs the planted program.
- `caps` (the chain without `--cap-drop ALL`): a read-only bind remounted, its host file written (r2 B1).
- `naive` (round 2's start, owner's environment, `--clearenv --setenv`): the token in `/proc/1/environ`, the key in 3
  host processes' argv (r2 M2).
- `sandbox`: no floor file, link or search hit; no token in any visible process; CapEff 0 (uid 0); every read-only
  bind refuses remount and write; no nested user namespace; `nft flush` refused; nix daemon, loopback TCP and UDP, an
  abstract socket and every host address unreachable, the forwarded port reachable; the key in no host argv; a port
  opened inside unreachable from every host address; the shared repo absent, another lane's commit and the owner's
  unpushed commit not in the clone; commit, base merge (a change to `orca.yaml`) and push by bundle work; the scope
  ran; the host pid was found by mount namespace.
- Outside: round 2's import takes the link-to-repo and the id-only entries (r2 B2 reproduced); the design's broker
  refuses a link to a repo, a link to a file, the id-only bundle, a FIFO, an 80 MB file, two refs, an orphan and a CI
  workflow change, and accepts the honest push; origin has the lane's work and not the owner's commit; host git in the
  Orca worktree, the main checkout and a fresh checkout runs nothing.
- Resume: a second `prepare` reuses the clone (head is the pushed work); an unpushed commit and a dirty file are
  reported; a removed clone is recreated from `refs/lanes/<lane>`.
- History: round 2's check misses a private file added and removed on a merged side branch; the design refuses it, a
  tracked private file, a nested `sub/.env.local`, and a private file committed to the base after `prepare`; a tracked
  `.env.example` is accepted.

Not in the spike, in the plan or children: Orca's launch through the shim (lc-9r81.1), OpenCode itself as uid 0
(lc-9r81.2), CI (lc-9r81.3), the broker's socket and Orca, gh and bd requests (plan task 5), a pty (plan task 7).

## Claude lanes (phase 3, lc-d2wk)

The same chain, clone and broker for `claude`, plus auth by `CLAUDE_CODE_OAUTH_TOKEN` or phase 2's key proxy (never
the credentials file), a generated `CLAUDE_CONFIG_DIR` per ticket, Orca's Claude plugin and hook scripts read-only.
Claude refuses some modes as root, so uid 0 inside (lc-9r81.2) matters more there.

## What this closes, and what stays

SBX-2's adversarial suite passes on the implementation (2026-11-28, `tests/test_sandbox_escapes.py` under real pasta
and bwrap); the "Open, designed" rows below stay so until the owner's live lane (plan task 9) also passes, and then
read "Closed for sandboxed OpenCode lanes". The probe column says where each fix is shown.

| Issue | Status for sandboxed OpenCode lanes | Probe |
|---|---|---|
| lc-mpb5 | Open, designed: no floor path is mounted, private and `.env` data in history refuse the lane, no read-only bind can be remounted, the broker takes no path. The agent's own key and Orca's hook token stay readable inside | spike `sandbox`, `refuse` |
| lc-yw1g.1 | Open, designed: no shared config mounted; the lane's own config is advisory; no unsandboxed agent loads the clone before review | spike `sandbox` |
| lc-yw1g.2 | Stays (the coordinator's folder) | — |
| lc-yw1g.3 | Stays until phase 3; before merge a lane's settings reach no unsandboxed Claude | — |
| lc-yw1g.4, .5, .7, .9 | Stay | — |
| lc-yw1g.6 | Partly: code-running names run only inside; the broker drops the lane's extras. Stays for unsandboxed sessions | — |
| lc-yw1g.8 | Partly: no gh token or Orca runtime token inside, no secret in host argv; `env -0`/`printenv` still print the gateway key and the hook token inside | spike `naive`, `sandbox` |
| lc-yw1g.10 | Open, designed: the forms are normalized before the history check | `spike_helpers.py history-hits` |
| lc-8h1.1 | Stays (project-setup) | — |
| lc-8h1.2 | Stays for project-setup; for lanes, no host git in a lane-made repo | spike `sandbox` |
| lc-8h1.3 | Stays (lc-pk2c) | — |

## What stays open

- The coordinator and the owner's own sessions are unsandboxed.
- The internet: exfiltration of the clone and the agent's key and hook token (phase 2).
- CI runs lane code before review with whatever the repository grants (question 7).
- A start that does not go through Orca's custom command (not set, or set to something else) is detected (the PATH
  detector refuses it, `lane started` stops the worker), not prevented.
- A renamed or re-encoded copy of private data in history.
- Code runs like any merged code after the owner merges a reviewed PR.
- A kernel, bwrap, pasta, nft or git bug.

## Open questions for the owner: answered 2026-11-28 (lc-9r81.4)

The owner (2026-11-28, "recs approved") decided 1, 2 and 7; every other question takes the recommended answer, as the
owner said.

1. Launch: **Orca's custom command, set once by the owner** (prevents an unsandboxed start where it is set); the PATH
   shim is kept only as the detector ("The launch"). lane-control prints what to set and checks it, read-only.
2. Reviewers and check reruns: **always sandboxed**; no Claude reviewer reads a sandboxed lane's work unsandboxed.
3. History: **refuse** a lane whose delivered history holds a `--private` path or a `.env` file (recommended); the
   template list stays `.env.example`, `.env.sample`, `.env.template`, `.env.dist`.
4. Reads: **yes**, the lane's own issue and the issues it links, and only the review files `prepare` names
   (recommended).
5. Network: **nothing else on the LAN**: private ranges and link-local dropped, only the gateway allowed; the
   internet stays open until phase 2 (recommended).
6. A clone per lane and per reviewer: **fine for every project**, no size limit in phase 1 (recommended; the design).
7. CI: **require a read-only default token, no secrets and no self-hosted runners**; check and report it, refuse
   `--sandbox` for a project that fails it. No per-project acceptance and no fork in phase 1.

## As built (SBX-2), where it differs from or adds to the text above

- The read-only check is `lane-control machine setup --check --sandbox`: `machine check` names the machine check
  automation (review m1 of the front door), so no form takes that name.
- The pinned store paths go to `~/.config/lane-control/sandbox.conf` (written by `machine setup --sandbox`, mode
  0600), not into `machine.conf`, which machine setup never changes; the owner's keys stay in `machine.conf`
  (`SANDBOX_PROJECTS`, `SANDBOX_LANE_MEMORY`, `SANDBOX_AGENT_DIRS`, `SANDBOX_REAL_OPENCODE[2]`, `SANDBOX_REVIEW_DIR`).
- The fresh clone fetches `origin/<base>` from the shared repo (`git init` and a fetch), never the owner's local base
  branch, which may hold unpushed commits; the recreated, reviewer and check clones are made `--no-checkout` and
  checked out inside by `lane-enter`, the chain's first step, so no host git ever writes a lane-written tree.
- The inside wrappers are bound read-only at `~/.agents/lane-inside` with `~/.agents/bin` a link to their `bin`, so
  the links resolve inside where personas name them; the lane's PATH starts with them.
- The agent binary must resolve under `/nix/store` or a folder in `SANDBOX_AGENT_DIRS` (bound read-only); an npm
  install outside the store is named by `machine setup --check --sandbox` with the line to add.
- The broker exits with the launcher that started it; the socket is bound by its relative name (a long `$HOME` would
  pass the 108-byte socket path limit).
- `lane-control sandbox inspect|close|check` run the spec's sandboxed step through the same launcher, with the clone
  bound at its own path; `close` removes the worktree's ticket and binding files too and leaves the Orca worktree to
  the coordinator.
- The adversarial suite runs every probe of the spike and of reviews r1 and r2 through the real launcher, broker and
  wrappers under real pasta and bwrap (`lane-control sandbox selftest`; a skip fails it). CI's runner has no nix
  store, so CI skips it and says so (`python3 tests/test_sandbox_escapes.py --availability`).

## Review round 2, answered

| Finding | Answer |
|---|---|
| B1 capabilities under pasta | fixed: `--cap-drop ALL` in the chain and mount plan; spike `caps` reproduces, `sandbox` refuses remount, write, umount, nested userns and `nft flush`; uid 0 inside filed (lc-9r81.2) |
| B2 broker follows the outbox | fixed: no-follow regular-file copy, quarantine with only base and earlier pushes, one ref, descent from the recorded base, fast-forward ("Push"); spike: entries a-g refused, round 2's import shown taking a and c |
| B3 launch cannot start | fixed: info and block fds opened by path inside pasta, host pid by mount namespace and `NSpid`, systemd-run given its bus variables and bwrap unsetting them ("Chain"); spike runs it; pasta attaching to bwrap failed here (A2', lc-9r81.2) |
| B4 history check | fixed: every changed path of every delivered commit against each parent, any depth, case-insensitive, on clone, base bundles, reviewer and check clones; renamed copies not detected (stated); spike `refuse` |
| B5 CI runs lane code | fixed for workflow changes (broker refuses without the owner's approval; spike entry h); stated plainly what CI runs ("Who runs lane-written code"); the rest filed (lc-9r81.3, question 7) |
| B6 relaunch loses work | fixed: resume reuses the clone, recreate from `refs/lanes/<lane>`, end only on a clean inspect or `--discard`; spike resume |
| M1 pasta defaults, host addresses | fixed: `-t none -u none -U none`, the nft filter; spike checks UDP, inbound, every host address |
| M2 key in host argv | fixed: allowlist as environment from `env -i`, no `--setenv`; spike `naive` reproduces, `sandbox` shows 0 |
| M3 close and keep rules | fixed: `sandbox close`, never the worktree checks; work survives a removed worktree; Orca's rule measured in lc-9r81.1 |
| M4 broker reads | fixed: `bd-show` limited to the lane's issue and links, `--id=`, id pattern, `bd-list-ready` removed; `review-read` limited to named files; question 4 |
| M5 spike and tests miss escapes | fixed: the spike runs the chain and every attack above with controls; the plan's suite runs through the real shim and broker (plan task 7) |
| M6 claim table | fixed: "Open, designed" with the probe, until SBX-2's suite passes |
| m1 normal work | fixed: persona and wrapper paths, `run-view`, tracking refs ("Mount plan", "What breaks") |
| m2 `.env.example` | fixed: template list, spike accepts `.env.example`; question 3 |
| m3 reviewer folders, check role | fixed: cache, data and run folders per ticket; `check` gets `heavy-slot` in spec and plan |
| m4 questions | fixed: questions 4, 5, 7 added; resume settled in the design |

## Review round 1, answered

| Finding | Where |
|---|---|
| B1 broker argv and paths | typed requests ("The broker") |
| B2 nested repo, host git | own clone, no host git in it (spike) |
| B3 beads hooks | folder not mounted; typed bd requests |
| B4 shared objects | shared repo not mounted |
| B5 tracked private, other refs | single-branch clone, full-history check (spike) |
| B6 rename past a mount | moot: nothing is masked by path |
| B7 unsandboxed agents run lane code | "Who runs lane-written code" |
| B8 dispatch identity | tickets bound to the terminal, dispatch checked per request |
| M1-M8, m1, m2, n1 | see round 2's rows and the sections above |
