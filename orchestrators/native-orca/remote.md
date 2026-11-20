# Native Orca on an SSH host

`project-setup <repo> <host>` sets a project up on an SSH host (a box) and keeps it driven from this machine's Orca
window (design: `docs/superpowers/specs/2026-11-19-native-orca-setup-design.md`, section 7). `<repo>` is a git URL or
an absolute (or `~/`) path on the host; `<host>` is what ssh calls it (`LANE_SSH_CONFIG` names a separate ssh config,
passed as `ssh -F`, for trial hosts). `--host <host>` means the same. A host that is not a plain name (an option, a
space, a newline) is refused before anything runs. Setup's own ssh calls never wait for a password (`BatchMode`,
`ConnectTimeout`, keepalives); only the host's own run keeps a terminal, to ask the owner. So run `ssh <host>` once
by hand first: with `BatchMode` an unknown host key is refused ("Host key verification failed"), not asked about. With `--show` or
`--refresh`, only the copy and the host's own `--show` or `--refresh` run. Every step says `exists` on a re-run.

## Direction 1: this machine drives the box
1. lane-control is copied to `~/.local/share/lane-control` on the box, and the box's copy says which orchestrator the
   project is on there (`--which-orchestrator`). A project the box already runs on Orca + Cotal keeps that path, and
   nothing below is done for it.
2. Orca's server on the box: when the environment `<host>` (every character but letters, digits, `.`, `_`, `-` made
   `-`) does not answer here, `orchestrators/orca-cotal/scripts/orca-host-install <host> --pair --port <p>` puts it
   there from this machine's Orca install and pairs it here through a `-L` tunnel (`orca-tunnel-<host>.service`).
   `<p>` is the first free port from 6769 (`--orca-port` to choose): the desktop listens on 6768, and the tunnel uses
   one port at both ends. **Ports 6769 and up are reserved for Orca tunnels** (the range project-setup searches is
   6769-6868): do not put your own services there, on this machine or on a box, or setup takes the next free one and a
   later run may find yours in the way.
   By default the server put there is the newest one this machine's Orca has built, so a box gets whatever the
   desktop has, and a box whose server is older than its desktop later fails as "server too old". To pin a box to a
   known-good server: `--orca-version <v>` installs exactly that version (and fails when the source has no such
   version): a slot's folder name, its own `.version`, or the Orca app version its `out/package.json` records (the
   `1.4.251` of a "server too old" complaint); it goes on the box under the slot's own name. `--orca-from <dir>` or
   `--orca-bundle <tar>` take it from a slot folder (or a folder of slots) or a tar instead of this machine's own, and
   the three pass straight to `orca-host-install --version/--from/--bundle`. A folder whose `.version` names another
   server than its own name does is refused as mislabelled. An Orca app's own `resources/orcad-template` (the packaged
   app) is not a slot and is refused with a pointer: connect a host once from that Orca and give its `ORCA_ARTIFACTS`
   folder (`~/.config/orca/orcad-artifacts/<platform>`) as `--orca-from`. It
   prints one line saying what it did: `installed orcad-<ver> - from <source>`, or `exists` when that version is
   already on the box. The options matter only while the box has no answering server yet; on one that answers,
   project-setup says it did not use them. To change a running box, run `orca-host-install` itself.
3. The box's copy of `project-setup` runs there (`--via-host`, over `ssh -t` when there is a terminal). It does the
   native setup of phase 3 there: rendered personas, the persona hook, the AGENTS.md sections, the settings store
   and the coordinator (and design lead) in Orca terminals of the box's own server, which show in this machine's
   window through the paired environment. With `--no-start` no agent needs to be on the box yet; the run there adds
   `~/bin` and `~/.local/bin` to the end of its PATH, where agents are often installed.
4. `core/scripts/machine-setup --host <host>` (skipped with `--no-machine-setup`): the box's own machine coordinator,
   night manager, lane sweep and machine check.
5. `seen here`: the coordinator's terminal is listed through `--environment <host>` from this machine.

Agents on the box sign in there once, by the owner; setup never copies a credential. Setup refuses to run as root,
there as here.

## Direction 2: the box's coordinator runs lanes here
For what the box lacks (graphics, a browser, a GPU), its coordinator starts a lane in this desktop's Orca with
`--on <this machine>`; the lane works in its own clone here, pushes to GitHub, and it reports by Orca; across hosts that
is poll-only (Reports across hosts, below).
1. The owner makes a share link once per box, in Orca here: Settings > Servers > "Share this computer", choosing this
   computer's own address (127.0.0.1; a network address widens what the desktop listens on, and setup refuses such a
   link). Orca 1.4.250 has no CLI for it.
2. `project-setup <repo> <host>` asks for the link on a hidden prompt, in a terminal only (Enter, no terminal or
   `--no-desktop-lanes` skips it; a box already paired is not asked again). A terminal that cannot hide what is
   typed is refused instead of asked.
