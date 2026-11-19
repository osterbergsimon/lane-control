# wsp

Status: parked

The wsp host and its agents: a chat-with-the-coordinator web UI with lanes as child threads, agents run headless.
Trial notes: `docs/wsp.md`; `scripts/wsp-owner-code.mjs` pairs its web UI as owner. No adapter yet.

## History
- 2026-11-05 trial: wsp 0.3.10 on an SSH-only box with limited internet (`docs/wsp.md`); trial notes only, not a
  port of lane-control's scripts.
- 2026-11-16 status recorded as parked (the orchestrator split); the repo holds trial notes only.

## Retest when
- wsp matures past 0.3.x (young, small community, AGPL-3.0: pin the tested version).
- A retest writes the adapter and runs the checklist.

## Trials

### 2026-11-05 trial notes (wsp 0.3.10)
From `docs/wsp.md`; points nobody tested are `untested`.
- 1. Install: pass — host and agents on the box, workstation as a viewer over an SSH tunnel.
- 2. Two workers: untested
- 3. Messages: partial — agents run headless, so a message is simply the next turn and can never land in a
  half-typed prompt; a launch that failed stays in the sidebar as Failed.
- 4. Nesting: untested
- 5. Survives: untested
- 6. Owner view: pass — sidebar by project, Needs you, Working, Finished, in a browser.
- 7. Auth: untested
- 8. Telemetry: untested
- 9. Fit with the core: partial — the doctrine carries over (roles, tiers, path map, beads, review by risk); the
  scripts called `rove api` then and did not run on wsp; no routines or gates.
