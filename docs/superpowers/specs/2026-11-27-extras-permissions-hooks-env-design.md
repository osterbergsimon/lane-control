# Extras, part 2: agent hooks, deny rules, env values (lc-yw1g, EXT-1b)

The owner approved (2026-11-26) agent hooks, permissions and non-secret env vars as extras. EXT-1 (#84) shipped
skills, MCP servers, personas and plugins and split these three off after its review found they leak: allow rules
reached secrets and `--private` paths (B2), on OpenCode a later allow beat an earlier deny (B3), secrets were accepted
and printed (B6), a changed hook was dropped silently (M2). This note says how each is done so none of that can happen.
It builds on the same store, scopes and writers (core/extras.py) and on EXT-2's pinning, verified copies, safe git and
secret-shape checks (core/hooks.py).

**Review round 1 (2026-11-27, coordinator's decision): deny rules only.** The review ran OpenCode 2.2.1's own
permission engine and found that its grep tool, its shell and a read through a link reach floor paths with no allow at
all (B1, now lc-mpb5), that a Bash allow runs arbitrary code however it is shaped (B2, M4), and that a lane-control
allow on OpenCode lifts denies other files or agents wrote (B3). So `--permission` takes `deny` only; allow extras move
to lc-pwdq, and lane-control writes no allow anywhere. Also from the review: a problem never takes a deny, the floor or
a guard hook out of a folder (B4); secrets are refused in env values and deny rules (B5); a PreToolUse hook fails
closed on a timeout or any runner error (M1, m4); OpenCode matchers name the tools that run (M2); OpenCode's credential
store is in the floor (M3); env refuses names that redirect traffic or code (m1); a guard alone protects its own entry
(m2); the floor follows the XDG folders (m3). The sections below are as they stand after it.

## The three kinds

```
lane-control project|machine extras add <project> ...
  --agent-hook PreToolUse|PostToolUse|SessionStart|Stop [--matcher Tool|Tool|...] [--timeout S] -- <script>
       <script>: an absolute path (pinned by its hash) or <git-url>@<full commit>:<path> (pinned, fetched at add)
  --permission deny <rule>              a Claude deny rule: Tool or Tool(specifier) (an allow is refused: lc-pwdq)
  --env NAME=value                      a non-secret environment value
  --for lanes|coordinator|reviewer|all  as for every extra (default all)
```

Ids: `agent-hook:<name>` (shown with its level, `agent-hook:machine:<name>`; a machine's and a project's both run, as
lifecycle hooks), `permission:deny:<rule>`, `env:<NAME>` (a project's replaces the
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
lane-control creates and owns whole, recorded, git-excluded, never committed, never through a link. On any problem
(a changed hook or MCP file, a refused extra, a foreign or committed file) the folder gets only lane-control's
protections, its deny rules, the floor and every agent hook (whose runner refuses a changed script), and none of what
grants (MCP servers, plugins, skills, env values); a file that cannot be written is left exactly as it is; prepare and
refresh exit 1 and say what failed (`protective`, `KEPT`).

## Deny rules and lane-control's floor

### How each harness decides (read, with what was run)

- **Claude Code** (permissions.md, settings.md; 2.1.332 here): deny, then ask, then allow; the first match in that
  order decides; "an allow rule can't carve an exception out of a deny rule", and "if a tool is denied at any level,
  no other level can allow it". A PreToolUse hook's `allow` does not bypass a deny either. Read/Edit deny rules bind
  the file tools, redirection targets and the file commands Claude recognises, **not** a program that opens files
  itself; a command deny matches the command's text, so the same program written another way is not denied. A
  `/path` rule anchors at the settings file's folder for `--settings`: the coordinator's floor names `//` paths.
- **OpenCode** (v2 permissions.md and policies.md; 2.2.1 here): `permissions` is an ordered list, the last match wins,
  the default allows everything; `policies` with `tool.use` run after every rule and saved approval and only turn
  allow or ask into deny. Each agent's list is its built-in rules, then every config file (global first, project
  last): so a project allow would lift a global or agent deny (B3), and lane-control writes none. **The floor is
  advisory there**: the grep tool asks only for the folder and the regex, the shell scanner only for `cd` targets,
  and a read through a link is checked by the link's name (review r1, against the 2.2.1 engine and code): none
  presents the path a deny names (lc-mpb5).

### What lane-control writes

Beside any deny rule: Claude `permissions.deny` = the owner's denies + **the floor**; OpenCode `permissions` = the
owner's denies, `policies` = every deny again (the owner's and the floor) as `tool.use` deny statements. Never an
allow. Beside agent hooks with no deny rule, the floor's guard part only (the edits below).

The floor (core/agentrules.py `floor`), each under HOME and under the folder the environment moved it to
(`XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`, `CLAUDE_CONFIG_DIR`): read and edit of the secrets and auth
files (`~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.azure`, `~/.kube`, `~/.config/gcloud`, `~/.config/gh`,
`~/.docker/config.json`, `~/.netrc`, `~/.git-credentials`, `~/.npmrc`, `~/.pypirc`, `~/.password-store`, the
keyrings, `~/.claude.json`, Claude's `.credentials.json`, OpenCode's `auth.json` and `opencode.db*` (its credential
and account tables), Codex's `auth.json`), any `.env` and `.env.*` file (templates too: Claude cannot carve them out of
a deny), every `--private` glob (at the main checkout's absolute path, and at any depth anywhere, so in every worktree
wherever Orca put it); edit of the agents' own settings, instructions and extensions (`~/.claude/settings*.json`, its
skills, agents, commands, hooks and plugins; not its own memory and plan files), `~/.config/opencode`, the main
checkout's `.claude/settings.local.json`, the files lane-control writes in that folder, and lane-control's own folders
(`~/.config/lane-control`, `~/.local/share/lane-control`, `~/.local/state/lane-control`, `~/.agents`); and the
commands that print a credential (`gh auth token`, `gh auth status --show-token|-t`, `git credential`, `printenv`, a
bare `env`, `export -p`, `declare -p|-x`, `security find-*-password`, `secret-tool lookup|search`; on OpenCode also
after a wrapper or an assignment).