3. `core/scripts/lane-tunnel start <host> <port>` runs `lane-tunnel@<host>.service`, a systemd user service from
   `core/templates/lane-tunnel@.service`: `ssh -N -R 127.0.0.1:<port>:127.0.0.1:<port> <host>` with the link's port,
   restarted always. Its host and port are in `~/.config/lane-control/tunnels/<host>.env`. It needs a systemd user
   manager here (`loginctl enable-linger $USER` keeps it without a login session); without one nothing is paired.
   Before it starts, the port must be free on the box (`--box-pairing port`): anything else listening there, such as
   the box's own Orca server on 6768, would get the code. The link is sent only once the box's end is open, the
   unit is active, and the port is open on the box's loopback only (sshd's `GatewayPorts yes` would open it on the
   box's network: setup refuses). By default the unit's ssh runs without your ssh agent, so it needs a key it can
   use alone, with no passphrase (`IdentityFile` in the ssh config it uses). See "A host that takes only a smartcard"
   below for one that does not.
4. The link goes to the box on ssh's stdin. There `project-setup --box-pairing add <this machine>` runs Orca's CLI as
   the launcher Orca wrote (`~/.orca/cli/bin/orca`) does, plus `node --require core/scripts/pairing-code-stdin.cjs`,
   with `environment add --name <this machine> --pairing-code -`: the preload reads the code from stdin and puts it in
   the CLI's own argument list inside the process. Orca 1.4.250's `environment add` takes the code only as an
   argument, so this keeps it out of `ps` and `/proc`.
   If that step fails, the tunnel is stopped again and the owner is told to revoke the link, since it may have
   reached the box.
5. Only the name is kept: `paired_environment` in the box's project settings store (the coordinator persona reads
   it), and `~/.config/lane-control/hosts/<host>.json` here (host, environment names, tunnel port).

The link is never in an argument, a log, a file, the settings store or a test's output (`tests/test_remote_native.py`,
`test_link_not_in_argv_or_files`). It lets the box run commands as the owner on this computer (no desktop control)
until it is revoked.

## A host that takes only a smartcard
The unit's ssh runs with `BatchMode`, so it can never ask for a PIN or a passphrase, and (by default) it does not see
your ssh agent. A jump host that accepts only a key on a smartcard therefore needs the unit to use an agent that has
the card unlocked. The owner unlocks it, then starts the tunnel:
- `lane-tunnel start --auth-sock <agent socket> <host> <port>` puts `SSH_AUTH_SOCK=<socket>` in the instance's env file
  (`LANE_TUNNEL_AUTH_SOCK` does the same). `--ssh <path>` (or `LANE_TUNNEL_SSH`) runs an executable of yours in place
  of `ssh`, with ssh's arguments, for a card that needs a wrapper. Both paths are absolute, with no line break, and
  the wrapper must be executable; neither is a secret.
- `--no-enable` only starts the unit (`systemctl --user start`): it is not enabled, so a login or reboot does not
  bring up a tunnel whose card is locked, and a start that enabled it earlier is undone. An estate can start it from
  its own "unlock the card, then bring things up" command. `lane-tunnel stop` always leaves the unit stopped and
  disabled.
- `project-setup <repo> <host>` passes these on: `--tunnel-auth-sock <socket>`, `--tunnel-ssh <path>` and
  `--tunnel-no-enable`. They are read here only; the box's own run never sees them. They apply while the box is
  being paired. Bringing the tunnel up again later (after a reboot, or when the card was locked) is `lane-tunnel start`
  with the same options and the port in `~/.config/lane-control/hosts/<host>.json` (`tunnel_port`); a new share link
  is not needed while the box still has this machine's environment.

A key or card the unit cannot use is not a silent restart loop, and a short outage does not end the tunnel. The unit
never gives up (`StartLimitIntervalSec=0`): it retries every 10 seconds at first and backs off to every 5 minutes
(`RestartSteps`, `RestartMaxDelaySec`; systemd 254 or later, an older one retries every 30 seconds), and ssh gives up
on a dead network after 15 seconds (`ConnectTimeout`). So a network that is down for an hour, or a box whose sshd still
holds the old forward for a while (the next ssh fails on `ExitOnForwardFailure` until it lets go), ends with the tunnel
back on its own, and a locked card costs about a dozen refused logins an hour, not a burst.
The backoff counter adds up across all automatic restarts since the last manual start, not within one bad stretch, so
after a bad stretch (or several blips over weeks) every later drop waits the full 5 minutes. To get back to the fast
first retries, run `systemctl --user restart lane-tunnel@<host>` (or `lane-tunnel stop <host>`, then `start`); a plain
`lane-tunnel start` does not reset it.

