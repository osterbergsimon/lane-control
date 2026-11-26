# Getting started

This guide takes a new owner from an empty machine to a first merged lane, on native Orca (the orchestrator in use).
Each step has the command, what you should see, and what to do if you do not. Most of the time is spent
waiting for agents. The index of every other doc is [docs/README.md](README.md).

Placeholders: `<project>` is your repo's name, `example-host` is an SSH host, `http://127.0.0.1:4000` is a model
gateway. Replace them with yours.

## What lane-control is

- Scripts, templates and written rules for running coding agents on your repos, within a budget, mostly unattended.
- Three layers of agents. The **machine coordinator** (optional, one per machine) splits the budget and watches the machine.
- The **project coordinator** (one per repo) queues work in beads (`bd`), starts lanes, reviews them and merges on your word.
- A **lane** is a worker agent with one issue, one branch, one PR and its own worktree, on a **tier**.
- Tiers are roles (swift, standard, deep, top). Each project's tier table says which engine and model fills each one.

Every part and how they connect, on one page: [How the parts fit](how-it-fits.md).

More in the [README](../README.md) and in [core/roles.md](../core/roles.md).

## 1. Requirements

Check each one. All are needed on the machine that runs the agents, as a normal user (never root).

| Need | Check | You should see |
|---|---|---|
| Orca desktop app, running | `orca-ide status` | `appRunning: true` and `runtimeState: ready` |
| beads | `bd version` | `bd version 1.0.4` or newer |
| GitHub CLI, signed in | `gh auth status` | `Logged in to github.com` |
| jq, git, Python 3 | `jq --version && git --version && python3 --version` | three versions |
| Git identity | `git config user.name && git config user.email` | your name and email; onboarding a project refuses without them |
| Your agent engine, signed in once | Claude: `claude --version`. OpenCode: `command -v opencode2`, and for lanes `opencode --version` (2.0.14 or newer) | a version, or a path |

If not: on Linux, Orca's CLI is `orca-ide`; plain `orca` may be another program. If `orca-ide status` cannot
connect, start the Orca app first. For bd, `machine-setup` (step 3) says how to fix it and never installs it. The
full list is in [the native Orca setup](../orchestrators/native-orca/setup.md).

**Claude is needed to onboard a new project, even when every role runs on OpenCode.** The agents that adapt a
project's files and review them always run on Claude (`claude -p`), because they read the whole project. So the
machine where you first run `project-setup` on a project needs `claude` installed and signed in. Once a project is
onboarded (its `.lane-control/` files and `AGENTS.lane-control.md` committed and pushed), any machine can set it up
again without Claude. A machine with only OpenCode does that: onboard the project once on a machine with Claude, push
it, and clone it on the OpenCode machine. Step 4B has the details.

## 2. Get lane-control, its tools and your engine config

### 2.1 The checkout and the lane tools

```bash
git clone <lane-control repo URL> ~/code/lane-control
export PATH="$HOME/code/lane-control/core/scripts:$PATH"   # also add this line to your shell profile
lane-room
```

You should see four lines (`memory`, `load`, `lanes`, `limits`) and last `room for another lane`, exit code 0.
`lane-room` is the check a coordinator runs before it opens a lane.

Then install it: `~/code/lane-control/scripts/lane-control install` prints `installed <sha>`. That puts the repo at its
last commit in `~/.local/share/lane-control/<sha>/` with a `current` link, and links the lane tools (`lane-heavy`,
`lane-room`, `lane-tier`, `lane-limits.sh`, `design-shots`) and `lane-control` into `~/.agents/bin`, where agents call
them. Personas and hooks name `.../lane-control/current`, never this checkout. (`project-setup`, step 4, also installs
when nothing is installed yet and says `lane-control: installed <sha>`.)

If not: `lane-room: command not found` means the PATH line did not take effect in this shell. `lane-room` exiting
non-zero says why: memory or load is too high, or the lane count is reached. That is a real answer, not a fault.

Optional: if the machine needs its own limits (builds at once, memory kept for the desktop), copy the example and
keep only the lines you change:

```bash
mkdir -p ~/.config/lane-control && cp ~/code/lane-control/core/limits.example.sh ~/.config/lane-control/limits.sh
```

