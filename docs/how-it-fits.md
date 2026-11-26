# How the parts fit

Every moving part of lane-control on native Orca (the orchestrator in use), in plain words, for an owner who has not
read the code. "Desktop" is your workstation with the Orca app. A "box" is a machine you reach over SSH. Every script
has one line in [the Scripts table](../core/README.md#scripts). **Pending** means an open pull request or an open
issue: not on main yet.

## Who does what

```
YOU      talk to a coordinator in its Orca terminal; say "merge"; sign agents in; say yes to a box's link (box only)

AGENTS   machine coordinator   one per machine, optional   splits the budget, watches memory and load
         project coordinator   one per project             queues, briefs, opens lanes, reviews, merges on your word
         lane                  one per issue               own worktree and branch, ends in a pull request

SETUP    machine-setup   once per machine   machine coordinator, 3 automations, CI limits timer
         project-setup   once per project   lane tools, skills, personas, tier table, coordinator (--refresh updates)

LANE     lane-room   may another lane open?        lane-tier     which model does this tier use?
TOOLS    lane-heavy  one gate for heavy builds     design-shots  screenshots of a UI
         lane-limits.sh   the limits behind lane-room and lane-heavy (a file the tools read, not a command)

BOX      optional. A coordinator that lives on the box can keep working while the desktop is off.
         BOTH TUNNELS START ON THE DESKTOP (it only needs to ssh out to the box).
  direction 1: the desktop controls the box
      tunnel   orca-tunnel-<host> (ssh -L), made by orca-host-install --pair
      you      no click in Orca, no pairing code
  direction 2: the box's coordinator starts lanes on the desktop
      tunnel   lane-tunnel@<host> (ssh -R), made by project-setup before it sends the box its link
      you      say yes once per box: setup makes the link with orca pair (the desktop then listens on every
               interface); or --pair-by-hand: make the share link in Orca and paste it (keeps it on loopback)

RUNNING  night manager       Orca automation    checks in on idle coordinators when you are away or it is night
         lane sweep          Orca automation    reports merged, parked, stuck and broken lanes
         machine check       Orca automation    tells the machine coordinator when budget, memory or runners change
         ci-presence         systemd timer      sets CI's CPU and memory limits by whether you are there
         orca-tunnel-<host>  systemd (desktop)  carries direction 1
         lane-tunnel@<host>  systemd (desktop)  carries direction 2
         orcad               on the box         Orca's server there (unit orcad.service when systemd allows)
         "Delete merged workspaces"             Orca's own rule that removes a merged lane's worktree; you turn it on
```

More: the sections below; [Getting started](getting-started.md).

## The three layers

- **Machine coordinator** (optional, one per machine). Judgment only: splits the budget across projects, decides which
  lane yields when memory is short, passes your orders on. It runs no project work. It is an agent in its own private
  folder, in an Orca terminal titled `machine coordinator`.
- **Project coordinator** (one per project). You talk to it in the terminal titled `<project> coordinator`. It turns
  your asks into issues, picks a tier, opens lanes, checks what comes back and merges only on your word. The one
  exception is away-improve mode, which you switch on yourself.
- **Lane**: one worker for one issue, with its own worktree and branch. It opens a pull request and reports once.
  Reviewers (code, and design for anything a user sees) are workers too. A lane for a failing test or a bug gets the
  debugger persona.

A **tier** (swift, standard, deep, top) is a role. Each project's tier table says which engine and model fill it; a
tier set to `none` is skipped.

What each agent reads: a **persona** (its standing instructions, in `orchestrators/native-orca/personas/`, copied into
the project by setup) and **skills** (how-to pages it loads when needed). The lane skills are `native-orca-lanes`
(the coordinator's gate, lane, report, review and close), `away-improve` (the away-improve cycle) and
`component-gallery` (a UI lane's gallery and screenshots). `orca-cotal-lanes` belongs to the parked Orca + Cotal setup.

More: [roles](../core/roles.md), [model routing](../core/model-routing.md), [native Orca setup](../orchestrators/native-orca/setup.md).

## Where lane-control lives on a machine

| What | Where | Who puts it there |
|---|---|---|
| The checkout: scripts, templates, docs | `~/code/lane-control` (any folder works) | you, `git clone`; update with `git pull` |
| The installed tree, the one everything uses: the repo at one commit | `~/.local/share/lane-control/<sha>/` and the `current` link to it (old trees kept for running lanes) | `lane-control install`, and `project-setup` or `machine-setup` run from the main checkout on its default branch at a newer commit (anywhere else they keep it, unless you say yes) |
| Links to the lane tools and `lane-control`, which agents call | `~/.agents/bin/`, links into `.../lane-control/current/core/scripts/` | `lane-control install`, `project-setup` |
| Skills (the three lane skills and the design skills) | `~/.agents/skills/`, linked for Claude and OpenCode | `project-setup`, `--refresh` |
| One project's settings and tier table, **outside its repo** | `~/.config/lane-control/projects/<folder>-<hash>/` (`settings.json`, `lanes.tsv`) | `project-setup`; `--show` prints them |
| This machine's settings and state | `~/.config/lane-control/`: `machine.conf`, `limits.sh` (optional, yours), the away file, away-improve's files, `hosts/<host>.json`, `tunnels/<host>.env` | `machine-setup`, you, `lane-away`, `lane-improve`, `project-setup`, `lane-tunnel` |
| The model environment (optional, for a gateway) | the one source `~/.config/lane-control/model-env.conf`, and from it `model-env.sh`, `~/.config/fish/conf.d/lane-control-models.fish` and the `env` block of Claude's user settings (what it wrote there is noted in `model-env.written.json`) | `model-env`, only when you run it |
| Budget config and weekly ceiling | `~/.config/rove/playbook.conf.sh` (an old folder name, still the default) and the ceiling file it names | you ([getting started](getting-started.md), step 2.2) |
| In a project's repo | `AGENTS.lane-control.md` plus one pointer line in `AGENTS.md`, and the lane files in `.lane-control/`; the rendered personas there are not committed | `project-setup`, committing only on your yes |
| Beads: issues and memories | a private repo `<project>-beads` in `~/code/`; the project's `.beads` is a git-ignored link to it | you, or `project-setup` on your yes |
| On a box | the desktop's installed tree at the same sha, in the box's own `~/.local/share/lane-control/<sha>/` and `current` | `project-setup <repo> <host>`, on every run (kept when the box has that sha) |

Personas name `.../lane-control/current`, never a checkout or a lane worktree. A `git pull` changes nothing until
`lane-control install`, or a setup or `--refresh` from that main checkout, moves `current` and with it every tool
link at once. A setup from a lane worktree, another branch, or a commit not shown to be newer keeps the installed tree
and runs that tree's own setup (`lane-control: kept`), unless you say yes. The skills and each project's
personas follow at `project-setup <project> --refresh`, which records the installed sha in the project's settings.
`lane-control status` compares the checkout with `current`; `lane-control doctor` lists every project, link, skill and
box that is behind and what to run; `lane-control prune` removes old trees that no running process uses and no hook,
persona, unit or link names, and nothing without a valid `current` (a dry run unless `--apply`).

More: [core and its scripts](../core/README.md), [where beads lives](../core/issues.md#where-beads-lives-backup-and-restore-lc-5c1),
[updating an existing host](getting-started.md#updating-an-existing-host).

## Models

No setup script has a built-in model for the machine coordinator or the automations: you name them once (a flag,
`machine.conf` or an answer in the terminal) and a re-run keeps them. An automation's own record keeps its model unless
you pass `--replace-automation-model`; a new automation takes the model the existing records agree on. Before a model
is written, a gateway in use is asked what it serves (`<base>/v1/models`): a name it does not list is not written, and
one it cannot confirm is written only on your yes in a terminal (`--no-model-check` skips the check). For Claude through a gateway, **`model-env`** keeps the
model variables in one source, writes the shell file, the fish file and Claude's settings `env` from it, including the
auto-mode classifier model (no default), and `model-env --check` compares every place that sets them. It never edits a
line you wrote, and never writes a key. A running agent keeps the environment it was born with.

More: [the model environment](../orchestrators/native-orca/setup.md#the-model-environment-claude-through-a-gateway).

## The lane tools

- **`lane-room`**: "is there room for another lane?" It looks at free memory, CPU contention and a count of running
  lane agents. A coordinator runs it before every lane; if it says no, the lane waits and you are told. The agent count
  only sees Orca + Cotal agents today, so on native Orca memory and CPU are what hold a lane (pending: lc-sq0).
- **`lane-heavy <command>`**: every build and full test suite goes through it. Only a few run at once on the whole
  machine; the rest wait. CI jobs have their own slots.
- **`lane-tier <table> <tier>`**: looks a tier up in a project's tier table and prints the engine and model.
- **`lane-limits.sh`**: works out the limits `lane-room` and `lane-heavy` use, from the machine's size, whether you are
  there, and your optional `limits.sh`.
- **`design-shots`**: headless screenshots at phone and desktop width, light and dark, and a before/after page.

More: [machine caps](../core/machine.md#machine-caps), [the limits example](../core/limits.example.sh).

## Setup

- **`machine-setup`** (once per machine; `--host <host>` runs it on a box): writes `machine.conf` (you fill in the
  projects), checks the usage feed, starts the machine coordinator, creates the three automations, reports Orca's
  cleanup rules and installs the `ci-presence` timer.
- **`project-setup <project>`** (once per project): onboards the repo (an adapting agent and a reviewer write the lane
  files; committed only on your yes), installs the main checkout's newer last commit as the machine's tree (as `lane-control
  install` does; elsewhere it keeps the installed one and runs its setup), links the lane tools, installs the skills, renders the personas, writes the tier table
  and starts the coordinator. Today onboarding a new project needs Claude. Onboarding on OpenCode is parked, so do not
  expect it soon: PR #66 was parked after review round 7, with findings still open.
- **`project-setup <project> --refresh`**: after lane-control changed. Updates the tools, skills and personas, keeps
  the tier table and engines and starts nothing. It commits nothing, with one exception, only on your yes: moving a
  retired design lead's additions into `DESIGN.md`. Restart the coordinator (`project-pause stop`, then `start`) so it
  reads the new persona.
- Both say `exists` for what is already done, so running them again is safe.
- **`project-setup --check <repo> <host>`**: run it before a box setup. It lists every prerequisite, says what is
  missing or could not be checked, and changes nothing. A listening port is not proof: it also checks each tunnel
  unit's state, restart counter and socket owner, that the box runs one Orca server (and which), and the box's SHELL
  ([remote.md](../orchestrators/native-orca/remote.md#a-listening-port-is-not-proof-lc-l78)).

More: [getting started](getting-started.md), [native Orca setup](../orchestrators/native-orca/setup.md).

## A box: two directions

`project-setup <repo> <host>` sets up both. They are separate connections with opposite roles. Both tunnels are
`ssh` processes **on the desktop**, so the box never has to reach the desktop's network.

**Why there are two.** In Orca's own model the coordinator runs on your computer (the client) and starts workers on
paired servers with `--on`. Direction 1 covers that. lane-control also lets a project's coordinator **live on its
box**, so that it keeps working while the desktop is off or unreachable. Such a coordinator still needs the desktop
for some lanes, and to start them the box must be a client of the desktop: that is direction 2. The owner decided
(2026-11-25) that direction 2 stays, as a supported, first-class path and not a fallback: "coordinator lives on box
for just that reason. direction 2 must stay."

While the desktop is off, the box's coordinator and the lanes on the box can go on; lanes on the desktop need it on
and the tunnel up. For the box's Orca to outlive your ssh logins, turn on lingering for the box's user (`loginctl
enable-linger`, on the box). Setup does not do that, and systemd normally stops a user's services after their last
login ends without it. A run with the desktop off has not been tested.

```
              DESKTOP                                              BOX

direction 1: the desktop controls the box
   Orca app (client)                                    orcad = Orca's server (server)
        |                                                    listens on the box's 127.0.0.1
        '--> 127.0.0.1:<port> ==[ orca-tunnel-<host>, ssh -L ]==> 127.0.0.1:<port>

direction 2: the box's coordinator starts lanes on the desktop
   Orca app (server)                                    Orca on the box (client)
   listens on 127.0.0.1                                      |
   127.0.0.1:<port> <==[ lane-tunnel@<host>, ssh -R ]== 127.0.0.1:<box port> <--'
```

| | Direction 1 | Direction 2 |
|---|---|---|
| What it is for | you watch and drive the box's coordinator and lanes in your Orca window | the box gets what it lacks (graphics, a browser, a GPU): its coordinator starts a lane on the desktop |
| Server (is dialled) | the box's `orcad` | the desktop's Orca app |
| Client (dials) | the desktop's Orca | the box's Orca |
| Tunnel | `orca-tunnel-<host>` (`ssh -L`), made by `orca-host-install --pair` | `lane-tunnel@<host>` (`ssh -R`), made by `project-setup` before the link is sent |
| systemd units | desktop: `orca-tunnel-<host>.service`; box: `orcad.service` when it has a user manager, else a detached process | desktop: `lane-tunnel@<host>.service` |
| Who makes the pairing code | the box's `orcad`, by itself; `orca-host-install` reads it over ssh | the desktop's Orca: `project-setup` runs `orca pair` after your yes; or **you**, by hand (`--pair-by-hand`) |
| Who saves it | `orca-host-install` gives it to `orca environment add` on the desktop on stdin | `project-setup` sends it on ssh's stdin; the box runs `environment add --pairing-code -` |

**Direction 1 is automatic.** `orchestrators/orca-cotal/scripts/orca-host-install <host> --pair` (`project-setup` runs
it when the box's server does not answer yet) copies `orcad` and its Node runtime from the desktop's own Orca install,
so the box downloads nothing, and checks Node's checksum. It starts `orcad` on the box's 127.0.0.1, opens the `-L`
tunnel, reads the box's pairing code and adds the box to your Orca. No click and no code from you. The script sits in
the parked Orca + Cotal folder, but native Orca uses it. Without `--pair` it only puts the files on the box, for
Orca's own Settings > SSH connect to reuse (useful where the box has no internet); that route makes no
`orca-tunnel-<host>`.

**Direction 2 needs one yes from you**, once per box. The owner decided (2026-11-25): "we do it with share link, with
--on-client as fallback, but must make sure to make it as painless as possible for user", and on the field report's
recipe (2026-11-26): "if we can handle automatically its much smoother for user". So by default `project-setup` makes
the link itself: `orca pair --address 127.0.0.1:<box port> --rotate --json` on the desktop, held in memory, sent to the box on
ssh's stdin, saved there by `environment add --pairing-code -` through `core/scripts/pairing-code-stdin.cjs`, then
proved with the same reads `--check` makes. It passes `--rotate`, so the box never gets an unused link made earlier
for someone else; that also ends every other unused access link of this Orca. Before it asks, it says what the link
grants (the box can open terminals here, so it can run any command as you, and such a command can reach your screen;
Orca itself will not drive the screen for it), what `--rotate` ends, that the desktop's Orca will then listen on
**every network interface** (or that it already does), and how to revoke it: `--remove-host`, then the "Runtime"
entry of that date in Settings > Servers. Revoking does not narrow the listener; restarting Orca does, when no other
used network pairing remains. After, it prints the bind and the revoke steps.
`core/scripts/lane-tunnel` opens the `-R` tunnel first, which makes the desktop's Orca port appear on the box's
127.0.0.1, and the link names that address. A box already paired is not paired again; a box that has this desktop
under an older name is reported with the remove-then-add steps (a box keeps one name per desktop).

**The loopback option.** Answer no, or pass `--pair-by-hand`, to keep the desktop on 127.0.0.1: in the desktop's Orca,
Settings > Servers > "Share this computer", choosing **127.0.0.1**, then paste that link at `project-setup`'s hidden
prompt. Only the window makes a link that does not widen the listener. Press Enter at the prompt, or pass
`--no-desktop-lanes`, to skip direction 2. The two ends can use different ports (`--tunnel-remote-port` in
`project-setup`, `--remote-port` in `lane-tunnel`). Either link lets the box run commands as you on the desktop until
you revoke it in the same Settings page.

**The fallback.** When neither link can be made, setup says so and points to the hand-off with `send --on-client`
(Orca 1.4.272 or later), documented in [remote.md](../orchestrators/native-orca/remote.md#the-fallback-send---on-client).
**It is not a drop-in:** it needs direction 1 in place and the desktop connected, it mails a bot on the desktop, and a
person approves it once. It reaches a bot, not a lane, so it can ask the desktop for work but cannot start a supervised
lane there ([pairing research](research/2026-11-25-orca-pairing.md), section 6; not run there). lane-control has not
built it, and setup does not fall back to it by itself. What each route grants, ends and leaves open:
[pairing facts](../orchestrators/native-orca/remote.md#pairing-facts).

**After a reboot or an upgrade:** `orca-host-install <host> --tunnel` brings direction 1's unit back without pairing
again; `lane-tunnel start` with the same options brings direction 2's back. Neither tunnel's ssh rides your own ssh
ControlMaster (`ControlPath=none`), and `lane-control doctor` says when a unit flaps, a box runs two Orca servers, or a
box's user manager kept an old SHELL after a `chsh`.

**The rest of what you do by hand for a box:** run `ssh <host>` once so its host key is known; run `project-setup
--check <repo> <host>`; make sure ssh can log in without asking anything (for a smartcard or agent key, `lane-tunnel`
has extra options; `orca-tunnel-<host>` has none); sign the agents in on the box; sign `gh` in there and let git use
it, for beads; answer setup's yes/no questions, the link's among them. Removing: `project-setup --remove-host <host>`
undoes direction 2 only, prints how to drop direction 1, and you revoke the link.

**Is `lane-tunnel` still needed?** This is about how direction 2 is carried; direction 2 itself stays. The tunnel is
not needed when the box can reach the desktop directly (same LAN or Tailscale) **and** you accept the desktop's Orca
listening on every network interface, not only on the address you picked. It is needed when the desktop must stay on
127.0.0.1 or only outbound ssh gets through.
`project-setup` itself always sets the tunnel up: the link it makes names 127.0.0.1, and it refuses a hand link
with any other address.
Details: [Do you need the tunnel?](../orchestrators/native-orca/remote.md#do-you-need-the-tunnel).

**How far this is tested, and what is pending:**
- On main, the native Orca trial ran both with containers: a container as the box, and a second Orca server standing in
  for the desktop. The desktop app's own share link and a lane started from a box's coordinator were not run
  ([record](../orchestrators/native-orca/record.md), NO-6). The docs on main say the no-tunnel route is not run.
- A container testbed exists in PR #68 (`tests/testbed/`, not on main). It was stood down on 2026-11-25. Its results
  are not verified yet, so this page does not rely on them.
- The link setup makes (`orca pair`) was measured working by a remote site on two servers at the same version (field
  report, 2026-11-26); here it is tested with stubs only. That it makes the desktop listen on every interface is read
  from Orca's source ([pairing research](research/2026-11-25-orca-pairing.md)). The link is a credential, and the
  address in it is what the box dials, not what the desktop listens on:
  [pairing facts](../orchestrators/native-orca/remote.md#pairing-facts).
- A box coordinator's lane on the desktop wakes it with its report, with both Orcas at the same version (1.4.274):
  measured in the field (field report 2026-11-26), not repeated here. A desktop coordinator's lane on a box is not
  measured. A report that is lost or never sent is left to the night manager's idle check, Orca's stopped-worker notice
  where the build has it, and otherwise you ([reports across hosts](../orchestrators/native-orca/remote.md#reports-across-hosts)).

More: [native Orca on an SSH host](../orchestrators/native-orca/remote.md).

## Automations

`machine-setup` creates three **Orca automations** in the machine coordinator's folder. Each first runs a **precheck**,
a plain script that spends no model tokens; an agent starts only when the precheck finds something.

| Automation | Runs | Precheck | What its agent does | Why Orca 1.4.274 does not make it unnecessary |
|---|---|---|---|---|
| `night manager` | hourly | `night-precheck` | sends a check-in to each coordinator that is due: idle while you are away or it is night (and, day or night, one that away-improve mode covers); passes on offline CI runners | no Orca built-in wakes an idle coordinator, or checks budget steps or runners |
| `lane sweep` | every 3 hours | `lane-sweep --precheck` (new findings only) | sends each new finding to that lane's coordinator; removes nothing | Orca tells a coordinator about its supervised workers; the sweep also sees lane worktrees with no live worker |
| `machine check` | every 30 minutes | `machine-check` | passes changes (budget step, memory, runners, a coordinator Orca cannot find) to the machine coordinator | Orca has no budget steps, no runner state and no "memory low" warning |

They are already Orca automations, so there is nothing of ours to move onto that feature. The reasons come from the
[overlap audit](research/2026-11-25-orca-overlap.md), rows 8 to 12 and 18. It read Orca's help, guides, release notes
and source; no row was verified by a run. It also says:

- **`STUCK` reports (pending, lc-cnm):** the sweep's stuck report, and the night manager repeating it, may give way to
  Orca's own worker notices for supervised workers, after a trial. Today both still report it.
- **Removing merged lanes (pending, lc-93i):** Orca's cleanup rule does the everyday removal. `lane-sweep --apply` is
  your manual bulk fallback; no automation runs it. The plan is that it removes only lanes Orca's rules did not take.
  **Not built yet:** today `--apply` removes any merged lane that passes its own checks, and also a clean, quiet lane
  whose open pull request already holds everything in it.

Two other things run by themselves and are not Orca automations: **`ci-presence`**, a systemd user timer (every 15
minutes and at boot) that sets CI's CPU and memory limits by whether you are there, and the tunnels above.

Tell the machine you leave or return with `lane-away on|off|status`. It changes limits and allows night check-ins; it
never starts away-improve mode.

More: [night manager](../orchestrators/native-orca/night-manager.md), [a machine](../orchestrators/native-orca/setup.md#a-machine).

## Budgets

- A **metered budget** has a usage feed (how much is used) and a weekly **ceiling** you set. Steps count down from it:
  at ceiling-15 a notice, -7 no new work, -4 push and write handovers, -2 stop. A short window (such as 5 hours) pauses
  new work at 95 % and asks lanes to push at 98 %.
- **`budget-state`** prints each budget's step now. Coordinators check the budget before a lane; the night manager's and
  machine check's prechecks run it. With no usage feed at all there are no steps and the machine's caps are the limit.
- **`window-guard`** is a daemon that sends budget and low-memory orders by itself. It sends them through Cotal, so it
  works only on the parked Orca + Cotal setup (`window-guard-install` installs it). On native Orca nothing like it
  runs: the machine check and the coordinators do this.

More: [budget steps](../core/model-routing.md#budget-thresholds-per-metered-budget-ceiling--the-owners-weekly-percent),
[usage feeds](../core/machine.md#usage-feeds-per-metered-budget).

## Away-improve mode

Off unless **you** switch it on (`lane-improve on`). Each chosen project's coordinator then keeps improving its product
on one `away/<date>` branch, merging there only through `away-merge`, and keeps one draft pull request for you. Nothing
reaches the main line without your word. `lane-improve off` ends it; running work finishes first.

More: [away-improve mode, the owner's guide](away-improve.md).

## Cleanup

- **Orca removes a merged lane's worktree**, with its own rule "Delete merged workspaces". Orca's rules are off by
  default and you turn them on; setup reports what it can tell and never changes Orca's settings. "Delete released
  orchestration worker workspaces" is recommended beside it.
- The rules are each Orca server's own. The app's Settings page does not set a box's global rules, so give each
  project on a box its own rule ([rules are per server](orca-cleanup.md#rules-are-per-server)).
- GitHub's "Automatically delete head branches" removes the remote branch; `project-setup` offers to turn it on.
- When Orca does not take a merged lane, the coordinator removes it by hand, after checks.
- `lane-sweep` reports what is left over. Without `--apply` it removes nothing.

More: [lane cleanup](orca-cleanup.md).

## Parked parts

Orca + Cotal, Rove and wsp are parked and herdr is untested. Their scripts are still in the repo, marked parked in the
[Scripts table](../core/README.md#scripts). This page does not describe them.

More: [orchestrators](../orchestrators/README.md), [switching a project](../orchestrators/switching.md).
