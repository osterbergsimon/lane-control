# Pairing a box with Orca's own features (PAIR-1)

Date: 2026-11-25. Issue lc-lxd. The owner's words, the same day: "investigate orca settings and see if we can automate
pairing in project-setup"; then "i think best way to pair is using orcas function to install a client/server on box via
ssh"; then "if we cannot pair automatically, we may want to make it an instruction in project-setup or in a prereq
stage"; then "we should probably look into what orca installs on the box ... perhaps we can automate it, or at least
ease install in airgaps". Orca 1.4.274 throughout. Related: the ORC-1 audit
(`docs/research/2026-11-25-orca-overlap.md` on its branch, rows 1-4 and 23), lc-65b (SSH targets), lc-7og, lc-eui,
lc-dqf (airgaps and the server bundle).

## Short answer

- **Setup cannot mint the direction-2 link itself without widening the desktop.** `orca pair --address 127.0.0.1`
  makes Orca listen on every interface (0.0.0.0) before it makes the offer, whatever the address. Read from 1.4.274's
  source and seen in a trial (below). Only the desktop window's own "Share this computer" with 127.0.0.1 makes an offer
  that keeps the desktop on loopback, and nothing but the window can ask for that. So the hand step stays. It is now a
  stated prerequisite: `project-setup <repo> <host>` names what pairing each direction has and gives the steps for the
  link before any box work, and `project-setup --check <repo> <host>` lists every missing prerequisite and changes
  nothing.
- **Orca's own SSH install is real and does most of direction 1**, but only from the window: Settings > "Orca on your
  SSH hosts" installs Orca's server on a box over the desktop's own SSH connection, keeps it up to date and saves it as
  a paired environment. The CLI can only look after one that exists (`environment status|update|rollback|recover|stop|
  forget`); nothing outside the window adds, connects or deploys an SSH host. It cannot be scripted from setup.
- **An SSH target is the most promising way to drop direction 2 altogether**: terminals Orca opens on an SSH target run
  `orca` commands against the desktop's own runtime (a setting on each target, on by default). A coordinator in such a
  terminal might start lanes on the desktop with plain `worker-start`, with no link and no `lane-tunnel`. That is read
  from the source and help, not run: it needs a desktop window to add and connect the target, so the headless TB-1
  testbed cannot try it (lc-65b).
- **Orca's own box install needs no network on the box.** The desktop pushes the server and a pinned Node 24.22.1 over
  SSH; only the desktop may download Node, and only for a box whose platform differs from its own, from nodejs.org or a
  mirror it is told about. orca-host-install does the same from the desktop's files.
- **Settings**: Orca keeps them in the profile's `profile-state.db`, which we may read (read-only, a copy at best) and
  never write. There is no CLI, RPC or documented file to change one. Writing them ourselves is not supported.

## 1. The owner's route first: Orca's own install on a box over SSH

Sources: `orca host --help`, `host list|rm|name --help`, `environment --help` and its managed-server commands,
`project setup-clone|setup-existing-folder --help`, `worktree create --help`, the orchestration guide's
`references/placement-and-remote.md`, `skills get orca-cli`, and the app's own code (`resources/app.asar`,
`app.asar.unpacked/out/shared/remote-runtime/`, `orcad-template/`), all read-only.

### 1.1 What the desktop installs on the box, and how

Orca has two ways to use a box over SSH. Both start in the window.

