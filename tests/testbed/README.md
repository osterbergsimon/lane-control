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
- **Orca** is pinned to **1.4.268** in `orca-pin.json` (version, download URL, sha256). By default `up` downloads that
  official server release (`orca-server-linux-x64.tar.gz` from Orca's GitHub releases) once into `TESTBED_WORK`,
  checks its sha256 against the pin before it unpacks or uses anything (a mismatch is deleted and refused), and copies
  its `orcad/` slot and Node into the image. `up --orca-from DIR` takes an Orca you have instead: an Orca app's
  `resources` folder, its `orcad-template`, or an unpacked server release; `--orca-from local` is the app `orca-ide` on
  your PATH resolves to (on NixOS a store path, read-only). From an app the slot is assembled from `orcad-template`
  (the common files, the `linux-x64-glibc` target, the packaged ripgrep) with the app's own Node. Either way the slot
  is asked for its content digest (its profile preflight) and named by it, since orcad takes no other name, and
  nothing is mounted. `up` refuses an Orca whose version is not the pin unless `TESTBED_ORCA_VERSION` names it.
  Tested: both the pinned download and `--orca-from local` (the nix-packaged 1.4.268 app) pass `scenario all`. The
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
  (streamed when asked). When the request offers tools, the last user message's steps come back as bash tool calls,
  one per answer: each `RUN-TOOL: <command>` in order, then, for an Orca worker brief, the brief's own `worker_done`
  command filled in, so an OpenCode worker on it can do a whole lane and report. It wants the key
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
| `refresh` | on the box: a second scratch project set up by lane-control `48a38ab` (main before LIT-1, whose lane-tier refuses OpenCode tiers), then `project-setup --refresh` from this checkout | the old setup left an older `~/.agents/bin/lane-tier`; after `--refresh` it and the `native-orca-lanes` skill are this checkout's |

Not built yet (the next slices, in this order): a `worker-start` lane on one host through its report, a stub review,
the merge on the fake GitHub, release and cleanup; a lane with `--on` across hosts; away-improve (`lane-improve on`,
the cap, the summary PR, drained). For the first, the gateway's worker mode is in (a worker brief's steps, then its own
`worker_done` command, as tool calls) and a probe got this far: on Orca 1.4.268 an OpenCode worker starts only as
`worker-start --agent opencode --model litellm/chat --worktree path:<a worktree made first>` (`--agent opencode2 --model`
and `new-child` with `--model` are refused, lc-69b); it started and called the gateway with its key, but did not run
its tool steps yet, which is where that slice starts.

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