`lane-room` prints the limits it uses on its `limits` line. Without the file it works them out from your cores and
memory.

### 2.2 The budget config (every path)

The scripts that watch the budget (`budget-state`, the night manager, the machine check) read one config. Without it
every budget is unknown, night check-ins stay off, and `budget-state` exits 2 with `no config ...: every budget is
unknown`.

```bash
mkdir -p ~/.config/rove && cp ~/code/lane-control/core/config.example.sh ~/.config/rove/playbook.conf.sh
```

(The folder name is the default the scripts look in; `PLAYBOOK_CONFIG` names another file.)

**With Claude as a metered budget:** the example reads Claude Code's usage file and a ceiling file. Make the ceiling
file, then set the weekly percent you allow:

```bash
cp ~/code/lane-control/core/templates/budget-ceiling.json ~/.config/claude-usage-ceiling.json
```

Edit `"ceiling"` in it (the template says 40). The usage file, `~/.claude/usage-history.jsonl`, must be written by
Claude Code's status line ([core/machine.md](../core/machine.md), "Usage feeds"). Then `budget-state` prints one line
per budget, `main weekly=<w> window=<s> ceiling=<c> step=<n> <name> window_step=<m>`, or `main unknown` when the usage
file is missing or stale.

**With no usage feed at all** (only a self-hosted gateway): there is no budget to meter and the caps are the limit.
In the copy, set the feed under `BUDGET_USAGE` to empty (`[main]=""`). Then `budget-state` prints
`budget-state: no metered budget (no usage feed in the config): no budget steps; the caps are the limit` and exits 0,
and night check-ins still go out.

### 2.3 OpenCode on a self-hosted gateway: tell OpenCode about it

Skip this if you use Claude only. Do it before step 3: `machine-setup` and every OpenCode role start on the provider
named here. Models are named by the gateway's aliases, such as `chat` and `code`.

1. OpenCode's config, `~/.config/opencode/opencode.json`, names the gateway, with the key as a reference to an
   environment variable (never the key itself), and a default model:

   ```json
   { "model": "litellm/chat",
     "provider": { "litellm": { "npm": "@ai-sdk/openai-compatible",
       "options": { "baseURL": "http://127.0.0.1:4000/v1", "apiKey": "{env:LITELLM_API_KEY}" },
       "models": { "chat": {}, "code": {} } } } }
   ```

   The alias `chat` is then `litellm/chat` in OpenCode. The `"model"` line matters: Orca passes no model to an
   OpenCode automation, so the three machine automations (night manager, lane sweep, machine check) run on whatever
   OpenCode's default is. Without that line OpenCode picks a model itself, and on a machine that has only the gateway
   it may pick one that does not exist.

2. The agents get the key from the environment of the program that starts them, which is Orca. Quit Orca fully, set
   the variable in a shell, and start Orca from that shell (or set it where your login or desktop session starts
   apps):

   ```bash
   export LITELLM_API_KEY=<your gateway key>
   ```

   Then check it from a terminal inside Orca, which prints nothing secret:

   ```bash
   test -n "$LITELLM_API_KEY" && echo set
   ```

   You should see `set`. If you see nothing, Orca kept an older environment: quit it again, including a background
   runtime if it left one, and start it from the shell that has the variable. Whether closing the window alone is
   enough has not been tested here; the check above is the proof.

3. Check the gateway answers (the key stays in your shell):

   ```bash
   curl -s -H "Authorization: Bearer $LITELLM_API_KEY" http://127.0.0.1:4000/v1/models
   ```

   You should see JSON that lists `chat` and `code`. If not, the gateway is down, or the URL or key is wrong.

## 3. Set up the machine

Once per machine. It makes the machine coordinator and three small automations (night manager, lane sweep, machine
check), and checks the usage feed.

Claude on this machine:

```bash
machine-setup
```

No model is built in, so the first run asks two things: the machine coordinator's model, as `claude/<model>` (for
example `claude/sonnet`), and the model for the automations (for example `sonnet`). To answer up front:
`machine-setup --machine-coordinator claude/sonnet --automation-model sonnet`, or set `MACHINE_COORDINATOR=` and
`AUTOMATION_MODEL=` in `machine.conf`. A later run keeps what the machine has and changes no automation's model unless
you add `--replace-automation-model`.