- **An SSH target** (Settings > SSH, or a host of `~/.ssh/config`, which Orca imports passively: every `Host` alias
  shows in `orca host list` as `ssh target ... not connected`). Connecting one (the window's `ssh:connect`) puts Orca's
  server code on the box and runs the box's terminals through it.
- **A managed Orca server** (Settings > "Orca on your SSH hosts": "Orca installs itself on an empty SSH host and keeps
  itself up to date"). The window's `runtimeEnvironments:deployOrcad` connects the SSH target, installs the server,
  starts it, forwards a local port to it over the same SSH connection and saves it as a paired environment
  (`orcadDeployment: {sshTargetId, localPort, remotePort}`), as orca-host-install `--pair` does by hand.

What goes on the box (the install code, `orcad-managed-serving`, `orcad-remote-node-runtime`, `orcad-artifacts.js`):

| What | Where on the box | Size (linux-x64-glibc) |
|---|---|---|
| A server slot: `orcad.js`, `orcad-server.js`, the worker entry files, `node_modules/node-pty` with its native addon, ripgrep, `bin/orca-provider-exec`, `.version`, `.server-target`, `.runtime-node` (the Node sha256) | `~/.orca-remote/` (a slot per version) | 83 MB |
| Node, the pinned build | `~/.orca-remote/runtimes/node-<sha256>/bin/node`, shared by the slots | 122 MB |
| The server's data: its own profile, pairing identity, terminal history | orca-host-install: `~/.orca`; Orca's own install: a folder of its own under the home (not traced) | grows with use |
| The machine key and activation record | `~/.orca-remote/orcad-machine.json`, `.orcad-activation-*` | small |

- **Which server**: the desktop's own build (`orcad 0.1.0+<hash>`, from `resources/orcad-template`), built into a slot
  for the box's platform: `linux-x64-glibc`, `linux-arm64-glibc`, `linux-x64-musl`, `linux-arm64-musl`, darwin and
  Windows; `linux-x64-glibc217` is an opt-in build for glibc 2.17. There is no version pin in the window: a managed
  server gets the desktop's version, and `orca environment update` moves it to the desktop's next one
  (`environment rollback` goes back one).
- **Node**: pinned in `shared/node-runtime/node-runtime-pin.js`: 24.22.1, each archive with its sha256 and the
  executable's sha256 (`linux-x64-glibc`: `node-v24.22.1-linux-x64.tar.gz`, executable
  `e32e2d16...cfafb`). The desktop has its own platform's Node in the app (`resources/node-runtime`): the scratch serve
  below built its slot from that, with no download. For another platform it downloads the archive on the desktop from
  `https://nodejs.org/dist/v24.22.1/` (linux-arm64-musl and the glibc 2.17 build:
  `https://unofficial-builds.nodejs.org/download/release/`), checks
  its sha256 and caches it in the desktop's `orcad-artifacts`. A mirror is honoured: the setting `nodeDistMirrorUrl`
  (Settings), or `NODEJS_ORG_MIRROR` / `NVM_NODEJS_ORG_MIRROR` in the desktop's environment. A Node already on the box
  is not used for the server (the slot names the sha256 it runs); the box never downloads it.
- **How it is copied**: uploaded over the desktop's SSH connection (SFTP), the Node archive unpacked there with `tar`,
  the executable bits set again, then a completion marker; the box needs `tar` and a home that allows running programs
  (a `noexec` home is refused with its own message).
- **How it runs**: started by the desktop over SSH with `setsid` and `nohup`, bound to 127.0.0.1 (`orcad-bind-address.js`:
  "SSH forwarding needs only loopback; network exposure must be an explicit operator choice"), on a port of the box's
  loopback. No systemd unit: after a box reboot it is down until the desktop connects again (the target's "Reconnect on
  launch", on by default). orca-host-install differs here: it writes a systemd user unit on the box when there is a
  user manager.
- **Updates**: from the desktop only (`environment update`, or the window). The box checks nothing on the network.

### 1.2 Can anything but the window start it?

No, in 1.4.274:
- **CLI**: `orca host` has `list`, `rm` and `name` only; `environment` adds a server only from a pairing code. The
  managed-server commands (`status`, `update`, `rollback`, `recover`, `stop`, `forget`) need one that exists and say
  "Runs on this machine's Orca desktop app, the same action as Settings > Orca on your SSH hosts. A runtime without the
  SSH registry (headless `orca serve`) refuses it." `project setup-clone` and `setup-existing-folder`: "SSH targets are
  cloned/set up through the desktop UI because the desktop client owns SSH connections".
- **The runtime's RPC** (what the CLI calls): no method adds, connects or deploys an SSH host. `ssh:addTarget`,
  `ssh:connect` and `runtimeEnvironments:deployOrcad` are Electron IPC handlers, reachable only from the window.
- **A deep link**: none found for SSH hosts (the only `orca://` link is `orca://pair?code=`).
- **The `~/.ssh/config` import** adds targets passively, so a box the owner can `ssh` to is already a target; it still
  has to be connected in the window.

### 1.3 With the box as an SSH target, where does an agent's `orca` go?

To the desktop, as far as the source and help say:
- Each SSH target has "Allow this host to control Orca browser tabs in its workspaces", on by default
  (`allowRemoteCliControl`): "Agents and orca commands on this host can open, read and use Orca browser tabs on this
  desktop ... Terminals started on this host by an older Orca also need this on to run orca orchestration commands."
  So `orca` commands in such a terminal reach the desktop's runtime (the relay's `remoteCli` bridge), while the
  desktop is connected.
- The orca-cli guide: "A terminal opened through Orca's older SSH relay can read mail only while it works a Task or
  coordinates a Run".
- A terminal of a **managed server** is different: it runs in the box's own server, and its `orca` goes to that
  server. Reaching the desktop from there is direction 2 as today (`--on <desktop>`, which needs the desktop's link).

If a coordinator in an SSH target's terminal can start a desktop lane with plain `worker-start --environment local`
(local being the desktop it talks to), direction 2 needs no link, no tunnel and no pairing. **Not run**: adding and
connecting a target is a window click, which the rules of this lane keep off the owner's desktop, and the headless
TB-1 testbed lists no SSH targets at all (lc-65b's notes).

### 1.4 Can the desktop start workers on the box?

Unclear. `worktree create --host ssh:<id>` is accepted by its help. The placement guide says of a project on an SSH
target: "a start cannot create a worktree there", so `worker-start --worktree new-child|new-top-level` does not; a
start into an existing SSH worktree (`--worktree path:<it>`) is not ruled out by anything read. Reports and cleanup of
such a worker were not traced. Open in lc-65b.

### 1.5 What it would replace, and what it cannot do

| Ours | Orca's | Verdict |
|---|---|---|
| orca-host-install `--pair` + `orca-tunnel-<host>` (`-L` unit) + `environment add` (direction 1) | "Orca on your SSH hosts": install, start, forward and pair in one window action; updates from the CLI | Replaceable in function, by a click per box. Keep ours for now: ours is scriptable, pins a version (`--orca-version`), and survives a box reboot (systemd user unit on the box). |
| The share link, `lane-tunnel` and `--box-pairing` (direction 2) | An SSH target's terminals run `orca` against the desktop | Possibly all of it, if 1.3 holds. Trial first (lc-65b). |
| `project-setup <repo> <host>`'s box code (copy, the box's own run) | `project setup-clone --host ssh:<id>`: window only | Keep. |

What Orca's route cannot do:
- **No desktop window**: everything above needs the desktop app (headless `orca serve` refuses the managed-server
  commands and has no SSH targets).
- **The desktop offline or closed**: the SSH connection, the forward and the relay are the desktop's. A box with its
  own coordinator keeps running on a managed server, but loses the desktop; an SSH-target coordinator stops with it.
- **Jump hosts and smartcards**: a target takes a jump host, a `ProxyCommand` and the system ssh's connection reuse,
  and asks for a passphrase in the window (`ssh:needsPassphrasePrompt`). A card unlocked in the owner's agent should
  work while the window runs (not tried). What it cannot do is what `lane-tunnel --no-enable` does: bring things up
  from an estate's own "unlock the card, then start" command.

## 2. The settings and the pairing state

Read-only throughout. Layout from a scratch profile (section 4), keys from the app's code; the owner's profile was not
opened.

| What | Where | Readable | Changeable other than the window |
|---|---|---|---|
| App settings (264 keys in a fresh profile), among them `workspaceCleanupPolicy`, `orchestrationWorkerAutoReleaseEnabled`, `nestedWorkerMaxDepth`, `nodeDistMirrorUrl`, `mobilePairingCustomAddress(es)`, `activeRuntimeEnvironmentId`, `sshRemoteCliControlExistingHostsMigrated` | `profiles/<id>/profile-state.db`, table `profile_state_documents`, domain `settings` (JSON) | yes: SQLite, opened read-only (`?mode=ro`), as the machine coordinator does | no |
| SSH targets, with `allowRemoteCliControl`, `reconnectOnLaunch`, `jumpHost`, `proxyCommand` | the same database, domain `sshTargets` | yes, read-only | `orca host rm` only |
| Paired clients and unused offers (`deviceId`, `token`, `scope`, `lastSeenAt`, `pairingReach`) | `orca-devices.json` in the profile root | holds live tokens: do not read it | no CLI revokes one (`mobile:revokeDevice` is window IPC) |
| Saved environments (servers this Orca is a client of), with their pairing codes | `orca-environments.json` | holds secrets: do not read it; `orca environment list|show` answers | `environment add|rm` |
| The running runtime: endpoints, its auth token | `orca-runtime.json` | holds a token; `orca status --json` answers | no |
| What the listener binds | not a setting: 127.0.0.1 until a client paired with a network reach has connected, then 0.0.0.0, also after restarts (`resolveInitialWebSocketBindHost`); `orca serve` defaults to 0.0.0.0, `--bind` pins it | `ss -ltn`, or `/proc/net/tcp` | no |
| Cleanup rules and auto-release | `settings`: `workspaceCleanupPolicy`, `orchestrationWorkerAutoReleaseEnabled` | read-only, as above; `workspace cleanup preview` shows their effect | no |

- **Changing a setting**: only the window writes them (`settings:set`, Electron IPC). There is no `orca settings`
  ("Unknown command"), no runtime RPC method for settings, and no documented settings file. **Writing the database or
  any of these files ourselves is not supported**: the format is Orca's own, it has an authority file and backups
  next to it, and a running Orca keeps its own copy.
- **What lane-control may do**: read `settings` and `sshTargets` read-only from a copy of the database, report
  differences (the machine coordinator's drift check, ORC-1 row 23), and tell the owner which switch to flip.

## 3. Pairing from the CLI: `orca pair`

### 3.1 Does an offer for 127.0.0.1 widen the desktop?

Yes. The path `orca pair` takes (1.4.274, `out/cli/server/pair.js` and the app's main bundle):

1. The CLI sends `pairing.createOffer` with `scope`, `address`, `relay`, `grantDesktopControl`, `rotate` and
   `callerTerminalHandle`. The method's schema has no `reach` field and is `.strict()`, so the CLI cannot ask for any
   other kind of offer.
2. The runtime's handler calls the offer maker without a `reach`. The maker skips widening only when
   `reach === "this-computer"` **and** the address is loopback; otherwise it calls `ensureNetworkExposure()`, which
   rebinds the listener to 0.0.0.0, and records the offer with `pairingReach: "network"`.
3. Once such a client has connected (`lastSeenAt > 0`), every later start binds 0.0.0.0 as well, until it is revoked.

The window's "Share this computer" calls the same maker over IPC (`mobile:getRuntimePairingUrl`) and passes the
window's `reach`; choosing "this computer" is the only way to an offer that keeps the desktop on loopback.

So `orca pair --address 127.0.0.1` gives a link that works through `lane-tunnel` and widens the desktop to every
interface: exactly what direction 2's 127.0.0.1 rule is there to prevent.

### 3.2 How the link is printed, rotated and revoked

- Printed: without `--json` the link goes to stdout with "Paste this link in Orca ..."; with `--json` it is
  `result.pairingUrl` (plus a `webClientUrl` that embeds it). Had setup used it: `--json` to a pipe, parsed in memory,
  never a terminal or a file.
- `--rotate` replaces an *unused* offer; an unused offer is otherwise reused. After a restart a new one is made and the
  earlier link keeps working until `--rotate`.
- Revoking a paired client is the window's (Settings > Servers, the client > revoke: `mobile:revokeDevice` /
  `mobile:revokeRuntimeAccess`, IPC only). The CLI can retire an unused link only by `--rotate`; it cannot revoke one
  that a box has used. So had setup minted a link and the box step then failed, it could rotate the offer if the box
  never used it and would still have to send the owner to the window otherwise, as today.

### 3.3 Direction 1 and SSH targets

- `orca pair` on the box (against the box's own server, the canary's case) prints the box's link for this desktop;
  `environment add` here then pairs, which is what orca-host-install `--pair` does with the code its `serve` prints. It
  does not replace the install, the start or the tunnel. Note it widens the box's server unless that runs with
  `--bind`; orca-host-install does pin `--bind 127.0.0.1`, which is why a box refuses to widen (the canary and TB-1).
- An SSH target would make direction 1 and its tunnel unnecessary (Orca carries the connection), and possibly direction
  2 and `lane-tunnel` (section 1.3); both need the window.

## 4. The trial

Against a scratch Orca only: `orca serve` (orcad, 1.4.274) in its own HOME, XDG folders and `ORCA_USER_DATA_PATH`
under this lane's scratch folder, started with `env -i` (no `ORCA_*` from this terminal), on a free port. The owner's
desktop and profile were not touched. No link was printed or kept: `--json` went to a parser that printed only
`available`, `reason` and whether a link was there.

| Run | Listener before | `orca pair --address 127.0.0.1 --json` | Listener after |
|---|---|---|---|
| `serve --port <p>` | 0.0.0.0:<p> (serve's default: "orcad is bound to 0.0.0.0") | not run: nothing to widen | |
| `serve --port <p> --no-pairing` | 0.0.0.0:<p>, the same | not run | |
| `serve --port <p> --bind 127.0.0.1 --no-pairing` | 127.0.0.1:<p> | `available: false`, `network_exposure_failed`; the server's log: "Runtime bind address is pinned to 127.0.0.1; refusing to widen to all interfaces" | 127.0.0.1:<p> |

The third run is the one that answers the question: an offer for 127.0.0.1 asked the runtime to widen to all
interfaces, and only the pin stopped it. A desktop app is not pinned (no `--bind`), so there it would widen. A
loopback, unpinned `serve` cannot be made (without `--bind` it binds 0.0.0.0), so the desktop's own case was read from
the source (3.1), not run. The desktop this was written on already listened on 0.0.0.0:6768 (read with `ss`, nothing
changed), so some network pairing had already widened it: `--check` now says so.

## 5. What Orca installs on a box, and airgaps

What needs the network, per stage:

| Stage | Network access | On an airgapped site |
|---|---|---|
| Orca's server on the box (Orca's install or orca-host-install) | The box: none. The desktop: Node from nodejs.org only for a box platform other than its own (or the glibc 2.17 build), sha256-checked | Set Settings > `nodeDistMirrorUrl` (or `NODEJS_ORG_MIRROR` in Orca's environment) to an internal mirror of `nodejs.org/dist` holding `v24.22.1`; or use orca-host-install, which takes the server and Node from the desktop's own `orcad-artifacts` (`--from`, `--bundle`) |
| Orca updates on the desktop | `github.com/stablyai/orca` releases (`latest-linux.yml`, `releases.atom`) | Install new builds by hand (the desktop is the source for every box) |
| The relay (mobile pairing through Orca's cloud) | Orca's relay, only when signed in and asked for | Not needed; leave it off |
| lane-control on the box | none: copied over SSH by `project-setup <repo> <host>` | as is |
| The design skills on the box | `github.com` (pinned commits, design-kit spec section 9) | `design-skills missing`; mirror the repos and point `core/design-skills.tsv` at the mirror (not built) |
| Agents on the box (Claude Code, OpenCode) | their own installers, and their providers or the owner's gateway at run time | install from an internal package mirror; the gateway's URL in OpenCode's config |
| The project and its beads | the git remote (GitHub, or an internal one) | an internal remote |

So the server needs nothing new. What an airgapped box lacks is everything around it: agent CLIs, a model endpoint, a
git remote and the design skills. `--check` now reports the agent CLI and the gateway key on the box. Pre-staging the
rest from the desktop is filed as its own issue, with the plan (lc-5n5; it builds on lc-7og, lc-eui
and lc-dqf).

## 6. Recommendation

1. **Keep the hand step for the direction-2 link, as a prerequisite** (done in this lane):
   - `project-setup <repo> <host>` prints, before any box work, what pairing each direction has
     (`pairing: direction 1 saved|to do`, `pairing: direction 2 recorded|skipped`) and, when it will ask for the link,
     the steps to make it now (Settings > Servers > "Share this computer", 127.0.0.1) and what it does with it. The
     prompt, the 127.0.0.1 rule and stdin-only delivery are unchanged.
   - `project-setup --check <repo> <host>` reads everything and changes nothing: Orca here, whether the desktop
     already listens beyond loopback, ssh without a prompt, direction 1 (saved, answers), the versions on both sides,
     direction 2 (`--box-pairing status`, only when lane-control is on the box already), systemd here, the tunnel, the
     coordinator's agent CLI and the gateway key's variable on the box (its name, never its value). Exit 1 when
     something is missing. It is the step to run first (getting-started, remote.md).
   - Do not use `orca pair` for the box link. Ask Orca upstream for a CLI offer that keeps a loopback desktop on
     loopback (`orca pair --this-computer`, the window's `reach`; lc-1i9); then setup can mint the link from a pipe with
     `--json`.
2. **Try the owner's route next, in a window**: connect one box as an SSH target, start a coordinator in its terminal
   and see whether `worker-start` lands on the desktop and its `worker_done` comes back (lc-65b). If it does, direction
   2 becomes "connect the box in Orca once", and `lane-tunnel`, the share link and `--box-pairing` can go.
3. **Direction 1**: keep orca-host-install. "Orca on your SSH hosts" does the same with a click and keeps itself up to
   date, but is not scriptable, has no version pin and no unit on the box. Revisit when Orca has a CLI for it.

Risks of what was built: `--check` runs read-only commands only, but its box probe is a small Python script on the
box's `python3` (setup needs Python there anyway); a box without Python shows as `python on box: missing`. The
desktop-listener line reads port 6768 (`LANE_DESKTOP_ORCA_PORT` for another).
