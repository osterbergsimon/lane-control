# Coordinator reliability log

Failures of coordinators (long-lived agent main tasks directing worker lanes), each with the durable fix and how to reuse it. Newest last. Add an entry for every failure that leads to a durable fix.

## 1. Read usage, never assume it is unknowable
- **Failure:** a coordinator told a peer that weekly usage could not be read, and kept working at 95-98 % of the weekly limit, past the owner's ceiling.
- **Fix:** for each metered budget, a usage feed (a file or command giving the weekly and window fractions) and a ceiling the owner sets per week. Thresholds: pressure signal at ceiling-15, no new work at ceiling-10, WIP+stop at ceiling-7, stop all at ceiling-4.
- **Reuse:** read feed and ceiling before every launch on a metered budget. Share a "PRESSURE on/off" signal between coordinators. Budgets with no feed are managed by caps and by the owner's reports.

## 2. Never let a worker report sit unhandled
- **Failure:** a worker reported while the coordinator session had crashed; the report was never read.
- **Fix:** a hook in the coordinator's checkout only (`UserPromptSubmit` and `SessionStart`) injects `rove api context --text` into every turn: finished, stuck and unknown tasks and open decision gates.
- **Reuse:** `templates/coordinator-settings.local.json`. Keep it out of committed settings so workers do not pay for it. On every report: verify the claims, decide, follow up, record the outcome on the issue.

## 3. Owner decisions as gates, not chat lines
- **Failure:** decisions drowned in long status output.
- **Fix:** `rove api gate-raise --task-id <main> --question ... --option k=label --recommend k`. The answer returns as a gate-answered message. Every reply with pending gates starts with a "Decisions for you" block.
- **Reuse:** as written.

## 4. Verify worker claims mechanically; review by risk, not by model
- **Failure:** worker-reported numbers were taken on trust; a check was skipped on a code PR; a "regression test" passed on main too, so it proved nothing; a cheap-tier worker added a normative spec rule unasked.
- **Fixes:**
  - The coordinator reruns the gates itself; deterministic checks cost CPU, not tokens.
  - Review tiers by what a PR touches: A docs/tests, gates only; B code, plus an independent review by the standard tier of the diff and the issue only; C formats/security/hostile input, plus an independent deep-tier review.
  - Spec changes go to the owner.
  - Recurring review findings become CI checks.
- **Evidence:** a deep-tier review found silent content loss in a crash "fix" that all tests passed; the recheck found a second instance.
- **Reuse:** the "Review tiers" and gate "Scope" paragraphs in AGENTS.md.

