# Newer Orca than 1.4.250: what changes for lane-control (RES-3)

Date: 2026-11-21. Asked by the owner: "we should prob evaluate newer orca as well." The desktop here runs Orca
1.4.250 (nix-packaged); a downstream estate runs 1.4.251 to 1.4.263 and one of its boxes hit "server too old".

How to read this: **[ran]** means a command was run here and its output seen. **[read]** means taken from release
notes or from reading shipped files (1.4.267's, and 1.4.250's from the installed package), not run. The installed
1.4.250 package's CLI was run (help, `agent-context --json`, a scratch server on its own port), isolated with a
scratch HOME and data folder (see Versions), and changed nothing. The running desktop, its server, its
`~/.config/orca` and any saved environment were not touched; no agent was started, nothing was paired and no
environment was added.

## Recommendation

**Pin to 1.4.250 today; upgrade to one exact newer version (1.4.267 or the newest by then) after a short canary, and
move the desktop and every box together.** Do not take "whatever is newest" on a box. The canary has since run: go
for **1.4.268** (Canary results).

What "pin" actually requires, because today it is close to a no-op: the desktop is already pinned by its nix
package, so the action is to keep that package at 1.4.250 and not let it follow the app's updater. A new box gets
the desktop's newest slot by default, which today is 250's, so it is also pinned until the desktop moves. The pin
only becomes an action when the desktop moves: from then on pass `project-setup --orca-version` (which passes
through to `orca-host-install --version`) for every box, so a box's server is a decision and not whatever the
desktop built last.

The estate's boxes (251-263) meanwhile: leave them. There are two directions, and only one is safe:
1. This 250 desktop's CLI reaching a box's 251-263 server is an older client against a newer server. It keeps
   working and only lacks the new features. (Not run: step 6 below does it.)
2. A box's 251-263 CLI reaching the 250 desktop's server, which is what a box coordinator does when it starts lanes
   here (`worker-start --on <paired environment>`; `remote.md`, `coordinator.md`), is a *newer* client against an
   *older* server, the direction that fails with the gates below. This is the most plausible origin of the "server
   too old" incident. The doc does not know which desktop the estate pairs with; if it is this one, the exposure
   exists for as long as the desktop stays on 250. **Unverified**: a 258+ box's `worker-start --on` against a 250
   server is exactly the untested case (258 changed `--on` workers into "the paired computer's own worker", and
   267's server checks the remote server's capabilities when it handles `--on`); nothing says it works against 250.

"Leave them" still stands as the conclusion: rolling the boxes back would add risk for nothing, and rolling the
desktop forward untested is the other risk. `--on <paired environment>` is the only way a box coordinator starts a
lane on the desktop (a 267 agent using `--environment` for it is refused with `agent_server_route_refused`), so when
`--on` hits a gate the box does not start that lane: it hands the work to the owner, in its own terminal, until the
desktop moves. There is no Orca route for the handoff from a box to a 250 desktop: `send --to run:<id>` without
`--on` goes to the box's own Orca, where that Run does not exist; with `--on`, 267's server refuses it ("--on sends
to one agent or bot on that server: --to term_..., orca_session_id:... or bot:<name>, not run:... Nothing was sent",
read from `orcad-server.js`; the 263 notes say the same); with `--environment` an agent delivers no mail (263) or is
refused (267). The only cross-server route is `send --on <desktop> --to term_...` or `orca_session_id:...`, which
needs peer mail (256+) at both ends, the same gate. "Poll-only" in `remote.md` is about how the report of a lane
that did start is noticed (the coordinator runs `check` itself); it is not an alternative to `--on`. The
mixed-version risk is a reason to finish the canary and then converge, not a reason to roll back.

The "server too old" incident that started this is **not explained**, and the recommendation does not depend on it:
the protocol check cannot have produced it between 250 and 267 (same protocol), and the candidate capability
messages are listed below. The canary should reproduce it in both directions (steps 6 and 7).

Why not upgrade now: the commands lane-control calls did not break (see CLI diff), but the things that decide
whether lanes work after an upgrade (setup handling, Run retention, nested workers, worker-lost reports) can only be
seen with a desktop window and a real agent, and this evaluation was not allowed either. Why not wait indefinitely:
1.4.250 is 16 releases behind with daily churn, the estate is already on 251-263, and 1.4.258 closes a gap we
recorded in UP-3 (a worker that can no longer report).

The canary (needs the owner, about an hour): see "Canary before upgrading" below.

## Versions

| | version | evidence |
|---|---|---|
| Desktop here | 1.4.250, orcad slot `0.1.0+b086a7d15d60` | [ran] `orca-ide --version`, slot `.version` |
| Newest release | v1.4.267, 2026-11-20 (orcad `0.1.0+8cc9da34df17`, Node 24.22.1) | [ran] `gh release list`, AppImage and server tarball |
| Releases since 250 | 251-259, 261-267 (there is no 260). 253 and 264 are small patches, 267 is a patch of 266 | [read] |
| Download integrity | AppImage sha512 equals `latest-linux.yml`; server tarball sha256 equals `orca-server-sha256sums.txt` | [ran] |
| Runtime protocol | 3 in both, minimum compatible client and server 2 in both | [read] the shipped `protocol-version.js` of each |
| Bundled Node for orcad | same sha (`e32e2d16...`) in the 250 slot's `.runtime-node` and the 267 tarball's | [read] the two files |