Claude through a gateway: **do this before `machine-setup`.** An alias such as `sonnet` can only be checked once it is
mapped to a name your gateway lists; unmapped, `machine-setup` asks whether to write it unchecked, and without a
terminal writes nothing. First run `model-env` (it asks for the gateway, its model names and the auto-mode classifier
model), with your gateway key set in that shell. Add the line it prints to your shell profile, restart Orca from a
shell that has the values, and check with `model-env --check`. Details:
[the model environment](../orchestrators/native-orca/setup.md#the-model-environment-claude-through-a-gateway).

OpenCode only, on a gateway (the machine coordinator and the automations then run on OpenCode; step 2.3 comes first):

```bash
machine-setup --machine-coordinator opencode/litellm/chat
```

You should see one line per step, in the shape `<step>: <state> <detail>`. The states are, for example, `created`,
`exists`, `updated`, `ok`, `missing`, `stale` and `warning`. Among them:

- `machine.conf: created ~/.config/lane-control/machine.conf (fill in COORDINATORS and RUNNER_REPOS)`
- `bd: exists (1.0.4, floor 1.0.4)`
- `usage feed: ok ...`
- `orca folder: created ...` and the machine coordinator started in an Orca terminal titled `machine coordinator`.

Every step says `exists` on a re-run, so running it again is safe. `--no-start` sets up without starting the coordinator.

If not:
- `bd: missing` or `too old`: install or update bd the way you installed it, then re-run.
- `usage feed: missing` or `stale`: on Claude, the status line must write `~/.claude/usage-history.jsonl` (step 2.2).
  Budget steps stay unknown and night check-ins stay off until it does. With no metered budget at all, an empty feed
  in the config (step 2.2) is the answer.
- The machine coordinator started before OpenCode knew the gateway (you ran this before step 2.3): finish step 2.3,
  then close the `machine coordinator` terminal in Orca and run `machine-setup` again, which starts it.
- An automation of an earlier hand-made setup (`night-manager`, `lane-sweep`) is reported with the command to remove it.
  Nothing is doubled or replaced for you.

Then open `~/.config/lane-control/machine.conf` and fill in `COORDINATORS` after step 4 (one line per project:
`<project> ~/code/<project>`), so the night manager knows whom it may wake. Night hours, idle minutes and the length of
`lane-away on` are in the same file. Nothing from it goes in a repo.

Check later: `machine-check --last` prints what the last 30-minute check found.

## 4. Set up a first project

`project-setup` onboards one repo. You need the repo cloned, with a remote on GitHub, and its beads set up (a new
project with no beads yet starts one with `bd init`; where beads lives and how it is backed up:
[core/issues.md](../core/issues.md), "Where beads lives").

```bash
git clone <project repo URL> ~/code/<project>
```

First choose the tier table. Two paths: A for Claude, B for OpenCode on a self-hosted gateway. Both write the same
kind of file, the project's own tier table, outside the repo.

### 4A. Claude

Do nothing: the default table is used.

| tier | harness | model | for |
|---|---|---|---|
| swift | none | - | skipped |
| standard | claude | sonnet | everyday lanes |
| deep | claude | opus | careful lanes, reviews |
| top | none | - | skipped |

Then run the setup:

```bash
project-setup ~/code/<project>
```

### 4B. OpenCode on a self-hosted gateway

Do step 2.3 first (the gateway in OpenCode's config, the key in Orca's environment). Nothing is refused by model or
harness, and the table is yours to edit.

1. Let project-setup suggest the table from the gateway's model list. It needs the clone from above, and the key
   variable set in the shell you run it in:

   ```bash
   project-setup ~/code/<project> --suggest-tiers litellm
   ```

   You should see `gateway: read <n> models at http://127.0.0.1:4000`, each alias with the model behind it, a suggested
   table, and the question `Write this as the tier table of <project> (...)? [y/N]`. Answer `y`. The table looks like this:

   ```
   tier      harness   model          permission
   swift     none      -              -
   standard  opencode  litellm/code   -
   deep      opencode  litellm/code   -
   top       none      -              -
   ```

   Edit it if you want other aliases: save the printed table to a file, change it, and give it to the setup below
   with `--tiers <file>`. The suggestion warns when the lane tools installed on this machine are older than this
   checkout. That is fixed by the setup below, or by [updating an existing host](#updating-an-existing-host).

   If not: `the gateway key's environment variable ... is not set` means the variable is missing in this shell.
   `has the apiKey ... written in it` means the config holds the key itself, which is never used: write it as
   `{env:LITELLM_API_KEY}`. Without a terminal it writes nothing and exits 3.

2. Run the setup with OpenCode as the coordinator and the reviewer:

   ```bash
   project-setup ~/code/<project> \
     --coordinator opencode/litellm/chat --reviewer opencode/litellm/code
   ```

   A later run keeps the table you accepted. Do not pass `--adapter` or `--adapt-reviewer`: they must be Claude models
   (the defaults, `claude/sonnet` and `claude/opus`), and project-setup refuses anything else with exit 2: `--adapter
   and --adapt-reviewer must be claude/<model>`.

**Onboarding a new project needs Claude on that machine.** The adapting agent and its reviewer are always Claude, so
the first `project-setup` of a project fails with `claude not on PATH, but the onboarding agents use it` on a machine
without it. (Making onboarding run on OpenCode is planned work; this step will change when it lands.) What a machine
with only OpenCode can do today:

- **The project is already onboarded** (its `.lane-control/` files and `AGENTS.lane-control.md` are committed on the
  default branch). Clone or pull it and run the command above. You should see `adapt: exists .lane-control/ is
  committed`, and no Claude is needed.
- **The project is new.** Onboard it once on a machine with Claude: `project-setup ~/code/<project> --no-start`, answer
  `y` to the commit and the push. Then pull it on the OpenCode machine and run the command above.
- **No machine with Claude at all.** `--skip-adapt` runs the rest of the setup without Claude, but it writes none of
  the project's lane-control files (no path map, no `AGENTS.lane-control.md`), and the coordinator is told to read
  them. That is a stopgap, not a setup. (Read from the source; not run.)

### What the setup does, and what you should see

In order, roughly:

1. It reads the integration branch's protection (reads only). Turn on branch protection if it says none.
2. It looks at the repo's beads, and may ask to change them. A plain `.beads` folder (what `bd init` makes) is offered
   a move: `Move <repo>/.beads to a private repo <owner>/<project>-beads ...? [y/N]`, then `Create the private
   GitHub repo ... now? This publishes beads data to GitHub (private). [y/N]`. Stop agents that write to beads first.
   Yes to both creates the private repo, copies the folder there, pushes it and links `.beads` to it; the old folder
   is kept beside it. No to either changes nothing, and beads stays local with no off-machine copy (the setup says so).
   With no beads and a private beads repo already on GitHub it offers `Restore beads: ...? [y/N]` instead.
3. On a project not onboarded yet, an adapting agent (Claude) writes the project's lane files, and a reviewer checks
   them. Then it asks `Commit these files and run the Orca setup? [y/N]`. Answer `y`; on no, or without a terminal,
   nothing more runs and it exits 3. An onboarded project says `adapt: exists` and skips this.
4. It may ask to commit a drafted `orca.yaml` and to push the default branch. Say yes to the push: Orca cuts lane
   worktrees from `origin/<branch>`.
5. `orca-repo: created ...`, `lane-tools: created ...` and the skills (`native-orca-lanes`, `component-gallery`,
   `away-improve`), the rendered personas, `tiers: created ...`.
6. A report on Orca's cleanup rules, and GitHub's "Automatically delete head branches" (`[Y/n]`: Enter turns it on).
7. The coordinator starts in an Orca terminal titled `<project> coordinator`, and the last line is
   `summary: <project> · native Orca · orca repo <id> · sessions <project> coordinator`.

Check the table the project now has:

```bash
project-setup ~/code/<project> --show
lane-tier <the lanes.tsv path that --show prints> standard
```

You should see `harness=claude model=sonnet permission=inherit` (path A) or `harness=opencode model=litellm/code
permission=-` (path B). `lane-tier ... swift` exits 3 and says to skip it, because its harness is `none`.

If not:
- `claude not on PATH` or `opencode2 not on PATH`: the coordinator's engine is not installed for this user.
- A re-run reports `exists` for what is done, so a run that stopped halfway is safe to run again.
- `--no-start` does everything but start the coordinator; start it later with `project-pause start ~/code/<project>`.
- Claude asks `Do you trust this folder?` the first time; `--trust-folder` answers yes for unattended runs.
- Orca's cleanup report: if it says no rule is on, turn on "Delete merged workspaces" at Settings -> Workspace Cleanup
  -> Cleanup rules, and "Delete released orchestration worker workspaces" beside it. Setup never changes Orca's
  settings ([docs/orca-cleanup.md](orca-cleanup.md)).

## 5. Start the coordinator and give it a first issue

The setup started it. In Orca, open the terminal titled `<project> coordinator`. After it has read its persona and
`AGENTS.md`, it says in one line that it is ready.

If the terminal is not there (you used `--no-start`, or Orca restarted): `project-pause start ~/code/<project>`. It
resumes the recorded session.

Make a small first issue and tell the coordinator, in its terminal:

```bash
cd ~/code/<project> && bd create "Add a short section on running the tests to the README"
bd ready
```

You should see the new issue in `bd ready`. Then type to the coordinator: `Take <issue id> as a lane.` You can also
just describe the job in words, and the coordinator files the issue itself.

You should see the coordinator check the gate (the issue, the tier from your path map, the budget, `lane-room`, a clean
and current checkout), then open a lane and say in one line which lanes run. If it holds the lane, it tells you
why. The usual reasons are no room (`lane-room`), a budget step, or a checkout that is behind or on another branch.

## 6. Watch a lane, its report, review and merge

You do not need to poll. The lane's report wakes the coordinator, and the coordinator tells you.

- In Orca, a new worktree appears under the project. Its card carries a comment `<KEY> <tier> <harness>/<model>: <one line>`.
- The lane works, merges `origin/<base>` into its branch (never rebase), runs the project's checks and opens a PR.
- It reports once, at the end, in at most six lines:

  ```
  <KEY> succeeded|failed|blocked|NEEDS_STANDARD|NEEDS_DEEP: <one line>
  branch <b> · PR #<n> · ci passing|pending|failing · base <BASE> @<sha7>
  gates: <command> ok|fail, ...
  deviations: none | <one line each>
  filed: none | <issue ids>
  agents-md: none | <one line>
  ```

- `NEEDS_STANDARD` or `NEEDS_DEEP` means the lane stopped instead of guessing. The coordinator reopens it one tier up.
- The coordinator does not trust the numbers. It reads the diff and reruns the fast checks. When the project's risk rules
  call for it, or whenever you ask, a reviewer on a different model reviews the exact commit. Its findings come back as
  `### <ID> [blocker|major|minor|nit] <title>`, and the lane fixes every blocker and major one (two rounds at most).
- Read the PR yourself: `gh pr view <n> --web`, or ask the coordinator for its summary.

If a report does not come: ask the coordinator, in its terminal, what the lane waits for. A lane that cannot report
(for example after an Orca restart) is covered in
[the remote doc](../orchestrators/native-orca/remote.md), "A dispatch that can no longer report".

When you are happy, say so in the coordinator's terminal: `Merge <KEY>-<n>.` Only your own words in its terminal count;
a message relayed by another agent never does.

## 7. Close it

The coordinator closes the lane after your yes, in this order, and tells you:

1. `gh pr merge <n> --merge --match-head-commit <full head sha>`: a merge commit, pinned to the commit that was reviewed.
2. `bd close <id>`, then `bd dolt push` (the backup of issues and memories).
3. It releases the lane's worker and stops the reviewer.

Orca's own cleanup rule then removes the merged worktree and branch. Check it yourself, read only:

```bash
bd show <id>                              # status: closed
orca-ide workspace cleanup preview --json # the lane's row says evaluation.reason: merged, until Orca removes it
```

If a row stays with an `evaluation.reason` other than `merged`, a rule is off, and the coordinator removes the clean, merged worktree
by hand or asks you ([docs/orca-cleanup.md](orca-cleanup.md)). You never need to delete a lane's worktree yourself.

## 8. Where to go next

- **Let it work while you are away:** [docs/away-improve.md](away-improve.md). It is off until you run `lane-improve on`.
  Presence alone is `lane-away on` (night hours check in on idle coordinators); it never starts improve mode.
- **Put a project on an SSH host:** run `ssh example-host` once by hand, then
  `project-setup --check ~/code/<project> example-host`: it lists every prerequisite (ssh, Orca on both sides and their
  versions, pairing both ways, the tunnel, the agent and the gateway key on the host) and changes nothing. Then
  `project-setup ~/code/<project> example-host`, driven from this machine's Orca
  ([orchestrators/native-orca/remote.md](../orchestrators/native-orca/remote.md)). For the host's coordinator to run
  lanes here it asks for this desktop's link, which you make by hand in Orca: Settings > Servers > "Share this
  computer", choosing 127.0.0.1 (setup says so before it starts, so you can make it while it runs).
- **Cleanup and branches:** [docs/orca-cleanup.md](orca-cleanup.md).
- **More projects and models:** repeat step 4 per repo; tiers and routing are in [core/model-routing.md](../core/model-routing.md).
- **Switch orchestrators:** `scripts/orch status ~/code/<project>`, then follow [the switch guide](../orchestrators/switching.md).
- **Everything else:** [docs/README.md](README.md).

## Updating an existing host

Each machine and each box has one installed lane-control: the whole repo at one commit, in
`~/.local/share/lane-control/<sha>/` (`$XDG_DATA_HOME` when set), with a `current` link to the one in use. Personas
name `~/.local/share/lane-control/current`, and the lane tools in `~/.agents/bin` (with `lane-control` itself) are
links into it, so nothing points at a checkout or a lane worktree that may go away. Old trees are kept: a running lane
may still read one. A project records the sha it was set up with. Installing a new commit moves the tools at once;
the skills and each project's personas follow at that project's `--refresh`. A running coordinator also keeps the
persona text it started with. Pick the case that fits.

```bash
lane-control status                            # the installed sha, the checkout's, and whether they differ
lane-control doctor                            # every project, link, skill and box against the install, with what to run
project-setup ~/code/<project> --show          # says "lane-control <a> installed, project set up with <b>: run project-setup --refresh"
```

### A machine with its own lane-control checkout, project stays on its engine

```bash
cd ~/code/lane-control && git pull
scripts/lane-control install                   # prints "installed <sha>" (or "exists <sha>")
project-setup ~/code/<project> --refresh      # once per project
project-pause stop ~/code/<project>
project-pause start ~/code/<project>
```

Why each step:
- `git pull` updates the checkout; `lane-control install` lays its last commit as the machine's tree and switches
  `current` to it (no network; `--version <sha or tag>` installs another commit, `--from <checkout>` names the
  checkout). After the first one, `~/.agents/bin/lane-control install` works from anywhere: it remembers the main
  checkout (never a lane worktree). `project-setup` run from the main checkout on its default branch installs that
  checkout's last commit itself too, so a forgotten install is not a problem. Run from a lane worktree, another branch
  or a commit not shown to be newer, it keeps the installed one (`lane-control: kept ...`) and runs that tree's own
  setup, so skills and personas come from it, unless you answer yes in a terminal.
  Uncommitted changes are never installed (it says so). A failed install exits 1 and changes nothing.
- `--refresh` renders the skills and the personas again from the installed tree and records its sha. It keeps each
  persona's engine and model and the project's tier table, starts nothing and commits nothing, and says which running
  agents need a restart. It ignores `--coordinator` (project-setup says so).
- A machine set up before the install moves to it with the first install: `note: this machine moves to the install
  ...` (from `lane-control install`, or `project-setup: note: ...` when setup installs first), and per project, at
  its `--refresh`, `lane-control: moved ...`. Nothing else to do.
- `project-pause stop` asks the coordinator for its handover, waits for it (up to 10 minutes) and closes its terminal. It
  refuses while a lane or reviewer runs, and it runs from an Orca terminal. `start` opens the coordinator again,
  resuming its session, with the new persona.

You should see `restart: needed ...` from `--refresh` when a persona changed and its agent is running, and `restart: none ...` when
nothing needs it. A running agent otherwise reads the new persona at its next compaction.
Re-run `machine-setup` too if the machine's automations changed: it only repairs what is out of date and says `exists`
for the rest.

### A project that runs on Claude and should move to the gateway

`--refresh` keeps the engine each persona was set up with, so it never moves a project. Run the setup with the new
engine instead (step 2.3 first, and the key set in the shell you run these in):

```bash
cd ~/code/lane-control && git pull
project-setup ~/code/<project> --suggest-tiers litellm      # answer y: it replaces the old table
project-setup ~/code/<project> --coordinator opencode/litellm/chat --reviewer opencode/litellm/code
project-pause stop ~/code/<project>
project-pause start ~/code/<project>
```

- The second command stores the new engines, renders the personas, and installs the current lane tools and skills (so
  it also does what `--refresh` does). It needs no Claude when the project is onboarded. It leaves the running
  Claude coordinator alone (`session coordinator: exists`), which is why the stop and start follow.
- `start` runs the coordinator on OpenCode. A recorded Claude session is not used: it starts fresh and reads
  `handover-latest`, which `stop` asked the old coordinator to write.
- An OpenCode coordinator takes only the permission modes `auto`, `bypassPermissions` or `default`. A project set up
  with `acceptEdits`, `plan` or `dontAsk` is refused with exit 2 and the reason; add `--permission-mode auto` to the
  second command.

### A box driven from a desktop

A box has two connections, both tunnels started on the desktop ([how the parts fit](how-it-fits.md#a-box-two-directions)):
`orca-tunnel-<host>` (`ssh -L`, direction 1: the desktop controls the box, made by `orca-host-install --pair`) and
`lane-tunnel@<host>` (`ssh -R`, direction 2: the box's coordinator starts lanes on the desktop, made by
`project-setup` after the share link).

A box set up with `project-setup <repo> <host>` has no lane-control checkout of its own. It gets the desktop's installed
tree, whole and at the same sha, in its own `~/.local/share/lane-control/<sha>/` with its own `current` link, on every
`project-setup <repo> <host>` run (sent over ssh; kept when the box has that sha already;
[the remote doc](../orchestrators/native-orca/remote.md)). So update it from the desktop:

```bash
cd ~/code/lane-control && git pull && scripts/lane-control install
project-setup --check <repo on the host> example-host     # what is missing; changes nothing
project-setup <repo on the host> example-host --refresh
```

`--check` also says when the box's Orca and this desktop's differ (`check versions: warn`): keep them on one version.

The box gets the tree (`lane-control: copied <sha> on example-host`) and the box's own `--refresh` runs there (lane
tools as links, skills, personas naming the box's own `current`). A box set up before has its old copy in the same
folder; it stays and is no longer used. The box has the whole tree, so `project-pause` and `--suggest-tiers` are there
too, under `~/.local/share/lane-control/current/scripts/`. To restart the box's
coordinator, tell it in its terminal to write its handover, close its terminal in Orca (it shows in your window), then
run `project-setup <repo on the host> example-host` again: it starts the missing coordinator, which reads
`handover-latest`. (From the source; not run.) Without a restart the coordinator reads the new persona at its next
compaction. To move a box to a gateway, the simpler way is a lane-control checkout on the box itself: clone it there and
use the second case.

If not: `project-pause stop` refusing and naming a lane means wait for the lane, or ask the coordinator to finish
or park it. Never stop a live lane without your word.

## Stopping

- **One project's coordinator:** `project-pause stop ~/code/<project>` (it hands over, then closes the terminal; it
  refuses while a lane or reviewer runs). `project-pause rest ~/code/<project>` stops no agent: it only makes the night
  manager skip the project, and `project-pause wake ~/code/<project>` undoes it.
- **A running lane:** ask its coordinator. It stops a live lane only on your word, and puts the issue back to open.
- **Away modes:** `lane-away off` says you are back. `lane-improve off` ends away-improve mode; what is running drains
  first ([docs/away-improve.md](away-improve.md)).
- **The machine coordinator and the automations:** close the `machine coordinator` terminal in Orca. The three
  automations wake coordinators at night, so remove them to stop that: `orca-ide automations list`, then
  `orca-ide automations remove <id>` for `night manager`, `lane sweep` and `machine check`. Running `machine-setup`
  again makes them again. The CI limits follow presence through a user timer: `systemctl --user disable --now
  ci-presence.timer`.
