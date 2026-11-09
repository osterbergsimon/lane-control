# Orca + Cotal as a supported runner (Rove is the other)

Status: Orca + Cotal is a supported runner next to Rove; `scripts/project-setup` (section 5b) is its path. The trial
runs below were on 2026-11-05 with Orca 1.4.250 (desktop on a NixOS workstation), Cotal 0.145.1, OpenCode 2.2.1,
against a Debian box reached only over SSH, whose internet was limited by an allowlisting proxy.
Tags: **[tested]** ran it, **[docs]** read in the project's docs but not run, **[none]** no equivalent.

lane-control's Rove scripts call `rove api` and do **not** run unchanged on Orca or Cotal. What carries over is the doctrine
(roles, tiers, path map, beads, review by risk) and the shape: one coordinator per project launching worker lanes.

## 1. Which layer does what
| Need | Orca alone | Orca + Cotal |
|---|---|---|
| Agents on a VPS, watched and typed into from the workstation, over plain SSH | yes: SSH worktrees, managed server on the host, agents survive disconnects [tested] | same |
| OpenCode 2.x in a real terminal (its own TUI) | yes ("OpenCode 2 beta") [tested] | yes, with `--no-events` [tested] |
| Coordinator starts lanes and gets their results | Orca orchestration: experimental, its panel is disabled in 1.4.249+ [docs] | `cotal_spawn` + DMs from inside the agents [tested] |
| Messages to an agent never land in a half-typed prompt | not documented: worker delivery is `dispatch --inject` into the terminal (the docs' example waits for `tui-idle` first) [docs] | yes: Cotal's OpenCode plugin injects a message as its own turn through OpenCode's server (section 6) |
| Lanes nested under the coordinator in the sidebar | worktrees nest under a parent ("N children" chip) [docs] | yes, with a worktree per lane: the coordinator runs `orca worktree create --parent-worktree active`, then `cotal_spawn` into it (section 5b) [tested] |
| A worktree (branch) per task | yes, Orca's core unit [docs] | yes, Orca creates it; Cotal only runs the agent in it (section 5b) [tested] |
| Chat UI instead of a TUI | "structured native chat", but **not over SSH** ("SSH sessions continue to use terminal-backed Chat UI") [docs] | n/a |

Rule of thumb: **Orca is the cockpit** (SSH, terminals, editor, git per worktree). **Cotal is the radio** (who
started whom, messages that wait for the agent's turn, roles). Use Orca alone if you drive lanes by hand; add Cotal
when a coordinator should run lanes and talk to them while you type.

## 2. Install Orca on the workstation
- Linux builds: AppImage, x86_64/aarch64 RPM (Fedora, Rocky), AUR `stably-orca-bin`. On NixOS, package the AppImage
  with `appimageTools.wrapType2` (an example overlay: the owner's dotfiles `overlays/orca-ide`): the app as `orca-app`
  with a menu entry, the CLI as `orca-ide` (Orca's own Linux name for it; plain `orca` there is often GNOME's screen
  reader). Verify downloads against the sha512 in the release's `latest-linux.yml`. [tested]
- **Telemetry is on by default** (anonymous, PostHog Cloud US). Start it with `ORCA_TELEMETRY_DISABLED=1` (or
  `DO_NOT_TRACK=1`) or turn off Settings, Privacy, "Share anonymous usage data". No account is needed. [docs]
  ```bash
  ORCA_TELEMETRY_DISABLED=1 setsid appimage-run ~/Applications/orca-linux-<ver>.AppImage >/dev/null 2>&1 &
  ```
- Releases are near daily (seven in the week of the trial): pin and note the version you evaluate.

## 3. Connect the VPS
1. Add the host in Orca (SSH alias from `~/.ssh/config`, or host, user and key). [tested]
2. Add the repo with **Run on: <host>**. On first connect Orca installs its own pinned server under
   `~/.orca-remote/` and `~/.orca/` on the host; terminals then live there and survive closing the desktop. [tested]
3. Host prerequisites: git, the agent CLIs, and Orca's server. By default Orca installs that on first connect and
   downloads its Node runtime from `nodejs.org` (or a mirror). On an air-gapped host run `scripts/orca-host-install
   <host>` first (section 5b): it copies the server and Node from your Orca install, so the host needs neither
   `nodejs.org` nor `onorca.dev`. [tested]
4. Model credentials: set the provider key in Orca's OpenCode account profile (worked in the trial), or log in on the
   host with `opencode auth login` in an Orca terminal. **Do not copy `auth.json` from the workstation** (section 7). [tested]
5. The `orca` CLI is installed on the host at `~/.orca/cli/bin/orca` and reaches the host's runtime as is:
   `orca status --json` returned `ok` with no extra setting. The docs also describe "Allow this host's orca CLI to
   control Orca" and Settings, Experimental, CLI; neither was needed or found in 1.4.250. [tested]

## 4. Orca alone: coordinator and lanes
- One worktree per lane is Orca's native unit: `orca worktree create --repo id:<repoId> --name <lane> --json`, nested
  under the coordinator's worktree via "Parent workspace" (Advanced drawer). This also gives one branch per lane, as
  `templates/AGENTS-coordination.md` expects. [docs]
- Drive agents from a script or the coordinator: `orca terminal create --worktree path:<dir> --command "<agent>"`,
  `orca terminal read`, `orca terminal wait --for tui-idle`, `orca terminal send --text … --enter`. [docs]
- `orca terminal send` **types** into the terminal: check `terminal read`/`wait --for tui-idle` before sending, and
  expect it to collide with whatever a human is typing in that terminal. [docs]
- Orca orchestration (runs, tasks, dispatch, coordinator inbox, gates): enable under Settings, Experimental; the panel
  is "temporarily disabled again" as of 1.4.249. Re-check before relying on it. [docs]

## 5. Orca + Cotal on the host
All on the VPS, in an Orca terminal or over SSH. Node 22+.
```bash
npm i -g cotal-ai @opencode/cli          # OpenCode 2.x is fine here (unlike wsp)
chown -R root:root /usr/local            # if node came from a tarball: Cotal refuses a writable bundled nats-server
cotal telemetry off                      # also on by default; or COTAL_TELEMETRY_DISABLED=1
# proxied host: agents get a filtered environment, so forward the proxy explicitly
echo '{ "spawn": { "env": ["HTTP_PROXY","HTTPS_PROXY","NO_PROXY","http_proxy","https_proxy","no_proxy"] } }' \
  > ~/.config/cotal/config.json
cd ~/code/<space-root>                   # one Cotal space; projects are folders under it
cotal setup --destination local --harness opencode --project . --space <space> --no-input
cotal ext add @cotal-ai/orca
export PATH="$HOME/.orca/cli/bin:$PATH"  # the manager calls `orca`
cotal up --detach --runtime orca         # broker, delivery daemon, manager (bundled nats-server, loopback)
cotal spawn -d -n <project>-coord --harness opencode --no-events -m <provider/model> -C ~/code/<project>
```
Notes [tested]:
- The folder must be inside a worktree Orca already has open (Cotal resolves it with `orca worktree current`); the
  agent appears as a tab `cotal-<name>` in that worktree. Watch and type there; `cotal attach` is not used.
- OpenCode 2.x needs `--no-events`: status (idle/working/waiting), DMs, roles and spawning work; the live activity
  feed (`cotal watch`, dashboard Activity) does not. With OpenCode 1.x the feed works too.
- Give the coordinator lane-control's doctrine as its first message (`cotal send -t <project>-coord "…"`), and tell it
  to start lanes with `cotal_spawn` (harness, model per tier, `--no-events` for 2.x), have lanes DM it when done, and
  report to you by DM.
- Talk to agents: type in their Orca tab, `cotal send -t <name> "…"` (one-off; replies can't reach a CLI sender), or
  `cotal chat` (a live session with its own identity, so replies arrive). Roster: `cotal ps`, `cotal who`.
- Read-only web dashboard: `cotal auth mint dashboard --profile admin --out <file>` then
  `cotal service add web --preset web --creds <file> --read-only` and an SSH tunnel to port 7799. It cannot post
  messages in 0.145.1 (read-only is forced).

## 5b. project-setup: Orca tasks, Cotal agents (recommended)
Verified end to end on 2026-11-06 (trial box, user `dev`, Orca 1.4.250, Cotal 0.145.1, OpenCode 2.2.1, Claude Code
2.1.319). Each project gets:
- its own Cotal space;
- a **coordinator** in the main checkout;
- a **frontend design lead** in its own Orca worktree, nested under the main checkout.

Lanes are Orca child tasks with one Cotal agent each, routed by tier to OpenCode or Claude. Messages never type into a
composer. [tested]

### Host: once per machine, as a normal user (not root)
- Agents never run as root: Claude refuses `bypassPermissions` as root, and nothing needs it.
- Install `cotal-ai` and the harnesses (`@opencode/cli`, `@anthropic-ai/claude-code`) from npm. Then, as the agent
  user:
  ```bash
  cotal ext add @cotal-ai/orca
  echo '{ "spawn": { "env": ["HTTP_PROXY","HTTPS_PROXY","NO_PROXY","http_proxy","https_proxy","no_proxy","TMPDIR"] } }' \
    > ~/.config/cotal/config.json                    # names to pass through, not values
  claude                                             # sign in once; for bypass mode also run
  claude --permission-mode bypassPermissions          # once, and accept the warning
  ```
- From the workstation, put Orca's server on the host without the host downloading anything:
  ```bash
  scripts/orca-host-install <user@host> --pair        # copies orcad + node from your Orca install, starts orcad,
                                                      # tunnels 127.0.0.1:6768 and pairs (environment <user-host>)
  ```
  - Without `--pair`, add the host in Orca and connect: Orca finds the install and reuses it.
  - Either way the host needs no `nodejs.org` or `onorca.dev`.
  - `--pair` keeps the tunnel up as a systemd user unit `orca-tunnel-<host>` on the workstation.
  - If orcad's port is taken (another user's Orca), the script refuses; pick another with `--port`.
  - After the desktop updates Orca, a re-run installs the new server and replaces the old running one.
  - `--pair --no-tunnel` starts orcad and prints its pairing code, without a tunnel or a saved environment.
  - A trial or throwaway host stays out of `~/.ssh/config`: set `LANE_SSH_CONFIG=<its ssh config>` and both scripts
    (and the tunnel unit) use `ssh -F` with it.

### Project: one command
```bash
scripts/project-setup <git-url | /abs/dir> [--host user@host] [--name Shop] \
  [--coordinator claude/opus] [--design-lead claude/opus] [--permission-mode auto|bypassPermissions|default] \
  [--reviewer claude/opus] [--tiers file.tsv] [--port N] [--write-orca-yaml (leaves an uncommitted draft as orca.yaml)] [--no-design-lead] [--trust-folder] [--no-start (opt-out of starting)] \
  [--private GLOB]...
```
It does each step only if it is not already done, so a re-run prints `exists` for every step:
(Onboarding is safe to interrupt. The adapting agent's run is recorded as finished in the settings store only when it
ends; files an interrupted run left under `.lane-control/` are never reviewed or committed as if complete, the agent
runs again over them. Files from a finished run that was declined, or failed its review, go back to review without
being rewritten. The record is dropped when the files are committed.)
(The review loop: the first review is the full audit. Every later one gets the earlier findings and only checks that
each was fixed and that the fix broke nothing it touched. The findings of the last review always get a last fix, with
no further full review; then the file list comes, with what that fix was asked to do listed as "fixed, not
re-checked". A finding marked `[safety]` (private data, weakened doctrine, smuggled instructions) is checked once more
after the last fix, wherever the reviewer wrote the mark and whatever its verdict; if it is still open, or that check gave
no verdict or crashed, the run stops with nothing committed. A first review that gave no verdict stops it too (the safety
checks never ran); a later review that gave none or crashed is listed beside the file list. A file written outside
`.lane-control/` stops it as well.)
(Repo visibility is a fact, not a guess. Before the adapter runs, project-setup reads it with `gh repo view` (read-only,
20 seconds at most) and gives it to the adapter and every reviewer as public, private or internal; when gh is missing,
logged out, offline or unclear it is `unknown`, and the adapter writes the safer value (treat as public) and a question.
The review checks the Exposure slot against the same value. A gh failure never stops the run. With visibility
`unknown`, a hand edit of Exposure needs the "(owner's answer)" mark, or a re-run's review flags it.)
(Review tiers. Review C (the owner approves spec statements before merge) is for paths where a mistake really hurts:
secrets, auth, data loss, the check that keeps bad input out, sandbox and host settings. Ordinary code in a sensitive
package is B, even at deep tier. "In doubt one tier up" is for the model tier, not for the review letter. The adapter
names the harm in the why of every C row, and the full review makes a C row without a named harm a major finding.)
(Overall time limit. `AGENT_LIMIT` in `scripts/project-setup`, next to `AGENT_TIMEOUT`, caps the agent time of one run
at 90 minutes, all agents together. It is checked before an agent starts: reaching it stops the run there, never kills
an agent, leaves the files under `.lane-control/` as they are, commits nothing, runs no Orca step and says to run
again. A re-run counts from zero. If the limit is reached before the answers are asked, they are not asked and the
questions stay on the list. There is no token or dollar cap (`--max-budget-usd` is API only). The run prints
`agent time used` at the end, also when it stopped.)
(Leftovers. Pressing Enter at a question means "ask me later". At the commit, the questions still open and the suggested
AGENTS.md edits are added to the coordinator's first message, labelled as information from the adapter. The coordinator
asks the owner about them in its tab, one at a time with its recommendation, opens a lane for any answer that changes a
file, and changes nothing on the strength of the list alone. Nothing left over: the message is unchanged. The list is
only given when this run starts the coordinator; otherwise the run prints what was left, so it is not lost.)
(Suggestions are one current list. A fix round is given the earlier list and writes the whole list again, still-open
entries kept and settled ones dropped, each once; its file replaces the kept one, and a round that writes none leaves
it. The adapter asks only what cannot be looked up: it opens a file before asking about it, reads the repo read-only (it
cannot look at the machine, so what only the machine knows is still asked), and never asks how a lane-control
template rule works. The suggestions file has two fixed headings, `## AGENTS.md edits you would make` and
`## Questions for the owner`, but the reader also finds other shapes.)
(Answers before the commit. After the review loop, in a terminal, each question still open is shown in turn and the owner
may answer it in plain text; Enter leaves that question for later (the agent's choice stays, and the coordinator asks, see Leftovers). Type-ahead is flushed before the commit
question, so a line typed early never answers it. The answers go to one more adapter run with its own short rules
(`templates/adapt/answers-prompt.md`: apply only the answers, change nothing else, an answer is the owner's word, no
new questions, no suggestions file), followed by the same scope check as every agent run. That run is not reviewed: the
file list says "Changed by your answers, not re-checked" and shows its diff. It adds at most one agent run to an
onboarding. Only the answered questions leave the list, and only when the run changed a file; if it changed nothing,
the note says the run "changed no file: the files already matched, or an answer was not applied" and the questions
stay. If the run fails, the files are put back as they
were before it, the file list comes with a note that the answers were not applied, and the owner can say no there and
run again to answer: a stop would throw away a reviewed result for a failure that changed nothing. A change outside
`.lane-control/` stops the run, as after any agent run. Without a terminal nothing is asked. A re-run keeps hand edits
and the answers' changes and asks only the questions still on the list.)
1. Clones the repo, or uses the folder (`git init` only when it is not a repo). It never commits.
2. Runs `orca repo add`.
3. Installs the skills for the agent user (`~/.agents/skills/`, linked into `~/.claude/skills/` and
   `~/.config/opencode/skills/`): Orca's `orca-cli` and lane-control's `orca-cotal-lanes`. They are user-level
   because a lane worktree carries only committed files.
4. Creates the Cotal space (`cotal space create`; plus `cotal setup -H claude` for the Claude plugin and wake channel).
5. Renders the personas from `personas/` (coordinator, design lead, reviewer) into `.cotal/agents/` (Cotal reads them
   there) and the tier table `templates/lanes.tsv` into the project's settings store (below), and gitignores `.cotal/`.
   Each persona names the tier table and the reviewer persona by absolute path, and the project's Claude permission
   mode.
5a. Setup script: Orca runs `orca.yaml` `scripts.setup` in each new worktree (a "Setup" tab), but reads it from the
   worktree, and creates the worktree from `origin/<branch>` (read from Orca's code, not confirmed by creating a
   worktree), so the script and the onboarding commit reach lanes only once committed and pushed. Without an
   `orca.yaml`, project-setup drafts one from the lockfiles (uv, poetry, npm/pnpm/yarn/bun, cargo, go, in the root and
   two levels down; the beads link when `.beads` is a symlink). During onboarding, after the owner's yes to the file
   list, it asks a second question: no orca.yaml was found, here is the generated text, it must be committed and
   pushed. A yes commits `orca.yaml` by explicit path and pushes the checked-out branch (never forced, nothing else
   set); that push also carries the onboarding commit. With a tracked orca.yaml, or no lockfiles, it asks about the
   push alone, only when origin's default branch lacks the onboarding files or `orca.yaml`; the question lists the
   commits the push carries. An already onboarded project is asked too on a re-run (that is how a declined, failed or
   interrupted answer is asked again), also with `--write-orca-yaml`. An untracked `orca.yaml` that differs from the draft is never
   overwritten or committed. The push is asked only on the branch origin's HEAD names; on another branch nothing is
   asked, and the run says lanes will not get the files only when origin's default branch really lacks them. With no
   origin Orca makes worktrees from the local branch, so the question is only to commit `orca.yaml`. A no, no terminal or a failed push commits
   and pushes nothing more, and the agents still start, with that warning. Only a no to the file list stops the run.
   A run without onboarding (`--skip-adapt`) asks nothing and never commits: it drafts into
   `.cotal/orca.yaml.proposed`. `--write-orca-yaml` only changes where an uncommitted draft is left (as `orca.yaml` in
   the repo, which a later run offers to commit); it never commits by itself. The draft ends by writing `orca-setup-done` in the worktree's git dir;
   agents start only after it.
   When the repo tracks `.envrc` and `direnv status` reports it allowed in the source checkout (the owner approved it
   there), the generated script first runs `direnv allow .envrc` in the worktree, guarded: a machine without direnv is
   fine and a failure only prints a line. direnv approves per path, so without it each new worktree opens with
   "direnv: error ... is blocked". With the `.envrc` not approved here no line is written and the output says so. An
   existing `orca.yaml` is never edited. The line approves whatever `.envrc` the worktree holds, not the content the
   owner approved in the source checkout, and it stays in the committed `orca.yaml`: direnv's per-content check is
   bypassed in every lane worktree. The owner chose this.
6. Starts the broker (`cotal up --no-manager`, on a per-project port recorded in the settings store) and a
   manager service `mgr` on the Orca runtime.
   `mgr` carries `envRefs` for the proxy variables, because `cotal up` gives its manager a fixed environment without
   them.
7. Starts the coordinator in the main checkout. A Claude agent first needs the owner's answer to Claude Code's
   "Do you trust this folder?" prompt for the main checkout (lane worktrees share it), which an agent tab can't give.
   In a terminal, project-setup offers to open Claude there for you to answer, then goes on (also with `--host`: the
   ssh session gets a terminal). Without a terminal it stops and says to run `cd <repo> && claude` once.
   `--trust-folder` answers yes for you instead, without asking, for unattended runs. Claude Code has no switch for
   this, so it writes `hasTrustDialogAccepted` for the folder into `~/.claude.json` the way Claude does: atomically,
   keeping everything else, refusing a file it can't parse, and checking afterwards that a running Claude session
   didn't overwrite it.
8. Creates the Orca worktree `design-lead` as a child of the main checkout, waits for the setup script, starts the design lead there, and only then closes
   Orca's startup shell and the Setup tab (kept if the script never finishes; an empty worktree may refuse the spawn),
   then opens a shell after the agent's tab. With
   `--no-design-lead` (a project without a frontend) there is no design lead: the coordinator routes any UI work
   itself and its persona says so.
9. Verifies that the agents are live and that the design lead's task is nested.

With `--host`, it copies itself to `~/.local/share/lane-control/` on the host and runs there; paths are host paths.

**Private files during onboarding.** The adapting agent and its reviewer are denied, by name: a `.git` folder or file at
any depth, every git-ignored path (found at launch with `git ls-files -o -i --exclude-standard --directory`, also inside
submodules and embedded repos), every `.env*` file except `*.example`, `*.sample`, `*.template` and `.envrc`, and each
`--private <glob>` (repeatable; relative to the repo root, e.g. `--private 'shot*.png'`). Other tracked files stay
readable. The rules cover Read, Glob and Grep, and the file tools stay inside the repo, so a symlink pointing out of it
is refused. The list exists only in the agents' command line, never in a file. An untracked, not-ignored private file
that is not `.env*` needs `--private` or an ignore line. A name with `( ) * ? [ ] { } \` or a newline cannot be put in a
rule: setup stops and names it.

### The settings store: per-project choices, outside the repo
Everything project-setup decides for a project lives in `~/.config/lane-control/projects/<folder>-<hash>/` (under
`$XDG_CONFIG_HOME` when set). The key is the main checkout's folder name plus the first 10 hex of the sha256 of its real
path, so `~/code/x` and `~/work/x` have separate settings, tables, ports and sandbox-ports. project-setup, `opencode-seat`
and the sandbox derive it the same way, not in `.cotal/`, which Cotal owns, and not in the repo:

- `settings.json`: `root` (the checkout, which `lane-tier` uses to find the project's manager), `name`, `models` (harness/model
  per role: coordinator, design-lead, reviewer), `design_lead` (on or off), `permission_mode`, `private` (the `--private`
  globs), `opencode` and `opencode_model` (the OpenCode answer), `own_models`, `port` (the broker's) and `claude_setup`.
  Mode 600 in a 700 folder. Only these names are ever written, and never a secret (keys stay in their own files, e.g.
  the sandbox key file); committed project knowledge stays in `.lane-control/`.
- `lanes.tsv`: the tier table. Lanes reach it by this absolute path, so worktrees do not matter.
- `sandbox-ports`: the host ports the sandbox may forward (below). Its name and format are unchanged.

Machine-wide limits (`lane-limits.sh`, `lane-heavy`, `lane-room`) stay in their own file. Only the rendered personas stay
in `.cotal/agents/`.

A run reads the store: a flag wins over the stored value, which wins over the default, and the flag is stored (`--coordinator`,
`--design-lead`, `--reviewer`, `--permission-mode`, `--name`, `--no-design-lead`, `--own-model`). `--private` adds to the
stored list and never drops an entry; to remove one, edit `settings.json`. `--design-lead` turns a design lead
that was switched off back on. `--refresh` keeps each persona's own harness and model, takes the design lead and mode from the
store, and fills in what the store lacks from the personas.

`project-setup <dir> --show` prints the store (settings, the table's tiers, the sandbox ports) and writes nothing.

**Migration.** A project set up before the store existed has `.cotal/lane-control.json` and `.cotal/lanes.tsv`. The next
`project-setup` run on it, also `--refresh`, moves them once and prints `settings: moved ...`: the record's keys go into
`settings.json`, the table is copied byte for byte (an edited table is kept as it is) and the old files are removed; models,
design lead and permission mode, which the old record never held, are read from the rendered personas. If the store already
has a different table, it wins and the old file stays, with a warning. A store folder keyed on the folder name alone (the
first version, which held `sandbox-ports`) is renamed to the new key, unless its `root` names another checkout. Until a project is moved, `opencode-seat` still reads
the old files, so a machine tool updated first does not break a project not yet moved. `--show` moves nothing and says
what is not moved yet.

### Refresh: after lane-control's personas, skill or tier defaults change
```bash
scripts/project-setup <dir> --refresh [--name Shop]
```
For a project set up earlier. It re-renders the coordinator, design lead (if the project has one) and reviewer
personas in `.cotal/agents/`, each keeping the harness, model and permission mode it already has, and reinstalls the
skill. The project's own `lanes.tsv` in the store is never replaced (it names the rows that differ from the defaults; new default
rows are for the owner to copy over). It starts no adapting or reviewing agent, broker, coordinator or worktree, and
commits nothing. Running agents keep the old persona and skill until restarted, so the last line (`restart: needed
...`) names the ones to stop and start again. A reviewer persona from before it existed is created (from `--reviewer`). All personas are checked before any is
written. Changing an agent's harness or model is not a refresh: `--coordinator`, `--design-lead`, `--no-design-lead` and
`--permission-mode` are refused with `--refresh`.


### Lanes
The coordinator (or the design lead, for its own frontend lanes) follows the `orca-cotal-lanes` skill:
1. Pick the tier per `model-routing.md`, then run `~/.agents/bin/lane-tier <the table your persona names> <tier>` for its harness and model. It refuses a `sandbox required` tier until the running manager uses the launch wrapper (below).
2. Run `orca worktree create --name <key> --parent-worktree active --json`, so the new task nests under the caller in
   the GUI. Wait for `orca-setup-done` when the setup script writes it, start the agent (step 3), and only then close
   the startup shell and the Setup tab (it stays if the marker never appears), so the agent's tab is first.
3. Call `cotal_spawn` with `agent`, `model`, `cwd` = that worktree, role `lane`, an inline `persona`, and the lane
   brief as `prompt`. For OpenCode add `events: false`; for Claude, `launchOptions: {"permission-mode": …}`.
4. The lane reports by `cotal_dm`, and the DM wakes the parent as its own turn.
5. Task cards show state via `orca worktree set --comment/--workspace-status`.
6. From review tier B up, a reviewer (`personas/reviewer.md`) runs as `<key>-review` in the lane's worktree, on a
   different model from the lane's: read-only, given the issue and commit but not the lane's report, findings as
   blocker/major/minor by DM. Blocker and major are fixed, at most two rounds, then the owner decides.

Never `orca terminal send` or Orca `orchestration` on agents: those type into the composer.

### The sandbox per tier
The project's `lanes.tsv` has a `sandbox` column, `required` or `none` (rule: `model-routing.md`, "Sandbox per tier"). A table
with no column, or a row with an empty cell, is `required` for OpenCode tiers and `none` for the rest.

A `required` tier runs inside `scripts/opencode-sandbox.sh`, through a launch wrapper. Cotal's manager starts `opencode`
for every OpenCode seat, so the wrapper is what it runs: `~/.agents/bin/cotal-opencode/opencode`, a link to
`scripts/opencode-seat`, first on the manager's PATH. (Cotal's service schema refuses `COTAL_OPENCODE_BIN` in its
`envRefs`, so the manager's PATH is the way in.) The wrapper puts `opencode/` and `opencode-go/` models in the sandbox,
runs a model bare only when the project says it is yours (a tier row with `sandbox none`, or `project-setup --own-model`,
recorded in the settings store, out of every lane's reach), and refuses a seat with no model, any other model, and any way of
starting `opencode` it does not know. So a model outside the table counts as `required`, even on a direct `cotal_spawn`.

`lane-tier` refuses a `required` tier (exit 4) unless the running manager of that project uses the wrapper, so a tier
never runs bare while a manager runs without it. It also refuses a `required` tier on a model the sandbox cannot run. A
hosted OpenCode model marked `none` is refused as a bad table. `project-setup` installs the wrapper and starts a new or
stopped manager with it. **A manager that was already running keeps its old PATH**: at a natural stop run
`cotal svc stop mgr -s <space> --yes`, then `cotal svc remove mgr -s <space> --yes`, then rerun `project-setup`. Until
then a required tier stays refused and work goes one tier up, or to Rove. The check is one the coordinator runs, Cotal
does not enforce it; `opencode-seat` is the backstop. Cotal's `modelPolicy` cannot replace it today, because a role under
a policy refuses `launchOptions`, which Claude lanes need.

What a sandboxed seat can reach is in the header of `scripts/opencode-sandbox.sh`. It adds, to what a Rove lane has, only
what the agent needs: the Cotal broker's port outward, this seat's server port inward on host loopback (its TUI runs
outside as a client of that server), and read-only this seat's mesh credential and launch material and the Cotal plugin.
It takes away the owner's global OpenCode config, its MCP servers and the Rove plugin, which a seat does not need.
The model can read the credential and use the mesh as its own identity, with the grants the broker enforces. A seat's
branch is the worktree's branch at its first launch, and the model is the one Cotal was asked for. The seat's database
lives in the lane, so a resumed sandboxed seat keeps it only if the same worktree stays.

What has been proven (Cotal 0.145.1, on a throwaway mesh with the wrapper first on the manager's PATH, read from the
seat's own log and database): a hosted seat launched through the wrapper, its `opencode serve` ran only under bubblewrap,
and it joined the mesh from inside the sandbox. On a free model, its kickoff and a live DM reached the model, the turns
succeeded, and its `cotal_status` and `cotal_dm` tools completed over the broker [tested]. Not proven: an outside reader
receiving what the seat sent. `cotal_dm` reported it sent to a CLI sender with no roster row, and `cotal status <seat>`
showed no activity for the status writes, so that command proves nothing here either way. The owner accepts the first
real `required` lane, watched by the coordinator, as the test that its outbound messages arrive: its report DM is the
missing observation.

Host ports: a lane can reach no host port except the broker, unless the project lists one. Lanes may need the UI or API
of a running container, so a project can list specific ports in
`sandbox-ports` in the settings store, one port per line (`#` comments allowed). Only
those are forwarded to the lane's loopback, the same way as the broker. It is closed by default, takes ports only, never
address ranges, and a malformed entry stops the launch. List only test or dev containers, never anything holding real
data. It applies to every sandbox lane of the project, Rove ones too.

The host's global addresses are refused from the list read at launch: an address the host gains later (a rotating public IPv6,
a DHCP change on a public IPv4) is not refused for a long-lived seat. Limits: static-auth meshes with one broker on host
loopback only; a user-auth mesh, a remote broker or a space that requires the event plane is refused. Marking
a model yours is a statement nothing can check: never mark another third party's model (`deepseek/...`) `none` or
`--own-model`.

`project-setup` asks once per project, when it has a terminal: 1 hosted models (sandbox required), 2 own models, offline
or internal gateway (no sandbox), 3 no OpenCode lanes (Claude only). Option 2 lists names from `opencode models`, without
OpenCode's own hosted ones, or takes one typed, and never reads `opencode.jsonc`. It writes no sandbox, so choose it
only for a gateway you run yourself: nothing checks that a listed or typed model really is one. Option 3 sets the OpenCode tiers'
harness to `none`, so their work goes one tier up. Without a terminal, say it with `--opencode hosted|own|none`
(`own` also needs `--opencode-model <provider/model>`) or a full `--tiers`; given neither, it is hosted. A re-run does not
ask again and keeps the table, and `--refresh` keeps both the table and the answer. An explicit `--opencode` on a re-run
replaces the table only if the project has not edited it; otherwise it is refused and the table stays.

The same rule covers the agents setup starts itself: `--coordinator`, `--design-lead` and `--reviewer` refuse every
`opencode/<provider>/<model>` unless `--own-model <provider>/<model>` declares it yours; `--own-model` itself refuses an
`opencode/` or `opencode-go/` model. `--refresh` warns about a persona that already pins a hosted one. An agent set up
earlier on a gateway model needs `--own-model` or a `sandbox none` row before the launch wrapper will start it again.

### Launching OpenCode lanes
- A hosted model runs in the sandbox with a key file of its own, `~/.config/opencode-sandbox/auth.json`
  (`{"opencode-go":{"type":"api","key":"..."}}`, also used for `opencode/` models), so the owner's `opencode auth login`
  does not reach it. Create it once per machine with a key for lanes only, `chmod 600`. `project-setup` warns, with these
  steps, when a `required` tier has no usable file, and a seat stops at launch if the provider rejects the key.
- Setup only sees whether the file is usable. A wrong key stops the launch. A used-up quota does not: the lane joins and
  shows as a silent first turn, with `usage limit exceeded` in the lane's `oc/data/opencode/log/opencode.log`
  (`~/.cache/opencode-lanes/wt-<hash>/`). [tested]
- `OPENCODE_API_KEY` in `mgr`'s environment is forwarded to OpenCode agents outside the sandbox, which only a gateway
  of your own could use; a sandboxed seat never gets it.
- A pinned model the server does not list stops the agent before it joins. The connector prints
  `<model>: GET /api/model does not list it. Refusing before join` into the tab, which closes, so `cotal_spawn` shows
  only `<name> exited on launch`. [tested]
- `opencode models` prints the whole catalog whatever the credentials, so it does not prove a lane can use a model.
- Free models, such as those named `*-free`, need no key. Pin one in the project's `lanes.tsv` only if the owner accepts it
  for that project.

### Permission modes (Claude)
- `auto` is the default: Claude screens each tool call, as `--opt permission-mode=auto` on spawn.
- Every Claude agent also starts with `--allow-dangerously-skip-permissions`, so bypass is in its Shift+Tab mode
  cycle for the owner to switch to; it never starts in bypass unless the project's mode is `bypassPermissions`.
- `bypassPermissions` is an option (non-root only, after accepting its warning once).
- The project's mode is written into the personas, and lanes inherit it (tier table `inherit`).

### The machine coordinator and the window guard
One agent per machine, `machine-coordinator` in the Cotal space `machine`, in its own private folder; `roles.md` has its
job. Projects keep their own spaces and Cotal has no messages between spaces, so orders cross by shell with
`cotal send -s <space> -t coordinator "machine coordinator: ..."`. That works from a shell on this machine under the
space's own signer; it has not been exercised live yet, so step 4 is the first real test. An order that starts
`machine coordinator:` counts like the owner's own words (`personas/coordinator.md`).

The prefix is not authentication. The guard, the machine coordinator, the owner and any agent with a shell on this
machine send under the same login, so the sender cannot be verified. That is why a decision the owner keeps (merging,
closing or stopping a lane, and the like) is always confirmed by the owner in the coordinator's own tab first.

`scripts/window-guard.sh` spends no tokens and runs as a systemd user service. It sends the budget steps to every
project in `COORD_SPACE` and one notice per low-memory episode to the machine coordinator. `COORD_TASK` is no longer
read by it, so a project on Rove, or an old Rove entry for a project that moved, gets no orders. A failed send waits and
is retried each poll for an hour, then the machine coordinator is told if its space answers. A low-memory notice that
cannot be delivered is not repeated while the episode lasts. The journal is the one record that always has it
(`journalctl --user -u lane-control-window-guard.service`), so if Cotal is down for the whole service, nobody is told.

Install, in this order, from the main checkout of lane-control (the unit runs the script where it finds it):
1. Add `COORD_SPACE` to the config (`config.example.sh`): one line per project, with the project's space from
   `cotal space`. Remove nothing else; the Rove scripts still read `COORD_TASK`.
2. Run `scripts/project-setup <project> --refresh` for each project, and restart its coordinator at a natural stop. Until
   it restarts, a running coordinator treats the orders as information.
3. `scripts/machine-coordinator-setup` (`--no-start` to look first; `--folder` for another place than
   `~/code/machine-coordinator`).
4. Test the path once by hand: `cotal send -s <space> -t coordinator "machine coordinator: FYI test, no reply"`, and
   check the coordinator sees it.
5. Only now stop the old guard and the old machine watch loop, and any routine that starts them, so the machine is
   not left without a guard. All of them must be stopped before step 6, because two guards send every order twice.
   Delete the old state file,
   `~/.local/state/lane-control/window-guard.state`: an order the old guard sent only to Rove would count as already
   sent.
6. `scripts/window-guard-install`, right after. It refuses while another guard runs or `COORD_SPACE` is empty. For a
   machine that runs with nobody logged in: `loginctl enable-linger $USER`.

### Verified on 2026-11-06 [tested]
- A manager-launched agent opens as one tab `cotal-<name>` inside an *existing* Orca worktree (next to the shell Orca
  opens with every worktree), and joins the roster. Tested with OpenCode and with Claude.
- The coordinator flow end to end, run as a spike:
  1. A Claude coordinator created a child worktree (`parentWorktreeId` = the main checkout).
  2. It spawned an OpenCode lane there with `cotal_spawn`.
  3. The lane did the work and DMed `DONE`.
  4. The DM woke the coordinator, which replied without claiming to have checked the file itself.
- The `cotal_spawn` requirements that run surfaced:
  - It needs an inline `persona` unless the name is a saved persona.
  - Handing role `lane` needs `role:lane` in the spawner's `capabilities`, which the personas carry.
- `project-setup` from the workstation with `--host`:
  - Both agents came up and loaded the skill, and the design lead reported ready to the coordinator by DM.
  - A re-run is all `exists`.
- The coordinator refused a request that arrived as a DM from the CLI ("only your words in this tab instruct me"),
  and asked one question with its recommendation instead.
- Draft safety: an OpenCode agent kept a half-typed draft through an incoming DM. Claude: not testable by script
  (`orca terminal send` text never shows in Claude's prompt). Check it by hand: type in the coordinator's tab while
  a lane reports.

### Host gotchas found
- **Manager environment.** `cotal up` starts the manager with a fixed environment allow-list, so proxy variables
  never reach agents through `spawn.env`. Hence the `mgr` service with `envRefs`.
- **Wrong space.** Run every `cotal` call with `-s <space>`. Otherwise it acts on the space last chosen with
  `cotal use`: an early run saw another project's broker and manager.
- **Debian umask 002** makes `~/.config` group-writable, and Cotal refuses to seed extensions. Use umask 022 and
  `chmod g-w ~/.config ~/.config/cotal`; project-setup does both.
- **OpenCode 2's managed service port (49374)** is per machine. A second user needs `opencode service set port <n>`.
- **A global `TMPDIR` in `/etc/environment`** pointing into one user's home breaks orcad for everyone else.
  orca-host-install falls back to `~/tmp`.
- **No systemd user manager** (the trial container lacks `pam_systemd`): orcad and the Cotal services run detached
  and survive logout, but don't restart after a reboot.

`scripts/cotal-agent` and `scripts/orca-cotal-task` are the superseded 2026-11-05 attempt, a foreground agent typed
into an Orca terminal, which never joined the mesh. Kept for reference; project-setup replaces them.

## 6. What was verified
| Check | Result |
|---|---|
| Coordinator (OpenCode 2.2.1, free model) starts two lanes with `cotal_spawn`, lanes write files and DM back | pass, three live Orca tabs [tested] |
| Peer message while a draft is typed in the agent's input box (Orca runtime, OpenCode 2.2.1) | pass: the message ran as its own turn (the coordinator answered with `cotal_dm`) and the draft stayed in the input box; same result earlier with Cotal's own pty runtime [tested] |
| Agents survive the desktop disconnecting | Orca docs say so [docs]; agents kept running while Cotal's manager restarted [tested] |
| Stopping and respawning agents from Cotal | pass [tested] |
| A Cotal-spawned OpenCode agent uses a custom provider from an on-disk config (project `opencode.json`, a local stand-in model; the model was called) | pass [tested]; the `~/.config/opencode` layer itself was not tested |

## 7. Gotchas from the trial
- **Stale credentials per agent.** Cotal gives every OpenCode agent its own database
  (`<space>/.cotal/opencode/<name>/opencode.db`, via `OPENCODE_DB`), and OpenCode 2 keeps credentials in that
  database. An agent created while a bad key was around keeps it, and respawning under the same name reuses it
  ("This API key has expired … Signing in again will not renew this key"). Fix: stop the agent, move
  `.cotal/opencode/<name>/` aside, spawn again.
- A copied `~/.local/share/opencode/auth.json` (OpenCode 1.x format) is still read by 2.x and overrides anonymous
  free-model access with whatever key it holds.
- OpenCode 2.x runs a background service (`opencode serve --service`) that outlives terminals; restart it after
  credential changes. When killing it by pattern, use `pgrep -f "[o]pencode.exe serve"` so the pattern does not
  match your own shell.
- The OpenCode TUI (bun) loads a native library from `$TMPDIR`: a `noexec` `/tmp` breaks it (set `TMPDIR`).
- Cotal's agents do not inherit the manager's environment: anything they need (proxy, extra keys) goes in
  `spawn.env`.
- Both Orca and Cotal send usage telemetry by default.

## 8. Rove verbs, nearest equivalent
| Rove (`rove api …`) | Orca | Cotal |
|---|---|---|
| `add` (coordinator) | open a worktree, start the agent in a terminal | `cotal spawn -d -n … -C <dir>` |
| `add` (lane) | `orca worktree create` (+ parent) + `orca terminal create` | the coordinator's `cotal_spawn` |
| `send` | `orca terminal send` (types into the terminal) | `cotal send -t`, `cotal_dm` (as a turn) |
| `list`, `get-task` | `orca terminal list`, `orca worktree current` | `cotal ps`, `cotal who` |
| `read-output` | `orca terminal read` | `cotal history --dms`, the agent's tab |
| wait on a lane | `orca terminal wait --for tui-idle\|exit` | lane DMs its coordinator when done |
| `delete` / archive | close the terminal / remove the worktree | `cotal stop <name>` |
| `pr-attach`, per-lane branch | worktree per lane (linked PR shown per worktree) [docs] | none |
| `gate-raise` / `gate-answer` | orchestration gates (experimental) [docs] | **[none]**: ask in a DM |
| `routine-create` | **[none]** | **[none]**: cron or systemd timers on the host |
| budget feed | **[none]** | **[none]** |

beads (`bd`), `gh` and the AGENTS.md templates are unaffected.

## 9. Evaluation checklist
1. Connect a real VPS from the workstation; confirm agents keep running when the laptop sleeps or the link drops.
2. One project, Orca alone: coordinator plus two lanes, each in its own child worktree; judge the sidebar.
3. Same project with Cotal: does the coordinator run lanes on its own; do messages ever disturb what you type.
4. Worktrees: Orca makes one per task natively; Cotal never creates one (it runs an agent in the folder it is given,
   and the Orca runtime only looks up the enclosing worktree). **Untested combination worth trying first:** the
   coordinator creates a child worktree per lane (`orca worktree create … ` with its own worktree as parent), then
   starts the lane with Cotal inside that folder (`cotal spawn -C <worktree path>`, or `cotal_spawn` if its tool takes
   a working directory). That would give one branch per lane, lanes nested under the coordinator in Orca's sidebar, and
   Cotal's draft-safe messaging. Check that the coordinator can run `~/.orca/cli/bin/orca` on the host.
5. Check telemetry is off in both, and which hosts the box contacted (proxy log).
