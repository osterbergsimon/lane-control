# Machine-specific checklist

Check each item on a new box before adopting the playbook.

## Resources
- [ ] Cores and memory (`nproc`, `free -g`). Set `MEM_LOW_GB` and `SWAP_HIGH_GB` in the config from them (and `NIGHT_MEM_MIN_GB` where the orchestrator has a night routine); the owner's own work shares the machine, so leave headroom.
- [ ] Machine caps (see below): check the limits `~/.agents/bin/lane-room` prints; a wrapper script per project if builds are large.
- [ ] Swap size and whether the OS kills on OOM or freezes; the window guard's memory check fires on available memory, not on crashes.
- [ ] The orchestrator's own machine items: its notes, section "Machine" (`orchestrators/<name>/notes.md`) and its `setup.md`.

## Machine caps
The limit on heavy work is per machine, across every project and lane, not per project: several lanes building at once overwhelm a machine (load 23 on 12 cores with four lanes). `lane-heavy` runs its jobs at nice 19, so its slots are what limit lane builds; `lane-room` leaves nice-19 tasks out and counts the rest.
- **Heavy work:** every build or full test suite runs through `~/.agents/bin/lane-heavy <command>`, the per-machine install of `scripts/lane-heavy` (a lane in another project has no `scripts/` of ours). A fixed number run at once; the rest wait, at low priority, with a memory limit where the host supports one.
- **Opening a lane:** `~/.agents/bin/lane-room` first. If it exits non-zero, hold the lane and tell the owner. Its limit is looser than the heavy-work gate: idle agents cost little. It holds a lane on available memory, on the number of lane agents, and on CPU contention: runnable tasks averaged over a few seconds, leaving out SCHED_IDLE and nice-19 tasks (idle-priority CI), not the 5-minute load average, which stays high for minutes after a burst and counts that CI.
- **Presence:** the owner being away or asleep is when the machine has room. Limits branch on it: night (23-07 by default) or an away file (expires after 14 hours) against day. The CI slice and the `lane-heavy` slots both change; a machine's `limits.sh` can branch on presence. Numbers and the reboot fallback: `reliability.md` entry 31. Native Orca's machine setup (phase 4 of the native-Orca plan) adds `lane-away` (writes the away file), `ci-presence` and `machine-setup`.
- **Settings:** one per-machine file outside any repo, `~/.config/lane-control/limits.sh` (`limits.example.sh`); without it the defaults follow cores and memory. `MEM_LOW_GB` and `SWAP_HIGH_GB` in the config are the window guard's memory check, a separate thing.

## Usage feeds (per metered budget)
- [ ] Each metered budget needs a feed: a command that prints `<weekly%> <window%>` as integers (`BUDGET_USAGE` in the config) and a ceiling (`BUDGET_CEILING`).
- [ ] Example (a Claude Code setup): the owner's status line appends every sample to `~/.claude/usage-history.jsonl`; `tail -1` gives `session` (5-hour window) and `weekly` as 0-1 fractions, with reset times. **A new machine needs an equivalent writer**; without it the budget is unmetered and the scripts skip it.
- [ ] Budgets with no feed (third-party plans seen only in a vendor console, unlimited, self-hosted): set the feed to empty. The owner reports usage; caps are the limit.
- [ ] Ceiling file: `core/templates/budget-ceiling.json`, updated by the owner weekly.

## OS and tool quirks
- [ ] Shebangs: use `#!/usr/bin/env bash`; some systems (NixOS) have no `/bin/bash`.
- [ ] Dynamic binaries downloaded by tools (browser builds, language runtimes) may need a compatibility layer on non-FHS systems.
- [ ] Browser-automation MCP: if the default browser channel is absent, set its executable path env var (e.g. `PLAYWRIGHT_MCP_EXECUTABLE_PATH`) to an installed browser, plus headless and isolated-profile settings, in the harness env settings; restart sessions. Test once from a fresh session.
- [ ] Claude Code `/config`: turn off "← opens agents" on each machine that runs Claude lanes (machine-wide; the built-in agent switcher can drop a spawned lane off the orchestrator's messages, seen with Cotal; cause not proven).
- [ ] Tools: `jq`, `awk`, `gh`, `bd`, the orchestrator's own tools (its notes), the engine CLIs for every configured tier. `bd` DB location and remote; off-machine backup of issues and memories.
- [ ] Containers: run as the invoking user, or caches become root-owned and block worktree deletion.
- [ ] CI runner: self-hosted if gates need local data; pin image versions.
- [ ] Nix machines that run night CI: schedule Nix garbage collection in the daytime (`reliability.md` entry 33).
- [ ] Self-hosted runners: runner labels apply only at registration; change them in the config and through the runner-labels API (entry 34). On NixOS, allow `capset` after the default deny list and set `Restart=always` (entry 27).
- [ ] Persistent background jobs (watches, dev servers) die with the session; use approved user services if they must survive.
- [ ] Host-acting software (anything that can reboot, shut down or reconfigure the machine) must be guarded or stubbed in worker environments.
- [ ] Clock and timezone: night mode and routine times, where the orchestrator has them, assume the owner's local time.

## CI runners (patterns)
Gates that need local data or minutes the hosted CI does not have run on self-hosted runners. Two patterns; choose by how much the owner uses the machine.

### A. Runner on the shared workstation, in a capped user slice
- Run the runner as the owner's user, inside a systemd user slice with low priority and hard caps: `CPUWeight=idle` (the owner's work wins every contention), `CPUQuota=<n>00%`, `MemoryHigh`/`MemoryMax` for all runners together, a low `IOWeight`. One job at a time per runner.
- Anything a job starts outside the slice (container service daemons, `systemd-run` scopes) escapes the caps: give it its own.
- Pin the runner image or OS version; treat a sudden repo-wide red as environment drift first (`reliability.md`).
- Do not let the runner share state with the owner's dev stack (ports, caches, credentials); a job must not need the owner's logged-in session.

### B. A dedicated small box
For a project whose CI should not touch the workstation at all (also fits an old spare machine):
- **OS as code:** a NixOS flake describing the box, plus an offline installer ISO built from the same flake (a single "install" command that wipes the disk after a typed confirmation; no network needed to install). The ISO boots into a headless installer reachable over SSH, so no monitor is needed once the firmware boots USB first.
- **Access:** key-only SSH (passwords refused), a non-root admin user with the owner's key, firewall closed except SSH. Put the box on its own network segment with internet only: no route to the owner's LAN.
- **Runner:** ephemeral (a fresh runner per job, state wiped after it), one job at a time, registered to the one repository.
- **Secrets:** the registration token lives outside the runner's state directory (root-owned, mode 600, short-lived, issued from a trusted machine per registration); the runner's own user cannot read it.
- **Changes:** edit the flake on the workstation and deploy as root over SSH (`nixos-rebuild switch --flake .#<box> --target-host <admin>@<box> --use-remote-sudo`); the box also auto-upgrades weekly from its local config.
- An instance may keep its box's flake in its own repo (`infra/` as an example only); this playbook describes the pattern, not a concrete box.
