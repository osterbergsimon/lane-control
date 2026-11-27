# Extras, part 2: agent hooks, permission rules, env values (lc-yw1g, EXT-1b)

The owner approved (2026-11-26) agent hooks, permissions and non-secret env vars as extras. EXT-1 (#84) shipped
skills, MCP servers, personas and plugins and split these three off after its review found they leak: allow rules
reached secrets and `--private` paths (B2), on OpenCode a later allow beat an earlier deny (B3), secrets were accepted
and printed (B6), a changed hook was dropped silently (M2). This note says how each is done so none of that can happen.
It builds on the same store, scopes and writers (core/extras.py) and on EXT-2's pinning, verified copies, safe git and
secret-shape checks (core/hooks.py).

## The three kinds

```
lane-control project|machine extras add <project> ...
  --agent-hook PreToolUse|PostToolUse|SessionStart|Stop [--matcher Tool|Tool|...] [--timeout S] -- <script>
       <script>: an absolute path (pinned by its hash) or <git-url>@<full commit>:<path> (pinned, fetched at add)
  --permission allow|deny <rule>        a Claude rule: Tool or Tool(specifier)
  --env NAME=value                      a non-secret environment value
  --for lanes|coordinator|reviewer|all  as for every extra (default all)
```

Ids: `agent-hook:<name>` (shown with its level, `agent-hook:machine:<name>`; a machine's and a project's both run, as
lifecycle hooks), `permission:allow:<rule>` / `permission:deny:<rule>`, `env:<NAME>` (a project's replaces the
machine's for that project). Each goes only where that role's sessions read it (EXT-1's B1 design):

```
              coordinator                                   lane or reviewer (lane prepare, its own worktree)
Claude        the launch --settings file in the store:      .claude/settings.json (or settings.local.json when the
              permissions, env, hooks                       project commits settings.json): permissions, env, hooks
OpenCode      the main checkout's .opencode/opencode.json   .opencode/opencode.json (permissions, policies) and
              (permissions, policies) and                   .opencode/plugins/lane-control-extras.js
              .opencode/plugins/lane-control-extras.js      (env, agent hooks)
              (env, agent hooks)
```

Nothing goes in the main checkout's `.claude/` (every worktree reads its `settings.local.json`). Every file is one
lane-control creates and owns whole, all or nothing, recorded, git-excluded, never committed, never through a link
(apply_folder, unchanged).

## Permissions: lane-control's denies are a floor no allow can reach

### How each harness decides (read, with what was run)

- **Claude Code** (permissions.md, settings.md; 2.1.332 here): deny, then ask, then allow; the first match in that
  order decides; "an allow rule can't carve an exception out of a deny rule", and "if a tool is denied at any level,
  no other level can allow it": rules from every settings file and `--settings` are merged lists. A PreToolUse hook's
  `allow` does not bypass a deny either. Read/Edit deny rules bind the file tools, redirection targets and the file
  commands Claude recognises (`cat`, `head`, `sed`, `tee`, ...), and **not** a program that opens files itself (a
  Python or Node script, `grep -r`): Claude says so. A `/path` rule anchors at the settings file's folder for
  `--settings`, at the working directory for project settings; `//path` is absolute, `~/` home.
- **OpenCode** (v2 permissions.md and policies.md; 2.2.1 here): `permissions` is an ordered list and **the last match
  wins**; the default is allow everything (`* * allow`, `.env` reads ask). V1's `permission` object is converted to
  the same ordered list (`opencode debug config --standalone` and `debug agents --standalone` in a scratch HOME show
  it). `policies` with `action: tool.use` run after every permission rule and saved approval and only ever turn
  allow or ask into deny ("A `tool.use` statement with `allow` never grants access"). Among policies the last match
  wins and broader files (the user's global config) are evaluated after a project's.

### What lane-control writes

When a role has at least one permission extra, its file gets, in this order:

1. Claude: `permissions.allow` = the owner's allows; `permissions.deny` = the owner's denies + **the floor**.
2. OpenCode: `permissions` = the owner's allows, then the owner's denies (so a deny always comes after any allow:
   B3); `policies` = every deny again (the owner's and the floor) as `tool.use` deny statements, which no permission
   rule, saved approval or later file's allow rule can lift. lane-control never writes an allow policy.

The floor (core/agentrules.py `floor`): read and edit of the secrets and auth files (`~/.ssh`, `~/.gnupg`, `~/.aws`,
`~/.azure`, `~/.kube`, `~/.config/gcloud`, `~/.config/gh`, `~/.docker/config.json`, `~/.netrc`, `~/.git-credentials`,
`~/.npmrc`, `~/.pypirc`, `~/.password-store`, the keyrings, Claude's `.credentials.json`, OpenCode's `auth.json`,
Codex's `auth.json`), the project's `.env` and `.env.*` files (templates too: Claude cannot carve them out of a deny),
every `--private` glob (in the worktree and at the main checkout's absolute path); edit of the agents' own settings
(`~/.claude`, `~/.config/opencode`, the main checkout's `.claude/settings.local.json`, the files lane-control writes in
that folder) and of lane-control's own folders (`~/.config/lane-control`, `~/.local/share/lane-control`,
`~/.local/state/lane-control`, `~/.agents`); and the commands that print a credential (`gh auth token`, `gh auth
status --show-token|-t`, `git credential`, `git credential-store|cache`, `printenv`, a bare `env`, `security
find-*-password`, `secret-tool lookup|search`). On OpenCode the secret folders are also denied as
`external_directory`, which its shell checks for paths it can infer from a command.

### What is refused at add, and again at apply

The effective rules are computed per harness (`claude_decision`, `opencode_decision`): with the floor written, no
allow can make a floor path or command allowed through the same tool on either harness, whatever the order or level
(machine, project) of the rules. The tests prove it per harness for every floor rule against wildcard allows, both
add orders and machine/project mixes. What the rules cannot bind is another route: a shell command that reads a file
itself. So an allow is refused when it:

- is a bare or wildcard tool (`Bash`, `Bash(*)`, `Read`, `Edit(*)`, `*`), an unknown tool, or a Glob/Grep path rule
  (Claude ignores those; use Read);
- **Bash**: starts with a wildcard, has a wildcard anywhere but one trailing ` *` / `:*`, has no subcommand before it
  (`Bash(git *)`, `Bash(npm:*)`: Claude's own advice), holds a shell operator or substitution, names a program by
  path, or its program is a shell, an interpreter, a file reader, copier or archiver, a network tool, a wrapper that
  runs another command (`env`, `sudo`, `xargs`, `timeout`, ...), a runner of arbitrary packages (`npx`, `uvx`,
  `docker`, `nix run`, ...), or `git` with an option before its subcommand or `config`/`credential`; or it overlaps a
  floor command (pattern intersection, the deny would win anyway: it is refused so the owner is told);
- **a file tool** (Read, Edit, Write, NotebookEdit): is not a plain path relative to the session's folder (a leading
  `/`, `//` or `~`, a `.` or `..` segment, a segment starting with a dot, a leading wildcard), names a floor path
  itself, or overlaps a `--private` glob of any project it applies to, either way (`fixtures/**` with `--private
  fixtures/customers/*`; for a machine extra, every project's globs).

An allow that is accepted and runs a program the project controls (`make test`, `npm run build`, `pytest`) is said at
add: what that program reads is not bound by any rule. The docs claim only what is enforced.

Denies are never refused for their reach (they only narrow); a deny with no OpenCode form (`Agent(model:opus)`) is
Claude only, and add says so. Rules are translated to OpenCode where they map: Bash to `shell`, Read to `read`,
Edit/Write to `edit`, WebFetch(domain:h) to `webfetch` `*://h` and `*://h/*`, `mcp__s__t` to `s_t`, Skill and Agent to
`skill` and `subagent`; Claude's `**` and `*` both become OpenCode's `*` (wider: safe for a deny, said for an allow).

## Agent hooks

- **The owner's script only**: an absolute path outside every project checkout (`--allow-repo-path` takes one with a
  warning), pinned by its hash, or a file in a git repo at a full commit, fetched at add: make_hook's pinning and
  containment exactly (EXT-2). Never from a repo, never with arguments.
- **What is written**: the hook entry runs lane-control's own runner from the install
  (`python3 -I <lane-control>/core/scripts/lane-agent-hook run <project root|-> <level> <name> <event>`), never the
  script. The runner reads the extra from the owner's store, checks it against its pin and runs a private copy made
  just before (hooks.prepared: a path hook's bytes that matched its hash, a git hook's pinned commit written out
  object by object, each hashed), with the event's JSON on stdin passed through, and its stdout, stderr and exit code
  passed back. Its environment is EXT-2's short list (PATH, HOME, USER, locale, XDG folders, TMPDIR, SSH_AUTH_SOCK) and
  `CLAUDE_PROJECT_DIR`, `LANE_AGENT_HOOK_EVENT`, `_NAME`, `_HARNESS`: none of the agent's variables, so no token or
  key. A timeout per hook (default 60 s, at most 600): Claude's own `timeout`, and the runner stops the copy's process
  group at it.
- **A changed hook is never dropped silently**: `check` and `doctor` flag it `changed` (or `missing`); apply
  (refresh, prepare) refuses it as a problem, so the folder keeps none of lane-control's extras, prepare exits 1 and
  refresh exits 1; and a session that still has it gets a refusal from the runner: a PreToolUse hook blocks the tool
  call (exit 2, fail closed) with the reason, the other events report the error (exit 1) and go on. A removed hook
  runs nothing: the runner finds no extra and says so.
- **OpenCode**: PreToolUse and PostToolUse map to the plugin hooks `tool.execute.before` (a throw stops the call: the
  same mechanism as lane-away-guard.js, probed on 2.2.1) and `tool.execute.after`, run through the same runner with
  Claude's JSON shape (`tool_name` is OpenCode's name; a matcher's Claude names are translated). SessionStart and Stop
  have no blocking OpenCode equivalent: they are Claude only, and add says so. A matcher is `*` or tool names joined
  by `|` (no regex), so it means the same on both.

## Env values

- **Names** are capitals, digits and `_`. Refused: a secret-looking name (EXT-1's secret words: KEY, TOKEN, SECRET,
  PASSWORD, AUTH, ...), and the names lane-control, the harness or the shell run on: `LANE_*`, `ORCA_*`, `HOME`,
  `XDG_*`, `PATH`, and also `USER`, `LOGNAME`, `SHELL`, `TMPDIR`, `LD_*`, `DYLD_*`, `BASH_ENV`, `ENV`, `CLAUDE_*`,
  `ANTHROPIC_*`, `OPENCODE_*`, `GIT_*`, `SSH_*`, `NODE_OPTIONS`, `PYTHONSTARTUP` (each can redirect where an agent
  talks to or what it runs).
- **Values**: one line, at most 1000 characters; refused when it looks like a key (EXT-2's real secret shapes,
  EXT-1's token prefixes, a password in a URL, a secret query parameter, an `Authorization`/bearer form, a long opaque
  word). A refused value is never stored or printed (argparse's own error never repeats it either).
- **Where**: Claude's `env` (the session and every subprocess it starts); on OpenCode the plugin's
  `shell.create.before` hook sets them for shell commands only (said in the docs).

## Checks and docs

- `check`/`doctor` list each with its roles and state: an agent hook `ok`/`missing`/`changed` (as a lifecycle hook);
  a permission or env extra `ok`, or `failing` with why when it would be refused now (a project's `--private` globs
  changed). `extras list` shows each; a refused value is never shown.
- docs/how-it-fits.md (Extras) and docs/getting-started.md (Adding extras) say what is enforced and what is not.

## Not done here, said

- No live agent run (no agent is started in this lane): Claude's rules are from its docs; OpenCode's config loading
  was run (`debug config`, `debug agents`, standalone, scratch HOME), its policy enforcement is from its docs. lc-hat1
  is the live session check.
