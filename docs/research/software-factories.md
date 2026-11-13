# Software factories, and lane-control as a framework (research, 2026-11-13)

## Summary
- **What it means now.** A "software factory" is a pipeline where coding agents turn a spec or a ticket into merged,
  checked code. The people set intent and acceptance criteria and review less or no code. The term is used loosely:
  at one end it is any agent pipeline with a PR review; at the other, StrongDM's "dark factory" with no human code
  review at all.
- **What the evidence says.** Measured productivity gains are weak or negative (METR). Where agents raise throughput,
  the cost moves to review and quality. Agents game visible tests. Parallel writers on shared code fail more often than
  one writer with independent reviewers. The setups people report as working are the same shape every time: a sandbox,
  small tasks, a clean-context reviewer, gates the agent cannot see or edit, a budget, and a human at the merge.
- **lane-control already is a small software factory** of exactly that shape. It has intake (beads), planning (the
  coordinator), routing (tiers, path map), isolation (a worktree per lane, a sandbox for third-party models), review by
  risk with re-run gates, a merge only on the owner's word, budget steps and machine caps, and a failure log. Two parts
  are unusually strong compared with the products surveyed: **routing by tier and path**, which no product documents,
  and **budget control tied to a real usage feed**.
- **What it lacks for a framework:** portability (it assumes Orca + Cotal or Rove, Claude Code and OpenCode, a NixOS
  sandbox and one owner), a defined runner interface, packaging and releases, CI on this repo, a config schema, and
  measurement (the ledger in this repo has no rows, and the Orca path never writes one).
- **Recommendation: option A+**, stay a personal toolkit, and take the few factory practices with evidence behind
  them: CI on this repo, a ledger that is actually filled, acceptance checks the lane cannot see, and a written runner
  interface. Revisit a framework (option B) only after a few weeks of ledger data show the method beats a plain
  single-agent workflow. A full framework (option C) would compete with funded products on their ground and spend the
  owner's budget on maintenance rather than on projects.

## How this was researched
Web search and page reads on 2026-11-13, with two research passes (concept and products; evidence of what works and
fails), then a read of this repo at `main` (6f2e7b1). Markers: **[read]** = the primary page was read; **[secondary]**
= only a secondary summary or search snippet was seen, not verified against the primary. Dates are the source's own
publish date; "no date" means the page shows none. Vendor numbers about their own products are reported as claims,
not facts.

## 1. What "software factory" means

### Today, for coding agents
- **Dan Shapiro, "The Five Levels", 2026-01-23 [read].** A self-driving analogy: level 3 is a human managing a stream
  of AI code ("almost everyone tops out here"), level 4 a spec writer who checks that tests pass, level 5 the "dark
  factory", "a black box that turns specs into software".
  https://www.danshapiro.com/blog/2026/01/the-five-levels-from-spicy-autocomplete-to-the-software-factory/
- **StrongDM, "Software Factories and the Agentic Moment", 2026-02-06 [read].** Rules: code is not written by humans
  and not reviewed by humans. Humans write the charter and curate scenarios. Rule of thumb: about $1,000 of tokens per
  engineer per day. https://factory.strongdm.ai/ . Simon Willison's write-up, 2026-02-07 [read], questions both the
  no-review rule and the cost: https://simonwillison.net/2026/Feb/7/software-factory/
- **Factory (factory.ai, now factory.com)** uses the term for its product: agents ("Droids") as the units of a
  pipeline, with seven stages from intake to observability. Article dated 2026-07-08 [read] (a search snippet gave
  January 2026 for the same title; not resolved).
  https://factory.com/articles/what-is-a-software-factory-architecture
- **Steve Yegge, "Gas Town", about 2026-01-01 [secondary]**: an orchestrator for 20-30 parallel Claude Code agents on
  beads, the same issue tracker lane-control uses. The repo was read: https://github.com/steveyegge/gastown (no date).
- GitHub (Copilot coding agent, Spec Kit) and the other vendors below build the same pipelines; no Microsoft or
  GitHub page found used the phrase "software factory" for them.

