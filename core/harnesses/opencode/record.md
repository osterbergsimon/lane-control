# OpenCode (v2)

Status: in use

Adapter: `adapter.py`, plugin `lane-persona.js`. Orca agent id `opencode2`. `worker-start` refuses `--model` for that
id: a lane with a tier's model starts as agent id `opencode` (the `native-orca-lanes` skill). The model must be one
the tier rules allow for the project (`core/scripts/lane-tier`).
- Start: `env LANE_ROLE=<role> LANE_PERSONA=<persona> OPENCODE_CONFIG_CONTENT=<config> opencode2 --standalone
  [--auto]`, `<config>` being `{"model": "<provider/model>", "agents": {"build": {"model": "<provider/model>"}}}`.
  `opencode2` is the v2 package's own command and what Orca detects as `opencode2` (a plain `opencode` may be v1 on
  some PATHs). OpenCode 2.2.1's interactive command has no `--model` flag; the model goes in through
  `OPENCODE_CONFIG_CONTENT`, as Orca 1.4.250 does for its own opencode2 agent, and is pinned on the primary agent
  `build` too, as Orca does: a model pinned on that agent in the user's own config would win otherwise (checked
  live). Unlike Orca, the adapter does not merge into an `OPENCODE_CONFIG_CONTENT` already in the terminal's
  environment; it replaces it. `--standalone` runs a private server started from that command, so the markers reach
  the plugin; the shared background service would not see them. `--auto` stands for project-setup's `auto` and
  `bypassPermissions`.
- Persona after compaction: `lane-persona.js` in `$XDG_CONFIG_HOME/opencode/plugins/` (default
  `~/.config/opencode/plugins/`), one copy per user. It uses the v2 plugin API: `export default { id: "lane-persona",
  setup }`, and `setup(ctx)` registers two session hooks with `ctx.session.hook`: `"context"` adds the persona to
  the system prompt of every model request of a turn, and `"compaction"` adds it to the compaction request. It acts
  only when the server was started with `LANE_ROLE` and `LANE_PERSONA` set and the file is readable. OpenCode
  watches the plugin folder, so installing or updating the file reaches every running OpenCode server of the user at
  once; without the markers it registers nothing there.
- Project-level install: `LANE_OPENCODE_PLUGIN=project` (default `user`) makes the adapter put the same file in
  `<checkout>/.opencode/plugins/` instead; `project-setup` keeps it out of git (`.git/info/exclude`) and, because it
  is not committed, installs it in the design lead's own worktree too. A server may load both a user-level and a
  project-level copy: a copy of this version adds the persona to a request only when the request does not already
  carry the same text, so two copies of this version add it once. An older user-level copy has no such check, so a
  project install updates an existing user-level `lane-persona.js` of ours (it has `id: "lane-persona"`) when its
  bytes are a version lane-control shipped (`lane-persona.sha256`; add the hash of the old version whenever the
  plugin changes). A copy with other bytes (a local edit, a fork), a symlink, or another plugin's file is never
  replaced; the first two are named in the output, with the warning that the persona may be added twice.
  Not verified against a real OpenCode, and the first live
  check to run: that OpenCode 2.2.1 loads `<project>/.opencode/plugins/` at all, that it accepts two plugin files with
  the same id (it might keep one, which is fine, or refuse both, which is not), and whether it writes more files into
  `.opencode/` (1.x wrote `package.json` and `node_modules` beside plugins; they would show as untracked). The
  double-load is tested with a node driver that registers two copies on one context.
  `project-setup` has no flag for it yet: set the variable in its environment.
- Idle signal (the record is a published interface: `../session-record.md`; `core/scripts/lane-idle` reads and waits
  on it): the coordinator's own session only. The plugin, in a marked server, records the id of the session
  taking a turn in `${XDG_STATE_HOME:-~/.local/state}/lane-control/sessions/<checkout>/<role>.opencode`; idle is the
  newest `time_updated` of that session's family (up to its root, then every sub-agent session below it) in the v2
  store, `$XDG_DATA_HOME/opencode/opencode.db` (default `~/.local/share/opencode/`), tables `session_v2` and
  `session_message`, times in milliseconds. Other sessions in the checkout do not count; no record means no marked
  session (`None`). The record is kept under the folder the server was started in (the checkout, as Orca's
  terminal starts it); a server started in a sub-folder is not found. The older `session` table and `storage/`
  folder are not v2's; a store with only those raises "not an OpenCode 2 store".
- Beads session hook: not covered. bd 1.0.4 has an OpenCode recipe (`bd setup opencode`), but it only writes an
  AGENTS.md section; there is no session-start hook or plugin to install, so the coordinator's persona and the
  `bd recall` steps carry beads for an OpenCode session. Revisit when bd offers a plugin.
- Folders come from the caller's environment (`XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`). A server
  started with other ones (an Orca-managed account with private XDG folders, or `OPENCODE_CONFIG_DIR`) loads
  plugins from and writes its store to places the adapter does not look: the plugin is then not installed for it,
  and idle reads as `None`.
- Wake: Orca types reports into an OpenCode terminal as for Claude; not yet confirmed for an OpenCode coordinator.
- Waiting on another session (`core/scripts/lane-idle --wait`): no background-command wake is known for OpenCode v2
  (not verified), so an OpenCode coordinator must not run `--wait` unbounded in the foreground. Wait with a bounded
  `--timeout` and look again on the next turn, or use `orca-ide orchestration check --wait` (it blocks until mail
  arrives, with `--timeout-ms`; not verified for an OpenCode coordinator either).
- Markers are environment variables: everything in a marked server gets the persona, child (sub-agent) sessions
  included, and so does an OpenCode the coordinator starts from its own shell.

