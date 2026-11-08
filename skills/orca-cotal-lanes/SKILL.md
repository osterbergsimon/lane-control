---
name: orca-cotal-lanes
description: Open, brief, watch and close worker lanes as Orca child tasks, each run by its own Cotal agent (OpenCode or Claude, chosen by tier). Use when you are a project coordinator or design lead and need to start work in a new lane, route it to a harness and model, mirror its state in the Orca GUI, or close it. Use it instead of Orca's orchestration skill.
---

# Lanes: Orca worktrees with a Cotal agent in each

A lane is one Orca worktree (a child task in the Orca GUI, nested under whoever opened it) plus one Cotal agent
running in it. You talk to a lane only by Cotal DM; it reports back by DM, and the DM wakes you as its own turn. The
`orca-cli` skill has the full Orca CLI reference.

Plain `orca` on PATH can be another program (on Linux GNOME's screen reader; it may also be OrcaSlicer). Find the real
CLI first: the first that exists and is executable of `$ORCA_CLI`, `~/.orca/cli/bin/orca`, `orca-ide` on PATH,
`~/.config/orca/linux-orca-cli-shim/orca`, and last `orca` on PATH (the same order as `scripts/project-setup`). Run this once and note the
absolute path it prints:

```
for c in "$ORCA_CLI" ~/.orca/cli/bin/orca "$(command -v orca-ide)" ~/.config/orca/linux-orca-cli-shim/orca "$(command -v orca)"; do [ -n "$c" ] && [ -x "$c" ] && { echo "$c"; break; }; done
```

Every command runs in a new shell, so a variable would not survive: write that absolute path, in double quotes, in place of
`$ORCA` in each `orca` command below.

Never type into an agent's terminal: never use `orca terminal send` on an agent, and do not use Orca's
`orchestration` skill (task-create, dispatch --inject, check --wait). Those type into the composer; Cotal DMs do not.

## 1. Pick the tier, then harness and model
Tier by the paths the task touches (lane-control model-routing: highest tier wins, in doubt one tier up; swift only
for prose docs, scripts without private data and CI). Then look the tier up in the tier table file your persona names (the project's `.cotal/lanes.tsv`, by absolute
path, because lane worktrees do not carry it). Each row gives the harness, model and permission for a tier.

A tier whose harness is `none` is skipped: go one tier up. `inherit` means the project's Claude permission mode, which
your persona names; pass that value.

## 2. Open the lane
From your own worktree (its cwd makes the new task your child):

```
$ORCA worktree create --name <key-slug> --parent-worktree active --setup inherit --json
```

Take `result.worktree.path` from the JSON. Right away give the task a readable name, because the slug alone is not
enough in the Orca GUI:

```
$ORCA worktree set --worktree path:<worktree> --display-name "<KEY> <short title>" --json
```

`--setup inherit` runs the repo's `orca.yaml` setup script if it has one.
For independent top-level work the owner asked for, use `--no-parent` instead.

Orca opens a plain shell in the new worktree, and the repo's setup script runs in its own Setup tab. Orca's CLI cannot
reorder tabs, so the agent's tab comes first by closing those two after the agent has started, never before: an empty
worktree may refuse the spawn and strand the agent's name. Do these in order:

1. If the worktree's `orca.yaml` ends by writing `orca-setup-done`, wait for it, so the agent never runs checks on
   missing dependencies:
   `until [ -e "$(git -C <worktree> rev-parse --git-dir)/orca-setup-done" ]; do sleep 5; done` (at most 10 minutes).
2. Start the agent with the `cotal_spawn` tool:
   - `name`: the lane key slug (same as the worktree name)
   - `agent`: the tier's harness (`opencode` or `claude`); `model`: the tier's model
   - `cwd`: the worktree path from above
   - `role`: `lane` (your persona holds `role:lane`, which is what allows handing it on)
   - OpenCode: `events: false`. Claude: `launchOptions: {"permission-mode": "<the project's mode>", "allow-dangerously-skip-permissions": ""}` (the second puts bypass in the Shift+Tab mode cycle for the owner, without starting in it)
   - `persona`: inline, `{ "prompt": "You are a lane worker for <PROJECT>, started by <you>. Work only in your Orca worktree. Report once by cotal_dm to <you>.", "subscribe": [] }`
   - `prompt`: the lane brief (`brief.md` next to this skill, filled in)

   The agent opens as a `cotal-<name>` tab inside that worktree. Confirm with `cotal_roster` that it joined; if the
   spawn is refused, report the refusal to the owner rather than working around it.
3. Only then close the plain shell and the Setup tab. In `$ORCA terminal list --json`, look at the terminals whose
   `worktreePath` is the new worktree. The titles are not reliable, so a terminal is the Setup tab if its
   `paneTitle` is "Setup", or its title or preview contains `setup-runner.sh`. Close the plain shell: no `agentIdentity`, a title
   that does not start with `cotal-`, and a preview of one line at most (the prompt, however it looks; more lines
   mean output or a command). Close the Setup tab only if the marker from step 1 appeared. Use
   `$ORCA terminal close --terminal <handle> --tab --json`. If unsure, leave a tab open. If the marker never
   appeared, keep the Setup tab open, read it with `$ORCA terminal read --terminal <handle>` and tell the owner. With
   no marker in `orca.yaml` nothing says the script finished, so keep the Setup tab.
4. Give the owner a shell after the agent's tab: `$ORCA terminal create --worktree path:<worktree> --json`.

## 3. Mirror the lane in the GUI
Keep the task card honest so the owner sees state at a glance:

```
$ORCA worktree set --worktree path:<worktree> --comment "<KEY> <tier> <harness>/<model>: <one line>" --json
$ORCA worktree set --worktree path:<worktree> --workspace-status in-review --json    # when it reports succeeded
```

## 4. Wait
After starting every independent lane, tell the owner in one line which lanes run, and end your turn. Do not poll:
the lane's report arrives as a DM and wakes you. Reply to a lane with `cotal_dm` when it needs an answer.

## 5. Review a lane
When your persona says a lane gets a reviewer, start one in the lane's own worktree, so its tab sits in the lane's
task. It reviews read-only and reports to you by DM.

1. Pick its tier and model as your persona says (a different model from the lane's), from the tier table.
2. Read the reviewer persona (the path your persona names) and take its body: everything after the second `---`.
3. `cotal_spawn`:
   - `name`: `<key-slug>-review`; `role`: `reviewer` (your persona holds `role:reviewer`)
   - `agent`, `model`: the reviewer's tier; `cwd`: the lane's worktree path
   - OpenCode: `events: false`. Claude: `launchOptions: {"permission-mode": "<the project's mode>", "allow-dangerously-skip-permissions": ""}` (the second puts bypass in the Shift+Tab mode cycle for the owner, without starting in it)
   - `persona`: `{ "prompt": "<the reviewer persona body>", "subscribe": [] }`
   - `prompt`: `Review <KEY> (bd <ISSUE_ID>): branch <BRANCH>, base <BASE>, commit <SHA>. Send your findings to
     <you> by cotal_dm.` Never include the lane's report.
4. When its findings arrive: send the `blocker` and `major` ones to the lane by `cotal_dm`, word for word. When the
   lane reports a new commit, DM the reviewer `Round 2: commit <SHA>`. After the last round:
   `cotal_despawn <key-slug>-review`.

## 6. Check and close
Before you call a lane good: look at its diff in the worktree, rerun its checks there, look for files outside its
scope. Merge, then close the lane only when the owner says so for that lane:

```
cotal_despawn (or: cotal stop <name>), for the lane and any reviewer still running
$ORCA worktree rm --worktree path:<worktree> --json
```

Never remove a worktree with uncommitted changes without asking.
