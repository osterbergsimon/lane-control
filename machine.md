# Machine-specific checklist

Check each item on a new box before adopting the playbook.

## Resources
- [ ] Cores and memory (`nproc`, `free -g`). Set `MEM_LOW_GB`, `SWAP_HIGH_GB` and `NIGHT_MEM_MIN_GB` in the config from them; the owner's own work shares the machine, so leave headroom.
- [ ] Heavy-job policy: one heavy build or run at a time per project; a wrapper script per project if builds are large.
- [ ] Swap size and whether the OS kills on OOM or freezes; the watch fires on available memory, not on crashes.
- [ ] Temp directory for the mode file and night-start snapshots (default `$TMPDIR` or `/tmp`); it must survive between routine runs, not reboots.

## Usage feeds (per metered budget)
- [ ] Each metered budget needs a feed: a command that prints `<weekly%> <window%>` as integers (`BUDGET_USAGE` in the config) and a ceiling (`BUDGET_CEILING`).
- [ ] Example (a Claude Code setup): the owner's status line appends every sample to `~/.claude/usage-history.jsonl`; `tail -1` gives `session` (5-hour window) and `weekly` as 0-1 fractions, with reset times. **A new machine needs an equivalent writer**; without it the budget is unmetered and the scripts skip it.
- [ ] Budgets with no feed (third-party plans seen only in a vendor console, unlimited, self-hosted): set the feed to empty. The owner reports usage; caps are the limit.
- [ ] Ceiling file: `templates/budget-ceiling.json`, updated by the owner weekly.

## OS and tool quirks
- [ ] Shebangs: use `#!/usr/bin/env bash`; some systems (NixOS) have no `/bin/bash`.
- [ ] Dynamic binaries downloaded by tools (browser builds, language runtimes) may need a compatibility layer on non-FHS systems.
- [ ] Browser-automation MCP: if the default browser channel is absent, set its executable path env var (e.g. `PLAYWRIGHT_MCP_EXECUTABLE_PATH`) to an installed browser, plus headless and isolated-profile settings, in the harness env settings; restart sessions. Test once from a fresh session.
- [ ] Tools: `jq`, `awk`, `gh`, `bd`, `rove`, the engine CLIs for every configured tier. `bd` DB location and remote; off-machine backup of issues and memories.
- [ ] Containers: run as the invoking user, or caches become root-owned and block worktree deletion.
- [ ] CI runner: self-hosted if gates need local data; pin image versions.
- [ ] Persistent background jobs (watches, dev servers) die with the session; use approved user services if they must survive.
- [ ] Host-acting software (anything that can reboot, shut down or reconfigure the machine) must be guarded or stubbed in worker environments.
- [ ] Clock and timezone: night mode and routine times assume the owner's local time.