Which 1.4.250 binary ran: there is no 250 server tarball (the first is 256), so the 250 CLI help, its `agent-context
--json` and the scratch 250 server below (port 6991) came from the installed nix package's own files (read-only, its
store path), run with `env -i`, a scratch HOME and a scratch `ORCA_USER_DATA_PATH` on a separate port, never with
the desktop's data folder, port or saved environments. The running desktop was not asked anything. The scratch 250
server had no `--bind` option (it arrived in 252) and bound every interface ("orcad is bound to 0.0.0.0 and is
reachable from the network") on port 6991 for the minute or two it ran, with no pairing offer, and was then stopped;
any future scratch server uses 267's `--bind 127.0.0.1` or runs only where that is possible.

Where 1.4.267 was kept: a scratch folder only (AppImage extracted with `--appimage-extract`, server tarball
unpacked). Its CLI was run with a scrubbed environment (`env -i`, a scratch HOME and `ORCA_USER_DATA_PATH`) because
this session's own terminal carries `ORCA_*` variables that point at the running desktop; without the scrub even
`--version` answers with the desktop's version.

## What changed that touches lane-control

From the release notes of 1.4.251 to 1.4.267 (all **[read]**; the CLI facts below are also in the CLI diff, which is
**[ran]**). Grouped by what lane-control uses.

**Orchestration (run-create, worker-start, send, check, ask, worker_done, worker-abandon, run-list)**
- 251: workers can start their own workers by default. Nested depth default is now **4**, and a profile set to 1 is
  moved to 4 on upgrade (the owner can set it back and Orca keeps that choice). `native-orca/setup.md` says the
  default is 1 and that nesting needs the owner to raise it: that becomes wrong on upgrade.
- 251: a finished worker can keep messaging its coordinator; mail sent after finishing, `worker_done` included,
  leaves the finished task unchanged (CLI `send` notes). Stopping a worker stops its helpers (`worker-stop`,
  `helpers` in the result). Mail waits for a draft typed in the pane, also in OpenCode and across a restart.
- 254: `check` refuses an ambiguous caller; an idle worker is reported once per coordinator message; a worker whose
  terminal dies during startup keeps its output.
- 255: a `reply` to a finished worker's report is refused and the CLI names the follow-up that works. Our contract
  already says a follow-up is a new Task, so this matches it. `worker-start` on a busy terminal is refused before
  any wait.
- 256: `send --on <server>` mails an agent on a paired server and the reply returns to the sender's own `check`.
  Needs the Orca the CLI talks to to carry mail between servers, otherwise `server_mail_unsupported` and nothing is
  sent.
- 258: a worker started with `--on` becomes the paired computer's own worker. A coordinator can answer a worker's
  permission prompt (`orchestration approve` is opt-in and hidden from help; `deny` is new in the CLI). A lost
  worker is reported to its coordinator (#40983). 257 says paired coordinator work "remains inert in this release",
  so 258 is the first release where cross-server workers are real.
- 261: closing a chat worker's chat ends its Dispatch. `orchestration inbox --run <id>` keeps all mail of a Run.
- 262: **settled Runs are deleted from `orchestration.db` a day after their last activity.** Mail is named from a
  Run's mailbox by its coordinator. `worker-start --terminal` now starts in that terminal's own workspace (the CLI
  note changed from "pass --worktree for that terminal" to "omit --worktree").
- 263: CLI `terminal close` stops the terminal's process when the window does not confirm. 265: more paired-server
  coordination fixes (a Claude worker's helper-agent permission prompts, worker reports).
- New in the CLI (additive): `send --body-file <path|->` and `reply --body-file`, `worker-list --on`, `request-show
  --on`, `worker-start --item`. `--body-file` fits our long lane messages.

**Worktree, setup, terminals**
- 252: worktree setup runs only when the script's content matches an approved script (#37977, #37978); 254 remembers
  approved scripts (up to 20 per repo); 262 makes an earlier approval of a repo's `orca.yaml` count for later
  versions. `orca.yaml` here has a `scripts.setup` and says agents wait for its last line. **Not verified**, see
  below.
- Setup handling, which is what lanes start with (all **[read]**): 257 #40547 "worker worktrees follow the project's
  setup setting", and with it the `worker-start` note changed from "New worktrees ... default --setup to run" (250)
  to "without --setup, setup runs unless the project turns it off" (267), a change the CLI diff below also covers.
  `worker-start --worktree new-child` is how our coordinator, design-lead and lane skill start lanes. 265 #43978
  waits for setup before starting the agent when a workspace is created; 262 #38585 includes the setup exit code in
  the skipped-agent-startup message (it implies startup is skipped when setup fails; that behaviour predates 262,
  this is inferred from the title); 252 #37979 makes automations store the approved setup hash and skip setup when a
  run's branch differs (254 #39354 lets them re-approve); 252 #37931 asks before running a committed
  `.orca/issue-command`.
- 254 added then 257 removed a per-repo "trust this repo for agents?" prompt (#40934). It never shipped as a lasting
  prompt, but a box on 254-256 could ask.
- 257: `agent launch --focus` and `worktree create --activate` switch to the chat tab. Configured setup scripts also
  run for folder workspaces, including through the CLI.
- `worktree create` and `worktree set` gained `--parent-environment`. `worktree rm` refuses `--worktree current` and
  `active` and names what they resolve to. `terminal send` now says in its help to use `orchestration send` for
  agents, never to type into their input (matches our rule).

**Environments, hosts, pairing, `serve`, orcad**
- 252: unused pairing codes expire (phone QR 10 minutes, "pair another computer" link 60 minutes); `host rm` is new.
- 254: **pairing codes are single-use**: `environment add` exchanges the code for the machine's own token when the
  host supports it; desktop and CLI use E2EE v2; `serve` refuses public source addresses unless allowed
  (`--allow-public`, `--refuse-public`); `orca pair` is new; a server's pairing identity rotates with a changed
  `ORCA_INSTANCE_ID` (255) or `orca serve reset-identity`.
- 256: **an official server-only tarball**, `orca-server-linux-x64.tar.gz` (and arm64), is published with every
  release, plus `orca vm env doctor`.
- orcad fixes: 251 only real version slots are evicted from the artifact cache (so the shared Node runtime survives
  upgrades); 252 a supervised server does not start without its terminal daemon and orcad defends its instance lock;
  263 a dead installer's lock is reclaimed in seconds.
- `serve --bind <ip>` arrived in 252 (#35095). On 1.4.250 `serve` listened on every interface (the scratch run below
  printed "orcad is bound to 0.0.0.0 and is reachable from the network"); on 1.4.267 `--bind 127.0.0.1` gives "local
  only".

**Agents and automations**
- Claude: 251 needs Claude 2.1.118 or later to clear a stuck "needs permission" status and installs one more managed
  hook for it; 254 can offer to match Claude Code's theme (off, asks once). Nothing here changes how lanes start.
- OpenCode 2: 255 starts it with the picked model, 257 accepts its hooks after the OSC report, 258 fixes the first
  prompt on 2.0.13 to 2.0.18, 266 installs Orca's own OpenCode, Cursor and others when a host has none. The agent
  ids `opencode` and `opencode2` still exist in 267's `worker-start` help.
- Automations: `--precheck`, `--precheck-timeout`, `--workspace`, `--workspace-mode` (`existing` is what
  machine-setup passes with `--workspace path:<folder>`, `--extra-agent-args` and `--enabled`; `new-per-run` the
  other value) and the notes on them are unchanged for project automations; `--setup run|skip` and
  `--extra-agent-args` too, except that `--extra-agent-args` now also accepts Antigravity. The new flags (`--bot`,
  `--on`, `--once`, `--agent`, `--model`, `--effort`, `--runs-in`, `--results-to`) are for bot automations only.
  `automations remove` has the alias `delete`.
- 259: **Workspace Cleanup** (`orca workspace cleanup preview|run|report`) can remove finished automation-run and
  released worker workspaces; 262 extends it to the Orca, Claude Code and Codex chats of deleted workspaces. Off by
  default; it deletes within seconds of confirming, so leave it off.
- 265/266: **Orca Bots** (Slack, Gmail, Calendar, Drive and more as `orca bot|slack|gmail|...`; 267 is a patch of
  266 that repeats the intro). Not used by lane-control; these are 69 of the 101 new commands.

## CLI diff, 1.4.250 vs 1.4.267 [ran]

Method: `orca-ide agent-context --json` of each version (312 commands in 250, 413 in 267), compared by command, flag
list, usage and notes. Root `--help` was also diffed.

- **Removed commands: none. Removed flags on any command: none. Changed positional arguments or argument mode:
  none.** 101 commands added (bots, Google, Slack, artifacts, plugin install/update, workspace cleanup, `pair`,
  `auth`, `agent list|show|read|installed`, `host rm`, `repo hide|unhide`, `worktree activate`, `vm env doctor`,
  `orchestration deny`, `serve reset-identity`, `tab annotations`, `extension`, `computer wait|launch-app`). 50 of
  the 312 shared commands changed, mostly notes.
- **Identical (usage, flags, notes):** `environment add|list|show|rm`, `orchestration
  run-create|run-list|run-current| check|worker-abandon|worker-show|task-list`, `terminal
  close|list|create|read|wait`, `status`, `repo list`, `worktree list|current`, `skills get|install`, `agent hooks
  status`, `automations list`.
- **`environment add` is unchanged: `--pairing-code <code>` only, no stdin and no file** (usage line, flags and
  source `specs/environment.js` all agree). So `core/scripts/pairing-code-stdin.cjs` is **still needed** on 267.
- Changed, additive only: `orchestration send` (`--on`, `--chat`, `--item`, `--body-file`, bot recipients; the
  default recipient is now the coordinator also after finishing), `reply` (`--body-file`), `worker-start` (`--item`;
  notes: `--terminal` without `--worktree`, setup "runs unless the project turns it off" instead of "default --setup
  to run", `--on` with `new-child`/`new-top-level`, `--model` per agent), `worker-list` and `request-show` (`--on`),
  `inbox` (`--run`), `worker-stop` and `worker-release` (notes), `ask` (note: "a worker that already finished waits
  for the answer instead"), `worktree create|set` (`--parent-environment`), `worktree rm` (note), `terminal send`
  (note), `serve` (`--bind`, `--allow-public`, `--refuse-public`), `host list` (note), `repo add` (note),
  `automations create|edit` (bot flags), `agent launch` (note on `--model`/`--effort`).

## What was run against 1.4.267 [ran]

A scratch headless server from the official tarball (`serve --port 6990 --bind 127.0.0.1 --no-pairing`, scratch HOME
and data folder, stopped afterwards; the desktop's port 6768 was never touched and stayed up throughout):

- Starts; reports `orcad 0.1.0+8cc9da34df17`, terminal daemon `healthy` (PTY self-test ok); `status` says
  `appVersion 1.4.267`.
- `repo add`, `worktree create` (with and without `--setup run`), `terminal create|send|read|close`, `host list`,
  `environment list`, `orchestration run-create --from <terminal>`, `run-list`, `check --terminal` all answer as in
  the 250 help. `run-create`, `run-current` and `check` outside a live Orca terminal refuse with
  `no_active_sender_terminal` (same contract as before; they need `ORCA_TERMINAL_HANDLE` or `--from`).
- orca-host-install's way of starting orcad (`orcad.js --json --bind 127.0.0.1 --port N`, first stdout line
  `orca_server_ready` with `pairing.url`) still works on the 267 slot's own Node: the readiness line carried a
  `pairing` object with `url`, `deviceId`, `scope`. **[ran]**
- The pairing preload runs under 267's Electron-as-Node: with an empty stdin it printed `pairing-code-stdin: no
  pairing code on stdin` and exited 2. That the substituted code then reaches `environment add` is **[read]**: the
  CLI takes `process.argv` when it runs (`main(process.argv.slice(2))`), after the preload. It was not run with a
  valid code because that would add an environment.
- Not decisive: with the repo's `orca.yaml` (`scripts.setup` touching `orca-setup-done`), `worktree create` ran **no
  setup on either version** on a headless server (250 was run the same way on a second scratch server, also stopped
  afterwards). So the setup-approval change (252) could not be confirmed or ruled out here; setup needs a desktop
  window.

## The suite with 1.4.267 on PATH: unaffected, as expected [ran]

`python3 -m unittest discover -s tests` through `~/.agents/bin/lane-heavy`, with the 267 `orca-ide` first on PATH
and the `ORCA_*` variables unset.

Result: **1141 tests, OK, 4 skipped, 525 s** (17 minutes wall, most of it waiting for a lane-heavy slot). No test or
script broke, so no baseline run on 1.4.250 was needed for comparison.

Read this result for what it is. The suite stubs Orca (`tests/stubs/orca-via-env`, `ORCA_STUB_STATE`), so the 267
`orca-ide` on PATH is most likely never called, and a pass says nothing about 267. It shows only that putting 267
first on PATH and unsetting `ORCA_*` breaks nothing, as expected. The CLI diff above is the real check: no command
or flag lane-control uses has gone.

## Docs and scripts that need updating (before or with an upgrade)

| Where | Change |
|---|---|
| `core/scripts/pairing-code-stdin.cjs` header, `native-orca/record.md` NO-6, `remote.md` | Say "through 1.4.267" instead of "1.4.250's `environment add` reads the code only from argv". The script stays. Note that a code is now single-use and expires (link 60 minutes): a failed `environment add` needs a new code, never a retry with the old one. |
| `docs/superpowers/specs/2026-11-19-native-orca-setup-design.md` (line on "no CLI for the desktop's pairing link") | From 1.4.254 `orca pair` prints the access link without Settings. It needs the desktop (or serve) to be on 254+; a 250 desktop has no `pair`. Say too that anything with a terminal on the desktop can now mint a link, so a lane's terminal on the desktop can too: keep the owner's step as the default and decide `orca pair` knowingly. |
| `native-orca/setup.md` (Nesting) | Default depth is 4 after 251, and an existing 1 moves to 4. Either state "set it back to 1 if you want one level" or say reviewers as sub-workers now work out of the box. |
| `orca-host-install` | **Does not accept the official server tarball** [ran, see below]. Add that (the slot is `orcad/` inside `orca-server-<ver>-<target>/`, named by its `.version`) **and** the tarball's Node at `runtimes/node-<sha>/bin/node` (see below). Add `--bind 127.0.0.1` to any `orca serve` we document. |
| `native-orca/remote.md` ("server too old" paragraph) | Name the actual gates (below) and say desktop and boxes are upgraded together. |
| `coordinator.md` (line 75), `design-lead.md`, `machine-coordinator.md`, `debugger.md`, `reviewer.md`, `night-manager.md`: "an agent on another host: find its Run in `run-list` and `send --to run:<id>`" | No route across servers on 256+: refused with `--on` ("--to term_... or orca_session_id:... not run:...") and delivered nowhere without it. It has to be `send --on <server> --to term_...|orca_session_id:...` (256+ at both ends) or the owner. Filed as its own bug; this PR only documents it. |
| `coordinator.md` (lines 43-44), `design-lead.md` (line 27), `native-orca-lanes/SKILL.md` (line 56): "Orca refuses `new-child` and `current` there" | Stale on 267: with `--on`, `new-child` is accepted when it comes with `--repo`, and falls back to top-level with a warning when the server is too old to nest. `current` is still invalid. |
| `native-orca/remote.md` line "Orca 1.4.250 has no CLI for it" (the share link) | Same fix as the spec row: `orca pair` from 254. |
| `native-orca/remote.md` "Reports across hosts" (marked 1.4.250) | 251 (mail after finishing, `worker_done` included, leaves the finished task unchanged) and 258 (#40983, a lost worker is reported to its coordinator) change what it says. |
| Lookups of another session's Run by objective in `run-list`: `night-manager.md` (Messages), the coordinator, design-lead, machine-coordinator, debugger and reviewer personas, `project-setup`'s fallback hint | With 262's retention a quiet day removes the Run. The personas' "if `run-current` shows none, create it" copes for one's own Run only; lookups of another session's Run do not. |
| `core/harnesses/{codex,pi}/record.md`, `docs/superpowers/specs/2026-11-06-project-setup-design.md` (version strings) | Bump only after the canary, with the others. |
| `orchestrators/native-orca/templates/lane-brief.md`, personas | Optional: long messages by `send --body-file`. `worker-start --terminal` no longer wants `--worktree`. |
| Version strings ("as tested: Orca 1.4.250") in `orca-cotal-setup.md`, harness records, `tests/test_harnesses.py:1115` | Re-test and bump only after the canary; the test asserts the string `1.4.250` in a record. |

**`orca-host-install` and the official tarball [ran with a fake `ssh` that only logs]**: `--bundle
orca-server-linux-x64.tar.gz` and `--from <unpacked>/orcad` are both refused with "is named orcad orcad but its
.version says 0.1.0+8cc9da34df17: not installing one server under another's name; nothing was copied", even with
`--version 1.4.267` or `--version 0.1.0+8cc9da34df17`. A copy of the `orcad/` folder renamed to the `.version`
passes validation and reaches the install step, **but only on this desktop and only while Node is unchanged**: the
tarball ships Node as `runtimes/node-<sha>/bin/node` beside `orcad/`, while `orca-host-install` looks only in
`node/archives/node-*-linux-<arch>.tar.gz` and the desktop's AppImage Node (x64 only). It found Node here only
because the packaged 1.4.250 Node has the same sha as 267's (`e32e2d16...`). Without the packaged desktop on PATH,
on arm64, or after a release that bumps Node, it refuses ("no node binary with sha ... found"). So the repack is not
a pin to rely on. `--bundle` has to learn both the `orcad/` slot named by its `.version` and the tarball's
`runtimes/node-<sha>/bin/node`. Also, the tarball's own launcher `bin/orca-ide` does not match `project-setup`'s
`shim_parts` pattern, so a box whose `ORCA_CLI` points at it fails with "is not the Orca CLI launcher"; the launcher
that orcad writes itself (`~/.orca/cli/bin/orca`) keeps the old shape, so the normal path is fine.

## Risks of mixed versions

- **No protocol fence between 250 and 267.** Protocol 3, minimum 2 in both, so the generic "The selected Orca server
  is too old for this client" (`protocol-compat`) cannot be what a 251-263 box showed against a 250-267 desktop. The
  incident that started this (a box that hit "server too old") is still unexplained; the candidates in the code are
  the capability gates below, and the canary should reproduce it.
- **Capability gates are the real "too old".** 267's code defines capabilities 250 lacks: `orchestration.peer-mail`,
  `peer-mail-types`, `peer-bot-mail`, `remote-owned-workers`, `worker-stop-helpers`, `paired-worker-approvals`,
  `person-approvals`, `terminal.send-approval-guard`, `terminal.host-pane-respawn`, `terminal.persistence`,
  `agent-session.trust-worktree`, `worktree.create-agent-options`, `worktree.create.exact-name`,
  `worktree.run-approved-setup`, `worktree.set.folder-parent`, `environment.doctor`, `host.recipe-vm-wake` (a static
  diff of the capability strings in the two builds' `shared/` trees, **[read]**; it is not `status` output). The CLI
  refuses or degrades when the server it reaches lacks one: `send --on` from a runtime without `peer-mail` fails
  with `server_mail_unsupported` ("too old to carry mail to another server", nothing sent); `worker-start --on` with
  `new-child` "starts it top-level with a warning when that server is too old to nest across machines"; project
  automations with `--setup` on an old host say "this host's Orca is too old for --setup". These messages are
  **[read]** from the CLI source.
- **Direction matters.** A newer CLI or desktop against an older box server loses the new features and fails with
  those messages; an older client against a newer server keeps working but gets none of them. Cross-server mail
  needs both ends on 256+ and cross-server workers 258+ (257 "paired coordinator work remains inert"). A box
  coordinator starts a lane on the desktop only with `--on`, so direction 2 (a newer box CLI against an older
  desktop server) is where lane-control is exposed; the doc has not run it.
- **A box that follows the desktop's newest slot drifts.** `orca-host-install` installs "the newest slot this
  desktop has built" by default, so a desktop upgrade silently changes what new boxes get, and an old box keeps its
  old server. After an upgrade, pass `project-setup --orca-version` (or `--orca-from`, `--orca-bundle`; they pass
  through to `orca-host-install --version`, `--from`, `--bundle`) on every box so the version is a decision.
- **Pairing is single-use from 254.** A link used by one 254+ client is dead for any second one, and codes expire. A
  250 client (its `environment add` does not exchange the code) paired to a 267 desktop keeps the old-style pairing
  and keeps working; the desktop's Settings marks such devices. Do not retry `environment add` with a used code.
- **Reachability exposure differs.** 250's `serve` binds every interface when given no address (seen in the scratch
  run: "orcad is bound to 0.0.0.0 and is reachable from the network"). 254+ refuses public source addresses by
  default; 267's default bind without `--bind` was not run here, and 268's is 0.0.0.0 (Canary results, step 5).
  `orca-host-install` already passes `--bind 127.0.0.1` to orcad directly, so boxes are fine either way.

## Risks of 1.4.267 itself (unverified, need a desktop and an agent)

1. **Setup handling.** The changes under "Setup handling" above may stop `orca.yaml`'s `scripts.setup` from running
   in a lane's new worktree (not approved, a different branch, a failed script, or a project setting), and lanes
   wait for `orca-setup-done`. Two outcomes are possible: in wait-for-setup mode the agent is never started
   (#38585), or in start-immediately mode the lane starts and, per `templates/lane-brief.md` step 1, waits at most
   10 minutes for `orca-setup-done` and then reports a missing setup. The canary has to say which. Check first.
2. **Run retention.** Settled Runs are deleted a day after their last activity (262). Anything of ours that looks a
   Run up later with `run-list` (OpenCode coordinators, cross-host sends to `run:<id>`) must expect it gone after a
   quiet day. Our docs say to find the Run by the session name in `run-list`; that still works for a live Run.
3. **Nested depth 4** changes the "single coordinator level" doctrine from a setting we rely on to a setting we must
   set (see the setup.md row).
4. **Wake behaviour.** The ORCH-1 recommendation kept a "check your mail" nudge for a reply-wake bug. The notes of
   251, 254 and 256 touch waking and idle reports (mail held behind a draft, an idle worker reported once per
   coordinator message) but none says the bug is fixed. Re-test the wake instead of dropping the nudge.
5. **Churn.** 16 releases in 14 days with changes to orchestration in nearly every one. A newer pin should be
   re-evaluated like this, by diffing `agent-context --json`, each time it moves.

## Canary before upgrading

On one box (a scratch container or a spare machine, not the owner's desktop) with the pinned server from the tarball
and a desktop-less coordinator, or on the owner's desktop only after the owner has backed up its Orca profile. Steps
6 and 7 reproduce the "server too old" message in both directions; they use scratch servers and a scratch repo,
never a real worker on an estate box and not the owner's desktop.

1. Create a lane with the real `orca.yaml` through `worker-start --worktree new-child`: record the project's setup
   setting (start-immediately or wait-for-setup), whether an approval prompt appeared, whether setup ran and
   `orca-setup-done` appeared, and whether the agent started or the lane reported a missing setup after 10 minutes.
2. A full lane: `run-create`, `worker-start --spec-file`, the worker's `worker_done`, `check` waking the coordinator
   without the nudge, `worker-release`, `worktree rm`.
3. `worker-abandon` and a worker whose terminal is closed: does the coordinator now hear that it was lost (1.4.258)?
4. Close the desktop and reopen it, then `run-list`: the Run is still there; wait a day and see it go, and check the
   lookups by objective listed in the docs table.
5. Pair a box to the upgraded desktop (`orca pair` or the Settings link) with `project-setup <repo> <box>` and
   confirm `pairing-code-stdin.cjs` still saves the code, once.
6. Direction 1 (older client, newer server), before the upgrade: a scratch 250 server (the installed package's own
   files, a scratch HOME and data folder, its own port) as the client, paired with `environment add` to a scratch
   267 server from the tarball (127.0.0.1 and a scratch repo). The saved environment lives only in the scratch 250
   profile. `--on` needs a terminal or chat agent on the client's Orca, and `run-create` and `check` outside one
   fail with `no_active_sender_terminal`, so run from an agent terminal there: `terminal create`, then `run-create
   --from <handle>`, then, with `--from <handle>`, `worker-start --on <scratch 267> --worktree new-top-level --repo
   <scratch repo>` with the cheapest agent. `send --on` does not exist in 250 (it arrived in 256, #40106), so it is
   not part of this step. Expect it to work without the new features. Agent sign-in: a scratch HOME has none, so the
   owner signs the agent in once there; never copy credentials into a scratch profile.
7. Direction 2 (newer client, older server), the likely incident: a scratch server and CLI pinned to one version,
   1.4.263 from its tarball (there is no server tarball before 256, and 251-255 lack `send --on`), paired to the
   scratch 250 server of step 6, with a scratch repo. Run from an agent terminal on the 263 side (`terminal create`,
   `run-create --from <handle>`, then `--from <handle>`): `worker-start --on <scratch 250> --worktree new-top-level
   --repo <scratch repo>` and the same with `--worktree new-child --repo <scratch repo>` (to see the nesting warning
   or refusal). Test mail in two separate ways: `send --to dispatch:<id>` to the worker, without `--on`, and `send
   --on <scratch 250> --to term_<the agent on the 250 side>`. (`send --on` with a `dispatch:`, `run:` or `task:`
   target is refused by 267's server, so the first is the Run-level route and the second the cross-server one.)
   Check that each mail actually arrives, not only that no error is printed. `server_mail_unsupported` is decided by
   the Orca running on the box side; this doc expected it could not appear on a 256+ box, but the canary saw the 263
   side refuse with it, judging the 250 server's capabilities (Canary results). For `send --to dispatch:<id>`, which
   goes through the 263 side's relay to a worker on the 250 server, the relay may itself refuse or drop it: record
   which. The scratch 250
   server must offer pairing and, having no `--bind`, binds every interface: run it only in a container or a network
   namespace, because it has to keep running for the rest of the step (`worker-start --on` and the mail tests), so
   stopping it after pairing is not an option. Same agent sign-in rule as step 6. Use the scratch 250 server as the
   server. Using the owner's desktop for this starts an agent on it, after pairing a box to it and adding a repo to
   it: do that only with the owner present and with a cleanup list (remove the environment on the box, remove the
   repo and worktrees on the desktop, revoke the share link, close the agent).
8. Then upgrade the desktop and all boxes to the same version (after 6 and 7).

## Canary results

Run 2026-11-21 (lc-seu) on **Orca 1.4.268** instead of 1.4.267: 268 was released that morning and was the newest
when the canary started, and its notes carry worker-coordination fixes that touch our open risks (#45819 "a worker's
final report wakes the managed agent that started it", #45370 mail held until the turn ends, #43918 one record per
Dispatch); 267 is a patch of 266, so converging on it would leave the estate one release behind on day one. The 268
and 263 server tarballs matched `orca-server-sha256sums.txt`.

Where it ran: an unprivileged docker network with no published ports (`--cap-drop=ALL`, `no-new-privileges`, one
non-root user, an empty HOME per container), never the desktop, its port, profile or saved environments.
- `lcc-desk`: a scratch 1.4.268 `serve` from the tarball, standing in for the desktop, with a scratch repo carrying
  lane-control's `orca.yaml` (and, from step 3 on, its `.gitignore`).
- `lcc-box`: a 1.4.268 orcad started the way `orca-host-install` starts it (`orcad.js --json --bind 127.0.0.1`).
- `lcc-s250`: a scratch 1.4.250 orcad, its slot assembled from the installed package's `orcad-template` (common
  files, `targets/linux-x64-glibc`, the packaged ripgrep) and run on the package's own Node under
  `~/.orca-remote/<version>` as on a box. That slot hashes to `0.1.0+3ce2fb9fdf7a` (the desktop's own 250 slot, with
  more files, is named `0.1.0+b086a7d15d60`); the code is 250's. 250's `serve` could not be used: under plain Node it
  finds no orcad template, and its orcad advertises 127.0.0.1, so it bound every interface inside this container
  only. The 1.4.263 side of step 7 ran in the same container under its own HOME (`serve --bind 127.0.0.1`).
- Agents: Claude with `--model haiku` (Haiku 5.5) only, signed in once per container by the owner; the credentials
  stayed in the container HOMEs and went with them. Two first-run prompts of Claude would otherwise hold a pane and
  were set off in the scratch profiles: `skipDangerousModePermissionPrompt` (Orca starts every Claude worker with
  `--dangerously-skip-permissions`, 250 as well as 268) and `fullscreenUpsellSeenCount`.

| Step | What ran | Result |
|---|---|---|
| 1 | `worker-start --worktree new-child --repo <scratch> --agent claude --model haiku --spec-file` with the real `orca.yaml`; again after committing a changed setup script | [ran] The project setting is `start-immediately` (`setupRunPolicy: run-by-default`, the default; no CLI changes it, so wait-for-setup was not tried). Receipt `setup: effective run, hookFound true, source orchestration_default`; a setup terminal ran the script (`__ORCA_SETUP_COMPLETE__…:0`), `orca-setup-done` was in the worktree's git dir within seconds, and the changed script ran too (its new line wrote its file). No approval prompt or refusal on the headless server. The agent started and reported "LANE-OK setup-done present". |
| 2 | Haiku coordinator by `agent launch`, `run-create --from <it>`, `worker-start --spec-file`, the worker's `worker_done`, `worker-release`, `worktree rm` | [ran] Start to `ready` in 4 s; the report reached the coordinator about 15 s later. Nothing nudged it: Orca typed "You have 1 orchestration message. Run orca orchestration check --run <run> --terminal <handle>." into the idle coordinator, which ran `check` and answered (a "Setup succeeded for worker" status came first, also waking it). `worker-release`: `released`, transcript captured. `worktree rm` refused "Failed to delete worktree at <path>. ?? .beads" while the scratch repo lacked lane-control's `.gitignore` (the setup line's untracked `.beads` link); with it, plain `worktree rm` worked. |
| 3 | Two sleeping workers: `worker-abandon` on one, `terminal close` on the other's agent terminal | [ran] Closing the terminal: within seconds the coordinator got a `[HIGH]` escalation "Worker lost without reporting … is gone (it was closed in Orca) without sending worker_done … Its Dispatch is closed as failed" with the retry command. `worker-abandon`: `abandoned`, "Possibly-live resources were retained; no process was stopped or deleted."; no mail (the coordinator did it itself); a later `worker-release` answered `retained`, `identity_unproven`, so its terminal had to be closed by hand. Workers that end a turn without reporting are also escalated ("Worker stopped without reporting"). |
| 4 | Stop the server (SIGINT), start it again, `run-list`, then mail to the idle coordinator | [ran] for the restart: both Runs listed, the coordinator's terminal and agent survived (the terminal daemon is separate). The mail was accepted with "Queued. run:<run> has not settled at its prompt (…); delivery waits until it does." and woke the idle coordinator about 2 minutes later, by itself. [not run: deleting a settled Run a day after its last activity needs a quiet day; the lookups by objective in the docs table stay as listed]. |
| 5 | Box-side pairing as `project-setup` does it: `project-setup --box-pairing add <name>` on the box, the desk server's link on stdin; then `status`; the same link again under another name; an empty stdin | [ran] `paired`, then `reachable`; the box's CLI reaches the desk as 1.4.268. The used link again: "environment add …: unauthorized: The host refused this pairing code. It may have expired or already been used by another device. …". Empty stdin: the preload exits 2, which `project-setup` reports only as "failed: exit 2". `orca pair` on the desk refused: "Runtime bind address is pinned to <ip>; refusing to widen to all interfaces" (also with `--address 127.0.0.1`); on a 268 `serve` started without `--bind` (it listens on 0.0.0.0) it printed a link to the container's network address. [not run: the whole `project-setup <repo> <box>`, which would start the adapting agents and install the `lane-tunnel@` systemd user unit; the step that uses `pairing-code-stdin.cjs` is the one above]. |
| 6 | Direction 1: the 250 CLI saves the 268 desk with `environment add` (code through `pairing-code-stdin.cjs`), then from a 250 terminal `run-create --from`, `worker-start --on <268> --worktree new-top-level --repo <scratch> --agent claude --model haiku` | [ran] Paired; `status --environment` says 1.4.268. The start was `ready`, setup ran on the 268 side, and the worker's `worker_done` came back to the 250 Run (`check` lists it). `--worktree new-child` with `--on` is refused by the 250 CLI before anything starts: "--on requires an exact remote worktree selector or new-top-level". `send --on` does not exist in 250. |
| 7 | Direction 2: a 263 server and CLI save the 250 server, then from a 263 terminal `worker-start --on <250>` with `new-top-level` and with `new-child`; mail by `send --to dispatch:<id>` and by `send --on <250> --to term_<worker>`. Also a 263 client against the 268 desk, the same way | [ran] Against 250: both starts are accepted (`ready`); `new-child` warns "Not nested under main: <server> needs an Orca update to nest across machines, so the worktree was created top-level." The worker's `worker_done` is listed at the 263 Run as `[HIGH]` "Rejected worker_done … expected handle <unknown>, pane <legacy>; received handle dispatch:<id>, pane <missing>" (`sender_not_assignee`) though the task settles as completed and the worker was told "Recorded". `send --to dispatch:<id>` arrived and woke the worker ("Accepted for relay … its delivery state is unknown here."), but haiku would not answer it: its brief names the coordinator as "Run home (relayed by Orca)" and the mail came from a raw `term_…`. `send --on` was refused on the 263 side: `server_mail_unsupported`, "Server <server> runs an older Orca that cannot receive agent mail from another server. Update Orca on <server>, then send again. Nothing was sent." So, unlike what step 7 expected, the 256+ side does produce it, from the old server's capabilities. `worker-list --on <250>` listed the 250 server's own Run's worker, not the two it ran for the 263 Run. Against 268: `new-top-level` worked, both mail routes arrived and woke the worker, and its answer came back to the 263 Run. `new-child` there was refused naming the wrong server ("… s250 has no workspace on this server for --worktree new-child"), apparently carried over from that terminal's earlier Run. The literal "server too old" text was not produced; the nearest are the two messages above. |

**Go for Orca 1.4.268**, desktop and every box on that one version. Nothing lane-control calls broke on 268: lanes
start with their setup, report, wake the coordinator and clean up; a lost worker is reported; pairing through
`pairing-code-stdin.cjs` works and codes are single-use. Mixed versions are only safe in one direction: an older box
against a 268 desktop worked (250 and 263 clients, steps 6 and 7), while a newer box against the 250 desktop loses
cross-server mail and gets its reports flagged as rejected (step 7). That is the exposure while the desktop stays on
250, and a reason to move it first and the boxes after it.

Risks that remain:
- The real desktop runs the Electron app, not `serve`: whether its window adds a setup-approval prompt for lanes
  (the orchestration path passed no approval and needed none here), and the project's start-immediately or
  wait-for-setup setting there, are only seen on the desktop. Check the first lane after the upgrade.
- `orca pair` is not a way to make the desktop's share link (step 5); remote.md says so. The Settings link with
  127.0.0.1 stays the owner's step.
- After a server restart, mail to an idle agent waited about 2 minutes (step 4). A coordinator that seems not to hear
  a report right after a restart should run `check` itself.
- Run retention (deleted a day after the last activity, 262) was not observed; lookups of another session's Run by
  objective still need the fix in the docs table.
- Cross-server wakes of an agent coordinator were not tested (the coordinator on the starting side was a shell);
  remote.md keeps reports across hosts poll-only.
- 268 was hours old; the next pin is re-evaluated the same way (diff `agent-context --json`, rerun this canary).
- `orca-host-install` still refuses the official tarball (CLI diff section), so boxes are pinned with
  `--orca-version` from the desktop's own slot after the desktop moves.

Cleanup: every worker was released or stopped and every agent terminal closed; the scratch worktrees were removed;
the containers `lcc-desk`, `lcc-box` and `lcc-s250` (with their HOMEs, scratch servers, repos, saved environments
and the two sign-ins), the network `lcc-net` and the image `lcc-canary:local` were deleted; the downloads stayed in
the session's scratch folder only.

## What was and was not done

Verified by running: the 1.4.267 version and checksums, the CLI diff (both `agent-context --json`, the installed
1.4.250 package's and 1.4.267's, run isolated), the scratch 267 server on port 6990 and the commands listed, the
scratch 1.4.250 server on port 6991 (started from the installed package's files, isolated, `repo add` and `worktree
create` only, then stopped), `orca-host-install` against the tarball (with a fake `ssh`), the pairing preload
starting under 267, and the suite. Only read: the release notes, the protocol version and Node sha (shipped files),
the capability diff and what each capability means, the "too old" messages in the CLI source, and everything under
"Risks of 1.4.267 itself". Not done, on the rules: no desktop upgrade, no agent, no pairing, no `environment add`
with a real code, no install on any box, no 250 versus 267 test of a lane with `orca.yaml` setup, and no direction-1
or direction-2 run (steps 6 and 7).

Since then the canary (Canary results) ran steps 1-3 and 5-7 and the restart part of 4 on scratch servers in
containers, with haiku agents the owner signed in: the lane with `orca.yaml` setup, the full lane and its wake, lost
workers, pairing through `pairing-code-stdin.cjs`, and both mixed-version directions. Still not done: anything on the
desktop itself, the desktop upgrade, Run deletion after a day, and the whole `project-setup <repo> <box>` run.
