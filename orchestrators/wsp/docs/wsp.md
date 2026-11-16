# wsp as an alternative to Rove

Status: trial, not a port. Written against wsp 0.3.10 (`@wsp-labs/wsp`) on 2026-11-05, on a Debian box reached
only over SSH, with internet limited to npm, PyPI, GitHub and the model providers. Tags: **[tested]** ran it,
**[docs]** read in wsp's own reference (`skills/wsp/SKILL.md` in its repo) but not run, **[none]** wsp has no
equivalent.

lane-control's scripts and templates call `rove api` throughout. They do **not** run on wsp unchanged. What carries
over is the doctrine (roles, tiers, routing by path map, beads, review by risk) and the shape: a project coordinator
thread that launches worker lanes as child threads and reviews their work.

## 1. When wsp fits
- You want a chat-with-the-coordinator UI with lanes nested under it (sidebar: project, Needs you, Working,
  Finished), in a browser, for agents running on a VPS. **[tested]**
- Agents run headless: each turn is a fresh `opencode run --format json -s <session>` (or `claude -p …`, `codex
  app-server`). There is no agent TUI to type into, so peer messages can never land in a half-typed prompt; a message
  to a thread is simply its next turn. **[tested]**
- You can live without Rove's routines and gates (section 5).

Choose Rove when you need routines, gates, `pr-attach`, the night watch or anything in `scripts/` as it is.

