# lane-control

How we run software projects with coding agents, whichever orchestrator starts them: scripts, templates and written doctrine for keeping several projects moving, unattended, within a budget. You decide only what matters; the rest is queued, launched, reviewed and merged by agents that verify each other's claims.

Orchestrators change fast, so the repo keeps what does not depend on one (`core/`) apart from what does (`orchestrators/<name>/`). A project can be set up on any supported orchestrator, moved from one to another without losing its rules, work or history, and a parked orchestrator can be retested later from what we learned last time.

## What it is
Three layers of agents, each run by the orchestrator you use:
- **Machine coordinator** (optional, one per machine): judgment only. Splits the budget across projects, watches memory and load, runs night mode where the orchestrator has one, approves rare top-tier requests, writes the owner's one report.
- **Project coordinator** (one per repo): queues work in beads (`bd`), writes briefs, launches worker lanes, reruns the gates, reviews, merges, raises owner decisions to you.
- **Worker lanes**: one issue, one branch, one PR each, on a **tier**, each in its own worktree.

Tiers are roles (swift, standard, deep, top), not model names. One config file maps each tier to an engine, command, model and budget, so other engines and Claude models can mix. Any tier may be "none". Swift on OpenCode runs unsandboxed only in calvin for now, through calvin's Orca coordinator; for every other project swift is `none` ("Sandbox" below).

Why: the real constraint is a budget (say a weekly plan limit), not attention. Work is routed by the paths it touches (each project's path map, `.lane-control/routing.md` where the orchestrator uses lane-control's lane files), reviewed by risk, and throttled by lane count, never by lowering the tier. The owner sees one report and a short list of decisions.

What it is not: not a CI system, not a substitute for human review on risky changes, not a hosted service, and not tied to one project, vendor or orchestrator. It assumes a single owner who trusts the agents with their repos.

## Orchestrators
| Orchestrator | What it is | Status | Setup |
|---|---|---|---|
| native-orca | Orca worktrees and Orca's own orchestration (run, task, worker), no Cotal | in use | `orchestrators/native-orca/setup.md` |
| orca-cotal | Orca worktrees, Cotal agents and Cotal messages | parked | `orchestrators/orca-cotal/setup.md` |
| rove | Rove daemon, tasks and gates | parked | `orchestrators/rove/setup.md` |
| wsp | wsp host and agents | parked | `orchestrators/wsp/docs/wsp.md` |
| herdr | terminal multiplexer for coding agents | untested | none yet |

Each has a `record.md` (status, dated history, trial results, what would make us retest). `orchestrators/README.md` says how to add one; `orchestrators/checklist.md` is the one checklist every trial and retest uses; `orchestrators/switching.md` is the guide for moving a project.

## The command
```bash
scripts/orch status  <project>                         # which orchestrator the project is on, and what of it is present
scripts/orch setup   <project> --orchestrator <name>   # prepare the project for that orchestrator
scripts/orch strip   <project> --orchestrator <name>   # remove that orchestrator's instructions and config
scripts/orch switch  <project> --from <a> --to <b>     # strip a, set up b, then print the human steps
```
`<project>` is the path to the project's main checkout. Changes land on a new branch in a new worktree, opened as a PR; the main checkout is never touched, and nothing is merged, stopped or started. The one exception is `setup --orchestrator orca-cotal`: it runs `project-setup --no-start` in the main checkout, which commits on its own and runs its adapting and reviewing agents (it says so first, and prints how to start the coordinator). `--dry-run` shows what would change. An action an orchestrator has no adapter for fails before any change.

## Requirements
Always: beads (`bd`), `gh` (authenticated), `jq`, `git`, bash, Python 3, and Claude Code (or whichever engines your tiers use), logged in. The rest is per orchestrator, in its `setup.md`.

## The core
The shared doctrine and scripts are in `core/` (`core/README.md`): roles, model routing, beads doctrine, the reliability log, machine checks, the config, and the machine caps every orchestrator uses: `lane-heavy` (the machine-wide gate for builds and full test suites), `lane-room` (the check before opening a lane), `lane-tier` (looks up a tier in a project's tier table) and `lane-limits.sh` (their shared limits; a machine's own in a copy of `limits.example.sh`). They are installed to `~/.agents/bin` and are reachable at their old paths under `scripts/`.

