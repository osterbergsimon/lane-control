# OpenCode (v2)

Status: in use

Adapter: `adapter.py`, plugin `lane-persona.js`. Orca agent id `opencode2`; Orca passes `--model` only. The model
must be one the tier rules allow for the project (`core/scripts/lane-tier`).
- Start: `env LANE_ROLE=<role> LANE_PERSONA=<persona> OPENCODE_CONFIG_CONTENT={"model":"<provider/model>"} opencode
  --standalone [--auto]`. OpenCode 2.2.1's interactive command has no `--model` flag; Orca 1.4.250 sets the model the
  same way for its own opencode2 agent. `--standalone` runs a private server started from that command, so the
  markers reach the plugin; the shared background service would not see them. `--auto` stands for project-setup's
  `auto` and `bypassPermissions`.
- Persona after compaction: `lane-persona.js` in `$XDG_CONFIG_HOME/opencode/plugins/` (default
  `~/.config/opencode/plugins/`), one copy per user. `experimental.chat.system.transform` adds the persona to the
  system prompt on every turn; `experimental.session.compacting` adds it to the compaction context as a backstop.
  Both act only with `LANE_ROLE` and `LANE_PERSONA` set and the file readable.
- Idle signal: the newest `time_updated` of the checkout's sessions in the v2 store,
  `$XDG_DATA_HOME/opencode/opencode.db` (default `~/.local/share/opencode/`), tables `session_v2` and
  `session_message`, times in milliseconds. The older `session` table and `storage/` folder are not v2's.
- Wake: Orca types reports into an OpenCode terminal as for Claude; not yet confirmed for an OpenCode coordinator.

## History
- 2026-11-19 adapter (NO-1): start command, plugin, idle signal. The plugin types installed with OpenCode
  (`@opencode-ai/plugin` 1.15.10) name both hooks; the suite runs the plugin under node with stand-in hooks.

## Retest when
- OpenCode changes its plugin hooks, its `--standalone` flag or its session store.

## Trials
None yet. Manual check, still to do (needs a real v2 session with a provider the owner allows): start a session with
the markers, ask for the persona's first line (system transform); run `/compact`, ask again (compaction backstop);
start one without the markers in the same folder and check it does not know the persona.