`lane-tunnel start` looks again after a few seconds (`LANE_TUNNEL_SETTLE`, default 10), while you are there to read it.
If the unit is not running it exits 1 and names the agent it used and `journalctl --user -u lane-tunnel@<host>.service`.
Without `--no-enable` the unit stays enabled and keeps retrying (a re-run during a network blip must not turn an
always-on tunnel off), and the message says so; `lane-tunnel stop <host>` ends it. With `--no-enable` it is stopped
again. The env file stays either way, so `start` again retries. `project-setup` stops and forgets a tunnel whose
start failed. While the unit is retrying, `lane-tunnel status` exits non-zero. The env file's values may not hold a
line break, a backslash, a leading quote or leading or trailing spaces (systemd reads them differently from sh).

## Removing a box
`project-setup --remove-host <host>`: the box forgets this machine's environment (`environment rm`, and
`paired_environment` leaves its stores), `lane-tunnel@<host>` stops and its env file goes, and the owner is told to
revoke the link in Orca here: Settings > Servers, the link shared with that box. An unreachable box still has its
tunnel stopped, and the command to run there later is printed. Direction 1 stays; the output names the two commands
that drop it too.

## Reports across hosts
What a worker's report (`worker_done`) does, as far as it is known. Orca 1.4.250; "tested" means run on one machine
in a scratch Run with a plain shell as the worker and no agent (`record.md`, 2026-11-20 UP-3).

Known:
- On one machine, mail to a coordinator's Run reaches its session as Orca's one-line message prompt (it waits while
  the session is busy); the coordinator then runs `orca-ide orchestration check`. Seen again while testing UP-3.
- `worker_done` needs `--outcome succeeded|failed`, `--task-id` and `--dispatch-id`, and is accepted only from the
  terminal the dispatch was made for. Tested refusals, each printed in the worker's terminal and also left in the
  coordinator's Run inbox as a `Rejected worker_done` message with the code, which `check` shows:
  - no `--task-id`: `missing_task_id` ("worker_done requires taskId")
  - no `--dispatch-id`: `missing_dispatch_id` ("worker_done requires dispatchId")
  - sent from a terminal that does not own the dispatch: `worker_identity_changed` ("<terminal> is not the exact
    process that owns Dispatch <dispatch>"). The same code is what the issue behind this page saw after a runtime
    restart; the restart itself was not tested.
  - an accepted report prints `lifecycle=completed` and settles the task.

Not verified: that a `worker_done` from a lane on the other Orca server (a lane started with `--on <environment>`)
reaches, or wakes, an agent coordinator on the box. Across hosts, treat a report as poll-only: the coordinator runs
`orca-ide orchestration check` right after it starts the lane, on each wake, and before it calls a quiet lane lost
(`orca-ide orchestration worker-show --dispatch <dispatch>` says what Orca knows of the worker). The personas say so
too. A live check needs a box with a signed-in agent coordinator and the owner's share link (record.md, NO-6); until
it is run, nothing here may be read as "delivered".

## A dispatch that can no longer report
A worker can finish its work and still be unable to report: a runtime restart left its dispatch with another owner
(`worker_identity_changed`), so every `worker_done` is refused. The work is on the lane's branch, so nothing is lost.
Coordinator:
1. `check` shows the `Rejected worker_done` with the code and the dispatch id; `orca-ide orchestration worker-show
   --dispatch <dispatch>` shows what Orca knows of the worker. Orca's own rule holds: silence or an `unverifiable`
   liveness alone never proves the agent is gone.
2. First read the lane's branch (`git fetch origin <branch>`, `git log`, `git diff`) and decide what is left: finished,
   half done, or not started.
3. Abandoning stops a lane's dispatch, so ask the owner first, even when the worker looks gone (Orca's own rule:
   silence or `unverifiable` liveness never proves it). Then
   `orca-ide orchestration worker-abandon --dispatch <dispatch>` (fences the dispatch, no process is touched;
   on one that already failed it says so and changes nothing) and `orca-ide orchestration worker-release --dispatch
   <dispatch>` (closes the worker's own terminal when it has one, else says `retained`).
4. Start the work again on the same task (untested): `orca-ide orchestration worker-start --task <task> --retry-of
   <dispatch> --worktree path:<the lane's worktree> --agent <id> --model <model>` (add `--on <environment>` for a
   lane on the other server: placement is not inherited). Per Orca's help, `--retry-of` names the failed task's
   dispatch and reopens it itself, so do not set the task `ready` first; only if Orca refuses the retry because the
   task is not failed, set the dispatch's task failed (`task-update --id <task> --status failed --result
   "<why>" --run <run>`) and retry. A lane whose branch is finished gets a short spec: check the branch, run the
   gates, report. Its new dispatch has new ids, and its `worker_done` uses those. After three failed starts of one
   chain Orca's breaker stops it, and only the owner reopens it.
Tested on one machine with a plain dispatch (`dispatch`, not `worker-start`): after the worker's terminal closed the
dispatch was failed and its task `ready` again, `worker-abandon` said it was already settled, `worker-release` said
`retained` (no terminal of its own), and a second dispatch of the task reported and completed. Not tested: a real
runtime restart, `worker-start --retry-of`, and anything on a second Orca server.
