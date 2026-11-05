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
| Lanes nested under the coordinator in the sidebar | worktrees nest under a parent ("N children" chip) [docs] | **no** by itself: Cotal opens each agent as a terminal tab in the worktree containing its folder, side by side [tested]; maybe with a worktree per lane (section 9, item 4) |
| A worktree (branch) per task | yes, Orca's core unit [docs] | **no**: Cotal never creates worktrees [tested] |
| Chat UI instead of a TUI | "structured native chat", but **not over SSH** ("SSH sessions continue to use terminal-backed Chat UI") [docs] | n/a |

Rule of thumb: **Orca is the cockpit** (SSH, terminals, editor, git per worktree). **Cotal is the radio** (who
started whom, messages that wait for the agent's turn, roles). Use Orca alone if you drive lanes by hand; add Cotal
when a coordinator should run lanes and talk to them while you type.

## 2. Install Orca on the workstation
- Linux builds: AppImage, x86_64/aarch64 RPM, AUR `stably-orca-bin`. On NixOS run the AppImage with `appimage-run`.
  Verify it against the sha512 in the release's `latest-linux.yml`. [tested]
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
3. Host prerequisites: git, the agent CLIs, and either internet to `nodejs.org` (Orca downloads its Node runtime) or a
   Node mirror (Orca offers "Use a Node.js mirror" on the connection error). For a proxied or air-gapped host allow or
   mirror `nodejs.org` and `onorca.dev` in addition to npm, PyPI and GitHub. [tested]
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

## 5b. WIP: Orca makes the tasks, Cotal is only the agent
**Status: work in progress, stopped mid-test (the model quota ran out).** This is the intended layout. Section 5 has
Cotal's manager open terminals in Orca. Here **Orca sets up everything it can** (task, worktree, branch, nesting
under the coordinator, terminal), and Cotal provides only the agent that runs in that terminal and its messaging. One
task is the coordinator; it creates more Orca tasks the same way, and all of them talk over Cotal's mesh.

Pieces (in `scripts/`, install on the host's `PATH`):
- `cotal-agent`: starts OpenCode as a Cotal agent **in the current terminal** (`cotal spawn` in the foreground, so no
  Cotal manager and no `--runtime orca`), named after the worktree it runs in. Configure it in Orca as a custom
  agent ("OpenCode (Cotal)", command `cotal-agent`) for tasks you create by hand. Env: `COTAL_SPACE`,
  `COTAL_AGENT_MODEL`, `COTAL_AGENT_EXTRA` (default `--no-events`, for OpenCode 2.x).
- `orca-cotal-task <name> "<first task>" [--role R]`: run **from inside a worktree** (the coordinator runs it in its
  own shell). Asks Orca for a new worktree nested under the caller's (`orca worktree create … --parent-worktree
  worktree:<caller>`), then opens a terminal in it running `cotal-agent --role R -p "<first task>"`
  (`orca terminal create --command`). Prints `{"name","worktree","branch","terminal"}`. The brief is shell-quoted,
  since Orca types `--command` into the terminal's shell.

Setup on the host (beyond section 5's install; the Cotal manager is not needed for this path, the broker and delivery
daemon from `cotal up` are):
```bash
install -m 755 scripts/cotal-agent scripts/orca-cotal-task /usr/local/bin/
cd <main checkout of the repo, open in Orca>
orca-cotal-task boss "You are the project coordinator … To create a teammate run: orca-cotal-task <name> \"<task>\" …" \
  --role coordinator
```
Then DM the coordinator (`cotal send -t boss "…"` or `cotal chat`) and have it create its teammates.

Why not Orca's own agent launch: `orca worktree create --agent` and `orca agent launch` accept only Orca's known
agents, so a Cotal agent goes in with `orca terminal create --command`.

Reached so far (2026-11-05) [tested]:
- `orca-cotal-task boss … --role coordinator` from the repo's main checkout: Orca created worktree `boss` on branch
  `boss` under `~/orca/workspaces/<repo>/`, nested under the main worktree, opened a terminal titled `cotal-boss`, and
  `cotal-agent` started OpenCode 2.2.1 there (terminal title `cotal:demo:boss`, Cotal connector process running).
- `cotal ps` does not list such agents (it lists manager-launched seats only); use `cotal who`.

Not yet verified:
- **the agent joining the mesh: it did not.** About 3 minutes after launch `boss` was not in `cotal who`, although
  its OpenCode TUI and Cotal connector were running. Not diagnosed. Candidates: a foreground spawn from a folder
  outside the space's project root (the worktree lives under `~/orca/workspaces/`), or the connector stuck before
  joining (the free-model quota was exhausted at that moment). First thing to check: run `cotal-agent` by hand in a
  worktree terminal and read what `cotal spawn` prints before the TUI takes the screen;
- the first task given with `-p` reaching the agent;
- the coordinator running `orca-cotal-task` itself, and the new tasks nesting under its worktree;
- two task agents messaging each other directly, and the coordinator steering them by DM;
- draft safety in this mode (expected to match section 6: it is the same OpenCode plugin).

Next session: re-run the steps above with a model that has quota (Orca's OpenCode profile key, or `COTAL_AGENT_MODEL`),
then fill in this list and fold the result into section 6.

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
