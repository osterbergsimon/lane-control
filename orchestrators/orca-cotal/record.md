# Orca + Cotal

Status: parked

Orca worktrees, Cotal agents and Cotal messages. Setup: `setup.md`; the runner guide `orchestrators/orca-cotal/docs/orca.md`; the parts,
bring-up and failure modes `orchestrators/orca-cotal/docs/orca-cotal-setup.md`. Adapter: `adapter.py` (`status`, `setup`, `strip`).

## History
- 2026-11-05 trial: Orca 1.4.250 with Cotal 0.145.1 and OpenCode 2.2.1 against an SSH-only box behind an
  allowlisting proxy (`orchestrators/orca-cotal/docs/orca.md` sections 6 and 7).
- 2026-11-06 `project-setup` first runs: a Claude coordinator made a child worktree, spawned a lane with
  `cotal_spawn`, the lane's DM woke the coordinator (`orchestrators/orca-cotal/docs/orca.md`, "Verified on 2026-11-06").
- 2026-11-08 in use: projects moved to Orca + Cotal.
- 2026-11-10 diagnosis of workers losing the mesh after about an hour: unproven; the one recorded loss was a second
  Claude session in a seat's terminal, not the mesh (`orchestrators/orca-cotal/docs/orca-cotal-setup.md` section 4).
- 2026-11-15 parked: removed from all projects; retest when Cotal is more mature. Known problems: auth, agents losing
  the mesh. The material stays in this folder (owner, 2026-11-15: "not strip cotal completely from lane-control").

## Retest when
- Cotal ships a fix for seat auth or mesh loss, or a release that changes how seats bind to a Claude session.
- A retest starts from `orchestrators/orca-cotal/docs/orca-cotal-setup.md` section 2 (bring-up with a check per step) and section 4 (what to
  save when a worker seems lost).

## Trials

### 2026-11-05 to 2026-11-10 first trial and use (Orca 1.4.250, Cotal 0.145.1)
From `orchestrators/orca-cotal/docs/orca.md` and `orchestrators/orca-cotal/docs/orca-cotal-setup.md`; points nobody tested are `untested`.
- 1. Install: partial — came up on a proxied SSH-only box with manual fixes (manager environment, umask, OpenCode
  service port, no systemd user manager), all in `orchestrators/orca-cotal/docs/orca.md`, "Host gotchas found".
- 2. Two workers: partial — a coordinator started two lanes with `cotal_spawn` and they reported back (three live
  Orca tabs); the docs do not record whether each lane had its own worktree and branch.
- 3. Messages: pass — a peer message ran as its own turn and an OpenCode draft stayed in the input box; a lane's DM
  woke the coordinator. Claude draft safety was not testable by script.
- 4. Nesting: partial — coordinator, design lead and per-lane reviewer worked; spawning role `lane` needs
  `role:lane` in the spawner's capabilities.
- 5. Survives: partial — agents kept running while Cotal's manager restarted; without a systemd user manager the
  services do not restart after a reboot.
- 6. Owner view: untested — not judged in a recorded trial.
- 7. Auth: fail — stale per-agent OpenCode credentials kept an expired key across respawns; seat auth was one of the
  reasons it was parked.
- 8. Telemetry: partial — both Orca and Cotal send usage telemetry by default.
- 9. Fit with the core: untested — the window guard, budget orders and beads were used with it from 2026-11-08,
  but not checked against this point.
