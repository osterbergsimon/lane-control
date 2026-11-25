# The testbed

Three throwaway containers on a private network, to test lane-control end to end on one machine: onboarding a project,
`--refresh`, both SSH directions (direct and through a jump host), with a fake model gateway and a fake GitHub so
nothing costs money or touches a real account. It is opt-in: it never runs in `python3 -m unittest discover -s tests`
or in CI. It is not `tests/test-orca-host-install.sh` (never run that one: it wipes a real host user's Orca).

## What it is

| Host | Runs |
|---|---|
| `desktop` | Orca's server (orcad, headless, on 127.0.0.1:6768 as the desktop app listens), lane-control in `~/lane-control`, the fake gateway on 127.0.0.1:4000, OpenCode |
| `jump` | sshd only |
| `box` | sshd, the fake gateway, OpenCode; Orca's server and lane-control arrive the way they do on a real box (`orca-host-install`, the copy in `~/.local/share/lane-control`) |

- Each container runs as a normal user (`u`) with every capability dropped (`--cap-drop ALL`), `no-new-privileges`,
  on its own `--internal` docker network (no way out, no published ports). Never privileged, never the host network,
  and nothing of yours is mounted: not your HOME, `~/.config`, `~/.ssh`, `~/.orca` or the docker socket.
- sshd runs as that user on port 2222. Every key is made inside its container when it starts (a host key and a user
  key each); `up` lets the desktop's key into `jump` and `box` and writes their host keys into the desktop's
  `known_hosts`. Your own keys are never used, and the generated ones go with the containers. The desktop's
  `~/.ssh/config` has `box` (direct), `jump` and `box-via-jump` (`ProxyJump jump`).
- **Orca** is pinned in `orca-pin.json`: **1.4.268** (the default, what the owner's desktop runs) and **1.4.274** (the
  newest release when this was built), each with its download URL and sha256; `TESTBED_ORCA_VERSION=1.4.274` picks the
  other. By default `up` downloads the chosen official server release (`orca-server-linux-x64.tar.gz` from Orca's GitHub releases) once into `TESTBED_WORK`,
  checks its sha256 against the pin before it unpacks or uses anything (a mismatch is deleted and refused), and copies
  its `orcad/` slot and Node into the image. `up --orca-from DIR` takes an Orca you have instead: an Orca app's
  `resources` folder, its `orcad-template`, or an unpacked server release; `--orca-from local` is the app `orca-ide` on
  your PATH resolves to (on NixOS a store path, read-only). From an app the slot is assembled from `orcad-template`
  (the common files, the `linux-x64-glibc` target, the packaged ripgrep) with the app's own Node. Either way the slot
  is asked for its content digest (its profile preflight) and named by it, since orcad takes no other name, and
  nothing is mounted. `up` refuses an Orca whose version is not the pin unless `TESTBED_ORCA_VERSION` names it.
  Tested on 1.4.268: the pinned download and `--orca-from local` (the nix-packaged app) both pass `scenario all` (and
  give the same slot digest). For 1.4.274 see Coverage. The
  desktop runs that slot as its server; the same slot is in `ORCA_ARTIFACTS` there, so `orca-host-install` puts it on
  the box exactly as it does from a real desktop.
- **The base image** is pinned by digest (`base_image` in `orca-pin.json`, Python 3.12 on Debian bookworm); its
  packages (sshd, git, jq, tini) come from Debian at build time.
- **No systemd** in a container, so a small `systemctl --user` stand-in (`image/systemctl`) runs the user units
  lane-control writes (`orca-tunnel-<host>`, `orcad`, `lane-tunnel@<host>`), with `EnvironmentFile`, `%i` and
  restarts. It exists only in the image.
- **The fake gateway** (`fakes/gateway.py`, built on `tests/stubs/fake_llm.py`) is OpenAI- and LiteLLM-compatible:
  `/v1/models` lists the aliases `chat`, `code` and `embed`, `/model/info` names made-up models behind them
  (`testbed/fake-chat-small`, `testbed/fake-code-large`, and an embedding model), and chat completions answer `ok`
  (streamed when asked). When the request offers tools, the last user message's steps come back as tool calls of
  OpenCode's shell tool (`shell` in v2, `bash` in v1), one per answer: each `RUN-TOOL: <command>` in order; for an Orca
  message prompt ("Run `orca orchestration check ...`") that check, then each `RUN-TOOL:` the messages carry; and the
  worker brief's own `worker_done` command filled in, after the brief's steps or, when the brief says
  `WAIT-FOR-MESSAGE`, after a message was read. So an OpenCode worker on it does a whole lane, takes a message and
  reports, at no cost. Its OpenCode config allows the shell tool without asking (the lanes' hosts only). It wants the key
  `testbed-not-a-secret` (made up), which each container sets as `LITELLM_API_KEY` where agents start: before its
  Orca server starts, and in sshd's environment for the box's server; OpenCode's config in a container points at it
  as provider `litellm`, as `orchestrators/native-orca/setup.md` describes. OpenCode lanes run against it at no cost.
- **The fake GitHub** (`fakes/gh`) is a stub `gh` over a folder shared by desktop and box (the docker volume mounted
  at `/forge`): bare repos at `/forge/repos/<owner>/<name>.git` are each clone's `origin` (`file://`), and pull
  requests, repo settings and check results are in `/forge/state.json`. It covers what lane-control calls: `auth
  status`, `repo view|edit --delete-branch-on-merge|create|clone`, `pr create|view|list|ready|edit|diff|checks|merge|
  close|comment` (a merge really merges in the bare repo, `--match-head-commit` and drafts are checked, a failing
  check blocks it), `api user`, `api repos/<o>/<n>` (`delete_branch_on_merge`, GET and PATCH) and the `graphql` call
  away-merge makes. `gh testbed checks <N|branch> success|failure|pending` sets what a PR's checks report;
  `TESTBED_CHECKS` is what a new PR starts with.
- A real throwaway GitHub repo is a later option, not built: the hook is the forge's place in the scenarios (every
  scenario makes its repos with `gh repo create` and clones `origin`), so a run against GitHub would swap `fakes/gh`
  for the real one and the forge URL for a repo you made for it.
- A real cheap model (Claude haiku) is opt-in and off by default: `TESTBED_REAL_MODEL=1` is reserved for it, no
  scenario uses it yet, and it would need your own sign-in inside a container. CI never sets it.

## Requirements

`tests/testbed/run doctor` checks each of these and says what is missing:

- Linux on x86-64 (the image and Orca's slot are linux-x64 glibc); no NixOS assumption.
- A container engine, picked by `TESTBED_ENGINE` (default docker when installed, else podman): docker 20.10 or newer
  that your user can reach, or rootless podman 4.4 or newer (with subuid and subgid for your user). Tested with docker
  29.8; the podman path uses only commands both share, but has not been run yet.
- Python 3.9 or newer, git and tar on the host.
- `bd` (beads 1.0.4 or newer) and OpenCode v2 (`opencode2` or `opencode`) as linux-x64 glibc binaries, on PATH or
  named by `TESTBED_BD` and `TESTBED_OPENCODE`; they are copied into the image.
- Network once: the Orca download (about 100 MB, unless `--orca-from`), the base image and Debian's packages. After
  that the containers have no network beyond each other.
- About 6 GB of disk (the image is about 2 GB, plus the download and the build context) and 4 GB of free memory.
  A cold `up` takes a few minutes (most of it Debian's packages); `scenario all` takes about a minute and a half.
- The image build runs through `~/.agents/bin/lane-heavy` when it is there (lane-control's machine-wide gate), and
  without it otherwise.

## Replicate it elsewhere

From a fresh clone, on a machine with the requirements above:

```sh
git clone https://github.com/osterbergsimon/lane-control && cd lane-control
tests/testbed/run doctor && tests/testbed/run up && tests/testbed/run scenario all
```

That needs no account of yours: the gateway and GitHub are fakes, every ssh key is made inside the containers, and the
pinned Orca is a public download. Reset with `tests/testbed/run down` (then `up` again); `down` also takes the image,
so the next `up` rebuilds it (Docker's build cache makes that quick). The downloads stay in `TESTBED_WORK` until you
delete it. What would need your own accounts, both off by default and not built yet: the real cheap model
(`TESTBED_REAL_MODEL=1`; your Claude sign-in inside the container) and a real throwaway GitHub repo (a repo you make
and a `gh` signed in to an account that may write to it).

## Run

```sh
tests/testbed/run up                # build the image and start desktop, jump and box
tests/testbed/run list              # the scenarios, in the order `all` runs them
tests/testbed/run scenario all      # or one name; prints a pass/fail table, exit 1 when one fails
tests/testbed/run status            # what runs, and each scenario's last result
tests/testbed/run down              # remove everything it made
```

`down` removes every container, network, volume and image tag labelled `lane-control-testbed=1` (all named
`lc-testbed-*`) and nothing else. `TESTBED_WORK` (default `$TMPDIR/lane-control-testbed-<uid>`) holds the build
context while building and the last results. lane-control goes into the desktop as the worktree is (tracked and
untracked files, not ignored ones), so a scenario tests your uncommitted change too; `up` again refreshes it.

## Scenarios

`all` runs them in this order (later ones use what earlier ones left); every agent in them is OpenCode on the fake
gateway or a plain shell command in an Orca terminal, never a real model.

| Name | What runs | Passes when |
|---|---|---|
| `suggest-tiers` | `project-setup <scratch> --suggest-tiers litellm` on the desktop, without a terminal, then on one with yes | without a terminal it prints the aliases with the models behind them and exits 3; on yes `lanes.tsv` in the settings store has OpenCode rows; the embedding alias is left out; the gateway saw the key; the key is not printed |
| `onboard` | `project-setup <scratch> --skip-adapt --coordinator opencode/litellm/chat --reviewer opencode/litellm/code` on a terminal (Enter to every question), then again | it exits 0; `.lane-control/coordinator.md` and `reviewer.md` are rendered; an Orca terminal titled `scratch coordinator` exists; its OpenCode called the gateway with the key (so the key reached the agent through Orca's environment); the re-run says `exists` |
| `d1-direct` | `orca-host-install box --pair --port 6769` from the desktop (the `-L` tunnel unit), then again | it says `installed`, `started`, `paired`; `orca-ide status --environment box` is reachable; a terminal made from the desktop through that environment runs on the box (its `hostname` lands there); the re-run says `exists`, `running`, `already-paired` |
| `d1-jump` | the same after the box is reset, as `box-via-jump` (`ProxyJump jump`) | as `d1-direct`, and nothing of Orca is on the jump host |
| `d2-tunnel` | on the desktop `lane-tunnel start <box> 6768` (`ssh -R`, through the jump after `d1-jump`); on the box `project-setup --box-pairing add desktop` with the desktop server's pairing link on stdin | the port is open on the box's loopback and not its network; pairing exits 0; the link is in no output and no file under the box's `~/.config` or lane-control copy; `status --environment desktop` from the box is reachable; a terminal the box makes through it runs on the desktop |
| `lane-a-direct` | after `d1-direct`: a shell coordinator on the desktop (`run-create`), `worker-start --on box --worktree new-top-level --repo path:<scratch on the box> --agent opencode`, a message by `send --to dispatch:<id>`, then `check`, `worker-release`, `worktree rm --environment box` | the lane runs on the box in its own worktree and branch; the message makes it act (a file it writes there); its `worker_done` reaches the desktop's `check` (polled, as remote.md says for reports across hosts); after release its terminal is gone from `terminal list --environment box`; the worktree is gone from the box |
| `lane-a-jump` | the same after `d1-jump`, `--on box-via-jump` | as `lane-a-direct` |
| `lane-b-jump` | after `d2-tunnel` (through the jump): the same with the coordinator on the box and `--on desktop` | as `lane-a-direct`, from the box's side, the lane on the desktop |
| `d2-ports` | `d2-tunnel` direct to `box` with other ports (TUN-1): `lane-tunnel start --remote-port 6870 box 6768`, the link pointed at 6870 by project-setup's own `link_for_port`, after a restart of the desktop's server (a link pairs once) | as `d2-tunnel`, on port 6870 on the box |
| `lane-b-direct` | `lane-b-jump` again over that tunnel | as `lane-b-jump` |
| `d2-no-tunnel` | after `lane-b-direct`: on the desktop `orca pair --address <its network address>`, the link to the box on stdin (the same preload project-setup uses), then the lane checks from the box over it; project-setup is not used (it refuses a link that is not 127.0.0.1); the desktop's listeners are recorded before and after, and its server is restarted afterwards | skips plainly today: headless orcad answers `network_exposure_failed` ("Runtime bind address is pinned to 127.0.0.1; refusing to widen") even started without `--bind`, and keeps listening on 127.0.0.1 only |
| `refresh` | on the box: a second scratch project set up by lane-control `48a38ab` (main before LIT-1, whose lane-tier refuses OpenCode tiers), then `project-setup --refresh` from this checkout | the old setup left an older `~/.agents/bin/lane-tier`; after `--refresh` it and the `native-orca-lanes` skill are this checkout's |

Lanes start the way the lane skill says for OpenCode, as far as Orca 1.4.268 lets them: `--agent opencode` without
`--model` (the model is OpenCode's own default, `litellm/chat`): `--agent opencode2 --model` and `--model` with
`new-child` or `new-top-level` are refused (lc-69b). A scripted stand-in agent under another agent id (Aider's) was
tried and dropped: Orca's launch readiness did not accept it.

Not built yet (the next slices): a lane through a PR, a stub review, the merge on the fake GitHub and the cleanup of its
branch; away-improve (`lane-improve on`, the cap, the summary PR, drained); and the items marked not verified below.

## Coverage

What remote.md and the owner's questions claim, and what the testbed checks. Verified means the scenario passed in a
`scenario all` from a fresh `up` on that Orca version (2026-11-25, docker 29.8, the pinned official server releases).

| Claim | 1.4.268 | 1.4.274 | Scenario |
|---|---|---|---|
| Direction 1: `orca-host-install --pair` installs and pairs a box over `-L`, direct and via ProxyJump; a re-run is idempotent | verified | verified | `d1-direct`, `d1-jump` |
| Direction 2 with lane-tunnel: `-R` opens the port on the box's loopback only; `--box-pairing add` with the link on stdin pairs the box; the link is in no output or file | verified | verified | `d2-tunnel`, `d2-ports` |
| TUN-1: different local and remote tunnel ports, the link pointed at the box's port | verified | verified | `d2-ports` |
| A desktop coordinator controls a lane on the box (`--on`, `new-top-level --repo`): worktree and branch there, `send --to dispatch:<id>` reaches it and it acts, its `worker_done` reaches the desktop's `check`, release closes its terminal, cleanup | verified | verified | `lane-a-direct`, `lane-a-jump` |
| A box coordinator runs a lane on the desktop through `-R`, the same checks | verified | verified | `lane-b-jump`, `lane-b-direct` |
| Reports across hosts arrive in the coordinator's `check` (polled) | verified | verified | the lane scenarios |
| The first OpenCode lane started on the desktop from the box gets its brief | not always: OpenCode came up with an empty composer, the start was retried (lc-7dd) | the same | `lane-b-jump` |
| Is lane-tunnel still needed? Direction 2 without it, the box pairing with the desktop's network address | not verified: headless orcad will not widen to the network (`network_exposure_failed`, "pinned to 127.0.0.1") even without `--bind`; the desktop app widens itself on a network pairing (`orca pair --help`: "makes a loopback-only Orca listen on every interface"), so without the tunnel the desktop's Orca listens on every interface: that is the exposure lane-tunnel avoids. Needs the desktop app, or orcad with `--bind 0.0.0.0` (next slice) | the same | `d2-no-tunnel` (skips) |
| A cross-host `worker_done` wakes an idle agent coordinator | not verified: needs a real agent coordinator (the opt-in `TESTBED_REAL_MODEL`, not built); the testbed's coordinators are shells | | none |
| A newer client against an older server (and back) | not verified: the pin file holds both versions; a mixed-pair scenario is not built | | none |
| A runtime restart: workers keep or lose their dispatch (`worker_identity_changed`), `worker-start --retry-of` | not verified (`d2-ports` restarts the desktop's server with no lane running and checks only that it comes back) | | none |
| 1.4.272+: `send --on-client <installation> --to bot:<name>`, `recipient_delivery_held` receipts, resume work on restart, a paired server's follow-up report no longer refused | | not verified (not built) | none |
| Onboarding with no Claude (`--skip-adapt`), `--suggest-tiers` against a LiteLLM-like gateway, `--refresh` from an older checkout | verified | verified | `suggest-tiers`, `onboard`, `refresh` |

## Not covered

The Orca desktop GUI (the testbed runs headless orcad, so no window, no approval prompt and no Settings > Servers share
link: the desktop's pairing link comes from its server's readiness line instead), phone pairing, real GitHub (its CI,
branch protection, permissions, rate limits), real models, `machine-setup` (its automations need a signed-in agent),
the full `project-setup <repo> <host>` run in one go (its parts run: `orca-host-install --pair`, `--box-pairing add`,
`lane-tunnel`, the native setup on a host), and the onboarding agents (`--adapter` and `--adapt-reviewer` must be
Claude, so a Claude-free onboarding runs with `--skip-adapt`).

## Adding a scenario

Add a function to `scenarios.py` with `@scenario("<one line>")`; its name (underscores as dashes) is the scenario's
name and its place in the file is its place in `all`. It gets a `ctx`: `run(ctx, role, script)` runs bash in a
container as `u` (raises `Fail` on a non-zero exit unless `check=False`), `tty(cmd, "REGEX=>reply", ...)` runs a
command on a terminal and answers its questions, `scratch_project(ctx, role, name)` makes a project on the fake GitHub
and its clone, `ctx.copy_lane_control(role, ref=..., dest=...)` puts lane-control (the worktree, or a commit) into a
container. Raise `Fail("what went wrong")` for a failed check, `Skip("why")` when what it needs is not there (say
which scenario makes it), and return one line saying what was checked. Use the fake gateway or a shell command for
anything an agent would do; a scenario that would need a real model checks `TESTBED_REAL_MODEL` and skips without it.
