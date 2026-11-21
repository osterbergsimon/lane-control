# Newer Orca than 1.4.250: what changes for lane-control (RES-3)

Date: 2026-11-21. Asked by the owner: "we should prob evaluate newer orca as well." The desktop here runs Orca
1.4.250 (nix-packaged); a downstream estate runs 1.4.251 to 1.4.263 and one of its boxes hit "server too old".

How to read this: **[ran]** means a command was run here and its output seen. **[read]** means taken from release
notes or from reading the shipped 1.4.267 code, not run. Nothing in this note touched the installed desktop Orca,
its server, its `~/.config/orca`, or any saved environment; no agent was started, nothing was paired and no
environment was added.

## Recommendation

**Pin to 1.4.250 today; upgrade to one exact newer version (1.4.267 or the newest by then) after a short canary, and
move the desktop and every box together.** Do not take "whatever is newest" on a box.

Why not upgrade now: the commands lane-control calls did not break (see CLI diff), but the things that decide whether
lanes work after an upgrade (setup approval for `orca.yaml`, Run retention, nested workers, worker-lost reports) can
only be seen with a desktop window and a real agent, and this evaluation was not allowed either. Why not wait
indefinitely: 1.4.250 is 16 releases behind with daily churn, the estate is already on 251-263, and 1.4.258 closes a
gap we recorded in UP-3 (a worker that can no longer report). Mixed versions are the main risk, not the new version
itself.

The canary (needs the owner, about an hour): see "Canary before upgrading" below.

## Versions

| | version | evidence |
|---|---|---|
| Desktop here | 1.4.250, orcad slot `0.1.0+b086a7d15d60` | [ran] `orca-ide --version`, slot `.version` |
| Newest release | v1.4.267, 2026-11-20 (orcad `0.1.0+8cc9da34df17`, Node 24.22.1) | [ran] `gh release list`, AppImage and server tarball |
| Releases since 250 | 251-259, 261-267 (there is no 260). 253 and 264 are small patches, 267 is a patch of 266 | [read] |
| Download integrity | AppImage sha512 equals `latest-linux.yml`; server tarball sha256 equals `orca-server-sha256sums.txt` | [ran] |
| Runtime protocol | 3 in both, minimum compatible client and server 2 in both | [ran] read from the shipped `protocol-version.js` of each |
| Bundled Node for orcad | same sha (`e32e2d16...`) in the 250 slot and the 267 server tarball | [ran] |

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
- 251: a finished worker can keep messaging its coordinator; mail sent after finishing, `worker_done` included, leaves
  the finished task unchanged (CLI `send` notes). Stopping a worker stops its helpers (`worker-stop`, `helpers` in the
  result). Mail waits for a draft typed in the pane, also in OpenCode and across a restart.
- 254: `check` refuses an ambiguous caller; an idle worker is reported once per coordinator message; a worker whose
  terminal dies during startup keeps its output.
- 255: a `reply` to a finished worker's report is refused and the CLI names the follow-up that works. Our contract
  already says a follow-up is a new Task, so this matches it. `worker-start` on a busy terminal is refused before any
  wait.
- 256: `send --on <server>` mails an agent on a paired server and the reply returns to the sender's own `check`.
  Needs the Orca the CLI talks to to carry mail between servers, otherwise `server_mail_unsupported` and nothing is
  sent.