## 5. Third-party engine lanes
- **Model:** the launch verb may refuse a model flag for the engine, and the engine's own flag may be ignored by its TUI. Set the model in the project's engine config. A per-lane override goes in a gitignored config that loads after the root file.
- **Permissions:** launch with the engine's auto-approve command, or the worker stalls on permission prompts.
- **First prompt:** it often sits unsubmitted although `send` reports delivered. Check the engine's session list in the worktree and the issue claim; nudge once.
- **Config format:** a new engine version may silently ignore a config in the wrong shape. Verify every config change with the engine's config dump.
- **Privacy:** third-party lanes see everything they read. Keep them code-only, with no private data links (the init script branches on the task's vendor).

## 6. Survive a coordinator crash
- All rules live in AGENTS.md and beads memories, not in the session.
- The `SessionStart` hook re-injects state; a `handover-latest` memory holds the last handover.
- A routine aimed at a dead tab does nothing: night routines revive the tab first (`rove api tab-revive`).

## 7. The agent harness itself
- **Auto mode:** it blocks a coordinator from editing its own permission settings ("self-modification") and from launching auto-approving agents. The owner installs the project allow rules for `rove api add` and `rove api spawn` once.
- **MCP servers and connectors:** turn off those a project does not need in the project settings; every tool schema costs context.

## 8. Verify reviewers too, and rerun every workspace
- **Reviewers can be wrong:** a deep-tier review claimed two code paths failed unsafely on main. The worker disputed it; the coordinator read the code: the worker was right. The real, older risk was filed as its own issue instead of blocking the PR. A reviewer's premise is a claim like any other.
- **Worker gate claims miss workspaces:** a worker reported "format green" but had run it in the root workspace only; a second workspace failed. The coordinator's rerun covers every workspace (AGENTS.md lists them); one script runs them all.

## 9. Lessons from the first fleet
- **Third-party first prompt lost on spawn, every time:** 11 of 11 spawns sat on an empty start screen. After spawn, wait ~25 s and resend the full brief with `--prompt-file` if no work shows.
- **Worker report silently lost to shell quoting:** a worker used `rove api send --prompt "...(...)..."`; the shell failed on the parenthesis and the coordinator saw nothing while the PR sat ready. Worker rule: always `--prompt-file - <<'EOF'` and check `"delivered":true`. Coordinator: sweep `context` for PRs with passing CI and no report.
- **Renaming a branch with an open PR closes the PR's link:** GitHub cannot retarget a PR's head branch. Rename only before the first push.
- **CI runner drift:** a floating runner image moved to a new OS release and a system library started reporting file types differently, failing every PR. Pin runner versions; treat a sudden repo-wide red as environment drift first.
- **Root-owned caches block worktree deletion:** a dev container running as root leaves root-owned files, so branch deletion, `git worktree remove` and `rove api delete` fail. Run containers as the invoking user, or clear with a throwaway container.
- **Review catches what green CI doesn't:** a cheap-tier file-type sniffing fix passed CI but would have reclassified every office file as a plain archive (the override fired on any non-matching verdict). Read every production diff and ask "what else matches this condition?"

## 10. Lessons from a second fleet
- **A worker's screenshot script rebooted the owner's desktop.** It signed in to a dev backend and clicked a button whose route ran the host's reboot as the desktop user, which the OS allowed without a prompt. Fixes: a worker rule (never call host-acting routes on a real backend; stub them in the browser test tool) and a product guard (host control off by default on desktops and in containers). Any app that can act on its host is a hazard when it runs on the coordinator's machine: list those routes in the worker rules.
- **A broad `pkill -f` killed the owner's live dev server.** Workers stop only processes they started, by the PID saved at start; never by pattern or a looked-up PID. Give the owner's stack reserved ports.
- **Verify in the real deployment artifact, not only in CI.** A command passed every test in the dev environment but failed in the published image, where the package was an editable install pointing at a deleted path. Run the command once inside the published image; add a CI smoke step for it.
- **Real hardware finds what CI can't.** First boot on a small device found a service ordering cycle and a browser GPU-path crash that CI-green PRs had hidden. Read the journal and crash dumps before guessing; take the owner's hardware observations seriously.
- **Unit tests that mock a library hide upgrade breakage.** A major bump changed a default export into a module object, so a widget's mount threw and skipped its parent's mounted hook. Keep one test that imports the real library; make optional widgets fail safe; check the browser console early.
- **First-prompt detection by screen text is unreliable.** Rule: wait ~20 s, then always send a one-line "Start now on the brief above." It is harmless if the worker already runs.
- **Crash recovery:** a reboot kills every engine. `tab-revive --restart-exited` brings third-party engines back with no memory: resend the brief plus a "where you left off" note from `git log`/`git status`. `send --respawn` resumes a conversation but can drop the prompt; resend once the tab is alive. Session-bound background jobs (dev servers) die too; the owner must approve persistent user services if those should survive.
- **Merges in a row conflict.** Two PRs touching the same files: send the second back with the exact files and the behaviour from both sides to keep. When a PR's CI ran before other merges, have the worker merge the base branch and rerun the gates (dependency bumps especially).

## 11. Coordinator-only hooks leak into worker worktrees
- **Failure:** the inbox hook in the main checkout's local settings also fired in every worker session (the harness applies the main checkout's local settings to its git worktrees). Workers received the coordinator's task list and owner decisions.
- **Fix:** guard the hook with the coordinator's own task id: `[ "${ROVE_TASK_ID:-}" = "<id>" ] || exit 0; ...` (in the template).
- **Reuse:** guard any coordinator-only hook the same way.

## 12. A web inbox that fails on decision gates
- **Failure:** every gate errored in the inbox UI while other items rendered (reported upstream).
- **Workaround:** until fixed, put every decision in the chat reply as well as in the gate, and call `rove api gate-answer` yourself when the owner answers in chat.

## 13. Browser tools on unusual OSes
- **Failure:** designers could not start the browser-automation MCP: it defaulted to a browser channel the OS does not have, and parallel sessions would have shared one browser profile.
- **Fix:** in the harness env settings, point it at an installed browser binary, run headless (no windows on the owner's desktop) and isolated (a profile per session). Sessions pick it up only after a restart.
- **Reuse:** test a browser tool once from a fresh session before briefing designers on it. See `machine.md`.
