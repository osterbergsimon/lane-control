# Orca (+ optional Cotal) as an alternative to Rove

Status: trial, not a port. Run on 2026-11-05 with Orca 1.4.250 (desktop on a NixOS workstation), Cotal 0.145.1,
OpenCode 2.2.1, against a Debian box reached only over SSH, whose internet was limited by an allowlisting proxy.
Tags: **[tested]** ran it, **[docs]** read in the project's docs but not run, **[none]** no equivalent.

lane-control's scripts call `rove api` and do **not** run unchanged on either. What carries over is the doctrine
(roles, tiers, path map, beads, review by risk) and the shape: one coordinator per project launching worker lanes.

## 1. Which layer does what
| Need | Orca alone | Orca + Cotal |
|---|---|---|
| Agents on a VPS, watched and typed into from the workstation, over plain SSH | yes: SSH worktrees, managed server on the host, agents survive disconnects [tested] | same |
| OpenCode 2.x in a real terminal (its own TUI) | yes ("OpenCode 2 beta") [tested] | yes, with `--no-events` [tested] |
| Coordinator starts lanes and gets their results | Orca orchestration: experimental, its panel is disabled in 1.4.249+ [docs] | `cotal_spawn` + DMs from inside the agents [tested] |
| Messages to an agent never land in a half-typed prompt | not documented: worker delivery is `dispatch --inject` into the terminal (the docs' example waits for `tui-idle` first) [docs] | yes: Cotal's OpenCode plugin injects a message as its own turn through OpenCode's server (section 6) |
| Lanes nested under the coordinator in the sidebar | worktrees nest under a parent ("N children" chip) [docs] | yes, with a worktree per lane: the coordinator runs `orca worktree create --parent-worktree active`, then `cotal_spawn` into it (section 5b) [tested] |
| A worktree (branch) per task | yes, Orca's core unit [docs] | yes, Orca creates it; Cotal only runs the agent in it (section 5b) [tested] |
| Chat UI instead of a TUI | "structured native chat", but **not over SSH** ("SSH sessions continue to use terminal-backed Chat UI") [docs] | n/a |

Rule of thumb: **Orca is the cockpit** (SSH, terminals, editor, git per worktree). **Cotal is the radio** (who
started whom, messages that wait for the agent's turn, roles). Use Orca alone if you drive lanes by hand; add Cotal
when a coordinator should run lanes and talk to them while you type.

## 2. Install Orca on the workstation
- Linux builds: AppImage, x86_64/aarch64 RPM (Fedora, Rocky), AUR `stably-orca-bin`. On NixOS, package the AppImage
  with `appimageTools.wrapType2` (an example overlay: the owner's dotfiles `overlays/orca-ide`): the app as `orca-app`
  with a menu entry, the CLI as `orca-ide` (Orca's own Linux name for it; plain `orca` there is often GNOME's screen
  reader). Verify downloads against the sha512 in the release's `latest-linux.yml`. [tested]
- **Telemetry is on by default** (anonymous, PostHog Cloud US). Start it with `ORCA_TELEMETRY_DISABLED=1` (or
  `DO_NOT_TRACK=1`) or turn off Settings, Privacy, "Share anonymous usage data". No account is needed. [docs]
  ```bash
  ORCA_TELEMETRY_DISABLED=1 setsid appimage-run ~/Applications/orca-linux-<ver>.AppImage >/dev/null 2>&1 &
  ```
- Releases are near daily (seven in the week of the trial): pin and note the version you evaluate.

## 3. Connect the VPS
1. Add the host in Orca (SSH alias from `~/.ssh/config`, or host, user and key). [tested]
2. Add the repo with **Run on: <host>**. On first connect Orca installs its own pinned server under
   `~/.orca-remote/` and `~/.orca/` on the host; terminals then live there and survive closing the desktop. [tested]
3. Host prerequisites: git, the agent CLIs, and Orca's server. By default Orca installs that on first connect and
   downloads its Node runtime from `nodejs.org` (or a mirror). On an air-gapped host run `scripts/orca-host-install
   <host>` first (section 5b): it copies the server and Node from your Orca install, so the host needs neither
   `nodejs.org` nor `onorca.dev`. [tested]
4. Model credentials: set the provider key in Orca's OpenCode account profile (worked in the trial), or log in on the
   host with `opencode auth login` in an Orca terminal. **Do not copy `auth.json` from the workstation** (section 7). [tested]
5. The `orca` CLI is installed on the host at `~/.orca/cli/bin/orca` and reaches the host's runtime as is:
   `orca status --json` returned `ok` with no extra setting. The docs also describe "Allow this host's orca CLI to
   control Orca" and Settings, Experimental, CLI; neither was needed or found in 1.4.250. [tested]

## 4. Orca alone: coordinator and lanes
- One worktree per lane is Orca's native unit: `orca worktree create --repo id:<repoId> --name <lane> --json`, nested
  under the coordinator's worktree via "Parent workspace" (Advanced drawer). This also gives one branch per lane, as
  `templates/AGENTS-coordination.md` expects. [docs]
- Drive agents from a script or the coordinator: `orca terminal create --worktree path:<dir> --command "<agent>"`,
  `orca terminal read`, `orca terminal wait --for tui-idle`, `orca terminal send --text … --enter`. [docs]
- `orca terminal send` **types** into the terminal: check `terminal read`/`wait --for tui-idle` before sending, and
  expect it to collide with whatever a human is typing in that terminal. [docs]
- Orca orchestration (runs, tasks, dispatch, coordinator inbox, gates): enable under Settings, Experimental; the panel
  is "temporarily disabled again" as of 1.4.249. Re-check before relying on it. [docs]

## 5. Orca + Cotal on the host
All on the VPS, in an Orca terminal or over SSH. Node 22+.
```bash
npm i -g cotal-ai @opencode/cli          # OpenCode 2.x is fine here (unlike wsp)
chown -R root:root /usr/local            # if node came from a tarball: Cotal refuses a writable bundled nats-server
cotal telemetry off                      # also on by default; or COTAL_TELEMETRY_DISABLED=1
# proxied host: agents get a filtered environment, so forward the proxy explicitly
echo '{ "spawn": { "env": ["HTTP_PROXY","HTTPS_PROXY","NO_PROXY","http_proxy","https_proxy","no_proxy"] } }' \
  > ~/.config/cotal/config.json
cd ~/code/<space-root>                   # one Cotal space; projects are folders under it
cotal setup --destination local --harness opencode --project . --space <space> --no-input
cotal ext add @cotal-ai/orca
export PATH="$HOME/.orca/cli/bin:$PATH"  # the manager calls `orca`
cotal up --detach --runtime orca         # broker, delivery daemon, manager (bundled nats-server, loopback)
cotal spawn -d -n <project>-coord --harness opencode --no-events -m <provider/model> -C ~/code/<project>
```
Notes [tested]:
- The folder must be inside a worktree Orca already has open (Cotal resolves it with `orca worktree current`); the
  agent appears as a tab `cotal-<name>` in that worktree. Watch and type there; `cotal attach` is not used.
- OpenCode 2.x needs `--no-events`: status (idle/working/waiting), DMs, roles and spawning work; the live activity
  feed (`cotal watch`, dashboard Activity) does not. With OpenCode 1.x the feed works too.
- Give the coordinator lane-control's doctrine as its first message (`cotal send -t <project>-coord "…"`), and tell it
  to start lanes with `cotal_spawn` (harness, model per tier, `--no-events` for 2.x), have lanes DM it when done, and
  report to you by DM.
- Talk to agents: type in their Orca tab, `cotal send -t <name> "…"` (one-off; replies can't reach a CLI sender), or
  `cotal chat` (a live session with its own identity, so replies arrive). Roster: `cotal ps`, `cotal who`.
- Read-only web dashboard: `cotal auth mint dashboard --profile admin --out <file>` then
  `cotal service add web --preset web --creds <file> --read-only` and an SSH tunnel to port 7799. It cannot post
  messages in 0.145.1 (read-only is forced).

## 5b. project-setup: Orca tasks, Cotal agents (recommended)
Verified end to end on 2026-11-06 (trial box, user `dev`, Orca 1.4.250, Cotal 0.145.1, OpenCode 2.2.1, Claude Code
2.1.319). Each project gets:
- its own Cotal space;
- a **coordinator** in the main checkout;
- a **frontend design lead** in its own Orca worktree, nested under the main checkout.

Lanes are Orca child tasks with one Cotal agent each, routed by tier to OpenCode or Claude. Messages never type into a
composer. [tested]

### Host: once per machine, as a normal user (not root)
- Agents never run as root: Claude refuses `bypassPermissions` as root, and nothing needs it.
- Install `cotal-ai` and the harnesses (`@opencode/cli`, `@anthropic-ai/claude-code`) from npm. Then, as the agent
  user:
  ```bash
  cotal ext add @cotal-ai/orca
  echo '{ "spawn": { "env": ["HTTP_PROXY","HTTPS_PROXY","NO_PROXY","http_proxy","https_proxy","no_proxy","TMPDIR"] } }' \
    > ~/.config/cotal/config.json                    # names to pass through, not values
  claude                                             # sign in once; for bypass mode also run
  claude --permission-mode bypassPermissions          # once, and accept the warning
  ```
- From the workstation, put Orca's server on the host without the host downloading anything:
  ```bash
  scripts/orca-host-install <user@host> --pair        # copies orcad + node from your Orca install, starts orcad,
                                                      # tunnels 127.0.0.1:6768 and pairs (environment <user-host>)
  ```
  - Without `--pair`, add the host in Orca and connect: Orca finds the install and reuses it.
  - Either way the host needs no `nodejs.org` or `onorca.dev`.
  - `--pair` keeps the tunnel up as a systemd user unit `orca-tunnel-<host>` on the workstation.
  - If orcad's port is taken (another user's Orca), the script refuses; pick another with `--port`.
  - After the desktop updates Orca, a re-run installs the new server and replaces the old running one.
  - `--pair --no-tunnel` starts orcad and prints its pairing code, without a tunnel or a saved environment.
  - A trial or throwaway host stays out of `~/.ssh/config`: set `LANE_SSH_CONFIG=<its ssh config>` and both scripts
    (and the tunnel unit) use `ssh -F` with it.

### Project: one command
```bash
scripts/project-setup <git-url | /abs/dir> [--host user@host] [--name Shop] \
  [--coordinator claude/opus] [--design-lead claude/opus] [--permission-mode auto|bypassPermissions|default] \
  [--reviewer claude/opus] [--tiers file.tsv] [--port N] [--write-orca-yaml] [--no-design-lead] [--trust-folder] [--no-start]
```
It does each step only if it is not already done, so a re-run prints `exists` for every step:
1. Clones the repo, or uses the folder (`git init` only when it is not a repo). It never commits.
2. Runs `orca repo add`.
3. Installs the skills for the agent user (`~/.agents/skills/`, linked into `~/.claude/skills/` and
   `~/.config/opencode/skills/`): Orca's `orca-cli` and lane-control's `orca-cotal-lanes`. They are user-level
   because a lane worktree carries only committed files.
4. Creates the Cotal space (`cotal space create`; plus `cotal setup -H claude` for the Claude plugin and wake channel).
5. Renders the personas from `personas/` (coordinator, design lead, reviewer) and the tier table
   `templates/lanes.tsv` into `.cotal/`, and gitignores `.cotal/`. Each persona names the tier table and the reviewer
   persona by absolute path, and the project's Claude permission mode.
5a. Setup script: Orca runs `orca.yaml` `scripts.setup` in each new worktree (a "Setup" tab), but reads it from the
   worktree, so only a committed one counts. Without one, project-setup drafts it from the lockfiles (uv, poetry,
   npm/pnpm/yarn/bun, cargo, go, in the root and two levels down; the beads link when `.beads` is a symlink) into
   `.cotal/orca.yaml.proposed`, or into `orca.yaml` with `--write-orca-yaml`, for the owner to review and commit. It
   never commits. The draft ends by writing `orca-setup-done` in the worktree's git dir; agents start only after it.
6. Starts the broker (`cotal up --no-manager`, on a per-project port recorded in `.cotal/lane-control.json`) and a
   manager service `mgr` on the Orca runtime.
   `mgr` carries `envRefs` for the proxy variables, because `cotal up` gives its manager a fixed environment without
   them.
7. Starts the coordinator in the main checkout. A Claude agent first needs the owner's answer to Claude Code's
   "Do you trust this folder?" prompt for the main checkout (lane worktrees share it), which an agent tab can't give.
   In a terminal, project-setup offers to open Claude there for you to answer, then goes on (also with `--host`: the
   ssh session gets a terminal). Without a terminal it stops and says to run `cd <repo> && claude` once.
   `--trust-folder` answers yes for you instead, without asking, for unattended runs. Claude Code has no switch for
   this, so it writes `hasTrustDialogAccepted` for the folder into `~/.claude.json` the way Claude does: atomically,
   keeping everything else, refusing a file it can't parse, and checking afterwards that a running Claude session
   didn't overwrite it.
8. Creates the Orca worktree `design-lead` as a child of the main checkout, waits for the setup script, starts the design lead there, and only then closes
   Orca's startup shell and the Setup tab (kept if the script never finishes; an empty worktree may refuse the spawn),
   then opens a shell after the agent's tab. With
   `--no-design-lead` (a project without a frontend) there is no design lead: the coordinator routes any UI work
   itself and its persona says so.
9. Verifies that the agents are live and that the design lead's task is nested.

With `--host`, it copies itself to `~/.local/share/lane-control/` on the host and runs there; paths are host paths.

### Lanes
The coordinator (or the design lead, for its own frontend lanes) follows the `orca-cotal-lanes` skill:
1. Pick the tier per `model-routing.md`, then look up the harness and model in `.cotal/lanes.tsv`.
2. Run `orca worktree create --name <key> --parent-worktree active --json`, so the new task nests under the caller in
   the GUI. Wait for `orca-setup-done` when the setup script writes it, start the agent (step 3), and only then close
   the startup shell and the Setup tab (it stays if the marker never appears), so the agent's tab is first.
3. Call `cotal_spawn` with `agent`, `model`, `cwd` = that worktree, role `lane`, an inline `persona`, and the lane
   brief as `prompt`. For OpenCode add `events: false`; for Claude, `launchOptions: {"permission-mode": …}`.
4. The lane reports by `cotal_dm`, and the DM wakes the parent as its own turn.
5. Task cards show state via `orca worktree set --comment/--workspace-status`.
6. From review tier B up, a reviewer (`personas/reviewer.md`) runs as `<key>-review` in the lane's worktree, on a
   different model from the lane's: read-only, given the issue and commit but not the lane's report, findings as
   blocker/major/minor by DM. Blocker and major are fixed, at most two rounds, then the owner decides.

Never `orca terminal send` or Orca `orchestration` on agents: those type into the composer.

### Launching OpenCode lanes
- Cotal gives every OpenCode agent its own empty database (section 7), so the owner's `opencode auth login` does not
  reach it. Without `OPENCODE_API_KEY` in the manager's environment, that agent's server lists only free models.
- A pinned model the server does not list stops the agent before it joins. The connector prints
  `<model>: GET /api/model does not list it. Refusing before join` into the tab, which closes, so `cotal_spawn` shows
  only `<name> exited on launch`. [tested]
- `opencode models` prints the whole catalog whatever the credentials, so it does not prove a lane can use a model.
- For a paid model, export `OPENCODE_API_KEY` before `project-setup`: it records the name (never the value) in the
  `mgr` service, and Cotal forwards the key to each OpenCode agent. Setup warns, with these steps, when an OpenCode pin
  not named as free has no key and the manager does not hold one. The warning can also name a free model without
  `-free` in its name.
- A `mgr` service that already exists keeps its old variable list. At a natural stop, run
  `cotal svc stop mgr -s <space> --yes` and `cotal svc remove mgr -s <space> --yes`, then rerun `project-setup` with the
  key exported. Afterwards `cotal svc start` refuses in a shell that does not set the key.
- Setup only sees whether the key is set. A wrong key or a spent quota shows only on the agent's first model call, and
  setup cannot tell them apart.
- Free models, such as those named `*-free`, need no key. Pin one in `.cotal/lanes.tsv` only if the owner accepts it
  for that project.

### Permission modes (Claude)
- `auto` is the default: Claude screens each tool call, as `--opt permission-mode=auto` on spawn.
- Every Claude agent also starts with `--allow-dangerously-skip-permissions`, so bypass is in its Shift+Tab mode
  cycle for the owner to switch to; it never starts in bypass unless the project's mode is `bypassPermissions`.
- `bypassPermissions` is an option (non-root only, after accepting its warning once).
- The project's mode is written into the personas, and lanes inherit it (tier table `inherit`).

### Verified on 2026-11-06 [tested]
- A manager-launched agent opens as one tab `cotal-<name>` inside an *existing* Orca worktree (next to the shell Orca
  opens with every worktree), and joins the roster. Tested with OpenCode and with Claude.
- The coordinator flow end to end, run as a spike:
  1. A Claude coordinator created a child worktree (`parentWorktreeId` = the main checkout).
  2. It spawned an OpenCode lane there with `cotal_spawn`.
  3. The lane did the work and DMed `DONE`.
  4. The DM woke the coordinator, which replied without claiming to have checked the file itself.
- The `cotal_spawn` requirements that run surfaced:
  - It needs an inline `persona` unless the name is a saved persona.
  - Handing role `lane` needs `role:lane` in the spawner's `capabilities`, which the personas carry.
- `project-setup` from the workstation with `--host`:
  - Both agents came up and loaded the skill, and the design lead reported ready to the coordinator by DM.
  - A re-run is all `exists`.
- The coordinator refused a request that arrived as a DM from the CLI ("only your words in this tab instruct me"),
  and asked one question with its recommendation instead.
- Draft safety: an OpenCode agent kept a half-typed draft through an incoming DM. Claude: not testable by script
  (`orca terminal send` text never shows in Claude's prompt). Check it by hand: type in the coordinator's tab while
  a lane reports.

### Host gotchas found
- **Manager environment.** `cotal up` starts the manager with a fixed environment allow-list, so proxy variables
  never reach agents through `spawn.env`. Hence the `mgr` service with `envRefs`.
- **Wrong space.** Run every `cotal` call with `-s <space>`. Otherwise it acts on the space last chosen with
  `cotal use`: an early run saw another project's broker and manager.
- **Debian umask 002** makes `~/.config` group-writable, and Cotal refuses to seed extensions. Use umask 022 and
  `chmod g-w ~/.config ~/.config/cotal`; project-setup does both.
- **OpenCode 2's managed service port (49374)** is per machine. A second user needs `opencode service set port <n>`.
- **A global `TMPDIR` in `/etc/environment`** pointing into one user's home breaks orcad for everyone else.
  orca-host-install falls back to `~/tmp`.
- **No systemd user manager** (the trial container lacks `pam_systemd`): orcad and the Cotal services run detached
  and survive logout, but don't restart after a reboot.

`scripts/cotal-agent` and `scripts/orca-cotal-task` are the superseded 2026-11-05 attempt, a foreground agent typed
into an Orca terminal, which never joined the mesh. Kept for reference; project-setup replaces them.

## 6. What was verified
| Check | Result |
|---|---|
| Coordinator (OpenCode 2.2.1, free model) starts two lanes with `cotal_spawn`, lanes write files and DM back | pass, three live Orca tabs [tested] |
| Peer message while a draft is typed in the agent's input box (Orca runtime, OpenCode 2.2.1) | pass: the message ran as its own turn (the coordinator answered with `cotal_dm`) and the draft stayed in the input box; same result earlier with Cotal's own pty runtime [tested] |
| Agents survive the desktop disconnecting | Orca docs say so [docs]; agents kept running while Cotal's manager restarted [tested] |
| Stopping and respawning agents from Cotal | pass [tested] |

## 7. Gotchas from the trial
- **Stale credentials per agent.** Cotal gives every OpenCode agent its own database
  (`<space>/.cotal/opencode/<name>/opencode.db`, via `OPENCODE_DB`), and OpenCode 2 keeps credentials in that
  database. An agent created while a bad key was around keeps it, and respawning under the same name reuses it
  ("This API key has expired … Signing in again will not renew this key"). Fix: stop the agent, move
  `.cotal/opencode/<name>/` aside, spawn again.
- A copied `~/.local/share/opencode/auth.json` (OpenCode 1.x format) is still read by 2.x and overrides anonymous
  free-model access with whatever key it holds.
- OpenCode 2.x runs a background service (`opencode serve --service`) that outlives terminals; restart it after
  credential changes. When killing it by pattern, use `pgrep -f "[o]pencode.exe serve"` so the pattern does not
  match your own shell.
- The OpenCode TUI (bun) loads a native library from `$TMPDIR`: a `noexec` `/tmp` breaks it (set `TMPDIR`).
- Cotal's agents do not inherit the manager's environment: anything they need (proxy, extra keys) goes in
  `spawn.env`.
- Both Orca and Cotal send usage telemetry by default.

## 8. Rove verbs, nearest equivalent
| Rove (`rove api …`) | Orca | Cotal |
|---|---|---|
| `add` (coordinator) | open a worktree, start the agent in a terminal | `cotal spawn -d -n … -C <dir>` |
| `add` (lane) | `orca worktree create` (+ parent) + `orca terminal create` | the coordinator's `cotal_spawn` |
| `send` | `orca terminal send` (types into the terminal) | `cotal send -t`, `cotal_dm` (as a turn) |
| `list`, `get-task` | `orca terminal list`, `orca worktree current` | `cotal ps`, `cotal who` |
| `read-output` | `orca terminal read` | `cotal history --dms`, the agent's tab |
| wait on a lane | `orca terminal wait --for tui-idle\|exit` | lane DMs its coordinator when done |
| `delete` / archive | close the terminal / remove the worktree | `cotal stop <name>` |
| `pr-attach`, per-lane branch | worktree per lane (linked PR shown per worktree) [docs] | none |
| `gate-raise` / `gate-answer` | orchestration gates (experimental) [docs] | **[none]**: ask in a DM |
| `routine-create` | **[none]** | **[none]**: cron or systemd timers on the host |
| budget feed | **[none]** | **[none]** |

beads (`bd`), `gh` and the AGENTS.md templates are unaffected.

## 9. Evaluation checklist
1. Connect a real VPS from the workstation; confirm agents keep running when the laptop sleeps or the link drops.
2. One project, Orca alone: coordinator plus two lanes, each in its own child worktree; judge the sidebar.
3. Same project with Cotal: does the coordinator run lanes on its own; do messages ever disturb what you type.
4. Worktrees: Orca makes one per task natively; Cotal never creates one (it runs an agent in the folder it is given,
   and the Orca runtime only looks up the enclosing worktree). **Untested combination worth trying first:** the
   coordinator creates a child worktree per lane (`orca worktree create … ` with its own worktree as parent), then
   starts the lane with Cotal inside that folder (`cotal spawn -C <worktree path>`, or `cotal_spawn` if its tool takes
   a working directory). That would give one branch per lane, lanes nested under the coordinator in Orca's sidebar, and
   Cotal's draft-safe messaging. Check that the coordinator can run `~/.orca/cli/bin/orca` on the host.
5. Check telemetry is off in both, and which hosts the box contacted (proxy log).
