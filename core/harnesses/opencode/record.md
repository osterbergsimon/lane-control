# OpenCode (v2)

Status: in use

Adapter: `adapter.py`, plugin `lane-persona.js`. Orca agent id `opencode2`; Orca passes `--model` only. The model
must be one the tier rules allow for the project (`core/scripts/lane-tier`).
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
- Idle signal: the coordinator's own session only. The plugin, in a marked server, records the id of the session
  taking a turn in `${XDG_STATE_HOME:-~/.local/state}/lane-control/sessions/<checkout>/<role>.opencode`; idle is the
  newest `time_updated` of that session's family (up to its root, then every sub-agent session below it) in the v2
  store, `$XDG_DATA_HOME/opencode/opencode.db` (default `~/.local/share/opencode/`), tables `session_v2` and
  `session_message`, times in milliseconds. Other sessions in the checkout do not count; no record means no marked
  session (`None`). Sessions are found by the recorded id, so one started in a sub-folder of the checkout counts
  too. The older `session` table and `storage/` folder are not v2's; a store with only those raises "not an
  OpenCode 2 store".
- Folders come from the caller's environment (`XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`). A server
  started with other ones (an Orca-managed account with private XDG folders, or `OPENCODE_CONFIG_DIR`) loads
  plugins from and writes its store to places the adapter does not look: the plugin is then not installed for it,
  and idle reads as `None`.
- Wake: Orca types reports into an OpenCode terminal as for Claude; not yet confirmed for an OpenCode coordinator.
- Markers are environment variables: everything in a marked server gets the persona, child (sub-agent) sessions
  included, and so does an OpenCode the coordinator starts from its own shell.

## History
- 2026-11-19 adapter (NO-1): start command, plugin, idle signal.
- 2026-11-19 review 1: the first plugin used the v1 API (v1 hook names, a named export); 2.2.1 refuses it
  ("Plugin must export a default definition with an id and an effect or setup function") and logs "failed to load
  plugin". Rewritten for the v2 API and checked live.

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
