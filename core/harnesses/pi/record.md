# pi

Status: untested

No adapter yet, not installed. What Orca 1.4.250 says (read from its code, not tried):
- Two agents: `pi` (command `pi`, homepage pi.dev) and `omp` (label "OMP", command `omp`, homepage omp.sh, search
  aliases "oh-my-pi" and "oh my pi" in Orca's agent catalog).
- Sessions: pi under `~/.pi/agent/` and omp under `~/.omp/agent/` (both moved by `PI_CODING_AGENT_DIR`); Orca's
  session search for omp also looks in pi's folders. Both share a "pi-compatible" terminal-title group.
- Models: Orca's worker-start accepts `--model` only for OMP; its other paths pass `--model` and `--thinking` (effort)
  to `omp`.
- Unconfirmed: that omp is a separate program built on pi (the shared folders and aliases suggest it; nothing in
  Orca says so). Which of the two to trial, and how it is installed, is the trial's first step.

## Retest when
- The owner asks for a trial lane: run the harness points of `orchestrators/checklist.md` and record them here.

## Trials
None yet.
