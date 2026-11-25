# Native Orca on an SSH host

Two separate connections, each with its own tunnel. Both tunnels are systemd user units **on this machine** (the
desktop), so only outbound ssh from here is needed. The picture, and what the owner does by hand:
[how the parts fit](../../docs/how-it-fits.md#a-box-two-directions).
- **Direction 1**, the desktop controls the box (the box's Orca is the server): `orca-tunnel-<host>` (`ssh -L`), made
  by `orca-host-install --pair`. The box's server makes the pairing code and the script uses it: nothing for the owner.
- **Direction 2**, the box's coordinator starts lanes on the desktop (the desktop's Orca is the server):
  `lane-tunnel@<host>` (`ssh -R`), made by `project-setup` after the share link, which the owner makes.

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
   there from this machine's Orca install (the server and its node runtime, node checked against its sha, so the box
   downloads neither), starts it on the box's 127.0.0.1 (a systemd user unit when the box has a user manager, else a
   detached process), opens `orca-tunnel-<host>` (`ssh -L`, direction 1, a user unit here), reads the box server's
   pairing code itself and runs `orca environment add` here: no click and no code from the owner. Without `--pair` the
   script only puts the files there, for Orca's own Settings > SSH connect to reuse (a box with no internet).
   `<p>` is the first free port from 6769 (`--orca-port` to choose): the desktop listens on 6768, and the tunnel uses
   one port at both ends. **Ports 6769 and up are reserved for Orca tunnels** (the range project-setup searches is
   6769-6868): do not put your own services there, on this machine or on a box, or setup takes the next free one and a
   later run may find yours in the way.
   By default the server put there is the newest one this machine's Orca has built, so a box gets whatever the
   desktop has, and a box whose server is older than its desktop later fails as "server too old". A newer Orca
   reaching an older server (canary 2026-11-21, a 1.4.263 box against a 1.4.250 server) names the gap in two of three
   cases: `send --on` is refused with `server_mail_unsupported` ("Server <name> runs an older Orca that cannot receive
   agent mail from another server. Update Orca on <name>, then send again. Nothing was sent."), and `worker-start --on`
   with `new-child` starts top-level ("Not nested under main: <name> needs an Orca update to nest across machines, so
   the worktree was created top-level."). The third names nothing about versions and looks like a bug: the worker's
   `worker_done` arrives as a `Rejected worker_done` (`sender_not_assignee`) while the worker is told "Recorded" (see
   Reports across hosts). Keep the desktop and every box on one version. To pin a box to a known-good server:
   `--orca-version <v>` installs exactly that version (and fails when the source has no such
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
   and the coordinator in an Orca terminal of the box's own server, which shows in this machine's
   window through the paired environment. With `--no-start` no agent needs to be on the box yet; the run there adds
   `~/bin` and `~/.local/bin` to the end of its PATH, where agents are often installed.
   The design kit goes on the box the same way (design-kit spec, section 9): `~/.agents/bin/design-shots`, the
   `component-gallery` skill, the frontend brief and the design reviewer persona, and the design skills fetched there
   from their pinned upstream commits into the box's `~/.agents/skills`. That needs git and the network on the box;
   without them it says `design-skills missing`, and a frontend lane there reports `skills missing`, which the
   coordinator acts on as anywhere. Shots need Playwright and a browser on the box, which setup never installs; a skill
   that runs Node needs Node there. A box set up before the kit gets it on its next `project-setup <repo> <host>` or
   `--refresh` (both install the lane tools, `design-shots` among them, and the skills).
4. `core/scripts/machine-setup --host <host>` (skipped with `--no-machine-setup`): the box's own machine coordinator,
   night manager, lane sweep and machine check.
5. `seen here`: the coordinator's terminal is listed through `--environment <host>` from this machine.

### Beads on a box (lc-5c1)
What reaches a box today: nothing of beads. The project's `.beads` is a git-ignored link, so a clone of the project has
none, and setup copies lane-control and the project, not the owner's beads. The box's own `project-setup` run (step 3)
therefore does the same restore as on any new machine: with no beads there and the private beads repo
`<owner>/<project>-beads` on GitHub, it clones that repo to `~/code/<project>-beads` on the box, links `.beads` to it
and restores the database, issues and memories, with `bd bootstrap --yes` (on the owner's yes, which is asked because
that run has the owner's terminal). The box's coordinator then backs up straight to GitHub at each handover, exactly as
on the desktop.

The box signs in to GitHub itself, once, by the owner: a fine-grained token limited to the beads repos
(Contents: read and write, nothing else). Signing in to `gh` is not enough on its own: `gh repo clone` uses gh's
login, but `bd bootstrap` (it runs `dolt clone git+https://...`), `bd dolt push` and the coordinator's `git push` use
git's credentials over https. So after `gh auth login` (which offers it) run `gh auth setup-git`, or give git another
credential helper. Setup says that is needed when `gh` is not signed in there, and before asking for the owner's yes it
checks that git can read the beads repo over https without a prompt (`git ls-remote`), naming `gh auth setup-git` when
it cannot; it never reads, prints or copies the token.
A restored database makes bd print "beads.role not configured" on a box; it is a warning only (`bd config` sets it). Beads is
copied over ssh from the desktop on no path: the box's work would then have no off-box copy until copied back. Only one
machine works on a project's beads at a time: before moving a project, the old machine's last step is the backup
(`git push`, then `bd dolt push`, in the beads repo).

Agents on the box sign in there once, by the owner; setup never copies a credential. Setup refuses to run as root,
there as here.

## Direction 2: the box's coordinator runs lanes here
Direction 2 is a supported, first-class path, not a fallback (owner 2026-11-25: "coordinator lives on box for just that
reason. direction 2 must stay."). Orca's own model, a coordinator on the client that starts workers on paired servers
with `--on`, is covered by direction 1. lane-control also lets a project's coordinator live on its box, so that it keeps
working while the desktop is off or unreachable; to run lanes on the desktop it needs the box paired the other way.
The way is the share link and `lane-tunnel` (below). The documented fallback, for when the link step cannot be done, is
the `send --on-client` hand-off (Orca 1.4.272+; owner 2026-11-25): pending, not built or tried
([how the parts fit](../../docs/how-it-fits.md#a-box-two-directions)).

For what the box lacks (graphics, a browser, a GPU), its coordinator starts a lane in this desktop's Orca with
`--on <this machine>`; the lane works in its own clone here, pushes to GitHub, and it reports by Orca; across hosts that
is poll-only (Reports across hosts, below).
1. The owner makes a share link once per box, in Orca here: Settings > Servers > "Share this computer", choosing this
   computer's own address (127.0.0.1; a network address widens what the desktop listens on, and setup refuses such a
   link). Orca 1.4.250 has no CLI for it. `orca pair` (1.4.254 and later) is not a tested stand-in: in the canary
   (2026-11-21) a scratch 1.4.268 `serve` pinned with `--bind` to its container-network address refused it, also with
   `--address 127.0.0.1` ("Runtime bind address is pinned to <ip>; refusing to widen to all interfaces"). That wording
   says the offer widens the server to every interface; the widening itself was not observed, and a desktop listening
   on 127.0.0.1 was not tried. Keep to the Settings > Servers link with 127.0.0.1.
2. `project-setup <repo> <host>` asks for the link on a hidden prompt, in a terminal only (Enter, no terminal or
   `--no-desktop-lanes` skips it; a box already paired is not asked again). A terminal that cannot hide what is
   typed is refused instead of asked.
3. `core/scripts/lane-tunnel start --remote-port <box port> <host> <port>` runs `lane-tunnel@<host>.service`
   (direction 2's tunnel), a systemd user service from `core/templates/lane-tunnel@.service`: `ssh -N -R 127.0.0.1:<box
   port>:127.0.0.1:<port> <host>`, restarted always. `<port>` is the link's; `<box port>` is the same unless
   `--tunnel-remote-port` names another (Two ports, below). Its host and ports are in
   `~/.config/lane-control/tunnels/<host>.env` (`PORT`, and `REMOTE_PORT` only when it differs: a file from before
   has `PORT` only, which the unit reads as both). It needs a systemd user manager here (`loginctl enable-linger
   $USER` keeps it without a login session); without one nothing is paired.
   Before it starts, the box's port must be free there (`--box-pairing port`): anything else listening there, such
   as the box's own Orca server on 6768, would get the code. Setup then says to pass `--tunnel-remote-port`. The link is sent only once the box's end is open, the
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
   it), and `~/.config/lane-control/hosts/<host>.json` here (host, environment names, `tunnel_port` here and
   `tunnel_remote_port` on the box; a record from before has `tunnel_port` only, which is both).

The link is never in an argument, a log, a file, the settings store or a test's output (`tests/test_remote_native.py`,
`test_link_not_in_argv_or_files`). It lets the box run commands as the owner on this computer (no desktop control)
until it is revoked.

### Two ports
The box's Orca dials what the link says: 127.0.0.1 and the link's port. When that port is taken on the box (its own
Orca server on 6768, say), `project-setup <repo> <host> --tunnel-remote-port <box port>` opens the box's end there
(`lane-tunnel start --remote-port`) and rewrites the port of the link's endpoint before the link goes to the box, in
memory, changing nothing else in it. That is safe because the endpoint is only where the box dials: Orca pins this
desktop by the public key in the link and seals the token to it, and this desktop's Orca server does not check which
port the box dialled (read from Orca 1.4.250's and 1.4.268's source; not yet run against a real desktop). The box
saves the port it dials, so moving the box's end later means pairing again (`--remove-host`, then a new link).
Direction 1's `-L` tunnel has the same shape (one port at both ends, `--orca-port`) and is not changed.

### Do you need the tunnel?
This is about direction 2's tunnel, `lane-tunnel@<host>`, only; direction 1 and `orca-tunnel-<host>` are not affected.
The box's Orca dials the address the link names; the tunnel is one way to give it one.
- Not needed when the box can reach this machine directly (the same LAN, or both on Tailscale) and you accept this
  desktop's Orca listening on every network interface: a link made with that address ("Share this computer",
  choosing the LAN or Tailscale address) should then work on the box with no tunnel (not run here). The address you
  choose only sets what the link says. The first network pairing makes this desktop's Orca listen on every
  interface (0.0.0.0), not only that address (`orca pair --help`: "The first network pairing makes a loopback-only
  Orca listen on every interface"). So anything that can reach any of this machine's addresses can try the port
  (the LAN, a café's Wi-Fi, a public address); only a firewall limits it to the LAN or the tailnet, and the link's
  token is still needed. It stays that way across restarts while such a client remains paired.
- Needed when the desktop must stay on 127.0.0.1, or the box cannot reach it (NAT, a company network: only outbound
  ssh from here gets through). The box then dials its own 127.0.0.1, and `ssh -R` carries it here.
- What setup does: it takes only a 127.0.0.1 link and refuses any other, so it always sets up the tunnel. Pairing a
  box with a direct address is not something setup does; whether it should is the owner's decision.

## A host that takes only a smartcard
This section and the `--tunnel-` options are about direction 2's unit, `lane-tunnel@<host>`; they change nothing in
direction 1's `orca-tunnel-<host>`, which has no such options. That unit's ssh runs with `BatchMode`, so it can never
ask for a PIN or a passphrase, and (by default) it does not see your ssh agent. A jump host that accepts only a key on a
smartcard therefore needs the unit to use an agent that has the card unlocked. The owner unlocks it, then starts the tunnel:
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
  with the same options and the ports in `~/.config/lane-control/hosts/<host>.json` (`lane-tunnel start <options>
  --remote-port <tunnel_remote_port> <host> <tunnel_port>`). A re-run of `project-setup <repo> <host>` prints that
  line from the record: when the box cannot reach this machine (the tunnel is down), before it asks for a link, and
  while it runs, when a `--tunnel-` option was given. A new share link is not needed while the box still has this
  machine's environment: press Enter at the prompt and run that line instead.

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
tunnel stopped, and the command to run there later is printed. Direction 1 (`orca-tunnel-<host>`) stays; the output
names the two commands that drop it too.

## Reports across hosts
What a worker's report (`worker_done`) does, as far as it is known. "Known" below is Orca 1.4.250, where "tested"
means run on one machine in a scratch Run with a plain shell as the worker and no agent (`record.md`, 2026-11-20
UP-3); the canary block further down is 1.4.250, 1.4.263 and 1.4.268 on scratch servers.

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

Messages to a session on the other host: a `run:` target does not cross hosts. Without `--on` it goes to the local Orca,
where that Run does not exist; `run:` targets are refused with `--on` ("--on sends to one agent or bot on that server"), on
any pair of servers; `--environment` from an agent delivers no mail or is refused. The one route is `orca-ide orchestration
send --on <environment> --to term_<id> --subject "<subject>" --body "<message>"`, and only when both servers are 1.4.256 or
newer (peer mail). It was run once between scratch servers in the canary (`docs/research/2026-11-21-orca-newer.md`,
Canary results: a 1.4.263 shell terminal sending to a haiku agent on 1.4.268): the mail arrived, woke the agent, and its
answer came back to the sender's `check`. To a 1.4.250 server it is refused before anything is sent
(`server_mail_unsupported`, "runs an older Orca that cannot receive agent mail from another server"). Not yet run with
the owner's desktop or between two agent sessions, so a send that raises no error still proves nothing: treat nothing
sent this way as "delivered" until a reply or the peer's own word says so. `send --to dispatch:<id>` without `--on`
reached a coordinator's own worker on the other server through the Run's relay (to 1.4.250 and to 1.4.268), but it
needs a dispatch of yours, so it is no route to a peer session: for that, the route above or the owner.
1. Versions: `runtime.appVersion` in `orca-ide status --json` and in `orca-ide status --environment <environment> --json`
   (`runtime.capabilities` lists `orchestration.peer-mail`). If either is older, or does not say, the handoff goes
   through the owner.
2. The peer's handle: `orca-ide terminal list --environment <environment> --json` returns each terminal's `handle`
   (`term_...`, which belongs to that server) and `title`; take the one whose title is the peer's session name. Prefer
   `term_<id>`: a terminal handle survives `/clear`. `orca_session_id:<id>` also works as the `--to`, but it needs
   `agent list` (not in 1.4.250), a terminal agent has none until it first reports, and it changes after `/clear`. No
   terminal with that title: the owner.
3. The reply comes to the sender's own `check`. To answer mail from another server, use plain `orca-ide orchestration
   reply --id <msg_id>` (`reply --on` is refused). A sender does not poll for it or look up the other side's handle.
`worker_done` from the other server's lane is not a message of this kind: it is covered below.

Not verified: that a `worker_done` from a lane on the other Orca server (a lane started with `--on <environment>`)
reaches, or wakes, an agent coordinator on the box. Across hosts, treat a report as poll-only: the coordinator runs
`orca-ide orchestration check` right after it starts the lane, on each wake, and before it calls a quiet lane lost
(`orca-ide orchestration worker-show --dispatch <dispatch>` says what Orca knows of the worker). The personas say so
too. If a cross-host wake is lost, the night manager's idle check or the owner notices it; the coordinator never loops
on `check`. The night manager's idle check covers coordinators, the only long-lived sessions that start lanes (frontend lanes and the design reviewer report to the coordinator too). A live check needs a box with a signed-in agent coordinator and the owner's share link (record.md, NO-6); until
it is run, nothing here may be read as "delivered".

Canary 2026-11-21 (scratch servers in containers, a plain shell terminal as the coordinator on the side that
started the lane, a haiku worker on the other; docs/research/2026-11-21-orca-newer.md, Canary results):
- Client older than or equal to the worker's server (1.4.250 or 1.4.263 against 1.4.268): the worker's `worker_done`
  reaches the Run home and `check` there lists it; `send --to dispatch:<id>` and `send --on <server> --to term_...`
  (263 against 268) both reach the worker and wake it, and its answer comes back to the Run.
- Client newer than the worker's server (1.4.263 against 1.4.250): the task settles as completed, but the report is
  listed as `Rejected worker_done` [HIGH] ("expected handle <unknown>, pane <legacy>; received handle
  dispatch:<id>, pane <missing>", `sender_not_assignee`) while the worker is told "Recorded". `send --to
  dispatch:<id>` reaches the worker, but the haiku worker would not act on it, saying the sender was not its
  coordinator's handle (our reading: its brief names the coordinator only as "Run home (relayed by Orca)"); `send
  --on` is refused (`server_mail_unsupported`).
- Whether a report wakes an agent coordinator across hosts is not verified: on one server it does (Orca prompts the
  idle coordinator to run `check`), across servers only the Run's mail was seen. Reports across hosts stay poll-only.

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
   <dispatch>` (closes the worker's own terminal when it has one, else says `retained`). After `worker-abandon` the
   canary saw `retained` with `identity_unproven` for a worker that did have its own terminal, and it stayed open:
   then close that terminal yourself with `orca-ide terminal close --terminal <handle>` (the handle is in
   `worker-show`).
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
