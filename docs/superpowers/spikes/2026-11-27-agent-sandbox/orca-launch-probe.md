# Orca launch probe (lc-9r81.1), SBX-2 task 1, 2026-11-28

Read only: Orca 1.4.274's bundled app (extracted from its `app.asar`), `orca-ide` help, the read-only `dispatch-show`
and `worker-show` of this lane's own dispatch, and Orca's settings document opened read-only and immutable. No Orca
setting was changed, no internal RPC used, no agent started.

## The hook: Orca's custom command for OpenCode (owner's decision 1, 2026-11-28)

- Orca's catalog launches `opencode` (OpenCode) and `opencode2 --standalone` (OpenCode 2, the default here). A custom
  command replaces the whole catalog command for that agent: `shared/agent-launch/startup/agent-command-plan.js`
  (`parseAgentCommand(defaultBinary, commandOverride)`: leading `NAME=value` words, then the binary and its prefix
  arguments; Orca's own launch arguments follow). So the custom command must carry `--standalone` itself.
- The setting is `agentCmdOverrides.<agent id>` in the profile's `settings` document
  (`profiles/<profile>/profile-state.db`, table `profile_state_documents`, `domain = 'settings'`). In the app it is
  Settings -> Agents -> <agent> -> custom command; `orca agent installed --help`: "ones set up there with a custom
  command are included". Here it was `{}` (nothing set).
- What the owner sets (printed by `lane-control machine check --sandbox`, which reads the setting and says whether it
  is set): OpenCode 2: `<data>/lane-control/current/core/scripts/lane-sandbox-shim opencode2 --standalone`;
  OpenCode: `<data>/lane-control/current/core/scripts/lane-sandbox-shim opencode`. The installed `current` path, so
  an install never changes the setting.
- Chats: `tui-agent-launch-command-override.js`: "Terminal-backed chat skips the structured catalog when its launch
  command is customized" (`hasExplicitTuiLaunchCommand`), and `structuredAgentCommandPlan` refuses an override with
  environment words or shell syntax. So with the custom command set, an OpenCode chat surface runs through the same
  TUI command, the launcher included. To be confirmed with a live stub launch (task 9, the owner's go).
- Resume: `agent-resume-argv.js`: "the launch command owns standalone/shared mode, including user overrides": a
  resumed OpenCode session goes through the custom command too.

## What Orca puts in an agent pane (names only)

`ORCA_TERMINAL_HANDLE`, `ORCA_PANE_KEY`, `ORCA_AGENT_PANE` (alias of the pane key), `ORCA_TAB_ID`, `ORCA_WORKTREE_ID`,
`ORCA_WORKSPACE_ID`, `ORCA_AGENT_HOOK_ENDPOINT` (a file under Orca's user data), `ORCA_AGENT_HOOK_PORT`,
`ORCA_AGENT_HOOK_TOKEN`, `ORCA_AGENT_HOOK_ENV`, `ORCA_AGENT_HOOK_TRANSPORT`, `ORCA_AGENT_HOOK_VERSION`,
`ORCA_AGENT_LAUNCH`, `ORCA_AGENT_LAUNCH_TOKEN`, `ORCA_USER_DATA_PATH`, `ORCA_CLI_COMMAND`, `ORCA_CLI_BIN_DIR`,
`ORCA_APP_VERSION`, and for OpenCode `ORCA_OPENCODE_CONFIG_DIR` (its overlay under user data),
`ORCA_OPENCODE_SOURCE_CONFIG_DIR` (the owner's own config: never passed inside), `ORCA_OPENCODE_AGENT`,
`ORCA_OPENCODE_PLUGIN_API`, `ORCA_OPENCODE_STARTUP_PROMPT_*` (bundled code; a live OpenCode pane is task 9).

The sandbox passes `ORCA_AGENT_HOOK_*`, `ORCA_AGENT_LAUNCH*`, `ORCA_OPENCODE_*` (not the source config dir),
`ORCA_PANE_KEY`, `ORCA_TAB_ID`, `ORCA_WORKTREE_ID`, `ORCA_TERMINAL_HANDLE`; the path variables only when the launcher
bound that path read-only (the overlay folder and the hook endpoint file, nothing else of user data).

What each token reaches, from the code: the hook token authorizes the hook listener on the forwarded hook port (the
only port a lane reaches on loopback); `ORCA_AGENT_LAUNCH_TOKEN` ties a launch to its dispatch (Orca keeps only
`launch_token_hash` on the dispatch record; the scrub-safe alias drops it when absent, "so inherited parent identity
cannot claim this pane's hooks"). Neither is the Orca runtime token, which stays out (`orca-runtime.json` is not
mounted). Whether the launch token authorizes anything beyond this pane's hooks is not settled by reading: task 9.

## The terminal a dispatch is bound to

- `orca-ide orchestration dispatch-show --task=<task> --json`: `result.dispatch.assignee_handle` (the worker's
  terminal handle), `id`, `task_id`, `status`. The `--flag=value` form is accepted.
- `orca-ide orchestration worker-show --dispatch <id> --json`: `result.terminal`, `terminalState`, `workspace`.
- The broker checks a report's dispatch with the first; `lane-control lane started` checks the run record's terminal
  with the second.

## Not measured here (task 9, with the owner's go)

- A stub `opencode2` launched by Orca through the custom command, as a terminal and as a chat; the binary run, the
  environment and files Orca sets in that pane; Orca's agent status for a launch whose process is the launcher.
- Which OpenCode store `worker-read --source transcript` opens for a sandboxed lane (its store is the ticket's own
  data folder, bound at `~/.local/share/opencode` inside).
- `orca-ide workspace cleanup preview --json` for an unchanged sandboxed lane worktree before and after
  `worker-release` (the design closes sandboxed lanes with `lane-control sandbox close`, never with those checks).
