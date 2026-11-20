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
   one port at both ends.
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
`--on <this machine>`; the lane works in its own clone here, pushes to GitHub, and its report comes back by Orca.
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
   box's network: setup refuses). The unit's ssh runs without your ssh agent, so it needs a key it can use alone,
   with no passphrase (`IdentityFile` in the ssh config it uses).
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

## Removing a box
`project-setup --remove-host <host>`: the box forgets this machine's environment (`environment rm`, and
`paired_environment` leaves its stores), `lane-tunnel@<host>` stops and its env file goes, and the owner is told to
revoke the link in Orca here: Settings > Servers, the link shared with that box. An unreachable box still has its
tunnel stopped, and the command to run there later is printed. Direction 1 stays; the output names the two commands
that drop it too.

## Not yet established
See `record.md` (phase 6): whether a cross-host `worker_done` wakes an agent coordinator on the box. The coordinator
persona runs `orchestration check` itself after a cross-host lane and on each wake, so it does not depend on it.
