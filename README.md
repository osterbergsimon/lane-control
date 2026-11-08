# lane-control

A coordinator platform on top of an agent task runner, [Rove](https://github.com/Sma1lboy/rove) or [Orca](https://github.com/stablyai/orca) with [Cotal](https://github.com/Cotal-AI/Cotal): scripts, templates and written doctrine for keeping several software projects moving with coding agents, unattended, within a budget. You decide only what matters; the rest is queued, launched, reviewed and merged by agents that verify each other's claims.

## What it is
Three layers of agents, each a task of the runner you use:
- **Machine coordinator** (optional, one per machine): judgment only. Splits the budget across projects, watches memory and load, runs night mode, approves rare top-tier requests, writes the owner's one report.
- **Project coordinator** (one per repo): the repo's main task. Queues work in beads (`bd`), writes briefs, launches worker lanes, reruns the gates, reviews, merges, raises owner decisions to you (as Rove gates on Rove).
- **Worker lanes**: child tasks of the coordinator (Rove child tasks, or Orca child tasks with one Cotal agent each), one issue, one branch, one PR each, on a **tier**.

Tiers are roles (swift, standard, deep, top), not model names. One config file maps each tier to an engine, command, model and budget, so cheap third-party or local models (through opencode) and Claude models mix freely. Any tier may be "none".

Why: the real constraint is a budget (say a weekly plan limit), not attention. Work is routed by the paths it touches, reviewed by risk, and throttled by lane count, never by lowering the tier. The owner sees one report and a short list of decisions.

What it is not: not a CI system, not a substitute for human review on risky changes, not a hosted service, and not tied to one project or vendor. It assumes a single owner who trusts the agents with their repos.

## Requirements
Two supported runners: Rove, and Orca + Cotal (`docs/orca.md`). [wsp](https://github.com/wsp-labs/wsp) is trial notes only (`docs/wsp.md`). The doctrine is the same on each; the scripts are per runner.

Always: beads (`bd`), `gh` (authenticated), `jq`, `git`, bash, and Claude Code (or whichever engines your tiers use), logged in.

**Rove:**
- Rove, daemon running, with its skill installed for each engine you use (`rove skill install --global --agent <engine>`)

**Orca + Cotal** (what `scripts/project-setup` needs; details and versions in `docs/orca.md` §5b):
- Orca (the desktop app on your workstation, with Orca's server on a remote host if the agents run there) and Cotal (`cotal-ai`, with its Orca extension: `cotal ext add @cotal-ai/orca`)
- the agent harnesses you use (`@anthropic-ai/claude-code`, `@opencode/cli`), signed in once as the user the agents run as
- a normal user, never root; a git identity configured in the repo

Optional for either runner:
- for sandboxed third-party lanes: `opencode`, bubblewrap, pasta, systemd user scopes. The sandbox is tested on NixOS only (it fetches `bubblewrap`, `passt` and `curl` through `nix`).

## Quickstart
### Orca + Cotal
One run of `project-setup` per project; the rest is in `docs/orca.md` §5b.
```bash
git clone https://github.com/osterbergsimon/lane-control ~/code/lane-control
~/code/lane-control/scripts/project-setup <git-url | /abs/dir>
```
It does each step only if it is not done yet. For a repo it has not onboarded, an agent adapts the lane files to the project (`.lane-control/`, a section in `AGENTS.md`) and a second agent reviews them. It then lists the files and asks you once; on yes it commits them, by explicit path. A second question follows: if the repo has no `orca.yaml` it shows the setup script it drafted from the lockfiles and offers to commit it and push, because Orca makes lane worktrees from `origin/<branch>`. Whatever you answer there, the coordinator (and the design lead) start after, with no flag; only a no to the file list stops the run. Interrupting is safe: an unfinished adapter run is run again, and a re-run asks only what is still open. `--no-start` skips starting the agents. Run `scripts/project-setup --help` and see `docs/orca.md` for host setup, `--host`, models and permission modes.

### Rove
About half an hour for one project. Everything marked **planned** is manual today.

1. **Clone and configure.**
   ```bash
   git clone https://github.com/osterbergsimon/lane-control ~/code/lane-control
   mkdir -p ~/.config/rove
   cp ~/code/lane-control/config.example.sh ~/.config/rove/playbook.conf.sh
   $EDITOR ~/.config/rove/playbook.conf.sh    # tiers, budgets, thresholds, coordinators
   bash -n ~/code/lane-control/scripts/*.sh
   ```
   A budget with a usage feed needs a ceiling: copy `templates/budget-ceiling.json` and point `BUDGET_CEILING` at it (`machine.md` explains feeds). Link `model-routing.md`, `reliability.md` and `lane-ledger.tsv` into `~/.config/rove/` if coordinators should find them there.
2. **Start the watches** (background tasks of the machine coordinator, or any terminal multiplexer):
   ```bash
   bash ~/code/lane-control/scripts/window-guard.sh     # never exits; window and weekly-step orders
   bash ~/code/lane-control/scripts/machine-watch.sh    # exits on low memory or budget pressure: re-arm after each wake
   ```
3. **Create the routines.** They run scripts inside the Rove daemon and spend no tokens; times are the daemon host's local time.
   ```bash
   LC=~/code/lane-control
   rove api routine-create --repo "$LC" --name "machine night watch" \
     --schedule '5 22-23,0-6 * * *' \
     --precheck 'grep -qx night "${TMPDIR:-/tmp}/coordinator-mode"' \
     --script "bash $LC/scripts/machine-night-watch.sh"
   rove api routine-create --repo "$LC" --name "beads stale-claim check" \
     --schedule '30 9 * * *' --script "bash $LC/scripts/stale-claims.sh"
   rove api routine-create --repo "$LC" --name "beads park check" \
     --schedule '35 9 * * *' --script "bash $LC/scripts/park-check.sh"
   ```
   The night watch runs only while the mode file (`MODE_FILE` in the config) says `night`: write `night` when you leave and `day` when you return. Run a supervised pilot night before leaving it unattended. An optional weekly docs review: `scripts/docs-review-launch.sh <PROJECT>` with a brief from `templates/docs-review-brief.md`.
4. **Set up a project.**
   - `rove add <path>`: the repo's main task is its coordinator. Put its task id in `COORD_TASK` and the checkout in `COORD_REPO` in the config.
   - `bd init` in the checkout; no bd git hooks (`issues.md`).
   - Write the project's **path map** (which paths need which tier, and which review tier) in `.lane-control/routing.md`, from `templates/adapt/routing.md`; gate commands, private data directories and the other project facts go in `.lane-control/project.md`. The Rove flow pasted `templates/AGENTS-coordination.md` into AGENTS.md instead; the Orca flow does not.
   - Install `templates/project-settings.json` as the project's agent settings, `templates/coordinator-settings.local.json` as the coordinator's gitignored local settings, and `templates/rove-init.sh` as `.rove/init.sh`.
   - For third-party lanes: pin the model in `templates/opencode.json`, list private paths in `SANDBOX_PRIVATE_PATHS`, run `scripts/test-opencode-sandbox.sh` once.
5. **Start the coordinators.** Open the machine coordinator's main task on a repo holding your instance notes (owner decisions, caps, priorities) and have it read `README.md`, `roles.md`, `model-routing.md`, `reliability.md` and the config. Open each project coordinator on its repo, pointing at its `AGENTS.md`. Queue issues, then tell it to go.

**Planned:** a bootstrap script for steps 1 to 3 and the per-project files of step 4, and a doctor command that checks an install against `machine.md`.

### Commit identity and AI attribution
By default Claude Code (local and cloud sessions) signs commits as `Claude <noreply@anthropic.com>` or adds `Co-Authored-By` and session-link trailers, and footers to PR bodies. **You will probably want to turn this off** if commits should carry only your identity:
- commit `templates/project-settings.json` (it sets `"attribution": {"commit": "", "pr": "", "sessionUrl": false}` and `"includeCoAuthoredBy": false`) as the repo's `.claude/settings.json`: cloud sessions read the committed file;
- have every brief set `git config user.name/user.email` to yours before the first commit (the AGENTS template's **Identity** line);
- squash-merge PRs whose commits are AI-authored, with you as author.
Keep attribution if you prefer it; then drop the Identity line from your AGENTS.md and the two keys from the settings.

## Map of files
| File | What |
|---|---|
| `roles.md` | jobs and rules of the machine coordinator and a project coordinator |
| `model-routing.md` | tiers, routing by path map, escalation, limits, weekly and window steps, night mode, message protocol, sandbox conventions, context protocol, cloud sessions |
| `issues.md` | beads doctrine: claim, close, parking, filing bar, weekly grooming, stale claims |
| `reliability.md` | numbered failure, fix, reuse log; read before setting up a coordinator |
| `machine.md` | what is machine-specific; checks for a new box; CI runner patterns |
| `config.example.sh` | the one config the scripts read: tiers, budgets, thresholds, coordinators |
| `scripts/` | `window-guard.sh`, `machine-watch.sh`, `machine-night-watch.sh`, `stale-claims.sh`, `park-check.sh`, `docs-review-launch.sh`, `opencode-sandbox.sh` with `test-opencode-sandbox.sh`, `lib.sh` (shared); machine caps, installed to `~/.agents/bin` by `project-setup`: `lane-heavy` (the machine-wide gate for builds and full test suites), `lane-room` (the check before opening a lane), `lane-tier` (looks up a tier and refuses a `sandbox required` tier under Orca + Cotal), `lane-limits.sh` (their shared limits; a machine's own in a copy of `limits.example.sh`); for Orca + Cotal (`docs/orca.md` §5b): `project-setup` (an Orca project with a Cotal coordinator and frontend design lead, here or on an SSH host; one run onboards the repo: an agent adapts the lane files, the owner says yes once to commit them, a second question offers to commit a drafted `orca.yaml` and push, since Orca makes lane worktrees from `origin/<branch>`, and the agents start after, with no flag; safe to re-run or interrupt) and `orca-host-install` (Orca's server on a host without downloads, optional scripted pairing); superseded trial scripts `cotal-agent`, `orca-cotal-task`; for wsp (`docs/wsp.md`): `wsp-owner-code.mjs`. Each header is its contract |
| `templates/` | AGENTS sections, project and coordinator settings, init script, worker and review briefs, budget ceiling, opencode config |
| `docs/multi-machine.md` | design (not implemented): projects pinned to machines, one global coordinator over SSH |
| `docs/orca.md` | the Orca + Cotal runner (`scripts/project-setup`), with what was tested on a trial box: Orca for SSH remote agents and OpenCode 2, plus Cotal for coordinator-to-lane messaging that never disturbs a half-typed prompt; setup, what was verified, gotchas, Rove verb map, evaluation checklist |
| `personas/`, `skills/orca-cotal-lanes/`, `templates/lanes.tsv`, `templates/lane-brief.md` | what project-setup installs: Cotal personas (coordinator, design lead), the lane skill (Orca child task + Cotal agent per lane), the tier table (harness and model per tier), the lane brief |
| `tests/` | `test_content.py`, `test_project_setup.py` (stub orca/cotal), `test-orca-host-install.sh` (against a real host) |
| `docs/wsp.md` | trial notes: running coordinators and lanes on wsp instead of Rove (setup on an SSH-only box, OpenCode versions, Rove-to-wsp verb map, what has no equivalent); `scripts/wsp-owner-code.mjs` pairs its web UI as owner |
| `lane-ledger.tsv` | one row per finished task, used to calibrate routing |

## Doctrines
- **Truth lives in files**, not sessions: `AGENTS.md`, beads issues and memories. A crashed coordinator rebuilds from them (`roles.md`, context protocol in `model-routing.md`).
- **Verify, do not trust**: coordinators rerun every deterministic gate and check reviewer claims as well as worker claims.
- **Route by paths, review by risk**, correctness over token cost; the budget limits lane count, never the tier (`model-routing.md`).
- **One issue, one key, one lane**; claim at launch, close at merge, park resumably (`issues.md`).
- **Decisions surface to you** (as Rove gates on Rove) with a recommendation; messages are few, terse and event-driven.
- **Scripts enforce only limits the owner decided**; no silent machine-wide caps.
- **Every failure with a durable fix** goes into `reliability.md`.

## Sandbox
`scripts/opencode-sandbox.sh` runs opencode for third-party model lanes in bubblewrap: a deny-by-default filesystem (the lane's worktree, a private git dir, a few read-only system files), its own network namespace through pasta (internet only, no host loopback), a memory cap in a systemd scope, and a relay so the lane can push its own branch and open its PR without holding credentials. `test-opencode-sandbox.sh` checks the denials. Which tiers need it is a per-tier choice (`model-routing.md`, "Sandbox per tier"): global-region models, which includes every hosted OpenCode model, must run in it, and an internal or offline gateway may run without it.

This is a practical boundary for code-only lanes, not a security guarantee. It is tested on one NixOS setup, depends on the host kernel and on bubblewrap and pasta behaving as documented, and has not been independently audited. Review the script and its test before trusting it with hostile code, and keep private material off any machine that runs third-party lanes.

## License
MIT, see `LICENSE`.
