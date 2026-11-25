# Pairing a box with Orca's own features (PAIR-1)

Date: 2026-11-25. Issue lc-lxd. The owner's words, the same day: "investigate orca settings and see if we can automate
pairing in project-setup"; then "i think best way to pair is using orcas function to install a client/server on box via
ssh"; then "if we cannot pair automatically, we may want to make it an instruction in project-setup or in a prereq
stage"; then "we should probably look into what orca installs on the box ... perhaps we can automate it, or at least
ease install in airgaps". After the first review, the owner decided: "coordinator lives on box for just that reason.
direction 2 must stay." and "we do it with share link, with --on-client as fallback, but must make sure to make it as
painless as possible for user". Orca 1.4.274 throughout. Related: the ORC-1 audit
(`docs/research/2026-11-25-orca-overlap.md`, rows 1-4, 16 and 23), lc-65b (SSH targets), lc-7og, lc-eui, lc-dqf and
lc-5n5 (airgaps and the server bundle), lc-1i9 (an upstream ask).

Everything below is read from Orca 1.4.274's help and source (`resources/app.asar` and `app.asar.unpacked/out/`,
read-only; minified names quoted as found) unless it says "seen" or "run". Nothing internal was called: no `ssh.*` or
`settings.*` method, no SSH target added or connected, no `orca pair` against the owner's Orca. The one run is the
scratch-serve trial of section 4.

## Short answer

