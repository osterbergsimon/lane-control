# Orca + Cotal: setup

Moved from the README. Orca + Cotal is parked (`record.md`): this is for switching a project back or retesting it.
The full runner guide is `orchestrators/orca-cotal/docs/orca.md` (§5b: `project-setup`); the parts, bring-up checks and failure modes are in
`orchestrators/orca-cotal/docs/orca-cotal-setup.md`.

## Requirements
Always: beads (`bd`), `gh` (authenticated), `jq`, `git`, bash, and Claude Code (or whichever engines your tiers use),
logged in. Then what `scripts/project-setup` needs (details and versions in `orchestrators/orca-cotal/docs/orca.md` §5b):
- Orca (the desktop app on your workstation, with Orca's server on a remote host if the agents run there) and Cotal (`cotal-ai`, with its Orca extension: `cotal ext add @cotal-ai/orca`)
- the agent harnesses you use (`@anthropic-ai/claude-code`, `@opencode/cli`), signed in once as the user the agents run as
- a normal user, never root; a git identity configured in the repo

## Quickstart
One run of `project-setup` per project; the rest is in `orchestrators/orca-cotal/docs/orca.md` §5b.
```bash
git clone https://github.com/osterbergsimon/lane-control ~/code/lane-control
~/code/lane-control/scripts/project-setup <git-url | /abs/dir>
```
It does each step only if it is not done yet. For a repo it has not onboarded, an agent adapts the lane files to the project (`.lane-control/`, a section in `AGENTS.lane-control.md` with one pointer line to it in `AGENTS.md`: `../README.md`, "What lane-control writes into a project") and a second agent reviews them (a full audit first, then only checks that the fixes held; a safety finding still open after the last fix stops the run). It reads the repo's visibility (public, private or internal) from the git host rather than guessing it. If questions are still open it shows them and lets you answer each in plain text before anything is committed (Enter leaves a question for later: the agent's choice stays and the coordinator asks you; answers go to one more adapter run that is not reviewed, and the list says so). It then lists the files and asks you once; on yes it commits them, by explicit path. A second question follows: if the repo has no `orca.yaml` it shows the setup script it drafted from the lockfiles (with a `direnv allow` line when a tracked `.envrc` is approved in your checkout) and offers to commit it and push, because Orca makes lane worktrees from `origin/<branch>`. Whatever you answer there, the coordinator (and the design lead) start after, with no flag; only a no to the file list stops the run. Interrupting is safe: an unfinished adapter run is run again, and a re-run asks only what is still open. `--no-start` skips starting the agents. Run `scripts/project-setup --help` and see `orchestrators/orca-cotal/docs/orca.md` for host setup, `--host`, models and permission modes.

Also on Orca + Cotal: the config with `COORD_SPACE` and then the window guard (steps 1 and 2 of
`orchestrators/rove/setup.md`; order in `orchestrators/orca-cotal/docs/orca.md`), and beads set up by hand (`bd init`). `scripts/orch setup
<project> --orchestrator orca-cotal` runs `project-setup --no-start` for you in the main checkout, says first that it
commits on its own and runs its adapting and reviewing agents, and prints how to start the coordinator afterwards.

## Files
`scripts/project-setup` (an Orca project with a Cotal coordinator and frontend design lead, here or on an SSH host;
one run onboards the repo: an agent adapts the lane files, the owner says yes once to commit them, a second question
offers to commit a drafted `orca.yaml` and push, and the agents start after; safe to re-run or interrupt),
`scripts/project-pause` (`rest` and `wake` leave a project alone so the window guard stops waking it, `stop` and
`start` also free its agents' memory), `scripts/machine-coordinator-setup`, `scripts/orca-host-install` (Orca's
server on a host without downloads, optional scripted pairing); superseded trial scripts `cotal-agent`,
`orca-cotal-task`. `orchestrators/orca-cotal/personas/`, `orchestrators/orca-cotal/skills/orca-cotal-lanes/`, `orchestrators/orca-cotal/templates/lanes.tsv` and `orchestrators/orca-cotal/templates/lane-brief.md` are
what project-setup installs: Cotal personas (coordinator, design lead), the lane skill (Orca child task + Cotal agent
per lane), the tier table (harness and model per tier), the lane brief. `orchestrators/orca-cotal/templates/adapt/` holds the adapting agent's
prompts and the shapes of the project's `.lane-control/` files.