- 258: a worker started with `--on` becomes the paired computer's own worker. A coordinator can answer a worker's
  permission prompt (`orchestration approve` is opt-in and hidden from help; `deny` is new in the CLI). A lost worker
  is reported to its coordinator (#40983). 257 says paired coordinator work "remains inert in this release", so 258
  is the first release where cross-server workers are real.
- 261: closing a chat worker's chat ends its Dispatch. `orchestration inbox --run <id>` keeps all mail of a Run.
- 262: **settled Runs are deleted from `orchestration.db` a day after their last activity.** Mail is named from a
  Run's mailbox by its coordinator. `worker-start --terminal` now starts in that terminal's own workspace (the CLI
  note changed from "pass --worktree for that terminal" to "omit --worktree").
- 263: CLI `terminal close` stops the terminal's process when the window does not confirm. 265: more paired-server
  coordination fixes (a Claude worker's helper-agent permission prompts, worker reports).
- New in the CLI (additive): `send --body-file <path|->` and `reply --body-file`, `worker-list --on`,
  `request-show --on`, `worker-start --item`. `--body-file` fits our long lane messages.

**Worktree, setup, terminals**
- 252: worktree setup runs only when the script's content matches an approved script (#37977, #37978); 254 remembers
  approved scripts (up to 20 per repo); 262 makes an earlier approval of a repo's `orca.yaml` count for later
  versions. `orca.yaml` here has a `scripts.setup` and says agents wait for its last line. **Not verified**, see below.
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
- 252, 263: orcad fixes (a supervised server does not start without its terminal daemon, the instance lock is
  defended and a dead installer's lock is reclaimed in seconds, only real version slots are evicted from the artifact
  cache).
- `serve` has `--bind <ip>`. On 1.4.250 `serve` listened on every interface (the scratch run below printed "orcad is
  bound to 0.0.0.0 and is reachable from the network"); on 1.4.267 `--bind 127.0.0.1` gives "local only".

**Agents and automations**
- Claude: 251 needs Claude 2.1.118 or later to clear a stuck "needs permission" status and installs one more managed
  hook for it; 254 can offer to match Claude Code's theme (off, asks once). Nothing here changes how lanes start.
- OpenCode 2: 255 starts it with the picked model, 257 accepts its hooks after the OSC report, 258 fixes the first
  prompt on 2.0.13 to 2.0.18, 266 installs Orca's own OpenCode, Cursor and others when a host has none. The agent
  ids `opencode` and `opencode2` still exist in 267's `worker-start` help.
- Automations: `--precheck`, `--precheck-timeout`, `--workspace`, `--workspace-mode` (`new-per-run`) and the notes on
  them are unchanged for project automations; `--setup run|skip` and `--extra-agent-args` too, except that
  `--extra-agent-args` now also accepts Antigravity. The new flags (`--bot`, `--on`, `--once`, `--agent`, `--model`,
  `--effort`, `--runs-in`, `--results-to`) are for bot automations only. `automations remove` has the alias `delete`.
- 259: **Workspace Cleanup** (`orca workspace cleanup preview|run|report`) can remove finished automation-run and
  released worker workspaces. Off by default; it deletes within seconds of confirming, so leave it off.
- 267: **Orca Bots** (Slack, Gmail, Calendar, Drive and more as `orca bot|slack|gmail|...`). Not used by lane-control;
  these are 69 of the 101 new commands.

## CLI diff, 1.4.250 vs 1.4.267 [ran]

Method: `orca-ide agent-context --json` of each version (312 commands in 250, 413 in 267), compared by command, flag
list, usage and notes. Root `--help` was also diffed.

- **Removed commands: none. Removed flags on any command: none. Changed positional arguments or argument mode:
  none.** 101 commands added (bots, Google, Slack, artifacts, plugin install/update, workspace cleanup, `pair`,
  `auth`, `agent list|show|read|installed`, `host rm`, `repo hide|unhide`, `worktree activate`, `vm env doctor`,
  `orchestration deny`, `serve reset-identity`, `tab annotations`, `extension`, `computer wait|launch-app`). 50 of the
  312 shared commands changed, mostly notes.
- **Identical (usage, flags, notes):** `environment add|list|show|rm`, `orchestration run-create|run-list|run-current|
  check|worker-abandon|worker-show|task-list`, `terminal close|list|create|read|wait`, `status`, `repo list`,
  `worktree list|current`, `skills get|install`, `agent hooks status`, `automations list`.
- **`environment add` is unchanged: `--pairing-code <code>` only, no stdin and no file** (usage line, flags and source
  `specs/environment.js` all agree). So `core/scripts/pairing-code-stdin.cjs` is **still needed** on 267.
- Changed, additive only: `orchestration send` (`--on`, `--chat`, `--item`, `--body-file`, bot recipients; the
  default recipient is now the coordinator also after finishing), `reply` (`--body-file`), `worker-start` (`--item`;
  note changes listed above), `worker-list` and `request-show` (`--on`), `inbox` (`--run`), `worker-stop` and
  `worker-release` (notes), `ask` (note: "a worker that already finished waits for the answer instead"),
  `worktree create|set` (`--parent-environment`), `worktree rm` (note), `terminal send` (note), `serve` (`--bind`,
  `--allow-public`, `--refuse-public`), `host list` (note), `repo add` (note), `automations create|edit` (bot flags),
  `agent launch` (note on `--model`/`--effort`).

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

## The test suite against the 1.4.267 CLI [ran]

`python3 -m unittest discover -s tests` through `~/.agents/bin/lane-heavy`, with the 267 `orca-ide` first on PATH and
the `ORCA_*` variables unset.

Result: **1141 tests, OK, 4 skipped, 525 s** (17 minutes wall, most of it waiting for a lane-heavy slot). No test or
script broke, so no baseline run on 1.4.250 was needed for comparison.

The suite stubs Orca (`tests/stubs/`, `ORCA_STUB_STATE`), so a pass says our scripts and docs do not depend on
anything 267 removed; it says nothing about runtime behaviour. The CLI diff above is the stronger check: no command
or flag lane-control uses has gone.

## Docs and scripts that need updating (before or with an upgrade)

| Where | Change |
|---|---|
| `core/scripts/pairing-code-stdin.cjs` header, `native-orca/record.md` NO-6, `remote.md` | Say "through 1.4.267" instead of "1.4.250's `environment add` reads the code only from argv". The script stays. Note that a code is now single-use and expires (link 60 minutes): a failed `environment add` needs a new code, never a retry with the old one. |
| `docs/superpowers/specs/2026-11-19-native-orca-setup-design.md` (line on "no CLI for the desktop's pairing link") | From 1.4.254 `orca pair` prints the access link without Settings. It needs the desktop (or serve) to be on 254+; a 250 desktop has no `pair`. Say too that anything with a terminal on the desktop can now mint a link, so a lane's terminal on the desktop can too: keep the owner's step as the default and decide `orca pair` knowingly. |
| `native-orca/setup.md` (Nesting) | Default depth is 4 after 251, and an existing 1 moves to 4. Either state "set it back to 1 if you want one level" or say reviewers as sub-workers now work out of the box. |
| `orca-host-install` | **Does not accept the official server tarball** [ran, see below]. Add that (the slot is `orcad/` inside `orca-server-<ver>-<target>/`, named by its `.version`). Add `--bind 127.0.0.1` to any `orca serve` we document. |
| `native-orca/remote.md` ("server too old" paragraph) | Name the actual gates (below) and say desktop and boxes are upgraded together. |
| `orchestrators/native-orca/templates/lane-brief.md`, personas | Optional: long messages by `send --body-file`. `worker-start --terminal` no longer wants `--worktree`. |
| Version strings ("as tested: Orca 1.4.250") in `orca-cotal-setup.md`, harness records, `tests/test_harnesses.py:1115` | Re-test and bump only after the canary; the test asserts the string `1.4.250` in a record. |

**`orca-host-install` and the official tarball [ran with a fake `ssh` that only logs]**: `--bundle
orca-server-linux-x64.tar.gz` and `--from <unpacked>/orcad` are both refused with "is named orcad orcad but its
.version says 0.1.0+8cc9da34df17: not installing one server under another's name; nothing was copied", even with
`--version 1.4.267` or `--version 0.1.0+8cc9da34df17`. Copying the `orcad/` folder to a folder named
`0.1.0+8cc9da34df17` passes validation and reaches the install step (the fake `ssh` stopped it there). So the pin
works today with a one-line repack, and `--bundle` should learn the official layout. A box can then be pinned to a
release of Orca without building a slot on a desktop.

## Risks of mixed versions

- **No protocol fence between 250 and 267.** Protocol 3, minimum 2 in both, so the generic "The selected Orca server
  is too old for this client" (`protocol-compat`) cannot be what a 251-263 box showed against a 250-267 desktop. I
  could not find which message ask 2 hit; the candidates in the code are the capability gates below.
- **Capability gates are the real "too old".** 267's status lists capabilities 250 lacks:
  `orchestration.peer-mail`, `peer-mail-types`, `peer-bot-mail`, `remote-owned-workers`, `worker-stop-helpers`,
  `paired-worker-approvals`, `person-approvals`, `terminal.send-approval-guard`, `terminal.host-pane-respawn`,
  `terminal.persistence`, `agent-session.trust-worktree`, `worktree.create-agent-options`,
  `worktree.create.exact-name`, `worktree.run-approved-setup`, `worktree.set.folder-parent`, `environment.doctor`,
  `host.recipe-vm-wake` (diff of the two `shared/` trees **[ran]**). The CLI refuses or degrades when the server it
  reaches lacks one: `send --on` from a runtime without `peer-mail` fails with `server_mail_unsupported` ("too old
  to carry mail to another server", nothing sent); `worker-start --on` with `new-child` "starts it top-level with a
  warning when that server is too old to nest across machines"; project automations with `--setup` on an old host
  say "this host's Orca is too old for --setup". These messages are **[read]** from the CLI source.
- **Direction matters.** A newer CLI or desktop against an older box server loses the new features and fails with
  those messages; an older client against a newer server keeps working but gets none of them. Cross-server mail
  needs both ends on 256+ and cross-server workers 258+ (257 "paired coordinator work remains inert").
  lane-control's `remote.md` already says poll-only across hosts, so nothing we ship needs these today.
- **A box that follows the desktop's newest slot drifts.** `orca-host-install` installs "the newest slot this
  desktop has built" by default, so a desktop upgrade silently changes what new boxes get, and an old box keeps its
  old server. After an upgrade, pass `--orca-version` or `--orca-bundle` on every box so the version is a decision.
- **Pairing is single-use from 254.** A link used by one 254+ client is dead for any second one, and codes expire.
  A 250 client (its `environment add` does not exchange the code) paired to a 267 desktop keeps the old-style pairing
  and keeps working; the desktop's Settings marks such devices. Do not retry `environment add` with a used code.
- **Reachability exposure differs.** 250's `serve` binds every interface when given no address (seen in the scratch
  run: "orcad is bound to 0.0.0.0 and is reachable from the network"). 254+ refuses public source addresses by
  default; 267's default bind without `--bind` was not run here. `orca-host-install` already passes `--bind
  127.0.0.1` to orcad directly, so boxes are fine either way.

## Risks of 1.4.267 itself (unverified, need a desktop and an agent)

1. **Setup approval.** The 252 change may stop `orca.yaml`'s `scripts.setup` from running in a lane's new worktree
   until approved, and lanes wait for `orca-setup-done`. The result would be an agent that never starts. Check first.
2. **Run retention.** Settled Runs are deleted a day after their last activity (262). Anything of ours that looks a
   Run up later with `run-list` (OpenCode coordinators, cross-host sends to `run:<id>`) must expect it gone after a
   quiet day. Our docs say to find the Run by the session name in `run-list`; that still works for a live Run.
3. **Nested depth 4** changes the "single coordinator level" doctrine from a setting we rely on to a setting we must
   set (see the setup.md row).
4. **Wake behaviour.** The ORCH-1 recommendation kept a "check your mail" nudge for a reply-wake bug. The notes of
   251, 254 and 256 touch waking and idle reports (mail held behind a draft, an idle worker reported once per
   coordinator message) but none says the bug is fixed. Re-test the wake instead of dropping the nudge.
5. **Churn.** 16 releases in 15 days with changes to orchestration in nearly every one. A newer pin should be
   re-evaluated like this, by diffing `agent-context --json`, each time it moves.

## Canary before upgrading

On one box (a scratch container or a spare machine, not the owner's desktop) with the pinned server from the tarball
and a desktop-less coordinator, or on the owner's desktop only after the owner has backed up its Orca profile:

1. Create a lane with the real `orca.yaml`: setup ran and `orca-setup-done` appeared; the agent started.
2. A full lane: `run-create`, `worker-start --spec-file`, the worker's `worker_done`, `check` waking the coordinator
   without the nudge, `worker-release`, `worktree rm`.
3. `worker-abandon` and a worker whose terminal is closed: does the coordinator now hear that it was lost (1.4.258)?
4. Close the desktop and reopen it, then `run-list`: the Run is still there; wait a day and see it go.
5. Pair a box to the upgraded desktop (`orca pair` or the Settings link) with `project-setup <repo> <box>` and
   confirm `pairing-code-stdin.cjs` still saves the code, once.
6. Then upgrade the desktop and all boxes to the same version.

## What was and was not done

Verified by running: the version and checksums, the CLI diff, the capability diff, the scratch 267 server and the
commands listed, `orca-host-install` against the tarball (with a fake `ssh`), the preload starting under 267, the
suite. Only read: the release notes, the meaning of the capabilities, the "too old" messages in the CLI source, and
everything under "Risks of 1.4.267 itself". Not done, on the rules: no desktop upgrade, no agent, no pairing, no
`environment add` with a real code, no install on any box, no 250 versus 267 test of a lane with `orca.yaml` setup.