## 2. Install on the box (host and agents in one place)
The robust layout when only SSH connects you to the box: the wsp **host runs on the box** next to the agents, bound
to loopback, and your workstation is only a viewer over an SSH tunnel. (`wsp add root@box` from a laptop host also
exists, but the box's daemon then has to dial back to the laptop, which an SSH-only box cannot.) **[tested]**

```bash
# on the box (Node 22+; root is fine on a dedicated box)
npm i -g @wsp-labs/wsp
npm i -g --prefix /opt/oc1 opencode-ai@1.19.5     # see section 3: wsp 0.3.10 needs OpenCode 1.x
wsp agents setup opencode --program /usr/local/bin/opencode1   # the wrapper from section 3
wsp agents default opencode
tmux new -d -s wsphost 'wsp up --no-relay'        # or: wsp up --service (systemd user unit)
wsp add ~/code/<project>                          # register a project folder
wsp mcp install --agent opencode                  # gives agents the wsp tools + writes an AGENTS.md section
```

Agent sign-in: `wsp agents signin opencode` in a real terminal on the box, or log in with `opencode auth login`
(OpenCode 2.x keeps credentials in `opencode.db`, not `auth.json`; don't copy them by hand). **[tested]**

Air-gapped or proxied boxes **[tested]**:
- Install-time needs npm (and GitHub for OpenCode's own downloads); mirror those. Runtime needs only the model API.
- `wsp up` serves on `127.0.0.1:4400` with no relay; nothing else phones home in this path as far as the trial saw
  (all egress went through an allowlisting proxy and only the model API and npm were hit).
- If node is installed from a tarball, `chown -R root:root /usr/local` (tarballs carry a foreign uid).

## 3. OpenCode versions
- **wsp 0.3.10 breaks on OpenCode 2.x**: it passes `--dir` to `opencode run`, which 2.x removed
  (`Unrecognized flag: --dir`). Use OpenCode 1.x for wsp until that is fixed upstream. **[tested]**
- OpenCode 1.x and 2.x on one box share `~/.local/share/opencode` and break each other ("Database is not empty and
  has no session table"). Give 1.x its own data dirs with a wrapper and point wsp at it:
  ```sh
  #!/bin/sh   # /usr/local/bin/opencode1
  export XDG_DATA_HOME=/root/.oc1/data XDG_STATE_HOME=/root/.oc1/state XDG_CACHE_HOME=/root/.oc1/cache
  exec /opt/oc1/bin/opencode "$@"
  ```
- Model per tier: `wsp run … --model <provider/model>` or `wsp agents set opencode --model …`. lane-control's tier
  table (`config.example.sh`) maps directly onto `--model` / `--effort`. **[docs]**

## 4. Watching from your workstation
```bash
ssh -N -L 14400:127.0.0.1:4400 <box> &      # then open http://127.0.0.1:14400
```
Pairing **[tested]**: the page asks for a code. `wsp host pair` (on the box) prints one, but in 0.3.10 a browser
paired that way is a role-less *device*: it can read but every action is refused with "is not a paired computer's to
ask for until the owner gives this device a role", and the CLI has no command to grant a role. Use
`scripts/wsp-owner-code.mjs` instead, which asks the host for the owner code `wsp init` would mint for its own browser:
```bash
ssh <box> node ~/code/lane-control/scripts/wsp-owner-code.mjs     # prints: owner code XXXXXXXX  expires …
```
`wsp host devices` should then list the browser `AS owner`. Revoke stale ones with `wsp host devices revoke <id>`.

## 5. Coordinator and lanes on wsp
Start the project coordinator as a thread, with the doctrine as its first message:
```bash
wsp run <project> --agent opencode --model <deep-tier-model> --title coordinator \
  "You are the project coordinator. Read AGENTS.md, roles.md and model-routing.md …"
```
The coordinator launches lanes with its wsp MCP tools (`run` with an agent, model and brief); lanes appear nested
under it in the sidebar, and each lane's finish is delivered to the coordinator as a new turn ("thread … finished
(completed, 14s): …"). In the trial a coordinator started two lanes, waited, read their files and reported, in 30 s.
**[tested]**

Rove verbs and their nearest wsp counterpart:

| Rove (`rove api …`) | wsp | |
|---|---|---|
| `add` (coordinator / lane) | `wsp run <project> …`; lanes via the coordinator's MCP `run` (nests under it) | [tested] |
| `send` | `wsp send <thread> "…"` (the thread's next turn) | [tested] |
| `list`, `get-task` | `wsp threads [--tree]`, `wsp thread head <thread>` | [tested] |
| `read-output` | `wsp thread read <thread> [--last]` | [docs] |
| waiting on a lane | `wsp threads wait <thread>… [--timeout s]` | [docs] |
| `delete` / archive | `wsp thread settle <thread> [--finished]` | [docs] |
| `pr-attach` | none needed: wsp reads the PR of the thread's branch; `wsp merge <thread> [--when-checks-pass]`, `wsp fix <thread>`, `wsp review <pr>` | [docs] |
| worktree per lane | `wsp worktree <project> <branch>`, `wsp run --branch` | [docs] |
| `gate-raise` / `gate-answer` | **[none]**. Tool approvals show as *Needs you* (`wsp thread allow/deny`); owner decisions must be asked in the coordinator's reply. `--notify me` on `run` pings you when a thread finishes | |
| `routine-create` | **[none]**: run `scripts/*.sh` from cron or systemd timers on the box | |
| `feedback`, `report`, `deliveries` | **[none]** | |
| budget feed | `wsp usage [--range week] [--by agent\|model]` gives the numbers; the window and weekly steps in `window-guard.sh` would need rewriting against it | [docs] |
| `context` | the section `wsp mcp install` writes into `AGENTS.md` | [tested] |

beads (`bd`), `gh` and the AGENTS.md templates are unaffected.

## 6. Gotchas
- `wsp` sees everything under the project folder as the thread's changes, including other tools' state directories:
  gitignore them.
- A thread with no turn yet (a launch that failed, for example on the OpenCode 2.x flag) stays in the sidebar as
  *Failed*; `wsp thread forget <thread>` clears it.
- `wsp up` keeps its token at `~/.wsp/host-token`: anyone with a shell on the box is the owner.
- wsp is young (0.3.x, small community, AGPL-3.0): pin the version you tested.