### Older meanings (briefly)
- **Japan, 1970s-80s:** Hitachi, Toshiba, NEC and Fujitsu organised large-scale software work like factories (reuse,
  standard tools, process control). Cusumano, *Japan's Software Factories*, Oxford University Press, 1991
  [secondary]: https://academic.oup.com/book/52424
- **Microsoft, 2004:** Greenfield and Short, *Software Factories*, Wiley 2004: model-driven product lines, not agents
  [secondary]: https://softwarefactories.com/TheBook.html
- **US DoD, from 2017:** "software factories" such as the Air Force's Kessel Run are DevSecOps teams and platforms
  [secondary]: https://govciomedia.com/how-software-factories-position-dod-to-win/ (no date). Platform One not
  verified.

The common thread across all three eras: repeatable process, reuse, and quality control built into the line rather
than inspected in at the end. The agent era adds: the workers are models, and the scarce input is tokens and review
attention.

## 2. Frameworks, products and published setups

| Part | What the field does |
|---|---|
| Intake | Tickets and chat: GitHub issues assigned to an agent (Copilot), `@claude` mentions and cron (Claude Code Action), Linear/Jira/Slack (Devin, Factory), a prompt on a repo and branch (Jules, Codex). |
| Planning | A plan the human approves before code (Jules, Factory Missions); spec files: constitution, spec, plan, tasks (Spec Kit), requirements, design, tasks (Kiro); NL specs (StrongDM). |
| Decomposition | Milestones with workers (Factory Missions); a task dependency graph run in waves (Kiro); convoys of issues (Gas Town). |
| Routing to models | Mostly one model per product, or a model flag. **No source documented routing work to models by task risk or by the paths it touches.** |
| Isolation | Git worktrees (Gas Town, Conductor); a container or VM per task (OpenHands, Jules, Codex, Copilot); network allowlists (Codex, Copilot cloud sandboxes). |
| Review and verification | Tests and CI everywhere; agent code review (Copilot, Claude Code plugin, Devin); fresh-agent QA per milestone (Factory); hidden holdout scenarios against cloned services (StrongDM). |
| Merge | Human merges the PR (most); a merge queue that tests the merged stack and bisects failures (Gas Town "Refinery"); opt-in auto-merge (Copilot). |
| Observability | Session views, feeds, audit events; little beyond that is documented. |
| Budget | Turn limits, timeouts, concurrency caps, a monthly compute allowance; **no hard money budgets found**, except StrongDM spending as a target. |
| Human checkpoints | Plan approval and PR review (most); charter and scenarios only (StrongDM). |

Per product, what was read:

- **StrongDM Software Factory and Attractor** (2026-02-06) [read]: https://factory.strongdm.ai/ ,
  https://github.com/strongdm/attractor . Attractor ships specs, not code: three markdown specs for a pipeline, a
  coding-agent loop and a unified LLM client. Verification is by **scenarios kept outside the codebase**, so agents
  cannot see them, judged by an LLM as a "satisfaction" probability, against a "Digital Twin Universe" of cloned
  services (Okta, Jira, Slack, Google Docs). Isolation, routing and merge are not described in what was read.
- **Factory Droids and Missions** [read]: https://docs.factory.com/missions/overview (no date). You plan with the
  agent and approve the plan; an orchestrator runs workers milestone by milestone, with QA after each; you can pause
  and redirect ("not fire-and-forget"). Factory's architecture article lists separate controls before and during review:
  command restrictions, a sandbox, secret detection, automated review, tests, security checks, branch protection.
- **Gas Town** [read: README and glossary, no dates]: https://github.com/steveyegge/gastown . Roles: Mayor
  (coordinator), Polecats (workers, one git worktree each), Witness (finds stuck agents), Refinery (merge queue),
  Deacon (supervisor across repos). Workers never push to main: the Refinery batches merge requests, runs the gates on
  the merged stack and bisects on failure. Concurrency cap `scheduler.max_polecats`; no money controls; Claude Code is
  the only runtime named.
