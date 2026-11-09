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

If it prints nothing, or only plain `orca` was found and `orca --version` does not answer as the Orca CLI, stop and
tell the owner. Every command runs in a new shell, so a variable would not survive: write that absolute path, in double quotes, in place of
`$ORCA` in each `orca` command below.

Never type into an agent's terminal: never use `orca terminal send` on an agent, and do not use Orca's
`orchestration` skill (task-create, dispatch --inject, check --wait). Those type into the composer; Cotal DMs do not.

## 1. Pick the tier, then harness and model
Tier by the paths the task touches (lane-control model-routing: highest tier wins, in doubt one tier up; swift only
for prose docs, scripts without private data and CI). Then look the tier up in the tier table file your persona names (the project's `lanes.tsv` in its settings store under
`~/.config/lane-control/projects/<project>/`, by absolute path, because lane worktrees do not carry it): run `~/.agents/bin/lane-tier <table> <tier>`, which prints the tier's harness, model, permission and sandbox.

A tier whose harness is `none` is skipped: `lane-tier` exits 3, go one tier up. Exit 4 means the tier is `sandbox required`
and cannot run sandboxed right now (it says why: most often the running manager does not use the launch wrapper yet, and
the owner must restart it at a natural stop): never start a refused tier with `cotal_spawn` and never edit the table to
get past it; route the work one tier up, or to Rove if the project has it, and tell the owner in your next line. A
`sandbox required` tier that lane-tier allows runs in the sandbox by itself: spawn it like any OpenCode lane. Any other non-zero exit: hold the lane and tell the owner what it printed. `inherit` means the project's Claude permission mode, which
your persona names; pass that value.

## 2. Open the lane
First check that the machine has room: run `~/.agents/bin/lane-room`. If it exits non-zero (too busy, or not installed),
hold the lane and tell the owner what it printed, instead of starting it. Then run `git status -sb` in the main
checkout: if it is detached, on a branch other than the default, or behind origin, hold the lane and tell the owner
(behind only and clean: `git merge --ff-only origin/<default>` is fine).

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
2. In `$ORCA terminal list --json`, note the handle of every terminal whose `worktreePath` is the new worktree. Then
   start the agent with the `cotal_spawn` tool:
   - `name`: the lane key slug (same as the worktree name)
   - `agent`: the tier's harness (`opencode` or `claude`); `model`: the tier's model
   - `cwd`: the worktree path from above
   - `role`: `lane` (your persona holds `role:lane`, which is what allows handing it on)
   - OpenCode: `events: false`. Claude: `launchOptions: {"permission-mode": "<the project's mode>", "allow-dangerously-skip-permissions": ""}` (the second puts bypass in the Shift+Tab mode cycle for the owner, without starting in it)
   - `persona`: inline, `{ "prompt": "You are a lane worker for <PROJECT>, started by <you>. Work only in your Orca worktree. Report once by cotal_dm to <you>.", "subscribe": [] }`
   - `prompt`: the lane brief (`brief.md` next to this skill, filled in)

   The agent opens as a `cotal-<name>` tab inside that worktree. Confirm with `cotal_roster` that it joined; if the
   spawn is refused, report the refusal to the owner rather than working around it.
3. Only then close the plain shell and the Setup tab, and only among the handles you noted in step 2, so the agent's
   own tab can never be closed. The titles are not reliable, so a terminal is the Setup tab if its `paneTitle` is
   "Setup", or its title or preview contains `setup-runner.sh`. Close the plain shell only if it has no
   `agentIdentity`, its title does not start with `cotal-`, and its preview is exactly one line (the prompt, however it
   looks; an empty preview or more lines mean output or a command). Close the Setup tab only if the marker from step 1
   appeared. Use `$ORCA terminal close --terminal <handle> --tab --json`. If unsure, leave a tab open. If the marker
   never appeared, keep the Setup tab open, read it with `$ORCA terminal read --terminal <handle>` and tell the owner.
   With no marker in `orca.yaml` nothing says the script finished, so keep the Setup tab.
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

1. Pick its tier and model as your persona says (a different model from the lane's), from the tier table, with `lane-tier` as in section 1 (a refused tier is never used).
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
scope. Merge only when the owner says so.

Close a lane once its PR is merged, without asking, if its worktree is clean (`git status --ignored` shows nothing but known build output and the `.beads` symlink; any other ignored
file, such as a local `.env` or notes, goes to the owner first), pushed, and its
branch is an ancestor of the branch its PR merged into: run `git fetch origin`, then
`git merge-base --is-ancestor <branch> origin/<base>` (`<base>` is main, or the design lead's branch for a design
lane). The lane's issue is already
closed at merge. Do these in order, and set nothing else:

```
cotal_despawn <key-slug>-review   (only if the reviewer is still running)
cotal_despawn <lane name>         (or: cotal stop <name>)
$ORCA worktree rm --worktree path:<worktree> --json
```

Never automatic, ask the owner instead: a lane whose work did not merge, uncommitted changes, `--force`, and any
check above that fails. Closing a lane never deletes its branch: merged branches stay unless the owner says to
delete them. If `worktree rm` prints a warning about an older host's branch policy, check the branch and tell
the owner. A reviewer is stopped after its last round (section 5), not left to idle.

## The machine coordinator
Each project is its own Cotal space, and one agent per machine, `machine-coordinator` in the space `machine`, works
across them; the window guard sends budget orders the same way. Both use `cotal send -s <space> -t coordinator`, and
an order starts `machine coordinator:`. Your persona says what such an order counts as; when one tells lanes to stop
or push WIP, pass it to them by `cotal_dm`. Reach the machine coordinator with
`cotal send -s machine -t machine-coordinator "<project>: <one line>"`.

## A lane that lost the mesh
A Claude lane that shows "Cotal incoming auto-wake is unavailable", or whose Cotal tools became the personal
`cotal_connect` set, has lost the mesh, though the roster may still show it idle. Switching back does not restore it.
On the owner's word: `cotal_despawn` it, spawn a new agent under a new name in the same worktree, and give it the
lane's state (branch, commits, what is done and open). Possible cause, not proven: the owner used Claude's built-in
agent switcher. The "← opens agents" option in `/config` (machine-wide) removes that key.
