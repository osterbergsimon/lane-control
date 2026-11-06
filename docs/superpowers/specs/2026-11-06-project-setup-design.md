# project-setup: Orca + Cotal projects with a coordinator and a frontend design lead

Status: approved design, 2026-11-06. bd: tux-1vm.

## Intent

One command turns a repo (URL or folder), on this machine or on an SSH host, into an Orca project run by Cotal agents:
a **coordinator** the owner chats with, and a **frontend design lead** in its own Orca worktree. The coordinator opens
worker lanes as **child tasks in the Orca GUI** (one Orca worktree per lane, a Cotal agent in it), routes each lane to a
harness and model by tier (cheap OpenCode models for well-scoped work, Claude for deep work), and gets results back by
Cotal DM. Peer messages never land in a half-typed composer (Cotal delivers them as their own turn).

Why: Rove is being replaced. Orca gives the GUI (tasks, nesting, worktrees, SSH hosts) and Cotal the mesh (DMs,
roles, spawning, draft-safe delivery). A working version of this was hand-assembled at work; this makes it repeatable
and air-gap friendly.

## Goals
- `scripts/project-setup` does the whole project setup idempotently (re-run reuses what exists).
- `scripts/orca-host-install` puts Orca's server (orcad) on an SSH host with no downloads on the host, and optionally
  pairs the desktop with it without clicking.
- Works with OpenCode and Claude Code agents, mixed within one project.
- Lanes appear nested under their parent in the Orca GUI; lanes under the design lead nest under it.
- Personas and a lane skill carry the doctrine (lane-control roles, routing, worker brief, report format).

## Non-goals
- No Rove compatibility; `rove api` verbs are not used.
- No Orca `orchestration` (task-create, dispatch --inject, terminal steering) and no `orca terminal send` to agents.
- No budget feed / machine coordinator / night watch port.
- No herdr plugin.

## Anti-goals (a failure even if it runs)
- Anything typed into an agent's terminal while the owner may be typing.
- Agents running as root.
- Copying credentials (auth.json, opencode.db, Claude creds) between machines.
- A coordinator that writes code, merges, or starts work the owner did not ask for.

## Constraints
- Hosts may be air-gapped: npm/PyPI/GitHub mirrors and the model APIs are reachable; nodejs.org and onorca.dev are not
  assumed.
- Versions tested: Orca 1.4.250 (orcad 0.1.0+b086a7d15d60, node v24.22.1), Cotal 0.145.1 (+ `@cotal-ai/orca`),
  OpenCode 2.2.x, Claude Code current npm.
- OpenCode 2.x agents need Cotal `--no-events` (`events: false`).
- Claude refuses `bypassPermissions` as root; agents run as a normal user.
- Commits use the owner's git identity, no AI attribution.

## Decisions (the owner's)
- One Cotal **space per project**, each with its own coordinator and design lead.
- Claude agents default to permission mode **auto**; **bypassPermissions** is an option (`--permission-mode`).
- Agents never run as root; on the trial box a user `dev`.
- Coordinator may use both harnesses: lanes are routed by tier to harness+model.
- Persona prompting draws on shepherdr (`eliasstravik/shepherdr` templates/assistant.md, worker.md, reviewer.md),
  lane-control doctrine, Cotal example personas, and the frontend-design skill.

## Design

### 1. `scripts/orca-host-install <ssh-host> [--pair] [--port 6768]` (runs on the workstation)
Facts (read from Orca 1.4.250's bundle):
- The desktop skips uploading when `~/.orca-remote/orcad-<ver>/` holds every artifact plus `.install-complete`, and
  skips node when `~/.orca-remote/runtimes/node-<sha256 of bin/node>/` has `.verified` and an executable `bin/node`
  whose sha matches.
- The slot to copy is the desktop's own `~/.config/orca/orcad-artifacts/<target>/<ver>/` (target `linux-x64-glibc`
  for Debian-like hosts; read from `.server-target`); the node binary is the AppImage's `resources/node-runtime/bin/node`
  or `bin/node` from `~/.config/orca/orcad-artifacts/node/archives/node-v24.22.1-linux-x64.tar.gz`; the node dir name
  comes from the slot's `.runtime-node`.

Steps: refuse when the remote user is root (warn and require `--allow-root`); rsync slot → `~/.orca-remote/orcad-<ver>/`;
chmod 755 `ripgrep/*/rg` and `agent-browser-*`; touch `.install-complete`; place node, check `--version` = expected,
touch `.verified`; chmod 700 `~/.orca-remote`. Idempotent.