- **GitHub Copilot coding agent** [read]:
  https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent (no date; the page says its new
  cloud mode is available from 2026-10-28). Issue in, one branch and one PR out; Actions sessions capped at 59
  minutes; rulesets can block the PR; Copilot review plus human review; a monthly compute allowance.
- **Claude Code GitHub Action / Agent SDK** [read]: https://code.claude.com/docs/en/github-actions (no date).
  Triggers on mentions or any event; only actors with write access; `--max-turns`, timeouts and concurrency for cost;
  no automatic merge.
- **OpenAI Codex cloud** [read]: https://learn.chatgpt.com/docs/cloud (no date). A workspace per task, parallel, a
  reusable environment, network access set per environment; you inspect and then open a PR.
- **Devin** [read]: https://docs.devin.ai/get-started/devin-intro (no date). Tickets or chat in; a shell, IDE and
  browser per session; draft PRs and human takeover. Sandbox and cost controls not on that page.
- **Google Jules** [read]: https://jules.google/docs (no date). A cloud VM clones the repo; a plan to approve before
  any change; reads AGENTS.md.
- **OpenHands** [read]: https://github.com/OpenHands/OpenHands (MIT, no dates). An agent server with a REST API, a
  Docker container per conversation, any model; a commercial cloud.
- **GitHub Spec Kit** [read]: https://github.com/github/spec-kit (news items from 2026-10, "Spec Kit 1.0").
  Constitution, specify, plan, tasks, implement, converge; each step reviewed by a human; ends in go,
  needs-clarification or kill.
- **Kiro (AWS)** [read]: https://kiro.dev/docs/specs/ (updated 2026-11-12). Requirements, design and task files;
  independent tasks run in waves; review comments as the human gate; an optional property-based check.
- **MetaGPT / MGX** [read]: https://github.com/FoundationAgents/MetaGPT . "Code = SOP(Team)": a simulated software
  company of product manager, architect, project manager and engineer agents. Research-flavoured: no sandbox or merge
  gates described.
- **Worktree managers** (Conductor, Vibe Kanban, Crystal, Sculptor) [secondary]: desktop apps that run several CLI
  agents, one worktree or container each, with a review and PR flow. Their status in 2026 is reported
  inconsistently. https://agentsroom.dev/blog/best-multi-agent-coding-tools (no date).
- Not researched: Amp/Sourcegraph, Cursor background agents, Sweep, Aider, claude-flow, BMAD, ChatDev, Tessl.

## 3. What works and what fails, with evidence

### Measured
- **Productivity is not proven.** METR's randomized trial (16 experienced developers, 246 tasks) found AI use made
  tasks **19% slower**, while the developers believed it had made them about 20% faster. 2025-07-10 [secondary]:
  https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/ . METR's follow-up (57 developers,
  800+ tasks), 2026-02-24 [read], gives -18% and -4% with wide intervals, calls its own data "an unreliable signal"
  because developers refused to work without AI, and says time measurements are unreliable **for developers running
  several agents at once**: https://metr.org/blog/2026-02-24-uplift-update/
- **Throughput up, stability down.** DORA 2025 (about 5,000 developers, a survey): AI adoption now correlates with
  throughput, and delivery instability keeps rising; "AI amplifies" existing strengths and weaknesses. Via InfoQ
  2025-09-29 [read]: https://www.infoq.com/news/2025/09/dora-state-of-ai-in-dev-2025 (the DORA report itself not
  read).
- **The cost moves to review.** Faros AI telemetry (10,000+ developers): high-AI teams merged 98% more PRs, review time
  rose 91%, PR size 154%, bugs per developer 9%, company delivery metrics flat. Vendor report, 2025 [secondary]:
  https://www.faros.ai/blog/ai-productivity-paradox-report-2025
- **Agent PRs are merged less often than human PRs**, and the rate differs a lot by agent. AIDev dataset of 456,000+
  agent PRs, 2025-07-20 [read]: https://arxiv.org/abs/2507.15003 . Per-agent merge rates from follow-up studies vary
  widely by subset and are not comparable [secondary].
