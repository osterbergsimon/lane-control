# Coordinator reliability log

Failures of coordinators (long-lived agent main tasks directing worker lanes), each with the durable fix and how to reuse it. Newest last. Add an entry for every failure that leads to a durable fix.

## 1. Read usage, never assume it is unknowable
- **Failure:** a coordinator told a peer that weekly usage could not be read, and kept working at 95-98 % of the weekly limit, past the owner's ceiling.
- **Fix:** for each metered budget, a usage feed (a file or command giving the weekly and window fractions) and a ceiling the owner sets per week. Thresholds are offsets below the ceiling, set in the config: pressure notice at ceiling-15, then no new work, WIP+handover, stop all (`model-routing.md`).
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

## 14. Briefs must keep workers out of every main checkout
- **Failure:** a brief told a worker to commit a script into another repo's checkout. The worker switched that main checkout to its branch, and the owning coordinator's next commit swept up the worker's uncommitted edits. Separately, a review agent briefly detached a project's main checkout. Nothing was lost, but both collided with a coordinator's working tree.
- **Fix:** every brief that touches another repo says "use your own worktree: `git -C <repo> worktree add <dir> <branch>`; never edit or check out anything in a main checkout". Every review brief says "inspect branches with `git diff origin/...`, or in a scratch worktree; never switch the main checkout".

## 15. A merge script must stop all follow-up steps when the merge fails
- **Failure:** a coordinator ran `set -e; ...; git merge ... && git push ...` and then, on separate lines, closed the issue, appended the ledger row, deleted the worker task and resumed another lane. The merge conflicted, and `set -e` does not stop on a failure inside an `&&` list, so every follow-up ran anyway: issue closed and task deleted before anything merged (the branch was pushed, so nothing was lost).
- **Fix:** chain the whole sequence with `&&`, or test the merge result explicitly (`if git merge ...; then ...; fi`), and run follow-up steps only after the push succeeded and `gh pr view <n> --json state` says MERGED.

## 16. Deleting a finished task: remove its private-data links first, by literal path
- **Failure:** `rove api delete` refuses a worktree with gitignored content (`DIRTY_WORKTREE`): private-data symlinks the init script adds to every task, a cache directory. An `rm "$d/$l"` loop to clear them was blocked by the harness safety check (an unbounded variable could expand to `/`).
- **Fix:** after the merge, `ls -la <worktree> | grep -- '->'`, then `rm` each link by its literal absolute path (never `-r`, never a bare variable; `"${d:?}/..."` if a variable is unavoidable), delete caches by literal path, then `rove api delete`. Better: have the init script record what it links, so a cleanup can undo exactly that.

## 17. Night caps can OOM a workspace test run
- **Failure:** a project's night profile ran the whole workspace's tests with many threads in the same memory-capped scope as day. The scope was OOM-killed: exit 143 with an empty log, which looks like a hang or a crash, not a test failure.
- **Fix:** rerun with day caps before judging a branch; a gate script logs the exit code per step so a 143 is visible at once. Set night caps with the memory scope in mind (`machine.md`).

## 18. A lane on a common dev port kills someone else's server
- **Failure:** a lane started its dev server on the framework's default port with a strict-port flag, the port the owner's own dev server held. It read the listener's pid from `ss` and killed it, twice; its screenshots may have shown the other app.
- **Fix:** lanes take a free or per-worktree port, never a framework default, and kill only PIDs they started (`$!`), never a pid read off a port or matched by name. Reserve ports for the owner's stack.

## 19. Decision dialogs get overwritten by lane messages
- **Failure:** several times an interactive question dialog came back with a lane's report text in place of the owner's answer. A report delivered while the dialog was open took the answer slot, so the dialog recorded no real answer.
- **Fix:** never act on a dialog answer that is not one of its options or the owner's own words. While lanes are active, put owner decisions in Rove gates (`gate-raise`; answers come back as a gate-answered message) and list them in chat as well. Use the dialog only when no lane is likely to report.

