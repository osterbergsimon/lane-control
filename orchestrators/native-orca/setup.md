# native Orca: setup

Orca's own orchestration (runs, tasks, workers, messages), with no Cotal. Status and trial results: `record.md`.
Pin the Orca version you tested: Orca ships often and its orchestration commands change (`record.md`).
Where Orca has a feature that does the job, use it rather than adding lane-control code (README, Doctrines, "Use Orca
where we can"; what overlaps today: `docs/research/2026-11-25-orca-overlap.md`).

## Install
- The Orca desktop app on the workstation (on Linux its CLI is `orca-ide`; plain `orca` may be another program).
  Agents run as a normal user, never root.
- Always: beads (`bd`, bd 1.0.4 or newer: `machine-setup` checks it and says how to fix it, never installs it), `gh` (authenticated), `jq`, `git`, bash, and the agent harnesses you use (Claude Code),
  signed in once as the user the agents run as.
- lane-control itself, installed once per machine: `scripts/lane-control install` from a checkout lays its last commit
  at `~/.local/share/lane-control/<sha>/` with a `current` link (`project-setup` does it too when run from a
  checkout; `core/README.md`, "The install"). Personas name `.../lane-control/current`, never the checkout.
- The machine caps: `~/.agents/bin/lane-heavy` and `lane-room` (links into `.../lane-control/current/core/scripts/`;
  `project-setup` and `lane-control install` make them, with `lane-limits.sh`, `lane-tier`, `design-shots` and
  `lane-control`).
- The lane skill, `skills/native-orca-lanes` (gate, lane, messages, report shape, review, close): `project-setup`
  installs it with the caps in `~/.agents/skills/native-orca-lanes` (with the lane brief as its `brief.md`), linked
  into `~/.claude/skills` and `~/.config/opencode/skills`; `--refresh` puts back a changed or missing file. Orca's
  own `orchestration` guide (`orca-ide skills get orchestration`) stays the command reference.
- Check: `orca-ide orchestration --help` lists the commands below.

## A project
`scripts/orch setup <project> --orchestrator native-orca` opens a PR that adds a short section
(`templates/agents-section.md`) to the project's `AGENTS.lane-control.md`, with one pointer line to it in AGENTS.md:
how the coordinator works on Orca's orchestration, the heavy-work rule and where the handovers live. The project's own
facts stay in its AGENTS.md (the policy: `../README.md`, "What lane-control writes into a project").

`project-setup` also keeps the coordinator findable and honest. The main checkout's `CLAUDE.local.md` gets a **persona
anchor** (`core/anchor.py`): a session started there by hand is told to read the persona; workers and headless runs are
told to ignore it. Other lines in the file are kept and it is never committed. Where the folder has an `AGENTS.md` and
no `CLAUDE.md`, the anchor imports it, because Claude would otherwise stop reading it. `project-setup --check <repo>`
says whether the persona and anchor are as setup writes them. Whether the coordinator **runs** is decided by its
processes, never by a terminal's title or Orca's agent mark (both stay on an empty shell, as after a server restart).
Three answers: running (left alone); `recorded but dead` (`project-pause start` clears the record and starts it; the
old terminal is left open for you to close); and `cannot tell`, when an agent that may be it cannot be read or placed.
After an Orca restore, a coordinator can run as `claude --resume <id>` in a sandbox whose environment lane-control
cannot read: it is ruled out only when its command line names another session or Claude's transcript for that id is
under other folders alone. `cannot tell` starts nothing, so there is never a second coordinator; if the terminal it
names is an empty shell, close it and run again.

## Beads
Where a project's beads lives, its backup and its restore: `core/issues.md`, "Where beads lives". `project-setup` checks
the layout, offers the move of a plain `.beads` folder or the restore on a new machine on your yes (a terminal), adds
`bd prime` as the session hook next to the persona hook, and names a failing committed beads hook without editing it.
`--beads-repo OWNER/NAME` overrides the beads repo's name and is kept in the settings store. On a box, setup says
when `gh` needs a sign-in (a fine-grained token limited to the beads repos) and never handles the token.

## Lane cleanup
A merged lane's worktree is removed by Orca's own cleanup rule "Delete merged workspaces", not by a script of ours
(`docs/orca-cleanup.md`). `project-setup` and `machine-setup` read `orca-ide workspace cleanup preview` and say
plainly when no rule is on or the project has cleanup off, with where to turn it on in Orca (Settings -> Workspace
Cleanup -> Cleanup rules); they also report the recommended rule "Delete released orchestration worker workspaces"
(lane-control lanes run as supervised workers; Orca removes a released worker's workspace only once Git finds its HEAD
in the default branch or a merged review, so an unmerged lane's worktree stays), and say "cannot tell" when the preview
shows no row it matched; they never change Orca's settings. `project-setup` also turns on GitHub's "Automatically
delete head branches" in a terminal (recommended: Enter, y or yes turns it on, anything else leaves it off; without a
terminal it only says so; a re-run says `exists`).

## How a coordinator works
- **Run and tasks:** the coordinator makes a run and its tasks (with dependencies), then starts every ready task
  itself: a run never schedules work.
- **Workers:** `orca-ide orchestration worker-start` starts a worker in its own worktree and branch, with its brief as
  the first message. Reusing a kept-open terminal is much faster than a fresh one; release a worker after its
  merge.
- **Messages:** `send`, `ask`, `reply` and `check` (a coordinator never uses `check --wait`: waits are lane work).
  A message reaches the agent as a one-line prompt in its composer telling it to run `check`; the content shows only
  after it does. If a worker that also coordinates its own run seems not to get a reply, nudge it to run `check`
  (`record.md`, point 3).
- **Nesting:** a worker may not start its own worker until the owner raises Settings > Orchestration > Nested worker
  depth (default 1). Prefer one coordinator level, or raise the depth knowingly.
- **Between sessions:** never type into another session's terminal (`orca-ide terminal send`): it disturbs what the
  owner types (owner, 2026-11-19). Claude sessions on one machine message each other with SendMessage; everything
  else on this machine goes to the peer's own Run (`orca-ide orchestration run-create` from its own terminal, then
  `send --to run:<id>`). A peer on another host has no `run:` route (without `--on` it goes to this Orca; with `--on` the server refuses it).
  Check that both servers are 1.4.256 or newer (`runtime.appVersion` in `orca-ide status --json` and in `orca-ide status
  --environment <environment> --json`), take the peer's handle from `orca-ide terminal list --environment <environment>
  --json` (the terminal whose `title` is its session name), and `orca-ide orchestration send --on <environment> --to
  term_<id> --subject "<subject>" --body "<message>"`. If either server is older, or no terminal has that title, the owner
  carries the message. This route has run only between scratch servers (`remote.md`, Messages to a session on the other
  host).

## Heavy work
Every build and full test suite runs through `~/.agents/bin/lane-heavy <command>`, one machine-wide gate for every
project and lane (`core/machine.md`); open a lane only when `~/.agents/bin/lane-room` exits 0. A project can route
its own heavy targets (a CI script, a make target) through `lane-heavy` so every caller gets the gate.

## An SSH host
`core/scripts/project-setup <repo> <host>` sets a project up on an SSH box in two directions, each with its own
tunnel, both started on this machine (`remote.md`; the picture:
[how the parts fit](../../docs/how-it-fits.md#a-box-two-directions)):
- direction 1, this machine controls the box: `orca-tunnel-<host>` (`ssh -L`), made by `orca-host-install --pair`,
  with no click and no pairing code from the owner;
- direction 2, the box's coordinator starts lanes here: `lane-tunnel@<host>` (`ssh -R`), made by `project-setup`
  before it sends the box its link to this machine's Orca: made with `orca pair` after the owner's yes, or the share
  link the owner makes by hand (`--pair-by-hand`, which keeps this machine on loopback). `project-setup --remove-host
  <host>` undoes this direction only.

## OpenCode on a self-hosted LiteLLM gateway
A self-hosted gateway with any models behind it is first class, and nothing is refused by model or harness (owner
2026-11-24, `core/model-routing.md`, "Sandbox per tier"); each project's own tier table says which models it uses.
0. A host set up before this change still has the old copies: `~/.agents/bin/lane-tier`, the lane skills and the
   rendered personas there refuse an OpenCode tier until they are refreshed. Update the host's lane-control checkout
   and install it (`git pull && scripts/lane-control install`), run `core/scripts/project-setup <repo> --refresh`
   from it for each project (it links the lane tools, reinstalls the skills
   and re-renders the personas), then restart the coordinator (`core/scripts/project-pause stop <repo>`, then `start <repo>`), so it
   reads the new persona. The suggestion below warns while the installed lane-tier is not the checkout's.
1. OpenCode's own config on the machine that runs the agents (`~/.config/opencode/opencode.json`) names the gateway
   as a provider, with the key as a reference to an environment variable, never the key itself:
   ```json
   { "provider": { "litellm": { "npm": "@ai-sdk/openai-compatible",
       "options": { "baseURL": "http://127.0.0.1:4000/v1", "apiKey": "{env:LITELLM_API_KEY}" },
       "models": { "chat": {}, "code": {} } } } }
   ```
   Set the variable where the agents start (the session that starts Orca). The gateway's aliases are then
   `litellm/chat` in OpenCode, a tier row of harness `opencode` and model `litellm/chat`, and
   `opencode/litellm/chat` for project-setup's `--coordinator` and `--reviewer`. A lane on such a row starts on a
   worktree made first, with `orca-ide orchestration worker-start --worktree path:<that worktree> --agent opencode
   --model litellm/chat` (the `native-orca-lanes` skill has the commands and the checks); it needs the `opencode`
   command, 2.0.14 or newer.
2. `core/scripts/project-setup <repo> --suggest-tiers litellm` reads the gateway's `/v1/models` and, where it answers
   (LiteLLM), `/model/info`, which gives the model behind each alias; it shows each alias with that model and a
   suggested table (swift, standard, deep from name hints; top `none`; rows `opencode/<provider>/<alias>`). It leaves
   out, and names, a model whose LiteLLM `model_info.mode` is not chat (an embedding model, a reranker) and, where
   OpenCode's config lists the provider's models (`provider.<id>.models`), an alias it does not list, since OpenCode
   starts only those. The base URL is `--gateway-url <url>` (the same flag the onboarding agents use; `--gateway` is
   its old name), else the project's stored gateway (the one onboarding keeps), else the provider's `baseURL` in
   OpenCode's config; the key is `--gateway-key-env NAME`, else the variable kept with that stored gateway, else the
   one the provider's `{env:NAME}` names, each only with the URL it goes with: a `--gateway-url` that is neither
   takes no key along (`gateway: using <url>, no key`). A key written in the config itself is never used (it stops
   and says so). OpenCode's auth
   files are never read, and the key is never printed, logged, stored or passed as an argument; it goes to the URL
   given only: no redirect is followed and no proxy is used. In a terminal it asks before it writes the table to the project's settings store; on no,
   or without a terminal, nothing is written: edit the table it printed and pass it with `--tiers <file>`. A later
   setup run keeps the table.
3. A machine without Claude: `core/scripts/machine-setup --machine-coordinator opencode/litellm/chat` (the
   automations follow the machine coordinator's harness; `--automation-provider opencode` says it outright). The
   four automations run OpenCode v2 on OpenCode's own default model (`"model"` in its config), since Orca passes no
   model to an OpenCode automation. With no usage feed (every budget's feed empty in the playbook config) no budget
   is metered: the caps are the limit, and night check-ins still go out.
4. Setting a project up on such a machine: the onboarding agents (the adapting agent and its reviewer) run on either
   harness. There are three routes for a new project; pick one before the first setup run:
   - **A model on a gateway you run (trusted gateway).** `core/scripts/project-setup <repo> --adapter
     opencode/litellm/code --adapt-reviewer opencode/litellm/deep --coordinator opencode/litellm/chat --reviewer
     opencode/litellm/deep --gateway-url http://127.0.0.1:4000/v1 --gateway-key-env LITELLM_API_KEY --trust-gateway`.
     The onboarding agents read the repo itself: no scratch copy, no private-path filtering. Before any agent runs it
     prints a warning that says what the model will see (every file in the folder, ignored and private ones included:
     `.env` files, keys, `.git` with its remotes and history, what `--private` names, and what a link there points to;
     all of it goes to the gateway and on to whatever the gateway passes it to) and that this is meant for a model on a
     gateway you run, then asks once, `Run them in <repo> itself, every file readable? [y/N]`; without a terminal the
     flag is the yes. A no runs nothing and exits 3. `--trust-gateway` is not kept: give it on each run that should
     use it. It is refused with `--skip-adapt`, `--refresh`, `--show` and `--suggest-tiers`, and does nothing for
     Claude roles (it says so).
   - **A hosted model you do not control (the default).** The same command without `--trust-gateway`: each agent works
     in a filtered scratch copy, below.
   - **No usable model on this machine yet.** `core/scripts/project-setup <repo> --skip-adapt --coordinator
     opencode/litellm/chat --reviewer opencode/litellm/deep`: no onboarding agent runs, so `.lane-control/` and
     `AGENTS.lane-control.md` are not written (`adapt: skipped ...` says so), and the rest of the setup (Orca, the
     coordinator) runs as usual; the coordinator works without the project's lane files. Adapt it later, once a model
     is at hand: `core/scripts/project-setup <repo> --readapt --adapter ... --adapt-reviewer ...`, which runs the
     onboarding as a first setup would.

   What both OpenCode routes share. The defaults stay `claude/sonnet` and `claude/opus`; `--adapter` and
   `--adapt-reviewer` are kept in the settings store (`models.adapter`, `models.adapt-reviewer`) once given, and a run
   on a default says so; only a harness a role uses must be on PATH, so a box without Claude needs no `claude`. Each
   agent runs `opencode2 run --standalone --model lane-gateway/<model> --agent lane-onboard-<random> --format json`,
   the prompt on stdin, with the same prompts, checks and review round as Claude, and with OpenCode starting from
   nothing (`opencode_agent`): HOME and the XDG folders are a scratch folder of the run's own, so no config, plugin,
   agent, formatter, LSP, MCP server, auth or session of yours is loaded (an owner formatter made OpenCode run
   JavaScript from the project after a write: seen live), and the session stays in that folder. No OpenCode config of
   yours is read for the run: an earlier version copied the provider's block from it and kept resolving it differently
   from OpenCode (merge order, the `providers` key, `{env:}` inside a URL), so that the prompt, the project and the key
   could reach another host, a vendor's API among them (each seen live). Its one config file is written fresh from
   explicit values:
   - one provider, with the id `lane-gateway` whatever the `<provider>` of `opencode/<provider>/<model>` is: OpenCode
     has built-in behaviour for some provider ids (with `opencode` or `github-copilot` it sent a side request for a
     model of its own, `gpt-6-luna`: seen live), so the `<provider>` part does not reach the run; two onboarding agents
     with the same `<model>` are the same model at the one gateway and are refused. npm `@ai-sdk/openai-compatible`,
     the one model `<model>` as the gateway names it, and OpenCode's title agent pinned to it, so no side request names
     another model;
   - `baseURL`, the `--gateway-url`. It must be written out plainly, since OpenCode reads a URL with a WHATWG parser and
     Python does not: `http://<host>` or `https://<host>`, a host of ASCII letters, digits, `.`, `_` and `-` (an IDN as punycode)
     or a bracketed IPv6 address, an optional port and a plain path; no `{env:...}`, no user or password, no `%`,
     backslash, other character, query or fragment (a vendor's host name with a trailing dot, a percent-encoded
     letter, a backslash before a suffix that reads as a gateway's name, an ideographic full stop or an invisible
     character in it each read as one host here and as the vendor's to OpenCode: seen live). Then known vendors' public APIs are
     refused by name (OpenAI, Anthropic, Azure OpenAI and AI Foundry, Google, Mistral, Groq, DeepSeek, xAI, OpenRouter,
     Together, Fireworks, AWS, Cohere, Perplexity, opencode.ai, GitHub Models and Copilot, NVIDIA, Cerebras, DeepInfra,
     Hugging Face, Vercel, Cloudflare, Moonshot, Alibaba, Z.ai, SambaNova, Replicate, Ollama's cloud and others); that
     does not prove a host is self-hosted, so `gateway: using <url>, key from $NAME` (or `no key`) is printed before
     the first agent, to read. The gateway must not redirect: OpenCode follows a 307 or 308 and sends the same request,
     prompt included, to wherever it points (seen live), and project-setup cannot switch that off. Plain `http://<host>`
     is for loopback or a network you trust; the line says so for any other host;
   - `apiKey` `"{env:LANE_GATEWAY_KEY}"` when `--gateway-key-env NAME` is given: the run's environment holds the
     key under `LANE_GATEWAY_KEY` only, never under your variable's name, since OpenCode reads some names itself
     (`OPENCODE_API_KEY` went to opencode.ai as its own service's key: seen live). Left out with no key
     (`--gateway-key-env ''`, or never given). `--gateway-key-env` takes a variable's name: a value that is not one is
     refused without being shown, and a name is kept only once that variable is set in this shell (so a key pasted
     there is neither printed nor stored). The URL and its key variable are one setting, kept as `gateway` in the
     settings store (never the key): a `--gateway-url` other than the stored one does not take the stored key variable
     along unless you name it with `--gateway-key-env` or say yes when asked in a terminal; otherwise it is dropped,
     and said (`gateway: key dropped ...`). No headers (a gateway that needs them is not supported yet, bd lc-5cr), no
     other provider; formatter, LSP, MCP and plugins off; and the run's agent and rules.

   The run's environment is PATH, locale, TLS and proxy settings and `LANE_GATEWAY_KEY`, nothing else of yours, and no
   model catalog is fetched (`OPENCODE_DISABLE_MODELS_FETCH=1`). OpenCode also loads a system config folder whatever
   HOME is (`/etc/opencode`; `/Library/Application Support/opencode` on macOS; `%ProgramData%\opencode` on Windows),
   with no switch to keep it out: when that folder exists the run refuses to start and names it. The run's rules:
   every tool off, then read on, and edit only on the adapter's exact paths, each template as `.lane-control/<its
   name>` or `.lane-control/additions/<its name>` and `.lane-control/SUGGESTIONS.md`, only for the adapter, so no
   shell, web, grep, glob, sub-agent or path outside the folder, for an agent whose name is new each run. The
   project's own OpenCode config and plugins are not loaded (`OPENCODE_DISABLE_PROJECT_CONFIG=1`), nor Claude Code's
   (`OPENCODE_DISABLE_CLAUDE_CODE=1`). On both harnesses, any of git's own names under `.lane-control/` (`.git`,
   `HEAD`, `commondir`, `gitdir`, `objects`, `refs`, any case, folder or file) stops the run before and after each
   agent, before any git command can go in: git would run a command its config names. A run that uses all its steps
   (80 for the adapter, 60 for the reviewer) fails as a Claude run out of turns does, and so does one that exits 0
   with no answer and no tool call. `--refresh` runs no agent and needs no Claude either.

   Writes, with `--trust-gateway`: the agent runs in the repo, so its writes are checked by the repo's state before
   and after each run (every path, `.git` and ignored files included, links never followed; in `.git` only what git
   rewrites on any status or fetch, objects, refs, logs, the index and lock files, is left out, so a config, hook or
   info file counts). Only new or changed plain files at the adapter's paths, and the folders they are in, may
   change; anything else (a `.git` or dot-name write, another file, a link, a deletion, any write by the reviewer)
   stops the run with the paths named, also after a failed run. It is in the repo by then: nothing is committed, and
   `git status` shows what to put back. Before the adapter runs, a link or a non-folder on the way to its files stops
   it, so no write goes through a link out of the repo.

   The scratch copy, without `--trust-gateway` (`opencode_export`, mode 700, removed after the run). It is made its
   own empty git repo (no history, remote or hooks) before anything goes in, since OpenCode takes the nearest git
   work tree as the project: a copy under a TMPDIR inside some other repo (a HOME kept in git) would otherwise give
   the agent all of that repo (seen live). A TMPDIR inside the project's repo is refused (in both routes: the run's
   HOME is made there too). Deny rules alone did not hold on OpenCode: it matches the path it is asked for, never what
   a symlink leads to; its wildcard is not git's; and it attaches an `AGENTS.md` by itself, past every read rule, when
   it reads in or lists that folder (each seen live). So the copy holds only what the agents may see: the tracked
   files (git's index, read from the repo's root, checked-out submodules included, with what is on disk now) and
   onboarding's own new files (`.lane-control/`, `AGENTS.lane-control.md`), minus `.env*` files, a tracked file a
   `.gitignore` line matches, and everything a `--private` glob covers. The globs are matched by project-setup with
   fnmatch from the repo root, as ignore lines are (a name alone covers a folder and all below it, `dir/`, each `**/`
   as a folder or none, `[^...]`), never by git, so no `.gitignore` negation, leading `#` or working folder changes
   them; they err towards covering more (a test checks each form against git's own matching); a POSIX class (`[:`
   anywhere: `[[:digit:]]`, `[![:alpha:]]`) and a `./`, `//` or `/./` spelling are refused, on both harnesses, with
   the way to write it. A glob may not cover the adapter's files, `AGENTS.lane-control.md`, `AGENTS.md` or the file it
   links to: a review that cannot see them proves nothing, and the reviewer's prompt holds AGENTS.md's diff. The copy
   has no `.git`, no ignored file and no symlink: a link to a file that is itself in the copy comes as that file's
   content (`AGENTS.md -> CLAUDE.md`), every other link is left out, and so is a file below a folder that has become a
   link. Untracked files, untracked embedded repos and anything ignored are not in it; a repo committed as a gitlink
   is copied like a submodule, its tracked files only, also with no `.gitmodules` entry (mark it `--private` if it
   should stay out). After a run that succeeded (a failed, empty or cut-off one brings nothing back) only new or
   changed plain files at the adapter's own paths, and only from the adapter, are copied back; anything else that
   changed (a file elsewhere, a dot-name or `.git`, a folder of its own, a deletion, a link, a link or a folder in the
   repo where a file goes, a file in the repo the copy left out (ignored or private) that it would replace, any write
   by the reviewer) stops the run with one line, checked before anything is written, and nothing is copied or made;
   the files go beside their places first and are all put in place at the end, so an error leaves none (a role file
   over a rendered persona on native Orca becomes that role's additions, as on Claude).

   The opt-in live check (real opencode2, a fake model on 127.0.0.1; a proxy that lets nothing through logs any request
   past the given gateway; for the copy, every request is searched for a marker in each private file; the owner's
   config there turns formatters, LSP, an MCP server, a plugin and instructions on, and the project has prettier and a
   prettier config that would leave a marker): `LANE_LIVE_OPENCODE=1 python3 -m unittest discover -s tests -p
   test_project_setup_native.py -k OpenCodeOnboardingLiveTests`. It covers both routes, each with a negative control
   (the copy's filter switched off lets a marker through; the trusted run's edit rules widened let an `AGENTS.md`
   write land, and the before/after check stops it), the key under `OPENCODE_API_KEY` (and, as a control, handed over
   under that name: opencode.ai is asked) and the side model under built-in provider ids (and, as a control, under the
   given id with no title model: another model is asked).

The URL, the key's variable and the model names stay in OpenCode's config and the project's settings store: no key,
host or gateway URL in any repo.

## The model environment (Claude through a gateway)
What lane-control writes itself: each role's model in the project's settings store and tier table, the persona and the
start command (`claude --model <model>`), and each automation's `--model` in its record. In a checkout's
`.claude/settings.local.json` it writes hooks and away-improve's deny rules, never a model and no `env` block. The
model environment is written by one tool, and only when you run it:

```bash
core/scripts/model-env --set ANTHROPIC_BASE_URL=<gateway URL> \
  --set ANTHROPIC_DEFAULT_SONNET_MODEL=<name> --set CLAUDE_CODE_AUTO_MODE_MODEL=<name>
core/scripts/model-env --check
```

Or run it with no arguments in a terminal and answer its questions. On a box, run it there.

- **One source**, `~/.config/lane-control/model-env.conf`: `NAME=value` lines (mode 0600). It takes
  `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_MODEL`, `ANTHROPIC_DEFAULT_{FABLE,OPUS,SONNET,HAIKU}_MODEL`,
  `ANTHROPIC_SMALL_FAST_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`, `CLAUDE_CODE_AUTO_MODE_MODEL` and
  `CLAUDE_CODE_DISABLE_THINKING`. **Never a key or a token** (it refuses them): the key stays where your shell or
  login starts agents.
- **Three homes written from it:** `~/.config/lane-control/model-env.sh` (add one `. <that file>` line to your shell
  profile; no startup file is ever edited), `~/.config/fish/conf.d/lane-control-models.fish` (when `~/.config/fish`
  exists) and the `env` block of Claude's user settings (`$CLAUDE_CONFIG_DIR/settings.json`, else
  `~/.claude/settings.json`). A file or a value it did not write, or one edited since, is reported and left as it is.
- **`--check`** compares each home with the source, value by value, and changes nothing: those three, hand-written
  lines in shell startup files and `~/.config/environment.d`, the `env` blocks of set-up projects' Claude settings, the
  systemd user manager's environment and the shell it runs in. Exit 1 when something differs, is missing or only a
  shell could work out.
- **A running agent keeps the environment it was born with.** After a change, restart each agent session, and whatever
  starts them (Orca, its server on a box) when it had the old values from a shell or the systemd user manager.

**The auto-mode classifier model is set explicitly** (field report 2026-11-26: a session died mid-turn with a 400
naming the classifier's model group). With a gateway, `model-env` writes nothing until
`CLAUDE_CODE_AUTO_MODE_MODEL` names one, and has no default. Give it a **non-reasoning** model your gateway serves, by
its own name (Claude Code 2.1.302 or newer reads it). Claude Code's docs say it ignores the variable for Claude Opus 5,
Sonnet 5.5 and Opus 5.5 (`model-env` warns), and that when your provider cannot serve the model, auto mode denies each
action it checks; hence the served-model check. Whether an alias reasons is something only you know: lane-control can
warn from its name and no more. To see which model reviewed, their docs give `claude --permission-mode auto
--debug-file <log>` and `classifier_request_started` in that log. **A classifier model configured by the server wins
over the variable:** Claude Code 2.1.332 then ignores it and logs `ignoring CLAUDE_CODE_AUTO_MODE_MODEL`.

`CLAUDE_CODE_BG_CLASSIFIER_MODEL` is **not** set or checked. Claude Code 2.1.329 to 2.1.332 declare the name, but in
those four binaries no code that reads its value was found (`CLAUDE_CODE_AUTO_MODE_MODEL`, the control, is read), and
their docs do not name it. In 2.1.332's code, the classifier of a background session's state asks the small fast model
with thinking off: `ANTHROPIC_SMALL_FAST_MODEL` when set, else usually `ANTHROPIC_DEFAULT_HAIKU_MODEL`, both of which
`model-env` takes. A server setting can change that, and none of this was run live. Look again after a Claude Code
update.

**A `--model` with a Claude name turns extended thinking on, and some gateway backends reject it** (the field report).
Claude Code decides from the model's name which request fields to send. Its gateway docs also say that on an
Anthropic-format gateway it treats a name it does not recognise, a gateway alias too, as a current model that gets the
thinking field, so renaming alone is not proof. If a backend answers 400 naming `thinking`, their compatibility switch
is `CLAUDE_CODE_DISABLE_THINKING=1` (`model-env --set` takes it); on a model that thinks by default it may still think.

**The settings `env` block may not reach the process** (the field report saw it not arrive). It is Claude Code that
reads it, so it reaches Claude Code and what Claude Code starts: not the terminal it was started from, not Orca, not
OpenCode. Its docs also list cases where the launch environment wins (sessions started by the desktop app or a
self-hosted runner), and variables a project's settings cannot set. That is why the same values also go in the shell
files, and why `--check` looks at every place rather than at one.

**The served-model check.** Before a model is written (`model-env`, `machine-setup`'s coordinator and automation
models, `project-setup`'s role pins and tier table), the gateway it goes through is asked once for
`<base>/v1/models`. **Nothing is written blindly:**

| The check says | Meaning | What happens |
|---|---|---|
| `ok` | the gateway lists the name | written |
| `not served` | the gateway answered and does not list it | not written |
| `could not check` | no answer, a refused key, a bare alias such as `opus` with no `ANTHROPIC_DEFAULT_OPUS_MODEL`, a paged list, a key that is for another URL | in a terminal you are asked, once per run for the same doubt, and only `y` writes; without a terminal, not written |
| `not proven` | only a wildcard in the list matches | the same |

`--no-model-check` writes without asking the gateway. A script or a box setup with no terminal needs that flag, or the
doubt fixed: for an alias, name its model first with `model-env --set ANTHROPIC_DEFAULT_<ALIAS>_MODEL=<name>`.
`ok` is not proof that the model works: only a request to it is. With no gateway nothing is asked.

**Which URL and which key.** For Claude the URL is `ANTHROPIC_BASE_URL` from `model-env`'s source (or the value being
written), else your Claude user settings, else the shell; never a repo's settings. For OpenCode it is the provider's
`baseURL` in OpenCode's config. The key comes only from the environment (`ANTHROPIC_AUTH_TOKEN` or
`ANTHROPIC_API_KEY`; for OpenCode the variable that same config names), goes in a header to that URL only, and is never
printed or stored. **When the shell names an `ANTHROPIC_BASE_URL` of its own and it is not the URL to be asked, the key
is not sent** (nothing is) and the check says `could not check`: to move to another gateway, run the command with its
URL and its key in the shell. A shell with a key and no URL of its own sends it to the URL above, as Claude Code would.

## A machine
`core/scripts/machine-setup [--host <h>]` sets a machine up once, here or on an SSH box (its header has the details;
every step says `exists` on a re-run).

**No model is built in.** The machine coordinator's: `--machine-coordinator claude/<model>` (or
`opencode/<provider>/<model>`), else `MACHINE_COORDINATOR=` in `machine.conf`, else what its persona already names,
else it asks in a terminal. The automations' (only when Claude runs them): `--automation-model <model>`, else
`AUTOMATION_MODEL=`; with neither, a new automation takes the model the existing Claude automations' records agree on
(so a machine that has them is not asked again), and where they name none or differ it is asked in a terminal. A re-run
never changes the model an existing
automation's record names; `--automation-model <model> --replace-automation-model` does, and changes only the
`--model` in its extra arguments. Without a terminal and without a model it stops and says what to write;
`machine.conf` is already there to write it in.

- `~/.config/lane-control/machine.conf` from `core/templates/machine.conf.example`: this machine's coordinators
  (`<name> <checkout>` each: the owner's list of whom the night manager may wake; Orca gives each one's harness and
  session name, the single agent terminal in that checkout, and a harness and terminal title written after the checkout
  override it), the runner repos it watches, night hours, idle and recheck minutes, and
  how long `lane-away on` lasts. Fill it in by hand; no value from it goes in a repo. A machine set up before
  `machine-setup` existed has no `machine.conf`: create it (run `machine-setup`, which writes it from the example, or
  copy the example there by hand), since `lane-improve` and `night-precheck` need the file.
- The machine coordinator, in its own private folder (`--folder`, default `~/machine-coordinator`), started in an
  Orca terminal titled `machine coordinator` with its persona and hook, unless it runs already or that cannot be told
  (above: the same proof). The folder's `CLAUDE.local.md` gets the same persona anchor, which the automations' sessions
  there are told to ignore; `machine-setup --check` reads it. The folder may be your own git repo: git does not see
  the file (listed in `.git/info/exclude` unless already ignored; a committed one is left alone).
- Four Orca automations in that folder, each behind a precheck that costs nothing when nothing is due: `night
  manager` hourly (`night-precheck`; the agent follows `night-manager.md`), `lane sweep` every 3 hours
  (`lane-sweep --precheck`, report only, waking its agent only for findings new since the last run) and `machine check` every 30 minutes (`machine-check`: a budget step,
  memory or runner state changed; it also keeps the `budget-stopped` marker for the CI limits) and `away watchdog` every 15
  minutes (`away-watchdog`: while away-improve mode is on its precheck finds a stalled cycle, and its agent sends the
  messages to that coordinator and the machine coordinator from its own terminal; [the watchdog](../../docs/away-improve.md#the-watchdog)).
- The `ci-presence` user timer: the CI slice's CPU and memory follow presence (`lane-limits.sh`).
- On a machine set up by hand before: automations named `night-manager` or `lane-sweep` and an older CI timer
  (`ci-cpu`) are reported with the commands to remove them; nothing is doubled or replaced without the owner.
- The usage feed check: without `~/.claude/usage-history.jsonl` written in the last hour, budget steps are unknown
  and night check-ins stay off (the night precheck says so once a night), unless no budget in the playbook config has
  a feed (only a self-hosted gateway, say): then there are no steps, the caps are the limit and check-ins go on.

The owner says they leave or return with `core/scripts/lane-away on|off|status` (or asks the machine coordinator);
`lane-away on --until-back` has no 14 h expiry, until `lane-away off`.
A coordinator gets night check-ins only when it was started with `LANE_ROLE`/`LANE_PERSONA` (project-setup does
this); one started by hand is named on the precheck's stderr instead.

Away-improve mode (optional, off by default) is a separate switch from presence, the owner's own: `core/scripts/lane-improve
on [--until HH:MM | --hours N | --until-back] [--limits "<text>"] [--deny-paths "<glob>,..."] [--project <name>]...
[--unmetered] [--continue]` cuts an `away/<date>` branch for each project (at most one per project, ever; `--continue`
carries on with it and cuts one for a project that has none, or the owner adopts or drops it first) and has each coordinator keep improving its product there until
`lane-improve off`; the coordinator's `away-improve` skill (installed with the other lane skills) runs it, and
`core/scripts/away-merge` is its only merge route. `lane-away on` (even `--until-back`), night and the budget never start it,
and it does not make the owner count as away (`docs/superpowers/specs/2026-11-23-away-improve-design.md`). `IMPROVE_IDLE_MIN` in `machine.conf` (default 30) is the
idle time after which the night manager nudges a coordinator that improve mode covers.