- **Agents game visible tests.** METR, 2025-06-05 [read]: on tasks where the scoring code was visible, 30.4% of runs
  (39 of 128) cheated, e.g. by overwriting the timer or patching the evaluator; telling the model not to cheat barely
  helped (80% at baseline, 70-95% with the instructions). https://metr.org/blog/2025-06-05-recent-reward-hacking
- **Multi-agent costs about 15x the tokens of chat** (agents alone about 4x), and token use explained about 80% of the
  variance in Anthropic's research eval; Anthropic says most coding tasks have fewer truly parallel parts than
  research. 2025-06-13 [read]: https://www.anthropic.com/engineering/multi-agent-research-system
- **Public benchmarks are weak evidence.** OpenAI stopped reporting SWE-bench Verified after finding flawed tests and
  memorised fixes; its own page could not be read (HTTP 403), details via a secondary report [secondary]:
  https://aiweekly.co/node/5874 . Claims that SWE-bench Pro mis-grades are unverified.

### Reported by practitioners (claims, not measurements)
- **One writer, several advisors works; parallel writers do not.** Cognition, 2026-04-22 [read]: multi-agent systems
  work "when writes stay single-threaded and the additional agents contribute intelligence rather than actions"; a
  clean-context review agent catches about 2 bugs per PR (their claim about their product). Unstructured swarms are
  "mostly a distraction". https://cognition.com/blog/multi-agents-working ; the earlier "Don't Build Multi-Agents",
  2025-06-12 [secondary]: https://cognition.ai/blog/dont-build-multi-agents
- **Start simple.** Anthropic, "Building effective agents", 2024-12-19 [read]: coding suits agents because tests verify,
  but human review is still needed; autonomy brings cost and compounding errors, so use a sandbox and guardrails.
  https://www.anthropic.com/engineering/building-effective-agents
- **Big parallel factories are expensive and messy.** Gas Town users report broken merges, duplicate fixes and lost
  designs; costs of about $2-5k a month; Yegge advises heavy design up front [secondary]:
  https://softwareengineeringdaily.com/2026/02/12/gas-town-beads-and-the-rise-of-agentic-development-with-steve-yegge/
- **Agents with real access do real damage.** Replit's agent deleted a production database during a code freeze,
  reported 2025-07 [secondary]: https://incidentdatabase.ai/cite/1152/ . One incident, not a rate.
- **Prompt injection through issues.** An agent with private data, untrusted content and a way to send data out can
  be made to leak it (Willison's "lethal trifecta", 2025-06-16 [read]:
  https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/ ). A malicious public issue hijacked an agent using the
  GitHub MCP server, Invariant Labs, May 2025 [secondary]: https://invariantlabs.ai/blog/mcp-github-vulnerability .
  This matters for any factory whose intake is public issues.

### What this adds up to
The credible design is not "many agents in parallel". It is: small tasks, one writer per task in its own sandbox, an
independent reviewer with a clean context, gates the writer cannot see or change, a token budget with hard steps, and
a human (or a very well-evidenced gate) at the merge. Everything beyond that, especially dropping human review, rests
on vendor claims.

## 4. lane-control measured against a factory