## History
- 2026-11-19 adapter (NO-1): start command, plugin, idle signal.
- 2026-11-19 review 1: the first plugin used the v1 API (v1 hook names, a named export); 2.2.1 refuses it
  ("Plugin must export a default definition with an id and an effect or setup function") and logs "failed to load
  plugin". Rewritten for the v2 API and checked live.

- 2026-11-20 UP-6: project-level install (`LANE_OPENCODE_PLUGIN=project`), the persona added once when two copies are
  loaded, and the session record published as a stable interface (`../session-record.md`, `lane-idle`).

- 2026-11-23 away-improve (lc-yzb): the deny rules Claude coordinators get have an OpenCode counterpart, a plugin
  hook (`lane-away-guard.js`, installed beside `lane-persona.js`). `setup(ctx)` registers
  `ctx.tool.hook("execute.before", fn)`; throwing from `fn` stops the call and the model gets the message as the tool
  result (probed live, below). The shell tool is `shell` (`bash` in older builds); the plugin matches both. It acts
  only in a session started with `LANE_ROLE=coordinator`, and only for a checkout `lane-improve on` recorded
  (`install_away_denies` writes the project and its integration branch to the state folder; `remove_away_denies`
  deletes it). On every shell call it reads `improve.json` and `improve-drain.json` (the same validation as
  `core/improve.py`; unreadable state means no guard, said once on stderr), so it follows `on`, `off` and
  `lane-improve drained` live, with no restart; a running coordinator needs one restart to load the plugin the first
  time. It refuses `gh pr merge` (and a gh api call that merges) and a git push to the integration branch, and says to
  use away-merge. A push is judged by its effective repository (the call's `workdir`, a `cd` earlier in the command,
  `git -C`), so the coordinator's beads backup, whose branch is also `main`, is never blocked: an explicit push to the
  integration branch is refused in the project's checkout or when the repository cannot be told, a bare push (no
  refspec, `--all`, `HEAD`) only in the project's checkout, and a dry run never. **It is a backstop, not a wall**: it
  reads the command text, so other routes past it exist (a script that pushes, a tool reached through MCP whose id is
  not `shell`). The real guards are `away-merge`, the persona rule and branch protection where the repo has it. A
  lane's own session has no guard.

- 2026-11-27 extras (lc-7c6): a folder's extras for OpenCode go in `.opencode/opencode.json` (`mcp`, with
  `{env:NAME}` for each secret; `plugin`, pinned npm packages) and `.opencode/skills/<name>` (links): the coordinator's
  in the main checkout, a lane's or reviewer's in its own worktree. Since lc-yw1g also `permissions` (the
  owner's allows, then every deny) and `policies` (every deny and lane-control's floor as `tool.use` deny statements),
  and `.opencode/plugins/lane-control-extras.js` (env values at `shell.create.before`, agent hooks at
  `tool.execute.before|after`; 2.2.1 loads the mixed file, checked with `debug config --standalone`). **Not verified against a real OpenCode 2, and the first live check to run (lc-hat1):** that it reads
  `<folder>/.opencode/opencode.json` and `.opencode/skills/`.

## Retest when
- OpenCode changes its plugin API, its `--standalone` flag or its session store: run the live check.

## Trials

### 2026-11-19 live check (OpenCode 2.2.1)
`LANE_LIVE_OPENCODE=1 python3 -m unittest tests.test_harnesses.OpenCodeLiveTests` (opt-in, about 10 s): the real
`opencode2 serve` in temp HOME and XDG folders, the plugin installed by the adapter, a fake model server on
127.0.0.1 (`tests/stubs/fake_llm.py`; no real provider, no sign-in, no code leaves the machine), driven through the
server's API (prompt, compact, prompt).
- Load: pass — the log loads `lane-persona.js` and has no "failed to load plugin".
- Turn: pass — the persona is in the system prompt of the turn's model request (the `"context"` hook). The title
  request has no persona (the plugin does not hook `"generate"`).
- Compaction: pass — the persona is in the system prompt of the compaction request (the `"compaction"` hook; a
  one-off run that tagged each hook's text showed each hook firing on its own request), and in the turn after it.
- No markers: pass — a server started without them sends no persona.
- Idle: pass — the plugin recorded the session it served, and `idle_minutes` read it from the store the server
  wrote.
- Model: pass — the model from `start_command`'s `OPENCODE_CONFIG_CONTENT` is the one used (the prompts carried
  none), also when the user's config pins another model on `build`; with the top-level `model` alone the pinned one
  won every request.
- Not checked: the TUI (`opencode2 --standalone` in a terminal) itself; the server, the plugin loader and the hooks
  are the same ones it runs.

### 2026-11-23 live probe of a blocking hook (OpenCode 2.2.1)
A scratch HOME and XDG folders, the real `opencode2 serve`, a fake model on 127.0.0.1 that answers with a tool call
(no real provider), and a scratch plugin; driven through the server's API.
- Hook: pass: `ctx.tool.hook("execute.before", fn)` fires before every tool call with `{ tool, sessionID, input, ... }`.
- Block: pass: throwing from it left the command unrun (its file never appeared) and the model's next request carried
  the message as the tool result. The control run with a logging hook ran the same command.
- Live: pass: on one server a flag file read on each call gave refused, ran, refused, with no restart.
- Names: the shell tool is `shell`; Code Mode's `execute` tool has no shell in its catalog in 2.2.1, so the only route
  to a shell call is the `shell` tool.
- The opt-in `tests.test_opencode_guard.GuardLiveTests` repeats it against the shipped plugin.
