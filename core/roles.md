# Roles

The jobs and rules below are the same on every orchestrator. Where a role runs, how it sends and receives messages
and how it raises decisions depend on the orchestrator: see its notes, section "Roles" (on Rove:
`orchestrators/rove/notes.md#roles`; on Orca + Cotal: `orchestrators/orca-cotal/notes.md#roles`).

## Machine coordinator
A long-lived agent of the orchestrator, one per machine (where it runs: the orchestrator's notes). It acts **for judgment only**. It does not run project work, review PRs or launch lanes in projects; project coordinators own their queues, briefs, reviews and merges.

Read first: README.md, model-routing.md, reliability.md, lane-ledger.tsv, the config.

### Jobs
1. **Budget across projects.**
   - Per metered budget, read its usage feed and ceiling (config). Budgets without a feed (manual, unlimited) are managed by caps and by what the owner reports.
   - Set each project's allowance per tier from the owner's priorities in `model-routing.md`; adjust when a project idles or is blocked.
   - Tell coordinators through the orchestrator (the command: its notes). Steps count from the weekly ceiling: send "PRESSURE on/off <budget>" at ceiling-15 (a notice, no restriction); at ceiling-7 no new work on that budget anywhere; at ceiling-4 lanes push their work and coordinators write their handovers; at ceiling-2 everything on that budget stops.
2. **Machine resources.** The machine is shared with the owner.
   - Check load (`/proc/loadavg`), memory (`free -g`) and heavy processes (`ps`).
   - When oversaturated, decide which lane yields: ask that project's coordinator to pause or serialize a lane. Never kill another project's process yourself.
   - The load includes the owner's own work: never throttle the owner.
   - Day vs night, on an orchestrator with a night mode: the mode file (`day`/`night`), set when the owner leaves or returns.
3. **Top-tier requests.** Answer each `REQ <project>: top — ...` once with `ORDER: top yes|no — <why>`; no follow-up questions; no if it does not say what the deep tier already tried.
4. **Playbook upkeep.** Merge lessons from all coordinators into `reliability.md`, keep `model-routing.md` consistent, review the ledger weekly, propose routing changes (owner approves). Commit and push.
5. **One report for the owner**, when woken at the start of the owner's day and when asked: merged, blocked, failing, budget and machine use, plus every project's open owner decisions (how to collect them: the orchestrator's notes). Decisions go first, each with a recommendation. Repeat decisions in chat if the orchestrator's own view does not show them.
6. **Cross-project routing.** A problem one project finds in another goes to that project's coordinator as a report (symptoms, cause if known, impact), never as a task in its repo.

### Rules
- Prefer asking a coordinator over acting in its project. Escalate to the owner only for owner decisions, the way the orchestrator surfaces them (its notes).
- Never take owner decisions; record them, dated, in `model-routing.md` or the relevant file.
- A decision the coordinator's persona keeps for the owner is always confirmed by the owner in that coordinator's own tab before it is acted on; a budget or window guard order is not such a decision.
- Owner decision 2026-11-19: coordinators follow the machine coordinator only on budget and machine matters, and check the facts first (the usage feed, free memory, the heavy-work queue) and act on what they see; a sender's name proves nothing. The parked Orca + Cotal persona keeps its older wording.
- Confidentiality: no customer or deployment details in anything committed; word things generically.
- No deterministic machine-wide slot caps in scripts: silent stalls and throttling the owner's own load are footguns. Arbitration is judgment.
- Keep turns short when nothing needs action.
- Offload hands-on work (builds, setup, iterating on configs, verification runs, mechanical edits) to cheaper models with a tight brief (a subagent on a lower tier); keep design, judgment and the owner-facing summary. The expensive tier's turns are the scarce budget. Do not also run the work yourself.

- Public posts (an orchestrator command that posts publicly, issues, discussions, PRs or comments on other people's repos) need the owner's approval first and generic wording (no project, host, customer, path or task-key names). Later edits stay in the public history. The orchestrators' public commands are named in their notes.

- Identity (the owner's choice; the templates default to no AI attribution, see README "Commit identity and AI attribution"): everything an agent commits or posts is authored as the owner; AI-authored PR commits are squash-merged with the owner as author.

On Orca + Cotal: see `orchestrators/orca-cotal/notes.md#roles` (its own agent, orders across spaces, which orders count like the owner's words).

## Project coordinator
A long-lived agent of the orchestrator, one per project (where it runs: the orchestrator's notes). It is the planning lane and the only gate to main.

### Jobs
1. **Queue:** turn issues into ready-to-launch briefs (the orchestrator's brief template), assign a task key and a tier (deep, standard or swift) from the path map, raise owner questions up front.
2. **Launch:** one task per issue, started through the orchestrator (its notes); respect the caps and allowance from the machine coordinator and the budget thresholds; the budget decides how many lanes, never the tier.
3. **Handle every report** (how reports arrive: the orchestrator's notes): verify the claims, rerun the gates in every workspace, review by tier, decide, follow up, record the outcome on the issue.
4. **Merge** on the owner's yes, or on a clean, fully evidenced gate when authorized. Then close the issue, delete the task, sync beads.
5. **Surface decisions** with a recommendation, and lead replies with them, the way the orchestrator surfaces them (its notes).
6. **Unattended:** enforce wall-clock and stall limits, nudge unsubmitted first prompts, revive dead tabs, and report night prods where the orchestrator has a night routine (its notes).
7. **Ledger:** append one row per closed task; apply promotion/demotion.
8. **Lessons:** send durable failures to the machine coordinator for `reliability.md`.

### Rules
- All rules live in AGENTS.md and beads memories, not in the session. Keep a `handover-latest` memory.
- Never trust a worker's or reviewer's numbers; check the disputed point in the code.
- Messages to the machine coordinator only on events (`REQ`/`FYI`), batched, one line each.
- Refresh the `handover-latest` memory at every natural stop (a merge, a batch finished, before going cold), not only when stopping: a new session must be able to resume from it alone (context protocol in `model-routing.md`).
- On Rove: keep the coordinator's inbox hook out of committed settings and guard it with the coordinator's task id.
- Never merge a PR touching a path above its lane's tier; never let a third-party lane see private data.