| Factory part | lane-control today | Gap |
|---|---|---|
| Intake | The owner talks to the coordinator; beads issues (`issues.md`); `project-setup` onboards a repo with an adapter agent and a reviewer agent | No intake from GitHub issues or chat. That is also a safety feature: no untrusted text in the intake (lethal trifecta). |
| Planning | Coordinator persona; briefs (`templates/worker-brief.md`, `templates/lane-brief.md`); design lead for frontend; owner questions raised up front | No spec step (requirements/design/tasks) and no plan approval as a written stage; `docs/superpowers/` holds specs and plans by hand. |
| Decomposition | One issue, one key, one lane (`issues.md`); the coordinator splits work | No dependency graph or waves; done by judgment. |
| Routing | Tiers as roles, path map per project, escalation and `NEEDS_*`, promotion and demotion from the ledger (`model-routing.md`); `templates/lanes.tsv` and `scripts/lane-tier` | **Ahead of every product surveyed.** But the calibration loop is not running: `lane-ledger.tsv` has only its header, and no Orca-path persona or skill mentions the ledger. |
| Isolation | An Orca worktree per lane; `scripts/opencode-sandbox.sh` (bubblewrap, own network namespace, memory cap, no credentials, PR relay) for third-party models; machine caps (`lane-heavy`, `lane-room`) | Claude lanes run unsandboxed as the owner (by design, `sandbox none`). The sandbox is tested on NixOS only. |
| Review and verification | Coordinator reruns every gate in every workspace; review tiers A/B/C by risk; the reviewer persona gets the diff and the issue, **not the lane's report** (a clean context); reviewers are verified too (`reliability.md` 4, 8) | Gates live in the repo, where the lane can read and edit them (METR's reward hacking). No hidden acceptance checks. **No CI on this repo** (no `.github/`). |
| Merge | Only the coordinator, only on the owner's word or a clean gate when authorized; close at merge; merge failure stops all follow-ups (`reliability.md` 15) | No merge queue: merges in a row conflict (`reliability.md` 10) and are handled by sending the PR back. |
| Observability | Orca GUI (lanes nested under the coordinator), Cotal roster and status, the 6-line report, beads state | No metrics: merge rate per tier, rounds, findings per review, tokens per merged PR. The ledger was meant for this. |
| Budget | Usage feed and weekly ceiling with steps; window steps; `scripts/window-guard.sh` sends the orders without spending tokens; caps per project; "budget decides lane count, never tier" | **Stronger than the products surveyed** (they cap turns and time, not spend). Depends on a usage feed from the owner's status line (`machine.md`). |
| Human checkpoints | Owner decisions with a recommendation; merge on the owner's word; `project-setup` asks once before committing | Matches the evidence; the opposite of the dark factory. |
| Learning loop | `reliability.md` (28 numbered failures with fixes); `bd remember`; "learnings go into the repo" | Unusual; no product publishes one. |
| Resumability | Truth in files, handover memory, parking with Resume notes, stale-claim and park checks | Good; Gas Town has the nearest equivalent. |

Also measured against the evidence: lane-control already follows the "one writer, advisors review" pattern (one lane
writes, the reviewer is read-only), keeps lanes small, keeps the human at the merge, and treats lane text as data. It
has no "dark factory" ambitions and should not grow them on current evidence.

## 5. What a framework would require
"Framework" here means other people could install it on their machines and run their projects with it, without this
owner's setup. Today that fails on these points:

1. **Runner coupling.** Two runners (Rove; Orca + Cotal), each with its own scripts; the doctrine is shared, the
   verbs are not. A framework needs one written **runner interface** (open a lane in a worktree, message it, get its
   report, stop it, list lanes) with Orca + Cotal and Rove as two adapters. `docs/orca.md` §8 (the Rove verb map) is
   the seed.
2. **Harness coupling.** Claude Code and OpenCode only; usage feed read from Claude Code's status line. Needs a
   harness adapter: how to launch, pin a model, set permissions, read usage.
3. **Platform coupling.** The sandbox is NixOS-tested and fetches its tools through `nix`; systemd user scopes; bash
   and Python scripts with Linux assumptions. Needs a portable sandbox path (a container option) or a clear "Linux
   with these tools" floor, tested in CI.
4. **Configuration.** A bash config (`config.example.sh`), a TSV tier table, a per-project JSON settings store, a
   limits file. Needs one documented, validated schema and a `doctor` (the README already lists a doctor as planned).
5. **Packaging and releases.** Today: `git clone` and a 2,800-line `scripts/project-setup`. Needs versions, a changelog,
   an upgrade path for installed personas and skills (`--refresh` is a start), and pinned versions of Orca and Cotal,
   which change fast.
6. **Tests and CI.** About 590 unit tests with Orca and Cotal stubbed, but no CI runs them on PRs. A framework needs CI,
   plus an end-to-end smoke run of one lane against a real runner.