- **Setup cannot mint the direction-2 link itself without widening the desktop.** `orca pair --address 127.0.0.1`
  makes Orca listen on every interface (0.0.0.0) before it makes the offer, whatever the address (read from the
  source; a pinned scratch serve's refusal to widen was seen in a trial, section 4). Only the window's "Share this
  computer" with 127.0.0.1 makes an offer that keeps the desktop on loopback. So the link stays the owner's step, now
  a stated prerequisite: `project-setup <repo> <host>` says what pairing each direction has and gives the steps for the
  link before the box's server and its own run, and `project-setup --check <repo> <host>` lists every prerequisite,
  reads only, and says "could not check" for anything it could not read.
- **Direction 2 stays** (owner's decision): the box's coordinator starts lanes on this desktop, through the share link
  and `lane-tunnel`; `orca orchestration send --on-client` is the fallback (section 6). A follow-up lane (PAIN-1)
  makes the share-link step as painless as the window allows.
- **Orca's own SSH install is direction 1.** Connecting an SSH target to an empty host installs Orca's server there
  (the managed server, Settings > "Orca on your SSH hosts"), forwards to it over the desktop's SSH connection and
  saves it as a paired environment: what orca-host-install `--pair` does by hand. An agent in that box's terminal talks
  to the box's own server, not to the desktop, so it does not give direction 2.
- **No `orca` CLI command adds or connects an SSH target or changes a setting, but the runtime's RPC has methods for
  them** (`ssh.addTarget`, `ssh.connect`, `settings.update`, section 2). Whether lane-control may call Orca's internal
  RPC is the owner's decision (section 6, question 1); nothing calls it now.
- **The box may download Node.** The desktop pushes Orca's server and a pinned Node 24.22.1 over SSH, but when the
  desktop cannot get the Node archive, the box downloads it itself (curl or wget, from the desktop's mirror setting,
  then nodejs.org). orca-host-install never lets the box download.
- **Settings**: kept in the profile's `profile-state.db`, readable read-only. Writing the database or any of Orca's
  files ourselves is not supported.

## 1. The owner's route: Orca's own install on a box over SSH

Sources: `orca host --help` and `host list|rm|name --help`, `environment --help` and its managed-server commands,
`project setup-clone|setup-existing-folder --help`, `worktree create --help`, `orca serve service install --help`, the
orchestration guide's `references/placement-and-remote.md`, `skills get orca-cli`, and the code named per point.

### 1.1 SSH targets and the managed server are one route

An SSH target comes from Settings > SSH, from `~/.ssh/config` (Orca imports every `Host` alias passively, so each one
shows in `orca host list` as `ssh target ... not connected`), or from the runtime's `ssh.addTarget` (section 2).
Connecting one is where Orca's server goes on the box. `resolveHostServerOnConnect`
(`chunks/ssh-host-server-on-connect-*.js`):
- a host whose server this desktop already runs: used as it is;
- an empty host (`isEmptyHost`): `deploy` (`route: managed`): the server is installed, started, forwarded to and
  saved as a paired environment (`orcadDeployment: {sshTargetId, localPort, remotePort}`), the same as Settings >
  "Orca on your SSH hosts" ("Orca installs itself on an empty SSH host and keeps itself up to date");
- a host that still has terminals an older Orca started through its relay: converted, or bridged to those old
  terminals only (`if (bridged) ...`); otherwise "This SSH host isn't supported by this version of Orca. To keep using
  it, install an older version of Orca."

So in 1.4.274 the relay is only for an older Orca's terminals; a target this desktop connects is a managed server.

### 1.2 What the desktop installs on the box, and how

From `orcad-managed-serving`, `orcad-remote-node-runtime`, `orcad-artifacts.js` and `shared/node-runtime/
node-runtime-pin.js`:

| What | Where on the box | Size (linux-x64-glibc) |
|---|---|---|
| A server slot: `orcad.js`, `orcad-server.js`, the worker entry files, `node_modules/node-pty` with its native addon, ripgrep, `bin/orca-provider-exec`, `.version`, `.server-target`, `.runtime-node` (the Node sha256) | `~/.orca-remote/` (a slot per version) | 83 MB |
| Node, the pinned build | `~/.orca-remote/runtimes/node-<sha256>/bin/node`, shared by the slots | 122 MB |
| The server's data: its profile, pairing identity, terminal history | orca-host-install: `~/.orca`; Orca's own install: a folder of its own under the home (not traced) | grows with use |
| The machine key and activation record | `~/.orca-remote/orcad-machine.json`, `.orcad-activation-*` | small |

- **Which server**: the desktop's own build (`orcad 0.1.0+<hash>`, from `resources/orcad-template`), built into a slot
  for the box's platform: `linux-x64-glibc`, `linux-arm64-glibc`, `linux-x64-musl`, `linux-arm64-musl`, darwin and
  Windows; `linux-x64-glibc217` is an opt-in build for glibc 2.17. No version pin: a managed server gets the desktop's
  version.
- **Node**: pinned at 24.22.1, each archive and executable with its sha256 (`linux-x64-glibc`:
  `node-v24.22.1-linux-x64.tar.gz`, executable `e32e2d16...cfafb`). The desktop has its own platform's Node in the app
  (`resources/node-runtime`; the scratch serve of section 4 built its slot from that, with no download). For another
  platform the desktop downloads the archive from `https://nodejs.org/dist/v24.22.1/` (linux-arm64-musl and the
  glibc 2.17 build: `https://unofficial-builds.nodejs.org/download/release/`), checks its sha256 and caches it in its
  `orcad-artifacts`. A mirror is honoured: the setting `nodeDistMirrorUrl`, or `NODEJS_ORG_MIRROR` /
  `NVM_NODEJS_ORG_MIRROR` in the desktop's environment.
- **The box downloads Node when the desktop cannot** (`orcad-remote-node-runtime`, a non-Windows box): if the
  desktop's download fails, a script runs on the box that uses `curl` or `wget` against the desktop's mirror setting
  (the mirror's URL is sent to the box) and then nodejs.org or unofficial-builds; the desktop logs "This computer
  couldn't download Node, so the host downloaded it". When both fail: "Neither this computer nor the host could
  download Node from <url> ...", and the error names an offline route: a path on the desktop to copy the archive to,
  and connect again. A Node already on the box is not used for the server (the slot names the sha256 it runs).
- **How it is copied**: uploaded over the desktop's SSH connection (SFTP), the Node archive unpacked there with
  `tar`, the executable bits set again, then a completion marker. The box needs `tar` and a home that allows running
  programs (a `noexec` home is refused with its own message).
- **How it runs**: started by the desktop over SSH with `setsid` and `nohup`, bound to 127.0.0.1 (`orcad-bind-address.js`:
  "SSH forwarding needs only loopback; network exposure must be an explicit operator choice"). No systemd unit: after a
  box reboot it is down until the desktop connects again (the target's "Reconnect on launch", on by default in the
  window's form). orca-host-install writes a systemd user unit on the box when there is a user manager.
  `orca serve service install` is Orca's own systemd user unit, but it needs the full Orca app on that machine ("The
  `orca` command in an Orca terminal on an SSH host belongs to that connection's server and cannot install a
  service"), so it is not for a box that has only the server.
- **Updates**: from the desktop. `orca environment update` (or the window) moves the server to the desktop's version,
  and a connect to an existing managed server runs an update check of its own (`managed-server-update-check`), so a
  box can move when the desktop reconnects. The box checks nothing on the network itself.

### 1.3 With the box as an SSH target, where does an agent's `orca` go?

To the box's own server. A target this desktop connects is a managed server (1.1), and a terminal there runs in that
server: "The `orca` command in an Orca terminal on an SSH host belongs to that connection's server" (`orca serve service
install --help`). So a coordinator on the box that runs `worker-start --environment local` starts its lane on the box;
reaching the desktop is still `--on <desktop>`, which needs the desktop's link (direction 2).

The two texts that suggested otherwise are about old terminals: the target setting "Allow this host to control Orca
browser tabs in its workspaces" (`allowRemoteCliControl`) says "Terminals started on this host by an older Orca also
need this on to run orca orchestration commands", and the orca-cli guide speaks of "Orca's older SSH relay". The
setting is on by default in the window's form; a target added through `ssh.addTarget` gets it only when the call sets
it.

### 1.4 Can the desktop start workers on the box?

On the managed server, yes, as on any paired server: `worker-start --on <box environment>`, the path lane-control's
direction 1 already uses. On a project registered only on the SSH target, no: the placement guide says "a start cannot
create a worktree there", and `project setup-clone --host ssh:<id>` is window-only ("SSH targets are cloned through
the desktop UI because the desktop client owns SSH connections").

### 1.5 What it replaces, and what it cannot do

| Ours | Orca's | Verdict |
|---|---|---|
| orca-host-install `--pair` + `orca-tunnel-<host>` (`-L` unit) + `environment add` (direction 1) | Connecting the box as an SSH target (the managed server): install, start, forward and pair; updates from the desktop | The same in function. Keep ours for now: it runs from setup without the window (or an internal RPC, section 6), pins a version (`--orca-version`), never lets the box download, and survives a box reboot (a systemd user unit on the box). |
| The share link, `lane-tunnel` and `--box-pairing` (direction 2) | Nothing: the managed server's pairing makes the desktop a client of the box, not the other way | Keep (owner: direction 2 must stay). |
| `project-setup <repo> <host>`'s box code (copy, the box's own run) | `project setup-clone --host ssh:<id>`: window only | Keep. |

What Orca's route cannot do:
- **No desktop window**: headless `orca serve` refuses the managed-server commands ("A runtime without the SSH registry
  (headless `orca serve`) refuses it") and answers SSH changes with "can't manage SSH hosts yet".
- **The desktop offline or closed**: the SSH connection and the forward are the desktop's. A box's own coordinator keeps
  running on its server, but loses the desktop.
- **Jump hosts and smartcards**: a target takes a jump host, a `ProxyCommand` and the system ssh's connection reuse. A
  passphrase or PIN is asked for in the window (`ssh:needsPassphrasePrompt`); whether a connect through the RPC can
  get one was not traced. What it cannot do is what `lane-tunnel --no-enable` does: come up from an estate's own
  "unlock the card, then start" command.

## 2. The settings, the pairing state and the runtime's RPC

The layout is from a scratch profile (section 4); keys and methods from the app's code. The owner's profile was not
opened.

| What | Where | Readable | Changeable other than the window |
|---|---|---|---|
| App settings (264 keys in a fresh profile), among them `workspaceCleanupPolicy`, `orchestrationWorkerAutoReleaseEnabled`, `nestedWorkerMaxDepth`, `nodeDistMirrorUrl`, `mobilePairingCustomAddress(es)`, `activeRuntimeEnvironmentId` | `profiles/<id>/profile-state.db`, table `profile_state_documents`, domain `settings` (JSON) | yes: SQLite, opened read-only (`?mode=ro`), as the machine coordinator does | the runtime's `settings.update`, for an allowlist only (below); no CLI command |
| SSH targets, with `allowRemoteCliControl`, `reconnectOnLaunch`, `jumpHost`, `proxyCommand` | the same database, domain `sshTargets` | yes, read-only | `orca host rm`; the runtime's `ssh.addTarget|updateTarget|removeTarget` |
| Paired clients and unused offers (`deviceId`, `token`, `scope`, `lastSeenAt`, `pairingReach`) | `orca-devices.json` in the profile root | holds live tokens: do not read it | no CLI revokes one (the window's `mobile:revokeDevice`) |
| Saved environments (servers this Orca is a client of), with their pairing codes | `orca-environments.json` | holds secrets: do not read it; `orca environment list|show` answers | `environment add|rm` |
| The running runtime: endpoints, its auth token | `orca-runtime.json` | holds a token; `orca status --json` answers | no |
| What the listener binds | not a setting: 127.0.0.1 until a client paired with a network reach has connected, then 0.0.0.0, also after restarts (`resolveInitialWebSocketBindHost`); `orca serve` defaults to 0.0.0.0, `--bind` pins it | `ss -ltn`, or `/proc/net/tcp` | no |

The runtime's RPC (the methods its CLI and paired clients call, registered in `chunks/orcad-retained-source-*.js`;
params in `shared/rpc-contract/ssh/ssh-params.js`):

| Method | Permission | Takes |
|---|---|---|
| `ssh.addTarget` | `workspace` | `{target: {label, host, port, username, configHost?, identityFile?, identityAgent?, identitiesOnly?, gssapiAuthentication?, proxyCommand?, jumpHost?, relayGracePeriodSeconds?, systemSshConnectionReuse?, systemSshProxyConnectionReuse?, remoteRuntime?, allowRemoteCliControl?}}`, strict: no credential is accepted ("Ask for credentials when connecting") |
| `ssh.updateTarget`, `ssh.removeTarget` | `workspace` | `{targetId, target}` / `{targetId}` |
| `ssh.connect` | `host-admin` | `{targetId, automatic?}`; on an empty host this deploys the managed server (1.1) |
| `settings.get`, `settings.update` | `workspace`, `settings-write` | `settings.update` takes a strict allowlist of the client settings: `workspaceCleanupPolicy` is in it; `orchestrationWorkerAutoReleaseEnabled`, `nestedWorkerMaxDepth` and `nodeDistMirrorUrl` are not |

- **Who can call them**: the local owner caller holds every permission (`{kind: "owner"}` with `workspace`,
  `settings-write`, `host-admin`, ...). The `orca` CLI is such a caller: it connects to the runtime with the token in
  `orca-runtime.json` and sends JSON RPC calls. But **no `orca` command sends any of these methods**, and the CLI has
  no generic "call a method" command. So reaching them means writing our own client of Orca's private socket protocol
  (the token, the framing, the method names), which can change in any release. Not tried here.
- **Changing a setting otherwise**: the window writes them (`settings:set`, Electron IPC). There is no `orca settings`
  ("Unknown command") and no documented settings file. **Writing the database or any of Orca's files ourselves is not
  supported**: the format is Orca's own, it has an authority file and backups next to it, and a running Orca keeps its
  own copy.
- **What lane-control does**: read `settings` and `sshTargets` read-only from a copy of the database, report
  differences (the machine coordinator's drift check, ORC-1 row 23), and tell the owner which switch to flip. Calling
  `settings.update` or `ssh.addTarget|connect` is question 1 of section 6.

## 3. Pairing from the CLI: `orca pair`

### 3.1 Does an offer for 127.0.0.1 widen the desktop?

Yes. The path `orca pair` takes (`out/cli/server/pair.js` and the app's main bundle):

1. The CLI sends `pairing.createOffer` with `scope`, `address`, `relay`, `grantDesktopControl`, `rotate` and
   `callerTerminalHandle`. The method's schema has no `reach` field and is `.strict()`, so the CLI cannot ask for any
   other kind of offer.
2. The runtime's handler calls the offer maker without a `reach`. The maker skips widening only when
   `reach === "this-computer"` **and** the address is loopback; otherwise it calls `ensureNetworkExposure()`, which
   rebinds the listener to 0.0.0.0, and records the offer with `pairingReach: "network"`. `--mobile` always widens.
3. Once such a client has connected (`lastSeenAt > 0`), every later start binds 0.0.0.0 as well, until it is revoked.

The window's "Share this computer" calls the same maker over IPC (`mobile:getRuntimePairingUrl`) with the window's
`reach`; choosing "this computer" is the only way to an offer that keeps the desktop on loopback.

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

### 3.3 Direction 1

`orca pair` on the box (against the box's own server) prints the box's link for this desktop, and `environment add`
here then pairs: what orca-host-install `--pair` does with the code its `serve` prints. It does not replace the
install, the start or the tunnel. It widens the box's server unless that runs with `--bind`; orca-host-install pins
`--bind 127.0.0.1`, which is why such a box refuses to widen (the canary and TB-1). The SSH-target route of section 1
does all of direction 1 at once.

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

The third run shows the request: an offer for 127.0.0.1 asked the runtime to widen to all interfaces, and only the pin
stopped it. No widening was seen: a loopback, unpinned `serve` cannot be made (without `--bind` it binds 0.0.0.0), so
the desktop's own case is read from the source (3.1), not run.

## 5. What Orca installs on a box, and airgaps

What needs the network, per stage:

| Stage | Network access | On an airgapped site |
|---|---|---|
| Orca's server on the box, Orca's own install | The desktop: Node from nodejs.org (or unofficial-builds) for a box platform other than its own, sha256-checked. The box: the same download when the desktop's fails (curl or wget, the desktop's mirror first) | Set `nodeDistMirrorUrl` (or `NODEJS_ORG_MIRROR` in Orca's environment) to an internal mirror of `nodejs.org/dist` holding `v24.22.1`, or copy the archive to the path the error names on the desktop; then the box downloads nothing |
| Orca's server on the box, orca-host-install | The box: none. The server and Node come from the desktop's own `orcad-artifacts` (`--from`, `--bundle`), the Node binary checked against the slot's sha256 | as is |
| Orca updates on the desktop | `github.com/stablyai/orca` releases (`latest-linux.yml`, `releases.atom`) | install new builds by hand; the desktop is the source for every box |
| The relay (mobile pairing through Orca's cloud) | Orca's relay, only when signed in and asked for | not needed; leave it off |
| lane-control on the box | none: copied over SSH by `project-setup <repo> <host>` | as is |
| The design skills on the box | `github.com` (pinned commits, design-kit spec section 9) | `design-skills missing`; mirror them (lc-5n5) |
| Agents on the box (Claude Code, OpenCode) | their own installers, and their providers or the owner's gateway at run time | install from an internal package mirror; the gateway's URL in OpenCode's config |
| The project and its beads | the git remote (GitHub, or an internal one) | an internal remote |

So the server needs nothing on the box once the desktop has Node for it. What an airgapped box lacks is everything
around it: agent CLIs, a model endpoint, a git remote and the design skills. `--check` reports the agent CLI and the
gateway key on the box. Pre-staging the rest from the desktop is lc-5n5, with the plan (it builds on lc-7og, lc-eui
and lc-dqf).

## 6. Recommendation

1. **Direction 2 stays, with the share link** (owner's decision). The hand step is a stated prerequisite (done in
   this lane):
   - `project-setup <repo> <host>` prints, for a native-Orca box project, before the box's server and its own run,
     what pairing each direction has (`pairing: direction 1 saved|to do|unknown`, `pairing: direction 2
     recorded|skipped`) and, when it will ask for the link, the steps to make it now (Settings > Servers > "Share this
     computer", 127.0.0.1) and what it does with it. The prompt, the 127.0.0.1 rule and stdin-only delivery are
     unchanged.
   - `project-setup --check <repo> <host>` reads everything and changes nothing; it refuses a flag that acts
     (`--remove-host`, `--refresh`, ...). Rows: Orca here, whether the desktop already listens beyond loopback, ssh
     without a prompt, direction 1 (saved, answers) and the versions on both sides, the server source given, systemd
     here, direction 2 (read from the box's Orca CLI), the tunnel, the coordinator's agent CLI and the gateway key's
     variable on the box (its name, never its value), lane-control on the box. `to do` is a step setup does, or the
     link it asks for; `could not check: <why>` is a row it could not read. Exit 1 when one is missing or could not
     be checked. It is the step to run first (getting-started, remote.md).
   - PAIN-1, next: make the share-link step as painless as the window allows.
   - Do not use `orca pair` for the box link. Ask Orca upstream for a CLI offer that keeps a loopback desktop on
     loopback (`orca pair --this-computer`, the window's `reach`; lc-1i9); then setup can mint the link from a pipe
     with `--json`.
2. **The fallback is `--on-client`** (owner's decision): with direction 1 in place, the desktop is a client of the
   box's server, so the box's coordinator can mail an agent on the desktop with `orca orchestration send --on-client
   <desktop> --to bot:<id>`. A person on the desktop approves it once, and the reply comes back to the coordinator's
   `check`; it fails at once when the desktop is not connected (`orca orchestration send --help`). It reaches a bot,
   not a lane, so it covers asking the desktop for work when no link exists, not starting supervised lanes there.
   Not run here.
3. **Direction 1**: keep orca-host-install. Connecting the box as an SSH target does the same and keeps itself up to
   date, but needs the window (or the internal RPC, question 1), has no version pin and no unit on the box, and may
   let the box download Node.

Questions for the owner:
1. May lane-control call Orca's internal RPC (`ssh.addTarget`, `ssh.connect`, `settings.update`) through the runtime's
   private socket protocol with the token in `orca-runtime.json`? It would let setup add and connect a box as an SSH
   target (direction 1 without orca-host-install) and set `workspaceCleanupPolicy`. Against: it is not a supported
   interface, it can change in any release, it writes Orca's own state, and adding a target that way leaves
   `allowRemoteCliControl` unset unless set. Not tried; nothing does it now.

Risks of what was built: `--check` runs read-only commands only, but its box probe is a small Python script on the
box's `python3` (setup needs Python there anyway), which runs the box's Orca CLI for `environment list` and `status`.
The desktop-listener line reads port 6768 (`LANE_DESKTOP_ORCA_PORT` for another).
