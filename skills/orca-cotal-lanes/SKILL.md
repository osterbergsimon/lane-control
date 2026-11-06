---
name: orca-cotal-lanes
description: Open, brief, watch and close worker lanes as Orca child tasks, each run by its own Cotal agent (OpenCode or Claude, chosen by tier). Use when you are a project coordinator or design lead and need to start work in a new lane, route it to a harness and model, mirror its state in the Orca GUI, or close it. Use it instead of Orca's orchestration skill.
---

# Lanes: Orca worktrees with a Cotal agent in each

A lane is one Orca worktree (a child task in the Orca GUI, nested under whoever opened it) plus one Cotal agent
running in it. You talk to a lane only by Cotal DM; it reports back by DM, and the DM wakes you as its own turn. The
`orca-cli` skill has the full Orca CLI reference.

Never type into an agent's terminal: never use `orca terminal send` on an agent, and do not use Orca's
`orchestration` skill (task-create, dispatch --inject, check --wait). Those type into the composer; Cotal DMs do not.

## 1. Pick the tier, then harness and model
Tier by the paths the task touches (lane-control model-routing: highest tier wins, in doubt one tier up; swift only
for prose docs, scripts without private data and CI). Then look the tier up in `.cotal/lanes.tsv` at the repo root:

```
tier      harness   model                          permission
swift     opencode  opencode/deepseek-v4.1-flash   -
standard  claude    sonnet                         inherit
deep      claude    opus                           inherit
```

A tier whose harness is `none` is skipped: go one tier up. `inherit` means the project's Claude permission mode, which
is in your own persona's `launchOptions` (`permission-mode`); pass the same value.

## 2. Open the lane
From your own worktree (its cwd makes the new task your child):

```
orca worktree create --name <key-slug> --parent-worktree active --setup inherit --json
```

Take `result.worktree.path` from the JSON. `--setup inherit` runs the repo's `orca.yaml` setup script if it has one.
For independent top-level work the owner asked for, use `--no-parent` instead.

Then start the agent with the `cotal_spawn` tool:

- `name`: the lane key slug (same as the worktree name)
- `agent`: the tier's harness (`opencode` or `claude`); `model`: the tier's model
- `cwd`: the worktree path from above
- `role`: `lane` (your persona holds `role:lane`, which is what allows handing it on)
- OpenCode: `events: false`. Claude: `launchOptions: {"permission-mode": "<your mode>"}`
- `persona`: inline, `{ "prompt": "You are a lane worker for <PROJECT>, started by <you>. Work only in your Orca worktree. Report once by cotal_dm to <you>.", "subscribe": [] }`
- `prompt`: the lane brief (`brief.md` next to this skill, filled in)

The agent opens as a `cotal-<name>` tab inside that worktree. Confirm with `cotal_roster` that it joined; if the
spawn is refused, report the refusal to the owner rather than working around it.

## 3. Mirror the lane in the GUI
Keep the task card honest so the owner sees state at a glance:

```
orca worktree set --worktree path:<worktree> --comment "<KEY> <tier> <harness>/<model>: <one line>" --json
orca worktree set --worktree path:<worktree> --workspace-status in-review --json    # when it reports succeeded
```

## 4. Wait
After starting every independent lane, tell the owner in one line which lanes run, and end your turn. Do not poll:
the lane's report arrives as a DM and wakes you. Reply to a lane with `cotal_dm` when it needs an answer.

## 5. Check and close
Before you call a lane good: look at its diff in the worktree, rerun its checks there, look for files outside its
scope. Merge, then close the lane only when the owner says so for that lane:

```
cotal_despawn (or: cotal stop <name>)
orca worktree rm --worktree path:<worktree> --json
```

Never remove a worktree with uncommitted changes without asking.