The tests model each harness's documented decision (`claude_decision`, `opencode_decision`): every floor path and
command stays denied against wide allows from other files, in either order, with machine and project denies mixed, and
lane-control's rules lift no global or agent deny. They prove the rules, not what a tool presents (B1).

### What is refused

An allow, always (lc-pwdq). A deny is never refused for what it covers, only for carrying a secret (best effort:
`secret_text`, below) or for not being a rule. A deny with no OpenCode form (`Agent(model:opus)`) is Claude only, and
add says so; every deny add says the OpenCode floor is advisory. Rules are translated to OpenCode where they map: Bash
to `shell`, Read to `read`, Edit/Write to `edit`, WebFetch(domain:h) to `webfetch` `*://h` and `*://h/*`, `mcp__s__t` to
`s_t`, Skill and Agent to `skill` and `subagent`; Claude's `**` and `*` both become OpenCode's `*` (wider: safe for a
deny).

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
  key. A timeout per hook (default 60 s, at most 600): the runner stops the copy's process group at it; Claude's
  `timeout` (and the OpenCode plugin's) is 30 s past it (`HOOK_MARGIN`), so the runner always answers first.
- **A PreToolUse hook fails closed**: Claude's entry has `onFailure: "block"` (2.1.295 or later; an older Claude lets
  a timed-out or crashed hook through), the plugin stops the call on any exit but 0, and the runner turns a timeout,
  a refused script and any error of its own (a hand-edited store, a crash) into exit 2. The other events report the
  error (exit 1) and go on.
- **A guard protects its own entry**: with agent hooks and no deny rule, the floor's guard part is written (no edit of
  the files lane-control wrote there, the agents' settings, lane-control's folders). A hook is not confined: it runs
  as the owner, may write anywhere and gets SSH_AUTH_SOCK; on OpenCode a shell command can remove the plugin file.