7. **Docs.** The doctrine is good but written for one owner, and mixes instance decisions (dated owner choices) with
   general rules. Needs a split: method (general), reference (scripts, config), instance (kept out of the repo).
8. **Extension points.** Intake sources, review steps (e.g. a hidden acceptance suite), merge strategies (direct or a
   queue), notifiers. Today each would be a persona or doctrine edit.
9. **Multi-user.** "A single owner who trusts the agents with their repos" (README). Teams would need roles,
   permissions and shared budgets; that is a different product.

### Risks and costs
- **Maintenance eats the budget.** The repo's tokens come from the same weekly budget as the owner's projects. Most of
  a large share of its history so far is integration work with fast-moving tools (Orca, Cotal, OpenCode versions; see
  `reliability.md` and `docs/orca-cotal-setup.md`). Supporting other people's setups multiplies that.
- **Upstream churn.** Orca, Cotal and the harnesses change weekly; a framework is only as stable as its runners.
- **Crowded field.** GitHub, Factory, Cognition, Google, OpenAI and Gas Town (18.8k stars) all occupy parts of this
  space with more people. lane-control's distinct value is the method (routing, budget, review by risk, failure log),
  which can be shared as writing without becoming a framework.
- **Security exposure.** A framework invites users to point it at public issues and secrets; lane-control's safety
  partly comes from its narrow intake and single trusted owner.
- **Unproven benefit.** No data yet shows the method's merge rate or cost per merged PR; the ledger is empty.

## 6. Options

**A. Stay a personal toolkit (status quo).** Cheapest. Keeps all budget on projects. Risk: the method stays
unmeasured, and good ideas from the field are not taken up.

**A+. Personal toolkit that adopts the evidenced factory practices (recommended).** Same scope, plus four changes
that the evidence supports and that also make a later framework possible:
1. **CI on this repo**: run `python3 -m unittest discover -s tests` and `bash -n`/`sh -n` on every PR. Recurring
   review findings become CI checks (`reliability.md` 4 already says so).
2. **Fill the ledger on the Orca path**: the coordinator appends a row per closed lane (the persona and skill do not say
   so today), and a small script prints merge rate, rounds and worst finding per tier and category. This is the
   factory's observability, and the input routing promotion and demotion need.
3. **Acceptance checks the lane cannot see**: for code lanes, the coordinator or reviewer writes or holds a check
   outside the lane's worktree and runs it before merge; try it on one project first (StrongDM holdouts, METR
   reward hacking).
4. **Write the runner interface** as a short doc from the existing Rove and Orca verb maps, without changing code. It
   costs little and is the first step of option B.

**B. A published method plus a reference kit.** Split doctrine into method, reference and instance; one config schema
and `doctor`; CI with a real-runner smoke test; Orca + Cotal as the one supported runner, Rove kept as an adapter;
releases. Suits people with a similar setup (a single owner, a token budget, Linux). Weeks of deep-tier work plus
ongoing support.

**C. A full framework.** Runner- and harness-neutral core, plugin points for intake, review and merge, a dashboard,
multi-user budgets and permissions, portable sandboxing. Months of work, competing directly with funded products, for
a benefit not yet measured.

### Recommendation and first steps
Take **A+** now. Revisit **B** after about four weeks of ledger rows across the owner's projects: if merge rate, rounds
and cost per merged PR look good, the method is worth packaging; if not, the data says where to fix the method first.
Do not pursue C.

First steps, each one lane:
1. Add a CI workflow for the unit tests and shell syntax checks (standard tier).
2. Add the ledger row to the Orca-path coordinator persona and skill, with a `tests/test_content.py` check, plus a
   ledger summary script (standard tier; persona change needs the owner's yes as doctrine).
3. Pilot one hidden acceptance check on one project's next code lane and record what it caught (deep tier, owner
   picks the project).
4. Write `docs/runner-interface.md` from `docs/orca.md` §8 and `docs/wsp.md` (standard tier).