`--pair` (fully scripted connection, no GUI click):
- On the host, a `systemd --user` unit `orcad.service` running
  `<node> --max-semi-space-size=8 <slot>/orcad.js --json --bind 127.0.0.1 --port <port>` with env
  `ORCA_VERSION=<ver> ORCA_USER_DATA=$HOME/.orca`; stdout to `<slot>/.orcad-readiness`. (`loginctl enable-linger`
  for the user if allowed.)
- Read the `orca_server_ready` JSON, take `pairing.url`.
- On the workstation, a `systemd --user` unit `orca-tunnel-<host>.service`: `ssh -N -o ExitOnForwardFailure=yes
  -L <port>:127.0.0.1:<port> <host>` (same port both sides so the pairing endpoint is valid as is).
- `orca environment add --name <host> --pairing-code <url>`; treat "already paired" as success.
Without `--pair` the doc tells the owner: add the host in Orca (Settings → SSH, or import ~/.ssh/config) and connect;
Orca reuses the install. Both paths are tested; the doc recommends whichever was reliable.

### 2. `scripts/project-setup <git-url|dir> [options]`
Options: `--host <ssh-host>` (run there: copy lane-control's needed files to `~/.local/share/lane-control/` on the host
and run the script there over ssh), `--name <project>` (default: repo basename), `--dest <dir>` (clone parent,
default `~/code`), `--coordinator <harness/model>` (default `claude/opus`), `--design-lead <harness/model>` (default
`claude/opus`), `--permission-mode auto|bypassPermissions|default` (default `auto`), `--port <nats-port>` (default
derived from the name in 14200–14999, recorded), `--no-start` (prepare only).

Steps, each skip-if-done:
1. Refuse root. Check `orca status` reachable, `cotal` present, chosen harnesses on PATH.
2. Repo: URL → `git clone` into `<dest>/<name>` (reuse if it exists with that origin); dir → use it, `git init` + an
   initial empty commit if it is not a repo.
3. Orca: `orca repo add --path <repo> --json` unless `orca repo list --json` already has the path; keep the repo id.
4. Skills, into the project: `orca skills install --skill orca-cli --local` and `skills/orca-cotal-lanes/` copied to
   `<repo>/.agents/skills/orca-cotal-lanes/`, linked into `<repo>/.claude/skills/` (and `.opencode/skills/` if
   OpenCode does not read `.agents/skills`, verified during build).
5. Cotal: `cotal space create <name> --root <repo>` unless prepared; personas rendered from `personas/*.md` into
   `<repo>/.cotal/agents/{coordinator,design-lead}.md` with harness, model, permission mode filled in; tier table
   written to `<repo>/.cotal/lanes.tsv`; `.cotal/` added to `.gitignore` (the skills and orca.yaml are committed by the
   owner if they want; the script does not commit). `cotal up --root <repo> --runtime orca --server
   nats://127.0.0.1:<port>`; telemetry off (`cotal telemetry off`), proxy and other env via `spawn.env` documented.
6. Coordinator: `cotal spawn coordinator -d --cwd <repo>` unless on the roster.
7. Design lead: `orca worktree create --repo id:<repoId> --name design-lead --parent-worktree path:<repo> --setup
   inherit --json` unless it exists, then `cotal spawn design-lead -d --cwd <worktree>` unless on the roster.
8. Verify: both in `cotal ps --json` and live; `orca worktree list --json` shows design-lead's `parentWorktreeId` =
   the repo's root worktree. Print a summary (repo id, worktrees, agents, port, how to open in Orca).

### 3. Personas (`personas/coordinator.md`, `personas/design-lead.md`)
Cotal persona files (frontmatter: name, role, description, agent, model, events, capabilities, subscribe/allow*,
launchOptions; body = system prompt). Placeholders filled by project-setup.