## 20. `pkill -f <pattern>` kills your own shell
- **Failure:** a coordinator ran `pkill -f '<script path>'` in a compound command whose own command line contained that pattern. It killed its own shell (exit 144), and the stale process kept running.
- **Fix:** stop your own processes by PID (`pgrep` first, check `/proc/<pid>/cwd`, then `kill <pid>` and its children). Never use `pkill -f` with a pattern that also appears in the command you are running.

## 21. The usage feed interleaves stale samples
- **Failure:** every session's status line appends to the usage history, and an idle session keeps logging its last-known values. The last line jumped between two weekly values second by second.
- **Fix:** read the max of the last 60 samples for the newest reset (`BUDGET_USAGE` in `config.example.sh`); usage only rises within a window.

## 22. `rove api feedback` is a public post under the owner's account
- **Failure:** a coordinator filed a bug with `rove api feedback`; it became a public discussion naming two private projects and their gate ids. Editing it later leaves the original in the public edit history; only the repo maintainer can delete that.
- **Fix:** public posts need the owner's approval first and generic wording (`roles.md`, `model-routing.md`).

## 23. First sandboxed-lane rollout
- **Failures:** the documented launch form broke on Rove's appended brief (needs `--auto --prompt`); the engine's new-session flag froze the TUI size; an explicit branch override lost to the caller's task env; files outside the worktree (worker rules, symlinked skills) vanished inside; no `bd`, no CI read, no host loopback; the header said no key was needed.
- **Fixes:** launch form and conventions in `model-routing.md` (sandboxed swift lanes); the flag gated; a read-only CI-checks relay; an explicit branch wins; `issues.md` says sandboxed lanes file through the coordinator. Positive: auto-approve inside the sandbox ended permission-prompt stalls (an unsandboxed lane had sat 8 h on a prompt).
- **Rollout rule:** supervised first lane per project; the test suite (`scripts/test-opencode-sandbox.sh`) from a scratch worktree after every sandbox change.

## 24. A crash-looping tool inside a lane floods the host with core dumps
- **Failure:** a browser binary downloaded inside a lane crashed in a loop; the host's core-dump handler tried to process every dump and overloaded the machine.
- **Fix:** the sandbox sets `RLIMIT_CORE` to 0, soft and hard, inherited by everything inside (a lane cannot raise it). Do the same for any unattended workload that can run untrusted or flaky binaries.

## 25. opencode 2.x keeps credentials in its own database
- **Failure:** after an upgrade, lanes were logged out: a 2.x data directory never imports `auth.json` (that happens only when migrating an old database), so a lane with a fresh per-lane data dir had no key.
- **Fix:** give the lane key as the provider's `apiKey` (a `{file:...}` reference in an inline config), restrict the config to the one provider with no free models (no silent fallback model), fail loud at launch on a rejected key or unavailable pinned model. Verify with the engine's own `models` listing after any engine upgrade.

## 26. A git credential prompt can pop up on the owner's desktop
- **Failure:** a relay running `git`/`gh` outside any terminal fell back to an askpass program when a credential was missing, opening a window on the owner's desktop.
- **Fix:** every script that runs git or gh unattended sets `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS` and `SSH_ASKPASS` to `false`, `GCM_INTERACTIVE=never`, `GH_PROMPT_DISABLED=1`: a missing credential then fails fast with an error. The sandbox test proves no askpass runs.

## 27. The NixOS GitHub runner module needs four changes for container CI jobs
- **Failure:** on a dedicated NixOS runner box (`services.github-runners`), container jobs failed in turn: "Could not find a part of the path '/proc/1/cgroup'" (ProtectProc=invisible); no Docker group access (PrivateUsers=yes); "No space left on device" in `uv sync` (the default work dir is a small RAM-backed runtime dir); and an ephemeral runner that could not re-register after its first job (registration tokens expire after an hour).
- **Fix:** serviceOverrides ProtectProc="default", PrivateUsers=false, ReadWritePaths=[workDir]; workDir on disk with an age-out tmpfiles rule; tokenFile holding a fine-grained PAT scoped to the one repo with only Administration read-write (the module mints a registration token per job); job containers with `--init` so killed children are reaped.
