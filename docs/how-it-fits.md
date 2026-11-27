# How the parts fit

Every moving part of lane-control on native Orca (the orchestrator in use), in plain words, for an owner who has not
read the code. "Desktop" is your workstation with the Orca app. A "box" is a machine you reach over SSH. Every script
has one line in [the Scripts table](../core/README.md#scripts). **Pending** means an open pull request or an open
issue: not on main yet.

## Who does what

```
YOU      talk to a coordinator in its Orca terminal; say "merge"; sign agents in; say yes to a box's link (box only)
         or from any Orca terminal: lane-control coordinator start|restart|status, lane new|list

AGENTS   machine coordinator   one per machine, optional   splits the budget, watches memory and load
         project coordinator   one per project             queues, briefs, opens lanes, reviews, merges on your word
         lane                  one per issue               own worktree and branch, ends in a pull request

SETUP    lane-control machine setup   once per machine   machine coordinator, 4 automations, CI limits timer
         lane-control project setup   once per project   lane tools, skills, personas, tier table, coordinator
                                                          (lane-control project refresh updates them)
         lane-control project|machine extras              your skills, MCP servers, personas, plugins, agent hooks,
                                                          permission rules and env values for the agents (refresh
                                                          puts them in place), and your lifecycle hooks (scripts
                                                          lane-control runs)

LANE     lane-room   may another lane open?        lane-tier     which model does this tier use?
TOOLS    lane-heavy  one gate for heavy builds     design-shots  screenshots of a UI
         lane-limits.sh   the limits behind lane-room and lane-heavy (a file the tools read, not a command)

BOX      optional. A coordinator that lives on the box can keep working while the desktop is off.
         BOTH TUNNELS START ON THE DESKTOP (it only needs to ssh out to the box).
  direction 1: the desktop controls the box
      tunnel   orca-tunnel-<host> (ssh -L), made by orca-host-install --pair
      you      no click in Orca, no pairing code
  direction 2: the box's coordinator starts lanes on the desktop
      tunnel   lane-tunnel@<host> (ssh -R), made by lane-control project setup before it sends the box its link
      you      say yes once per box: setup makes the link with orca pair (the desktop then listens on every
               interface); or --pair-by-hand: make the share link in Orca and paste it (keeps it on loopback)

RUNNING  night manager       Orca automation    checks in on idle coordinators when you are away or it is night
         lane sweep          Orca automation    reports merged, parked, stuck and broken lanes
         machine check       Orca automation    tells the machine coordinator when budget, memory or runners change
         away watchdog       Orca automation    tells a stalled away-improve coordinator, then the machine coordinator
         ci-presence         systemd timer      sets CI's CPU and memory limits by whether you are there
         orca-tunnel-<host>  systemd (desktop)  carries direction 1
         lane-tunnel@<host>  systemd (desktop)  carries direction 2
         orcad               on the box         Orca's server there (unit orcad.service when systemd allows)
         "Delete merged workspaces"             Orca's own rule that removes a merged lane's worktree; you turn it on
```

More: the sections below; [Getting started](getting-started.md).

## The three layers

- **Machine coordinator** (optional, one per machine). Judgment only: splits the budget across projects, decides which
  lane yields when memory is short, passes your orders on. It runs no project work. It is an agent in its own private
  folder, in an Orca terminal titled `machine coordinator`.
- **Project coordinator** (one per project). You talk to it in the terminal titled `<project> coordinator`. It turns
  your asks into issues, picks a tier, opens lanes, checks what comes back and merges only on your word. The one
  exception is the ratchet (unattended improvement), which you switch on yourself.
- **Lane**: one worker for one issue, with its own worktree and branch. It opens a pull request and reports once.
  Reviewers (code, and design for anything a user sees) are workers too. A lane for a failing test or a bug gets the
  debugger persona.

A **tier** (swift, standard, deep, top) is a role. Each project's tier table says which engine and model fill it; a
tier set to `none` is skipped.

What each agent reads: a **persona** (its standing instructions, in `orchestrators/native-orca/personas/`, copied into
the project by setup) and **skills** (how-to pages it loads when needed). The lane skills are `native-orca-lanes`
(the coordinator's gate, lane, report, review and close), `away-improve` (the away-improve cycle) and
`component-gallery` (a UI lane's gallery and screenshots). `orca-cotal-lanes` belongs to the parked Orca + Cotal setup.

More: [roles](../core/roles.md), [model routing](../core/model-routing.md), [native Orca setup](../orchestrators/native-orca/setup.md).

## Where lane-control lives on a machine

| What | Where | Who puts it there |
|---|---|---|
| The checkout: scripts, templates, docs | `~/code/lane-control` (any folder works) | you, `git clone`; update with `git pull` |
| The installed tree, the one everything uses: the repo at one commit | `~/.local/share/lane-control/<sha>/` and the `current` link to it (old trees kept for running lanes) | `lane-control install`, and `lane-control project setup` or `lane-control machine setup` run from the main checkout on its default branch at a newer commit (anywhere else they keep it, unless you say yes) |
| Links to the lane tools and `lane-control`, which agents call | `~/.agents/bin/`, links into `.../lane-control/current/core/scripts/` | `lane-control install`, `lane-control project setup` |
| Skills (the three lane skills and the design skills) | `~/.agents/skills/`, linked for Claude and OpenCode |
`lane-control project setup`, `--refresh` |
| One project's settings and tier table, **outside its repo** | `~/.config/lane-control/projects/<folder>-<hash>/` (`settings.json`, `lanes.tsv`) | `lane-control project setup`; `--show` prints them |
| This machine's settings and state | `~/.config/lane-control/`: `machine.conf`, `limits.sh` (optional, yours), the away file, away-improve's files, `hosts/<host>.json`, `tunnels/<host>.env` | `lane-control machine setup`, you, `lane-away`, `lane-control ratchet`, `lane-control project setup`, `lane-tunnel` |
| The model environment (optional, for a gateway) | the one source `~/.config/lane-control/model-env.conf`, and from it `model-env.sh`, `~/.config/fish/conf.d/lane-control-models.fish` and the `env` block of Claude's user settings (what it wrote there is noted in `model-env.written.json`) | `model-env`, only when you run it |
| Budget config and weekly ceiling | `~/.config/rove/playbook.conf.sh` (an old folder name, still the default) and the ceiling file it names | you ([getting started](getting-started.md), step 2.2) |
| In a project's repo | `AGENTS.lane-control.md` plus one pointer line in `AGENTS.md`, and the lane files in `.lane-control/`; the rendered personas there are not committed | `lane-control project setup`, committing only on your yes |
| Beads: issues and memories | a private repo `<project>-beads` in `~/code/`; the project's `.beads` is a git-ignored link to it | you, or `lane-control project setup` on your yes |
| On a box | the desktop's installed tree at the same sha, in the box's own `~/.local/share/lane-control/<sha>/` and `current` | `lane-control project setup <repo> <host>`, on every run (kept when the box has that sha) |

Personas name `.../lane-control/current`, never a checkout or a lane worktree. A `git pull` changes nothing until
`lane-control install`, or a setup or `--refresh` from that main checkout, moves `current` and with it every tool
link at once. A setup from a lane worktree, another branch, or a commit not shown to be newer keeps the installed tree
and runs that tree's own setup (`lane-control: kept`), unless you say yes. The skills and each project's
personas follow at `lane-control project refresh <project>`, which records the installed sha in the project's settings.
`lane-control status` compares the checkout with `current`; `lane-control doctor` lists every project, link, skill and
box that is behind and what to run; `lane-control prune` removes old trees that no running process uses and no hook,
persona, unit or link names, and nothing without a valid `current` (a dry run unless `--apply`).

More: [core and its scripts](../core/README.md), [where beads lives](../core/issues.md#where-beads-lives-backup-and-restore-lc-5c1),
[updating an existing host](getting-started.md#updating-an-existing-host).

## Models

No setup script has a built-in model for the machine coordinator or the automations: you name them once (a flag,
`machine.conf` or an answer in the terminal) and a re-run keeps them. An automation's own record keeps its model unless
you pass `--replace-automation-model`; a new automation takes the model the existing records agree on. Before a model
is written, a gateway in use is asked what it serves (`<base>/v1/models`): a name it does not list is not written, and
one it cannot confirm is written only on your yes in a terminal (`--no-model-check` skips the check). For Claude through a gateway, **`model-env`** keeps the
model variables in one source, writes the shell file, the fish file and Claude's settings `env` from it, including the
auto-mode classifier model (no default), and `model-env --check` compares every place that sets them. It never edits a
line you wrote, and never writes a key. A running agent keeps the environment it was born with.

More: [the model environment](../orchestrators/native-orca/setup.md#the-model-environment-claude-through-a-gateway).

## The lane tools

- **`lane-room`**: "is there room for another lane?" It looks at free memory, CPU contention and a count of running
  lane agents. A coordinator runs it before every lane; if it says no, the lane waits and you are told. The agent count
  only sees Orca + Cotal agents today, so on native Orca memory and CPU are what hold a lane (pending: lc-sq0).
- **`lane-heavy <command>`**: every build and full test suite goes through it. Only a few run at once on the whole
  machine; the rest wait. CI jobs have their own slots.
- **`lane-tier <table> <tier>`**: looks a tier up in a project's tier table and prints the engine and model.
- **`lane-limits.sh`**: works out the limits `lane-room` and `lane-heavy` use, from the machine's size, whether you are
  there, and your optional `limits.sh`.
- **`design-shots`**: headless screenshots at phone and desktop width, light and dark, and a before/after page.

More: [machine caps](../core/machine.md#machine-caps), [the limits example](../core/limits.example.sh).

## Setup

- **`lane-control machine setup`** (once per machine; `--host <host>` runs it on a box): writes `machine.conf` (you fill
  in the projects), checks the usage feed, starts the machine coordinator, creates the four automations, reports Orca's
  cleanup rules and installs the `ci-presence` timer.
- **`lane-control project setup <project>`** (once per project): onboards the repo (an adapting agent and a reviewer write the lane
  files; committed only on your yes), installs the main checkout's newer last commit as the machine's tree (as `lane-control
  install` does; elsewhere it keeps the installed one and runs its setup), links the lane tools, installs the skills, renders the personas, writes the tier table
  and starts the coordinator. Today onboarding a new project needs Claude. Onboarding on OpenCode is parked, so do not
  expect it soon: PR #66 was parked after review round 7, with findings still open.
- **`lane-control project refresh <project>`**: after lane-control changed. Updates the tools, skills and personas,
  keeps the tier table and engines and starts nothing. It commits nothing, with one exception, only on your yes: moving
  a retired design lead's additions into `DESIGN.md`. Restart the coordinator (`lane-control project stop`, then
  `start`) so it reads the new persona.
- Both say `exists` for what is already done, so running them again is safe.
- **`lane-control coordinator start <project>`** (or `machine`; `--on <host>` runs it on a box): starts a coordinator
  only when the live check says it is not running. `cannot tell` starts nothing and says what it found; `--force` is
  your way past that when you can tell what the check cannot (a person only: it refuses when an agent may run it; it
  asks once, and the project's settings store records how it was confirmed), never past a coordinator that runs.
  `lane-control coordinator restart <project>` asks the running one by Orca message to get ready, and closes and
  starts it only on its own `ready to stop <code>` reply; `status` says which state it is in.
- **`lane-control lane new <project> <issue>`** (`--tier`, `--note`; a person only): one Orca message to the
  project's running coordinator, a request for a lane for that issue. It is no approval: the coordinator asks you in
  its terminal, then gates, briefs, reviews and merges it as any lane; nothing is started directly. `lane-control lane list <project>` (and `lane status`)
  reads Orca's worker list: lane, issue, PR, CI, idle time.
- **`lane-control project check <repo> <host>`**: run it before a box setup. It lists every prerequisite, says what is
  missing or could not be checked, and changes nothing. A listening port is not proof: it also checks each tunnel
  unit's state, restart counter and socket owner, that the box runs one Orca server (and which), and the box's SHELL
  ([remote.md](../orchestrators/native-orca/remote.md#a-listening-port-is-not-proof-lc-l78)).

More: [getting started](getting-started.md), [native Orca setup](../orchestrators/native-orca/setup.md).

## Extras

What you add to the agents' environments, in one place (`core/extras.py`, `core/scripts/lane-extras`):

```
lane-control project extras add|list|remove <project> ...   this project's
lane-control machine extras add|list|remove ...             every project on this machine (a project's add to these)
  --skill <git-url>@<commit>[:<path>]                        a skill, pinned at a full commit
  --mcp <name> [--env NAME]... -- <command> [args]           an MCP server; --env names a secret, never its value
  --persona <name> --prompt-file <path> [--tier T] [--harness H]   a lane role the coordinator can brief a lane with
  --plugin claude:<plugin>@<marketplace>@<version> | opencode:<package>@<version>
  --hook <event> --script <path | git-url@commit:path> [--timeout S]   a lifecycle hook (below)
  --agent-hook PreToolUse|PostToolUse|SessionStart|Stop [--matcher T|T] [--timeout S] -- <path | git-url@commit:path>
                                                             the agent's own hook, your script (below)
  --permission allow|deny <rule>                             a Claude rule; lane-control's own denies stay (below)
  --env NAME=value                                           a value that is not a secret
  --for lanes|coordinator|reviewer|all                       who gets it (default all; not for a hook)
```

- **Stored** in the project's settings store (`~/.config/lane-control/projects/<key>/extras.json`, beside its tier
  table) or `~/.config/lane-control/extras.json`. Never in a project repo, and never a secret. An MCP server gets
  its secret from the agent's own environment: `--env NAME`, or `${NAME}` in an argument (`${NAME}` in Claude's
  `.mcp.json`, `{env:NAME}` in OpenCode's config). Refused, without the value ever being shown: a URL with a password
  or token in it, a token-like or long opaque argument, a secret-named option, variable, header (`X-API-Key: ...`,
  `Cookie: ...`), query parameter (`?access-token=...`) or JSON key (`{"apiKey": "..."}`) given a value, an
  `Authorization` or bearer header, a `--env` that is not a NAME.
- **Pinned:** a skill at a full commit (its checkout is checked against it), a file an MCP command names by its hash:
  the command, an argument, or a path inside one (`--opt=<path>`, `-I<path>`). A relative path is refused (it would
  resolve in the lane's folder), and so is a path inside any project checkout, as given or through a link
  (`--allow-repo-path` takes one anyway, with a warning). What an interpreter finds by itself (`python3 -m x`,
  `npx x`) is not pinned.
- **Who adds them:** `add` and `remove` are a person's only, at a terminal; an agent is refused, as for `lane new`.
- **Each role's extras go only where that role's sessions read them:**

```
              coordinator                                    lane or reviewer (its own worktree, by lane prepare)
Claude        files in the settings store given at launch:  .mcp.json; .claude/settings.json (enabledMcpjsonServers:
              --mcp-config, --settings (enabledPlugins),     only the servers lane-control wrote there; enabledPlugins;
              --plugin-dir (its skills)                      .claude/settings.local.json when the project commits it);
                                                             .claude/skills/<name>
OpenCode      the main checkout's .opencode/opencode.json    .opencode/opencode.json (mcp, plugin) and
              (mcp, plugin) and .opencode/skills/<name>      .opencode/skills/<name>
```

  Nothing goes in the main checkout's `.claude/` or `.mcp.json`: Claude reads the main checkout's
  `.claude/settings.local.json`, and its `.claude/skills`, from every linked worktree. OpenCode reads `.opencode/`
  from the session's own worktree up to its root, and Orca's lane worktrees are not under the main checkout.
  `lane-control project refresh` (and setup) writes the coordinator's (it loads them at its next start: refresh says
  when it must restart) and renders each persona into `.lane-control/personas/<name>.md`. Orca starts a lane or
  reviewer with no settings of our own, so the coordinator's persona gets a part about the project's extras, and before
  such a worker starts the coordinator runs `lane-control lane prepare <project> <worktree> --role lanes|reviewer`. A
  reviewer shares its lane's worktree, so both then have both roles' extras, and a lane that runs beside it can still
  change what is in its worktree. A lane on another host gets none. Once prepare puts a skill in a worktree's
  `.claude/skills/`, Claude no longer reads the main checkout's own `.claude/skills` there (it does only when the
  worktree has none): keep a skill lanes need committed, or add it as an extra. A project skill with a default design
  skill's name may lose to the user's copy in Claude (add warns; turn the default off to be sure).
- **Written safely:** in a folder, lane-control writes only files it creates and owns whole: never a committed file,
  never one that was there before or changed since it wrote it, never through a symlink. Applying is all or
  nothing: on any problem the folder keeps none of lane-control's extras, prepare exits 1 (the coordinator holds the
  lane) and refresh exits 1 after doing the rest. Refresh also applies every worktree prepare wrote in again, so a
  removed extra is taken out there too, and each file it removes is named. The repo's git exclude names exactly the
  files lane-control has written.
- **The design skills** (`core/design-skills.tsv`) are the default machine extras, installed for the whole user as
  before (`~/.agents/skills`, and `~/.claude/skills` unless Claude has the skill's plugin). `lane-control machine
  extras remove <name>` turns one off. An owner's pins of skills live apart, under `~/.agents/extras-sources/`.
- **Checked** by `lane-control project check <project>` and `lane-control doctor`: every extra with its roles, and
  `missing` (not fetched, not installed), `changed` (a pinned checkout, file or plugin version differs), `failing` (an
  MCP command not found, or the coordinator cannot get its extras now, with why) or `not applied` (refresh has not
  run yet), each with what to run.
- Setup's onboarding agents run with no MCP server and no settings files, so they get no extras.

### Permission rules, agent hooks and env values

The same store, roles and files (lc-yw1g; `core/agentrules.py`, design in
[the spec](superpowers/specs/2026-11-27-extras-permissions-hooks-env-design.md)). Claude's go in the session's
settings (the coordinator's launch `--settings` file, a lane's `.claude/settings.json`); OpenCode's in
`.opencode/opencode.json` (`permissions`, `policies`) and `.opencode/plugins/lane-control-extras.js` (env values and
agent hooks).

- **A floor no allow can reach.** With any permission rule, lane-control writes its own denies beside it: reading or
  editing the secret and auth files (`~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/gh`, `~/.netrc`,
  `~/.git-credentials`, Claude's `.credentials.json`, OpenCode's and Codex's `auth.json`, ...), any `.env` or
  `.env.*` (templates too), every `--private` path; editing the agents' own settings, instructions and extensions
  (`~/.claude/settings*.json`, its skills, agents, hooks and plugins, `~/.claude.json`, `~/.config/opencode`, the main checkout's `.claude/settings.local.json`, the files lane-control wrote in that
  folder) and lane-control's folders; and the commands that print a credential (`gh auth token`, `git credential`,
  `printenv`, ...). Claude takes a deny over any allow from any file. On OpenCode, where the last matching rule wins,
  every deny comes after every allow, and every deny (yours and the floor) is also a `tool.use` policy, which no
  rule, saved approval or later allow lifts. The tests prove both against each harness's documented rules, with
  wildcards, either add order and machine and project rules mixed.
- **Refused at add, and again at refresh and prepare** (a project's `--private` paths may change; a machine allow is
  checked against every project's): a bare or wildcard tool (`Bash`, `Read`, `Edit(*)`); a Bash allow with a
  wildcard anywhere but one at the end, without a subcommand before it (`Bash(git *)`), with a shell operator, a
  program by path, or a program that can read any file or run any command by itself (shells, interpreters, file
  readers and copiers, network tools, `env`, `sudo`, `xargs`, `npx`, `docker`, `git -c`, `git config`, ...), or one
  overlapping a floor command; a file allow that is not a plain path inside the session's folder (no `/`, `//`, `~`,
  `..`, dot-file or leading wildcard) or that overlaps a `--private` path either way; a rule with a key or password
  in it (never shown). Not enforced, and said at add: what an allowed program reads by itself (`make test`, `npm
  run build` run the project's own code, which a lane can change): Claude's Read denies do not reach a program's own
  reads.
- **A deny** is never refused for what it covers. One with no OpenCode form (`Agent(model:opus)`) is Claude only,
  and add says so. Rules are translated: Bash to `shell`, Read to `read`, Edit and Write to `edit`, WebFetch's domain
  to `webfetch` URLs, `mcp__server__tool` to `server_tool`; Claude's `**` and `*` both become OpenCode's `*`.
- **Agent hooks** are your script only, pinned as a lifecycle hook is (an absolute path outside every checkout, by
  its hash, or a file at a full commit), never from a repo, never with arguments. The session runs lane-control's
  runner (`core/scripts/lane-agent-hook`), never the script: it reads the hook from your store, checks it and runs a
  private copy made just before, in the session's folder, with the event's JSON on stdin and its output and exit code
  passed back, and with EXT-2's short environment (`PATH`, `HOME`, `USER`, locale, XDG folders, `TMPDIR`,
  `SSH_AUTH_SOCK`), `CLAUDE_PROJECT_DIR` and `LANE_AGENT_HOOK_EVENT`, `_NAME`, `_HARNESS`: no token or key of the
  agent's. A timeout per hook (default 60 s). A machine's and a project's of the same name both run.
  A changed or missing script is never dropped: `check` and `doctor` flag it, refresh and prepare refuse it (the
  folder keeps none of lane-control's extras, prepare exits 1), and a session that still has it is refused at run: a
  PreToolUse hook blocks the call, the other events report the error. On OpenCode, PreToolUse and PostToolUse run
  at its tool hooks (a refusal stops the call); SessionStart and Stop are Claude only (add says so). `--matcher` is
  `*` or tool names joined by `|`.
- **Env values** are never a secret: a secret-looking name, a value shaped like a key, token, password in a URL or
  auth header, or a long opaque word is refused and never stored or shown; so is a name lane-control, the harness or
  the shell runs on (`LANE_*`, `ORCA_*`, `HOME`, `XDG_*`, `PATH`, `CLAUDE_*`, `ANTHROPIC_*`, `GIT_*`, `SSH_*`, `LD_*`,
  ...). Claude sets them for the session and what it starts; OpenCode's plugin for its shell commands only.
- Not run against a live agent here: Claude's rules are read from its documentation (2.1.332); OpenCode 2.2.1 loads
  the files (`opencode debug config` and `debug agents`, standalone, in a scratch HOME), and its policy enforcement
  is from its documentation. A live session check is lc-hat1.
- Checked without starting an agent: Claude's rules above are read from its code (2.1.332) and documentation;
  OpenCode 2.2.1's `debug config` and skill list show a worktree reading only its own `.opencode/opencode.json` and
  `.opencode/skills/`, and the main checkout only its own. A live session check is lc-hat1.

### Hooks

Your own scripts, which lane-control runs at five moments (`core/hooks.py`; lc-76o). They are extras of kind `hook`,
added, listed and removed like the others. A hook never takes another's place: the machine's run for every project,
before the project's own, even with the same name (gates: every one must pass). Each is shown and recorded with its
level, `hook:machine:<name>` or `hook:project:<name>`; one name is one event per level (add refuses it for another).

```
event        when                                                where it fires                       a failure
after-setup  the end of setup or refresh                         project-setup                        setup/refresh exit 1
lane-start   a lane's worktree is ready, before its agent        lane-control lane prepare --role     prepare exits 1:
             starts                                              lanes (the coordinator)              hold the lane
gate         before a merge: checks that must be green           lane-control project gate <p> <PR>   not green: no merge
                                                                 (the coordinator); away-merge        away-merge refuses
after-merge  after the merge                                     lane-control project hook <p>        warns only
                                                                 after-merge; away-merge
on-report    a lane's report; the away watchdog's escalation     lane-control project hook <p>        warns only
                                                                 on-report; away-watchdog --send
```

- **Never from a project repo.** Only what you add runs: an absolute path outside every project checkout (as given or
  through a link; `--allow-repo-path` takes one anyway, with a warning), pinned by its hash, or a file in a git repo
  pinned at a full commit, fetched at add (and by refresh) and checked against its commit and hash. A link is
  resolved at add: its target is what is pinned and run (add says so), and once the link leads elsewhere the hook is
  `changed` and refused until it is added again. A missing or changed script does not run: it
  is `refused` (for a gate: not green) and `check` and `doctor` flag it `missing` or `changed`, with what to do.
  Every hook runs from a private copy made and checked just before: a path hook's bytes that matched its hash; a git
  hook's whole pinned commit, written out by lane-control itself from the commit's objects, each one hashed against
  its id from the pinned commit down, with git run on no config but the cache's own, no replace refs, attributes,
  filters or hooks, and none of the caller's variables. Nothing written, ignored, replaced or rewritten in the cache
  counts (a rewritten object is refused), and what the hook writes beside itself is gone after the run. A repo with a
  link that leads out of it is refused at add.
- **Facts, never secrets.** No arguments, stdin closed. The environment is a short list (`PATH`, `HOME`, `USER`, the
  locale, the XDG folders, `TMPDIR`, `SSH_AUTH_SOCK`) and the facts as `LANE_HOOK_<FACT>`: `EVENT`, `NAME`, `SOURCE`,
  `PROJECT`, `PROJECT_ROOT` and, where the event has them, `PR`, `HEAD_SHA`, `MERGE_SHA`, `LANE`, `ISSUE`, `OUTCOME`,
  `WORKTREE`, `ROLE`, `SETUP` (`setup` or `refresh`), and `REPO`, the checkout of the repo the PR is in (the
  project's own, or an attached repo's with `--repo`). `OUTCOME` is a report's status (`succeeded`, `failed`,
  `blocked`, `partial`, `NEEDS_STANDARD`, `NEEDS_DEEP`) or `escalation`. Each fact is checked: a number, a full sha
  and a word against their shape, a name or a path only for the real shapes of a key (so `task-tracker` or
  `fix-disk-usage` is fine). None of the caller's other variables gets through, so no token or key does. A hook that
  needs a secret reads it itself.
- **Where it runs.** With your HOME, in a fresh empty folder of its own under `~/.local/state/lane-control/hooks/`,
  removed after: never in a lane's worktree or a checkout (the facts say where they are).
- **A timeout** per hook (`--timeout`, default 600 s). Each hook runs in its own process group: at its timeout, when
  it ends, or when the command running it is stopped (TERM, HUP, INT), the whole group is stopped. A child that
  leaves the group (`setsid`) is not. What the command printed before (away-merge's merge line, the watchdog's sends)
  is out before any hook runs.
- **Shown:** each hook's exit code, its time and the last 20 lines of its output, with escape sequences and control
  and format characters (C0, C1, bidi) taken out. Best effort, a mask, not a promise: the real shapes of a token, a
  password in a URL, an `Authorization` header, a secret-named key or option given a value (`password=...`,
  `DB_PASSWORD: ...`, `--token ...`, `"client_secret": "..."`) and a long opaque word (a sha lane-control passed in
  is kept) become `[redacted]`. A hook must not print secrets.
- **A broken blocking hook:** the failure says the way past it, which is the owner's: fix the script and add it
  again, or `lane-control project|machine extras remove hook:<name>` (a machine hook is every project's), or, for one
  run, `--skip-hook <name>` (or `machine:<name>`, `project:<name>`) `--reason "<why>"` on `project gate`, `project hook` or `lane prepare`: a person at a
  terminal only (an agent is refused), recorded in `~/.local/state/lane-control/hooks/skips.jsonl`.
- **The coordinator** runs `gate` right before a merge and the after-merge hooks once after it, and the on-report
  hooks when it takes a report (its persona and the `native-orca-lanes` skill say when); with no hooks they answer at
  once, green. In the ratchet `away-merge` runs the gate and after-merge hooks itself, and reads the PR again after
  the gates: a base, head, review, body or CI that changed meanwhile refuses the merge. A lane-start hook adds the
  prepare step to the coordinator's persona at the next refresh.
- **Orca's own features do not fit here** (read on 1.4.274): its automations start an agent from a prompt, on a
  schedule or a GitHub, Slack or Linear event (`pr_merged` among them, through Orca's GitHub app), never a script, and
  cannot hold a merge; `orca.yaml`'s `scripts.setup` is the project repo's own committed file, run inside each new
  worktree (lane-control writes it for the beads link); `orca agent hooks` are Orca's agent-status hooks.

## A box: two directions

`lane-control project setup <repo> <host>` sets up both. They are separate connections with opposite roles. Both tunnels
are `ssh` processes **on the desktop**, so the box never has to reach the desktop's network.

**Why there are two.** In Orca's own model the coordinator runs on your computer (the client) and starts workers on
paired servers with `--on`. Direction 1 covers that. lane-control also lets a project's coordinator **live on its
box**, so that it keeps working while the desktop is off or unreachable. Such a coordinator still needs the desktop
for some lanes, and to start them the box must be a client of the desktop: that is direction 2. The owner decided
(2026-11-25) that direction 2 stays, as a supported, first-class path and not a fallback: "coordinator lives on box
for just that reason. direction 2 must stay."

While the desktop is off, the box's coordinator and the lanes on the box can go on; lanes on the desktop need it on
and the tunnel up. For the box's Orca to outlive your ssh logins, turn on lingering for the box's user (`loginctl
enable-linger`, on the box). Setup does not do that, and systemd normally stops a user's services after their last
login ends without it. A run with the desktop off has not been tested.

```
              DESKTOP                                              BOX

direction 1: the desktop controls the box
   Orca app (client)                                    orcad = Orca's server (server)
        |                                                    listens on the box's 127.0.0.1
        '--> 127.0.0.1:<port> ==[ orca-tunnel-<host>, ssh -L ]==> 127.0.0.1:<port>

direction 2: the box's coordinator starts lanes on the desktop
   Orca app (server)                                    Orca on the box (client)
   listens on 127.0.0.1                                      |
   127.0.0.1:<port> <==[ lane-tunnel@<host>, ssh -R ]== 127.0.0.1:<box port> <--'
```

| | Direction 1 | Direction 2 |
|---|---|---|
| What it is for | you watch and drive the box's coordinator and lanes in your Orca window | the box gets what it lacks (graphics, a browser, a GPU): its coordinator starts a lane on the desktop |
| Server (is dialled) | the box's `orcad` | the desktop's Orca app |
| Client (dials) | the desktop's Orca | the box's Orca |
| Tunnel | `orca-tunnel-<host>` (`ssh -L`), made by `orca-host-install --pair` | `lane-tunnel@<host>` (`ssh -R`), made by `lane-control project setup` before the link is sent |
| systemd units | desktop: `orca-tunnel-<host>.service`; box: `orcad.service` when it has a user manager, else a detached process | desktop: `lane-tunnel@<host>.service` |
| Who makes the pairing code | the box's `orcad`, by itself; `orca-host-install` reads it over ssh | the desktop's Orca: `lane-control project setup` runs `orca pair` after your yes; or **you**, by hand (`--pair-by-hand`) |
| Who saves it | `orca-host-install` gives it to `orca environment add` on the desktop on stdin | `lane-control project setup` sends it on ssh's stdin; the box runs `environment add --pairing-code -` |

**Direction 1 is automatic.** `orchestrators/orca-cotal/scripts/orca-host-install <host> --pair` (`lane-control project
setup` runs it when the box's server does not answer yet) copies `orcad` and its Node runtime from the desktop's own
Orca install, so the box downloads nothing, and checks Node's checksum. It starts `orcad` on the box's 127.0.0.1, opens
the `-L` tunnel, reads the box's pairing code and adds the box to your Orca. No click and no code from you. The script
sits in the parked Orca + Cotal folder, but native Orca uses it. Without `--pair` it only puts the files on the box, for
Orca's own Settings > SSH connect to reuse (useful where the box has no internet); that route makes no
`orca-tunnel-<host>`.

**Direction 2 needs one yes from you**, once per box. The owner decided (2026-11-25): "we do it with share link, with
--on-client as fallback, but must make sure to make it as painless as possible for user", and on the field report's
recipe (2026-11-26): "if we can handle automatically its much smoother for user". So by default `lane-control project setup` makes
the link itself: `orca pair --address 127.0.0.1:<box port> --rotate --json` on the desktop, held in memory, sent to the box on
ssh's stdin, saved there by `environment add --pairing-code -` through `core/scripts/pairing-code-stdin.cjs`, then
proved with the same reads `--check` makes. It passes `--rotate`, so the box never gets an unused link made earlier
for someone else; that also ends every other unused access link of this Orca. Before it asks, it says what the link
grants (the box can open terminals here, so it can run any command as you, and such a command can reach your screen;
Orca itself will not drive the screen for it), what `--rotate` ends, that the desktop's Orca will then listen on
**every network interface** (or that it already does), and how to revoke it: `--remove-host`, then the "Runtime"
entry of that date in Settings > Servers. Revoking does not narrow the listener; restarting Orca does, when no other
used network pairing remains. After, it prints the bind and the revoke steps.
`core/scripts/lane-tunnel` opens the `-R` tunnel first, which makes the desktop's Orca port appear on the box's
127.0.0.1, and the link names that address. A box already paired is not paired again; a box that has this desktop
under an older name is reported with the remove-then-add steps (a box keeps one name per desktop).

**The loopback option.** Answer no, or pass `--pair-by-hand`, to keep the desktop on 127.0.0.1: in the desktop's Orca,
Settings > Servers > "Share this computer", choosing **127.0.0.1**, then paste that link at `lane-control project
setup`'s hidden prompt. Only the window makes a link that does not widen the listener. Press Enter at the prompt, or
pass `--no-desktop-lanes`, to skip direction 2. The two ends can use different ports (`--tunnel-remote-port` in
`lane-control project setup`, `--remote-port` in `lane-tunnel`). Either link lets the box run commands as you on the
desktop until you revoke it in the same Settings page.

**The fallback.** When neither link can be made, setup says so and points to the hand-off with `send --on-client`
(Orca 1.4.272 or later), documented in [remote.md](../orchestrators/native-orca/remote.md#the-fallback-send---on-client).
**It is not a drop-in:** it needs direction 1 in place and the desktop connected, it mails a bot on the desktop, and a
person approves it once. It reaches a bot, not a lane, so it can ask the desktop for work but cannot start a supervised
lane there ([pairing research](research/2026-11-25-orca-pairing.md), section 6; not run there). lane-control has not
built it, and setup does not fall back to it by itself. What each route grants, ends and leaves open:
[pairing facts](../orchestrators/native-orca/remote.md#pairing-facts).

**After a reboot or an upgrade:** `orca-host-install <host> --tunnel` brings direction 1's unit back without pairing
again; `lane-tunnel start` with the same options brings direction 2's back. Neither tunnel's ssh rides your own ssh
ControlMaster (`ControlPath=none`), and `lane-control doctor` says when a unit flaps, a box runs two Orca servers, or a
box's user manager kept an old SHELL after a `chsh`.

**The rest of what you do by hand for a box:** run `ssh <host>` once so its host key is known; run `lane-control project
check <repo> <host>`; make sure ssh can log in without asking anything (for a smartcard or agent key, `lane-tunnel` has
extra options; `orca-tunnel-<host>` has none); sign the agents in on the box; sign `gh` in there and let git use it, for
beads; answer setup's yes/no questions, the link's among them. Removing: `lane-control project remove-host <host>`
undoes direction 2 only, prints how to drop direction 1, and you revoke the link.

**Is `lane-tunnel` still needed?** This is about how direction 2 is carried; direction 2 itself stays. The tunnel is not
needed when the box can reach the desktop directly (same LAN or Tailscale) **and** you accept the desktop's Orca
listening on every network interface, not only on the address you picked. It is needed when the desktop must stay on
127.0.0.1 or only outbound ssh gets through. `lane-control project setup` itself always sets the tunnel up: the link it
makes names 127.0.0.1, and it refuses a hand link with any other address. Details: [Do you need the
tunnel?](../orchestrators/native-orca/remote.md#do-you-need-the-tunnel).

**How far this is tested, and what is pending:**
- On main, the native Orca trial ran both with containers: a container as the box, and a second Orca server standing in
  for the desktop. The desktop app's own share link and a lane started from a box's coordinator were not run
  ([record](../orchestrators/native-orca/record.md), NO-6). The docs on main say the no-tunnel route is not run.
- A container testbed exists in PR #68 (`tests/testbed/`, not on main). It was stood down on 2026-11-25. Its results
  are not verified yet, so this page does not rely on them.
- The link setup makes (`orca pair`) was measured working by a remote site on two servers at the same version (field
  report, 2026-11-26); here it is tested with stubs only. That it makes the desktop listen on every interface is read
  from Orca's source ([pairing research](research/2026-11-25-orca-pairing.md)). The link is a credential, and the
  address in it is what the box dials, not what the desktop listens on:
  [pairing facts](../orchestrators/native-orca/remote.md#pairing-facts).
- A box coordinator's lane on the desktop wakes it with its report, with both Orcas at the same version (1.4.274):
  measured in the field (field report 2026-11-26), not repeated here. A desktop coordinator's lane on a box is not
  measured. A report that is lost or never sent is left to the night manager's idle check, Orca's stopped-worker notice
  where the build has it, and otherwise you ([reports across hosts](../orchestrators/native-orca/remote.md#reports-across-hosts)).

More: [native Orca on an SSH host](../orchestrators/native-orca/remote.md).

## Automations

`lane-control machine setup` creates four **Orca automations** in the machine coordinator's folder. Each first runs a
**precheck**, a plain script that spends no model tokens; an agent starts only when the precheck finds something.

| Automation | Runs | Precheck | What its agent does | Why Orca 1.4.274 does not make it unnecessary |
|---|---|---|---|---|
| `night manager` | hourly | `night-precheck` | sends a check-in to each coordinator that is due: idle while you are away or it is night (and, day or night, one that the ratchet covers); passes on offline CI runners | no Orca built-in wakes an idle coordinator, or checks budget steps or runners |
| `lane sweep` | every 3 hours | `lane-sweep --precheck` (new findings only) | sends each new finding to that lane's coordinator; removes nothing | Orca tells a coordinator about its supervised workers; the sweep also sees lane worktrees with no live worker |
| `machine check` | every 30 minutes | `machine-check` (by hand, read only: `lane-control machine report`) | passes changes (budget step, memory, runners, a coordinator Orca cannot find) to the machine coordinator | Orca has no budget steps, no runner state and no "memory low" warning |
| `away watchdog` | every 15 minutes | `away-watchdog` (finds a stalled away-improve cycle and writes the messages, with their evidence, to an outbox; a daily summary) | sends them with `away-watchdog --send` from its own terminal (a precheck has none), to the coordinator, then the machine coordinator; puts what reached nobody in front of you | Orca's worker notices (silent 10 min, idle 60 min, exited) go to the coordinator, which is the one that may be stuck; the schedule is Orca's own |

They are already Orca automations, so there is nothing of ours to move onto that feature. The reasons come from the
[overlap audit](research/2026-11-25-orca-overlap.md), rows 8 to 12 and 18. It read Orca's help, guides, release notes
and source; no row was verified by a run. It also says:

- **`STUCK` reports (pending, lc-cnm):** the sweep's stuck report, and the night manager repeating it, may give way to
  Orca's own worker notices for supervised workers, after a trial. Today both still report it.
- **Removing merged lanes (pending, lc-93i):** Orca's cleanup rule does the everyday removal. `lane-sweep --apply` is
  your manual bulk fallback; no automation runs it. The plan is that it removes only lanes Orca's rules did not take.
  **Not built yet:** today `--apply` removes any merged lane that passes its own checks, and also a clean, quiet lane
  whose open pull request already holds everything in it.

Two other things run by themselves and are not Orca automations: **`ci-presence`**, a systemd user timer (every 15
minutes and at boot) that sets CI's CPU and memory limits by whether you are there, and the tunnels above.

Tell the machine you leave or return with `lane-away on|off|status`. It changes limits and allows night check-ins; it
never starts the ratchet.

More: [night manager](../orchestrators/native-orca/night-manager.md), [a machine](../orchestrators/native-orca/setup.md#a-machine).

## Budgets

- A **metered budget** has a usage feed (how much is used) and a weekly **ceiling** you set. Steps count down from it:
  at ceiling-15 a notice, -7 no new work, -4 push and write handovers, -2 stop. A short window (such as 5 hours) pauses
  new work at 95 % and asks lanes to push at 98 %.
- **`budget-state`** prints each budget's step now. Coordinators check the budget before a lane; the night manager's and
  machine check's prechecks run it. With no usage feed at all there are no steps and the machine's caps are the limit.
- **`window-guard`** is a daemon that sends budget and low-memory orders by itself. It sends them through Cotal, so it
  works only on the parked Orca + Cotal setup (`window-guard-install` installs it). On native Orca nothing like it
  runs: the machine check and the coordinators do this.

More: [budget steps](../core/model-routing.md#budget-thresholds-per-metered-budget-ceiling--the-owners-weekly-percent),
[usage feeds](../core/machine.md#usage-feeds-per-metered-budget).

## The ratchet

Off unless **you** switch it on (`lane-control ratchet on`). Each chosen project's coordinator then keeps improving its
product on one `away/<date>` branch, merging there only through `away-merge`, and keeps one draft pull request for you.
Nothing reaches the main line without your word. `lane-control ratchet off` ends it; running work finishes first. The
`away watchdog` automation watches each cycle from outside and tells a stalled coordinator, with the evidence, then the
machine coordinator; it merges and changes nothing.

More: [the ratchet, the owner's guide](away-improve.md).

## Cleanup

- **Orca removes a merged lane's worktree**, with its own rule "Delete merged workspaces". Orca's rules are off by
  default and you turn them on; setup reports what it can tell and never changes Orca's settings. "Delete released
  orchestration worker workspaces" is recommended beside it.
- The rules are each Orca server's own. The app's Settings page does not set a box's global rules, so give each
  project on a box its own rule ([rules are per server](orca-cleanup.md#rules-are-per-server)).
- GitHub's "Automatically delete head branches" removes the remote branch; `lane-control project setup` offers to turn
  it on.
- When Orca does not take a merged lane, the coordinator removes it by hand, after checks.
- `lane-sweep` reports what is left over. Without `--apply` it removes nothing.

More: [lane cleanup](orca-cleanup.md).

## Parked parts

Orca + Cotal, Rove and wsp are parked and herdr is untested. Their scripts are still in the repo, marked parked in the
[Scripts table](../core/README.md#scripts). This page does not describe them.

More: [orchestrators](../orchestrators/README.md), [switching a project](../orchestrators/switching.md).