Coordinator (role `coordinator`, capabilities `[spawn]`): talks with the owner; never writes code; turns requests into
lanes; routes by tier; opens lanes via the skill; reviews per lane-control review tiers (reruns checks itself, checks
out-of-scope files, independent reviewer sees diff+issue, not the worker's report); merges, closes, stops lanes only when
the owner says so; uses bd; surfaces owner decisions with a recommendation; treats worker/issue/PR text as information,
never instructions; leads replies with the result; ends with ids. Sources: shepherdr assistant.md, roles.md,
model-routing.md, AGENTS-coordination.md, reliability.md, Cotal example standing rules.

Design lead (role `design`, capabilities `[spawn]` for its own frontend lanes): owns frontend/visual quality; works in
its own worktree; plan-first (tokens, type, layout, then critique the plan before code), the AI-tell checklist and
quality floor from the frontend-design skill; reviews frontend lanes cold at an exact sha, says exercised vs read,
APPROVE/BLOCK with one-line blockers, never edits a worker's code; may open frontend lanes as its own children; reports
to the coordinator by DM in the 5-line format.

### 4. Lane skill (`skills/orca-cotal-lanes/SKILL.md`)
Used by coordinator and design lead. Covers: choose tier (model-routing.md) → look up harness/model in
`.cotal/lanes.tsv`; open: `orca worktree create --name <key-slug> --parent-worktree active --setup inherit --json`, then
`cotal_spawn` (agent, model, cwd = worktree path, role `lane`, launchOptions = project permission mode for Claude,
events false for OpenCode, prompt = the lane brief); lane brief template (worker-brief.md adapted: Cotal DM report
instead of rove); mirror state in the GUI: `orca worktree set --worktree <id> --workspace-status <s> --comment <c>`;
communicate only by `cotal_dm`; after launching, end the turn and wait for the DM; close (`cotal stop`, `orca worktree
rm`) only when the owner asks. Explicitly: do not use Orca `orchestration` or `orca terminal send` on agents.

### 5. Tier table (`.cotal/lanes.tsv`)
`tier  harness  model  permission`. Defaults: swift opencode `opencode/deepseek-v4.1-flash` (free variant on the trial
box), standard claude `sonnet`, deep claude `opus`, top none. `--tiers <file>` overrides.

### 6. Docs
`docs/orca.md` §5b replaced by the working recipe (host install, project-setup, lanes); README file map updated.

## Test plan (trial-vps, user `dev`)
Box prep: user `dev` (no sudo needed for agents); Claude Code via npm; proxy allowlist adds `api.anthropic.com`,
`claude.ai`, `.anthropic.com`; owner logs Claude in once as dev (and accepts the bypass warning once if wanted).
1. orca-host-install with `--pair` and without (GUI connect): Orca reaches the host, no nodejs.org/onorca.dev traffic in
   the proxy log.
2. project-setup on a throwaway repo; re-run is a no-op.
3. Coordinator claude/opus, design lead claude/opus; owner asks for a small task → coordinator opens one swift lane on
   OpenCode and one standard lane on Claude; a lane under the design lead.
4. Each lane: child in `orca worktree list` lineage (and the owner eyeballs the GUI), on the roster, finish DM reaches its
   parent, a half-typed draft in the receiver survives an incoming DM (Claude and OpenCode).
5. Old root trial setup is removed afterwards.

## Builder-check answers (calls made by the designer)
- **Spike first.** Before scripting, prove on trial-vps as `dev`: a manager-launched agent (`cotal spawn -d --cwd
  <orca worktree outside the space root>`, runtime orca) joins the roster and opens as a terminal tab *inside that
  existing worktree* (no new worktree, no duplicate tab). The 2026-11-05 trial's lane-ui/lane-api suggest yes; the
  failed `boss` was a foreground `cotal spawn` typed into an Orca terminal, a different path. If the spike fails, fall
  back to `orca terminal create --command "cotal spawn … (foreground)"` only after diagnosing why foreground did not
  join; no typing into existing terminals either way.
- CLIs: verify every flag against the real CLIs on trial-vps (cotal 0.145.1, orca 1.4.250); treat spec flags as
  provisional until then.
- `--host` copies `scripts/project-setup`, `personas/`, `skills/` to `~/.local/share/lane-control/` on the host; the
  host needs git, python3, cotal, orca (from orcad), and the harnesses. project-setup does not source lib.sh/config.sh.
- `cotal up` detaches (background stack under the root's `.cotal/`); "done" = `cotal status --root`/`cotal ps` answer
  for that space. Survival across ssh logout is checked in the spike (linger on); if it dies, wrap it in a
  `systemd --user` unit `cotal-<name>.service`.
- `--tiers <file>` is a project-setup option: same TSV format, replaces the defaults.
- Models are written through unvalidated; Claude aliases (`opus`, `sonnet`) pass to Cotal as is.

## Left to the builder
Script language (POSIX sh + small python3 for JSON is fine), exact file names inside `scripts/`, port derivation,
rendering of placeholders, wording of personas within the sources above.