- **A changed hook is never dropped silently**: `check` and `doctor` flag it `changed` (or `missing`); apply
  (refresh, prepare) refuses it as a problem, so prepare and refresh exit 1, but its entry stays in place with every
  other protection; the runner refuses it at every run: a PreToolUse hook blocks the tool call (exit 2) with the
  reason, the other events report the error (exit 1) and go on. A removed hook
  runs nothing: the runner finds no extra and says so.
- **OpenCode**: PreToolUse and PostToolUse map to the plugin hooks `tool.execute.before` (a throw stops the call: the
  same mechanism as lane-away-guard.js, probed on 2.2.1) and `tool.execute.after`, run through the same runner with
  Claude's JSON shape (`tool_name` and `tool_input` are OpenCode's). A matcher is `*` or tool names joined by `|` (no
  regex); its names are mapped to the 2.2.1 tools that really run (`Edit` and `Write` also `patch`, which 2.2.1 offers
  some models instead; `Agent` and `Task` `subagent`), and a name with no OpenCode tool is said at add (a hook with
  none left is Claude only). SessionStart and Stop have no blocking OpenCode equivalent: Claude only, said at add
  (lc-eox9).

## Env values

- **Names** are capitals, digits and `_`. Refused: a secret-looking name (EXT-1's secret words: KEY, TOKEN, SECRET,
  PASSWORD, AUTH, ...), the names lane-control, the harness or the shell run on, and the names that redirect where an
  agent talks to or what it runs (`RESERVED`, `RESERVED_PREFIXES`, `RESERVED_SUFFIXES`): `LANE_*`, `ORCA_*`, `HOME`,
  `XDG_*`, `PATH`, `USER`, `SHELL`, `TMPDIR`, `LD_*`, `DYLD_*`, `BASH_ENV`, `CLAUDE_*`, `ANTHROPIC_*`, `OPENAI_*`,
  `CODEX_*`, `OPENCODE_*`, `GIT_*`, `GH_*`, `SSH_*`, `NODE_*`, `NPM_CONFIG_*`, `PYTHON*`, `DOCKER_*`, `AWS_*`, `OTEL_*`,
  `*_PROXY`, `*_PRELOAD`, `*_BASE_URL`, `*_OPTIONS`, `MAKEFLAGS`, `PERL5OPT`, `RUBYOPT`, `JAVA_TOOL_OPTIONS`, `PAGER`,
  `EDITOR`, the CA bundle variables, ...
- **Values**: one line, at most 1000 characters; refused when it carries a secret (`secret_text`, best effort, also
  run on deny rules): EXT-2's real key shapes and more prefixes (`sk_live_`, `rk_live_`, `whsec_`, `npm_`, `pypi-`,
  ...), a password in a URL, an `Authorization`/bearer form, a secret-named assignment, JSON key, option or query
  parameter given a value (`password=...`, `Pwd=...`, `--token ...`, `"api_key": "..."`, `X-Amz-Signature=`; a
  `${NAME}` reference or a wildcard is fine), `sshpass -p` and `mysql -p<value>`, or a long opaque word. A short
  password after another program's `-p` cannot be told from anything else. A refused value is never stored or printed
  (argparse's own error never repeats it either).
- **Where**: Claude's `env` (the session and every subprocess it starts); on OpenCode the plugin's
  `shell.create.before` hook sets them for shell commands only (said in the docs).

## Checks and docs

- `check`/`doctor` list each with its roles and state: an agent hook `ok`/`missing`/`changed` (as a lifecycle hook);
  a deny or env extra `ok`, or `failing` with why when it would be refused now (an allow stored before this round:
  "remove it"). `extras list` shows each; a refused value is never shown.
- docs/how-it-fits.md (Extras) and docs/getting-started.md (Adding extras) say what is enforced and what is not.

## Not done here, said

- No live agent run (no agent is started in this lane): Claude's rules are from its docs; OpenCode's config loading
  was run (`debug config`, `debug agents`, standalone, scratch HOME), and the review ran its permission engine. lc-hat1
  is the live session check; lc-mpb5 the OpenCode floor (a guard or bwrap); lc-pwdq allow rules.