Away-improve mode lets each project's coordinator keep improving its product on its own `away/<date>` branch while you are away, spending your usage, and keeps one draft PR for you to review (opened when the mode starts, its summary refreshed after every merge): it is off unless you switch it on with `lane-improve on`, and has optional caps (`--max-items`, `--stop-at`). The owner's guide is `docs/away-improve.md`.

A merged lane's worktree is removed by Orca's own "Delete merged workspaces" cleanup rule, not by a script of ours; setup says when that rule is off and where to turn it on, and turns on GitHub's "Automatically delete head branches" on your yes (`docs/orca-cleanup.md`).

The design kit (`docs/superpowers/specs/2026-11-21-design-kit-design.md`) replaces a long-lived design lead: the frontend brief addition (`core/templates/frontend-brief.md`), a project's design file (template `core/templates/DESIGN.md`), the design reviewer (native Orca), the `component-gallery` skill (`core/skills/`) and `design-shots` (headless screenshots at phone and desktop width in both themes, installed with the lane tools). Setup installs the design skills it names for Claude and OpenCode from their upstream repos at pinned commits (`core/design-skills.tsv`); lane-control never copies their content.

### Commit identity and AI attribution
By default Claude Code (local and cloud sessions) signs commits as `Claude <noreply@anthropic.com>` or adds `Co-Authored-By` and session-link trailers, and footers to PR bodies. **You will probably want to turn this off** if commits should carry only your identity:
- commit a `.claude/settings.json` with `"attribution": {"commit": "", "pr": "", "sessionUrl": false}` and `"includeCoAuthoredBy": false` (as in `orchestrators/rove/templates/project-settings.json`; take only those two keys, the rest is Rove-specific): cloud sessions read the committed file;
- have every brief set `git config user.name/user.email` to yours before the first commit (the AGENTS template's **Identity** line);
- squash-merge PRs whose commits are AI-authored, with you as author.
Keep attribution if you prefer it; then drop the Identity line from your AGENTS.md and the two keys from the settings.

## Map of files
| Path | What |
|---|---|
| `core/` | shared doctrine (`roles.md`, `model-routing.md`, `issues.md`, `reliability.md`, `machine.md`), the config and limits examples, the ledger, the machine caps and watch scripts; `core/README.md` lists the old paths kept as links for live machines |
| `orchestrators/` | one folder per orchestrator: `record.md`, `setup.md`, `notes.md` (how it carries out the shared doctrine), its adapter, docs, scripts and templates; plus the status table, the trial checklist and the switch guide |
| `scripts/` | `orch` (the command) and links to every script at its old path |
| `tests/` | `python3 -m unittest discover -s tests`; `test-orca-host-install.sh` runs against a real host |

## Doctrines
- **Truth lives in files**, not sessions: `AGENTS.md`, beads issues and memories. A crashed coordinator rebuilds from them (`core/roles.md`, context protocol in `core/model-routing.md`).
- **Verify, do not trust**: coordinators rerun every deterministic gate and check reviewer claims as well as worker claims.
- **Route by paths, review by risk**, correctness over token cost; the budget limits lane count, never the tier (`core/model-routing.md`).
- **One issue, one key, one lane**; claim at launch, close at merge, park resumably (`core/issues.md`).
- **Decisions surface to you** with a recommendation, the way the orchestrator offers; messages are few, terse and event-driven.
- **Scripts enforce only limits the owner decided**; no silent machine-wide caps.
- **Every failure with a durable fix** goes into `core/reliability.md`.

## Sandbox
Owner 2026-11-15: the OpenCode sandbox is removed to keep things simple; OpenCode may run unsandboxed (calvin, for now). To bring it back, restore it from git history (last in main at b97c919). Swift still exists: OpenCode on `opencode-go/deepseek-v4.1-flash`, unsandboxed, allowed only in calvin for now (owner 2026-11-15). There, calvin's Orca coordinator starts it with `orca-ide orchestration worker-start --agent opencode2 --model opencode-go/deepseek-v4.1-flash`; lane-control's own lane tools do not start OpenCode. Everywhere else there are no OpenCode lanes and no hosted global-region model runs bare: `scripts/lane-tier` refuses every OpenCode tier (exit 4), the default tier table's swift row is `none` (the default for every project but calvin), and re-adding OpenCode lanes elsewhere needs the sandbox back first (restore it from b97c919).

## License
MIT, see `LICENSE`.
