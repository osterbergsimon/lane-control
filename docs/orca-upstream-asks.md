# Asks for Orca upstream (recorded, not filed)

What the field report 2026-11-26 would ask of Orca. Owner, 2026-11-26: "no asks to orca upstream for now, but record
them". Nothing here has been filed; filing is outward-facing and the owner's call (lc-l78.28). The evidence is the
field's; lane-control has not reproduced it unless a section says so.

## A1: a notice when a supervised worker exits without reporting
- **Ask:** a notice shaped like `worker_failed` to the supervisor when a supervised worker exits without reporting.
- **Field evidence:** the report (A1) asks for this notice for a supervised worker that exits without reporting; no more
  detail is recorded here.
- **Meanwhile:** the ask may already be met: the [overlap audit](research/2026-11-25-orca-overlap.md) read in Orca's
  source and release notes that 1.4.268 already sends "Worker stopped without reporting", and later builds more exit
  notices (row 10; not tested): check that before filing. `lane-sweep` covers only the lanes on its own machine, so for
  a lane on the other host the coordinator's night manager does not get a stuck report from it; what remains is the
  night manager's idle check of the coordinator, and the owner. An away-improve watchdog is open (lc-l78.4).
- **Status:** recorded, not filed.

## A5: a reply identity across hosts
- **Ask:** a reply address that is not a terminal handle on the target server, or documented `send --on-client`
  semantics that carry the sender.
- **Field evidence:** the report (A5) names the cross-host reply identity, the `--from` handle; no more detail is
  recorded here.
- **Meanwhile:** the peer-mail route and `reply --id`
  ([messages to a session on the other host](../orchestrators/native-orca/remote.md#reports-across-hosts)); persona
  rules for answering a peer are open (lc-l78.3).
- **Status:** recorded, not filed.

## C5/G2: the setup failure in the worker-start receipt
- **Ask:** the `worker-start` receipt carries the last lines of the setup's stderr, or the failure marker.
- **Field evidence:** a worker start failed with "Setup failed (exit 2)" at `agent_readiness`; the cause showed only
  in a Setup terminal.
- **Meanwhile:** read the Setup terminal. Checking the machine that will build the lane before it starts, and
  documenting `--setup skip` for read-only or edit-only lanes, are open (lc-l78.14).
- **Status:** recorded, not filed.

## D1: a CLI pairing offer that keeps the desktop on loopback
- **Ask:** a `reach` field on `pairing.createOffer`, or `orca pair --this-computer`, so a CLI offer can stay
  loopback-only; also a CLI revoke of a paired client (lc-1i9).
- **Field evidence:** an offer made at `127.0.0.1:<port>` works behind a tunnel, yet the desktop's listener widens to
  every interface. lane-control read the cause in Orca 1.4.274's source
  ([pairing research](research/2026-11-25-orca-pairing.md), section 3).
- **Meanwhile:** the hand share link ("Share this computer", 127.0.0.1) is the route that stays on loopback; the CLI
  mint, chosen as the default route (lc-bmk), widens the listener. Revoking is done in the window.
- **Status:** recorded, not filed.

## I: an automation's model pin without a fresh session per run
- **Ask:** let the model be set in a field that does not force a fresh session, or have the refusal name the conflict
  and the flag to clear.
- **Field evidence:** `automations edit --reuse-session` is refused with extra agent arguments ("Extra arguments
  require a fresh session for every run."), so a model pin starts a fresh session on every run.
- **Meanwhile:** no workaround is recorded.
- **Status:** recorded, not filed.

## J: workspace cleanup settings for a paired server
- **Ask:** a way for the owner to read and set a paired server's cleanup rules, its global ones included.
- **Field evidence:** the app's Settings page is the client's, even with the window on a paired server; per-project
  rules are kept on the server.
- **Meanwhile:** give each project on a box its own rule ([rules are per server](orca-cleanup.md#rules-are-per-server)).
- **Status:** recorded, not filed.
